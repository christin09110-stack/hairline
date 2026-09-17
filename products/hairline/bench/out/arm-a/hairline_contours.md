# Benchmark: hairline_contours

findContours RETR_LIST over the whole thresholded 4K frame

`15` timed runs per arm after `3` warm-up runs, 2026-09-16T23:59:14.691265+00:00.

| Arm | Instance | median ms | p95 ms | fps | $/1k frames | OpenCV | HAL |
|---|---|---:|---:|---:|---:|---|---|
| graviton-pip | c8g.4xlarge | 43.644 | 43.913 | 22.91 | $0.007740 | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) |
| graviton-cool | c8g.4xlarge | 43.559 | 43.928 | 22.96 | $0.008208 | 4.14.0-pre | YES (carotene (ver 0.0.1) KleidiCV (ver 0.7.0)) |

## Speedup against `graviton-pip`

| Arm | median ms | speedup |
|---|---:|---:|
| graviton-pip | 43.64 | 1.00x |
| graviton-cool | 43.56 | 1.00x |

## What this comparison does and does not show

The stock opencv-python aarch64 wheel already bundles Arm KleidiCV 26.03 (verified by `strings` on cv2.abi3.so: 'Custom HAL: YES (carotene, KleidiCV (ver 26.03))'). The 'graviton-pip' arm is therefore an ACCELERATED baseline, not a plain one. Any speedup attributed to COOL is measured against that, and comparing COOL-on-Graviton to pip-on-x86 measures the architecture, not COOL.
