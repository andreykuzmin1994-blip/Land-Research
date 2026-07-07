# CoStar Ingestion Contract

> How CoStar exports feed the Land Site Selector agent.
> CoStar does not expose a public REST API to standard subscribers. This document defines the manual export workflow that delivers CoStar data to the agent.
> Operational companion: [`COSTAR_EXPORTS_README.md`](COSTAR_EXPORTS_README.md) — the on-disk folder layout, loader behavior, and one-time email-to-folder setup that implement this contract. This file is the frozen spec (`costar_ingest.py` is held to it); day-to-day ops details live in the companion.

---

## Why Manual Export, Not Scraping

CoStar's terms of service explicitly prohibit automated scraping of their web interface. They actively monitor for it and have taken enforcement action against subscribers who do it, including account termination and litigation. **The agent does not scrape CoStar under any circumstances.**

CoStar's own scheduled report functionality (saved searches that email exports on a schedule) is permitted use. The ingestion contract below uses only this permitted pathway.

If the firm chooses to negotiate a CoStar enterprise data feed agreement in the future, this contract will be updated to support direct API ingestion. Until then, the agent operates against scheduled exports.

---

## Required Exports

The agent depends on five recurring CoStar exports plus one on-demand export. Each export is a saved CoStar search configured to email a CSV/Excel file to a designated address on a defined cadence. The agent's research.py reads the exports from the configured ingestion folder.

### Export 1: Submarket Statistics (Weekly)

**Cadence**: Every Monday morning
**Format**: CSV
**Filename pattern**: `submarket_stats_{YYYYMMDD}.csv`
**Filter**: All industrial submarkets in target markets (initially Atlanta)

**Required fields per row** (one row per submarket):
- `submarket_name` — CoStar's submarket designation
- `market` — Atlanta, DFW, Houston, etc.
- `total_inventory_sf` — Total industrial inventory in submarket
- `vacancy_rate_pct` — Current direct vacancy rate
- `availability_rate_pct` — Includes sublease availability
- `net_absorption_t12_sf` — Trailing 12-month net absorption
- `under_construction_sf` — Currently under construction
- `proposed_sf` — Planned but not started
- `asking_rent_nnn_psf` — Average asking rent (NNN, PSF, annual)
- `report_date` — When CoStar generated the data

**Powers**: S4 (vacancy), S5 (absorption), S6 (pipeline), spec development feasibility

### Export 2: Industrial Land Sales Comps (Monthly)

**Cadence**: First Monday of each month
**Format**: CSV
**Filename pattern**: `land_sales_comps_{YYYYMM}.csv`
**Filter**: Industrial-zoned land sales, target counties, last 24 months, 5–50 acre range

**Required fields per row** (one row per transaction):
- `address` — Property address
- `parcel_id` — County parcel ID where available
- `county` — County name
- `submarket` — CoStar submarket
- `acres` — Land size
- `sale_date` — Transaction date
- `sale_price` — Total transaction price
- `price_per_acre` — Calculated $/acre
- `buyer_name` — Buyer of record
- `seller_name` — Seller of record
- `zoning` — Zoning at time of sale
- `intended_use` — If disclosed
- `cap_rate` — N/A for land but populated if it's an improved sale that comes through the filter

**Powers**: S8 (land basis), land flip strategy fit, market context

### Export 3: Industrial Building Sales Comps (Monthly)

**Cadence**: First Monday of each month
**Format**: CSV
**Filename pattern**: `building_sales_comps_{YYYYMM}.csv`
**Filter**: Industrial building sales, target submarkets, last 12 months, 50,000+ SF

**Required fields per row**:
- `address`, `submarket`, `building_sf`, `year_built`, `clear_height_ft`
- `sale_date`, `sale_price`, `price_psf`
- `cap_rate`, `noi_at_sale`
- `buyer_name`, `seller_name`
- `tenant_at_sale`, `lease_term_remaining_years`

**Powers**: Implied land value calculations, spec development feasibility validation

### Export 4: Industrial Leasing Comps (Monthly)

**Cadence**: First Monday of each month
**Format**: CSV
**Filename pattern**: `leasing_comps_{YYYYMM}.csv`
**Filter**: Industrial leases, target submarkets, last 12 months, 25,000+ SF

**Required fields per row**:
- `address`, `submarket`
- `tenant_name`, `tenant_industry`
- `lease_start_date`, `lease_term_months`
- `building_sf_leased`
- `starting_rent_psf_nnn` — Annual NNN starting rent
- `rent_escalation_pct` — Annual escalation if disclosed
- `lease_type` — NNN, MG, FSG

**Powers**: Spec development feasibility (achievable rent assumptions), BTS strategy fit (tenant patterns)

### Export 5: Land Listings (Weekly)

**Cadence**: Every Monday morning
**Format**: CSV
**Filename pattern**: `land_listings_{YYYYMMDD}.csv`
**Filter**: Currently listed industrial land, target counties, 5–50 acres

**Required fields per row**:
- `address`, `parcel_id`, `county`, `submarket`
- `acres`, `zoning`, `topography_notes`
- `asking_price`, `asking_price_per_acre`
- `listing_date`, `days_on_market`
- `listing_broker`, `listing_broker_firm`
- `utilities_status` — If disclosed
- `entitlement_status` — If disclosed

**Powers**: On-market discovery (complement to off-market mismatched-use engine), comparison against asking prices

### Export 6: Tenant Intelligence (On-Demand)

**Cadence**: Triggered when agent flags a parcel for BTS strategy fit
**Format**: CSV
**Filename pattern**: `tenant_intel_{submarket}_{YYYYMMDD}.csv`
**Filter**: Industrial tenants in specified submarket with leases expiring within 24 months

**Required fields per row**:
- `tenant_name`, `tenant_parent_company`
- `tenant_industry`, `naics_code`
- `current_address`, `current_building_sf`
- `lease_expiration_date`, `lease_remaining_months`
- `expansion_announcements_t12` — If CoStar tracks this

**Powers**: BTS demand signal research, off-market BTS pitch targeting

---

## Ingestion Folder Structure

The agent expects exports in a specific folder structure. The user (or CoStar's email-to-folder automation) drops files here:

```
costar_exports/
├── submarket_stats/
│   ├── submarket_stats_20260427.csv
│   ├── submarket_stats_20260420.csv
│   └── ...
├── land_sales_comps/
│   ├── land_sales_comps_202604.csv
│   └── ...
├── building_sales_comps/
│   └── ...
├── leasing_comps/
│   └── ...
├── land_listings/
│   ├── land_listings_20260427.csv
│   └── ...
├── tenant_intel/
│   └── tenant_intel_south_fulton_20260427.csv
└── ARCHIVED/
    └── (older exports moved here automatically by the agent after ingestion)
```

The agent's research.py:
1. Scans each subfolder for new files (filename newer than last ingestion timestamp)
2. Validates each file against the required schema for that export type
3. Loads validated rows into the corresponding Postgres table
4. Moves processed files to `ARCHIVED/` with timestamp
5. Logs ingestion results to `harness_reports/costar_ingestion_{date}.json`

---

## Schema Validation and Failure Handling

Each export type has an explicit schema validation step. If an export file fails validation, the agent does NOT attempt to use the partial data — it logs the failure, flags the export for human review, and falls back to the previous valid export for that data type.

Validation rules:
- All required fields must be present (column headers match expected names)
- Data types must match (numeric fields parseable as numbers, dates parseable as dates)
- For submarket stats: vacancy rate must be 0–100, asking rent must be > 0
- For sales comps: sale price > 0, sale date within filter window
- For listings: asking price > 0 if populated (null acceptable)

If validation fails:
- Move the file to `costar_exports/FAILED/` with a `.error.json` companion file describing the failure
- Email or log alert to the human operator
- Continue using the most recent successfully validated export for that data type
- Flag the strategy memo for that cycle with a "stale CoStar data" note

---

## Setting Up the Saved Searches in CoStar

For each export above, the human operator must configure a CoStar saved search with email scheduling. This is one-time setup:

1. Log into CoStar
2. Run the search matching the filter criteria above
3. Save the search with a clear name (e.g., "Agent_Submarket_Stats_Atlanta")
4. Configure email delivery: weekly Monday 6 AM, CSV format, addressed to the agent's ingestion email
5. Set up an email rule (Gmail filter, Outlook rule) that auto-saves attachments from CoStar emails to the appropriate `costar_exports/{export_type}/` folder

For agents running in cloud environments, the email-to-folder pipeline can be:
- **Gmail + Google Drive**: Gmail filter triggers Apps Script that saves attachment to a Drive folder, agent reads from Drive
- **Outlook + OneDrive**: Power Automate flow that saves attachment to OneDrive folder
- **Mailgun/SendGrid + S3**: Inbound email parsing that saves to S3, agent reads from S3

The specific pipeline is an implementation detail. The contract is: CSV files matching the filename patterns above appear in the configured folders within 24 hours of CoStar generating them.

---

## What Happens If an Export Is Late or Missing

If a scheduled export is missing for more than 7 days past expected delivery:
- The agent continues to operate using the most recent valid data
- The strategy memo for affected cycles includes a warning: "CoStar submarket stats data is N days stale"
- The agent's confidence_score on parameters that depend on the missing data is reduced proportionally
- After 14 days of staleness, the agent flags an alert recommending the human verify the saved search is still active

---

## Future: Enterprise Data Feed

If the firm pursues a CoStar enterprise data feed agreement, this contract will be updated to support:
- SFTP delivery of structured files (typically daily batches)
- Direct API access to specific data sets (rare, expensive)
- Real-time webhooks for transaction events (very rare, very expensive)

The agent's ingestion logic is designed so that swapping the data source (manual export → SFTP → API) requires only changing the file pickup mechanism — the schema and downstream processing remain identical.
