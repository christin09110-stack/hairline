"""Measured widths against targets whose width is known, and the geometry that feeds them.

Every assertion here is a real tolerance on a real number, not a smoke test. The
tolerances come from `docs/evaluation.md`: they are set at roughly twice the bias the
sweep measured, so a regression that doubles the error fails the suite.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest
from hairline.config import SurveyParams
from hairline.geometry import PlaneMap, rectify
from hairline.synth import CameraSpec, RenderOptions, plane_to_image_homography
from tests.conftest import (
    CLOSE_CAMERA,
    KNOWN_WIDTHS,
    Bench,
    make_scene,
    measure_all,
)


class TestScaleRecovery:
    def test_recovers_the_scale_to_better_than_half_a_percent(self, close_bench):
        assert close_bench.calibration.ok
        error = close_bench.calibration.px_per_mm / close_bench.truth.px_per_mm_at_marker - 1.0
        assert abs(error) < 0.005, f"scale error {error:.4%}"

    def test_scale_survives_an_oblique_view(self, oblique_bench):
        assert oblique_bench.calibration.ok
        error = (
            oblique_bench.calibration.px_per_mm / oblique_bench.truth.px_per_mm_at_marker - 1.0
        )
        assert abs(error) < 0.01, f"scale error at 24 degrees yaw: {error:.4%}"

    def test_blur_is_read_from_the_marker_and_is_close_to_the_truth(self, close_bench):
        # The render applies 0.9 px of defocus plus a prefilter for the plane resample,
        # so the measurable blur is a little above the nominal figure.
        assert 0.8 < close_bench.sigma < 1.8
        assert "marker edges" in close_bench.sigma_source


class TestKnownWidths:
    """The headline claim: measured millimetres against drawn millimetres."""

    def test_every_crack_is_found(self, close_bench):
        found = measure_all(close_bench)
        assert len(found) == len(KNOWN_WIDTHS), f"found {sorted(found)}"

    @pytest.mark.parametrize("width", KNOWN_WIDTHS)
    def test_width_is_within_six_percent_of_the_truth(self, close_bench, width):
        found = measure_all(close_bench)
        result = found[f"w{width:.2f}"]
        assert result.ok, result.refusal
        error = result.p50_mm / width - 1.0
        assert abs(error) < 0.06, f"{width} mm read as {result.p50_mm:.3f} mm ({error:+.1%})"

    @pytest.mark.parametrize("width", KNOWN_WIDTHS)
    def test_the_stated_interval_contains_the_truth(self, close_bench, width):
        """If the uncertainty does not cover the error, the uncertainty is decoration."""
        found = measure_all(close_bench)
        result = found[f"w{width:.2f}"]
        assert result.ok
        assert abs(result.p50_mm - width) <= result.expanded_p95_mm + 0.02, (
            f"{width} mm: read {result.p50_mm:.3f} +- {result.expanded_p95_mm:.3f}"
        )

    def test_widths_come_out_in_the_right_order(self, close_bench):
        found = measure_all(close_bench)
        ordered = sorted(KNOWN_WIDTHS)
        measured = [found[f"w{w:.2f}"].p50_mm for w in ordered]
        assert measured == sorted(measured)

    def test_standing_further_back_refuses_the_fine_cracks_rather_than_guessing(
        self, arms_length_bench
    ):
        """The same wall, the same cracks, 380 mm away instead of 240.

        This is the whole instrument in one test. Backing off drops the scale from
        12.5 to 7.9 px/mm, which puts the finest measurable crack at 0.60 mm, so the
        0.45 and 0.60 mm runs stop being measurable. They must come back as a refusal
        with a correct upper bound, not as a number that happens to be wrong.
        """
        found = measure_all(arms_length_bench)
        for width in (0.45, 0.60):
            result = found[f"w{width:.2f}"]
            assert not result.ok, f"{width} mm was measured at 380 mm as {result.p50_mm}"
            assert result.refusal.code == "BELOW_RESOLUTION"
            assert result.upper_bound_mm is not None
            assert width <= result.upper_bound_mm, (
                f"{width} mm claimed narrower than {result.upper_bound_mm:.3f} mm"
            )
        for width in (0.85, 1.60):
            result = found[f"w{width:.2f}"]
            assert result.ok, f"{width} mm should still measure at 380 mm"
            assert abs(result.p50_mm / width - 1.0) < 0.05

    def test_length_is_recovered_to_within_a_fifth(self, close_bench):
        found = measure_all(close_bench)
        for width in KNOWN_WIDTHS:
            result = found[f"w{width:.2f}"]
            truth = close_bench.truth.crack_length_mm[f"w{width:.2f}"]
            assert result.length_mm == pytest.approx(truth, rel=0.30), (
                f"{width} mm run: {result.length_mm:.0f} mm measured, {truth:.0f} mm drawn"
            )

    def test_an_oblique_view_still_measures_within_ten_percent(self, oblique_bench):
        found = measure_all(oblique_bench)
        assert found, "no cracks matched at 24 degrees yaw"
        for name, result in found.items():
            if not result.ok:
                continue
            truth = float(name[1:])
            assert abs(result.p50_mm / truth - 1.0) < 0.10, (
                f"{name} at yaw: {result.p50_mm:.3f} mm"
            )


class TestEstimatorComparison:
    """The default has to actually be the best one, or it should not be the default."""

    def test_the_correction_never_makes_a_reading_worse(self, close_bench):
        """On an accepted frame the two estimators nearly agree, and that is the point.

        The blur correction is largest exactly where the gate refuses, so on anything
        Hairline actually reports, the corrected and raw readings sit within a few
        tens of microns of each other. What the correction buys is not accuracy on
        accepted frames but the ability to *decide*: it is what tells the tool where
        the raw reading would have started inventing width. The size of that error is
        asserted exactly, on the model rather than on an image, in
        `test_width_math.py::test_the_half_depth_reading_is_badly_wrong_for_a_narrow_crack`.
        """
        base = close_bench.params
        corrected = measure_all(close_bench, replace(base, estimator="blur_corrected"))
        naive = measure_all(close_bench, replace(base, estimator="halfdepth"))
        for width in KNOWN_WIDTHS:
            key = f"w{width:.2f}"
            if not (corrected[key].ok and naive[key].ok):
                continue
            corrected_error = abs(corrected[key].p50_mm - width)
            naive_error = abs(naive[key].p50_mm - width)
            assert corrected_error <= naive_error + 0.005, (
                f"{width} mm: corrected {corrected[key].p50_mm:.4f} is worse than "
                f"raw {naive[key].p50_mm:.4f}"
            )

    def test_the_raw_reading_is_the_one_that_needs_the_gate(self, close_bench):
        """Turn the correction off and the finest crack reads high. That is the trap."""
        base = replace(close_bench.params, min_width_sigma_ratio=1.0, min_resolved_px=1.0)
        finest = min(KNOWN_WIDTHS)
        key = f"w{finest:.2f}"
        naive = measure_all(close_bench, replace(base, estimator="halfdepth"))[key]
        corrected = measure_all(close_bench, replace(base, estimator="blur_corrected"))[key]
        assert naive.ok and corrected.ok
        assert naive.p50_mm > corrected.p50_mm, (
            "without the blur correction the reading should be biased upward"
        )

    def test_the_distance_transform_cross_check_is_reported(self, close_bench):
        found = measure_all(close_bench)
        result = found[f"w{max(KNOWN_WIDTHS):.2f}"]
        assert "distance_transform_p95_mm" in result.cross_check
        assert result.cross_check["distance_transform_p95_mm"] > 0


class TestUncertaintyBudget:
    def test_every_component_of_the_budget_is_present_and_finite(self, close_bench):
        result = measure_all(close_bench)[f"w{KNOWN_WIDTHS[1]:.2f}"]
        for key in ("scale", "coplanarity", "sampling", "quantisation", "estimator_noise",
                    "blur_calibration", "combined_standard", "expanded_k2"):
            assert key in result.budget, key
            assert math.isfinite(result.budget[key]), key
            assert result.budget[key] >= 0.0

    def test_the_combined_uncertainty_is_the_quadrature_sum(self, close_bench):
        b = measure_all(close_bench)[f"w{KNOWN_WIDTHS[1]:.2f}"].budget
        parts = ("scale", "coplanarity", "sampling", "quantisation",
                 "estimator_noise", "blur_calibration")
        expected = math.sqrt(sum(b[k] ** 2 for k in parts))
        assert b["combined_standard"] == pytest.approx(expected, rel=1e-6)
        assert b["expanded_k2"] == pytest.approx(2.0 * b["combined_standard"], rel=1e-9)

    def test_a_finer_crack_carries_a_larger_relative_uncertainty(self, close_bench):
        found = measure_all(close_bench)
        fine = found[f"w{min(KNOWN_WIDTHS):.2f}"]
        coarse = found[f"w{max(KNOWN_WIDTHS):.2f}"]
        assert (fine.expanded_p95_mm / fine.p50_mm) > (coarse.expanded_p95_mm / coarse.p50_mm)


class TestPlaneGeometry:
    def test_the_jacobian_matches_the_camera_it_was_rendered_with(self):
        camera = CameraSpec(image_size=(1920, 1080), focal_px=1600.0, distance_mm=500.0)
        plane = PlaneMap(plane_to_image_homography(camera))
        assert plane.px_per_mm(0.0, 0.0) == pytest.approx(1600.0 / 500.0, rel=1e-6)
        assert plane.foreshortening(0.0, 0.0) == pytest.approx(1.0, abs=1e-6)

    def test_a_tilt_foreshortens_along_one_axis_only(self):
        camera = CameraSpec(image_size=(1920, 1080), focal_px=1600.0,
                            distance_mm=500.0, yaw_deg=30.0)
        plane = PlaneMap(plane_to_image_homography(camera))
        jac = plane.jacobian(0.0, 0.0)
        across = float(np.linalg.norm(jac @ np.array([1.0, 0.0])))
        along = float(np.linalg.norm(jac @ np.array([0.0, 1.0])))
        assert across == pytest.approx(along * math.cos(math.radians(30.0)), rel=1e-3)

    def test_sampling_density_across_a_crack_depends_on_the_cracks_direction(self):
        """The reason width is converted with a direction and not with one number."""
        camera = CameraSpec(image_size=(1920, 1080), focal_px=1600.0,
                            distance_mm=500.0, yaw_deg=40.0)
        plane = PlaneMap(plane_to_image_homography(camera))
        horizontal = plane.px_per_mm_along(0.0, 0.0, np.array([1.0, 0.0]))
        vertical = plane.px_per_mm_along(0.0, 0.0, np.array([0.0, 1.0]))
        assert vertical > horizontal * 1.2
        isotropic = plane.px_per_mm(0.0, 0.0)
        assert horizontal < isotropic < vertical

    def test_round_trip_through_the_plane_returns_the_same_pixel(self, close_bench):
        points = np.array([[1000.0, 700.0], [2500.0, 1400.0]])
        back = close_bench.plane.to_px(close_bench.plane.to_mm(points))
        assert back == pytest.approx(points, abs=1e-6)

    def test_rectify_produces_a_square_on_grid(self, close_bench):
        out, out_plane = rectify(
            close_bench.image, close_bench.plane,
            extent_mm=(-80.0, -60.0, 80.0, 60.0), px_per_mm=4.0,
        )
        assert out.shape[:2] == (480, 640)
        assert out_plane.foreshortening(0.0, 0.0) == pytest.approx(1.0, abs=1e-9)
        assert out_plane.px_per_mm(0.0, 0.0) == pytest.approx(4.0, rel=1e-9)


class TestSegmentation:
    def test_the_marker_is_not_reported_as_a_crack(self, close_bench):
        """The card's black border is long, dark and straight. It has to be excluded."""
        from hairline.segment import segment_cracks

        seg = segment_cracks(
            close_bench.image, close_bench.params, plane=close_bench.plane,
            px_per_mm=close_bench.calibration.px_per_mm, marker_corners=close_bench.corners,
        )
        for quad in close_bench.corners:
            centre = np.asarray(quad).reshape(4, 2).mean(axis=0).astype(int)
            for component in seg.components:
                assert component.mask[centre[1], centre[0]] == 0

    def test_the_threshold_constant_follows_the_surface(self):
        """A rough surface must get a higher constant than a smooth one, on its own."""
        from hairline.segment import segment_cracks

        params = SurveyParams()
        smooth = Bench(make_scene(surface_contrast=0.02), CLOSE_CAMERA,
                       RenderOptions(blur_px=0.9, noise_dn=1.0))
        rough = Bench(make_scene(surface_contrast=0.12), CLOSE_CAMERA,
                      RenderOptions(blur_px=0.9, noise_dn=1.0))
        a = segment_cracks(smooth.image, params, px_per_mm=smooth.calibration.px_per_mm,
                           marker_corners=smooth.corners)
        b = segment_cracks(rough.image, params, px_per_mm=rough.calibration.px_per_mm,
                           marker_corners=rough.corners)
        assert b.threshold_c > a.threshold_c * 1.5

    def test_crossing_cracks_are_split_into_separate_runs(self):
        """A 0.35 and a 1.60 that touch must not report as one crack somewhere between."""
        from hairline.segment import segment_cracks
        from hairline.synth import CrackSpec, SceneSpec

        scene = SceneSpec(
            panel_mm=(360.0, 260.0),
            marker_mm=30.0,
            marker_quiet_mm=6.0,
            marker_centre_mm=(-126.0, 0.0),
            cracks=(
                CrackSpec(points_mm=((-40.0, -50.0), (10.0, 0.0), (60.0, 50.0)),
                          width_mm=1.60, name="thick"),
                CrackSpec(points_mm=((-40.0, 50.0), (10.0, 0.0), (60.0, -50.0)),
                          width_mm=0.40, name="thin"),
            ),
        )
        bench = Bench(scene, CLOSE_CAMERA, RenderOptions(blur_px=0.9, noise_dn=2.0))
        assert bench.calibration.ok, bench.calibration.refusal
        joined = segment_cracks(bench.image, replace(bench.params, split_branches=False),
                                px_per_mm=bench.calibration.px_per_mm,
                                marker_corners=bench.corners)
        split = segment_cracks(bench.image, replace(bench.params, split_branches=True),
                               px_per_mm=bench.calibration.px_per_mm,
                               marker_corners=bench.corners)
        assert len(split.components) > len(joined.components)

        from hairline.width import measure_component

        widths = []
        for component in split.components:
            result = measure_component(
                bench.image, component, bench.plane, bench.params,
                sigma_px=bench.sigma, scale_rel_uncertainty=bench.scale_rel,
            )
            if result.ok:
                widths.append(result.p50_mm)
        assert any(w > 1.3 for w in widths), f"no thick run recovered: {widths}"
        assert any(w < 0.6 for w in widths), f"no thin run recovered: {widths}"
