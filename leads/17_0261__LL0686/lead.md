# Lead check: parcel 17 0261 LL0686, run fulton-2026-11-13-screens

**In one line (the launching session's reading, not checked):** Probably tied to the operating truck stop next door, held by the same owner (the skeptic reads it as part of a long-leased travel-center portfolio); the city's layer shows two buildings on it, and residential districts lie within 150 ft. Find out whether the parcel is in the lease, and read the C-1-C conditions, before anything else.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-13T22:02:02Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `unknown`.**
- City of Atlanta's zoning district layer, queried at a point inside this parcel (33.791043, -84.487241), returns C-1-C, and the layer links it to Chapter 11 of the zoning code (Community Business District Regulations): a commercial district with conditions, not an industrial distr (cut) (zoning-1)
  - On the page: "`"ZONECLASS":"C-1-C","ZONEDESC":"https://library.municode.com/ga/atlanta/codes/code_of_ordinances?nodeId=PTIIICOORANDECO_PT16ZO_CH11COBUDIRE`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/22/query?geometry=-84.487241%2C33.791043&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...,'features': 'attributes': 'objectid':11303, (the quote) /r/n','baseelev':0.0,'height':0.0,'lastupdate...
- and 3 more kept claims in the checked file.

**Plans: `supports`.**
- The city's Development Patterns / Future Land Use layer, queried at a point inside the parcel, returns land use code I, Industrial. (plans-1)
  - On the page: "`"LANDUSECOD":"I","LANDUSEDES":"Industrial"`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/19/query?geometry=-84.487241%2C33.791043&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...only','ndp':'i','fid l8land':2470, (the quote) ,'lastupdate':null,'lasteditor':' ','density'...
- and 5 more kept claims in the checked file.

**Utilities: `unknown`.**

**Market: `no_sign_found`.**
- The city's building footprint layer, queried with this parcel's outline from the county parcel map (simplified by me to 50 corners, within about 0.5 m of the original; footprints the outline contains), returns two buildings of about 13,363 and 14,830 sq ft wholly inside the parce (cut) (market-1)
  - On the page: "`"AREASQFT":13362.67473684,"COMMENTS":null`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/10/query?geometry=%7B%22rings%22%3A%5B%5B%5B-84.48575%2C33.79358%5D%2C%5B-84.48532%2C33.79342%5D%2C%5B-84.48477%2C33.79391%5D%2C%5B-84.4847%2C33.79375%5D%2C%5B-84.48427%2C33.79337%5D%2C%5B-84.48437%2C33.79331%5D%2C%5B-84.48455%2C33.79318%5D%2C%5B-84.48469%2C33.793%5D%2C%5B-84.48475%2C33.7929%5D%2C%5B-84.48484%2C33.7927%5D%2C%5B-84.48488%2C33.7926%5D%2C%5B-84.48493%2C33.79239%5D%2C%5B-84.48494%2C33.79229%5D%2C%5B-84.48494%2C33.79223%5D%2C%5B-84.48493%2C33.79217%5D%2C%5B-84.48489%2C33.79206%5D%2C%5B-84.48479%2C33.79192%5D%2C%5B-84.48468%2C33.79181%5D%2C%5B-84.48453%2C33.79164%5D%2C%5B-84.48451%2C33.79161%5D%2C%5B-84.48448%2C33.79154%5D%2C%5B-84.48448%2C33.79151%5D%2C%5B-84.48449%2C33.79141%5D%2C%5B-84.48451%2C33.7913%5D%2C%5B-84.48453%2C33.79125%5D%2C%5B-84.48463%2C33.79109%5D%2C%5B-84.4847%2C33.79103%5D%2C%5B-84.48484%2C33.79091%5D%2C%5B-84.48512%2C33.7907%5D%2C%5B-84.48555%2C33.79032%5D%2C%5B-84.48568%2C33.79023%5D%2C%5B-84.48594%2C33.7901%5D%2C%5B-84.48607%2C33.79002%5D%2C%5B-84.48614%2C33.78997%5D%2C%5B-84.48627%2C33.78985%5D%2C%5B-84.48639%2C33.78972%5D%2C%5B-84.4865%2C33.78957%5D%2C%5B-84.48679%2C33.78905%5D%2C%5B-84.4869%2C33.78886%5D%2C%5B-84.48728%2C33.78831%5D%2C%5B-84.49039%2C33.7883%5D%2C%5B-84.49038%2C33.78936%5D%2C%5B-84.48972%2C33.79013%5D%2C%5B-84.48903%2C33.79079%5D%2C%5B-84.48911%2C33.79084%5D%2C%5B-84.48835%2C33.79174%5D%2C%5B-84.4873%2C33.7942%5D%2C%5B-84.48582%2C33.79417%5D%2C%5B-84.48585%2C33.79362%5D%2C%5B-84.48575%2C33.79358%5D%5D%5D%2C%22spatialReference%22%3A%7B%22wkid%22%3A4326%7D%7D&geometryType=esriGeometryPolygon&inSR=4326&spatialRel=esriSpatialRelContains&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...eunits':null,'baseelev':null,'roofelev':null, (the quote) ,'createdate':null,'geo oid':null,'struct id'...
- and 5 more kept claims in the checked file.

**Owner: `holding_entity`.**
- The county record for this parcel (queried by parcel id) gives the owner as an LLC mailing to a suite in Westlake, Ohio. (owner-1)
  - On the page: "`"Owner":"HPT PSC PROPERTIES LLC ","OwnerAddr1":"24601 CENTER RIDGE RD STE 200","OwnerAddr2":"WESTLAKE OH 44145-5634"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%20IN%20%28%2717%200261%20%20LL0686%27%2C%2717%20026000030295%27%29&outFields=*&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ...sdir':'nw','addrunttyp':null,'addrunit':null, (the quote) ,'taxdist':'05','totassess':388440,'landasses...
- and 2 more kept claims in the checked file.

## Skeptic

**Skeptic: `weakened`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: This parcel is not spare land on the market. Its owner of record is the REIT entity that on 30 May 2007, the date of this parcel's last sale, bought 40 Petro travel centers and leased them to TravelCenters of America; it owns the operating Petro truck stop next door, two buildings stand on this parcel beside it, and the landlord's leases on all 178 travel centers were extended in 2023 for 10 years with 50 years of tenant renewal options, now guaranteed by BP. On top of that the parcel is zoned C-1-C, whose use list bars wholesaling and permits no warehouse, and it sits within 150 ft of single-family and multi-family districts. Not killed only because no document read shows this 42.6 ac parcel is inside the leased premises rather than excess land.

It disputes these first-pass claims: market-6.

- Owner and use: a trade-press report of 30 May 2007 (the same date as this parcel's last recorded sale in the brief, $5.4 million) says that, as TravelCenters of America (TA) bought the Petro Stopping Centers business, HPT (Hospitality Properties Trust) bought the real estate of 4 (cut) (skeptic-1)
  - On the page: "`HPT acquired 40 Petro travel centers and leased them to TA under a long-term lease.`" (<https://www.truckinginfo.com/news/travelcenters-of-america-acquires-petro-stopping-centers>)
  - Around it the page reads: ...with ta's acquisition of petro, (the quote) in addition to its purchase price of approxi...
- Same article: the 40 Petro travel centers were leased from Hospitality Properties Trust at the time of the acquisition. (skeptic-2)
  - On the page: "`Simultaneously with this acquisition, 40 Petro travel centers were leased from Hospitality Properties Trust.`" (<https://www.truckinginfo.com/news/travelcenters-of-america-acquires-petro-stopping-centers>)
  - Around it the page reads: ...businesses of petro stopping centers. (the quote) prior to this acquisition, petro was a priva...
- and 5 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 26 claims and dropped 2. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: zoning, utilities.

## Questions for a person (the agents' own, not checked)

- (skeptic) Seen, not verifiable: search-result summaries (not opened; sec. gov answered my robots. txt request with HTTP 403 'Request Rate Threshold Exceeded', it demands a contact User-Agent) of TA's 2007 prospectus (sec. gov Archives edgar data 1378453, 424B1) and HPT's 2007 10-Q (edgar d (cut)
- (zoning) The conditions of C-1-C could not be read. Atlanta's code is on Municode (known wall), atlantaga. gov answers robots. txt with 403, and the third-party code copy atlanta. elaws. us timed out on the section page (16-11.003). Ask Atlanta Office of Zoning and Development (OZD) for t (cut)
- (plans) The same case layer also shows larger residential and mixed-use rezonings within 2,500 ft (Z-22-081, RG-3 to PD-MU, 68.6 ac, about 1,760 ft away by my measurement; R-4A to MR-MU cases in 2024-25): ask planning whether neighbourhood opposition to truck traffic is likely.
- (utilities) No published water or sewer map was read: Atlanta Department of Watershed Management publishes no queryable public layer found in earlier leads, and atlantaga. gov is a robots wall. The adjacent parcel holds an operating truck stop with a city address, so service very likely exis (cut)
- (market) Biggest finding: this parcel looks like part of an operating truck stop, not spare land for sale. The same owner holds the adjacent 16 ac truck stop (Petro, 3181 Donald Lee Hollowell Pkwy NW, at I-285 exit 12), and two buildings of about 28,000 sq ft together stand inside this pa (cut)
- (owner) Georgia Secretary of State business search is a known wall: entity status, formation date, principal office and registered agent were not read. Pull the record by hand.
- (skeptic) Ask (via title, not by contacting anyone) whether a memorandum of lease to the travel center operator is recorded against this parcel, and whether the tax-roll mailing address is the tenant's office rather than the landlord's: tax bills sent to the tenant would point to a triple- (cut)
- (zoning) Seen, not verifiable (search-result summary only, page on atlantaga. gov not opened): a Zoning Review Board agenda of April 10, 2014 (atlantaga. gov Home ShowDocument id 13563) is said to list Z-13-55/Z-80-100, a site plan amendment for the C-1-C property at 3181 Donald Lee Hollo (cut)
- and 15 more in the checked file.
