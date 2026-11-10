# 36 — Keep thresholds on the key as built (2026-11-10, finished after the merge)

One of the two checks `04_status_at_merge.md` lists as not done. The
other (the adversarial review, `05`) is still outstanding as this is
written. `REVIEW_PENDING` stays set: this note changes no code.

## Method

Scratch script, using the merged evaluator's own `keep_verdict` on the
dev split of the committed key. Each trial compares two rankings: "best"
is the current `rank.py` scores plus random noise; "new" is the same
scores plus independent noise of the same size, plus a real signal of
strength alpha (alpha 0: neither is better, so a keep is a false keep).
300 trials per cell; 500 cell-resamples per decision, as shipped. Noise
is in score points on the 0-100 scale: it stands for how much an
experiment reshuffles the ranking.

## Result

Share of trials kept. Every rule below also requires 95% of resamples
and hits not lower.

| Gain floor | noise 2: false / small / medium | noise 4: false / small / medium | noise 8: false / small / medium |
|---|---|---|---|
| +2.0 (as merged) | 0.0% / 73% / 91% | 1.0% / 56% / 85% | **5.3%** / 26% / 59% |
| +2.5 | 0.0% / 47% / 88% | 0.3% / 36% / 83% | 3.7% / 23% / 56% |
| +3.0 | 0.0% / 25% / 82% | 0.0% / 22% / 72% | 1.7% / 19% / 50% |
| +3.5 | 0.0% / 10% / 71% | 0.0% / 11% / 56% | 0.7% / 13% / 43% |

"Small" and "medium" are real improvements averaging about +2.5 and +4.0
graded at low noise. Raising the win share to 97.5% instead of the floor
barely lowers false keeps at noise 8 (5.0%) and costs more power.

## Reading

- For experiments that change the ranking modestly (noise 2 to 4), the
  merged rule meets the target of at most about 2% false keeps (0.0% and
  1.0%) and keeps most real improvements.
- For experiments that reshuffle the ranking heavily (noise 8), the merged
  rule keeps 5.3% of non-improvements, above the target. A floor of +3.0
  brings that to 1.7%, at the price of keeping a small real improvement
  only about a quarter of the time.
- The false-keep rate therefore depends on how big a change an experiment
  makes, which the evaluator does not control. Two honest options:
  1. raise `MIN_GAIN` to 3.0 (target met at every noise level tried); or
  2. keep 2.0 and state in `PROGRAM.md` that the bar assumes one focused
     change per experiment, with the sealed test split as the backstop.
- This is a judgement for the operator, informed by the adversarial
  review. Not decided here.

## Limits

- 300 trials: a rate of 1% is 3 trials. The 5.3% is 16 of 300.
- The simulated improvement is spread evenly over the positives; real
  edits will not be.
- Random noise is not how `rank.py` edits behave. A targeted change (a
  rule keyed to a narrow range of one field) is a different risk; the
  adversarial review was asked to test that.
