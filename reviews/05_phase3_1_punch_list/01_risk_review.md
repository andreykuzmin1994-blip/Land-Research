# Phase 3.1 Risk and Architecture Review — Punch-List Hardening

**Reviewer:** Agent 1 role, completed by orchestrator (sub-agent timeout
pattern from Phase 3 persists; see `04_independent_revalidation.md` §0).
**Date:** 2026-05-01.
**Branch:** `claude/revalidate-phase-3-ytHPT`.
**Scope:** the 10-item amended punch list in
`reviews/04_phase3_fulton_discovery/04_independent_revalidation.md` §6.

---

## 1. Summary verdict

**GO.** All 10 punch-list items are bounded, mechanical, and orthogonal to
the working Phase 3 contract. Two are real bugs that need surgical fixes
(items 2, 8), one is a spec/code alignment edit (item 1), one is a docstring
note (item 3), one is a CI workflow addition (item 5), and the rest are
test additions or strengthenings (items 4, 6, 7, 9, 10).

The only architecturally sensitive change is **item 8** (multi-polygon
centroid fix), because it changes the return shape of `_arcgis_polygon_to_wkt`
from `(wkt, bool)` to `(wkt, bool, kept_outer)`. Every existing caller and
test must be updated in lockstep. Risk: a missed call site silently uses
the old `(wkt, bool)` unpacking and crashes at runtime with a tuple-size
mismatch.

---

## 2. Per-item risk and mitigation

### Item 1 — `program.md` action_type vocabulary expansion

**Change:** Append `abort` and `discovery_empty` to the `action_type`
enumeration at `program.md:127`.

**Risk:** None worth naming. `program.md` is human-edited between runs;
this is a pure spec edit. No code reads the enumeration; nothing to break.

**Mitigation:** None needed.

### Item 2 — Replace `parcel_id="(none)"` with `None`

**Change:** `research.py:1056` and `:1205` — replace the string `"(none)"`
with Python `None` so psycopg sends SQL NULL into `flagged_items.parcel_id`.

**Risk:** None worth naming. The column is already nullable (verified in
`prepare.py` schema). The only consequence is correct semantics for future
joins.

**Mitigation:** Add a test that loads the harness=degraded fixture, runs
the cycle, and asserts the flag row's `params[1]` (parcel_id) is `None`.
Combine with Item 9 below.

### Item 3 — `_DiscoverySession` docstring note

**Change:** Add a sentence to the class docstring at `research.py:246-251`
clarifying the class is single-threaded by design and that the per-host
spacing is conservative-but-correct under concurrent use.

**Risk:** None.

### Item 4 — Fallback-pagination test (R-13 fallback path)

**Change:** New test fixture + test exercising
`exceededTransferLimit` absent + `len(features) < page_size` →
loop terminates without extra round-trip.

**Risk:** Test could pass for the wrong reason if it asserts "no extra
call" but the consumer break-on-empty path is what actually terminates.

**Mitigation:** The new fixture should have `len(features) == page_size - 1`
(one short of the page) and the assertion should count `_MockSession.calls
== 1`, proving termination on the short-page heuristic, not the
`not features` branch.

### Item 5 — Live PostGIS CI workflow

**Change:** New `.github/workflows/discovery-fulton.yml` with a postgres+postgis
service container that:
1. Installs requirements
2. Applies the schema via `python -c "import prepare; ..."`
3. Runs a happy-path UPSERT against live PostGIS using a fixture parcel
4. Asserts `ST_IsValid(geometry)=true` and `ST_Within(centroid, geometry)=true`

**Risks:**
- **R-CI-1** — adding a `DATABASE_URL`-secret-dependent workflow that fails
  with a confusing error if the secret is unset. The existing
  `validate-phase1.yml` already has this pattern; we'll match.
- **R-CI-2** — service container postgres+postgis startup is slow (~30s).
  Mitigate by setting `timeout-minutes: 10` per job, separate offline-tests
  job from live-postgis job (parallel).
- **R-CI-3** — the test must spin up a *fresh* DB per run (no shared state),
  so we use the service container's auto-created DB and pass its DSN via env.
  Don't reuse Supabase here.

**Mitigation:** Use the service-container pattern (postgres image with PostGIS
extension), run `prepare.apply_schema` against it, then run a small
`tests/test_postgis_smoke.py` script that imports research, calls the per-parcel
processor against a fixture, and queries `ST_IsValid`/`ST_Within`. Keep the
script ~80 lines max.

### Item 6 — `field_mapping_drift` and `cycle_id_collision` tests

**Change:** Two new offline tests, both using existing fixtures.

**Risks:** None worth naming. Both functions are unit-testable via direct call.

**Mitigation:**
- `field_mapping_drift`: load `arcgis_layer11_schema_missing_landacres.json`
  via a `_MockSession` whose `get` returns the schema for the schema URL.
  Call `_check_field_mapping_drift`; assert `(False, ["LandAcres"])`.
- `cycle_id_collision`: drive a `FakeConnection` whose `fetchone_returns`
  queue starts with `(1,)` (so `_count_log_rows` returns 1). Call
  `run_discovery_cycle("atlanta")`; assert `summary["aborted"]` and
  `summary["abort_reason"] == "cycle_id_collision"`.

### Item 7 — Strengthen `test_no_immutable_writes`

**Change:** Walk `Path(...).write_text`, `Path(...).open("w")`, and
`json.dump`/`csv.writer` with first-arg path resolution that catches dynamic
paths whose string concatenations include `"parameters.json"`, `"program.md"`,
or `"sources.json"`.

**Risk:** Over-aggressive scanner produces false positives on
unrelated `.write_text` calls. Mitigate by allowlisting known safe
write targets (`sources/{cycle_id}/...` cache writes) by checking that
the path expression reaches `_safe_cache_path` or `_SOURCES_DIR`.

**Mitigation:** Use a focused approach: walk all `Call` nodes whose
function is `write_text`, `write_bytes`, or `open` with mode containing
"w". For each, traverse the call's argument *and* the variable assignments
in the same module to see if any constant string in the data-flow contains
a forbidden path. Keep it pragmatic — a slightly-fuzzy match is better
than an over-engineered taint analysis. ~40 lines of test code.

### Item 8 — Multi-polygon centroid fix (real bug)

**Change:** Modify `_arcgis_polygon_to_wkt` (`research.py:308-345`) to
return `(wkt, was_multi, kept_outer_ring)`. Update `_map_feature_to_parcel`
at `:840-844` to pass `kept_outer` to `_ring_centroid` instead of `rings[0]`.

**Risks:**
- **R-MP-1** — return shape change: every caller and test must be updated.
  Inventory: `_map_feature_to_parcel` (one production caller) and the two
  tests `TestPolygonAndSrid.test_simple_polygon_to_wkt` /
  `.test_multipolygon_keeps_largest_outer`. No external consumers because
  `_arcgis_polygon_to_wkt` is module-private.
- **R-MP-2** — silent regression: the existing multi-polygon test at
  `test_discovery.py:369-373` would still pass after the fix because it
  doesn't check the centroid. We need a *new* test that exercises the
  bug: fixture with rings[0] smaller than rings[1], assert that the row's
  `centroid_lng`/`centroid_lat` come from rings[1], not rings[0].

**Mitigation:** Update the function and both call sites in the same diff.
Add the new fixture `arcgis_query_multipolygon_largest_second.json` with
small-first-large-second geometry. Add the new test
`test_multipolygon_centroid_uses_kept_outer`.

### Item 9 — `harness=degraded` test

**Change:** New test that mocks `connector_harness.run_harness_for_county`
to return `harness_degraded.json`, runs the cycle, and asserts:
- `summary["harness_status"] == "degraded"`
- `summary["aborted"] is False`
- exactly one flag row was emitted with `params[1] is None` (parcel_id NULL,
  combining with Item 2)
- the cycle proceeded into `_discover_fulton`

**Risk:** The happy-path mock chain is complex (params, sources, session,
harness). Reuse `TestHappyPathDryRun`'s mock setup verbatim, just swap the
harness fixture.

**Mitigation:** Extract a `_make_happy_path_mocks()` helper in the test file
to avoid duplication. ~20 lines.

### Item 10 — Strengthen `test_filter_pipeline_extensible`

**Change:** Append a synthetic filter `_h5_test_stub` that returns
`_FilterResult("flag", "H5_TEST", "marker_h5")`. Run `_process_parcel`
against a happy-path fixture parcel and assert that the executed SQL
includes a `flagged_items` insert with description containing
`"marker_h5"`.

**Risk:** The synthetic filter mutation must be cleaned up in `tearDown`
or via a `try/finally`. The existing test does this; we just extend.

**Mitigation:** Wrap the new assertion in the existing `try/finally` that
restores `_HARD_FILTERS`. Use `FakeConnection` to capture the SQL.

---

## 3. Go/no-go gates for Agent 3

Before merge of the Phase 3.1 fix branch, all of:

1. All 10 items implemented in code or tests (or CI YAML for item 5).
2. Existing 48 tests still pass (no regression).
3. New tests pass: at least 8 new tests across items 2, 4, 6, 8, 9, 10
   (and the strengthened items 7, 10 still passing).
4. `_arcgis_polygon_to_wkt`'s return shape change has updated all callers
   and tests in lockstep — no `ValueError: too many values to unpack`
   anywhere.
5. `program.md` updated and the deviation from L127 is gone.
6. The new CI workflow file is syntactically valid YAML and uses the same
   secrets pattern as `validate-phase1.yml`.
7. No new Phase 3.1 code writes to `parameters.json`, `program.md`, or
   `sources.json` — the immutable contract holds.

---

## 4. Out of scope (do NOT do in Phase 3.1)

- Add a `cycle_id` column to `flagged_items` (this is a `prepare.py`
  mutation; defer to Phase 4 per the original Phase 3 review's open question).
- Convert `parcels.geometry` to `MultiPolygon` (also a `prepare.py` mutation;
  Phase 4+).
- Add H5–H10 hard filters (Phase 4).
- Refactor the corridor bbox config to a JSON file (Phase 11).

---

## 5. Final verdict

**GO.** 10 items. ~4 hours. Single PR-equivalent on the
`claude/revalidate-phase-3-ytHPT` branch. Lands before Phase 4.
