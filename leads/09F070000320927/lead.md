# 0 IRWIN RD, parcel 09F070000320927 (City of South Fulton) — lead check, run fulton-2026-11-10

**Lead grade A**: zoning path, building fit, pond room and road frontage all came back clear, and no watch item was found

**In one line:** the M-1 zoning is real but it was granted in March 2026 for a named developer's 1.6 million SF project, and public records point to that developer buying the land. Check that first.

## What the documents say

- **Zoning: `conditional`.** The city rezoned this parcel, by id, from AG-1 to M-1 with conditions (LEED, community improvement district, $400,000 for parks, state road conditions). Best support: "and this Ordinance contains attached conditions of approval, building permits shall be issued only in compliance with the attached conditions." ([ordinance ORD2026-010](https://www.cityofsouthfultonga.gov/AgendaCenter/ViewFile/Agenda/_03242026-1806)); adopted 24 March 2026 per the [signed copy](https://www.cityofsouthfultonga.gov/AgendaCenter/ViewFile/ArchivedAgenda/_03242026-796). The city code's own M-1 use table was not read.
- **Plans: `supports`.** "The subject property has a future land use designation of Industrial Zone as designated in the 2021 South Fulton Comprehensive Plan, which supports the proposed zoning classification and density." ([staff report, June 2025](https://www.cityofsouthfultonga.gov/DocumentCenter/View/13326/Z24-026-CV24-010-MAPCO-rm)). The same report says the designation dates only from April 2025 and that the regional (ARC) review found the project not aligned with the regional plan.
- **Utilities: `nearby` (sewer only; water not shown).** Fulton County's sewer map returns county 8-inch pipes within 1,500 ft of the parcel's centre: `"Basin":"MORNING CREEK","SubBasin":"MC62","UpSize":"8","UpMaterial":"PVC"` ([county map query](https://gismaps.fultoncountyga.gov/arcgispub/rest/services/WaterResources/WaterResourcesSewer/MapServer/4/query?where=1%3D1&geometry=-84.572769%2C33.550782&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&distance=1500&units=esriSRUnit_Foot&outFields=*&returnGeometry=false&orderByFields=OBJECTID&f=json)). The county's water pipe layer shows nothing in that radius.
- **Market: `signs_under_contract`.** The rezoning applicant was a developer acting as agent, and the county development authority backed its project here: "The estimated closing date of Project MAPCO is Q3 of 2026." ([Develop Fulton release, 29 April 2026](https://www.developfultoncounty.com/uploads/about-us-news_47_1005894727.pdf)). No page shows the contract or a closing.
- **Owner: `unknown`.** The Secretary of State record could not be read. The [county tax parcel record](https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query?where=ParcelID%3D%2709F070000320927%27&outFields=ParcelID,Owner,OwnerAddr1,OwnerAddr2,Address,LandAcres,LUCode,ClassCode,TaxDist&returnGeometry=false&f=json) still showed the brief's owner on 2026-11-10.

## Skeptic

Not run yet. A second agent does this step.

## What the check proves

`quotecheck` kept 25 claims and dropped 0; no verdict was downgraded. It proves only that each quote was on its page when fetched. It does not prove the page is current or that the reading of it is right. Still `unknown`: the owner section, the M-1 use table, the water provider and whether the sale closed.

## Questions for a person, most important first

1. Has the developer closed on this parcel, or is its contract still live? Pull the latest deeds at the Fulton County Clerk of Superior Court (the release estimated a closing in Q3 2026).
2. Which copy of ORD2026-010 is final? The posted copies differ (191.20 vs 161.77 acres, wording of condition 5). Is all of this parcel inside the legal description, and do the conditions bind any buyer and any plan?
3. Does the M-1 text in the city code (Appendix C, on Municode) permit warehouse / distribution by right, with no special use permit?
4. What happened to the first case, Z24-026, before the council ordinance, and is any challenge to the rezoning pending?
5. Sewer capacity and tap point (Fulton County Public Works; one nearby segment has an old "downstream manhole is surcharged" note). Who supplies water, and where is the nearest main?
6. Were the stream buffer variance, the Army Corps permit and the 100 ft to 50 ft buffer variance obtained?
7. Secretary of State record for the owner, and the owner's relationship to the developer.

All claims, quotes and the full open-item lists are in `findings.checked.json`.
