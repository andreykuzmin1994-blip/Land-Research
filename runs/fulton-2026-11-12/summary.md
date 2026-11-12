# Fulton County land screen — 2026-11-12

| Stage | Parcels |
|---|---|
| 5-50 acres in the chosen jurisdictions | 5,367 |
| not built on, not government / developer / association / exempt | 2,521 |
| pass floodplain, wetlands, protected-land and size screens | 2,119 |
| pass the slope screen | 1,582 |
| ranked | 1,582 |

## Top 10

| # | Score | Acres | Where | Entitlement | Owner type |
|---|---|---|---|---|---|
| 1 | 84.4 | 20.5 | 600 NATIONAL DR (Fulton Industrial District) | by_right | entity |
| 2 | 81.4 | 20.0 | 81 POOLE CREEK RD SE (Atlanta) | by_right | entity |
| 3 | 80.8 | 24.5 | 0 THAMES RD (Union City) | by_right | entity |
| 4 | 80.7 | 31.1 | 3600 NATURALLY FRESH BLVD (South Fulton / unincorporated) | by_right | entity |
| 5 | 80.6 | 18.4 | 0 MARIETTA RD NW (Atlanta) | by_right | entity |
| 6 | 79.9 | 47.5 | 0 IRWIN RD (South Fulton / unincorporated) | by_right | entity |
| 7 | 79.8 | 14.0 | 0 FORREST PARK RD SE # REAR (Atlanta) | likely | entity |
| 8 | 79.7 | 12.8 | 0 STACKS RD REAR (South Fulton / unincorporated) | by_right | entity |
| 9 | 79.6 | 9.7 | 897 ADAMSON ST SW (Atlanta) | likely | entity |
| 10 | 78.8 | 5.4 | 555 SPENCE RD (South Fulton / unincorporated) | by_right | individual |

## Most common reject reasons

- 1,784 — already built on
- 683 — tax-exempt parcel
- 451 — of the open ground is steeper than 15%
- 311 — owned by a homeowners' / community association
- 242 — in the 100-year floodplain
- 102 — too few usable acres after constraints
- 75 — in a regulatory floodway
- 38 — overlaps protected land or a conservation easement
- 31 — mapped wetlands
- 26 — held by a development authority
- 20 — railroad / utility owned
- 20 — already owned by an industrial developer / REIT
- 2 — other reasons (1 kinds; see rejected.csv)

## Notes

- Same-owner groups: 521 groups of 2 or more parcels (1853 parcels, largest 152) share an exact tax-roll owner name and mailing address among the 5367 parcels of 5-50 ac fetched (before any screen or trial limit); 394 adjacent sets (touching or within 10 ft), 300 inside the band and 94 larger. Different spellings or addresses of one owner are not joined
- 1 of 2119 parcels that pass the map screens have under one 10 m lidar pixel (100 m²) of open ground on the county parcel map, so their map checks can't be trusted and slope was not checked (each is flagged). The map shape is the problem, not a failed check: compare the county parcel map with the tax record.
- Slope: 2117 of 2119 lidar answers were reused from cache/slope (oldest kept 2026-11-12), 1 asked now. A kept answer is one reading from a service whose answers for the same shape do not always agree (README, "Public data changes between days"); --refresh-slope takes new readings.
- Detention pond (rough allowance, no hydrology): pond fits on 1123 of 1582 ranked parcels, no pond room found on 459
- Soils (USDA SSURGO via Soil Data Access, map-unit level, not a geotechnical report): checked for 250 of 1582 ranked parcels
- Soils not checked for 1332 of 1582 ranked parcels: outside the top 250
- Road frontage (GDOT road centrelines, parcel line within 50 ft; curb cuts not checked): frontage on 198 of 1582 ranked parcels, none within 50 ft on 52
- Road frontage not checked for 1332 of 1582 ranked parcels: outside the top 250
- Lead grade (a fixed rule over this screen's checks; not a score or a screen, the ranking is unchanged): A 14, B 96, C 140, blocked 391 of 1582 ranked parcels; see leads.md
- Lead grade not given for 941 of 1582 ranked parcels: soils only checked for the top 250 ([soils] max_parcels)
- 531 ranked parcels have no machine-readable zoning (Chattahoochee Hills 397, Palmetto 84, College Park 32, East Point 15, Hapeville 2, Union City 1): check with the city
- Wetlands come from the 1980s National Wetlands Inventory and miss small and forested wetlands; floodplain from FEMA NFHL (Mar 2026 snapshot)
