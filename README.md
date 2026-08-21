# Oslo lufthavn – status

To tabeller for Oslo lufthavn, Gardermoen (OSL), som begge oppdateres automatisk hvert 30. minutt via GitHub Actions:

1. **Oversikt** – dagens antall innstilte og forsinkede flyvninger (avganger og ankomster separat), med egne kolonner som viser endring siste time (▲ rødt = flere, ▼ grønt = færre)
2. **Innstilte flyvninger** – liste over hver enkelt innstilte flyvning i dag (dato, klokkeslett, retning, flight, selskap, flyplass)

Data hentes fra Avinors gratis flydata-API (`https://asrv.avinor.no/XmlFeed/v1.0`). "Forsinket" er Avinors egen forsinkelsesmarkering for flyvningen (samme som vises på flyplassens infoskjermer); "innstilt" er flyvninger med statuskode `C`.

Flyplass- og flyselskapsnavn (`data/airports.json`, `data/airlines.json`) er slått opp fra [OpenFlights](https://openflights.org/data.php) sin database (ODbL-lisens), med noen få manuelle rettelser i `AIRPORT_NAME_OVERRIDES`/`AIRLINE_NAME_OVERRIDES` i scriptet der kildedataene var utdaterte (f.eks. gjenbrukte IATA-koder som D8, DK, RK).

---

## Oppsett

### 1. Opprett Datawrapper API-token

1. Gå til [app.datawrapper.de/account/api-tokens](https://app.datawrapper.de/account/api-tokens)
2. Opprett et nytt token med tilgang til å lese/skrive charts

### 2. Opprett tabell-chartene i Datawrapper

Lag to separate **Table**-charts i Datawrapper sitt UI (så du fritt kan style farger, kolonner, footer osv. for hver):

1. **Oversikt** – legg inn en midlertidig CSV manuelt første gang (kolonner: `Retning,Totalt antall,Innstilt,Endring innstilt (1t),Forsinket,Endring forsinket (1t)`). Skru på **Parse markdown** for denne charten under **Refine → Customize table** – det er det som gjør at de fargede pil-cellene (`<span style="color:...">`) rendres som farget tekst i stedet for rå HTML.
2. **Innstilte flyvninger** – legg inn en midlertidig CSV manuelt (kolonner: `Dato,Klokkeslett,Retning,Flight,Selskap,Flyplass`)

Noter chart-ID-en for hver fra URL-en, f.eks. `https://app.datawrapper.de/chart/AbCdE/edit` → ID er `AbCdE`

### 3. Legg til GitHub Secrets

Gå til **Settings → Secrets and variables → Actions** i repoet og legg til:

| Secret | Beskrivelse |
|---|---|
| `DATAWRAPPER_API_TOKEN` | Token fra steg 1 (husk scopes: Chart read+write, Theme read, Visualization read – kreves for publisering) |
| `DATAWRAPPER_CHART_ID` | Chart-ID for oversikts-tabellen |
| `DATAWRAPPER_CHART_ID_CANCELLED` | Chart-ID for listen over innstilte flyvninger |

### 4. Push repoet til GitHub

```bash
cd ~/oslo-lufthavn-status
git init
git add .
git commit -m "Sett opp automatisk oppdatering av Oslo lufthavn-status"
git remote add origin https://github.com/<din-bruker>/oslo-lufthavn-status.git
git push -u origin main
```

---

## Lokal testing

```bash
pip install -r requirements.txt
cp .env.example .env   # Fyll inn verdiene
python oslo_lufthavn_status.py
```

---

## Tidsplan

Kjører automatisk hvert 30. minutt (`*/30 * * * *`) via GitHub Actions – uavhengig av om denne maskinen er på. Kan også trigges manuelt fra **Actions**-fanen i GitHub (`workflow_dispatch`).

---

## Arkitektur

```
GitHub Actions (cron hver 30. min)
    ├── Last ned previous_summary.json (artifact "flydata-state" fra forrige kjøring)
    ├── oslo_lufthavn_status.py
    │    ├── fetch_flights("D"/"A")        → Avinor XmlFeed for dagens avganger/ankomster
    │    ├── airport_name()/airline_name() → slår opp fulle navn via data/*.json (+ overrides)
    │    ├── build_summary_csv()           → Retning, Totalt antall, Innstilt, Endring innstilt (1t), Forsinket, Endring forsinket (1t)
    │    ├── build_cancelled_csv()         → Dato, Klokkeslett, Retning, Flight, Selskap, Flyplass
    │    ├── push_to_datawrapper(...)      → PUT data + PATCH footer-notat + POST publish (per chart)
    │    └── save_state()                  → skriver previous_summary.json for neste kjøring
    └── Last opp previous_summary.json som artifact "flydata-state"
```

"Endring"-kolonnene sammenligner alltid med snapshoten som er nærmest én time gammel (`find_snapshot_one_hour_ago()`), ikke bare forrige kjøring. All historikk nullstilles automatisk ved midnatt (norsk tid), siden `previous_summary.json` inneholder dagens dato og ignoreres hvis den er fra en tidligere dag.
