"""Run the two held-out TEST photos once each, with frozen blackhat defaults.

Outputs go to media/real/hairline/scaled/runs/<name>/ (run.json plus evidence images).
Not used for tuning: run only after the defaults in config.py were committed.
"""

from __future__ import annotations

import json
from pathlib import Path

from hairline.config import SurveyParams
from hairline.survey import survey

SCALED = Path("$HAIRLINE_PHOTOS/scaled")

TESTS = {
    "dsc07068": ("crack-dsc07068-jpg.jpg", {
        "manual_scale": [0.64352, 0.82047, 0.98573, 0.88477, 80],
        "manual_scale_click_px": 3, "expected_width_mm": 0.3,
        "segmentation": "blackhat", "keep_edge_cracks": True,
        "exclude_regions": [
            [[0, 0.54], [0.32, 0.53], [1, 0.63], [1, 1], [0, 1]],
            [[0.66, 0.36], [0.88, 0.36], [0.88, 0.61], [0.66, 0.61]],
            [[0.29, 0.35], [0.55, 0.35], [0.55, 0.50], [0.29, 0.50]],
        ],
    }),
    "dnipro": ("crack-monitor-in-dnipro-jpg.jpg", {
        "manual_scale": [0.34800, 0.65033, 0.55390, 0.65033, 40],
        "manual_scale_click_px": 5, "expected_width_mm": 4.0,
        "segmentation": "blackhat", "keep_edge_cracks": True,
        "exclude_regions": [
            [[0.13, 0.52], [0.81, 0.52], [0.81, 0.76], [0.13, 0.76]],
            [[0.72, 0.82], [1, 0.82], [1, 1], [0.72, 1]],
        ],
    }),
}


def main() -> None:
    for name, (fname, req) in TESTS.items():
        out = SCALED / "runs" / name
        out.mkdir(parents=True, exist_ok=True)

        def save(filename: str, data: bytes, _out=out) -> str:
            (_out / filename).write_bytes(data)
            return filename

        params = SurveyParams.from_request(req)
        res = survey(SCALED / fname, params, save_evidence=save)
        doc = {
            "photo": fname,
            "request": req,
            "params": params.to_dict(),
            "record": res.record.to_dict(),
            "gates": [g.to_dict() for g in res.gates],
            "cracks": [c.to_dict() for c in res.cracks],
        }
        (out / "run.json").write_text(json.dumps(doc, indent=2, default=str))
        print(f"== {name}: {len(res.cracks)} cracks, refused={res.record.refused}")
        for c in res.cracks:
            d = c.to_dict()
            print(
                f"  {d['crack_id']}: p50={d['width_p50_mm']} p95={d['width_p95_mm']} "
                f"U={d['expanded_uncertainty_mm']} len={d['length_mm']} "
                f"refusal={(d['refusal'] or {}).get('code')}"
            )


if __name__ == "__main__":
    main()
