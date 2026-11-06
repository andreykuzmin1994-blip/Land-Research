"""Live-PostGIS check of the adjacency import and the site-fit buffers
(reviews/27_site_fit_buffers). No network.

The offline suite drives fakes; this runs the real SQL on a real PostGIS. It
creates and drops its OWN database (liveadj_<hex>) on the local server named
by DATABASE_URL, applies prepare.py's schema plus the PROPOSED
``parcel_adjacency`` DDL (and the proposed ``parcel_hazards`` DDL, for the
2x2 matrix) there only; both gates are switched on in-process; no file on
disk changes. Neighbour polygons are synthetic, in EPSG:2240 feet, handed to
``adjacency_import.compute_and_write`` as fetched sources would be:

  (a) BUF-09 units: a 1,000 x 1,000 ft South Fulton M-1 square (setback 40)
      with an R-3 neighbour on the north side: with a human-style class
      table (in memory only) the buildable area is 920 x 850 ft within 1 SF
      (110 ft = rear 100 + improvement 10, "rear value applied"); with the
      real rules (no class table) it is 920 x 920 and the text names the
      missing table; every stored segment is SRID 2240 MultiLineString;
  (b) BUF-08 misalignment (neighbour 15 ft off), sliver (2 ft strip: no
      buffer, counted, side unknown) and across-street (Atlanta, 60 ft gap:
      no buffer, side unknown);
  (c) BUF-04 polygon gap / uncovered city / county line: the side reads
      'adjacency unknown', nothing deducted;
  (d) BUF-10 L-shape, hole bordered by residential, multipart: no fitted
      envelope enters the exact strip (flat ends, round joins, 64 segments
      per quarter) of the residential segments;
  (e) BUF-14 Fairburn M-1 next to M-2: 50 ft with the literal-reading label;
      'M1' and own-district polygons are not 'other district'; own zoning
      changed to M-2 recomputes at read time (BUF-13); BUF-17 split parcel;
  (f) BUF-13 stale: geometry edited after the import -> stale, no deduction;
  (g) SR-9: a re-run with the same inputs changes 0 rows;
  (h) BUF-11 2x2 matrix hazards on/off x adjacency on/off on one parcel; the
      adjacency-on/empty ring text equals adjacency-off;
  (i) BUF-06 no parcel gains buildable or building SF with the gate on;
  (j) BUF-12 the runner's adjacency audit raises on a row change, and on a
      delete-and-restore of every row with the same ids (xmin);
  (k) review finding 4: one R-3 polygon solid along the north side and a
      1 ft R-3 strip along the south side: only the north side is stored;
  (l) review finding 7: own M-1 digitised 14 ft past the lot line (a street
      centreline), R-3 beyond it: no R-3 row, nothing deducted;
  (m) review finding 6: R-3 along the west half of the north side only:
      buildable 846,400 - 460 x 70 = 814,200 SF (flat ends, no disc past the
      end of the abutting part);
  (n) review finding 5: every applied deduction covers the exact strip of
      its distance and lies within 0.5 ft of it.

Refuses unless the DSN points at a local server (tests/live_txn_check.py's
guard). Exit 0 = pass, 1 = fail, 2 = refused.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import psycopg  # noqa: E402
from psycopg import conninfo  # noqa: E402

from live_txn_check import UnsafeTarget, check_connection_is_local, check_dsn_is_local  # noqa: E402

X0, Y0 = 2_200_000.0, 1_330_000.0       # near 33.6 N in Georgia West feet
STEP = 5000.0

_SQL_INSERT_FT = (
    "INSERT INTO parcels (parcel_id, county, state, market, acreage, geometry, centroid, "
    "zoning, zoning_description) "
    "VALUES (%s, 'fulton', 'GA', 'liveadj', %s, ST_Transform(ST_GeomFromText(%s, 2240), 4326), "
    "ST_Centroid(ST_Transform(ST_GeomFromText(%s, 2240), 4326)), %s, %s)"
)
_SQL_TO_4326 = "SELECT ST_AsGeoJSON(ST_Transform(ST_GeomFromText(%s, 2240), 4326), 12)"
_SQL_ROWS = ("SELECT neighbour_kind, neighbour_district, round(ST_Length(segment)::numeric, 1), "
             "ST_SRID(segment), GeometryType(segment) FROM parcel_adjacency "
             "WHERE parcel_id = %s ORDER BY neighbour_kind, neighbour_district")
_SQL_DUMP = ("SELECT adjacency_id, parcel_id, neighbour_kind, neighbour_district, "
             "ST_AsEWKB(segment), parcel_geom_md5, fetched_at::text FROM parcel_adjacency "
             "ORDER BY adjacency_id")
_SQL_MIN_DIST = (
    "SELECT MIN(ST_Distance(ST_GeomFromText(%s, 2240), a.segment)) FROM parcel_adjacency a "
    "WHERE a.parcel_id = %s AND a.neighbour_district = %s"
)
# The exact strip (flat ends, round joins, 64 segments per quarter) of the
# rows of one district, shrunk by 0.02 ft: an envelope must not enter it.
_SQL_IN_STRIP = (
    "SELECT COALESCE(bool_or(ST_Intersects(ST_GeomFromText(%s, 2240), ST_Buffer(a.segment, "
    "%s::float8, 'endcap=flat join=round quad_segs=64'))), false) FROM parcel_adjacency a "
    "WHERE a.parcel_id = %s AND a.neighbour_district = %s"
)
_SQL_RESTORE_ALL = (
    "CREATE TEMP TABLE adj_copy AS SELECT * FROM parcel_adjacency; "
    "DELETE FROM parcel_adjacency; INSERT INTO parcel_adjacency SELECT * FROM adj_copy; "
    "DROP TABLE adj_copy"
)
_SQL_ROW_IDS = ("SELECT adjacency_id FROM parcel_adjacency WHERE parcel_id = %s AND "
                "neighbour_district = %s ORDER BY adjacency_id")
_SQL_SHIFT = ("UPDATE parcels SET geometry = ST_Translate(geometry, 0.00001, 0) "
              "WHERE parcel_id = %s")
_SQL_SET_ZONING = "UPDATE parcels SET zoning = %s, zoning_description = %s WHERE parcel_id = %s"
_SQL_TOUCH = ("UPDATE parcel_adjacency SET source_as_of = COALESCE(source_as_of, '') || ' ' "
              "WHERE adjacency_id = (SELECT MIN(adjacency_id) FROM parcel_adjacency)")
_SQL_HAZARD_ROW = (
    "INSERT INTO parcel_hazards (parcel_id, nfhl_parcel_geom_md5, nfhl_parcel_sf, "
    "nfhl_mapped_sf, nfhl_undetermined_sf, sfha_sf, floodway_sf, x500_sf, nfhl_zone_sf, "
    "nfhl_zone_lomr_sf, zones, lomr_status, nfhl_as_of, nfhl_source, nfhl_fetched_at, sfha_geom) "
    "SELECT parcel_id, md5(ST_AsBinary(geometry)), 1000000, 1000000, 0, 100000, 0, 0, "
    "'{}'::jsonb, '{}'::jsonb, ARRAY['AE'], 'none', 'test', 'test', now(), "
    "ST_Multi(ST_GeomFromText(%s, 2240)) FROM parcels WHERE parcel_id = %s"
)


class Results:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, ok: bool, label: str) -> None:
        print(("OK: " if ok else "FAIL: ") + label)
        self.failures += 0 if ok else 1


def rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def at(i: int) -> tuple[float, float]:
    return X0 + (i % 6) * STEP, Y0 + (i // 6) * STEP


def gj(conn: Any, wkt: str) -> str:
    with conn.cursor() as cur:
        cur.execute(_SQL_TO_4326, (wkt,))
        return cur.fetchone()[0]


def corners_wkt(p: Any, f: Any) -> str:
    import site_fit
    c = site_fit.building_corners(p, f)
    pts = ",".join(f"{x} {y}" for x, y in list(c) + [c[0]])
    return f"POLYGON(({pts}))"


def envelope_wkt(p: Any, f: Any) -> str:
    """The full fitted ENVELOPE (building + courts + trailer parking) in 2240."""
    import math
    a = math.radians(f.angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    ox, oy = p._origin
    x0, x1 = f._xc - f.length_ft / 2.0, f._xc + f.length_ft / 2.0
    y0, y1 = f._y0, f._y0 + f.depth_ft
    pts = []
    for (u, v) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)):
        pts.append(f"{ox + u * ca - v * sa} {oy + u * sa + v * ca}")
    return f"POLYGON(({','.join(pts)}))"


def fixture(conn: Any) -> tuple[dict[str, str], list[Any]]:
    """Parcels (2240 WKT, zoning) and the neighbour polygons as fetched sources."""
    import adjacency_import as ai
    import zoning
    sf = lambda c, split=False: zoning.encode_stored("south_fulton", "south_fulton_zoning", c,  # noqa: E731
                                                     split, "-")
    fb = lambda c, split=False: zoning.encode_stored("fairburn", "fairburn_zoning", c, split,  # noqa: E731
                                                     "-")
    atl = lambda c: zoning.encode_stored("atlanta", "atlanta_zoning", c, False, "-")  # noqa: E731
    parcels: dict[str, tuple[str, tuple[str, str]]] = {}
    sfz: list[tuple[str, str]] = []     # (district, wkt) for the South Fulton layer
    fbz: list[tuple[str, str]] = []
    atz: list[tuple[str, str]] = []

    def square(i: int) -> tuple[float, float, str]:
        x, y = at(i)
        return x, y, rect(x, y, x + 1000, y + 1000)

    # (a) SQ: R-3 north, own M-1 around the rest.
    x, y, w = square(0)
    parcels["la-SQ"] = (w, sf("M-1"))
    sfz += [("R-3", rect(x - 300, y + 1000, x + 1300, y + 1600)),
            ("M-1", rect(x - 300, y - 300, x + 1300, y + 1000))]
    # (b) MIS: R-3 15 ft north of the lot line (gap), own M-1 elsewhere.
    x, y, w = square(1)
    parcels["la-MIS"] = (w, sf("M-1"))
    sfz += [("R-3", rect(x - 300, y + 1015, x + 1300, y + 1600)),
            ("M-1", rect(x - 300, y - 300, x - 0.0, y + 1000)),
            ("M-1", rect(x + 1000, y - 300, x + 1300, y + 1000)),
            ("M-1", rect(x, y - 300, x + 1000, y))]
    # (b) SLV: a 2 ft R-3 strip along the north side, nothing beyond.
    x, y, w = square(2)
    parcels["la-SLV"] = (w, sf("M-1"))
    sfz += [("R-3", rect(x, y + 1000, x + 1000, y + 1002)),
            ("M-1", rect(x - 300, y - 300, x - 0.0, y + 1000)),
            ("M-1", rect(x + 1000, y - 300, x + 1300, y + 1000)),
            ("M-1", rect(x, y - 300, x + 1000, y))]
    # (b) ATL: Atlanta I-1, R-4 across a 60 ft street gap to the north.
    x, y, w = square(3)
    parcels["la-ATL"] = (w, atl("I-1"))
    atz += [("R-4", rect(x - 300, y + 1060, x + 1300, y + 1600)),
            ("I-1", rect(x - 300, y - 300, x + 1300, y + 1000))]
    # (c) GAP: own M-1 polygon on three sides, nothing to the east (a gap,
    # an uncovered city or the county line).
    x, y, w = square(4)
    parcels["la-GAP"] = (w, sf("M-1"))
    sfz += [("M-1", rect(x - 300, y - 300, x + 1000, y + 1300))]
    # (d) LSH: L-shape, R-3 along the top of the short leg only.
    x, y = at(5)
    lsh = (f"POLYGON(({x} {y},{x + 1400} {y},{x + 1400} {y + 600},{x + 700} {y + 600},"
           f"{x + 700} {y + 1400},{x} {y + 1400},{x} {y}))")
    parcels["la-LSH"] = (lsh, sf("M-1"))
    sfz += [("R-3", rect(x + 700, y + 600, x + 1700, y + 1200)),
            ("M-1", rect(x - 300, y - 300, x + 1700, y + 600)),
            ("M-1", rect(x - 300, y + 600, x + 700, y + 1700))]
    # (d) HOLE: 1,600 ft square with a 200 ft enclave hole zoned R-3.
    x, y = at(6)
    hole = (f"POLYGON(({x} {y},{x + 1600} {y},{x + 1600} {y + 1600},{x} {y + 1600},{x} {y}),"
            f"({x + 700} {y + 700},{x + 700} {y + 900},{x + 900} {y + 900},{x + 900} {y + 700},"
            f"{x + 700} {y + 700}))")
    parcels["la-HOLE"] = (hole, sf("M-1"))
    sfz += [("R-3", rect(x + 700, y + 700, x + 900, y + 900)),
            ("M-1", f"POLYGON(({x - 300} {y - 300},{x + 1900} {y - 300},{x + 1900} {y + 1900},"
                    f"{x - 300} {y + 1900},{x - 300} {y - 300}),({x + 700} {y + 700},"
                    f"{x + 700} {y + 900},{x + 900} {y + 900},{x + 900} {y + 700},"
                    f"{x + 700} {y + 700}))")]
    # (d) MULTI: parcels.geometry is a Polygon column, so a multipart parcel
    # is a ring touching itself at one corner (MakeValid: two 1,000 ft
    # squares); R-3 east of the upper square.
    x, y = at(7)
    multi = (f"POLYGON(({x} {y},{x + 1000} {y},{x + 1000} {y + 1000},{x + 2000} {y + 1000},"
             f"{x + 2000} {y + 2000},{x + 1000} {y + 2000},{x + 1000} {y + 1000},"
             f"{x} {y + 1000},{x} {y}))")
    parcels["la-MULTI"] = (multi, sf("M-1"))
    sfz += [("R-3", rect(x + 2000, y + 700, x + 2500, y + 2300)),
            ("M-1", rect(x - 300, y - 300, x + 2000, y + 2300))]
    # (e) FB: Fairburn M-1 next to M-2 to the north; own polygon M-1 spelled
    # 'M1' on the west (a spelling, not another district).
    x, y, w = square(8)
    parcels["la-FB"] = (w, fb("M-1"))
    fbz += [("M-2", rect(x - 300, y + 1000, x + 1300, y + 1600)),
            ("M1", rect(x - 300, y - 300, x, y + 1000)),
            ("M-1", rect(x, y - 300, x + 1300, y + 1000))]
    # (e) SPLIT: South Fulton split-zoned parcel next to M-2.
    x, y, w = square(9)
    parcels["la-SPLIT"] = (w, sf("M-1", split=True))
    sfz += [("M-2", rect(x - 300, y + 1000, x + 1300, y + 1600)),
            ("M-1", rect(x - 300, y - 300, x + 1300, y + 1000))]
    # (e) OVH: Fairburn M-1 whose own polygon overhangs 1 ft, then M-2.
    x, y, w = square(10)
    parcels["la-OVH"] = (w, fb("M-1"))
    fbz += [("M-1", rect(x - 300, y - 300, x + 1300, y + 1001)),
            ("M-2", rect(x - 300, y + 1001, x + 1300, y + 1600))]
    # (f) STALE: as SQ.
    x, y, w = square(11)
    parcels["la-STALE"] = (w, sf("M-1"))
    sfz += [("R-3", rect(x - 300, y + 1000, x + 1300, y + 1600)),
            ("M-1", rect(x - 300, y - 300, x + 1300, y + 1000))]
    # (k) MIXSLV: R-3 solid north, a 1 ft R-3 strip along the south lot line,
    # own M-1 beyond the strip and on the east and west.
    x, y, w = square(12)
    parcels["la-MIXSLV"] = (w, sf("M-1"))
    sfz += [("R-3", rect(x - 300, y + 1000, x + 1300, y + 1600)),
            ("R-3", rect(x, y - 1, x + 1000, y)),
            ("M-1", rect(x - 300, y - 300, x + 1300, y - 1)),
            ("M-1", rect(x - 300, y - 1, x, y + 1000)),
            ("M-1", rect(x + 1000, y - 1, x + 1300, y + 1000))]
    # (l) CL: own M-1 digitised to a street centreline 14 ft north of the lot
    # line, R-3 beyond it.
    x, y, w = square(13)
    parcels["la-CL"] = (w, sf("M-1"))
    sfz += [("M-1", rect(x - 300, y - 300, x + 1300, y + 1014)),
            ("R-3", rect(x - 300, y + 1014, x + 1300, y + 1600))]
    # (m) PART: R-3 along the west half of the north side only.
    x, y, w = square(14)
    parcels["la-PART"] = (w, sf("M-1"))
    sfz += [("R-3", rect(x - 300, y + 1000, x + 500, y + 1600)),
            ("M-1", rect(x + 500, y + 1000, x + 1300, y + 1600)),
            ("M-1", rect(x - 300, y - 300, x + 1300, y + 1000))]
    with conn.cursor() as cur:
        for pid, (w, (z, d)) in parcels.items():
            cur.execute(_SQL_INSERT_FT, (pid, 23.0, w, w, z, d))
    conn.commit()

    def src(key: str, jurs: tuple[str, ...], polys: list[tuple[str, str]]) -> Any:
        feats = [ai.Feature(i + 1, d, gj(conn, w)) for i, (d, w) in enumerate(polys)]
        return ai.SourceData(key, jurs, features=feats, as_of="2026-11-01")
    sources = [src("south_fulton_zoning", ("south_fulton",), sfz),
               src("fairburn_zoning", ("fairburn",), fbz),
               src("atlanta_zoning", ("atlanta",), atz)]
    return {k: v[0] for k, v in parcels.items()}, sources


def rows(conn: Any, pid: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(_SQL_ROWS, (pid,))
        return [tuple(r) for r in cur.fetchall()]


def import_all(conn: Any, ids: list[str], sources: list[Any]) -> Any:
    import adjacency_import as ai
    res = ai.compute_and_write(conn, ids, sources)
    conn.commit()
    return res


def synthetic(conn: Any, res: Results) -> None:
    import adjacency
    import site_fit
    import zoning
    import runner
    from adjacency_helpers import LITERAL, MAX, chain, enabled, with_classes
    from zoning_helpers import patch_rules, verified
    geo, sources = fixture(conn)
    ids = sorted(geo)
    out = import_all(conn, ids, sources)
    res.check(out.processed == len(ids), f"import processed {out.processed} parcels")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM parcel_adjacency WHERE ST_SRID(segment) <> 2240 OR "
                    "GeometryType(segment) <> 'MULTILINESTRING'")
        res.check(cur.fetchone()[0] == 0, "BUF-09: every stored segment is 2240 MultiLineString")
    sq = rows(conn, "la-SQ")
    res.check(("zoned", "R-3", 1000.0, 2240, "MULTILINESTRING") in sq
              and sum(r[2] for r in sq if r[1] == "M-1") == 3000.0 and
              not any(r[0] == "uncovered" for r in sq),
              f"(a) SQ rows: R-3 north 1,000 ft, own M-1 3,000 ft, nothing unknown ({sq})")
    # The human's reading switches (in memory only): rear value on every
    # side, Fairburn Sec. 80-372 literally.
    classes = chain(with_classes("south_fulton", residential=["R-*", "AG-1"], readings=MAX),
                    with_classes("atlanta", residential=["R-*"]),
                    with_classes("fairburn", residential=["R-*"], readings=LITERAL))
    real = verified("south_fulton", "fairburn", "atlanta")
    table = verified("south_fulton", "fairburn", "atlanta", mutate=classes)
    site_fit._FIT_CACHE.clear()
    with patch_rules(real):
        off = dict(site_fit.project_parcels(conn, ids))
        with enabled():
            on_real = dict(site_fit.project_parcels(conn, ids))
    with patch_rules(table):
        off_t = dict(site_fit.project_parcels(conn, ids))
        with enabled():
            on = dict(site_fit.project_parcels(conn, ids))
    p = on["la-SQ"]
    res.check(abs(p.buildable_sf - 920 * 850) <= 1,
              f"(a) BUF-09 SQ buildable {p.buildable_sf} == 920 x 850 = 782,000 within 1 SF")
    res.check("residential 110 ft on 1 side" in p.adjacency and
              adjacency.REAR_APPLIED in p.constraints and adjacency.USE_NOT_CHECKED in
              p.constraints and adjacency.HEIGHT_NOT_APPLIED in p.constraints,
              "(a) BUF-07/15/16 SQ texts: 110 ft on 1 side, rear value, use and height notes")
    pr = on_real["la-SQ"]
    res.check(abs(pr.buildable_sf - 920 * 920) <= 1 and any(
        adjacency.CLASS_TABLE_MISSING in c for c in pr.constraints),
        f"(a) BUF-01 real rules: no residential buffer ({pr.buildable_sf}), table named")
    res.check(abs(off["la-SQ"].buildable_sf - 920 * 920) <= 1,
              "(a) gate off: 920 x 920")
    mis = rows(conn, "la-MIS")
    res.check(any(r[1] == "R-3" and r[2] == 1000.0 for r in mis),
              f"(b) BUF-08 misaligned R-3 (15 ft off) found along 1,000 ft ({mis})")
    res.check(abs(on["la-MIS"].buildable_sf - 920 * 850) <= 1,
              f"(b) MIS buildable {on['la-MIS'].buildable_sf} == 782,000")
    slv = rows(conn, "la-SLV")
    res.check(not any(r[1] == "R-3" for r in slv) and any(r[0] == "uncovered" for r in slv)
              and out.slivers >= 1,
              f"(b) BUF-08 2 ft sliver: no R-3 row, side unknown, {out.slivers} sliver(s) counted")
    res.check(abs(on["la-SLV"].buildable_sf - 920 * 920) <= 1, "(b) SLV: nothing deducted")
    atl = rows(conn, "la-ATL")
    res.check(not any(r[1] == "R-4" for r in atl) and any(r[0] == "uncovered" for r in atl)
              and abs(on["la-ATL"].buildable_sf - off_t["la-ATL"].buildable_sf) <= 0,
              f"(b) BUF-08 Atlanta across a 60 ft street: no buffer, side unknown ({atl})")
    gap = rows(conn, "la-GAP")
    res.check(any(r[0] == "uncovered" and r[2] == 1000.0 for r in gap)
              and "1 side adjacency unknown" in on["la-GAP"].adjacency
              and on["la-GAP"].buildable_sf == off_t["la-GAP"].buildable_sf,
              f"(c) BUF-04 east gap: 'adjacency unknown', nothing deducted ({gap})")
    for pid, dist in (("la-LSH", 110.0), ("la-HOLE", 110.0), ("la-MULTI", 110.0)):
        p = on[pid]
        po = off_t[pid]
        ok_fits = [f for f in p.fits if f.fits]
        dmin, entered = None, False
        with conn.cursor() as cur:
            for f in ok_fits:
                cur.execute(_SQL_MIN_DIST, (envelope_wkt(p, f), pid, "R-3"))
                d = float(cur.fetchone()[0])
                dmin = d if dmin is None else min(dmin, d)
                cur.execute(_SQL_IN_STRIP, (envelope_wkt(p, f), dist - 0.02, pid, "R-3"))
                entered = entered or bool(cur.fetchone()[0])
        res.check(p.buildable_sf < po.buildable_sf and ok_fits and not entered,
                  f"(d) BUF-10 {pid}: buildable {p.buildable_sf} < {po.buildable_sf}; no "
                  f"envelope enters the {dist:g} ft strip of R-3 (min distance {dmin})")
    fbp = on["la-FB"]
    res.check("other_district 50 ft on 1 side" in fbp.adjacency
              and adjacency.FAIRBURN_LITERAL in fbp.constraints
              and abs(fbp.buildable_sf - 930 * 915) <= 1,
              f"(e) BUF-14 Fairburn M-1 next to M-2: 50 ft, literal label, 'M1' not other "
              f"({fbp.buildable_sf} == 930 x 915)")
    ovh = on["la-OVH"]
    res.check("other_district 50 ft on 1 side" in ovh.adjacency,
              f"(e) BUF-17 own polygon 1 ft overhang then M-2: the M-2 side is found ({ovh.adjacency})")
    spl = on["la-SPLIT"]
    res.check(any("split-zoned parcel" in c for c in spl.constraints)
              and spl.buildable_sf == off_t["la-SPLIT"].buildable_sf,
              "(e) BUF-17 split parcel: other-district unknown, nothing deducted")
    m2 = zoning.encode_stored("fairburn", "fairburn_zoning", "M-2", False, "-")
    with conn.cursor() as cur:
        cur.execute(_SQL_SET_ZONING, (m2[0], m2[1], "la-FB"))
    conn.commit()
    with patch_rules(table), enabled():
        p2 = site_fit.project_parcel(conn, "la-FB")
    res.check("other_district 50 ft on 2 sides" in p2.adjacency,
              f"(e) BUF-13 own zoning M-1 -> M-2 recomputed at read: the M-1/'M1' sides become "
              f"other ({p2.adjacency})")
    with conn.cursor() as cur:
        cur.execute(_SQL_DUMP)
        before = cur.fetchall()
    out2 = import_all(conn, ids, sources)
    with conn.cursor() as cur:
        cur.execute(_SQL_DUMP)
        after = cur.fetchall()
    res.check(out2.changed == 0 and before == after,
              f"(g) SR-9 re-run changes 0 rows ({out2.changed} changed)")
    with conn.cursor() as cur:
        cur.execute(_SQL_SHIFT, ("la-STALE",))
    conn.commit()
    with patch_rules(table), enabled():
        st = site_fit.project_parcel(conn, "la-STALE")
    res.check(st.adjacency == adjacency.ADJ_STALE,
              f"(f) BUF-13 stale after a geometry edit: {st.adjacency}")
    # (i) BUF-06: never more buildable or building SF with the gate on.
    gained = [pid for pid in ids if (on[pid].buildable_sf or 0) > (off_t[pid].buildable_sf or 0)
              or (site_fit.max_building_sf(on[pid]) or 0) >
              (site_fit.max_building_sf(off_t[pid]) or 0)]
    res.check(not gained, f"(i) BUF-06 no parcel gains SF with the gate on ({gained})")
    # (h) BUF-11: 2x2 matrix on la-SQ with a 100 ft SFHA strip on the south.
    import hazards
    from hazards_helpers import enabled as hz_enabled
    x, y = at(0)
    with conn.cursor() as cur:
        cur.execute(_SQL_HAZARD_ROW, (rect(x, y, x + 1000, y + 100), "la-SQ"))
    conn.commit()
    got = {}
    for hz in (False, True):
        for adj_on in (False, True):
            site_fit._FIT_CACHE.clear()
            with patch_rules(table):
                ctx_h = hz_enabled() if hz else mock.patch.object(hazards, "_SOURCE_DDL", None)
                ctx_a = enabled() if adj_on else mock.patch.object(adjacency, "_SOURCE_DDL",
                                                                    None)
                with ctx_h, ctx_a:
                    got[(hz, adj_on)] = site_fit.project_parcel(conn, "la-SQ")
    # The hazard geometry is expanded by 2 ft before it is differenced (the
    # hazard SQL's conservative 2t), so the strip removes 102 ft of depth.
    exp = {(False, False): 920 * 920, (False, True): 920 * 850, (True, False): 920 * 858,
           (True, True): 920 * 788}
    res.check(all(abs(got[k].buildable_sf - v) <= 1 for k, v in exp.items()),
              "(h) BUF-11 2x2 matrix buildable: " + ", ".join(
                  f"hz={k[0]} adj={k[1]}: {got[k].buildable_sf} (want {v})" for k, v in exp.items()))
    with conn.cursor() as cur:
        cur.execute("DELETE FROM parcel_hazards")
    conn.commit()
    # on/empty: the ring text equals gate-off.
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE keep_adj AS SELECT * FROM parcel_adjacency")
        cur.execute("DELETE FROM parcel_adjacency")
    site_fit._FIT_CACHE.clear()
    with patch_rules(table):
        g_off = {k: v._geometry for k, v in site_fit.project_parcels(conn, ids).items()}
        with enabled():
            g_on = {k: v._geometry for k, v in site_fit.project_parcels(conn, ids).items()}
    res.check(g_off == g_on, "(h) BUF-03 gate on + empty table: ring text identical to gate off")
    with conn.cursor() as cur:
        cur.execute("INSERT INTO parcel_adjacency SELECT * FROM keep_adj")
    conn.commit()
    # (j) BUF-12 runner audit.
    with enabled():
        fp = runner._adjacency_fingerprint(conn)
        ok = runner._adjacency_audit(conn, fp)["rows"] == fp[0]
        with conn.cursor() as cur:
            cur.execute(_SQL_TOUCH)
        conn.commit()
        try:
            runner._adjacency_audit(conn, fp)
            raised = False
        except runner.AdjacencyAuditFailed:
            raised = True
    res.check(ok and raised, "(j) BUF-12 runner audit: unchanged passes, a row change raises")
    with enabled():
        fp = runner._adjacency_fingerprint(conn)
        with conn.cursor() as cur:
            cur.execute(_SQL_DUMP)
            before = cur.fetchall()
            cur.execute(_SQL_RESTORE_ALL)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(_SQL_DUMP)
            same = cur.fetchall() == before
        try:
            runner._adjacency_audit(conn, fp)
            raised = False
        except runner.AdjacencyAuditFailed:
            raised = True
    res.check(same and raised, "(j) review finding 2: delete-and-restore with the same ids and "
                               "fetched_at raises (row text identical, xmin differs)")
    # (k) a thin strip of a district that is solid elsewhere is a sliver.
    mx = rows(conn, "la-MIXSLV")
    res.check([r[2] for r in mx if r[1] == "R-3"] == [1000.0]
              and abs(on["la-MIXSLV"].buildable_sf - 920 * 850) <= 1,
              f"(k) finding 4: 1 ft R-3 strip on the south not stored, north found; buildable "
              f"{on['la-MIXSLV'].buildable_sf} == 782,000 ({mx})")
    # (l) a neighbour beyond 14 ft of the own district does not abut.
    cl = rows(conn, "la-CL")
    res.check(not any(r[1] == "R-3" for r in cl)
              and abs(on["la-CL"].buildable_sf - 920 * 920) <= 1,
              f"(l) finding 7: own M-1 to a 14 ft centreline, R-3 beyond: no R-3 row, nothing "
              f"deducted ({on['la-CL'].buildable_sf}; {cl})")
    # (m) flat ends.
    pt = on["la-PART"]
    res.check(abs(pt.buildable_sf - (920 * 920 - 460 * 70)) <= 1
              and adjacency.STRIP_ENDS in pt.constraints,
              f"(m) finding 6: R-3 on the west half of the north side: buildable "
              f"{pt.buildable_sf} == 814,200 (flat ends), named")
    # (n) tolerance of the deduction: covers the exact strip, within 0.5 ft.
    sql, names = site_fit._compile(site_fit._ADJ_DEDUCTION)
    bad = []
    with conn.cursor() as cur:
        for pid in ("la-SQ", "la-LSH", "la-HOLE", "la-MULTI", "la-PART", "la-MIXSLV"):
            cur.execute(_SQL_ROW_IDS, (pid, "R-3"))
            rid = [int(r[0]) for r in cur.fetchall()]
            params = site_fit._named_params(names, {
                "adj_ids": rid, "adj_dists": [110.0] * len(rid),
                "vertex_factor": adjacency.VERTEX_FACTOR})
            cur.execute(
                "WITH ded AS (" + sql + ") SELECT ST_Covers(ST_Buffer(d.ad, 0.001), s.inner_s), "
                "ST_Covers(s.outer_s, d.ad), ST_HausdorffDistance(d.ad, s.exact) FROM ded d, "
                "(SELECT ST_Union(ST_Buffer(segment, 109.99, 'endcap=flat join=round "
                "quad_segs=64')) AS inner_s, ST_Union(ST_Buffer(segment, 110.5, "
                "'quad_segs=64')) AS outer_s, ST_Union(ST_Buffer(segment, 110.0, "
                "'endcap=flat join=round quad_segs=64')) AS exact FROM parcel_adjacency "
                "WHERE adjacency_id = ANY(%s)) s", params + (rid,))
            cov, within, haus = cur.fetchone()
            if not (cov and within):
                bad.append((pid, cov, within, haus))
    res.check(not bad, f"(n) finding 5: every deduction covers its exact 110 ft strip (to "
                       f"0.001 ft) and lies within 0.5 ft of the segment's 110 ft buffer "
                       f"({bad or 'all'})")


def main(argv: list[str] | None = None, environ: Any = os.environ) -> int:
    import prepare
    res = Results()
    dsn = environ.get("DATABASE_URL")
    try:
        check_dsn_is_local(dsn, environ)
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as admin:
            check_connection_is_local(admin, environ)
    except UnsafeTarget as exc:
        print(f"REFUSED: {exc}")
        return 2
    import adjacency
    import hazards
    dbname = f"liveadj_{secrets.token_hex(3)}"
    own = conninfo.make_conninfo(dsn, dbname=dbname)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{dbname}"')       # dbname is [a-z0-9_] only
    saved = os.environ.get("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = own
        with psycopg.connect(own) as conn:
            prepare.apply_schema(conn)
            conn.execute(adjacency.PROPOSED_DDL)       # own database only
            for s in adjacency.PROPOSED_INDEXES:
                conn.execute(s)
            conn.execute(hazards.PROPOSED_DDL)
            conn.commit()
            try:
                synthetic(conn, res)
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                import traceback
                traceback.print_exc()
                res.check(False, f"synthetic check raised {type(exc).__name__}: {exc}")
    finally:
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        print("OK: dropped the check's own database")
    if res.failures:
        print(f"FAIL: {res.failures} live adjacency check(s) failed")
        return 1
    print("OK: live adjacency check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
