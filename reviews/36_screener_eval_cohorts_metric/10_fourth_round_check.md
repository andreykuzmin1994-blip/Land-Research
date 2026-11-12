# Check of the evaluator's fourth round (commit c27debc, N1/N2/N3/N6)

Recorded as received on 2026-11-11 from a fresh-context reviewer that did
not write the code. One line was added by the launching session and is
marked as such, under "Tests and mutations".

**Verdict: sound after these fixes.** N2 and N6 are fixed and I could not break them. The N1 fix reopened a hole that 307ce21 refused (finding 1), and the tests would not catch two of the regressions that matter (finding 2).

Reviewer model: Claude Fable 5.2. Nothing was edited, created or committed in `/home/user/Land-Research` (tree clean at 1b4deea afterwards). No network requests were made.

All work was in `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_eval4/`:
- `p1`: pristine clone.
- `c1`: `REVIEW_PENDING = ""` and `BUILDER_META = ()` in `evaluate.py`, committed there as a scratch commit.
- `n1`: files planted at the root, then cleaned.
- `old`: checked out at 307ce21 for comparison.
- `m1`: mutations, each restored.

`--test` was never run against the key. For N6 I called `main(["--null-test","20","--test"])` with `evaluate()` wrapped to raise before scoring any split other than dev, so the sealed split could not be scored.

## N1, N2, N3, N6

| # | Status | What I ran |
|---|---|---|
| N1 | Fixed; regression (finding 1) | Plain looks in `n1`. `mkdir venv`, a real `python -m venv venv` (also run with that venv's Python), empty `site/ types/ test/`, and `types/foo.py` all give a normal look. `random/__init__.py` and a symlinked package folder are still REFUSED. |
| N2 | Fixed | 16 scenarios through real git in `c1`. With one or three discards left in place, the reset to the baseline is printed with the right count; running it left HEAD on the baseline. "Do not reset" and no command is printed for: a human docs commit before or after the left discard, a logged discard amended afterwards, a human `rank.py`-only commit, a human merge commit, and a merge whose first parent is older than the best run. A human commit before a single experiment still gets `git reset --hard <human commit>`, and running it kept that commit. A CRLF log still matches. A discard row cut to a 5-character prefix does not match. |
| N3 | Fixed as specified; evadable (finding 3) | A root `csv.pyc`, `gzip.pyc`, symlinked `csv.py` and broken symlink are REFUSED. Empty `json.so`, `.abi3.so` and `.cpython-313-…so` crash on import and score nothing. A `.so` for another Python version, a root `__pycache__/csv.cpython-313.pyc` and a `csv/` folder without `__init__` are not flagged and do not run. |
| N6 | Fixed | With the guard above, 1b4deea asks only for `dev` and reaches the null test; 307ce21 asks for `test`. |

## New findings, most severe first

**1. SHOULD-FIX — the N1 fix lets a root folder without `__init__` run code. Introduced by this commit. Verified by running.**
- Repro: an empty root folder `msvcrt/`, plus `_winapi/CREATE_NEW_CONSOLE.py`. `subprocess` imports `msvcrt` as a namespace package, then `from _winapi import CREATE_NEW_CONSOLE` runs the file.
- The planted code ran, swapped in a working `subprocess` and deleted its own `__file__`. A look returned exit 0, and `--decide` returned a normal DISCARD.
- At 307ce21 the same layout is REFUSED. `git status` shows only `?? _winapi/`.
- Fix: exempt a folder without `__init__` only when the standard module of that name exists on this Python (`venv`, `site` and `types` do; `msvcrt` and `_winapi` do not).

**2. SHOULD-FIX — the new tests miss two regressions. Verified by running.**
- The N2 test reads only the last printed line. A mutant that prints `git reset --hard <best>` and then "Do not reset" passes.
- N6 has no test; reverting it passes.

**3. SHOULD-FIX (older than this commit) — a root shadow that runs can switch the check off. Verified by running.**
- A root `csv.py` or `csv.pyc` that adds `sys.stdlib_module_names = frozenset()` gets a normal look, at 307ce21 and at 1b4deea. So does one that deletes its file and restores it at exit.
- The listing runs after the shadow, in the same process, so it cannot be made airtight there.
- Fix: say in the README that it is best effort, or launch with the root after the standard library (`python -P` plus an appended path) and refuse otherwise.

**4. SHOULD-FIX (older than this commit) — the printed `git reset --hard` deletes uncommitted work outside `screener/`. Verified by running.**
- Repro: an uncommitted edit to root `research.py`, then a discarded experiment. Running the hint removed the edit. Both branches of the hint do this.
- Fix: print `git reset --keep <commit>`; I ran it and it kept the edit.

**5. NOTE — a change in git's short-hash length gives a false "Do not reset". Verified by running** (`core.abbrev 9` between two discards). Git lengthens short hashes by itself at about 16,000 packed objects; the repository has about 4,900. Fix: compare full hashes.

**6. NOTE — `types/__init__.pyi` or `site/__init__.txt` still refuses. Verified by running.** The glob is `__init__.*`.

**7. NOTE — the hint trusts the log. Verified by running.** A hand-added discard row naming a human commit gets that commit reset away. By reading: a human who runs `--decide` on their own `rank.py` commit and leaves it is a logged discard too.

**8. NOTE — `--null-test N --test` drops `--test` without saying so. By reading.**

## Tests and mutations

- **Full suite not run.** `shapely` is not installed here and installing it needs the network. In the pristine clone 13 of 14 test modules fail to import (88 tests ran, 13 errors).
- *(Added by the launching session, not the reviewer: with `screener/requirements.txt` installed in a scratch virtual environment, the full suite in a fresh clone of 1b4deea ran 611 tests, OK, 3 skipped.)*
- With an import-only `shapely` stub outside the clones, the two changed modules run 148 tests: 144 pass, 3 skipped, 1 error that needs real geometry.
- 18 single-line mutations: 9 caught, 9 survived. The fixed-seed mutant from 09 is now caught, and so are the reverts of N1, N3 and N2.
- Survivors:
  - the two in finding 2;
  - `__init__.py` only in the glob;
  - only the first extension suffix;
  - dropping the `endswith` test;
  - dropping `between and`;
  - any decision row counted as a discard;
  - discards of any fingerprint counted;
  - look rows counted.

## Not checked

- The full suite with real `shapely`.
- A KEEP through real git.
- HEAD itself a merge that brings in a human branch.
- A baseline re-recorded on an older commit after discards.
- An unreadable root folder (I ran as root).
- Windows or a case-insensitive filesystem.
- The document changes, beyond reading the diff.

Scripts are in `tools/` under the scratch folder: `case.sh`, `lib.sh`, `ns_plant.py`, `n6_check.py`, `mutate.py`.
