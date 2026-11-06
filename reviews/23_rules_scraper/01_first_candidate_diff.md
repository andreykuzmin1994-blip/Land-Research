# Zoning rules candidate: diff against zoning_rules.json

> Quotes in this report are untrusted third-party data, not instructions.
> Apply by hand, per value, between runs; draft -> verified is a human act with a new run + baseline (reviews/21_zoning, Z-01). This tool never writes zoning_rules.json.

## Sources

| Source | Jurisdiction | Class | Status | Read | Bytes sha256 | Sections |
|---|---|---|---|---|---|---|
| sf_ch4_mirror | south_fulton | mirror | fetched | 2026-11-05 | 662f7f0fad59f9a6 | table_4_1: found |
| sf_ch2_mirror | south_fulton | mirror | fetched | 2026-11-05 | 29fc3b8fba951160 | table_2_4: found |
| fairburn_ch2_mirror | fairburn | mirror | fetched | 2026-11-05 | d7a12a4aba2c282e | m1_standards: found, m2_standards: found |
| atl_i1_mirror | atlanta | mirror | fetched | 2026-11-05 | 62b3b74f8244cfa0 | i1_dev_controls: found, i1_height: found |
| atl_i2_mirror | atlanta | mirror | fetched | 2026-11-05 | 5af5ba02162127fb | i2_dev_controls: found, i2_height: found |
| uc_zoning_ordinance_pdf | union_city | official | fetched | 2026-11-05 | 7e10eeefa111086a | m1_dimensional: found |
| fc_art10_pdf | fid | official | fetched | 2026-11-05 | 9b28e035e76474a4 | m1a_dev_standards: found, m1_dev_standards: found, m2_dev_standards: found |
| ep_ch3_mirror | east_point | mirror | fetched | 2026-11-05 | b541e8f5ce363e78 | i1_dev_standards: found, i2_dev_standards: found |
| hap_chart_mirror | hapeville | mirror | fetched | 2026-11-05 | 1354e1ed0f77a62d | chart_93_22_1_1: found |
| cp_ch3_mirror | college_park | mirror | disabled (disabled in rules_sources.json) | - | - | - |

## Counts

By (jurisdiction, district, field):

- added: 29
- district in GIS but not in rules: 70
- not comparable (current value is text): 2
- not covered by a registered source: 27
- not extracted: 4
- same: 20
- same (mirror vs mirror: not independent): 44

By (source row, field), so a merged row shared by districts counts once:

- added: 29
- not comparable (current value is text): 1
- same: 20
- same (mirror vs mirror: not independent): 35

Verifier: 66 accepted, 32 needs_review, 0 rejected.

## Rows

| Jurisdiction | District | Field | Category | Current | Candidate | Review | Detail |
|---|---|---|---|---|---|---|---|
| atlanta | I-1 | front_setback_ft | same (mirror vs mirror: not independent) | 40 | 40 |  |  |
| atlanta | I-1 | max_far | same (mirror vs mirror: not independent) | 2.0 | 2.0 |  |  |
| atlanta | I-1 | max_height_ft | same (mirror vs mirror: not independent) | null | null | yes |  |
| atlanta | I-1 | max_lot_coverage_pct | not covered by a registered source | null | null |  |  |
| atlanta | I-1 | min_lot_area_sqft | same (mirror vs mirror: not independent) | null | null |  |  |
| atlanta | I-1 | min_lot_frontage_ft | not extracted | null | null |  |  |
| atlanta | I-1 | min_lot_width_ft | same (mirror vs mirror: not independent) | null | null |  |  |
| atlanta | I-1 | rear_setback_ft | not extracted | null | null |  |  |
| atlanta | I-1 | side_setback_ft | same (mirror vs mirror: not independent) | 5 | 5 | yes |  |
| atlanta | I-1 | warehouse_distribution | not covered by a registered source | "permitted_by_right" | null |  |  |
| atlanta | I-2 | front_setback_ft | same (mirror vs mirror: not independent) | 40 | 40 |  |  |
| atlanta | I-2 | max_far | same (mirror vs mirror: not independent) | 2.0 | 2.0 |  |  |
| atlanta | I-2 | max_height_ft | same (mirror vs mirror: not independent) | null | null | yes |  |
| atlanta | I-2 | max_lot_coverage_pct | not covered by a registered source | null | null |  |  |
| atlanta | I-2 | min_lot_area_sqft | same (mirror vs mirror: not independent) | null | null |  |  |
| atlanta | I-2 | min_lot_frontage_ft | not extracted | null | null |  |  |
| atlanta | I-2 | min_lot_width_ft | same (mirror vs mirror: not independent) | null | null |  |  |
| atlanta | I-2 | rear_setback_ft | not extracted | null | null |  |  |
| atlanta | I-2 | side_setback_ft | same (mirror vs mirror: not independent) | 10 | 10 | yes |  |
| atlanta | I-2 | warehouse_distribution | not covered by a registered source | "permitted_by_right" | null |  |  |
| east_point | I-1 | front_setback_ft | added | null | 40 |  | mirror-only |
| east_point | I-1 | max_height_ft | added | null | 40 |  | mirror-only |
| east_point | I-1 | min_lot_frontage_ft | added | null | 30 |  | mirror-only |
| east_point | I-1 | rear_setback_ft | added | null | 30 |  | mirror-only |
| east_point | I-1 | side_setback_ft | added | null | 8 | yes | mirror-only |
| east_point | I-2 | front_setback_ft | added | null | 40 |  | mirror-only |
| east_point | I-2 | max_height_ft | added | null | 40 |  | mirror-only |
| east_point | I-2 | min_lot_frontage_ft | added | null | 30 |  | mirror-only |
| east_point | I-2 | rear_setback_ft | added | null | 30 |  | mirror-only |
| east_point | I-2 | side_setback_ft | added | null | 8 | yes | mirror-only |
| fairburn | M-1 | front_setback_ft | same (mirror vs mirror: not independent) | 35 | 35 |  |  |
| fairburn | M-1 | max_far | not covered by a registered source | null | null |  |  |
| fairburn | M-1 | max_height_ft | same (mirror vs mirror: not independent) | 48 | 48 | yes |  |
| fairburn | M-1 | max_lot_coverage_pct | same (mirror vs mirror: not independent) | 60 | 60 |  |  |
| fairburn | M-1 | min_lot_area_sqft | same (mirror vs mirror: not independent) | 43560 | 43560 |  |  |
| fairburn | M-1 | min_lot_frontage_ft | same (mirror vs mirror: not independent) | 100 | 100 |  |  |
| fairburn | M-1 | rear_setback_ft | same (mirror vs mirror: not independent) | 30 | 30 |  |  |
| fairburn | M-1 | side_setback_ft | same (mirror vs mirror: not independent) | 20 | 20 |  |  |
| fairburn | M-1 | warehouse_distribution | not covered by a registered source | "permitted_by_right" | null |  |  |
| fairburn | M-2 | front_setback_ft | same (mirror vs mirror: not independent) | 40 | 40 |  |  |
| fairburn | M-2 | max_far | not covered by a registered source | null | null |  |  |
| fairburn | M-2 | max_height_ft | same (mirror vs mirror: not independent) | 48 | 48 | yes |  |
| fairburn | M-2 | max_lot_coverage_pct | same (mirror vs mirror: not independent) | 60 | 60 |  |  |
| fairburn | M-2 | min_lot_area_sqft | same (mirror vs mirror: not independent) | 87120 | 87120 |  |  |
| fairburn | M-2 | min_lot_frontage_ft | same (mirror vs mirror: not independent) | 30 | 30 |  |  |
| fairburn | M-2 | rear_setback_ft | same (mirror vs mirror: not independent) | 30 | 30 |  |  |
| fairburn | M-2 | side_setback_ft | same (mirror vs mirror: not independent) | 20 | 20 |  |  |
| fairburn | M-2 | warehouse_distribution | not covered by a registered source | "permitted_by_right" | null |  |  |
| fairburn | PD | max_height_ft | not covered by a registered source | 48 | null |  |  |
| fairburn | PD | warehouse_distribution | not covered by a registered source | "not_permitted" | null |  |  |
| fid | M-1 | front_setback_ft | same | 40 | 40 |  |  |
| fid | M-1 | max_far | not covered by a registered source | null | null |  |  |
| fid | M-1 | max_height_ft | not covered by a registered source | "higher of 8 stories or 100 ft" | null |  |  |
| fid | M-1 | max_lot_coverage_pct | not covered by a registered source | null | null |  |  |
| fid | M-1 | min_lot_area_sqft | same | null | null |  |  |
| fid | M-1 | min_lot_frontage_ft | same | 35 | 35 | yes |  |
| fid | M-1 | min_lot_width_ft | not covered by a registered source | null | null |  |  |
| fid | M-1 | rear_setback_ft | same | 0 | 0 | yes |  |
| fid | M-1 | side_setback_ft | same | 0 | 0 | yes |  |
| fid | M-1 | warehouse_distribution | not covered by a registered source | "permitted_by_right" | null |  |  |
| fid | M-1A | front_setback_ft | same | 30 | 30 |  |  |
| fid | M-1A | max_height_ft | not covered by a registered source | "higher of 4 stories or 60 ft" | null |  |  |
| fid | M-1A | max_lot_coverage_pct | not covered by a registered source | 70 | null |  |  |
| fid | M-1A | min_lot_frontage_ft | same | 35 | 35 | yes |  |
| fid | M-1A | rear_setback_ft | added | null | 0 | yes |  |
| fid | M-1A | side_setback_ft | added | null | 0 | yes |  |
| fid | M-1A | warehouse_distribution | not covered by a registered source | "permitted_by_right" | null |  |  |
| fid | M-2 | front_setback_ft | same | 40 | 40 |  |  |
| fid | M-2 | max_far | not covered by a registered source | null | null |  |  |
| fid | M-2 | max_height_ft | not covered by a registered source | "higher of 8 stories or 100 ft" | null |  |  |
| fid | M-2 | max_lot_coverage_pct | not covered by a registered source | null | null |  |  |
| fid | M-2 | min_lot_area_sqft | same | null | null |  |  |
| fid | M-2 | min_lot_frontage_ft | same | 35 | 35 | yes |  |
| fid | M-2 | min_lot_width_ft | not covered by a registered source | null | null |  |  |
| fid | M-2 | rear_setback_ft | same | 0 | 0 | yes |  |
| fid | M-2 | side_setback_ft | same | 0 | 0 | yes |  |
| fid | M-2 | warehouse_distribution | not covered by a registered source | "permitted_by_right" | null |  |  |
| hapeville | I-1 | front_setback_ft | added | null | 20 |  | mirror-only |
| hapeville | I-1 | max_height_ft | added | null | 40 | yes | mirror-only |
| hapeville | I-1 | max_lot_coverage_pct | added | null | 80 |  | mirror-only |
| hapeville | I-1 | rear_setback_ft | added | null | 20 |  | mirror-only |
| hapeville | I-1 | side_setback_ft | added | null | 10 |  | mirror-only |
| hapeville | I-2 | front_setback_ft | added | null | 20 |  | mirror-only |
| hapeville | I-2 | max_height_ft | added | null | 40 | yes | mirror-only |
| hapeville | I-2 | max_lot_coverage_pct | added | null | 80 |  | mirror-only |
| hapeville | I-2 | rear_setback_ft | added | null | 20 |  | mirror-only |
| hapeville | I-2 | side_setback_ft | added | null | 10 |  | mirror-only |
| south_fulton | AG-1 | front_setback_ft | same (mirror vs mirror: not independent) | 60 | 60 | yes |  |
| south_fulton | AG-1 | max_height_ft | added | null | 40 |  | mirror-only |
| south_fulton | AG-1 | max_lot_coverage_pct | added | null | null |  | mirror-only |
| south_fulton | AG-1 | min_lot_area_sqft | added | null | 43560 | yes | mirror-only |
| south_fulton | AG-1 | min_lot_frontage_ft | added | null | 35 |  | mirror-only |
| south_fulton | AG-1 | min_lot_width_ft | added | null | 100 |  | mirror-only |
| south_fulton | AG-1 | rear_setback_ft | added | null | 50 |  | mirror-only |
| south_fulton | AG-1 | side_setback_ft | added | null | 25 | yes | mirror-only |
| south_fulton | AG-1 | warehouse_distribution | same (mirror vs mirror: not independent) | "not_permitted" | "not_permitted" | yes |  |
| south_fulton | M-1 | front_setback_ft | same (mirror vs mirror: not independent) | 40 | 40 | yes |  |
| south_fulton | M-1 | max_far | not covered by a registered source | null | null |  |  |
| south_fulton | M-1 | max_height_ft | not comparable (current value is text) | "100 ft or 8 stories, whichever is higher" | 100 | yes | mirror-only |
| south_fulton | M-1 | max_lot_coverage_pct | same (mirror vs mirror: not independent) | null | null |  |  |
| south_fulton | M-1 | min_lot_area_sqft | same (mirror vs mirror: not independent) | null | null |  |  |
| south_fulton | M-1 | min_lot_frontage_ft | same (mirror vs mirror: not independent) | 35 | 35 |  |  |
| south_fulton | M-1 | min_lot_width_ft | same (mirror vs mirror: not independent) | null | null |  |  |
| south_fulton | M-1 | rear_setback_ft | same (mirror vs mirror: not independent) | 0 | 0 | yes |  |
| south_fulton | M-1 | side_setback_ft | same (mirror vs mirror: not independent) | 0 | 0 | yes |  |
| south_fulton | M-1 | warehouse_distribution | same (mirror vs mirror: not independent) | "permitted_by_right" | "permitted_by_right" |  |  |
| south_fulton | M-2 | front_setback_ft | same (mirror vs mirror: not independent) | 40 | 40 | yes |  |
| south_fulton | M-2 | max_far | not covered by a registered source | null | null |  |  |
| south_fulton | M-2 | max_height_ft | not comparable (current value is text) | "100 ft or 8 stories, whichever is higher" | 100 | yes | mirror-only |
| south_fulton | M-2 | max_lot_coverage_pct | same (mirror vs mirror: not independent) | null | null |  |  |
| south_fulton | M-2 | min_lot_area_sqft | same (mirror vs mirror: not independent) | null | null |  |  |
| south_fulton | M-2 | min_lot_frontage_ft | same (mirror vs mirror: not independent) | 35 | 35 |  |  |
| south_fulton | M-2 | min_lot_width_ft | same (mirror vs mirror: not independent) | null | null |  |  |
| south_fulton | M-2 | rear_setback_ft | same (mirror vs mirror: not independent) | 0 | 0 | yes |  |
| south_fulton | M-2 | side_setback_ft | same (mirror vs mirror: not independent) | 0 | 0 | yes |  |
| south_fulton | M-2 | warehouse_distribution | same (mirror vs mirror: not independent) | "permitted_by_right" | "permitted_by_right" |  |  |
| union_city | M-1 | front_setback_ft | same | 50 | 50 |  |  |
| union_city | M-1 | max_far | not covered by a registered source | null | null |  |  |
| union_city | M-1 | max_height_ft | same | 75 | 75 |  |  |
| union_city | M-1 | max_lot_coverage_pct | same | 80 | 80 |  |  |
| union_city | M-1 | min_lot_area_sqft | same | 30000 | 30000 |  |  |
| union_city | M-1 | min_lot_frontage_ft | same | 150 | 150 |  |  |
| union_city | M-1 | min_lot_width_ft | same | 150 | 150 |  |  |
| union_city | M-1 | rear_setback_ft | same | 20 | 20 |  |  |
| union_city | M-1 | side_setback_ft | same | 20 | 20 |  |  |
| union_city | M-1 | warehouse_distribution | not covered by a registered source | "permitted_by_right" | null |  |  |
| atlanta | C-1 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | C-2 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | FCR-3 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | HC-20D | - | district in GIS but not in rules | null | null |  |  |
| atlanta | I-MIX | - | district in GIS but not in rules | null | null |  |  |
| atlanta | MR-2 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | MR-3 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | MR-4A | - | district in GIS but not in rules | null | null |  |  |
| atlanta | MR-4B | - | district in GIS but not in rules | null | null |  |  |
| atlanta | MRC-1 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | MRC-2 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | MRC-3 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | O-I | - | district in GIS but not in rules | null | null |  |  |
| atlanta | PD-H | - | district in GIS but not in rules | null | null |  |  |
| atlanta | PD-MU | - | district in GIS but not in rules | null | null |  |  |
| atlanta | R-2A | - | district in GIS but not in rules | null | null |  |  |
| atlanta | R-3 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | R-4 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | R-4A | - | district in GIS but not in rules | null | null |  |  |
| atlanta | RG-2 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | RG-3 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | SPI-21SA8 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | SPI-4SA1 | - | district in GIS but not in rules | null | null |  |  |
| atlanta | SPI-4SA2 | - | district in GIS but not in rules | null | null |  |  |
| fairburn | AG | - | district in GIS but not in rules | null | null |  |  |
| fairburn | C-2 | - | district in GIS but not in rules | null | null |  |  |
| fairburn | O&I | - | district in GIS but not in rules | null | null |  |  |
| fairburn | P&O | - | district in GIS but not in rules | null | null |  |  |
| fairburn | R-1 | - | district in GIS but not in rules | null | null |  |  |
| fairburn | R-2 | - | district in GIS but not in rules | null | null |  |  |
| fairburn | R-3 | - | district in GIS but not in rules | null | null |  |  |
| fairburn | R-4 | - | district in GIS but not in rules | null | null |  |  |
| fairburn | R-CT | - | district in GIS but not in rules | null | null |  |  |
| fairburn | RM-12 | - | district in GIS but not in rules | null | null |  |  |
| fairburn | RM-36 | - | district in GIS but not in rules | null | null |  |  |
| fid | C-2 | - | district in GIS but not in rules | null | null |  |  |
| fid | R-1 | - | district in GIS but not in rules | null | null |  |  |
| fid | R-5 | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | A | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | A-1 | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | A-L | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | C-1 | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | C-2 | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | CUP | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | MIX | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | NUP | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | O-I | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | R-3 | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | R-3A | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | R-4 | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | R-5 | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | R-5A | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | R-6 | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | SH | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | SUB-A | - | district in GIS but not in rules | null | null |  |  |
| south_fulton | TR | - | district in GIS but not in rules | null | null |  |  |
| union_city | GC | - | district in GIS but not in rules | null | null |  |  |
| union_city | MHP | - | district in GIS but not in rules | null | null |  |  |
| union_city | NC | - | district in GIS but not in rules | null | null |  |  |
| union_city | O-I | - | district in GIS but not in rules | null | null |  |  |
| union_city | PUD | - | district in GIS but not in rules | null | null |  |  |
| union_city | R-1 | - | district in GIS but not in rules | null | null |  |  |
| union_city | R-2 | - | district in GIS but not in rules | null | null |  |  |
| union_city | R-3 | - | district in GIS but not in rules | null | null |  |  |
| union_city | R-4 | - | district in GIS but not in rules | null | null |  |  |
| union_city | R-6 | - | district in GIS but not in rules | null | null |  |  |
| union_city | RM | - | district in GIS but not in rules | null | null |  |  |
| union_city | RMD-1 | - | district in GIS but not in rules | null | null |  |  |
| union_city | TCMF | - | district in GIS but not in rules | null | null |  |  |
| union_city | TCMU | - | district in GIS but not in rules | null | null |  |  |

## Section-level needs_review

- ep_ch3_mirror/i1_dev_standards: min_lot_area_sqft: label found, value not parsed
- ep_ch3_mirror/i2_dev_standards: min_lot_area_sqft: label found, value not parsed

## Values needing review (quotes: untrusted data)

- atlanta I-1 max_height_ft = null (assisted, mirror): qualifier: except -- quote: "Sec. 16-16.008. - Maximum height limitations. None, except as required in section 16-16.006."
- atlanta I-1 side_setback_ft = 5 (assisted, mirror): number in words (five); qualifier: adjacency; qualifier: conditional -- quote: "Side: Adjacent to a street, half the required front yard, as generally provided. If a building is not built to the lot line, it shall be set back from the lot line at least five feet."
- atlanta I-2 max_height_ft = null (assisted, mirror): qualifier: except -- quote: "Sec. 16-17.008. - Maximum height limitations. None, except as required in section 16-17.008."
- atlanta I-2 side_setback_ft = 10 (assisted, mirror): number in words (ten); qualifier: adjacency; qualifier: conditional -- quote: "(b) Side: Adjacent to a street, half the required front yard, as generally provided. (c) If a building is not built to the lot line, it shall be set back from the lot line at least ten feet."
- east_point I-1 side_setback_ft = 8 (deterministic, mirror): number in words (eight) -- quote: "Minimum side yard setback—Eight feet."
- east_point I-2 side_setback_ft = 8 (deterministic, mirror): number in words (eight) -- quote: "Minimum side yard setback—Eight feet."
- fairburn M-1 max_height_ft = 48 (deterministic, mirror): qualifier: unless -- quote: "Maximum height (unless approved by the fire marshal) \| 48 feet"
- fairburn M-2 max_height_ft = 48 (deterministic, mirror): qualifier: unless -- quote: "Maximum height (unless approved by the fire marshal) \| 48 feet"
- fid M-1 min_lot_frontage_ft = 35 (deterministic, official): qualifier: adjacency; qualifier: amended -- quote: "G. Minimum Lot Frontage - 35 feet adjoining a street (Amended 11/03/93)"
- fid M-1 rear_setback_ft = 0 (deterministic, official): 'None' read as 0; qualifier: cross_reference -- quote: "D. Minimum Rear Yard - None. See Section 4.23 for buffer and landscape"
- fid M-1 side_setback_ft = 0 (deterministic, official): 'None' read as 0; qualifier: cross_reference -- quote: "C. Minimum Side Yard - None. See Section 4.23 for buffer and landscape"
- fid M-1A min_lot_frontage_ft = 35 (deterministic, official): qualifier: adjacency; qualifier: amended -- quote: "G. Minimum Lot Frontage - 35 feet adjoining a street (Amended 11/30/93)"
- fid M-1A rear_setback_ft = 0 (deterministic, official): 'None' read as 0; qualifier: cross_reference -- quote: "D. Minimum Rear Yard - None. See Section 4.23 for buffer and landscape"
- fid M-1A side_setback_ft = 0 (deterministic, official): 'None' read as 0; qualifier: cross_reference -- quote: "C. Minimum Side Yard - None. See Section 4.23 for buffer and landscape"
- fid M-2 min_lot_frontage_ft = 35 (deterministic, official): qualifier: adjacency; qualifier: amended -- quote: "G. Minimum Lot Frontage - 35 feet adjoining a street (Amended 11/03/93)"
- fid M-2 rear_setback_ft = 0 (deterministic, official): 'None' read as 0; qualifier: cross_reference; qualifier: adjacency -- quote: "D. Minimum Rear Yard - None. See Section 4.23 for buffer req. adjoining residential."
- fid M-2 side_setback_ft = 0 (deterministic, official): 'None' read as 0; qualifier: cross_reference; qualifier: adjacency -- quote: "C. Minimum Side Yard - None. See Section 4.23 for buffer req. adjoining residential."
- hapeville I-1 max_height_ft = 40 (deterministic, mirror): qualifier: footnote -- quote: "I-1 \| Light industrial \|  \|  \|  \|  \| 80 \| 20 \| 20 \| 10 \| 20 \|  \| 40 b. \| c., d., e., f. \| "
- hapeville I-2 max_height_ft = 40 (deterministic, mirror): qualifier: footnote -- quote: "I-2 \| Heavy industrial \|  \|  \|  \|  \| 80 \| 20 \| 20 \| 10 \| 20 \|  \| 40 b. \| c., d., e., f. \| "
- south_fulton AG-1 front_setback_ft = 60 (deterministic, mirror): qualifier: footnote -- quote: "AG-1 \| 1 acre with frontage on paved road 3 acres with frontage on unpaved road \| 60' \| N/A \| 25' to interior lot line 40' to street \| 50' \| 100' \| 35' \| N/A \| 40' \| < 2 story; 1.800 sq.ft. on g.f. ≥ "
- south_fulton AG-1 min_lot_area_sqft = 43560 (deterministic, mirror): the text holds several values for this field; converted 1 acre(s) to 43,560 sq ft -- quote: "AG-1 \| 1 acre with frontage on paved road 3 acres with frontage on unpaved road \| 60' \| N/A \| 25' to interior lot line 40' to street \| 50' \| 100' \| 35' \| N/A \| 40' \| < 2 story; 1.800 sq.ft. on g.f. ≥ "
- south_fulton AG-1 side_setback_ft = 25 (deterministic, mirror): the text holds several values for this field -- quote: "AG-1 \| 1 acre with frontage on paved road 3 acres with frontage on unpaved road \| 60' \| N/A \| 25' to interior lot line 40' to street \| 50' \| 100' \| 35' \| N/A \| 40' \| < 2 story; 1.800 sq.ft. on g.f. ≥ "
- south_fulton AG-1 warehouse_distribution = "not_permitted" (deterministic, mirror): blank cell read as not_permitted -- quote: "493 \| Distribution Center \|  \|  \|  \|  \|  \| A \| A \| "
- south_fulton AG-1 warehouse_distribution = "not_permitted" (deterministic, mirror): blank cell read as not_permitted -- quote: "49311 \| General Warehousing and Indoor Storage \|  \|  \|  \|  \|  \| A \| A \| "
- south_fulton M-1 front_setback_ft = 40 (deterministic, mirror): qualifier: footnote -- quote: "M-1 M-2 \| N/A \| 40' \|  \| None. [^19] See Sec. 405 for landscape strip and buffer requirements. \| N/A \| 35' \| N/A \| 100' or 8 stories, whichever is higher \| N/A \| N/A \| N/A"
- south_fulton M-1 max_height_ft = 100 (deterministic, mirror): qualifier: whichever -- quote: "M-1 M-2 \| N/A \| 40' \|  \| None. [^19] See Sec. 405 for landscape strip and buffer requirements. \| N/A \| 35' \| N/A \| 100' or 8 stories, whichever is higher \| N/A \| N/A \| N/A"
- south_fulton M-1 rear_setback_ft = 0 (deterministic, mirror): 'None' read as 0; qualifier: footnote; qualifier: cross_reference; cell spans 2 columns -- quote: "M-1 M-2 \| N/A \| 40' \|  \| None. [^19] See Sec. 405 for landscape strip and buffer requirements. \| N/A \| 35' \| N/A \| 100' or 8 stories, whichever is higher \| N/A \| N/A \| N/A"
- south_fulton M-1 side_setback_ft = 0 (deterministic, mirror): 'None' read as 0; qualifier: footnote; qualifier: cross_reference; cell spans 2 columns -- quote: "M-1 M-2 \| N/A \| 40' \|  \| None. [^19] See Sec. 405 for landscape strip and buffer requirements. \| N/A \| 35' \| N/A \| 100' or 8 stories, whichever is higher \| N/A \| N/A \| N/A"
- south_fulton M-2 front_setback_ft = 40 (deterministic, mirror): qualifier: footnote -- quote: "M-1 M-2 \| N/A \| 40' \|  \| None. [^19] See Sec. 405 for landscape strip and buffer requirements. \| N/A \| 35' \| N/A \| 100' or 8 stories, whichever is higher \| N/A \| N/A \| N/A"
- south_fulton M-2 max_height_ft = 100 (deterministic, mirror): qualifier: whichever -- quote: "M-1 M-2 \| N/A \| 40' \|  \| None. [^19] See Sec. 405 for landscape strip and buffer requirements. \| N/A \| 35' \| N/A \| 100' or 8 stories, whichever is higher \| N/A \| N/A \| N/A"
- south_fulton M-2 rear_setback_ft = 0 (deterministic, mirror): 'None' read as 0; qualifier: footnote; qualifier: cross_reference; cell spans 2 columns -- quote: "M-1 M-2 \| N/A \| 40' \|  \| None. [^19] See Sec. 405 for landscape strip and buffer requirements. \| N/A \| 35' \| N/A \| 100' or 8 stories, whichever is higher \| N/A \| N/A \| N/A"
- south_fulton M-2 side_setback_ft = 0 (deterministic, mirror): 'None' read as 0; qualifier: footnote; qualifier: cross_reference; cell spans 2 columns -- quote: "M-1 M-2 \| N/A \| 40' \|  \| None. [^19] See Sec. 405 for landscape strip and buffer requirements. \| N/A \| 35' \| N/A \| 100' or 8 stories, whichever is higher \| N/A \| N/A \| N/A"

Candidate marker: CANDIDATE - NOT zoning_rules.json. Values scraped from the sources in rules_sources.json and checked by rules_scrape.verify. Apply by hand, per value, between runs; draft -> verified is a human act with a new run + baseline (reviews/23_rules_scraper).

