# 40 — Screener site planner: room for the pond before a shorter building (Tier 1)

**Decision: merged to `main` at the operator's instruction (2026-11-17)
while the Tier 1 review was still running.** The reviewer's findings, and
what is done about them, follow in `02_review.md` on a later branch. After
the merge a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline` on the
merge commit, as for every screener change. The score should not change:
the evaluator imports none of the planner's modules.

Asked by the operator on 2026-11-17, looking at the redrawn test-fit
sheets: "Are you sure you are orienting these properly? Maximizing
footprint?" Then, shown the evidence below and the proposed fix: "Yes".

## Tier

Tier 1 (one independent reviewer), as `reviews/38` and `39`: a
screener-side change to the planner's layout search, default behaviour
only. No evaluator, metric, credential or new external service; no new
request. `--prototype-behaviour` records are byte-identical (below).

## What was wrong

The angle search was sound: every 2° plus the parcel's long edges, docks
turned away from the street and homes. The placement was not. Each
layout variant was fitted at the longest length wherever it fits longest
(`fit_variant`: one placement per length), and its pond and routes were
checked only after (`finish`). When the pond had no room, the building
was shortened 40 ft where it stood; it was never moved to leave the
pond room. The pond itself was a 2:1 rectangle centred on the 40 ft
lidar samples at six angles.

An exhaustive check (every 3° x a 20 ft position grid x 4 flips, each
through the planner's own `finish`, then re-planned through `plan_site`)
found:

| Site | Drawn before | Largest passing layout | Note |
|---|---|---|---|
| #21 | 68,000 SF | 84,000 SF, same cross-section, building moved south, pond on the open north ground | 37% of the paving envelope had been left unused |
| #25 | 67,000 SF, 7/7 trailer stalls | 72,000 SF | only with the truck court on the arterial and 0 trailer stalls: the planner's plan is the right one |

## What changed (`screener/siteplan.py`, default behaviour only)

- `relocate_for_pond`: a layout blocked by its pond alone is moved
  before it is shortened. The 6 lowest open spots a pond could use
  (`pond_spots`) are reserved in turn; the same cross-section is fitted
  around each, no longer than before, with the planner's own fit; the
  reserved pond is re-sized for the building that fits and fitted again
  (up to 3 rounds); each fit is checked in full. The largest that passes
  is kept, and the shorter lengths where the building stood are still
  tried down to it. At most 48 checks per site (`RELOCATE_MAX_CHECKS`, a
  count, so the same site gives the same plan);
  `inputs.pond_relocation` records them. The dock-orientation rule is
  unchanged.
- Pond fallback: where no 2:1 pond fits at the samples, the same volume
  is tried 20 ft off them, then with bottom proportions 1.5, 1, 3 and 4
  (`POND_FALLBACK_RATIOS`); the detention basis names the proportions
  used. Skipped for a layout a route already fails; the finer positions
  stay within the usual 700.
- `pond_dims(ratio=)`; 2.0 gives the old numbers to the bit.
- Docs: `screener/README.md`, `screener/SITE_PLANNING.md`.
- Tests: `PondShapes`, `PondRoom` (7 tests) in `test_siteplan.py`. Three
  older tests rested on the old search and were moved, not weakened:
  the rescue-pass budget test switches the new search off (its 700 x
  700 site no longer needs the rescue: a 4:1 pond fits it at 151,000 SF
  on the first pass, 133,000 SF before); the open-yard test checks that
  the yard moves the building (44 ft from a lot line without it, 100 ft
  with it; it no longer costs floor area there); the code-truck-space
  read-out test uses a 650 x 500 site (at 700 x 500 the 68,000 SF layout
  now passes, 54,000 SF before, and meets the code minimum itself).

## What was verified before the merge

| Check | Result |
|---|---|
| Prototype parity | 9 prototype records (2 fixtures, 7 synthetic sites) byte-identical to HEAD before the change; `ReferenceSites` passes. |
| Default behaviour | 2 fixtures and 7 synthetic sites: 5 larger (#21 68,000 -> 84,000; synthetic 103,000 -> 115,000, 147,000 -> 166,000, 160,000 -> 223,000, a tight 12 ac site 129,000 -> 138,000), the rest the same plan; none smaller or less feasible. Two changed only in the layouts listed as compared. |
| Exhaustive check | #21's new plan equals the largest layout the exhaustive check found. |
| Run time | #21 5 s -> 15 s, #25 2 s -> 8 s; a 78 ac synthetic site 7 s -> 7 s (after the fallback was gated and capped: ungated it doubled the time for no gain). Budget 900 s per site. |
| Tests | The full suite before the three test moves: 978 run, those 3 failed, nothing else. The 3 pass after. The full suite was not re-run on the final commit before the merge. |

## Open at the merge

- The Tier 1 review (fresh-context reviewer, given the code and the
  requirements) had not reported. `02_review.md` will hold it.
- The full suite on the final commit, and its time on a quiet machine:
  it took 15 minutes with the reviewer's jobs on the same 4 cores
  (4.5 minutes before the change).
- Seen while testing, not caused by this change: a 680 x 480 ft Fairburn
  site draws a plan with 0 of the 14 truck spaces the draft code asks
  for, before and after, because no passing layout meets it.
