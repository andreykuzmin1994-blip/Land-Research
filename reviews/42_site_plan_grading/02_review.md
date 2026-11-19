# 42 — Review of the grading change

The Tier 1 reviewer (fresh context, given the code and the requirements,
not the author's conclusions) reviewed `58ee6b3..3f7abd1`. It ran 108
sites (the 2 fixtures, 42 of the earlier reviews and 64 of its own:
two-frontage, no-frontage, wetland, rotated and partial-lidar sites),
each with real angles and with the tests' angle patch, on both commits.

**No blocking finding.** Verified by it: 216 of 216 prototype records and
sheets byte-identical; `stepped_grading` agrees with its own calculation
on 214 drawn plans and every candidate (to 3e-9 CY; cut equals fill); of
the 216 default runs 178 were identical but for the new keys and note,
33 differed only in `pond_room.tries`, 2 gained `SECOND_DOCKS_UPHILL`
and 3 were turned; no change of floor area, stalls, feasibility, status,
severity-1 or -2 flags, street or homes facing, or drainage outside the
turned plans; `DOCKS_UPHILL` exactly where the court was on the high
side; the same site twice and under two hash seeds identical; all 432
sheets pass the sanitiser; run time +1.5 to +4%.

## Findings and what was done

| # | Finding | Severity | Done |
|---|---|---|---|
| 1 | The docks step could put the loading on the street side of the building with nothing weighing it. On a 1600 x 1000 ft site (street on the low side, 2% rise to the rear) it turned the plan: 64,000 to 25,000 CY, the court and trailer rows between the building and the street (279 ft off it, so no `STREET_FACING_DOCKS`), the offices and 240 cars at the back, the car route 297 to 2,283 ft, light paving 99,000 to 145,000 SF | should fix (operator decision) | Two rules. A turn is not laid out when it would put a court between the building and the street where the plan had none there, at any distance (`loading_on_street_side`: the court's middle more than 40 ft nearer the street than the building's). A turn laid out is refused when the cars' way in grows beyond 1.5 times and 200 ft more. That site keeps its plan; its record says what was refused and the 40,000 CY it would have saved. **The operator's call** whether loading on the street side at that distance should be allowed for that saving: the conservative answer is built. |
| 2 | "Where the street is on the low side the docks cannot turn because no car route reaches the cars … an engineer would too" was not true as stated: with room at the sides such a site does turn, and on the tight sites named the turned layout fails for the planner's own limits (module fitted to the envelope, no drive round the building's end, only the same footprint tried) | should fix (docs) | `SITE_PLANNING.md`, the decision note and the test comment say it is the planner's limit and name the three causes. |
| 3 | Nothing protected a second building: the acceptance compared building 1 only. Not reproduced as shipped (no turned plan with a second building in 108 sites and about 170 scans); with the street pre-check removed, the suite's two-building site (543,000 SF) turned building 1 and lost the second | should fix | The second-building search is a function run for the turned plan and for the plan it came from; the turn stands only if it costs no second-building floor area (and, where the turned plan has none, only if the other search finished). Tested with the guards patched away: 543,000 SF kept, the record says why. |
| 4 | The step ran on the pond pass's allowance and wrote into its record: nothing tried once the 90 tries were spent, `tried: 0` with no reason, `pond_room.tries` +1 to 2, `drawn: True` with `found: 0` on a turned plan, `cut_short` set by the step | should fix | Its own two tries (`DOCK_TURN_TRIES`); the pass's record is the same with and without the step (tested). `inputs.dock_side` has an `outcome` (`turned`, `not_cheaper`, `refused`, `courts_on_both_sides`, `no_lidar`, `no_time`, `no_plan`) and each refusal with its reason and what it would have saved. `pond_room.drawn` means the plan (or the plan it was turned from) came from the pass. |
| 5 | 14 mutants of the step's conditions passed the planner's tests together | should fix | The conditions are three functions (`dock_turn_saves`, `dock_turn_blocked`, `dock_turn_refused`), each tested condition by condition: pass in full, pond drains, trailer stalls, severity-2 flags, car route, both thresholds, buildable area, street (100 ft), street side, homes. Also tested: trailer rows graded with the court, the area each sample stands for, partial lidar, courts on both sides, a rise under 3 ft not flagged, the alternatives' figure, the second building. `Grading` is 12 tests (4 before). The `saved_cy` assertion that held by luck allows the rounding. **Not covered:** the "1,000 CY or more" wording threshold of the note, and laying the turn out under the pass's pond rules (`ctx_room`) rather than the search's. |
| 6 | Two-court layouts read wrongly: a cross-dock's mean fall is about 0 on any plane ("court on level ground" on 30 ft of relief), the step's gate was then noise (one try a duplicate, one a car-field move that would have been recorded as a turn); "level" also covered a 2.9 ft rise | should fix | `court_side` is `both` for a building with courts on two sides, with each court's fall listed; no `DOCKS_UPHILL` and no turn for it (`courts_on_both_sides`). A rise or fall of 1 ft is a side; the flag stays at 3 ft. |
| 7 | Model limits not stated: module ground beside trailer rows graded at the floor (up to 9% of the module; up to 14% of the figure); volume by sample count, not module area (0.93 to 1.07); partial lidar gave small figures beside blanks; nothing outside the module; "40 ft" hard-coded | nit / should fix | Each sample stands for its share of the module's area, as the one-plane figure does. No figure under 80% sample cover (`STEPPED_MIN_COVER`). The basis text takes the grid step and lists what is left out; the docs list the limits. **Not changed:** the ground beside trailer rows stays at the floor (documented with the reviewer's figures). |
| 8 | The step's gate (`fall_to_court < 0`) was not "the other side is cheaper": #25's court is on the low side, yet the other side moves 13% less | nit | The gate is the comparison itself: each turn's stepped pad is figured first and only a material saving is laid out. #25's record now shows the turn refused (toward the street) and its 4,000 CY. |
| 9 | The `DOCKS_UPHILL` note and playbook implied the turn was available (on #21 it means docks on the street and homes); `SECOND_DOCKS_UPHILL` undocumented | nit | The note says the other side is "a layout not checked here", and for the plan drawn why it was not taken. The playbook no longer proposes the turn outright. `SECOND_DOCKS_UPHILL` is in the docs. |
| 10 | Sheet: footnotes still described one plane only; "PAD EL 911" beside "floor 913"; the narrower LAYOUT column wrapped most names; leading spaces collapsed; the turned plan and its source two identical rows; a duplicate candidate cost a try | nit | A "Stepped pad" footnote with its basis and what "PAD EL" is; the column is 300 px again (the others narrowed); the turned row reads "(court turned downhill)"; duplicates are skipped and the first turn that passes is taken (the office's end does not change the stepped earthwork). |
| 11 | `alternatives[].earthwork_cy` (stepped) shared its name with the one-plane `quantities.earthwork_cy` and the pipeline column | nit | Renamed `stepped_cy`. |
| 12 | The decision note said 43 prototype records; 23 sites x 2 is 46 | nit | 46. |

## For the operator

- **Loading on the street side of the building** (finding 1). Built as
  "never newly". On the review's site that leaves 40,000 CY on the
  table; the record names it. Say so if a court more than some distance
  off the street should be allowed there.
- **The drainage check still reads the one-plane pad**, which the sheet
  now shows beside a stepped floor about 2 ft above it and a court about
  2 ft below (finding 10). Measuring to the court tightens the check of
  `reviews/41` by as much. Unchanged; an operator decision.
- **Earthwork is not in the ranking.** Unchanged.

## After the fixes

- The 23 sites x 2 angle modes against the code reviewed: all 46
  prototype records byte-identical; on the 46 default records no change
  of floor area, trailer stalls, feasibility or any flag. The
  differences are the stepped figures (area scaling, within about 7%),
  the reworded note, `court_side` on the cross-dock (`both`), the dock
  step's record, and `pond_room.tries` back to the pass's own count.
  The wetland site still turns (11,000 CY less), now in one try.
- Run times unchanged (#21 24 s, others under 21 s).
- Full suite: SUITE_RESULT

The fixes were not sent to a second independent review: they narrow the
step (more refusals, never a new acceptance), the model's arithmetic is
changed only in its scaling, and the prototype is byte-identical.
