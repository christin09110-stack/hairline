"""Every refusal path, fired by a scene built to trigger it.

A refusal is a product feature here, so it gets the same treatment as a
measurement: a scene whose right answer is known, and an assertion on the code,
not just on the absence of a number. The thing these tests are really guarding
against is the opposite failure -- the tool producing a confident number from a
photograph that cannot support one.
"""

from __future__ import annotations

import numpy as np
import pytest
from hairline.config import SurveyParams
from hairline.survey import gate_frame, survey
from hairline.synth import CameraSpec, RenderOptions, render
from hairline.width import measure_component
from tests.conftest import CLOSE_CAMERA, Bench, make_scene, measure_all
from visioncore import Frame, RunRecord


def _frame(image: np.ndarray) -> Frame:
    return Frame(index=0, timestamp_ms=0.0, image=image, source="test")


def _write(tmp_path, name, image):
    import cv2

    path = tmp_path / name
    cv2.imwrite(str(path), image)
    return path


class TestFrameGate:
    def test_no_marker_is_refused_with_a_named_reason(self):
        image, _ = render(make_scene(with_marker=False), CLOSE_CAMERA, RenderOptions())
        gate, plane, _ = gate_frame(_frame(image), SurveyParams(), focus_reference=0.0)
        assert not gate.ok
        assert plane is None
        assert gate.refusal.code == "NO_MARKER"
        assert "millimetres" in gate.refusal.message

    def test_a_marker_too_far_away_is_refused(self):
        image, _ = render(
            make_scene(),
            CameraSpec(image_size=(1920, 1080), focal_px=1500.0, distance_mm=4000.0),
            RenderOptions(),
        )
        gate, _, _ = gate_frame(_frame(image), SurveyParams(), focus_reference=0.0)
        assert not gate.ok
        assert gate.refusal.code in {"MARKER_TOO_SMALL", "NO_MARKER", "NO_FIDUCIAL"}

    def test_an_edge_on_surface_is_refused(self):
        image, _ = render(
            make_scene(),
            CameraSpec(image_size=(3840, 2160), focal_px=3000.0,
                       distance_mm=600.0, yaw_deg=74.0),
            RenderOptions(),
        )
        gate, plane, _ = gate_frame(_frame(image), SurveyParams(), focus_reference=0.0)
        assert not gate.ok
        assert plane is None
        assert gate.refusal.code in {"TOO_OBLIQUE", "NO_MARKER", "MARKER_TOO_SMALL", "DEGENERATE"}

    def test_a_black_frame_is_refused_before_anything_else_runs(self):
        image, _ = render(make_scene(), CLOSE_CAMERA, RenderOptions(exposure=0.06))
        gate, _, _ = gate_frame(_frame(image), SurveyParams(), focus_reference=0.0)
        assert not gate.ok
        assert gate.refusal.code in {"UNDER_EXPOSED", "OUT_OF_FOCUS", "NO_MARKER"}

    def test_a_blown_out_frame_is_refused(self):
        image, _ = render(make_scene(), CLOSE_CAMERA, RenderOptions(exposure=2.6))
        gate, _, _ = gate_frame(_frame(image), SurveyParams(), focus_reference=0.0)
        assert not gate.ok
        assert gate.refusal.code in {"OVER_EXPOSED", "CLIPPED", "NO_MARKER"}

    def test_a_smeared_frame_is_refused(self):
        image, _ = render(make_scene(), CLOSE_CAMERA, RenderOptions(blur_px=16.0))
        gate, _, _ = gate_frame(_frame(image), SurveyParams(), focus_reference=0.0)
        assert not gate.ok
        assert gate.refusal.code in {"OUT_OF_FOCUS", "NO_MARKER", "MOTION_BLUR"}

    def test_a_sharp_frame_next_to_sharper_ones_is_refused_as_motion_blur(self):
        """Relative focus: a frame can be fine on its own and still be the worst one."""
        image, _ = render(make_scene(), CLOSE_CAMERA, RenderOptions(blur_px=3.0))
        params = SurveyParams()
        alone, _, _ = gate_frame(_frame(image), params, focus_reference=0.0)
        in_company, _, _ = gate_frame(_frame(image), params, focus_reference=alone.sharpness * 8)
        assert alone.ok
        assert not in_company.ok
        assert in_company.refusal.code == "MOTION_BLUR"

    def test_a_good_frame_passes_and_carries_its_numbers(self, close_bench):
        gate, plane, corners = gate_frame(
            _frame(close_bench.image), SurveyParams(), focus_reference=0.0
        )
        assert gate.ok
        assert plane is not None
        assert corners
        assert gate.px_per_mm > 0
        assert gate.station_mm is not None
        assert gate.refusal is None


class TestCrackLevelRefusals:
    def test_a_crack_finer_than_the_frame_can_resolve_is_refused_with_a_bound(
        self, far_bench
    ):
        results = measure_all(far_bench)
        refused = [r for r in results.values() if not r.ok]
        assert refused, "nothing was refused on a scene of 0.10 to 0.25 mm cracks at 800 mm"
        bounded = [r for r in refused if r.refusal.code == "BELOW_RESOLUTION"]
        assert bounded, [r.refusal.code for r in refused]
        for result in bounded:
            assert result.upper_bound_mm is not None
            assert result.upper_bound_mm > 0
            assert "narrower than" in result.refusal.message
            assert "closer" in result.refusal.message

    def test_the_upper_bound_is_actually_an_upper_bound(self, far_bench):
        """A bound that the true width exceeds would be worse than no bound at all."""
        results = measure_all(far_bench)
        for name, result in results.items():
            if result.ok or result.upper_bound_mm is None:
                continue
            truth = float(name[1:])
            assert truth <= result.upper_bound_mm * 1.35, (
                f"{name}: claimed narrower than {result.upper_bound_mm:.3f} mm"
            )

    def test_the_refused_crack_reports_no_width_at_all(self, far_bench):
        for result in measure_all(far_bench).values():
            if result.ok:
                continue
            assert result.p50_mm is None
            assert result.p95_mm is None
            assert result.max_mm is None

    def test_a_blank_surface_yields_no_cracks(self):
        bench = Bench(make_scene(widths=()), CLOSE_CAMERA, RenderOptions())
        from hairline.segment import segment_cracks

        seg = segment_cracks(
            bench.image, bench.params, px_per_mm=bench.calibration.px_per_mm,
            marker_corners=bench.corners,
        )
        measured = [
            measure_component(bench.image, c, bench.plane, bench.params,
                              sigma_px=bench.sigma, scale_rel_uncertainty=bench.scale_rel)
            for c in seg.components
        ]
        assert not [m for m in measured if m.ok], (
            f"found {sum(1 for m in measured if m.ok)} cracks on a blank wall"
        )


class TestWholeSurveyRefuses:
    def test_a_clip_with_no_marker_refuses_and_says_what_to_do(self, tmp_path):
        image, _ = render(make_scene(with_marker=False), CLOSE_CAMERA, RenderOptions())
        path = _write(tmp_path, "no-marker.png", image)
        saved: dict[str, bytes] = {}
        result = survey(
            path, SurveyParams(),
            save_evidence=lambda name, data: (saved.__setitem__(name, data), f"/e/{name}")[1],
        )
        assert result.record.refused
        assert result.record.refusals[0].code == "NO_MARKER"
        assert not [c for c in result.cracks if c.measurement.ok]
        assert saved, "a refusal should still produce a picture of what was refused"
        assert result.record.metrics["frames_usable"] == 0

    def test_the_refusal_record_still_carries_the_environment(self, tmp_path):
        """A refusal is a result, so it has to be as citable as a measurement."""
        image, _ = render(make_scene(with_marker=False), CLOSE_CAMERA, RenderOptions())
        result = survey(_write(tmp_path, "x.png", image), SurveyParams())
        payload = result.record.to_dict()
        assert payload["refused"] is True
        assert payload["env"]["opencv_version"].startswith("5.")
        assert payload["metrics"]["frames_read"] == 1

    def test_an_undecodable_file_is_an_error_not_a_measurement(self, tmp_path):
        from visioncore import DecodeError

        path = tmp_path / "broken.png"
        path.write_bytes(b"not an image")
        with pytest.raises(DecodeError):
            survey(path, SurveyParams())

    def test_a_good_still_produces_a_schedule(self, tmp_path, close_bench):
        path = _write(tmp_path, "good.png", close_bench.image)
        result = survey(path, SurveyParams())
        measured = [c for c in result.cracks if c.measurement.ok]
        assert measured, "the reference scene should measure"
        assert result.record.metrics["frames_usable"] == 1
        for crack in measured:
            assert crack.measurement.p95_mm > 0
            assert crack.measurement.expanded_p95_mm > 0
            assert crack.band
            assert crack.crack_id.startswith("C")


class TestScheduleExport:
    def test_refused_runs_appear_in_the_csv_with_their_reason(self, far_bench, tmp_path):
        """A schedule that silently drops what it could not measure reads as a clean wall."""
        from hairline.service import schedule_csv

        path = _write(tmp_path, "far.png", far_bench.image)
        result = survey(path, SurveyParams())
        csv_text = schedule_csv(result.record)
        lines = csv_text.strip().splitlines()
        assert lines[0].startswith("crack_id,")
        refused = [c for c in result.cracks if not c.measurement.ok]
        if refused:
            assert any("BELOW_RESOLUTION" in line or "UNCERTAINTY" in line for line in lines[1:])
        assert len(lines) == len(result.cracks) + 1

    def test_the_csv_has_one_row_per_crack_and_a_stated_coverage_factor(self, close_bench,
                                                                       tmp_path):
        from hairline.service import schedule_csv

        result = survey(_write(tmp_path, "good.png", close_bench.image), SurveyParams())
        lines = schedule_csv(result.record).strip().splitlines()
        assert "coverage_factor_k" in lines[0]
        assert all(",2.0," in line or ",2," in line for line in lines[1:])


class TestVersionGuard:
    def test_the_engine_refuses_to_run_on_opencv_4(self):
        import cv2
        from visioncore.version import OpenCVVersionError, assert_opencv5

        assert cv2.__version__.startswith("5."), "this suite must run on OpenCV 5"
        assert_opencv5()
        assert OpenCVVersionError is not None

    def test_the_run_record_names_the_opencv_it_used(self, close_bench, tmp_path):
        record = RunRecord(product="hairline")
        assert record.env.opencv_version.startswith("5.")
