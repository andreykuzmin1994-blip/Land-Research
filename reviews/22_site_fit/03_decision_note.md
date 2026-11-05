# 03 — Site fit: decision note

> **Tier 2.** Design `00_design.md`; pre-change register and dispositions
> `02_risk_review.md`. This note records the decisions the change rests
> on and the evidence, for the independent post-change adversarial review
> (a context that did not write the code), whose verdicts are added below
> when it has run. Model names are omitted.

## Decisions

1. **Scope of the scoring switch (SF-02).** Only `_assess_strategy_bts`
   changes, and only for parcels that pass the S9 gate:
   `zoning.classify_stored(...)` is `verified` AND the class is by_right,
   conditional or rezoning (`research._zoning_verified`). For those, the
   program.md "accommodates >= 150K SF footprint" test is
   `site_fit.meets_bts_footprint(projection)` (largest fitted prototype
   >= 150,000 SF); anything else (no geometry, error, prototypes
   unavailable, nothing fits) is BTS N/A, never the acreage proxy. Every
   other parcel keeps the 8.61 ac proxy exactly. **Ground lease keeps the
   acreage proxy for every parcel.** No other strategy, sub-score or gate
   changes.
2. **Flipping a jurisdiction to verified now changes S9 AND BTS
   together.** Like any `zoning_rules.json` verification it needs a new
   run and baseline. Once anything is verified, a change to
   `building_prototypes.json` or `site_fit.py` (algorithm, angle grid,
   tolerance) and a PostGIS/GEOS upgrade can move BTS ratings, so they
   are metric changes: between runs only, then a new run and baseline
   (SF-20).
3. **Fit rule (SF-08).** The full envelope (building L x W plus truck
   court and trailer parking per dock side) must lie inside the
   setback-buffered area. This is stricter than SiteFit's own check,
   which tests only the building against the setback line; truck drives
   and circulation are not modelled either way. Lattice 40 ft; minimum
   length 600 ft cross-dock (L >= W), 400 ft front/rear (104,000 SF).
4. **Setback rule (SF-09).** Uniform setback = max(non-null front, side,
   rear) of the district on every side; a known district with none of the
   three numeric, an unlisted district, or no zoning gets the 25 ft
   default, labelled. Buffer keys are never folded in. A non-numeric
   value is reported, not guessed.
5. **Residential buffers are not applied (SF-10)**: adjacency cannot be
   detected (neighbours are not in the database). Every projection says
   so.
6. **Coverage (SF-11)** = building SF / gross geography area of the
   parcel (never assessor acreage). A cap steps the length down on the
   lattice. Height is not checked; a note names the clear height and the
   district maximum.
7. **The projection cannot be steered from research.py (SF-03).** The
   public API takes `(conn, parcel_ids)` only; the module reads geometry,
   zoning and prototypes itself; the result is a frozen dataclass; the
   150K threshold and the test live in `site_fit`. Static tests pin that
   research.py uses only `project_parcel`, `project_parcels`,
   `meets_bts_footprint`, `data_gap_text` and
   `verify_prototypes_unchanged`, assigns nothing to `site_fit.*` or to a
   result field, reads no result field, passes no extra arguments and
   never names the prototypes file. **Runtime monkeypatching of the
   protected module from research.py remains SR-16 (accepted)**: it
   needs a drifting agent and is visible in the `exp:` diff.
8. **Car parking is not modelled (SF-21)**; no stall count is derived
   from leftover area.
9. **The operator command does not refuse a non-local database
   (deviation from SF-18).** The operator's real database is the hosted
   one, so refusing it would make the command useless. Instead it
   refuses without `DATABASE_URL` (exit 2, the `zoning_import`
   pattern), sets the session read-only, selects only parcel id,
   geometry, zoning and scores, never prints owner names or addresses,
   and writes only to the gitignored `site_fit_studies/`. It needs no
   run-state refusal because it writes nothing shared.

## Evidence (live, local PostGIS 3.4.3 / GEOS 3.9.0 / PROJ 7.2.1)

On copies of the 1,075 Fulton corridor parcels with imported zoning (all
rules draft). Counts only.

- **Metric unchanged.** The same copy scored by research.py at 28d3205
  and by this change: 1,075 `parcel_scores` rows each, every column
  (composite, confidence, actionability, blockers, sub_scores,
  strategy_fit, primary_strategy, thesis, notes, run_tag) byte-identical;
  `flagged_items` row counts equal; `actionable_pipeline_count` 15 in
  both. Scoring cycle 3.7 s (old) and 3.4 s (new).
- **Offline proof.** `tests/site_fit_golden_check.py`: identical to the
  28d3205 record (results, every SQL statement and parameter, inserts,
  notes; per parcel and cycle). With South Fulton verified, the 14
  parcels of other jurisdictions are identical apart from zoning's own
  stored-zoning read. `tests/zoning_golden_check.py` still exits 0.
- **Projections over all 1,075 parcels** (draft rules, as the memo shows
  them): ok 1,065, empty after setbacks 10. Cross-dock fits at full size
  21, only after shrinking 74; front-load (and rear-load) full size 271,
  only after shrinking 150; no prototype fits 644 (median acreage of
  those 7.3 ac). Median projected max building 166,400 SF (quartiles
  135,200 / 166,400 / 166,400). Setback from a district rule 427, the
  25 ft default 648 (mostly districts not in the rules).
- **Agreement with the acreage proxy** (>= 8.61 ac vs >= 150K SF
  projected): both 293, proxy only 313, projection only 1, neither 468.
- **Units.** SRID 2240 area / geography area: median 0.99983, all within
  0.1%. 25 ft inward buffer, 2240 vs geography: within 0.5% for all
  1,053 parcels that keep at least half their area (median 0.99976; the
  12 slivers differ by up to 18% of a remainder under 3% of the parcel).
- **Containment.** All 937 reported envelopes are covered (`ST_Covers`)
  by PostGIS's own unrounded buffer.
- **Runtime.** All 1,075 projections in one batch: 17.5 s (16 ms per
  parcel); the slowest single parcel uncached 1.4 s; a cached repeat
  0.5 s and identical. Informational, every jurisdiction verified: the
  scoring cycle takes 11.9 s; BTS MODERATE 535 -> 409, N/A 469 -> 609,
  WEAK 71 -> 57 against the proxy with the same verified S9;
  `actionable_pipeline_count` 29 either way on this copy.
- `tests/live_site_fit_check.py` (synthetic, own database): all checks
  pass (units, degenerate geometry, savepoint recovery, ST_Covers, the
  SiteFit export placed with SiteFit's own rot/toLL math inside a
  geography buffer of the exported boundary).

Suites: `python -m unittest discover -s tests` (1,150 tests) and
`python -m unittest discover -s screener/tests -t .` (134) pass.

## Post-change adversarial review

Pending: to be run by a fresh context that did not write the code.
