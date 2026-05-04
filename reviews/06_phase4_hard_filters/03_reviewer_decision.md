# Phase 4 Reviewer Decision — Hard Filters H5–H10 (PASS-WITH-FLAG stubs)

**Reviewer:** Agent 3 role (sub-agent context).
**Date:** 2026-05-01.
**Branch:** `claude/add-environmental-filters-Lzf3W`.
**Reviewing:**
- Agent 1 risk review at `reviews/06_phase4_hard_filters/01_risk_review.md`
  (R-101..R-114; verdict GO).
- Agent 2 implementation at `research.py` (six new filter callables;
  `_HARD_FILTERS` extended; H3/H4 docstring micro-edits) and
  `tests/test_discovery.py` (two existing tests updated; seven new tests).
- Agent 2 code writer response at
  `reviews/06_phase4_hard_filters/02_code_writer_response.md`.

---

## 1. Verdict at the top

**APPROVE.** All 13 go/no-go gates from `01_risk_review.md` §4 pass on
mechanical and semantic inspection. The diff is minimal and focused: six
new pure-Python filter functions appended after `_h4_flag`, a 4-element
`_HARD_FILTERS` list grown to 10, two existing tests updated in lockstep
with the list extension, two new test classes (`TestPhase4HardFilterStubs`
with six per-filter unit tests, `TestPhase4FilterPipelineEndToEnd` with
one integration test), and optional H3/H4 docstring micro-edits per R-109.
No new HTTP, no new SQL, no new database columns, no new action_type
literals, no parameter reads inside the new bodies, no mutation of any
immutable file.

The Five-File Contract holds: `parameters.json`, `sources.json`,
`program.md`, `prepare.py`, `connector_harness.py`, and
`connector_registry.json` are bytes-identical to HEAD on this branch
(the only `program.md` line difference vs. `main` is the Phase 3.1
human-authorized vocabulary expansion at L127, already shipped on
the prior PR — not a Phase 4 change).

64 tests pass (57 prior + 7 new) in 0.091s, zero failures, zero errors.
The Phase 3.1 strict immutable-write scanner, the no-string-interpolated-SQL
scanner, the no-print scanner, and `test_no_immutable_writes` all still
pass — Phase 4 introduces no regression in any guard.

---

## 2. Gate-by-gate verification

The 13 gates from `01_risk_review.md` §4:

### Gate 1 — Six new callables exist with the correct signature
**PASS.** `_h5_filter`, `_h6_filter`, `_h7_filter`, `_h8_filter`,
`_h9_filter`, `_h10_filter` defined at `research.py:554-611`. Each has
the signature `(parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]) -> _FilterResult`
matching the existing `_h3_flag` / `_h4_flag` pattern.

### Gate 2 — Each returns `_FilterResult("flag", "H<N>", "<non-empty reason>")`
**PASS.** Reasons map cleanly to the data sources from `program.md` L169-L174:
- H5: "EPA Envirofacts + state EPD" — matches `program.md:169` "EPA Envirofacts, state EPD"
- H6: "USGS NWI mapper" — matches `program.md:170` "USGS NWI mapper"
- H7: "county road classification + DOT layer" — matches `program.md:171` "County road classification, DOT"
- H8: "utility provider service map + extension-distance" — matches `program.md:172` "Utility provider service maps, municipality"
- H9: "USGS 3DEP elevation" — matches `program.md:173` "USGS topo, LiDAR/contour data" / `sources.json:114-121` 3DEP entry
- H10: "deed records + conservation easement registry" — matches `program.md:174` "County assessor, deed records"

### Gate 3 — `_HARD_FILTERS` ends with H5–H10 in correct order
**PASS.** `research.py:619-623`:
```python
_HARD_FILTERS: list[Any] = [
    _h1_filter, _h2_filter,
    _h3_flag, _h4_flag,
    _h5_filter, _h6_filter, _h7_filter, _h8_filter, _h9_filter, _h10_filter,
]
```
Reject filters precede flag-only stubs as required by the
`_process_parcel` two-loop pattern. Comment block above the list now
documents the ordering invariant per R-102 mitigation.

### Gate 4 — `test_filter_pipeline_order` updated to expect 10 elements
**PASS.** `tests/test_discovery.py:438-446`:
```python
self.assertEqual(ids, [
    "_h1_filter", "_h2_filter",
    "_h3_flag", "_h4_flag",
    "_h5_filter", "_h6_filter", "_h7_filter", "_h8_filter", "_h9_filter", "_h10_filter",
])
```

### Gate 5 — `TestHappyPathDryRun` flag count assertion bumped to >=32
**PASS.** `tests/test_discovery.py:702-705` updated from `>= 8` to `>= 32`
with the new comment math: `4 parcels × 8 flags (H3, H4, H5, H6, H7, H8, H9, H10) = 32 minimum`.

### Gate 6 — Six per-filter unit tests
**PASS.** `TestPhase4HardFilterStubs.test_h5_returns_flag` through
`test_h10_returns_flag` at `tests/test_discovery.py:1009-1046`. Each test
asserts:
- `result.action == "flag"`
- `result.filter_id == "H<N>"`
- non-empty `result.reason`
- a case-insensitive token match against the eventual data source from `program.md`

The `TestPhase4FilterPipelineEndToEnd.test_h5_through_h10_emit_flag_rows`
test goes further — it runs the full `_process_parcel` path and asserts
each H5..H10 marker appears in the `flagged_items` insert descriptions.

### Gate 7 — No reads of `params[...]` inside the six new callables
**PASS.** Static grep `grep -nE 'params\[' research.py` shows the only
`params[...]` references inside the H5–H10 functions are in **docstrings**
(lines 567, 577, 587, 597) — not in function bodies. Each body is exactly:
```python
return _FilterResult("flag", "H<N>", "<reason>")
```
with no parameter access.

### Gate 8 — No new external HTTP / DB calls inside the six new callables
**PASS.** Bodies are single `return` statements. No `requests.`, no
`session.`, no `cur.execute`, no `conn.cursor` in any of the six new
function bodies. (The other `requests.`/cursor occurrences in the file
are in pre-existing code, untouched by Phase 4.)

### Gate 9 — No new `action_type` literals
**PASS.** Phase 4 stubs ride on the existing `_flag` insert path
(research.py:660-681), which writes to `flagged_items` with
`flag_type='data_gap'`. The `research_log.action_type` for the parcel
itself remains `discovery` (research.py:1009-1012). The only string
literals at any `action_type` argument site are the existing
`discovery|discovery_empty|scoring|rescore|rejection|flag|abort` set.

### Gate 10 — No mutation of immutable files
**PASS.** `git diff HEAD -- parameters.json sources.json program.md prepare.py connector_harness.py connector_registry.json`
returns empty output. The only files modified in the working directory
are `research.py` and `tests/test_discovery.py`, plus the three new files
in `reviews/06_phase4_hard_filters/`.

The `program.md | 2 +-` row in `git diff main..HEAD --stat` is the
Phase 3.1 vocabulary expansion (commit `41ff7bb`), which was a
human-authorized between-runs edit to align the `action_type` enum with
what `research.py` emits. It is NOT a Phase 4 change.

### Gate 11 — No new prepare.py columns / DDL changes
**PASS.** `prepare.py` is unchanged on this branch.

### Gate 12 — All tests pass (57 prior + new)
**PASS.** Full `python3 -m unittest tests.test_discovery -v` run completes
in 0.091s with 64 tests passing — 57 prior + 7 new (6 per-filter unit
tests + 1 end-to-end integration test). Output captured in §5 below.

### Gate 13 — Commit message format
**PASS.** Will be honored at commit time per the spec ("phase4: hard
filters H5-H10 PASS-WITH-FLAG stubs" subject + HEREDOC body summarizing
the change, listing the six new filters, referencing BUILD_PHASES.md
Phase 4 exit criteria, citing the reviews directory paper trail, ending
with the `https://claude.ai/code/...` footer per project convention).

---

## 3. Per-risk verification

All 14 R-1XX risks from `01_risk_review.md` §3 are addressed in Agent 2's
response (`02_code_writer_response.md` §2). I independently verified each:

| Risk | Severity | Agent 2 disposition | Agent 3 verification |
|------|----------|---------------------|----------------------|
| R-101 | S1 | addressed | `test_filter_pipeline_order` asserts the full 10-element list (PASS) |
| R-102 | S2 | addressed | New filters appended at end; ordering invariant documented |
| R-103 | S2 | accepted | Volume note in module comment; Phase 9 will summarize |
| R-104 | S2 | addressed | Static grep confirms no `params[...]` body reads in new callables |
| R-105 | S2 | addressed | Flag count assertion bumped to `>= 32` with new comment math |
| R-106 | S2 | addressed | Six per-filter unit tests + 1 end-to-end test |
| R-107 | S2 | addressed | Zero new action_type literals |
| R-108 | S2 | accepted | Re-run semantics inherit from Phase 3.1 cycle-id collision check |
| R-109 | S3 | addressed (optional adopted) | H3/H4 docstrings + reason strings updated to "(Phase 5+)" |
| R-110 | S3 | addressed | `_h9_filter` docstring foreshadows S3 sharing |
| R-111 | S3 | addressed | No HTTP / DB activity in any new body |
| R-112 | S3 | addressed | No other test hard-codes `_HARD_FILTERS` length / H3-H4 reason |
| R-113 | S4 | addressed | Each new docstring documents "Replace body in Phase 5+; preserve signature" |
| R-114 | S4 | addressed | Reason strings are short, professional, follow H3/H4 precedent |

I also independently checked for risks Agent 1 could have missed:

- **R-CI-2 / R-CI-3 from Phase 3.1** — `test_no_immutable_writes` and the
  strict variant `TestPhase31ImmutableWritesStrict.test_strict_no_immutable_writes`
  both still pass after Phase 4. No regression.
- **R-05 from Phase 3** — `test_no_string_interpolated_sql` (every
  `cur.execute` first arg must be a `Constant` or `Name`). Still passes.
  Phase 4 introduces no SQL.
- **R-39 from Phase 3** — `test_no_print_in_run_discovery_cycle`. Still
  passes. No new `print()` calls.
- **R-01 / R-30 / R-04 / R-43** static checks at `TestStaticChecks` — all
  still pass.

Nothing material was missed.

---

## 4. Style / consistency observations (non-blocking)

### Naming inconsistency: `_h3_flag` / `_h4_flag` vs `_h5_filter` ... `_h10_filter`

Per Agent 1's R-101 mitigation and the orchestrator's directive ("do NOT
rename them"), the H3/H4 names retain the `_flag` suffix while the new
H5–H10 callables use the `_filter` suffix. This is intentional but is a
code smell. Future cleanup options (NOT in Phase 4 scope):

a. Rename `_h3_flag` → `_h3_filter` and `_h4_flag` → `_h4_filter` in a
   dedicated cosmetic-only commit on a follow-up branch, with the
   `test_filter_pipeline_order` assertion updated in lockstep. Trivial,
   ~5 lines of diff.
b. Or document the convention explicitly: `_flag` suffix means
   "stub-since-day-one"; `_filter` means "added in Phase 4+ as a stub but
   intended to become a real filter." If we adopt this convention, the
   docstring above `_HARD_FILTERS` should call it out.

Either is fine. Recommend (a) at the next opportunistic-cleanup window
after Phase 5 lands.

### Docstring length

The new H5–H10 docstrings are single-line but long (170-280 chars). They
satisfy R-104 (parameter-key references for the Phase 5+ implementer),
R-110 (S3 sharing note for H9), and R-113 (signature-preservation note).
The information density is appropriate; line length is fine for a
docstring even though it's beyond PEP 8's 79-char body line limit
(docstrings have their own conventions).

### Consistency with H3/H4

The `(Phase 4)` → `(Phase 5+)` micro-edits on H3/H4 reason strings
(R-109) eliminate the prior inconsistency where H3/H4 said "Phase 4" and
the new H5–H10 stubs say "Phase 5+". The Phase 5+ data-wiring
implementer can now grep for `Phase 5+` across all eight stubs and see
the full set. This is a net improvement.

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
Ran 64 tests in 0.091s

OK
```

64 tests pass. Zero failures, zero errors. Same total Agent 2 reported in
`02_code_writer_response.md` §5 (independently re-run by Agent 3).

---

## 6. Five-File Contract integrity

- `parameters.json` — unchanged on this branch (verified: `git diff HEAD -- parameters.json` empty).
- `sources.json` — unchanged on this branch (verified).
- `program.md` — unchanged on this branch. The `program.md | 2 +-` line in
  `git diff main..HEAD --stat` is the Phase 3.1 human-authorized
  vocabulary expansion at L127 (commit `41ff7bb`), shipped on the prior
  PR. Phase 4 introduces no `program.md` edit.
- `prepare.py` — unchanged on this branch (verified).
- `connector_harness.py` — unchanged on this branch (verified).
- `connector_registry.json` — unchanged on this branch (verified).

The only modified files in the working tree are:
- `research.py` (Phase 4 filter additions + H3/H4 docstring micro-edits)
- `tests/test_discovery.py` (two existing tests updated; two new test classes)
- `reviews/06_phase4_hard_filters/` (three new review docs)

The strict immutable-write test
(`TestPhase31ImmutableWritesStrict.test_strict_no_immutable_writes`)
passes, confirming `research.py` does not write to any locked file via
`open()`, `Path.open()`, `Path.write_text()`, `json.dump()`, or
`csv.writer()`.

---

## 7. Phase 4 BUILD_PHASES.md exit criteria

`BUILD_PHASES.md:74-80`:

> ### Phase 4: Hard Filters Complete
>
> Goal: All 10 hard filters operational, including environmental and
> federal data sources.
>
> Use the three-agent workflow. Add hard filters H5-H10 covering
> environmental contamination (EPA Envirofacts), wetlands (USGS NWI),
> road access, utility availability, topography (USGS 3DEP), and
> ownership availability.
>
> **Exit criteria**: A discovery cycle filters parcels through all 10
> hard filters. Rejected parcels have rejection reasons logged.

Phase 4 satisfies the exit criteria with this caveat: **H5–H10 are
PASS-WITH-FLAG stubs, not reject-capable hard filters yet.** A
discovery cycle now filters parcels through all 10 hard filters in the
sense that every parcel that passes H1+H2 is evaluated by H3–H10 and
emits a `flagged_items` row of `flag_type='data_gap'` per stub. The
"rejection reason" surface lives in two places: H1/H2 emit `rejection`
rows in `research_log` for true rejects, and H3–H10 emit `data_gap`
flags in `flagged_items` with a resolution-hint description that Phase 5+
data wiring will close.

The orchestrator's directive (per the Phase 4 risk review §1 Verdict at
the top) was explicit: Phase 4 is the stub-only phase; Phase 5+ wires the
real data sources. This decision aligns with that scope.

---

## 8. Followups for future phases

These are not blockers; they are notes for the next agent context.

1. **Naming cleanup** (cosmetic): rename `_h3_flag`/`_h4_flag` to
   `_h3_filter`/`_h4_filter` in a follow-up commit so all 8 stubs share
   the `_filter` suffix. Update `test_filter_pipeline_order` assertion in
   lockstep. ~5 lines of diff. Optimal timing: between Phase 4 commit and
   Phase 5 implementation.

2. **Phase 5+ data wiring** (substantive):
   - H5: EPA Envirofacts NPL/RCRA bulk pull + state EPD GEOS scrape +
     500 ft adjacency PostGIS join.
   - H6: USGS NWI WMS/WFS + `ST_Area(ST_Intersection(...))` polygon overlap.
   - H7: county road classification layer + DOT functional-class normalizer.
   - H8: utility provider service maps (heavy AI fallback territory).
   - H9: USGS 3DEP raster + zonal max-min (share helper with scored S3 per R-110).
   - H10: county deed records + conservation easement registries (web-scrape only).

   Each is a separate Phase 5+ sub-phase per `BUILD_PHASES.md` Phase 5
   structure. Phase 5+ should preserve the `(parcel, conn, params) -> _FilterResult`
   signature and the function-symbol names per R-113.

3. **Re-process flagged parcels** (Phase 3 multipolygon-reduction note):
   when Phase 5+ wires real H6 wetlands data, the parcels flagged for
   multipolygon-largest-ring reduction in Phase 3 (`research.py:1011-1017`)
   may have wetland coverage that the kept-largest-ring missed. Phase 5+
   wetland implementation must re-process those flagged parcels using the
   full multipolygon geometry, not the reduced one.

4. **Snapshot summarization** (Phase 9): per-parcel snapshots will need
   to summarize, not enumerate, the 8+ data_gap flags per parcel. Group
   by `flag_type='data_gap'` with a count and a representative resolution
   hint.

---

## 9. Decision

**APPROVE.** Phase 4 lands at the next commit on
`claude/add-environmental-filters-Lzf3W`. The six new hard filter stubs
(H5–H10) are wired into `_HARD_FILTERS` correctly, the existing tests
are updated in lockstep, the new tests exercise both unit and end-to-end
behavior, all 64 tests pass, and the Five-File Contract holds. Phase 5+
may proceed.

The Phase 4 commit message will end with the
`https://claude.ai/code/session_012iJMZKs2ya1Yjz556uDrVn` footer per the
project's commit message convention.

---

AGENT 3 (sub-agent) DONE — verdict: APPROVE.
