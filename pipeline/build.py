"""Prospect data pipeline.

Fetches official open data for the test areas and writes static JSON/GeoJSON for the app.
Each step is independent: a failing source is logged and the others still run.

Usage: python pipeline/build.py <output-dir>
"""
import io
import json
import re
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


def fetch(url, binary=False, timeout=120, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            return body if binary else body.decode("utf-8")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


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
    import functools

    @functools.wraps(fn)
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
    "percentageBouwjaarklasseVanaf2000", "percentageWoningenMetZonnestroom", "wijkcode", "gemeentecode",
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


CRIME_TYPES = {"0.0.0", "1.1.1", "1.1.2", "1.2.1", "1.2.3", "1.2.4", "1.4.3", "1.4.4", "1.4.5", "1.4.6",
               "1.4.7", "2.1.1", "2.2.1", "2.4.2", "3.1.1", "3.6.4"}


@step
def crime():
    """Registered crimes per buurt (politie via CBS): yearly by type for all buurten in the test gemeenten,
    monthly totals for the last 36 months."""
    for table, kind in (("47018NED", "jaar"), ("47022NED", "maand")):
        props = fetch_json(f"{CBS_DERDEN}/{table}/DataProperties?$format=json")["value"]
        log(f"  {table} properties: {[(p['Key'], p.get('Type')) for p in props]}")
        geo_key = next(p["Key"] for p in props if p["Key"] not in ("SoortMisdrijf", "Perioden")
                       and ("Dimension" in (p.get("Type") or "") or p["Key"] in ("WijkenEnBuurten", "RegioS")))
        perioden = [p["Key"] for p in cbs_rows(CBS_DERDEN, table, "Perioden")]
        periods = perioden[-36:] if kind == "maand" else perioden[-5:]
        log(f"  {table}: geo key {geo_key}, periods {periods[0]}..{periods[-1]}")
        rows = []
        for gm in GEMEENTEN:
            for p in periods:
                flt = f"(substringof('BU{gm[2:]}',{geo_key}) or substringof('{gm}',{geo_key})) and Perioden eq '{p}'"
                if kind == "maand":
                    flt += " and SoortMisdrijf eq '0.0.0 '"
                got = cbs_rows(CBS_DERDEN, table, "TypedDataSet", flt)
                rows += [r for r in got if r["SoortMisdrijf"].strip() in CRIME_TYPES]
        for r in rows:
            r["regio"] = r.pop(geo_key).strip()
            r["SoortMisdrijf"] = r["SoortMisdrijf"].strip()
        log(f"  {table}: {len(rows)} rows")
        write(f"crime/{table}.json", rows)
        if kind == "jaar":
            # National totals per buurt for ranking; split by gemeente-code range to stay under 10 000 rows a call.
            last_full = periods[-1]
            nat = []
            for prefix in [f"BU{i:02d}" for i in range(20)]:
                nat += cbs_rows(CBS_DERDEN, table, "TypedDataSet",
                                f"substringof('{prefix}',{geo_key}) and Perioden eq '{last_full}' and SoortMisdrijf eq '0.0.0 '")
            log(f"  national {last_full}: {len(nat)} buurten")
            write("national/crime_totals.json", {"periode": last_full, "rows": {r[geo_key].strip(): r.get("GeregistreerdeMisdrijven_1") for r in nat}})


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
    # every gemeente, last 12 years, for the national map and the value estimate anywhere
    perioden = [p["Key"] for p in cbs_rows(CBS, table, "Perioden")][-12:]
    nat = []
    for per in perioden:
        nat += cbs_rows(CBS, table, "TypedDataSet", f"substringof('GM',RegioS) and Perioden eq '{per}'")
    log(f"  national: {len(nat)} gemeente-year rows {perioden[0]}..{perioden[-1]}")
    write("national/prices_gemeenten.json", [{"gm": r["RegioS"].strip(), "jaar": r["Perioden"][:4], "prijs": r["GemiddeldeVerkoopprijs_1"]} for r in nat])


@step
def national_geo():
    """Boundaries of every buurt in the Netherlands, simplified for a web map (about 20 m tolerance)."""
    from shapely.geometry import mapping, shape
    feats = wfs_features("buurten")  # all attributes: the geometry field name is not documented
    out = []
    for f in feats:
        p = f["properties"]
        if p.get("water") == "J" or not f.get("geometry"):
            continue
        g = shape(f["geometry"]).simplify(0.0002, preserve_topology=True)
        if g.is_empty:
            continue
        geom = mapping(g)
        def r(c):
            return [round(c[0], 5), round(c[1], 5)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
        out.append({"type": "Feature", "properties": {k: p.get(k) for k in ("buurtcode", "buurtnaam", "gemeentecode", "gemeentenaam")},
                    "geometry": {"type": geom["type"], "coordinates": r(geom["coordinates"])}})
    log(f"  {len(out)} land buurten with simplified geometry")
    write("national/buurten-geo.geojson", {"type": "FeatureCollection", "features": out})


def duo_package(query):
    return fetch_json(f"https://onderwijsdata.duo.nl/api/3/action/package_search?q={q(query)}&rows=50")["result"]["results"]


def duo_csv(url):
    text = fetch(url).lstrip("﻿")
    delim = ";" if text[:2000].count(";") > text[:2000].count(",") else ","
    import csv as _csv
    return list(_csv.DictReader(io.StringIO(text), delimiter=delim))


GEMEENTE_NAMEN = {"GM0546": "LEIDEN", "GM0484": "ALPHEN AAN DEN RIJN"}


def geocode(postcode, huisnummer):
    fq = f"type:adres AND postcode:{postcode.replace(' ', '')} AND huisnummer:{huisnummer}"
    docs = fetch_json(f"{LOCATIESERVER}/free?q=*&fq={q(fq)}&rows=1&fl=centroide_ll,buurtcode,buurtnaam")["response"]["docs"]
    if not docs:
        return None
    lon, lat = docs[0]["centroide_ll"][6:-1].split()
    return {"lon": float(lon), "lat": float(lat), "buurtcode": docs[0].get("buurtcode"), "buurtnaam": docs[0].get("buurtnaam")}


@step
def schools():
    """Primary and secondary school locations (DUO) with end-of-primary test results and school advice."""
    catalog = {}
    for query in ("adressen_bo", "adressen_vo", "wpo-eindscores", "wpoadvies-v1", "wporeferentieniveau-v1",
                  "examen", "slaagpercentage", "inspectie", "schooladvies"):
        for pkg in duo_package(query):
            catalog[pkg["name"]] = {"title": pkg["title"], "resources": [(r.get("name"), r["url"]) for r in pkg.get("resources", [])]}
    write("schools/duo_catalog.json", catalog)

    def resource(pkg, needle):
        return next(u for n, u in catalog[pkg]["resources"] if needle in u)

    names = set(GEMEENTE_NAMEN.values())
    vest = []
    for pkg, needle, sector in (("adressen_bo", "vestigingenbo", "po"), ("adressen_vo", "vestigingenvo", "vo")):
        rows = duo_csv(resource(pkg, needle))
        log(f"  {pkg}: {len(rows)} rows, columns {list(rows[0])[:30]}")
        gem_col = next(c for c in rows[0] if "GEMEENTENAAM" in c.upper() and "CORRESP" not in c.upper())
        for r in rows:
            if r[gem_col].strip().upper() in names:
                r["_sector"] = sector
                vest.append(r)
    log(f"  {len(vest)} school locations in test gemeenten")
    for v in vest:
        pc = next((v[c] for c in v if c.upper() in ("POSTCODE", "POSTCODE VESTIGING")), "")
        hn = next((v[c] for c in v if c.upper() in ("HUISNUMMER-TOEVOEGING", "HUISNUMMER", "HUISNUMMER VESTIGING")), "")
        hn = "".join(ch for ch in hn if ch.isdigit())
        try:
            v["_geo"] = geocode(pc, hn) if pc and hn else None
        except Exception as e:
            v["_geo"] = None
    write("schools/vestigingen.json", vest)

    # Results files carry no gemeente; join them on school code (INSTELLINGSCODE + VESTIGINGSCODE).
    local_codes = {(v["INSTELLINGSCODE"].strip(), v["VESTIGINGSCODE"].strip()) for v in vest}
    for pkg, needle in (("wpo-eindscores", "eindscores"), ("wpoadvies-v1", "advies"), ("wporeferentieniveau-v1", "referentieniveau")):
        rows = duo_csv(resource(pkg, needle))
        keep = [r for r in rows if (r["INSTELLINGSCODE"].strip(), r["VESTIGINGSCODE"].strip()) in local_codes
                or (r["INSTELLINGSCODE"].strip(), r["INSTELLINGSCODE"].strip() + r["VESTIGINGSCODE"].strip()[-2:]) in local_codes]
        log(f"  {pkg}: {len(rows)} rows nationally, {len(keep)} for local schools; sample codes {[(r['INSTELLINGSCODE'], r['VESTIGINGSCODE']) for r in rows[:2]]}")
        write(f"schools/{pkg}.json", keep)
        if pkg == "wpo-eindscores":
            # national distribution of the latest year (per test type), for ranking local schools
            last = max(r["PEILJAAR"] for r in rows)
            write("national/eindscores.json", [{k: r[k] for k in ("PEILJAAR", "DOORSTROOMTOETS_PO", "GEMIDDELDE_UITSLAG", "AANTAL_LEERLINGEN")}
                                               for r in rows if r["PEILJAAR"] == last])


@step
def schools_extra():
    """Inspectie verdicts and secondary-school exam results (DUO), plus the dataset descriptions that
    explain the codes used in the results files."""
    vest = json.loads((OUT / "schools/vestigingen.json").read_text())
    instellingen = {v["INSTELLINGSCODE"].strip() for v in vest}
    notes = {}
    for pkg in ("wpoadvies-v1", "wpo-eindscores", "wporeferentieniveau-v1", "oordelen-v02", "04_voex-v1", "03_voex-v1"):
        try:
            meta = fetch_json(f"https://onderwijsdata.duo.nl/api/3/action/package_show?id={pkg}")["result"]
            notes[pkg] = {"title": meta.get("title"), "notes": meta.get("notes"),
                          "resources": [{"name": r.get("name"), "url": r["url"], "description": r.get("description")} for r in meta.get("resources", [])]}
        except Exception as e:
            notes[pkg] = f"ERROR {e}"
    write("schools/duo_notes.json", notes)
    for pkg in ("oordelen-v02", "04_voex-v1"):
        if not isinstance(notes.get(pkg), dict):
            continue
        for res in notes[pkg]["resources"]:
            if not res["url"].lower().endswith(".csv"):
                continue
            try:
                rows = duo_csv(res["url"])
            except Exception as e:
                log(f"  {pkg}: could not download {res['url']}: {e}")
                continue
            code_col = next((c for c in rows[0] if c.upper() in ("INSTELLINGSCODE", "BRIN NUMMER", "BRINNUMMER", "BRIN")), None)
            keep = [r for r in rows if code_col and r[code_col].strip() in instellingen]
            name = res["url"].rsplit("/", 1)[-1].replace(".csv", "")
            log(f"  {pkg}/{name}: {len(rows)} rows, {len(keep)} local; columns {list(rows[0])}")
            write(f"schools/{pkg}-{name}.json", keep)


BAG = "https://api.pdok.nl/kadaster/bag/ogc/v2/collections"


@step
def bag():
    """BAG building data for every address on the test streets: use, floor area, building year, status."""
    areas_data = json.loads((OUT / "areas.json").read_text())
    panden = {}
    result = {}
    for key, a in areas_data.items():
        rows = []
        for d in a["adressen"]:
            vbo_id = d.get("adresseerbaarobject_id")
            item = {"adres": d["weergavenaam"], "postcode": d.get("postcode"), "huisnummer": d.get("huisnummer"),
                    "buurtcode": d.get("buurtcode"), "vbo": vbo_id}
            try:
                vbo = fetch_json(f"{BAG}/verblijfsobject/items?identificatie={vbo_id}&f=json")["features"]
                if vbo:
                    p = vbo[0]["properties"]
                    item.update({k: p.get(k) for k in ("gebruiksdoel", "oppervlakte", "status")})
                    # the verblijfsobject links its building(s) by URL
                    href = p.get("pand.href")
                    href = href[0] if isinstance(href, list) else href
                    pid = href.rstrip("/").split("/")[-1].split("?")[0] if href else None
                    if pid and pid not in panden:
                        url = href + ("&" if "?" in href else "?") + "f=json"
                        pand = fetch_json(url)
                        panden[pid] = pand.get("properties", pand)
                        if len(panden) == 1:
                            log(f"  pand fields: {sorted(panden[pid])}")
                    if pid:
                        pp = panden.get(pid, {})
                        item["pand"] = pid
                        item["bouwjaar"] = pp.get("bouwjaar") or pp.get("oorspronkelijk_bouwjaar") or pp.get("oorspronkelijkBouwjaar")
                        item["pand_status"] = pp.get("status")
            except Exception as e:
                item["error"] = str(e)
            rows.append(item)
        result[key] = rows
        years = [r["bouwjaar"] for r in rows if r.get("bouwjaar")]
        log(f"  {key}: {len(rows)} addresses, {len(years)} with building year, range {min(years) if years else '-'}..{max(years) if years else '-'}")
    write("bag/adressen.json", result)


@step
def leefbaarometer():
    """Leefbaarometer 3.0 (BZK) scores per buurt 2002-2024, for the test gemeenten and nationally (latest year)."""
    blob = fetch(LBM_DATA, binary=True, timeout=900)
    z = zipfile.ZipFile(io.BytesIO(blob))
    files = [i.filename for i in z.infolist() if not i.filename.startswith("__MACOSX")]
    log(f"  files: {files}")
    prefixes = tuple(f"BU{gm[2:]}" for gm in GEMEENTEN)
    for f in files:
        if not f.lower().endswith(".csv") or "grid" in f.lower():
            continue
        with z.open(f) as fh:
            lines = io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace")
            header = lines.readline()
            delim = ";" if header.count(";") > header.count(",") else ","
            cols = [c.strip().strip('"') for c in header.strip().split(delim)]
            log(f"  {f.split('/')[-1]}: columns {cols}")
            import csv as _csv
            rows = list(_csv.DictReader(lines, fieldnames=cols, delimiter=delim))
        key = f.split("/")[-1].replace(".csv", "").replace(" ", "_")
        code_col = next((c for c in cols if c.lower() in ("bu_code", "buurtcode", "wk_code", "gm_code", "gemeentecode", "wijkcode", "pc4", "code")), cols[0])
        local = [r for r in rows if r[code_col].startswith(prefixes) or r[code_col].replace("GM", "") in [g[2:] for g in GEMEENTEN]
                 or r[code_col] in GEMEENTEN or r[code_col].startswith(tuple(f"WK{gm[2:]}" for gm in GEMEENTEN))]
        log(f"  {key}: {len(rows)} rows, {len(local)} local (code column {code_col})")
        write(f"leefbaarometer/{key}.json", local)
        if "scores_buurten" in key:
            jaar_col = next((c for c in cols if c.lower() in ("jaar", "year")), None)
            last = max(r[jaar_col] for r in rows) if jaar_col else None
            write("national/leefbaarometer_buurten.json",
                  [r for r in rows if not jaar_col or r[jaar_col] == last])


LBM_DATA = "https://www.leefbaarometer.nl/resources/open-data-leefbaarometer-meting-2024.zip"
RIVM_WFS = "https://data.rivm.nl/geo/alo/wfs"


@step
def rivm():
    """RIVM Gezondheidsmonitor 2024 per buurt: noise nuisance, sleep disturbance, satisfaction, heat."""
    rows, start = [], 0
    while True:  # pages of 2000; one request for all buurten gets 503s
        params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "count": "2000", "startIndex": str(start),
                  "typeNames": "alo:rivm_20251201_geluidhinder_bu_weg_2024", "outputFormat": "application/json",
                  "propertyName": "buurtcode,buurtnaam,wijkcode,gemeenteco,gemeentena,jaar,b_gel_weg,b_gel_lt50,b_gel_gt50,"
                                  "b_gel_trei,b_gel_vv,b_gel_wtn,b_gel_bu,b_sv_weg,b_sv_trein,b_sv_vv,b_sv_bu,b_tevr_won,"
                                  "b_tevr_wo,b_tevr_gr,b_verk_bi,b_verk_bu,b_vold_br,b_nverk_bi,b_nverk_bu"}
        batch = [f["properties"] for f in fetch_json(f"{RIVM_WFS}?{urllib.parse.urlencode(params)}")["features"]]
        rows += batch
        if len(batch) < 2000:
            break
        start += 2000
    log(f"  {len(rows)} buurten nationally; fields {list(rows[0])}")
    write("national/rivm_buurten.json", rows)
    write("rivm/buurten.json", [r for r in rows if r.get("gemeenteco") in GEMEENTEN])


@step
def soil():
    """BRO Bodemkaart (soil type) at addresses along each test street."""
    areas_data = json.loads((OUT / "areas.json").read_text())
    result = {}
    for key, a in areas_data.items():
        pts = a["adressen"][:: max(1, len(a["adressen"]) // 8)][:8]
        samples = []
        for d in pts:
            lon, lat = map(float, d["centroide_ll"][6:-1].split())
            d_ = 0.0005
            params = {"service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "layers": "soilarea",
                      "query_layers": "soilarea", "crs": "EPSG:4326", "bbox": f"{lat-d_},{lon-d_},{lat+d_},{lon+d_}",
                      "width": "101", "height": "101", "i": "50", "j": "50", "info_format": "application/json"}
            try:
                info = fetch_json("https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0?" + urllib.parse.urlencode(params))
                props = [f["properties"] for f in info.get("features", [])]
            except Exception as e:
                props = f"ERROR {e}"
            samples.append({"adres": d["weergavenaam"], "lon": lon, "lat": lat, "bodemkaart": props})
        result[key] = samples
        log(f"  {key}: {len(samples)} samples; first: {json.dumps(samples[0]['bodemkaart'], ensure_ascii=False)[:400]}")
    write("soil/bodemkaart.json", result)


@step
def publications():
    """Official publications (Gemeenteblad, Staatscourant) mentioning each test street: permits, traffic decisions."""
    import xml.etree.ElementTree as ET
    result = {}
    for key, a in AREAS.items():
        creator = {"GM0546": "Leiden", "GM0484": "Alphen aan den Rijn"}[a["gemeentecode"]]
        query = f'cql.textAndIndexes="{a["straat"]}" and dt.creator="{creator}"'
        records, start = [], 1
        while start <= 2000:
            xml = fetch(f"https://repository.overheid.nl/sru?query={q(query)}&maximumRecords=50&startRecord={start}")
            root = ET.fromstring(xml)
            recs = [el for el in root.iter() if el.tag.endswith("}recordData")]
            for rd in recs:
                item = {}
                for el in rd.iter():
                    tag = el.tag.split("}")[-1]
                    if el.text and el.text.strip() and tag not in item:
                        item[tag] = el.text.strip()
                    for k, v in el.attrib.items():
                        if k.endswith("scheme") or k.endswith("resourceIdentifier"):
                            item.setdefault(f"{tag}@", v)
                records.append(item)
            total = next((int(el.text) for el in root.iter() if el.tag.endswith("numberOfRecords")), 0)
            start += 50
            if start > total:
                break
        # "Rijndijk" also matches the village name Hazerswoude-Rijndijk; keep records about the street itself.
        street = a["straat"].lower()
        # the village name in any spelling: "Hazerswoude-Rijndijk", "Hazerswoude Rijndijk", "Hazerswoude- Rijndijk"
        village = re.compile(r"[\s-]*".join(map(re.escape, re.split(r"[\s-]+", a["woonplaats"].lower()))))

        def about_street(r):
            if (r.get("straatnaam") or "").lower() == street:
                return True
            text = village.sub(" ", f"{r.get('title', '')} {r.get('abstract', '')}".lower())
            return street in text
        kept = sorted((r for r in records if about_street(r)), key=lambda r: r.get("available") or r.get("date") or "", reverse=True)
        log(f"  {key}: {len(records)} of {total} fetched, {len(kept)} about the street")
        result[key] = {"query": query, "total": total, "records": kept}
    write("publications/bekendmakingen.json", result)


@step
def council():
    """Leiden council documents (Open Raadsinformatie) mentioning the street or buurt.
    Alphen aan den Rijn is not in Open Raadsinformatie; see notes."""
    result = {}
    for key, terms in {"zamenhofstraat-leiden": ["Zamenhofstraat", "Noordvest"]}.items():
        hits = []
        for term in terms:
            body = json.dumps({"query": {"simple_query_string": {"query": f'"{term}"', "fields": ["text", "name", "description"]}},
                               "size": 50, "sort": [{"start_date": {"order": "desc", "unmapped_type": "date"}}],
                               "_source": {"excludes": ["text"]},
                               "highlight": {"fields": {"text": {"fragment_size": 300, "number_of_fragments": 3}}}}).encode()
            req = urllib.request.Request("https://api.openraadsinformatie.nl/v1/elastic/ori_leiden_*/_search",
                                         data=body, headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            for h in data["hits"]["hits"]:
                src = h["_source"]
                hits.append({"term": term, "id": h["_id"], "type": src.get("@type"), "name": src.get("name"),
                             "date": src.get("start_date") or src.get("last_discussed_at") or src.get("date_modified"),
                             "url": src.get("original_url") or src.get("url"), "highlight": h.get("highlight", {}).get("text", [])})
            log(f"  {key} '{term}': {data['hits']['total']['value']} hits")
        result[key] = hits
    write("council/ori_leiden.json", result)


STEPS = [areas, buurten, national, national_geo, crime, prices, schools, schools_extra, bag, leefbaarometer, rivm, soil,
         publications, council]

if __name__ == "__main__":
    import os
    only = [x for x in os.environ.get("STEPS", "").split(",") if x]
    t0 = time.time()
    for fn in STEPS:
        if not only or fn.__name__ in only:
            fn()
    log(f"\ndone in {time.time() - t0:.0f}s")
    (OUT / "build.log").write_text("\n".join(log_lines))
