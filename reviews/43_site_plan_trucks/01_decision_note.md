# 43 — Screener site planner: design for the truck (Tier 1)

**Decision: committed on `claude/festive-archimedes-eyjbo6`, on top of the
open PR #51 (`reviews/40` fix, `reviews/41`, `reviews/42`); not merged.**
After a merge a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline` on the
merge commit. The score should not change: the evaluator imports none of
the planner's modules.

Asked by the operator on 2026-11-19: the third of the five priorities of
"plan like a site planner, an architect, an engineer" (`reviews/41`):
driveways and court entries a WB-67 can turn, no tight reverse bends,
drivers backing in from their own side, stacking inside the gate, no
trucks through the car areas. "Go."

## Tier

Tier 1 (one independent reviewer), as `reviews/38` to `42`: a screener-side
change to the planner's default behaviour, with one new pure module
(`screener/siteplan_truck.py`: shapely and math only). No evaluator,
metric, credential or new external service; no new request.
`--prototype-behaviour` records are byte-identical (below).

## What was measured first

The search routes trucks as the shortest path on a 20 ft grid, buffered
to a 40 ft road. Nothing asked whether a tractor-trailer can steer it.
On the plans drawn for the 23 review sites (21 with a truck route):

| | Plans |
|---|---|
| Truck route with three reverse bends on legs of 2 to 9 ft and a 160 degree hairpin at the court | 10 |
| Trucks enter the court with the docks on their right (blind-side backing), by the end the grid route reached | 16 |
| No path a WB-67 can steer from the driveway to the court, round what the plan placed | 10 |

The ten are one defect. A truck driveway stands 110 ft or more from a
frontage corner (the planner's own rule, for its 75 ft curb radii), a
building's end road 20 ft inside the module's edge, and the module fills
the lot's width. So the driveway is in front of the car field, about
50 ft to one side of the end road, and the grid route ran 70 ft sideways
along the street in the 10 ft strip between the car field and the
property line.

## What changed (default behaviour only)

- **The model** (`siteplan_truck`). The AASHTO WB-67; `sweep` (the ground
  its body covers along a line), `drive` (a path that turns no tighter
  than 45 ft, by a search over positions and headings), `design` (the
  two together, tried again on a wider line where the body cuts a
  corner).
- **Truck first, then the pond.** The layout at the top of the ranking
  is finished again with its truck road laid out first; the car route,
  ponds and drop yards go round it, and detention is sized for its
  pavement. In the court the path comes onto a truck lane off the strip
  in front of the dock doors, from whichever end is nearer.
- **The lead-in over the car field.** Where the driveway stands in front
  of the car field, the path takes its corner; the stalls are counted
  off the rows and the plan says so. A hard S-bend as trucks come off
  the street is a condition, not a note.
- **The ranking.** A layout no WB-67 can enter ranks below one it can
  (after the pond that drains, before floor area) when that one keeps
  at least 80% of the floor area and turns nothing new to the street or
  homes. Where the top layout has no way in, layouts with an end road
  in line with a buildable driveway are added first. Otherwise the
  ranking stands and the plan carries `TRUCK_PATH_NONE`.
- **Sight-side backing.** The best plan's trucks are sent to the end of
  the court from which they back in on the driver's side, where nothing
  else is worse and no second building is lost.
- **Flags** (with playbook entries): `TRUCK_PATH_NONE`,
  `TRUCK_SWEPT_PATH`, `TRUCK_ENTRANCE_S_BEND`,
  `PARKING_SHORT_FOR_LEAD_IN` (severity 2);
  `TRUCK_LEAD_IN_OVER_PARKING`, `TRUCK_REVERSE_BEND`,
  `BLIND_SIDE_BACKING` (information). `SHORT_THROAT` and
  `DEAD_END_ACCESS` are measured on the path.
- **The record and the sheet.** `access.truck_path`, `inputs.truck_side`,
  `inputs.truck_first`, `drivable` on the layouts judged,
  `cars.row_capacity` less the stalls taken. On the sheet: the path's
  centreline as a lane line, its pavement widened to the swept path,
  "Truck path", "Truck lead-in" and "Truck court entry" in the takeoff,
  the basis in the footnotes.
- Docs: `screener/README.md`, `screener/SITE_PLANNING.md`.

Two things were tried and dropped on the way, and why:

- **Shortening the building to line its end road up with the driveway**
  as the first remedy. It cost 15% of the floor area on the 1200 x 1200
  site (335,000 to 286,000 SF) where giving the lead-in nine car stalls
  keeps all of it. It is kept as the fallback only.
- **Laying the path out round the pond the search had placed.** The pond
  was sited against the grid route and could stand in a real truck
  road's way. The truck road is laid out first instead.

Not in this change: the backing manoeuvre into a door, two trucks
passing in a bend, a gate and its stacking beyond the throat's length,
one-way circulation round the building, the car route's own geometry,
drop-yard links. The other two priorities (use of the land, structural
bays) are not started.

## Sources for the design vehicle

Read in this work (public pages; the Green Book itself was not):

| Figure | Value | Read from |
|---|---|---|
| Length, width, front and rear overhang | 73.5, 8.5, 4.0, 4.5 ft | a freight-planning curriculum page citing AASHTO 2018, Table 2-4a |
| Minimum design turning radius, centreline turning radius, minimum inside radius | 44.8, 41.0, 1.9 ft | TxDOT Roadway Design Manual, Table 4-1 |
| Centreline turning radius | 41 ft | Iowa DOT Design Manual 6A-2, citing the Green Book 2011 |

Not read from a page: the split of the wheelbase (19.5 ft tractor,
45.5 ft kingpin to the trailer's axles). With those two the model gives
44.8 ft at the outer front wheel on a 41 ft centreline (the published
figure, which fixes the tractor's 19.5 ft) and an inside radius of
1.4 to 1.8 ft on a 180 degree turn against the published 1.9 ft (the
reviewer's exact integration of the same dimensions gives 1.96 ft).

## What was verified (author, after the review's fixes; the review is `02_review.md`)

| Check | Result |
|---|---|
| Prototype parity | 46 prototype records (23 sites, real angles and the tests' angle patch) byte-identical to the state of `reviews/42`. |
| The 23 sites, default, both angle modes (46 records) | Floor area, trailer stalls and feasibility unchanged on all 46; no new severity-1 flag; the ranking tier changed no plan. New severity 2: `TRUCK_ENTRANCE_S_BEND` on 7 records, `PARKING_SHORT_FOR_LEAD_IN` on 5. Gone: `FIRE_SECOND_ACCESS` on 2 (the driveway moved to the sight-side end). |
| The 23 sites, real angles | 20 plans have a truck route; all 20 have a designed path whose swept path is clear. 10 take 2 to 13 car stalls for the lead-in (3 then short of the count); 5 have the S-bend at the street. 13 come onto the lane on the driver's side (11 turned there, 2 already), 6 stay blind with the reason, 1 has courts on both sides. |
| Run time | 46 default plans: 231 s before, 251 s after (+9%). |
| Sheets | #21, #25 and three synthetic sites redrawn and read. |
| Tests | New module `test_siteplan_truck` (21 tests) and classes `Trucks` and `TruckTier` in `test_siteplan` (15 tests). Full suite: in `02_review.md`. |
