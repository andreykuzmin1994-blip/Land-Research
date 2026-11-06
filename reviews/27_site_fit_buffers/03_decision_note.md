# 03 — Site-fit adjacency buffers: decision note

> **Tier 2.** Design `00_design.md`; storage and rules-schema proposals for
> the human `01_prepare_mutation_proposal.md`; pre-change register,
> dispositions and the round-1 post-change review `02_risk_review.md`. This
> note records the change, the review verdict, the evidence, the live
> numbers (counts only), what a human will notice, and what a human must do.
> Model names are omitted.

## Verdict

| Step | Who | Result |
|---|---|---|
| Pre-change risk register (BUF-01 to BUF-24) | independent reviewer, fresh context | 24 items, every one disposed with a test |
| Implementation | implementer | built inert until a human acts; byte-identical to 595aa08 while inert |
| Post-change adversarial review, round 1 | two independent reviewers (correctness, integrity), fresh contexts, own database copy of the corridor | 12 findings confirmed by a skeptic pass: 1 high, 8 should-fix, 3 nits |
| Fixes, round 1 | implementer | all 12 fixed with a test; none deferred; one residual recorded in SR-16 (finding 2) |

**Ship as inert** (unchanged scoring until a human applies the proposal). The
round-1 fixes have not yet had a second independent review; the next
workflow stage is that re-review.

## What the reviewers verified

- Reproduced each finding on the code at 0f8781c: the "none listed" label for
  unknown districts; composed SQL strings passing the research.py guard and
  a same-id delete-and-restore passing the runner audit; the readings
  hard-coded in `BUFFER_KEYS` and `component_distance`; thin strips of a
  district solid elsewhere stored as abutting (380 of 1,510 zoned parts
  nowhere 3 ft wide on their re-run, 35 of them other-district parts on 24
  parcels); octagon overshoot (Hausdorff 9.02 ft at 110 ft, one parcel's
  fit 156,000 vs 166,400 SF); round caps removing up to 5,733 SF past the
  end of an abutting part; neighbours picked up across a street when Atlanta
  polygons run to the centreline (17 rows at 10.0 to 16.3 ft); the
  "202 ambiguous parcels" being own-district overhang (all 202 involve the
  parcel's own code).
- Confirmed nothing moved scoring while inert, and that no finding changed
  BTS today (no class table; Atlanta's 20 ft within its 40 ft setback; the
  Fairburn and South Fulton layers blocked by robots.txt).

## Change (stage plus round 1)

- **`adjacency.py`** (new, protected): source-text storage gate, fixed
  classification constants, the cited buffer-key allow-list, read-time
  classification (other district by identity; residential / agricultural /
  industrial only from a human table; unreadable codes are no district),
  the human reading switches (`neighbour_classes.readings`), and the
  per-parcel plan. Imports `prepare` and `zoning`.
- **`adjacency_import.py`** (new, protected operator tool, `make
  adjacency-import`): robots-gated fetch of the registered zoning layers;
  in PostGIS (SRID 2240) opened covers, a 5 ft reach, per-edge outward
  bands, raw codes per facing segment, uncovered sides stored as unknown;
  one transaction; exit 0-4; counts only.
- **`site_fit.py`**: one protected import (`adjacency as _adj`); the
  adjacency statements composed from the frozen buffer statements by named
  placeholders; the deduction is a flat-ended strip with round joins and
  tight vertex polygons; per-parcel texts; `adjacency_text()`.
- **`reporting.py`**: footnote and highlight variants (gate off: the old
  strings). **`runner.py`**: `parcel_adjacency` fingerprint audit (row text
  and `xmin`) once enabled.
- **Tests**: `tests/static_guards.py` string guard (folding, pieces,
  fragment allow-list); `test_adjacency*.py`, `live_adjacency_check.py`
  cases (j) to (n); `adjacency_golden_case.py` allows the new no-district
  text.
- **Docs**: SR-1 and SR-16 in `STANDING_RISKS.md`, the `adjacency.py` entry
  in `AUTORESEARCH_MECHANICS.md`, this folder.
- **`research.py`, `prepare.py`, every human-owned file: unchanged**
  (`git diff --quiet 595aa08` on prepare.py, parameters.json, sources.json,
  program.md, zoning_rules.json, building_prototypes.json, rules_sources.json,
  requirements.txt, screener/rank.py, screener/autoresearch/data and
  research.py before each commit; pinned by
  `test_adjacency_scoring.ProtectionTests`).

## Metric and run state (BUF-12)

- Gate off (the shipped state): nothing changes. Proven by
  `tests/adjacency_golden_check.py` against a frozen record of research.py,
  site_fit.py and reporting.py at 595aa08 (scoring with every fixture
  jurisdiction verified, projections and memo lines verified and draft).
- Gate on, table empty: parcel_scores inserts, results and notes identical;
  one extra read; only adjacency texts change (same check).
- **Applying the DDL plus a populated `make adjacency-import` changes site
  fit, and so BTS and `actionable_pipeline_count`, as soon as any zoning
  jurisdiction is verified and a buffer can apply. That is a new run plus a
  baseline, between runs only.** While every jurisdiction is draft, BTS and
  the metric do not move. Writing a `neighbour_classes` table or a reading
  in zoning_rules.json is the same kind of act. The import refuses (exit 3)
  once a run has its baseline; the runner discards an experiment during
  which the table changed.

## Evidence

- Offline suite: 1,615 tests, OK (`python -m unittest discover -s tests`);
  screener suite: 134 tests, OK (`python -m unittest discover -s
  screener/tests -t .`).
- Golden checks, exit 0: `tests/zoning_golden_check.py` and
  `tests/site_fit_golden_check.py` (both unmodified),
  `tests/hazards_golden_check.py`, `tests/adjacency_golden_check.py`.
- Live checks on local PostGIS 16 / 3.4 (own databases, created and
  dropped): `tests/live_adjacency_check.py` every case OK, including the
  round-1 cases: (j) delete-and-restore of every row with the same ids and
  fetched_at raises; (k) a 1 ft R-3 strip along the south of a parcel whose
  north side faces solid R-3 is not stored (782,000 SF, north side only);
  (l) own M-1 digitised 14 ft past the lot line with R-3 beyond: no R-3 row,
  nothing deducted; (m) R-3 along half the north side: 814,200 SF = 846,400
  - 460 x 70 (flat ends); (n) every deduction covers its exact 110 ft strip
  and lies within 0.5 ft of the segment's 110 ft buffer.
  `tests/live_site_fit_check.py` and `tests/live_hazards_check.py`
  unchanged, OK.

## LIVE (counts only)

Setup: a throwaway clone of this branch under the scratchpad with (a)
prepare.py carrying the proposed `_DDL_PARCEL_ADJACENCY`, (b) sources.json
carrying the reviews/21_zoning `zoning_sources` snippet; its own database
created with `createdb -T zoningcheck` (the local corridor copy with
imported zoning: 1,075 Fulton parcels), the proposed DDL applied there,
dropped afterwards. Rules states patched IN MEMORY only; no file committed.
The copy's experiment mirror ends in a baselined, never-halted run, so the
import ran with `--no-open-run-check` (no loop runs against the copy).

**Fetch (2026-11-06, re-run after the fixes).** robots.txt of
services.arcgis.com, services3, services7 and services8.arcgis.com answered
HTTP 403 (deny-all under the unchanged policy): `south_fulton_zoning`,
`fairburn_zoning`, `union_city_zoning` and `east_point_zoning` FAILED with one
request each and no data request; their territory reads as adjacency
unknown. gis.atlantaga.gov and gismaps.fultoncountyga.gov answered 404
(allow): `atlanta_zoning` 702 polygons in 240 requests,
`fulton_layer34_zoning` 36 polygons plus 1 whose code reads `UNKNOWN`
(finding 10: now no polygon) in 233 requests; 1 request/second per host;
486 s; exit 4 naming the four failed sources.

**Stored (before -> after the round-1 fixes).**

| Count | Before | After |
|---|---|---|
| zoned rows / parcels / boundary ft | 811 / 393 / 1,402,978 | 783 / 393 / 1,228,215 |
| uncovered rows (= parcels) / boundary ft | 754 / 2,582,617 | 765 / 2,615,074 |
| sliver pieces / parcels | 32 / 25 (whole covers only) | 855 / 229 (whole covers plus thin parts of solid covers, finding 4) |
| parcels with a side facing two NEIGHBOUR districts | 202 (mislabelled, finding 8) | 10 |
| parcels where the own district polygon overhangs a neighbour's side | not counted | 55 more |
| Atlanta parcels with an other-district side / an unknown side | 223 / 17 | 217 / 27 |

Every segment is SRID 2240 MultiLineString. The 202 the earlier note sent the
human to inspect were the expected own-district overhang, not overlapping
Atlanta polygons; with the 5 ft reach and the opened covers, 10 parcels face
two neighbour districts on one stretch.

**Per jurisdiction**, every jurisdiction verified in memory, three rules
states:

- **A**: the real rules values, no class table (what verifying today gives).
- **B0**: A plus an ILLUSTRATIVE class table copied from the cite prose
  (South Fulton: R-*, TR, A, A-L, NUP, CUP residential, AG-1 agricultural;
  Atlanta: R-*, RG*, MR*), no readings written.
- **B**: B0 plus the readings `side_rear: "max"` (South Fulton) and
  `other_district: "literal"` (a Fairburn table). Not a human table; never
  committed.

Parcels (a parcel counts once per column):

| Jurisdiction | Parcels | Residential side (A / B0 / B) | Residential buffer applied (A / B0 / B) | Other-district side | ... industrial | Unknown side | No district rule | Reading undecided (B0) |
|---|---|---|---|---|---|---|---|---|
| south_fulton | 364 | 0 / 10 / 10 | 0 / 0 / 10 | 13 | 3 | 187 | 109 | 10 |
| fid | 24 | 0 / 2 / 2 | 0 / 0 / 2 | 4 | 1 | 20 | 4 | 2 |
| atlanta | 335 | 0 / 29 / 29 | 0 / 0 / 0 (20 ft within the 40 ft setback) | 35 | 14 | 5 | 286 | 0 |
| fairburn | 237 | 0 | 0 | 0 (source blocked) | 0 | 87 | 120 | 0 |
| union_city | 111 | 0 | 0 | 0 (source blocked) | 0 | 16 | 95 | 0 |

"No district rule" counts parcels whose stored district the rules do not
list (finding 1: they now read "adjacency buffers not applied (no district
rule for this parcel)", no longer "none listed in the rules for this
district"); the side columns count only parcels with a listed district.

**Projected building SF** (max over prototypes, buckets >=300K / 150-300K /
100-150K / 50-100K / <50K / none):

- States A and B0: identical before and after in every jurisdiction. 0
  parcels lowered, 0 below 150K, 0 gained; BTS footprint test 100 parcels
  before and after.
- State B: South Fulton 28 / 63 / 35 / 0 / 0 / 238 before, 28 / 62 / 36 / 0
  / 0 / 238 after: **1 parcel with a lower max building SF, 1 dropped below
  150K SF** (before the fixes: 2 lowered, 1 below 150K; the flat ends and the
  tight vertex polygons no longer remove land past the abutting parts); FID:
  2 parcels deduct, max building SF unchanged; every other jurisdiction
  unchanged; 0 parcels gained buildable or building SF anywhere (BUF-06);
  BTS footprint test 100 before, 99 after.
- Status transitions: none in any state.

**Benchmark** (BUF-23), `site_fit.project_parcels` over all 1,075 parcels,
cache cleared: gate off 24.6 s, gate on 25.1 s (ratio 1.02; B0 0.98, B 1.01).
Bound < 2x: met.

## Behaviour a human will notice (once the DDL is applied)

- A parcel whose district the rules do not list says "adjacency buffers not
  applied (no district rule for this parcel)" (286 Atlanta parcels on the
  copy), not "none listed in the rules".
- Writing a `neighbour_classes` table is not enough for South Fulton/FID
  residential buffers or for Fairburn's other-district buffer: each
  projection says "side/rear reading not decided in the rules" or "which
  other districts: reading not decided in the rules" until the human writes
  `readings`.
- Every deducting projection says "buffer measured as a strip along the
  abutting boundary (flat ends)".
- The import's console separates sides facing two neighbour districts from
  own-district overhang, and counts thin parts of solid covers as slivers.

## Terms and disclaimers of the layers read

- Atlanta, `LandUsePlanning/MapServer/0` ("Zoning District"): service
  copyright "City of Atlanta - DCP"; no layer description or use terms
  published in the service; robots.txt 404.
- Fulton County, `PropertyMapViewer/MapServer/34` ("Zoning"): service
  description "This map service supporting the Fulton County Property Map
  Viewer application."; no copyright or use terms published in the service;
  robots.txt 404.
- The four ArcGIS Online services: not read (robots).
- Zoning layers are screening data, not an official zoning verification;
  every projection names the source and fetch date. Zoneomics and Municode
  (cited by the rules values) were never contacted.

## What the human must do (between runs)

1. Read `01_prepare_mutation_proposal.md`; if accepted, add
   `_DDL_PARCEL_ADJACENCY` (+ indexes) to prepare.py as a `prepare-mutation:`
   commit; `make db-check`.
2. Optionally write a cited `neighbour_classes` object per jurisdiction in
   zoning_rules.json (the example in the proposal is illustrative only),
   with the `readings` you decide:
   - South Fulton (governs FID) and FID: `"side_rear": "max"` (rear value on
     every side) or `"side"`; unset, no residential buffer applies there.
   - Fairburn: `"other_district": "literal"` or `"non_industrial"` (with an
     `industrial` list); unset, Sec. 80-372 is not applied.
3. Confirm or change the fixed readings in `00_design.md` (20 ft tolerance
   and 5 ft reach, strip with flat ends, multi-jurisdiction layers); a
   change is a Tier-2 code change.
4. `make adjacency-import`; read the console's two-neighbour count (10 on
   the copy) before relying on those parcels; then, if any jurisdiction is
   verified, start a new run and baseline.

## Not done / limits

- South Fulton (city layer), Fairburn, Union City and East Point neighbour
  zoning could not be fetched: their hosts' robots.txt answer 403 (deny-all
  under the unchanged policy). Fairburn's other-district buffer, Union City's
  95 ft and most South Fulton residential sides are tested on synthetic
  PostGIS fixtures only.
- No ROW source exists; ROW buffers are never applied (Fairburn's 25 ft is
  inside its 35/40 ft setback anyway).
- Use-based triggers (existing single-family homes, MIX with residential) and
  height-based setbacks are not modelled; every affected projection says so.
- SR-16 residual (finding 2): research.py runs site fit on its own
  connection, so a drifting edit could hide the rows inside one transaction
  (a rolled-back savepoint, a TEMP table of the same name) and leave no
  committed trace; the static guard and the `xmin` fingerprint block the
  plain and the committed routes. Earmarked hardening is named in SR-16.
- The corridor import was not run twice against the live hosts to show SR-9
  on real data (it would repeat 473 requests to two small city hosts); SR-9
  is proven on the live check fixtures (every row, ids and fetched_at
  included, unchanged on a re-run).
- The round-1 fixes have not had a second independent review.
