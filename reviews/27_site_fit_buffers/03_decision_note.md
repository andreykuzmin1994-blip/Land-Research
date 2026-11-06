# 03 — Site-fit adjacency buffers: decision note

> **Tier 2.** Design `00_design.md`; storage and rules-schema proposals for
> the human `01_prepare_mutation_proposal.md`; pre-change register and
> dispositions `02_risk_review.md`. This note records the change, the
> evidence, the live numbers (counts only), what a human must do, and what
> could not be done. Model names are omitted.

## Verdict (implementer)

Built, inert until a human acts, and byte-identical to 595aa08 while inert.
All 24 register items are disposed with a test (`02_risk_review.md`). The
independent adversarial post-change review has NOT run yet (next workflow
stage); until it has, this is an implementer's note, not a review verdict.

## Change

- **`adjacency.py`** (new, protected): the source-text storage gate, fixed
  classification constants, the cited buffer-key allow-list, read-time
  classification (other district by identity; residential/agricultural only
  from a human table) and the per-parcel plan. Imports `prepare` and `zoning`.
- **`adjacency_import.py`** (new, protected operator tool, `make
  adjacency-import`): robots-gated fetch of the registered zoning layers,
  per-edge outward bands in PostGIS (SRID 2240), raw codes per facing
  segment, uncovered sides stored as unknown, one transaction, exit 0-4,
  counts only.
- **`site_fit.py`**: one new protected import (`adjacency as _adj`); the
  adjacency statements composed from the frozen buffer statements by named
  placeholders; per-parcel texts; `adjacency_text()`.
- **`reporting.py`**: footnote and highlight variants (gate off: the old
  strings). **`runner.py`**: `parcel_adjacency` fingerprint audit once enabled.
- **Lists**: rules_scrape.PROTECTED_MODULES, static_guards (PROTECTED_NAMES,
  REEXPORTING_MODULES, new `absent_module_problems`), test_mirror,
  test_discovery, the PIPELINE lists of test_site_fit_scoring and
  test_zoning_scoring, the discovery workflow (paths + the live check step),
  Makefile, and every doc list (one `docs:` commit).
- **`research.py`, `prepare.py`, every human-owned file: unchanged**
  (`git diff --quiet 595aa08` on prepare.py, parameters.json, sources.json,
  program.md, zoning_rules.json, building_prototypes.json, rules_sources.json,
  requirements.txt, screener/rank.py, screener/autoresearch/data before each
  commit; pinned by `test_adjacency_scoring.ProtectionTests`).

## Metric and run state (BUF-12)

- Gate off (the shipped state): nothing changes; proven by
  `tests/adjacency_golden_check.py` against a frozen record of research.py,
  site_fit.py and reporting.py at 595aa08 (scoring with every fixture
  jurisdiction verified, projections and memo lines verified and draft).
- Gate on, table empty: parcel_scores inserts, results and notes identical;
  one extra read; only adjacency texts change (same check).
- **Applying the DDL plus a populated `make adjacency-import` changes site
  fit, and so BTS and `actionable_pipeline_count`, as soon as any zoning
  jurisdiction is verified. That is a new run + baseline**, between runs
  only. While every jurisdiction is draft, BTS and the metric do not move.
  Adding a `neighbour_classes` table to zoning_rules.json is the same kind of
  act. The import refuses (exit 3) once a run has its baseline; the runner
  discards an experiment during which the table changed.

## Evidence

- Offline suite: 1,599 tests, OK (`python -m unittest discover -s tests`);
  screener suite: 134 tests, OK (`python -m unittest discover -s
  screener/tests -t .`).
- Golden checks, exit 0: `tests/zoning_golden_check.py`,
  `tests/site_fit_golden_check.py` (both unmodified), `tests/hazards_golden_check.py`,
  `tests/adjacency_golden_check.py` (new; gate off and gate on + empty).
- Live checks on local PostGIS 16 / 3.4 (own databases, created and dropped):
  `tests/live_adjacency_check.py` (new, every case OK: the 1,000 ft square
  with a residential side is 782,000 SF = 920 x 850 within 1 SF;
  misalignment, sliver, across-street, gap, L-shape, hole, multipart,
  Fairburn literal reading, split, own-polygon overhang, own-zoning change,
  stale, SR-9 re-run, 2x2 hazards/adjacency matrix, no SF gained, runner
  audit), `tests/live_site_fit_check.py` and `tests/live_hazards_check.py`
  (unchanged, OK).

## LIVE (counts only)

Setup: a throwaway clone under the scratchpad with (a) prepare.py carrying the
proposed `_DDL_PARCEL_ADJACENCY` in `_ALL_DDL`, (b) sources.json carrying the
reviews/21_zoning `zoning_sources` snippet; its own database `bufadj_live`
created with `createdb -T zoningcheck` (the local corridor copy with imported
zoning: 1,075 Fulton parcels), schema applied there, dropped afterwards.
Verified rules were patched IN MEMORY only (zoning_helpers.patch_rules); no
file was committed.

**Run state.** The first import on the copy refused with exit 3: the copy's
experiment mirror ends in a baselined run that was never halted. The copy is
throwaway and no loop runs against it, so the import was re-run with
`--no-open-run-check`.

**Fetch (2026-11-06).** The robots.txt of services.arcgis.com,
services3.arcgis.com, services7.arcgis.com and services8.arcgis.com
answered HTTP 403, which rules_scrape's unchanged policy reads as deny-all:
`south_fulton_zoning`, `fairburn_zoning`, `union_city_zoning` and
`east_point_zoning` FAILED with one request each (the robots.txt) and no data
request; their territory reads as adjacency unknown. No count below is
synthetic or substituted. gis.atlantaga.gov and gismaps.fultoncountyga.gov
answered 404 (allow): `atlanta_zoning` 702 polygons in 240 requests,
`fulton_layer34_zoning` 37 polygons in 233 requests (1 req/s per host; wall
time 479 s; exit 4 naming the four failed sources). A first attempt failed
both MapServer layers with "layer has no object-id field" (MapServer layers
declare it only by field type); fixed (`_oid_field`, tested) before the run
counted here.

**Stored.** 811 zoned rows on 393 parcels (1,402,978 ft of boundary), 754
uncovered rows on 754 parcels (2,582,617 ft); every segment SRID 2240
MultiLineString; 32 sliver pieces on 25 parcels (never a buffer); 202 parcels
with a boundary part facing two different district codes (both stored, the
stricter applied at read; mostly Atlanta, where the zoning layer carries
overlapping polygons: a question for the human before relying on it).

**Per jurisdiction**, two rules states, every jurisdiction verified in memory:

- **A: the real rules values, no class table** (what the human would get by
  verifying today).
- **B: A plus an ILLUSTRATIVE class table** copied from the cite prose
  (South Fulton: R-*, TR, A, A-L, NUP, CUP residential, AG-1 agricultural;
  Atlanta: R-*, RG*, MR*), to show what the code does once a human writes
  one. Not a human table, never committed.

Counts of parcels (a parcel counts once per column):

| Jurisdiction | Parcels | Residential side (A / B) | Residential buffer applied (A / B) | Other-district side | ... of which industrial | Unknown side | Split | Class not known (A / B) |
|---|---|---|---|---|---|---|---|---|
| south_fulton | 364 | 0 / 10 | 0 / 10 | 13 | 3 | 187 | 3 | 13 / 3 |
| fid | 24 | 0 / 2 | 0 / 2 | 4 | 1 | 19 | 0 | 4 / 4 |
| atlanta | 335 | 0 / 29 | 0 / 0 (20 ft is within the 40 ft setback) | 37 | 14 | 4 | 1 | 38 / 27 |
| fairburn | 237 | 0 / 0 | 0 / 0 | 0 (source blocked) | 0 | 87 | 0 | 0 / 0 |
| union_city | 111 | 0 / 0 | 0 / 0 | 0 (source blocked) | 0 | 16 | 0 | 0 / 0 |

(The plan columns count only parcels whose district has a buffer in the
rules; parcels in districts without one read "none listed in the rules".
Fairburn's other-district buffer would apply in state A, but its source is
blocked, so no Fairburn side has a known neighbour.)

**Projected building SF** (max over prototypes, per jurisdiction, buckets
>=300K / 150-300K / 100-150K / 50-100K / <50K / none):

- State A: identical before and after in every jurisdiction (no buffer could
  apply: no class table, Fairburn's source blocked, Atlanta's buffer within
  its setback). 0 parcels lowered, 0 below 150K, 0 gained; BTS footprint test
  100 parcels before and after.
- State B: South Fulton 28 / 63 / 35 / 0 / 0 / 238 before, 28 / 62 / 36 / 0 /
  0 / 238 after: 2 parcels with a lower max building SF, **1 dropped below
  150K SF**; every other jurisdiction unchanged; 0 parcels gained buildable or
  building SF anywhere (BUF-06); BTS footprint test 100 before, 99 after.
- Status transitions: none in either state (no ok -> error, no ok -> empty).

**Benchmark** (BUF-23), `site_fit.project_parcels` over all 1,075 parcels on
the copy, cache cleared: gate off 24.2 s, gate on 24.8 s (ratio 1.02; state
B 1.01). Bound < 2x: met.

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
2. Optionally write a cited `neighbour_classes` table per jurisdiction in
   zoning_rules.json (the example in the proposal is illustrative only).
3. Decide the open readings in `00_design.md` (Fairburn literal reading, rear
   value on every side, 20 ft tolerance, multi-jurisdiction layers) and look at
   the 202 parcels' overlapping Atlanta polygons.
4. `make adjacency-import`; then, if any jurisdiction is verified, start a new
   run and baseline.

## Not done / limits

- South Fulton (city layer), Fairburn, Union City and East Point neighbour
  zoning could not be fetched: their hosts' robots.txt answer 403 (deny-all
  under the unchanged policy). Fairburn's other-district buffer, Union City's
  95 ft and most South Fulton residential sides are therefore untested on real
  data; they are tested on synthetic PostGIS fixtures only.
- No ROW source exists; ROW buffers are never applied (Fairburn's 25 ft would
  be a no-op inside its 35/40 ft setback anyway).
- Use-based triggers (existing single-family homes, MIX with residential) and
  height-based setbacks are not modelled; every affected projection says so.
- The corridor import was not run a second time against the live hosts to
  show SR-9 on real data (it would repeat 473 requests to two small city
  hosts); SR-9 is proven on the live check fixtures (every row, ids and
  fetched_at included, unchanged on a re-run), and the second corridor run
  left 682 parcels unchanged from the first (failed) run as expected.
- The independent adversarial review has not run in this stage.
