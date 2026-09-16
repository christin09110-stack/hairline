"""The survey: video or still in, a measured crack schedule out.

Refusal is the design, not the error path
-----------------------------------------
Almost every frame of a real walk-past is unusable: the operator was moving, the
marker left the frame, the autofocus hunted, the wall went into shadow. A tool that
tries to measure them anyway produces a schedule of numbers with no way to tell the
good rows from the bad, which is worse than producing nothing.

So every frame passes a gate before any measurement happens, and every gate failure
is recorded with a reason and a count. A frame is used only if the marker is present,
large enough, fitted to within the residual limit, not too foreshortened, in focus
relative to the rest of the clip, and correctly exposed. The run record carries both
the frames that were used and a tally of why the rest were not, so the report can say
"nineteen of twenty-eight frames were rejected, seventeen of them for focus" instead
of quietly averaging the bad ones in.

Stations, not frames
--------------------
A walk-past sees the same crack from many positions. Reporting it once per frame
would inflate the schedule and double-count. Frames are grouped into stations by how
far the camera moved along the wall, measured in millimetres through the marker
homography rather than in pixels, and the sharpest frame at each station is the one
measured.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from visioncore import (
    Evidence,
    Frame,
    Refusal,
    RunRecord,
    calibrate_from_aruco,
    calibrate_from_checkerboard,
    detect_aruco,
    encode_jpeg,
    iter_video,
    read_image,
    recording,
    sharpness,
    stage,
    to_gray,
    video_info,
)

from .config import SurveyParams
from .geometry import PlaneMap
from .overlay import draw_refusal_frame, draw_survey_overlay
from .segment import segment_cracks
from .width import CrackMeasurement, estimate_psf_sigma, measure_component

__all__ = ["CrackRecord", "FrameGate", "SurveyResult", "analyse_path", "survey"]

Progress = Callable[[float, str], None]
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


# ---------------------------------------------------------------------------


@dataclass
class FrameGate:
    """Whether one frame may be measured, and if not, exactly why."""

    index: int
    timestamp_ms: float
    ok: bool
    sharpness: float
    mean_level: float
    clipped_fraction: float
    px_per_mm: float | None = None
    apparent_tilt_deg: float | None = None
    residual_px: float | None = None
    marker_edge_px: float | None = None
    marker_ids: tuple[int, ...] = ()
    station_mm: tuple[float, float] | None = None
    refusal: Refusal | None = None

    def to_dict(self) -> dict[str, Any]:
        def r(v: float | None, n: int = 2) -> float | None:
            return None if v is None else round(float(v), n)

        return {
            "frame": self.index,
            "t_ms": round(self.timestamp_ms, 1),
            "ok": self.ok,
            "sharpness": r(self.sharpness, 1),
            "mean_level": r(self.mean_level, 1),
            "clipped_fraction": r(self.clipped_fraction, 4),
            "px_per_mm": r(self.px_per_mm, 3),
            "apparent_tilt_deg": r(self.apparent_tilt_deg),
            "residual_px": r(self.residual_px, 3),
            "marker_edge_px": r(self.marker_edge_px, 1),
            "marker_ids": list(self.marker_ids),
            "station_mm": None
            if self.station_mm is None
            else [round(self.station_mm[0], 1), round(self.station_mm[1], 1)],
            "refusal": self.refusal.to_dict() if self.refusal else None,
        }


@dataclass
class CrackRecord:
    """One measured crack run, as it appears in the schedule."""

    crack_id: str
    frame_index: int
    timestamp_ms: float
    measurement: CrackMeasurement
    bbox_px: tuple[int, int, int, int]
    centroid_mm: tuple[float, float]
    contrast_dn: float
    band: str
    evidence_uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        m = self.measurement
        return {
            "crack_id": self.crack_id,
            "frame": self.frame_index,
            "t_ms": round(self.timestamp_ms, 1),
            "position_mm": [round(self.centroid_mm[0], 1), round(self.centroid_mm[1], 1)],
            "bbox_px": list(self.bbox_px),
            "contrast_dn": round(self.contrast_dn, 1),
            "band": self.band,
            "measurable": m.ok,
            "width_p50_mm": None if m.p50_mm is None else round(m.p50_mm, 3),
            "width_p95_mm": None if m.p95_mm is None else round(m.p95_mm, 3),
            "expanded_uncertainty_mm": None
            if m.expanded_p95_mm is None
            else round(m.expanded_p95_mm, 3),
            "upper_bound_mm": None if m.upper_bound_mm is None else round(m.upper_bound_mm, 3),
            "length_mm": None if m.length_mm is None else round(m.length_mm, 1),
            "samples": len(m.samples),
            "confidence": m.confidence,
            "refusal": m.refusal.to_dict() if m.refusal else None,
            "detail": m.to_dict(),
            "evidence": self.evidence_uri,
        }


@dataclass
class SurveyResult:
    record: RunRecord
    cracks: list[CrackRecord] = field(default_factory=list)
    gates: list[FrameGate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Frame gate
# ---------------------------------------------------------------------------


def _exposure(gray: np.ndarray) -> tuple[float, float]:
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
    total = float(hist.sum()) or 1.0
    clipped = float(hist[0] + hist[1] + hist[254] + hist[255]) / total
    mean = float(np.dot(hist, np.arange(256)) / total)
    return mean, clipped


def gate_frame(
    frame: Frame, params: SurveyParams, *, focus_reference: float
) -> tuple[FrameGate, PlaneMap | None, list[np.ndarray]]:
    """Decide whether this frame can carry a measurement."""
    gray = to_gray(frame.image)
    sharp = sharpness(gray)
    mean_level, clipped = _exposure(gray)

    def refuse(code: str, message: str, **details: Any) -> tuple[FrameGate, None, list]:
        return (
            FrameGate(
                index=frame.index,
                timestamp_ms=frame.timestamp_ms,
                ok=False,
                sharpness=sharp,
                mean_level=mean_level,
                clipped_fraction=clipped,
                refusal=Refusal(code, message, details),
            ),
            None,
            [],
        )

    if sharp < params.min_absolute_focus:
        return refuse(
            "OUT_OF_FOCUS",
            f"nothing in this frame is in focus (detail score {sharp:.1f}, "
            f"floor {params.min_absolute_focus:.0f})",
            sharpness=round(sharp, 2),
        )
    if focus_reference > 0 and sharp < params.min_focus_ratio * focus_reference:
        return refuse(
            "MOTION_BLUR",
            f"this frame is {sharp / focus_reference:.0%} as sharp as the best frame "
            f"in the clip; the camera was moving",
            sharpness=round(sharp, 2),
            reference=round(focus_reference, 2),
        )
    if mean_level < params.min_mean_level:
        return refuse(
            "UNDER_EXPOSED",
            f"the frame is too dark to separate a crack from the surface "
            f"(mean level {mean_level:.0f})",
            mean_level=round(mean_level, 1),
        )
    if mean_level > params.max_mean_level:
        return refuse(
            "OVER_EXPOSED",
            f"the frame is washed out (mean level {mean_level:.0f})",
            mean_level=round(mean_level, 1),
        )
    if clipped > params.max_clipped_fraction:
        return refuse(
            "CLIPPED",
            f"{clipped:.0%} of the frame is pure black or pure white; detail there "
            f"is gone and cannot be recovered",
            clipped_fraction=round(clipped, 4),
        )

    corners, ids = detect_aruco(frame.image, params.marker_dictionary)
    if corners:
        cal = calibrate_from_aruco(
            frame.image,
            params.marker_length_mm,
            dictionary=params.marker_dictionary,
            min_marker_px=params.min_marker_px,
            max_residual_px=params.max_residual_px,
            max_obliquity_deg=90.0,  # tilt is judged below, from the homography
        )
    elif params.checkerboard and params.square_size_mm:
        cal = calibrate_from_checkerboard(
            frame.image,
            params.checkerboard,
            params.square_size_mm,
            min_marker_px=params.min_marker_px,
            max_residual_px=params.max_residual_px,
            max_obliquity_deg=90.0,
        )
    else:
        return refuse(
            "NO_MARKER",
            f"no {params.marker_dictionary} marker in this frame. Hairline cannot "
            f"convert pixels to millimetres without a printed reference of known size "
            f"in the same plane as the surface.",
            dictionary=params.marker_dictionary,
        )

    if not cal.ok:
        r = cal.refusal or Refusal("NO_FIDUCIAL", "calibration failed")
        return refuse(r.code, r.message, **r.details)

    try:
        plane = PlaneMap(cal.homography_mm_to_px)
    except ValueError as exc:
        return refuse("DEGENERATE", f"the marker plane is ill-conditioned: {exc}")

    tilt = plane.apparent_tilt_deg(0.0, 0.0)
    marker_edge = (
        min(float(np.linalg.norm(corners[0][(i + 1) % 4] - corners[0][i])) for i in range(4))
        if corners
        else cal.min_feature_px
    )

    gate = FrameGate(
        index=frame.index,
        timestamp_ms=frame.timestamp_ms,
        ok=True,
        sharpness=sharp,
        mean_level=mean_level,
        clipped_fraction=clipped,
        px_per_mm=cal.px_per_mm,
        apparent_tilt_deg=tilt,
        residual_px=cal.residual_px,
        marker_edge_px=marker_edge,
        marker_ids=tuple(ids),
    )

    if tilt > params.max_apparent_tilt_deg:
        gate.ok = False
        gate.refusal = Refusal(
            "TOO_OBLIQUE",
            f"the surface is {tilt:.0f} degrees off square to the camera "
            f"(limit {params.max_apparent_tilt_deg:.0f}). Foreshortening at this angle "
            f"costs more resolution across the crack than the measurement can afford. "
            f"Stand square to the wall and re-shoot.",
            details={"apparent_tilt_deg": round(tilt, 1)},
        )
        return gate, None, corners

    # Where on the wall the camera is pointing, so frames can be grouped into stations.
    h, w = gray.shape[:2]
    centre_mm = plane.to_mm(np.array([[w / 2.0, h / 2.0]]))[0]
    gate.station_mm = (float(centre_mm[0]), float(centre_mm[1]))
    return gate, plane, corners


# ---------------------------------------------------------------------------
# Survey
# ---------------------------------------------------------------------------


def _band_for(measurement: CrackMeasurement, params: SurveyParams) -> str:
    if not measurement.ok or measurement.p95_mm is None:
        return "Not measured"
    for band in params.bands:
        if band.upper_mm is None or measurement.p95_mm <= band.upper_mm:
            return band.label
    return params.bands[-1].label


def _load_frames(path: Path, params: SurveyParams) -> tuple[list[Frame], dict[str, Any]]:
    if path.suffix.lower() in VIDEO_SUFFIXES:
        info = video_info(path)
        frames = list(
            iter_video(path, stride=max(1, params.frame_stride), max_frames=400)
        )
        return frames, {"kind": "video", **info.to_dict()}
    image = read_image(path)
    h, w = image.shape[:2]
    return (
        [Frame(index=0, timestamp_ms=0.0, image=image, source=str(path))],
        {"kind": "image", "width": w, "height": h},
    )


def survey(
    path: str | Path,
    params: SurveyParams | None = None,
    *,
    record: RunRecord | None = None,
    progress: Progress | None = None,
    save_evidence: Callable[[str, bytes], str] | None = None,
) -> SurveyResult:
    """Run the whole pipeline over a video or a still and return the schedule."""
    params = params or SurveyParams()
    record = record or RunRecord(product="hairline")
    report = progress or (lambda pct, msg: None)
    source = Path(path)

    # `stage()` only records into an active record, and the CLI and tests call
    # survey() outside servicekit's job runner, so bind it here too. Nesting is
    # harmless: the inner binding restores the outer one on exit.
    with recording(record):
        return _survey(source, params, record, report, save_evidence)


def _survey(
    source: Path,
    params: SurveyParams,
    record: RunRecord,
    report: Progress,
    save_evidence: Callable[[str, bytes], str] | None,
) -> SurveyResult:
    report(3, "reading the clip")
    with stage("decode"):
        frames, source_info = _load_frames(source, params)
    record.input.update(source_info)
    record.params.update(params.to_dict())
    if not frames:
        record.refuse("EMPTY_INPUT", "no frames could be decoded from this file")
        return SurveyResult(record=record)

    report(10, f"scoring {len(frames)} frames for focus")
    with stage("focus_scan"):
        sharpness_by_index = {f.index: sharpness(to_gray(f.image)) for f in frames}
    focus_reference = max(sharpness_by_index.values()) if sharpness_by_index else 0.0

    report(16, "checking each frame for a usable scale reference")
    gates: list[FrameGate] = []
    usable: list[tuple[Frame, FrameGate, PlaneMap, list[np.ndarray]]] = []
    with stage("frame_gate"):
        for i, frame in enumerate(frames):
            gate, plane, corners = gate_frame(frame, params, focus_reference=focus_reference)
            gates.append(gate)
            if gate.ok and plane is not None:
                usable.append((frame, gate, plane, corners))
            if i % 5 == 0:
                report(16 + 14 * i / max(1, len(frames)), f"frame {i + 1} of {len(frames)}")

    reasons: dict[str, int] = {}
    for gate in gates:
        if not gate.ok and gate.refusal:
            reasons[gate.refusal.code] = reasons.get(gate.refusal.code, 0) + 1
    record.metrics["frames_read"] = len(frames)
    record.metrics["frames_usable"] = len(usable)
    record.metrics["frames_rejected"] = dict(reasons)

    if not usable:
        report(100, "cannot measure")
        top = max(reasons, key=reasons.get) if reasons else "NO_MARKER"
        detail = next(
            (g.refusal for g in gates if g.refusal and g.refusal.code == top), None
        )
        record.refuse(
            top,
            detail.message
            if detail
            else "no frame in this clip carries a usable scale reference",
            frames_read=len(frames),
            by_reason=reasons,
        )
        if save_evidence is not None and frames:
            worst = frames[len(frames) // 2]
            overlay = draw_refusal_frame(
                worst.image, top, detail.message if detail else "", params
            )
            uri = save_evidence("000-cannot-measure.jpg", encode_jpeg(overlay, 86))
            record.add_evidence(
                Evidence(
                    label="cannot measure",
                    kind="overlay",
                    uri=uri,
                    frame_index=worst.index,
                    caption=detail.message if detail else "",
                )
            )
        record.metrics["gates"] = [g.to_dict() for g in gates]
        return SurveyResult(record=record, gates=gates)

    # --- one frame per station, the sharpest -----------------------------
    stations: list[tuple[Frame, FrameGate, PlaneMap, list[np.ndarray]]] = []
    for item in usable:
        frame, gate, _, _ = item
        near = None
        for j, chosen in enumerate(stations):
            if (
                gate.station_mm
                and chosen[1].station_mm
                and math.dist(gate.station_mm, chosen[1].station_mm)
                < params.min_station_shift_mm
            ):
                near = j
                break
        if near is None:
            stations.append(item)
        elif sharpness_by_index[frame.index] > sharpness_by_index[stations[near][0].index]:
            stations[near] = item
    stations = stations[: params.max_frames]
    record.metrics["stations"] = len(stations)

    cracks: list[CrackRecord] = []
    crack_index = 0
    for n, (frame, gate, plane, corners) in enumerate(stations):
        pct = 32 + 58 * n / max(1, len(stations))
        report(pct, f"station {n + 1} of {len(stations)}: finding cracks")

        gray = to_gray(frame.image)
        with stage("psf"):
            sigma_px, sigma_source = estimate_psf_sigma(gray, corners)
            sigma_u = _sigma_uncertainty(gray, corners, sigma_px)

        with stage("segment"):
            seg = segment_cracks(
                frame.image,
                params,
                plane=plane,
                px_per_mm=gate.px_per_mm or 1.0,
                marker_corners=corners,
            )
        record.metrics.setdefault("segmentation", []).append(
            {"frame": frame.index, **seg.to_dict()}
        )

        scale_rel = max(
            params.scale_floor_rel,
            (gate.residual_px or 0.0) / max(gate.marker_edge_px or 1.0, 1.0),
        )

        frame_cracks: list[CrackRecord] = []
        with stage("measure"):
            for comp in seg.components:
                if crack_index >= params.max_cracks_reported:
                    break
                measurement = measure_component(
                    frame.image,
                    comp,
                    plane,
                    params,
                    sigma_px=sigma_px,
                    sigma_uncertainty_px=sigma_u,
                    scale_rel_uncertainty=scale_rel,
                    working_distance_mm=params.working_distance_mm,
                    surface_sigma_dn=seg.residual_sigma,
                )
                if (
                    not measurement.ok
                    and measurement.refusal
                    and measurement.refusal.code in {"TOO_FEW_SAMPLES", "TOO_FAINT"}
                ):
                    continue  # not a crack, not worth a row in the schedule
                crack_index += 1
                centroid_mm = plane.to_mm(np.array([comp.centroid_px]))[0]
                frame_cracks.append(
                    CrackRecord(
                        crack_id=f"C{crack_index:03d}",
                        frame_index=frame.index,
                        timestamp_ms=frame.timestamp_ms,
                        measurement=measurement,
                        bbox_px=comp.bbox,
                        centroid_mm=(float(centroid_mm[0]), float(centroid_mm[1])),
                        contrast_dn=comp.contrast_dn,
                        band=_band_for(measurement, params),
                    )
                )

        if save_evidence is not None:
            with stage("overlay"):
                overlay = draw_survey_overlay(
                    frame.image, frame_cracks, plane, gate, params,
                    sigma_px=sigma_px, sigma_source=sigma_source, corners=corners,
                )
                uri = save_evidence(
                    f"{n:03d}-station.jpg", encode_jpeg(overlay, 88)
                )
            for crack in frame_cracks:
                crack.evidence_uri = uri
            record.add_evidence(
                Evidence(
                    label=f"station {n + 1}",
                    kind="overlay",
                    uri=uri,
                    frame_index=frame.index,
                    timestamp_ms=frame.timestamp_ms,
                    caption=(
                        f"{len(frame_cracks)} crack runs, scale "
                        f"{gate.px_per_mm:.2f} px/mm, blur sigma {sigma_px:.2f} px "
                        f"from {sigma_source}"
                    ),
                    metrics={
                        "px_per_mm": round(gate.px_per_mm or 0.0, 3),
                        "apparent_tilt_deg": round(gate.apparent_tilt_deg or 0.0, 1),
                        "sigma_px": round(sigma_px, 2),
                    },
                )
            )
        cracks.extend(frame_cracks)

    report(94, "building the schedule")
    measured = [c for c in cracks if c.measurement.ok]
    widths = [c.measurement.p95_mm for c in measured if c.measurement.p95_mm is not None]
    record.metrics.update(
        {
            "cracks_found": len(cracks),
            "cracks_measured": len(measured),
            "cracks_unmeasurable": len(cracks) - len(measured),
            "widest_p95_mm": round(max(widths), 3) if widths else None,
            "total_length_mm": round(
                sum(c.measurement.length_mm or 0.0 for c in measured), 1
            ),
            "gates": [g.to_dict() for g in gates],
        }
    )
    record.results = [c.to_dict() for c in cracks]
    for crack in cracks:
        if crack.measurement.refusal:
            record.refusals.append(crack.measurement.refusal)
    if not measured and cracks:
        record.warn(
            "cracks were found but none could be measured at this scale; "
            "re-shoot closer so each crack spans more pixels"
        )
    report(100, "done")
    return SurveyResult(record=record, cracks=cracks, gates=gates)


def _sigma_uncertainty(
    gray: np.ndarray, corners: list[np.ndarray], sigma_px: float
) -> float:
    """Standard error of the blur estimate, from the spread across marker edges."""
    from .width import _sigma_from_edge

    if not corners:
        return 0.25
    readings: list[float] = []
    offsets = np.arange(-7.0, 7.2, 0.2, dtype=np.float32)
    for quad in corners:
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
                xs = (base[0] + normal[0] * offsets).astype(np.float32)
                ys = (base[1] + normal[1] * offsets).astype(np.float32)
                profile = cv2.remap(
                    gray, xs.reshape(1, -1), ys.reshape(1, -1), cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE,
                ).astype(np.float64).ravel()
                value = _sigma_from_edge(profile, 0.2)
                if value is not None and 0.15 < value < 6.0:
                    readings.append(value)
    if len(readings) < 4:
        return max(0.2, 0.25 * sigma_px)
    arr = np.asarray(readings)
    mad = float(np.median(np.abs(arr - np.median(arr))))
    return max(0.05, 1.4826 * mad / math.sqrt(len(arr)))


def analyse_path(
    path: str | Path,
    params: SurveyParams | None = None,
    **kwargs: Any,
) -> SurveyResult:
    """Convenience wrapper used by the CLI and the tests."""
    return survey(path, params, **kwargs)
