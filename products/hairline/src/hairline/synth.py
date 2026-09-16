"""Synthetic concrete scenes whose crack widths are known in millimetres by construction.

Why synthesise
--------------
An evaluation of a measuring instrument needs targets whose true value is known to
better than the instrument's claimed precision. A photograph of a real wall does not
come with a certified crack width; a crack comparator card read by eye is good to
about 0.05 mm at best. So the accuracy numbers in `docs/evaluation.md` come from
scenes rendered here, where the width is a float we chose, and the reported error is
the difference between that float and what the pipeline recovered.

How the rendering avoids flattering the pipeline
------------------------------------------------
1. The crack is drawn **analytically in image space**, not rastered in plane space
   and resampled. For every image pixel we invert the plane homography, measure the
   distance in millimetres from that pixel's plane position to the crack centre line,
   and set coverage from the exact geometry. So a 0.30 mm crack is 0.30 mm wide to
   the precision of float64 at every point, at every viewing angle, and the edge
   antialiasing is the true area coverage rather than a resampling artefact.
2. The camera is a real pinhole with intrinsics and a pose. Tilting the plane
   produces genuine perspective: the scale varies across the frame, exactly the
   effect the pipeline must undo. A cheap affine squeeze would let a pipeline that
   ignores perspective score well.
3. Defocus, sensor noise, illumination gradient and JPEG-style quantisation are
   applied after compositing, in that order, which is the order a camera applies
   them.

What this does NOT model, and therefore what the evaluation cannot claim: real
cracks are V-shaped in section with spalled lips and a dark interior of variable
depth, they carry dirt and efflorescence, and the surrounding concrete has form-tie
holes, aggregate pop-out and shutter lines that look like cracks. The limitations
section of the report says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from visioncore.calibration import ARUCO_DICTS

__all__ = [
    "CameraSpec",
    "CrackSpec",
    "GroundTruth",
    "RenderOptions",
    "SceneSpec",
    "plane_to_image_homography",
    "render",
    "walk_past_video",
]


# ---------------------------------------------------------------------------
# Specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrackSpec:
    """A crack of known width, as a polyline on the wall in millimetres."""

    points_mm: tuple[tuple[float, float], ...]
    width_mm: float
    depth: float = 0.62
    """Darkness of a fully resolved crack, as a fraction of local surface luminance."""
    name: str = "crack"

    def polyline(self) -> np.ndarray:
        return np.asarray(self.points_mm, dtype=np.float64).reshape(-1, 2)

    def length_mm(self) -> float:
        pts = self.polyline()
        return float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))


@dataclass(frozen=True)
class SceneSpec:
    """A flat concrete panel carrying an ArUco marker and some cracks.

    Plane coordinates are millimetres with the origin at the panel centre, x right,
    y down, which matches image convention and keeps the homography free of flips.
    """

    panel_mm: tuple[float, float] = (420.0, 300.0)
    cracks: tuple[CrackSpec, ...] = ()
    marker_id: int = 7
    marker_mm: float = 60.0
    marker_centre_mm: tuple[float, float] = (-150.0, -100.0)
    marker_dictionary: str = "DICT_4X4_50"
    marker_quiet_mm: float = 12.0
    with_marker: bool = True
    surface_seed: int = 11
    surface_grain_mm: float = 2.2
    surface_contrast: float = 0.055
    aggregate_density: float = 0.00035
    """Pop-out and form-tie blemishes per square millimetre. These are the distractors."""


@dataclass(frozen=True)
class CameraSpec:
    """A pinhole camera looking at the panel."""

    image_size: tuple[int, int] = (1920, 1080)
    focal_px: float = 1600.0
    distance_mm: float = 700.0
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    centre_offset_mm: tuple[float, float] = (0.0, 0.0)
    """Where on the panel the optical axis lands, in plane millimetres."""

    def intrinsics(self) -> np.ndarray:
        w, h = self.image_size
        return np.array(
            [[self.focal_px, 0.0, w / 2.0], [0.0, self.focal_px, h / 2.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )


@dataclass(frozen=True)
class RenderOptions:
    blur_px: float = 0.8
    """Gaussian defocus sigma. 0.8 px is a phone at a sensible stand-off."""
    noise_dn: float = 2.2
    exposure: float = 1.0
    lighting_gradient: float = 0.18
    lighting_angle_deg: float = 25.0
    vignette: float = 0.12
    jpeg_quality: int | None = 92
    seed: int = 3


@dataclass(frozen=True)
class GroundTruth:
    """What the scene really was. The evaluation compares against this and nothing else."""

    homography_mm_to_px: np.ndarray
    px_per_mm_at_marker: float
    px_per_mm_at_crack: dict[str, float]
    crack_width_mm: dict[str, float]
    crack_length_mm: dict[str, float]
    crack_polyline_px: dict[str, np.ndarray]
    marker_edge_px: float | None
    obliquity_deg: float
    scene: SceneSpec
    camera: CameraSpec
    options: RenderOptions

    def to_dict(self) -> dict[str, Any]:
        return {
            "px_per_mm_at_marker": round(self.px_per_mm_at_marker, 4),
            "px_per_mm_at_crack": {k: round(v, 4) for k, v in self.px_per_mm_at_crack.items()},
            "crack_width_mm": dict(self.crack_width_mm),
            "crack_length_mm": {k: round(v, 2) for k, v in self.crack_length_mm.items()},
            "marker_edge_px": (
                None if self.marker_edge_px is None else round(self.marker_edge_px, 2)
            ),
            "obliquity_deg": round(self.obliquity_deg, 3),
            "camera": {
                "image_size": list(self.camera.image_size),
                "focal_px": self.camera.focal_px,
                "distance_mm": self.camera.distance_mm,
                "yaw_deg": self.camera.yaw_deg,
                "pitch_deg": self.camera.pitch_deg,
                "roll_deg": self.camera.roll_deg,
            },
            "render": {
                "blur_px": self.options.blur_px,
                "noise_dn": self.options.noise_dn,
                "exposure": self.options.exposure,
                "jpeg_quality": self.options.jpeg_quality,
            },
        }


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _rotation(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    y, p, r = (math.radians(v) for v in (yaw_deg, pitch_deg, roll_deg))
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1.0]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1.0, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1.0, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    return rz @ ry @ rx


def plane_to_image_homography(camera: CameraSpec) -> np.ndarray:
    """H mapping plane millimetres (x, y) to image pixels, for a pinhole camera.

    The plane is z = 0 in world coordinates, so with world-to-camera rotation R and
    translation t the projection collapses to H = K [r1 r2 t].
    """
    rot = _rotation(camera.yaw_deg, camera.pitch_deg, camera.roll_deg)
    ox, oy = camera.centre_offset_mm
    t = rot @ np.array([-ox, -oy, 0.0]) + np.array([0.0, 0.0, camera.distance_mm])
    h = camera.intrinsics() @ np.column_stack([rot[:, 0], rot[:, 1], t])
    return h / h[2, 2]


def _plane_coords(h_mm_to_px: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Plane-millimetre coordinates of every image pixel, and the local mm per pixel."""
    w, h = size
    h_inv = np.linalg.inv(h_mm_to_px)
    us, vs = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    ones = np.ones_like(us)
    stack = np.stack([us, vs, ones], axis=0).reshape(3, -1)
    plane = h_inv @ stack
    wcoord = plane[2]
    wcoord = np.where(np.abs(wcoord) < 1e-12, 1e-12, wcoord)
    xy = (plane[:2] / wcoord).reshape(2, h, w).transpose(1, 2, 0)

    # Local mm-per-pixel from first differences of the same map. Exact enough for
    # antialiasing and it costs nothing extra.
    dx = np.gradient(xy[..., 0], axis=1)
    dy = np.gradient(xy[..., 1], axis=1)
    du = np.hypot(dx, dy)
    dx = np.gradient(xy[..., 0], axis=0)
    dy = np.gradient(xy[..., 1], axis=0)
    dv = np.hypot(dx, dy)
    mm_per_px = np.sqrt(np.maximum(du * dv, 1e-12))
    return xy, mm_per_px


def _local_px_per_mm(h_mm_to_px: np.ndarray, x_mm: float, y_mm: float) -> float:
    """sqrt(|det J|) of the projective map at a plane point: the isotropic local scale."""
    hm = np.asarray(h_mm_to_px, dtype=np.float64)
    p = hm @ np.array([x_mm, y_mm, 1.0])
    wq = p[2]
    if abs(wq) < 1e-12:
        return 0.0
    u, v = p[0], p[1]
    j = np.empty((2, 2))
    for k in range(2):
        j[0, k] = (hm[0, k] * wq - u * hm[2, k]) / (wq * wq)
        j[1, k] = (hm[1, k] * wq - v * hm[2, k]) / (wq * wq)
    return math.sqrt(abs(float(np.linalg.det(j))))


def _distance_to_polyline_mm(xy: np.ndarray, polyline: np.ndarray) -> np.ndarray:
    """Per-pixel distance in plane millimetres to a polyline, vectorised per segment."""
    best = np.full(xy.shape[:2], np.inf, dtype=np.float64)
    px = xy[..., 0]
    py = xy[..., 1]
    for i in range(len(polyline) - 1):
        ax, ay = polyline[i]
        bx, by = polyline[i + 1]
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        if denom < 1e-12:
            continue
        t = ((px - ax) * vx + (py - ay) * vy) / denom
        np.clip(t, 0.0, 1.0, out=t)
        dx = px - (ax + t * vx)
        dy = py - (ay + t * vy)
        np.minimum(best, np.hypot(dx, dy), out=best)
    return best


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------


def _concrete_plane_texture(scene: SceneSpec, ppm: float) -> np.ndarray:
    """A grey concrete surface as a float32 plane-space image, values around 1.0."""
    pw, ph = scene.panel_mm
    w = max(64, round(pw * ppm))
    h = max(64, round(ph * ppm))
    rng = np.random.default_rng(scene.surface_seed)

    # Multi-octave value noise: coarse mottling plus fine aggregate grain.
    field = np.zeros((h, w), dtype=np.float32)
    amplitude = 1.0
    total = 0.0
    grain_px = max(2.0, scene.surface_grain_mm * ppm)
    for _ in range(4):
        small_h = max(2, round(h / grain_px))
        small_w = max(2, round(w / grain_px))
        octave = rng.standard_normal((small_h, small_w)).astype(np.float32)
        field += amplitude * cv2.resize(octave, (w, h), interpolation=cv2.INTER_CUBIC)
        total += amplitude
        amplitude *= 0.55
        grain_px = max(1.5, grain_px / 2.4)
    field /= max(total, 1e-6)
    field = (field - float(field.mean())) / max(float(field.std()), 1e-6)
    surface = 1.0 + scene.surface_contrast * field

    # Distractors: aggregate pop-out (dark round pits) and shutter lines. These exist
    # so that a segmenter which simply keeps every dark thing will score badly.
    n_blemish = int(scene.aggregate_density * pw * ph)
    for _ in range(n_blemish):
        cx = int(rng.uniform(0, w))
        cy = int(rng.uniform(0, h))
        radius = int(max(2, rng.uniform(0.8, 3.0) * ppm * 0.5))
        shade = float(rng.uniform(0.62, 0.88))
        cv2.circle(surface, (cx, cy), radius, shade, -1, lineType=cv2.LINE_AA)
    for _ in range(2):
        y0 = int(rng.uniform(0, h))
        cv2.line(surface, (0, y0), (w, y0 + int(rng.uniform(-8, 8))), 0.9,
                 max(1, int(0.6 * ppm)), lineType=cv2.LINE_AA)
    return np.clip(surface, 0.25, 1.6).astype(np.float32)


def _marker_plane_patch(scene: SceneSpec, ppm: float) -> tuple[np.ndarray, tuple[float, float]]:
    """The printed marker card as a plane-space patch, plus its top-left in plane mm."""
    side_mm = scene.marker_mm + 2 * scene.marker_quiet_mm
    side_px = max(48, round(side_mm * ppm))
    card = np.ones((side_px, side_px), dtype=np.float32)
    adict = cv2.aruco.getPredefinedDictionary(ARUCO_DICTS[scene.marker_dictionary])
    marker_px = max(16, round(scene.marker_mm * ppm))
    marker = cv2.aruco.generateImageMarker(adict, scene.marker_id, marker_px).astype(np.float32)
    marker /= 255.0
    off = (side_px - marker_px) // 2
    card[off : off + marker_px, off : off + marker_px] = marker
    cx, cy = scene.marker_centre_mm
    return card, (cx - side_mm / 2.0, cy - side_mm / 2.0)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(
    scene: SceneSpec | None = None,
    camera: CameraSpec | None = None,
    options: RenderOptions | None = None,
) -> tuple[np.ndarray, GroundTruth]:
    """Render one BGR frame and the ground truth that goes with it."""
    scene = scene or SceneSpec()
    camera = camera or CameraSpec()
    options = options or RenderOptions()

    h_mm_to_px = plane_to_image_homography(camera)
    xy, mm_per_px = _plane_coords(h_mm_to_px, camera.image_size)
    pw, ph = scene.panel_mm
    inside = (
        (np.abs(xy[..., 0]) <= pw / 2.0)
        & (np.abs(xy[..., 1]) <= ph / 2.0)
        & (mm_per_px < 1e3)
    )

    # Plane texture resolution: at least twice the finest image sampling of the plane,
    # so the surface is never the thing that limits detail.
    finest_mm_per_px = float(np.percentile(mm_per_px[inside], 2)) if inside.any() else 0.2
    ppm = float(np.clip(2.0 / max(finest_mm_per_px, 1e-3), 4.0, 24.0))
    texture = _concrete_plane_texture(scene, ppm)

    # Sample the plane texture at each pixel's plane position.
    _tex_h, _tex_w = texture.shape
    map_x = ((xy[..., 0] + pw / 2.0) * ppm).astype(np.float32)
    map_y = ((xy[..., 1] + ph / 2.0) * ppm).astype(np.float32)
    typical_mm_per_px = float(np.median(mm_per_px[inside])) if inside.any() else 0.2
    median_image_ppm = 1.0 / max(typical_mm_per_px, 1e-6)
    minify = ppm / max(median_image_ppm, 1e-6)
    if minify > 1.2:
        # Minifying with INTER_LINEAR aliases rather than blurs, which would show up
        # as a sharper-than-real edge. Prefilter so the synthetic camera has a real
        # point spread function rather than an aliased one.
        texture = cv2.GaussianBlur(texture, (0, 0), 0.5 * minify)
    image = cv2.remap(
        texture, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    ).astype(np.float64)
    image = np.where(inside, image, 0.42)  # off-panel: darker background

    # Marker card, composited from its own patch so its edges stay crisp.
    marker_edge_px: float | None = None
    if scene.with_marker:
        card, (card_x, card_y) = _marker_plane_patch(scene, ppm)
        if minify > 1.2:
            card = cv2.GaussianBlur(card, (0, 0), 0.5 * minify)
        card_h, card_w = card.shape
        cm_x = ((xy[..., 0] - card_x) * ppm).astype(np.float32)
        cm_y = ((xy[..., 1] - card_y) * ppm).astype(np.float32)
        card_sample = cv2.remap(
            card, cm_x, cm_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=-1.0
        )
        on_card = (
            (cm_x >= 0) & (cm_x <= card_w - 1) & (cm_y >= 0) & (cm_y <= card_h - 1)
        )
        image = np.where(on_card, np.clip(card_sample, 0.0, 1.0) * 0.97 + 0.02, image)
        marker_edge_px = scene.marker_mm * _local_px_per_mm(
            h_mm_to_px, *scene.marker_centre_mm
        )

    # Cracks, drawn analytically so the width is exact at every viewing angle.
    width_gt: dict[str, float] = {}
    length_gt: dict[str, float] = {}
    scale_gt: dict[str, float] = {}
    polyline_px: dict[str, np.ndarray] = {}
    for crack in scene.cracks:
        poly = crack.polyline()
        dist_mm = _distance_to_polyline_mm(xy, poly)
        half = crack.width_mm / 2.0
        # Coverage is the fraction of the pixel footprint inside the band. One pixel
        # spans mm_per_px millimetres on the plane, so the soft edge is exactly that wide.
        coverage = np.clip(0.5 + (half - dist_mm) / np.maximum(mm_per_px, 1e-9), 0.0, 1.0)
        image = image * (1.0 - coverage * crack.depth)
        centre = poly.mean(axis=0)
        width_gt[crack.name] = crack.width_mm
        length_gt[crack.name] = crack.length_mm()
        scale_gt[crack.name] = _local_px_per_mm(h_mm_to_px, float(centre[0]), float(centre[1]))
        pts = cv2.perspectiveTransform(poly.reshape(-1, 1, 2), h_mm_to_px).reshape(-1, 2)
        polyline_px[crack.name] = pts

    # Illumination: a smooth gradient across the plane plus lens vignetting.
    ang = math.radians(options.lighting_angle_deg)
    grad = (xy[..., 0] * math.cos(ang) + xy[..., 1] * math.sin(ang)) / max(pw, ph)
    image *= 1.0 + options.lighting_gradient * np.clip(grad, -1.5, 1.5)
    if options.vignette > 0:
        iw, ih = camera.image_size
        yy, xx = np.mgrid[0:ih, 0:iw]
        r = np.hypot(xx - iw / 2.0, yy - ih / 2.0) / (0.5 * math.hypot(iw, ih))
        image *= 1.0 - options.vignette * r**2

    image = np.clip(image * options.exposure * 205.0, 0, 255)

    if options.blur_px > 0:
        image = cv2.GaussianBlur(image, (0, 0), options.blur_px)
    if options.noise_dn > 0:
        rng = np.random.default_rng(options.seed)
        image = image + rng.normal(0.0, options.noise_dn, image.shape)

    frame = cv2.cvtColor(np.clip(image, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    if options.jpeg_quality is not None:
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), options.jpeg_quality])
        if ok:
            frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    obliquity = math.degrees(
        math.acos(min(1.0, abs(math.cos(math.radians(camera.yaw_deg)) *
                               math.cos(math.radians(camera.pitch_deg)))))
    )
    truth = GroundTruth(
        homography_mm_to_px=h_mm_to_px,
        px_per_mm_at_marker=_local_px_per_mm(h_mm_to_px, *scene.marker_centre_mm),
        px_per_mm_at_crack=scale_gt,
        crack_width_mm=width_gt,
        crack_length_mm=length_gt,
        crack_polyline_px=polyline_px,
        marker_edge_px=marker_edge_px,
        obliquity_deg=obliquity,
        scene=scene,
        camera=camera,
        options=options,
    )
    return frame, truth


# ---------------------------------------------------------------------------
# A walk-past, as a video
# ---------------------------------------------------------------------------


@dataclass
class WalkPast:
    scene: SceneSpec
    frames: int = 90
    fps: float = 15.0
    sweep_mm: tuple[float, float] = (-60.0, 60.0)
    yaw_sweep_deg: tuple[float, float] = (-14.0, 10.0)
    distance_mm: tuple[float, float] = (620.0, 540.0)
    image_size: tuple[int, int] = (1920, 1080)
    focal_px: float = 1500.0
    shake_mm: float = 4.0
    blur_bursts: tuple[int, ...] = field(default_factory=lambda: (18, 19, 20, 46, 47))
    """Frames where the operator moved and the autofocus lost it. Real footage has these."""
    seed: int = 5


def walk_past_video(plan: WalkPast, out_path: str) -> dict[str, Any]:
    """Write an MP4 of a camera moving past the panel. Returns the manifest."""
    rng = np.random.default_rng(plan.seed)
    writer = cv2.VideoWriter(
        out_path, cv2.VideoWriter_fourcc(*"mp4v"), plan.fps, plan.image_size
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot open video writer for {out_path}")
    manifest: dict[str, Any] = {
        "frames": plan.frames,
        "fps": plan.fps,
        "image_size": list(plan.image_size),
        "cracks": {c.name: {"width_mm": c.width_mm, "length_mm": round(c.length_mm(), 1)}
                   for c in plan.scene.cracks},
        "marker_mm": plan.scene.marker_mm,
        "marker_dictionary": plan.scene.marker_dictionary,
        "blurred_frames": list(plan.blur_bursts),
        "note": "synthetic; widths are exact by construction, see hairline.synth",
    }
    try:
        for i in range(plan.frames):
            t = i / max(1, plan.frames - 1)
            ox = plan.sweep_mm[0] + t * (plan.sweep_mm[1] - plan.sweep_mm[0])
            ox += float(rng.normal(0.0, plan.shake_mm))
            oy = float(rng.normal(0.0, plan.shake_mm * 0.6))
            camera = CameraSpec(
                image_size=plan.image_size,
                focal_px=plan.focal_px,
                distance_mm=plan.distance_mm[0]
                + t * (plan.distance_mm[1] - plan.distance_mm[0]),
                yaw_deg=plan.yaw_sweep_deg[0]
                + t * (plan.yaw_sweep_deg[1] - plan.yaw_sweep_deg[0])
                + float(rng.normal(0.0, 0.6)),
                pitch_deg=float(rng.normal(0.0, 1.2)),
                roll_deg=float(rng.normal(0.0, 0.8)),
                centre_offset_mm=(ox, oy),
            )
            blur = 6.0 if i in plan.blur_bursts else 0.9
            frame, _ = render(
                plan.scene,
                camera,
                RenderOptions(blur_px=blur, noise_dn=2.4, seed=plan.seed + i, jpeg_quality=None),
            )
            writer.write(frame)
    finally:
        writer.release()
    return manifest
