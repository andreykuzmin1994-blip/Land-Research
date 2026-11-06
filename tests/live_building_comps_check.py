"""Live-PostGIS check of building sale and lease comps (reviews/28_building_comps).

The offline suite only drives fakes; this runs the real SQL in a market of
its own (livebc_<token>), deletes every row it writes, and refuses any
non-local DATABASE_URL (the guard of tests/live_txn_check.py). Run it
against a throwaway database of your own:

    createdb -T landtest bc_check && \\
    DATABASE_URL=postgresql://postgres:testpw@localhost:55432/bc_check \\
    python tests/live_building_comps_check.py

  (a) BC-04: score the market's parcels, import building and lease comps,
      score again: every parcel_scores column apart from score_id,
      scored_at, run_tag and experiment_id is equal, and submarkets.bbox is
      unchanged (an import that holds no area sheet never redraws areas).
  (b) BC-09 / SR-9: the tool's DELETE spares land rows, CSV-delivered
      building and lease rows and a CSV row whose raw forges the tool's
      import_origin and imported_on; a comp a CSV also delivered is
      counted and stored by the tool too, so the memo (which reads only
      the tool's rows) counts it (review finding 3); a second import gives
      the same rows.
  (c) BC-03: reporting resolves each parcel to the area research.py
      resolves it to (both resolvers agree per parcel), and a parcel whose
      stored submarket names another area shows its resolved area's rent.
  (d) BC-13: the lease window is inclusive at its first day, counted from
      the injected date.
  (e) BC-10: a value the database rejects rolls the whole table write
      back (row count unchanged).
  (f) BC-15: a failing read inside an open transaction runs in a SAVEPOINT;
      the connection stays usable.

Exit 0 = pass, 1 = fail, 2 = refused.
"""

from __future__ import annotations

import os
import secrets
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg

from live_txn_check import UnsafeTarget, check_connection_is_local, check_dsn_is_local

REPO_ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 2)

_SQL_INSERT_MARKET = "INSERT INTO markets (market_id, market_name) VALUES (%s, %s)"
_SQL_INSERT_AREA = (
    "INSERT INTO submarkets (submarket_id, market_id, submarket_name, bbox, notes) "
    "VALUES (%s, %s, %s, ST_MakeEnvelope(%s, %s, %s, %s, 4326), %s)"
)
_SQL_INSERT_CONTEXT = (
    "INSERT INTO market_context (submarket_id, as_of_date, source, vacancy_rate_pct, "
    "net_absorption_t12_sf, under_construction_sf, proposed_sf, asking_rent_nnn_psf) "
    "VALUES (%s, %s, 'costar', %s, %s, %s, %s, %s)"
)
_SQL_INSERT_PARCEL = (
    "INSERT INTO parcels (parcel_id, county, state, market, submarket, acreage, "
    "assessed_value_total, geometry, centroid) VALUES (%s, 'fulton', 'GA', %s, %s, 20.0, "
    "250000, ST_GeomFromText(%s, 4326), ST_Centroid(ST_GeomFromText(%s, 4326)))"
)
_SQL_INSERT_CSV_BUILDING = (
    "INSERT INTO sales_comps (address, submarket_id, comp_type, building_sf, sale_date, "
    "sale_price, price_psf, cap_rate, raw) VALUES (%s, %s, 'building', 200000, %s, "
    "20000000, 100, 5.5, %s::jsonb)"
)
_SQL_INSERT_CSV_LEASE = (
    "INSERT INTO leasing_comps (address, submarket_id, tenant_name, lease_start_date, "
    "lease_term_months, building_sf_leased, starting_rent_psf_nnn, lease_type, raw) "
    "VALUES (%s, %s, 'csv tenant', %s, 60, 250000, 6.0, 'NNN', %s::jsonb)"
)
_SQL_INSERT_TOOL_LAND = (
    "INSERT INTO sales_comps (address, submarket_id, comp_type, acres, sale_date, sale_price, "
    "price_per_acre, raw) VALUES ('land row', %s, 'land', 20, %s, 2000000, 100000, %s::jsonb)"
)
_SQL_SCORES = (
    "SELECT parcel_id, composite_score, confidence_score, actionability, "
    "actionability_blockers, sub_scores, strategy_fit, primary_strategy, investment_thesis, "
    "notes FROM parcel_scores WHERE parcel_id = ANY(%s) AND run_tag = %s ORDER BY parcel_id"
)
_SQL_BBOXES = (
    "SELECT submarket_id, ST_AsEWKT(bbox), notes FROM submarkets WHERE market_id = %s "
    "ORDER BY submarket_id"
)
_SQL_SALES_STATE = (
    "SELECT comp_type, address, raw->>'import_origin', raw ? 'submarket' FROM sales_comps "
    "WHERE submarket_id IN (SELECT submarket_id FROM submarkets WHERE market_id = %s) "
    "ORDER BY comp_type, address, raw->>'import_origin'"
)
_SQL_LEASE_STATE = (
    "SELECT address, raw->>'import_origin', raw ? 'submarket' FROM leasing_comps "
    "WHERE submarket_id IN (SELECT submarket_id FROM submarkets WHERE market_id = %s) "
    "ORDER BY address, raw->>'import_origin'"
)
_SQL_DELETE_SCORES = "DELETE FROM parcel_scores WHERE parcel_id = ANY(%s)"
_SQL_DELETE_FLAGS = "DELETE FROM flagged_items WHERE parcel_id = ANY(%s) OR market = %s"
_SQL_DELETE_LOG = "DELETE FROM research_log WHERE parcel_id = ANY(%s) OR market = %s"
_SQL_DELETE_PARCELS = "DELETE FROM parcels WHERE parcel_id = ANY(%s)"
_SQL_DELETE_MC = ("DELETE FROM market_context WHERE submarket_id IN "
                  "(SELECT submarket_id FROM submarkets WHERE market_id = %s)")
_SQL_DELETE_SALES = ("DELETE FROM sales_comps WHERE submarket_id IN "
                     "(SELECT submarket_id FROM submarkets WHERE market_id = %s)")
_SQL_DELETE_LEASES = ("DELETE FROM leasing_comps WHERE submarket_id IN "
                      "(SELECT submarket_id FROM submarkets WHERE market_id = %s)")
_SQL_DELETE_SUBMARKETS = "DELETE FROM submarkets WHERE market_id = %s"
_SQL_DELETE_MARKET = "DELETE FROM markets WHERE market_id = %s"
_SQL_COUNT_LEASES = ("SELECT COUNT(*) FROM leasing_comps WHERE submarket_id IN "
                     "(SELECT submarket_id FROM submarkets WHERE market_id = %s)")
_SQL_SELECT_ONE = "SELECT 1"


def _square(lng: float, lat: float, half: float = 0.002) -> str:
    return (f"POLYGON(({lng - half} {lat - half},{lng + half} {lat - half},"
            f"{lng + half} {lat + half},{lng - half} {lat + half},{lng - half} {lat - half}))")


class Results:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        print(("ok   " if ok else "FAIL ") + label + (f" -- {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(label)


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    try:
        check_dsn_is_local(dsn, os.environ)
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as c:
            check_connection_is_local(c, os.environ)
    except UnsafeTarget as exc:
        print(f"REFUSED: {exc}")
        return 2
    sys.path.insert(0, str(REPO_ROOT))
    import json

    import costar_import as ci  # noqa: E402
    import prepare  # noqa: E402
    import reporting  # noqa: E402
    import research  # noqa: E402
    import test_building_comps as fx  # noqa: E402

    res = Results()
    tok = secrets.token_hex(3)
    market = f"livebc_{tok}"
    a, b = f"{market}__alpha", f"{market}__beta"
    pids = [f"livebc-{tok}-p{i}" for i in (1, 2, 3)]
    run1, run2 = f"livebc-{tok}-1", f"livebc-{tok}-2"
    counties = dict(research._MARKET_TO_COUNTIES)
    counties[market] = list(counties["atlanta"])
    print(f"market: {market}")
    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            conn.execute(_SQL_INSERT_MARKET, (market, market))
            conn.execute(_SQL_INSERT_AREA, (a, market, "Alpha", -84.52, 33.68, -84.48, 33.72,
                                            ci.AREA_NOTE_PREFIX + " live"))
            conn.execute(_SQL_INSERT_AREA, (b, market, "Beta", -84.42, 33.68, -84.38, 33.72,
                                            ci.AREA_NOTE_PREFIX + " live"))
            conn.execute(_SQL_INSERT_CONTEXT, (a, date(2026, 9, 30), 4.0, 2_000_000, 500_000,
                                               None, 7.0))
            conn.execute(_SQL_INSERT_CONTEXT, (b, date(2026, 9, 30), 9.0, -100_000, 2_000_000,
                                               None, 5.0))
            # p1 sits in Alpha but its stored submarket names Beta (BC-03).
            for pid, (lng, lat, stored) in zip(pids, ((-84.50, 33.70, b), (-84.40, 33.70, b),
                                                      (-84.20, 33.70, None))):
                conn.execute(_SQL_INSERT_PARCEL, (pid, market, stored, _square(lng, lat),
                                                  _square(lng, lat)))
            conn.execute(_SQL_INSERT_TOOL_LAND, (a, date(2026, 3, 2), json.dumps(
                {"import_origin": ci.LAND_ORIGIN, "imported_on": "2026-11-01"})))
            # CSV-delivered rows (their raw carries the CSV's 'submarket' key);
            # one forges this tool's origin and imported_on keys.
            csv_raw = {"submarket": "Alpha", "address": "csv"}
            forged = {**csv_raw, "import_origin": ci.BUILDING_ORIGIN, "imported_on": "x"}
            conn.execute(_SQL_INSERT_CSV_BUILDING, ("5 Csv St", a, date(2026, 3, 2),
                                                    json.dumps(csv_raw)))
            conn.execute(_SQL_INSERT_CSV_BUILDING, ("6 Forged St", a, date(2026, 3, 2),
                                                    json.dumps(forged)))
            conn.execute(_SQL_INSERT_CSV_BUILDING, ("11 Test St", a, date(2026, 3, 2),
                                                    json.dumps(csv_raw)))   # duplicate of a tool row
            conn.execute(_SQL_INSERT_CSV_LEASE, ("7 Csv St", a, date(2026, 7, 1), json.dumps(
                {**csv_raw, "import_origin": ci.LEASE_ORIGIN, "imported_on": "x"})))
            conn.commit()

        old = research._MARKET_TO_COUNTIES
        research._MARKET_TO_COUNTIES = counties
        try:
            research.run_scoring_cycle(market, run_tag=run1, experiment_id=run1)
        finally:
            research._MARKET_TO_COUNTIES = old
        with psycopg.connect(dsn) as conn:
            before = conn.execute(_SQL_SCORES, (pids, run1)).fetchall()
            bbox_before = conn.execute(_SQL_BBOXES, (market,)).fetchall()

        # The import: building and lease sheets (no area sheet).
        b_rows = ci.convert_building([fx.BUILDING_HEADER] + [fx.building_row(**{
            "Property Address": f"1{i} Test St", "Sale Price": psf * 200_000, "Price Per SF": psf,
            "Actual Cap Rate": cap, "Latitude": 33.70 + i * 1e-4, "Longitude": -84.50})
            for i, (psf, cap) in enumerate(((100, 0.05), (110, 0.055), (120, 0.06),
                                            (130, None), (140, 0.065), (150, 0.07)))],
            today=TODAY).rows
        l_rows = ci.convert_lease([fx.LEASE_HEADER] + [fx.lease_row(**{
            "Property Address": f"2{i} Test St", "Starting Rent/SF/Yr": r,
            "Latitude": 33.70, "Longitude": -84.50 + i * 1e-4})
            for i, r in enumerate((5.0, 6.0, 7.0, 8.0, 9.0))] + [fx.lease_row(**{
                "Property Address": "29 Test St", "Starting Rent/SF/Yr": 4.0,
                "Latitude": 33.70, "Longitude": -84.40})], today=TODAY).rows
        with prepare.get_connection() as conn:
            cb = ci.write_comps(conn, ci.BUILDING_KIND, b_rows, TODAY, market_name=market)
            cl = ci.write_comps(conn, ci.LEASE_KIND, l_rows, TODAY, market_name=market)
            conn.commit()
        res.check((cb["assigned"], cb["also_delivered"]) == (6, 1),
                  "(b) a building comp a CSV also delivered is counted and stored", repr(cb))
        res.check(cl["assigned"] == 6 and cl["submarkets"] == 2, "(b) leases assigned", repr(cl))

        old = research._MARKET_TO_COUNTIES
        research._MARKET_TO_COUNTIES = counties
        try:
            research.run_scoring_cycle(market, run_tag=run2, experiment_id=run2)
        finally:
            research._MARKET_TO_COUNTIES = old
        with psycopg.connect(dsn) as conn:
            after = conn.execute(_SQL_SCORES, (pids, run2)).fetchall()
            bbox_after = conn.execute(_SQL_BBOXES, (market,)).fetchall()
            sales1 = conn.execute(_SQL_SALES_STATE, (market,)).fetchall()
            leases1 = conn.execute(_SQL_LEASE_STATE, (market,)).fetchall()
        res.check(len(before) == 3 and before == after,
                  "(a) every parcel_scores column equal before and after the import",
                  f"{len(before)} vs {len(after)} rows")
        res.check(bbox_before == bbox_after, "(a) submarkets.bbox and notes unchanged")

        with prepare.get_connection() as conn:
            ci.write_comps(conn, ci.BUILDING_KIND, b_rows, TODAY, market_name=market)
            ci.write_comps(conn, ci.LEASE_KIND, l_rows, TODAY, market_name=market)
            conn.commit()
        with psycopg.connect(dsn) as conn:
            sales2 = conn.execute(_SQL_SALES_STATE, (market,)).fetchall()
            leases2 = conn.execute(_SQL_LEASE_STATE, (market,)).fetchall()
        res.check(sales1 == sales2 and leases1 == leases2, "(b) a second import gives the same rows")
        kept = {(t, addr) for t, addr, _o, _s in sales2}
        res.check({("land", "land row"), ("building", "5 Csv St"), ("building", "6 Forged St"),
                   ("building", "11 Test St")} <= kept,
                  "(b) land, CSV and forged-origin CSV rows survive the tool's DELETE",
                  repr(sorted(kept)))
        res.check(sorted((str(o) for t, addr, o, _s in sales2 if addr == "11 Test St"))
                  == sorted(["None", ci.BUILDING_ORIGIN]),
                  "(b) the CSV row and the tool's own row of the shared sale are both kept",
                  repr([r for r in sales2 if r[1] == "11 Test St"]))
        res.check(("7 Csv St", ci.LEASE_ORIGIN, True) in leases2,
                  "(b) the CSV lease with a forged origin survives")

        with prepare.get_connection() as conn:
            rows = [(pid, conn.execute(research._SQL_FETCH_PARCEL, (pid,)).fetchone()[2])
                    for pid in pids]
            bc = reporting.fetch_building_comps(conn, market, pids, TODAY)
        res.check(bc is not None and dict(rows) == dict(bc.resolved),
                  "(c) reporting resolves every parcel as research.py does",
                  f"{rows} vs {bc and dict(bc.resolved)}")
        res.check(bc is not None and bc.resolved.get(pids[0]) == a,
                  "(c) the parcel stored under Beta resolves to Alpha")
        rent, _ = reporting.comp_stat(bc, "rent", bc.resolved.get(pids[0]))
        res.check(rent is not None and rent.value == 7.0 and rent.n == 5
                  and rent.label == "Alpha submarket",
                  "(c) the snapshot shows the resolved area's rent", repr(rent))
        psf, _ = reporting.comp_stat(bc, "psf", a)
        cap, _ = reporting.comp_stat(bc, "cap", a)
        # Six tool sales (the one a CSV also delivered included, finding 3).
        res.check(psf is not None and psf.value == 125.0 and psf.n == 6
                  and cap is not None and cap.value == 0.06 and cap.n == 5,
                  "(c) sale $/SF and cap medians of the tool's rows; CSV cap 5.5 never counted",
                  f"{psf} {cap}")
        res.check(sorted(bc.caps.get(a, [])) == [0.05, 0.055, 0.06, 0.065, 0.07],
                  "(c) the cap rates read are the tool's fractions only", repr(bc.caps))

        # (d) window boundary: the first day is in, the day before is out.
        first = ci.months_before(TODAY, ci.LEASE_WINDOW_MONTHS)
        edge = [dict(l_rows[0], address="30 Test St", sign_date=first.isoformat()),
                dict(l_rows[0], address="31 Test St",
                     sign_date=(first - timedelta(days=1)).isoformat())]
        with prepare.get_connection() as conn:
            ci.write_comps(conn, ci.LEASE_KIND, edge, TODAY, market_name=market)
            conn.commit()
            bc = reporting.fetch_building_comps(conn, market, pids, TODAY)
        res.check(sorted(bc.rents.get(a, [])) == [5.0], "(d) lease window inclusive at its first day",
                  repr(bc.rents))

        # (e) a rejected value rolls the whole lease write back.
        with psycopg.connect(dsn) as conn:
            n_before = conn.execute(_SQL_COUNT_LEASES, (market,)).fetchone()[0]
        bad = [dict(l_rows[0], address="40 Test St"), dict(l_rows[0], address="41 Test St",
                                                              term_months=10 ** 12)]
        try:
            with prepare.get_connection() as conn:
                ci.write_comps(conn, ci.LEASE_KIND, bad, TODAY, market_name=market)
                conn.commit()
            rolled = False
        except psycopg.Error:
            rolled = True
        with psycopg.connect(dsn) as conn:
            n_after = conn.execute(_SQL_COUNT_LEASES, (market,)).fetchone()[0]
        res.check(rolled and n_before == n_after == 3,
                  "(e) a database error mid-write leaves the lease table as it was",
                  f"{rolled} {n_before} {n_after}")

        # (f) a failing read inside an open transaction does not poison it.
        with prepare.get_connection() as conn:
            conn.execute(_SQL_SELECT_ONE)                     # opens the transaction
            saved = reporting._SQL_BC_SALES
            reporting._SQL_BC_SALES = "SELECT 1/0"
            try:
                got = reporting.fetch_building_comps(conn, market, pids, TODAY)
            finally:
                reporting._SQL_BC_SALES = saved
            ok = conn.execute(_SQL_SELECT_ONE).fetchone() == (1,)
            conn.rollback()
        res.check(got is None and ok, "(f) failed read renders nothing; connection usable")
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(_SQL_DELETE_SCORES, (pids,))
            conn.execute(_SQL_DELETE_FLAGS, (pids, market))
            conn.execute(_SQL_DELETE_LOG, (pids, market))
            conn.execute(_SQL_DELETE_PARCELS, (pids,))
            for sql in (_SQL_DELETE_MC, _SQL_DELETE_SALES, _SQL_DELETE_LEASES,
                        _SQL_DELETE_SUBMARKETS, _SQL_DELETE_MARKET):
                conn.execute(sql, (market,))
            conn.commit()
        print("cleaned up")
    if res.failures:
        print(f"FAIL: {len(res.failures)} check(s)")
        return 1
    print("OK: live building-comps check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
