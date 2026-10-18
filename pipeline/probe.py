"""Probe official data sources and record what they return, so the fetchers can be written
against real responses. Writes one file per probe into the output directory."""
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out/probe")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "prospect-pipeline/0.1 (+https://github.com/bar371/prospector)"}


def get(url, limit=400_000):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.headers.get("content-type", ""), r.read(limit)


def probe(name, url, keep=20_000, links=None):
    try:
        status, ctype, body = get(url)
        text = body.decode("utf-8", "replace")
        found = sorted(set(re.findall(links, text))) if links else []
        note = f"{status} {ctype} {len(body)} bytes\n{url}\n"
        if found:
            note += "\nLINKS:\n" + "\n".join(found[:200]) + "\n"
        (OUT / f"{name}.txt").write_text(note + "\nBODY:\n" + text[:keep])
        print(f"OK    {name}: {status} {len(body)}b {len(found)} links")
    except Exception as e:  # record failures too; they are findings
        (OUT / f"{name}.txt").write_text(f"ERROR {e}\n{url}\n")
        print(f"FAIL  {name}: {e}")


LS = "https://api.pdok.nl/bzk/locatieserver/search/v3_1"
q = urllib.parse.quote
probe("ls_rijndijk_addresses",
      f"{LS}/free?q=*&fq={q('type:adres AND straatnaam:Rijndijk AND woonplaatsnaam:Hazerswoude-Rijndijk')}&rows=5&fl=*")
probe("ls_zamenhof_addresses",
      f"{LS}/free?q=*&fq={q('type:adres AND straatnaam:Zamenhofstraat AND woonplaatsnaam:Leiden')}&rows=5&fl=*")

WB = "https://service.pdok.nl/cbs/wijkenbuurten/2024/wfs/v1_0"
probe("wfs_wijkenbuurten_caps", f"{WB}?request=GetCapabilities&service=WFS", keep=60_000)
probe("wfs_buurt_sample",
      f"{WB}?service=WFS&version=2.0.0&request=GetFeature&typeNames=wijkenbuurten:buurten&outputFormat=json&count=1"
      f"&CQL_FILTER={q(chr(39).join(['buurtcode=', 'BU05460105', '']))}")
probe("wfs_buurt_sample_nofilter",
      f"{WB}?service=WFS&version=2.0.0&request=GetFeature&typeNames=buurten&outputFormat=json&count=1")

CAT = "https://opendata.cbs.nl/ODataCatalog/Tables?$format=json&$select=Identifier,Title,Modified&$filter="
probe("cbs_catalog_kwb", CAT + q("substringof('wijken en buurten',Title)"))
probe("cbs_catalog_nabijheid", CAT + q("substringof('Nabijheid voorzieningen',Title)"))
probe("cbs_catalog_prijzen", CAT + q("substringof('Bestaande koopwoningen',Title)"))
DCAT = "https://dataderden.cbs.nl/ODataCatalog/Tables?$format=json&$select=Identifier,Title,Modified&$filter="
probe("cbs_dataderden_misdrijven", DCAT + q("substringof('misdrijven',Title)"))

probe("leefbaarometer_downloads", "https://www.leefbaarometer.nl/page/Open%20data",
      links=r'href="([^"]+\.(?:zip|csv|xlsx|gpkg)[^"]*)"')
probe("leefbaarometer_home", "https://www.leefbaarometer.nl/", links=r'href="([^"]+)"')
probe("duo_po_vestigingen",
      "https://duo.nl/open_onderwijsdata/primair-onderwijs/scholen-en-adressen/vestigingen-basisonderwijs.jsp",
      links=r'href="([^"]+\.(?:csv|xlsx)[^"]*)"')
probe("duo_vo_vestigingen",
      "https://duo.nl/open_onderwijsdata/voortgezet-onderwijs/adressen/vestigingen.jsp",
      links=r'href="([^"]+\.(?:csv|xlsx)[^"]*)"')
probe("duo_po_doorstroomtoets",
      "https://duo.nl/open_onderwijsdata/primair-onderwijs/leerlingen/doorstroomtoetsen.jsp",
      links=r'href="([^"]+\.(?:csv|xlsx)[^"]*)"')
probe("inspectie_open_data", "https://www.onderwijsinspectie.nl/onderwerpen/open-data",
      links=r'href="([^"]+)"')
probe("bodemloket_home", "https://www.bodemloket.nl/kaart", links=r'(https?://[^"\' ]+(?:wms|WMS|arcgis|ows)[^"\' ]*)')
probe("klimaateffectatlas_home", "https://www.klimaateffectatlas.nl/nl/", links=r'(https?://[^"\' ]+(?:wms|WMS|arcgis|ows|geoserver)[^"\' ]*)')
probe("rivm_atlas_geodata", "https://data.rivm.nl/geo/alo/wms?request=GetCapabilities&service=WMS", keep=80_000)
probe("ruimtelijkeplannen", "https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/", keep=5000)
probe("openraadsinformatie",
      "https://api.openraadsinformatie.nl/v1/elastic/ori_*/_search?q=Zamenhofstraat&size=3", keep=30_000)
probe("officielebekendmakingen",
      "https://repository.overheid.nl/sru?query=" + q('cql.textAndIndexes="Zamenhofstraat" and dt.creator="Leiden"') +
      "&maximumRecords=3", keep=30_000)
probe("ep_online", "https://public.ep-online.nl/api/v5/PandEnergielabel/Adres?postcode=2312NX&huisnummer=25", keep=3000)
probe("bag_ogc", "https://api.pdok.nl/kadaster/bag/ogc/v2/collections?f=json", keep=20_000)
