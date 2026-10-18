"""Building- and street-level data for a whole village (woonplaats).

For every address and building in the woonplaats:
  - address, buurt and coordinates (PDOK Locatieserver);
  - building footprint, building year, status, floor area and use per address (BAG, PDOK OGC API);
  - soil type at the building (BRO Bodemkaart) and a foundation-risk flag (building year + soil);
  - rijksmonument status (RCE).
Per street: building summary, official publications (Gemeenteblad) and council documents (iBabs).

Usage: python pipeline/village.py <output-dir>
Writes <output-dir>/<village>/{addresses.json, buildings.geojson, streets.json, summary.json}.
"""
import html
import http.cookiejar
import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/village")
UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}
q = urllib.parse.quote

VILLAGES = {
    "hazerswoude-rijndijk": {
        "woonplaats": "Hazerswoude-Rijndijk", "gemeente": "Alphen aan den Rijn", "gemeentecode": "GM0484",
        "postcode4": ["2394"], "ibabs": "https://alphenaandenrijn.bestuurlijkeinformatie.nl",
    },
    # Part of a city: the wijken Binnenstad-Zuid, Binnenstad-Noord and Stationsdistrict.
    "leiden-centrum": {
        "naam": "Leiden city centre", "woonplaats": "Leiden", "gemeente": "Leiden", "gemeentecode": "GM0546",
        "buurtcodes": ["BU05460000", "BU05460001", "BU05460002", "BU05460003", "BU05460100", "BU05460101", "BU05460102",
                       "BU05460103", "BU05460104", "BU05460105", "BU05460106", "BU05460107", "BU05460108", "BU05460109",
                       "BU05460200"],
        "postcode4": ["2311", "2312", "2313", "2316"], "ori": "leiden",
    },
}
LOCATIESERVER = "https://api.pdok.nl/bzk/locatieserver/search/v3_1"
BAG = "https://api.pdok.nl/kadaster/bag/ogc/v2/collections"
BODEMKAART = "https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0"
RCE_WFS = "https://service.pdok.nl/rce/ps-ch/wfs/v1_0"

log_lines = []


def log(msg):
    print(msg, flush=True)
    log_lines.append(str(msg))


def fetch(url, data=None, headers=None, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(4 * (attempt + 1))


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))


def ogc_items(collection, bbox):
    """All features of a BAG collection in a bbox, following the OGC API 'next' links."""
    url = f"{BAG}/{collection}/items?f=json&limit=1000&bbox={','.join(map(str, bbox))}"
    feats = []
    while url:
        page = fetch_json(url)
        feats += page.get("features", [])
        url = next((l["href"] for l in page.get("links", []) if l.get("rel") == "next"), None)
    return feats


# ---------------------------------------------------------------- steps

def addresses(v):
    # A whole woonplaats, or (for part of a city) one query per buurt, which keeps each result set small.
    filters = ([f"type:adres AND buurtcode:{b}" for b in v["buurtcodes"]] if v.get("buurtcodes")
               else [f'type:adres AND woonplaatsnaam:"{v["woonplaats"]}"'])
    docs = []
    for fq in filters:
        start = 0
        while True:
            resp = fetch_json(f"{LOCATIESERVER}/free?q=*&fq={q(fq)}&rows=100&start={start}"
                              "&fl=weergavenaam,straatnaam,huisnummer,huisletter,huisnummertoevoeging,postcode,buurtcode,buurtnaam,"
                              "wijkcode,centroide_ll,adresseerbaarobject_id,nummeraanduiding_id")["response"]
            docs += resp["docs"]
            start += 100
            if start >= resp["numFound"]:
                break
    for d in docs:
        lon, lat = map(float, d.pop("centroide_ll")[6:-1].split())
        d["lon"], d["lat"] = round(lon, 6), round(lat, 6)
    log(f"  {len(docs)} addresses in {len({d['straatnaam'] for d in docs})} streets")
    return docs


def bag(docs):
    lons, lats = [d["lon"] for d in docs], [d["lat"] for d in docs]
    bbox = [min(lons) - 0.002, min(lats) - 0.002, max(lons) + 0.002, max(lats) + 0.002]
    vbos = {f["properties"]["identificatie"]: f["properties"] for f in ogc_items("verblijfsobject", bbox)}
    panden, by_feature_id = {}, {}
    for f in ogc_items("pand", bbox):
        panden[f["properties"]["identificatie"]] = f
        by_feature_id[str(f.get("id"))] = f["properties"]["identificatie"]
    log(f"  BAG in bbox: {len(vbos)} verblijfsobjecten, {len(panden)} panden")
    for d in docs:
        v = vbos.get(d.get("adresseerbaarobject_id"))
        if not v:
            continue
        d["oppervlakte"] = v.get("oppervlakte")
        d["gebruiksdoel"] = ",".join(v.get("gebruiksdoel")) if isinstance(v.get("gebruiksdoel"), list) else v.get("gebruiksdoel")
        href = v.get("pand.href")
        href = href[0] if isinstance(href, list) else href
        # ".../collections/pand/items/<feature id>" or "...?identificatie=<BAG id>"
        m = re.search(r"identificatie=(\d+)|/items/([^/?]+)", href or "")
        pid = (m.group(1) or by_feature_id.get(m.group(2))) if m else None
        if pid in panden:
            d["pand"] = pid
            d["bouwjaar"] = panden[pid]["properties"].get("bouwjaar")
    return panden, bbox


def soil(points):
    """Soil type for each (key, lon, lat); points within ~50 m share one lookup."""
    cells = {}
    for key, lon, lat in points:
        cells.setdefault((round(lon / 0.0007), round(lat / 0.0004)), []).append((key, lon, lat))

    def lookup(item):
        _, pts = item
        _, lon, lat = pts[0]
        d = 0.0005
        params = {"service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "layers": "soilarea", "query_layers": "soilarea",
                  "crs": "EPSG:4326", "bbox": f"{lat - d},{lon - d},{lat + d},{lon + d}", "width": "101", "height": "101",
                  "i": "50", "j": "50", "info_format": "application/json"}
        try:
            feats = fetch_json(f"{BODEMKAART}?{urllib.parse.urlencode(params)}").get("features", [])
            name = feats[0]["properties"].get("first_soilname") if feats else None
        except Exception:
            name = None
        return [(k, name) for k, _, _ in pts]

    with ThreadPoolExecutor(8) as pool:
        result = {k: n for group in pool.map(lookup, cells.items()) for k, n in group}
    log(f"  soil: {len(cells)} lookups for {len(points)} buildings, {sum(1 for n in result.values() if n)} mapped")
    return result


def monuments(bbox):
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": "ps-ch:rce_inspire_points",
              "outputFormat": "application/json", "srsName": "EPSG:4326", "bbox": f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]},urn:ogc:def:crs:EPSG::4326"}
    try:
        feats = fetch_json(f"{RCE_WFS}?{urllib.parse.urlencode(params)}").get("features", [])
    except Exception as e:
        log(f"  monuments failed: {e}")
        return []
    if feats:
        log(f"  {len(feats)} rijksmonument points; fields {sorted(feats[0]['properties'])}")
    return feats


def soft_soil(name):
    return bool(name) and any(w in name.lower() for w in ("veen", "klei", "zavel"))


def foundation_flag(year, soil_name):
    if not year:
        return None
    year = int(year)
    if year < 1925:
        return "high"  # wooden piles or shallow foundations are likely
    if year < 1970:
        return "check" if soft_soil(soil_name) or soil_name is None else "low"
    return "low"


def publications(v, streets):
    """Gemeenteblad publications per street, kept when they are about this village."""
    village = re.compile(r"[\s-]*".join(map(re.escape, re.split(r"[\s-]+", v["woonplaats"].lower()))))
    out = {}

    def one(street):
        query = f'cql.textAndIndexes="{street}" and dt.creator="{v["gemeente"]}"'
        try:
            root = ET.fromstring(fetch(f"https://repository.overheid.nl/sru?query={q(query)}&maximumRecords=100"))
        except Exception:
            return street, []
        recs = []
        for rd in (el for el in root.iter() if el.tag.endswith("}recordData")):
            item = {}
            for el in rd.iter():
                tag = el.tag.split("}")[-1]
                if el.text and el.text.strip() and tag not in item:
                    item[tag] = el.text.strip()
            text = f"{item.get('title', '')} {item.get('abstract', '')}".lower()
            pc = (item.get("postcodeHuisnummer") or "")[:4]
            if street.lower() in text and (village.search(text) or pc in v["postcode4"]):
                recs.append({k: item.get(k) for k in ("title", "available", "type", "preferredUrl")})
        recs.sort(key=lambda r: r.get("available") or "", reverse=True)
        return street, recs

    with ThreadPoolExecutor(4) as pool:
        for street, recs in pool.map(one, streets):
            out[street] = recs
    log(f"  publications: {sum(len(r) for r in out.values())} across {sum(1 for r in out.values() if r)} streets")
    return out


def council_documents(v, streets):
    return council_ori(v, streets) if v.get("ori") else council_ibabs(v, streets)


def council_ori(v, streets):
    """Open Raadsinformatie: the most recent council documents that mention the street."""
    def one(street):
        body = json.dumps({"query": {"simple_query_string": {"query": f'"{street}"', "fields": ["text", "name", "description"]}},
                           "size": 15, "sort": [{"last_discussed_at": {"order": "desc", "unmapped_type": "date"}}],
                           "_source": {"excludes": ["text", "md_text"]}}).encode()
        try:
            data = json.loads(fetch(f"https://api.openraadsinformatie.nl/v1/elastic/ori_{v['ori']}_*/_search", data=body,
                                    headers={"Content-Type": "application/json"}))
        except Exception as e:
            return street, [], str(e)
        hits = []
        for h in data["hits"]["hits"]:
            src = h["_source"]
            url = src.get("original_url") or src.get("url")
            if url and src.get("name"):
                hits.append({"url": url, "title": src["name"], "date": (src.get("last_discussed_at") or src.get("start_date") or "")[:10]})
        return street, hits, data["hits"]["total"]["value"]

    out, errors = {}, []
    with ThreadPoolExecutor(4) as pool:
        for street, hits, total in pool.map(one, streets):
            out[street] = hits
            if isinstance(total, str):
                errors.append(total)
    log(f"  council documents (ORI): {sum(len(r) for r in out.values())} across {sum(1 for r in out.values() if r)} streets"
        + (f"; {len(errors)} errors, first: {errors[0]}" if errors else ""))
    return out


def council_ibabs(v, streets):
    """iBabs search per street; keeps results whose title names the street."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = list(UA.items())
    base = v["ibabs"]
    out = {}
    try:
        page = opener.open(f"{base}/Search", timeout=60).read().decode("utf-8", "replace")
        token = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', page).group(1)
    except Exception as e:
        log(f"  council search unavailable: {e}")
        return out
    for street in streets:
        found = []
        for pageno in (1, 2):
            data = urllib.parse.urlencode({"Page": pageno, "Query": f'"{street}"', "OrderBy": "0", "__RequestVerificationToken": token}).encode()
            req = urllib.request.Request(f"{base}/Search/Results", data=data,
                                         headers={"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"})
            try:
                body = opener.open(req, timeout=60).read().decode("utf-8", "replace")
            except Exception:
                break
            hits = [(base + html.unescape(m.group(1)), re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip())
                    for m in re.finditer(r'<a[^>]+href="(/(?:Document/View|Agenda/Index|Reports/Item)[^"]+)"[^>]*>(.*?)</a>', body, re.S)]
            found += [{"url": u, "title": t} for u, t in hits if street.lower() in t.lower()]
            if len(hits) < 10:
                break
        seen = set()
        out[street] = [f for f in found if not (f["url"] in seen or seen.add(f["url"]))]
        time.sleep(0.3)
    log(f"  council documents: {sum(len(r) for r in out.values())} across {sum(1 for r in out.values() if r)} streets")
    return out


def build(key, v):
    from shapely.geometry import Point, mapping, shape
    out = OUT / key
    out.mkdir(parents=True, exist_ok=True)
    docs = addresses(v)
    panden, bbox = bag(docs)

    # buildings that hold at least one address in the village
    by_pand = {}
    for d in docs:
        if d.get("pand"):
            by_pand.setdefault(d["pand"], []).append(d)
    shapes = {}
    for pid in by_pand:
        g = panden[pid].get("geometry")
        if g:
            shapes[pid] = shape(g)
    soils = soil([(pid, s.centroid.x, s.centroid.y) for pid, s in shapes.items()])
    mons = monuments(bbox)
    # The building that contains the monument point, else the nearest one within ~15 m.
    from shapely import STRtree
    pids = list(shapes)
    tree = STRtree([shapes[p] for p in pids])
    mon_by_pand = {}
    for m in mons:
        g = m.get("geometry") or {}
        if g.get("type") not in ("Point", "MultiPoint"):
            continue
        pt = shape(g).representative_point()
        hit = tree.query(pt, predicate="intersects")
        if not len(hit):
            idx, dist = tree.query_nearest(pt, max_distance=0.00015, return_distance=True)
            hit = idx[:1]
        if len(hit):
            mon_by_pand.setdefault(pids[int(hit[0])], {k: m["properties"].get(k) for k in m["properties"] if k.lower() in (
                "monumentnummer", "rijksmonumentnummer", "rijksmonument_nummer", "localid", "text", "naam", "omschrijving",
                "hoofdcategorie", "url", "rijksmonumenturl")})
    log(f"  {len(mon_by_pand)} buildings are rijksmonumenten")

    features = []
    for pid, addrs in by_pand.items():
        s = shapes.get(pid)
        if s is None:
            continue
        year = panden[pid]["properties"].get("bouwjaar")
        uses = sorted({u for a in addrs for u in (a.get("gebruiksdoel") or "").split(",") if u})
        props = {
            "pand": pid, "bouwjaar": year, "status": panden[pid]["properties"].get("status"),
            "adressen": len(addrs), "straat": addrs[0]["straatnaam"], "buurtcode": addrs[0].get("buurtcode"),
            "huisnummers": ", ".join(sorted({str(a.get("huisnummer")) + (a.get("huisletter") or "") + (("-" + a["huisnummertoevoeging"]) if a.get("huisnummertoevoeging") else "") for a in addrs}, key=lambda x: (len(x), x)))[:120],
            "oppervlakte": sum(a.get("oppervlakte") or 0 for a in addrs) or None, "gebruik": ",".join(uses),
            "wonen": any("woon" in u for u in uses), "bodem": soils.get(pid), "fundering": foundation_flag(year, soils.get(pid)),
            "monument": bool(mon_by_pand.get(pid)), "monument_info": mon_by_pand.get(pid),
        }
        geom = mapping(s.simplify(0.000005, preserve_topology=True))
        def r(c):
            return [round(c[0], 6), round(c[1], 6)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
        features.append({"type": "Feature", "properties": props, "geometry": {"type": geom["type"], "coordinates": r(geom["coordinates"])}})
        for a in addrs:
            a["bodem"], a["fundering"], a["monument"] = props["bodem"], props["fundering"], props["monument"]

    streets = sorted({d["straatnaam"] for d in docs})
    pubs = publications(v, streets)
    council = council_documents(v, streets)
    street_rows = []
    for st in streets:
        a = [d for d in docs if d["straatnaam"] == st]
        years = sorted(int(d["bouwjaar"]) for d in a if str(d.get("bouwjaar") or "").isdigit())
        areas = sorted(d["oppervlakte"] for d in a if d.get("oppervlakte") and "woon" in (d.get("gebruiksdoel") or ""))
        buurten = {}
        for d in a:
            buurten[d.get("buurtcode")] = buurten.get(d.get("buurtcode"), 0) + 1
        flags = {}
        for d in a:
            if d.get("fundering"):
                flags[d["fundering"]] = flags.get(d["fundering"], 0) + 1
        street_rows.append({
            "straat": st, "adressen": len(a), "woningen": sum(1 for d in a if "woon" in (d.get("gebruiksdoel") or "")),
            "buurten": buurten, "bouwjaar_mediaan": statistics.median(years) if years else None,
            "bouwjaar_min": years[0] if years else None, "bouwjaar_max": years[-1] if years else None,
            "oppervlakte_mediaan": statistics.median(areas) if areas else None,
            "fundering": flags, "monumenten": sum(1 for d in a if d.get("monument")),
            "bodem": sorted({d["bodem"] for d in a if d.get("bodem")}),
            "centrum": [round(statistics.mean(d["lon"] for d in a), 6), round(statistics.mean(d["lat"] for d in a), 6)],
            "bekendmakingen": pubs.get(st, [])[:20], "bekendmakingen_totaal": len(pubs.get(st, [])),
            "raad": council.get(st, [])[:15],
        })

    keep = ("weergavenaam", "straatnaam", "huisnummer", "huisletter", "huisnummertoevoeging", "postcode", "buurtcode",
            "lon", "lat", "adresseerbaarobject_id", "oppervlakte", "gebruiksdoel", "bouwjaar", "pand", "bodem", "fundering", "monument")
    addr_rows = [{k: d.get(k) for k in keep} for d in docs]
    (out / "addresses.json").write_text(json.dumps(addr_rows, ensure_ascii=False, separators=(",", ":")))
    (out / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":")))
    (out / "streets.json").write_text(json.dumps(street_rows, ensure_ascii=False, indent=0))
    years = [int(f["properties"]["bouwjaar"]) for f in features if str(f["properties"]["bouwjaar"] or "").isdigit()]
    summary = {
        "key": key, "naam": v.get("naam", v["woonplaats"]), "buurtcodes": v.get("buurtcodes"),
        **{k: v[k] for k in ("woonplaats", "gemeente", "gemeentecode")},
        "adressen": len(docs), "gebouwen": len(features), "straten": len(streets),
        "woningen": sum(1 for d in docs if "woon" in (d.get("gebruiksdoel") or "")),
        "bbox": [round(x, 5) for x in bbox], "bouwjaar_mediaan": statistics.median(years) if years else None,
        "fundering": {k: sum(1 for f in features if f["properties"]["fundering"] == k) for k in ("high", "check", "low")},
        "monumenten": sum(1 for f in features if f["properties"]["monument"]),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    log(f"  {key}: {summary}")


if __name__ == "__main__":
    t0 = time.time()
    try:
        only = [x for x in __import__("os").environ.get("VILLAGES", "").split(",") if x]
        for key, v in VILLAGES.items():
            if only and key not in only:
                continue
            log(f"\n== {key}")
            build(key, v)
    finally:
        log(f"\ndone in {time.time() - t0:.0f}s")
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "village.log").write_text("\n".join(log_lines))
