# 05 — Land comps import: pre-change risk review

> **Tier 2:** a between-runs `costar_import.py` change. This is the
> independent **pre-change** review: a fresh context that did not write the
> design. Model names are omitted, as in `reviews/18_test_readiness/`.

## The operator's decision

S8 should take real CoStar **Land** sales.
- **Assignment:** comps go to submarkets **by area** (spatially), not by
  CoStar's land submarket names. Those are general geographies that do not
  match the industrial stats (`04_*`).
- **Filter:** only **industrially entitled** land counts, because
  entitlement drives price. So zoning decides, not CoStar's "Secondary
  Type" (72 sales were typed Industrial, many of them on A1, R1 or C2
  land).

## Latent defect, found while designing

`costar_import.run` takes the first sheet with a `Submarket Name` header
as the area-comps sheet. The Land export has that header but no parcel
numbers, so:

1. `comp_parcels` returns `{}`;
2. the area plan comes out empty;
3. `write_areas` then **clears every tool-written area** as stale.

Importing the Land export today, by hand or through the autopilot, would
wipe the operator's areas.

## Risk register and dispositions

| # | Severity | Risk | Disposition |
|---|----------|------|-------------|
| R-1 | blocking | A building sale-comps sheet also has acreage, price, date, zoning and coordinates, so it could be detected as Land. | A Land sheet must lack `Parcel Number 1 (Min)`, `RBA`, `Building SF` and `Year Built`. Rows whose `Property Type` is not `Land` are dropped. Tested. |
| R-2 | blocking | The ingest deletes only by (submarket, address, date). Sales that move submarket, or later stop qualifying, live on. Duplicates within a file are counted twice. | **Changed design:** the importer writes `sales_comps` itself, in **one transaction**. It deletes the land rows it wrote before (`raw.import_origin`), then inserts the new set. Hand-delivered Export 2 rows are never touched. Sales are deduplicated on (normalised address, date). An empty result is refused and changes nothing. Tested. |
| R-3 | blocking | The autopilot imports oldest first. A Land workbook imported before any areas exist would be refused and stop every later Start. Land comps would also keep an old assignment after the areas are redrawn. | No areas yet: the Land sales are **saved** in a gitignored snapshot (`costar_exports/.land_sales_snapshot.json`) and the import exits 0, so the autopilot is not blocked. Whenever a later workbook redraws the areas, the snapshot is re-assigned in the same run. `autopilot.sh` is unchanged. Tested. |
| R-4 | high | xlsx dates are serial numbers and may use the 1904 system. | Serial and ISO/US-text dates are parsed, `date1904` is honoured, and future dates are dropped. Tested. |
| R-5 | high | Portfolio and non-arm's-length sales distort $/acre. Without a status column, listings would pass as sales. | `Sale Status` is required, otherwise the sheet is refused. Rows marked "Part of a … Property" are dropped. $/acre is price ÷ acres; a row is dropped if CoStar's own $/acre differs by more than 5%. Tested. |
| R-6 | high | Ingest row errors put licensed values into flag notes. | Avoided: there is no CSV round trip. Rows use the ingest's own insert SQL and parameter builder, so S8 reads the same shape. |
| R-7 | should-fix | The resolver rule is copied from `research.py` and could drift. | The rule is pinned in this tool's own test, not compared byte-for-byte with the sandbox. It is checked live: comps and parcels at the same points must resolve to the same area. Assignment is frozen at import time (documented). |
| R-8 | should-fix | Human-seeded area ids differ from slug(name), so comps are lost. | Avoided: comps take the area's `submarket_id` directly. |
| R-9 | should-fix | County-to-market mapping noise. | Avoided: the market comes from the area. |
| R-10 | should-fix | Zoning classifier errors. | Token rule (`M`, `M-1A`, `LI-1`, `I-MIX`, `PD-I`, `IND`, …), plus "industrial" anywhere in the zoning text. `O-I`, `MU`, `MHP` and `MD` stay out. Table-driven test. A bare `I`/`M` token is accepted; that is the known false-positive risk. |
| R-11 | should-fix | A same-month CSV is overwritten. | Avoided: no CSV. |
| R-12 | should-fix | Mid-run crediting. | The existing baseline refusal (exit 3) runs before any land work. Tested. |
| R-13 | should-fix | Licensed-data leakage. | Console shows counts only. Fixtures are synthetic. The snapshot lives under gitignored `costar_exports/`. |
| R-14 | should-fix | The latent defect above. | An area-comps sheet now requires a parcel-number column. An empty plan writes and clears nothing. Tested. |
| R-15 | nit | What `--no-areas` does to land. | `--no-areas` means no database writes: land is skipped and reported. |
| — | note | Medians from 3+ comps are thin. | Unchanged. The 3-comp minimum is `research.py`'s (the loop's) constant. |
