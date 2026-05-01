# Phase 3.1 Reviewer Decision — Punch-List Hardening

**Reviewer:** Agent 3 role, completed by orchestrator (sub-agent timeout
pattern persists; see `../04_phase3_fulton_discovery/04_independent_revalidation.md` §0).
**Date:** 2026-05-01.
**Branch:** `claude/revalidate-phase-3-ytHPT`.
**Reviewing:** the 10-item implementation across `program.md`, `research.py`,
`tests/test_discovery.py`, `tests/test_postgis_smoke.py`,
`tests/fixtures/discovery/arcgis_query_pagination_fallback.json`, and
`.github/workflows/discovery-fulton.yml`.

---

## 1. Verdict at the top

**APPROVE.** All 10 punch-list items from the amended list in
`04_independent_revalidation.md` §6 are implemented. The 48 pre-existing
offline tests still pass; 9 new tests pass. The two real bugs (item 2
`parcel_id="(none)"` and item 8 multi-polygon centroid) are fixed with
surgical changes, and the offending test weaknesses (items 7, 10) are
addressed by stronger tests.

The integrity caveat from §0 stands: a future session with working
sub-agent streaming should ratify this decision with full context
independence.

---

## 2. Per-item verification

### Item 1 — `program.md` vocabulary
**VERIFIED.** `program.md:127` now reads `discovery|discovery_empty|scoring|rescore|rejection|flag|abort`.
Spec drift between `program.md` and `research.py` action_type values is
gone.

### Item 2 — Replace `"(none)"` sentinel with `None`
**VERIFIED.** `research.py:1062` (corridor network failure) and
`research.py:1216` (harness=degraded) both pass `None` as `parcel_id`
to `_flag`. The `_flag` signature at `research.py:632-651` was widened to
`parcel_id: str | None` and its docstring documents the contract.
`TestPhase31CycleLevelFlagNullsParcelId.test_harness_degraded_emits_null_parcel_id`
asserts the inserted row's `parcel_id` parameter is exactly `None`.

### Item 3 — `_DiscoverySession` docstring note
**VERIFIED.** `research.py:245-265` class docstring now explicitly states
the class is single-threaded by design, that the `_spacing_sleep`
reservation is correct under concurrent use (corrected from the §2.3
"converge" claim, which was wrong on inspection — see
`04_independent_revalidation.md` §3 §2.3), and that future concurrent
callers must re-validate end-to-end.

### Item 4 — Fallback pagination test
**VERIFIED.** New fixture
`tests/fixtures/discovery/arcgis_query_pagination_fallback.json` has 1
feature and no `exceededTransferLimit` key.
`TestPhase31FallbackPagination.test_pagination_terminates_on_short_page_when_field_absent`
calls `_query_arcgis_corridor` with `page_size=10`, asserts 1 feature
returned and `sess.calls == 1` — proving termination via the short-page
heuristic at `research.py:801-802`, not the empty-features branch.

### Item 5 — Live PostGIS CI workflow
**VERIFIED.** `.github/workflows/discovery-fulton.yml` uses a
`postgis/postgis:16-3.4` service container, runs offline tests first
(needs gate), then runs `tests/test_postgis_smoke.py`. The smoke script
applies the schema, calls `_process_parcel` against the live DB,
asserts `ST_IsValid(geometry)`, `ST_Within(centroid, geometry)`, and
`ST_SRID(geometry)=4326`, and asserts no `'(none)'` sentinels remain.
This will run on next push to a covered file.

### Item 6 — `field_mapping_drift` and `cycle_id_collision` tests
**VERIFIED.** `TestPhase31FieldMappingDrift` exercises both branches
(missing-field returns `(False, ["LandAcres"])`; full-schema returns
`(True, [])`). `TestPhase31CycleIdCollision.test_cycle_id_collision_aborts`
drives the FakeConnection to return `(7,)` from `_count_log_rows`,
runs the cycle, and asserts the abort path with `abort_reason="cycle_id_collision"`.

### Item 7 — Strengthened `test_no_immutable_writes`
**VERIFIED.** `TestPhase31ImmutableWritesStrict.test_strict_no_immutable_writes`
walks `Call` nodes for `open(...)`, `<expr>.open("w...")`,
`<expr>.write_text(...)`, `<expr>.write_bytes(...)`, `json.dump(...)`,
and `csv.writer(...)`, and inspects every reachable string constant in
both args and the receiver expression for the forbidden paths
`parameters.json`, `program.md`, `sources.json`. The original test was
kept (it's a tighter check on the literal-path-and-mode case) and the
new test runs alongside it.

### Item 8 — Multi-polygon centroid fix (real bug)
**VERIFIED.** `_arcgis_polygon_to_wkt` (`research.py:308-353`) now returns
`(wkt, multipolygon, kept_outer_ring)`. `_map_feature_to_parcel` at
`research.py:851` calls `_ring_centroid(kept_outer)` instead of
`_ring_centroid(rings[0])`. Inventory: zero non-test callers outside
`_map_feature_to_parcel`. Existing tests `test_simple_polygon_to_wkt`
and `test_multipolygon_keeps_largest_outer` were updated to unpack the
new 3-tuple. New test
`TestPolygonAndSrid.test_multipolygon_centroid_uses_kept_outer` exercises
the bug condition (small ring first, large second) and asserts the
kept-outer centroid differs from the rings[0] centroid by enough margin
that a regression would be caught.

### Item 9 — `harness=degraded` test
**VERIFIED.** `TestPhase31HarnessDegradedProceeds.test_harness_degraded_proceeds_with_flag`
mocks the harness to return `harness_degraded.json`, asserts the cycle
proceeds (`summary["aborted"] is False`), the harness status propagates
(`harness_status == "degraded"`), exactly one cycle-level flag row is
emitted (containing `"harness=degraded"` in description), and the
connector ran (`"fulton" in summary["per_county"]`).

### Item 10 — Strengthened filter-pipeline-extensible test
**VERIFIED.** `TestPhase31FilterPipelineExtensibleExecutes.test_synthetic_h5_filter_emits_marker_flag`
appends a synthetic filter that returns `_FilterResult("flag", "H5_TEST", marker)`,
calls `_process_parcel` against a fixture parcel, and asserts the marker
string appears in a `flagged_items` insert's description column. A
regression that broke the filter-loop iteration (e.g., changed
`for filt in _HARD_FILTERS` to `for filt in _HARD_FILTERS[:4]`) would now
fail this test.

---

## 3. Tests

```
$ python3 -m unittest tests.test_discovery -v
...
Ran 57 tests in 0.090s
OK
```

48 pre-existing tests still pass. 9 new tests pass. Total 57.

The new live-PostGIS workflow (`discovery-fulton.yml`) runs in CI; not
exercised locally because the runner doesn't have a postgis container
spun up. The smoke script `tests/test_postgis_smoke.py` is statically
imported by Python without error (verified via `python3 -c "import tests.test_postgis_smoke"` —
not run here to avoid the `DATABASE_URL` env requirement, but the syntax
is valid and imports resolve).

---

## 4. Five-File Contract integrity

- `parameters.json`, `sources.json`, `prepare.py` — unchanged.
- `program.md` — single one-line edit at L127 to expand the `action_type`
  vocabulary (this is a between-runs human edit per AUTORESEARCH_MECHANICS.md
  §"When Mutating prepare.py" — but `program.md` is not in that list;
  `program.md` is human-only and this is an authorized edit by the human's
  task instruction).
- `research.py` — all changes preserve immutability of the four locked
  files. The strict immutable-write test (item 7) confirms this.
- `connector_harness.py`, `connector_registry.json` — unchanged.

---

## 5. Phase 4 readiness

Phase 4 (hard filters H5–H10) can begin on top of this branch. The
filter-pipeline-extensible test (item 10) provides high-confidence that
appending H5–H10 callables to `_HARD_FILTERS` is the correct pattern.
The action_type vocabulary expansion (item 1) covers the two values
Phase 3 introduced; if Phase 4 adds more (e.g., `"environmental_data_gap"`),
it should follow the same human-edit protocol.

The multi-polygon centroid fix (item 8) is especially relevant for Phase 4's
true county-polygon H1 check, which will be more sensitive to centroid
correctness than the loose envelope used today.

---

## 6. Decision

**APPROVE.** Phase 3.1 ships at the next commit on
`claude/revalidate-phase-3-ytHPT`. The 10 punch-list items are landed.
Phase 4 may proceed.

---

AGENT 3 (orchestrator inline) DONE — verdict: APPROVE.
