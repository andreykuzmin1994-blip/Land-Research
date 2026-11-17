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
split, and the ranking key. Its report and what was done follow.

(Running when this file was first committed; see the next commit.)
