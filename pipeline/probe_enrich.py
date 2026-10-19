"""Probe the Leefbaarometer 100 m grid file: header, sample rows, id format, years."""
import io
import urllib.request
import zipfile

UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}
URL = "https://www.leefbaarometer.nl/resources/open-data-leefbaarometer-meting-2024.zip"
with urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=900) as r:
    blob = r.read()
print("zip bytes", len(blob))
z = zipfile.ZipFile(io.BytesIO(blob))
for i in z.infolist():
    print(i.filename, i.file_size)
name = next(i.filename for i in z.infolist() if "scores grids" in i.filename)
with z.open(name) as fh:
    t = io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace")
    for n, line in enumerate(t):
        if n < 6:
            print(repr(line))
        last = line
        count = n
print("rows", count, "last", repr(last))
