# Benchmark: crack_pipeline

the whole measurement pipeline on a 4K frame: adaptive threshold, morphological open, connected components, distance transform

`15` timed runs per arm after `3` warm-up runs, 2026-09-16T05:07:15.261042+00:00.

| Arm | Instance | median ms | p95 ms | fps | $/1k frames | OpenCV | HAL |
|---|---|---:|---:|---:|---:|---|---|
| local-x86_64-pip | local | 73.952 | 76.664 | 13.52 | — | 5.0.0 | YES (ipp (ver 0.0.1)) |
| graviton-pip | c8g.4xlarge | 49.171 | 49.331 | 20.34 | $0.008720 | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) |
| graviton-cool | c8g.large | — | — | — | — | — | _skipped: BENCH_COOL_HOST is not set; no instance to run on_ |
| hybrid-x86 | c7i.4xlarge | 86.854 | 87.256 | 11.51 | $0.017226 | 5.0.0 | YES (ipp (ver 0.0.1)) |

## Speedup against `graviton-pip`

| Arm | median ms | speedup |
|---|---:|---:|
| local-x86_64-pip | 73.95 | 0.66x |
| graviton-pip | 49.17 | 1.00x |
| hybrid-x86 | 86.85 | 0.57x |

## What this comparison does and does not show

The stock opencv-python aarch64 wheel already bundles Arm KleidiCV 26.03 (verified by `strings` on cv2.abi3.so: 'Custom HAL: YES (carotene, KleidiCV (ver 26.03))'). The 'graviton-pip' arm is therefore an ACCELERATED baseline, not a plain one. Any speedup attributed to COOL is measured against that, and comparing COOL-on-Graviton to pip-on-x86 measures the architecture, not COOL.
