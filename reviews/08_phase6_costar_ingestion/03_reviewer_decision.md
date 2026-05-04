# Phase 6 Reviewer Decision — CoStar Ingestion (Option A)

**Reviewer:** Agent 3 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Same orchestrator-inline
deviation as Phase 2/3/3.1/4/5. The orchestrator wrote all three role
documents (`01_risk_review.md`, `02_code_writer_response.md`, this
decision).
**Date:** 2026-05-04.
**Branch:** `claude/costar-ingestion-setup-ovYfS`.
**Reviewing:** the Phase 6 Option A implementation across `research.py`,
`tests/test_discovery.py`, `tests/fixtures/costar/*.csv`,
`COSTAR_EXPORTS_README.md`, and the three review documents.

---

## 1. Verdict at the top

**APPROVE.** All 9 go/no-go gates from `01_risk_review.md` §4 pass on
independent verification. 35 R-3XX risks are addressed in code or
accepted with explicit rationale in `02_code_writer_response.md`. The
pre-existing 104 tests in `test_discovery` still pass; 57 new Phase 6
tests pass; 35 tests in `test_harness` still pass. Five-File Contract
intact.

The orchestrator-inline deviation is documented (fifth occurrence —
Phase 2/3/3.1/5/6). A future session with working sub-agent streaming
should ratify this decision with full context independence.

---

## 2. Per-gate verification

### Gate 1 — Five-File Contract intact

```
$ git diff 4c020bc -- parameters.json sources.json program.md \
                       prepare.py connector_harness.py \
                       connector_registry.json requirements.txt
(empty diff)
```

Verified bytes-identical to the Phase 5 head (`4c020bc`). ✓

### Gate 2 — research.py edits

The Phase 6 diff against `4c020bc` adds:

- 7 new `_SQL_*` constants:
  `_SQL_UPSERT_MARKETS_REF`, `_SQL_UPSERT_SUBMARKETS_REF`,
  `_SQL_FETCH_SUBMARKET_NAME`,
  `_SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST`,
  `_SQL_INSERT_MARKET_CONTEXT`,
  `_SQL_INSERT_RESEARCH_LOG_INGESTION`,
  `_SQL_COUNT_LOG_FOR_INGESTION_CYCLE`.
- New module-level constants: `_COSTAR_BASE_DIR`, `_COSTAR_SOURCE`,
  `_SUBMARKET_STATS_FILENAME_RE`, `_SUBMARKET_STATS_REQUIRED_COLUMNS`,
  `_INGESTION_CYCLE_ID_RE`, `_SLUG_NONWORD_RE`, `_DATE_FORMATS`,
  `_INGESTION_LOADERS`.
- Helpers: `_make_ingestion_cycle_id`, `_slugify`,
  `_resolve_costar_subdir`, `_scan_export_dir`, `_archive_destination`,
  `_move_file`, `_archive_file`, `_fail_file`, `_normalize_header`,
  `_validate_submarket_stats_headers`, `_coerce_optional_decimal`,
  `_coerce_optional_int`, `_parse_report_date`,
  `_validate_submarket_stats_row`, `_ensure_submarket`,
  `_read_csv_with_bom`, `_load_submarket_stats_file`,
  `_load_placeholder` + 4 specialised placeholders, `_load_submarket_stats`,
  `_count_log_rows_for_ingestion_cycle`.
- Public driver: `run_ingestion_cycle()`.
- Two new module imports: `csv`, `shutil`.
- Single-line edit to `_print_phase1_status` mentioning Phase 6.

No existing functions renamed or behavior-changed. No edits to the Phase
5 scoring code path. ✓

### Gate 3 — Tests

| Class | Count | Pass |
|---|---|---|
| TestPhase6Slugify | 6 | ✓ |
| TestPhase6IngestionCycleId | 2 | ✓ |
| TestPhase6ScanExportDir | 5 | ✓ |
| TestPhase6ArchiveAndFailMovement | 3 | ✓ |
| TestPhase6Coercion | 7 | ✓ |
| TestPhase6DateParsing | 5 | ✓ |
| TestPhase6HeaderValidation | 6 | ✓ |
| TestPhase6RowValidation | 7 | ✓ |
| TestPhase6EnsureSubmarket | 3 | ✓ |
| TestPhase6LoadSubmarketStatsFile | 5 | ✓ |
| TestPhase6Reingest | 2 | ✓ |
| TestPhase6RunIngestionCycle | 4 | ✓ |
| TestPhase6SqlConstantsStaticChecks | 2 | ✓ |
| **New total** | **57** | ✓ |
| Pre-existing | 104 | ✓ |
| **Grand total (test_discovery)** | **161** | ✓ |

```
$ python3 -m unittest tests.test_discovery 2>&1 | tail -3
----------------------------------------------------------------------
Ran 161 tests in 0.176s

OK
```

`tests.test_harness` (35 tests) still passes when run as a separate
process. The pre-existing `test_no_prepare_or_psycopg_imports` cross-
module import test contamination is unchanged from main.

### Gate 4 — Existing 104 tests still pass

Verified by the test count above. Specifically:
- `TestStaticChecks.test_no_immutable_writes` ✓
- `TestStaticChecks.test_no_string_interpolated_sql` ✓
- `TestStaticChecks.test_no_print_in_run_discovery_cycle` ✓
- `TestPhase5SqlConstantsStaticChecks.test_no_update_or_delete_against_parcel_scores` ✓
  (Phase 6 only DELETEs from `market_context`, not `parcel_scores`)
- `TestPhase5SqlConstantsStaticChecks.test_scoring_sql_uses_parameterized_placeholders` ✓
- `TestHappyPathDryRun.test_two_feature_happy_path` ✓
- `TestPhase5RunScoringCycle.*` (3 tests) ✓
- All Phase 4 hard-filter tests ✓

### Gate 5 — `COSTAR_EXPORTS_README.md` at repo root

Present (148 lines). Documents the directory layout, the human's CoStar
saved-search + email-to-folder one-time setup procedure, the agent's
behavior on file drop, the failure handling matrix, the
re-delivery/idempotency contract, and the Phase 6 Option A scope (only
`submarket_stats` wired; other 4 export types report
`status='not_implemented'` and don't move files). ✓

### Gate 6 — Test fixtures

Present at `tests/fixtures/costar/`:

- `submarket_stats_happy.csv` — 3 valid rows (3 distinct submarkets) ✓
- `submarket_stats_missing_column.csv` — header missing `vacancy_rate_pct` ✓
- `submarket_stats_row_errors.csv` — 1 valid + 1 out-of-range vacancy + 1 unparseable date ✓
- `submarket_stats_with_bom.csv` — verified first 3 bytes are `EF BB BF` (UTF-8 BOM) ✓
- `submarket_stats_duplicate_header.csv` — two `submarket_name` columns ✓

### Gate 7 — No new runtime dependency

```
$ cat requirements.txt
psycopg[binary]>=3.1,<4
python-dotenv>=1.0,<2
requests>=2.31,<3
```

Unchanged from Phase 5. `csv`, `shutil`, `re`, `pathlib`, `tempfile` all
stdlib. ✓

### Gate 8 — Static AST checks pass

- `test_no_immutable_writes` ✓ — no writes to parameters.json/program.md
  in research.py.
- `test_no_string_interpolated_sql` ✓ — every new
  `cursor.execute(...)` first-arg is a `Name` (module-level constant),
  zero violations across the 7 new SQL constants.
- `test_no_print_in_run_discovery_cycle` ✓ — Phase 3 forbidden-name set
  unaffected by Phase 6.
- New `TestPhase6SqlConstantsStaticChecks.test_ingestion_sql_uses_parameterized_placeholders` ✓ —
  all 7 Phase 6 SQL constants checked.
- New `TestPhase6SqlConstantsStaticChecks.test_no_print_in_ingestion_helpers` ✓ —
  forbidden set covers all 11 new ingestion functions.

### Gate 9 — Reviewer decision document written

This file. ✓

---

## 3. Independent risk re-check

Walked the 35 R-3XX risks in `01_risk_review.md` §3 independently.
Findings:

- All 33 "addressed in code/tests" risks are actually addressed.
  Spot-checked R-302 (DELETE-then-INSERT idempotency): the loader builds
  `dedup_keys = {(r["submarket_id"], r["report_date"]) for r in parsed_rows}`
  and executes `_SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST` once per unique
  key, then INSERTs every parsed row. Inside one transaction. Verified
  by `TestPhase6Reingest.test_dedup_delete_executed_per_unique_key`.
- Spot-checked R-301 (auto-UPSERT): `_ensure_submarket` does
  market-then-submarket UPSERT; new submarket emits a `data_gap` flag
  with `"backfill from STORAGE_ARCHITECTURE.md corridor bounding boxes"`
  resolution text. Verified by
  `TestPhase6LoadSubmarketStatsFile.test_happy_path_loads_and_archives`
  (asserts `len(result["submarkets_auto_created"]) == 3`).
- Spot-checked R-303 (directory traversal):
  `TestPhase6ScanExportDir.test_directory_traversal_rejected` exercises
  both `"../etc"` and `"/abs/path"` and asserts `ValueError`. ✓
- Spot-checked R-308 (BOM): `submarket_stats_with_bom.csv` first 3
  bytes are `EF BB BF` (verified at fixture creation time);
  `TestPhase6LoadSubmarketStatsFile.test_bom_csv_loads_cleanly` loads
  it without error. ✓
- The 2 "accepted with rationale" risks (R-318 partial-file philosophy,
  R-330 action_type vocab) are clearly explained and bounded; future-
  phase implications documented.
- No risks missed beyond what Agent 1 already flagged. The
  `network_error` retry semantics for `_move_file` are not exhaustively
  tested but the OSError fallback path is short and well-bounded;
  documented in §2.C of the response.

---

## 4. Code-quality review

- **Naming consistency:** new helpers follow existing `_compute_*`,
  `_score_*`, `_check_*`, `_make_*` patterns. SQL constants follow the
  `_SQL_*` upper-snake convention.
- **Docstring style:** single-line + multi-line docstrings consistent
  with Phase 3/4/5. Each new helper documents its R-3XX scope.
- **Comments:** sparse, only where the WHY is non-obvious (the
  Five-File Contract guard on `_COSTAR_BASE_DIR`, the BOM strip
  rationale in `_normalize_header`, the R-302 idempotency comment in
  the loader, the R-322 placeholder rationale). Matches the project's
  "default to no comments" directive in CLAUDE.md.
- **Error handling:** `_load_submarket_stats_file` rolls back on any
  exception inside the per-file transaction and quarantines the file
  to `FAILED/` rather than leaving it in intake. `_move_file` falls
  back from atomic rename to copy+unlink on cross-device errors.
  `_resolve_costar_subdir` raises early on traversal attempts.
- **No opportunistic refactoring:** Phase 5's `score_parcel`,
  `run_scoring_cycle`, and the OZ helpers are untouched. The previously-
  flagged H3/H4 → H3_filter/H4_filter rename is still deferred.
  `Phase5FakeConnection` is reused without modification (R-328).
- **Code volume:** +725 net lines in `research.py`, +589 in
  `tests/test_discovery.py`. Reasonable for the 13-deliverable scope
  (folder scan, schema validation, archive/fail movement, markets/
  submarkets auto-UPSERT, locale-tolerant number parser, multi-format
  date parser, header validator, row validator, per-file loader, 4
  placeholder loaders, registry, driver).

---

## 5. Architectural notes / followups

Non-blocking, for future phases:

1. **Phase 6.1+ wires the four other recurring export types.** Each
   replaces one placeholder loader (e.g.,
   `_load_land_sales_comps_placeholder` → `_load_land_sales_comps`)
   with a real loader following the same pattern as
   `_load_submarket_stats_file`. The framework, validation primitives,
   archive/fail machinery, and run driver are ready.

2. **Phase 7 wires `_compute_s4`/`s5`/`s6` to read from
   `market_context`.** The append pattern of
   `_load_submarket_stats_file` (one row per (submarket_id,
   as_of_date, source) tuple, latest-wins via `as_of_date DESC` index
   `idx_context_submarket_date`) means S4/S5/S6 can `SELECT ...
   ORDER BY as_of_date DESC LIMIT 1` cleanly.

3. **R-318 partial-file ratchet.** If row-level failures exceed ~5%
   for any week, the agent should escalate to whole-file refusal.
   Phase 7 can add this as a parameter-driven threshold; for now the
   row-level flags surface in the strategy memo.

4. **R-330 action vocabulary.** The `program.md` action vocabulary
   list at L127-L129 currently reads
   `discovery | scoring | rescore | rejection | flag | abort`.
   Phase 6 introduces `ingestion`. The DB column accepts it (no CHECK
   constraint). A future docs PR (between runs, not during) should
   expand the documented vocabulary.

5. **Submarket bbox backfill.** Every auto-created submarket has a
   NULL `bbox`. Phase 7+ corridor-based queries against `submarkets`
   will return empty until the human seeds the bboxes. The flagged
   `data_gap` rows make this discoverable.

6. **harness_reports/costar_ingestion_{date}.json output.** The
   CoStar contract §Ingestion Folder Structure step 5 calls for a
   per-cycle JSON report in `harness_reports/`. Phase 6 logs to
   `research_log` and `flagged_items` instead. A future phase (likely
   Phase 9, the snapshot generator) can add the JSON file as an
   additive step.

7. **Phase5FakeConnection / FakeConnection unification.** Phase 6 reuses
   `Phase5FakeConnection` (per R-328) — no new fake class needed.
   The unification with the original `FakeConnection` remains a
   future cleanup.

---

## 6. Five-File Contract integrity

| File | Status |
|---|---|
| `parameters.json` | unchanged from main |
| `sources.json` | unchanged from main |
| `program.md` | unchanged from main |
| `prepare.py` | unchanged from main |
| `connector_harness.py` | unchanged from main |
| `connector_registry.json` | unchanged from main |
| `requirements.txt` | unchanged from main |
| `research.py` | edited — Phase 6 ingestion subsystem (this phase) |
| `tests/test_discovery.py` | edited — 57 new Phase 6 tests |
| `COSTAR_EXPORTS_README.md` | NEW |
| `tests/fixtures/costar/*.csv` (5 files) | NEW |
| `reviews/08_phase6_costar_ingestion/*.md` (3 files) | NEW |

---

## 7. Phase 7 readiness

Phase 7 (Scoring Engine Complete) per BUILD_PHASES.md L108-L114 is the
natural next step. It depends on:

- `market_context` rows for at least one (submarket, as_of_date,
  source='costar') tuple. Phase 6 Option A delivers this via the
  `submarket_stats` loader.
- `_compute_s4` (vacancy), `_compute_s5` (absorption), `_compute_s6`
  (pipeline) helpers in `research.py` reading from `market_context`.
  These follow the same pattern as `_compute_s2`: one PostGIS-or-SQL
  query, score-mapping function, return 0–10 or None.
- `_compute_s8` refinement: read from `sales_comps` (which requires
  Phase 6.1 land_sales_comps loader to be wired first).

Phase 6.1 (the next 1.5h slice — wire `land_sales_comps`,
`leasing_comps`, `land_listings`) is recommended before Phase 7 so all
four CoStar-dependent scored parameters can move at once. Building
sales comps and tenant intel can wait.

The Phase 5 `score_parcel` orchestrator was deliberately structured so
that Phase 7 only adds three new sub-score branches — no orchestrator
rewrite. Same scaffold remains applicable.

---

## 8. Decision

**APPROVE.** Phase 6 Option A ships at the next commit on
`claude/costar-ingestion-setup-ovYfS`. The 9 go/no-go gates and 35
R-3XX risks are landed. Phase 6.1 (the four other recurring export
types) and Phase 7 (CoStar-dependent scoring) may proceed.

---

AGENT 3 (orchestrator inline) DONE — verdict: APPROVE.
