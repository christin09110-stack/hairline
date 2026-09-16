"""Survey parameters. One dataclass, defaults chosen so a refusal is cheap.

Every threshold here is a decision about when Hairline declines to answer. The
defaults were not guessed: `eval/sweep.py` measures the error of each estimator
against synthetic targets of known width across distance, angle, focus and lighting,
and `docs/evaluation.md` records the numbers that set them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ESTIMATORS", "GradeBand", "SurveyParams"]

ESTIMATORS = ("blur_corrected", "halfdepth", "area_ratio", "distance_transform")


@dataclass(frozen=True)
class GradeBand:
    """A review band. Deliberately operator-set, not a code limit.

    Hairline ships no crack-width limit from any design code. We could not verify the
    numbers in ACI 224R, EN 1992-1-1 or BS 8110 against a primary source we hold, and
    quoting a limit we have not read would be the kind of confident wrong number this
    product exists to avoid. So the default bands are labelled as an operator's review
    threshold, the UI says so, and the value is a parameter.
    """

    label: str
    upper_mm: float | None
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "upper_mm": self.upper_mm, "note": self.note}


DEFAULT_BANDS: tuple[GradeBand, ...] = (
    GradeBand("Below review threshold", 0.20, "operator-set default, not a code limit"),
    GradeBand("Record and monitor", 0.40, "operator-set default, not a code limit"),
    GradeBand("Refer to engineer", None, "operator-set default, not a code limit"),
)


@dataclass(frozen=True)
class SurveyParams:
    # --- scale reference -------------------------------------------------
    marker_length_mm: float = 60.0
    """Printed edge of the ArUco square, black border included, in millimetres."""
    marker_dictionary: str = "DICT_4X4_50"
    checkerboard: tuple[int, int] | None = None
    square_size_mm: float | None = None

    # --- frame selection --------------------------------------------------
    frame_stride: int = 3
    max_frames: int = 24
    """Frames kept for full analysis after gating. A walk-past is mostly redundant."""
    min_station_shift_mm: float = 45.0
    """Two frames closer together than this on the wall are the same station."""
    min_focus_ratio: float = 0.35
    """Frame sharpness as a fraction of the best frame in the clip, below which we refuse."""
    min_absolute_focus: float = 12.0
    """Laplacian variance floor. Under this nothing in the frame is in focus at all."""

    # --- geometry gates ---------------------------------------------------
    max_apparent_tilt_deg: float = 55.0
    """Apparent foreshortening, from the homography, not a measured plane angle.

    Off the optical axis the projection adds shear, so this reading runs above the
    true surface tilt: `docs/evaluation.md` records 25 degrees apparent for a true 10.
    The gate is deliberately loose because the scale itself survives well past it
    (0.1% error at 50 degrees apparent); what actually limits a measurement at an
    angle is the resolution left across the crack, and that is gated per crack by
    `min_resolved_px` on the real sampling density along the crack normal."""
    min_marker_px: float = 48.0
    max_residual_px: float = 2.0
    min_width_sigma_ratio: float = 4.25
    """A crack must span at least this many lens blurs across, or no width is reported.

    This is the gate that matters, and it is set from data rather than taste. Across
    the 1,232-row sweep in `docs/evaluation.md`, every reading that was badly wrong
    was a crack narrow relative to the blur it was photographed through: a fixed
    pixel threshold let a 0.30 mm crack read as 1.03 mm, while requiring the crack to
    span 3.5 sigma removed every such case and left a worst error of 7.5 percent.

    The blur is measured from the marker's own printed edges in the same frame, so
    the threshold adapts to how well that photograph was actually taken instead of
    assuming a camera."""
    min_resolved_px: float = 4.0
    """Absolute pixel floor across the crack, whatever the blur estimate says.

    The blur-relative gate above is the principled one, but it can be undercut. On a
    compressed video the marker's edges read sharper than the lens really is: the
    bundled walk-past measures sigma at 0.57 px where the frames were rendered with
    0.9 px of defocus, because the codec rings the very edges the estimate is
    calibrated on. Four sigma of an under-estimated sigma is not a real floor, so an
    absolute one sits underneath it, at the width where the sweep's tail of large
    errors ends independently of blur.

    In practice this is the number that tells an inspector how close to stand. On a
    4K phone at a 350 mm stand-off it puts the finest measurable crack at about
    0.47 mm; at 150 mm, at about 0.20 mm."""
    max_relative_uncertainty: float = 0.30
    """Decline rather than report a width whose own interval is wider than this fraction."""

    # --- exposure gates ---------------------------------------------------
    max_clipped_fraction: float = 0.08
    min_mean_level: float = 28.0
    max_mean_level: float = 232.0

    # --- segmentation -----------------------------------------------------
    expected_width_mm: float = 0.5
    """Sets the adaptive-threshold block size. Not a prior on the answer."""
    threshold_k: float = 2.4
    """Adaptive-threshold constant, in robust standard deviations of the surface texture."""
    threshold_c: float = 6.0
    """Absolute grey-level constant, used only when `threshold_c_fixed` is set."""
    threshold_c_fixed: bool = False
    min_component_area_px: int = 90
    min_elongation: float = 4.0
    min_contrast_dn: float = 7.0
    """Absolute floor: a component darker than its surround by less than this is a stain."""
    min_contrast_sigma: float = 8.0
    """Relative floor, in robust standard deviations of the surface's own texture.

    An absolute grey-level threshold cannot separate a faint crack from mottled
    concrete, because both numbers move with the exposure. Measured against the
    surface's own residual spread they separate cleanly: in the sweep, real cracks
    ran 11 to 40 sigma above their surround and the texture artefacts that survived
    everything else ran 6 to 7."""
    max_mean_width_mm: float = 12.0
    """Anything this fat is spalling or a shadow; Hairline measures cracks."""
    min_length_mm: float = 25.0
    edge_margin_px: float = 6.0
    """Components this close to the frame edge are refused: their extent is unknown."""
    split_branches: bool = True
    """Cut crack networks at their junctions so each run is measured on its own."""

    # --- width estimation -------------------------------------------------
    sample_step_px: float = 4.0
    tangent_window_px: float = 11.0
    profile_half_len_mult: float = 5.0
    estimator: str = "blur_corrected"
    """One of 'blur_corrected' (default), 'halfdepth', 'area_ratio', 'distance_transform'.

    All four are implemented and compared in `docs/evaluation.md`; the default is the
    one with the flattest bias against targets of known width."""
    min_samples: int = 6
    min_depth_fraction: float = 0.5
    """Reject a width profile whose trough is shallower than this fraction of the
    crack's own characteristic darkness.

    Not an arbitrary quality filter. Under the blur model the observed depth is
    D0 * erf(w / (2 sqrt(2) sigma)), so a trough at half the crack's full darkness
    means erf(...) = 0.5, which means the crack is narrower than about one sigma at
    that point: unresolved. Testing the depth is therefore the same test as testing
    the resolution, applied per sample rather than per crack, and it is what stops a
    crack that fades out along its length from dragging its own width upwards."""
    max_samples: int = 280
    """Profiles per crack. Beyond a few hundred the percentiles stop moving and the
    cost does not, so long cracks are sampled evenly rather than exhaustively."""

    # --- uncertainty ------------------------------------------------------
    coplanarity_mm: float = 2.0
    """Assumed out-of-plane offset between the marker card face and the crack surface."""
    working_distance_mm: float = 350.0
    """Assumed camera stand-off. Only used to turn `coplanarity_mm` into a relative term.

    We cannot recover the true stand-off without camera intrinsics, so this is an
    operator input with a stated default, and the report says so rather than implying
    the number was measured."""
    scale_floor_rel: float = 0.004
    """Relative scale uncertainty that never goes below this, whatever the fit residual."""
    coverage_factor: float = 2.0
    """k in U = k*u. 2 is the conventional ~95% interval."""

    # --- reporting --------------------------------------------------------
    bands: tuple[GradeBand, ...] = DEFAULT_BANDS
    max_cracks_reported: int = 60
    overlay_max_side: int = 1600
    evidence_per_crack: bool = True
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker_length_mm": self.marker_length_mm,
            "marker_dictionary": self.marker_dictionary,
            "frame_stride": self.frame_stride,
            "max_frames": self.max_frames,
            "min_station_shift_mm": self.min_station_shift_mm,
            "min_focus_ratio": self.min_focus_ratio,
            "max_apparent_tilt_deg": self.max_apparent_tilt_deg,
            "min_marker_px": self.min_marker_px,
            "min_resolved_px": self.min_resolved_px,
            "min_width_sigma_ratio": self.min_width_sigma_ratio,
            "max_relative_uncertainty": self.max_relative_uncertainty,
            "expected_width_mm": self.expected_width_mm,
            "threshold_k": self.threshold_k,
            "threshold_c": self.threshold_c if self.threshold_c_fixed else None,
            "min_component_area_px": self.min_component_area_px,
            "min_elongation": self.min_elongation,
            "min_contrast_dn": self.min_contrast_dn,
            "min_length_mm": self.min_length_mm,
            "estimator": self.estimator,
            "sample_step_px": self.sample_step_px,
            "coplanarity_mm": self.coplanarity_mm,
            "working_distance_mm": self.working_distance_mm,
            "coverage_factor": self.coverage_factor,
            "bands": [b.to_dict() for b in self.bands],
        }

    @classmethod
    def from_request(cls, params: dict[str, Any]) -> SurveyParams:
        """Build from the UI's JSON, ignoring anything we do not recognise."""
        known = {
            f: params[f]
            for f in (
                "marker_length_mm", "marker_dictionary", "frame_stride", "max_frames",
                "min_focus_ratio", "max_apparent_tilt_deg", "min_marker_px",
                "min_resolved_px", "min_width_sigma_ratio", "max_relative_uncertainty",
                "expected_width_mm", "threshold_c",
                "min_component_area_px", "min_elongation", "min_contrast_dn", "threshold_k",
                "min_length_mm", "estimator", "coplanarity_mm", "coverage_factor",
                "working_distance_mm",
            )
            if f in params and params[f] is not None
        }
        for key in ("frame_stride", "max_frames", "min_component_area_px"):
            if key in known:
                known[key] = int(known[key])
        for key in (
            "marker_length_mm", "min_focus_ratio", "max_apparent_tilt_deg", "min_marker_px",
            "min_resolved_px", "min_width_sigma_ratio", "max_relative_uncertainty",
            "expected_width_mm", "threshold_c", "threshold_k", "min_elongation",
            "min_contrast_dn", "min_length_mm", "coplanarity_mm", "coverage_factor",
            "working_distance_mm",
        ):
            if key in known:
                known[key] = float(known[key])
        params_obj = cls(**known)
        if params_obj.marker_length_mm <= 0:
            raise ValueError("marker_length_mm must be positive")
        if params_obj.estimator not in ESTIMATORS:
            raise ValueError(f"unknown estimator {params_obj.estimator!r}")
        return params_obj
