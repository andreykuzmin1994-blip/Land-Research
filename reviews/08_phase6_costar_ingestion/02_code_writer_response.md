# Phase 6 Code Writer Response — CoStar Ingestion (Option A)

**Writer:** Agent 2 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Same orchestrator-inline
deviation as Phase 2/3/5; sub-agent stream-idle timeouts in this sandbox
make the role split impractical here. The orchestrator wrote the Agent 1
risk review at `01_risk_review.md`, this response, and the Agent 3
decision at `03_reviewer_decision.md`.
**Date:** 2026-05-04.
**Branch:** `claude/costar-ingestion-setup-ovYfS`.
**Verdict from Agent 1:** GO-WITH-CONDITIONS (R-301 auto-UPSERT submarkets,
R-302 DELETE-then-INSERT idempotency, R-331 commit
`COSTAR_EXPORTS_README.md`).

---

## 1. Files changed

| File | Action | Net lines |
|---|---|---|
| `research.py` | edit (Phase 6 ingestion subsystem) | +725 / -1 |
| `tests/test_discovery.py` | edit (12 new test classes, 57 tests) | +589 / 0 |
| `COSTAR_EXPORTS_README.md` | new (operational README) | +148 |
| `tests/fixtures/costar/submarket_stats_happy.csv` | new | +4 |
| `tests/fixtures/costar/submarket_stats_missing_column.csv` | new | +2 |
| `tests/fixtures/costar/submarket_stats_row_errors.csv` | new | +4 |
| `tests/fixtures/costar/submarket_stats_with_bom.csv` | new (BOM bytes) | +2 |
| `tests/fixtures/costar/submarket_stats_duplicate_header.csv` | new | +2 |
| `reviews/08_phase6_costar_ingestion/01_risk_review.md` | new | + |
| `reviews/08_phase6_costar_ingestion/02_code_writer_response.md` | new | + |
| `reviews/08_phase6_costar_ingestion/03_reviewer_decision.md` | new | + |

**Five-File Contract verification** (`git diff` against
`4c020bc` Phase 5 head):

```
parameters.json                : unchanged
sources.json                   : unchanged
program.md                     : unchanged
prepare.py                     : unchanged
connector_harness.py           : unchanged
connector_registry.json        : unchanged
requirements.txt               : unchanged
```

✓ All immutable files byte-identical to head.

---

## 2. Per-deliverable summary

### A. Folder-scan engine — `_scan_export_dir`

Implemented per §2.A of `01_risk_review.md`. Resolves
`costar_exports/{subdir}/`, lists `.csv` files matching the export type's
filename regex, sorts by parsed date ascending. Returns
`[(path, date_str), ...]`.

R-303 (directory traversal) mitigated by `_resolve_costar_subdir`:
checks `(_COSTAR_BASE_DIR / subdir).resolve().relative_to(_COSTAR_BASE_DIR.resolve())`
and rejects with `ValueError` otherwise.

R-304 (missing dir) → returns `[]`.

R-305 (symlinks outside base) → per-entry resolve+relative_to check.

R-310 / R-311 (extra cols / empty file) → separated into header validator
and per-row validator; empty file yields `rows_loaded=0` and is archived.

### B. Schema validation — `_validate_submarket_stats_headers`, `_validate_submarket_stats_row`

Header validator returns `None` on success or an error string. Per-row
validator returns `(parsed_dict, None)` on success or `(None, error_msg)`.

R-308 (BOM) — handled via `_normalize_header` which strips `﻿` and
trims whitespace, plus `encoding="utf-8-sig"` in `_read_csv_with_bom`.
Verified by `tests/fixtures/costar/submarket_stats_with_bom.csv` (first
3 bytes `EF BB BF` confirmed via `python3 -c "open(...,'rb').read(5)"`).

R-309 (duplicate header) — pre-check `len(set(headers)) == len(headers)`.

R-310 (extra columns) — accepted; only required-column presence enforced.

R-306 (locale numbers) — `_coerce_optional_decimal` strips `$ , %`, then
floats. Returns `(None, None)` for blank/N/A/NA/NULL/-, returns
`(None, "unparseable...")` for unparseable.

R-307 (date formats) — `_parse_report_date` tries
`%Y-%m-%d`, `%m/%d/%Y`, `%Y-%m-%dT%H:%M:%S`, `%Y/%m/%d`. Returns ISO
date string on success.

Range checks per CoStar contract §Schema Validation: vacancy_rate_pct
in [0,100], availability_rate_pct in [0,100], asking_rent_nnn_psf > 0,
non-negative inventory/under_construction/proposed counts.

### C. Archive / fail movement — `_archive_file`, `_fail_file`

R-312 (atomic rename with cross-device fallback) — `_move_file` tries
`Path.replace()` first, falls back to `shutil.copy2 + Path.unlink` on
`OSError`.

R-313 (collision) — `_archive_destination` includes 4-hex random
suffix in the destination filename (mirrors `_make_cycle_id` pattern).

R-314 (DB-commits-then-archive failure) — DB commit happens INSIDE the
transaction context manager, then file move runs after. If the move
fails, the rows are already in Postgres and R-302 idempotent re-ingest
handles a future re-attempt cleanly.

`_fail_file` also writes a sibling `{stem}.error.json` with the failure
summary.

### D. markets / submarkets auto-UPSERT — R-301

`_ensure_submarket(conn, market_name, submarket_name)` returns
`(submarket_id, created, drift_msg)`. Slug derivation:
- `_slugify(value)` lowercases, replaces non-`[a-z0-9]` runs with `_`,
  strips edges, truncates to 60 chars; raises `ValueError` on empty
  result.
- `submarket_id = f"{market_id}__{_slugify(submarket_name)}"`.

UPSERT statements use `ON CONFLICT DO NOTHING`. The submarket UPSERT has
a `RETURNING submarket_name` clause so we know whether the row was newly
inserted; on no-row-returned we fetch the existing name and emit a
drift message if it differs (R-315).

Auto-creation triggers a `flagged_items(flag_type='data_gap')` row per
new submarket, prompting the human to backfill `submarkets.bbox` from
the corridor bounding boxes in `STORAGE_ARCHITECTURE.md`.

### E. `_load_submarket_stats_file` — per-file loader

Behavior:
1. Read CSV (utf-8-sig, header normalized).
2. Header validation → fail-file on missing/duplicate column.
3. Per-row validation → split into `parsed_rows` (queue) and
   `summary["row_errors"]` (data_gap flag candidates).
4. One DB transaction:
   - For each unique (market, submarket) in `parsed_rows`:
     `_ensure_submarket` (auto-UPSERT) → cache `submarket_id`.
     Emit `data_gap` flag for newly created submarkets,
     `conflict` flag for name drifts.
   - For each unique `(submarket_id, report_date)` tuple:
     execute `_SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST` (R-302).
   - For each parsed row: execute `_SQL_INSERT_MARKET_CONTEXT`.
   - One `_SQL_INSERT_RESEARCH_LOG_INGESTION` row with file-level
     summary (`action_type='ingestion'`).
   - One `_SQL_INSERT_FLAG` per row error.
5. Commit, archive the file, return `summary`.

R-318 (partial-file philosophy) — softened from CoStar contract:
row-level failures are flagged but the rest of the file ingests.
Rationale documented in §3 of the risk review and in the function
docstring; documented as a Phase 7 ratchet item in the reviewer
decision.

R-322 (placeholder loaders) — the four other registered export types
(`land_sales_comps`, `building_sales_comps`, `leasing_comps`,
`land_listings`) have placeholder loaders that report
`{"status": "not_implemented", "files_seen": [...]}` and take no
destructive action. Files staged in those folders remain in place.

### F. `run_ingestion_cycle` driver

Mirrors the Phase 3/5 pattern:
- `prepare.verify_parameters_unchanged()` first.
- `cycle_id = _make_ingestion_cycle_id()` — format
  `ingest-{ISO8601-Z}-{4hex}` (R-321).
- One connection per cycle.
- Cycle-id collision guard via
  `_count_log_rows_for_ingestion_cycle` (R-321).
- Iterates `_INGESTION_LOADERS` registry, dispatches to per-export-type
  loader.
- Returns summary dict with per-export-type results.

R-323 (parameter-free signature) — accepted; CoStar exports cover all
markets via the `market` column.

### G. Idempotent re-ingest — R-302

Implemented as DELETE-then-INSERT inside the per-file transaction.
The DELETE WHERE clause matches `source = 'costar' AND submarket_id = %s
AND as_of_date = %s`, keyed off the unique `(submarket_id, report_date)`
tuples in the parsed batch.

R-324 (readers see brief gap during DELETE-INSERT) accepted; the
metric SQL doesn't read `market_context` until Phase 7.

---

## 3. Risks addressed in code or accepted

### Mitigated in code (33)

R-301, R-302, R-303, R-304, R-305, R-306, R-307, R-308, R-309, R-311,
R-312, R-313, R-314, R-315, R-316, R-317, R-319, R-320, R-321, R-322,
R-323, R-324, R-325 (Five-File Contract), R-326 (SQL injection), R-327
(no print in ingestion helpers — verified by new test class
`TestPhase6SqlConstantsStaticChecks.test_no_print_in_ingestion_helpers`),
R-328 (Phase5FakeConnection reuse for tests), R-329 (tempdir +
monkey-patch for FS tests), R-331 (`COSTAR_EXPORTS_README.md` committed
at repo root), R-332 (mkdir on demand inside `_archive_destination`),
R-333 (small-file load assumption documented), R-334 (single-threaded
assumption documented), R-335 (csv.DictReader RFC 4180 quoting via
default `excel` dialect — well-tested in stdlib).

### Accepted with rationale (2)

R-310 (extra columns allowed — see §2.B).
R-318 (softer than CoStar contract on partial-file ingestion — Phase 7
ratchet item).
R-330 (`action_type='ingestion'` is a new vocab value — table column is
plain TEXT with no CHECK constraint, so DB accepts it; spec docs may
need updating in a future docs PR; not a blocker).

### Out of scope (still in Option A scope deferral list)

- The four other recurring export types — Phase 6.1+.
- The on-demand `tenant_intel` export — Phase 8+.
- Wiring `_compute_s4`/`s5`/`s6` to read from `market_context` — Phase 7.
- Refining `_compute_s8` to use `sales_comps` — Phase 7.
- The `harness_reports/costar_ingestion_{date}.json` JSON output —
  research_log + flagged_items rows are sufficient for Phase 6.
- Email staleness alerts — Phase 13.
- Hard cutoff on % row-level failures — Phase 7 followup.
- Excel/`.xlsx` parsing — CSV only.
- Modifying any of the immutable spec files.

---

## 4. Tests

12 new test classes, 57 new test methods, all passing:

| Class | Tests |
|---|---|
| TestPhase6Slugify | 6 |
| TestPhase6IngestionCycleId | 2 |
| TestPhase6ScanExportDir | 5 |
| TestPhase6ArchiveAndFailMovement | 3 |
| TestPhase6Coercion | 7 |
| TestPhase6DateParsing | 5 |
| TestPhase6HeaderValidation | 6 |
| TestPhase6RowValidation | 7 |
| TestPhase6EnsureSubmarket | 3 |
| TestPhase6LoadSubmarketStatsFile | 5 |
| TestPhase6Reingest | 2 |
| TestPhase6RunIngestionCycle | 4 |
| TestPhase6SqlConstantsStaticChecks | 2 |
| **Total new** | **57** |
| Pre-existing | 104 |
| **Grand total (test_discovery)** | **161** |

```
$ python3 -m unittest tests.test_discovery 2>&1 | tail -3
----------------------------------------------------------------------
Ran 161 tests in 0.176s

OK
```

`tests.test_harness` still passes when run as a separate process
(35 tests, OK). The pre-existing
`test_no_prepare_or_psycopg_imports` test in test_harness.py asserts
`prepare not in sys.modules`, which is violated when `discover` loads
test_discovery first — this is a pre-existing test-isolation issue
on the main branch and is not regressed by Phase 6 (verified via
`git stash && python3 -m unittest discover tests` on the prior head).

---

## 5. Sub-agent deviation note

This is the fifth orchestrator-inline phase
(Phase 2/3/3.1/4/5/6) due to sub-agent stream-idle timeouts in this
sandbox at ~270-480s. The risk review (R-300 series) and code
implementation were both written by the orchestrator with no separate
Agent 1 / Agent 2 sub-agent runs. A future session with stable
sub-agent streaming should ratify Phase 6 with full context
independence, same caveat as Phase 5.

---

AGENT 2 (orchestrator inline) DONE — handoff to Agent 3.
