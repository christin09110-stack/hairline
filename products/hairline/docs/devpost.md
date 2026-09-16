# Hairline: Devpost submission

Paste each section into the matching Devpost field. Every number here comes from
`docs/evaluation.md`, `docs/cool-benchmark.md` or `docs/report.md`, or from the run
records they cite.

- **Live:** https://54-224-119-247.sslip.io (AWS Graviton4, `c8g.large`, us-east-1)
- **Repository:** https://github.com/christin09110-stack/hairline
- **OpenCV:** 5.0.0 (`opencv-python-headless==5.0.0.93`), pinned

## Elevator pitch

Photograph a concrete crack next to a scale reference and Hairline gives you its width
in millimetres, with an uncertainty. When the photo can't support a number, it refuses
and tells you why.

## Awards we opt into

- **Overall.**
- **Best Use of COOL.** Hairline's per-frame work is adaptive Gaussian thresholding and
  contour extraction on 4K frames, two of the functions the Cloud Optimized OpenCV
  Library names in its listing. It runs on AWS Graviton4, and `docs/cool-benchmark.md`
  sets up a four-arm benchmark. One arm is missing. The COOL AMI arm did not run: it
  needs the AWS Marketplace terms accepted by hand, and the account owner has been
  asked to do that. The harness and launcher for that arm are committed, so it is one
  console click and one command away.

## Inspiration

The first version of our crack-width routine filled an open contour and took the
maximum of its distance transform. It reported 283 mm for a 0.75 mm crack. Nothing
crashed and nothing warned. The number looked like any other number.

Crack width is what separates a cosmetic crack from one an engineer needs to see.
Today an inspector holds a comparator card against the wall, reads a width by eye and
writes it down. Nobody can check that reading later, two inspectors may not agree on
it, and it has no uncertainty attached. ASCE's 2025 Infrastructure Report Card counts
623,218 bridges in the US, and about a third of them (221,791 spans) need repair work
or replacement. We wanted a width you could still defend six months later, and a tool
that says so when it can't give you one.

## What it does

Tape a printed marker of known size flat on the wall next to the crack, then walk past
with a phone. Hairline:

1. scores every frame for focus and exposure and finds the marker;
2. recovers the wall plane from the marker, so the scale in pixels per millimetre is
   correct across the frame even when the camera is not square on;
3. finds dark, thin, long components that are really darker than the surface around
   them, and cuts crack networks at their junctions so each run is measured on its
   own;
4. reads intensity profiles across each crack, perpendicular to it on the wall rather
   than on the screen;
5. corrects each width for lens blur, measured from the marker's own printed edges in
   the same frame;
6. reports a width distribution per crack with an uncertainty budget, as a schedule
   you can export to CSV.

It also refuses, by name. No marker, marker too small, surface too oblique, out of
focus, exposure clipped, crack finer than the photo can resolve, too faint to be a
crack: each gets a code, a reason and what to do next. A crack below the resolution
limit gets an upper bound instead of a width.

**Real photos without our marker.** Public crack photos never contain Hairline's card,
so all of them end in `NO_MARKER`. Many real inspection photos do have a ruler, a crack
card or a crack monitor in frame, though. In manual scale mode you pick two points on
that reference and type in the distance between them. The uncertainty then charges for
what two points can't tell you (see How we built it). A black-hat segmentation mode
handles rough real surfaces where the default threshold breaks a crack into fragments.

**Accuracy, on synthetic targets of known width** (stand-off, angle, defocus, lighting
and noise varied; 266 readings attempted):

| | |
|---|---|
| Measured | 103 |
| Declined, each with a named reason | 163 (61%) |
| Median error | −0.61%, which is 6 micrometres |
| Absolute error, 95th percentile | 2.1% (0.036 mm) |
| Worst single error | 5.6% (0.056 mm) |
| Stated 95% interval contained the truth | 98.1% |

The last row is the one we'd ask you to look at. An uncertainty that doesn't cover the
error is decoration.

**On two real test photos with a manual scale:** [PENDING: cracks found, widths with
expanded uncertainty, refusals, from `media/real/hairline/scaled/runs/{dsc07068,dnipro}/run.json`.]

## How we built it

**OpenCV 5.0.0, pinned** to `opencv-python-headless==5.0.0.93`. Unpinned, pip resolves
to 4.14.x, which shipped after 5.0.0, so `import visioncore` raises on a 4.x wheel.

**Scale.** `cv2.aruco.ArucoDetector` with sub-pixel corner refinement finds the marker
(it's in the main wheel in OpenCV 5, so no contrib install), and
`cv2.getPerspectiveTransform` gives the homography. We don't divide by one pixels-per-mm
number. At each sample we use the 2×2 Jacobian of the plane map: the crack's normal on
the wall maps to an image direction, and one pixel along that direction is exactly
`1/|J n|` mm. Across the sweep, scale recovery had a median error of 0.082% and a worst
case of 0.284%.

**Segmentation.** `cv2.adaptiveThreshold` (Gaussian), `morphologyEx`,
`connectedComponentsWithStats` and `findContours`. The threshold constant is measured
per image from the surface's own texture spread instead of being a magic number. For
rough real surfaces there is a `MORPH_BLACKHAT` mode with hysteresis.

**Width.** A crack of width `w` blurred by `σ` has a half-depth width that levels off at
2.355σ as `w` shrinks, so the textbook half-depth reading can't see below the blur. We
invert the blurred-box model, with `σ` measured from the marker's edges. Where no
inversion exists we stop: a crack must span at least 4.25σ and 4 px or it is refused.
With that gate turned off, the half-depth reading returns 0.31 mm for a 0.20 mm crack.

**Uncertainty.** Six terms in quadrature (scale, coplanarity, sampling, quantisation,
estimator noise, blur calibration), reported at k = 2. With a manual scale the scale
term becomes `2 × click error / span` plus `1/cos(tilt) − 1` for a tilt the operator
vouches for (1.54% at the default 10°), and blur falls back to a stated default of
0.9 px.

**OpenCV 5 specifics.** `cv2.FontFace` renders the survey annotations in a real
TrueType face. `VideoCapture.get` now returns −1 for unsupported properties, where 4.x
returned 0, and we handle that.

**AWS.** An arm64 Docker image in Amazon ECR runs on a `c8g.large` (Graviton4) EC2
instance behind Caddy, with a Let's Encrypt certificate for an `sslip.io` name. There is
no load balancer and no inbound SSH; shell access goes through SSM. We chose EC2 over
App Runner for one reason: COOL is an AMI that runs on c8g, m8g and r8g instances, and
App Runner has no Arm option. The demo runs on the same instance family the benchmark
measures, and `/version` prints `KleidiCV (ver 26.03)` as its HAL.

**The COOL benchmark.** The product's own per-frame pipeline on a 4K frame, 15 timed
runs per arm:

| Arm | Instance | Median | $ per 1,000 frames |
|---|---|---:|---:|
| A, COOL AMI | c8g.4xlarge | did not run | |
| B, stock wheel on Graviton4 | c8g.4xlarge | 75.31 ms | $0.01335 |
| C, local x86 workstation | 22 vCPU | 86.34 ms | |
| D, cost-matched x86 | c7i.4xlarge | 99.29 ms | $0.01969 |
| E, the live demo instance | c8g.large | 134.25 ms | $0.00297 |

Per function, Graviton4 against x86: `adaptiveThreshold` 1.65× faster, `findContours`
1.08×. The speedup lands on the per-pixel kernel KleidiCV accelerates, and hardly at
all on the sequential contour trace.

**Testing.** 108 tests assert measured millimetres against drawn millimetres, check that
the stated uncertainty covers the error, and fire every refusal path. A calibration gate
renders the printable calibration card, measures its lines, and blocks the image build
if it can't reproduce them.

## Challenges we ran into

**The stock wheel is already accelerated.** The PyPI aarch64 `opencv-python` wheel ships
Arm's KleidiCV 26.03. `pip install` on Graviton is not an unaccelerated baseline, so
comparing COOL on Graviton against pip on x86 would have measured the processor rather
than the library. Every Arm comparison we make is against a stock wheel on the same
instance family.

**COOL needs a click we couldn't make.** Accepting AWS Marketplace terms has no CLI or
API. `run-instances --dry-run` against the COOL AMI returns `OptInRequired`; the same
dry run against plain Ubuntu arm64 succeeds. So arm A is missing, and we don't estimate
what it would have shown.

**A per-sample gate that looked right and wasn't.** We first applied the resolution
floor to individual width samples. For a crack just under the floor that dropped the
narrow samples and kept the wide ones, so a 0.35 mm crack came back measured at
0.382 mm. Deciding resolvability once per crack moved the worst error from 13.2% to
5.6%, and coverage from 95.4% to 98.1%.

**Staircase pixels.** Counting skeleton neighbours found 931 junctions in a skeleton
that had one. The crossing number fixed it.

**Deploying to arm64.** One `apt-get install` with five packages installed none of them,
because `awscli` isn't available on arm64 there. Caddy wouldn't start on a one-line
block. The first launch pulled buildx's cache manifest instead of the image. All three
are fixed in `infra/deploy.sh`, with the reason in a comment.

## Accomplishments that we're proud of

- A stated 95% interval that held the truth 98.1% of the time over 103 readings.
- Refusal as a first-class result: 163 declined readings, every one with a code, and
  refused rows kept in the CSV so a schedule never reads as a clean wall.
- A benchmark that says which arm is missing and why, and reports the speedup per
  function instead of averaging it away.
- The whole COOL benchmark, both instances included, cost under eight cents in compute.

## What we learned

No estimator recovers a width the photograph doesn't contain. With the gate off,
half-depth read a 0.20 mm crack as 0.311 mm, and the blur-corrected estimator read the
same crack 12% low. The answer was a refusal with an upper bound, not a cleverer formula.

A textbook baseline can quietly be someone else's optimised build. We only caught the
KleidiCV wheel because the instances printed their own HAL.

## What's next

- Run arm A once the Marketplace terms are accepted.
- Print the calibration target, photograph it, and check the printed widths. Every
  accuracy figure above comes from rendered targets.
- Measure real cracks next to a comparator card, which is the only way to compare
  against current practice.
- A two-view check on coplanarity, the largest assumption in the budget and the one a
  single view can't test.

## Built with

opencv, opencv-python-headless-5.0.0.93, python, numpy, fastapi, uvicorn, pydantic, docker, caddy, aws-ec2, aws-graviton4, amazon-ecr, aws-systems-manager, lets-encrypt

## Try it out

- **Live:** https://54-224-119-247.sslip.io
- **Repository:** https://github.com/christin09110-stack/hairline

Three samples are bundled in the left rail: a walk-past that produces a crack schedule,
and two that produce refusals. `/version` shows the OpenCV build and HAL the demo is
running, and `/api/calibration` shows the latest calibration gate result. To try your
own wall, download the marker from `/api/target/marker.png?dpi=600`, print it at 100%,
and measure the printed square with a ruler before you enter its size.

## Footage credits

Real photographs, used with a manual scale:

- **"Crack DSC07068.JPG"** by IJD Dublin. Public domain.
  https://commons.wikimedia.org/wiki/File:Crack_DSC07068.JPG
- **"Crack monitor in Dnipro.jpg"** by Alex Blokha. CC BY-SA 4.0. Shown with Hairline's
  measurement overlay.
  https://commons.wikimedia.org/wiki/File:Crack_monitor_in_Dnipro.jpg

[PENDING: credits for the real development photos, from `eval/real_dev/README.md`.]

Everything else shown is rendered by code in the repository. The survey annotations use
Barlow Condensed under the SIL Open Font License 1.1.
