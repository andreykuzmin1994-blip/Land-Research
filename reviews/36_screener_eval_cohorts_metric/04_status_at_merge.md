# 36 — Status when this was merged (2026-11-10)

The operator asked to merge and carry on in a new session before two
checks had finished. This note says exactly what is and is not done.
Counts by year and split only.

## Done

- `build_eval.py`, `evaluate.py` and `data/` rebuilt (commit `a6bfa6c`
  and the one after it): as-of years 2015-2019; a sale counts only if
  dated before the first digest that shows the building; each distinct
  site sums to weight 1; sealed test cells frozen in
  `data/test_blocks.json`; graded score; allow-list of fields; scores
  shown to one decimal.
- Pre-change risk review (`02`): verdict "do not build as written". Its
  two blockers and its must-handle items were designed in; whether each
  was handled well is exactly what the unfinished review below checks.
- The key as built:

  | As-of year | Pool | Positives (dev / test) | Left out: built, not sold | Left out: sold after the building |
  |---|---|---|---|---|
  | 2015 | 3,175 | 30 / 10 | 57 | 4 |
  | 2016 | 3,173 | 29 / 10 | 49 | 7 |
  | 2017 | 3,123 | 29 / 11 | 43 | 1 |
  | 2018 | 3,058 | 19 / 7 | 39 | 2 |
  | 2019 | 3,021 | 12 / 7 | 39 | 1 |

  49 distinct sites: 38 dev, 11 test (22% test). No record was left out
  for the missing 2022 digest, and none dropped for sharing ground with a
  test parcel.
- The 2016 year against the old key: the same 3,173 records with the same
  split, block and features; the only difference is the 7 old positives
  now left out as sold after the building.
- Snapshot date check (deeds in the quarter after 1 January where the
  digest already shows the buyer): 19 of 43, 10 of 40, 10 of 36, 9 of 34,
  13 of 33 for 2015 to 2019. The digests are from somewhat after 1
  January. Not corrected; stated in `PROGRAM.md`.
- Dev baseline of the current `rank.py`: graded 12.2 of 38, hits 1.9.
  Random ranking: graded 3.4 (2.1 to 4.8), hits 0.9.
- 511 tests pass.

## NOT done

1. **Adversarial post-change review.** Commissioned on `a6bfa6c`; it had
   not reported when this was merged. Its file would be
   `05_adversarial_review.md`. If it is not in the repository, the review
   must be run again from a fresh context.
2. **Keep thresholds on the built key.** `MIN_GAIN = 2.0` and
   `WIN_SHARE_TO_KEEP = 0.95` come from the pre-change simulation (`03`),
   which used a wider credit curve (zero at 50%, now zero at 20%) and the
   key before the sale-before-building rule. They have not been confirmed
   with `--null-test` on the built key. A calibration run was in progress
   at merge. Target: a change that is not an improvement is kept at most
   about 2% of the time.
3. **The risk review's suggestion to leave out a site's sale-year view**
   (risk 11) was not taken up or measured.

## The stop

Because of 1 and 2, `evaluate.py` carries `REVIEW_PENDING`: `--decide`
refuses, with or without `--new-baseline`, and `PROGRAM.md` opens with a
notice. Scoring without `--decide`, `--control` and `--null-test` work.

To lift it, in one reviewed change: record the adversarial review's
outcome and fixes, record the `--null-test` result and the final
thresholds, set `REVIEW_PENDING = ""`, remove the notice from
`PROGRAM.md`. Then a human records the baseline where the loop will run
(`--decide --new-baseline`).

## Who may do what next

- The session that made this change read the answer data. It, and any
  session that opens `data/` or this folder, must not write `exp:`
  commits to `rank.py`.
- Run the loop from a fresh session that reads only `PROGRAM.md`.
