# Lead check: parcel 14 0001 LL0605, run fulton-2026-11-12

**In one line (the launching session's reading, not checked):** The Industrial future land use behind its grade is a superseded field: Atlanta's July 2025 plan shows Low Density Residential, beside platted subdivisions, so industrial use needs a plan amendment and a rezoning next to homes.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-12T19:51:47Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `rezoning_needed`.**
- The City of Atlanta zoning layer (layer 0), queried over the parcel's bounding box (-84.3560,33.6486 to -84.3498,33.6519), returns a single district: R-4, Single Family Residential, part of a 284.6-acre R-4 polygon. A single-family district does not list warehouse or industrial u (cut) (zoning-1)
  - On the page: "`"ZONING":"R-4","EDITTED":" ","REFERENCE":"R3094","SHAPE.AREA":12398313.6893618,"SHAPE.LEN":15854.2358461323,"ZONINGCODE":"Single Family Residential"`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/0/query?geometry=-84.3560%2C33.6486%2C-84.3498%2C33.6519&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...','acres':284.62725626, (the quote) ...

**Plans: `against`.**
- The City of Atlanta 'Development Patterns - Future Land Use' layer (layer 8), queried over the parcel's bounding box, returns one polygon (1,053 acres) whose current development pattern is LDR, named Low Density Residential. The 'Industrial' value the screener read is the older L (cut) (plans-1)
  - On the page: "`"NDP":"LDR","FID_L8LAND":2641,"LANDUSECOD":"I","LANDUSEDES":"Industrial"`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/8/query?geometry=-84.3560%2C33.6486%2C-84.3498%2C33.6519&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...conversion', (the quote) ,'lastupdate':null,'lasteditor':' ','density'...
- and 6 more kept claims in the checked file.

**Utilities: `unknown`.**
- Sewer: the City of Atlanta sewer pipe layer (MapEGIS/manholes layer 1), queried over the parcel's bounding box (which is larger than the parcel), returns 11 pipes, all 8-inch, including outfall segments named RED OAK DR OUTF and laterals on Forest Valley Dr, Conley Rd, Rigby Ct a (cut) (utilities-1)
  - On the page: "`"NAME":"RED OAK DR OUTF","UPS_STNO":"1025","UPS_PREDIR":" ","UPS_STNAME":"Forest Valley","UPS_SUFFIX":"DR","UPS_POSTDIR":"SE","ZIP":"30354","SEWERSHED":"SR11YB","BASIN":"BSOC","SEWERGROUP":1,"UNITTYPE":"OUT","SM_SUBTYPE":5,"SURF":"G","SERVSTAT":"APUB","PIPETYPE":"CO","PIPESHP":"C","PIPEDIAM":8`" (<https://egis.atlantaga.gov/arc/rest/services/MapEGIS/manholes/MapServer/1/query?geometry=-84.3560%2C33.6486%2C-84.3498%2C33.6519&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...o':'233202','source':'sses','invmethod':null, (the quote) ,'pipeht':0,'pipelen':257.52134183999999,'ups...

**Market: `no_sign_found`.**
- The rezoning-cases layer queried over the parcel's bounding box returns no case, so no rezoning has been applied for on this parcel by anyone (owner or other) per the city's map. (market-1)
  - On the page: "`"alias":"ADDINALL LEGISLATIN","length":50}],"features":[]`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/10/query?geometry=-84.3560%2C33.6486%2C-84.3498%2C33.6519&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...legis','type':'esrifieldtypestring', (the quote) ...
- and 2 more kept claims in the checked file.

**Owner: `unknown`.**

## Skeptic

**Skeptic: `weakened`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: The lead's grade A and its 'likely' entitlement path rest on a future land use of Industrial. The city's own authoritative record for its July 2025 Comprehensive Development Plan says the development-pattern layer replaced that future land use dataset, and on the parcel's own polygon that layer shows only Low Density Residential. So the Industrial value is superseded, and the adopted plan points to houses. The tract is also zoned R-4, and recorded single-family subdivisions touch it: The Villages at Oakshire Phase 2 along its north line and Forest Valley lots. Industrial use would need a plan amendment and a rezoning next to existing homes, and a residential-named owner may intend houses. A person must settle whether any such path is realistic before this is treated as an industrial lead.

It disputes these first-pass claims: plans-2, market-1.

- The city's own ArcGIS Online record for its development-pattern layer calls it the authoritative layer created by the Comprehensive Development Plan passed in July 2025, replacing the now-deprecated Future Land Use dataset. So the development pattern, not the old future land use  (cut) (skeptic-1)
  - On the page: "`The authoritative Development Patterns GIS layer created by the Comprehensive Development Plan passed in July 2025. The Development Patterns replaces the Future Land Use dataset, which is now deprecated.`" (<https://www.arcgis.com/sharing/rest/content/items/4deacd62fd36463fa33afa87b45acaf3?f=json>)
  - Around it the page reads: ...text-transform:none; word-spacing:0px;' (the quote) (address removed) p span style='background-co (cut)...
- That authoritative record points to exactly the layer the first pass queried (LandUsePlanning MapServer layer 8 on gis. atlantaga. gov). (skeptic-2)
  - On the page: "`"url":"https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/8","proxyFilter":null,"access":"public"`" (<https://www.arcgis.com/sharing/rest/content/items/4deacd62fd36463fa33afa87b45acaf3?f=json>)
  - Around it the page reads: ...s','properties':null,'advancedsettings':null, (the quote) ,'size':88,'subinfo':0,'appcategories': ,'in...
- and 6 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 20 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: utilities, owner.

## Questions for a person (the agents' own, not checked)

- (skeptic) plans-2: the quote is on the page, but the surrounding text quotecheck matched (shape. area 117264, shape leng 1695.96) belongs to feature OBJECTID 16895, a 2.7-acre polygon that my parcel-polygon query shows does not touch the parcel. Its words are identical to those of the feat (cut)
- (zoning) The R-4 use table is on Municode (not fetchable by policy here); a person should confirm R-4 permits no warehouse/distribution use, and what an R-4 to I-1/I-2 rezoning would need.
- (plans) Confirm with Atlanta City Planning that the adopted CDP (Plan A, reported adopted July 2025) shows Low Density Residential here; an industrial project would need a CDP amendment as well as a rezoning.
- (utilities) Water: the City of Atlanta EGIS MapWF folder lists no services publicly and no water-main layer was found; ask Atlanta Watershed Management for the nearest water main and its size.
- (market) Two web searches by the site address found no listing, notice or development record; listing sites were not opened. A person may check the owner directly (no contact made here).
- (owner) Owner is an LLC whose name contains 'Residential'; the Georgia Secretary of State record (status, formation, agent) could not be read (403 wall) and the name was not searched by rule. A person should pull it: a residential-builder owner of an LDR-designated R-4 tract may be holdi (cut)
- (skeptic) plans-1 detail: the bounding-box query returned two features, not one (16895 and 16892), and '1,053 acres' is a carried ACRES attribute, not either polygon's size (about 2.7 and 199 acres). The LDR reading is right (my claims 1 to 4).
- (zoning) The zoning record carries no conditional-zoning case number (CASEIN/CASEOUT blank), so no conditions are recorded on this polygon in the layer.
- and 15 more in the checked file.
