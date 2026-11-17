# Site planning: how the test fits are drawn, and what is still open

Written 2026-11-16 for the Fulton land screen. This covers
`screener.siteplan` (the planner), its read-out (`siteplan_assess`, added
2026-11-17), `screener.builtcheck` (is it land today?) and the decisions
the operator still has to make. How to run the
tools is in `README.md` § "Site plans and the built check".

**A plan is a screening test fit from public data.** It is not a survey,
a civil design or a zoning determination. Every zoning value in it is a
DRAFT. Use a plan to compare sites and to see what to check first, not to
price a deal.

## Where the numbers come from

The planner is a port of the v4 prototype written in the session of
2026-11-16. Its design rests on:

- **A research spec.** Six topics (massing, doors and trailers, parking,
  fire access, driveways, detention, zoning), each researched and then
  fact-checked by a second agent. Every value carries its source and a
  confidence. Values the checker could not confirm were kept out of the
  defaults, with two kinds of exception named where they are used: the
  detention coefficients for soil groups A and D (the spec gives B and C;
  its D is marked extrapolated, A is not in it), and the draft zoning
  cells the spec is silent on (marked in the zoning table).
- **A critic's review of the spec.** It corrected the cross-sections
  (one explicit section per building type; the 60 ft door-free zone is on
  the wall, not added past it; the wall-side car aisle doubles as the
  aerial fire lane), widened truck roads to 40 ft for bends, and moved
  `FIRE_SECOND_ACCESS` from severity 1 to 2.
- **Two rounds of engineering review of drawn plans**, about 25 plans
  each. In version 2, 12 plans were not credible; in version 3, 9 were.
  The causes named (truck entries on the car side, unreachable drop
  yards counted as provided, routes outside the paving envelope,
  driveways swapped) are fixed in version 4.
- **A Tier 2 review of the port** (`reviews/37_screener_site_planner/`).
  It added fail-closed inputs, a declared feasibility rule, geometry
  checks on every plan, and owner-data guards. With
  `--prototype-behaviour` the port reproduces the prototype's numbers on
  its 33 reference plans (the draft labels, input states and budget rule
  it adds apply in both behaviours); the default carries the fixes, and
  each change from the prototype is to be listed in the review's
  decision note.

The research files were session notes and are not in git. The values
below, and the comments in `siteplan.py`, keep what the code uses and
why.

## Who owns what

- **Zoning values** live in `JUR_DRAFT` in `siteplan.py`, every entry
  marked `draft`. The root system's `zoning_rules.json` is the authority.
  Only a human edits it, between runs, and marking a jurisdiction
  verified is the operator's call. The planner neither reads nor writes
  it.
- **Building prototypes** are read, read-only, from
  `screener/sitefit.py` (`PROTOTYPES`, a copy of the operator's SiteFit
  prototypes). The planner never reads or edits the root's
  `building_prototypes.json`.
- The planner changes no score, grade or rank, and nothing in the run
  reads its output.

## What a plan contains

One explicit cross-section per building, placed on the parcel's open
ground inside the draft setbacks and buffers. The search uses
`sitefit.fit_lengths`, the rectangle search copied from the root site
fit.

### Cross-sections

- **Rear-load, cars along the office wall.** Across the depth: car rows,
  where the wall-side row's 26 ft aisle is also the aerial fire lane;
  then the building, depth D; then a 130 ft truck court; then an optional
  55 ft trailer row, sized to the trailer target. Along the length: a
  40 ft truck road at each end, with a curb between it and the car field.
- **Rear-load, cars at the office end.** A 31 ft fire lane along the
  office wall. The car field sits against the office end and a truck road
  runs at the far end. The court runs the full length, so trucks can
  enter it from either end.
- **Cross-dock.** Across the depth: trailer row, court, building, court,
  trailer row. A truck road joins the two courts at one end. The car
  field sits at the office end; the aerial lane goes on a short side.
- **Depths tried.** Rear-load 160, 190, 230, 270 and 310 ft, and the
  operator's 260 ft prototype. Cross-dock 470 and 600 ft, and the
  operator's 520 ft prototype. Front-load is not drawn.
- **Lengths.** For the market depths, length over depth from 2.0 to 4.5;
  buildings 190 ft deep or less may go down to 1.6. Never shorter than
  220 ft or longer than 1,600 ft. The operator's prototypes use their own
  range from `sitefit.PROTOTYPES` (`min_length_ft` to `L`: 400 to 640 ft
  rear-load, 600 to 1,000 ft cross-dock). Lengths step by 20 ft.

### Doors, trailers and cars

- **Doors.** One per 5,000 SF, at 12 ft centres, with no doors in the
  60 ft at each end of a dock wall. Office is 2% of the floor area.
- **Trailer target.** Stalls per dock door: 0.5 below 250k SF, 1.0 below
  450k SF, 1.3 above. A shortfall goes to a drop-trailer yard near the
  court. The yard counts only when a truck route reaches it; otherwise the
  plan says `DROP_YARD_UNREACHABLE` or `TRAILER_SHORT`.
- **Cars.** The higher of the draft code minimum and the market ratio.
  The market ratio is 0.55 per 1,000 SF at 250k SF and up, 0.70 from
  100k, and 1.0 below 100k. The last row may be partial. Stalls are
  9 × 18 ft, or 10 × 18 ft in Fairburn. South Fulton, Fulton and
  Fairburn deduct for landscape islands. ADA stalls follow the standard table.

### Driveways and routes

- Driveways go on merged runs of GDOT frontage.
- The truck driveway needs about 190 ft of curb (a 40 ft opening with
  75 ft radius returns) and sits at least 110 ft from the end of a run,
  so only a frontage run of at least 220 ft can take it.
- The car driveway sits 250 ft from the truck driveway on local roads,
  300 ft on collectors and arterials (Fulton's driveway manual).
- Each driveway goes nearest its own target: the truck driveway nearest
  a court end, the car driveway nearest the car field.
- Truck and car routes are found by a grid search (20 ft cells, coarser
  on very large sites so the grid stays under 60,000 cells; the cell
  used is recorded as `inputs.route_grid_ft`) inside the paving
  envelope. They avoid wetlands with a 25 ft buffer, ponds,
  buildings and parking, and cost three times as much on steep ground
  (15% or more).
- The car route may not cross truck pavement. A plan with no truck route
  or no car route is not feasible, and its floor area is not counted.
- Every route is checked against its envelope after it is drawn. A route
  more than 5 ft outside it is flagged `ROUTE_OUTSIDE_ENVELOPE` and the
  plan is rejected. The two short joins from a driveway (or entry) point
  to the first grid cell are the throat, not part of the search: they are
  measured by how far they go into what they must not cross (a building,
  the car field for trucks, truck pavement for cars, a water buffer),
  not against the grid's walk area.

### Detention

- **Storage** (acre-feet) = 1.15 × (k_imp × impervious acres + k_perv ×
  disturbed pervious acres). For soil group B, k_imp = 0.30 and
  k_perv = 0.07. Group C 0.23 / 0.10 (from the spec). Group D 0.20 /
  0.10 is the spec's extrapolation and group A 0.30 / 0.05 is not in the
  spec (both unsourced: treat A and D plans as rougher still).
  Disturbed area is taken as 1.35 × impervious.
- This is the TR-55 shortcut applied to NOAA Atlas 14 rainfall. It is
  good to about ±30%. Georgia's stormwater manual sanctions the equation
  only for channel protection, so treat it as a screening number.
- The soil group comes from the run's SSURGO column. A dual group such
  as B/D is read as the letter that gives the larger pond
  (`DUAL_HSG_USE`): with these coefficients that is the drained letter
  (B), because the shortcut nets out the pre-development runoff, and D's
  higher pre-development runoff leaves about 27% less storage. Reading D
  would shrink the pond, which is not conservative. An assemblage whose
  members' groups differ uses the one that gives the larger pond, and
  says so (`HSG_ASSUMED`).
  With no group rated, no soil value, or a soil check that was not run
  (outside the soil screen's top parcels), B is assumed and the plan says
  `HSG_ASSUMED` with which it was. The detention basis names the letter
  used.
- **Pond geometry.** 6 ft deep, 4:1 side slopes, length twice the width,
  1 ft freeboard and a 15 ft rim. One pond up to 10 acre-feet, two up to
  20, three above that.
- Ponds go on low lidar ground near the building, on its side of any
  mapped wetland, outside water buffers and transmission corridors.
- The impervious area, and so the pond, is worked out again after the
  drop-yard links are drawn, and only placed yards count. The ponds are
  then re-sized to that volume, larger or smaller, so the storage
  reported, the pond count and the ponds drawn agree. (If smaller ponds
  do not fit where the larger ones did, the larger ones stay and the
  storage reported is the one they hold.)
- When no pond fits, the plan says `POND_DOES_NOT_FIT`: underground
  vaults or a smaller building.

### Second building, grading and ranking

- **Second building.** Placed on land that building 1, its ponds, yards
  and routes leave free (at least 3.5 acres and 50,000 SF). It gets its
  own driveways when the frontage allows, its own routes, and its own
  detention.
- **Grading.** One balanced plane over the whole module, from USGS 1 m
  lidar sampled every 40 ft. Relief and the steep share are reported.
- **Ranking of alternatives.** Plans that pass their own layout checks
  first (`geometry_ok`, below), then plans with a route, then by floor area.
  Floor area is marked down for steep ground, for relief over 25 ft and
  for a trailer shortfall.
- **Comp band.** Georgia comps run 10,500 to 15,400 SF per gross acre. A
  plan below that says `BELOW_COMP_BAND`.

### When a plan counts as feasible

- `feasible` is true when no severity-1 flag stands, other than an
  entitlement gate. Gates (`USE_PERMIT_REQUIRED`) are listed in `gated`:
  the geometry can work while the use still needs a permit.
- `feasible` is null when an input could not be read. Each input
  (frontage, neighbours, transmission lines, map layers, lidar) carries a
  state: checked, none, not checked or partial. A missing input is never
  read as "nothing there"; a county neighbour record without a shape
  makes the neighbours partial (an unseen residential edge would lose its
  buffer).
- The one exception: when no building fits at all (`NO_FIT`), `feasible`
  is false even with an input missing. A missing input can only make the
  envelope larger (no layer or corridor cut, no residential buffer, a
  road edge read as an ordinary lot line), so it cannot hide a fit.
- A checked frontage with no usable run makes the plan `hypothetical`
  (`ACCESS_NONE`): what would fit if access were bought. When the
  frontage could not be checked, `hypothetical` is null.
- A flag that no other layout of the site can clear blocks the site but
  not the layout (`LENGTH_INDEPENDENT_FLAGS`): a draft height cap
  (`HEIGHT_CAP`; no shorter building would clear it) and a frontage too
  short for a truck driveway (`ACCESS_CONSTRAINED`). They do not shrink
  the building, and they stay out of the layout's own check, the ranking
  of layouts, the rescue below and the second-building search. The plan
  drawn and `total_sf_if_access` are then those of the same site without
  the flag (every College Park plan carries the draft 35 ft cap).
- When the pond or a route blocks every plan the search explored, it
  also tries shorter lengths of those variants and up to 12 of the fits
  it left out (shallower and shorter cross-sections). A larger parcel
  then no longer reads infeasible while a smaller parcel inside it reads
  feasible (the prototype read 700 × 700 ft infeasible next to a
  feasible 600 × 700 ft).
- The alternatives' `geometry_ok` is the layout's own check (routes,
  pond, plan checks, coverage cap), not the site's feasibility.
- An assemblage is planned under its best-ranked member's draft rules.
  Members under other draft rules raise `ASSEMBLAGE_MIXED_RULES` at
  severity 1 (a person decides which code governs); a different zoning
  within one jurisdiction raises it at severity 2; a member not in
  `ranked.csv` raises `ASSEMBLAGE_MEMBER_NOT_RANKED`. Every member's
  entitlement gate applies.
- The search has a time budget per site (900 s by default). A search cut
  short is flagged `PLAN_BUDGET_EXCEEDED` and is not feasible, in both
  behaviours. Cut only in the second-building search, building 1 stands
  (`SECOND_BUILDING_NOT_SEARCHED`); cut only in the extra search for
  smaller layouts, the plan stays as found (`SMALLER_LAYOUTS_NOT_ALL_TRIED`).

## The read-out

Added 2026-11-17 (`siteplan_assess.py`, `reviews/38_site_plan_read_out/`).
A plan used to end in a list of flag names. The read-out says what the
plan means and what to do about it. It is worked out from the finished
plan and changes no layout, flag, feasibility or floor area.

- **Status.** One word per plan, from `feasible`, `gated` and
  `hypothetical`: fits, fits with conditions, gated, hypothetical,
  blocked, incomplete, no fit, not planned. Three flags stand on every
  plan (`ZONING_DRAFT`, `STREAMS_PIPELINES`, `POND_SITE_FROM_LIDAR`):
  they are the limits of the method, so a plan with only those reads
  "fits: standard checks only". A condition blocks the plan only when
  the record says the plan is not feasible, so the read-out agrees with
  the record in both behaviours.
- **Conditions.** Each flag becomes a condition with a workstream
  (zoning, access, fire, grading, stormwater, environmental, utilities,
  program, title, planner inputs), a stage (desk check, before an offer,
  due diligence), an action and what closes it. The table is `PLAYBOOK`.
  A test fails when the planner gains a flag with no entry. The actions
  are the usual next step, written once per flag. They are prompts for
  a person: none was checked against a code or a document.
- **Next steps.** What blocks the plan, then the gate, then the rest by
  stage. An incomplete plan has one step: run it again.
- **Where the land goes.** Gross acres; what the draft setbacks and
  buffers take; what mapped constraints and any test cut take; the net
  paving envelope; the building, truck paving, trailer and car parking
  and ponds; and what is left unused inside the envelope, with its
  largest piece (at least 40 ft wide). An area in two uses is counted
  once, in the order above, so the lines add up to the gross.
- **Draft zoning check.** The building and the module's paving are
  measured to each kind of lot line and set against the draft offsets,
  with the margin. The measure is the distance off the lot line itself,
  not past its ends (plain distance reads short at the end of a line,
  where a residential edge is read 20 ft past the corner; the planner's
  envelope is square at the ends too). A row marked building code is
  not zoning: the open yard round the building under the building
  code's unlimited-area rule (60 ft, or 40 ft with rated walls), which
  most buildings here would be built under. Height (clear height plus the 10 ft roof allowance
  the height cap already used), coverage, parking and use follow.
  Driveways and routes are left out of the paving rows: a driveway has
  to cross the road setback. Every row is against `JUR_DRAFT`.
- **Why this layout.** The search ranks finished layouts by floor area,
  marked down for steep ground under the pad (60% of the steep share),
  for relief past 25 ft (400 SF per ft) and for each trailer stall short
  of the target (2,000 SF). Those numbers were in the code; they are now
  named (`RANKING`) and printed. When a larger layout passed its checks
  but ranks lower, the read-out names it, the gain, and only the
  mark-downs it carries beyond the plan drawn.
- **What the surface pond costs.** When a layout passes every check but
  the pond, the search shortens it until a pond fits. The read-out keeps
  the layout as first tried and reports it (`selection.without_pond`):
  the floor area underground or off-site detention would add, and the
  storage that would have to move. On reference site #21 the plan drawn
  is 58,000 SF and a 96,000 SF layout passed all but the pond.
- **Takeoff.** Heavy paving (truck courts, roads and routes, trailer
  stalls, drop yards), light paving (car parking and drives), earthwork
  for one level pad with its elevation and deepest cut and fill, the
  disturbed area, pond footprint and route lengths. No prices: unit
  costs are the operator's.
- **The pipeline table** (`plans.csv`). One row per site, for sorting
  and for a pipeline total. Count floor area only from rows whose
  status is `fits` or `fits_with_conditions`.

What the read-out is not: a second feasibility rule, a cost estimate, or
a zoning opinion. The search itself is unchanged (see the open items in
the review note for what the read-out shows about it).

## Draft zoning values

Derived from the code mirrors in the research spec (§1.10), with the
prototype's own readings where the spec is silent or differs (marked *
below and listed after the table). Offsets are in feet. "Res side /
rear" is the setback toward residential land, buffer included. None of
these is verified against the adopted code.

| Rules | Building: road / res side / res rear / other | Paving: road / res side / res rear / other | Coverage cap | Height cap | Parking code (draft) |
|---|---|---|---|---|---|
| South Fulton, unincorporated Fulton, Fulton Industrial | 40 / 60 / 110 / 10 | 10 / 60 / 110 / 5 | none | 100 ft | warehouse 1/2,000 SF + office 3/1,000 |
| Fairburn | 40* / 50 / 50 / 25* | 25 / 50 / 50 / 5 | building 60% | 48 ft | Fairburn warehouse formula |
| Union City | 50 / 95 / 95 / 20 | 10 / 75 / 75 / 5 | building + parking 80% | 75 ft | SF / 2,500 (employee proxy) |
| Atlanta | 40 / 20 / 20 / 10 | 10 / 20 / 20 / 0 | none | none (a height plane) | 1/600 SF, not used by default (`ATL_PARKING_CODE`) |
| East Point | 40 / 50* / 50* / 8 | 10 / 50* / 50* / 5 | none* | 40 ft* | as South Fulton |
| College Park | 50 / 40* / 40* / 25 | 10 / 40* / 40* / 5 | impervious 70% | 35 ft | 10% of floor area / 330 |
| Palmetto | 50 / 50 / 50 / 20 | 10 / 50 / 50 / 5 | building 70% | 40 ft | floor area / 1,500 |

- Cells marked * are not the spec's: College Park's residential edge is
  "not found" in the spec (40 ft is the prototype's reading); East
  Point's spec row gives a 25 ft parking and a 50 ft loading strip but no
  residential buffer (50 ft used); the spec's East Point WH-I district
  (75 ft height, 70% impervious) is not told apart, every East Point
  parcel gets 40 ft and no cap; Fairburn's spec gives 35 ft (M-1) / 40 ft
  (M-2) at the road and 20 ft side / 30 ft rear, read here as 40 and 25.
- Paving on other lot lines keeps at least a 10 ft landscape strip
  (`OTHER_EDGE_MIN_PAVE_FT`).
- A jurisdiction with no entry gets South Fulton's rules and the flag
  `JURISDICTION_DEFAULTED`.
- A height cap lowers the clear height to the cap less 10 ft. Below
  28 ft clear the plan says `HEIGHT_CAP` at severity 1.
- East Point I-1/I-2 warehousing and anything in Chattahoochee Hills
  needs a use permit (`USE_PERMIT_REQUIRED`, a gate). The spec names
  I-1 only; gating I-2 too is the prototype's reading (it can only
  over-gate).
- Not drawn, only flagged: Fairburn's 50 ft buffer to all other
  districts (`DISTRICT_BUFFER_NOT_DRAWN` on a Fairburn plan with lot
  lines toward non-residential land), stream buffers
  (`STREAMS_PIPELINES`), and the 50 ft loading strip toward residential
  land (`LOADING_FACES_RES`, a court or trailer row within 150 ft of a
  residential edge).
- Neither drawn nor flagged: the loading strip toward other
  non-industrial land (commercial, institutional). The county land-use
  codes the planner reads tell residential from the rest, not industrial
  from commercial.

## Flags

Severity 1: likely killed or cut. Severity 2: needs checking. Severity 3:
information.

- **Severity 1.**
  - Access: `ACCESS_NONE` (hypothetical), `ACCESS_CONSTRAINED`,
    `ROUTE_INFEASIBLE`, `CAR_ROUTE_NONE`, `ROUTE_OUTSIDE_ENVELOPE`,
    `PLAN_GEOMETRY_CONFLICT`, `ASSEMBLAGE_NOT_CONTIGUOUS`.
  - Program and caps: `POND_DOES_NOT_FIT`, `COVERAGE_OVER_CAP`,
    `HEIGHT_CAP` (below 28 ft clear), `USE_PERMIT_REQUIRED` (a gate).
  - Inputs not read: `FRONTAGE_NOT_CHECKED`, `FRONTAGE_GEOMETRY`,
    `NEIGHBOURS_NOT_CHECKED`, `TRANSMISSION_NOT_CHECKED`,
    `LAYERS_NOT_CHECKED`, `DEM_NOT_CHECKED`, `DEM_PARTIAL`. These make
    `feasible` null.
  - Search: `NO_FIT`, `PLAN_BUDGET_EXCEEDED`, `NOT_FEASIBLE_REASON` (a
    check failed without a named flag; a guard that should not fire).
- **Severity 2.**
  - Access and fire: `FIRE_SECOND_ACCESS`, `DEAD_END_ACCESS`,
    `SHORT_THROAT`, `ACCESS_SHARED`.
  - Program and layout: `TRAILER_SHORT`, `DROP_YARD_UNREACHABLE`,
    `SECOND_DROP_YARD_UNREACHABLE`, `LOADING_FACES_RES`,
    `STREET_FACING_DOCKS`, `HEIGHT_CAP` (28 ft clear or more).
  - Ground: `GRADING_RISK`, `RELIEF`, `TRANSMISSION_EASEMENT_ASSUMED`,
    `RESIDENTIAL_EDGE`.
  - Code and data: `ZONING_DRAFT` (on every plan),
    `JURISDICTION_DEFAULTED`, `ATL_PARKING_CODE`, `COLLECTOR_FRONTAGE`
    and `DISTRICT_BUFFER_NOT_DRAWN` (Fairburn), `ACREAGE_MISMATCH`,
    `STREAMS_PIPELINES`.
  - Search: `SECOND_BUILDING_NOT_SEARCHED`,
    `SMALLER_LAYOUTS_NOT_ALL_TRIED`.
  - Second building: each of the second building's own flags appears
    as `SECOND_<flag>`, at severity 2 or 3 (it never blocks building 1),
    for example `SECOND_DROP_YARD_UNREACHABLE`.
- **Severity 3.** `BELOW_COMP_BAND`, `LD_OUT_OF_BAND`,
  `DOOR_DENSITY_HIGH`, `IMPERVIOUS_RATIO`, `TRUCK_ENTRY_OFFICE_SIDE`,
  `HSG_ASSUMED`, `VOLTAGE_ASSUMED` (a transmission line of unknown
  voltage gets an assumed corridor width), `POND_SITE_FROM_LIDAR`,
  `PARCEL_MULTIPART` (one county parcel in separate pieces, often split
  by a road: its ponds and drop yards are kept on the building's piece).

## Still out of scope

Not labelled item by item on each plan: every drawing carries the
disclaimer that it is a screening test fit, not a survey, civil design
or zoning determination, with draft zoning values and indicative routes,
fire access and grading.

- Zoning values are draft (above).
- Not modelled: stream centrelines and their buffers, petroleum
  pipelines, utility capacity, fire flow, sight distance, turn lanes,
  traffic studies, truck swept paths, terraces and retaining walls,
  outfalls, pond maintenance drives, tree replanting, airport height
  limits, costs.
- Residential edges come from county land-use codes, not zoning.
- The fit search places each building at its longest fit and checks
  four orientations. It can miss a layout that straddles a corridor.
- The building size is the best layout found, not the best there is, and
  it depends on how the parcel sits on the map: the pond and drop-yard
  angles include fixed map angles, the route grid is map-aligned, and the
  search keeps its first fits. The same parcel turned on the map has
  planned from about 87,000 to 115,000 SF (a 600 x 700 ft test
  rectangle), so read a yield as good to about ±30%, like detention, and
  compare close sites by drawing, not by `total_sf` alone.

## The built check

The planner does not check whether a parcel is built: it draws a
greenfield plan on any parcel it is given. Run `screener.builtcheck`
first and plan only parcels it reads as `raw_land` or `partly_usable`
(the viewer shows both side by side). The screen cannot see every
building (a building on a
separate improvements-only tax record, a paved yard, a quarry, a
landfill). `screener.builtcheck` reads three signals for the top
parcels:

- FEMA USA Structures footprints inside the parcel's outline, holes
  filled in. **Built** at 10,000 SF or more in the outline, or 2,500 SF
  or more inside a hole. **Check** from 2,500 SF in the outline, or when
  another tax record lies inside it.
- Other Fulton tax records lying 80% or more inside the outline. One
  with $250,000 or more of improvements makes the parcel built.
- The county's 2024 aerial photo, reviewed by a person or an agent who
  can see it (the screener has no image tooling). That gives
  `land_today`. Where the photo and the footprints disagree, a second
  look decides; until then the parcel is `unclear`. A check with any
  input missing (a failed footprint or records fetch) never gives
  `raw_land`, even when the other input found something.

The thresholds are named constants in `builtcheck.py` and are
placeholders for the operator to set. On the 2026-11-16 check of the top
250: 112 raw land, 80 partly usable, 54 not raw land, 4 unclear (re-run
it with that check's `visual.json` and `--second-looks-from` its
`land_today.json`: the visual file alone holds no second looks, and 73
parcels would read unclear). Most of
what the screen had missed was paved yards, outdoor storage, quarries,
landfills and houses with low improvement values.

## Operator decisions (open)

Decisions 1 to 4 are named constants in `siteplan.py`; parking, Atlanta
parking and the trailer target are also command-line options. Every plan
records them under `operator_decisions` (with the prototypes' W, L and
minimum length), so plans made with different choices are never mixed
(`--merge` refuses). Decision 6 is the draft paving offsets in
`JUR_DRAFT`, decision 8 the thresholds at the top of `builtcheck.py`
(`HOLE_BUILT_SF` and the others). Decision 7 has no switch yet: the
planner uses SSURGO soils (a dual group read by `DUAL_HSG_USE`, recorded
as `dual_hsg`), one storage shortcut and surface ponds only; the
questions stand for you to answer.

1. **Car parking ratio.** `PARKING_RULE = "max"`: the higher of the draft
   code minimum and the market ratio. `MARKET_PARKING_TIERS`: 0.55 per
   1,000 SF at 250k SF and up, 0.70 from 100k, 1.0 below. Option
   `--parking {max,code,market}`. Related: South Fulton staff have
   applied 1/1,000 SF (manufacturing) rather than 1/2,000 SF (warehouse),
   which doubles the cars. Which applies to your product?
2. **Atlanta's parking code.** `ATLANTA_PARKING = "market"`: Atlanta
   plans use the market ratio while the 1/600 SF code minimum is
   unverified after the city's parking reforms. Every Atlanta plan
   carries `ATL_PARKING_CODE`. Option `--atlanta-parking {market,code}`.
3. **Trailer target per dock door.** `TRAILER_TARGET_TIERS`: 0.5 below
   250k SF, 1.0 below 450k SF, 1.3 above. Option `--trailer-ratio`, one
   number or three. Is that right, or does your tenant run drop-heavy
   (2 or more per door)?
4. **Building prototypes.** `PLANNER_PROTOTYPES = ("rear", "cross")`,
   read from `sitefit.PROTOTYPES`. Your prototypes cover 104k to 166k SF
   (rear-load 260) and 312k to 520k SF (cross-dock 520). They don't cover
   166k to 312k SF, the typical product on 12 to 25 acres.
   `MARKET_REAR_DEPTHS` and `MARKET_CROSS_DEPTHS` fill that gap with
   market depths, marked as not yours in every plan. Should prototypes
   be added? That is an edit to the root's `building_prototypes.json`,
   yours to make between runs. The screener's copy in `sitefit.py` would
   then need to match, which is a screener change and needs a new
   baseline.
5. **Draft zoning values.** Every value in the table above is a draft.
   Which jurisdictions should be verified first? South Fulton, Fairburn
   and Union City hold most 5 to 50 acre industrial parcels. Verified
   values go in `zoning_rules.json`, by a human, between runs.
6. **Paving in setbacks.** May truck courts, trailers and parking sit
   inside zoning setbacks, outside buffers? The draft paving offsets
   above allow it. This matters most on 5 to 15 acre parcels.
7. **Detention.** Soil group from SSURGO (the current default) or B
   everywhere? Full 100-year attenuation, or the 25-year storm with safe
   conveyance? Is underground detention acceptable at screening? The
   read-out now puts a number on that last question for each site: the
   layout that passed every check but the surface pond
   (`selection.without_pond`, the `without_pond_sf` column of
   `plans.csv`).
8. **What counts as built.** The built check's thresholds (10,000 SF in
   the outline, 2,500 SF in a hole, $250,000 of improvements) are
   placeholders. Should the built check become a screen? A screen change
   is in human-owned `screens.py` and needs a new baseline.

9. **How layouts are ranked.** `RANKING` in `siteplan.py`: floor area,
   less 60% of the steep share, 400 SF per ft of relief past 25 ft, and
   2,000 SF per trailer stall short of the target. The trailer mark
   decides many small sites: on both reference fixtures a court-only
   layout 10,000 SF larger passed its checks and ranks below the plan
   drawn. Is a
   trailer stall worth 2,000 SF of building to you? Changing the numbers
   changes which plan is drawn, so it is a planner change with its own
   review, not something the read-out does.

The research spec listed further questions (court depth, door ratio,
gated courts, fire assumptions, the number of buildings per parcel,
driveway spacing rules). The defaults above stand until you decide them.
