# Lead check: parcel 09F140000805662, run fulton-2026-11-13-screens

**In one line (the launching session's reading, not checked):** Likely not a building site: the county shape is a long curved strip, by the agents' measurement under about 300 ft wide, inside a built industrial park. At most trailer parking or storage; drop it as a building lead.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-13T19:22:45Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `unknown`.**
- The 2005 ARC regional review of the 192.54-acre Majestic Airport Center III warehouse park (2.3 million sq ft, Land Lots 61, 62 and 80, District 9F) records the park site as zoned M-1 with no rezoning needed: a 2005 statement about the park as a whole, not a reading of today's ma (cut) (zoning-1)
  - On the page: "`The project site is currently zoned M-1 (light industrial). Rezoning is not required for the development. The DRI review was triggered by a land disturbance permit request from the applicant.`" (<http://documents.atlantaregional.com/Land%20Use/Reviews/ID33/Preliminary%20Report.PDF>, PDF page 3)
  - Around it the page reads: ...plan? if not, identify inconsistencies. (the quote) information submitted for the review states...
- and 1 more kept claims in the checked file.

**Plans: `supports`.**
- Union City's 2020 comprehensive plan update (ARC review copy, January 2021) applies its District-Industrial character area close to I-85. This parcel lies about 0.12 mi from I-85 exit 66 (brief) inside an operating warehouse park, but the plan's Future Development Map was not rea (cut) (plans-1)
  - On the page: "`Application: D-I in close proximity to I-85, US-29, South Fulton Parkway and rail lines for the purpose of convenient transportation access.`" (<http://documents.atlantaregional.com/Land%20Use/Reviews/ID2086/2021.01.26.CityofUnionCity.CompPlanUpdate_Reduced.pdf>, PDF page 74)
  - Around it the page reads: ...as industrial, commercial and service uses. (the quote) green space formal landscaping and appropr...
- and 3 more kept claims in the checked file.

**Utilities: `not_shown`.**
- Sewer, service area: Fulton County's water/sewer service area layer (WR Public Map Viewer layer 16), asked at the brief's map point for this parcel (-84.513565, 33.582718), answers Fulton County, sewer, service available. (utilities-1)
  - On the page: "`"Name":"Fulton County","ServiceTyp":"Sewer","ServiceAva":"Yes"`" (<https://gismaps.fultoncountyga.gov/arcgispub/rest/services/WaterResources/WR_Public_Map_Viewer/MapServer/16/query?where=1%3D1&geometry=-84.513565%2C33.582718&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...star':73.23144531,'shape stle':769.14763871, (the quote) ,'shape. starea()':10173715022.83869,'shape.  (cut)...
- and 3 more kept claims in the checked file.

**Market: `no_sign_found`.**
- ODD LOT, NOT A PAD: asked by parcel id, Fulton County's parcel record gives 8.99 ac of land only, no improvements, as Lot 4 of Block A, with an area of about 408,000 sq ft and a perimeter of about 5,320 ft. A compact 9-acre lot has a perimeter near 2,500 ft; from the returned pol (cut) (market-1)
  - On the page: "`"TotAppr":453200,"LandAppr":453200,"ImprAppr":0,"LUCode":"400","ClassCode":"I4","ExCode":null,"LivUnits":0,"LandAcres":8.99,"NbrHood":"C811","Subdiv":null,"SubdivNum":null,"SubdivLot":"4","SubdivBlck":"A"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2709F140000805662%27&outFields=*&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ...s':181280,'landassess':181280,'imprassess':0, (the quote) ,'featureid':'09f140000805662','shape. starea (cut)...
- and 4 more kept claims in the checked file.

**Owner: `developer_or_investor`.**
- Asked by parcel id, the county record gives the owner as a phase-numbered LLC of the Majestic Airport Center III park, mailing to 3490 Piedmont Rd NE, Suite 210, Atlanta. (owner-1)
  - On the page: "`"Owner":"MAJESTIC AIRPORT CTR III PHASE TWO LLC ","OwnerAddr1":"3490 PIEDMONT RD NE STE 210","OwnerAddr2":"ATLANTA GA 30305"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2709F140000805662%27&outFields=*&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ...sdir':null,'addrunttyp':null,'addrunit':null, (the quote) ,'taxdist':'50','totassess':181280,'landasses...
- and 1 more kept claims in the checked file.

## Skeptic

**Skeptic: `killed`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: The lot cannot hold a warehouse. Fulton County's own parcel polygon for it (same record, returned with geometry) is a curved strip about 2,170 ft long. By the skeptic's own computation on those coordinates, the widest circle that fits inside it is only about 290 ft across, so no 400 ft deep building envelope (the smallest prototype) fits anywhere on it, whatever the grading. The record's area of about 408,000 sq ft against a perimeter of about 5,320 ft says the same: a rectangle of that area and perimeter is about 165 ft wide. On the county's 2024 aerial it is the leftover land between Oakley Industrial Blvd and the I-85 ramp, held by the developer of the built-out park around it. At most it is a trailer-parking or outdoor-storage strip, not a building site, and the plan and zoning claims rest on 2005 and city-wide text, not on  (cut)

It disputes these first-pass claims: plans-1, plans-3.

- Asked by parcel id with its geometry, Fulton County's tax parcel layer gives the lot an area of about 408,280 sq ft and a perimeter of about 5,320 ft. The page also returns the polygon (54 corner points). From those points the skeptic computes (not on the page) a bounding box of  (cut) (skeptic-1)
  - On the page: "`"Shape.STArea()":408280.43562627491,"Shape.STLength()":5320.0777685435742`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2709F140000805662%27&outFields=*&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ...ubdivblck':'a','featureid':'09f140000805662', (the quote) ,'geometry': 'rings': -84.511478366957732,...
- Asked by parcel id without geometry, the same county record gives the same area and perimeter. The record carries 8.99 acres of land only, with no improvements. (skeptic-2)
  - On the page: "`"TotAppr":453200,"LandAppr":453200,"ImprAppr":0,"LUCode":"400","ClassCode":"I4","ExCode":null,"LivUnits":0,"LandAcres":8.99`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2709F140000805662%27&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...s':181280,'landassess':181280,'imprassess':0, (the quote) ,'nbrhood':'c811','subdiv':null,'subdivnum':n...

## What the check proves

`quotecheck` kept 19 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: zoning.

## Questions for a person (the agents' own, not checked)

- (skeptic) Seen, not verifiable: Fulton County's 2024 aerial (gismaps. fultoncountyga. gov, Imagery/Pictometry2024 NAIP MapServer export over the parcel's extent, with the county polygon drawn on it by the skeptic). It shows the lot as an open grassed strip. Oakley Industrial Blvd runs alon (cut)
- (zoning) Union City's own website (unioncityga. gov) answered 403 even for robots. txt, so the current zoning map, the M-1 use table and any conditions of zoning for this lot could not be read. Ask Union City planning to confirm the lot's district today and whether warehouse/distribution  (cut)
- (plans) Rezoning history in the last five years could not be read (Union City site 403). Search results only (not read, not verifiable): Union City Planning and Zoning Commission minutes of June 30, 2025 list a rezoning of a property addressed 0 Oakley Industrial Blvd from GC to TCMU for (cut)
- (utilities) The lots beside this one carry built warehouses (market section: about 6.2, 36.4 and 43.2 million dollars of improvements), so water and sewer are very likely in the park's streets; the verdict only says no map read here shows the lines. Ask Union City (water) and Fulton County P (cut)
- (market) Places looked: Fulton County parcel layer (by parcel id and by extent), ARC regional review documents, web searches by site address and road names. No listing, sale notice, contract or development-authority release for this lot was found; Union City agendas could not be read (403 (cut)
- (owner) Georgia Secretary of State record not read (known wall; and the owner name may not be put into an outside service). Status, formation date and registered agent are unknown.
- (skeptic) The plans verdict 'supports' rests on city-wide character-area text (plans-1, plans-2) and on the developer's 2005 statement about Land Lots 61, 62 and 80 (plans-3). The lot lies mostly in Land Lots 81 and 82 (zoning-2). No document read places this lot in a character area or in  (cut)
- (zoning) The screener's M-1 reading comes from its own zoning layer; no document read here places this specific lot in M-1.
- and 8 more in the checked file.
