# The COOL benchmark

**Submitted for the Best Use of COOL award. Read §1 first: one arm of this benchmark
did not run, and the reason is a manual step nobody could take from a terminal.**

Measured 16 September 2026, us-east-1, account <aws-account-id>. Raw JSON and the
per-workload markdown are in [`../bench/out/`](../bench/out); the workload definitions
are in [`../bench/workload.py`](../bench/workload.py) and the instance launcher in
[`../bench/instances.sh`](../bench/instances.sh).

---

## 1. What did not run, and why

The Cloud Optimized OpenCV Library is an AWS Marketplace AMI. Using it requires
accepting the Marketplace terms, and **there is no API for that**. The full command
inventory of every marketplace service in the AWS CLI — `marketplace-agreement`,
`marketplace-catalog`, `marketplace-deployment`, `marketplace-entitlement`,
`marketplace-reporting` — contains no subscribe or accept-agreement operation.
`marketplace-catalog start-change-set` is the seller-side publishing API, not a buyer
action. Accepting terms is a browser-console click, and this build had programmatic
credentials only.

Verified rather than assumed. `run-instances --dry-run` creates nothing and answers
the question exactly:

```
$ aws ec2 run-instances --dry-run --region us-east-1 \
    --image-id ami-01db31139bc5615d8 --instance-type c8g.large \
    --subnet-id subnet-0b6a703d8a9a3ded3

An error occurred (OptInRequired) when calling the RunInstances operation:
In order to use this AWS Marketplace product you need to accept terms and subscribe.
To do so please visit https://aws.amazon.com/marketplace/pp?sku=aajkmdd4qo3r7yhqg61a7aah9
```

The same dry run against the plain Ubuntu arm64 AMI returns `DryRunOperation: Request
would have succeeded`, so the block is the Marketplace opt-in and not IAM, networking,
capacity or quota. Cross-checked from the buyer side:

```
$ aws marketplace-agreement search-agreements --catalog AWSMarketplace \
    --filters '[{"name":"PartyType","values":["Acceptor"]},
                {"name":"AgreementType","values":["PurchaseAgreement"]}]'
→ 12 active agreements, all proposed by 979382823631 (Bitnami).
  None from 679593333241 (OpenCV).
```

So **arm A did not run**, and rather than guess at what it would have done, this
report presents the three arms that did and states precisely what arm A would add.
Everything needed to run it is committed: `bench/instances.sh up cool` launches the
COOL AMI and the harness already knows to use `/opt/cool/venvs/python_3.12/bin/python`.
One console click and one command completes the table.

The AMI is identified as `ami-01db31139bc5615d8`, "COOL-Graviton4-v2-AMI-prod-k6u24vxijyzvg",
product code `aajkmdd4qo3r7yhqg61a7aah9`, created 2026-04-22. **UNVERIFIED:** the listing
id `prodview-fdvbfiewzuehs` could not be mapped to a product code from the CLI —
`describe-entity` returns `ResourceNotFoundException` because the Catalog API only
resolves entities the account owns as a seller — so that AMI is inferred from its name
matching "For AWS Graviton4", and the same product code also covers a Graviton5 image.

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

One workload, four arms, the same source shipped to each over ssh and executed there.

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
actually paid, because on-demand is the number a reader can check.

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
| **A · graviton-cool** | c8g.4xlarge | 16 | — | — | — | — | _did not run: Marketplace subscription is console-only_ |
| **B · graviton-pip** | c8g.4xlarge | 16 | **75.31** | 75.41 | 13.28 | **$0.01335** | KleidiCV 26.03 |
| **C · local x86** | this workstation | 22 | 86.34 | 88.82 | 11.58 | — | Intel IPP 2026.0.0 |
| **D · hybrid-x86** | c7i.4xlarge | 16 | 99.29 | 100.03 | 10.07 | $0.01969 | Intel IPP 2026.0.0 |
| **E · the deployed endpoint** | c8g.large | 2 | 134.25 | 136.01 | 7.45 | **$0.00297** | KleidiCV 26.03 |

Arm E is not part of the comparison. It is the live demo instance, measured in place,
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

### 4.3 Cost

Per thousand 4K frames, at us-east-1 on-demand rates:

- c8g.4xlarge, Graviton4: **$0.01335**
- c7i.4xlarge, x86: **$0.01969** — 47% more for the same work
- c8g.large, Graviton4, 2 vCPU: **$0.00297**

The third line is the one worth sitting with. Dropping from 16 vCPUs to 2 makes each
frame **1.78× slower** and **4.5× cheaper**, because the instance costs one eighth as
much. For a survey that is a batch job rather than an interactive request, the small
instance is strictly the better buy, and the only reason to take the large one is
latency you have promised somebody.

---

## 5. What arm A would add, and what we can and cannot say without it

**What can be said now.** The Arm/KleidiCV path is faster and materially cheaper than
cost-matched x86 on this workload, and the advantage is concentrated in exactly the
per-pixel kernels KleidiCV names. That result stands on its own and is reproducible
from this repository in about fifteen minutes.

**What cannot be said.** Nothing about COOL's speedup over a stock Arm wheel, because
the measurement was not taken. COOL's listing claims a benchmark "across 78 widely
used imgproc, core, and I/O functions against the default OpenCV 5.0.0 build" and
publishes an average speedup as an image on the listing page, which we could not read
and therefore do not quote. We will not repeat a vendor figure we have not verified,
and we will not model, extrapolate or estimate what arm A would have shown.

**What we expect, stated as an expectation and not a result.** The stock wheel's
`Baseline: NEON FP16` is generic ARMv8, while Graviton4 is Neoverse-V2 with SVE2. A
build targeting that specific core, which is what COOL is, has real headroom on the
same functions KleidiCV already helps — and the 1.65× on `adaptiveThreshold` is
evidence that this workload responds to kernel-level work. Whether COOL realises that
headroom is a measurement, and it is the one measurement missing here.

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
standing up Arm CI, that is the product, and it is worth saying so plainly even though
we could not put a number on the latency.

---

## 6. Reproducing this

```bash
# 1. the two arms that ran
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

# 2. the arm that did not
#    accept the terms at https://aws.amazon.com/marketplace/pp/prodview-fdvbfiewzuehs
products/hairline/bench/instances.sh up cool c8g.4xlarge
export BENCH_COOL_HOST=ubuntu@<dns> BENCH_COOL_TYPE=c8g.4xlarge
python -m bench.run hairline_frame --repeats 15 --out products/hairline/bench/out

# 3. always
products/hairline/bench/instances.sh down
```

### Instances started and stopped

| Instance | Type | Purpose | Started (UTC) | Terminated (UTC) | Wall | Spot $/hr | Cost |
|---|---|---|---|---|---|---|---|
| `i-0a6e675cd2c58a483` | c8g.4xlarge | arm B, Graviton stock wheel | 05:00:28 | 05:09:22 | 9 min | $0.2512 | **$0.038** |
| `i-036a60724d96032eb` | c7i.4xlarge | arm D, cost-matched x86 | 05:02:38 | 05:09:22 | 7 min | $0.3088 | **$0.036** |

Both confirmed terminated. `bench/instances.sh list` returns nothing, and `down`
terminates by tag rather than by id so a forgotten identifier cannot leave one
running. Arm E was measured on the already-running demo instance and started nothing.

Total benchmark compute: **under eight cents.**
