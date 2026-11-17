# 39 — Re-check of the review fixes

The same reviewer re-checked the fixes in `619eda1`'s working tree (before
that commit) and repeated its record comparison: 24 cases (2 fixtures and
10 synthetic sites, both behaviours) byte-identical to `df51bec`;
`test_siteplan_assess`, `test_siteplan`, `test_viewer`: 257 tests OK.

Verified correct: fixes 1, 2, 4, 5, 6 (no end hook at 4x on rank25, none
in a non-contiguous assemblage's gap, follows a kinked frontage), 8, 9,
11. 16 planned sheets and 11 hand-made variants (dem None, empty, few,
partial, not checked; invalid, far, covering, empty, GeometryCollection
and unknown-kind context; no frontage; two assemblages; 78 acres) render,
pass `sanitise_svg` and give the same bytes twice.

| # | Finding | Severity | Done |
|---|---|---|---|
| A | "buffered as residential" (fix 3) told the reader a buffer was applied when none was (rank25: tinted parcels across the road, no `RESIDENTIAL_EDGE`) | should fix | "classed residential (county land-use / tax class)". |
| B | Both context swatches shown when only one kind is drawn; a checked DEM with under 4 samples on the site read as "level ground" | nit | Each swatch only when that kind is drawn (tested); the reason reads "level ground, or too few samples on the site". |
| C | The finiteness filter dropped number types that are not `int`/`float` (numpy float32) | nit | Any value `float()` takes, if finite (tested). |
| D | "about half a grid step" short of the line is true on two sides only | nit | "up to one grid step". |
| E | The centre-line test did not check the line sits half a street out | nit | It now checks the distance from the parcel's frontage side, within 1 px. |

After these: the planner records were compared again (18 records,
identical) and the full suite run (see the decision note).
