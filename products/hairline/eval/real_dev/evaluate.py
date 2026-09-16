"""Detection metric for `segmentation="blackhat"` on the real dev photos.

No scale is needed to find a crack, so `segment_cracks` runs directly with a nominal
scale: px_per_mm is chosen so each image's expected width (labels.json, by eye) reads
as the default 0.5 mm. The mm-valued adaptive filters then keep their synthetic meaning
in units of crack width (25 mm = 50 widths long, 12 mm = 24 widths wide).

A kept component is a hit on the nearest labelled crack when at least HIT_FRACTION of
its skeleton lies within the tolerance of that polyline. The tolerance is 2% of the
image diagonal, or two expected widths if that is larger (hand-traced lines on a
227 px patch are not better than a crack width). Components inside a labelled network
region count towards that network. Everything else is a false component.

    python evaluate.py                      # frozen defaults
    python evaluate.py --set blackhat_min_contrast_sigma=2 --json out.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from hairline.config import SurveyParams
from hairline.segment import segment_cracks

HERE = Path(__file__).resolve().parent
HIT_FRACTION = 0.6
NOMINAL_WIDTH_MM = 0.5


def _poly_px(poly, w, h):
    return np.array([[x * (w - 1), y * (h - 1)] for x, y in poly], dtype=np.float64)


def evaluate(overrides: dict, labels: dict, *, verbose: bool = False) -> dict:
    root = Path(labels["root"])
    per_image = {}
    tot_labelled = tot_found = tot_false = 0
    for rel, lab in labels["images"].items():
        img = cv2.imread(str(root / rel))
        h, w = img.shape[:2]
        exp_px = float(lab["expected_px"])
        ppm = exp_px / NOMINAL_WIDTH_MM
        params = dataclasses.replace(
            SurveyParams(), segmentation="blackhat", keep_edge_cracks=True,
            expected_width_mm=NOMINAL_WIDTH_MM, **overrides,
        )
        seg = segment_cracks(img, params, px_per_mm=ppm)
        diag = float(np.hypot(w, h))
        tol = max(0.02 * diag, 2.0 * exp_px)

        dists = []
        for poly in lab.get("cracks", []):
            m = np.full((h, w), 255, np.uint8)
            cv2.polylines(m, [_poly_px(poly, w, h).astype(np.int32)], False, 0, 1)
            dists.append(cv2.distanceTransform(m, cv2.DIST_L2, 5))
        net = np.zeros((h, w), np.uint8)
        for poly in lab.get("networks", []):
            cv2.fillPoly(net, [_poly_px(poly, w, h).astype(np.int32)], 255)

        found = set()
        network_hit = False
        false = 0
        for comp in seg.components:
            pts = comp.skeleton.astype(int)
            if len(pts) == 0:
                false += 1
                continue
            xs, ys = pts[:, 0], pts[:, 1]
            best, best_frac = None, 0.0
            for i, d in enumerate(dists):
                frac = float(np.mean(d[ys, xs] <= tol))
                if frac > best_frac:
                    best, best_frac = i, frac
            if best is not None and best_frac >= HIT_FRACTION:
                found.add(best)
            elif net.any() and float(np.mean(net[ys, xs] > 0)) >= HIT_FRACTION:
                network_hit = True
            else:
                false += 1
        n_lab = len(dists) + (1 if lab.get("networks") else 0)
        n_found = len(found) + (1 if network_hit else 0)
        per_image[rel] = {
            "labelled": n_lab, "found": n_found, "false_components": false,
            "kept": len(seg.components), "rejected": seg.rejected,
        }
        tot_labelled += n_lab
        tot_found += n_found
        tot_false += false
        if verbose:
            print(f"{rel:48s} found {n_found}/{n_lab} false {false:3d} rejected {seg.rejected}")
    n = len(labels["images"])
    return {
        "overrides": overrides,
        "cracks_found": tot_found, "cracks_labelled": tot_labelled,
        "false_components": tot_false, "false_per_image": round(tot_false / n, 2),
        "per_image": per_image,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    overrides = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        overrides[k] = float(v)
    labels = json.loads((HERE / "labels.json").read_text())
    res = evaluate(overrides, labels, verbose=True)
    print(f"found {res['cracks_found']}/{res['cracks_labelled']}, "
          f"false/image {res['false_per_image']}")
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    sys.exit(main())
