"""Live-PostGIS check of the flood/wetland import and its consumers
(reviews/26_flood_wetlands). No network.

The offline suite drives fakes; this runs the real SQL on a real PostGIS.
It creates and drops its OWN database (livehazards_<hex>) on the local
server named by DATABASE_URL, applies prepare.py's schema plus the PROPOSED
``parcel_hazards`` DDL there only (the gate is switched on in-process; no
file on disk changes), writes synthetic NFHL/NWI shapefiles to a temp dir
and imports them with ``hazards_import.run(mode="files")``:

  (a) units: a 1,000 ft square half covered by AE -> SFHA 50% within 0.1%;
      the 2240 area agrees with the geography area within 0.1%; a bogus
      Shape_Area attribute in the .dbf changes nothing;
  (b) rings and overlaps: a donut's hole is excluded, two overlapping AE
      features count once, a self-intersecting NWI ring does not raise,
      every share is in [0, 100] and SFHA >= floodway;
  (c) LOMR: an intersecting LOMR turns AE into a review pass; a LOMR in
      the parcel's bbox but off the parcel leaves the block;
  (d) noise floor: a 0.5 SF AE sliver passes, a 1.5 SF one blocks;
  (e) unmapped parcel -> gap; 0.2% zone -> pass; wetlands 25% -> fail,
      an excavated pond -> the deepwater/excavated review flag;
  (f) idempotency: a re-run, and a re-run with every file's records in
      reverse order, change 0 rows;
  (g) site fit: with a 200 ft floodway strip deducted every fitted
      envelope is ST_Disjoint from the strip; the fit cache follows the
      hazard data; geometry edited after the import reads stale;
  (h) a source failing mid-write leaves that source's columns byte-
      identical (exit 4) while a partial import changes only its parcels;
  (i) scoring: the cycle prefetch equals the per-parcel read, and a parcel
      with no hazard row scores exactly as with the gate off.

Refuses unless the DSN points at a local server (tests/live_txn_check.py's
guard). Exit 0 = pass, 1 = fail, 2 = refused.
"""

from __future__ import annotations

import io
import json
import math
import os
import secrets
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import psycopg  # noqa: E402
from psycopg import conninfo  # noqa: E402

from live_txn_check import UnsafeTarget, check_connection_is_local, check_dsn_is_local  # noqa: E402

X0, Y0 = 2_200_000.0, 1_330_000.0       # near 33.6 N in Georgia West feet
STEP = 3000.0

_SQL_INSERT_FT = (
    "INSERT INTO parcels (parcel_id, county, state, market, acreage, geometry, centroid) "
    "VALUES (%s, 'fulton', 'GA', 'livehazards', %s, "
    "ST_Transform(ST_GeomFromText(%s, 2240), 4326), "
    "ST_Centroid(ST_Transform(ST_GeomFromText(%s, 2240), 4326)))"
)
_SQL_TO_NAD83 = "SELECT ST_AsGeoJSON(ST_Transform(ST_GeomFromText(%s, 2240), 4269), 12)"
_SQL_ROW = "SELECT * FROM parcel_hazards WHERE parcel_id = %s"
_SQL_DUMP = ("SELECT parcel_id, nfhl_parcel_geom_md5, nfhl_zone_sf::text, sfha_sf, floodway_sf, "
             "x500_sf, nfhl_as_of, nfhl_fetched_at::text, ST_AsEWKB(sfha_geom), "
             "nwi_parcel_geom_md5, wetland_sf, wetland_type_sf::text, nwi_fetched_at::text, "
             "ST_AsEWKB(wetland_geom) FROM parcel_hazards ORDER BY parcel_id")
_SQL_AREA_RATIO = (
    "SELECT ST_Area(ST_Transform(g, 2240)) * 0.09290341161 / ST_Area(geography(g)) "
    "FROM (SELECT ST_CollectionExtract(ST_MakeValid(geometry), 3) AS g FROM parcels "
    "WHERE parcel_id = %s) v"
)
_SQL_DISJOINT = "SELECT ST_Disjoint(ST_GeomFromText(%s, 2240), ST_GeomFromText(%s, 2240))"
_SQL_SHIFT = ("UPDATE parcels SET geometry = ST_Translate(geometry, 0.00001, 0) "
              "WHERE parcel_id = %s")
_SQL_ABSENT = "UPDATE parcel_hazards SET nfhl_fetched_at = NULL WHERE parcel_id = %s"


class Results:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, ok: bool, label: str) -> None:
        print(("OK: " if ok else "FAIL: ") + label)
        self.failures += 0 if ok else 1


def rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def at(i: int) -> tuple[float, float]:
    return X0 + (i % 5) * STEP, Y0 + (i // 5) * STEP


def nad83(conn: Any, wkt: str) -> list[list[tuple[float, float]]]:
    """2240 WKT polygon -> rings in NAD83 lon/lat, outer clockwise, holes ccw."""
    from hazards_helpers import ccw, cw
    with conn.cursor() as cur:
        cur.execute(_SQL_TO_NAD83, (wkt,))
        g = json.loads(cur.fetchone()[0])
    rings = [[tuple(p) for p in r] for r in g["coordinates"]]
    return [cw(rings[0])] + [ccw(r) for r in rings[1:]]


def nad83_raw(conn: Any, wkt: str) -> list[list[tuple[float, float]]]:
    with conn.cursor() as cur:
        cur.execute(_SQL_TO_NAD83, (wkt,))
        g = json.loads(cur.fetchone()[0])
    return [[tuple(p) for p in r] for r in g["coordinates"]]


Z = {"FLD_ZONE": "AE", "ZONE_SUBTY": "", "SFHA_TF": "T", "STATIC_BFE": 812.5,
     "DFIRM_ID": "13121C", "VERSION_ID": "1.1.1.0"}


def fixture(conn: Any, d: Path, reverse: bool = False) -> dict[str, str]:
    """Parcels in the DB (first call) and shapefiles in ``d``."""
    import hazards_helpers as hh
    names = ["lh-HALF", "lh-DONUT", "lh-OVER", "lh-FW", "lh-LOMR", "lh-LOMRB", "lh-SLIV05",
             "lh-SLIV15", "lh-WET25", "lh-WETX", "lh-BOWTIE", "lh-X500", "lh-UNMAP"]
    geo = {}
    for i, n in enumerate(names):
        x, y = at(i)
        side = 1500.0 if n == "lh-FW" else 1000.0
        if n == "lh-UNMAP":
            x, y = X0 + 8 * STEP, Y0             # east of the X polygon: no NFHL at all
        if n == "lh-LOMRB":
            geo[n] = f"POLYGON(({x} {y},{x + 1000} {y},{x} {y + 1000},{x} {y}))"
        else:
            geo[n] = rect(x, y, x + side, y + side)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM parcels WHERE market = 'livehazards'")
        if cur.fetchone()[0] == 0:
            for n, w in geo.items():
                cur.execute(_SQL_INSERT_FT, (n, 23.0, w, w))
    conn.commit()
    big = rect(X0 - 500, Y0 - 500, X0 + 5 * STEP, Y0 + 2 * STEP + 2000)   # rows 0-2: X zone
    nf: list[tuple[list, dict]] = [
        (nad83(conn, big), dict(Z, FLD_ZONE="X", ZONE_SUBTY="AREA OF MINIMAL FLOOD HAZARD",
                                SFHA_TF="F", STATIC_BFE=-9999))]
    x, y = at(0)
    nf.append((nad83(conn, rect(x - 50, y - 50, x + 500, y + 1050)), Z))
    x, y = at(1)
    donut = (f"POLYGON(({x - 100} {y - 100},{x + 1100} {y - 100},{x + 1100} {y + 1100},"
             f"{x - 100} {y + 1100},{x - 100} {y - 100}),({x + 300} {y + 300},{x + 300} {y + 700},"
             f"{x + 700} {y + 700},{x + 700} {y + 300},{x + 300} {y + 300}))")
    nf.append((nad83(conn, donut), Z))
    x, y = at(2)
    nf.append((nad83(conn, rect(x - 10, y - 10, x + 600, y + 1010)), Z))
    nf.append((nad83(conn, rect(x + 400, y - 10, x + 1010, y + 1010)), Z))
    x, y = at(3)
    strip = rect(x - 10, y + 650, x + 1510, y + 850)
    nf.append((nad83(conn, strip), dict(Z, ZONE_SUBTY="FLOODWAY")))
    nf.append((nad83(conn, rect(x - 10, y + 600, x + 1510, y + 900)), Z))
    for k in (4, 5):
        x, y = at(k)
        nf.append((nad83(conn, rect(x - 10, y - 10, x + 1010, y + 1010)), Z))
    x, y = at(6)
    nf.append((nad83(conn, rect(x + 100, y - 10, x + 101, y + 0.5)), Z))     # 0.5 SF inside
    x, y = at(7)
    nf.append((nad83(conn, rect(x + 100, y - 10, x + 101, y + 1.5)), Z))     # 1.5 SF inside
    x, y = at(11)
    nf.append((nad83(conn, rect(x - 10, y - 10, x + 300, y + 1010)),
               dict(Z, FLD_ZONE="X", ZONE_SUBTY="0.2 PCT ANNUAL CHANCE FLOOD HAZARD",
                    SFHA_TF="F", STATIC_BFE=-9999)))
    far = rect(X0 + 30 * STEP, Y0 + 30 * STEP, X0 + 30 * STEP + 10, Y0 + 30 * STEP + 10)
    nf.append((nad83(conn, far), dict(Z, FLD_ZONE="D", ZONE_SUBTY="", SFHA_TF="F")))
    if reverse:
        nf = nf[::-1]
    hh.write_shapefile(d / "nfhl", "S_FLD_HAZ_AR", nf, hh.NFHL_DBF_FIELDS)
    lomr = []
    x, y = at(4)
    lomr.append((nad83(conn, rect(x + 200, y + 200, x + 400, y + 400)), {"CASE_NO": "1"}))
    x, y = at(5)
    lomr.append((nad83(conn, rect(x + 700, y + 700, x + 990, y + 990)), {"CASE_NO": "2"}))
    hh.write_shapefile(d / "nfhl", "S_LOMR", lomr[::-1] if reverse else lomr,
                       (("CASE_NO", "C", 10, 0),))
    fields = hh.NWI_DBF_FIELDS + (("Shape_Area", "N", 18, 2),)
    wet = []
    x, y = at(8)
    wet.append((nad83(conn, rect(x - 10, y - 10, x + 250, y + 1010)),
                {"ATTRIBUTE": "PFO1A", "WETLAND_TY": "Freshwater Forested/Shrub Wetland",
                 "Shape_Area": 1.0}))
    x, y = at(9)
    wet.append((nad83(conn, rect(x + 100, y + 100, x + 200, y + 1100)),
                {"ATTRIBUTE": "PUBHx", "WETLAND_TY": "Freshwater Pond", "Shape_Area": 9e9}))
    x, y = at(10)
    bow = nad83_raw(conn, f"POLYGON(({x} {y},{x + 500} {y + 500},{x + 500} {y},{x} {y + 500},"
                          f"{x} {y}))")
    wet.append((bow, {"ATTRIBUTE": "PEM1C", "WETLAND_TY": "Freshwater Emergent Wetland",
                      "Shape_Area": 0}))
    wet.append((nad83(conn, far), {"ATTRIBUTE": "L1UBH", "WETLAND_TY": "Lake", "Shape_Area": 1}))
    wet.append((nad83(conn, rect(X0 - 900, Y0 - 900, X0 - 890, Y0 - 890)),
                {"ATTRIBUTE": "L1UBH", "WETLAND_TY": "Lake", "Shape_Area": 1}))
    hh.write_shapefile(d / "nwi", "GA_Wetlands", wet[::-1] if reverse else wet, fields)
    return {"strip": strip}


def run_import(inbox: Path) -> tuple[int, str]:
    import hazards_import as hi
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = hi.run(mode="files", inbox=inbox, check_open_run=False)
    return rc, buf.getvalue()


def dump(conn: Any) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(_SQL_DUMP)
        rows = [tuple(bytes(v) if isinstance(v, memoryview) else v for v in r)
                for r in cur.fetchall()]
    conn.rollback()
    return rows


def envelope_wkt(p: Any, f: Any) -> str:
    rad = math.radians(f.angle_deg or 0.0)
    c, s = math.cos(rad), math.sin(rad)
    ox, oy = p._origin
    h = f.length_ft / 2.0
    loc = [(f._xc - h, f._y0), (f._xc + h, f._y0), (f._xc + h, f._y0 + f.depth_ft),
           (f._xc - h, f._y0 + f.depth_ft)]
    pts = [(x * c - y * s + ox, x * s + y * c + oy) for x, y in loc]
    return "POLYGON((" + ",".join(f"{x} {y}" for x, y in pts + pts[:1]) + "))"


def synthetic(conn: Any, res: Results, dsn: str) -> None:
    import hazards
    import hazards_import as hi
    import research
    import site_fit
    tmp = Path(tempfile.mkdtemp())
    extra = fixture(conn, tmp / "a")
    fixture(conn, tmp / "b", reverse=True)
    rc, out = run_import(tmp / "a")
    res.check(rc == 0, f"import exit {rc}")
    ids = ["lh-HALF", "lh-DONUT", "lh-OVER", "lh-FW", "lh-LOMR", "lh-LOMRB", "lh-SLIV05",
           "lh-SLIV15", "lh-WET25", "lh-WETX", "lh-BOWTIE", "lh-X500", "lh-UNMAP"]
    a = hazards.assess_parcels(conn, ids)
    conn.rollback()

    def r(pid):
        with conn.cursor() as cur:
            cur.execute(_SQL_ROW, (pid,))
            cols = [d.name for d in cur.description]
            row = dict(zip(cols, cur.fetchone()))
        conn.rollback()
        return row
    half = r("lh-HALF")
    share = float(half["sfha_sf"]) / float(half["nfhl_parcel_sf"])
    res.check(abs(share - 0.5) < 0.001, f"(a) half-covered square: SFHA share {share:.5f}")
    with conn.cursor() as cur:
        cur.execute(_SQL_AREA_RATIO, ("lh-HALF",))
        ratio = cur.fetchone()[0]
    conn.rollback()
    res.check(abs(ratio - 1) < 0.001, f"(a) 2240 area / geography area {ratio:.5f}")
    w25 = r("lh-WET25")
    res.check(abs(float(w25["wetland_sf"]) - 250_000) < 250 and a["lh-WET25"].h6 == hazards.FAIL,
              f"(a/e) wetland 25%: {float(w25['wetland_sf']):,.0f} SF (bogus Shape_Area "
              f"ignored), H6 {a['lh-WET25'].h6}")
    dn = r("lh-DONUT")
    res.check(abs(float(dn["sfha_sf"]) - 840_000) < 840, f"(b) donut: SFHA {float(dn['sfha_sf']):,.0f} "
              "SF (hole excluded)")
    ov = r("lh-OVER")
    res.check(abs(float(ov["sfha_sf"]) - 1_000_000) < 1000,
              f"(b) overlapping AE counted once: {float(ov['sfha_sf']):,.0f} SF")
    res.check(a["lh-BOWTIE"].wetland_state == hazards.FRESH,
              f"(b) self-intersecting NWI ring imported: H6 {a['lh-BOWTIE'].h6}")
    ok = True
    for pid in ids:
        row = r(pid)
        for k in ("sfha_pct", "floodway_pct", "x500_pct", "wetland_pct"):
            v = row.get(k)
            ok &= v is None or 0 <= float(v) <= 100.0001
        if row.get("sfha_sf") is not None:
            ok &= float(row["sfha_sf"]) + 0.01 >= float(row["floodway_sf"])
            ok &= float(row["sfha_sf"]) + float(row["x500_sf"]) <= float(row["nfhl_parcel_sf"]) + 1
    res.check(ok, "(b) every share in [0, 100], SFHA >= floodway, SFHA + 0.2% <= parcel")
    res.check(a["lh-LOMR"].h4 == hazards.REVIEW and a["lh-LOMR"].blocker is None,
              f"(c) AE + intersecting LOMR: H4 {a['lh-LOMR'].h4}")
    res.check(a["lh-LOMRB"].h4 == hazards.BLOCK,
              f"(c) LOMR in the bbox but off the parcel: H4 {a['lh-LOMRB'].h4}")
    res.check(a["lh-SLIV05"].h4 == hazards.PASS and a["lh-SLIV15"].h4 == hazards.BLOCK,
              f"(d) 0.5 SF sliver {a['lh-SLIV05'].h4}, 1.5 SF sliver {a['lh-SLIV15'].h4}")
    res.check(a["lh-UNMAP"].h4 == hazards.GAP and a["lh-X500"].h4 == hazards.PASS,
              f"(e) unmapped {a['lh-UNMAP'].h4}, 0.2% zone {a['lh-X500'].h4}")
    res.check(any("deepwater or excavated" in t for t in a["lh-WETX"].reviews),
              "(e) excavated pond -> deepwater/excavated review")
    res.check(a["lh-FW"].blocker.startswith("H4: FEMA regulatory floodway"),
              "(e) floodway strip blocks")
    before = dump(conn)
    rc, out = run_import(tmp / "a")
    res.check(rc == 0 and "0 changed" in out and dump(conn) == before,
              "(f) re-run with the same files: 0 rows changed")
    rc, out = run_import(tmp / "b")
    res.check(rc == 0 and dump(conn) == before,
              "(f) re-run with every record in reverse order: identical rows")
    # (g) site fit
    site_fit._FIT_CACHE.clear()
    with mock.patch.object(hazards, "enabled", return_value=False):
        off = site_fit.project_parcels(conn, ["lh-FW"])["lh-FW"]
    on = site_fit.project_parcels(conn, ["lh-FW"])["lh-FW"]
    conn.rollback()
    disjoint = True
    for f in on.fits:
        if f.fits:
            with conn.cursor() as cur:
                cur.execute(_SQL_DISJOINT, (envelope_wkt(on, f), extra["strip"]))
                disjoint &= bool(cur.fetchone()[0])
    conn.rollback()
    res.check(on.status == "ok" and disjoint and (on.buildable_sf or 0) < (off.buildable_sf or 0),
              f"(g) floodway strip deducted: buildable {off.buildable_sf:,} -> "
              f"{on.buildable_sf:,} SF; every envelope ST_Disjoint from the strip: {disjoint}")
    res.check(on.flood_wetlands == hazards.deduction_text("fresh", "fresh")
              and hazards.STREAM_BUFFERS in on.constraints,
              f"(g) projection text: {on.flood_wetlands}")
    with conn.cursor() as cur:
        cur.execute(_SQL_ABSENT, ("lh-FW",))
    again = site_fit.project_parcels(conn, ["lh-FW"])["lh-FW"]
    conn.rollback()
    res.check(again.buildable_sf == off.buildable_sf and again.fits == off.fits,
              "(g) fit cache follows the hazard data (no stale fit reused)")
    with conn.cursor() as cur:
        cur.execute(_SQL_SHIFT, ("lh-HALF",))
        st = hazards.assess_parcels(conn, ["lh-HALF"])["lh-HALF"]
    conn.rollback()
    res.check(st.flood_state == hazards.STALE and st.blocker is None,
              "(g) geometry edited after the import -> stale, data_gap pass")
    # (h) injected failure in the NWI write; partial NFHL import
    before = dump(conn)
    calls = {"n": 0}
    real = hi._poly_params

    def boom(src, f):
        if src == "nwi":
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("injected")
        return real(src, f)
    with mock.patch.object(hi, "_poly_params", boom):
        rc, out = run_import(tmp / "a")
    after = dump(conn)
    nwi_cols = (9, 10, 11, 12, 13)
    res.check(rc == 4 and [tuple(x[i] for i in nwi_cols) for x in after]
              == [tuple(x[i] for i in nwi_cols) for x in before],
              f"(h) NWI failing mid-write: exit {rc}, NWI columns byte-identical")
    import hazards_helpers as hh
    part = tmp / "p"
    x, y = at(0)
    hh.write_shapefile(part / "nfhl", "S_FLD_HAZ_AR", [(nad83(conn, rect(x - 400, y - 400, x + 1400, y + 1400)),
                       dict(Z, FLD_ZONE="X", ZONE_SUBTY="AREA OF MINIMAL FLOOD HAZARD",
                            SFHA_TF="F"))], hh.NFHL_DBF_FIELDS)
    before = dump(conn)
    rc, out = run_import(part)
    after = {x[0]: x for x in dump(conn)}
    changed = sorted(p for p in after if after[p][:9] != {x[0]: x for x in before}[p][:9])
    res.check(changed == ["lh-HALF"], f"(h) partial NFHL file changes only its parcels: "
              f"{len(changed)} changed")
    # (i) scoring
    params = __import__("prepare").get_parameters()
    cache = research._prefetch_cycle_cache(conn, "livehazards", ids)
    per = {p: hazards.assess_parcels(conn, [p])[p] for p in ids}
    conn.rollback()
    res.check(dict(cache.hazard_assessments) == per,
              "(i) cycle prefetch == per-parcel read for every parcel")
    with conn.cursor() as cur:
        cur.execute(_SQL_INSERT_FT, ("lh-NOROW", 23.0, rect(X0, Y0 + 9 * STEP, X0 + 1000,
                                                             Y0 + 9 * STEP + 1000),
                                     rect(X0, Y0 + 9 * STEP, X0 + 1000, Y0 + 9 * STEP + 1000)))
    conn.commit()
    keys = ("composite_score", "confidence_score", "sub_scores", "actionability",
            "actionability_blockers", "strategy_fit", "primary_strategy")
    with mock.patch.object(hazards, "enabled", return_value=False):
        off_r = research.score_parcel("lh-NOROW", conn=conn, cycle_id="score-live-1",
                                      params=params, run_tag=None)
    on_r = research.score_parcel("lh-NOROW", conn=conn, cycle_id="score-live-2",
                                 params=params, run_tag=None)
    conn.commit()
    res.check(all(off_r[k] == on_r[k] for k in keys),
              f"(i) parcel without a hazard row scores as with the gate off "
              f"({on_r['actionability']})")
    if True:
        fw = research.score_parcel("lh-FW", conn=conn, cycle_id="score-live-3", params=params,
                                   run_tag=None)
    conn.commit()
    res.check(fw["actionability"] in ("FAIL:deal_killer", "FAIL:strategy", "FAIL:entitlement"),
              f"(i) floodway parcel actionability {fw['actionability']} "
              f"{fw['actionability_blockers']}")


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
    import hazards
    dbname = f"livehazards_{secrets.token_hex(3)}"
    own = conninfo.make_conninfo(dsn, dbname=dbname)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{dbname}"')       # dbname is [a-z0-9_] only
    saved = os.environ.get("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = own
        with psycopg.connect(own) as conn:
            prepare.apply_schema(conn)
            conn.execute(hazards.PROPOSED_DDL)        # own database only
            for s in hazards.PROPOSED_INDEXES:
                conn.execute(s)
            conn.commit()
            from hazards_helpers import enabled
            try:
                with enabled():
                    synthetic(conn, res, own)
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
        print(f"FAIL: {res.failures} live hazards check(s) failed")
        return 1
    print("OK: live hazards check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
