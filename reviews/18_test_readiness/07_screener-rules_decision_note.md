# Decision Note — Screener owner/zoning rules + AutoResearch evaluator guards

> Tier 2 per `STANDING_RISKS.md` § "Change tiers": `screener/autoresearch/evaluate.py`
> is the screener's metric and keep/discard logic (SR-15 / SR-16 analog), and
> `owners.same_owner` feeds `years_held` and the answer-key labels. Three roles,
> each in an independent fresh context on the session's strongest available
> model: risk review before the change (`07_screener-rules_risk_review.md`),
> implementation, and adversarial review + commit (this note). Between-runs
> session, no autoresearch run active. Base `29c051e`. Screener-only change.

## What changed

1. **`owners.py`.** New owner type `association`. It covers homeowners', property owners',
   community, neighborhood, condominium, civic and townhome associations. An owners' word must
   sit right before ASSOCIATION/ASSN/ASSOC, or HOA must appear inside a company name. POA alone,
   bare ASSOC and bare ASSOCIATION never count. Surname-like developer names (SCANNELL, DERMODY,
   BREIT, EXETER, ...) now need company context. Federal agencies are typed `government`.
   ADMINISTRATION no longer means estate. `same_owner` is rewritten to a written five-part rule
   (see its docstring): companies that only share words are different owners.
2. **`screens.py` / `settings.toml`.** Association owners get an attribute reject with a clear
   reason. The switch `exclude_association_owned` defaults to true and is read with `s.get`, so
   the existing settings dicts and the frozen meta screens work unchanged.
3. **`zoning.py`.** PARK and NEIGHBORHOOD are matched as words in the FLU labels. Office,
   industrial, business and mobile-home parks, Parkway and Parking are no longer conservation.
   Neighborhood Commercial and Neighborhood Mixed Use are no longer residential.
4. **`scoring.py`.** A NaN, infinite or non-numeric learned score counts as 0 instead of 100,
   and the brief gets a note saying so.
5. **`evaluate.py`.** New guards:
   - A static purity check of `rank.py` runs in `main()` before `rank.py` is imported, in every
     mode except `--control`.
   - Non-finite scores are counted per score and refused.
   - The touched-file check covers the whole commit.
   - Untracked files under `screener/` are refused.
   - A git failure refuses (fails closed).
   - A missing, corrupt or mismatched `best_ranking.json` is refused, and so are orphaned or
     deleted log rows.
   - `--new-baseline` starts a new chain (R1) and is allowed on a commit that was already scored.
   - `LOG_COLUMNS` and `EVAL_FILES` are unchanged.
6. **`tests/test_screener.py`.** `unittest.main()` moved to the end of the file, so the module
   run now executes 23 tests instead of 18. `tests/test_rules.py` is new (34 tests). README
   updated.

## Verified (re-run by the reviewer)

- Root suite: 636 OK. Screener suite: 57 OK (23 + 34). `python -m screener.tests.test_screener`
  runs 23 tests. The new tests run against base code fail 32 of 34; the 2 that pass are guard
  tables that must keep passing. The rows the reviewer added fail against the unreviewed patch.
- **SR-6.** All tests are offline. DecideTests patch the log, best file, fingerprint and git, and
  their tearDown asserts that the real `experiments.tsv` and `best_ranking.json` were not touched.
  None of the tests or probes ran `--decide`, `--new-baseline`, `--test` or `build_eval`.
- Plain `evaluate`: pool 2318, positives 39, k 46, hits 2, `score_range` [6.5, 85.0],
  `nonfinite_scores` 0. `--control`: mean 0.86, p5–p95 [0, 3]. Both unchanged.
- **R2.** Importing `evaluate` does not import `screener.rank` (checked in `sys.modules`), so the
  purity check runs before any `rank.py` code.
- Offline probes on `screener/cache/` (counts only, SR-8):
  - **owner_type.** 472 of 7,890 parcels change, all to `association` (459 from entity, 13 from
    individual). The reviewer read every changed form; all are residential associations. 471 of
    the 3,805 parcels that passed the attribute screens (12.4%) are now rejected. The
    developer-surname and agency rules change no cached parcel.
  - **FLU.** Of 70 distinct labels (89,777 rows), only 'Neighborhood Commercial' (7 rows) changes,
    from residential to other. The test table covers exactly these 70 labels.
  - **`same_owner`.** Across 24,381 deed/owner pairs, 943 pairs newly stop matching and 11 newly
    match. `acquisition()` changes for 320 of 6,398 owner/parcel pairs (5.0%): `years_held` goes
    down for 223 and up for 9, and 101 move by 10 years or more. `sold_after(2016)` flips for 180
    pairs (179 from True to False). In the reviewer's hand-checked samples, most of the dropped
    matches were distinct SPEs, distinct funds or developer→HOA deeds. A few were misses
    (CTR/CENTER, "CITY OF X" vs "CITY OF X GEORGIA", a typo plus an extra word).
- **SR-10.** Standard library only. **SR-12.** No secret-shaped literals. No model names.
  - Only allowed files changed. `rank.py`, `build_eval.py`, `data/`, `PROGRAM.md` and the
    root immutable files are untouched.
- **Python 3.11.** Every changed file parses with `feature_version=(3, 11)` and compiles. A
  runtime smoke test passed for evaluate, owners, zoning and scoring.

## Risks found in review, and what changed as a result (reviewer fixups)

1. **The purity check could be bypassed to read the answer key (material).** A `rank.py` that
   passed `rank_problems` could walk `gen.gi_frame.f_back...f_locals` into `ranking()` and
   `evaluate()` and score every positive. On a synthetic pool this gave 3/3 hits, against 0 for
   an honest scorer. The same was possible through `str.format` attribute fields, `match`
   class-keyword patterns (`case object(__globals__=g)`) and private typing helpers
   (`from typing import _eval_type`).
   - **Fix.** Frame attributes, `format` and `format_map` are banned. Private from-imports are
     refused. `MatchClass` keyword attributes get the same attribute rule.
   - Tests added; README and docstring updated.
2. **`same_owner` rule 5 (near spelling) overrode rule 4.** Long names with clashing initials or
   JR vs SR matched ("DOE JOHNATHAN R" / "W", "ACME PARTNERS A LLC" / "B LLC").
   - **Fix.** A near spelling must keep the same initials, generation marks and numbers.
   - This changes one cached deed pair. Tests added.
3. **SR-8.** One test name that matched a real person's name pattern was replaced with a
   synthetic one. The README was re-wrapped.

## Deviations

- R5 asked for `same_owner` to ship in its own commit; the orchestrator asked for a single commit.
  It is a single commit. The `same_owner` hunks can be reverted by hand: in `owners.py`, the
  `Counter` import and everything from `DEED_NAME_CAP` to the end of the file, plus
  `SameOwnerTests`.
- `STANDING_RISKS.md` asks the note to name the model that ran each role, but this session
  forbids model names, so the roles are described as above instead.

## Residual gaps / human follow-ups

1. **Rebuild the frozen key.**
   - The dev score is unchanged only because `owner_type`, `years_held` and the labels are frozen
     in `data/`. The live tool and the key now diverge materially:
     - about 12% of the attribute-screen pool is association land that the key still holds as
       negatives;
     - the old `same_owner` is baked into `years_held` and the `sold_after` labels;
     - VA-type owners are still typed estate.
   - **Recommendation:** after merge, a human runs `python -m screener.autoresearch.build_eval`,
     then `python -m screener.autoresearch.evaluate --decide --new-baseline`, and updates the
     baseline line in PROGRAM.md and README.
   - Until then `--decide` refuses because EVAL_FILES changed. That is expected.
2. **PROGRAM.md edits for the human** (not made here):
   - (a) Under "What rank.py can use", list the allowed imports (`__future__`, `math`, `typing`)
     and the refused constructs: other imports, private names, open/eval/exec/compile/getattr/
     globals/vars, `str.format`, frame attributes, dunders, global/nonlocal, and strings naming
     data files. Say that the check runs before `rank.py` is imported, in every mode.
   - (b) Rule 1: the whole commit may touch only `screener/rank.py`, and untracked files under
     `screener/` are refused.
   - (c) Add a REFUSED branch to the loop:
     - commit, untracked-file or purity refusals: fix and retry;
     - NaN, infinite or out-of-range scores: fix `rank.py`;
     - "evaluation changed" or a best-run record that is missing or mismatched: stop and ask a
       human. The loop never runs `--new-baseline`.
   - (d) Recovery from a lost `best_ranking.json`: a human re-records the best kept commit with
     `--decide --new-baseline`.
   - (e) Scores must be finite as well as within 0–100.
   - (f) The pool, positive and baseline numbers change after the rebuild.
3. **Accepted limits.**
   - The purity check is a drift guard, not a sandbox (SR-16 analog). A stale `__pycache__` .pyc
     or rebinding at runtime still gets through.
   - Deleting both `experiments.tsv` and `best_ranking.json` still re-baselines silently.
   - `--new-baseline` is not authenticated, and it now really resets the bar. A `baseline` row in
     the middle of a run in `experiments.tsv` is a red flag to check.
4. **Owner-rule edges not seen in the Fulton cache.**
   - `<surname> HOA LLC` (HOA as a given name) reads as an association.
   - `<builder> HOMES ASSOC L P` (ASSOC meaning associates) reads as an association.
   - `<developer surname> <first name> CO-TRUSTEE` reads as developer, because CO matches a
     company word.
   - Generic OWNERS and MASTER associations, misspellings, and HOA without a company word are not
     matched. Whether to add them is a human decision.
   - `same_owner` has no abbreviation map, and it strips EXEC* and ADMINISTRAT* broadly.
5. **Other tracks and adjacent issues.**
   - The `run.py` funnel label should mention associations (owned by the other track).
   - The screener suite is not in CI: it needs shapely, an SR-10 decision for the human.
   - The FLU label 'Manufactured Housing' would read as industrial. This predates the change and
     the label is not in the current sources.
