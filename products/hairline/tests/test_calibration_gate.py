"""The gate that decides whether this build is allowed to report a width at all.

`docs/design/hairline-spec.md` §9 requires this to run in CI and to gate publishing,
and it is the right requirement. Every other test here checks that a stage still does
what it did. This one checks the only thing a user cares about: a line whose width is
printed on the card comes back with that width on it.

If this file fails, the build fails, and the service refuses to publish any width and
says so. That is the difference between this product and the 283 mm reading for a
0.75 mm crack in `research/FINDINGS.md` §5.0.
"""

from __future__ import annotations

import pytest

from hairline.calibration_gate import TOLERANCE_PCT, CalibrationReport, check, render_card
from hairline.targets import COMPACT


@pytest.fixture(scope="module")
def report() -> CalibrationReport:
    return check()


class TestTheGate:
    def test_the_build_is_calibrated(self, report):
        """The headline assertion. Everything else in this file explains it."""
        assert report.ok, report.failure

    def test_every_printed_line_that_should_resolve_is_within_tolerance(self, report):
        measurable = [line for line in report.lines if line.expected_measurable]
        assert len(measurable) >= 3, "the card should offer at least three resolvable lines"
        for line in measurable:
            assert line.measured_mm is not None, (
                f"the {line.printed_mm:.3f} mm line was not measured: {line.refusal}"
            )
            assert abs(line.error_pct) <= TOLERANCE_PCT, (
                f"{line.printed_mm:.3f} mm printed read as {line.measured_mm:.3f} mm "
                f"({line.error_pct:+.2f}%)"
            )

    def test_lines_below_the_resolution_floor_are_declined_not_guessed(self, report):
        """The other half of the contract, and the half that is easy to lose."""
        unresolvable = [line for line in report.lines if not line.expected_measurable]
        assert unresolvable, "the card should include a line too fine for this stand-off"
        for line in unresolvable:
            assert line.measured_mm is None, (
                f"a {line.printed_mm:.3f} mm line should not have been measured, "
                f"but read {line.measured_mm}"
            )
            assert line.refusal, "a declined line must carry a reason"

    def test_the_scale_is_recovered_from_the_card(self, report):
        assert report.px_per_mm and report.px_per_mm > 0
        assert abs(report.scale_error_pct) < 1.0, (
            f"scale off by {report.scale_error_pct:+.2f}% on a rendered card"
        )

    def test_the_stated_interval_covers_the_printed_width(self, report):
        for line in report.lines:
            if line.measured_mm is None or line.expanded_mm is None:
                continue
            assert abs(line.measured_mm - line.printed_mm) <= line.expanded_mm + 0.01, (
                f"{line.printed_mm:.3f} mm: read {line.measured_mm:.3f} "
                f"+- {line.expanded_mm:.3f}, which does not cover the truth"
            )

    def test_the_report_serialises_for_the_interface(self, report):
        payload = report.to_dict()
        assert payload["ok"] is True
        assert payload["tolerance_pct"] == TOLERANCE_PCT
        assert len(payload["lines"]) == len(COMPACT.widths_mm)
        assert payload["checked_at"]
        assert "calibration ok" in report.summary()


class TestTheGateCanFail:
    """A gate that cannot fail is a decoration. These make it fail on purpose."""

    def test_an_unreadable_marker_fails_the_gate_rather_than_skipping_it(self):
        failed = check(blur_px=22.0)
        assert not failed.ok
        assert failed.failure
        assert not any(line.measured_mm for line in failed.lines)

    def test_a_card_too_far_away_fails_instead_of_passing_vacuously(self):
        """Declining everything is not a pass. A gate that proves nothing must say so."""
        far = check(stand_off_mm=1400.0)
        assert not far.ok
        assert "proves nothing" in far.failure or far.failure
        assert not [line for line in far.lines if line.measured_mm is not None]

    def test_a_mis_entered_marker_size_is_caught(self):
        """The printer's 'fit to page' mistake, which nothing downstream can detect.

        The card is drawn at its true size and the operator enters a marker 10 percent
        larger than it is, which is what a page scaled to fit does. The gate has to
        fail. It can fail two ways and both are correct detections: the widths come out
        10 percent wide, or the lines stop appearing where the card says they are,
        because a scale error moves positions as well as widths. On this card the
        second happens first, since the lines are 9 mm apart and a 10 percent error
        displaces the far ones by nearly 7 mm.
        """
        drifted = check(measured_marker_mm=COMPACT.marker_mm * 1.10)
        assert not drifted.ok, "a 10 percent scale error passed the gate unnoticed"
        assert drifted.failure
        passed_anyway = [
            line for line in drifted.lines
            if line.expected_measurable and line.ok
        ]
        assert not passed_anyway, (
            f"{len(passed_anyway)} lines still passed with the scale 10 percent out"
        )

    def test_a_small_scale_error_still_moves_the_widths(self):
        """Small enough that the lines are still found, so the widths carry the error."""
        drifted = check(measured_marker_mm=COMPACT.marker_mm * 1.04)
        measured = [line for line in drifted.lines if line.error_pct is not None]
        assert measured, "the lines should still be locatable at a 4 percent error"
        assert all(line.error_pct > 2.0 for line in measured), (
            f"a 4 percent over-stated marker did not widen the readings: "
            f"{[round(line.error_pct, 2) for line in measured]}"
        )


class TestTheRenderedCard:
    def test_the_card_declares_the_width_it_actually_drew(self):
        from hairline.targets import calibration_target

        _, manifest = calibration_target(COMPACT)
        for line in manifest["lines"]:
            # printed_mm is dots x dot pitch, so it is exact and is what we compare to.
            assert line["printed_mm"] == pytest.approx(
                line["dots"] * manifest["dot_mm"], abs=1e-5
            )
            assert abs(line["printed_mm"] - line["nominal_mm"]) < manifest["dot_mm"]

    def test_the_photograph_contains_the_marker(self):
        from visioncore import detect_aruco

        frame, truth = render_card()
        corners, ids = detect_aruco(frame, COMPACT.dictionary)
        assert ids == [COMPACT.marker_id]
        assert truth["px_per_mm_at_centre"] > 5
