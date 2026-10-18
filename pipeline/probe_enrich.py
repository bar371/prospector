"""Probe noise values at points around Hazerswoude-Rijndijk: EEA END image services and Zuid-Holland contours."""
import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}
POINTS = {"Koolrabi": (4.5789, 52.1266), "N11 roadside": (4.5800, 52.1222), "Rijndijk 33": (4.5926, 52.1326),
          "Valeriusstraat": (4.5908, 52.1262), "railway": (4.5880, 52.1290)}


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, str(e)


for svc in ("NoiseContours_road_lden", "NoiseContours_rail_lden"):
    base = f"https://noise.discomap.eea.europa.eu/arcgis/rest/services/noiseStoryMap/{svc}/ImageServer"
    code, body = get(base + "?f=json")
    print(f"\n### {svc} info -> {code}\n{body[:1500]}")
    for name, (lon, lat) in POINTS.items():
        q = urllib.parse.urlencode({"geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
                                    "geometryType": "esriGeometryPoint", "returnGeometry": "false", "returnCatalogItems": "false", "f": "json"})
        code, body = get(f"{base}/identify?{q}")
        print(f"  {name}: {code} {body[:300]}")
    code, body = get(base + "/rasterAttributeTable?f=json")
    print(f"  attribute table -> {code} {body[:1200]}")

wms = "https://geodata.zuid-holland.nl/geoserver/ows"
for layer in ("milieu:GELUID_2022_CONTOUREN_ETMAAL",):
    for name, (lon, lat) in POINTS.items():
        d = 0.0004
        params = {"service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "layers": layer, "query_layers": layer, "styles": "",
                  "crs": "EPSG:4326", "bbox": f"{lat-d},{lon-d},{lat+d},{lon+d}", "width": "81", "height": "81", "i": "40", "j": "40",
                  "info_format": "application/json", "feature_count": "5"}
        code, body = get(f"{wms}?{urllib.parse.urlencode(params)}")
        print(f"\n### ZH {layer} at {name} -> {code}\n{body[:700]}")
