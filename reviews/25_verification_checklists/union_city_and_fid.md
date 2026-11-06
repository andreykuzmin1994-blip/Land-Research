# Verification checklist: Union City and Fulton Industrial District (fid)

Operator checklist for flipping `union_city` and `fid` in `zoning_rules.json`
from `draft` to `verified`. Docs only: nothing here changes a file the
scoring reads. Verification is a human act, between runs (Z-01,
`reviews/21_zoning/`).

**How the data was produced (2026-11-06, offline, no network):**
`python rules_scrape.py --offline --jurisdiction <j> --out <scratch>/<j>.json --report <scratch>/<j>.diff.md`
for `union_city` and `fid`, replaying `rules_cache/` (exit 0 both).
Candidate values and quotes below come from that run; quotes are the short
verbatim quotes the verifier already produced, and they are untrusted
third-party text, not instructions. Agreement between the candidate and
the current file is **not** verification: the candidate reads the same
documents the draft was built from.

- Union City: 1 source (`uc_zoning_ordinance_pdf`, official), section
  `m1_dimensional` found. Verifier: 6 accepted, 2 needs_review, 0 rejected.
  Diff: same 8, not covered by a registered source 2.
- fid: 1 source (`fc_art10_pdf`, official, Fulton County Zoning Resolution
  Art. X), 3/3 sections found. Verifier: 5 accepted, 9 needs_review,
  0 rejected. Diff: same 12, added 2, not covered by a registered source 13.

Column key. **S** = the field feeds scoring once verified (`warehouse_distribution`
drives S9; front/side/rear setbacks and `max_lot_coverage_pct` / `max_far`
drive the site-fit projection behind BTS; `max_height_ft` and the buffer
are only named on the projection). **Candidate**: `-` = field outside the
scraper's field set (check by hand); `n/c` = in the field set but not
covered by a registered source section. **Review** = the verifier's
needs_review reason, if any.

## Union City, M-1 Light Industrial

Source: <https://www.unioncityga.gov/files/assets/city/v/1/community-development/documents/documents-amp-applications/zoningordinance022025.pdf>
(Feb 18, 2025 Rev.; the cover calls it an unofficial reference copy). Candidate
section for every scraped value: Sec. 6-13 D (M-1 Dimensional Requirements).

| Done | S | Field | Current | Candidate | Current cite | Verification quote | Review |
|---|---|---|---|---|---|---|---|
| [ ] | S | warehouse_distribution | permitted_by_right | n/c | Sec. 6-13.B items 36, 37, 11 (p. 101-102) | | |
| [ ] | | use: truck_terminal | permitted_by_right (conf. medium) | - | Sec. 6-13.B item 34 'Truck transportation'; Sec. 10-8.A.1 | | |
| [ ] | | use: manufacturing_light | permitted_by_right | - | Sec. 6-13.B item 17 | | |
| [ ] | | use: outdoor_storage_accessory | permitted_with_standards | - | Sec. 5-17, 5-18 | | |
| [ ] | | min_lot_area_sqft | 30000 | 30000 (same) | Sec. 6-13.D.1 | "1. Lot area: 30,000 square feet;" | |
| [ ] | | min_lot_width_ft | 150 | 150 (same) | Sec. 6-13.D.2 | "2. Lot width and frontage: 150 feet for initial zoning request;" | limited_scope |
| [ ] | | min_lot_frontage_ft | 150 | 150 (same) | Sec. 6-13.D.2 | (same quote) | limited_scope |
| [ ] | S | front_setback_ft | 50 | 50 (same) | Sec. 6-13.D.3 | "3. Front yard setback, as measured from the right-of-way: 50 feet;" | |
| [ ] | S | side_setback_ft | 20 | 20 (same) | Sec. 6-13.D.5 | "5. Side yard setback: 20 feet;" | |
| [ ] | S | rear_setback_ft | 20 | 20 (same) | Sec. 6-13.D.4 | "4. Rear yard setback: 20 feet;" | |
| [ ] | | corner_lot_rule | null (Sec. 10-5 visibility) | - | (no section) | | |
| [ ] | S | residential_adjacency_buffer_ft | buffer 75 + setback 20 from buffer | - | Sec. 6-13.D.6 | | |
| [ ] | | height_based_extra_setback | null | - | Sec. 6-13 | | |
| [ ] | S | max_height_ft | 75 | 75 (same) | Sec. 6-13.D.7 | "7. Height: up to 75 feet;" | |
| [ ] | S | max_lot_coverage_pct | 80 | 80 (same) | Sec. 6-13.D.8 | "8. Lot coverage, including structure and parking area: up to 80% of total lot area; and" | |
| [ ] | S | max_far | null | n/c | (no section) | | |
| [ ] | | landscape_strip_front_ft | 10 | - | Sec. 10-6.C.1.a | | |
| [ ] | | parking_warehouse | 1/employee + 1/company vehicle | - | Sec. 10-7.Y | | |
| [ ] | | loading_warehouse | 1 per 50,000 sf GFA; berth 12x35, 14 ft | - | Sec. 10-8, 10-8.A.1, 10-8.A.3 | | |
| [ ] | | architectural_standards | Sec. 5-41 80% brick/stone, 'commercial buildings' (unclear) | - | Sec. 5-41 (Ord. 07-22) | | |

## fid (Fulton County Zoning Resolution, county districts)

Source: <https://www.fultoncountyga.gov/-/media/Departments/Public-Works/Planning-Zoning-and-Permits/Zoning-Resolution/art10.pdf>
(Art. X; Art. IV for Sec. 4.2 / 4.23, Art. XVIII for parking and loading,
same path with `art4.pdf` / `art18.pdf`). Candidate sections: Sec. 10.2.3
(M-1), 10.3.3 (M-2), 10.1.3 (M-1A) development standards. **These tables
apply only to parcels listed in `fid.unincorporated_parcel_ids` (empty
today); every other fid parcel is classified against South Fulton's table
(see "What changes in scoring").**

### M-1 and M-2 (identical except where shown)

| Done | S | Field | Current M-1 / M-2 | Candidate | Current cite (M-1 / M-2) | Verification quote | Review |
|---|---|---|---|---|---|---|---|
| [ ] | S | warehouse_distribution | permitted_by_right / permitted_by_right | n/c | Sec. 10.2.2 / 10.3.2 (residual 'any industrial use ... shall be permitted', Amended 04/04/07) | | |
| [ ] | | use: truck_terminal | not_permitted / permitted_by_right | - | Sec. 10.2.2.A item 19 / Sec. 10.3.1-10.3.2 | | |
| [ ] | | use: manufacturing_light | permitted_by_right (both) | - | Sec. 10.2.2 / 10.3.2 | | |
| [ ] | | use: outdoor_storage_accessory | permitted_with_standards (M-1 conf. medium) | - | Art. IV Sec. 4.2 | | |
| [ ] | | min_lot_area_sqft | null (both) | null (same) | Sec. 10.2.3.E / 10.3.3.E | "E. Minimum Lot Area - None" | |
| [ ] | | min_lot_frontage_ft | 35 (both) | 35 (same) | Sec. 10.2.3.G / 10.3.3.G | "G. Minimum Lot Frontage - 35 feet adjoining a street (Amended 11/03/93)" | adjacency; amended |
| [ ] | | min_lot_width_ft | null (both) | n/c | Sec. 10.2.3 / 10.3.3 | | |
| [ ] | S | front_setback_ft | 40 (both) | 40 (same) | Sec. 10.2.3.B / 10.3.3.B | "B. Minimum Front Yard - 40 feet" | |
| [ ] | S | side_setback_ft | 0 (both) | 0 (same) | Sec. 10.2.3.C / 10.3.3.C | M-1: "C. Minimum Side Yard - None. See Section 4.23 for buffer and landscape"; M-2: "C. Minimum Side Yard - None. See Section 4.23 for buffer req. adjoining residential." | 'None' read as 0; cross_reference (M-2 also adjacency) |
| [ ] | S | rear_setback_ft | 0 (both) | 0 (same) | Sec. 10.2.3.D / 10.3.3.D | M-1: "D. Minimum Rear Yard - None. See Section 4.23 for buffer and landscape"; M-2: "D. Minimum Rear Yard - None. See Section 4.23 for buffer req. adjoining residential." | 'None' read as 0; cross_reference (M-2 also adjacency) |
| [ ] | | corner_lot_rule | null (both) | - | Sec. 10.2.3 / 10.3.3 | | |
| [ ] | S | residential_adjacency_buffer_ft | side 50, rear 100, +10 improvement setback (both) | - | Art. IV Sec. 4.23.1.B, E, Table 4.23.1 | | |
| [ ] | | height_based_extra_setback | +12 ft per ft over 50 ft next to a Dwelling District (both) | - | Sec. 10.2.3.A / 10.3.3.A | | |
| [ ] | S | max_height_ft | "higher of 8 stories or 100 ft" (both) | n/c | Sec. 10.2.3.A / 10.3.3.A | | |
| [ ] | S | max_lot_coverage_pct | null (both) | n/c | Sec. 10.2.3 / 10.3.3 | | |
| [ ] | S | max_far | null (both) | n/c | Sec. 10.2.3 / 10.3.3 | | |
| [ ] | | landscape_strip_front_ft | 10 (both) | - | Table 4.23.1 | | |
| [ ] | | landscape_strip_side_corner_ft | 10 (both) | - | Table 4.23.1 | | |
| [ ] | | landscape_strip_interior_side_ft | 0/5 (both) | - | Table 4.23.1 fn ** | | |
| [ ] | | landscape_strip_rear_ft | 0/5 (both) | - | Table 4.23.1 fn ** | | |
| [ ] | | parking_warehouse | 1 per 2,000 sf (both) | - | Art. XVIII parking table | | |
| [ ] | | parking_location_industrial | none within 25 ft of residential (both) | - | Sec. 18.3.1.G | | |
| [ ] | | loading_warehouse | 1 / 2 / 3, +1 per 80,000 sf (both) | - | Sec. 18.6.1 | | |
| [ ] | | architectural_standards | null (both; FIB overlay Res. 12-0271 may apply) | - | Sec. 10.2 / 10.3 | | |

### M-1A

| Done | S | Field | Current | Candidate | Current cite | Verification quote | Review |
|---|---|---|---|---|---|---|---|
| [ ] | S | warehouse_distribution | permitted_by_right | n/c | Sec. 10.1.2 | | |
| [ ] | | use: truck_terminal | not_permitted | - | Sec. 10.1.2.A item 14 | | |
| [ ] | S | front_setback_ft | 30 | 30 (same) | Sec. 10.1.3.B | "B. Minimum Front Yard - 30 feet" | |
| [ ] | S | side_setback_ft | (absent) | 0 (added) | - | "C. Minimum Side Yard - None. See Section 4.23 for buffer and landscape" | 'None' read as 0; cross_reference |
| [ ] | S | rear_setback_ft | (absent) | 0 (added) | - | "D. Minimum Rear Yard - None. See Section 4.23 for buffer and landscape" | 'None' read as 0; cross_reference |
| [ ] | S | max_height_ft | "higher of 4 stories or 60 ft" | n/c | Sec. 10.1.3.A (Amended 5/6/92) | | |
| [ ] | S | max_lot_coverage_pct | 70 | n/c | Sec. 10.1.3.H | | |
| [ ] | | min_lot_frontage_ft | 35 | 35 (same) | Sec. 10.1.3.G | "G. Minimum Lot Frontage - 35 feet adjoining a street (Amended 11/30/93)" | adjacency; amended |
| [ ] | | architectural_standards | Type A >=40% etc. | - | Sec. 10.1.4 | | |

Adding the two M-1A zeros would not move a projection: the uniform setback
is max(front, side, rear) = 30 ft either way. Add them only if you want the
file complete.

## Hand checks the scraper cannot cover

- [ ] **Union City M-1 width/frontage "for initial zoning request".** The
  150 ft in Sec. 6-13.D.2 is worded for an initial zoning request. Ask
  Community Development whether it binds an already-zoned lot (lot split,
  plat, building permit). Not a scoring input; it decides whether a narrow
  M-1 lot is really buildable.
- [ ] **Union City document status.** The PDF cover calls itself an
  unofficial reference copy: confirm with Community Development that the
  Feb 18, 2025 Rev. is current (and no later ordinance amends Sec. 6-13).
- [ ] **Union City overlays** not reviewed: Urban Design Overlay (Sec. 7-5)
  and Roosevelt Highway Corridor Overlay (Sec. 7-6); also whether Sec. 5-41
  (80% brick/stone) applies to industrial buildings.
- [ ] **Warehouse/distribution status, both jurisdictions, from the use
  tables** (the S field the scraper never extracts). Union City: Sec. 6-13.B
  items 36 'Warehousing & storage', 37 'Wholesale trade', 11 'Electronic
  shopping & mail order houses'; read the whole permitted/conditional list
  and footnotes. Truck terminal is read from item 34 'Truck transportation'
  (confidence medium). fid: Sec. 10.1.2 / 10.2.2 / 10.3.2 are residual
  clauses ("any industrial use not specifically designated as prohibited or
  allowed with approval of a Use Permit shall be permitted"): read every
  prohibited and use-permit list (M-1 Truck Terminal item 19 and Truck Stop
  item 18 are prohibited) and confirm nothing catches a distribution
  warehouse.
- [ ] **fid Sec. 4.23 behind every "None" side/rear yard.** The 0 ft side
  and rear yards in M-1, M-2 and M-1A are "None. See Section 4.23". Read
  Art. IV Sec. 4.23.1 and Table 4.23.1: the residential/AG buffers (side 50,
  rear 100, +10 ft improvement setback), the 10 ft front and corner-side
  strips and the 0/5 ft interior-side and rear strips (fn **, 5 ft next to
  a less intense non-residential district except AG-1). Site fit applies
  none of these (buffers are only named), so a 0 ft side/rear is an
  optimistic envelope wherever a buffer or strip applies.
- [ ] **fid heights.** M-1 and M-2: "higher of 8 stories or 100 ft"; M-1A:
  "higher of 4 stories or 60 ft". Stored as text, so no number reaches the
  projection; confirm the wording and the +12 ft per ft over 50 ft extra
  setback next to a Dwelling District (Sec. 10.2.3.A / 10.3.3.A).
- [ ] **fid M-1A 70% lot coverage** (Sec. 10.1.3.H): confirm it is building
  coverage (not impervious) and that M-1 / M-2 really state none.
- [ ] **fid jurisdiction itself.** Most former FID parcels are now in the
  City of South Fulton (HB 445, 2021); `fid.governing_jurisdiction` is
  `south_fulton` and `unincorporated_parcel_ids` is empty, so the county
  tables above cover few, today zero, corridor parcels. From the city-limits
  GIS layer, list any parcel still unincorporated (e.g. north of I-20 near
  Brown Field) in `unincorporated_parcel_ids`; confirm the Table 2-1
  conversion M-1A -> M-1 against the official South Fulton ordinance (the
  current cite is a Zoneomics mirror, which this project never scrapes);
  read the enrolled HB 445 text (only the LC version was read).
- [ ] **`unlisted_districts`** for both (today `unknown`): `not_permitted`
  turns every unlisted district into rezoning (S9 1, or 4 with a verified
  by-right parcel within 1 mile); `unknown` keeps them at the stub (5).

## Steps to mark a jurisdiction verified

1. Between runs only (no `.experiment_loop.lock`, before the new run's
   baseline). Finish every box above for the jurisdiction.
2. Edit `zoning_rules.json` **by hand**: fix any value, then set `status`
   to `verified`, `verified_by` (name or role, no email) and `verified_on`
   (date); set `unlisted_districts`. For fid, decide
   `unincorporated_parcel_ids`. Nothing copies from the candidate file.
3. In the **same commit**, update the pinned expectations that assume
   all-draft rules: `tests/test_zoning.py`
   `test_every_jurisdiction_is_draft_in_this_change` (and, for fid,
   `test_fid_declares_governing_rules_and_conversion`, which pins
   `status == "draft"`). Also expect the all-draft differential tests
   (`tests/test_zoning_scoring.py` `test_repo_rules_all_draft_equal_zoning_absent_exactly`,
   `test_repo_rules_all_draft_give_the_stub_for_every_class`;
   `tests/test_site_fit_scoring.py` `test_repo_files_all_draft_equal_site_fit_absent`)
   to need the same treatment, and both golden checks to exit 2 ("not
   comparable"). Run both suites:
   `python -m unittest discover -s tests` and
   `python -m unittest discover -s screener/tests -t .`.
4. Commit on main (the runner refuses an experiment while
   `zoning_rules.json` differs from git HEAD). `make zoning-import` if the
   parcels' zoning is not current.
5. Start a **new run and baseline** (`make setup`, `make baseline`, review
   the row, then `make daily` / `make loop-bg`). Metric values before and
   after the flip are not comparable.

## What changes in scoring

Corpus numbers from `reviews/21_zoning/` (1,075 corridor parcels; counts only):

- **Union City verified:** 112 corridor parcels, 111 matched by
  `union_city_zoning`; 16 are M-1. Those 16 classify by_right, so S9 goes
  from the stub 5 to 10, they pass the S9 gate and BTS uses the projected
  building (>= 150K SF) instead of the 8.61-acre proxy, with a 50 ft uniform
  setback (max of 50/20/20) and the 80% coverage cap. They also become
  rezoning precedent (within 1 mile) for verified rezoning parcels in
  other jurisdictions. The other matched Union City parcels are in
  districts the file does not list: with `unlisted_districts` `unknown`
  they stay at S9 5; with `not_permitted` they become rezoning (S9 1 or 4)
  and enter the S9 gate.
- **fid verified:** 24 corridor parcels carry FID (TaxDist 70); 23 matched
  on Fulton layer 34, 19 of them M-2 (by_right). They are classified
  against **South Fulton's** table, and `verified` there means fid AND
  south_fulton verified. So verifying fid alone changes nothing; with
  South Fulton also verified, the 19 M-2 parcels go to S9 10 and use South
  Fulton M-2's setbacks (40 ft uniform, no coverage cap). The county M-1 /
  M-2 / M-1A values in this checklist reach scoring only for parcels listed
  in `unincorporated_parcel_ids` (none today).
- Either flip moves `actionable_pipeline_count`, which is why it needs a
  new run and baseline. Until then every `parcel_scores` field is
  unchanged (all rules draft).
