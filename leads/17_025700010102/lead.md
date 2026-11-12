# Lead check: parcel 17 025700010102, run fulton-2026-11-12

**In one line (the launching session's reading, not checked):** An operating petroleum terminal, still on the IRS list of active fuel terminals in 2026; not available land.

Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. Words in double quotation marks were on the linked page when it was fetched (2026-11-12T19:47:45Z). Everything else is an agent's own reading, not checked.

## What the documents say

**Zoning: `unknown`.**
- The City of Atlanta zoning layer (LandUsePlanning MapServer layer 0), queried at the brief's map point for this parcel, returns an I-2 (heavy industrial) district polygon of about 490 acres, status Current. This shows the district only, not what the I-2 text permits. (zoning-1)
  - On the page: "`"ACRES":490.58788953,"ZONING":"I-2","EDITTED":" ","REFERENCE":"R2907"`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/0/query?geometry=-84.485238%2C33.804185&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...a9e984ba-3ce5-4486-b37f-6497ae5d3ee3 ', (the quote) ,'shape. area':21369922.988046501,'shape. len (cut)...
- and 1 more kept claims in the checked file.

**Plans: `supports`.**
- The City of Atlanta future land use layer (Development Patterns - Future Land Use, layer 8), queried at the parcel point, returns Industrial. (plans-1)
  - On the page: "`"LANDUSECOD":"I","LANDUSEDES":"Industrial"`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/8/query?geometry=-84.485238%2C33.804185&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...only','ndp':'i','fid l8land':3630, (the quote) ,'lastupdate':1517875200000,'lasteditor':'gis...
- and 2 more kept claims in the checked file.

**Utilities: `unknown`.**

**Market: `no_sign_found`.**
- No sign of a sale or developer: the city's Rezoning Cases layer, queried within 1,500 ft of the parcel point, shows no case on this parcel (only Z-16-074 on a nearby R-4/C-1 site, 2016-17). The parcel is instead in use as a petroleum terminal (see owner section). (market-1)
  - On the page: "`"FROM_ZONE":"R-4/C-1","TO_ZONE":"MRC-3-C","DOCKET_NO":"Z-16-074","STATUS":"COMPLETE"`" (<https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/10/query?geometry=-84.485238%2C33.804185&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&distance=1500&units=esriSRUnit_Foot&outFields=*&returnGeometry=false&f=json>)
  - Around it the page reads: ...,'features': 'attributes': 'objectid':3630, (the quote) ,'final upda':1508198400000,'status subtype':...
- and 3 more kept claims in the checked file.

**Owner: `operator`.**
- Georgia EPD's Title V review names the facility after the tax-roll owner, KM Phoenix Holdings - Chattahoochee Terminal, at 3132 Parrott Avenue N. W., and gives KM Phoenix Holdings LLC as the parent/holding company. (owner-1)
  - On the page: "`2. Parent/Holding Company Name KM Phoenix Holdings LLC`" (<https://epd.georgia.gov/document/document/tv549944-narrative/download>, PDF page 2)
  - Around it the page reads: ...km phoenix holdings - chattahoochee terminal (the quote) 3. previous and/or other name(s) this facili...
- and 3 more kept claims in the checked file.

## Skeptic

**Skeptic: `killed`** (backed by quotes of its own, below).

The skeptic's objection, in its own words: The first pass's central finding holds and is now current: the IRS list of active fuel terminals as of 9/30/2026 (a federal record published November 2026) still carries terminal T-58-GA-2511 at 3132 Parrott Avenue N. W., Atlanta, operated by Kinder Morgan Liquids Terminals LLC. The site is a working petroleum terminal in 2026, not available land, and no closure, decommissioning or sale record was found. It has been a fuel terminal since at least 1975 under several operators, so any future sale would bring a petroleum-contamination question that no public record read here answers.

It disputes these first-pass claims: zoning-2, owner-1.

- The IRS Terminal Control Numbers list (Active Fuel Terminals) has a row for terminal T-58-GA-2511 at 3132 Parrott Avenue N. W., Atlanta GA 30318, with the operator named as Kinder Morgan Liquids Terminals LLC (the operator name runs onto a second line in the table, so the row rea (cut) (skeptic-1)
  - On the page: "`T-58-GA-2511 Kinder Morgan Liquids 3132 Parrott Avenue Atlanta GA 30318`" (<https://www.irs.gov/pub/irs-sbse/tcn-db.pdf>, PDF page 18)
  - Around it the page reads: ...4127 winters chapel doraville ga 30360 road (the quote) terminals llc n. w. t-58-ga-2514 global compa...
  - Found only in the PDF's layout order, which can read across columns: open the page.
- The same IRS document dates itself as the list of active fuel terminals as of 9/30/2026, so the Parrott Avenue terminal was a registered, active terminal at the end of September 2026. (skeptic-2)
  - On the page: "`ACTIVE FUEL TERMINALS @9/30/2026`" (<https://www.irs.gov/pub/irs-sbse/tcn-db.pdf>, PDF page 33)
  - Around it the page reads: ...t-99-hi-4567 t-99-hi-4568 t-99-hi-4570 (the quote) tesoro hawaii corporation aloha petroleum lt...
- and 4 more kept claims in the checked file.

## What the check proves

`quotecheck` kept 19 claims and dropped 0. A kept claim proves only that its quote was on its page when fetched. It does not prove the page is current, or that the claim follows from the quote. Still `unknown`: zoning, utilities.

## Questions for a person (the agents' own, not checked)

- (skeptic) zoning-2 is disputed: its claim says the parcel is in the NW Atlanta Affordable WH overlay, but its own URL asks for features within 500 ft of the parcel point. A point query with no buffer and a query with the parcel polygon both return nothing. The overlay is beside the parcel, (cut)
- (zoning) The I-2 use table (Atlanta code sec. 16-17.003) was not read: Municode is a known wall, (address removed) answered 403 to robots. txt, atlantacityga. iqm2. com gave no reply to robots. txt, and the atlanta. elaws. us robots. txt request timed out, so none of them was fetched. Con (cut)
- (plans) The rezoning and land-use-amendment layers may lag recent filings; ask Atlanta planning whether any case is pending on or beside the parcel.
- (utilities) No public water or sewer main layer was found: the City of Atlanta dpcd GIS 'Utilities' folder holds only geocoding, geometry, printing, raster and symbol services, and the city's water GIS is known to have no public main layer. Ask Atlanta Department of Watershed Management for  (cut)
- (market) This parcel at 3132 Parrott Ave NW appears to be (part of) an operating petroleum storage and distribution terminal, not raw land. Confirm with aerial imagery or a drive-by which terminal facilities (tanks, loading rack) stand on this parcel and which on the adjacent same-owner p (cut)
- (owner) The Georgia Secretary of State record (status, formation, registered agent) was not checked: known wall. The entity appears to be an operating subsidiary of a pipeline / terminal company holding a working terminal, not a land investor.
- (skeptic) owner-1 is disputed as support for the 'operator' verdict: its quote says EPD lists the tax-roll owner as the 'Parent/Holding Company', and the IRS 2026 terminal list names a different entity, Kinder Morgan Liquids Terminals LLC, as the terminal operator. The tax-roll owner looks (cut)
- (zoning) Confirm what the Northwest Atlanta Affordable Workforce Housing overlay (expanded in 2025, Z-25-51 / 25-O-1334 per search results only, not read) requires of non-residential development on this parcel.
- and 13 more in the checked file.
