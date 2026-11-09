# 02 — Hazards table enabled: pre-change risk register

> **Tier 2 (prepare-mutation).** Written by an independent reviewer BEFORE
> the change, in a context that did not write the proposal or the code. It
> ranks the risks, gives mitigations and go/no-go gates, and leaves a
> Disposition column for the implementer. The proposal under review is
> `reviews/26_flood_wetlands/01_prepare_mutation_proposal.md`. Model names
> are omitted. Live evidence is counts only.

## Operator authorization (recorded verbatim)

The operator's own chat message (2026-11-09), as relayed by the workflow
harness:

> 1 - Count them, jsut review, 3 - fix 4 - can you add this yourself. Start
> building what you proposed keeping my answers in mind

The numbers refer to the "Recommended sequence" of the background analysis
given to the operator. The workflow reads them as follows; this review
relies only on item 4:

| Item | Operator words | Reading |
|---|---|---|
| 1 (D3) | "Count them" | D3-01/D3-02 answered: count unlisted districts AND districts with unreadable setback values. Not part of this change. |
| 1 (D2) | "jsut review" | D2 stays as implemented: V/VE are review-only and a floodway still blocks. No flood-rule change. The null-beats-low question is NOT answered, so no new root sub-score. Not part of this change. |
| 3 | "fix" | The research.py ArcGIS error-envelope bug and audit-path bug, and the D1-11 zoning_import fetcher round. Not part of this change. |
| 4 | "can you add this yourself" | **This change.** Item 4 of the sequence is "Operator prepare-mutation for hazards (reviews/26)". The operator delegates the prepare.py edit to the agent, acting for the human between runs. The rest of item 4 stays with the operator: the NFHL/NWI downloads, `make hazards-import` against the real database, and the new run and baseline. |
| (2) | "Start building what you proposed" | The screener quick wins. Not part of this change. |

**Scope of the delegation.** The CLAUDE.md invariant says "NEVER edit
`prepare.py`" and makes prepare-mutation a HUMAN act between runs. The
operator lifted that invariant for this one edit only. The delegation
covers the exact text of the reviews/26 proposal: the two constants, the
`_ALL_DDL` entry, and the SQL mirror in STORAGE_ARCHITECTURE.md. It does
NOT cover:

- the optional metric-SQL exclusion in the proposal (not requested);
- any other prepare.py change, such as `SCHEMA_VERSION`, a refactor or a
  comment elsewhere;
- `parameters.json`, `sources.json`, `program.md`, `zoning_rules.json`,
  `building_prototypes.json` or `rules_sources.json`;
- any write to a non-local database.

The verbatim text and this scope must also be recorded in
`reviews/26_flood_wetlands/03_decision_note.md` (see gate G1).

## Standing risks (STANDING_RISKS.md)

- **SR-1 / SR-15 (deviation, operator-authorized).** prepare.py is edited
  by the agent. This is the one place this change departs from the
  contract. Mitigated by R-01 and gate G1. The metric SQL itself
  (`calculate_*`, `_LATEST_SCORE_FILTER`) must stay byte-identical.
- **SR-2.** The new DDL consists of module-level constants run by
  `apply_schema`'s existing `cur.execute(stmt)` loop. There is no new
  dynamic SQL. Enforced by `test_no_string_interpolated_sql`.
- **SR-6.** The change makes the shipped gate ON. About 50 offline tests
  assumed it was off (R-02). Hermeticity must survive: no test may need
  `DATABASE_URL` (R-05).
- **SR-9.** `CREATE TABLE/INDEX IF NOT EXISTS` keeps `apply_schema`
  idempotent. Verified live, twice (Evidence).
- **SR-10.** No new dependency. `requirements.txt` must stay unchanged.
- **SR-13 / SR-16.** Enabling arms `runner._hazard_audit`. It runs in the
  same interpreter and so carries SR-16's accepted in-process exposure,
  which is unchanged. It is also a new route by which every experiment
  crashes (R-03).
- **SR-14.** No new `action_type`.
- SR-3, SR-4, SR-5, SR-7, SR-8, SR-11 and SR-12 are untouched. SR-5:
  `hazards._query` reads under a SAVEPOINT, so a missing table cannot
  poison the scoring connection.

## Ranked register

| ID | Sev | Risk | Mitigation / gate | Disposition (implementer) |
|---|---|---|---|---|
| R-01 | blocking | **The contract boundary is crossed by the agent.** A loosely worded record turns "the operator authorized one edit" into precedent for agent prepare.py edits. Any extra byte in prepare.py (an optional exclusion, a `SCHEMA_VERSION` bump, a reformat) is unauthorized metric-layer change. | Apply exactly the proposal text (G2). Record the verbatim operator words and the delegation scope above in this folder AND in reviews/26's decision note (G1). Put a comment above the new constants citing "prepare-mutation 2026-11-09, operator-authorized (reviews/33_hazards_enabled)", in the style of the 2026-07-08 mirror comment. Do not bump `SCHEMA_VERSION`: the 2026-07-08 additive table did not (precedent `796379d`), and nothing reads it beyond CI's line presence check. Commit subject `prepare-mutation: ...`. | |
| R-02 | blocking | **About 55 tests break** when the gate turns on (measured, Evidence). They split into two kinds. (a) About 11 assert the off state by design: `test_hazards.GateTests` (4), `test_hazards_scoring.AbsentTests` (3), `test_hazards_runner.DisabledTests` (1), `test_hazards_import.RunGuardTests.test_gate_off_refuses` (1), and the `test_discovery` H6 stub tests (2). (b) About 42 have fakes that never expected the hazard read, the runner's extra audit connection or the longer site-fit SQL: `test_site_fit` (12), `test_discovery` evaluate/routing (10), `test_adjacency_scoring` (9), `test_site_fit_scoring` (5), `test_operator_decisions.D3ScoringTests` (4), `test_hazards_scoring.SiteFitTests` (1), `test_txn_integrity` (1). On top come the git-history byte pins of prepare.py (R-06). The hazard is to "fix" them by turning the gate off suite-wide. Then no test exercises the shipped state, and the change appears green while unproven. | NO suite-wide default-off: nothing in `tests/__init__.py` or a module-import side effect may patch `hazards._SOURCE_DDL`. Add ONE named helper next to `enabled()` in `tests/hazards_helpers.py` (for example `disabled()`, patching `hazards._SOURCE_DDL` to None). Apply it per class or per test, and only where the test's subject is NOT hazards: kind (b), plus the off-branch halves of kind (a). For each off test in kind (a), keep the off behaviour under `disabled()` AND add the inverse assertion on the shipped state: `hazards.enabled()` True, `schema_problem()` None, `_SOURCE_DDL` equal to the prepare constant, discovery H4/H6 `pass` with empty reason, and the import no longer refusing with exit 1. Record the count of `disabled()` uses in the decision note. | |
| R-03 | blocking (operational) | **Every experiment crashes on a DB without the table.** Once the change merges, prepare.py says the table exists, but the operator's real database has it only after `make db-check`. `verify_setup` does not check for `parcel_hazards`. Scoring degrades safely (every parcel is an "unreadable" data_gap pass, savepoint-protected). But `runner._hazard_audit` treats an unreadable read as a failure: the baseline and every experiment end `crash` with `HazardAuditFailed`. The loop's NEVER STOP then burns the run. | Operator steps put `make db-check` against the real DB FIRST, before any `make verify`/`baseline`/`daily` (G9). State the failure text in the note, so a crash on the first baseline is recognised. A `verify_setup` table-presence check would close this mechanically. It is a runner.py (Tier 2) change outside this delegation: record it as a follow-up and do not add it here. | |
| R-04 | high | **"Every score is unchanged with the table empty" is claimed but not pinned.** The five golden checks freeze pre-hazard records. With the gate on, `hazards_golden_check` prints "not comparable" (by design), and the zoning, site_fit, adjacency and building_comps checks print DIFFERS (measured). Without a replacement proof, a score change would hide behind expected text drift. | Re-prove on the shipped state against the 8ddaf69 record. Probe result, from the reviewer's scratch copy with the proposal applied (G5's target): 22/22 scored parcels have identical `parcel_scores` inserts, results (composite, confidence, sub-scores, actionability, blockers, strategy) and research_log notes; 16/16 discovery `_process_parcel` statuses are identical; and the only discovery statement delta is 2 fewer `flagged_items` INSERTs per parcel (the H4/H6 stubs). Pin this as an offline test plus an "enabled, empty table" mode of `hazards_golden_check.py`, as adjacency's check already does ("gate on + empty table: ..."). For the other four golden checks, which record non-hazard behaviour, run their case under `disabled()` inside the check, so their meaning is preserved. Document the expected delta. | |
| R-05 | high | **Hermeticity leak in the runner path.** With the gate on, `runner.evaluate` opens a second connection through `prepare.get_connection()` for the audit. In tests that mock only the metric block's connection, the real `get_connection` ran and raised `SystemExit: 2` (no DATABASE_URL): 4 `test_discovery` errors. `SystemExit` is a BaseException, so it also slips past `_hazard_audit`'s `except Exception` (the R-M1 pattern from reviews/17). In production the metric block has already connected, so a missing DSN cannot reach here first (low real risk). | Kind (b) of R-02: the runner tests either run under `disabled()` or mock `get_connection` for every call. No test may pass because a `DATABASE_URL` is set in the shell (run the suite with it unset, G4). Record the BaseException residual as a nit and do not change runner.py. | |
| R-06 | high | **The byte pins on prepare.py fail in the real worktree** (they skipped in the reviewer's history-less copy): `test_hazards_scoring.ProtectionTests.test_human_owned_and_prepare_byte_identical_to_base` (8ddaf69) and `test_adjacency_scoring.ProtectionTests.test_human_owned_and_prepare_byte_identical_to_base` (595aa08). They also pin parameters.json, sources.json, program.md and the other human-owned files. Re-pinning carelessly would drop protection for those files. | Re-pin ONLY prepare.py. Either compare prepare.py to the new commit, or compare it to base with exactly the proposal's added lines removed (better: it proves the diff is the proposal and nothing else). Keep every other file on the old base. Add a comment citing "operator decision 2026-11-09, reviews/33_hazards_enabled". `test_zz_human_files_unchanged` / `zoning_helpers.HUMAN_OWNED` do not list prepare.py: leave them as they are. STORAGE_ARCHITECTURE.md has no byte pin today (only CI path filters). | |
| R-07 | high | **Three copies of the DDL can drift**: `hazards.PROPOSED_DDL`, `prepare._DDL_PARCEL_HAZARDS` and STORAGE_ARCHITECTURE.md. The gate checks only that columns are present (`schema_problem`). A later prepare.py edit that changes a type, the SRID or the FK would pass the gate silently. The proposal's text ends `);"""`, while `PROPOSED_DDL` ends `);\n"""`: equal only after stripping. | New tests: `prepare._DDL_PARCEL_HAZARDS.strip() == hazards.PROPOSED_DDL.strip()`; `prepare._DDL_PARCEL_HAZARDS_INDEXES == hazards.PROPOSED_INDEXES`; `_ALL_DDL` order: the mirror, then the table, then its indexes, then `*_DDL_INDEXES` (the FK needs `parcels` first); and STORAGE_ARCHITECTURE.md contains the table SQL and both index lines verbatim. Do NOT edit hazards.py (protected; its behaviour must stay identical). | |
| R-08 | should-fix | **The gate reads the source text with the AST.** It is true only for a module-level `Assign`/`AnnAssign` whose value is a string literal, or a `+` concatenation of literals, that names `parcel_hazards`. An f-string, `textwrap.dedent(...)`, a function call or a definition nested in a block would silently leave it OFF, while `apply_schema` still creates the table. That is a confusing half-state. | Use a plain triple-quoted literal at module level, as in the proposal. Add a test that `hazards.ddl_from_prepare_source()` on the real prepare.py returns the constant's value (not None). | |
| R-09 | should-fix | **The canonical docs go stale.** CLAUDE.md (flood bullet), STANDING_RISKS SR-1 ("inert until a human applies"), AUTORESEARCH_MECHANICS.md (the `hazards.py` and `hazards_import.py` entries), README Status and the "Flood and wetlands" section and file tree, and reviews/26 operator steps 2-3 would all still say "inert / a human applies". A later session would then re-apply it, or distrust the gate. | Truth-up text only: "table enabled 2026-11-09 (operator-authorized prepare-mutation, reviews/33_hazards_enabled); H4/H6 stay data_gap passes until the first populated `make hazards-import`, which is the metric change". Keep every needle `ProtectionTests.test_docs_name_the_protected_files` checks ("hazards.py", "hazards_import.py", "reviews/26_flood_wetlands", the SR-1 test name). Editing these files puts the implementer at full-tier orientation (START_HERE.md). program.md is never edited. | |
| R-10 | should-fix | **The run-comparability statement could be wrong.** AUTORESEARCH_MECHANICS.md says a prepare-mutation makes earlier metric values "NOT comparable". Here, with the table empty, the count is unchanged (R-04). Only flag text and the runner's audit change. The real break is the first populated import. | Say it in the commit message, as `796379d` did ("metric UNCHANGED while parcel_hazards is empty; the first populated import is the metric change: new run + baseline"). Follow protocol steps 1-6. Step 1: no active run in this worktree (no experiment_log.tsv, no .halt). Step 3: authored on the work branch; the operator's merge to main is the commit-to-main step (precedent `796379d`). Steps 4-5: the new run and baseline come after the populated import, not after this commit. | |
| R-11 | should-fix | **Merge conflicts with sibling worktrees.** The D3 change ("count them") edits `test_site_fit`, `test_operator_decisions` and site_fit code. Item 3 edits research.py and zoning_import.py. Several of those tests are kind (b) here. A merge in either order can silently undo a `disabled()` wrap, or re-expect the off state. | Note the merge order in the decision note. Whichever lands second re-runs both suites and the five golden checks after rebasing. No test may be deleted to resolve a conflict. | |
| R-12 | nit | **The old open stub flags.** Existing "H4 flood unjoined" / "H6 wetlands unjoined" data_gaps in the operator's DB stay open. Scoring writes new "no hazards import for this parcel" data_gaps (2 per parcel). Noise in `flagged_items` and the memo. | Documented in the proposal. Restate it in the operator steps (optional resolution). No code. | |
| R-13 | nit | **Running the live checks oversteps the database constraint.** `tests/live_hazards_check.py`, `live_site_fit_check.py` and `live_adjacency_check.py` each `CREATE DATABASE` a sibling database on the local server and point `DATABASE_URL` at it. This task allows only `lr_haz` (schemas created and dropped inside it). `tests/test_postgis_smoke.py` works inside the named database. | Run `test_postgis_smoke.py` against `lr_haz`. Do not run the CREATE DATABASE checks unless the orchestrator confirms that a sibling local database is allowed. If not run, say so in the note, with this reason (not as a pass). An equivalent probe inside a schema of `lr_haz` (`options=-csearch_path=<schema>,public`) is acceptable: it is how this review applied the DDL. | |

## Go / no-go gates

All gates must hold before the implementer commits. Each gate's result
goes into `03_decision_note.md`, counts only.

- **G1 (record).** This folder carries the verbatim operator text and the
  delegation scope. `reviews/26_flood_wetlands/03_decision_note.md` gets a
  dated "Operator decisions (2026-11-09, item 4)" section with the same
  verbatim text and marks operator step 2 as done by the agent, under the
  delegation.
- **G2 (exact diff).** `git diff <base> -- prepare.py` contains exactly:
  the two constants in the proposal's text, after
  `_DDL_EXPERIMENT_LOG_MIRROR`; the two `_ALL_DDL` entries; and at most a
  provenance comment. No other prepare.py line changes. No optional
  exclusion. No `SCHEMA_VERSION` change. The metric region from
  `def calculate_actionable_pipeline_count` onwards is byte-identical
  (test). `parameters.json`, `sources.json`, `program.md`,
  `requirements.txt`, `zoning_rules.json`, `building_prototypes.json`,
  `rules_sources.json` and `screener/` are byte-identical to 4fe8080.
- **G3 (gate on).** `python -c "import hazards; print(hazards.enabled(),
  hazards.schema_problem())"` prints `True None`, and a test pins it on
  the shipped repo.
- **G4 (suites).** With `DATABASE_URL` unset:
  `python -m unittest discover -s tests` passes, at ≥ 1744 tests plus the
  new ones, and `python -m unittest discover -s screener/tests -t .`
  passes with 134. The rewiring follows R-02 (no suite-wide default-off;
  the `disabled()` count is recorded).
- **G5 (scores unchanged, table empty).** The R-04 differential is a test,
  and the "enabled, empty" mode of `tests/hazards_golden_check.py` exits
  0. The other four golden checks exit 0 with their case under
  `disabled()`. The expected delta is documented: discovery H4/H6
  `flag` → `pass` and 2 fewer flagged_items INSERTs per parcel; scoring
  writes the two "no hazards import for this parcel (FEMA NFHL / USFWS NWI
  not assessed)" data_gaps; site-fit projection text and memo flood lines
  change. Inserts, results and notes are identical.
- **G6 (live, local only).** In `lr_haz` (a schema created and dropped,
  or the database itself): `python prepare.py` exits 0 twice, and the
  second run changes nothing. `parcel_hazards` has 30 columns, both GIST
  indexes and both geometry columns `MULTIPOLYGON` 2240. Both metric lines
  print 0 on the empty DB. `tests/test_postgis_smoke.py` passes. R-13
  decides whether the live_* checks run.
- **G7 (CI).** `validate-phase1.yml` triggers (prepare.py and
  STORAGE_ARCHITECTURE.md are in its path filter). Its offline job
  (`tests.test_prepare`) and its live job (`python prepare.py` on
  `postgis/postgis:16-3.4`, four output lines, both values 0) must pass.
  G6 on the local PostGIS 16.15 / 3.4.2 reproduces the live job. The same
  edit also triggers `discovery-fulton.yml` (test_discovery plus
  test_postgis_smoke on the same image) and `offline-tests.yml` (both
  suites): G4 and G6 cover them.
- **G8 (drift pins).** The R-07 tests and the R-08 source-text test
  exist and pass.
- **G9 (operator steps).** The decision note and README Status carry the
  ordered steps below, with R-03's failure text.

**No-go** if any of these holds: G2 shows any other prepare.py change; a
golden or differential shows any `parcel_scores` insert, result or note
change; the suite passes only with a suite-wide gate-off; or a non-local
DSN was used at any point.

## Operator next steps (draft for the decision note and README Status)

Between runs only, in this order:

1. Merge the change to `main` (the protocol's commit-to-main step). Halt
   any active loop first (`make halt`).
2. `make db-check` against the REAL database (`python prepare.py`). This
   creates the empty `parcel_hazards` table. **Do this before any
   `make verify`, `make baseline` or `make daily`.** Otherwise every
   experiment, the baseline included, ends `crash` with `HazardAuditFailed:
   ... unreadable` (R-03). With the table empty, nothing is scored
   differently.
3. In a browser (both hosts' robots.txt deny automated queries; the
   `--mode rest` route makes no data request):
   - **FEMA NFHL**, Fulton County GA, county package `13121C`, from the
     FEMA Flood Map Service Center (msc.fema.gov, "Search All Products" →
     NFHL data → county). Unzip so that these files sit DIRECTLY in
     `hazards_inbox/nfhl/` (no subfolder): `S_FLD_HAZ_AR.shp`, `.dbf` and
     `.prj` (required; the name is exactly `S_FLD_HAZ_AR`, and the sidecar
     extensions may be upper or lower case), plus `S_LOMR.shp`, `.dbf` and
     `.prj` when the package has them (optional; without them
     `lomr_status = not_read` and AE never relaxes).
   - **USFWS NWI**, Georgia state shapefile download (fws.gov Wetlands
     Mapper / "Download Seamless Wetlands Data" by state). Unzip FLAT into
     `hazards_inbox/nwi/`: exactly one `.shp`, or several with exactly one
     ending in `_Wetlands` (for example `GA_Wetlands.shp`, with its `.dbf`
     and `.prj`). Project-metadata or historic-map shapefiles may stay
     beside it. The `.prj` must be NAD83 geographic, WGS84 geographic or
     NAD83 Conus Albers; Web Mercator is refused.
4. `make hazards-import` (files mode). Exit 0 = both sources written.
   Exit 4 names the failed source and keeps its previous data. Exit 2
   after "source(s) ... committed" = partial: re-run before the baseline.
   Exit 3 = the current run already has a baseline: start a new run first.
   Exit 1 = refused (the gate is off: step 1 not merged). Read the console
   counts: parcels with SFHA, floodway, 0.2% zone and wetlands; share
   bands; H4/H6 outcomes (fail / review / gap); "previously imported
   parcels not covered".
5. **The first populated import IS the metric change.** Start a new run
   (`autoresearch/<new tag>`, AUTORESEARCH_MECHANICS.md "The Setup Phase")
   and record its baseline (`make baseline`). Metric values before it are
   not comparable with values after it. Never import mid-run: the tool
   refuses, and the runner audit discards an experiment if the table
   changes.
6. Optional: resolve the old open "H4 flood unjoined" / "H6 wetlands
   unjoined" data_gap flags (R-12).

Unchanged by this change and by the operator's 2026-11-09 answers: H4 stays
as implemented. A floodway blocks. A/AE block. AE wholly inside a LOMR, and
AH/AO/A99/AR/V/VE, are review passes ("just review", D2). H6 fails above
`wetlands_max_pct_of_parcel`.

## Evidence (reviewer probe, counts only)

- **Scratch copy.** `git archive` of 4fe8080 into the scratchpad (no git
  history), with only the proposal's two constants and the two `_ALL_DDL`
  entries added to prepare.py (+41 lines). The repo worktree was not
  modified.
  - `hazards.enabled()` = True and `schema_problem()` = None.
  - Root suite: 1,744 tests, 47 failures, 6 errors and 3 skipped. The
    skips are git-history pins; at least two of them fail in the real
    worktree (R-06).
  - Failures by module: test_site_fit 12, test_discovery 12,
    test_adjacency_scoring 9, test_site_fit_scoring 5,
    test_operator_decisions 4, test_hazards_scoring 4, test_hazards 4,
    test_txn_integrity 1, test_hazards_runner 1, test_hazards_import 1.
- **Golden checks in the copy.**
  - `hazards_golden_check`: "not comparable".
  - zoning (13 cases), site_fit (23), adjacency (9) and building_comps:
    DIFFERS.
  - All five exit 0 on the unmodified worktree.
- **Field-level diff, gate on, against the 8ddaf69 hazards record.**
  - Scoring: 0 of 22 parcels differ in inserts, result keys or notes.
  - Discovery: 7 of 8 filter sets differ, only in H4/H6 `flag` → `pass`.
    10 of 16 process cases differ, only in executed statements (11 → 9,
    the two flagged_items INSERTs). 0 status differences.
- **Live schema probe.** Local PostGIS (PostgreSQL 16.15, PostGIS 3.4.2),
  database `lr_haz`, schema `rv33` created for the probe and dropped
  afterwards.
  - `python prepare.py` run twice: exit 0 both times, with
    `actionable_pipeline_count: 0` and `confidence_weighted_pipeline: 0.0`.
  - `parcel_hazards`: 30 columns, indexes `idx_hazards_sfha_geom`,
    `idx_hazards_wetland_geom` and the primary key, and both geometry
    columns `MULTIPOLYGON` SRID 2240.
  - No other database or schema was touched.
