# 40 — Review of the room-for-the-pond search, and the fix

The Tier 1 reviewer (fresh context, given the code and the requirements,
not the author's conclusions) reviewed `bcfc833..abdf6fd`, the code merged
in PR #50 before the review reported (`01_decision_note.md`). Its findings
and what was done, on `claude/festive-archimedes-eyjbo6` restarted from
`main` (`3868c03`).

## The fix in one line

The first version changed the search itself (a pond-blocked layout was
moved inside `explore`, and other pond shapes were tried in every
`finish`). The search is now exactly the code before the change
(`git diff bcfc833 -- screener/siteplan.py`, the section from `VARIANTS =
variants()` to the rescue pass: no difference), and the room-for-the-pond
work is a pass after it that only adds layouts. The plan drawn is the
best of everything by the unchanged ranking.

## Findings and what was done

| # | Finding | Severity | Done |
|---|---|---|---|
| 1 | A moved or other-shape plan found in the first pass switched off the rescue pass, so larger plans were lost: assemblage 179,000/18 stalls -> 157,000/0; 520 x 640 76,000/8 -> 68,000/0; Fairburn 650 x 750 120,000/24 -> 115,000/23 | blocking | The pass runs after the search and its rescue and only adds layouts. All three now plan as before (table below). New test `PondRoomOnlyAdds` on those sites. |
| 2 | 2.9–4.9x run time on pond-limited sites; the 48-check cap did not count the refits; a site near its 900 s budget would be blocked | blocking | The allowance (60) counts every fit and every full check. The pass has its own deadline: half of the time left after the search, so the second building keeps the rest. It does not run when under half the budget is left, so a slow site plans exactly as before. A cut keeps what it found and is not flagged. Tests `test_a_slow_site_plans_as_before`, `test_a_cut_pass_is_not_a_flag`. Times below. |
| 3 | More drawn plans had a truck court within 100 ft of the street; the docs claimed a dock rule the planner does not have | should fix | A layout the pass adds is refused when it faces the street and the best plan before it did not. No site gains `STREET_FACING_DOCKS` (table). Docs corrected: `orient_score` chooses among the four flips only; #25 keeps its plan because its only larger layout faces the arterial and is refused. |
| 4 | The pond-shape fallback traded trailer stalls for floor area, undocumented | should fix | Follows the operator's ranking (no floor area for a trailer stall, 2026-11-17). Now in both docs. Stalls traded for no floor area at all: re-check finding 1, below. |
| 5 | `fine=True` searched only near the ~78 lowest samples | should fix | It now tries nine positions round each of the 700 lowest; and the other proportions are tried at the samples first, the finer positions after. |
| 6 | A budget cut inside `relocate_for_pond` threw away the layout and any plan found | should fix | Each plan found is appended as it is found; the cut is caught round the whole pass. |
| 7 | 1:1 ponds; a design manual's minimum length-to-width not checked | should fix | 1:1 dropped; not below 1.5:1. The manual's minimum is noted as not checked. |
| 8 | Only one pond is reserved when there are two or more | nit | Left; noted in the code. |
| 9 | The `floor` stop and the one-move-per-layout rule ignored parts of the ranking | nit | Gone with the move out of `explore`. |
| 10 | `moved` counted calls, not the drawn plan; repeated shapes in the basis; `id()` keys; a garbled test comment | nit | `inputs.pond_room.drawn` says whether the plan drawn came from the pass; shapes listed once; ponds carry their shape; comment left (the test is valid). |

## The reviewer's 23 sites again (default behaviour; floor area / trailer stalls)

Before = `bcfc833` (the reviewer's saved records), after = this fix.
`*_ang` = with the tests' two-angle patch. Prototype records: all 43
byte-identical. No site smaller, less feasible, with a new severity-1
flag or with a new `STREET_FACING_DOCKS`.

| Site | Before | After | Time |
|---|---|---|---|
| rank21 | 68,000/0 | **84,000/0** | 6.3 -> 21.5 s |
| rank25 | 67,000/7 | 67,000/7 | 2.0 -> 3.7 s |
| assem (real / ang) | 179,000/18 | 179,000/18 | 5.7 -> 8.9 s |
| 520 x 640 ang | 76,000/8 | 76,000/8 | 3.5 -> 1.9 s |
| fairburn 650 x 750 ang | 120,000/24 | 120,000/24 | 1.7 -> 2.3 s |
| 600 x 700 ang | 103,000/0 | **109,000/11** | 4.2 -> 3.2 s |
| homes beside 900 | 236,000/24 | **242,000/24** | 2.7 -> 2.5 s |
| homes behind 700 x 900 | 151,000/0 | 151,000/**15** | 2.4 -> 2.5 s |
| low front 700 x 800 ang | 147,000/15 | **151,000**/0 | 2.0 -> 3.6 s |
| slope 1000 x 600 | 160,000/0 | **173,000/18** | 6.0 -> 9.0 s |
| slope 800 x 900 | 211,000/21 | 211,000/21 (after the re-check fix) | 2.9 -> 6.0 s |
| slope 800 x 900 ang | 200,000/0 | **205,000/0** | 1.5 -> 1.5 s |
| steep 800 x 800 | 198,000/0 | **211,000/0** | 2.7 -> 2.9 s |
| 1800 x 1900 with homes | 428,000/86 | 428,000/86 | 6.1 -> 6.5 s |
| the 30 other runs | — | the same plan | within 1.5 s |

Run time: at most 21.5 s (#21, which spends all 60 tries); most sites
under 2x.

## Re-check (same reviewer, `3868c03..b6a804b`)

No blocking finding. Original findings 1, 2, 3, 5, 6, 7, 8, 9 and 10
fixed and verified: with the pass switched off every default record
equals `bcfc833` byte for byte (42 runs); the three regressions are back
to their old plans; prototype records identical (42 runs); determinism
under two hash seeds; the pass cannot cause `PLAN_BUDGET_EXCEEDED`; the
drawn plan never ranks below the plan without the pass.

| # | Finding | Severity | Done (`03de152`) |
|---|---|---|---|
| 1 | slope 800 x 900: 211,000/21 -> 211,000/0, won on a 1 ft relief difference among near-identical moved plans; the comment "only adds floor area" was false | should fix | A plan the pass adds needs more floor area than the best plan before it, or the same with as many trailer stalls. The site plans 211,000/21 again. Test `test_no_stalls_traded_for_nothing`. |
| 2 | The pass's time limits are wall-clock: a site near them can plan differently on a slower machine, unflagged; the docs said "the same site gives the same plan" | should fix | Docs say so; `inputs.pond_room.cut_short` / `skipped_for_time` record it. |
| 3 | The second building gets the time left after the pass | nit | Documented. |
| 4 | No guard against loading newly facing homes | nit | Refused like street-facing docks (`LOADING_FACES_RES` test, 150 ft). |
| 5 | The pass's fits not counted; near-duplicate alternatives | nit | Counted in `inputs.fit_searches`; the duplicates left (the layouts compared show the best few, which may be one variant at nearby positions). |

After these: the reviewer's 23 sites in four modes again (no default row
smaller, less feasible or newly flagged; prototype identical); the full
suite 982 tests OK in 457 s on a quiet machine (about 273 s before
PR #50: the default-behaviour tests now run the pass).
