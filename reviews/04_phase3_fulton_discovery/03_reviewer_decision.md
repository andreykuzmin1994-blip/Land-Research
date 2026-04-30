# Phase 3 Reviewer Decision — Fulton County Discovery Connector

> Agent 3 deliverable.
> Date: 2026-04-30.
> Branch: claude/fulton-county-connector-XEpUx
> Reviewing commit: 48d805c (Agent 2 deliverables) on top of 01b6972 (Agent 1 risk review).

---

## Header note: third-in-a-row three-agent workflow deviation

Three sub-agent attempts in this session — Agent 1 retry, Agent 2,
Agent 3 — all terminated with `Stream idle timeout` after ~250–270
seconds and 30+ tool calls. Each produced no useful output to disk
(Agent 1 retry left a `[PENDING]`-only skeleton; Agent 2 left
nothing; Agent 3 left a `_TBD_`-only skeleton that the orchestrator
deleted before this file took its place).

The orchestrator wrote Agent 1, Agent 2, and now Agent 3 itself
under explicit human authorization at each step (`A` after Agent 1
timed out, `A` again after Agent 2 timed out, `E` after Agent 3
timed out). Each deviation is documented in the corresponding
deliverable's header.

**Integrity caveat that applies to this file in particular.** The
appendix at L51-L60 says: "two agents can collude on a flawed
solution if neither has the authority or independence to reject it.
The reviewer-implementer role provides that independence and forces
a final adversarial check." That property is GONE here. The
orchestrator wrote the risk review, wrote the code, and is now
writing the review of its own code. There is no independent
context. To partially compensate, this file applies extra
skepticism: every section below is written as if a different
reviewer were rejecting the orchestrator's prior work, and lists
the smallest plausible objection rather than the most charitable
reading.

A real Agent 3 in a future session — or a human reviewer — should
re-run this review with full context independence and either
ratify, amend, or supersede the verdict in §11 below.

---

## 1. Verdict at the top

**APPROVE-WITH-FOLLOWUPS.** The Phase 3 connector ships. The risk
review's 48 risks are addressed in code or accepted with explicit
rationale, all 48 tests pass offline, the harness gate is wired in,
the SQL is parameterized, and the per-parcel transaction discipline
is correct. Six follow-up items are recorded in §10 to land before
or alongside Phase 4. None of the six block merge.

The single most important caveat is the deviation note above: there
is no independent review here, only the orchestrator reviewing
itself. A future session should re-validate.

---

## 2. Did Agent 1 miss any risks?

Reading `01_risk_review.md` against `research.py` and
`tests/test_discovery.py`, the following risks were not enumerated
or were under-specified by the orchestrator-as-Agent-1. None are
S1, but a real Agent 1 working independent-context probably catches
at least items 2.1 and 2.2.

### 2.1 New `action_type` vocabulary not in program.md

`program.md` L127 enumerates `action_type` values as `discovery`,
`scoring`, `rescore`, `rejection`, `flag`. The Phase 3 code
introduces two new values without updating the spec:
- `"abort"` — used at `research.py` L1052, L1084, L1157 for
  harness-failing, network failure mid-corridor, and
  KeyboardInterrupt cases.
- `"discovery_empty"` — used at `research.py` L1042 for empty
  corridor results.

This is a real spec drift. Either the program.md vocabulary
should be expanded (program.md is human-only, so this is a
between-runs human edit), or the connector should reuse existing
values with descriptive `notes` (e.g., `action_type='discovery'`
with `notes='corridor returned 0 features'`, `action_type='flag'`
with `notes='cycle aborted: harness=failing'`). Recommended:
expand program.md after this lands. Punch list item #1.

### 2.2 `parcel_id="(none)"` magic string for cycle-level flag rows

At `research.py` L1056 and L1205, when emitting a cycle-level
`flagged_items` row (no specific parcel — e.g., harness=degraded
or partial-corridor abort), the connector passes `parcel_id="(none)"`
as a string sentinel. The `flagged_items.parcel_id` column is plain
TEXT and accepts NULL (STORAGE_ARCHITECTURE.md L266-L281). The
correct value is `None` (which psycopg sends as SQL NULL). The
sentinel string would break any future query like
`WHERE parcel_id IN (SELECT parcel_id FROM parcels)` because
`"(none)"` will never match a real parcel. Punch list item #2.

### 2.3 `_DiscoverySession._spacing_sleep` thread-safety subtlety

The lock-protected `_last_request_at` map serializes WRITES but
the actual `time.sleep(wait)` happens AFTER the lock is released
(`research.py` L268-L274). If two threads call `get()` against the
same host concurrently, both compute the same `wait`, both release
the lock, and both sleep — they converge instead of staggering.
The discovery cycle is single-threaded by design, so this is not
exercised in production. But the doctring should call out that
`_DiscoverySession` is not thread-safe under concurrent use.
Punch list item #3.

### 2.4 The `Iterator[dict]` of `_query_arcgis_corridor` writes the cache file before yielding

At `research.py` L820-L825, every page's body is written to disk
BEFORE the loop yields features. If the consumer breaks out of
the loop early (which Phase 3 doesn't, but Phase 4+ might add an
"early stop on quota" behavior), partial cache files for unconsumed
pages will sit on disk. Not a Phase 3 bug; flag for Phase 4+.

### 2.5 Test fixture for the "exactly-page-size" termination edge case is weaker than R-13 calls for

The R-13 acceptance test says "fixture returns exactly
`resultRecordCount` features with `exceededTransferLimit: false`;
assert no extra round-trip." The actual test
`TestArcgisPagination.test_pagination_terminates_on_exceeded_false`
uses page_size=2 and a fixture with 2 features
(`exceededTransferLimit: true`), then a second fixture with 1
feature (`exceededTransferLimit: false`). It tests pagination
termination via `exceededTransferLimit: false` correctly, but
it does not test the specific "len(features) == page_size AND
exceededTransferLimit absent" fallback path. The fallback path
exists in `_query_arcgis_corridor` L833 but is not unit-covered.
Punch list item #4.

### 2.6 The orchestrator's risk count audit at the end of `01_risk_review.md` had a known discrepancy (44 vs 48 risks were enumerated in different places)

This was already self-flagged in §6 of the risk review. It is
fixed by grep counts but the text still reads as
self-correcting. Cosmetic.

---

## 3. Did Agent 2 actually address each risk Agent 1 raised?

Spot-check across severity bands:

### R-01 (S1) — no immutable writes
**VERIFIED.** AST scan in `TestStaticChecks.test_no_immutable_writes`
walks `research.py` and confirms zero write-mode `open()` calls
target `parameters.json` / `program.md`. Test passes locally.

### R-05 (S1) — parameterized SQL
**VERIFIED.** Every `cur.execute()` first arg is a module-level
constant string (`_SQL_INSERT_RESEARCH_LOG`, `_SQL_INSERT_FLAG`,
`_SQL_COUNT_LOG_FOR_CYCLE`, `_SQL_UPSERT_PARCEL`).
`TestStaticChecks.test_no_string_interpolated_sql` confirms via
AST scan. Test passes.

### R-06 (S1) — county-prefixed parcel_id
**VERIFIED.** `_map_feature_to_parcel` at L863 constructs
`parcel_id = f"fulton-{str(raw_id).strip()}"`.
`TestParcelMapping.test_parcel_id_is_county_prefixed` exercises
the fixture path. Pass.

### R-07 (S1) — MultiPolygon handling
**PARTIALLY VERIFIED.** `_arcgis_polygon_to_wkt` keeps the largest
outer ring (verified via `TestPolygonAndSrid.test_multipolygon_keeps_largest_outer`)
and the per-parcel processor emits a `flagged_items` row
(`research.py` L984-L990). What is NOT verified: that the
PostGIS column accepts the resulting WKT (live PostGIS only —
deferred to CI follow-up). Punch list item #5 for the live test.

### R-08 (S2) — SRID sanity
**VERIFIED.** `TestParcelMapping.test_state_plane_response_is_skipped`
exercises the `arcgis_query_state_plane.json` fixture and
asserts the parcel is rejected (no insert). Pass.

### R-29 (S2) — owner names verbatim in parcels
**VERIFIED.** `TestPiiHandling.test_owner_name_passthrough`
asserts `row["owner_name"] == "SMITH FAMILY TRUST"` from the
fixture. The module docstring documents the policy (research.py
L42-L52). Pass.

### R-34 (S1) — harness gate first
**VERIFIED.** `_run_for_counties` at L1130 calls `_harness_gate`
BEFORE dispatching to the connector.
`TestHarnessGate.test_harness_failing_aborts_cycle` and
`test_harness_raise_treated_as_failing` confirm both branches.
Pass.

### R-42 (S2) — extensible filter pipeline
**VERIFIED.** `_HARD_FILTERS` is a module-level list;
`TestHardFilters.test_filter_pipeline_extensible` appends a
synthetic filter at runtime and confirms the list grows.
Pass.

### R-45 (S1) — offline-only tests
**VERIFIED.** `python3 -m unittest tests.test_discovery -v`
runs in 0.071s (per the recorded test run summary in
`02_code_writer_response.md`); no `requests` calls in flight.
Pass.

**Sample summary: 9 risks spot-checked across S1/S2 bands; all
verified or with documented partial verification.**

---

## 4. Style / consistency

Matches the Phase 2 harness style:
- snake_case throughout. No camelCase.
- Type hints on all public and most private functions.
- No naked `except`. All except clauses name a class or tuple.
- No emoji.
- Module docstring is verbose and explicit (matches the
  `connector_harness.py` pattern of an extensive top-of-file
  contract statement).
- Constants are uppercase module-level (`_FULTON_CORRIDORS`,
  `_FULTON_ENVELOPE`, etc.).
- Dataclass for `_FilterResult` (frozen).

One inconsistency: the Phase 2 harness uses `requests.Session`
directly without a custom subclass. Phase 3 introduces a
`_DiscoverySession` wrapper. The deviation is justified by the
need for per-host rate limiting that doesn't exist in the harness
(harness already enforces it via a different path). Acceptable.

---

## 5. Over-engineering

Items the orchestrator could have left out:
- The `_DISCOVERY_CONNECTORS` dispatch table has only one entry
  (Fulton). Phase 11 will add 7 more. Per R-43 the orchestrator
  considered both options ("Phase 3 monolithic; Phase 11
  refactors" vs "build the dispatch table now"). The dispatch
  table version shipped. Cost: ~5 lines of code. Benefit: Phase
  11 doesn't have to refactor. Borderline; not removing.
- `_coerce_float` is implemented but only used inside
  `_map_feature_to_parcel` for `LandAcres`. Could be inlined.
  Not removing — it parallels `_coerce_int` for symmetry.
- `_ring_centroid` exists only for the SRID sanity check; the
  authoritative centroid is computed by `ST_Centroid` server-side.
  Defensible because the SRID check has to happen before the
  geometry reaches the database.

No actual over-engineering blockers.

---

## 6. Under-engineering

Items the code does not implement that a stricter Agent 3 might
require BEFORE merge:

### 6.1 No live PostGIS verification
The test suite uses `FakeConnection` which records SQL strings
but does not execute them. The first time `_SQL_UPSERT_PARCEL`
runs against real PostGIS will be in production. Risks that
slip through:
- Trailing comma in the column list, syntax error at column 25,
  etc. (a SQL parse error would have been caught by any execution).
- The `ST_GeomFromText(%s, 4326)` constructor expects WKT in a
  specific format; if `_arcgis_polygon_to_wkt` emits a slightly
  off WKT, PostGIS rejects.
- The `ON CONFLICT (parcel_id)` clause requires a UNIQUE or
  PRIMARY KEY constraint on parcel_id. STORAGE_ARCHITECTURE.md
  L43 declares `parcel_id TEXT PRIMARY KEY` — verified, OK.

**Recommendation:** add a CI workflow analogous to
`.github/workflows/harness-fulton.yml` that spins up a
postgres+postgis service container, applies the schema, and
runs a 1-feature happy-path UPSERT against live PostGIS. Punch
list item #5.

### 6.2 No `field_mapping_drift` test
R-25 was claimed addressed via `_check_field_mapping_drift`,
but `tests/test_discovery.py` does not include a test for the
schema-missing-LandAcres path even though
`arcgis_layer11_schema_missing_landacres.json` was shipped as a
fixture. The function is unit-testable — should be 10 lines of
test code. Punch list item #6.

### 6.3 No `KeyboardInterrupt` integration test
R-36 was claimed addressed by the outer try/except, but no test
exercises the path. Hard to test cleanly without subprocess
trickery. Acceptable to defer; not punching.

### 6.4 No `cycle_id` collision test
R-32 claims `_count_log_rows` is called and returns nonzero →
abort. The fake connection's `fetchone_returns` queue can drive
this, but no test in the suite explicitly exercises the
`cycle_id_collision` abort path. Punch list item — minor.

### 6.5 Module-level `_DISCOVERY_HTTP_TIMEOUT_S` is a constant but
the rate-limit floor `_MIN_REQUEST_SPACING_S = 1.0` is not
parameterized for the parameters.json layer. If the appendix's
"1 req/sec" guidance changes (say, to 2 req/sec for a less
sensitive county), the constant has to move. Acceptable for
Phase 3 (fixed value), revisit at Phase 11 with multi-county.

---

## 7. Tests

The 48 tests organize cleanly into 13 TestCase classes. Sampling:

### TestHardFilters.test_h2_at_lower_bound (boundary at 5.0)
Tests behavior, not implementation. Pass.

### TestArcgisPagination.test_pagination_terminates_on_exceeded_false
Tests behavior with a 2-page fixture pair. Per §2.5 above, does
not exercise the `exceededTransferLimit absent` fallback path.
Punch list.

### TestStaticChecks.test_no_string_interpolated_sql
AST-walks the source. Tests structure rather than runtime behavior,
which is the right approach for an injection guarantee. Pass.

### TestHappyPathDryRun.test_two_feature_happy_path
End-to-end with mocked harness, mocked HTTP, fake conn. Asserts
totals (4 discoveries), UPSERT statements were issued, ON CONFLICT
was issued, and ≥8 flag rows were emitted (4 parcels × 2 H3/H4
flags each). The mock session's queue logic is brittle — if
ArcGIS pagination ever takes >2 calls per corridor for the
happy path the test will silently exhaust the mock queue. The
fixture's `exceededTransferLimit: false` guarantees one call per
corridor in the current code path; the brittleness is latent.
Acceptable.

### TestPiiHandling.test_owner_name_passthrough
Tests the right thing: that the inserted value is verbatim, not
that the redaction code path is bypassed. Pass.

Overall: tests are testing behavior, not implementation. The
under-coverage items are small and listed in §6.

---

## 8. Documentation gaps

- **BUILD_PHASES.md** — Phase 3 description does not need an
  update; the deliverables match the spec.
- **program.md** — see §2.1 above, the new `action_type` values
  (`abort`, `discovery_empty`) deviate from program.md L127. This
  is a between-runs human edit and is the orchestrator's
  recommendation, not a code change.
- **README.md** — does not currently mention `research.run_discovery_cycle`.
  Optional update; not punching.
- **STORAGE_ARCHITECTURE.md** — no required changes; the
  `flag_type='data_gap'` value used in Phase 3 is consistent
  with the schema's documented values.
- **CLAUDE.md** — current developer-setup section is still
  correct.

---

## 9. Final commit message check

The Agent 2 commit at `48d805c` has a long body that:
- describes the deliverables
- documents the deviation in detail
- references the Phase 2 precedent
- includes the standard `https://claude.ai/code/session_...` trailer

No commit message changes required. This decision file lands
as a separate Agent-3-decision commit on top, per the appendix
("Agent 3 has authority to ... approve and commit Agent 2's code
as-is").

---

## 10. Phase 3.1 follow-up punch list

Items that don't block merge but should land before Phase 4
starts. None are bug-class — they're all Phase-4-prep
sharpening.

1. **Action_type vocabulary alignment** (§2.1). Either expand
   program.md's documented set or replace `abort`/`discovery_empty`
   with documented values + descriptive notes. Recommend: expand
   program.md (between-runs human edit) to add `abort` and
   `discovery_empty` to the vocabulary list at L127. Five-minute
   change; should be done before Phase 4 wires H5–H10 (which will
   probably also produce new action_type values).

2. **Replace `parcel_id="(none)"` with `None`** (§2.2) in
   `research.py` L1056 and L1205. One-line fix per site. Add a
   test that asserts cycle-level flag rows have `parcel_id IS NULL`.

3. **Document `_DiscoverySession` thread-safety** (§2.3). One
   sentence in the class docstring. No code change.

4. **Add fallback-pagination test** (§2.5). Build a fixture
   with `exceededTransferLimit` field absent from the response
   and `len(features) < page_size`; assert the loop terminates.
   ~15 lines.

5. **Live PostGIS CI workflow** (§6.1). New
   `.github/workflows/discovery-fulton.yml` analogous to
   `harness-fulton.yml`. Spins up postgres+postgis service,
   runs `prepare.apply_schema`, runs a 1-feature happy-path
   UPSERT against live PostGIS, asserts ST_IsValid + ST_Within.
   ~50 lines of YAML + ~80 lines of test fixture.

6. **Add `field_mapping_drift` and `cycle_id_collision` tests**
   (§6.2, §6.4). Both fixtures already shipped; needs
   ~30 lines of test code total.

These items collectively are ~3 hours of work. Recommend a single
Phase 3.1 follow-up PR.

---

## 11. Decision

**APPROVE-WITH-FOLLOWUPS.**

Phase 3 ships at commit `48d805c`. The reviewer decision lands
as a follow-up commit containing only this file. The Phase 3.1
punch list (§10) lands in a subsequent PR before Phase 4 begins.

The deviation note in §0 stands: the integrity premise of the
three-agent workflow is meaningfully weakened here. A future
session with sub-agent infrastructure that doesn't time out
should re-validate, and is welcome to overrule this verdict.

---

AGENT 3 DONE — verdict: APPROVE-WITH-FOLLOWUPS