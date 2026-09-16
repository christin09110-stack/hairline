"""The clips the deployed service ships with, so a cold start has something to run.

Three samples, and the second and third matter as much as the first.

`walk-past.mp4`      a camera moving along a wall with four cracks of known width,
                     including two frames where the operator moved and the autofocus
                     lost it, so the frame gate has something real to reject.
`no-marker.jpg`      the same wall photographed without the scale card. Hairline has
                     to refuse this, and the refusal screen is the most important
                     thing in the product.
`too-oblique.jpg`    the card is there but the wall is nearly edge on.

Every width is declared in `manifest.json`, so anyone running the samples can check
the answers against what was drawn rather than against our say-so. These are
synthetic: we had no access to a camera or a concrete structure while building, and
the report says so plainly in its limitations section.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2

from .synth import (
    CameraSpec,
    CrackSpec,
    RenderOptions,
    SceneSpec,
    WalkPast,
    render,
    walk_past_video,
)

DEMO_SCENE = SceneSpec(
    panel_mm=(620.0, 460.0),
    marker_mm=60.0,
    marker_centre_mm=(-30.0, 40.0),
    surface_seed=4,
    cracks=(
        CrackSpec(points_mm=((-128.0, -74.0), (-120.0, -20.0), (-112.0, 30.0), (-104.0, 76.0)),
                  width_mm=0.50, name="west run"),
        CrackSpec(points_mm=((-16.0, -76.0), (-6.0, -20.0), (-4.0, 30.0), (6.0, 78.0)),
                  width_mm=0.80, name="centre run"),
        CrackSpec(points_mm=((104.0, -76.0), (116.0, -16.0), (130.0, 56.0)),
                  width_mm=1.60, name="east run"),
        CrackSpec(points_mm=((56.0, -74.0), (62.0, -14.0), (68.0, 54.0)),
                  width_mm=0.25, name="hairline run"),
    ),
)

DEMO_CAMERA = CameraSpec(image_size=(3840, 2160), focal_px=4050.0, distance_mm=335.0)


def build_samples(out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "note": (
            "Synthetic. Widths are exact by construction, drawn analytically in image "
            "space from a millimetre value by hairline.synth. No camera or concrete "
            "structure was available while this was built."
        ),
        "samples": [],
    }

    plan = WalkPast(
        scene=DEMO_SCENE,
        frames=72,
        fps=12.0,
        image_size=(2560, 1440),
        focal_px=2700.0,
        distance_mm=(335.0, 325.0),
        sweep_mm=(-68.0, 68.0),
        yaw_sweep_deg=(-10.0, 8.0),
        blur_bursts=(16, 17, 18, 41, 42),
    )
    video = out_dir / "walk-past.mp4"
    detail = walk_past_video(plan, str(video))
    manifest["samples"].append(
        {
            "file": video.name,
            "kind": "video",
            "title": "Walk-past of a wall with four cracks",
            "expect": "a crack schedule",
            "truth_mm": {c.name: c.width_mm for c in DEMO_SCENE.cracks},
            **detail,
        }
    )

    no_marker, _ = render(
        SceneSpec(**{**DEMO_SCENE.__dict__, "with_marker": False}),
        DEMO_CAMERA,
        RenderOptions(blur_px=0.9, noise_dn=2.2),
    )
    path = out_dir / "no-marker.jpg"
    cv2.imwrite(str(path), no_marker, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    manifest["samples"].append(
        {
            "file": path.name,
            "kind": "image",
            "title": "The same wall, photographed without the scale card",
            "expect": "a refusal: NO_MARKER",
            "truth_mm": {c.name: c.width_mm for c in DEMO_SCENE.cracks},
        }
    )

    oblique, _ = render(
        DEMO_SCENE,
        CameraSpec(image_size=(3840, 2160), focal_px=4050.0, distance_mm=520.0, yaw_deg=68.0),
        RenderOptions(blur_px=1.0, noise_dn=2.4),
    )
    path = out_dir / "too-oblique.jpg"
    cv2.imwrite(str(path), oblique, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    manifest["samples"].append(
        {
            "file": path.name,
            "kind": "image",
            "title": "The card is in shot but the wall is nearly edge on",
            "expect": "a refusal: TOO_OBLIQUE or MARKER_TOO_SMALL",
            "truth_mm": {c.name: c.width_mm for c in DEMO_SCENE.cracks},
        }
    )

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest
