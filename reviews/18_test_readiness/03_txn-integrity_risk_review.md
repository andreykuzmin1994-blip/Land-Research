# Risk review: txn-integrity (Tier 2), Agent 1

Session: between runs, full tier, reviewer role. I wrote no code and changed no repo file. Base is `29c051e`, branch `claude/tender-mayer-1rd4y4`, and no run is active.
Invariants in force: CLAUDE.md card. prepare.py, parameters.json, sources.json and program.md are untouchable. SR-1..SR-16 apply.

## 0. What I verified myself on real PG 16 / PostGIS 3.4 (container lr-txn-integrity, now stopped)

The probe scripts are in this directory: `probe_pg.py`, `probe_a.py`, `probe_bc.py`. They are throwaway files and are not in the repo.

| Claim | Result |
|---|---|
| (a) Two files, the 2nd has `net_absorption_t12_sf=99999999999999999999` | Statuses `['loaded','failed']`. File 1 sits in ARCHIVED/ and file 2 in FAILED/ with its `.error.json`. **market_context has 0 rows**. CONFIRMED |
| (b) `run_scoring_cycle`, 3 parcels, the 2nd parcel has a NaN sub-score | Statuses scored/error/scored. Only parcel 3 persisted, so **parcel 1 was silently lost**. Postgres error: `invalid input syntax for type json`. CONFIRMED |
| (c) Memo with two PASS rows per parcel at the same `scored_at` | Memo says "Total scored parcels: **4**" and lists each parcel twice. The metric-style DISTINCT ON gives **2**. CONFIRMED |
| psycopg 3.3.6 semantics | After an implicit SELECT, a failed `with conn.transaction()` leaves the connection INTRANS and healthy. A following `conn.rollback()` then wipes the earlier block's rows. `conn.commit()` inside `transaction()` raises ProgrammingError. `transaction()` started from IDLE runs a real BEGIN/COMMIT. |
| `now()` | Constant within a transaction. Every parcel_scores row in one scoring cycle therefore shares `scored_at`. |
| **`'NaN'::numeric >= 60` is TRUE** (and so is a Python NaN parameter) | Postgres sorts NaN above every number |
| bigint and integer overflow | Overflow raises NumericValueOutOfRange. Python `inf` is stored in NUMERIC as `Infinity`. |
| Validators (no database) | `'nan'` in an int-coerced field raises an **uncaught ValueError** (`inf` raises OverflowError). NaN/inf is **accepted** for asking_rent, starting_rent, acres, price_per_acre and cap_rate. `days_on_market=3e9` and `lease_term_months=1e10` are accepted and the INTEGER column then overflows. NaN `price_per_acre` makes PERCENTILE_CONT return NaN, so S8 becomes 0 (a fabricated value). |
| NUL in TEXT | psycopg raises DataError on the client side. A NUL inside a comps `raw` JSON value (written as `\u0000`) fails **on the server** with UntranslatableCharacter, which makes it a usable fault trigger after the fix. |

## 1. Risks, most severe first

### CRITICAL

**R1. A NaN composite would inflate the metric if NaN is only sanitized at JSON time.** Postgres treats NaN as greater than every number. So `composite_score >= %s` in `prepare._LATEST_SCORE_FILTER` counts a NaN composite as passing. Today the jsonb rejection is an accidental guard: a NaN sub-score kills the INSERT. Suppose the fix only sanitizes `json.dumps` (an encoder that maps NaN to null). The row would then insert with `composite_score = NaN` and could count toward the metric (SR-15). prepare.py cannot be changed here.
*Mitigation:* normalize non-finite sub-scores to `None` right after the sub_scores dict is built. Do it **before** `_compute_composite`, `_compute_confidence`, strategy fit and actionability, so every consumer sees the value that gets stored. Also guard composite and confidence (non-finite becomes `None`). Use `json.dumps(..., allow_nan=False)` for the three JSONB payloads, so any remaining non-finite value fails loudly for that parcel and is never stored. This follows program.md L731: "leave it null and flag it". The existing per-null-sub-score `data_gap` flag provides the flag. It changes nothing for finite inputs.

**R2. The live check could write to production.** `prepare._get_connection_dsn()` calls `load_dotenv(.env)`. If the shell lacks DATABASE_URL, the pipeline entry points the check must call (`run_ingestion_cycle`, `run_scoring_cycle`, `generate_strategy_memo`) would connect to the .env DSN. `run_scoring_cycle('atlanta')` scores **every** atlanta parcel that needs scoring, and ingestion upserts markets and submarkets. (SR-6, SR-12)
*Mitigation (the guard, built from scratch; `test_postgis_smoke.py` has none):*
- Read DATABASE_URL from `os.environ` **at startup, before any dotenv load**. Refuse if it is missing.
- Parse it with `psycopg.conninfo.conninfo_to_dict`, which handles both URI and key=value forms.
- Require an explicit host in {localhost, 127.0.0.1, ::1} or an absolute unix-socket directory.
- Refuse a multi-host list containing any non-local entry, a non-loopback `hostaddr`, and `service=`.
- After connecting, check that `conn.info.hostaddr` is loopback. In Docker the server-side `inet_server_addr()` is the container IP, so do not use it.
- Allow non-local DSNs only through a loudly named opt-in env var.
- Print only `prepare._mask_dsn(...)`.
- Even on localhost, run the same "needs scoring" selection before `run_scoring_cycle` and **abort if it would touch any parcel the check did not create**.
- CI's `postgresql://lr:lr_pw@localhost:5432/lr_test` must pass the guard.

### HIGH

**R3. The obvious ingest fixes break pinned tests or psycopg.** Agent 2 cannot edit `tests/test_discovery.py`, which pins:
- `fake.commits == 1` per happy file load (L2019, L2524), so an extra `conn.commit()` inside the loader breaks it;
- the file being gone from intake when the loader returns (L2021+), so deferring the archive out of the loader breaks it.

psycopg itself also forbids `commit()` inside `transaction()`.
*Mitigation (recommended design):* in `run_ingestion_cycle`, end the implicit transaction opened by the collision SELECT (commit) **before** dispatching loaders. Each per-file `with conn.transaction()` then becomes a real top-level BEGIN/COMMIT, and the file is archived only after its rows are durable. Remove the explicit `conn.rollback()` from both loader except-handlers, because the context manager already rolled back its own scope. Keep the final commit, which is now harmless, and rewrite its stale comment at L1553-1556. This changes nothing on FakeConnection that tests assert.

**R4. The fix depends on the caller, so it can silently regress.** Any future statement on the shared connection outside a `transaction()` block, issued after the guard commit, turns per-file blocks back into savepoints, and archive-before-commit returns.
*Mitigation:* add an offline test in `test_txn_integrity.py`. Its psycopg-faithful fake should model IDLE/INTRANS/INERROR, top-level vs nested blocks, savepoint rollback, commit-forbidden-in-block and a committed-rows ledger. It drives `run_ingestion_cycle` and asserts that, for each file, the commit event comes before the move event, and that file 1's rows are committed after file 2 fails. Optional structural hardening: set `conn.autocommit = True` after the guard commit, or make the commit depend on transaction status.

**R5. Poison-pill crash, which exists before any fix.** `'nan'` or `'inf'` in any int-coerced CoStar cell raises an uncaught ValueError or OverflowError, because the validation loop has no try. The whole of `run_ingestion_cycle` raises, and `evaluate()` reports `crash` for **every** experiment because the file stays in intake. The breaker then halts the loop after 5. Before the fix it also loses all earlier files.
*Mitigation:* make `_coerce_optional_decimal` return an error for any non-finite result (NaN, ±inf, `1e400`) so it never reaches `int()`. Add a regression test that runs the full cycle with no exception.

**R6. Removing the rollback leaves no recovery path when the connection is INERROR or broken.** If a savepoint rollback itself fails, or the connection drops, two things follow without the old `conn.rollback()`:
- Ingest: every remaining file in the cycle fails and is quarantined to FAILED/, which is never rescanned. That is silent data loss of good files.
- Scoring and discovery: every remaining parcel fails, and the final COMMIT on an aborted transaction quietly rolls back.

*Mitigation:* in the modified handlers, use `getattr(conn, "broken"/"closed", False)`. If the connection is broken, re-raise without quarantining, so the cycle aborts loudly and files stay in intake. If the status is INERROR, call `rollback()` and log at ERROR level. Only an INTRANS/IDLE connection, the normal case after a savepoint rollback, should do nothing. The getattr defaults keep the existing fakes working.

**R7. Pinned surface Agent 2 must not disturb.**
- `test_only_select_statements` requires the memo SQL to **start with `SELECT`**, so a `WITH latest AS` CTE fails.
- The constant must not contain `{`.
- The memo row has 15 columns in a fixed order (`_phase9_memo_row`).
- The FakeConnection fetchone/fetchall queues are sequenced, so **any new query** in the ingest or scoring paths shifts them and breaks tests.
- `_assert_only_reads` applies to the memo.

### MEDIUM

**R8. Validation bounds are broader than the spec states.**
- Bound **INTEGER (int4)** columns as well as BIGINT: `lease_term_months` and `days_on_market` are int4. BIGINT columns: `net_absorption_t12_sf` (signed), `under_construction_sf`, `proposed_sf`, `sale_price`, `asking_price`.
- Check the **coerced int that will actually be sent**, because float parsing means the text `9223372036854775807` becomes 2^63 and is correctly rejected.
- Do not reject `total_inventory_sf` or `year_built` for size. They are not stored, so rejecting them would drop good rows.
- Reject non-finite values in every NUMERIC field. NUMERIC accepts NaN/Infinity, but they poison the S8 median and the R1 path.
- These are **row-level** errors (row skipped, `data_gap` flag), the same as every existing range check (`test_row_errors_flagged_other_rows_load`).
- Rejecting non-finite values only ever adds rejections and never changes an accepted value. Note that pandas re-saves write `nan` for empty cells. Those rows will now show up as flagged row errors instead of loading NaN.

**R9. The data behind the metric moves, so runs are not comparable.** After the fix, cycles stop losing parcels, files and discoveries, so metric values move up for identical code. This is not a prepare-mutation, and SR-15 holds because the definition is unchanged. But a run whose baseline was taken before the fix is not comparable with experiments after it. Branches resumed with `make daily` or `make resume` keep their own copies of the modules.
*Operator gate:* after merging, start a **new** run tag. Never merge main into an in-flight `autoresearch/*` branch.

**R10. The live check and the smoke test interfere with each other in the same job and database.**
- The smoke test asserts the **global unscoped** `calculate_actionable_pipeline_count == 1`.
- It also leaves a fulton parcel in the `atlanta` market with two tied PASS rows.

*Mitigation:*
- Add the live step **after** the smoke step.
- Give every row a unique run prefix.
- Run the memo test on a dedicated market string, which `generate_strategy_memo` accepts.
- Scope every assertion to the check's own ids.
- Delete its own rows in `finally`, in FK order: score/log/flag/context/comps, then submarkets. Delete markets only if the check created them.

**R11. The live proof only counts if it reproduces production semantics.**
- Use the real `prepare.get_connection`: `autocommit=False`, close without commit. A replacement built on `with psycopg.connect()` **commits on exit** in psycopg 3 and would hide the bug.
- Monkeypatching is allowed only to inject faults, and must be restored in `finally`.
- Do not call `run_discovery_cycle` or `_harness_gate`, which hit the network (SR-11). Drive `_process_parcel` as the smoke test does, after an initial SELECT, then commit.
- Use a temporary CoStar base (`costar_ingest._COSTAR_BASE_DIR`, plus the research and runner bindings as `_temp_costar_base` does) and a temporary memo `output_dir`. Nothing may be written under the repo (SR-7).

**R12. Once validation catches the overflow, the stated repro no longer reaches the database.** The transaction-path proof would then pass without testing anything.
*Mitigation:* prove (a) with a **server-side** fault in the middle file:
- a comps CSV with a NUL in a text field, which gives `\u0000` in `raw` and an UntranslatableCharacter error (verified), or
- an injected failing statement.

Assert the overflow, int4 and NaN cases separately as validation row errors that raise no exception.

**R13. The memo fix has to match the metric exactly.**
- Keep it unscoped (the spec says this is deliberate).
- Pick exactly one row per parcel using the metric's ordering, `scored_at DESC, score_id DESC`, with Postgres's default NULLS FIRST, the same as `_LATEST_SCORE_CTE`.
- Keep the outer `ORDER BY composite_score DESC NULLS LAST, scored_at DESC`.
- The simplest shape that satisfies R7 is `AND ps.score_id = (SELECT score_id FROM parcel_scores WHERE parcel_id = ps.parcel_id ORDER BY scored_at DESC, score_id DESC LIMIT 1)`. It mirrors `_SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT` and uses `idx_scores_parcel_scored_at`. A DISTINCT ON derived table in `FROM (...)` is also acceptable.
- Do not reference prepare's private CTE: the memo is not the metric (SR-15).

### LOW

**R14. BudgetExceeded is swallowed.** It is a RuntimeError, so the per-file and per-parcel `except Exception` handlers would catch it if `wall_clock_budget` ever wrapped evaluate. A good file would be quarantined and the budget ignored. Nothing wires this up today. Optional fix: `except prepare.BudgetExceeded: raise` ahead of the generic handler.

**R15. Possible PII in error output.** `.error.json` and the logs embed `str(psycopg error)`, which includes DETAIL/CONTEXT parameter fragments (raw row JSON with buyer, seller and broker names) (SR-8). This existed before the change. New error paths must not add raw values. Consider using `diag.message_primary`.

**R16. Archive failure after commit.** An `_archive_file` OSError after the commit propagates and aborts the cycle. The file is re-ingested next time, which is idempotent per SR-9, but its research_log and flag rows are duplicated. This is acceptable; document it or report it per file.

**R17. NaN inputs to the band functions become 0.** S4, S5, S6 and S8 map NaN input to 0, which is fabricated. Rejecting non-finite values at ingest removes the main source. Changing the band functions themselves would change scoring semantics and is out of scope.

## 2. Corrections to the spec as written

1. "Reject values outside BIGINT": also bound **INTEGER** columns, and reject **non-finite** values in every numeric field. Today `nan` in an int column is an uncaught crash, a poison pill that halts the loop (R5, R8).
2. "So such files fail validation": out-of-range values must be **row-level** validation errors, consistent with every existing check and pinned tests, not file-level failures. As a result, the bigint repro cannot prove the transaction fix after the change, and a server-side fault is needed (R12).
3. "The same local-host guard idea" as `test_postgis_smoke.py`: that script **has no guard**. Build one, and account for prepare's `.env` fallback (R2).
4. "Store non-finite sub-scores as null": yes, but normalize **before** composite, confidence, strategy and actionability, not in the serializer. A NaN composite counts in the metric (R1).
5. Do **not** switch scoring or discovery to per-parcel commits. That would make partial rows durable on crash or timeout (relying on the purge and leaving orphans) and give each parcel its own `scored_at`. Keep the single end-of-cycle commit and stop the except handlers from rolling back the outer transaction. Ingest is the exception: it needs a durable per-file commit **before** the archive move.
6. Scope of (b): only the existing write-phase except branches. Server errors in the read phase (S2 query, parcel fetch) stay fatal to the cycle. That is a loud crash, not silent loss, and is acceptable. Do not widen the catch to Python compute errors, because that turns loud crashes into silent partial cycles.
7. Trigger paths: `costar_ingest.py` and `reporting.py` are **already** in `discovery-fulton.yml`. Add only `tests/live_txn_check.py`, to **both** the `push` and `pull_request` lists.
8. (c) is reproducible by construction (memo 4 vs metric 2), so the fix is required. It cannot use `WITH` (R7).

## 3. Tests required beyond the happy path

Offline tests go in `tests/test_txn_integrity.py`. Use a psycopg-faithful fake and hermetic temp dirs, with no new fixture files.
- **(a) Isolation:**
  - Two-file and three-file cycles with a middle server-side failure: the others are committed, the middle file is in FAILED/ with `.error.json`, and the others are in ARCHIVED/.
  - The same across export types (stats OK, land_sales fails, leasing OK).
  - A non-database exception inside the block (for example a `_slugify` ValueError) is isolated the same way.
  - The commit comes before the archive event for every file.
  - SR-9: the DELETE comes before the INSERT inside each file's transaction.
- **(a) Hardening:** with a broken connection, the remaining files stay in intake (R6). An archive OSError after commit leaves the rows committed.
- **(a) Validation, every export type and every int/decimal field:**
  - Accepted: the max, and negative absorption.
  - Rejected: max+1 and min-1 (int4 and int8); `nan`, `NaN`, `inf`, `-inf` and `1e400`.
  - Unchanged: `total_inventory_sf`.
  - A full cycle with `nan` in an int column raises nothing.
- **(b) Isolation:**
  - `run_scoring_cycle`: middle parcel fails in the write block, parcels 1 and 3 committed, the outer transaction is never rolled back, and the final commit happens exactly once.
  - The same for `_process_parcel`, with all three handlers covered.
  - INERROR is handled as in R6.
- **(b) NaN and ±inf sub-scores:**
  - The value is stored as JSON null, composite is finite or None, confidence goes down, a `data_gap` flag is raised, and status is `scored`.
  - Scan every executed parameter: no `NaN` or `Infinity` strings.
- **(b) Byte-identical finite results:** for fixed finite fixtures, the INSERT parameters must match exactly the output of base `29c051e`. The existing cache vs no-cache equivalence tests stay green.
- **(c) Memo SQL static check:** the statement starts with SELECT; it contains `score_id DESC` and `scored_at DESC` and no `MAX(scored_at)`, `run_tag` or `{`; it has one `%s` and the outer ORDER BY is unchanged.
- **Guard unit tests:** importing `tests/live_txn_check.py` must have no side effects. Cases to cover:
  - localhost, 127.0.0.1, ::1 and a socket directory are accepted;
  - a remote host, a mixed multi-host list, a missing host, `hostaddr` and `service=` are refused;
  - the opt-in env var works;
  - a missing DATABASE_URL is refused, with no `.env` read.

Live checks (`tests/live_txn_check.py`) cover (a), (b) and (c) as in R10-R12, plus "memo count == DISTINCT ON count for own parcels, including rows from another run_tag", which shows the memo is still unscoped.

## 4. Go/no-go gates for Agent 3

G1. `git diff --stat 29c051e` touches only `costar_ingest.py`, `research.py`, `reporting.py`, `.github/workflows/discovery-fulton.yml`, `tests/test_txn_integrity.py` and `tests/live_txn_check.py`. These are untouched: prepare.py, parameters.json, sources.json, program.md, COSTAR_INGESTION_CONTRACT.md, `tests/test_discovery.py`, fixtures, screener/ and requirements.txt (SR-10). There is no experiment_log.tsv, and `git status` shows no `costar_exports/`, `rankings/`, `snapshots/` or `sources/` leftovers. Ignore `.claude/worktrees/`.
G2. The root suite is green at 636+N, and so is the screener suite at 23. No existing test was edited.
G3. The new offline tests **fail on base `29c051e`** modules and pass after the fix (run them against the stashed base modules).
G4. `live_txn_check.py` **fails on base** and **passes after**, against local PostGIS on 55443, through the real `prepare.get_connection`. Both outputs are captured in the decision note, and the container is stopped.
G5. (a) is proven on real PG with a server-side fault: earlier and later files persist, and archiving happens only after the commit. The failing file goes to FAILED/ with `.error.json`. Overflow, int4 and non-finite values are row-level validation errors, and there are no uncaught exceptions.
G6. (b) is proven on real PG: a failing parcel or feature does not remove the others. The NaN parcel persists with a null sub-score, and no `composite_score = 'NaN'` row exists.
G7. (c) is proven on real PG: the memo selects one row per parcel, matching the metric's tie-break, and stays unscoped.
G8. No unconditional `conn.rollback()` of the shared connection remains in the six per-file and per-parcel handlers (2 loaders, 3 in `_process_parcel`, 1 in `score_parcel`).
G9. Commit granularity in research.py is unchanged: one end-of-cycle commit and no per-parcel commits.
G10. Finite scoring output is byte-identical to base, proven by a test.
G11. No new `research_log.action_type` or `flag_type` values. SR-14 would need a program.md edit, which is forbidden.
G12. SR-2 holds: new pipeline SQL is module-level constants, the memo constant starts with SELECT, and the AST scanner is green.
G13. The live guard passes the R2 checklist, accepts CI's `localhost` DSN, and prints only a masked DSN. The check uses temp dirs, cleans up its own rows, and makes no network calls.
G14. The workflow diff only adds the step after the smoke step and adds the path to both trigger lists. Services, env and the other jobs are unchanged, and the YAML parses.
G15. No model identifiers appear in code, comments, docs or commit messages. The stale savepoint comment in `run_ingestion_cycle` has been updated.

## 5. Out of scope (flag to the human)

- **Prepare-mutation candidate:** add `AND composite_score <> 'NaN'` to `_LATEST_SCORE_FILTER`, and correct prepare.py's comment that "inserts one row per parcel per cycle in its own transaction". In reality there is one outer transaction per cycle with a shared `scored_at`.
- **Lost historical ingestions:** these cannot be recovered automatically. Their research_log rows were rolled back as well. An operator audit should compare ARCHIVED/ against market_context and the comps tables, then move the affected files back into intake. Re-ingest is idempotent.
- **Contract mismatch:** COSTAR_INGESTION_CONTRACT.md says partial data must not be used, but the implementation loads the valid rows (existing behavior, pinned by tests).
- **Ad hoc scoring never saves:** `score_parcel(conn=None)` never persists, because it closes without committing. It has no production callers.
- **Informational count is wrong:** the `make db-stats` "actionable" count includes every row, not only each parcel's latest.
- **Concurrent cycles:** two concurrent ingestion cycles race on file moves and on DELETE-then-INSERT.
- **Band functions:** NaN input to the band functions yields 0. This is a scoring-semantics change for a later Tier 1 review.
- **Conflicting instructions on model names:** STANDING_RISKS.md asks Tier 2 decision notes to "record which model ran", but this task forbids model names in docs. The orchestrator should decide.
