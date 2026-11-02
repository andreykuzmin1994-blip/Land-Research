# 01 — Is the root metric still 0? Baseline diagnosis

> Item 1 of `00_plan.md`. Between runs, debug/diagnose session. No code was
> changed for this item: nothing was found that stops data reaching scoring.
> Everything below ran in a throwaway clone on local PostGIS 16/3.4, with
> live Fulton discovery and TEST CoStar data only.

## How the baseline was run

1. A throwaway clone of `claude/tender-mayer-1rd4y4` (34c4f99, = main
   97b33f5 + the plan), with no remote, a local `.env` and a fresh database.
   `python prepare.py` applied the schema.
2. One standalone discovery cycle loaded 1,075 Fulton parcels
   (`south_fulton_campbellton` 495, `west_atlanta_i20` 580). That is the
   same count as the 2026-10-28 run.
3. A synthetic workbook in CoStar's layout was built from repo test data:
   - **Stats:** the stats values of `tests/fixtures/costar/submarket_stats_happy.csv`
     (South Fulton, West Atlanta / I-20, Clayton County).
   - **Comps:** a sheet whose comp parcel numbers are 8 randomly chosen
     *discovered* Fulton parcels per corridor. These are public county ids,
     not CoStar data.

   `python costar_import.py` wrote the stats CSV and two submarket areas
   (8 of 8 comps located per submarket).
4. Each diagnostic baseline used its own local `autoresearch/<tag>` branch:
   `make baseline`. None was pushed.

## Results

| Run | Test CoStar data | Metric | Strategy gate PASS / FAIL | Composite ≥ 70 | Best PASS composite |
|-----|------------------|--------|---------------------------|----------------|---------------------|
| a | stats + areas | **0** | 970 / 105 | 0 | 57.0 |
| b | a + 8 synthetic land comps (4 per area) | **0** | 970 / 105 | 0 | 62.3 |
| c | b, but under-construction SF ≤ 1.5M (what-if) | **15** | 970 / 105 | 15 | 72.1 |

**Per gate, all runs.**
- **H1/H2:** 0 rejections. Acreage and the Fulton envelope are applied in
  the ArcGIS query.
- **H3–H10:** stubs that pass every parcel and flag it.
- **Control, entitlement and deal-killer gates:** pass all 1,075. No
  `actionability_block` flags exist.
- **Strategy gate:** passes 970. Spec development is MODERATE wherever
  S4 ≥ 6 and S5 ≥ 7.
- **Composite threshold (70):** blocks every parcel in runs a and b.

**Null sub-scores per run** (of 1,075):

| Sub-score | a | b | c | Why |
|-----------|---|---|---|-----|
| S1, S3, S7, S11, S12 | 1,075 | 1,075 | 1,075 | not implemented in `research.py` (43 of 100 weight points) |
| S4, S5, S6 | 105 | 105 | 105 | parcel more than 3 km from any submarket area |
| S8 | 1,075 | 340 | 340 | a: no land comps. b/c: 105 have no submarket, 235 have `assessed_value_total = 0` (265 county-wide), so no basis |
| S2, S9, S10 | 0 | 0 | 0 | S9 is the constant stub 5; S10 is OZ-only (0 or 4) |

## Why the metric is 0, and what moved since 2026-10-28

- **Then:** all 1,075 parcels failed the strategy gate, because no
  CoStar data reached them (corridor names never matched submarket ids).
- **Now:** PR #29's read-time resolution works. 970 parcels (90%) get
  S4/S5/S6 and pass the strategy gate. The binding gate is now the
  **composite threshold**.
- **The ceiling.** The composite is the weighted mean of the sub-scores
  that have a value, and only S2, S4, S5, S6, S8, S9 = 5 and S10 ≤ 4 ever
  have one. Best case, that is (100 + 100 + 100 + 80 + 70 + 35 + 20) / 57
  = **88.6**.
- **With the fixture stats:** vacancy 4–5% gives S4 ≤ 8, and absorption
  0.95–1.85M SF gives S5 = 7. Under construction is 1.8–2.4M SF, so
  S6 = 0. The ceiling is then (100 + 80 + 70 + 0 + 70 + 35 + 20) / 57 =
  **65.8**. No parcel can reach 70.
- **Run c** changes only under-construction SF. S6 rises to 4–7 and
  15 parcels clear 70 (all S2 = 10, S8 = 10, West Atlanta). This shows
  nothing else in the pipeline blocks the metric.
- **So the real baseline depends on the operator's real CoStar numbers.**
  With S4 = 8 and S5 = 7, the best parcel (S2 = 10, S8 = 10, S10 = 4)
  needs S6 ≥ 4, i.e. under construction ≤ 1.5M SF. Without the OZ point
  (S10 = 0) it needs S6 ≥ 7, i.e. under construction < 500K SF.

## Defects looked for and not found

- Submarket resolution: in both runs, 970 of 1,075 parcels resolved. The
  other 105 are farther than 3 km from the test areas; that depends on
  where the comps sit, not on the code.
- The stats CSV loaded into `market_context` under the same ids the areas
  use (`atlanta__south_fulton`, `atlanta__west_atlanta_i_20`).
- The land comps loaded and the S8 median read them (n = 4 per submarket).
- The Fulton parcel layer exposes no sale date or sale price. `sources.json`
  maps none, so S8 always uses the assessed-value proxy. That is a data
  limit, not a defect.

## For the operator or the loop (nothing changed here)

1. **Run the real import before the real baseline.** Whether the metric
   starts at 0 is decided by the real CoStar under-construction and
   vacancy numbers for the submarkets near the two corridors.
2. **S8 needs a `land_sales_comps` export.** The workbook's sale comps
   sheet is used only to draw areas. Turning its land sales into the
   contract's land comps CSV would be a `costar_import.py` change: Tier 2,
   and an operator decision.
3. **Five of twelve sub-scores (43% of the weight) are never computed.**
   S9 is a constant. Filling these in is the loop's job (`research.py`).
4. **The composite averages only non-null sub-scores, so a null beats a
   low score.** Parcels with no CoStar data score up to 61.4 (70.5 with
   the OZ point). In run b, the 28 parcels at 62.3 (S2 = 10, S6 = 0,
   S8 = 10) would move to 72.4 if an experiment returned S6 as null
   instead of 0. The metric would go from 0 to 28 without any new
   information.
   - Nulling a low sub-score is therefore the cheapest way for an
     experiment to raise the metric. The secondary confidence metric only
     breaks ties, so it does not stop this.
   - **Guard today:** the `program.md` "never fabricate" rule and human
     review of the `exp:` diffs.
   - **Structural fix (for the human to decide):** a minimum confidence,
     or counting null weight as 0. Either is a `prepare-mutation` (SR-15).
5. **Staleness.** `costar_import` dates the stats at the last full
   quarter end. Every import is therefore 1–92 days old on arrival, and
   the 30-day freshness check (`verify_setup` `costar_freshness` and the
   R-514 flag) fires for most of each quarter. It is informational only.
6. **Within a run, experiments score only parcels the run has not scored
   yet.** This is `_SQL_LIST_PARCELS_FOR_SCORING_RUN_SCOPED`. A no-op
   experiment scored 0 parcels and kept the baseline's 1,075 rows.
   - So an experiment that changes only scoring logic cannot move the
     metric unless it also re-scores.
   - This is consistent with AUTORESEARCH_MECHANICS.md ("data
     progression"), but it is easy for the loop to miss. See `03_*`.
