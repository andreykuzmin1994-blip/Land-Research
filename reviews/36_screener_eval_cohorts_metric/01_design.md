# 36 — Screener evaluation: more as-of years and a graded score (design, before the change)

## Authority

Operator, in chat, 2026-11-10: "Do 1 and 2, you can edit the files", in
answer to a proposal to (1) build the answer key from several Fulton
snapshot years instead of 2016 alone and (2) score where each known site
falls in the ranking instead of only whether it is in the top 2%.

This delegates edits to `screener/autoresearch/build_eval.py`,
`evaluate.py` and `data/` for these two changes only. Those files are
otherwise human-only (CLAUDE.md scope note, `PROGRAM.md`). It is not a
precedent (compare `reviews/33_hazards_enabled`). Treated as the
screener's equivalent of Tier 2: it changes what the score means.

Out of scope: `screens.py`, `scoring.py`, `rank.py`, `settings.toml`, the
positive definition itself, other counties, and the 49 sites left out of
the pool.

The session making this change reads the evaluation data. It must not
then author `exp:` commits to `rank.py`; the loop is run from a fresh
session that never opens `data/`.

## Why

- The dev split has 34 positives. A change is kept only if it adds a
  whole hit to the top 2% and wins 90% of clustered resamples. Each site
  is a yes or a no, so most real improvements are invisible.
- Fulton publishes yearly digest snapshots with one schema for 2015 to
  2021 and 2023 to 2026 (field lists and counts fetched 2026-11-10). The
  key uses 2016 only.

## Change 1: cohorts

- As-of years 2015, 2016, 2017, 2018 and 2019, each built exactly as 2016
  is today: that year's digest for the pool and features, deed history
  before 1 January of that year, the same slow layers. The label year
  stays 2025 for all. 2020 and later are left out: under five years to
  sell and build is too short.
- A positive in year Y: the year-Y owner sold on or after 1 January Y at
  a real price, and the parcel is at least half covered by a 2025 parcel
  with $1M+ of industrial improvements. The existing rule, per year.
- One file pair per year in `data/` (`fulton_<year>_features.json.gz`,
  `fulton_<year>_labels.json`).
- **The same site appears in several years.** A parcel sold in 2021 is a
  positive in all five. To keep the units honest:
  - each record carries a hidden `site` (a hash of the parcel id, no
    year) and a hidden `cohort`;
  - each positive record has weight 1 / (number of years in which that
    site is a positive), so every distinct site sums to 1;
  - the bootstrap already resamples whole map cells, and a cell's block
    id is the same in every year, so a site's years are always drawn
    together.
- **Sealed test split.** A cell is test in every year or in none. The
  cells that are test today stay test (their block ids are read from the
  current data file and frozen as a constant in `build_eval.py`); cells
  that appear only in other years go to dev. If that leaves the test
  share of sites far from a quarter, the note after the build says so.
- Rankings are made within each year (standing in January of that year)
  and results are summed over years.

Expected gain, stated before measuring: modest in distinct sites (sales
during 2015; parcels that only qualify in a later year), larger in
steadiness (each site is ranked in up to five years). The build log will
give site-years and distinct sites; the note after the build reports
both.

## Change 2: graded score

- Each positive earns credit by its percentile in its year's ranking:
  1 in the top 2%, falling on a log scale to 0.5 at 10% and 0 at 50%:
  `credit = clamp(1 - ln(pct / 2) / ln(25), 0, 1)`. A positive the frozen
  screens reject earns 0.
- `graded` = sum over positives of weight x credit, in units of distinct
  sites. `hits` (weighted top-2% hits) is still computed and reported.
- Keep rule: `graded` at least 0.5 above the best kept run, and the new
  ranking's `graded` beats the best kept run's in at least 90% of
  clustered bootstrap resamples (whole cells, all years together, both
  rankings on the same draw). The "+1 hit" requirement goes.
- K stays 2% of each year's pool. Score range, purity, git and
  fingerprint guards are unchanged. The fingerprint covers every data
  file. `experiments.tsv` gains `graded` and `sites` columns; rows from
  the old key never compare (different fingerprint).
- Old scores are not comparable with new ones. A human records a new
  baseline with `--decide --new-baseline` where the loop is run.

## Risks I already see

1. **Pseudo-replication.** Five views of one site are not five sites.
   Mitigation: site weights and the cell bootstrap, above.
2. **Leak across the sealed split** if test cells move. Mitigation:
   frozen test block ids.
3. **Time leak.** A later cohort's features must not see the outcome:
   by 2019 a site sold in 2017 may already show a new owner or grading.
   The existing rules handle this per year (the pool drops parcels
   already built on, developer-named owners, and "built but not sold by
   the as-of owner"); to be checked on the built data.
4. **A continuous score is easier to overfit** than whole hits: many
   small kept changes can ride noise. Mitigation: the 0.5 minimum, the
   90% paired bootstrap, and the sealed test split, which a human checks
   as before.
5. **Shorter windows** in later years lower their positive rate.
   Rankings are within-year, so years are never compared with each other.
6. **`rank.py` could learn the year** from features (for example
   `land_value` drifting up). Within-year ranking makes a year signal
   worthless for ordering.
7. **Label source drift**: `Tax_Parcels_2025` content may have changed
   since the key was last built (2026-10-29), so the 2016 cohort may not
   reproduce exactly. The note after the build compares.
