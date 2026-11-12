# Check of the evaluator's sixth round (5d5837d) and the owner-name cut rule (11bb4fa)

Recorded as received on 2026-11-12 from a fresh-context reviewer that did
not write the code.

**Check of 5d5837d (evaluator, sixth round) and 11bb4fa (same_owner cut rule, key rebuilt)**

Reviewer: Claude Opus 5.5, fresh context. Nothing was edited in `/home/user/Land-Research`. Its HEAD moved to 84fd66f while I worked; another session did that, not me. I never ran `--test` and made no network requests. All my work is in `/tmp/claude-0/-home-user-Land-Research/073c2925-b364-5a5f-90db-1d7fbc42afeb/scratchpad/review_eval6/`:
- `p1` is a pristine clone.
- `c1` has `REVIEW_PENDING = ""`, committed there as a scratch commit.
- `m1` is where the mutations ran.
- The scripts are in `tools/`.

**Verdict A: sound.** Every claim holds; what remains is NOTE-level.
**Verdict B: sound after these fixes.** No label is wrong and the counts in the commit message are right. But the new rule also cuts tax-roll names, which are never cut, and that makes doubtful matches, one of them clearly false.

| Claim | Result | What I ran |
|---|---|---|
| A1: discard hashes made full by `rev-parse` | Holds | Real git at `core.abbrev 4`. The note-12 collision (a reset-away discard, then a human commit sharing its prefix) now prints "Do not reset". Changing abbrev 4→9, and the "takes back 2" hint with an uncommitted root edit, still work, and the edit survived. |
| A2: namespace folder elsewhere is no module; frozen counts as present | Holds | The note-12 plant is REFUSED with empty `msvcrt/` and `_winapi/` placed outside the repository by four routes: on `PYTHONPATH`, in the working directory, after the root, and inside a zip. `_frozen_importlib/` and `ntpath/` pass, also with `-X frozen_modules=off`. |
| A3: `__init__` must be a file | Holds | `random/__init__.py` as a folder, a broken symlink or `/dev/null` passes, and nothing runs. A symlink to a real file is REFUSED. `types/__init__.py/` passes. |
| A4: test hard-codes msvcrt/_winapi; root on `sys.path` | Holds as written | But see NOTE 3. |
| A5: PROGRAM.md "never --hard" | Holds | By reading. |
| B: +6 positives, none removed | Holds | Counts from the parent's and the new key. One positive each in 2015, 2017, 2018 and 2019, and two in 2016, all dev. Dev sites 33→35, test 10. No pool record was removed, and 6 were added (the new positives). |
| B: features change on 6–9 records a year | Holds | 8, 7, 6, 6 and 9 records (36 in all). `inherited` also changes on 1 record a year, which the message does not mention. |

**New findings**

**SHOULD-FIX 1 (B): the cut rule is applied to tax-roll names and to whole last words, so it matches different owners. Verified by running.**
- How I ran it: I took every (deed seller, digest owner) pair the builder asks about. That is 13,310 distinct pairs over 3,521 parcels. The deeds were read with `fulton._read_cama_zip`, and the owners came from the cache-only replay in `keycheck_agent/`. I compared the parent's `same_owner` with the new one.
- 51 pairs change, all from no match to match. I read every one. 40 look right: cut legal forms, "AS TRUSTEE/EXECUTOR" tails, typos.
- 12 of the 51 match only because the *digest* name (30 to 50 characters, not cut) loses its last word.
  - Dropping "LLC", "INC" or "LP" turns `owner_type` from entity to individual. That opens the loose people rule, which accepts any extra words.
  - Doubtful pairs of this kind:
    - a parent company against two of its subsidiaries;
    - a national builder against its state subsidiary;
    - two limited partnerships sharing their first two words;
    - a person against "that person & another LLC";
    - a family company against family members.
  - One is clearly false: a JR seller against a digest name ending in SR (the dropped word was "SR").
- Two more are deed names of 29 characters. At 29 the 30th character was a space, so the last word was complete, not cut. One drops a fund number ("PARTNERS II" against "PARTNERS LTD"). The other drops a clashing middle name.
- Effect on the key: no label moves. 13 of the 36 feature-changed records (6 dev, 7 test) rest on these doubtful matches. The live screener gets the same matches.
- Minimal repro with made-up names:
  - `same_owner("MAPLE CROSSING", "MAPLE CROSSING RIVERSIDE TOWNHOMES LLC")`: False before, True now.
  - `same_owner("SMITH JOHN WILLIAM ALEXANDER JR", "SMITH JOHN WILLIAM ALEXANDER SR")`: True now.
- Fix: cut only the deed side (every caller passes the seller first), and only when it is exactly 30 characters. Simulated, that keeps both intended sites and 35 of the 51 changes. It also loses a few true ones (a typo pair and an "INDIVIDUALLY" pair). Then rebuild. This is the operator's call.

**NOTE 2 (A): a pruned discard can still stand for a human's commit. Verified by running.**
- The reset-away discard must be pruned (reflog expiry plus gc). A later human commit must then share its logged prefix.
- When both happen, the logged short hash resolves uniquely to the human's commit. The hint then said "takes back 2" and would have removed it.
- At 7 characters this is about 1 in 2^28 per pair.
- Related: `rev-parse` also resolves revision expressions. A hand-written row of `HEAD~1` or a branch name got a human's commit reset away (note 10's finding 7 class).
- Fix: log full hashes from now on, accept only hex rows, and check the resolved commit's subject against the row's `description`.

**NOTE 3 (A): the six repository-exclusion mutants still survive. Verified by running.**
- With the loader check in place, the root's own namespace folder gives a spec with no loader anyway. So the exclusion only matters when a repository subfolder is on `sys.path`.
- That case is unguarded, and has been since before this commit. With `PYTHONPATH=<root>/reviews` and a `msvcrt.py` there that raises `ModuleNotFoundError`, the file ran and the look exited 0.
- Fix: refuse when any `sys.path` entry other than the root lies inside the repository.

**NOTE 4: tests.** No test covers the frozen or builtin checks. In B, no test pins the length condition: the "only a name at the limit" case passes without it.

**Tests and mutations**
- Full suite in `p1` with `tv`: 625 tests, OK, none skipped.
- A: 30 single-line mutations, 13 caught, 17 survived.
  - Survivors that matter: frozen check dropped; builtin check dropped; the five exclusion mutants; find_spec error → True; `__init__.py` only; `.py`/`.pyc` only; any fingerprint; any decision row; `between and` dropped.
  - Equivalent now: no `^{commit}`, `discard("")` dropped, empty-commit filter dropped (an empty row gives nothing; ran it).
  - One survivor was a variant of the fix.
- B: 9 mutations, 4 caught, 5 survived: no length condition, `>= 30`, `== 30`, no space check (equivalent) and no strip (equivalent).

**Not checked**
- The current tax-roll layer. It is not cached, so the live tool's pairs were judged from the 2015–2019 digest owners only.
- A KEEP through real git.
- Windows, or a case-insensitive filesystem.
- A writable site-packages route.
- Map-layer features.
