"""Turn the raw pipeline output into a due-diligence dossier per test area.

Every score is a national percentile (0-100, higher is better) over all Dutch buurten, so
"80" means better than 80% of buurten in the Netherlands. Streets that cross several buurten
are weighted by the number of addresses in each.

Usage: python pipeline/report.py <data-dir> <report-dir> [<repo-data-dir>]
Writes <area>.json (for the app) and <area>.md (the readable report) into <report-dir>.
"""
import bisect
import json
import math
import statistics
import sys
from pathlib import Path

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "out/data")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "out/report")
REPO_DATA = Path(sys.argv[3] if len(sys.argv) > 3 else Path(__file__).resolve().parent.parent / "data")
OUT.mkdir(parents=True, exist_ok=True)


def load(name, default=None):
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() else default


def num(v):
    try:
        f = float(str(v).strip())
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


class Ranking:
    """National distribution of one indicator; percentile() says how a value compares (100 = best)."""

    def __init__(self, values, higher_is_better):
        self.values = sorted(v for v in (num(x) for x in values) if v is not None)
        self.higher = higher_is_better

    def percentile(self, value):
        value = num(value)
        if value is None or not self.values:
            return None
        lo = bisect.bisect_left(self.values, value)
        hi = bisect.bisect_right(self.values, value)
        share_below = (lo + hi) / 2 / len(self.values)
        return round(100 * (share_below if self.higher else 1 - share_below))

    def median(self):
        return statistics.median(self.values) if self.values else None


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def point(doc):
    lon, lat = doc["centroide_ll"][6:-1].split()
    return float(lon), float(lat)


def weighted(items):
    """Weighted mean of (value, weight) pairs, ignoring missing values."""
    items = [(v, w) for v, w in items if v is not None]
    total = sum(w for _, w in items)
    return round(sum(v * w for v, w in items) / total) if total else None


# ---------------------------------------------------------------- load everything once

areas = load("areas.json", {})
buurt_props = {f["properties"]["buurtcode"]: f["properties"] for f in load("buurten.geojson", {"features": []})["features"]}
gemeenten = {g["gemeentecode"]: g for g in load("gemeenten.json", [])}
national = load("national/buurten.json", [])
nat_by_code = {r["buurtcode"]: r for r in national}

crime_year = load("crime/47018NED.json", [])
crime_month = load("crime/47022NED.json", [])
crime_nat = load("national/crime_totals.json", {"periode": None, "rows": {}})
lbm_local = load("leefbaarometer/Leefbaarometer-scores_buurten_2002-2024.json", [])
lbm_gem = load("leefbaarometer/Leefbaarometer-scores_gemeenten_2002-2024.json", [])
lbm_nat = load("national/leefbaarometer_buurten.json", [])
rivm_nat = load("national/rivm_buurten.json", [])
rivm_by_code = {r["buurtcode"]: r for r in rivm_nat}
vestigingen = load("schools/vestigingen.json", [])
eindscores = load("schools/wpo-eindscores.json", [])
eind_nat = load("national/eindscores.json", [])
advies = load("schools/wpoadvies-v1.json", [])
prices = load("prices/83625NED.json", [])
soil = load("soil/bodemkaart.json", {})
pubs = load("publications/bekendmakingen.json", {})
council = load("council/ori_leiden.json", {})
bag = load("bag/adressen.json", {})
oordelen = {}
for path in sorted(DATA.glob("schools/oordelen-v02-*.json")):
    for r in json.loads(path.read_text()):
        oordelen.setdefault(r.get("INSTELLINGSCODE", "").strip(), []).append(r)

# National rankings
R = {}
for field, higher in [
    ("gemiddeldeWoningwaarde", True), ("gemiddeldInkomenPerInwoner", True), ("percentageKoopwoningen", True),
    ("opritHoofdverkeerswegGemiddeldeAfstandInKm", False), ("treinstationGemiddeldeAfstandInKm", False),
    ("overstapstationGemiddeldeAfstandInKm", False), ("basisonderwijsGemiddeldeAfstandInKm", False),
    ("voortgezetOnderwijsGemAfstandInKm", False), ("huisartsenpraktijkGemiddeldeAfstandInKm", False),
    ("groteSupermarktGemiddeldeAfstandInKm", False), ("kinderdagverblijfGemiddeldeAfstandInKm", False),
    ("afstandTotOpenbaarGroenTotaal", False), ("afstandTotParkOfPlantsoen", False),
    ("personenautosPerHuishouden", False), ("bevolkingsdichtheidInwonersPerKm2", True),
]:
    R[field] = Ranking([r.get(field) for r in national], higher)

crime_rate_nat = {}
for code, total in crime_nat["rows"].items():
    inw = num((nat_by_code.get(code) or {}).get("aantalInwoners"))
    if total is not None and inw and inw >= 100:
        crime_rate_nat[code] = 1000 * total / inw
R["crime_rate"] = Ranking(crime_rate_nat.values(), False)
for field in ("lbm", "onv", "fys", "soc", "vrz", "won"):
    R[f"lbm_{field}"] = Ranking([r.get(field) for r in lbm_nat], True)
for field, higher in [("b_tevr_wo", True), ("b_tevr_won", True), ("b_tevr_gr", True), ("b_gel_weg", False),
                      ("b_gel_trei", False), ("b_gel_vv", False), ("b_sv_weg", False), ("b_gel_bu", False),
                      ("b_nverk_bi", False)]:
    R[field] = Ranking([r.get(field) for r in rivm_nat], higher)
eind_rank = {}
for r in eind_nat:
    eind_rank.setdefault(r["DOORSTROOMTOETS_PO"].strip(), []).append(r["GEMIDDELDE_UITSLAG"])
eind_rank = {k: Ranking(v, True) for k, v in eind_rank.items()}
eind_year = max((r["PEILJAAR"] for r in eind_nat), default=None)

CRIME_NAMES = {
    "0.0.0": "Totaal misdrijven", "1.1.1": "Woninginbraak", "1.1.2": "Inbraak box/garage/schuur",
    "1.2.1": "Diefstal uit/vanaf auto", "1.2.3": "Fietsdiefstal", "1.2.4": "Zakkenrollerij",
    "1.4.3": "Openlijk geweld", "1.4.4": "Bedreiging", "1.4.5": "Mishandeling", "1.4.6": "Straatroof",
    "1.4.7": "Overval", "2.1.1": "Drugs-/drankoverlast", "2.2.1": "Vernieling", "2.4.2": "Huisvredebreuk",
    "3.1.1": "Drugshandel", "3.6.4": "Aantasting openbare orde",
}

# ---------------------------------------------------------------- per-area dossier


def latest_lbm(code, rows, code_key):
    mine = sorted((r for r in rows if r[code_key] == code), key=lambda r: r["jaar"])
    return mine


def crime_for(code):
    rows = [r for r in crime_year if r["regio"] == code]
    by_year = {}
    for r in rows:
        by_year.setdefault(r["Perioden"][:4], {})[r["SoortMisdrijf"]] = r["GeregistreerdeMisdrijven_1"]
    return by_year


def buurt_section(code):
    p = buurt_props.get(code, {})
    inw = num(p.get("aantalInwoners"))
    years = crime_for(code)
    latest_year = max(years) if years else None
    total = years.get(latest_year, {}).get("0.0.0") if latest_year else None
    rate = 1000 * total / inw if total is not None and inw else None
    lbm_rows = latest_lbm(code, lbm_local, "bu_code")
    lbm = lbm_rows[-1] if lbm_rows else {}
    rivm = rivm_by_code.get(code, {})
    top_types = sorted(((k, v) for k, v in years.get(latest_year, {}).items() if k != "0.0.0" and v),
                       key=lambda kv: -kv[1])[:6]
    monthly = sorted((r["Perioden"], r["GeregistreerdeMisdrijven_1"]) for r in crime_month if r["regio"] == code)
    return {
        "buurtcode": code, "buurtnaam": p.get("buurtnaam"), "wijknaam": None,
        "inwoners": inw, "huishoudens": num(p.get("aantalHuishoudens")),
        "kerncijfers": {k: p.get(k) for k in (
            "gemiddeldeWoningwaarde", "percentageKoopwoningen", "percentageHuurwoningen",
            "percHuurwoningenInBezitWoningcorporaties", "percentageEengezinswoning", "percentageMeergezinswoning",
            "percentageBouwjaarklasseTot2000", "percentageBouwjaarklasseVanaf2000", "gemiddeldInkomenPerInwoner",
            "percentageHuishoudensMetKinderen", "percentageEenpersoonshuishoudens", "personenautosPerHuishouden",
            "bevolkingsdichtheidInwonersPerKm2", "percentageWoningenMetZonnestroom", "gemiddeldAardgasverbruik",
            "opritHoofdverkeerswegGemiddeldeAfstandInKm", "treinstationGemiddeldeAfstandInKm",
            "overstapstationGemiddeldeAfstandInKm", "basisonderwijsGemiddeldeAfstandInKm",
            "voortgezetOnderwijsGemAfstandInKm", "havoVwoGemiddeldeAfstandInKm", "huisartsenpraktijkGemiddeldeAfstandInKm",
            "groteSupermarktGemiddeldeAfstandInKm", "kinderdagverblijfGemiddeldeAfstandInKm",
            "afstandTotOpenbaarGroenTotaal", "afstandTotParkOfPlantsoen", "jaar")},
        "percentielen": {k: R[k].percentile(p.get(k)) for k in R if k in p},
        "criminaliteit": {
            "jaar": latest_year, "totaal": total, "per_1000_inwoners": round(rate, 1) if rate is not None else None,
            "percentiel": R["crime_rate"].percentile(rate) if inw and inw >= 100 else None,
            "landelijke_mediaan_per_1000": round(R["crime_rate"].median(), 1) if R["crime_rate"].values else None,
            "per_jaar": {y: v.get("0.0.0") for y, v in sorted(years.items())},
            "meest_voorkomend": [{"soort": CRIME_NAMES.get(k, k), "aantal": v} for k, v in top_types],
            "per_maand": monthly,
        },
        "leefbaarometer": {
            "jaar": lbm.get("jaar"), "score": num(lbm.get("lbm")),
            "percentiel": R["lbm_lbm"].percentile(lbm.get("lbm")),
            "dimensies": {name: {"waarde": num(lbm.get(k)), "percentiel": R[f"lbm_{k}"].percentile(lbm.get(k))}
                          for k, name in (("fys", "fysieke omgeving"), ("onv", "overlast en onveiligheid"),
                                          ("soc", "sociale samenhang"), ("vrz", "voorzieningen"), ("won", "woningvoorraad"))},
            "trend": [(r["jaar"], round(num(r["lbm"]), 3)) for r in lbm_rows if num(r["lbm"]) is not None],
        },
        "gezondheidsmonitor": {
            "jaar": rivm.get("jaar"),
            **{k: {"waarde": rivm.get(k), "percentiel": R[k].percentile(rivm.get(k))} for k in
               ("b_tevr_wo", "b_tevr_won", "b_tevr_gr", "b_gel_weg", "b_gel_trei", "b_gel_vv", "b_sv_weg", "b_gel_bu", "b_nverk_bi")},
        },
    }


def schools_near(center):
    out = []
    for v in vestigingen:
        g = v.get("_geo")
        if not g:
            continue
        d = haversine_km(center, (g["lon"], g["lat"]))
        limit = 2.0 if v["_sector"] == "po" else 6.0
        if d > limit:
            continue
        inst, vest = v["INSTELLINGSCODE"].strip(), v["VESTIGINGSCODE"].strip()
        mine = [r for r in eindscores if r["INSTELLINGSCODE"].strip() == inst and (inst + r["VESTIGINGSCODE"].strip()[-2:]) == vest]
        latest = max(mine, key=lambda r: r["PEILJAAR"], default=None)
        score = pct = None
        if latest and latest["PEILJAAR"] == eind_year:
            score = num(latest["GEMIDDELDE_UITSLAG"])
            rank = eind_rank.get(latest["DOORSTROOMTOETS_PO"].strip())
            pct = rank.percentile(score) if rank else None
        out.append({
            "naam": v["VESTIGINGSNAAM"].strip(), "sector": v["_sector"], "afstand_km": round(d, 2),
            "denominatie": v.get("DENOMINATIE", "").strip(), "adres": f"{v['STRAATNAAM'].strip()} {v['HUISNUMMER-TOEVOEGING'].strip()}, {v['PLAATSNAAM'].strip().title()}",
            "onderwijsstructuur": v.get("ONDERWIJSSTRUCTUUR", "").strip() or None,
            "website": v.get("INTERNETADRES", "").strip() or None, "lon": v["_geo"]["lon"], "lat": v["_geo"]["lat"],
            # DUO publishes -1 where numbers are suppressed for privacy (small groups)
            "doorstroomtoets": {"jaar": latest["PEILJAAR"], "toets": latest["DOORSTROOMTOETS_PO"].strip(),
                                "gemiddelde": score if score is None or score >= 0 else None,
                                "leerlingen": num(latest["AANTAL_LEERLINGEN"]) if (num(latest["AANTAL_LEERLINGEN"]) or 0) >= 0 else None,
                                "percentiel": pct if score is not None and score >= 0 else None} if latest else None,
            "inspectie": oordelen.get(inst),
        })
    out.sort(key=lambda s: (s["sector"], s["afstand_km"]))
    po = [s for s in out if s["sector"] == "po"]
    vo = [s for s in out if s["sector"] == "vo"][:12]
    return po + vo


def gemeente_prices(code):
    rows = sorted((r["Perioden"][:4], r["GemiddeldeVerkoopprijs_1"]) for r in prices if r["RegioS"].strip() == code)
    return [(y, v) for y, v in rows if v]


def index_to_latest(price, year, series):
    """Bring an asking price from an earlier year to the latest year with the gemeente's average-price trend."""
    s = dict(series)
    latest_year = max(s) if s else None
    if not price or not year or not latest_year:
        return None, latest_year
    if str(year) >= latest_year:
        return price, latest_year  # no later price data to index with
    if str(year) not in s:
        return None, latest_year
    return round(price * s[latest_year] / s[str(year)], -3), latest_year


def classify_publication(r):
    t = (r.get("title") or "").lower()
    for key, words in (("verkeersbesluit", ("verkeersbesluit", "parkeer", "verkeersmaatregel", "gehandicaptenparkeerplaats")),
                       ("omgevingsvergunning", ("omgevingsvergunning", "aanvraag", "vergunning")),
                       ("bestemmingsplan", ("bestemmingsplan", "omgevingsplan", "wijzigingsplan")),
                       ("evenement", ("evenement", "ontheffing"))):
        if any(w in t for w in words):
            return key
    return "overig"


def dimension_scores(sections, weights, schools):
    def w(fn):
        return weighted([(fn(s), weights[s["buurtcode"]]) for s in sections])

    po = [s["doorstroomtoets"]["percentiel"] for s in schools
          if s["sector"] == "po" and s["doorstroomtoets"] and s["doorstroomtoets"]["percentiel"] is not None and s["afstand_km"] <= 1.5]

    def avg(*vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals)) if vals else None

    gm = sections[0]["gezondheidsmonitor"] if sections else {}
    return {
        "veiligheid": avg(w(lambda s: s["criminaliteit"]["percentiel"]),
                          w(lambda s: s["leefbaarometer"]["dimensies"]["overlast en onveiligheid"]["percentiel"])),
        "leefbaarheid": avg(w(lambda s: s["leefbaarometer"]["percentiel"]),
                            w(lambda s: s["gezondheidsmonitor"]["b_tevr_wo"]["percentiel"])),
        "onderwijs": avg(round(sum(po) / len(po)) if po else None,
                         w(lambda s: s["percentielen"].get("basisonderwijsGemiddeldeAfstandInKm"))),
        "bereikbaarheid": avg(w(lambda s: s["percentielen"].get("opritHoofdverkeerswegGemiddeldeAfstandInKm")),
                              w(lambda s: s["percentielen"].get("treinstationGemiddeldeAfstandInKm")),
                              w(lambda s: s["percentielen"].get("overstapstationGemiddeldeAfstandInKm"))),
        "autoluw": avg(w(lambda s: s["percentielen"].get("personenautosPerHuishouden")),
                       w(lambda s: s["gezondheidsmonitor"]["b_gel_weg"]["percentiel"])),
        "voorzieningen": avg(w(lambda s: s["percentielen"].get("groteSupermarktGemiddeldeAfstandInKm")),
                             w(lambda s: s["percentielen"].get("huisartsenpraktijkGemiddeldeAfstandInKm")),
                             w(lambda s: s["percentielen"].get("kinderdagverblijfGemiddeldeAfstandInKm")),
                             w(lambda s: s["percentielen"].get("afstandTotOpenbaarGroenTotaal"))),
        "rust": avg(w(lambda s: s["gezondheidsmonitor"]["b_gel_weg"]["percentiel"]),
                    w(lambda s: s["gezondheidsmonitor"]["b_gel_trei"]["percentiel"]),
                    w(lambda s: s["gezondheidsmonitor"]["b_gel_vv"]["percentiel"]),
                    w(lambda s: s["gezondheidsmonitor"]["b_gel_bu"]["percentiel"])),
    }


def soil_summary(samples):
    names = {}
    unmapped = 0
    for s in samples:
        if isinstance(s["bodemkaart"], list) and s["bodemkaart"]:
            n = s["bodemkaart"][0].get("first_soilname") or s["bodemkaart"][0].get("soilname")
            names[n] = names.get(n, 0) + 1
        else:
            unmapped += 1
    return {"monsters": len(samples), "niet_gekarteerd": unmapped,
            "bodemtypen": sorted(({"naam": k, "monsters": v} for k, v in names.items()), key=lambda x: -x["monsters"])}


def bag_summary(rows):
    years = sorted(int(r["bouwjaar"]) for r in rows if str(r.get("bouwjaar") or "").isdigit())
    areas_m2 = sorted(int(r["oppervlakte"]) for r in rows if str(r.get("oppervlakte") or "").isdigit())
    buckets = {}
    for y in years:
        b = "voor 1900" if y < 1900 else "1900-1944" if y < 1945 else "1945-1969" if y < 1970 else "1970-1999" if y < 2000 else "2000 en later"
        buckets[b] = buckets.get(b, 0) + 1
    uses = {}
    for r in rows:
        for u in (r.get("gebruiksdoel") if isinstance(r.get("gebruiksdoel"), list) else [r.get("gebruiksdoel")]):
            if u:
                uses[u] = uses.get(u, 0) + 1
    return {"adressen": len(rows), "met_bouwjaar": len(years),
            "bouwjaar_mediaan": round(statistics.median(years)) if years else None,
            "bouwjaar_min": years[0] if years else None, "bouwjaar_max": years[-1] if years else None,
            "bouwjaar_klassen": buckets, "oppervlakte_mediaan_m2": round(statistics.median(areas_m2)) if areas_m2 else None,
            "gebruiksdoelen": uses}


def build(key, a):
    weights = {b["buurtcode"]: b["adressen"] for b in a["buurten"]}
    sections = [buurt_section(b["buurtcode"]) for b in a["buurten"]]
    for s, b in zip(sections, a["buurten"]):
        s["wijknaam"], s["adressen_op_straat"] = b["wijknaam"], b["adressen"]
    pts = [point(d) for d in a["adressen"]]
    center = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    schools = schools_near(center)
    gm_code = a["gemeentecode"]
    gm_lbm = latest_lbm(gm_code, lbm_gem, "gm_code")
    series = gemeente_prices(gm_code)
    listings_file = REPO_DATA / "listings" / f"{key}.json"
    listings = json.loads(listings_file.read_text()) if listings_file.exists() else None
    if listings:
        for item in listings.get("listings", []):
            year = (item.get("sold_on") or item.get("listed_on") or "")[:4]
            item["asking_price_indexed_eur"], item["indexed_to_year"] = index_to_latest(item.get("asking_price_eur"), year, series)
    regel_file = {"GM0546": "leiden.json", "GM0484": "alphen-aan-den-rijn.json"}[gm_code]
    regelingen = [json.loads((REPO_DATA / "regelingen" / f).read_text()) for f in (regel_file, "landelijk.json")
                  if (REPO_DATA / "regelingen" / f).exists()]
    publications = (pubs.get(key) or {}).get("records", [])
    for r in publications:
        r["categorie"] = classify_publication(r)
    pub_counts = {}
    for r in publications:
        pub_counts[r["categorie"]] = pub_counts.get(r["categorie"], 0) + 1

    return {
        "key": key, "straat": a["straat"], "woonplaats": a["woonplaats"], "gemeente": a["gemeentenaam"],
        "gemeentecode": gm_code, "centrum": {"lon": round(center[0], 6), "lat": round(center[1], 6)},
        "adressen": len(a["adressen"]),
        "scores": dimension_scores(sections, weights, schools),
        "buurten": sections,
        "gemeente_vergelijking": {
            "leefbaarometer": {"jaar": gm_lbm[-1]["jaar"], "score": num(gm_lbm[-1]["lbm"])} if gm_lbm else None,
            "gemiddeldeWoningwaarde": gemeenten.get(gm_code, {}).get("gemiddeldeWoningwaarde"),
            "inwoners": gemeenten.get(gm_code, {}).get("aantalInwoners"),
            "criminaliteit_per_jaar": {y: v.get("0.0.0") for y, v in sorted(crime_for(gm_code).items())},
            "gemiddelde_verkoopprijs": series[-12:],
            "gemiddelde_verkoopprijs_nl": gemeente_prices("NL01")[-12:],
        },
        "scholen": schools,
        "bodem": soil_summary(soil.get(key, [])),
        "bebouwing": bag_summary(bag.get(key, [])),
        "aanbod": listings,
        "regelingen": regelingen,
        "bekendmakingen": {"aantal": len(publications), "per_categorie": pub_counts,
                           "recent": [{k: r.get(k) for k in ("title", "available", "type", "categorie", "preferredUrl")}
                                      for r in publications[:25]]},
        "raad": (council.get(key) or [])[:40],
        "bronnen": SOURCES,
    }


SOURCES = [
    {"naam": "CBS Kerncijfers wijken en buurten 2024", "via": "PDOK WFS wijkenbuurten 2024", "url": "https://service.pdok.nl/cbs/wijkenbuurten/2024/wfs/v1_0"},
    {"naam": "Politie: geregistreerde misdrijven per wijk/buurt (47018NED, 47022NED)", "via": "CBS dataderden", "url": "https://dataderden.cbs.nl/ODataApi/odata/47018NED"},
    {"naam": "Leefbaarometer 3.0, meting 2024 (BZK)", "url": "https://www.leefbaarometer.nl/page/Opendata"},
    {"naam": "RIVM Gezondheidsmonitor Volwassenen en Ouderen 2024 (buurt)", "via": "Atlas Leefomgeving WFS", "url": "https://data.rivm.nl/geo/alo/wfs"},
    {"naam": "DUO open onderwijsdata: vestigingen, doorstroomtoets, advies, inspectie-oordelen", "url": "https://onderwijsdata.duo.nl/datasets"},
    {"naam": "BRO Bodemkaart", "via": "PDOK WMS", "url": "https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0"},
    {"naam": "BAG (Kadaster)", "via": "PDOK OGC API", "url": "https://api.pdok.nl/kadaster/bag/ogc/v2"},
    {"naam": "CBS gemiddelde verkoopprijzen bestaande koopwoningen (83625NED)", "url": "https://opendata.cbs.nl/ODataApi/odata/83625NED"},
    {"naam": "Officiële bekendmakingen (Gemeenteblad, Staatscourant)", "via": "KOOP SRU", "url": "https://repository.overheid.nl/sru"},
    {"naam": "Open Raadsinformatie (Leiden)", "url": "https://api.openraadsinformatie.nl"},
    {"naam": "PDOK Locatieserver (adressen, buurtindeling)", "url": "https://api.pdok.nl/bzk/locatieserver/search/v3_1"},
]

# ---------------------------------------------------------------- markdown report


def fmt_eur(v):
    return f"€{int(v):,}".replace(",", ".") if v is not None else "n/a"


def fmt(v, suffix=""):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        v = int(v) if v.is_integer() else round(v, 1)
    return f"{v}{suffix}"


def bar(p):
    return "n/a" if p is None else f"{p}/100"


LABELS = {"veiligheid": "Safety", "leefbaarheid": "Liveability", "onderwijs": "Education", "bereikbaarheid": "Accessibility",
          "autoluw": "Car-light (autoluw)", "voorzieningen": "Amenities", "rust": "Quiet (noise nuisance)"}


def markdown(d):
    L = []
    add = L.append
    add(f"# Due diligence: {d['straat']}, {d['woonplaats']}")
    add("")
    add(f"Gemeente {d['gemeente']} · {d['adressen']} addresses on the street · buurten: "
        + ", ".join(f"{s['buurtnaam']} ({s['adressen_op_straat']} addresses)" for s in d["buurten"]))
    add("")
    add("Scores are national percentiles over all Dutch buurten: 50 is typical, 100 is the best in the Netherlands. "
        "Where the street crosses several buurten, they are weighted by the number of addresses.")
    add("")
    add("## Scores")
    add("")
    add("| Theme | Score | Based on |")
    add("|---|---|---|")
    basis = {"veiligheid": "registered crime per 1,000 residents; Leefbaarometer nuisance and insecurity",
             "leefbaarheid": "Leefbaarometer total; residents' satisfaction with their surroundings (RIVM)",
             "onderwijs": "doorstroomtoets results of primary schools within 1.5 km; distance to primary school",
             "bereikbaarheid": "distance to highway on-ramp, train station and transfer station (CBS)",
             "autoluw": "cars per household; share of residents severely bothered by road noise (proxy, see below)",
             "voorzieningen": "distance to supermarket, GP, day care, public green space",
             "rust": "share of residents severely bothered by noise from road, rail, air and neighbours (RIVM)"}
    for k, v in d["scores"].items():
        add(f"| {LABELS[k]} | **{bar(v)}** | {basis[k]} |")
    add("")
    for s in d["buurten"]:
        k = s["kerncijfers"]
        c = s["criminaliteit"]
        lb = s["leefbaarometer"]
        gm = s["gezondheidsmonitor"]
        add(f"## Buurt {s['buurtnaam']} ({s['buurtcode']}), wijk {s['wijknaam']}")
        add("")
        add(f"{fmt(s['inwoners'])} residents, {fmt(s['huishoudens'])} households; CBS figures for {k.get('jaar')}.")
        add("")
        add("**Housing and residents**")
        add("")
        add(f"- Average WOZ value: {fmt_eur(num(k.get('gemiddeldeWoningwaarde')) * 1000 if num(k.get('gemiddeldeWoningwaarde')) else None)} "
            f"(national percentile {fmt(s['percentielen'].get('gemiddeldeWoningwaarde'))})")
        add(f"- Owner-occupied {fmt(k.get('percentageKoopwoningen'), '%')}, rented {fmt(k.get('percentageHuurwoningen'), '%')} "
            f"(housing association {fmt(k.get('percHuurwoningenInBezitWoningcorporaties'), '%')}); "
            f"single-family homes {fmt(k.get('percentageEengezinswoning'), '%')}")
        if k.get("percentageBouwjaarklasseTot2000") is not None:
            add(f"- Built before 2000: {fmt(k.get('percentageBouwjaarklasseTot2000'), '%')}; from 2000: {fmt(k.get('percentageBouwjaarklasseVanaf2000'), '%')}")
        income = num(k.get("gemiddeldInkomenPerInwoner"))
        income_text = fmt_eur(income * 1000) if income else f"not yet published by CBS for {k.get('jaar')}"
        add(f"- Average income per resident: {income_text}; "
            f"households with children {fmt(k.get('percentageHuishoudensMetKinderen'), '%')}, single-person {fmt(k.get('percentageEenpersoonshuishoudens'), '%')}")
        add("")
        add("**Safety**")
        add("")
        add(f"- {fmt(c['totaal'])} registered crimes in {c['jaar']}: {fmt(c['per_1000_inwoners'])} per 1,000 residents "
            f"(national median {fmt(c['landelijke_mediaan_per_1000'])}; percentile {fmt(c['percentiel'])}, higher is safer)")
        central = (s["percentielen"].get("bevolkingsdichtheidInwonersPerKm2") or 0) >= 90 or (k.get("groteSupermarktGemiddeldeAfstandInKm") or 9) <= 0.5
        if central and (c["percentiel"] or 100) < 25:
            add("- Context: police register crimes where they happen, not where the victims live. Busy central buurten with "
                "shops, nightlife or a station count every visitor's incident against a small resident population, so the "
                "rate per resident overstates the risk for people living there. Compare the mix of crime types and the trend.")
        add(f"- Trend: " + ", ".join(f"{y}: {v}" for y, v in c["per_jaar"].items()))
        if c["meest_voorkomend"]:
            add("- Most common in " + str(c["jaar"]) + ": " + ", ".join(f"{t['soort']} ({t['aantal']})" for t in c["meest_voorkomend"]))
        add("")
        add("**Liveability**")
        add("")
        add(f"- Leefbaarometer {lb['jaar']}: {fmt(lb['score'])} (percentile {fmt(lb['percentiel'])}). Dimensions: "
            + "; ".join(f"{n} {fmt(v['percentiel'])}" for n, v in lb["dimensies"].items()))
        if lb["trend"]:
            add(f"- Trend: " + ", ".join(f"{y}: {v}" for y, v in lb["trend"][-6:]))
        add(f"- Residents satisfied with their surroundings: {fmt(gm['b_tevr_wo']['waarde'], '%')} (percentile {fmt(gm['b_tevr_wo']['percentiel'])}); "
            f"with their home: {fmt(gm['b_tevr_won']['waarde'], '%')}; with greenery: {fmt(gm['b_tevr_gr']['waarde'], '%')}")
        add(f"- Severely bothered by road noise: {fmt(gm['b_gel_weg']['waarde'], '%')}; rail {fmt(gm['b_gel_trei']['waarde'], '%')}; "
            f"air traffic {fmt(gm['b_gel_vv']['waarde'], '%')}; neighbours {fmt(gm['b_gel_bu']['waarde'], '%')} (RIVM {gm['jaar']})")
        add("")
        add("**Accessibility and amenities (average distance by road, CBS)**")
        add("")
        add(f"- Highway on-ramp {fmt(k.get('opritHoofdverkeerswegGemiddeldeAfstandInKm'), ' km')}, train station {fmt(k.get('treinstationGemiddeldeAfstandInKm'), ' km')}, "
            f"transfer station {fmt(k.get('overstapstationGemiddeldeAfstandInKm'), ' km')}")
        add(f"- Primary school {fmt(k.get('basisonderwijsGemiddeldeAfstandInKm'), ' km')}, secondary {fmt(k.get('voortgezetOnderwijsGemAfstandInKm'), ' km')}, "
            f"havo/vwo {fmt(k.get('havoVwoGemiddeldeAfstandInKm'), ' km')}")
        add(f"- Supermarket {fmt(k.get('groteSupermarktGemiddeldeAfstandInKm'), ' km')}, GP {fmt(k.get('huisartsenpraktijkGemiddeldeAfstandInKm'), ' km')}, "
            f"day care {fmt(k.get('kinderdagverblijfGemiddeldeAfstandInKm'), ' km')}, public green {fmt(k.get('afstandTotOpenbaarGroenTotaal'), ' km')}")
        add(f"- Cars per household: {fmt(k.get('personenautosPerHuishouden'))} (percentile {fmt(s['percentielen'].get('personenautosPerHuishouden'))}, higher means fewer cars)")
        add("")
    g = d["gemeente_vergelijking"]
    add(f"## Gemeente {d['gemeente']} for comparison")
    add("")
    if g["leefbaarometer"]:
        add(f"- Leefbaarometer {g['leefbaarometer']['jaar']}: {fmt(g['leefbaarometer']['score'])}")
    add(f"- Registered crimes per year: " + ", ".join(f"{y}: {v}" for y, v in g["criminaliteit_per_jaar"].items()))
    if g["gemiddelde_verkoopprijs"]:
        add(f"- Average sale price of existing homes (CBS/Kadaster): " + ", ".join(f"{y}: {fmt_eur(v)}" for y, v in g["gemiddelde_verkoopprijs"][-6:]))
        nl = dict(g["gemiddelde_verkoopprijs_nl"])
        y = g["gemiddelde_verkoopprijs"][-1][0]
        if y in nl:
            add(f"- Netherlands {y}: {fmt_eur(nl[y])}")
    add("")
    add("## Schools nearby")
    add("")
    add(f"Primary schools within 2 km and secondary schools within 6 km of the street. Doorstroomtoets {eind_year}: average score and "
        "national percentile among schools that took the same test.")
    add("")
    add("| School | Type | Distance | Doorstroomtoets | Percentile |")
    add("|---|---|---|---|---|")
    for s in d["scholen"]:
        t = s["doorstroomtoets"]
        toets = (f"{t['toets']} {t['jaar']}: {fmt(t['gemiddelde'])} ({fmt(t['leerlingen'])} pupils)" if t and t["gemiddelde"] is not None
                 else f"{t['toets']} {t['jaar']}: not published (small group)" if t else "–")
        kind = "primary" if s["sector"] == "po" else f"secondary ({s['onderwijsstructuur'] or ''})"
        add(f"| {s['naam']} | {kind} | {s['afstand_km']} km | {toets} | {fmt(t['percentiel']) if t else '–'} |")
    add("")
    b = d["bebouwing"]
    add("## Buildings on the street (BAG)")
    add("")
    if b["adressen"]:
        add(f"- {b['met_bouwjaar']} of {b['adressen']} addresses have a building year; median {fmt(b['bouwjaar_mediaan'])}, "
            f"range {fmt(b['bouwjaar_min'])}–{fmt(b['bouwjaar_max'])}")
        add("- By period: " + ", ".join(f"{k}: {v}" for k, v in b["bouwjaar_klassen"].items()))
        add(f"- Median floor area {fmt(b['oppervlakte_mediaan_m2'], ' m²')}; uses: " + ", ".join(f"{k} ({v})" for k, v in b["gebruiksdoelen"].items()))
    else:
        add("- Not yet available.")
    add("")
    so = d["bodem"]
    add("## Soil (BRO Bodemkaart)")
    add("")
    if so["bodemtypen"]:
        for t in so["bodemtypen"]:
            add(f"- {t['naam']} ({t['monsters']} of {so['monsters']} sample points)")
    if so["niet_gekarteerd"]:
        add(f"- {so['niet_gekarteerd']} of {so['monsters']} sample points are not mapped. The Bodemkaart does not cover built-up urban areas, "
            "so soil type there needs another source (GeoTOP, local soil investigations).")
    b = d["bebouwing"]
    old = sum(v for k, v in b.get("bouwjaar_klassen", {}).items() if k in ("voor 1900", "1900-1944", "1945-1969"))
    soft = any(w in t["naam"].lower() for t in so["bodemtypen"] for w in ("klei", "veen", "zavel"))
    add("")
    add("**Foundation risk indication**")
    add("")
    if b.get("met_bouwjaar"):
        if old and soft:
            add(f"- {old} of {b['met_bouwjaar']} addresses are in buildings from before 1970, on clay or peat soils. Buildings of that age on soft "
                "soil often stand on wooden piles or shallow foundations, which can deteriorate with falling groundwater "
                "(pile rot) or subsidence. Ask for a foundation survey (funderingsonderzoek) for any pre-1970 property here.")
        elif old:
            add(f"- {old} of {b['met_bouwjaar']} addresses are in buildings from before 1970. The soil here is not mapped, so check the "
                "foundation type for older properties.")
        else:
            add(f"- All buildings date from {b['bouwjaar_min']} or later. Buildings of this age normally stand on concrete piles; "
                "foundation risk is low.")
    add("- Soil contamination (Bodemloket) is not yet included; see open items.")
    add("")
    add("## Market: asking prices found online")
    add("")
    ls = d["aanbod"]
    if ls and ls.get("listings"):
        add("Asking prices (vraagprijs k.k.), not sale prices. \"Indexed\" brings the asking price to the latest year "
            "with the gemeente's average-price trend (CBS).")
        add("")
        add("| Address | Type | Asking price | Listed | Sold | Indexed |")
        add("|---|---|---|---|---|---|")
        for i in ls["listings"]:
            add(f"| [{i['address']}]({i['source']}) | {i['type']} | {fmt_eur(i['asking_price_eur'])} | {i['listed_on'] or '–'} | "
                f"{i['sold_on'] or i['status']} | {fmt_eur(i.get('asking_price_indexed_eur'))} |")
    elif ls:
        add(ls.get("price_note", ""))
        for v in ls.get("other_values", []):
            val = fmt_eur(v.get("value_eur")) if v.get("value_eur") else " – ".join(fmt_eur(x) for x in v.get("value_eur_range", []))
            add(f"- [{v['address']}]({v['source']}): {v['kind']}: {val}")
    add("")
    add("## Buyer rules and starter loans")
    add("")
    for doc in d["regelingen"]:
        for r in doc["regelingen"]:
            src = "; ".join(f"[{s['titel']}]({s['url']}){'' if s.get('officieel') else ' (unofficial)'}" for s in r.get("bronnen", []))
            extra = f"€{r['bedrag_eur']:,}".replace(",", ".") + f" ({r['jaar']})" if r.get("bedrag_eur") else ""
            status = f" ({r['status']})" if r.get("status") else ""
            add(f"- **{r['naam']}**{status}: {r.get('samenvatting') or extra} Sources: {src}")
    add("")
    pb = d["bekendmakingen"]
    add("## Official publications about the street")
    add("")
    add(f"{pb['aantal']} publications in the Gemeenteblad and Staatscourant mention the street: "
        + ", ".join(f"{k} {v}" for k, v in pb["per_categorie"].items()) + ".")
    add("")
    for r in pb["recent"][:15]:
        add(f"- {r.get('available')}: [{r.get('title')}]({r.get('preferredUrl')})")
    add("")
    add("## Council documents (gemeenteraad)")
    add("")
    if d["raad"]:
        for h in d["raad"][:15]:
            add(f"- {(h.get('date') or '')[:10]}: [{h.get('name')}]({h.get('url')}) (matched \"{h.get('term')}\")")
            if h.get("highlight"):
                quote = " ".join(h["highlight"][0].replace("<em>", "**").replace("</em>", "**").split())
                add(f"  > {quote[:280]}")
    else:
        add("- Not yet available for this gemeente: Alphen aan den Rijn is not in Open Raadsinformatie. "
            "Its council system (alphenaandenrijn.bestuurlijkeinformatie.nl) is the next source to add.")
    add("")
    add("## Notes and open items")
    add("")
    add("- **Autoluw** is approximated by cars per household and road-noise nuisance. Traffic decisions (verkeersbesluiten), "
        "speed limits and parking regimes are planned additions.")
    add("- **Energy labels** (EP-Online) need an API key and are not included yet.")
    add("- **School inspection verdicts**: DUO's published download link does not resolve; to be fetched from the Inspectie directly. "
        "Secondary-school exam grades on DUO stop at 2017, so they are not used.")
    add("- **Soil contamination** (Bodemloket) and a 100 m Leefbaarometer grid are planned.")
    add("- **Crime** is published per buurt, never per address.")
    add("- **Asking prices** come from public listing pages found through online search and are not sale prices.")
    add("")
    add("## Sources")
    add("")
    for s in d["bronnen"]:
        add(f"- {s['naam']}{' (via ' + s['via'] + ')' if s.get('via') else ''}: {s['url']}")
    return "\n".join(L) + "\n"


def map_layer():
    """All buurten in the test gemeenten with per-theme scores, for colouring the map."""
    features = []
    for f in load("buurten.geojson", {"features": []})["features"]:
        p = f["properties"]
        if p.get("water") == "J" or not p.get("aantalInwoners"):
            continue
        sec = buurt_section(p["buurtcode"])
        d = dimension_scores([sec], {p["buurtcode"]: 1}, [])
        d.pop("onderwijs")  # needs a street location; shown on the street card only
        f = {"type": "Feature", "geometry": f["geometry"], "properties": {
            "buurtcode": p["buurtcode"], "buurtnaam": p["buurtnaam"], "gemeentenaam": p["gemeentenaam"],
            "inwoners": sec["inwoners"], "woz": num(p.get("gemiddeldeWoningwaarde")),
            "misdrijven_per_1000": sec["criminaliteit"]["per_1000_inwoners"],
            "leefbaarometer": sec["leefbaarometer"]["score"], **{f"score_{k}": v for k, v in d.items()}}}
        features.append(f)
    return {"type": "FeatureCollection", "features": features}


if __name__ == "__main__":
    (OUT / "buurten-scores.geojson").write_text(json.dumps(map_layer(), ensure_ascii=False))
    index = []
    for key, a in areas.items():
        dossier = build(key, a)
        (OUT / f"{key}.json").write_text(json.dumps(dossier, ensure_ascii=False, indent=1))
        (OUT / f"{key}.md").write_text(markdown(dossier))
        index.append({"key": key, "straat": dossier["straat"], "woonplaats": dossier["woonplaats"], "scores": dossier["scores"]})
        # per-address building facts for the valuation form
        addresses = [{k: r.get(k) for k in ("adres", "postcode", "huisnummer", "buurtcode", "oppervlakte", "bouwjaar", "gebruiksdoel")}
                     for r in bag.get(key, [])]
        (OUT / f"{key}-addresses.json").write_text(json.dumps(addresses, ensure_ascii=False))
        print(key, dossier["scores"])
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
