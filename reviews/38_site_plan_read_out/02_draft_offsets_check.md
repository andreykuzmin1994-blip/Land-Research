# 38 / 02 — The planner's draft offsets against the codes

Asked by the operator on 2026-11-17: "Have you researched building codes
to make sure these work with offsets?"

**Answer: not before this note.** The setbacks, buffers, caps and parking
rules the planner draws under (`JUR_DRAFT` in `screener/siteplan.py`) came
from the research notes of the session that built it. Those notes are not
in git, and nobody had set the table against a code. This note does that
with what can be read today. **No value in `JUR_DRAFT` was changed**, no
jurisdiction is marked verified, and the root's human-owned zoning facts
file was read, not edited. Applying any of this is the operator's call
(operator decision 5 in `SITE_PLANNING.md`).

Two sources were used:

- **A. The repository's own zoning facts** (the root's human-owned file,
  researched 2026-11-05 with a citation per value, status draft). It
  covers South Fulton, Fairburn, Atlanta and Union City.
- **B. New web research** for the three cities that file marks "not
  researched": East Point, College Park, Palmetto. Section B below.

"Looser" means the planner allows more than the source says the code
does, so a yield could be overstated. "Stricter" means the planner is on
the safe side.

## A. Against the repository's zoning facts

### South Fulton, unincorporated Fulton, Fulton Industrial (M-1 / M-2)

| Item | Planner | Facts file | Reads |
|---|---|---|---|
| Front setback | 40 ft | 40 ft | matches |
| Side / rear setback | 10 ft | 0 ft | stricter |
| Residential buffer, side | 60 ft | 50 ft buffer + 10 ft improvement setback | matches |
| Residential buffer, rear | 110 ft | 100 ft + 10 ft | matches |
| Paving strip at the road | 10 ft | 10 ft landscape strip | matches |
| Paving strip, other lines | 10 ft | 0 or 5 ft | stricter |
| Height | 100 ft | 100 ft or 8 storeys | matches |
| Coverage | none | none | matches |
| Parking | 1 / 2,000 SF + office | 1 / 2,000 SF (warehouse) | matches |

- **Looser, by what the planner cannot see:** the buffer is triggered by
  AG-1 neighbours as well as residential ones. The planner reads
  "residential" from county land-use codes, so vacant AG-1 land next
  door gets no buffer. Check the neighbours' zoning by hand.
- **Holds only at the planner's heights:** the code adds 12 ft of setback
  for each foot of building height over 50 ft beside a residential
  district. The planner's buildings are 42 to 46 ft, so it does not bite.
  A 40 ft clear building would bring it in.
- Staff have applied 1 / 1,000 SF (manufacturing) to some warehouses:
  operator decision 1.

### Fairburn (M-1 / M-2)

| Item | Planner | Facts file | Reads |
|---|---|---|---|
| Front setback | 40 ft | 35 ft (M-1), 40 ft (M-2) | matches / stricter |
| Side setback | 25 ft | 20 ft | stricter |
| Rear setback | 25 ft | 30 ft | **looser by 5 ft** |
| Residential buffer | 50 ft | 50 ft | matches |
| Buffer along the road | 25 ft paving | 25 ft | matches |
| Buffer to other districts | not drawn (flagged) | 50 ft, wording ambiguous | **looser where it applies** |
| Height | 48 ft | 48 ft | matches |
| Coverage | building 60% | 60% | matches |
| Car parking | Fairburn formula | 1 / 2,500 SF to 150,000 SF, then 1 / 7,500 | matches |
| Truck parking | planner's trailer target (0.5 per door under 250,000 SF, about 1 per 10,000 SF) | 1 oversized (truck) space per 5,000 SF of warehouse | **looser: about half** |

- The planner uses one "other lot line" offset; the code has 20 ft at the
  side and 30 ft at the rear. A building against a rear line that is not
  residential sits 5 ft too close.
- The truck-space rule is the largest gap: it is a code minimum, and the
  planner's trailer count is a market target that can sit below it.
- M-2 wants docks at the rear unless fully screened from the street
  (`STREET_FACING_DOCKS` flags the case). Collector frontage is flagged.

### Atlanta (I-1 / I-2)

| Item | Planner | Facts file | Reads |
|---|---|---|---|
| Front setback | 40 ft | 40 ft | matches |
| Side setback | 10 ft | 5 ft (0 if built to the line) | stricter |
| Rear setback | 10 ft | none | stricter |
| Transitional yard to residential | 20 ft, no paving | 20 ft, not paved | matches |
| Height | none | none, but a 45-degree plane from 35 ft above the yard line beside a protected district | **looser near homes** |
| Coverage | none | none (FAR 2.0) | matches |
| Parking | market ratio; 1 / 600 SF not applied | 1 / 600 SF "minimum" | flagged (`ATL_PARKING_CODE`) |

- **Looser:** beside a residential district a 42 to 46 ft building has to
  stand 27 to 31 ft from the line to stay under the height plane. The
  planner lets it stand at 20 ft.
- Corner lots: the side yard on a street is 20 ft; the planner uses 40 ft
  on every street (stricter).

### Union City (M-1)

| Item | Planner | Facts file | Reads |
|---|---|---|---|
| Front setback | 50 ft | 50 ft | matches |
| Side / rear setback | 20 ft | 20 ft | matches |
| Residential buffer | 95 ft building, 75 ft paving | 75 ft buffer + 20 ft setback from it | matches |
| Height | 75 ft | 75 ft | matches |
| Coverage | building + parking 80% | 80% | matches |
| Parking | floor area / 2,500 | 1 per employee + 1 per company vehicle | a proxy |

- **Looser, by what the planner cannot see:** agricultural districts
  trigger the 75 ft buffer too (as in South Fulton).

## B. East Point, College Park, Palmetto

Researched on the web on 2026-11-17 by a subagent of this session. **The
result is thin and none of it is from a current official code text.**
Only Palmetto could be set against a full ordinance, and that copy is
from 2013. Treat every line as a lead to check, not a finding.

Sources: [P] a 2013 PDF of the Palmetto Zoning Ordinance on a third-party
host; [E] the eLaws mirror of East Point's zoning, current to January
2019; [A] official East Point hearing agendas of 2025 and 2026; [S] a
search-engine excerpt, page not opened. The College Park and Palmetto
city sites refused automated requests.

### East Point (I-1, I-2, B-P)

| Item | Planner | Source says | Reads |
|---|---|---|---|
| Warehouse use | use permit in I-1 / I-2; 5,280 ft from residential (a gate) | [A] I-1: a use permit plus a variance from the one-mile rule, in hearings of 2025-26. I-2 not confirmed. The 2019 text [E] allowed it by right. | matches for I-1 |
| Front setback | 40 ft | [E] 40 ft (I-1, I-2); 45 ft (B-P) | matches; looser in B-P |
| Side setback | 8 ft | [E] 8 ft | matches |
| Rear setback | 8 ft | [E] **30 ft** | **looser by 22 ft** |
| Truck court and loading strip | 50 ft toward residential | [E] a 50 ft landscaped strip beside **any non-industrial district** | **looser beside commercial land** |
| Car parking toward residential | 50 ft | [E] 25 ft | stricter |
| Height / coverage | 40 ft / none | [E] 40 ft / none listed | matches |
| Residential buffer width, parking ratio | 50 ft; 1 / 2,000 SF | tables missing from the mirror | not found |

- The 8 ft rear offset is the planner's "other lot lines" value; the 2019
  text has 30 ft at the rear.
- East Point is rewriting its code; adoption is aimed at March 2027.

### College Park (M-1 / M-2)

Nothing dimensional was found. Every College Park value in the planner
(50 / 40 / 40 / 25 ft, 35 ft height, 70% impervious) is unchecked.

- [secondary] An ordinance of May 2024 put a 120-day moratorium on
  applications for warehousing and distribution centers, pending a text
  amendment. What replaced it was not found. **Ask before trusting any
  warehouse fit there.**
- [S] The parking rule reads "1 per 2 employees on the largest shift, or
  10% of the gross floor area, whichever is greater". The planner uses
  only the floor-area half.

### Palmetto (M-1 / M-2), 2013 text

| Item | Planner | Source says [P] | Reads |
|---|---|---|---|
| Warehouse use | no gate | permitted in M-1 and M-2 | matches |
| Front / side / rear | 50 / 20 / 20 ft | 50 / 20 / 20 ft | matches |
| Residential buffer | 50 ft | 50 ft with a 6 ft screen | matches |
| Buffer beside commercial | 10 ft paving strip | 20 ft (C-1, C-2), 10 ft (C-3, C-4) | **looser beside C-1 / C-2** |
| Arterial corridor overlay (US 29, SR 154, Weldon, Johnson, Tommy Lee Cook roads) | 50 ft building, 10 ft paving | **95 ft building** (80 with one parking bay), **35 ft paving** | **looser on those roads** |
| Coverage / height | building 70% / 40 ft | 70% / 40 ft, 3 storeys | matches |
| Parking | floor area / 1,500 | 1 per employee or 1 per 1,500 SF, whichever is greater | matches but for the employee test |
| Dock doors | no rule | may not face a public right-of-way unless the building is 200 ft back | no rule (`STREET_FACING_DOCKS` flags within 100 ft) |

- [S] An ordinance of February 2026 rewrote the industrial article and
  was not read. Every row may be out of date.
- A watershed overlay caps impervious cover at 25% where it applies.

### What was not read, and a mistake to record

This repository does not read Zoneomics by machine: the operator reviewed
its terms, which ban scraping, and `rules_scrape.py` blocks the host
(`reviews/24_rules_local_files/`). Municode refuses automated requests.
The brief I gave the research subagent named both as places to try. That
was my error: I had not read review 24. The subagent found the rule and
read neither site, but it had already sent **five status-only requests
(three to Zoneomics, two to Municode)**; no content was kept or read. A
printout of East Point's code on a CoStar-owned host was not opened.

The repository's zoning facts file (section A) cites the Zoneomics mirror
for South Fulton, Fairburn and Atlanta. That research predates the
operator's decision; section A read the file, not the mirror.

## C. Building and fire code

These are not zoning. They come from the state-adopted building and fire
codes as the repository's facts file reads them (confidence "medium"
there: verify with the building official and the fire marshal).

1. **Open yard round the building.** A one-storey sprinklered warehouse
   over the code's tabular area is built under the unlimited-area rule,
   which wants a yard or public way 60 ft wide all round; 40 ft is allowed
   along part of the perimeter with 3-hour walls. The planner did not
   check this. Its "cars at the office end" section has a 31 ft fire lane
   on the office side, and with the 10 ft strip a building can stand 41 ft
   from a lot line; its end roads are 40 ft, so 50 ft. On synthetic tight
   sites the narrowest yard came out at 41 to 45 ft; on the two reference
   sites it is 63 and 92 ft.
   **Done:** the sheet's check now has the row "Open yard at the building
   (building code)": meets at 60 ft, "limited" from 40 ft (rated walls),
   short below. The layout is not changed. A street counts as open space,
   so the row measures to lot lines that are not a road.
2. **Aerial fire lane.** For a building over 30 ft the fire code's
   appendix asks for a 26 ft road 15 to 30 ft from the building along one
   long side. In the "cars along the office wall" section the 26 ft aisle
   starts 23 ft from the wall: inside the range. In the "cars at the
   office end" section the 31 ft lane starts 5 ft from the wall, so its
   near edge is closer than 15 ft. **Not changed:** how the distance is
   measured is for the fire marshal; it is listed here so it is asked.
3. **Hose reach.** Access within 150 ft of every part of the exterior
   wall. The module rings the building with the court, the end roads and
   the car aisle, so this holds by construction.

The 60 ft and 40 ft figures, the 75% limit and the 3-hour walls were
confirmed on 2026-11-17 against the model building code text (Section
507.4 and 507.2.1, 2021 edition, read in a city's published copy), and
the aerial lane's 30 ft trigger, 26 ft width and 15 to 30 ft distance
against the fire code's appendix D105. The appendix does not say which
edge of the road the distance is measured to. Georgia's amendments to
either section were not checked.

## What would change if the looser values were tightened

**Done on 2026-11-17 at the operator's word ("all of them"):
`03_operator_decisions.md`.** The list as it stood:

- Fairburn: a 30 ft rear offset, and a truck-space minimum of 1 per
  5,000 SF beside the trailer target.
- Atlanta: a height-plane check beside residential land.
- South Fulton and Union City: a way to mark an agricultural neighbour
  as triggering the buffer (the screener's zoning layers have the
  neighbour's district; the planner reads only land-use codes).
- East Point: a 30 ft rear offset, if the 2019 text still stands.
- Palmetto: the corridor overlay's 95 ft and 35 ft on the five roads.
- All: the open yard as a layout rule, not only a row on the sheet; a
  rear offset apart from the side offset (the planner has one "other lot
  lines" value, and three of these codes have a deeper rear yard).
