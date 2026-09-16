# Real-photo dev set for `segmentation="blackhat"`

Detection-only tuning on real photographs. Widths are not scored here: none of these
photos has a scale.

## Set

- `media/real/hairline/commons/`: 10 Wikimedia Commons photos.
- `media/real/hairline/mendeley-ozgenel/`: 6 patches of 227 px (5 positive, 1 negative).

The two scaled TEST photos (`crack-dsc07068`, `crack-monitor-in-dnipro`) were not opened
or run until the defaults below were committed.

## Labels (`labels.json`)

Crack centre lines traced by eye on a 10x10 grid (0.05 on zoomed crops), stored as
fractions of width and height. They are accurate to about 1-2% of the frame. Each image
also has `expected_px`, the crack width estimated by eye, which plays the part of the
operator's `expected_width_mm`. Four images have no measurable dark crack (rubble,
light-on-dark crazing, a whole wall at distance, a dataset negative), so any detection
on them counts as false. The FHWA barrier is map cracking, so it is labelled as one
network region. In total: 18 labelled cracks or networks.

## Metric (`evaluate.py`)

`segment_cracks` runs with `px_per_mm = expected_px / 0.5`, so the expected width is
the nominal 0.5 mm, with `keep_edge_cracks=True`. A kept component is a hit when at
least 60% of its skeleton lies within max(2% of the diagonal, 2 expected widths) of a
labelled line. Anything else, apart from components inside a network, is false.

## What changed (blackhat only; adaptive output is byte-identical)

- Contrast is measured in robust sigmas of the black-hat response, not of the adaptive
  residual: `blackhat_min_contrast_sigma`. The `min_contrast_dn` floor stays.
- Length, width and area filters are in crack widths, not mm, so they do not depend on
  the scale: `blackhat_min_length_widths` and `blackhat_max_width_ratio`, and the area
  floor is half of a minimum-length crack.
- `blackhat_min_elongation` replaces the bounding-box elongation, which rejects any
  diagonal or wavy crack.
- Length is measured on the thinned centre line. The distance-transform ridge breaks up
  on cracks several pixels wide, so it undercounted length and inflated width.

## Sweeps and choice

54 settings over seed sigma, contrast sigma, length in widths, elongation and width
ratio (results in `results.json`). The best score was 17/18 hits, but with 2013 false
components. Frozen setting:

| param | value |
|---|---|
| blackhat_seed_sigma | 5.0 (was 6.0) |
| blackhat_min_contrast_sigma | 4.0 |
| blackhat_min_elongation | 1.0 |
| blackhat_min_length_widths | 20 |
| blackhat_max_width_ratio | 5 |

## Before and after

| | found | false components | false per image |
|---|---|---|---|
| before (1d2e307 filters) | 1 / 18 | 0 | 0.0 |
| after (frozen) | 12 / 18 | 231 | 14.4 |

191 of the 231 false components come from the two 4000x3000 Richfield cladding photos,
where the panel joints are long, dark and straight, so no contrast or shape filter
separates them from a crack. The tool relies on exclusion polygons for those. Found:
all 4 Richfield-9061 hairlines, Boyana, Medway x2, the FHWA network, and 4 of 5
Mendeley patches. Missed: all three Harvey cracks (the exposed-aggregate slab lifts the
response spread), the Nara 1936 print, the Boyana upper stub and Mendeley positive-00001.
