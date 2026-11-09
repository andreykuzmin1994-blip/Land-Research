# 02 — Hazards table enabled: code writer response

> **Tier 2 (prepare-mutation).** Written by the code writer, in reply to the
> pre-change register `02_risk_review.md`. It states what was changed, the
> disposition of every risk and gate, and the evidence. The adversarial
> review after the change and the verdict belong in `03_decision_note.md`,
> written by a context that did not write this code. Model names are
> omitted. Live evidence is counts only.

## Operator authorization (recorded verbatim)

The operator's chat message of 2026-11-09, as relayed by the workflow
harness:

> 1 - Count them, jsut review, 3 - fix 4 - can you add this yourself. Start
> building what you proposed keeping my answers in mind

How the workflow reads each answer, and what this change does with it:

| Item | Operator words | Reading | In this change |
|---|---|---|---|
| 1 (D3) | "Count them" | D3-01/D3-02: count unlisted districts AND districts with unreadable setback values | No. Another worktree. |
| 1 (D2) | "jsut review" | D2 stays as implemented: V/VE review-only, a floodway still blocks. No flood-rule change. The null-beats-low question is NOT answered, so no new root sub-score | No flood-rule change made. |
| 3 | "fix" | the research.py ArcGIS error-envelope and audit-path bugs; the D1-11 zoning_import fetcher round | No. Another worktree. |
| 4 | "can you add this yourself" | the operator delegates the reviews/26 prepare-mutation to the agent, acting for the human between runs | **This change.** |
| (2) | "Start building what you proposed" | the screener quick wins | No. Another worktree. |

**Scope of the delegation, as applied.** One prepare.py edit (the proposal's
two constants, the two `_ALL_DDL` entries, one provenance comment) and the
SQL mirror in STORAGE_ARCHITECTURE.md. Not applied: the optional
metric-SQL exclusion, any `SCHEMA_VERSION` change, any other prepare.py
byte, and any edit to `parameters.json`, `sources.json`, `program.md`,
`zoning_rules.json`, `building_prototypes.json`, `rules_sources.json`,
`requirements.txt`, `hazards.py`, `hazards_import.py`, `runner.py` or
`screener/`. No non-local database was used. The verbatim text and scope
are also recorded in a dated section of
`reviews/26_flood_wetlands/03_decision_note.md` (G1), which marks operator
step 2 as done by the agent under the delegation.

## What changed

- **prepare.py** (+47 lines, 0 removed). After `_DDL_EXPERIMENT_LOG_MIRROR`:
  a 6-line provenance comment, `_DDL_PARCEL_HAZARDS` (the proposal's text,
  ending `);"""`) and `_DDL_PARCEL_HAZARDS_INDEXES`. In `_ALL_DDL`:
  `_DDL_EXPERIMENT_LOG_MIRROR, _DDL_PARCEL_HAZARDS,
  *_DDL_PARCEL_HAZARDS_INDEXES, *_DDL_INDEXES`. Nothing else.
- **STORAGE_ARCHITECTURE.md** (+50 lines): a `parcel_hazards` entry after
  `experiment_log_mirror`, with the proposal's SQL verbatim.
- **tests/hazards_helpers.py**: `disabled()` (one named helper, a
  `mock.patch.object(hazards, "_SOURCE_DDL", None)`, usable as a context
  manager or a class/method decorator) and `with_prepare_mutation(base)`
  (a base prepare.py with exactly this edit applied; raises if the base
  lacks either anchor once).
- **tests/test_hazards_enabled.py** (new, 20 tests): G3, G2, G8 and G5 on
  the shipped state.
- **tests/hazards_golden_case.py**: `differences_on_empty(want, got,
  hazard_read_sql)`, the documented delta as code.
- **tests/hazards_golden_check.py**: two modes, gate off (byte-identical)
  and enabled with the table empty (the delta). The zoning, site_fit,
  adjacency and building_comps checks run their case under `disabled()`.
- **R-02 rewiring** in 13 test modules (below), and the two byte pins
  re-pinned for prepare.py only (R-06).
- **tests/live_site_fit_check.py**: case (c) now injects its failure into
  `_SQL_BUFFERED_FOR_FIT_HAZARDS` as well (new finding W-01 below).
- **Docs truth-up** (R-09): CLAUDE.md, STANDING_RISKS.md (SR-1, SR-16),
  AUTORESEARCH_MECHANICS.md (hard filters, `hazards.py`,
  `hazards_import.py`, a dated "Delegated edit" note under the mutation
  protocol), README.md (Status with the operator steps, "Flood and
  wetlands", the file tree) and reviews/26's decision note.

## Dispositions

| ID | Disposition |
|---|---|
| R-01 | **Done.** The diff is the proposal text plus one comment (G2, pinned by `ExactDiffTests.test_prepare_is_base_plus_exactly_the_mutation`, which rebuilds the expected file from 4fe8080 and compares bytes). No optional exclusion, no `SCHEMA_VERSION` bump. Verbatim record in this folder and in reviews/26. The mechanics doc says the edit is not a precedent. Commit subject `prepare-mutation: ...`. |
| R-02 | **Done.** 53 behavioural failures, plus the 2 byte pins, at the gate flip (measured: 55 in all, as the register predicted). Fixed with the one helper, per class or per test, and only where hazards is not the subject. 25 `disabled()` uses rewire existing tests: 9 class-scope (7 class decorators; 2 `setUpClass` starts with `addClassCleanup`, because a class decorator does not cover `setUpClass`) and 16 per-test. A further 2 are in the new module (testing the helper and recording the off state) and 5 in the golden-check scripts, 32 in all. Per module: test_discovery 8, test_adjacency_scoring 4, test_hazards 3, test_hazards_scoring 2, test_site_fit 2, test_site_fit_scoring 2, and 1 each in test_hazards_import, test_hazards_runner, test_operator_decisions and test_txn_integrity. No suite-wide default-off: `ShippedStateTests.test_disabled_is_never_suite_wide` fails on any module-level or `setUpModule` call of a `*disabled` helper in tests/. Inverse assertions on the shipped state: `test_on_in_the_shipped_repo_off_under_disabled` (test_hazards), `test_shipped_gate_no_longer_refuses` (the import passes the gate to the run guard: exit 3, not 1), `test_shipped_gate_arms_the_audit` (a counted AE parcel crashes, and the audit opens its own connection), the ERROR-status stream-buffer line (test_hazards_scoring), the source-text constant (test_hazards), and `ShippedStateTests` (H4/H6 `pass` with empty reason, the hazards footnote, the stream-buffer constraint). |
| R-03 | **Documented, not coded.** `make db-check` is step 2 of the operator steps (README Status, reviews/26), before any verify/baseline/daily, with the crash text `HazardAuditFailed: hazard audit: parcel_hazards unreadable (...)`. Follow-up (runner.py, Tier 2, outside the delegation): a `verify_setup` table-presence check. |
| R-04 | **Done.** `EmptyTableDifferentialTests` (suite) and the enabled mode of `hazards_golden_check.py` (exit 0). Both use `differences_on_empty`, which holds the documented delta and nothing more. Two kinds of test show it is not vacuous: the gate-off output fails the enabled-mode rules, and five single mutations are each caught (an insert value, a note, an extra statement, a status, an H4 action). |
| R-05 | **Done in tests.** Every runner-path test that met the second `get_connection` runs under `disabled()` or mocks every connection. Both suites pass with `DATABASE_URL` unset. Nit kept: `SystemExit` from a missing DSN would still slip past `_hazard_audit`'s `except Exception`. Unreachable in production (the metric block connects first). runner.py is not changed. |
| R-06 | **Done.** Both pins (`test_hazards_scoring` at 8ddaf69 and `test_adjacency_scoring` at 595aa08) compare prepare.py to `with_prepare_mutation(base)`, with a comment citing the operator decision of 2026-11-09. Every other file stays on its old base bytes. `HUMAN_OWNED` and `test_zz_human_files_unchanged` are untouched. |
| R-07 | **Done.** `DriftPinTests` checks: strip-equality with `PROPOSED_DDL`; index tuple equality; 30 columns in `hazards.COLUMNS` order, 2 `MultiPolygon, 2240`, the FK; `IF NOT EXISTS` on all three statements; the `_ALL_DDL` order (parcels before; mirror, table, indexes, then `_DDL_INDEXES`); STORAGE_ARCHITECTURE.md verbatim; and the proposal's SQL block equal to the prepare text. hazards.py is not edited. |
| R-08 | **Done.** A plain module-level triple-quoted literal. `test_gate_reads_the_real_constant_as_a_plain_literal` pins `ddl_from_prepare_source()` equal to the constant, with exactly one assignment whose value is an `ast.Constant` string. |
| R-09 | **Done.** Wording: "enabled 2026-11-09 (operator-authorized, reviews/33_hazards_enabled); inert until the first populated import". The ProtectionTests needles are kept (`hazards.py`, `hazards_import.py`, `reviews/26_flood_wetlands`, the SR-1 test name). program.md is not edited. |
| R-10 | **Done.** The commit message says "metric UNCHANGED while parcel_hazards is empty; the first populated import is the metric change: new run + baseline". Protocol: (1) no active run in this worktree (no experiment_log.tsv, no .halt); (2) the edit; (3) authored on the work branch, and the operator's merge to main is the commit-to-main step (precedent `796379d`); (4)-(5) the new run and baseline come after the populated import; (6) noted in the mechanics doc's "Delegated edit" paragraph. |
| R-11 | **Noted** in reviews/26's dated section. Whichever of this change and the sibling D3/fix/screener changes lands second re-runs both suites and the five golden checks after rebasing. A merge must not drop a `disabled()` wrap or delete a test. Overlapping files: test_site_fit.py, test_site_fit_scoring.py, test_operator_decisions.py (D3), test_discovery.py (research.py fixes). |
| R-12 | **Documented** as optional step 6. |
| R-13 | **Followed.** test_postgis_smoke, live_txn_check, live_submarket_check and live_building_comps_check ran in `lr_haz` itself. The four CREATE DATABASE checks were NOT run as scripts. Instead, an equivalent probe called their inner functions (`check_precedent`/`check_overlay`, `synthetic`) with each main's own setup, against `lr_haz`'s public schema, reset before and after each (scratchpad probe, not a repo file). |

## New finding from this round

| ID | Sev | Finding | Fix |
|---|---|---|---|
| W-01 | high (CI) | `tests/live_site_fit_check.py` case (c) injects a failing statement into `site_fit._SQL_BUFFERED_FOR_FIT` only. With the gate on, `_project_group` reads `_SQL_BUFFERED_FOR_FIT_HAZARDS` first, which succeeds on the empty table. So (c) got status `ok`, not `error`, and `discovery-fulton.yml`'s live job would have failed (G7). In the probe: 1 FAIL with the gate on, 0 on base 4fe8080. | The case injects into both statements and restores both. Probe after the fix: 11 OK, 0 FAIL, the same count as base. |

## Gate results

- **G1 record**: here and in reviews/26 (dated section).
- **G2 exact diff**: `git diff 4fe8080 -- prepare.py` = +47/-0, which is the
  proposal plus the comment. Pinned by `ExactDiffTests` (byte rebuild from
  4fe8080; the metric region from `def calculate_actionable_pipeline_count`
  onwards and the `SCHEMA_VERSION` line are byte-identical; no
  `parcel_hazards` in `_LATEST_SCORE_FILTER` or the metric SQL).
  parameters.json, sources.json, program.md, requirements.txt,
  zoning_rules.json, building_prototypes.json, rules_sources.json,
  screener/, hazards.py, hazards_import.py, runner.py, research.py,
  site_fit.py, reporting.py, the other protected modules, the Makefile and
  .github/: 0 changed lines against 4fe8080.
- **G3 gate on**: `hazards.enabled()` True, `schema_problem()` None
  (`ShippedStateTests.test_gate_on_and_schema_readable`).
- **G4 suites** (`DATABASE_URL` unset): root 1766 OK (1744 + 22 new: 20 in
  test_hazards_enabled, 1 in test_hazards_import, 1 in test_hazards_runner;
  0 skipped in this worktree, so the git-history pins ran); screener 134 OK; `tests.test_prepare` 31 OK.
  `disabled()` uses: 32 (25 rewiring + 2 new + 5 golden checks).
- **G5 scores unchanged, table empty**, against the 8ddaf69 record. 22/22
  parcels have identical results, parcel_scores inserts and research_log
  notes; the cycle's summary, inserts and notes are identical. Scoring
  statements differ only by the one batched hazard read per path and 2
  data_gap INSERTs per parcel ("H4 flood: no hazards import for this
  parcel (FEMA NFHL not assessed)", "H6 wetlands: no hazards import for
  this parcel (USFWS NWI not assessed)"): 14 -> 17 statements per parcel,
  247 -> 292 on the cycle (+1 read, +44 INSERTs). Discovery: 7 of 8 filter
  sets differ, each only in H4/H6 `flag` -> `pass` with an empty reason;
  16/16 `_process_parcel` statuses are identical; 10 of 16 runs execute 2
  fewer statements (the two stub flagged_items INSERTs), the other 6
  none. All five golden checks exit 0.
- **G6 live, local only**, in `lr_haz` (PostgreSQL 16.15, PostGIS 3.4.2):
  - `python prepare.py` exits 0 twice, with both metric lines 0 / 0.0.
    The re-run leaves the catalog unchanged (same relations, same oids).
  - `parcel_hazards`: 30 columns; `idx_hazards_sfha_geom` and
    `idx_hazards_wetland_geom` (GIST) plus the primary key; both
    geometry columns `MULTIPOLYGON` SRID 2240; the FK to `parcels` is
    `ON DELETE CASCADE`.
  - `test_postgis_smoke.py` passes (6 OK lines).
  - live_txn (22 OK), live_submarket (13) and live_building_comps (16 ok)
    all exit 0.
  - Equivalent probe of the CREATE DATABASE checks: zoning 12 OK,
    hazards 34 OK, adjacency 31 OK, site_fit 11 OK (after W-01). 0 FAIL.
  - **Environment note.** `lr_haz` is `SQL_ASCII`, so psycopg returns bytes
    for text. Without `client_encoding=UTF8` in the DSN, the smoke test and
    the building-comps check raise "Invalid endian flag value encountered",
    and live_submarket fails on bytes. Base 4fe8080 fails identically, so
    this is environmental, not this change. The CI image is UTF8. Every
    run above used
    `postgresql://postgres:testpw@localhost:55432/lr_haz?client_encoding=UTF8`
    (the same database).
  - A first attempt inside throwaway schemas (`options=-csearch_path`) hit
    the same encoding problem. Those schemas were dropped, and `lr_haz`
    public was reset to `spatial_ref_sys` only. No other database or
    schema remains.
- **G7 CI**: `validate-phase1.yml` offline (`tests.test_prepare`, 31 OK)
  and live (prepare.py's four lines, values 0 and 0.0) are reproduced by
  G6. `discovery-fulton.yml`: `tests.test_discovery` OK offline, and its
  live steps (smoke plus the seven live checks) are reproduced above,
  once W-01 is fixed. `offline-tests.yml`: G4.
- **G8 drift pins**: `DriftPinTests` (7 tests).
- **G9 operator steps**: below, in README Status and in reviews/26.

## Behaviour a human will notice (table empty)

- Discovery no longer writes the "H4 flood unjoined" / "H6 wetlands
  unjoined" stub flags. H4/H6 pass with no reason.
- Scoring writes two data_gap flags per parcel, "no hazards import for
  this parcel" (deduplicated per open gap, like every data_gap).
- The runner opens a second connection per experiment for the H4/H6 audit
  and reports `hazard_audit` counts in the sub-summaries.
- Projections, the snapshot and the memo footnote use the hazard variants
  ("flood/wetlands not deducted (...)", the stream/state-waters buffer
  line). These texts are already pinned by test_hazards_scoring.
- No score, composite, actionability, strategy or metric value changes.

## Operator next steps (between runs only, in this order)

1. Merge to `main` (halt any active loop first: `make halt`).
2. `make db-check` (`python prepare.py`) against the REAL database, before
   any `make verify`, `make baseline` or `make daily`. It creates the empty
   `parcel_hazards` table and changes no score. If skipped, every
   experiment, the baseline included, ends `crash` with
   `HazardAuditFailed: hazard audit: parcel_hazards unreadable (...)`.
3. Download in a browser (both hosts' robots.txt deny automated queries;
   `--mode rest` makes no data request). Unpack FLAT, with no subfolder:
   - FEMA NFHL, Fulton County GA, county package `13121C`, from the FEMA
     Flood Map Service Center (msc.fema.gov, "Search All Products", NFHL
     data by county), into `hazards_inbox/nfhl/`. Required:
     `S_FLD_HAZ_AR.shp`, `.dbf` and `.prj`. The name is exact; sidecar
     extensions may be either case. Optional: `S_LOMR.shp`, `.dbf` and
     `.prj`. Without them `lomr_status = not_read` and AE never relaxes.
   - USFWS NWI, the Georgia state shapefile (fws.gov Wetlands Mapper,
     "Download Seamless Wetlands Data" by state), into
     `hazards_inbox/nwi/`. Either exactly one `.shp`, or several with
     exactly one ending in `_Wetlands` (for example `GA_Wetlands.shp`),
     each with its `.dbf` and `.prj`. Project-metadata shapefiles may stay
     beside it. The `.prj` must be NAD83 geographic, WGS84 geographic or
     NAD83 Conus Albers; Web Mercator is refused.
4. `make hazards-import` (files mode). The exit codes:
   - 0: both sources written.
   - 4: names the failed source and keeps its previous data.
   - 2 after "source(s) ... committed": a partial import; re-run before
     the baseline.
   - 3: the current run already has a baseline; start a new run first.
   - 1: refused. The gate is off (step 1 not merged), or the database has
     no table (do step 2).

   Read the console counts: parcels with SFHA, floodway, the 0.2% zone and
   wetlands; share bands; H4/H6 outcomes; "previously imported parcels not
   covered".
5. **The first populated import IS the metric change.** Start a new run
   (`autoresearch/<new tag>`, AUTORESEARCH_MECHANICS.md "The Setup Phase")
   and record its baseline (`make baseline`). Metric values before it are
   not comparable with values after it. Never import mid-run.
6. Optional: resolve the old open "H4 flood unjoined" / "H6 wetlands
   unjoined" data_gap flags.

Unchanged, per the operator's 2026-11-09 answers ("jsut review", D2): a
floodway blocks; A/AE block; AE wholly inside a LOMR, AH/AO/A99/AR and V/VE
are review passes; H6 fails above `wetlands_max_pct_of_parcel`.

## Follow-ups (not in this delegation)

- runner.py (Tier 2): a `verify_setup` check that `parcel_hazards` exists
  while `hazards.enabled()` (R-03).
- hazards_import.py (protected): its gate-off refusal text still says "a
  proposal ... that only a human applies". It can only be reached after a
  rollback of this edit. hazards.py's docstrings still call the DDL a
  proposal. Both files are left byte-identical, as G8 requires.
- R-05 nit (`SystemExit` past `except Exception` in `_hazard_audit`).
