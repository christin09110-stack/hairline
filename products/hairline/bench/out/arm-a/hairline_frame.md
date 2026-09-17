# Benchmark: hairline_frame

Hairline's per-frame segmentation on a 4K concrete frame: measured adaptive-threshold constant, adaptiveThreshold GAUSSIAN block 61, morphology, connectedComponentsWithStats, per-component distance transform and findContours

`15` timed runs per arm after `3` warm-up runs, 2026-09-16T23:58:38.005526+00:00.

| Arm | Instance | median ms | p95 ms | fps | $/1k frames | OpenCV | HAL |
|---|---|---:|---:|---:|---:|---|---|
| graviton-pip | c8g.4xlarge | 75.25 | 75.447 | 13.29 | $0.013344 | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) |
| graviton-cool | c8g.4xlarge | 68.022 | 68.467 | 14.7 | $0.012818 | 4.14.0-pre | YES (carotene (ver 0.0.1) KleidiCV (ver 0.7.0)) |

## Speedup against `graviton-pip`

| Arm | median ms | speedup |
|---|---:|---:|
| graviton-pip | 75.25 | 1.00x |
| graviton-cool | 68.02 | 1.11x |

## What this comparison does and does not show

The stock opencv-python aarch64 wheel already bundles Arm KleidiCV 26.03 (verified by `strings` on cv2.abi3.so: 'Custom HAL: YES (carotene, KleidiCV (ver 26.03))'). The 'graviton-pip' arm is therefore an ACCELERATED baseline, not a plain one. Any speedup attributed to COOL is measured against that, and comparing COOL-on-Graviton to pip-on-x86 measures the architecture, not COOL.
