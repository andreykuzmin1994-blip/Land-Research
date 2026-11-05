# 03 — Zoning import, rules and S9: decision note

> **Tier 2.** The design is `00_design.md`, the cited draft rules are
> `01_zoning_rules_DRAFT.{json,md}` and the pre-change risk register is
> `02_risk_review.md`. This note records the independent **adversarial**
> post-change review (fresh contexts that did not write the code; each
> finding was then challenged by a separate skeptic pass, and probed on a
> live local PostGIS with real Fulton corridor parcels, reporting counts
> only) and what changed after it. Model names are omitted, as in
> `reviews/18_test_readiness/`.

## Change

- **`zoning_import.py`** (new, protected, between runs only;
  `make zoning-import`). Finds each Fulton parcel's jurisdiction from its
  TaxDist on the registered Fulton parcel layer and its zoning district
  from the city/county layers listed in `sources.json`
  (`county_parcel_data.fulton_ga.zoning_sources`): by parcel id (join) or
  largest polygon overlap in PostGIS (overlay). Writes only
  `parcels.zoning` / `zoning_description` (with import provenance) in one
  transaction. Counts only on the console. Refuses (exit 3) once the run
  has its baseline, as `costar_import` does.
- **`zoning_rules.json`** (new, human-owned, never edited by the agent).
  Per jurisdiction a `status` (all `draft`), per district the
  warehouse/distribution status and dimensional values with citations,
  built from the draft.
- **`zoning.py`** (new, protected). Loads and freezes the rules, decodes
  the stored provenance, and classifies a parcel as by_right /
  conditional / rezoning / unknown, plus whether its rules are verified.
- **`research.py`** (between-runs edit). S9 uses the program.md table
  (10 / 7 / 4 with industrial precedent within 1 mile / 1) **only** for
  parcels whose jurisdiction is `verified`; otherwise S9 stays the stub
  (5). H3 stays flag-only and now writes a zoning-aware `data_gap` text
  through `_flag_data_gap_once`. Discovery no longer erases zoning.
- **`runner.py`, `reporting.py`** (protected, between-runs edits after the
  review): see findings 3, 9 and 11.

## Review verdict: approve-with-fixes (1 blocking, 1 high, 5 should-fix; all fixed)

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | blocking | The byte-identical test compared the whole scoring output of `research.py` with a frozen record of commit ba4a780. Every legitimate scoring experiment (S2, S8, S10, composite, notes, queries) failed the suite the agent must pass, so the loop could no longer improve scoring. Reproduced with a one-line S2 edit (4 failures). | The frozen record moved out of the suite to `reviews/21_zoning/golden_scores_head_ba4a780.json`, checked one-off by `tests/zoning_golden_check.py` (still identical at this commit). The suite now has a **differential** test: the CURRENT `research.py` with the real all-draft rules vs. with zoning absent (and vs. missing/malformed rules, verified rules elsewhere, and draft rules with the zoning reads forced on). A test bans any frozen scoring golden in `tests/test_*.py`. Re-run of the reviewer's repro: the S2 edit now passes the suite (1070 OK). |
| 2 | high | `research.py` could forge import provenance (`zoning.encode_stored`) and write `parcels.zoning` through discovery's upsert (`COALESCE(EXCLUDED.zoning, ...)`) or an inline `UPDATE` literal. Forged rows would outlive a git revert. | The upsert no longer names the zoning columns at all (an INSERT leaves them NULL; a conflict leaves them untouched), so the row dict cannot reach them. This makes operator decision 5a structural instead of a COALESCE. New tests: the upsert mentions no zoning column; `_upsert_parcel` drops zoning from any row; `research.py` never references `encode_stored`, `no_source_description`, `STORED_PREFIX`, `_FIELD_SEP`, `zoning.__file__`/`_STATE` or the provenance prefix; EVERY string in `research.py` (inline `execute()` literals, implicit and `+` concatenations, f-string parts) is scanned for `UPDATE parcels`, `zoning =` and a zoning INSERT. Both of the reviewer's injections now fail the suite. |
| 3 | should-fix | The rules-file freeze compared only with the process's own import-time hash, so a new process would freeze an uncommitted edit (a crashed experiment skipped instead of reset, a restart, a revert of `research.py` only). The AST write guard was evadable. | `zoning.verify_rules_committed()` compares the file's git blob id with `HEAD:zoning_rules.json`; `runner.evaluate` calls it right after `prepare.verify_parameters_unchanged()`, before any cycle, outside the agent's sandbox. Missing/untracked/dirty/deleted all fail loud; git unavailable fails loud. Tested on a temp repository and on the repo's own file. In the suite the runner-side call is a no-op (`tests/__init__.py`), so a human verifying rules can test before committing; the repo-file test still fails once, by name, while the file is uncommitted. The AST write guard is kept as a first line; the git check is the real defence. Not done: the same check for `parameters.json` (optional in the finding; it is a `prepare.py` concern). |
| 4 | should-fix | The overlay summed overlapping polygons of one district, so shares exceeded 1.0 (up to 2.42 live), split detection was skipped and a duplicated district could win. | `_SQL_OVERLAY` now takes `ST_Area(ST_Union(ST_CollectionExtract(ST_Intersection(...), 3))::geography)` per (parcel, district); the share is clamped at 1.0 as a backstop. New live-check fixture: two overlapping M-2 polygons (40%, 45%) vs. R-3 55% gives R-3, split, M-2 0.45. The old SQL fails that fixture (M-2 0.85). |
| 5 | should-fix | A failed or refused zoning source still exited 0. | Exit **4**: imported, but at least one zoning source with candidate parcels failed; the last line names them (`N zoning source(s) FAILED: ...`). The other sources' rows commit. Documented in the module docstring and README. Tested offline and live. |
| 6 | should-fix | The snippet let the City of South Fulton layer serve FID parcels ahead of Fulton layer 34 (one FID parcel stored M-2 from the city layer). | `south_fulton_zoning.jurisdictions` is now `["south_fulton"]` only. Live: layer 34 serves all 24 FID parcels (23 matched), the city layer 365 South Fulton candidates. |
| 7 | should-fix | Same divergence from `screener/fulton.py` ZONING_SOURCES, undisclosed in the snippet. | Fixed with 6; the snippet's `_comment` states it matches the screener exactly. A new test compares every snippet entry with the screener (URL, kind, fields, jurisdictions via TaxDist). |
| 8 | nit | The 1-mile S9 precedent radius sits next to the 2-mile `rezoning_precedent_radius_miles` parameter, uncross-referenced. | Comment on `_S9_PRECEDENT_RADIUS_M` explains they are independent; the 1 mile was already pinned by a test. |
| 9 | nit | The memo showed the raw provenance string. | `reporting._zoning_line` renders `zoning.memo_text` for imported values (e.g. `I-1 (atlanta): by_right, rules draft; source atlanta_zoning: Light Industrial`); other values render as before. Tested. |
| 10 | nit | Unmatched parcels after an import got "zoning not imported: run make zoning-import". | H3 text is now `zoning.H3_NOT_IMPORTED`: "zoning not imported or not matched by any zoning source (make zoning-import between runs)". Writing a per-parcel "unmatched" provenance was not done: the reworded text is accurate for every no-provenance case and adds no new stored state. |
| 11 | nit | Duplicate of 9. | Fixed with 9. |
| 12 | nit | Old "H3 zoning unjoined" `data_gap` flags stay open next to the new text. | Documented in README (one new H3 flag per parcel on the first discovery after this change; old ones resolved by hand). Not automated: the flags do not feed the metric, and resolving flags from the import tool would widen its write surface. |
| 13 | nit | Atlanta's BeltLine 500 ft exception ships as `ignored`, a default the draft never stated. | README verification checklist line: choose `ignored` or `downgrade_to_conditional` before verifying Atlanta. |
| 14 | nit | `-C` parcels score full by_right once Atlanta is verified. | Kept (design choice Z-08); README checklist line. |
| 15 | nit | `M-1A -> M-1` conversion only under `fid`. | Noted in the README checklist; no M-1A in today's data. |
| 16 | nit | The draft's county `as_of` was dropped. | `fid.county_as_of` added to `zoning_rules.json`. |

No finding was refuted. The skeptic passes narrowed two: finding 3's
revert-survival needs a non-standard revert or a restart (the documented
`git reset --hard` restores the tracked file), and finding 5 was a
documented but unsignalled choice.

## Evidence

- **Offline:** root suite `python -m unittest discover -s tests`:
  **1070 OK** (1057 before; 98 zoning tests across `test_zoning.py`,
  `test_zoning_import.py`, `test_zoning_scoring.py`). Screener suite
  `python -m unittest discover -s screener/tests -t .`: **134 OK**.
  `tests/zoning_golden_check.py`: identical to the ba4a780 record (12
  parcels + the cycle).
- **Reviewer repros, re-run in a throwaway clone:** the S2 compactness
  edit passes the suite; the `encode_stored` injection and an inline
  `UPDATE parcels SET zoning` literal each fail it.
- **Live check (local PostGIS, own database, no network):**
  `tests/live_zoning_check.py` 12/12 OK, including the new overlapping
  polygons case.
- **Live import** (throwaway clone with the snippet pasted into ITS
  `sources.json`; own database from the 1,075-parcel corridor template;
  1 request/second per host; counts only):
  - 1,075 parcels, all found on the parcel layer, 0 TaxDist conflicts.
  - Sources: south_fulton_zoning 364/365 matched (8 split);
    fulton_layer34_zoning 23 matched (of 389 candidates; 2 slivers);
    fairburn_zoning 237/239; union_city_zoning 111/112; atlanta_zoning
    335/335 (7 split); east_point_zoning 0 candidates.
  - Written: 1,070 changed, 5 unmatched_kept, 0 precedence conflicts.
    Second run: 0 changed, 1,070 unchanged (SR-9).
  - Classes against the rules: by_right 358, conditional 0, rezoning 98,
    unknown 619. FID: 19 by_right (now equal to the spike).
  - With union_city's URL broken in the clone: exit 4, the source named,
    its 112 parcels kept their values.
  - A scoring cycle on the imported data: 1,075 parcels, S9 = 5 for all
    (every jurisdiction is draft); the committed-rules check passed.

## Behaviour a human will notice

- **S9 does not change yet.** Every jurisdiction ships `draft`, so S9
  stays the stub (5) for every parcel and the metric is unchanged. S9
  moves only after you check a jurisdiction against its ordinance, set it
  to `verified` (with `verified_by` / `verified_on`), commit, and start a
  new run and baseline. The README has a verification checklist (BeltLine,
  `-C` parcels, `unlisted_districts`, M-1A).
- **You must paste the sources snippet once.** Copy the `zoning_sources`
  list from `reviews/21_zoning/sources_json_snippet.json` into
  `sources.json` as a new key inside `county_parcel_data.fulton_ga`, and
  commit it. Until then `make zoning-import` refuses with exit 1 and a
  message naming the key.
- **Run `make zoning-import` between runs**, before the new run's
  baseline. Exit 4 means a source failed: its parcels kept their old
  zoning; re-run when the source is back.
- **The runner refuses an experiment while `zoning_rules.json` differs
  from git HEAD** (each experiment records a crash naming the file).
  Commit or restore the file.
- **One new H3 data_gap flag per parcel** on the first discovery after
  this change; the old "H3 zoning unjoined" flags stay open.
- **Memos show the decoded zoning** (district, jurisdiction, class, rules
  status, source) instead of the stored provenance string.
