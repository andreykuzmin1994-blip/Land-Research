# Design spec: screening-level industrial site planner (Fulton County, 5-50 ac)

Status: proposed spec. Each value below carries its source and its verification status. "Corrected" means the verifier's correction is used here. Values the verifier could not confirm are kept out of the defaults and listed in §1.13.

**Governance (repo invariants).** Several homes for these values are human-owned:
- Zoning values belong in `zoning_rules.json`. Only a human edits it, between runs, and draft→verified is the operator's call.
- Building dimensions belong in `building_prototypes.json`. The operator owns it.
- The planner logic is `site_fit.py`. Changing it is Tier 2 work, between runs only.

Once any jurisdiction is `verified`, two kinds of change move BTS and need a new run plus a baseline: (a) a planner change, and (b) a prototype edit. This spec is therefore input for a human-approved, between-runs change. Nothing in it should be applied mid-run.

---

## 0. Gate before planning: is the parcel already built?

The planner must not draw a greenfield plan on an improved parcel. Run this gate **before** step 1.

| Check | Default rule | Data | Flag |
|---|---|---|---|
| Building footprint on parcel | Existing footprint area ≥ 10,000 SF **or** ≥ 5% of parcel area | County planimetric building footprints, or an open national footprint set | `EXISTING_BUILT` (stop; report "improved parcel, not a development site") |
| Assessor improvement | Improvement value > 0 with a commercial or industrial building class, or any year-built on record | Fulton County Board of Assessors parcel record | `EXISTING_BUILT` / `EXISTING_IMPROVEMENT_MINOR` if the footprint is under the threshold |
| Paved yard without building | Impervious share ≥ 30% with no building | Footprint plus impervious layer, if available | `EXISTING_PAVED_YARD` (plan possible, label it) |

The thresholds are placeholders. The operator decides them (§5).

**Regression fixtures that must come back `EXISTING_BUILT`:**
- 6715 Oakley Industrial Blvd: the user reports an existing industrial building there.
- 1525 Oakley Industrial Blvd: Prologis, 556,800 SF, confirmed by the Prologis flyer [PLD1525].
- 5395 Oakley Industrial Blvd: Southpark, 422,304 SF, built 2001 [SP5395].
- 1625 Oakley Industrial Blvd: 907,665 SF. This is known from a search summary only; the brochure was not opened.

Existing buildings can serve as **benchmarks** (§3.6). They must never appear as candidate sites.

---

## 1. Parameter table

Source keys resolve in §6. Confidence: H = primary text verified; M = mirror, secondary, or derived from verified data; L = assumption, or a single weak source.

### 1.1 Configuration and building massing

| Parameter | Default | Range | Basis (source) | Conf. |
|---|---|---|---|---|
| Configuration by target SF S | rear-load if S < 350k; cross-dock if S ≥ 450k; between them, rear-load only if usable depth ≥ D + court + 2×60 ft | front-load only as a fallback on shallow parcels | GA comps: rear-load verified 90k-254k [THORN][BRAS][FS]; cross-dock 474k-1.2M [CTC][COL85][PLP]; front-load 386k Mason Rd [MASON]. Palmetto 375k/408k rear-load is **unverified** | M |
| Operator prototypes (as given) | cross-dock W=520, L 600-1000 (312k-520k SF); rear/front-load W=260, L 400-640 (104k-166k SF) | — | operator SiteFit | — |
| **Prototype gap** | no prototype covers **166k-312k SF**, which is the typical 12-25 ac product (e.g. 254k on 16.5 ac [FS]) | — | see §5, Q1 | — |
| Rear-load depth by SF (proposed prototypes) | 190 ft (≤120k); 230 (120-180k); 270 (180-260k); 300-320 (260-350k); 340-400 (350-450k) | 190-400 | Thornton 190 [THORN]; Braselton 230×648, 270×756 [BRAS]; Mason 300 front-load [MASON]; Hillwood 310 rear-load [HILL]; Coastal Bldg 1 360×1,008 (config not stated) [CTC] | M |
| Cross-dock depth | 470 (450-550k); 520 (550-800k); 600-620 (800k-1.3M) | 400-650 | Coastal 470/520/620 [CTC]; Palmetto 600 [PLP]; Prologis 1525 Oakley 400 [PLD1525] | H |
| Length L | S/D, snapped to whole column bays of 52-56 ft (or to the 12 ft door module) | L/D 2.0-3.8; warn outside | observed 2.3-3.5 [CTC][PLP][PLD1525]; 4.7 at Southpark [SP5395] | M |
| Speed bay / column bay (reporting only) | 60 ft speed bay (65-75 at ≥500k); bays 50 deep × 52-56 wide | 40-60 deep | GA sheets [CTC][FS][MASON][PLP]; Rite-Hite: staging ≥53-57 ft, big box ≥75 [RH] (corrected) | H |
| Clear height (reporting only) | 32 ft (<200k); 36 ft (200-450k); 36-40 ft (cross-dock >450k) | 32-40 | corrected: Coastal 474k is 40 ft [CTC]; Braselton 32 [BRAS]; FS/Mason/COL85 36 | H |
| Building height for zoning checks | clear + 10 ft | +8 to +12 ft | **assumption**, unverified | L |
| Office share | 2% of footprint, in 1-3 corner pods on the car side | 1-3% | Prologis 1525: 1.6% in 3 pods [PLD1525]; Stonecrest DRI 3% [NAIOPPARK area] | M |

### 1.2 Dock doors

| Parameter | Default | Range | Basis | Conf. |
|---|---|---|---|---|
| Door spacing | 12 ft o.c. (prototype) | 12-14 | MHI: 12 ft minimum, 14+ common [MHI] | H |
| End clearance (no doors) | 60 ft each end of each dock wall (prototype) | 60-75 | prototype; GA dock walls average 16-21 ft of wall per door [BRAS][CTC][PLP] | M |
| Door capacity per dock wall | `cap = floor((L − 120)/12)` | — | geometry | H |
| Target ratio R (SF per door) | 5,000 for spec distribution | GA 3,500-6,500; storage 7,500-10,000; mfg 10,000-15,000 | GA derived, verified subset: 3,923-5,917 [THORN][BRAS][FS][MASON][CTC][COL85][PLP]; ULI ~10,000 historic [ULI]; VVCo 7,500 [VVCO] | H |
| Doors N | `min(round(S/R), cap × walls)`; walls = 1 rear/front-load, 2 cross-dock | — | — | — |
| Wall per door (report) | (L − 120) × walls / N | GA norm 16-21 ft; warn < 14 | derived [BRAS][CTC] | M |
| Drive-in doors | 1 (<150k); 2 (150-500k); 4 (≥500k); 12×14 ft | — | GA sheets | M |
| Code-minimum loading (check only; never sizes the plan) | South Fulton / Fulton / East Point: 3 + ceil((S−65,000)/80,000) for S > 65k, with 1 in 3 at 12×55 ft; Union City: ceil(S/50,000) at 12×35×14 | — | [FCZR18] 18.6; [SF6] 608; [UC] 10-8 | H |

### 1.3 Truck court

| Parameter | Default | Range | Basis | Conf. |
|---|---|---|---|---|
| Maneuvering court C (dock face to curb or trailer row) | **130 ft** (prototype; functional floor) | 130-135; 120 = substandard; ATA conservative check 155-165 | ARC 60+75 = 135 [ARC]; Blue Giant 135 for a 65 ft rig [BG]; ATA 2×75+5-15 [RH]; observed 130 [FS][PLD1525] | H |
| Concrete apron | first 60 ft of court | — | ARC [ARC]; lease clauses [LAWI] (L) | H |
| Court with far-edge trailer row | C + 55 = **185 ft** | 185-190; 200 for cross-dock ≥900k | GA standard 185: 90k-1.2M [THORN][BRAS][MASON][CTC][COL85][PWCC]; 200 [PLP]. Corrected: **not gated by building size** | H |
| Shared court, two facing rear-load buildings | 185 ft with no row; 240 ft with one row | — | **assumption, no source** | L |
| Loading strip facing non-industrial land | 50 ft landscaped strip where loading or maneuvering faces or adjoins non-industrial zoning, including across a street | — | Fulton 18.6.3 [FCZR18]; South Fulton 608.03 [SF6]; East Point 10-2171 [EP5] | H |

### 1.4 Trailer stalls

| Parameter | Default | Range | Basis | Conf. |
|---|---|---|---|---|
| Stall | 12 × 55 ft (prototype) | 10-12 × 55 | Fulton 18.6.2.B 12×55 [FCZR18]; Irwindale 10×55 [IRW] | H |
| Court-row capacity | (court length along dock wall − driveway openings)/12 | — | geometry | H |
| Target per door k | 0.5 (S < 250k); 1.0 (250-450k); 1.3 (≥450k) | small 0.47-0.73; large 1.18-1.44; drop-heavy 2+ | GA verified: 25/53, 19/38, 30/41, 12/23; 151/105, 143/102, 312/220, 190/161 [FS][BRAS][THORN][COL85][CTC][PLD1525]; C&W "≥1 extra per door" is a broker forecast [CW] | H |
| Drop yard (overflow) | prototype module 600 × 190 ft (55 + 80 aisle + 55) = 100 stalls ≈ 38 stalls/ac in the module | ~30/gross ac with end circulation | geometry; the gross figure is derived and **unverified** | L |
| Trailer and car flex | trailer fields and car fields are interchangeable land; report the trade-off | e.g. 46-90 trailers OR 215-330 cars on 386k | [MASON] | H |
| Fairburn oversized truck spaces | 1 per 5,000 SF warehouse, counted against trailer stalls (pending §5 Q9) | — | Fairburn 80-337(22) [FAIR9] | M |
| ARC staging (report only) | one 10×80 ft space per 2 docks | 12×80 preferred | ARC guidance, not code [ARC] | H (as guidance) |

### 1.5 Car parking

| Parameter | Default | Range | Basis | Conf. |
|---|---|---|---|---|
| Spaces | `max(code_min, market) × (1 + 0)`, clipped to any code maximum | — | method | — |
| Market ratio (per 1,000 SF) | bulk ≥250k: 0.55; 100-250k: 0.70; <100k: 1.0; manufacturing: 1.5 | bulk 0.30-0.72; mid 0.49-1.03 | Palmetto BC 0.71 [DRI-PBC]; Chattahoochee LC 0.64 [TM]; COL85 0.65; Coastal 0.30; Braselton 0.94/1.03; FS 0.55; Camp Creek 0.49 (corrected: 108 spaces, a proposed BTS) | H |
| Stall / aisle | 9 × 18, 90°, 24 ft two-way aisle; 60 ft double bay, 42 ft single | Fulton/SF 8.5×18 + 22 ft; East Point 9×18 governs (corrected); Fairburn 10×18 | SUDAS [SUDAS]; [FCZR18][SF6][EP5][FAIR9][UC] | H |
| Gross SF per stall (field sizing) | South Fulton / unincorporated Fulton: 380 (island every 5 stalls plus bay ends, each ≥200 SF); Fairburn: 350 (10 ft island per 12 stalls, 10% interior landscaping); elsewhere: 330 | — | derived from [SF4] 405.02, [FCZR4] 4.23.2, [FAIR9] | M |
| Prototype car module 500 × 124 | about 220 stalls before islands, assuming 4 rows × 55 (please confirm the layout, §5 Q11); about 180 after the South Fulton island rule | — | geometry | L |
| ADA | Table 208.2: 1-25 → 1; 26-50 → 2; 51-75 → 3; 76-100 → 4; 101-150 → 5; 151-200 → 6; 201-300 → 7; 301-400 → 8; 401-500 → 9; 501-1,000 → 2%; >1,000 → 20 + 1/100. Van-accessible = ceil(n/6), 11 ft stall + 5 ft aisle; placed at the office entry; counted **per lot** | — | [ADA] | H |
| EV (South Fulton) | 1 for 10-25 spaces, +1 per additional 25; counts toward the total | — | SF 602.04 [SF6] | M |
| Setback from residential lot lines | 25 ft (Fulton / South Fulton / East Point M- and I-districts); none in buffers or landscape strips | Fairburn: 5 ft from all lot lines | [FCZR18] 18.3.1.G; [SF6] 606.07; [EP5]; [FAIR9] 80-332 | H |
| Admin reduction | none by default; report that 90% is available (Fulton / South Fulton) | — | [FCZR18] 18.2.4; [SF6] 602.03 | H |

**Code minimum by jurisdiction** (BSF = building SF, OFF = office SF, WH = BSF − OFF):

| Jurisdiction | Warehouse / distribution formula | Max | Basis | Conf. |
|---|---|---|---|---|
| Uninc. Fulton, City of South Fulton, East Point | WH/2,000 + OFF×3/1,000 (2.8 if office >250k); manufacturing BSF/1,000 | none | [FCZR18][SF6][EP5] | H / M |
| South Fulton, staff practice | Z22-013 applied 1/1,000 to a warehouse classed as manufacturing/industrial → see §5 Q7 | — | [SFZ22] | M |
| Fairburn | WH ≤150k: WH/2,500; else 60 + (WH−150k)/7,500; office over 10% at 1/250 | none | [FAIR9] 80-337(22) | M |
| Union City | employees + company vehicles; proxy employees = BSF/2,500 (bulk), BSF/1,000 (mfg) | none | [UC] 10-7.Y; labour density [DCV] | H (rule) / M (proxy) |
| College Park | max(employees/2, (0.10 × BSF)/330) | none | [CP6] | M |
| Palmetto | max(employees, UFA/1,500) | 1.10 × minimum | [PAL3] 17-1 | M |
| City of Atlanta I-1/I-2 | max(BSF/600, peak-shift employees/2) ≈ 1.67/1,000; usually governs; flag `ATL_PARKING_VERIFY` | none | [ATL22] (mirror) | M |
| Chattahoochee Hills | 0 | 5/1,000; any lot ≤1.5 ac unless split | [CH5] | M |

### 1.6 On-site drives and gates

| Parameter | Default | Range | Basis | Conf. |
|---|---|---|---|---|
| Truck road | 30 ft two-way, centerline R60 (prototype), giving a 45 ft inside edge | min 26 two-way / 14 one-way; observed 40-45 | Blue Giant [BG]; ARC 12 ft lanes [ARC]; WB-67 minimum design turning radius 44.8 ft [TXDOT] | H |
| Car road | 24 ft, R30 (prototype) | 22-24 | [UC][SF6][CP6] | H |
| Right-angle truck turn | 50 ft inside radius where not a curve module | — | [BG] | M |
| Gate width | 32 ft two-way | 30-32; ≥20 if also a fire lane, with emergency operation | [BG]; IFC 503.6/D103.5 [TUP][MOB] | H |
| Sidewalk, car lot to office | 5 ft wide, set 2 ft back from the curb (corrected) | — | [ARC] | H |
| Truck–car separation | no car aisle may cross a court, trailer row or truck road; each lot has its own driveway | — | [ARC] | H |

### 1.7 Fire apparatus access (Georgia: 120-3-3 makes IFC Appendix D mandatory)

| Parameter | Default | Range | Basis | Conf. |
|---|---|---|---|---|
| Reach | every point of the first-story exterior wall within 150 ft of an access road, measured along an approved route around the building | the fire chief **may** increase this for sprinklered buildings; keep 150 | GA 503.1.1 [GAFIRE][OCI] | H |
| Width / clearance / grade | 20 ft unobstructed, 13'6" vertical, ≤10% grade, all-weather surface (corrected: "asphalt/concrete" is a local amendment) | 26 ft where a hydrant is on the road | IFC 503.2.1, D103.1, D103.2 [MOB] | H |
| Dead ends | ≤150 ft: none; 151-500: 20 ft + 120 ft hammerhead / 60 ft Y / 96 ft cul-de-sac; 501-750: 26 ft + the same; >750: special approval | — | Table D103.4 [MOB] | H |
| Number of access roads | 2 if bldg > 124k SF (sprinklered) or > 62k unsprinklered, **or** height > 30 ft. Every 32-40 ft clear box exceeds 30 ft, so the default is **always 2** | — | D104.1-2 [TUP] (2018 text; GA now adopts the 2024 IFC; unchanged text not verified) | H / M |
| Remoteness of the two accesses | straight-line distance ≥ ½ × the maximum diagonal of the lot or area served | — | D104.3; GA D107.2 [TUP][OCI] | H |
| Aerial access road | 26 ft wide, 15-30 ft from the building, parallel to one entire side, no overhead lines in between | — | D105 [TUP] (2018 text) | H / M |
| Turning radius | local fire official decides; default 25 ft inside / 50 ft outside; flag `TURNING_PER_FIRE_OFFICIAL` | Tupelo 25/48; Decatur GA 25/50; IFC figure 28 | [TUP]; verifier note | M |
| On-site hydrants | any wall point > 500 ft (600 sprinklered) from a hydrant on an access road → the fire chief may require them (corrected: discretionary); cost flag | — | GA 507.5.1 [GAFIRE] | H |
| FD access doors (report) | 1 per 100 ft of wall facing access roads; 1 per 200 ft on dock walls | — | GA 504.1.1 [GAFIRE] | H |
| Unlimited-area yards (IBC 507) | building ≥ 60 ft from interior lot lines; the full width of an adjoining public way counts; courts and parking count as yard | 40 ft on ≤75% of the perimeter with 3-hr walls (corrected) | [IBC] | M |

### 1.8 Access driveways

| Parameter | Default | Range | Basis | Conf. |
|---|---|---|---|---|
| Posted speed when unknown | local 25-30; collector 35-40; minor arterial 45; state route / principal arterial 45-55; limited access = no driveway | — | class defaults; flag `SPEED_ASSUMED` | L |
| Spacing, state route (GDOT Table 3-1), radius return to radius return; also applies to opposite driveways on undivided roads | 25 mph 125; 30 mph 125; 35 mph 150; 40 mph 185; 45 mph 230; 50 mph 275; 55 mph 350; 60 mph 450; 65 mph 550 ft | turn-lane lengths may increase it | [GDOT] | H |
| Spacing, county/city roads (conservative) | Fulton 2005 manual: 25 mph 200; 30 mph 250; 35-50 mph 300; 55 mph 350 ft | GDOT values as the optimistic case | [FCDM]; whether the cities adopted it is unconfirmed | M |
| Side property line | radius return ≥5 ft from the property line (waived for a joint driveway) | — | [FCDM] | M |
| Truck driveway | W = 40 ft, R = 75 ft (state routes and Fulton "designed for trucks"), angle ≥88°; footprint along curb = W + 2R = 190 ft | city streets R 50-60 (footprint 140-160) with flag `RADIUS_REDUCED` | [GDOT] T4-1/4-2/4-5; [ARC]; Moreno Valley 55 ft swept-path example [MVAL] | H |
| Car driveway | W = 24-30, R = 25-35 → footprint 75-100 ft | — | [GDOT][ARC] | H |
| Emergency-only access | W = 20 (26 with hydrant), R 25-30, gate ≥20 ft | — | IFC | H |
| Throat to first internal conflict (gate, car split, cross-aisle) | state route: lot depth <500 → 100 ft; 500-1,000 → depth/5; ≥1,000 → 200 ft. Other roads: truck ≥100 ft; car ≥20 ft (prefer 50) | — | [GDOT] 3.1.3; [ARC] | H |
| Gate stacking (only if the court is gated) | max(140 ft, 2 WB-67 ≈ 170 ft) + 70 ft per 20 docks beyond 50 | CA AB 98 floor 50 ft; York PA 140 + 170 | CA AG [CAAG]; [AB98]; [MON]; [YORK]. Out-of-state benchmarks | M |
| Reject / turnaround before the gate | ≥90 ft paved width (WB-67 U-turn) | — | GDOT T4-4 [GDOT] | H |
| Gate setback from ROW (South Fulton) | ≥20 ft | — | [SF4] 406.02(b) | M |
| Fairburn | collector-street frontage required; on-site truck queuing required; no backing to or from the street | — | [FAIR2] 80-85(h) | M |
| Median | divided road with no known opening → right-in/right-out; opening spacing urban 1,000 / rural 1,320 ft | — | [GDOT] T3-2/3-3 | H |
| Decel lane reserve (state routes, flag only) | 45 mph: 175 + 100 ft taper; 50 mph: 225 + 100; 55 mph: 250 + 100 | warrant by right-turn volume (RTV); TIS if >500 trips/day | [GDOT] T4-6/4-8, 2.4 | H |
| Sight distance | cannot be computed; flag | 2-lane 45 mph needs 500 ft | [GDOT] T3-4 | H |

### 1.9 Detention and water quality

| Parameter | Default | Range | Basis | Conf. |
|---|---|---|---|---|
| Applicability | always (≥1 ac disturbance) | — | [GSMM1] 4.2.2 | H |
| 24-hr rainfall (NOAA Atlas 14), 1 / 25 / 100-yr | South Fulton / Fairburn / Union City 3.38 / 6.34 / 8.01 in; airport area (Atlanta S, College Park, East Point) 3.32 / 6.10 / 7.72; west South Fulton / Palmetto 3.40 / 6.36 / 8.01 | — | [NOAA] three point queries | H |
| Rv | 0.05 + 0.009 × I (I = % impervious) | — | [GSMM2] | H |
| RRv / WQv | RRv = 1.0 × Rv × A/12; WQv = 1.2 × Rv × A/12 (ac-ft, A in ac). Runoff reduction is mandatory MEP everywhere in Fulton since Dec 2020 | — | [GSMM1] Std 3-4; [FCSW]; [MNGWPD] | H |
| Detention storage V_det (ac-ft) | 1.15 × (k_i × A_imp + k_p × A_perv); HSG B: k_i = 0.30, k_p = 0.07 | HSG C 0.23/0.10; HSG D 0.20/0.10 (extrapolated); 25-yr-only B 0.26/0.04; ±30% | TR-55 Ch. 6 shortcut applied to NOAA depths. **Corrected: GSMM sanctions this equation only for CPv, not detention.** Label "screening only"; check against GSMM 3.3.4 or a routed model | L-M |
| Standards satisfied | 24-hr ED of the 1-yr storm (nested); 25-yr peak ≤ pre-dev; 100-yr controlled (South Fulton, Fulton) | Atlanta: 48-hr ED of the 25-yr storm under alternative compliance (adopted text unverified) | [FCSW] 26-166; [SFSW] 14-4029; [ATLDWM] | H / M |
| Pre-dev condition | woods, good condition (CN 55/70/77 for B/C/D) | redevelopment may use existing | [FCSW] 26-112 | H |
| Pond active depth D | 6 ft (prototype) | 4-10 (dry ≤10; wet pool ≤8) | [GSMM2] 4.5.5.3, 4.25 | H |
| Side slope | 4:1 (prototype; waives the safety bench for wet ponds) | 3:1 GSMM preferred; 2:1 absolute | [GSMM2] | H |
| Plan shape | rectangle, L0 = 2 × W0 | L:W ≥1.5 | [GSMM2] | H |
| Freeboard | 1 ft above the 100-yr water surface | — | [GSMM2] 4.5.5.6 | H |
| Rim / maintenance | 15 ft bench all round + 12 ft access drive (≤15%) from a road | — | [GSMM2] 4.25.5.7 | H |
| Setbacks | 10 ft from property lines and building foundations; wet pond: 25 ft buffer from max water surface | — | [GSMM2] 4.5.4, 4.25.5.9 | H |
| Wet pond trigger | drainage area ≥25 ac → add permanent pool = 1.0 × WQv | — | [GSMM2] 4.25 | H |
| Forebay | 0.0083 ac-ft per impervious ac, inside the pond footprint | — | [GSMM2] 4.5.5.4 | H |
| Runoff-reduction land | if modelled: bioretention 4% of treated area (3-6%); otherwise flag `RR_WAIVER_ASSUMED` | — | [GSMM2] 4.2 | H |
| Underground option | ≤25 ac drainage area per vault, 4-8 ft head, 0% RR credit, not for the 100-yr storm | — | [GSMM2] 4.28 | H |
| Land-take sanity | pond land 5-15% of site | warn outside | Mack Engineering (vendor) [MACK] | M |
| Prototype pond (400×200×6, 4:1) | if 400×200 is the top-of-water rectangle: bottom 352×152, **V ≈ 398,000 cf ≈ 9.1 ac-ft**, footprint 1.84 ac (≈2.3 ac with freeboard and rim) | — | geometry | H (arithmetic) |

Fixed-pond error, with V_det sized as above (HSG B):

| Site | V_det needed | Fixed pond (9.1 ac-ft) |
|---|---|---|
| 10 ac, 80% impervious | ≈ 2.9 ac-ft | ~3× too large |
| 30 ac, 75% impervious | ≈ 8.4 ac-ft | about right |
| 50 ac, 75% impervious | ≈ 13.9 ac-ft | ~35% too small |

### 1.10 Zoning dimensional standards (draft; for the operator to enter in `zoning_rules.json`)

- **Building line** = max(district setback; buffer + improvement setback, or a setback measured from the buffer where the code says so; height-driven setback; IBC 60 ft on interior lines).
- **Pavement line** = max(buffer + improvement setback; landscape strip; parking setback; 50 ft loading strip toward non-industrial land).

| Jurisdiction / district | Front (bldg) | Side / rear (bldg) | Residential edge | Street landscape | Coverage cap | Height cap | Other | Source / conf. |
|---|---|---|---|---|---|---|---|---|
| South Fulton M-1/M-2; uninc. Fulton M-1/M-2 | 40 ft on every street frontage (corner lots: side-corner rule) | 0 (5 ft strip next to a less intense non-res district) | side: 50 buffer + 10 improvement setback = **60 undisturbed**; rear: 100 + 10 = **110**; parking ≥25; +12 ft setback per ft of height over 50 | front 10, side-corner 10 | none | 100 ft / 8 stories | loading 50 ft strip to non-industrial; outdoor storage ≥25 ft from residential; FIB overlay 15 ft strip; stream buffer 100 ft (staff standard [SFZ22]) | [SF4][SF6][FCZR4][FCZR10] H / M |
| Fairburn M-1 | 35 | side 20, rear 30; **50** abutting residential | 50 ft buffer to all other districts; buffers count toward yards | **25 ft buffer on all ROW** | 60% building (gross less floodplain and >30% slopes) | 48 ft (higher with fire marshal) | collector frontage; on-site truck queuing; parking 5 ft from lot lines | [FAIR2][FAIR10] M |
| Fairburn M-2 | 40 | 20 / 30; 50 abutting residential (corrected) | as M-1 | 25 ft ROW buffer | 60% building | 48 ft | docks at rear unless fully screened | [FAIR2] M |
| Union City M-1/M-2 | 50 from ROW | 20 / 20 (none next to rail) | 75 buffer + 20 measured from the buffer = **95** | 10 ft (no parking or detention in it) | 80% building + parking | 75 ft | frontage ≥150 ft at initial zoning; stream 50 + 25 | [UC] H |
| Atlanta I-1 / I-2 | 40 | side 0 or ≥5 (I-1) / ≥10 (I-2); street side 20; no rear | 20 ft side and rear transitional yards, unpaved, + 6 ft opaque fence; 45° plane from 35 ft | — | FAR 2.0 | plane only | parking 1/600 SF | [ATL22][ATL24] H / M |
| East Point I-1 / I-2 | 40 | 8 / 30 | parking 25; loading 50 ft strip | — | — | **40 ft** | warehousing is use-permit only in I-1; use-permit uses ≥5,280 ft from residential | [EP3][EP5] M |
| East Point WH-I | 40 (45 on collector) | 20 / 30 | as above | — | 70% impervious | 75 ft | 5,280 ft separation applies to "warehouse & distribution center" only (corrected); flag `USE_CATEGORY_VERIFY` | [EP3] M |
| College Park M-1 / M-2 | 50 / 40 / 35 (arterial / collector / local) | 25 / 25 (M-1); 40 / 40 (M-2) | not found | — | 70% impervious | **35 ft** | docks ≤50% of a street facade; ≤50% of truck parking in front or street-side yards | [CP3] M |
| Palmetto M-1 / M-2 | 50 | 20 / 20 | Level A 50 ft + 6 ft screen (B 20 next to C-1/C-2; C 10 next to C-3/C-4) | greenbelt (width in Ch. 23.5, unread) | 70% building | **40 ft / 3 stories** | overhead doors may not face a ROW unless the building is ≥200 ft back with a greenbelt; stream 50 + 25 | [PALT] H; [PALZO] M |
| Chattahoochee Hills | — | — | — | — | — | — | no by-right industrial; warehousing needs a use permit in HM/VL only → `USE_PERMIT_REQUIRED` | [CHUDC] H |

Stream buffers (where stream lines exist; they are not in the current inputs, so flag `STREAM_BUFFER_UNMODELLED`):
- 25 ft state E&S buffer everywhere.
- Union City, Palmetto, Fairburn: 50 ft undisturbed + 25 ft impervious setback = 75 ft.
- South Fulton: 100 ft (staff standard).

### 1.11 Coverage and impervious sanity bands

| Metric | Target | Warn | Basis | Conf. |
|---|---|---|---|---|
| Net FAR (building SF / net developable land) | 0.25-0.45 | < 0.18 or > 0.50; ≥0.55 infeasible | NAI 0.25 optimal, >0.55 too tight [NAI]; ULI bulk ≤50%, cross-dock 30-40% [ULI]; GA 24-36% [THORN][FS][COL85][DRI-OAK] | M |
| Gross FAR | report only | — | encumbered Fulton campuses 17-20% [DRI-PBC][DRI-CORE5] | H |
| Impervious ÷ building footprint | 2.0 | outside 1.8-2.3 | DRI derived: Oakley 2.08, Palmetto BC 2.0, Core5 2.11 | M |
| Net impervious | 70-80% | > 85% | TR-55 industrial 72% [TR55]; Oakley 75% (corrected) [DRI-OAK] | M |

### 1.12 Regression massing set (verified comps; the planner should reproduce SF within ±15%, given the acreage and configuration)

| Comp | SF | Dims (ft) | Acres | Court (ft) | Doors | Trailers | Cars |
|---|---|---|---|---|---|---|---|
| Thornton Rd, Douglasville | 90,236 | 190 × 475 rear | 8.6 | 135 / 185 | 23 | 12 | 76 |
| Braselton 200 | 149,318 | 230 × 648 rear | — | 185 | 38 | 19 | 154 |
| Braselton 100 | 204,514 | 270 × 756 rear | — | 185 | 41 | 30 | 193 |
| Factory Shoals, Austell | 254,400 | rear | 16.5 | 130 | 53 | 25 | 140 |
| 5195 Mason Rd, Union City | 386,374 | 300 deep front-load | — | 185 / 135 | 67 | 46-90 | 215-330 |
| 85 South CC, Union City | 573,324 | cross | 41.83 | 185 | 105 | 151 | 373 |
| Parkway West CC, Union City | 570,988 | cross | 45.82 | 185 | 122 | 123 | 243 |

Existing-building benchmarks, usable for ratios but never as sites:
- Prologis 1525 Oakley: 556,800 SF, 1,392 × 400, 161 doors, 190 trailers, 167 cars, 130 ft court.
- Southpark 5395 Oakley: 1,407 × 300, front-load.

### 1.13 Unverified, excluded from defaults

- Cushman's optimal depths (single-load <300 / cross-dock 500-660).
- Atlanta Sec. 158-30 parking-lot tree rule.
- Fairburn stormwater manual specifics.
- $10-20/cf underground cost.
- Shared-court depth.
- Drop-yard 30/gross ac.
- Palmetto 375k/408k rear-load.
- 408k / 557k / 908k door and trailer ratios.
- Whether the BeltLine Overlay exempts industrial parking.
- The 53-60 ft stall figure attributed to Innowave.
- 2024 IFC Appendix D text being unchanged from 2018/2021.
- TestFit defaults beyond those the verifier confirmed.

---

## 2. Placement algorithm

All geometry is in a local planar CRS in feet (EPSG:2240, GA West ftUS), using shapely. Every number comes from a config table keyed by (jurisdiction, district). Each value carries source, confidence and `draft|verified` status, read from `zoning_rules.json` and `building_prototypes.json`, never hard-coded.

**Step 0: gates.**
1. Existing-built check (§0).
2. Use status from `zoning.py` (Chattahoochee Hills and East Point WH-I separation → flags).
3. If gross acreage < 5 or net open ground < 3 ac → `TOO_SMALL`.

**Step 1: classify edges.** Split the parcel boundary into segments wherever the neighbour changes. Label each segment:
- `ROW` (matched to a GDOT centreline within 100 ft; carries road class, state-route yes/no, divided yes/no if known)
- `RES` (residential/AG zoning or an existing single-family use)
- `NONRES_LESS` (less intense non-res)
- `IND`
- `RAIL`
- `UNKNOWN` (treated as non-industrial for the loading strip, not as residential)

**Step 2: envelopes.** For each segment build two strips, using `LineString.buffer(d, cap_style=2, join_style=1)` clipped to the parcel:
- d = building offset b(e)
- d = pavement offset p(e)

Then:
- `P = (parcel ∩ open_ground) − ⋃ pavement strips − easements`
- `B = (parcel ∩ open_ground) − ⋃ building strips − 60 ft from interior lot lines (IBC 507)`

B ⊆ P. Discard slivers: `P = P.buffer(-15).buffer(15)`.

**Step 3: access slots on each ROW segment.**
- usable = segment length − 5 ft at each side line − S(speed) at any end meeting an intersecting street.
- Slide a truck footprint (190 ft at R75; 150 ft at R55 on non-state roads, flagged) and a car footprint (90 ft) along the segment at 10 ft steps.
- k driveways fit on a segment of length L if `L ≥ Σ footprints + (k−1)·S + 10`.
- Rough minimums at 45 mph (GDOT): truck only ≈ 200-240 ft; truck + car ≈ 520 ft; two truck driveways ≈ 610 ft. Fulton spacing (300 ft at 35-50 mph) raises truck + car to ≈ 590 ft. Run both and report which rule was applied.
- Rank truck slots by: highest road class (no limited access) → farthest from `RES` edges → nearest to a candidate court end.

**Step 4: orientation and configuration search.**

Angles θ:
- each frontage bearing and its perpendicular;
- both axes of `P.minimum_rotated_rectangle`;
- 0-175° in 5° steps.

For each θ, rotate P and B by −θ and rasterise at 10 ft. Find candidate rectangles with the maximal-rectangle (histogram) algorithm, then confirm with `contains`.

For each allowed configuration (prototypes; §5 Q1-Q2), the **dock module** is, across depth:
- Rear-load: `[car/aerial side zone ≥ 15-30 ft aerial lane offset + 26 ft lane] + D + C + (55 if trailer row) + (30 truck road if loop)`
- Cross-dock: `30 + 55 + C + D + C + 55 + 30`

Along length: `L + 2 × (60 end clearance + 30 truck road)`.

Constraints: building rectangle ⊂ B; module ⊂ P.

Typical minimum depths:
- **Rear-load (prototype 260):** ≈ 10 + 62 + 24 + 15 + 260 + 130 + 55 ≈ 560 ft. Add ~100 ft for a South Fulton residential rear edge.
- **Cross-dock (520):** ≈ 30 + 55 + 130 + 520 + 130 + 55 + 30 ≈ 950 ft.

Dock-face rules:
- Prefer courts facing `IND`/interior edges.
- A court facing `RES`/`NONRES` already pays the 50 ft strip; also flag `LOADING_FACES_RES`.
- Street-facing docks: Palmetto forbids them unless the building is ≥200 ft back (else flag); College Park caps doors at ≤50% of the street facade; Fairburn M-2 requires rear docks unless screened.
- Front-load only if no rear-load or cross-dock fits.

Reserve land:
- **Pond**, initial guess 8% of P (refined in step 9).
- **Car field** = required stalls × gross SF/stall.

Choose the fit by: max SF → fewest severity-1 flags → shortest truck route.

**Step 5: doors and trailers.**
- N = min(round(S/R), cap × walls).
- Fill the court-edge trailer row: (court length − openings)/12.
- If the trailer row is drawn, court = 185 ft. If the target k × N is not met and the trailer row fits, keep it. Otherwise court = C (130) with no row.
- Overflow goes to prototype drop-yard modules (600 × 190) in the remaining P, behind the security line.
- Report the shortfall against k × N as `TRAILER_SHORT`.

**Step 6: truck route.**
- From the chosen truck slot: driveway throat (≥ throat rule) → optional gate (with stacking and a 90 ft reject loop) → 30 ft road with R60 bends → both ends of every court.
- Cross-dock: always a full loop.
- Rear-load: a through-court with both ends connected when end zones allow. Otherwise a dead end; the court itself (≥130 ft clear beyond docked trailers ≥ 96 ft) serves as the fire turnaround, flagged `DEAD_END_COURT`.
- Route the polyline as a shortest path on a visibility graph inside P minus building and car field. Reject routes that cross car aisles.

**Step 7: car field and car access.**
- Required = §1.5 formula.
- Place prototype car modules (or 60 ft bays) on the office side and building ends, nearest the office pods, 25 ft from `RES` lines, outside buffers.
- Put the aerial lane (26 ft, 15-30 ft from the wall) along the office long side, with only sidewalk or landscape between it and the wall. Do not put a parking row against that wall.
- Car driveway: a different segment or road if possible; otherwise the same road ≥ S from the truck slot.
- If only one slot exists: a shared throat, splitting cars off ≥ throat distance inside, flagged `ACCESS_SHARED`.
- ADA stalls at the office entry; count per lot.

**Step 8: fire check.**
- Build a network of all paved lanes ≥20 ft wide with no parking (truck roads, courts, aerial lane).
- Sample the building perimeter every 10 ft. For each sample, take the walking distance along the wall to the nearest point within 10 ft of the network. Pass if ≤150 ft.
- Quick pass: roads on both long sides and extending to the corners cover end walls when D/2 ≤ 150 (true at D ≤ 300). Deeper buildings need end drives (cross-dock: the loop).
- Two accesses: the straight-line distance between the two access points must be ≥ ½ × max diagonal of P. Otherwise try an emergency-only link on a second frontage; otherwise `FIRE_SECOND_ACCESS`.

**Step 9: detention.**
- `A_imp` = building + courts + trailer stalls + car field + all drives + walks.
- `A_perv` = gross − A_imp.
- V_det from §1.9, adding the wet pool if drainage area ≥25 ac.
- Solve the prismoid for W0 at D = 6, z = 4, L0 = 2W0:
  `V = D·(A_bot + A_top + 4·A_mid)/6`, with `A(h) = (2W0 + 2zh)(W0 + 2zh)`.
- Footprint = top at D + 1 ft freeboard, plus the 15 ft rim.
- Rule of thumb at 4:1, D = 6: ≈ 0.28 ac per ac-ft at 8 ac-ft; ≈ 0.35 at 2-3 ac-ft.
- Worked example (HSG B): 30 ac, 75% impervious → 1.15 × (0.30 × 22.5 + 0.07 × 7.5) ≈ 8.4 ac-ft → W0 ≈ 156 ft, bottom 156 × 313, gross ≈ 2.3 ac (7.8% of site).
- Placement without a DEM:
  - fit the pond rectangle (any θ) into the remnant of P, ≥10 ft from lot lines and the building;
  - prefer a remnant adjacent to excluded floodplain, wetland or stream ground (likely low ground and outfall);
  - next choice is the corner farthest from the high-class road;
  - flag `POND_SITE_UNVERIFIED`;
  - two ponds are allowed if each holds ≥2 ac-ft.

**Step 10: converge.**
- If the pond does not fit: shrink L by one bay (52-56 ft), or drop a drop-yard module, then redo steps 5-9.
- Stop after 3 passes with < 1% change, or at 25 iterations.
- If the pond still fails, report two outcomes:
  - (a) the largest plan with a surface pond;
  - (b) the plan with underground storage, valid only if drainage area ≤25 ac per vault, plus a surface 100-yr route, flagged `UNDERGROUND_DETENTION`.

**Step 11: odd shapes and multiple buildings.**
- Shape efficiency = footprint / area(B). If < 0.45 → `IRREGULAR_SHAPE`.
- Core finding: erode P by half the minimum module width (rear-load ≈ (260 + 130)/2 ≈ 195 ft); each surviving component is a candidate pad.
- L-shapes: after the best primary module, rerun step 4 on `P − used` for a second building.
- Two buildings on one pad: compare against two facing rear-load buildings sharing a court (185 / 240 ft, assumption, flagged `SHARED_COURT_ASSUMED`). Keep whichever gives more total SF with fewer flags.
- Limit: 3 buildings.
- Each building > 62k SF gets its own two-access check; driveways may be shared.
- Ponds are sized on the combined impervious area per outfall (one per pad by default).

**Step 12: steep ground (share known, location unknown).**
- Steep share > 15% → `GRADING_RISK`.
- Report net FAR both with and without deducting steep area.
- Never site the pond with knowledge of slope; flag it.

**Step 13: checks.** Apply §1.11 bands, code-minimum loading, coverage and height caps, the parking max, the ADA count, door wall per door, and trailers per door. Emit flags (§3).

---

## 3. What each plan reports

**3.1 Identity and provenance:**
- parcel id, address;
- jurisdiction, district, zoning status (`draft`/`verified`);
- existing-built gate result;
- planner version;
- prototype set hash;
- config-table version;
- for every derived number, the source key and confidence.

**3.2 Land:**
- gross ac; open-ground ac; net developable ac (area of P); building-envelope ac (area of B);
- steep share;
- buffers and strips by edge (type, width, length).

**3.3 Building(s), per building and total:**
- configuration; L × D; footprint and SF; office SF;
- clear height (reported) and assumed building height;
- orientation θ; dock walls and their facing (edge type);
- shape efficiency.

**3.4 Loading:**
- dock doors (N, cap, SF/door, wall ft/door); drive-ins;
- code-minimum berths versus provided;
- truck court depth per wall (apron 60 ft shown);
- trailer stalls: court row, drop yard, total, per door, versus target k × N;
- Fairburn oversized truck spaces, if applicable;
- ARC staging count (informational).

**3.5 Car parking:**
- required (code formula named) versus market versus provided;
- per 1,000 SF; ADA (van-accessible) per lot; EV;
- lot areas; gross SF per stall;
- parking maximum check.

**3.6 Coverage and impervious:**
- gross and net FAR; zoning coverage metric versus cap (building or impervious, per code);
- impervious ac and % (gross and net); impervious ÷ footprint;
- open/landscape %;
- comp-band warnings.

**3.7 Stormwater:**
- HSG assumed; rainfall set;
- Rv, RRv, WQv, V_det (ac-ft, ±30% label);
- pond count, bottom dims, depth, slope, footprint ac and % of site;
- wet pool yes/no; runoff-reduction approach (modelled or waiver assumed);
- underground alternative, if used;
- "TR-55 shortcut, screening only" label.

**3.8 Access and circulation:**
- driveways: count and type (truck / car / emergency); road and class; assumed speed; spacing rule (GDOT or Fulton); curb radius; throat length;
- gate yes/no and stacking length;
- truck route length; loop or dead end;
- fire: reach pass/fail (worst wall point distance); two-access separation versus ½ diagonal; aerial lane side; dead-end turnarounds.

**3.9 Flags** (severity 1 = likely kills or materially cuts yield; 2 = needs verification; 3 = information):

| Severity | Flags |
|---|---|
| 1 | `EXISTING_BUILT`, `USE_PERMIT_REQUIRED`, `TOO_SMALL`, `ACCESS_NONE`, `FIRE_SECOND_ACCESS`, `POND_DOES_NOT_FIT`, `HEIGHT_VARIANCE` (clear + 10 > cap: College Park 35, Palmetto 40, East Point I-1/I-2 40, Fairburn 48), `COVERAGE_OVER_CAP`, `PARKING_DOES_NOT_FIT` |
| 2 | `ACCESS_SHARED`, `ACCESS_CONSTRAINED`, `RADIUS_REDUCED`, `MEDIAN_RIRO_ONLY`, `DECEL_LANE_LIKELY`, `OFFSITE_ROW`, `SIGHT_DISTANCE_UNVERIFIED`, `SPEED_ASSUMED`, `TURNING_PER_FIRE_OFFICIAL`, `DEAD_END_COURT`, `SHARED_COURT_ASSUMED`, `LOADING_FACES_RES`, `STREET_FACING_DOCKS`, `UNDERGROUND_DETENTION`, `RR_WAIVER_ASSUMED`, `POND_SITE_UNVERIFIED`, `STREAM_BUFFER_UNMODELLED`, `GRADING_RISK`, `IRREGULAR_SHAPE`, `TRAILER_SHORT`, `ATL_PARKING_VERIFY`, `USE_CATEGORY_VERIFY`, `ZONING_DRAFT`, `EXISTING_PAVED_YARD` |
| 3 | `ONSITE_HYDRANTS_LIKELY`, `IBC_YARD_REDUCED`, `PARKING_ADMIN_REDUCTION_AVAILABLE`, `DOOR_DENSITY_HIGH` (wall per door < 14 ft), `FAR_OUT_OF_BAND`, `IMPERVIOUS_RATIO_OUT_OF_BAND` |

**3.10 Drawing layers:**
- parcel; P and B envelopes; buffers and strips by type;
- building with dock walls, doors and office pods;
- courts with apron; trailer rows; drop yards; car lots with ADA;
- truck road, car road, aerial lane, fire turnarounds;
- driveways with radii; gate and reject loop;
- pond(s) with rim and access drive; open space.

---

## 4. Out of scope (must be labelled on every plan)

- **Grading and earthwork; DEM-based pond and outfall location; slope location.** The pond position is indicative only.
- **Hydrology beyond the screening k-factors.** No routing, no GSMM 3.3.4 or modified-rational check, no downstream 10% analysis, no Atlas 15 uplift. Volumes are ±30%.
- **Traffic.** No ITE trips, turn-lane warrants, TIS, sight distance, posted speed, median openings or opposite driveways. These are flagged, not computed.
- **Swept-path analysis** and the local fire marshal's turning templates.
- **Utilities.** Water, sewer, hydrant layout, power (overhead lines over the aerial lane).
- **Geotechnical conditions; soil HSG** (defaulted to B unless SSURGO is supplied).
- **Stream lines and stream buffers** (not in the inputs).
- **Entitlement.** Rezoning conditions, overlays (FIB beyond the strip, Hwy 29/74, the Hartsfield-Jackson height overlay), variances, DRI review, facade materials, tree-ordinance replanting.
- **Zoning values.** They are **draft** until a human verifies them in `zoning_rules.json`. Most come from Zoneomics mirrors.
- **Products not modelled.** Building interior and racking, multi-tenant demising, IOS as a primary use, last-mile van stations, rail-served sites, cold storage.
- **Costs** (including the underground-detention cost premium).
- **The existing-structure determination itself.** The planner consumes the gate result; it does not do image analysis.

---

## 5. Open questions for the operator

1. **Prototype gap.** Should rear-load prototypes be added for 166k-312k SF, e.g. depth 270-320 ft with L to about 950 ft? Or should rear-load depth become a function of SF (the §1.1 table)? This is your file to edit.
2. **Configuration thresholds.** Is cross-dock at ≥450k (rear-load <350k) acceptable? Is front-load allowed as a fallback at all?
3. **Truck court.** Keep 130 ft as the default maneuvering court, or 135 (ARC)? Should the 185 ft court-with-trailer-row always be drawn when trailers are needed? Is the 200 ft court relevant for 50 ac sites?
4. **Trailer target.** Is k = 0.5 / 1.0 / 1.3 per door right, or does your typical tenant run drop-heavy (2+)? Should the drop-yard overflow be drawn, or only reported?
5. **Door ratio R.** Keep the default of 5,000 SF/door, or always place the maximum doors (spec-developer practice)?
6. **Car parking policy.** Use max(code, market), code only, or market only? Which market ratio (0.55 bulk / 0.70 mid)? Should the 90% administrative reduction be assumed?
7. **South Fulton use class.** Warehouse at 1/2,000 SF, or manufacturing/industrial at 1/1,000 as staff applied in Z22-013? This roughly doubles cars.
8. **Pavement in setbacks.** May truck courts, trailers and car parking sit inside zoning setbacks, outside buffers and strips? SiteFit allows it and site_fit.py reportedly forbids it. This changes yield a lot on 5-15 ac parcels.
9. **Fairburn oversized truck spaces.** Should they count against trailer stalls?
10. **Prototype pond.** Is 400×200 ft the top-of-water, top-of-bank or bottom rectangle? Do you accept replacing it with a pond sized from impervious area? Which depth and slope defaults (6 ft, 4:1)?
11. **Car module 500×124.** What is its internal layout (rows, stall width, aisle)? It is needed to count stalls.
12. **Hydrology defaults.** HSG B everywhere, or SSURGO by parcel? Full 100-yr attenuation (governs, +15-25% storage) or safe conveyance plus 25-yr? Model runoff-reduction land (4%) or assume the waiver? Allow underground detention at screening?
13. **Gated courts.** Should the default spec court be secured, with gate stacking of 140+ ft and a reject loop? (Fairburn requires on-site queuing regardless.)
14. **Fire assumptions.** Should every building be assumed sprinklered and taller than 30 ft (so always two accesses plus an aerial lane)? Should 40 ft IBC yards with 3-hr walls be accepted?
15. **Height allowance.** Is clear + 10 ft right for your product? Should over-cap parcels be flagged, or excluded, in College Park / Palmetto / East Point?
16. **Multiple buildings.** Maximum per parcel (default 3)? Should the facing rear-load shared court be allowed at the 185 / 240 ft assumption?
17. **Existing-built gate.** Which thresholds (≥10,000 SF footprint, ≥5% of parcel, any improvement value)? Which data source? Should partly built parcels with excess land be screened as expansion land or excluded? Where does the gate live: the root system (metric change → new run + baseline) or a pre-filter?
18. **Driveway spacing rule** on city and county roads: GDOT (optimistic) or the Fulton 2005 manual (conservative)? Is there a source for posted speed?
19. **Acceptance tolerance** for the §1.12 regression set: ±15% SF, ±20% doors and trailers?
20. **Verification order** for zoning values in `zoning_rules.json` (draft → verified). Suggested first: South Fulton, Fairburn, Union City, which hold most 5-50 ac industrial parcels.

---

## 6. Sources

| Key | URL |
|---|---|
| MHI | https://og.mhi.org/downloads/industrygroups/lodem/dock_planning_101.pdf |
| BRAS | https://res.cloudinary.com/jll-global-sandbox/image/upload/v1789569025/MyListing/public/prod/property-873836/braselton-technology-ctrs-bldgs-100-200-final.pdf |
| CTC | https://www.coastaltradecenter.com/uploads/1/4/9/3/149335235/ctc_brochure_final.pdf |
| COL85 | https://colliersatlanta.com/eflyer/85SouthCommerceCenter/brochure.pdf |
| PLP | https://colliersatlanta.com/eflyer/PalmettoLogisticsPark/brochure.pdf |
| FS | https://s3.us-east-2.amazonaws.com/media.myelisting.com/listings/9/242660/factory-shoals-logistics-center-0SS-DjX.pdf |
| MASON | https://admin.zoomprospector.com/photos/STATEGA/b02f4f9d-eec5-4a96-a3e0-872c0dbd0f7d.pdf |
| THORN | https://properties.zoomprospector.com/douglascountyga/property/Thornton-Road-Douglasville-Georgia/1DC0C52D-79A6-49C1-B383-5542C76D140C |
| HILL | https://www.hillwood.com/newsroom/press-releases/hillwood-announces-two-new-speculative-industrial-buildings-at-alliancetexas/ |
| PWCC | https://colliersatlanta.com/eflyer/ParkwayWCC/home.html |
| PLD1525 | https://prologis.getbynder.com/m/12bd89224636336a/original/1525-Oakley-Industrial-Flyer.pdf |
| SP5395 | https://properties.zoomprospector.com/georgiaemc/property/5395-Oakley-Industrial-Blvd-Fairburn-Georgia/ABAE6C23-BF7F-4398-96C2-87E6A75838C1 |
| ULI | http://courses.washington.edu/cee320ag/warehousing/WarehouseClassification.pdf |
| VVCO | https://vvco.com/what-is-a-good-dock-to-door-ratio-for-a-distribution-warehouse/ |
| CW | https://www.cushmanwakefield.com/en/united-states/insights/the-industrial-spec-sweet-spot-what-tenants-want |
| NAI | https://www.nairealvest.com/wp-content/uploads/sites/18/A-New-Standard-for-Warehouse-Design.pdf |
| RH | https://www.ritehite.com/en/AM/News/2012/Blog/Loading-Dock-Design-Pt1 |
| BG | https://www.bluegiant.com/architects/planning-and-design/dock-access/ |
| INNO | https://www.innowave-studio.com/site-plan/warehouse-site-plan |
| LAWI | https://www.lawinsider.com/clause/truck-court |
| IRW | https://irwindaleca.gov/DocumentCenter/View/4594/PC-Staff-Report-and-Resolutions?bidId= |
| FHWA | https://ops.fhwa.dot.gov/Freight/infrastructure/truck_parking/docs/FHWA_Truck_Parking_Development_Handbook_2026.pdf |
| IBC | https://gocodebook.com/library/us/ca/ca-building-code/chapter-5-general-building-heights-and-areas/section-507-unlimited-area-buildings |
| SUDAS | https://www.iowasudas.org/wp-content/uploads/sites/15/2020/03/8B-1.pdf |
| ADA | https://www.access-board.gov/ada/guides/chapter-5-parking/ |
| ARC | https://cdn.atlantaregional.org/wp-content/uploads/freight-design-guidelines.pdf |
| TXDOT | https://www.txdot.gov/manuals/des/rdw/chapter-4--basic-design-criteria/4-3-traffic-characteristics/4-3-3-control-vehicles.html |
| MVAL | https://www.moreno-valley.ca.us/cdd/pdfs/projects/MoVal-BizCenter/K3.pdf |
| GDOT | https://www.dot.ga.gov/PartnerSmart/DesignManuals/Encroachment/Driveway.pdf |
| FCDM | https://up.sandyspringsga.gov/sites/default/files/2021-06/FC_Driveway_Manual.pdf |
| GAFIRE | https://rules.sos.ga.gov/gac/120-3-3 |
| OCI | https://oci.georgia.gov/document/document/120-3-3/download |
| MOB | https://www.cityofmobile.org/fire/uploads/access-road-requirements.pdf |
| TUP | https://tupeloms.gov/wp-content/uploads/2023/12/19.-Fire_Compliance_Guide_IBC2018.pdf |
| CAAG | https://oag.ca.gov/system/files/media/warehouse-best-practices.pdf |
| AB98 | https://www.sandiego.gov/development-services/forms-publications/information-bulletins/498/ab-98-warehouse-design-and-build-standards |
| MON | https://www.monroecountypa.gov/getmedia/4cefb25c-5e20-4433-9c8c-a93781a5a494/20230324_WarehousingandDistributionModelOrdinance.pdf |
| YORK | https://www.ycpc.org/DocumentCenter/View/5146/Warehousing--Associated-Uses-Model-Ordinance-PDF |
| GSMM1 | https://documents.atlantaregional.com/gastormwater/GSMM_2016%20EDITION_FINAL_V1.pdf |
| GSMM2 | https://atlantaregional.org/wp-content/uploads/gsmm-2016-edition-final-v2.pdf |
| FCSW | https://www.fultoncountyga.gov/-/media/Departments/Public-Works/Stormwater-Services/Stormwater-Ord-Amendments-Resolution.pdf |
| SFSW | https://www.cityofsouthfultonga.gov/AgendaCenter/ViewFile/Agenda/_11272018-621 |
| MNGWPD | https://up.sandyspringsga.gov/sites/default/files/2022-03/Build%20Seminars%20-%20GA%20Stormwater%20Management.pdf |
| ATLDWM | https://www.atlantawatershed.org/wp-content/uploads/2020/05/QA-Responses_Final.pdf |
| NOAA | https://hdsc.nws.noaa.gov/cgi-bin/new/cgi_readH5.py?lat=33.567&lon=-84.581&type=pf&data=depth&units=english&series=pds (also lat 33.640/lon -84.427 and lat 33.590/lon -84.670) |
| MACK | https://mack-engineering.com/detention-pond-design-metro-atlanta-gsmm-sizing-cost/ |
| TR55 | https://ehs.umich.edu/wp-content/uploads/2017/01/TR-55_curve_numbers.pdf |
| FCZR4 | https://www.fultoncountyga.gov/-/media/Departments/Public-Works/Planning-Zoning-and-Permits/Zoning-Resolution/art4.pdf |
| FCZR10 | https://www.fultoncountyga.gov/-/media/Departments/Public-Works/Planning-Zoning-and-Permits/Zoning-Resolution/art10.pdf |
| FCZR18 | https://www.fultoncountyga.gov/-/media/Departments/Public-Works/Planning-Zoning-and-Permits/Zoning-Resolution/art18.pdf |
| SF3 / SF4 / SF6 | https://www.zoneomics.com/code/south-fulton-GA/chapter_3 , /chapter_4 , /chapter_6 |
| SFZ22 | https://www.cityofsouthfultonga.gov/DocumentCenter/View/6839/Z22-013-CV22-005-0-FIB_PC-08_28_22 |
| FAIR2 / FAIR9 / FAIR10 | https://www.zoneomics.com/code/fairburn-GA/chapter_2 , /chapter_9 , /chapter_10 |
| UC | https://www.unioncityga.gov/files/assets/city/v/1/community-development/documents/documents-amp-applications/zoningordinance022025.pdf |
| ATL22 / ATL24 | https://www.zoneomics.com/code/atlanta-GA/chapter_22 , /chapter_24 |
| EP3 / EP5 | https://www.zoneomics.com/code/east-point-GA/chapter_3 , /chapter_5 |
| CP3 / CP6 | https://www.zoneomics.com/code/college-park-GA/chapter_3 , /chapter_6 |
| PAL3 | https://www.zoneomics.com/code/palmetto-GA/chapter_3 |
| PALT | https://mcclibrary.blob.core.usgovcloudapi.net/codecontent/12162/402234/AppB_9-3a.png |
| PALZO | https://bwws-assets.s3.amazonaws.com/amcbid/assets/content/auctions/1185/palmetto_zoning_ordinance.pdf |
| CH5 | https://www.zoneomics.com/code/chattahoochee-hills-GA/chapter_5 |
| CHUDC | https://cms3.revize.com/revize/chattahoocheehillsga/Chattahoochee%20Hills%20Zoning%20with%203.1.22%20ordinances.pdf |
| DRI-OAK | http://documents.atlantaregional.com/Land%20Use/Reviews/ID1599/Preliminary%20Report%20-%20Oakley%20Distribution%20Center%20(DRI%202452).pdf |
| DRI-PBC | http://documents.atlantaregional.com/Land%20Use/Reviews/ID4213/2022%20Palmetto%20Business%20Center%20DRI%203684%20-%20Final%20Report.pdf |
| DRI-CORE5 | http://documents.atlantaregional.com/Land%20Use/Reviews/ID4188/2022%20Core%205%20at%20Palmetto%20Logistics%20Center%20DRI%203622-%20Final%20Report.pdf |
| NAIOPPARK | http://documents.atlantaregional.com/Land%20Use/Reviews/ID4175/2022%20Stonecrest%20Logistics%20Center%20DRI%203584%20-%20Final%20Report.pdf |
| TM | https://www.taylormathis.com/projects/chattahoochee-logistics-centers/ |
| DCV | https://www.dcvelocity.com/articles/28612-e-commerce-fulfillment-growth-into-secondary-markets-driving-up-warehouse-labor-costs-report-says |
