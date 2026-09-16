# Hairline

**A phone walk-past of a concrete surface becomes a measured crack survey: every
crack located, its width in millimetres, and an uncertainty that means something.
Where the photograph cannot support a number, Hairline says so and says why.**

An entry in the OpenCV AI Competition 2026. It is also this workshop's bid for the
**Best Use of COOL** award, because its per-frame work — adaptive Gaussian
thresholding and contour extraction on a 4K frame — is exactly what the Cloud
Optimized OpenCV Library is built to accelerate. See [`docs/cool-benchmark.md`](docs/cool-benchmark.md).

- **Live endpoint:** see [`docs/costs.md`](docs/costs.md) for the current URL.
- **Technical report:** [`docs/report.md`](docs/report.md)
- **Evaluation, with the numbers:** [`docs/evaluation.md`](docs/evaluation.md)
- **Architecture diagrams:** [`docs/architecture.md`](docs/architecture.md)

---

## What it does

Tape a printed marker of known size flat on the wall, next to the crack, and walk
past with a phone. Hairline:

1. reads the clip, scores every frame for focus, and finds the marker;
2. recovers the plane the marker lies in, and with it the scale in pixels per
   millimetre, which varies across the frame whenever the camera is not square on;
3. thresholds each usable frame adaptively, keeps the components that are long, thin
   and genuinely darker than the surface around them, and cuts crack networks at
   their junctions so each run is measured separately;
4. samples the intensity **perpendicular to each crack on the wall** — not
   perpendicular to it on screen, which under a tilt is a different direction — and
   reads a width from each profile;
5. corrects that width for the lens blur, which it measures from the marker's own
   printed edges in the same frame;
6. reports a distribution per crack with an uncertainty budget, and a schedule you
   can export as CSV.

And then the part that matters most:

7. **it refuses.** No marker, marker too small, surface too oblique, frame out of
   focus, exposure clipped, crack finer than the photograph can resolve, or an
   interval too wide to be useful — each is a named refusal with the reason and the
   next action, not a number with a shrug.

## Why refusal is the feature

`research/FINDINGS.md` §5.0 records what happened on the first attempt at this
problem: a crack-width routine filled an open contour and took the maximum of the
distance transform. It reported **283 mm for a 0.75 mm crack**, a factor of 380.
Nothing crashed. Nothing warned. The number looked like a number.

The measured version of the same failure is in [`docs/evaluation.md`](docs/evaluation.md).
With the resolution gate turned off, reading the full width at half depth — the
textbook approach — returns **0.31 mm for a 0.20 mm crack**, because below about four
lens blurs across it is measuring the lens and not the crack. And the corrected
estimator is wrong there too, by 12% in the other direction: no method recovers a width
the photograph does not contain. That is why the answer is a refusal with an upper
bound rather than a cleverer formula.

## Accuracy, in one line

Against synthetic targets of known width, across stand-off, viewing angle, defocus,
lighting and sensor noise, with the default estimator and the default gates:

| | | |
|---|---|---|
| Readings attempted | 266 | |
| **Measured** | **108** | |
| **Declined, by name, with a reason** | **158** | 59% of a deliberately hostile sweep |
| Median error | **−0.37%** | 6 micrometres |
| Absolute error, 95th percentile | **4.3%** | 0.051 mm |
| Worst single error | **13.2%** | 0.13 mm |
| **Stated 95% interval contained the truth** | **95.4%** | against a nominal 95% |

The last row is the one worth looking at: an uncertainty that does not cover the error
is decoration. The declined readings are counted rather than dropped, and the method,
the full 1,312-row sweep and the plots are in
[`docs/evaluation.md`](docs/evaluation.md).

## Pinned dependencies

`opencv-python-headless==5.0.0.93` and nothing that resolves around it.
`opencv-python` without a pin resolves to 4.14.x, which shipped *after* 5.0.0 and
would fail the competition's core requirement, so `import visioncore` raises at
import time on a 4.x wheel and `/version` prints what is actually running.

| Package | Version |
|---|---|
| opencv-python-headless | 5.0.0.93 |
| numpy | 2.5.3 |
| fastapi | 0.141.1 |
| uvicorn | 0.53.0 |
| pydantic | 2.13.5 |
| python-multipart | 0.0.32 |

The full transitive set is in `constraints.txt` at the repository root, and the
Docker build installs against it. No AGPL dependency, no model weights, no network
fetch at run time. The two bundled fonts are Barlow Condensed under the SIL Open
Font License 1.1; see `src/hairline/assets/FONTS.md`.

## Run it

```bash
# from the repository root
uv venv products/hairline/.venv --python 3.13
uv pip install --python products/hairline/.venv/bin/python \
  -e packages/visioncore -e packages/servicekit -e products/hairline

# measure a clip from the command line
products/hairline/.venv/bin/python -m hairline.cli measure \
  products/hairline/src/hairline/samples/walk-past.mp4 --marker-mm 60 --out /tmp/hairline

# or run the web service
products/hairline/.venv/bin/python -m hairline.cli serve --port 8000
```

Then open <http://localhost:8000>. Three samples are bundled and appear in the left
rail: a walk-past that produces a schedule, and two that produce refusals.

Print the scale marker and the check target from the running service:

```
GET /api/target/marker.png?dpi=600
GET /api/target/calibration.png?dpi=600
```

or with the CLI:

```bash
products/hairline/.venv/bin/python -m hairline.cli sheets --out docs/sheets --dpi 600
```

**Print at 100 percent, then measure the printed square with a rule and enter what
you measured.** A printer that quietly scales the page by four percent puts four
percent into every width in the report and nothing downstream can detect it.

## Test

```bash
products/hairline/.venv/bin/python -m pytest products/hairline/tests -q
```

The suite asserts measured millimetres against drawn millimetres with real
tolerances, checks that the stated uncertainty actually covers the error, checks
each refusal path fires on a scene built to trigger it, and includes a source-level
guard that no filled contour is ever used to measure a thin feature.

Re-run the evaluation sweep (about 12 minutes, it renders every scene):

```bash
cd products/hairline
.venv/bin/python eval/sweep.py all --out eval/out
```

## Deploy

```bash
# arm64 image to ECR, then a Graviton EC2 instance running it
infra/ecr.sh hairline --context . --dockerfile products/hairline/Dockerfile --arch linux/arm64
products/hairline/infra/deploy.sh
```

This product deploys on **Graviton EC2**, not App Runner, because the Cloud
Optimized OpenCV Library is an AMI and App Runner has no Arm option. Details, costs
and every resource created are in [`docs/costs.md`](docs/costs.md).

## Responsible use, in short

Hairline measures a crack. It does not decide whether a structure is safe, and it
ships **no crack-width limit from any design code** — the review bands in the
interface are an operator setting with a default that is explicitly not taken from
ACI 224R, EN 1992-1-1 or anything else, because we could not verify those numbers
against a primary source we hold. The longer version is in
[`docs/report.md`](docs/report.md).
