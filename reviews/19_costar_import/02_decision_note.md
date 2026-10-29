# 19 — CoStar Excel import: decision note

> Tier 2. Pre-change risks: `01_risk_review.md`. This note records the
> independent ADVERSARIAL post-change review (fresh context, did not write
> the code; it probed the live PostGIS and the real workbook, reporting
> counts only) and what changed. Model names are omitted per this
> session's policy, as in `reviews/18_test_readiness/`.

## Verified by the reviewer

- Read-time resolution leaves nothing behind on discard; both queries
  resolved the same set on the live DB.
- `conn.transaction()` is a real BEGIN/COMMIT and rolls back on refusal.
- Only `YYYY Qn` columns qualify; `report_date` is the quarter end.
- Stats and comps submarket names slug identically (0 mismatches).

## Findings and resolutions

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | blocking | Risk 2 did not hold: the Makefile lock check passes while the agent proposes (the lock is held only during evaluation), and the tool itself had no guard. | `costar_import.run` refuses (exit 3) when the current run has a baseline; `run_has_baseline` mirrors `runner.current_run_has_baseline` without importing runner (equivalence test; regex pinned). Lock check removed. |
| 2 | high | A failed import after `new_run` left a branch + `run_tag`; plain Start then baselined without areas. | Resume path imports when the run has no baseline yet; exit 3 handled. |
| 3 | perf | Resolver ~3 ms/parcel; the DISTINCT query evaluated it twice (~31 s/cycle at 5,000 parcels). | `s.bbox && ST_Expand(p.centroid, 0.05)` prefilter and `OFFSET 0`: prefetch 0.58 s for 5,000 parcels, 0.26 ms per-parcel fetch, same results. |
| 4 | should-fix | Stale tool areas survive a newer export. | Cleared in the same transaction (tool-noted areas of the market not in the plan). |
| 5 | should-fix | Coverage reporting and the 3 km fallback. | Output reports located share and submarkets with no area. Radius kept at 3 km, deliberately: inside-only covers 15-16% of the two corridors vs 72-85%; discovery is Fulton-only and the unmapped Fulton submarkets (North Fulton/Forsyth, Central Perimeter) are far north of both corridors. Revisit when more corridors or counties are added. |
| 6 | should-fix | Cache poisoning by an empty answer; unbounded paging. | Empty lots are not cached; paging capped and stopped on a repeated page. |
| 7 | should-fix | Bad names reported as "database error"; non-numeric cells raised with values in a traceback. | `ValueError` -> REFUSED (exit 1, type only); unknown cell kinds read as text. |
| 8 | should-fix | Submarket names and areas printed (and tee'd into the autopilot log). | Counts only. |
| 9 | should-fix | No live test of the resolver or the writer. | `tests/live_submarket_check.py` (inside / 2 km / 5 km / overlap tie / both queries agree; writer: valid polygon, human kept, stale cleared), run in `discovery-fulton.yml`. |
| nits | | Missing `r` attributes, header not first, strict namespace, phonetic runs, `%` strings, two pending workbooks, drift ignored. | All handled (local-name XML parsing, header search, `%` = percent, oldest-first import, drift counted). Not changed: a human edit of a tool-noted area is still treated as the tool's; `runner._check_corridor_bbox` now passes on CoStar areas (runner is immutable during runs; wording only). |

## Accepted / out of scope

- `sync_costar` still drops CSV exports in mid-run (pre-existing contract
  behaviour, weekly refreshes by design); the new `.xlsx` path is stricter.
- S8 stays empty until a `land_sales_comps` export exists.

## Evidence

Offline suite 936 OK. Live PostGIS 16/3.4: real workbook import (34
submarkets, 54 of 227 comp ids located, 4 areas), `test_postgis_smoke.py`,
`live_txn_check.py` and `live_submarket_check.py` pass.
