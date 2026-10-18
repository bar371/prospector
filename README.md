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

## Smoke test

After each deploy, the `smoke-test` job opens the live site in headless Chromium (phone-sized). It checks that:

- the page and MapLibre load;
- PDOK map tiles arrive;
- the PDOK address search finds Zamenhofstraat;
- both test areas open a place card.

Screenshots are uploaded as the `smoke-screenshots` artifact.

Run it locally:

```sh
npm ci && npx playwright install chromium
npm run serve &   # then:
npm run smoke -- http://localhost:8000/
```
