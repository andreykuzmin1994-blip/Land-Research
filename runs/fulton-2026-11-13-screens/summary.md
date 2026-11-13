# Fulton County land screen — 2026-11-13

| Stage | Parcels |
|---|---|
| 5-50 acres in the chosen jurisdictions | 5,367 |
| not built on, not government / developer / association / exempt | 2,426 |
| pass floodplain, wetlands, protected-land and size screens | 2,030 |
| pass the slope screen | 1,499 |
| ranked | 1,499 |

## Top 10

| # | Score | Acres | Where | Entitlement | Owner type |
|---|---|---|---|---|---|
| 1 | 84.4 | 20.5 | 600 NATIONAL DR (Fulton Industrial District) | by_right | entity |
| 2 | 80.8 | 24.5 | 0 THAMES RD (Union City) | by_right | entity |
| 3 | 80.6 | 18.4 | 0 MARIETTA RD NW (Atlanta) | by_right | entity |
| 4 | 79.9 | 47.5 | 0 IRWIN RD (South Fulton / unincorporated) | by_right | entity |
| 5 | 79.7 | 12.8 | 0 STACKS RD REAR (South Fulton / unincorporated) | by_right | entity |
| 6 | 78.8 | 5.4 | 555 SPENCE RD (South Fulton / unincorporated) | by_right | individual |
| 7 | 78.8 | 39.6 | 0 ROCK QUARRY RD (Union City) | by_right | entity |
| 8 | 78.6 | 27.0 | 0 SPENCE RD (South Fulton / unincorporated) | by_right | entity |
| 9 | 78.6 | 19.9 | 0 CONNALLY DR (East Point) | by_right | entity |
| 10 | 77.9 | 12.0 | 0 WELCOME ALL RD (South Fulton / unincorporated) | by_right | entity |

## Most common reject reasons

- 1,877 — already built on
- 683 — tax-exempt parcel
- 447 — of the open ground is steeper than 15%
- 311 — owned by a homeowners' / community association
- 240 — in the 100-year floodplain
- 100 — too few usable acres after constraints
- 73 — in a regulatory floodway
- 36 — overlaps protected land or a conservation easement
- 31 — mapped wetlands
- 26 — held by a development authority
- 21 — railroad / utility owned
- 20 — already owned by an industrial developer / REIT
- 3 — other reasons (2 kinds; see rejected.csv)

## Notes

- Same-owner groups: 521 groups of 2 or more parcels (1853 parcels, largest 152) share an exact tax-roll owner name and mailing address among the 5367 parcels of 5-50 ac fetched (before any screen or trial limit); 394 adjacent sets (touching or within 10 ft), 300 inside the band and 94 larger. Different spellings or addresses of one owner are not joined
- Slope: 2030 of 2030 lidar answers were reused from cache/slope (oldest kept 2026-11-13), 0 asked now. A kept answer is one reading from a service whose answers for the same shape do not always agree (README, "Public data changes between days"); --refresh-slope takes new readings.
- Detention pond (rough allowance, no hydrology): pond fits on 1067 of 1499 ranked parcels, no pond room found on 432
- Soils (USDA SSURGO via Soil Data Access, map-unit level, not a geotechnical report): checked for 250 of 1499 ranked parcels
- Soils not checked for 1249 of 1499 ranked parcels: outside the top 250
- Road frontage (GDOT road centrelines, parcel line within 50 ft; curb cuts not checked): frontage on 199 of 1499 ranked parcels, none within 50 ft on 51
- Road frontage not checked for 1249 of 1499 ranked parcels: outside the top 250
- Lead grade (a fixed rule over this screen's checks; not a score or a screen, the ranking is unchanged): A 3, B 68, C 174, blocked 390 of 1499 ranked parcels; see leads.md
- Lead grade not given for 864 of 1499 ranked parcels: soils only checked for the top 250 ([soils] max_parcels)
- 521 ranked parcels have no machine-readable zoning (Chattahoochee Hills 396, Palmetto 83, College Park 27, East Point 12, Hapeville 2, Union City 1): check with the city
- Wetlands come from the 1980s National Wetlands Inventory and miss small and forested wetlands; floodplain from FEMA NFHL (Mar 2026 snapshot)
