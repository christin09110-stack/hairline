# Hairline

**A phone walk-past of a concrete surface becomes a measured crack survey: every
crack located, its width in millimetres, an uncertainty that means something, and a
stated refusal where the photograph cannot support a number.**

An entry in the OpenCV AI Competition 2026, and this workshop's bid for the **Best Use
of COOL** award.

## Start here

| | |
|---|---|
| **What it is, how to run it, how to test it** | [`products/hairline/README.md`](products/hairline/README.md) |
| **Technical report** | [`products/hairline/docs/report.md`](products/hairline/docs/report.md) |
| **Evaluation, with the numbers and the plots** | [`products/hairline/docs/evaluation.md`](products/hairline/docs/evaluation.md) |
| **COOL benchmark, including the arm that did not run** | [`products/hairline/docs/cool-benchmark.md`](products/hairline/docs/cool-benchmark.md) |
| **Architecture diagrams** | [`products/hairline/docs/architecture.md`](products/hairline/docs/architecture.md) |
| **AWS resources and what they cost** | [`products/hairline/docs/costs.md`](products/hairline/docs/costs.md) |

## Why the layout looks like a monorepo

Because it is a slice of one. Hairline is one of five entries built on a shared
foundation, and the two shared packages are vendored here at the same paths they have
upstream so that every command in the documentation runs verbatim:

```
packages/visioncore     OpenCV 5 primitives: the version assertion, marker calibration
                        with its refusal path, video iteration, run records, timings
packages/servicekit     the FastAPI app factory, job store and progress stream
products/hairline       this product
bench/                  the four-arm COOL benchmark harness
infra/                  the deployment scripts
constraints.txt         the pinned transitive dependency set
```

Nothing has been rewritten to fit this repository. The paths in the report, the CI
workflow and the deploy scripts are the paths that are here.

## The one-minute version

```bash
uv venv products/hairline/.venv --python 3.13
uv pip install --python products/hairline/.venv/bin/python \
  -e packages/visioncore -e packages/servicekit -e products/hairline

products/hairline/.venv/bin/python -m pytest products/hairline/tests -q
products/hairline/.venv/bin/python -m hairline.cli serve --port 8000
```

Open <http://localhost:8000>. Three samples are bundled: one that produces a crack
schedule and two that produce refusals.

`opencv-python-headless` is pinned to **5.0.0.93** everywhere and `import visioncore`
raises on a 4.x wheel, because unpinned pip resolves to 4.14.x — which shipped *after*
5.0.0 and would fail the competition's core requirement without anybody noticing.

## The calibration gate

[`ci/hairline.yml`](ci/hairline.yml) measures printed lines of known width on every
push, and the image build job depends on it. It sits in `ci/` rather than
`.github/workflows/` for one reason, recorded in [`ci/README.md`](ci/README.md): the
token that published this repository has `repo` scope but not `workflow`, and GitHub
refuses such a push. One `git mv` enables it. If a line whose width is written on the card comes
back with the wrong width on it, the build fails and the service refuses to publish
any width at all.

That is not ceremony. `research/` records what this measurement looks like when it
goes wrong quietly: an earlier implementation filled an open crack contour and took
the maximum of a distance transform, and reported **283 mm for a 0.75 mm crack**.
Nothing crashed. The number looked like a number.

## Licence

Code in this repository is released under the MIT licence (see `LICENSE`). Third-party
models, datasets and sample media keep their own licences, listed in the product README.
