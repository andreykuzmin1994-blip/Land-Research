# Phase 6.1 Code Writer Response — CoStar Comps + Listings Loaders

**Writer:** Agent 2 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Sixth orchestrator-inline
deviation (Phase 2/3/3.1/4/5/6/6.1).
**Date:** 2026-05-04.
**Branch:** `claude/costar-ingestion-setup-ovYfS`.
**Verdict from Agent 1:** GO-WITH-CONDITIONS (R-401 county→market lookup,
R-402 per-table dedup keys, R-417 update Phase 6 placeholder tests,
R-422 comp_type discriminator carried in DELETE).

---

## 1. Files changed

| File | Action | Net lines |
|---|---|---|
| `research.py` | edit (8 SQL constants, 4 validators, 1 generic loader, 4 per-export loaders, registry rewire, county→market lookup) | + |
| `tests/test_discovery.py` | edit (Phase 6 placeholder tests rewritten + 12 new Phase 6.1 test classes, 46 new tests) | + |
| `COSTAR_EXPORTS_README.md` | edit (reflects all 5 wired export types) | ± |
| `tests/fixtures/costar/land_sales_comps_happy.csv` | new | + |
| `tests/fixtures/costar/land_sales_comps_missing_column.csv` | new | + |
| `tests/fixtures/costar/land_sales_comps_row_errors.csv` | new | + |
| `tests/fixtures/costar/building_sales_comps_happy.csv` | new | + |
| `tests/fixtures/costar/building_sales_comps_missing_column.csv` | new | + |
| `tests/fixtures/costar/leasing_comps_happy.csv` | new | + |
| `tests/fixtures/costar/leasing_comps_missing_column.csv` | new | + |
| `tests/fixtures/costar/land_listings_happy.csv` | new | + |
| `tests/fixtures/costar/land_listings_missing_column.csv` | new | + |
| `tests/fixtures/costar/land_listings_optional_nulls.csv` | new | + |
| `reviews/09_phase6_1_costar_loaders/01_risk_review.md` | new | + |
| `reviews/09_phase6_1_costar_loaders/02_code_writer_response.md` | new | + |
| `reviews/09_phase6_1_costar_loaders/03_reviewer_decision.md` | new | + |

**Five-File Contract verification** (`git diff` against `ba0c7a9` Phase 6
head):

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

### A. County → market lookup (R-401)

Implemented `_COUNTY_TO_MARKET` constant with 8 Atlanta counties from
`appendix_a_county_connectors.md` L5 (Fulton, DeKalb, Cobb, Gwinnett,
Clayton, Henry, Spalding, Fayette). `_DEFAULT_INGESTION_MARKET = "Atlanta"`.
The `_resolve_market_from_county(county, default)` helper:
- normalizes case, strips whitespace
- returns `("Atlanta", False)` for known counties
- returns `(default, True)` for unknown / blank / None counties
- `True` flag triggers a per-file `data_gap` flag emission inside the
  loader

R-405 (Atlanta-only) accepted; Phase 11+ adds more.

### B. Per-export-type validators

4 new validators following the `_validate_submarket_stats_row` pattern.
Each:
- Normalizes header keys via `_normalize_header` (lowercase, strip BOM,
  strip whitespace).
- Returns `(parsed_dict, None)` or `(None, error_msg)`.
- Required text fields go through `_require_field`.
- Optional numeric fields via `_coerce_optional_decimal` /
  `_coerce_optional_int` (which already handle `$ , %` and N/A
  variants from Phase 6).
- Dates via `_parse_report_date` (multi-format: ISO, US slash,
  ISO+time, slash-Y/M/D).
- The full normalized row is preserved in `out["raw"]` so unmapped
  contract fields (`tenant_at_sale`, `lease_term_remaining_years`,
  `intended_use`, `topography_notes`, etc.) survive into the JSONB
  column (R-403).

Per-validator rules:

- **`_validate_land_sales_comps_row`** — `address`, `submarket` required;
  `acres` > 0; `sale_price` > 0 (R-406 confidential prices rejected;
  Phase 7+ ratchet); `sale_date` parseable; `cap_rate` optional null.
- **`_validate_building_sales_comps_row`** — `address`, `submarket`
  required; `building_sf` > 0; `sale_price` > 0; `year_built` in
  [1850, current+2] if present; `clear_height_ft` in [8, 80] if present;
  most other fields optional.
- **`_validate_leasing_comps_row`** — `address`, `submarket`,
  `tenant_name` required; `lease_term_months` > 0; `building_sf_leased`
  > 0; `starting_rent_psf_nnn` > 0 (R-410 confidential rent rejected;
  Phase 7+ ratchet); `naics_code` optional but must be all digits if
  present.
- **`_validate_land_listings_row`** — `address`, `submarket` required;
  `acres` > 0; `listing_date` parseable; `asking_price` /
  `asking_price_per_acre` optional null but > 0 if populated (per CoStar
  contract §Schema Validation); `days_on_market` >= 0 if populated.

### C. Generic per-file loader — `_ingest_one_comp_file`

The `_load_submarket_stats_file` shape is parameterised over: validator,
market resolver, INSERT SQL + params builder, DELETE SQL + params
builder, required column tuple, and export_type label. All 4 new
loaders flow through this helper, so the transaction shape, the
markets/submarkets auto-UPSERT, the data_gap flag emission, the
`research_log` row, the row-error flagging, and the archive/quarantine
logic are identical across export types and the submarket_stats loader.

This is the structural payoff of Phase 6 Option A's framework: the new
loaders are essentially a 5-line config dict each (per-export
constants + per-export params builders + a thin wrapper).

R-414 (per-row INSERT volume) and R-415 (transaction lock volume)
accepted — CoStar monthly/weekly batches are 50-200 rows.

### D. SQL constants — 8 new (R-402, R-413, R-422)

```
_SQL_DELETE_LAND_SALES_FOR_REINGEST            'land' comp_type, (submarket_id, address, sale_date)
_SQL_INSERT_LAND_SALES                         comp_type='land', raw::jsonb
_SQL_DELETE_BUILDING_SALES_FOR_REINGEST        'building' comp_type, (submarket_id, address, sale_date)
_SQL_INSERT_BUILDING_SALES                     comp_type='building', raw::jsonb
_SQL_DELETE_LEASING_COMPS_FOR_REINGEST         (submarket_id, address, tenant_name, lease_start_date)
_SQL_INSERT_LEASING_COMP                       raw::jsonb
_SQL_DELETE_LAND_LISTINGS_FOR_REINGEST         (snapshot_date, address) — snapshot semantics
_SQL_INSERT_LAND_LISTING                       snapshot_date stamped, is_active=TRUE
```

R-422 mitigated: the two `sales_comps` DELETE clauses include the literal
`'land'` / `'building'` constant inside the SQL so the comp_type
discriminator is always carried; cross-contamination of land vs building
re-ingest is impossible by SQL shape, not just by application code
discipline.

R-413 verified: `raw JSONB` columns receive `json.dumps(row["raw"])`
with a `%s::jsonb` cast — same pattern as Phase 5's
`_SQL_INSERT_PARCEL_SCORE`.

### E. Snapshot semantics for land_listings (R-426)

`snapshot_date` comes from the filename's `_{YYYYMMDD}_` group, NOT from
each row's `listing_date`. The row's `listing_date` is the date CoStar
recorded the listing's first appearance; `snapshot_date` is the date
this weekly crawl captured it. Two rows can share a `listing_date` but
appear in different `snapshot_date` snapshots — this is how the agent
will (in Phase 7+) detect listings that disappeared.

`_load_land_listings` driver (the Sequence-of-files wrapper) parses the
filename date once and passes it through as a string ISO date to
`_load_land_listings_file`, which inner-wraps the row validator to
stamp `snapshot_date` on each parsed row before insertion.

R-412 (cross-snapshot is_active flip) deferred to Phase 7+; documented.
R-426 (re-ingest of same snapshot replaces all rows) verified by
`TestPhase61LandListingsLoader.test_dedup_uses_snapshot_date_and_address`.

### F. Registry rewiring (R-417)

`_INGESTION_LOADERS` now points to 5 real loaders. The 4 placeholder
functions (`_load_placeholder`, `_load_*_placeholder`) were DELETED.
Tests verify their absence
(`TestPhase61SqlConstantsStaticChecks.test_placeholder_helpers_removed`).

The two Phase 6 tests that asserted placeholder semantics
(`TestPhase6RunIngestionCycle.test_no_files_returns_clean_summary`,
`test_placeholder_reports_files_seen_without_loading`) were rewritten /
removed. The first now asserts all 5 export types report
`status='loaded'` with `files_loaded=0` when no files are staged. The
second was removed entirely (the placeholder semantics it tested no
longer exist); a new
`TestPhase61RunIngestionCycleAllReal.test_mixed_files_dispatched_to_real_loaders`
test exercises the Phase 6.1 dispatch behavior end-to-end.

### G. Test fixtures

10 new synthetic CSVs under `tests/fixtures/costar/`:

```
land_sales_comps_happy.csv               3 rows, 3 distinct submarkets
land_sales_comps_missing_column.csv      header missing intended_use
land_sales_comps_row_errors.csv          1 happy + 1 blank-address +
                                          1 zero-sale_price + 1 unknown-county
building_sales_comps_happy.csv           2 rows, 2 distinct submarkets
building_sales_comps_missing_column.csv  header missing noi_at_sale
leasing_comps_happy.csv                  3 rows, 3 distinct submarkets
leasing_comps_missing_column.csv         header missing lease_start_date
land_listings_happy.csv                  3 rows (1 with null asking_price)
land_listings_missing_column.csv         header missing asking_price_per_acre
land_listings_optional_nulls.csv         minimal row, all optionals null
```

All staged through the same `.gitignore !tests/fixtures/costar/*.csv`
exception added in Phase 6.

---

## 3. Risks addressed in code or accepted

### Mitigated in code (26)

R-401, R-402, R-403, R-404, R-407, R-408 (range accepted, no rejection),
R-409, R-411 (acceptance), R-413, R-414, R-415, R-417, R-418 (Five-File
Contract), R-419 (parameterised SQL — verified by static AST check),
R-420 (no print in new helpers — extended forbidden-names set), R-421
(no UPDATE/DELETE on parcel_scores), R-422, R-423, R-424, R-425, R-426,
R-427, R-428 (registry signature), R-429 (Phase 6 tests still pass),
R-430 (realistic fixture data).

### Accepted with rationale (4)

- **R-405** Atlanta-only county→market lookup. Phase 11+.
- **R-406 / R-410** Confidential sale price / rent rejected. Phase 7+
  ratchet to optionally allow null with a flag.
- **R-412** Cross-snapshot `is_active=FALSE` flipping for disappearing
  listings. Phase 7+ join.
- **R-416** `_DEFAULT_INGESTION_MARKET = "Atlanta"` hardcoded.
  Phase 11.

### Out of scope

- Tenant intel (Export 6) — Phase 8+.
- Multi-market expansion — Phase 11+.
- Wiring `_compute_s4`/`s5`/`s6` from `market_context` — Phase 7.
- Wiring `_compute_s8` from `sales_comps` — Phase 7.
- Wiring strategy-fit signals from `leasing_comps` — Phase 8.
- The `harness_reports/costar_ingestion_{date}.json` JSON output —
  deferred from Phase 6, still deferred.
- Modifying any of the immutable spec / schema files.
- Adding pandas / openpyxl / Excel parsing.

---

## 4. Tests

12 new test classes, 46 new test methods, all passing:

| Class | Tests |
|---|---|
| TestPhase61CountyToMarket | 5 |
| TestPhase61LandSalesCompsValidation | 6 |
| TestPhase61BuildingSalesCompsValidation | 5 |
| TestPhase61LeasingCompsValidation | 6 |
| TestPhase61LandListingsValidation | 6 |
| TestPhase61LandSalesCompsLoader | 4 |
| TestPhase61BuildingSalesCompsLoader | 2 |
| TestPhase61LeasingCompsLoader | 2 |
| TestPhase61LandListingsLoader | 4 |
| TestPhase61RunIngestionCycleAllReal | 2 |
| TestPhase61SqlConstantsStaticChecks | 4 |
| **New total** | **46** |
| Phase 6 baseline (post-update) | 160 |
| **Grand total (test_discovery)** | **206** |

```
$ python3 -m unittest tests.test_discovery 2>&1 | tail -3
----------------------------------------------------------------------
Ran 206 tests in 0.345s

OK
```

`tests.test_harness` still passes when run alone (35 tests OK).
The pre-existing `test_no_prepare_or_psycopg_imports` cross-module
isolation issue from Phase 6 is unchanged — not regressed.

---

## 5. Sub-agent deviation note

Seventh orchestrator-inline phase. Same precedent as
Phase 2/3/3.1/4/5/6 — sub-agent stream-idle timeouts in this sandbox.
The Phase 6 Option A framework was deliberately structured to make
Phase 6.1 a tight delta, which it was: the bulk of new code is the 4
validators (~250 lines) and the generic `_ingest_one_comp_file` helper
(~150 lines); each new per-export-type loader is a 12-line config call.

A future session with stable sub-agent streaming should ratify Phase 6.1
with full context independence.

---

AGENT 2 (orchestrator inline) DONE — handoff to Agent 3.
