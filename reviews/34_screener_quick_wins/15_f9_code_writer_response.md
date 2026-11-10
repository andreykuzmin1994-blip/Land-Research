# 15 — F9 code writer: SSURGO soils via USDA Soil Data Access

**Access check (2026-11-10).** `sdmdataaccess.sc.egov.usda.gov/robots.txt`
(and the `nrcs.usda.gov` alias) answer HTTP 404, so no crawl rule applies.
The NRCS Web Service Help page publishes `Tabular/post.rest` as a public web
service for ad hoc, real-time queries of any area of interest, capped at
100,000 rows per answer, down nightly about 00:00-02:00 Mountain. No terms
page forbids automated use. Fetched with the screener's own User-Agent.
Live probe of the final query on two Fulton boxes: about 0.7 s each, sane
values (HSG B, small A/D share, 0-1% hydric).

**What.** New `screener/soils.py`; `run.attach_soils` is called right after
`attach_ponds` (after scores, flags, the sort, F6 and F7), for the top
`[soils] max_parcels` ranked parcels, `workers` at most 3. One POST per
parcel through the existing `arcgis.request_json` (cache, retries=2, a
`check` so bad answers are never cached). The query clips map units to the
parcel WKT (lon/lat, `STGeomFromText(...).MakeValid()`, SDA's own
`GetClippedMapunits` / `GetGeogFromGeomWgs84` macros), sums geodesic area
per mukey and joins `muaggatt` (one row per map unit, reliably populated):
`hydgrpdcd`, `drclassdcd`, `hydclprs`, `engdwobdcd`. The WKT is checked
against a numbers-and-brackets regex before it reaches the SQL text.

CSV (after `pond_fit`): `soil_hsg` (dominant HSG by area / `none rated` /
`not checked: <why>`), `soil_hydric_pct`, `soil_poorly_drained_pct`,
`soil_dwellings_limited_pct` (number / `none rated` / empty when not
checked). Brief: one "Soils" line with the HSG mix, the three shares, the
unrated share when 1% or more, and the map-scale caveat. "Small Commercial
Buildings" is per-component (`cointerp`) only, so it is not asked for;
dwellings without basements is the documented slab-on-grade proxy.

**Fail soft.** HTTP error, SDA's `{}` no-match answer, header-only table,
unexpected columns, malformed row or rating, hydric outside 0-100, an answer
at the 100,000-row cap, or mapped soil under 50% / over 150% of the parcel
shape: `not checked`, never a value. After 5 service failures in a row the
rest of the run is skipped (nightly outage) and `summary.md` says so.

**Leak rule.** Keys `soil_*` and `_soil_detail` (stem `soil`) are attached
only after the sort. Spy tests on `rank.learned_score`, `scoring.fixed_score`,
`screens.site_rejects`, `screens.flags` (slope on and off); a patched-off run
gives identical rank/score/flags/why/fit/pond and the same non-SDA requests;
static backstop: rank.py, scoring.py, screens.py never name the stem.

**Settings.** New `[soils]`: `enabled` (true), `max_parcels` (250 in
settings.toml; 0 = all, the default when the table is absent), `workers`
(3, 1-3 enforced). Bad values exit 2. `RunHarness` serves a fake SDA route
by default, so every run test stays offline.

**Not touched.** scoring.py, screens.py, rank.py, autoresearch/, any root
file. No new dependency. Suite: 370 tests OK.
