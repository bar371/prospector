# Prospect — Plan

> Status: **draft for review**. No code yet. Open questions are at the bottom.

## 1. What Prospect is

A single, very simple and good-looking HTML app (installable on the iPhone home screen) that lets you:

1. **See a neighbourhood** as colour-coded overlays (polygons or a grid) for education, safety, liveability, soil and environment, development plans, and market value. You switch overlays on and off and compare them.
2. **Open a property** (an address, a street, or a Funda listing you paste in) and get a due-diligence card with every fact we have about the building and its surroundings.
3. **Get an estimated value** for that property. This is an LLM call added in a later phase. It reasons over the dossier plus recent comparable sales.
4. **Read the council's view** ("Wat zegt de raad?"). A button shows what the gemeenteraad and its committees have said about the area, taken from meeting transcripts.

Every data point comes from an official or open public source and keeps its source and reference date, so the user can always see where a number comes from.

**Test areas (phase 1):**
- **Zamenhofstraat, Leiden.** Wijk Noordvest (Stationsdistrict), postcodes 2312 NV / NW / NX. This corrects an earlier guess of Merenwijk.
- **Hazerswoude-Rijndijk** (gemeente Alphen aan den Rijn), along the Rijndijk. Confirmed. The exact CBS buurt codes are pinned down in phase 0.

---

## 2. Data layers and official sources

All polygons are the **CBS wijk- en buurtkaart** (via PDOK). Most Dutch statistics are published per *buurt*, so this is the natural unit. It also handles the geolocation problem for crime: police data is published per buurt, not per incident. Where finer data exists, we use a **100 m grid** (Leefbaarometer, CBS vierkanten).

### 2.1 Property and value
| Data | Source | Notes |
|---|---|---|
| Building year, use, floor area, type, number of units | **BAG** (Kadaster, via PDOK API/WFS) | Free and open, address-level |
| Parcel boundaries, plot size, ownership type (erfpacht!) | **BRK / Kadastrale kaart** (PDOK) | Plot geometry is open. Owner and erfpacht details are paid per lookup via Kadaster |
| WOZ value per address, with history | **WOZ-waardeloket** (wozwaardeloket.nl) | Public per address. Use carefully, without bulk scraping |
| Average WOZ per buurt, share of owner-occupied homes | **CBS Kerncijfers Wijken en Buurten** (StatLine) | Open |
| Listing history: original asking price, date listed, date sold or withdrawn | **Online search** of public listing pages and archives (estate-agent sites, Funda, Huispedia, Walter Living, Jaap.nl, news), recorded by hand per property with the source URL and the date found | **Free.** These are asking prices, not sale prices, and are always labelled that way. No bulk scraping |
| Regional price trend | **CBS / Kadaster** price index per region (StatLine, free) | Used to bring older asking prices up to today's level |
| Transaction prices (koopsommen) | **Kadaster** koopsominformatie | **Not used for now** (paid). The budget decision is "strictly free" |
| Energy label | **EP-Online** (RVO) open API | Free API key |
| Listing details | **Funda**, entered manually by the user (paste the URL or the fields) | Funda's terms forbid scraping, so we don't build on it |

### 2.2 Education
| Data | Source |
|---|---|
| School locations, type, student numbers | **DUO open onderwijsdata** |
| Inspection verdicts (onvoldoende / zeer zwak) | **Inspectie van het Onderwijs** (open data / inspection reports) |
| Results: eindtoets / doorstroomtoets, onderadvies vs. overadvies, exam pass rates, where students go next | **DUO** and **Scholen op de kaart** (PO-Raad / VO-raad) |
| Education level of residents | **CBS KWB** |

Display: school pins coloured by inspection verdict and results, plus a buurt score from the schools within walking or cycling distance.

### 2.3 Safety
| Data | Source |
|---|---|
| Registered crimes per buurt per month, by type (burglary, violence, vandalism…) | **Politie open data** via **CBS StatLine** (geregistreerde misdrijven per wijk/buurt), also shown on politie.nl "Misdaad in kaart" |
| Nuisance reports, perceived safety | **Veiligheidsmonitor** (CBS/BZK), at gemeente or wijk level |

Shown as crimes per 1,000 residents, compared with the national and gemeente average, with a 3-year trend.

### 2.4 Liveability and social
| Data | Source |
|---|---|
| **Leefbaarometer** score and its sub-dimensions (housing, physical environment, amenities, social cohesion, nuisance and safety) on a **100 m grid** | Ministerie BZK. **This is the best-fitting official "grid overlay"** |
| Income, benefits, age structure, household types | **CBS KWB** |
| Distance to GP, supermarket, school, station, motorway, day care | **CBS Nabijheid voorzieningen** |
| Health (loneliness, overweight, perceived health) | **RIVM / GGD Gezondheidsmonitor** |

### 2.5 Soil, subsurface and foundations (important around Leiden and Alphen: peat and clay)
| Data | Source |
|---|---|
| Soil contamination: investigations, remediation, suspect locations | **Bodemloket** (WBB, from the provinces) and provincie Zuid-Holland |
| Soil type (peat, clay, sand) | **BRO – Bodemkaart 1:50.000** and **GeoTOP** (TNO/BRO via PDOK) |
| Groundwater levels | **BRO** groundwater monitoring wells |
| **Subsidence** (mm/year, InSAR) | **Bodemdalingskaart** (NCG / Skygeo) |
| **Foundation risk** (wooden piles, pile rot, shallow foundations) | **Klimaateffectatlas – funderingsrisico**, **KCAF**. Building year from BAG is a strong proxy |
| PFAS | RIVM / provincial PFAS maps |
| Unexploded ordnance (NGE) | Gemeente bodembeleid / NGE maps |

### 2.6 Climate and environment
| Data | Source |
|---|---|
| Flood risk, waterlogging, heat stress, drought | **Klimaateffectatlas** |
| Air quality (NO₂, PM2.5), road and rail noise | **RIVM Atlas Leefomgeving** (GCN maps, geluidkaarten) |
| External safety (hazardous sites, pipelines, LPG) | **Risicokaart / Atlas Leefomgeving** |
| High-voltage lines, antennas | Netbeheerder maps, **Antenneregister** (RDI) |

### 2.7 Development, now and in the future
| Data | Source |
|---|---|
| Zoning and what may be built (omgevingsplan / bestemmingsplan) | **Omgevingsloket (DSO)** and **ruimtelijkeplannen.nl** |
| Permit applications and decisions near the address | **Officiële bekendmakingen** (KOOP open data API), the gemeenteblad |
| New-build plans and large projects | Gemeente woningbouwprogramma, provincie Zuid-Holland. Locally: **RijnlandRoute**, Hazerswoude-area developments |
| Building permits issued and new homes completed per buurt (trend) | **CBS** |

Shown as a "development" layer with plan contours and permit pins, plus a short "what is changing here" list.

### 2.8 Council intelligence ("Wat zegt de raad?")
- **Sources:** the gemeenteraad and committee meetings and documents of **Leiden** and **Alphen aan den Rijn**. These come from their raadsinformatie systems (Notubiz/iBabs) and **Open Raadsinformatie** (Open State Foundation), which brings Dutch council documents together under one API.
- **Pipeline:**
  1. Find meetings and agenda items that mention the street, buurt, wijk, or themes such as overlast, veiligheid, wijkagent, or bouwplannen.
  2. Get the meeting video or audio. Use existing transcripts where they exist; otherwise transcribe them (Whisper, in Dutch).
  3. Cut the transcript into segments with timestamps and speakers.
  4. Have an LLM summarise each segment: topic, sentiment, who spoke (police, residents, aldermen), and the claims made.
  5. Store the result as static JSON, with deep links to the exact moment in the video.
- **In the app:** a dedicated button on the area card. It opens a timeline of quotes and summaries, each linked to its source.
- **Phase 1:** done offline and **pre-populated** for the two test areas. A monthly refresh comes later.

### 2.9 Mobility: highways, public transport and "hoe autoluw is je zone?"
Three sub-scores shown side by side. Being car-free is good for quiet and safety but bad for parking, so the user decides through the weights how much each counts.

**a) Highway access**
| Data | Source | Notes |
|---|---|---|
| Distance to the nearest highway on-ramp (per buurt) | **CBS Nabijheid voorzieningen** ("afstand tot oprit hoofdverkeersweg") | Official, ready to use |
| Drive time from the address to the nearest on-ramp and to key destinations (A4, A44, A12; Den Haag, Amsterdam, Utrecht, Schiphol) | **NWB – Nationaal Wegenbestand** (Rijkswaterstaat, via PDOK), with drive times calculated in our pipeline | Distances are calculated off-peak. Rush-hour traffic is noted, not modelled |
| Planned road projects (for example the RijnlandRoute and N11 changes) | Rijkswaterstaat, provincie Zuid-Holland | Also feeds the development layer (2.7) |

**b) Public transport**
| Data | Source | Notes |
|---|---|---|
| Distance to the nearest train station and nearest major transfer station | **CBS Nabijheid voorzieningen** | Per buurt |
| Stops within walking distance, the lines serving them, and how often they run (peak and evening) | **NDOV Loket** (official public-transport timetable data, GTFS feed) | Calculated per address |
| Door-to-door travel time by public transport to Leiden Centraal, Den Haag, Amsterdam, Schiphol and Utrecht | Same GTFS, with an offline route planner in the pipeline (r5py or OpenTripPlanner) | Stored as **isochrones**: map areas showing everything reachable within 15, 30 or 45 minutes |
| Cycling time to the station or centre | Calculated over the NWB / OSM cycle network | Matters a lot in Leiden |

**c) "Hoe autoluw is je zone?"**: how car-free or low-traffic the area is
| Signal | Source |
|---|---|
| Car-free streets, one-way streets, filters and closures, 30 km/h zones, woonerven (residential streets with walking-pace traffic) | **Verkeersbesluiten**. Every such measure must be published in the Gemeenteblad / Staatscourant, which we search via the KOOP open-data API. Road speeds come from the **NDW Wegkenmerkendatabase** |
| Paid parking, permit areas, rates, waiting lists | **RDW Open Data Parkeren** (parking areas and rates), plus the gemeente's parking pages |
| Traffic volume on nearby roads | **NDW** traffic data |
| Cars per household | **CBS KWB** |
| Environmental or zero-emission zones | **milieuzones.nl** (Rijksoverheid) and the gemeente verkeersbesluit |
| Policy now and planned (for example Leiden's plans for a car-free city centre, or circulation plans) | Gemeente mobiliteitsplan / verkeersvisie, raadsinformatie, and the council feature (2.8) |

- **Autoluw score (0–100).** This is a score we calculate ourselves, combining the share of nearby road length that is 30 km/h or woonerf, car-free or filtered streets, traffic volume, cars per household, and the parking regime. A "planned" marker appears when council or policy documents announce new measures.
- The app always shows the parts of the score and explains the result. For example: "Autoluw 78: 30 km/h zone, through-traffic filtered on the Zamenhofstraat side; permit parking, waiting list ±X months."

### 2.10 Financing and municipal buyer rules
Whether someone can buy here, and what it costs them, depends partly on the gemeente. Prospect shows the rules for the municipality of the selected address.

| Data | Source | Notes |
|---|---|---|
| **Starterslening**: whether the gemeente offers it, maximum loan, maximum purchase price, income or age limits, local-tie requirements, whether the budget is used up | **SVn** (Stimuleringsfonds Volkshuisvesting) lists the participating municipalities. The binding rules are in each gemeente's **Verordening Starterslening** on **lokaleregelgeving.overheid.nl** (CVDR) | Varies per gemeente and changes when the budget runs out or is topped up. We store the date checked and link to the verordening |
| Other municipal loans: **Duurzaamheidslening**, **Blijverslening**, **Toekomstbestendig Wonen-lening**, funderingsherstel loans | SVn and the gemeente verordeningen (CVDR) | Relevant to sustainability and foundation repair costs (see 2.5) |
| **Opkoopbescherming** (buy-to-let ban) and **zelfbewoningsplicht** (owner-occupancy obligation) | Gemeente huisvestingsverordening or omgevingsplan (CVDR) | Limits renting out the property. Often applies only below a WOZ threshold or to new-build |
| Priority for local starters on new-build, **Koopgarant** or **Koopstart** schemes | Gemeente / housing corporation rules (CVDR) | Mostly new-build |
| National rules for comparison: **NHG** cost limit, **startersvrijstelling overdrachtsbelasting** (transfer tax exemption: age and price limits) | **NHG**, **Belastingdienst** / Rijksoverheid | The same everywhere. Shown alongside so the user sees the full starter picture |

**In the app:**
- A **"Kopen als starter" (buying as a starter) section** on the property card. It shows which schemes apply, the limits, and whether this property's price fits within them. For example: "Asking price €X is above the Starterslening limit of €Y in this gemeente."
- An optional **map layer** that colours municipalities by whether they offer a Starterslening. This matters mainly at regional zoom, for comparing Leiden with Alphen aan den Rijn.

**Pipeline:** a small table per gemeente (`/data/regelingen/<gemeente>.json`) with each scheme, its limits, its source link, its effective date, and the date it was checked. It is built by hand or semi-automatically from CVDR for the test municipalities and refreshed regularly, because these rules change often (usually every 1 January).

### 2.11 Other things worth adding
- **Erfpacht** (ground lease): a big hidden cost, common in some municipalities. Source: BRK.
- **VvE / apartment rights**, from BAG units and BRK apartment rights.
- **Monument status**, from the **Rijksmonumentenregister** (RCE) and municipal monument lists. This limits renovation.
- **Protected cityscape**, from beschermd stads- en dorpsgezicht (RCE).
- **Price trend per region**, from **CBS / Kadaster prijsindex**.
- **Time on market and overbidding.** This is NVM data and not official, so it is optional and clearly labelled.

---

## 3. App design

**Principles:** one screen, map first, almost no chrome. It must work as a static page.

- **Stack:** plain HTML + JS (no build step, or a tiny Vite build) and **MapLibre GL JS**, with the **PDOK BRT-Achtergrondkaart** (the official Dutch basemap, open vector tiles) in a muted grey style so the overlays stand out.
- **Installable:** a PWA manifest, apple-touch-icon and `apple-mobile-web-app-capable`, plus a service worker so test-area data works offline.
- **Hosting:** GitHub Pages (static). All data is pre-built JSON or GeoJSON in `/data`.

**Layout (mobile first):**
1. **Search bar** at the top. It uses **PDOK Locatieserver** (official address search, free, CORS-enabled).
2. **Layer chips** in a horizontal scroll: Prospect score · Education · Safety · Liveability · Mobility & autoluw · Soil & foundations · Climate · Development · Value. Tap one to show it; long-press to add it as a second layer.
3. **Map:** buurt polygons filled on a colour-blind-safe diverging scale (deep green → neutral → deep red). A toggle switches to the **100 m Leefbaarometer grid**. Points (schools, permits, contamination sites) appear when zoomed in.
4. **Bottom sheet** (drag up), opened by tapping a buurt or address:
   - a header with an overall **Prospect score** and a spider or radar chart of the dimensions;
   - one row per dimension: a score, a one-line explanation, the source and date;
   - **Bereikbaarheid (accessibility)**: highway, public transport and cycling times, with an isochrone toggle ("what can I reach in 30 minutes?") and the autoluw score with its explanation;
   - **"Kopen als starter"** (municipal starter loans and buyer rules, see 2.10);
   - **"Wat zegt de raad?"** (council intelligence);
   - **"Waardeer deze woning"** (valuation, phase 4).
5. **Weights:** sliders for how much each dimension counts (for example, families may weigh education more). The composite score and colours update live. The weights are saved locally.

**Scoring:** each indicator is turned into a 0–100 score against the national distribution of all buurten (percentiles), so green or red means "better or worse than typical in the Netherlands". The national and gemeente averages always show next to the raw values.

---

## 4. Valuation (later phase: LLM at inference time)
- **Input:** a structured **property dossier** JSON with:
  - BAG facts;
  - WOZ history;
  - energy label;
  - plot and erfpacht;
  - foundation and soil risk;
  - buurt scores;
  - comparable homes from the last 12–24 months within about 500 m with similar type and size. These are asking prices and dates found through online search, adjusted to today's level with the CBS index;
  - development plans;
  - council signals;
  - accessibility and autoluw scores;
  - municipal buyer rules (Starterslening limits, opkoopbescherming, zelfbewoningsplicht);
  - user-supplied listing details.
- **Output:** a value range, the main drivers up and down, risks to check (such as a foundation survey or soil investigation), and confidence, with every claim citing a source in the dossier.
- **Implementation:** a Claude API call. A static page can't safely hold an API key, so we either (a) use a tiny serverless proxy (Cloudflare Worker), or (b) let the user paste their own key, stored only on the device. **Decision needed.**
- **Phase 1:** only the dossier schema is defined. No LLM calls.

---

## 5. Repository layout (proposed)
```
/app            index.html, app.js, styles.css, manifest.webmanifest, sw.js, icons/
/data           generated per area: buurten.geojson, grid.geojson, schools.json,
                soil.json, plans.json, raad/<area>.json, dossiers/<address>.json
/pipeline       fetch + transform scripts (Python), one module per source
/docs           due-diligence reports for the test areas (Markdown)
PLAN.md
```

## 6. Phasing
| Phase | Deliverable |
|---|---|
| **0 – Confirm** | Answer the open questions below and pin down the exact buurt codes for both test areas |
| **1 – Data & DD** | Pipeline scripts for the open sources (CBS KWB, buurt polygons, politie/CBS crime, DUO + Inspectie, Leefbaarometer, BAG, EP-Online, BRO soil, Bodemloket, Klimaateffectatlas, RIVM, ruimtelijke plannen), the mobility layer (CBS nabijheid, NWB, NDOV GTFS isochrones, verkeersbesluiten, RDW parking, autoluw score), plus the municipal buyer-rules table for Leiden and Alphen aan den Rijn. Output: `/data` for both areas plus a written **DD report per area** in `/docs` | **Status: first version done (Oct 2026)**: see `docs/dd/` and `app/data/`. The pipeline is in `pipeline/` and runs on GitHub Actions (Data workflow).
| **2 – App** | The map app with layers, bottom sheet, search, weights and PWA install, using the phase-1 data. Deployed on GitHub Pages |
| **3 – Raad** | The council-transcript pipeline for both test areas, pre-populated, plus the "Wat zegt de raad?" view |
| **4 – Valuation** | The dossier builder for any address, comparables from online search, the LLM valuation call, and the result card |
| **5 – Scale** | Any address in the Netherlands on demand, a monthly refresh job (GitHub Actions) for the council data and statistics |

## 7. Risks and limits
- **Crime** is only available per buurt, never per address. We say so in the UI.
- **Transaction prices** cost money at Kadaster, and **Funda can't be scraped**. We stay free: WOZ, CBS indices, and asking prices with dates found through online search. Asking prices can differ from final prices, especially when buyers overbid, so the valuation must treat them as an indication and say so.
- **WOZ-waardeloket** is meant for individual lookups. We respect that and don't bulk-scrape.
- **Council transcripts** often don't exist. Transcription costs compute, and speakers are hard to identify automatically.
- **Freshness:** CBS buurt data lags 1–2 years. Every value shows its reference date.
- **Municipal schemes** change often, and a Starterslening budget can run out mid-year. We show the date each rule was checked and always link to the gemeente's own page, so the user confirms before relying on it.
- **Privacy:** we show only aggregates and public registers, and no personal data from BRK.

## 8. Open questions
1. ~~**"Oudewaarde-Rijndijk"**: which place exactly?~~ **Answered:** Hazerswoude-Rijndijk, gemeente Alphen aan den Rijn.
2. ~~**Paid sources**~~ **Answered:** strictly free for now. Comparables come from online search of public listings (asking prices and dates).
3. **LLM key handling (phase 4):** a serverless proxy, or a key the user pastes on the device?
4. **UI language:** Dutch, English, or both?
5. **Scope of phase 1:** do the DD reports and data first and then the app (as planned), or a clickable app skeleton first?
