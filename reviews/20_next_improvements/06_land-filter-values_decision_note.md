# 06 — Land comps filter: 10–50 acres, last 22 months

> **Tier 1:** config values plus one filter rule in `costar_import.py`.
> Reviewed by one independent reviewer in a fresh context. Model names
> are omitted.

## Operator's decision

Land comps should come only from sales of **10–50 acres** (was 5–50) sold
in the **last 22 months**, counted back from the import date:
- smaller sites are not targets;
- older sales predate the current rate environment.

The change to `COSTAR_INGESTION_CONTRACT.md` Export 2 says the same.

## Change

- `LAND_MIN_AC = 10`.
- New `LAND_MAX_AGE_MONTHS = 22`, with `months_before()` doing
  calendar-month arithmetic, clamped to month end. Drops are counted as
  "outside 10-50 acres" and "older than 22 months".
- Review fix: sales replayed from the saved snapshot, when a later workbook
  redraws the areas, are re-checked against both rules on the replay date.

## Review (fresh context): approve-with-fixes

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | medium | Snapshot rows were replayed without re-filtering. A snapshot saved under the old rule could bring back 5–10 ac sales, and replayed sales never aged out. | Fixed with `refilter_snapshot`, and tested. |
| 2 | low | Comps imported under the old rule stay until the next Land import. | Operator note. |
| 3 | low | Comps age during a run, because the window is counted from the import date. | Operator note. |
| 4 | low | `research.py`'s S8 median still uses its own 36-month lookback on every land row, including hand-delivered CSV rows. `reporting.py` thesis comps use 24 months. | Not changed. `research.py` is the loop's file, and tightening it is a separate reviewed change. |
| 5 | info | Export 3 (listings) and `program.md`'s H2 rule still say 5–50 ac. | Out of scope. `program.md` is human-only. |

**Verified by the reviewer:**
- `months_before()` agrees with a calendar reference on every day from
  2020 to 2031.
- Boundaries are inclusive: 10 ac and a sale exactly 22 months old are
  kept.
- Both suites pass.
- The docs match the code.

## Evidence

The real Land export still yields **44** kept sales. It already starts at
10 ac and covers about 22 months.
