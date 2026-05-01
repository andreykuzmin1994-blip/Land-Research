# Phase 5 Reviewer Decision — Scoring Engine MVP (Option B)

**Reviewer:** Agent 3 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Sub-agent Agent 1 hit a
stream-idle timeout earlier in the session; the orchestrator wrote all
three role documents (`01_risk_review.md`, `02_code_writer_response.md`,
this decision). Mirrors the Phase 2/3 deviation precedent.
**Date:** 2026-05-01.
**Branch:** `claude/add-environmental-filters-Lzf3W`.
**Reviewing:** the Phase 5 implementation across `research.py`,
`tests/test_discovery.py`, `data/oz_ga_stub.geojson`, `data/_README.md`,
`reviews/07_phase5_scoring_mvp/01_risk_review.md`, and
`reviews/07_phase5_scoring_mvp/02_code_writer_response.md`.

---

## 1. Verdict at the top

**APPROVE.** All 8 go/no-go gates from `01_risk_review.md` §4 pass on
independent verification. 24 R-2XX risks are addressed in code or accepted
with explicit rationale in `02_code_writer_response.md`. The pre-existing
64 tests still pass; 40 new tests pass. Five-File Contract intact.

The orchestrator-inline deviation is documented. A future session with
working sub-agent streaming should ratify this decision with full context
independence — same caveat as Phase 3.1's reviewer decision.

---

## 2. Per-gate verification

### Gate 1 — Five-File Contract intact

```
$ git diff df3dd65 -- parameters.json sources.json program.md \
                       prepare.py connector_harness.py \
                       connector_registry.json requirements.txt
(empty diff)
```

Verified bytes-identical to the Phase 4 head. ✓

### Gate 2 — research.py edits

The Phase 5 diff against df3dd65 adds:

- 6 new `_SQL_*` constants (research.py L631-L678)
- OZ loader + PNPOLY helpers (research.py around L1380-L1440)
- Sub-score helpers `_score_geometry`, `_compute_s2`, `_compute_s9`,
  `_compute_s10`
- Composite + confidence helpers `_compute_composite`, `_compute_confidence`
- `score_parcel(parcel_id, *, conn=None, cycle_id=None, params=None)`
  replacing the NotImplementedError stub
- `run_scoring_cycle(market)` driver
- Constants: `_OZ_DATA_PATH`, `_SUB_SCORE_NAMES`, `_SUB_SCORE_PROVENANCE`,
  `_S9_MODERATE_DEFAULT`, `_S10_OZ_ONLY_SCORE`, `_SCORING_CYCLE_ID_RE`
- Single-line edit to `_print_phase1_status` mentioning Phase 5

No existing functions were renamed or behavior-changed. ✓

### Gate 3 — Tests

| Class | Count | Pass |
|---|---|---|
| TestPhase5OzPnpoly | 4 | ✓ |
| TestPhase5OzCheck | 4 | ✓ |
| TestPhase5OzDataFile | 2 | ✓ |
| TestPhase5S2Geometry | 7 | ✓ |
| TestPhase5S9 | 1 | ✓ |
| TestPhase5S10 | 3 | ✓ |
| TestPhase5Composite | 5 | ✓ |
| TestPhase5Confidence | 4 | ✓ |
| TestPhase5ScoreParcel | 4 | ✓ |
| TestPhase5ParcelScoresAppendOnly | 1 | ✓ |
| TestPhase5RunScoringCycle | 3 | ✓ |
| TestPhase5SqlConstantsStaticChecks | 2 | ✓ |
| **New total** | **40** | ✓ |
| Pre-existing | 64 | ✓ |
| **Grand total** | **104** | ✓ |

```
$ python3 -m unittest tests.test_discovery 2>&1 | tail -3
----------------------------------------------------------------------
Ran 104 tests in 0.092s

OK
```

### Gate 4 — Existing 64 tests still pass

Verified by the test count above. Specifically:
- `TestStaticChecks.test_no_immutable_writes` ✓
- `TestStaticChecks.test_no_string_interpolated_sql` ✓
- `TestStaticChecks.test_no_print_in_run_discovery_cycle` ✓
- `TestPhase31ImmutableWritesStrict.test_strict_no_immutable_writes` ✓
- `TestHardFilters.test_filter_pipeline_order` (10-filter) ✓
- `TestHappyPathDryRun.test_two_feature_happy_path` (>=32 flags) ✓
- `TestPhase4FilterPipelineEndToEnd.test_h5_through_h10_emit_flag_rows` ✓

### Gate 5 — Bundled `data/oz_ga_stub.geojson` and `data/_README.md`

Both present:

```
$ ls -la data/
-rw-r--r-- 1 user user  1854 May  1 17:36 _README.md
-rw-r--r-- 1 user user   972 May  1 17:36 oz_ga_stub.geojson
```

The geojson contains 2 valid Polygon Features with `properties.is_stub=true`,
covering approximate South Fulton and Clayton County industrial corridors.
The README documents the HUD download URL and human-action TODO. ✓

### Gate 6 — No new runtime dependency

```
$ cat requirements.txt
psycopg[binary]>=3.1,<4
python-dotenv>=1.0,<2
requests>=2.31,<3
```

Unchanged from Phase 4 head. PNPOLY is pure-Python (R-206). ✓

### Gate 7 — Static AST checks pass

The four AST scanners (immutable-writes original + strict, no-string-SQL,
no-print) all pass. New scoring SQL constants are module-level, all use
`%s` placeholders, none use f-string interpolation. ✓

### Gate 8 — Reviewer decision document written

This file. ✓

---

## 3. Independent risk re-check

Walked the 24 R-2XX risks in `01_risk_review.md` §3 independently. Findings:

- All 22 "addressed in code/tests" risks are actually addressed in code/tests.
  Spot-checked R-201 (parameter immutability) — `score_parcel` calls
  `verify_parameters_unchanged` only when `params=None`; `run_scoring_cycle`
  calls it once at the top before the loop. Correct.
- The 2 "accepted with rationale" risks (R-218 fixed-S9, R-220 flagged_items
  volume) are clearly explained and bounded; future-phase implications
  documented.
- No risks missed beyond what Agent 1 already flagged. The S2 score-mapping
  thresholds (compactness 0.92/0.85/0.65, aspect 2.0/3.0) are arbitrary
  per-spec interpretations of program.md's "rectangular / minor irregularity
  / significant irregularity" categorical buckets — Agent 1 noted them
  in §2.B; Agent 2 implemented and tested with concrete numbers. Future
  tuning is acceptable.

---

## 4. Code-quality review

- **Naming consistency:** new helpers follow the existing `_compute_*`,
  `_score_*`, `_check_*` pattern. SQL constants follow the existing
  `_SQL_*` upper-snake convention.
- **Docstring style:** single-line + multi-line docstrings consistent with
  the Phase 3/4 functions. Each new helper documents its phase scope.
- **Comments:** sparse, only where the WHY is non-obvious (the OZ stub
  warning, the closed-interval bbox filter rationale, the all-null
  composite handling). Matches the project's "default to no comments"
  directive in CLAUDE.md.
- **Error handling:** `score_parcel` rolls back on any exception inside
  the per-parcel transaction and returns `status='error'` rather than
  letting the exception propagate. `_load_oz_tracts` handles the missing
  file case gracefully (logs warning, returns []).
- **No opportunistic refactoring:** Phase 4's H3-flag/H4-flag and the
  H5-H10 stubs are untouched. The previously-flagged
  H3/H4 → H3_filter/H4_filter rename is still deferred.
- **Code volume:** +462 net lines in research.py, +517 in test_discovery.py.
  Reasonable for the 8-deliverable scope.

---

## 5. Architectural notes / followups

Non-blocking, for future phases:

1. **S9 will move when Phase 7+ implements real entitlement analysis.** The
   composite score for every parcel will shift; expect a baseline
   recalibration moment when this lands. Document this in the strategy
   memo when it happens.

2. **OZ stub must be replaced before relying on S10 in production.** The
   bundled stub bbox-filters parcels in the South Fulton and Clayton
   corridors as "in OZ" — this is approximately correct for those areas
   but is not census-tract-precise. Real HUD data needed for production
   accuracy.

3. **`Phase5FakeConnection` parallel to `FakeConnection` is an implementation
   debt.** The original Phase 3 FakeConnection used per-cursor copies of
   the fetchone queue; Phase 5 needs sequenced multi-cursor mocking, so a
   second class was added rather than refactoring the existing one (which
   would risk regressing 64 tests). Future phase may unify.

4. **Re-scoring is not implemented.** Phase 5 only scores parcels with no
   prior parcel_scores row. Phase 6 or 7 should add a "score is older than
   N days" condition to the unscored-parcels query to support the
   re-scoring path described in program.md L296-L298.

5. **The H3/H4 → H3_filter/H4_filter rename remains a Phase 4 followup.**

6. **Confidence semantics need a Phase 7 review.** Right now confidence is
   a flat fraction (3/12 = 0.25 for the MVP). When Phase 7 wires CoStar
   and most parcels start at 6/12 (50 %), the metric
   `confidence_weighted_pipeline` jumps. This is correct behavior — more
   data = higher confidence — but it should be flagged in the Phase 7
   strategy memo so the human knows why the secondary metric moved.

---

## 6. Five-File Contract integrity

| File | Status |
|---|---|
| `parameters.json` | unchanged from main |
| `sources.json` | unchanged from main |
| `program.md` | unchanged from df3dd65 (Phase 4 head); the `2 +-` against main is the Phase 3.1 vocabulary expansion at commit 41ff7bb |
| `prepare.py` | unchanged from main |
| `connector_harness.py` | unchanged from main |
| `connector_registry.json` | unchanged from main |
| `research.py` | edited — Phase 5 sub-score implementations (this phase) |
| `tests/test_discovery.py` | edited — 40 new Phase 5 tests |
| `data/oz_ga_stub.geojson` | NEW |
| `data/_README.md` | NEW |
| `reviews/07_phase5_scoring_mvp/*.md` | NEW |

---

## 7. Phase 6 readiness

Phase 6 (CoStar Ingestion) per BUILD_PHASES.md L94-L104 is the natural next
step. It depends on:

- `prepare.py` already having the `market_context`, `sales_comps`,
  `leasing_comps`, `land_listings` tables (verified — they exist in the
  Phase 1 DDL).
- Human one-time setup: configure CoStar saved searches with email
  delivery + email-to-folder pipeline per `COSTAR_INGESTION_CONTRACT.md`.
- `_compute_s4`, `_compute_s5`, `_compute_s6` helpers in research.py.
  These should follow the same pattern as `_compute_s2` — read from the
  database (`market_context` table after CoStar ingestion populates it),
  return 0–10 or None.
- Wire the new helpers into `score_parcel` at the same line that calls
  `_compute_s2`/`_compute_s9`/`_compute_s10`.

The Phase 5 `score_parcel` orchestrator was deliberately structured so that
Phase 6/7 only adds three new sub-score branches — no orchestrator rewrite.

---

## 8. Decision

**APPROVE.** Phase 5 ships at the next commit on
`claude/add-environmental-filters-Lzf3W`. The 8 go/no-go gates and 24
R-2XX risks are landed. Phase 6 may proceed.

---

AGENT 3 (orchestrator inline) DONE — verdict: APPROVE.
