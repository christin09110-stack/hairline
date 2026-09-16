# Benchmark: hairline_frame

Hairline's per-frame segmentation on a 4K concrete frame: measured adaptive-threshold constant, adaptiveThreshold GAUSSIAN block 61, morphology, connectedComponentsWithStats, per-component distance transform and findContours

`15` timed runs per arm after `3` warm-up runs, 2026-09-16T05:05:19.417854+00:00.

| Arm | Instance | median ms | p95 ms | fps | $/1k frames | OpenCV | HAL |
|---|---|---:|---:|---:|---:|---|---|
| local-x86_64-pip | local | 86.337 | 88.822 | 11.58 | — | 5.0.0 | YES (ipp (ver 0.0.1)) |
| graviton-pip | c8g.4xlarge | 75.306 | 75.414 | 13.28 | $0.013354 | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) |
| graviton-cool | c8g.large | — | — | — | — | — | _skipped: BENCH_COOL_HOST is not set; no instance to run on_ |
| hybrid-x86 | c7i.4xlarge | 99.292 | 100.031 | 10.07 | $0.019693 | 5.0.0 | YES (ipp (ver 0.0.1)) |

## Speedup against `graviton-pip`

| Arm | median ms | speedup |
|---|---:|---:|
| local-x86_64-pip | 86.34 | 0.87x |
| graviton-pip | 75.31 | 1.00x |
| hybrid-x86 | 99.29 | 0.76x |

## What this comparison does and does not show

The stock opencv-python aarch64 wheel already bundles Arm KleidiCV 26.03 (verified by `strings` on cv2.abi3.so: 'Custom HAL: YES (carotene, KleidiCV (ver 26.03))'). The 'graviton-pip' arm is therefore an ACCELERATED baseline, not a plain one. Any speedup attributed to COOL is measured against that, and comparing COOL-on-Graviton to pip-on-x86 measures the architecture, not COOL.
