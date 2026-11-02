# 05 — Land comps import: decision note

> **Tier 2.** The pre-change risks are in `05_land-comps-import_risk_review.md`.
> This note records the independent **adversarial** post-change review (a
> fresh context that did not write the code; it probed a live local PostGIS
> and ran the real export, reporting counts only) and what changed after
> it. Model names are omitted, as in `reviews/18_test_readiness/`.

## Change

`costar_import.py` now also reads a CoStar **Land** sales sheet. It writes
S8 land comps:
- **Kept:** sold sales of 5–50 acres on **industrial zoning**.
- **Assigned by area:** each sale goes to the submarket area that a parcel
  at the same point resolves to.
- **Written in one transaction**, straight to `sales_comps`. The write
  replaces the tool's earlier land rows in the market.

It also fixes the latent defect in which a Land sheet was taken as the
area-comps sheet, and the resulting empty plan cleared every tool-written
area.

## Review verdict: approve-with-fixes (no blocking finding)

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | should-fix | When no sale got an area, the import still deleted the earlier tool comps. | If nothing would be inserted, nothing is deleted; the output says the earlier comps were kept. Tested. |
| 2 | should-fix | An out-of-range price overflowed BIGINT. The snapshot was already saved, so every later areas import failed with exit 2 ("database not reachable") and Start stopped every time. | Implausible prices are dropped (over $10B, or over $20M per acre). The snapshot is saved only after the land write succeeds or is deferred. Tested. |
| 3 | should-fix | A corrupt snapshot raised after the areas had committed, so every Start redid the areas and failed. | The snapshot is read before any write. If it is unreadable, a warning is printed and the areas import alone (exit 0). Tested. |
| 4 | should-fix | Classifier false positives: `O&I` / `O/I`, `PUD Phase I`, `Non-Industrial`, `rezoning to industrial`. | `O&I`-style codes are one token. A bare `I` or `M` counts only as the first token. Proposed, pending, rezoning or negated text never counts. Table-driven tests. |
| 5 | should-fix | A sale also delivered by a hand `land_sales_comps` CSV would be counted twice in the median. | Such sales (same address and date in the market) are not inserted; the output counts them. Tested. |
| 6 | nit | The duplicate key (address, date) merged different tracts on a road with no street number. | The key now includes acres (to 0.1 ac). Tested. |
| 7 | nit | Missing codes `IG`, `IP`. | Added. `ML` and `MI` are left out: they are ambiguous across Georgia jurisdictions. |
| 8 | nit | A data error exited 2, which the autopilot reports as "database not reachable". | `psycopg.DataError` now returns exit 1 ("REFUSED"). Tested. |
| 9 | nit | A mixed workbook whose Land sheet qualifies nothing refuses the whole workbook. | Accepted: CoStar Land exports are single-sheet. |
| 10 | nit | `prepare.get_connection` logs raw psycopg messages, which could echo a cell value. | Residual SR-8 risk outside this diff. Values are validated before any insert. |

**Verified by the reviewer:**
- R-1: building and area sheets are never treated as Land.
- R-14: a Land sheet leaves areas intact.
- The delete is scoped to the tool's rows in the market. A hand-inserted
  CSV land row survived every scenario.
- R-3: deferral and re-assignment work.
- R-4: dates, R-5: status and portfolio filtering, R-12: refusal before
  any land work, R-13: counts only, R-15: `--no-areas`.
- The assignment SQL matches research's resolver clause for clause, and
  the `ST_Expand(0.05)` prefilter never excludes an area within 3 km at
  Georgia latitudes.
- SR-15/SR-2: the importer imports neither `research` nor `runner`.

## Evidence

- **Offline:** `tests/test_costar_import.py` has 51 tests (24 new, covering
  every finding above). Root suite: 969 OK. Screener: 134 OK.
- **Live (local PostGIS 16/3.4):** `tests/live_submarket_check.py` part
  (c) puts land sales at the parcels' test points. They are assigned to
  the same areas as the parcels (the 5 km one to none), and a second
  import replaces the first rather than adding to it.
- **Real export** (counts only): 483 rows → **44 kept**. Dropped:
  - 203 not industrially zoned;
  - 105 outside 5–50 ac;
  - 68 not sold;
  - 48 without a price or acres;
  - 8 not land;
  - 7 portfolio pieces.

  Re-importing gives the same rows, with no duplicates. A baseline then ran
  cleanly.

## Behaviour a human will notice

- **Coverage depends on the real areas.** Against the test database's two
  small synthetic areas, only 2 of the 44 sales fell within 3 km, so S8
  stayed null (it needs 3 comps). Real areas come from the stats
  workbook's comps and should cover more ground. The import prints the
  "farther than 3 km from every area" count, and that number is worth
  checking after the first real import.
- **Assignment is frozen until the next import.** If the areas change,
  any workbook with sale comps re-assigns the saved sales.
- **Hand-delivered `land_sales_comps_*.csv` rows are never changed** by
  this tool.
