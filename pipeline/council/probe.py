"""Find where Leiden and Alphen aan den Rijn publish council meetings, agendas and video."""
import json
import re
import sys
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/council-probe")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)", "Accept": "application/json, text/html;q=0.9"}


def get(url, data=None, headers=None, limit=3_000_000):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.headers.get("content-type", ""), r.read(limit).decode("utf-8", "replace")


def probe(name, url, data=None, headers=None, keep=40_000, pattern=None):
    try:
        status, ctype, text = get(url, data, headers)
        found = sorted(set(re.findall(pattern, text)))[:300] if pattern else []
        (OUT / f"{name}.txt").write_text(f"{status} {ctype} {len(text)}\n{url}\n\nMATCHES:\n" + "\n".join(found) + "\n\nBODY:\n" + text[:keep])
        print(f"OK   {name}: {status} {len(text)} chars, {len(found)} matches")
    except Exception as e:
        (OUT / f"{name}.txt").write_text(f"ERROR {e}\n{url}\n")
        print(f"FAIL {name}: {e}")


ORI = "https://api.openraadsinformatie.nl/v1/elastic"
q = {"query": {"bool": {"must": [{"term": {"@type": "Meeting"}}]}}, "size": 3, "sort": [{"start_date": {"order": "desc", "unmapped_type": "date"}}]}
probe("ori_leiden_meetings", f"{ORI}/ori_leiden_*/_search", json.dumps(q).encode(), {"Content-Type": "application/json"})
q = {"query": {"bool": {"must": [{"term": {"@type": "AgendaItem"}}, {"match": {"name": "Energiepark"}}]}}, "size": 3}
probe("ori_leiden_agendaitems", f"{ORI}/ori_leiden_*/_search", json.dumps(q).encode(), {"Content-Type": "application/json"})
q = {"size": 0, "aggs": {"types": {"terms": {"field": "@type", "size": 50}}}}
probe("ori_leiden_types", f"{ORI}/ori_leiden_*/_search", json.dumps(q).encode(), {"Content-Type": "application/json"})
q = {"query": {"bool": {"must": [{"term": {"@type": "MediaObject"}}, {"wildcard": {"content_type": "*video*"}}]}}, "size": 3}
probe("ori_leiden_video", f"{ORI}/ori_leiden_*/_search", json.dumps(q).encode(), {"Content-Type": "application/json"})

# Notubiz (Leiden): public API and site
for name, url in {
    "notubiz_orgs": "https://api.notubiz.nl/organisations?format=json",
    "notubiz_leiden_site": "https://leiden.notubiz.nl/",
    "notubiz_leiden_events": "https://api.notubiz.nl/events?organisation_id=344&format=json&version=1.10.8",
    "leiden_raad_site": "https://leiden.raadsinformatie.nl/",
    "gemeenteraad_leiden": "https://gemeenteraad.leiden.nl/",
}.items():
    probe(name, url, pattern=r'(https?://[^"\'\s<>]+(?:m3u8|mp4|webcast|video|media|vergadering|event)[^"\'\s<>]*)')

# Alphen aan den Rijn: bestuurlijkeinformatie.nl
for name, url in {
    "alphen_home": "https://alphenaandenrijn.bestuurlijkeinformatie.nl/",
    "alphen_calendar": "https://alphenaandenrijn.bestuurlijkeinformatie.nl/Calendar",
    "alphen_search": "https://alphenaandenrijn.bestuurlijkeinformatie.nl/Search?searchString=Rijndijk",
    "alphen_reports": "https://alphenaandenrijn.bestuurlijkeinformatie.nl/Reports",
}.items():
    probe(name, url, pattern=r'href="([^"]+)"')
