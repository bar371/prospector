"""Probe noise sources for Hazerswoude-Rijndijk (Koolrabi) and the roads around it."""
import json
import math
import re
import urllib.parse
import urllib.request

UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}
PT = (4.5789, 52.1266)  # Koolrabi


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, str(e)


def show(title, url, n=1200):
    code, body = get(url)
    print(f"\n### {title}\n{url}\n-> {code}\n{body[:n]}")
    return body


# roads around Koolrabi
d = 0.006
bbox = f"{PT[0]-d},{PT[1]-d*0.6},{PT[0]+d},{PT[1]+d*0.6}"
code, body = get(f"https://api.pdok.nl/rws/nationaal-wegenbestand-wegen/ogc/v1/collections/wegvakken/items?f=json&limit=1000&bbox={bbox}")
kx, ky = 111320 * math.cos(math.radians(PT[1])), 110574
rows = {}
for f in json.loads(body)["features"]:
    p = f["properties"]
    if int(p.get("frc") or 7) > 5:
        continue
    coords = f["geometry"]["coordinates"]
    pts = [c for line in (coords if f["geometry"]["type"] == "MultiLineString" else [coords]) for c in line]
    dist = min(math.hypot((x - PT[0]) * kx, (y - PT[1]) * ky) for x, y in pts)
    k = (p.get("stt_naam"), p.get("wegnummer"), p.get("frc"), p.get("wegbehsrt"))
    rows[k] = min(rows.get(k, 1e9), dist)
print("\n### main roads near Koolrabi (name, road number, frc, manager): distance m")
for k, v in sorted(rows.items(), key=lambda x: x[1])[:15]:
    print(k, round(v))

# RIVM again
show("RIVM ALO", "https://data.rivm.nl/geo/alo/wms?service=WMS&request=GetCapabilities", 200)
# EEA noise (END) map services
for url in ["https://noise.discomap.eea.europa.eu/arcgis/rest/services?f=json",
            "https://noise.discomap.eea.europa.eu/arcgis/rest/services/noiseStoryMap?f=json",
            "https://image.discomap.eea.europa.eu/arcgis/rest/services/Noise?f=json"]:
    show("EEA services", url, 2500)
body = show("EEA NoiseContours road Lden (identify)",
            "https://noise.discomap.eea.europa.eu/arcgis/rest/services/noiseStoryMap/NoiseContours_road_lden/MapServer/identify?"
            + urllib.parse.urlencode({"geometry": f"{PT[0]},{PT[1]}", "geometryType": "esriGeometryPoint", "sr": "4326", "layers": "all",
                                      "tolerance": "2", "mapExtent": f"{PT[0]-0.01},{PT[1]-0.01},{PT[0]+0.01},{PT[1]+0.01}",
                                      "imageDisplay": "400,400,96", "returnGeometry": "false", "f": "json"}), 2500)
# Zuid-Holland provincial geodata
show("Zuid-Holland geoservices", "https://geodata.zuid-holland.nl/geoserver/ows?service=WMS&request=GetCapabilities", 300)
code, caps = get("https://geodata.zuid-holland.nl/geoserver/ows?service=WMS&request=GetCapabilities")
print("ZH noise layers:", [n for n in re.findall(r"<Name>([^<]+)</Name>", caps) if re.search(r"geluid|lden|noise", n, re.I)][:40])
# Rijkswaterstaat noise (geluidregister / geluidbelastingkaarten)
show("RWS geoservices noise", "https://geo.rijkswaterstaat.nl/services/ogc/gdr/geluidbelastingkaarten/ows?service=WMS&request=GetCapabilities", 1500)
