# 43 — Review of the truck design

The Tier 1 reviewer (fresh context, given the code and the requirements,
not the author's conclusions) reviewed `42ab8d1..2af81fa`. It ran 63
sites (the 23 of the earlier reviews and 41 of its own: corner and
two-frontage lots, rotated and irregular parcels, deep and wide lots,
wetlands by the entrance, partial lidar, second buildings), each with
real angles and with the tests' angle patch, on both commits.

**Three blocking findings, all fixed (below).** Verified by it and
holding: 113 of 113 prototype records and 110 of 110 prototype sheets
byte-identical; on 124 default runs no change of feasibility, floor
area, second building or pond drainage and no new severity-1 flag;
trailer stalls changed once, upward; its own swept-path integration
(0.25 ft steps, in and out) found no body over a building, trailer row,
pond, drop yard or setback strip, or off the parcel beyond the
driveway's mouth, on 104 paths; the sight-side logic checked by hand;
28 records and sheets identical under two hash seeds; all 370 sheets
pass the sanitiser.

## Findings and what was done

| # | Finding | Severity | Done |
|---|---|---|---|
| 1 | `drive` returned a centreline that was not the path it searched. A pose already built on could be replaced by a cheaper one in the same cell, leaving sideways jumps of up to 11 ft and kinks down to 15 ft radius in a line recorded and printed as "no tighter than 45 ft" (7 of 102 designed plans, #21 with the angle patch among them; 15 of 100 random obstacle fields) | blocking | Every pose tried is its own record and never changes; a cheaper way to the same cell makes a new one. The line built is the moves searched. The last few degrees onto the lane are an arc, not a kink. Tested on 40 seeded obstacle fields: every line holds 45 ft from start to run-on, and its length is the sum of its moves. |
| 2 | Truck pavement was drawn across the paving setback to the property line (13 of 102 plans over 50 SF; 2,091 SF along 244 ft on the main test site), and the check that would catch it was set to zero | blocking | The pavement is clipped to the paving envelope and the driveway's own opening. The route's measure in the plan's checks is how far its swept path strays (2 ft at most, or the path is not taken), not zero. Tested. |
| 3 | An exception in the truck design crashed `plan_site`: only the last of its five call sites caught it | blocking | The design is guarded where it is called (inside `finish`, and the fallback in the ranking step): a plan is then as the search left it, with the reason in `access.truck_path`. Tested with the design raising, which the earlier test did not reach. |
| 4 | The goal forced arrival from the end the grid route had reached, giving loops of 210 to 255 degrees at 45 ft (a cross-dock: 1,726 ft, a 255 degree loop on new pavement, "swept path clear"); no articulation limit | should fix | The path comes onto the truck lane where it first reaches it, either way; the backing side is read from the way it comes on. The sight-side step alone asks for one way, and refuses a path with a bend over 135 degrees. |
| 5 | "No tight reverse bends" not met: 54 of 80 designed plans had one, typically 90 degrees right at 45 ft from 0 ft, then 90 left from 71 ft, reported as information; the turn off the street is not modelled; the throat counts the S-bend as stacking | should fix (judgement) | Two bends of 60 degrees or more to opposite hands at the minimum radius, the first within a truck's length of the street, are `TRUCK_ENTRANCE_S_BEND`, severity 2, before an offer. Other reverse bends stay information. **Not changed:** the truck is still taken square and straight at the street line, and the throat's length still counts the lead-in (both stated in the docs). |
| 6 | "No trucks crossing car areas": the lead-in ran through the car field on 33 of 80 plans, severity 3 unless the count fell short; remnants of 0.5 to 2.7 stalls beyond the lead-in not counted | should fix (judgement) | Stalls cut off from the rest of the field are counted with those under the pavement. With finding 5 the hard cases are a condition. **The operator's call** whether a lead-in over the car field should be allowed at all: see below. |
| 7 | Sheet: the "Truck path" row overprinted its label when it carried the stalls taken or the swept-path warning; the legend entry ran over the next | should fix | A shorter row, the stalls and the S-bend in a row of their own ("Truck lead-in"), a shorter legend entry. Sheets read again. |
| 8 | The search gave up at its cap where a path existed (a detour round a far end on a 1,200 ft lot), and the flag then stated a geometric cause | should fix | The search is steered by the distance still to go round what is in the way (shortest paths on a 10 ft grid from the lane), which also ends it at once where the lane is cut off; the cap is 200,000 poses. The reviewer's kind of case is a test (found in under a quarter of the cap). The flag says no path "was found". |
| 9 | When the drivable tier fires it can cost a lot (forced on 80 runs: floor area changed in 26, median -12%, worst -50%; trailer stalls to 0 in 8) and newly faced docks to the street in 5 | should fix / design risk | A layout with a way in is drawn in place of the larger one only with at least 80% of its floor area and nothing newly facing the street or homes; otherwise the ranking stands and `TRUCK_PATH_NONE` names it. Tested for the floor-area guard. **Not tested:** the street-and-homes guard on the tier's pick. |
| 10 | Nits: `sweep` overstated the trailer's cut-in by 1 to 1.9 ft at the default step, and the "1.3 ft against 1.9" held only at a 1 ft step; a path found only over the car field that clipped was reported as no path; `totals_both_buildings.cars_capacity` not reduced; `DEAD_END_ACCESS` measured 100 to 260 ft into the court; the note's counts did not reproduce | nit | The trailing is stepped six times finer than the bodies are drawn (1.4 to 1.8 ft at either step; test tolerance 0.6 ft). The clipping path is kept and reported as `TRUCK_SWEPT_PATH`. Totals reduced. `DEAD_END_ACCESS` uses the street-to-court length. Counts redone on the final code. **Not changed:** `truck_side.why` after two reverts in a row (read only, not reproduced). |

Tests: of the reviewer's 38 mutants 17 survived. Added since: the
record's flags one by one (`truck_record`: stalls taken and cut off,
short and not; the swept-path flag with what it clips; reverse bend and
entrance S-bend; throat and dead end on the path; no path), what a truck
must stay off (`truck_obstacles`, the pond and yards among them), the
pavement inside the envelope, the search's consistency, the lane taken
either way, the long detour, the wider line that must not become a way
round, the design raising. Still not covered: the sight step reverted
for a second building, `with_truck` refusing a worse re-finish,
`truck_first`'s street filter, the dock zone's depth in a plan.

## For the operator

- **A lead-in over the car field, with an S-bend at the street.** On 5
  of the 20 review plans the only way in is two hard turns across the
  corner of the car parking, because the driveway must stand 110 ft
  from the lot corner and the building fills the lot's width. The plan
  keeps its floor area and carries a condition. The alternative is a
  building about 90 ft shorter at that end (15% less on the 1200 x 1200
  site) with the end road in line with the driveway. Say so if such
  plans should be drawn the shorter way instead.
- **The 110 ft corner rule** is the planner's own (75 ft curb radii).
  A driveway allowed nearer the corner would remove most of these.
- **Blind-side backing stays information.** 6 of 20 plans.
- **The check is a screen.** One truck, forward, square off the street;
  no backing manoeuvre, no passing in a bend.

## After the fixes

- The 23 sites x 2 angle modes against the state of `reviews/42`: all 46
  prototype records byte-identical; on the 46 default records no change
  of floor area, trailer stalls or feasibility and no new severity-1
  flag. Figures in `01_decision_note.md`.
- Full suite (`python -m unittest discover -s screener/tests -t .`):
  1,040 tests OK in 683 s (1,004 in 604 s before this change; the
  planner's own tests are about 75 s longer). CI's `offline-tests` job
  still allows 5 minutes (`reviews/41`, "Open"): not changed here.

The fixes were not sent to a second independent review. They are larger
than in `reviews/42` (the search's bookkeeping and goal, the pavement's
clip, the guards, a new condition); they were checked by the author on
the 23 sites, the seeded search test and the full suite.
