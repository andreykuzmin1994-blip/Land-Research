# Lead check: parcel 07 400001630455, run fulton-2026-11-13-screens

**In one line (the launching session's reading, not checked):** The city layer puts it in a residential plan category that allows no industrial zoning (confirm on the adopted map), the plan schedules truck calming on Gullatt Rd at Johnson Rd, and the city owns a lot nearby for a park; a warehouse rezoning looks unlikely. Watch the planned Gullatt Rd interchange.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-13T22:11:13Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `rezoning_needed`.**
- Fairburn's comprehensive plan (adopted October 2025) shows its zoning districts with AG (Agricultural) separate from the two industrial districts, M-1 Light Industrial and M-2 Heavy Industrial. The brief gives this parcel's zoning as AG (screener's city layer, not re-read here),  (cut) (zoning-1)
  - On the page: "`O&I - Office & Institutional M-1 - Light Industrial M-2 - Heavy Industrial`" (<https://www.fairburn.com/DocumentCenter/View/2932/Final-Approved-Comp-Plan>, PDF page 39)
  - Around it the page reads: ...highway commercial dtmu - downtown mixed-use (the quote) n miles 0 0.5 1 2 p and o - parks and open sp (cut)...
- and 1 more kept claims in the checked file.

**Plans: `against`.**
- The brief places this parcel in Fairburn's Rural Residential future land use category (screener's city layer, on an ArcGIS Online host that is a known wall; the plan's own map was not read at parcel scale). In the plan adopted in 2025, Rural Residential lists conservation, parks, (cut) (plans-1)
  - On the page: "`Rural Residential Details Appropriate Development Types Conservation; parks and recreation; public/ institutional uses (schools, places of worship, and civic facilities); large-lot single-family detached houses; conservation subdivisions; small retail; agriculture; accessory dwelling units (ADUs). Density/Intensity 0 to 1 dwelling units/acre Appropriate Zoning District(s) AG, R-1, C-1, PD, P&O`" (<https://www.fairburn.com/DocumentCenter/View/2932/Final-Approved-Comp-Plan>, PDF page 128)
  - Around it the page reads: ...development typical character intensity (the quote) 126 city of fairburn comprehensive plan (pag...
- and 12 more kept claims in the checked file.

**Utilities: `not_shown`.**
- Sewer, near the parcel: Fulton County's Waste Water Pipe layer (WR Public Map Viewer layer 4), asked for pipes within 300 ft of this parcel's own polygon, returns 6 active 8-inch gravity sewer segments (Line Creek basin, county service area, city Fairburn). Which side of the parc (cut) (utilities-1)
  - On the page: "`"Enabled":1,"PipeSize":8,"Private":"0","ServArea":"FC","CITY_NAME":"FAIRBURN"`" (<https://gismaps.fultoncountyga.gov/arcgispub/rest/services/WaterResources/WR_Public_Map_Viewer/MapServer/4/query?geometry=%7B%22rings%22%3A%5B%5B%5B-84.608571%2C33.516467%5D%2C%5B-84.610059%2C33.515649%5D%2C%5B-84.611515%2C33.515664%5D%2C%5B-84.611496%2C33.515965%5D%2C%5B-84.611484%2C33.516267%5D%2C%5B-84.611478%2C33.516568%5D%2C%5B-84.611479%2C33.51687%5D%2C%5B-84.611502%2C33.518179%5D%2C%5B-84.61151%2C33.518386%5D%2C%5B-84.611524%2C33.518592%5D%2C%5B-84.611545%2C33.518798%5D%2C%5B-84.611572%2C33.519003%5D%2C%5B-84.611606%2C33.519208%5D%2C%5B-84.611646%2C33.519412%5D%2C%5B-84.611693%2C33.519617%5D%2C%5B-84.611746%2C33.519821%5D%2C%5B-84.611806%2C33.520023%5D%2C%5B-84.611911%2C33.520357%5D%2C%5B-84.611647%2C33.52036%5D%2C%5B-84.609408%2C33.520394%5D%2C%5B-84.609389%2C33.519505%5D%2C%5B-84.606649%2C33.519452%5D%2C%5B-84.606649%2C33.518922%5D%2C%5B-84.606649%2C33.5189%5D%2C%5B-84.606647%2C33.518111%5D%2C%5B-84.60665%2C33.518108%5D%2C%5B-84.606651%2C33.516663%5D%2C%5B-84.606692%2C33.516659%5D%2C%5B-84.608571%2C33.516467%5D%5D%5D%2C%22spatialReference%22%3A%7B%22wkid%22%3A4326%7D%7D&geometryType=esriGeometryPolygon&inSR=4326&spatialRel=esriSpatialRelIntersects&distance=300&units=esriSRUnit_Foot&outFields=%2A&returnGeometry=false&f=json>)
  - Around it the page reads: ...dby':null,'createdate':null,'flowmeter':null, (the quote) ,'lengthinfeet':117.46144143,'shape. stlength (cut)...
- and 3 more kept claims in the checked file.

**Market: `unknown`.**

**Owner: `holding_entity`.**
- Fulton County's parcel record, asked by parcel id, gives an LLC owner with a farm name and a Pennsylvania mailing address; the brief shows the last recorded sale as a 2004 executor's deed. With no Secretary of State record read, 'holding entity' (a family or estate vehicle for in (cut) (owner-1)
  - On the page: "`"Owner":"TARENE FARM LLC ","OwnerAddr1":"380 LANBURG LN","OwnerAddr2":"AMBLER PA 19002"`" (<https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2707%20400001630455%27&outFields=*&returnGeometry=true&outSR=4326&f=json>)
  - Around it the page reads: ...sdir':null,'addrunttyp':null,'addrunit':null, (the quote) ,'taxdist':'25a','totassess':102460,'landasse...
- and 1 more kept claims in the checked file.

## Skeptic

**Skeptic: `weakened`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: Beyond the Rural Residential plan category the first pass found, the same adopted 2025 plan works against trucks at this exact corner: its 2026-2030 work program item T.47 (Gullatt Road, 2027-2028) is a mini-roundabout at Johnson Road with No Through Truck Traffic signage, and its policy text says the Gullatt Road calming is meant to discourage truck intrusion. Johnson and Gullatt are the parcel's two frontage roads, the plan's industrial policy asks for direct truck-route access, and the parcel adjoins homesteaded houses and a planned city park. The parcel also appears to drain to the Line Creek small water supply watershed (the county sewer pipes beside it are in the Line Creek basin), where the state Part 5 criteria carry a 25% impervious limit and stream buffers that Fairburn's code follows. Together these make an industrial (cut)

- Fairburn's adopted 2025 plan, Community Work Program item T.47 (PDF page 216), schedules signage and traffic calming along Gullatt Road, one of the parcel's two frontage roads, for 2027-2028. (skeptic-1)
  - On the page: "`Install signage and traffic calming along Gullatt Road.`" (<https://www.fairburn.com/DocumentCenter/View/2932/Final-Approved-Comp-Plan>, PDF page 216)
  - Around it the page reads: ...along rivertown road. x x public works t.47 (the quote) x x public works t.48 install signage and tr...
- The matching cost row on the next PDF page (217; the row is read across the page break, so check it against the printed table) describes that Gullatt Road project as a mini-roundabout at Johnson Road with No Through Truck Traffic signage: the corner of the parcel's two frontages. (cut) (skeptic-2)
  - On the page: "`Mini-roundabout at Johnson Road; “No Through Truck Traffic” signage; lighting upgrades`" (<https://www.fairburn.com/DocumentCenter/View/2932/Final-Approved-Comp-Plan>, PDF page 217)
  - Around it the page reads: ...$65,000 $105,000 general fund, ss4a, gtib (the quote) srts, gdot, community organizations $30,000...
- and 4 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 27 claims and dropped 1. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: market.

## Questions for a person (the agents' own, not checked)

- (skeptic) Ask Fairburn planning whether the T.47 mini-roundabout at Johnson Road is at the parcel's corner, its design vehicle, and whether No Through Truck Traffic signage would still let a warehouse's own trucks use Gullatt and Johnson Roads as a destination.
- (zoning) AG district use table not read: Fairburn's zoning text is on Municode (known wall). Ask Fairburn planning whether AG allows any warehouse, distribution or outdoor storage use, and what a rezoning to M-1 would require.
- (plans) Confirm on the adopted Future Development Map (the plan's map is an image) that this parcel is Rural Residential and not Industrial or Light Industrial; the brief's category comes from the city's ArcGIS Online layer (known wall).
- (utilities) Verdict is the weaker of the two: county gravity sewer (8-inch) runs within 300 ft of the parcel, but no published map read here shows a water main. Ask Fairburn utilities for the nearest water main and its size; 8-inch sewer and the plan's 'limit infrastructure expansion' policy (cut)
- (market) Search results only, not verifiable: web search summaries described an MLS listing (FMLS 7423469) for 6790 Johnson Road as 46.7 acres at the corner of Johnson and Gullatt Roads, marketed to builders and investors, asking $3,299,000 (down from $3,499,000, last change 2024-11-05),  (cut)
- (owner) Georgia Secretary of State record (status, formation date, registered agent, principal office) not read: the site answers 403 (known wall). Ask a person to pull it, and to check whether the LLC is registered in Georgia or only in Pennsylvania.
- (skeptic) Ask Fairburn whether its water supply watershed ordinances (City Code 80-111(g) and 80-111.1, on Municode, a known wall) apply a 25% impervious limit per site in the Line Creek watershed, and whether this parcel is inside that watershed and within 7 miles upstream of an intake (w (cut)
- (zoning) Search results only (not read; the listing page now redirects away): a 2024-2025 brokerage listing for this address said current zoning Agricultural with future zoning residential. Ask the city whether any rezoning application has ever been filed on this parcel.
- and 15 more in the checked file.
