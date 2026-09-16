"""Printable sheets: the scale marker, and a target of known widths to check it against.

Two things get printed.

`marker_sheet` is the scale reference an inspector tapes to the wall. It carries the
ArUco square, its nominal edge in millimetres, and the one instruction that decides
whether the survey works: measure the printed square with a rule and enter what you
measured, because a printer that scales a page by 4% puts a 4% error straight into
every width in the report, and nothing downstream can detect it.

`calibration_target` is how anyone checks Hairline without taking our word for it. It
prints lines of declared width from 0.10 mm to 2.00 mm beside the same marker.
Photograph it, run it through the tool, and compare. Print fidelity is the limit --
at 600 dpi one dot is 0.042 mm, so a 0.10 mm line is between two and three dots and
will not come out at exactly 0.10 -- so the sheet prints the dot pitch it was
generated for and the report says the residual belongs to the printer as much as to
the measurement. It is a check on the instrument, not a certified artefact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from visioncore.calibration import ARUCO_DICTS

__all__ = ["TargetSpec", "calibration_target", "marker_sheet"]

A4_MM = (210.0, 297.0)
DEFAULT_WIDTHS_MM = (0.10, 0.15, 0.20, 0.30, 0.50, 0.75, 1.00, 1.50, 2.00)


@dataclass(frozen=True)
class TargetSpec:
    page_mm: tuple[float, float] = A4_MM
    dpi: int = 600
    marker_id: int = 7
    marker_mm: float = 60.0
    dictionary: str = "DICT_4X4_50"
    widths_mm: tuple[float, ...] = DEFAULT_WIDTHS_MM
    line_length_mm: float = 90.0
    compact: bool = False
    """A small card the whole of which fits in one close-up frame.

    The A4 sheet is for a person with a ruler. The compact card is for the build:
    the calibration gate photographs it synthetically at a stand-off where the
    coarse lines are resolvable and the fine ones are not, and checks both halves
    of the contract in one frame."""

    @property
    def px_per_mm(self) -> float:
        return self.dpi / 25.4

    @property
    def dot_mm(self) -> float:
        return 25.4 / self.dpi


def _canvas(spec: TargetSpec) -> np.ndarray:
    w = round(spec.page_mm[0] * spec.px_per_mm)
    h = round(spec.page_mm[1] * spec.px_per_mm)
    return np.full((h, w), 255, dtype=np.uint8)


def _font() -> cv2.FontFace:
    from .overlay import ASSETS

    path = ASSETS / "BarlowCondensed-600.ttf"
    return cv2.FontFace(str(path)) if path.is_file() else cv2.FontFace("sans")


def _label(page: np.ndarray, spec: TargetSpec, x_mm: float, y_mm: float, text: str,
           pt: float = 9.0) -> None:
    size = round(pt / 72.0 * spec.dpi)
    cv2.putText(
        page, text,
        (round(x_mm * spec.px_per_mm), round(y_mm * spec.px_per_mm)),
        (0, 0, 0), _font(), size, cv2.FILLED,
    )


def _place_marker(page: np.ndarray, spec: TargetSpec, x_mm: float, y_mm: float) -> None:
    side = round(spec.marker_mm * spec.px_per_mm)
    adict = cv2.aruco.getPredefinedDictionary(ARUCO_DICTS[spec.dictionary])
    marker = cv2.aruco.generateImageMarker(adict, spec.marker_id, side)
    x = round(x_mm * spec.px_per_mm)
    y = round(y_mm * spec.px_per_mm)
    page[y : y + side, x : x + side] = marker
    # Corner crosses at the true corners, so the printed edge can be measured with a rule.
    arm = round(4.0 * spec.px_per_mm)
    for cx, cy in ((x, y), (x + side, y), (x, y + side), (x + side, y + side)):
        cv2.line(page, (cx - arm, cy), (cx + arm, cy), 0, 2)
        cv2.line(page, (cx, cy - arm), (cx, cy + arm), 0, 2)


def _compact_target(spec: TargetSpec) -> tuple[np.ndarray, dict[str, Any]]:
    """The build's own card: marker top-left, the lines beneath it, all in one frame."""
    page = _canvas(spec)
    _place_marker(page, spec, 8.0, 8.0)
    _label(page, spec, 8.0 + spec.marker_mm + 6.0, 16.0, "Hairline calibration card", pt=9)
    _label(page, spec, 8.0 + spec.marker_mm + 6.0, 24.0,
           f"{spec.dictionary} id {spec.marker_id}, {spec.marker_mm:.0f} mm", pt=7)
    _label(page, spec, 8.0 + spec.marker_mm + 6.0, 31.0,
           f"drawn at {spec.dpi} dpi, one dot = {spec.dot_mm:.4f} mm", pt=7)

    drawn: list[dict[str, Any]] = []
    top = 8.0 + spec.marker_mm + 14.0
    pitch = (spec.page_mm[1] - top - 8.0) / max(1, len(spec.widths_mm))
    x_mm = 8.0
    for i, width_mm in enumerate(spec.widths_mm):
        width_px = max(1, round(width_mm * spec.px_per_mm))
        actual_mm = width_px * spec.dot_mm
        y_mm = top + i * pitch
        x0 = round(x_mm * spec.px_per_mm)
        x1 = round((x_mm + spec.line_length_mm) * spec.px_per_mm)
        y0 = round(y_mm * spec.px_per_mm)
        page[y0 : y0 + width_px, x0:x1] = 0
        _label(page, spec, x_mm + spec.line_length_mm + 4.0, y_mm + 1.5,
               f"{actual_mm:.3f} mm", pt=7)
        drawn.append(
            {
                "nominal_mm": width_mm,
                "printed_mm": round(actual_mm, 7),
                "dots": width_px,
                "y_mm": round(y_mm, 3),
                "x0_mm": x_mm,
                "x1_mm": x_mm + spec.line_length_mm,
            }
        )
    return page, {
        "dpi": spec.dpi,
        "dot_mm": round(spec.dot_mm, 8),
        "compact": True,
        "marker": {
            "dictionary": spec.dictionary,
            "id": spec.marker_id,
            "nominal_mm": spec.marker_mm,
            "top_left_mm": [8.0, 8.0],
        },
        "lines": drawn,
        "page_mm": list(spec.page_mm),
    }


def marker_sheet(spec: TargetSpec | None = None) -> np.ndarray:
    """The scale reference, ready to print at 100% and tape to the wall."""
    spec = spec or TargetSpec()
    page = _canvas(spec)
    _label(page, spec, 20, 24, "Hairline scale reference", pt=18)
    _place_marker(page, spec, 20.0, 34.0)
    y = 34.0 + spec.marker_mm + 14.0
    lines = [
        f"{spec.dictionary}, marker id {spec.marker_id}, nominal edge "
        f"{spec.marker_mm:.0f} mm between the corner crosses.",
        "",
        "Print at 100 percent. Do not let the driver fit to page.",
        "Then measure the printed edge between the crosses with a steel rule and enter",
        "that measurement, not the nominal one. A printer that scales the page by four",
        "percent puts four percent into every width in the report, and nothing further",
        "down the pipeline can see it.",
        "",
        "Tape the sheet flat against the same surface as the crack, as close to it as",
        "you can, and keep it in the frame. Hairline refuses to report a width from any",
        "frame where this marker is not visible, is under 48 pixels across, or does not",
        "fit a plane to within two pixels.",
    ]
    for line in lines:
        _label(page, spec, 20, y, line, pt=10.5)
        y += 6.0
    _label(page, spec, 20, spec.page_mm[1] - 12, f"generated at {spec.dpi} dpi", pt=8)
    return page


COMPACT = TargetSpec(
    page_mm=(150.0, 110.0),
    dpi=1200,
    marker_mm=34.0,
    widths_mm=(0.15, 0.25, 0.40, 0.70, 1.20),
    line_length_mm=96.0,
    compact=True,
)


def calibration_target(spec: TargetSpec | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Lines of declared width beside the marker, plus the manifest of what was drawn."""
    spec = spec or TargetSpec()
    if spec.compact:
        return _compact_target(spec)
    page = _canvas(spec)
    _label(page, spec, 20, 22, "Hairline calibration target", pt=18)
    _place_marker(page, spec, 20.0, 30.0)
    _label(page, spec, 20 + spec.marker_mm + 10, 44,
           f"{spec.dictionary} id {spec.marker_id}", pt=11)
    _label(page, spec, 20 + spec.marker_mm + 10, 52,
           f"nominal edge {spec.marker_mm:.0f} mm", pt=11)
    _label(page, spec, 20 + spec.marker_mm + 10, 60,
           "measure the printed edge and enter that", pt=11)

    drawn: list[dict[str, Any]] = []
    y_mm = 30.0 + spec.marker_mm + 22.0
    x_mm = 26.0
    for width_mm in spec.widths_mm:
        width_px = max(1, round(width_mm * spec.px_per_mm))
        actual_mm = width_px * spec.dot_mm
        x0 = round(x_mm * spec.px_per_mm)
        x1 = round((x_mm + spec.line_length_mm) * spec.px_per_mm)
        y0 = round(y_mm * spec.px_per_mm)
        page[y0 : y0 + width_px, x0:x1] = 0
        _label(page, spec, x_mm + spec.line_length_mm + 6, y_mm + 2.0,
               f"{width_mm:.2f} mm nominal", pt=10)
        _label(page, spec, x_mm + spec.line_length_mm + 40, y_mm + 2.0,
               f"{actual_mm:.3f} mm as printed ({width_px} dots)", pt=10)
        drawn.append(
            {
                "nominal_mm": width_mm,
                "printed_mm": round(actual_mm, 4),
                "dots": width_px,
                "y_mm": round(y_mm, 2),
            }
        )
        y_mm += 16.0

    note_y = y_mm + 8.0
    for line in (
        f"Drawn at {spec.dpi} dpi, so one dot is {spec.dot_mm:.4f} mm and every line is a",
        "whole number of dots. The 'as printed' column is the width the file contains;",
        "ink spread on paper makes the sheet in your hand wider than that by an amount",
        "this file cannot know. Compare Hairline against the printed column, and read any",
        "residual bias as belonging to the printer as much as to the measurement.",
    ):
        _label(page, spec, 20, note_y, line, pt=10.5)
        note_y += 6.0

    manifest = {
        "dpi": spec.dpi,
        "dot_mm": round(spec.dot_mm, 5),
        "marker": {
            "dictionary": spec.dictionary,
            "id": spec.marker_id,
            "nominal_mm": spec.marker_mm,
        },
        "lines": drawn,
        "page_mm": list(spec.page_mm),
    }
    return page, manifest
