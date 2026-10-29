# 19 — CoStar Excel import and submarket areas: pre-change risk review

> Tier 2 (`STANDING_RISKS.md`): external-service use, reference data the
> scoring reads, and metric inputs. Independent risk enumeration BEFORE the
> change by a fresh-context reviewer that did not design it. As in
> `reviews/18_test_readiness/`, this session's policy forbids model names in
> the repo, so roles are described instead.

## Problem

`parcels.submarket` held the discovery corridor name, while `market_context`
and `sales_comps` key on CoStar submarket ids (`atlanta__<slug>`), so
S4/S5/S6/S8 never populated and the metric started at 0
(`reviews/18_test_readiness/00_overview.md`, "Needs a human" 1). Nothing wrote
`submarkets.bbox`. The operator's CoStar export is an Excel workbook with a
long-format stats sheet and a building sale comps sheet whose parcel numbers
match Fulton's ParcelID once spaces and dashes are removed.

## Design reviewed

An operator tool converts the stats sheet into the contract CSV and writes
`submarkets.bbox` from the located comps; `research.py` reassigns
`parcels.submarket` to the nearest area once per scoring cycle.

## Findings (and what the design changed to)

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | blocking | A stored reassignment survives a discarded experiment (the purge covers `parcel_scores` only; the UPSERT never resets `submarket`), and history-dependent values break SR-9. | Resolved at READ time: one SQL expression (`_SQL_RESOLVED_SUBMARKET`) used by the per-parcel fetch and the cycle prefetch; nothing written to `parcels`. |
| 2 | blocking | A first import mid-run splits the run into parcels scored with and without CoStar data; the jump is credited to the next experiment. | Import only between runs: autopilot imports a workbook after a NEW run's branch is cut and before its baseline; `make costar-import` refuses while a loop holds the lock. |
| 3 | blocking | `2026 Q4 EST` is a forecast and always populated. | Only `YYYY Qn` columns qualify; the latest one is used for every concept. |
| 4 | blocking | `report_date` = conversion date duplicates rows on re-import and lets an old export outrank a new one. | `report_date` and the file name come from the quarter end. |
| 5 | design | Long→wide traps: other property classes, market-total rows, duplicates, fraction vs percent, trailing spaces, clusters. | Filtered, duplicates refused, fraction detection per concept, names stripped, clusters dropped. Approximations documented. |
| 6 | design | Id consistency with the loader. | Ids built only via `costar_ingest._ensure_submarket` / `_slugify`; test pins `'atlanta'`. |
| 7 | design | The tool must not depend on the agent sandbox; must be immutable during a run. | Imports prepare/costar_ingest/pipeline_common only (test); listed with the immutable modules in `CLAUDE.md` and `AUTORESEARCH_MECHANICS.md`. Fulton layer read from `sources.json` (already registered). |
| 8 | design | ArcGIS query safety and politeness. | `where` built only from regex-validated alphanumerics; `outSR=4326` + Fulton envelope check; paging; exactly one polygon per id; 1 request/second; cache. |
| 9 | design | Geometry: Polygon type, validity, SRID, metres, outliers, coverage. | PostGIS buffer of the convex hull, type/validity/SRID asserted; geography metres; ties by area then id; ≥3 points; isolated comps dropped; 3 km radius. |
| 10 | design | Never overwrite human-seeded areas; one transaction. | Provenance in `submarkets.notes`; only tool-written or empty areas replaced; single transaction. |
| 11 | design | A failing statement before the scoring loop poisons the shared connection. | Moot after #1: no new statement; the resolver is part of existing SELECTs. |
| 12 | design | Tests: xlsx is gitignored; static scans list modules by name. | Synthetic workbook built in the test; module added to `ALL_PIPELINE_AST` and `_PIPELINE_MODULES`. |
| 13 | design | Licensing and file safety. | Everything under gitignored `costar_exports/`; counts-only output; atomic CSV write; workbook size cap; sheets via workbook relationships. |
| 14 | design | No database. | CSV still written; exit 2 "areas not written"; areas all-or-nothing. |
| 15 | note | S8 still empty: the comps are building sales. | Documented; needs a `land_sales_comps` export. |
| 16 | note | Memo grouping mixes corridor names and ids; no new `action_type`. | Accepted; no `program.md` change needed (SR-14). |
