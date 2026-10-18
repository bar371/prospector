"""Find where Leiden and Alphen aan den Rijn publish council meetings, agendas, video and subtitles."""
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/council-probe")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (prospect-pipeline/0.1; +https://github.com/bar371/prospector)"}
MEDIA = r'(https?:)?//[^"\'\s<>]*(?:m3u8|mp4|\.vtt|webcast|player|stream|video|ondertitel|transcript|caption)[^"\'\s<>]*'


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(5_000_000).decode("utf-8", "replace")


def probe(name, url):
    try:
        text = get(url)
        hrefs = sorted(set(re.findall(r'(?:href|src)="([^"]+)"', text)))
        media = sorted(set(m.group(0) for m in re.finditer(MEDIA, text, re.I)))
        (OUT / f"{name}.html").write_text(text)
        (OUT / f"{name}.links.txt").write_text(url + "\n\nMEDIA:\n" + "\n".join(media) + "\n\nLINKS:\n" + "\n".join(hrefs))
        print(f"OK   {name}: {len(text)} chars, {len(hrefs)} links, {len(media)} media")
        return text, hrefs
    except Exception as e:
        (OUT / f"{name}.links.txt").write_text(f"ERROR {e}\n{url}\n")
        print(f"FAIL {name}: {e}")
        return "", []


LEIDEN = "https://gemeenteraad.leiden.nl"
_, links = probe("leiden_agendas", f"{LEIDEN}/gemeenteraad/agendas-en-stukken")
_, vlinks = probe("leiden_vergaderingen", f"{LEIDEN}/actueel/vergaderingen")
meetings = [l for l in links + vlinks if re.search(r"vergadering|agenda|meeting|event", l, re.I) and l.startswith(("/", LEIDEN))]
for i, l in enumerate(meetings[:4]):
    probe(f"leiden_meeting_{i}", urllib.parse.urljoin(LEIDEN, l))
probe("leiden_search", f"{LEIDEN}/zoeken?q=Energiepark")

ALPHEN = "https://alphenaandenrijn.bestuurlijkeinformatie.nl"
_, alinks = probe("alphen_calendar", f"{ALPHEN}/Calendar")
for i, l in enumerate([l for l in alinks if l.startswith("/Agenda/Index/")][:4]):
    probe(f"alphen_agenda_{i}", ALPHEN + l)
probe("alphen_search", f"{ALPHEN}/Search?searchString=Rijndijk")
probe("alphen_search_results", f"{ALPHEN}/Search/Results?searchString=Rijndijk")
