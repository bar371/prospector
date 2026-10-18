"""Council intelligence for the test areas.

Leiden (Qualigraf + Connected Views):
  1. search council documents and agenda items for the area's terms;
  2. for each meeting with video, read the agenda-item start and stop times from the meeting event log;
  3. download the Dutch subtitles for those time windows (published two days after a meeting);
  4. keep the passages that mention the area, with timestamps and a link to the video.

Usage: python pipeline/council/build.py <output-dir>
"""
import datetime as dt
import html
import json
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/council")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (prospect-pipeline/0.1; +https://github.com/bar371/prospector)"}

QUALIGRAF = "https://leiden.qualigraf.nl"
CV_API = "https://amcpwe-quali-cc-apim.azure-api.net/api"
CV_TENANT = "06cf837c-02ca-4956-8ee6-eecf2c3554e7"  # Leiden's tenant in Connected Views (public)
HLS = "https://amcpwequalihls.azurewebsites.net"

AREAS = {
    "zamenhofstraat-leiden": {
        "gemeente": "Leiden",
        # search terms: the street, its buurt, and the area plans that border it
        "search": ["Zamenhofstraat", "Noordvest", "Energiepark", "Binnenvestgracht"],
        # words that make a subtitle passage relevant to this area
        "match": ["zamenhof", "noordvest", "energiepark", "binnenvestgracht", "langegracht", "maresingel"],
    },
}
SINCE = "01-01-2023"

log_lines = []


def log(msg):
    print(msg, flush=True)
    log_lines.append(str(msg))


def fetch(url, data=None, headers=None, binary=False, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
            return body if binary else body.decode("utf-8", "replace")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def fetch_json(url, data=None, headers=None):
    return json.loads(fetch(url, data, headers))


def qualigraf_search(term):
    body = {"text": term, "where": "", "categories": [], "commissions": [], "modules": [],
            "dates": {"from": SINCE, "until": dt.date.today().strftime("%d-%m-%Y")}, "documentTypes": [], "locations": [],
            "casecode": "", "media": False, "party": [], "person": [], "onlyTitle": False, "secties": ""}
    return fetch_json(f"{QUALIGRAF}/vji/public/search/action=doSearch", json.dumps(body).encode(),
                      {"Content-Type": "application/json"})


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_vtt(text):
    """WebVTT -> list of (start_seconds, end_seconds, text)."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        m = re.search(r"(\d+:)?(\d+):(\d+)\.(\d+)\s*-->\s*(\d+:)?(\d+):(\d+)\.(\d+)", block)
        if not m:
            continue
        def secs(h, mi, s, ms):
            return int((h or "0:")[:-1] or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000
        start = secs(m.group(1), m.group(2), m.group(3), m.group(4))
        end = secs(m.group(5), m.group(6), m.group(7), m.group(8))
        lines = block[m.end():].strip().splitlines()
        txt = strip_tags(" ".join(lines))
        if txt:
            cues.append((start, end, txt))
    return cues


def explore_meeting(ag_hexkey):
    """Log the shape of each source for one meeting (used while building the pipeline)."""
    detail = fetch_json(f"{QUALIGRAF}/vji/public/agenda/agendaview/action=detaildata/ag={ag_hexkey}")
    video = fetch_json(f"{QUALIGRAF}/vji/public/agendavideo/action=detaildata/ag={ag_hexkey}")
    (OUT / "explore").mkdir(exist_ok=True)
    (OUT / "explore" / f"agendavideo-{ag_hexkey}.json").write_text(json.dumps(video, indent=1, ensure_ascii=False)[:200000])
    log(f"  agendavideo keys: {list(video) if isinstance(video, dict) else type(video)}")
    return detail, video


def check_connected_views():
    """Confirm the Connected Views endpoints answer without a browser (known meeting of 15 Oct 2026)."""
    mid = "0378b8e5-94df-49a0-f3fe-08df252bc936"
    for name, url in {
        "eventlog": f"{CV_API}/EventLog/HighPerformance?api-version=1.0&tenantId={CV_TENANT}&meetingId={mid}",
        "subs_playlist": f"{HLS}/2026-10-15/leidenraadzaal/subtitles/nl-pol_Live.m3u8?start=2026-10-15T18%3A02%3A01.0000000%2B00%3A00&end=2026-10-15T18%3A12%3A01.0000000%2B00%3A00&sourcetimestamps=1&returnType=hls",
    }.items():
        try:
            text = fetch(url)
            (OUT / "explore").mkdir(exist_ok=True)
            (OUT / "explore" / f"cv-{name}.txt").write_text(text[:300000])
            log(f"  connected views {name}: OK, {len(text)} chars: {text[:300]!r}")
            if name == "subs_playlist":
                segs = [l for l in text.splitlines() if l and not l.startswith("#")]
                if segs:
                    first = urllib.parse.urljoin(url, segs[0])
                    vtt = fetch(first)
                    (OUT / "explore" / "cv-first-segment.vtt").write_text(vtt)
                    log(f"  first subtitle segment ({len(segs)} segments): {vtt[:600]!r}")
            if name == "eventlog":
                events = json.loads(text)
                kinds = {}
                for e in events:
                    kinds[(e.get("entityType"), e.get("eventType"))] = kinds.get((e.get("entityType"), e.get("eventType")), 0) + 1
                log(f"  event types: {kinds}")
        except Exception as e:
            log(f"  connected views {name}: FAILED {e}")


def main():
    check_connected_views()
    result = {}
    for key, area in AREAS.items():
        hits = []
        for term in area["search"]:
            try:
                found = qualigraf_search(term)
            except Exception as e:
                log(f"  search '{term}' failed: {e}")
                continue
            for h in found:
                h["term"] = term
            hits += found
            log(f"  {key}: '{term}' -> {len(found)} results")
        (OUT / f"{key}-search.json").write_text(json.dumps(hits, indent=1, ensure_ascii=False))
        meetings = {}
        for h in hits:
            if h.get("ag_hexkey"):
                meetings.setdefault(h["ag_hexkey"], {"date": h.get("date"), "commission": h.get("commission"), "items": set()})
                meetings[h["ag_hexkey"]]["items"].add(h.get("ap_title") or h.get("title"))
        log(f"  {key}: {len(meetings)} meetings with matching agenda items")
        result[key] = {"meetings": {k: {**v, "items": sorted(x for x in v["items"] if x)} for k, v in meetings.items()}}
        # explore the most recent meeting in depth
        recent = sorted(meetings, key=lambda k: dt.datetime.strptime(meetings[k]["date"], "%d-%m-%Y"), reverse=True)
        for ag in recent[:1]:
            try:
                explore_meeting(ag)
            except Exception as e:
                log(f"  explore {ag} failed: {e}\n{traceback.format_exc()}")
    (OUT / "council.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
    (OUT / "build.log").write_text("\n".join(log_lines))
