# 35 — Screener lead layer, batch A: reviewer decision (Tier 1)

Reviewed commit `f2e3414` (staged when the review began; same diff) against
`01_code_writer_note.md`. Fresh-context reviewer (Claude Fable 5.2), did
not write the code. Nothing under `screener/` was edited; no network
request was made. Suite: 446 tests, OK, offline.

## Checked, nothing found

- **Leak rule.** Read `run.main`: `_lead_owner_built_next_door`, the
  built-on flag line and `grade.attach` all come after `scoring.score`,
  `screens.flags`, the sort and F6-F10. Parcel keys at the end of a run,
  HEAD against this commit: exactly five new keys, all with the `lead_`
  stem. Mutation (the next-door key set before scoring, in a scratch
  copy): four tests fail, the spy among them. Limit: the spy matches the
  stem, so a later key without it rests on the hand-kept list in
  `test_every_key_carries_a_stem`.
- **Eval files.** `screens.py`, `scoring.py`, `rank.py`, `autoresearch/`
  are not in the diff; `settings.toml` only gains lines.
- **Facts.** The real `facts.json` (1,581 records) holds no owner name,
  mailing or site address from `ranked.csv`. Missing, empty, binary,
  wrong-shaped or directory `facts.json`: the decision is still recorded.
  Nothing in `autoresearch/` reads either file; both stores are ignored.
- **Edited tests.** `clear_cache()` clears what `rmtree(http)` used to
  clear (slope lived there); `cached()` now reads more, so "never cached"
  is stricter. No assertion weakened, none vacuous.
- **Slope cache mechanics.** Bad answers are not kept; the key holds the
  whole request, and 24 orderings of the same constraint features gave
  one key; the lifetime is set on every `main` and restored in tests; it
  is only read inside the thread pool.
- Settings validation, determinism, the digest's column count under
  hostile county text (11 pipes held).

## Findings

1. **Must-fix: a failed or absent check can grade A and read clear**
   (`grade.py:202-209`, `226-242`, `290-291`; `digest.py:101`;
   `README.md:317`). Reproduced end to end: with Soil Data Access
   answering 503, both parcels got `lead_grade` A, an empty
   `lead_not_checked`, the brief line "no watch item was found" and
   `leads.md` "Grade A: every check clear". `run.py:181` says that
   service is down nightly. The same happens when the slope service
   fails (flag "slope check failed", steep-ground screen not applied,
   grade A), with `--no-slope`, with soils off or `[soils] max_parcels`
   below the frontage cap (soils is not in the caps list, `run.py:457`),
   with `acreage_mismatch` (`Fit.map_mismatch` is ignored; 4 of the 250
   graded Fulton parcels, none A that day), and when the owner group's
   adjacency was not checked. Fix: list these in `lead_not_checked` and
   count them; at the least reword the A headline, the digest title and
   the README sentence.
2. **Should-fix: a facts failure blocks the decision** (`decide.py:225-230`).
   `append_facts` is unguarded. A `NaN` in `facts.json`, or an unwritable
   `decision_facts.jsonl`, raises after the features entry is appended:
   traceback, no decision row (reproduced both). Catch `OSError` /
   `ValueError` and print the "no report facts kept" line.
3. **Should-fix: the brief cleanup deletes files it did not write**
   (`report.py:42`, `107-109`). On a re-run it removed
   `2026_site_visit_notes.md`, `100_acre_comparison.svg` and
   `001_14F0001_0001_my_notes.md` from `briefs/` (reproduced); the test
   only tries names that cannot match. Delete from a list of the last
   run's files, or say plainly in the README what is removed.
4. **Note: a kept slope answer is one draw, and the output does not say
   so** (`checks.py:47-57`, `README.md:44`, `359`). The README records
   that 22% of identical slope requests changed overnight; the first
   answer now decides `max_steep_pct` for ten years. "Does not change"
   is true of the survey, not of the service's answer. Suggest one
   summary note (answers reused, oldest date). Also: `--refresh-slope`
   skips the kept answer, so a flaky service then leaves parcels
   unscreened that had a good one.
5. **Note: wording.** "a recent buyer rarely sells" (`grade.py:223`) is
   an untested rule of thumb stated as fact, "bought" covers any priced
   deed, and a future-dated deed prints "bought -0.3 years ago". "Next
   door" / "adjacent and built on" (`grade.py:217`,
   `owner_clusters.py:279`) includes parcels joined only through a
   chain; the existing line says "directly or through each other".
6. **Note: rule edges.** A `hard` parcel beyond a cap reads "not
   graded", not "blocked" (`grade.py:235`). An unknown frontage or soils
   state counts as clear (`grade.py:194`, `204`); only three states
   exist today. `max_hydric_pct = 0` marks every checked parcel, while
   `recent_purchase_years = 0` means off.
7. **Note: markdown.** Links, HTML and backticks in an owner name or
   address pass into `leads.md` unescaped, as in the existing briefs.

## Verdict

**GO with fixes.** The scores, the order and the evaluation are
untouched, so nothing here blocks the commit from standing. Fix 1
before anyone reads grade A as "clear"; 2 and 3 are small and should go
in the same pass.
