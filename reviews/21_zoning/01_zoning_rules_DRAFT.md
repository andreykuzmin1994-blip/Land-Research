# Draft zoning rules: industrial districts, south/west Fulton County, GA

Generated 2026-11-05. **DRAFT for human verification.** Full citations (URL + section/table) for every value are in `zoning_rules_draft.json`; this file shows values with confidence flags. `null` = not stated / not found.

## Headline findings

- **Warehouse/distribution is allowed by right** in: South Fulton M-1 and M-2, Fairburn M-1 and M-2, Atlanta I-1 and I-2 (except within 500 ft of the Beltline), Union City M-1, and Fulton County M-1/M-1A/M-2.
- **Not allowed (rezoning needed):** South Fulton AG-1 (blank in Table 2-4 = prohibited) and Fairburn PD (permitted-use list has no industrial uses). Recent AG-1 to industrial rezonings in South Fulton have been denied or recommended for denial.
- **Truck terminals differ by city:** Atlanta needs a special use permit. Fairburn needs a use permit (its M-1 text is self-contradictory). Union City permits 'truck transportation' by right. South Fulton has no row for them, which by Sec. 207.04 means prohibited unless staff decides otherwise. Fulton County M-1 prohibits them; M-2 allows them.
- **Height limits that bind modern warehouses:** Fairburn caps height at 48 ft unless the fire marshal approves more. The South Fulton and Fulton County M-1/M-2 rule next to residential adds 12 ft of setback for every foot of height above 50 ft. That wording appears in the official county text too, so it is not a mirror typo. Atlanta uses a 45-degree transitional height plane.
- **Biggest site-yield items:** South Fulton/Fulton County require a 50 ft side / 100 ft rear undisturbed buffer + 10 ft improvement setback next to residential or AG-1. Union City requires a 75 ft buffer + 20 ft setback next to residential or agricultural. Fairburn requires a 50 ft buffer next to other districts and 25 ft along all rights-of-way, with 60% coverage. Under IBC 507, unlimited-area buildings need 60 ft yards on all sides.
- **FID:** most of it has been in the City of South Fulton since HB 445 (2021), so the South Fulton ordinance and its Sec. 510 FIB overlay apply. Use the county Zoning Resolution only for parcels still unincorporated, such as the area north of I-20 around Brown Field. Check each parcel against city-limits GIS.

## City of South Fulton

- Ordinance: City of South Fulton Zoning Ordinance (City Code, Appendix C), Articles 2-6
- URL: https://library.municode.com/ga/south_fulton (official, blocked to fetch); text read from https://www.zoneomics.com/code/south-fulton-GA/chapter_2 and chapters 3-6
- As of: Mirror shows amendments through Ord. No. 2026-011 (4-28-2026); read 2026-11-05
- Source quality: third-party mirror (Zoneomics); key values cross-checked against official City staff report Z24-029 (buffers) and against the official Fulton County Zoning Resolution the ordinance was derived from (identical numbers)

| Rule | M-1 | M-2 | AG-1 |
|---|---|---|---|
| warehouse distribution | permitted by right | permitted by right | not permitted |
| cold storage | permitted by right | permitted by right |  |
| truck terminal | not listed presumed prohibited (low) | not listed presumed prohibited (low) | not permitted |
| manufacturing light | permitted by right | permitted by right | not permitted |
| outdoor storage accessory | permitted with standards | permitted with standards |  |
| min lot area sqft | null | null |  |
| min lot width ft | null | null |  |
| min lot frontage ft | 35 | 35 |  |
| front setback ft | 40 | 40 | 60 |
| side setback ft | 0 | 0 |  |
| rear setback ft | 0 | 0 |  |
| corner lot rule | Front yard (40 ft) on each street frontage except corner lots; corner side yard 'as specified in the district regulations' (Table 4-1 gives none for M-1/M-2); corner-side landscape strip 10 ft (Table 4-4) (medium) | Front yard (40 ft) on each street frontage except corner lots; corner side yard 'as specified in the district regulations' (Table 4-1 gives none for M-1/M-2); corner-side landscape strip 10 ft (Table 4-4) (medium) |  |
| residential adjacency buffer ft | side: 50; rear: 100; plus improvement setback: 10 | side: 50; rear: 100; plus improvement setback: 10 |  |
| height based extra setback | at least 12 additional feet for each foot of building height in excess of 50 feet when adjacent to a residential district (medium) | at least 12 additional feet for each foot of building height in excess of 50 feet when adjacent to a residential district (medium) |  |
| max height ft | 100 ft or 8 stories, whichever is higher | 100 ft or 8 stories, whichever is higher |  |
| max lot coverage pct | null | null |  |
| max far | null | null |  |
| landscape strip front ft | 10 | 10 |  |
| landscape strip side corner ft | 10 | 10 |  |
| landscape strip interior side ft | 0, or 5 where the lot line adjoins a less intense non-residential (except AG-1) district | 0, or 5 where the lot line adjoins a less intense non-residential (except AG-1) district |  |
| landscape strip rear ft | 0, or 5 where the lot line adjoins a less intense non-residential (except AG-1) district | 0, or 5 where the lot line adjoins a less intense non-residential (except AG-1) district |  |
| parking warehouse | 1 space per 2,000 sq ft (warehousing and storage / distribution centers); manufacturing 1 per 1,000 sq ft | 1 space per 2,000 sq ft (warehousing and storage / distribution centers); manufacturing 1 per 1,000 sq ft |  |
| parking location industrial | No off-street parking within 25 ft of any property line adjoining a residential use or district | No off-street parking within 25 ft of any property line adjoining a residential use or district |  |
| loading warehouse | Manufacturing/warehousing/wholesaling: 1 (<15,000 sf), 2 (15,000-39,999), 3 (40,000-65,000), +1 per each additional 80,000 sf; berth 12x35 ft, 14 ft clear; 1 in 3 must be 12x55 ft when 3+ required; loading areas across from/adjacent to a non-industrial district need a 50-ft landscaped strip | Manufacturing/warehousing/wholesaling: 1 (<15,000 sf), 2 (15,000-39,999), 3 (40,000-65,000), +1 per each additional 80,000 sf; berth 12x35 ft, 14 ft clear; 1 in 3 must be 12x55 ft when 3+ required; loading areas across from/adjacent to a non-industrial district need a 50-ft landscaped strip |  |
| architectural standards | M-1 only: no wood siding; walls visible from a street >=40% Type A (brick/stone/split-face block/tilt-up or precast concrete fluted or exposed-aggregate/stucco), <=60% Type B (finished metal panel), <=25% plain concrete, <=10% corrugated metal; walls >25 ft high may get special percentages from the Director. | No M-2-specific material standard found in Art. 4 (Sec. 404.10 is M-1 only); FIB overlay (Sec. 510) applies inside its boundary. (medium) |  |
| industrial buffer owed to AG1 neighbour |  |  | side: 50; rear: 100 |

Notes for M-1 (M-1 Light Industrial (formerly M-1A Industrial Park; Table 2-1 Sec. 203)):
- Source is the Zoneomics mirror of the City of South Fulton Zoning Ordinance (City Code Appendix C); Municode (library.municode.com/ga/south_fulton) returned 403 to automated fetches. Latest amendment shown on the mirror: Ord. No. 2026-011, 4-28-2026.
- Sec. 402.05: only one principal building per lot of record - multi-building campuses need subdivision or a variance/rezoning condition.
- Sec. 405.01(e)(3): City Council may impose larger buffers/setbacks as zoning conditions; conditions of zoning on a parcel override these defaults.
- Overlays may add rules: Fulton Industrial Business District Overlay (Sec. 510), South Fulton Parkway Overlay (Sec. 513), Old National Hwy (511), Cascade (506), Sandtown (512), Cedar Grove (507/508), Cliftondale (509), Red Oak (514).
- Sec. 302.48 data center rules exist (min 20 ac, 100 ft residential buffer); a data-center moratorium (RES2025-094) was extended to Sept 9, 2026 - not a warehouse moratorium.

Notes for M-2 (M-2 Heavy Industrial):
- Source is the Zoneomics mirror of the City of South Fulton Zoning Ordinance (City Code Appendix C); Municode (library.municode.com/ga/south_fulton) returned 403 to automated fetches. Latest amendment shown on the mirror: Ord. No. 2026-011, 4-28-2026.
- Sec. 402.05: only one principal building per lot of record - multi-building campuses need subdivision or a variance/rezoning condition.
- Sec. 405.01(e)(3): City Council may impose larger buffers/setbacks as zoning conditions; conditions of zoning on a parcel override these defaults.
- Overlays may add rules: Fulton Industrial Business District Overlay (Sec. 510), South Fulton Parkway Overlay (Sec. 513), Old National Hwy (511), Cascade (506), Sandtown (512), Cedar Grove (507/508), Cliftondale (509), Red Oak (514).
- Sec. 302.48 data center rules exist (min 20 ac, 100 ft residential buffer); a data-center moratorium (RES2025-094) was extended to Sept 9, 2026 - not a warehouse moratorium.
- M-2 Table 2-4 rows: 'Wholesale Trade, no Showrooms' A; 'Wholesaler with Retail Outlet' M-1 only.

Notes for AG-1 (Agricultural District (only use status checked)):
- AG-1 land is a rezoning play, not a by-right warehouse site.

## City of Fairburn

- Ordinance: Fairburn Code of Ordinances Ch. 80 Zoning (Art. II districts, Art. IX parking, Art. X buffers)
- URL: https://library.municode.com/ga/fairburn (official, not fetched); read from https://www.zoneomics.com/code/fairburn-GA/chapter_2
- As of: Mirror read 2026-11-05; latest amendment seen Ord. No. 2025-03 (2-24-2025)
- Source quality: third-party mirror (Zoneomics), not cross-checked

| Rule | M-1 | M-2 | PD |
|---|---|---|---|
| warehouse distribution | permitted by right | permitted by right | not permitted (medium) |
| truck terminal | use permit (low) | use permit |  |
| manufacturing light | permitted by right | permitted by right |  |
| outdoor storage accessory | permitted with standards | permitted with standards |  |
| min lot area sqft | 43560 | 87120 |  |
| min lot frontage ft | 100 | 30 (low) |  |
| front setback ft | 35 | 40 |  |
| side setback ft | 20 | 20 |  |
| rear setback ft | 30 | 30 |  |
| corner lot rule | null | null |  |
| residential adjacency buffer ft | side rear yard abutting residential: 50; buffer abutting other districts: 50; buffer along all public ROW: 25 (medium) | side rear yard abutting residential: 50; buffer abutting other districts: 50; buffer along all public ROW: 25 (medium) |  |
| height based extra setback | null | null |  |
| max height ft | 48 | 48 | 48 |
| max lot coverage pct | 60 | 60 |  |
| max far | null | null |  |
| landscape strip front ft | 25 | 25 |  |
| parking warehouse | 1 standard space per 2,500 sq ft of warehouse/storage floor area for first 150,000 sf + 1 per 7,500 sf above; plus 1 oversized (truck) space per 5,000 sf of gross warehouse space; office >10% of floor area at office rate | 1 standard space per 2,500 sq ft of warehouse/storage floor area for first 150,000 sf + 1 per 7,500 sf above; plus 1 oversized (truck) space per 5,000 sf of gross warehouse space; office >10% of floor area at office rate |  |
| loading warehouse | Industrial: 1 for first 25,000 sf; 2 (25,000-99,000); 3 (100,000-159,999); 4 (160,000-239,000); '3' (240,000-349,999 - apparent typo); +1 per 100,000 sf above 350,000; space 15x60 ft (reducible to 12x45) (medium) | Industrial: 1 for first 25,000 sf; 2 (25,000-99,000); 3 (100,000-159,999); 4 (160,000-239,000); '3' (240,000-349,999 - apparent typo); +1 per 100,000 sf above 350,000; space 15x60 ft (reducible to 12x45) (medium) |  |
| site design | Must be on a collector street or in an industrial park served by a collector; on-site truck queuing; no backing onto street | Must be on a collector street or in an industrial park served by a collector; on-site truck queuing; no backing onto street; loading docks to rear unless completely screened from street |  |
| architectural standards | null (medium) | null (medium) |  |
| min pd size ac |  |  | 5 |

Notes for M-1 (M-1 Light Industrial):
- Source: Zoneomics mirror of Fairburn Code Ch. 80 (Zoning); not cross-checked against an official copy. Latest amendments seen: M-1 Ord. No. 2022-230 (3-28-2022); M-2 Ord. No. 2025-03 (2-24-2025).

Notes for M-2 (M-2 Heavy Industrial):
- Source: Zoneomics mirror of Fairburn Code Ch. 80 (Zoning); not cross-checked against an official copy. Latest amendments seen: M-1 Ord. No. 2022-230 (3-28-2022); M-2 Ord. No. 2025-03 (2-24-2025).

Notes for PD (PD Planned Development (Fairburn)):
- Ord. No. 2023-12 (7-24-2023) latest amendment seen.

## City of Atlanta

- Ordinance: Atlanta Zoning Ordinance, Code of Ordinances Part 16 (Ch. 16 I-1, Ch. 17 I-2)
- URL: https://library.municode.com/ga/atlanta (official, blocked to fetch); read from https://www.zoneomics.com/code/atlanta-GA/chapter_22 and https://www.zoneomics.com/code/atlanta-GA/chapter_24
- As of: Mirror read 2026-11-05; latest amendments seen 2023
- Source quality: third-party mirror (Zoneomics), not cross-checked against an official copy

| Rule | I-1 | I-2 |
|---|---|---|
| warehouse distribution | permitted by right | permitted by right |
| truck terminal | special use permit | special use permit |
| manufacturing light | permitted by right | permitted by right |
| outdoor storage accessory | permitted by right (medium) | permitted by right (medium) |
| min lot area sqft | null | null |
| min lot width ft | null | null |
| min lot frontage ft | null | null |
| front setback ft | 40 | 40 |
| side setback ft | 5 | 10 |
| rear setback ft | null | null |
| corner lot rule | Side yard adjacent to a street = half the required front yard (20 ft) | Side yard adjacent to a street = half the required front yard (20 ft) |
| residential adjacency buffer ft | side: 20; rear: 20; screen: 6-ft opaque fence/screen | side: 20; rear: 20; screen: 6-ft opaque fence/screen |
| height based extra setback | 45-degree transitional height plane starting 35 ft above the required setback/transitional yard line where contiguous to a protected district (R-1..R-5, RG, MR, and similar PD/SPI/historic); if within 150 ft but not contiguous, plane starts 15 ft above the protected district lot line | 45-degree transitional height plane starting 35 ft above the required setback/transitional yard line where contiguous to a protected district (R-1..R-5, RG, MR, and similar PD/SPI/historic); if within 150 ft but not contiguous, plane starts 15 ft above the protected district lot line |
| max height ft | null | null |
| max lot coverage pct | null | null |
| max far | 2.0 | 2.0 |
| landscape strip front ft | null | null |
| parking warehouse | 1 space per 600 sq ft or per 2 employees on peak shift, whichever is greater (manufacturing, warehousing, distribution centers) | 1 space per 600 sq ft or per 2 employees on peak shift, whichever is greater (manufacturing, warehousing, distribution centers) |
| architectural standards | null (medium) | null (medium) |

Notes for I-1 (I-1 Light Industrial):
- Source: Zoneomics mirror of Atlanta Zoning Ordinance Part 16; Municode blocked. Latest amendment seen in Ch. 16: Ord. No. 2023-01(22-O-1209).
- Atlanta 'Zoning 2.0' rewrite: Draft V2 comment period closed Apr 8, 2026; not adopted as of last check. If adopted, I-1/I-2 will be converted to new districts - re-check before relying.
- Suffix '-C' (e.g. I-1-C): Sec. 16-02.003(2) - district 'zoned conditional under previous ordinance amendments by the council'; conditions remain in effect and are obtained from the Clerk of Council. Treat every -C parcel as having site-specific conditions that may override these defaults (https://www.zoneomics.com/code/atlanta-GA/chapter_2).

Notes for I-2 (I-2 Heavy Industrial):
- Source: Zoneomics mirror of Atlanta Zoning Ordinance Part 16; Municode blocked. Latest amendment seen in Ch. 16: Ord. No. 2023-01(22-O-1209).
- Atlanta 'Zoning 2.0' rewrite: Draft V2 comment period closed Apr 8, 2026; not adopted as of last check. If adopted, I-1/I-2 will be converted to new districts - re-check before relying.
- Suffix '-C' (e.g. I-1-C): Sec. 16-02.003(2) - district 'zoned conditional under previous ordinance amendments by the council'; conditions remain in effect and are obtained from the Clerk of Council. Treat every -C parcel as having site-specific conditions that may override these defaults (https://www.zoneomics.com/code/atlanta-GA/chapter_2).

## City of Union City

- Ordinance: Union City, Georgia Zoning Ordinance (city-maintained reference copy, Feb 18, 2025 Rev.)
- URL: https://www.unioncityga.gov/files/assets/city/v/1/community-development/documents/documents-amp-applications/zoningordinance022025.pdf
- As of: February 18, 2025 Rev. (cover states it is an unofficial reference copy; confirm with Community Development)
- Source quality: official city website PDF (self-described unofficial reference copy)

| Rule | M-1 |
|---|---|
| warehouse distribution | permitted by right |
| truck terminal | permitted by right (medium) |
| manufacturing light | permitted by right |
| outdoor storage accessory | permitted with standards |
| min lot area sqft | 30000 |
| min lot width ft | 150 |
| min lot frontage ft | 150 |
| front setback ft | 50 |
| side setback ft | 20 |
| rear setback ft | 20 |
| corner lot rule | null |
| residential adjacency buffer ft | buffer: 75; plus setback measured from buffer: 20 |
| height based extra setback | null |
| max height ft | 75 |
| max lot coverage pct | 80 |
| max far | null |
| landscape strip front ft | 10 (medium) |
| parking warehouse | 1 space per employee + 1 per company vehicle operating from the premises |
| loading warehouse | 1 per 50,000 sq ft GFA (or fraction); berth 12x35 ft, 14 ft clear; truck terminals: enough for max trucks stored/loaded at once |
| architectural standards | Sec. 5-41 requires 80% brick/stone on street-facing facade of 'commercial buildings' - unclear whether it applies to industrial buildings (low) |

Notes for M-1 (M-1 Light Industrial):
- Overlays not reviewed: Urban Design Overlay (Sec. 7-5) and Roosevelt Highway Corridor Overlay (Sec. 7-6).
- Data Centers added as permitted M-1 use by Ord. No. 2023-05 (8/22/23).

## Fulton Industrial District (FID) - which code applies

- Ordinance: For annexed FID parcels: City of South Fulton Zoning Ordinance (see City of South Fulton entry) incl. Sec. 510 Fulton Industrial Business District Overlay. For any parcel still unincorporated: Fulton County Zoning Resolution Art. X/IV/XVIII.
- URL: https://www.fultoncountyga.gov/-/media/Departments/Public-Works/Planning-Zoning-and-Permits/Zoning-Resolution/art10.pdf
- As of: read 2026-11-05

**Which code applies (medium confidence):** Most of the Fulton Industrial Boulevard area was annexed into the City of South Fulton by HB 445 (2021), signed by Gov. Kemp May 10, 2021. HB 445 amends the South Fulton charter (Sec. 1.11(b)) to add the territory; it contains no zoning-transition clause (only Sec. 1 boundaries and Sec. 2 repealer). Parcels inside the annexed area are governed by the City of South Fulton Zoning Ordinance, whose Sec. 510 'Fulton Industrial Business District Overlay' now carries the FIB standards, and whose district conversion table (Table 2-1) maps the old county M-1A to M-1. The area north of I-20 around Fulton County Executive Airport/Brown Field was NOT in HB 445 (AJC May 2021: plan was for it to go to Atlanta; airport land reportedly to remain unincorporated). If any of our parcels fall there and are still unincorporated, the Fulton County Zoning Resolution applies.

*Caveat:* Could not confirm whether the north-of-I-20 remnant was later annexed (by Atlanta or South Fulton) after 2021. Decide per parcel from the city-limits GIS layer, not the FID name. The enrolled/final HB 445 text was not read (LC version only).

Sources: https://www.legis.ga.gov/api/legislation/document/20212022/197554 (HB 445, LC 47 0808, Sections 1-2); https://www.ajc.com/news/atlanta-news/south-fulton-gets-fulton-industrial-spells-end-for-unincorporated-land/B7QHR2JLZZAJXF67VIOVS762OQ; https://www.fultonneighbor.com/news/new-state-law-allows-south-fulton-to-annex-part-of-fulton-industrial-area/article_b298d2f1-1072-509c-8129-d84ef024e1b4.html; https://www.zoneomics.com/code/south-fulton-GA/chapter_5 Sec. 510

**South Fulton FIB Overlay (https://www.zoneomics.com/code/south-fulton-GA/chapter_5 Sec. 510.03-510.07):**
- Applies to all non-single-family property in the overlay; COE/architectural review before LDP or building permit (Sec. 510.04, 10 working-day community review).
- All properties: refuse areas >=50 ft from residential, enclosed in masonry-type walls; all utilities underground; facade variation on ROW-facing walls; office portion of industrial buildings at the front, facing the ROW, with >=40% windows (Sec. 510.05).
- Industrial parcels fronting Fulton Industrial Blvd: 15-ft landscape strip on FIB frontage; outside storage screened from FIB (wall/fence >=50% opacity or 10-ft landscape strip); walls visible from FIB must meet Type A >=40% / Type B <=60% / Type C <=25% / Type D <=10% material mix; no wood siding or reflective glass; no wood fencing (Sec. 510.06).
- Parcels designated 'industrial marketplace' on the future development map: industrially zoned building walls visible from ROW must be stucco, stone, brick or approved alternative (Sec. 510.07(b)(1)) - potentially a significant cost item.

| Rule | M-1 | M-2 | M-1A |
|---|---|---|---|
| warehouse distribution | permitted by right | permitted by right | permitted by right |
| truck terminal | not permitted | permitted by right | not permitted |
| manufacturing light | permitted by right | permitted by right |  |
| outdoor storage accessory | permitted with standards (medium) | permitted with standards |  |
| min lot area sqft | null | null |  |
| min lot frontage ft | 35 | 35 | 35 |
| min lot width ft | null | null |  |
| front setback ft | 40 | 40 | 30 |
| side setback ft | 0 | 0 |  |
| rear setback ft | 0 | 0 |  |
| corner lot rule | null | null |  |
| residential adjacency buffer ft | side: 50; rear: 100; plus improvement setback: 10 | side: 50; rear: 100; plus improvement setback: 10 |  |
| height based extra setback | 12 additional feet for each foot of height in excess of 50 feet when adjoining a Dwelling District | Any part shall be set back 12 feet from the required yard lines for each foot of height in excess of 50 feet (adjoining a Dwelling District) |  |
| max height ft | higher of 8 stories or 100 ft | higher of 8 stories or 100 ft | higher of 4 stories or 60 ft |
| max lot coverage pct | null | null | 70 |
| max far | null | null |  |
| landscape strip front ft | 10 | 10 |  |
| landscape strip side corner ft | 10 | 10 |  |
| landscape strip interior side ft | 0/5 (5 where adjoining a less intense non-residential district except AG-1) | 0/5 (5 where adjoining a less intense non-residential district except AG-1) |  |
| landscape strip rear ft | 0/5 | 0/5 |  |
| parking warehouse | 1 per 2,000 sq ft (warehousing & storage, distribution centers); industrial/manufacturing 1 per 1,000 sq ft | 1 per 2,000 sq ft (warehousing & storage, distribution centers); industrial/manufacturing 1 per 1,000 sq ft |  |
| parking location industrial | No off-street parking within 25 ft of a property line adjoining a residential use or district | No off-street parking within 25 ft of a property line adjoining a residential use or district |  |
| loading warehouse | 1 (<15,000 sf), 2 (15,000-39,999), 3 (40,000-65,000), +1 each additional 80,000 sf | 1 (<15,000 sf), 2 (15,000-39,999), 3 (40,000-65,000), +1 each additional 80,000 sf |  |
| architectural standards | null (medium) | null (medium) | Type A >=40% etc. (same table as South Fulton Sec. 404.10) |

Notes for M-1 (M-1 Light Industrial (Fulton County)):
- Official PDF (Fulton County). Article X adopted 3-7-1990 with amendments noted in text (latest seen 05/05/10). Article IV adopted 10-5-1988 w/ amendments; Article XVIII adopted 4-5-1989 w/ amendments.
- cm.fultoncountyga.gov host returned 503 / TLS error; same file served at www.fultoncountyga.gov.

Notes for M-2 (M-2 Heavy Industrial (Fulton County)):
- Official PDF (Fulton County). Article X adopted 3-7-1990 with amendments noted in text (latest seen 05/05/10). Article IV adopted 10-5-1988 w/ amendments; Article XVIII adopted 4-5-1989 w/ amendments.
- cm.fultoncountyga.gov host returned 503 / TLS error; same file served at www.fultoncountyga.gov.

Notes for M-1A (M-1A Industrial Park (Fulton County; converted to South Fulton M-1)):
- Only relevant if the GIS layer still shows M-1A on unincorporated parcels.

## Fulton County (unincorporated) - see FID entry

- Ordinance: Fulton County Zoning Resolution
- URL: https://www.fultoncountyga.gov/-/media/Departments/Public-Works/Planning-Zoning-and-Permits/Zoning-Resolution/art10.pdf
- As of: Art. X adopted 3-7-1990 with amendments
- District data is under the 'Fulton Industrial District' entry.

## Building and fire codes (state minimums)

- **Editions:** 2024 IBC with Georgia Amendments, 2024 IFC as adopted by the Safety Fire Commissioner (plus 2024 IRC/IMC/IPC/IFGC/ISPSC, 2023 NEC with 2026 GA amendments), effective January 1, 2026. Source: https://dca.georgia.gov/document/document/new-codes-and-amendments-effective-january-1-2026-memo/download (DCA memo, Dec 1, 2025; O.C.G.A. § 8-2-23)

**IBC 507 unlimited-area buildings** (medium; IBC 2024 Sec. 507 (codes.iccsafe.org returned 403; values from general knowledge of IBC 2018-2024 text and secondary sources - VERIFY)):
- 507.4: one-story Group B, F, M or S buildings of any construction type, sprinklered throughout (NFPA 13), are unlimited in area when surrounded and adjoined by public ways or yards >= 60 ft wide.
- 507.3: nonsprinklered one-story F-2 / S-2 (low-hazard) buildings also unlimited with 60-ft yards.
- 507.5: two-story B, F, M, S sprinklered buildings unlimited with 60-ft yards.
- 507.2.1 (reduced open space): 60 ft may drop to >= 40 ft along <= 75% of the perimeter if that exterior wall has a 3-hour fire-resistance rating and its openings have 3-hour opening protectives.
- The 60-ft yard may be on the same lot or permanently secured (e.g. easement/public way); it acts like a 60-ft building setback from lot lines/other buildings for large boxes.

**Fire apparatus access** (medium; IFC Sec. 503 as modified by Ga. Comp. R. & Regs. 120-3-3 - text read from https://oci.georgia.gov/document/document/120-3-3pdf/download (this copy still cites the 2018 IFC; the 2026 re-adoption on the 2024 IFC should be checked for changes)):
- GA 503.1.1 (replaces IFC text): access roads must comply with Sec. 503 AND Appendix D (so Appendix D is effectively mandatory in Georgia), and extend to within 150 ft of all portions of the exterior wall of the first story, measured by an approved route around the building.
- Exception: local Fire Chief/Fire Code Official may increase the 150 ft where the building is fully sprinklered (903.3.1.1/.2/.3), where terrain prevents roads and alternative protection is provided, etc. No fixed sprinklered distance (the often-quoted 450 ft is not in this text).
- IFC 503.2.1: min 20 ft unobstructed width, 13 ft 6 in vertical clearance (IFC model text - verify 2024).
- IFC 503.2.5: dead-end access roads > 150 ft need an approved turnaround.
- IFC Appendix D105 (aerial access; model text - verify 2024): buildings with eave/parapet/roof height > 30 ft need an aerial fire apparatus access road, 26 ft min unobstructed width, located 15-30 ft from the building along at least one entire long side, no overhead utility lines over it.
- IFC Appendix D104 (model text - verify): commercial buildings > 3 stories or 30 ft high, or > 62,000 sq ft (124,000 sq ft if sprinklered), need two separate access roads; GA D107.2 adds the remoteness rule (>= 1/2 the max diagonal) where two are required.
- GA 504.1.1: one exterior access door per 100 linear ft of wall facing access roads, or per 200 ft for walls with continuous dock doors; 3 ft x 6 ft 8 in; rolling doors OK if sprinklered.
- GA 507.5.1: on-site hydrants where any part of the building is > 500 ft from a hydrant on an access road (600 ft if sprinklered).

## Needs human verification

1. **Mirror sources.** South Fulton, Atlanta and Fairburn text came from the Zoneomics mirror because Municode blocked automated fetches with 403. Check each value against Municode. South Fulton buffer values were cross-checked against official staff report Z24-029 and the official county text.
2. **12 ft of extra setback per foot of height above 50 ft** (South Fulton Table 4-1 fn 19; Fulton County 10.2.3.A / 10.3.3.A). Confirm the wording and how staff apply it next to residential.
3. **South Fulton truck terminal status.** No use-table row exists. Get a Director use determination under Sec. 207.05. The truck-parking-lot standards are codified as Sec. 302.49, while the use table references 302.48.
4. **Fairburn.** M-1 lists truck terminals as both a permitted use (c)(47) and a use-permit use (e). M-2 minimum lot frontage reads 30 ft against 100 ft in M-1. The industrial loading schedule has a '3 spaces' step that looks like a typo. 'Abut all other zoning districts' in the Sec. 80-372 buffer is ambiguous.
5. **Atlanta.** The I-2 height section refers to itself (16-17.008), which looks like a codification error. Check that no citywide parking reform has changed the 1 per 600 sf minimum. Loading (16-28.015) and landscaping/tree rules were not reviewed. Confirm Zoning 2.0 has not been adopted.
6. **Union City.** The PDF says it is an unofficial reference copy. Confirm 'truck transportation' covers truck terminals and whether Sec. 5-41 (80% brick/stone) applies to industrial buildings. The overlays (Sec. 7-5, 7-6) were not reviewed.
7. **FID.** Check whether the area north of I-20 was annexed after 2021, and read the enrolled text of HB 445. Fulton County Sec. 4.2 does not name M-1 in its outdoor-storage yard sentence.
8. **Building code.** ICC pages were blocked, so the IBC 507 and IFC 503.2.1/503.2.5/Appendix D values are model-code text from knowledge and secondary sources. Confirm them in the 2024 editions. Confirm that the 2026 re-adoption of GA Rule 120-3-3 kept the GA 503.1.1, 504.1.1 and 507.5.1 changes; the copy read still cites the 2018 IFC.
9. **Not covered:** conditions of zoning on individual parcels (any '-C' or conditionally zoned parcel), stream buffers, tree ordinances, watershed rules and airport height zones.

### All medium/low-confidence values

- City of South Fulton M-1 `truck_terminal` (low): No 'truck terminal' / 'freight trucking' row found. Related rows: 'Commercial truck parking lot' (NAICS 812930) = A in M-1 and M-2 subject to Sec. 302.48 (codified as 302.49: min 5 ac, 250 ft from residential, 10 ft opaque fence + 10 ft evergreen buffer, 25 ft from ROW); 'Fleet Storage' = A; 'Truck Stops' = S (special use). A cross-dock truck terminal may be classified by the Director under Sec. 207.05 - needs a written use determination.
- City of South Fulton M-1 `corner_lot_rule` (medium): 
- City of South Fulton M-1 `height_based_extra_setback` (medium): Verbatim on the mirror AND identical in the official Fulton County Zoning Resolution Sec. 10.2.3.A / 10.3.3.A (from which this was inherited), so it is not a mirror typo; but it is extreme (a 60-ft building would need +120 ft). Human should confirm against Municode and ask staff how it is applied.
- City of South Fulton M-2 `truck_terminal` (low): No 'truck terminal' / 'freight trucking' row found. Related rows: 'Commercial truck parking lot' (NAICS 812930) = A in M-1 and M-2 subject to Sec. 302.48 (codified as 302.49: min 5 ac, 250 ft from residential, 10 ft opaque fence + 10 ft evergreen buffer, 25 ft from ROW); 'Fleet Storage' = A; 'Truck Stops' = S (special use). A cross-dock truck terminal may be classified by the Director under Sec. 207.05 - needs a written use determination.
- City of South Fulton M-2 `corner_lot_rule` (medium): 
- City of South Fulton M-2 `height_based_extra_setback` (medium): Verbatim on the mirror AND identical in the official Fulton County Zoning Resolution Sec. 10.2.3.A / 10.3.3.A (from which this was inherited), so it is not a mirror typo; but it is extreme (a 60-ft building would need +120 ft). Human should confirm against Municode and ask staff how it is applied.
- City of South Fulton M-2 `architectural_standards` (medium): 
- City of Fairburn M-1 `truck_terminal` (low): M-1 text is internally inconsistent (permitted list and use-permit table both list it); assume use permit until staff confirms.
- City of Fairburn M-1 `residential_adjacency_buffer_ft` (medium): Sec. 80-372: 'When C-1, C-2, M-1, or M-2 zoned properties abut all other zoning districts; a 50 feet buffer shall be required' - wording ambiguous (may include other industrial). Sec. 80-369: buffers may be counted within required yards. Buffers must give complete visual obstruction to 12 ft (80-371).
- City of Fairburn M-1 `loading_warehouse` (medium): 
- City of Fairburn M-1 `architectural_standards` (medium): No district material standard found; Hwy 29 and Hwy 74 overlays (Sec. 80-89, 80-90) not reviewed.
- City of Fairburn M-2 `min_lot_frontage_ft` (low): M-2 shows 30 ft vs 100 ft in M-1 - possibly a typo in the code/mirror; verify.
- City of Fairburn M-2 `residential_adjacency_buffer_ft` (medium): Sec. 80-372: 'When C-1, C-2, M-1, or M-2 zoned properties abut all other zoning districts; a 50 feet buffer shall be required' - wording ambiguous (may include other industrial). Sec. 80-369: buffers may be counted within required yards. Buffers must give complete visual obstruction to 12 ft (80-371).
- City of Fairburn M-2 `loading_warehouse` (medium): 
- City of Fairburn M-2 `architectural_standards` (medium): No district material standard found; Hwy 29 and Hwy 74 overlays (Sec. 80-89, 80-90) not reviewed.
- City of Fairburn PD `warehouse_distribution` (medium): Industrial is listed in the PD intent (80-87(a)) but M-1/M-2 uses are not in the permitted-use list, so a warehouse in Fairburn PD would need a rezoning; Fairburn PDs are mixed-residential, not industrial.
- City of Atlanta I-1 `outdoor_storage_accessory` (medium): Principal-use storage yards allowed; general accessory outdoor-storage rules (Ch. 28) not reviewed.
- City of Atlanta I-1 `architectural_standards` (medium): None found in district chapter.
- City of Atlanta I-2 `outdoor_storage_accessory` (medium): Principal-use storage yards allowed; general accessory outdoor-storage rules (Ch. 28) not reviewed.
- City of Atlanta I-2 `architectural_standards` (medium): None found in district chapter.
- City of Union City M-1 `truck_terminal` (medium): 'Truck transportation' read as covering truck terminals - confirm with staff.
- City of Union City M-1 `landscape_strip_front_ft` (medium): 
- City of Union City M-1 `architectural_standards` (low): 
- Fulton Industrial District (FID) - which code applies M-1 `outdoor_storage_accessory` (medium): Rear and side yards only in M-1A; all yards in M-2 (M-1 not named in the yard sentence - ambiguous); >=25 ft from any residential property line; screened from residential in all industrial districts and from streets in M-1A and M-1.
- Fulton Industrial District (FID) - which code applies M-1 `architectural_standards` (medium): No exterior-material standard for M-1 in Art. X (the Type A/B/C/D wall-material table, Sec. 10.1.4, applies to M-1A only). Fulton Industrial Business District overlay (Res. 12-0271, 4-4-12) may apply.
- Fulton Industrial District (FID) - which code applies M-2 `architectural_standards` (medium): No exterior-material standard for M-2 in Art. X (the Type A/B/C/D wall-material table, Sec. 10.1.4, applies to M-1A only). Fulton Industrial Business District overlay (Res. 12-0271, 4-4-12) may apply.
