"""Market weights for the Prospect score: how much each theme moves home values, learned from all Dutch buurten.

Within each gemeente, buurt differences in log average WOZ value are regressed on the theme scores (with the share of
owner-occupied homes and population density as housing-stock controls). Comparing buurten within a gemeente keeps
regional price levels (Randstad versus the north) out of the weights. Coefficients are kept non-negative: a theme
that only looks negative because of what WOZ per home also measures (bigger rural homes far from shops) gets weight 0.

Usage: python pipeline/calibrate.py <gemeenten-dir> <national-buurten.json> <out.json>
"""
import glob
import json
import math
import sys
from pathlib import Path

import numpy as np

THEMES = ["veiligheid", "leefbaarheid", "bereikbaarheid", "autoluw", "voorzieningen", "rust"]


def nnls_first(X, C, y):
    """Least squares with the theme coefficients (X columns) constrained >= 0 and the controls (C) free."""
    active = list(range(X.shape[1]))
    while True:
        M = np.hstack([X[:, active], C])
        beta, *_ = np.linalg.lstsq(M, y, rcond=None)
        theme_beta = beta[:len(active)]
        if (theme_beta >= 0).all():
            full = np.zeros(X.shape[1])
            full[active] = theme_beta
            return full, beta[len(active):]
        active.pop(int(np.argmin(theme_beta)))


def main(gemeenten_dir, national_file, out_file):
    nat = {r["buurtcode"]: r for r in json.loads(Path(national_file).read_text())}
    by_gm = {}
    for f in glob.glob(f"{gemeenten_dir}/GM*.geojson"):
        for ft in json.loads(Path(f).read_text())["features"]:
            p = ft["properties"]
            n = nat.get(p["buurtcode"], {})
            scores = [p.get(f"score_{t}") for t in THEMES]
            koop, dens = n.get("percentageKoopwoningen"), n.get("bevolkingsdichtheidInwonersPerKm2")
            if p.get("woz") and None not in scores and koop is not None and dens is not None and (p.get("inwoners") or 0) >= 200:
                by_gm.setdefault(Path(f).stem, []).append((math.log(p["woz"]), scores, [koop, math.log1p(dens)]))
    Y, X, C = [], [], []
    for rows in by_gm.values():
        if len(rows) < 3:
            continue
        y = np.array([r[0] for r in rows])
        x = np.array([r[1] for r in rows], float)
        c = np.array([r[2] for r in rows], float)
        Y.append(y - y.mean()); X.append(x - x.mean(0)); C.append(c - c.mean(0))
    Y, X, C = np.concatenate(Y), np.vstack(X), np.vstack(C)
    beta, ctl = nnls_first(X, C, Y)
    fitted = X @ beta + C @ ctl
    r2 = 1 - ((Y - fitted) ** 2).sum() / (Y ** 2).sum()
    total = beta.sum()
    result = {
        "method": "Within-gemeente regression of log average WOZ per buurt on the theme scores (national percentiles), "
                  "with owner-occupied share and population density as controls; theme coefficients non-negative.",
        "buurten": int(len(Y)), "gemeenten": sum(1 for r in by_gm.values() if len(r) >= 3), "r2_within_gemeente": round(float(r2), 3),
        "weights": {t: round(float(b / total), 3) for t, b in zip(THEMES, beta)},
        "value_effect_25_to_75_pct": {t: round((math.exp(float(b) * 50) - 1) * 100, 1) for t, b in zip(THEMES, beta)},
        "source": "CBS Kerncijfers wijken en buurten 2024 (average WOZ, value date 1 January 2023); Prospect theme scores",
    }
    Path(out_file).write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
