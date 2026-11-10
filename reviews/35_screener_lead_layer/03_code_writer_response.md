# 35 — Batch A: code writer response to the reviewer decision

Reviewer verdict (02): GO with fixes. All seven findings are addressed.

1. **Must-fix: a failed or absent check graded A.** Fixed in `grade.py`.
   Only a state the check itself reports as good is clear; anything else
   is "not checked" and counts. Now counted as not checked: a failed or
   skipped slope reading (`slope_error`, `slope_skipped`, no `steep_pct`),
   a soils check that failed or is missing, an acreage mismatch between
   the parcel map and the tax record, same-owner adjacency that could not
   be measured (`run.py` now sets the key to None for that), and any
   unknown state of fit, pond, frontage or soils. Slope and soils joined
   the caps list, so `--no-slope`, soils off or a soils cap give `not
   graded`. Reproductions from the review are now tests
   (`test_failed_service_is_not_checked_not_clear` covers the soil, road
   and lidar services each answering 503). The A headline, the digest
   text and the README table were rewritten to say what A covers.
2. **A facts failure blocked the decision.** `decide.py` catches the
   write failure, prints "no report facts kept", and writes the decision
   row. The line is built before the file is opened, so a refused entry
   leaves no empty store. Tested with a NaN in `facts.json` and a folder
   in the store's place.
3. **Stale-brief cleanup deleted look-alike files.** The run now keeps
   the names it wrote in `.briefs-written.json` and a re-run removes only
   names on that list that it did not write again. Names with a path in
   them are ignored. Tested with the reviewer's three file names and a
   tampered list. A folder from before this change has no list, so
   nothing is removed there.
4. **A kept slope answer is one reading.** `summary.md` now says how many
   answers were reused, how many asked, and the oldest kept date, and
   names the README section. `--refresh-slope` falls back on the kept
   reading when the new request fails (tested). README wording changed
   from "does not change" to the survey / service distinction.
5. **Wording.** "a recent buyer rarely sells" is gone: the item reads
   "last priced deed N years ago (a recent purchase, or a transfer
   between related owners)", and the README calls the idea a rule of
   thumb. A negative holding period is not counted. The next-door text
   says "directly or through its other parcels".
6. **Rule edges.** `hard` is `blocked` even beyond a cap. Unknown states
   are not checked. `max_hydric_pct = 0` and `max_poorly_drained_pct = 0`
   now mean off, like `recent_purchase_years = 0`.
7. **Markdown.** `digest._cell` also replaces brackets, angle brackets,
   backticks, asterisks and underscores in county text. The brief link is
   built from the report's own safe file name and is not passed through
   it.

Not changed: the spy still matches on the `lead_` stem, backed by
`test_every_key_carries_a_stem` (the reviewer's stated limit).

Fulton, 2026-11-10, after the fixes: A 14, B 96, C 140 of the top 250;
blocked 390 (every `hard` parcel, whatever its rank); 941 not graded.

Suite: 475 tests, OK, offline.
