# Hairline — technical report

**Crack widths in millimetres from a phone walk-past, with an uncertainty, and a
stated refusal where the photograph cannot support a number.**

OpenCV AI Competition 2026. OpenCV 5.0.0 (`opencv-python-headless==5.0.0.93`) on
AWS Graviton4.

---

## 1. The problem

Concrete cracks, and the question of whether a crack is cosmetic or structural, come
down to width. An inspector holds a crack comparator card against the surface, reads
a number by eye, and writes it on a sheet. The number is not traceable, it is not
repeatable between inspectors, and it carries no uncertainty.

The scale of the inventory that gets inspected this way, from the American Society of
Civil Engineers' *2025 Infrastructure Report Card*, bridges category (fetched
16 September 2026; the page text is saved at `research/sources/asce-2025-bridges.txt`
and quoted in `research/EVIDENCE-bridges.md`):

> "There are 623,218 bridges in the country, with an average age of about 47 years"

> "About a third of the nation's bridge inventory (221,791 spans) needs repair work or
> replacement. Approximately 45% of bridges have exceeded their planned design lives
> of 50 years."

> "63,085 of the nation's 623,218 bridges were posted for load in 2024"

> "ASCE's Bridging the Gap report indicates there is a funding gap of $373 billion
> over 10 years to bring the nation's bridges into a state of good repair."

The same page describes a specific failure of the inspection process itself:

> "The National Transportation Safety Board (NTSB) concluded that critical lapses in
> maintenance and oversight by multiple agencies led to the bridge's collapse.
> Although previous inspections had repeatedly documented issues with the bridge,
> maintenance and repairs were not performed to resolve these issues. Additionally,
> Pennsylvania Department of Transportation (PennDOT) contractors conducted
> inspections that did not comply with guidance and failed to identify
> fracture-critical areas on the bridge's legs."

That paragraph contains two different failures and Hairline addresses only the
second. Inspections that were performed but did not record what was there is a
measurement problem. Documented problems that nobody acted on is not, and no camera
fixes it. We are careful to claim only the first.

**What this report does not claim.** The ASCE page gives no crack-width threshold, no
inspection interval, and no figure for how often a crack is mismeasured in current
practice. We therefore make no claim about how accurate inspectors are today, and
Hairline ships no crack-width limit from any design code. There is no published trial
of a deployed camera-based inspection product improving an outcome in this or any
adjacent domain, and we do not imply one.

## 2. Users

The inspector with a phone and a printed card, who needs a width they can defend six
months later. The reviewing engineer, who needs to know which readings to trust: a
schedule where every row carries an uncertainty and the unmeasurable rows say so is
worth more than a schedule of bare numbers. And the asset owner, who needs the same
crack measured the same way on the next visit.

Hairline is a measuring instrument. It does not decide whether a structure is safe.

## 3. Architecture

Diagrams are in [`architecture.md`](architecture.md). In words: a video or still goes
through a frame gate that rejects anything unusable and says why; surviving frames are
grouped into stations by how far the camera moved along the wall; each station is
segmented, each crack run is measured perpendicular to itself on the wall, and each
measurement gets an uncertainty budget. The output is a `RunRecord` — the same JSON
shape every product in this workshop emits — plus a CSV crack schedule and an
annotated frame per station.

The service is `servicekit`'s FastAPI app with one analyzer function. The vision
primitives — OpenCV 5 assertion, marker calibration with its refusal path, video
iteration, stage timing, run records — are the shared `visioncore` package. Hairline
is about 2,300 lines on top of that.

## 4. The OpenCV 5 implementation

### 4.1 Scale, and why one number is not enough

`cv2.aruco.ArucoDetector` with `CORNER_REFINE_SUBPIX` locates the printed marker;
`cv2.getPerspectiveTransform` fits the homography **H** from plane millimetres to
image pixels. That much is ordinary. What matters is what happens next.

The naive step is to divide by one px-per-mm number recovered at the marker. That is
wrong twice over as soon as the camera is not square to the wall. The scale varies
across the frame, and the direction perpendicular to the crack *in the image* is not
the direction perpendicular to it *on the wall*.

Hairline works with the 2×2 Jacobian **J** of the projective map at each sample:

- the crack centre line is mapped into plane coordinates, where its normal `n` is the
  true perpendicular on the wall;
- the image direction to sample along is the unit vector of `J n`;
- one pixel along that direction is exactly `1 / |J n|` millimetres on the wall.

Perspective is corrected analytically, per sample, with no resampling. A rectified
view is produced for the report picture and never carries a number, because warping
costs the sharpness the measurement depends on.

This also produces the quantity that decides whether a crack is measurable at all:
`|J n|` is the sampling density the camera actually gave us *across that crack*,
which under a tilt is smaller than the isotropic scale and different for two cracks
running in different directions in the same frame.

**Measured:** scale recovery is within 0.1% of ground truth out to 50 degrees of
apparent foreshortening (§6).

### 4.2 Segmentation, and the constant that is not a constant

`cv2.adaptiveThreshold` with `ADAPTIVE_THRESH_GAUSSIAN_C`, then
`cv2.morphologyEx` open and close, then `cv2.connectedComponentsWithStats`, then
`cv2.findContours` per surviving component. On a 4K frame this is where the time goes,
and two of those functions are named in the Cloud Optimized OpenCV Library's own
listing, which is why this product carries the COOL benchmark.

The adaptive threshold's constant `C` is normally a magic number. Hairline measures it.
A quarter-scale pass computes the image's robust spread about its own local mean, and
`C` is set to 2.4 of those. "Six grey levels below the local mean" means something
different on a rough soffit and a polished column; "2.4 standard deviations into the
tail of this surface's own texture" means the same thing on both. On the synthetic
scenes the recovered `C` ranges from about 7 to 24 with no change to any other
parameter.

Four filters then run in increasing order of cost — area, bounding-box elongation,
mean darkness against a dilated ring of the surround, and mean width from area over
skeleton length. The contrast test is against the surface's own residual spread as
well as an absolute floor. That one change removed nine false positives in a single
frame: texture artefacts sitting 6 to 7 standard deviations above their surround,
where real cracks ran 11 to 40.

Components touching the frame edge are refused rather than measured, because their
extent is unknown and their profiles cannot be completed on one side.

### 4.3 Crack networks

Cracks cross. Left as one connected component, a 0.8 mm crack crossing a 0.3 mm crack
reports a single distribution describing neither, and the headline figure lands
somewhere in between — a width that exists nowhere on the wall. Hairline thins the
component to a one-pixel centre line, cuts it at its junctions, and measures each run
separately. On the test case it turns a merged reading of 1.567 mm into 1.594 mm and
0.396 mm against drawn widths of 1.60 and 0.40.

Two things had to be got right for that to work, and both were wrong first:

- **Thinning, not ridge-finding.** The local maxima of the distance transform are a
  fine centre line on a thin stroke and a broken scatter on a wide one: the 1.6 mm
  crack came out in 65 disconnected pieces. `cv2.ximgproc.thinning` would solve it but
  ximgproc is contrib-only and the contrib wheel conflicts with the main one, so
  Zhang-Suen thinning is implemented here as array operations.
- **Crossing number, not neighbour count.** An 8-connected diagonal is digitised as a
  staircase, and a staircase pixel has three or four neighbours while being an
  ordinary point on a line. Counting neighbours found 931 junctions in a skeleton with
  one. The crossing number — transitions from 0 to 1 around the neighbourhood — is 2
  on a line however it is drawn, and 3 or more only where branches actually meet.

### 4.4 Width, and the estimator that matters

A crack in an image is a dark band of width `w` and intrinsic darkness `D0`, blurred
by the lens into

    p(x) = D0/2 · [ erf((x + w/2)/(√2 σ)) − erf((x − w/2)/(√2 σ)) ]

The textbook reading is the full width at half the observed depth. It is right for a
wide crack and badly wrong for a narrow one, because as `w` falls the half-depth width
does not fall with it — it flattens out at `2.355 σ`, the width of the blur itself.

Since that half-depth width is a known monotonic function of `w` and `σ`, and `σ` is
measurable, it can be inverted. `σ` comes from the marker's own printed edges in the
same frame, from the 25%-to-75% rise distance of the intensity across them: the card
that supplies the scale also calibrates the deconvolution, at the same focus, with no
separate calibration shot. Below `2.355 σ` the function is flat and no inversion
exists. That is not a limitation to work around; it is the resolution limit of the
photograph, and it is where Hairline stops reporting a width.

Three other estimators are implemented and compared in [`evaluation.md`](evaluation.md):
the raw half-depth reading, an area-over-depth ratio that is blur-invariant in
principle and the noisiest of the four in practice, and the width the binary mask
implies via its medial axis, kept as a cross-check because it moves with wherever the
threshold fell.

Inverting the model per sample by bisection cost 8 ms a profile, which on a 4K frame
with a thousand profiles was most of the run. The curve is tabulated once per frame
and every sample after that is an interpolation: 9.4 s to 0.85 s, with the answers
agreeing to four decimal places.

**Sampling along the crack.** Each sample's width is replaced by the median of its
spatial neighbours before the percentiles are taken. A crack's width genuinely varies
along its length and the estimate at each point also carries noise; taking the 95th
percentile of raw samples confounds the two, and for a target of exactly constant
width the raw p95 comes out above the truth by however noisy the estimator is. The
scatter left over after smoothing is a direct measurement of that noise, so it feeds
the uncertainty budget instead of being guessed.

### 4.5 Uncertainty

Six components, combined in quadrature, reported as a standard uncertainty and as an
expanded uncertainty at k = 2:

| Component | Where it comes from |
|---|---|
| Scale | marker fit residual over marker edge length, with a 0.4% floor |
| Coplanarity | assumed card-to-crack stand-off over working distance; an operator input with a stated default, not a measurement |
| Sampling | bootstrap of the reported percentile over the smoothed width series |
| Quantisation | 0.15 px of sub-pixel interpolation noise |
| Estimator noise | the scatter of raw samples about the locally smoothed value |
| Blur calibration | ∂w/∂σ at the solution, times the standard error of σ across the marker edges |

The last one is the interesting one. Near the resolution limit `∂w/∂σ` is large, so a
crack barely wider than the lens blur carries an uncertainty that says, correctly,
that it cannot be known better than the blur is known. The interval widens by itself
where the answer is weak. Across the sweep the stated 95% interval contained the truth
**98.1% of the time**, against a nominal 95% — see §6.

### 4.6 What OpenCV 5 specifically gave us

- **`cv2.FontFace`**, new in 5.0: the annotation on the survey frames is rendered
  through a real TrueType face (bundled Barlow Condensed, OFL 1.1) so it reads like
  drawing lettering rather than like a screenshot. The legacy Hershey fonts render
  through a TrueType engine in OpenCV 5 anyway and look different from 4.x, so there
  was no reason not to.
- **`cv2.VideoCapture.get` returning −1** for unsupported properties where 4.x
  returned 0. `visioncore.imageio` treats anything below zero as missing; the naive
  check would have produced a zero frame rate and timestamps of zero.
- **`cv2.aruco.ArucoDetector`** is in the main wheel in 5.0, so marker detection needs
  no contrib install. That matters because contrib and main conflict, and the contrib
  wheel would have been a 120 MB dependency for one detector.
- **`cv2.distanceTransformWithLabels`** with `DIST_LABEL_CCOMP` does the Voronoi
  assignment of mask pixels to crack branches in one call.
- The pin is `opencv-python-headless==5.0.0.93` everywhere, and `import visioncore`
  raises on a 4.x wheel. Unpinned, pip resolves to 4.14.x, which shipped after 5.0.0.

## 5. AWS deployment

A Docker image built for `linux/arm64` in ECR, and a **c8g.large** EC2 instance —
AWS Graviton4 — pulling it at boot with an instance-profile role, behind Caddy
terminating TLS with a Let's Encrypt certificate for an `sslip.io` name. No load
balancer, because an idle one is $16 a month for nothing. No inbound SSH: the
instance carries the SSM role, so a shell is `aws ssm start-session`.

This product is on EC2 while the other four in this workshop are on App Runner, for
one reason. The Cloud Optimized OpenCV Library is an Amazon Machine Image. It runs on
EC2, on the c8g, m8g and r8g families, and nowhere else, and App Runner has no Arm
option. An entry claiming COOL has to be an EC2 architecture, so the demo endpoint
runs on the same instance family the benchmark measures, and the version banner a
judge sees is the architecture the benchmark describes:

```
opencv 5.0.0 | machine aarch64 | threads 2 | baseline NEON FP16
HAL: YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))
```

What the deployment actually took, because the write-up is more useful than the
happy path:

- The first launch pulled buildx's `buildcache` manifest instead of the image, because
  the tag resolver took the most recently pushed tag and buildx pushes the cache last.
  That instance was terminated two minutes later and the resolver now excludes it by
  name.
- Cloud-init's `apt-get install -y docker-ce docker-ce-cli containerd.io awscli caddy`
  installed **none** of them, because a single apt transaction aborts entirely if one
  package is unavailable, and `awscli` is not installable on arm64 from the enabled
  repositories here. It is now one call per package, plus the official AWS CLI v2
  installer.
- Caddy refused to start on a one-line `reverse_proxy host { ... }`. Caddyfile braces
  open and close on their own lines. `caddy validate` now runs before `systemctl`, so
  a syntax error fails the boot loudly rather than leaving port 443 closed.

All three are fixed in `infra/deploy.sh`, and each carries the reason in a comment
rather than just the fix. Every resource, every rate, both things that are blocked, and
every instance this product started with its start and stop times are in
[`costs.md`](costs.md).

## 6. Evaluation

Full method, plots and tables in [`evaluation.md`](evaluation.md). The summary, for
the default estimator against synthetic targets of known width across stand-off,
viewing angle, defocus, lighting and sensor noise:

| | |
|---|---|
| Readings attempted | 266 |
| Measured | 103 |
| Declined, by name | 163, being 61% of a deliberately hostile sweep |
| Median error | −0.61%, which is 6 micrometres |
| Absolute error, 95th percentile | 2.1%, which is 0.036 mm |
| Worst single error | 5.6% |
| Stated 95% interval contained the truth | 98.1% against a nominal 95% |

and, with the gate turned off on the same scenes, the raw half-depth reading returning
**0.31 mm for a 0.20 mm crack** — while the corrected estimator reads 12% low on the
same frame. Neither recovers a width the photograph does not contain, which is why the
answer is the gate.

**One exclusion, stated plainly because a reader who diffs the row counts will find
it.** The sweep produces 1,312 rows. The accuracy figures above are scored over 266 of
them: the default estimator, one row per drawn crack per scene, **excluding 62 rows
whose refusal code is `NOT_VISIBLE`**. A crack under about 1.6 pixels wide at that
geometry leaves no mark at all, so any component overlapping its path is a different
feature, and scoring a reading against it measures our labelling rather than the
pipeline. In an earlier run exactly that produced a 1,436% "error" from a component
that was really a 1.5 mm crack lying near a 0.10 mm crack's path under a 21 degree
tilt. **Those rows are counted and published; they are never scored.** They are in
`eval/out/accuracy.json` with that code on them, and `docs/evaluation.md` §1 gives the
rule that produces them.

The targets are synthetic because a measuring instrument needs targets known to better
than its own precision, and a crack comparator read by eye is good to about 0.05 mm at
best. The crack is drawn analytically in image space from a millimetre value, so its
width is exact at every viewing angle rather than being a rasterised approximation of
one, and the camera is a real pinhole with intrinsics and a pose.

### 6.1 The calibration gate

Separately from the sweep, every build measures printed lines of known width and is
not allowed to publish if it cannot.

`hairline.calibration_gate` renders the printable calibration card at 1200 dpi,
photographs it through the synthetic camera at a slight angle, and runs the full
pipeline over it. Each line is matched by **where it is on the card**, never by how
wide it came out, because matching on width would let a drifted measurement choose the
line that agrees with it. The most recent run:

| Printed | Measured | Error | Across the line | Verdict |
|---:|---:|---:|---:|---|
| 0.148 mm | declined | — | — | correctly declined, below the floor |
| 0.254 mm | declined | — | — | correctly declined, below the floor |
| 0.402 mm | 0.400 mm | −0.47% | 5.8 px | within tolerance |
| 0.699 mm | 0.697 mm | −0.22% | 10.1 px | within tolerance |
| 1.207 mm | 1.204 mm | −0.21% | 17.4 px | within tolerance |

Both halves of that table are the contract: the coarse lines measure, and the fine
ones are refused rather than guessed. The gate fails if either half breaks, and it
fails if fewer than three lines were measurable at all, so a card photographed from
too far away cannot pass by declining everything.

It runs in three places and all three are the same code: `pytest`, the CI workflow at
`.github/workflows/hairline.yml` where the image build depends on it, and the service
at startup, where a failure makes `analyze` refuse every upload with
`CALIBRATION_FAILED` and the interface show it. `/api/calibration` publishes the
result so a judge can check it without reading the tests.

Its own tests make it fail on purpose: an unreadable marker, a card too far away, and
a marker size entered 4% and 10% wrong — which is exactly what a printer's "fit to
page" does, and which nothing further down the pipeline can detect.

### 6.2 The live capture check

`docs/design/hairline-spec.md` puts this above the report screen, and it is right:
every refusal in a report is a walk somebody has already done. `hairline.capture.assess`
takes one viewfinder frame and returns the angle, the recovered scale, and the finest
crack that view could measure, with one instruction. `POST /api/capture` is the
endpoint a phone calls between shots; it costs a few tens of milliseconds because it
stops after the homography.

It is the same detector and the same resolution arithmetic the survey uses. Guidance
computed a different way from the measurement would eventually disagree with it, and
the operator would learn to trust whichever was more optimistic.

## 7. COOL

[`cool-benchmark.md`](cool-benchmark.md). The short version: the stock PyPI aarch64
wheel **already ships Arm's KleidiCV 26.03**, so `pip install opencv-python` on
Graviton is not an unaccelerated baseline, and a comparison against pip-on-x86
measures the processor rather than the library. Every speedup here is computed against
a stock wheel on the same Graviton instance, and the benchmark report prints that
disclosure in its own results table.

## 8. Limitations

**The evaluation is synthetic.** We had no camera and no concrete structure. Synthetic
cracks have parallel sides, uniform darkness, and a clean surface around them. Real
cracks are V-shaped in section with spalled lips, carry dirt and efflorescence, and sit
on concrete with form-tie holes, shutter lines and aggregate pop-out. The numbers in
§6 bound the geometry and the estimator. They do not bound the whole problem, and the
first thing anyone should do with this tool is photograph the printable calibration
target it generates and check it against the printed widths.

**Coplanarity is assumed, not measured.** The scale comes from the marker's plane. If
the crack surface is not in that plane — the card is on a proud patch, or the wall is
curved — the scale is wrong by the offset over the working distance, and nothing in a
single view can detect it. Both numbers are operator inputs with stated defaults, and
they appear in the uncertainty budget as a named term rather than being buried.

**Print scaling is the largest uncontrolled error.** A printer that fits a page to the
margins scales it by a few percent, and that goes straight into every width. The
printed sheet says to measure the square with a rule and enter what you measured, and
the interface asks for it, but nothing downstream can detect the mistake.

**A width is not a diagnosis.** Hairline ships no crack-width limit from any design
code. We could not verify the numbers in ACI 224R, EN 1992-1-1 or BS 8110 against a
primary source we hold, and quoting a limit we have not read would be exactly the kind
of confident wrong number this product exists to avoid. The review bands are an
operator setting, they are labelled as such in the interface, and the default is not
taken from any standard.

**The blur estimate reads low below about one pixel, and that matters on video.**
The 25-to-75 percent rise distance is measured by sampling the image along the
marker's edge normal with bilinear interpolation, and bilinear reconstruction of a
discrete edge is steeper than the continuous edge it came from. Measured on a frame
rendered with 0.9 px of defocus, the estimator returns 0.58 px raw, 0.55 px after JPEG
at quality 92, and 0.51 px after mp4v. The consequence is that the blur correction
under-corrects, so widths near the resolution floor read slightly high rather than
slightly low. On the bundled walk-past, drawn widths of 1.60, 0.80 and 0.50 mm come
back as 1.65, 0.83 and 0.53 mm, which is +3 to +6 percent, against under 1 percent for
uncompressed stills of the same wall.

Two things follow, and both are already in the build. The absolute floor of 4 pixels
exists exactly for this: below a measured sigma of about 0.94 px it is the floor that
binds, not `4.25 x sigma`, so an under-read sigma cannot open the gate. And for the
readings that matter, shoot stills or high-bitrate video rather than a compressed clip,
because the compression does not blur the crack so much as sharpen the edge the
correction is calibrated on.

**Depth is invisible.** A 0.4 mm surface crack and a 0.4 mm crack through the section
photograph identically. Width is one input to an assessment, not the assessment.

**The demo instance is 2 vCPU.** A 4K frame takes a second or two of CPU. A real survey
of a structure would be a batch job, not a web request.

## 8.1 What we would do next, in order

1. **Print the calibration target and photograph it.** Everything here is bounded by
   rendered targets. That check costs ten minutes and a printer and would either
   confirm the numbers or be the most interesting result in the project.
2. **Run the COOL arm.** One console click and one command; the harness and the
   launcher are committed and the other three arms are measured.
3. **Measure a real crack next to a crack comparator card**, which is the only way to
   compare against current practice, and the ASCE page gives no figure for how
   accurate that practice is.
4. **A stereo or two-view check on coplanarity**, which is the largest assumption in
   the uncertainty budget and the only one a single view cannot test.

## 9. Responsible use

**It measures, it does not decide.** Every screen and every export says the review
bands are operator-set. An engineer reads the schedule.

**It refuses, loudly.** No marker, marker too small, surface too oblique, out of focus,
exposure clipped, crack finer than the photograph can resolve, too faint to be a crack
at all, interval too wide — each is a named refusal with a reason and a next action. Refused rows stay in the exported
CSV with their reason, because a schedule that silently drops what it could not measure
reads as a clean wall.

**Nobody is in frame.** Hairline photographs concrete. It contains no face detection,
no person detection, and no model weights of any kind. It is a classical pipeline: the
only thing it can find in an image is a dark thin line and a printed square.

**No data leaves the instance.** Uploads are written to `/tmp`, analysed, and evicted
on a TTL. There is no database, no object store in the request path, and no third
party. The container runs as a non-root user with no inbound port but the one Caddy
proxies.

**Licences.** `opencv-python-headless` is Apache-2.0. NumPy, FastAPI, uvicorn and
pydantic are BSD or MIT. The two bundled fonts are SIL OFL 1.1. Nothing here is AGPL,
there are no model weights, and no dataset is redistributed — the evaluation scenes are
generated by code in this repository.

**Reproducibility.** Pinned dependencies in `constraints.txt`, a two-stage Dockerfile
that asserts the OpenCV major version at build time, one command to re-run the whole
evaluation, and a `/version` endpoint that prints the OpenCV build, the HAL, the
architecture and the git sha of the running image.
