"""The estimator's mathematics, checked without any image in the way.

These are the tests that would have caught the 380x error in 
section 5.0 before it reached a frame: they assert what the width model does on inputs
whose right answer is arithmetic rather than photography.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hairline.width import (
    HalfDepthModel,
    blurred_box_profile,
    halfdepth_width,
    resolution_floor_px,
    solve_width_from_halfdepth,
    solve_width_from_ratio,
)


class TestBlurredBoxModel:
    def test_an_unblurred_box_is_its_own_half_depth_width(self):
        assert halfdepth_width(7.0, 0.0) == pytest.approx(7.0)

    @pytest.mark.parametrize("width", [2.0, 3.0, 4.29, 6.86, 13.71, 40.0])
    def test_forward_then_inverse_returns_the_width(self, width):
        sigma = 1.12
        recovered = solve_width_from_halfdepth(halfdepth_width(width, sigma), sigma)
        assert recovered == pytest.approx(width, rel=1e-3)

    def test_half_depth_saturates_at_the_blur_for_a_vanishing_crack(self):
        sigma = 1.4
        floor = resolution_floor_px(sigma)
        assert floor == pytest.approx(2.3548 * sigma, rel=1e-3)
        assert halfdepth_width(1e-4, sigma) == pytest.approx(floor, rel=1e-2)

    def test_the_half_depth_reading_is_badly_wrong_for_a_narrow_crack(self):
        """The reason the correction exists, asserted as a number."""
        sigma = 1.12
        naive = halfdepth_width(2.0, sigma)
        assert naive / 2.0 > 1.4  # the raw reading is at least 40 percent high
        assert solve_width_from_halfdepth(naive, sigma) == pytest.approx(2.0, rel=1e-3)

    def test_below_the_floor_there_is_no_answer_rather_than_a_small_one(self):
        sigma = 1.5
        assert solve_width_from_halfdepth(resolution_floor_px(sigma) * 0.9, sigma) is None
        assert solve_width_from_halfdepth(resolution_floor_px(sigma), sigma) is None

    def test_profile_is_symmetric_and_peaks_at_the_centre(self):
        xs = np.linspace(-8, 8, 33)
        values = np.array([blurred_box_profile(x, 5.0, 1.0) for x in xs])
        assert values[16] == values.max()
        assert values[:16] == pytest.approx(values[17:][::-1], abs=1e-12)

    def test_area_ratio_also_has_a_floor(self):
        sigma = 1.2
        assert solve_width_from_ratio(math.sqrt(2 * math.pi) * sigma * 0.99, sigma) is None
        assert solve_width_from_ratio(9.0, sigma) == pytest.approx(
            solve_width_from_ratio(9.0, sigma)
        )


class TestLookupTable:
    def test_the_table_agrees_with_the_solver(self):
        sigma = 1.05
        model = HalfDepthModel(sigma)
        for width in (2.0, 2.6, 4.0, 9.0, 25.0):
            reading = halfdepth_width(width, sigma)
            assert model.width_from_halfdepth(reading)[0] == pytest.approx(width, rel=2e-3)

    def test_the_table_returns_nan_below_the_floor(self):
        model = HalfDepthModel(1.3)
        out = model.width_from_halfdepth(np.array([model.floor_px * 0.8, 9.0]))
        assert math.isnan(out[0])
        assert out[1] > 0

    def test_sensitivity_to_blur_grows_as_the_crack_approaches_the_floor(self):
        model = HalfDepthModel(1.2)
        near = model.sensitivity_to_sigma(np.array([model.floor_px * 1.1]))[0]
        far = model.sensitivity_to_sigma(np.array([20.0]))[0]
        assert near > far
        assert far < 0.5  # a wide crack barely cares what the blur is


class TestTheRecordedBug:
    """section 5.0: a filled contour reported 283 mm for 0.75 mm."""

    def test_filling_an_open_polyline_still_produces_the_380x_error(self):
        from visioncore.measure import filled_polyline_width_max

        points = np.array([[40, 300], [200, 120], [420, 260], [560, 90]], dtype=np.int32)
        wrong = filled_polyline_width_max(points, (400, 640))
        assert wrong > 60, "the recorded bug should still be reproducible from the library"

    def test_hairline_never_fills_a_contour_to_measure(self):
        """A source-level guard: `drawContours(..., -1)` must not appear in the engine."""
        from pathlib import Path

        import hairline

        offenders = []
        for path in Path(hairline.__file__).parent.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "drawContours" not in stripped:
                    continue
                if "-1" in stripped or "FILLED" in stripped or "thickness=-1" in stripped:
                    offenders.append(f"{path.name}: {stripped}")
        assert not offenders, f"a filled contour appears in the engine: {offenders}"

    def test_the_medial_axis_of_a_known_band_gives_its_width(self):
        """The right way, on a mask whose width is exact by array slicing."""
        from visioncore import stroke_width_profile

        mask = np.zeros((200, 400), np.uint8)
        mask[98:105, 50:350] = 255  # exactly 7 rows
        profile = stroke_width_profile(mask)
        assert profile.ok
        assert profile.p50_px == pytest.approx(7.0, abs=1.0)


class TestProfileReading:
    def _profile(self, width_px: float, sigma: float, depth: float = 90.0,
                 background: float = 190.0, noise: float = 0.0, seed: int = 3) -> np.ndarray:
        pitch = 0.25
        xs = np.arange(-20.0, 20.0 + pitch, pitch)
        values = background - depth * np.array(
            [blurred_box_profile(x, width_px, sigma) for x in xs]
        )
        if noise:
            values = values + np.random.default_rng(seed).normal(0, noise, values.size)
        return values

    def test_reads_a_clean_wide_crack(self):
        from hairline.width import _read_profile

        reading = _read_profile(self._profile(8.0, 1.0), 0.25, 1.0)
        assert reading.ok
        assert reading.halfdepth_px == pytest.approx(8.0, abs=0.2)

    def test_refuses_a_profile_with_no_darkness(self):
        from hairline.width import _read_profile

        flat = np.full(160, 200.0)
        assert not _read_profile(flat, 0.25, 1.0).ok

    def test_refuses_a_crack_wider_than_the_profile_it_was_sampled_with(self):
        """A profile whose darkness never comes back up has not bracketed the crack."""
        from hairline.width import _read_profile

        reading = _read_profile(self._profile(34.0, 1.0), 0.25, 1.0)
        assert not reading.ok
        assert "past the end" in reading.reason

    def test_refuses_when_the_whole_profile_is_inside_the_dark(self):
        """Wider still, and there is no background left to measure against at all."""
        from hairline.width import _read_profile

        reading = _read_profile(self._profile(200.0, 1.0), 0.25, 1.0)
        assert not reading.ok
        assert "no darkness" in reading.reason

    def test_survives_realistic_noise(self):
        from hairline.width import _read_profile

        reading = _read_profile(self._profile(6.0, 1.1, noise=2.5), 0.25, 1.1)
        assert reading.ok
        assert reading.halfdepth_px == pytest.approx(6.0, abs=0.6)


class TestPsfEstimate:
    def test_reads_the_blur_off_a_synthetic_edge(self):
        from hairline.width import _sigma_from_edge

        for sigma in (0.6, 1.0, 1.8):
            pitch = 0.2
            xs = np.arange(-7.0, 7.0 + pitch, pitch)
            edge = 40.0 + 180.0 * 0.5 * (1.0 + np.array(
                [math.erf(x / (math.sqrt(2) * sigma)) for x in xs]
            ))
            assert _sigma_from_edge(edge, pitch) == pytest.approx(sigma, rel=0.12)

    def test_returns_none_when_there_is_no_edge(self):
        from hairline.width import _sigma_from_edge

        assert _sigma_from_edge(np.full(100, 128.0), 0.2) is None

    def test_falls_back_when_no_marker_is_present(self):
        from hairline.width import estimate_psf_sigma

        sigma, source = estimate_psf_sigma(np.zeros((200, 200), np.uint8), [])
        assert sigma == 0.9
        assert "no marker" in source
