"""Probe the public WOZ-waardeloket for one address (Koolrabi 11, Hazerswoude-Rijndijk): response shape and CORS headers."""
import json
import urllib.request

UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)", "Origin": "https://bar371.github.io"}


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")[:500]
    except Exception as e:
        return None, {}, str(e)


code, _, body = get("https://api.pdok.nl/bzk/locatieserver/search/v3_1/free?q=Koolrabi%2011%20Hazerswoude-Rijndijk&fq=type:adres&rows=1"
                    "&fl=weergavenaam,nummeraanduiding_id,adresseerbaarobject_id")
doc = json.loads(body)["response"]["docs"][0]
print("address", doc)
na = doc["nummeraanduiding_id"]
for url in [f"https://api.kadaster.nl/lvwoz/wozwaardeloket-api/v1/wozwaarde/nummeraanduiding/{na}",
            f"https://www.wozwaardeloket.nl/wozwaardeloket-api/v1/wozwaarde/nummeraanduiding/{na}",
            "https://www.wozwaardeloket.nl/"]:
    code, headers, body = get(url)
    print(f"\n### {url}\n-> {code}\nheaders: { {k: v for k, v in headers.items() if k.lower().startswith(('access-control', 'content-type', 'set-cookie'))} }\n{body[:1500]}")
