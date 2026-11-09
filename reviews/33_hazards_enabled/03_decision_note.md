# 03 — Hazards table enabled: reviewer decision

> **Tier 2 (prepare-mutation).** Written by the reviewer and implementer,
> in a fresh context that wrote neither the pre-change register
> (`02_risk_review.md`) nor the code and the code writer response
> (`02_code_writer_response.md`). It checks every gate against the actual
> diff (`git diff 4fe8080..HEAD`), re-runs the evidence, records the
> verdict and the operator next steps. Model names are omitted. Live
> evidence is counts only.

## Who ran which role

`STANDING_RISKS.md` (Tier 2) asks which model ran each role. The repository
rule forbids model names in files, so this note records the requirement
instead: each role (pre-change register, code writer, this review) ran in
its own fresh context, and this context did not write the code it reviews.

## Operator authorization (recorded verbatim)

The operator's chat message of 2026-11-09, as relayed by the workflow
harness, is the only operator voice in this change:

> 1 - Count them, jsut review, 3 - fix 4 - can you add this yourself. Start
> building what you proposed keeping my answers in mind

How each answer is honoured:

| Item | Operator words | Reading | Here |
|---|---|---|---|
| 1 (D3) | "Count them" | D3-01/D3-02: COUNT unlisted districts AND districts with unreadable setback values | Not in this change (sibling worktree). |
| 1 (D2) | "jsut review" | D2 stays as implemented: V/VE review-only, a floodway still blocks; no flood-rule change. The null-beats-low question is NOT answered, so no new root sub-score | Checked: `hazards.py`, `parameters.json` and every scoring module are byte-identical to 4fe8080. No sub-score added. |
| 3 | "fix" | the research.py ArcGIS error-envelope bug and audit-path bug; the D1-11 zoning_import fetcher round | Not in this change (sibling worktree); `research.py` and `zoning_import.py` are byte-identical to 4fe8080 here. |
| 4 | "can you add this yourself" | the operator delegates the reviews/26 hazards prepare-mutation to the agent, acting for the human between runs | **This change.** |
| (Step 2) | "Start building what you proposed" | the screener quick wins | Not in this change; `screener/` is byte-identical to 4fe8080. |

Scope of the delegation, as checked: the two constants and the two
`_ALL_DDL` entries of `reviews/26_flood_wetlands/01_prepare_mutation_proposal.md`,
one provenance comment, and the SQL mirror in STORAGE_ARCHITECTURE.md. Not
applied, as required: the optional metric-SQL exclusion, any
`SCHEMA_VERSION` change, any other prepare.py byte. The CLAUDE.md invariant
("NEVER edit `prepare.py`") is unchanged; this one delegation is not a
precedent. The same verbatim text is in reviews/26's decision note
("Operator decisions (2026-11-09, item 4)").

## Verdict

**Approved after small fixes. Ship.** Every gate G1-G9 holds on re-check.
No score, insert, result or note changes with the table empty. The fixes
in this round are documentation only (below); no code or test changed.

## Gate re-check (this context, counts only)

- **G1 record.** Verbatim text and scope present in `02_risk_review.md`,
  `02_code_writer_response.md`, this note, and reviews/26's dated section.
- **G2 exact diff.** `git diff 4fe8080..HEAD -- prepare.py`: 47 added
  lines, 0 removed. Independently checked: the constants exec'd from the
  proposal's python block equal `prepare._DDL_PARCEL_HAZARDS` and
  `prepare._DDL_PARCEL_HAZARDS_INDEXES`; in `_ALL_DDL` the mirror is
  followed by the table, its two indexes, then exactly `_DDL_INDEXES`. No
  diff line touches `_LATEST_SCORE_FILTER`, `SCHEMA_VERSION` or a
  `calculate_*` function. 0 changed lines against 4fe8080 in
  parameters.json, sources.json, program.md, requirements.txt,
  zoning_rules.json, building_prototypes.json, rules_sources.json,
  screener/, hazards.py, hazards_import.py, runner.py, research.py,
  site_fit.py, reporting.py, the Makefile and .github/.
  `tests/test_zz_human_files_unchanged.py` and `zoning_helpers.HUMAN_OWNED`
  are untouched; the two git-history byte pins were re-pinned for prepare.py
  only (base plus exactly the mutation), with a comment citing the
  operator decision.
- **G3 gate on.** `hazards.enabled()` True, `schema_problem()` None.
- **G4 suites** (`DATABASE_URL` unset): root 1766 OK (base 1744 + 22);
  screener 134 OK; `tests.test_prepare` OK. Also run in a depth-1 clone
  (the CI checkout shape): 1766 OK, 6 skipped (the git-history pins skip
  cleanly). In a history-less `git archive` copy, HEAD and base 4fe8080
  show the same 1 failure + 3 errors (tests that need a git repo:
  `test_the_repo_file_is_committed` x3, a gitignore check), so that is
  pre-existing and not this change. Every changed .py file compiles under
  Python 3.11 (the CI version).
- **G5 scores unchanged, table empty.** All five golden checks exit 0.
  `hazards_golden_check.py`: gate off identical to the 8ddaf69 record (16
  discovery runs, 8 filter sets, 22 scored parcels + the cycle); enabled
  with the table empty: inserts, results and notes identical, discovery
  statuses identical, only the documented delta. Expected flag-text
  change (the proposal's): discovery H4/H6 `flag` -> `pass` with an empty
  reason, the two "unjoined" stub flagged_items INSERTs gone; scoring adds
  one hazard read per path and two data_gap flags per parcel, "H4 flood:
  no hazards import for this parcel (FEMA NFHL not assessed)" and "H6
  wetlands: no hazards import for this parcel (USFWS NWI not assessed)";
  projections and the memo use the hazard footnote variants.
- **G6 live, local only** (`lr_haz`, PostgreSQL 16 / PostGIS 3.4, DSN
  with `client_encoding=UTF8` because the database is SQL_ASCII, as the
  writer recorded): `python prepare.py` exit 0 twice, four output lines,
  both metric values 0 / 0.0; the second run left every relation's oid
  unchanged (idempotent). `parcel_hazards`: 30 columns, both geometry
  columns MULTIPOLYGON 2240, indexes `idx_hazards_sfha_geom`,
  `idx_hazards_wetland_geom` and the primary key, FK `ON DELETE CASCADE`.
  `hazards.assess_parcels` on the empty table: 2/2 parcels (one present,
  one unknown) are `gap`/`gap` with the two data_gap texts, no blocker.
  `tests/test_postgis_smoke.py`: passes (exit 0). The CREATE DATABASE
  live checks were not re-run here (R-13; the writer's in-database
  equivalent probe is recorded in `02_code_writer_response.md`).
  Afterwards `lr_haz` public was reset to `spatial_ref_sys` plus the
  PostGIS views; no other database or schema was touched. No non-local
  DSN was used.
- **G7 CI.** `validate-phase1.yml` triggers on prepare.py and
  STORAGE_ARCHITECTURE.md. Offline job: `import prepare` and
  `tests.test_prepare` pass. Live job: `python prepare.py` on a fresh
  PostGIS 16-3.4 is reproduced by G6 (four lines, values 0 and 0.0; the
  sanity step accepts 0). Expected to pass. `discovery-fulton.yml` and
  `offline-tests.yml`: covered by G4/G6 and the writer's W-01 fix to
  `tests/live_site_fit_check.py`.
- **G8 drift pins.** `DriftPinTests` (prepare vs `hazards.PROPOSED_DDL`,
  the indexes, the `_ALL_DDL` order, STORAGE_ARCHITECTURE.md verbatim) and
  the AST source-text test pass. The STORAGE_ARCHITECTURE.md block equals
  the proposal's SQL.
- **G9 operator steps.** In README Status, reviews/26 and below.

## Review findings of this round

| ID | Sev | Finding | Disposition |
|---|---|---|---|
| V-01 | nit | reviews/26's dated section said "Step 2 is the screener quick wins", which reads as operator step 2 (db-check) of the list just below it; it also summarised item 3 without naming its parts. | Fixed: names the ArcGIS error-envelope and audit-path bugs and the D1-11 fetcher round, and says "step 2 of that sequence". |
| V-02 | nit | The operator steps say the sidecar extensions may be either case, but `hazards_import._gather_files` opens `nfhl/S_FLD_HAZ_AR.shp` by exact name: the `.shp` itself must be lowercase (only `.dbf`/`.prj`/`.cpg` are tried in both cases). The NWI `.shp` match is case-insensitive. | Fixed in README Status and in the steps below. No code change (hazards_import.py is protected and out of scope). |
| V-03 | info | The re-pinned byte tests and `ExactDiffTests` need git history; they skip in CI's depth-1 checkout, so CI does not enforce G2 there. | Accepted: the same tests run in every worktree with history (the operator's merge review), and `DriftPinTests` (no history needed) still pins the DDL text and order. |

Residuals carried from the writer (not in this delegation): a
`runner.verify_setup` check that `parcel_hazards` exists while the gate is
on (R-03); `hazards_import.py`'s gate-off text still says "only a human
applies" (reachable only after a rollback); the R-05 `SystemExit` nit in
`runner._hazard_audit`.

## OPERATOR NEXT STEPS (between runs only, in this order)

1. **Merge** this branch to `main` (halt any active loop first: `make
   halt`). The merge is the protocol's commit-to-main step. If a sibling
   2026-11-09 change (D3 counting, research.py / zoning_import fixes,
   screener) lands first, rebase, then re-run both suites and the five
   golden checks; never drop a `disabled()` wrap or delete a test.
2. **`make db-check`** (`python prepare.py`) against the REAL database,
   BEFORE any `make verify`, `make baseline` or `make daily`. It creates the
   empty `parcel_hazards` table; no score changes. If skipped, every
   experiment, the baseline included, ends `crash` with
   `HazardAuditFailed: hazard audit: parcel_hazards unreadable (...)`, and
   `make hazards-import` refuses with exit 1 ("the database has no
   parcel_hazards table").
3. **Download in a browser** (both hosts' robots.txt deny automated
   queries). Create the folders and unpack FLAT, no subfolders:
   - **FEMA NFHL**, Fulton County GA, county package `13121C`, from the
     FEMA Flood Map Service Center (msc.fema.gov, "Search All Products" ->
     National Flood Hazard Layer (NFHL) -> county download). Into
     `hazards_inbox/nfhl/`: `S_FLD_HAZ_AR.shp` (exact name, lowercase
     `.shp`) with `S_FLD_HAZ_AR.dbf` and `S_FLD_HAZ_AR.prj` (required),
     plus `S_LOMR.shp`, `.dbf`, `.prj` when the package has them (optional;
     without them `lomr_status = not_read` and AE never relaxes). Other
     package files may stay beside them.
   - **USFWS NWI**, the Georgia state shapefile (fws.gov Wetlands Mapper,
     "Download Seamless Wetlands Data", by state). Into
     `hazards_inbox/nwi/`: exactly one `.shp`, or several with exactly one
     ending in `_Wetlands` (for example `GA_Wetlands.shp`), each with its
     `.dbf` and `.prj`. The `.prj` must be NAD83 geographic, WGS84
     geographic or NAD83 Conus Albers; Web Mercator is refused.
4. **`make hazards-import`** (files mode) against the real database.
   Exit 0: both sources written. 4: names the failed source, its previous
   data kept. 2 after "source(s) ... committed": partial; re-run before the
   baseline (2 without that line: database error or no DATABASE_URL). 3:
   the current run already has a baseline; start a new run first. 1:
   refused (gate off, i.e. not merged; or no table, i.e. step 2). Read the
   console counts (parcels with SFHA, floodway, 0.2% zone, wetlands; H4/H6
   outcomes; "previously imported parcels not covered").
5. **The first populated import IS the metric change.** Start a new run
   (`autoresearch/<new tag>`, AUTORESEARCH_MECHANICS.md "The Setup Phase")
   and record its baseline (`make baseline`). Metric values before it are
   not comparable with values after it. Never import mid-run.
6. Optional: resolve the old open "H4 flood unjoined" / "H6 wetlands
   unjoined" data_gap flags.

Unchanged by this change and by "jsut review" (D2): a floodway blocks;
A/AE block; AE wholly inside a LOMR, AH/AO/A99/AR and V/VE are review
passes; H6 fails above `wetlands_max_pct_of_parcel`.
