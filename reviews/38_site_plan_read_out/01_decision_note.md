# 38 — Screener site plan read-out and test-fit sheet: decision note (Tier 1)

**Decision: committed on `claude/stoic-keller-r95w17`, not merged.** After a
merge a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline` on the
merge commit. The score should not change: the evaluator imports none of
these modules.

Asked by the operator on 2026-11-17: the planner's output and planning
read as thin; make them more actionable and institutional. Then, in the
same session: were the offsets checked against the codes, and make the
plan itself clearer to read.

## Tier

Tier 1 (one independent reviewer). Screener-side human edit; no evaluator,
metric, credential or new external service. The layout search, the fetch
layer (`siteplan_inputs.py`) and the eval files are untouched:
`git diff e50e830 -- screener/rank.py screener/scoring.py screener/screens.py
screener/settings.toml screener/autoresearch screener/siteplan_inputs.py`
is empty. The reviewer ran as a fresh-context subagent that did not write
the code and was given the code, not the author's conclusions.

## What changed

- `screener/siteplan_assess.py` (new, pure): status, conditions with a
  playbook (workstream, stage, action, what closes it), next steps, land
  budget, the plan against its draft zoning values, the building-code
  open yard, a takeoff, why this layout, the pipeline table.
- `screener/siteplan.py`: calls it after a plan is finished. Named
  constants for numbers that were literals (`RANKING`,
  `COMP_BAND_SF_PER_AC`, `ROOF_ALLOWANCE_FT`, `CLEAR_BELOW_MARKET_FT`),
  same values. `finish()` returns the pad elevation, its deepest cut and
  fill and the disturbed area. `explore()` remembers layouts that passed
  every check but the pond, for the read-out only. `plans.csv`.
  `SCHEMA_VERSION` 2.
- `screener/siteplan_render.py`: the drawing is a sheet. The plan is drawn
  the way published industrial site plans are (see "The plan's look").
- `screener/viewer.py`, `viewer_template.html`: the panel beside a plan
  shows the read-out; a plan made before it existed reads as before.
- Tests: `test_siteplan_assess.py` (43 tests); the static guards in
  `test_siteplan.py` now cover the new module.

## What was verified

| Check | Result |
|---|---|
| Offline suite | `python -m unittest discover -s screener/tests -t .`: 941 tests, OK (898 before). |
| No planner behaviour change | Records from the code before this change and after it, compared on every key the old record had: 2 reference sites (default behaviour) and 16 synthetic records (8 sites, both behaviours). 0 differences. The reviewer repeated it on 23 synthetic sites in both behaviours: identical. The prototype-parity fixtures still pass. |
| Sheet is safe | Same element allow-list as before (no `g`, no `tspan`), every text through `plan._esc`, passes `viewer.sanitise_svg`, same bytes on a second render. One more in-file hatch pattern (`wx`). |
| Viewer | Built from the two reference sites and opened in headless Chrome: no script error; the new sort and filter work. Every record string is escaped; one script; CSP hash unchanged in kind. |
| `plans.csv` | Formula guard on every cell, parcel ids and numbers only, owner guard before any write. |

## Review findings and what was done

The reviewer confirmed the search is unchanged and reported eight
findings. All were verified by it except where marked "read".

| # | Finding | Done |
|---|---|---|
| 1 (high) | A feasible plan with a residential rear edge read "short" on its own setback: plain distance was measured to the end of an edge that is read 20 ft past the corner. | Setbacks are measured square to each stretch of the lot line and to its bends, not past its ends (`band_clearance`). Test with a residential rear edge. |
| 2 | Under `--prototype-behaviour` the read-out contradicted the record (a "blocking" condition on a counted plan; "0 blocking items"). | A condition blocks only when the record says the plan is not feasible. Tests for both cases. |
| 3 | A yield note asserted a cause ("the parcel's shape limits the building") it had not established, and named 0.0 ac losses. | The note states the unused acres only; losses under 0.05 ac are not named; nothing about other layouts on a plan that is not a result. |
| 4 | "Run it again" was the only step for two flags a rerun may not clear. | `FRONTAGE_GEOMETRY` and `DEM_PARTIAL` have their own entries. |
| 5 (read) | Playbook lines stated as fact (curb length, "homes", what a zoning verification letter proves, grading, pond accuracy), a wrong stage, a CLI option that does not exist. | Reworded; `FIRE_SECOND_ACCESS` moved before an offer; `ACCESS_NONE` starts with a desk check of the road map; the same-jurisdiction `ASSEMBLAGE_MIXED_RULES` has its own action. |
| 6 | A plan cut by the time budget read "blocked"; "passed every check but the pond" was said of layouts that carry a site-level block. | A drawn plan with `PLAN_BUDGET_EXCEEDED` reads incomplete; the wording is "every layout check"; a no-fit with an unread input says the no-fit stands and why. |
| 7 | Two buildings: parking and height rows covered building 1 without saying so; disturbed area could exceed the net area. | Rows are labelled; the sum is capped at the net area; building 2's car count uses building 1's rule. |
| 8 | A literal 0.6 left in `prelim()`; long titles cut without a mark; the read-out could abort a batch. | Uses `RANKING`; a cut title ends in "..."; a failure in the read-out is caught, recorded in the record (`read_out`) and the plan is still written. |

Tests the reviewer asked for and now present: compliance with a
residential edge; `upside` and `without_pond` produced by `plan_site` on a
reference site; the read-out under the prototype behaviour; a real
two-building plan (the earlier test never produced one).

Not done: a test that runs the viewer's JavaScript (the suite has no
browser; it was checked by hand in headless Chrome, above).

## The plan's look

The operator asked for more contrast and pointed at published plans. Four
were read (a Majestic Realty site plan, a Panattoni project sheet, a Hartz
Mountain building sheet, a CBRE marketing site plan). What they share, and
what the sheet now does:

- open ground green; the building one warm solid with a heavy outline and
  its name and floor area in bold; the office a lighter block;
- truck paving mid grey, car paving a lighter blue-grey, both striped in
  white; trailer stalls dark;
- water blue (the pond), with a grassed rim; mapped flood and wetland a
  blue hatch so it cannot be taken for the pond;
- the street a dark band with its name in white capitals; the property
  line heavy, long dash and two short;
- dimension strings with arrowheads for the building's length and depth
  and the truck court's depth; counts written on the fields ("70 CAR
  STALLS", "13 DOCK DOORS").

## Open items for the operator

1. **Underground detention.** On reference site #21 the plan drawn is
   58,000 SF; a 96,000 SF layout passed every layout check but the surface
   pond. `plans.csv` now carries that number for every site
   (`without_pond_sf`). Operator decision 7 in `SITE_PLANNING.md`.
2. **The trailer mark in the ranking.** 2,000 SF per missing stall decides
   which plan is drawn on both reference sites. Operator decision 9 (new).
3. **The open yard at the building.** On tight sites the planner's
   layouts leave 41 to 45 ft between the building and a lot line (a 31 ft
   fire lane plus the 10 ft strip). The building code's unlimited-area
   rule wants 60 ft, or 40 ft with 3-hour walls. The sheet now shows the
   row; the layout is unchanged. See `02_draft_offsets_check.md`.
4. **The draft offsets against the codes.** `02_draft_offsets_check.md`.
   No value in `JUR_DRAFT` was changed.
5. **The search itself.** Unchanged by this work. What the read-out shows
   about it: 33 to 41% of the net area is left unused on both reference
   sites, and the layout is one rectangle module per building. Improving
   that changes yields and needs the 33-site replay of `reviews/37`, whose
   cached inputs are not in this container.
