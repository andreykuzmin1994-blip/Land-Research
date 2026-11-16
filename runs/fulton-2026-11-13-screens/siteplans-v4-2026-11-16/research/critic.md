CRITIQUE: screening industrial site-planner spec (Fulton Co., 5-50 ac). This is a read-only review: no files were touched. I checked the arithmetic by hand. Prismoid 400x200x6 at 4:1 gives 398,208 cf, so that is correct. Detention, 30 ac at 75%: 8.37 ac-ft, W0=156, 364,032 cf, about 2.2 ac before the access drive, so that is correct. Drop module: 100 stalls / 2.62 ac = 38/ac, correct. Door ratios on the comps come to 3.5k-5.8k SF/door, which supports R=5,000.

A. ERRORS (fix before use)
1. Dead-end court as fire turnaround (Step 6). The court is 130 ft from the dock face, and a docked 53 ft trailer plus seal takes about 55 ft. That leaves about 75 ft clear, not the 96 ft or more claimed. The 185 ft court with a far trailer row also nets about 75 ft. As written the check fails geometrically. Either require a real hammerhead or cul-de-sac at the dead end, or make it fire-official discretion (sev 2) and stop presenting it as a computed pass.
2. Module length "L + 2x(60 end clearance + 30 road)" double-counts. The 60 ft door-free end zone is ON the building wall, which is why cap = (L-120)/12. Adding it again beyond the corners inflates every module by 120 ft and depresses yield. Choose one: either the end clearance is on-building (and is not added), or there is a court run-out past the corner (state it, typically 0-30 ft).
3. Rear-load depth arithmetic does not match the module formula. In "10+62+24+15+260+130+55": 62 already holds a 60 ft double bay with its aisle, and the extra 24 is a second aisle. The 26 ft aerial lane is missing. The rear curb or landscape strip and the fence-to-lot-line strip are missing. Rewrite both from one explicit cross-section.
4. "Do not put a parking row against the aerial-lane wall" is wrong and costly. One row of 18 ft stalls puts a 26 ft aisle about 18-20 ft off the wall, inside the 15-30 ft D105.3 band. Standard Atlanta practice is wall-side row plus aisle-as-aerial-lane. The rule as written wastes about 45 ft of depth on every rear-load plan.
5. Fire loop contradicts truck-car separation. Step 8 needs a continuous fire network, but §1.6 forbids car aisles crossing truck roads. Add an emergency crossover (gated, or bollards or mountable curb) as an allowed element. Otherwise most rear-load plans with a separate car lot fail FIRE_SECOND_ACCESS or reach.
6. The wet pond is not "triggered" at 25 ac. In GSMM, 25 ac is a minimum-drainage feasibility guideline for keeping a permanent pool, not a mandate. Treat it as a land-conservative assumption (label it). Otherwise dry ED plus a WQ BMP is a valid design.
7. South Fulton "+12 ft setback per ft of height over 50" is almost certainly 12 inches, i.e. 1:1. As written, a 60 ft building gains +120 ft. Re-read the source.
8. Regression set (§1.12): Braselton 100/200 and Mason Rd have no acreage. Thornton, Braselton and Factory Shoals are outside Fulton. Acreage alone cannot reproduce SF within ±15%; you need real parcel polygons plus their buffers and topography. Either attach the parcel geometry or demote these to ratio benchmarks.
9. Gate width 32 ft two-way is tight. Two-way truck gates with a guard island are typically 36-40 ft or more; keep 32 only as a minimum.
10. Truck road 30 ft two-way at centerline R60 cannot pass two WB-67s on the bend: offtracking is about 18-20 ft per truck. Widen curves to 40-45 ft, which matches the "observed 40-45".
11. WB-67 "44.8 ft minimum design turning radius" is the outer front-wheel path, not the inside edge. The minimum inside radius is about 8 ft. The citation is misapplied, although the R60 default is fine.
12. Hydrant spacing: base IFC 507.5.1 is 400 ft (600 sprinklered). Confirm that 500 is a real Georgia amendment. The FD access doors at 1 per 100 ft likely come from IFC Ch. 32 (high-piled storage), not 504.1.1. Ch. 32 also independently imposes the 150 ft reach for high-piled storage over 12,000 SF.
13. Court depth: the Range column says "200 for cross-dock >=900k" next to "not gated by building size". Reconcile the two.
14. The "GA now adopts 2024 IFC" claim is unverified. Confirm the edition actually in force under 120-3-3.

B. EXISTING-BUILT GATE (the user's 6715 Oakley complaint)
- The most likely cause of the miss is data recency or address-to-parcel geocoding, not the thresholds. Footprint sets (county planimetric, Microsoft, Overture) lag by years, and South Fulton/Fairburn built heavily in 2020-25. Use several sources together: assessor year-built and improvement class, building permits/CO, ARC DRI records, and NLCD 2021/2023 impervious. Add a human imagery check on fixtures.
- Gate on parcel geometry, not the address string. Get the parcel id for 6715 Oakley and every fixture. Verify 1625 Oakley from the assessor before using it as a fixture: a fixture from a search summary is an unverified test.
- Add `UNDER_CONSTRUCTION / RECENTLY_GRADED`, i.e. cleared or graded with a permit but no footprint yet. This is common along Oakley and Hwy 74.
- Add a same-owner-as-adjacent-improved-parcel check. Trailer yards and parking lots of existing warehouses sit on separate parcels and would pass as `EXISTING_PAVED_YARD` (plan possible), which is wrong.
- False kills: ">=10k SF or any year-built" kills a 40 ac tract that has a farmhouse and barns. Use EXISTING_BUILT only for commercial/industrial class with meaningful coverage. Make residential or agricultural improvements on a large tract `TEARDOWN` (sev 3).

C. MISSING TOPICS A DEVELOPER WILL ASK ABOUT FIRST
1. Topography and earthwork. This is the main yield and cost driver in the South Fulton Piedmont, and the single biggest credibility gap. Free 3DEP 1 m lidar covers Fulton, so "slope location unknown" is a choice, not a limit. At minimum, report relief across the building pad and the court, a rough cut/fill and retaining-wall flag, and the pond at the DEM low point with an outfall.
2. Water and sewer. Report fire flow, the need for an ESFR fire pump and tank (tank land is about 0.2-0.3 ac), and the distance to water and sewer mains (county GIS).
3. ROW dedication and future ROW. Fulton parcel lines often run to the road centerline. Setbacks are measured from the dedicated or future ROW line. Model a half-width dedication by road class.
4. Linear easements. Georgia Power transmission corridors, and the Colonial/Plantation petroleum pipelines that cross South Fulton and Fairburn. "Easements" is subtracted but has no data source.
5. Truck route connectivity. Report the distance to an I-85/I-285 interchange and whether frontage roads are designated or restricted truck routes (South Fulton has truck-route restrictions). A site with perfect geometry on a truck-prohibited road is not a site.
6. Frontage legality. A parcel "within 100 ft of a GDOT centreline" may be separated from the road by a strip parcel or rail line, or reached only by easement. Add `LANDLOCKED / ACCESS_BY_EASEMENT`.
7. DRI trigger. Industrial over about 500k SF in the ARC region (verify the threshold) means ARC/GRTA review time. Add a flag.
8. Tree density and replanting, and any site-wide minimum open space. These are missing from land take.
9. Airport: FAA Part 77 / 7460 and noise contours near College Park, East Point and north South Fulton. These are listed out of scope but deserve a flag.
10. Objective function. "Max SF" picks a larger building that falls short on trailers or cars. Make the program minimums (k x N trailers, parking) hard constraints, or a score, before maximising SF.
11. Headline sanity metric. Developers check SF per gross acre first. The comps run about 10.5k-15.4k: Thornton 10.5k, Factory Shoals 15.4k, 85 South 13.7k, Parkway West 12.5k. Gross FAR should get a warn band, not be "report only".
12. Cross-dock aerial access. Both long sides are courts, so state that the aerial lane goes on a short (end) side, which is allowed because it is "one entire side".

D. NUMBERS VERSUS PRACTICE (reasonable, with notes)
- Court 130/185, the 60 ft apron, 12x55 stalls, R=5,000, k 0.5/1.0/1.3, clear heights 32/36/40, 2% office, parking 0.55-0.70/1,000 and 330-380 SF/stall are all in line with Atlanta spec practice.
- Hillwood 310 is Texas, so label it non-GA. Stonecrest is DeKalb.
- Atlanta's 1/600 SF (1.67/1,000) is far above market and above any other jurisdiction. Verify it before letting it govern, since the 2019-era parking reforms may apply.
- `FIRE_SECOND_ACCESS` at sev 1 is too harsh. Single-frontage 100-200k buildings are routinely approved with two driveways on one frontage at ½-diagonal separation, or with an emergency-only gated link. Use sev 2.
- IBC 507: the 60 ft yard rarely binds, because pavement counts as yard. Small buildings (about 50-90k) can use the Table 506.2 area plus a frontage increase and skip unlimited-area status. Do not apply the 60 ft yard blindly at the low end.
- Shared facing-court values of 185-200 ft (no row) and about 240 ft (one row) are common industry rules of thumb. Label them "practice, unsourced" rather than "no source".

E. OVER-PRECISION
- Three NOAA rainfall zones differing by 1-4%, with 2-decimal k-factors, feed a ±30% volume. Use one rainfall set.
- GDOT spacing values are marked H but take an L (assumed) speed. Derived confidence should be the minimum of the input confidences, and it should be shown.
- "H" is given to out-of-state or city summaries: SUDAS Iowa stalls, Mobile/Tupelo fire guides, Irwindale, Moreno Valley. Use M, or cite the ICC text.
- Many zoning values come from Zoneomics mirrors yet are marked H. Keep everything draft until verified in zoning_rules.json.
- Round the outputs: SF to the nearest 1,000-5,000, doors and trailers to the nearest 5, acres to 0.1, pond volume to 0.5 ac-ft. A single-foot SF figure on a screening fit reads as false precision.
- A 5° sweep on a 10 ft raster is fine, but report the envelope as ±10 ft.

F. GOVERNANCE NOTE
The gate's location (§5 Q17) and every prototype, zoning, pond or court default above are operator decisions. Prototype and zoning edits go in human-owned building_prototypes.json and zoning_rules.json. site_fit.py changes are Tier 2, between runs only, and need a new run plus baseline once any jurisdiction is verified.