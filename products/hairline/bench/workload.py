"""Hairline's own workload, registered into the shared harness.

The shared `bench/workloads.py` has a `crack_pipeline` entry that exercises the right
OpenCV functions on a synthetic frame of rectangles and polylines. That is a fair
microbenchmark and it is not what this product does. `hairline_frame` below is the
exact call sequence `hairline.segment.segment_cracks` makes, in the same order, with
the same parameters, on a frame that actually contains cracks on concrete.

It is written as source rather than as an import, because the harness ships the
workload to each arm over ssh and the arms have nothing installed but a pinned
`opencv-python-headless` and numpy. That is deliberate: it means the COOL arm runs
against COOL's own OpenCV inside COOL's own venv, with no wheel of ours in the way,
which is the thing the award is asking to see. The correspondence between this source
and `segment.py` is line for line and is worth checking; the alternative -- installing
the product on the COOL image -- would have put a pip-installed OpenCV next to the one
being measured.

Usage:

    from bench.workloads import WORKLOADS
    import products.hairline.bench.workload   # registers on import
    python -m bench.run hairline_frame --repeats 15
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from bench.workloads import Workload, register  # noqa: E402

# A 4K frame of concrete with four cracks and a marker, built from the same geometry
# the product's own synthetic scenes use, but written out longhand so it depends on
# nothing but cv2 and numpy.
_CONCRETE_4K = """
import cv2, numpy as np
rng = np.random.default_rng(20261026)
H, W = 2160, 3840

# Multi-octave concrete: coarse mottling plus aggregate grain. Structured noise, not
# white noise: the cost of adaptiveThreshold and findContours depends on real edges.
field = np.zeros((H, W), np.float32)
amp, total, grain = 1.0, 0.0, 26.0
for _ in range(4):
    sh, sw = max(2, int(H / grain)), max(2, int(W / grain))
    field += amp * cv2.resize(rng.standard_normal((sh, sw)).astype(np.float32), (W, H),
                              interpolation=cv2.INTER_CUBIC)
    total += amp; amp *= 0.55; grain = max(1.5, grain / 2.4)
field /= max(total, 1e-6)
field = (field - float(field.mean())) / max(float(field.std()), 1e-6)
surface = 1.0 + 0.055 * field

# Aggregate pop-out and shutter lines: the distractors the filters have to reject.
for _ in range(90):
    cv2.circle(surface, (int(rng.integers(0, W)), int(rng.integers(0, H))),
               int(rng.integers(4, 22)), float(rng.uniform(0.62, 0.88)), -1, lineType=cv2.LINE_AA)
for _ in range(3):
    y0 = int(rng.integers(0, H))
    cv2.line(surface, (0, y0), (W, y0 + int(rng.integers(-40, 40))), 0.9, 5, lineType=cv2.LINE_AA)

# Four cracks, at the pixel widths a 60 mm marker at 350 mm stand-off would give.
for pts, width in (
    ([(620, 300), (700, 900), (760, 1700), (840, 2050)], 14),
    ([(1500, 260), (1560, 980), (1640, 1780), (1700, 2060)], 7),
    ([(2450, 280), (2520, 1000), (2580, 1820)], 5),
    ([(3200, 300), (3260, 1020), (3320, 1860)], 4),
):
    cv2.polylines(surface, [np.array(pts, np.int32)], False, 0.38, width, lineType=cv2.LINE_AA)

# The printed marker card, so the frame carries the thing the detector looks for.
card = np.ones((520, 520), np.float32)
adict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
card[80:440, 80:440] = cv2.aruco.generateImageMarker(adict, 7, 360).astype(np.float32) / 255.0
surface[120:640, 120:640] = card

frame = np.clip(surface * 205.0, 0, 255).astype(np.uint8)
frame = cv2.GaussianBlur(frame, (0, 0), 0.9)
frame = np.clip(frame.astype(np.float32) + rng.normal(0, 2.2, frame.shape), 0, 255).astype(np.uint8)
gray = frame

k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
ring = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
BLOCK = 61
"""

_HAIRLINE_FRAME = """
# --- hairline.segment.segment_cracks, step for step -----------------------
# 1. the adaptive threshold's constant, measured from the surface itself
small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
local_mean = cv2.GaussianBlur(small.astype(np.float32), (15, 15), 0)
residual = small.astype(np.float32) - local_mean
mad = float(np.median(np.abs(residual - float(np.median(residual)))))
C = float(np.clip(2.4 * max(1.0, 1.4826 * mad), 3.0, 45.0))

# 2. the threshold itself  [COOL names adaptive Gaussian thresholding]
binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, BLOCK, C)

# 3. morphological cleanup
binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k3)
binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k3)

# 4. components, with the statistics the filters use
count, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)

# 5. per-component work on everything that survives the cheap filters
kept = 0
for label in range(1, count):
    x, y, bw, bh, area = (int(v) for v in stats[label])
    if area < 90:
        continue
    if max(bw, bh) / max(1.0, min(bw, bh)) < 4.0 and float(np.hypot(bw, bh)) < 1284:
        continue
    pad = 8
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(gray.shape[1], x + bw + pad), min(gray.shape[0], y + bh + pad)
    comp = (labels[y0:y1, x0:x1] == label).astype(np.uint8) * 255
    sub_gray = gray[y0:y1, x0:x1]
    surround = cv2.bitwise_and(cv2.dilate(comp, ring), cv2.bitwise_not(comp))
    if not np.any(surround):
        continue
    if float(cv2.mean(sub_gray, surround)[0]) - float(cv2.mean(sub_gray, comp)[0]) < 7.0:
        continue
    # 6. medial axis, on the mask, never on a filled contour
    dist = cv2.distanceTransform(comp, cv2.DIST_L2, 5)
    dilated = cv2.dilate(dist, np.ones((3, 3), np.uint8))
    ridge = ((dist >= dilated - 1e-6) & (dist > 0.5)).astype(np.uint8)
    if int(np.count_nonzero(ridge)) < 4:
        continue
    # 7. outline  [COOL names contour detection]
    contours, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if contours:
        best = max(contours, key=cv2.contourArea)
        cv2.arcLength(cv2.approxPolyDP(best, 1.0, True), True)
        kept += 1
"""

register(
    Workload(
        name="hairline_frame",
        description=(
            "Hairline's per-frame segmentation on a 4K concrete frame: measured "
            "adaptive-threshold constant, adaptiveThreshold GAUSSIAN block 61, "
            "morphology, connectedComponentsWithStats, per-component distance "
            "transform and findContours"
        ),
        setup_src=_CONCRETE_4K,
        run_src=_HAIRLINE_FRAME,
        frames_per_run=1,
        params={"resolution": "3840x2160", "block": 61, "cracks": 4},
        cool_relevant=True,
    )
)

register(
    Workload(
        name="hairline_threshold",
        description="just the adaptiveThreshold call from the pipeline, block 61, 4K",
        setup_src=_CONCRETE_4K,
        run_src="cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, "
                "cv2.THRESH_BINARY_INV, BLOCK, 12.0)",
        frames_per_run=1,
        params={"resolution": "3840x2160", "block": 61},
        cool_relevant=True,
    )
)

register(
    Workload(
        name="hairline_contours",
        description="findContours RETR_LIST over the whole thresholded 4K frame",
        setup_src=_CONCRETE_4K + """
binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, BLOCK, 12.0)
""",
        run_src="cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)",
        frames_per_run=1,
        params={"resolution": "3840x2160", "mode": "RETR_LIST"},
        cool_relevant=True,
    )
)
