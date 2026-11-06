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

## CoStar Excel exports (`costar_import.py`)

CoStar's own Excel export does not match the CSV contract above, so
`costar_import.py` converts it. Run it **between runs only**: after
`make setup` cuts the new run's branch and before `make baseline`. Once the
current run has its baseline the tool refuses (exit 3), because new stats
and areas would change what every later experiment scores. The
home-machine autopilot imports any new `.xlsx` in your CoStar folder at
that point when you use **Start New Run**.

```bash
make costar-import FILE=path/to/ATL_export.xlsx
```

Sheets are recognized by their headers:

- **Submarket stats** (`Geography Name`, `Concept Name`, quarter columns)
  become `costar_exports/submarket_stats/submarket_stats_<quarter end>.csv`.
  Only the last FULL quarter is used (`2026 Q3`), never `QTD` or `EST`
  (forecast); `report_date` is that quarter's end, so re-importing the same
  workbook is idempotent. Rows other than `Industrial`, the market total
  and `(Cluster)` rows are dropped; `Atlanta - GA USA - ` is stripped from
  names. Rates given as fractions are converted to percents.
  Approximations: `availability_rate_pct` = `Vacant Available %`;
  `asking_rent_nnn_psf` = `All Service Type Rent Overall` (CoStar's
  all-service-type rent, not strictly NNN; the memo still labels it NNN);
  `proposed_sf` is blank (not in this export; S6 uses under-construction).
- **Sale comps** (`Submarket Name`, `Parcel Number 1 (Min)`) locate each
  submarket. Fulton-format parcel numbers (`14F-0134-LL-019-5`) are looked
  up in the Fulton parcel layer registered in `sources.json` (1 request per
  second, cached in `costar_exports/.comp_parcel_locations.json`). A
  submarket with at least 3 located comps (isolated ones, with no other comp
  of the submarket within 8 km, are dropped) gets `submarkets.bbox` = a
  300 m buffer around their convex hull, all in one transaction. Areas this
  tool did not write (a human-seeded bbox) are never overwritten; areas it
  wrote earlier for submarkets that no longer qualify are cleared.
  Other counties' parcel numbers can look like Fulton's; they are simply
  not found (the tool reports the located share and how many submarkets
  get no area).
  `research.py` then gives each parcel the submarket whose area is nearest,
  within 3 km, at read time; parcels further from every area keep their
  corridor name and score S4/S5/S6 as missing data.

- **Land sales** (a CoStar *Land* export) become S8 land comps. A sheet
  counts as Land when it has `Land Area AC`, `Sale Price`, `Sale Date`,
  `Zoning`, `Latitude` and `Longitude`, and no `Parcel Number 1 (Min)`,
  `RBA`, `Building SF` or `Year Built` column. `Sale Status` is required.
  Add Latitude/Longitude in CoStar's column picker before exporting.
  - **Kept:** sold land sales of 10–50 acres from the last 22 months
    (counted back from the import date) whose **zoning** is industrial
    (M, M-1, LI, HI, I-2, MIA, …). CoStar's "Secondary Type" is ignored,
    because entitlement drives price.
  - **Dropped:** portfolio rows ("Part of a N Property …"), duplicates, and
    rows whose $/acre disagrees with price ÷ acres. The console reports
    counts per reason.
  - **Assignment:** each sale goes to the submarket area a parcel at the
    same point would get (nearest within 3 km). CoStar's land submarket
    names are general geographies that do not match the industrial stats,
    so they are not used.
  - **Storage:** the comps are written straight to `sales_comps` in one
    transaction that replaces this tool's earlier land rows. Hand-delivered
    `land_sales_comps_*.csv` rows are untouched.
  - **Snapshot:** the kept sales are saved in
    `costar_exports/.land_sales_snapshot.json`. If no areas exist yet, they
    wait there (exit 0), and any later workbook that redraws the areas
    assigns them again.
  - **Coverage:** check the "farther than 3 km from every area" count. S8
    needs at least 3 comps in a parcel's submarket.
  - Details: `reviews/20_next_improvements/05_*`.

- **Building sale comps and lease comps** (memo only, no scoring input;
  `reviews/28_building_comps/`) feed the memo's market rent and the
  exit-value estimate for the projected building. Details in the next
  section.

The building sale comps do NOT feed S8 (land basis); only the Land sheet
above or a `land_sales_comps` CSV does. Other counties' parcel numbers are not located yet
(Fulton is the only configured county).

Without a database the stats CSV is still written and the tool exits 2
("areas and comps not written: no DATABASE_URL ...").

### Building sale comps and lease comps (memo rent and exit value)

No real building export has been seen yet, so the column names below are
the documented assumptions about CoStar's standard Sale Comps and Lease
Comps exports. Headers are matched **exactly** (case and surrounding
spaces aside) against these lists; nothing is matched by substring, so
`Price Per SF Land` is never read as the building $/SF. A sheet that looks
like building sales or lease comps but matches no layout is **named on the
console with the expected columns it lacks** ("building: NOT imported: ..."
or "lease: NOT imported: ..."): add them in the column picker, or rename
the header to one of the listed names, and import again. "Looks like"
means: building sales = `RBA`/`Building SF`/`Total SF` with `Sale Price`,
or `Sale Price` with `Sale Date` or `Sale Status` (so a size column under
an unknown name, such as `Bldg SF`, is still caught); lease comps =
`SF Leased`/`Square Feet Leased`/`Leased SF`, or any of `Sign Date`,
`Service Type`, `Rent Type` or a rent column (so `Leased Square Feet` is
still caught). If such a sheet is the only one in the workbook, the import
is **refused (exit 1)** with that list; if the workbook also holds a stats,
area, land, building or lease sheet that matches, those are imported as
usual (exit 0) and only the look-alike is skipped. A `Leased SF` column on
a building sales sheet does not make it a lease sheet: a lease sheet also
needs a lease-only header (`Sign Date`, `Service Type`, `Rent Type` or a
rent column). If CoStar names a column differently, tell the developer
the exact header; the alias lists are in `costar_import.py`
(`BUILDING_COLUMNS`, `LEASE_COLUMNS`).

Text dates: an Excel date, `2026-03-02`, `3/2/2026`, a trailing time
(`3/2/2026 12:00:00 AM`) and a month name (`Mar 2, 2026`, `March 2, 2026`)
are read on the building and lease sheets (the Land sheet keeps its
narrower parser). `Secondary Type` may be `Warehouse`, `Distribution` or a
combination of only those two (`Warehouse/Distribution`); `Rent Type` may
read `Starting` or `Starting Rent` (`Asking`/`Asking Rent` and
`Effective`/`Effective Rent` are dropped, counted).

**CoStar search 1: Sale Comps** (Sales > Sale Comps).
- Filters: Property Type **Industrial**; Market **Atlanta**; Sale Date in
  the last **36 months**; RBA **100,000 to 1,000,000 SF**; Sale Status
  **Sold** (the tool re-checks every filter).
- Columns (required): `Property Address`, `Property Type`,
  `Secondary Type`, `Sale Status`, `Sale Date`, `Sale Price`, `RBA`
  (else `Building SF`, else `Total SF`), `Sale Condition`, `Year Built`,
  `Latitude`, `Longitude`.
- Columns (used when present): `Price Per SF` (cross-checked against
  price / RBA, 5% tolerance), `Actual Cap Rate` (or `Cap Rate`),
  `Number Of Properties`, `Property Name`, `Ceiling Height` (or `Clear
  Height`).
- Kept: sold; warehouse or distribution secondary type (flex, R&D,
  manufacturing, truck terminal, cold storage and data-centre types are
  dropped); 100,000-1,000,000 SF; built in the last 15 years; $30-$400/SF;
  inside Georgia. Dropped with a counted reason: sale conditions portfolio
  or bulk, partial interest, condo, sale-leaseback, distressed (REO,
  bankruptcy, short sale, foreclosure, receivership), auction,
  redevelopment or land value, non-arm's length, leased fee or ground
  lease, business value included; "Part of a N Property" names and
  `Number Of Properties` above 1; duplicates (address, date, size).
- Cap rates: `5.5`, `5.50%` and `0.055` all mean 5.5% and are stored as
  the fraction 0.055 (`sales_comps.cap_rate` of this tool's rows is a
  fraction). A blank cap rate (`-` and `n/a` count as blank), an
  unreadable one (`5.5% (Est)`) or one outside 3-12% keeps the sale (for
  $/SF) without a cap rate; the console counts each reason.

**CoStar search 2: Lease Comps** (Lease > Lease Comps).
- Filters: Property Type **Industrial**; Market **Atlanta**; Sign Date in
  the last **24 months**; SF Leased **100,000 to 1,000,000**.
- Columns (required): `Property Address`, `Property Type`,
  `Secondary Type`, `SF Leased`, `Sign Date`, `Service Type`, `Latitude`,
  `Longitude`, and a starting rent: `Starting Rent/SF/Yr` (or
  `Starting Rent/SF`, `Starting Rent`), or `Rent/SF/Yr` together with
  `Rent Type`.
- Columns (used when present): `Start Date` (stored as the lease start;
  recency always uses the Sign Date), `Rent Type`, `Asking Rent/SF/Yr`
  (only to name a drop "asking rent only"), `Lease Type` and `Deal Type`
  (subleases and renewals/extensions), `Lease Term` (`5 Years 3 Months` ->
  63 months; a bare number is ambiguous and stored as unknown) or
  `Term (Months)`, `RBA`, `Year Built` (strongly recommended: see below).
- Kept: signed in the last 24 months; 100,000-1,000,000 SF leased;
  when `Year Built` is given, built in the last 15 years (the same rule as
  the sales, so rent and cap rate describe the same new-building stock; a
  lease without a year built is kept); warehouse or distribution; service type NNN / Triple Net; a disclosed
  starting rent of $2-$25/SF/yr (a monthly rent such as $0.55 falls out);
  inside Georgia. Dropped with a counted reason: gross, modified gross and
  full-service leases, asking-only, effective-only and withheld rents,
  subleases, renewals and extensions, duplicates (address, sign date, SF
  leased; two suites of one building on one day are two leases).

**What is stored.** Located from the export's Latitude/Longitude only (no
county lookup, no network call), assigned to the submarket area a parcel
at the same point resolves to (nearest within 3 km), and written straight
to `sales_comps` (`comp_type = 'building'`) and `leasing_comps`, one
transaction per table, replacing only this tool's earlier rows
(`raw->>'import_origin'` = `costar_import_building` / `costar_import_lease`).
Contacts, brokers, phone numbers, e-mails, tenants, buyers, sellers and
comments are never stored; `raw` holds only coordinates, year built,
clear height, secondary type, sign date, service type and deal type. The
kept comps are also saved in `costar_exports/.building_sales_snapshot.json`
and `.lease_comps_snapshot.json`: with no areas yet they wait there (exit
0), and every workbook that redraws the areas assigns them again. A comp a
`building_sales_comps`/`leasing_comps` CSV also delivered is counted on
the console ("N also in a CSV-delivered row") and stored by this tool as
well, because the memo reads only this tool's filtered rows (nothing else
reads building or lease comps, so it is never counted twice). Prefer one
path for each comp type all the same: a later CSV re-ingest deletes a tool
row with the same submarket, address and date until the next import of
the CoStar workbook restores it.

**One empty sheet does not block the others.** A Land sheet in which no
sale qualifies leaves the land comps unchanged; when the workbook also
holds a building or lease sheet, those are still imported (the console
says so). Without one, the workbook is refused exactly as before.

**Sheet roles.** Every sheet gets exactly one role, and two sheets with
the same role are refused. A lease sheet is never an area sheet. A sheet
with `Submarket Name` and `Parcel Number 1 (Min)` keeps its area role
exactly as before; when it also carries every building column above, its
sales are ALSO imported as building comps, after its areas are written
(the console says so). An area sheet without those columns imports areas
only and names the building columns it lacks.

**Where it shows.** The snapshot's Market Context gains "Signed NNN
starting rent from lease comps" (distinct from the stats sheet's asking
rent), "Building sale comps" ($/SF and cap rate) and "Exit value
estimate"; the memo gains a "Building Comps" section with one line per
highlight. Each value is the median of the parcel's resolved submarket
when it has at least 5 comps, else the labelled market-wide median, else
"—", always with its count and date window, counted from the memo date.
The exit value uses the building the memo's projection shows (with its
draft/verified label): signed rent x SF / cap rate when both have 5
comps, else sale $/SF x SF, else nothing; never a default cap rate. The
income method assumes a stabilised building, 100% leased at the median
signed NNN starting rent, with no vacancy, credit loss or reserves, and
divides by CoStar's cap rate at sale (on in-place income), so it reads
high when rents have risen since those sales. Output shows counts only: CoStar data is licensed
and stays under the gitignored `costar_exports/`.

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
