"""Address-level features that drive house value, measured at the home rather than taken from buurt averages.

Per home (from the village build in <dir>/<key>/):
  - plot (BRK parcel) size, building footprint, garden size, back-garden orientation, access to the back (path or road)
  - house type from shared walls (detached, semi-detached, end or mid terrace, apartment); floors and height (3D BAG)
  - distance to open water and whether the plot borders it (BGT)
  - distance to main roads and the railway (NWB, ProRail); the class of the home's own street
  - road and rail noise (RIVM Lden map when reachable, else estimated from road class and distance)
  - new construction; energy label (EP-Online, only with the EP_ONLINE_API_KEY secret)
Writes <dir>/<key>/enrich.json (per adresseerbaarobject) and <dir>/<key>/streets_enrich.json.

Usage: VILLAGES=hazerswoude-rijndijk python pipeline/enrich.py <dir>
"""
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shapely import STRtree
from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/village")
UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}
BAG = "https://api.pdok.nl/kadaster/bag/ogc/v2/collections"
BRK = "https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/v1/collections"
BGT = "https://api.pdok.nl/lv/bgt/ogc/v1/collections"
NWB = "https://api.pdok.nl/rws/nationaal-wegenbestand-wegen/ogc/v1/collections"
SPOOR = "https://api.pdok.nl/prorail/spoorwegen/ogc/v1/collections"
RIVM_WMS = ["https://data.rivm.nl/geo/alo/wms", "https://data.rivm.nl/geo/ank/wms"]

log_lines = []


def log(msg):
    print(msg, flush=True)
    log_lines.append(str(msg))


def fetch(url, headers=None, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404) or attempt == retries - 1:
                raise
            time.sleep(4 * (attempt + 1))
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(4 * (attempt + 1))


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))


def ogc(url_base, collection, bbox, extra=""):
    url = f"{url_base}/{collection}/items?f=json&limit=1000&bbox={','.join(map(str, bbox))}{extra}"
    feats = []
    while url:
        page = fetch_json(url)
        feats += page.get("features", [])
        url = next((l["href"] for l in page.get("links", []) if l.get("rel") == "next"), None)
    return feats


# ---------------------------------------------------------------- local metric projection

class Metric:
    """Equirectangular projection around the area's centre: metres, accurate to well under 1% over a few km."""

    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = lon0, lat0
        self.kx, self.ky = 111320 * math.cos(math.radians(lat0)), 110574

    def fwd(self, x, y, z=None):
        return (x - self.lon0) * self.kx, (y - self.lat0) * self.ky

    def geom(self, g):
        return transform(self.fwd, shape(g) if isinstance(g, dict) else g)


def compass(bearing):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int(((bearing % 360) + 22.5) // 45) % 8]


def bearing(a, b):
    """Bearing in degrees from point a to b in metric coordinates (0 = north, 90 = east)."""
    return math.degrees(math.atan2(b.x - a.x, b.y - a.y)) % 360


def active(f):
    p = f["properties"]
    return not p.get("eind_registratie") and p.get("status", "bestaand") in (None, "bestaand")


# ---------------------------------------------------------------- Leefbaarometer 100 m grid

LBM_ZIP = "https://www.leefbaarometer.nl/resources/open-data-leefbaarometer-meting-2024.zip"


def rd(lon, lat):
    """WGS84 to Rijksdriehoek (EPSG:28992), the standard polynomial approximation (accurate to about 1 m)."""
    dphi, dlam = 0.36 * (lat - 52.15517440), 0.36 * (lon - 5.38720621)
    x = (155000 + 190094.945 * dlam - 11832.228 * dphi * dlam - 114.221 * dphi ** 2 * dlam - 32.391 * dlam ** 3
         - 0.705 * dphi - 2.340 * dphi ** 3 * dlam - 0.608 * dphi * dlam ** 3 - 0.008 * dlam ** 2 + 0.148 * dphi ** 2 * dlam ** 3)
    y = (463000 + 309056.544 * dphi + 3638.893 * dlam ** 2 + 73.077 * dphi ** 2 - 157.984 * dphi * dlam ** 2
         + 59.788 * dphi ** 3 + 0.433 * dlam - 6.439 * dphi ** 2 * dlam ** 2 - 0.032 * dphi * dlam + 0.092 * dlam ** 4
         - 0.054 * dphi * dlam ** 4)
    return x, y


def grid_id(lon, lat):
    x, y = rd(lon, lat)
    return f"E{int(x // 100):04d}N{int(y // 100):04d}"


def lbm_grid(cells):
    """Latest Leefbaarometer scores for the given 100 m cells, with national percentiles among all cells of that year."""
    import bisect
    import csv
    import io
    import tempfile
    import zipfile
    tmp = Path(tempfile.gettempdir()) / "leefbaarometer-2024.zip"
    if not tmp.exists():
        req = urllib.request.Request(LBM_ZIP, headers=UA)
        with urllib.request.urlopen(req, timeout=900) as r, open(tmp, "wb") as fh:
            while chunk := r.read(1 << 20):
                fh.write(chunk)
    z = zipfile.ZipFile(tmp)
    name = next(i.filename for i in z.infolist() if "scores grids" in i.filename and not i.filename.startswith("__MACOSX"))
    latest, national = {}, {}
    with z.open(name) as fh:
        for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")):
            year = int(row["jaar"])
            if year == 2024:
                for k in ("lbm", "onv", "vrz", "won"):
                    if row[k] not in ("NA", ""):
                        national.setdefault(k, []).append(float(row[k]))
            if row["grid_id"] in cells and year >= latest.get(row["grid_id"], {}).get("jaar", 0):
                latest[row["grid_id"]] = {"jaar": year, **{k: (None if row[k] in ("NA", "") else float(row[k]))
                                                          for k in ("lbm", "onv", "vrz", "won", "fys", "soc")}}
    for v in national.values():
        v.sort()

    def pct(k, val):
        arr = national.get(k)
        return None if val is None or not arr else int(round(100 * bisect.bisect_left(arr, val) / len(arr)))

    for cell in latest.values():
        cell["pct_lbm"], cell["pct_onv"], cell["pct_vrz"] = pct("lbm", cell["lbm"]), pct("onv", cell["onv"]), pct("vrz", cell["vrz"])
    log(f"  Leefbaarometer grid: {len(latest)} of {len(cells)} cells found; national 2024 cells {len(national.get('lbm', []))}")
    return latest


# ---------------------------------------------------------------- noise

def noise_layers():
    """Lden noise layers from the RIVM WMS services (Atlas Leefomgeving), if reachable."""
    for base in RIVM_WMS:
        try:
            caps = fetch(f"{base}?service=WMS&request=GetCapabilities", retries=3)
        except Exception as e:
            log(f"  noise map {base}: {e}")
            continue
        names = re.findall(r"<Name>([^<]+)</Name>", caps)
        lden = [n for n in names if re.search(r"lden", n, re.I)]
        log(f"  noise map {base}: {len(lden)} Lden layers, e.g. {lden[:12]}")
        pick = {}
        for kind, pat in (("all", r"alle.?bronnen"), ("road", r"wegverkeer|weg_"), ("rail", r"spoor|trein")):
            cands = sorted((n for n in lden if re.search(pat, n, re.I)), reverse=True)  # newest first by date prefix
            if cands:
                pick[kind] = cands[0]
        if pick:
            return base, pick
    return None, {}


def lden_at(base, layer, lon, lat):
    d = 0.0003
    params = {"service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "layers": layer, "query_layers": layer,
              "styles": "", "crs": "EPSG:4326", "bbox": f"{lat - d},{lon - d},{lat + d},{lon + d}", "width": "61", "height": "61",
              "i": "30", "j": "30", "info_format": "application/json"}
    try:
        feats = fetch_json(f"{base}?{urllib.parse.urlencode(params)}", retries=2).get("features", [])
    except Exception:
        return None
    for f in feats:
        for k, v in f.get("properties", {}).items():
            if isinstance(v, (int, float)) and 20 <= v <= 95:
                return float(v)
    return None


# NWB functional road class -> typical Lden at 10 m from the road axis: motorway, provincial road (N11), collector roads,
# then residential streets (30 km/h, a few hundred cars a day), which hardly add noise.
# EU Environmental Noise Directive maps (EEA, updated April 2026): Lden bands for major roads, 10 m pixels.
# A road appears when it carries more than about 8,000 vehicles a day; no data means below 55 dB from those roads.
EEA_ROAD = "https://noise.discomap.eea.europa.eu/arcgis/rest/services/noiseStoryMap/NoiseContours_road_lden/ImageServer/identify"
EEA_BAND_DB = {1: 57, 2: 62, 3: 67, 4: 72, 5: 77}  # band 1 = 55-59 dB ... band 5 = 75 dB and more (band middles)


def eea_road_band(lon, lat):
    q = urllib.parse.urlencode({"geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
                                "geometryType": "esriGeometryPoint", "returnGeometry": "false", "returnCatalogItems": "false", "f": "json"})
    try:
        value = fetch_json(f"{EEA_ROAD}?{q}", retries=3).get("value")
    except Exception:
        return "error"
    return int(value) if str(value).isdigit() else None


ROAD_DB_AT_10M = {0: 78, 1: 77, 2: 74, 3: 71, 4: 64, 5: 60, 6: 51, 7: 48}
RAIL_DB_AT_10M = 68  # regional line with sprinter trains (Leiden-Alphen)


def estimated_lden(road_dists, rail_dist):
    """Rough Lden from the nearest roads per class and the railway: a line source over soft ground, about -4.5 dB per
    doubling of distance. Only a fallback for when the RIVM noise map is unreachable."""
    levels = []
    for frc, dist in road_dists.items():
        if dist is not None:
            levels.append(ROAD_DB_AT_10M.get(frc, 55) - 15 * math.log10(max(dist, 10) / 10))
    if rail_dist is not None:
        levels.append(RAIL_DB_AT_10M - 15 * math.log10(max(rail_dist, 10) / 10))
    total = 10 * math.log10(sum(10 ** (l / 10) for l in levels) + 10 ** 4.0)  # 40 dB background
    return round(total, 1)


def quiet_score(lden):
    """0-100 from Lden: 45 dB or less is 100, 65 dB or more is 0 (WHO advises below 53 dB for road traffic)."""
    return None if lden is None else int(round(max(0, min(100, (65 - lden) * 5))))


# ---------------------------------------------------------------- per village

def build(key):
    src = OUT / key
    addresses = json.loads((src / "addresses.json").read_text())
    buildings = json.loads((src / "buildings.geojson").read_text())["features"]
    summary = json.loads((src / "summary.json").read_text())
    homes = [a for a in addresses if "woon" in (a.get("gebruiksdoel") or "") and a.get("lon")]
    lons, lats = [a["lon"] for a in homes], [a["lat"] for a in homes]
    bbox = [min(lons) - 0.004, min(lats) - 0.003, max(lons) + 0.004, max(lats) + 0.003]
    M = Metric(statistics.mean(lons), statistics.mean(lats))
    log(f"\n== {key}: {len(homes)} homes, bbox {bbox}")

    # all buildings (also sheds and garages without an address), to cut gardens out of parcels
    panden = [f for f in ogc(BAG, "pand", bbox) if f.get("geometry")]
    pand_geom = [M.geom(f["geometry"]).buffer(0) for f in panden]
    pand_id = [f["properties"]["identificatie"] for f in panden]
    pand_tree = STRtree(pand_geom)
    pand_index = {pid: i for i, pid in enumerate(pand_id)}
    log(f"  BAG: {len(panden)} buildings")

    parcels = [f for f in ogc(BRK, "perceel", bbox) if f.get("geometry")]
    parcel_geom = [M.geom(f["geometry"]).buffer(0) for f in parcels]
    parcel_tree = STRtree(parcel_geom)
    log(f"  BRK: {len(parcels)} parcels; sample {json.dumps(parcels[0]['properties'], ensure_ascii=False)[:300] if parcels else '-'}")

    water_feats = [f for f in ogc(BGT, "waterdeel", bbox) if f.get("geometry") and active(f)]
    types = {}
    for f in water_feats:
        types[f["properties"].get("type")] = types.get(f["properties"].get("type"), 0) + 1
    log(f"  BGT water: {len(water_feats)} active parts by type {types}")
    water = [(M.geom(f["geometry"]).buffer(0), f["properties"].get("type")) for f in water_feats
             if "greppel" not in (f["properties"].get("type") or "")]
    water_tree = STRtree([g for g, _ in water])
    # width of each water part (area / half perimeter) separates a river or canal from a ditch
    water_width = [g.area / max(g.length / 2, 1) for g, _ in water]

    wegdeel_feats = [f for f in ogc(BGT, "wegdeel", bbox) if f.get("geometry") and active(f)]
    funcs = {}
    for f in wegdeel_feats:
        funcs[f["properties"].get("functie")] = funcs.get(f["properties"].get("functie"), 0) + 1
    log(f"  BGT road parts: {len(wegdeel_feats)} by function {dict(sorted(funcs.items(), key=lambda x: -x[1])[:14])}")
    paths = [M.geom(f["geometry"]).buffer(0) for f in wegdeel_feats]
    path_tree = STRtree(paths)
    car = [g for g, f in zip(paths, wegdeel_feats) if (f["properties"].get("functie") or "").startswith("rijbaan")]
    car_tree = STRtree(car)

    roads = [f for f in ogc(NWB, "wegvakken", bbox) if f.get("geometry")]
    road_geom = [M.geom(f["geometry"]) for f in roads]
    road_frc = [int(f["properties"].get("frc") or 7) for f in roads]
    road_name = [f["properties"].get("stt_naam") or "" for f in roads]
    road_beh = [f["properties"].get("wegbehsrt") for f in roads]
    road_tree = STRtree(road_geom)
    log(f"  NWB: {len(roads)} road segments; classes {sorted({(road_frc[i], road_beh[i]) for i in range(len(roads))})}")

    rail_geom = []
    try:
        cols = fetch_json(f"{SPOOR}?f=json")["collections"]
        ids = [c["id"] for c in cols]
        rail_col = next((i for i in ids if re.search(r"spoorbaan|spooras|spoor", i)), None)
        log(f"  ProRail collections {ids}; using {rail_col}")
        if rail_col:
            rail_geom = [M.geom(f["geometry"]) for f in ogc(SPOOR, rail_col, bbox) if f.get("geometry")]
    except Exception as e:
        log(f"  ProRail: {e}")
    rail = unary_union(rail_geom) if rail_geom else None
    log(f"  rail: {len(rail_geom)} segments")

    noise_base, noise_layer = noise_layers()

    # 3D BAG: floors, height and roof type per home building
    home_pids = sorted({a["pand"] for a in homes if a.get("pand")})

    def bag3d(pid):
        try:
            doc = fetch_json(f"https://api.3dbag.nl/collections/pand/items/NL.IMBAG.Pand.{pid}", retries=2)
        except Exception:
            return pid, None
        objs = (doc.get("feature") or doc).get("CityObjects", {})
        attrs = next((o.get("attributes") for o in objs.values() if o.get("attributes", {}).get("b3_h_maaiveld") is not None
                      or o.get("attributes", {}).get("b3_bouwlagen") is not None), None) or next(
            (o.get("attributes") for o in objs.values() if o.get("attributes")), None)
        return pid, attrs

    with ThreadPoolExecutor(8) as pool:
        attrs3d = dict(pool.map(bag3d, home_pids))
    sample = next((v for v in attrs3d.values() if v), None)
    log(f"  3D BAG: {sum(1 for v in attrs3d.values() if v)} of {len(home_pids)} buildings; sample keys {sorted(sample)[:40] if sample else '-'}")

    def three_d(pid):
        a = attrs3d.get(pid) or {}
        top = a.get("b3_h_dak_max") or a.get("b3_h_max")
        ground = a.get("b3_h_maaiveld")
        height = round(top - ground, 1) if isinstance(top, (int, float)) and isinstance(ground, (int, float)) else None
        return {"bouwlagen": a.get("b3_bouwlagen"), "hoogte_m": height, "daktype": a.get("b3_dak_type")}

    # house type from shared walls with other home buildings
    home_pid_set = set(home_pids)
    home_pand_set = {i for i, pid in enumerate(pand_id) if pid in home_pid_set}

    def neighbours(i):
        g = pand_geom[i].buffer(0.3)
        return [j for j in pand_tree.query(g) if j != i and j in home_pand_set and g.intersection(pand_geom[j].buffer(0.3)).length > 4]

    def house_type(pid, n_addr):
        if n_addr > 1:
            return "apartment"
        i = pand_index.get(pid)
        if i is None:
            return None
        nb = neighbours(i)
        if not nb:
            return "detached"
        if len(nb) == 1:
            return "end of terrace" if len(neighbours(nb[0])) >= 2 else "semi-detached"
        return "mid-terrace"

    addr_per_pand = {}
    for a in addresses:
        if a.get("pand"):
            addr_per_pand[a["pand"]] = addr_per_pand.get(a["pand"], 0) + 1

    energy_key = os.environ.get("EP_ONLINE_API_KEY")

    def energy(vbo):
        if not energy_key:
            return None
        try:
            doc = fetch_json(f"https://public.ep-online.nl/api/v5/PandEnergielabel/AdresseerbaarObject/{vbo}",
                             headers={"Authorization": energy_key}, retries=2)
        except Exception:
            return None
        doc = doc[0] if isinstance(doc, list) and doc else doc
        return {"label": doc.get("Energieklasse") or doc.get("energieklasse"), "datum": doc.get("Registratiedatum") or doc.get("registratiedatum")} if doc else None

    noise_cache = {}
    try:
        lbm_cells = lbm_grid({grid_id(a["lon"], a["lat"]) for a in homes})
    except Exception as e:
        log(f"  Leefbaarometer grid failed: {e}")
        lbm_cells = {}

    def one(a):
        pt = M.geom(Point(a["lon"], a["lat"]))
        out = {}
        # parcel: the smallest parcel holding the address point
        cands = [i for i in parcel_tree.query(pt, predicate="intersects")]
        parcel = min((parcel_geom[i] for i in cands), key=lambda g: g.area) if cands else None
        pi = min(cands, key=lambda i: parcel_geom[i].area) if cands else None
        home = pand_geom[pand_index[a["pand"]]] if a.get("pand") in pand_index else None
        n_addr = addr_per_pand.get(a.get("pand"), 1)
        out["woningtype"] = house_type(a.get("pand"), n_addr)
        if home is not None:
            out["footprint_m2"] = round(home.area / n_addr)
        out.update(three_d(a.get("pand")))
        if home is not None and parcel is not None and home.intersects(parcel):
            house_c = home.intersection(parcel).centroid
        else:
            house_c = (home if home is not None else pt).centroid

        # nearest roads: own street, main roads
        near = road_tree.query(pt.buffer(1500))
        own = [i for i in near if road_name[i] == a["straatnaam"]]
        front = min(own or list(near), key=lambda i: road_geom[i].distance(pt)) if len(near) else None
        if front is not None:
            out["straat_klasse"] = road_frc[front]
        dist_by_frc = {}
        for i in near:
            d = road_geom[i].distance(pt)
            if d < dist_by_frc.get(road_frc[i], 1e9):
                dist_by_frc[road_frc[i]] = d
        main = [road_geom[i].distance(pt) for i in near if road_frc[i] <= 3 or road_beh[i] in ("R", "P")]
        out["afstand_hoofdweg_m"] = round(min(main)) if main else None
        out["afstand_spoor_m"] = round(rail.distance(pt)) if rail is not None else None

        # parcel, garden and its orientation
        if parcel is not None:
            out["perceel_m2"] = parcels[pi]["properties"].get("kadastrale_grootte_waarde") or round(parcel.area)
            on_parcel = pand_tree.query(parcel, predicate="intersects")
            garden = parcel.difference(unary_union([pand_geom[j] for j in on_parcel])) if len(on_parcel) else parcel
            if n_addr == 1:  # apartments share the plot; no own garden from the parcel
                pieces = [g for g in getattr(garden, "geoms", [garden]) if g.area >= 4]
                out["tuin_m2"] = round(sum(g.area for g in pieces))
                if front is not None and pieces:
                    rd = road_geom[front]
                    back = [g for g in pieces if rd.distance(g.centroid) > rd.distance(house_c) + 1]
                    if back:
                        biggest = max(back, key=lambda g: g.area)
                        out["achtertuin_m2"] = round(sum(g.area for g in back))
                        b = bearing(house_c, biggest.centroid)
                        out["achtertuin_richting"] = compass(b)
                        out["achtertuin_zuid"] = 112.5 <= b <= 247.5
                        far = biggest.buffer(1.5)
                        out["achterom"] = any(paths[j].distance(rd) > 8 for j in path_tree.query(far, predicate="intersects"))
            # water
            wn = water_tree.query(parcel.buffer(300))
            if len(wn):
                dists = [(water[j][0].distance(parcel), water_width[j]) for j in wn]
                d_any = min(d for d, _ in dists)
                wide = [d for d, w in dists if w >= 12]
                out["afstand_water_m"] = round(d_any)
                out["aan_het_water"] = d_any <= 2
                out["afstand_breed_water_m"] = round(min(wide)) if wide else None
                out["aan_breed_water"] = bool(wide) and min(wide) <= 5
            else:
                out["afstand_water_m"] = None
                out["aan_het_water"] = False

        # noise: measured map if reachable, else estimated from road and rail distances
        cell = (round(a["lon"] / 0.00015), round(a["lat"] / 0.0001))  # about 10 m, the map's pixel size
        if noise_layer.get("all") or noise_layer.get("road"):
            if ("rivm", cell) not in noise_cache:
                noise_cache[("rivm", cell)] = lden_at(noise_base, noise_layer.get("all") or noise_layer["road"], a["lon"], a["lat"])
            out["lden_db"] = noise_cache[("rivm", cell)]
            out["lden_bron"] = "RIVM noise map (all sources)"
        if out.get("lden_db") is None:
            if ("eea", cell) not in noise_cache:
                noise_cache[("eea", cell)] = eea_road_band(a["lon"], a["lat"])
            band = noise_cache[("eea", cell)]
            # smaller roads and the railway are not on the EU map: estimate them, staying below the map's 55 dB threshold
            minor = estimated_lden({k: v for k, v in dist_by_frc.items() if k >= 4}, out.get("afstand_spoor_m"))
            if band == "error":
                out["lden_db"] = estimated_lden(dist_by_frc, out.get("afstand_spoor_m"))
                out["lden_bron"] = "estimated from road class and distance (EU noise map unreachable)"
            elif band:
                major = EEA_BAND_DB.get(band, 57)
                out["lden_db"] = round(10 * math.log10(10 ** (major / 10) + 10 ** (min(minor, 54) / 10)), 1)
                out["lden_band"] = f"{40 + 5 * (band + 2)}-{44 + 5 * (band + 2)} dB" if band < 5 else "75 dB or more"
                out["lden_bron"] = "EU noise map for major roads, plus an estimate for small roads and rail"
            else:
                out["lden_db"] = round(min(minor, 54.5), 1)
                out["lden_bron"] = "below 55 dB on the EU noise map; small roads and rail estimated"
        out["score_rust"] = quiet_score(out["lden_db"])
        # car-light: quiet own street, far from main roads
        own_class = out.get("straat_klasse")
        street_score = {7: 100, 6: 80, 5: 40, 4: 10}.get(own_class, 0)
        main_score = min(100, (out["afstand_hoofdweg_m"] or 1000) / 5)
        # how far the home is from where cars drive (a home on a footpath or a car-free court scores high)
        near_car = car_tree.query(pt.buffer(200))
        car_d = min((car[j].distance(home if home is not None else pt) for j in near_car), default=200)
        out["afstand_autoweg_m"] = round(car_d)
        car_score = min(100, car_d * 4)  # 25 m or more from any car lane is fully car-free at the door
        out["score_autoluw"] = int(round(0.4 * street_score + 0.3 * car_score + 0.3 * main_score))

        # liveability and its safety part for the home's own 100 m cell (Leefbaarometer is itself built from house prices)
        cell = lbm_cells.get(grid_id(a["lon"], a["lat"]))
        if cell and cell.get("lbm") is not None:
            out["leefbaarometer_grid"] = {"jaar": cell["jaar"], "score": round(cell["lbm"], 3), "pct": cell["pct_lbm"],
                                          "veiligheid_pct": cell["pct_onv"], "voorzieningen_pct": cell["pct_vrz"]}
            out["score_leefbaarheid"] = cell["pct_lbm"]
        year = a.get("bouwjaar")
        out["nieuwbouw"] = bool(year) and int(year) >= 2015
        out["energielabel"] = energy(a["adresseerbaarobject_id"])
        if not out["energielabel"] and year and int(year) >= 2021:
            out["energielabel_verwacht"] = "A+++ or better (built under the BENG energy rules from 2021)"
        return a["adresseerbaarobject_id"], {k: v for k, v in out.items() if v is not None}

    t0 = time.time()
    with ThreadPoolExecutor(8) as pool:
        result = dict(pool.map(one, homes))
    bands = {}
    for k, v in noise_cache.items():
        if k[0] == "eea":
            bands[v] = bands.get(v, 0) + 1
    log(f"  enriched {len(result)} homes in {time.time() - t0:.0f}s; RIVM layers {noise_layer or 'unavailable'}; EU road bands per 10 m cell {bands}")

    # per street
    streets = {}
    for a in homes:
        e = result.get(a["adresseerbaarobject_id"], {})
        streets.setdefault(a["straatnaam"], []).append(e)

    def med(vals):
        vals = [v for v in vals if isinstance(v, (int, float))]
        return round(statistics.median(vals)) if vals else None

    def share(vals):
        vals = [v for v in vals if isinstance(v, bool)]
        return round(100 * sum(vals) / len(vals)) if vals else None

    street_out = {}
    for st, es in streets.items():
        types = {}
        for e in es:
            if e.get("woningtype"):
                types[e["woningtype"]] = types.get(e["woningtype"], 0) + 1
        street_out[st] = {
            "woningen": len(es), "woningtypen": types,
            "perceel_m2_mediaan": med(e.get("perceel_m2") for e in es if e.get("woningtype") != "apartment"),
            "tuin_m2_mediaan": med(e.get("tuin_m2") for e in es),
            "achtertuin_zuid_pct": share(e.get("achtertuin_zuid") for e in es),
            "achterom_pct": share(e.get("achterom") for e in es),
            "aan_het_water_pct": share(e.get("aan_het_water") for e in es),
            "aan_breed_water_pct": share(e.get("aan_breed_water") for e in es),
            "afstand_hoofdweg_m_mediaan": med(e.get("afstand_hoofdweg_m") for e in es),
            "afstand_autoweg_m_mediaan": med(e.get("afstand_autoweg_m") for e in es),
            "afstand_spoor_m_mediaan": med(e.get("afstand_spoor_m") for e in es),
            "lden_db_mediaan": med(e.get("lden_db") for e in es),
            "lden_bron": es[0].get("lden_bron") if es else None,
            "score_rust": med(e.get("score_rust") for e in es),
            "score_autoluw": med(e.get("score_autoluw") for e in es),
            "score_leefbaarheid": med(e.get("score_leefbaarheid") for e in es),
            "veiligheid_grid_pct": med((e.get("leefbaarometer_grid") or {}).get("veiligheid_pct") for e in es),
            "leefbaarometer_jaar": max(((e.get("leefbaarometer_grid") or {}).get("jaar") or 0 for e in es), default=None) or None,
            "nieuwbouw_pct": share(e.get("nieuwbouw") for e in es),
            "bouwlagen_mediaan": med(e.get("bouwlagen") for e in es),
        }
    (src / "enrich.json").write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    (src / "streets_enrich.json").write_text(json.dumps(street_out, ensure_ascii=False, indent=0))
    for st in ("Koolrabi", "Pastinaak", "Rijndijk", "Valeriusstraat"):
        if st in street_out:
            log(f"  {st}: {street_out[st]}")
    ex = next((result[a["adresseerbaarobject_id"]] for a in homes if a["straatnaam"] == "Koolrabi"), None)
    log(f"  example Koolrabi home: {ex}")


if __name__ == "__main__":
    t0 = time.time()
    only = [x for x in os.environ.get("VILLAGES", "").split(",") if x]
    try:
        for d in sorted(p for p in OUT.iterdir() if (p / "addresses.json").exists()):
            if not only or d.name in only:
                build(d.name)
    finally:
        log(f"\ndone in {time.time() - t0:.0f}s")
        (OUT / "enrich.log").write_text("\n".join(log_lines))
