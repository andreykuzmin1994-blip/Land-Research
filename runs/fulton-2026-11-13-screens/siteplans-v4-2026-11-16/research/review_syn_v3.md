SYSTEMIC PLANNER PROBLEMS (siteplan3), ranked by impact on credibility

Scale: 25 plans reviewed. 9 rated "no" (115-170, 20, 30, 39, 45, 53, 55, 56, 8) and 16 rated "mostly". The first six problems below cause almost all of the "wrong" findings and every "no" verdict.

1. Truck entries sit at the car-side corners (v=0), and end roads run through the car field depth
- Symptoms:
  - Driveways are swapped: T is opposite the car field and C is opposite the court.
  - The car route crosses the truck route (CAR_CROSSES_TRUCK).
  - Trucks drive past the office pods and the ends of the car aisles.
  - Car aisles open onto truck pavement.
  - End-road stubs dead-end at the car field.
  - Throats are short.
- Fix:
  - In build() (around line 164), put `truck_entries` at the court's outer corners, (ue0, vB+D+court) and (ue1, vB+D+court), not at v=0.
  - Start the end roads at the wall line, `rect(ue0, uL, vB, vB+D+court)`, not at 0. Add a curb or island between the car field and the truck pavement.
  - In pick_driveways(), choose T and C together: T near the court end, C near the car field, and maximise separation.
  - Make the office-side aerial/fire lane a fire lane only, not part of the truck network, and set it 15-30 ft off the wall (D105).
- Plans: 100, 124, 21, 23, 25, 26, 30, 33, 39, 4, 41, 44, 48, 57, 64, 9, 20, 5.

2. Drop yards are unreachable, placed anywhere, and still counted as provided
- Symptoms:
  - Yards are 300-2,000 ft from the court, behind ponds, streams, steep ground or the car lot.
  - No road connects them.
  - Their stalls still count toward the target. That suppresses TRAILER_SHORT and lets "court-only + remote yard" win on SF.
- Fix:
  - In finish() (lines 579-592), replace `v2.place_rect_lowest(free, ..., {}, ang)`. Score candidates by distance to the court or end roads, and reject any without a truck route (grid_route from the court or end road).
  - Add the access aisle to the route network and to impervious area.
  - Count yard stalls only when the yard is reachable. Otherwise emit TRAILER_SHORT or DROP_YARD_UNREACHABLE.
  - Size yard depth to the stall count, not a fixed 190 ft.
  - Try more angles than building and building+90.
  - Prefer a partial court row before any remote yard.
- Plans: 100, 115-170, 21, 23, 26, 3, 30, 33, 39, 45, 53, 55, 56, 64, 83, 8.

3. Routes are not confined to the paving envelope P, and route polygons spill outside the parcel
- Symptoms:
  - Truck and car routes hug lot lines, run inside residential, ROW and jurisdiction buffers, and run outside the dashed envelope.
  - Their 40 ft (truck) and 24 ft (car) polygons extend past the property line, by up to about 3,500 SF on plan 41.
- Fix:
  - In finish() (line 514), set `walk = P.buffer(-half_width)` (20 ft for trucks, 12 ft for cars) minus wet_buf, cut, ponds and modules, instead of `parcel.buffer(-3)`.
  - Assert `route_poly.within(P)` (driveway throats excepted) and flag ROUTE_OUTSIDE_ENVELOPE as a failure.
  - Route before ponds are placed, or reserve the route corridor first.
- Plans: 100, 124, 20, 26, 30, 33, 4, 41, 44, 48, 8.

4. Ranking selects infeasible or inferior plans
- Symptoms:
  - (a) When every alternative fails, the largest one wins (115-170: ROUTE_INFEASIBLE, yet its 264k SF counts in total_sf).
  - (b) The x0.8 TRAILER_SHORT multiplier trades about 10-20% of building for a few stalls (124, 21, 44).
  - (c) A trailer surplus is never penalised, so full-length rows win (25, 44).
  - (d) Unreachable drop yards make court-only win (39, 64, 83).
  - (e) Relief and cut/fill are not scored (25, 39).
- Fix:
  - Make final_score lexicographic: route- and access-feasible first, then SF. Treat a trailer shortfall as a stall-for-SF trade or a tie-breaker, not a 20% haircut.
  - Penalise surplus pavement and add a relief/cut-fill term.
  - In report(), zero total_sf on route_problem, not only when `hypothetical`.
  - In plan_site(), when the best placement is unreachable, exclude that pad and re-run fit_variant. Today the retry only shrinks L (length_cap=L-40), so it keeps the same pad.
  - Make access reachability part of the placement search.
- Plans: 115-170, 124, 21, 25, 39, 44, 64, 83, 20, 41.

5. Multi-building placement ignores building 1's ponds, car field and routes, and double-counts detention
- Symptoms:
  - Building 2 is drawn over building 1's ponds (53: about 10k SF; 55: about 29k SF).
  - Building 2's court abuts building 1's car lot and pond rims (33).
  - Building 2 inherits T and C with no driveway search of its own, so its truck lane crosses C (33). It has no car route, fire check or dead-end check (33, 55).
  - Building 2's ponds are sized on the combined impervious area while building 1's ponds are kept, so drawn storage is far above reported storage (33: about 18 vs 10.5 ac-ft; 53, 55).
  - Primary and secondary buildings are not compared on combined SF (53).
- Fix:
  - In plan_site(), build `used` as module.buffer(40) + drops + routes + best_o['ponds'] + building 1's car field and route polygons before computing P2 and B2.
  - Give building 2 its own pick_driveways() pass, its own car route and its own fire and dead-end checks.
  - Size building 2's detention on the incremental impervious area only (finish lines 558-559), and have report() sum the pond volumes actually drawn.
  - Rank building combinations by total SF.
  - Make orient_score aware of the neighbouring building's car and truck zones, so courts face courts.
- Plans: 33, 53, 55 (and 8 via `used` omitting ponds).

6. Failed car access passes silently, and car entries sit on unreachable edges
- Symptoms:
  - car_driveway is true but car_route_ft is 0, with no flag and ok stays True.
  - Car entries sit at the car field's outer edge on the lot line, or at the far end.
  - With one driveway, no car route and no throat split is drawn.
- Fix:
  - In finish() (lines 530-536), when both the separated and the fallback grid_route return None, emit CAR_ROUTE_NONE at sev 1 and set ok=False.
  - In build() (line 154), put `car_entries` on a car-field edge that faces a reachable area, never on the lot line.
  - With one driveway, route from tpt to car_entries and draw and check the split inside the throat. Do not just print ACCESS_SHARED.
- Plans: 23, 41, 45, 83, 30, 33, 55, 8, 20.

7. Car-field, office-pod and orientation logic: cars sit outside the end road, pods are always at both ends, and the u-axis is never flipped
- Symptoms:
  - Employees cross 40 ft of truck road from the end car field to the office.
  - The second pod is 360-1,160 ft from any parking.
  - The car end is never chosen against access, because Frame.flip mirrors only v. Plan 45 is inverted (cars behind the court) and plan 20 has cars about 1,900 ft from the drive.
  - orient_score caps distances at 400 ft (deep flag lots tie).
  - With no frontage, orientation is arbitrary (13, 3, 5).
- Fix:
  - In the build() 'end' variant (lines 146-158), put the car field against the office end with the truck end road outboard or at the far end.
  - Place office pods only at ends that have parking.
  - Add a u-mirror to Frame.flip and score all four flips.
  - Remove the 400 ft cap in orient_score.
  - With no frontage, orient toward the nearest probable access (nearest road or pole).
- Plans: 124, 13, 20, 26, 3, 4, 45, 56, 8, 83, 5.

8. Access and fire checks are missing or wrong
- Symptoms:
  - No dead-end over 750 ft check or pole-access detection (20: 1,764 ft; 8: about 1,400 ft; 55: 1,224 ft).
  - A real pole is reported as ACCESS_NONE (56).
  - SHORT_THROAT is sev 3 only (18-87 ft throats) and reports "about 0 ft" when no route exists (115-170).
  - FIRE_SECOND_ACCESS uses the axis-aligned bounds of a rotated building, giving false positives (25, 57, 64, 9), and fires with 0 driveways on landlocked plans (3).
  - No court turnaround is generated and DEAD_END_COURT does not exist (25, 57, 9, 41).
- Fix:
  - In finish() and plan_site(), compute dead-end length along the route and flag over 750 ft. Detect narrow poles or panhandles toward a road and emit ACCESS_CONSTRAINED (pole width vs WB-67).
  - Make SHORT_THROAT under 100 ft a constraint that triggers a re-plan, and skip it when there is no route.
  - Fire check (line 547): use the rotated rectangle's own diagonal, hypot(L, D)/2, and skip it when there are no runs.
  - Generate a hammerhead or loop for one-ended courts and implement DEAD_END_COURT.
- Plans: 20, 8, 55, 56, 115-170, 25, 57, 64, 9, 3, 41, 45.

9. Edge classes, buffers and jurisdiction rules are incomplete
- Symptoms:
  - Only ROW/RES/OTHER classes exist, and OTHER paving is about 0-5 ft, so courts sit 5 ft off lot lines.
  - Fairburn's 50 ft buffer to other districts is never applied (48, 55, 64, 83).
  - The 50 ft loading strip and landscape strips are never applied (100, 25, 5, 9).
  - No IBC 507 60 ft yard for unlimited-area buildings (5).
  - wet_buf is a generic 25 ft everywhere: no South Fulton 100 ft stream buffer (39), and ponds can extend into wet_buf (48).
  - No College Park street-yard truck parking rule (44) and no Fairburn truck queuing rule (64).
- Fix:
  - Add IND/NONRES/UNKNOWN classes to classify_edges() from the zoning layers, with per-jurisdiction pave/bldg/landscape/loading-strip values in JUR.
  - Subtract 60 ft from interior lines in envelope() for unlimited-area buildings.
  - Use a per-jurisdiction stream buffer.
  - Subtract wet_buf and `trans` from the pond region (line 566).
- Note: these are planner-side values. Human-owned zoning facts in zoning_rules.json stay human-edited.
- Plans: 100, 25, 48, 64, 83, 55, 56, 5, 39, 44, 9, 4.

10. Driveway siting rules are too crude
- Symptoms:
  - T and C land 5-90 ft from parcel corners, because the clamp is measured along merged runs that include bits of the side lines, and radius returns are never tested against side lines.
  - Spacing is a fixed 250 ft, not taken from road class or speed.
  - There is no remoteness objective (39: 262 ft apart on a 1,276 ft frontage).
  - The run nearest the target is taken, not the longest straight run (48).
  - Frontage double-counts both sides of a through road (83).
- Fix:
  - In pick_driveways(), measure clearance from property corners and intersections, test the radius-return polygon against side lines, take spacing from road class and speed, and add a separation objective.
  - Rank runs by length and straightness.
  - Deduplicate opposite-side runs in frontage_runs().
- Plans: 26, 30, 39, 41, 44, 48, 55, 83, 9, 33.

11. Massing variant bounds and program constraints are off-market
- Symptoms:
  - lo=1.5*D allows squat boxes (115-170: 660x400; 20: 480x310).
  - Rear-load goes to D=400 at more than 450k SF, where cross-dock belongs (55, 33).
  - hi=4.2*D caps length, leaving land unused (8: 660 ft at D=160 on a 1,500 ft envelope).
  - The fixed cross-section is never rotated to the parcel (56).
  - The trailer row is all-or-nothing, full length (25, 44, 41, 5, 20).
  - Atlanta 1/600 parking is a hard constraint with no market-ratio variant (21, 26, 3, 45, 57).
  - HEIGHT_CAP only flags and does not reduce clear height (44, 9).
  - Remnants get no parking, IOS or drop program (26, 57, 64, 30, 9).
- Fix:
  - In VARIANTS and length_range, set lo of about 2.0*D and hi of about 3.8*D unless the envelope allows more, cap rear-load D at about 300, and switch to cross-dock at 450k+.
  - Allow a partial trailer row sized to the target (court_stalls up to need).
  - Run a lower-ratio parking variant while ATL_PARKING_VERIFY is open.
  - Reduce clear height or mark the plan non-viable under HEIGHT_CAP.
  - Try parcel-aligned rotations.
  - Program remnants.
- Plans: 115-170, 20, 23, 25, 33, 41, 44, 45, 5, 55, 56, 57, 8, 9, 21, 26, 3.

12. Flags are stubbed, use wrong thresholds, or are missing
- Symptoms:
  - LOADING_FACES_RES and STREET_FACING_DOCKS are not implemented: report() has `for c in p.courts + p.rows: pass` and `res_near` runs over an empty list.
  - The L/D check is `> 4.2` while the message cites 3.8 (23, 55).
  - BELOW_COMP_BAND uses 9,000 while the comp band is 10,500 (41), and is suppressed for hypothetical plans (56).
  - No impervious-to-footprint band (57).
  - DOOR_DENSITY_HIGH and LD_OUT_OF_BAND are sev 3 only.
  - Trailer-row ticks are drawn every 24 ft against 12 ft stalls (124).
  - Road class comes from a 'local' string match (30).
- Fix:
  - Implement dock-face flags per court face against RES and ROW edges.
  - Align the thresholds with the message text and the comps.
  - Add the impervious/footprint band.
  - Fix the tick spacing to 12 ft.
  - Take road class from GDOT attributes.
- Plans: 20, 23, 33, 4, 41, 44, 45, 55, 8, 57, 124, 30, 56.

13. Pond placement ignores outfall, access and circulation
- Symptoms:
  - Ponds land on the opposite side from streams and the probable outfall (4, 44).
  - Ponds sit 450-500 ft from the court (100).
  - No 12 ft maintenance drive (25, 39, 4).
  - A pond inside a transmission corridor (83).
  - Ponds block the access corridor or the direct truck approach (56, 4, 8).
  - No variant trades building area for a pond (124).
- Fix:
  - In place_pond(), add outfall and stream proximity terms and draw the access drive.
  - Place ponds after a route corridor is reserved.
  - Subtract `trans` or flag utility consent.
- Plans: 100, 4, 44, 48, 83, 56, 25, 39, 8, 124.

14. Grading uses a single balanced plane, and relief is not scored
- Symptoms: 40-76 ft of relief and 49k-200k CY are reported with no terraces and no effect on ranking.
- Fix: add terraced grading, or a relief and earthwork term in prelim() and final_score().
- Plans: 25, 39, 48, 8, 45, 115-170.

Fixed since v2, per the reviewers:
- The hypothetical / ACCESS_NONE gate works (13, 3, 5, 56).
- East Point use-permit and height gates are flagged (9).
- ACREAGE_MISMATCH and COLLECTOR_FRONTAGE now fire.
- Underbuilding is fixed on 100.
- On 4, the pond is now on the developed piece.