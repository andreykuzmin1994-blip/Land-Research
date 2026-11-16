SYSTEMIC PLANNER PROBLEMS, RANKED BY IMPACT ON CREDIBILITY
(27 plans reviewed: 12 judged not credible ("no"), 15 judged "mostly")

The 12 "no" plans are 115-170, 13, 170, 20, 26, 3, 41, 5, 53, 56, 64 and 8. Problems 1 to 3 below account for every one of them.

---------------------------------------------------------------
1. Fixed rear-load cross-section underbuilds narrow and deep lots
---------------------------------------------------------------
**Symptom.** The module depth is always (car rows on the office wall) + D + court 130 + trailer row 55. That is at least 424 ft at D=190. On lots 340-450 ft wide the module only fits turned crosswise, so the building is short and squat (L/D 1.3, 240-300 ft long). The rest of the long axis is left empty. Yield comes out at 1,800-6,600 SF per gross acre, against comps of 10.5-15.4k, and is often about half of what the lot would hold. The trailer row is drawn even when the target is 5-22 stalls (for example 43 vs 12, 51 vs 14, 60 vs 19). Cars can only go in strips along the office wall, so a high parking minimum (Atlanta 1/600) turns directly into extra module depth. The MARKET Lmin/Lmax values are arbitrary floors and caps: Lmin 600 blocks 310-deep buildings, Lmax 1,150 cuts a building short, there are no depths under 190 or over 310, and squat Lmin values of 220-300 are allowed.

**Fix (`cross_section` / `build_plan` / `fit_config` / MARKET):**
- Add a court-only variant (130 ft court, no trailer row) and use it when court-side stalls, or a small drop yard in leftover land, meet `trailers_target` (spec step 5). Size any trailer row to the target instead of always running it full length.
- Add parking layouts beyond the office-wall strip: cars at the building ends, cars in leftover land or lobes, and a partial last row. Then required parking no longer has to be added to module depth.
- Add shallow-bay depths of 140-160 and deep-bay depths of 340-400 for buildings over 350k SF. Replace the hard Lmin/Lmax values with an L/D range of 2.0-3.8 (warn outside it).
- Add a front-load or side-load fallback for narrow lots.
- Treat `parking_required('atlanta')` 1/600 as a variant to test, not a hard constraint, while ATL_PARKING_VERIFY is open.

**Plans affected:** 13, 26, 3, 5, 8, 41, 53, 56, 64, 100, 21, 57, 23, 45, 25, 30, 4, 83, 44

---------------------------------------------------------------
2. Access routes are straight lines that are never checked against anything
---------------------------------------------------------------
**Symptom.** `finish()` draws each route as `LineString(drivewayPt, nearest_points(target))`. Nothing checks it against the parcel, P (the paving envelope), constraints, steep cells, ponds, drop yards or the other route. As a result, routes:
- leave the parcel: 170, 30, 48 (crosses the outparcel notch), 9;
- run through the residential buffer or outside P: 8, 30;
- cross wetland or floodplain and steep ground: 115-170;
- run over ponds: 23, 44, 170, 9;
- run through the transmission corridor that P had excluded: 170.

The two drives also converge on the same corner, so the truck/car separation is only apparent (20, 41). Ponds and second-building ponds are placed with no reserved corridor and the routes are never checked again. The route pavement is also left out of impervious area and detention: 41, 48, 8.

**Fix:**
- Route inside P as a visibility graph or grid A* (spec step 6), over P minus ponds, drops, constraints and steep cells. Add a cost for crossing truck pavement on the car route.
- If no path exists, raise ROUTE_INFEASIBLE (sev 1) and reject or rerank the plan.
- Lay out routes before ponds (or reserve the corridor), and re-validate after every later placement.
- Add the route polygons to `imp`.

**Plans affected:** 115-170, 170, 23, 30, 48, 8, 9, 44, 41, 20, 100, 21, 39, 64

---------------------------------------------------------------
3. No access gate: plans with no access or one dead-end access are still drawn as buildable
---------------------------------------------------------------
**Symptom.** ACCESS_NONE is only a flag. `plan_site()` still runs `fit_config` and the second-building search and reports SF, doors, trailers and ponds as if the site were buildable (13, 3, 5, 53, 56). The second-building flag text says it "shares the site driveways" when there are none. With no frontage, `prefer_dock_away` is None and the flip is arbitrary; in 56 the docks face the only access.

Flag-lot or panhandle poles are missed and reported as no road (56).

FIRE_SECOND_ACCESS only runs when both `tpt` and `cpt` exist, so single-access and zero-access sites skip it (13, 170, 8). There is no dead-end length check, although 170 has a 1,300 ft single approach.

The diagonal is taken from `P.bounds`, which gives false positives on odd-shaped lots (83, 9).

**Fix:**
- Gate in `plan_site()`: on ACCESS_NONE, either stop with "landlocked / access by easement only", or label the plan HYPOTHETICAL and keep its totals out of the results.
- Detect narrow poles and panhandles and report "pole access N ft" with ACCESS_CONSTRAINED.
- Run the fire check whenever there are fewer than 2 driveways. Add a dead-end flag for approaches over 750 ft.
- Compute the diagonal from the building or module, not `P.bounds`.
- With no frontage, orient cars toward the nearest probable access.

**Plans affected:** 13, 3, 5, 53, 56, 170, 8, 83, 9

---------------------------------------------------------------
4. Rear-load end roads run through the car-field depth, so trucks and cars mix
---------------------------------------------------------------
**Symptom.** The end roads are `rect(0, TRUCK_ROAD, 0, car_depth+D)`, so they start at the car-side edge. Three things follow:
- Every car approach from an end crosses a truck road, which breaks spec 1.6.
- The truck route goes to the nearest point of roads ∪ courts, which is usually the car-side stub. Trucks then pass the office pod and the car aisles to reach the court.
- The car aisles open straight onto truck roads, with no island.

Throats are 22-76 ft against the 100 ft minimum, with no gate stacking. The end roads dead-end at the lot line with no hammerhead, and DEAD_END_COURT is never emitted. The cross-dock variant puts the car field inside the truck loop, between the two courts (33). A related bug: `car_rows_layout()` counts rows from the outer edge, so the wall row and aerial aisle end up at the property line (21).

**Fix:**
- Start the end roads at `v = car_depth` (the wall line) and give the car field its own link to the frontage.
- Aim the truck route at a court-end entry point, not the nearest point.
- Enforce a truck throat of at least 100 ft and add a test that rejects any car route intersecting truck pavement.
- Add a turnaround, or a loop with a gated emergency crossover, and emit DEAD_END_COURT.
- For cross-dock, put the car field outside the courts on the frontage side.
- Fix the row origin in `car_rows_layout()`.

**Plans affected:** 100, 115-170, 170, 13, 21, 23, 25, 33, 39, 41, 45, 55, 57, 64, 83, 8

---------------------------------------------------------------
5. Driveway placement ignores segment length, spacing across pieces and side lines
---------------------------------------------------------------
**Symptom.** `driveway_points()` takes the frontage piece nearest the court, however short it is (16-108 ft fragments), and puts T at its midpoint, so the 75 ft radius returns land on the neighbour (20, 26, 55, 9, 48). The frontage MultiLineString is fragmented at bends, so the 95 ft clamp lands at a joint or corner (30, 41, 57). Spacing is checked in full only on the same piece; on other pieces 0.5x spacing (125 ft) is enough (20: 135 ft, 26: 166 ft). The `[45, len-45]` keep ignores the half-footprint, the 5 ft side-line clearance and steep cells (21, 39). There is no ranking by road class or length, so 9 and 48 skip the long main frontage. Fragments also cause a false ACCESS_CONSTRAINED (55: "16 ft" on 3,416 ft of SR 14) and double-counted frontage (83).

**Fix:**
- Merge frontage into contiguous runs (`linemerge` plus a snap tolerance) and deduplicate frontage on both sides of a road that runs through the parcel.
- Rank runs by whether they can hold the footprint (WB-67 about 190 ft of curb, plus clearances) and by road class, then by distance to the court (spec step 3).
- Apply the full spacing across all pieces.
- Test the radius-return polygon against the side lines (at least 5 ft) and steep cells.
- Raise ACCESS_CONSTRAINED to sev 1 when no run can hold a truck driveway.

**Plans affected:** 20, 26, 30, 41, 55, 9, 48, 57, 21, 39, 83

---------------------------------------------------------------
6. Pond placement: the lowest lidar point anywhere, with no regard to drainage
---------------------------------------------------------------
**Symptom.** `place_rect_lowest()` sorts every DEM sample in the pond region and takes the first rectangle that fits. So ponds end up:
- across a stream from the development: 115-170, 39;
- across a public road or in a detached part of the parcel: 83, 4;
- in a separate lobe 600-1,000 ft away: 100, 26, 3, 44, 45;
- on the opposite side from the streams that would be the outfall: 44, 45;
- in the middle of the only second-pad land: 115-170;
- in the only access corridor or circulation strip: 56, 53.

On flat sites the "lowest point" is noise (56). Order A forces `npd = 2` at least, even when one pond fits (4, 53). `pond_bottom_ft` goes stale when the ponds are replaced (53, 55). The pervious term counts the whole parcel, including undisturbed woods (8).

**Fix:**
- Limit `pond_region` to the polygon part that holds the module, on the same side of mapped streams, ROW and constraint lines, and downhill of the pad.
- Score candidates on elevation below the pad plus a conveyance-distance penalty plus adjacency to impervious area.
- Add a flat-site fallback (adjacent to the court), and reserve route corridors and second-pad land.
- Set the `npd` minimum to 1, refresh `pond_bottom_ft` after replacement, and set `perv_ac` from the disturbed area only.

**Plans affected:** 115-170, 39, 83, 4, 100, 26, 3, 44, 45, 56, 53, 55, 8, 23

---------------------------------------------------------------
7. The second-building path counts SF without access, and leftover land is never used
---------------------------------------------------------------
**Symptom.** Routes, driveways and the fire check are built only for the first plan in `finish()`. The second building's SF is counted with no truck or car connection (23, 4, 55, 53, 3). On 55 that is 236k SF, with a self-inflicted FIRE_SECOND_ACCESS on a 3,400 ft frontage. The first pond is placed before the second pad is searched, which blocks it (115-170). The search only accepts a full module of 50k SF or more, so remnants, lobes and panhandles stay empty (33, 39, 23, 41) and nothing smaller is tried (drop yard, IOS, expansion). The 40 ft gap between modules gives two parallel truck roads (4).

**Fix:**
- Run the full `finish()` (driveways, routes, fire check) for each building, and give a building its own driveway when the frontage allows.
- Do not count the SF of a building that has no route.
- Reserve the second pad before placing ponds, and let adjacent modules share an end road.
- Fill remnants that cannot hold a module with drop-trailer, IOS or parking program.

**Plans affected:** 23, 4, 55, 53, 3, 115-170, 33, 39, 41

---------------------------------------------------------------
8. Buffers and edge classes: stream buffers, loading strips, residential detection, transmission corridor, outparcels
---------------------------------------------------------------
**Symptom.**
- P is cut only by mapped constraints, with no stream buffer (25 ft state, 100 ft South Fulton). Trailer rows sit 20-30 ft from streams (39, 115-170, 8, 55), and `render()` hides STREAM_PIPELINE_UNMODELLED and ZONING_DRAFT.
- `classify_edges()` has only ROW/RES/OTHER. There is no IND, NONRES or UNKNOWN class, so the 50 ft loading strip (spec 1.3) and Fairburn's 50 ft buffer to all other districts are never applied. `JUR['atlanta'] pave OTHER = 0` puts pavement on the lot line (26, 3, 45). Fairburn's OTHER is 5 ft (48, 83, 64, 5, 56).
- Residential is detected from the assessor tax class, not zoning. RES_REAR is assigned by the 0.5 x dmax midpoint rule, which labels the long side lines of deep lots as rear (8).
- The transmission corridor is cut from P instead of only from B (the building envelope), which kills yield (170, 83).
- Enclosed outparcels are not detected (23).

**Fix:**
- Buffer streams and wetlands in P by jurisdiction, and stop hiding those flags.
- Add IND, NONRES and UNKNOWN edge classes from zoning layers, with the loading strip and district buffers by jurisdiction. Classify side and rear by the angle to the frontage, not by distance.
- Subtract `trans` from B only, and allow paving or trailers under the lines with a flag.
- Detect interior rings and other owners' parcels as outparcels (raise an assemble or easement flag).

**Plans affected:** 115-170, 39, 8, 55, 26, 3, 45, 48, 83, 64, 5, 56, 170, 23

---------------------------------------------------------------
9. Dock orientation ignores residential and street edges
---------------------------------------------------------------
**Symptom.** `fit_config()` scores the flip only by court distance from the frontage. Courts and trailer rows face residential edges (20, 33, 4, 41) or streets (64, 44). LOADING_FACES_RES and STREET_FACING_DOCKS (spec 4) are not implemented, and neither are Fairburn M-2 "docks at rear unless screened" or College Park's limits on truck parking in street-side yards.

**Fix:** Penalise courts facing RES or ROW edges in the flip score (spec 4 dock-face rules), and emit LOADING_FACES_RES and STREET_FACING_DOCKS.

**Plans affected:** 20, 33, 4, 41, 64, 44, 3, 5, 56

---------------------------------------------------------------
10. Grading: cut/fill covers the pad only, and steepness never affects ranking
---------------------------------------------------------------
**Symptom.** `cutfill = building.area * mean|dz| / 2 / 27` uses only the DEM points inside the building. The courts, roads, car field and walls are left out, so earthwork is understated by several times (115-170, 25, 33, 39, 48, 83). `fit_config` ranks only by SF and court distance. Buildings land on the worst ground (115-170: 75 ft of relief, 21% steep), and steep cells are never subtracted from P or B (3, 45, 53).

**Fix:**
- Compute cut/fill per terrace plane over the whole graded footprint (module, routes and pond).
- Add a grading-cost term to the `fit_config` ranking.
- Derive RETAINING_WALL or RELIEF flags from the relief across each element.
- Optionally exclude cells steeper than 15% from B.

**Plans affected:** 115-170, 25, 33, 39, 48, 83, 3, 45, 53

---------------------------------------------------------------
11. The spec 1.11 sanity bands and accounting are missing from `report()`
---------------------------------------------------------------
**Symptom.** None of these are emitted:
- SF per gross acre or FAR outside the band (almost every plan: 1,800-9,800 against 10.5-15.4k);
- L/D outside 2.0-3.8;
- DOOR_DENSITY_HIGH (12.9-13.75 ft of wall per door against a floor of 14);
- impervious ÷ footprint outside 1.8-2.3 (2.75-3.6 seen).

So the underbuilt plans in problem 1 pass with no warning. GIS acreage against the tax roll is not reconciled (83: 44.4 vs 37.9 ac; 9: 21.5 vs 19.9 ac). College Park's 35 ft height cap only flags; the plan should drop to about 24 ft clear or be marked non-viable (44).

**Fix:**
- Implement the spec 1.11 bands as flags, and feed the SF-per-acre gap back into variant search, so that a plan below band forces the problem 1 variants to be tried.
- Check door count against the usable wall length.
- Add ACREAGE_MISMATCH.
- Reduce clear height to fit the height cap before reporting.

**Plans affected:** 100, 20, 25, 23, 5, 53, 56, 57, 64, 8, 41, 26, 3, 13, 30, 39, 83, 9, 44

---------------------------------------------------------------
12. Parking count: whole rows over-park, and islands and stall width are ignored
---------------------------------------------------------------
**Symptom.** `car_stalls = n * int((L-48)//9)`, so being one stall short adds a whole row. That gives 122 vs 80 (100), 144 vs 94 (23) and 102 vs 69 (25). There is no deduction for landscape islands or ADA stalls (39 and 41 drop below the requirement under the South Fulton island rule, which should raise PARKING_DOES_NOT_FIT). `STALL_W = 9` is used everywhere, although Fairburn requires 10x18 (48).

**Fix:** Allow partial last rows and end-zone stalls. Count with the spec 1.5 gross SF per stall or the island rules by jurisdiction. Set stall dimensions per jurisdiction in `JUR`.

**Plans affected:** 100, 23, 25, 39, 41, 48, 57, 26, 45

---------------------------------------------------------------
13. Jurisdiction use and access rules are not gated
---------------------------------------------------------------
**Symptom.** `JUR` carries only dimensional values. These rules are never checked:
- East Point I-1 warehousing is use-permit only, with 5,280 ft of separation from residential, which could be fatal to the site (9);
- Fairburn collector frontage, on-site truck queuing and M-2 dock-at-rear (30, 48, 64);
- College Park truck-parking yard rules (44).

**Fix:** Implement the spec step 0.2 use-status and separation gate in `plan_site()`, and extend `JUR` with the access, queuing and dock rules from spec 1.8 and 1.10.

**Plans affected:** 9, 30, 48, 64, 44

---------------------------------------------------------------
**Priority note:** fixing problems 1 to 4 would address the main defect in every plan rated "no".
- Problem 1 (the cross-section variants) is the largest yield and credibility lever: 11 of the 12 "no" plans.
- Problems 2 and 4 (route search inside P, and end roads that start at the wall line) remove most of the "wrong" geometry.
- Problem 3 (the access gate) is the cheapest fix and stops 5 plans from being presented as buildable sites.

Classification: all of these are planner code changes. If the planner lives in `site_fit.py`, which is immutable during a run, they are Tier 2 between-runs changes, and once any zoning jurisdiction is verified they need a new run and baseline. If it lives in `screener/rank.py`, they fall under `screener/autoresearch/PROGRAM.md`.
