# Test Readiness — Overview and Index

> Session goal (operator): "what do we need to test the land research
> tool" → "proceed": fix what testing found, re-run the live tests, and
> run the main system end to end in a throwaway copy. Between runs; the
> full `START_HERE.md` orientation was completed before any change.
> Every change went through the tier `STANDING_RISKS.md` requires, each
> role in an independent fresh context. The notes in this folder were
> written in parallel, so where they disagree this overview is the
> reconciled view.

## Commits on this branch

| # | Commit subject | Tier | Note |
|---|----------------|------|------|
| 01 | test readiness — gitignore TSVs, screener suite in CI, branch-proof smoke test | 0 | `01_*` |
| 02 | runner-ratchet — decision precision, setup gate, enforced budget | 2 | `02_*` |
| 03 | transaction boundaries — no silent loss of earlier files/parcels | 2 | `03_*` |
| 04 | harness never false-healthy, explicit failure class, CI keeps exit code | 2 | `04_*` |
| 05 | env.template guard — one checker for hook + CI, never prints values | 2 | `05_*` |
| 06 | screener: stop on malformed/partial map answers, slope failures, safe output | screener | `06_*` |
| 07 | screener: owner/zoning rule fixes + AutoResearch evaluator guards | screener | `07_*` |
| 08 | screener: catch misspelled and generic owners' associations | screener | `08_*` |
| 09 | durable pending purges, run=/exp= markers kept inside the cap, ops tooling | 2 | `09_*` |
| 10 | live txn check on run branches, '@' DSN redaction, memo actionable count | 1 | `10_*` |
| 11 | screener: close purity-check routes, sub-pixel slope parcels, THE not an HOA word | screener | `11_*` |

09–11 fix what a three-lens review of the combined branch (each finding
re-checked by an independent skeptic) and the end-to-end run found after
01–08 were merged.

## What is now tested, and how

| Layer | Command | Where it runs |
|-------|---------|---------------|
| Root offline suite | `python -m unittest discover -s tests` | CI, every push |
| Screener offline suite | `python -m unittest discover -s screener/tests -t .` | CI, every push (new) |
| Live PostGIS smoke + transaction check | `tests/test_postgis_smoke.py`, `tests/live_txn_check.py` | CI service container; locally via the README "Testing" recipe |
| Live connector harness | `python connector_harness.py --county fulton --quick` | CI on harness/registry changes |
| env.template secret guard | `.githooks/check_env_template.py` | pre-commit hook + CI |
| Screener live run + read-only evaluator | `python -m screener.run --refresh`, `python -m screener.autoresearch.evaluate [--control]` | manual |
| Root end to end | throwaway clone + local PostGIS, `make verify` → `make baseline` → `make loop MAX=1` | manual (done once in this session, below) |

## Live results from this session

- **Root end to end** (throwaway clone, local PostGIS 16/3.4, live Fulton
  discovery): verify, baseline, the Step-6 confirmation gate, discard,
  keep, crash, timeout (budget interrupt), halt, loop lock and the TSV
  mirror all behaved as specified. **The baseline metric was 0**: all
  1,075 discovered parcels failed the strategy gate (mean composite 30.8).
  See "Needs a human" item 1.
- **Screener full run** (cold, `--refresh`): base code ranked 1,747 of
  5,367 parcels; this branch ranks 1,583. The drop is mostly 311
  homeowners'/owners' association common areas now screened out (0 left
  in the ranking). No false stops from the stricter map-answer checks;
  map-layer feature counts identical. Dev evaluator score unchanged
  (2 of 39 at k = 46; random control ≈ 0.86).
- **Public data drifts between days**: USGS 3DEP returned different slope
  statistics for ~22% of identical requests on consecutive days, and
  Fulton appraisal values changed overnight, so parcels near thresholds
  flip between runs (`screener/README.md`).

## Needs a human (reconciled across all notes)

1. **The root metric starts at 0.** Parcels get `submarket` = corridor
   name (`research.py` ~1281) while CoStar rows use
   `atlanta__<slug>` ids, so S4/S5/S8 never populate even with exports
   loaded; nothing writes `submarkets.bbox`, so `verify_setup` warns
   unless it is seeded by hand. Decide the corridor → CoStar submarket
   mapping (and where it lives) before a real run; until then the loop
   has nothing to improve.
2. **Supabase CI check**: `validate-phase1` has failed all 25 runs. The
   `DATABASE_URL` secret exists; the last recorded error was an
   `ENOTFOUND` for the Supabase tenant (`reviews/17_tsv_mirror/`), and
   newer logs have expired. Check the project and replace the secret (or
   retire the job).
3. **Start a new run tag after merging** — cycles no longer silently lose
   files/parcels, so metric inputs change for identical `research.py`.
   Never merge main into an in-flight `autoresearch/*` branch.
4. **Screener AutoResearch**: rebuild the frozen key
   (`python -m screener.autoresearch.build_eval`), then
   `python -m screener.autoresearch.evaluate --decide --new-baseline`.
   Until then `--decide` refuses (evaluator and screens changed). Update
   `screener/autoresearch/PROGRAM.md` with the refused constructs and the
   REFUSED branch (proposed text: `07_*` residual 2, `11_*` residual 2).
5. **90-minute budget is in-process, not OS-level** (SIGALRM in the
   runner; server-side queries are not cancelled). Either fund subprocess
   isolation (Tier 2) or amend `AUTORESEARCH_MECHANICS.md` L166/L530.
6. **`make daily`** derives the tag from today's date, so each calendar
   day is a new run that now refuses until `make baseline`. Decide
   whether `TAG` should default to the current run (`CLAUDE.md` L72).
7. **`sources.json`** (human-only): DeKalb's URLs are dead (candidate
   endpoint in `04_*`); Cobb, Gwinnett, Clayton and Henry have none.
8. **prepare-mutation candidates** (between runs only): exclude
   `composite_score = 'NaN'` in `_LATEST_SCORE_FILTER`; fix prepare.py
   docstrings about `run_with_os_timeout` and per-parcel transactions.
9. **Growth**: the purge reverts `parcel_scores` only; the e2e run left
   ~159k `flagged_items` data-gap rows after 13 experiments. Decide on
   retention before long runs on a size-capped database.
10. **Repo settings**: branch protection / required checks on `main`;
    glance at old `check-env-template` logs (they echoed the password
    slot before 05).
11. **Process**: `STANDING_RISKS.md` asks Tier-2 notes to name the model;
    this session's policy forbids model names in the repo. Reconcile.

## Stale statements in the per-track notes (superseded here)

- `02_*` residual 3 / RR-12(iii) (ingestion rolls back): fixed by 03;
  corrected by a dated update in 09.
- `03_*` "exits 2 on an autoresearch/* branch": fixed by 10.
- `06_*` "root suite writes `sources/disco-fulton-*`" and `07_*` "screener
  suite is not in CI": both fixed by 01.
- `10_*` residual 4 (runner docstring/residual still stale): fixed by 09.
