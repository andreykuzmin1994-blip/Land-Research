# Decision Note — tests-ci-reporting (live check on run branches, DSN '@' redaction, real budget interrupt, memo actionable count)

> Decision-note format per `STANDING_RISKS.md` § Change tiers (deltas only).
> Tier 1 for `reporting.py` (run-immutable module, changed between runs;
> informational memo, not a ratchet input). Tier 0 for the tests, the live
> CI scripts and the workflow comment. Base `bf4d29b`; no `autoresearch/*`
> run active. Two roles, each in an independent fresh context on the
> session's strongest available model: implementation, then adversarial
> review + commit (this note). The reviewer applied the patch to a clean
> worktree at `bf4d29b` and re-ran every check below itself.
> **Verdict: APPROVE.** One reviewer fixup: a cross-reference from the 03
> note's correction to this note.

## What changed

1. **(a) `tests/live_txn_check.py` on `autoresearch/<tag>` branches.**
   The script refused with exit 2 whenever HEAD was a run branch, and
   `discovery-fulton.yml` runs it with no branch filter after the smoke
   test. Since tier0 made the smoke test branch-proof, that refusal alone
   turned the live-postgis job red on every pushed run branch touching the
   workflow's paths.
   - Both scoring checks pass an explicit throwaway `run_tag`
     (`livetxn-<token>`, also used as `experiment_id`) to
     `research.run_scoring_cycle`, and score parcels in a market of their
     own (`livetxn-<token>-score`). That market is registered in
     `research._MARKET_TO_COUNTIES` only while the cycle runs.
   - The foreign-parcel guard reads the same run-scoped selection the
     cycle uses (`research._SQL_LIST_PARCELS_FOR_SCORING_RUN_SCOPED`,
     `(market, tag, tag)`). In `atlanta` a fresh tag would also select the
     parcel the smoke test leaves behind; the own-market choice is why.
   - The branch refusal is removed. The script docstring, the workflow
     comment ("asserts a global unscoped pipeline count") and the 03
     note's residual (dated correction) are updated.
2. **(b) Password fragments in host-guard refusals (SR-8).** libpq splits
   a URL's userinfo at the FIRST '@', so an unencoded '@' in a password
   spills its tail into host, port, dbname or a query parameter.
   - `tests/test_postgis_smoke.py` and `tests/live_txn_check.py` gain
     `_has_stray_at`: an '@' anywhere outside user/password (a single
     leading '@' on a host, the abstract-socket form, excepted; PGHOST /
     PGHOSTADDR included). Such a DSN is refused without printing host,
     port or dbname, with a "URL-encode '@' as %40" hint.
   - `live_txn_check.describe_target` withholds those fields under the
     opt-in too.
3. **(c) `runner._ExperimentBudgetInterrupt` through the real handlers.**
   Offline tests only (no production code change): ingestion mid-file,
   scoring mid-parcel and discovery mid-parcel, each with and without a
   failing block ROLLBACK. The `prepare.BudgetExceeded` branches and their
   tests are kept.
4. **(d) `reporting.py` strategy memo.** "Actionable" now means what the
   memo header says: composite >= threshold AND actionability = PASS. The
   threshold is the `params` the memo already uses
   (`prepare.get_parameters()` when the caller passes none; never
   re-parsed, SR-3). PASS parcels without a primary strategy are listed as
   "(no primary strategy): N" instead of "(no parcels passed
   actionability)". PASS parcels only below the threshold get their own
   message. The memo stays UNSCOPED (AUTORESEARCH_MECHANICS.md), and its
   SQL is unchanged (SR-2).

## Deviation accepted

Requirement (c) asked that "earlier work" be committed in all three cycles.
That holds only for ingestion (one commit per file). Scoring and discovery
commit once at cycle end by design (03 note), so an interrupted cycle
commits nothing and the connection context rolls it back. The tests pin
what the handlers must do: the same interrupt object propagates; no
handler calls `rollback()`; the earlier parcel's writes are still pending
in the cycle's transaction; the in-flight parcel is neither marked `error`
nor skipped; later parcels are never processed; discovery does not take
its KeyboardInterrupt commit-and-abort path. Changing `research.py` was out
of scope.

## Verified (reviewer, independently)

- **Suites.** Root 869 OK (847 + 22) on Python 3.13 and on a 3.11 venv
  holding only `requirements.txt` (CI's version). Screener 96 OK. All five
  changed `.py` files parse under a real 3.11 interpreter.
- **Tests fail on base.** The two new test files run against `bf4d29b`'s
  code: 66 failures + 4 errors of 74. Every (a), (b) and (d) test fails
  except those pinning deliberately kept behaviour. (c) passes on base by
  design (coverage gap, behaviour already right). Reviewer mutants, each
  widening one handler on the interrupt path, are all caught:
  `score_parcel` per-parcel, `_process_parcel` UPSERT and the comp-file
  loader `except Exception` -> `except BaseException`, and
  `run_discovery_cycle`'s `except KeyboardInterrupt` -> `except
  BaseException`.
- **(a) live**, on the reviewer's own `postgis/postgis:16-3.4`
  (127.0.0.1:55581, DSN inline, no `.env`; container removed afterwards):
  - Base reproduced on a temporary `autoresearch/tcr-rev` branch: smoke
    rc 0, then base `live_txn_check.py` rc 2 ("REFUSED: an
    autoresearch/<tag> branch is checked out").
  - Fixed: smoke rc 0 and live check rc 0 with 20/20 checks OK in every
    run. Runs: the shared DB twice on the branch; fresh DB A twice on the
    branch and once detached; fresh DB B twice detached.
  - After every run: 0 `livetxn-*` parcels, score rows, log rows or flags,
    0 market/submarket leftovers, and 0 `parcel_scores` rows tagged with
    the branch's tag (SR-13). The temporary branch was deleted.
- **(b) live**, both scripts run with runtime-built fake DSNs (`.invalid`
  and `localhost` hosts) over 9 password-tail shapes. These are the
  finder's three plus `#`, `[...]`, `,`, `?key=`, `?x` and `%40`.
  - Base printed the fragment in 32 of 36 runs.
  - Fixed printed it in 0 of 36. Both scripts still refuse (exit 1 / 2).
- **(d) live**, with seeded rows (memo vs the metric definition over the
  same rows):
  - Mixed rows: base 4, fixed 2, metric 2.
  - PASS rows with no strategy: base 2 under "(no parcels passed
    actionability)", fixed "(no primary strategy): 2".
  - One PASS row below the threshold: base 1, listed as Spec Development;
    fixed 0, with the below-threshold message; metric 0.
- **Invariants.** No changes to `prepare.py`, `parameters.json`,
  `sources.json`, `program.md` or `screener/`. No new dependencies
  (SR-10). No secret-shaped literals: the fake passwords are built at
  runtime. No model names. All files are within the track's list. The
  tests are offline and hermetic (SR-6): `psycopg.connect` and
  `prepare.dsn_available` are patched, and no `.env` is read.

## Risks found / residual gaps (human follow-ups)

1. **Opt-in paths.** With `POSTGIS_SMOKE_ALLOW_NONLOCAL=1` or
   `LIVE_TXN_CHECK_ALLOW_NONLOCAL_DB=1` the guards are skipped. libpq's own
   connection error for a malformed '@' DSN then quotes the spilled host,
   just as `prepare.py`'s connection-error log does. This predates the
   change and is outside the guards.
2. **Side effect (intended).** A local DSN with an '@' in its port,
   dbname, hostaddr or a query parameter (e.g. a `%40`-encoded dbname) is
   now refused, with an explicit message. A `%40`-encoded password or user
   is unaffected (pinned by tests).
3. **Memo "Top N Highlights"** (`_select_top_n_actionable`) still ranks
   PASS parcels first even below the threshold. Each line shows its
   composite and verdict, so it is left unchanged. Decide whether it
   should use the same "actionable" definition.
4. **Out of this track's files** (verify_result2 finding 2): the
   `runner._ExperimentBudgetInterrupt` docstring and residual 3 of
   `02_runner-ratchet_decision_note.md` still describe pre-txn-track
   ingestion behaviour. These are documentation only; the new (c) tests
   show the real behaviour.
5. A primary strategy outside `research._STRATEGY_KEYS` on an actionable
   parcel is counted under "(no primary strategy)". Before the change it
   was dropped. No such value is produced today.
