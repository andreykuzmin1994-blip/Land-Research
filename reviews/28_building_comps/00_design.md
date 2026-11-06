# Building sale and lease comps — design

> **Tier 2** (external licensed data, the protected importer, the area
> writer the metric depends on, and the memo the runner renders on every
> experiment). The pre-change risk register (BC-01 to BC-20) and the
> disposition of every item is `02_risk_review.md`; what was verified and
> what a human must do is `03_decision_note.md`. Model names are omitted.

## Why

The operator asked whether the system can be used for building research.
The memo already projects the largest prototype building a parcel holds
(`reviews/22_site_fit/`); what it lacked is what that building is worth:
the market rent it would lease at and the value it would trade at. CoStar
holds both (Sale Comps and Lease Comps), the operator already exports
CoStar workbooks, and the database already has `sales_comps`
(`comp_type`, `building_sf`, `price_psf`, `cap_rate`) and `leasing_comps`
(`starting_rent_psf_nnn`, `building_sf_leased`, `lease_start_date`,
`lease_term_months`, `raw`).

## Decisions

1. **No new module, no schema change.** The importer is the protected
   `costar_import.py` (where the land path lives); the read and render is
   the protected `reporting.py`. Both are already in every protected-file
   list, so BC-14 needs no new list entry. Extra fields go to an
   allowlisted `raw` JSONB. `prepare.py` untouched; no prepare-mutation
   proposal is needed.
2. **Memo only.** Nothing in `research.py` reads the new rows: every
   `sales_comps` reader filters `comp_type = 'land'` and there is no
   `leasing_comps` reader (pinned by a static test). `research.py` has no
   diff. Scoring is byte-identical by construction and by the four
   existing golden checks; a live differential scores before and after
   an import.
3. **Sheet roles are exclusive** (`costar_import.sheet_role`), checked in
   this order: stats (`Geography Name` + `Concept Name`); lease (any of
   `SF Leased`, `Square Feet Leased`, `Leased SF`); area comps
   (`Submarket Name` + `Parcel Number 1 (Min)`), which is ALSO building
   comps when every building column is present ("areas+building"); land
   (the existing `is_land_sheet`, unchanged); building (a building SF
   column plus `Sale Price`). A lease sheet is therefore never an area
   sheet, whatever else it carries. Two sheets with one role are refused
   (no more `next()`): this also applies to stats and land, which today
   took the first match silently.
4. **The operator's existing area sheet keeps its role exactly.** It is a
   building sale-comps export (`reviews/19_costar_import/01_*`). Its area
   plan is computed from its parcel numbers alone, as before (a test
   compares the plan). If it also carries every building column, its
   sales are imported as building comps AFTER the areas are written, and
   the console says so; if it does not, the areas import as before and
   the console names the building columns it lacks (never a refusal: the
   area import must not break).
5. **Exact headers, documented aliases, loud refusal.** Headers are
   stripped and case-folded, never substring-matched. A sheet that looks
   like building or lease data but lacks a required column is refused
   (exit 1) naming only the expected headers. No real building export has
   been seen; every assumed header is listed below and in
   `COSTAR_EXPORTS_README.md`.
6. **Units.** Cap rate: one stored convention, a FRACTION (0.055); a value
   with `%` or above 1 is a percent. Rent: annual NNN $/SF/yr; a monthly
   value falls outside the $2-25 band. Building SF: `RBA`, then `Building
   SF`, then `Total SF`; a lease's size is `SF Leased`, never the
   building's RBA (stored separately in raw). Dates: the existing
   `_sale_date` with the workbook's 1904 flag; leases use the Sign Date for
   recency and store the Start Date as `lease_start_date`. Lease term:
   `N Years M Months` or a months column; a bare number in `Lease Term` is
   ambiguous and stored as NULL.
7. **Location.** Latitude/Longitude only (no locator, no network), the
   Georgia box, then the land path's `_SQL_ASSIGN_POINTS_TO_AREAS`
   (`ST_MakePoint(lon, lat)`, SRID 4326, nearest area within 3 km).
8. **Own rows only.** Origins `costar_import_building` and
   `costar_import_lease`. The tool's predicate is
   `raw->>'import_origin' = origin AND raw ? 'imported_on' AND NOT (raw ?
   'submarket')`: every CSV-delivered row has a `submarket` key in raw (a
   required CSV column), so a CSV that forges the two tool keys is still
   not the tool's. The DELETE, the other-origin key read and the memo
   read share this predicate. Comps another origin delivered (address and
   date for sales; address, start date and SF for leases) are skipped and
   counted.
9. **Transactions and snapshots.** One transaction per table, after the
   areas and land commits; a failure rolls back that table and the
   console says which steps committed (exit 2). The kept rows (allowlisted
   fields) are saved atomically in gitignored
   `costar_exports/.building_sales_snapshot.json` and
   `.lease_comps_snapshot.json`, replayed and refiltered whenever an area
   sheet is imported; no areas yet = deferred (exit 0); nothing
   assignable = earlier rows kept. An unreadable snapshot is reported and
   the area import continues.
10. **Memo read.** `reporting.fetch_building_comps`: two reads (leases,
    sales of the market within the windows and bands, tool rows only);
    if both are empty it returns None and the section is omitted (memo
    and snapshot byte-identical to 3898047). Otherwise one resolver read
    for the highlight / snapshot parcel ids (a copy of
    `research._SQL_RESOLVED_SUBMARKET` kept in `costar_import`, pinned
    equal) and one name read. At most four reads whatever the number of
    parcels, each in a SAVEPOINT (site_fit's `_query` pattern); any error
    is logged and renders nothing. Windows are parameters counted from the
    memo's injected date, never `CURRENT_DATE`. Only the new section
    resolves the submarket; the old land-comps and market-context lines
    keep using the stored value, byte-identical.
11. **Scope and counts.** `MIN_COMPS = 5`. A statistic is the resolved
    submarket's median when it has 5 comps, else the market-wide median
    labelled "Atlanta market-wide; k in the submarket" when the market
    has 5, else "—" with both counts. Median = `statistics.median` (the
    average of the two middle values for even n); the land thesis keeps
    its upper median (unchanged).
12. **Exit value.** Only for the projection the memo shows
    (`site_fit.best_fit`, status OK), with its `rules_label`. Income
    method (median signed NNN rent x SF / median cap rate) when both have
    their comps; else sale $/SF x SF; else no line. Never a default cap
    rate; a cap rate at or below 0 is never divided by. Rounded to $0.1M,
    with each input's count, scope and window, and "an estimate, not an
    appraisal".

## Constants (all in `costar_import.py`, with rationale in comments)

| Constant | Value | Why |
|---|---|---|
| `BUILDING_MIN_SF` / `MAX` | 100,000 / 1,000,000 | prototypes project 104,000-520,000 SF; same bulk product |
| `BUILDING_SALE_WINDOW_MONTHS` | 36 | sales are sparse; starts after the 2022-23 rate reset |
| `LEASE_WINDOW_MONTHS` | 24 | rents move several % a year; enough 100K+ leases |
| `BUILDING_MAX_AGE_YEARS` | 15 | exit comps for a new building (32-36 ft clear generation) |
| `CAP_RATE_MIN` / `MAX` | 3% / 12% | brackets plausible stabilised industrial caps |
| `SALE_PSF_MIN` / `MAX` | $30 / $400 | below = land value or distress; above = data centre / cold storage |
| `PRICE_PSF_TOLERANCE` | 5% | CoStar `Price Per SF` vs price / RBA |
| `NNN_RENT_MIN` / `MAX` | $2 / $25 per SF per year | catches monthly rents and totals |
| `MIN_COMPS` | 5 | one deal must not move the median |

## Assumed CoStar headers (no real export seen yet)

Building sales: `Property Address`/`Address`, `Property Name`/`Building
Name`, `Property Type`, `Secondary Type`, `Sale Status`, `Sale Date`,
`Sale Price`, `RBA`/`Building SF`/`Total SF`, `Price Per SF`/`Price/SF`/
`Sale Price Per SF`, `Actual Cap Rate`/`Cap Rate`, `Sale Condition`/`Sale
Conditions`, `Number Of Properties`/`Number Of Properties In Sale`, `Year
Built`, `Ceiling Height`/`Clear Height`/`Ceiling Ht`, `Latitude`,
`Longitude`. Required: address, property type, secondary type, sale
status, sale date, sale price, building SF, sale condition, year built,
latitude, longitude.

Leases: `Property Address`/`Address`, `Property Type`, `Secondary Type`,
`SF Leased`/`Square Feet Leased`/`Leased SF`, `Sign Date`/`Lease Sign
Date`, `Start Date`/`Lease Start Date`/`Commencement Date`/`Move In
Date`/`Move-In Date`, `Starting Rent/SF/Yr`/`Starting Rent/SF`/`Starting
Rent`, `Rent/SF/Yr` (with `Rent Type`), `Asking Rent/SF/Yr`/`Asking
Rent/SF`/`Asking Rent` (drop reason only), `Rent Type`, `Service
Type`/`Rent Service Type`, `Lease Type`, `Deal Type`/`Transaction Type`,
`Lease Term`/`Term`, `Term (Months)`/`Lease Term (Months)`/`Term Months`,
`RBA`/`Building SF`, `Year Built`, `Latitude`, `Longitude`. Required:
address, property type, secondary type, SF leased, sign date, service
type, latitude, longitude, and a starting rent (or `Rent/SF/Yr` plus
`Rent Type`).

Service type values kept: `NNN`, `Triple Net`, `Net Net Net` (case and
spacing aside). Rent type values: `Starting` kept; `Asking` dropped as
asking-only; any other (Effective, ...) dropped as not a starting rent.
Secondary types kept: `Warehouse`, `Distribution`.

## Not done

- No research.py use (BTS or spec strategy fit could use rent later: a
  Tier 2 scoring change with a new run and baseline).
- No cap-rate default, no NOI from the export (the contract's
  `noi_at_sale` is CSV-only), no rent escalation.
- No real export tested: the first real workbook will be diagnosed by the
  refusal message (BC-12).
