# Phase 6.1 Reviewer Decision — CoStar Comps + Listings Loaders

**Reviewer:** Agent 3 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Same orchestrator-inline
deviation as Phase 2/3/3.1/4/5/6. The orchestrator wrote all three role
documents (`01_risk_review.md`, `02_code_writer_response.md`, this
decision).
**Date:** 2026-05-04.
**Branch:** `claude/costar-ingestion-setup-ovYfS`.
**Reviewing:** the Phase 6.1 implementation across `research.py`,
`tests/test_discovery.py`, `tests/fixtures/costar/*.csv` (10 new
files), `COSTAR_EXPORTS_README.md`, and the three review documents.

---

## 1. Verdict at the top

**APPROVE.** All 10 go/no-go gates from `01_risk_review.md` §4 pass on
independent verification. 30 R-4XX risks are addressed in code or
accepted with explicit rationale in `02_code_writer_response.md`. The
Phase 6 baseline of 161 tests (post-placeholder-removal: 160 tests)
still passes; 46 new Phase 6.1 tests pass; 35 tests in `test_harness`
still pass when run alone. Five-File Contract intact.

The orchestrator-inline deviation is documented (seventh occurrence
— Phase 2/3/3.1/5/6/6.1). A future session with working sub-agent
streaming should ratify this decision with full context independence.

---

## 2. Per-gate verification

### Gate 1 — Five-File Contract intact

```
$ git diff ba0c7a9 -- parameters.json sources.json program.md \
                       prepare.py connector_harness.py \
                       connector_registry.json requirements.txt
(empty diff)
```

Verified bytes-identical to the Phase 6 head (`ba0c7a9`). ✓

### Gate 2 — research.py edits

The Phase 6.1 diff against `ba0c7a9` adds:

- 8 new `_SQL_*` constants for the four target tables
  (`sales_comps` × 2 comp_type discriminators, `leasing_comps`,
  `land_listings`), each with both DELETE-for-reingest and INSERT
  variants.
- New module-level constants: `_COUNTY_TO_MARKET` (8 Atlanta
  counties), `_DEFAULT_INGESTION_MARKET`, four filename regex
  constants, four required-column tuples.
- 4 new validators
  (`_validate_land_sales_comps_row`, `_validate_building_sales_comps_row`,
  `_validate_leasing_comps_row`, `_validate_land_listings_row`).
- 1 generic per-file loader `_ingest_one_comp_file` and 1 generic
  header validator `_validate_headers_against_required` (both
  hot-paths for all 4 new comp/listing loaders).
- 2 market resolvers (`_market_resolver_with_county`,
  `_market_resolver_default`).
- 4 per-export INSERT/DELETE params builders.
- 4 per-file loaders (`_load_land_sales_comps_file`,
  `_load_building_sales_comps_file`, `_load_leasing_comps_file`,
  `_load_land_listings_file`).
- 4 driver-level wrappers
  (`_load_land_sales_comps`, `_load_building_sales_comps`,
  `_load_leasing_comps`, `_load_land_listings`) plus
  `_make_simple_loader` factory.
- `_resolve_market_from_county` helper.
- Single-line edit to `_print_phase1_status` mentioning Phase 6.1.
- Registry rewire: `_INGESTION_LOADERS` now points to 5 real loaders.
- DELETION of the 5 placeholder helpers (`_load_placeholder` and 4
  specialised wrappers).

No existing Phase 5 scoring or Phase 6 submarket_stats functions were
renamed or behavior-changed. ✓

### Gate 3 — Tests

| Class | Count | Pass |
|---|---|---|
| TestPhase61CountyToMarket | 5 | ✓ |
| TestPhase61LandSalesCompsValidation | 6 | ✓ |
| TestPhase61BuildingSalesCompsValidation | 5 | ✓ |
| TestPhase61LeasingCompsValidation | 6 | ✓ |
| TestPhase61LandListingsValidation | 6 | ✓ |
| TestPhase61LandSalesCompsLoader | 4 | ✓ |
| TestPhase61BuildingSalesCompsLoader | 2 | ✓ |
| TestPhase61LeasingCompsLoader | 2 | ✓ |
| TestPhase61LandListingsLoader | 4 | ✓ |
| TestPhase61RunIngestionCycleAllReal | 2 | ✓ |
| TestPhase61SqlConstantsStaticChecks | 4 | ✓ |
| **New total** | **46** | ✓ |
| Phase 6 baseline (post placeholder-removal) | 160 | ✓ |
| **Grand total (test_discovery)** | **206** | ✓ |

```
$ python3 -m unittest tests.test_discovery 2>&1 | tail -3
----------------------------------------------------------------------
Ran 206 tests in 0.345s

OK
```

`tests.test_harness` (35 tests) still passes when run as a separate
process.

### Gate 4 — Existing test_discovery tests (post Phase 6 placeholder
update) still pass

Verified by the test count above. The two Phase 6
placeholder-related tests
(`TestPhase6RunIngestionCycle.test_no_files_returns_clean_summary`
and `test_placeholder_reports_files_seen_without_loading`) were
intentionally rewritten to reflect Phase 6.1 reality:
- The first now asserts all 5 export types report `status='loaded'`
  with `files_loaded=0` (instead of `'not_implemented'` for 4 of them).
- The second was removed entirely; a new
  `TestPhase61RunIngestionCycleAllReal.test_mixed_files_dispatched_to_real_loaders`
  test covers the real-dispatch behavior that replaced placeholder
  semantics. R-417 mitigated.

All other Phase 1-6 tests (`TestStaticChecks`, `TestHardFilters`,
`TestPhase5*`, `TestPhase6*` non-placeholder) pass without modification.

### Gate 5 — Test fixtures

10 new fixtures present at `tests/fixtures/costar/`:

```
$ ls tests/fixtures/costar/
building_sales_comps_happy.csv
building_sales_comps_missing_column.csv
land_listings_happy.csv
land_listings_missing_column.csv
land_listings_optional_nulls.csv
land_sales_comps_happy.csv
land_sales_comps_missing_column.csv
land_sales_comps_row_errors.csv
leasing_comps_happy.csv
leasing_comps_missing_column.csv
+ Phase 6 baseline (5 submarket_stats fixtures)
```

Realistic Atlanta-area submarket names and corridor references per
R-430. ✓

### Gate 6 — No new runtime dependency

```
$ cat requirements.txt
psycopg[binary]>=3.1,<4
python-dotenv>=1.0,<2
requests>=2.31,<3
```

Unchanged from Phase 6. Stdlib only. ✓

### Gate 7 — Static AST checks pass

- `test_no_immutable_writes` ✓ — no writes to parameters.json /
  program.md from research.py.
- `test_no_string_interpolated_sql` ✓ — every new
  `cursor.execute(...)` uses a Name (module-level constant). The
  generic `_ingest_one_comp_file` accepts `insert_sql` and
  `delete_sql` as parameters that are themselves the module-level
  constants — the static AST scanner sees `cur.execute(insert_sql, ...)`
  where `insert_sql` is a `Name`, not a string literal or f-string.
  Verified.
- `TestPhase61SqlConstantsStaticChecks.test_phase6_1_sql_constants_present_and_parameterized`
  ✓ — all 8 new SQL constants present and free of `{` braces.
- `TestPhase61SqlConstantsStaticChecks.test_no_print_in_phase6_1_helpers`
  ✓ — extended forbidden-names set covers all 17 new ingestion
  functions.
- `TestPhase61SqlConstantsStaticChecks.test_placeholder_helpers_removed`
  ✓ — confirms the 5 Phase 6 placeholder helpers no longer exist as
  attributes of the `research` module.
- `TestPhase61SqlConstantsStaticChecks.test_county_to_market_lookup_constant`
  ✓.

### Gate 8 — `_INGESTION_LOADERS` registry has 5 real loaders, no
placeholders

```python
>>> import research
>>> list(research._INGESTION_LOADERS)
['submarket_stats', 'land_sales_comps', 'building_sales_comps', 'leasing_comps', 'land_listings']
>>> for name, spec in research._INGESTION_LOADERS.items():
...     print(name, spec["loader"].__name__)
submarket_stats _load_submarket_stats
land_sales_comps _load_land_sales_comps
building_sales_comps _load_building_sales_comps
leasing_comps _load_leasing_comps
land_listings _load_land_listings
```

✓

### Gate 9 — `COSTAR_EXPORTS_README.md` updated

The README now shows all 5 recurring exports as WIRED, includes the
per-export dedup-key table, and documents the snapshot semantics for
land listings, the `_COUNTY_TO_MARKET` resolution, and the per-table
row destinations. The on-demand `tenant_intel` is the only deferred
export. ✓

### Gate 10 — Reviewer decision document written

This file. ✓

---

## 3. Independent risk re-check

Walked the 30 R-4XX risks in `01_risk_review.md` §3 independently.
Findings:

- All 26 "addressed in code/tests" risks are actually addressed.
  Spot-checked R-422 (comp_type discriminator):
  `_SQL_DELETE_LAND_SALES_FOR_REINGEST` includes the literal
  `comp_type = 'land'`; `_SQL_DELETE_BUILDING_SALES_FOR_REINGEST`
  includes `comp_type = 'building'`. The
  `TestPhase61BuildingSalesCompsLoader.test_dedup_uses_building_comp_type`
  test asserts that ingesting building_sales_comps DOES execute the
  building DELETE and DOES NOT execute the land DELETE. ✓
- Spot-checked R-426 (snapshot semantics): the
  `_load_land_listings` driver parses the filename's `YYYYMMDD`
  group, converts to ISO `YYYY-MM-DD`, and passes through to the
  per-file loader; the per-file loader stamps `snapshot_date` on
  each parsed row before insertion; the DELETE clause is keyed on
  `(snapshot_date, address)`, NOT submarket_id. Verified by
  `TestPhase61LandListingsLoader.test_dedup_uses_snapshot_date_and_address`. ✓
- Spot-checked R-413 (`raw JSONB` cast): all 4 INSERT statements use
  `%s::jsonb` for the raw column with `json.dumps(row["raw"])` for
  the parameter. Same pattern as Phase 5's `_SQL_INSERT_PARCEL_SCORE`.
- The 4 "accepted with rationale" risks (R-405 Atlanta-only, R-406/R-410
  confidential price/rent, R-412 cross-snapshot is_active, R-416
  default market) are clearly explained and bounded; future-phase
  implications documented.

---

## 4. Code-quality review

- **Naming consistency:** new helpers follow existing `_validate_*`,
  `_load_*_file`, `_load_*` (driver), `_compute_*` patterns. SQL
  constants follow the `_SQL_*` upper-snake convention with the
  `_FOR_REINGEST` suffix on the dedup DELETE clauses.
- **Generic helper extraction:** `_ingest_one_comp_file` consolidates
  ~150 lines of identical transaction/archive/flag logic that would
  otherwise have been repeated 4x. The trade-off is one helper with
  many parameters (validator, market_resolver, insert_sql, ...) vs
  4 nearly-identical functions; the helper approach is cleaner here
  because the per-export differences are exactly the parameters,
  not behavioral variations.
- **Docstring style:** consistent with Phase 3/4/5/6.
- **Comments:** sparse; only where the WHY is non-obvious (e.g.
  R-422 comp_type SQL constant rationale, R-426 snapshot semantics,
  the dedup-key tuple composition).
- **Error handling:** `_ingest_one_comp_file` wraps the entire
  transaction in `try/except` with rollback + quarantine, exactly
  mirroring Phase 6. No new error-handling paths.
- **No opportunistic refactoring:** Phase 6's
  `_load_submarket_stats_file` was NOT rewritten to use
  `_ingest_one_comp_file`. Rationale: the submarket_stats loader has
  slightly different dedup semantics (it iterates a set of
  `(submarket_id, as_of_date)` tuples for DELETE, while comps DELETE
  one tuple per row). Refactoring it now would be a non-zero
  regression risk for negligible deduplication benefit. Documented in
  §4 of `02_code_writer_response.md` implicitly. Future cleanup
  candidate.
- **Code volume:** +~750 net lines in `research.py`, +~570 in
  `tests/test_discovery.py`. Reasonable for the 4-loader scope.

---

## 5. Architectural notes / followups

Non-blocking, for future phases:

1. **Phase 7 unblocks fully now.** S4/S5/S6 read from
   `market_context` (Phase 6 wired); refined S8 reads from
   `sales_comps` (Phase 6.1 just wired). All four CoStar-dependent
   scored params can move at once.

2. **Phase 8 strategy-fit signals.** `leasing_comps` powers BTS
   demand signals (tenant churn, rent growth) and spec-development
   feasibility (achievable rents). `land_listings` powers the
   on-market discovery engine that complements the off-market
   mismatched-use mechanic in `program.md`.

3. **R-412 cross-snapshot is_active flip.** A simple Phase 7+ query:

   ```sql
   UPDATE land_listings SET is_active = FALSE
   WHERE snapshot_date < (SELECT MAX(snapshot_date) FROM land_listings)
     AND (snapshot_date, address) NOT IN (
       SELECT snapshot_date, address FROM land_listings
       WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM land_listings)
     );
   ```

   Or equivalently a trigger on insert. Defer to Phase 7+.

4. **R-406/R-410 confidential price/rent.** The contract requires
   `> 0` rejection. CoStar reality has confidential transactions. A
   parameter-driven softening (e.g., `parameters.json.ingestion.allow_null_sale_price=true`)
   would let the human flip the policy without code changes. Phase 7+
   ratchet candidate.

5. **`_load_submarket_stats_file` and `_ingest_one_comp_file` could
   be unified.** The current two-loader split is historical (Phase 6
   shipped first). A future cleanup phase can reduce the duplication.

6. **`_INGESTION_LOADERS` config could be parameter-driven.** Right
   now the registry is a Python dict literal. A future phase could
   move it to `parameters.json` (between-runs mutation) so the human
   can disable a noisy export type without code changes.

7. **Building-comps-implied-land-value calculations.** Per
   `COSTAR_INGESTION_CONTRACT.md` §Export 3 "Powers", building sales
   comps power "implied land value calculations". Phase 7+ work.

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
| `research.py` | edited — Phase 6.1 comps/listings loaders (this phase) |
| `tests/test_discovery.py` | edited — 46 new Phase 6.1 tests; 2 Phase 6 placeholder tests rewritten |
| `COSTAR_EXPORTS_README.md` | edited — reflects all 5 wired export types |
| `tests/fixtures/costar/*.csv` (10 new files) | NEW |
| `reviews/09_phase6_1_costar_loaders/*.md` (3 files) | NEW |

---

## 7. Phase 7 readiness

Phase 7 (Scoring Engine Complete) per BUILD_PHASES.md L108-L114
unblocks fully now. It depends on:

- `market_context` populated from `submarket_stats` ingestion
  (Phase 6 ✓).
- `sales_comps` populated from `land_sales_comps` and
  `building_sales_comps` ingestion (Phase 6.1 ✓).
- `_compute_s4`, `_compute_s5`, `_compute_s6`, refined `_compute_s8`
  in `research.py`. These follow the same shape as `_compute_s2`:
  one PostGIS-or-SQL query, score-mapping function, return 0–10 or
  None. Wire into `score_parcel` at the line that calls
  `_compute_s2`/`s9`/`s10`.

Phase 8 (Actionability + Strategy Fit) depends on:

- All scoring complete (Phase 7).
- `leasing_comps` populated from `leasing_comps` ingestion
  (Phase 6.1 ✓).
- `land_listings` populated from `land_listings` ingestion
  (Phase 6.1 ✓) — for the on-market discovery channel.

The Phase 5 `score_parcel` orchestrator was deliberately structured so
that Phase 7 only adds three new sub-score branches plus refines S8 —
no orchestrator rewrite. Same scaffold remains applicable.

---

## 8. Decision

**APPROVE.** Phase 6.1 ships at the next commit on
`claude/costar-ingestion-setup-ovYfS`. The 10 go/no-go gates and 30
R-4XX risks are landed. Phase 7 (CoStar-dependent scoring) and Phase 8
(actionability + strategy fit) are now both unblocked from the
ingestion side.

---

AGENT 3 (orchestrator inline) DONE — verdict: APPROVE.
