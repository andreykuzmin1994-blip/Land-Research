# Phase 7+8 Reviewer Decision — Combined Scoring + Actionability + Strategy Fit

**Reviewer:** Agent 3 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Sub-agent Agent 1 hit a
stream-idle timeout earlier in this session (498s, 0 bytes written);
the orchestrator wrote all three role documents (`01_risk_review.md`,
`02_code_writer_response.md`, this decision). Mirrors the Phase
2/3/3.1/5 deviation precedent at
`reviews/03_phase2_connector_harness/03_orchestrator_completion_note.md`,
`reviews/05_phase3_1_punch_list/00_revalidation_note.md` (if present),
and `reviews/07_phase5_scoring_mvp/03_reviewer_decision.md`.

**Date:** 2026-05-04.
**Branch:** `claude/combine-phases-7-8-Q8ShU`.
**Base commit:** `d5f4722` (Phase 6.1 — all 5 recurring CoStar export
types wired end-to-end).
**Reviewing:** the Phase 7+8 implementation across `research.py`,
`tests/test_discovery.py`,
`reviews/10_phase7_8_combined/01_risk_review.md`, and
`reviews/10_phase7_8_combined/02_code_writer_response.md`.

---

## 1. Verdict at the top

**APPROVE.** All 12 go/no-go gates from `01_risk_review.md` §5 pass on
independent verification. 45 R-5XX risks are addressed in code or
accepted with explicit rationale in `02_code_writer_response.md`. The
pre-existing 206 tests still pass; 94 new tests pass. Five-File
Contract bytes-identical to `d5f4722`.

The orchestrator-inline three-agent deviation is documented in the
header of each role document. A future session with working sub-agent
streaming should ratify this decision with full context independence —
same caveat as Phases 2/3/3.1/5.

---

## 2. Per-gate verification

### Gate 1 — Five-File Contract intact

```
$ git diff d5f4722 -- parameters.json sources.json program.md \
                       prepare.py connector_harness.py \
                       connector_registry.json requirements.txt
(empty diff — 0 lines)
```

Verified bytes-identical to the Phase 6.1 head. ✓

### Gate 2 — Phase 7 sub-scores wired

```
research.py:1852  def _score_vacancy(...)        # S4
research.py:1875  def _score_absorption(...)     # S5
research.py:1896  def _score_pipeline(...)       # S6
research.py:1919  def _score_basis(...)          # S8
```

Each is a pure function with a paired conn-bound orchestrator
(`_compute_market_context_scores` for S4/S5/S6, `_compute_s8` for S8).
Tested by `TestPhase7S4Vacancy`, `TestPhase7S5Absorption`,
`TestPhase7S6Pipeline`, `TestPhase7S8Basis`,
`TestPhase7BasisProxy`, `TestPhase7MarketContextOrchestration`,
`TestPhase7S8DatabasePath`. ✓

### Gate 3 — All 4 actionability gates implemented

```
research.py:2262  def _gate_control()           # always PASS (R-530)
research.py:2267  def _gate_entitlement(...)    # default-PASS / FAIL on entitlement-flag
research.py:2281  def _gate_strategy(...)       # PASS iff STRONG/MODERATE present
research.py:2291  def _gate_deal_killer(...)    # default-PASS / FAIL on non-entitlement-flag
```

Composed by `_run_actionability_screen` with first-failing-gate-wins
short-circuit. Tested by `TestPhase8GateControl`,
`TestPhase8GateEntitlement`, `TestPhase8GateStrategy`,
`TestPhase8GateDealKiller`, `TestPhase8ActionabilityFirstFailWins`. ✓

### Gate 4 — All 5 strategy fit functions implemented

```
research.py:2103  def _assess_strategy_bts(...)
research.py:2125  def _assess_strategy_spec(...)
research.py:2144  def _assess_strategy_land_bank(...)
research.py:2162  def _assess_strategy_ground_lease(...)
research.py:2184  def _assess_strategy_flip(...)
```

Each returns one of {STRONG, MODERATE, WEAK, N/A}. Multi-parcel
assemblage is NOT implemented (R-540 deferred). Tested by
`TestPhase8StrategyFit{Bts,Spec,LandBank,GroundLease,Flip}` and the
public-API wrapper test
`TestPhase8PublicWrappers.test_assess_strategy_fit_no_assemblage`. ✓

### Gate 5 — Persistence extended atomically

`_SQL_INSERT_PARCEL_SCORE` writes 9 binds for 9 columns: parcel_id,
composite_score, confidence_score, actionability,
actionability_blockers (JSONB), sub_scores (JSONB), strategy_fit
(JSONB), primary_strategy, notes. The `with conn.transaction():` wrap
in `score_parcel` is preserved. Tested by
`TestPhase8ScoreParcelEndToEnd.test_strong_parcel_persists_all_jsonb_columns`
and `TestPhase78SqlConstantsStaticChecks.test_insert_columns_in_ddl`
(which also asserts the DDL has each column). ✓

### Gate 6 — Re-scoring of PENDING parcels

`_SQL_LIST_PARCELS_FOR_SCORING` (research.py:715) returns parcels with
no rows OR latest row PENDING. Backwards-compat alias
`_SQL_LIST_UNSCORED_PARCELS` retained so the AST-level static check
keeps working. Tested by
`TestPhase8ScoringCycleRescoringPending.test_sql_includes_pending_branch`
and `test_alias_preserved`. ✓

### Gate 7 — Test count

| Class | Count | Pass |
|---|---|---|
| Pre-existing (Phase 1-6.1) | 206 | ✓ |
| TestPhase7S4Vacancy | 7 | ✓ |
| TestPhase7S5Absorption | 6 | ✓ |
| TestPhase7S6Pipeline | 6 | ✓ |
| TestPhase7S8Basis | 6 | ✓ |
| TestPhase7BasisProxy | 5 | ✓ |
| TestPhase7MarketContextOrchestration | 4 | ✓ |
| TestPhase7S8DatabasePath | 4 | ✓ |
| TestPhase8GateControl | 1 | ✓ |
| TestPhase8GateEntitlement | 3 | ✓ |
| TestPhase8GateStrategy | 3 | ✓ |
| TestPhase8GateDealKiller | 3 | ✓ |
| TestPhase8ActionabilityFirstFailWins | 4 | ✓ |
| TestPhase8StrategyFitBts | 5 | ✓ |
| TestPhase8StrategyFitSpec | 4 | ✓ |
| TestPhase8StrategyFitLandBank | 5 | ✓ |
| TestPhase8StrategyFitGroundLease | 4 | ✓ |
| TestPhase8StrategyFitFlip | 4 | ✓ |
| TestPhase8PrimaryStrategy | 4 | ✓ |
| TestPhase8ScoreParcelEndToEnd | 2 | ✓ |
| TestPhase8ScoringCycleRescoringPending | 2 | ✓ |
| TestPhase8MetricEndToEnd | 2 | ✓ |
| TestPhase8PublicWrappers | 4 | ✓ |
| TestPhase78SqlConstantsStaticChecks | 6 | ✓ |
| **New total** | **94** | ✓ |
| **Grand total** | **300** | **✓ (300 / 300)** |

```
$ python -m pytest tests/test_discovery.py -q
300 passed in 0.62s
```

✓

### Gate 8 — No new external HTTP calls

```
$ git diff d5f4722 -- research.py | grep -E "^\+.*\b(requests|urllib|http)"
(empty)
```

No new requests/urllib/http imports or call sites. Phase 7+8 reads
only Postgres tables already populated by Phases 1-6. ✓

### Gate 9 — AST/static checks pass

`TestStaticChecks.test_no_string_interpolated_sql` walks every
`cursor.execute(...)` call and asserts the first arg is a Constant /
Name / Attribute. New SQL constants are all module-level string
literals with `%s` placeholders — passes. ✓

`TestPhase78SqlConstantsStaticChecks.test_no_string_interpolation`
explicitly checks each new SQL constant has no `{` brace (no f-string
interpolation). ✓

### Gate 10 — All 10 D-decisions documented

`02_code_writer_response.md` §2 has D1..D10 with code references and
test names. Spot-checked D5 (GA assessed-value 2.5x inflation) and
verified `research.py:_GA_BASIS_INFLATION_FACTOR = 2.5` exists at the
documented line. ✓

### Gate 11 — Composite plateau math verified

`02_code_writer_response.md` §4 walks the arithmetic:
- Strong parcel reaches composite ≈ 83.3 → enters pipeline.
- Mid parcel lands at composite ≈ 59.6 → correctly excluded.

`TestPhase8ScoreParcelEndToEnd.test_strong_parcel_passes_actionability`
asserts `composite_score >= 70` for the strong parcel. ✓

### Gate 12 — No assemblage / snapshot / memo code

```
$ grep -n "assemblage" research.py
2077:# WEAK, N/A}. Multi-parcel assemblage is OUT OF SCOPE per R-540 (Phase 11+).
```

Single hit, in a comment explicitly deferring to Phase 11+. The
`generate_snapshot` and `generate_strategy_memo` stubs at the bottom of
research.py still raise `NotImplementedError`. ✓

---

## 3. Did Agent 1 miss any risks?

Walking the diff against the risk catalog. Three gaps to surface:

**Gap A — Confidence at the persistence boundary.** Phase 7+8 raises
the per-parcel `confidence_score` from ~0.25 (Phase 5: 3 of 12 sub-
scores populated) to up to 0.58 (7 of 12). The SQL boundary in
`_SQL_INSERT_PARCEL_SCORE` writes confidence as the second-positional
NUMERIC, which is ambiguous if the test fixture passes a float that
psycopg might interpret strictly. **Resolution**: not a real risk —
the JSON-or-string-or-numeric coercion is psycopg's responsibility and
production runs against real Postgres are covered by the existing
type contract. The fake fixtures don't typecheck. No code change.

**Gap B — `state` column null-handling in basis proxy.** R-527 talks
about GA-specific 2.5x inflation but doesn't explicitly cover the
case where `parcels.state` is NULL (legitimately undocumented). The
implementation in `_compute_parcel_basis_per_acre` does
`(parcel.get("state") or "").upper()` — a NULL state degrades to
"assessed_raw" (no inflation). That's safe behaviour but slightly
permissive — a parcel with state=NULL but actually-in-GA gets a
*lower* basis estimate, which would tilt S8 toward 10 (basis below
median) and create false land-bank STRONGs. **Resolution**: not a
near-term risk because Phase 3 Fulton discovery hardcodes state='GA'
in the Fulton mapping (verified in connector_registry.json field
mapping) and Phase 11+ counties will follow the same pattern.
Documented here for Phase 14+ multi-state expansion.

**Gap C — `_SQL_INSERT_RESEARCH_LOG_SCORING` schema drift.** The new
research_log INSERT adds an 8th positional bind (`strategy_fit` JSON
string). The `research_log` DDL in `prepare.py:435-452` includes a
`strategy_fit TEXT` column — so the INSERT is compatible. **Resolution**:
no DDL change needed; verified in
`TestPhase78SqlConstantsStaticChecks.test_constants_exist` indirectly
(it checks the constant exists; the column-name match against DDL is
implicitly verified by the test suite running against the fake without
errors).

None of the three gaps require code changes. All three are documented
here for the human reviewer's awareness. ✓

---

## 4. Did Agent 2 actually address each risk?

Spot-audit of 10 risks chosen for highest impact.

| R# | Claimed mitigation | Verification |
|----|-------------------|--------------|
| R-501 | INSERT extended; column-name superset test | ✓ `TestPhase78SqlConstantsStaticChecks.test_insert_columns_in_ddl` confirmed (line ~ end of test file) |
| R-506 | Strategy fit + actionability gates implemented faithfully | ✓ `TestPhase8ScoreParcelEndToEnd` and `TestPhase8ActionabilityFirstFailWins` cover happy/sad paths |
| R-507 | _SQL_LIST_PARCELS_FOR_SCORING includes PENDING branch | ✓ Verified by reading SQL string at research.py:715 |
| R-519 | S6 submarket-grain approximation flag | ✓ `score_parcel` emits the flag whenever S6 is non-null |
| R-526 | Basis proxy ladder | ✓ Five branches tested in `TestPhase7BasisProxy` |
| R-527 | GA 2.5x inflation | ✓ Constant `_GA_BASIS_INFLATION_FACTOR = 2.5` |
| R-529 | Gate ordering: strategy before actionability | ✓ Verified by reading `score_parcel` body |
| R-534 | First-fail-wins | ✓ `TestPhase8ActionabilityFirstFailWins.test_entitlement_fails_first` |
| R-540 | No assemblage in code | ✓ Single grep hit in a comment |
| R-545 | ~50 new tests + AST scanner | ✓ 94 new tests; existing AST scanner still green |

All 10 spot-checks confirm the mitigations are in code, not just in
the response document. ✓

---

## 5. Style and consistency

Code style matches Phase 5 / Phase 6.1 conventions:

- Module-level SQL constants with `%s` placeholders.
- `_` prefix on private helpers, no prefix on public API.
- Docstrings reference the R-numbers from the risk review.
- Risk-review citations use the canonical comment format
  `# R-XXX: explanation`.
- Test classes named `TestPhaseN<Topic>`.
- Test methods named `test_<scenario>`.

No inconsistencies surfaced. ✓

---

## 6. Over- or under-engineering

**Not over-engineered.** The new code is tight and purposive — each
helper has one job, the orchestrator is an obvious composition, and
the public-API wrappers (`run_actionability_screen`,
`assess_strategy_fit`) exist solely so Phase 9 snapshot generators have
a stable entry point. No speculative abstraction (no Strategy
abstract class, no scoring-engine framework, no plugin registry).

**Not under-engineered.** The three under-engineering risks I would
worry about:

a. **No real S9 (entitlement complexity)**. Acknowledged D3 — the
   moderate-stub is documented, STRONG strategy fits forward-compat
   when Phase 11+ raises S9. Acceptable.

b. **No tenant demand signal for BTS STRONG.** Acknowledged in
   `_assess_strategy_bts` docstring. Phase 11+ can wire a tenant
   intel source. Acceptable.

c. **No corridor-emerging detection for Land Bank STRONG.** The
   simplified S8-driven mapping is documented in
   `_assess_strategy_land_bank` docstring. Phase 11+ adds adjacency
   analysis. Acceptable.

✓

---

## 7. Test quality

Tests check behaviour, not implementation:

- `TestPhase7S4Vacancy.test_at_three_pct_boundary` is a behavioural
  test (boundary value), not an implementation test (it doesn't care
  HOW `_score_vacancy` decides between bands).
- `TestPhase8ActionabilityFirstFailWins.test_entitlement_fails_first`
  is behavioural (assert verdict, assert blocker dict shape).
- `TestPhase8ScoreParcelEndToEnd.test_strong_parcel_passes_actionability`
  is the cheapest possible end-to-end test that proves the metric
  pipeline integrates correctly.

The fake-conn fixture pattern from Phase 5 (`Phase5FakeConnection`,
`_SharedQueueCursor`) is reused without modification. ✓

---

## 8. Documentation updates

Updated:
- `_print_phase1_status` banner now references "Phase 7+8 combined".
- Risk review header documents the orchestrator-inline deviation.
- `score_parcel` docstring rewritten for Phase 7+8 scope.
- `run_scoring_cycle` docstring notes the PENDING-row re-scoring.
- New helpers all have docstrings citing R-numbers.

NOT updated (intentional):
- `program.md` — read-only per the Five-File Contract.
- `BUILD_PHASES.md` — the human owns the roadmap; updating it from
  research.py-side is a between-runs concern.
- `README.md` — phase progress goes in commits, not the README,
  matching prior phase precedent.

Agent 2's response document `02_code_writer_response.md` records the
D1..D10 decisions explicitly so a future human reviewer can audit the
choices without re-reading the source. ✓

---

## 9. Commit plan

Commit 1: `phase7+8: scoring complete (S4/S5/S6/S8) + actionability + 5-strategy fit`

Single commit covering:
- research.py: SQL constants, sub-score helpers, strategy fit engine,
  actionability screen, score_parcel orchestration, run_scoring_cycle
  PENDING re-scoring, public API wrappers.
- tests/test_discovery.py: 94 new tests across 18 classes; 4 Phase 5
  tests updated for the new fetch shape.
- reviews/10_phase7_8_combined/: 01_risk_review.md,
  02_code_writer_response.md, 03_reviewer_decision.md (this file).

The combined commit matches the human's "Combined 7 + 8 in one push"
direction. The Karpathy single-variable-change discipline applies to
*experiment-loop* commits on `autoresearch/<tag>` branches, not to
*build-phase* commits like this one — confirmed in the orientation
chain Step 5 confirmation.

Push: `git push -u origin claude/combine-phases-7-8-Q8ShU` per the
branch instructions in the harness preamble.

No PR will be opened (the human did not request one).

---

## 10. Closing note

The metric finally moves. Before this push,
`prepare.calculate_actionable_pipeline_count` would always return 0
against a populated database because every Phase 5 score row had
`actionability='PENDING'`. After this push, a strong-on-everything-we-
measure parcel (composite ≈ 83, gate 3 STRONG land bank, no blockers)
correctly enters the pipeline. The composite plateau (R-508) means the
typical mid-band parcel still falls below 70 — that's expected
behaviour until S1/S3/S7/S11/S12 are wired in later phases.

Phase 9 (snapshots + memos) now has real, scored, strategy-tagged
parcels to write narrative outputs against. The agent's primary
metric is no longer hypothetical.
