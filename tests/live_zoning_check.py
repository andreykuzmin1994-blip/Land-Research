"""Live-PostGIS check of the zoning SQL (reviews/21_zoning). No network.

The offline suite drives fakes; this runs the real SQL on a real PostGIS:

  (a) research._SQL_ZONING_PRECEDENT / _BATCH: neighbours at ~1,500 m and
      ~1,700 m plus the parcel itself give exactly ONE hit (metres on
      geography, self excluded); a degree-unit variant of the same query
      would match both (the regression it guards against); with seq scans
      disabled the plan uses the GIST index on parcels.geometry.
  (b) zoning_import: the TEMP-table overlay (ST_MakeValid, geography areas)
      ranks a majority, a split, a tie (alphabetical) and a sliver
      correctly; the UPDATE writes only zoning columns; a second plan
      changes 0 rows; the parcel count is unchanged.
  (c) research's stored-zoning reads parse what the import wrote.

It refuses unless DATABASE_URL points at a local server (the guard of
tests/live_txn_check.py), prints only a masked DSN, and CREATES AND DROPS
ITS OWN DATABASE (livezoning_<hex>) on that server. Exit 0 = pass,
1 = fail, 2 = refused.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg import conninfo

from live_txn_check import UnsafeTarget, check_connection_is_local, check_dsn_is_local

REPO_ROOT = Path(__file__).resolve().parent.parent

_SQL_INSERT_PARCEL = (
    "INSERT INTO parcels (parcel_id, county, state, market, geometry, centroid, zoning, "
    "zoning_description) VALUES (%s, 'fulton', 'GA', 'livezoning', "
    "ST_GeomFromText(%s, 4326), ST_Centroid(ST_GeomFromText(%s, 4326)), %s, %s)"
)
_SQL_PARCEL_ROWS = (
    "SELECT parcel_id, zoning, zoning_description, last_updated FROM parcels ORDER BY parcel_id"
)
_SQL_COUNT = "SELECT COUNT(*) FROM parcels"
# The bug the module constant guards against: metres bound against degrees.
_SQL_DEGREE_VARIANT = (
    "SELECT p2.parcel_id FROM parcels p1 JOIN parcels p2 ON p2.parcel_id <> p1.parcel_id "
    "AND ST_DWithin(p1.geometry, p2.geometry, %s) WHERE p1.parcel_id = %s"
)
_SQL_EXPLAIN_PREFIX = "EXPLAIN "
_SQL_SEQSCAN_OFF = "SET enable_seqscan = off"

M_PER_DEG_LAT = 111_320.0


def guard_dsn(dsn: str | None, environ: Any = os.environ) -> dict[str, Any]:
    """Refuse anything but a local server (Z-24). Returns the parsed DSN."""
    parts = check_dsn_is_local(dsn, environ)
    if parts.get("host") not in (None, "", "localhost", "127.0.0.1", "::1") \
            and not str(parts.get("host", "")).startswith("/"):
        raise UnsafeTarget("host is not local")
    return parts


def _square(lng: float, lat: float, half_m: float = 10.0) -> str:
    import math
    dy = half_m / M_PER_DEG_LAT
    dx = half_m / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return (f"POLYGON(({lng-dx} {lat-dy},{lng+dx} {lat-dy},{lng+dx} {lat+dy},"
            f"{lng-dx} {lat+dy},{lng-dx} {lat-dy}))")


def _east(lng: float, lat: float, metres: float) -> float:
    import math
    return lng + metres / (M_PER_DEG_LAT * math.cos(math.radians(lat)))


class Results:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, ok: bool, label: str) -> None:
        print(("OK: " if ok else "FAIL: ") + label)
        self.failures += 0 if ok else 1


def check_precedent(conn: Any, research: Any, zoning: Any, res: Results) -> None:
    lng, lat = -84.55, 33.55
    z, d = zoning.encode_stored("south_fulton", "south_fulton_zoning", "M-1", False, None)
    rows = [("lz-T", lng), ("lz-N1500", _east(lng, lat, 1500.0)), ("lz-N1700", _east(lng, lat, 1700.0))]
    with conn.cursor() as cur:
        for pid, x in rows:
            w = _square(x, lat)
            cur.execute(_SQL_INSERT_PARCEL, (pid, w, w, z, d))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(research._SQL_ZONING_PRECEDENT,
                    (research._S9_PRECEDENT_BBOX_PAD_DEG, research._S9_PRECEDENT_RADIUS_M, "lz-T"))
        hits = sorted(r[0] for r in cur.fetchall())
    res.check(hits == ["lz-N1500"], f"(a) precedent within 1 mile: {len(hits)} hit (want 1, self excluded)")
    with conn.cursor() as cur:
        cur.execute(research._SQL_ZONING_PRECEDENT_BATCH,
                    (research._S9_PRECEDENT_BBOX_PAD_DEG, research._S9_PRECEDENT_RADIUS_M,
                     ["lz-T", "lz-N1700"]))
        batch = sorted((r[0], r[1]) for r in cur.fetchall())
    res.check(batch == [("lz-N1700", "lz-N1500"), ("lz-T", "lz-N1500")],
              f"(a) batch precedent agrees: {len(batch)} pairs (want 2)")
    with conn.cursor() as cur:
        cur.execute(_SQL_DEGREE_VARIANT, (research._S9_PRECEDENT_RADIUS_M, "lz-T"))
        wrong = len(cur.fetchall())
    res.check(wrong == 2, f"(a) degree-unit variant would match {wrong} (the regression guarded)")
    with conn.cursor() as cur:
        cur.execute(_SQL_SEQSCAN_OFF)
        cur.execute(_SQL_EXPLAIN_PREFIX + research._SQL_ZONING_PRECEDENT,
                    (research._S9_PRECEDENT_BBOX_PAD_DEG, research._S9_PRECEDENT_RADIUS_M, "lz-T"))
        plan = "\n".join(r[0] for r in cur.fetchall())
    conn.rollback()
    res.check("idx_parcels_geometry" in plan, "(a) plan can use the GIST index idx_parcels_geometry")


def _esri(x0: float, y0: float, x1: float, y1: float) -> list[list[list[float]]]:
    return [[[x0, y0], [x0, y1], [x1, y1], [x1, y0], [x0, y0]]]   # clockwise


def check_overlay(conn: Any, zi: Any, zoning: Any, research: Any, res: Results) -> None:
    # Four 100 m parcels in a row; zoning polygons in degrees around them.
    lng, lat = -84.40, 33.60
    half = 50.0
    import math
    dx = half / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
    dy = half / M_PER_DEG_LAT
    centres = {f"lz-P{i}": _east(lng, lat, 300.0 * i) for i in range(4)}
    with conn.cursor() as cur:
        for pid, x in centres.items():
            w = _square(x, lat, half)
            cur.execute(_SQL_INSERT_PARCEL, (pid, w, w, None, None))
    conn.commit()

    def box(pid: str, frac0: float, frac1: float) -> list:
        x = centres[pid]
        return _esri(x - dx + 2 * dx * frac0, lat - 2 * dy, x - dx + 2 * dx * frac1, lat + 2 * dy)
    feats = [
        {"attributes": {"Z": "M-1"}, "geometry": {"rings": box("lz-P0", -0.5, 0.8)}},   # 80% M-1
        {"attributes": {"Z": "AG-1"}, "geometry": {"rings": box("lz-P0", 0.8, 1.5)}},
        {"attributes": {"Z": "R-3"}, "geometry": {"rings": box("lz-P1", -0.5, 0.55)}},  # 55/45 split
        {"attributes": {"Z": "M-2"}, "geometry": {"rings": box("lz-P1", 0.55, 1.5)}},
        {"attributes": {"Z": "M-2"}, "geometry": {"rings": box("lz-P2", -0.5, 0.5)}},   # tie
        {"attributes": {"Z": "C-1"}, "geometry": {"rings": box("lz-P2", 0.5, 1.5)}},
        {"attributes": {"Z": "AG-1"}, "geometry": {"rings": box("lz-P3", 0.9, 1.5)}},   # 10% sliver
    ]
    src = zi.ZoningSource("live_overlay", "live", "zoning", "overlay", ("south_fulton",),
                          "https://live.test/0", None, "Z", None, False)
    group = sorted(centres)
    stored = {pid: (None, None) for pid in group}
    before = _count(conn)
    r = zi.match_overlay(conn, src, feats, group)
    got = {pid: (a[0], a[2]) for pid, a in r.assigned.items()}
    res.check(got == {"lz-P0": ("M-1", False), "lz-P1": ("R-3", True), "lz-P2": ("C-1", True)},
              f"(b) overlay: majority, split and tie ranked ({len(got)} assigned, want 3)")
    res.check((r.slivers, r.split) == (1, 2), f"(b) overlay: {r.slivers} sliver, {r.split} split")
    plan = zi.build_plan(stored, {p: "south_fulton" for p in group}, [src], {src.key: r})
    with conn.cursor() as cur:
        cur.execute(_SQL_PARCEL_ROWS)
        lu_before = {row[0]: row[3] for row in cur.fetchall()}
    n = zi.write_plan(conn, plan)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(_SQL_PARCEL_ROWS)
        after = {row[0]: row[1:] for row in cur.fetchall()}
    res.check(n == 3 and _count(conn) == before, f"(b) {n} rows updated, parcel count unchanged")
    res.check(all(after[p][2] == lu_before[p] for p in group), "(b) last_updated untouched")
    st = {p: zoning.parse_stored(after[p][0], after[p][1]) for p in group}
    res.check(st["lz-P0"].district == "M-1" and st["lz-P3"] is None,
              "(b) stored values parse back; the sliver parcel is untouched")
    stored2 = {p: (after[p][0], after[p][1]) for p in group}
    r2 = zi.match_overlay(conn, src, feats, group)
    plan2 = zi.build_plan(stored2, {p: "south_fulton" for p in group}, [src], {src.key: r2})
    conn.rollback()
    res.check(len(plan2.updates) == 0, "(b) a second import changes 0 rows")
    with conn.cursor() as cur:
        cur.execute(research._SQL_STORED_ZONING_BATCH, (group,))
        rows = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
        cur.execute(research._SQL_STORED_ZONING_FOR_MARKET, ("livezoning",))
        market_rows = cur.fetchall()
    conn.rollback()
    cls = zoning.classify_stored(*rows["lz-P0"]).cls
    res.check(cls == "by_right" and len(market_rows) == 6,
              f"(c) research reads the imported zoning ({len(market_rows)} rows with provenance)")


def _count(conn: Any) -> int:
    with conn.cursor() as cur:
        cur.execute(_SQL_COUNT)
        return int(cur.fetchone()[0])


def main(environ: Any = os.environ) -> int:
    dsn = environ.get("DATABASE_URL")
    try:
        parts = guard_dsn(dsn, environ)
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as admin:
            check_connection_is_local(admin, environ)
    except UnsafeTarget as exc:
        print(f"REFUSED: {exc}")
        return 2
    sys.path.insert(0, str(REPO_ROOT))
    import prepare  # noqa: E402
    print(f"target: {prepare._mask_dsn(dsn)}")
    dbname = f"livezoning_{secrets.token_hex(3)}"
    own = conninfo.make_conninfo(dsn, dbname=dbname)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{dbname}"')       # dbname is [a-z0-9_] only
    res = Results()
    try:
        import research  # noqa: E402
        import zoning  # noqa: E402
        import zoning_import as zi  # noqa: E402
        with psycopg.connect(own) as conn:
            prepare.apply_schema(conn)
            for label, fn in (("(a)", lambda: check_precedent(conn, research, zoning, res)),
                              ("(b)", lambda: check_overlay(conn, zi, zoning, research, res))):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    res.check(False, f"{label} raised {type(exc).__name__}")
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        print("OK: dropped the check's own database")
    if res.failures:
        print(f"FAIL: {res.failures} live zoning check(s) failed")
        return 1
    print("OK: live zoning check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
