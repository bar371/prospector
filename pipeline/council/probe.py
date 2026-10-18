"""Probe Leiden (Qualigraf) and Alphen aan den Rijn (iBabs + Company Webcast) for meeting video,
agenda timestamps and transcripts."""
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/council-probe")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (prospect-pipeline/0.1; +https://github.com/bar371/prospector)"}
MEDIA = r'(?:https?:)?//[^"\'\s<>]*(?:m3u8|mp4|\.vtt|\.srt|webcast|player|stream|video|ondertitel|transcri|caption|api)[^"\'\s<>]*'


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.geturl(), r.read(8_000_000).decode("utf-8", "replace")


def probe(name, url):
    try:
        final, text = get(url)
        hrefs = sorted(set(re.findall(r'(?:href|src|data-[a-z-]+)="([^"]+)"', text)))
        media = sorted(set(m.group(0) for m in re.finditer(MEDIA, text, re.I)))
        (OUT / f"{name}.html").write_text(text)
        (OUT / f"{name}.links.txt").write_text(f"{url}\nfinal: {final}\n\nMEDIA:\n" + "\n".join(media) + "\n\nLINKS:\n" + "\n".join(hrefs))
        print(f"OK   {name}: {final} {len(text)} chars, {len(hrefs)} links, {len(media)} media")
        return final, text, hrefs
    except Exception as e:
        (OUT / f"{name}.links.txt").write_text(f"ERROR {e}\n{url}\n")
        print(f"FAIL {name}: {e}")
        return url, "", []


final, _, links = probe("qualigraf_home", "https://leiden.qualigraf.nl/")
base = f"{urllib.parse.urlparse(final).scheme}://{urllib.parse.urlparse(final).netloc}"
cands = [l for l in links if re.search(r"vergader|meeting|agenda|calendar|kalender|event|webcast|video", l, re.I)]
for i, l in enumerate(cands[:6]):
    probe(f"qualigraf_{i}", urllib.parse.urljoin(base + "/", l))
for name, path in {"qualigraf_search": "/zoeken?q=Energiepark", "qualigraf_search2": "/search?q=Energiepark",
                   "qualigraf_api": "/api/", "qualigraf_calendar": "/calendar"}.items():
    probe(name, base + path)

probe("cwc_sdk", "https://sdk.companywebcast.com/sdk/player/client.js")
for name, url in {
    "cwc_webcast_page": "https://gemeentealphenaandenrijn.companywebcast.com/webcasts/20261015_1",
    "cwc_webcast_page2": "https://gemeentealphenaandenrijn.bestuurlijkeinformatie.nl/",
    "cwc_player": "https://sdk.companywebcast.com/sdk/player/?id=gemeentealphenaandenrijn/20261015_1",
}.items():
    probe(name, url)
