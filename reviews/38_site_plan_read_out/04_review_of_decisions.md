# 38 / 04 — Review of the three operator decisions

Tier 1 by the repository's rule (one independent reviewer for a logic
change outside the metric), run twice here because the first review's
fixes were themselves logic changes. Both reviewers were fresh-context
subagents that did not write the code and were given the code and the
operator's decisions, not the author's conclusions.

## First review (of `279f1e6` and its docs)

It ran the suite (951 tests then), compared the code before and after on
22 synthetic sites in both behaviours plus the two reference fixtures,
and re-ran the seven rows of the effects table (they matched). Findings,
and what was done:

| # | Finding | Done |
|---|---|---|
| 1 | The 60 ft open yard was not held at an inside corner of a parcel: the boundary was cut into two-point segments, so an offset lost its corner. 4 of 70 notched rectangles gave a plan 50 to 54 ft from the notch. | The yard is one band round every lot line that is not a road, cut from the building envelope; lines are merged back per kind. Test with a notched Fairburn parcel. |
| 2 | The agricultural rule sent sites into an older failure: with a residential line behind, every layout failed on its car route. Also a neighbour along a whole side read as a rear line. | A layout is turned the other ways round before it is given up (see `03`); residential lines are split per straight stretch. Tests for a farm behind and homes beside. |
| 3 | The prototype behaviour's read-out stated decisions of the default behaviour ("the ranking does not trade floor area", "not used in this market"), and a 5% threshold dropped one of its notes. Its plans were identical. | The read-out states no market and no ranking decision for a prototype record, and reports as before. Test. |
| 4 | Fairburn's truck-space minimum did not steer the layout: a 200,000 SF plan with 0 of 40 was drawn. The reviewer left the choice to the operator. | Among passing layouts one that meets the draft code minimum is drawn first. Recorded in `03` as a choice the operator can reverse. Test. |
| 5 | `DISTRICT_BUFFER_NOT_DRAWN` was lost when the only uncovered line was a rear one. | The flag covers rear lines. Test. |
| 6 | The new tie-break also applied in the prototype behaviour. | Default behaviour only. |
| 7 | The corridor overlay read only the longest frontage's road. | It reads every road the parcel fronts (the frontage check already returned them). Test. |
| 8 | Three tightened values never drew: the 60 ft floor overwrote them in the table, and the sheet called 60 ft a zoning requirement. | Resolved with finding 1: the table keeps 30 and 31 ft. |
| 9 | Two stale lines in `SITE_PLANNING.md`; "each plan lists what was applied" was not accurate. | Fixed. |
| 10 | Three weak tests; untested cases named. | Rewritten; the named cases have tests, except an exact tie in the ranking and truck spaces on a two-building plan. |

Found sound: every edge kind has a value in both tables and none is
looser than the draft; boundary length is conserved on rectangles,
L-shapes, flag lots, corner lots, landlocked and multi-part parcels; an
empty tax class is safe; "SR 140" is not read as "SR 14"; the new flags
are default behaviour only; no 2,000 SF remains in the rescue pass, the
second-building search or the first sort; `--merge` refuses when the
underground switch differs.

Left for the operator, from that review:

- `USE_RESTRICTION_REPORTED` (College Park) is severity 2: the floor
  area is still counted. Raising it to a gate would stop that.
- Tax class E takes in public vacant land as well as schools and
  churches. It errs on the safe side.

## Second review (of the fixes)

Narrow: turning a layout round, the open yard as one cut, the edge
split, and the ranking key (commit `0899615`). It ran the cases itself.

| # | Finding | Done |
|---|---|---|
| 1 (high) | The turn-round was skipped in the rescue pass, so five sites with homes behind (1000 x 700, 800 x 600, 1200 x 500, 700 x 500 ft) still read infeasible. | A layout is turned on any route failure, in the first pass and the rescue. All five now plan (65,000 to 202,000 SF). Test on 800 x 600 ft. |
| 2 | With homes along a whole side, the plan's own check read "short" on a residential rear line: the 21 ft read round the back corner was its own rear line, asking for 110 ft. | A side or rear stub up to 25 ft joins the run beside it. Test. |
| 3 | The read-out gave a false reason where a larger Fairburn layout ranked lower ("steep ground and relief" on flat ground; the real reason was 0 of 48 code truck spaces). | Each layout carries its draft code minimum; the read-out states it. Test. |
| 4 (read) | A time-budget stop during a turn dropped the finished failing layout (a no-fit record instead of the best so far). | The layout is kept before the stop is passed on. Test. |
| 5 | An unguarded `linemerge` could raise on a single line. | One guarded helper. |

Found sound: the open-yard row read "meets" on every plan drawn (L,
notched front and rear, triangle, flag lot, holed, partial frontage,
corner, landlocked, frontage not checked, and a 144-site grid; with the
test angle list only); boundary length conserved with no overlaps; every
flip inside the building envelope with the same car capacity;
`LOADING_FACES_RES` follows the turned plan; no failing layout ranked
above a passing one; the prototype behaviour identical on 7 sites.

After these fixes: 959 tests pass; the prototype behaviour has 0
differences from the original code on 8 synthetic sites; the two
reference sites plan as in `03`. These last fixes were not reviewed a
third time: they are the reviewer's own suggested fixes, each with a
test that failed before and passes after.

## Still open

- The 33-site replay of `reviews/37` under the new rules (`03`).
- An exact tie in the ranking, and truck spaces on a two-building plan,
  have no test.
- The open yard was checked with the test angle list (0 and 90
  degrees) on synthetic shapes, and with the real angles on the two
  reference sites only.
