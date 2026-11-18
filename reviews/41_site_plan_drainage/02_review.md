# 41 — Review of the drainage-first change

The Tier 1 reviewer (fresh context, given the code and the requirements,
not the author's conclusions) reviewed `780e32c..4c86071`. It ran 44 sites
(the 23 of the earlier reviews and 21 of its own: partial and no lidar,
valley, ridge, knoll, pit, a 12 ft step, rear-low, homes on the low side,
a rotated parcel, an elevation over 1,000 ft), each with real angles and
with the tests' angle patch, on both commits.

**No blocking finding.** Verified by it: 88 of 88 prototype records
byte-identical; no change of feasibility or status, no new severity-1
flag and no new `STREET_FACING_DOCKS` on any default record; with the
pass off, the only differences from `780e32c` are the ranking tier, the
flag and the read-out filter; the figure and the flag agree with its own
measurement on all 86 drawn plans; flat sites read 0 and are not flagged;
determinism under two hash seeds; all 176 sheets pass the sanitiser; the
90 tries are not binding (unlimited tries give the same records).

## Findings and what was done (`8ff329e`)

| # | Finding | Severity | Done |
|---|---|---|---|
| 1 | Slope 800 x 900: the pass drew a 211,000 SF plan with 0 stalls over its own equal 211,000 SF plan with 21, on 2.4 ft of relief; `POND_ROOM_KEEP` then dropped the 21-stall plan and the read-out misstated what the stalls cost | should fix | A pass plan gives way to another pass plan with at least its floor area and more stalls; each starting point also returns its best plan that meets its trailer target, and one such plan is always kept. The site plans 211,000/21 with the pond 16 ft below the pad. |
| 2 | `test_no_stalls_traded_for_nothing` had been loosened and no longer saw that trade | should fix | It pins the plan without the pass (205,000/21) and with it (211,000/21). |
| 3 | The prototype sheet gained the "PAD EL" line (84 of 88 prototype sheets differed) | should fix | Printed only on the default behaviour's sheet; tested. |
| 4 | Inside the 3 ft tolerance nothing looks for a lower pond (1200 x 1200: pond 2.6 ft above the pad where 12 ft below was possible); "inside its own ±30%" understated what a deeper pond takes (15–31%, 26–54% with inlet fall) | should fix | Partly. Each pond is now first looked for on ground 3 ft or more below the pad; a pond 1 ft or more above it says `POND_ABOVE_PAD` (information, with a playbook entry); the docs give the reviewer's footprint figures. **Not fixed:** a layout inside the tolerance is not laid out again (the 1200 x 1200 case stands, now flagged); tightening the tolerance to zero cost one site 30% of its floor area in the reviewer's run. An operator decision if wanted. |
| 5 | `drains_by_gravity` read True with no lidar, with the pond on unsampled ground, and for a blocked plan with no pond; under a ceiling the pond search used invented ground | should fix | None where a pond was not measured; no pond on invented ground under a ceiling. Tested. |
| 6 | A second building's uphill pond was flagged but the record's summary described building 1 only | should fix | `ponds_over_pad_ft` lists every pond (building 1's, then the second's, each against its own pad); the highest and `drains_by_gravity` cover both. The second building's choice and the pass still do not consider drainage: documented. |
| 7 | "An upper bound" is false: 119 of 2,970 fits round a pond were longer than the open-parcel fit (#21's 480 ft among them) | nit | Reworded as a rule of thumb that saves tries (removing the stop under 90 tries gave worse plans on 7 runs, the reviewer found). |
| 8 | Every pond carried the highest pond's figure | nit | Each pond its own. |
| 9 | The tier also reordered failed layouts | nit | Only among layouts that pass. |
| 10 | `inputs.pond_room.layouts` counted each starting layout once per round | nit | Once. Tested. |
| 11 | A thousands comma in "PAD EL"; the pond line allowed on an 80 px pond | nit | No comma; 115 px. |
| 12 | Stale docs (#21 "84,000", "#25 keeps its plan"); flags missing from the note's table; the yield note gave no reason | nit | Fixed. |
| 13 | Two mutants passed the suite (the second round removed; the pass's pond ceiling removed); no test of a second building, several ponds, the prototype sheet | nit | Tests for the second round, the ceiling, unmeasured ponds, the prototype sheet and the record over several ponds and a second building. |

Things the reviewer recorded for the operator, by design:

- **The tier outranks dock orientation.** On a site with homes on the low
  side, the only layout whose pond drains puts loading within 150 ft of
  the homes (242,000 SF with the pond 13 ft above the pad became 223,000
  SF that drains, with a new `LOADING_FACES_RES`).
- **With the pass off** (a site too slow for it, or cut), the tier alone
  costs up to 13% of floor area where a draining layout exists only
  smaller, and can turn docks to the street. With the pass, at most 8%
  (the L-shape), and every larger layout passed over had its pond 9 ft
  or more above the pad.
- Run time: +19% over the default runs; the L-shape 2 s to 20 s.

## After the fixes

- The 23 sites in four modes again: prototype records identical; the
  table in `01_decision_note.md` is of this state.
- Full suite and the reviewer's re-check: below.
