# Lead check: parcel 09F140000615103, run fulton-2026-11-13-screens

**In one line (the launching session's reading, not checked):** Probably not land: a separate county record at this address carries 22.4 million dollars of improvements on a building footprint cut out of this parcel, and a manufacturer is registered here. Confirm on the county record, then drop it.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-13T17:46:42Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `unknown`.**

**Plans: `supports`.**
- Union City's 2020 comprehensive plan update (as submitted for ARC review, January 2021) applies its District-Industrial character area close to I-85, US-29, South Fulton Parkway and rail lines. This parcel is in an industrial park about 0.8 mi from I-85 exit 64 (brief), but the p (cut) (plans-1)
  - On the page: "`Application: D-I in close proximity to I-85, US-29, South Fulton Parkway and rail lines for the purpose of convenient transportation access.`" (<http://documents.atlantaregional.com/Land%20Use/Reviews/ID2086/2021.01.26.CityofUnionCity.CompPlanUpdate_Reduced.pdf>, PDF page 74)
  - Around it the page reads: ...as industrial, commercial and service uses. (the quote) green space formal landscaping and appropr...
- and 1 more kept claims in the checked file.

**Utilities: `not_shown`.**
- Sewer, service area: Fulton County's water/sewer service area layer (WR Public Map Viewer layer 16), asked at the brief's map point for this parcel (-84.520819, 33.572517), answers Fulton County, sewer, service available. (utilities-1)
  - On the page: "`"Name":"Fulton County","ServiceTyp":"Sewer","ServiceAva":"Yes"`" (<https://gismaps.fultoncountyga.gov/arcgispub/rest/services/WaterResources/WR_Public_Map_Viewer/MapServer/16/query?where=1%3D1&geometry=-84.520819%2C33.572517&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...star':73.23144531,'shape stle':769.14763871, (the quote) ,'shape. starea()':10173715022.83869,'shape.  (cut)...
- and 2 more kept claims in the checked file.

**Market: `no_sign_found`.**
- ALREADY BUILT: asked by the site address, Fulton County's parcel layer returns a second record at 6715 Oakley Industrial Blvd, 09F140000615111, held by an abbreviated form of the same owner name, with no land, $22,407,900 of improvements, class I1, and a shape of about 156,000 sq (cut) (market-1)
  - On the page: "`"ImprAppr":22407900,"LUCode":"405","ClassCode":"I1","ExCode":null,"LivUnits":0,"LandAcres":null,"NbrHood":"C810","Subdiv":null,"SubdivNum":null,"SubdivLot":null,"SubdivBlck":null,"FeatureID":"09F140000615111","Shape.STArea()":156036.69247538838`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=Address%3D%276715%20OAKLEY%20INDUSTRIAL%20BLVD%27&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...ess':8963160,'totappr':22407900,'landappr':0, (the quote) ,'shape. stlength()':1551.5119393173418...
- and 2 more kept claims in the checked file.

**Owner: `developer_or_investor`.**
- The county parcel record (asked by site address) gives the land owner as a building-numbered LLC mailing from New York; the improvements record carries an abbreviated form of the same name. The registered manufacturer at the address is a different company (market section), so the (cut) (owner-1)
  - On the page: "`"Owner":"B9 MALBEC OAKLEY BUILDING 3 LLC ","OwnerAddr1":"345 PARK AVE","OwnerAddr2":"NEW YORK NY 10154","TaxDist":"50"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=Address%3D%276715%20OAKLEY%20INDUSTRIAL%20BLVD%27&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...sdir':null,'addrunttyp':null,'addrunit':null, (the quote) ,'totassess':412280,'landassess':332840,'impr...
  - Some of these words are also in the request (oakley): check the page did not just show the request back.

## Skeptic

**Skeptic: `killed`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: This is not land: it is the site of an operating roughly 156,000 sq ft manufacturing building. Fulton County's parcel map draws the lead parcel as an outer boundary with an inner ring cut out of it, and that inner ring is, point for point, the polygon of the separate improvements-only record at the same address (no land, $22.4 million of improvements, class I1). The 13.9 ac therefore surround the building, and the open ground the screen saw is the plant's yard, parking and truck court, which is why no envelope fitted. A pharmaceutical manufacturer is registered at the address through 2027 (first-pass market-2), and the 2021 sale at $16.7 million is a building price, not a land price.

It disputes these first-pass claims: plans-1, plans-2.

- Asked by parcel id (09F140000615103 and 09F140000615111, outSR=4326), Fulton County's parcel layer returns the lead parcel's polygon with two rings: an outer boundary and, after it, an inner ring (a hole). The quote runs from the last point of the outer ring into the first points (cut) (skeptic-1)
  - On the page: "`[-84.519940943419144,33.574427331212199]],[[-84.521760204507473,33.572805926389179],[-84.521767700906281,33.571862065899687],[-84.520536461854277,33.571840839256829],[-84.520518412887512,33.573005109771209]`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=FeatureID+IN+%28%2709F140000615103%27%2C%2709F140000615111%27%29&outFields=*&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ..., -84.519941115918016,33.574487658335116 , (the quote) , -84.521493175070844,33.573019019171028 , -8...
- In the same answer, the improvements-only record at 6715 Oakley Industrial Blvd has one ring made of the same five corner points as the hole in the lead parcel (listed in a different order). Computed by the skeptic from these coordinates: the hole is about 156,000 sq ft, the oute (cut) (skeptic-2)
  - On the page: "`"rings":[[[-84.520518412887512,33.573005109771209],[-84.520536461854277,33.571840839256829],[-84.521767700906281,33.571862065899687],[-84.521760204507473,33.572805926389179],[-84.521493175070844,33.573019019171028]`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=FeatureID+IN+%28%2709F140000615103%27%2C%2709F140000615111%27%29&outFields=*&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ...stlength()':1551.5119393173418 ,'geometry': (the quote) , -84.520518412887512,33.573005109771209...
- and 1 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 12 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: zoning.

## Questions for a person (the agents' own, not checked)

- (skeptic) The brief's map point (-84.520819, 33.572517) falls inside the hole, that is, on the building record and not on the lead parcel's land (skeptic's own computation from the coordinates above). The first pass's utility queries (utilities-1, utilities-2) were asked at that point. Tha (cut)
- (zoning) Union City's own site (unioncityga. gov) answered 403 to earlier investigators even for robots. txt, its zoning layer is on ArcGIS Online and its code is on Municode (known walls), so neither the M-1 designation nor the M-1 use table could be quoted. Not re-tried here.
- (plans) Confirm on Union City's adopted Future Development Map which character area covers this parcel (the ARC copy was not read at parcel scale; the word Oakley does not appear in its text).
- (utilities) The site already carries an operating manufacturing building (market section), so water and sewer are very likely connected; the verdict only says no map read here shows the lines. Ask Union City utilities and Fulton County Public Works for the service lines and capacity.
- (market) No sale, contract or developer sign was found, and none was expected: this is an improved, occupied industrial property, not a land lead. A person should treat it as screened out (already built on) rather than as a land opportunity.
- (owner) Georgia Secretary of State record not read (known wall: 403 and a search form; the owner name may not be put into an outside service). Status, formation date, principal office and registered agent are unknown.
- (skeptic) plans-1 and plans-2 describe what the District-Industrial character area is for, in general. Neither quote places this parcel in that area, so they do not carry the 'supports' verdict at parcel scale. The question is moot for a built site.
- (zoning) The parcel is already built on (market section): ask Union City planning what use and conditions the existing roughly 156,000 sq ft building was approved under, and whether any conditions of zoning or a site plan approval bind the land around it.
- and 11 more in the checked file.
