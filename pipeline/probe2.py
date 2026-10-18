"""Second probe round: locate download links and service layers found missing in round one."""
import re
import sys
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/probe2")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}


def get(url, limit=30_000_000):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read(limit).decode("utf-8", "replace")


def probe(name, url, pattern, keep=4000):
    try:
        status, text = get(url)
        found = sorted(set(re.findall(pattern, text)))
        (OUT / f"{name}.txt").write_text(f"{status} {len(text)} chars\n{url}\n\nMATCHES ({len(found)}):\n" + "\n".join(found[:600]) + "\n\nHEAD:\n" + text[:keep])
        print(f"OK    {name}: {len(found)} matches")
    except Exception as e:
        (OUT / f"{name}.txt").write_text(f"ERROR {e}\n{url}\n")
        print(f"FAIL  {name}: {e}")


HREF = r'href="([^"]+)"'
probe("leefbaarometer_opendata", "https://www.leefbaarometer.nl/page/Opendata", HREF)
probe("duo_overzicht", "https://duo.nl/open_onderwijsdata/overzicht-open-data.jsp", HREF)
probe("rivm_layers", "https://data.rivm.nl/geo/alo/wms?request=GetCapabilities&service=WMS", r"<Name>([^<]+)</Name>")
probe("rivm_wfs_layers", "https://data.rivm.nl/geo/alo/wfs?request=GetCapabilities&service=WFS", r"<Name>([^<]+)</Name>")
for name, url in {
    "pdok_bro_bodemkaart": "https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0?request=GetCapabilities&service=WMS",
    "pdok_bro_geotop": "https://service.pdok.nl/bzk/bro-geotop/wms/v1_0?request=GetCapabilities&service=WMS",
    "pdok_bodemdaling": "https://service.pdok.nl/rws/bodemdaling/wms/v1_0?request=GetCapabilities&service=WMS",
    "pdok_rvo_ep": "https://service.pdok.nl/rvo/energielabels/wms/v1_0?request=GetCapabilities&service=WMS",
    "pdok_cbs_vierkant100": "https://service.pdok.nl/cbs/vierkantstatistieken100m/2023/wfs/v1_0?request=GetCapabilities&service=WFS",
    "pdok_nwb": "https://service.pdok.nl/rws/nwbwegen/wfs/v1_0?request=GetCapabilities&service=WFS",
    "pdok_ov_haltes": "https://service.pdok.nl/cbs/ov-haltes/wfs/v1_0?request=GetCapabilities&service=WFS",
    "pdok_rce_monumenten": "https://service.pdok.nl/rce/beschermde-gebieden-cultuurhistorie/wfs/v1_0?request=GetCapabilities&service=WFS",
    "pdok_rijksmonumenten": "https://service.pdok.nl/rce/ps-ch/wfs/v1_0?request=GetCapabilities&service=WFS",
    "pdok_risicokaart": "https://service.pdok.nl/rws/risicokaart/wms/v1_0?request=GetCapabilities&service=WMS",
    "pdok_ruimtelijke_plannen": "https://service.pdok.nl/kadaster/plu/wms/v1_0?request=GetCapabilities&service=WMS",
    "pdok_leefbaarometer": "https://service.pdok.nl/bzk/leefbaarometer/wms/v1_0?request=GetCapabilities&service=WMS",
}.items():
    probe(name, url, r"<(?:wfs:)?Name>([^<]+)</(?:wfs:)?Name>")
probe("ori_indices", "https://api.openraadsinformatie.nl/v1/elastic/_cat/indices?format=json&h=index", r'"index":"(ori_(?:leiden|alphen)[^"]*)"')
probe("ori_leiden_search", "https://api.openraadsinformatie.nl/v1/elastic/ori_leiden_*/_search?q=Zamenhofstraat&size=2", r'"_index":"([^"]+)"', keep=6000)
probe("ori_alphen_search", "https://api.openraadsinformatie.nl/v1/elastic/ori_alphen*/_search?q=Rijndijk&size=2", r'"_index":"([^"]+)"', keep=6000)
probe("bodemloket_js", "https://www.bodemloket.nl/kaart", r'src="([^"]+\.js[^"]*)"')
probe("klimaateffectatlas_viewer", "https://www.klimaateffectatlas.nl/nl/klimaatviewer", r'(https?://[^"\' )]+)')
probe("inspectie_search", "https://www.onderwijsinspectie.nl/zoeken?keyword=open%20data", HREF)
probe("scholenopdekaart_api", "https://scholenopdekaart.nl/api/v1/zoeken/?zoekterm=Leiden", r'"([a-zA-Z]+)":', keep=3000)
