# Lead check: parcel 14 006800060376, run fulton-2026-11-13-screens

**In one line (the launching session's reading, not checked):** Zoned and planned industrial with no sign of a deal, but the screen fit no building and homes face its frontage. Ask Atlanta zoning about residential buffers and the Zoning 2.0 remap, and ask the city or GDOT whether trucks can enter from Mount Zion Rd or only from Waters Rd.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-13T17:47:31Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `unknown`.**
- City of Atlanta's own zoning district layer, queried at a point inside this parcel (33.674261, -84.39638), returns district I-1, and the layer itself links I-1 to the Light Industrial District Regulations chapter of the city code. The district text (on Municode) could not be read (cut) (zoning-1)
  - On the page: "`"ZONECLASS":"I-1","ZONEDESC":"https://library.municode.com/ga/atlanta/codes/code_of_ordinances?nodeId=PTIIICOORANDECO_PT16ZO_CH16LIINDIRE"`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/22/query?geometry=-84.39638%2C33.674261&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...,'features': 'attributes': 'objectid':10717, (the quote) ,'baseelev':0.0,'height':0.0,'lastupdate':nul...
- and 4 more kept claims in the checked file.

**Plans: `supports`.**
- The city's Development Patterns / Future Land Use layer, queried at a point inside the parcel, returns land use code I, Industrial. (plans-1)
  - On the page: "`"LANDUSECOD":"I","LANDUSEDES":"Industrial","LASTUPDATE":null`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/19/query?geometry=-84.39638%2C33.674261&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...only','ndp':'i','fid l8land':2040, (the quote) ,'lasteditor':' ','density':' ','far':' ','hi...
- and 4 more kept claims in the checked file.

**Utilities: `unknown`.**

**Market: `no_sign_found`.**
- The county parcel record (queried by parcel id) shows land value only and zero improvement value: vacant land, not an operating or built use, assessed at $293,600. (market-1)
  - On the page: "`"TotAssess":117440,"LandAssess":117440,"ImprAssess":0,"TotAppr":293600,"LandAppr":293600,"ImprAppr":0,"LUCode":"300","ClassCode":"I4"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2714%20006800060376%27&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...ga 30342','taxdist':'05', (the quote) ,'excode':null,'livunits':0,'landacres':9.38,...
- and 4 more kept claims in the checked file.

**Owner: `holding_entity`.**
- The county record for this parcel (queried by parcel id) gives the owner as an LLC mailing to an office suite on Roswell Rd in Atlanta (30342). (owner-1)
  - On the page: "`"Owner":"ZION SEVENTY FIVE PROPERTY LLC ","OwnerAddr1":"3717 ROSWELL RD STE 100","OwnerAddr2":"ATLANTA GA 30342"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2714%20006800060376%27&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...sdir':'sw','addrunttyp':null,'addrunit':null, (the quote) ,'taxdist':'05','totassess':117440,'landasses...
- and 2 more kept claims in the checked file.

## Skeptic

**Skeptic: `weakened`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: Nothing found shows a building, a developer or a sale on this parcel, and the city's own maps still show plain I-1 and Industrial future land use, so the lead is not killed. But it is a narrow wedge of about 8.7 acres by the city's parcel shape (the county says 9.38), with no frontage on anything but two 24 ft, two-lane collectors. Its long east side faces Waters Rd SW, and across that road are occupied single-family homes in residential zoning. The west side is an 830 ft curved edge with no parcel beside it, which by my reading is the I-75 right-of-way (no access there). Truck access means curb cuts across from houses on a residential street, on a site where the screener already found no envelope for the smallest building prototype. Atlanta's code rewrite (Draft V2, being finalised as Draft V3 for Council) is still pending, and (cut)

It disputes these first-pass claims: owner-1.

- The city's street centreline layer, queried within 80 ft of the parcel's own polygon (taken from the city's tax parcel layer), returns only Waters Rd SW and Mount Zion Rd SW. The Waters Rd SW segment that runs along the parcel's east side (address range 2894-3009) is a two-lane,  (cut) (skeptic-1)
  - On the page: "`"FUNCCLASS":"Collector","SYMBOLCLASS":null,"SPEEDLIMIT":"35","SURFACE":null,"DIRTRAVEL":"Both","CARDDIRTRVL":null,"WIDTH":"24","LANES":"2"`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/33/query?geometry=%7B%22rings%22%3A%5B%5B%5B-84.39526%2C33.674849%5D%2C%5B-84.395489%2C33.674229%5D%2C%5B-84.3955%2C33.674198%5D%2C%5B-84.395887%2C33.673117%5D%2C%5B-84.397184%2C33.673132%5D%2C%5B-84.397178%2C33.673199%5D%2C%5B-84.397173%2C33.673266%5D%2C%5B-84.397169%2C33.673332%5D%2C%5B-84.397164%2C33.673399%5D%2C%5B-84.39716%2C33.673465%5D%2C%5B-84.397156%2C33.673532%5D%2C%5B-84.397152%2C33.673599%5D%2C%5B-84.397149%2C33.673666%5D%2C%5B-84.397146%2C33.673732%5D%2C%5B-84.397144%2C33.673799%5D%2C%5B-84.397141%2C33.673866%5D%2C%5B-84.397139%2C33.673932%5D%2C%5B-84.397137%2C33.673999%5D%2C%5B-84.397136%2C33.674066%5D%2C%5B-84.397134%2C33.674133%5D%2C%5B-84.397133%2C33.674199%5D%2C%5B-84.397133%2C33.674266%5D%2C%5B-84.397132%2C33.674333%5D%2C%5B-84.397132%2C33.6744%5D%2C%5B-84.397132%2C33.674466%5D%2C%5B-84.397133%2C33.674533%5D%2C%5B-84.397134%2C33.6746%5D%2C%5B-84.397135%2C33.674667%5D%2C%5B-84.397136%2C33.674733%5D%2C%5B-84.397138%2C33.6748%5D%2C%5B-84.39714%2C33.674867%5D%2C%5B-84.397142%2C33.674934%5D%2C%5B-84.397144%2C33.675%5D%2C%5B-84.397147%2C33.675067%5D%2C%5B-84.39715%2C33.675134%5D%2C%5B-84.397154%2C33.6752%5D%2C%5B-84.397157%2C33.675267%5D%2C%5B-84.397161%2C33.675334%5D%2C%5B-84.397165%2C33.6754%5D%2C%5B-84.395944%2C33.675386%5D%2C%5B-84.395941%2C33.675142%5D%2C%5B-84.395798%2C33.675044%5D%2C%5B-84.395807%2C33.674903%5D%2C%5B-84.39526%2C33.674849%5D%5D%5D%2C%22spatialReference%22%3A%7B%22wkid%22%3A4326%7D%7D&geometryType=esriGeometryPolygon&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=%2A&returnGeometry=true&outSR=4326&distance=80&units=esriSRUnit_Foot&f=json>)
  - Around it the page reads: ...tlanta','zipleft':'30354','zipright':'30354', (the quote) ,'workzone':null,'subdiv':null,'insthwynum':n...
- The same street query gives that segment's label and address range: Waters Rd SW, 2894-3009, the block where the homes across the road from the parcel sit (for example 2960 Waters Rd SW). (skeptic-2)
  - On the page: "`"LABEL":"Waters Rd SW","FROMLEFT":2894,"TOLEFT":3008,"FROMRIGHT":2895,"TORIGHT":3009`" (<https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/33/query?geometry=%7B%22rings%22%3A%5B%5B%5B-84.39526%2C33.674849%5D%2C%5B-84.395489%2C33.674229%5D%2C%5B-84.3955%2C33.674198%5D%2C%5B-84.395887%2C33.673117%5D%2C%5B-84.397184%2C33.673132%5D%2C%5B-84.397178%2C33.673199%5D%2C%5B-84.397173%2C33.673266%5D%2C%5B-84.397169%2C33.673332%5D%2C%5B-84.397164%2C33.673399%5D%2C%5B-84.39716%2C33.673465%5D%2C%5B-84.397156%2C33.673532%5D%2C%5B-84.397152%2C33.673599%5D%2C%5B-84.397149%2C33.673666%5D%2C%5B-84.397146%2C33.673732%5D%2C%5B-84.397144%2C33.673799%5D%2C%5B-84.397141%2C33.673866%5D%2C%5B-84.397139%2C33.673932%5D%2C%5B-84.397137%2C33.673999%5D%2C%5B-84.397136%2C33.674066%5D%2C%5B-84.397134%2C33.674133%5D%2C%5B-84.397133%2C33.674199%5D%2C%5B-84.397133%2C33.674266%5D%2C%5B-84.397132%2C33.674333%5D%2C%5B-84.397132%2C33.6744%5D%2C%5B-84.397132%2C33.674466%5D%2C%5B-84.397133%2C33.674533%5D%2C%5B-84.397134%2C33.6746%5D%2C%5B-84.397135%2C33.674667%5D%2C%5B-84.397136%2C33.674733%5D%2C%5B-84.397138%2C33.6748%5D%2C%5B-84.39714%2C33.674867%5D%2C%5B-84.397142%2C33.674934%5D%2C%5B-84.397144%2C33.675%5D%2C%5B-84.397147%2C33.675067%5D%2C%5B-84.39715%2C33.675134%5D%2C%5B-84.397154%2C33.6752%5D%2C%5B-84.397157%2C33.675267%5D%2C%5B-84.397161%2C33.675334%5D%2C%5B-84.397165%2C33.6754%5D%2C%5B-84.395944%2C33.675386%5D%2C%5B-84.395941%2C33.675142%5D%2C%5B-84.395798%2C33.675044%5D%2C%5B-84.395807%2C33.674903%5D%2C%5B-84.39526%2C33.674849%5D%5D%5D%2C%22spatialReference%22%3A%7B%22wkid%22%3A4326%7D%7D&geometryType=esriGeometryPolygon&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=%2A&returnGeometry=true&outSR=4326&distance=80&units=esriSRUnit_Foot&f=json>)
  - Around it the page reads: ...sttype':'road','postdir':null,'postmod':'sw', (the quote) ,'cityleft':'atlanta','cityright':'atlanta','...
- and 6 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 26 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: zoning, utilities.

## Questions for a person (the agents' own, not checked)

- (skeptic) My own measurement from the city's parcel shapes (approximate, local projection): the east side (about 650 ft) faces Waters Rd SW with houses about 45 ft away across the road; the west side is a curved edge of about 830 ft with no parcel within about 150-300 ft, consistent with I (cut)
- (zoning) Atlanta's zoning text is on Municode (known wall), and the city's main site (atlantaga. gov) answers robots. txt with 403; an attempt to read a third-party copy of the code (atlanta. elaws. us) timed out at robots. txt. Ask Atlanta Office of Zoning and Development for a zoning ve (cut)
- (plans) Seen, not verifiable: the Atlanta Zoning Committee agenda on atlantacityga. iqm2. com (search result) lists U-25-28 / 26-O-1054 for 200 Cleveland Ave SW; iqm2's robots. txt asks a 60 s crawl delay, so it was not fetched. Ask the city for the outcome of U-25-28 (a large truck/park (cut)
- (utilities) No published water or sewer map could be read: the Watershed Management GIS server (egis. atlantaga. gov) lists no public services in its MapWF folder and the WasteWater1c service named in search results answers 'not found'. Ask the City of Atlanta Department of Watershed Managem (cut)
- (market) Three web searches (by site address and road names, not owner) found no listing, news, development authority release or DRI for this parcel; listing sites were not visited and CoStar/LoopNet/Crexi were not used. Absence of a sign is not 'not for sale'.
- (owner) Georgia Secretary of State business search is a known wall: entity status, formation date, principal office and registered agent were not read. Pull the record by hand.
- (skeptic) The residential land that actually touches the parcel's north-east lot line (first pass zoning-3) is, by my overlay of the city's parcel shapes, the same LLC's own lots (2931 Waters Rd SW and 0 Waters Rd SW). Any I-1 transitional yard there falls on land the seller controls; the  (cut)
- (zoning) The parcel abuts R-4 single-family zoning and single-family homes (see market claims): ask what transitional yard, buffer, screening, height and truck-access restrictions apply on the residential sides; these could take a large share of a 9.4 ac site and may explain why the scree (cut)
- and 10 more in the checked file.
