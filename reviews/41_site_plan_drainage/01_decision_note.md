# 41 — Screener site planner: drainage first (Tier 1)

**Decision: committed on `claude/festive-archimedes-eyjbo6`, on top of the
open PR #51 (the fix of `reviews/40`); not merged.** After a merge a human
runs `python -m screener.autoresearch.evaluate --decide --new-baseline` on
the merge commit. The score should not change: the evaluator imports none
of the planner's modules.

Asked by the operator on 2026-11-18, looking at the test-fit sheets: "think
like a site planner, like an architect, like an engineer when you design
these." Shown a critique in five priorities (drainage, grading with the
land, the truck, use of the land, structural sizing) and a proposal to
start with drainage: "Go ahead."

## Tier

Tier 1 (one independent reviewer), as `reviews/38` to `40`: a screener-side
change to the planner's default behaviour. No evaluator, metric,
credential or new external service; no new request.
`--prototype-behaviour` records are byte-identical (below).

## What was wrong

The planner placed the building first, wherever it fits longest, and its
pond in what was left. Nothing asked whether the paving could drain to
the pond. Measured on the two reference sites:

| Site | Ground | Pond | Pad | |
|---|---|---|---|---|
| #25 | 930–980 ft, falls 4% to the north-west | ground 958–979 (mean 968): above three-quarters of the site | 959 | the pond stood 9 ft **above** the pad; the truck court had the low side |
| #21 | 890–932 ft, falls 6% to the east | ground mean 908 | 919 | 11 ft below the pad: drains |

A civil engineer starts the other way: where the water leaves the site,
the pond there, the building above it.

## What changed (default behaviour only)

- **The check** (`finish`, `pond_over_pad`). Each pond's ground (the mean
  of the lidar samples on its footprint) against the pad (one plane at
  the module's mean ground). More than 3 ft above it
  (`POND_UPHILL_TOL_FT`) is `POND_UPHILL`, severity 2, with a playbook
  entry (stormwater, before an offer). In the record:
  `detention.pond_over_pad_ft`, `detention.drains_by_gravity`, and
  `drains` on each layout compared.
- **The ranking.** Among layouts that pass, one whose pond drains before
  a larger one whose pond does not (after the code truck spaces, before
  floor area). The read-out names the larger one and the reason; its
  trailer note no longer names a layout whose pond does not drain.
- **Pond first, building second** (`drainage_first`, the first step of
  the pass of `reviews/40`). The two lowest open pond spots are reserved
  in turn and every cross-section is fitted round the reserved pond, the
  ones that fit largest on the open parcel first, stopping when none
  left can beat what is in hand. The pass also starts from layouts that
  passed with an uphill pond; in the pass ponds are sited only on ground
  the pad drains to (`max_z`), and no plan with an uphill pond is added.
- **The street rule of the pass**, in two rounds: first only layouts with
  docks off the street and loading away from homes; layouts that face
  either only where the best plan before the pass faced it already and
  the first round found none that faces neither and is no smaller.
  At most three of the pass's plans go into the results
  (`POND_ROOM_KEEP`); 90 tries (60 before).
- **The sheet** (default behaviour). The pad elevation on the building
  ("PAD EL 917") and each pond's ground against it ("GROUND 8 FT BELOW
  PAD").
- Docs: `screener/README.md`, `screener/SITE_PLANNING.md`.

What the check is not: a grading plan. One level pad; no paving slopes,
pipes, outfall or receiving water; a pond up to 3 ft above the pad is
taken as dug deeper (from 1 ft the plan says so, `POND_ABOVE_PAD`; the
deeper pond's footprint is 15 to 30% larger than drawn), and a layout
inside that tolerance is not laid out again to get its pond lower. A
second building's pond is measured and flagged but not chosen for. The four other priorities (dock side and a court
pad below the floor, truck turning, use of the land, structural bays)
are not in this change.

## What was verified (author; the review follows in `02_review.md`)

| Check | Result |
|---|---|
| Prototype parity | 43 prototype records (23 sites, real angles and the tests' angle patch) byte-identical to the records before `reviews/40`. |
| Exhaustive check, #25 | Every 3° x a 20 ft position grid x 4 flips, each through `finish` with ponds sited low, docks off the street: the largest layout that drains is 70,400 SF (rear-load 160, 440 ft). The planner now draws it (70,000 SF, pond 24 ft below the pad). |
| The 23 sites against PR #51's code | Table below. No site less feasible, none with a new severity-1 flag, none newly street-facing. One smaller by design (the L-shape: every pond was uphill). |
| Sheets | #21 and #25 redrawn and read: pond on the low ground, building above it, docks off the street. |
| Tests | New class `Drainage` (5 tests); `PondRoom` re-pointed (the reference sites planned once per process); the #21 read-out test uses the search's own plan for its pond note. Full suite: in `02_review.md`. |

### The 23 sites, default behaviour (floor area / trailer stalls)

Before = PR #51's code; after = `8ff329e` (the review's fixes in).
`ang` = the tests' two-angle patch. Rows not listed plan the same.

| Site | Before | After | Note |
|---|---|---|---|
| rank21 | 84,000/0 | **91,000/0** | pond first; new severity-2 `FIRE_SECOND_ACCESS` (driveways a few feet under half the building diagonal apart) |
| rank25 | 67,000/7, pond 9 ft above the pad | **70,000/0**, pond 24 ft below | the old plan could not drain; new `TRAILER_SHORT`, `DROP_YARD_UNREACHABLE` (no stalls) |
| 1800 x 1900 with homes (both) | 428,000/86 | **520,000/136** | pond first |
| slope 800 x 900 | 211,000/21, pond 9 ft above the pad | 211,000/21, pond 16 ft below | the same floor area and stalls, with a pond that drains |
| slope 800 x 900 ang | 205,000/0 | **211,000/21** | new `FIRE_SECOND_ACCESS` |
| low front 700 x 800 ang | 151,000/0 | **157,000/16** | new `FIRE_SECOND_ACCESS` |
| L-shape (both) | 170,000/17, every pond 9 ft above the pad | 156,000/0, pond 10 ft below | smaller by design; the 170,000 is named in the read-out; new `TRAILER_SHORT`, `DROP_YARD_UNREACHABLE` |

Run time: #21 26 s (21 s before), the L-shape 20 s (2 s before: it now
spends all 90 tries), others under 13 s.

## Open

- **CI.** `.github/workflows/offline-tests.yml` allows 5 minutes; the
  screener suite took about 4.5 before `reviews/40` and takes about 9
  now (the default-behaviour tests run the pass). Both CI runs on PR #51
  were cancelled at the limit. The limit needs raising (a root file: the
  operator's call, not made here).
- Seen while testing, not caused by this change (`reviews/40`): a
  680 x 480 ft Fairburn site draws a plan with 0 of the 14 truck spaces
  the draft code asks for.
