# Architecture

Three diagrams: what happens to a frame, what runs on AWS, and where the Cloud
Optimized OpenCV Library sits in that.

---

## 1. The measurement pipeline

Every box is OpenCV 5 unless it says otherwise. The two stages marked **[COOL]** are
the ones the Cloud Optimized OpenCV Library names in its own listing, and they are
where the per-frame time goes.

```mermaid
flowchart TB
    subgraph IN["Input"]
        V["Video or still<br/>cv2.VideoCapture"]
    end

    subgraph GATE["Frame gate — every rejection is recorded with a reason"]
        F["Focus score<br/>cv2.Laplacian variance"]
        E["Exposure<br/>cv2.calcHist, clipping fraction"]
        M["Marker detection<br/>cv2.aruco.ArucoDetector<br/>CORNER_REFINE_SUBPIX"]
        C["Calibration<br/>cv2.getPerspectiveTransform<br/>homography decomposition for tilt"]
        R1(["REFUSE<br/>OUT_OF_FOCUS · MOTION_BLUR<br/>UNDER_EXPOSED · OVER_EXPOSED · CLIPPED<br/>NO_MARKER · MARKER_TOO_SMALL<br/>TOO_OBLIQUE · HIGH_RESIDUAL<br/>INCONSISTENT_SCALE"])
    end

    subgraph GEOM["Plane geometry"]
        J["PlaneMap: 2x2 Jacobian of the<br/>millimetre → pixel map, per point"]
        P["Blur sigma from the marker's<br/>own printed edges, 25–75% rise"]
    end

    subgraph SEG["Segmentation — the heavy per-frame work"]
        T["**[COOL]** cv2.adaptiveThreshold<br/>ADAPTIVE_THRESH_GAUSSIAN_C<br/>constant set from the surface's own<br/>residual spread, not a fixed number"]
        MO["cv2.morphologyEx OPEN then CLOSE"]
        CC["cv2.connectedComponentsWithStats<br/>area · elongation · contrast vs surround"]
        DT["cv2.distanceTransform<br/>→ medial axis, never a filled contour"]
        FC["**[COOL]** cv2.findContours<br/>RETR_EXTERNAL per component"]
        BR["Branch split at junctions<br/>cv2.distanceTransformWithLabels"]
    end

    subgraph W["Width, perpendicular on the wall"]
        O["Local tangent from the<br/>structure tensor, cv2.Sobel + boxFilter"]
        N["Plane normal = J⁻¹ tangent, rotated<br/>Sampling direction in image = J · normal"]
        PR["cv2.remap sub-pixel profile<br/>across the crack"]
        IV["Invert the blurred-box model<br/>w from half-depth and sigma"]
        U["Uncertainty budget<br/>scale · coplanarity · sampling<br/>quantisation · noise · blur"]
        R2(["REFUSE per crack<br/>BELOW_RESOLUTION with an upper bound<br/>UNCERTAINTY_TOO_LARGE<br/>TOO_FEW_SAMPLES"])
    end

    subgraph OUT["Output"]
        OV["Overlay: measurement ladder,<br/>scale bar, title block<br/>cv2.FontFace (new in OpenCV 5)"]
        RR["RunRecord → JSON, CSV schedule"]
    end

    V --> F --> E --> M --> C
    F -.-> R1
    E -.-> R1
    M -.-> R1
    C -.-> R1
    C --> J --> T
    C --> P
    T --> MO --> CC --> DT --> FC --> BR --> O
    J --> N
    P --> IV
    O --> N --> PR --> IV --> U --> RR
    IV -.-> R2
    U -.-> R2
    BR --> OV
    U --> OV --> RR
```

The two things in that diagram that are easy to get wrong, and that the evaluation
exists to check:

- **`DT` never runs on a filled contour.** Filling an open crack path and taking the
  maximum of the distance transform measures the area the path encloses, not the
  stroke. `research/FINDINGS.md` §5.0 recorded that reading 283 mm for a 0.75 mm
  crack. `tests/test_width_math.py` keeps that bug executable and asserts the engine
  contains no filled-contour call.
- **`N` uses the Jacobian, not a single scale.** Perpendicular-on-screen and
  perpendicular-on-the-wall are different directions as soon as the camera is not
  square on, and one pixel is a different number of millimetres in each.

---

## 2. AWS

```mermaid
flowchart LR
    U["Inspector's browser"]

    subgraph AWS["AWS · us-east-1 · account <aws-account-id> · Project=opencv26"]
        subgraph VPC["Default VPC"]
            subgraph EC2["EC2 c8g.large — AWS Graviton4, arm64"]
                CAD["Caddy<br/>automatic TLS via sslip.io"]
                DOC["Docker container<br/>uvicorn + FastAPI (servicekit)"]
                ENG["hairline engine<br/>opencv-python-headless 5.0.0.93<br/>aarch64 wheel, KleidiCV HAL"]
                SMP["Bundled samples<br/>walk-past.mp4 + two refusal stills"]
            end
            SG["Security group<br/>80/443 open · 22 from one address"]
        end
        ECR["ECR<br/>opencv26/hairline<br/>linux/arm64, scan on push"]
        S3["S3<br/>opencv26-artifacts-…<br/>benchmark outputs"]
        CW["CloudWatch<br/>instance metrics"]
    end

    subgraph BENCH["Benchmark only — launched, measured, terminated"]
        B1["c8g EC2<br/>COOL AMI<br/>ami-01db31139bc5615d8"]
        B2["c8g EC2<br/>stock pip wheel"]
        B3["c7i EC2<br/>x86, IPP HAL"]
    end

    U -->|HTTPS| CAD --> DOC --> ENG
    SMP --> ENG
    ECR -->|docker pull at boot| DOC
    SG --- EC2
    EC2 --> CW
    B1 & B2 & B3 -->|results| S3
```

**Why EC2 and not App Runner.** Every other product in this workshop deploys to App
Runner, which is simpler and cheaper. Hairline does not, for one reason: the Cloud
Optimized OpenCV Library is delivered as an Amazon Machine Image, so it runs on EC2
and on nothing else, and only on the `c8g`, `m8g` and `r8g` families. An entry
claiming COOL has to be an EC2 architecture. The demo endpoint therefore runs on the
same instance family the benchmark runs on, so the version banner a judge sees is
the architecture the benchmark describes.

**No load balancer.** An idle Application Load Balancer is about $16 a month for
nothing. Caddy on the instance terminates TLS with a certificate from Let's Encrypt,
using an `sslip.io` name that resolves to the instance's own address, so there is no
domain to buy and no balancer to pay for.

---

## 3. Where COOL sits, and what it is being compared against

```mermaid
flowchart TB
    W["One workload: the Hairline per-frame call sequence<br/>adaptiveThreshold → morphology → connectedComponents<br/>→ distanceTransform → findContours, on a 4K frame"]

    subgraph ARMS["Four arms, same source, shipped over ssh and run in place"]
        A["**A · graviton-cool**<br/>c8g · COOL AMI<br/>/opt/cool/venvs/python_3.12"]
        B["**B · graviton-pip**<br/>c8g · pip opencv-python-headless 5.0.0.93<br/>⚠ this wheel ALREADY ships KleidiCV 26.03"]
        C["**C · local-x86_64-pip**<br/>this workstation · IPP HAL"]
        D["**D · hybrid-x86**<br/>c7i · cost-matched x86"]
    end

    R["latency p50 / p95 · throughput<br/>cost per 1000 frames · speedup vs **arm B**"]

    W --> A & B & C & D --> R
```

Arm B exists because of the one thing that sinks most COOL submissions. The stock
PyPI **aarch64** wheel already bundles Arm's KleidiCV 26.03, confirmed by `strings`
on `cv2.abi3.so`:

```
Custom HAL:   YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))
3rdparty:     ... tegra_hal kleidicv_hal kleidicv kleidicv_thread
```

So `pip install opencv-python` on Graviton is **not** an unaccelerated baseline.
Benchmarking COOL-on-Graviton against pip-on-x86 measures the processor, not the
library. Every speedup Hairline reports is computed against arm B, and
[`docs/cool-benchmark.md`](docs/cool-benchmark.md) states this in the results table
itself rather than in a footnote.
