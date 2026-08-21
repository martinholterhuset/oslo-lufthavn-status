# Oslo lufthavn – status

Tabell som viser dagens antall innstilte og forsinkede flyvninger (avganger og ankomster separat) for Oslo lufthavn, Gardermoen (OSL). Oppdateres automatisk hvert 30. minutt via GitHub Actions og pushes til en Datawrapper-tabell.

Data hentes fra Avinors gratis flydata-API (`https://asrv.avinor.no/XmlFeed/v1.0`). "Forsinket" er Avinors egen forsinkelsesmarkering for flyvningen (samme som vises på flyplassens infoskjermer); "innstilt" er flyvninger med statuskode `C`.

---

## Oppsett

### 1. Opprett Datawrapper API-token

1. Gå til [app.datawrapper.de/account/api-tokens](https://app.datawrapper.de/account/api-tokens)
2. Opprett et nytt token med tilgang til å lese/skrive charts

### 2. Opprett tabell-charten i Datawrapper

1. Lag et nytt **Table**-chart i Datawrapper sitt UI (så du fritt kan style farger, kolonner, footer osv.)
2. Legg inn en midlertidig CSV manuelt første gang (kolonner: `Retning,Totalt antall,Innstilt,Forsinket`)
3. Noter chart-ID-en fra URL-en, f.eks. `https://app.datawrapper.de/chart/AbCdE/edit` → ID er `AbCdE`

### 3. Legg til GitHub Secrets

Gå til **Settings → Secrets and variables → Actions** i repoet og legg til:

| Secret | Beskrivelse |
|---|---|
| `DATAWRAPPER_API_TOKEN` | Token fra steg 1 |
| `DATAWRAPPER_CHART_ID` | Chart-ID fra steg 2 |

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
    └── oslo_lufthavn_status.py
         ├── fetch_flights("D"/"A")  → Avinor XmlFeed for dagens avganger/ankomster
         ├── build_csv()             → Retning, Totalt antall, Innstilt, Forsinket
         └── push_to_datawrapper()   → PUT data + PATCH footer-notat + POST publish
```
