# 44 — Screener site planner: use the land (Tier 1)

**Decision: committed on `claude/festive-archimedes-eyjbo6`, on top of the
open PR #51 (`reviews/40` fix, `reviews/41` to `43`); not merged.** After a
merge a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline` on the
merge commit. The score should not change: the evaluator imports none of
the planner's modules.

Asked by the operator on 2026-11-20: the fourth of the five priorities of
"plan like a site planner, an architect, an engineer" (`reviews/41`): use
the land (plans covered 16 to 20% of their sites against 35 to 45% for
typical distribution sites). "go."

## Tier

Tier 1 (one independent reviewer), as `reviews/38` to `43`: a screener-side
change to the planner's default behaviour. No evaluator, metric,
credential or new external service; no new request.
`--prototype-behaviour` records are byte-identical (below).

## What was measured first

On the 23 review sites (state of `reviews/43`, real angles): floor-area
ratio 0.12 to 0.33; 12 to 60% of the buildable land unused. What stood
between the plan drawn and a larger one, by the layouts each search had
rejected:

| What blocked the larger layout | Sites | Example |
|---|---|---|
| `CAR_ROUTE_NONE`: no car route the search's rules could find | every cross-dock on the large sites, most "cars at office end" layouts | 1200 x 1200: a 458,000 SF cross-dock against 335,000 drawn |
| `POND_DOES_NOT_FIT`: no rectangular pond in the ground left | the mid-size sites | 650 x 750: 151,000 to 157,000 SF against 114,000 drawn |
| No second building: the land behind building 1 had no way in | the two largest sites | 1200 x 1200: 13 ac unused in one piece; 1800 x 1900: 41 ac |

The first is a gap in the search, not in the land: a cross-dock's car
field stands at the building's end with the layout's own open yard in
front of it, and the car route was not allowed across the layout's own
module. The second is a rule of shape: a pond need not be a rectangle.
The third is the first building standing across the whole lot.

## What changed (default behaviour only)

- **The pass** (after the search and the pond pass; only adds). The
  rejected layouts larger than the best plan, largest first, at most 10,
  finished again with: a car drive over the layout's own open ground; a
  pond shaped to the open ground where no rectangle fits (`free_pond`,
  its storage from its outline by the rectangle's own rule); the truck
  road laid out first (`reviews/43`). Only for a failed route or pond;
  never a layout that newly faces the street or homes; at most 3 go on.
- **A second building behind the first.** Candidates with no way in of
  their own are tried again reached through building 1's truck road and
  the end of its court, from its truck driveway; trucks and cars share
  that drive. Conditions on the sheet: `SECOND_ACCESS_THROUGH_FIRST`,
  `SECOND_ACCESS_SHARED`.
- **Flags** (with playbook entries): `POND_SHAPED_TO_SITE`
  (information), `ACCESS_THROUGH_FIRST` (severity 2).
- **The record and the sheet.** `inputs.land_pass`; the pond's shape in
  the detention basis; a shaped pond drawn and labelled like any other.
- Docs: `screener/README.md`, `screener/SITE_PLANNING.md`.

Not in this change: a third building; the pond's volume split over
several small pieces or wrapped round a building; parking clustered at
the office end; building depths off the market list. The cross-docks on
the large sites stay undrawn where they would newly put a court on the
street (the pass's rule); those sites gain by the second building
instead. The fifth priority (structural bays) is not started.

## What was verified (author; the review follows in `02_review.md`)

RESULTS
