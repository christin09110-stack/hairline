# The COOL benchmark

**Submitted for the Best Use of COOL award. Read §1 first: the COOL AMI does not ship
OpenCV 5, and that changes what its numbers mean.**

Measured 16 September 2026, us-east-1, account <aws-account-id>. Raw JSON and the
per-workload markdown are in [`../bench/out/`](../bench/out); the workload definitions
are in [`../bench/workload.py`](../bench/workload.py) and the instance launcher in
[`../bench/instances.sh`](../bench/instances.sh).

---

## 1. Arm A, and what the COOL AMI actually contains

Arm A ran on 16 September 2026 at 23:58 UTC, about nineteen hours after arms B to E.
The first attempt that morning stopped at the AWS Marketplace terms, which can only be
accepted in the browser console (`run-instances` returned `OptInRequired`). The account
owner accepted them that evening. Arm B was launched again
alongside it, so the COOL-against-pip comparison in §4.3 comes from one session on two
c8g.4xlarge instances. Arm B's whole-frame median in that session was 75.25 ms, against
75.31 ms that morning.

The image is `ami-01db31139bc5615d8`, "COOL-Graviton4-v2-AMI-prod-k6u24vxijyzvg",
product code `aajkmdd4qo3r7yhqg61a7aah9`, created 2026-04-22, default user `ubuntu`,
Ubuntu 24.04.3. This is what its OpenCV reports about itself:

```
General configuration for OpenCV 4.14.0-pre
  Version control:     4.13.0-153-g9a6e0d1bff-dirty
  Timestamp:           2026-04-21T08:44:26Z
  Baseline:            SVE NEON FP16 NEON_DOTPROD NEON_FP16 NEON_BF16
  C++ flags (Release): -O3 -mcpu=neoverse-v2 ... -flto=auto
  Parallel framework:  TBB (ver 2022.1 interface 12150)
  Lapack:              YES (/opt/arm/armpl_25.07.1_gcc/lib/libarmpl_lp64.so ...)
  Custom HAL:          YES (carotene (ver 0.0.1) KleidiCV (ver 0.7.0))
```

Three things follow.

- **It is OpenCV 4.14.0-pre, not 5.0.** It is a development snapshot 153 commits after
  4.13.0. Arms B to E run OpenCV 5.0.0 from the pinned wheel. So arm A against arm B
  compares two builds and also two versions of OpenCV. We ran COOL's build as shipped
  and did not swap in ours.
- **It is tuned for the core.** The wheel's baseline is generic `NEON FP16`. COOL's
  adds SVE, dotprod and BF16, and the whole library is compiled with
  `-mcpu=neoverse-v2` and link-time optimisation, with TBB for threading and Arm
  Performance Libraries for LAPACK.
- **Its KleidiCV is labelled 0.7.0, where the wheel's is 26.03.** We did not work out
  whether those are two version schemes for similar code or really different releases.

A practical detail for anyone reproducing this: the venvs at
`/opt/cool/venvs/python_3.1{0,1,2}` do not contain `cv2`. Calling
`/opt/cool/venvs/python_3.12/bin/python` directly raises `ModuleNotFoundError`. The
venv's `activate` script adds `/opt/cool/python_3.12/site-packages/cv2/python-3.12` to
`PYTHONPATH` and `/opt/cool/cpp_sdk/lib` to `LD_LIBRARY_PATH`, and without those two
variables there is no OpenCV. The root snapshot is also 50 GB, so a launch that asks
for a smaller root volume fails with `InvalidBlockDeviceMapping`.

**UNVERIFIED:** the listing id `prodview-fdvbfiewzuehs` could not be mapped to a product
code from the CLI, because `describe-entity` only resolves entities the account sells.
The AMI is matched to the Graviton4 listing by its name, and the same product code also
covers a Graviton5 image.

---

## 2. The thing that sinks most COOL submissions

**The stock PyPI `opencv-python` aarch64 wheel already ships Arm's KleidiCV 26.03.**
Confirmed by `strings` on the wheel's `cv2.abi3.so`, and confirmed again by the
running instances in this benchmark, which print their own HAL:

```
Custom HAL:   YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))
3rdparty:     ... tegra_hal kleidicv_hal kleidicv kleidicv_thread
```

OpenCV's own 5.0 release notes say KleidiCV "is selected automatically when
available". So `pip install opencv-python` on Graviton is **not** an unaccelerated
baseline. An entry that benchmarks COOL-on-Graviton against pip-on-x86 and reports the
ratio is measuring the processor, not the library, and the number will be large and
meaningless.

Every comparison below is therefore against **a stock pinned wheel on the same
instance family**, and the x86 arm is reported as what it is: a cost-matched
architecture comparison, not a COOL baseline.

---

## 3. Method

One workload, five arms, the same source shipped to each over ssh and executed there.

`hairline_frame` is the product's own per-frame segmentation, function for function:
the measured adaptive-threshold constant, `adaptiveThreshold` with
`ADAPTIVE_THRESH_GAUSSIAN_C` at block 61, morphological open and close,
`connectedComponentsWithStats`, then per surviving component a `distanceTransform`,
a medial-axis pass and `findContours`. Two of those — adaptive Gaussian thresholding
and contour detection — are named in COOL's own listing.

It is written as **source text, not as an import of `hairline`**. The harness ships it
to each arm and runs it there against whatever OpenCV that arm has. That matters for
the COOL arm specifically: installing our own package on the COOL image would have put
a pip-installed OpenCV next to the one being measured. The correspondence between
`bench/workload.py` and `src/hairline/segment.py` is line for line and is meant to be
checked.

The input is a 4K (3840×2160) synthetic concrete frame with four cracks, aggregate
pop-out, shutter lines and a printed marker, generated deterministically from a fixed
seed inside `setup_src` so every arm gets a bit-identical image with no file transfer.

15 timed runs per arm after 3 warm-up runs. Latency is reported as median and p95 over
the timed runs. Cost per thousand frames is the arm's on-demand rate from the AWS
Price List API divided into the measured latency; on-demand rather than the spot price
actually paid, because on-demand is the number a reader can check. Arm A's rate also
includes COOL's $0.04 per hour software fee on c8g.4xlarge, from the listing, so it is
charged at $0.6784 an hour against $0.6384 for arm B.

Instances were **Spot**, and that is not a cost decision: the account's *Running
On-Demand Standard instances* quota is 16 vCPUs with 14 already used by other
projects, so a 16-vCPU on-demand instance cannot launch at all. The Spot quota is a
separate, unused 32 vCPUs. An increase to 96 was requested and is open as a support
case. Spot and on-demand are the same hardware.

---

## 4. Results

### 4.1 The whole per-frame pipeline, 4K

| Arm | Instance | vCPU | median ms | p95 ms | frames/s | $/1000 frames | HAL |
|---|---|---:|---:|---:|---:|---:|---|
| **A · graviton-cool** | c8g.4xlarge | 16 | **68.02** | 68.47 | 14.70 | **$0.01282** | KleidiCV 0.7.0, OpenCV 4.14.0-pre |
| **B · graviton-pip** | c8g.4xlarge | 16 | **75.31** | 75.41 | 13.28 | **$0.01335** | KleidiCV 26.03 |
| **C · local x86** | this workstation | 22 | 86.34 | 88.82 | 11.58 | — | Intel IPP 2026.0.0 |
| **D · hybrid-x86** | c7i.4xlarge | 16 | 99.29 | 100.03 | 10.07 | $0.01969 | Intel IPP 2026.0.0 |
| **E · the deployed endpoint** | c8g.large | 2 | 134.25 | 136.01 | 7.45 | **$0.00297** | KleidiCV 26.03 |

Arm A was measured about nineteen hours after the others, in the same session as a second
run of arm B (§4.3). Arm E is not part of the comparison. It is the live demo instance, measured in place,
inside the container a judge's upload actually runs through — `docker exec hairline
python -c …` over SSM. It is here because a benchmark of a configuration nobody runs
is worth less than a number from the thing that is running.

### 4.2 Per function, Graviton against cost-matched x86

| Workload | c8g.4xlarge | c7i.4xlarge | Arm advantage |
|---|---:|---:|---:|
| `adaptiveThreshold` GAUSSIAN, block 61, 4K | **24.55 ms** | 40.56 ms | **1.65×** |
| `findContours` RETR_LIST over a 4K binary | 43.16 ms | 46.70 ms | 1.08× |
| whole `hairline_frame` pipeline | 75.31 ms | 99.29 ms | 1.32× |
| shared `crack_pipeline` microbenchmark | 49.17 ms | 86.85 ms | 1.77× |

This breakdown is the most interesting thing in the benchmark. Adaptive Gaussian
thresholding is a per-pixel kernel and is one of the functions KleidiCV accelerates:
**1.65×** against Intel's IPP on a cost-matched instance. Contour detection is a
sequential boundary trace, is not a per-pixel kernel, and shows **1.08×** — essentially
the clock-speed difference. Averaging the two into one headline figure would hide the
only thing a reader can act on, which is that the acceleration is real and is specific.

### 4.3 COOL against the stock wheel, same session

Both on c8g.4xlarge, measured one after the other at 23:58 UTC on 16 September. Arm A in
us-east-1a, arm B in us-east-1d.

| Workload | A · COOL, median / p95 | B · pip, median / p95 | COOL speedup | $/1000, A | $/1000, B |
|---|---:|---:|---:|---:|---:|
| `adaptiveThreshold` GAUSSIAN, block 61, 4K | **14.18** / 14.32 ms | 24.57 / 24.62 ms | **1.73×** | **$0.00267** | $0.00436 |
| `findContours` RETR_LIST over a 4K binary | 43.56 / 43.93 ms | 43.64 / 43.91 ms | 1.00× | $0.00821 | $0.00774 |
| whole `hairline_frame` pipeline | **68.02** / 68.47 ms | 75.25 / 75.45 ms | **1.11×** | **$0.01282** | $0.01334 |
| shared `crack_pipeline` microbenchmark | 47.34 / 47.81 ms | 50.04 / 50.21 ms | 1.06× | $0.00892 | $0.00887 |

On this product's own frame, COOL is 1.11× faster than the stock wheel on the same
instance. After its software fee it is 4% cheaper per frame.

All of the gain is in one function. `adaptiveThreshold` runs 1.73× faster, taking
10.4 ms off each frame. `findContours` is unchanged. The whole pipeline saves 7.2 ms a
frame, which is less than the threshold call saves on its own. The pipeline makes the
same threshold call, so the other calls in it (morphology, connected components,
distance transforms, per-component contours) probably lost about 3 ms a frame between
them on COOL. We did not time those calls one by one, so we cannot say which.

Where a workload gets no speedup, the fee makes COOL more expensive: `findContours`
costs 6% more per thousand calls on COOL, and the shared microbenchmark is level.

We cannot say which part of the build produces the 1.73×. The candidates are the
Neoverse-V2 compile flags, the SVE baseline, the different KleidiCV, and the
differences between OpenCV 4.14.0-pre and 5.0.0. Separating them would take our own
builds, which we did not make. The harness times each call. It does not compare the
two arms' output images, so this benchmark does not show that the two builds produce
identical masks.

### 4.4 Cost

Per thousand 4K frames, at us-east-1 on-demand rates:

- c8g.4xlarge, Graviton4, COOL AMI including its $0.04/hr fee: **$0.01282**
- c8g.4xlarge, Graviton4, stock wheel: **$0.01335**
- c7i.4xlarge, x86: **$0.01969** — 47% more for the same work
- c8g.large, Graviton4, 2 vCPU: **$0.00297**

The third line is the one worth sitting with. Dropping from 16 vCPUs to 2 makes each
frame **1.78× slower** and **4.5× cheaper**, because the instance costs one eighth as
much. For a survey that is a batch job rather than an interactive request, the small
instance is strictly the better buy, and the only reason to take the large one is
latency you have promised somebody.

---

## 5. What arm A shows, and what it does not

**What it shows.** On Hairline's own 4K frame, the COOL AMI's OpenCV is 1.11× faster
than the stock OpenCV 5.0.0 wheel on the same c8g.4xlarge, and 4% cheaper per frame
after the software fee. The gain comes from `adaptiveThreshold`, 1.73× faster, which is
one of the two functions COOL's listing names for this kind of work. The other,
`findContours`, runs at the same speed on both builds. Measured against cost-matched
x86 from the morning session, COOL runs the whole frame 1.46× faster (68.02 ms against
99.29 ms), and at 35% lower cost per frame.

**What it does not show.** It does not isolate the build flags. COOL ships OpenCV
4.14.0-pre, a development snapshot, and the baseline is OpenCV 5.0.0. Part of any
difference could come from source changes between those versions rather than from the
tuning. We have not read the COOL listing's own average speedup, which is published as
an image, so we do not quote it or compare against it. COOL's samples README reports
its own figures against "Standard (Pip 4.13)", an older wheel than the 5.0.0 one
used here.

**What it means for this product.** Thresholding is about a third of Hairline's frame
time on the stock wheel (24.6 of 75.3 ms), so even a large gain there moves the whole
frame by a modest amount. A 1.11× gain on one instance size is real and small. It does
not change the deployment choice in §4.4: the 2-vCPU c8g.large on the stock wheel is
still 4.3× cheaper per frame than COOL on the 16-vCPU instance, and COOL on the small
instance was not measured.

**The second axis, which is not latency.** The COOL rubric credits "measured
performance, cost, reliability, **or developer-productivity value**". On developer
productivity we can report something concrete from this build, because we did the work
COOL exists to avoid: bringing up a benchmark-ready Arm instance meant installing
python3-venv, creating a venv, pinning `opencv-python-headless==5.0.0.93` and
`numpy==2.5.3`, and waiting for cloud-init — about four minutes per instance, plus a
first attempt that produced neither Docker nor Caddy because a single `apt-get install`
with five package names aborts the whole transaction when one of them is unavailable
on arm64. COOL ships an image with three preconfigured venvs at
`/opt/cool/venvs/python_3.1{0,1,2}` and a C++ SDK at `/opt/cool/cpp_sdk`. For a team
standing up Arm CI, that is the product. One caveat from actually using it: the venvs
only work through their `activate` script (§1), so a CI job that calls the venv's
python by path gets no OpenCV.

---

## 6. Reproducing this

```bash
# 1. arms B and D
products/hairline/bench/instances.sh up graviton c8g.4xlarge
products/hairline/bench/instances.sh up hybrid   c7i.4xlarge
# wait for /var/log/bench-ready on each

export BENCH_SSH_KEY=~/.ssh/opencv26-hairline.pem
export BENCH_GRAVITON_HOST=ubuntu@<dns>  BENCH_GRAVITON_TYPE=c8g.4xlarge
export BENCH_GRAVITON_PYTHON=/opt/bench/bin/python
export BENCH_HYBRID_HOST=ubuntu@<dns>    BENCH_HYBRID_TYPE=c7i.4xlarge
export BENCH_HYBRID_PYTHON=/opt/bench/bin/python

python -c "import products.hairline.bench.workload" \
  && python -m bench.run hairline_frame --repeats 15 --out products/hairline/bench/out

# 2. arm A (accept the Marketplace terms once, in the console, first:
#    https://aws.amazon.com/marketplace/pp/prodview-fdvbfiewzuehs)
products/hairline/bench/instances.sh up cool     c8g.4xlarge   # 50 GB root, no cloud-init
products/hairline/bench/instances.sh up graviton c8g.4xlarge   # arm B again, same session
export BENCH_COOL_HOST=ubuntu@<dns> BENCH_COOL_TYPE=c8g.4xlarge
for w in hairline_frame hairline_threshold hairline_contours crack_pipeline; do
  python -m bench.run $w --repeats 15 --warmup 3 --arms graviton-pip,graviton-cool \
    --out products/hairline/bench/out/arm-a
done
# The recorded run used a two-line wrapper on the instance that sources
# /opt/cool/venvs/python_3.12/bin/activate and execs python3 (BENCH_COOL_PYTHON pointed
# at it). The harness now sets the same PYTHONPATH and LD_LIBRARY_PATH for the COOL arm
# itself, so the wrapper is no longer needed.

# 3. always
products/hairline/bench/instances.sh down
```

### Instances started and stopped

| Instance | Type | Purpose | Started (UTC) | Terminated (UTC) | Wall | Spot $/hr | Cost |
|---|---|---|---|---|---|---|---|
| `i-0a6e675cd2c58a483` | c8g.4xlarge | arm B, Graviton stock wheel | 05:00:28 | 05:09:22 | 9 min | $0.2512 | **$0.038** |
| `i-036a60724d96032eb` | c7i.4xlarge | arm D, cost-matched x86 | 05:02:38 | 05:09:22 | 7 min | $0.3088 | **$0.036** |
| `i-011d9ab8952e1efb1` | c8g.4xlarge | arm A, COOL AMI | 23:56:01 | 23:59:53 | 4 min | $0.2703 + $0.04 fee | **$0.020** |
| `i-017d88171112503e2` | c8g.4xlarge | arm B again, same session as A | 23:56:38 | 23:59:53 | 3 min | $0.2473 | **$0.013** |

All four confirmed terminated. `bench/instances.sh list` returns nothing, and `down`
terminates by tag rather than by id so a forgotten identifier cannot leave one
running. Arm E was measured on the already-running demo instance and started nothing.

Spot prices are the us-east-1 price history for each instance's zone at launch. The
arm A cost assumes the Marketplace fee is billed per second like the instance. If it is
billed by the whole hour, arm A cost $0.31. We have not checked the bill.

Total benchmark compute: **about 11 cents**, or 40 cents if the COOL fee bills a full hour.

### A footnote on the quota

The account's on-demand quota increase to 96 vCPUs, requested at 03:18 UTC because a
16-vCPU instance could not otherwise launch, **was approved at about 06:40** — after
these numbers were taken. The benchmark ran on Spot, which is the same hardware and a
separate quota, so nothing here would have changed. The increase matters only for
anyone reproducing it on demand, who no longer has to.
