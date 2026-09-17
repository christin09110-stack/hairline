# Benchmark: hairline_threshold

just the adaptiveThreshold call from the pipeline, block 61, 4K

`15` timed runs per arm after `3` warm-up runs, 2026-09-16T23:58:58.110180+00:00.

| Arm | Instance | median ms | p95 ms | fps | $/1k frames | OpenCV | HAL |
|---|---|---:|---:|---:|---:|---|---|
| graviton-pip | c8g.4xlarge | 24.573 | 24.621 | 40.69 | $0.004358 | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) |
| graviton-cool | c8g.4xlarge | 14.177 | 14.318 | 70.54 | $0.002672 | 4.14.0-pre | YES (carotene (ver 0.0.1) KleidiCV (ver 0.7.0)) |

## Speedup against `graviton-pip`

| Arm | median ms | speedup |
|---|---:|---:|
| graviton-pip | 24.57 | 1.00x |
| graviton-cool | 14.18 | 1.73x |

## What this comparison does and does not show

The stock opencv-python aarch64 wheel already bundles Arm KleidiCV 26.03 (verified by `strings` on cv2.abi3.so: 'Custom HAL: YES (carotene, KleidiCV (ver 26.03))'). The 'graviton-pip' arm is therefore an ACCELERATED baseline, not a plain one. Any speedup attributed to COOL is measured against that, and comparing COOL-on-Graviton to pip-on-x86 measures the architecture, not COOL.
