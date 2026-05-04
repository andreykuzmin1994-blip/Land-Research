# Phase 7+8 Risk and Architecture Review — Combined Scoring Completion + Actionability/Strategy Fit

**Reviewer:** Agent 1 role, completed by orchestrator (Claude Code main session) under
explicit human authorization ("Proceed" on the combined Phase 7+8 plan,
2026-05-04). The Agent 1 sub-agent hit a stream-idle timeout at 498s with
54 tool uses and 0 bytes written, mirroring the Phase 2/3/5 precedents
(`reviews/03_phase2_connector_harness/03_orchestrator_completion_note.md`,
`reviews/04_phase3_fulton_discovery/02_code_writer_response.md`,
`reviews/07_phase5_scoring_mvp/03_reviewer_decision.md`). The orchestrator
will also write the Agent 2 response and Agent 3 decision inline; a future
session with working sub-agent streaming should ratify these documents
with full context independence — same caveat as the prior four phases.

**Date:** 2026-05-04.
**Branch:** `claude/combine-phases-7-8-Q8ShU`.
**Base commit:** `d5f4722` (Phase 6.1 — all five recurring CoStar export
types wired end-to-end).
**Scope:** BUILD_PHASES.md Phase 7 (CoStar-dependent sub-scores S4/S5/S6
+ refined S8) AND Phase 8 (4-gate actionability screen + Strategy Fit
Assessment Engine for the five investment strategies, persisted to
`parcel_scores`). Combined into one push so the metric finally moves and
Phase 9 has real, scored, screened, strategy-tagged parcels to write
narrative outputs against.

---

## 1. The Five-File Contract

Phase 7+8 edits ONLY `research.py` and `tests/test_discovery.py`. The
metric layer (`prepare.py`) and the configuration layer
(`parameters.json`, `sources.json`) stay bytes-identical to `d5f4722`.

The temptation in Phase 8 will be to "just add an `actionability_blockers`
column" or "tighten the metric SQL to also require strategy_fit IS NOT NULL"
— both of those are `prepare.py` mutations and BOTH are forbidden in this
session (see AUTORESEARCH_MECHANICS.md "When Mutating prepare.py"). The
table DDL in `prepare.py:317-332` already has `actionability`,
`actionability_blockers`, `strategy_fit`, and `primary_strategy` columns;
the only thing missing is the INSERT statement in research.py:655-661 that
currently writes only six of the eleven columns. Extending the INSERT to
write all eleven columns is an in-`research.py` change and is allowed.

**Hard rule for Agent 2**: every diff against `d5f4722` for `prepare.py`,
`parameters.json`, `sources.json`, `program.md`, `connector_harness.py`,
`connector_registry.json`, and `requirements.txt` MUST be empty. Agent 3
verifies this as Gate 1.

---

## 2. The Metric Contract

`prepare.calculate_actionable_pipeline_count` (prepare.py:568-583) counts
parcels where:

```sql
ps.actionability = 'PASS'
AND ps.composite_score >= composite_threshold        -- 70 from parameters.json
AND ps.scored_at = MAX(scored_at) FOR THIS parcel_id -- latest-row only
```

For the metric to legitimately move, three things have to be true after
Phase 7+8:

1. `parcel_scores` must contain rows where `actionability = 'PASS'`
   (currently every Phase 5 row has `actionability = 'PENDING'`).
2. The latest-per-parcel row must have that PASS status (so
   `run_scoring_cycle` must re-score parcels whose latest row is PENDING,
   appending a new row with the Phase 7+8 verdict).
3. `composite_score >= 70` must be achievable from the new sub-scores —
   this puts a hard constraint on the S4/S5/S6/S8 mappings: if all five
   real sub-scores top out at ~5/10 the composite plateau is well below
   70 and the metric stays at 0 even with a working actionability
   screen. Agent 2 must verify that the composite arithmetic in the
   happy path (good submarket, low-vacancy, positive absorption, low
   pipeline, below-median basis, OZ tract) yields >= 70.

This third point is the most consequential: **the metric integrity check
is not "the test returns 1," it's "the test returns 1 because real
signal pushed composite over the threshold, not because the test
fixture happened to plant the right numbers."** Agent 2 writes one
end-to-end test (TestPhase8MetricEndToEnd) that walks the full path —
parcel + market_context + sales_comps inserted via fake conn → score
parcel → actionability screen → strategy fit → persist → call
`prepare.calculate_actionable_pipeline_count` — and asserts the count is
exactly 1. The composite arithmetic in that test is recomputed from
program.md weights to double-check.

---

## 3. Risk Catalog

Risks are numbered R-501 .. R-545 (continuing from R-401..R-499 used in
Phase 6.1). Severity: CRITICAL / HIGH / MEDIUM / LOW.

### 3.1 Five-File Contract integrity (R-501 .. R-505)

**R-501 (CRITICAL) — Accidental DDL drift via _SQL_INSERT_PARCEL_SCORE
extension.** The current INSERT writes 6 columns; Phase 8 needs to write
4 more (`actionability_blockers`, `strategy_fit`, `primary_strategy`,
plus `confidence_score` is already there but recompute it). The columns
already exist in the table DDL — the change is ONLY the INSERT
statement. Mitigation: Agent 2 adds a unit test that asserts the new
INSERT references each of the 10 column names exactly once and that
prepare.py's DDL contains all 10 columns (read prepare.py source,
parse the CREATE TABLE block for parcel_scores, assert column names are
a superset of the INSERT columns). This catches "I added a column to
the INSERT that's not in the DDL" and "I forgot to add a column to the
INSERT."

**R-502 (HIGH) — Temptation to tighten the metric SQL.** Phase 8's
"viable strategy with next step" gate could be enforced in two places:
(a) here in research.py (set actionability='FAIL:strategy' when no
strategy is STRONG/MODERATE), or (b) in prepare.py (add `AND
strategy_fit IS NOT NULL` to the metric WHERE clause). Option (b) is a
prepare.py mutation and is FORBIDDEN. Mitigation: Agent 2 enforces all
four gates exclusively in research.py via the `actionability` text
value; prepare.py's WHERE clause stays untouched.

**R-503 (LOW) — Temptation to bump composite_threshold.** parameters.json
sets composite_threshold=70 and Agent 2 may discover that with the
limited sub-score coverage (S2 real, S9 stub, S10 OZ-only, plus the new
S4/S5/S6/S8) the typical parcel composite lands in the 50-65 range and
the threshold blocks legitimate parcels. THIS IS NOT A VALID REASON to
modify parameters.json. parameters.json mutations are between-runs and
out of scope. Mitigation: if Agent 2 notes a coverage-driven plateau,
they document it in 02_code_writer_response.md as a note for the human
reviewer. The S1/S3/S7/S11/S12 are still null at this phase and pull the
composite down via the weighted-sum-over-non-null-weight denominator —
that's expected behavior. Agent 3 verifies that Agent 2 did not reduce
the threshold or otherwise tune parameters.

**R-504 (LOW) — Temptation to add a new column to parcel_scores DDL.**
Some implementers will want to track "number of strategies fit" or
"actionability_evaluated_at" or similar. Forbidden. The DDL is locked.
Mitigation: Agent 2 confines new state to existing columns
(`actionability_blockers` JSONB can hold arbitrary metadata).

**R-505 (LOW) — Temptation to import psycopg2 instead of psycopg.**
Phase 5 already pinned psycopg3. Agent 2 must use the same driver.
Mitigation: no new top-level import statements beyond what research.py
already has (psycopg is imported transitively via `prepare`).

### 3.2 Metric integrity (R-506 .. R-510)

**R-506 (CRITICAL) — Default-PASS actionability on insufficient data.**
The four gates in program.md (path-to-control informational, plausible
entitlement, viable strategy, no deal-killers) are mostly affirmative
checks. With Phase 7+8's data coverage, gate 1 is always informational
PASS, gates 2 and 4 default-PASS unless we have affirmative blocking
evidence (which we mostly don't), and gate 3 PASSes iff strategy fit
produces at least one STRONG or MODERATE rating. So every parcel with
strategy_fit STRONG/MODERATE will pass actionability. This means the
metric reduces to "parcels with composite >= 70 AND at least one
strategy STRONG/MODERATE." If strategy fit is too lenient (every parcel
gets STRONG on at least one strategy), the metric is meaningless.
Mitigation: Agent 2 implements strategy fit faithfully to program.md's
table criteria (which DO have meaningful gates — e.g., BTS STRONG
requires by-right zoning AND tenant signal, MODERATE requires entitlement
clear within 6 months, etc.) and writes a test that constructs a
mediocre parcel (composite ~55, no obvious strategy fit) and asserts its
actionability is FAIL:strategy.

**R-507 (HIGH) — Latest-row staleness across re-runs.** The metric query
filters to MAX(scored_at) per parcel. If `run_scoring_cycle` only scores
parcels with NO existing parcel_scores row (current Phase 5 behavior at
research.py:1923 via `_SQL_LIST_UNSCORED_PARCELS`), Phase 7+8 wiring
won't re-score the existing PENDING-status parcels and the metric stays
at 0. Mitigation: extend `_SQL_LIST_UNSCORED_PARCELS` (or rename it)
to include parcels whose LATEST row has actionability='PENDING'. The
SQL change is local to research.py — no DDL impact. Agent 2 writes
TestPhase8RescoringPendingParcels.

**R-508 (HIGH) — Composite plateau.** Even if Phase 7+8 is correct, the
composite for a typical parcel may not clear 70 because S1/S3/S7/S11/S12
are still null. Walk the math (program.md weights):

- S1 weight 15, S2 weight 10, S3 weight 10, S4 weight 10, S5 weight 10,
  S6 weight 8, S7 weight 8, S8 weight 7, S9 weight 7, S10 weight 5,
  S11 weight 5, S12 weight 5. Total weight 100.
- Phase 5 populated S2, S9 (=5 fixed), S10 ∈ {0, 4} → max sub-weight 25.
- Phase 7+8 adds S4, S5, S6, S8 → adds 35 to populated weight = 60.
- _compute_composite divides weighted sum by populated weight, multiplies
  by 10. So if populated sub-scores average 7, composite = 70. To clear
  70, the parcel needs an average sub-score >= 7 across S2/S4/S5/S6/S8
  with S9=5 and S10∈{0,4} dragging the average down.
- A parcel in a tight submarket (S4=10, S5=10, S6=10), good geometry
  (S2=10), below-median basis (S8=10), OZ tract (S10=4), S9=5, S2=10:
  weighted = 10*10 + 10*10 + 10*10 + 8*10 + 7*10 + 7*5 + 5*4 = 505,
  populated weight = 57, composite = 505/57 * 10 ≈ 88.6. Clears 70.
- A typical parcel (S4=6, S5=7, S6=7, S2=7, S8=7, S9=5, S10=0):
  weighted = 10*6 + 10*7 + 8*7 + 10*7 + 7*7 + 7*5 + 5*0 = 320,
  populated weight = 57, composite = 320/57 * 10 ≈ 56.1. Below 70 — does
  not enter the pipeline. This is correct behavior: until S1/S3/S7/S11
  are wired, only strong-on-everything-we-measure parcels qualify.

Mitigation: Agent 2 documents the math in `02_code_writer_response.md`
and writes a happy-path end-to-end test where the parcel clears 70 and
a near-miss test where the parcel lands at ~56 and is excluded. No
parameters.json changes.

**R-509 (MEDIUM) — Confidence-weighted pipeline secondary metric.**
`prepare.calculate_confidence_weighted_pipeline` SUMS confidence_score
across the actionable pipeline. With more sub-scores populated,
confidence per parcel rises (Phase 5 was 3/12=0.25; Phase 7+8 reaches
7/12≈0.58 at most). This is automatic — no special handling. Mitigation:
none needed beyond a sanity check in TestPhase8MetricEndToEnd that the
weighted pipeline equals the sum of confidence_score over passing rows.

**R-510 (MEDIUM) — Backwards compatibility with Phase 5 PENDING rows.**
Existing parcel_scores rows from Phase 5 have actionability='PENDING'.
Those rows correctly do NOT enter the metric (R-506's gate 1 is
"actionability='PASS'"). When Phase 7+8 re-scores those parcels, the
new rows are APPENDED (not UPDATEd) per AUTORESEARCH_MECHANICS-style
versioned-append, and prepare.py's MAX(scored_at) selects the new row.
Mitigation: TestPhase8AppendOnly verifies two consecutive scoring runs
produce two parcel_scores rows for the same parcel.

### 3.3 S4 — submarket_vacancy (R-511 .. R-515)

**R-511 (HIGH) — Submarket id matching.** `parcels.submarket` is a TEXT
column populated during Phase 3 discovery — for Fulton, currently NULL
or set to a corridor name like "south_fulton_campbellton". `market_context.submarket_id`
is a TEXT FK to `submarkets.submarket_id`. There is NO guarantee these
two columns share the same vocabulary. Mitigation: Agent 2 implements
S4 with a graceful NULL return when the join produces no row. Agent 2
writes TestPhase7S4SubmarketJoinMiss that constructs a parcel with
submarket="nonexistent" and expects S4=None plus a data_gap flag.
Long-term: Phase 11+ will reconcile submarket vocabulary.

**R-512 (HIGH) — Latest-as-of-date selection.** market_context can have
multiple rows per submarket (one per as_of_date per source). S4 must
pull the LATEST row. Mitigation: SQL constant
`_SQL_LATEST_MARKET_CONTEXT` uses ORDER BY as_of_date DESC LIMIT 1, with
explicit submarket_id parameter. Test: TestPhase7S4LatestRowOnly.

**R-513 (MEDIUM) — Source priority.** program.md lines 615-650 say CoStar
is the primary source for vacancy/absorption/pipeline. Brokerage reports
(Cushman, CBRE, JLL, Colliers) are secondary. market_context has a
`source` column. If both sources are present for the same submarket and
date, prefer 'costar'. Mitigation: SQL adds
`ORDER BY (source = 'costar') DESC, as_of_date DESC` for deterministic
selection. Test: TestPhase7S4PrefersCostar.

**R-514 (MEDIUM) — Staleness threshold.** program.md L743 mandates
market_context refresh every 30 days. If the latest row is >30 days old
at scoring time, the score is degraded. Mitigation: emit a data_gap
flag noting staleness but still return the score (don't return None —
that hurts confidence and the agent has no better data). Test:
TestPhase7S4StalenessFlag.

**R-515 (LOW) — Vacancy-band boundaries.** program.md gives:
10 = <3%; 8 = 3–5%; 6 = 5–7%; 3 = 7–10%; 0 = >10%. Boundary handling
matters. At exactly 3.0%, is it 10 or 8? The "<3%" notation says
strictly less than, so 3.0% → 8. Mitigation: Agent 2 codes strict
inequalities. TestPhase7S4Boundaries covers 2.99, 3.0, 5.0, 7.0, 10.0.

### 3.4 S5 — submarket_absorption (R-516 .. R-518)

**R-516 (MEDIUM) — Negative absorption.** program.md says "0 = negative".
Need to handle the boundary at 0 (where does ±500K start). Mitigation:
program.md L191 explicitly says "10 = strong positive (>2M SF);
7 = positive (500K–2M SF); 4 = flat (±500K SF); 0 = negative". Map:
- absorption > 2_000_000 → 10
- 500_000 <= absorption <= 2_000_000 → 7
- -500_000 <= absorption < 500_000 → 4
- absorption < -500_000 → 0

Test TestPhase7S5Boundaries covers 2_000_001, 2_000_000, 500_000,
499_999, 0, -500_000, -500_001.

**R-517 (LOW) — Null absorption.** market_context.net_absorption_t12_sf
can be NULL (CoStar export omitted the field). Return None (data_gap),
do not coerce to 0. Test: TestPhase7S5NullAbsorption.

**R-518 (LOW) — Same join as S4.** S4 and S5 read the same row of
market_context. Agent 2 reads it once per scoring run (cache or
combined fetch) rather than two SELECTs. Mitigation: a single
`_compute_market_context_scores(conn, submarket)` helper returns a
dict {S4, S5, S6, plus staleness flag}.

### 3.5 S6 — competing_pipeline (R-519 .. R-522)

**R-519 (CRITICAL) — Radius vs. submarket fidelity gap.** program.md
L191 says "no spec construction within 5 mi" — that's a radius
predicate. Our data is submarket-aggregated (`under_construction_sf`
per submarket per as_of_date). The submarket grain is coarser than
5-mile radius for tight infill submarkets, finer than 5-mile radius for
wide rural submarkets. This is a fidelity mismatch we cannot resolve
without a radius-search facility we don't have. Mitigation: at-source-
of-truth approximation: compute S6 from the submarket's
`under_construction_sf` and emit a flag of `flag_type='data_gap'`
noting "S6 approximated at submarket grain; program.md spec is 5-mi
radius". Score using the program.md cuts (10=0; 7<500K; 4=500K-1.5M;
0>1.5M). This approximation is acceptable for Phase 7+8 because (a) the
agent's discovery loop cannot do better with current data, and (b) the
flag preserves the option to refine in Phase 11+. Test:
TestPhase7S6SubmarketApproximationFlag.

**R-520 (MEDIUM) — under_construction_sf vs proposed_sf.** S6 measures
"competing pipeline." market_context has both `under_construction_sf`
and `proposed_sf`. program.md is ambiguous. Read narrowly, "competing
pipeline" is build-imminent, so under_construction_sf only. Read
broadly, all spec including proposed. Decision: under_construction_sf
ONLY (build-imminent risk to lease-up); proposed_sf is too speculative
to dock the score. Document in code comment AND
02_code_writer_response.md. Test: TestPhase7S6OnlyUnderConstruction.

**R-521 (LOW) — Null pipeline.** If under_construction_sf is NULL,
treat as 0 (no competing supply on file). This is more lenient than
"return None" because absence of evidence ≈ absence in a
well-curated CoStar export. Document. Test:
TestPhase7S6NullPipelineGivesPerfect.

**R-522 (MEDIUM) — Boundary handling.** "10 = no spec; 7 = <500K SF;
4 = 500K–1.5M; 0 = >1.5M". At exactly 500K → 4. At exactly 1.5M → 4.
At >1.5M → 0. At 0 → 10. Test: TestPhase7S6Boundaries.

### 3.6 S8 — refined land_basis (R-523 .. R-528)

**R-523 (HIGH) — Sales comp filter.** sales_comps holds both land and
building rows (comp_type column). S8 needs `comp_type='land'` only. The
SQL constant must explicitly filter this. Mitigation: dedicated
`_SQL_SUBMARKET_LAND_MEDIAN` with `WHERE submarket_id = %s AND
comp_type = 'land'`. Test: TestPhase7S8LandOnlyFilter that plants a
mixed land+building dataset and asserts the median is computed from land
rows only.

**R-524 (HIGH) — Sample size minimum.** A submarket with 1 land comp
shouldn't drive a credible median. program.md doesn't specify a
minimum but production-grade comps analysis usually requires n >= 3.
Mitigation: minimum 3 land comps in the submarket within the last
36 months. If fewer, return None (data_gap flag) rather than a
1-comp "median". Test: TestPhase7S8MinSampleSize.

**R-525 (HIGH) — Lookback window.** Stale comps degrade the median.
36 months is a defensible default for industrial land in tight
markets. Mitigation: SQL adds `AND sale_date >= CURRENT_DATE - INTERVAL
'36 months'`. Test: TestPhase7S8LookbackWindow with a 5-year-old comp
that's filtered out.

**R-526 (HIGH) — Parcel basis proxy.** S8 maps the parcel's basis vs.
the submarket median. The parcel may have:
(a) a recent sale (parcels.last_sale_price + last_sale_date) — use it
    if last_sale_date is within 24 months,
(b) no recent sale → fall back to assessed_value_total / acreage,
(c) neither (FMV null AND assessed null) → return None.

Mitigation: explicit fallback ladder with each step logged. Test:
TestPhase7S8BasisProxyRecentSale, TestPhase7S8BasisProxyAssessedFallback,
TestPhase7S8BasisProxyNoneAvailable.

**R-527 (MEDIUM) — GA assessed-value convention.** GA assesses at 40% of
FMV. So `assessed_value_total / acreage` materially understates the
market-rate basis (gives ~$X*0.4/acre when sale comps are at $X/acre).
Compare apples-to-apples by inflating assessed by 1/0.4 = 2.5x when the
state is GA. Mitigation: when state='GA' AND using the assessed
fallback, multiply assessed_value_total by 2.5 before dividing by
acreage. Test: TestPhase7S8GaAssessedInflation. NOTE: this is a
GA-specific rule. Phase 14+ multi-state expansion will need a state-by-
state assessment-ratio table. For Phase 7+8, GA-only is fine and
documented.

**R-528 (MEDIUM) — Boundary mapping.** program.md L193:
"10 = below submarket median; 7 = at median; 4 = 10–25% above median;
0 = >25% above median." The "at median" band needs definition. Use
±5% as the "at median" band (anything 95-105% of median = score 7).
Anything <95% of median = score 10. 105-110% = score 4 (treating as
"a bit above"; matches program.md "10-25% above" upper tier when read
literally). Decision: define the bands explicitly:
- basis < 0.95 * median → 10
- 0.95 * median <= basis <= 1.10 * median → 7  (1.10 mapped from "at most 10% above the broad band")
- 1.10 * median < basis <= 1.25 * median → 4
- basis > 1.25 * median → 0

Document the choice in code comment AND `02_code_writer_response.md`.
Test: TestPhase7S8Boundaries.

### 3.7 Actionability gates (R-529 .. R-534)

**R-529 (CRITICAL) — Gate ordering.** The four gates are evaluated in
program.md order: (1) path to control, (2) plausible entitlement,
(3) viable strategy, (4) no deal-killers. Gate 3 depends on strategy
fit having been computed first. Mitigation: `score_parcel` orchestrator
ordering:
  a. Compute sub-scores (S1..S12).
  b. Compute composite + confidence.
  c. Compute strategy fit (consumes sub-scores + parcel attributes
     + market_context).
  d. Run actionability screen (consumes strategy fit results from c).
  e. Set primary_strategy from strategy fit.
  f. Persist single parcel_scores row with all of the above.

Test: TestPhase8ScoreParcelOrdering verifies strategy fit is computed
before actionability_screen.

**R-530 (HIGH) — Path-to-control gate.** program.md is explicit:
"informational, not a gate." Always PASS. Owner contact info goes into
the snapshot (Phase 9), not the actionability decision. The only
ownership-related FAIL is "government entity with no disposition program
or active conservation easement" — which is already covered by hard
filter H10 (rejected at discovery, never reaches scoring). Mitigation:
gate 1 implementation is `return ("PASS", None)` with a comment
linking to program.md L72-L77. Test: TestPhase8GateControlAlwaysPass.

**R-531 (CRITICAL) — Plausible entitlement gate.** program.md L82-L86
lists multiple PASS conditions: (a) by-right industrial, (b) approved
ag-to-industrial rezoning within 2 mi in past 5 years, (c) comp plan
designates for industrial, (d) adjacent industrial, (e) reasonable
agent-articulated theory. FAIL only when "affirmative evidence" of a
block. We have NONE of (a)..(d) data wired in Phase 7+8 (S9 is the
moderate stub at value 5 from research.py:1568). Decision: gate 2
default-PASSes with a `notes` annotation "entitlement signal pending
Phase 11+". This is faithful to program.md ("FAIL only when affirmative
evidence" — we have no evidence either way, so default-PASS is
correct). Future Phase 11+ adds real signals and can flip to FAIL.
Document the decision in 02_code_writer_response.md. Test:
TestPhase8GateEntitlementDefaultPass plus
TestPhase8GateEntitlementWithBlocker (a synthetic flagged_items row of
flag_type='actionability_block' with description containing
'entitlement' triggers FAIL).

**R-532 (CRITICAL) — Viable strategy gate.** PASS iff at least one of
the five strategies (BTS, spec, land_bank, ground_lease, flip) is
rated STRONG or MODERATE per the Strategy Fit Assessment Engine.
Otherwise FAIL:strategy. Mitigation: deterministic implementation.
Test: TestPhase8GateStrategyPass (strategy_fit has BTS=STRONG → PASS),
TestPhase8GateStrategyFail (all five WEAK or N/A → FAIL:strategy).

**R-533 (HIGH) — No deal-killers gate.** program.md L92-L96 lists
title encumbrance, hostile easement access, active legal dispute, and
visible site issues. We have none of these data sources wired. Default-
PASS with a `notes` annotation. Optional check: if `flagged_items`
contains an open `flag_type='actionability_block'` row for this
parcel (Phase 11+ may populate these from manual review), gate 4
FAILs. Mitigation: query flagged_items for the parcel; if any open
actionability_block row exists, FAIL:deal_killer. Test:
TestPhase8GateDealKillerSyntheticBlocker.

**R-534 (MEDIUM) — Combined actionability output.** The output of
`run_actionability_screen` is `{actionability: 'PASS' | 'FAIL:control'
| 'FAIL:entitlement' | 'FAIL:strategy' | 'FAIL:deal_killer',
blockers: {gate_id: blocker_text}}`. With the gates in order, the
FIRST failing gate wins (program.md doesn't specify but matches the
results.tsv enum which is single-valued). Mitigation: short-circuit at
first FAIL. Document. Test: TestPhase8ActionabilityFirstFailWins (a
parcel that would fail gates 2 AND 3 reports FAIL:entitlement, not
FAIL:strategy).

### 3.8 Strategy fit decision logic (R-535 .. R-540)

**R-535 (CRITICAL) — BTS Fit.** program.md L334-L341. STRONG conditions
require ALL of: (a) by-right industrial (S9 >= 7) — we have S9=5 stub,
so STRONG is unreachable in Phase 7+8 unless we relax, (b) utilities at
boundary (H8 was a PASS-WITH-FLAG stub at Phase 4, no real data), (c)
identifiable tenant demand signal — no data source wired, (d) parcel >=
150K SF buildable. Decision: with current data (S9 stub-moderate, no
tenant signal source), STRONG is structurally unreachable in Phase 7+8.
MODERATE requires "entitlement path is clear but not by-right (S9 >= 4)
+ utilities available within extension distance + general submarket
demand strong (S4 >= 6, S5 >= 7) but no specific tenant signal." With
S9=5 (>= 4) and good market context (S4>=6, S5>=7) → MODERATE.
Otherwise WEAK. N/A if parcel acreage < 5 (covers the "<5 acres"
program.md condition; H2 already filters at acquisition). The "150K SF
footprint" condition: 150K SF / 0.40 coverage = 375K SF land = ~8.6
acres. So acreage >= 8.6 is the BTS prerequisite at MODERATE. Decision
rules:

  - acreage < 8.6 → "N/A"
  - S9 < 4 → "WEAK"
  - S9 >= 4 AND S4 >= 6 AND S5 >= 7 → "MODERATE"
  - else → "WEAK"

STRONG is unreachable in Phase 7+8 (documented). Test:
TestPhase8StrategyFitBts covering each branch.

**R-536 (CRITICAL) — Spec Dev Fit.** program.md L348-L355. STRONG: S4
>= 8 AND S5 >= 7 AND S6 >= 7 AND S9 >= 7. With S9=5 stub → STRONG
unreachable. MODERATE: S4 >= 6 AND positive absorption AND
"entitlement path clear within 6 months" (proxy: S9 >= 4 — we have 5,
clears it). WEAK: S4 < 6 OR S5 negative OR S6 < 4. N/A: S4 < ~3 (very
high vacancy). Decision rules:

  - S4 < 3 → "N/A"  (vacancy >= 7%, market clearly oversupplied)
  - S4 >= 8 AND S5 >= 7 AND S6 >= 7 AND S9 >= 7 → "STRONG"  (unreachable in P7+8)
  - S4 >= 6 AND S5 >= 7 AND S9 >= 4 → "MODERATE"
  - S4 < 6 OR S5 < 4 OR S6 < 4 → "WEAK"
  - else → "WEAK"

Test: TestPhase8StrategyFitSpecDev.

**R-537 (CRITICAL) — Land Bank Fit.** program.md L362-L367. STRONG: on
emerging corridor + basis <= 50% of mature submarket pricing + entitlement
risk acceptable on 3-5 year horizon + carry cost manageable. We don't
have "emerging corridor" as a feature. Use S8 as the proxy for basis
(S8=10 means below median = STRONG basis advantage). MODERATE: corridor
plausible + basis below mature pricing but discount <50%. Decision:

  - S8 = 10 (basis well below median) → "STRONG" land bank candidate
  - S8 = 7 (at median) → "MODERATE"
  - S8 = 4 (10-25% above) → "WEAK"
  - S8 = 0 (>25% above) → "N/A"
  - S8 None → "N/A"

This is a simplified mapping — Phase 11+ adds corridor-emerging
detection. Document. Test: TestPhase8StrategyFitLandBank.

**R-538 (HIGH) — Ground Lease Fit.** program.md L375-L379. STRONG: S1
>= 8 AND S4 >= 8 AND by-right (S9 >= 7) AND large enough for
institutional. With S9=5 stub, STRONG unreachable. MODERATE: good
location + acquirable basis supports 5-7% ground rent yields + developer
demand exists. Without rent-yield data, fall back to: S1 >= 6 AND S4
>= 6 → MODERATE. Otherwise WEAK. N/A: rural emerging corridor (S8
strongly below median + S4 weak). Decision:

  - S1 < 4 OR acreage < 8.6 → "N/A"  (institutional minimum scale)
  - S1 >= 8 AND S4 >= 8 AND S9 >= 7 → "STRONG"  (unreachable in P7+8)
  - S1 >= 6 AND S4 >= 6 → "MODERATE"
  - else → "WEAK"

Test: TestPhase8StrategyFitGroundLease.

**R-539 (HIGH) — Land Flip Fit.** program.md L386-L391. STRONG:
off-market acquisition at >= 25% below comps + active developer demand
+ clean title + closes within 6-12 months. We don't have an
"off-market discount" signal except as derivable from S8. MODERATE:
10-25% discount + demand exists + entitlement work needed. Decision
mapping S8 → flip fit:

  - S8 = 10 (basis well below median) AND S4 >= 6 (active market) → "STRONG"
  - S8 = 10 AND S4 < 6 → "MODERATE" (discount but soft demand)
  - S8 = 7 → "MODERATE"
  - S8 = 4 → "WEAK"
  - S8 = 0 → "N/A"
  - S8 None → "N/A"

Test: TestPhase8StrategyFitFlip.

**R-540 (MEDIUM) — Multi-Parcel Assemblage.** program.md L394-L402.
EXPLICITLY OUT OF SCOPE for Phase 7+8 — it's a cross-parcel analysis
that requires querying ADJACENT parcels' ownership and computing
combined acreage. Defer to Phase 11+. Decision: do not implement. The
strategy_fit JSONB stores 5 keys (bts, spec, land_bank, ground_lease,
flip) — assemblage is added in Phase 11+. Document. Agent 3 verifies
no assemblage code in research.py.

### 3.9 Persistence and ordering (R-541 .. R-543)

**R-541 (HIGH) — Atomic transaction.** Phase 5 already wraps the
parcel_scores INSERT + research_log INSERT + flagged_items INSERTs in
`conn.transaction()` (research.py:1825). Phase 7+8 must preserve this
wrapping. Adding 4 more parameters to the parcel_scores INSERT does not
break the transaction. Mitigation: don't change the transaction
boundary. Test: TestPhase8AtomicPersistence (force a flag-insert raise
mid-transaction; assert no parcel_scores row was committed).

**R-542 (HIGH) — primary_strategy selection.** primary_strategy is a
TEXT column. From the 5-strategy strategy_fit JSONB, choose the
"primary" strategy as: first STRONG, else first MODERATE, else NULL
(if all WEAK/N/A, primary_strategy is NULL even though one of the gates
might have passed via... wait, no — if all are WEAK/N/A then gate 3
FAILs and the parcel doesn't reach the metric anyway). Order of
priority among STRONGs (when there are multiple): BTS > spec >
land_bank > flip > ground_lease (Agent 2's call; document). Test:
TestPhase8PrimaryStrategySelection covering tied STRONGs and
all-MODERATE cases.

**R-543 (LOW) — Notes field.** parcel_scores.notes is freeform TEXT.
Phase 5 wrote a short summary. Phase 7+8 should append the actionability
verdict and the primary strategy: notes = "phase78: composite=78.3
strategy=BTS-MODERATE actionability=PASS sub-scores=..." or similar.
Bounded length (~500 chars). Test: TestPhase8NotesContent.

### 3.10 Test architecture (R-544 .. R-545)

**R-544 (CRITICAL) — Fake conn fixture for Phase 7+8.** The Phase 5
`Phase5FakeConnection` (test_discovery.py:1113) and `_SharedQueueCursor`
(test_discovery.py:1083) handle sequenced fetchone/fetchall queues
across multiple cursors. Phase 7+8 reuses this — no new fixture
infrastructure needed. The fake_conn for Phase 8 end-to-end tests will
need to handle: parcel fetch → S2 geometry fetch → market_context
fetch → sales_comps fetch → parcel_scores INSERT → research_log INSERT
→ N flagged_items INSERTs. That's a 4-fetchone-queue + 1-fetchall-queue
setup. Mitigation: existing fixture is sufficient. Test author
documents the queue order in test docstrings.

**R-545 (HIGH) — Test count and AST scanner test.** Phase 5 added 40
new tests (104 total). Phase 7+8 should add ~50: ~5 per sub-score
(S4/S5/S6/S8 = 20), ~5 per actionability gate (4 gates = 20), ~5 per
strategy (5 strategies = 25 — but a lot of overlap), end-to-end metric
test, atomic persistence test, ordering test, etc. Plus the existing
TestPhase5SqlConstantsStaticChecks at test_discovery.py:1568 needs a
sister TestPhase78SqlConstantsStaticChecks that asserts:
- new SQL constants are parameterised (no f-strings),
- the extended _SQL_INSERT_PARCEL_SCORE references all 10 expected
  column names,
- the actionability text is one of the 5 enum values,
- (still) no string interpolation in cursor.execute().

Mitigation: Agent 2 writes ~50 new tests. Agent 3 verifies pre-existing
104 still pass.

---

## 4. Architectural decisions Agent 2 must record

Agent 2's `02_code_writer_response.md` MUST address each of these
decisions explicitly:

D1. **Re-scoring policy.** Re-score parcels whose latest row is PENDING
    (modify _SQL_LIST_UNSCORED_PARCELS), append a new row, never UPDATE
    in place. (R-507, R-510.)

D2. **Default-PASS for entitlement and deal-killer gates.** Without
    affirmative-block evidence, gates 2 and 4 default-PASS with a notes
    annotation. (R-531, R-533.)

D3. **STRONG strategy fit unreachable in Phase 7+8.** S9 is a fixed
    moderate stub (=5), so the BTS/Spec/Ground-Lease STRONG branches
    that require S9>=7 are structurally unreachable. MODERATE is the
    expressive ceiling for those three. Document. (R-535, R-536, R-538.)

D4. **S6 submarket-grain approximation.** program.md says "5-mi radius";
    we use submarket aggregation and emit a flag. (R-519.)

D5. **S8 GA assessed-value 2.5x inflation when using assessed fallback.**
    GA-specific. (R-527.)

D6. **S8 sample size minimum n=3, lookback 36 months.** (R-524, R-525.)

D7. **First-failing-gate-wins for actionability.** (R-534.)

D8. **primary_strategy priority order: BTS > spec > land_bank > flip >
    ground_lease.** (R-542.)

D9. **Multi-parcel assemblage explicitly deferred to Phase 11+.**
    (R-540.)

D10. **No parameters.json / prepare.py / sources.json edits.** (R-501..
     R-505.)

---

## 5. Go/No-Go Gates for Agent 3

Gate 1. **Five-File Contract intact.** `git diff d5f4722 -- prepare.py
parameters.json sources.json program.md connector_harness.py
connector_registry.json requirements.txt` is empty.

Gate 2. **All 4 sub-scores wired.** S4, S5, S6, S8 helpers implemented
with the boundary mappings in §3.3-§3.6. Each helper is a pure function
plus a SQL fetch.

Gate 3. **All 4 actionability gates implemented.** Path-to-control
informational PASS, entitlement default-PASS with optional FAIL via
flagged_items, strategy gate consumes strategy_fit, deal-killer
default-PASS with optional FAIL via flagged_items.

Gate 4. **All 5 strategy fit functions implemented.** BTS, spec,
land_bank, ground_lease, flip — each producing one of {STRONG,
MODERATE, WEAK, N/A} per the rules in §3.8. Multi-parcel assemblage
NOT implemented.

Gate 5. **Persistence extended.** _SQL_INSERT_PARCEL_SCORE writes
actionability, actionability_blockers, strategy_fit, primary_strategy
in addition to the Phase 5 columns. Atomic transaction preserved.

Gate 6. **Re-scoring of PENDING parcels.** _SQL_LIST_UNSCORED_PARCELS
(or its successor) returns parcels with no row OR latest row PENDING.

Gate 7. **Test count.** Pre-existing 104 passing + ~50 new Phase 7+8
tests passing → 154+ total. Includes TestPhase8MetricEndToEnd that
constructs a fake conn with one passing parcel and asserts
`prepare.calculate_actionable_pipeline_count(fake_conn)` returns 1.

Gate 8. **No new external HTTP calls.** Phase 7+8 reads only from
Postgres tables already populated by Phases 1-6. No requests/urllib
calls beyond what research.py already imports.

Gate 9. **AST/static checks pass.** TestStaticChecks (no_immutable_writes,
no_string_interpolated_sql, sources_dir_in_gitignore,
no_print_in_run_discovery_cycle) all green. New
TestPhase78SqlConstantsStaticChecks green.

Gate 10. **All 10 D* decisions in §4 documented in
02_code_writer_response.md** with code references.

Gate 11. **Composite plateau math verified.** A near-miss test (typical
parcel composite ~56) excluded from the metric, and a happy-path test
(strong-on-everything composite ~88) included with actionability=PASS.

Gate 12. **No assemblage, snapshot, or memo code introduced.** Those
remain NotImplementedError stubs at research.py:3350-3361. Agent 3
greps research.py for "assemblage" and confirms it appears only in
comments referring to Phase 11+.

---

## 6. Closing notes

The combined Phase 7+8 push is bigger than a single phase but each
piece is local and contained. The metric finally moves not because we
softened any gate but because we wired four real sub-scores and one
real screening gate (strategy fit consumed by viable-strategy
actionability). The composite plateau math (R-508) is the most likely
practical limit on the metric — until S1/S3/S7/S11 are populated in
later phases, only strong-on-everything-we-measure parcels will clear
70.

The single biggest architectural risk is R-506: default-PASS gates
trivializing the metric. The mitigation is faithful implementation of
program.md's strategy fit criteria — the meaningful gating is in
strategy fit's STRONG/MODERATE thresholds, which require real signal
from S4/S5/S8/S9 before they pass. As long as Agent 2 doesn't relax
those criteria, the metric movement will reflect real signal.
