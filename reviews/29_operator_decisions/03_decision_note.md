# 03 — Operator decisions of 2026-11-09: decision note

> **Tier 2.** Design `00_design.md`; pre-change register and dispositions
> `02_risk_review.md`. This note records the operator's decisions, what
> changed, the evidence and the live counts (counts only). The post-change
> adversarial review by a fresh context that did not write the code is the
> workflow's next step and is NOT recorded here: this note was written by
> the implementer, so nothing below is a review verdict. Model names are
> omitted.

## The operator's answers (2026-11-09, binding)

"1 - allow 2 - Flag, dont block 3 - if no set backs do not block, we will
jus investigate further 4- Keep for now"

| | Decision | Code |
|---|---|---|
| D1 | Allow ArcGIS Online public layers whose robots.txt answers 403 | `rules_scrape.py` (shared verdict), `adjacency_import.py`, `hazards_import.py` |
| D2 | Flood: flag, don't block (AE with LOMR, AH/AO/A99/AR; also V/VE) | none; recorded in `reviews/26_flood_wetlands/03_decision_note.md`, pins |
| D3 | No district setbacks: do not block, investigate further | `site_fit.py`, `research.py`, `reporting.py` |
| D4 | Keep the Zoneomics cache and committed quotes | none; recorded in `reviews/24_rules_local_files/03_decision_note.md`, pins |

## Decisions this change rests on

1. **D1 is one exception in one verdict.** `rules_scrape.robots_verdict` is
   the verdict of all three tools. It reads robots.txt as before; only a
   deny-all that came from a plain 401/403 of the origin's own robots.txt on
   hop 0, on `services[0-9]*.arcgis.com` (or the empty org-host constant),
   for a layer metadata or `/query` path, is let through. A readable
   robots.txt always wins (FEMA stays refused), as does an off-site redirect
   (USFWS stays refused).
2. **The exception is visible.** `deny_all` stays True; each tool prints
   `robots: <host>: robots unavailable (403): ArcGIS public REST accepted by
   operator 2026-11-09` once per host, and --watch writes it into its rows,
   report and state.
3. **Side effect named (D1-10).** rules_scrape `--flags` now reads the South
   Fulton ZoningCases layer and `--watch` reads the South Fulton, Fairburn,
   Union City and East Point layers. The loop-lock skip is unchanged.
4. **zoning_import.py not adopted (D1-11).** D1 makes its existing reads of
   the ArcGIS Online zoning layers acceptable. **Open item for a separate
   Tier 2 round:** `ZoningSession` follows redirects automatically
   (requests' default `allow_redirects=True`, any host, no https or
   off-site check, no denylist) and checks no robots.txt. Moving it onto the
   shared fetcher changes how stored zoning, a scoring input, is ingested.
5. **D3 is narrow.** Only a district LISTED in `zoning_rules.json` with all
   of front/side/rear absent or null counts with the default (today:
   Fairburn PD). Unlisted districts and unreadable values stay BTS N/A.
   **For the operator (D3-01):** "districts with no setbacks of their own"
   was read as "listed districts whose rules give no setback". A district
   the rules do not list at all (for example a residential R-1 in a
   jurisdiction whose `unlisted_districts` is `not_permitted`, which the
   zoning gate classes as REZONING) is NOT counted: counting it would let a
   residential parcel with a 25 ft default reach BTS MODERATE and the
   metric. If you want unlisted districts counted too, say so; it is a
   one-line change in the same place, between runs.
   **Fact to decide with (review round 2, F3):** the 13 parcels D3 moves
   into BTS today are all Fairburn PD, where `zoning_rules.json` lists
   `warehouse_distribution` as `not_permitted` ("Fairburn PDs are
   mixed-residential"); they pass S9 only as REZONING. So the one case the
   listed-only reading changes is itself a mixed-residential parcel that
   needs a rezoning, which weakens the reason given above for leaving
   unlisted districts out. Please answer listed-only vs also-unlisted (and
   unreadable values, D3-02) with that in hand, before the first
   verification.
   **Answered (operator, in chat, 2026-11-09): "Count them".** Unlisted
   districts AND listed districts whose setback values are unreadable also
   count with the default setback, never block, and carry their own
   investigate text. D3-01 and D3-02 are CLOSED by
   `reviews/30_d3_count_unlisted/` (dist-is-None and rules-unavailable still
   never count; Fairburn PD's text is unchanged). The first verification
   after that change needs a new run and baseline.
6. **D3 never blocks and never runs while draft.** The text is a `data_gap`
   flag written once while open and a line in the notes and memo; draft
   parcels get no projection, so nothing changes until a jurisdiction is
   verified. The first verification after this change needs a new run and
   baseline (as before; it now also moves BTS for Fairburn PD).
7. **D2 and D4 are records, not code.** `git diff` of `hazards.py` is
   empty; nothing under `reviews/23_rules_scraper/` or `rules_cache/` was
   deleted.

## Commits (local, branch claude/tender-mayer-1rd4y4, not pushed)

1. `site_fit/research/reporting: D3 listed district without setbacks counts for BTS`
2. `rules_scrape: one shared robots verdict with the D1 ArcGIS exception`
3. `reviews: record operator decisions D2, D3, D4 of 2026-11-09`
4. `reviews/29_operator_decisions: design, risk dispositions, decision note`

No protected file is in any commit (`git diff --cached --name-only` checked
before each): prepare.py, parameters.json, sources.json, program.md,
zoning_rules.json, building_prototypes.json, rules_sources.json,
requirements.txt and screener/ are unchanged.

## Evidence

- `python -m unittest discover -s tests`: 1,739 tests OK (1,702 at the base
  commit; +37: 34 in `tests/test_operator_decisions.py`, 3 in
  `tests/test_site_fit.py`). `python -m unittest discover -s screener/tests
  -t .`: 134 OK. `test_zz_human_files_unchanged` passes.
- Golden checks, unmodified, no record regenerated:
  `zoning_golden_check.py` exit 0 (identical to ba4a780);
  `site_fit_golden_check.py` exit 0 (identical to 28d3205, also with South
  Fulton verified); `adjacency_golden_check.py` exit 0 (gate off identical;
  gate on + empty table as before): its verified default-setback parcels
  fulton-Z03/Z05 are UNLISTED districts, so D3 changes nothing there and no
  expected-diff list was needed; `building_comps_golden_check.py` exit 0.
  `hazards_golden_check.py` printed "DIFFERS ... 10 case(s)" both at the base
  commit and after this change (fixed in round 2, F4): a pre-existing clock artefact
  (`discovery_date` is today; the record is of 2026-11-06). With
  `research.datetime.now` frozen to 2026-11-06 it is identical: "identical to
  the 8ddaf69 record: 16 discovery runs, 8 filter sets, 22 scored parcels +
  the cycle".
- robots answers observed 2026-11-09 (one GET of robots.txt each):
  services, services3, services7, services8.arcgis.com: `HTTP/2 403`, body
  "Invalid URL", no WAF/challenge marker; gis.atlantaga.gov and
  gismaps.fultoncountyga.gov: 404; hazards.fema.gov: 200 text with
  `Disallow: /*?*` and `Disallow: /arcgis`; fwspublicservices.wim.usgs.gov:
  301 to www.fws.gov.

## LIVE (counts only)

Setup (D1-12): a `git clone` of this branch (at commit 2) under the
session scratchpad, never committed from; in the clone only, prepare.py
carried the proposed `_DDL_PARCEL_ADJACENCY` (reviews/27) and sources.json
the reviews/21_zoning `zoning_sources` snippet. Own database
**`adj29_ad1e4c4e`** on the local PostGIS (localhost:55432), created with
`createdb -T zoningcheck` (the local corridor copy, 1,075 Fulton parcels),
schema applied there, `DATABASE_URL` set explicitly to it; **dropped
afterwards** (`pg_database` count 0). Rules states were patched in memory
only through `tests/zoning_helpers.patch_rules`; the illustrative class
tables were never written to any file. `make`-equivalent run:
`python adjacency_import.py --no-open-run-check` (the copy's mirror ends in a
baselined run; no loop runs against it). `git status` of the repo showed no
protected file afterwards.

**Fetch.** Exit 0, 1,566 s. All four ArcGIS Online hosts printed the
exception line. Sources: south_fulton_zoning 719 polygons (240 requests),
fulton_layer34_zoning 36 (+1 without a district, 233), fairburn_zoning 6,255
(295), union_city_zoning 4,721 (280), east_point_zoning 0 polygons near the
parcels (232 requests; the corridor copy has no East Point parcels),
atlanta_zoning 702 (240). Layers with a non-empty copyrightText: 0 of 6.
Written: 1,075 parcels, 2,857 rows; 3,892 sliver pieces on 861 parcels
(never a buffer); 35 parcels with a side facing two neighbour districts, 127
more with own-district overhang (whole corridor).

**Per jurisdiction** (parcels counted once per column; every jurisdiction
verified in memory). "Residential/agricultural" needs a class table: state
A has none (the real rules), state B0 adds an ILLUSTRATIVE table (South
Fulton R-*, TR, A, A-L, NUP, CUP residential, AG-1 agricultural; Fairburn
R-* / AG*; Union City R-*, R* / AG*; East Point R*), state B adds the readings
`side_rear: max` (South Fulton) and `other_district: literal` (Fairburn).

| Jurisdiction | Parcels | Other-district side | Residential side (A / B0) | Agricultural side (B0) | Two-neighbour-district | Own overhang | Unknown side |
|---|---|---|---|---|---|---|---|
| south_fulton | 364 | 208 | 0 / 98 | 28 | 13 | 47 | 309 |
| fairburn | 237 | 143 | 0 / 58 | 40 | 5 | 16 | 215 |
| union_city | 111 | 85 | 0 / 49 | 7 | 3 | 6 | 104 |
| east_point | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

(Before D1 these four sources failed: their territory read as adjacency
unknown.)

**Site-fit projections** (max building SF buckets >=300K / 150-300K /
100-150K / 50-100K / <50K / none; adjacency gate off vs on):

- State A and B0: identical to gate off in every jurisdiction (South Fulton
  28 / 63 / 35 / 0 / 0 / 238, Fairburn 23 / 52 / 38 / 0 / 0 / 124, Union
  City 17 / 15 / 13 / 0 / 0 / 66): no buffer applies without the readings.
- State B: South Fulton 5 parcels lower (100-150K 35 -> 32, none 238 ->
  241), Fairburn 4 lower (100-150K 38 -> 37, none 124 -> 125), Union City
  unchanged; 0 dropped below 150K SF, 0 gained SF anywhere.
- BTS footprint test (>= 150K SF), unchanged by the gate in every state:
  South Fulton 56, Fairburn 41, Union City 4, East Point 0. All pass the S9
  gate. **D3 effect:** Fairburn 30 projections carry the investigate text
  (listed district without setbacks), of which **13 now pass the BTS
  footprint test only because of D3** (they were N/A before); South Fulton,
  Union City and East Point 0.

**rules_scrape --watch --flags** (from the clone, its own rules_out/,
`--local-db-only` against the throwaway database before it was dropped):
all 9 GIS change signals read (9 with a count/max id, 5 with
lastEditDate); first watch in that directory, so every GIS row is
"unknown" (no previous state), never "unchanged". The sf_zoning_layer,
sf_zoning_cases, fairburn_zoning_layer, uc_zoning_layer and ep_zoning_layer
rows carry the exception text; `watch_state.json` has
`robots_exceptions` for the four hosts. Flags: 0 case layers not checked
(the ZoningCases layer was read: 82 parcels with conditions on file, 22 with
other cases, 64 only touching a case polygon). Requests: services 3,
services3 19, services7 3, services8 3, gis.atlantaga.gov 23,
gismaps.fultoncountyga.gov 3. Exit 4 for a pre-existing reason: the Union
City and Fulton PDF hosts have no terms review in rules_sources.json; seven
Zoneomics mirror sources skipped by the terms block.

## Not done / for the human

- The independent post-change adversarial review (Tier 2) — next workflow
  step.
- D1-11: zoning_import.py redirect/robots residual (decision 4).
- D3-01: confirm that unlisted districts (and unreadable values, D3-02)
  stay BTS N/A, knowing the only parcels D3 moves today are Fairburn PD
  where warehouse is not_permitted (decision 5, round 2 F2/F3).
  **Closed** (operator, 2026-11-09, "count them"): both count, flagged;
  implemented in `reviews/30_d3_count_unlisted/`.
- Esri / item-owner terms of use remain unverified (D1-09); D1 is the basis.
- Before verifying any jurisdiction: a new run and baseline (D3 moves BTS
  for Fairburn PD; the adjacency import, once the DDL is applied, moves
  site fit).

## Review round 2 (2026-11-09)

The independent post-change review reported eight findings (three
should-fix, five nits). This section was written by the implementer; it
records the dispositions, not a fresh verdict.

| # | Sev | Finding | Verdict | What changed |
|---|---|---|---|---|
| F1 | should-fix | `;params` on the last path segment hidden from the robots/D1 path guard by `urlparse` | fixed | `rules_scrape.robots_path` (urlsplit) is the checked path in all three fetchers |
| F2 | should-fix | D3 narrower than the operator's wording | open for the operator | decision 5 / D3-01 kept, here and in reviews/22 |
| F3 | should-fix | D3 today moves only Fairburn PD, warehouse not_permitted | recorded | decision 5 and reviews/22 carry the fact |
| F4 | nit | hazards golden check clock-dependent | fixed | the check freezes `research.datetime` to 2026-11-06 |
| F5 | nit | exception line written when nothing read under it | fixed | `note_robots_exception` in `_check`, after a let-through only |
| F6 | nit | zoning_import outside the shared policy | open (D1-11) + pin | `PinTests.test_zoning_import_hosts_are_arcgis_rest_only` |
| F7 | nit | watch Detail of a read row looked like a refusal | fixed | `read under D1 exception: <exact text>` |
| F8 | nit | east_point_zoning 232 requests for 0 polygons | not changed | later round: skip tiles outside the layer extent |

Dispositions with tests: `02_risk_review.md` § "Review round 2".

**Commit.** `rules_scrape/adjacency_import/hazards_import: review round 2
fixes for D1` (code + tests), then this note (docs). Local only, not
pushed; no protected file in either.

**Evidence.**
- `python -m unittest discover -s tests`: 1,744 OK (+5: four PathTests,
  one PinTest; the watch test's expected Detail updated). The five new
  tests fail on the round-1 code (4 failures, 1 error), checked with the
  code files stashed. `python -m unittest discover -s screener/tests -t .`:
  134 OK.
- Golden checks, unmodified records: zoning, site_fit (also South Fulton
  verified), adjacency, building_comps and now hazards all exit 0
  ("identical to the 8ddaf69 record: 16 discovery runs, 8 filter sets, 22
  scored parcels + the cycle"). All-draft scoring stays byte-identical.

**LIVE re-run after the fixes (counts only).** Same setup as above (the
clone fast-forwarded to the round-2 commit, its prepare.py/sources.json
edits kept in the clone only); own database **`adj29r2_79d90b00`** from
`zoningcheck`, DDL applied there, **dropped afterwards** (`pg_database`
count 0). `adjacency_import.py --no-open-run-check`: exit 0, 1,561 s, every
count identical to the round-1 run above (sources 719 / 36 / 6,255 / 4,721
/ 0 / 702 polygons, 2,857 rows on 1,075 parcels, 35 two-neighbour-district
parcels, 127 own overhang; the four ArcGIS Online hosts each print the
exception line once, now because a request was read under it). Per
jurisdiction, with every jurisdiction verified in memory:

| Jurisdiction | Other-district side | Residential side (no table / illustrative) | Two-neighbour | BTS footprint (of which D3) | Investigate flags |
|---|---|---|---|---|---|
| south_fulton | 208 | 0 / 98 | 13 | 56 (0) | 0 |
| fairburn | 143 | 0 / 58 | 5 | 41 (13) | 30 |
| union_city | 85 | 0 / 49 | 3 | 4 (0) | 0 |
| east_point | 0 | 0 / 0 | 0 | 0 (0) | 0 |

Site-fit projections with the gate on: unchanged without readings; with
the illustrative readings South Fulton 5 and Fairburn 4 parcels lower, 0
below 150K SF, BTS footprint counts unchanged.

`rules_scrape --watch --flags --local-db-only` (clone, fresh rules_out/):
all 9 GIS change signals read (count/max id for 9, lastEditDate for 5);
the ArcGIS Online rows of the five layers (sf_zoning_layer, sf_zoning_cases,
fairburn_zoning_layer, uc_zoning_layer, ep_zoning_layer) read "read under
D1 exception: robots unavailable (403): ArcGIS public REST accepted by
operator 2026-11-09"; `robots_exceptions` names the four hosts; flags as
before (82 / 22 / 64, 0 case layers not checked). Exit 4 for the same
pre-existing reason (two PDF hosts without a terms review).

**Behaviour a human will notice.** Watch rows read under D1 now start
"read under D1 exception:". A host whose robots.txt answers 403 but where
nothing was read is no longer named in the robots lines. A URL with
`;params` on an ArcGIS host is refused. `hazards_golden_check.py` exits 0
on any day.

**Operator steps.** (1) Answer D3-01 (listed-only vs also unlisted and
unreadable) before verifying any jurisdiction. (2) Schedule the D1-11
zoning_import round if wanted. (3) Before any verification or populated
import: new run and baseline.

**2026-11-09, D1-11 (item 4) implemented:** zoning_import.ZoningSession is now a
rules_scrape.PoliteFetcher on the shared robots verdict (code commit 16b5b97,
offline equivalence proof, operator answer 3 "fix"); pending the post-change
review in `reviews/32_zoning_import_fetcher/` (02_risk_review.md,
02_code_writer_response.md, then 03_decision_note.md).

**2026-11-09, D1-11 reviewed:** the post-change review approved it after one
docstring fix; see `reviews/32_zoning_import_fetcher/03_decision_note.md`.
Item 4 is closed.
