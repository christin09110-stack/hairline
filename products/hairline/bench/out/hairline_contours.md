# Benchmark: hairline_contours

findContours RETR_LIST over the whole thresholded 4K frame

`15` timed runs per arm after `3` warm-up runs, 2026-09-16T05:06:47.953299+00:00.

| Arm | Instance | median ms | p95 ms | fps | $/1k frames | OpenCV | HAL |
|---|---|---:|---:|---:|---:|---|---|
| local-x86_64-pip | local | 35.855 | 36.573 | 27.89 | — | 5.0.0 | YES (ipp (ver 0.0.1)) |
| graviton-pip | c8g.4xlarge | 43.161 | 43.406 | 23.17 | $0.007654 | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) |
| graviton-cool | c8g.large | — | — | — | — | — | _skipped: BENCH_COOL_HOST is not set; no instance to run on_ |
| hybrid-x86 | c7i.4xlarge | 46.702 | 47.527 | 21.41 | $0.009263 | 5.0.0 | YES (ipp (ver 0.0.1)) |

## Speedup against `graviton-pip`

| Arm | median ms | speedup |
|---|---:|---:|
| local-x86_64-pip | 35.86 | 1.20x |
| graviton-pip | 43.16 | 1.00x |
| hybrid-x86 | 46.70 | 0.92x |

## What this comparison does and does not show

The stock opencv-python aarch64 wheel already bundles Arm KleidiCV 26.03 (verified by `strings` on cv2.abi3.so: 'Custom HAL: YES (carotene, KleidiCV (ver 26.03))'). The 'graviton-pip' arm is therefore an ACCELERATED baseline, not a plain one. Any speedup attributed to COOL is measured against that, and comparing COOL-on-Graviton to pip-on-x86 measures the architecture, not COOL.
