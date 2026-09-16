"""The calibration gate: measure printed lines of known width, or publish nothing.

`docs/design/hairline-spec.md` §9 makes this a requirement rather than a nicety, and
it is the right call. Every other test in this repository checks that a piece of the
pipeline still does what it did. This one checks the only thing a user cares about:
that a line whose width is written on the card comes back with that width on it.

It runs three ways, and all three are the same code:

* `pytest tests/test_calibration_gate.py` — fails the build if the chain has drifted.
* the CI workflow, which is the same pytest run, so a drift fails publishing.
* the service at startup, which records the result and, **if it fails, refuses to
  report any width at all** and says so on every response.

That last one matters. A measuring instrument that has lost its calibration and keeps
answering is worse than one that stops, and `research/FINDINGS.md` §5.0 is the record
of what a confident wrong number from this exact pipeline looks like: 283 mm for a
0.75 mm crack.

The card is rendered, not photographed, because there is no camera here. What that
does and does not prove is in the docstring of `check()`.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from visioncore import calibrate_from_aruco, detect_aruco, to_gray

from .config import SurveyParams
from .geometry import PlaneMap
from .segment import segment_cracks
from .targets import COMPACT, TargetSpec, calibration_target
from .width import estimate_psf_sigma, measure_component

__all__ = ["CalibrationReport", "LineResult", "check", "cached_report", "TOLERANCE_PCT"]

TOLERANCE_PCT = 8.0
"""How far a measured line may sit from its printed width before the build fails.

Set from `docs/evaluation.md`: across the sweep the 95th percentile of absolute error
is 4.3 percent, so eight percent is about twice the worst routine error and will not
fire on noise. It will fire on a sign error, a lost blur correction, a scale mistake,
or a threshold that stopped finding the line's edges."""

STAND_OFF_MM = 230.0
"""Close enough that 0.40 mm resolves and 0.15 mm does not. Both are checked."""

MIN_MEASURED_LINES = 3
"""Below this the gate has proved nothing and reports a failure rather than a pass."""


@dataclass
class LineResult:
    printed_mm: float
    nominal_mm: float
    measured_mm: float | None
    expanded_mm: float | None
    error_pct: float | None
    refusal: str
    resolved_px: float | None
    expected_measurable: bool

    @property
    def ok(self) -> bool:
        """A line passes if it measured within tolerance, or was correctly declined."""
        if not self.expected_measurable:
            return self.measured_mm is None
        if self.measured_mm is None or self.error_pct is None:
            return False
        return abs(self.error_pct) <= TOLERANCE_PCT

    def to_dict(self) -> dict[str, Any]:
        return {
            "printed_mm": round(self.printed_mm, 5),
            "nominal_mm": self.nominal_mm,
            "measured_mm": None if self.measured_mm is None else round(self.measured_mm, 4),
            "expanded_mm": None if self.expanded_mm is None else round(self.expanded_mm, 4),
            "error_pct": None if self.error_pct is None else round(self.error_pct, 2),
            "refusal": self.refusal,
            "resolved_px": None if self.resolved_px is None else round(self.resolved_px, 2),
            "expected_measurable": self.expected_measurable,
            "ok": self.ok,
        }


@dataclass
class CalibrationReport:
    ok: bool
    lines: list[LineResult] = field(default_factory=list)
    scale_error_pct: float | None = None
    px_per_mm: float | None = None
    sigma_px: float | None = None
    marker_edge_px: float | None = None
    worst_error_pct: float | None = None
    tolerance_pct: float = TOLERANCE_PCT
    checked_at: str = ""
    duration_s: float = 0.0
    failure: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tolerance_pct": self.tolerance_pct,
            "worst_error_pct": None
            if self.worst_error_pct is None
            else round(self.worst_error_pct, 2),
            "scale_error_pct": None
            if self.scale_error_pct is None
            else round(self.scale_error_pct, 3),
            "px_per_mm": None if self.px_per_mm is None else round(self.px_per_mm, 3),
            "sigma_px": None if self.sigma_px is None else round(self.sigma_px, 3),
            "marker_edge_px": None
            if self.marker_edge_px is None
            else round(self.marker_edge_px, 1),
            "checked_at": self.checked_at,
            "duration_s": round(self.duration_s, 2),
            "failure": self.failure,
            "lines": [line.to_dict() for line in self.lines],
        }

    def summary(self) -> str:
        if not self.ok:
            return f"CALIBRATION FAILED: {self.failure or 'see lines'}"
        measured = [line for line in self.lines if line.measured_mm is not None]
        return (
            f"calibration ok: {len(measured)} of {len(self.lines)} lines measured, "
            f"worst error {self.worst_error_pct:.2f}% against a {self.tolerance_pct:.0f}% "
            f"tolerance"
        )


# ---------------------------------------------------------------------------


def render_card(
    spec: TargetSpec = COMPACT,
    *,
    stand_off_mm: float = STAND_OFF_MM,
    yaw_deg: float = 6.0,
    pitch_deg: float = 3.0,
    blur_px: float = 0.9,
    noise_dn: float = 2.0,
    seed: int = 7,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Photograph the printed card through the synthetic camera.

    The card is rendered at 1200 dpi and then sampled by a pinhole camera at a
    slight angle, so the pipeline has to recover the scale from the marker and undo
    the foreshortening exactly as it would on a real photograph. What is not
    simulated is a printer, an ink, or a paper.
    """
    from .synth import CameraSpec, plane_to_image_homography

    page, manifest = calibration_target(spec)
    page_w_mm, page_h_mm = spec.page_mm
    camera = CameraSpec(
        image_size=(3840, 2160),
        focal_px=3400.0,
        distance_mm=stand_off_mm,
        yaw_deg=yaw_deg,
        pitch_deg=pitch_deg,
    )
    h_mm_to_px = plane_to_image_homography(camera)
    plane = PlaneMap(h_mm_to_px)

    # The card's own coordinates run from its top-left; the camera's plane runs from
    # the centre. Subtract half the card so it sits centred in front of the lens.
    to_plane = np.array(
        [[1.0, 0.0, -page_w_mm / 2.0], [0.0, 1.0, -page_h_mm / 2.0], [0.0, 0.0, 1.0]]
    )
    page_ppm = spec.px_per_mm
    card_px = np.array([[page_ppm, 0.0, 0.0], [0.0, page_ppm, 0.0], [0.0, 0.0, 1.0]])
    warp = h_mm_to_px @ to_plane @ np.linalg.inv(card_px)

    # Prefilter before minifying, or the sampling aliases and every printed edge
    # reads sharper than any lens could deliver.
    scale = page_ppm / max(plane.px_per_mm(0.0, 0.0), 1e-6)
    source = cv2.GaussianBlur(page, (0, 0), 0.5 * scale) if scale > 1.2 else page
    image = cv2.warpPerspective(
        source, warp, camera.image_size, flags=cv2.INTER_AREA,
        borderMode=cv2.BORDER_CONSTANT, borderValue=118,
    )

    if blur_px > 0:
        image = cv2.GaussianBlur(image, (0, 0), blur_px)
    if noise_dn > 0:
        rng = np.random.default_rng(seed)
        image = np.clip(image.astype(np.float64) + rng.normal(0, noise_dn, image.shape), 0, 255)
    frame = cv2.cvtColor(np.asarray(image, dtype=np.uint8), cv2.COLOR_GRAY2BGR)

    truth = dict(manifest)
    truth["homography_mm_to_px"] = (h_mm_to_px @ to_plane).tolist()
    truth["px_per_mm_at_centre"] = plane.px_per_mm(0.0, 0.0)
    # The marker is at the card's top-left, and under a tilt the scale there is not
    # the scale at the centre. Comparing the recovered scale against the wrong point
    # reported a 2.1% error on a pipeline that was correct.
    marker_centre_card = (8.0 + spec.marker_mm / 2.0, 8.0 + spec.marker_mm / 2.0)
    truth["px_per_mm_at_marker"] = plane.px_per_mm(
        marker_centre_card[0] - page_w_mm / 2.0,
        marker_centre_card[1] - page_h_mm / 2.0,
    )
    truth["camera"] = {
        "stand_off_mm": stand_off_mm, "yaw_deg": yaw_deg, "pitch_deg": pitch_deg,
        "focal_px": camera.focal_px,
    }
    return frame, truth


def check(
    spec: TargetSpec = COMPACT,
    *,
    measured_marker_mm: float | None = None,
    **render_kwargs: Any,
) -> CalibrationReport:
    """Measure the card and compare every line against the width it was printed at.

    What this proves: that marker detection, the plane homography, segmentation, the
    perpendicular sampling, the blur correction and the unit conversion still compose
    into a millimetre that matches a millimetre. A regression in any one of them moves
    a line and fails the build.

    What it does not prove: anything about a real printer, real ink, real paper or a
    real lens. The card is rendered. `hairline sheets` writes the same card at 600 dpi
    for a person to print and photograph, and that is the check a user should run
    before trusting this on a structure.
    """
    started = time.perf_counter()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        frame, truth = render_card(spec, **render_kwargs)
    except Exception as exc:  # a broken renderer must fail the gate, not skip it
        return CalibrationReport(
            ok=False, checked_at=stamp, failure=f"could not render the card: {exc}"
        )

    # `measured_marker_mm` is what the operator typed in. Differing from the card's
    # true edge is exactly what a printer's "fit to page" does, and the gate has to
    # notice it.
    entered_marker_mm = measured_marker_mm or spec.marker_mm
    params = SurveyParams(
        marker_length_mm=entered_marker_mm,
        marker_dictionary=spec.dictionary,
        min_length_mm=20.0,
        expected_width_mm=0.5,
    )
    gray = to_gray(frame)
    corners, _ = detect_aruco(frame, spec.dictionary)
    cal = calibrate_from_aruco(
        frame, entered_marker_mm, dictionary=spec.dictionary,
        min_marker_px=params.min_marker_px, max_obliquity_deg=90.0,
    )
    if not cal.ok:
        code = cal.refusal.code if cal.refusal else "NO_FIDUCIAL"
        return CalibrationReport(
            ok=False, checked_at=stamp, duration_s=time.perf_counter() - started,
            failure=f"the marker on the calibration card could not be read: {code}",
        )

    plane = PlaneMap(cal.homography_mm_to_px)
    sigma_px, _ = estimate_psf_sigma(gray, corners)
    marker_edge = min(
        float(np.linalg.norm(corners[0][(i + 1) % 4] - corners[0][i])) for i in range(4)
    )
    scale_rel = max(params.scale_floor_rel, (cal.residual_px or 0.0) / max(marker_edge, 1.0))
    seg = segment_cracks(
        frame, params, plane=plane, px_per_mm=cal.px_per_mm, marker_corners=corners
    )

    # Each line is matched by where it is on the card, never by how wide it came out:
    # matching on width would let a drifted measurement pick the line that agrees with
    # it, which is exactly the failure this gate exists to catch.
    #
    # The plane's origin is the marker's centre, not the card's, and ArUco's object
    # points put +y upward, so card coordinates are converted rather than assumed.
    marker_centre = (
        truth["marker"]["top_left_mm"][0] + spec.marker_mm / 2.0,
        truth["marker"]["top_left_mm"][1] + spec.marker_mm / 2.0,
    )
    floor_px = max(params.min_resolved_px, params.min_width_sigma_ratio * sigma_px)
    lines: list[LineResult] = []
    for entry in truth["lines"]:
        want_x = (entry["x0_mm"] + entry["x1_mm"]) / 2.0 - marker_centre[0]
        want_y = -(entry["y_mm"] - marker_centre[1])
        best, best_dist = None, 1e9
        for component in seg.components:
            mm = plane.to_mm(np.array([component.centroid_px]))[0]
            distance = math.hypot(float(mm[0]) - want_x, float(mm[1]) - want_y)
            if distance < best_dist:
                best, best_dist = component, distance
        expected = entry["printed_mm"] * cal.px_per_mm >= floor_px
        if best is None or best_dist > 5.0:
            lines.append(
                LineResult(entry["printed_mm"], entry["nominal_mm"], None, None, None,
                           "NOT_DETECTED", None, expected)
            )
            continue
        result = measure_component(
            frame, best, plane, params,
            sigma_px=sigma_px, scale_rel_uncertainty=scale_rel,
            working_distance_mm=truth["camera"]["stand_off_mm"],
        )
        measured = result.p50_mm if result.ok else None
        error = (
            100.0 * (measured / entry["printed_mm"] - 1.0)
            if measured is not None and entry["printed_mm"] > 0
            else None
        )
        lines.append(
            LineResult(
                printed_mm=entry["printed_mm"],
                nominal_mm=entry["nominal_mm"],
                measured_mm=measured,
                expanded_mm=result.expanded_p95_mm if result.ok else None,
                error_pct=error,
                refusal="" if result.ok else (result.refusal.code if result.refusal else "?"),
                resolved_px=result.resolved_px,
                expected_measurable=expected,
            )
        )

    errors = [abs(line.error_pct) for line in lines if line.error_pct is not None]
    failed = [line for line in lines if not line.ok]
    # Without this, a card photographed from too far away passes every assertion by
    # declining everything, and a build with no working measurement at all would ship.
    measured = [line for line in lines if line.measured_mm is not None]
    vacuous = len(measured) < MIN_MEASURED_LINES
    report = CalibrationReport(
        ok=not failed and not vacuous,
        lines=lines,
        scale_error_pct=100.0 * (cal.px_per_mm / truth["px_per_mm_at_marker"] - 1.0),
        px_per_mm=cal.px_per_mm,
        sigma_px=sigma_px,
        marker_edge_px=marker_edge,
        worst_error_pct=max(errors) if errors else None,
        checked_at=stamp,
        duration_s=time.perf_counter() - started,
        failure=(
            f"only {len(measured)} of {len(lines)} lines measured; the gate needs at "
            f"least {MIN_MEASURED_LINES} or it proves nothing"
            if vacuous and not failed
            else ""
        ) if not failed else "; ".join(
            f"{line.printed_mm:.3f} mm line "
            + (
                f"read {line.measured_mm:.3f} mm ({line.error_pct:+.1f}%)"
                if line.measured_mm is not None
                else f"was not measured ({line.refusal})"
            )
            for line in failed
        ),
    )
    return report


# ---------------------------------------------------------------------------
# The service's copy
# ---------------------------------------------------------------------------

CACHE = Path(os.environ.get("HAIRLINE_CALIBRATION_CACHE", "/tmp/hairline-calibration.json"))
_REPORT: CalibrationReport | None = None


def cached_report(refresh: bool = False) -> CalibrationReport:
    """The gate result for this process, computed once.

    Deliberately not lazy-on-first-request: the service runs it at startup so that a
    drifted build refuses from its first response rather than from its second.
    """
    global _REPORT
    if _REPORT is not None and not refresh:
        return _REPORT
    if CACHE.is_file() and not refresh:
        try:
            payload = json.loads(CACHE.read_text(encoding="utf-8"))
            _REPORT = CalibrationReport(
                ok=bool(payload["ok"]),
                worst_error_pct=payload.get("worst_error_pct"),
                scale_error_pct=payload.get("scale_error_pct"),
                px_per_mm=payload.get("px_per_mm"),
                sigma_px=payload.get("sigma_px"),
                marker_edge_px=payload.get("marker_edge_px"),
                tolerance_pct=payload.get("tolerance_pct", TOLERANCE_PCT),
                checked_at=payload.get("checked_at", ""),
                duration_s=payload.get("duration_s", 0.0),
                failure=payload.get("failure", ""),
                lines=[
                    LineResult(
                        row["printed_mm"], row["nominal_mm"], row["measured_mm"],
                        row.get("expanded_mm"), row["error_pct"], row["refusal"],
                        row.get("resolved_px"), row["expected_measurable"],
                    )
                    for row in payload.get("lines", [])
                ],
            )
            return _REPORT
        except (OSError, KeyError, ValueError, TypeError):
            pass
    _REPORT = check()
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_REPORT.to_dict(), indent=1), encoding="utf-8")
    except OSError:
        pass
    return _REPORT
