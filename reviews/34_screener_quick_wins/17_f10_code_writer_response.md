# 17 — F10 code writer: road frontage from GDOT road centrelines

**Source and access check (2026-11-10).** GDOT's road inventory on
`rnhp.dot.ga.gov` (`GDOT_ROUTE_NETWORK`, `GDOT_Network_LRSN`) carries route
codes but no functional class or name. The layer used is GDOT's own
published Functional Class map service,
`maps.itos.uga.edu/arcgis/rest/services/GDOT/GDOT_FunctionalClass/MapServer`
(ArcGIS Online items owned by GDOT, copyright "Georgia Department of
Transportation, ITOS-CVIOG-UGA", item updated 2026-08): layers 0-6 are the
statewide LRS road network split by functional class (interstate ...
local road). `robots.txt` on that host (and on `rnhp`/`enterprisegis`)
answers HTTP 404, so no crawl rule applies; the service is public, no
sign-in, Query/Data on, and no posted terms forbid automated use. Probed
live with the screener's User-Agent: one `identify` per parcel, about
0.1-0.5 s, sane values on three Fulton boxes (local roads, a minor
arterial, I-20 tagged as limited access). The layer has no street names;
the `route_id` gives "SR n" for state routes and the GDOT route id for
county/city roads. osmnx is not used.

**What.** New `screener/frontage.py`; `run.attach_frontage` runs right after
`attach_soils` (after scores, flags, the sort, F6, F7, F9) for the top
`[frontage] max_parcels` ranked parcels, `workers` at most 3. One
`identify` (all class layers 0-6, envelope = parcel box + 150 m, `sr=4326`)
through `arcgis.request_json` (cache, retries 2, a `check` so bad answers
are never cached). Frontage = parcel line (local plane) within 50 ft of a
centreline; per road, total (union) for non-limited-access roads, and
interstate/freeway frontage kept apart (no driveway). Corner allowance
(up to about 50 ft per end) is stated in the brief caveat.

CSV (after `soil_dwellings_limited_pct`): `frontage_ft`, `frontage_road`,
`frontage_limited_ft`. States: number + road / `0` + `none within 50 ft`
(brief gives the nearest road's distance) / empty + `not checked: <why>`.
Brief: one "Road frontage" line with the curb-cut caveat, and a new Next
steps line: confirm a curb cut / driveway permit (GDOT or city/county),
not checked by the screen. F12 plan: the frontage drawn as an orange
polyline plus a legend row, only when the F10 keys are present (the pinned
plan hash without them is unchanged).

**Fail soft.** HTTP or ArcGIS error, no `results` list (header-only),
a result from another layer (or a bool id), no polyline paths, not WGS84,
malformed coordinates, or an answer at the 1,000-result cap: `not
checked`, never a value. 5 service failures in a row skip the rest; notes
in `summary.md` (`ROAD SERVICE FAILURES`, and a "--refresh" note if no
ranked parcel had frontage). An empty `results` list is a real "none".

**Leak rule.** Keys `frontage_*`, `_frontage_detail`, `_frontage_line`
(stem `frontage_`; screens.py's existing "no road frontage" text is not a
key) are attached only after the sort. Spy tests on `rank.learned_score`,
`scoring.fixed_score`, `screens.site_rejects`, `screens.flags` (slope on
and off); a patched-off run gives identical rank/score/flags/why/fit/
pond/soil and the same non-GDOT requests. The roads are not in
`layers.LAYERS` (build_eval copies every layer) nor in `checks.py`;
static tests pin that, and that rank/scoring/screens/autoresearch never
name it. `RunHarness` serves a fake identify route by default.

**Settings.** New `[frontage]`: `enabled` (true), `max_parcels` (250; 0 =
all, the default when the table is absent), `workers` (3, 1-3 enforced).
Bad values exit 2.

**Not touched.** scoring.py, screens.py, rank.py, autoresearch/, any root
file. No new dependency. Suite: 396 tests OK.
