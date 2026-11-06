# 03 — Flood and wetlands: decision note

> **Tier 2.** Design `00_design.md`; storage proposal for the human
> `01_prepare_mutation_proposal.md`; pre-change register and dispositions
> `02_risk_review.md`. This note records the decisions the change rests on
> and the evidence. The independent adversarial post-change review (a
> context that did not write the code) is the workflow's next stage and is
> not recorded here yet; until it is, this change is implemented, not
> reviewed. Model names are omitted.

## Change

- **`hazards.py`** (new, protected, immutable during a run; imports only
  the standard library and `prepare`): NFHL categories with SiteFit's
  precedence, the three per-source states, the H4 and H6 decisions
  (thresholds from `prepare.get_parameters()` only), one read statement,
  the memo lines, the site-fit deduction texts, and the proposed DDL.
- **`hazards_import.py`** (new, protected operator tool,
  `make hazards-import`, between runs only): local NFHL/NWI shapefiles
  (default) or the public services through rules_scrape's robots policy;
  areas in PostGIS SRID 2240; one transaction per source; exit codes 0-4.
- **`research.py`** (between-runs edit): H4/H6 at scoring through gate 4;
  data_gap and review flags; the discovery stubs stop once enabled.
- **`site_fit.py`**, **`reporting.py`** (protected): the deduction inside
  site_fit's own SQL; program.md's snapshot Flood zone and Wetlands lines;
  honest projection texts in the memo.
- **`rules_scrape.py`**: the two new modules added to its
  `PROTECTED_MODULES` output guard list (nothing else).
- Docs and lists (CLAUDE.md, AUTORESEARCH_MECHANICS.md, SR-1, START_HERE,
  agent prompt, README, Makefile, .gitignore, the discovery workflow).

prepare.py, parameters.json, sources.json, program.md, requirements.txt,
zoning_rules.json, building_prototypes.json and rules_sources.json are
byte-identical to 8ddaf69 (pinned by a test).

## Decisions

1. **Storage is a proposal; the gate costs no SQL (FW-03, FW-06).**
   `hazards.enabled()` is true only when prepare.py carries
   `_DDL_PARCEL_HAZARDS`; only the human adds it (prepare-mutation
   protocol). While it is absent every consumer is byte-identical to
   8ddaf69 and the import refuses with exit 1.
2. **H4 = any area of a blocked zone at or above a 1 SF numeric noise
   floor blocks; a floodway always blocks (FW-08, FW-10).** The 1 SF floor
   is SiteFit's clip drop, not a tolerance. No sliver percentage was
   invented; the band counts let the human decide, between runs, whether to
   add one to parameters.json.
3. **`AE_with_LOMR` relaxes AE (never a floodway) to a review pass when a
   LOMR polygon intersects the parcel (FW-09).** This is program.md's "may
   pass, flag for review"; it relaxes a block. LOMA is never checked and
   every text says so. Without LOMR data AE blocks.
4. **Other SFHA zones, future conditions, levee and open water are review
   flags; unmapped / D / AREA NOT INCLUDED are data gaps; 0.2% passes
   (FW-08).**
5. **H6 fails only above `wetlands_max_pct_of_parcel`, strictly and
   exactly; a smaller positive share is a review pass ("minimum footprint
   undefined"), never gated on site fit or draft zoning (FW-07, FW-15).**
6. **Enforcement point is scoring, gate 4 (FW-02).** `FAIL:deal_killer`
   with blocker `H4: ...` / `H6: ...`; no new actionability value, no
   `actionability_block` flag. Reviews use `flag_type='review'`, which no
   gate reads. Discovery cannot enforce (no row exists at discovery, and a
   discovery reject never reaches parcel_scores), so it emits nothing for
   H4/H6 once enabled.
7. **Three states; stale by geometry md5 (FW-04).** A missing, stale or
   unreadable source is a data_gap pass, never a block and never "clear".
8. **Site fit deducts SFHA (floodway included) and NWI wetlands, not the
   0.2% zone, inside its own SQL, conservatively (FW-14).** Fill,
   compensatory storage and stream/state-waters buffers are not modelled
   and every projection says so once enabled. BTS still moves only for
   verified jurisdictions.
9. **Data route (FW-01).** Both hosts' robots.txt deny the REST queries;
   the tool honours that unchanged and offers the local-file route as the
   default. No override in code.
10. **The first populated `make hazards-import` is the intended metric
    change.** H4/H6 begin failing parcels through gate 4 and, for verified
    jurisdictions, the site-fit deduction can move BTS. Run it between
    runs only, then start a new run and baseline; re-import at a new FIRM
    or NWI release, again between runs. Applying the DDL with an empty
    table changes no score (only flag text).

## Evidence

- Offline suite: `python -m unittest discover -s tests`: 1,464 tests, OK
  (1,359 at 8ddaf69; 105 new). Screener suite: 134 tests, OK.
- Golden checks, all exit 0: `tests/zoning_golden_check.py` (ba4a780
  record), `tests/site_fit_golden_check.py` (28d3205 record, including the
  south_fulton-verified case), `tests/hazards_golden_check.py` (new
  8ddaf69 record: 16 discovery runs, 8 filter sets, 22 scored parcels and
  the cycle; statements, parameters, inserts, notes). The record was
  generated from `git show 8ddaf69:research.py` run from its own
  directory (recipe in the check's docstring).
- `tests/live_hazards_check.py` against a local PostGIS (own database,
  created and dropped by the check, the proposed DDL applied there only),
  synthetic shapefiles: half-covered 1,000 ft square SFHA share 0.50000;
  2240/geography area 0.99982; bogus Shape_Area ignored; donut hole
  excluded (840,000 SF); overlapping AE counted once; self-intersecting
  NWI ring imported; all shares in [0, 100], SFHA >= floodway; LOMR review
  vs LOMR-in-bbox block; 0.5 SF pass / 1.5 SF block; unmapped gap; 0.2%
  pass; excavated pond flag; re-run 0 rows changed; every file reversed:
  identical rows; 200 ft floodway strip (300 ft AE band) deducted:
  buildable 2,102,500 -> 1,661,700 SF with every fitted envelope
  `ST_Disjoint` from the strip; the fit cache follows the data; geometry
  edit -> stale; NWI failure mid-write -> exit 4 with the NWI columns
  byte-identical; partial NFHL file changed 1 parcel; cycle prefetch equals
  the per-parcel read; a parcel without a row scores exactly as with the
  gate off. `tests/live_site_fit_check.py` and `tests/live_zoning_check.py`
  still pass.

## LIVE (counts only)

Own database copies of the 1,075 corridor parcels (`createdb -T landtest`;
`landtest` itself untouched), and a scratchpad clone with prepare.py
patched exactly as the proposal says (`prepare.apply_schema` created
`parcel_hazards` there).

- **The live import could not run with real data.** `--mode rest`:
  1,075 Fulton parcels, 0 outside EPSG:2240's area of use; source nfhl
  FAILED on robots ("robots.txt disallows this path" at hazards.fema.gov),
  source nwi FAILED on robots ("robots unavailable (robots redirect
  off-site)"); requests: hazards.fema.gov 1, fwspublicservices.wim.usgs.gov
  1 (the two robots.txt), no data request; wall time 1 s; exit 4.
  `--mode files`: both sources FAILED, files not present (the NFHL county
  package and the NWI state shapefile were not downloaded in this stage:
  their download pages are browser-only, and msc.fema.gov's robots.txt
  disallows `/portal/downloadProduct?productID=`); exit 4. **No hazard
  counts exist** (parcels with SFHA, floodway, 0.2% zone or wetlands, the
  share distribution, H4/H6 fail counts, the change in site-fit
  projections, the "too complex" count and the real-data wall time are all
  unmeasured), and none were fabricated or substituted. They are what the
  first operator run of `make hazards-import` with downloaded files will
  print.
- **Present-and-empty state on the 1,075 parcels** (DDL applied, no rows),
  `run_scoring_cycle` with the gate off (unpatched repo) vs on (patched
  clone): 1,075 scored on each; every parcel_scores field (composite,
  confidence, actionability, blockers, sub-scores, strategy fit, primary
  strategy, notes) identical; metric 15 vs 15; 2,150 new open data_gap
  flags (one H4 and one H6 "no hazards import for this parcel" per parcel);
  a second and a third scoring cycle added 0 open flags. Snapshot of the
  top parcel: only the Flood zone / Wetlands lines, the projection's
  flood/wetlands text and the new flags differ.
- `make status` (cli status) against the database with the table: the same
  checks and outcome as the copy without it; the extra table is not
  misreported.
- Databases created for this stage (hz_fw26, hz_off, and the live checks'
  livehazards_*) were dropped; none remain.

## Not done / for the human

- Apply `01_prepare_mutation_proposal.md` to prepare.py (and
  STORAGE_ARCHITECTURE.md), between runs; optionally add `review` to the
  flag_type vocabulary comment in STORAGE_ARCHITECTURE.md.
- Download the two datasets into `hazards_inbox/`, run
  `make hazards-import`, read the band counts, then start a new run and
  baseline.
- Decide whether boundary slivers need a parameters.json tolerance
  (prepare-mutation protocol), after seeing the real SFHA bands.
- Decide whether to resolve the old open "H4 flood unjoined" / "H6 wetlands
  unjoined" stub flags once the data exists (they stay open).
- The post-change adversarial review (next workflow stage).
