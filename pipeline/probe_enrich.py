"""Probe CBS open data with average WOZ per postcode-6 and per 100 m square (PDOK)."""
import json
import urllib.request

UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}
PTS = {"Koolrabi": (4.5789, 52.1266), "Valeriusstraat": (4.5908, 52.1262), "Rijndijk 33": (4.5926, 52.1326)}


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, str(e)


for base in ["https://api.pdok.nl/cbs/postcode6/ogc/v1", "https://api.pdok.nl/cbs/vierkantstatistieken100m/ogc/v1",
             "https://api.pdok.nl/cbs/postcode6/ogc/v2", "https://api.pdok.nl/cbs/vierkantstatistieken100m/ogc/v2"]:
    code, body = get(f"{base}/collections?f=json")
    print(f"\n### {base} -> {code}")
    if code != 200:
        print(body[:200]); continue
    cols = [c["id"] for c in json.loads(body)["collections"]]
    print("collections", cols)
    for col in cols:
        for name, (lon, lat) in PTS.items():
            d = 0.0002
            code, body = get(f"{base}/collections/{col}/items?f=json&limit=3&bbox={lon-d},{lat-d},{lon+d},{lat+d}")
            try:
                feats = json.loads(body)["features"]
            except Exception:
                print(" ", col, name, code, body[:200]); continue
            for f in feats[:2]:
                p = f["properties"]
                woz = {k: v for k, v in p.items() if "woz" in k.lower() or k.lower() in ("postcode", "postcode6", "jaarstatcode", "jaar", "crs28992res100m", "aantal_woningen", "aantal_inwoners")}
                print(" ", col, name, woz)
        if cols.index(col) == 0:
            print("   all fields:", sorted(feats[0]["properties"]) if feats else "-")
