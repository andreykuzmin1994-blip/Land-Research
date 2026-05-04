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
├── submarket_stats/            (Export 1, weekly, WIRED in Phase 6 Option A)
├── land_sales_comps/           (Export 2, monthly, deferred)
├── building_sales_comps/       (Export 3, monthly, deferred)
├── leasing_comps/              (Export 4, monthly, deferred)
├── land_listings/              (Export 5, weekly, deferred)
├── tenant_intel/               (Export 6, on-demand, deferred)
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

## Phase 6 scope (Option A)

Phase 6 wires `submarket_stats` end-to-end. The four other recurring
export types (`land_sales_comps`, `building_sales_comps`, `leasing_comps`,
`land_listings`) are accepted by the pipeline but report
`status='not_implemented'` in the cycle summary. **The agent does not
move, archive, or modify files for the deferred export types** — they
remain in their intake directory until the matching loader is wired in
a future phase. This is intentional so the human can stage files in
advance.

The on-demand `tenant_intel` export is not yet registered at all.

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

## What lives in Postgres after a successful submarket_stats ingest

- One row per (submarket, report_date, source='costar') in
  `market_context`, with vacancy / availability / absorption /
  under_construction / proposed / asking_rent populated.
- A `markets` row per unique market name and a `submarkets` row per
  unique (market, submarket_name) pair. Both auto-created on first
  encounter; submarket `bbox` is NULL until human backfill.
- One `research_log` row per ingested file with
  `action_type='ingestion'`.
- Zero or more `flagged_items` rows for row-level failures, name drifts,
  and auto-created submarkets pending bbox backfill.

Phase 7 will read `market_context` for parameters S4 (submarket
vacancy), S5 (absorption), and S6 (competing pipeline). Until Phase 7
lands, the data sits in the table waiting.
