# Phase 10 Code Writer Response — Experiment Loop

**Author:** Agent 2 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Following the
orchestrator-inline three-agent precedent from Phases
2/3/3.1/5/7+8/9 — sub-agent streaming has timed out consistently in
this environment. This response documents the implementation against
the risk catalog at `01_risk_review.md` (R-701..R-733).

**Date:** 2026-05-04.
**Branch:** `claude/setup-research-loop-ZUuA6`.
**Base commit before Phase 10 edits:** `a94050a` (merge of Phase 9
onto main).

---

## 1. What Was Built

A single Phase 10 section appended to `research.py` (≈ 750 lines)
implementing:

### Public API

| Function | Purpose | Risk addressed |
|----------|---------|----------------|
| `evaluate(market, *, skip_*=False)` | One full ingest → discover → score → memo cycle + metric read via `prepare.calculate_*` | R-701, R-702, R-709, R-710, R-711, R-712 |
| `apply_keep_or_revert_decision(...)` | Pure decision function for the Karpathy ratchet | R-713, R-714, R-715 |
| `read_experiment_log(path)` | TSV reader returning list-of-dicts | R-722 |
| `append_experiment_log_row(row, path)` | TSV append-only writer with header bootstrap and atomic single-line append | R-716, R-717, R-718, R-719, R-720, R-721 |
| `verify_setup(market)` | Composite Setup Step 4 verifier (DB / harness / bbox / CoStar) | R-705, R-706, R-707, R-708 |
| `run_baseline_experiment(market)` | Setup Step 5 — runs `evaluate` once on unmodified research.py and writes the first TSV row | (Setup Phase) |
| `experiment_loop(market, ...)` | The NEVER STOP loop with halt sentinels, advisory locking, crash isolation, and infrastructure-failure circuit breaker | R-723, R-724, R-725, R-726, R-727, R-728, R-729, R-731, R-732, R-733 |

### Private helpers

`_git_current_branch`, `_git_head_commit`, `_parse_tag_from_branch`,
`_assert_autoresearch_branch`, `_experiment_log_path`,
`_sanitize_description`, `_validate_log_row`,
`_check_db_connection`, `_check_corridor_bbox`,
`_check_costar_freshness`, `_check_harness_for_market`,
`_last_baseline_or_keep`, `_halted`, `_loop_lock_path`,
`_acquire_loop_lock`, `_record_halt_row`, `_format_loop_description`.

### Exceptions

`SetupError` (R-703, R-705) and `LoopLockError` (R-729) are new
public exceptions raised when preconditions fail.

### Imports added

`math`, `subprocess`. `fcntl` is imported lazily inside
`_acquire_loop_lock` to keep the module Windows-importable
(advisory lock degrades to a no-op on non-POSIX, with a warning).

---

## 2. Risk Address Map

Every R-7XX risk from the risk review is mitigated in code OR
explicitly accepted with rationale. Spot-checks below; Agent 3
should walk the full list.

### Five-File Contract (R-701, R-702 — CRITICAL)

`evaluate()` calls `prepare.calculate_actionable_pipeline_count(conn)`
and `prepare.calculate_confidence_weighted_pipeline(conn)` exactly
once each, inside a single `with prepare.get_connection() as conn`
block. No reimplemented metric SQL. No mid-loop `parameters.json`
reload — `prepare.verify_parameters_unchanged()` is the only
parameters touchpoint per iteration, and it runs ONCE at the start
of `evaluate`.

Test: `TestPhase10EvaluateMetricRouting.test_metric_value_comes_from_prepare`
monkey-patches `prepare.calculate_actionable_pipeline_count` to a
sentinel value and confirms `evaluate()` surfaces it verbatim.

### Branch invariant (R-703, R-704 — HIGH)

`_assert_autoresearch_branch()` and `verify_setup()` both use
`_git_current_branch()` which calls `git rev-parse --abbrev-ref HEAD`
via `subprocess.run` with explicit `cwd`, `text=True`, `check=True`,
and a 10-second timeout. No shell, no porcelain parsing.
`SetupError` raised with an actionable message.

Tests: `TestPhase10AssertAutoresearchBranch` (4 cases — main, dev,
detached HEAD, valid).

### Setup phase (R-705, R-706, R-707, R-708 — MEDIUM/HIGH)

`verify_setup` is idempotent (no side effects beyond a DB ping). The
overall status aggregates per-check statuses with the rule:
`fail` ∈ statuses → `fail`; else `warning` ∈ statuses → `warning`;
else `ok`. Non-autoresearch branch is a hard `fail`.

CoStar staleness is **soft** (`status='warning'` even when
`fresh_files=0`) — the strategy memo flags the gap per
COSTAR_INGESTION_CONTRACT.md. The corridor bbox check is also
**soft** in setup but produces null sub-scores at scoring time,
which the agent will see as a degraded metric and surface in the
strategy memo.

Tests: `TestPhase10VerifySetupComposite` (4 cases — fail / ok /
warning / db-fail-skips-bbox).

### Evaluator semantics (R-709 .. R-712 — HIGH/MEDIUM)

`evaluate()` runs sub-cycles in the order specified by the risk
review. Each sub-cycle uses its own connection per existing
contract; R-710 documented in the docstring. The metric read uses
ONE connection. `BudgetExceeded` is caught and surfaced as
`status=timeout`. Generic exceptions are caught and surfaced as
`status=crash` with the exception type and message in `error`.

`api_calls=0` is a documented placeholder (R-712).

Tests: `TestPhase10EvaluateMetricRouting.test_evaluate_calls_sub_cycles_in_order`,
`test_evaluate_catches_crash_and_returns_status`,
`test_evaluate_catches_budget_exceeded`,
`test_evaluate_calls_verify_parameters_unchanged`.

### Decision logic (R-713, R-714, R-715 — CRITICAL/HIGH/MEDIUM)

`apply_keep_or_revert_decision` is a pure function. Every cell of
the §6 matrix has a unit test:

- crash → crash
- timeout → timeout
- prior=None → baseline
- new>prior → keep
- new<prior → discard
- new==prior, new_conf > prior_conf → keep
- new==prior, new_conf == prior_conf → discard (R-714 simplicity)
- new==prior, new_conf < prior_conf → discard
- new==prior, new_conf ≈ prior_conf within ULP → discard (R-715)
- bogus status → ValueError

Tests: `TestPhase10DecisionMatrix` (10 cases).

### TSV schema and append semantics (R-716 .. R-722 — CRITICAL/HIGH/MEDIUM)

`append_experiment_log_row` opens the file with
`os.open(..., O_WRONLY | O_CREAT | O_APPEND, 0o644)`. The header is
bootstrapped on first write (file missing OR file size zero).
`os.fsync` is called before close. Single-line appends (header +
row, ≤ 4096B) are atomic on Linux per PIPE_BUF.

`_validate_log_row` rejects:

- non-int / boolean / negative `metric`
- non-finite / negative `confidence`
- non-int / boolean / negative `api_calls`
- non-finite / negative `wall_clock_min`
- status outside the canonical set
- `commit` not matching `^([0-9a-f]{7,40}|pending)$` (rejects empty,
  uppercase, special chars, short SHA)

`_sanitize_description` collapses `\t \r \n \x00` and arbitrary
whitespace runs to a single space, and truncates to 200 chars
with a trailing `…` indicator.

Tests:
- `TestPhase10TsvSchemaValidation` (14 cases).
- `TestPhase10DescriptionSanitization` (8 cases).
- `TestPhase10TsvHeaderBootstrap` (3 cases).
- `TestPhase10TsvAppendOnly` (3 cases).
- `TestPhase10TsvCommitFormat` (4 cases).

### Loop driver (R-723 .. R-732 — CRITICAL/HIGH/MEDIUM)

**R-723 (CRITICAL) — no git mutation from Python.** `experiment_loop`
contains zero `git reset`, `git commit`, `git push`, or any other
git-mutating subprocess call. The only `subprocess.run` calls are
`git rev-parse --abbrev-ref HEAD` (read-only) and
`git rev-parse --short=7 HEAD` (read-only). The loop appends a
DECISION to the TSV; the agent (Claude Code) reads the TSV after
each iteration and performs the corresponding git operation in its
own tool calls. The loop docstring is explicit.

Tests: `TestPhase10ExperimentLoopReadOnlyVsImmutables.test_no_writes_to_immutable_layer`
(static check on research.py source for forbidden write modes on
`_PARAMETERS_PATH` / `_SOURCES_PATH`).

**R-725, R-728 (MEDIUM) — halt sentinel.** `_halted()` checks
`_HALT_SENTINEL_PATH` and `EXPERIMENT_LOOP_HALT` env var. Either
mechanism stops the loop on the next iteration boundary. A
synthetic `status=halt` row is appended to the TSV for accounting.

Tests: `TestPhase10HaltDetection` (3 cases).

**R-726 (MEDIUM) — crash isolation.** The loop body wraps `evaluate`
in a try/except that synthesizes a crash result row if anything
escapes the evaluator's own exception handling. Loop continues to
the next iteration.

Tests: `TestPhase10ExperimentLoopIterations.test_crash_isolated_loop_continues`.

**R-729 (HIGH) — advisory lock.** `_acquire_loop_lock` uses
`fcntl.LOCK_EX | fcntl.LOCK_NB` so a second invocation raises
`LoopLockError` immediately. PID is written to the lockfile for
forensic ergonomics. Lock releases on context exit.

Tests: `TestPhase10ExperimentLoopAdvisoryLock` (2 cases).

**R-731 (HIGH) — infra-failure circuit breaker.** Three consecutive
`verify_setup` failures trigger a clean exit with a synthetic
`status=halt` row carrying the reason. Backoff between retries is
proportional to consecutive failures, capped at 5 minutes.

**R-732 (MEDIUM) — graceful 7-day exit.** Loop checks elapsed wall
clock against `_LONG_RUN_GRACEFUL_EXIT_SECONDS` between iterations.

**R-733 (HIGH, partially accepted) — soft per-iteration budget.**
The loop measures `wall_clock_min` per iteration and promotes any
`status=ok` result that ran > 90 minutes to `status=timeout`. OS-
level enforcement requires running the evaluator as a subprocess
wrapped by `prepare.run_with_os_timeout`. This is documented in
the `experiment_loop` docstring as a future ergonomic.

### Halt accounting (synthetic row)

`_record_halt_row` appends a `status=halt` row with `metric=0,
confidence=0.0` and a description carrying the halt reason
(sentinel detected, graceful 7-day, infrastructure failure x3).
This row is NOT a Karpathy-spec status — the spec lists five
statuses (baseline, keep, discard, crash, timeout). Adding `halt`
as a sixth status preserves an audit trail of why the loop exited
without polluting the keep/discard decision history. The decision
function is unaware of `halt` — it cannot be returned as a
decision.

This is a Phase 10 extension, documented in
`TestPhase10ConstantsContract.test_status_set_matches_spec`.

---

## 3. Decisions (D1 .. D10)

### D1 — `experiment_loop()` does not call git, by design

Per R-723. The agent (Claude Code) is the hypothesis driver; Phase
10 provides the evaluator and TSV. Auto-reverting from a long-
running Python loop fights the agent's own git operations and is a
durability footgun.

### D2 — `evaluate()` is non-transactional across sub-cycles

Per R-710. Each existing public-API helper (run_ingestion_cycle,
run_discovery_cycle, run_scoring_cycle, generate_strategy_memo)
opens its own connection. Refactoring to a single transaction
across all four would require touching the existing helpers and
risking regressions in Phases 6, 3, 5, 7+8, 9. Out of scope for
Phase 10. The doc string documents the trade-off.

### D3 — Memo failure is non-fatal

If `generate_strategy_memo` raises, `evaluate()` logs and continues
to the metric read. The metric is still readable from the database
even if the markdown generator hit a snag. This avoids losing a
genuine metric movement to a memo-rendering bug.

### D4 — `api_calls` is a placeholder column

Per R-712. Counting external API calls accurately requires
threading a counter through `_DiscoverySession` + every CoStar
loader. Doable, but adds bookkeeping for limited current value.
Phase 11+ can populate it.

### D5 — `halt` is a Phase 10 status extension

Karpathy's spec lists five statuses. Phase 10 adds `halt` as a
sixth for auditing why the loop exited. The decision function
never returns `halt`; it is only emitted by `_record_halt_row`.
The TSV reader and the constants test both accept the extended set.

### D6 — Description max length is 200 chars with `…` truncation

The Karpathy spec says "no tabs, no commas". We allow commas (TSV,
not CSV) but cap at 200 chars to keep grep / `tail` of the TSV
readable. The trailing `…` indicates truncation.

### D7 — Float comparison uses `math.isclose` with 1e-9 tolerance

Per R-715. Without this, two confidence values that arithmetic to
the "same" logical number but differ in the last ULP would be
treated as a "tie won by the new value" and incorrectly kept.
The tolerance matches Python's default for `isclose`.

### D8 — `_LONG_RUN_GRACEFUL_EXIT_SECONDS` is 7 days

AUTORESEARCH_MECHANICS.md L342 explicitly authorizes a graceful
7-day cap. Hard-coded as a constant; can be adjusted between runs
if needed.

### D9 — The lock fallback on Windows is a no-op with a warning

`fcntl` is POSIX-only. On Windows, the lock degrades to a no-op
and a single log warning is emitted. Concurrent loops on Windows
must be prevented by the human. This is acceptable because the
target deployment (Codespaces, Linux servers, the human's Mac/
Linux laptop) is POSIX.

### D10 — The Phase 1 status banner is updated to "Phase 10"

`_print_phase10_status` replaces `_print_phase1_status` as the
`if __name__ == "__main__"` body so a quick `python research.py`
shows the agent's current build state.

---

## 4. Test Coverage

497 tests total (415 prior + 82 new). All pass.

New Phase 10 test classes:

| Class | Tests | Risk(s) |
|-------|-------|---------|
| `TestPhase10TsvSchemaValidation` | 14 | R-719 |
| `TestPhase10DescriptionSanitization` | 8 | R-718 |
| `TestPhase10TsvHeaderBootstrap` | 3 | R-717 |
| `TestPhase10TsvAppendOnly` | 3 | R-716, R-720, R-722 |
| `TestPhase10DecisionMatrix` | 10 | R-713, R-714, R-715 |
| `TestPhase10ParseTagFromBranch` | 5 | R-704 |
| `TestPhase10HaltDetection` | 3 | R-725, R-728 |
| `TestPhase10LastBaselineOrKeep` | 4 | (decision input) |
| `TestPhase10AssertAutoresearchBranch` | 4 | R-703 |
| `TestPhase10VerifySetupComposite` | 4 | R-705..R-708 |
| `TestPhase10EvaluateMetricRouting` | 5 | R-701, R-702, R-709 |
| `TestPhase10ExperimentLoopBaselineBootstrap` | 2 | (Setup Step 5/6) |
| `TestPhase10ExperimentLoopIterations` | 4 | R-723..R-728 |
| `TestPhase10ExperimentLoopAdvisoryLock` | 2 | R-729 |
| `TestPhase10ExperimentLoopReadOnlyVsImmutables` | 2 | G2/G6/G11 |
| `TestPhase10TsvCommitFormat` | 4 | R-719 |
| `TestPhase10ConstantsContract` | 5 | G1/G3 |
| **Total** | **82** | |

Existing 415 tests: still pass.

---

## 5. Files Mutated vs Base

| File | Lines added | Lines removed |
|------|-------------|---------------|
| `research.py` | ≈ 750 | 30 (the stub block + Phase 1 banner) |
| `tests/test_discovery.py` | ≈ 720 | 0 |
| `reviews/12_phase10_experiment_loop/01_risk_review.md` | NEW | — |
| `reviews/12_phase10_experiment_loop/02_code_writer_response.md` | NEW (this file) | — |
| `reviews/12_phase10_experiment_loop/03_reviewer_decision.md` | NEW (Agent 3 writes) | — |

`prepare.py`, `parameters.json`, `sources.json`, `program.md`,
`connector_harness.py`, `connector_registry.json`,
`requirements.txt`, `.gitignore` are bytes-identical to `a94050a`.
Agent 3 verifies as Gate 1.

---

## 6. Open Items for Agent 3

1. **G12 — line count delta**: Phase 10 adds ≈ 750 LOC to
   research.py against the risk review's 600-1200 budget. Within
   the soft ceiling.
2. **R-733 partial acceptance**: the soft per-iteration budget
   instead of an OS-enforced subprocess wrapper. Documented.
3. **D5 — `halt` status extension**: not in the original Karpathy
   spec; argue acceptance in §3 above.
4. **D2 — non-transactional `evaluate`**: cross-cycle atomicity is
   a known limitation; documented in the docstring.
5. **D4 — `api_calls=0` placeholder**: the column is wired but
   always zero. Phase 11+ task.

Agent 3 may surface additional gaps; this list is the writer's
self-audit.
