# 39 — Screener site plan drawing: context, contours and a presentation look (Tier 1)

**Decision: committed on `claude/festive-archimedes-eyjbo6`, not merged.**
After a merge a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline` on the
merge commit, as for every screener change. The score should not change:
the evaluator imports none of these modules.

Asked by the operator on 2026-11-17, after seeing two test-fit sheets: the
plan "looks like a bunch of geometrical figures drawn in MS Paint"; improve
the colouring, the level of detail and the pixelation.

## Tier

Tier 1 (one independent reviewer), as `reviews/38`. Screener-side change to
the drawing only: no evaluator, metric, credential or new external service,
no new request. `siteplan_inputs.py`, the eval files and `rank.py`,
`scoring.py`, `screens.py`, `settings.toml` are untouched
(`git diff df51bec -- screener/rank.py screener/scoring.py screener/screens.py
screener/settings.toml screener/autoresearch screener/siteplan_inputs.py`
is empty). The reviewer ran as a fresh-context subagent that did not write
the code and was given the code and the requirements, not the author's
conclusions.

## What changed

- `screener/siteplan_render.py`, the plan only (the read-out panels are as
  before):
  - a muted presentation palette, a grass stipple on open ground (one more
    in-file pattern, `gx`), the plan box on a paper ground;
  - the neighbouring county parcels in outline, residential ones tinted;
  - contour lines of the existing ground from the lidar samples
    (`contours()`: marching squares on the 40 ft grid, the saddle split by
    the cell mean, pieces chained into lines, Chaikin smoothing, clipped to
    the parcel; an interval of 1 to 20 ft chosen from the relief, every
    fifth an index contour, labelled on open ground only);
  - a cast shadow and a parapet line on the roof, dark dock-door marks, a
    dashed yellow street centre line, a pond with its top of bank and a
    deeper middle;
  - legend and footer lines for what is new; nothing drawn when there is
    nothing to show (no context, flat ground, partial lidar), and the
    legend says why.
- `screener/siteplan.py`: `Drawing` gains two optional drawing-only fields,
  `dem` and `context`; `residential_neighbour()` is the residential test
  `classify_edges()` used, factored out so the tint and the edges cannot
  disagree. No record key or value changes.
- Tests: `test_siteplan_assess.py` classes `Contours` and `PlanContext`
  (12 tests); the pattern guard in both sheet tests admits `#gx` and now
  also requires every `url(#…)` to name a pattern defined in the file.
- `screener/README.md`: "Context on the plan" under "How to read the sheet".

Not done, and why: an aerial photo under the plan would do most for the
look, but the sheet's allow-list has no `image` element, the renderer may
not touch the network, and the screener's dependency rule allows only
`requests` and `shapely`. That is an operator decision (dependency rule,
viewer allow-list, imagery licence), not a drawing change.

## What was verified

| Check | Result |
|---|---|
| Offline suite | `python -m unittest discover -s screener/tests -t .`: 971 tests, OK (959 before). |
| No planner behaviour change | Records compared byte for byte before and after: 2 reference sites and 7 synthetic sites, both behaviours (18 records), by the author; the reviewer repeated it from a worktree of `df51bec` on 24 cases (the 2 fixtures and 10 synthetic sites incl. a 78-acre sloped site, Atlanta and South Fulton farms, no frontage, a seam assemblage, both behaviours): identical. |
| Sheet is safe | Same element allow-list, passes `viewer.sanitise_svg`, every `url()` to an in-file pattern, every text through `plan._esc`, same bytes on a second render and across `PYTHONHASHSEED` values. |
| Contours are where the ground is | Reviewer: on a tilted plane 0.0 ft error; on 4 random smooth terrains the interpolated height at every vertex is exact before smoothing and within 0.035 of an interval after; no crossings; saddle rule checked for a mean above, equal to and below the level. |
| Tint agrees with the edges | Reviewer: in all 18 planned cases every RES edge has a tinted neighbour within 21 ft; the agricultural rule follows `ag_buffer` and the behaviour, now also a test. |
| Speed | The contour pass on a 78-acre site takes about 0.03 s. |

## Review findings and what was done

| # | Finding | Severity | Done |
|---|---|---|---|
| 1 | An invalid context shape raised `GEOSException` out of `render()` (not reachable from `plan_site`) | should fix | `make_valid` per shape; a shape GEOS still cannot cut is left out, never the sheet. Tested. |
| 2 | A partial lidar answer drew contours on half the site, the rest reading as level | should fix | Contours only when the lidar state is `checked`; the legend says "none drawn (lidar incomplete)". Tested. |
| 3 | Legend called the tint "residential land-use code"; it also covers tax class R and, where the rules buffer it, agricultural | should fix | Legend: "classed residential (county land-use / tax class)" (the re-check's wording, A); footer line spells out the rule and that it is not zoning. |
| 4 | The context test did not test the agricultural × behaviour matrix | should fix | `test_the_tint_follows_the_edge_rule_in_each_behaviour`, checked against `Drawing.edges`. |
| 5 | The saddle test passed with the rule inverted | should fix | Now asserts which corners are cut off, for a mean above and below the level. |
| 6 | The centre line kept an 8.5 ft hook at each end, and a second line in the gap of a split assemblage | should fix | Rebuilt as an offset of each frontage run on the side away from the parcel, clipped to the street band. Tested. |
| 7 | Flat ground claimed "every 1 ft" with no lines; neighbour legend shown with no neighbours | nit | `contours()` returns `(0, [])` when no line is drawn; neighbour legend and footer only when drawn. |
| 8 | A NaN sample raised | nit | Non-finite samples skipped (not reachable from `plan_site`). Tested. |
| 9 | Contour label clearance tested at the anchor only | nit | The whole label must be clear of the paving. |
| 10 | Contours stop 20–40 ft inside the property line (the sample grid) | nit | Left; the footer says so. |
| 11 | New tests placed after the `__main__` guard | nit | Moved above it. |

The reviewer re-checked the fixes; see `02_recheck.md`.
