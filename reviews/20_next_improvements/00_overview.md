# Next Improvements — Overview and Index

> Session goal (operator): work items 1–3 of `00_plan.md`.
> - Items 4–10 were out of scope. Anything new learned about them is listed
>   below.
> - Work was done between runs, after the full `START_HERE.md` orientation.
> - All live work ran in throwaway clones on local PostGIS 16/3.4, with live
>   Fulton discovery and TEST CoStar data only.
> - No real `autoresearch/*` branch was touched or pushed, and no
>   non-local database was used.

## Commits on this branch

| # | Commit subject | Tier | Note |
|---|----------------|------|------|
| 01 | research: idempotent data_gap flags; baseline diagnosis note | 1 | `01_*`, `02_*` |
| 02 | research: per-parcel error on a failed ad-hoc gap read; Tier 1 note | 1 | `02_*` (reviewer finding 1) |
| 03 | notes: noise floor, proposed keep rule, overview | docs | `03_*`, this file |

## Results

1. **Baseline (`01_*`).**
   - The metric is still 0 on the repo's test stats. Data now reaches
     scoring: 970 of 1,075 parcels pass the strategy gate.
   - The **composite threshold** now blocks every parcel, because:
     - five sub-scores (43% of the weight) are never computed;
     - with the fixture CoStar stats, the composite ceiling is 65.8.
   - With under-construction SF ≤ 1.5M, the metric is 15. So the real
     starting value depends on the operator's real CoStar numbers.
   - No pipeline defect was found, so nothing was fixed for this item.
2. **`data_gap` idempotency (`02_*`).**
   - A parcel's gap flag is written only if no open identical flag exists.
   - `flagged_items` stayed at 16,695 across three no-op experiments and a
     second baseline. With the old code it reached 59,246.
   - The metric, confidence and all 2,150 latest score rows are
     byte-identical between the old and new code. History was not purged.
   - Tier 1 review: approve. Its one functional finding is fixed.
3. **Noise (`03_*`).**
   - 9 baselines over 1 h 42 min with identical `research.py`.
   - Metric 15 and confidence 8.75 every time. 0 of 1,075 parcels
     changed, and the score hashes are identical.
   - A keep rule (re-score the anchor on the same inputs) is **proposed
     only**.

**Suites at the final commit:** root 945 OK, screener 134 OK.

## Needs a human

- **Real CoStar import, then the real baseline.** This decides whether the
  metric starts at 0 (`01_*`).
- **Prepare-mutation decision (SR-15):**
  - A null sub-score raises the composite, so an experiment can lift the
    metric by returning null instead of a low score (`01_*` point 4).
  - Options: a minimum confidence, or counting null weight as 0.
- **Land comps export (Tier 2 `costar_import.py` change).** S8 needs a
  `land_sales_comps` CSV, and the import does not produce one from the
  workbook (`01_*`).
- **Keep rule.** Adopt, defer or reject the paired-control proposal
  (`03_*`). Adopting it is a spec change plus a Tier 2 runner change.
- **Overnight drift.** Run one null baseline a day for a week to measure
  it. This session could only cover intraday.
- **Existing duplicate flags** in any already-running database. Delete them
  or not: an SR-13 decision.
- **`research_log` growth:** about 1,076 rows per experiment, also
  append-only.
- **Pull request.** Whether to open one from this branch.

## New learnings on items 4–10

- **4 (budget):** a full 1,075-parcel baseline took about 20 s locally.
  The 90-minute budget is far from binding until scoring makes live calls.
- **5 (counties):** the Fulton layer returned the same 1,075 parcels with
  identical attributes all afternoon. It also exposes no sale fields and
  no edit dates, so S8 always uses the assessed-value proxy. 265
  county-wide parcels have `assessed_value_total = 0`, which nulls S8 for
  them.
- **New, not on the list:**
  - The CoStar staleness check fires for most of each quarter, because
    imports are dated at quarter-end (`01_*` point 5).
  - Within a run, experiments score only parcels the run has not yet
    scored, so a scoring-logic experiment cannot move the metric unless
    it re-scores (`01_*` point 6). The loop's `program.md` direction may
    want to say so; that is a human-only file.
- **6–10:** nothing new.
