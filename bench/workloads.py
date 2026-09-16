"""Named workloads: a callable plus its input, defined identically on every arm.

A workload is shipped to a remote arm as **source code**, not as a pickle, so
the arms genuinely run the same lines. Each one declares `frames_per_run` so
throughput and cost-per-1000-frames mean something.

Which functions are worth benchmarking at all (measured on x86, 4K frame,
22 threads, opencv-python 5.0.0.93 -- research/FINDINGS.md 2.3):

    findContours (RETR_LIST)          607.16 ms
    adaptiveThreshold GAUSSIAN 31      30.54 ms
    Canny 100/200                      12.96 ms
    warpPerspective                     3.77 ms
    GaussianBlur 7x7                    1.01 ms
    resize 4K->720p INTER_LINEAR        0.35 ms

adaptiveThreshold and findContours dominate, and both are named in COOL's own
speedup claim. A single-call resize benchmark is noise; do not build a report
around one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Workload:
    """`setup_src` builds the inputs once; `run_src` is what gets timed."""

    name: str
    description: str
    setup_src: str
    run_src: str
    frames_per_run: int = 1
    params: dict[str, Any] = field(default_factory=dict)
    cool_relevant: bool = False  # does COOL's marketing name this operation?

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "frames_per_run": self.frames_per_run,
            "params": dict(self.params),
            "cool_relevant": self.cool_relevant,
        }


_SYNTHETIC_4K = """
import cv2, numpy as np
rng = np.random.default_rng(20261026)
h, w = {height}, {width}
# Structured, not pure noise: contour and threshold cost depends on real edges.
frame = np.zeros((h, w, 3), np.uint8)
frame[:] = rng.integers(40, 90, (h, w, 3), dtype=np.uint8)
for i in range(240):
    x0, y0 = int(rng.integers(0, w - 200)), int(rng.integers(0, h - 200))
    cv2.rectangle(frame, (x0, y0), (x0 + int(rng.integers(20, 200)),
                  y0 + int(rng.integers(20, 200))), (200, 200, 200), int(rng.integers(1, 4)))
for i in range(120):
    pts = rng.integers(0, min(h, w), (6, 2)).astype(np.int32)
    cv2.polylines(frame, [pts.reshape(-1, 1, 2)], False, (230, 230, 230), 2)
gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
"""


WORKLOADS: dict[str, Workload] = {
    "adaptive_threshold": Workload(
        name="adaptive_threshold",
        description="adaptiveThreshold GAUSSIAN, block 31, on a 4K greyscale frame",
        setup_src=_SYNTHETIC_4K.format(width=3840, height=2160),
        run_src="cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, "
                "cv2.THRESH_BINARY_INV, 31, 5)",
        frames_per_run=1,
        params={"resolution": "3840x2160", "block": 31},
        cool_relevant=True,
    ),
    "find_contours": Workload(
        name="find_contours",
        description="findContours RETR_LIST on a thresholded 4K frame",
        setup_src=_SYNTHETIC_4K.format(width=3840, height=2160) + """
binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 31, 5)
""",
        run_src="cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)",
        frames_per_run=1,
        params={"resolution": "3840x2160", "mode": "RETR_LIST"},
        cool_relevant=True,
    ),
    "crack_pipeline": Workload(
        name="crack_pipeline",
        description=(
            "the whole measurement pipeline on a 4K frame: adaptive threshold, "
            "morphological open, connected components, distance transform"
        ),
        setup_src=_SYNTHETIC_4K.format(width=3840, height=2160) + """
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
""",
        run_src="""
binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 31, 5)
opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
n, labels, stats, centroids = cv2.connectedComponentsWithStats(opened, 8)
dist = cv2.distanceTransform(opened, cv2.DIST_L2, 5)
""",
        frames_per_run=1,
        params={"resolution": "3840x2160"},
        cool_relevant=True,
    ),
    "resize_720p": Workload(
        name="resize_720p",
        description="resize 4K to 720p, INTER_AREA (fast: included as a control)",
        setup_src=_SYNTHETIC_4K.format(width=3840, height=2160),
        run_src="cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)",
        frames_per_run=1,
        params={"interpolation": "INTER_AREA"},
        cool_relevant=True,
    ),
    "canny_1080p": Workload(
        name="canny_1080p",
        description="Canny 100/200 on a 1080p frame",
        setup_src=_SYNTHETIC_4K.format(width=1920, height=1080),
        run_src="cv2.Canny(gray, 100, 200)",
        frames_per_run=1,
        params={"resolution": "1920x1080"},
    ),
    "gaussian_blur": Workload(
        name="gaussian_blur",
        description="GaussianBlur 7x7 on a 4K frame (control: dominated by memory bandwidth)",
        setup_src=_SYNTHETIC_4K.format(width=3840, height=2160),
        run_src="cv2.GaussianBlur(frame, (7, 7), 0)",
        frames_per_run=1,
        cool_relevant=True,
    ),
}


def get_workload(name: str) -> Workload:
    try:
        return WORKLOADS[name]
    except KeyError:
        raise KeyError(
            f"unknown workload {name!r}; known: {', '.join(sorted(WORKLOADS))}"
        ) from None


def register(workload: Workload) -> Workload:
    """Products add their own workloads: `register(Workload(...))` before running."""
    WORKLOADS[workload.name] = workload
    return workload
