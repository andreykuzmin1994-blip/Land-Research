# 35 — Item 6: can neighbouring counties be added to the answer key? (feasibility, 2026-11-10)

> Research note by a read-only agent, saved unedited below the line. Nothing here
> has been built, and no evaluation file was touched. Its own section 4 separates
> what was fetched from what is inferred. One slip, reported by the agent: a
> byte-range request for Gwinnett's ownership ZIP was ignored by the server and
> the whole 240 MB file came down; only its headers were read and the file has
> been deleted.

---

# Can Clayton, Cobb, Gwinnett or DeKalb be added to the answer key?

Checked 2026-11-10 by fetching live endpoints. No repo file was touched; no CoStar or paid/sign-in source was used.

**Short answer.** None of the four counties publishes what Fulton does: a dated 2016 parcel layer with shape, owner, values and class code together. Every county has a usable current layer. Sales history is partial everywhere. Gwinnett is the best first candidate, but only as a "backcast" from today's data, which is a weaker key than Fulton's. Cobb has the best-matching data but its county policy sells parcel polygons for a fee, so it is not a free source.

**One thing went wrong.** I meant to read only the file listing of Gwinnett's ownership ZIP, but the server ignored the byte-range request and sent the whole 240 MB file. I read only the column headers and the layout sheet. The file is at `/tmp/claude-0/-home-user-Land-Research/4616ae55-d508-57c8-96ab-2dc9218861ee/scratchpad/dl_gwinnett/tax_assessor_property_ownership_data.zip`; delete it if you do not want it kept.

## 1. Availability

| County | (a) 2015-17 snapshot | (b) current parcel layer | (c) sales / deed history |
|---|---|---|---|
| Clayton | Not found | Confirmed | Partly: last sale only (date, price, deed book/page); no deed type, no grantor |
| Cobb | Partly: `Parcel2016` has shape, PIN and acreage only; no owner, values or class | Reachable without sign-in, but not free under county policy (see section 2) | Partly: full history with date, price, instrument; no grantor or buyer names; instrument blank in about 64% of rows |
| Gwinnett | Not found | Confirmed | Partly: three sales per parcel with date, amount, document type and grantor |
| DeKalb | Not found for 2016; dated snapshots exist for 2021-2025 only, without owner | Confirmed | Partly: one sale per parcel (the latest) with date, price, instrument, old owner, new owner |

The Georgia DOR tax digest cannot stand in for (a): the public files (georgiadata.org, 1990-2025) are totals by taxing jurisdiction and property class, not parcel records.

## 2. Sources I fetched

**Gwinnett**
- Current layer: `https://services3.arcgis.com/RfpmnkSAQleRbndX/arcgis/rest/services/FYT_Owner_Data/FeatureServer/1`, 305,466 records. Fields: `PIN, OWNER1, MAILSTAT, DEEDEDACREAGE, CALCULATEDACREAGE, LANDVAL1, DWLGVAL1, TOTVAL1, PROPCLAS, PCDESC, ZONING, GRANTOR1-3, DOC1REF-3`. Values are text fields.
- Plain parcel shapes: `.../Property_and_Tax/FeatureServer/0`, 309,585 records (`PIN, DEEDEDACREAGE, CALCULATEDACREAGE`; no owner or values).
- Free ZIP, updated quarterly: `https://www.gwinnettcounty.com/content/departments/its/gis-maps/tax_assessor_property_ownership_data.zip` (files dated 2026-10-05). Ownership sheet, about 315,000 rows: `PIN, OWNERNAME1, LEGALAC, LANDVAL1, DWLGVAL1, OTHVAL1, TOTVAL1, SALE1D-3D, SALE1AMT-3AMT, GRANTORNAME1-3, DOC1REF-3, PROPCLAS`. Land sheet adds `Doc1Type-3Type, Acres`. Improvement sheet adds `YrBuilt, UseCode, UseDesc`.
- Class codes differ from Fulton's: 398 Warehouse, 399 Warehouse PreFab, 397 Office Warehouse, 395 Truck Terminal, 391 Cold Storage, 401 Manufacturing; 390 is Amusement Park.
- Terms: no licence text on either ArcGIS item. `www.gwinnettcounty.com/robots.txt` sets `Crawl-delay: 5` and does not disallow the ZIP path. The county page offers the ZIP for public download.

**Cobb** (host `gis.cobbcounty.org/gisserver/rest/services/tax/`)
- 2016 shapes: `tax_assessors_map_mercator_rel/MapServer/28` (`Parcel2016`), 246,448 records, 4,881 of them 5-50 acres. Fields: `PIN, PARCEL_ID, ACRE_DEEDED, ACRE_CALC, TAXYR`. The same service has `Parcel2010` to `Parcel2023`.
- Current layer: `taxassessorsdaily/MapServer/0`, 280,022 records. Fields: `PIN, OWNER_NAM1, OWNER_STAT, ACRES, FMV_LAND, FMV_BLDG, FMV_TOTAL, CLASS, TAXDIST`. No land-use code. Only 442 parcels are class I, 168 of them with a $1M+ building.
- Sales table: `taxassessorsdaily/MapServer/9`, 941,607 rows, 333,535 dated 2016 or later. Fields: `PIN, SALEDT, PRICE, SALETYPE, SALEVAL, INSTRTYP, APRTOT`.
- Year built table: `taxassessorsdaily/MapServer/5`, 265,276 rows (`PIN, CARD, YRBLT, SQFT`).
- Terms: service copyright reads "Copyright Cobb County. All rights reserved." The county's "GIS Data Costs & Distribution Policy" (dated August 2022, posted as a 2026 PDF in its ArcGIS account) prices Tax Assessor parcel polygons at $22,000 county-wide and says users may not "create derivative works without the express written permission of Cobb County Government". It lists "Tax Assessor Tabular Parcel Data" (ownership, appraisal) as free on a Sharefile site, which I did not locate. No robots.txt on the GIS host (404). `cobbassessor.org/robots.txt` blocks named AI crawlers and signals `ai-train=no`; I fetched nothing else there.

**DeKalb** (ArcGIS account `services2.arcgis.com/IxVN2oUE9EYLSnPE/arcgis/rest/services/`)
- Dated snapshots: `Tax_Parcels_2021/FeatureServer/4` (244,234 records) and `Tax_Parcels_2025/FeatureServer/0` (246,602). Fields: `ParcelID, StatedArea, TAXYR, PROPERTY_CLASS, LANDUSECODE, APPRAISED_VALUE, LAND_VALUE, BLDG_VALUE`. No owner. 2022-2024 are listed but I did not open them.
- Current layer with owner: `TaxParcelsOD/FeatureServer/0`, 246,604 records, licence CC0. Fields: `PARCELID, OWNERNME1, PSTLSTATE, ACREAGE, CLASSCD, USECD, LNDVALUE, TOTAPR1, ZONING`.
- Sales: `TYLER_SALES/FeatureServer/3`, 263,995 rows, exactly one per parcel. Fields: `PARID, SALEDT, PRICE, INSTRTYP, OLDOWN, OWN1, SALEVAL, BOOK, PAGE`. These are the same column names as Fulton's `Tax_Sales.txt`.
- DeKalb uses the same land-use codes as Fulton (a sample warehouse was class `I3`, code `398`). 953 parcels in the 2025 snapshot carry a Fulton-list industrial code and a $1M+ building.
- A city-only copy exists: "2016 Tucker Parcels", 12,947 parcels, "accurate as of June 2016", with owner, acreage and class.
- Terms: the 2025 item says "for informational purposes only"; the sales table and 2021 item carry no licence text. No robots.txt on the county GIS host (404).

**Clayton**
- Current layer: `https://gis.claytoncountyga.gov/server/rest/services/TaxAssessor/Parcels/MapServer/0`, 92,100 records, 1,819 of them 5-50 acres. Fields: `PARCELID, OWNERNME, PSTLSTATE, ACERAGE, LANDFMV, DWELLINGFMV, BLDGSFMV, OTHERBLDGSFMV, LANDUSEC, LANDUSED, TDCC, YEARBUILT, SALEDATE, SALEPRICE, DEEDYEAR, CURRENTDEEDBOOK, CURRENTDEEDPAGE, ZONE`. Values and dates are text (`SALEDATE` like `20190828`).
- Same land-use codes as Fulton: 390 Warehouse Service (310 parcels), 394 Warehouse Distribution (173), 395 Truck Terminal (26), 401 Manufacturing (70).
- `TaxAssessor/ParcelsHistorical/MapServer/0`: 2,535 retired parcel shapes with a name and system dates ending August 2019, no other attributes.
- A March 2024 copy sits at `services5.arcgis.com/m528W8U8YDYeMPrQ/.../Clayton_GIS_SD_TAXPARCELS/FeatureServer/0` (90,848 records).
- Terms: no licence text; no robots.txt on the GIS host (404).

ArcGIS Online hosts (`services*.arcgis.com`) serve no robots.txt (403). I did not review Esri's own terms of use.

## 3. Which county first

**Gwinnett looks most feasible.** It is the only county that openly posts a bulk file with several sales per parcel, grantor names and deed types, plus year built, values and class. It is also large: 6,287 parcels of 5-50 acres and 2,266 in warehouse or manufacturing classes. The catch is that there is no 2016 snapshot, so the key would be rebuilt backwards from today's data. Three things are lost that way:
- Sites that were split or assembled after the sale carry new PINs and today's acreage, so some true positives drop out.
- 2016 land value and class code are unknown; today's values would leak the outcome and would have to be blank.
- The 2016 owner must be taken as the grantor on the first sale after 1 January 2016, or today's owner where there was none.

**Cobb looks infeasible as a free source.** Its data fits the key best (a real 2016 shape layer and a full sales table), but the county licenses parcel polygons for a fee and restricts derivative works. It also has no owner or grantor names for 2016, and few parcels are classed industrial.

**DeKalb looks infeasible for a 2016 start date.** Nothing county-wide predates 2021 and the sales table keeps only the latest sale. A 2021 start date would work mechanically, but four years is a short window.

**Clayton** sits between: a rich current layer on Fulton's code scheme, but only the last sale and nothing from 2016.

## 4. Open questions

Verified by fetching: every URL, field name, record count and terms quote above.

Inferred, not confirmed:
- That Gwinnett's `SALE1-3` are the three most recent sales, and what order they are in.
- That Clayton's `SALEDATE` is the most recent sale.
- That "the first deed after 1 January 2016 was granted by the 2016 owner" is a safe way to recover the 2016 owner.
- That Cobb's `Parcel2016` was captured around the end of 2016 (its `TAXYR` values run to 2017).

Could not determine:
- Whether each assessor still holds its 2016 digest and would release it on an open-records request. This is the only route I can see to a true 2016 snapshot.
- Whether Cobb's fee policy covers bulk queries of its public map service, where the free Sharefile tabular file is, whether it needs sign-in, and whether old years are kept.
- Whether Gwinnett keeps earlier quarterly ZIPs; only the current one is linked.
- How many positives each county would add. That needs row-level pulls, which I did not do.
- Whether a backcast key is acceptable for the metric at all. That is a design decision for whoever owns `build_eval.py`.
- Clayton's older host (`weba.co.clayton.ga.us:5443`) refused connections; the Gwinnett "GIS data map sales" page and the DeKalb assessor "Data Product - Full File" order form both returned 404.
