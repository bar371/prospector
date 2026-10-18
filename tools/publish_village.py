"""Copy village builds (pipeline/village.py output) into the app.

Usage: python tools/publish_village.py <village-build-dir>
Writes app/data/villages/<key>/{buildings.geojson,streets.json,addresses.json} and app/data/villages/index.json.
Asking prices found online (data/listings/village-<key>.json) are added to the index entry.
"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "app" / "data" / "villages"


def main(build_dir):
    index = {}
    for summary_file in sorted(Path(build_dir).glob("*/summary.json")):
        src = summary_file.parent
        summary = json.loads(summary_file.read_text())
        key = summary["key"]
        (DEST / key).mkdir(parents=True, exist_ok=True)
        for name in ("buildings.geojson", "streets.json", "addresses.json"):
            shutil.copy(src / name, DEST / key / name)
        listings_file = ROOT / "data" / "listings" / f"village-{key}.json"
        listings = json.loads(listings_file.read_text())["listings"] if listings_file.exists() else []
        for item in listings:
            item.setdefault("straat", re.sub(r"\s+\d.*$", "", item["address"]))
        index[key] = {**summary, "listings": listings}
        print(f"published {key}: {summary['adressen']} addresses, {summary['gebouwen']} buildings, {len(listings)} listings")
    (DEST / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
