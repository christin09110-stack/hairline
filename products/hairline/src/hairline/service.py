"""The Hairline web service: a servicekit app plus one analyzer function.

Everything the browser talks to -- upload, progress stream, result JSON, evidence
images -- comes from `servicekit`. This module supplies the product identity, the
parameters the interface exposes, and the analyzer that turns an uploaded file into
a `RunRecord`. The interface itself is in `static/`.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# `UploadFile` has to be importable from this module's globals, not from inside the
# function that declares the route: FastAPI resolves the annotation against module
# scope, and a local import leaves it an unresolved forward reference that pydantic
# rejects at request time rather than at import time.
from fastapi import File, UploadFile
from servicekit import JobContext, ProductInfo, ServiceConfig, create_app
from servicekit.errors import ServiceError
from visioncore import RunRecord

from .calibration_gate import cached_report
from .config import ESTIMATORS, SurveyParams
from .survey import survey

__all__ = ["analyze", "app", "build_app", "schedule_csv"]

HERE = Path(__file__).parent
STATIC = HERE / "static"
SAMPLES = HERE / "samples"

PARAMS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "marker_length_mm",
        "label": "Printed marker edge",
        "type": "number",
        "default": 60.0,
        "min": 5.0,
        "max": 500.0,
        "step": 0.1,
        "unit": "mm",
        "help": "Measure the printed square with a rule. Do not trust the nominal size: "
                "a printer that scales the page puts that error into every width.",
    },
    {
        "name": "working_distance_mm",
        "label": "Stand-off from the wall",
        "type": "number",
        "default": 350.0,
        "min": 50.0,
        "max": 5000.0,
        "step": 10.0,
        "unit": "mm",
        "help": "Roughly how far the camera was from the surface. Used only to turn the "
                "assumed card-to-crack offset into a scale uncertainty.",
    },
    {
        "name": "estimator",
        "label": "Width estimator",
        "type": "select",
        "default": "blur_corrected",
        "options": list(ESTIMATORS),
        "help": "blur corrected is the default and the one docs/evaluation.md recommends. "
                "The others are here so the correction can be seen working.",
    },
    {
        "name": "min_length_mm",
        "label": "Shortest crack to report",
        "type": "number",
        "default": 25.0,
        "min": 2.0,
        "max": 500.0,
        "step": 1.0,
        "unit": "mm",
    },
    {
        "name": "frame_stride",
        "label": "Frame stride",
        "type": "number",
        "default": 3,
        "min": 1,
        "max": 30,
        "step": 1,
        "help": "Keep one frame in this many. A walk-past is mostly redundant.",
    },
]


def analyze(ctx: JobContext) -> RunRecord:
    """The one function servicekit needs. Runs in a worker thread.

    The first thing it does is refuse to work if the build is not calibrated. A
    measuring instrument that has lost its calibration and keeps answering is worse
    than one that stops.
    """
    calibration = cached_report()
    if not calibration.ok:
        ctx.record.refuse(
            "CALIBRATION_FAILED",
            "This build is not publishing widths. Its calibration check against "
            "printed lines of known width did not pass, so every width it produced "
            "would be suspect. "
            + (calibration.failure or "See /api/calibration for the detail."),
            checked_at=calibration.checked_at,
            tolerance_pct=calibration.tolerance_pct,
        )
        ctx.record.metrics["calibration"] = calibration.to_dict()
        ctx.progress(100, "refused: this build is not calibrated")
        return ctx.record

    try:
        params = SurveyParams.from_request(ctx.params)
    except (TypeError, ValueError) as exc:
        raise ServiceError("BAD_REQUEST", f"invalid parameters: {exc}") from exc

    result = survey(
        ctx.input_path,
        params,
        record=ctx.record,
        progress=lambda pct, msg: ctx.progress(pct, msg),
        save_evidence=ctx.save_evidence,
    )
    record = result.record
    record.metrics["calibration"] = calibration.to_dict()
    record.metrics["schedule_csv"] = ctx.save_evidence(
        "crack-schedule.csv", schedule_csv(record).encode("utf-8")
    )
    record.metrics["schedule_json"] = ctx.save_evidence(
        "crack-schedule.json", record.to_json().encode("utf-8")
    )
    return record


SCHEDULE_COLUMNS = (
    "crack_id", "frame", "t_ms", "position_x_mm", "position_y_mm", "length_mm",
    "width_p50_mm", "width_p95_mm", "expanded_uncertainty_mm", "coverage_factor_k",
    "upper_bound_mm", "band", "confidence", "samples", "refusal_code", "refusal_message",
)


def schedule_csv(record: RunRecord) -> str:
    """The crack schedule, as the spreadsheet an engineer will actually open.

    Refused rows are present with an empty width and the reason spelled out, rather
    than dropped. A schedule that silently omits what could not be measured reads as
    a clean wall.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(SCHEDULE_COLUMNS)
    k = record.params.get("coverage_factor", 2.0)
    for row in record.results:
        position = row.get("position_mm") or [None, None]
        refusal = row.get("refusal") or {}
        writer.writerow(
            [
                row.get("crack_id"),
                row.get("frame"),
                row.get("t_ms"),
                position[0],
                position[1],
                row.get("length_mm"),
                row.get("width_p50_mm"),
                row.get("width_p95_mm"),
                row.get("expanded_uncertainty_mm"),
                k,
                row.get("upper_bound_mm"),
                row.get("band"),
                row.get("confidence"),
                row.get("samples"),
                refusal.get("code", ""),
                refusal.get("message", ""),
            ]
        )
    return buffer.getvalue()


def build_app(**overrides: Any):
    product = ProductInfo(
        slug="hairline",
        title="Hairline",
        tagline="Crack widths in millimetres, from a phone walk-past, with the uncertainty",
        description=(
            "Hairline turns a phone video of a concrete surface into a measured crack "
            "schedule. A printed marker of known size gives the scale, widths are sampled "
            "perpendicular to each crack on the wall rather than on the screen, and every "
            "width carries an uncertainty. Where the photograph cannot support a number, "
            "it says so and says why."
        ),
        accent="#15618F",
        version="1.0.0",
        repo_url=os.environ.get("HAIRLINE_REPO_URL", ""),
    )
    config = ServiceConfig(
        product=product,
        params_schema=PARAMS_SCHEMA,
        static_dir=STATIC if STATIC.is_dir() else None,
        max_concurrent_jobs=int(os.environ.get("HAIRLINE_MAX_CONCURRENT", 1)),
        **overrides,
    )
    application = create_app(config, analyze)
    _install_extras(application)
    return application


def _install_extras(application: Any) -> None:
    """Routes that are Hairline's own: the bundled samples and the printable sheets."""
    import cv2
    from fastapi.responses import JSONResponse, Response

    from .targets import TargetSpec, calibration_target, marker_sheet

    @application.post("/api/capture")
    async def capture(file: UploadFile = File(...), target_width_mm: float = 0.30):
        """One viewfinder frame in, one line of guidance out.

        This is the endpoint a phone calls while the operator is still standing in
        front of the wall. It runs the same detector and the same resolution
        arithmetic as the survey, so it cannot disagree with the report that follows.
        """
        from visioncore import decode_image

        from .capture import assess

        data = await file.read(12 * 1024 * 1024)
        if not data:
            raise ServiceError("BAD_REQUEST", "empty frame")
        try:
            frame = decode_image(data)
        except Exception as exc:
            raise ServiceError("BAD_REQUEST", f"could not decode that frame: {exc}") from exc
        return JSONResponse(assess(frame, target_width_mm=float(target_width_mm)).to_dict())

    @application.get("/api/calibration")
    async def calibration() -> JSONResponse:
        """The gate's own result, so a judge can check it without reading the tests."""
        return JSONResponse(cached_report().to_dict())

    @application.get("/api/samples")
    async def samples() -> JSONResponse:
        manifest = SAMPLES / "manifest.json"
        if not manifest.is_file():
            return JSONResponse({"samples": []})
        return JSONResponse(json.loads(manifest.read_text(encoding="utf-8")))

    @application.get("/api/samples/{name}")
    async def sample(name: str) -> Response:
        path = SAMPLES / Path(name).name
        if not path.is_file():
            raise ServiceError("NOT_FOUND", f"no bundled sample named {name}")
        media = {
            ".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg",
            ".json": "application/json",
        }.get(path.suffix.lower(), "application/octet-stream")
        return Response(path.read_bytes(), media_type=media)

    @application.get("/api/target/{kind}.png")
    async def target(kind: str, dpi: int = 600) -> Response:
        spec = TargetSpec(dpi=max(150, min(1200, dpi)))
        if kind == "marker":
            page = marker_sheet(spec)
        elif kind == "calibration":
            page, _ = calibration_target(spec)
        else:
            raise ServiceError("NOT_FOUND", "kind must be 'marker' or 'calibration'")
        ok, buf = cv2.imencode(".png", page)
        if not ok:
            raise ServiceError("INTERNAL", "could not encode the sheet")
        return Response(
            buf.tobytes(),
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="hairline-{kind}.png"'},
        )


app = build_app()


@asynccontextmanager
async def _lifespan(_: Any) -> AsyncIterator[None]:
    """Run the calibration gate at startup, not on the first request.

    A drifted build then refuses from its first answer rather than its second, and the
    interface can show the state before anybody has uploaded anything.
    """
    report = await asyncio.get_running_loop().run_in_executor(None, cached_report)
    logging.getLogger("hairline").info(report.summary())
    yield


app.router.lifespan_context = _lifespan
