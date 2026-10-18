"""Probe the JavaScript of Qualigraf (Leiden) and the Company Webcast player (Alphen) for their
public data endpoints."""
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/council-probe")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (prospect-pipeline/0.1; +https://github.com/bar371/prospector)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.geturl(), r.read(20_000_000).decode("utf-8", "replace")


def endpoints(text):
    pats = [r'["\'`](/api/[^"\'`\s]{2,120})', r'["\'`](https?://[^"\'`\s]*(?:api|rivet|graphql|webcast|media|hls|m3u8|subtit|transcri|vtt)[^"\'`\s]{0,120})',
            r'["\'`]([a-zA-Z0-9_/.-]*(?:transcri|subtit|caption|chapters|topics|speakers|agendaitems?|webcasts?)[a-zA-Z0-9_/.{}-]{0,80})["\'`]']
    found = set()
    for p in pats:
        found.update(re.findall(p, text, re.I))
    return sorted(found)


report = []
final, html = get("https://leiden.qualigraf.nl/app/public/")
(OUT / "qualigraf_index.html").write_text(html)
scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
report.append(f"QUALIGRAF {final}\nscripts: {scripts}")
for s in scripts:
    url = urllib.parse.urljoin(final, s)
    try:
        _, js = get(url)
        report.append(f"\n--- {url} ({len(js)} chars)\n" + "\n".join(endpoints(js)[:200]))
    except Exception as e:
        report.append(f"\n--- {url} ERROR {e}")

_, js = get("https://sdk.companywebcast.com/sdk/player/client.js")
(OUT / "cwc_client.js").write_text(js)
report.append("\n\nCOMPANY WEBCAST client.js\n" + "\n".join(endpoints(js)[:300]))
# The player loads more bundles; follow them.
for s in sorted(set(re.findall(r'["\'](https?://[^"\']+\.js)["\']|["\'](//[^"\']+\.js)["\']', js)))[:10]:
    url = s[0] or "https:" + s[1]
    try:
        _, more = get(url)
        report.append(f"\n--- {url} ({len(more)} chars)\n" + "\n".join(endpoints(more)[:200]))
    except Exception as e:
        report.append(f"\n--- {url} ERROR {e}")
(OUT / "endpoints.txt").write_text("\n".join(report))
print("\n".join(report)[:20000])
