"""hazards_import.py: robots, paging, politeness, the run guard, the file
reader, counts-only output (reviews/26_flood_wetlands). Offline (SR-6): no
network, no database; the PostGIS side is tests/live_hazards_check.py."""

from __future__ import annotations

import ast
import io
import json
import struct
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import tests  # noqa: F401  (suite guards)
import hazards
import hazards_import as hi
import prepare
import rules_scrape
import zoning_import
from hazards_helpers import (NFHL_DBF_FIELDS, NWI_DBF_FIELDS, PRJ_ALBERS, PRJ_MERCATOR,
                             PRJ_NAD83, PRJ_WGS84, ccw, cw, enabled, write_shapefile)
from zoning_helpers import REPO_ROOT

FEMA_ROBOTS = ("User-agent: *\nDisallow: /*?*\nDisallow: /arcgis\n"
               "Disallow: /nri/report/viewer\n")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = dict(headers or {})
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/json"

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]


def text(status, body, ctype="text/plain", **h):
    return Resp(status, body, {"Content-Type": ctype, **h})


def js(obj):
    return Resp(200, json.dumps(obj))


class Http:
    """handler(url) -> Resp; records every URL and the clock at each call."""

    def __init__(self, handler, clock=None):
        self.handler, self.clock = handler, clock
        self.calls, self.times, self.headers = [], [], {}

    def get(self, url, **kw):
        self.calls.append(url)
        self.times.append((urlparse(url).hostname, self.clock.t if self.clock else 0))
        return self.handler(url)


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def fetcher(handler):
    c = Clock()
    http = Http(handler, c)
    return hi.HazardsFetcher(http, clock=c.clock, sleep=c.sleep), http, c


def allow_robots(url):
    if url.endswith("/robots.txt"):
        return text(404, "not found")
    return None


def layer_meta(name, fields, mrc=500):
    return {"name": name, "type": "Feature Layer", "geometryType": "esriGeometryPolygon",
            "fields": [{"name": f} for f in fields + ["OBJECTID"]], "maxRecordCount": mrc,
            "editingInfo": {"lastEditDate": 1767225600000}}


def square_feature(oid, attrs):
    return {"type": "Feature", "id": oid, "properties": attrs,
            "geometry": {"type": "Polygon", "coordinates": [[[-84.5, 33.5], [-84.49, 33.5],
                                                              [-84.49, 33.51], [-84.5, 33.5]]]}}


class ArcGIS:
    """A fake NFHL/NWI service: ``n`` features, pages of ``page``; hooks
    corrupt one answer."""

    def __init__(self, n=1234, page=500, fields=hi.NWI_FIELDS, name=hi.NWI_LAYER_NAME,
                 corrupt=None):
        self.n, self.page, self.fields, self.name, self.corrupt = n, page, fields, name, corrupt
        self.pages = 0

    def __call__(self, url):
        r = allow_robots(url)
        if r is not None:
            return r
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path.endswith("/MapServer"):
            return js({"layers": [{"id": 28, "name": hi.NFHL_LAYER_NAME},
                                  {"id": 1, "name": hi.LOMR_LAYER_NAME}]})
        if not u.path.endswith("/query"):
            return js(layer_meta(self.name, list(self.fields), self.page))
        if q.get("returnIdsOnly") == "true":
            return js({"objectIdFieldName": "OBJECTID", "objectIds": list(range(1, self.n + 1))})
        ids = [int(i) for i in q["objectIds"].split(",")]
        self.pages += 1
        feats = [square_feature(i, {f: "X" for f in self.fields}) for i in ids]
        body = {"type": "FeatureCollection", "features": feats}
        if self.corrupt:
            return self.corrupt(self.pages, body)
        return js(body)


# ---------------------------------------------------------------------------
class RobotsTests(unittest.TestCase):
    """FW-01: rules_scrape's robots policy, unchanged; a refusal is a source
    failure with zero data requests."""

    def run_rest(self, robots_answer):
        def handler(url):
            if url.endswith("/robots.txt"):
                return robots_answer(url)
            return ArcGIS()(url)
        f, http, _ = fetcher(handler)
        out = hi._gather_rest(f, ["fulton-a"], {"fulton-a": (-84.5, 33.5, -84.49, 33.51)})
        return out, f, http

    def test_fema_robots_text_disallows_the_query_path(self):
        out, f, _ = self.run_rest(lambda u: text(200, FEMA_ROBOTS))
        self.assertIn("robots.txt disallows", out["nfhl"].failed)
        self.assertIn("robots.txt disallows", out["nwi"].failed)
        self.assertEqual(f.data_calls(), [])
        rb = rules_scrape.Robots(FEMA_ROBOTS)
        self.assertIsNotNone(rules_scrape.robots_verdict(rb, "/arcgis/rest/services/public/NFHL/MapServer/28/query?f=json", hi.UA_TOKEN))
        self.assertIsNotNone(rules_scrape.robots_verdict(rb, "/arcgis/rest/services/public/NFHL/MapServer", hi.UA_TOKEN))

    def test_redirect_to_html_off_site_denies(self):
        out, f, _ = self.run_rest(lambda u: text(
            301, "<h1>Object Moved</h1>", "text/html",
            Location="https://www.fws.gov/program/national-wetlands-inventory/wetlands-mapper"))
        self.assertIn("robots unavailable", out["nwi"].failed)
        self.assertEqual(f.data_calls(), [])

    def test_html_200_denies(self):
        out, f, _ = self.run_rest(lambda u: text(200, "<html></html>", "text/html"))
        self.assertIn("robots unavailable", out["nfhl"].failed)
        self.assertEqual(f.data_calls(), [])

    def test_5xx_denies(self):
        out, f, _ = self.run_rest(lambda u: text(503, "busy"))
        self.assertTrue(out["nfhl"].failed and out["nwi"].failed)
        self.assertEqual(f.data_calls(), [])

    def test_404_allows(self):
        out, f, _ = self.run_rest(lambda u: text(404, "none"))
        self.assertIsNone(out["nwi"].failed)
        self.assertTrue(f.data_calls())

    def test_ai_tokens_are_honoured(self):
        rb = rules_scrape.Robots(f"User-agent: {rules_scrape.AI_AGENT_TOKENS[0]}\nDisallow: /\n")
        self.assertIsNotNone(rules_scrape.robots_verdict(rb, "/x", hi.UA_TOKEN))
        rb = rules_scrape.Robots(f"User-agent: {hi.UA_TOKEN}\nDisallow: /\n")
        self.assertIsNotNone(rules_scrape.robots_verdict(rb, "/x", hi.UA_TOKEN))
        self.assertIsNone(rules_scrape.robots_verdict(rules_scrape.Robots("User-agent: *\nAllow: /\n"),
                                                      "/x?y=1", hi.UA_TOKEN))

    def test_policy_is_inherited_not_overridden(self):
        self.assertIs(hi.HazardsFetcher.robots_for, rules_scrape.PoliteFetcher.robots_for)
        src = (REPO_ROOT / "hazards_import.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"allow_all\s*=\s*True|deny_all\s*=\s*False")
        self.assertNotIn("def robots_for", src)

    def test_user_agent_generic(self):
        self.assertNotRegex(hi.USER_AGENT, r"@|mailto|http")
        f, http, _ = fetcher(lambda u: text(404, ""))
        self.assertEqual(http.headers.get("User-Agent"), hi.USER_AGENT)


class PagingTests(unittest.TestCase):
    """FW-12: ids by envelope, features by ids, every page checked."""

    BOXES = [(-84.5, 33.5, -84.49, 33.51)]

    def test_1234_ids_three_pages(self):
        svc = ArcGIS(n=1234, page=500)
        f, http, _ = fetcher(svc)
        data = hi.fetch_rest_nwi(f, self.BOXES)
        self.assertEqual(len(data.features), 1234)
        self.assertEqual(svc.pages, 13)       # page size capped at OID_CHUNK (100)
        self.assertEqual(data.as_of, "2026-01-01")

    def assert_fails(self, corrupt, fields=hi.NWI_FIELDS):
        svc = ArcGIS(n=250, page=100, corrupt=corrupt, fields=fields)
        f, http, _ = fetcher(svc)
        with self.assertRaises(hi.SourceError):
            hi.fetch_rest_nwi(f, self.BOXES)

    def test_dropped_page_short(self):
        self.assert_fails(lambda n, b: js({**b, "features": b["features"][:-1]}) if n == 2 else js(b))

    def test_exceeded_transfer_limit(self):
        self.assert_fails(lambda n, b: js({**b, "exceededTransferLimit": True}))
        self.assert_fails(lambda n, b: js({**b, "properties": {"exceededTransferLimit": True}}))

    def test_html_error_body(self):
        self.assert_fails(lambda n, b: text(200, "<html>error</html>", "text/html"))

    def test_error_json(self):
        self.assert_fails(lambda n, b: js({"error": {"code": 500}}))

    def test_missing_field_in_layer(self):
        svc = ArcGIS(fields=("ATTRIBUTE",))
        f, _, _ = fetcher(svc)
        with self.assertRaises(hi.SourceError):
            hi.fetch_rest_nwi(f, self.BOXES)

    def test_missing_field_in_feature(self):
        def corrupt(n, b):
            for ft in b["features"]:
                ft["properties"].pop("WETLAND_TYPE")
            return js(b)
        self.assert_fails(corrupt)

    def test_layer_renamed(self):
        svc = ArcGIS(name="Wetlands (beta)")
        f, _, _ = fetcher(svc)
        with self.assertRaises(hi.SourceError):
            hi.fetch_rest_nwi(f, self.BOXES)

    def test_ids_truncated(self):
        def handler(url):
            if "returnIdsOnly" in url:
                return js({"objectIds": [1], "exceededTransferLimit": True})
            return ArcGIS()(url)
        f, _, _ = fetcher(handler)
        with self.assertRaises(hi.SourceError):
            hi.fetch_rest_nwi(f, self.BOXES)

    def test_nfhl_lomr_located_by_name(self):
        svc = ArcGIS(n=3, fields=hi.NFHL_FIELDS, name=hi.NFHL_LAYER_NAME)

        def handler(url):
            if "/MapServer/1" in url and "query" not in url:
                return js(layer_meta(hi.LOMR_LAYER_NAME, []))
            return svc(url)
        f, http, _ = fetcher(handler)
        data = hi.fetch_rest_nfhl(f, self.BOXES)
        self.assertEqual(len(data.features), 3)
        self.assertEqual(len(data.lomr), 3)
        self.assertTrue(any("/MapServer/1/query" in u for u in http.calls))
        self.assertTrue(all("Shape_Area" not in u and "ACRES" not in u for u in http.calls))
        self.assertTrue(all("outSR=4326" in u for u in http.calls if "objectIds=" in u))
        self.assertTrue(all("maxAllowableOffset" not in u for u in http.calls))

    def test_feature_order_does_not_matter(self):
        a = [hi.Feature("10", {}, "g10", 4326), hi.Feature("2", {}, "g2", 4326),
             hi.Feature("2", {}, "dup", 4326)]
        self.assertEqual([f.fid for f in hi.sort_features(a)], ["2", "10"])
        self.assertEqual([f.fid for f in hi.sort_features(reversed(a))],
                         ["2", "10"])


class PolitenessTests(unittest.TestCase):
    """FW-19 / SR-11 on a fake clock."""

    def test_one_request_per_second_per_host(self):
        svc = ArcGIS(n=450)
        f, http, c = fetcher(svc)
        hi.fetch_rest_nwi(f, [(-84.5, 33.5, -84.4, 33.6)])
        last: dict = {}
        for host, t in http.times:
            if host in last:
                self.assertGreaterEqual(t - last[host], 1.0 - 1e-9)
            last[host] = t

    def test_retry_after_capped_and_retries_bounded(self):
        calls = []

        def handler(url):
            r = allow_robots(url)
            if r:
                return r
            calls.append(url)
            return text(429, "slow", **{"Retry-After": "120"})
        f, _, _ = fetcher(handler)
        with self.assertRaises(hi.SourceError):
            f.get_json("https://example.test/x", {"f": "json"})
        self.assertEqual(len(calls), 1)
        calls.clear()

        def handler2(url):
            r = allow_robots(url)
            if r:
                return r
            calls.append(url)
            return text(503, "busy")
        f, _, c = fetcher(handler2)
        with self.assertRaises(hi.SourceError):
            f.get_json("https://example.test/x", {"f": "json"})
        self.assertEqual(len(calls), rules_scrape.MAX_RETRIES + 1)
        self.assertTrue(all(s <= rules_scrape.RETRY_AFTER_CAP_S for s in c.sleeps))

    def test_retry_after_honoured(self):
        n = {"k": 0}

        def handler(url):
            r = allow_robots(url)
            if r:
                return r
            n["k"] += 1
            return text(429, "slow", **{"Retry-After": "5"}) if n["k"] == 1 else js({"ok": 1})
        f, _, c = fetcher(handler)
        self.assertEqual(f.get_json("https://example.test/x", {}), {"ok": 1})
        self.assertIn(5.0, c.sleeps)


class ShapefileTests(unittest.TestCase):
    """FW-13 (rings), FW-11 (.prj), the dBASE reader."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_round_trip_donut_and_multipart(self):
        outer = cw([(0, 0), (10, 0), (10, 10), (0, 10)])
        hole = ccw([(2, 2), (4, 2), (4, 4), (2, 4)])
        other = cw([(20, 0), (30, 0), (30, 5), (20, 5)])
        shp = write_shapefile(self.tmp, "S_FLD_HAZ_AR", [
            ([outer, hole, other], {"FLD_ZONE": "AE", "ZONE_SUBTY": "", "SFHA_TF": "T",
                                    "STATIC_BFE": 812.5, "DFIRM_ID": "13121C",
                                    "VERSION_ID": "1.1.1.0"})], NFHL_DBF_FIELDS)
        feats, srid, ddate, box = hi.read_shapefile(shp, hi.NFHL_FIELDS,
                                                    lambda s: [(-1, -1, 40, 40)])
        self.assertEqual(srid, 4269)
        self.assertEqual(ddate, "2026-11-06")
        self.assertEqual(len(feats), 1)
        g = json.loads(feats[0].geojson)
        self.assertEqual(g["type"], "MultiPolygon")
        self.assertEqual(len(g["coordinates"]), 2)
        self.assertEqual(len(g["coordinates"][0]), 2)       # outer + hole
        self.assertEqual(feats[0].attrs["STATIC_BFE"], 812.5)
        self.assertEqual(feats[0].attrs["FLD_ZONE"], "AE")

    def test_bbox_filter_skips_far_records(self):
        near = cw([(0, 0), (1, 0), (1, 1), (0, 1)])
        far = cw([(50, 50), (51, 50), (51, 51), (50, 51)])
        shp = write_shapefile(self.tmp, "w", [([near], {"ATTRIBUTE": "PFO1A",
                                                        "WETLAND_TY": "Riverine"}),
                                              ([far], {"ATTRIBUTE": "PUBHx",
                                                       "WETLAND_TY": "Freshwater Pond"})],
                              NWI_DBF_FIELDS, PRJ_ALBERS)
        feats, srid, _, _ = hi.read_shapefile(shp, hi.NWI_FIELDS, lambda s: [(0.5, 0.5, 2, 2)])
        self.assertEqual(srid, 5070)
        self.assertEqual([f.fid for f in feats], ["0"])
        self.assertEqual(feats[0].attrs, {"WETLAND_TYPE": "Riverine", "ATTRIBUTE": "PFO1A"})

    def test_orientation(self):
        g = json.loads(hi.rings_to_geojson([cw([(0, 0), (4, 0), (4, 4), (0, 4)]),
                                            ccw([(1, 1), (2, 1), (2, 2), (1, 2)])]))
        self.assertEqual(len(g["coordinates"]), 1)
        self.assertEqual(len(g["coordinates"][0]), 2)
        g = json.loads(hi.rings_to_geojson([ccw([(0, 0), (4, 0), (4, 4), (0, 4)])]))
        self.assertEqual(len(g["coordinates"]), 1)        # SiteFit's fallback

    def test_prj(self):
        self.assertEqual(hi.srid_from_prj(PRJ_NAD83), 4269)
        self.assertEqual(hi.srid_from_prj(PRJ_WGS84), 4326)
        self.assertEqual(hi.srid_from_prj(PRJ_ALBERS), 5070)
        for bad in (PRJ_MERCATOR, "", PRJ_ALBERS.replace("-96.0", "-84.0"),
                    'PROJCS["NAD_1983_StatePlane_Georgia_West_FIPS_1002_Feet"]'):
            with self.assertRaises(hi.SourceError):
                hi.srid_from_prj(bad)

    def test_missing_field_and_bad_files(self):
        shp = write_shapefile(self.tmp, "w", [([cw([(0, 0), (1, 0), (1, 1)])],
                                               {"ATTRIBUTE": "PFO1A"})],
                              (("ATTRIBUTE", "C", 20, 0),))
        with self.assertRaises(hi.SourceError):
            hi.read_shapefile(shp, hi.NWI_FIELDS, lambda s: [(0, 0, 1, 1)])
        (self.tmp / "junk.shp").write_bytes(b"x" * 120)
        with self.assertRaises(hi.SourceError):
            hi.shp_header(self.tmp / "junk.shp")


# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=()):
        self.conn.executed.append((sql, tuple(params or ())))
        self.rows = list(self.conn.answer(sql, tuple(params or ())))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


class FakeConn:
    def __init__(self, answer):
        self.answer, self.executed, self.commits = answer, [], 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    @contextmanager
    def transaction(self):
        yield self


PARCELS = [("fulton-SECRET-PID-1", -84.5, 33.5, -84.501, 33.499, -84.499, 33.501, False),
           ("fulton-SECRET-PID-2", -84.6, 33.6, -84.601, 33.599, -84.599, 33.601, False)]


def answer_factory(*, mirror_baseline=False, table=True, parcels=None, prev=0):
    parcels = PARCELS if parcels is None else parcels
    def answer(sql, p):
        if sql == zoning_import._SQL_MIRROR_EXISTS:
            return [(mirror_baseline,)]
        if sql == zoning_import._SQL_MIRROR_BASELINE_FOR_TAG:
            return [(1,)]
        if sql == zoning_import._SQL_MIRROR_LATEST_RUN:
            return [("t1", "keep", True)]
        if sql == hi._SQL_TABLE_EXISTS:
            return [(table,)]
        if sql == hi._SQL_LIST_PARCELS:
            return parcels
        if sql in (hi._SQL_PREVIOUS_NOT_COVERED_NFHL, hi._SQL_PREVIOUS_NOT_COVERED_NWI):
            return [(prev,)]
        if sql == hi._SQL_COUNT_PARCELS:
            return [(2,)]
        if sql == hi._SQL_BOXES_IN_SRID:
            return [(r[0], r[3], r[4], r[5], r[6]) for r in parcels if not r[7]]
        if sql in (hi._SQL_UPSERT_NFHL, hi._SQL_UPSERT_NWI):
            return [(PARCELS[0][0], "13121C 1.1.1.0")]
        return []
    return answer


def run_tool(conn, **kw):
    @contextmanager
    def get_connection():
        yield conn
    buf = io.StringIO()
    with mock.patch.object(prepare, "get_connection", get_connection), \
            mock.patch.object(prepare, "dsn_available", return_value=True), \
            redirect_stdout(buf):
        rc = hi.run(**kw)
    return rc, buf.getvalue()


class RunGuardTests(unittest.TestCase):
    """FW-16: exit 3 on a baselined run (TSV or mirror), zero requests;
    exit 1 while the table is not enabled."""

    def test_gate_off_refuses(self):
        conn = FakeConn(answer_factory())
        rc, out = run_tool(conn, mode="files", check_open_run=False)
        self.assertEqual(rc, hi.EXIT_REFUSED)
        self.assertIn("01_prepare_mutation_proposal.md", out)
        self.assertEqual(conn.executed, [])

    def test_tsv_baseline_exit_3_no_requests(self):
        f, http, _ = fetcher(lambda u: text(404, ""))
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "experiment_log.tsv"
            log.write_text("commit\tmetric\tconfidence\tapi_calls\twall_clock_min\tstatus\t"
                           "description\nabc\t1\t0.5\t1\t1\tbaseline\trun=t1 baseline\n")
            conn = FakeConn(answer_factory())
            with enabled(), mock.patch.object(prepare, "current_run_tag", return_value="t1"):
                rc, out = run_tool(conn, mode="rest", fetcher=f, log_path=log)
        self.assertEqual(rc, hi.EXIT_RUN_OPEN)
        self.assertEqual(http.calls, [])
        self.assertEqual(conn.executed, [])

    def test_mirror_only_baseline_exit_3_no_requests(self):
        f, http, _ = fetcher(lambda u: text(404, ""))
        conn = FakeConn(answer_factory(mirror_baseline=True))
        with enabled(), mock.patch.object(prepare, "current_run_tag", return_value="t1"):
            rc, out = run_tool(conn, mode="rest", fetcher=f,
                               log_path=Path("/nonexistent/experiment_log.tsv"))
        self.assertEqual(rc, hi.EXIT_RUN_OPEN)
        self.assertEqual(http.calls, [])

    def test_table_missing_refuses(self):
        conn = FakeConn(answer_factory(table=False))
        with enabled():
            rc, out = run_tool(conn, mode="files", check_open_run=False)
        self.assertEqual(rc, hi.EXIT_REFUSED)

    def test_rest_mode_today_is_exit_4_with_no_data_request(self):
        f, http, _ = fetcher(lambda u: text(200, FEMA_ROBOTS) if u.endswith("robots.txt")
                             else js({}))
        conn = FakeConn(answer_factory())
        with enabled():
            rc, out = run_tool(conn, mode="rest", fetcher=f, check_open_run=False)
        self.assertEqual(rc, hi.EXIT_SOURCE_FAILED)
        self.assertEqual(f.data_calls(), [])
        last = out.strip().splitlines()[-1]
        self.assertIn("nfhl", last)
        self.assertIn("nwi", last)
        self.assertFalse(any(s in (hi._SQL_UPSERT_NFHL, hi._SQL_UPSERT_NWI)
                             for s, _ in conn.executed))

    def test_failed_source_keeps_the_other(self):
        conn = FakeConn(answer_factory())
        with tempfile.TemporaryDirectory() as d, enabled():
            inbox = Path(d)
            write_shapefile(inbox / "nwi", "GA", [([cw([(-84.502, 33.498), (-84.49, 33.498),
                                                        (-84.49, 33.51), (-84.502, 33.51)])],
                                                   {"ATTRIBUTE": "PFO1A",
                                                    "WETLAND_TY": "Freshwater Forested/Shrub Wetland"})],
                            NWI_DBF_FIELDS)
            rc, out = run_tool(conn, mode="files", inbox=inbox, check_open_run=False)
        self.assertEqual(rc, hi.EXIT_SOURCE_FAILED)
        self.assertIn("source nfhl: FAILED", out)
        self.assertIn("source nwi: 1 features", out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("1 source(s) FAILED: nfhl"))
        self.assertTrue(any(s == hi._SQL_UPSERT_NWI for s, _ in conn.executed))
        self.assertFalse(any(s == hi._SQL_UPSERT_NFHL for s, _ in conn.executed))


def _nwi_inbox(inbox: Path, name: str = "GA") -> None:
    write_shapefile(inbox / "nwi", name, [([cw([(-84.502, 33.498), (-84.49, 33.498),
                                                (-84.49, 33.51), (-84.502, 33.51)])],
                                           {"ATTRIBUTE": "PFO1A",
                                            "WETLAND_TY": "Freshwater Forested/Shrub Wetland"})],
                    NWI_DBF_FIELDS)


def _nfhl_inbox(inbox: Path) -> None:
    box = cw([(-84.7, 33.4), (-84.4, 33.4), (-84.4, 33.7), (-84.7, 33.7)])
    write_shapefile(inbox / "nfhl", "S_FLD_HAZ_AR", [([box], {
        "FLD_ZONE": "X", "ZONE_SUBTY": "AREA OF MINIMAL FLOOD HAZARD", "SFHA_TF": "F",
        "STATIC_BFE": -9999, "DFIRM_ID": "13121C", "VERSION_ID": "1.1.1.0"})],
        NFHL_DBF_FIELDS)


class ReviewRoundTests(unittest.TestCase):
    """Review findings 5, 6, 10 and 12 on the import tool."""

    def test_empty_repaired_geometry_is_counted_apart(self):
        parcels = PARCELS + [("fulton-SECRET-PID-3", None, None, None, None, None, None, True)]
        conn = FakeConn(answer_factory(parcels=parcels))
        with tempfile.TemporaryDirectory() as d, enabled():
            inbox = Path(d)
            _nwi_inbox(inbox)
            _nfhl_inbox(inbox)
            rc, out = run_tool(conn, mode="files", inbox=inbox, check_open_run=False)
        self.assertEqual(rc, hi.EXIT_OK, out)
        self.assertIn("3 Fulton parcels with geometry; 0 outside EPSG:2240's area of use "
                      "(not imported); 1 with no polygonal area after repair", out)
        ups = [p for s_, p in conn.executed if s_ == hi._SQL_UPSERT_NFHL]
        self.assertEqual(sorted(ups[0][0]), sorted(r[0] for r in PARCELS))

    def test_list_sql_uses_the_made_valid_geometry(self):
        for sql in (hi._SQL_LIST_PARCELS, hi._SQL_BOXES_IN_SRID):
            self.assertIn("ST_CollectionExtract(ST_MakeValid(geometry), 3)", sql)
        self.assertNotIn("ST_Centroid(geometry)", hi._SQL_LIST_PARCELS)
        self.assertIn("ST_IsEmpty(v)", hi._SQL_LIST_PARCELS)

    def test_database_error_after_a_commit_names_the_committed_source(self):
        base = answer_factory()

        def answer(sql, p):
            if sql == hi._SQL_UPSERT_NWI:
                raise RuntimeError("server closed the connection")
            return base(sql, p)
        conn = FakeConn(answer)
        with tempfile.TemporaryDirectory() as d, enabled(), \
                mock.patch.object(hi, "_connection_unusable", return_value=True):
            inbox = Path(d)
            _nwi_inbox(inbox)
            _nfhl_inbox(inbox)
            rc, out = run_tool(conn, mode="files", inbox=inbox, check_open_run=False)
        self.assertEqual(rc, hi.EXIT_DB)
        self.assertNotIn("hazards not imported", out)
        last = out.strip().splitlines()[-1]
        self.assertIn("after source(s) nfhl committed; nwi not written", last)
        self.assertIn("new run and baseline", last)

    def test_database_error_before_any_commit_says_not_imported(self):
        base = answer_factory()

        def answer(sql, p):
            if sql == hi._SQL_UPSERT_NFHL:
                raise RuntimeError("server closed the connection")
            return base(sql, p)
        conn = FakeConn(answer)
        with tempfile.TemporaryDirectory() as d, enabled(), \
                mock.patch.object(hi, "_connection_unusable", return_value=True):
            inbox = Path(d)
            _nwi_inbox(inbox)
            _nfhl_inbox(inbox)
            rc, out = run_tool(conn, mode="files", inbox=inbox, check_open_run=False)
        self.assertEqual(rc, hi.EXIT_DB)
        self.assertIn("hazards not imported: database error", out)

    def test_previously_imported_parcels_not_covered_are_counted(self):
        conn = FakeConn(answer_factory(prev=7))
        with tempfile.TemporaryDirectory() as d, enabled():
            inbox = Path(d)
            _nwi_inbox(inbox)
            _nfhl_inbox(inbox)
            rc, out = run_tool(conn, mode="files", inbox=inbox, check_open_run=False)
        self.assertEqual(rc, hi.EXIT_OK, out)
        self.assertEqual(out.count("7 previously imported parcels not covered "
                                   "(previous values kept)"), 2)
        q = [p for s_, p in conn.executed if s_ == hi._SQL_PREVIOUS_NOT_COVERED_NWI]
        self.assertEqual(len(q), 1)

    def test_nwi_package_with_several_shapefiles_picks_the_wetlands_file(self):
        with tempfile.TemporaryDirectory() as d:
            nd = Path(d) / "nwi"
            _nwi_inbox(Path(d), "GA_Wetlands")
            _nwi_inbox(Path(d), "GA_Wetlands_Project_Metadata")
            _nwi_inbox(Path(d), "GA_Wetlands_Historic_Map_Info")
            self.assertEqual(hi._single_shp(nd).name, "GA_Wetlands.shp")
            _nwi_inbox(Path(d), "XX_Wetlands")
            with self.assertRaises(hi.SourceError):
                hi._single_shp(nd)

    def test_deleted_dbf_records_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            sq = cw([(0, 0), (1, 0), (1, 1), (0, 1)])
            shp = write_shapefile(Path(d), "w", [
                ([sq], {"ATTRIBUTE": "PFO1A", "WETLAND_TY": "Riverine"}),
                ([sq], {"ATTRIBUTE": "PUBHx", "WETLAND_TY": "Lake"})], NWI_DBF_FIELDS)
            dbf = shp.with_suffix(".dbf")
            raw = bytearray(dbf.read_bytes())
            head_len, rec_len = struct.unpack("<HH", raw[8:12])
            raw[head_len + rec_len] = ord("*")          # second record deleted
            dbf.write_bytes(bytes(raw))
            feats, *_ = hi.read_shapefile(shp, hi.NWI_FIELDS, lambda s: [(-1, -1, 2, 2)])
        self.assertEqual([f.attrs["ATTRIBUTE"] for f in feats], ["PFO1A"])


class AsOfTests(unittest.TestCase):
    """FW-20: no row is written without as-of values."""

    def test_write_refused_without_as_of(self):
        conn = FakeConn(answer_factory())
        data = hi.SourceData("nwi", [hi.Feature("1", {}, "{}", 4326)], as_of="  ",
                             covered=["fulton-a"])
        with self.assertRaises(hi.SourceError):
            hi.write_source(conn, data)
        self.assertEqual(conn.executed, [])

    def test_returned_row_without_as_of_aborts(self):
        def answer(sql, p):
            if sql == hi._SQL_UPSERT_NWI:
                return [("fulton-a", "")]
            return []
        data = hi.SourceData("nwi", [], as_of="dbf 2026-05-01", covered=["fulton-a"])
        with self.assertRaises(hi.SourceError):
            hi.write_source(FakeConn(answer), data)


class OutputTests(unittest.TestCase):
    """FW-21: counts only on the console, never ids, owners or attributes."""

    def test_sentinels_never_printed(self):
        conn = FakeConn(answer_factory())
        fields = NFHL_DBF_FIELDS + (("OWNER", "C", 30, 0), ("ADDRESS", "C", 30, 0))
        with tempfile.TemporaryDirectory() as d, enabled():
            inbox = Path(d)
            box = cw([(-84.7, 33.4), (-84.4, 33.4), (-84.4, 33.7), (-84.7, 33.7)])
            write_shapefile(inbox / "nfhl", "S_FLD_HAZ_AR", [([box], {
                "FLD_ZONE": "AE", "ZONE_SUBTY": "SECRET-SUBTYPE", "SFHA_TF": "T",
                "STATIC_BFE": 812.5, "DFIRM_ID": "13121C", "VERSION_ID": "1.1.1.0",
                "OWNER": "SECRET OWNER NAME", "ADDRESS": "123 SECRET ST"})], fields)
            write_shapefile(inbox / "nwi", "GA", [([box], {"ATTRIBUTE": "SECRETPFO",
                                                           "WETLAND_TY": "SECRET TYPE"})],
                            NWI_DBF_FIELDS)
            rc, out = run_tool(conn, mode="files", inbox=inbox, check_open_run=False)
        self.assertEqual(rc, hi.EXIT_OK, out)
        for s in ("SECRET", "fulton-", "812.5", "13121C"):
            self.assertNotIn(s, out)
        notes = "\n".join(p.read_text(encoding="utf-8")
                          for p in (REPO_ROOT / "reviews" / "26_flood_wetlands").glob("*.md"))
        self.assertNotIn("SECRET", notes)
        self.assertNotRegex(notes, r"fulton-\d{2}\s?\d{3}")

    def test_bands(self):
        self.assertIsNone(hi.band_of(0))
        self.assertEqual(hi.band_of(0.05), ">0-0.1%")
        self.assertEqual(hi.band_of(0.1), ">0-0.1%")
        self.assertEqual(hi.band_of(3), "1-5%")
        self.assertEqual(hi.band_of(20), "5-20%")
        self.assertEqual(hi.band_of(20.01), ">20%")


class StaticTests(unittest.TestCase):
    SRC = (REPO_ROOT / "hazards_import.py").read_text(encoding="utf-8")
    TREE = ast.parse(SRC)

    def test_never_imports_research_or_runner(self):
        mods = set()
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0])
        self.assertFalse(mods & {"research", "runner", "reporting", "site_fit", "screener"})
        self.assertIn("rules_scrape", mods)
        self.assertIn("zoning_import", mods)

    def test_sql_constants_only(self):
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "execute":
                self.assertIsInstance(node.args[0], (ast.Name, ast.Attribute), node.lineno)

    def test_no_mercator(self):
        self.assertNotRegex(self.SRC, r"3857|900913|102100")
        consts = [n.value for n in ast.walk(self.TREE) if isinstance(n, ast.Constant)
                  and isinstance(n.value, str)]
        doc = self.TREE.body[0].value.value
        for c in consts:
            if c != doc:
                self.assertNotRegex(c, r"Shape_Area|Shape_Length|\bACRES\b")

    def test_writes_only_parcel_hazards(self):
        for k, v in vars(hi).items():
            if k.startswith("_SQL_") and isinstance(v, str):
                for m in __import__("re").finditer(r"(?i)\b(INSERT INTO|(?<!DO )UPDATE|DELETE FROM)\s+(\w+)", v):
                    self.assertIn(m.group(2), ("parcel_hazards", "hazards_import_polys"), k)

    def test_exit_codes_documented(self):
        doc = hi.__doc__
        for code in ("0 imported", "1 refused", "2 no database", "3 a run has its",
                     "4 at least one source FAILED"):
            self.assertIn(code, doc)


if __name__ == "__main__":
    unittest.main()
