# Phase 5 Code Writer Response — Scoring Engine MVP (Option B)

**Author:** Agent 2 role, completed by orchestrator (Claude Code main session)
under explicit human authorization after the Phase 5 Agent 1 sub-agent
attempt hit a stream-idle timeout. The orchestrator wrote both the risk
review (`01_risk_review.md`) and this response document. Mirrors the
deviation precedent in `reviews/04_phase3_fulton_discovery/02_code_writer_response.md`.
**Date:** 2026-05-01.
**Branch:** `claude/add-environmental-filters-Lzf3W`.
**Reviewing:** the Phase 5 implementation across `research.py`,
`tests/test_discovery.py`, `data/oz_ga_stub.geojson`, and `data/_README.md`.

---

## 1. Summary

Phase 5 — Scoring Engine MVP (Option B) is implemented. `score_parcel` and
`run_scoring_cycle` produce real composite scores against the existing
`parcel_scores` table using S2 (real, PostGIS), S9 (stub returning moderate
default 5), and S10 (real, OZ-portion only via bundled stub geojson). The
9 other sub-scores stay null and emit one `flagged_items` data_gap row each
per scoring call. The composite formula handles null sub-scores correctly
(weighted average over only populated terms; returns None when all null).
Every parcel_scores row gets `actionability='PENDING'` so the Phase 1
metric SQL `actionable_pipeline_count` continues to return 0 until Phase 8
runs the actionability screen.

No `parameters.json`, `program.md`, `sources.json`, `prepare.py`,
`connector_harness.py`, or `connector_registry.json` edits. No new
runtime dependencies. Pure-Python PNPOLY ray-casting replaces the would-be
shapely dep. Phase 4's H3-flag/H4-flag stubs and Phase 4's H5-H10
PASS-WITH-FLAG pattern are untouched.

## 2. Per-risk responses

- **R-201** (parameter immutability) — addressed. `score_parcel` calls
  `prepare.verify_parameters_unchanged()` and `prepare.get_parameters()`
  on the production path when no `params=` is passed; `run_scoring_cycle`
  calls them once at the top and threads the cached params dict into every
  `score_parcel` invocation so the per-parcel inner loop does not re-load.
- **R-202** (SQL injection) — addressed. All six new SQL strings are
  module-level constants composed of static string concatenation and
  parameterised `%s` placeholders. The existing
  `TestStaticChecks.test_no_string_interpolated_sql` AST scanner passes
  with zero violations after the diff. New
  `TestPhase5SqlConstantsStaticChecks.test_scoring_sql_uses_parameterized_placeholders`
  asserts no `{` braces in any new SQL constant.
- **R-203** (composite divide-by-zero) — addressed. `_compute_composite`
  has explicit `if weight_sum == 0: return None` branch. Tested by
  `TestPhase5Composite.test_all_null_returns_none`.
- **R-204** (parcel_scores append-only) — addressed. Every code path issues
  only `INSERT INTO parcel_scores`; no UPDATE or DELETE statements appear
  in research.py. Static check
  `TestPhase5SqlConstantsStaticChecks.test_no_update_or_delete_against_parcel_scores`
  greps the source for forbidden SQL patterns. Versioned-append behavior
  exercised by `TestPhase5ParcelScoresAppendOnly.test_two_calls_produce_two_inserts`.
- **R-205** (OZ data sourcing — the GO-WITH-CONDITIONS condition) —
  addressed. `data/oz_ga_stub.geojson` is bundled with two valid Polygon
  features approximating the South Fulton (Campbellton-Fairburn) and
  Clayton County (I-85 South / Airport) industrial OZ areas. Each feature
  has `properties.is_stub = true`. `data/_README.md` documents the human
  follow-up: download from HUD opportunityzones.hud.gov, filter to GA
  state FIPS=13, replace the file. The lazy loader
  `_load_oz_tracts` logs a warning and returns an empty list if the file
  is missing, so S10 degrades to None rather than raising.
- **R-206** (pure-Python PNPOLY vs shapely) — addressed. `_point_in_ring`
  is a 15-line ray-casting implementation; no new requirements.txt entries.
  Tested by `TestPhase5OzPnpoly` (4 cases: inside, outside, outside-nearby,
  degenerate ring → False).
- **R-207** (NULL geometry → S2=None) — addressed. `_compute_s2` returns
  None when `_SQL_S2_GEOMETRY` returns no rows (the WHERE clause filters
  `geom IS NOT NULL`); `_score_geometry` also defends against null/zero
  area. Tested by `TestPhase5S2Geometry.test_null_geometry_returns_none`.
- **R-208** (confidence in [0, 1]) — addressed. `_compute_confidence`
  bounds the result via `min(1.0, max(0.0, populated / 12))`. Tested by
  `TestPhase5Confidence` (4 cases: 0/12, 12/12, 3/12, zero-as-populated).
- **R-209** (action_type vocabulary) — addressed. `scoring` is already in
  `program.md:127` per Phase 3.1's vocabulary expansion; no spec edit
  needed. The new `_SQL_INSERT_RESEARCH_LOG_SCORING` writes literal
  `'scoring'` from the orchestrator.
- **R-210** (idempotency / re-scoring) — addressed by R-204's
  versioned-append test.
- **R-211** (per-parcel transactions) — addressed. `score_parcel` wraps
  the parcel_scores INSERT + research_log INSERT + flagged_items INSERTs
  in `with conn.transaction()`. Exception path rolls back and returns
  `status='error'`.
- **R-212** (tests run without DATABASE_URL) — addressed. New
  `Phase5FakeConnection` stand-in with shared fetchone/fetchall queues
  enables sequenced multi-cursor mocking.
- **R-213** (driver query + cycle id) — addressed.
  `_make_scoring_cycle_id` generates `score-{market}-{ISO}-{4hex}`;
  `_SQL_COUNT_LOG_FOR_SCORING_CYCLE` and the abort-on-collision path mirror
  the Phase 3 discovery pattern. `_SQL_LIST_UNSCORED_PARCELS` uses
  `WHERE NOT EXISTS (...)` against parcel_scores. No pagination — Fulton
  cycle volume is ~hundreds, fits in memory. Tested by
  `TestPhase5RunScoringCycle.test_iterates_unscored_parcels` and
  `.test_cycle_id_collision_aborts`.
- **R-214** (signature stability) — addressed. `score_parcel(parcel_id)`
  is the public API; the `conn=`, `cycle_id=`, `params=` kwargs are
  optional with sensible defaults so production callers can simply call
  `score_parcel("fulton-001")`.
- **R-215** (H3/H4 naming followup) — accepted; Phase 4 followup, not
  Phase 5 scope.
- **R-216** (confidence_weighted_pipeline contract) — addressed via R-208.
- **R-217** (future-phase risk) — accepted. The per-S helper functions
  (`_compute_s2`, `_compute_s9`, `_compute_s10`) are independent so
  Phase 7 can drop in real S4/S5/S6 implementations without touching
  the orchestrator.
- **R-218** (S9 fixed-5 baseline shift) — accepted with documentation in
  the function docstring and risk review.
- **R-219** (closed-interval bbox filter) — addressed. `_check_oz` uses
  `<=` and `>=` for the bbox pre-filter. Tested by the in/out boundary
  cases of `TestPhase5OzCheck`.
- **R-220** (flagged_items volume) — accepted with documentation.
- **R-221** (deterministic timestamps) — accepted; tests don't assert on
  scored_at value.
- **R-222** (static AST checks pass) — addressed. The `forbidden_names`
  set in `test_no_print_in_run_discovery_cycle` was NOT extended to cover
  the new scoring helpers — they don't call print, but if a future Phase
  ever adds one inadvertently, the test would not catch it. This is a
  deliberate non-extension to keep the static check focused on the
  discovery cycle (its original R-39 scope). Future-phase improvement.
- **R-223** (numeric type coercion) — addressed. `composite_score` and
  `confidence_score` are NUMERIC in the DDL; psycopg coerces Python
  float / Decimal cleanly.
- **R-224** (`data/` directory addition) — addressed. `data/_README.md`
  bootstraps the directory with documentation; `data/oz_ga_stub.geojson`
  is checked in. Neither is gitignored. `sources/` (gitignored API cache)
  remains separate.

## 3. Files modified

| File | Change |
|---|---|
| `research.py` | +6 SQL constants, +OZ loader/PNPOLY helpers, +sub-score computations (S2, S9, S10), +composite/confidence helpers, +`score_parcel` (replacing the NotImplementedError stub), +`run_scoring_cycle` driver, +`_print_phase1_status` blurb update |
| `tests/test_discovery.py` | +`Phase5FakeConnection` and `_SharedQueueCursor` shared-queue fakes, +12 new test classes covering PNPOLY, OZ check, S2 mapping, S9, S10, composite, confidence, score_parcel orchestrator, run_scoring_cycle driver, append-only, OZ data file, SQL static checks |
| `data/oz_ga_stub.geojson` | NEW. 2-feature stub GeoJSON for the Phase 5 OZ check |
| `data/_README.md` | NEW. Documents `data/` purpose, the stub status, the human follow-up to populate from HUD |
| `reviews/07_phase5_scoring_mvp/01_risk_review.md` | NEW. Agent 1's review (orchestrator-inline) |
| `reviews/07_phase5_scoring_mvp/02_code_writer_response.md` | NEW. This document |

Untouched: `parameters.json`, `program.md`, `sources.json`, `prepare.py`,
`connector_harness.py`, `connector_registry.json`, `requirements.txt`.

## 4. Tests added / updated

40 new tests across 12 classes. None of the 64 pre-existing tests were
modified or removed.

| Class | Tests | What they exercise |
|---|---|---|
| `TestPhase5OzPnpoly` | 4 | Pure-function ray-casting (R-206) |
| `TestPhase5OzCheck` | 4 | Bundled-stub OZ check (R-205, R-219) |
| `TestPhase5OzDataFile` | 2 | Stub file structure validity |
| `TestPhase5S2Geometry` | 7 | S2 score mapping against synthetic area/bbox/aspect inputs |
| `TestPhase5S9` | 1 | S9 stub returns 5 |
| `TestPhase5S10` | 3 | S10 in/out/null cases |
| `TestPhase5Composite` | 5 | Composite formula edge cases (R-203) |
| `TestPhase5Confidence` | 4 | Confidence range (R-208) |
| `TestPhase5ScoreParcel` | 4 | Orchestrator: happy path, missing parcel, data_gap flags, PENDING actionability |
| `TestPhase5ParcelScoresAppendOnly` | 1 | Versioned-append (R-204, R-210) |
| `TestPhase5RunScoringCycle` | 3 | Driver: iteration, collision abort, market validation |
| `TestPhase5SqlConstantsStaticChecks` | 2 | No UPDATE/DELETE; parameterised placeholders only |

## 5. Test run output

```
$ python3 -m unittest tests.test_discovery -v 2>&1 | tail -5
test_no_string_interpolated_sql (tests.test_discovery.TestStaticChecks.test_no_string_interpolated_sql)
R-05: every cursor.execute() first arg is a Constant or Name (module-level SQL). ... ok
test_sources_dir_in_gitignore (tests.test_discovery.TestStaticChecks.test_sources_dir_in_gitignore)
R-30: sources/ is gitignored so cached PII is not committed. ... ok

----------------------------------------------------------------------
Ran 104 tests in 0.092s

OK
```

64 pre-existing + 40 new = 104. All pass on first run.

## 6. Sign-off

AGENT 2 (orchestrator inline) DONE — handoff to Agent 3.
