"""Copy pipeline output into the app and the reports folder.

Usage: python tools/publish_data.py <report-dir> [<council-dir>]
  <report-dir>  output of pipeline/report.py (dossiers, address lists, map layers, Markdown reports)
  <council-dir> output of pipeline/council/build.py (council.json)
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DATA = ROOT / "app" / "data"
REPORTS = ROOT / "docs" / "dd"


def main(report_dir, council_dir=None):
    report = Path(report_dir)
    APP_DATA.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    for f in report.glob("*.json"):
        shutil.copy(f, APP_DATA / f.name)
    for f in report.glob("*.geojson"):
        shutil.copy(f, APP_DATA / f.name)
    for f in report.glob("*.md"):
        shutil.copy(f, REPORTS / f.name)
    if (report / "gemeenten").is_dir():
        shutil.rmtree(APP_DATA / "gemeenten", ignore_errors=True)
        shutil.copytree(report / "gemeenten", APP_DATA / "gemeenten")
    if council_dir:
        council = json.loads((Path(council_dir) / "council.json").read_text())
        for key, data in council.items():
            (APP_DATA / f"council-{key}.json").write_text(json.dumps(data, ensure_ascii=False))
    print(f"published {report} {'and ' + str(council_dir) if council_dir else ''}")


if __name__ == "__main__":
    main(*sys.argv[1:])
