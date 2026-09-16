"""Measure Hairline against targets whose width is known, and record where it fails.

    .venv/bin/python eval/sweep.py accuracy   --out eval/out
    .venv/bin/python eval/sweep.py refusals   --out eval/out
    .venv/bin/python eval/sweep.py plots      --out eval/out
    .venv/bin/python eval/sweep.py all        --out eval/out

Method
------
Every scene is rendered by `hairline.synth` from a pinhole camera looking at a flat
panel. The crack is drawn analytically in image space from its width in millimetres,
so the true width is exact at every viewing angle rather than being a rasterised
approximation of one. The pipeline then runs end to end -- marker detection,
calibration, segmentation, width -- and the error reported is

    (measured p50 width) - (the float we drew the crack with)

with no fitting, no per-scene tuning and no scenes dropped after the fact. Each scene
carries four cracks of different widths, and each scene is measured by all four
estimators from the same segmentation, so the estimator comparison is paired.

One axis is varied at a time from a base configuration, which is what the plots show,
and then a randomised block crosses all the axes at once, which is what the headline
bias and spread come from. The refusal sweep is separate: it builds scenes that ought
to defeat the tool and checks that it declines with the right reason instead of
returning a number.

What this cannot tell you: synthetic cracks have parallel sides, uniform darkness and
a clean surface around them. Real cracks are V-shaped in section, carry dirt and
spalled lips, and sit on concrete with form-tie holes and shutter lines. The numbers
here bound the geometry and the estimator, not the whole problem.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import itertools

from hairline.config import ESTIMATORS, SurveyParams
from hairline.geometry import PlaneMap
from hairline.segment import segment_cracks
from hairline.synth import (
    CameraSpec,
    CrackSpec,
    RenderOptions,
    SceneSpec,
    render,
)
from hairline.width import estimate_psf_sigma, measure_component
from visioncore import calibrate_from_aruco, detect_aruco, to_gray

# Four cracks per scene, well separated so none of them cross and each is its own run.
WIDTH_SETS: tuple[tuple[float, ...], ...] = (
    (0.15, 0.30, 0.60, 1.20),
    (0.20, 0.40, 0.80, 2.00),
    (0.25, 0.50, 1.00, 3.00),
    (0.10, 0.35, 0.70, 1.50),
)

CRACK_PATHS: tuple[tuple[tuple[float, float], ...], ...] = (
    ((-140.0, -70.0), (-120.0, -10.0), (-108.0, 60.0)),
    ((-40.0, -95.0), (-16.0, -20.0), (-4.0, 55.0)),
    ((60.0, -95.0), (78.0, -25.0), (96.0, 50.0)),
    ((150.0, -80.0), (168.0, -10.0), (176.0, 60.0)),
)

BASE_CAMERA = CameraSpec(
    image_size=(3840, 2160), focal_px=3000.0, distance_mm=350.0
)
BASE_RENDER = RenderOptions(blur_px=0.9, noise_dn=2.2, lighting_gradient=0.18, exposure=1.0)


CLOSE_PATHS: tuple[tuple[tuple[float, float], ...], ...] = (
    ((-58.0, -30.0), (-50.0, 0.0), (-44.0, 30.0)),
    ((-14.0, -32.0), (-8.0, -2.0), (-2.0, 30.0)),
    ((30.0, -32.0), (36.0, 0.0), (42.0, 30.0)),
    ((66.0, -30.0), (70.0, 0.0), (74.0, 28.0)),
)


def build_close_scene(widths: tuple[float, ...], *, seed: int = 11) -> SceneSpec:
    """A tighter layout for the close-up stand-offs.

    At 150 mm the field of view is under 200 mm across, so the wide-layout marker
    leaves the frame and every reading in that arm becomes NO_FIDUCIAL. That is a
    fact about the test rig rather than about the pipeline, so the close arm gets a
    scene that fits: a smaller panel, the marker near the optical axis, and the
    cracks inside 160 mm.
    """
    cracks = tuple(
        CrackSpec(points_mm=CLOSE_PATHS[i], width_mm=w, name=f"w{w:.2f}")
        for i, w in enumerate(widths[: len(CLOSE_PATHS)])
    )
    return SceneSpec(
        panel_mm=(300.0, 240.0),
        cracks=cracks,
        marker_mm=30.0,
        marker_centre_mm=(8.0, -30.0),
        marker_quiet_mm=6.0,
        surface_seed=seed,
    )


def build_scene(
    widths: tuple[float, ...], *, seed: int = 11, with_marker: bool = True
) -> SceneSpec:
    cracks = tuple(
        CrackSpec(points_mm=CRACK_PATHS[i], width_mm=w, name=f"w{w:.2f}")
        for i, w in enumerate(widths[: len(CRACK_PATHS)])
    )
    return SceneSpec(
        panel_mm=(620.0, 460.0),
        cracks=cracks,
        marker_mm=60.0,
        marker_centre_mm=(-40.0, 95.0),
        surface_seed=seed,
        with_marker=with_marker,
    )


# ---------------------------------------------------------------------------


@dataclass
class Observation:
    axis: str
    setting: str
    estimator: str
    true_mm: float
    measured_mm: float | None
    expanded_mm: float | None
    px_per_mm_normal: float | None
    resolved_px: float | None
    confidence: str
    refusal: str
    upper_bound_mm: float | None
    apparent_tilt_deg: float
    sigma_px: float
    scale_error_pct: float
    scene_seed: int

    @property
    def error_mm(self) -> float | None:
        if self.measured_mm is None:
            return None
        return self.measured_mm - self.true_mm

    @property
    def error_pct(self) -> float | None:
        if self.measured_mm is None or self.true_mm <= 0:
            return None
        return 100.0 * (self.measured_mm / self.true_mm - 1.0)

    @property
    def covered(self) -> bool | None:
        """Did the stated interval actually contain the truth? The honesty check."""
        if self.measured_mm is None or self.expanded_mm is None:
            return None
        return abs(self.measured_mm - self.true_mm) <= self.expanded_mm

    def row(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "setting": self.setting,
            "estimator": self.estimator,
            "true_mm": round(self.true_mm, 4),
            "measured_mm": None if self.measured_mm is None else round(self.measured_mm, 4),
            "error_mm": None if self.error_mm is None else round(self.error_mm, 4),
            "error_pct": None if self.error_pct is None else round(self.error_pct, 2),
            "expanded_mm": None if self.expanded_mm is None else round(self.expanded_mm, 4),
            "covered": self.covered,
            "px_per_mm_normal": None
            if self.px_per_mm_normal is None
            else round(self.px_per_mm_normal, 3),
            "resolved_px": None if self.resolved_px is None else round(self.resolved_px, 2),
            "confidence": self.confidence,
            "refusal": self.refusal,
            "upper_bound_mm": None
            if self.upper_bound_mm is None
            else round(self.upper_bound_mm, 3),
            "apparent_tilt_deg": round(self.apparent_tilt_deg, 2),
            "sigma_px": round(self.sigma_px, 3),
            "scale_error_pct": round(self.scale_error_pct, 4),
            "scene_seed": self.scene_seed,
        }


def _match_crack(component_mask: np.ndarray, polylines: dict[str, np.ndarray]) -> str | None:
    """Which drawn crack does this component cover, if the answer is unambiguous?

    Matching on a bare hit count scored two catastrophic rows in an earlier run: a
    component that was really the 1.5 mm crack got attributed to a 0.10 mm crack
    whose path happened to pass near it under a 21 degree tilt, and the sweep
    recorded a 1,436 percent measurement error that the pipeline had never made.
    So a match now has to cover a real fraction of the crack it claims to be, and
    has to beat the runner-up clearly. Anything else returns None and is counted as
    a spurious detection instead, which is a different and also worth-knowing number.
    """
    h, w = component_mask.shape[:2]
    scores: list[tuple[float, int, str]] = []
    for name, poly in polylines.items():
        dense: list[np.ndarray] = []
        for i in range(len(poly) - 1):
            steps = max(2, int(np.linalg.norm(poly[i + 1] - poly[i])))
            dense.extend(poly[i] + (poly[i + 1] - poly[i]) * t / steps for t in range(steps))
        visible = [pt for pt in dense if 0 <= int(pt[1]) < h and 0 <= int(pt[0]) < w]
        if len(visible) < 40:
            continue
        hits = sum(1 for pt in visible if component_mask[int(pt[1]), int(pt[0])])
        scores.append((hits / len(visible), hits, name))
    if not scores:
        return None
    scores.sort(reverse=True)
    fraction, hits, name = scores[0]
    # Branch splitting means one drawn crack can arrive as several components, so a
    # fragment covering a sixth of the path is a legitimate match. What is not
    # legitimate is a component that fits two drawn cracks nearly as well, or one
    # that only glances off the path: those are the shapes the 1,436 percent row had.
    if fraction < 0.15 or hits < 40:
        return None
    if len(scores) > 1 and scores[1][1] > hits / 3.0:
        return None
    return name


def measure_scene(
    scene: SceneSpec,
    camera: CameraSpec,
    options: RenderOptions,
    params: SurveyParams,
    *,
    axis: str,
    setting: str,
    estimators: tuple[str, ...] = ESTIMATORS,
) -> list[Observation]:
    """Render one scene and measure every crack in it with every estimator."""
    image, truth = render(scene, camera, options)
    gray = to_gray(image)
    corners, _ = detect_aruco(image, params.marker_dictionary)
    cal = calibrate_from_aruco(
        image,
        scene.marker_mm,
        dictionary=params.marker_dictionary,
        min_marker_px=params.min_marker_px,
        max_residual_px=params.max_residual_px,
        max_obliquity_deg=90.0,
    )
    if not cal.ok:
        code = cal.refusal.code if cal.refusal else "NO_FIDUCIAL"
        return [
            Observation(
                axis, setting, est, crack.width_mm, None, None, None, None,
                "none", code, None, 0.0, 0.0, 0.0, scene.surface_seed,
            )
            for est in estimators
            for crack in scene.cracks
        ]

    plane = PlaneMap(cal.homography_mm_to_px)
    sigma_px, _ = estimate_psf_sigma(gray, corners)
    # A crack far below the frame's resolution floor leaves no mark to attribute a
    # component to, so anything overlapping its path is a different feature. Grading
    # a reading against it measures the labelling, not the pipeline: in one run a
    # component that was really a 1.5 mm crack got scored as a 1,436 percent error on
    # a 0.10 mm one. Those cracks are reported as NOT_VISIBLE and left out of the
    # error statistics, and the count is published alongside them.
    tilt = plane.apparent_tilt_deg(0.0, 0.0)
    scale_error = 100.0 * (cal.px_per_mm / truth.px_per_mm_at_marker - 1.0)
    marker_edge = (
        min(float(np.linalg.norm(corners[0][(i + 1) % 4] - corners[0][i])) for i in range(4))
        if corners
        else 1.0
    )
    scale_rel = max(params.scale_floor_rel, (cal.residual_px or 0.0) / max(marker_edge, 1.0))

    seg = segment_cracks(
        image, params, plane=plane, px_per_mm=cal.px_per_mm, marker_corners=corners
    )
    pairs = [(c, _match_crack(c.mask, truth.crack_polyline_px)) for c in seg.components]

    out: list[Observation] = []
    for estimator in estimators:
        local = replace(params, estimator=estimator, marker_length_mm=scene.marker_mm)
        found: dict[str, Observation] = {}
        for component, name in pairs:
            if name is None:
                continue
            m = measure_component(
                image, component, plane, local,
                sigma_px=sigma_px, scale_rel_uncertainty=scale_rel,
                working_distance_mm=camera.distance_mm,
            )
            true_mm = truth.crack_width_mm[name]
            obs = Observation(
                axis=axis,
                setting=setting,
                estimator=estimator,
                true_mm=true_mm,
                measured_mm=m.p50_mm,
                expanded_mm=m.expanded_p95_mm,
                px_per_mm_normal=m.px_per_mm_normal,
                resolved_px=m.resolved_px,
                confidence=m.confidence,
                refusal=m.refusal.code if m.refusal else "",
                upper_bound_mm=m.upper_bound_mm,
                apparent_tilt_deg=tilt,
                sigma_px=sigma_px,
                scale_error_pct=scale_error,
                scene_seed=scene.surface_seed,
            )
            # Keep the longest run per drawn crack if segmentation split it.
            if name not in found or len(m.samples) > (
                0 if found[name].measured_mm is None else found[name].resolved_px or 0
            ):
                found[name] = obs
        for crack in scene.cracks:
            centre = crack.polyline().mean(axis=0)
            visible_px = crack.width_mm * plane.px_per_mm(float(centre[0]), float(centre[1]))
            fallback = "NOT_VISIBLE" if visible_px < 1.6 else "NOT_DETECTED"
            observed = found.get(crack.name)
            if observed is not None and fallback == "NOT_VISIBLE":
                observed = Observation(
                    axis, setting, estimator, crack.width_mm, None, None, None, None,
                    "none", "NOT_VISIBLE", None, tilt, sigma_px, scale_error,
                    scene.surface_seed,
                )
            out.append(
                observed
                or Observation(
                    axis, setting, estimator, crack.width_mm, None, None, None, None,
                    "none", fallback, None, tilt, sigma_px, scale_error,
                    scene.surface_seed,
                )
            )
    return out


# ---------------------------------------------------------------------------
# The sweeps
# ---------------------------------------------------------------------------


def accuracy_cases() -> Iterator[tuple[str, str, SceneSpec, CameraSpec, RenderOptions]]:
    """One axis at a time from the base, then a randomised block across all of them."""
    for widths in WIDTH_SETS:
        scene = build_scene(widths)
        yield "base", "reference", scene, BASE_CAMERA, BASE_RENDER

    # 150 to 250 mm is a close-up: a phone held at arm's length against the wall,
    # which is how anyone actually photographs a hairline crack. The far end of the
    # sweep is there to show the tool declining, not to flatter it.
    for distance in (350.0, 500.0, 700.0, 1000.0):
        for widths in WIDTH_SETS[:2]:
            yield (
                "stand_off_mm",
                f"{distance:.0f}",
                build_scene(widths),
                replace(BASE_CAMERA, distance_mm=distance),
                BASE_RENDER,
            )

    # The close arm: a phone held 15 to 25 cm off the wall, which is how anyone
    # actually photographs a hairline crack, and the only geometry in which the
    # finest widths are resolvable at all.
    for distance in (150.0, 200.0, 250.0):
        for widths in ((0.10, 0.15, 0.20, 0.30), (0.25, 0.40, 0.60, 1.00)):
            yield (
                "stand_off_mm",
                f"{distance:.0f}",
                build_close_scene(widths),
                replace(BASE_CAMERA, distance_mm=distance),
                BASE_RENDER,
            )

    for yaw in (0.0, 10.0, 20.0, 30.0, 40.0, 50.0):
        for widths in WIDTH_SETS[:2]:
            yield (
                "view_angle_deg",
                f"{yaw:.0f}",
                build_scene(widths),
                replace(BASE_CAMERA, yaw_deg=yaw, pitch_deg=yaw * 0.25),
                BASE_RENDER,
            )

    for blur in (0.5, 0.9, 1.5, 2.5, 4.0):
        for widths in WIDTH_SETS[:2]:
            yield (
                "defocus_sigma_px",
                f"{blur:.1f}",
                build_scene(widths),
                BASE_CAMERA,
                replace(BASE_RENDER, blur_px=blur),
            )

    for exposure, gradient in ((0.55, 0.10), (0.75, 0.18), (1.0, 0.18), (1.25, 0.30), (1.5, 0.45)):
        for widths in WIDTH_SETS[:2]:
            yield (
                "lighting",
                f"exposure {exposure:.2f}, gradient {gradient:.2f}",
                build_scene(widths),
                BASE_CAMERA,
                replace(BASE_RENDER, exposure=exposure, lighting_gradient=gradient),
            )

    for noise in (1.0, 2.2, 4.0, 7.0):
        for widths in WIDTH_SETS[:1]:
            yield (
                "sensor_noise_dn",
                f"{noise:.1f}",
                build_scene(widths),
                BASE_CAMERA,
                replace(BASE_RENDER, noise_dn=noise),
            )

    rng = np.random.default_rng(90210)
    for i in range(28):
        widths = WIDTH_SETS[i % len(WIDTH_SETS)]
        camera = replace(
            BASE_CAMERA,
            distance_mm=float(rng.uniform(260.0, 750.0)),
            yaw_deg=float(rng.uniform(-32.0, 32.0)),
            pitch_deg=float(rng.uniform(-14.0, 14.0)),
            roll_deg=float(rng.uniform(-8.0, 8.0)),
            centre_offset_mm=(float(rng.uniform(-40, 40)), float(rng.uniform(-30, 30))),
        )
        options = replace(
            BASE_RENDER,
            blur_px=float(rng.uniform(0.6, 2.2)),
            noise_dn=float(rng.uniform(1.2, 5.0)),
            exposure=float(rng.uniform(0.7, 1.35)),
            lighting_gradient=float(rng.uniform(0.05, 0.40)),
            seed=int(rng.integers(0, 10_000)),
        )
        yield (
            "combined",
            f"random {i:02d}",
            build_scene(widths, seed=int(rng.integers(0, 10_000))),
            camera,
            options,
        )


def run_accuracy(out_dir: Path, *, estimators: tuple[str, ...] = ESTIMATORS) -> list[Observation]:
    params = SurveyParams(marker_length_mm=60.0)
    cases = list(accuracy_cases())
    results: list[Observation] = []
    started = time.time()
    for i, (axis, setting, scene, camera, options) in enumerate(cases, 1):
        results.extend(
            measure_scene(scene, camera, options, params,
                          axis=axis, setting=setting, estimators=estimators)
        )
        print(
            f"  [{i:3d}/{len(cases)}] {axis:<18} {setting:<28} "
            f"{time.time() - started:6.1f}s",
            flush=True,
        )
    _write(out_dir, "accuracy", results)
    return results


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def refusal_cases() -> Iterator[tuple[str, str, Any]]:
    """Scenes built to defeat the tool, with the refusal each one should produce."""
    from hairline.survey import survey as _unused  # noqa: F401  (import cost, not use)

    widths = (0.20, 0.40, 0.80, 2.00)
    yield (
        "no marker in frame",
        "NO_MARKER",
        (build_scene(widths, with_marker=False), BASE_CAMERA, BASE_RENDER),
    )
    yield (
        "marker too far to resolve",
        "MARKER_TOO_SMALL",
        (build_scene(widths), replace(BASE_CAMERA, distance_mm=3200.0), BASE_RENDER),
    )
    yield (
        "surface almost edge on",
        "TOO_OBLIQUE",
        (build_scene(widths), replace(BASE_CAMERA, yaw_deg=70.0), BASE_RENDER),
    )
    yield (
        "camera moving, heavy motion blur",
        "OUT_OF_FOCUS|MOTION_BLUR|NO_MARKER",
        (build_scene(widths), BASE_CAMERA, replace(BASE_RENDER, blur_px=14.0)),
    )
    yield (
        "frame badly under exposed",
        "UNDER_EXPOSED|NO_MARKER",
        (build_scene(widths), BASE_CAMERA, replace(BASE_RENDER, exposure=0.10)),
    )
    yield (
        "frame blown out",
        "OVER_EXPOSED|CLIPPED|NO_MARKER",
        (build_scene(widths), BASE_CAMERA, replace(BASE_RENDER, exposure=2.4)),
    )
    yield (
        "crack finer than the frame can resolve",
        "BELOW_RESOLUTION",
        (
            build_scene((0.08, 0.10, 0.12, 0.15)),
            replace(BASE_CAMERA, distance_mm=900.0),
            BASE_RENDER,
        ),
    )


def run_refusals(out_dir: Path) -> list[dict[str, Any]]:
    from hairline.survey import survey

    params = SurveyParams()
    rows: list[dict[str, Any]] = []
    tmp = out_dir / "frames"
    tmp.mkdir(parents=True, exist_ok=True)
    import cv2

    for name, expected, (scene, camera, options) in refusal_cases():
        image, _ = render(scene, camera, options)
        path = tmp / f"{name.replace(' ', '-').replace(',', '')}.png"
        cv2.imwrite(str(path), image)
        result = survey(path, params)
        codes = [r.code for r in result.record.refusals]
        frame_codes = list(result.record.metrics.get("frames_rejected", {}).keys())
        seen = codes + frame_codes
        measured = [c for c in result.cracks if c.measurement.ok]
        accepted = {c for c in expected.split("|")}
        ok = bool(accepted & set(seen)) and not measured
        rows.append(
            {
                "case": name,
                "expected": expected,
                "observed": ",".join(sorted(set(seen))) or "(none)",
                "cracks_measured": len(measured),
                "refused_correctly": ok,
                "message": result.record.refusals[0].message if result.record.refusals else "",
            }
        )
        print(f"  {name:<44} {'REFUSED' if ok else 'ANSWERED'}  {rows[-1]['observed']}", flush=True)
    _write_rows(out_dir, "refusals", rows)
    return rows


# ---------------------------------------------------------------------------


def _write(out_dir: Path, name: str, observations: list[Observation]) -> None:
    _write_rows(out_dir, name, [o.row() for o in observations])


def _write_rows(out_dir: Path, name: str, rows: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    if rows:
        with (out_dir / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    print(f"  wrote {out_dir / name}.json and .csv ({len(rows)} rows)")


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Bias, spread and coverage per estimator, and the resolution curve."""
    by_estimator: dict[str, dict[str, Any]] = {}
    for estimator in sorted({r["estimator"] for r in rows}):
        subset = [r for r in rows if r["estimator"] == estimator]
        errs = [r["error_pct"] for r in subset if r["error_pct"] is not None]
        abs_mm = [abs(r["error_mm"]) for r in subset if r["error_mm"] is not None]
        covered = [r["covered"] for r in subset if r["covered"] is not None]
        by_estimator[estimator] = {
            "measured": len(errs),
            "declined": sum(1 for r in subset if r["measured_mm"] is None),
            "bias_pct": round(float(np.mean(errs)), 2) if errs else None,
            "median_error_pct": round(float(np.median(errs)), 2) if errs else None,
            "spread_pct_p16_p84": (
                [round(float(np.percentile(errs, 16)), 2), round(float(np.percentile(errs, 84)), 2)]
                if errs
                else None
            ),
            "abs_error_p50_mm": round(float(np.median(abs_mm)), 4) if abs_mm else None,
            "abs_error_p95_mm": round(float(np.percentile(abs_mm, 95)), 4) if abs_mm else None,
            "interval_coverage_pct": (
                round(100.0 * sum(covered) / len(covered), 1) if covered else None
            ),
        }
    return by_estimator


def resolution_curve(rows: list[dict[str, Any]], estimator: str) -> list[dict[str, Any]]:
    """Error against how many pixels the crack actually spanned. The key plot."""
    subset = [
        r
        for r in rows
        if r["estimator"] == estimator and r["error_pct"] is not None and r["resolved_px"]
    ]
    edges = [1.5, 2.5, 3.5, 5.0, 7.0, 10.0, 15.0, 25.0, 1e9]
    out: list[dict[str, Any]] = []
    for lo, hi in itertools.pairwise(edges):
        bucket = [r for r in subset if lo <= r["resolved_px"] < hi]
        if not bucket:
            continue
        errs = [r["error_pct"] for r in bucket]
        out.append(
            {
                "resolved_px_from": lo,
                "resolved_px_to": None if hi > 1e8 else hi,
                "n": len(bucket),
                "bias_pct": round(float(np.mean(errs)), 2),
                "p16_pct": round(float(np.percentile(errs, 16)), 2),
                "p84_pct": round(float(np.percentile(errs, 84)), 2),
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=("accuracy", "refusals", "plots", "all"))
    parser.add_argument("--out", default="eval/out")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)

    if args.what in ("accuracy", "all"):
        print("accuracy sweep")
        run_accuracy(out_dir)
    if args.what in ("refusals", "all"):
        print("refusal sweep")
        run_refusals(out_dir)
    if args.what in ("plots", "all"):
        from plots import write_plots

        rows = json.loads((out_dir / "accuracy.json").read_text(encoding="utf-8"))
        summary = {
            "estimators": summarise(rows),
            "resolution_curve": {
                est: resolution_curve(rows, est) for est in sorted({r["estimator"] for r in rows})
            },
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
        print(json.dumps(summary["estimators"], indent=1))
        write_plots(rows, summary, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
