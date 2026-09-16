"""Plane geometry: the local Jacobian, and why width is measured with it.

The naive way to turn a crack width in pixels into millimetres is to divide by one
px-per-mm number recovered at the marker. That is wrong twice over when the camera
is not square to the wall: the scale varies across the frame, and the direction that
is perpendicular to the crack *in the image* is not the direction that is
perpendicular to the crack *on the wall*.

The fix is the 2x2 Jacobian J of the plane-millimetres to image-pixels map, evaluated
at the sample point:

* the crack centre line is mapped into plane coordinates, where its normal `n` is the
  true perpendicular on the wall;
* the image direction to sample along is the unit vector of `J n`;
* one pixel along that direction is exactly `1 / |J n|` millimetres on the wall.

So perspective is corrected analytically, per sample, with no resampling. The
rectified image this module can also produce is for the human looking at the report;
it never carries a number.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

__all__ = [
    "PlaneMap",
    "rectify",
]


class PlaneMap:
    """A plane-millimetres <-> image-pixels map with local differential geometry."""

    __slots__ = ("h_mm_to_px", "h_px_to_mm")

    def __init__(self, h_mm_to_px: np.ndarray) -> None:
        h = np.asarray(h_mm_to_px, dtype=np.float64)
        if h.shape != (3, 3) or not np.all(np.isfinite(h)):
            raise ValueError("homography must be a finite 3x3 matrix")
        det = float(np.linalg.det(h))
        if abs(det) < 1e-12:
            raise ValueError("homography is singular")
        self.h_mm_to_px = h
        self.h_px_to_mm = np.linalg.inv(h)

    # ---- point maps -------------------------------------------------------
    def to_px(self, points_mm: np.ndarray) -> np.ndarray:
        pts = np.asarray(points_mm, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.h_mm_to_px).reshape(-1, 2)

    def to_mm(self, points_px: np.ndarray) -> np.ndarray:
        pts = np.asarray(points_px, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.h_px_to_mm).reshape(-1, 2)

    # ---- differential -----------------------------------------------------
    def jacobian(self, x_mm: float, y_mm: float) -> np.ndarray:
        """d(u, v) / d(x, y) in pixels per millimetre at a point on the plane."""
        h = self.h_mm_to_px
        p = h @ np.array([x_mm, y_mm, 1.0])
        w = p[2]
        if abs(w) < 1e-12:
            raise ValueError("point is on the horizon of this homography")
        u, v = p[0], p[1]
        j = np.empty((2, 2), dtype=np.float64)
        for k in range(2):
            j[0, k] = (h[0, k] * w - u * h[2, k]) / (w * w)
            j[1, k] = (h[1, k] * w - v * h[2, k]) / (w * w)
        return j

    def px_per_mm(self, x_mm: float, y_mm: float) -> float:
        """Isotropic local scale, sqrt(|det J|)."""
        return math.sqrt(abs(float(np.linalg.det(self.jacobian(x_mm, y_mm)))))

    def px_per_mm_along(self, x_mm: float, y_mm: float, direction_mm: np.ndarray) -> float:
        """Pixels per millimetre when stepping along `direction_mm` on the wall.

        This is the number that decides whether a crack is resolvable: it is the
        sampling density the camera actually gave us across the crack, which under a
        tilt is smaller than the isotropic scale.
        """
        d = np.asarray(direction_mm, dtype=np.float64).reshape(2)
        norm = float(np.linalg.norm(d))
        if norm < 1e-12:
            raise ValueError("direction must be non-zero")
        return float(np.linalg.norm(self.jacobian(x_mm, y_mm) @ (d / norm)))

    def image_direction(self, x_mm: float, y_mm: float, direction_mm: np.ndarray) -> np.ndarray:
        """Unit image direction corresponding to a plane direction at a point."""
        d = np.asarray(direction_mm, dtype=np.float64).reshape(2)
        img = self.jacobian(x_mm, y_mm) @ d
        norm = float(np.linalg.norm(img))
        if norm < 1e-12:
            raise ValueError("plane direction collapses to a point in the image")
        return img / norm

    def foreshortening(self, x_mm: float, y_mm: float) -> float:
        """Ratio of the smaller to the larger singular value of J. 1.0 is square-on.

        Reported rather than a tilt angle in degrees, because recovering a true tilt
        needs camera intrinsics we do not have, while this ratio is exactly what the
        homography knows and exactly what limits the measurement.
        """
        s = np.linalg.svd(self.jacobian(x_mm, y_mm), compute_uv=False)
        if s[0] <= 0:
            return 0.0
        return float(s[1] / s[0])

    def apparent_tilt_deg(self, x_mm: float, y_mm: float) -> float:
        """acos(foreshortening), in degrees. A reading, not a measured plane angle.

        For a plane viewed on the optical axis this equals the true tilt. Off axis the
        projection adds shear and the reading runs high; the evaluation records by how
        much. Treated throughout as a conservative gate, never as a reported geometry.
        """
        return math.degrees(math.acos(max(0.0, min(1.0, self.foreshortening(x_mm, y_mm)))))


def rectify(
    image: np.ndarray,
    plane: PlaneMap,
    *,
    extent_mm: tuple[float, float, float, float],
    px_per_mm: float,
    interpolation: int = cv2.INTER_CUBIC,
) -> tuple[np.ndarray, PlaneMap]:
    """Warp the image to a fronto-parallel millimetre grid, for the report picture.

    `extent_mm` is (x0, y0, x1, y1) on the wall. The returned PlaneMap describes the
    rectified image, so an overlay drawn on it can still be positioned in millimetres.
    Resampling costs sharpness, which is why measurements are never taken from here.
    """
    x0, y0, x1, y1 = extent_mm
    if x1 <= x0 or y1 <= y0:
        raise ValueError("extent must have positive width and height")
    out_w = max(2, round((x1 - x0) * px_per_mm))
    out_h = max(2, round((y1 - y0) * px_per_mm))
    to_out = np.array(
        [[px_per_mm, 0.0, -x0 * px_per_mm], [0.0, px_per_mm, -y0 * px_per_mm], [0.0, 0.0, 1.0]]
    )
    warp = to_out @ plane.h_px_to_mm
    out = cv2.warpPerspective(
        image, warp, (out_w, out_h), flags=interpolation, borderMode=cv2.BORDER_CONSTANT
    )
    return out, PlaneMap(to_out)
