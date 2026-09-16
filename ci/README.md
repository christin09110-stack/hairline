# ci

`hairline.yml` is a GitHub Actions workflow and belongs at
`.github/workflows/hairline.yml`. It is here instead because the token that published
this repository carries `repo` but not `workflow` scope, and GitHub refuses a push
from an OAuth app that creates or updates anything under `.github/workflows/`:

```
! [remote rejected] main -> main (refusing to allow an OAuth App to create or
  update workflow `.github/workflows/hairline.yml` without `workflow` scope)
```

Recorded here rather than worked around quietly, because the file's location is the
difference between a workflow that runs and a workflow that is only described. To
enable it:

```bash
mkdir -p .github/workflows && git mv ci/hairline.yml .github/workflows/
```

## What it does, and why it matters more than a usual CI file

It runs the calibration gate: `products/hairline/tests/test_calibration_gate.py`
photographs a card of printed lines whose widths are written on it and checks that
each one comes back with that width. **The image build job depends on that job**, so a
build that cannot measure a line it drew itself does not produce an image.

`docs/design/hairline-spec.md` §9 asks for exactly this, and
`research/EVIDENCE-bridges.md` and the project's own research notes say why: an earlier
implementation of this measurement reported 283 mm for a 0.75 mm crack, and nothing
raised.

The gate is enforced in three places and all three run the same code:

| Where | Effect of a failure |
|---|---|
| `pytest products/hairline/tests/test_calibration_gate.py` | the suite fails |
| this workflow | the image is not built |
| the service, at startup | every upload is refused with `CALIBRATION_FAILED`, and `/api/calibration` says so |

The third is the one that protects a user, and it does not depend on this file being
in the right directory.
