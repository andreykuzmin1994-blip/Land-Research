# Lead check: parcel 14 013200110835, run fulton-2026-11-13-screens

**In one line (the launching session's reading, not checked):** Likely not a lead: the county lists 96 active addresses on the parcel's internal streets, which the agents read as a manufactured-home community (confirm by a drive-by), and MI in East Point appears to be the Medical Institutional district, not industrial. Confirm with East Point, then drop it.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-13T19:22:37Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `unknown`.**
- The screener read this parcel's East Point code MI as industrial (by right). In East Point, MI is the Medical Institutional district of Sec. 10-2071, not an industrial district: a March 2025 Planning and Zoning Commission notice lists a city-initiated text amendment of Sec. 10-20 (cut) (zoning-1)
  - On the page: "`Text Amendment of Sec. 10-2071 Medical Institutional Zoning district classification to streamline the scope and intent of the Medical Institutional zoning district to provide for amended permitted uses and uses permitted through use permit only.`" (<https://eastpointga.gov/wp-content/uploads/2025/03/March-PZC-PH-Meeting-Public-Notice-and-Agenda_03.20.2025.pdf>, PDF page 3)
  - Around it the page reads: ...initiated text amendment city of east point (the quote) text amendment city wide city of east point...
- and 4 more kept claims in the checked file.

**Plans: `against`.**
- The adopted comprehensive plan's key points from public engagement say warehouse/logistics/light industrial belongs only in the business park area south of Camp Creek Parkway and west of I-285. By my reading of the coordinates (33.677, -84.429, next to the hospital on Cleveland A (cut) (plans-1)
  - On the page: "`Warehouse/logistics/light industrial is only appropriate in business park area south of Camp Creek Parkway and west of I-285`" (<https://eastpointga.gov/wp-content/uploads/2025/06/EastPointPlanDraft5_FinalForAdoption_2023.07.17_Copy_rv.pdf>, PDF page 26)
  - Around it the page reads: ...and future redevelopment transportation (the quote) improve roadway conditions/maintenance in...
- and 3 more kept claims in the checked file.

**Utilities: `unknown`.**
- East Point runs its own water and sewer system (164+ miles of water mains, 153+ miles of sewer mains), but its web page shows no map, so whether mains run at or next to this parcel is not shown here. (utilities-1)
  - On the page: "`The water distribution system is comprised of a main line that runs 164+ miles.`" (<https://eastpointga.gov/water-sewer/>)
  - Around it the page reads: ...replacement construction begins jan 20, 2026 (the quote) the main lines range in size from 2' - 16'....
- and 1 more kept claims in the checked file.

**Market: `no_sign_found`.**
- Already in use: the county address-point layer, queried by this parcel id, returns 96 active main addresses on the parcel, on internal streets (Long St, 1st, 2nd, 3rd, 4th, 5th, 6th, 7th St and Veterans St), field data dated 11/16/2017. That is the pattern of an occupied manufact (cut) (market-1)
  - On the page: "`{"Label":"34 Long St","City":"East Point","Status":"Active","PtType":"Main Address","Comment":"Field Data 11/16/2017"}`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/10/query?where=ParcelID%3D%2714%20013200110835%27&outFields=Label,City,Status,PtType,Comment&returnGeometry=false&f=json>)
  - Around it the page reads: ...data 11/16/2017' , 'attributes': (the quote) , 'attributes': 'label':'3 2nd st','city':'e...
  - Some of these words are also in the request (label, city, status, pttype, comment): check the page did not just show the request back.
- and 2 more kept claims in the checked file.

**Owner: `unknown`.**
- The county record (queried by parcel id) shows an LLC owner with a Texas mailing address; the Secretary of State record was not read (known wall), so status, formation and agent are unknown. The 96 active addresses on the parcel suggest the owner operates the community, but that  (cut) (owner-1)
  - On the page: "`"OwnerAddr1":"330 RAYFORD RD #312","OwnerAddr2":"SPRING TX 77386"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2714%20013200110835%27&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...point mhc llc ', (the quote) ,'taxdist':'20y','totassess':300000,'landasse...

## Skeptic

**Skeptic: `killed`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: This is not vacant industrial land. The county address-point layer, queried by this parcel id, returns 96 active main addresses on the parcel, 2848 Veterans St itself classed Residential - Single Structure: an occupied manufactured-home community, so buying it means closing a lived-in community and relocating residents. The screener's MI code is East Point's M-I Medical Institutional district, not industrial, and the city has since approved a use permit for 138 assisted senior living units in M-I on Norman Berry Drive parcels that lie within 1,500 ft of this site. East Point also treats a warehouse within 5,280 ft of residential property as needing a variance even in its I-1 Light Industrial district, and this parcel is surrounded by homes. The by right entitlement and grade B rest on a misread zoning code.

It disputes these first-pass claims: zoning-5, plans-2, plans-3.

- The county address-point layer, queried by this parcel id (all fields), returns 96 active main-address points on the parcel; the point for 2848 Veterans St itself is classed Residential - Single Structure. The parcel is in residential use, not vacant land. (skeptic-1)
  - On the page: "`"Label":"2848 Veterans St","UnitLow":null,"UnitHigh":null,"UnitActual":null,"City":"East Point","State":"GA","Zip":"30344","Zip4":null,"PtType":"Main Address","PtClass":"Residential - Single Structure"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/10/query?where=ParcelID%3D%2714%20013200110835%27&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...type':'street','postdir':null,'postmod':null, (the quote) ,'subdivision':null,'status':'active','common...
- East Point's code M-I is the Medical Institutional zoning district, not an industrial one; the city's September 18, 2025 Planning and Zoning Commission agenda lists a use permit for 138 units of assisted senior living in M-I at 0 Norman Berry Drive (PINs 14 012500111113, 14 0125L (cut) (skeptic-2)
  - On the page: "`The applicant is requesting a Use permit to develop 138 units of assisted senior living in the M-I Medical Institutional zoning district.`" (<https://eastpointga.gov/wp-content/uploads/2025/09/Agenda-PZ-Public-Hearing-SEPT-18-2025.pdf>, PDF page 1)
  - Around it the page reads: ...14 0125ll0200, 14 012500100793) (the quote) use permit 1318 eubanks avenue pin: 14 01300...
- and 2 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 19 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: zoning, utilities, owner.

## Questions for a person (the agents' own, not checked)

- (skeptic) Ask East Point Planning for a zoning verification letter: confirm the parcel is M-I, whether any warehouse or light-industrial use is allowed in M-I at all, and whether the October 2024 MI moratorium and the deferred Sec. 10-2071 amendment (reported in third-party summaries, not  (cut)
- (zoning) East Point's zoning text is on Municode (known wall) and the city's zoning layer is on an ArcGIS Online host (known wall); a third-party code copy (eastpoint-ga. elaws. us) timed out at robots. txt. Ask East Point Planning and Community Development for a zoning verification lette (cut)
- (plans) Confirm the parcel's character area on the Future Development Map (likely Regional Center around South Fulton Medical Center, not checked).
- (utilities) Ask East Point Water and Sewer for the nearest water main and sewer line and their capacity. A third-party park listing seen only as a search snippet (not fetched, not verified) said the community's water comes from a well and sewer is city-provided; worth checking.
- (market) Search results (third-party listings, not fetched, not primary) describe 2848 Veterans St as an operating all-ages manufactured-home land-lease community with on-site management and homes listed for sale or rent in it. Drive by to confirm occupancy; buying it would mean closing a (cut)
- (owner) Georgia Secretary of State business search is a known wall; a person should pull the entity record (status, formed, registered agent) and check whether it is registered in Texas as well.
- (skeptic) Ask whether Sec. 10-2150.6(a)(1) (5,280 ft from residential) applies outside I-1/I-2; a third-party news summary (not verified) says it applies to all new industrial uses.
- (zoning) Search-result summaries (third-party, not fetched, not verified) say East Point placed a moratorium on MI applications in October 2024, that council approved a moratorium in December 2023 on reuse of the former Atlanta Medical Center South hospital property next door, and that a  (cut)
- and 7 more in the checked file.
