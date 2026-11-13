# Check of the evaluator's fifth round (commit 3cc4067)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code.

**Verdict: sound after these fixes.** All eight findings from note 10 are fixed or documented, and running them bears that out. The new prefix match brings in one way for the reset hint to remove a human's commit (new finding 1). The `msvcrt`/`_winapi` fix can be bypassed from outside the repository (new finding 2). The test for finding 1 checks against the very function it is meant to test (new finding 3).

Reviewer model: Claude Opus 5.5. I edited nothing in `/home/user/Land-Research`. Its HEAD moved to ed7c4c2 during the review; another session did that, not me. I made no network requests and never ran `--test`. All work was in `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_eval5/`:
- `p1` is a pristine clone.
- `c1` has `REVIEW_PENDING = ""` and `BUILDER_META = ()`, committed there as a scratch commit.
- `m1` is where the mutations ran.
- The scripts are in `tools/`.

| # | Status | What I ran |
|---|---|---|
| 1 | Fixed; bypassable (new 2) | An empty `msvcrt/` plus `_winapi/CREATE_NEW_CONSOLE.py` is refused. I tried it from the repository root and from another directory, with `PYTHONPATH` set absolute, relative (`.`, `../c1`, `screener/..`) and through a symlink, with the working directory a symlink to the root, and with `''` on `sys.path`. `random/__init__.py` and a symlinked package are refused. `venv/` (empty and real), `site/`, `types/`, `test/` and `types/foo.py` pass. |
| 2 | Fixed | Both regressions are caught by mutation. |
| 3 | Documented | README only. |
| 4 | Fixed | I ran the printed `--keep` hint through real git. Uncommitted, staged and new work at the root survived, in both branches of the hint (one discard, and a hint that takes back three). With `rank.py` dirty, git refused and HEAD stayed on the discard. |
| 5 | Fixed | Changing `core.abbrev` from 7 to 9 between discards now gives the reset. |
| 6 | Fixed | `types/__init__.pyi` and `site/__init__.txt` pass. |
| 7 | Documented | README only. |
| 8 | Fixed | With `evaluate()` guarded to stop on any split but dev, the message prints and only `dev` is scored. |

## New findings

**1. SHOULD-FIX: prefix matching lets a human's commit count as a logged discard. Introduced by this commit. Verified by running.**
- Repro in `c1`, with `core.abbrev 4`:
  - discard X is logged as `1820` and then reset;
  - discard D1 is left in place;
  - a human docs commit is built (`tools/collide.py`) so its hash starts `1820e…`; git shows it as `1820e`;
  - the next discard prints `git reset --keep 2df6` and "takes back 2". That removes the human's commit.
- Under the old exact match, `1820e` ≠ `1820`, so the hint would have said "Do not reset".
- A hand-cut row such as `f` now matches every commit starting with `f`; I ran this too. Note 10 had found that a 5-character prefix did not match.
- At 7 characters a collision is rare, but its chance grows with each discard logged.
- Fix: turn each logged discard into a full hash with `git rev-parse --verify --quiet <d>^{commit}`, so an ambiguous prefix fails, and compare full hashes. Or log full hashes from now on.

**2. SHOULD-FIX: `_stdlib_has` counts a namespace folder outside the repository as the standard module. Verified by running.**
- Repro: put empty `msvcrt/` and `_winapi/` folders anywhere on `sys.path` outside the repository (I used `PYTHONPATH`; by reading, a writable site-packages works too). Then plant the root `msvcrt/` and `_winapi/CREATE_NEW_CONSOLE.py`.
- The planted file ran (`tools/ns_plant2.py`). A plain look exited 0, and `--decide` gave a normal DISCARD.
- Cause: `PathFinder.find_spec` returns a spec with `loader None` for a namespace folder.
- Fix: return `spec is not None and spec.loader is not None`.

**3. SHOULD-FIX: the finding-1 test asks `ev._stdlib_has` what to expect. Verified by running.**
- If `_stdlib_has` is mutated to always return True, the test expects no flags and still passes. That mutant has the same effect as reverting finding 1.
- No test puts the scratch root on `sys.path`, so none of the six mutations of the repository exclusion is caught.
- Fix: hard-code `msvcrt`/`_winapi` as missing on non-Windows, and add a test with the tmp root on `sys.path`.

**4. NOTE: PROGRAM.md does not say what to do when git refuses the `--keep` reset. Verified by running.**
- The refusal leaves HEAD on the logged discard, with `rank.py` dirty. The next decision can recover from there, but a loop might fall back to `--hard`.
- Fix: add "if git refuses the reset, stop and ask a human; never use `--hard`."

**5. NOTE: some refusals are false. By reading, plus one run.**
- An empty root folder named for a frozen-only module (`_frozen_importlib`) is refused, though that folder would not load.
- A folder named `__init__.py` inside `types/` is refused (run).

## Tests and mutations

- The full suite in `p1` with the `tv` virtual environment: 621 tests, OK, 3 skipped.
- 28 single-line mutations: 11 caught, 17 survived. One survivor is my proposed fix for finding 2; one (`_rows` in place of `_decisions`) is equivalent.
- Survivors:
  - `_stdlib_has` always True;
  - no builtin check;
  - six mutations of the repository exclusion;
  - an error in `find_spec` returning True;
  - `__init__.py` only;
  - `.py`/`.pyc` only;
  - the empty-commit filter dropped, so an empty row matches everything;
  - `between and` dropped;
  - any fingerprint counted;
  - any decision row counted;
  - the `--null-test` message dropped.

## Not checked

- A KEEP through real git.
- Windows, or a case-insensitive filesystem.
- A Python whose standard library lives inside the repository.
- Finding 3's bypass again (it is documented as best effort).
- The documents, beyond reading the diff.
