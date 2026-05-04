# Phase 4 Code Writer Response — Hard Filters H5–H10 (PASS-WITH-FLAG stubs)

**Author:** Agent 2 role (sub-agent context).
**Date:** 2026-05-01
**Branch:** continues from Phase 3.1 at `claude/revalidate-phase-3-ytHPT` (orchestrator-confirmed).
**Inputs read:** `reviews/06_phase4_hard_filters/01_risk_review.md`, `CLAUDE.md`, `AUTORESEARCH_MECHANICS.md`, `program.md` (Hard Filters table, L163-L174), `parameters.json`, `research.py` (full), `tests/test_discovery.py` (relevant blocks), `reviews/04_phase3_fulton_discovery/02_code_writer_response.md` (format precedent).

**Deliverables produced:**
- `research.py` — six new filter callables `_h5_filter`..`_h10_filter` inserted after `_h4_flag`; `_HARD_FILTERS` list extended; comment above the list updated; H3/H4 docstring micro-edits ("Phase 4" → "Phase 5+") per R-109.
- `tests/test_discovery.py` — `test_filter_pipeline_order` updated to expect 10 elements; `test_two_feature_happy_path` flag-row assertion bumped from 8 to 32; new test class `TestPhase4HardFilterStubs` with 6 unit tests; new test class `TestPhase4FilterPipelineEndToEnd` with 1 integration test.
- This document.

---

## 1. Summary

Phase 4 lands H5–H10 as PASS-WITH-FLAG stubs that mirror the proven Phase 3 H3/H4 pattern: each new callable returns `_FilterResult("flag", "H<N>", "<reason referencing the eventual data source> (Phase 5+)")` and lets the parcel through, emitting one `flagged_items` row of `flag_type='data_gap'` per stub via the existing `_process_parcel` second filter loop. No new HTTP, no new SQL, no new database columns, no new `action_type` literals, no parameter reads inside the new bodies, no mutation of immutable files. Six new filter functions, six new unit tests, one new integration test, two existing tests updated in lockstep with the `_HARD_FILTERS` extension. The diff is intentionally small — exactly what the orchestrator asked for.

---

## 2. Per-risk responses

The 14 R-1XX risks from `01_risk_review.md` are addressed below. **(addressed)** = the code mitigates the risk and an acceptance test passes; **(accepted)** = the risk is acknowledged with explicit rationale (no code change); **(deferred)** = explicitly out-of-Phase-4 scope per the risk review §5.

### 2.1 R-101 (S1) — `test_filter_pipeline_order` lockstep update

**addressed.** `tests/test_discovery.py:438-446` now asserts the full 10-element list `["_h1_filter", "_h2_filter", "_h3_flag", "_h4_flag", "_h5_filter", "_h6_filter", "_h7_filter", "_h8_filter", "_h9_filter", "_h10_filter"]`. The H3/H4 names retain their `_flag` suffix per the orchestrator's directive ("do NOT rename them"); the new H5–H10 callables use `_filter` per Agent 1's recommendation. Test passes (see §5).

### 2.2 R-102 (S2) — Pipeline ordering: append after reject filters

**addressed.** The new callables are appended at the end of `_HARD_FILTERS` (research.py:619-623) so the H1/H2 short-circuit semantics in `_process_parcel:975-996` are preserved. The comment above the list is updated to document the invariant: "Reject filters MUST come first; PASS-WITH-FLAG stubs follow." A second sentence flags that "When Phase 5+ replaces a stub with a reject-capable real filter, ordering must be revisited." Acceptance test is the updated `test_filter_pipeline_order` from R-101.

### 2.3 R-103 (S2) — `flagged_items` row volume per parcel grows ~3-4x

**accepted.** No code change. Phase 4 stubs unconditionally emit one `data_gap` row per filter per passing parcel (8 rows in steady state: H3, H4, H5, H6, H7, H8, H9, H10), bumping the per-cycle volume from ~2,000 to ~8,000 rows for a typical Fulton run. Postgres handles this trivially. The comment above `_HARD_FILTERS` documents the volume expectation; the Phase 9 snapshot generator will summarize rather than enumerate (this is a Phase 9 design constraint, not a Phase 4 blocker). Phase 5+ data wiring closes flags via bulk UPDATE on the `Phase X: resolve HN ...` resolution hint pattern (R-22, R-23 from Phase 3 review).

### 2.4 R-104 (S2) — No `params[...]` reads inside the new callables

**addressed.** Each new filter body is exactly `return _FilterResult("flag", "H<N>", "<reason>")`. Zero parameter lookups. The eventual parameter keys (`wetlands_max_pct_of_parcel`, `min_road_classification`, `max_utility_extension_ft`, `max_grade_differential_ft`) are referenced only inside the docstrings so a Phase 5+ implementer can find them without re-deriving from `parameters.json`. Static-grep confirmation: `grep -n "params\[" research.py` shows the only `params[...]` references in the H5–H10 functions are in docstrings (lines 567, 577, 587, 597); no body reads.

### 2.5 R-105 (S2) — `TestHappyPathDryRun` flag count assertion

**addressed.** `tests/test_discovery.py:701-705` updated from `>= 8` to `>= 32` with the new comment math: "4 parcels × 8 flags (H3, H4, H5, H6, H7, H8, H9, H10) = 32 minimum (multipolygon flag may add more)." Used `>=` rather than `==` to keep the assertion robust against future multipolygon parcels in `arcgis_query_two_features.json`. Test passes.

### 2.6 R-106 (S2) — Per-filter unit test coverage

**addressed.** New test class `TestPhase4HardFilterStubs` adds six unit tests (`test_h5_returns_flag` through `test_h10_returns_flag`) plus a shared `_assert_flag` helper. Each test calls the filter with `({}, None, _passing_params())`, asserts `action == "flag"`, `filter_id == "H<N>"`, non-empty reason, and a case-insensitive token match against the eventual data source name from `program.md` (e.g., "EPA"/"Envirofacts" for H5, "NWI"/"wetlands" for H6, etc.). All six pass.

### 2.7 R-107 (S2) — `action_type` vocabulary unchanged

**addressed.** Phase 4 stubs ride on the existing `_flag` insert path (research.py:660-681), which writes to `flagged_items` with `flag_type='data_gap'`. The `research_log.action_type` for the parcel itself remains `discovery` (research.py:1009-1012) — Phase 4 introduces zero new action_type literals. Static-grep confirmation: no new string literals appear at any `action_type` argument site in the new code.

### 2.8 R-108 (S2) — Idempotency / re-run semantics

**accepted.** Phase 3.1's `TestPhase31CycleIdCollision.test_cycle_id_collision_aborts` already proves a re-run of the same cycle aborts at `research.py:1172-1175`. Phase 4 inherits this unchanged. The cumulative-volume note (~6 new rows per re-discovery cycle on top of existing H3/H4) is bounded and consistent with the cycle-id-in-description deduplication pattern from R-38.

### 2.9 R-109 (S3) — H3/H4 docstring micro-edit

**addressed (optionally adopted).** `_h3_flag` and `_h4_flag` docstrings and reason strings updated from "(Phase 4)" to "(Phase 5+)" so they line up with the H5–H10 stubs' "(Phase 5+)" language and reflect the actual resolution phase. Single-line edits; no behavioral change. The Phase 5+ data wiring will still find these flag rows via the `H3 zoning unjoined%` / `H4 flood unjoined%` LIKE patterns. The "Replace body in Phase 5+; preserve signature" sentence was added to both H3 and H4 to match the Phase 4 stubs' signature-preservation guidance per R-113.

### 2.10 R-110 (S3) — H9 + S3 share a topo computation (architectural foreshadowing)

**addressed.** `_h9_filter`'s docstring includes the sentence "Phase 5+ should share the grade-differential computation with scored parameter S3." This surfaces the shared dependency without adding any code today. No additional changes.

### 2.11 R-111 (S3) — No new external HTTP calls

**addressed.** The six new filter bodies consist of a single `return _FilterResult(...)` statement each. No `requests.`, no `_DiscoverySession`, no `cur.execute`, no `conn.cursor`, no network or DB activity. Static-grep verifiable.

### 2.12 R-112 (S3) — Backwards compatibility

**addressed.** Beyond R-101 and R-105, no other test in `tests/test_discovery.py` hard-codes the `_HARD_FILTERS` length or the H3/H4 reason strings. `TestHardFilters.test_filter_pipeline_extensible` uses `len(original) + 1` (robust). `TestPhase31FilterPipelineExtensibleExecutes.test_synthetic_h5_filter_emits_marker_flag` matches on its own marker string (also robust). Full-suite run (§5) confirms all 64 tests pass — 57 prior + 6 new H5–H10 stub tests + 1 new end-to-end test.

### 2.13 R-113 (S4) — Phase 5+ will replace the stub bodies

**addressed.** Each new docstring includes the sentence "Replace body in Phase 5+, preserve signature." (also added to H3 and H4). This warns the Phase 5+ implementer not to delete the function symbol or change the `(parcel, conn, params) -> _FilterResult` signature, which would break the `_HARD_FILTERS` iteration in `_process_parcel`.

### 2.14 R-114 (S4) — Stub reason strings are user-facing

**addressed.** Reason strings are short, professional, and follow the H3/H4 precedent's tone: `"H<N> <topic> unjoined: pending <data source> wiring (Phase 5+)"`. They surface in `flagged_items.description` (after the `cycle=...` prefix from `_flag` at research.py:670) and read cleanly in dashboards / Phase 9 snapshots without revealing internal phase numbers in a confusing way.

### 2.15 Future-phase per-filter risks (§2.1–§2.6 of risk review)

**deferred.** All Phase 5+ data-wiring concerns (EPA Envirofacts spatial joins, USGS NWI raster intersection, county road taxonomy normalization, fragmented utility providers, USGS 3DEP zonal stats, conservation easement web-scrape) are explicitly out-of-scope for Phase 4 per the risk review §5 and the orchestrator's "no new HTTP" directive. Each is captured in the corresponding stub's docstring so a Phase 5+ Agent 1 review can pick them up.

---

## 3. Files modified

- `research.py` — six new filter callables (`_h5_filter`, `_h6_filter`, `_h7_filter`, `_h8_filter`, `_h9_filter`, `_h10_filter`) inserted after `_h4_flag`; `_HARD_FILTERS` extended to 10 entries; comment above the list updated to document the reject-first ordering invariant and the Phase 4 append; H3/H4 docstring + reason-string "(Phase 4)" → "(Phase 5+)" micro-edits per R-109; "Replace body in Phase 5+; preserve signature" guidance added across all eight stubs per R-113.
- `tests/test_discovery.py` — `test_filter_pipeline_order` updated to assert 10-element list; `TestHappyPathDryRun.test_two_feature_happy_path` flag-row assertion bumped from `>= 8` to `>= 32`; new `TestPhase4HardFilterStubs` class with 6 unit tests; new `TestPhase4FilterPipelineEndToEnd` class with 1 integration test.

No other files modified. `parameters.json`, `program.md`, `sources.json`, `prepare.py`, `connector_harness.py`, `connector_registry.json`, and everything else in `reviews/` (outside of this response) are untouched.

---

## 4. Tests added / updated

Added (new):
- `TestPhase4HardFilterStubs.test_h5_returns_flag`
- `TestPhase4HardFilterStubs.test_h6_returns_flag`
- `TestPhase4HardFilterStubs.test_h7_returns_flag`
- `TestPhase4HardFilterStubs.test_h8_returns_flag`
- `TestPhase4HardFilterStubs.test_h9_returns_flag`
- `TestPhase4HardFilterStubs.test_h10_returns_flag`
- `TestPhase4FilterPipelineEndToEnd.test_h5_through_h10_emit_flag_rows`

Updated (existing):
- `TestHardFilters.test_filter_pipeline_order` — 4-element → 10-element expected list (R-101).
- `TestHappyPathDryRun.test_two_feature_happy_path` — flag-row min `>= 8` → `>= 32` and updated comment math (R-105).

Total test count: **64 passing** (57 baseline + 7 new).

---

## 5. Test run output

```
$ python3 -m unittest tests.test_discovery -v
test_empty_corridor_yields_no_features (tests.test_discovery.TestArcgisPagination.test_empty_corridor_yields_no_features) ... ok
test_pagination_terminates_on_exceeded_false (tests.test_discovery.TestArcgisPagination.test_pagination_terminates_on_exceeded_false) ... ok
test_coerce_float (tests.test_discovery.TestCoercion.test_coerce_float) ... ok
test_coerce_int (tests.test_discovery.TestCoercion.test_coerce_int) ... ok
test_cycle_id_format (tests.test_discovery.TestCycleId.test_cycle_id_format) ... ok
test_cycle_id_unique_within_second (tests.test_discovery.TestCycleId.test_cycle_id_unique_within_second) ... ok
test_two_feature_happy_path (tests.test_discovery.TestHappyPathDryRun.test_two_feature_happy_path) ... ok
test_filter_pipeline_extensible (tests.test_discovery.TestHardFilters.test_filter_pipeline_extensible)
R-42: Phase 4+ can append H5 onwards without rewriting. ... ok
test_filter_pipeline_order (tests.test_discovery.TestHardFilters.test_filter_pipeline_order)
Pipeline is H1 → H2 → H3-flag → H4-flag → H5..H10 stubs (R-24, R-101). ... ok
test_h1_inside_envelope (tests.test_discovery.TestHardFilters.test_h1_inside_envelope) ... ok
test_h1_outside_envelope (tests.test_discovery.TestHardFilters.test_h1_outside_envelope) ... ok
test_h2_above_bound (tests.test_discovery.TestHardFilters.test_h2_above_bound) ... ok
test_h2_at_lower_bound (tests.test_discovery.TestHardFilters.test_h2_at_lower_bound) ... ok
test_h2_at_upper_bound (tests.test_discovery.TestHardFilters.test_h2_at_upper_bound) ... ok
test_h2_below_bound (tests.test_discovery.TestHardFilters.test_h2_below_bound) ... ok
test_h2_none (tests.test_discovery.TestHardFilters.test_h2_none) ... ok
test_harness_failing_aborts_cycle (tests.test_discovery.TestHarnessGate.test_harness_failing_aborts_cycle) ... ok
test_harness_raise_treated_as_failing (tests.test_discovery.TestHarnessGate.test_harness_raise_treated_as_failing) ... ok
test_market_not_supported_raises (tests.test_discovery.TestHarnessGate.test_market_not_supported_raises) ... ok
test_addr1_and_addr2 (tests.test_discovery.TestMailingComposition.test_addr1_and_addr2) ... ok
test_addr1_only (tests.test_discovery.TestMailingComposition.test_addr1_only) ... ok
test_attn_stripped (tests.test_discovery.TestMailingComposition.test_attn_stripped) ... ok
test_co_stripped (tests.test_discovery.TestMailingComposition.test_co_stripped) ... ok
test_estate (tests.test_discovery.TestOwnerTypeInference.test_estate) ... ok
test_government_takes_priority_over_trust (tests.test_discovery.TestOwnerTypeInference.test_government_takes_priority_over_trust) ... ok
test_individual_default (tests.test_discovery.TestOwnerTypeInference.test_individual_default) ... ok
test_none_owner_returns_unknown (tests.test_discovery.TestOwnerTypeInference.test_none_owner_returns_unknown) ... ok
test_trump_is_not_trust (tests.test_discovery.TestOwnerTypeInference.test_trump_is_not_trust) ... ok
test_trust_with_trailing_space_token (tests.test_discovery.TestOwnerTypeInference.test_trust_with_trailing_space_token) ... ok
test_parcel_id_is_county_prefixed (tests.test_discovery.TestParcelMapping.test_parcel_id_is_county_prefixed) ... ok
test_state_plane_response_is_skipped (tests.test_discovery.TestParcelMapping.test_state_plane_response_is_skipped) ... ok
test_cycle_id_collision_aborts (tests.test_discovery.TestPhase31CycleIdCollision.test_cycle_id_collision_aborts) ... ok
test_harness_degraded_emits_null_parcel_id (tests.test_discovery.TestPhase31CycleLevelFlagNullsParcelId.test_harness_degraded_emits_null_parcel_id) ... ok
test_pagination_terminates_on_short_page_when_field_absent (tests.test_discovery.TestPhase31FallbackPagination.test_pagination_terminates_on_short_page_when_field_absent) ... ok
test_full_schema_returns_true (tests.test_discovery.TestPhase31FieldMappingDrift.test_full_schema_returns_true) ... ok
test_missing_landacres_returns_false_with_field_listed (tests.test_discovery.TestPhase31FieldMappingDrift.test_missing_landacres_returns_false_with_field_listed) ... ok
test_synthetic_h5_filter_emits_marker_flag (tests.test_discovery.TestPhase31FilterPipelineExtensibleExecutes.test_synthetic_h5_filter_emits_marker_flag) ... ok
test_harness_degraded_proceeds_with_flag (tests.test_discovery.TestPhase31HarnessDegradedProceeds.test_harness_degraded_proceeds_with_flag) ... ok
test_strict_no_immutable_writes (tests.test_discovery.TestPhase31ImmutableWritesStrict.test_strict_no_immutable_writes) ... ok
test_h5_through_h10_emit_flag_rows (tests.test_discovery.TestPhase4FilterPipelineEndToEnd.test_h5_through_h10_emit_flag_rows) ... ok
test_h10_returns_flag (tests.test_discovery.TestPhase4HardFilterStubs.test_h10_returns_flag) ... ok
test_h5_returns_flag (tests.test_discovery.TestPhase4HardFilterStubs.test_h5_returns_flag) ... ok
test_h6_returns_flag (tests.test_discovery.TestPhase4HardFilterStubs.test_h6_returns_flag) ... ok
test_h7_returns_flag (tests.test_discovery.TestPhase4HardFilterStubs.test_h7_returns_flag) ... ok
test_h8_returns_flag (tests.test_discovery.TestPhase4HardFilterStubs.test_h8_returns_flag) ... ok
test_h9_returns_flag (tests.test_discovery.TestPhase4HardFilterStubs.test_h9_returns_flag) ... ok
test_owner_name_passthrough (tests.test_discovery.TestPiiHandling.test_owner_name_passthrough) ... ok
test_multipolygon_centroid_uses_kept_outer (tests.test_discovery.TestPolygonAndSrid.test_multipolygon_centroid_uses_kept_outer) ... ok
test_multipolygon_keeps_largest_outer (tests.test_discovery.TestPolygonAndSrid.test_multipolygon_keeps_largest_outer) ... ok
test_simple_polygon_to_wkt (tests.test_discovery.TestPolygonAndSrid.test_simple_polygon_to_wkt) ... ok
test_srid_sanity_accepts_wgs84 (tests.test_discovery.TestPolygonAndSrid.test_srid_sanity_accepts_wgs84) ... ok
test_srid_sanity_rejects_state_plane (tests.test_discovery.TestPolygonAndSrid.test_srid_sanity_rejects_state_plane) ... ok
test_unsafe_field_name_rejected (tests.test_discovery.TestQueryParamBuilder.test_unsafe_field_name_rejected) ... ok
test_where_clause_only_int_bounds (tests.test_discovery.TestQueryParamBuilder.test_where_clause_only_int_bounds) ... ok
test_negative_offset (tests.test_discovery.TestSafeCachePath.test_negative_offset) ... ok
test_unsafe_corridor (tests.test_discovery.TestSafeCachePath.test_unsafe_corridor) ... ok
test_unsafe_cycle_id (tests.test_discovery.TestSafeCachePath.test_unsafe_cycle_id) ... ok
test_valid_path (tests.test_discovery.TestSafeCachePath.test_valid_path) ... ok
test_corridor_bboxes_match_appendix (tests.test_discovery.TestStaticChecks.test_corridor_bboxes_match_appendix)
R-04: corridor bboxes match appendix L266-L283 verbatim. ... ok
test_dispatch_table_has_fulton (tests.test_discovery.TestStaticChecks.test_dispatch_table_has_fulton)
R-43: discovery dispatch table is populated for Fulton. ... ok
test_no_immutable_writes (tests.test_discovery.TestStaticChecks.test_no_immutable_writes)
R-01: research.py never writes parameters.json / sources.json / program.md. ... ok
test_no_print_in_run_discovery_cycle (tests.test_discovery.TestStaticChecks.test_no_print_in_run_discovery_cycle)
R-39: run_discovery_cycle and helpers do not call print(). ... ok
test_no_string_interpolated_sql (tests.test_discovery.TestStaticChecks.test_no_string_interpolated_sql)
R-05: every cursor.execute() first arg is a Constant or Name (module-level SQL). ... ok
test_sources_dir_in_gitignore (tests.test_discovery.TestStaticChecks.test_sources_dir_in_gitignore)
R-30: sources/ is gitignored so cached PII is not committed. ... ok

----------------------------------------------------------------------
Ran 64 tests in 0.098s

OK
```

All 64 tests pass — 57 prior + 6 new H5–H10 stub tests + 1 new end-to-end test. No live network calls; no real DB; no fixture mutations.

---

## 6. Sign-off

AGENT 2 (sub-agent) DONE — handoff to Agent 3
