# 03 — Run-to-run noise and a proposed keep rule

> Item 3 of `00_plan.md`. A measurement session, run between runs. **Nothing
> was implemented.** The keep rule below is a **proposal**. Adopting it would
> mean a spec change to `AUTORESEARCH_MECHANICS.md` plus a Tier 2 `runner.py`
> change, and that is the operator's decision.

## How it was measured

- **Where:** the throwaway clone from `01_*`, on local PostGIS 16/3.4.
- **What ran:** `research.py` frozen at 34c4f99 (the script checks
  `git diff --quiet` before every run).
- **CoStar data:** the run c test data from `01_*`, frozen. That is
  under-construction ≤ 1.5M SF, so the metric is non-zero and can move.
- **The series:** 9 baselines, each a fresh `autoresearch/noise-*` branch.
  None was pushed.
  - Two ran back to back.
  - Seven ran every 14 minutes, 14:23–16:06 UTC on 2026-11-02.
- **Fresh data each run:** every baseline re-fetched the Fulton layer live
  (495 + 580 features) and upserted it (`ON CONFLICT DO UPDATE`).
- **Snapshots:** after each run, every parcel's acreage, assessed values,
  land-use code and geometry hash were copied to a side table.

## Result: no measurable intraday noise

| Measure | Spread across 9 baselines |
|---------|---------------------------|
| Metric (`actionable_pipeline_count`) | 15 every run (spread **0**) |
| Confidence-weighted pipeline | 8.75 every run (spread **0**) |
| Parcels discovered | 1,075 every run |
| Parcel attributes changed vs run 1 | **0** of 1,075 in every run |
| Latest score per parcel (composite, confidence, actionability) | byte-identical hash in all 9 runs |

**Within a day, scoring is deterministic.** With identical code and
identical inputs, the pipeline returns identical numbers. No randomness,
row ordering or wall-clock effect reached the metric.

**Item 2 runs agree.** Its old-code and new-code clones also gave 15 / 8.75
at every step.

## What this does *not* cover

The noise floor is zero only for what could be measured this session. It
missed three real sources of drift:

1. **Overnight county edits.**
   - The Fulton layer has no edit-date field, and this session spanned
     less than two hours.
   - Earlier screener evidence (`screener/`) found about **19 parcels** of
     drift overnight, out of roughly 1k.
   - To measure it: one baseline per day for a week, diffed through the
     same snapshot table. That needs a machine that stays up, so it is
     left to the operator.
2. **CoStar re-imports.**
   - These are step changes, not noise: a new quarter moves S4/S5/S6 for
     every parcel in a submarket.
   - `01_*` shows that under-construction SF alone can move the metric
     from 0 to 15.
3. **Future live APIs.**
   - If the loop implements S3 with 3DEP slope data, earlier evidence
     shows **22%** of requests returning a different value from one day
     to the next.
   - That would bring real noise into any experiment that scores with it.

## Where noise could enter a keep-or-revert decision

- **Experiments are compared within a run.** The metric counts each
  parcel's latest row in the run.
- **Experiments only score parcels the run has not scored yet** (`01_*`
  point 6). So drift reaches a decision in only two ways:
  - **(a)** new parcels discovered by the experiment;
  - **(b)** an experiment that deliberately re-scores parcels, which then
    compares today's inputs against the anchor's older inputs.
- **The metric is coarse near the threshold.** In this data, all 15
  actionable parcels sit at exactly 72.11. The next PASS parcels are at
  68.2 (20 parcels, S8 null), 67.89 (13) and 66.84 (15). No PASS parcel is
  in [69, 71].
  - So a small input wobble rarely flips a parcel across 70.
  - When something does, it tends to move a block of parcels with the
    same inputs together.
  - 12 parcels have an S8 price ratio within 5% of a band edge (none near
    actionable). 100 parcels are within 0.25 ac of an acreage threshold.
- **The tie-break is the most sensitive path.**
  - A parcel's confidence is populated sub-scores / 12. One extra non-null
    sub-score on one actionable parcel adds 1/12 = 0.083 to the
    confidence-weighted pipeline.
  - That survives the TSV's 2-decimal rounding, and the strict `>`
    (R-714) keeps it.
  - So on a tied metric, a single parcel's data arriving (or a single
    sub-score being added) decides the experiment.

## Proposed keep rule (operator's decision, not implemented)

**Recommendation: a paired control. Keep the current rule unchanged, but
compare against the anchor re-measured on the same inputs.**

1. **Re-score the anchor on the same snapshot.**
   - Before evaluating experiment *k*, re-score the kept `research.py` on
     the inputs the experiment will see. These are the same parcels and
     the same `market_context`, re-fetched in the same cycle.
   - Record the result as `anchor_metric_now`.
2. **Decide against that.** Keep if `metric_k > anchor_metric_now`, or if
   they are equal and `confidence_k > anchor_conf_now + 0.10`. The 0.10
   margin is a little more than one sub-score on one parcel.
3. **Log both numbers** in the TSV, so a human can see when the anchor
   itself moved. A move there is drift, not an experiment effect.

**Why this rule.**
- It cancels county drift, CoStar refreshes and future 3DEP wobble
  exactly, without first measuring their size.
- It costs one extra scoring pass per experiment. The pass is cheap today
  (the whole 1,075-parcel baseline took about 20 s), but grows with S3/S11 lookups.

**Cheaper fallback** if the extra pass is not wanted:
- Keep the current comparison.
- Re-baseline the anchor (a null experiment) once a day or after each
  CoStar import.
- Require `metric_k ≥ anchor + max(1, observed day-to-day spread)`.
- The daily null runs measure that spread over time, starting from the
  0 found here.

**What adopting either rule needs.**
- The text in `AUTORESEARCH_MECHANICS.md` (keep-or-revert) and
  `runner.apply_keep_or_revert_decision` / evaluate both change. That is a
  Tier 2 change: a risk review before, an adversarial review after, each
  in a fresh context.
- Until the loop calls a live API in scoring, or runs across a CoStar
  import, the current rule loses nothing to noise. The measured intraday
  floor is 0.

## Recommendation on timing

**Not urgent.** For the first real run, the current rule is safe while
these hold:

- the run stays within one CoStar quarter;
- experiments do not re-score;
- the loop does not call a live API in scoring.

**Revisit before whichever comes first:**
- an S3/3DEP experiment;
- a run long enough to span county edits;
- automated CoStar imports.
