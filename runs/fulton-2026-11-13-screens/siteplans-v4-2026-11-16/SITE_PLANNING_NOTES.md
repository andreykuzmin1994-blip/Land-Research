# Industrial site planning: screening notes and planner v4

Written 2026-11-16 for the Fulton land screen. These notes cover how the screening test fits are now drawn, what each number rests on, and what the operator still has to decide. Sources:

- **Research spec** (`research/spec.md`): six researched topics, each fact-checked by a second agent; every rule has its source.
- **Critic's review of the spec** (`research/critic.md`).
- **Two engineering reviews of drawn plans** (`research/review_syn.md`, `research/review_syn_v3.md`). Reviewers judged 27 plans each round. In version 2, 12 were not credible. In version 3, 9 were. Version 4 fixes the causes they named.

## What a plan contains now

One explicit cross-section per building, placed on the parcel's open ground inside draft setbacks and buffers:

- **Rear-load, cars along the office wall.** Across the depth: car rows, where the wall-side row's 26 ft aisle doubles as the aerial fire lane; then building depth D; then a 130 ft truck court; then an optional trailer row of 55 ft, sized to the trailer target. Along the length: a 40 ft truck road at each end, with a curbed lead-in beside the car field.
- **Rear-load, cars at the office end.** A 31 ft fire lane runs along the office wall. The car field sits against the office end. A truck road runs at the far end. The court runs the full length, so trucks can enter it from either end.
- **Cross-dock.** Across the depth: trailer row, court, building, court, trailer row. A truck road connects the two courts at one end. The car field sits at the office end.
- **Depths tried.** Rear-load 160, 190, 230, 270 and 310 ft, plus the operator's 260 ft prototype. Cross-dock 470 and 600 ft, plus the operator's 520 ft prototype.
- **Lengths.** Length over depth runs from 2.0 to 4.5. Buildings 190 ft deep or less may go down to 1.6. Lengths step by 20 ft.
- **Doors.** One per 5,000 SF, at 12 ft centres, with no doors in the 60 ft at each end of a dock wall.
- **Trailer target.** 0.5 stalls per door below 250k SF, 1.0 per door up to 450k SF, and 1.3 per door above that.
- **Drop-trailer yard.** Sized to any shortfall, placed near the court, and counted only when a truck route reaches it.
- **Car parking.** The higher of the draft code minimum and the market ratio. The market ratio is 0.55 per 1,000 SF at 250k SF and up, 0.70 per 1,000 from 100k to 250k, and 1.0 per 1,000 below 100k. The last row may be partial. Stalls are 9 × 18 ft, or 10 × 18 ft in Fairburn. South Fulton and Fulton deduct for landscape islands. Atlanta uses the market ratio while its 1/600 SF code minimum is unverified, and the plan carries a flag saying so.
- **Driveways.**
  - Placed on merged runs of GDOT frontage.
  - The truck driveway needs about 190 ft of curb (a 40 ft opening with 75 ft radius returns) and sits at least 110 ft from the end of a run.
  - The car driveway sits 250 ft from the truck driveway on local roads, or 300 ft on collectors and arterials (Fulton manual).
  - Each driveway goes nearest its own target: the truck driveway nearest a court end, the car driveway nearest the car field.
- **Routes.**
  - Truck and car routes are found by grid search inside the paving envelope.
  - They avoid wetlands with a 25 ft buffer, ponds, buildings and parking, and cost three times as much on steep ground.
  - The car route may not cross truck pavement. If it has to, the plan fails.
  - A plan with no truck route or no car route is infeasible, and its floor area is not counted.
- **Detention.**
  - Storage: 1.15 × (0.30 × impervious acres + 0.07 × disturbed pervious acres) acre-feet for HSG B, with other soil groups from the spec. This is the TR-55 shortcut on NOAA Atlas 14 depths, ±30%.
  - Pond geometry: 6 ft deep, 4:1 side slopes, length twice the width, 1 ft freeboard and a 15 ft rim.
  - Ponds are placed on low lidar ground near the module and on its side of mapped wetlands. They stay out of water buffers and transmission corridors.
  - One pond up to 10 acre-feet, two up to 20, and three above that.
  - When no pond fits, the plan says underground vaults or a smaller building.
- **Second building.** Placed on land that building 1's module, ponds, drop yards and routes leave free. It gets its own driveways when the frontage allows, its own routes, and detention for its own impervious area.
- **Grading.** One balanced plane over the whole module, from 1 m lidar sampled at 40 ft. Relief and steep share are reported, and both count against a plan in the ranking.
- **Ranking.** Feasible plans first (routes, pond and access all work), then by floor area. Floor area is reduced for steep ground, for relief over 25 ft, and for a trailer shortfall.

## Flags each plan can carry

Severity 1 means the result is likely killed or cut. Severity 2 means it needs verification.

- **Severity 1:** `ACCESS_NONE` (the plan is hypothetical), `ROUTE_INFEASIBLE`, `CAR_ROUTE_NONE`, `ACCESS_CONSTRAINED`, `USE_PERMIT_REQUIRED` (East Point I-1/I-2; Chattahoochee Hills), `COVERAGE_OVER_CAP`, and `HEIGHT_CAP` when the clear height would fall below 28 ft.
- **Severity 2:**
  - Access and fire: `FIRE_SECOND_ACCESS`, `DEAD_END_ACCESS`, `SHORT_THROAT`, `ACCESS_SHARED`.
  - Program and layout: `POND_DOES_NOT_FIT`, `TRAILER_SHORT`, `DROP_YARD_UNREACHABLE`, `LOADING_FACES_RES`, `STREET_FACING_DOCKS`.
  - Ground: `GRADING_RISK`, `RELIEF`, `TRANSMISSION_EASEMENT_ASSUMED`, `RESIDENTIAL_EDGE`.
  - Code and data: `COLLECTOR_FRONTAGE` (Fairburn), `ATL_PARKING_CODE`, `ACREAGE_MISMATCH`, `ZONING_DRAFT`, `STREAMS_PIPELINES`.
- **Severity 3:** `BELOW_COMP_BAND` (Georgia comps run 10,500-15,400 SF per gross acre), `LD_OUT_OF_BAND`, `DOOR_DENSITY_HIGH`, `IMPERVIOUS_RATIO`, `TRUCK_ENTRY_OFFICE_SIDE`.

## Still out of scope (labelled on every plan)

- Zoning values are draft. They come mostly from code mirrors, and `zoning_rules.json` is the authority.
- Not modelled: stream centrelines and their buffers, petroleum pipelines, utility capacity, fire flow, sight distance, turn lanes, traffic studies, swept paths, terraces and retaining walls, outfalls, pond maintenance drives, tree replanting, costs.
- Residential edges come from county land-use codes, not zoning. Fairburn's 50 ft buffer to all other districts and the 50 ft loading strip are flagged, not drawn.
- The fit search places each building module at its longest fit and checks four orientations. It can miss layouts that straddle a corridor.

## Decisions only the operator can make

The full list is in the spec, section 5. These change the yield the most:

1. **Prototype gap.** The operator's prototypes cover 104k-166k SF (rear-load 260) and 312k-520k SF (cross-dock 520). They don't cover the 166k-312k SF product, which is typical on 12-25 acres. The market depths above fill that gap in these plans. Should prototypes be added to `building_prototypes.json`?
2. **Trailer target.** Is 0.5 / 1.0 / 1.3 stalls per door right, or does your tenant run drop-heavy?
3. **Car parking.** Should the plans use the code minimum, the market ratio, or the higher of the two? South Fulton staff have applied 1/1,000 SF (manufacturing) rather than 1/2,000 SF (warehouse), which doubles the cars.
4. **Paving in setbacks.** May courts, trailers and parking sit inside zoning setbacks, outside buffers? This matters most on 5-15 acre parcels.
5. **Detention.** Should HSG come from SSURGO or default to B? Full 100-year attenuation, or the 25-year storm with safe conveyance? Is underground detention acceptable at screening?
6. **Existing-built gate.** What counts as built? The built check on the top 250 used aerial review plus footprints. Should that become a screen?
