# 14 — F7 detention pond allowance: reviewer decision (Tier 1)

Fresh reviewer. I read `13_f7_code_writer_response.md`, the diff of `583f51f`,
`detention.py` against `sitefit.py`, and the new tests, and ran the suite and a
few probe shapes myself (an L-shaped parcel, two open parts, a 33-degree rotated
lot). Each one placed the pond with no overlap on the envelope and inside the
open ground.

## Verdict: GO, with the fixes below

- **Leak rule holds.** `attach_ponds` runs after the sort and `attach_fits`,
  outside the slope branch. The spies cover rank, scoring, site_rejects and flags
  with the slope check on and with `--no-slope`. The patched-off run keeps the
  same order, scores, flags and request count.
- The copied prototype and the volume formula are checked against the root
  files as text only. Nothing reads a root file at run time.
- No new service, no new dependency, and no file on the do-not-edit list was
  touched.
- The wording never says "does not fit". The three states stay apart in the CSV
  and the brief.

## Fixed in this step

1. **`detention_pct` was dropped whenever the building fit was off or capped.**
   The share needs only the open ground, so it is now kept. Only the placement
   reads "not checked". The brief's not-checked line now gives the share too.
   Tests cover this at the geometry level and the run level.
2. **The caveat now says the pond may sit right against the envelope**, with no
   room for access, grading or a buffer. The difference with the envelope leaves
   no gap. The README and the brief test were updated.
3. **The `summary.md` notes counted only the not-checked parcels.** The coder's
   response said the pond's numbers were there. A note now gives the fits and
   no-room counts as well (tested).

## Left as is

- The F6 building-fit line still lists detention as not modelled. That is still
  true of the envelope fit itself, and the detention line follows right after it.
- "No pond room found" is common on large fits, because the envelope is placed
  at its largest length first. The brief says so.
