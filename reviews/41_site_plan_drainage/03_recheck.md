# 41 — Re-check of the review fixes

The same reviewer re-checked `4c86071..8ff329e`, again on its 44 sites in
both angle modes, plus 21 two-frontage and large sites.

**One blocking finding, in a fix made for its first review.**

| # | Finding | Severity | Done (`591c03a`) |
|---|---|---|---|
| 1 | The rule added for `02` finding 4 (look first for a pond 3 ft below the pad) moved a two-frontage ridge site's pond, already 1 ft below its pad and beside the building, 174 ft away onto the ground of the second building: 543,000 SF in two buildings became 428,000 SF in one, with no flag. It helped nowhere (it moved the drawn pond on one other site, and fixed no uphill pond); no test covered it | blocking | Removed. Outside the pass a pond is sited as it was before this change. `test_the_pond_does_not_take_the_second_buildings_ground` pins the site at 543,000 SF. `02` finding 4 therefore stands as documented: inside the 3 ft tolerance nothing looks for a lower pond; `POND_ABOVE_PAD` says so. |
| 2 | The stalls filter could drop a pass plan for another that is then not drawn | nit | It gives way only to a plan that itself ranks above the plan before the pass. |
| 3 | A second plan per starting point even when the best already meets its trailer target; a comparison by value where identity was meant | nit | Only when the best is short of its target; by identity. |
| 4 | `drains_by_gravity`: None for one pond measured uphill with another unmeasured; True possible with not every pond placed | nit | False for any measured uphill pond; True only when every pond is placed and measured. Tested. |
| 5 | Fixes 4 and 9 of `02` untested; a stale comment | nit | Fix 4 is gone; the comment corrected. Fix 9 (the tier only among layouts that pass) is correct by the reviewer's reading and stays untested: no site of its 44 has a failed layout the tier would move. |
| 6 | A second building's pond read against a pad the sheet did not print | nit | The second building's pad elevation is in the record and on the sheet. Tested. |

Verified by the reviewer at `8ff329e`: every other fix of `02` does what
it says; both mutants it had found passing are now caught (the second
round removed; the pass's pond ceiling removed); prototype records and
sheets 88 of 88 identical to `780e32c`; against `4c86071` no default
plan worse in floor area, stalls, feasibility, status or flags (apart
from the site of finding 1); determinism; run time unchanged.

## After `591c03a` (author)

- The 23 sites in four modes: prototype records identical; the default
  table of `01_decision_note.md` reproduces row for row.
- Full suite: 992 tests OK in 514 s on a quiet machine (about 273 s
  before `reviews/40`). CI's 5-minute limit cannot hold it: open, the
  operator's call (`01_decision_note.md`).
- Not re-reviewed: `591c03a` itself (a removal and the nits above).
