#!/usr/bin/env python3
import csv
import io
import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

AVINOR_URL = "https://asrv.avinor.no/XmlFeed/v1.0"
AIRPORT = "OSL"
OSLO_TZ = ZoneInfo("Europe/Oslo")

DATAWRAPPER_API_TOKEN = os.environ["DATAWRAPPER_API_TOKEN"]
DATAWRAPPER_CHART_ID = os.environ["DATAWRAPPER_CHART_ID"]
DATAWRAPPER_CHART_ID_CANCELLED = os.environ.get("DATAWRAPPER_CHART_ID_CANCELLED")

DIRECTION_LABELS = {"D": "Avgang", "A": "Ankomst"}
STATE_FILE = Path(__file__).parent / "previous_summary.json"

# Norsk månedsnavn i stedet for tall – uavhengig av systemets locale (f.eks. GitHub Actions-runneren).
NORWEGIAN_MONTHS = {
    1: "januar", 2: "februar", 3: "mars", 4: "april", 5: "mai", 6: "juni",
    7: "juli", 8: "august", 9: "september", 10: "oktober", 11: "november", 12: "desember",
}


def format_norwegian_date(dt):
    return f"{dt.day}. {NORWEGIAN_MONTHS[dt.month]}"

DATA_DIR = Path(__file__).parent / "data"
AIRPORT_NAMES = json.loads((DATA_DIR / "airports.json").read_text(encoding="utf-8"))
AIRLINE_NAMES = json.loads((DATA_DIR / "airlines.json").read_text(encoding="utf-8"))

# Rettelser for koder der kildedataene (OpenFlights, sist oppdatert ~2017-2019) er
# utdaterte eller feil – bekreftet manuelt mot dagens IATA-tildeling.
AIRPORT_NAME_OVERRIDES = {
    "BER": {"name": "Berlin Brandenburg Airport", "city": "Berlin"},
    "FDE": {"name": "Førde Airport, Bringeland", "city": "Førde"},
}
AIRLINE_NAME_OVERRIDES = {
    "D8": "Norwegian Air Sweden",
    "DK": "Sunclass Airlines",
    "RK": "Ryanair UK",
    "EZY": "easyJet",
    "KLJ": "KlasJet",
    "SK": "SAS",
}


# Bokstaver som IKKE dekomponeres av unicodedata.normalize("NFKD", ...) (i motsetning til f.eks. å/é/ü),
# så de må erstattes manuelt, én-til-én, for at lengden skal stemme ved sammenligning mot original streng.
NON_DECOMPOSING_LETTERS = str.maketrans(
    {"ø": "o", "Ø": "O", "æ": "a", "Æ": "A", "ß": "s", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L"}
)


def strip_diacritics(text):
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return text.translate(NON_DECOMPOSING_LETTERS)


def shorten_airport_name(name):
    """Fjerner "Airport"/"International Airport" for å holde Flyplass-kolonnen smal."""
    name = re.sub(r"\bInternational Airport\b", "", name)
    name = re.sub(r"\bAirport\b", "", name)
    name = re.sub(r"\s{2,}", " ", name)
    name = re.sub(r"\s+,", ",", name)
    return name.strip(" ,")


def strip_city(name, city):
    """Fjerner byen/stedsnavnet fra flyplassnavnet, og beholder bare flyplassens egennavn."""
    if not city:
        return name
    normalized_name = strip_diacritics(name).lower()
    normalized_city = strip_diacritics(city).lower()

    remainder = None
    if normalized_name.startswith(normalized_city):
        remainder = name[len(city):]
    elif normalized_name.endswith(normalized_city):
        remainder = name[: len(name) - len(city)]

    if remainder is None:
        return name

    remainder = remainder.strip(" -,()")
    return remainder or name


def airport_name(code):
    entry = AIRPORT_NAME_OVERRIDES.get(code) or AIRPORT_NAMES.get(code) or {"name": code, "city": ""}
    name = shorten_airport_name(entry["name"])
    return strip_city(name, entry["city"])


def airline_name(code):
    return AIRLINE_NAME_OVERRIDES.get(code) or AIRLINE_NAMES.get(code) or code


def fetch_flights(direction):
    """Hent dagens flyvninger (lokal Oslo-dato) for gitt retning ("D" eller "A")."""
    now_local = datetime.now(OSLO_TZ)
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight_local = midnight_local + timedelta(days=1)

    params = {
        "airport": AIRPORT,
        "direction": direction,
        # Rundes opp for å garantere at hele dagens vindu er dekket.
        "TimeFrom": math.ceil((now_local - midnight_local).total_seconds() / 3600),
        "TimeTo": math.ceil((next_midnight_local - now_local).total_seconds() / 3600),
    }
    response = requests.get(AVINOR_URL, params=params, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    today_local = now_local.date()
    flights = []
    for flight in root.iter("flight"):
        schedule_text = flight.findtext("schedule_time")
        if not schedule_text:
            continue
        schedule_time = datetime.fromisoformat(schedule_text.replace("Z", "+00:00"))
        if schedule_time.astimezone(OSLO_TZ).date() != today_local:
            continue

        status = flight.find("status")
        status_code = status.get("code") if status is not None else None
        cancelled = status_code == "C"
        delayed = not cancelled and flight.findtext("delayed") == "Y"
        flights.append(
            {
                "cancelled": cancelled,
                "delayed": delayed,
                "direction": direction,
                "flight_id": flight.findtext("flight_id") or "",
                "airline": flight.findtext("airline") or "",
                "other_airport": flight.findtext("airport") or "",
                "schedule_time": schedule_time,
            }
        )

    return flights


def summarize(flights):
    total = len(flights)
    cancelled = sum(1 for f in flights if f["cancelled"])
    delayed = sum(1 for f in flights if f["delayed"])
    return {"total": total, "cancelled": cancelled, "delayed": delayed}


def load_history(today_local):
    """Les historikk over tidligere kjøringer i dag (nullstilles ved midnatt)."""
    if not STATE_FILE.exists():
        return []
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if state.get("date") != today_local.isoformat():
        return []
    return state.get("history", [])


def find_snapshot_one_hour_ago(history, now_local):
    """Finn den nyeste historikk-snapshoten som er minst én time gammel."""
    target = now_local - timedelta(hours=1)
    candidates = [h for h in history if datetime.fromisoformat(h["time"]) <= target]
    if not candidates:
        return None
    return max(candidates, key=lambda h: h["time"])


def save_state(today_local, history, now_local, departures_summary, arrivals_summary):
    new_history = history + [
        {
            "time": now_local.isoformat(),
            "avganger": departures_summary,
            "ankomster": arrivals_summary,
        }
    ]
    state = {"date": today_local.isoformat(), "history": new_history}
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def format_delta(current, previous):
    if previous is None:
        return "–"
    diff = current - previous
    if diff > 0:
        return f'<span style="color:#c0392b">▲ +{diff}</span>'
    if diff < 0:
        return f'<span style="color:#27ae60">▼ {diff}</span>'
    return '<span style="color:#999999">± 0</span>'


def build_summary_csv(departures, arrivals, previous_snapshot):
    departures_summary = summarize(departures)
    arrivals_summary = summarize(arrivals)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Retning", "Totalt antall", "Innstilt", "Endring innstilt (1t)", "Forsinket", "Endring forsinket (1t)"]
    )
    for label, key, summary in (("Avganger", "avganger", departures_summary), ("Ankomster", "ankomster", arrivals_summary)):
        previous = previous_snapshot.get(key) if previous_snapshot else None
        writer.writerow(
            [
                label,
                summary["total"],
                summary["cancelled"],
                format_delta(summary["cancelled"], previous["cancelled"] if previous else None),
                summary["delayed"],
                format_delta(summary["delayed"], previous["delayed"] if previous else None),
            ]
        )
    return output.getvalue(), departures_summary, arrivals_summary


def build_cancelled_csv(departures, arrivals):
    cancelled_flights = [f for f in departures + arrivals if f["cancelled"]]
    cancelled_flights.sort(key=lambda f: f["schedule_time"])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Dato", "Klokkeslett", "Retning", "Flight", "Selskap", "Flyplass"])
    for f in cancelled_flights:
        local_time = f["schedule_time"].astimezone(OSLO_TZ)
        writer.writerow(
            [
                format_norwegian_date(local_time),
                local_time.strftime("%H:%M"),
                DIRECTION_LABELS[f["direction"]],
                f["flight_id"],
                airline_name(f["airline"]),
                airport_name(f["other_airport"]),
            ]
        )
    return output.getvalue()


def push_to_datawrapper(chart_id, csv_data, notes):
    auth_header = {"Authorization": f"Bearer {DATAWRAPPER_API_TOKEN}"}
    base_url = f"https://api.datawrapper.de/v3/charts/{chart_id}"

    data_response = requests.put(
        f"{base_url}/data",
        headers={**auth_header, "Content-Type": "text/csv"},
        data=csv_data.encode("utf-8"),
        timeout=30,
    )
    data_response.raise_for_status()

    patch_response = requests.patch(
        base_url,
        headers=auth_header,
        json={
            "metadata": {
                "annotate": {"notes": notes},
                "describe": {"source-name": "Avinor", "source-url": "https://www.avinor.no/"},
            }
        },
        timeout=30,
    )
    patch_response.raise_for_status()

    publish_response = requests.post(f"{base_url}/publish", headers=auth_header, timeout=30)
    if not publish_response.ok:
        print(f"Datawrapper publish feilet ({publish_response.status_code}): {publish_response.text}", file=sys.stderr)
    publish_response.raise_for_status()


def main():
    departures = fetch_flights("D")
    arrivals = fetch_flights("A")

    if not departures and not arrivals:
        print(
            "Ingen flyvninger funnet for i dag hos Avinor – avbryter uten å oppdatere Datawrapper.",
            file=sys.stderr,
        )
        sys.exit(1)

    now_local = datetime.now(OSLO_TZ)
    updated_at = now_local.strftime("%H:%M")
    updated_at_dotted = now_local.strftime("%H.%M")
    notes_base = (
        f"<b>Sist oppdatert klokka {updated_at_dotted} (norsk tid)</b><br>"
        '"Forsinket" er Avinors egen forsinkelsesmarkering for flyvningen.'
    )

    history = load_history(now_local.date())
    previous_snapshot = find_snapshot_one_hour_ago(history, now_local)
    summary_csv, departures_summary, arrivals_summary = build_summary_csv(departures, arrivals, previous_snapshot)
    push_to_datawrapper(DATAWRAPPER_CHART_ID, summary_csv, notes_base + " Endring viser utvikling siste time.")
    print(f"Oppdatert oversikt kl. {updated_at} (norsk tid):\n{summary_csv}")
    save_state(now_local.date(), history, now_local, departures_summary, arrivals_summary)

    if DATAWRAPPER_CHART_ID_CANCELLED:
        cancelled_csv = build_cancelled_csv(departures, arrivals)
        push_to_datawrapper(DATAWRAPPER_CHART_ID_CANCELLED, cancelled_csv, notes_base)
        print(f"Oppdatert liste over innstilte flyvninger:\n{cancelled_csv}")
    else:
        print(
            "DATAWRAPPER_CHART_ID_CANCELLED er ikke satt – hopper over oppdatering av "
            "listen over innstilte flyvninger.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
