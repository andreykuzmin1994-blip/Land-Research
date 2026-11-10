# 36 — Measurements before the change (2026-11-10)

Made with scratch scripts on a scratch copy of the build. No evaluation
file was edited. Counts by year and split only; no parcel, owner or
feature detail is recorded here.

## What the extra years add

Built each as-of year exactly as `build_eval.main` builds 2016 today.

| As-of year | Pool | Positives | Built but not sold by that year's owner (left out) |
|---|---|---|---|
| 2015 | 3,179 | 44 | 57 |
| 2016 | 3,180 | 46 | 49 |
| 2017 | 3,124 | 41 | 43 |
| 2018 | 3,060 | 28 | 39 |
| 2019 | 3,022 | 20 | 39 |

- 2016 reproduces today's key exactly (3,180 and 46).
- 179 positive records, **58 distinct sites** (46 today): 16 sites are
  positive in all five years, 14 in one only.
- No site is a positive in one year and a negative in another; in its
  other years it is absent from the pool.
- No parcel changes map cell between years.
- All positives sit in 17 map cells; 12 of the 32 dev cells hold any.
  The extra years add sites, not cells.
- Not yet corrected for risk 2 of `02_risk_review.md` (a developer's
  resale of a finished building counted as a land sale in later years).

## Keep rules compared

Simulation on the dev split. "Old" is a noisy copy of today's ranking;
"new" is another noisy copy, plus a real signal of strength alpha
(alpha 0: neither is better, so a keep is a false keep). 400 trials per
row, 200 cell-resamples per decision. "Graded" is the design's credit
curve with one site summing to 1.

| Key | Rule | False keeps (alpha 0) | Kept, small real gain (alpha 2) | medium (4) | large (8) |
|---|---|---|---|---|---|
| 2016 only | today: +1 hit and 90% | 1.5% | 10% | 24% | 44% |
| 2016 only | graded +0.5 and 90% (as designed) | 13.0% | 57% | 78% | 79% |
| 2016 only | graded +2.0 and 95% | 2.2% | 32% | 60% | 64% |
| 2015-2019 | today: +1 hit and 90% | 3.2% | 38% | 62% | 82% |
| 2015-2019 | graded +0.5 and 90% (as designed) | 12.8% | 87% | 97% | 94% |
| 2015-2019 | graded +1.5, 95%, hits not lower | 3.0% | 64% | 90% | 84% |
| 2015-2019 | graded +2.0 and 95% | 1.2% | 57% | 89% | 84% |

Reading:

- The reviewer's blocker 1 is confirmed: the rule as designed keeps about
  13% of changes that are not improvements.
- Tightened to "graded +2.0 and 95%", the graded rule's false-keep rate is
  at or below today's, and it keeps real improvements far more often.
- Both changes together (five years, graded +2.0 and 95%): false keeps
  1.2% against 1.5% today; a small real improvement is kept 57% of the
  time against 10% today.
- These are simulated improvements spread evenly over the positives. Real
  edits to `rank.py` will not look like that, so the numbers rank the
  rules; they do not predict a keep rate.
- The final thresholds must be set by this test on the key as actually
  built, and the test shipped with the evaluator.
