"""Manual scale, exclusion regions and the edge-crack band, on frames with no marker.

Every scene here is rendered without the printed marker, so the only thing that can
put millimetres on the frame is the two-point scale. The render's own ground truth
gives the true pixels per millimetre, and the "operator's" two points are placed a
known number of pixels apart, so the right scale is exact rather than eyeballed.

The scene carries three dark lines, all drawn with known widths:

- ``inner``: a crack well inside the frame, the one the end-to-end check reads;
- ``edge``: a crack that runs off the top and bottom of the frame;
- ``ruler``: a straight line standing in for a ruler's printed edge, which is what an
  exclusion region is for.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import cv2
import numpy as np
import pytest
from hairline.cli import main as cli_main
from hairline.config import SurveyParams
from hairline.survey import (
    exclusion_polygons_px,
    gate_frame,
    manual_scale_rel_uncertainty,
    survey,
)
from hairline.synth import CameraSpec, CrackSpec, RenderOptions, SceneSpec, render
from visioncore import Frame

# 1920 x 1080 at 1500 px focal length from 200 mm: 7.5 px/mm, square to the wall,
# about 256 by 144 mm of wall in view. Small enough to render in a few seconds.
CAMERA = CameraSpec(image_size=(1920, 1080), focal_px=1500.0, distance_mm=200.0)
W, H = CAMERA.image_size

INNER = CrackSpec(points_mm=((-40.0, -50.0), (-30.0, 0.0), (-20.0, 50.0)), width_mm=1.0,
                  name="inner")
EDGE = CrackSpec(points_mm=((40.0, -95.0), (50.0, 0.0), (60.0, 95.0)), width_mm=1.2,
                 name="edge")
RULER = CrackSpec(points_mm=((-100.0, -55.0), (-100.0, 55.0)), width_mm=0.8, name="ruler")

# Two scale points 60 % of the frame width apart on a horizontal line.
SCALE_X1, SCALE_X2, SCALE_Y = 0.2, 0.8, 0.5
SPAN_PX = (SCALE_X2 - SCALE_X1) * W

# The ruler is drawn at x = -100 mm, which lands at x = 210 px (0.109 of the width).
RULER_REGION = [[0.08, 0.05], [0.14, 0.05], [0.14, 0.95], [0.08, 0.95]]


class Scene:
    def __init__(self, tmp_dir):
        scene = SceneSpec(
            panel_mm=(400.0, 300.0),
            cracks=(INNER, EDGE, RULER),
            with_marker=False,
            surface_seed=11,
        )
        self.image, self.truth = render(scene, CAMERA, RenderOptions(blur_px=0.9, noise_dn=2.2))
        self.path = tmp_dir / "markerless.png"
        cv2.imwrite(str(self.path), self.image)
        self.true_ppm = self.truth.px_per_mm_at_crack["inner"]
        self.scale_length_mm = SPAN_PX / self.true_ppm

    def params(self, **extra) -> SurveyParams:
        return SurveyParams.from_request({
            "manual_scale": [SCALE_X1, SCALE_Y, SCALE_X2, SCALE_Y, self.scale_length_mm],
            "manual_scale_max_tilt_deg": 0.0,
            **extra,
        })

    def frame(self) -> Frame:
        return Frame(index=0, timestamp_ms=0.0, image=self.image, source="test")

    def crack_on(self, result, name: str):
        """The surveyed crack whose bounding box holds the drawn line's middle."""
        poly = self.truth.crack_polyline_px[name]
        mx, my = poly[len(poly) // 2] if len(poly) % 2 else poly.mean(axis=0)
        for crack in result.cracks:
            x, y, bw, bh = crack.bbox_px
            if x - 4 <= mx <= x + bw + 4 and y - 4 <= my <= y + bh + 4:
                return crack
        return None


@pytest.fixture(scope="module")
def scene(tmp_path_factory) -> Scene:
    return Scene(tmp_path_factory.mktemp("manual_scale"))


# ---------------------------------------------------------------------------
# from_request validation
# ---------------------------------------------------------------------------


class TestFromRequest:
    def test_a_valid_request_is_parsed_into_tuples(self):
        p = SurveyParams.from_request({
            "manual_scale": ["0.1", 0.2, 0.9, 0.2, "150"],
            "manual_scale_click_px": "3",
            "manual_scale_max_tilt_deg": 7,
            "exclude_regions": [[[0, 0], [0.2, 0], [0.2, 0.3]]],
            "keep_edge_cracks": True,
            "segmentation": "blackhat",
            "blackhat_seed_sigma": "5",
        })
        assert p.manual_scale == (0.1, 0.2, 0.9, 0.2, 150.0)
        assert p.manual_scale_click_px == 3.0
        assert p.manual_scale_max_tilt_deg == 7.0
        assert p.exclude_regions == (((0.0, 0.0), (0.2, 0.0), (0.2, 0.3)),)
        assert p.keep_edge_cracks is True
        assert p.segmentation == "blackhat"
        assert p.blackhat_seed_sigma == 5.0

    def test_defaults_leave_the_published_path_alone(self):
        p = SurveyParams.from_request({})
        assert p.manual_scale is None
        assert p.exclude_regions == ()
        assert p.keep_edge_cracks is False
        assert p.segmentation == "adaptive"

    def test_to_dict_round_trips(self):
        p = SurveyParams.from_request({
            "manual_scale": [0.1, 0.2, 0.9, 0.2, 150.0],
            "exclude_regions": [[[0, 0], [0.2, 0], [0.2, 0.3]]],
            "keep_edge_cracks": True,
        })
        again = SurveyParams.from_request(json.loads(json.dumps(p.to_dict())))
        assert again.manual_scale == p.manual_scale
        assert again.exclude_regions == p.exclude_regions
        assert again.keep_edge_cracks is True

    @pytest.mark.parametrize(
        "request_, message",
        [
            ({"manual_scale": [0.1, 0.2, 0.9, 150.0]}, "x1, y1, x2, y2"),
            ({"manual_scale": [0.1, 0.2, 0.9, 0.2, 150.0, 1.0]}, "x1, y1, x2, y2"),
            ({"manual_scale": [1.2, 0.2, 0.9, 0.2, 150.0]}, "fractions"),
            ({"manual_scale": [0.1, -0.01, 0.9, 0.2, 150.0]}, "fractions"),
            ({"manual_scale": [0.1, 0.2, 0.9, 0.2, 0.0]}, "positive"),
            ({"manual_scale": [0.1, 0.2, 0.9, 0.2, -5.0]}, "positive"),
            ({"exclude_regions": [[[0, 0], [0.2, 0.2]]]}, "three points"),
            ({"segmentation": "otsu"}, "segmentation"),
        ],
    )
    def test_bad_values_are_refused(self, request_, message):
        with pytest.raises(ValueError, match=message):
            SurveyParams.from_request(request_)

    def test_fraction_bounds_are_inclusive(self):
        p = SurveyParams.from_request({"manual_scale": [0, 0, 1, 1, 10]})
        assert p.manual_scale == (0.0, 0.0, 1.0, 1.0, 10.0)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class TestManualScaleGate:
    def test_markerless_frame_takes_the_manual_scale(self, scene):
        gate, plane, corners = gate_frame(scene.frame(), scene.params(), focus_reference=0.0)
        assert gate.ok, gate.refusal
        assert gate.scale_source == "manual"
        assert gate.to_dict()["scale_source"] == "manual"
        assert corners == []
        assert plane is not None
        assert gate.px_per_mm == pytest.approx(SPAN_PX / scene.scale_length_mm, rel=1e-9)
        assert gate.px_per_mm == pytest.approx(scene.true_ppm, rel=1e-6)
        assert gate.marker_edge_px == pytest.approx(SPAN_PX)
        assert gate.apparent_tilt_deg == 0.0
        # The plane map is isotropic: 10 mm is 75 px along either axis.
        pts = plane.to_mm(np.array([[0.0, 0.0], [75.0, 0.0], [0.0, 75.0]]))
        assert np.linalg.norm(pts[1] - pts[0]) == pytest.approx(10.0, rel=1e-6)
        assert np.linalg.norm(pts[2] - pts[0]) == pytest.approx(10.0, rel=1e-6)

    def test_diagonal_points_use_the_pixel_distance(self, scene):
        params = SurveyParams.from_request({"manual_scale": [0.1, 0.1, 0.7, 0.9, 100.0]})
        gate, _, _ = gate_frame(scene.frame(), params, focus_reference=0.0)
        span = math.hypot(0.6 * W, 0.8 * H)
        assert gate.px_per_mm == pytest.approx(span / 100.0)

    def test_points_too_close_are_refused(self, scene):
        # 0.01 of 1920 px is 19 px, under the 48 px floor.
        params = SurveyParams.from_request({"manual_scale": [0.50, 0.5, 0.51, 0.5, 2.5]})
        assert 0.01 * W < params.min_marker_px
        gate, plane, _ = gate_frame(scene.frame(), params, focus_reference=0.0)
        assert not gate.ok
        assert plane is None
        assert gate.scale_source == "manual"
        assert gate.refusal.code == "SCALE_TOO_SHORT"
        assert gate.refusal.details["span_px"] == pytest.approx(0.01 * W, abs=0.1)

    def test_span_just_over_the_floor_is_accepted(self, scene):
        params = SurveyParams.from_request({"manual_scale": [0.50, 0.5, 0.53, 0.5, 7.7]})
        assert 0.03 * W >= params.min_marker_px
        gate, _, _ = gate_frame(scene.frame(), params, focus_reference=0.0)
        assert gate.ok

    def test_without_manual_scale_a_markerless_frame_is_still_refused(self, scene):
        gate, plane, _ = gate_frame(scene.frame(), SurveyParams(), focus_reference=0.0)
        assert not gate.ok
        assert plane is None
        assert gate.refusal.code == "NO_MARKER"
        assert gate.scale_source == "marker"

    def test_survey_without_manual_scale_refuses_with_no_marker(self, scene):
        result = survey(scene.path, SurveyParams())
        assert result.record.refused
        assert [r.code for r in result.record.refusals] == ["NO_MARKER"]
        assert not result.cracks
        assert "scale_source" not in result.record.metrics


class TestManualScaleUncertainty:
    def _gate(self, scene, **extra):
        params = scene.params(**extra)
        gate, _, _ = gate_frame(scene.frame(), params, focus_reference=0.0)
        return gate, params

    @pytest.mark.parametrize("click, tilt", [(2.0, 10.0), (5.0, 3.0), (1.0, 25.0)])
    def test_matches_click_plus_tilt(self, scene, click, tilt):
        gate, params = self._gate(
            scene, manual_scale_click_px=click, manual_scale_max_tilt_deg=tilt
        )
        expected = 2 * click / SPAN_PX + (1 / math.cos(math.radians(tilt)) - 1)
        assert expected > params.scale_floor_rel
        assert manual_scale_rel_uncertainty(gate, params) == pytest.approx(expected, rel=1e-12)

    def test_respects_the_floor(self, scene):
        gate, params = self._gate(
            scene, manual_scale_click_px=0.5, manual_scale_max_tilt_deg=0.0
        )
        assert 2 * 0.5 / SPAN_PX < params.scale_floor_rel
        assert manual_scale_rel_uncertainty(gate, params) == params.scale_floor_rel

    def test_a_shorter_span_costs_more(self, scene):
        params = SurveyParams.from_request({
            "manual_scale": [0.4, 0.5, 0.6, 0.5, 50.0], "manual_scale_max_tilt_deg": 0.0,
        })
        short, _, _ = gate_frame(scene.frame(), params, focus_reference=0.0)
        long_, long_params = self._gate(scene)
        assert manual_scale_rel_uncertainty(short, params) > manual_scale_rel_uncertainty(
            long_, long_params
        )


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_width_is_inside_its_expanded_uncertainty(self, scene):
        result = survey(scene.path, scene.params())
        record = result.record
        assert not record.refusals
        assert record.metrics["scale_source"] == "manual"
        assert all(g.scale_source == "manual" for g in result.gates)
        assert any("two points on a reference" in w for w in record.warnings)

        crack = scene.crack_on(result, "inner")
        assert crack is not None, [c.bbox_px for c in result.cracks]
        m = crack.measurement
        assert m.ok, m.refusal
        true = INNER.width_mm
        # The same coverage test eval/sweep.py publishes: |p50 - truth| <= expanded.
        assert abs(m.p50_mm - true) <= m.expanded_p95_mm, (m.p50_mm, m.expanded_p95_mm)
        assert m.px_per_mm_normal == pytest.approx(scene.true_ppm, rel=0.01)

    def test_a_wrong_manual_scale_moves_the_width_proportionally(self, scene):
        right = scene.crack_on(survey(scene.path, scene.params()), "inner").measurement
        # Tell it the reference is 10 % longer than it is: every width grows 10 %.
        params = replace(
            scene.params(),
            manual_scale=(SCALE_X1, SCALE_Y, SCALE_X2, SCALE_Y, scene.scale_length_mm * 1.1),
        )
        wrong = scene.crack_on(survey(scene.path, params), "inner").measurement
        assert wrong.ok
        assert wrong.p50_mm == pytest.approx(right.p50_mm * 1.1, rel=0.03)

    def test_vouched_tilt_widens_the_interval(self, scene):
        flat = scene.crack_on(survey(scene.path, scene.params()), "inner").measurement
        tilted = scene.crack_on(
            survey(scene.path, scene.params(manual_scale_max_tilt_deg=15.0)), "inner"
        ).measurement
        assert tilted.budget["scale"] > flat.budget["scale"]
        assert tilted.expanded_p95_mm > flat.expanded_p95_mm
        assert tilted.p50_mm == pytest.approx(flat.p50_mm, abs=1e-9)


class TestExcludeRegions:
    def test_polygons_convert_from_fractions_to_pixels(self):
        params = SurveyParams.from_request({"exclude_regions": [RULER_REGION]})
        (poly,) = exclusion_polygons_px(params, (H, W, 3))
        np.testing.assert_allclose(poly[0], [0.08 * W, 0.05 * H])
        np.testing.assert_allclose(poly[2], [0.14 * W, 0.95 * H])
        assert exclusion_polygons_px(SurveyParams(), (H, W)) == []

    def test_the_ruler_is_found_without_an_exclusion(self, scene):
        result = survey(scene.path, scene.params())
        assert scene.crack_on(result, "ruler") is not None

    def test_an_exclusion_removes_the_ruler_and_nothing_else(self, scene):
        before = survey(scene.path, scene.params())
        after = survey(scene.path, scene.params(exclude_regions=[RULER_REGION]))
        assert scene.crack_on(after, "ruler") is None
        assert len(after.cracks) == len(before.cracks) - 1
        inner_before = scene.crack_on(before, "inner").measurement
        inner_after = scene.crack_on(after, "inner").measurement
        assert inner_after.p50_mm == pytest.approx(inner_before.p50_mm, abs=1e-9)

    def test_exclusion_also_applies_to_blackhat_segmentation(self, scene):
        result = survey(
            scene.path, scene.params(segmentation="blackhat", exclude_regions=[RULER_REGION])
        )
        assert scene.crack_on(result, "ruler") is None


class TestKeepEdgeCracks:
    def test_a_crack_leaving_the_frame_is_refused_by_default(self, scene):
        result = survey(scene.path, scene.params())
        assert scene.crack_on(result, "edge") is None
        assert result.record.metrics["segmentation"][0]["rejected"]["touches_frame_edge"] >= 1
        assert not any("lower bounds" in w for w in result.record.warnings)

    def test_keep_edge_cracks_measures_it_and_warns(self, scene):
        result = survey(scene.path, scene.params(keep_edge_cracks=True))
        crack = scene.crack_on(result, "edge")
        assert crack is not None, [c.bbox_px for c in result.cracks]
        m = crack.measurement
        assert m.ok, m.refusal
        assert abs(m.p50_mm - EDGE.width_mm) <= m.expanded_p95_mm, (m.p50_mm, m.expanded_p95_mm)
        # Only the interior is in view, so the length can only be short.
        assert m.length_mm < scene.truth.crack_length_mm["edge"]
        assert any("lower bounds" in w for w in result.record.warnings)
        # The inner crack is unaffected by the band.
        assert scene.crack_on(result, "inner") is not None
        # The band keeps the component clear of the frame edge.
        x, y, bw, bh = crack.bbox_px
        assert y > 6 and y + bh < H - 6


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_scale_exclude_and_edge_flags_reach_the_survey(self, scene, tmp_path):
        out = tmp_path / "out"
        code = cli_main([
            "measure", str(scene.path), "--out", str(out),
            "--scale", f"{SCALE_X1},{SCALE_Y},{SCALE_X2},{SCALE_Y},{scene.scale_length_mm}",
            "--max-tilt-deg", "4",
            "--exclude", "0.08,0.05;0.14,0.05;0.14,0.95;0.08,0.95",
            "--exclude", "0.9,0.9;0.95,0.9;0.95,0.95",
            "--keep-edge-cracks",
        ])
        assert code == 0
        run = json.loads((out / "run.json").read_text())
        params = run["params"]
        assert params["manual_scale"] == pytest.approx(
            [SCALE_X1, SCALE_Y, SCALE_X2, SCALE_Y, scene.scale_length_mm]
        )
        assert params["manual_scale_max_tilt_deg"] == 4.0
        assert params["exclude_regions"] == [
            [[0.08, 0.05], [0.14, 0.05], [0.14, 0.95], [0.08, 0.95]],
            [[0.9, 0.9], [0.95, 0.9], [0.95, 0.95]],
        ]
        assert params["keep_edge_cracks"] is True
        assert run["metrics"]["scale_source"] == "manual"

    def test_a_short_scale_argument_is_rejected(self, scene, tmp_path):
        with pytest.raises(ValueError, match="x1, y1, x2, y2"):
            cli_main([
                "measure", str(scene.path), "--out", str(tmp_path / "o"),
                "--scale", "0.1,0.5,0.9,100",
            ])

    def test_no_scale_on_a_markerless_still_exits_with_a_refusal(self, scene, tmp_path):
        out = tmp_path / "o"
        assert cli_main(["measure", str(scene.path), "--out", str(out)]) == 2
        run = json.loads((out / "run.json").read_text())
        assert run["params"]["manual_scale"] is None
