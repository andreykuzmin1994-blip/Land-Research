# 04 — Screener quick wins: code writer response (F14 decision capture, Phase A)

> Response to `01_risk_review.md` § F14, written after the code. Each risk
> item has its disposition and the test that pins it. Tests are in
> `screener/tests/test_decisions.py` unless named otherwise. Base for this
> item `7cb435a` (F14 risk review), on `4fe8080`. No eval file, `rank.py`,
> `scoring.py`, `screens.py`, `settings.toml`, `build_eval.py` or
> `PROGRAM.md` was touched.

## Operator answers this round (2026-11-09, in chat, binding)

Restated so this response stands alone:

1. **D3 (D3-01 / D3-02):** COUNT unlisted districts AND districts with
   unreadable setback values too. Root D3 round, not this folder.
2. **D2 flood scope:** "just review". D2 stays as implemented (V/VE
   review-only, floodway still blocks); no flood-rule change.
3. **Null-beats-low:** not answered. No new root sub-score lands. F14 adds
   no score of any kind: `maybe` is a mark only.
4. **"Fix":** the research.py ArcGIS error-envelope bug, the audit-path bug
   and the D1-11 `zoning_import` fetcher round. Root work, not this folder.
5. **"Can you add this yourself":** the agent, acting for the human between
   runs, applies the reviews/26 hazards prepare-mutation to `prepare.py`.
   Root work under the prepare-mutation protocol, not this folder.
6. **Step 2: build the screener quick wins.** Human-side `screener/` edits
   are authorized except the eval files (`screens.py`,
   `autoresearch/evaluate.py`, `autoresearch/build_eval.py`,
   `autoresearch/data/`, `settings.toml` defaults). No new eval baseline.

**Phase B is NOT done.** No decision reaches the evaluation. Turning
decisions into labels stays a human `--new-baseline` step, as the risk
review describes it.

## What changed

| File | Change |
|---|---|
| `screener/run.py` | `DECISION_VALUES = ("pursue", "pass", "maybe", "listed")`. `load_decisions` reads with `utf-8-sig` and accepts `maybe`. `main` takes the feature snapshot from the final survivors right before the scoring loop, into a local variable, adds a summary note when NaN or inf values were written as null, and writes `features.json` after `report.write_all`. `slope_on` is named once and used for both the slope branch and the snapshot meta (same condition as before). |
| `screener/snapshot.py` (new) | `take()` copies `build_eval.FEATURES` (imported) under `features` and `build_eval.BANNED_AS_NONE` under `live_only`, plus `parcel_id`, `lon`, `lat`, all as plain JSON values. `run_meta()` holds schema, county, run date, run time, `--limit`, slope on or off, settings SHA-256, code commit (None if git doesn't answer) and the pool name. `write()` writes `.features.json.tmp` in the run folder with `allow_nan=False`, then calls `os.replace`. |
| `screener/decide.py` (new) | Stdlib CLI: `python -m screener.decide ID {pursue,pass,maybe,listed} [reason ...] [--run DIR] [--no-features]`. Details under F14-3, F14-6 and F14-7. |
| `screener/.gitignore` | `inputs/decisions.csv` becomes `inputs/*` + `!inputs/README.md`. |
| `screener/inputs/README.md`, `screener/README.md` | `maybe`, `decide.py`, hand-editing notes, `features.json` fields, the meaning caveats (W8), the store, and that Phase B is not done. |
| `screener/tests/test_decisions.py` (new) | 38 tests. |

## Risk dispositions

| ID | Sev | Disposition | Tests |
|---|---|---|---|
| F14-1 | HIGH | **Held.** `decision` is still set on `p` only after `scoring.score` and `screens.flags`, exactly where it was. The snapshot is a local variable (`snap`) and never a key on `p`. `maybe` has no branch anywhere except `load_decisions`' allow-list. `scoring.py`, `rank.py` and `screens.py` are unchanged. Spies on `rank.learned_score` and `scoring.fixed_score` run during one offline `run.main`, slope on and then `--no-slope`, with pursue / maybe / listed / pass rows. They see no key containing `decision` and no snapshot key. `maybe` and `pursue` show in `ranked.csv`. `pass` and `listed` land in `rejected.csv` with their existing reasons. | `LeakTests.test_spy_with_every_decision`, `test_maybe_changes_no_score_flag_or_order` |
| F14-2 | HIGH | **Fixed.** Every new runtime file lands under an ignored path. `inputs/*` with `!inputs/README.md` covers `decisions.csv`, `decision_features.jsonl` and anything an editor leaves there: `.tmp`, `.bak`, Excel's `~$decisions.csv` lock file. The snapshot temp file is in the run folder under `output/`. `decide.py` only appends, so it writes no temp file. The `check-ignore` test uses the code's own name constants. It skips, and says so, if git is unavailable. | `IgnoreTests` (2), `SnapshotTests.test_crash_before_replace_leaves_only_ignored_files` |
| F14-3 | HIGH | **Fixed, option (i).** At decision time `decide.py` appends to `inputs/decision_features.jsonl`, append-only. Each entry holds the parcel's snapshot record(s), the run's `meta`, the run folder name, the decision and the date, and no note. If the run has no snapshot for the id, `decide.py` refuses (exit 2) unless `--no-features` is given. With `--no-features` it writes the decision, stores `records: []`, and prints the 25 missing feature names. Two cases have no snapshot: a rejected parcel, and a run older than this change. If the chosen run's `features.json` is unreadable, it refuses even with `--no-features`. Write order: features first, then the CSV row. A crash between the two leaves features with no decision, which is harmless. It never leaves a decision whose features are lost. | `FeatureStoreTests.test_features_survive_rerun_deletion_and_pass`: (a) same-day re-run with changed acres, (b) `output/` deleted, (c) the passed parcel absent from the next run; store bytes unchanged throughout. `CliTests.test_rejected_parcel_needs_no_features_flag`, `test_run_without_snapshot`, `test_unreadable_snapshot_refuses` |
| F14-4 | MED | **Fixed.** Allow-list only: `build_eval.FEATURES` and `BANNED_AS_NONE`, imported (`is` identity is tested). The snapshot never iterates `p.items()`. It holds no geometry, owner name, mailing address, earthwork, score or decision. It is taken before `scoring.score`: a spy `rank.learned_score` sets `acres` to 999 and changes `owner_type`, and the snapshot still holds 20.0 and `individual`. Scope is the ranked survivors, stated in the README and in `meta.pool`. Importing `build_eval` does no I/O. The whole suite runs under the offline patch that fails any HTTP call. | `SnapshotTests.test_keys_and_meta`, `test_imported_not_copied`, `test_taken_before_scoring`, `test_trial_and_no_slope_runs_are_labelled` |
| F14-5 | MED | **Fixed.** Output is JSON in `{"meta":…, "records":[…]}`, with `allow_nan=False`. NaN and inf become `null`, and the count goes in `meta.nonfinite_as_null`. A summary note says the same. The text holds no `NaN` / `Infinity` token. The write is atomic: a temp file, then `os.replace` (SR-7). Values that aren't JSON scalars become `str()`; tuples become lists. | `test_nan_is_null_and_counted`, `test_plain_values`, `test_crash_before_replace_leaves_only_ignored_files` |
| F14-6 | MED | **Fixed.** Behaviour on hand-edited files: <br>• **Missing last newline:** `\r\n` is added before the append. <br>• **BOM:** `utf-8-sig` in `run.load_decisions` and in `decide.py`. A BOM file's `pass` still drops the parcel in `run.main`. This also fixes the latent bug the review found. <br>• **CRLF and LF files:** both handled through `csv` with `newline=""`. <br>• **Reason text:** commas, quotes and newlines round-trip through `csv.writer`. <br>• **Header:** the four-column header is unchanged and the reason goes to `note`. The row follows the file's own header order, so a reordered or wider header still lines up. A file with no `parcel_id`/`decision` header is refused, and nothing is written. <br>• **Absent or empty file:** created with `run.DECISIONS_HEADER`, one header line. | `DecisionsFileTests` (10) |
| F14-7 | MED | **Fixed.** CLI rules: <br>• **Ids:** `strip()` only, internal spaces kept, matched exactly against the run's `ranked.csv` and `rejected.csv`. <br>• **Unknown id:** exit 2. Close matches are printed and never picked: ids differing only in spacing come first, then same-length prefix matches. <br>• **Rank shorthand:** none; `"1"` is just an unknown id. <br>• **Run choice:** the newest `ranked.csv` by mtime under `run.HERE/output`, and the chosen folder is printed. A tie refuses. No run refuses. `--run DIR` overrides. <br>• **Values:** exactly the four, case-insensitive. Anything else gets argparse's exit 2. <br>• **Consequence:** printed before writing. For `pass` it reads "this parcel will be dropped from future runs". <br>• **Repeats:** append-only, the last row wins, and history is kept. | `CliTests` (11) |
| F14-8 | MED | **Fixed.** `decide.py` resolves `run.DECISIONS_FILE` and `run.HERE` at call time. `setUpModule` / `tearDownModule` fingerprint the real `inputs/decisions.csv`, the real store path and the real `output/` (existence, mtime, size, hash), and fail the module if any changed. | `OfflineImportTests.test_paths_resolve_at_call_time`, module teardown |
| F14-9 | LOW | **Fixed.** `decide.py` and `snapshot.py` import stdlib plus `.run`, `.snapshot` and `.autoresearch.build_eval` only, which is AST-checked. `requirements.txt` is unchanged. | `OfflineImportTests.test_imports` |
| F14-10 | LOW | **Done.** The READMEs are updated. `PROGRAM.md` is untouched. | — |
| F14-11 | LOW | **Followed.** See the live evidence below. | — |

## Deviations from the review's wording (for the reviewer)

1. **Unknown-id candidates.** The review asks for "the nearest exact-length candidates". Ids that differ only in whitespace are listed first, because that is the likely mistake (`14F0063 LL0219` for `14F0063  LL0219`). Then come same-length ids that share a prefix of 3 or more characters, at most 5. Both lists are printed and never picked.
2. **Reason as trailing words.** `reason` is `nargs="*"`, joined with single spaces, so an unquoted reason works. A reason that needs exact spacing or line breaks must be quoted as one argument.
3. **Duplicate ids within a run.** County ids aren't guaranteed unique. Every snapshot record with that id goes into the store entry's `records` list, and the CSV row applies to all of them, as `load_decisions` already does.
4. **`evaluate --decide` on the landing commit.** As W6 notes, the human commit that lands F14 touches files other than `rank.py`, so it can't itself be scored. The next `exp:` commit is unaffected. Nothing was changed for this.

## Guard limit (SR-16 analog)

The spy tests live in `screener/tests`, which the loop's `evaluate.py` doesn't run. They hold because the loop edits `rank.py` alone and can't reorder `run.py`. Every `exp:` diff gets a human read, and the README says so.

## Gates

| Gate | Status |
|---|---|
| H1 | Met: spies, every decision value, slope on and off. |
| H2 | Met. The `python -m screener.autoresearch.evaluate` line is byte-identical before and after: `pool 2229, positives 34, k 45, hits 2, recall 0.059, positives_screened_out 1, score_range [6.5, 85.0], nonfinite 0`. `fingerprint()` is `4efd44280951` both times, and `rank_import_problems() == []`. `git diff --stat 4fe8080 -- screener/autoresearch screener/screens.py screener/rank.py screener/scoring.py screener/settings.toml` is empty. |
| H3 | Met: `check-ignore` passes on every written path and temp name. The README stays tracked. A forced crash before `os.replace` leaves only ignored files. |
| H4 | Met: feature keys `==` `build_eval.FEATURES`, banned keys kept apart, lon/lat present, run metadata present, the mutation spy, and NaN written as `null` and counted. |
| H5 | Met: the store is unchanged after a re-run, after `output/` is deleted, and after a pass. |
| H6 | Met: no final newline, BOM (and `run.main` honouring its pass), CRLF, LF, reason quoting, the four-column header, a reordered header, a headerless file refused, absent and empty files. |
| H7 | Met: see F14-7. |
| H8 | Met: the real files are fingerprinted, paths resolve at call time, there is no network import, and `requirements.txt` is unchanged. |
| H9 | Met. Root `python -m unittest discover -s tests`: **1744 OK** (base 1744). Screener `python -m unittest discover -s screener/tests -t .`: **203 OK** (134 at `4fe8080`, 165 after F8, +38 here). Docs updated. Operator answers and the Phase B status are recorded above. |

## Live evidence (counts only)

There was one `python -m screener.run --limit 30` against the public Fulton layers and USGS 3DEP, through the screener's own fetchers and cache, mostly served from the cache. It was driven with `run.DECISIONS_FILE` pointed at a scratch file and `--out` in a scratch directory outside the repo. The real `inputs/decisions.csv` was never opened for writing: its SHA-256 matched before and after. No `screener/output/` was created.

- Exit 0. 7 ranked parcels, 7 snapshot records, `meta.records` 7, `nonfinite_as_null` 0, `limit` 30, `slope` true, code commit present.
- `null` feature counts by name, over the 7 records: `rail_ft` 7 and `rail_owner` 7 (no rail within range), `last_sale_price` 2, `years_held` 2. The other 21 features: 0. `live_only` nulls: `zoning` 5 and `flu` 5. These are the "no machine-readable zoning" parcels.
- `decide.py` round trip on three ranked parcels, recording pursue, maybe and pass against the run folder: exit codes 0 / 0 / 0, 3 store lines, all 3 with features.
- The re-run gave exit 0, 6 ranked and 24 rejected, against 7 and 23 in the first run. The pursue and maybe cells read `pursue` / `maybe`. The passed parcel was in `rejected.csv` with decision `pass`, and not in `ranked.csv`.
- The scratch files were deleted afterwards. The evaluate line was identical after the live run.

Standing risks:
- SR-6: every test runs on fakes and temp paths, and the real files are fingerprinted.
- SR-7: the snapshot is written atomically under an ignored name. The CSV and the store are append-only.
- SR-8 analog: the store holds no owner name and no note. The evidence above is counts only.
- SR-9: append-only, last row wins.
- SR-10: stdlib only.
- SR-11: no new request.
- SR-15 / SR-16 analogs: the eval boundary is unchanged (H2). Labels stay off `p` until after scoring. Phase B is the human baseline step.
