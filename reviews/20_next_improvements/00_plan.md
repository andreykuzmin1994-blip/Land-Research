# Next Improvements — Plan

> Written after PR #28 (test readiness, `reviews/18_test_readiness/`) and
> PR #29 (CoStar Excel import + submarket areas, Windows autopilot,
> rebuilt screener key, PostGIS-backed `validate-phase1`) were merged.
> Operator decision (2026-11-02): "proceed with your recommendations".
> Items 1–3 are in scope for the next session; items 4–10 stay listed for
> a human decision or a later pass.

## In scope now

### 1. Does the root metric still start at 0?

**Evidence.** In the end-to-end run of 2026-10-28 (throwaway clone,
local PostGIS, live Fulton discovery) the baseline metric was 0. All
1,075 discovered parcels scored `FAIL:strategy` (mean composite 30.8,
17 at 70 or above). The cause then was that parcels carried corridor
names, so vacancy, absorption and land price (S4/S5/S8) never filled
in. PR #29 now resolves each parcel's CoStar submarket at read time from
`submarkets.bbox` areas written by `costar_import.py`. S8 stays empty
until a `land_sales_comps` export exists. Nobody has re-run a baseline
since #29.

**Do.** Re-run a baseline end to end with CoStar areas and stats in place
(test data only — the real workbook is licensed and is not in the repo).
For each gate, report how many parcels pass and which gate blocks them,
and per sub-score how many are null.

**Rule.** Fix only genuine defects that stop data reaching scoring,
between runs and at the tier `STANDING_RISKS.md` requires. Don't tune
gates, thresholds or weights to raise the number. That is the loop's job
(and `parameters.json`/`prepare.py` are human-only). Report what a human
or the loop would need to change.

### 2. `flagged_items` growth on long unattended runs

**Evidence.** The purge reverts `parcel_scores` only. Each experiment
writes about 18 `data_gap` flags per scored parcel, so the e2e run held
159,311 rows after 13 experiments. A week on the autopilot (about 16
experiments a day) is roughly 2 million rows, which is a problem on a
size-capped database.

**Do.** Write a `data_gap` flag only when the same open flag
(parcel + description) doesn't already exist, or an equivalent idempotent
rule. This is a between-runs `research.py` change (Tier 1, one
independent reviewer).

**Prove.** The metric, the gates and the purge don't change. Measure row
counts before and after across repeated experiments. Note the matching
`research_log` growth too, but don't purge history (SR-13).

### 3. Natural run-to-run noise in the metric

**Evidence.** Public data drifts between days. On consecutive days USGS
3DEP gave different slope statistics for 22% of identical requests
(464 of 2,110), and Fulton appraisal values flipped about 19 parcels
between built-on and buildable overnight. So an experiment can be kept
because the data moved, not because the change helped.

**Do.** Measure it. Run two or more baselines back to back with
identical `research.py` against live data, then report the metric and
confidence spread. Propose — don't implement — a keep rule that tolerates
the noise (that's a spec change to `AUTORESEARCH_MECHANICS.md` and a
Tier 2 runner change, so it's the operator's decision).

## Listed for later or a human decision

4. **90-minute budget is in-process (SIGALRM).** A hung C-level call
   or a slow server query can outlast it. Options: a DB
   `statement_timeout`, subprocess isolation per experiment (Tier 2), or
   amend `AUTORESEARCH_MECHANICS.md` L166/L530.
5. **Counties.** Only Fulton is configured. DeKalb's `sources.json`
   URLs are dead (candidate endpoint in
   `reviews/18_test_readiness/04_harness_decision_note.md`). Cobb,
   Gwinnett, Clayton and Henry need endpoint discovery. Human-only file.
6. **Repo settings.** Branch protection and required checks on `main`.
7. **Supply chain.** Dependency ranges with no lockfile, and GitHub
   Actions on the deprecated Node 20 runtime.
8. **Screener polish.**
   - `--limit` trials sample one corner (3 of 10 jurisdictions).
   - 551 ranked parcels have no machine-readable zoning (Chattahoochee
     Hills 415).
   - `why_site` cites exit distance even when access scores 0. That's in
     `rank.py`, which the loop owns.
9. **Coverage gaps** (as of PR #28):
   - `screener/fulton.py` 58% (deed parsing drives years held and seller
     signals);
   - `screener/screens.py` 74%;
   - `cli.py` 72%.
10. **Halt latency.** `make halt` takes effect only between experiments,
    so stopping can take up to 90 minutes.
