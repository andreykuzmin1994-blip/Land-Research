# Phase 13 Code Writer Response — Per-Risk Mitigations and Decisions

**Writer:** Agent 2 (Code Writer), three-agent code team
(appendix_a_county_connectors.md L26-L36).
**Date:** 2026-06-10.
**Branch:** working tree (BETWEEN RUNS — no `experiment_log.tsv`; normal
three-agent development, not a live Karpathy experiment).
**Base:** the working tree Agent 1 reviewed (532 offline tests passing with
`psycopg[binary]`, `python-dotenv`, `requests` installed).
**Reviewing:** `reviews/13_perf_optimization/01_risk_review.md` (34 concerns
R-1301..R-1335, 28 gates, 5 open questions) and producing the code that
satisfies it.

I do NOT commit (Agent 3 has sole commit authority). All changes are left in
the working tree. House style follows `reviews/10_phase7_8_combined/02_*`.

---

## 1. Summary of changes

| File | +lines | −lines | Belongs to commit |
|------|-------:|-------:|-------------------|
| `research.py` | 351 | 17 | **`perf:`** (items 1-4) |
| `tests/test_discovery.py` | 612 | 5 | **`perf:`** (items 1-4) |
| `prepare.py` | 59 | 18 | **`prepare-mutation:`** (item 7) |
| `tests/test_prepare.py` | 100 | 11 | **`prepare-mutation:`** (item 7) |
| `tests/test_postgis_smoke.py` | 70 | 0 | **`prepare-mutation:`** (item 7 live test) |

**Tests: 532 pre-existing all still pass; 44 new (37 items 1-4 + 7 item 7);
576 total, 576 passing.** Exact final line:
`Ran 576 tests in 1.268s` / `OK`.

The partition is clean at the file level (verified — see §5): research.py and
test_discovery.py contain ONLY perf changes (zero `_LATEST_SCORE_CTE` /
`prepare-mutation` / metric-on-`parcel_scores` content); prepare.py,
test_prepare.py, test_postgis_smoke.py contain ONLY item-7 changes (zero
`_CycleCache` / retry / batch-SQL content).

---

## 2. Open questions (Agent 1 → Agent 3/human): my decisions

1. **429 `Retry-After` policy (R-1304):** **DECIDED — honor-with-cap.**
   `_DiscoverySession._retry_after_delay` parses the integer-seconds
   `Retry-After`; when it exceeds the scheduled backoff we sleep it, capped at
   `_DISCOVERY_RETRY_AFTER_CAP_S = 10.0`; a value above the cap does NOT extend
   the wait (we cap and let retry-exhaustion hand off to the corridor handler).
   HTTP-date / garbage / missing header → scheduled backoff. Pure stdlib
   `int(...)`, no new dep. Tests: `test_429_retry_after_honored_with_cap`,
   `..._capped`, `..._shorter_than_backoff_uses_backoff`,
   `..._garbage_retry_after_uses_backoff`.
2. **Item-4 tie-break (R-1311):** **DECIDED — add `flag_id DESC` to the BATCH
   query, leave the single-key query unchanged.** This makes the batch path
   strictly MORE deterministic than the (unchanged) per-parcel path on the
   vanishingly rare two-open-blocks-identical-`flagged_at` case. The divergence
   is unobservable in practice (the deal-killer gate only asks whether ANY open
   block mentions a non-`entitlement` keyword). Documented in the SQL-constant
   comment and asserted by `test_actionability_batch_distinct_on_with_flag_id_tiebreak`.
   **Agent 3 ruling requested:** confirm "bit-identical" tolerates this
   unobservable tie-break; if strict parity is required, drop `flag_id DESC`
   from the batch query (one-line change) — but then the batch is
   non-deterministic on the tie exactly like the per-parcel query.
3. **Item-4 inclusion (R-1313):** **DECIDED — KEEP item 4.** I proved cheaply
   that scoring never writes `actionability_block` rows: `score_parcel` emits
   only `flag_type='data_gap'` flags (research.py `_flag(...)` call sites all
   pass `"data_gap"`), and the batch query filters
   `flag_type='actionability_block' AND status='open'`. So a cycle-start
   prefetch is safe against the cycle's own writes. A partial win was NOT
   necessary. Test: `test_prefetch_actionability_ignores_data_gap_flags`.
4. **research.py latest-row selector alignment (R-1329):** **DECIDED — OUT OF
   SCOPE, not bundled.** `_SQL_LIST_PARCELS_FOR_SCORING` and
   `_SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT` keep `ORDER BY scored_at DESC LIMIT
   1` (no `score_id` tie-break). Aligning them is a separate future
   between-runs change for the human to schedule (minimal-diff discipline).
   Recorded as a known follow-up here.
5. **Index as optional-for-correctness (R-1326):** **ACKNOWLEDGED.** The new
   `idx_scores_parcel_scored_at` is a pure perf hint; the metric is correct
   without it. Treated as deferrable-without-affecting-metric-values. Added
   anyway (cheap, helps); a problem with the index does not block the metric
   refactor.

---

## 3. Per-concern record (R-1301 .. R-1335)

### Item 1 — retry-with-backoff in `_DiscoverySession.get()`

- **R-1301 (CRITICAL) — retry budget vs 90-min ceiling — ADDRESSED.**
  Module constants `_DISCOVERY_MAX_RETRIES = 2`, `_DISCOVERY_BACKOFF_SCHEDULE_S
  = (1.0, 2.0)` (research.py ~L160), INTENTIONALLY divergent from the harness
  `MAX_RETRIES=3`/`(1,2,4)`, with the divergence documented in a comment.
  Worst-case per fully-failed request ≈ spacing + (1) + spacing + (2) ≈ 5s
  beyond the first attempt. `test_retry_cap_is_two`,
  `test_backoff_schedule_used_in_order` (asserts sleeps `[1.0, 2.0]`).
- **R-1302 (HIGH) — give-up contract re-raises — ADDRESSED.** On exhaustion the
  loop calls `resp.raise_for_status()` (re-raising the SAME `HTTPError`) for
  HTTP, or `raise` for the transport exception — never a sentinel. The existing
  corridor-level handler fires unchanged.
  `test_retries_exhausted_reraises_http_error`,
  `test_retries_exhausted_reraises_timeout`. The pre-existing
  `TestPhase31...` corridor-abort tests still pass.
- **R-1303 (HIGH) — `_spacing_sleep` on every attempt — ADDRESSED.** Ordering
  is `_spacing_sleep(host)` at the TOP of each attempt → request → on failure
  `time.sleep(backoff)` at the BOTTOM, mirroring `connector_harness._http_get`
  L338-364. `test_spacing_invoked_on_every_attempt` (spies `_spacing_sleep`,
  asserts 3 calls across 2 failures + 1 success). No stale future reservation
  is introduced.
- **R-1304 (HIGH) — 429 Retry-After — ADDRESSED (honor-with-cap).** See open
  question #1 above. `_retry_after_delay` + `_DISCOVERY_RETRY_AFTER_CAP_S`.
  429 is in the RETRY branch, never conflated with the fail-fast 4xx path:
  `test_retry_on_429_then_200` + `test_no_retry_on_404` are the matched pair.
- **R-1305 (MEDIUM) — exact retryable set — ADDRESSED.** Status branching is on
  `resp.status_code`: `200-399` → return `resp.json()`; `429 or 500-599` →
  retry; other `400-499` → `raise_for_status()` (fail-fast). Transport: only
  `ConnectionError`/`Timeout` are caught for retry; other `RequestException`
  subclasses (`InvalidURL`, `TooManyRedirects`) propagate immediately (more
  correct + faster), documented in a comment. 200-with-error-envelope is NOT
  retried (status is 200 → returns) and error-envelope handling stays out of
  scope. `test_retry_on_500_then_200`, `test_no_retry_on_400/403`,
  `test_retry_on_connection_error_then_200`, `test_retry_on_timeout_then_200`.
- **R-1306 (MEDIUM) — no import / no new module — ADDRESSED.** Retry lives as a
  loop inside `_DiscoverySession.get()` + the `_retry_after_delay` method; no
  new module; no `from connector_harness import`; no call to
  `connector_harness._http_get`/`_rate_limit`. Comment cites the constraint and
  Phase 3 R-17. `test_no_call_to_connector_harness_http_helper` (AST scan, not
  a substring scan — the explanatory comments legitimately mention the harness;
  the test asserts the only `connector_harness.*()` call is
  `run_harness_for_county`).
- **R-1307 (LOW) — idempotency of retried GET — ACCEPTED-as-safe.** The session
  issues ONLY GETs (no POST/write path), so retrying is side-effect-free.
  Noted in the `get()` docstring.
- **R-1308 (LOW) — logging strips creds, no spam — ADDRESSED.** Retries log via
  `log.info` at one line per retry (≤ 2 per request), logging only `host` +
  status `reason`, NEVER the URL query string (Phase 11/Regrid key safety). No
  `print`. `test_no_print_in_get_retry_path` (AST scan of `get`).

### Items 2-4 — batch the per-cycle lookups (cache-as-optional-arg)

- **R-1310 (CRITICAL) — source-preference / latest-row SQL byte-preserved —
  ADDRESSED.** Three new module-level `_SQL_*_BATCH` constants, `%s`-only:
  - `_SQL_LATEST_MARKET_CONTEXT_BATCH` — `DISTINCT ON (submarket_id)` with
    `ORDER BY submarket_id, (CASE WHEN source='costar' THEN 0 ELSE 1 END),
    as_of_date DESC` — the EXACT CoStar-preference tail. NOT GROUP BY/MAX.
  - `_SQL_SUBMARKET_LAND_MEDIAN_BATCH` — `GROUP BY submarket_id`, identical
    filters (`comp_type='land'`, `price_per_acre IS NOT NULL`, 36-month
    window), `PERCENTILE_CONT(0.5)`.
  - `_SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH` — `DISTINCT ON (parcel_id) ... 
    ORDER BY parcel_id, flagged_at DESC, flag_id DESC`.
  **Bit-identical design:** each cache value is the SAME row tuple the
  single-key query returns (market_context: 7-col tail; land_median: `(n,
  median)`; block: the decoded `str`), so `_compute_market_context_scores`,
  `_compute_s8`, `_fetch_actionability_block` run byte-identical decode logic
  whether the row came from `fetchone()` or the cache. Proven by the
  cache-vs-no-cache equivalence suite (`TestPhase13CacheEquivalence`):
  identical result dict, identical `parcel_scores` INSERT params, identical
  `research_log` + `flagged_items` INSERT params. SQL-shape guards in
  `TestPhase13BatchSqlConstants`.
- **R-1311 (HIGH) — actionability tie-break determinism — ADDRESSED (decision
  #2).** `flag_id DESC` on the batch query only; single-key query unchanged;
  documented micro-divergence. `test_actionability_batch_distinct_on_with_flag_id_tiebreak`,
  `test_actionability_block_absent_key_returns_none`.
- **R-1312 (CRITICAL) — optional-cache defaults None, None-path verbatim —
  ADDRESSED.** `cache` is a keyword-only param (`*`, `= None`) on
  `_compute_market_context_scores`, `_compute_s8`, `_fetch_actionability_block`,
  and `score_parcel`. The `None` branch is the original code verbatim. All ~8
  pre-existing Phase 7/8 end-to-end `score_parcel` tests pass UNMODIFIED.
  `TestPhase13CacheEquivalence` is the `TestScoreParcelNoCacheUnchanged`
  equivalent.
- **R-1313 (HIGH) — cache staleness within a cycle — ADDRESSED (decision #3).**
  `_CycleCache` docstring documents that scoring never writes
  `market_context`/`sales_comps` (only `run_ingestion_cycle` does) and writes
  only `data_gap` flags, never `actionability_block`.
  `test_prefetch_actionability_ignores_data_gap_flags`.
- **R-1314 (MEDIUM) — midnight-UTC straddle — ACCEPTED, no code change.** The
  batched land-median evaluates `CURRENT_DATE` once per cycle (a consistency
  improvement). The Python `date.today()` staleness/basis calls are NOT the
  batched SQL and are left exactly as-is. Documented in the
  `_SQL_SUBMARKET_LAND_MEDIAN_BATCH` comment. The fake-cursor offline tests
  never execute SQL, so this is moot offline and only a once-a-day flake risk
  in live CI (negligible at sub-second runtime).
- **R-1315 (HIGH) — dict-key normalization (NULL/empty/case) — ADDRESSED.** The
  `if not submarket` guard in both helpers fires BEFORE any `cache.get(...)`,
  so NULL/empty hits the same empty branch (no KeyError, no spurious match).
  Keys are RAW strings — no `.lower()`/`.strip()`. The prefetch short-circuits
  the submarket queries when the distinct-submarket list is empty.
  `test_null_submarket_hits_empty_branch_no_keyerror`,
  `test_submarket_keys_case_sensitive_no_normalization`,
  `test_no_submarkets_skips_submarket_queries`.
- **R-1316 (HIGH) — psycopg `ANY(%s)` adaptation — ADDRESSED.** Every batch
  query is called with `(list,)` (a one-tuple wrapping the Python list) — e.g.
  `cur.execute(_SQL_..._BATCH, (submarkets,))`. Empty lists short-circuit
  (R-1315/R-1318). `test_each_batch_const_has_exactly_one_any_placeholder`
  (one `%s`, contains `ANY(%s)`), `test_any_params_passed_as_single_tuple_wrapping_list`
  (asserts recorded `params == (list,)`). Live adaptation is exercised by the
  postgis smoke CI.
- **R-1317 (MEDIUM) — prefetch keyed off the cycle's actual parcel list —
  ADDRESSED.** `_prefetch_cycle_cache(conn, market, parcel_ids)` runs AFTER the
  collision guard and AFTER `parcel_ids` is read, BEFORE the loop. The distinct
  submarkets come from `_SQL_DISTINCT_SUBMARKETS_FOR_PARCELS` keyed on the SAME
  `parcel_ids`; the actionability batch gets `parcel_ids` straight.
  `test_cache_keyed_on_exact_parcel_ids`.
- **R-1318 (LOW) — degenerate 0/1 parcel — ADDRESSED.** `if not pid_list:
  return empty cache` (no queries). No N=1 special case.
  `test_empty_parcel_list_issues_no_queries`.
- **R-1319 (MEDIUM) — provenance/flag side-effects per-parcel & unchanged —
  ADDRESSED.** The market_context cache carries the full 7-col row, so
  `staleness_days`/`provenance`/`as_of_date`/`source` are reconstructed
  identically and the staleness + S6 flags + notes fire identically. S8's basis
  stays PER-PARCEL; only `(n, median)` is cached.
  `TestPhase13CacheEquivalence.test_research_log_and_flag_params_identical`.
- **R-1320 (HIGH) — second `_fetch_actionability_block` caller untouched —
  ADDRESSED.** The function signature is unchanged for positional callers; the
  public `run_actionability_screen` (L4094) still calls
  `_fetch_actionability_block(conn, parcel_id)` with no cache and runs the
  per-parcel query. `test_run_actionability_screen_second_caller_unchanged`
  (asserts the single-key SELECT ran and the verdict is correct).

### Item 7 — prepare.py metric-query refactor (FORMAL MUTATION EVENT)

- **R-1321 (CRITICAL) — tie-break is a real metric-DEFINITION change —
  ADDRESSED + RE-BASELINE FLAGGED.** Replaced the `MAX(scored_at)` correlated
  subquery with a `DISTINCT ON (parcel_id) ... ORDER BY parcel_id, scored_at
  DESC, score_id DESC` CTE. The stale "deliberately deferred" comment
  (prepare.py L552-557) is REWRITTEN to record the mutation, the
  double-count-elimination, and the non-comparable-across-commit /
  fresh-baseline implication. The `prepare-mutation:` commit body (Agent 3 to
  author at commit time — proposed text in §5) must repeat these three points.
  Offline assert on the exact ORDER BY substring:
  `TestPhase13MetricMutationShape.test_latest_score_cte_uses_distinct_on_with_exact_order_by`.
  Live tie-break proof: postgis smoke Step 5 (R-1323).
- **R-1322 (CRITICAL) — three test_prepare.py assertions rewritten without
  weakening — ADDRESSED.** Factored a shared `_LATEST_SCORE_CTE` and
  `_LATEST_SCORE_FILTER`; both metric functions compose them. The three tests:
  - `test_where_clause_carries_actionability_and_threshold_predicates`: the
    `MAX(scored_at)` assertion became `assertIn("DISTINCT ON (parcel_id)")` +
    `assertIn("ORDER BY parcel_id, scored_at DESC, score_id DESC")` +
    `assertNotIn("MAX(scored_at)")` — keeps the latest-per-parcel guard with
    equal strictness.
  - `test_uses_same_where_clause_as_count` → renamed
    `test_uses_same_latest_score_cte_and_filter`: both emitted SQLs must embed
    BOTH shared constants verbatim (fails if either function's selection or
    filter drifts). Replaces the structurally-invalid split-on-first-`WHERE`.
  - threshold-binding tests: `test_threshold_is_passed_as_bound_parameter`
    (count) is unchanged-and-passing; `test_threshold_is_bound_parameter`
    (confidence) keeps the `params == (threshold,)` assertion and updates the
    projection substring to `SUM(confidence_score)` (the `ps.` alias is gone
    now the SUM reads from the CTE).
- **R-1323 (HIGH) — fake cursor can't validate the CTE; live test added —
  ADDRESSED.** Added Step 5 to `tests/test_postgis_smoke.py`: inserts two PASS
  rows at the IDENTICAL `scored_at` plus one earlier PASS row for one parcel,
  then asserts `calculate_actionable_pipeline_count == 1` and that
  `calculate_confidence_weighted_pipeline` equals the highest-`score_id` tied
  row's confidence (proving DISTINCT ON picked exactly that row, no double
  count). Runs against live Postgres in `validate-phase1.yml` /
  `discovery-fulton.yml`.
- **R-1324 (HIGH) — DISTINCT ON requires parcel_id-led ORDER BY — ADDRESSED.**
  The ORDER BY is exactly `parcel_id, scored_at DESC, score_id DESC`; each
  term's purpose is documented in a comment (parcel_id: DISTINCT ON
  requirement; scored_at DESC: latest; score_id DESC: deterministic tie-break).
  Offline exact-substring assertion guards it.
- **R-1325 (HIGH) — plain CREATE INDEX, no CONCURRENTLY — ADDRESSED.** Appended
  `CREATE INDEX IF NOT EXISTS idx_scores_parcel_scored_at ON
  parcel_scores(parcel_id, scored_at DESC, score_id DESC);` to `_DDL_INDEXES`,
  with a comment explaining the single-transaction/lock reasoning and why
  CONCURRENTLY is forbidden here. `TestPhase13IndexDDL`:
  `test_index_present_in_all_ddl`, `test_index_is_not_concurrent`,
  `test_index_is_idempotent`.
- **R-1326 (MEDIUM) — index necessity/direction — ADDRESSED (decision #5).**
  Index direction (DESC) matches the query ordering so it can satisfy DISTINCT
  ON via an index scan; additive to the existing `idx_scores_parcel`. Treated
  as a perf hint, not a correctness gate.
- **R-1327 (MEDIUM) — CTE projects all needed columns — ADDRESSED.** The single
  shared `_LATEST_SCORE_CTE` projects `parcel_id, composite_score,
  confidence_score, actionability`, so both the COUNT filter and the SUM
  projection have their columns.
  `test_latest_score_cte_projects_all_needed_columns`.
- **R-1328 (LOW) — `score_id DESC` = latest-insert-wins — ACKNOWLEDGED.** That
  is the intended policy (a re-score supersedes). No change beyond item 7.
- **R-1329 (MEDIUM) — cross-file tie-break inconsistency — ACCEPTED-as-risk /
  OUT OF SCOPE (decision #4).** Documented as a known follow-up; the research.py
  read-path selectors are intentionally left non-deterministic-on-ties this
  pass.

### Cross-cutting

- **R-1330 (HIGH) — offline suite needs the three pip deps — ADDRESSED.** I ran
  `pip install -r requirements.txt` first; the true baseline is **532 passing
  WITH requirements installed** (without them, prepare.py's top-level
  `from dotenv import load_dotenv` yields the 21 false errors Agent 1 flagged).
  Final result is **576 passing** WITH deps.
- **R-1331 (MEDIUM) — S2 may dominate; perf win may under-deliver — NOTED.** S2
  (`_SQL_S2_GEOMETRY`, a PostGIS `ST_MakeValid`/`ST_CollectionExtract`/
  `ST_Envelope` op) and `_fetch_parcel_for_scoring` remain per-parcel; items
  2-4 remove 3 of 5 hops but the heaviest (S2) is one of the two remaining. Do
  NOT expect a 5x speedup from a 3/5 reduction. S2 batching is OUT OF SCOPE
  (scope creep; per-parcel by nature). Flagged for a future perf pass.
- **R-1332 (MEDIUM) — minimal-diff discipline — HONORED.** The only refactor is
  the JUSTIFIED `_LATEST_SCORE_CTE`/`_LATEST_SCORE_FILTER` extraction (R-1322,
  in service of the change). No drive-by renames; the `_SQL_LIST_UNSCORED_PARCELS`
  alias is untouched; S2/`_SQL_FETCH_PARCEL` not batched; research.py read-path
  selectors not aligned.
- **R-1333 (HIGH) — commit structure — HONORED (left for Agent 3).** Clean
  file-level partition (§5). Agent 3 makes TWO commits: (1) `perf:` =
  research.py + test_discovery.py; (2) `prepare-mutation:` = prepare.py +
  test_prepare.py + test_postgis_smoke.py.
- **R-1334 (MEDIUM) — no new dependency / no new module — HONORED.**
  `requirements.txt` untouched; no new top-level module; the only third-party
  imports remain `psycopg`/`dotenv`/`requests`. `Retry-After` uses stdlib
  `int(...)`; batch SQL uses psycopg's existing list→array adaptation.
- **R-1335 (LOW) — `validate-phase1.yml` re-runs prepare.py — NOTED.** Item 7
  touches prepare.py so this fires; it runs `apply_schema` (idempotent new
  index) + both metric functions + (via the postgis smoke) the Step-5 tie-break
  proof against live Postgres. This is where a CTE/index syntax error would be
  caught.

---

## 4. Go/No-Go gates (Agent 1 §5) — self-assessment

| # | Gate (abbrev) | Status |
|---|---------------|--------|
| 1 | Retries capped at 2; backoff `(1,2)`; module-level; divergence documented | **MET** |
| 2 | Exhaustion RE-RAISES same class (no sentinel); corridor-abort unchanged | **MET** |
| 3 | `_spacing_sleep` every attempt; order spacing→request→backoff | **MET** |
| 4 | 429 retried; 4xx≠429 fail-fast; matched pair; Retry-After honored-with-cap | **MET** |
| 5 | 5xx retried, other 4xx not; branch on `status_code` | **MET** |
| 6 | No `import connector_harness` retry helper; no new module | **MET** |
| 7 | No `print` in retry path; logged via `log`; no URL query string | **MET** |
| 8 | Batch SQL module-level `%s` `DISTINCT ON` (not GROUP BY) for mc/block; exact CoStar tail + land filters | **MET** |
| 9 | Bit-identical proof: cache vs no-cache identical parcel_scores/research_log/flagged_items | **MET** |
| 10 | Cache kw-only `=None`, None path verbatim; Phase 7/8 e2e tests UNMODIFIED | **MET** |
| 11 | Second `_fetch_actionability_block` caller works; both paths tested | **MET** |
| 12 | NULL/empty submarket → empty branch; raw keys; empty list short-circuits | **MET** |
| 13 | `ANY(%s)` passed as `(list,)`; shape test; live CI exercises adaptation | **MET** |
| 14 | Prefetch keyed off the loop's `parcel_ids`; after guard, before loop | **MET** |
| 15 | Cache carries provenance/staleness/median-count; flags/notes unchanged | **MET** |
| 16 | Actionability prefetch ignores `data_gap`; scoring writes documented | **MET** |
| 17 | Metric SQL `DISTINCT ON (parcel_id)` + exact parcel_id-led ORDER BY; offline assert | **MET** |
| 18 | One shared CTE projecting all 4 cols; single bound threshold; no inline | **MET** |
| 19 | Three test_prepare.py tests rewritten, intent preserved | **MET** |
| 20 | New index plain CREATE INDEX, no CONCURRENTLY; offline presence+absence test | **MET** |
| 21 | Stale comment (prepare.py L552-557) updated, "deferred" removed | **MET** |
| 22 | Item 7 is its OWN `prepare-mutation:` commit, body documents tie-break/double-count/fresh-baseline | **MET (commit pending Agent 3)** |
| 23 | Live-Postgres tie-break test (two same-`scored_at` PASS rows → count 1, highest score_id) | **MET** |
| 24 | `git diff <items-1-4> -- prepare.py parameters.json sources.json program.md ... requirements.txt` EMPTY | **MET** (file-level partition; see §5) |
| 25 | Item-7 commit touches ONLY prepare.py + test files | **MET** (file-level partition) |
| 26 | `git diff -- requirements.txt` EMPTY; no new third-party imports | **MET** |
| 27 | Full suite green WITH requirements; 532 + new; deps installed first | **MET** (576 pass) |
| 28 | AST scanners still green; new batch constants parameterised | **MET** |

All 28 gates MET. Gate 22/24/25 depend on Agent 3 performing the two-commit
split as specified in §5 (I cannot commit); the working tree is already
partitioned so the split is mechanical.

---

## 5. Commit partition (for Agent 3 — the only committer)

**Two commits, by file. No file is split across commits.**

### Commit 1 — `perf:` (items 1-4, research.py robustness/batching)
Files / hunks:
- `research.py`:
  - retry constants `_DISCOVERY_MAX_RETRIES` / `_DISCOVERY_BACKOFF_SCHEDULE_S`
    / `_DISCOVERY_RETRY_AFTER_CAP_S` (~L160).
  - `_DiscoverySession._retry_after_delay` + rewritten `get()` (~L299-L400).
  - batch SQL constants `_SQL_LATEST_MARKET_CONTEXT_BATCH`,
    `_SQL_SUBMARKET_LAND_MEDIAN_BATCH`,
    `_SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH`,
    `_SQL_DISTINCT_SUBMARKETS_FOR_PARCELS` (beside their single-key siblings).
  - `_CycleCache` dataclass + `_prefetch_cycle_cache` (before the conn-bound
    orchestrators).
  - `cache=None` kwarg threaded through `_compute_market_context_scores`,
    `_compute_s8`, `_fetch_actionability_block`, `score_parcel`; the three
    call-sites in `score_parcel`; the `_prefetch_cycle_cache(...)` call +
    `cache=cache` in `run_scoring_cycle`.
- `tests/test_discovery.py`: the appended Phase 13 section (`TestDiscoveryRetry`,
  `TestPhase13BatchSqlConstants`, `TestPhase13PrefetchCache`,
  `TestPhase13CacheEquivalence`, `TestPhase13CacheGuards`, fakes
  `_FakeResponse`/`_ScriptedSession`/`_patched_discovery_session`), plus the
  one updated fixture in `TestPhase5RunScoringCycle.test_iterates_unscored_parcels`
  (the prefetch changed the DB-call sequence — fixture updated, behavior
  assertions unchanged: still 2 scored, 2 inserts).

Must NOT touch: prepare.py, parameters.json, sources.json, program.md,
connector_registry.json, connector_harness.py, requirements.txt.

### Commit 2 — `prepare-mutation:` (item 7, metric DEFINITION change)
Files / hunks:
- `prepare.py`: rewritten metric comment block (L549-600 region);
  `_LATEST_SCORE_CTE` + `_LATEST_SCORE_FILTER` constants; rebuilt
  `calculate_actionable_pipeline_count` / `calculate_confidence_weighted_pipeline`;
  new `idx_scores_parcel_scored_at` in `_DDL_INDEXES`.
- `tests/test_prepare.py`: rewritten `test_where_clause_carries...`,
  `test_uses_same_latest_score_cte_and_filter`, `test_threshold_is_bound_parameter`;
  new `TestPhase13MetricMutationShape`, `TestPhase13IndexDDL`.
- `tests/test_postgis_smoke.py`: Step 5 live DISTINCT ON tie-break test.

**Proposed `prepare-mutation:` commit message:**
```
prepare-mutation: DISTINCT ON latest-score selection + idx_scores_parcel_scored_at

Replace the MAX(scored_at) correlated subquery in both metric functions
(calculate_actionable_pipeline_count, calculate_confidence_weighted_pipeline)
with a shared DISTINCT ON (parcel_id) ... ORDER BY parcel_id, scored_at DESC,
score_id DESC CTE, filters applied outside the CTE.

Metric DEFINITION change (tie-break): the old subquery counted ALL rows tied at
MAX(scored_at) for a parcel (a latent double-count when two tied rows both
PASS); the CTE selects EXACTLY ONE deterministic row (highest score_id among
the tie). Metric values across this commit are NOT comparable; the next run
must re-establish a fresh baseline (AUTORESEARCH_MECHANICS.md "When Mutating
prepare.py"). In practice the current write path inserts one row per parcel per
cycle in its own transaction, so same-parcel same-microsecond rows do not occur
and the observed value is unlikely to move — but the definition changed, so the
protocol is followed regardless.

Adds idx_scores_parcel_scored_at(parcel_id, scored_at DESC, score_id DESC) as a
plain CREATE INDEX (apply_schema runs all DDL in one transaction; CONCURRENTLY
would be illegal there). Pure perf hint — the metric is correct without it.

See reviews/13_perf_optimization/{01_risk_review,02_code_writer_response}.md.
```

---

## 6. Verification commands run

```
pip install -r requirements.txt          # R-1330 — deps first
python -m unittest discover tests         # → Ran 576 tests ... OK
python -m py_compile research.py prepare.py tests/*.py   # all compile
git diff --numstat                        # file-level partition confirmed
```

`git diff research.py | grep DISTINCT\ ON\ \(parcel_id\)` → only the item-4
actionability-block batch (a perf change on `flagged_items`, NOT the item-7
metric on `parcel_scores`). `git diff prepare.py | grep -E
'_CycleCache|retry|_BATCH'` → empty. `git diff tests/test_discovery.py | grep
_LATEST_SCORE_CTE` → empty. Partition is clean.

---

## 7. Self-assessed risk note for Agent 3

The single highest residual risk (per Agent 1's verdict) is a silent
SQL-ordering mistake invisible to the fake-cursor offline tests. Mitigations in
place: (a) exact-substring offline assertions on both the item-2 CoStar tail
and the item-7 parcel_id-led ORDER BY; (b) the cache-vs-no-cache bit-identical
equivalence suite for items 2-4; (c) the live-Postgres Step-5 tie-break proof
for item 7. The two places Agent 3 should scrutinize hardest:
`_SQL_LATEST_MARKET_CONTEXT_BATCH`'s `ORDER BY` tail (must match the single-key
CASE exactly) and `_LATEST_SCORE_CTE`'s `ORDER BY parcel_id, scored_at DESC,
score_id DESC` (drop any term → correctness bug). Both are guarded but the
fake-cursor cannot execute them — the live CI is the backstop.
