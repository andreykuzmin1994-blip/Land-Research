# Phase 5 Risk and Architecture Review — Scoring Engine MVP (Option B)

**Reviewer:** Agent 1 role, completed by orchestrator (Claude Code main session)
under explicit human authorization after the Phase 5 sub-agent attempt hit a
stream-idle timeout at ~480 s / 33 tool calls. Mirrors the deviation
precedents at `reviews/03_phase2_connector_harness/03_orchestrator_completion_note.md`
and `reviews/04_phase3_fulton_discovery/02_code_writer_response.md`.
**Date:** 2026-05-01.
**Branch:** `claude/add-environmental-filters-Lzf3W`.
**Scope:** Phase 5 — Scoring Engine MVP per `BUILD_PHASES.md` L84-L91, scoped
to Option B (S2 real, S9 stub-moderate, S10 OZ-portion real, all other
sub-scores null).

---

## 1. Verdict at the top

**GO-WITH-CONDITIONS.** Option B is mechanically bounded and consistent with
the BUILD_PHASES.md exit criteria ("Some parameters return null where data
isn't yet available; this is expected"). The five-file contract holds —
research.py edits only, plus a new gitignored module-private constant for
the bundled GA OZ tract polygons. The two architectural concerns worth
naming up front are R-205 (OZ data sourcing — the sandbox cannot fetch the
HUD file live, so Agent 2 must bundle a minimal-but-valid stub
GeoJSON and document the human follow-up needed to populate the real list)
and R-203 (composite formula edge cases — divide-by-zero when all
sub-scores are null must return composite=null, not 0 or NaN).

The condition for full GO is: Agent 2 explicitly addresses R-205 by
checking the bundled stub into `data/oz_ga_stub.geojson`, with a `_README.md`
in `data/` documenting the path to the real HUD download.

---

## 2. Per-deliverable risks

### A. `score_parcel(parcel_id)` — per-parcel scoring orchestrator

**Behavior contract:**

1. Read the parcel row from `parcels` by parcel_id (raise / return error
   dict if missing — caller must handle).
2. Compute sub_scores S1..S12 per the matrix below.
3. Compute composite_score per program.md L201-L203, weighted by the
   `scoring_weights` block in `parameters.json`, summing only over
   non-null sub_scores. If ALL sub_scores are null, composite_score=null.
4. Compute confidence_score = (count_of_non_null_subscores / 12), bounded
   in [0, 1].
5. Insert ONE row into `parcel_scores` with `actionability='PENDING'`
   (Phase 8), `strategy_fit=NULL` (Phase 8), `primary_strategy=NULL`,
   `investment_thesis=NULL` (Phase 9), `actionability_blockers=NULL`,
   `notes` populated with a short summary.
6. Insert one `research_log` row of action_type='scoring' with the
   composite_score in the dedicated column.
7. Return a status dict.

**Risks:** see R-200 series below.

### B. S2 (parcel geometry) — REAL implementation

**Algorithm (PostGIS-side):**

```sql
WITH g AS (
  SELECT
    geometry AS geom,
    ST_Envelope(geometry) AS bbox,
    ST_Area(geometry::geography) AS area_m2
  FROM parcels WHERE parcel_id = %s
)
SELECT
  area_m2,
  ST_Area(bbox::geography) AS bbox_area_m2,
  -- aspect ratio approximation: longer side / shorter side of bbox
  GREATEST(
    ST_XMax(bbox) - ST_XMin(bbox),
    ST_YMax(bbox) - ST_YMin(bbox)
  ) /
  NULLIF(LEAST(
    ST_XMax(bbox) - ST_XMin(bbox),
    ST_YMax(bbox) - ST_YMin(bbox)
  ), 0) AS aspect_ratio
FROM g;
```

**Score mapping** (per program.md L187):

- compactness = area_m2 / bbox_area_m2
- aspect_ratio = bbox longer / bbox shorter

| Condition | Score |
|-----------|-------|
| compactness ≥ 0.92 AND 1.0 ≤ aspect_ratio ≤ 2.0 | 10 |
| compactness ≥ 0.85 AND aspect_ratio ≤ 3.0 | 7 |
| compactness ≥ 0.65 | 4 |
| else | 0 |

**Risks:**
- R-201/R-202 (parameter discipline + SQL injection) — covered by re-using
  the Phase 3 module-level SQL constant pattern.
- R-207 — geometry can be NULL (e.g. ArcGIS feature with malformed rings
  caught upstream). Then S2 returns None; emit a flagged_items row.
- The aspect-ratio uses a planar bbox computed from WGS84 degrees; this is
  fine for ranking small parcels in mid-latitude Georgia (the lat-vs-lng
  degree skew is ~17 % at 33°N — acceptable for a coarse 0/4/7/10 mapping).
  Document this caveat in the function's docstring; do NOT introduce a
  per-parcel projection step in MVP.

### C. S9 (entitlement complexity) — stub returning 5

Per program.md L194, the score scale is:
- 10 = by-right industrial, no variances needed
- 7  = minor variance / CUP likely approved
- 4  = rezoning required but precedent exists nearby
- 1  = rezoning required, no nearby precedent or known opposition

Returning 5 is between "rezoning with precedent" (4) and "minor variance
likely approved" (7). It's the right neutral default for an unknown
entitlement state and matches the spirit of "moderate by default" from
BUILD_PHASES.md L88.

**Risks:**
- R-218 — using a fixed integer for S9 means S9 contributes a constant 5
  to every composite. This is intentional for MVP; future Phase 7+ replaces
  with a real check that joins against the Fulton zoning layer (Layer 34
  of the ArcGIS service per appendix L307). Document.

### D. S10 (incentives) — REAL OZ-portion

**Algorithm (Python-side, after fetching parcel centroid via SQL):**

1. Load the bundled GA OZ tract polygons once at module load
   (`_OZ_TRACTS = _load_oz_tracts()`, lazy property that defers disk I/O
   until first call).
2. For the parcel's centroid (lat, lng), iterate OZ tract polygons; first
   filter by bounding box, then by ray-casting point-in-polygon.
3. Return 4 if in any OZ tract (1 of 3 incentive criteria — OZ only),
   else 0.

State and local incentive checks are deferred — document this in the
function docstring as "Phase 7+ will add state EDA cross-reference and
per-municipality TIF / abatement lookups".

**Risks:**
- R-205 — see Cross-cutting below.
- R-206 — pure-Python PNPOLY ray-casting (~30 lines) avoids adding shapely.
  This is the right call: shapely pulls in GEOS and ~15 MB of native libs
  for one point-in-polygon check.
- R-219 — bounding-box pre-filter must use closed intervals (≤, not <) so
  parcels exactly on a tract boundary aren't dropped.

### E. All other sub-scores (S1, S3, S4, S5, S6, S7, S8, S11, S12)

Return `None` and emit one `flagged_items` row per missing sub-score with:
- `flag_type='data_gap'`
- `description='S<N> <pretty name> unjoined: pending Phase 5+ data wiring'`
- `suggested_resolution='Phase 5+: wire <data source from program.md> for parcel_id=<id>'`

**Risks:**
- R-220 — flag-row volume per parcel jumps from Phase 4's ~8 (H3..H10) to
  ~17 (8 hard-filter + 9 sub-score data_gap rows). Acceptable for the MVP
  but document; Phase 9 snapshot generator will need to summarize, not
  enumerate, data_gap flags by category.

### F. Composite score formula

Per program.md L201-L203:
```
composite_score = (Σ(sub_score_i × weight_i) / Σ(weight_i)) × 10
```

Apply the formula ONLY over indices `i` where `sub_score_i is not None`.
Weights come from `parameters.json["scoring_weights"]`, keyed by the canonical
names `S1_interstate_proximity`..`S12_demand_generators`.

**Edge cases (R-203):**
- All sub_scores null → composite_score = None (NOT 0, NOT NaN).
- One sub_score = 0 with non-zero weight → composite contributes 0 from
  that term; this is correct — it's the spec's "fails on this dimension"
  signal.
- Weights are non-negative integers in `parameters.json`; sum is non-zero
  iff at least one sub_score is non-null AND its weight is > 0. The weights
  in `parameters.json` are all ≥ 5, so this holds.
- The `× 10` in the spec scales the per-parameter 0–10 score to a 0–100
  composite. Confirm by inspection: max composite = (10·100 / 100)·10 = 100. ✓

### G. `run_scoring_cycle(market: str)` driver

**Behavior:**
1. SELECT parcel_id FROM parcels WHERE market = %s AND NOT EXISTS (SELECT 1 FROM parcel_scores WHERE parcel_scores.parcel_id = parcels.parcel_id);
2. For each, call `score_parcel(parcel_id)`.
3. Aggregate counts by status; return summary dict.

**Risks:**
- R-213 — cycle_id collision pattern from Phase 3 should also apply here:
  generate a `score-{market}-{ISO8601-Z}-{4hex}` cycle id, log it on every
  research_log row, abort if a row already exists with that id.
- R-211 — ONE transaction per parcel; rollback on exception; accumulate
  per-parcel status into the summary.

### H. parcel_scores writes — versioned-append

Phase 5 must NEVER UPDATE or DELETE prior parcel_scores rows. The metric
SQL in `prepare.py:558-565` (`_LATEST_SCORE_WHERE`) explicitly picks the
LATEST row per parcel via `MAX(scored_at)`. Phase 5's `score_parcel`
APPENDS one new row per call.

**Risks:**
- R-204 — lockstep with the metric SQL. Verify by re-reading prepare.py
  L558-L583 before merging.
- R-221 — `scored_at` defaults to NOW() in the DDL (`prepare.py:321`).
  Don't override unless tests need deterministic timestamps.

### I. flagged_items volume

Per parcel: 6 hard-filter flags (H5..H10 from Phase 4) + 2 (H3/H4 from
Phase 3) + 9 sub-score data_gap rows = 17. For a 4-parcel happy-path test
that's 68 rows; for a real Fulton cycle of ~500 parcels that's ~8500 rows.

Acceptable for Postgres. The downstream consumers (Phase 9 snapshot
generator) need to summarize by `flag_type` + `flag_category`, not enumerate
every row. Document.

---

## 3. Cross-cutting risks (R-200 series)

- **R-201** — Parameter immutability. Every `score_parcel` call must call
  `prepare.verify_parameters_unchanged()` once at the top of
  `run_scoring_cycle` (NOT inside the per-parcel loop) and read params
  via `prepare.get_parameters()`. **Mitigation:** mirror the Phase 3 pattern
  in `run_discovery_cycle` (research.py L1151-L1152).

- **R-202** — SQL injection. Every `cursor.execute` must use parameterised
  queries; SQL strings live as module-level constants. **Mitigation:**
  re-use the Phase 3 `_SQL_*` constant pattern; tests/test_discovery.py's
  `test_no_string_interpolated_sql` AST scanner will catch violations
  for free.

- **R-203** — Composite formula edge cases. Divide-by-zero when all
  sub_scores null. **Mitigation:** explicit `if sum_weights == 0: return None`
  branch; unit-test the all-null case.

- **R-204** — `parcel_scores` append-only contract. The metric SQL relies
  on latest-wins. **Mitigation:** Phase 5 issues only INSERTs; verify in
  the AST scanner that no UPDATE / DELETE against parcel_scores exists.

- **R-205** — OZ data sourcing (the GO-WITH-CONDITIONS condition). The
  sandbox cannot fetch the HUD opportunityzones.hud.gov GeoJSON live;
  Agent 2 must bundle a minimal valid stub at `data/oz_ga_stub.geojson`
  with 1–2 known Atlanta-area OZ census tract polygons (e.g., one tract
  in South Fulton, one in Clayton County) so the code path is exercised
  end-to-end. Add a `data/_README.md` documenting the path to the real
  HUD download URL and the human-action TODO. **Mitigation:** Agent 2
  documents this in a flagged_items row for the human at scoring-cycle
  startup ("OZ data is stub only; populate from HUD before relying on
  S10 signal").

- **R-206** — Pure-Python PNPOLY vs shapely. Adding shapely means GEOS,
  ~15 MB of native deps, and a longer install. **Mitigation:** Bundle a
  ~30-line PNPOLY ray-casting helper inside research.py; it has no
  dependencies and is well-tested numerically.

- **R-207** — Parcels with NULL geometry. **Mitigation:** S2 PostGIS query
  uses LEFT JOIN-equivalent semantics; if geometry is NULL, the query
  returns no rows and S2=None → flag.

- **R-208** — confidence_score range. Must be in [0, 1] so that
  `calculate_confidence_weighted_pipeline` SUMs to a meaningful total.
  **Mitigation:** explicit bound `min(1.0, max(0.0, populated / 12))`.

- **R-209** — action_type vocabulary. `scoring` is already in
  `program.md:127` per Phase 3.1's expansion. No spec edit needed.
  **Verified.**

- **R-210** — Idempotency / re-scoring. Calling `score_parcel` twice on
  the same parcel produces TWO parcel_scores rows. The metric picks the
  latest. **Mitigation:** unit-test the second call returns a different
  `score_id` and that the latest-wins SQL picks the most recent.

- **R-211** — Per-parcel transactions. **Mitigation:** wrap the parcel_scores
  INSERT + research_log INSERT + flagged_items INSERTs in `with conn.transaction()`.
  Mirror the Phase 3 pattern in `_process_parcel`.

- **R-212** — Tests run without DATABASE_URL. **Mitigation:** mock
  `prepare.get_connection` with `FakeConnection` (already imported in
  tests/test_discovery.py); add a similar pattern for tests/test_scoring.py
  if a new test file is created, OR co-locate the new tests in
  test_discovery.py to re-use the existing FakeConnection.

- **R-213** — `run_scoring_cycle` driver — parcel selection query and
  pagination. **Mitigation:** simple SQL with `WHERE NOT EXISTS (...)`
  subquery; pagination not needed for MVP since Fulton-cycle volume is
  ~hundreds, not millions.

- **R-214** — `score_parcel` signature stability. Future phases (8, 9)
  will call this. **Mitigation:** signature is `score_parcel(parcel_id: str) -> dict`;
  don't change in MVP; if Phase 8 needs more inputs, add via kwargs with
  defaults.

- **R-215** — H3-flag/H4-flag naming inconsistency. **Out of scope** for
  Phase 5; track as Phase 4 followup.

- **R-216** — confidence_weighted_pipeline contract. Phase 5 contributes
  a per-parcel `confidence_score` in [0,1]. The metric SUMs these. **Verified
  no breakage.**

- **R-217** — Future-phase risk. When Phase 7 wires CoStar S4/S5/S6, the
  `score_parcel` function gets a real branch for those three. **Mitigation:**
  structure the sub-score computations as a list of per-S helpers
  (`_compute_s1`, ..., `_compute_s12`), so Phase 7 swaps three of them.

- **R-218** — S9 fixed-5 means S9 contributes a constant 35 (5 × 7) to
  every composite numerator. Acceptable signal-flat for MVP. Future-phase
  risk: when S9 becomes real, parcels' composite scores will move; expect
  a baseline shift in the metric value. Document.

- **R-219** — OZ bbox pre-filter must use closed intervals. **Mitigation:**
  use `<=` and `>=` in the bbox check.

- **R-220** — flagged_items volume bump. **Mitigation:** documented;
  Phase 9 snapshot generator must group by flag_type/category.

- **R-221** — `scored_at` deterministic timestamps for tests. **Mitigation:**
  let the DDL default fire (NOW()); tests can assert the row exists, not
  its exact timestamp.

- **R-222** — Static AST checks must still pass after Phase 5 edits.
  Specifically: `test_no_immutable_writes`, `test_no_string_interpolated_sql`,
  `test_no_print_in_run_discovery_cycle` (extend the forbidden-names set
  if the new helpers should be silenced too — recommend adding
  `score_parcel`, `run_scoring_cycle`, and the new sub-score helpers to
  the silenced set).

- **R-223** — Schema verification. Confirm `parcel_scores.composite_score`
  is NUMERIC (it is, prepare.py:323), so a Python float will be coerced
  cleanly. Confirm `confidence_score` is NUMERIC (prepare.py:324). ✓

- **R-224** — `data/` directory addition. New top-level directory.
  **Mitigation:** add `data/` to `.gitignore` for now? NO — the OZ stub
  geojson must be checked in so CI and other developers see it. Add the
  directory to the repo with a `_README.md` and the stub file. The cached
  raw API responses already use `sources/` (gitignored); `data/` is for
  bundled reference data and is committed.

---

## 4. Go / no-go gates for Agent 3

Before merge of Phase 5:

1. ✅ Five-File Contract intact: parameters.json, sources.json, program.md,
   prepare.py, connector_harness.py, connector_registry.json byte-identical
   to main.
2. ✅ research.py edits: new `_compute_s2`, `_compute_s9`, `_compute_s10`,
   `_compute_composite`, `_compute_confidence`, `score_parcel`,
   `run_scoring_cycle`, plus the OZ helpers and SQL constants. Existing
   functions untouched except where the silenced-print set is updated.
3. ✅ tests/test_discovery.py edits or new tests/test_scoring.py — at
   minimum:
   - composite-formula unit tests (all-null → null, single sub-score,
     mixed null/non-null, rounding)
   - confidence-score range tests (0, 12/12, edge values)
   - S2 score mapping tests (perfect rectangle → 10; long thin → 4 or 7)
   - S9 fixed-5 test
   - S10 OZ in/out test using the bundled stub
   - score_parcel happy-path test against FakeConnection
   - run_scoring_cycle dispatch test (parcel selection, per-parcel call,
     summary aggregation)
   - parcel_scores append-only test (calling score_parcel twice produces
     two rows)
4. ✅ All existing 64 tests still pass.
5. ✅ Bundled `data/oz_ga_stub.geojson` (valid GeoJSON FeatureCollection
   with 1+ Polygon features) and `data/_README.md` (with HUD download URL
   and human-action TODO).
6. ✅ No new runtime dependency added to `requirements.txt` — pure-Python
   PNPOLY only.
7. ✅ Static AST checks pass: no immutable writes, no string-interpolated
   SQL, no print() in scoring helpers.
8. ✅ Reviewer decision document written to
   `reviews/07_phase5_scoring_mvp/03_reviewer_decision.md` with the
   APPROVE/REVISE verdict.

---

## 5. Out of scope for Phase 5

Explicitly NOT in this phase:

- S1, S3, S4, S5, S6, S7, S8, S11, S12 — all return None (Phase 6/7).
- State and local incentive checks within S10 (Phase 7+).
- The actionability screen (Phase 8).
- Strategy fit assessment (Phase 8).
- Snapshot generation (Phase 9).
- Strategy memo generation (Phase 9).
- The autonomous experiment loop (Phase 10).
- Modifying parameters.json, program.md, sources.json, prepare.py,
  connector_harness.py.
- Adding shapely or geopandas (R-206).
- Renaming `_h3_flag`/`_h4_flag` (Phase 4 followup).
- Live HUD OZ data download (R-205 — bundled stub only).
- Replacing the lat/lng planar bbox approximation in S2 with a true
  projected calculation (acceptable for mid-Georgia; revisit when expanding
  to DFW/Houston).

---

## 6. Final verdict

**GO-WITH-CONDITIONS.** The condition is R-205 — Agent 2 must bundle a
minimal-but-valid `data/oz_ga_stub.geojson` and `data/_README.md` so the
S10 code path is exercised end-to-end and the human knows how to populate
the real OZ data. All other risks are R-200 mitigated in code, tests, or
explicit acceptance.

Total risks: 24 (R-201 .. R-224). 22 mitigated in code/tests; 2 accepted
with rationale (R-218 fixed-S9, R-220 flagged_items volume).

---

AGENT 1 (orchestrator inline) DONE — verdict: GO-WITH-CONDITIONS.
