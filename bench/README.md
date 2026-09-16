# bench

The harness for the **Best Use of COOL** award. One workload, up to four arms,
the same source code executed on each.

```bash
.venv/bin/python -m bench.run --list
.venv/bin/python -m bench.run crack_pipeline --repeats 9
```

It runs with only the local arm present and skips the rest with a stated reason,
so you can benchmark a pipeline before any EC2 instance exists.

## The arms

| Arm | Where | What |
|---|---|---|
| `local-<machine>-pip` | this machine | stock pip wheel, no cost attributed |
| `graviton-pip` | `BENCH_GRAVITON_HOST` | stock pip wheel on Graviton — **already has KleidiCV** |
| `graviton-cool` | `BENCH_COOL_HOST` | the COOL AMI's `/opt/cool/venvs/python_3.12` |
| `hybrid-x86` | `BENCH_HYBRID_HOST` | cost-matched x86, IPP HAL (optional) |

```bash
export BENCH_SSH_KEY=~/.ssh/opencv26.pem
export BENCH_GRAVITON_HOST=ubuntu@ec2-...compute-1.amazonaws.com
export BENCH_GRAVITON_TYPE=c8g.large
export BENCH_COOL_HOST=ubuntu@ec2-...compute-1.amazonaws.com
export BENCH_COOL_TYPE=c8g.large
.venv/bin/python -m bench.run crack_pipeline --repeats 15
```

## The one thing that will sink a COOL submission

The stock `opencv-python` **aarch64** wheel already bundles Arm KleidiCV 26.03.
Confirmed by `strings` on `cv2.abi3.so`:

```
Custom HAL:   YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))
3rdparty:     ... tegra_hal kleidicv_hal kleidicv kleidicv_thread
```

So `pip install opencv-python` on Graviton is **not** an unaccelerated baseline.
Most entries will compare COOL-on-Graviton against pip-on-x86, get a large
number, and be measuring the architecture. Every report this harness writes
carries that disclosure verbatim, and the speedup table is computed against
`graviton-pip`, never against the x86 arm. If the honest number turns out small,
say so: the COOL rubric credits "measured performance **or developer-productivity**
value", and a turnkey AMI with preconfigured venvs is a real second axis.

## Choosing a workload

Measured on x86, 4K frame, 22 threads:

| Function | ms |
|---|---:|
| findContours (RETR_LIST) | 607.16 |
| adaptiveThreshold GAUSSIAN 31 | 30.54 |
| Canny 100/200 | 12.96 |
| GaussianBlur 7x7 | 1.01 |
| resize 4K→720p INTER_LINEAR | 0.35 |

`adaptiveThreshold` and `findContours` dominate and are two of the three
functions COOL's listing names. Build the benchmark around those. A
single-call resize benchmark is noise.

## Adding your own workload

```python
from bench.workloads import Workload, register

register(Workload(
    name="my_pipeline",
    description="what it does",
    setup_src="import cv2, numpy as np\nframe = cv2.imread('sample.png')",
    run_src="cv2.Canny(frame, 100, 200)",
    frames_per_run=1,
))
```

`setup_src` and `run_src` are **source strings**, not callables, because they are
shipped to remote arms over ssh and run there. That guarantees every arm runs the
same lines instead of the same pickle.
