"""The harness must produce real numbers locally and skip absent arms cleanly."""

from __future__ import annotations

import json

import pytest

from bench.arms import KLEIDICV_DISCLOSURE, Arm, available_arms, local_arm
from bench.harness import ArmResult, build_runner, run_workload
from bench.workloads import WORKLOADS, Workload, get_workload, register


@pytest.fixture
def tiny_workload() -> Workload:
    return Workload(
        name="tiny",
        description="a 64x64 blur, fast enough for a unit test",
        setup_src="import cv2, numpy as np\nframe = np.zeros((64, 64, 3), np.uint8)",
        run_src="cv2.GaussianBlur(frame, (5, 5), 0)",
        frames_per_run=1,
    )


class TestRunsWithOnlyTheLocalArm:
    def test_local_arm_produces_real_timings(self, tiny_workload):
        report = run_workload(tiny_workload, [local_arm()], repeats=3, warmup=1)
        result = report.results[0]
        assert result.ok, result.error
        assert len(result.samples_ms) == 3
        assert result.median_ms > 0
        assert result.env["opencv_version"].startswith("5.")

    def test_remote_arms_are_skipped_not_fatal(self, tiny_workload, monkeypatch):
        for var in ("BENCH_GRAVITON_HOST", "BENCH_COOL_HOST", "BENCH_HYBRID_HOST"):
            monkeypatch.delenv(var, raising=False)
        report = run_workload(tiny_workload, repeats=2, warmup=0)
        assert any(r.ok for r in report.results), "the local arm must still run"
        skipped = [r for r in report.results if not r.ok]
        assert skipped, "the graviton arms should be skipped"
        for result in skipped:
            assert "is not set" in result.skipped_reason

    def test_an_unreachable_host_is_skipped_with_a_reason(self, tiny_workload):
        unreachable = Arm(
            name="graviton-pip",
            description="fake",
            instance_type="c8g.large",
            host="nobody@198.51.100.7",
        )
        runnable, skipped = available_arms([unreachable])
        assert runnable == []
        assert len(skipped) == 1
        assert "ssh to nobody@198.51.100.7 failed" in skipped[0][1]

    def test_one_broken_arm_does_not_lose_the_others_numbers(self, tiny_workload):
        broken = Arm(name="broken", description="bad interpreter",
                     instance_type="local", python="/nonexistent/python")
        report = run_workload(tiny_workload, [local_arm(), broken], repeats=2, warmup=0)
        assert sum(r.ok for r in report.results) == 1
        assert any(not r.ok and r.error for r in report.results)


class TestStatistics:
    def _result(self, samples):
        return ArmResult(arm=local_arm(), ok=True, samples_ms=samples)

    def test_median_and_p95_are_what_they_claim(self):
        result = self._result([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])
        assert result.median_ms == 55.0
        assert result.p95_ms == 100.0, "nearest-rank p95 of 10 samples is the slowest"

    def test_p95_of_a_single_sample_is_that_sample(self):
        assert self._result([12.5]).p95_ms == 12.5

    def test_throughput_is_frames_over_median(self):
        assert self._result([50.0]).fps(1) == pytest.approx(20.0)
        assert self._result([50.0]).fps(5) == pytest.approx(100.0)

    def test_no_samples_yields_none_not_zero(self):
        empty = ArmResult(arm=local_arm(), ok=False)
        assert empty.median_ms is None and empty.p95_ms is None and empty.fps(1) is None


class TestCost:
    def test_cost_per_1k_frames_matches_the_published_hourly_rate(self):
        """c8g.large is $0.0798/hr. At 100 ms/frame, 1000 frames is 100 s."""
        arm = Arm(name="graviton-pip", description="", instance_type="c8g.large",
                  host="x@y")
        expected = 0.0798 / 3600 * 100  # 100 seconds of c8g.large
        assert arm.cost_per_1k_frames(100.0) == pytest.approx(expected, rel=1e-9)

    def test_cool_software_fee_is_added_on_top_of_ec2(self):
        plain = Arm(name="a", description="", instance_type="c8g.large", host="x")
        cool = Arm(name="b", description="", instance_type="c8g.large", host="x", cool=True)
        assert cool.usd_per_hour == pytest.approx(0.0798 + 0.01)
        assert cool.usd_per_hour > plain.usd_per_hour

    def test_the_local_arm_has_no_cost_because_it_is_not_billed(self):
        assert local_arm().usd_per_hour is None
        assert local_arm().cost_per_1k_frames(100.0) is None

    def test_an_unknown_instance_type_declines_to_invent_a_price(self):
        arm = Arm(name="x", description="", instance_type="z9.enormous", host="x")
        assert arm.usd_per_hour is None


class TestReport:
    def test_json_and_markdown_both_carry_the_kleidicv_disclosure(self, tiny_workload):
        report = run_workload(tiny_workload, [local_arm()], repeats=2, warmup=0)
        payload = json.loads(report.to_json())
        assert payload["kleidicv_disclosure"] == KLEIDICV_DISCLOSURE
        assert "KleidiCV" in report.to_markdown()

    def test_markdown_table_has_a_row_per_arm_including_skips(self, tiny_workload, monkeypatch):
        monkeypatch.delenv("BENCH_GRAVITON_HOST", raising=False)
        report = run_workload(tiny_workload, repeats=2, warmup=0)
        markdown = report.to_markdown()
        for result in report.results:
            assert result.arm.name in markdown
        assert "_skipped:" in markdown

    def test_report_writes_both_files(self, tiny_workload, tmp_path):
        report = run_workload(tiny_workload, [local_arm()], repeats=2, warmup=0)
        json_path, md_path = report.write(tmp_path)
        assert json_path.is_file() and md_path.is_file()
        assert json.loads(json_path.read_text())["workload"]["name"] == "tiny"

    def test_speedup_table_appears_only_with_a_graviton_pip_reference(self, tiny_workload):
        report = run_workload(tiny_workload, [local_arm()], repeats=2, warmup=0)
        assert "Speedup against" not in report.to_markdown()


class TestRunnerScript:
    def test_the_runner_contains_the_workload_source_verbatim(self, tiny_workload):
        script = build_runner(tiny_workload, repeats=4, warmup=2)
        assert "cv2.GaussianBlur(frame, (5, 5), 0)" in script
        assert "range(4)" in script and "range(2)" in script

    def test_multiline_run_source_is_indented_into_the_function(self):
        workload = Workload(
            name="multi", description="",
            setup_src="import cv2, numpy as np\nf = np.zeros((8, 8), np.uint8)",
            run_src="a = cv2.blur(f, (3, 3))\nb = cv2.blur(a, (3, 3))",
        )
        script = build_runner(workload, 1, 0)
        assert "    a = cv2.blur(f, (3, 3))" in script
        assert "    b = cv2.blur(a, (3, 3))" in script
        report = run_workload(workload, [local_arm()], repeats=1, warmup=0)
        assert report.results[0].ok, report.results[0].error


class TestWorkloadRegistry:
    def test_the_cool_relevant_workloads_are_the_expensive_ones(self):
        """FINDINGS 2.3: adaptiveThreshold and findContours dominate a 4K frame."""
        assert WORKLOADS["adaptive_threshold"].cool_relevant
        assert WORKLOADS["find_contours"].cool_relevant
        assert WORKLOADS["crack_pipeline"].cool_relevant

    def test_unknown_workload_names_the_known_ones(self):
        with pytest.raises(KeyError, match="adaptive_threshold"):
            get_workload("does-not-exist")

    def test_a_product_can_register_its_own(self):
        workload = register(Workload(name="_probe", description="", setup_src="x = 1",
                                     run_src="x + 1"))
        try:
            assert get_workload("_probe") is workload
        finally:
            WORKLOADS.pop("_probe", None)

    def test_repeats_must_be_positive(self, tiny_workload):
        with pytest.raises(ValueError, match="repeats"):
            run_workload(tiny_workload, [local_arm()], repeats=0)
