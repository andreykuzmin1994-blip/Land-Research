# 35 — Screener lead layer, batch A: code writer note

Operator request (2026-11-10, in chat): build the seven improvements
proposed after the first full cloud run (`screener/AGENTS_DESIGN.md`).
This note covers batch A, the parts that are plain code in `screener/`.
Tier 1 (STANDING_RISKS.md): screener human-side edits, no evaluator, no
metric, no credentials, no new external service. The eval files are
untouched: `screens.py`, `scoring.py`, `rank.py`, `autoresearch/`.
`settings.toml` only gains keys.

## What changed

1. **Lead grade** (`grade.py`, new). A fixed rule over the report facts
   already on a ranked parcel: A / B / C / blocked / `not graded: <why>`,
   with `lead_found` and `lead_not_checked` saying what decided it. Three
   new `ranked.csv` columns, a block at the top of each brief, a
   `summary.md` note. Attached after the sort and after every report
   check (`run.main`), like F6 to F10. It changes no score and no order.
2. **Built next door** (`owner_clusters.built_next_door`, `built_line`).
   A flag line and a grade watch item when a same-owner parcel adjacent
   to the ranked one was screened out as already built on.
3. **`leads.md`** (`digest.py`, new). A leads then B leads, in rank
   order, at most `[grade] digest_max`; counts for the rest; the caveats
   once. Listed leads get a brief even outside the top `briefs`.
4. **Report facts** (`facts.py`, new; `decide.py`). `facts.json` per run:
   the allow-listed report columns per ranked parcel, no owner name,
   mailing, site address, flags, geometry or decision. `decide.py` copies
   a parcel's record into `inputs/decision_facts.jsonl`, a separate
   store from `decision_features.jsonl`. A run without facts still
   records the decision.
5. **Kept slope answers** (`arcgis.request_json`, `checks.py`, `run.py`).
   Slope answers go to `cache/slope/` with their own lifetime (`[run]
   slope_cache_days`, 3650). `--refresh` leaves them; `--refresh-slope`
   asks again. The cache key (file name) is unchanged; only the folder
   moved.
6. **Stale briefs** (`report.write_all`). A re-run into the same folder
   removes brief and plan files the run did not write (names matching
   `NNN_*.md|svg` only).

## Why these do not reach the score or the evaluation

- Every new parcel key carries the `lead_` stem and is put on the parcel
  only after `scoring.score`, `screens.flags` and the sort.
  `tests/test_grade.py` spies on `rank.learned_score`,
  `scoring.fixed_score`, `screens.site_rejects` and `screens.flags` with
  slope on and off, and checks that turning the grade off leaves every
  other column, the order and the request list identical.
- `facts.json` is a different file from `features.json`, written after
  the reports. A static test checks that nothing in `autoresearch/`
  imports `grade`, `digest` or `facts` or names the facts files.
- The kept slope answers feed the live steep-ground screen exactly as
  before; the evaluation has no slope (`BANNED_AS_NONE`). The request and
  the key are unchanged (`test_normal_parcel_request_and_cache_key_unchanged`
  still passes unedited).

## Existing tests edited

Ten tests cleared the shared cache with `shutil.rmtree(self.tmp / "http")`
to get a cold run. They now call `Offline.clear_cache()`, which clears
the slope folder too. `Offline.cached()` reads both folders, so "bad
answers are never cached" still covers slope. `Offline.setUp` restores
`checks.slope_cache_days`. No assertion was weakened.

## Measured on real data (Fulton, 2026-11-10, cloud)

- Cold run: 55 min (slope about 49). Warm run with kept slope answers:
  1 min 22 s; same 1,581 ranked parcels.
- Top 250 graded: A 14, B 97, C 139, blocked 0. 1,331 not graded
  (frontage is capped at the top 250).
- Before the grade, 137 of the top 250 had no envelope found. Checked by
  hand on the largest: the misses are narrow or irregular lots against a
  smallest prototype that needs a clear 400 x 445 ft, not a search fault.

## Known limits (also in the README)

- "Built next door" sees only same-owner parcels the run fetched (5-50
  acres, exact name and address).
- The recent-purchase item treats any priced deed as a purchase.
- Soils that are unrated or unchecked are not counted either way.
- The grade thresholds are defaults the operator has not yet confirmed.

## Not in this batch

The shared enrichment contract (one registry for the report tools), the
investigation agents, and more counties.

Suite: 446 tests, OK, offline.
