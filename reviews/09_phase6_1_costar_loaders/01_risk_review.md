# Phase 6.1 Risk and Architecture Review — CoStar Ingestion (4 Remaining Export Types)

**Reviewer:** Agent 1 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Sixth orchestrator-inline
deviation; same precedent as Phase 2/3/3.1/4/5/6 (sub-agent stream-idle
timeouts in this sandbox).
**Date:** 2026-05-04.
**Branch:** `claude/costar-ingestion-setup-ovYfS`.
**Scope:** Phase 6.1 — replace the four placeholder loaders that Phase 6
Option A registered with real loaders. The four export types per
`COSTAR_INGESTION_CONTRACT.md`:

1. **Export 2** — `land_sales_comps` (monthly) → `sales_comps` table with
   `comp_type='land'`.
2. **Export 3** — `building_sales_comps` (monthly) → `sales_comps` table
   with `comp_type='building'`.
3. **Export 4** — `leasing_comps` (monthly) → `leasing_comps` table.
4. **Export 5** — `land_listings` (weekly) → `land_listings` table.

Tenant intel (Export 6) remains out of scope per `BUILD_PHASES.md`
(on-demand, Phase 8+).

---

## 1. Verdict at the top

**GO-WITH-CONDITIONS.** Phase 6 Option A delivered a clean framework:
folder scan, header validation, archive/fail movement, idempotent
re-ingest, markets/submarkets auto-UPSERT, registry-style dispatch.
Phase 6.1 is structurally a 4-times repetition of the
`_load_submarket_stats_file` pattern, with three architectural decisions
specific to comps/listings:

1. **R-401 (no `market` column in 4 of 4 export types).** The contracts
   for Exports 2–5 do not require a `market` column — only `submarket`
   (and sometimes `county`). `_ensure_submarket` needs `(market_name,
   submarket_name)` to derive the deterministic submarket_id consistent
   with Phase 6 Option A's submarket_stats output. **Mitigation:** add
   a county→market lookup `_COUNTY_TO_MARKET` for the 8 Atlanta
   counties (`appendix_a_county_connectors.md` L5). Loaders that have
   `county` (land_sales_comps, land_listings) derive market from county.
   Loaders that lack `county` (building_sales_comps, leasing_comps)
   default to a configured `_DEFAULT_INGESTION_MARKET = "Atlanta"` and
   emit a `data_gap` flag noting the assumption — this is acceptable
   because all Atlanta-target CoStar saved searches are already
   filtered to Atlanta submarkets, and Phase 11+ multi-market expansion
   will revisit. Document.

2. **R-402 (per-export idempotent dedup keys).** Phase 6 Option A used
   `(submarket_id, as_of_date, source='costar')` for `market_context`.
   Each new table needs its own dedup key so a re-delivered file is
   replaced, not duplicated. Decisions:

   | Table              | Dedup key                                                  |
   |--------------------|-----------------------------------------------------------|
   | `sales_comps` land | `(submarket_id, address, sale_date, comp_type='land')`     |
   | `sales_comps` bldg | `(submarket_id, address, sale_date, comp_type='building')` |
   | `leasing_comps`    | `(submarket_id, address, tenant_name, lease_start_date)`   |
   | `land_listings`    | `(snapshot_date, address)` — snapshot semantics            |

   Land listings get snapshot semantics because the export is a
   point-in-time crawl (weekly): re-delivering the same week's file
   replaces all rows with that `snapshot_date`; new weeks add new
   `snapshot_date`-keyed rows; the `is_active` column stays
   default-true (cross-snapshot is_active management is a Phase 7+
   join, not Phase 6.1). Document.

3. **R-403 (`raw` JSONB stores extra columns the schema doesn't model).**
   Building sales comps ship `tenant_at_sale` and
   `lease_term_remaining_years` (CoStar contract §Export 3) which have
   no column in `sales_comps`. Land sales comps ship `intended_use`
   which also has no column. Leasing comps may ship `naics_code` (in
   schema) but the contract §Export 4 doesn't list it. Land listings
   ship `topography_notes` which has no column. **Mitigation:** the
   schema already includes a `raw JSONB` column on each of the four
   tables. The loader serializes the entire validated row dict into
   `raw` so nothing is lost; modeled columns are also populated for
   query-friendliness. Phase 7+ snapshot generator can pull from `raw`
   for narrative writeups.

The condition for full GO is that Agent 2 implements R-401 (county→market
lookup + default market for tablesless-of-county) and R-402 (per-table
dedup keys with idempotent DELETE-then-INSERT). R-403 is naturally
addressed by the existing schema's `raw JSONB` columns.

---

## 2. Per-deliverable risks

### A. County → market lookup (R-401)

```python
_COUNTY_TO_MARKET: dict[str, str] = {
    "fulton":   "Atlanta",
    "dekalb":   "Atlanta",
    "cobb":     "Atlanta",
    "gwinnett": "Atlanta",
    "clayton":  "Atlanta",
    "henry":    "Atlanta",
    "spalding": "Atlanta",
    "fayette":  "Atlanta",
}
_DEFAULT_INGESTION_MARKET = "Atlanta"
```

Lookup is case-insensitive. `_resolve_market(county, default_market)`:
1. If `county` is non-empty, slugify and look up; on hit, return.
2. If `county` is empty or not in the lookup, return `default_market`
   and emit a flag once per file.

**Risks:**
- **R-404** — county not in lookup (e.g., Forsyth, Bartow, Cherokee).
  Mitigation: emit `flagged_items(flag_type='data_gap',
  description='ingestion: unknown county <X> not in county→market
  map; defaulted to <default>')` once per file, not per row.
- **R-405** — lookup is hard-coded for Atlanta only. Phase 11+ adds
  Orlando, Chicago, Lehigh Valley. Documented as out-of-scope; the
  lookup is just a const dict, easy to expand later.

### B. Per-export-type validators

Each follows the `_validate_submarket_stats_row` pattern: returns
`(parsed_dict, error_or_None)`.

#### B.1 `_validate_land_sales_comps_row`

Required columns (per CoStar contract §Export 2):

```
address, parcel_id, county, submarket, acres, sale_date, sale_price,
price_per_acre, buyer_name, seller_name, zoning, intended_use, cap_rate
```

Validation:
- `submarket` non-empty
- `address` non-empty (used in dedup key, so cannot be NULL)
- `acres` parseable, > 0
- `sale_date` parseable
- `sale_price` parseable, > 0 (CoStar contract §Schema Validation:
  "sale price > 0")
- `price_per_acre` parseable, optional (we can recompute if missing)
- `cap_rate` optional null (per contract: "N/A for land")
- `parcel_id`, `county`, `buyer_name`, `seller_name`, `zoning`,
  `intended_use` may be null/blank

**Risks:**
- **R-406** — confidential / undisclosed sale prices. CoStar sometimes
  anonymizes. Per contract `sale_price > 0` is required; reject row
  with row-level flag. Future-phase ratchet: relax to allow null with
  a flag (Phase 7+).
- **R-407** — `address` required for dedup. Reject row if blank;
  document that this excludes "near intersection of X and Y" style
  unaddressed sales (rare in CoStar).

#### B.2 `_validate_building_sales_comps_row`

Required columns (per CoStar contract §Export 3):

```
address, submarket, building_sf, year_built, clear_height_ft,
sale_date, sale_price, price_psf, cap_rate, noi_at_sale,
buyer_name, seller_name, tenant_at_sale, lease_term_remaining_years
```

Validation:
- `submarket` non-empty
- `address` non-empty (dedup)
- `building_sf` parseable, > 0
- `sale_date` parseable
- `sale_price` parseable, > 0
- `price_psf` optional (recomputable)
- `cap_rate`, `noi_at_sale` optional (commonly populated for buildings)
- `year_built` optional, between 1850 and current year + 2 if populated
- `clear_height_ft` optional, between 8 and 80 if populated
- `tenant_at_sale`, `lease_term_remaining_years` flow into `raw` (R-403)

**Risks:**
- **R-408** — `building_sf < 50000` would violate the contract filter
  ("50,000+ SF") but not the schema. Don't reject; just accept (the
  CoStar saved search is the contract-level filter). Document.

#### B.3 `_validate_leasing_comps_row`

Required columns (per CoStar contract §Export 4):

```
address, submarket, tenant_name, tenant_industry, lease_start_date,
lease_term_months, building_sf_leased, starting_rent_psf_nnn,
rent_escalation_pct, lease_type
```

Validation:
- `submarket` non-empty
- `address` non-empty (dedup)
- `tenant_name` non-empty (dedup)
- `lease_start_date` parseable (dedup)
- `lease_term_months` parseable, > 0
- `building_sf_leased` parseable, > 0
- `starting_rent_psf_nnn` parseable, > 0
- `lease_type` optional (informational; commonly NNN/MG/FSG)
- `rent_escalation_pct` optional null (often missing)
- `naics_code` optional, validated as a digit string if present

**Risks:**
- **R-409** — leases re-signed by same tenant at same address with
  identical start date are extremely rare; the dedup key
  `(submarket_id, address, tenant_name, lease_start_date)` is sound.
- **R-410** — confidential rent. Same as R-406 — reject row with flag;
  Phase 7+ ratchet.

#### B.4 `_validate_land_listings_row`

Required columns (per CoStar contract §Export 5):

```
address, parcel_id, county, submarket, acres, zoning, topography_notes,
asking_price, asking_price_per_acre, listing_date, days_on_market,
listing_broker, listing_broker_firm, utilities_status, entitlement_status
```

Validation:
- `submarket` non-empty
- `address` non-empty (dedup)
- `acres` parseable, > 0
- `listing_date` parseable
- `asking_price` optional (per contract §Schema Validation: "asking
  price > 0 if populated (null acceptable)") — null OK, > 0 if set
- `asking_price_per_acre` same nullable contract
- `days_on_market` optional, >= 0 if populated
- `topography_notes`, `parcel_id`, `county`, `zoning`,
  `listing_broker`, `listing_broker_firm`, `utilities_status`,
  `entitlement_status` may be null/blank

**Risks:**
- **R-411** — listing date in the future (data entry error). Mitigation:
  accept; flag if > 30 days in the future.
- **R-412** — listings disappear from the next snapshot (sold/withdrawn).
  Phase 6.1 doesn't update prior `is_active` to false. Phase 7+ adds
  the cross-snapshot diff. Document in §3 below.

### C. Per-export-type loaders

Each loader follows the `_load_submarket_stats_file` shape almost
verbatim:

1. Read CSV (utf-8-sig).
2. Validate headers (file-level fail → quarantine).
3. Per-row validate (row-level fail → flag, drop row).
4. Inside one transaction:
   a. For each unique `(market, submarket)` from rows:
      `_ensure_submarket` (auto-UPSERT). Where `market` is derived per
      R-401.
   b. For each unique dedup key (R-402): execute the per-table
      `_SQL_DELETE_*_FOR_REINGEST`.
   c. For each parsed row: execute the per-table `_SQL_INSERT_*`.
   d. One `_SQL_INSERT_RESEARCH_LOG_INGESTION` row with file-level
      summary.
   e. One `_SQL_INSERT_FLAG` per row error / county-default / drift.
5. Commit, archive the file, return summary.

**Net new SQL constants** (8 total):

```
_SQL_DELETE_LAND_SALES_FOR_REINGEST       _SQL_INSERT_LAND_SALES
_SQL_DELETE_BUILDING_SALES_FOR_REINGEST   _SQL_INSERT_BUILDING_SALES
_SQL_DELETE_LEASING_COMPS_FOR_REINGEST    _SQL_INSERT_LEASING_COMP
_SQL_DELETE_LAND_LISTINGS_FOR_REINGEST    _SQL_INSERT_LAND_LISTING
```

Two of the SQL constants share the underlying `sales_comps` table but
parameterize on `comp_type='land'` vs `comp_type='building'` so the
DELETE-then-INSERT is correctly scoped.

**Risks:**
- **R-413** — the `raw JSONB` column is populated with `json.dumps(row)`
  on each INSERT. psycopg3 passes `str` to a `JSONB` column with `::jsonb`
  cast. Mitigation: keep the same `%s::jsonb` pattern as
  `_SQL_INSERT_PARCEL_SCORE`. Verified.
- **R-414** — INSERT volume per file. CoStar monthly comps are
  typically 50-200 rows per export. Far below psycopg3 batching
  thresholds; per-row INSERT is fine. Document.
- **R-415** — inside one transaction, large numbers of rows might block
  on locks. Acceptable for monthly batches.
- **R-416** — `_DEFAULT_INGESTION_MARKET` choice. Atlanta-only is the
  current scope. When Phase 11 adds Orlando, the default needs to flip
  to per-saved-search config. Documented as a future-phase parameter.

### D. Registry rewiring

`_INGESTION_LOADERS` currently has 5 entries (1 real + 4 placeholders).
Phase 6.1 replaces the 4 placeholder loaders with the new real ones. No
new export types are added. The placeholder helper (`_load_placeholder`,
`_load_*_placeholder`) and its 4 specialised wrappers are DELETED — they
served only as a transitional scaffold for Phase 6 Option A.

**Risks:**
- **R-417** — TestPhase6RunIngestionCycle.test_no_files_returns_clean_summary
  asserts `status='not_implemented'` for the 4 placeholder loaders.
  After Phase 6.1, that assertion is invalid and the test must be
  updated to reflect that all 5 loaders are now real. Mitigation:
  rewrite that test plus
  test_placeholder_reports_files_seen_without_loading (which tests the
  land_sales_comps placeholder semantics) to assert the new behavior.

### E. Tenant intel registration

Out of scope. Phase 6.1 does not add a `tenant_intel` registry entry.
The contract specifies it as on-demand (Phase 8+); leaving it
unregistered means files dropped into `costar_exports/tenant_intel/`
are silently ignored, which is the right Phase 6.1 default.

### F. Test fixtures

8+ new fixtures under `tests/fixtures/costar/`:

```
land_sales_comps_happy.csv
land_sales_comps_missing_column.csv
land_sales_comps_row_errors.csv
building_sales_comps_happy.csv
building_sales_comps_missing_column.csv
leasing_comps_happy.csv
leasing_comps_missing_column.csv
land_listings_happy.csv
land_listings_optional_nulls.csv      # asking_price null acceptable
land_listings_missing_column.csv
```

The duplicate-header / BOM / row-errors variations from Phase 6 cover
the base validator behavior; per-export-type fixtures focus on
type-specific schema variation. **Risks:** none — fixtures are
synthetic.

---

## 3. Cross-cutting risks (R-400 series)

- **R-401** — county→market lookup — see §A. Mitigated.
- **R-402** — per-export dedup keys — see top. Mitigated.
- **R-403** — `raw JSONB` for unmapped columns — see top. Already
  mitigated by schema.
- **R-404** — unknown county — see §A. Flag once per file.
- **R-405** — Atlanta-only lookup — accepted, documented.
- **R-406** — confidential sale prices — accepted, documented as
  Phase 7+ ratchet.
- **R-407** — blank address rejected — accepted.
- **R-408** — building_sf below saved-search filter — accepted.
- **R-409** — lease dedup key soundness — accepted.
- **R-410** — confidential rent — accepted, Phase 7+ ratchet.
- **R-411** — future listing dates — flag, accept.
- **R-412** — `is_active` cross-snapshot — Phase 7+. Documented.
- **R-413** — `raw JSONB` cast pattern — verified.
- **R-414** — per-row INSERT volume — accepted.
- **R-415** — transaction lock volume — accepted.
- **R-416** — default market hardcoding — accepted, Phase 11.
- **R-417** — Phase 6 placeholder tests need updating — see §D.
- **R-418** — Five-File Contract. **NO** edits to `prepare.py`,
  `parameters.json`, `sources.json`, `program.md`,
  `connector_harness.py`, `connector_registry.json`,
  `requirements.txt`. Mitigation: pre-merge git diff verification.
- **R-419** — SQL injection. Every new `cursor.execute` uses module-level
  SQL constants and `%s` placeholders. Verified by
  `test_no_string_interpolated_sql`.
- **R-420** — `print()` in new ingestion helpers. Mitigation: extend the
  forbidden-names set in
  `TestPhase6SqlConstantsStaticChecks.test_no_print_in_ingestion_helpers`
  to include the 4 new validators, 4 new loaders, and the
  county→market resolver.
- **R-421** — UPDATE/DELETE against `parcel_scores`. Phase 6.1 only
  DELETE-and-INSERTs `sales_comps`, `leasing_comps`, `land_listings`.
  No regression on the `_LATEST_SCORE_WHERE` invariant.
- **R-422** — `comp_type` discriminator. Both land and building rows
  go into `sales_comps`. The DELETE clause for re-ingest must include
  `comp_type = %s` so a building file re-ingest doesn't blow away the
  land rows for the same (submarket, address, date) tuple. Mitigation:
  separate `_SQL_DELETE_*_FOR_REINGEST` constants per `comp_type`.
- **R-423** — `parcel_id` hint linkage. `sales_comps.parcel_id` and
  `land_listings.parcel_id` are plain TEXT (no FK) per the schema.
  We populate them when CoStar provides them but don't enforce
  existence in `parcels`. This is intentional — sales/listings can
  pre-date parcel discovery. Phase 8 actionability scoring may LEFT
  JOIN to surface "we own this parcel & there's a comp on it".
  Documented.
- **R-424** — date-only `sale_date` storage. `sales_comps.sale_date` is
  DATE not TIMESTAMP, so any time component from CoStar gets dropped.
  The validator's `_parse_report_date` already truncates to date.
  Verified.
- **R-425** — large `raw JSONB` payloads. CoStar rows are <2 KB each;
  a year of monthly comps is ~2400 rows × 2 KB = 5 MB total. Trivial
  for Postgres. Accepted.
- **R-426** — listings is_active flip on re-ingest. When a snapshot is
  re-delivered, the DELETE clears prior rows for that snapshot_date
  and the INSERT re-creates them with `is_active = TRUE`. No need to
  preserve prior is_active state since snapshot is point-in-time.
  Documented.
- **R-427** — slug collisions between markets. After R-401, we slugify
  per-market; "Atlanta__south_fulton" and "DFW__south_fulton" don't
  collide. Verified.
- **R-428** — `_INGESTION_LOADERS` registry signature backward-
  compatibility. Phase 6 used `loader(conn, cycle_id, files)` returning
  a dict. Phase 6.1 keeps the same signature exactly; no consumer
  changes. Verified.
- **R-429** — re-running `run_ingestion_cycle` after Phase 6.1 against
  the existing Phase 6 submarket_stats workflow must not regress.
  Mitigation: all Phase 6 tests still pass post-merge.
- **R-430** — fixture data realism. Use plausible Atlanta-area
  submarket names ("South Fulton", "West Atlanta / I-20", "Clayton
  County") and Atlanta industrial corridors so end-to-end tests
  exercise realistic strings. Documented.

---

## 4. Go / no-go gates for Agent 3

Before merge of Phase 6.1:

1. ✅ Five-File Contract intact: `parameters.json`, `sources.json`,
   `program.md`, `prepare.py`, `connector_harness.py`,
   `connector_registry.json`, `requirements.txt` byte-identical to
   Phase 6 head.
2. ✅ `research.py` edits: 8 new `_SQL_*` constants (4 DELETE + 4
   INSERT), 4 new validators
   (`_validate_land_sales_comps_row`, `_validate_building_sales_comps_row`,
   `_validate_leasing_comps_row`, `_validate_land_listings_row`),
   4 new loaders (`_load_land_sales_comps_file`, `_load_building_sales_comps_file`,
   `_load_leasing_comps_file`, `_load_land_listings_file`), 4 new driver
   wrappers replacing the 4 placeholder driver wrappers in
   `_INGESTION_LOADERS`, the county→market lookup constants, and the
   `_resolve_market_from_county` helper. Existing Phase 6 functions
   untouched except where the placeholder loader stubs are deleted.
3. ✅ `tests/test_discovery.py` extended:
   - `TestPhase61CountyToMarket` (3+ tests)
   - `TestPhase61LandSalesCompsValidation` (5+ tests)
   - `TestPhase61LandSalesCompsLoader` (3+ tests)
   - `TestPhase61BuildingSalesCompsValidation` (4+ tests)
   - `TestPhase61BuildingSalesCompsLoader` (2+ tests)
   - `TestPhase61LeasingCompsValidation` (5+ tests)
   - `TestPhase61LeasingCompsLoader` (2+ tests)
   - `TestPhase61LandListingsValidation` (5+ tests)
   - `TestPhase61LandListingsLoader` (3+ tests)
   - `TestPhase61RunIngestionCycleAllReal` (2+ tests; replaces the
     Phase 6 placeholder-related tests)
   - `TestPhase61SqlConstantsStaticChecks` (extending Phase 6's
     `TestPhase6SqlConstantsStaticChecks` with the 8 new constants and
     the 4 new validators / loaders to the print-forbidden set, OR
     by extending the existing class — author's call)
4. ✅ All 161 pre-existing test_discovery tests still pass; 35
   test_harness tests still pass when run alone.
5. ✅ NEW test fixture files under `tests/fixtures/costar/` (≥ 10
   files, see §F).
6. ✅ No new runtime dependency added to `requirements.txt`.
7. ✅ Static AST checks pass with the extended forbidden-names set.
8. ✅ `_INGESTION_LOADERS` updated to 5 real loaders; 4 placeholder
   functions and `_load_placeholder` helper deleted.
9. ✅ `COSTAR_EXPORTS_README.md` updated to reflect that Phase 6.1
   wires all 4 remaining recurring export types; `tenant_intel`
   remains the only on-demand / Phase 8 deferred export.
10. ✅ Reviewer decision document written to
    `reviews/09_phase6_1_costar_loaders/03_reviewer_decision.md` with the
    APPROVE/REVISE verdict.

---

## 5. Out of scope for Phase 6.1

Explicitly NOT in this phase:

- Tenant intel (`tenant_intel`) export — Phase 8+.
- Multi-market expansion (Orlando / Chicago / Lehigh Valley) —
  Phase 11+.
- Cross-snapshot `is_active` management for `land_listings` — Phase 7+.
- The `harness_reports/costar_ingestion_{date}.json` JSON output
  (deferred from Phase 6).
- Confidential / undisclosed sale price acceptance — Phase 7+ ratchet.
- Wiring `_compute_s4`/`s5`/`s6` to read from `market_context` — Phase 7.
- Wiring `_compute_s8` to use `sales_comps` — Phase 7.
- Wiring strategy-fit signals from `leasing_comps` (BTS, spec-dev) —
  Phase 8.
- Modifying any of the immutable spec / schema files.
- Adding pandas / Excel parsing.

---

## 6. Sub-agent deviation note

Sixth orchestrator-inline phase. The user's directive after Phase 6
shipped was "Proceed with A, max effort", which means the work is
proceeded inline by the orchestrator due to the sub-agent timeout
issue documented at
`reviews/03_phase2_connector_harness/03_orchestrator_completion_note.md`,
`reviews/04_phase3_fulton_discovery/02_code_writer_response.md`,
`reviews/05_phase3_1_punch_list/02_reviewer_decision.md`,
`reviews/07_phase5_scoring_mvp/01_risk_review.md`, and
`reviews/08_phase6_costar_ingestion/01_risk_review.md`.

---

## 7. Final verdict

**GO-WITH-CONDITIONS.** Conditions:

1. R-401 — Agent 2 implements `_COUNTY_TO_MARKET` lookup and
   `_resolve_market_from_county` helper.
2. R-402 — Agent 2 implements per-export dedup keys with idempotent
   DELETE-then-INSERT semantics inside one transaction.
3. R-417 — Agent 2 rewrites the Phase 6 placeholder-related tests to
   reflect that all 5 loaders are now real.
4. R-422 — `comp_type` is correctly carried in both INSERT and DELETE
   for `sales_comps` so land vs building re-ingest doesn't cross-
   contaminate.

All other risks are mitigated in code, tests, or accepted with explicit
rationale. Total risks: 30 (R-401 .. R-430). 26 mitigated in code/tests;
4 accepted with rationale (R-405 Atlanta-only lookup, R-406/R-410
confidential price/rent rejection, R-412 cross-snapshot is_active,
R-415/R-414 transaction volume).

---

AGENT 1 (orchestrator inline) DONE — verdict: GO-WITH-CONDITIONS.
