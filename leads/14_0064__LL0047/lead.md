# Lead check: parcel 14 0064 LL0047, run fulton-2026-11-12

**In one line (the launching session's reading, not checked):** The seller is the doubt: tax bills go to the railroad's real estate office and the parcel adjoins railroad land; also check the abandoned 1950s streets under the site.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-12T19:32:40Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `by_right`.**
- City of Atlanta zoning layer (LotsWithZoning), queried at the parcel's map point (33.650297, -84.385996): one lot of 19.96 GIS acres zoned I-1. The PIN field is cut to '14 0064 LL004' (20-character field); the acreage matches the 20.0-acre parcel. (zoning-1)
  - On the page: "`"PIN":"14 0064 LL004","GIS_ACRES":19.96226963,"ZONING_CLASSIFICATION":"I-1"`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LotsWithZoning/MapServer/0/query?geometry=-84.385996%2C33.650297&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...'attributes': 'objectid 12':24344, (the quote) ,'muni link':'/u003ca (address removed)...
- and 4 more kept claims in the checked file.

**Plans: `supports`.**
- Atlanta GIS 'Development Patterns - Future Land Use' layer (layer 8), queried at the parcel's map point: future land use Industrial. (plans-1)
  - On the page: "`"NDP":"I","FID_L8LAND":2267,"LANDUSECOD":"I","LANDUSEDES":"Industrial"`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/8/query?geometry=-84.385996%2C33.650297&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...only', (the quote) ,'lastupdate':null,'lasteditor':' ','density'...
- and 4 more kept claims in the checked file.

**Utilities: `unknown`.**
- Sewer: the City of Atlanta Sewer Pipe layer (MapEGIS/manholes layer 1), asked for pipes within 1,200 ft of the parcel's map point that are not marked abandoned, returns 8-inch Active Public lines on Poole Creek Rd (the parcel's frontage road), upstream street numbers 44 to 183, i (cut) (utilities-1)
  - On the page: "`"NAME":"POOLE CREEK RD","SERVSTAT":"APUB","PIPEDIAM":8`" (<https://egis.atlantaga.gov/arc/rest/services/MapEGIS/manholes/MapServer/1/query?where=SERVSTAT%3C%3E%27ABAN%27&geometry=-84.385996%2C33.650297&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&distance=1200&units=esriSRUnit_Foot&outFields=NAME,SERVSTAT,PIPEDIAM,UNITTYPE,INSTALL_DATE,UPS_STNO,UPS_STNAME,UPS_SUFFIX&returnGeometry=false&f=json>)
  - Around it the page reads: ...suffix':null , 'attributes': (the quote) ,'unittype':'lat','install date':1956,'ups st...
  - Some of these words are also in the request (name, servstat, pipediam): check the page did not just show the request back.
- and 2 more kept claims in the checked file.

**Market: `no_sign_found`.**
- Atlanta GIS Rezoning Cases layer, queried at the parcel's map point: no rezoning case (by the owner or anyone else) covers the parcel. (market-1)
  - On the page: "`{"name":"ADDL_LEGIS","type":"esriFieldTypeString","alias":"ADDINALL LEGISLATIN","length":50}],"features":[]}`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/10/query?geometry=-84.385996%2C33.650297&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...len' , (the quote) ...
- and 1 more kept claims in the checked file.

**Owner: `unknown`.**

## Skeptic

**Skeptic: `weakened`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: The seller is the open question, and the first pass never tested it. The county roll sends the owner's tax bills to 650 West Peachtree St NW, which Norfolk Southern's own site gives as its Real Estate Department. The parcel adjoins a 25.5-acre tract owned by Central of Georgia Railroad Co, about 400 ft from the NS line, so this looks like railroad land next to railroad land, held since 2007. NS names two real-estate channels: sales to non-shipping customers, and strategic sales for its core business and freight customers. Its site does not say which channel this tract belongs to, whether it is held for rail-served or operating use, or whether the 'ET AL' co-owners would sell to a non-rail warehouse buyer. The market verdict no sign found rests only on empty rezoning and special-use queries, which say nothing about the owner's in (cut)

- Fulton County tax parcel layer, queried at the same map point the first pass used: it returns parcel 14 0064 LL0047 at 81 Poole Creek Rd SE. So the Atlanta zoning lot with the truncated PIN '14 0064 LL004' (zoning-1) is this parcel. (skeptic-1)
  - On the page: "`"ParcelID":"14 0064 LL0047","TaxYear":2027,"Address":"81 POOLE CREEK RD SE"`" (<https://gismaps.fultoncountyga.gov/arcgispub/rest/services/OpenData/Tax_Parcels/MapServer/0/query?geometry=-84.385996%2C33.650297&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...,'features': 'attributes': 'objectid':80587, (the quote) ,'addrnumber':'81','addrpredir':null,'addrstr...
- The same Fulton record sends the owner's bills to 650 West Peachtree St NW, Atlanta 30308. (skeptic-2)
  - On the page: "`"Owner":"SOUTHERN REGION INDUSTRIAL REALTY INC ET AL","OwnerAddr1":"650 WEST PEACHTREE ST NW","OwnerAddr2":"ATLANTA GA 30308"`" (<https://gismaps.fultoncountyga.gov/arcgispub/rest/services/OpenData/Tax_Parcels/MapServer/0/query?geometry=-84.385996%2C33.650297&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...sdir':'se','addrunttyp':null,'addrunit':null, (the quote) ,'taxdist':'05','lucode':'339','classcode':'i...
- and 6 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 23 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: utilities, owner.

## Questions for a person (the agents' own, not checked)

- (skeptic) Ask the owner (through a person, not this agent) whether the tract is held for rail-served or strategic use, is leased or used with the adjoining Central of Georgia Railroad tract at 25 Poole Creek Rd SE, who the 'ET AL' co-owners are, and whether a sale to a non-rail warehouse u (cut)
- (zoning) The zoning queries hit one map point inside the parcel, not the whole outline: confirm with Atlanta Office of Zoning and Development that the full parcel is I-1 with no conditions, overlay or pending text amendment (Atlanta is rewriting its zoning ordinance).
- (plans) Seen, not verifiable: (address removed) (scanned; the host answers robots. txt with 400, so quotecheck will not fetch it). Ordinance 06-O-2263 (Z-06-111), adopted 2007-02-05, rezoned 84, 88, 96, 98, 100, 120 and 130 Poole Creek Road and 3715 and 3711 Wilson Drive from I-1 and R-4 (cut)
- (utilities) Water: no public City of Atlanta water-main layer was found (the watershed GIS folders answered empty and the capital-projects service was not started), so water is unknown and the verdict is unknown though sewer is shown nearby. Ask Atlanta Department of Watershed Management for (cut)
- (market) A web search for the site address found no sale notice, public notice or development-authority release; listing sites were not checked (CoStar, LoopNet and Crexi are excluded by rule). Ask a broker or the owner whether the land is offered or committed.
- (owner) The owner on the assessor roll is an entity with 'ET AL' (more than one owner on title). The Georgia Secretary of State search is a known wall (403); a person should look up the entity's status, formation date, principal office and registered agent by hand.
- (skeptic) Status of ATL Zoning 2.0: web search results (snippets only, not verified) said the adopting ordinance (26-O-1508) was held in the Council Zoning Committee in autumn 2026. Check the Council file and the Draft V2 zoning map for this lot's proposed district, and whether warehousing (cut)
- (zoning) Municode (the current text of Chapter 16, I-1) was not read (known wall). Check the I-1 transitional yard / height-plane rules next to residential districts: the brief's surroundings and the abandoned 1958 street sewers (see utilities) suggest residential land nearby.
- and 12 more in the checked file.
