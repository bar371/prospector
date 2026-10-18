"""Council intelligence for the test areas: what is said in the gemeenteraad about the neighbourhood.

Leiden (Qualigraf):
  1. search council agenda items and documents for the area's terms (street, buurt, area plans);
  2. pick the agenda items that are about the area, newest first;
  3. get each item's place in the meeting video, and the named speakers, from Qualigraf's video index;
  4. get the words spoken: Leiden's own subtitles where the meeting has them (Connected Views player),
     otherwise transcribe that part of the published MP4 with Whisper (Dutch);
  5. keep the passages that mention the area, with speaker, timestamp and a link to the video.

Alphen aan den Rijn uses iBabs with Company Webcast; see alphen() for what is collected there.

Usage: python pipeline/council/build.py <output-dir>
Environment: MAX_ITEMS (agenda items to transcribe per area, default 8), WHISPER_MODEL (default "small").
"""
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/council")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (prospect-pipeline/0.1; +https://github.com/bar371/prospector)"}
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "8"))
MAX_ITEM_SECONDS = 50 * 60  # cap per agenda item, to bound transcription time
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")

QUALIGRAF = "https://leiden.qualigraf.nl"
CV_API = "https://amcpwe-quali-cc-apim.azure-api.net/api"
CV_TENANT = "06cf837c-02ca-4956-8ee6-eecf2c3554e7"  # Leiden's public tenant in Connected Views
CV_HLS = "https://amcpwequalihls.azurewebsites.net"

AREAS = {
    "zamenhofstraat-leiden": {
        "gemeente": "Leiden",
        "search": ["Zamenhofstraat", "Noordvest", "Energiepark", "Binnenvestgracht", "Langegracht"],
        # an agenda item counts as "about the area" when its title has one of these
        "title_terms": ["zamenhof", "noordvest", "energiepark", "binnenvestgracht", "langegracht", "maredorp", "stationsgebied"],
        # a spoken passage is relevant when it has one of these
        "match": ["zamenhof", "noordvest", "energiepark", "binnenvestgracht", "binnenvest", "langegracht", "maresingel",
                  "maredorp", "reineveststeeg", "oude vest"],
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
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
            return body if binary else body.decode("utf-8", "replace")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def fetch_json(url, data=None, headers=None):
    return json.loads(fetch(url, data, headers))


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def hhmmss(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def parse_date(d):
    return dt.datetime.strptime(d, "%d-%m-%Y")


# ---------------------------------------------------------------- Leiden: search and agenda

def qualigraf_search(term):
    body = {"text": term, "where": "", "categories": [], "commissions": [], "modules": [],
            "dates": {"from": SINCE, "until": dt.date.today().strftime("%d-%m-%Y")}, "documentTypes": [], "locations": [],
            "casecode": "", "media": False, "party": [], "person": [], "onlyTitle": False, "secties": ""}
    return fetch_json(f"{QUALIGRAF}/vji/public/search/action=doSearch", json.dumps(body).encode(),
                      {"Content-Type": "application/json"})


def agenda(ag):
    return fetch_json(f"{QUALIGRAF}/vji/public/agenda/agendaview/action=detaildata/ag={ag}")


def agenda_video(ag):
    return fetch_json(f"{QUALIGRAF}/vji/public/agendavideo/action=detaildata/ag={ag}")


def find_index_entry(video, point, title):
    """The video index entry for an agenda point: match on the point's rule key, else on its title."""
    ndx = (video.get("indexdata") or {}).get("ndx") or []
    rule = point.get("ap_rule_hexkey")
    for e in ndx:
        if rule and e.get("point") == rule:
            return e
    t = (title or "").lower()[:40]
    for e in ndx:
        if t and t in (e.get("play_label") or "").lower():
            return e
    return None


# ---------------------------------------------------------------- spoken words

def vtt_text(text):
    """All cue text in a WebVTT segment."""
    lines = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        m = re.search(r"-->.*", block)
        if m:
            t = strip_tags(" ".join(block[m.end():].strip().splitlines()))
            if t:
                lines.append(t)
    return lines


def connected_views_subtitles(embed_html, start, end):
    """Subtitles for [start, end] seconds into the meeting, from the Connected Views HLS subtitle track.
    Cues are placed at their 4-second segment, which is precise enough to find the moment in the video."""
    m = re.search(r"connectedviews\.nl/embed/meeting/([0-9a-f-]{36})", embed_html)
    if not m:
        return None
    meeting = fetch_json(f"{CV_API}/FullMeeting?api-version=1.0&tenantId={CV_TENANT}&key={m.group(1)}")
    t0 = dt.datetime.fromisoformat(meeting["startDateTime"]).astimezone(dt.timezone.utc)
    room = meeting["roomLayout"]["room"]["label"]
    fmt = lambda d: urllib.parse.quote(d.strftime("%Y-%m-%dT%H:%M:%S.0000000+00:00"))
    a, b = t0 + dt.timedelta(seconds=start), t0 + dt.timedelta(seconds=end)
    url = f"{CV_HLS}/{t0:%Y-%m-%d}/{room}/subtitles/nl-pol_Live.m3u8?start={fmt(a)}&end={fmt(b)}&sourcetimestamps=1&returnType=hls"
    segments, pos, dur = [], float(start), 4.0
    for line in fetch(url).splitlines():
        if line.startswith("#EXTINF:"):
            dur = float(line.split(":")[1].split(",")[0])
        elif line and not line.startswith("#"):
            segments.append((pos, dur, urllib.parse.urljoin(url, line)))
            pos += dur
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(16) as pool:
        texts = list(pool.map(lambda seg: vtt_text(fetch(seg[2])), segments))
    cues = []
    for (pos, dur, _), lines in zip(segments, texts):
        for t in lines:
            if not cues or cues[-1]["text"] != t:  # live subtitles repeat across segments
                cues.append({"start": pos, "end": pos + dur, "text": t})
    return cues


_whisper = None


def whisper_transcribe(mp4_url, start, end):
    """Cut [start, end] seconds of audio from the published MP4 and transcribe it in Dutch."""
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "part.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(start), "-t", str(end - start), "-i", mp4_url,
                        "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True, timeout=1800)
        # Pass samples rather than a path: faster-whisper's own decoder breaks on current PyAV releases.
        import numpy as np
        import wave
        with wave.open(str(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = _whisper.transcribe(audio, language="nl", vad_filter=True, beam_size=1)
        return [{"start": start + s.start, "end": start + s.end, "text": s.text.strip()} for s in segments if s.text.strip()]


def speaker_at(index_entry, t):
    for s in (index_entry or {}).get("mb", []):
        if s.get("start", 0) <= t < s.get("end", 0) + 1:
            return s.get("name")
    return None


def passages(cues, terms, index_entry, context=2):
    """Cues that mention one of the terms, with a little context, merged when they overlap."""
    hits = [i for i, c in enumerate(cues) if any(t in c["text"].lower() for t in terms)]
    groups = []
    for i in hits:
        lo, hi = max(0, i - context), min(len(cues), i + context + 1)
        if groups and lo <= groups[-1][1]:
            groups[-1][1] = hi
        else:
            groups.append([lo, hi])
    out = []
    for lo, hi in groups:
        part = cues[lo:hi]
        text = " ".join(c["text"] for c in part)
        out.append({"start": round(part[0]["start"]), "time": hhmmss(part[0]["start"]),
                    "speaker": speaker_at(index_entry, part[0]["start"]),
                    "terms": sorted({t for t in terms if t in text.lower()}), "text": text})
    return out


# ---------------------------------------------------------------- Leiden pipeline

def leiden(key, area):
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
        log(f"  '{term}': {len(found)} results")

    documents = {}
    for h in hits:
        url = h.get("url") or ""
        doc = documents.setdefault(url or h.get("title"), {
            "date": h.get("date"), "title": strip_tags(h.get("title")), "type": h.get("type"), "module": h.get("module_name"),
            "commission": h.get("commission"), "agenda_item": strip_tags(h.get("ap_title")),
            "url": urllib.parse.urljoin(QUALIGRAF, url) if url else None,
            "snippet": strip_tags(h.get("description"))[:400], "terms": set()})
        doc["terms"].add(h["term"])
    docs = sorted(documents.values(), key=lambda d: parse_date(d["date"]), reverse=True)
    for d in docs:
        d["terms"] = sorted(d["terms"])

    # agenda items whose title is about the area, newest first
    items = {}
    for h in hits:
        title = strip_tags(h.get("ap_title"))
        if h.get("ag_hexkey") and h.get("ap_hexkey") and any(t in title.lower() for t in area["title_terms"]):
            items.setdefault((h["ag_hexkey"], h["ap_hexkey"]), {
                "ag": h["ag_hexkey"], "ap": h["ap_hexkey"], "title": title, "date": h["date"], "commission": h.get("commission")})
    items = sorted(items.values(), key=lambda i: parse_date(i["date"]), reverse=True)
    log(f"  {len(docs)} documents, {len(items)} agenda items about the area")

    fragments = []
    transcripts_dir = OUT / "transcripts" / key
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    for item in items:
        if len(fragments) >= MAX_ITEMS:
            break
        label = f"{item['date']} {item['title'][:70]}"
        try:
            ag = agenda(item["ag"])
            point = next((p for p in ag.get("points", []) if p.get("ap_hexkey") == item["ap"]), None)
            if not point or not ag.get("ag_actions", {}).get("has_vod"):
                log(f"  skip {label}: no video")
                continue
            video = agenda_video(item["ag"])
            entry = find_index_entry(video, point, item["title"])
            if not entry or entry.get("end", 0) <= entry.get("start", 0):
                log(f"  skip {label}: not in the video index")
                continue
            start = entry["start"]
            end = min(entry["end"], start + MAX_ITEM_SECONDS)
            player = video.get("player") or {}
            source, cues = None, []
            embed_path = (player.get("VIDEO_PLAYER") or {}).get("url")
            embed = fetch(urllib.parse.urljoin(QUALIGRAF, embed_path)) if embed_path else ""
            if "connectedviews" in embed:
                cues = connected_views_subtitles(embed, start, end) or []
                source = "Leiden's own subtitles"
            if not cues and player.get("VIDEO_DOWNLOAD"):
                cues = whisper_transcribe(player["VIDEO_DOWNLOAD"], start, end)
                source = f"Transcribed from the published video (Whisper {WHISPER_MODEL}, Dutch)"
            if not cues:
                log(f"  skip {label}: no subtitles and no downloadable video")
                continue
            fname = f"{parse_date(item['date']):%Y%m%d}-{item['ap'][:8]}.json"
            (transcripts_dir / fname).write_text(json.dumps(cues, ensure_ascii=False))
            speakers = []
            for s in entry.get("mb", []):
                if s.get("name") and s["name"] not in speakers:
                    speakers.append(s["name"])
            quotes = passages(cues, area["match"], entry)
            words = sum(len(c["text"].split()) for c in cues)
            if words < 150 and not quotes:
                log(f"  skip {label}: procedural ({words} words)")
                continue
            fragments.append({
                "date": item["date"], "commission": item["commission"], "agenda_item": item["title"],
                "video": f"{QUALIGRAF}/app/public/agenda/{item['ag']}/vod/{point.get('ap_rule_hexkey')}",
                "video_start": hhmmss(start), "duration_min": round((end - start) / 60), "source": source,
                "speakers": speakers, "quotes": quotes[:12], "transcript": f"transcripts/{key}/{fname}",
                "words": sum(len(c["text"].split()) for c in cues)})
            log(f"  {label}: {len(cues)} cues ({source}), {len(quotes)} passages, speakers {speakers[:6]}")
        except Exception as e:
            log(f"  {label} failed: {e}")
            log(traceback.format_exc().splitlines()[-3] if traceback.format_exc() else "")
    fragments += scan_recent_meetings(key, area, {(f["date"], f["agenda_item"]) for f in fragments})
    fragments.sort(key=lambda f: parse_date(f["date"]), reverse=True)
    return {"gemeente": area["gemeente"], "documents": docs[:60], "documents_total": len(docs),
            "agenda_items_total": len(items), "fragments": fragments}


def scan_recent_meetings(key, area, done):
    """Recent meetings with Leiden's own subtitles: search the whole meeting, so residents speaking under
    'Burger aan het woord' or police and aldermen under any agenda item are found too."""
    home = fetch_json(f"{QUALIGRAF}/vji/public/homepage/action=detaildata")
    meetings = [m for m in home.get("past_tab", []) if (m.get("video") or {}).get("has_vod")]
    found = []
    for m in meetings[:12]:
        ag = m.get("agenda_hexkey") or m.get("hexkey")
        try:
            video = agenda_video(ag)
            embed_path = ((video.get("player") or {}).get("VIDEO_PLAYER") or {}).get("url")
            embed = fetch(urllib.parse.urljoin(QUALIGRAF, embed_path)) if embed_path else ""
            if "connectedviews" not in embed:
                continue
            ndx = (video.get("indexdata") or {}).get("ndx") or []
            end = max([e.get("end", 0) for e in ndx] + [video.get("duration") or 0])
            cues = connected_views_subtitles(embed, 0, end) or []
            log(f"  scan {m['date']} {m['title']}: {len(cues)} subtitle lines")
            for e in ndx:
                title = re.sub(r"^Speel video af vanaf agendapunt (.*) om [0-9:]+$", r"\1", e.get("play_label") or "")
                part = [c for c in cues if e.get("start", 0) <= c["start"] < e.get("end", 0)]
                quotes = passages(part, area["match"], e)
                if not quotes or (m["date"], title) in done:
                    continue
                speakers = []
                for sp in e.get("mb", []):
                    if sp.get("name") and sp["name"] not in speakers:
                        speakers.append(sp["name"])
                fname = f"{parse_date(m['date']):%Y%m%d}-{(e.get('point') or 'x')[:8]}.json"
                (OUT / "transcripts" / key / fname).write_text(json.dumps(part, ensure_ascii=False))
                found.append({"date": m["date"], "commission": m["title"], "agenda_item": title,
                              "video": f"{QUALIGRAF}/app/public/agenda/{ag}/vod/{e.get('point')}",
                              "video_start": hhmmss(e.get("start", 0)), "duration_min": round((e.get("end", 0) - e.get("start", 0)) / 60),
                              "source": "Leiden's own subtitles", "speakers": speakers, "quotes": quotes[:12],
                              "transcript": f"transcripts/{key}/{fname}", "words": sum(len(c["text"].split()) for c in part)})
                log(f"    {title[:70]}: {len(quotes)} passages")
        except Exception as ex:
            log(f"  scan {m.get('date')} failed: {ex}")
    return found


# ---------------------------------------------------------------- Alphen aan den Rijn (iBabs)

def alphen():
    """Alphen aan den Rijn publishes through iBabs with Company Webcast video. Search its public portal for
    the street; the results form posts with an anti-forgery token tied to a session cookie."""
    import http.cookiejar
    base = "https://alphenaandenrijn.bestuurlijkeinformatie.nl"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = list(UA.items())
    results = []
    for term in ("Rijndijk",):
        try:
            page = opener.open(f"{base}/Search?searchString={urllib.parse.quote(term)}", timeout=60).read().decode("utf-8", "replace")
            token = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', page).group(1)
            order = re.search(r'<select[^>]*id="OrderBy"[^>]*>\s*<option[^>]*value="([^"]*)"', page)
            for pageno in range(1, 6):
                data = urllib.parse.urlencode({"Page": pageno, "Query": term, "OrderBy": order.group(1) if order else "0",
                                               "__RequestVerificationToken": token}).encode()
                req = urllib.request.Request(f"{base}/Search/Results", data=data,
                                             headers={"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"})
                body = opener.open(req, timeout=60).read().decode("utf-8", "replace")
                (OUT / "explore").mkdir(exist_ok=True)
                (OUT / "explore" / f"alphen-results-{term}-{pageno}.html").write_text(body)
                found = 0
                for m in re.finditer(r'<a[^>]+href="(/(?:Document/View|Agenda/Index|Reports/Item)[^"]+)"[^>]*>(.*?)</a>', body, re.S):
                    title = strip_tags(m.group(2))
                    if title:
                        results.append({"term": term, "url": base + html.unescape(m.group(1)), "title": title})
                        found += 1
                log(f"  Alphen '{term}' page {pageno}: {found} results ({len(body)} chars)")
                if not found:
                    break
        except Exception as e:
            log(f"  Alphen search '{term}' failed: {e}")
    seen, unique = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    return unique


def main():
    result = {}
    for key, area in AREAS.items():
        log(f"\n== {key}")
        result[key] = leiden(key, area)
    log("\n== rijndijk-hazerswoude-rijndijk (Alphen aan den Rijn)")
    result["rijndijk-hazerswoude-rijndijk"] = {"gemeente": "Alphen aan den Rijn", "documents": alphen(), "fragments": []}
    (OUT / "council.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    t0 = time.time()
    try:
        main()
    finally:
        log(f"\ndone in {time.time() - t0:.0f}s")
        (OUT / "build.log").write_text("\n".join(log_lines))
