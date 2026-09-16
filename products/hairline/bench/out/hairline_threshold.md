# Benchmark: hairline_threshold

just the adaptiveThreshold call from the pipeline, block 61, 4K

`15` timed runs per arm after `3` warm-up runs, 2026-09-16T05:06:10.540814+00:00.

| Arm | Instance | median ms | p95 ms | fps | $/1k frames | OpenCV | HAL |
|---|---|---:|---:|---:|---:|---|---|
| local-x86_64-pip | local | 34.769 | 35.323 | 28.76 | — | 5.0.0 | YES (ipp (ver 0.0.1)) |
| graviton-pip | c8g.4xlarge | 24.551 | 24.642 | 40.73 | $0.004354 | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) |
| graviton-cool | c8g.large | — | — | — | — | — | _skipped: BENCH_COOL_HOST is not set; no instance to run on_ |
| hybrid-x86 | c7i.4xlarge | 40.563 | 41.213 | 24.65 | $0.008045 | 5.0.0 | YES (ipp (ver 0.0.1)) |

## Speedup against `graviton-pip`

| Arm | median ms | speedup |
|---|---:|---:|
| local-x86_64-pip | 34.77 | 0.71x |
| graviton-pip | 24.55 | 1.00x |
| hybrid-x86 | 40.56 | 0.61x |

## What this comparison does and does not show

The stock opencv-python aarch64 wheel already bundles Arm KleidiCV 26.03 (verified by `strings` on cv2.abi3.so: 'Custom HAL: YES (carotene, KleidiCV (ver 26.03))'). The 'graviton-pip' arm is therefore an ACCELERATED baseline, not a plain one. Any speedup attributed to COOL is measured against that, and comparing COOL-on-Graviton to pip-on-x86 measures the architecture, not COOL.
