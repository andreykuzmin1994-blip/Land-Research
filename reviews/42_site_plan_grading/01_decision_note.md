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

Where the street is on the low side, a court on the low side lies
between the street and the car field, and no car route reaches the cars
without crossing truck pavement: the planner's access checks reject it,
and an engineer would too. The docks stay at the back, cut in.

## What changed (default behaviour only)

- **The model** (`stepped_grading`). Per layout, from the lidar samples
  on its module: floor and court elevations (the floor where cut equals
  fill), cut, fill, earth moved, the ground's fall from the building to
  the court. In the record: `grading.stepped_pad`; `earthwork_cy` on
  each layout compared. The one-plane figures are unchanged, and still
  what the ranking and the drainage check read.
- **The note.** `DOCKS_UPHILL` (information, with a playbook entry) when
  the ground rises 3 ft or more to the court, with the earthwork it
  costs against the court on the other side.
- **Docks downhill** (after the pass). The best plan's footprint with
  the court on the other side, checked in full; taken only when it
  passes, drains, has no fewer stalls and no new severity-2 flag, faces
  neither street nor homes anew, and moves at least 10% and 2,000 CY
  less. `inputs.dock_side` records it.
- **The sheet.** Rows "Stepped pad" and "Earthwork, stepped" in the
  takeoff; an "EARTH CY" column in the layouts compared.
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

## What was verified (author; the review follows in `02_review.md`)

| Check | Result |
|---|---|
| Prototype parity | 43 prototype records (23 sites, real angles and the tests' angle patch) byte-identical to the state of `reviews/41`. |
| The 23 sites, default | No change of floor area, trailer stalls or feasibility on any. One plan turned: a wetland site (700 x 700 ft, real angles), court from the high to the low side, 11,000 CY less. `DOCKS_UPHILL` on 11 of 44 planned records (#21, the 1200 x 1200 site, slope 800 x 900, low front and the steep site in both angle modes, one wetland run). |
| Sheets | #21 and #25 redrawn and read; both pass the sanitiser. |
| Tests | New class `Grading` (4 tests): the model on a tilted plane both ways round and on level ground, the record, the note with its cost and the prototype's silence, a plan turned. Full suite: in `02_review.md`. |
