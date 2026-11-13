# Lead check: parcel 14 0180 LL0368, run fulton-2026-11-13-screens

**In one line (the launching session's reading, not checked):** Not vacant: county utility-class improvements and an EPA-registered propane terminal at an address the city puts on this parcel; the skeptic also reads part of it as R-4. Only a lead if the terminal is closing; needs an environmental review.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-13T19:15:35Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `by_right`.**
- City of Atlanta's zoning district layer, queried at a point inside the parcel's eastern piece (33.754476, -84.462237), returns I-1 Light Industrial, linked to the I-1 chapter of the city code. (zoning-1)
  - On the page: "`"ZONECLASS":"I-1","ZONEDESC":"https://library.municode.com/ga/atlanta/codes/code_of_ordinances?nodeId=PTIIICOORANDECO_PT16ZO_CH16LIINDIRE"`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/22/query?geometry=-84.462237%2C33.754476&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...,'features': 'attributes': 'objectid':12657, (the quote) ,'baseelev':0.0,'height':0.0,'lastupdate':nul...
- and 5 more kept claims in the checked file.

**Plans: `supports`.**
- The city's Development Patterns / Future Land Use layer, queried at a point inside the parcel, returns Industrial. (plans-1)
  - On the page: "`"LANDUSECOD":"I","LANDUSEDES":"Industrial","LASTUPDATE":null`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/19/query?geometry=-84.462237%2C33.754476&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...only','ndp':'i','fid l8land':2296, (the quote) ,'lasteditor':' ','density':' ','far':' ','hi...
- and 4 more kept claims in the checked file.

**Utilities: `unknown`.**

**Market: `no_sign_found`.**
- EPA's Facility Registry lists 'AmeriGas Atlanta Terminal' at 71 Fairfield Place NW, Atlanta, under the EPA air program (AIRS/AFS). The city's address layer (next claim) puts 71 Fairfield Place NW on this parcel: the parcel appears to be an operating or recently operating propane  (cut) (market-1)
  - On the page: "`"primary_name": "AMERIGAS ATLANTA TERMINAL", "std_state_code": null, "county_name": "FULTON", "small_bus_ind": null, "source_of_data": "AIRS/AFS"`" (<https://data.epa.gov/efservice/FRS_PROGRAM_FACILITY/REGISTRY_ID/110013693770/JSON>)
  - Around it the page reads: ...name': null, 'registry id': '110013693770', (the quote) , 'std street name': null, 'postal code': '30...
- and 8 more kept claims in the checked file.

**Owner: `operator`.**
- The county record for this parcel (queried by parcel id) gives the owner as an LLC mailing to a suite on Peachtree St in Atlanta 30308. (owner-1)
  - On the page: "`"Owner":"AGL PROPANE LLC ","OwnerAddr1":"817 PEACHTREE ST # 1000","OwnerAddr2":"ATLANTA GA 30308"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2714%200180%20%20LL0368%27&outFields=*&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ...sdir':'nw','addrunttyp':null,'addrunit':null, (the quote) ,'taxdist':'05','totassess':0,'landassess':13...
- and 2 more kept claims in the checked file.

## Skeptic

**Skeptic: `weakened`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: This is not vacant industrial land: the county classes it as utility property with 1.5 million dollars of improvements, and EPA registers a propane terminal at 71 Fairfield Place NW, an address the city puts on this parcel. The owner, a gas-utility affiliate with gas-utility land across the street, looks like it operates the site, so a sale would need the terminal closed or moved, and nothing shows that is planned. The first pass also missed that the parcel is not all I-1. The east end of the larger eastern piece, the 10.5 acre piece the building fit uses, is zoned R-4 single-family (about 2.6 acres by my overlay of the city zoning polygons on the county parcel shape) and shown as Single-Family Residential on the future land use map (about 3.6 acres). That leaves roughly 7.8 acres of I-1 in that piece, around an existing termina (cut)

It disputes these first-pass claims: zoning-1, zoning-4, plans-1.

- The city's zoning district layer, queried at a point in the east end of the parcel's larger (eastern) piece (33.754193, -84.460674; inside the county parcel shape by my own point-in-polygon test), returns R-4 single-family residential, not I-1. By my overlay of the city's zoning  (cut) (skeptic-1)
  - On the page: "`"ZONECLASS":"R-4","ZONEDESC":"https://library.municode.com/ga/atlanta/codes/code_of_ordinances?nodeId=PTIIICOORANDECO_PT16ZO_CH6SIMIREDIRE"`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/22/query?geometry=-84.460674%2C33.754193&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...,'features': 'attributes': 'objectid':11103, (the quote) ,'baseelev':0.0,'height':0.0,'lastupdate':nul...
- The city's Development Patterns / Future Land Use layer, queried at the same point inside the parcel's eastern piece, returns Single-Family Residential (Medium Lot Single Family), not Industrial. By my overlay, about 3.6 acres of the eastern piece are Single-Family Residential on (cut) (skeptic-2)
  - On the page: "`"PIN":"Medium Lot Single Family","CONVERSION":"FLU + Zoning","NDP":"MLSF","FID_L8LAND":2251,"LANDUSECOD":"SFR","LANDUSEDES":"Single-Family Residential"`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/19/query?geometry=-84.460674%2C33.754193&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...l4ndp ':3174,'fid 1':0.0, (the quote) ,'lastupdate':null,'lasteditor':' ','density'...
- and 2 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 27 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: utilities.

## Questions for a person (the agents' own, not checked)

- (skeptic) Ask Atlanta Office of Zoning and Development to confirm the zoning line: is the east end of the eastern piece really R-4 single-family (a split-zoned parcel), or is the city polygon mis-drawn against the county parcel line? A zoning verification letter should name every district  (cut)
- (zoning) The I-1 district text itself is on Municode (known wall) and atlantaga. gov answers robots. txt with 403; the use permission rests on the 2023 ordinance's own recital. Ask Atlanta Office of Zoning and Development for a zoning verification letter for warehouse/distribution on this (cut)
- (plans) Future land use and the case layers were queried at one point in the parcel's eastern piece; the western piece (around 33.7546, -84.4645) was not queried separately. Confirm the whole parcel is Industrial on the adopted CDP map.
- (utilities) No published water or sewer map was read: Atlanta Department of Watershed Management publishes no queryable public layer found in an earlier lead, and atlantaga. gov is a robots wall. The parcel holds existing buildings with city address points, so service probably exists, but no (cut)
- (market) Biggest finding: the parcel appears to hold an active (or recently active) propane bulk terminal (EPA names AmeriGas Atlanta Terminal, Heritage Propane, Gasco at 71 Fairfield Pl NW, an address the city puts on this parcel) plus about 10,000 sq ft of buildings and a likely cell-to (cut)
- (owner) The Georgia Secretary of State record (status, formation, registered agent) was not read: known wall. Ask whether the owner of record is the terminal operator or leases the site to the propane distributor named in EPA's records; a lease would affect any sale.
- (skeptic) Re-run the building fit on the I-1 part only (about 3.9 ac west piece and about 7.8 ac of the east piece, the latter roughly 650 by 690 ft by my overlay) and inside Atlanta's transitional yard and screening rules toward R-4, RG-3 and MR-2-C (Municode wall, not read). The 520 x 26 (cut)
- (zoning) Confirm the parcel is more than 500 ft from the Beltline corridor (my reading of the map: the parcel is in Dixie Hills near I-20/MLK Jr Dr, well west of the Westside Beltline, but this was not measured from a city layer).
- and 11 more in the checked file.
