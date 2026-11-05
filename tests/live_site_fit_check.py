"""Live-PostGIS check of site_fit's SQL and geometry (reviews/22_site_fit). No network.

The offline suite drives fakes; this runs the real SQL on a real PostGIS.

Synthetic mode (default, CI): creates and drops its own database
(livesitefit_<hex>) on the local server and checks

  (a) units: a 1,000 x 1,000 ft square at 33.6 N (built in SRID 2240 feet)
      has a 2240 area within 0.1% of its geography area, and a 25 ft inward
      buffer leaves 950^2 SF within 0.2%; the projection fits a 920 ft
      cross-dock (lattice below 950 - tolerance);
  (b) degenerate geometry: NULL geometry -> no_geometry; a parcel smaller
      than twice the setback -> empty_after_setbacks; an invalid 'hole
      outside shell' polygon is made valid and projected without raising;
  (c) an injected failing read inside an open transaction is rolled back to
      its savepoint and the next query on the same connection succeeds;
  (d) every reported placement is covered (ST_Covers) by PostGIS's own
      unrounded buffer, and the SiteFit export's building lies inside a
      geography buffer of the exported boundary.

Corpus mode (``--corpus``): reads (never writes) an existing LOCAL database
named by SITE_FIT_CORPUS_DSN and prints counts and distributions only (no
parcel ids, no owners): how many parcels fit each prototype at full size,
only after shrinking, or not at all; the median projected SF; agreement with
the old acreage proxy; the 2240-vs-geography area ratio; ST_Covers of every
placement; and the runtime.

Refuses unless the DSN points at a local server (the guard of
tests/live_txn_check.py). Exit 0 = pass, 1 = fail, 2 = refused.
"""

from __future__ import annotations

import collections
import math
import os
import secrets
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
from psycopg import conninfo

from live_txn_check import UnsafeTarget, check_connection_is_local, check_dsn_is_local

REPO_ROOT = Path(__file__).resolve().parent.parent

_SQL_INSERT_FT = (
    "INSERT INTO parcels (parcel_id, county, state, market, acreage, geometry, centroid) "
    "VALUES (%s, 'fulton', 'GA', 'livesitefit', %s, "
    "ST_Transform(ST_GeomFromText(%s, 2240), 4326), "
    "ST_Centroid(ST_Transform(ST_GeomFromText(%s, 2240), 4326)))"
)
_SQL_INSERT_NULL = (
    "INSERT INTO parcels (parcel_id, county, state, market, acreage) "
    "VALUES (%s, 'fulton', 'GA', 'livesitefit', 10)"
)
_SQL_AREAS = (
    "SELECT ST_Area(ST_Transform(g, 2240)) * 0.09290341161 / ST_Area(geography(g)), "
    "ST_Area(ST_Buffer(ST_Transform(g, 2240), -25.0)) "
    "FROM (SELECT ST_CollectionExtract(ST_MakeValid(geometry), 3) AS g FROM parcels "
    "WHERE parcel_id = %s) v"
)
_SQL_ALL_IDS = "SELECT parcel_id, acreage FROM parcels ORDER BY parcel_id"
_SQL_RATIO_ALL = (
    "SELECT ST_Area(ST_Transform(g, 2240)) * 0.09290341161 / NULLIF(ST_Area(geography(g)), 0), "
    "ST_Area(ST_Buffer(ST_Transform(g, 2240), -25.0)) * 0.09290341161 / "
    "NULLIF(ST_Area(ST_Buffer(geography(g), -7.62)), 0), "
    "ST_Area(ST_Buffer(ST_Transform(g, 2240), -25.0)) * 0.09290341161 / "
    "NULLIF(ST_Area(geography(g)), 0) "
    "FROM (SELECT ST_CollectionExtract(ST_MakeValid(geometry), 3) AS g FROM parcels "
    "WHERE geometry IS NOT NULL) v"
)
_SQL_COVERS = (
    "SELECT ST_Covers(ST_Buffer(ST_Transform(ST_CollectionExtract(ST_MakeValid(geometry), 3), "
    "2240), -1.0 * %s::float8), ST_GeomFromText(%s, 2240)) FROM parcels WHERE parcel_id = %s"
)
_SQL_EXPORT_INSIDE = (
    "SELECT ST_Covers(ST_Buffer(geography(ST_GeomFromText(%s, 4326)), %s), "
    "geography(ST_GeomFromText(%s, 4326)))"
)
_SQL_FAILING = "SELECT 1 / 0"
_SQL_ONE = "SELECT 1"


class Results:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, ok: bool, label: str) -> None:
        print(("OK: " if ok else "FAIL: ") + label)
        self.failures += 0 if ok else 1


def guard_dsn(dsn: str | None, environ: Any = os.environ) -> dict[str, Any]:
    parts = check_dsn_is_local(dsn, environ)
    if parts.get("host") not in (None, "", "localhost", "127.0.0.1", "::1") \
            and not str(parts.get("host", "")).startswith("/"):
        raise UnsafeTarget("host is not local")
    return parts


def _square_wkt(x0: float, y0: float, side: float) -> str:
    return (f"POLYGON(({x0} {y0},{x0 + side} {y0},{x0 + side} {y0 + side},"
            f"{x0} {y0 + side},{x0} {y0}))")


def _envelope_wkt(p: Any, f: Any) -> str:
    rad = math.radians(f.angle_deg)
    c, s = math.cos(rad), math.sin(rad)
    ox, oy = p._origin
    h = f.length_ft / 2.0
    loc = [(f._xc - h, f._y0), (f._xc + h, f._y0), (f._xc + h, f._y0 + f.depth_ft),
           (f._xc - h, f._y0 + f.depth_ft)]
    pts = [(x * c - y * s + ox, x * s + y * c + oy) for x, y in loc]
    return "POLYGON((" + ",".join(f"{x} {y}" for x, y in pts + pts[:1]) + "))"


def _sitefit_building_ll(doc: dict) -> list[tuple[float, float]]:
    """Apply SiteFit's own element math (rect, rot, toLL) to the exported
    building: corners as (lng, lat)."""
    import site_fit
    el = doc["elements"][0]
    L, W = float(el["p"]["L"]), float(el["p"]["W"])
    a = math.radians(el["rot"])
    out = []
    for x, y in ((-L / 2, -W / 2), (L / 2, -W / 2), (L / 2, W / 2), (-L / 2, W / 2)):
        rx, ry = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
        lat, lng = site_fit.to_ll((rx, ry), el["center"])
        out.append((lng, lat))
    return out


def check_placements(conn: Any, projections: Any, res: Results, label: str) -> int:
    bad = n = 0
    for pid, p in projections.items():
        for f in p.fits:
            if not f.fits:
                continue
            with conn.cursor() as cur:
                cur.execute(_SQL_COVERS, (p.setback_ft, _envelope_wkt(p, f), pid))
                ok = bool(cur.fetchone()[0])
            n += 1
            bad += 0 if ok else 1
    res.check(bad == 0, f"{label} every reported envelope ({n}) is covered by PostGIS's own buffer")
    return n


def check_export(conn: Any, projections: Any, res: Results, limit: int = 25) -> None:
    import site_fit
    done = bad = 0
    for pid in sorted(projections):
        p = projections[pid]
        best = site_fit.best_fit(p)
        if best is None:
            continue
        boundary, corners = site_fit.export_geometry(conn, p, best)
        doc = site_fit.study_document(p, best, boundary, corners)
        n = len(doc["boundary"])
        ring_ok = len(doc["setbacks"]) == n == len(doc["edgeTags"]) and \
            doc["boundary"][0] != doc["boundary"][-1]
        bwkt = "POLYGON((" + ",".join(f"{lng} {lat}" for lat, lng in
                                      doc["boundary"] + doc["boundary"][:1]) + "))"
        b = _sitefit_building_ll(doc)
        wkt = "POLYGON((" + ",".join(f"{x} {y}" for x, y in b + b[:1]) + "))"
        with conn.cursor() as cur:
            cur.execute(_SQL_EXPORT_INSIDE, (bwkt, -p.setback_ft * 0.3048, wkt))
            inside = bool(cur.fetchone()[0])
        done += 1
        bad += 0 if (inside and ring_ok) else 1
        if done >= limit:
            break
    res.check(bad == 0, f"(d) SiteFit export: {done} studies; the building, placed with "
                        "SiteFit's own rot/toLL math, lies inside a geography buffer of the "
                        "exported boundary; setbacks/edgeTags match an open ring")


def synthetic(conn: Any, res: Results) -> None:
    import site_fit
    x0, y0 = 2_200_000.0, 1_330_000.0   # near 33.6 N in Georgia West feet
    with conn.cursor() as cur:
        sq = _square_wkt(x0, y0, 1000.0)
        cur.execute(_SQL_INSERT_FT, ("ls-SQ", 22.96, sq, sq))
        small = _square_wkt(x0 + 5000, y0, 45.0)
        cur.execute(_SQL_INSERT_FT, ("ls-SMALL", 0.05, small, small))
        bad = (f"POLYGON(({x0 + 9000} {y0},{x0 + 10000} {y0},{x0 + 10000} {y0 + 1000},"
               f"{x0 + 9000} {y0 + 1000},{x0 + 9000} {y0}),({x0 + 12000} {y0},{x0 + 12100} {y0},"
               f"{x0 + 12100} {y0 + 100},{x0 + 12000} {y0},{x0 + 12000} {y0}))")
        cur.execute(_SQL_INSERT_FT, ("ls-HOLE", 22.96, bad, bad))
        cur.execute(_SQL_INSERT_NULL, ("ls-NULL",))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(_SQL_AREAS, ("ls-SQ",))
        ratio, inner = cur.fetchone()
    res.check(abs(ratio - 1.0) < 0.001, f"(a) 2240 area / geography area = {ratio:.5f} (within 0.1%)")
    res.check(abs(inner / 950.0 ** 2 - 1.0) < 0.002,
              f"(a) 25 ft inward buffer of a 1,000 ft square = {inner:,.0f} SF (950^2 within 0.2%)")
    pr = site_fit.project_parcels(conn, ["ls-SQ", "ls-SMALL", "ls-HOLE", "ls-NULL"])
    cross = pr["ls-SQ"].fits[0]
    res.check(pr["ls-SQ"].status == "ok" and cross.length_ft == 920.0 and cross.docks == 134,
              f"(a) square: cross-dock {cross.length_ft} ft, {cross.docks} docks (want 920, 134)")
    res.check(pr["ls-NULL"].status == "no_geometry", "(b) NULL geometry -> no_geometry")
    res.check(pr["ls-SMALL"].status == "empty_after_setbacks", "(b) 45 ft parcel -> empty_after_setbacks")
    res.check(pr["ls-HOLE"].status == "ok", "(b) invalid 'hole outside shell' is made valid and projected")
    # (c) failing read inside an open transaction, then the same connection
    with conn.cursor() as cur:
        cur.execute(_SQL_ONE)
    saved = site_fit._SQL_BUFFERED_FOR_FIT
    site_fit._SQL_BUFFERED_FOR_FIT = _SQL_FAILING
    try:
        site_fit._FIT_CACHE.clear()
        err = site_fit.project_parcel(conn, "ls-SQ")
    finally:
        site_fit._SQL_BUFFERED_FOR_FIT = saved
    with conn.cursor() as cur:
        cur.execute(_SQL_ONE)
        alive = cur.fetchone()[0] == 1
    res.check(err.status == "error" and alive,
              f"(c) injected failure -> {err.status}; next query on the same connection works")
    conn.rollback()
    check_placements(conn, pr, res, "(d)")
    check_export(conn, pr, res)


def corpus(conn: Any, res: Results) -> None:
    import site_fit
    with conn.cursor() as cur:
        cur.execute(_SQL_ALL_IDS)
        rows = [(r[0], float(r[1] or 0)) for r in cur.fetchall()]
    ids = [r[0] for r in rows]
    acres = dict(rows)
    site_fit._FIT_CACHE.clear()
    t = time.monotonic()
    pr = site_fit.project_parcels(conn, ids)
    dt = time.monotonic() - t
    print(f"parcels: {len(ids)}; projection wall clock {dt:.1f} s "
          f"({1000 * dt / max(1, len(ids)):.0f} ms per parcel, one batch)")
    print("status: " + ", ".join(f"{k} {v}" for k, v in sorted(
        collections.Counter(p.status for p in pr.values()).items())))
    for k in site_fit.PROTOTYPE_KEYS:
        full = sum(1 for p in pr.values() for f in p.fits if f.key == k and f.full_size)
        shr = sum(1 for p in pr.values() for f in p.fits if f.key == k and f.fits and not f.full_size)
        print(f"{k}: full size {full}, only after shrinking {shr}")
    best = {pid: site_fit.max_building_sf(p) for pid, p in pr.items()}
    print(f"fit no prototype (status ok): "
          f"{sum(1 for pid, v in best.items() if v is None and pr[pid].status == 'ok')}")
    vals = sorted(v for v in best.values() if v)
    if vals:
        print(f"projected max building SF: median {statistics.median(vals):,.0f}, "
              f"quartiles {', '.join(f'{q:,.0f}' for q in statistics.quantiles(vals, n=4))}")
    proxy = {pid: acres[pid] >= 150_000 / 0.40 / 43_560 for pid in ids}
    proj = {pid: (best[pid] or 0) >= site_fit.BTS_MIN_FOOTPRINT_SF for pid in ids}
    tab = collections.Counter((proxy[p], proj[p]) for p in ids)
    print("acreage proxy (>= 8.61 ac) vs projection (>= 150K SF): "
          f"both {tab[(True, True)]}, proxy only {tab[(True, False)]}, "
          f"projection only {tab[(False, True)]}, neither {tab[(False, False)]}")
    print("setback source: " + ", ".join(f"{k} {v}" for k, v in sorted(collections.Counter(
        p.setback_source.split(" (")[0].split(",")[0] for p in pr.values()).items())))
    with conn.cursor() as cur:
        cur.execute(_SQL_RATIO_ALL)
        ratios = cur.fetchall()
    area = [r[0] for r in ratios if r[0] is not None]
    # Slivers (under half the parcel left inside the setbacks) are excluded:
    # there a geography buffer's own segmentation dominates the ratio.
    buf = [r[1] for r in ratios if r[1] is not None and (r[2] or 0) >= 0.5]
    slivers = sum(1 for r in ratios if r[1] is not None and (r[2] or 0) < 0.5)
    res.check(all(abs(a - 1) < 0.001 for a in area),
              f"2240 vs geography area within 0.1% for all {len(area)} parcels "
              f"(median {statistics.median(area):.5f})")
    res.check(all(abs(b - 1) < 0.005 for b in buf),
              f"25 ft inward buffer: 2240 vs geography within 0.5% for all {len(buf)} parcels "
              f"keeping >= half their area (median {statistics.median(buf):.5f}; "
              f"{slivers} slivers not compared)")
    check_placements(conn, pr, res, "corpus:")
    t = time.monotonic()
    again = site_fit.project_parcels(conn, ids)
    res.check(dict(again) == dict(pr), f"cached re-projection identical ({time.monotonic() - t:.1f} s)")
    conn.rollback()


def main(argv: list[str] | None = None, environ: Any = os.environ) -> int:
    argv = sys.argv[1:] if argv is None else argv
    sys.path.insert(0, str(REPO_ROOT))
    import prepare  # noqa: E402
    res = Results()
    if argv[:1] == ["--corpus"]:
        dsn = environ.get("SITE_FIT_CORPUS_DSN")
        try:
            guard_dsn(dsn, {**environ, "DATABASE_URL": dsn or ""})
        except UnsafeTarget as exc:
            print(f"REFUSED: {exc}")
            return 2
        print(f"corpus: {prepare._mask_dsn(dsn)} (read only)")
        with psycopg.connect(dsn) as conn:
            check_connection_is_local(conn, {**environ, "DATABASE_URL": dsn})
            conn.read_only = True
            corpus(conn, res)
    else:
        dsn = environ.get("DATABASE_URL")
        try:
            guard_dsn(dsn, environ)
            with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as admin:
                check_connection_is_local(admin, environ)
        except UnsafeTarget as exc:
            print(f"REFUSED: {exc}")
            return 2
        print(f"target: {prepare._mask_dsn(dsn)}")
        dbname = f"livesitefit_{secrets.token_hex(3)}"
        own = conninfo.make_conninfo(dsn, dbname=dbname)
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{dbname}"')       # dbname is [a-z0-9_] only
        try:
            with psycopg.connect(own) as conn:
                prepare.apply_schema(conn)
                try:
                    synthetic(conn, res)
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    res.check(False, f"synthetic check raised {type(exc).__name__}: {exc}")
        finally:
            with psycopg.connect(dsn, autocommit=True) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            print("OK: dropped the check's own database")
    if res.failures:
        print(f"FAIL: {res.failures} live site-fit check(s) failed")
        return 1
    print("OK: live site-fit check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
