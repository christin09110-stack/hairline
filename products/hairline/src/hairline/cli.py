"""Command line: measure a file, make the printable sheets, build the bundled samples.

    hairline measure walk.mp4 --marker-mm 60 --out out/
    hairline sheets --out docs/sheets --dpi 600
    hairline samples --out src/hairline/samples
    hairline serve --port 8000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from .config import ESTIMATORS, SurveyParams
from .service import schedule_csv


def _measure(args: argparse.Namespace) -> int:
    from .survey import survey

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    extra: dict = {}
    if args.scale:
        extra["manual_scale"] = [float(v) for v in args.scale.split(",")]
        extra["manual_scale_max_tilt_deg"] = args.max_tilt_deg
    if args.exclude:
        extra["exclude_regions"] = [
            [[float(c) for c in pt.split(",")] for pt in poly.split(";")]
            for poly in args.exclude
        ]
    if args.keep_edge_cracks:
        extra["keep_edge_cracks"] = True
    params = SurveyParams.from_request({
        "marker_length_mm": args.marker_mm,
        "estimator": args.estimator,
        "frame_stride": args.stride,
        "working_distance_mm": args.stand_off_mm,
        **extra,
    })

    def save(name: str, data: bytes) -> str:
        (out / name).write_bytes(data)
        return str(out / name)

    def progress(pct: float, message: str) -> None:
        if message:
            print(f"  {pct:5.1f}%  {message}", flush=True)

    result = survey(args.path, params, progress=progress, save_evidence=save)
    record = result.record
    (out / "run.json").write_text(record.to_json(), encoding="utf-8")
    (out / "crack-schedule.csv").write_text(schedule_csv(record), encoding="utf-8")

    print()
    if record.refused and not result.cracks:
        for refusal in record.refusals:
            print(f"cannot measure: {refusal.code}\n  {refusal.message}")
        print(f"\nwrote {out / 'run.json'}")
        return 2

    print(f"{'crack':<7} {'width p95':>12} {'uncertainty':>12} {'length':>9}  confidence  band")
    for crack in result.cracks:
        m = crack.measurement
        if m.ok and m.p95_mm is not None:
            print(
                f"{crack.crack_id:<7} {m.p95_mm:>9.3f} mm "
                f"{'±' + format(m.expanded_p95_mm or 0.0, '.3f'):>12} "
                f"{m.length_mm or 0.0:>6.0f} mm  {m.confidence:<10}  {crack.band}"
            )
        else:
            reason = m.refusal.code.lower().replace("_", " ") if m.refusal else "not measured"
            bound = f" (finer than {m.upper_bound_mm:.2f} mm)" if m.upper_bound_mm else ""
            print(f"{crack.crack_id:<7} {'cannot measure':>12}{bound}   {reason}")
    print(f"\nwrote {out / 'run.json'} and {out / 'crack-schedule.csv'}")
    return 0


def _sheets(args: argparse.Namespace) -> int:
    from .targets import TargetSpec, calibration_target, marker_sheet

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    spec = TargetSpec(dpi=args.dpi, marker_mm=args.marker_mm)
    cv2.imwrite(str(out / "hairline-marker.png"), marker_sheet(spec))
    page, manifest = calibration_target(spec)
    cv2.imwrite(str(out / "hairline-calibration-target.png"), page)
    (out / "hairline-calibration-target.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    print(f"wrote three files to {out}")
    return 0


def _samples(args: argparse.Namespace) -> int:
    from .samplegen import build_samples

    manifest = build_samples(Path(args.out))
    print(json.dumps(manifest, indent=1))
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("hairline.service:app", host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hairline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    measure = sub.add_parser("measure", help="measure a video or a still")
    measure.add_argument("path")
    measure.add_argument("--out", default="out")
    measure.add_argument("--marker-mm", type=float, default=60.0)
    measure.add_argument("--stand-off-mm", type=float, default=350.0)
    measure.add_argument("--stride", type=int, default=3)
    measure.add_argument("--estimator", choices=ESTIMATORS, default="blur_corrected")
    measure.add_argument(
        "--scale", metavar="X1,Y1,X2,Y2,MM",
        help="manual scale when there is no printed marker: two points on a reference of "
        "known length (as fractions of width and height) and that length in mm",
    )
    measure.add_argument("--max-tilt-deg", type=float, default=10.0,
                         help="largest surface tilt you vouch for with a manual scale")
    measure.add_argument("--exclude", action="append", metavar="X,Y;X,Y;X,Y",
                         help="polygon to leave out of crack finding, e.g. the ruler")
    measure.add_argument("--keep-edge-cracks", action="store_true",
                         help="measure cracks that leave the frame on their interior only")
    measure.set_defaults(func=_measure)

    sheets = sub.add_parser("sheets", help="write the printable marker and calibration target")
    sheets.add_argument("--out", default="docs/sheets")
    sheets.add_argument("--dpi", type=int, default=600)
    sheets.add_argument("--marker-mm", type=float, default=60.0)
    sheets.set_defaults(func=_sheets)

    samples = sub.add_parser("samples", help="build the bundled demo clips")
    samples.add_argument("--out", default="src/hairline/samples")
    samples.set_defaults(func=_samples)

    serve = sub.add_parser("serve", help="run the web service")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
