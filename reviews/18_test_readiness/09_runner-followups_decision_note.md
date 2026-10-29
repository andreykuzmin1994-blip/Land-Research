# Decision Note — Runner follow-ups (durable purges, description budget, ops tooling)

> **Tier 2** for `runner.py`'s purge and TSV-description logic
> (`STANDING_RISKS.md` § "Change tiers"); **Tier 0** for the Makefile/cli
> ops fixes and the dated doc corrections. Between runs; no AutoResearch
> run active. Base `bf4d29b`. Three roles, each in an independent fresh
> context on the session's strongest available model: risk review
> (`09_runner-followups_risk_review.md`, RF-1..RF-15), implementation, then
> adversarial review + commit (this note). Files: `runner.py`, `cli.py`,
> `Makefile`, `tests/test_runner_ratchet.py`, dated Update lines in the two
> `02_runner-ratchet_*` notes, and the two `09_` notes. **Verdict: approved
> as submitted. The reviewer made no code changes.**

## Why

Verified findings from the cross-track review of `bf4d29b`:

- **(a) medium.** The pending-purge retry waited for an overall passing
  `verify_setup`. A purge that failed on a DB blip, followed by 3 harness-only
  failures (DB healthy), tripped the infra breaker and dropped the in-memory
  queue. The discarded experiment's rows stayed in the run, and the next
  experiment on reverted code was recorded `keep` with A's rows as its
  metric (SR-13/SR-15). The queue never survived a process anyway, and
  `make loop MAX=1` restarts the process for every experiment.
- **(b) low.** `_format_loop_description` read a made-up `inserted` key, so
  live rows never carried `discovery_fulton=`. The 200-char cap also cut
  `run=`/`exp=` off long crash rows (a realistic psycopg blip row is 250+
  chars), so the rows most likely to need a re-purge were the ones that lost
  their markers.
- **(c) low.** Ratchet residual 3 / RR-12 still described pre-`686174a`
  ingestion.
- **(d) low.** `make log/status/orient` needed `column`; a recorded
  `make baseline` then exited 2 ("setup refused") and a retry was refused as
  a duplicate.
- **(e) nit.** `make mirror-restore` refused with a raw traceback.

## What changed

1. **Pending purges are retried, durable and gated (`runner.py`).**
   - Retried at every iteration boundary whose **DB sub-check** is ok,
     whatever else failed (`_db_check_ok`, defensive for `checks: {}` mocks).
   - Tried once more on every loop exit: halt sentinel, `max_iterations`,
     7-day exit, F5 breaker, infra halt (always after the halt row), and on
     an exception exit without masking the exception. Ctrl-C/SystemExit exits
     do no DB work. Ids still unpurged are logged at ERROR and returned as
     `unpurged_experiment_ids` (cli prints a WARN line).
   - Rebuilt from **this run's** TSV rows at every loop start, after a
     bootstrap, and before a gated baseline (`_pending_purges_from_log`):
     end-anchored, shape-validated `run=<tag> | exp=<id>` tail only; tag must
     equal the current run; statuses discard/crash/timeout (baseline attempts
     included); any id on a keep/baseline row is excluded; TSV only, never the
     mirror; unparseable rows of this run are counted at WARNING, never
     guessed.
   - Rebuilt candidates go through **one** run-scoped read
     (`_SQL_SELECT_EXPERIMENT_RESIDUE`, `SET LOCAL statement_timeout '30s'`);
     only ids that still have rows are purged, via `_purge_experiment_scores`
     only. A failed read keeps every candidate pending (fail closed). A clean
     loop start writes nothing (RF-6 decision: the residue read).
   - "purged" is logged only by a purge that committed. A vanished DSN
     (prepare's `SystemExit`) is caught only around the retry's DB steps and
     reported as "NOT purged".
   - No baseline is recorded over unpurged rows: the loop bootstrap and
     `run_gated_baseline` (after verify, under the lock) refuse with
     `SetupError` (exit 2); after a crashed bootstrap, an iteration that would
     become the baseline counts as a setup failure, so the infra breaker
     halts.
   - A purge that deletes rows of an experiment older than the run's newest
     anchor logs a "late purge" ERROR recommending a new run (RF-11).
2. **Description budget (`_compose_description`).** The loop and baseline
   descriptions are composed to fit the 200-char cap before the writer sees
   them: the tail (`stamp_audit_violations=`, `run=`, `exp=`) is never cut;
   the error text is clipped with `…` first, then optional counters are
   dropped. `_sanitize_description` is unchanged. Run tags longer than 64
   chars are refused at loop and gated-baseline entry.
3. **Real discovery count.** `discovery_<county>=` now reports
   `totals['discovery']` from the real `{'aborted','per_corridor','totals'}`
   shape (parcels upserted, equal to the cycle's research_log `discovery`
   rows); aborted/skipped counties print nothing. The formatter is total over
   any sandbox shape, and only `^[a-z0-9_]+$` county keys are printed.
4. **Ops.** One guarded `TSV_ALIGN` (column if present, else POSIX awk) for
   `orient`/`status`/`log` (orient still imports no Python); `make baseline`
   renders the log non-fatally, so a recorded baseline exits 0 and a refused
   one keeps its non-zero exit; operator text no longer claims "purged"
   unconditionally. `make mirror-restore` delegates to a new
   `cli.py mirror-restore`, which turns the narrow `RestoreRefusedError`
   (a `RuntimeError`, raised before any connection) into one stderr line,
   exit 2.
5. **Docs.** Dated Update lines on ratchet residual 3, RR-12 (ii)/(iii) and
   the § 4 ingestion bullet; the `_ExperimentBudgetInterrupt` docstring names
   the two re-raising helpers and the remaining `except Exception` sites; the
   `_check_costar_freshness` claim is kept (reason added, not weakened).

## Verified by the reviewer (independently)

- **Suites:** root 887 OK (847 + 40 new, 2 rewritten), screener 96 OK.
  Root suite also 887 OK under Python 3.11 (CI's version) in a scratch venv.
- **Fails at base (G2):** the new test file run against a `bf4d29b` export:
  A1 `test_retry_runs_during_a_harness_only_failure_streak` and B3
  (`test_markers_survive_every_length`, `test_realistic_db_blip_row_keeps_its_markers`)
  fail, and so do 36 of the other 39 new or rewritten tests. The three that
  pass at base are guards that must keep passing: `column` is used when
  present, a refused baseline stays non-zero, and aborted/skipped counties
  print no count.
- **Live (reviewer's own probe, own container on 127.0.0.1:55580, throwaway
  tree, DSN inline, loopback-only socket guard; container removed):**
  real scoring and the real run-scoped metric.
  - Harness outage, base: A's 5 rows survive, B is written
    `keep 5 1.25 | scored=0`, metric 5, crash row markers `(None, None)`, no
    `discovery_fulton`. Fixed: A purged during the outage (5 rows), B scores 5
    and is `discard`, metric 0; crash row 200 chars with both markers and
    `discovery_fulton=5`.
  - Two sessions, A's purge failing in-iteration and at exit: base again
    credits B with A's rows (metric 5); fixed, session 2's loop start reads
    the residue and purges A before B runs (metric 0).
  - The residue read works with a Python list for `ANY(%s)` and is
    run-scoped (an id under another run is not returned).
- **SR-6 (G5):** whole root suite under a record-only guard on
  `prepare.get_connection` **and** `psycopg.connect`, with and without
  `dsn_available()` forced True: 0 recorded calls.
- **Composer fuzz:** 40,000 random loop descriptions (errors 0-1000 chars
  with tabs/NUL/unicode whitespace, 0-30 counties, tags 1-64, huge counts)
  plus 5,000 baseline descriptions: all ≤ 200, unchanged by
  `_sanitize_description`, tail parses to `(tag, id)`, stamp token kept.
- **SR-13 / SR-2:** one `DELETE FROM parcel_scores` constant; every purge goes
  through `_purge_experiment_scores`; new SQL is module constants; TSV
  format, statuses and append sites unchanged (`TestMirrorCallSitePairing`
  green); `_sanitize_description` byte-identical.
- **Ops (G9/G10), in this container (no `column`):** `make -f <abs Makefile>
  log` / `orient` in a temp cwd exit 0 and print every row; `make
  mirror-restore` over a live (scratch) TSV prints one `[FAIL] mirror-restore
  refused: …` line and exits 2 with no traceback.
- **Invariants:** no immutable file touched, no new dependency (SR-10), no
  secret-shaped literals or model names, only allowed files changed; no
  overlap with the `tests-ci-reporting` track's files.

## Deviations from the risk review

- **RF-4 partly adopted.** Evaluate is gated on an empty queue only where a
  baseline would be recorded. With the run's baseline in place, the loop
  still evaluates while a purge keeps failing (it logs an ERROR naming the
  ids and saying the next metric includes them), because
  `tests/test_discovery.py::TestLoopPurgeWiring.test_purge_failure_does_not_kill_the_loop`
  pins NEVER STOP and is outside this track's files.
- **RF-5(i):** instead of a `dsn_available()` gate, the retry catches
  prepare's `SystemExit` narrowly around its DB steps (the
  `_check_db_connection` precedent; RF-5 allows either).

## Residual gaps / human follow-ups

1. **RF-4 residual.** A purge that keeps failing while `SELECT
   POSTGIS_VERSION()` succeeds can still let a later experiment be credited
   with leftover rows (now loud at ERROR, and the late-purge ERROR fires once
   it succeeds). Decide whether a non-empty queue should block evaluation
   (it would change the pinned NEVER-STOP test above).
2. **Merging into an in-flight run.** The first loop start after this merge
   re-checks every historical non-kept experiment of the run; leftovers older
   than the latest anchor trigger "late purge" ERRORs. **Start a new run after
   merging, and never merge main into an in-flight `autoresearch/*` branch.**
   Pre-fix truncated rows cannot be re-checked (WARNING only).
3. **Not durable in two edge paths.** If the strict `_git_head_commit` fails
   right after a failed purge, the id is only in the ERROR log (no TSV row);
   the same holds for a baseline attempt whose purge and git lookup both
   fail. A SystemExit from the in-iteration purge (DSN deleted mid-run) still
   ends the loop (ratchet residual 4).
4. **Superseded, not edited (file list):** ratchet decision-note residual 7
   and the ratchet risk-review § 4 bullet ("the 200-char cap can cut
   `run=`/`exp=`") no longer hold for rows written after this change.
5. **Out of scope, unchanged:** no `statement_timeout` on the purge DELETE;
   no index on `parcel_scores(experiment_id)` (prepare-mutation); research_log
   and flagged_items of discarded experiments are not purged; a timeout whose
   COMMIT lands after its purge is re-checked only at the next process start.
6. Run tags over 64 characters are now refused at loop/baseline entry.
