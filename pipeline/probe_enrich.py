"""Probe the free sources for building-level enrichment; prints what each endpoint returns."""
import json
import re
import urllib.parse
import urllib.request

UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}
PT = (4.5935, 52.1335)  # Koolrabi area, Hazerswoude-Rijndijk
BBOX = [PT[0] - 0.002, PT[1] - 0.0015, PT[0] + 0.002, PT[1] + 0.0015]


def get(url, data=None, headers=None):
    try:
        req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500]
    except Exception as e:
        return None, str(e)


def show(title, url, n=1500):
    code, body = get(url)
    print(f"\n### {title}\n{url}\n-> {code}\n{body[:n]}")
    return body


bb = ",".join(map(str, BBOX))
# Locate Koolrabi
show("Locatieserver Koolrabi", "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free?q=Koolrabi%20Hazerswoude-Rijndijk&rows=1&fl=weergavenaam,centroide_ll,buurtcode")
# BRK parcels (OGC API versions)
for v in ("v1", "v5", "v4"):
    body = show(f"BRK kadastrale kaart OGC {v} landing", f"https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/{v}/collections?f=json", 800)
body = show("BRK perceel items", f"https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/v1/collections/perceel/items?f=json&limit=2&bbox={bb}", 2500)
# BGT
show("BGT OGC collections", "https://api.pdok.nl/lv/bgt/ogc/v1/collections?f=json", 3000)
show("BGT waterdeel", f"https://api.pdok.nl/lv/bgt/ogc/v1/collections/waterdeel/items?f=json&limit=2&bbox={bb}", 2000)
show("BGT wegdeel", f"https://api.pdok.nl/lv/bgt/ogc/v1/collections/wegdeel/items?f=json&limit=2&bbox={bb}", 2000)
# NWB
show("NWB OGC collections", "https://api.pdok.nl/rws/nationaal-wegenbestand-wegen/ogc/v1/collections?f=json", 1500)
show("NWB wegvakken", f"https://api.pdok.nl/rws/nationaal-wegenbestand-wegen/ogc/v1/collections/wegvakken/items?f=json&limit=2&bbox={bb}", 2500)
# 3D BAG
show("3DBAG pand", "https://api.3dbag.nl/collections/pand/items?bbox=" + bb + "&limit=1", 2500)
# RIVM noise WMS
caps = show("RIVM ALO WMS capabilities", "https://data.rivm.nl/geo/alo/wms?service=WMS&request=GetCapabilities", 300)
names = re.findall(r"<Name>([^<]*(?:[Gg]eluid|lden|Lden)[^<]*)</Name>", caps)
print("noise layers:", names[:80])
for layer in [n for n in names if re.search(r"lden", n, re.I)][:6]:
    d = 0.0005
    params = {"service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "layers": layer, "query_layers": layer,
              "crs": "EPSG:4326", "bbox": f"{PT[1]-d},{PT[0]-d},{PT[1]+d},{PT[0]+d}", "width": "101", "height": "101",
              "i": "50", "j": "50", "info_format": "application/json", "styles": ""}
    show(f"RIVM GetFeatureInfo {layer}", "https://data.rivm.nl/geo/alo/wms?" + urllib.parse.urlencode(params), 600)
# EP-Online (energy labels) without a key, to see the error
show("EP-Online without key", "https://public.ep-online.nl/api/v5/PandEnergielabel/AdresseerbaarObject/1672010000003432", 400)
