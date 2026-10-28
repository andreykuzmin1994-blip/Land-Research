# Tier-2 Decision Note — transaction boundaries (CoStar ingest, scoring, discovery) + memo latest row

> Decision-note format per `STANDING_RISKS.md` § Change tiers (deltas only).
> Tier 2: changes the metric's INPUTS (which ingested rows and scored
> parcels survive a cycle) and touches run-immutable modules
> (`costar_ingest.py`, `reporting.py`) between runs. Base `29c051e`; no
> `autoresearch/*` run active.

- **Roles and context independence**: risk reviewer (Agent 1,
  `03_txn-integrity_risk_review.md`), implementer (Agent 2) and adversarial
  reviewer/committer (Agent 3, this note) each ran in an independent fresh
  context that did not author the others' work, all on the session's
  strongest available model. Agent 3 applied Agent 2's patch to a clean
  worktree at `29c051e` and re-ran every check below itself.
- **Verdict**: APPROVE. Every defect reproduced on real PostgreSQL 16 /
  PostGIS 3.4 before the fix and disappeared after it.

## What changed

1. **(a) `costar_ingest.py`.** Data loss on real PG: the collision-guard
   SELECT opened an outer transaction, so each per-file
   `with conn.transaction()` was a SAVEPOINT. A failing file's
   `conn.rollback()` then discarded every earlier file of the cycle, which
   had already been reported `loaded` and moved to `ARCHIVED/` (never
   rescanned). The fix:
   - `run_ingestion_cycle` commits right after the guard, so each file's
     block is a top-level BEGIN/COMMIT and a file is archived only after its
     rows are durable.
   - Both loader handlers no longer roll back.
   - `_abort_after_failed_file_block` re-raises without quarantining on
     BudgetExceeded, a broken or closed connection, or UNKNOWN status, which
     leaves the file in intake. INERROR rolls back and logs at ERROR.
   - Validation rejects non-finite cells (`nan`/`inf`/`1e400`) and values
     outside INT4/INT8 for every INTEGER/BIGINT target column, as row-level
     errors (row skipped, `data_gap` flag). Before the fix, `nan` in an
     int-coerced cell raised an uncaught ValueError that crashed every
     cycle: a loop-halting poison pill.
2. **(b) `research.py`** (between-runs sandbox fix; scoring semantics
   unchanged):
   - The per-parcel handlers (three in `_process_parcel`, one in
     `score_parcel`) no longer roll back the cycle's shared transaction; a
     failing parcel drops only itself.
   - `_abort_cycle_after_failed_block` re-raises on a broken or closed
     connection, INERROR, UNKNOWN or BudgetExceeded.
   - Non-finite sub-scores become `None` before composite, confidence,
     strategy fit and actionability read them, and get the existing
     per-null `data_gap` flag. Composite and confidence pass through
     `_finite_or_none`, and JSONB payloads use `allow_nan=False`.
   - There is still one end-of-cycle commit and no per-parcel commits.
3. **(c) `reporting.py`.** The strategy memo now picks exactly one row per
   parcel with the metric's tie-break (`ORDER BY scored_at DESC, score_id
   DESC LIMIT 1`, a correlated subquery). It stays UNSCOPED
   (AUTORESEARCH_MECHANICS.md), still starts with SELECT, and keeps its 15
   columns and outer ORDER BY.
4. **Tests and CI.**
   - `tests/test_txn_integrity.py`: 38 offline tests on `PgFake`, a fake
     that models psycopg/PG transaction semantics.
   - `tests/live_txn_check.py`: 20 real-SQL checks behind a local-target
     guard.
   - `discovery-fulton.yml`: the live check runs as a step after the smoke
     test, and its path is added to both trigger lists.

## What Agent 3 verified independently

- **Suites (SR-6).** Root suite 674 OK (636 + 38, no existing test edited);
  screener 23 OK. The root suite is also green under Python 3.11 (CI's
  version).
- **G3.** The new tests run against the `29c051e` modules: 27/38 fail (105
  failures and 71 errors, counting subtests). The 11 that pass on base do so
  by design:
  - the finite-output golden, which was captured from base;
  - finite parsing, and the unstored `total_inventory_sf` bound;
  - the memo shape;
  - six guard tests.
- **G4: live, port 55543, through the real `prepare.get_connection`.**
  - On base modules: exit 1 with 10 FAILs:
    - `market_context` rows=0 for the earlier file;
    - the stats file was archived with 0 committed rows;
    - `ValueError: cannot convert float NaN to integer`;
    - scoring rows `{s1:0, s2:0, s3:1}`;
    - the NaN parcel was an `error` and wiped n1;
    - discovery kept only D3;
    - the memo had 5 rows against 3 from DISTINCT ON ("Total scored
      parcels: **5**").
  - On the fixed modules: exit 0, every check OK. It also passes under
    Python 3.11 with CI's `localhost` DSN form.
  - smoke → live → smoke on one database: all green, and only the smoke
    test's rows remained.
- **SR-2.** All new SQL is module-level `%s` constants, and the AST scanner
  is green.
- **SR-5 / SR-9.**
  - Ingest is now one file per transaction, as SR-5 and
    `COSTAR_EXPORTS_README.md` ("Commits the transaction, then moves the
    file") already state.
  - DELETE comes before INSERT inside each file's transaction (pinned).
  - research.py commit count is unchanged (3 call sites).
- **SR-8 / SR-12.** The live guard refuses an unset `DATABASE_URL` (it never
  reads `.env`), a remote host, a mixed host list, and a non-loopback
  `hostaddr`. Each refusal exits 2 and prints no password. The guard also
  refuses to score any parcel the check did not create.
- **SR-10.** No new dependencies.
- **SR-14.** No new `action_type` or `flag_type` values.
- **SR-15.** The metric definition is untouched; `prepare.py`,
  `parameters.json`, `sources.json` and `program.md` are unchanged.
- **Bounds coverage.** The live `information_schema` confirms the bounds
  cover every INTEGER/BIGINT target column. All NUMERIC targets are
  unconstrained, so finite values cannot overflow them.

## Phase-specific risks found and their disposition

- **R1.** PG sorts `'NaN'::numeric` above every number, so a serializer-only
  NaN fix would have inflated the metric. Mitigated: values are normalized
  before any consumer reads them.
- **R2.** The live check could have reached production through prepare's
  `.env` fallback. Mitigated by the new guard.
- **R3.** psycopg forbids `commit()` inside `transaction()`, and tests pin
  `commits==1` per file. The fix commits after the guard instead of inside
  the loader.
- **R5 / R8.** The poison pill and the INT4/INT8 bounds are covered above.
- **R12.** Once validation catches the bigint overflow, that repro no longer
  reaches the database. So (a) is proven with a SERVER-side fault (a NUL
  reaches only the JSONB `raw` column, giving UntranslatableCharacter), and
  (b) with an injected `SELECT 1/0` inside the block.

## Deviations (accepted)

- **R6 in research.py.** INERROR re-raises instead of `rollback()`. There,
  INERROR means the outer transaction holding the earlier parcels is already
  aborted, so rollback-and-continue would commit a silently partial cycle.
- **R4.** The optional autocommit hardening was declined; the fake pins the
  commit-before-archive event order instead.
- **R16.** An archive OSError after the commit aborts the cycle with the
  rows already durable. The file is re-ingested idempotently, apart from
  duplicated log and flag rows.
- **Model name.** STANDING_RISKS asks Tier-2 notes to record which model
  ran, but this task forbids naming it, so the roles are described as above.

## Reviewer fixups

- `tests/live_txn_check.py` docstring: states that the script needs the
  schema applied. The smoke step applies it in CI; run `python prepare.py`
  first when running standalone.

## Residual gaps / human follow-ups

- **Operator gate (R9).** Metric inputs change for identical `research.py`:
  cycles no longer lose files, parcels or discoveries, and poison cells
  become row errors. After merging, start a NEW run tag. Never merge main
  into an in-flight `autoresearch/*` branch; resumed branches keep their own
  module copies. The metric definition is unchanged, so this is not a
  prepare-mutation.
- **Prepare-mutation candidate (human only).**
  - Add `AND composite_score <> 'NaN'` to `_LATEST_SCORE_FILTER`.
  - Correct prepare.py's comment "one row per parcel per cycle in its own
    transaction". In fact one outer transaction covers each cycle, and every
    row shares `scored_at`.
- **Past losses.** Earlier cycles' lost ingestions are not recoverable
  automatically. Audit `ARCHIVED/` against `market_context` and the comps
  tables, then move the affected files back to intake (re-ingest is
  idempotent).
- **Behaviour change.** A pandas-style `nan` written for an empty numeric
  cell now makes that row a flagged row error, including in unstored
  columns such as `clear_height_ft`. If that turns out to be common in real
  exports, decide whether `nan` should mean null instead.
- **Docs, outside this track's files.**
  - The `COSTAR_EXPORTS_README.md` failure table does not list the new
    "connection lost or budget exceeded → cycle aborts, file stays in
    intake" case.
  - `COSTAR_INGESTION_CONTRACT.md` says partial data is never used, but
    valid rows load (pre-existing, pinned by tests).
- **Pre-existing, unchanged.**
  - R15: `.error.json` files and logs embed `str(exc)`, which includes
    PG's CONTEXT line with raw row JSON. Consider `diag.message_primary`.
  - R17: band functions map NaN input to 0.
- **Live-check limits.**
  - A loopback port tunnelled to a remote server cannot be detected. Damage
    is bounded: the check scores only its own parcels and deletes its own
    rows.
  - The check exits 2 on an `autoresearch/*` branch, where the smoke step
    already fails.
