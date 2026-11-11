# Verification of the evaluator's third round (commit 307ce21, F1-F6)

## Verdict: the third round is sound

No BLOCKER or MUST-FIX. F1, F2, F3, F5 and F6 are fixed; F4 is partly fixed. Three SHOULD-FIX remain, and one of them (N1) is a false refusal this commit introduced.

Light, read-only session. Nothing was edited, created or committed in `/home/user/Land-Research` (tree clean, no `experiments.tsv` or `best_ranking.json`; the `__pycache__` there predates my first command). `--test` was not run anywhere. All runs were in clones under `/tmp/claude-0/-home-user-Land-Research/afef5b53-de64-56bc-a533-76d7fe240cea/scratchpad/verify_eval3/` (`c1` = stop lifted and `BUILDER_META = ()`, `c3` = pristine, `m1` = mutations).

## F1-F6

| # | Status | What I ran |
|---|---|---|
| F1 | Fixed; one gap (N2) | (a) Baseline, human commit (`screener/README.md` + `screener/tests/test_rules.py`), comment experiment: `git reset --hard <human commit>`. Running it left the human commit at HEAD and the tree clean; a second experiment decided normally. (b) Two and three discards left in place: reset to the best commit plus "also removes 1" / "2 commit(s)", both counts right. Best == HEAD~1, amend instead of reset, and HEAD~1 a human merge commit all point at the right commit. HEAD~1 missing (shallow clone, amended root) cannot reach the hint: it refuses earlier. |
| F2 | Fixed | Five plain looks on the real `rank.py`: all exit 0 with byte-identical output (graded 12.2, hits 1.9, 0 unstable). Also fine under `PYTHONHASHSEED=0/1/2`, `python -O` and `PYTHONINSPECT=1`. The unstable refusal has no digit, in `main` and in `decide`. |
| F3 | Fixed | 18 direct cases on `digest_shows_buyer`. Same-day pair gives the same answer in both file orders (and `classify` agrees in both). An unrelated first-day seller plus the January owner's deed later gives True in either list order. Unpriced and non-transfer first-day deeds are ignored. Trailing spaces, "LLC" / ", L L C" and a name cut at 29 characters all match; the call `same_owner(deed seller, digest owner)` is right. |
| F4 | Partly (N3); new false refusal (N1) | Real repo: `foreign_modules()` returns `[]` and no root entry collides. A root `csv.py` that disguises itself is now REFUSED. Dotfiles, `CSV.py` (Linux) and a root `screener.py` do not refuse, and none of them runs. |
| F5 | Fixed | 08's reproduction (`PYTHONPATH=.` plus `json.py` in `screener/autoresearch/`) now gives a normal look. `sys.executable` set to a missing file, `/bin/false` or `/bin/echo`: "could not run, so nothing was scored", exit 3, no row. |
| F6 | Fixed, by reading and its unit test | `test` is scored before the stability check and is included in it. |

**Second-pass leaks (task 2):** nothing else found. All refusals exit 3 and a refused run leaves no row. The yes/no an unstable refusal gives depends only on `rank.py` and the features, never on labels. The NaN refusal still prints a count, but the pool size is public and no longer helps anyone rebuild the order.

## New findings, most severe first

**N1. SHOULD-FIX — a root `venv/`, `site/` or `types/` folder refuses every mode.** *Verified by running.*
- Repro: `mkdir venv` (or `python -m venv venv`) at the root, then a plain look: `REFUSED: … (venv (shadows a standard module)): run from a clean checkout`. Same when run with that venv's own Python.
- `.gitignore` already lists `venv/`, so this layout is expected. A folder without `__init__.py` cannot shadow a standard module, so the refusal is false.
- Fix: count a folder only when it holds an `__init__.*` file.

**N2. SHOULD-FIX — F1's second branch prints a reset that deletes a human commit, and leaves the judgement to the loop.** *Verified by running.*
- Repro (c): baseline B, human docs commit H, experiment 1 discarded and not reset, experiment 2. Output: `git reset --hard <B>`, then "(… also removes 2 commit(s) … If any is not an experiment, stop and ask a human.)". Running the command removed H. Same with H made after experiment 1.
- The command comes first and the warning second. PROGRAM.md says to stop on any such warning; the evaluator says to stop only "if any is not an experiment". The two disagree.
- Fix: in that branch, print the command only when every commit in `best..HEAD~1` is logged as a discard in `experiments.tsv`. Otherwise print "stop and ask a human" and no command.

**N3. SHOULD-FIX — F4 still misses a root `csv.pyc` (and `.so`).** *`.pyc` verified by running; `.so` by reading.*
- Repro: compile the self-disguising `csv.py` to a root `csv.pyc` and delete the source. The planted code ran inside the evaluator; a look and `--decide` both returned normally. Git ignores `*.pyc`.
- Same class as the original F4: it needs an untracked root file, against rule 1's letter.
- Fix: match the name against every suffix in `importlib.machinery.all_suffixes()`, not only `.py`.

**N4. NOTE — a root `sitecustomize.py` runs when `PYTHONPATH=.` is set.** *Verified by running.* It can delete itself from `sys.modules` and the look succeeds. It needs an environment variable the loop's printed command does not set.

**N5. NOTE — a merge can make the hint point outside the baseline's history.** *Verified by running.* HEAD is a merge whose first parent holds the best `rank.py` through a cherry-pick. After the printed reset, the next `--decide` refuses ("none is in this branch's history"), so it fails safe.

**N6. NOTE — `--null-test N --test` now scores the sealed split.** *By reading.* Nothing is shown or logged; before this commit that path never touched the test split.

**N7. NOTE — "This is the setup, not rank.py" is also printed when `rank.py` itself makes the second process fail** (a crash at module level, or the 600 s timeout). *By reading.* Misleading only; the run is still refused.

## Tests and mutations

- Pristine clone: 610 tests, OK, 3 skipped. In `c1` four tests fail, all because the stop is lifted there.
- 15 single-line mutations in `m1`: 14 caught. All five F1 mutations and all three F3 mutations are caught, so a regression of either would fail the suite.
- One survives: putting the fixed seed back (`random.Random(11)`). No test checks that the order differs between runs.

## Nothing else broken

`--control` (13 s), `--null-test 20` (24 s) and `--null-test 5` (exit 2) work. The pristine clone still refuses `--decide` and `--decide --new-baseline` with the stop message and creates no file. Besides `screener.*`, the evaluator attempts no import outside the standard library.

## Not checked

- `--test`, in any clone.
- A KEEP through real git.
- Anything on a rebuilt key; `build_eval.main()` end to end.
- A case-insensitive filesystem (`CSV.py` with `PYTHONCASEOK`), Windows, or a compiled `.so` shadow.
- The child timeout and very large child output.
- The document changes in the commit, beyond PROGRAM.md lines 158-164.
- How the county file orders same-day deeds.

Scratch files: `mutate.py`, `mutations.out` and `trace_imports.py` in the scratch folder above.
