# Phase 10 Risk and Architecture Review — Experiment Loop and Setup Phase

**Reviewer:** Agent 1 role, completed by orchestrator (Claude Code main
session) under explicit human authorization ("Build phase 10",
2026-05-04). Following the established orchestrator-inline precedent
from Phases 2/3/3.1/5/7+8/9 — sub-agent streaming has timed out
consistently in this environment, so the orchestrator authors all
three role documents and a future session with working sub-agent
streaming should ratify them with full context independence.

**Date:** 2026-05-04.
**Branch:** `claude/setup-research-loop-ZUuA6`.
**Base commit:** `a94050a` (merge of Phase 9 onto main).
**Scope:** BUILD_PHASES.md Phase 10 — replace the
`NotImplementedError` stub at `research.py:5324` with a working
experiment loop, plus the supporting setup-phase, evaluator,
keep-or-revert decision, and `experiment_log.tsv` I/O per
AUTORESEARCH_MECHANICS.md "The Setup Phase" + "The Experiment Loop" +
"The Git Ratchet" + "The Experiment Log".

---

## 1. The Five-File Contract

Phase 10 is the loop runtime. It edits ONLY `research.py`,
`tests/test_discovery.py`, and authoring of three role documents in
`reviews/12_phase10_experiment_loop/`. The metric layer (`prepare.py`)
and the configuration layer (`parameters.json`, `sources.json`) stay
bytes-identical to `a94050a`. `program.md` is read-only.

`.gitignore` already contains `experiment_log.tsv` (line 60) so no
gitignore mutation is needed.

**Hard rule for Agent 2**: every diff against `a94050a` for
`prepare.py`, `parameters.json`, `sources.json`, `program.md`,
`connector_harness.py`, `connector_registry.json`, `requirements.txt`
MUST be empty. Allowed mutations:

- `research.py` — append a Phase 10 section with public + private helpers
- `tests/test_discovery.py` — append new test classes
- `reviews/12_phase10_experiment_loop/` — three role documents

Agent 3 verifies as Gate 1.

---

## 2. The Metric Contract — UNCHANGED in Phase 10

`prepare.calculate_actionable_pipeline_count` (prepare.py:569) and
`prepare.calculate_confidence_weighted_pipeline` (prepare.py:586) are
the GROUND TRUTH for the Karpathy metric. Phase 10 callers MUST
invoke them via `prepare.*` symbols and MUST NOT reimplement the
WHERE clause, the latest-score selector, or the threshold lookup. The
threshold value comes from the frozen `prepare._PARAMETERS` dict, not
from re-parsing `parameters.json`.

**R-701 (CRITICAL) — The evaluator calls `prepare.calculate_*`
verbatim.** No reimplemented metric. No mid-loop parameters reload.
Agent 3 verifies via grep: every metric read in the Phase 10 diff
goes through `prepare.calculate_actionable_pipeline_count` or
`prepare.calculate_confidence_weighted_pipeline`. No new SQL with
`actionability` + `composite_score` predicates outside `prepare.py`.

**R-702 (CRITICAL) — `prepare.verify_parameters_unchanged()` runs
once per evaluation cycle.** The SHA-256 sentinel guards against
on-disk drift between iterations. The loop calls the sentinel at the
start of each iteration before `evaluate()` runs.

---

## 3. The Branch Invariant

AUTORESEARCH_MECHANICS.md "The Git Ratchet" requires that
experiments commit on `autoresearch/<tag>` and that `main` stays
clean during a run. The setup phase requires the branch be cut
from `main` BEFORE the loop starts. Phase 10 does not enforce this
at the OS level (no chroot, no permissions), but it does enforce it
at the loop boundary.

**R-703 (HIGH) — `verify_setup` and `experiment_loop` refuse to
proceed on a non-autoresearch branch.** The branch name must match
`^autoresearch/[a-z0-9._-]+$`. Any other branch (`main`, dev
branches, detached HEAD) raises `SetupError` with a message
explaining the AUTORESEARCH_MECHANICS.md branch rule. The agent
running `experiment_loop()` from a dev branch (this current branch
included) gets a clear refusal instead of a silent no-op or
half-baked experiment.

**R-704 (MEDIUM) — Branch detection uses git plumbing, not parsing
porcelain output.** Use `subprocess.run(["git", "rev-parse",
"--abbrev-ref", "HEAD"], ...)` and `subprocess.run(["git",
"symbolic-ref", "-q", "HEAD"], ...)`. `git status` output is
porcelain-dependent and brittle.

---

## 4. The Setup Phase — Discrete, Idempotent, Confirmation-Gated

AUTORESEARCH_MECHANICS.md "Setup Sequence" lists six steps. Phase 10
implements the parts that can be programmatic. Steps 1, 2, 6 are
human-side (tag agreement, branch creation, confirmation); the code
verifies + reports state.

| Step | Programmatic? | Phase 10 deliverable |
|------|---------------|----------------------|
| 1. Agree on a run tag | No (human) | `verify_setup` reads the branch, parses `<tag>` from `autoresearch/<tag>` |
| 2. Create the branch | No (human) | refuse to proceed if not on autoresearch/* |
| 3. Read in-scope files | Implicit | the agent (Claude Code) does this via the orientation chain |
| 4. Verify infrastructure | YES | `verify_setup` runs DB ping + `prepare.verify_parameters_unchanged` + harness gate + corridor-bbox seed check + CoStar staleness check (soft) |
| 5. Establish baseline | YES | `run_baseline_experiment` invokes `evaluate()` against unmodified research.py and writes the first TSV row with `status=baseline` |
| 6. Confirm with human | YES | `experiment_loop` requires either a `--confirmed` CLI flag OR a non-empty `experiment_log.tsv` with a `baseline` row — refuses to spin up otherwise |

**R-705 (HIGH) — Setup phase code is idempotent.** Running
`verify_setup(market)` twice on a healthy environment returns the
same shape. Running `run_baseline_experiment` twice on a branch that
already has a baseline row appends a NEW row (not a duplicate) so
the agent can re-baseline if `prepare.py` mutated between runs.
That said, the loop driver checks for an existing baseline row
before calling `run_baseline_experiment`.

**R-706 (MEDIUM) — The CoStar staleness check is informational.**
Per AUTORESEARCH_MECHANICS.md "Setup Sequence" and
COSTAR_INGESTION_CONTRACT.md "What Happens If an Export Is Late or
Missing", the agent can baseline with stale or missing CoStar data;
the strategy memo flags it. So this gate emits a warning, not an
error.

**R-707 (MEDIUM) — The corridor-bbox check is informational at
setup but causes scoring sub-scores to null out if missing.** The
spec defers corridor bbox seeding to Phase 3 / Phase 11 county
expansion. Phase 10 just reports state; it does not seed bboxes.

**R-708 (HIGH) — The harness gate calls
`connector_harness.run_harness_for_county` exactly the same way
`run_discovery_cycle` does (research.py:1547). No second
implementation. A `failing` harness blocks the loop; `degraded`
proceeds with a warning logged to the strategy memo.

---

## 5. The Evaluator — Single Function, Single Cycle

`evaluate(market)` is the equivalent of Karpathy's `evaluate.py`.
It runs ONE complete cycle of the existing pipeline (Phases 6 → 3 →
5/7 → 9) and computes the metric. It does NOT modify `research.py`,
does NOT make git commits, does NOT decide keep-or-revert. The
caller (the loop driver) handles those. This separation is the
basis for testability: `evaluate()` is a pure-data function; the
loop driver is the side-effect orchestrator.

Order of operations inside `evaluate(market)`:

1. `prepare.verify_parameters_unchanged()` — fail-loud if
   `parameters.json` drifted on disk mid-run.
2. `run_ingestion_cycle()` — pulls in any new CoStar exports
   dropped since the last cycle. R-322 already documents that this
   is non-destructive (defers unimplemented loaders).
3. `run_discovery_cycle(market)` — Phase 3 Fulton-only for now.
4. `run_scoring_cycle(market)` — Phase 5/7. Each parcel gets a new
   `parcel_scores` row APPENDED so
   `calculate_actionable_pipeline_count`'s MAX(scored_at) selector
   sees the freshest verdict.
5. `generate_strategy_memo(market, today=...)` — Phase 9 deterministic
   markdown. Snapshots are emitted by Phase 9 callers as needed.
6. Compute metric: open one connection, call
   `prepare.calculate_actionable_pipeline_count(conn)` and
   `prepare.calculate_confidence_weighted_pipeline(conn)`.
7. Return a dict carrying both metrics, the cycle ids of each
   sub-cycle, the wall-clock delta, and the memo path.

**R-709 (HIGH) — `evaluate()` is wrapped in a wall-clock measurement
but does NOT install the SIGALRM handler from
`prepare.wall_clock_budget`.** The OS-level timeout is enforced by
the LOOP DRIVER subprocess-launching the per-experiment evaluator
through `prepare.run_with_os_timeout`. SIGALRM inside the same
process as the loop conflicts with the harness HTTP code that
expects unblocked signals. Defer SIGALRM to a future phase.

**R-710 (HIGH) — `evaluate()` is not transactional across
sub-cycles.** Discovery commits before scoring runs. This is by
design — the existing helpers each open + close their own
connection. If discovery succeeds and scoring crashes, the parcels
in the database are real; the next iteration will re-score them.
Document this in the docstring; do not refactor to a single
transaction (too risky and outside Phase 10 scope).

**R-711 (MEDIUM) — `evaluate()` records `wall_clock_min` from
`time.monotonic()`.** Wall-clock time, not CPU time. Aligns with
AUTORESEARCH_MECHANICS.md "Time Budget" semantics.

**R-712 (MEDIUM) — `evaluate()` does NOT count API calls into
external services.** API call counting was Karpathy's
budget-control mechanism for GPU runs; for our domain it is a soft
SLA tracker, not a metric. Phase 10 records `api_calls=0` as a
placeholder; Phase 11+ can wire `_DiscoverySession` request counts
through the return value if the human wants the column populated.

---

## 6. The Keep-or-Revert Decision

The decision logic from AUTORESEARCH_MECHANICS.md "The Keep-or-Revert
Decision" (lines 247-269) is implemented as a PURE FUNCTION that
the caller invokes after `evaluate()`. The function returns one of
`"keep"`, `"discard"`, `"crash"`, `"timeout"`, `"baseline"`. The
caller is responsible for the corresponding git operation.

Decision matrix:

| Status from evaluator | Prior metric vs new | Decision |
|-----------------------|---------------------|----------|
| `crash` | n/a | `crash` |
| `timeout` | n/a | `timeout` |
| `ok` AND first row | n/a | `baseline` |
| `ok` AND new > prior | strictly improved | `keep` |
| `ok` AND new == prior AND new_conf > prior_conf | tie, confidence wins | `keep` |
| `ok` AND new == prior AND new_conf <= prior_conf | tie, simplicity revert | `discard` |
| `ok` AND new < prior | regression | `discard` |

**R-713 (CRITICAL) — Decision function is pure, deterministic,
side-effect-free, and unit-tested for every cell of the matrix.**
The TSV writer and git operations are SEPARATE callers; the
decision logic is invariant to them.

**R-714 (HIGH) — Confidence as tiebreaker uses strict `>`.** Equal
confidence on a tied metric is a `discard` per Karpathy's
simplicity criterion (AUTORESEARCH_MECHANICS.md L271-280): "removing
something and getting equal or better results is a great outcome".
A change that produces an EQUAL metric AND EQUAL confidence has not
demonstrated value and reverts. Tested.

**R-715 (MEDIUM) — Float comparison on confidence uses an
explicit tolerance.** `math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)`
for the equality check. Without this, a confidence value that
re-arithmeticked to the same logical value but differs in the last
ULP would be treated as a "tie won by the new value" and
incorrectly kept.

---

## 7. The Experiment Log

AUTORESEARCH_MECHANICS.md "The Experiment Log: experiment_log.tsv"
specifies a 7-column TSV at the repo root, gitignored, with this
schema:

```
commit	metric	confidence	api_calls	wall_clock_min	status	description
```

**R-716 (CRITICAL) — TSV is APPEND-ONLY.** The Phase 10 writer
opens with mode `"a"`, never `"w"`. The reader does NOT load and
re-write. Loss of historical rows would invalidate the entire
experimental record.

**R-717 (CRITICAL) — TSV header is bootstrapped on first write.**
If the file does not exist OR is empty, the writer emits the
header before the first data row. The reader skips a leading row
that exactly matches the header tuple.

**R-718 (HIGH) — Description sanitization.** Per
AUTORESEARCH_MECHANICS.md L309-310: "no tabs, no commas in
description". Phase 10 strips/replaces tabs with single spaces and
truncates to 200 chars. Newlines collapsed to single space. The
TSV format is line-delimited, so embedded newlines would corrupt
the parser.

**R-719 (HIGH) — Schema-validated rows.** The writer rejects rows
that:
- have non-int `metric`
- have non-finite or negative `confidence`
- have non-int `api_calls`
- have negative `wall_clock_min`
- have a `status` outside `{baseline, keep, discard, crash, timeout}`
- have a `commit` that does not match `^[0-9a-f]{7,40}$|^pending$`

**R-720 (MEDIUM) — Cross-run accumulation by append.**
AUTORESEARCH_MECHANICS.md "Cross-Run Aggregation" requires that
new-run rows append to the existing file. Phase 10's writer
satisfies this trivially because it never truncates. Each run's
first row has `status=baseline`, so a parser can split runs by
walking baseline boundaries.

**R-721 (MEDIUM) — Atomic append.** Each row is written with a
single `write()` call ending in `\n` and `flush()` + `os.fsync()`.
On Linux, append + fsync of a single line ≤ PIPE_BUF (4096
bytes) is atomic. Tested.

**R-722 (LOW) — Reader returns dicts, not tuples.** Tests and
future analysis code want named field access. The reader yields a
list of `dict[str, str]` keyed by column name; numeric coercion is
the caller's job.

---

## 8. The Loop Driver

`experiment_loop(market, *, max_iterations=None)` is the actual
NEVER STOP runner. Default `max_iterations=None` runs until halted
externally; tests pass small integers to bound the runtime.

Pseudocode:

```
def experiment_loop(market, *, max_iterations=None):
    setup = verify_setup(market)
    if setup["status"] != "ok":
        raise SetupError(setup["details"])

    log_path = _experiment_log_path()
    rows = read_experiment_log(log_path)
    if not any(r["status"] == "baseline" for r in rows):
        baseline_row = run_baseline_experiment(market)
        append_experiment_log_row(baseline_row, log_path)
        rows = read_experiment_log(log_path)

    while not _halted() and (max_iterations is None or iters < max_iterations):
        prior = _last_keep_or_baseline(rows)
        new_row = evaluate_for_loop(market)
        decision = apply_keep_or_revert_decision(
            prior_metric=int(prior["metric"]),
            prior_confidence=float(prior["confidence"]),
            new_metric=new_row["metric"],
            new_confidence=new_row["confidence"],
            status=new_row["status"],
        )
        new_row["status"] = decision
        append_experiment_log_row(new_row, log_path)
        rows.append(new_row)
        iters += 1
```

**R-723 (CRITICAL) — The loop body NEVER calls `git reset
--hard HEAD~1` from Python in this phase.** Karpathy's pattern has
the AGENT (Claude Code) modify research.py + commit + invoke
evaluate + read result + decide + revert if needed. Phase 10
provides the helpers; the agent invokes them. Auto-reverting from
a long-running Python loop is a footgun (loses agent state, fights
the agent's own git operations, can revert a commit the agent
hasn't yet finished writing). The loop driver in Phase 10
appends the decision to the TSV; the agent reads the TSV after
each iteration and performs the git operation in its tool calls.
The docstring of `experiment_loop` is explicit about this contract.

**R-724 (HIGH) — Wall-clock budget per iteration.** Each iteration
is wrapped in a try/except that catches `prepare.BudgetExceeded`
and treats it as `status=timeout`. The actual OS-level enforcement
is provided by `prepare.run_with_os_timeout`; the loop driver
launches the evaluator via that helper when invoked from a CLI
subprocess (not within the same Python process). For in-process
testing, the loop's wall-clock check is best-effort.

**R-725 (HIGH) — Halt detection.** The loop checks for the
existence of a sentinel file `_REPO_ROOT / ".halt"` between
iterations. If present, the loop exits cleanly after writing a
final memo and a `status=halt` log row. Avoids relying on signal
handlers that conflict with subprocess semantics.

**R-726 (MEDIUM) — Iteration exception isolation.** A crash inside
`evaluate()` is caught at the loop boundary, logged as
`status=crash`, written to the TSV, and the loop continues.
Karpathy's spec L380-385 says ~3 fix attempts before giving up;
Phase 10 does not implement auto-fix attempts (that is the agent's
job) — it just records the crash and proceeds.

**R-727 (MEDIUM) — Logging granularity.** Every evaluation
emits a structured log line at the start (`evaluate.start`) and
end (`evaluate.end`). The human watching the loop tail logs gets
clean per-iteration boundaries. No emojis.

**R-728 (LOW) — `_halted()` polls `.halt` plus an env var
override (`EXPERIMENT_LOOP_HALT=1`) for ergonomics.** Either
mechanism stops the loop on the next iteration boundary.

---

## 9. Concurrency, Locking, and Re-entrance

The loop runner is single-process by design. Two concurrent
`experiment_loop()` invocations against the same database would
race on the parcel table and corrupt the metric.

**R-729 (HIGH) — Advisory file lock.** The loop driver acquires an
exclusive `fcntl.flock` on `_REPO_ROOT / ".experiment_loop.lock"`
for the duration of the run. A second invocation fails with a
clear "loop already running on this checkout" message. Lock is
released on normal exit or process death (kernel guarantees this).

**R-730 (MEDIUM) — Tests that exercise the loop set
`EXPERIMENT_LOOP_LOCK_PATH` to a tempdir-scoped path so concurrent
test runs do not contend for the real lock.**

---

## 10. Catastrophe and Recovery

AUTORESEARCH_MECHANICS.md "What 'Manually Halted' Means" (L340-345)
lists three exit conditions. Phase 10 honors all three.

**R-731 (HIGH) — Catastrophic infrastructure failure detection.**
If `verify_setup` fails THREE iterations in a row (e.g., DB
unreachable for >1 hour given typical iteration cadence), the loop
exits with `status=infra_failure` rather than spinning forever. The
threshold is conservative — three consecutive failures across at
least 60 minutes wall-clock.

**R-732 (MEDIUM) — Long-run graceful conclusion.** If the wall-
clock total since loop start exceeds 7 days, the loop exits cleanly
after the current iteration. AUTORESEARCH_MECHANICS.md L342-343
explicitly authorizes this. No timer threads; the loop just
checks `time.time() - _started_at` between iterations.

---

## 11. Tests Required (≥ 25, must pass alongside the existing 367)

Required test classes:

1. `TestPhase10TsvSchemaValidation` — every R-719 rejection.
2. `TestPhase10TsvHeaderBootstrap` — empty file, missing file, header-already-present.
3. `TestPhase10TsvAppendOnly` — multi-row append preserves prior rows; reader yields all rows in order.
4. `TestPhase10DescriptionSanitization` — tabs, newlines, commas, length cap.
5. `TestPhase10DecisionMatrix` — all seven cells of §6, including the float-tolerance edge cases.
6. `TestPhase10VerifySetupBranchCheck` — autoresearch/foo passes; main, dev/foo, detached HEAD fail.
7. `TestPhase10VerifySetupHarnessGate` — failing harness blocks; degraded warns; healthy passes.
8. `TestPhase10EvaluateOrchestration` — `evaluate()` calls ingest → discovery → scoring → memo in order, then reads metric. Uses Phase5FakeConnection.
9. `TestPhase10EvaluateMetricRouting` — metric values come from `prepare.calculate_*` symbols, not reimplementation. Test by monkey-patching `prepare.calculate_actionable_pipeline_count` to a sentinel value and confirming the evaluator returns it.
10. `TestPhase10ExperimentLoopBaselineBootstrap` — loop on empty TSV writes a baseline row first.
11. `TestPhase10ExperimentLoopHalt` — `.halt` file or env var halts the loop after the current iteration.
12. `TestPhase10ExperimentLoopMaxIterations` — `max_iterations=N` exits after N iterations regardless of halt sentinel.
13. `TestPhase10ExperimentLoopAdvisoryLock` — second concurrent invocation fails with the lock error.
14. `TestPhase10ExperimentLoopCrashIsolation` — a crash inside `evaluate()` writes a `status=crash` row and the loop continues to the next iteration.

Target: 30+ new tests. Existing 367 must still pass.

---

## 12. Setup Phase Code Mapping to AUTORESEARCH_MECHANICS.md

| AUTORESEARCH spec line | Phase 10 helper |
|------------------------|-----------------|
| L96-L97 (Step 1: tag) | `_parse_tag_from_branch(name)` |
| L98 (Step 2: branch) | `_assert_autoresearch_branch()` (refusal only — no auto-create) |
| L100 (Step 3: read files) | implicit (orientation chain) |
| L102-L106 (Step 4: verify) | `verify_setup(market)` |
| L108 (Step 5: baseline) | `run_baseline_experiment(market)` |
| L110 (Step 6: confirm) | `experiment_loop()` requires baseline row OR `--confirmed` |

---

## 13. Go/No-Go Gates

Agent 3 verifies before approving:

- **G1**: Five-File Contract intact (§1).
- **G2**: Metric routes through `prepare.calculate_*` (R-701).
- **G3**: TSV is APPEND-ONLY and schema-validated (R-716, R-719).
- **G4**: Branch check refuses non-autoresearch (R-703).
- **G5**: Decision matrix unit-tested for all seven cells (R-713).
- **G6**: No git mutation from Python loop body (R-723).
- **G7**: Advisory lock prevents concurrent runs (R-729).
- **G8**: All 367 existing tests pass alongside ≥30 new tests.
- **G9**: `python -c "import research; research.experiment_loop"` resolves to a callable, not `NotImplementedError`.
- **G10**: `import research` still works in the offline CI job (no new `prepare`-time DB I/O at import).
- **G11**: AST scanner (`test_no_string_interpolated_sql`) still passes — any new SQL in Phase 10 is a module-level constant with `%s` placeholders.
- **G12**: `research.py` line count delta is bounded — Phase 10 should add roughly 600-1200 lines, not 5000. Bloat is a smell.

---

## 14. Out of Scope (Deferred)

These are real but defer to later phases:

- **Auto-creation of `autoresearch/<tag>` branches**. The setup
  phase requires a human to run `git checkout -b
  autoresearch/<tag>` from clean main per
  AUTORESEARCH_MECHANICS.md L98. Phase 10 does not auto-create.
- **Anthropic-SDK-driven hypothesis generation**. The agent
  (Claude Code) is the hypothesis driver; Phase 10 provides the
  evaluator and TSV. A future phase MAY layer an SDK driver on
  top, but the spec is satisfied by the current setup.
- **Auto-modification of `research.py`**. Same reason. The agent
  edits research.py via its own tool calls between iterations.
- **Snapshot generation per cycle**. Phase 9 already provides
  `generate_snapshot`; Phase 10 calls `generate_strategy_memo` at
  the end of each cycle but does not auto-snapshot every parcel.
  The strategy memo references the parcel IDs; the agent or human
  can generate snapshots on demand.
- **API call counting**. R-712 — placeholder column.
- **Per-county harness gate at evaluate time**. The harness gate
  already runs inside `run_discovery_cycle`. Phase 10 does not
  re-run it independently.
- **Cross-run TSV trend reporting / dashboards**. The TSV is the
  raw record; visualization is a future ergonomic.

---

## 15. The 90-Minute Budget

AUTORESEARCH_MECHANICS.md "The Time Budget" specifies 90 minutes
per experiment, OS-level enforced. The authoritative enforcement
helper is `prepare.run_with_os_timeout` (prepare.py:639). Phase 10's
in-process loop driver does NOT use this helper directly because
running the evaluator as a subprocess from inside a parent loop
that itself owns the database connection adds operational
complexity (subprocess inherits no DB handle, must re-import,
etc.).

**R-733 (HIGH, partially accepted) — Phase 10 in-process loop is
soft-budgeted, not OS-enforced.** The loop measures wall-clock per
iteration and logs a `status=timeout` row if elapsed >
`PHASE10_BUDGET_SECONDS`. For full OS enforcement, a human invokes
`python -m research --evaluate` as a subprocess and the parent
script wraps it in `prepare.run_with_os_timeout`. A future phase
can add a `experiment_loop --subprocess-evaluator` mode if the
soft budget proves insufficient.

This is a documented partial acceptance, not an unmitigated risk.
The first overnight run will surface whether the soft budget is
adequate.

---

## 16. Final Posture

Phase 10 implements the smallest set of helpers that satisfies
AUTORESEARCH_MECHANICS.md "The Setup Phase" + "The Experiment Loop"
+ "The Git Ratchet" + "The Experiment Log" without overstepping
into agent-driven hypothesis generation or auto-git-mutation. The
metric layer is untouched. The TSV is append-only and
schema-validated. The branch check refuses to spin up on the wrong
branch. The decision logic is a pure function with full
unit-test coverage.

Approve scope; proceed to Agent 2.
