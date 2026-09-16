"""Crack width, measured perpendicular to the crack on the wall, with an uncertainty.

The model, and why the default estimator is what it is
------------------------------------------------------
A crack in an image is a dark band of width `w` and intrinsic darkness `D0`, blurred
by the lens into a profile

    p(x) = D0/2 * [ erf((x + w/2) / (sqrt(2) sigma)) - erf((x - w/2) / (sqrt(2) sigma)) ]

Reading the full width at half the observed depth gives a number that is right for a
wide crack and badly wrong for a narrow one, because as `w` falls the half-depth
width does not fall with it: it flattens out at `2.355 * sigma`, the width of the
blur itself. Measured on this pipeline that is a +0.1% error at 6.9 px across the
crack and a +50% error at 2.0 px. An inspector reading the narrow one would record
half a millimetre of crack that is not there.

Since the half-depth width is a known, monotonic function of `w` and `sigma`, and
`sigma` is measurable from the marker's own printed edges in the same frame, the
function can simply be inverted. That is the `blur_corrected` estimator and it is the
default. Below `2.355 * sigma` the function is flat and no inversion exists, which is
not a limitation to work around but the actual resolution limit of the photograph:
there, Hairline reports an upper bound and asks for a closer frame.

Three others are implemented, and `docs/evaluation.md` reports all four side by side:

* `halfdepth` -- the raw reading, kept so the correction can be seen working.
* `area_ratio` -- from the blur-invariant integral of the darkness deficit,
  `A / D = w / erf(w / (2 sqrt(2) sigma))`. Elegant, and in practice the noisiest of
  the four, because sensor noise inflates the observed depth `D` and biases `w` low.
* `distance_transform` -- the width the binary mask implies, via the medial axis. It
  moves with wherever the threshold happened to fall, so it is carried as a
  cross-check rather than as an answer.

All four sample **perpendicular to the crack on the wall**, not perpendicular to it
in the image: under a tilt those are different directions, and the second one is
wrong. See `geometry.PlaneMap`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from visioncore import Refusal, to_gray

from .config import SurveyParams
from .geometry import PlaneMap
from .segment import Component

__all__ = [
    "CrackMeasurement",
    "WidthSample",
    "blurred_box_profile",
    "estimate_psf_sigma",
    "halfdepth_width",
    "measure_component",
    "resolution_floor_px",
    "solve_width_from_halfdepth",
    "solve_width_from_ratio",
]

BOOTSTRAP_DRAWS = 400
BOOTSTRAP_SEED = 20261026
SAMPLE_PITCH_PX = 0.25
"""Sub-pixel spacing of the intensity profile. Bilinear interpolation below this is noise."""


# ---------------------------------------------------------------------------
# Profile mathematics
# ---------------------------------------------------------------------------


SQRT2 = math.sqrt(2.0)


def blurred_box_profile(x_px: float, width_px: float, sigma_px: float) -> float:
    """Darkness of a box of width `width_px` blurred by `sigma_px`, at offset `x_px`.

    Normalised so a fully resolved crack reads 1.0 at its centre.
    """
    if sigma_px <= 1e-9:
        return 1.0 if abs(x_px) <= width_px / 2.0 else 0.0
    return 0.5 * (
        math.erf((x_px + width_px / 2.0) / (SQRT2 * sigma_px))
        - math.erf((x_px - width_px / 2.0) / (SQRT2 * sigma_px))
    )


def halfdepth_width(width_px: float, sigma_px: float) -> float:
    """Full width at half the observed depth, for a blurred box. Monotonic in width."""
    if sigma_px <= 1e-9:
        return width_px
    target = 0.5 * blurred_box_profile(0.0, width_px, sigma_px)
    lo, hi = 0.0, width_px / 2.0 + 8.0 * sigma_px
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if blurred_box_profile(mid, width_px, sigma_px) > target:
            lo = mid
        else:
            hi = mid
    return lo + hi


def resolution_floor_px(sigma_px: float) -> float:
    """The half-depth width a vanishingly thin crack still produces: 2.355 * sigma.

    Any observed half-depth width at or under this carries no information about the
    crack's width, only about the lens.
    """
    return 2.0 * SQRT2 * math.sqrt(math.log(2.0)) * max(sigma_px, 0.0)


class HalfDepthModel:
    """Tabulated `halfdepth_width` for one blur, so inverting it is an interpolation.

    A closed-form inverse does not exist, and bisecting the forward function per
    sample costs about eight milliseconds -- for a 4K frame with a thousand profiles
    that is most of the run. The blur is constant across a frame, so the curve is
    built once, vectorised over the whole width grid at once, and every sample after
    that is an `np.interp`. Measured effect on a four-crack 4K frame: 9.4 s to 0.2 s.
    """

    __slots__ = ("floor_px", "halfdepth_grid", "sigma_px", "width_grid")

    def __init__(self, sigma_px: float, *, points: int = 900, max_width_px: float = 400.0):
        self.sigma_px = float(sigma_px)
        self.width_grid = np.geomspace(1e-3, max_width_px, points)
        self.halfdepth_grid = _halfdepth_width_vectorised(self.width_grid, self.sigma_px)
        self.floor_px = resolution_floor_px(self.sigma_px)

    def width_from_halfdepth(self, halfdepth_px: np.ndarray | float) -> np.ndarray:
        """Inverse, as an array. NaN where the reading is below the resolution floor."""
        values = np.atleast_1d(np.asarray(halfdepth_px, dtype=np.float64))
        out = np.interp(values, self.halfdepth_grid, self.width_grid)
        out[values <= self.floor_px * 1.002] = np.nan
        out[values >= self.halfdepth_grid[-1]] = self.width_grid[-1]
        return out

    def sensitivity_to_sigma(self, halfdepth_px: np.ndarray | float) -> np.ndarray:
        """|dw/dsigma| at each reading, for the uncertainty budget."""
        step = max(0.02, 0.05 * self.sigma_px)
        lo = HalfDepthModel(max(1e-3, self.sigma_px - step))
        hi = HalfDepthModel(self.sigma_px + step)
        a = lo.width_from_halfdepth(halfdepth_px)
        b = hi.width_from_halfdepth(halfdepth_px)
        out = np.abs(b - a) / (2.0 * step)
        fallback = np.nan_to_num(
            self.width_from_halfdepth(halfdepth_px), nan=0.0
        ) / max(self.sigma_px, 1e-6)
        return np.where(np.isfinite(out), out, fallback)


def _halfdepth_width_vectorised(widths: np.ndarray, sigma_px: float) -> np.ndarray:
    """`halfdepth_width` over a whole grid, by bisecting every entry at once."""
    if sigma_px <= 1e-9:
        return widths.copy()
    scale = SQRT2 * sigma_px
    half = widths / 2.0

    def profile(x: np.ndarray) -> np.ndarray:
        # numpy has no erf; the CDF of the standard normal is erf rescaled, and
        # math.erf vectorised through np.vectorize is slower than doing it this way.
        return 0.5 * (_erf_array((x + half) / scale) - _erf_array((x - half) / scale))

    target = 0.5 * profile(np.zeros_like(widths))
    lo = np.zeros_like(widths)
    hi = half + 8.0 * sigma_px
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        above = profile(mid) > target
        lo = np.where(above, mid, lo)
        hi = np.where(above, hi, mid)
    return lo + hi


def _erf_array(x: np.ndarray) -> np.ndarray:
    """Abramowitz and Stegun 7.1.26, accurate to 1.5e-7, which is far finer than a pixel."""
    sign = np.sign(x)
    ax = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    poly = t * (
        0.254829592
        + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))
    )
    return sign * (1.0 - poly * np.exp(-ax * ax))


def solve_width_from_halfdepth(halfdepth_px: float, sigma_px: float) -> float | None:
    """Invert `halfdepth_width` for the true width. None below the resolution floor."""
    if sigma_px <= 1e-9:
        return max(0.0, halfdepth_px)
    floor = resolution_floor_px(sigma_px)
    if halfdepth_px <= floor * 1.002:
        return None
    lo, hi = 1e-6, max(4.0 * halfdepth_px, 8.0 * sigma_px)
    if halfdepth_width(hi, sigma_px) < halfdepth_px:
        return hi
    for _ in range(70):
        mid = 0.5 * (lo + hi)
        if halfdepth_width(mid, sigma_px) < halfdepth_px:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def sensitivity_to_sigma(width_px: float, sigma_px: float, halfdepth_px: float) -> float:
    """|dw / dsigma| at the solution, so an error in the blur estimate can be carried.

    Near the resolution floor this is large, which is the honest signal that a crack
    barely wider than the blur cannot be measured more precisely than the blur is known.
    """
    step = max(0.02, 0.05 * sigma_px)
    a = solve_width_from_halfdepth(halfdepth_px, max(1e-6, sigma_px - step))
    b = solve_width_from_halfdepth(halfdepth_px, sigma_px + step)
    if a is None or b is None:
        return abs(width_px) / max(sigma_px, 1e-6)
    return abs(b - a) / (2.0 * step)


def solve_width_from_ratio(ratio_px: float, sigma_px: float) -> float | None:
    """Invert ratio = w / erf(w / (2*sqrt(2)*sigma)) for w, in pixels.

    The `area_ratio` estimator. The right-hand side increases monotonically from
    sqrt(2*pi)*sigma at w -> 0 to w itself at large w, so a ratio at or under that
    floor means the crack is narrower than the blur and its width is not recoverable.
    Returns None in that case rather than a small number that would look measured.
    """
    if sigma_px <= 1e-6:
        return max(0.0, ratio_px)
    floor = math.sqrt(2.0 * math.pi) * sigma_px
    if ratio_px <= floor * 1.001:
        return None
    lo, hi = 1e-4, max(4.0 * ratio_px, 8.0 * sigma_px)

    def f(w: float) -> float:
        e = math.erf(w / (2.0 * SQRT2 * sigma_px))
        return w / e if e > 1e-12 else float("inf")

    if f(hi) < ratio_px:
        return hi
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if f(mid) < ratio_px:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@dataclass
class ProfileReading:
    ok: bool
    halfdepth_px: float = 0.0
    area_ratio_px: float | None = None
    """The blur-invariant ratio A/D, in pixels. Inverted in bulk, not here."""
    area_px_dn: float = 0.0
    depth_dn: float = 0.0
    background_dn: float = 0.0
    reason: str = ""


def _read_profile(values: np.ndarray, pitch_px: float, sigma_px: float) -> ProfileReading:
    """Turn one intensity profile across the crack into width readings."""
    n = values.size
    if n < 9:
        return ProfileReading(False, reason="profile too short")
    edge = max(2, n // 6)
    background = float(np.median(np.concatenate([values[:edge], values[-edge:]])))
    deficit = np.clip(background - values, 0.0, None)

    centre = n // 2
    search = max(2, n // 8)
    lo_c = max(0, centre - search)
    peak_idx = int(lo_c + np.argmax(deficit[lo_c : centre + search + 1]))
    depth = float(deficit[peak_idx])
    if depth <= 1.0:
        return ProfileReading(False, reason="no darkness at the crack centre")

    # Walk out from the peak to the first point where the deficit dies away, so a
    # neighbouring crack in the same profile cannot be added into this one's area.
    cut = 0.12 * depth
    left = peak_idx
    while left > 0 and deficit[left - 1] > cut:
        left -= 1
    right = peak_idx
    while right < n - 1 and deficit[right + 1] > cut:
        right += 1
    if left == 0 or right == n - 1:
        return ProfileReading(False, reason="crack runs past the end of the profile")

    # The background came from the ends of this profile, so if the dark band covers
    # most of it the background is measured from inside the crack. That does not fail
    # loudly: it lowers the half-depth level and quietly under-reports the width. The
    # check is on how much of the profile is dark, which is the one signal that is not
    # circular here.
    dark_fraction = float(np.count_nonzero(deficit > 0.12 * depth)) / n
    if dark_fraction > 0.6:
        return ProfileReading(
            False,
            reason="crack runs past the end of the profile",
        )

    segment = deficit[left : right + 1]
    area = float(np.trapezoid(segment, dx=pitch_px))

    half = 0.5 * depth
    crossings: list[float] = []
    for direction in (-1, 1):
        i = peak_idx
        found = None
        while 0 < i < n - 1:
            j = i + direction
            if deficit[j] <= half:
                span = deficit[i] - deficit[j]
                frac = (deficit[i] - half) / span if span > 1e-9 else 0.0
                found = i + direction * frac
                break
            i = j
        if found is None:
            return ProfileReading(False, reason="profile never returns to half depth")
        crossings.append(found)
    halfdepth_px = abs(crossings[1] - crossings[0]) * pitch_px

    return ProfileReading(
        ok=True,
        halfdepth_px=halfdepth_px,
        area_ratio_px=area / depth,
        area_px_dn=area,
        depth_dn=depth,
        background_dn=background,
    )


# ---------------------------------------------------------------------------
# Point spread function, from the marker's own edges
# ---------------------------------------------------------------------------


def estimate_psf_sigma(
    gray: np.ndarray, marker_corners: list[np.ndarray], *, default: float = 0.9
) -> tuple[float, str]:
    """Blur width in pixels, read off the printed marker's black-to-white edges.

    The marker is the one object in the frame whose true edge is known to be a step,
    so differentiating across it gives the line spread function directly: the same
    card that provides the scale also calibrates the deconvolution, in the same frame,
    at the same focus. No separate calibration shot.

    Sigma comes from the 25%-to-75% rise distance rather than the second moment of the
    gradient. The second moment weights the profile tails by the square of their
    distance, so sensor noise nine pixels out counts ninety times more than noise at
    the edge, and a sharp edge in a noisy frame reads as a soft one. The rise distance
    ignores the tails. For a Gaussian edge, sigma = rise / 1.349.
    """
    if not marker_corners:
        return default, "default (no marker)"
    sigmas: list[float] = []
    half = 7.0
    pitch = 0.2
    offsets = np.arange(-half, half + pitch, pitch, dtype=np.float32)
    for quad in marker_corners:
        pts = np.asarray(quad, dtype=np.float64).reshape(4, 2)
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            edge = b - a
            length = float(np.linalg.norm(edge))
            if length < 16:
                continue
            tangent = edge / length
            normal = np.array([-tangent[1], tangent[0]])
            for frac in (0.25, 0.4, 0.6, 0.75):
                base = a + edge * frac
                pts_x = (base[0] + normal[0] * offsets).astype(np.float32)
                pts_y = (base[1] + normal[1] * offsets).astype(np.float32)
                profile = cv2.remap(
                    gray,
                    pts_x.reshape(1, -1),
                    pts_y.reshape(1, -1),
                    cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE,
                ).astype(np.float64).ravel()
                sigma = _sigma_from_edge(profile, pitch)
                if sigma is not None and 0.15 < sigma < 6.0:
                    sigmas.append(sigma)
    if len(sigmas) < 4:
        return default, "default (too few usable marker edges)"
    return float(np.median(sigmas)), f"marker edges (n={len(sigmas)})"


def _sigma_from_edge(profile: np.ndarray, pitch_px: float) -> float | None:
    """Gaussian sigma from the 25%-75% rise distance of one edge profile."""
    n = profile.size
    if n < 15:
        return None
    plateau = max(3, n // 6)
    low = float(np.median(profile[:plateau]))
    high = float(np.median(profile[-plateau:]))
    if abs(high - low) < 25.0:  # not a black-to-white transition
        return None
    rising = high > low
    normalised = (profile - low) / (high - low) if rising else (profile - high) / (low - high)
    xs = np.arange(n, dtype=np.float64) * pitch_px

    def crossing(level: float) -> float | None:
        above = normalised >= level
        idx = np.flatnonzero(above[1:] != above[:-1])
        if idx.size == 0:
            return None
        # the crossing nearest the middle of the profile, where the edge should be
        centre = (n - 1) / 2.0
        i = int(idx[np.argmin(np.abs(idx - centre))])
        y0, y1 = normalised[i], normalised[i + 1]
        if abs(y1 - y0) < 1e-9:
            return None
        return float(xs[i] + (level - y0) / (y1 - y0) * pitch_px)

    x25, x75 = crossing(0.25), crossing(0.75)
    if x25 is None or x75 is None:
        return None
    rise = abs(x75 - x25)
    if rise <= 0:
        return None
    return rise / 1.349


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------


def _orientation_field(mask: np.ndarray, window_px: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel unit tangent of a thin structure, from the structure tensor.

    The smaller eigenvector of the gradient structure tensor points along the ridge.
    Computed on a blurred copy of the mask, so it is stable at a one-pixel-wide crack
    where fitting a line to skeleton pixels is not.
    """
    soft = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), max(1.0, window_px / 4.0))
    gx = cv2.Sobel(soft, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(soft, cv2.CV_32F, 0, 1, ksize=3)
    ksize = max(3, int(window_px) | 1)
    jxx = cv2.boxFilter(gx * gx, cv2.CV_32F, (ksize, ksize))
    jyy = cv2.boxFilter(gy * gy, cv2.CV_32F, (ksize, ksize))
    jxy = cv2.boxFilter(gx * gy, cv2.CV_32F, (ksize, ksize))
    # Dominant gradient orientation; the tangent is perpendicular to it.
    theta = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy)
    return -np.sin(theta), np.cos(theta)


def _spread_samples(points: np.ndarray, step_px: float) -> np.ndarray:
    """One skeleton point per `step_px` grid cell: even spacing without sorting a path."""
    if points.size == 0:
        return points
    step = max(1.0, step_px)
    cells = np.floor(points / step).astype(np.int64)
    _, index = np.unique(cells, axis=0, return_index=True)
    return points[np.sort(index)]


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass
class WidthSample:
    x_px: float
    y_px: float
    x_mm: float
    y_mm: float
    width_mm: float
    width_px: float
    px_per_mm_normal: float
    depth_dn: float
    normal_px: tuple[float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "px": [round(self.x_px, 1), round(self.y_px, 1)],
            "mm": [round(self.x_mm, 2), round(self.y_mm, 2)],
            "width_mm": round(self.width_mm, 4),
            "width_px": round(self.width_px, 3),
            "px_per_mm_normal": round(self.px_per_mm_normal, 3),
            "normal_px": [round(self.normal_px[0], 4), round(self.normal_px[1], 4)],
        }


@dataclass
class CrackMeasurement:
    ok: bool
    samples: list[WidthSample] = field(default_factory=list)
    p50_mm: float | None = None
    p95_mm: float | None = None
    max_mm: float | None = None
    mean_mm: float | None = None
    u_p95_mm: float | None = None
    u_p50_mm: float | None = None
    expanded_p95_mm: float | None = None
    length_mm: float | None = None
    upper_bound_mm: float | None = None
    """Set when the crack is real but too fine to measure: it is narrower than this."""
    resolved_px: float | None = None
    px_per_mm_normal: float | None = None
    estimator: str = ""
    sigma_px: float | None = None
    confidence: str = "none"
    refusal: Refusal | None = None
    cross_check: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def r(v: float | None, n: int = 3) -> float | None:
            return None if v is None else round(float(v), n)

        return {
            "ok": self.ok,
            "samples": len(self.samples),
            "p50_mm": r(self.p50_mm),
            "p95_mm": r(self.p95_mm),
            "max_mm": r(self.max_mm),
            "mean_mm": r(self.mean_mm),
            "u_p95_mm": r(self.u_p95_mm),
            "u_p50_mm": r(self.u_p50_mm),
            "expanded_p95_mm": r(self.expanded_p95_mm),
            "length_mm": r(self.length_mm, 1),
            "upper_bound_mm": r(self.upper_bound_mm),
            "resolved_px": r(self.resolved_px, 2),
            "px_per_mm_normal": r(self.px_per_mm_normal, 2),
            "estimator": self.estimator,
            "sigma_px": r(self.sigma_px, 2),
            "confidence": self.confidence,
            "refusal": self.refusal.to_dict() if self.refusal else None,
            "cross_check": dict(self.cross_check),
            "uncertainty_budget_mm": {k: round(v, 4) for k, v in self.budget.items()},
        }


def _smooth_along_crack(
    points_px: np.ndarray, widths: np.ndarray, radius_px: float
) -> tuple[np.ndarray, float]:
    """Local median width at each sample, and the per-sample noise it reveals.

    A crack's width genuinely varies along its length, and the estimate at each sample
    also carries noise. Taking the 95th percentile of the raw samples confounds the
    two: for a target of exactly constant width, the raw p95 comes out above the truth
    by however noisy the estimator is, and the report would claim the crack is wider
    somewhere than it is anywhere.

    Replacing each sample by the median of its spatial neighbours keeps the real
    variation, which is smooth along the crack, and averages away the noise, which is
    not. The scatter left over is a direct measurement of that noise, so it feeds the
    uncertainty budget instead of being guessed.
    """
    n = widths.size
    if n < 3:
        return widths.copy(), 0.0
    step = max(radius_px, 1.0)
    cells: dict[tuple[int, int], list[int]] = {}
    keys = np.floor(points_px / step).astype(np.int64)
    for i, (cx, cy) in enumerate(keys):
        cells.setdefault((int(cx), int(cy)), []).append(i)

    smoothed = np.empty(n, dtype=np.float64)
    r2 = radius_px * radius_px
    for i in range(n):
        cx, cy = keys[i]
        near: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                near.extend(cells.get((int(cx) + dx, int(cy) + dy), ()))
        if not near:
            smoothed[i] = widths[i]
            continue
        cand = np.asarray(near)
        d2 = np.sum((points_px[cand] - points_px[i]) ** 2, axis=1)
        inside = cand[d2 <= r2]
        smoothed[i] = float(np.median(widths[inside])) if inside.size else widths[i]

    residual = widths - smoothed
    mad = float(np.median(np.abs(residual - float(np.median(residual)))))
    return smoothed, 1.4826 * mad


def _percentile_bootstrap(widths: np.ndarray, q: float) -> float:
    rng = np.random.default_rng(BOOTSTRAP_SEED + int(q * 100))
    n = widths.size
    draws = rng.integers(0, n, size=(BOOTSTRAP_DRAWS, n))
    stats = np.percentile(widths[draws], q, axis=1)
    return float(np.std(stats, ddof=1))


def measure_component(
    image: np.ndarray,
    component: Component,
    plane: PlaneMap,
    params: SurveyParams,
    *,
    sigma_px: float,
    scale_rel_uncertainty: float,
    sigma_uncertainty_px: float = 0.15,
    working_distance_mm: float = 350.0,
) -> CrackMeasurement:
    """Width distribution for one crack, in millimetres on the wall."""
    gray = to_gray(image).astype(np.float32)
    h, w = gray.shape[:2]
    if component.skeleton.size == 0:
        return CrackMeasurement(
            ok=False, refusal=Refusal("NO_RIDGE", "the component has no medial axis to sample")
        )

    tangent_x, tangent_y = _orientation_field(component.mask, params.tangent_window_px)
    points = _spread_samples(component.skeleton, params.sample_step_px)
    if points.shape[0] > params.max_samples:
        keep = np.linspace(0, points.shape[0] - 1, params.max_samples).round().astype(int)
        points = points[np.unique(keep)]
    dist = cv2.distanceTransform(component.mask, cv2.DIST_L2, 5)

    model = HalfDepthModel(sigma_px)
    samples: list[WidthSample] = []
    readings: list[ProfileReading] = []
    geometry: list[tuple[float, float, float, float, float, float, float, float]] = []
    tangent_px_per_mm: list[float] = []
    sigma_sensitivity: list[float] = []
    sigma_gradient: np.ndarray | None = None
    failures: dict[str, int] = {}
    unresolved = 0
    resolved_limits: list[float] = []

    for px, py in points:
        ix, iy = round(px), round(py)
        if not (0 <= ix < w and 0 <= iy < h):
            continue
        t_img = np.array([float(tangent_x[iy, ix]), float(tangent_y[iy, ix])])
        if not np.all(np.isfinite(t_img)) or np.linalg.norm(t_img) < 1e-6:
            continue

        mm = plane.to_mm(np.array([[px, py]]))[0]
        try:
            jac = plane.jacobian(float(mm[0]), float(mm[1]))
            t_plane = np.linalg.solve(jac, t_img)
        except (ValueError, np.linalg.LinAlgError):
            continue
        norm = float(np.linalg.norm(t_plane))
        if norm < 1e-9:
            continue
        t_plane /= norm
        n_plane = np.array([-t_plane[1], t_plane[0]])

        img_normal = jac @ n_plane
        ppm_normal = float(np.linalg.norm(img_normal))
        if ppm_normal < 1e-6:
            continue
        e_hat = img_normal / ppm_normal
        tangent_ppm = float(np.linalg.norm(jac @ t_plane))

        half_len = max(6.0, params.profile_half_len_mult * float(dist[iy, ix]) + 3.0 * sigma_px)
        offsets = np.arange(-half_len, half_len + SAMPLE_PITCH_PX, SAMPLE_PITCH_PX)
        xs = (px + e_hat[0] * offsets).astype(np.float32)
        ys = (py + e_hat[1] * offsets).astype(np.float32)
        if xs.min() < 1 or ys.min() < 1 or xs.max() > w - 2 or ys.max() > h - 2:
            failures["off_frame"] = failures.get("off_frame", 0) + 1
            continue
        profile = cv2.remap(
            gray, xs.reshape(1, -1), ys.reshape(1, -1), cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        ).astype(np.float64).ravel()

        reading = _read_profile(profile, SAMPLE_PITCH_PX, sigma_px)
        if not reading.ok:
            failures[reading.reason] = failures.get(reading.reason, 0) + 1
            continue
        readings.append(reading)
        geometry.append(
            (float(px), float(py), float(mm[0]), float(mm[1]), ppm_normal,
             float(e_hat[0]), float(e_hat[1]), float(dist[iy, ix]))
        )
        tangent_px_per_mm.append(tangent_ppm)

    # --- invert the whole frame's readings in one vectorised pass ---------
    if readings:
        halfdepths = np.array([r.halfdepth_px for r in readings], dtype=np.float64)
        if params.estimator == "halfdepth":
            widths_px = halfdepths
        elif params.estimator == "distance_transform":
            widths_px = np.array([2.0 * g[7] - 1.0 for g in geometry], dtype=np.float64)
        elif params.estimator == "area_ratio":
            widths_px = np.array(
                [
                    solve_width_from_ratio(r.area_ratio_px or 0.0, sigma_px) or np.nan
                    for r in readings
                ],
                dtype=np.float64,
            )
        else:
            widths_px = model.width_from_halfdepth(halfdepths)
            sigma_gradient = model.sensitivity_to_sigma(halfdepths)
    else:
        widths_px = np.zeros(0)

    # A crack's darkness is roughly constant along its length, so the deepest
    # profiles show what its full darkness D0 is. Under the blur model a trough at
    # less than half of that means the crack is narrower than the blur just there.
    depths = np.array([r.depth_dn for r in readings], dtype=np.float64)
    full_depth = float(np.percentile(depths, 90)) if depths.size else 0.0
    depth_floor = params.min_depth_fraction * full_depth

    # Resolvability is a property of the crack and the frame, not of one noisy
    # sample. Applying the floor per sample keeps only the samples that happened to
    # measure wide, so a crack sitting just under the floor came back measured, from
    # its widest readings, biased upward: a 0.35 mm crack read 0.382 mm that way.
    # Decide once, on the median of everything, then keep every sample or none.
    finite = widths_px[np.isfinite(widths_px)] if widths_px.size else np.zeros(0)
    finite = finite[finite > 0]
    floor_px = max(params.min_resolved_px, params.min_width_sigma_ratio * sigma_px)
    median_width_px = float(np.median(finite)) if finite.size else 0.0
    crack_is_resolvable = finite.size > 0 and median_width_px >= floor_px

    for i, width_px in enumerate(widths_px):
        gx, gy, mx, my, ppm_normal, ex, ey, _d = geometry[i]
        if not crack_is_resolvable:
            unresolved += 1
            resolved_limits.append(floor_px / ppm_normal)
            continue
        if readings[i].depth_dn < depth_floor:
            unresolved += 1
            resolved_limits.append(floor_px / ppm_normal)
            continue
        if not math.isfinite(width_px):
            unresolved += 1
            resolved_limits.append(floor_px / ppm_normal)
            continue
        if width_px <= 0:
            failures["non_positive"] = failures.get("non_positive", 0) + 1
            continue
        if sigma_gradient is not None:
            sigma_sensitivity.append(float(sigma_gradient[i]))
        samples.append(
            WidthSample(
                x_px=gx,
                y_px=gy,
                x_mm=mx,
                y_mm=my,
                width_mm=float(width_px) / ppm_normal,
                width_px=float(width_px),
                px_per_mm_normal=ppm_normal,
                depth_dn=readings[i].depth_dn,
                normal_px=(ex, ey),
            )
        )

    ridge_px = int(component.skeleton.shape[0])

    if len(samples) < params.min_samples:
        if unresolved >= max(params.min_samples, 3) and resolved_limits:
            bound = float(np.median(resolved_limits))
            mean_ppm = float(np.median([
                plane.px_per_mm(*plane.to_mm(component.skeleton[:1])[0])
            ])) if ridge_px else 1.0
            return CrackMeasurement(
                ok=False,
                upper_bound_mm=bound,
                resolved_px=params.min_resolved_px,
                px_per_mm_normal=mean_ppm,
                estimator=params.estimator,
                sigma_px=sigma_px,
                confidence="bounded",
                refusal=Refusal(
                    "BELOW_RESOLUTION",
                    f"the crack is visible but finer than this frame can measure; "
                    f"it is narrower than {bound:.2f} mm. Move closer, or use a longer lens, "
                    f"so the crack spans at least {params.min_resolved_px:.1f} pixels.",
                    details={
                        "upper_bound_mm": round(bound, 3),
                        "unresolved_samples": unresolved,
                        "min_resolved_px": params.min_resolved_px,
                    },
                ),
            )
        return CrackMeasurement(
            ok=False,
            estimator=params.estimator,
            sigma_px=sigma_px,
            refusal=Refusal(
                "TOO_FEW_SAMPLES",
                f"only {len(samples)} usable width profiles across this crack "
                f"(need {params.min_samples}); it is too short, too broken, or too "
                f"faint against the surface to measure",
                details={"usable": len(samples), "failures": failures, "unresolved": unresolved},
            ),
        )

    raw_widths = np.array([s.width_mm for s in samples], dtype=np.float64)
    points_px = np.array([[s.x_px, s.y_px] for s in samples], dtype=np.float64)
    ppm_normals = np.array([s.px_per_mm_normal for s in samples], dtype=np.float64)
    widths, sample_noise_mm = _smooth_along_crack(
        points_px, raw_widths, radius_px=max(3.0 * params.sample_step_px, 8.0)
    )
    p50 = float(np.percentile(widths, 50))
    p95 = float(np.percentile(widths, 95))

    # --- uncertainty budget, in millimetres, combined in quadrature -------
    u_scale_p95 = p95 * scale_rel_uncertainty
    u_coplanar_p95 = p95 * (params.coplanarity_mm / max(working_distance_mm, 1.0))
    u_sampling_p95 = _percentile_bootstrap(widths, 95.0)
    u_noise = sample_noise_mm / math.sqrt(max(1, min(len(samples), 9)))
    # How much a wrong blur estimate would move the answer. Near the resolution floor
    # this dominates everything else, which is the correct signal: a crack barely
    # wider than the lens blur cannot be known better than the blur is known.
    u_blur = (
        float(np.median(sigma_sensitivity)) * sigma_uncertainty_px / float(np.median(ppm_normals))
        if sigma_sensitivity
        else 0.0
    )
    u_quant_p95 = 0.15 / float(np.median(ppm_normals))
    u_p95 = math.sqrt(
        u_scale_p95**2
        + u_coplanar_p95**2
        + u_sampling_p95**2
        + u_quant_p95**2
        + u_noise**2
        + u_blur**2
    )

    u_scale_p50 = p50 * scale_rel_uncertainty
    u_coplanar_p50 = p50 * (params.coplanarity_mm / max(working_distance_mm, 1.0))
    u_p50 = math.sqrt(
        u_scale_p50**2
        + u_coplanar_p50**2
        + _percentile_bootstrap(widths, 50.0) ** 2
        + u_quant_p95**2
        + u_noise**2
        + u_blur**2
    )

    mean_tangent_ppm = float(np.median(tangent_px_per_mm)) if tangent_px_per_mm else 1.0
    length_mm = _run_length_px(component, dist) / max(mean_tangent_ppm, 1e-6)

    resolved = float(np.median([s.width_px for s in samples]))

    # Last gate: a width whose own interval is wider than a third of it is not a
    # measurement anybody should put in a schedule, however it was arrived at.
    relative = params.coverage_factor * u_p95 / max(p95, 1e-9)
    if relative > params.max_relative_uncertainty:
        return CrackMeasurement(
            ok=False,
            samples=samples,
            resolved_px=resolved,
            px_per_mm_normal=float(np.median(ppm_normals)),
            estimator=params.estimator,
            sigma_px=sigma_px,
            confidence="low",
            upper_bound_mm=p95 + params.coverage_factor * u_p95,
            refusal=Refusal(
                "UNCERTAINTY_TOO_LARGE",
                f"this crack measures {p95:.2f} mm give or take "
                f"{params.coverage_factor * u_p95:.2f} mm, which is {relative:.0%} of the "
                f"answer. Hairline will not put that in a schedule. Re-shoot closer, "
                f"squarer to the wall, and in focus.",
                details={
                    "p95_mm": round(p95, 3),
                    "expanded_mm": round(params.coverage_factor * u_p95, 3),
                    "relative": round(relative, 3),
                },
            ),
            budget={
                "scale": u_scale_p95, "coplanarity": u_coplanar_p95,
                "sampling": u_sampling_p95, "quantisation": u_quant_p95,
                "estimator_noise": u_noise, "blur_calibration": u_blur,
                "combined_standard": u_p95,
                "expanded_k2": params.coverage_factor * u_p95,
            },
        )

    confidence = _confidence(
        resolved_px=resolved,
        n=len(samples),
        floor_px=max(params.min_resolved_px, params.min_width_sigma_ratio * sigma_px),
        rel_u=relative,
        unresolved=unresolved,
    )

    dt_widths = np.array(
        [2.0 * float(dist[round(s.y_px), round(s.x_px)]) - 1.0 for s in samples]
    )
    cross_check = {
        "distance_transform_p95_mm": round(
            float(np.percentile(dt_widths / ppm_normals, 95)), 4
        ),
        "raw_p95_mm": round(float(np.percentile(raw_widths, 95)), 4),
        "raw_p50_mm": round(float(np.percentile(raw_widths, 50)), 4),
        "sample_noise_mm": round(sample_noise_mm, 4),
        "unresolved_samples": unresolved,
        "profile_failures": failures,
        "ridge_px": ridge_px,
    }

    return CrackMeasurement(
        ok=True,
        samples=samples,
        p50_mm=p50,
        p95_mm=p95,
        max_mm=float(widths.max()),
        mean_mm=float(widths.mean()),
        u_p95_mm=u_p95,
        u_p50_mm=u_p50,
        expanded_p95_mm=params.coverage_factor * u_p95,
        length_mm=length_mm,
        resolved_px=resolved,
        px_per_mm_normal=float(np.median(ppm_normals)),
        estimator=params.estimator,
        sigma_px=sigma_px,
        confidence=confidence,
        cross_check=cross_check,
        budget={
            "scale": u_scale_p95,
            "coplanarity": u_coplanar_p95,
            "sampling": u_sampling_p95,
            "estimator_noise": u_noise,
            "blur_calibration": u_blur,
            "quantisation": u_quant_p95,
            "combined_standard": u_p95,
            "expanded_k2": params.coverage_factor * u_p95,
        },
    )


def _run_length_px(component: Component, dist: np.ndarray) -> float:
    """Length of a crack run, from its outline rather than from its skeleton.

    Counting medial-axis pixels seems like the obvious answer and is not: on a wide
    stroke the discrete distance transform's ridge is broken, and a 1.6 mm crack
    measured that way came out 26 percent short while a 0.35 mm crack of the same
    drawn length came out right. The outline does not have that problem. For a long
    thin region the perimeter is about twice the length plus twice the width, so

        length = perimeter / 2 - width

    with the width taken from the same mask the perimeter came from, which keeps the
    two consistent. The contour is simplified by one pixel first: `arcLength` over a
    dense boundary sums the staircase a diagonal is digitised into, which overstates
    a sloping crack by about five percent. Simplifying removes that and leaves the
    error under one percent against known lengths.
    """
    smoothed = cv2.approxPolyDP(component.contour, 1.0, True)
    perimeter = float(cv2.arcLength(smoothed, True))
    on_ridge = dist[_ridge_mask(component, dist)]
    width_px = (2.0 * float(on_ridge.mean()) - 1.0) if on_ridge.size else component.mean_width_px
    return max(0.0, perimeter / 2.0 - max(width_px, 0.0))


def _ridge_mask(component: Component, dist: np.ndarray) -> np.ndarray:
    dilated = cv2.dilate(dist, np.ones((3, 3), np.uint8))
    return (dist >= dilated - 1e-6) & (dist > 0.5)


def _confidence(
    *, resolved_px: float, n: int, floor_px: float, rel_u: float, unresolved: int
) -> str:
    """A word, not a number, because the number is already the uncertainty.

    `margin` is how far the crack sits above the resolution floor of the photograph
    it was measured in, which is the variable the sweep showed the error depends on.
    """
    margin = resolved_px / max(floor_px, 1e-6)
    if margin >= 1.8 and n >= 25 and rel_u <= 0.10 and unresolved <= n * 0.2:
        return "high"
    if margin >= 1.25 and n >= 12 and rel_u <= 0.18:
        return "moderate"
    return "low"
