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
