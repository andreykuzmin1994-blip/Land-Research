# Lead check: parcel 14 0002 LL0331, run fulton-2026-11-12

**In one line (the launching session's reading, not checked):** The Industrial future land use behind its grade is a superseded field: the current plan shows Low Density Residential and it is zoned multi-family with unknown conditions, so it needs a plan amendment and a rezoning.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-12T19:55:14Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `rezoning_needed`.**
- The City of Atlanta zoning district layer, queried at the parcel's point (-84.35199, 33.661329), returns district RG-2-C, coded Multi-Family Residential, in a 37.3-acre zoning polygon (the parcel is 27.1 acres, so the polygon reaches beyond it). A residential district: warehouse  (cut) (zoning-1)
  - On the page: "`"REFERENCE":"R717","SHAPE.AREA":1626190.7094644101,"SHAPE.LEN":5595.9590502994897,"ZONINGCODE":"Multi-Family Residential"`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/22/query?geometry=-84.35199%2C33.661329&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...', (the quote) ...
- and 2 more kept claims in the checked file.

**Plans: `against`.**
- The city's current 'Development Patterns - Future Land Use' layer, queried at the parcel's point, gives development pattern LDR (Low Density Residential). The Industrial value the screener read is the older land-use field (LANDUSEDES) carried in the same record, beside a 'Technic (cut) (plans-1)
  - On the page: "`"PIN":"Low Density Residential","CONVERSION":"Technical Conversion","NDP":"LDR","FID_L8LAND":2641,"LANDUSECOD":"I","LANDUSEDES":"Industrial"`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/8/query?geometry=-84.35199%2C33.661329&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...l4ndp ':779,'fid 1':0.0, (the quote) ,'lastupdate':null,'lasteditor':' ','density'...
- and 4 more kept claims in the checked file.

**Utilities: `unknown`.**

**Market: `no_sign_found`.**
- No rezoning case (by the owner or anyone else) is recorded within 800 m of the parcel's point in the city's rezoning-cases layer (LandUsePlanning layer 10): the answer is empty. (market-1)
  - On the page: "`"alias":"ADDINALL LEGISLATIN","length":50}],"features":[]`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/10/query?geometry=-84.35199%2C33.661329&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json&distance=800&units=esriSRUnit_Meter>)
  - Around it the page reads: ...legis','type':'esrifieldtypestring', (the quote) ...
- and 2 more kept claims in the checked file.

**Owner: `unknown`.**

## Skeptic

**Skeptic: `weakened`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: The grade A rests on a future-land-use value the city has deprecated. The Industrial label the screener read is copied from the deprecated LandUse Future map (same polygon 2641, same 1,053 acres). The current development pattern over the whole parcel polygon, not just its centre point, is Low Density Residential. The parcel is zoned RG-2-C and touches RG-2, both multi-family residential, and an R-4 single-family district lies within 30 m. Industrial use would need both a plan amendment and a rezoning, against the adopted pattern, beside homes, with only about 219 ft of frontage on a residential collector. That is not a likely entitlement path. It is not killed: a 2025 amendment nearby (CDP-25-012, single-family to industrial) and a pending PD-H to I-1 rezoning show such a change has recent precedent, and I-1-C land lies within 3 (cut)

It disputes these first-pass claims: market-2, market-3.

- Asked the city zoning-district layer (OpenDataService1 layer 22) for districts within 30 m of the tax parcel's own polygon (taken from the city TaxParcel layer): it returns only RG-2 (R3072), RG-2-C (R717) and R-4 (R3094), and R-4 is coded Single Family Residential, a 284.6-acre  (cut) (skeptic-1)
  - On the page: "`"REFERENCE":"R3094","SHAPE.AREA":12398313.6893618,"SHAPE.LEN":15854.2358461323,"ZONINGCODE":"Single Family Residential"`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/22/query?geometry=%7B%22rings%22%3A%5B%5B%5B-84.349874%2C33.66013%5D%2C%5B-84.352635%2C33.660164%5D%2C%5B-84.355202%2C33.660195%5D%2C%5B-84.355465%2C33.660199%5D%2C%5B-84.355469%2C33.660234%5D%2C%5B-84.355471%2C33.660257%5D%2C%5B-84.355474%2C33.66028%5D%2C%5B-84.355479%2C33.660326%5D%2C%5B-84.355482%2C33.660349%5D%2C%5B-84.355488%2C33.660395%5D%2C%5B-84.355495%2C33.660441%5D%2C%5B-84.355498%2C33.660464%5D%2C%5B-84.355505%2C33.66051%5D%2C%5B-84.355513%2C33.660556%5D%2C%5B-84.355517%2C33.660579%5D%2C%5B-84.355525%2C33.660624%5D%2C%5B-84.355533%2C33.66067%5D%2C%5B-84.355261%2C33.660838%5D%2C%5B-84.355204%2C33.66086%5D%2C%5B-84.35389%2C33.661384%5D%2C%5B-84.353636%2C33.661446%5D%2C%5B-84.353537%2C33.661654%5D%2C%5B-84.353949%2C33.661726%5D%2C%5B-84.352639%2C33.662276%5D%2C%5B-84.349845%2C33.66345%5D%2C%5B-84.349874%2C33.66013%5D%5D%5D%7D&geometryType=esriGeometryPolygon&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&f=json&returnGeometry=false&distance=30&units=esriSRUnit_Meter>)
  - Around it the page reads: ...', (the quote) ...
- Asked the Development Patterns - Future Land Use layer (LandUsePlanning layer 8) with the whole parcel polygon, not just its centre point: it returns a single polygon, whose development-pattern name is Low Density Residential. No part of the parcel carries an industrial pattern. (skeptic-2)
  - On the page: "`"DP_NAME":"Low Density Residential","SIMPLE_TRA":"Technical Changes"`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/8/query?geometry=%7B%22rings%22%3A%5B%5B%5B-84.349874%2C33.66013%5D%2C%5B-84.352635%2C33.660164%5D%2C%5B-84.355202%2C33.660195%5D%2C%5B-84.355465%2C33.660199%5D%2C%5B-84.355469%2C33.660234%5D%2C%5B-84.355471%2C33.660257%5D%2C%5B-84.355474%2C33.66028%5D%2C%5B-84.355479%2C33.660326%5D%2C%5B-84.355482%2C33.660349%5D%2C%5B-84.355488%2C33.660395%5D%2C%5B-84.355495%2C33.660441%5D%2C%5B-84.355498%2C33.660464%5D%2C%5B-84.355505%2C33.66051%5D%2C%5B-84.355513%2C33.660556%5D%2C%5B-84.355517%2C33.660579%5D%2C%5B-84.355525%2C33.660624%5D%2C%5B-84.355533%2C33.66067%5D%2C%5B-84.355261%2C33.660838%5D%2C%5B-84.355204%2C33.66086%5D%2C%5B-84.35389%2C33.661384%5D%2C%5B-84.353636%2C33.661446%5D%2C%5B-84.353537%2C33.661654%5D%2C%5B-84.353949%2C33.661726%5D%2C%5B-84.352639%2C33.662276%5D%2C%5B-84.349845%2C33.66345%5D%2C%5B-84.349874%2C33.66013%5D%5D%5D%7D&geometryType=esriGeometryPolygon&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...edi 1':1615939200000,'unitcount':0.0, (the quote) ,'shape leng':37241.5991306,'shape. area':866 (cut)...
- and 6 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 19 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: utilities, owner.

## Questions for a person (the agents' own, not checked)

- (skeptic) market-2 (no improvement value) and market-3 (no building footprints) are true as far as their quotes go, but they say the land is vacant, not whether it is for sale, under contract or with a developer. They do not bear on the no sign found verdict, which rests on market-1 alone.
- (zoning) The RG-2 district text (Atlanta Code Part 16, Chapter 8) is on Municode, which cannot be fetched; confirm with Atlanta planning that RG-2 permits no warehouse, distribution or light industrial use.
- (plans) Confirm with Atlanta planning which map is the adopted future land use map (the 2025 Plan A development patterns or the older land-use field): the screener's 'likely' entitlement path rests on the older Industrial value, while the current pattern reads Low Density Residential.
- (utilities) Water: no public City of Atlanta water-main layer was found (known from another Atlanta lead).
- (market) Listing sites were not checked (CoStar, LoopNet and Crexi are off limits); a person should look for a sign or listing on site.
- (owner) Owner is an LLC per the assessor; the Georgia Secretary of State record (status, formation, agent, principal office) could not be read (known wall). A person should pull it.
- (skeptic) The first pass's plans-1 point holds and is strengthened: queried with the full parcel polygon, layer 8 returns one LDR polygon only. The first pass's own open item that the area has been moving from industrial to residential plans is contradicted by CDP-25-012 (single-family to  (cut)
- (zoning) Find the rezoning ordinance that created the RG-2-C conditions (the zoning polygon carries only reference R717 and no case number); the conditions may tie the land to a specific residential site plan.
- and 17 more in the checked file.
