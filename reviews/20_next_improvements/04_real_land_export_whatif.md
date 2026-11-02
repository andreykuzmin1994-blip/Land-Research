# 04 — A real CoStar Land export: what it can feed, and a what-if

> **Context.** The operator supplied a real CoStar Land sales export after
> items 1–3. This was a between-runs measurement session; **no code was
> changed.**
>
> **How it was run.** The export was converted by a scratch script to the
> contract's `land_sales_comps_YYYYMM.csv` and loaded into throwaway local
> databases only.
>
> **Licensing.** CoStar data is licensed, so this note gives counts and
> metric values only: no names, addresses or prices. The export and the
> converted CSVs never entered the repo.

## What the export is

- **Shape:** one sheet of 483 land sales in the Atlanta area, from
  2025-01 to 2026-10.
  - Every sale is 10 acres or more.
  - All land uses: residential, commercial and industrial.
  - Statuses are *Sold* or *Under Contract*.
- **Columns:** 41. They map almost one-to-one onto the contract's
  Export 2 (address, county, submarket, acres, sale date and price,
  $/acre, buyer, seller, zoning, proposed use).
- **What is missing:**
  - **Parcel number:** none.
  - **Location:** no coordinates.

## What it can and cannot feed today

1. **`costar_import.py` refuses it.** The importer needs a stats sheet or a
   sale-comps sheet with `Parcel Number 1 (Min)`. This export has neither.
   That is correct behaviour: the importer is for the stats workbook.
2. **`costar_ingest` (runner ingestion) loads it once converted.**
   - The conversion: rename the columns, and drop rows with no sale date
     (68, all *Under Contract*) or no price (48).
   - Converting it permanently would be a `costar_import.py` change. That
     is Tier 2 and the operator's decision (`01_*` point 2).
3. **It cannot draw submarket areas.** Without parcel numbers or
   coordinates there is nothing to locate. Areas still have to come from
   the stats workbook's sale-comps sheet.
4. **Submarket names may not match.**
   - S8 looks up comps by the **slug of `Submarket Name`**
     (`atlanta__<slug>`). Parcels get their submarket from the area drawn
     from the *stats* workbook.
   - So comps reach a parcel only if both exports spell the submarket
     identically.
   - This export uses names like `N Fulton/Forsyth County`. The earlier
     real stats workbook's Fulton submarket is recorded as `North
     Fulton/Forsyth` (`reviews/19_costar_import/02_*` finding 5). Those
     would slug differently.
   - The two corridor submarkets here are **North Clayton/Airport** (south
     corridor: Fairburn, Palmetto, College Park, South Fulton) and
     **Douglasville/Lithia Springs** (west corridor: Fulton Industrial,
     Camp Creek). They could not be checked against the stats workbook,
     because that workbook is not in this session.

## What-if: the same baseline, three land-comp filters

**Setup.**
- The `01_*` run c database: 1,075 live Fulton parcels and the TEST stats.
- Its two test areas were relabelled to the two real corridor submarket
  ids, and the synthetic land comps were removed.
- The converted real comps were then loaded through the normal runner
  ingestion, and a baseline was run.
- Code: this branch at 18c2e94.
- Only the land comps file differs between rows.

| Land comps loaded | Rows | Comps in west-corridor submarket | Metric | Confidence |
|-------------------|------|---------------------------------|--------|------------|
| (run c: 8 synthetic) | 8 | 4 | 15 | 8.75 |
| Every row with a date and price | 367 | 14 | **9** | 5.25 |
| Contract filter, loose (sold, 5–50 ac, "industrial" by secondary type, proposed use or zoning) | 83 | 4 | **17** | 9.92 |
| Contract filter, strict (sold, 5–50 ac, industrial secondary type or M/I/LI zoning) | 60 | 2 | **0** | 0.00 |

**Why the metric moves.**
- Every actionable parcel in every variant is in the **west corridor**, and
  every one has S8 = 10. The west corridor's S8 rests on the
  Douglasville/Lithia Springs median, which is decided by very few sales.
- **Loose filter (4 comps):** two of those sales are not industrial (a
  mobile-home-park zoning and a commercial zoning). They lift the count to
  the 3-comp minimum, so S8 exists at all. The median of the 4 is high
  enough that many parcels sit "below median" (S8 = 10).
- **All rows (14 comps):** residential and agricultural land pulls the
  median down to about a fifth of the loose median, so fewer parcels
  score 10.
- **Strict filter (2 comps):** below the 3-comp minimum, so S8 is null for
  all 580 west parcels. No parcel reaches 70.
- **South corridor:** North Clayton/Airport has 10–34 comps in every
  variant, and about three-quarters of south parcels score S8 = 10 in all
  of them. Its parcels' assessed basis (× 2.5) is far below recent land
  sale prices.
  - None is actionable, because the TEST stats give that submarket
    S6 = 0.

**What this shows.**
- **The filter matters as much as the CoStar stats.** The choice of which
  land sales count as comps, a human data decision, moves the baseline
  between 0 and 17 with everything else fixed.
- **The west-corridor submarket has very few industrial land sales:** 2
  strict comps in 22 months. S8 there will be either null or decided by a
  handful of sales, unless the comps are pooled. For example, the
  contract's 24-month window could be widened, or neighbouring submarkets
  or county-level comps could be used.
  - The 3-comp minimum and the 36-month lookback are `research.py`
    constants, so the loop could change them, but only with evidence.
    Pooling would also be the loop's job.
- **Nulls beat low scores** (`01_*` point 4). The strict variant's west
  parcels peak at 68.2 with S8 null. One more qualifying sale at a high
  $/acre would make S8 = 10 and push many of them over 70.

## For the operator

1. **Re-export with a location column.** CoStar's column picker includes
   `Parcel Number 1 (Min)` and/or `Latitude` / `Longitude`. With either,
   comps can be placed spatially and the name-matching risk goes away.
   Adding that to the tooling would be a Tier 2 change.
2. **Choose the comp filter**, ideally matching the contract (industrial,
   5–50 ac, sold, 24 months). Note that CoStar's export started at 10 ac.
   This decides S8 for the west corridor.
3. **Confirm the names.** Do the stats workbook's submarket names match
   `North Clayton/Airport` and `Douglasville/Lithia Springs` exactly? If
   not, S8 stays null for every parcel even after loading.
4. **Decide on the converter.** Whether to make the Land-export
   conversion a permanent part of `costar_import.py` is Tier 2. The
   scratch converter used here was not committed.
