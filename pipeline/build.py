"""Prospect data pipeline.

Fetches official open data for the test areas and writes static JSON/GeoJSON for the app.
Each step is independent: a failing source is logged and the others still run.

Usage: python pipeline/build.py <output-dir>
"""
import io
import json
import sys
import time
import traceback
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/data")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}
q = urllib.parse.quote

LOCATIESERVER = "https://api.pdok.nl/bzk/locatieserver/search/v3_1"
WIJKENBUURTEN = "https://service.pdok.nl/cbs/wijkenbuurten/2024/wfs/v1_0"
CBS = "https://opendata.cbs.nl/ODataApi/odata"
CBS_DERDEN = "https://dataderden.cbs.nl/ODataApi/odata"

AREAS = {
    "zamenhofstraat-leiden": {"straat": "Zamenhofstraat", "woonplaats": "Leiden", "gemeentecode": "GM0546"},
    "rijndijk-hazerswoude-rijndijk": {"straat": "Rijndijk", "woonplaats": "Hazerswoude-Rijndijk", "gemeentecode": "GM0484"},
}
GEMEENTEN = sorted({a["gemeentecode"] for a in AREAS.values()})
# CBS uses these values for "unknown" or "secret" in the wijken-en-buurten data.
MISSING = {-99997, -99997.0, -99995, -99995.0, -99999, -99999.0}

log_lines = []


def log(msg):
    print(msg, flush=True)
    log_lines.append(msg)


def fetch(url, binary=False, timeout=120, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            return body if binary else body.decode("utf-8")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def fetch_json(url):
    return json.loads(fetch(url))


def write(name, obj):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1 if not name.endswith(".geojson") else None))
    log(f"  wrote {name} ({path.stat().st_size // 1024} KB)")


def clean(props):
    return {k: (None if v in MISSING else v) for k, v in props.items()}


def step(fn):
    def run(*args):
        log(f"\n== {fn.__name__}")
        try:
            return fn(*args)
        except Exception as e:
            log(f"  FAILED: {e}")
            log(traceback.format_exc())
    return run


@step
def areas():
    """All addresses on each test street, and the buurten they fall in."""
    result = {}
    for key, a in AREAS.items():
        fq = f"type:adres AND straatnaam:\"{a['straat']}\" AND woonplaatsnaam:\"{a['woonplaats']}\""
        docs, start = [], 0
        while True:
            url = (f"{LOCATIESERVER}/free?q=*&fq={q(fq)}&rows=100&start={start}"
                   "&fl=id,weergavenaam,huisnummer,huisletter,huisnummertoevoeging,postcode,buurtcode,buurtnaam,"
                   "wijkcode,wijknaam,gemeentecode,gemeentenaam,centroide_ll,adresseerbaarobject_id,nummeraanduiding_id")
            resp = fetch_json(url)["response"]
            docs += resp["docs"]
            start += 100
            if start >= resp["numFound"]:
                break
        buurten = {}
        for d in docs:
            b = buurten.setdefault(d.get("buurtcode"), {"buurtcode": d.get("buurtcode"), "buurtnaam": d.get("buurtnaam"),
                                                        "wijkcode": d.get("wijkcode"), "wijknaam": d.get("wijknaam"),
                                                        "adressen": 0})
            b["adressen"] += 1
        result[key] = {**a, "gemeentenaam": docs[0]["gemeentenaam"] if docs else None, "adressen": docs,
                       "buurten": sorted(buurten.values(), key=lambda b: -b["adressen"])}
        log(f"  {key}: {len(docs)} addresses in {len(buurten)} buurten: "
            + ", ".join(f"{b['buurtnaam']} ({b['adressen']})" for b in result[key]["buurten"]))
    write("areas.json", result)
    return result


def wfs_features(type_name, gemeentecode=None, properties=None, srs="EPSG:4326"):
    """Page through a wijkenbuurten WFS layer, optionally filtered on one gemeente."""
    features, start, page = [], 0, 1000
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": f"wijkenbuurten:{type_name}",
              "outputFormat": "json", "srsName": srs, "count": str(page)}
    if gemeentecode:
        params["FILTER"] = ("<fes:Filter xmlns:fes=\"http://www.opengis.net/fes/2.0\"><fes:PropertyIsEqualTo>"
                            f"<fes:ValueReference>gemeentecode</fes:ValueReference><fes:Literal>{gemeentecode}</fes:Literal>"
                            "</fes:PropertyIsEqualTo></fes:Filter>")
    if properties:
        params["propertyName"] = ",".join(properties)
    while True:
        params["startIndex"] = str(start)
        data = fetch_json(f"{WIJKENBUURTEN}?{urllib.parse.urlencode(params)}")
        batch = data.get("features", [])
        features += batch
        if len(batch) < page:
            return features
        start += page


def round_coords(geom, nd=6):
    def r(c):
        return [round(c[0], nd), round(c[1], nd)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
    if geom:
        geom["coordinates"] = r(geom["coordinates"])
    return geom


@step
def buurten():
    """Buurt and wijk polygons with all CBS kerncijfers for the test gemeenten."""
    for layer in ("buurten", "wijken"):
        feats = []
        for gm in GEMEENTEN:
            got = wfs_features(layer, gm)
            log(f"  {layer} {gm}: {len(got)}")
            feats += got
        for f in feats:
            f["properties"] = clean(f["properties"])
            round_coords(f["geometry"])
        write(f"{layer}.geojson", {"type": "FeatureCollection", "features": feats})
    gm = []
    for code in GEMEENTEN:
        gm += wfs_features("gemeenten", code)
    write("gemeenten.json", [clean(f["properties"]) for f in gm])


# Indicators used for national comparison (percentiles). Direction: +1 higher is better, -1 lower is better.
NATIONAL_FIELDS = [
    "aantalInwoners", "gemiddeldeWoningwaarde", "percentageKoopwoningen", "percentageHuurwoningen",
    "gemiddeldInkomenPerInwoner", "percentageHuishoudensMetKinderen", "personenautosPerHuishouden",
    "opritHoofdverkeerswegGemiddeldeAfstandInKm", "treinstationGemiddeldeAfstandInKm",
    "overstapstationGemiddeldeAfstandInKm", "basisonderwijsGemiddeldeAfstandInKm",
    "voortgezetOnderwijsGemAfstandInKm", "huisartsenpraktijkGemiddeldeAfstandInKm",
    "groteSupermarktGemiddeldeAfstandInKm", "kinderdagverblijfGemiddeldeAfstandInKm",
    "afstandTotOpenbaarGroenTotaal", "afstandTotParkOfPlantsoen", "bevolkingsdichtheidInwonersPerKm2",
    "percentageBouwjaarklasseVanaf2000", "percentageWoningenMetZonnestroom",
]


@step
def national():
    """Selected indicators for every buurt in the Netherlands, to rank the test buurten nationally."""
    props = ["buurtcode", "buurtnaam", "gemeentenaam", "water"] + NATIONAL_FIELDS
    feats = wfs_features("buurten", properties=props)
    rows = [clean(f["properties"]) for f in feats if f["properties"].get("water") != "J"]
    log(f"  {len(rows)} land buurten nationally")
    write("national/buurten.json", rows)


def cbs_rows(base, table, path, flt=None, select=None):
    """Page through a CBS OData v3 endpoint (max 10 000 rows per call)."""
    url = f"{base}/{table}/{path}?$format=json"
    if flt:
        url += "&$filter=" + q(flt)
    if select:
        url += "&$select=" + ",".join(select)
    rows = []
    while url:
        data = fetch_json(url)
        rows += data["value"]
        url = data.get("odata.nextLink")
    return rows


@step
def crime():
    """Registered crimes per buurt (politie via CBS): yearly totals by type, and monthly for the focus buurten."""
    for table, kind in (("47018NED", "jaar"), ("47022NED", "maand")):
        props = fetch_json(f"{CBS_DERDEN}/{table}/DataProperties?$format=json")["value"]
        dims = {p["Key"]: p for p in props if p.get("Type") in ("Dimension", "TimeDimension", "GeoDimension")}
        log(f"  {table} dimensions: {list(dims)}; topics: {[p['Key'] for p in props if p.get('Type') == 'Topic']}")
        soort = cbs_rows(CBS_DERDEN, table, "SoortMisdrijf")
        perioden = cbs_rows(CBS_DERDEN, table, "Perioden")
        write(f"crime/{table}-soortmisdrijf.json", soort)
        geo_key = next(k for k, p in dims.items() if p["Type"] == "GeoDimension")
        wanted = [s["Key"] for s in soort if s["Key"].strip() in {"0.0.0", "1.1.1", "1.2.1", "1.2.3", "1.4.5", "1.4.6",
                                                                     "1.4.7", "2.2.1", "3.5.2", "1.1.2", "1.2.2", "1.2.4", "1.2.5", "3.6.4", "1.3.1", "2.5.1", "2.5.2"}]
        periods = [p["Key"] for p in perioden][-36:] if kind == "maand" else [p["Key"] for p in perioden][-5:]
        log(f"  {table}: {len(wanted)} crime types, periods {periods[0]}..{periods[-1]}")
        rows = []
        for gm in GEMEENTEN:
            prefix = "BU" + gm[2:]
            for p in periods:
                flt = f"substringof('{prefix}',{geo_key}) and Perioden eq '{p}'"
                if kind == "maand":
                    flt = f"substringof('{prefix}',{geo_key}) and Perioden eq '{p}' and SoortMisdrijf eq '{wanted[0]}'"
                    rows += cbs_rows(CBS_DERDEN, table, "TypedDataSet", flt)
                    continue
                for s in wanted:
                    rows += cbs_rows(CBS_DERDEN, table, "TypedDataSet", flt + f" and SoortMisdrijf eq '{s}'")
            # gemeente totals for comparison
            gm_flt = f"{geo_key} eq '{gm}'" + (f" and SoortMisdrijf eq '{wanted[0]}'" if kind == "maand" else "")
            rows += [r for r in cbs_rows(CBS_DERDEN, table, "TypedDataSet", gm_flt) if r["Perioden"] in periods]
        log(f"  {table}: {len(rows)} rows")
        write(f"crime/{table}.json", rows)


@step
def prices():
    """Average sale price of existing homes per gemeente (CBS/Kadaster), for trend and indexing asking prices."""
    table = "83625NED"
    props = fetch_json(f"{CBS}/{table}/DataProperties?$format=json")["value"]
    log(f"  {table} properties: {[(p['Key'], p.get('Type')) for p in props]}")
    regios = cbs_rows(CBS, table, "RegioS")
    codes = [r["Key"] for r in regios if r["Key"].strip() in GEMEENTEN or r["Key"].strip() == "NL01"]
    rows = []
    for c in codes:
        rows += cbs_rows(CBS, table, "TypedDataSet", f"RegioS eq '{c}'")
    log(f"  {len(rows)} rows for {codes}")
    write("prices/83625NED.json", rows)


@step
def probe_more():
    """Leftover discovery: DUO dataset catalogue, Leefbaarometer zip contents, RIVM buurt layer fields."""
    found = {}
    try:
        cat = fetch_json("https://onderwijsdata.duo.nl/api/3/action/package_search?q=vestigingen&rows=50")
        found["duo_packages"] = [(p["name"], p["title"], [r["url"] for r in p.get("resources", [])][:6]) for p in cat["result"]["results"]]
    except Exception as e:
        found["duo_packages"] = f"ERROR {e}"
    try:
        cat = fetch_json("https://onderwijsdata.duo.nl/api/3/action/package_search?q=doorstroomtoets&rows=20")
        found["duo_toets"] = [(p["name"], p["title"], [r["url"] for r in p.get("resources", [])][:6]) for p in cat["result"]["results"]]
    except Exception as e:
        found["duo_toets"] = f"ERROR {e}"
    for name, url in {"lbm_data": "https://www.leefbaarometer.nl/resources/open-data-leefbaarometer-meting-2024.zip",
                      "lbm_geo": "https://www.leefbaarometer.nl/resources/geometrie-lbm3-2024.zip"}.items():
        try:
            blob = fetch(url, binary=True, timeout=600)
            z = zipfile.ZipFile(io.BytesIO(blob))
            found[name] = {"bytes": len(blob), "files": [(i.filename, i.file_size) for i in z.infolist()]}
            for i in z.infolist():
                if i.filename.lower().endswith(".csv"):
                    head = z.open(i).read(3000).decode("utf-8", "replace").splitlines()[:4]
                    found[name][i.filename] = head
        except Exception as e:
            found[name] = f"ERROR {e}"
    for layer in ("rivm_20251201_geluidhinder_bu_weg_2024", "rivm_20251201_tevreden_woonomg_bu_2024"):
        try:
            data = fetch_json("https://data.rivm.nl/geo/alo/wfs?service=WFS&version=2.0.0&request=GetFeature"
                              f"&typeNames=alo:{layer}&count=1&outputFormat=application/json")
            p = data["features"][0]["properties"]
            found[layer] = p
        except Exception as e:
            found[layer] = f"ERROR {e}"
    write("probe_more.json", found)


if __name__ == "__main__":
    t0 = time.time()
    areas()
    buurten()
    national()
    crime()
    prices()
    probe_more()
    log(f"\ndone in {time.time() - t0:.0f}s")
    (OUT / "build.log").write_text("\n".join(log_lines))
