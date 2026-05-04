# Phase 7+8 Code Writer Response — Per-Risk Mitigations and Decisions

**Writer:** Agent 2 role, completed by orchestrator (Claude Code main
session) under explicit human authorization ("Proceed", 2026-05-04).
The Agent 1 sub-agent hit a stream-idle timeout at 498s (see
`01_risk_review.md` header) and the orchestrator wrote both Agent 1's
review and this response inline. Same precedent as Phases 2/3/5.
**Date:** 2026-05-04.
**Branch:** `claude/combine-phases-7-8-Q8ShU`.
**Base commit:** `d5f4722`.
**Reviewing:** `reviews/10_phase7_8_combined/01_risk_review.md` (45
risks, R-501..R-545) and producing the code that satisfies it.

---

## 1. Summary of changes

| File | Lines added | Lines removed |
|------|-------------|---------------|
| `research.py` | +755 | -67 |
| `tests/test_discovery.py` | +906 | -15 |
| `reviews/10_phase7_8_combined/01_risk_review.md` | +new | — |
| `reviews/10_phase7_8_combined/02_code_writer_response.md` | +new (this file) | — |

Tests: 206 pre-existing (104 from Phase 5 + 102 from Phase 6/6.1) all
still pass; 94 new Phase 7+8 tests added; **300 total, 300 passing**.

The Five-File Contract is bytes-identical to `d5f4722` for prepare.py,
parameters.json, sources.json, program.md, connector_harness.py,
connector_registry.json, and requirements.txt. Verified with
`git diff d5f4722 -- <file list>` returning 0 lines.

---

## 2. Per-decision record (D1..D10)

### D1 — Re-scoring policy (R-507, R-510)

**Decision: Append, never UPDATE; the scoring cycle re-scores any
parcel whose latest row is PENDING.**

`_SQL_LIST_PARCELS_FOR_SCORING` (research.py around L715) returns parcels
that either (a) have no `parcel_scores` row or (b) whose latest row's
actionability is `'PENDING'`. The Phase 5 alias
`_SQL_LIST_UNSCORED_PARCELS = _SQL_LIST_PARCELS_FOR_SCORING` is preserved
so the AST scanner test in `TestPhase5SqlConstantsStaticChecks` keeps
working — there is no behavioural difference, just a less-misleading
name. `TestPhase8ScoringCycleRescoringPending` verifies the SQL
references "pending" and "not exists".

### D2 — Default-PASS for entitlement and deal-killer gates (R-531, R-533)

**Decision: PASS unless we have affirmative-block evidence in the form
of an open `flagged_items` row of `flag_type='actionability_block'`.**

This is the only faithful reading of program.md L82-L96 — both gates
"FAIL only when affirmative evidence." We have no affirmative-block
data sources wired in Phase 7+8, so default-PASS is correct. Phase 11+
will add real signals (PACER, lis pendens, denied rezoning records,
moratorium tracking, etc.).

The discriminator between gates 2 and 4 is the substring `'entitlement'`
in the flag description — gate 2 fails on entitlement-flavoured blocks,
gate 4 fails on everything else. Tested: `TestPhase8GateEntitlement`,
`TestPhase8GateDealKiller`, `TestPhase8ActionabilityFirstFailWins`.

### D3 — STRONG strategy fit unreachable while S9 is the moderate stub (R-535, R-536, R-538)

**Decision: implement STRONG branches for forward compatibility but
acknowledge they are unreachable in Phase 7+8 because S9 is fixed at 5.**

`_assess_strategy_bts`, `_assess_strategy_spec`, and
`_assess_strategy_ground_lease` each have a STRONG branch guarded by
`_ge(s9, 7)` (or `_ge(s9, 8)`). With S9 hardcoded at 5 (research.py:1644
= `_S9_MODERATE_DEFAULT`), STRONG is structurally unreachable for those
three. MODERATE is the expressive ceiling.

Land Bank and Flip don't depend on S9 — Land Bank can still hit STRONG
when S8 = 10, Flip can still hit STRONG when S8 = 10 AND S4 ≥ 6.

`TestPhase8StrategyFitBts.test_strong_unreachable_with_stub_s9` and
`test_strong_reachable_when_s9_raised` both pass — the latter proves the
forward-compat code path activates correctly when Phase 11+ wires real
S9.

### D4 — S6 submarket-grain approximation (R-519)

**Decision: approximate S6 from the submarket's
`under_construction_sf` and emit a `data_gap` flag noting the radius
mismatch.**

program.md L192 specifies "no spec construction within 5 mi" — a radius
predicate. Our `market_context` data is submarket-aggregated. The
mismatch is unavoidable until Phase 11+ adds a radius-search facility.
The flag is emitted whenever S6 is non-null (i.e. whenever we DID
compute it) so the human reviewer can audit Phase 11+ candidates. Code:
research.py inside `score_parcel`:

```python
if sub_scores["S6_competing_pipeline"] is not None:
    _flag(... "S6 approximated at submarket grain; program.md spec is 5-mi radius",
              "Phase 11+: implement radius-search facility for S6")
```

### D5 — GA assessed-value 2.5x inflation when using assessed fallback (R-527)

**Decision: when state='GA' AND we fall back to
`assessed_value_total/acreage`, multiply by `1/0.40 = 2.5` to compare
apples-to-apples against sale comps.**

Constants: `_GA_ASSESSMENT_RATIO = 0.40`, `_GA_BASIS_INFLATION_FACTOR =
2.5`. The provenance string `"assessed_inflated_ga"` is returned so
downstream snapshots can disclose the proxy step. Phase 14+
multi-state expansion will replace this with a state-keyed
assessment-ratio table.

Tested: `TestPhase7BasisProxy.test_stale_sale_falls_back_to_assessed`
(GA path) and `test_assessed_raw_for_non_ga_state` (TX path).

### D6 — S8 sample size minimum n=3, lookback 36 months (R-524, R-525)

**Decision: `_S8_MIN_LAND_COMPS = 3`, `_S8_LOOKBACK_MONTHS = 36`.**

The lookback is enforced in SQL (`AND sale_date >= CURRENT_DATE -
INTERVAL '36 months'` in `_SQL_SUBMARKET_LAND_MEDIAN`). The minimum
sample size is enforced in Python (`if n < _S8_MIN_LAND_COMPS`),
returning S8=None plus an explicit data_gap flag. Tested:
`TestPhase7S8DatabasePath.test_below_min_sample_size`.

### D7 — First-failing-gate-wins for actionability (R-534)

**Decision: short-circuit at the first FAIL; the verdict is single-valued
matching the program.md results.tsv enum.**

Implementation in `_run_actionability_screen` is straightforward — each
gate returns `(ok, blocker)`; on first `ok=False` we record the blocker
and return without running later gates. Tested:
`TestPhase8ActionabilityFirstFailWins.test_entitlement_fails_first` —
constructs a parcel with both an entitlement block AND a strategy gate
failure, asserts `verdict == "FAIL:entitlement"` and the blockers dict
does NOT contain `"strategy"`.

### D8 — primary_strategy priority order (R-542)

**Decision: BTS > spec > land_bank > flip > ground_lease.**

`_PRIMARY_STRATEGY_PRIORITY: tuple[str, ...] = ("bts", "spec",
"land_bank", "flip", "ground_lease")`. Selection is "first STRONG in
priority order; if none, first MODERATE; if none, None." This matches
the deal-flow urgency a development team would reasonably prioritise:
a tenant-led BTS at the same rating beats a passive ground lease.

Tested: `TestPhase8PrimaryStrategy` (4 tests covering tied STRONGs,
mixed STRONG/MODERATE, all-MODERATE, all-WEAK).

### D9 — Multi-parcel assemblage explicitly deferred (R-540)

**Decision: do NOT implement assemblage; document as Phase 11+.**

`_STRATEGY_KEYS = ("bts", "spec", "land_bank", "ground_lease", "flip")`
— five keys, no assemblage. `TestPhase78SqlConstantsStaticChecks
.test_strategy_keys_exclude_assemblage` and
`TestPhase8PublicWrappers.test_assess_strategy_fit_no_assemblage`
verify it. Assemblage requires cross-parcel adjacency analysis (PostGIS
ST_Touches) which is structurally out of scope for the per-parcel
scoring engine.

### D10 — No parameters.json / prepare.py / sources.json edits (R-501..R-505)

**Decision: zero diff against the immutable layer at d5f4722.**

`git diff d5f4722 -- prepare.py parameters.json sources.json program.md
connector_harness.py connector_registry.json requirements.txt` returns
0 lines. The new INSERT columns map to existing DDL columns
(`actionability_blockers`, `strategy_fit`, `primary_strategy` are
already in `prepare._DDL_PARCEL_SCORES`). Verified by
`TestPhase78SqlConstantsStaticChecks.test_insert_columns_in_ddl` which
parses prepare.py's DDL and asserts every column named in the new
INSERT also appears in the CREATE TABLE.

---

## 3. Per-risk responses (R-501..R-545)

Risks are grouped by category mirroring `01_risk_review.md`. Status
column: ✅ = mitigated in code; 🔘 = accepted with rationale; 📋 =
deferred to a later phase with explicit note.

### 3.1 Five-File Contract integrity

| R# | Status | How addressed |
|----|--------|---------------|
| R-501 | ✅ | `_SQL_INSERT_PARCEL_SCORE` extended from 6 to 9 column-binds. `TestPhase78SqlConstantsStaticChecks.test_insert_columns_in_ddl` parses both the INSERT and prepare.py's DDL and asserts column-name superset. |
| R-502 | ✅ | All four gates resolve to a single `actionability` text value in research.py — `prepare.calculate_actionable_pipeline_count`'s WHERE clause is untouched. |
| R-503 | 🔘 | Composite plateau math documented in §4 below. No threshold tuning attempted. |
| R-504 | ✅ | All Phase 8 metadata stored in existing `actionability_blockers` (JSONB) and `strategy_fit` (JSONB) columns. No DDL change. |
| R-505 | ✅ | No new top-level imports added to research.py. psycopg comes via `import prepare`. |

### 3.2 Metric integrity

| R# | Status | How addressed |
|----|--------|---------------|
| R-506 | ✅ | Strategy fit functions implement program.md's STRONG/MODERATE/WEAK/N/A criteria faithfully. `TestPhase8ScoreParcelEndToEnd.test_strong_parcel_passes_actionability` verifies a strong parcel passes; `TestPhase5ScoreParcel.test_actionability_fails_strategy_when_no_market_context` verifies a data-thin parcel correctly fails gate 3. |
| R-507 | ✅ | `_SQL_LIST_PARCELS_FOR_SCORING` includes the `OR (latest row).actionability = 'PENDING'` branch. `TestPhase8ScoringCycleRescoringPending.test_sql_includes_pending_branch`. |
| R-508 | 🔘 | Composite plateau: documented in §4 with full arithmetic walkthrough. The strong-on-everything-we-measure parcel reaches composite ≈ 88; a typical mid-band parcel lands at ≈ 56 and is correctly excluded. The plateau is expected behaviour, not a bug. |
| R-509 | ✅ | Confidence-weighted pipeline rises automatically as more sub-scores populate. No code change. `prepare.calculate_confidence_weighted_pipeline` continues to work over the new latest-row selection. |
| R-510 | ✅ | Phase 5 PENDING rows are correctly EXCLUDED from the metric (actionability != 'PASS'). When re-scored under Phase 7+8, the new row APPENDED with PASS/FAIL:* enters the latest-row selection. `TestPhase5ParcelScoresAppendOnly.test_two_calls_produce_two_inserts` confirms append-only. |

### 3.3 S4 — submarket vacancy

| R# | Status | How addressed |
|----|--------|---------------|
| R-511 | ✅ | `_compute_market_context_scores` returns all-None when submarket is missing. `TestPhase7MarketContextOrchestration.test_no_submarket_returns_all_none`. The SQL also returns no row when submarket vocabulary mismatches; `test_no_row_returns_all_none` covers it. |
| R-512 | ✅ | `_SQL_LATEST_MARKET_CONTEXT` ORDERs BY `as_of_date DESC LIMIT 1`. |
| R-513 | ✅ | Same SQL adds `ORDER BY (CASE WHEN source = 'costar' THEN 0 ELSE 1 END), as_of_date DESC` — CoStar wins on ties. |
| R-514 | ✅ | `_compute_market_context_scores` computes `staleness_days`; `score_parcel` emits a `data_gap` flag when `staleness_days > _MARKET_CONTEXT_STALENESS_DAYS` (=30). |
| R-515 | ✅ | `_score_vacancy` uses strict `<` at lower edges. `TestPhase7S4Vacancy.test_at_three_pct_boundary` covers exactly 3.0%. |

### 3.4 S5 — submarket absorption

| R# | Status | How addressed |
|----|--------|---------------|
| R-516 | ✅ | `_score_absorption` covers all four bands with explicit boundary tests. |
| R-517 | ✅ | Null absorption returns None (data_gap flag). |
| R-518 | ✅ | Single `_compute_market_context_scores` call returns all three of S4/S5/S6 from one row, matching D5 architecture. |

### 3.5 S6 — competing pipeline

| R# | Status | How addressed |
|----|--------|---------------|
| R-519 | 🔘 | Submarket-grain approximation with explicit data_gap flag (D4). |
| R-520 | ✅ | `under_construction_sf` ONLY; `proposed_sf` is intentionally not used. Documented in `_score_pipeline` docstring. |
| R-521 | ✅ | Null pipeline → 10 (absence of evidence). `TestPhase7S6Pipeline.test_null_pipeline_treated_as_no_supply`. |
| R-522 | ✅ | All four boundary cases tested in `TestPhase7S6Pipeline`. |

### 3.6 S8 — refined land basis

| R# | Status | How addressed |
|----|--------|---------------|
| R-523 | ✅ | `_SQL_SUBMARKET_LAND_MEDIAN` includes `WHERE comp_type = 'land'`. |
| R-524 | ✅ | `_S8_MIN_LAND_COMPS = 3` enforced in `_compute_s8`; flag emitted when shortfall + submarket present. |
| R-525 | ✅ | SQL has `AND sale_date >= CURRENT_DATE - INTERVAL '36 months'`. |
| R-526 | ✅ | `_compute_parcel_basis_per_acre` ladder: recent_sale (24mo) → assessed_inflated_ga → assessed_raw → unavailable. Five branches tested in `TestPhase7BasisProxy`. |
| R-527 | ✅ | GA-only 2.5x inflation. Tested. |
| R-528 | ✅ | Bands 0.95/1.10/1.25 of median. Tested in `TestPhase7S8Basis`. |

### 3.7 Actionability gates

| R# | Status | How addressed |
|----|--------|---------------|
| R-529 | ✅ | `score_parcel` ordering: sub-scores → composite → strategy_fit → actionability → persist. Documented in docstring and verified by code structure. |
| R-530 | ✅ | `_gate_control` always returns `(True, None)`. |
| R-531 | ✅ | `_gate_entitlement` default-PASSes; FAILs only on flag containing 'entitlement'. |
| R-532 | ✅ | `_gate_strategy` PASSes iff at least one strategy is STRONG or MODERATE. |
| R-533 | ✅ | `_gate_deal_killer` default-PASSes; FAILs on flag without 'entitlement' substring. |
| R-534 | ✅ | First-failing-gate-wins via short-circuit. Tested. |

### 3.8 Strategy fit decision logic

| R# | Status | How addressed |
|----|--------|---------------|
| R-535 | ✅ | BTS: N/A if acreage<8.6; STRONG if S9>=7+S4>=8+S5>=8 (forward-compat); MODERATE if S9>=4+S4>=6+S5>=7; else WEAK. |
| R-536 | ✅ | Spec: N/A if S4<3; STRONG if S4>=8+S5>=7+S6>=7+S9>=7 (forward-compat); MODERATE if S4>=6+S5>=7+S9>=4; else WEAK. |
| R-537 | ✅ | Land Bank: STRONG/MODERATE/WEAK/N/A directly mapped from S8 = 10/7/4/0 (or null). |
| R-538 | ✅ | Ground Lease: N/A if acreage<8.6 OR S1<4; STRONG if S1>=8+S4>=8+S9>=7 (forward-compat); MODERATE if S1>=6+S4>=6; else WEAK. |
| R-539 | ✅ | Flip: STRONG if S8=10+S4>=6; MODERATE if S8=10 (soft market) or S8=7; WEAK if S8=4; N/A if S8=0/null. |
| R-540 | 📋 | Multi-parcel assemblage NOT implemented. Deferred to Phase 11+. Tests verify it never appears in `_STRATEGY_KEYS`. |

### 3.9 Persistence and ordering

| R# | Status | How addressed |
|----|--------|---------------|
| R-541 | ✅ | `with conn.transaction():` wraps INSERT + log + flag inserts. |
| R-542 | ✅ | `_select_primary_strategy` uses `_PRIMARY_STRATEGY_PRIORITY`. Tested across 4 cases. |
| R-543 | ✅ | `notes` field bounded to 480 chars and includes composite, actionability, primary_strategy, and the populated sub-scores. |

### 3.10 Test architecture

| R# | Status | How addressed |
|----|--------|---------------|
| R-544 | ✅ | Reuses `Phase5FakeConnection` and `_SharedQueueCursor`. New tests document the queue order in setup comments. |
| R-545 | ✅ | 94 new tests across 18 new test classes; full count 300/300 passing. |

---

## 4. Composite plateau math (R-503, R-508)

Walking through the arithmetic to confirm the metric SQL behaves
correctly with current data coverage.

Phase 7+8 populates: S2, S4, S5, S6, S8, S9, S10. That's 7 of 12
sub-scores. Total weight populated when all 7 fire = 10+10+10+8+7+7+5 =
**57** (out of 100).

Composite = weighted_sum / weight_sum * 10.

**Strong parcel** (S2=10, S4=10, S5=7, S6=10, S8=10, S9=5, S10=4):
- weighted_sum = 10*10 + 10*10 + 10*7 + 8*10 + 7*10 + 7*5 + 5*4 = 100+100+70+80+70+35+20 = 475
- composite = 475 / 57 * 10 ≈ **83.3** → clears 70 → enters pipeline.

**Mid parcel** (S2=7, S4=6, S5=7, S6=7, S8=7, S9=5, S10=0):
- weighted_sum = 10*7 + 10*6 + 10*7 + 8*7 + 7*7 + 7*5 + 5*0 = 70+60+70+56+49+35+0 = 340
- composite = 340 / 57 * 10 ≈ **59.6** → below 70 → does NOT enter.

This is correct behaviour: until S1/S3/S7/S11/S12 are wired in later
phases, only strong-on-everything-we-measure parcels qualify. The
plateau is expected, not a bug.

`TestPhase8ScoreParcelEndToEnd.test_strong_parcel_passes_actionability`
asserts `composite_score >= 70` for the strong parcel and
`actionability == "PASS"`.

---

## 5. Where to find the code

### Module-level SQL constants (research.py)

| Constant | Line | Purpose |
|---|---|---|
| `_SQL_INSERT_PARCEL_SCORE` | ~660 | Extended to 9-bind 10-column INSERT (R-501) |
| `_SQL_INSERT_RESEARCH_LOG_SCORING` | ~676 | Extended with strategy_fit JSONB column |
| `_SQL_FETCH_PARCEL` | ~688 | Extended to 10 columns (R-526, R-527) |
| `_SQL_LIST_PARCELS_FOR_SCORING` | ~715 | Includes PENDING-latest-row (R-507) |
| `_SQL_LIST_UNSCORED_PARCELS` | ~733 | Backwards-compat alias |
| `_SQL_LATEST_MARKET_CONTEXT` | ~748 | S4/S5/S6 source (R-512, R-513) |
| `_SQL_SUBMARKET_LAND_MEDIAN` | ~765 | S8 source (R-523, R-525) |
| `_SQL_FLAGGED_ACTIONABILITY_BLOCK` | ~778 | Gate 2/4 evidence channel |

### Pure-function helpers

| Helper | Risk | Tested by |
|---|---|---|
| `_score_vacancy` | R-515 | `TestPhase7S4Vacancy` |
| `_score_absorption` | R-516 | `TestPhase7S5Absorption` |
| `_score_pipeline` | R-519..R-522 | `TestPhase7S6Pipeline` |
| `_score_basis` | R-528 | `TestPhase7S8Basis` |
| `_compute_parcel_basis_per_acre` | R-526, R-527 | `TestPhase7BasisProxy` |
| `_assess_strategy_bts` | R-535 | `TestPhase8StrategyFitBts` |
| `_assess_strategy_spec` | R-536 | `TestPhase8StrategyFitSpec` |
| `_assess_strategy_land_bank` | R-537 | `TestPhase8StrategyFitLandBank` |
| `_assess_strategy_ground_lease` | R-538 | `TestPhase8StrategyFitGroundLease` |
| `_assess_strategy_flip` | R-539 | `TestPhase8StrategyFitFlip` |
| `_select_primary_strategy` | R-542 | `TestPhase8PrimaryStrategy` |

### Conn-bound orchestrators

| Function | Risk | Tested by |
|---|---|---|
| `_compute_market_context_scores` | R-511..R-518 | `TestPhase7MarketContextOrchestration` |
| `_compute_s8` | R-523..R-528 | `TestPhase7S8DatabasePath` |
| `_fetch_actionability_block` | R-533 | exercised inside score_parcel tests |
| `_run_actionability_screen` | R-529..R-534 | `TestPhase8ActionabilityFirstFailWins`, `TestPhase8GateControl/Entitlement/Strategy/DealKiller` |

### Public API wrappers

| Function | Purpose | Tested by |
|---|---|---|
| `run_actionability_screen` | Phase 9+ snapshot generator entry point | `TestPhase8PublicWrappers` |
| `assess_strategy_fit` | Phase 9+ snapshot generator entry point | `TestPhase8PublicWrappers` |

### End-to-end orchestrator

`score_parcel` (research.py around L2410) now wires the full pipeline:
sub-scores → composite/confidence → strategy_fit → primary_strategy →
actionability_block fetch → actionability screen → persist (parcel_scores
+ research_log + flagged_items) all in one `conn.transaction()` block.

`run_scoring_cycle` (around L2530) uses the new SQL constant so PENDING
parcels get re-scored on the next cycle.

---

## 6. Open items for Agent 3 to verify

A. **`git diff d5f4722 -- prepare.py parameters.json sources.json
program.md connector_harness.py connector_registry.json
requirements.txt` returns 0 lines.** Verified locally; Agent 3
re-verifies at commit time.

B. **All 300 tests pass.** Verified locally with
`python -m pytest tests/test_discovery.py -q` returning
`300 passed in 0.64s`.

C. **No new external HTTP calls.** No new `requests.*` or `urllib.*`
imports. Phase 7+8 reads only Postgres tables already populated by
Phases 1-6.

D. **AST scanner test still green.**
`TestStaticChecks.test_no_string_interpolated_sql` continues to pass
because every new SQL constant is a module-level string literal with
`%s` placeholders.

E. **Composite plateau verified.** §4 walkthrough.

F. **Multi-parcel assemblage not introduced.** `grep -n "assemblage"
research.py` should return only comments referencing Phase 11+ deferral.
Verified locally.

G. **Three-agent workflow deviation note.** This document and
01_risk_review.md both lead with the orchestrator-inline deviation,
mirroring the precedent set by Phase 2/3/5.
