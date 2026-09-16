"""The live capture check: tell the operator the angle before they take the shot.

`docs/design/hairline-spec.md` §9 puts this above the report screen, and it is right.
Every refusal in the report is a walk somebody has already done. A readout that says
"you are 31 degrees off square, the finest crack you can measure from here is 0.62 mm"
while the phone is still pointed at the wall prevents the trip rather than explaining
it afterwards.

This is the same code the survey uses -- the same detector, the same homography, the
same resolution arithmetic -- run on one frame with everything after segmentation
skipped. A guidance readout computed a different way from the measurement would
eventually disagree with it, and the operator would learn to ignore whichever one was
more optimistic.

It is deliberately cheap. `assess()` on a 1080p frame is a marker detection and a
handful of matrix operations: a few milliseconds, so a phone can call it per frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from visioncore import calibrate_from_aruco, detect_aruco, sharpness, to_gray

from .config import SurveyParams
from .geometry import PlaneMap
from .width import estimate_psf_sigma

__all__ = ["CaptureAdvice", "assess"]

READY, MARGINAL, NOT_READY = "ready", "marginal", "not-ready"


@dataclass
class CaptureAdvice:
    """What the operator should be told, right now, about this view."""

    state: str
    headline: str
    detail: str
    apparent_tilt_deg: float | None = None
    px_per_mm: float | None = None
    finest_measurable_mm: float | None = None
    sigma_px: float | None = None
    sharpness: float | None = None
    marker_edge_px: float | None = None
    marker_ids: tuple[int, ...] = ()

    @property
    def ready(self) -> bool:
        return self.state == READY

    def to_dict(self) -> dict[str, Any]:
        def r(v: float | None, n: int = 2) -> float | None:
            return None if v is None else round(float(v), n)

        return {
            "state": self.state,
            "headline": self.headline,
            "detail": self.detail,
            "apparent_tilt_deg": r(self.apparent_tilt_deg, 1),
            "px_per_mm": r(self.px_per_mm, 2),
            "finest_measurable_mm": r(self.finest_measurable_mm, 3),
            "sigma_px": r(self.sigma_px),
            "sharpness": r(self.sharpness, 1),
            "marker_edge_px": r(self.marker_edge_px, 1),
            "marker_ids": list(self.marker_ids),
        }


# The angle at which the readout turns green. The design spec asks for 20 degrees,
# and what Hairline reports is apparent foreshortening from the homography, which off
# the optical axis runs above the true plane tilt: docs/evaluation.md records 25
# degrees apparent for a true 10. So 20 apparent is a stricter instruction than the
# spec's 20 true, which is the right way round for guidance.
READY_TILT_DEG = 20.0


def assess(
    image: np.ndarray, params: SurveyParams | None = None, *, target_width_mm: float = 0.30
) -> CaptureAdvice:
    """Judge one viewfinder frame and say what to do about it.

    `target_width_mm` is the finest crack the operator cares about. The readout is
    green only when the current view could actually measure it, because "the angle is
    fine" is useless advice if the stand-off makes the crack unresolvable anyway.
    """
    params = params or SurveyParams()
    gray = to_gray(image)
    sharp = sharpness(gray)

    corners, ids = detect_aruco(image, params.marker_dictionary)
    if not corners:
        return CaptureAdvice(
            state=NOT_READY,
            headline="No marker in view",
            detail=(
                f"Point the camera so the whole printed {params.marker_dictionary} "
                f"card is in the frame. Without it there is no scale and no width."
            ),
            sharpness=sharp,
        )

    cal = calibrate_from_aruco(
        image,
        params.marker_length_mm,
        dictionary=params.marker_dictionary,
        min_marker_px=params.min_marker_px,
        max_residual_px=params.max_residual_px,
        max_obliquity_deg=90.0,
    )
    marker_edge = min(
        float(np.linalg.norm(corners[0][(i + 1) % 4] - corners[0][i])) for i in range(4)
    )
    if not cal.ok:
        refusal = cal.refusal
        code = refusal.code if refusal else "NO_FIDUCIAL"
        advice = {
            "MARKER_TOO_SMALL": "Move closer. The marker is too few pixels across to "
                                "set a scale worth having.",
            "TOO_OBLIQUE": "Stand square to the wall.",
            "DEGENERATE": "The marker is folded or creased. Flatten it against the surface.",
            "HIGH_RESIDUAL": "The marker is not flat. Smooth it down against the surface.",
            "INCONSISTENT_SCALE": "The markers in view are not on the same plane. Use one.",
        }.get(code, "Get the whole marker into the frame, in focus.")
        return CaptureAdvice(
            state=NOT_READY,
            headline=code.replace("_", " ").capitalize(),
            detail=advice,
            sharpness=sharp,
            marker_edge_px=marker_edge,
            marker_ids=tuple(ids),
        )

    plane = PlaneMap(cal.homography_mm_to_px)
    tilt = plane.apparent_tilt_deg(0.0, 0.0)
    sigma_px, _ = estimate_psf_sigma(gray, corners)

    # The finest crack this view could measure, from the same arithmetic the measurement
    # uses. Worst case over direction: the smaller singular value of the Jacobian is the
    # sampling density across a crack lying the least favourable way.
    singular = np.linalg.svd(plane.jacobian(0.0, 0.0), compute_uv=False)
    worst_ppm = float(singular[1])
    floor_px = max(params.min_resolved_px, params.min_width_sigma_ratio * sigma_px)
    finest = floor_px / max(worst_ppm, 1e-6)

    if tilt > params.max_apparent_tilt_deg:
        return CaptureAdvice(
            state=NOT_READY,
            headline=f"{tilt:.0f} degrees off square",
            detail=(
                "Too oblique to measure. Move round until you are facing the wall, "
                f"not looking along it. Under {READY_TILT_DEG:.0f} degrees is right."
            ),
            apparent_tilt_deg=tilt, px_per_mm=cal.px_per_mm, finest_measurable_mm=finest,
            sigma_px=sigma_px, sharpness=sharp, marker_edge_px=marker_edge,
            marker_ids=tuple(ids),
        )

    reasons: list[str] = []
    if tilt > READY_TILT_DEG:
        reasons.append(f"{tilt:.0f} degrees off square, aim for under {READY_TILT_DEG:.0f}")
    if finest > target_width_mm:
        closer = math.ceil(100.0 * finest / max(target_width_mm, 1e-6)) / 100.0
        reasons.append(
            f"from here the finest measurable crack is {finest:.2f} mm; "
            f"get about {closer:.1f} times closer for {target_width_mm:.2f} mm"
        )
    if sharp < params.min_absolute_focus * 3:
        reasons.append("hold still and let the camera focus")

    if not reasons:
        return CaptureAdvice(
            state=READY,
            headline=f"Ready, {tilt:.0f} degrees off square",
            detail=(
                f"{cal.px_per_mm:.1f} pixels per millimetre. From here Hairline can "
                f"measure a crack down to {finest:.2f} mm."
            ),
            apparent_tilt_deg=tilt, px_per_mm=cal.px_per_mm, finest_measurable_mm=finest,
            sigma_px=sigma_px, sharpness=sharp, marker_edge_px=marker_edge,
            marker_ids=tuple(ids),
        )

    return CaptureAdvice(
        state=MARGINAL,
        headline=f"Usable, {tilt:.0f} degrees off square",
        detail="; ".join(reasons).capitalize() + ".",
        apparent_tilt_deg=tilt, px_per_mm=cal.px_per_mm, finest_measurable_mm=finest,
        sigma_px=sigma_px, sharpness=sharp, marker_edge_px=marker_edge,
        marker_ids=tuple(ids),
    )
