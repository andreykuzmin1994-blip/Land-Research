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

(Research in progress when this file was first committed; see the next
commit.)

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

## What would change if the looser values were tightened

Not done here; each is a planner change that moves yields and needs its
own review and the replay of `reviews/37`.

- Fairburn: a 30 ft rear offset, and a truck-space minimum of 1 per
  5,000 SF beside the trailer target.
- Atlanta: a height-plane check beside residential land.
- South Fulton and Union City: a way to mark an agricultural neighbour
  as triggering the buffer (the screener's zoning layers have the
  neighbour's district; the planner reads only land-use codes).
- All: the open yard as a layout rule, not only a row on the sheet.
