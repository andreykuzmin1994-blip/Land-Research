# Decision Note — runner-ratchet (decision precision, setup gate, budget)

> Tier 2 per `STANDING_RISKS.md` § "Change tiers": `runner.py`
> decision/TSV logic and the Setup Step 6 gate, changed between runs (no
> autoresearch run active). Full three-agent adversarial workflow: Agent 1
> risk review (`02_runner-ratchet_risk_review.md`), Agent 2 implementation,
> Agent 3 review + commit. Every role ran in an independent fresh context
> on the session's strongest available model. Base `29c051e`.
> Files: `runner.py`, `Makefile`, `cli.py`, `README.md` (operator text),
> `tests/test_runner_ratchet.py` (new). No contract or immutable file touched.

## What changed

- **(a) Keep-or-revert precision.** `_format_confidence` (the TSV's `.2f`)
  is shared by the writer and `apply_keep_or_revert_decision`, which now
  compares prior and new confidence at stored precision. The in-memory
  anchor is `_validate_log_row(row)`, byte-identical to the TSV row, so a
  fresh process (`make loop MAX=1`) and a long-running one decide alike.
  Before, an identical re-run at 4 of 12 n/12 remainders (49/12 → "4.08")
  was `keep` against the TSV anchor. TSV format unchanged.
- **(b) DSN-less setup check.** `_check_db_connection` checks
  `prepare.dsn_available()` first and catches `SystemExit` only around the
  connect. `make status`/`verify` print a `fail` JSON, and the loop's
  infra breaker sees a missing DSN. Pending-purge retries now run only
  after a passing per-iteration `verify_setup`.
- **(c) CoStar freshness.** Judged by the contract's filename date across
  `<type>/` and `ARCHIVED/<type>/` for the five registered types. The
  archive stamp is stripped, `FAILED/` and `tenant_intel` are excluded,
  and invalid or >1-day-future dates are skipped. The check never raises
  and never returns `fail`.
- **(d) 90-minute budget enforced in-process.** `_evaluate_with_budget`
  is the single budget layer, used by the loop and the baseline. A
  runner-owned SIGALRM raises `_ExperimentBudgetInterrupt` (a
  BaseException, so the sandbox's `except Exception` cannot swallow it).
  The runner pre-generates the `experiment_id`, so the interrupted run is
  recorded `timeout` and purged. The timer is cancelled before the
  handler is restored. Off the main thread or without SIGALRM, the run is
  unbudgeted with a WARNING. The runner-measured relabel (`ok`/`crash`
  with elapsed ≥ budget → `timeout`) is kept as the backstop.
- **(e) Setup Step 6 gate.**
  - `make loop` passes `--confirmed` only for a literal `CONFIRM=1` on the
    make command line. An exported `CONFIRM`, `0`, `yes` or `true` does
    not count.
  - `loop-bg` and `daily` check for a baseline (`current_run_has_baseline`,
    the loop's own F1 rule) before spawning tmux, and forward only the
    literal.
  - New `run_gated_baseline` (lock → `verify_setup` → refuse on `fail` →
    refuse a duplicate run baseline → baseline) backs `make baseline` and
    `cli.py baseline`. `run_baseline_experiment` stays lock-free (RR-1).
  - `make help`, the Makefile hints and the README describe the new flow.
- **(f) `make db-stats`** delegates to `cli.py db-stats`, which was
  already correct. Before, it printed psycopg `Cursor` reprs.

## What was verified (Agent 3, independently)

- **Suites:** root 694 OK (636 existing, unmodified, plus 58 new) on
  Python 3.13 and 3.11. Screener 23 OK.
- **New tests against base code:** a detached base worktree plus only
  the new test file gives 30 failures and 36 errors. The remainder test
  fails exactly at k = 1, 4, 7, 10. So the tests pin behaviour, not just
  new names.
- **Live local PostGIS** (throwaway container, DSN inline, schema via
  `python prepare.py`):
  - **(f)** base printed `<psycopg.Cursor …>` for all six counts. The fix
    prints integers plus the action_type breakdown.
  - **(d)** The REAL `experiment_loop` and `evaluate` ran under a 2 s
    budget, with ingestion, discovery and memo stubbed. The scoring stub
    committed one `parcel_scores` row stamped with the runner's id, then
    blocked in `pg_sleep(30)`.
    - Interrupted at 2.2 s; the TSV row was `timeout | … | exp=<id>`.
    - The real purge deleted the row (an `experiment_purge` log row was
      written).
    - The handler was restored and the itimer was `(0.0, 0.0)`.
- **Offline behaviour checks:**
  - **(b)** with no DSN, `_check_db_connection()` returns a `fail` dict
    and exits 0. Base exited 2 with no output.
  - **(c)** an export archived by the real `costar_ingest._archive_file`:
    the fix says `ok`, base said `warning`.
  - **(e)** the `make -n` outputs for `loop` and `loop-bg` match the gate
    described above.
- **SR-1** no contract or immutable file touched.
- **SR-2** no new SQL.
- **SR-6** new tests patch `verify_setup`, `dsn_available`,
  `get_connection`, the purge and git, use tempdir TSV/lock files, and
  keep the mirror kill switch on.
- **SR-10** stdlib only.
- **SR-8/SR-12** no DSN or env content in code, docs or commit.
- **SR-13** the purge stays the only deletion.
- **SR-14** no new `action_type`.
- **SR-15** the metric is untouched.

## Risks found → what changed as a result

Agent 1 raised 19 risks (RR-1..RR-19) and 10 spec corrections. Agent 2
addressed all of them; Agent 3 confirmed each in code and tests. The ones
that changed the design:

- RR-1: the lock went into a new gated entry point. Locking inside
  `run_baseline_experiment` would deadlock every confirmed bootstrap.
- RR-2/RR-3: the interrupt is a BaseException, and the runner owns the
  experiment id. `prepare.wall_clock_budget` was shown to be swallowed,
  and the work finished at 4.1 s against a 1 s budget.
- RR-6: the anchor row is the validated TSV row. This also covers
  description truncation.
- RR-7/RR-8: the pre-flight runs before tmux, and a duplicate baseline is
  refused.

Agent 3 found no defects that need code changes, and made no fixups.

## Deviations from the brief

- (d) uses a runner-owned handler instead of
  `prepare.wall_clock_budget` (see RR-3).
- Accepted behaviour change: a confidence gain that does not move the
  stored 2-decimal value is now a tie, and a tie reverts.
- `make loop` now runs through `cli.py loop`, so it gets exit codes 2 and
  3 and make-oriented hints.

## Residual gaps / human follow-ups

1. **Not OS-level.** `AUTORESEARCH_MECHANICS.md` L166 (SIGTERM, then
   SIGKILL) and checklist L530 remain unmet.
   - Code in the same interpreter can defeat the alarm: its own handler
     or `alarm(0)`, a BaseException catch, a blocking C call, or worker
     threads (SR-16 class).
   - The alarm fires once.
   - The relabel is the only backstop.
2. **Server-side queries are not cancelled.** psycopg does not cancel the
   server query on this interrupt. Live, the `pg_sleep` backend stayed
   `active` after the timeout and holds its locks until it ends.
3. **Interrupted cycles lose their writes.** Interrupted discovery and
   ingestion cycles roll back. `costar_ingest` archives files before the
   outer commit, so an interrupt (or any crash) mid-ingestion can drop the
   DB load of already-archived exports. Follow-up: a Tier 2
   `costar_ingest` change to archive only after commit.
4. **DSN deletion mid-run.** A `SystemExit` from inside evaluate or the
   purge after in-process DSN deletion still ends the loop (SR-16 class).
5. **Step 6 is procedural.** Nothing records that a human reviewed the
   baseline row: `make baseline` followed by `make loop` proceeds,
   whoever runs it. `CONFIRM=1` is documented as human-only consent but
   cannot be enforced.
6. **Daily new runs.** The date-derived `TAG` makes `make daily` start a
   new run every calendar day, and it now refuses until `make baseline`.
   Decide whether `TAG` should default to the current run.
7. **Description truncation.** The 200-char description cap can still cut
   the `run=`/`exp=` tokens. The decision is now consistent across anchor
   sources, but a truncated keep row stops anchoring under F1.
8. **Docs outside this file list:**
   - `CLAUDE.md` L72: the daily one-liner needs `make baseline` for a new
     run.
   - `AUTORESEARCH_MECHANICS.md` L166 and L530.
   - Stale `prepare.py` docstrings about `run_with_os_timeout`. The file
     is immutable, so this goes through the prepare-mutation protocol.
9. **Unscoped count.** The `composite_score >= 70` actionable count in
   `db-stats` is unscoped (SR-3/SR-15 drift). Left as is.
