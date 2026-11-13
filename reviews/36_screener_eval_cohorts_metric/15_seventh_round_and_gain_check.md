# Check of the evaluator's seventh round (cbc059d) and MIN_GAIN 2.5 (041d282)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code. What was done about it is at the end.

**Check of cbc059d (evaluator, seventh round) and 041d282 (MIN_GAIN 2.0 → 2.5)**

Reviewer: Claude Opus 5.5, fresh context. I edited nothing in `/home/user/Land-Research`, never ran `--test` or `build_eval`, and made no network requests. My work is in `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_eval7/`: `p1` is a pristine clone, `v1` is the setup probes, `c1` is real git with `REVIEW_PENDING = ""` committed there as a scratch commit, `m1` is the mutations, and the scripts are in `tools/`.

**Verdict: sound.** No blocker or must-fix. Normal setups are not refused. The gaps below need someone who controls `PYTHONPATH` or writes log rows by hand.

| Claim | Result | What I ran |
|---|---|---|
| Only all-hex rows are looked up | Holds; partly against its own aim | Real git. One discard gives `reset --keep <parent>`. Two left in place give "takes back 2". Rows of `HEAD~1`, a branch name, empty, or upper-case give "Do not reset". |
| A repository folder on `sys.path` refuses | Holds for an honest setup | NOTE 3's plant (`PYTHONPATH=reviews/x`, `msvcrt.py`) is REFUSED now; the parent passed it with rc 0. Not refused: `.venv` and `venv` (activated, `-S` with the site-packages path, `-I`), `PYTHONPATH=.` / `$PWD` / `$PWD/`, running from `/tmp`, `-P`, a symlinked checkout, an editable `.pth` pointing at the root, and the standard `discover … -t .`. |
| Builtin and frozen checks have tests | Holds | Three mutants are caught. |
| MIN_GAIN 2.5 is consistent | Holds | The message prints "+2.5"; PROGRAM.md and the tests match; README has no number. |

**Findings**

**SHOULD-FIX 1: the check runs after the planted code, so a plant can remove itself. Verified by running.** `subprocess` imports `msvcrt` at evaluate.py line 97, before `foreign_modules`.
- Repro: a `reviews/plantB/msvcrt.py` that deletes its own entry from `sys.path` and then raises `ModuleNotFoundError`. With `PYTHONPATH` pointing there, the look exits 0 and the file ran.
- The same holds before this commit for a root `msvcrt.py` that deletes its own file.
- Fix: check before any import that could be shadowed, or run in isolated mode. For example, re-exec under `-I -P` with the root added explicitly.

**SHOULD-FIX 2: two name exemptions skip the check. Verified by running.** Each of these exited 0 with the planted file run:
- `PYTHONPATH=reviews/site-packages`, because any path with a part named `site-packages` is exempt;
- `PYTHONPATH=.venv/evil` while that venv is in use, because anything under `sys.prefix` is exempt.

Fix: exempt only the entries `site.getsitepackages()` lists, plus the standard library's own folders.

**SHOULD-FIX 3: the screener suite now fails when run without `-t .`. Verified by running.** `python -m unittest discover -s screener/tests` gives 2 failures (`test_foreign_modules_sees…`, `test_main_reports_an_unusable_key`), because `screener/tests` is on `sys.path`. The parent passed this run. The documented command and CI use `-t .`. Fix: say so next to the test command, or let those tests remove the entry.

**NOTE 4: a hex or all-digit ref name still resolves. Verified by running.** With branch `2026` pointing at a human's docs commit between the best run and HEAD, a hand-written discard row `2026` turns "Do not reset" into "takes back 2". The human's commit would be reset away. Fix: keep a resolved hash only if it starts with the logged text, and require at least 7 characters.

**NOTE 5: the refusal message is misleading. Verified by running.** It says "modules were loaded … outside screener/ (screener (a repository folder on sys.path)) … run from a clean checkout". Running from `screener/` with `PYTHONPATH=..` gets that, and so does a stale `PYTHONPATH=src` folder that does not exist. Fix: name the cause (the working directory or `PYTHONPATH`).

**NOTE 6: one doc wording is stale. By reading.** PROGRAM.md and the docstring still say "about 2% or less", but the measured worst case at 2.5 is 2.4%. The docstring points to note 07 for the measured rates; they are now in note 14.

**Tests and mutations**
- Full suite in `p1` with `tv`: 630 tests, OK, none skipped.
- 23 single-line mutations: 10 caught, 13 survived.
- Equivalent survivors (4): dropping `where != ROOT`, `entry or "."`, `+` → `*`, dropping `or ""`.
- Survivors that matter (9):
  - no test checks that an in-repo venv is *not* refused: the three exemption-removal mutants survive;
  - only direct children of the root checked;
  - `sys.path[1:]`;
  - `.absolute()` instead of `.resolve()`;
  - `match` instead of `fullmatch`;
  - upper-case allowed;
  - `[0-9a-z~]` allowed (the test only uses `HEAD~1`).
- MIN_GAIN mutants: 3 of 3 caught.

**Not checked**
- pytest (not installed).
- A Python installed inside the repository (by reading, it is exempt).
- Windows, or a case-insensitive filesystem.
- A KEEP through real git.
- The null-test numbers in note 14 (not re-run).

## What was done (launching session)

- NOTE 4: a hash that git resolves is kept only if it starts with the logged text, so a ref name such as `2026` stands for nothing. Test added.
- NOTE 5: the refusal names the working directory and `PYTHONPATH` as the likely cause.
- NOTE 6: PROGRAM.md and the docstring give the measured rate (2.4% in the worst case) and point to `14`.
- SHOULD-FIX 3: the README says the suite is run with `-t .`.
- SHOULD-FIX 1 and 2: written into the README's known gaps. Both need someone who sets `PYTHONPATH` or plants files against rule 1; the loop's printed command does neither.
- Then the stop was lifted (`REVIEW_PENDING = ""`), as `07`'s checklist allows once its steps are done.
