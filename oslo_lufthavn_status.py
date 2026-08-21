#!/usr/bin/env python3
import csv
import io
import math
import os
import sys
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
        flights.append({"cancelled": cancelled, "delayed": delayed})

    return flights


def summarize(flights):
    total = len(flights)
    cancelled = sum(1 for f in flights if f["cancelled"])
    delayed = sum(1 for f in flights if f["delayed"])
    return total, cancelled, delayed


def build_csv(departures, arrivals):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Retning", "Totalt antall", "Innstilt", "Forsinket"])
    for label, flights in (("Avganger", departures), ("Ankomster", arrivals)):
        writer.writerow([label, *summarize(flights)])
    return output.getvalue()


def push_to_datawrapper(csv_data, updated_at):
    auth_header = {"Authorization": f"Bearer {DATAWRAPPER_API_TOKEN}"}
    base_url = f"https://api.datawrapper.de/v3/charts/{DATAWRAPPER_CHART_ID}"

    data_response = requests.put(
        f"{base_url}/data",
        headers={**auth_header, "Content-Type": "text/csv"},
        data=csv_data.encode("utf-8"),
        timeout=30,
    )
    data_response.raise_for_status()

    notes = (
        'Kilde: Flydata fra Avinor. "Forsinket" er Avinors egen '
        f"forsinkelsesmarkering for flyvningen. Sist oppdatert kl. {updated_at} (norsk tid)."
    )
    patch_response = requests.patch(
        base_url,
        headers=auth_header,
        json={"metadata": {"annotate": {"notes": notes}}},
        timeout=30,
    )
    patch_response.raise_for_status()

    publish_response = requests.post(f"{base_url}/publish", headers=auth_header, timeout=30)
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

    csv_data = build_csv(departures, arrivals)
    updated_at = datetime.now(OSLO_TZ).strftime("%H:%M")
    push_to_datawrapper(csv_data, updated_at)

    print(f"Oppdatert kl. {updated_at} (norsk tid):\n{csv_data}")


if __name__ == "__main__":
    main()
