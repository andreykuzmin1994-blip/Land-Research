# Risk review: runner-followups (Tier 2 for runner.py purge/TSV logic, Tier 0 for ops tooling), Agent 1

Base `bf4d29b`. I am Agent 1: I wrote no code and changed no repo file. Root suite at base: 847 OK (re-run here).

Scope:
- (a) pending-purge durability: Tier 2, SR-13/SR-15.
- (b) discovery count and the description cap: Tier 2, because it writes TSV descriptions.
- (c) stale docs.
- (d) `column` in the Makefile: Tier 0.
- (e) mirror-restore message: Tier 0 plus a small runner/cli change.

## What I reproduced (offline, scratch only)

1. **Truncation destroys both tokens on a realistic DB-blip crash row.** The test row: `status=crash`, psycopg error "OperationalError: consuming input failed: server closed the connection unexpectedly … processing the request.", `run=atl-2026-10-29`, `exp=exp-…`.
   - `_format_loop_description` gives 250 chars, and the TSV writer cuts that to 200 (`… | run…`).
   - `_row_run_tag` returns None, and `_parse_markers_from_description` returns `(None, None)`.
   - This is exactly the row whose purge is most likely to fail too (same blip). A TSV-rebuilt queue cannot see it. So (b)'s token preservation is a **prerequisite of (a)**, not a nit.
2. **The real Fulton shape produces no `discovery_fulton=` token.** A `per_county` value that is not a dict, e.g. `{'fulton': 5}`, makes `_format_loop_description` raise `AttributeError`. That call sits outside any try in `experiment_loop` (runner.py:1889/1910). It runs after the purge and before the TSV append, so a strange sandbox return value kills the NEVER STOP loop and loses the row.
3. **GNU make exits 2 for ANY failing recipe.** I checked with `exit 1`, `exit 3` and command-not-found: all three exit 2. `column` is absent in this container.
4. **The documented agent flow restarts the process for every experiment** (README §"Karpathy iteration loop" step 4: `make loop MAX=1`). So the in-memory `pending_purges` never survives even one boundary there, and the durable rebuild is the real fix. It also runs at every single experiment start (see RF-6).

## Ranked risks

| ID | Sev | Req | Concern | Mitigation |
|----|-----|-----|---------|-----------|
| RF-1 | HIGH | a+b | Repro 1: the 200-char cap cuts `run=`/`exp=` on crash rows that carry long error text. The rebuild then misses exactly the DB-blip crashes. A cut can also leave a valid-looking PREFIX tag (`run=atl-2026-1…`). `_DESCRIPTION_RUN_RE` stops before `…` and returns `atl-2026-1`, so a truncated keep row can be credited to the wrong run. | Budget in the composers (`_format_loop_description`, baseline description), never in `_sanitize_description`:<br>- Build the suffix ` \| run=<tag> \| exp=<id>` first.<br>- Sanitize the free-text error piece (collapse whitespace) before measuring it.<br>- Shrink the error text first (with `…`), then drop the discovery/scored pieces.<br>- Never drop `run=`, `exp=` or `stamp_audit_violations=` (the SR-16 monitor).<br>- The final length must be ≤ 200 before the writer's sanitize runs.<br>- For a pathological tag (`_AUTORESEARCH_BRANCH_RE` has no length bound), either refuse at loop start (SetupError) or record it as a residual. |
| RF-2 | HIGH | a | **The rebuilt queue becomes a purge instruction read from a file (SR-13/SR-16).** Ways it can go wrong:<br>- the first-match `_row_run_tag`;<br>- error text containing ` run=<x>` or `exp=exp-…`, from a sandbox exception;<br>- truncated prefix tags;<br>- a forged TSV line `discard … exp=<KEPT id>`.<br>Any of these can purge the wrong rows, and deleting a KEPT experiment's rows corrupts the anchor. | Parse ONLY the trailing runner-written tokens. Use an anchored regex `(?:^\|\s)run=([a-z0-9._-]+) \| exp=(exp-\d{8}T\d{6}Z-[0-9a-f]{6})$`, or last-match plus `_EXPERIMENT_ID_SHAPE_RE`. Then:<br>- the tag must equal the current run_tag;<br>- status must be in {discard, crash, timeout}; this includes `baseline \| …` crash/timeout attempts;<br>- exclude every id that appears on any baseline/keep row;<br>- dedup while keeping order;<br>- read the TSV only, never the mirror (`TestMirrorStaticGuards`: runner reads the mirror exactly twice);<br>- delete only through `_purge_experiment_scores` (the one sanctioned DELETE).<br>Rows whose tokens are malformed or truncated are skipped and logged, never guessed. |
| RF-3 | HIGH | a | **SR-6 hermeticity.** The new purge call sites (start-of-loop rebuild, exit-time retry, pre-baseline reconcile) run the REAL `_purge_experiment_scores` in every loop or baseline test that does not patch it. Example: `TestHasRunBaseline.test_helper_agrees_with_the_loop_gate` runs `experiment_loop(max_iterations=0)` with no purge patch. `prepare.get_connection` loads `.env`, so on an operator machine that is a live DELETE plus a research_log INSERT. In this container it is a SystemExit, and that hides the problem if the new retry helper swallows SystemExit: green here, live writes on a laptop. | Never override a returned dict's missing `experiment_id` with the runner's id. RR-14 still applies: old fixtures without ids must not start carrying `exp=` tokens. Patch the purge in every new test and audit all 20 `experiment_loop(` call sites. **Gate G5**: a record-only guard runs over the whole suite (below). |
| RF-4 | MED-HIGH | a | **The spec has a gap: a retry is not a guarantee.** With "retry whenever db ok", a purge can still fail while `SELECT POSTGIS_VERSION()` succeeds (pooler blip, lock, privileges). The next experiment is then evaluated on residue, which is the same mis-credited `keep` the finding describes. This only becomes rarer; it is not closed. | Do not run `evaluate` (loop iteration or bootstrap/gated baseline) while the queue is non-empty after the retry. Count it as a setup failure (sleep, `continue`), so a persistent failure trips the existing infra breaker: halt row plus ERROR listing the ids. There is no FK into `parcel_scores`, so a per-id permanent failure is implausible; failures are infra. If Agent 3 declines, record the residual explicitly. |
| RF-5 | MED | a | **Exit-time retry hazards:**<br>(i) A vanished DSN means `get_connection` raises SystemExit. That escapes `except Exception`, cli exits 2, and make prints "loop refused at setup", which is false.<br>(ii) The purge has no statement_timeout. A hang before the halt row loses the accounting row and `make halt` cannot stop it.<br>(iii) A `finally` that raises masks the original exception or summary.<br>(iv) On KeyboardInterrupt it must not block for long or swallow the interrupt.<br>(v) Saying "purged" when nothing was attempted. | Write the halt row FIRST, then do the exit retry.<br>Gate the retry on `prepare.dsn_available()`; catch `Exception` only, or SystemExit narrowly around the connect, consistent with `_check_db_connection`. Never catch BaseException.<br>Stop at the first failure (fail fast) and log every remaining id once at ERROR. Add `unpurged_experiment_ids` to the returned summary (JSON-safe; cli prints it).<br>Wrap the exit retry in try/except so it never replaces a propagating exception. Let KeyboardInterrupt propagate.<br>Distinguish "purged N" / "still pending (failed)" / "not attempted (no DSN or DB check failed)". |
| RF-6 | MED | a | **"DELETE by experiment_id is idempotent" is true, but it is not free.** Under the MAX=1 flow, re-purging every historical non-kept id at each start costs O(N) connections and O(N) DELETEs. Each DELETE is a full scan: there is no index on `parcel_scores.experiment_id`, and adding one is a prepare-mutation. It also writes O(N) zero-row `experiment_purge` research_log rows per start, about N²/2 per run, which inflates `db-stats` and the research_log breakdown (SR-9 noise). At roughly 70 discards a day that is seconds to minutes per start. | Preferred: one SR-2 constant residue read, `SELECT experiment_id FROM parcel_scores WHERE run_tag = %s AND experiment_id = ANY(%s) GROUP BY experiment_id`. It uses `idx_scores_run_parcel_scored_at` and only reads, so the metric stays untouched (SR-15). Then purge only the ids that still have rows. FAIL CLOSED: if the read fails, every candidate stays pending. Minimum fallback: suppress the research_log row for zero-row reconcile purges. Gate G6. |
| RF-7 | MED | a | **The gate on the shape of `verify_setup`.** `iter_setup["checks"]["db"]["status"]` raises KeyError on the many existing mocks that use `checks: {}`, and `.get` chains change their retry semantics. `verify_setup` itself can raise `SetupError` (git), and `_git_head_commit()` (strict) runs AFTER the purge and BEFORE the TSV append. If git fails right after a failed purge, the id is in neither the TSV nor the queue. | Read it defensively (`(setup.get("checks") or {}).get("db") or {}`). In new tests, model db/harness explicitly. Put the exit retry in a `finally` around the loop body so that, after an exception exit, the id is at least attempted once and logged at ERROR. |
| RF-8 | MED | b | **`_format_loop_description` must be total.** Sandbox return values are untrusted (repro 2). County keys come from the sandbox's `_MARKET_TO_COUNTIES`, so a key like `x run=evil` injects a token: `_row_run_tag` takes the first match, which gives `evil`. | Use isinstance checks at every level. Print counts only when they are `int` and not `bool`. Print only county keys matching `^[a-z0-9_]+$`. Any unexpected shape is skipped, never raised, and the tail tokens are always appended. |
| RF-9 | MED | b | **Byte compatibility with mirror and backfill (SR-13).** The mirror stores `_validate_log_row` output, and backfill dedups on exact 7-column values. Changing `_sanitize_description` changes re-validated history, which leaves permanent mirror duplicates, and the mirror has no sanctioned delete. | Leave `_sanitize_description` unchanged. If it must change, it has to stay byte-identical on every input of ≤ 200 chars and be idempotent. Keep the TSV format and statuses unchanged and add no new TSV row kind: `TestMirrorCallSitePairing` expects exactly 4 append sites. |
| RF-10 | MED | a | **The baseline-attempt remark is real.** A crash or timeout attempt whose scoring cycle COMMITTED (the crash came at the metric read, or during memo or metric under timeout) and whose purge raised leaves rows for this run. A retried `make baseline` skips those parcels (run-scoped selection, research.py ~861) and counts the leftovers. Makefile:138 "(row logged + purged)" and cli.py:195 are wrong when the purge raised. | `run_gated_baseline`, after verify and under the lock, and the loop's bootstrap both reconcile this run's queue before evaluating, and refuse with SetupError (exit 2) if leftovers remain. Rebuild after the bootstrap's TSV re-read so that the bootstrap's own failed purge is included. Fix the operator text. Add no keys to the TSV row dict. |
| RF-11 | LOW-MED | a | **A late purge breaks comparability.** Purging leftovers that came before a recorded keep or baseline changes the metric under that anchor, so later decisions systematically read as worse. Runs already in flight that predate this fix may hold such leftovers. | When a reconcile deletes rows (>0) for an id OLDER than the latest anchor, log ERROR: "anchor <row> recorded while residue of <id> was present; start a new run". In the decision note: start a new run after merging, and never merge main into an in-flight `autoresearch/*` branch. |
| RF-12 | LOW | d | **The `column` fallback.**<br>- `orient` is the light-tier "no DB" path. A fallback to `python cli.py log` imports runner, which needs psycopg and requests, so a fresh clone fails.<br>- The `.halt` NOTE in `status`/`orient` is skipped today when `column` fails.<br>- Make cannot carry the cli codes 1/2/3 (repro 3); they exist only in the echoed `(exit N)` text.<br>- Running targets from tests with cwd = repo root could create `experiment_log.tsv` there. | One guarded rendering: `command -v column` first, else a stdlib or awk fallback (no runner import in `orient`). Every TSV-present path exits 0. `make baseline` success runs `log` non-fatally (`-$(MAKE)` or `\|\| true`) and still exits 0, and the failure path still exits non-zero. Tests run `make -f <abs Makefile>` in a temp cwd only. |
| RF-13 | LOW | e | **mirror-restore.** A broad `except RuntimeError` also covers `SetupError`, `LoopLockError` and `BudgetExceeded`. Any refusal must come before a DB connect. | Add a narrow subclass `class …(RuntimeError)` so `TestRestore`'s `assertRaises(RuntimeError)` stays green, plus a `cli.py` subcommand (or Makefile delegation) that prints one stderr line and returns non-zero (document the code). The TSV must stay byte-identical and no connection may be attempted. |
| RF-14 | LOW | c | **Doc accuracy.**<br>- RR-12 **(ii)** also has a stale ingestion clause ("discovery and ingestion commit only at the end"), and §4's bullet "(RR-12 iii)" is stale too.<br>- The corrected facts: discovery AND scoring still roll back a whole interrupted cycle (one outer transaction). Ingestion commits per file before archiving (686174a, costar_ingest.py ~1633), so an interrupt loses only the in-flight file's uncommitted rows (the file stays in intake). A file committed but not yet archived is re-ingested idempotently. Archives made by code before 686174a may lack rows (03 note "Past losses").<br>- The `_ExperimentBudgetInterrupt` docstring: the six txn-track handlers now re-raise `prepare.BudgetExceeded`, but `except Exception` sites remain (discovery log-write handlers, harness gate, the evaluate memo handler, future sandbox edits), so the BaseException design still holds.<br>- The `_check_costar_freshness` claim is still TRUE (pre-fix archives, files moved by hand). Leave it, or only add the reason. | Append dated "Update (2026-10-29): …" lines and do not rewrite any history. In docstrings, name functions, not line numbers. |
| RF-15 | LOW | process | Tier 2 needs a decision note, but the file list allows the 02 notes only for the dated correction. Another track (tests-ci-reporting) may touch the Makefile or CI. | Agent 3 confirms with the orchestrator where the new decision note goes (do not put it in the 02 notes), and checks for overlapping edits before committing. No model names. |

## SR checklist (only deviations and specifics)

- **SR-1**: none of the invariant files are touched. runner.py is changed between runs.
- **SR-2**: any residue read is a module constant with `%s` and `ANY(%s)`. No new DELETE.
- **SR-5**: the purge transaction is unchanged (DELETE plus research_log, one commit).
- **SR-6**: see RF-3. Patch `time.sleep`; no real 60 s sleeps.
- **SR-8**: ids and exception text only. Logs never contain a DSN, and the crash-text budget only shortens the free text.
- **SR-9**: see RF-6.
- **SR-10**: stdlib only, and no new tools (the awk/stdlib fallback).
- **SR-13**: see RF-2 and RF-9. The purge stays the only deletion, and the TSV and mirror stay append-only and unchanged in format.
- **SR-14**: no new `action_type`; reconcile purges reuse `experiment_purge`.
- **SR-15**: neither the metric nor the purge rule changes (non-kept rows, deleted by `experiment_id`). It is not a prepare-mutation.
- **SR-16**: the forged-row vector (RF-2) is mitigated by the keep/baseline exclusion, and the stamp-audit token is preserved.
- **Never purge from read-only, lock-free paths**: `verify_setup`, `current_run_has_baseline` (the loop-bg pre-flight) and status/orient/log.

## Tests required (append a new TestCase class in `tests/test_runner_ratchet.py`; hermetic)

- **A1 (must FAIL at bf4d29b).**
  - Setup: A is discarded and its purge raises once. Verify then returns harness-fail with `checks.db.status == "ok"`, three times.
  - The purge of A succeeds during the streak, before the halt row and before any evaluate.
  - Rewrite `test_pending_purge_retry_waits_for_a_passing_verify` to assert this behaviour, not the call order.
- **A2.** The DB fails three times:
  - no purge is attempted while the DSN is missing;
  - a halt row is written;
  - ERROR names the id (`assertLogs`);
  - the summary lists it;
  - nothing says "purged".
- **A3, the durable queue across processes.** Run 1 (`max_iterations=1`): the purge fails both in the iteration and at exit. Run 2 is a fresh call: the purge of A happens BEFORE the first evaluate.
- **A4, the rebuild filters.** Only this run's discard/crash/timeout rows qualify, including `baseline | …` attempts. Check that these are excluded:
  - other runs, legacy rows without a tag, and keep/baseline/halt rows;
  - `exp=e1`, `exp=exp-2026…`, and a forged `run=test exp=exp-…` earlier in the error text;
  - ids that also appear on a keep row.
  - Duplicates are removed.
- **A5, every exit path.** Halt sentinel, `max_iterations`, the 7-day exit (patch the constant), the F5 breaker, the infra halt, and an exception exit (`_git_head_commit` raising after a failed purge) each:
  - attempt the retry once;
  - write the halt row first;
  - never mask the original exception;
  - let KeyboardInterrupt propagate.
- **A6 (if RF-4 is adopted).** The purge keeps failing while the DB is ok, so evaluate is never called and the breaker trips.
- **A7.**
  - `run_gated_baseline` with a crash attempt row and a valid id purges before evaluating.
  - If that purge fails, it raises SetupError with no evaluate and no TSV row.
  - The operator text no longer claims "purged" (static check).
- **A8 (if RF-6 is adopted).** 50 clean ids give 1 read, 0 purges and 0 research_log rows. Leftovers on 2 ids purge exactly those 2. A read failure fails closed.
- **B1.** Use the real `_discover_fulton` shape: `discovery_fulton=<totals.discovery>`. A county-level abort (`field_mapping_drift`) or skip, or a cycle-level abort, prints no number.
- **B2.** Garbage shapes (int, list, None, str, bool, a totals value that is not a dict, an injecting county key) never raise, and the description still ends with ` | run=<tag> | exp=<id>`.
- **B3, a property test.** Error lengths 0–600, tag lengths up to 64, many counties, and the realistic 250-char psycopg text. Run each case through `append_experiment_log_row` and back through `read_experiment_log`, then check:
  - `_row_run_tag` equals the tag;
  - `_parse_markers_from_description` equals (tag, id);
  - the stamp token is preserved;
  - the length is ≤ 200.
- **B4.** `_sanitize_description` outputs are unchanged on a fixed corpus, and it is idempotent.
- **B5.** Rewrite `test_truncated_description_anchors_identically_on_both_paths`. It currently asserts that `run=` IS dropped, using the fake `{'inserted': i}` shape, so it will fail or become vacuous. Use real shapes and assert that the tokens survive and both paths anchor alike.
- **D1.** In this container (no `column`), `make -f <abs> log` and `orient`, run in a temp cwd with a TSV, exit 0 and print every row.
- **D2.** Static checks:
  - `status`, `orient` and `log` have no unguarded `| column`;
  - the `baseline` success path's `log` call is non-fatal and the failure path still exits non-zero;
  - `orient`'s fallback does not import runner.
- **E1.** mirror-restore over a non-empty TSV:
  - one stderr line;
  - no `Traceback`;
  - rc ≠ 0;
  - the TSV is byte-identical;
  - `get_connection` is never called.
  - `TestRestore` stays green.

## Go/no-go gates for Agent 3

- **G1.** Both suites are green: root 847 plus the new tests, screener 96. No real sleeps, and only a small increase in wall time.
- **G2.** Run A1 and B3 (the realistic crash row) in a detached `bf4d29b` scratch worktree with only the new test class: they FAIL there and PASS after the fix.
- **G3.** SR-13:
  - runner.py still has exactly one `DELETE FROM parcel_scores` constant;
  - the rebuild only calls `_purge_experiment_scores`;
  - there are 4 TSV append sites;
  - `test_writer_confidence_strings_unchanged` is green;
  - `_sanitize_description` is unchanged, or B4 proves it equivalent.
- **G4.** RF-2's parse, keep/baseline exclusion, statuses and run scope are all present and covered by A4.
- **G5, SR-6.** Run the whole root suite from a scratch script that first replaces `prepare.get_connection` with a RECORD-only guard (append to a list, then raise). Mocks installed inside tests override it. The run must record zero calls, and the list must be printed at exit rather than relying on an exception, which a retry helper could swallow.
- **G6.** The decision on RF-6 is recorded (the residue read, or the minimum). Either way the TSV, the experiment ids and the research_log are not written per clean id at loop start.
- **G7.** The decision on RF-4 is recorded (evaluate gated, or the residual stated). The exit retry writes the halt row first, catches no BaseException, and logs unpurged ids at ERROR.
- **G8.** No reconcile in `verify_setup`, `current_run_has_baseline` or the read-only targets.
- **G9.** Makefile:
  - D1 and D2 pass;
  - `make -n` dry-run tests stay green;
  - no `experiment_log.tsv`, `.halt` or lock files are left in the repo;
  - `git status` shows only intended files.
- **G10.** The docs carry dated Update lines only (residual 3, RR-12 (ii)/(iii), §4 bullet). The budget docstring is accurate and the freshness docstring is not weakened.
- **G11.** Optional live proof uses only port 55480 / `lr-runner-followups` on a throwaway clone, with the DSN inline. Stop the container afterwards. No `.env`, never the main checkout, no pushes.

## Out of scope

- An index on `parcel_scores(experiment_id)`. That is a prepare-mutation for the human.
- An OS-level budget or subprocess isolation (SR-16 hardening). Cancelling server-side queries (residual 2).
- Purging research_log/flagged_items for discarded experiments (an e2e design observation).
- The memo's actionable count (reporting.py) and the live_txn_check CI refusal. Both belong to other tracks.
- Rows from `runner.evaluate` called ad hoc on a run branch (no TSV row, so they cannot be rebuilt).
- Historical truncated rows and leftovers from runs that predate this fix. The remedy is a new run.
- A statement_timeout on the purge. Optional; it would interact with RF-4's breaker.
