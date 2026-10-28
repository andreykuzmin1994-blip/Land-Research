# Risk review: runner-ratchet track (Tier 2), Agent 1

Base `29c051e`. Scope: runner.py (decision, TSV, budget, setup checks), Makefile, cli.py (if needed), README operator text, new `tests/test_runner_ratchet.py`. I wrote no code. Everything below was checked against the source. Evidence I reproduced:

- **(b) is real.** With `DATABASE_URL` unset, `runner._check_db_connection()` lets `SystemExit(2)` escape.
- **(a) is real.** At k/12 remainders, the TSV anchor makes an identical re-run come out `keep` for k = 1, 4, 7 and 10 (4 of 12).
- **(d)'s minimum fix is weaker than it looks.** I ran `prepare.wall_clock_budget(1)` around a loop that catches `except Exception` per item (the research.py style). The alarm fired once, was swallowed, and the work ran to completion in 4.1 s against a 1 s budget.

**Verdict: GO, with conditions.** (a), (b), (c) and (f) are low-risk if the gates below hold. (d) and (e) have traps that would corrupt the ratchet or deadlock the loop if they are implemented the obvious way.

## 1. Ranked risks

| ID | Sev | Req | Concern | Mitigation |
|----|-----|-----|---------|-----------|
| RR-1 | CRIT | e | Putting `_acquire_loop_lock()` inside `run_baseline_experiment` deadlocks `experiment_loop`. The loop already holds the lock when it calls the baseline. flock locks belong to an open file description, so a second `os.open` + `LOCK_NB` in the same process fails. `TestPhase10ExperimentLoopAdvisoryLock.test_second_acquire_raises` proves this. Every `CONFIRM=1` bootstrap would raise `LoopLockError`. | Take the lock in a NEW gated entry point (lock → verify → baseline) used only by `make baseline` and cli `baseline`. Leave `run_baseline_experiment` lock-free. `test_bootstraps_baseline_when_confirmed`, which uses the real lock, must stay green. |
| RR-2 | CRIT | d | An interrupt can escape `evaluate` if the alarm fires in its except/return tail, or between `evaluate` returning and the budget context's `alarm(0)`. The loop's outer-crash fallback then sets `experiment_id=None`, so nothing is purged and the timed-out experiment's `parcel_scores` rows inflate every later metric read (SR-13, SR-15). If the interrupt is a BaseException, it kills the NEVER-STOP loop with no row. | The runner generates `experiment_id` before the call (`_make_experiment_id()`) and passes it into `evaluate`. One wrapper converts ANY budget interrupt, including one raised at context exit, into a `timeout` result that carries that id. The outer-crash fallback also uses the pre-generated id. |
| RR-3 | HIGH | d | `prepare.BudgetExceeded` subclasses RuntimeError and the alarm fires once. About 20 `except Exception` sites swallow it: research.py per-parcel discovery and scoring, per-corridor logging, costar_ingest per-file, and the memo block in evaluate. So the minimum fix rarely interrupts anything (demonstrated above). Worse, in `costar_ingest` (L1013/L1190) the swallow moves a VALID export into `FAILED/` as a "db transaction failed". | Preferred: a runner-owned SIGALRM context that raises a dedicated `BaseException` subclass, which `except Exception` cannot catch, converted to `timeout` at the wrapper. If `prepare.wall_clock_budget` is kept as the "minimum", the decision note must say that enforcement mostly falls back to the after-the-fact relabel, and a swallow-and-retry loop written by the agent can still hang forever. |
| RR-4 | HIGH | d | Signal handling mechanics: (i) the timer must be cancelled BEFORE the old handler is restored, because a SIGALRM that arrives under SIG_DFL kills the process with no TSV row, and in tests kills the whole suite; (ii) `signal.alarm(int(s))` turns a budget under 1 s into `alarm(0)`, meaning no alarm, so the test hangs; (iii) nested budget layers share one process-wide timer, so the inner `alarm(0)` cancels the outer one; (iv) a budget bound as a default argument cannot be patched by tests; (v) `signal.signal` raises ValueError off the main thread; (vi) Windows has no SIGALRM (`NotImplementedError`). | Use exactly one budget layer, in the wrapper and not inside `evaluate`, so `evaluate` stays signal-free. Read the module constant at call time and clamp it with `max(1, int(...))`. Guard on main thread and `hasattr(signal, 'SIGALRM')`, else log a WARNING and run unbudgeted with the relabel as backstop. Test that the handler is restored and `getitimer(ITIMER_REAL) == (0.0, 0.0)` afterwards. |
| RR-5 | HIGH | d | The after-the-fact relabel at runner.py L1479 only promotes `ok`, and uses `evaluate`'s own clock with a strict `>`. Interrupting a psycopg wait leaves the connection ACTIVE, and the remaining statements then fail with "another command is already in progress". The run ends as `crash` even though it hit the budget. A swallowed alarm plus a fast metric read can also land as `ok`, or even `keep`, right at the deadline. | Keep the relabel; never remove it, because it is the backstop against SR-16 and against swallowing. The runner measures elapsed time itself, from arming to return. If elapsed ≥ budget, record `timeout` whether the result was `ok` or `crash`. |
| RR-6 | HIGH | a | The fix has to remove the TSV-vs-memory difference itself, not just the k/12 symptom. Today `rows.append({k: str(v)})` keeps full precision AND an unsanitized, untruncated description. The TSV truncates descriptions at 200 chars, which can cut the `run=`/`exp=` tokens. When that happens, a TSV `keep` row stops anchoring under F1 and the anchor quietly falls back to an older row, while the in-memory row still anchors. That is latent today (Fulton only, about 105 chars) but grows with more counties or a long TAG. | Format confidence through one helper shared with `_validate_log_row`, with no second copy of `.2f`, and apply it to both prior and new before deciding. Append `_validate_log_row(row)`, the exact TSV values, to `rows`. Do NOT change the TSV byte format (`f"{x:.2f}"`), because mirror backfill dedups on exact 7-column values (SR-13). Accepted change in behaviour: confidence gains under 0.005 now tie and are discarded. Record this. |
| RR-7 | HIGH | e | Operator flow: (i) TAG is derived from the date, so `make daily` starts a NEW run every calendar day and the gate fires daily; (ii) `loop-bg` spawns tmux, and if the loop then fails the gate, the session stays alive on `read -n 1` and the next `loop-bg` refuses with "session 'loop' already exists"; (iii) make-variable forwarding through MAKEFLAGS does not reach a tmux server that is already running; (iv) GNU make imports environment variables, so an exported `CONFIRM=1` auto-confirms forever; (v) `CONFIRM=0` or `CONFIRM=no` must not count as confirmation. | Check for a baseline before spawning tmux (in `daily` and `loop-bg`), using a runner helper that reuses the F1 logic (`_row_run_tag`). Never parse the TSV with grep or awk. Forward only a literal `CONFIRM=1` via `$(if $(filter 1,$(CONFIRM)),CONFIRM=1,)`. In Python, compare `== "1"`. Prefer a specific name such as `CONFIRM_BASELINE`, or document the env-import hazard. README: CONFIRM is the human's consent for one run, and the agent must never supply it itself. |
| RR-8 | MED | e | `make baseline` on a run that already has a baseline appends a second `baseline` row. `_last_baseline_or_keep` takes the newest, so the run is re-anchored to HEAD in the middle of the ratchet, and the keep chain no longer reflects "baseline = unmodified research.py". The new flow makes this easy to do by accident. | The gated entry point refuses when a `baseline` row for this run tag already exists, unless an explicit override is given. The documented path is a new TAG. Rows with status `crash` or `timeout` from earlier baseline attempts do not count. |
| RR-9 | MED | e | `cli.py baseline` (`cmd_baseline`) bypasses the same gates (no verify, no lock). | Route it through the same gated entry point (allowed as "if needed"), or list it as a residual bypass. |
| RR-10 | MED | b | (i) A broad catch would swallow Ctrl-C, and a BaseException budget interrupt if one were ever in scope. (ii) A test that only unsets `DATABASE_URL` still contacts the LIVE DB on an operator machine, because `dsn_available()` and `get_connection()` call `load_dotenv(.env)` (SR-6). (iii) The fix only covers the verify path. Pending-purge retries run BEFORE `verify_setup` in each iteration (L1438-1447), and evaluate's own `get_connection` would still raise SystemExit. | Check `prepare.dsn_available()` first, then catch `SystemExit` narrowly around the `get_connection` block only, never BaseException. Tests patch `prepare.dsn_available` and make `get_connection` a guard that fails the test if called. Recommended: move the pending-purge retry after a passing `verify_setup` (purges need a healthy DB anyway). Otherwise document it. In-process DSN deletion is the SR-16 class. |
| RR-11 | MED | c | `verify_setup` does not wrap its checks, and the loop calls it every iteration without a try. Any exception from the new scan therefore kills the NEVER-STOP loop: `strptime("20261399")`, PermissionError or OSError from `iterdir`, a file where a directory should be. Also: counting `FAILED/` would hide staleness; a future-dated filename would stay "fresh" forever; the archive renames `<stem>.csv` to `<stem>_<YYYYMMDDTHHMMSSZ>_<4hex><suffix>`, so the intake regexes no longer match; `.CSV` is uppercase in some cases; `Path.walk` exists only from 3.12 while CI runs 3.11, and rglob symlink behaviour differs by version. | Enumerate exactly `<base>/<type>/` and `<base>/ARCHIVED/<type>/` for the five types registered in `costar_ingest._INGESTION_LOADERS`, reusing their regexes. Parse archived names by stripping the stamp suffix, case-insensitively. Skip bad dates. Reject dates more than 1 day in the future. Treat YYYYMM as the 1st of the month (conservative). Exclude `FAILED/` and `tenant_intel`. Wrap the scan so it never raises, and return 'ok' or 'warning' only, never 'fail'. Read the one `runner._COSTAR_BASE_DIR`; do not mix in costar_ingest helpers that read their own copy. |
| RR-12 | MED | d | Side effects on real Postgres that FakeConnection cannot show: (i) psycopg 3.3 cancels the server-side query only for `KeyboardInterrupt`/`SystemExit` (`psycopg/connection.py` `_INTERRUPTED`), so after any other interrupt the backend keeps running and holding locks until it notices the closed socket; (ii) discovery and ingestion commit only at the end of a cycle, so an interrupted cycle rolls back all of that cycle's writes (KeyboardInterrupt alone commits progress so far); (iii) costar_ingest archives each file BEFORE the outer commit, so an interrupt during ingestion silently drops the DB load of files already moved to `ARCHIVED/`. (iii) already exists for any crash, SIGKILL or OOM, and the budget makes it newly reachable. | Do not fix this in this track (costar_ingest is outside the file list). State it in the decision note's residuals and earmark a costar_ingest follow-up: archive only after the outer commit. Optional evidence: `pg_sleep(30)` under a 2 s budget on the local container, then `pg_stat_activity`. |
| RR-13 | MED | all | SR-14: a new `research_log.action_type`, such as a timeout log, cannot be added to `program.md`, which is human-only. | Add no new action_type values. Reuse `experiment_purge`. |
| RR-14 | MED | d | Changing the signature `experiment_loop` uses to call `run_baseline_experiment` breaks `TestRunAwareBaseline.fake_baseline(market, *, run_tag=None)`. A default "fallback id" applied to results that explicitly carry `experiment_id: None` (`TestTerminalStatusBreaker`, and the result dicts without ids in `TestPhase10ExperimentLoopIterations`) would call the REAL `_purge_experiment_scores`. That raises SystemExit offline, or writes to the live DB on an operator machine (SR-6). | Keep the `run_baseline_experiment(market, run_tag=...)` call shape. Use the pre-generated id only when `evaluate` raised or was interrupted, never to override a returned dict. Every new test patches `_purge_experiment_scores`. |
| RR-15 | LOW | f | `cur.execute(q) or cur.fetchone()[0]`: psycopg 3's `execute` returns the cursor, which is truthy. The repo's fakes (`Phase5FakeConnection`, `_PurgeFakeCursor`) return None from `execute`, so a fake-based test would PASS the buggy code. That is how this bug survived. The `python -c` one-liner cannot hold `with` or `for` statements. | Make `db-stats` delegate to `python cli.py --no-color db-stats` (already correct, using `with` blocks), or fix it inline. Verify ONLY against real psycopg on local PostGIS (port 55442, `DATABASE_URL` inline, schema applied via `python prepare.py`, a few `research_log` rows seeded to exercise GROUP BY). Do not touch the hard-coded, unscoped `composite_score >= 70` (an existing SR-3/SR-15 drift; out of scope). |
| RR-16 | LOW | b | After the fix, `make status` without a DSN goes on to the live harness probe. That is network traffic, and a fresh process has no TTL cache. | Acceptable: the probe is polite (SR-11) and existing behaviour when a DSN is present. |
| RR-17 | LOW | d | Sandbox code can call `signal.alarm(0)`, install its own handler, catch BaseException, or block in a worker thread (`with ThreadPoolExecutor` waits on exit). | Accepted SR-16 class. The runner-measured relabel is the backstop. Mention it in the residuals. |
| RR-18 | LOW | e | After a crashed or timed-out bootstrap baseline, the loop carries on, and the first successful iteration (same in-process code) is written as `baseline`. This is existing behaviour. | Acceptable and defensible. Document it, or raise `SetupError`; either is fine, but do not leave it unspecified. |
| RR-19 | LOW | e | Stale docs outside the file list: CLAUDE.md L72 ("make daily → make tail → make halt"), and the `make orient`/`make status` hints "run 'make baseline' or 'make loop'". | Update the Makefile hints and README. Flag CLAUDE.md for the human. |

## 2. Design notes per requirement

- **(a)** Make TSV replay deterministic: replaying `apply_keep_or_revert_decision` over the written TSV, using TSV anchors only, must reproduce every status the loop wrote. Quantize either in the pure function (none of the existing tests use sub-0.01 deltas except 1e-12, which still ties) or at the call site, but with ONE shared formatter. Pin the writer's output strings in a test.
- **(b)** Return `{"status": "fail", "error": "DATABASE_URL not set ..."}`. Put no DSN or environment content in the message (SR-8, SR-12). `make status` and `make verify` keep exit 0 and print the JSON; `cli verify` keeps returning 2 on fail.
- **(c)** Keep the key `fresh_files` (cli prints keys generically). Adding `newest_export_date` is optional. An archived file is not proof the data was committed (RR-12 iii), so the check stays informational.
- **(d)** Use one wrapper, `_evaluate_with_budget(market, run_tag, experiment_id)`, for both the loop and `run_baseline_experiment`. Answer to the open question: YES, the baseline also needs the budget, because a hung `make baseline` or bootstrap otherwise stalls forever. A timed-out baseline is already written as a `timeout` row and purged. The wrapper wraps `evaluate` ONLY, never the purge, TSV append, mirror or git calls. Keep the constant at `_PHASE10_BUDGET_SECONDS == prepare.WALL_CLOCK_BUDGET_SECONDS` (assert this in a test). This is not OS-level enforcement, so the mechanics checklist item at L530 stays unmet; say so.
- **(e)** The gating logic lives in Python (runner helpers, tested). Makefile recipes only call them and pass a literal `CONFIRM=1`. Do not rely on `make verify`'s exit code: it is 0 on 'fail'. In the `make loop` `python -c`, catch `SetupError` and `LoopLockError`, print a make-oriented hint (`make baseline`, then `make loop`, or `make loop CONFIRM=1`) and exit non-zero. Pass the tag from `verify_setup` explicitly into `run_baseline_experiment` (F2).
- **(f)** See RR-15.

## 3. Tests required (`tests/test_runner_ratchet.py`; `import tests` at the top; TSV and lock in tmp dirs; patch `verify_setup`, `_git_head_commit` and `_purge_experiment_scores`; no network, no DSN)

1. **(a)** For k in 0..11 with confidence (48+k)/12 and the same metric: decision via a TSV-seeded anchor (fresh-process path, `max_iterations=1`) == decision via an in-memory anchor (iteration 1 keeps, iteration 2 identical) == `discard`. Plus:
   - a genuine gain (`4.0833 → 4.0951`) gives `keep` on both paths;
   - a sub-0.005 gain (`4.0833 → 4.0849`) gives `discard` on both paths;
   - the TSV replay test;
   - the writer's `.2f` strings are unchanged;
   - a long description where truncation drops `run=` gives the same anchor on both paths.
2. **(b)**
   - `dsn_available` False gives `fail` with no SystemExit, and `verify_setup` returns JSON-serializable `fail` with bbox `skipped`.
   - `get_connection` raising `SystemExit(2)` gives `fail`.
   - `KeyboardInterrupt` still propagates.
   - `experiment_loop` on a missing DSN raises `SetupError`, not SystemExit.
   - In-loop fail ×3 writes the halt row (patch `runner.time.sleep` or the test takes about 3 min).
3. **(c)** Using a tmp base and an injected or patched clock (never hard-code absolute dates that go stale):
   - an intake-only fresh file gives ok;
   - an archived-only fresh file, named via the REAL `costar_ingest._archive_destination` with `costar_ingest._COSTAR_BASE_DIR` patched, gives ok; this pins naming drift;
   - `FAILED/`-only gives warning;
   - stale, future, invalid (`20261399`), non-matching and uppercase `.CSV` names are handled;
   - YYYYMM is handled;
   - an unreadable directory gives warning, not an exception;
   - a missing base gives warning;
   - the result is never 'fail'.
4. **(d)** Budget patched to 1 (an int).
   - A fake `evaluate` that hangs, bounded at about 10 s so a regression fails slowly instead of hanging CI (5-min job), gives a `timeout` row, and the purge is called with the RUNNER-generated id. Elapsed < 5 s.
   - A swallowing fake (`except Exception: continue` per item) is interrupted (BaseException design), or recorded `timeout` via the relabel (minimum design; this documents the residual).
   - An interrupt type raised directly out of `evaluate` gives `timeout` plus a purge, not `crash` with id None.
   - A late `crash` (elapsed ≥ budget) gives `timeout`.
   - A hung `run_baseline_experiment` gives a `timeout` row, a purge, and no `baseline` row.
   - Off the main thread: no ValueError, a WARNING is logged, and `evaluate` still runs.
   - After every test, the handler is restored and the itimer is zero.
   - setUp installs a SIGALRM handler that fails the test, and tearDown calls `alarm(0)` and restores, so a stray alarm fails one test instead of killing the suite.
   - Keep the suite's added wall time to about 3 s or less.
5. **(e)**
   - The gated baseline refuses on verify `fail`: no `evaluate`, no TSV row.
   - It refuses while the lock is held (`LoopLockError`) and refuses a duplicate baseline for the run.
   - It proceeds on `warning`.
   - `experiment_loop(confirmed=True)` bootstrap still works under the REAL lock.
   - The `has_baseline` helper matches the loop's F1 logic.
   - Static Makefile checks: the `loop` recipe no longer hard-codes `confirmed=True`; `loop-bg` forwards only a literal `CONFIRM=1`; `daily`/`loop-bg` check for a baseline before `tmux new-session`.
6. **(f)** Real local PostGIS run, recorded in the decision note. No fake-based test is accepted as evidence (RR-15).

## 4. Out of scope (state in the decision note)

- OS-level, subprocess-isolated experiment enforcement (SIGTERM, then SIGKILL after 30 s, as the spec describes). It remains the earmarked hardening, shared with SR-16.
- costar_ingest archives before the outer commit (RR-12 iii).
- The 200-char description cap can cut the `run=`/`exp=` tokens (anchor scoping). Changing the format or token order is out of scope.
- `db-stats`/`cli` hard-coded threshold 70 and unscoped "actionable" count.
- Doc drift in CLAUDE.md, START_HERE.md and AUTORESEARCH_MECHANICS.md (not in the file list). Stale prepare.py docstrings (immutable).
- SystemExit raised from inside `evaluate` after in-process DSN tampering (SR-16).

## 5. Standing risks

- **SR-1:** no edits to immutable files. Running `prepare.py` is fine.
- **SR-2:** no new SQL is expected; any that is added must be a module constant.
- **SR-5:** purge transaction unchanged. Interrupted transactions roll back on close (RR-12).
- **SR-6:** RR-10, RR-14, the test rules in §3, and `import tests`.
- **SR-7:** the freshness check is read-only.
- **SR-8 / SR-12:** no DSN in messages. `DATABASE_URL` is passed inline only, and `.env` is never written.
- **SR-10:** stdlib only (`signal`, `threading`).
- **SR-13:** the TSV format and statuses are unchanged and the mirror is untouched.
- **SR-14:** RR-13.
- **SR-15:** quantization changes only the decision's precision, not the metric.
- **SR-16:** RR-17.
- **CI:** offline suite on Python 3.11, so avoid APIs from 3.12 and later.
- **Screener:** shares no code with this track; its 23-test suite must stay green.
