# Prospect

Insight into a Dutch neighbourhood or house, based on official open data. See [PLAN.md](PLAN.md) for the full plan.

## App

A static web app in [`app/`](app/), with no build step. It can be installed on the iPhone home screen (Share → "Zet op beginscherm").

- Basemap: PDOK BRT-Achtergrondkaart (grey)
- Address search: PDOK Locatieserver
- Map library: MapLibre GL JS 4.7.1, vendored in `app/vendor/`

Run locally:

```sh
python3 -m http.server -d app 8000
```

## Deployment

[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) publishes `app/` to GitHub Pages on every push to `main` (and to the development branch) that touches `app/`. You can also start it by hand from the Actions tab.

One-time setup: Settings → Pages → Source: **GitHub Actions**.

## Tests

[`tests/e2e.mjs`](tests/e2e.mjs) is an end-to-end suite that runs in headless Chromium against the real PDOK services. It covers:

- **Assets:** assets and install metadata (manifest, icons, apple-touch-icon size).
- **Map:** basemap tiles, including panning.
- **Search:**
  - address and street results, including buurt, wijk and gemeente;
  - the Enter key, short and nonsense queries;
  - HTML injection;
  - the Locatieserver being down.
- **Test areas:** both test-area presets.
- **Layout:** 5 screen sizes (iPhone SE up to desktop) in light and dark mode.
- **Offline:** the app shell loads offline via the service worker.

Where it runs:

- **Test** workflow: on every branch push and pull request, against that commit's `app/` served on the runner.
- **Deploy** workflow: after every deploy, against the live site.

Screenshots and `results.json` are uploaded as artifacts.

Run it locally:

```sh
npm ci && npx playwright install chromium
npm run serve &   # then:
npm test -- http://localhost:8000/
```

## Data pipeline

[`pipeline/build.py`](pipeline/build.py) fetches official open data for the test areas:
- CBS wijken en buurten, crime, house prices;
- Leefbaarometer, the RIVM Gezondheidsmonitor;
- DUO schools, BAG, the BRO Bodemkaart;
- official publications, Open Raadsinformatie.

[`pipeline/report.py`](pipeline/report.py) turns that data into a due-diligence dossier per area.

It runs on GitHub Actions (the **Data** workflow), because the official sources are reachable there. Raw output goes to the `data-build` branch. To run only some steps, start the workflow by hand and fill in `steps`.

- Reports: [`docs/dd/`](docs/dd/)
- App data: [`app/data/`](app/data/), with a dossier per area and `buurten-scores.geojson` for the map
- Hand-collected data: [`data/listings/`](data/listings/) (asking prices found online) and [`data/regelingen/`](data/regelingen/) (starter loans and buyer rules)

### Whole villages, down to the building

[`pipeline/village.py`](pipeline/village.py) covers every address and building in a village. It currently covers Hazerswoude-Rijndijk.

For each building and address it collects:
- the footprint, building year, floor area and use, from BAG;
- the soil type, from the BRO Bodemkaart;
- whether it is a rijksmonument, from RCE;
- a foundation-risk flag. This is a rule of thumb from the building year and the soil type, not a survey.

For each street it collects a building summary, official publications and council documents.

It runs on GitHub Actions (the **Village** workflow), and its output goes to the `village-build` branch. `python tools/publish_village.py <dir>` copies that output into [`app/data/villages/`](app/data/villages/). The publish step also adds the asking prices in `data/listings/village-<key>.json`.

In the app:
- buildings appear once you zoom in on the village, coloured by foundation risk;
- tapping a building opens its card;
- searching a street in the village opens a street card.
