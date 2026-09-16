"""Shared fixtures. Rendering a 4K scene is slow, so the scenes are session-scoped."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from hairline.config import SurveyParams
from hairline.geometry import PlaneMap
from hairline.synth import CameraSpec, CrackSpec, RenderOptions, SceneSpec, render
from hairline.width import estimate_psf_sigma
from visioncore import calibrate_from_aruco, detect_aruco, to_gray

# Chosen against the fixture's own geometry rather than picked to look good. At
# 240 mm the resolution floor is 0.38 mm, so these four are all measurable there and
# the two finest are correctly refused from 380 mm, which is the pair of tests below.
KNOWN_WIDTHS = (0.45, 0.85, 1.60, 0.60)

# Laid out for a close-up: at a 200 mm stand-off on a 4K frame the field of view is
# about 256 by 144 mm, which is what an inspector actually photographs a hairline
# crack from. Hairline's own resolution floor puts the finest measurable crack at
# about 0.20 mm there, and at about 0.47 mm from 350 mm back, so a fixture shot from
# arm's length would refuse half of these by design rather than by failure.
PATHS = (
    ((-74.0, -58.0), (-66.0, -6.0), (-58.0, 52.0)),
    ((-16.0, -60.0), (-10.0, -4.0), (-2.0, 54.0)),
    ((44.0, -60.0), (52.0, -2.0), (60.0, 52.0)),
    ((104.0, -56.0), (110.0, -4.0), (116.0, 50.0)),
)


def make_scene(widths=KNOWN_WIDTHS, **overrides) -> SceneSpec:
    cracks = tuple(
        CrackSpec(points_mm=PATHS[i], width_mm=w, name=f"w{w:.2f}")
        for i, w in enumerate(widths[: len(PATHS)])
    )
    base = {
        "panel_mm": (360.0, 260.0),
        "cracks": cracks,
        "marker_mm": 30.0,
        "marker_centre_mm": (-126.0, 0.0),
        "marker_quiet_mm": 6.0,
        "surface_seed": 11,
    }
    base.update(overrides)
    return SceneSpec(**base)


CLOSE_CAMERA = CameraSpec(image_size=(3840, 2160), focal_px=3000.0, distance_mm=240.0)
ARMS_LENGTH_CAMERA = CameraSpec(image_size=(3840, 2160), focal_px=3000.0, distance_mm=380.0)


class Bench:
    """A rendered scene with everything the measurement stages need."""

    def __init__(self, scene, camera, options):
        self.image, self.truth = render(scene, camera, options)
        self.scene, self.camera, self.options = scene, camera, options
        self.params = SurveyParams()
        self.corners, self.ids = detect_aruco(self.image, self.params.marker_dictionary)
        self.params = replace(self.params, marker_length_mm=scene.marker_mm)
        self.calibration = calibrate_from_aruco(
            self.image, scene.marker_mm,
            min_marker_px=self.params.min_marker_px, max_obliquity_deg=90.0,
        )
        self.plane = (
            PlaneMap(self.calibration.homography_mm_to_px) if self.calibration.ok else None
        )
        self.sigma, self.sigma_source = estimate_psf_sigma(to_gray(self.image), self.corners)

    @property
    def scale_rel(self) -> float:
        edge = min(
            float(np.linalg.norm(self.corners[0][(i + 1) % 4] - self.corners[0][i]))
            for i in range(4)
        ) if self.corners else 1.0
        return max(
            self.params.scale_floor_rel, (self.calibration.residual_px or 0.0) / max(edge, 1.0)
        )


@pytest.fixture(scope="session")
def close_bench() -> Bench:
    """The reference geometry: 8.6 px/mm, square to the wall, a phone at arm's length."""
    return Bench(make_scene(), CLOSE_CAMERA, RenderOptions(blur_px=0.9, noise_dn=2.2))


@pytest.fixture(scope="session")
def oblique_bench() -> Bench:
    return Bench(
        make_scene(),
        CameraSpec(image_size=(3840, 2160), focal_px=3000.0, distance_mm=210.0, yaw_deg=24.0),
        RenderOptions(blur_px=1.0, noise_dn=2.4),
    )


@pytest.fixture(scope="session")
def arms_length_bench() -> Bench:
    """The same wall from 380 mm, where the finer cracks stop being measurable."""
    return Bench(make_scene(), ARMS_LENGTH_CAMERA, RenderOptions(blur_px=0.9, noise_dn=2.2))


@pytest.fixture(scope="session")
def far_bench() -> Bench:
    """Far enough that the fine cracks stop being resolvable."""
    return Bench(
        make_scene((0.08, 0.10, 0.12, 0.15)),
        CameraSpec(image_size=(3840, 2160), focal_px=3000.0, distance_mm=430.0),
        RenderOptions(blur_px=1.0, noise_dn=2.2),
    )


def measure_all(bench: Bench, params: SurveyParams | None = None) -> dict[str, object]:
    """Measure every crack in a bench and key the results by the drawn crack's name."""
    from hairline.segment import segment_cracks
    from hairline.width import measure_component

    params = params or bench.params
    seg = segment_cracks(
        bench.image, params, plane=bench.plane,
        px_per_mm=bench.calibration.px_per_mm, marker_corners=bench.corners,
    )
    out: dict[str, object] = {}
    for component in seg.components:
        name = _match(component.mask, bench.truth.crack_polyline_px)
        if name is None:
            continue
        result = measure_component(
            bench.image, component, bench.plane, params,
            sigma_px=bench.sigma, scale_rel_uncertainty=bench.scale_rel,
            working_distance_mm=bench.camera.distance_mm,
        )
        previous = out.get(name)
        if previous is None or len(getattr(result, "samples", [])) > len(
            getattr(previous, "samples", [])
        ):
            out[name] = result
    return out


def _match(mask, polylines) -> str | None:
    h, w = mask.shape[:2]
    best, best_hits = None, 0
    for name, poly in polylines.items():
        hits = 0
        for i in range(len(poly) - 1):
            steps = max(2, int(np.linalg.norm(poly[i + 1] - poly[i])))
            for t in range(steps):
                pt = poly[i] + (poly[i + 1] - poly[i]) * t / steps
                x, y = int(pt[0]), int(pt[1])
                if 0 <= x < w and 0 <= y < h and mask[y, x]:
                    hits += 1
        if hits > best_hits:
            best, best_hits = name, hits
    return best if best_hits >= 20 else None
