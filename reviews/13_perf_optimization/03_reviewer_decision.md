# Phase 13 Reviewer Decision — Performance / Robustness Pass

**Reviewer:** Agent 3 (Reviewer and Implementer), three-agent code team
(appendix_a_county_connectors.md L37-L49, L67). Agent 3 is the ONLY agent with
commit authority.
**Date:** 2026-06-10.
**Branch:** `claude/ecstatic-davinci-yrclnr` (verified `git branch
--show-current`; NOT an `autoresearch/*` branch, no `experiment_log.tsv` —
confirmed BETWEEN RUNS, normal three-agent development, not a live Karpathy
experiment).
**Reviewing:** `reviews/13_perf_optimization/01_risk_review.md` (34 concerns
R-1301..R-1335, 28 gates, 5 open questions, verdict GO-WITH-GATES),
`reviews/13_perf_optimization/02_code_writer_response.md` (per-concern record,
open-question decisions, gate self-assessment, two-commit partition), and the
actual working-tree diff across `research.py`, `prepare.py`,
`tests/test_discovery.py`, `tests/test_prepare.py`, `tests/test_postgis_smoke.py`.

House style follows `reviews/10_phase7_8_combined/03_reviewer_decision.md`.

---

## 1. Verdict at the top

**APPROVE AND COMMIT.** All 28 go/no-go gates from `01_risk_review.md` §5 pass on
independent verification. All 34 R-13xx concerns are addressed in code or
accepted with explicit, correct rationale. The full suite is green —
independently reproduced **`Ran 576 tests in 1.244s` / `OK`** after
`pip install -r requirements.txt` (matching Agent 2's reported 576/OK). Items
1-4 are genuinely pure perf/robustness with a byte-for-byte `cache=None`
fallback proven bit-identical by a real cache-vs-no-cache equivalence suite.
Item 7 is a correctly-scoped formal `prepare.py` mutation that lands in its own
`prepare-mutation:` commit with the fresh-baseline implication documented. No
opportunistic refactoring beyond the justified `_LATEST_SCORE_CTE` extraction.
The five-file contract (parameters.json, sources.json, program.md, prepare.py
outside item 7's own commit) is intact.

I made **no** final-pass code edits — the implementation needs none. One
non-blocking cleanliness observation is recorded in §5 for a future pass.

**Ruling on open question #2 (the one I was explicitly required to decide):**
**ACCEPT Agent 2's choice — `flag_id DESC` on the BATCH actionability query,
single-key query unchanged.** Full rationale in §4.

---

## 2. Independent verification I ran

```
pip install -r requirements.txt              # R-1330 — deps first
python -m unittest discover tests            # → Ran 576 tests in 1.244s / OK
python -m py_compile research.py prepare.py tests/test_discovery.py \
        tests/test_prepare.py tests/test_postgis_smoke.py   # all compile
python -m unittest <all 7 Phase-13 test classes> -v          # → Ran 44 tests / OK
git branch --show-current                    # claude/ecstatic-davinci-yrclnr
git status --short parameters.json sources.json program.md \
        connector_registry.json connector_harness.py requirements.txt  # empty
```

The 44-test verbose run confirms the Phase 13 classes actually EXECUTE (not
skipped) and the retry log lines prove the live backoff schedule (1.0 → 2.0),
Retry-After honor (5.0), cap (10.0), and fallback (1.0) all fire as designed.

I did NOT take Agent 2's word for any gate; every item below was checked against
the working-tree diff hunk-by-hunk and against the running test output.

---

## 3. Per-gate verification (Agent 1 §5, all 28)

### Item 1 — retry-with-backoff in `_DiscoverySession.get()`

- **Gate 1 (R-1301) — MET.** `_DISCOVERY_MAX_RETRIES = 2`,
  `_DISCOVERY_BACKOFF_SCHEDULE_S = (1.0, 2.0)`, `_DISCOVERY_RETRY_AFTER_CAP_S =
  10.0` are module-level (research.py ~L160). The divergence from the harness
  `MAX_RETRIES=3`/`(1,2,4)` is documented in a comment citing the 90-min OS kill
  and the 30-min soft ceiling. `test_retry_cap_is_two` pins the constants;
  `test_backoff_schedule_used_in_order` asserts the recorded sleeps are exactly
  `[1.0, 2.0]`.
- **Gate 2 (R-1302) — MET.** On exhaustion the HTTP path calls
  `resp.raise_for_status()` (re-raising the SAME `HTTPError`) and the transport
  path `raise`s the original `ConnectionError`/`Timeout` — never a sentinel. A
  defensive `raise RuntimeError(...)` guards the unreachable fall-through.
  `test_retries_exhausted_reraises_http_error` /
  `..._reraises_timeout` assert 3 calls (cap+1) then propagation. The existing
  corridor handler therefore still fires unchanged.
- **Gate 3 (R-1303) — MET.** `_spacing_sleep(host)` is the FIRST statement
  inside the `for attempt` loop (runs on every attempt, pre-request); the
  backoff `time.sleep(sleep_s)` is at the BOTTOM after a failed attempt —
  ordering mirrors `connector_harness._http_get`.
  `test_spacing_invoked_on_every_attempt` spies `_spacing_sleep` and asserts 3
  calls across 2 failures + 1 success.
- **Gate 4 (R-1304) — MET.** 429 is in the RETRY branch (`status == 429 or 500
  <= status < 600`), never the fail-fast path. `_retry_after_delay` parses
  integer-seconds `Retry-After`, honors it when longer than the scheduled
  backoff, caps at 10s, and falls back to the schedule for garbage/HTTP-date/
  missing/zero values. Matched pair `test_retry_on_429_then_200` +
  `test_no_retry_on_404`, plus the four Retry-After variants, all pass.
- **Gate 5 (R-1305) — MET.** Status branching is on `resp.status_code`: 200-399
  → `resp.json()`; 429/5xx → retry; other 4xx → `raise_for_status()` fail-fast.
  Only `ConnectionError`/`Timeout` are caught for transport retry; other
  `RequestException` subclasses propagate immediately (documented).
  `test_no_retry_on_400/403/404`, `test_retry_on_500_then_200`,
  `..._connection_error_...`, `..._timeout_...`.
- **Gate 6 (R-1306) — MET.** I grepped research.py: the only
  `from connector_harness` is NONE, and the only `connector_harness.X()` call is
  the pre-existing public `run_harness_for_county("fulton")` at L1608 (the
  documented setup-phase gate, present in HEAD, untouched). The retry lives
  inline as a loop in `get()` + the `_retry_after_delay` method; no new module.
  The top-level `import connector_harness` (L107) is PRE-EXISTING (verified in
  `git show HEAD:research.py`) and is NOT the private-helper import R-1306
  forbids. `test_no_call_to_connector_harness_http_helper` is a proper AST scan
  (not a substring scan that would false-positive on the explanatory comments)
  enforcing exactly this.
- **Gate 7 (R-1308) — MET.** Retries log via `log.info` (host + reason only,
  never the URL query string — Phase 11/Regrid key safety), ≤ 2 lines/request.
  `test_no_print_in_get_retry_path` AST-scans `get` for `print()`.

### Items 2-4 — per-cycle prefetch (cache-as-optional-arg)

- **Gate 8 (R-1310) — MET.** Three new module-level `_SQL_*_BATCH` constants,
  `%s`-only:
  - `_SQL_LATEST_MARKET_CONTEXT_BATCH` is `DISTINCT ON (submarket_id)` with the
    EXACT tail `ORDER BY submarket_id, (CASE WHEN source = 'costar' THEN 0 ELSE
    1 END), as_of_date DESC` — NOT GROUP BY/MAX.
    `test_market_context_batch_preserves_costar_case_tail` asserts the exact
    substring AND `assertNotIn("MAX(")`/`assertNotIn("GROUP BY")`.
  - `_SQL_SUBMARKET_LAND_MEDIAN_BATCH` reproduces the filters byte-identically
    (`comp_type = 'land'`, `price_per_acre IS NOT NULL`, the 36-month window) +
    `PERCENTILE_CONT(0.5)` + `GROUP BY submarket_id` (correct — this is an
    aggregate, not a latest-row pick).
  - `_SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH` is `DISTINCT ON (parcel_id) ...
    ORDER BY parcel_id, flagged_at DESC, flag_id DESC` (see §4).
- **Gate 9 (R-1310, R-1319) — MET.** `TestPhase13CacheEquivalence` is the real
  acceptance gate and it is MEANINGFUL: `_score_no_cache()` drives the per-parcel
  `fetchone` path through the actual single-key SQL constants; `_score_with_cache()`
  feeds a `_CycleCache` with the IDENTICAL row tuples. It asserts identical
  result dict, identical `INSERT INTO parcel_scores` params, identical
  `research_log` and `flagged_items` INSERT params. Because each cache value is
  the SAME row shape the single-key query returns, the decode logic is shared —
  the test WOULD fail if the decode diverged. `test_research_log_and_flag_params_identical`
  correctly compares only INSERT params (the no-cache path's extra SELECT is an
  expected recorded-query difference, not a row-content difference).
- **Gate 10 (R-1312) — MET.** `cache` is a keyword-only (`*`, `= None`) param on
  `_compute_market_context_scores`, `_compute_s8`, `_fetch_actionability_block`,
  and `score_parcel`. The `None` branch is the original code verbatim (I diffed
  each: the `with conn.cursor()` block is byte-for-byte the prior body). All
  pre-existing Phase 7/8 end-to-end `score_parcel` tests pass UNMODIFIED — the
  only test_discovery.py edit to an existing test is the
  `test_iterates_unscored_parcels` fixture, whose DB-call sequence legitimately
  changed (prefetch added two `fetchall`s, removed one per-parcel block
  `fetchone`); its behavior assertions (2 scored, 2 inserts) are unchanged.
- **Gate 11 (R-1320) — MET.** `_fetch_actionability_block(conn, parcel_id)`
  signature is unchanged for positional callers; the public
  `run_actionability_screen` (L4094) still calls it with no cache and runs the
  single-key SELECT. `test_run_actionability_screen_second_caller_unchanged`
  proves the per-parcel path runs and the deal-killer verdict is correct.
- **Gate 12 (R-1315) — MET.** The `if not submarket` / `if submarket` guard
  fires BEFORE any `cache.get(...)` in both helpers, so NULL/empty submarkets
  hit the same empty branch (no KeyError, no spurious match). Keys are RAW
  strings — verified no `.lower()`/`.strip()`. The prefetch short-circuits the
  market_context and land-median queries when the distinct-submarket list is
  empty. `test_null_submarket_hits_empty_branch_no_keyerror`,
  `test_submarket_keys_case_sensitive_no_normalization` (proves
  `"south fulton"` MISSES `"South Fulton"`), `test_no_submarkets_skips_submarket_queries`.
  I independently confirmed cache-miss parity: `cache.X.get(k) -> None` flows
  through the identical `if not row:` / `if row:` branches as `fetchone() ->
  None`.
- **Gate 13 (R-1316) — MET.** Every batch query is called with `(list,)` — a
  one-tuple wrapping the Python list.
  `test_each_batch_const_has_exactly_one_any_placeholder` (one `%s`, contains
  `ANY(%s)`) and `test_any_params_passed_as_single_tuple_wrapping_list` (asserts
  recorded `len(params)==1` and `isinstance(params[0], list)`) lock the shape
  the fake cursor cannot otherwise catch. Live adaptation is exercised by the
  postgis smoke CI.
- **Gate 14 (R-1317) — MET.** `_prefetch_cycle_cache(conn, market, parcel_ids)`
  is called in `run_scoring_cycle` AFTER the collision guard and AFTER
  `parcel_ids` is read, BEFORE the loop. Distinct submarkets come from
  `_SQL_DISTINCT_SUBMARKETS_FOR_PARCELS` keyed on the SAME `parcel_ids`; the
  actionability batch gets `parcel_ids` straight.
  `test_cache_keyed_on_exact_parcel_ids` asserts the exact list, in order.
- **Gate 15 (R-1319) — MET.** The market_context cache carries the full 7-col
  tail, so `staleness_days`/`provenance`/`as_of_date`/`source` reconstruct
  identically and the staleness/S6 flags + notes fire identically. S8's basis
  stays PER-PARCEL; only `(n, median)` is cached. Proven by
  `test_research_log_and_flag_params_identical`.
- **Gate 16 (R-1313) — MET.** I verified the batch query filters
  `flag_type = 'actionability_block' AND status = 'open'` and contains no
  `data_gap`. `_CycleCache`'s docstring documents that scoring writes only
  `data_gap` flags and never `market_context`/`sales_comps`/`actionability_block`
  rows, so a cycle-start prefetch is safe against the cycle's own writes.
  `test_prefetch_actionability_ignores_data_gap_flags`.

### Item 7 — prepare.py metric-query refactor (FORMAL MUTATION EVENT)

- **Gate 17 (R-1321, R-1324) — MET.** `_LATEST_SCORE_CTE` is `WITH latest AS
  (SELECT DISTINCT ON (parcel_id) ... ORDER BY parcel_id, scored_at DESC,
  score_id DESC)`. The ORDER BY is EXACTLY parcel_id-led with the two-term
  tie-break. `test_latest_score_cte_uses_distinct_on_with_exact_order_by` asserts
  the exact substring AND `assertNotIn("MAX(scored_at)")`.
- **Gate 18 (R-1322, R-1327) — MET.** ONE shared `_LATEST_SCORE_CTE` projecting
  `parcel_id, composite_score, confidence_score, actionability` (all four
  columns the COUNT filter and SUM projection need) + ONE shared
  `_LATEST_SCORE_FILTER` (`actionability = 'PASS' AND composite_score >= %s`),
  applied OUTSIDE the CTE. Both metric functions compose them. The threshold is
  the single bound param; no inline threshold. `test_latest_score_cte_projects_all_needed_columns`,
  `test_filter_carries_pass_and_single_threshold_placeholder` (`flt.count("%s")
  == 1`).
- **Gate 19 (R-1322) — MET, and NOT weakened.** I checked all three rewrites
  hunk-by-hunk:
  - `test_where_clause_carries_actionability_and_threshold_predicates`: the old
    `assertIn("MAX(scored_at)")` became `assertIn("DISTINCT ON (parcel_id)")` +
    `assertIn("ORDER BY parcel_id, scored_at DESC, score_id DESC")` +
    `assertNotIn("MAX(scored_at)")`. This is STRICTER than before (it now pins
    the full tie-break ordering, not just the existence of a latest-row mechanism).
  - `test_uses_same_where_clause_as_count` → `test_uses_same_latest_score_cte_and_filter`:
    the structurally-broken split-on-first-`WHERE` (which a CTE invalidates, as
    R-1322 foresaw) is replaced by asserting BOTH emitted SQLs embed BOTH shared
    constants verbatim + `FROM latest`. This genuinely fails if either function's
    selection CTE or filter drifts. Intent (lock-step parcel set) preserved.
  - `test_threshold_is_bound_parameter`: keeps `params == (threshold,)`; updates
    the projection substring `SUM(ps.confidence_score)` → `SUM(confidence_score)`
    (correct — the `ps.` alias is gone now the SUM reads from the CTE). Equal
    strictness.
- **Gate 20 (R-1325) — MET.** `idx_scores_parcel_scored_at ON
  parcel_scores(parcel_id, scored_at DESC, score_id DESC)` is appended to
  `_DDL_INDEXES` as a PLAIN `CREATE INDEX IF NOT EXISTS` with a comment
  explaining the single-transaction/ACCESS-EXCLUSIVE-lock reasoning and why
  CONCURRENTLY is forbidden in `apply_schema`. `TestPhase13IndexDDL`:
  `test_index_present_in_all_ddl`, `test_index_is_not_concurrent` (scans ALL
  DDL), `test_index_is_idempotent`.
- **Gate 21 (R-1321) — MET.** The stale "deliberately deferred" comment at
  prepare.py L552-557 is rewritten to document the implemented `DISTINCT ON` CTE,
  the tie-break/double-count-elimination, and the non-comparable-across-commit /
  fresh-baseline implication. The "deferred" language is gone.
- **Gate 22 (R-1321, R-1333) — MET via Commit B below.** Item 7 lands in its own
  `prepare-mutation:` commit whose body states the old correlated-subquery shape,
  the new CTE shape, the tie-count semantic change, and the fresh-baseline
  requirement.
- **Gate 23 (R-1323) — MET.** `tests/test_postgis_smoke.py` Step 5 inserts two
  PASS rows at the IDENTICAL `scored_at` + one earlier PASS row for one parcel,
  then asserts `calculate_actionable_pipeline_count == 1` AND that
  `calculate_confidence_weighted_pipeline` equals the highest-`score_id` tied
  row's confidence (proving DISTINCT ON picked exactly that row, no double
  count). This is the live-Postgres backstop the fake cursor cannot provide; it
  runs in `validate-phase1.yml` on any push touching prepare.py.

### Cross-cutting

- **Gate 24 / 25 (R-1333) — MET.** The working tree is partitioned at the FILE
  level: research.py + test_discovery.py (perf) vs prepare.py + test_prepare.py
  + test_postgis_smoke.py (mutation). I confirmed `git status --short` shows
  parameters.json, sources.json, program.md, connector_registry.json,
  connector_harness.py, requirements.txt are ALL unmodified. The two-commit
  split below honors the boundary.
- **Gate 26 (R-1334) — MET.** `requirements.txt` unchanged; no new third-party
  import (Retry-After uses stdlib `int(...)`; batch SQL uses psycopg's existing
  list→array adaptation).
- **Gate 27 (R-1330) — MET.** 576 pass WITH `pip install -r requirements.txt`
  run first (without it, prepare.py's top-level `from dotenv import load_dotenv`
  yields the false dotenv errors Agent 1 flagged — I installed deps first and
  saw zero such errors).
- **Gate 28 — MET.** The pre-existing AST scanners (`test_no_string_interpolated_sql`,
  `test_no_immutable_writes`, `test_no_print_in_run_discovery_cycle`) are
  UNTOUCHED by the diff and remain green: `test_no_string_interpolated_sql`
  requires every `.execute()` first arg to be Constant/Name/Attribute, which the
  new `cur.execute(_SQL_..._BATCH, (list,))` calls satisfy (Name), and which
  would catch any f-string SQL. New batch constants carry no `{` brace
  (`test_constants_present_and_no_format_braces`,
  `test_no_runtime_format_braces_in_metric_constants`).

---

## 4. Ruling on open question #2 (R-1311) — the call I was required to make

**Question:** the batch actionability query adds `flag_id DESC` as a
deterministic tie-break the single-row path lacks. Is this an acceptable
unobservable divergence, or must the single-row path gain the same ORDER BY?

**RULING: ACCEPT Agent 2's choice as-is — `flag_id DESC` on the BATCH query,
single-key `_SQL_FLAGGED_ACTIONABILITY_BLOCK` unchanged.** I do NOT require any
change, and I made none.

**Justification:**

1. **The divergence precondition is not merely rare — it is currently
   impossible to generate.** It requires one parcel to have TWO open
   `actionability_block` rows at the IDENTICAL-microsecond `flagged_at` whose
   descriptions DISAGREE on deal-killer status. Per decision #3 (which I
   independently verified: the batch SQL filters `flag_type =
   'actionability_block'`, and `score_parcel` emits only `flag_type='data_gap'`
   flags), the scoring path never writes `actionability_block` rows at all.
   Those rows come only from Phase 11+ manual review at one-block-per-parcel
   cadence. No committed code path produces the tie.

2. **Even given the tie, the observable surface is null.** The deal-killer gate
   `_gate_deal_killer` is an existence test over the single returned
   description ("does ANY open block mention a non-`entitlement` keyword"). The
   batch returns exactly ONE description per parcel, identical in cardinality to
   the single-key `LIMIT 1`. The only difference would be WHICH of two
   simultaneously-flagged descriptions is returned — and only if they disagreed.

3. **The divergence direction is toward MORE determinism.** The batch is
   reproducible; the single-key query is already arbitrary-on-ties (PostgreSQL
   returns an unspecified row for equal `flagged_at`). Adding `flag_id DESC`
   mirrors item 7's `score_id DESC` philosophy and is the better engineering
   choice. The "bit-identical" bar in this pass governs the scoring cycle's
   metric and DB-row OUTPUTS, which are provably unchanged.

4. **It is honest and auditable.** The micro-divergence is documented in the
   SQL-constant comment and asserted by
   `test_actionability_batch_distinct_on_with_flag_id_tiebreak`, which also
   locks that the single-key query keeps NO `flag_id`
   (`assertNotIn("flag_id", _SQL_FLAGGED_ACTIONABILITY_BLOCK)`).

I explicitly considered the strict-parity alternative (drop `flag_id DESC` from
the batch). I reject it: it would make the batch arbitrary-on-ties to mirror an
arbitrariness that cannot manifest — strictly worse engineering for zero
observable benefit. The cache-path NULL-description handling also matches the
single-key path exactly (`str(row[1])` stored only when non-NULL; absent key
reads back None == the single-key `str(row[0]) if row[0] is not None else
None`), which I verified directly.

**Disposition of the other four open questions:** #1 (429 Retry-After
honor-with-cap) — concur, matches Agent 1's preference and is correctly capped/
tested. #3 (keep item 4) — concur, the no-write-of-actionability_block proof is
sound. #4 (research.py read-path selector alignment OUT OF SCOPE) — concur,
that is a research.py read, not the immutable metric, and bundling it would
break minimal-diff discipline; the snapshot-read comment at L4535 documents the
follow-up. #5 (index is a perf hint, not a correctness gate) — concur.

---

## 5. What I checked that BOTH prior agents could have missed

Per my unique failure mode (Agent 1 missed a risk and Agent 2 implemented
around the wrong concern), I looked specifically for gaps neither covered:

- **Cache-miss vs `fetchone()`-None parity** (not spelled out as its own
  concern): a parcel WITH a submarket that has NO market_context / sales_comps
  row. Verified both paths converge — `cache.get() -> None` and `fetchone() ->
  None` flow through the identical `if not row:` / `if row:` branches. No
  divergence. ✓
- **NULL `description` in an actionability_block row:** verified the cache path
  (`if row[1] is not None: store str(...)`, absent → None) is byte-equivalent to
  the single-key return (`str(row[0]) if row[0] is not None else None`). ✓
- **`_SQL_DISTINCT_SUBMARKETS_FOR_PARCELS` has no market filter** — neither does
  the single-key `_SQL_LATEST_MARKET_CONTEXT` (it keys purely on `submarket_id`).
  So no behavioral divergence is introduced. ✓
- **NON-BLOCKING observation (no action required this pass):** the `market`
  parameter of `_prefetch_cycle_cache(conn, market, parcel_ids)` is unused in the
  body (the cycle's `parcel_ids` already encode the market constraint, and the
  market_context lookup is submarket-keyed without a market filter). This is
  harmless and removing it would be gold-plating against the minimal-diff
  discipline. Recorded for a future tidy-up if desired; it does NOT affect
  correctness, the metric, or any gate.

No missed risk rises to a revision trigger. Agent 1's review was thorough and
Agent 2 addressed the substance of every concern in code rather than paying lip
service — the equivalence suite, the exact-substring SQL guards, and the live
tie-break test are real, not decorative.

---

## 6. Commits made

Three commits on `claude/ecstatic-davinci-yrclnr` (the verified current branch;
never any other):

- **Commit A** `perf: cycle-level batch prefetch + discovery retry-with-backoff`
  — research.py, tests/test_discovery.py.
- **Commit B** `prepare-mutation: latest-score selection via DISTINCT ON +
  tie-break index` — prepare.py, tests/test_prepare.py, tests/test_postgis_smoke.py.
- **Commit C** `docs: Agent 2 response + Agent 3 decision for perf pass` —
  reviews/13_perf_optimization/02_code_writer_response.md + this file.

**Run-history implication (per AUTORESEARCH_MECHANICS.md "When Mutating
prepare.py"):** Commit B changes the metric DEFINITION. The next run MUST start
a NEW branch with a fresh baseline; metric values across this commit are NOT
comparable. The human merging Commit B to `main` is the mutation commit that
triggers the protocol.

---

## 7. Approval rationale

The single biggest residual risk Agent 1 named — a silent SQL-ordering mistake
invisible to the fake-cursor offline tests (a dropped CoStar-preference CASE in
the item-2 batch, or a missing parcel_id-led ORDER BY / dropped `score_id DESC`
in the item-7 CTE) — is mitigated by three independent backstops that all exist
and all pass: (a) exact-substring offline assertions on both the item-2 CoStar
tail and the item-7 ORDER BY; (b) the cache-vs-no-cache bit-identical
equivalence suite for items 2-4; (c) the live-Postgres Step-5 tie-break proof
for item 7. Items 1-4 are pure perf with a verbatim `cache=None` fallback. Item
7 is a correctly-isolated mutation with the fresh-baseline implication
documented in code, in the commit body, and here. Every gate is genuinely met on
independent verification, not by deference. APPROVED.
