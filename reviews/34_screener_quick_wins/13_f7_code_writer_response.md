# 13 — F7 code writer: stormwater detention allowance

**What.** New `screener/detention.py`. For each ranked parcel, after scores,
flags, the sort and the F6 building fit (`run.attach_ponds`, called right after
`attach_fits`, outside the slope branch, so it runs with `--no-slope` too):

- `detention_pct` (CSV): the prototype pond's top-of-bank footprint
  (400 x 200 ft = 80,000 SF, 1.84 ac) as a percent of the parcel's open ground
  on the county map (all parts). Empty when no open ground or not checked.
- `pond_fit` (CSV): `pond fits` / `pond fits (no building envelope placed)` /
  `no pond room found` / `not checked: <why>`. The pond rectangle is placed with
  the copied F6 rotated-rectangle search (`sitefit.fit_lengths`, one depth
  200 ft, one length 400 ft) on open ground inside the 25 ft setback minus the
  fitted envelope. Envelope none found: tried on open ground alone and labelled
  so. Fit not checked: pond not checked. Wording never says "does not fit".
- Brief line "Detention pond (rough allowance)" with depth, 4:1 slopes, about
  9.1 acre-feet, and the caveat: no hydrology, no NOAA Atlas 14, no impervious
  runoff, no local rules. For no room it adds that the envelope was placed first
  at its largest length, so a shorter building may leave room.
- F12 SVG: teal `class="pond"` rectangle when placed, plus a legend row (only
  when an F7 result is attached, so the pinned F12 hash is unchanged).

**Copied, not read.** `POND = {"L": 400, "W": 200, "depth": 6, "slope": 4}` from
root `building_prototypes.json` "detention" (schema 1, as_of 2026-11-05) and
`detention_volume_cf` from root `site_fit.py`. Text-only drift tests compare both
(skipped when root is absent). No root file is imported or read at run time.

**Leak rule.** Keys `detention_pct`, `pond_fit`, `_pond_polygon`, `_pond_detail`
(stems `detention`, `pond`) are attached only after the sort. Spy tests on
`rank.learned_score`, `scoring.fixed_score`, `screens.site_rejects` and
`screens.flags` (slope on and off) check none of them is seen; a patched-off
run gives identical rank/score/flags/why and the same HTTP request count. Static
backstop: rank.py, scoring.py, screens.py never name the stems.

**Settings.** New `[detention]` table: `enabled` (default true) and
`max_parcels` (default 0 = all); validated like `[fit]` (bad values exit 2;
old settings without the table still run). No web service, so no workers
setting and no robots/terms check needed.

**Cost.** A few ms per parcel; the F6 near-cap worst case (100 holes, nothing
fits) takes about 3.5 s more. README states it; a test bounds it.

**Not touched.** scoring.py, screens.py, rank.py, sitefit.py, autoresearch/,
any root file. No new dependency. Suite: 344 tests OK.
