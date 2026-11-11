# Verification of the evaluator's second round (N1-N10)

Verifier: independent, fresh context, light session. Evaluator code checked at `8346c39` (unchanged since, per the coordinator). Nothing was edited, created or committed in `/home/user/Land-Research`; no `experiments.tsv` or `best_ranking.json` there; `--test` not run. All runs were in scratch clones under `/tmp/claude-0/-home-user-Land-Research/afef5b53-de64-56bc-a533-76d7fe240cea/scratchpad/verify_eval2/` (`c1` = stop lifted and `BUILDER_META = ()` in the scratch copy only; `c3` = unmodified, for mutations).

## Verdict: clear after these fixes

Nothing found lets a loop that keeps to PROGRAM.md's letter raise the score without ranking better. Three small fixes should go in first:

- **F1** (the reset hint) is new damage from round 2.
- **F3** (N8, same-day resale) is in the builder, so it must precede the rebuild.
- **F2** (N6, record index) is a one-line change.

Then rebuild the key, re-run the null test on it, and lift.

## N1-N10

| # | Status | Evidence (all run in `c1` unless noted) |
|---|---|---|
| N1 | Fixed | `--decide` on an uncommitted `rank.py` printed only `REFUSED: uncommitted changes…`; log stayed at 3 lines. `--decide --test` exits 2 before any scoring. "Already scored" and "no baseline" refusals show no score and write no row. |
| N2 | Fixed | Same-second, same-size rewrite (`"access": 30` to `90`, 3 of 3): 12.2 then 9.6, range `5.5..145`. A symlinked `rank.py` is refused. |
| N3 | Fixed | `--null-test 20` (24 s) appended a `null` row with the `rank.py` hash; `--null-test 5` exits 2 with no output. |
| N4 | Fixed | From `screener/` with `PYTHONPATH=..`: a dirty `evaluate.py` plus an untracked `.py` is REFUSED; clean gives DISCARD. From `/` with an absolute `PYTHONPATH`: DISCARD against `c1`'s git. From clone `c2` running `c1`'s code with `c1`'s `rank.py` uncommitted: REFUSED, no row in either clone. |
| N5 | Fixed as reproduced; evadable (F4) | An untracked root `csv.py` and a root `random.py` committed one commit earlier are both REFUSED, in a look and in `--decide`. |
| N6 | Partly (F2) | First-seen-index memo: 9,167 unstable, REFUSED. `repr(f)` address salt: 9,235 unstable. Set-order salt passes only when it equals the child's fixed-seed value, so a re-commit cannot re-roll it. A file-order record index still gets through. |
| N7 | Not changed, as stated | PROGRAM.md "Known limits" states it and the asymmetry. |
| N8 | Partly (F3) | Via `classify`: February sale plus resale five weeks later gives `positive` in either file order. Same-day resale gives `positive` only if the January owner's deed is listed first. |
| N9 | Fixed; opened F1 | A baseline over an unjudged discard prints `NOTE: … rank.py DIFFERS from it: this baseline also accepts that change, unjudged`. After a docs-only commit it prints "is the same as there". `.md` and `screener/tests/` commits no longer force a baseline. |
| N10 | Fixed | `best_ranking.json` with an empty `order`, or the top 300 per year: REFUSED "does not give the score logged", no row. With `BUILDER_META` restored: `--decide` and `--new-baseline` are REFUSED "built before the 2026-11-11 builder rules"; a plain look still works. |

## End to end (real git, old key, scratch clone)

- **Baseline:** `--decide --new-baseline` gave `BASELINE … graded 12.2, hits 1.9`. Dev has 10,879 records, 119 positive records, 38 sites.
- **Experiment 1** (a comment edit, committed): `DISCARD — graded 12.2 vs best kept 12.2 (+0.0); hits 1.9 vs 1.9`, then `git reset --hard 3f59880`, which is the baseline commit. Win share logged as 0.000.
- **Re-run on the same commit:** REFUSED "already scored".
- **Experiment 2** after the reset: the same clean DISCARD.
- **Logged-score check:** it agreed with the rescoring in every honest run, about eight decisions.
- **Cost:** a look takes 0.41-0.47 s (was about 0.3 s); a decision about 1.1 s.
- **Not done:** a KEEP through real git.

## New findings, most severe first

**F1. MUST-FIX — the DISCARD hint deletes human commits that N9's exemption now lets through.** *Verified by running.*
- Repro: baseline B, then a human commit touching `README.md` and `screener/tests/test_rules.py`, then an experiment commit. `--decide` prints `git reset --hard <B>`, and PROGRAM.md tells the loop to run it exactly.
- Before round 2 such a commit forced a new baseline, so the reset target could not sit behind it.
- Fix: print `git reset --hard HEAD~1` when `git diff --quiet <best> HEAD~1 -- screener/rank.py` succeeds. Otherwise keep the present hint and name the commits it would drop.

**F2. SHOULD-FIX — a record's file-order index survives the child-process second pass (N6).** *Verified by running.*
- `rank.py` can tell parent from child without banned names: remaining recursion depth is 992 in the parent and different in the child.
- The second order is a fixed seed (`ORDER_SEED = 11`). A 45-line pure-Python Mersenne Twister in `rank.py` rebuilds the shuffle (checked equal to `random.Random(11).shuffle` for n = 9,235).
- A `rank.py` adding 12.5 points to "the first 200 records in file order" scored with 0 unstable. The same file with the wrong seed gave 9,235 unstable.
- The refusal text supplies n ("gave 9235 records a different score").
- It breaks rule 3's letter, so a loop that keeps to the letter won't do it; the guard just doesn't hold as claimed.
- Fix: shuffle with `random.SystemRandom()`; an honest `rank.py` does not care about the order.

**F3. SHOULD-FIX (builder, before the rebuild) — N8 remains for a same-day resale listed first.** *Verified by running.*
- `sorted(key=date)` is stable, so with two priced deeds on the earliest day `quarter[0]` is whichever the deed file lists first.
- Repro: buyer's resale listed before the January owner's sale, both 10 February, gives "left out: the digest shows the buyer".
- Fix: take all priced deeds on the earliest date and return True only if none has the digest's owner as seller.
- I did not check how the county file orders same-day deeds.

**F4. SHOULD-FIX — `foreign_modules()` trusts what the module says about itself.** *Verified by running.*
- A root `csv.py` that loads the real `csv` and puts it in `sys.modules["csv"]` ran inside the evaluator and `--decide` returned a normal DISCARD. Setting `__file__` to the stdlib path also got past the check.
- A root file needs an earlier commit or an untracked file, both against rule 1's letter.
- Fix: refuse when a root-level `<name>.py` or `<name>/` collides with `sys.stdlib_module_names`. That does not depend on `sys.modules`.

**F5. NOTE — a failed second pass reads as an unstable `rank.py`.** *Verified by running.* With `PYTHONPATH=.` and a `json.py` in `screener/autoresearch/`, the child crashed and the message was "gave 9235 records a different score". The child's stderr is dropped. The child inherits `PYTHONPATH`; relative entries resolve against `screener/autoresearch/`, which the dirty-tree check covers for `--decide` but not for looks.

**F6. NOTE — `--test` does not refuse unstable scores on the test split.** *By reading only.* `test["unstable_scores"]` is printed but only `dev`'s is checked.

**F7. NOTE — the logged-score check is to one decimal.** *Verified by running.* A `best_ranking.json` cut to the top 1,000 per year passes (harmless here). A doctored order can lower the bar by under 0.05. Needs writing a file the loop may not open.

**F8. NOTE — two tests fail if the root suite shares the process.** *Verified by running, simulated with a root-path module in `sys.modules`.* `test_foreign_modules_sees_a_repository_file_outside_screener` and `test_main_reports_an_unusable_key` fail. CI and the Makefile run the suites separately, so nothing breaks today.

**F9. NOTE — the live tool still imports `rank.py` through the bytecode cache.** *Verified by running.* `import screener.rank` showed the old weight after a same-second same-size edit. `__name__` is `screener.rank` both ways. I found no route for `rank.py` to tell exec from import other than stack depth.

No false refusals found:
- `-0.0`, int scores, thirds and NaN all give 0 unstable on the real key (NaN shows as non-finite).
- Feature values are scalars only, so the JSON round trip is exact.
- A `.venv` inside the repo works.
- `evaluate.py` imports only `screens.py`, so nothing under `screener/tests/` or any `.md` reaches the score.
- `--no-renames` only applies to the "since best" diff; a rename in the experiment commit is refused by the one-file rule.

## Tests and mutations

Full suite in the real repo: 604 tests, OK, 3 skipped, no file created.

28 single-line mutations in `c3` (`test_eval_key`, `test_rules`, `test_integrity`): 19 caught, 9 survive.

- M11: `--no-renames` removed.
- M16: a failed child pass returns 0.0 instead of inf.
- M17: a row that raises in the child is reported as 0.0.
- M18: `main()` passes no `recheck`, so the child process is never used.
- M22: `rank_source` returns bytes from a second read, not the ones checked.
- M23: `dont_inherit=True` dropped in `load_scorer`.
- M25: NaN-equals-NaN tolerance removed.
- M26: the child's return code is ignored.
- M28: the installed-prefix exception in `foreign_modules` dropped (an in-repo venv would then be refused).

## Documents: sentences now wrong (by reading)

- **PROGRAM.md 128-129:** "refuses it, before importing it and in every mode". It is never imported, and `--control` skips the check.
- **PROGRAM.md 188-189:** "Every run is appended to experiments.tsv" contradicts line 166 (a refused run logs nothing).
- **PROGRAM.md 107-108:** "every scoring that shows a number is logged". `--control` prints label-derived numbers with no row, though it does not run `rank.py`.
- **PROGRAM.md 161:** "run the exact `git reset --hard <commit>` it prints" is unsafe until F1 is fixed.
- **`evaluate.py`, `rank_problems` docstring:** "before rank.py is imported (… the way the importer compiles them)".
- **`evaluate.py` module docstring:** the `--decide` refusal list omits "commit already scored".
- **README refusal list:** headed "prints `REFUSED: ...`, exits 3", but `--decide --test` exits 2 with no REFUSED.
- **README:** "It is checked before it is imported".
- **README:** "In each of the last cases a human re-records the baseline" now follows "already scored", `--test` and "module outside screener/", where a new baseline is not the remedy.
- **README:** "Every record is scored twice… in every mode" is not enforced for the test split (F6).

## Not checked

- Anything on the rebuilt key, and `--test`.
- `--null-test` at 1,000 trials (20 only).
- Child timeout and very large child output.
- Process-pool workers under spawn or forkserver.
- pytest (not installed) and the real root suite (`psycopg` missing; simulated instead).
- Editable installs and `.pth` files.
- Python other than 3.13.18.
- `build_eval.main()` end to end.
- README outside the AutoResearch section.

Scratch files: `mutate.py`, `mutations.out` and `rank_orig.py` in the scratch folder above.
