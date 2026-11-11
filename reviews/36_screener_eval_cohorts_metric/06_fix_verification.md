# Verification of the fixes to `reviews/36 … /05_adversarial_review.md`

Verifier: independent, fresh context, 2026-11-11. Light session: nothing tracked was edited, nothing committed, `rank.py` untouched, `--test` not run, no test score computed. Evaluator code checked at `c619c00`; the real branch moved to `2d41f6d` during the work (quotecheck and README only; `screener/autoresearch` and the three test files are unchanged since). All runs were in scratch clones under `/tmp/claude-0/-home-user-Land-Research/afef5b53-de64-56bc-a533-76d7fe240cea/scratchpad/verify_eval/` (`c1` = clone with `REVIEW_PENDING = ""` committed in the scratch copy only; `PROGRESS.log`, `p_*.py`, `mutate.py`, `mutations.out` there).

## Verdict: clear after these fixes

Do not lift the stop yet. Three new MUST-FIX items (N1-N3) undo parts of findings 2 and 3 or let the evaluator score code other than the file it checked. After them: rebuild the key, re-run the null test on it, write `06`, then lift.

## Findings 1-11

| # | Status | Evidence |
|---|---|---|
| 1 | Partly (rest waits on the rebuild) | 0.99 / 2.0 / 1,000 trials pinned by test; cell-shared mode added. My re-run of one setting (independent, noise 16, 1,000 trials, old key) gave 23 false keeps, gain 5th-95th −2.93..3.09: identical to `nulltest_oldkey_99.json`. 20-trial run of all six: process pool == serial run under another `PYTHONHASHSEED`. The docstring (lines 38-41) and the comment at line 137 cite "(06)", which does not exist, and say "on the key as built"; it was the old key. |
| 2 | Partly | Review's probe re-run: +0.4 bump on a band with positives and on one with none now both show `12.2 / 1.9 (+0.0)`; `recall` gone; win share only in the log; plain looks logged. But see N1 and N3. |
| 3 | Fixed as reproduced; new holes | (a) no log → REFUSED "no baseline"; (b) `rm` log + best at a HEAD with a discard → REFUSED (also log only, best only, zero-byte log, look rows only); (c) `__init__.py` commit then `rank.py` commit → REFUSED. Rename in the experiment commit and a copy of `rank.py` → REFUSED. Holes: N2, N4, N5. |
| 4 | Partly | `classify`: June-2016 / Nov-2018 chain → 2015-16 positive, 2017-19 "left out: bought at a real price since 2015". The same chain two years earlier is still positive (N7). Key not rebuilt. |
| 5 | Partly | `hash` / `id` refused; module-list and default-argument counters give 295 of 300 unstable scores. A first-seen-index memo and per-process salts pass both checks (N6). No false refusals: NaN, ±inf, −0.0, int/bool, sums over float sets all give 0 unstable. |
| 6 | Mostly | `fields` mandatory (TypeError test), spy through `main()`, `site_cells` / `dev_on_test_ground` / `positive_years` extracted and tested. The weight line, straddle drop and `sites` map in `build_eval.main` are still untested, and no data test uses `sites` (see mutations). |
| 7 | Fixed | Zero-byte log: header written on first append (run for a look; unit test for a baseline). |
| 8 | Fixed in the builder, too broad | `digest_shows_buyer` leaves out built and never-built alike. It also leaves out a true January owner (N8). |
| 9 | Partly | "Top 20%" wording fixed; README refusal list now matches the code. PROGRAM.md line 193 still says "12 map cells" (old key: 10 of 32 dev cells hold positives) and "95% of resamples". |
| 10 | Partly | `write_gz` has no timestamp (test); deed-file hash goes into meta. `meta.built` (today's date) still changes the fingerprint from one day to the next; no rebuild-and-compare mode. |
| 11 | Partly | Reset message names the best kept commit; 0-100 check on exact values; plain `--test` logs a row. `--decide --test` does not (N1). Test-split weakness and re-platted sites not attempted. |

## New findings, most severe first

**N1. MUST-FIX — a refused `--decide` shows the score and logs nothing.** *Verified by running.* `main()` prints `{"dev": …}` before `decide()` runs its guards. In `c1`, with `rank.py` edited and not committed: `--decide` printed `graded 12.1, hits 1.9`, then `REFUSED: uncommitted changes`, and the log had no new row. The same happens today under `REVIEW_PENDING`. With `--decide --test` the test split's score is printed too with no row (checked on a synthetic key, `p_main_synth.py`). So looks are still free and leave no trace. *Fix:* for `--decide`, print no score until `decide()` has passed its guards (its DECISION line already carries the rounded scores); refuse `--test` together with `--decide`, or log the `test` row first.

**N2. MUST-FIX — the evaluator can score stale bytecode instead of the `rank.py` it checked.** *Verified by running; first hit by accident.* A full evaluation takes 0.3 s. If `rank.py` is rewritten in the same second and with the same size as the version last imported (a one-digit weight change after `git reset --hard`), Python reuses `screener/__pycache__/rank.*.pyc`. Repro (3 of 3): score `"access": 30` → 12.2; `sed` to 90 in the same second → still `12.2`, range `5.5..85`; `touch` after 1 s → `9.6`, range `5.5..145`. The log shows the same `rank.py` hash `257dd05d2df7` with two scores. By accident, a `--new-baseline` straight after a reset recorded 12.1 at a HEAD whose code scores 12.2. The purity check, the 0-100 check and the commit all describe a file that did not run. *Fix:* build `screener.rank` by `exec(compile(checked_bytes, …))` into a fresh module, never through the import cache. Add a test with equal size and `os.utime`. This also closes two README "known gaps" (planted `.pyc`, file changed between check and import).

**N3. MUST-FIX — `--null-test` is an unlogged scoring whose output depends on the labels.** *Verified by running.* `--null-test 20` takes 24 s, writes no row, does not check 0-100, and prints `false_keeps` and `gain_p5_p95` per setting. On a synthetic pool (`p_nulloracle.py`), a `rank.py` that puts a band on top and spaces the rest widely gets `gain_p5_p95 [0.0, 0.0]` in all six settings when the band holds no positive, and non-zero when it holds one. Not tried on the real key. *Fix:* log a row before running (status `null`, `rank.py` hash); say in PROGRAM.md that the loop runs `--decide` only.

**N4. SHOULD-FIX — the git guards follow the working directory.** *Verified by running.* `_git` has no `cwd`, and the pathspec `screener` is relative to it. From `screener/` with `PYTHONPATH=..`, `--decide` returned DISCARD with an uncommitted edit to `evaluate.py` and an untracked `.py` under `screener/` (from the root: REFUSED). From another clone, a decision row was written for that clone's commit while the scored `rank.py` was an uncommitted one elsewhere. *Fix:* `cwd=<repo root>` in `_git` and `_is_ancestor`.

**N5. SHOULD-FIX — code outside `screener/` runs inside the evaluator unseen.** *Verified by running.* A root-level `random.py` committed in an earlier commit, and an untracked root-level `csv.py`, were both imported by `python -m …evaluate --decide`; neither check looks outside `screener/`. A root `sitecustomize.py` is not loaded; a `.pth` only acts from site-packages (by reading). Ignored files under `screener/` (`*.so`, other `.pyc`) are unseen too (by reading). *Fix:* refuse if any module in `sys.modules` has a file inside the repo but outside `screener/`.

**N6. SHOULD-FIX — finding 5's record id and salt survive in other forms.** *Verified by running* (`p_state.py`). `SEEN.setdefault(p[field], len(SEEN))` gives every record its file-order index on the first pass and the same value on the second: 0 unstable. `list({'a','b',…})[0]` and `repr(f)` (an address) pass the static check, give 0 unstable, and change between processes, so a re-commit re-rolls the score. Rule 3's wording forbids both; the evaluator does not catch them. *Fix:* run the second pass on a fresh exec of the checked bytes, ideally in a child process with another `PYTHONHASHSEED`.

**N7. SHOULD-FIX (operator's call) — the finding-4 chain is still a positive when the land was bought before 2015.** *Verified by running* `classify`: land sold June 2014, building sold November 2016, first shown in the 2017 digest → the developer's 2015 and 2016 records are `positive`. The window rule is empty for 2015 by construction. On the old key, positive records whose owner bought at a real price in 2012-2014 exist (counts withheld). *Fix:* open the window earlier (the deed file has the history), or state the limit in PROGRAM.md as the review asked.

**N8. SHOULD-FIX — the first-quarter rule drops a true January owner.** *Verified by running.* January owner sells on 10 February, the buyer resells in the same quarter (same day or five weeks later); the digest shows the January owner → "left out: the digest shows the buyer", was `positive`. `any(...)` flags the second deed. *Fix:* test only the earliest priced deed of the quarter.

**N9. SHOULD-FIX — `--new-baseline` blesses whatever `rank.py` is at HEAD, silently.** *Verified by running:* baseline 12.2, then an experiment at 12.1, then `--new-baseline` → second baseline row at 12.1, no warning. Any commit under `screener/` (docs, tests, quotecheck: five in the last hour on this branch) forces that step, so it will be routine. *Fix:* print the best kept score next to the new one and whether `rank.py` differs from the best kept commit; consider exempting `*.md` and `screener/tests/` from the "other files" rule.

**N10. NOTE.**
- Nothing makes the evaluator require the rebuilt key: the old key lacks `window_start` and `deed_file_sha256` and loads cleanly (`None == None`). Add a check in `load()`.
- A `best_ranking.json` with the right commit and fingerprint but an empty `order` gives "best kept 0.0" → KEEP (run; needs writing the file). Cheap check: the rescored best must round to its logged score.
- `rank.py` as a symlink to a file outside the repo is accepted; edits to the target leave the tree clean and the look row unflagged (run).
- Moving the log aside and back hides looks (run).
- `hits` to one decimal resolves a single positive record (weights 0.2-1.0): the large-bump probe still works; only the log and rule 3 stand against it.
- Commit subjects with tabs are quoted safely (run).
- In-window foreclosure deeds, priced executor's deeds, sibling buy-outs and priced transfers to the owner's own LLC count as "bought" (run); $0 trust retitles, corrective deeds and the owner's own deeds do not.

## The in-window rule's asymmetry

In year Y of the rebuilt key, no positive can have "held ≤ Y−2015 years, bought for ≥ $50k"; never-built parcels with that trait stay as negatives. Never-built records with the trait on the old key (approximated from `years_held` / `last_sale_price`):

| Year | Dev (of pool) | Test (of pool) |
|---|---|---|
| 2015 | 15 of 2,228 | 1 of 947 |
| 2016 | 111 of 2,224 | 37 of 949 |
| 2017 | 180 of 2,179 | 68 of 944 |
| 2018 | 271 of 2,141 | 101 of 917 |
| 2019 | 318 of 2,107 | 125 of 914 |

The 2015 figures are rounding at the window edge; the true count is 0. On an approximation of the rebuilt dev key, a `rank.py` that zeroes such parcels gains +0.1 (held ≤ 1 year), +0.2 (≤ 2) or +0.4 (≤ 4) shown graded; none is kept.

My view: keep the operator's rule and do not drop the never-built ones. Dropping them removes 5-15% of the 2016-19 pools and leaves the ranking of recent buyers unconstrained in the live tool. The bias is small against the +2.0 bar and points the way `rank.py` already leans. But state it in PROGRAM.md's known limits, and have the human who reads kept diffs distrust gains from cliffs in `years_held` / `last_sale_price` / `sales_count` under about 4 years.

## Documents: sentences now wrong

- **PROGRAM.md**
  - Lines 102-105, "every scoring, with or without `--decide`, is logged with a hash of `rank.py`": false for a refused `--decide` and for `--null-test`.
  - Line 160: add that the score has already been printed (until N1 is fixed).
  - Lines 165-167: the stop list omits "kept runs, none in this branch's history", "the evaluation set changed" and "another version of this evaluator".
  - Line 193: "12 map cells" and "95% of resamples" (it is 99%).
  - Lines 202-204: such parcels are now left out, so the limit needs rewording.
  - Lines 22, 35, 43, 50, 60-63 are old-key numbers, beside rules (lines 31-34, 41-42) that the key in `data/` does not follow yet.
  - Missing: the pre-2015 limit (N7), the asymmetry, and "the loop never runs `--null-test`, `--control` or `--test`".
- **`evaluate.py` docstring**
  - Lines 38-41 and the comment at 137: "(06)" does not exist; the check was on the old key.
  - Lines 49-50, "every scoring without `--decide` is logged": not `--null-test`.
  - Lines 13-17 describe builder rules not yet in `data/`.
- **`build_eval.py` docstring:** "so a test can check that a site's weights sum to 1…": no such test exists.
- **README, AutoResearch (as of `2d41f6d`)**
  - "Every scoring without `--decide` is logged as a `look` (or `test`) row": same exceptions.
  - 49 / 164 / 38 / 11, 12.2 / 1.9 / 3.4 and "Key rebuilt 2026-11-10" need refreshing after the rebuild, with the two new left-out rules added.
  - The refusal list omits "already scored".
  - Known gaps should name the same-second bytecode case and the working-directory case until fixed.

## Check on the rebuilt key

- Meta has `window_start` 2015-01-01 and `deed_file_sha256`, the same in all years; gzip mtime 0; two builds on one day give the same fingerprint.
- Pool, positive records and site weight by year and split against the old key (dev 2,228 / 2,224 / 2,179 / 2,141 / 2,107; positive records dev 30 / 29 / 29 / 19 / 12, test 10 / 10 / 11 / 7 / 7; sites 38 / 11); test share of sites; cells holding positives.
- Per `sites` hash: weights sum to 1, one block, one split. Add this as a data test.
- Zero positives with the in-window trait in each year.
- Left-out counts by reason and year. Read the deed chain of every built parcel left out by the first-quarter rule (N8) and of 2015-16 positives bought in 2012-14 (N7).
- `snapshot_date_check` "owner is not the seller" after the rule.
- Re-run `--control`, the baseline, all six `--null-test` settings at 1,000 trials, and the trait-zeroing probe; record them in `06`.

## Mutations (scratch copy; `test_eval_key`, `test_rules`, `test_screener`, `test_integrity`)

45 single-line mutations, 32 caught, 13 survive:

- E03: second pass in the same order.
- E10: look row never flags "(uncommitted)".
- E11: decision prints the exact gain instead of the rounded difference.
- E13: `hash` and `id` allowed again.
- E27: `window_start` / `deed_file_sha256` not compared across years.
- B02: a deed on the as-of date counts as bought before it (equivalent in `classify`: the first-quarter rule fires first).
- B07: first-quarter rule needs `MIN_PRICE`.
- B08: first-quarter rule skips a deed dated 1 January.
- B12: weight 1 per record in `main`.
- B13: straddling parcels kept in `main`.
- B15: site hash not stable.
- B16: `sites` not written.
- B18: `window_start` not in meta.

Full list in `/tmp/claude-0/-home-user-Land-Research/afef5b53-de64-56bc-a533-76d7fe240cea/scratchpad/verify_eval/mutations.out`.

Full suite: 578 tests pass, in a scratch clone and in the real repo; no file was created there.

## Not checked

- `build_eval.main()` end to end (needs the map server) and anything on the rebuilt key.
- The test split beyond counts.
- A real KEEP through real git (unit tests only, plus one KEEP against a doctored best file).
- Five of the six null settings at 1,000 trials.
- Power of the 99% rule.
- Python other than 3.13 (string seeding and `gauss` / `shuffle` / `choice` stability across versions by reading only).
- The static checker beyond the cases in N6.
- README outside the AutoResearch section; `AGENTS_DESIGN.md`.

Withheld (traits of positives, operator only), in `/tmp/claude-0/-home-user-Land-Research/afef5b53-de64-56bc-a533-76d7fe240cea/scratchpad/verify_eval/`: `WITHHELD_trait_positive_counts.json`, `WITHHELD_prewindow_buyer_positive_counts.json`.
