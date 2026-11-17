# 38 / 03 — Three operator decisions applied to the planner

Decided by the operator in chat on 2026-11-17, in answer to the open
items of `01_decision_note.md`:

1. Underground detention: "don't do underground in a cheap market like
   Atlanta, but in expensive markets like infill Chicago or NJ, yes."
2. Should a missing trailer stall count as 2,000 SF in the ranking? "No."
3. Which of the looser offsets to tighten? "All of them."

**These change what the planner draws in its default behaviour.**
`--prototype-behaviour` is untouched: its records are identical to the
code before this session on 8 synthetic sites, and the two parity
fixtures still pass.

## What changed

| Decision | In the code | Effect |
|---|---|---|
| 1 | `UNDERGROUND_DETENTION = False`; `--underground-detention`; recorded in every plan | The read-out no longer offers a vault. When the pond holds the building it says so and that the plan drawn stands. The pond flag's note and action say "a smaller building or detention off site". With the switch on (a dear market) it says to price a vault. The layout is not affected either way: surface ponds only. |
| 2 | `RANKING["trailer_sf"] = 0` (`PROTOTYPE_RANKING` keeps 2,000) | The largest layout that passes its checks is drawn. A trailer shortfall is flagged (`TRAILER_SHORT`, severity 2) and only settles a tie. The read-out names the largest layout that meets its trailer target (`selection.with_trailers`, `meets_trailer_target_sf` in `plans.csv`). |
| 3 | `JUR_TIGHTENED`, `OPEN_YARD_RULE_FT = 60`, two edge kinds (`OTHER_REAR`, `COMM`), two flags (`CORRIDOR_OVERLAY`, `USE_RESTRICTION_REPORTED`) | See `SITE_PLANNING.md` § "Tightened draft values". |

Two changes to the search came out of the review of these decisions
(`04_review_of_decisions.md`), both in the default behaviour only:

- **A layout is turned round before it is given up.** With homes behind
  a site every layout failed on its car route: the first turn keeps the
  courts away from homes, which put the court on the street and the cars
  at the back. Decision 3 made farms count as homes in three
  jurisdictions, so the failure would have spread. The planner now tries
  the other ways round the same fit.
- **A draft code minimum of truck spaces is met before floor area**,
  among layouts that pass. Only Fairburn has one. It is a code minimum,
  not the market target that decision 2 took out of the ranking; say so
  if you read decision 2 as covering it, and it comes out with one line.

What decision 3 covers, item by item (the "looser" rows of
`02_draft_offsets_check.md`):

- Fairburn rear yard 30 ft; East Point rear yard 30 ft.
- Fairburn truck spaces, 1 per 5,000 SF, as a floor under the trailer
  target, with a row in the check.
- Fairburn's 50 ft buffer and East Point's 50 ft strip, drawn toward
  commercial and institutional neighbours; Palmetto's 20 ft.
- Atlanta: 31 ft from a residential line (the height plane).
- South Fulton, unincorporated Fulton, Union City: agricultural
  neighbours trigger the residential buffer.
- East Point B-P: 45 ft front yard.
- Palmetto corridor overlay: 95 ft and 35 ft on SR 14 and SR 154;
  flagged on every other Palmetto frontage.
- College Park: the reported moratorium, flagged.
- Everywhere: the building 60 ft from every lot line that is not a road,
  cut as one band so an inside corner of the parcel is held too. The
  zoning table keeps the codes' own numbers; the yard has its own row.

Not tightened, and why:

- **The aerial fire lane's distance from the wall** (`02`, section C.2).
  The code does not say which edge of the road is measured; it is a
  question for the fire marshal, not a value that reads looser.
- **Fairburn's side yard** stays at the draft 25 ft. The code says 20;
  loosening was not asked.
- **South Fulton's extra setback over 50 ft of height.** The planner's
  buildings are 42 to 46 ft.

## What it rests on

The tightened values are draft. They come from the repository's zoning
facts (a third-party mirror, read before the operator barred it) and
from the web research of `02` section B: a 2013 text for Palmetto, a
2019 mirror for East Point, nothing dimensional for College Park.
Neighbours are told apart by county tax class, a stand-in for zoning.

## Effect on the plans that can be run here

| Site | Before these decisions | After |
|---|---|---|
| Reference #21 (Atlanta, 8.7 ac) | 58,000 SF, 6 of 6 trailer stalls | 68,000 SF, 0 of 7 (`TRAILER_SHORT`); the 58,000 SF layout is named as the one that meets the target |
| Reference #25 (South Fulton, 9.4 ac) | 67,000 SF, 7 of 7 | 67,000 SF, 7 of 7 (the 77,000 SF court-only layout no longer passes with the 60 ft yard) |
| 600 x 700 ft rectangle, South Fulton | 106,000 SF | 80,000 SF (the open yard) |
| 600 x 700 ft, East Point I-1 (gated) | 106,000 SF | 109,000 SF |
| 900 x 800 ft, Fairburn | 211,000 SF | 211,000 SF |
| 800 x 900 ft, College Park | 205,000 SF, blocked by the height cap | 200,000 SF, blocked |
| 1300 x 1500 ft, South Fulton | 372,000 SF | 415,000 SF (a larger layout passes once turned round) |
| 900 x 900 ft, South Fulton, a farm behind | 242,000 SF, no buffer | 242,000 SF, 110 ft from the farm's line |
| 900 x 900 ft, South Fulton, homes behind | not feasible (`CAR_ROUTE_NONE`) | 242,000 SF |

The open yard is the change that costs floor area on tight sites; the
trailer decision is the one that adds it.

## What was verified

- Offline suite: 957 tests, OK. New tests cover each decision and each
  review finding (`TightenedRules`, `Selection`, `ReferenceSite` in
  `test_siteplan_assess.py`). Two existing tests were re-pointed, not
  weakened (the reviewer checked): the drop-yard test uses a site whose
  plan has a drop yard under the new ranking, and the two-piece parcel
  is compared with its own piece on the same road side.
- The prototype behaviour: 0 differences from the original code on 8
  synthetic sites, every original key; the reviewer found the same on
  22 sites for every key outside the read-out.
- Two independent reviews of this change: `04_review_of_decisions.md`.

## What was NOT verified

**The 33-site replay of `reviews/37`.** Its cached inputs are not in the
cloud container, so the effect on the real reference set is known for
two sites only. Before relying on a pipeline total, re-run the planner
on the reference set and read the deltas, as review 37 did. Plans made
before this change cannot be merged with new ones (`--merge` refuses:
the code and the options differ), which is the intended behaviour.

After a merge a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline`.
