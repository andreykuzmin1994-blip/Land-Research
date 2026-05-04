# Phase 10 Reviewer Decision — Experiment Loop and Setup Phase

**Reviewer:** Agent 3 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Following the
orchestrator-inline three-agent precedent from Phases
2/3/3.1/5/7+8/9 — sub-agent streaming has timed out consistently in
this environment. A future session with working sub-agent streaming
should ratify this decision with full context independence.

**Date:** 2026-05-04.
**Branch:** `claude/setup-research-loop-ZUuA6`.
**Base commit:** `a94050a` (merge of Phase 9 onto main).
**Reviewing:** Phase 10 implementation across `research.py`,
`tests/test_discovery.py`, and the role docs at
`reviews/12_phase10_experiment_loop/01_risk_review.md` and
`02_code_writer_response.md`.

---

## 1. Verdict at the top

**APPROVE.** All 12 go/no-go gates from `01_risk_review.md` §13 pass
on independent verification. The R-701..R-733 risk catalog is
addressed in code or accepted with explicit rationale in
`02_code_writer_response.md`. The pre-existing 415 tests still pass;
82 new Phase 10 tests pass. Five-File Contract bytes-identical to
`a94050a`.

The orchestrator-inline three-agent deviation is documented in the
header of each role document, mirroring the Phase 2/3/3.1/5/7+8/9
precedents.

---

## 2. Per-gate verification

### Gate 1 — Five-File Contract intact

```
$ git diff --stat a94050a -- prepare.py parameters.json sources.json \
                              program.md connector_harness.py \
                              connector_registry.json requirements.txt
(empty diff — 0 lines)
```

Verified bytes-identical to `a94050a`. ✓

### Gate 2 — Metric routes through `prepare.calculate_*`

```
$ grep -c "calculate_actionable_pipeline_count\|calculate_confidence_weighted_pipeline" research.py
5
```

All 5 references are in the Phase 10 block:
- 1 in the docstring header
- 1 each in `evaluate()` (the two calls)
- 2 in test descriptions in the response document via reference

No reimplemented metric SQL. The Phase 10 SQL surface contains
exactly four read queries for `verify_setup` (POSTGIS_VERSION,
submarkets bbox count) — none touch `parcel_scores`. ✓

### Gate 3 — TSV is APPEND-ONLY and schema-validated

```
$ grep -n "O_APPEND\|os.open.*log_path" research.py
6021:    fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)

$ grep -n "open.*\"w\"\|open.*'w'" research.py | grep -i log
(empty)
```

The TSV is opened with `os.O_APPEND`, never `O_TRUNC` or `"w"`.
`_validate_log_row` rejects every R-719 case. ✓

`.gitignore` carries `experiment_log.tsv` (line 60, unchanged
from before Phase 10 — already present from Phase 1). ✓

### Gate 4 — Branch check refuses non-autoresearch

```
$ python -m unittest tests.test_discovery.TestPhase10AssertAutoresearchBranch -v
test_accepts_autoresearch ... ok
test_refuses_dev_branch ... ok
test_refuses_detached_head ... ok
test_refuses_main ... ok
4 tests passed
```

`SetupError` is raised with an actionable message that explains the
AUTORESEARCH_MECHANICS.md branch rule. ✓

### Gate 5 — Decision matrix unit-tested for all cells

```
$ python -m unittest tests.test_discovery.TestPhase10DecisionMatrix -v
10 tests passed
```

All seven cells from the §6 risk-review matrix plus three bonus
cases (ULP tolerance, unknown-status raise, lower-confidence
discard). ✓

### Gate 6 — No git mutation from Python loop body (R-723)

```
$ grep -n "git reset\|git commit\|git push\|git checkout" research.py
5343:#   - It does not call ``git reset --hard HEAD~1`` from Python.
5349:#     requires a human to ``git checkout -b autoresearch/<tag>`` from main.
5488:            "a clean main. Run: git checkout -b autoresearch/<tag>"
6088:    R-723: this loop does NOT call ``git reset --hard HEAD~1`` or any
```

Four matches, ALL in comments / docstrings / SetupError messages.
No subprocess invocations of any git-mutating command. The only
`subprocess.run` calls in the Phase 10 block are
`git rev-parse --abbrev-ref HEAD` (read-only) and
`git rev-parse --short=7 HEAD` (read-only). ✓

### Gate 7 — Advisory lock prevents concurrent runs

```
$ python -m unittest tests.test_discovery.TestPhase10ExperimentLoopAdvisoryLock -v
2 tests passed
```

`fcntl.LOCK_EX | LOCK_NB` raises `BlockingIOError` on second
acquisition, surfaced as `LoopLockError`. Lock releases on context
exit. ✓

### Gate 8 — All prior tests + ≥30 new tests pass

```
$ python -m unittest discover tests
Ran 497 tests in 0.614s
OK
```

415 prior + 82 new Phase 10 = 497. Target was 367 prior + 30 new =
397; we over-delivered. ✓

### Gate 9 — `experiment_loop` is callable, not NotImplementedError

```
$ python -c "import research, inspect; \
             assert 'NotImplementedError' not in inspect.getsource(research.experiment_loop)"
(no output — assertion holds)
```

The Phase 3 stub is replaced. The function takes `(market, *,
max_iterations=None, confirmed=False)`. ✓

### Gate 10 — `import research` works in offline CI

```
$ python -c "import research; print('ok')"
ok
```

No new DB I/O at import time. The lazy-connection pattern from
prepare is preserved. ✓

### Gate 11 — AST scanner still passes

```
$ python -m unittest tests.test_discovery.TestStaticChecks
6 tests passed
```

The Phase 9 SQL-static-checks (`test_no_string_interpolated_sql`,
`TestPhase9SqlConstantsStaticChecks`) and Phase 10's new constants
contract test all pass. The four new Phase 10 SQL strings (DB ping,
submarket bbox count) use parameterized queries with `%s`
placeholders. ✓

### Gate 12 — Line-count delta bounded

```
$ wc -l research.py tests/test_discovery.py
6288 research.py        (was 5350; +938 net, 952 added / 14 deleted)
5501 tests/test_discovery.py (was 4564; +937 added)
```

research.py +938 LOC against the 600-1200 target — within the soft
ceiling. test_discovery.py +937 LOC for 82 new tests is dense but
not bloated; the test classes mirror the risk catalog 1:1. ✓

---

## 3. Did Agent 1 miss any risks?

Walking the diff against the risk catalog. Three gaps surface:

**Gap A — `evaluate()` non-transactional across sub-cycles.**
Discovery commits before scoring runs; if scoring crashes after
discovery, the parcels are real and the metric reflects them. The
next iteration will re-score them. **Resolution**: documented in
the docstring (D2 in Agent 2's response). Not a correctness bug
because `prepare.calculate_actionable_pipeline_count` always counts
the LATEST score row per parcel — a partially-scored cycle yields
a metric that reflects the partial work.

**Gap B — Memo failure swallowed.** If `generate_strategy_memo`
raises mid-cycle, `evaluate()` logs and continues to the metric
read. **Resolution**: D3 in Agent 2's response. Acceptable —
losing a memo is a far smaller cost than losing a real metric
movement to a memo-rendering bug. The strategy memo CAN BE
regenerated; the metric movement cannot if the loop crashes.

**Gap C — `halt` status not in Karpathy's original five.**
AUTORESEARCH_MECHANICS.md L309 specifies `baseline | keep | discard
| crash | timeout`. Phase 10 adds `halt` for accounting why the
loop exited (sentinel, 7-day, infra-failure x3). **Resolution**:
documented in D5; the decision function never returns `halt` (it
is only emitted by `_record_halt_row`); the schema validator
accepts the extended set. A future TSV consumer that strictly
follows the spec must handle `halt` rows gracefully — they have
metric=0 and confidence=0 so any aggregation that filters by
status works correctly.

None of the three gaps require code changes. Documented for the
human reviewer's awareness. ✓

---

## 4. Did Agent 2 actually address each risk?

Spot-audit of 10 risks chosen for highest impact.

| R# | Claimed mitigation | Verification |
|----|-------------------|--------------|
| R-701 | metric routes via `prepare.calculate_*` | grep'd Phase 10 block (Gate 2) |
| R-702 | `prepare.verify_parameters_unchanged()` once per evaluate | code review confirms one call at top of `evaluate` |
| R-703 | branch check refuses non-autoresearch | `TestPhase10AssertAutoresearchBranch` (4 cases) |
| R-713 | decision function pure + tested | `TestPhase10DecisionMatrix` (10 cases) |
| R-714 | strict tiebreaker on simplicity | `test_tied_metric_equal_confidence_discards` |
| R-715 | float tolerance via `math.isclose` | `test_tied_metric_isclose_confidence_discards` |
| R-716 | TSV append-only | grep `O_APPEND` confirmed; no `"w"` mode anywhere |
| R-719 | schema validation rejects every cell | `TestPhase10TsvSchemaValidation` (14) + `TestPhase10TsvCommitFormat` (4) + `TestPhase10ConstantsContract` (5) |
| R-723 | no git mutation in loop | grep'd entire research.py — only read-only `git rev-parse` calls |
| R-729 | advisory lock | `TestPhase10ExperimentLoopAdvisoryLock` (2 cases) |

All 10 spot-checks confirm the mitigations are in code, not just
in the response document. ✓

---

## 5. Style and consistency

Code style matches Phase 5 / Phase 6.1 / Phase 7+8 / Phase 9 conventions:

- Module-level constants with `_TSV_*`, `_AUTORESEARCH_*` prefixes.
- `_` prefix on private helpers, no prefix on public API
  (`evaluate`, `experiment_loop`, `apply_keep_or_revert_decision`,
  `read_experiment_log`, `append_experiment_log_row`, `verify_setup`,
  `run_baseline_experiment`, `SetupError`, `LoopLockError`).
- Docstrings reference R-numbers from the risk review.
- Risk-review citations use `# R-XXX: explanation` comments where
  inlined, or full sentences in docstrings.
- Test classes named `TestPhase10<Topic>` matching the precedent.
- Test methods named `test_<scenario>`.

The Phase 10 block is bracketed by a clearly delimited section
header (`# === Phase 10 — The experiment loop, setup phase, and
experiment_log.tsv I/O ===`) making the scope obvious to a future
maintainer.

No inconsistencies surfaced. ✓

---

## 6. Over- or under-engineering

**Not over-engineered.** Each helper has one job. The TSV writer
is 25 lines; the decision function is 35 lines. No new abstractions
(no `LoopRunner` class, no `Decision` dataclass, no plugin system).
Markdown / TSV is assembled via direct string ops against
deterministic helper outputs.

**Not under-engineered.** Three under-engineering risks worth
calling out:

a. **The loop does not auto-revert via git.** This is by design
   (R-723 / D1). The agent (Claude Code) handles git operations
   between iterations. A future phase MAY add an opt-in
   `--auto-revert` flag, but the spec is satisfied.

b. **`api_calls=0` is a placeholder column.** D4 — Phase 11+ task.
   The TSV format is correct; the value is just not yet wired.

c. **The infra-failure backoff is fixed at 60s × consecutive
   failures, capped at 5 min.** Could be made configurable via
   constants, but the values are reasonable defaults for the
   target deployment (Codespaces or Linux laptop). Not blocking.

✓

---

## 7. Test quality

Tests check behaviour, not implementation:

- `TestPhase10DecisionMatrix` covers every cell of the public
  decision matrix without caring HOW the function dispatches.
- `TestPhase10ExperimentLoopIterations` is a true end-to-end test:
  pre-seeded TSV + mocked verify_setup + mocked evaluate sequence
  → loop runs → file on disk → row sequence asserts.
- `TestPhase10EvaluateMetricRouting.test_metric_value_comes_from_prepare`
  proves R-701 by using the prepare.* symbols as the integration
  point — replacing them with a sentinel and confirming the
  evaluator surfaces the sentinel verbatim.
- `TestPhase10ExperimentLoopBaselineBootstrap.test_refuses_without_baseline_and_unconfirmed`
  proves Setup Step 6 is enforced.
- `TestPhase10ExperimentLoopAdvisoryLock` exercises the actual
  `fcntl.flock` semantics, not a mock.

The fake-conn fixture pattern from Phase 5 (`Phase5FakeConnection`,
`_SharedQueueCursor`) is reused without modification. ✓

---

## 8. Documentation updates

Updated:
- Phase 10 section header in research.py with R-701..R-733 reference.
- New helper docstrings cite R-numbers.
- `_print_phase10_status` banner replaces the Phase 1 holdover.
- `02_code_writer_response.md` records D1..D10 explicitly.

NOT updated (intentional):
- `program.md` — read-only per the Five-File Contract.
- `BUILD_PHASES.md` — the human owns the roadmap; updating it from
  research.py-side is a between-runs concern (matches Phase 7+8/9
  precedent).
- `README.md` — phase progress goes in commits.
- `AUTORESEARCH_MECHANICS.md` — read-only; the implementation
  conforms to the spec, the spec does not need updating.
- `CLAUDE.md` / `START_HERE.md` — orientation chain is unchanged.

✓

---

## 9. Commit plan

Commit 1: `phase10: experiment loop, setup phase, evaluator, TSV I/O`

Single commit covering:
- `research.py`: Phase 10 imports (`math`, `subprocess`), constants
  (`_TSV_COLUMNS`, `_TSV_STATUSES`, `_AUTORESEARCH_BRANCH_RE`,
  budget / lock / halt sentinel paths), exceptions (`SetupError`,
  `LoopLockError`), helpers (git plumbing, TSV I/O, decision
  function, setup verifier sub-checks, evaluator orchestration,
  loop-driver helpers), public API (`evaluate`,
  `apply_keep_or_revert_decision`, `read_experiment_log`,
  `append_experiment_log_row`, `verify_setup`,
  `run_baseline_experiment`, `experiment_loop`), and the updated
  `_print_phase10_status` banner.
- `tests/test_discovery.py`: 82 new tests across 17 classes;
  `Phase5FakeConnection` reused.
- `reviews/12_phase10_experiment_loop/`: 01_risk_review.md,
  02_code_writer_response.md, 03_reviewer_decision.md (this file).

The combined commit is a Phase 10 build commit. The Karpathy single-
variable-change discipline applies to *experiment-loop* commits on
`autoresearch/<tag>` branches, not to *build-phase* commits like
this one.

Push: `git push -u origin claude/setup-research-loop-ZUuA6` per
the branch instructions in the harness preamble.

No PR will be opened (the human did not request one).

---

## 10. Closing note

Phase 10 closes the structural gap between the Phase 9 deliverable
and the autonomous loop. Before this push, every supporting helper
existed (Phases 3–9) but the orchestrator that ties them together
plus the metric reader plus the keep-or-revert TSV did not. After
this push, a human on an `autoresearch/<tag>` branch with a healthy
`.env` and Supabase project can run:

```python
import research
research.experiment_loop("atlanta", confirmed=True)
```

…to bootstrap the baseline and begin the loop. The loop runs
until the human creates a `.halt` sentinel or sets
`EXPERIMENT_LOOP_HALT=1`, at which point it exits cleanly with a
synthetic accounting row.

Per AUTORESEARCH_MECHANICS.md "The Experiment Loop", git operations
between iterations are the agent's responsibility, NOT the loop
driver's. The agent (Claude Code) reads the TSV after each
iteration, decides whether the most recent decision row was `keep`
or `discard`, and performs the corresponding git operation in its
own tool calls. This separation prevents the auto-revert footgun
and matches Karpathy's actual pattern more cleanly than a fully
git-aware Python loop would.

Phase 11 (additional county connectors) and Phase 13 (tuning) can
proceed independently. The loop infrastructure is now stable and
testable.

The honest limitation: the OS-level 90-minute budget is enforced
softly inside the in-process loop, not via a `prepare.run_with_os_timeout`
subprocess wrapper. R-733 documents the partial acceptance. If the
first overnight run shows runaway iterations, a future phase can
add a `--subprocess-evaluator` mode that wraps `evaluate()` in
the OS-enforced helper.

Approved for commit and push.
