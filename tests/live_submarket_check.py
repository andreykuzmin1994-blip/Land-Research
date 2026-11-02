"""Live-PostGIS check of CoStar submarket resolution (reviews/19_costar_import).

The offline suite only drives fakes; this runs the real SQL:

  (a) research._SQL_RESOLVED_SUBMARKET, through both _SQL_FETCH_PARCEL and
      _SQL_DISTINCT_SUBMARKETS_FOR_PARCELS: a parcel inside an area, ~2 km
      from it, ~5 km from it (keeps its stored value), and inside two
      overlapping areas (the smaller one wins); both queries agree.
  (b) costar_import.write_areas: writes a valid 4326 Polygon from points,
      keeps a human-seeded bbox, and clears a stale tool-written area, in
      one transaction.

Run after tests/test_postgis_smoke.py (which applies the schema), as
.github/workflows/discovery-fulton.yml does. Uses its own market
(livesub-<token>) and deletes every row it writes. Same local-only DSN
guard as tests/live_txn_check.py. Exit 0 = pass, 1 = fail, 2 = refused.
"""

from __future__ import annotations

import os
import secrets
import sys
from datetime import date
from pathlib import Path

import psycopg

from live_txn_check import UnsafeTarget, check_connection_is_local, check_dsn_is_local

REPO_ROOT = Path(__file__).resolve().parent.parent

_SQL_INSERT_MARKET = "INSERT INTO markets (market_id, market_name) VALUES (%s, %s)"
_SQL_INSERT_AREA = (
    "INSERT INTO submarkets (submarket_id, market_id, submarket_name, bbox, notes) "
    "VALUES (%s, %s, %s, ST_MakeEnvelope(%s, %s, %s, %s, 4326), %s)"
)
_SQL_INSERT_PARCEL = (
    "INSERT INTO parcels (parcel_id, county, state, market, submarket, centroid, geometry) "
    "VALUES (%s, 'fulton', 'GA', %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), "
    "ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 0.0005))"
)
_SQL_AREA_STATE = (
    "SELECT submarket_id, GeometryType(bbox), ST_IsValid(bbox), ST_SRID(bbox), notes "
    "FROM submarkets WHERE market_id = %s ORDER BY submarket_id"
)
_SQL_DELETE_PARCELS = "DELETE FROM parcels WHERE market = %s"
_SQL_DELETE_SUBMARKETS = "DELETE FROM submarkets WHERE market_id = %s"
_SQL_DELETE_MARKET = "DELETE FROM markets WHERE market_id = %s"


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
    import costar_import  # noqa: E402
    import research  # noqa: E402

    token = secrets.token_hex(3)
    market_name = f"LiveSub {token}"
    market = f"livesub_{token}"
    big, small, human, stale = (f"{market}__{n}" for n in ("big", "small", "human", "stale"))
    failures: list[str] = []

    def expect(label: str, got: object, want: object) -> None:
        print(f"{'OK' if got == want else 'FAIL'}: {label}: {got!r}")
        if got != want:
            failures.append(label)

    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(_SQL_INSERT_MARKET, (market, market_name))
            # big: 0.10 x 0.10 deg; small: inside big's corner.
            cur.execute(_SQL_INSERT_AREA, (big, market, "Big", -84.60, 33.60, -84.50, 33.70, "test"))
            cur.execute(_SQL_INSERT_AREA, (small, market, "Small", -84.60, 33.60, -84.58, 33.62, "test"))
            parcels = {
                f"{market}-inside": (-84.55, 33.65),     # inside big only
                f"{market}-overlap": (-84.59, 33.61),    # inside both -> small
                f"{market}-2km": (-84.48, 33.65),        # ~1.9 km east of big
                f"{market}-5km": (-84.45, 33.65),        # ~4.6 km east: keeps stored
            }
            for pid, (x, y) in parcels.items():
                cur.execute(_SQL_INSERT_PARCEL, (pid, market, "corridor_x", x, y, x, y))

            want = {f"{market}-inside": big, f"{market}-overlap": small,
                    f"{market}-2km": big, f"{market}-5km": "corridor_x"}
            for pid in parcels:
                cur.execute(research._SQL_FETCH_PARCEL, (pid,))
                expect(f"fetch {pid.rsplit('-', 1)[1]}", cur.fetchone()[2], want[pid])
            cur.execute(research._SQL_DISTINCT_SUBMARKETS_FOR_PARCELS, (list(parcels),))
            expect("prefetch agrees with per-parcel fetch",
                   sorted(r[0] for r in cur.fetchall()), sorted(set(want.values())))

            # (b) write_areas: human-seeded kept, stale tool area cleared.
            cur.execute(_SQL_INSERT_AREA, (human, market, "Human", -84.4, 33.4, -84.3, 33.5, "by hand"))
            cur.execute(_SQL_INSERT_AREA, (stale, market, "Stale", -84.4, 33.6, -84.3, 33.7,
                                           costar_import.AREA_NOTE_PREFIX + " old"))
        pts = [(-84.40, 33.80), (-84.41, 33.81), (-84.39, 33.81)]
        plan = costar_import.AreaPlan(points={"Fresh": pts, "Human": pts})
        with psycopg.connect(dsn) as conn:
            counts = costar_import.write_areas(conn, plan, date(2026, 10, 29), market_name)
            conn.commit()
        expect("write_areas counts", counts,
               {"written": 1, "kept_human_seeded": 1, "cleared_stale": 1, "name_drift": 0})
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(_SQL_AREA_STATE, (market,))
            state = {r[0]: r[1:] for r in cur.fetchall()}
        fresh = state.get(f"{market}__fresh")
        expect("fresh area is a valid 4326 polygon", fresh[:3] if fresh else None,
               ("POLYGON", True, 4326))
        expect("human-seeded area kept", state[human][3], "by hand")
        expect("stale tool area cleared", state[stale][0], None)
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(_SQL_DELETE_PARCELS, (market,))
            cur.execute(_SQL_DELETE_SUBMARKETS, (market,))
            cur.execute(_SQL_DELETE_MARKET, (market,))
        print("OK: cleaned up this check's rows")

    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("OK: live submarket resolution check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
