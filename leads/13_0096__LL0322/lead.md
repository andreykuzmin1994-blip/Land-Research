# Lead check: parcel 13 0096 LL0322, run fulton-2026-11-12

**In one line (the launching session's reading, not checked):** Probably part of the quarry owner's buffer strip between the Red Oak quarry and the homes on Buffington Rd; get the mining permit boundary and the deed before anything else.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-12T19:45:14Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `unknown`.**
- The county tax parcel record for this parcel (queried by parcel id) shows 7.6 acres of land with no improvement value, land-use code 100 and class code R4. These are assessor codes, not zoning: no quotable source confirmed the M-1 zoning the screener read from Union City's layer. (zoning-1)
  - On the page: "`"TotAppr":74700,"LandAppr":74700,"ImprAppr":0,"LUCode":"100","ClassCode":"R4","ExCode":null,"LivUnits":0,"LandAcres":7.6`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2713%200096%20%20LL0322%27&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...ess':29880,'landassess':29880,'imprassess':0, (the quote) ,'nbrhood':'9600','subdiv':null,'subdivnum':n...
- and 2 more kept claims in the checked file.

**Plans: `unknown`.**

**Utilities: `not_shown`.**
- Sewer, nearby: Fulton County's public water resources map returns two 8-inch PVC gravity sewer segments (Morning Creek basin, Fulton County service area) in a box about 300 ft around this parcel. From the returned geometry the nearer one is about 190 ft from the parcel line (my c (cut) (utilities-1)
  - On the page: "`"PipeSize":8,"Private":"0","ServArea":"FC","CITY_NAME":"FULTON COUNTY","LengthInFeet":108.33467164`" (<https://gismaps.fultoncountyga.gov/arcgispub/rest/services/WaterResources/WR_Public_Map_Viewer/MapServer/4/query?geometry=2194924,1311341,2196306,1312414&geometryType=esriGeometryEnvelope&inSR=2240&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=true&outSR=2240&f=json>)
  - Around it the page reads: ...atedate':null,'flowmeter':'mc51','enabled':1, (the quote) ,'shape. stlength()':108.33454283612528 ,'geo (cut)...
- and 2 more kept claims in the checked file.

**Market: `unknown`.**

**Owner: `unknown`.**
- The county tax parcel record (queried by parcel id) gives the owner as a real-estate investment company with a P. O. box mailing address in Fort Wayne, Indiana. (owner-1)
  - On the page: "`"Owner":"MARTIN MARIETTA MATERIALS REAL ESTATE INVESTMENTS INC","OwnerAddr1":"P.O. BOX 8040","OwnerAddr2":"FORT WAYNE IN 46898-8040","TaxDist":"50"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2713%200096%20%20LL0322%27&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...sdir':null,'addrunttyp':null,'addrunit':null, (the quote) ,'totassess':29880,'landassess':29880,'impras...
- and 3 more kept claims in the checked file.

## Skeptic

**Skeptic: `weakened`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: This is not a stray lot. It belongs to the real-estate arm of the company that runs the Red Oak granite quarry (EPD permit 085-16, 288 acres), and quarry-related land runs unbroken from its east line to the quarry. Next to it on the east is 12.46 ac held by the quarry landowner's family company. Beyond that is 39.6 ac of the same family's industrial-class land, and then the parcel that holds EPD's listed quarry point. Together with the owner's two residential-class parcels to the north and south, it forms a strip of about 25 acres between the quarry land and the homes and church on Buffington Rd. That is the shape of a buffer or reserve. A 2019 Corps report, known here only from a news account, described a 50-year pit expansion, a plant move and overburden stored nearby. I found no document I could reach that shows the permit bo (cut)

- The parcel holding the quarry's listed point: Georgia EPD lists the Red Oak Quarry point at 33.6053, -84.4894 (claim owner-2). In this county parcel query (a box around the quarry and this lead, by coordinates only), that point falls inside parcel 13 0095 LL0109 at 5414 Rock Quar (cut) (skeptic-1)
  - On the page: "`"ParcelID":"13 0095 LL0109","Address":"5414 ROCK QUARRY RD","Owner":"THAMES FAMILY INVESTMENTS LLC ","LUCode":"398","ClassCode":"I5","LandAcres":17.15`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?geometry=-84.5000,33.5960,-84.4800,33.6150&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=ParcelID,Address,Owner,LUCode,ClassCode,LandAcres,TotAppr,ImprAppr&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ..., 'attributes': (the quote) ,'totappr':310600,'imprappr':134600 ,'geometr...
  - Some of these words are also in the request (parcelid, address, owner, lucode, classcode, landacres): check the page did not just show the request back.
- The land between: parcel 13 0095 LL0091, 0 Rock Quarry Rd, 39.6 ac, industrial class I5, is held by the same family company as the quarry-point parcel. From this page's geometry (my computation), it runs from about 360 ft east of this lead to the west line of the quarry-point par (cut) (skeptic-2)
  - On the page: "`"ParcelID":"13 0095 LL0091","Address":"0 ROCK QUARRY RD","Owner":"THAMES FAMILY INVESTMENTS LLC ","LUCode":"400","ClassCode":"I5","LandAcres":39.6`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?geometry=-84.5000,33.5960,-84.4800,33.6150&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=ParcelID,Address,Owner,LUCode,ClassCode,LandAcres,TotAppr,ImprAppr&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ..., 'attributes': (the quote) ,'totappr':148000,'imprappr':0 ,'geometry': '...
  - Some of these words are also in the request (parcelid, address, owner, lucode, classcode, landacres): check the page did not just show the request back.
- and 1 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 13 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: zoning, plans, market, owner.

## Questions for a person (the agents' own, not checked)

- (skeptic) Settle the permit boundary first. The US Army Corps of Engineers Savannah District host (address removed) answers 403 even for robots. txt, so I did not fetch it, and its public notices could not be read. The national Regulatory Request System (rrs. usace. army. mil) is JavaScrip (cut)
- (zoning) Union City's own site (unioncityga. gov) answers 403 even for robots. txt and its zoning layer is on ArcGIS Online, so neither the M-1 designation nor the M-1 use table could be quoted. Ask Union City planning whether warehouse/distribution is permitted by right in M-1 and whethe (cut)
- (plans) Union City's comprehensive plan, future development map and planning agendas are hosted on unioncityga. gov, which answers 403; none could be read. Ask the city for the future development map designation of this parcel and for any rezoning, variance or plat case on it or beside i (cut)
- (utilities) Find out who supplies water at this address (Union City or the City of Atlanta are candidates; not checked) and where the nearest main is.
- (market) No public notice, surplus list, development-authority release or third-party rezoning naming this parcel was found; Union City's agendas (the likeliest place for a rezoning by a buyer) could not be read (403). Ask the city whether any application has been filed on 13 0096 LL0322  (cut)
- (owner) Georgia Secretary of State records answer 403, so status, formation date, principal office and registered agent of the owner entity are unknown. The name suggests a real-estate holding arm of the aggregates company that runs the neighbouring quarry, but that link is not documente (cut)
- (skeptic) My computation, not on any page: EPD lists 288 permitted acres. In the county parcel query (claim 1's address), the industrial-class parcels held by the quarry landowner's family companies and by the lead's owner add up to about 221 ac within roughly 0.6 mile of the quarry point. (cut)
- (zoning) The assessor class R4 and land-use code 100 on an M-1 parcel: ask the Fulton assessor what they mean here (assessor classes are not zoning, but a residential class can signal a mapped or historic residential designation).
- and 17 more in the checked file.
