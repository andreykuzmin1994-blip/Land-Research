# 44 — Review of the land-use change

**This change was merged to `main` (PR #51, `c6b8c68`) before its review
reported**: the operator asked for the merge while the review was
running. The review then found two blocking problems in the merged code.
They, and its other findings, are fixed on
`claude/festive-archimedes-eyjbo6` after the merge; those fixes are not
on `main` until a further pull request is merged.

The Tier 1 reviewer (fresh context, given the code and the requirements,
not the author's conclusions) reviewed `177973d..2766da0`. It ran 139
sites (the 23 of the earlier reviews and 116 of its own: sites of 20 to
100 acres, deep and corner lots, irregular and rotated parcels, water and
transmission lines, homes on one or more sides, slopes each way, partial
and no lidar, no frontage, tight lots), in four modes on both commits:
1,112 plans.

Verified by it and holding: prototype records and sheets byte-identical
on 138 sites in both angle modes; on the default behaviour no loss of
feasibility, of building 1's or of total floor area, no new severity-1
flag, no new plan-check failure and no layout newly facing the street or
homes; 40 of 138 sites gain (total floor area +11.7%; median floor-area
ratio 24% to 27.5%); the storage of all 42 shaped ponds it drew matches
its own integration to 0.01 ac-ft; identical records under three hash
seeds; no crash with the truck design failing or raising.

## Findings and what was done

| # | Finding | Severity | Done |
|---|---|---|---|
| 1 | A shaped pond ignored the rectangle's rule to stay on the layout's side of mapped water. On a 750 x 850 ft lot with a wetland band, the plan went from 114,000 to 192,000 SF with its pond across the band from the building, recorded as draining and "on its side of mapped wetlands". The whole gain was the bypass | blocking | `free_pond` takes the layout and the mapped water and refuses a pond across it, as `place_sized_pond` does. The two sites plan 114,000 and 133,000 SF, the figures of the reviewer's own corrected check. Tested at unit level and on the site. |
| 2 | A second building's cars ran through building 1's dock positions (in 16 of 29 two-building plans, over 50 ft inside the 60 ft dock strip, up to 6 ft from the dock wall), their pavement overlapped building 1 on two sites, the sheet painted the court over the drive so it seemed to start in the middle of the court, the record had no car route length, and the flag's "the end of its truck court" was untrue (65 to 1,706 ft inside the court) | blocking | Building 1's shared ground excludes its dock strip, for trucks and cars, and cars keep 20 ft off its building. The sheet draws the shared drive over building 1's pavement. `second_building.car_route_ft` is recorded. The flag gives the distances its trucks and cars run over building 1's pavement, and `SECOND_ACCESS_SHARED` (whose text did not describe this) is no longer raised for it. Where open ground leads there, cars come by their own drive from building 1's car driveway. Tested: no route in the dock strip, no pavement on either building. |
| 3 | A through-first second building was accepted on almost any terms: dead ends of 955 to 3,447 ft against the 750 ft limit, as a condition only; kept with no drivable path when one was forced away | should fix | A candidate is taken only with a path a WB-67 can drive. **Not changed:** the dead end stays a condition (`SECOND_DEAD_END_ACCESS`), now said plainly in the docs as the usual case; a second building may be larger than the first. A drive of its own for the rear building, at the cost of some of building 1's length, is the real answer and is not built. |
| 4 | Building 2's pond, yard and impervious area were sized round the grid route from the street, not the path drawn (4,055 against 630 ft on one site; the drawn line 11 ft from its own pond) | should fix | The second building's truck path is laid out inside `finish`, before its pond, as building 1's is; building 1's pavement is no longer counted in building 2's impervious area. |
| 5 | The land pass had no guard on new conditions: #21 with the angle patch went from 84,000 to 91,000 SF with its parking cut from 85 to 48 of 92 by a truck road through the car field; 12 of 138 sites lost all trailer stalls, one for 1,000 SF | should fix | The pass does not take a layout whose truck road leaves parking short, cannot be driven or has no stacking room (`LAND_NOT_FOR`), nor one with fewer trailer stalls for under 5% more floor area (`LAND_MIN_GAIN`). #21 with the angle patch now gains 2,000 SF with no new condition; the tight 560 x 600 lot keeps 70,000 SF and its 7 stalls. Tested. |
| 6 | `inputs.land_pass.drawn` was false for a plan of the pass whose docks were then turned | should fix | Fixed (as the pond pass does). Tested on the reviewer's site. |
| 7 | Docs untrue as stated: "the pond is not wrapped round a building" (a boomerang pond, and L, U and ring ponds at unit level); "the end of its truck court"; "never more" in the storage docstring (strips 62 to 86 ft wide read up to 3.2% high); "6 ft of water" for a strip 3.9 ft deep; "push the building to its setback lines" neither done nor listed | should fix | A shaped pond is one piece with no island (rings refused); the docs say it may be a strip or bend round a corner and that nothing checks maintenance access. Docstring and flag reworded. The unbuilt items are listed. |
| 8 | Tests: 24 of 38 mutants survived class `Land` | should fix | Added: the water rule, the dock strip and pavements for a second building, no island, a smaller piece that holds the volume, the pass's guards, `drawn` after a turn, building 1 unchanged and nothing turned to the street on the two-building site. **Still not covered:** the land mode kept through the tier, dock and sight steps; `LAND_KEEP`; shaped ponds with partial lidar inside a plan. |
| 9 | Nits: a `break` that assumed no smaller piece could hold more; plans found before a budget cut dropped; the cut "from its lower end" compares only the two ends | nit | `continue`; found plans are kept; the docs say "the end whose ground is lower". |

## For the operator

- **A second building through the first is a long dead end** with trucks
  and cars sharing a drive through another building's court. It is a
  condition on the sheet, not a clean plan. A real site would give the
  rear building a drive of its own along one side.
- **Trailer stalls.** With the 5% rule, two review sites still lose all
  their stalls for 35 to 40% more floor area. That is the ranking's own
  rule; say so if stalls should weigh.
- **Shaped ponds** can be long strips. Their storage is right; whether
  one can be built and maintained is the civil engineer's call.

## After the fixes (`bdae2f3` and later)

- The 23 sites x 2 angle modes against the state of `reviews/43`: all 46
  prototype records byte-identical. On the 46 default records: 14 gain
  floor area, 32 are unchanged, none loses any; no change of
  feasibility, no new severity-1 flag, no plan-check failure.

| Site (real angles) | Before | After | How |
|---|---|---|---|
| 1800 x 1900 (78 ac) | 520,000/136; 0.15 | **948,000**/136; 0.28 | a second building (428,000 SF) through the first |
| 1200 x 1200 (33 ac) | 335,000/67; 0.23 | **450,000**/67; 0.31 | a second building (115,000 SF) through the first |
| homes at the rear, 700 x 900 | 151,000/15; 0.24 | **211,000**/0; 0.33 | a pond shaped to the site; loses its trailer stalls |
| assemblage, 900 x 700 | 179,000/18; 0.28 | **242,000**/0; 0.38 | a pond shaped to the site; loses its trailer stalls |
| Fairburn 650 x 750 | 114,000/0; 0.23 | **135,000**/28; 0.28 | a pond shaped to the site; gains its trailer stalls |
| flat 520 x 640 | 65,000/7; 0.19 | **76,000**/8; 0.23 | a pond shaped to the site |
| flat 600 x 700 | 110,000/11; 0.26 | **125,000**/13; 0.30 | a pond shaped to the site; its S-bend entrance and parking shortfall go |

  (floor area / trailer stalls; floor-area ratio). Reference sites #21
  and #25 are unchanged at real angles (91,000 and 70,000 SF).
- Run time: 46 default plans 251 s before, 362 s after (+44%); most of it
  on the two-building sites (10 to 20 s and 9 to 24 s).
- Full suite (`python -m unittest discover -s screener/tests -t .`):
  1,051 tests OK in 1,093 s on the fixes (1,047 OK in 1,144 s on the
  merged code; 1,040 in 683 s before this change).
- CI: the `offline-tests` job's limit is raised from 5 to 45 minutes
  (`.github/workflows/offline-tests.yml`), on the operator's instruction
  of 2026-11-20. It had cancelled every run since #50 (`reviews/41`,
  "Open").

The fixes were not sent to a second independent review.
