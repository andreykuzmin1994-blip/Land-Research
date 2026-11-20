# 42 — Screener site planner: grading with the land (Tier 1)

**Decision: committed on `claude/festive-archimedes-eyjbo6`, on top of the
open PR #51 (`reviews/40` fix and `reviews/41`); not merged.** After a
merge a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline` on the
merge commit. The score should not change: the evaluator imports none of
the planner's modules.

Asked by the operator on 2026-11-18/19: the second of the five priorities
of "plan like a site planner, an architect, an engineer" (`reviews/41`):
grade with the land, docks on the downhill side where the street and
homes allow, a court pad below the floor instead of one flat plane.
"Proceed."

## Tier

Tier 1 (one independent reviewer), as `reviews/38` to `41`: a screener-side
change to the planner's default behaviour. No evaluator, metric,
credential or new external service; no new request.
`--prototype-behaviour` records are byte-identical (below).

## What was measured first

The planner grades one plane over the whole module. A dock building is
graded as a stepped pad: the floor at one level, the truck court 4 ft
below it. Measured on the plans drawn, the same footprint with its
court on the other side:

| Site | Court as drawn | Stepped earthwork | Court on the other side | Why not |
|---|---|---|---|---|
| #21 | high side (ground rises 6 ft to it) | 33,500 CY | 23,900 CY | docks to the street and homes |
| #25 | low side (falls 2.7 ft) | 31,600 CY | 27,500 CY | docks to the street |
| slope 800 x 900 | high (7 ft) | 53,900 CY | 42,300 CY | no car route (below) |
| low front 700 x 800 | high (5 ft) | 31,800 CY | 18,500 CY | no car route |
| 1200 x 1200 | high (4 ft) | 60,900 CY | 50,800 CY | docks to the street |

Where the street is on the low side, a court on the low side is on the
street side of the building. The planner does not take that turn (after
the review, by rule: `02_review.md`, finding 1). On the two tighter sites
the turned layout also fails its routes, which is this planner's limit
(the module is fitted to the envelope, no drive round the building's end
is modelled, only the same footprint is tried), not a finding about the
sites. The docks stay at the back, cut in.

## What changed (default behaviour only)

- **The model** (`stepped_grading`). Per layout, from the lidar samples
  on its module: floor and court elevations (the floor where cut equals
  fill), cut, fill, earth moved, the ground's fall from the building to
  the court. In the record: `grading.stepped_pad`; `stepped_cy` on each
  layout compared. The one-plane figures are unchanged, and still what
  the ranking and the drainage check read.
- **The note.** `DOCKS_UPHILL` (information, with a playbook entry) when
  the ground rises 3 ft or more to a building's one court, with the
  earthwork it costs against the court on the other side and, for the
  plan drawn, why that side was not taken.
- **Docks downhill** (after the pass). The best plan's footprint with
  the court on the other side. Laid out only when it would move at
  least 10% and 2,000 CY less and would not newly turn the docks to the
  street, put the loading between the building and the street, or turn
  it to homes; taken only when it then passes in full, drains, has no
  fewer stalls, no new severity-2 flag, no much longer car route, and
  costs no second building. Its own two tries. `inputs.dock_side`
  records the outcome and each refusal with what it would have saved.
- **The sheet.** Rows "Stepped pad" and "Earthwork, stepped" in the
  takeoff; an "EARTH CY" column in the layouts compared; the stepped
  pad's basis in the footnotes.
- Docs: `screener/README.md`, `screener/SITE_PLANNING.md`.

Not in this change, and why:

- **Earthwork in the ranking.** The ranking's marks (steep ground,
  relief over 25 ft) are the operator's. What a cubic yard is worth in
  floor area is a cost question this tool has no prices for. The
  earthwork is now on the sheet beside each layout's floor area, to
  decide it with.
- **The drainage check against the court.** It still reads the one-plane
  pad; the court sits 2 to 3 ft below that. Moving the reference
  tightens `reviews/41`'s check by as much, which cost floor area in its
  review. An operator decision.
- Paving slopes, walls and cut slopes at the pad's edge, rock, swell and
  shrink; the building's long side along the contours as a goal of the
  search.

## What was verified (author, after the review's fixes; the review is `02_review.md`)

| Check | Result |
|---|---|
| Prototype parity | 46 prototype records (23 sites, real angles and the tests' angle patch) byte-identical to the state of `reviews/41`. |
| The 23 sites, default | Against the state of `reviews/41`, and again after the review's fixes against the code reviewed: no change of floor area, trailer stalls, feasibility or flags on any of the 46 records. One plan turned: a wetland site (700 x 700 ft, real angles), court from the high to the low side, 11,000 CY less. `DOCKS_UPHILL` on 11 records (#21, the 1200 x 1200 site, slope 800 x 900, low front and the steep site in both angle modes, one wetland run). Turns refused before being laid out: toward the street on #21, #25 and the 1200 x 1200 site; between the building and the street on slope 800 x 900 and low front; outside the buildable area on one wetland run. |
| Sheets | #21, #25 and the turned wetland site redrawn and read; all pass the sanitiser. |
| Tests | Class `Grading` (12 tests; 4 before the review). Full suite: in `02_review.md`. |
