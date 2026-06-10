# Phase 13 Risk and Architecture Review — Performance / Robustness Pass

**Reviewer:** Agent 1 (Risk and Architecture Reviewer), three-agent code team
(appendix_a_county_connectors.md L9-L86).
**Date:** 2026-06-10.
**Branch:** `claude/ecstatic-davinci-yrclnr` (NOT an `autoresearch/*` branch; no
`experiment_log.tsv` present — confirmed BETWEEN RUNS, so this is normal
three-agent development work, not a live Karpathy experiment).
**Base:** working tree at review time (532 offline tests passing once
`psycopg[binary]`, `python-dotenv`, `requests` are installed — see R-1330).
**Scope:** Coordinator game plan items 1, 2, 3, 4 (pure perf/robustness in
`research.py`) and item 7 (a FORMAL `prepare.py` mutation event, its own
commit, run-history implication documented).

This review continues the numbered-concern house style of
`reviews/04_phase3_fulton_discovery/01_risk_review.md` and
`reviews/10_phase7_8_combined/01_risk_review.md`, opening a fresh **R-13xx**
series. Severity scale: **CRITICAL** (must fix before merge / blocks GO),
**HIGH** (must fix before merge, routine), **MEDIUM** (fix before merge or
carry an explicit accepted-risk note), **LOW** (post-merge acceptable).

---

## 0. Orientation: what the code actually looks like today

Line numbers verified against the working tree (they differ slightly from the
coordinator's estimates, which is itself worth flagging to Agent 2):

| Item | Symbol | Real location | Coordinator estimate |
|------|--------|---------------|---------------------|
| 1 | `_DiscoverySession.get()` | research.py **299-310** (class 260-313) | 260-314 |
| 1 | retry constants | none exist; `_MIN_REQUEST_SPACING_S` L158, `_DISCOVERY_HTTP_TIMEOUT_S` L155 | — |
| 1 | harness reference pattern | connector_harness.py `_http_get` **320-365**; constants `MAX_RETRIES=3`, `BACKOFF_SCHEDULE_S=(1.0,2.0,4.0)`, `RATE_LIMIT_PER_HOST_S=1.0` L100-102 | 273-413 |
| 2 | `_compute_market_context_scores` | research.py **1971-2016** | 1971-2017 |
| 2 | `_SQL_LATEST_MARKET_CONTEXT` | research.py **754-763** | 754-763 |
| 2 | `score_parcel` | research.py **2405-2602** | 2405-2605 |
| 2 | `run_scoring_cycle` loop | research.py **2648-2652** (fn 2607-2659) | 2607-2660 |
| 3 | `_compute_s8` | research.py **2054-2090** | 2054-2092 |
| 3 | `_SQL_SUBMARKET_LAND_MEDIAN` | research.py **769-779** | 2068 (wrong — it is at 769) |
| 4 | `_fetch_actionability_block` | research.py **2329-2336** | 2329-2338 |
| 4 | `_SQL_FLAGGED_ACTIONABILITY_BLOCK` | research.py **785-791** | — |
| 7 | `_LATEST_SCORE_WHERE` + metric fns | prepare.py **559-600** | 549-600 |
| 7 | stale comment to update | prepare.py **552-557** | 552-557 |
| 7 | `_DDL_INDEXES` | prepare.py **485-503**; `parcel_scores` DDL 318-333 | — |
| 7 | `apply_schema` (single txn!) | prepare.py **525-546** | — |

**Two call sites of `_fetch_actionability_block`, not one.** The coordinator's
plan names only the `score_parcel` caller (L2482). There is a SECOND caller:
the public wrapper `run_actionability_screen` at **L4094**. That wrapper is a
single-parcel API used by Phase 9/10 (research.py L4072-L4102), is NOT in the
`run_scoring_cycle` loop, and must keep working through the per-parcel
fallback path (see R-1320). Agent 2 must not break or "optimize" it.

**The per-parcel hot path has FIVE DB round-trips, not three.** Inside the
`run_scoring_cycle` → `score_parcel` loop each parcel triggers:
1. `_fetch_parcel_for_scoring` (L2387, `_SQL_FETCH_PARCEL`) — NOT batched.
2. `_compute_s2` (L1794, `_SQL_S2_GEOMETRY`, a PostGIS `ST_MakeValid`/
   `ST_CollectionExtract` query) — NOT batched.
3. `_compute_market_context_scores` (item 2) — batched.
4. `_compute_s8` (item 3) — batched.
5. `_fetch_actionability_block` (item 4) — batched.

Items 2-4 remove three of five hops. See R-1331 — the un-batched S2 PostGIS
query is very plausibly the dominant per-parcel cost, so the perf win may be
smaller than the plan implies. This is in-scope-as-written (the plan
deliberately scopes 1, 5 — wait, no item 5/6 here — only 2/3/4), but Agent 2
and the human should not expect a 5x speedup from a 3/5 reduction when one of
the remaining two is the heavy one.

---

## 1. Item 1 — retry-with-backoff in `_DiscoverySession.get()`

### R-1301 (CRITICAL) — Retry budget vs. the 90-minute experiment ceiling

`_DiscoverySession.get()` is reached from `run_discovery_cycle`, which under
Phase 10 runs inside the Karpathy experiment and is bounded by the 90-minute
wall clock enforced at the OS level (`prepare.run_with_os_timeout`,
AUTORESEARCH_MECHANICS.md L153-160, L472). The discovery cycle's own soft
ceiling is `_CYCLE_BUDGET_SECONDS = 30*60` (research.py L152). Adding retries
multiplies worst-case wall time per request: with 2 retries and backoff (1s,
2s) PLUS the mandatory `_spacing_sleep` (1s/host) on **every** attempt, a
single permanently-failing request costs up to `1 + (1+1) + (2+1) = 6s`
(spacing + first backoff + spacing + second backoff + spacing) before giving
up, vs ~1s today. Across a paginated corridor of N pages where the endpoint is
flapping, this can blow the 30-minute soft budget and edge toward the 90-min
hard kill, which reverts the whole experiment to baseline (a lost cycle).

**Failure mode:** an experiment that would have failed-fast-and-flagged now
burns 90 minutes retrying a degraded county, gets SIGKILLed by
`run_with_os_timeout`, logs `timeout`, and reverts — strictly worse than the
current bare-`raise_for_status` behavior for systemic outages.

**Mitigation (Agent 2 must do all):**
1. Cap retries at exactly **2** (3 total attempts), matching the Phase 3
   mandate (R-18 in `reviews/04_phase3_fulton_discovery/01_risk_review.md`
   L175: "Up to 2 retries on 5xx with exponential backoff (1s, 2s)").
   Note this is INTENTIONALLY fewer than the harness's `MAX_RETRIES=3` /
   `BACKOFF_SCHEDULE_S=(1.0,2.0,4.0)` — do NOT copy the harness constant.
2. Module-level constants: `_DISCOVERY_MAX_RETRIES = 2`,
   `_DISCOVERY_BACKOFF_SCHEDULE_S = (1.0, 2.0)`. Document the divergence from
   the harness in a comment.
3. Bound the per-request worst case and assert it in a test: with spacing 1s +
   backoff (1,2), worst-case added latency per fully-failed request <= ~5s
   beyond the first attempt. Confirm `2 retries * pages * (~5s)` stays well
   under `_CYCLE_BUDGET_SECONDS` for the realistic Fulton page count (<= a few
   pages per corridor per R-14).

### R-1302 (HIGH) — Retry masks systemic outages; define the give-up contract

The retry must distinguish *transient* (retry) from *systemic* (give up and
let the corridor-level handler flag it). The existing Phase 3 corridor handler
(R-18) already does: on exhausted transport failure, log a `research_log`
abort row + a `data_gap` flag, abort the corridor, **continue to the next
corridor** — it does NOT abort the whole cycle. The new retry must slot UNDER
that handler, not replace it: after 2 retries are exhausted, `get()` must
**raise the same exception class it raises today** so the existing
corridor-level try/except still fires unchanged.

**Failure mode:** if `get()` starts returning a sentinel (e.g. `{}` or `None`)
on exhausted retries instead of raising, the downstream pagination loop
silently treats a dead endpoint as "empty corridor" (R-19), logs
`discovery_empty`, and the experiment records a falsely-low pipeline as if the
county simply had no parcels. That is a metric-integrity corruption disguised
as a perf change.

**Mitigation:** on retry exhaustion, re-raise (preserve the current
`requests.HTTPError` / `ConnectionError` / `Timeout` propagation contract).
Add `TestDiscoveryRetryExhaustionRaises` asserting that after max retries the
exception propagates (NOT swallowed). Cross-check that the existing
corridor-abort test (`test_corridor_failure_does_not_abort_cycle` per Phase 3
R-18) still passes unchanged.

### R-1303 (HIGH) — `_spacing_sleep` must run on EVERY attempt, and its reservation accounting must not double-count

`_spacing_sleep` (research.py L286-297) is not a plain sleep — it *reserves*
the next slot by writing `self._last_request_at[host] = max(now, last) +
wait`. If the retry loop calls `_spacing_sleep` once per attempt (correct, per
the plan: "per-host polite spacing must still be respected on every attempt"),
the reservation logic compounds: attempt 2's `_spacing_sleep` sees the slot
already reserved by attempt 1 and waits the full spacing again — which is the
DESIRED politeness behavior but means the *effective* inter-attempt delay is
`backoff + spacing`, not `max(backoff, spacing)`. Agent 2 must be deliberate
about ordering: the intended sequence per attempt is `_spacing_sleep(host)` →
issue request → on failure `time.sleep(backoff)` → loop. Putting the backoff
sleep BEFORE `_spacing_sleep` vs after changes total latency and must be
chosen and tested explicitly. The harness's `_http_get` (connector_harness.py
L338-364) calls `_rate_limit` at the TOP of each attempt then `time.sleep(
backoff)` at the BOTTOM — mirror that ordering for consistency.

**Failure mode A:** spacing skipped on retries → bursts of requests to a
county already returning 5xx → 429 / IP ban (the appendix L994 explicitly
mandates 1 req/sec/host politeness; violating it on retry is exactly when the
county is most likely to be stressed).

**Failure mode B:** the reservation map `_last_request_at` is never pruned;
under the single-cycle lifetime this is fine, but if a retry path writes a
reservation far in the future and the cycle then queries a different host, no
issue. No action beyond a comment, but Agent 2 should NOT introduce a code
path that leaves a stale future reservation that a later same-host request
honors as a multi-second stall.

**Mitigation:** `_spacing_sleep(host)` at the top of every attempt
(pre-request); `time.sleep(backoff)` after a failed attempt and before the
next loop iteration. Add `TestDiscoveryRetrySpacingPerAttempt` that
monkeypatches `time.sleep` / `time.monotonic` and asserts spacing is invoked
on each of the 3 attempts.

### R-1304 (HIGH) — Retry-on-429 requires reading `Retry-After`, or at least not ignoring it

The plan explicitly adds **429** to the retry set (the harness does NOT retry
429 — see connector_harness.py L351-353, which fail-fasts all 4xx including
429). This is a deliberate divergence and is defensible (429 is the one 4xx
that is genuinely transient), but a 429 retried after only 1-2 seconds while
the server asked for more will just earn another 429 and waste the budget.

**Failure mode:** retrying 429 on the fixed (1s, 2s) schedule when the server
sent `Retry-After: 30` → two more 429s → exhausted retries → corridor aborted
anyway, but now 2x slower and having hammered a rate-limited server.

**Mitigation (choose one, document the choice):**
- (Preferred, minimal) Honor `Retry-After` when present: if the header gives a
  delay LONGER than the scheduled backoff, sleep that instead (cap it so a
  pathological `Retry-After: 3600` cannot blow the budget — e.g.
  `min(retry_after, _DISCOVERY_RETRY_AFTER_CAP_S)` with cap ~10s; beyond the
  cap, give up and let the corridor handler flag it). This is still pure
  stdlib (`int(resp.headers.get("Retry-After", 0))`), no new dependency.
- (Acceptable) Treat 429 exactly like 5xx on the fixed schedule and document
  that `Retry-After` is intentionally ignored for simplicity, with a TODO.

Add `TestDiscoveryRetryHonors429RetryAfter` (or `...Ignores429RetryAfter` with
a documented rationale). Either way: **429 must NOT be conflated with the
fail-fast 4xx path** — write `TestDiscoveryNoRetryOn404` and
`TestDiscoveryRetryOn429` as a matched pair to lock the boundary.

### R-1305 (MEDIUM) — Exact retryable-condition set; do not over- or under-retry

The plan's retry set: connection errors, timeouts, HTTP 5xx, and 429. The
NON-retry set: all other 4xx. Boundary precision matters:
- `requests.exceptions.ConnectionError`, `...Timeout`, `...RequestException`
  (catch-all) — the harness catches all three (L342-347). But the catch-all
  `RequestException` would ALSO catch things like `TooManyRedirects` and
  `InvalidURL`, which are NOT transient. Retrying an `InvalidURL` is pointless
  but harmless (it just fails 3x). Acceptable, but prefer catching
  `ConnectionError` and `Timeout` specifically for retry, and letting other
  `RequestException` subclasses propagate immediately (fail-fast), which is
  both more correct and faster.
- HTTP status branching must be: `200-399` → return; `429` → retry; other
  `400-499` → raise immediately (no retry); `500-599` → retry. The harness
  pattern at L348-355 is the template, MINUS the 429-into-4xx behavior.

**Failure mode:** a 503 with a JSON `{"error": {...}}` body (ArcGIS returns
HTTP 200 with an error envelope sometimes — see connector_harness.py
`_parse_arcgis_response` L373-393 / R-04). `_DiscoverySession.get()` calls
`resp.raise_for_status()` then `resp.json()`; it does NOT inspect for the
ArcGIS error envelope. Item 1 is scoped to retry on HTTP status, NOT to add
error-envelope handling — but Agent 2 must NOT accidentally start retrying on
a 200-with-error-envelope (that path returns 200, so it correctly will NOT
retry; just confirm the status check happens on `resp.status_code`, not on
parsed content). Keep error-envelope handling out of scope (it is the
corridor parser's job, already handled elsewhere).

**Mitigation:** explicit status-class branching mirroring the harness, with
429 pulled into the retry branch. `TestDiscoveryRetryOn500`,
`TestDiscoveryNoRetryOn400`, `TestDiscoveryNoRetryOn403`. Document why
`InvalidURL`/`TooManyRedirects` are not retried (if Agent 2 narrows the
except).

### R-1306 (MEDIUM) — No-import-from-`connector_harness`, no new module: copy-with-attribution

Hard constraint: `research.py` must not import from `connector_harness.py`
(contractual isolation, AUTORESEARCH_MECHANICS.md Five-File Contract spirit;
Phase 3 R-17 already established the "recreate the pattern, don't import the
private API" precedent). The retry logic must be re-implemented inside
`_DiscoverySession`, NOT factored into a new shared module (out of scope).

**Failure mode:** Agent 2 "DRYs" the retry into a new `http_utils.py` or
imports `connector_harness._http_get` → violates both the
no-new-top-level-module and the no-cross-import constraints → automatic NO-GO.

**Mitigation:** retry lives as a method/loop on `_DiscoverySession`. Add a
comment citing this constraint and Phase 3 R-17. Agent 3 greps `research.py`
for `import connector_harness` / `from connector_harness` and confirms zero.

### R-1307 (MEDIUM) — Idempotency / side effects of retrying a GET

GETs to ArcGIS `query` endpoints are nominally idempotent, so retrying is
safe. BUT `_DiscoverySession.get()` is also used to fetch the layer schema for
the field-mapping-drift check (Phase 3 R-25). Retrying those is fine. There is
NO write/POST through this session, so no double-write risk. Confirm no caller
treats a retried-then-succeeded response differently from a first-try success
(they should be identical). Low effort, just verify.

**Mitigation:** none needed beyond confirming all `get()` callers are
read-only. Note in the Agent 2 response that the retry is safe because the
session issues only GETs.

### R-1308 (LOW) — Logging on retry must strip credentials and not spam

The harness logs each retry at INFO with `_strip_sensitive_query_params`
(connector_harness.py L283-295, L359-363). `research.py`'s ArcGIS URLs do not
currently carry tokens (Fulton is public), but Phase 11 counties or Regrid
(appendix L806-811, API key) might. Agent 2 should log retries via the module
`log` (research.py uses `log = logging.getLogger("research")`, never `print` —
enforced by `test_no_print_in_run_discovery_cycle`, test_discovery.py L186),
and if it logs the URL, strip query params defensively. Since
`test_no_print_in_run_discovery_cycle` (L189-194) only covers a fixed function
allow-list, ensure any new retry helper is named such that it is covered, or
simply never `print`.

**Mitigation:** INFO-level retry log via `log`, no URL query string (or
stripped). One test asserting no `print` in the new code path.

---

## 2. Items 2-4 — batch the per-cycle lookups (cache-as-optional-arg)

### R-1310 (CRITICAL) — Source-preference / latest-row SQL semantics must be byte-preserved

This is the single highest-risk area for Items 2-4. The metric, confidence,
sub-scores, and all DB rows must be **bit-identical** before/after (hard
constraint). Three SQL constants encode delicate ordering semantics that a
"batch the same thing" rewrite can silently change:

- `_SQL_LATEST_MARKET_CONTEXT` (L754-763): picks ONE row per submarket via
  `ORDER BY (CASE WHEN source = 'costar' THEN 0 ELSE 1 END), as_of_date DESC
  LIMIT 1`. The CoStar-preference CASE then `as_of_date DESC` is the exact tie
  policy (R-513). A batch query over many submarkets MUST reproduce this
  per-submarket. The correct construction is
  `SELECT DISTINCT ON (submarket_id) submarket_id, <cols> FROM market_context
  WHERE submarket_id = ANY(%s) ORDER BY submarket_id, (CASE WHEN source =
  'costar' THEN 0 ELSE 1 END), as_of_date DESC`. **The `DISTINCT ON` ORDER BY
  must lead with `submarket_id` and then carry the EXACT same CASE +
  `as_of_date DESC` tail** or the chosen row changes for any submarket with
  multiple sources/dates. A `GROUP BY` + `MAX` rewrite is WRONG here (cannot
  reproduce the CoStar-preference tie-break) — Agent 2 must use `DISTINCT ON`.
- `_SQL_SUBMARKET_LAND_MEDIAN` (L769-779): a per-submarket aggregate
  (`PERCENTILE_CONT(0.5)` + `COUNT(*)`) with `comp_type='land'`,
  `price_per_acre IS NOT NULL`, and `sale_date >= CURRENT_DATE - INTERVAL '36
  months'`. The batch form is `SELECT submarket_id, COUNT(*) AS n,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_per_acre) FROM sales_comps
  WHERE submarket_id = ANY(%s) AND comp_type='land' AND price_per_acre IS NOT
  NULL AND sale_date >= (CURRENT_DATE - INTERVAL '36 months') GROUP BY
  submarket_id`. **`CURRENT_DATE` is evaluated by the DB.** Per-parcel today,
  each call re-evaluates `CURRENT_DATE` — but all within the same cycle, same
  day, so identical. Batched, it is evaluated once. Result: bit-identical
  EXCEPT in the pathological case of a cycle that straddles midnight UTC
  (see R-1314).
- `_SQL_FLAGGED_ACTIONABILITY_BLOCK` (L785-791): `SELECT description ... WHERE
  parcel_id=%s AND flag_type='actionability_block' AND status='open' ORDER BY
  flagged_at DESC LIMIT 1`. Batch form needs `DISTINCT ON (parcel_id)
  description, parcel_id ... WHERE parcel_id = ANY(%s) AND ... ORDER BY
  parcel_id, flagged_at DESC`. See R-1311 for the tie-break subtlety.

**Failure mode:** silent metric drift. Two submarkets that today pick the
CoStar row would, under a `GROUP BY ... MAX(as_of_date)` rewrite that drops
the CASE, pick a brokerage row with a later date → different S4/S5/S6 →
different composite → metric moves WITHOUT any real research change → the
Karpathy ratchet records a phantom improvement/regression. This is exactly the
"metric corruption disguised as a perf change" the immutability rules exist to
prevent (AUTORESEARCH_MECHANICS.md L11, L47).

**Mitigation:**
1. New batch SQL constants are `DISTINCT ON`-based (NOT GROUP BY for the
   market_context and flag cases). Module-level `_SQL_*` constants with `%s`
   placeholders (hard constraint; AST scanner R-1325).
2. **Equivalence test against the per-parcel path.** For each of items 2/3/4,
   Agent 2 writes a test that builds a fixture dataset with >= 2 rows per
   submarket spanning sources/dates and asserts the batch result for each key
   EQUALS what the existing single-key SQL would return. The cleanest form:
   run the cycle with cache=None (per-parcel) and again with the prefetch
   cache, assert IDENTICAL `parcel_scores` rows (composite, confidence,
   sub_scores JSON, actionability, strategy_fit) for every parcel. This is the
   real acceptance gate for "bit-identical."

### R-1311 (HIGH) — `_fetch_actionability_block` batch changes tie-break determinism

The single-parcel query (L785-791) is `ORDER BY flagged_at DESC LIMIT 1` with
NO secondary tie-break. Among rows with equal `flagged_at`, PostgreSQL returns
an arbitrary one. `flagged_items` has `flag_id SERIAL PRIMARY KEY` and
`flagged_at` (prepare.py L471-472). A batch `DISTINCT ON (parcel_id) ... ORDER
BY parcel_id, flagged_at DESC` is ALSO non-deterministic on ties — but if
Agent 2 adds `flag_id DESC` as a tie-break (sensible for determinism, mirrors
item 7's `score_id DESC`), the batch path becomes MORE deterministic than the
per-parcel path. That is technically a behavioral change.

In practice it does not matter: `_gate_deal_killer` (L2321-2326) only asks
"does ANY open block mention a non-`entitlement` keyword", and two blocks with
the identical microsecond `flagged_at` for the same parcel both being open is
vanishingly rare. But "bit-identical" is the stated bar.

**Failure mode:** a contrived parcel with two open `actionability_block` rows
sharing `flagged_at`, one mentioning "entitlement" and one mentioning a
deal-killer, could flip the gate verdict depending on which row wins — and the
per-parcel vs batch path could disagree.

**Mitigation:** to be strictly bit-identical, the batch query should reproduce
the SAME (non-deterministic) ordering — i.e. do NOT add `flag_id DESC`.
HOWEVER, the better engineering choice is to add `flag_id DESC` to BOTH the
batch query AND keep the per-parcel fallback query unchanged... which would
make them disagree. **Recommendation:** add `flag_id DESC` tie-break to the
batch query AND leave the single-parcel `_SQL_FLAGGED_ACTIONABILITY_BLOCK`
unchanged, and write `TestActionabilityBlockTieBreakDocumented` that
constructs the equal-`flagged_at` two-block case and asserts the documented
behavior. Flag this micro-divergence explicitly in the Agent 2 response so
Agent 3 can rule on whether "bit-identical" tolerates an
unobservable-in-practice tie-break that only manifests on duplicate-microsecond
flags. If Agent 3 insists on exact parity, drop the tie-break from the batch
query too.

### R-1312 (CRITICAL) — Optional-cache parameter must default None and preserve the exact current path

The API-compat design: add an optional cache arg defaulting to `None`; when
`None`, fall back to the current per-parcel query. This protects the second
caller of `_fetch_actionability_block` (`run_actionability_screen` L4094) and
the ad-hoc `score_parcel(parcel_id)` path (L2438, `cycle_id="adhoc"`), and any
test that calls `score_parcel` / the helpers directly.

**ALL callers of `score_parcel` (verified by grep):**
- `run_scoring_cycle` loop, research.py **L2649** (passes `conn`, `cycle_id`,
  `params`; this is where the cache should be threaded in).
- Tests: `tests/test_discovery.py` `TestPhase78ScoreParcelEndToEnd` and
  siblings (L1338+, L1357, L1390, L1423, L1441, L1475, L1519, L1554) call
  `research.score_parcel(...)` directly with a `Phase5FakeConnection` and NO
  cache. These MUST keep passing untouched → the cache parameter MUST be
  keyword-only with a `None` default and the None path MUST be the existing
  code verbatim.

**Callers of the three helpers directly:** `_compute_market_context_scores`,
`_compute_s8`, `_fetch_actionability_block` are each called once from
`score_parcel`. `_fetch_actionability_block` is ALSO called from
`run_actionability_screen` (L4094). Agent 2 must make the new cache argument
optional on whichever function it threads through, and the no-cache behavior
must be byte-identical.

**Failure mode:** changing `score_parcel`'s positional signature, or making the
cache non-optional, breaks ~8 existing end-to-end tests and the public
`run_actionability_screen` API → blast radius far beyond the perf change.

**Mitigation:** keyword-only optional cache params, `= None` default, None
path = current code. Add `TestScoreParcelNoCacheUnchanged` that asserts a
parcel scored via the direct (no-cache) path produces a row identical to the
cached-cycle path. Agent 3 verifies the existing Phase 7/8 end-to-end tests
pass with ZERO modification.

### R-1313 (HIGH) — Cache staleness WITHIN a cycle: who writes market_context / sales_comps / flagged_items mid-cycle?

The coordinator explicitly asks: can `market_context` / `sales_comps` /
`flagged_items` change mid-cycle, and who writes them? Findings from the code:

- **`market_context`** is written ONLY by `run_ingestion_cycle` (CoStar
  ingestion, research.py L4024-4060, via `_SQL_INSERT_MARKET_CONTEXT` and the
  submarket_stats loader). It is NEVER written by `run_scoring_cycle`. So
  within a single scoring cycle, prefetching once is safe UNLESS an ingestion
  cycle runs concurrently. Concurrency across cycles is "supported but not
  encouraged" (Phase 3 R-33). A concurrent ingestion that inserts a fresher
  market_context row mid-scoring-cycle would, under per-parcel querying,
  affect parcels scored after the insert but not before — i.e. the CURRENT
  behavior is itself nondeterministic under concurrency. Prefetch actually
  makes it MORE consistent (all parcels in the cycle see the same snapshot).
- **`sales_comps`** — same story: written by ingestion (`land_sales_comps`
  loader), never by scoring. Prefetch is safe; concurrency caveat identical.
- **`flagged_items`** — THIS ONE IS WRITTEN DURING `score_parcel` ITSELF.
  `score_parcel` emits `data_gap` / staleness / S6 / S8 flags via `_flag(...)`
  inside its transaction (L2530-2566). Those are `flag_type='data_gap'`, NOT
  `flag_type='actionability_block'`. `_fetch_actionability_block` filters
  `flag_type='actionability_block' AND status='open'`. So scoring NEVER writes
  the rows that the actionability-block prefetch reads. **Therefore prefetching
  the actionability blocks once at cycle start is safe against the cycle's own
  writes.** Confirm there is no other writer of `actionability_block` rows in
  the scoring path (grep: the only producers are Phase 11+ manual review per
  Phase 7/8 R-533).

**Failure mode:** if a future phase makes `score_parcel` write an
`actionability_block` row for parcel A that parcel B (scored later in the same
cycle) should see, a cycle-start prefetch would miss it. Today no such
cross-parcel dependency exists, but the prefetch bakes in the assumption.

**Mitigation:** Agent 2 documents in a comment ON the prefetch that it is
valid because (a) scoring does not write `market_context`/`sales_comps`, and
(b) scoring writes only `data_gap` flags, never `actionability_block`. Add
`TestActionabilityBlockPrefetchIgnoresDataGapFlags` proving a `data_gap` flag
written mid-cycle does not enter the actionability-block cache. If Agent 2
cannot cheaply prove (b) holds for all current writers, fall back to NOT
caching item 4 and keep items 2/3 only — a partial win is acceptable; a
correctness bug is not.

### R-1314 (MEDIUM) — Midnight-UTC straddle changes `CURRENT_DATE` between prefetch and (hypothetical) per-parcel re-eval

`_SQL_SUBMARKET_LAND_MEDIAN` and the staleness math (`date.today()` in
`_compute_market_context_scores` L2008 and `_compute_parcel_basis_per_acre`
L2037) use the wall clock. Per-parcel today, a cycle that runs across the
UTC-midnight boundary could compute `CURRENT_DATE` / `date.today()` as day D
for early parcels and D+1 for late ones — already nondeterministic. Prefetch
collapses the land-median `CURRENT_DATE` to one evaluation. This is a
consistency IMPROVEMENT, but means the batched result can differ from a
per-parcel result for a midnight-straddling cycle, so the equivalence test
(R-1310) must NOT run across midnight (it won't — tests use fixtures and fake
conns, `CURRENT_DATE` is the DB's; with a fake conn the SQL is never executed
so this is moot in tests; it only matters in live CI against Postgres).

**Failure mode:** the live-Postgres CI equivalence check flakes once a day if
it happens to straddle 00:00 UTC. Extremely unlikely given sub-second test
runtime, but note it.

**Mitigation:** none required in code. Document that the batched land-median
evaluates `CURRENT_DATE` once per cycle (a feature, not a bug — it makes the
cycle internally consistent). The staleness `date.today()` calls in Python are
out of scope for items 2-4 (they are not the batched SQL) and should be left
exactly as-is.

### R-1315 (HIGH) — Dict-key normalization: submarket NULL, empty, and case

The prefetch builds a dict keyed by submarket. Risks:
- **NULL submarket.** `parcels.submarket` is nullable and for Fulton is often
  NULL or a corridor name (Phase 7/8 R-511). `_compute_market_context_scores`
  and `_compute_s8` already guard `if not submarket: return <empty>` (L1990,
  L2067). The prefetch must:
  (a) collect the DISTINCT NON-NULL, NON-empty submarkets from the cycle's
      parcel list for the `ANY(%s)` array,
  (b) on lookup, a parcel with NULL/empty submarket must hit the SAME
      empty-result branch as today (return the all-None dict / S8 None), NOT a
      `KeyError` and NOT a spurious match.
- **Case / whitespace.** The per-parcel SQL matches `submarket_id = %s`
  exactly (case-sensitive, no trim). The dict key MUST therefore be the raw
  `parcel['submarket']` string, NOT `.lower()` / `.strip()`-normalized — or a
  parcel whose submarket is `"South Fulton"` would miss a market_context row
  keyed `"south_fulton"` differently than the DB's `=` would. **Do NOT
  normalize keys.** Use the exact string the DB comparison would use. (Phase
  7/8 R-511 already flags that `parcels.submarket` and
  `market_context.submarket_id` may not share a vocabulary; the prefetch must
  preserve whatever match/miss behavior exists today, not "fix" it.)
- **`ANY(%s)` with an empty list.** If NO parcel in the cycle has a non-NULL
  submarket, the `ANY(%s)` array is empty. `submarket_id = ANY(ARRAY[]::text[])`
  is valid SQL returning zero rows — but psycopg adapting an empty Python list
  needs care (see R-1316). The prefetch must short-circuit (skip the query,
  build an empty dict) when the distinct-submarket list is empty.

**Failure mode:** `KeyError` on a NULL-submarket parcel crashes the cycle
(every parcel after it is unscored); or case-normalization causes a parcel to
match a row it would not have matched per-parcel → different sub-score →
metric drift.

**Mitigation:** prefetch helper returns a plain dict; lookup is
`cache.get(submarket)` with the same `if not submarket` guard as today BEFORE
the lookup, so None/empty never reaches the dict. Keys are raw strings.
`TestPrefetchSubmarketNullParcel`, `TestPrefetchSubmarketCaseSensitive`,
`TestPrefetchEmptySubmarketList` (asserts no query issued / empty dict).

### R-1316 (HIGH) — psycopg `ANY(%s)` list adaptation

Item 4 (and the batch forms of 2/3) use `WHERE parcel_id = ANY(%s)` /
`submarket_id = ANY(%s)` with a Python list as the bound parameter. psycopg3
adapts a Python `list` to a Postgres array, so `cur.execute(sql, (parcel_ids,))`
with `parcel_ids: list[str]` and `... = ANY(%s)` works. Subtleties:

- The parameter must be passed as a **single-element tuple wrapping the list**:
  `cur.execute(_SQL_..., (parcel_ids,))` — NOT `cur.execute(sql, parcel_ids)`
  (which would treat each id as a separate placeholder and fail, since there
  is one `%s`). Easy to get wrong.
- **Empty list:** psycopg3 adapts `[]` to an empty array; `= ANY(ARRAY[])`
  returns no rows without error. Still, short-circuit (R-1315) to avoid a
  pointless round-trip.
- **Type homogeneity:** `parcel_id` is TEXT, submarkets are TEXT — a list of
  `str` adapts to `text[]`. No mixed types expected. If any id is non-str
  (shouldn't happen; they come from `parcels.parcel_id`), adaptation could
  pick the wrong array type.
- **Mock/test fidelity:** `Phase5FakeConnection._SharedQueueCursor`
  (test_discovery.py L1084-1112) records `execute(sql, params)` and serves
  `fetchall()` from a queue — it does NOT actually run SQL, so it will NOT
  catch a malformed `ANY(%s)`. The equivalence/coverage tests must therefore
  either (a) assert on the recorded `(sql, params)` shape (one `%s`, params is
  `(list,)`), AND/OR (b) rely on the live-Postgres CI smoke test to catch real
  adaptation errors. Prefer adding a `(sql, params)`-shape assertion so the
  offline suite catches the `(parcel_ids,)` vs `parcel_ids` mistake.

**Failure mode:** `cur.execute(sql, parcel_ids)` (forgetting the tuple wrap)
→ "the query has N placeholders but M parameters were passed" at runtime in
production, invisible to the fake-conn tests → cycle crash only discovered in
live CI or, worse, the overnight run.

**Mitigation:** pass `(list,)`; short-circuit empty; add a static/shape test
`TestBatchSqlAnyParamShape` asserting each batch constant has exactly one
`%s` and is called with a one-tuple-wrapping-a-list. Confirm in the
live-Postgres CI smoke (`validate-phase1.yml` / the postgis smoke test) that
the batch queries execute.

### R-1317 (MEDIUM) — Prefetch must key off the cycle's ACTUAL parcel list, computed before the loop

`run_scoring_cycle` builds `parcel_ids` at L2646 (`_SQL_LIST_PARCELS_FOR_SCORING`).
The prefetch must use exactly this list. Two ordering hazards:
- The prefetch needs the parcels' **submarkets** (for items 2/3 keyed by
  submarket) and **parcel_ids** (for item 4). Submarkets are NOT in the
  `parcel_ids` result (which is just `SELECT p.parcel_id`). So the prefetch
  either (a) issues one extra query joining parcels to get distinct
  submarkets, or (b) reuses the per-parcel `_fetch_parcel_for_scoring` results
  — but those are fetched INSIDE `score_parcel`, after the prefetch would run.
  Cleanest: one small `SELECT DISTINCT submarket FROM parcels WHERE market=%s
  AND submarket IS NOT NULL AND parcel_id = ANY(%s)` for the submarket set, and
  pass the `parcel_ids` list straight to the item-4 batch. Net DB hops: +2 or
  +3 batch queries replacing 3*N per-parcel queries. Strongly net-positive for
  N >> 1, net-NEGATIVE for N in {0,1} (R-1318).
- The prefetch must run AFTER the cycle-id-collision guard (L2637-2642) and
  AFTER `parcel_ids` is known, BEFORE the loop.

**Failure mode:** prefetching from a different parcel set than the loop
iterates (e.g. all market parcels vs only unscored/PENDING ones) → cache
contains rows for parcels not scored (harmless) or MISSES submarkets for
scored parcels (forces None → metric drift). Must key off the same set.

**Mitigation:** derive the prefetch keys from the same `parcel_ids` list /
their submarkets. `TestPrefetchKeyedOnCycleParcelSet`.

### R-1318 (LOW) — Degenerate cycle sizes (0 or 1 parcel) make batching a net loss

For a cycle with 0 parcels, the loop never runs; the prefetch should not issue
queries (short-circuit). For 1 parcel, 3 batch queries replace 3 per-parcel
queries — a wash, slightly worse (extra distinct-submarket query). This is
fine (correctness unaffected) but Agent 2 should short-circuit the prefetch
when `parcel_ids` is empty, and not bother special-casing N=1.

**Mitigation:** `if not parcel_ids: skip prefetch`. No N=1 special case.

### R-1319 (MEDIUM) — Provenance/flag side-effects must stay per-parcel and unchanged

`_compute_market_context_scores` returns not just S4/S5/S6 but
`staleness_days`, `provenance`, `as_of_date`, `source` (L1981-1989), which
`score_parcel` uses to emit the staleness `data_gap` flag (L2537-2544) and the
notes string. `_compute_s8` returns the `s8_prov` dict driving the
sample-size-shortfall flag (L2556-2566). If item 2/3 batching returns ONLY the
scores and drops the provenance, the flags/notes change → DB rows (flagged_items,
research_log, parcel_scores.notes) are no longer bit-identical.

**Failure mode:** batched market-context cache stores only S4/S5/S6; the
staleness flag stops firing (or fires with wrong provenance) → flagged_items
rows differ → violates bit-identical.

**Mitigation:** the prefetch cache value per submarket must carry the SAME
fields the per-parcel helper returns today (scores + staleness + provenance +
as_of_date + source for market_context; basis/median/n/n_below_min for S8 —
note S8's basis is PER-PARCEL, only the median+n are per-submarket, so the S8
cache holds only `{median, n}` per submarket and the basis is still computed
per-parcel from parcel attributes). `TestPrefetchPreservesProvenanceFlags`
asserts identical flagged_items + notes between cached and no-cache paths.

### R-1320 (HIGH) — Second `_fetch_actionability_block` caller (`run_actionability_screen`, L4094) must be untouched

`run_actionability_screen` (L4072-4102) calls `_fetch_actionability_block(conn,
parcel_id)` for a single parcel when `conn is not None` (L4093-4094). It has
no cache and no cycle context. The item-4 change must keep
`_fetch_actionability_block(conn, parcel_id)` working with its current
signature (the cache is an ADDITIONAL optional path, not a replacement). If
Agent 2 replaces `_fetch_actionability_block` with a batch-only function, this
public wrapper breaks.

**Failure mode:** Phase 9 snapshot / Phase 10 loop code that calls
`run_actionability_screen(conn=...)` crashes or silently returns wrong
blockers.

**Mitigation:** keep `_fetch_actionability_block(conn, parcel_id)` intact; add
the batch as a separate helper feeding the optional cache. `score_parcel`
consults the cache when present, else calls `_fetch_actionability_block`.
Agent 3 greps for all `_fetch_actionability_block` and
`run_actionability_screen` callers and confirms both paths exercised by tests.

---

## 3. Item 7 — prepare.py metric-query refactor (FORMAL MUTATION EVENT)

### R-1321 (CRITICAL) — Tie-break semantics change is REAL and affects metric comparability across the mutation boundary

The known semantic difference (correctly surfaced by the coordinator): the
correlated subquery `scored_at = (SELECT MAX(scored_at) ...)` counts **ALL**
rows tied at MAX(scored_at) for a parcel, whereas `DISTINCT ON (parcel_id) ...
ORDER BY parcel_id, scored_at DESC, score_id DESC` picks **exactly one**. So
for a parcel with two rows at the identical `scored_at`:
- **Today:** if BOTH tied rows satisfy `actionability='PASS' AND
  composite_score>=threshold`, the parcel is counted **twice** in
  `actionable_pipeline_count` and its confidence summed **twice** in
  `confidence_weighted_pipeline`. If only one satisfies, counted once. This is
  a latent DOUBLE-COUNT bug in the current metric.
- **After:** exactly one row per parcel is selected (the `score_id DESC`
  tie-break makes it deterministic — the highest score_id, i.e. the
  last-inserted among the tie), then the PASS/threshold filter is applied to
  that single row. No double count.

**This is a metric DEFINITION change, which is precisely why item 7 is a
`prepare.py` mutation requiring its own commit + a fresh baseline**
(AUTORESEARCH_MECHANICS.md "When Mutating prepare.py" L388-405). Metric values
computed before this commit are NOT comparable to values after — the protocol
must be followed: the next run starts a NEW branch with a NEW tag and the
first experiment re-establishes the baseline under the new rule.

**How often do scored_at ties actually occur?** `scored_at TIMESTAMPTZ DEFAULT
NOW()` (prepare.py L322). Within one transaction, `NOW()` is the transaction
start time, constant. `score_parcel` inserts ONE parcel_scores row per
transaction (L2500-2515), and `run_scoring_cycle` calls `score_parcel`
sequentially — each in its own `conn.transaction()` SAVEPOINT but sharing the
outer connection/transaction (L2657 `conn.commit()`). **If all inserts in a
cycle share the outer transaction's `NOW()`, EVERY row in a cycle could share
the identical `scored_at`** — making ties NOT rare but POTENTIALLY UNIVERSAL
within a cycle. BUT a parcel is inserted only once per cycle, so two rows for
the SAME parcel at the same `scored_at` requires the parcel to be scored twice
in one cycle (it is not — `parcel_ids` is distinct) OR scored in two different
cycles that happen to share a `scored_at` (different transactions → different
`NOW()` → astronomically unlikely to collide to the microsecond). **Net: the
double-count is real in principle but requires same-parcel same-microsecond
rows, which the current write path does not produce.** So in practice the
metric value is unlikely to CHANGE for existing data — but the DEFINITION
changed, and Agent 2 must not assume the value is identical; it must
re-baseline.

**Mitigation:**
1. Land item 7 as its OWN commit, message starting `prepare-mutation:`
   (hard requirement). Body documents: (a) the tie-break change, (b) the
   double-count-elimination, (c) "next run requires a fresh baseline; metric
   values across this commit are not comparable" per the mutation protocol.
2. Add a test in `tests/test_prepare.py` constructing the
   same-parcel-two-rows-same-`scored_at` case (via the fake cursor scripting
   the COUNT result) — actually, the fake cursor cannot execute SQL, so this
   must be a LIVE-Postgres test (R-1324). At minimum, add an offline test
   asserting the new SQL contains `DISTINCT ON (parcel_id)` and the
   `ORDER BY parcel_id, scored_at DESC, score_id DESC` tail.
3. Update the now-stale comment at prepare.py L552-557 (hard requirement) to
   describe the implemented `DISTINCT ON` CTE and REMOVE the "deliberately
   deferred" language.

### R-1322 (CRITICAL) — test_prepare.py assertions hard-code the OLD SQL shape and WILL break

Three existing tests in `tests/test_prepare.py` assert on the exact SQL string
and WILL fail after the refactor — Agent 2 MUST update them as part of item 7,
and Agent 3 must verify the updates do not weaken the metric's guarantees:

- **L107-118 `test_where_clause_carries_actionability_and_threshold_predicates`**
  asserts `self.assertIn("MAX(scored_at)", sql)`. The DISTINCT ON refactor
  REMOVES `MAX(scored_at)` entirely → this assertion fails. It must become an
  assertion on the new latest-row mechanism, e.g. `assertIn("DISTINCT ON
  (parcel_id)", sql)` and `assertIn("scored_at DESC", sql)`. **Do not simply
  delete the latest-row assertion** — that would drop the guard that the
  metric selects latest-per-parcel.
- **L136-146 `test_uses_same_where_clause_as_count`** splits each query on the
  FIRST `"WHERE"` and compares the tails. With a CTE
  (`WITH latest AS (SELECT DISTINCT ON ... WHERE ...) SELECT COUNT(*) FROM
  latest WHERE ...`), the FIRST `WHERE` is now INSIDE the CTE, so the split
  captures the CTE's inner predicate, not the outer PASS/threshold filter, and
  the two functions may legitimately share the CTE but differ in projection.
  This assertion's logic breaks structurally. It must be rewritten to compare
  the shared CTE / shared latest-row sub-SQL, or restructured so both
  functions are built from a shared `_LATEST_SCORE_CTE` constant and the test
  asserts both reference that constant.
- **L95-105 / L148-153 threshold-binding tests** assert `params ==
  (composite_threshold,)` and `%s` present. If the CTE introduces NO new bound
  parameter (the actionability/threshold filter stays in the outer query),
  params remain `(threshold,)` — GOOD, keep. But if Agent 2 moves the
  threshold filter INTO the CTE, params still `(threshold,)`. Either way the
  single-bound-param invariant must hold; confirm the refactor does not add a
  second placeholder that breaks these.

**Failure mode:** Agent 2 refactors prepare.py, the offline `test_prepare.py`
goes red, and a careless fix "greens" it by deleting the latest-row / shared-
WHERE assertions → the metric loses its regression guards silently.

**Mitigation:** Agent 2 rewrites these three tests to assert the NEW structure
while PRESERVING their intent (latest-per-parcel selection; both metric
functions agree on the parcel set; single bound threshold param; no inline
threshold). **Recommended design that keeps the tests simple:** factor a
single module-level `_LATEST_SCORE_CTE` (the `WITH latest AS (SELECT DISTINCT
ON (parcel_id) ... )`) and a single `_LATEST_SCORE_FILTER` (the
`actionability='PASS' AND composite_score >= %s` applied to `latest`), and
have BOTH metric functions compose `f"{_LATEST_SCORE_CTE} SELECT <proj> FROM
latest WHERE {_LATEST_SCORE_FILTER}"`. Then `test_uses_same_where_clause`
becomes "both reference `_LATEST_SCORE_CTE` and `_LATEST_SCORE_FILTER`". Agent
3 verifies the rewritten tests still fail if someone changes one function's
parcel-selection but not the other's.

### R-1323 (HIGH) — The fake cursor cannot validate the CTE; correctness rests on live-Postgres CI

`tests/test_prepare.py::FakeCursor` (L37-54) records SQL and serves scripted
`fetchone` results — it does NOT run SQL. So the offline suite CANNOT verify
that the `DISTINCT ON` CTE actually returns the right rows; it can only assert
on the SQL string. The real semantic validation must come from the
live-Postgres path: `.github/workflows/validate-phase1.yml` re-runs
`python prepare.py` against live Supabase on any push touching `prepare.py`
(per CLAUDE.md). The CLI path (`_cli_main` L665-680) calls both metric
functions. But that only exercises them against whatever data is in the live
DB (likely empty → returns 0, which does not exercise the tie-break).

**Failure mode:** the DISTINCT ON SQL has a subtle bug (e.g. wrong ORDER BY
column order making `DISTINCT ON` pick the wrong row, or a missing
`ORDER BY parcel_id` prefix which is REQUIRED for `DISTINCT ON` to be
well-defined) that no offline test catches and the empty-DB CI does not
exercise.

**Mitigation:** Agent 2 SHOULD add a live-Postgres test (in
`tests/test_postgis_smoke.py`, which already uses a real cursor and `fetchall`
— L93) that inserts two PASS rows for one parcel at the SAME `scored_at` plus
a third lower-`scored_at` row, then asserts `calculate_actionable_pipeline_count
== 1` (DISTINCT ON picks one) and that the chosen row is the highest `score_id`.
If a live DB is unavailable in the offline suite, gate the test behind the
same skip guard the smoke test uses. At minimum, an offline assertion that the
SQL has `ORDER BY parcel_id, scored_at DESC, score_id DESC` (the `parcel_id`
lead is mandatory for `DISTINCT ON (parcel_id)`).

### R-1324 (HIGH) — `DISTINCT ON` requires `ORDER BY` leading with the distinct key — a correctness landmine

`SELECT DISTINCT ON (parcel_id) ... ORDER BY parcel_id, scored_at DESC,
score_id DESC` is correct. If Agent 2 writes `ORDER BY scored_at DESC,
score_id DESC` WITHOUT the leading `parcel_id`, Postgres raises `SELECT
DISTINCT ON expressions must match initial ORDER BY expressions` — a hard
error at query time (caught by live CI, NOT by fake-cursor offline tests).
Worse, some rewrites "fix" the error by reordering to `ORDER BY parcel_id,
scored_at DESC` and DROPPING `score_id DESC`, which reintroduces
non-determinism on `scored_at` ties (defeating the entire deterministic
tie-break the plan requires).

**Mitigation:** the ORDER BY MUST be exactly `parcel_id, scored_at DESC,
score_id DESC`. Offline assertion on this exact substring. Document why each
term is present (parcel_id: DISTINCT ON requirement; scored_at DESC: latest;
score_id DESC: deterministic tie-break).

### R-1325 (HIGH) — The new index inside `apply_schema`'s single transaction: lock + CONCURRENTLY incompatibility

`apply_schema` runs ALL of `_ALL_DDL` inside ONE transaction
(prepare.py L536-539, single cursor loop, then `conn.commit()`). The proposed
`CREATE INDEX IF NOT EXISTS idx_scores_parcel_scored_at ON
parcel_scores(parcel_id, scored_at DESC, score_id DESC)`:

- **As a plain `CREATE INDEX` (non-CONCURRENTLY):** valid inside a
  transaction. It takes an `ACCESS EXCLUSIVE` lock on `parcel_scores` for the
  build duration, blocking reads/writes to that table. For the current data
  volume (Phase 7/8 scale, thousands of rows) the build is sub-second, so the
  lock is negligible. SAFE to add to `_DDL_INDEXES`.
- **As `CREATE INDEX CONCURRENTLY`:** ILLEGAL inside a transaction block —
  Postgres raises `CREATE INDEX CONCURRENTLY cannot run inside a transaction
  block`. Since `apply_schema` wraps everything in a transaction, Agent 2 MUST
  NOT use `CONCURRENTLY` here. (If a non-blocking build were ever needed for a
  large table, it would require restructuring `apply_schema` to commit before
  the concurrent index — out of scope; the table is small.)
- **Deadlock risk:** a single `CREATE INDEX` in a schema-apply transaction
  that does nothing but DDL cannot deadlock against itself. The only deadlock
  vector is `apply_schema` running concurrently with a scoring cycle holding a
  long transaction on `parcel_scores`. `apply_schema` is a setup/migration
  action (CLI `python prepare.py`, `_cli_main`), not run during scoring;
  concurrency is not a normal operating mode. Low risk; document.

**Failure mode:** Agent 2 reaches for `CONCURRENTLY` (a common "don't lock
production" instinct) → `apply_schema` throws → "schema apply failed" → CI
red and nobody can apply the schema.

**Mitigation:** plain `CREATE INDEX IF NOT EXISTS` appended to `_DDL_INDEXES`
(prepare.py ~L502, before the closing paren). NO `CONCURRENTLY`. Add an
offline test (extend `test_prepare.py` or wherever DDL is asserted) that the
index DDL string is present in `_ALL_DDL`/`_DDL_INDEXES` and does NOT contain
`CONCURRENTLY`. Document the lock/transaction reasoning in a comment beside the
index.

### R-1326 (MEDIUM) — Is the new index even necessary, and does it match the query's ORDER BY?

The index `(parcel_id, scored_at DESC, score_id DESC)` is precisely the
ordering `DISTINCT ON (parcel_id) ORDER BY parcel_id, scored_at DESC, score_id
DESC` wants, so it lets Postgres satisfy the DISTINCT ON via an index scan
instead of a sort — a legitimate optimization. There is ALREADY
`idx_scores_parcel ON parcel_scores(parcel_id)` (prepare.py L494). The new
composite index supersedes it for this query but the single-column one is
still used elsewhere (e.g. `_SQL_LIST_PARCELS_FOR_SCORING`'s correlated
lookups L729-736, the snapshot fetch L4209). Adding the composite index does
NOT obsolete the existing one for those callers. Net: the new index is
additive, modest storage cost, clear read benefit for the metric. Worth adding
but NOT load-bearing for correctness — the metric is correct without it, just
slower. So if the index addition complicates the commit, it can be deferred
without affecting metric values (it is a pure perf hint).

**Mitigation:** add it (cheap, helps), but Agent 2 should treat it as
optional-for-correctness so a problem with the index does not block the metric
refactor. Document that `DISTINCT ON` direction (DESC) must match the index
direction to be used.

### R-1327 (MEDIUM) — CTE vs correlated subquery: confirm the COUNT/SUM semantics survive

The current `SELECT COUNT(*) FROM parcel_scores ps WHERE <correlated latest +
PASS + threshold>` returns one scalar. The CTE form `WITH latest AS (SELECT
DISTINCT ON (parcel_id) parcel_id, composite_score, confidence_score,
actionability, scored_at FROM parcel_scores ORDER BY parcel_id, scored_at
DESC, score_id DESC) SELECT COUNT(*) FROM latest WHERE actionability='PASS'
AND composite_score >= %s` must project enough columns into `latest` for the
outer filter (actionability, composite_score) and the SUM variant
(confidence_score). Easy to forget `confidence_score` in the CTE projection →
`calculate_confidence_weighted_pipeline` can't `SUM(confidence_score)` →
column-does-not-exist error.

**Failure mode:** the CTE projects only the columns the COUNT function needs;
the SUM function then references `confidence_score` not in `latest` → live CI
error. Or the two functions use DIFFERENT CTEs that drift.

**Mitigation:** ONE shared CTE projecting all needed columns
(parcel_id, composite_score, confidence_score, actionability), used by BOTH
functions (R-1322 recommends `_LATEST_SCORE_CTE`). Offline test asserts both
functions' SQL contains `confidence_score` in the CTE / both reference the
shared constant.

### R-1328 (LOW) — `score_id DESC` semantics: "latest insert wins" — confirm that is the intended tie policy

`score_id SERIAL` is monotonically increasing per insert. `score_id DESC` ⇒
among `scored_at` ties, the LAST-inserted row wins. That matches the intuition
"most recent score" and is consistent with how a re-score should supersede.
The Phase 9 snapshot (`_SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT` L4205-4211) and
`_SQL_LIST_PARCELS_FOR_SCORING` (L733-736) both use `ORDER BY scored_at DESC
LIMIT 1` WITHOUT a `score_id` tie-break — so after item 7 the METRIC picks the
highest-`score_id` row on ties while those two read paths pick an arbitrary
tied row. For perfect cross-consistency they SHOULD also gain `, score_id
DESC`. **However, those are `research.py` reads, NOT the metric, and changing
them is OUT OF SCOPE for item 7 (which is prepare.py-only) and arguably out of
scope for the whole pass.** See R-1329 — flag for the human, do not fix here.

**Mitigation:** none in item 7. Note the inconsistency for a future cleanup.

### R-1329 (MEDIUM) — Cross-file tie-break inconsistency the coordinator's plan did NOT mention

After item 7, three "latest score per parcel" selectors exist with DIFFERENT
tie-break determinism:
- `prepare._LATEST_SCORE_*` (metric): `scored_at DESC, score_id DESC` —
  deterministic (after item 7).
- `research._SQL_LIST_PARCELS_FOR_SCORING` (L733-736): `scored_at DESC LIMIT
  1` — non-deterministic on ties; decides whether a parcel is RE-scored.
- `research._SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT` (L4205-4211): `scored_at
  DESC LIMIT 1` — non-deterministic; decides what the Phase 9 snapshot
  DISPLAYS.

If a parcel ever has tied-`scored_at` rows, the metric could count it based on
row A while the snapshot shows row B and the re-score decision uses row C.
Today this is harmless (ties don't occur in practice per R-1321), but the plan
silently leaves the inconsistency in place while "fixing" only the metric.

**Failure mode:** a future change that DOES create same-microsecond rows
(e.g. batch-inserting scores in one transaction) would make the metric,
snapshot, and re-score logic disagree about which score is "latest."

**Mitigation:** OUT OF SCOPE to fix (those are research.py, and only the
metric is the immutable-layer concern). Agent 2 documents the inconsistency in
the `prepare-mutation:` commit body / response doc as a known follow-up; the
human decides whether a later between-runs change aligns the research.py
selectors. Agent 1 RECOMMENDS aligning them in a SEPARATE future change, not
bundled here (minimal-diff discipline).

---

## 4. Cross-cutting risks the coordinator's plan under-weighted

### R-1330 (HIGH) — The "offline/hermetic" test suite is NOT dependency-free; CI/reviewers see 21 false errors

`python -m unittest discover tests` imports `prepare`, which does `from dotenv
import load_dotenv` at module level (prepare.py L56). With `python-dotenv` not
installed, **21 tests ERROR** (all of `test_prepare.py` plus everything
importing `prepare`), and a reviewer could mistake this for a real regression.
With `psycopg[binary]`, `python-dotenv`, `requests` installed, **all 532 tests
pass** (verified at review time). This matters for item 7 specifically: Agent
3 will run `test_prepare.py` to validate the metric refactor — if their
environment lacks `dotenv`, they will see import errors and might
mis-attribute them.

**Failure mode:** Agent 3 (or CI on a fresh runner) runs the suite without
deps, sees 21 errors, and either (a) wrongly blames item 7, or (b) wastes a
cycle. The suite is "offline" (no network) but not "hermetic" (needs the
three pip deps).

**Mitigation:** Agent 2/3 ensure `pip install -r requirements.txt` precedes
the test run, and the Agent 2 response documents the true baseline as "532
passing WITH requirements installed." If desired (optional, low priority),
note that `prepare.py`'s top-level `load_dotenv` import is what couples the
"offline" suite to a dependency — not something to change in this pass, but
worth recording.

### R-1331 (MEDIUM) — Perf win may under-deliver: S2's un-batched PostGIS query is likely the dominant per-parcel cost

Items 2-4 remove 3 of the 5 per-parcel DB round-trips, but the two REMAINING
un-batched hops are `_fetch_parcel_for_scoring` (`_SQL_FETCH_PARCEL`, a simple
row fetch) and `_compute_s2` (`_SQL_S2_GEOMETRY`, a PostGIS
`ST_MakeValid`/`ST_CollectionExtract`/`ST_Envelope` computation over the
parcel polygon — research.py L705-723, L1792-1796). The geometry op is far
heavier than the three KV-style lookups being batched. So the wall-clock
improvement from items 2-4 may be modest if S2 dominates. This is not a
correctness risk and S2 batching is explicitly OUT OF SCOPE — but the human
should calibrate expectations, and a future perf pass might target S2.

**Mitigation:** none (in scope). Agent 2 response notes the remaining S2
per-parcel cost so the human knows the next perf lever. Do NOT batch S2 in
this pass (scope creep; S2 is per-parcel by nature and batching PostGIS
geometry ops is a larger change).

### R-1332 (MEDIUM) — Minimal-diff / no-opportunistic-refactoring discipline

Hard constraint: minimal diffs, no opportunistic refactoring. Temptations to
resist, for Agent 3 to police:
- Item 7's "factor a shared `_LATEST_SCORE_CTE`" (R-1322) is JUSTIFIED
  refactoring (it keeps the two metric functions in lock-step and simplifies
  the tests) — acceptable because it is in service of the change, not
  unrelated cleanup.
- Aligning the research.py latest-row selectors (R-1329) is OUT OF SCOPE — do
  NOT bundle.
- "While I'm in `score_parcel`, let me also batch S2 / `_SQL_FETCH_PARCEL`" —
  OUT OF SCOPE.
- Renaming `_SQL_LIST_UNSCORED_PARCELS` (the back-compat alias L743) or other
  drive-by renames — OUT OF SCOPE.

**Mitigation:** Agent 3 diffs each commit and rejects unrelated changes.

### R-1333 (HIGH) — Commit structure: items 1-4 in one (or grouped) commit(s), item 7 STRICTLY isolated

Hard requirement: item 7 lands as its OWN commit, message starting
`prepare-mutation:`. Items 1-4 (pure perf/robustness, research.py + tests
only) must be in SEPARATE commit(s) that do NOT touch prepare.py /
parameters.json / sources.json / program.md. If item 7 and items 1-4 share a
commit, the mutation-event boundary is blurred and the "fresh baseline"
implication is no longer cleanly attributable.

**Failure mode:** a single mega-commit touching research.py AND prepare.py →
the run-history invalidation can't be cleanly tied to the prepare.py change →
violates the mutation protocol's intent (AUTORESEARCH_MECHANICS.md L394-399).

**Mitigation:** at least two commits: (1) items 1-4 (research.py + tests),
(2) `prepare-mutation: DISTINCT ON latest-score selection + idx_scores_parcel_scored_at`
(prepare.py + test_prepare.py + any DDL test). Agent 3 verifies
`git diff <items-1-4-commit> -- prepare.py parameters.json sources.json
program.md` is EMPTY, and the item-7 commit touches ONLY prepare.py +
test files. Items 1-4 could be one commit or split per-item; the plan does not
require per-item isolation for the perf items (they are independent and each
must be bit-identical, so bundling is acceptable, though per-item commits ease
review/bisect).

### R-1334 (MEDIUM) — `requirements.txt` / no-new-dependency invariant

Hard constraint: no new external deps (psycopg, python-dotenv, requests only),
no new top-level modules. Item 1's `Retry-After` handling (R-1304) uses only
stdlib (`int(resp.headers.get(...))`). The batch SQL uses only psycopg's
existing list→array adaptation. Nothing here needs a new dep. Agent 3 verifies
`git diff -- requirements.txt` is EMPTY and no new `import` of a third-party
module appears (the only allowed third-party imports are
`psycopg`/`dotenv`/`requests`, already present transitively).

**Mitigation:** Agent 3 greps new imports; confirms `requirements.txt`
unchanged.

### R-1335 (LOW) — `validate-phase1.yml` re-runs prepare.py on item 7 push

Per CLAUDE.md, `.github/workflows/validate-phase1.yml` re-runs `python
prepare.py` against live Supabase on any push touching `prepare.py`. Item 7
touches prepare.py, so this fires. The CLI (`_cli_main` L665-680) calls
`apply_schema` (which now creates the new index — idempotent
`IF NOT EXISTS`, safe) then both metric functions. Against the live DB this is
the only place the DISTINCT ON CTE runs for real in CI. Ensure the live DB is
reachable (needs `DATABASE_URL` secret, per CLAUDE.md). If the index DDL or
the CTE has a syntax error, THIS is the gate that catches it.

**Mitigation:** none beyond awareness. Agent 2 may run `python prepare.py`
locally against a scratch Postgres if available to pre-flight the DDL + CTE.

---

## 5. Go / No-Go Gates for Agent 3

Every gate must be verified TRUE before Agent 3 commits. Tick each explicitly.

**Item 1 (retry):**
1. Retries capped at 2 (3 total attempts); `_DISCOVERY_BACKOFF_SCHEDULE_S =
   (1.0, 2.0)`; constants are module-level; divergence from harness
   `MAX_RETRIES=3` documented. (R-1301)
2. On retry exhaustion, `get()` RE-RAISES the same exception class as today
   (no sentinel) — `TestDiscoveryRetryExhaustionRaises` passes; the existing
   corridor-abort behavior is unchanged. (R-1302)
3. `_spacing_sleep(host)` runs on EVERY attempt; ordering = spacing→request→
   backoff; `TestDiscoveryRetrySpacingPerAttempt` passes. (R-1303)
4. 429 retried (NOT fail-fast); 4xx≠429 fail-fast; matched-pair tests
   `TestDiscoveryRetryOn429` + `TestDiscoveryNoRetryOn404`. `Retry-After`
   honored-with-cap OR documented-ignored. (R-1304)
5. 5xx retried, other 4xx not; status branching on `resp.status_code`;
   `TestDiscoveryRetryOn500`, `TestDiscoveryNoRetryOn403`. (R-1305)
6. NO `import connector_harness` in research.py; NO new top-level module.
   (R-1306)
7. No `print` in the retry path; retry logged via `log`; URL query stripped if
   logged. (R-1308)

**Items 2-4 (batch):**
8. New batch SQL are module-level `_SQL_*` constants with `%s`, using
   `DISTINCT ON` (NOT GROUP BY) for market_context and flagged-block; the
   CoStar-preference CASE + `as_of_date DESC` tail and the land-median filters
   (`comp_type='land'`, `price_per_acre IS NOT NULL`, 36-month window) are
   reproduced EXACTLY. (R-1310)
9. **Bit-identical proof:** a test scores the cycle with cache=None and with
   the prefetch and asserts IDENTICAL parcel_scores (composite, confidence,
   sub_scores, actionability, strategy_fit), research_log, and flagged_items
   rows. (R-1310, R-1319)
10. Cache parameter is keyword-only, defaults `None`, None path = current code
    verbatim; ALL existing Phase 7/8 end-to-end tests pass UNMODIFIED.
    (R-1312)
11. The SECOND `_fetch_actionability_block` caller
    (`run_actionability_screen` L4094) still works; both cached and
    per-parcel paths covered by tests. (R-1320)
12. NULL/empty submarket parcels hit the existing empty-result branch (no
    KeyError, no spurious match); keys are RAW strings (no case/whitespace
    normalization); empty distinct-submarket list short-circuits (no query).
    (R-1315)
13. `ANY(%s)` params passed as `(list,)` (one tuple wrapping the list);
    `TestBatchSqlAnyParamShape` asserts one `%s` per batch constant + the
    one-tuple shape; live-Postgres CI exercises the actual adaptation.
    (R-1316)
14. Prefetch keyed off the SAME `parcel_ids` set the loop iterates, run after
    the collision guard and before the loop. (R-1317)
15. Cache values carry the SAME provenance/staleness/median-count fields the
    per-parcel helpers return today, so flags/notes are unchanged. (R-1319)
16. Actionability-block prefetch proven NOT to capture the cycle's own
    `data_gap` flags; documented that scoring never writes
    `market_context`/`sales_comps`/`actionability_block`. (R-1313)

**Item 7 (prepare-mutation):**
17. New latest-score SQL uses `DISTINCT ON (parcel_id)` with `ORDER BY
    parcel_id, scored_at DESC, score_id DESC` (exact, parcel_id-led). Offline
    assertion on this substring. (R-1321, R-1324)
18. Both metric functions built from ONE shared `_LATEST_SCORE_CTE` projecting
    parcel_id + composite_score + confidence_score + actionability; single
    bound threshold param; no inline threshold. (R-1322, R-1327)
19. The three affected `test_prepare.py` tests
    (`test_where_clause_carries...`, `test_uses_same_where_clause_as_count`,
    threshold-binding) are REWRITTEN to assert the new structure WITHOUT
    dropping their intent (latest-per-parcel; both functions agree; single
    bound param). (R-1322)
20. New index `idx_scores_parcel_scored_at ON parcel_scores(parcel_id,
    scored_at DESC, score_id DESC)` appended to `_DDL_INDEXES` as a PLAIN
    `CREATE INDEX IF NOT EXISTS` — NO `CONCURRENTLY`; offline test asserts
    presence and absence of `CONCURRENTLY`. (R-1325)
21. The stale comment at prepare.py L552-557 is UPDATED to describe the
    implemented refactor (remove "deferred" language). (R-1321)
22. Item 7 is its OWN commit, message starts `prepare-mutation:`, body
    documents the tie-break change, double-count elimination, and the
    fresh-baseline / non-comparable-across-commit run-history implication.
    (R-1321, R-1333)
23. (Recommended) a live-Postgres test inserts two same-`scored_at` PASS rows
    for one parcel + a lower-`scored_at` row and asserts
    `calculate_actionable_pipeline_count == 1` selecting the highest
    `score_id`. (R-1323)

**Cross-cutting:**
24. `git diff <items-1-4-commit(s)> -- prepare.py parameters.json sources.json
    program.md connector_registry.json connector_harness.py requirements.txt`
    is EMPTY. (R-1333, R-1334)
25. The item-7 commit touches ONLY prepare.py + test files (no research.py,
    no parameters.json/sources.json/program.md). (R-1333)
26. `git diff -- requirements.txt` EMPTY; no new third-party imports. (R-1334)
27. Full suite green WITH requirements installed: the pre-existing 532 pass
    plus all new tests (target ~25-40 new across items 1-4-7). Agent 3 runs
    `pip install -r requirements.txt` first to avoid the 21 false dotenv
    errors. (R-1330)
28. AST scanners still green: `test_no_string_interpolated_sql` (every new
    research.py `execute()` first-arg is a Constant/Name/Attribute),
    `test_no_immutable_writes`, `test_no_print_in_run_discovery_cycle`, and the
    Phase 5/6 `...SqlConstantsStaticChecks` (new `_SQL_*` constants have no `{`
    brace). A sister static check should assert the new batch constants are
    parameterised. (R-1310, R-1325)

If any gate is false, Agent 3 returns the PR to Agent 2 with the specific
gate(s) cited.

---

## 6. Open questions for Agent 3 / human

1. **429 `Retry-After` policy (R-1304):** honor-with-cap vs documented-ignore.
   Agent 1 prefers honor-with-cap (~10s ceiling). Human/Agent 3 to confirm.
2. **Item-4 actionability-block tie-break (R-1311):** add `flag_id DESC` to
   the batch query (more deterministic, micro-divergence from the unchanged
   per-parcel query) vs strict parity (no tie-break). Agent 1 leans
   add-`flag_id DESC`-and-document; Agent 3 rules on whether "bit-identical"
   tolerates an unobservable-in-practice difference.
3. **Item-4 inclusion (R-1313):** if Agent 2 cannot cheaply prove scoring never
   writes `actionability_block` rows for the prefetch's validity, is a
   partial win (items 2/3 only, drop item 4) acceptable? Agent 1 says yes —
   correctness over completeness.
4. **research.py latest-row selector alignment (R-1329):** leave the snapshot
   and re-score selectors non-deterministic-on-ties (out of scope) vs a
   separate future change to add `score_id DESC` there too. Agent 1
   recommends a SEPARATE future change; not in this pass.
5. **New index as optional-for-correctness (R-1326):** confirm Agent 3 treats
   the index as a perf hint (deferrable) rather than a correctness gate, so an
   index issue does not block the metric refactor.

---

## 7. Verdict

**GO-WITH-GATES.** All five items are implementable as scoped and the
constraints are satisfiable with minimal diffs. Items 1-4 are genuinely pure
perf/robustness PROVIDED Agent 2 reproduces the delicate SQL ordering
semantics exactly (the CoStar-preference `DISTINCT ON` tail, the land-median
filters, the optional-cache None-path-equals-current-code rule) and proves
bit-identical output via a cache-vs-no-cache equivalence test. Item 7 is a
correctly-identified formal `prepare.py` mutation that changes the metric
DEFINITION (eliminating a latent same-`scored_at` double-count via the
`DISTINCT ON ... score_id DESC` tie-break) and therefore MUST land in its own
`prepare-mutation:` commit with the fresh-baseline run-history implication
documented; it will also break three hard-coded `test_prepare.py` assertions
that Agent 2 must rewrite without weakening the metric's regression guards.

**The single biggest risk is R-1310 / R-1321 in combination: a SQL-ordering
mistake in the item-2 market-context batch (dropping or mis-tailing the
CoStar-preference CASE) or in the item-7 `DISTINCT ON` (a missing
`parcel_id`-led ORDER BY or a dropped `score_id DESC`) would silently change
which row feeds the score/metric — corrupting the Karpathy metric while
masquerading as a performance change, with the fake-cursor offline tests
unable to catch it and only the live-Postgres CI standing in the way.** The
mandatory mitigations: a cache-vs-no-cache bit-identical equivalence test for
items 2-4, an exact-substring assertion on the item-7 ORDER BY, and a
live-Postgres tie-break test for item 7.
