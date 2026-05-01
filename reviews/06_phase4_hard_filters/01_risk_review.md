# Phase 4 Risk and Architecture Review — Hard Filters H5–H10 (PASS-WITH-FLAG stubs)

**Reviewer:** Agent 1 role (sub-agent context).
**Date:** 2026-05-01.
**Branch:** to be confirmed by orchestrator (continues from Phase 3.1 at `claude/revalidate-phase-3-ytHPT`).
**Scope:** `BUILD_PHASES.md:74-80` (Phase 4) and `program.md:163-174` (Hard Filter table H1–H10), implemented as stub callables appended to `_HARD_FILTERS` in `research.py`.
**Predecessors:**
- Phase 3 risk review at `reviews/04_phase3_fulton_discovery/01_risk_review.md` (R-01 through R-48).
- Phase 3.1 reviewer decision at `reviews/05_phase3_1_punch_list/02_reviewer_decision.md` (APPROVE; see §5 "Phase 4 readiness").

---

## Sections

1. Verdict at the top
2. Per-filter risks (H5, H6, H7, H8, H9, H10)
3. Cross-cutting risks (R-100 series)
4. Go/no-go gates for Agent 3
5. Out of scope for Phase 4
6. Final verdict

---

## 1. Verdict at the top

**GO.**

Phase 4's stub-only scope is the minimum-viable extension of the Phase 3 pipeline that satisfies BUILD_PHASES.md's exit criterion ("all 10 hard filters operational, ... rejected parcels have rejection reasons logged") without requiring any new external data wiring or any mutation of the immutable layer (`prepare.py` / `parameters.json` / `sources.json` / `program.md`). The pattern is already proven: `_h3_flag` and `_h4_flag` at `research.py:530-547` demonstrate that a `_FilterResult("flag", "H<N>", reason)` plumbs cleanly through `_process_parcel:933-1025` and produces a `flagged_items` row of `flag_type='data_gap'` with a `Phase X: resolve HN for parcel_id=...` resolution hint. The Phase 3.1 punch-list test `TestPhase31FilterPipelineExtensibleExecutes.test_synthetic_h5_filter_emits_marker_flag` at `tests/test_discovery.py:962-998` already proves the runtime append pattern works end-to-end through `_process_parcel`'s second filter loop at `research.py:1003-1010`.

Six new callables (`_h5_filter` ... `_h10_filter`), six new unit tests, two existing tests updated (`test_filter_pipeline_order` and `TestHappyPathDryRun.test_two_feature_happy_path`), and optional H3/H4 docstring micro-edits. No new HTTP, no new tables, no new columns, no parameter reads. Estimated implementation surface: ~80 lines of code plus ~150 lines of tests. Phase 4 should land in well under the BUILD_PHASES.md Phase 4 budget (~2 hours).

The conditions Agent 3 must enforce before merge are concrete and listed in §4. The two highest-probability "missed-update" failure modes are R-101 (the existing `test_filter_pipeline_order` will hard-fail unless the test's expected list is updated in the same diff) and R-105 (the existing `TestHappyPathDryRun` "4 parcels x 2 flags = 8 minimum" assertion will pass weakly but lose its informational signal unless updated to "4 parcels x 8 flags = 32 minimum"). Both are flagged below.

---

## 2. Per-filter risks

Each subsection lists: the eventual data source, the Phase 4 stub behavior, stub-specific risks (Phase 4 problem space), and labeled "future-phase risks" the human should know are coming.

### 2.1 H5 — Environmental contamination

- **Eventual data source.** EPA Envirofacts (NPL Superfund sites, RCRA corrective action), state EPD/DEQ brownfield registries (GA: GEOS), per `program.md:169` and `sources.json:91-112`.
- **Phase 4 stub behavior.** `_h5_filter(parcel, conn, params) -> _FilterResult("flag", "H5", "H5 environmental unjoined: pending EPA Envirofacts + state EPD wiring (Phase 5+ data wiring)")`. Identical structure to `_h3_flag` at `research.py:530-538`. Parcel proceeds; one `flagged_items` row of `flag_type='data_gap'` with `suggested_resolution='Phase X: resolve H5 for parcel_id={parcel_id}'` is emitted.
- **Stub-specific risks.** Adds one row per passing parcel to `flagged_items`. With Phase 3 already emitting H3 + H4 + occasional multipolygon flags (~2-3 rows/parcel today), H5 alone bumps that to ~3-4. See R-103 below for the cumulative volume across H5–H10.
- **Future-phase risk (NOT this Phase 4 scope, but flagged for the human).** EPA Envirofacts has no spatial query API for arbitrary lat/lng; you must either (a) bulk-pull GA NPL/RCRA point geometries and PostGIS-spatially join, or (b) hit per-record APIs with rate limits. The 500-ft adjacent-contamination flag in `program.md:169` requires a buffered spatial join, which means Phase 5+ should add an `environmental_sites` table to `prepare.py` (a future-run mutation under AUTORESEARCH_MECHANICS.md "When Mutating prepare.py"). State EPD scraping for GEOS is web-scrape only (no API); rate limit politeness will matter.

### 2.2 H6 — Wetlands

- **Eventual data source.** USGS National Wetlands Inventory (NWI) WMS/WFS or per-tile shapefile pulls, per `program.md:170` and `sources.json:102-106`. Threshold: `parameters.json:12` `wetlands_max_pct_of_parcel: 20`.
- **Phase 4 stub behavior.** `_h6_filter(parcel, conn, params) -> _FilterResult("flag", "H6", "H6 wetlands unjoined: pending USGS NWI mapper wiring (Phase 5+ data wiring)")`. Stub does NOT read `params["hard_filters"]["wetlands_max_pct_of_parcel"]`; documenting that the eventual implementation will is enough for now (R-104).
- **Stub-specific risks.** Same volume risk as H5.
- **Future-phase risk.** USGS NWI WMS returns raster tiles; computing "% of parcel covered by wetlands polygon" requires either a vector-format pull (NWI WFS in a few regions) or rasterized intersection in PostGIS. The 20% threshold is a polygon-overlap calculation, not a point query — you cannot use the parcel centroid alone. Phase 5+ will need `ST_Area(ST_Intersection(parcel.geometry, wetland.geometry)) / ST_Area(parcel.geometry)`. This means parcel polygons must already be in PostGIS as valid Polygon (4326) — which Phase 3 ensured. The Phase 3 multipolygon-reduction flagged_items rows (`research.py:1011-1017`) become MORE relevant in Phase 5+ because the dropped rings could carry wetland coverage that the kept-largest-ring misses. Re-processing those flagged parcels is a Phase 5+ obligation.

### 2.3 H7 — Road access

- **Eventual data source.** County road classification layer (varies by county; for Fulton this is likely a separate ArcGIS layer on the Fulton MapServer that Phase 4+ would discover) or state DOT functional-classification GIS. `program.md:171` requires "minimum: county collector road"; `parameters.json:14` is `min_road_classification: "county_collector"`.
- **Phase 4 stub behavior.** `_h7_filter(parcel, conn, params) -> _FilterResult("flag", "H7", "H7 road access unjoined: pending county road classification + DOT layer wiring (Phase 5+ data wiring)")`.
- **Stub-specific risks.** Same volume risk.
- **Future-phase risk.** "Frontage on or deeded access to" is a non-trivial spatial test — the parcel polygon must touch (`ST_Touches`) or share an edge (`ST_Intersects` with a buffered road centerline) with a road feature whose `functional_classification` is at least "county collector." Many parcels nominally lacking direct frontage have access via private easement; per `program.md:171` deeded easements count, but they're recorded in deed documents, not GIS. Expect a meaningful flag-rate even when full data is wired. Also: county road classification taxonomies differ per state; `min_road_classification: "county_collector"` is a string token that needs a mapping function per county. Phase 5+ should add a road-class normalizer.

### 2.4 H8 — Utility availability

- **Eventual data source.** Municipal utility provider service area maps (water, sewer, electric 3-phase), per `program.md:172` and `sources.json:660-663` ("Utility Provider Service Maps", access: `web_scrape`). Threshold: `parameters.json:15` `max_utility_extension_ft: 1500`.
- **Phase 4 stub behavior.** `_h8_filter(parcel, conn, params) -> _FilterResult("flag", "H8", "H8 utility availability unjoined: pending utility provider service map + extension-distance wiring (Phase 5+ data wiring)")`.
- **Stub-specific risks.** Same volume risk.
- **Future-phase risk.** Fragmented data — Atlanta metro alone has dozens of water/sewer providers (City of Atlanta DWM, Fulton County Public Works, City of Union City, etc.). No single API. Expect heavy AI-fallback and per-jurisdiction connectors. The 1,500-ft extension distance is `ST_Distance(parcel, ST_LineString(nearest_utility_main))` in PostGIS once the utility-main geometries are loaded. Loading them is the hard part.

### 2.5 H9 — Topography (grade differential)

- **Eventual data source.** USGS 3DEP LiDAR-derived elevation, per `program.md:173` and `sources.json:114-121`. Threshold: `parameters.json:13` `max_grade_differential_ft: 15`.
- **Phase 4 stub behavior.** `_h9_filter(parcel, conn, params) -> _FilterResult("flag", "H9", "H9 topography unjoined: pending USGS 3DEP elevation wiring (Phase 5+ data wiring)")`.
- **Stub-specific risks.** Same volume risk.
- **Future-phase risk.** USGS 3DEP serves elevation as a raster (1-meter or 10-meter resolution depending on coverage). Computing "max - min elevation across the parcel buildable area" requires zonal statistics — sample the raster at every grid cell within the parcel polygon, take max-min. Doable with `rasterstats` Python library or PostGIS Raster (PR_*). 3DEP is rate-limited but free. The most realistic implementation: pull a 10-m DEM tile per corridor once at corridor-load time, then compute zonal stats client-side. Phase 5+ will likely add a `topography_cache` table or a per-corridor cached GeoTIFF in `sources/`. Side note: H9 overlaps with scored parameter S3 ("Topography / grading cost"); both compute grade differential, so once H9 is wired the same calculation feeds S3 — Phase 5+ should structure the topo helper to be shared (R-110 architectural foreshadowing).

### 2.6 H10 — Ownership availability

- **Eventual data source.** County deed records / Clerk of Court (for active conservation easements), county assessor (for "owned by government entity with no disposition program"), per `program.md:174` and `sources.json:703-723`.
- **Phase 4 stub behavior.** `_h10_filter(parcel, conn, params) -> _FilterResult("flag", "H10", "H10 ownership availability unjoined: pending deed records + conservation easement registry wiring (Phase 5+ data wiring)")`.
- **Stub-specific risks.** Same volume risk; PLUS a partial-detection note: `parcels.owner_type_inferred` is already populated by `_infer_owner_type` at `research.py:406-433` and would label `owner_type_inferred='government'` for `"COUNTY OF FULTON"` etc. The orchestrator's directive is explicitly to leave H10 as a Phase 4 stub (not a partial-implementation), so DO NOT short-circuit on `owner_type_inferred=='government'` in Phase 4 — emit the flag and let Phase 5+ apply the disposition-program test. Adding the partial check now would mix stub semantics with reject semantics in a way that complicates the Phase 5+ replacement.
- **Future-phase risk.** Conservation easements are recorded in county deed books and not generally exposed via API; expect web-scrape-only data flow. Most counties don't centralize a "conservation easement registry" — Phase 5+ may need state-level registries (GA DNR for Georgia easements). The "no disposition program" half of H10 is a manual research item per government owner; Phase 5+ should likely de-scope this to a flag-for-human rather than an automated reject.

---

## 3. Cross-cutting risks (R-100 series)

R-100 series is reserved for Phase 4 to avoid collision with Phase 3's R-01..R-48 and the Phase 3.1 R-CI-1..R-CI-3 / R-MP-1..R-MP-2.

Severity scale (same as Phase 3): **S1** critical (block merge), **S2** high (block merge but routine), **S3** medium (block merge or accept-with-rationale), **S4** low (post-merge OK).

### R-101 (S1) — `test_filter_pipeline_order` is currently a hard assertion of the exact 4-filter list

`tests/test_discovery.py:438-441` reads:
```
ids = [f.__name__ for f in research._HARD_FILTERS]
self.assertEqual(ids, ["_h1_filter", "_h2_filter", "_h3_flag", "_h4_flag"])
```
Appending H5–H10 to `_HARD_FILTERS` at `research.py:552` will break this test on the next run unless updated in the same diff. This is the single most likely "missed-update" failure mode for Phase 4.

**Mitigation.** Update the assertion to expect the new 10-element list:
```
self.assertEqual(ids, ["_h1_filter", "_h2_filter", "_h3_flag", "_h4_flag",
                       "_h5_filter", "_h6_filter", "_h7_filter", "_h8_filter",
                       "_h9_filter", "_h10_filter"])
```
Agent 2 must update this in lockstep with the `_HARD_FILTERS` change. Agent 3 must verify the test_filter_pipeline_order assertion matches the new list element-for-element.

### R-102 (S2) — Pipeline ordering: H5–H10 must be appended AFTER H1/H2 reject filters

The current order at `research.py:552` is `[_h1_filter, _h2_filter, _h3_flag, _h4_flag]`. The semantics in `_process_parcel:970-1025` are: the first filter loop (lines 971-992) short-circuits on `reject` (returning early before any insert) and DEFERS `flag` actions to the second loop after the parcel UPSERT lands. Reject filters MUST come first so that a parcel rejected on H1 (centroid outside Fulton envelope) or H2 (acreage out of range) does not generate any `flagged_items` rows.

H5–H10 are all PASS-WITH-FLAG (no reject path in Phase 4). Appending them at the end of `_HARD_FILTERS` preserves the H1/H2 short-circuit correctness. Inserting them BETWEEN H2 and H3 would be wrong if any of them ever became a reject filter in a later phase, because the deferred-flag pattern only works if the reject filters precede the flag filters in the list. The current `_process_parcel` design expresses this implicitly: the second loop iterates the entire list a second time and only acts on `flag` results, so as long as H5–H10 are flag-only stubs, ordering is correct regardless of where they sit.

**Mitigation.** Append at the END of `_HARD_FILTERS`. Document the ordering invariant in a comment block above the list: "Reject filters MUST come first; PASS-WITH-FLAG stubs follow." When Phase 5+ replaces a stub with a reject-capable real filter, the ordering must be revisited (call this out).

**Acceptance test.** The updated `test_filter_pipeline_order` from R-101 is sufficient.

### R-103 (S2) — flagged_items row volume per parcel grows ~3-4x

Phase 3 emits per passing parcel: H3 flag + H4 flag + (occasional) multipolygon flag = 2-3 rows. Phase 4 stubs add 6 more (H5–H10), so per parcel: 8-9 rows. Two corridors × ~500 parcels each ≈ 8,000 flagged_items rows per Fulton cycle (vs. ~2,000 today). Acceptable for Postgres (millions of rows is routine), but with implications for downstream:

- **Phase 9 snapshot/memo cost.** Per-parcel snapshots will need to summarize, not enumerate, data_gap flags. The "Flags / Open Items" section in the snapshot template at `program.md:517-518` should aggregate by `flag_type='data_gap'` with a count and a representative resolution hint, NOT list 8 separate lines per parcel. This is a Phase 9 design constraint, not a Phase 4 blocker.
- **Phase 10 strategy memo cost.** Same concern; the memo's "What I Learned This Cycle" section MUST NOT enumerate every flag.
- **Database storage growth.** ~8 rows/parcel × ~5,000 parcels/run × ~100 runs/year ≈ 4M rows/year. Trivial for Supabase Pro; check that Supabase free tier's 500 MB storage doesn't fill prematurely (it won't from `flagged_items` alone — single-row size is small).

**Mitigation.** No code change in Phase 4. Document in a module-level comment block in `research.py` near `_HARD_FILTERS` that "every passing parcel emits one data_gap row per H3..H10 stub during Phases 4-(when stubs land), 5+ (when wired)." The Phase 9 snapshot generator must summarize, and the Phase 5+ data wiring is what closes the flags via bulk UPDATE per the resolution hint pattern (R-22, R-23 in Phase 3 review).

**Acceptance test.** Update `TestHappyPathDryRun.test_two_feature_happy_path` per R-105 below to assert the new minimum count.

### R-104 (S2) — Schema-drift / parameters.json keys (no read in Phase 4 stubs)

The orchestrator confirmed the four hard-filter parameter keys are present at `parameters.json:11-15`:
- `flood_zones_blocked: ["A", "AE"]` (used by future H4)
- `wetlands_max_pct_of_parcel: 20` (used by future H6)
- `max_grade_differential_ft: 15` (used by future H9)
- `min_road_classification: "county_collector"` (used by future H7)
- `max_utility_extension_ft: 1500` (used by future H8)

I personally verified all five keys are present at L10–L15 of `parameters.json`.

The Phase 4 stubs do NOT read these keys (they don't need to — they unconditionally return a flag). But each stub's docstring should reference its corresponding parameter so a Phase 5+ implementer doesn't have to re-derive it. Example for `_h6_filter`: "Eventual implementation will read `params['hard_filters']['wetlands_max_pct_of_parcel']` (default 20) ..."

Risk: if Phase 4 stubs accidentally read a parameter that doesn't exist (typo), the stub will raise a KeyError at runtime, which would manifest as a `_process_parcel` exception (caught at `research.py:1018-1024`) and produce no flag for that parcel. Since Phase 4 stubs MUST NOT read parameters, this is theoretical, but Agent 3 should verify no `params[...]` lookup appears in any of the six new callables.

**Mitigation.** None for code. Each stub's docstring references the param key by full path for the future implementer. Agent 3 grep: `grep -n 'params\[' research.py` should show zero new param lookups inside `_h5_filter` ... `_h10_filter`.

**Acceptance test.** Static grep, performed by Agent 3 in §4 below.

### R-105 (S2) — `TestHappyPathDryRun.test_two_feature_happy_path` flag count assertion

`tests/test_discovery.py:701` reads:
```
# 4 parcels x 2 flags = 8 minimum (multipolygon flag may add more).
self.assertGreaterEqual(len(flag_rows), 8)
```
This will still PASS after Phase 4 lands (the count grows from 8 to 32+, and ≥8 is still true), but the test loses informational signal. A regression that broke H5–H10 emission would slip past silently — exactly the failure mode Phase 3.1 punch-list item 10 was designed to prevent (`reviews/05_phase3_1_punch_list/01_risk_review.md` Item 10).

**Mitigation.** Update the assertion to the new minimum:
```
# 4 parcels x 8 flags (H3, H4, H5, H6, H7, H8, H9, H10) = 32 minimum
# (multipolygon flag may add more).
self.assertGreaterEqual(len(flag_rows), 32)
```
Even better: change `>=` to `==` for the deterministic case (no multipolygon parcels in `arcgis_query_two_features.json`) — but that requires verifying the fixture. `>=` is safe.

**Acceptance test.** Updated assertion. Agent 3 verifies the count math: 4 parcels × 8 flags (H3 + H4 + H5 + H6 + H7 + H8 + H9 + H10) = 32. If `arcgis_query_two_features.json` contains a multipolygon parcel, add the corresponding extra flag count.

### R-106 (S2) — Test coverage: every new filter needs at least one positive test

Each of `_h5_filter` ... `_h10_filter` needs a unit test asserting it returns `_FilterResult("flag", "H<N>", "<non-empty reason>")`. Pattern from existing H3/H4 tests is implicit via the pipeline-order test, but Phase 4 should add explicit per-filter tests so a future regression that, say, returns `_FilterResult("pass", ...)` instead of `_FilterResult("flag", ...)` is caught at the unit level rather than only via the integration test.

**Mitigation.** Add one test per filter, e.g.:
```
def test_h5_returns_flag(self) -> None:
    parcel = {"parcel_id": "fulton-test", "centroid_lng": -84.5, "centroid_lat": 33.6, "acreage": 10}
    result = research._h5_filter(parcel, conn=None, params=_passing_params())
    self.assertEqual(result.action, "flag")
    self.assertEqual(result.filter_id, "H5")
    self.assertTrue(result.reason)  # non-empty
```
Six such tests, ~10 lines each. Group in a `TestHardFiltersStubs` class or extend `TestHardFilters`.

**Acceptance test.** All six unit tests pass; `pytest -k h5_returns_flag or h6_returns_flag or h7_returns_flag or h8_returns_flag or h9_returns_flag or h10_returns_flag` shows 6/6 passing.

### R-107 (S2) — action_type vocabulary unchanged

The `action_type` enumeration at `program.md:127` is `discovery|discovery_empty|scoring|rescore|rejection|flag|abort`. Phase 4 stubs go through the existing `_flag` path at `research.py:650-671` (which writes to `flagged_items`, not to `research_log` — the `research_log` action_type for the parcel itself remains `discovery` at line 1000). So Phase 4 introduces ZERO new `action_type` values.

This was the explicit guidance from the orchestrator and it is correct: `data_gap` flags ride on the `flagged_items` insert path, not on a new `action_type` value. If Phase 4 accidentally added a new `action_type` (e.g., `data_gap`), it would require a `program.md` edit (per Phase 3.1 item 1 protocol), which is OUT OF SCOPE for Phase 4.

**Mitigation.** No code change. Agent 3 grep: `grep -n '"action_type"\|cycle_id, "' research.py` should show no new action_type literals beyond the existing `discovery|discovery_empty|scoring|rescore|rejection|flag|abort`.

**Acceptance test.** Static grep performed by Agent 3.

### R-108 (S2) — Idempotency / re-run semantics

Phase 3.1's `TestPhase31CycleIdCollision.test_cycle_id_collision_aborts` already proves that a re-run of the same cycle aborts at `research.py:1172-1175`. Phase 4 inherits this behavior unchanged. The only Phase 4-specific re-run concern: if Phase 4 lands and a re-discovery cycle hits a parcel that previously had H3/H4 flags (and now also gets H5–H10 flags), the cumulative `flagged_items` rows for that `parcel_id` grow each cycle. There is no DEDUP on `flagged_items` insert (no `ON CONFLICT`) — every cycle emits a fresh set. This is consistent with Phase 3's design (R-38 in Phase 3 review encoded `cycle_id` into `description` precisely so flags from different cycles are distinguishable).

Acceptable: the volume is bounded (~6 new rows per parcel per cycle on top of existing H3/H4) and the resolution hint pattern (`Phase X: resolve HN for parcel_id={parcel_id}`) lets Phase 5+ close them in bulk regardless of how many cycles emitted them.

**Mitigation.** No code change. Document in the module docstring header that "re-discovery emits a fresh data_gap flag set per cycle; dedup on resolution rather than on insert."

**Acceptance test.** None (existing collision test covers the only failing case).

### R-109 (S3) — H3/H4 docstring micro-edit (optional)

The orchestrator suggests tightening `_h3_flag`'s and `_h4_flag`'s docstrings to reference Phase 5+ now that Phase 4 IS the stub-add phase. Current text at `research.py:533, 543`:
```
"""H3 zoning is unjoined in Phase 3 — emit data_gap flag, parcel passes (R-22)."""
"""H4 flood is unjoined in Phase 3 — emit data_gap flag, parcel passes (R-23)."""
```
Plus the reason strings reference `Phase 4`:
```
"H3 zoning unjoined: pending Layer 34 cross-query (Phase 4)"
"H4 flood unjoined: pending FEMA NFIP wiring (Phase 4)"
```

Both are now stale: the H3 zoning cross-query is explicitly OUT OF SCOPE for THIS Phase 4 (per the orchestrator's directive) and is deferred to Phase 5+; the H4 FEMA wiring is also Phase 5+. Updating to "(Phase 5+)" is a micro-edit, optional.

**Risk if not updated.** Cosmetic only — the resolution hint will say "Phase 4" when the actual resolution phase is Phase 5+. Phase 5+ will still find these flags via the `H3 zoning unjoined%` / `H4 flood unjoined%` LIKE patterns, so no functional impact. Skipping the edit is acceptable.

**Mitigation.** Agent 2 may include the micro-edit. Agent 3 should NOT block merge if it's omitted — but if the H5..H10 reason strings say "Phase 5+" while H3/H4 say "Phase 4", that inconsistency is a code smell.

**Acceptance test.** None — docstring edit only.

### R-110 (S3) — Future-phase architectural foreshadowing: H9 + S3 share a topo computation

H9 (topography hard filter) and S3 (topography scored parameter, weight 10 per `parameters.json:21`) both compute "grade differential across the parcel buildable area." H9 is binary pass/fail at 15 ft; S3 is a 0–10 score with bands at 3, 8, 15 ft (`program.md:188`). When Phase 5+ wires real topo data, the same DEM-zonal-stats computation feeds both. Phase 4 doesn't need to do anything here, but Phase 5+ should structure a single `_compute_grade_differential(parcel) -> float` helper and let both H9 and S3 consume it.

Foreshadowing this here so the Phase 5+ Agent 1 review notices the shared dependency.

**Mitigation.** Agent 2 may add a comment near `_h9_filter` saying "Phase 5+ should share grade-differential computation with scored parameter S3." No code today.

**Acceptance test.** None.

### R-111 (S3) — No new external HTTP calls

The orchestrator's directive: "DO NOT add any new external HTTP calls in this phase." Phase 4 stubs trivially comply (they return a constant FilterResult — no network, no DB read). Agent 3 grep verification: no new `requests.` or `_DiscoverySession` calls inside the six new callables.

**Mitigation.** Static grep, performed by Agent 3.

**Acceptance test.** Same.

### R-112 (S3) — Backwards compatibility: existing tests beyond pipeline_order/HappyPath

Beyond R-101 and R-105, scan `tests/test_discovery.py` for any other test that hard-codes the `_HARD_FILTERS` length or the H3/H4 reason strings. Quick search:

- `test_filter_pipeline_extensible` at `tests/test_discovery.py:443-452` reads `len(original) + 1` after appending a synthetic stub — this works regardless of `len(original)` so it's robust.
- `TestPhase31FilterPipelineExtensibleExecutes.test_synthetic_h5_filter_emits_marker_flag` at `tests/test_discovery.py:962-998` appends a stub and checks the marker appears in `flagged_items` description — robust under `_HARD_FILTERS` length change.
- No other test asserts the H3/H4 reason strings verbatim (verified by orchestrator).

**Mitigation.** None beyond R-101 and R-105 fixes.

**Acceptance test.** Agent 3 runs the full test suite after Phase 4 lands; expects 57 + 6 = 63 tests passing (or 63+ if Agent 2 adds extras).

### R-113 (S4) — Phase 5+ will delete the stub return statements

When Phase 5 wires real environmental/wetland/etc. data, the body of `_h5_filter` etc. will be replaced with real logic. The Phase 4 stub's docstring should say "Replace this body with real wiring in Phase 5+; do not delete the function signature." Function signatures for the filter callables are part of the contract with `_process_parcel`, which iterates `_HARD_FILTERS` and assumes the `(parcel, conn, params) -> _FilterResult` shape.

**Mitigation.** Each stub's docstring includes "Replace body in Phase 5+; preserve function signature (parcel, conn, params) -> _FilterResult."

**Acceptance test.** None — informational.

### R-114 (S4) — Stub reason strings are user-facing

The reason string ends up in `flagged_items.description` (after the `cycle=...` prefix from `research.py:666`). Eventually this surfaces in human-facing dashboards and Phase 9 snapshots. Keep reason strings short and professional. The H3/H4 precedent ("H3 zoning unjoined: pending Layer 34 cross-query (Phase 4)") is the right tone — short, descriptive, references the phase that resolves it.

**Mitigation.** Agent 3 spot-checks the six reason strings for tone.

**Acceptance test.** Visual review by Agent 3.

---

## 4. Go / no-go gates for Agent 3

Before Agent 3 approves and commits Agent 2's PR, every item below must be verified true. Agent 3's review document should explicitly tick each off.

1. **Six new callables.** `_h5_filter`, `_h6_filter`, `_h7_filter`, `_h8_filter`, `_h9_filter`, `_h10_filter` exist in `research.py`, each with the signature `(parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]) -> _FilterResult`. (R-102, R-113.)
2. **Each returns `_FilterResult("flag", "H<N>", "<non-empty reason>")`.** No `pass` action; no `reject` action; non-empty reason string. (Per-filter risks §2.1–§2.6.)
3. **`_HARD_FILTERS` list ends with the six new callables, in order.** Append to the existing list at `research.py:552`; do not interleave. (R-102.)
4. **`tests/test_discovery.py:438-441` `test_filter_pipeline_order` assertion updated.** Expects 10 elements: `["_h1_filter", "_h2_filter", "_h3_flag", "_h4_flag", "_h5_filter", ..., "_h10_filter"]`. (R-101.)
5. **`tests/test_discovery.py:701` `TestHappyPathDryRun` flag-count assertion updated.** From `≥ 8` to `≥ 32`. Comment updated to reflect the new math. (R-105.)
6. **Six new per-filter unit tests.** One per H5–H10, each asserting `action == "flag"`, `filter_id == "H<N>"`, non-empty `reason`. (R-106.)
7. **No reads of `params[...]` inside the six new callables.** Static grep: `grep -nE '_h(5|6|7|8|9|10)_filter' research.py` shows function bodies that do not access `params`. (R-104.)
8. **No new external HTTP / DB calls inside the six new callables.** Static grep: no `requests.`, no `session.`, no `cur.execute`, no `conn.cursor` in the new function bodies. (R-111.)
9. **No new `action_type` literals.** Static grep: existing seven values only. (R-107.)
10. **No mutation of immutable files.** `parameters.json`, `sources.json`, `program.md`, `prepare.py`, `connector_harness.py`, `connector_registry.json` unchanged in the Phase 4 diff. (Out of scope per §5; verified by `git diff --name-only main...phase4`.)
11. **No new prepare.py columns.** `prepare.py` schema DDL unchanged. (Out of scope per §5.)
12. **Existing 57 tests still pass.** `python3 -m unittest tests.test_discovery -v` shows 57 + 6 = 63 (or 63+) passing, zero failures, zero errors.
13. **Phase 4 commit message.** Single commit with subject like `phase4: H5-H10 hard filter stubs (PASS-WITH-FLAG)` and a body summarizing the six new callables, the two updated tests, and the five-file-contract integrity confirmation.

If any item is false, Agent 3 returns the PR to Agent 2 with specific item(s) called out.

---

## 5. Out of scope for Phase 4

Explicitly NOT done in this phase:

- **Wiring real EPA Envirofacts / USGS NWI / FEMA flood / USGS 3DEP / utility provider service maps / county deed records / conservation easement registries.** All deferred to Phase 5+ data wiring.
- **Cross-querying Fulton ArcGIS Layer 34 (zoning) for H3.** The orchestrator explicitly de-scoped this from Phase 4 ("smaller and shouldn't hit the stream-idle limit"). H3 remains the existing PASS-WITH-FLAG stub at `research.py:530-538`. Phase 4+ (some later phase) will wire it.
- **Modifying `program.md`.** The action_type vocabulary already covers Phase 4's needs (see R-107). No `program.md` edit required.
- **Modifying `parameters.json`.** All five threshold keys (`flood_zones_blocked`, `wetlands_max_pct_of_parcel`, `max_grade_differential_ft`, `min_road_classification`, `max_utility_extension_ft`) are already present at `parameters.json:11-15` and are NOT read by the Phase 4 stubs.
- **Modifying `sources.json`.** No new sources added; the existing `environmental` and `topography` sections suffice for Phase 5+ wiring.
- **Modifying `prepare.py`.** No schema changes. No new tables. No `cycle_id` column on `flagged_items` (deferred per Phase 3 review §5 open question 2 and Phase 3.1 review §4 out-of-scope item 1).
- **Adding any new `prepare.py` columns.** Same as above.
- **Real H1 county polygon.** H1 remains the loose envelope at `research.py:126-128, 482-488`. Replacing with a true county polygon ST_Within check is a Phase 4+ item (some later phase, NOT this Phase 4). Phase 3 review R-20 mitigation already documents the deferral.
- **Convert `parcels.geometry` to `MultiPolygon`.** `prepare.py` mutation; deferred per Phase 3 review R-07.
- **Connectors for any other county.** Phase 11.
- **AI fallback (Playwright + Claude vision).** Phase 12.
- **Scoring (S1–S12), actionability screen, strategy fit, snapshots, memos, the Karpathy experiment loop.** Phases 5–10.
- **Adding any new external HTTP calls.** Phase 4 stubs are pure-Python; no network.

---

## 6. Final verdict

**GO.** All risks are concrete, low-severity, and have explicit mitigations. The pattern is proven by the Phase 3.1 punch-list item-10 test. The two highest-probability "missed-update" failure modes (R-101 and R-105) are explicitly flagged and have mechanical fixes that Agent 2 must include in the same diff.

Risk count by severity:
- **S1:** 1 (R-101 — test_filter_pipeline_order will hard-fail without lockstep update)
- **S2:** 7 (R-102, R-103, R-104, R-105, R-106, R-107, R-108)
- **S3:** 5 (R-109, R-110, R-111, R-112, R-114)

Wait — recount: R-114 is S4 per the §3 register. Let me re-tally precisely.

- **S1:** R-101 → 1
- **S2:** R-102, R-103, R-104, R-105, R-106, R-107, R-108 → 7
- **S3:** R-109, R-110, R-111, R-112 → 4
- **S4:** R-113, R-114 → 2

Total: **14 risks** across 6 categories (per-filter §2.1–§2.6 are foreshadowing future-phase risks, not current-phase risks; the 14 above are the actual Phase 4 risks).

VERDICT: GO — 1 S1, 7 S2, 4 S3, 2 S4, 0 informational.

---

AGENT 1 (sub-agent) DONE — verdict: GO
