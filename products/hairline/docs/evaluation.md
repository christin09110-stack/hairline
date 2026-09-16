# Evaluation

**Every number on this page is reproducible with one command**, and the population
behind each one is named. Re-run with:

```bash
cd products/hairline
.venv/bin/python eval/sweep.py all --out eval/out   # about 12 minutes
```

Raw rows are in [`../eval/out/accuracy.json`](../eval/out) and `accuracy.csv`, one row
per drawn crack per scene per estimator, 1,312 of them. The plots below are written by
`eval/plots.py` from those rows.

---

## 1. Method

### What the targets are, and why they are synthetic

A measuring instrument needs targets known to better than its own precision. A crack
comparator card read by eye is good to perhaps 0.05 mm, which is the same order as the
error being measured, so it cannot certify anything here. We also had no camera and no
concrete structure. So the targets are rendered, and the rendering is built to avoid
flattering the pipeline:

1. **The crack is drawn analytically in image space, not rastered and resampled.** For
   every image pixel the plane homography is inverted, the distance in millimetres
   from that pixel's plane position to the crack centre line is computed, and coverage
   comes from the exact geometry. A 0.30 mm crack is 0.30 mm wide to float64 precision
   at every point and every viewing angle, and the edge antialiasing is true area
   coverage rather than a resampling artefact.
2. **The camera is a real pinhole**, with intrinsics and a pose. Tilting the plane
   produces genuine perspective, so the scale varies across the frame — the effect the
   pipeline has to undo. An affine squeeze would let a pipeline that ignores
   perspective score well.
3. **Defocus, sensor noise, an illumination gradient, vignetting and JPEG
   quantisation** are applied after compositing, in the order a camera applies them.
4. **The surface carries distractors**: multi-octave concrete mottling, aggregate
   pop-out and shutter lines. A segmenter that keeps every dark thing scores badly.

The renderer is `src/hairline/synth.py` and the ground truth it returns is the float
the crack was drawn with, plus the true local scale from the homography's Jacobian.

### The sweep

82 rendered scenes, four cracks in each, all four estimators measured from the same
segmentation so the comparison is paired. One axis is varied at a time from a
reference configuration — that is what the plots show — and then a randomised block
crosses all axes at once, which is where the headline spread comes from.

| Axis | Settings |
|---|---|
| Stand-off | 150, 200, 250, 350, 500, 700, 1000 mm |
| View angle | 0, 10, 20, 30, 40, 50 degrees yaw with a quarter of that in pitch |
| Defocus | sigma 0.5, 0.9, 1.5, 2.5, 4.0 px |
| Lighting | exposure 0.55 to 1.50, gradient 0.10 to 0.45 |
| Sensor noise | 1.0, 2.2, 4.0, 7.0 DN |
| Combined | 28 scenes, all axes randomised together |
| Widths | 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.70, 0.80, 1.00, 1.20, 1.50, 2.00, 3.00 mm |

Frames are 3840×2160. No scene is dropped after the fact, nothing is tuned per scene,
and the error reported is `measured − drawn` with no fitting anywhere.

### The population, stated once

Accuracy figures below are for the default estimator, `blur_corrected`, over **one row
per drawn crack per scene**, with one exclusion:

> **`NOT_VISIBLE` rows are excluded (62 of 328).** A crack whose true width is under
> about 1.6 px at that geometry leaves no mark, so any component overlapping its path
> is a different feature and grading a reading against it measures the labelling, not
> the pipeline. In an earlier run this exact case recorded a 1,436% "error" that the
> pipeline had never made: a component that was really a 1.5 mm crack, attributed to a
> 0.10 mm crack whose path passed near it under a 21 degree tilt. Those rows are
> counted and reported, never scored.

That leaves a population of **266**.

---

## 2. Headline result

| | |
|---|---|
| Population | 266 crack readings attempted |
| **Measured** | **103** |
| **Declined** | **163 (61%)** |
| Median signed error | **−0.61%** |
| Median absolute error | **0.61%**, which is **6 micrometres** |
| Absolute error, 95th percentile | **2.1%**, which is **0.036 mm** |
| Worst single error | **5.6%**, which is **0.056 mm** |
| Central 68% of errors | −1.02% to −0.19% |
| **Stated 95% interval contained the truth** | **98.1%** against a nominal 95% |

That last row is the one we would ask a judge to look at. An uncertainty that does not
cover the error is decoration. 98.1% against a nominal 95%, over 103 readings, means
the interval is honest — if anything slightly conservative, which is the direction an
instrument should err in.

**61% of a deliberately hostile sweep was declined**, by name:

| Refusal | Rows | What it means |
|---|---:|---|
| `BELOW_RESOLUTION` | 108 | the crack is finer than this photograph can resolve; an upper bound is reported instead |
| `NO_FIDUCIAL` | 32 | the marker was not usable, so there is no scale |
| `NOT_DETECTED` | 23 | segmentation found nothing at that crack's location |

Those 163 are not failures. The sweep deliberately includes a 0.15 mm crack
photographed from a metre away, which no method can measure, and the correct answer
there is a refusal with a bound.

![Measured against true width](../eval/out/truth-vs-measured.svg)

---

## 3. What actually limits a measurement

Not stand-off, not angle, not lighting. **How many lens blurs the crack spans.**

![Width error against resolution](../eval/out/error-vs-resolution.svg)

The blurred-box model says why. A crack of width `w` blurred by sigma images as

    p(x) = D0/2 · [ erf((x + w/2)/(√2 σ)) − erf((x − w/2)/(√2 σ)) ]

and the full width at half the observed depth does not fall to zero as `w` does — it
flattens out at `2.355 σ`, the width of the blur. Below that, the profile carries
information about the lens and none about the crack.

### Choosing the gate, from the data

`min_width_sigma_ratio` was set by sweeping the threshold itself and reading off where
the tail of large errors ends:

| Gate | Readings kept | Median | Worst error | Interval coverage |
|---|---:|---:|---:|---:|
| none | 187 | −0.32% | **242%** | 91.4% |
| `w/σ ≥ 2.5` | 149 | −0.62% | 242% | 92.6% |
| `w/σ ≥ 3.0` | 113 | −0.62% | 149% | 96.5% |
| `w/σ ≥ 3.5` | 102 | −0.61% | 7.5% | 98.0% |
| **`w/σ ≥ 4.25`, shipped** | **103** | **−0.61%** | **5.6%** | **98.1%** |

The first four rows are the tuning run that chose the value, on the sweep as it stood
then; the last is the shipped configuration on the final sweep. The shape is what
matters: the tail collapses between 3.5 and 4.25 and stays collapsed.

**One decision underneath that table was a bug for a while, and it is worth the
paragraph.** The floor was first applied per width sample rather than per crack. For a
crack sitting just under the floor that discards the samples which measured narrow and
keeps the ones which measured wide, so the crack comes back *measured*, from a biased
subset: a 0.35 mm crack read 0.382 mm, nine percent high, where it should have been
refused. Resolvability is a property of the crack and the frame, not of one noisy
sample. It is now decided once, on the median of every sample, and then every sample is
kept or none are. Fixing it moved the sweep's worst error from 13.2% to 5.6% and its
interval coverage from 95.4% to 98.1%, which is most of what the two numbers are worth. An absolute floor of 4 px sits underneath the ratio, because on a
compressed video the marker's edges read sharper than the lens really is — the bundled
walk-past measures sigma at 0.57 px where its frames were rendered with 0.9 px of
defocus, since the codec rings the very edges the estimate is calibrated on.

### What the gate is worth, shown by turning it off

The gates disabled, the reference close-up scene, the same frames:

| Stand-off | True width | Half-depth reading | Blur-corrected reading |
|---|---:|---:|---:|
| 350 mm | 0.20 mm | **0.311 mm (+56%)** | 0.177 mm (−12%) |
| 350 mm | 0.30 mm | 0.356 mm (+19%) | 0.246 mm (−18%) |
| 250 mm | 0.15 mm | **0.223 mm (+49%)** | 0.132 mm (−12%) |
| 250 mm | 0.30 mm | 0.311 mm (+4%) | 0.283 mm (−6%) |
| 200 mm | 0.30 mm | 0.303 mm (+1%) | 0.293 mm (−2%) |

Read the middle column first: the textbook method returns **0.31 mm for a crack
0.20 mm wide**, and it does not look uncertain while doing it.

Then read the right-hand column, because it is the more important one. Below the gate
the corrected estimator is wrong too, by 12 to 23 percent, in the other direction.
**No estimator recovers a width the photograph does not contain.** That is why the
answer is a gate and a refusal rather than a cleverer formula, and it is the single
design decision this whole product rests on.

---

## 4. The four estimators

Same population, same segmentation, paired.

| Estimator | Measured | Median | Central 68% | Worst | abs p95 | Coverage |
|---|---:|---:|---:|---:|---:|---:|
| **`blur_corrected`** (default) | 103 | −0.61% | −1.02 to −0.19% | 5.6% | 0.036 mm | 98.1% |
| `halfdepth` | 103 | −0.31% | −0.88 to +0.43% | 4.5% | 0.032 mm | 98.1% |
| `area_ratio` | 98 | −3.60% | −4.95 to −3.17% | 10.1% | 0.109 mm | 81.6% |
| `distance_transform` | 110 | −4.53% | −8.57 to +3.52% | 79.1% | 0.235 mm | 36.4% |

Three things to take from this table, and the first is not the one we expected.

**`halfdepth` is marginally the better of the top two on accepted readings, and that
does not make it the right default.** Median −0.31% against −0.61%, worst 4.5% against
5.6%. The reason is circular in a way worth stating: the gate that decided which
readings are in this table is itself derived from the blur model, and it has already
removed every case where the raw half-depth reading fails. Inside the fence the two
agree to a few tens of micrometres; outside it, §3 shows half-depth returning 0.311 mm
for a 0.20 mm crack. What the correction buys is not accuracy on good frames. It is
the fence. It is also what carries the blur-calibration term in the uncertainty
budget, which is why the two estimators reach the same 98.1% coverage by different
routes.

**`area_ratio` is biased 3.6% low with 81.6% coverage.** The blur-invariant integral
`A/D = w/erf(w/2√2σ)` is elegant and, in practice, sensor noise inflates the observed
depth `D` and the width follows it down. It is in the repository because knowing which
good idea does not survive contact with a sensor is worth as much as knowing which one
does.

**`distance_transform` has 36.4% coverage and a 79% worst case.** The width a binary
mask implies moves with wherever the threshold happened to fall, and its spread does
not know that. It is carried as a cross-check in every run record and it is never the
answer.

---

## 5. Per-axis behaviour

![Error against stand-off](../eval/out/error-vs-standoff.svg)

| Axis | Measured / attempted | Median error | Worst |
|---|---:|---:|---:|
| Reference | 8 / 14 | −0.39% | 0.9% |
| Stand-off | 18 / 44 | −0.56% | 2.0% |
| View angle | 22 / 36 | −0.43% | 1.1% |
| Defocus | 12 / 35 | −0.27% | 2.8% |
| Lighting | 20 / 35 | −0.87% | 5.6% |
| Sensor noise | 8 / 12 | −0.95% | 2.1% |
| All axes combined | 15 / 90 | −0.88% | 2.8% |

![Error against viewing angle](../eval/out/error-vs-angle.svg)
![Error against defocus](../eval/out/error-vs-blur.svg)

**Viewing angle barely moves the error** and that is the Jacobian doing its job: the
error at 50 degrees of apparent foreshortening is indistinguishable from the error
square on, because the measurement is taken along the true perpendicular on the wall
and converted with the local scale in that direction.

**No axis holds a worst case above 5.6%**, and the one that does is lighting, not
defocus. Once the gate refuses everything the blur cannot carry, blur stops being the
variable that drives the residual error and exposure takes over: the 5.6% row is the
brightest, most strongly graded scene in the sweep, where the adaptive threshold's
measured constant is working hardest.

**Stand-off decides what is measurable, not how accurate it is.** At 150 mm the tool
measures a 0.20 mm crack to +0.5%; at 1000 mm it declines everything under about
1.2 mm. The refusal message carries the arithmetic, which is why the next action is
always "stand closer" and never "try again".

---

## 6. Scale recovery, measured separately

The marker homography, checked against the renderer's own ground-truth scale on every
frame in the sweep:

| | |
|---|---|
| Median absolute scale error | **0.082%** |
| Worst scale error | **0.284%** |

across apparent foreshortening from 0 to 62 degrees. Scale is not the limiting factor
in this instrument, which is why the uncertainty budget's scale term is dominated by
its 0.4% floor rather than by the measured fit residual.

A note on the geometry reading. What Hairline reports as "apparent tilt" is
`acos` of the ratio of the Jacobian's singular values, and off the optical axis that
runs above the true plane tilt because the projection adds shear: 25 degrees apparent
for a true 10. It is a conservative gate, not a measured plane angle, and the report
says so wherever it appears. The quantity that actually matters is the sampling
density along each crack's own normal, which is per crack, not per frame.

---

## 7. The refusal sweep

Separately from accuracy, scenes built to defeat the tool, checking that it declines
with the right reason instead of returning a number. `eval/sweep.py refusals` and
`tests/test_refusals.py` both cover this; the tests are the authority because they
assert the code, not just the absence of a number.

**All eight scenes refuse, and each refuses with a code that names what was wrong.**

| Scene | Observed | Result |
|---|---|---|
| No marker in frame | `NO_MARKER` | refused; the message says a printed reference in the same plane is required |
| Marker 3.2 m away | `BELOW_RESOLUTION` | refused |
| Surface almost edge on | `TOO_OBLIQUE` | refused |
| Camera moving, heavy smear | `NO_MARKER` | refused |
| Frame badly under exposed | `UNDER_EXPOSED` | refused |
| Frame blown out | `OVER_EXPOSED` | refused |
| Crack finer than resolvable | `BELOW_RESOLUTION` | refused, with an upper bound |
| Surface texture, no measurable crack | `BELOW_RESOLUTION` | refused |

Two of those rows are worth a note, because the first version of this table had the
wrong expectation and the sweep was right.

**"Marker 3.2 m away" was expected to give `MARKER_TOO_SMALL` and gives
`BELOW_RESOLUTION`.** At that distance the 60 mm marker still spans 56 pixels, above
the 48-pixel floor, so the scale is recovered perfectly well and it is the *cracks*
that cannot be measured. The refusal arrives from the crack side rather than the
marker side, and that is the more accurate answer. The expectation was changed to
match the pipeline, not the other way round.

**"Camera moving, heavy smear" gives `NO_MARKER` rather than `OUT_OF_FOCUS`.** At 14
pixels of blur the ArUco detector stops finding the marker before the focus gate is
reached. Both refusals are correct and the order they fire in is an implementation
detail; the expectation names all the codes that would be right.

Two properties are asserted rather than eyeballed:

- **A refused crack reports no width at all** — `p50`, `p95` and `max` are all `None`,
  so there is no number anywhere for a caller to pick up by accident.
- **The upper bound is actually an upper bound.** For every `BELOW_RESOLUTION` row in
  the sweep, the drawn width is below the bound the tool reported.

**`TOO_FAINT` was added because of this sweep, not before it.** At a 900 mm stand-off
on a wall whose cracks are all sub-pixel, two patches of surface texture survived every
filter and were reported as a 2.0 mm and a 1.5 mm crack. They were wide enough to
measure and only 20 and 22 grey levels darker than their surround, where the real
cracks in the same sweep run 70 to 190. A crack is a shadowed void and its darkness
does not fall off with distance; only the *observed* depth does, and only while the
crack is unresolved, which the resolution gate has already excluded by that point. So a
component that is wide enough to measure and barely darker than the wall is a stain, a
shadow or texture, and it is now refused by name.

A relative threshold could not have done it: at 900 mm the surface's own residual
spread collapses to 1.0 grey level because the downsampling averages the texture away,
so those artefacts read as 20 sigma — the same as a real crack.

A frame can also be fine on its own and still be rejected: `MOTION_BLUR` is relative
to the sharpest frame in the same clip, so the walk-past discards frames that would
pass in isolation because better views of the same wall exist.

---

## 8. What this evaluation does not establish

**It is synthetic, end to end.** Synthetic cracks have parallel sides, uniform
darkness and a clean surround. Real cracks are V-shaped in section with spalled lips,
carry dirt and efflorescence, and sit on concrete with form-tie holes and shutter
lines. These numbers bound the geometry and the estimator. They do not bound the
problem.

**Nothing here tests a printer.** `hairline sheets` writes a calibration target with
lines from 0.10 to 2.00 mm and prints the width each line has in the file, to the dot.
Photographing that sheet and running it through the tool is the first real check
anybody should do, and we could not do it.

**Nothing here tests a non-coplanar surface.** Every scene has the marker in the same
plane as the crack. If the card sits on a proud patch, the scale is wrong by the
offset over the working distance and nothing in a single view can detect it. It
appears in the uncertainty budget as an assumption with a stated default, not as a
measurement.

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

**No outcome claim.** There is no published trial of a deployed camera inspection
system improving a real outcome, in this field or any next to it, and nothing here
suggests otherwise.
