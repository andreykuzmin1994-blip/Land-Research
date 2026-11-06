"""adjacency_import.py: robots, fields asked, paging, budget, politeness, the
run guard, exit codes, counts-only output (reviews/27_site_fit_buffers).
Offline (SR-6): no network, no database; the PostGIS side is
tests/live_adjacency_check.py."""

from __future__ import annotations

import ast
import io
import json
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import tests  # noqa: F401  (suite guards)
import adjacency
import adjacency_import as ai
import prepare
import rules_scrape
import zoning
import zoning_import
from adjacency_helpers import enabled
from zoning_helpers import REPO_ROOT

SECRET_PID = "fulton-SECRET-ADJ-PID-1"
SECRET_OWNER = "SECRET OWNER LLC"
SECRET_ADDR = "123 SECRET MAIN ST"

OVERLAY_URL = "https://gis.example-city.test/arcgis/rest/services/Zoning/MapServer/0"
JOIN_URL = "https://gis.example-join.test/arcgis/rest/services/Zoning/FeatureServer/0"


def sources_file(d: Path, entries=None) -> Path:
    p = d / "sources.json"
    entries = entries if entries is not None else [
        {"key": "city_zoning", "name": "City zoning", "role": "zoning", "kind": "overlay",
         "jurisdictions": ["south_fulton"], "url": OVERLAY_URL, "zoning_field": "ZClass",
         "desc_field": "ZClassDesc"},
        {"key": "join_zoning", "name": "Join zoning", "role": "zoning", "kind": "join",
         "jurisdictions": ["fairburn"], "url": JOIN_URL, "id_field": "ParcelID",
         "zoning_field": "ZoningCurr"},
        {"key": "city_flu", "name": "City FLU", "role": "flu", "kind": "join",
         "jurisdictions": ["south_fulton"], "url": OVERLAY_URL, "id_field": "ParcelID",
         "flu_field": "FLU"}]
    p.write_text(json.dumps({"county_parcel_data": {"fulton_ga": {
        "service_url": "https://gismaps.example.test/MapServer", "parcel_layer_id": 11,
        "zoning_sources": entries}}}), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# HTTP fakes (test_hazards_import's shape)
# ---------------------------------------------------------------------------
class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Type", "application/json")

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]


def text(status, body, ctype="text/plain", **h):
    return Resp(status, body, {"Content-Type": ctype, **h})


def js(obj):
    return Resp(200, json.dumps(obj))


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Http:
    def __init__(self, handler, clock):
        self.handler, self.clock = handler, clock
        self.calls, self.times, self.headers = [], [], {}

    def get(self, url, **kw):
        self.calls.append(url)
        self.times.append((urlparse(url).hostname, self.clock.t))
        return self.handler(url)


def fetcher(handler):
    c = Clock()
    http = Http(handler, c)
    return ai.AdjacencyFetcher(http, clock=c.clock, sleep=c.sleep), http, c


RING = [[-84.5, 33.5], [-84.5, 33.51], [-84.49, 33.51], [-84.49, 33.5], [-84.5, 33.5]]


class Layer:
    """A fake zoning layer: ``n`` polygons, pages of ``page``; ``corrupt``
    rewrites one page; ``geometry`` False makes it a table (join layer)."""

    def __init__(self, n=250, page=100, field="ZClass", geometry=True, corrupt=None,
                 robots=None, ring=RING):
        self.n, self.page, self.field, self.geometry = n, page, field, geometry
        self.corrupt, self.robots, self.ring = corrupt, robots, ring
        self.pages = 0
        self.queries: list[dict] = []

    def __call__(self, url):
        u = urlparse(url)
        if u.path == "/robots.txt":
            return self.robots(url) if self.robots else text(404, "nf")
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not u.path.endswith("/query"):
            meta = {"name": "Zoning", "type": "Feature Layer", "objectIdField": "OBJECTID",
                    "fields": [{"name": "OBJECTID"}, {"name": self.field},
                               {"name": "OWNER"}, {"name": "SITEADDRESS"}],
                    "maxRecordCount": self.page,
                    "editingInfo": {"lastEditDate": 1767225600000}}
            if self.geometry:
                meta["geometryType"] = "esriGeometryPolygon"
            return js(meta)
        self.queries.append(q)
        if q.get("returnIdsOnly") == "true":
            return js({"objectIds": list(range(1, self.n + 1))})
        ids = [int(i) for i in q["objectIds"].split(",")]
        self.pages += 1
        feats = [{"attributes": {"OBJECTID": i, self.field: "R-3" if i % 2 else "M-1"},
                  "geometry": {"rings": [self.ring]}} for i in ids]
        body = {"features": feats}
        if self.corrupt:
            return self.corrupt(self.pages, body)
        return js(body)


def src(kind="overlay", url=OVERLAY_URL, field="ZClass", key="city_zoning"):
    return zoning_import.ZoningSource(key=key, name="x", role="zoning", kind=kind,
                                      jurisdictions=("south_fulton",), url=url, id_field=
                                      "ParcelID" if kind == "join" else None,
                                      zoning_field=field, desc_field=None, id_trim_last=False)


BOXES = [(-84.501, 33.499, -84.499, 33.501)]


# ---------------------------------------------------------------------------
class FetchTests(unittest.TestCase):
    """BUF-05: fields asked, paging all-or-nothing, the budget, join layers."""

    def test_out_fields_are_the_id_and_zoning_fields_only(self):
        layer = Layer(n=5)
        f, http, _ = fetcher(layer)
        d = ai.fetch_source(f, src(), BOXES)
        self.assertIsNone(d.failed)
        self.assertEqual(len(d.features), 5)
        pages = [q for q in layer.queries if "objectIds" in q]
        self.assertTrue(pages)
        for q in pages:
            self.assertEqual(q["outFields"], "OBJECTID,ZClass")
            self.assertEqual(q["outSR"], "4326")
        for url in http.calls:
            low = url.lower()
            for bad in ("owner", "address", "sale", "parcelid", "outfields=%2a", "outfields=*"):
                self.assertNotIn(bad, low, url)

    def test_short_page_fails_the_whole_source(self):
        def corrupt(n, body):
            if n == 2:
                body["features"] = body["features"][:-1]
            return js(body)
        f, _, _ = fetcher(Layer(n=250, corrupt=corrupt))
        d = ai.fetch_source(f, src(), BOXES)
        self.assertIn("truncated or short", d.failed)
        self.assertEqual(d.features, [])

    def test_transfer_limit_fails_the_source(self):
        def corrupt(n, body):
            body["exceededTransferLimit"] = True
            return js(body)
        f, _, _ = fetcher(Layer(n=5, corrupt=corrupt))
        self.assertIn("truncated", ai.fetch_source(f, src(), BOXES).failed)

    def test_missing_field_fails(self):
        def corrupt(n, body):
            for ft in body["features"]:
                ft["attributes"].pop("ZClass")
            return js(body)
        f, _, _ = fetcher(Layer(n=5, corrupt=corrupt))
        self.assertIn("lacks an expected field", ai.fetch_source(f, src(), BOXES).failed)
        f, _, _ = fetcher(Layer(n=5, field="OTHER"))
        self.assertIn("zoning field missing", ai.fetch_source(f, src(), BOXES).failed)

    def test_budget_exceeded_fails_the_source(self):
        layer = Layer(n=50_000, page=100)
        f, http, _ = fetcher(layer)
        d = ai.fetch_source(f, src(), BOXES)
        self.assertIn(f"request budget of {ai.MAX_REQUESTS_PER_SOURCE} exceeded", d.failed)
        self.assertEqual(d.features, [])
        self.assertLessEqual(len(f.data_calls()), ai.MAX_REQUESTS_PER_SOURCE)

    def test_mapserver_oid_field_by_type(self):
        self.assertEqual(ai._oid_field({"objectIdField": "OBJECTID"}), "OBJECTID")
        self.assertEqual(ai._oid_field({"fields": [{"name": "FID", "type": "esriFieldTypeOID"},
                                                   {"name": "Z", "type": "esriFieldTypeString"}]}),
                         "FID")
        self.assertIsNone(ai._oid_field({"fields": [{"name": "Z"}]}))
        self.assertIsNone(ai._oid_field({"fields": [{"name": "A", "type": "esriFieldTypeOID"},
                                                    {"name": "B", "type": "esriFieldTypeOID"}]}))

    def test_join_layer_without_geometry_is_skipped_unknown(self):
        f, _, _ = fetcher(Layer(n=5, geometry=False, field="ZoningCurr"))
        d = ai.fetch_source(f, src("join", JOIN_URL, "ZoningCurr", "join_zoning"), BOXES)
        self.assertIsNone(d.failed)
        self.assertIn("neighbours read as unknown", d.skipped)
        self.assertEqual(d.features, [])

    def test_join_layer_with_geometry_is_fetched_by_envelope(self):
        layer = Layer(n=3, field="ZoningCurr")
        f, _, _ = fetcher(layer)
        d = ai.fetch_source(f, src("join", JOIN_URL, "ZoningCurr", "join_zoning"), BOXES)
        self.assertEqual(len(d.features), 3)
        self.assertTrue(any(q.get("geometryType") == "esriGeometryEnvelope"
                            for q in layer.queries))
        self.assertFalse(any("where" in q and "ParcelID" in q["where"] for q in layer.queries))

    def test_outside_area_of_use_rejected(self):
        far = [[-100.0, 40.0], [-100.0, 40.01], [-99.99, 40.01], [-100.0, 40.0]]
        f, _, _ = fetcher(Layer(n=4, ring=far))
        d = ai.fetch_source(f, src(), BOXES)
        self.assertEqual(d.features, [])
        self.assertEqual(d.rejected_outside, 4)

    def test_feature_order_never_matters(self):
        def rev(n, body):
            body["features"] = body["features"][::-1]
            return js(body)
        f, _, _ = fetcher(Layer(n=7))
        a = ai.fetch_source(f, src(), BOXES)
        f, _, _ = fetcher(Layer(n=7, corrupt=rev))
        b = ai.fetch_source(f, src(), BOXES)
        self.assertEqual([x.oid for x in a.features], [x.oid for x in b.features])
        self.assertEqual([x.oid for x in a.features], sorted(x.oid for x in a.features))


    def test_unreadable_codes_are_no_polygon(self):
        """Review finding 10: 'UNKNOWN', 'TBD', 'N/A' are not districts; their
        area reads uncovered (adjacency unknown), counted with no district."""
        def placeholders(n, body):
            for ft, code in zip(body["features"], ("UNKNOWN", "TBD", "N/A", "R-3")):
                ft["attributes"]["ZClass"] = code
            return js(body)
        f, _, _ = fetcher(Layer(n=4, corrupt=placeholders))
        d = ai.fetch_source(f, src(), BOXES)
        self.assertEqual([x.district for x in d.features], ["R-3"])
        self.assertEqual(d.no_district, 3)


class RobotsTests(unittest.TestCase):
    """BUF-19: rules_scrape's robots policy unchanged; a refusal is a source
    failure with zero data requests."""

    def test_disallow_refuses_with_no_data_request(self):
        layer = Layer(robots=lambda u: text(200, "User-agent: *\nDisallow: /arcgis\n"))
        f, _, _ = fetcher(layer)
        d = ai.fetch_source(f, src(), BOXES)
        self.assertIn("robots.txt disallows", d.failed)
        self.assertEqual(f.data_calls(), [])

    def test_403_robots_denies_all(self):
        layer = Layer(robots=lambda u: text(403, "Invalid URL", "text/html"))
        f, _, _ = fetcher(layer)
        d = ai.fetch_source(f, src(), BOXES)
        self.assertIn("robots unavailable (HTTP 403)", d.failed)
        self.assertEqual(f.data_calls(), [])

    def test_ai_agent_token_disallow_refuses(self):
        tok = rules_scrape.AI_AGENT_TOKENS[0]
        layer = Layer(robots=lambda u: text(200, f"User-agent: {tok}\nDisallow: /\n"))
        f, _, _ = fetcher(layer)
        self.assertIn("disallows", ai.fetch_source(f, src(), BOXES).failed)

    def test_denylisted_hosts_never_contacted(self):
        for url in ("https://www.zoneomics.com/x/arcgis/rest/services/Z/MapServer/0",
                    "https://library.municode.com/ga/x/MapServer/0"):
            f, http, _ = fetcher(Layer())
            d = ai.fetch_source(f, src(url=url), BOXES)
            self.assertIsNotNone(d.failed, url)
            self.assertEqual(http.calls, [], url)

    def test_user_agent_generic(self):
        self.assertNotIn("@", ai.USER_AGENT)
        self.assertTrue(ai.USER_AGENT.startswith(ai.UA_TOKEN))


class PolitenessTests(unittest.TestCase):
    """SR-11 on a fake clock."""

    def test_one_request_per_second_per_host(self):
        f, http, _ = fetcher(Layer(n=450))
        ai.fetch_source(f, src(), BOXES)
        last: dict = {}
        self.assertGreater(len(http.times), 5)
        for host, t in http.times:
            if host in last:
                self.assertGreaterEqual(t - last[host], 1.0 - 1e-9)
            last[host] = t

    def test_retry_after_honoured_and_capped(self):
        n = {"k": 0}

        def handler(url):
            if url.endswith("/robots.txt"):
                return text(404, "")
            n["k"] += 1
            return text(429, "slow", **{"Retry-After": "5"}) if n["k"] == 1 else js({"ok": 1})
        f, _, c = fetcher(handler)
        self.assertEqual(f.get_json("https://example.test/x", {}), {"ok": 1})
        self.assertIn(5.0, c.sleeps)

        def slow(url):
            if url.endswith("/robots.txt"):
                return text(404, "")
            return text(429, "slow", **{"Retry-After": "999"})
        f, http, c = fetcher(slow)
        with self.assertRaises(ai.SourceError):        # above the cap: give up, never wait
            f.get_json("https://example.test/x", {})
        self.assertTrue(all(s <= rules_scrape.RETRY_AFTER_CAP_S for s in c.sleeps))

        def busy(url):
            if url.endswith("/robots.txt"):
                return text(404, "")
            return text(503, "busy")
        f, http, c = fetcher(busy)
        with self.assertRaises(ai.SourceError):        # bounded retries
            f.get_json("https://example.test/x", {})
        self.assertEqual(len(f.data_calls()), rules_scrape.MAX_RETRIES + 1)


# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=()):
        self.conn.executed.append((sql, tuple(params or ())))
        if self.conn.fail_on == sql:
            raise RuntimeError("injected")
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
    def __init__(self, answer, fail_on=None):
        self.answer, self.executed, self.commits, self.fail_on = answer, [], 0, fail_on
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


SF_STORED = zoning.encode_stored("south_fulton", "city_zoning", "M-1", False, SECRET_ADDR)
PARCELS = [(SECRET_PID, -84.5, 33.5, -84.501, 33.499, -84.499, 33.501, False, *SF_STORED)]


def answer_factory(*, mirror_baseline=False, table=True, parcels=None, changed=None):
    parcels = PARCELS if parcels is None else parcels

    def answer(sql, p):
        if sql == zoning_import._SQL_MIRROR_EXISTS:
            return [(mirror_baseline,)]
        if sql == zoning_import._SQL_MIRROR_BASELINE_FOR_TAG:
            return [(1,)]
        if sql == zoning_import._SQL_MIRROR_LATEST_RUN:
            return [("t1", "keep", True)]
        if sql == ai._SQL_TABLE_EXISTS:
            return [(table,)]
        if sql == ai._SQL_LIST_PARCELS:
            return parcels
        if sql == ai._SQL_COUNT_PARCELS:
            return [(len(parcels),)]
        if sql == ai._SQL_CHANGED:
            return [(x,) for x in (changed if changed is not None else [r[0] for r in parcels])]
        if sql == ai._SQL_SRID_CHECK:
            return [(0,)]
        if sql == ai._SQL_AMBIGUOUS:
            return []
        if sql == ai._SQL_SLIVERS:
            return [(0, 0)]
        if sql == ai._SQL_NEW_SUMMARY:
            return [(SECRET_PID, "zoned", "R-3", "south_fulton"),
                    (SECRET_PID, "uncovered", None, None)]
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
        rc = ai.run(**kw)
    return rc, buf.getvalue()


def ok_handler(url):
    host = urlparse(url).hostname
    if host == "gis.example-join.test":
        return Layer(n=2, field="ZoningCurr", geometry=False)(url)
    return Layer(n=3)(url)


class RunTests(unittest.TestCase):
    """BUF-12 / BUF-22: exit codes 0-4, the run guard before any request."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sources = sources_file(self.tmp)
        self.nolog = self.tmp / "experiment_log.tsv"

    def test_exit_1_gate_off(self):
        conn = FakeConn(answer_factory())
        rc, out = run_tool(conn, sources_path=self.sources, check_open_run=False,
                           log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_REFUSED)
        self.assertIn("01_prepare_mutation_proposal.md", out)
        self.assertEqual(conn.executed, [])

    def test_exit_1_no_zoning_sources_in_sources_json(self):
        conn = FakeConn(answer_factory())
        with enabled():
            rc, out = run_tool(conn, sources_path=REPO_ROOT / "sources.json",
                               check_open_run=False, log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_REFUSED)
        self.assertIn("zoning_sources", out)

    def test_exit_1_table_missing(self):
        conn = FakeConn(answer_factory(table=False))
        f, http, _ = fetcher(ok_handler)
        with enabled():
            rc, _ = run_tool(conn, sources_path=self.sources, fetcher=f, check_open_run=False,
                             log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_REFUSED)
        self.assertEqual(http.calls, [])

    def test_exit_1_schema_problem(self):
        conn = FakeConn(answer_factory())
        with enabled("CREATE TABLE parcel_adjacency (parcel_id TEXT)"):
            rc, out = run_tool(conn, sources_path=self.sources, check_open_run=False,
                               log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_REFUSED)
        self.assertEqual(conn.executed, [])

    def test_exit_2_no_dsn(self):
        buf = io.StringIO()
        with enabled(), mock.patch.object(prepare, "dsn_available", return_value=False), \
                redirect_stdout(buf):
            rc = ai.run(sources_path=self.sources, check_open_run=False, log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_DB)

    def test_exit_2_database_error_writes_nothing(self):
        conn = FakeConn(answer_factory(), fail_on=ai._SQL_INSERT_ROWS)
        f, _, _ = fetcher(ok_handler)
        with enabled():
            rc, out = run_tool(conn, sources_path=self.sources, fetcher=f,
                               check_open_run=False, log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_DB)
        self.assertEqual(conn.commits, 0)
        self.assertIn("nothing written", out)

    def test_exit_3_tsv_baseline_no_requests(self):
        log = self.tmp / "experiment_log.tsv"
        log.write_text("commit\tmetric\tconfidence\tapi_calls\twall_clock_min\tstatus\t"
                       "description\nabc\t1\t0.5\t1\t1\tbaseline\trun=t1 baseline\n")
        conn = FakeConn(answer_factory())
        f, http, _ = fetcher(ok_handler)
        with enabled(), mock.patch.object(prepare, "current_run_tag", return_value="t1"):
            rc, _ = run_tool(conn, sources_path=self.sources, fetcher=f, log_path=log)
        self.assertEqual(rc, ai.EXIT_RUN_OPEN)
        self.assertEqual((http.calls, conn.executed), ([], []))

    def test_exit_3_mirror_baseline_lost_tsv_no_requests(self):
        conn = FakeConn(answer_factory(mirror_baseline=True))
        f, http, _ = fetcher(ok_handler)
        with enabled(), mock.patch.object(prepare, "current_run_tag", return_value="t1"):
            rc, _ = run_tool(conn, sources_path=self.sources, fetcher=f,
                             log_path=Path("/nonexistent/experiment_log.tsv"))
        self.assertEqual(rc, ai.EXIT_RUN_OPEN)
        self.assertEqual(http.calls, [])
        self.assertFalse(any(s == ai._SQL_LIST_PARCELS for s, _ in conn.executed))

    def test_exit_3_off_branch_open_run(self):
        conn = FakeConn(answer_factory(mirror_baseline=True))
        f, http, _ = fetcher(ok_handler)
        with enabled(), mock.patch.object(prepare, "current_run_tag", return_value=None):
            rc, _ = run_tool(conn, sources_path=self.sources, fetcher=f, log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_RUN_OPEN)
        self.assertEqual(http.calls, [])

    def test_exit_0_one_transaction_and_flu_never_fetched(self):
        conn = FakeConn(answer_factory())
        f, http, _ = fetcher(ok_handler)
        with enabled():
            rc, out = run_tool(conn, sources_path=self.sources, fetcher=f,
                               check_open_run=False, log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_OK, out)
        self.assertEqual(conn.commits, 1)
        sql = [s for s, _ in conn.executed]
        self.assertIn(ai._SQL_INSERT_ROWS, sql)
        self.assertIn("source join_zoning: skipped", out)
        self.assertEqual(sum("city_flu" in u for u in http.calls), 0)
        # outFields of every page are the id + zoning fields
        for u in f.data_calls():
            q = {k: v[0] for k, v in parse_qs(urlparse(u).query).items()}
            if "objectIds" in q:
                self.assertEqual(q["outFields"], "OBJECTID,ZClass")

    def test_exit_4_failed_source_named_last(self):
        def handler(url):
            if urlparse(url).hostname == "gis.example-join.test":
                return Layer(robots=lambda u: text(403, "no", "text/html"))(url)
            return Layer(n=3)(url)
        conn = FakeConn(answer_factory())
        f, _, _ = fetcher(handler)
        with enabled():
            rc, out = run_tool(conn, sources_path=self.sources, fetcher=f,
                               check_open_run=False, log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_SOURCE_FAILED)
        last = out.strip().splitlines()[-1]
        self.assertIn("join_zoning", last)
        self.assertIn("Exit 4", last)
        self.assertEqual(conn.commits, 1)

    def test_rerun_with_no_change_writes_no_rows(self):
        conn = FakeConn(answer_factory(changed=[]))
        f, _, _ = fetcher(ok_handler)
        with enabled():
            rc, out = run_tool(conn, sources_path=self.sources, fetcher=f,
                               check_open_run=False, log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_OK)
        sql = [s for s, _ in conn.executed]
        self.assertNotIn(ai._SQL_DELETE_ROWS, sql)
        self.assertNotIn(ai._SQL_INSERT_ROWS, sql)
        self.assertIn("0 changed, 1 unchanged", out)

    def test_outside_area_of_use_parcels_not_imported(self):
        far = [("fulton-FAR", -100.0, 40.0, -100.01, 39.99, -99.99, 40.01, False, None, None)]
        conn = FakeConn(answer_factory(parcels=far))
        f, http, _ = fetcher(ok_handler)
        with enabled():
            rc, out = run_tool(conn, sources_path=self.sources, fetcher=f,
                               check_open_run=False, log_path=self.nolog)
        self.assertEqual(rc, ai.EXIT_OK)
        self.assertIn("1 outside EPSG:2240's area of use", out)
        self.assertEqual(http.calls, [])


class OutputTests(unittest.TestCase):
    """BUF-20: counts only, never an id or an attribute."""

    def test_sentinels_never_printed(self):
        tmp = Path(tempfile.mkdtemp())
        conn = FakeConn(answer_factory())
        f, _, _ = fetcher(ok_handler)
        with enabled():
            rc, out = run_tool(conn, sources_path=sources_file(tmp), fetcher=f,
                               check_open_run=False, log_path=tmp / "x.tsv")
        self.assertEqual(rc, ai.EXIT_OK)
        for s in (SECRET_PID, SECRET_OWNER, SECRET_ADDR, "R-3", "M-1"):
            self.assertNotIn(s, out)
        self.assertIn("jurisdiction south_fulton: 1 parcels", out)

    def test_projection_report_counts_only(self):
        before = {SECRET_PID: ("ok", 160_000, "south_fulton"),
                  "fulton-SECRET-2": ("ok", 120_000, "south_fulton"),
                  "fulton-SECRET-3": ("ok", 200_000, "fairburn")}
        after = {SECRET_PID: ("ok", 140_000, "south_fulton"),
                 "fulton-SECRET-2": ("error", None, "south_fulton"),
                 "fulton-SECRET-3": ("ok", 200_000, "fairburn")}
        lines = ai.projection_report(before, after)
        text_ = "\n".join(lines)
        self.assertNotIn("SECRET", text_)
        self.assertIn("jurisdiction south_fulton: 2 parcels; 2 with a lower max building SF; "
                      "1 dropped below 150K SF; 0 gained SF", text_)
        self.assertIn("ok -> error 1", text_)
        self.assertIn("jurisdiction fairburn: 1 parcels; 0 with a lower", text_)

    def test_summarise_classes_through_the_table_only(self):
        stored = {SECRET_PID: SF_STORED}
        summary = [(SECRET_PID, "zoned", "R-3", "south_fulton"),
                   (SECRET_PID, "zoned", "M1", "south_fulton")]
        lines = ai.summarise(stored, summary)
        self.assertIn("0 residential and 0 agricultural (human class table); 1 with an "
                      "unclassified neighbour", lines[0])
        from adjacency_helpers import with_classes
        from zoning_helpers import patch_rules, verified
        with patch_rules(verified("south_fulton",
                                  mutate=with_classes("south_fulton", residential=["R-*"]))):
            lines = ai.summarise(stored, summary)
        self.assertIn("1 residential", lines[0])


class AmbiguityTests(unittest.TestCase):
    """Review finding 8: a part facing the parcel's OWN district polygon (an
    overhang) and a neighbour is not two neighbour districts."""

    def test_own_overhang_counted_apart(self):
        stored = {"p1": SF_STORED, "p2": SF_STORED, "p3": SF_STORED}
        pairs = [("p1", "M-1", "south_fulton", "R-3", "south_fulton"),     # own overhang
                 ("p2", "M1", "south_fulton", "R-3", "south_fulton"),      # own, spelled M1
                 ("p3", "R-3", "south_fulton", "R-4", "south_fulton"),     # two neighbours
                 ("p3", "M-1", "south_fulton", "R-4", "south_fulton")]
        self.assertEqual(ai.ambiguity_counts(stored, pairs), (1, 2))
        self.assertEqual(ai.ambiguity_counts({}, pairs[2:3]), (1, 0))

    def test_console_line_names_both_counts(self):
        self.assertIn("own district polygon overhangs", (REPO_ROOT / "adjacency_import.py")
                      .read_text(encoding="utf-8"))


class StaticTests(unittest.TestCase):
    """BUF-18 / BUF-22: no DDL outside prepare.py, layering, SQL constants."""

    SRC = (REPO_ROOT / "adjacency_import.py").read_text(encoding="utf-8")

    def test_imports(self):
        tree = ast.parse(self.SRC)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0])
        for bad in ("research", "runner", "site_fit", "screener", "reporting", "cli"):
            self.assertNotIn(bad, mods)
        self.assertLessEqual(mods - {"__future__", "argparse", "json", "sys", "time",
                                     "dataclasses", "datetime", "pathlib", "typing", "urllib"},
                             {"adjacency", "hazards", "prepare", "rules_scrape", "zoning",
                              "zoning_import"})

    def test_no_ddl_on_parcel_adjacency_outside_prepare(self):
        import re
        pat = re.compile(r"\b(CREATE|ALTER|DROP)\b[^;\"]*\bparcel_adjacency\b", re.I)
        for path in sorted(REPO_ROOT.glob("*.py")):
            if path.name == "prepare.py":
                continue
            text_ = path.read_text(encoding="utf-8")
            if path.name == "adjacency.py":
                # The proposal text itself (never executed by the module).
                text_ = text_.replace(adjacency.PROPOSED_DDL, "")
                for s in adjacency.PROPOSED_INDEXES:
                    text_ = text_.replace(s, "")
            self.assertIsNone(pat.search(text_), path.name)
        tree = ast.parse(self.SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotIn("CREATE TABLE IF NOT EXISTS parcel_adjacency", node.value)
                self.assertNotIn("ALTER TABLE parcels", node.value)

    def test_temp_tables_only_and_no_mercator(self):
        for name in dir(ai):
            if name.startswith("_SQL_CREATE"):
                self.assertIn("CREATE TEMP TABLE", getattr(ai, name), name)
        self.assertNotRegex(self.SRC, r"3857|900913|102100")
        self.assertIn("'side=right endcap=flat'", ai._SQL_INSERT_EDGES)
        self.assertIn("ST_ForcePolygonCCW", ai._SQL_INSERT_EDGES)

    def test_tolerances_come_from_the_protected_module(self):
        self.assertNotIn("ABUT_TOL_FT =", self.SRC)
        self.assertNotIn("MIN_SEGMENT_LEN_FT =", self.SRC)
        self.assertNotIn("MISALIGNMENT_ALLOWANCE_FT =", self.SRC)
        self.assertIn("a.MISALIGNMENT_ALLOWANCE_FT", self.SRC)
        self.assertNotRegex(self.SRC, r"add_argument\(\"--(tol|tolerance|min)")

    def test_slivers_are_dropped_per_part_and_reach_is_required(self):
        """Review findings 4 and 7: the cover is opened (every part under the
        sliver width dropped, not only a cover thin everywhere), and a zoned
        part must lie within the reach of its edge."""
        cover = ai._SQL_INSERT_COVER
        self.assertIn("ST_Buffer(ST_Buffer(c, -0.5 * %s::float8), 0.5 * %s::float8)", cover)
        self.assertEqual(cover.count("%s"), 3)
        self.assertIn("JOIN adjacency_import_reach r ON r.eid = e.eid AND r.src = c.src",
                      ai._SQL_INSERT_ZONED)
        self.assertIn("r.reach", ai._SQL_INSERT_ZONED)
        self.assertIn("ST_DWithin(d.geom, e.e, 0.01)", ai._SQL_INSERT_REACH)

    def test_never_writes_files(self):
        writers = {"write_text", "write_bytes", "dump", "replace", "rename", "unlink"}
        tree = ast.parse(self.SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in writers:
                self.fail(f"adjacency_import.py:{node.lineno} writes a file")


if __name__ == "__main__":
    unittest.main()
