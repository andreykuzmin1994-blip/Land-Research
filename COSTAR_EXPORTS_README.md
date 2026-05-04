# CoStar Exports — Folder Layout and Setup

> **Operational README for the CoStar ingestion pipeline (Phase 6).**
> The contract that this implements lives in
> [`COSTAR_INGESTION_CONTRACT.md`](COSTAR_INGESTION_CONTRACT.md). This file
> documents the directory layout the agent expects on disk and the one-time
> human setup needed to feed it. It is committed because the
> `costar_exports/` directory itself is gitignored (`.gitignore` line 53)
> and would otherwise be undiscoverable from the repo.

---

## Directory layout

```
costar_exports/                 (gitignored — created on demand)
├── submarket_stats/            (Export 1, weekly, WIRED in Phase 6)
├── land_sales_comps/           (Export 2, monthly, WIRED in Phase 6.1)
├── building_sales_comps/       (Export 3, monthly, WIRED in Phase 6.1)
├── leasing_comps/              (Export 4, monthly, WIRED in Phase 6.1)
├── land_listings/              (Export 5, weekly, WIRED in Phase 6.1)
├── tenant_intel/               (Export 6, on-demand, deferred to Phase 8+)
├── ARCHIVED/
│   └── {export_type}/          (files moved here after successful load)
└── FAILED/
    └── {export_type}/          (files moved here on validation failure
                                 with a sibling .error.json)
```

Filename patterns (case-insensitive, enforced by the agent):

| Export type            | Pattern                              | Cadence     |
|------------------------|--------------------------------------|-------------|
| `submarket_stats`      | `submarket_stats_{YYYYMMDD}.csv`     | Weekly      |
| `land_sales_comps`     | `land_sales_comps_{YYYYMM}.csv`      | Monthly     |
| `building_sales_comps` | `building_sales_comps_{YYYYMM}.csv`  | Monthly     |
| `leasing_comps`        | `leasing_comps_{YYYYMM}.csv`         | Monthly     |
| `land_listings`        | `land_listings_{YYYYMMDD}.csv`       | Weekly      |
| `tenant_intel`         | `tenant_intel_{submarket}_{YYYYMMDD}.csv` | On-demand |

Files that don't match the pattern for their subdirectory are silently
skipped — they don't fail the cycle, they just don't ingest. Hidden files
(`.something`) are also skipped.

---

## Phase 6.1 scope

Phase 6 Option A wired `submarket_stats` end-to-end. Phase 6.1 wires the
four other recurring export types: `land_sales_comps`,
`building_sales_comps`, `leasing_comps`, and `land_listings`. All five
recurring exports are now real loaders — files dropped into the matching
intake directory get validated, loaded into Postgres, and archived (or
quarantined to `FAILED/` on validation failure) within one
`run_ingestion_cycle()` call.

Each comp/listing loader follows the same shape as the submarket_stats
loader: header validation, per-row validation with locale-tolerant
number parsing and multi-format date parsing, idempotent
DELETE-then-INSERT inside one transaction, markets/submarkets
auto-UPSERT, archive/fail movement, research_log + flagged_items
emission. Per-export differences are limited to required-column sets,
range checks (e.g. building clear height in [8, 80] ft), dedup keys,
and target tables.

Idempotent dedup keys per export type:

| Table              | Dedup key                                                       |
|--------------------|-----------------------------------------------------------------|
| `market_context`   | `(submarket_id, as_of_date, source='costar')`                   |
| `sales_comps` land | `(submarket_id, address, sale_date, comp_type='land')`          |
| `sales_comps` bldg | `(submarket_id, address, sale_date, comp_type='building')`      |
| `leasing_comps`    | `(submarket_id, address, tenant_name, lease_start_date)`        |
| `land_listings`    | `(snapshot_date, address)` — snapshot semantics                 |

Land listings use snapshot semantics: `snapshot_date` comes from the
filename (`land_listings_{YYYYMMDD}.csv`); re-delivering the same
weekly file replaces all rows for that snapshot_date; new weeks
accumulate as new snapshot_date-keyed rows. Cross-snapshot
`is_active=FALSE` flipping for listings that disappear is a Phase 7+
join — not part of Phase 6.1.

For comp/listing types that don't ship a `market` column (per CoStar
contract), the loader resolves market from `county` via
`_COUNTY_TO_MARKET` (8 Atlanta counties seeded for Phase 6.1) or
defaults to `Atlanta` when the column is absent (`building_sales_comps`,
`leasing_comps`). A `data_gap` flag fires once per file when an unknown
county defaults.

The on-demand `tenant_intel` export is not yet registered (Phase 8+).

---

## What the agent does

`research.run_ingestion_cycle()` performs one full sweep:

1. Acquires one Postgres connection per cycle.
2. Generates an `ingest-{ISO8601-Z}-{4hex}` cycle id; aborts on collision.
3. For each registered export type:
   - Lists matching files in `costar_exports/{export_type}/`, oldest first.
   - For each file, the wired loader (Phase 6 Option A: `submarket_stats`):
     - Validates the column header set.
     - Validates each row (numeric ranges, parseable date, required
       fields). Row-level failures are recorded as `flagged_items`
       data_gap rows but do NOT fail the whole file.
     - Auto-UPSERTs `markets` and `submarkets` reference rows for any
       (market, submarket) seen for the first time. The auto-created
       submarket has a NULL `bbox`; the agent emits a `flagged_items`
       data_gap row prompting a human backfill.
     - Inside one transaction: DELETE prior `market_context` rows for the
       (submarket_id, as_of_date, source='costar') tuples being re-loaded
       (idempotent re-ingest), then INSERT the new rows, then INSERT one
       `research_log` row of `action_type='ingestion'`.
     - Commits the transaction, then moves the file to
       `costar_exports/ARCHIVED/{export_type}/`.
   - On file-level validation failure (missing column, duplicate column,
     unreadable file, DB transaction failure): the file is moved to
     `costar_exports/FAILED/{export_type}/` with a sibling `.error.json`
     describing the failure.

4. Returns a summary dict suitable for logging or for inclusion in the
   Phase 9 strategy memo.

---

## Human one-time setup

Per `COSTAR_INGESTION_CONTRACT.md` §"Setting Up the Saved Searches in
CoStar", configure each export's saved search and email-to-folder
pipeline. **Until this is set up, the agent has nothing to ingest in
production** — it works against fixture CSVs (see `tests/fixtures/costar/`)
but the live pipeline is dormant.

Concrete steps:

1. **In CoStar**: configure 5 (or 6, including `tenant_intel`) saved
   searches with the filters specified in
   `COSTAR_INGESTION_CONTRACT.md` §Required Exports. Set each to email a
   CSV on its scheduled cadence to a dedicated address.
2. **In your email + storage layer**: route the attachments to
   `costar_exports/{export_type}/` on the host running the agent. Common
   patterns:
   - Gmail filter + Apps Script → Google Drive folder mounted at the
     agent host.
   - Outlook + Power Automate → OneDrive folder synced at the agent host.
   - Mailgun/SendGrid → S3 bucket the agent reads from.
3. **Verify**: drop one valid CSV (a copy of the previous week's CoStar
   email is fine for testing) into `costar_exports/submarket_stats/`,
   then run `python -c "import research; print(research.run_ingestion_cycle())"`
   on the host. The summary should show `rows_loaded > 0` and the file
   should now live in `costar_exports/ARCHIVED/submarket_stats/`.

---

## Re-delivery and idempotency

If CoStar re-issues a file (or the human edits a row and drops the file
back into the intake folder under the same name), the agent re-ingests
it. Inside one transaction, it deletes any prior `market_context` rows
for the same `(submarket_id, as_of_date, source='costar')` tuples and
then inserts the new ones. This means: **re-delivering the same file is
safe and idempotent.**

Filename uniqueness is enforced via the archive directory's per-file
random suffix, so re-delivering a file under the same name doesn't
overwrite the prior archived copy.

---

## Failure handling

| Scenario | Outcome |
|---|---|
| Missing required column | File moved to `FAILED/`, `.error.json` written, no DB writes. |
| Duplicate header | File moved to `FAILED/`, `.error.json` written, no DB writes. |
| Empty file (header only or blank) | File archived, `rows_loaded=0`. |
| Unreadable file (permissions, etc.) | File moved to `FAILED/`, `.error.json` written. |
| DB transaction fails mid-load | File moved to `FAILED/`, `.error.json` written, partial transaction rolled back. |
| Single row out of range / unparseable | Row skipped; `flagged_items` data_gap row inserted; rest of file ingests. |
| Submarket name drift (existing id, different name) | `flagged_items` conflict row inserted; ingest proceeds. |
| File matches no registered loader | Silently skipped (file remains in place). |

The agent never deletes files. Failed files stay in `FAILED/` for human
review; archived files stay in `ARCHIVED/` for audit.

---

## What lives in Postgres after a successful ingestion cycle

For each successfully loaded file:

- One `research_log` row with `action_type='ingestion'` and a notes
  string of the form `"<export_type>: file=<name> rows_loaded=<N>
  rows_failed=<M>"`.
- Zero or more `flagged_items` rows for row-level failures, submarket
  name drifts, auto-created submarkets pending bbox backfill, and
  unknown-county-defaulted-to-Atlanta cases.

Per export type the row destinations are:

- **submarket_stats** → `market_context` (one row per
  (submarket_id, as_of_date, source='costar'))
- **land_sales_comps** → `sales_comps` with `comp_type='land'`
- **building_sales_comps** → `sales_comps` with `comp_type='building'`
- **leasing_comps** → `leasing_comps`
- **land_listings** → `land_listings` (with snapshot_date set from
  the filename; `is_active=TRUE` until Phase 7+ cross-snapshot diff)

Plus, on first encounter, a `markets` row and a `submarkets` row per
unique (market, submarket_name). Both auto-created with `bbox=NULL`
until human backfill.

The full original CSV row is preserved in the `raw JSONB` column on
`sales_comps`, `leasing_comps`, and `land_listings` so unmapped CoStar
fields (e.g. `tenant_at_sale`, `topography_notes`,
`lease_term_remaining_years`, `intended_use`) are available to Phase 9
snapshot generation without schema changes.

Phase 7 will read `market_context` for parameters S4 (submarket
vacancy), S5 (absorption), and S6 (competing pipeline), and read
`sales_comps` for refined S8 (land basis). Phase 8+ will read
`leasing_comps` for BTS / spec-development strategy fit and
`land_listings` for on-market discovery.
