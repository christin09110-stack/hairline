"""Drawing the survey onto the frame: the measurement ladder and the title block.

`docs/design/atlas.md` specifies Hairline's signature element as a measurement
ladder: short perpendicular ticks along the crack's medial axis, each one a real
sample from the profile pass, with the 95th-percentile tick drawn longer and labelled
in millimetres. Under the frame sits a drawing scale bar in millimetres, and in the
corner a title block carrying the marker dictionary, the recovered scale with its
tolerance, the blur the deconvolution was calibrated with, and how far off square the
surface is.

The ticks are not decoration. Each one is drawn along the direction the profile was
actually sampled -- perpendicular to the crack on the wall, which under a tilt is not
perpendicular to it on screen -- and its length is the measured width scaled up so it
can be seen. An engineer checking the tool can look at a tick, look at the crack under
it, and see whether the tool sampled across the crack or along it.

Text is rendered with `cv2.FontFace`, new in OpenCV 5, using a bundled Barlow
Condensed. The legacy Hershey fonts render through a TrueType engine in OpenCV 5 and
look different from 4.x anyway, so there is no reason not to use a real face.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from .config import SurveyParams

if TYPE_CHECKING:  # pragma: no cover
    from .geometry import PlaneMap
    from .survey import CrackRecord, FrameGate

__all__ = ["draw_refusal_frame", "draw_survey_overlay", "palette"]

ASSETS = Path(__file__).parent / "assets"

# docs/design/atlas.md section 5, as BGR.
PALETTE: dict[str, tuple[int, int, int]] = {
    "ink": (29, 24, 18),
    "paper": (243, 248, 250),
    "raised": (230, 238, 241),
    "accent": (143, 97, 21),
    "caution": (6, 90, 138),
    "signal": (31, 50, 168),
    "dim": (112, 102, 94),
}


def palette(name: str) -> tuple[int, int, int]:
    return PALETTE[name]


@lru_cache(maxsize=4)
def _font(weight: int = 600) -> cv2.FontFace:
    path = ASSETS / f"BarlowCondensed-{weight}.ttf"
    if path.is_file():
        try:
            return cv2.FontFace(str(path))
        except cv2.error:  # pragma: no cover - a corrupt bundle should not stop a survey
            pass
    return cv2.FontFace("sans")


def _text(
    image: np.ndarray,
    origin: tuple[int, int],
    text: str,
    *,
    size: int = 20,
    colour: tuple[int, int, int] = PALETTE["ink"],
    weight: int = 600,
    halo: tuple[int, int, int] | None = PALETTE["paper"],
) -> tuple[int, int]:
    """Draw text with a halo so it survives being laid over grey concrete."""
    face = _font(weight)
    if halo is not None:
        cv2.putText(image, text, origin, halo, face, size, cv2.FILLED)
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            cv2.putText(image, text, (origin[0] + dx, origin[1] + dy), halo, face, size, cv2.FILLED)
    # OpenCV 5's Python `getTextSize` still only takes a legacy integer font id, so
    # the new `putText` overload's own return value is what gives a FontFace's extent.
    extent, _ = cv2.putText(image, text, origin, colour, face, size, cv2.FILLED)
    return int(extent[0]), int(extent[1])


def _band_colour(crack: CrackRecord) -> tuple[int, int, int]:
    if not crack.measurement.ok:
        return PALETTE["signal"]
    if crack.measurement.confidence == "low":
        return PALETTE["caution"]
    return PALETTE["accent"]


def _scale_for(image: np.ndarray, params: SurveyParams) -> float:
    longest = max(image.shape[:2])
    return min(1.0, params.overlay_max_side / longest) if longest > 0 else 1.0


# ---------------------------------------------------------------------------


def draw_survey_overlay(
    image: np.ndarray,
    cracks: list[CrackRecord],
    plane: PlaneMap,
    gate: FrameGate,
    params: SurveyParams,
    *,
    sigma_px: float,
    sigma_source: str,
    corners: list[np.ndarray] | None = None,
) -> np.ndarray:
    """The station frame with its ladder, scale bar and title block."""
    scale = _scale_for(image, params)
    canvas = (
        cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else image.copy()
    )
    _h, w = canvas.shape[:2]
    base = max(12, round(w / 78))

    if corners:
        for quad in corners:
            pts = (np.asarray(quad, dtype=np.float64) * scale).astype(np.int32)
            cv2.polylines(canvas, [pts], True, PALETTE["caution"], max(1, base // 8), cv2.LINE_AA)
            centre = pts.mean(axis=0).astype(int)
            _text(canvas, (int(centre[0]) - base, int(centre[1])),
                  f"{params.marker_length_mm:.0f} mm", size=base, colour=PALETTE["caution"])

    for crack in cracks:
        _draw_ladder(canvas, crack, scale, base)

    _draw_scale_bar(canvas, plane, scale, base)
    _draw_title_block(canvas, gate, params, sigma_px, sigma_source, cracks, base)
    return canvas


def _draw_ladder(
    canvas: np.ndarray, crack: CrackRecord, scale: float, base: int
) -> None:
    colour = _band_colour(crack)
    m = crack.measurement
    samples = m.samples
    thin = max(1, base // 10)

    if samples:
        # No line is drawn along the crack. The crack is the evidence, and an
        # inspector has to be able to see it under the annotation to check that the
        # tool measured a crack and not a shadow. The ticks alone carry the reading.

        # The ladder. Tick length is the measured width, magnified so a 0.4 mm crack
        # is visible on screen, with the magnification stated in the title block.
        widths = np.array([s.width_mm for s in samples])
        widest = float(np.percentile(widths, 95)) if widths.size else 0.0
        magnify = max(3.0, base * 1.6 / max(widest, 1e-6))
        step = max(1, len(samples) // 26)
        for s in samples[::step]:
            half = 0.5 * s.width_mm * magnify
            half = float(np.clip(half, base * 0.35, base * 2.2))
            nx, ny = s.normal_px
            x, y = s.x_px * scale, s.y_px * scale
            cv2.line(
                canvas,
                (int(x - nx * half), int(y - ny * half)),
                (int(x + nx * half), int(y + ny * half)),
                colour, thin, cv2.LINE_AA,
            )
        widest_idx = int(np.argmax(widths)) if widths.size else 0
        s = samples[widest_idx]
        nx, ny = s.normal_px
        x, y = s.x_px * scale, s.y_px * scale
        long_half = base * 2.8
        cv2.line(
            canvas,
            (int(x - nx * long_half), int(y - ny * long_half)),
            (int(x + nx * long_half), int(y + ny * long_half)),
            colour, max(2, thin + 1), cv2.LINE_AA,
        )
        label = (
            f"{crack.crack_id}  {m.p95_mm:.2f} mm "
            f"±{m.expanded_p95_mm:.2f}"
            if m.p95_mm is not None and m.expanded_p95_mm is not None
            else crack.crack_id
        )
        _place_label(
            canvas,
            (int(x + nx * long_half) + base // 2, int(y + ny * long_half)),
            label, size=int(base * 1.15), colour=colour,
        )
        return

    x, y, bw, bh = (int(v * scale) for v in crack.bbox_px)
    cv2.rectangle(canvas, (x, y), (x + bw, y + bh), colour, max(1, base // 10), cv2.LINE_AA)
    reason = m.refusal.code.replace("_", " ").lower() if m.refusal else "not measured"
    text = crack.crack_id + "  cannot measure"
    if m.upper_bound_mm is not None:
        text = f"{crack.crack_id}  finer than {m.upper_bound_mm:.2f} mm"
    _text(canvas, (x, max(base * 2, y - base // 2)), text,
          size=int(base * 1.1), colour=colour)
    _text(canvas, (x, y + bh + int(base * 1.4)), reason,
          size=int(base * 0.95), colour=PALETTE["dim"])


def _place_label(
    canvas: np.ndarray,
    origin: tuple[int, int],
    text: str,
    *,
    size: int,
    colour: tuple[int, int, int],
) -> None:
    """Draw a label, nudged back inside the frame if it would run off the edge."""
    h, w = canvas.shape[:2]
    width = max(1, int(len(text) * size * 0.46))
    x = min(max(int(size * 0.4), origin[0]), w - width - int(size * 0.4))
    y = min(max(size, origin[1]), h - int(size * 0.6))
    _text(canvas, (x, y), text, size=size, colour=colour)


def _draw_scale_bar(canvas: np.ndarray, plane: PlaneMap, scale: float, base: int) -> None:
    """A real drawing scale bar: 50 mm on the wall, measured where the bar is drawn."""
    h, w = canvas.shape[:2]
    y = h - int(base * 2.2)
    x0 = int(base * 1.6)
    mm_here = plane.to_mm(np.array([[x0 / scale, y / scale]]))[0]
    try:
        ppm = plane.px_per_mm(float(mm_here[0]), float(mm_here[1])) * scale
    except ValueError:
        return
    if ppm <= 0:
        return
    span_mm = 50.0
    while span_mm * ppm > w * 0.32 and span_mm > 5:
        span_mm /= 2.0
    length = int(span_mm * ppm)
    if length < 20:
        return
    cv2.rectangle(
        canvas,
        (x0 - base // 2, y - int(base * 1.9)),
        (x0 + length + base // 2, y + int(base * 0.9)),
        PALETTE["paper"], -1,
    )
    tick = max(4, base // 2)
    cv2.line(canvas, (x0, y), (x0 + length, y), PALETTE["ink"], max(2, base // 8), cv2.LINE_AA)
    for frac in (0.0, 0.5, 1.0):
        px = int(x0 + length * frac)
        cv2.line(canvas, (px, y - tick), (px, y + tick), PALETTE["ink"],
                 max(2, base // 8), cv2.LINE_AA)
    half = int(x0 + length * 0.5)
    cv2.rectangle(canvas, (x0, y - tick // 2), (half, y + tick // 2), PALETTE["ink"], -1)
    _text(canvas, (x0, y - int(base * 0.95)), "0", size=base, colour=PALETTE["ink"], halo=None)
    _text(canvas, (x0 + length - int(base * 1.8), y - int(base * 0.95)), f"{span_mm:.0f} mm",
          size=base, colour=PALETTE["ink"], halo=None)


def _draw_title_block(
    canvas: np.ndarray,
    gate: FrameGate,
    params: SurveyParams,
    sigma_px: float,
    sigma_source: str,
    cracks: list[CrackRecord],
    base: int,
) -> None:
    _h, w = canvas.shape[:2]
    ppm = gate.px_per_mm or 0.0
    tolerance = max(
        params.scale_floor_rel,
        (gate.residual_px or 0.0) / max(gate.marker_edge_px or 1.0, 1.0),
    )
    measured = sum(1 for c in cracks if c.measurement.ok)
    rows = [
        (
            "Scale reference",
            f"{params.marker_dictionary}  "
            f"id {', '.join(str(i) for i in gate.marker_ids) or '-'}",
        ),
        ("Recovered scale", f"{ppm:.3f} px/mm  ±{tolerance * 100:.2f}%"),
        ("Resolution", f"{1000.0 / ppm:.0f} µm per pixel" if ppm else "-"),
        ("Surface off square", f"{gate.apparent_tilt_deg:.0f}° apparent"
         if gate.apparent_tilt_deg is not None else "-"),
        ("Lens blur", f"{sigma_px:.2f} px std dev, from {sigma_source}"),
        ("Estimator", params.estimator.replace("_", " ")),
        ("Crack runs", f"{measured} measured, {len(cracks) - measured} declined"),
        ("Frame", f"{gate.index} at {gate.timestamp_ms / 1000.0:.2f} s"),
    ]
    pad = int(base * 0.9)
    line = int(base * 1.55)
    label_w = int(base * 9.0)
    block_w = int(base * 22.0)
    block_h = line * len(rows) + pad * 2 + int(base * 1.6)
    x0 = w - block_w - int(base * 1.4)
    y0 = int(base * 1.4)

    overlay = canvas.copy()
    cv2.rectangle(overlay, (x0, y0), (x0 + block_w, y0 + block_h), PALETTE["paper"], -1)
    cv2.addWeighted(overlay, 0.93, canvas, 0.07, 0, canvas)
    cv2.rectangle(canvas, (x0, y0), (x0 + block_w, y0 + block_h),
                  PALETTE["ink"], max(1, base // 10))

    y = y0 + pad + int(base * 1.25)
    _text(canvas, (x0 + pad, y), "Hairline crack survey",
          size=int(base * 1.4), colour=PALETTE["ink"], halo=None)
    y += int(base * 0.55)
    cv2.line(canvas, (x0 + pad, y), (x0 + block_w - pad, y), PALETTE["ink"], 1)
    y += line
    for label, value in rows:
        _text(canvas, (x0 + pad, y), label, size=base,
              colour=PALETTE["dim"], weight=500, halo=None)
        _text(canvas, (x0 + pad + label_w, y), value, size=base,
              colour=PALETTE["ink"], halo=None)
        y += line


def draw_refusal_frame(
    image: np.ndarray, code: str, message: str, params: SurveyParams
) -> np.ndarray:
    """The picture for the screen that says Hairline will not give you a number."""
    scale = _scale_for(image, params)
    canvas = (
        cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else image.copy()
    )
    h, w = canvas.shape[:2]
    base = max(12, round(w / 64))

    veil = canvas.copy()
    cv2.rectangle(veil, (0, 0), (w, h), PALETTE["paper"], -1)
    cv2.addWeighted(veil, 0.55, canvas, 0.45, 0, canvas)

    band_h = int(base * 7.5)
    y0 = (h - band_h) // 2
    strip = canvas.copy()
    cv2.rectangle(strip, (0, y0), (w, y0 + band_h), PALETTE["paper"], -1)
    cv2.addWeighted(strip, 0.96, canvas, 0.04, 0, canvas)
    cv2.rectangle(canvas, (0, y0), (w, y0 + band_h), PALETTE["signal"], max(2, base // 6))

    _text(canvas, (int(base * 1.6), y0 + int(base * 2.4)), "Cannot measure here",
          size=int(base * 2.0), colour=PALETTE["signal"], halo=None)
    _text(canvas, (int(base * 1.6), y0 + int(base * 3.9)), code.replace("_", " ").lower(),
          size=int(base * 1.1), colour=PALETTE["dim"], weight=500, halo=None)

    words = message.split()
    lines: list[str] = []
    current = ""
    limit = max(28, int(w / (base * 0.52)))
    for word in words:
        if len(current) + len(word) + 1 > limit:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    y = y0 + int(base * 5.3)
    for text in lines[:2]:
        _text(canvas, (int(base * 1.6), y), text, size=int(base * 1.05),
              colour=PALETTE["ink"], weight=500, halo=None)
        y += int(base * 1.35)
    return canvas
