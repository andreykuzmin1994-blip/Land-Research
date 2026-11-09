"""zoning_import.py, offline (SR-6): fakes for HTTP and the database.

Risk ids (Z-xx) refer to reviews/21_zoning/02_risk_review.md.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import re
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import parse_qsl, urlsplit

import connector_harness
import prepare
import zoning
import rules_scrape
import zoning_import as zi
from zoning_helpers import REPO_ROOT

SNIPPET = REPO_ROOT / "reviews" / "21_zoning" / "sources_json_snippet.json"
LAYER = "https://parcels.example.test/MapServer/11"
OWNER = "SECRET OWNER LLC"
SAFE_LINE = re.compile(r"^[A-Za-z0-9 _:;,.()%'/=+\-]*$")


def _sources(entries: list[dict[str, Any]] | None = None, *, drop_key: bool = False) -> dict:
    fulton = {"service_url": "https://parcels.example.test/MapServer", "parcel_layer_id": 11,
              "field_mapping": {"parcel_id": "ParcelID"}}
    if not drop_key:
        fulton["zoning_sources"] = entries if entries is not None else _entries()
    return {"county_parcel_data": {"fulton_ga": fulton}}


def _entries() -> list[dict[str, Any]]:
    return [
        {"key": "sf_zoning", "name": "SF zoning", "role": "zoning", "kind": "overlay",
         "jurisdictions": ["south_fulton", "fid"], "url": "https://sf.example.test/Zoning/0",
         "zoning_field": "ZClass", "desc_field": "ZClassDesc"},
        {"key": "l34", "name": "Layer 34", "role": "zoning", "kind": "overlay",
         "jurisdictions": ["south_fulton", "fid"], "url": "https://parcels.example.test/MapServer/34",
         "zoning_field": "ZClass"},
        {"key": "fb", "name": "Fairburn", "role": "zoning", "kind": "join",
         "jurisdictions": ["fairburn"], "url": "https://fb.example.test/0",
         "id_field": "ParcelID", "zoning_field": "ZoningCurr"},
        {"key": "fb_flu", "name": "Fairburn FLU", "role": "flu", "kind": "join",
         "jurisdictions": ["fairburn"], "url": "https://fb.example.test/0",
         "id_field": "ParcelID", "flu_field": "FLU"},
    ]


def _write_sources(td: str, payload: dict) -> Path:
    p = Path(td) / "sources.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# --- HTTP fakes -------------------------------------------------------------
class _Resp:
    """One answer, readable both ways: ``json()`` (the 4fe8080 session) and
    ``content`` + ``headers`` (rules_scrape.PoliteFetcher reads the bytes)."""

    def __init__(self, body: Any = None, status: int = 200, headers: dict | None = None) -> None:
        self._body, self.status_code = body, status
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Type", "application/json")

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    @property
    def content(self) -> bytes:
        if isinstance(self._body, bytes):
            return self._body
        if isinstance(self._body, Exception):
            return b"this is not json"
        return b"" if self._body is None else json.dumps(self._body).encode("utf-8")


ROBOTS_404 = _Resp(b"not found", 404, {"Content-Type": "text/plain"})


class FakeHttp:
    """Serves the layers of the fixture world; records every request.

    Adapter (reviews/32, ZI-04): the 4fe8080 session calls
    ``get(url, params=..., timeout=...)``; PoliteFetcher calls
    ``get(full_url, timeout=, allow_redirects=False, stream=True, headers=)``.
    Both reach ``World.answer`` with the same (base URL, params dict); a
    query string is parsed back into that dict. robots.txt is answered here
    (404 unless ``robots`` names the host) and never reaches the world.
    ``calls`` = data requests as (base URL, params); ``raw`` = every URL
    requested, robots.txt included; ``headers`` = the per-request headers."""

    def __init__(self, world: "World", robots: dict[str, Any] | None = None) -> None:
        self.world = world
        self.robots = dict(robots or {})
        self.calls: list[tuple[str, dict]] = []
        self.raw: list[str] = []
        self.headers_seen: list[dict] = []
        self.kwargs: list[dict] = []

    override: Any = None    # callable(url) -> answer or None, before the world

    def get(self, url: str, params: dict | None = None, timeout: Any = None, **kw: Any) -> Any:
        self.raw.append(url)
        self.headers_seen.append(dict(kw.get("headers") or {}))
        self.kwargs.append(dict(kw, timeout=timeout))
        if self.override is not None:
            r = self.override(url)
            if r is not None:
                if isinstance(r, Exception):
                    raise r
                return r
        base = url
        if params is None and "?" in url:
            base, _q, query = url.partition("?")
            params = dict(parse_qsl(query, keep_blank_values=True))
        parts = urlsplit(base)
        if parts.path == "/robots.txt":
            r = self.robots.get(parts.hostname, ROBOTS_404)
            if isinstance(r, Exception):
                raise r
            return r(url) if callable(r) else r
        params = dict(params or {})
        self.calls.append((base, params))
        return self.world.answer(base, params)

    def robots_calls(self) -> list[str]:
        return [u for u in self.raw if urlsplit(u).path == "/robots.txt"]


def _ring(x0, y0, x1, y1):
    # Esri outer ring: clockwise.
    return [[x0, y0], [x0, y1], [x1, y1], [x1, y0], [x0, y0]]


class World:
    """Fixture sources: layer 11 TaxDist, an overlay layer, a join layer."""

    def __init__(self) -> None:
        self.taxdist = {"P1": "55", "P2": "55", "P3": "25", "P4": "15", "P5": "70",
                        "07 1500  0001": "25"}
        self.sf_polys = [  # oid -> (zoning, desc, rings)
            (1, "M-1", "Light Industrial", [_ring(-84.6, 33.5, -84.5, 33.6)]),
            (2, "AG-1", "Agricultural", [_ring(-84.5, 33.5, -84.4, 33.6)]),
        ]
        self.fb_rows = [(10, "P3", "M-2"), (11, "07 1500 0001", "M-1")]
        self.fail: dict[str, Any] = {}
        self.truncate_ids: set[str] = set()
        self.short_pages: set[str] = set()

    def answer(self, url: str, params: dict) -> _Resp:
        host = url.split("/")[2]
        if host in self.fail:
            return self.fail[host]
        if url == LAYER + "/query":
            ids = re.findall(r"'([^']*)'", params["where"])
            feats = [{"attributes": {"ParcelID": i, "TaxDist": self.taxdist[i], "Owner": OWNER,
                                     "OwnerAddr1": "1 SECRET WAY"}}
                     for i in ids if i in self.taxdist]
            return _Resp({"features": feats})
        base = url[:-len("/query")] if url.endswith("/query") else url
        if params.get("f") == "pjson":
            if "sf.example" in url or "MapServer/34" in url:
                return _Resp({"fields": [{"name": "ZClass"}, {"name": "ZClassDesc"}],
                              "geometryType": "esriGeometryPolygon", "maxRecordCount": 1000})
            return _Resp({"fields": [{"name": "ParcelID"}, {"name": "ZoningCurr"}, {"name": "FLU"}],
                          "geometryType": "esriGeometryPolygon", "maxRecordCount": 1000})
        rows = self._rows(base)
        if params.get("returnIdsOnly") == "true":
            if host in self.truncate_ids:
                return _Resp({"objectIds": [r[0] for r in rows], "exceededTransferLimit": True})
            return _Resp({"objectIds": [r[0] for r in rows]})
        want = {int(o) for o in params["objectIds"].split(",")}
        feats = [f for o, f in self._features(base) if o in want]
        if host in self.short_pages:
            feats = feats[:-1]
        return _Resp({"features": feats})

    def _rows(self, base: str) -> list[tuple]:
        if "sf.example" in base:
            return self.sf_polys
        if "MapServer/34" in base:
            return []
        return self.fb_rows

    def _features(self, base: str) -> list[tuple[int, dict]]:
        if "sf.example" in base:
            return [(o, {"attributes": {"ZClass": z, "ZClassDesc": d, "Owner": OWNER},
                         "geometry": {"rings": r}}) for o, z, d, r in self.sf_polys]
        return [(o, {"attributes": {"ParcelID": pid, "ZoningCurr": z, "FLU": "Industrial"}})
                for o, pid, z in self.fb_rows]


def _session(world: World, *, ref: bool = False, robots: dict | None = None,
             override: Any = None, **kw: Any) -> tuple[Any, FakeHttp]:
    """The session under test, or (``ref``) the verbatim 4fe8080 session
    behind an adapter that gives it the one attribute run() now sets."""
    http = FakeHttp(world, robots)
    http.override = override
    if ref:
        import zoning_session_4fe8080 as ref_mod
        s = ref_mod.ZoningSession(http=http, clock=lambda: 0.0, sleep=lambda x: None)
        s.max_requests = 0            # adapter: run() raises it; the reference never reads it
        return s, http
    return zi.ZoningSession(http=http, clock=lambda: 0.0, sleep=lambda x: None, **kw), http


# --- DB fake ------------------------------------------------------------------
class _Cur:
    def __init__(self, conn: "StoreConn") -> None:
        self.conn, self.rows = conn, []

    def execute(self, sql: str, params: Any = ()) -> None:
        params = tuple(params or ())
        self.conn.executed.append((sql, params))
        self.rows = self.conn.run(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


class StoreConn:
    """parcels in memory; overlay results scripted per source key."""

    def __init__(self, parcels: dict[str, tuple[Any, Any]], *, mirror: list | None = None,
                 overlay: dict[str, list[tuple]] | None = None) -> None:
        self.parcels = dict(parcels)
        self.committed = dict(parcels)
        self.mirror = mirror        # None = no table; else [(run_tag, status)]
        self.overlay = overlay or {}
        self.executed: list[tuple[str, tuple]] = []
        self.commits = 0
        self.inserted_polys: list[tuple] = []

    def run(self, sql: str, params: tuple) -> list:
        if sql == zi._SQL_MIRROR_EXISTS:
            return [(self.mirror is not None,)]
        if sql == zi._SQL_MIRROR_BASELINE_FOR_TAG:
            return [(sum(1 for t, s in self.mirror if t == params[0] and s == "baseline"),)]
        if sql == zi._SQL_MIRROR_LATEST_RUN:
            if not self.mirror:
                return []
            t, s = self.mirror[-1]
            return [(t, s, any(tt == t and ss == "baseline" for tt, ss in self.mirror))]
        if sql == zi._SQL_COUNT_PARCELS:
            return [(len(self.parcels),)]
        if sql == zi._SQL_LIST_PARCELS:
            return [(p, z, d) for p, (z, d) in sorted(self.parcels.items())]
        if sql == zi._SQL_PARCEL_BBOXES:
            return [(p, -84.55, 33.55, -84.54, 33.56) for p in params[0] if p in self.parcels]
        if sql == zi._SQL_INSERT_POLY:
            self.inserted_polys.append(params)
            return []
        if sql == zi._SQL_OVERLAY:
            return [r for r in self.overlay.get(params[1], []) if r[0] in params[0]]
        if sql == zi._SQL_UPDATE_ZONING:
            self.parcels[params[2]] = (params[0], params[1])
            return []
        return []

    def cursor(self):
        return _Cur(self)

    @contextmanager
    def transaction(self):
        yield self

    def commit(self):
        self.commits += 1
        self.committed = dict(self.parcels)

    def rollback(self):
        self.parcels = dict(self.committed)


def _run(conn: StoreConn, world: World, *, sources: dict | None = None, tag: str | None = "t1",
         tsv: str | None = None, check_open_run: bool = True,
         **session_kw: Any) -> tuple[int, str, FakeHttp]:
    session, http = _session(world, **session_kw)
    code, out = _drive(conn, session, sources=sources, tag=tag, tsv=tsv,
                       check_open_run=check_open_run)
    return code, out, http


def _drive(conn: StoreConn, session: Any, *, sources: dict | None = None, tag: str | None = "t1",
           tsv: str | None = None, check_open_run: bool = True) -> tuple[int, str]:
    @contextmanager
    def _gc():
        yield conn

    out = io.StringIO()
    with tempfile.TemporaryDirectory() as td:
        sp = _write_sources(td, sources if sources is not None else _sources())
        log = Path(td) / "experiment_log.tsv"
        if tsv is not None:
            log.write_text(tsv, encoding="utf-8")
        with mock.patch.object(prepare, "current_run_tag", return_value=tag), \
                mock.patch.object(prepare, "dsn_available", return_value=True), \
                mock.patch.object(prepare, "get_connection", _gc), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = zi.run(sources_path=sp, session=session, today=date(2026, 11, 5),
                          check_open_run=check_open_run, log_path=log)
    return code, out.getvalue()


def _parcels() -> dict[str, tuple[Any, Any]]:
    return {f"fulton-{p}": (None, None) for p in ("P1", "P2", "P3", "P4", "P5", "07 1500  0001")}


def _sf_overlay() -> dict[str, list[tuple]]:
    # P1 mostly M-1; P2 split 55/45; P5 (FID) M-1A-free sliver only.
    return {"sf_zoning": [("fulton-P1", "M-1", "Light Industrial", 900.0, 1000.0),
                          ("fulton-P1", "AG-1", "Agricultural", 100.0, 1000.0),
                          ("fulton-P2", "AG-1", "Agricultural", 550.0, 1000.0),
                          ("fulton-P2", "M-1", "Light Industrial", 450.0, 1000.0),
                          ("fulton-P5", "M-2", "Heavy", 50.0, 1000.0)]}


def _assert_counts_only(test: unittest.TestCase, text: str) -> None:
    for line in text.splitlines():
        test.assertRegex(line, SAFE_LINE, line)
    for secret in (OWNER, "SECRET WAY", "P1", "P3", "07 1500", "fulton-"):
        test.assertNotIn(secret, text)


# ---------------------------------------------------------------------------
class SnippetAndConfigTests(unittest.TestCase):
    """Z-17."""

    def test_snippet_passes_the_validator_and_names_its_key_path(self):
        snip = json.loads(SNIPPET.read_text(encoding="utf-8"))
        self.assertEqual(snip["key_path"], zi.SOURCES_KEY_PATH)
        srcs = zi.validate_zoning_sources(snip["zoning_sources"])
        self.assertEqual([s.key for s in srcs if s.role == "zoning"][:2],
                         ["south_fulton_zoning", "fulton_layer34_zoning"])   # precedence
        for s in srcs:
            if s.role == "flu":
                self.assertIsNone(s.zoning_field, s.key)

    def test_snippet_matches_the_screener_sources_exactly(self):
        # Findings 6/7: same URL, kind, fields and jurisdictions as
        # screener/fulton.py ZONING_SOURCES (the city of South Fulton layer
        # does NOT serve FID; Fulton layer 34 does).
        from screener import fulton
        name_to_key = {fulton.JURISDICTIONS[t]: k for t, k in zi.TAXDIST_TO_JURISDICTION.items()}
        snip = json.loads(SNIPPET.read_text(encoding="utf-8"))["zoning_sources"]
        by_url: dict[str, list] = {}
        for e in snip:
            by_url.setdefault(e["url"], []).append(e)
        self.assertEqual(set(by_url), {s["url"] for s in fulton.ZONING_SOURCES})
        for src in fulton.ZONING_SOURCES:
            want = sorted(name_to_key[n] for n in src["jurisdictions"])
            for e in by_url[src["url"]]:
                self.assertEqual(sorted(e["jurisdictions"]), want, e["key"])
                self.assertEqual(e["kind"], src["kind"], e["key"])
                for f in ("id_field", "id_trim_last", "desc_field"):
                    if e["role"] == "zoning" or f == "id_field":
                        self.assertEqual(e.get(f) or None, src.get(f) or None, (e["key"], f))
                field_name = "zoning_field" if e["role"] == "zoning" else "flu_field"
                self.assertEqual(e[field_name], src[field_name], e["key"])
        sf = next(e for e in snip if e["key"] == "south_fulton_zoning")
        self.assertEqual(sf["jurisdictions"], ["south_fulton"])

    def test_absent_key_exits_1(self):
        code, out, http = _run(StoreConn(_parcels()), World(), sources=_sources(drop_key=True))
        self.assertEqual(code, 1)
        self.assertIn("county_parcel_data.fulton_ga.zoning_sources", out)
        self.assertEqual(http.calls, [])

    def test_malformed_entry_exits_1_naming_the_index_only(self):
        for mutate in (lambda e: e.pop("kind"), lambda e: e.__setitem__("url", "http://x.test/0"),
                       lambda e: e.pop("zoning_field"), lambda e: e.__setitem__("jurisdictions", ["Fairburn"]),
                       lambda e: e.__setitem__("zoning_field", "Z'; DROP")):
            entries = _entries()
            mutate(entries[2])
            code, out, _ = _run(StoreConn(_parcels()), World(), sources=_sources(entries))
            self.assertEqual(code, 1)
            self.assertIn("zoning_sources[2] is malformed", out)
            self.assertNotIn("fb.example", out)

    def test_snippet_applied_creates_no_extra_connector(self):
        snip = json.loads(SNIPPET.read_text(encoding="utf-8"))
        reg = json.loads((REPO_ROOT / "sources.json").read_text(encoding="utf-8"))
        before = set(connector_harness.load_registry(REPO_ROOT / "sources.json"))
        reg["county_parcel_data"]["fulton_ga"]["zoning_sources"] = snip["zoning_sources"]
        with tempfile.TemporaryDirectory() as td:
            p = _write_sources(td, reg)
            after = connector_harness.load_registry(p)
            layer, srcs = zi.load_config(p)
        self.assertEqual(set(after), before)
        self.assertTrue(layer.endswith("/MapServer/11"))
        self.assertEqual(len(srcs), len(snip["zoning_sources"]))


class RunStateGuardTests(unittest.TestCase):
    """Z-05: TSV OR mirror baseline refuses with exit 3, before any network."""

    TSV = ("commit\tmetric\tconfidence\tapi_calls\twall_clock_min\tstatus\tdescription\n"
           "abc\t1\t0.5\t0\t1\tbaseline\tbaseline run=t1\n")

    def test_tsv_baseline_exits_3(self):
        code, out, http = _run(StoreConn(_parcels()), World(), tsv=self.TSV)
        self.assertEqual(code, 3)
        self.assertEqual(http.calls, [])
        _assert_counts_only(self, out)

    def test_mirror_baseline_without_tsv_exits_3(self):
        conn = StoreConn(_parcels(), mirror=[("t1", "baseline"), ("t1", "keep")])
        code, out, http = _run(conn, World())
        self.assertEqual(code, 3)
        self.assertIn("baseline", out)
        self.assertEqual(http.calls, [])
        self.assertFalse(any(s == zi._SQL_UPDATE_ZONING for s, _ in conn.executed))

    def test_off_branch_open_run_exits_3_unless_overridden(self):
        conn = StoreConn(_parcels(), mirror=[("old", "baseline"), ("old", "discard")])
        code, out, _ = _run(conn, World(), tag=None)
        self.assertEqual(code, 3)
        conn2 = StoreConn(_parcels(), mirror=[("old", "baseline"), ("old", "halt")],
                          overlay=_sf_overlay())
        self.assertEqual(_run(conn2, World(), tag=None)[0], 0)
        conn3 = StoreConn(_parcels(), mirror=[("old", "baseline"), ("old", "keep")],
                          overlay=_sf_overlay())
        self.assertEqual(_run(conn3, World(), tag=None, check_open_run=False)[0], 0)

    def test_fresh_tag_without_baseline_imports(self):
        conn = StoreConn(_parcels(), mirror=[("old", "baseline"), ("old", "keep")],
                         overlay=_sf_overlay())
        code, out, _ = _run(conn, World(), tag="t2")
        self.assertEqual(code, 0, out)

    def test_baseline_rule_matches_costar_import(self):
        import costar_import
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "experiment_log.tsv"
            p.write_text(self.TSV, encoding="utf-8")
            with mock.patch.object(prepare, "current_run_tag", return_value="t1"):
                self.assertEqual(zi.tsv_has_baseline("t1", p), costar_import.run_has_baseline(p))
            self.assertFalse(zi.tsv_has_baseline("t2", p))
            self.assertFalse(zi.tsv_has_baseline(None, p))


class EndToEndTests(unittest.TestCase):
    """Z-04, Z-10, Z-11, Z-13, Z-19, Z-20: the full run on fakes."""

    def _world_and_conn(self):
        return World(), StoreConn(_parcels(), overlay=_sf_overlay())

    def test_writes_only_zoning_columns_with_provenance_and_counts(self):
        world, conn = self._world_and_conn()
        code, out, http = _run(conn, world)
        self.assertEqual(code, 0, out)
        p = conn.committed
        self.assertEqual(p["fulton-P1"][0], "M-1")
        self.assertEqual(zoning.parse_stored(*p["fulton-P1"]).jurisdiction, "south_fulton")
        self.assertEqual(p["fulton-P2"][0], "AG-1")
        self.assertTrue(zoning.parse_stored(*p["fulton-P2"]).split)
        self.assertEqual(p["fulton-P3"][0], "M-2")                  # Fairburn join
        self.assertEqual(p["fulton-07 1500  0001"][0], "M-1")       # spacing variant
        self.assertEqual(p["fulton-P4"], (None, zoning.no_source_description("college_park")))
        self.assertEqual(p["fulton-P5"], (None, None))              # sliver: unmatched, kept
        self.assertEqual(len(p), 6)                                 # nothing inserted/deleted
        self.assertEqual(conn.commits, 1)
        for sql, _ in conn.executed:
            self.assertNotRegex(sql, r"(?i)\b(INSERT\s+INTO|DELETE\s+FROM)\s+parcels\b")
        self.assertIn("written: 5 changed", out)
        self.assertIn("1 unmatched_kept", out)
        self.assertIn("jurisdiction college_park: 1 parcels, 0 matched, 0 unmatched, 1 no_source", out)
        self.assertIn("as of 2026-11-05", out)
        self.assertIn("1 split", out)
        _assert_counts_only(self, out)
        # FLU source never queried for features or written.
        self.assertFalse(any("FLU" in (c[1].get("outFields") or "") for c in http.calls))
        # Layer 11: ParcelID,TaxDist only (SR-8).
        l11 = [c for c in http.calls if c[0] == LAYER + "/query"]
        self.assertTrue(l11)
        self.assertTrue(all(c[1]["outFields"] == "ParcelID,TaxDist" for c in l11))
        self.assertLessEqual(len(http.calls), 20)                  # request budget for the fixture

    def test_second_run_changes_nothing_and_a_rezoning_updates_one_row(self):
        world, conn = self._world_and_conn()
        self.assertEqual(_run(conn, world)[0], 0)
        code, out, _ = _run(conn, world)
        self.assertEqual(code, 0)
        self.assertIn("written: 0 changed, 4 unchanged", out)       # SR-9
        world.fb_rows = [(10, "P3", "C-2"), (11, "07 1500 0001", "M-1")]
        code, out, _ = _run(conn, world)
        self.assertIn("written: 1 changed, 3 unchanged", out)
        self.assertIn("1 drift", out)
        self.assertEqual(conn.committed["fulton-P3"][0], "C-2")

    def test_failed_truncated_or_short_source_writes_nothing_from_it(self):
        for breaker in ("http", "truncate", "short", "error_body"):
            world, conn = self._world_and_conn()
            conn.parcels["fulton-P3"] = zoning.encode_stored("fairburn", "fb", "M-1", False, None)
            conn.committed = dict(conn.parcels)
            if breaker == "http":
                world.fail["fb.example.test"] = _Resp(None, status=503)
            elif breaker == "truncate":
                world.truncate_ids.add("fb.example.test")
            elif breaker == "short":
                world.short_pages.add("fb.example.test")
            else:
                world.fail["fb.example.test"] = _Resp({"error": {"code": 400, "message": OWNER}})
            code, out, _ = _run(conn, world)
            self.assertEqual(code, 4, breaker)                 # finding 5: not a silent 0
            self.assertIn("source fb: FAILED, nothing used", out, breaker)
            self.assertIn("1 zoning source(s) FAILED: fb;", out.splitlines()[-1], breaker)
            self.assertEqual(conn.commits, 1, breaker)          # the other sources' rows commit
            self.assertEqual(conn.committed["fulton-P3"][0], "M-1", breaker)   # prior value kept
            self.assertEqual(conn.committed["fulton-P1"][0], "M-1", breaker)   # other sources fine
            _assert_counts_only(self, out)

    def test_empty_answer_keeps_prior_values(self):
        world, conn = self._world_and_conn()
        conn.parcels["fulton-P3"] = zoning.encode_stored("fairburn", "fb", "M-1", False, None)
        conn.committed = dict(conn.parcels)
        world.fb_rows = []
        code, out, _ = _run(conn, world)
        self.assertEqual(code, 4)
        self.assertIn("source fb: FAILED", out)            # 0% < the 50% match floor
        self.assertEqual(conn.committed["fulton-P3"][0], "M-1")

    def test_parcel_layer_down_exits_1_and_writes_nothing(self):
        world, conn = self._world_and_conn()
        world.fail["parcels.example.test"] = _Resp(None, status=500)
        code, out, _ = _run(conn, world)
        self.assertEqual(code, 1)
        self.assertEqual(conn.commits, 0)
        # ZI-07 (reviews/32): the shared fetcher's text; was "500 after 4 attempts".
        self.assertIn("parcels.example.test: HTTP 500", out)
        _assert_counts_only(self, out)

    def test_unknown_taxdist_prefix_is_untouched(self):
        world, conn = self._world_and_conn()
        world.taxdist["P4"] = "99X"
        code, out, _ = _run(conn, world)
        self.assertEqual(conn.committed["fulton-P4"], (None, None))
        self.assertIn("1 unknown jurisdiction", out)

    def test_precedence_first_source_wins_and_conflicts_are_counted(self):
        results = {
            "sf_zoning": zi.SourceResult("sf_zoning", assigned={"fulton-P1": ("R-3", None, False)}),
            "l34": zi.SourceResult("l34", assigned={"fulton-P1": ("M-1A", None, False),
                                                    "fulton-P2": ("M-2", None, False)}),
        }
        srcs = zi.validate_zoning_sources(_entries())
        plan = zi.build_plan({"fulton-P1": (None, None), "fulton-P2": (None, None)},
                             {"fulton-P1": "south_fulton", "fulton-P2": "fid"}, srcs, results)
        got = {pid: z for z, d, pid in plan.updates}
        self.assertEqual(got, {"fulton-P1": "R-3", "fulton-P2": "M-2"})
        self.assertEqual(plan.counts["precedence_conflicts"], 1)


class JoinTests(unittest.TestCase):
    """Z-11."""

    SRC = zi.ZoningSource("ep", "EP", "zoning", "join", ("east_point",), "https://ep.test/0",
                          "PIN", "ZONING", None, True)

    def test_normalize_id(self):
        self.assertEqual(zi.normalize_id("fulton-14 0129  LL0273 "), "14 0129 LL0273")
        self.assertEqual(zi.normalize_id(" 07 1500 0001 ", trim_last=True), "07 1500 000")
        self.assertIsNone(zi.normalize_id("fulton-  "))

    def test_east_point_trim_and_collision(self):
        feats = [{"attributes": {"PIN": "14 0001 000", "ZONING": "I1"}},
                 {"attributes": {"PIN": "14 0002 000", "ZONING": "R1"}}]
        group = ["fulton-14 0001 0001", "fulton-14 0002 0001", "fulton-14 0002 0002"]
        r = zi.match_join(self.SRC, feats, group)
        self.assertEqual(r.collisions, 2)
        self.assertEqual(list(r.assigned), [])       # 1 of 3 < floor -> refused
        self.assertIsNotNone(r.failed)
        r = zi.match_join(self.SRC, feats, group[:1] + ["fulton-14 0003 0001"] * 0)
        self.assertEqual(r.assigned, {"fulton-14 0001 0001": ("I1", None, False)})

    def test_conflicting_duplicates_are_deterministic_and_counted(self):
        src = zi.ZoningSource("fb", "FB", "zoning", "join", ("fairburn",), "https://fb.test/0",
                              "ParcelID", "ZoningCurr", None, False)
        feats = [{"attributes": {"ParcelID": "07  1", "ZoningCurr": "M-2"}},
                 {"attributes": {"ParcelID": "07 1", "ZoningCurr": "AG"}}]
        for order in (feats, feats[::-1]):
            r = zi.match_join(src, order, ["fulton-07 1"])
            self.assertEqual(r.assigned["fulton-07 1"][0], "AG")
            self.assertEqual(r.conflicting_duplicates, 1)


class OverlayTests(unittest.TestCase):
    """Z-10: ranking, split, tie, sliver; geometry conversion."""

    def test_rank_ties_split_and_sums(self):
        rows = [("a", "M-2", None, 400.0, 1000.0), ("a", "M-1", None, 400.0, 1000.0),
                ("a", "AG-1", None, 200.0, 1000.0),
                ("b", "R-3", None, 300.0, 1000.0), ("b", "R-3", None, 400.0, 1000.0)]
        r = zi.rank_overlay([rows[i] for i in (0, 1, 2)] + [("b", "R-3", None, 700.0, 1000.0)])
        self.assertEqual(r["a"][0], "M-1")            # tie -> alphabetical
        self.assertAlmostEqual(r["a"][2], 0.4)
        self.assertEqual(r["b"][:1], ("R-3",))

    def test_share_is_clamped_and_the_sql_unions_each_district(self):
        # Finding 4: overlapping polygons of one district are unioned in SQL
        # (counted once); the share is clamped at 1.0 as a backstop.
        r = zi.rank_overlay([("a", "M-2", None, 2420.0, 1000.0)])
        self.assertEqual(r["a"], ("M-2", None, 1.0))
        self.assertIn("ST_Area(ST_Union(ST_CollectionExtract(ST_Intersection(pg.g, z.geom), 3))"
                      "::geography)", zi._SQL_OVERLAY)
        self.assertNotIn("SUM(", zi._SQL_OVERLAY)
        self.assertIn("GROUP BY pg.parcel_id, z.district", zi._SQL_OVERLAY)

    def test_match_overlay_split_and_sliver(self):
        src = zi.ZoningSource("sf_zoning", "SF", "zoning", "overlay", ("south_fulton",),
                              "https://sf.test/0", None, "ZClass", None, False)
        conn = StoreConn({}, overlay=_sf_overlay())
        feats = [{"attributes": {"ZClass": "M-1"}, "geometry": {"rings": [_ring(0, 0, 1, 1)]}},
                 {"attributes": {"ZClass": ""}, "geometry": {"rings": [_ring(0, 0, 1, 1)]}},
                 {"attributes": {"ZClass": "M-2"}, "geometry": None}]
        r = zi.match_overlay(conn, src, feats, ["fulton-P1", "fulton-P2", "fulton-P5", "fulton-P9"])
        self.assertEqual(len(conn.inserted_polys), 1)
        self.assertEqual(r.assigned["fulton-P1"], ("M-1", "Light Industrial", False))
        self.assertEqual(r.assigned["fulton-P2"], ("AG-1", "Agricultural", True))
        self.assertEqual((r.slivers, r.unmatched, r.split), (1, 2, 1))

    def test_esri_rings_with_a_hole(self):
        outer = _ring(0, 0, 10, 10)
        hole = [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]]           # counter-clockwise
        gj = json.loads(zi.esri_rings_to_geojson([outer, hole, _ring(20, 20, 21, 21)]))
        self.assertEqual(gj["type"], "MultiPolygon")
        self.assertEqual([len(p) for p in gj["coordinates"]], [2, 1])
        self.assertIsNone(zi.esri_rings_to_geojson([[[0, 0], [1, 1]]]))

    def test_tiles_cover_and_are_stable(self):
        boxes = [(-84.56, 33.51, -84.54, 33.52), (-84.56, 33.51, -84.55, 33.515)]
        t = zi.tiles_for(boxes)
        self.assertEqual(t, zi.tiles_for(boxes[::-1]))
        for xmin, ymin, xmax, ymax in boxes:
            for x, y in ((xmin, ymin), (xmax, ymax)):
                self.assertTrue(any(a <= x <= c and b <= y <= d for a, b, c, d in t))
        self.assertEqual(len(zi.tiles_for([(-84.51, 33.49, -84.49, 33.51)])), 4)


class HttpEtiquetteTests(unittest.TestCase):
    """Z-12, now through rules_scrape.PoliteFetcher (reviews/32, ZI-07): 1
    req/s per host, bounded retries, Retry-After capped. The 4fe8080 policy
    (3 retries, 429 retried without Retry-After, "<status> after 4 attempts")
    is replaced by the shared one; each changed assertion is listed in
    reviews/32_zoning_import_fetcher/02_code_writer_response.md."""

    class _Seq:
        """Data answers in order; robots.txt answered 404 and not consumed."""

        def __init__(self, responses, clock=None):
            self.responses = list(responses)
            self.calls: list[str] = []
            self.raw: list[str] = []
            self.at: list[tuple[str, float]] = []
            self.clock = clock

        def get(self, url, params=None, timeout=None, **kw):
            self.raw.append(url)
            if self.clock is not None:
                self.at.append((urlsplit(url).hostname, self.clock[0]))
            if urlsplit(url).path == "/robots.txt":
                return ROBOTS_404
            self.calls.append(url)
            return self.responses.pop(0)

    @staticmethod
    def _clocked():
        now = [100.0]
        sleeps: list[float] = []

        def sleep(x):
            sleeps.append(x)
            now[0] += x
        return now, sleeps, sleep

    def test_same_host_spaced_different_hosts_not_serialised(self):
        now, sleeps, sleep = self._clocked()
        http = self._Seq([_Resp({}), _Resp({}), _Resp({}), _Resp({})], clock=now)
        s = zi.ZoningSession(http=http, clock=lambda: now[0], sleep=sleep)
        for u in ("https://a.test/x", "https://b.test/x", "https://a.test/y"):
            s.get_json(u, {})
        now[0] += 5.0
        s.get_json("https://a.test/z", {})
        # Every two requests to one host (robots.txt included) are >= 1 s
        # apart; a request to another host is not held back by them.
        by_host: dict[str, list[float]] = {}
        for h, t in http.at:
            by_host.setdefault(h, []).append(t)
        for h, ts in by_host.items():
            self.assertTrue(all(b - a >= 1.0 for a, b in zip(ts, ts[1:])), (h, ts))
        a_last_before_b = [t for h, t in http.at if h == "a.test"][1]
        b_first = [t for h, t in http.at if h == "b.test"][0]
        self.assertEqual(b_first, a_last_before_b)             # b not serialised behind a
        self.assertTrue(all(x == 1.0 for x in sleeps), sleeps)
        # ZI-07: one robots.txt GET per host counts in the requests line.
        self.assertEqual(s.requests, {"a.test": 4, "b.test": 2})
        self.assertEqual(s.total_requests(), 4)                # data requests only (ZI-08)

    def test_429_without_acceptable_retry_after_is_refused_at_once(self):
        # Changed (ZI-07): 4fe8080 retried 4 times sleeping min(3600, 30).
        for ra in ("3600", None, "x"):
            now, sleeps, sleep = self._clocked()
            hdr = {"Retry-After": ra} if ra is not None else {}
            http = self._Seq([_Resp(None, 429, hdr)] * 4)
            s = zi.ZoningSession(http=http, clock=lambda: now[0], sleep=sleep)
            with self.assertRaises(zi.SourceError) as cm:
                s.get_json("https://a.test/x", {})
            self.assertEqual(str(cm.exception), "a.test: HTTP 429 without an acceptable Retry-After")
            self.assertEqual(len(http.calls), 1, ra)
            self.assertTrue(all(x <= rules_scrape.RETRY_AFTER_CAP_S for x in sleeps), sleeps)

    def test_retries_are_bounded_and_retry_after_is_honoured_under_the_cap(self):
        now, sleeps, sleep = self._clocked()
        http = self._Seq([_Resp(None, 429, {"Retry-After": "7"})] * 4)
        s = zi.ZoningSession(http=http, clock=lambda: now[0], sleep=sleep)
        with self.assertRaises(zi.SourceError) as cm:
            s.get_json("https://a.test/x", {})
        self.assertEqual(len(http.calls), rules_scrape.MAX_RETRIES + 1)
        self.assertEqual(sleeps.count(7.0), rules_scrape.MAX_RETRIES)
        self.assertTrue(all(x <= rules_scrape.RETRY_AFTER_CAP_S for x in sleeps))
        self.assertEqual(str(cm.exception), "a.test: HTTP 429 without an acceptable Retry-After")
        now, sleeps, sleep = self._clocked()
        http = self._Seq([_Resp(None, 503)] * 4)
        s = zi.ZoningSession(http=http, clock=lambda: now[0], sleep=sleep)
        with self.assertRaises(zi.SourceError) as cm:
            s.get_json("https://a.test/x", {})
        self.assertEqual(len(http.calls), rules_scrape.MAX_RETRIES + 1)
        self.assertEqual(str(cm.exception), "a.test: HTTP 503")       # was "503 after 4 attempts"
        self.assertEqual([x for x in sleeps if x > 1.0], list(rules_scrape.BACKOFF_S))
        http = self._Seq([_Resp(None, 503, {"Retry-After": "3600"})])
        s = zi.ZoningSession(http=http, clock=lambda: now[0], sleep=sleep)
        with self.assertRaises(zi.SourceError) as cm:
            s.get_json("https://a.test/x", {})
        self.assertEqual(str(cm.exception), "a.test: HTTP 503 with Retry-After above the cap")
        self.assertEqual(len(http.calls), 1)

    def test_a_retry_then_success_returns_the_body(self):
        http = self._Seq([_Resp(None, 502), _Resp({"ok": 1})])
        s = zi.ZoningSession(http=http, clock=lambda: 0.0, sleep=lambda x: None)
        self.assertEqual(s.get_json("https://a.test/x", {}), {"ok": 1})
        self.assertEqual(len(http.calls), 2)

    def test_4xx_fails_fast_and_messages_carry_no_body(self):
        # Unchanged texts: "service error <code>" (codes only, SR-8) and "HTTP 404".
        http = self._Seq([_Resp({"error": {"code": 498, "message": OWNER}}), _Resp(None, 404)])
        s = zi.ZoningSession(http=http, clock=lambda: 0.0, sleep=lambda x: None)
        with self.assertRaises(zi.SourceError) as cm:
            s.get_json("https://a.test/x", {})
        self.assertEqual(str(cm.exception), "a.test: service error 498")
        with self.assertRaises(zi.SourceError) as cm:
            s.get_json("https://a.test/x", {})
        self.assertEqual(str(cm.exception), "a.test: HTTP 404")
        self.assertEqual(len(http.calls), 2)
        for body, want in (({"error": "x"}, "a.test: service error ?"),
                           ([1, 2], "a.test: answer is not a JSON object"),
                           (ValueError("x"), "a.test: answer is not JSON")):
            http = self._Seq([_Resp(body)])
            s = zi.ZoningSession(http=http, clock=lambda: 0.0, sleep=lambda x: None)
            with self.assertRaises(zi.SourceError) as cm:
                s.get_json("https://a.test/x", {})
            self.assertEqual(str(cm.exception), want)
            self.assertNotIn(OWNER, str(cm.exception))

    def test_source_budget(self):
        world = World()
        world.sf_polys = [(i, "M-1", None, [_ring(0, 0, 1, 1)]) for i in range(1, 2000)]
        session, http = _session(world)
        src = zi.validate_zoning_sources(_entries())[0]
        with mock.patch.object(zi, "MAX_REQUESTS_PER_SOURCE", 5), mock.patch.object(zi, "OID_CHUNK", 10):
            with self.assertRaises(zi.SourceError):
                zi.fetch_features(session, src, [(-84.6, 33.5, -84.4, 33.6)])
        self.assertLessEqual(len(http.calls), 7)


class WhereClauseTests(unittest.TestCase):
    """Z-25."""

    def test_quote_and_unsafe_ids_are_skipped(self):
        where, skipped = zi.in_list_where("ParcelID", ["07 1500", "O'BRIEN", "x;1", "14F-01 LL"])
        self.assertEqual(where, "ParcelID IN ('07 1500','14F-01 LL')")
        self.assertEqual(skipped, 2)
        self.assertEqual(zi.in_list_where("ParcelID", ["a'b"]), ("", 1))
        with self.assertRaises(ValueError):
            zi.in_list_where("ParcelID) OR (1=1", ["07"])


class StaticTests(unittest.TestCase):
    """Z-04, Z-16, SR-2."""

    SRC = (REPO_ROOT / "zoning_import.py").read_text(encoding="utf-8")

    def _sql_constants(self) -> dict[str, str]:
        out = {}
        for node in ast.parse(self.SRC).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id.startswith("_SQL_"):
                out[node.targets[0].id] = getattr(zi, node.targets[0].id)
        return out

    def test_only_one_parcels_write_and_it_is_the_zoning_update(self):
        consts = self._sql_constants()
        self.assertGreaterEqual(len(consts), 9)
        writes = {k: v for k, v in consts.items()
                  if re.search(r"(?i)\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP)\b", v)}
        self.assertEqual(set(writes), {"_SQL_UPDATE_ZONING", "_SQL_INSERT_POLY", "_SQL_CREATE_TEMP"})
        self.assertEqual(consts["_SQL_UPDATE_ZONING"],
                         "UPDATE parcels SET zoning = %s, zoning_description = %s WHERE parcel_id = %s")
        self.assertIn("zoning_import_polys", consts["_SQL_INSERT_POLY"])
        self.assertIn("TEMP TABLE", consts["_SQL_CREATE_TEMP"])
        self.assertNotRegex(self.SRC, r"(?i)(INSERT\s+INTO|DELETE\s+FROM)\s+parcels")

    def test_no_research_log_or_flag_rows(self):
        # Z-16: the import writes no research_log (so no new action_type) and no flags.
        self.assertNotIn("research_log", self.SRC)
        self.assertNotIn("flagged_items", self.SRC)

    def test_execute_takes_constants_only(self):
        for node in ast.walk(ast.parse(self.SRC)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "execute" and node.args:
                self.assertIsInstance(node.args[0], (ast.Name, ast.Attribute), ast.dump(node.args[0])[:80])

    def test_no_cache_files_written(self):
        tree = ast.parse(self.SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                recv = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
                self.assertNotIn(node.func.attr, {"write_text", "write_bytes", "mkstemp"})
                self.assertFalse(recv in ("json", "os", "shutil") and node.func.attr in
                                 ("dump", "replace", "rename", "copy", "copyfile"), ast.dump(node)[:80])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                self.fail("zoning_import opens a file for writing")


if __name__ == "__main__":
    unittest.main()


class LiveCheckGuardTests(unittest.TestCase):
    """Z-24: the live check refuses anything but a local server."""

    def test_guard_rejects_non_local_dsns(self):
        import live_zoning_check as lz
        from live_txn_check import UnsafeTarget
        for dsn in ("postgresql://u:p@db.example.com:5432/x",
                    "postgresql://u:p@abc.supabase.co:6543/postgres", None, ""):
            with self.assertRaises(UnsafeTarget):
                lz.guard_dsn(dsn, {})
        self.assertEqual(lz.guard_dsn("postgresql://u:p@localhost:55432/x", {})["port"], "55432")


# ===========================================================================
# reviews/32_zoning_import_fetcher (D1-11): ZoningSession on the shared fetcher
# ===========================================================================
REF_PATH = REPO_ROOT / "tests" / "zoning_session_4fe8080.py"


def _requests_line(out: str) -> dict[str, int]:
    line = [l for l in out.splitlines() if l.startswith("requests: ")]
    if not line:
        return {}
    body = line[0][len("requests: "):]
    return {h: int(n) for h, n in (p.rsplit(" ", 1) for p in body.split(", ") if p)}


def _normalise_new(out: str, http: FakeHttp) -> str:
    """The new stdout minus the two expected diffs (ZI-04): the robots: lines
    and the robots.txt share of the requests: line."""
    robots: dict[str, int] = {}
    for u in http.robots_calls():
        robots[urlsplit(u).hostname] = robots.get(urlsplit(u).hostname, 0) + 1
    lines = []
    for line in out.splitlines():
        if line.startswith("robots: "):
            continue
        if line.startswith("requests: "):
            counts = {h: n - robots.get(h, 0) for h, n in _requests_line(line).items()}
            line = "requests: " + ", ".join(f"{h} {k}" for h, k in sorted(counts.items()))
        lines.append(line)
    return "\n".join(lines)


def _str_params(calls: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    return [(u, {k: str(v) for k, v in p.items()}) for u, p in calls]


def _variant(name: str) -> tuple[World, StoreConn, dict]:
    """A fixture world, its database and the run arguments for one variant.
    The World class and its data are the ones the tests above use."""
    world, conn = World(), StoreConn(_parcels(), overlay=_sf_overlay())
    kw: dict[str, Any] = {}
    if name in ("http", "truncate", "short", "error_body", "join_floor"):
        conn.parcels["fulton-P3"] = zoning.encode_stored("fairburn", "fb", "M-1", False, None)
        conn.committed = dict(conn.parcels)
    if name == "http":
        world.fail["fb.example.test"] = _Resp(None, status=503)
    elif name == "truncate":
        world.truncate_ids.add("fb.example.test")
    elif name == "short":
        world.short_pages.add("fb.example.test")
    elif name == "error_body":
        world.fail["fb.example.test"] = _Resp({"error": {"code": 400, "message": OWNER}})
    elif name == "join_floor":
        world.fb_rows = []
    elif name == "unknown_taxdist":
        world.taxdist["P4"] = "99X"
    elif name == "parcel_layer_down":
        world.fail["parcels.example.test"] = _Resp(None, status=500)
    elif name == "truncated_parcels":
        world.fail["parcels.example.test"] = _Resp({"features": [], "exceededTransferLimit": True})
    elif name == "tsv_baseline":
        kw["tsv"] = RunStateGuardTests.TSV
    elif name == "mirror_baseline":
        conn.mirror = [("t1", "baseline"), ("t1", "keep")]
    elif name == "no_key":
        kw["sources"] = _sources(drop_key=True)
    elif name == "flu_only":
        kw["sources"] = _sources([e for e in _entries() if e["role"] == "flu"])
    return world, conn, kw


VARIANTS = ("full", "http", "truncate", "short", "error_body", "join_floor", "unknown_taxdist",
            "parcel_layer_down", "truncated_parcels", "tsv_baseline", "mirror_baseline", "no_key",
            "flu_only")
# Transport failures: the shared policy's reason text and retry count (ZI-07).
TRANSPORT = {"http": ("fb.example.test: 503 after 4 attempts", "fb.example.test: HTTP 503",
                      "fb.example.test"),
             "parcel_layer_down": ("parcels.example.test: 500 after 4 attempts",
                                   "parcels.example.test: HTTP 500", "parcels.example.test")}


class DifferentialTests(unittest.TestCase):
    """ZI-04 / G4: the verbatim 4fe8080 session and the new one drive run()
    over the same world and database: identical SQL (UPDATEs in order and
    values), commits, exit code and stdout after the two named diffs."""

    def _both(self, name: str, runs: int = 1):
        got = {}
        for ref in (True, False):
            world, conn, kw = _variant(name)
            outs, https = [], []
            for _ in range(runs):
                session, http = _session(world, ref=ref)
                code, out = _drive(conn, session, **kw)
                outs.append((code, out))
                https.append(http)
            got[ref] = (outs, conn, https)
        return got[True], got[False]

    def test_reference_copy_is_verbatim(self):
        import subprocess
        res = subprocess.run(["git", "-C", str(REPO_ROOT), "show", "4fe8080:zoning_import.py"],
                             capture_output=True, text=True)
        if res.returncode != 0:
            self.skipTest("commit 4fe8080 not available")
        base = res.stdout.splitlines(True)
        ref = REF_PATH.read_text(encoding="utf-8")
        self.assertTrue(base[307].startswith("class ZoningSession:"))
        self.assertIn("".join(base[307:373]), ref)        # the class body
        self.assertIn("".join(base[90:96]), ref)          # its constants

    def test_every_variant_matches_the_base_session(self):
        for name in VARIANTS:
            with self.subTest(name):
                (ref_outs, ref_conn, ref_http), (new_outs, new_conn, new_http) = self._both(name)
                (rc, rout), (nc, nout) = ref_outs[0], new_outs[0]
                self.assertEqual(nc, rc, (name, nout))
                self.assertEqual(new_conn.executed, ref_conn.executed, name)     # SQL, in order
                upd = [p for q, p in new_conn.executed if q == zi._SQL_UPDATE_ZONING]
                self.assertEqual(upd, [p for q, p in ref_conn.executed if q == zi._SQL_UPDATE_ZONING])
                self.assertEqual(new_conn.committed, ref_conn.committed, name)
                self.assertEqual(new_conn.commits, ref_conn.commits, name)
                norm = _normalise_new(nout, new_http[0])
                if name in TRANSPORT:
                    old_text, new_text, host = TRANSPORT[name]
                    self.assertIn(old_text, rout)
                    self.assertIn(new_text, nout)
                    rout = rout.replace(old_text, new_text)
                    # one failed call: 4 attempts before, 3 now
                    rc_ = _requests_line(rout)
                    if rc_:
                        rc_[host] -= 4 - (rules_scrape.MAX_RETRIES + 1)
                        rout = "\n".join("requests: " + ", ".join(
                            f"{h} {k}" for h, k in sorted(rc_.items()))
                            if l.startswith("requests: ") else l for l in rout.splitlines())
                else:
                    self.assertEqual(_str_params(new_http[0].calls),
                                     _str_params(ref_http[0].calls), name)  # same queries, in order
                self.assertEqual(norm, rout.rstrip("\n"), name)
                self.assertFalse([l for l in nout.splitlines() if l.startswith("robots: ")], name)

    def test_second_run_and_rezoning_match(self):
        # SR-9 through both sessions: a re-run changes 0 rows.
        (ref_outs, ref_conn, ref_http), (new_outs, new_conn, new_http) = self._both("full", runs=2)
        for (rc, rout), (nc, nout), h in zip(ref_outs, new_outs, new_http):
            self.assertEqual(nc, rc)
            self.assertEqual(_normalise_new(nout, h), rout.rstrip("\n"))
        self.assertIn("written: 0 changed, 4 unchanged", new_outs[1][1])
        self.assertEqual(new_conn.executed, ref_conn.executed)

    def test_robots_lines_are_the_only_new_lines_and_the_counted_diff_is_one_per_host(self):
        (ref_outs, _, _), (new_outs, _, new_http) = self._both("full")
        hosts = {urlsplit(u).hostname for u in new_http[0].raw}
        self.assertEqual(sorted(urlsplit(u).hostname for u in new_http[0].robots_calls()),
                         sorted(hosts))                                  # one robots.txt per host
        ref_counts, new_counts = _requests_line(ref_outs[0][1]), _requests_line(new_outs[0][1])
        self.assertEqual({h: new_counts[h] - ref_counts[h] for h in ref_counts},
                         {h: 1 for h in ref_counts})


class WireUrlTests(unittest.TestCase):
    """ZI-04: requests' params= (4fe8080) and urlencode (PoliteFetcher) put
    the same URL on the wire for every query shape zoning_import sends."""

    @staticmethod
    def _prep(u: str, params: dict | None = None) -> str:
        import requests
        return requests.Request("GET", u, params=params).prepare().url

    def test_every_query_of_a_full_run_is_the_same_on_the_wire(self):
        world, conn, _ = _variant("full")
        ref, ref_http = _session(world, ref=True)
        _drive(conn, ref)
        world, conn, _ = _variant("full")
        new, new_http = _session(world)
        _drive(conn, new)
        shapes = set()
        sent = [u for u in new_http.raw if urlsplit(u).path != "/robots.txt"]
        self.assertEqual(len(sent), len(ref_http.calls))
        for (u, p), wire in zip(ref_http.calls, sent):
            self.assertEqual(self._prep(u, p), self._prep(u + "?" + zi.urlencode(p)), (u, p))
            self.assertEqual(self._prep(wire), self._prep(u, p), u)
            shapes.add(tuple(sorted(p)))
        # Every shape: metadata f=pjson, the parcel IN-list (with the
        # double-spaced id), the envelope objectIds query, feature pages
        # with and without outSR.
        self.assertIn(("f",), shapes)
        self.assertTrue(any("where" in s and "outFields" in s for s in shapes))
        self.assertTrue(any("geometry" in s and "inSR" in s for s in shapes))
        self.assertTrue(any("objectIds" in s and "outSR" in s for s in shapes))
        self.assertTrue(any("objectIds" in s and "outSR" not in s for s in shapes))
        self.assertTrue(any("07 1500  0001" in (p.get("where") or "") for _, p in ref_http.calls))


class FetcherPolicyTests(unittest.TestCase):
    """ZI-02, ZI-03, ZI-05, ZI-08 to ZI-11 on fakes."""

    def _fb_failed(self, code: int, out: str, conn: StoreConn, reason: str) -> None:
        self.assertEqual(code, 4, out)
        self.assertIn(f"source fb: FAILED, nothing used: {reason}", out)
        self.assertEqual(conn.committed["fulton-P3"][0], "M-1")          # prior value kept
        self.assertEqual(conn.committed["fulton-P1"][0], "M-1")          # other sources fine
        self.assertFalse([p for q, p in conn.executed
                          if q == zi._SQL_UPDATE_ZONING and p[2] == "fulton-P3"])
        _assert_counts_only(self, out)

    def _fb_conn(self) -> tuple[World, StoreConn]:
        world, conn, _ = _variant("http")
        world.fail.clear()
        return world, conn

    # --- inheritance (G3/G5) -------------------------------------------------
    def test_policy_is_inherited_unchanged(self):
        P = rules_scrape.PoliteFetcher
        self.assertTrue(issubclass(zi.ZoningSession, P))
        for name in ("robots_for", "_check", "fetch", "_raw_get", "_space"):
            self.assertIs(getattr(zi.ZoningSession, name), getattr(P, name), name)
        self.assertEqual(zi.ZoningSession.robots_token, "Land-Research-ZoningImport")
        self.assertEqual(zi.ZoningSession.user_agent, zi.USER_AGENT)
        self.assertEqual(zi.USER_AGENT, "Land-Research-ZoningImport/1.0 (between-runs operator "
                                        "tool; 1 req/s per host)")

    def test_every_request_carries_the_zoning_user_agent(self):
        world, conn, _ = _variant("full")
        code, out, http = _run(conn, world)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(http.robots_calls()), 3)
        self.assertEqual(len(http.headers_seen), len(http.raw))
        for h, kw in zip(http.headers_seen, http.kwargs):
            self.assertEqual(h, {"User-Agent": zi.USER_AGENT})
            self.assertIs(kw["allow_redirects"], False)
            self.assertIs(kw["stream"], True)

    def test_rules_scrape_keeps_its_own_user_agent(self):
        world = World()
        http = FakeHttp(world)
        f = rules_scrape.PoliteFetcher(http, clock=lambda: 0.0, sleep=lambda x: None)
        f.fetch("https://fb.example.test/0?f=pjson")
        self.assertEqual(rules_scrape.PoliteFetcher.user_agent, rules_scrape.USER_AGENT)
        self.assertEqual({h["User-Agent"] for h in http.headers_seen}, {rules_scrape.USER_AGENT})

    def test_real_session_headers_and_no_cookies(self):
        import requests
        from requests.adapters import BaseAdapter

        class Adapter(BaseAdapter):
            def __init__(self):
                super().__init__()
                self.sent = []

            def send(self, request, **kw):
                self.sent.append(request)
                r = requests.Response()
                r.status_code = 404 if request.path_url == "/robots.txt" else 200
                r._content = b"{}"
                r.headers["Content-Type"] = "application/json"
                r.headers["Set-Cookie"] = "sid=abc; Path=/"
                r.url, r.request = request.url, request
                r.raw = io.BytesIO(b"{}")
                return r

            def close(self):
                pass

        s = zi.ZoningSession(clock=lambda: 0.0, sleep=lambda x: None)
        ad = Adapter()
        s.http.mount("https://", ad)
        self.assertEqual(s.http.headers["User-Agent"], zi.USER_AGENT)
        self.assertEqual(s.http.headers["Accept"], "application/json")
        s.get_json("https://a.example.test/0", {"f": "pjson"})
        s.get_json("https://a.example.test/0/query", {"f": "json"})
        self.assertEqual(len(ad.sent), 3)                       # robots + 2 data
        for req in ad.sent:
            self.assertNotIn("Cookie", req.headers)              # ZI-11: never replayed
            self.assertEqual(req.headers["User-Agent"], zi.USER_AGENT)
            self.assertEqual(req.headers["Accept"], "application/json")
        self.assertEqual(len(s.http.cookies), 0)

    def test_denylisted_and_terms_blocked_urls_make_no_request(self):
        for url, reason in (("https://library.municode.com/x/0", "denylisted host"),
                            ("https://%6Dunicode.com/x/0", "denylisted host"),
                            ("https://www.zoneomics.com/x/0", "terms prohibit automated access")):
            world, conn = self._fb_conn()
            entries = _entries()
            entries[2]["url"] = url
            code, out, http = _run(conn, world, sources=_sources(entries))
            host = urlsplit(url).hostname
            self.assertFalse([u for u in http.raw if urlsplit(u).hostname == host], url)
            self.assertEqual(code, 4, out)
            self.assertIn(f"source fb: FAILED, nothing used: {host}: {reason}", out)
            self.assertEqual(conn.committed["fulton-P3"][0], "M-1")

    # --- ZI-02 budget --------------------------------------------------------
    def test_request_budget_bound(self):
        for n, parcels in ((1, 0), (6, 2000), (6, 1), (12, 100_000)):
            b = zi.request_budget(n, parcels)
            self.assertGreaterEqual(b, n * (zi.MAX_REQUESTS_PER_SOURCE + 1)
                                    + -(-parcels // zi.ID_CHUNK))
        self.assertGreater(zi.request_budget(0, 0), 0)

    def test_six_sources_at_their_budget_and_2000_parcels_never_hit_the_global_budget(self):
        hosts = [f"s{i}.example.test" for i in range(6)]
        entries = [{"key": f"s{i}", "name": f"S{i}", "role": "zoning", "kind": "overlay",
                    "jurisdictions": ["south_fulton"], "url": f"https://{h}/Z/0",
                    "zoning_field": "ZClass"} for i, h in enumerate(hosts)]
        pids = [f"P{i:05d}" for i in range(2000)]

        def answer(url):
            base, _q, query = url.partition("?")
            p = dict(parse_qsl(query))
            if urlsplit(base).path == "/robots.txt":
                return None
            if base == LAYER + "/query":
                ids = re.findall(r"'([^']*)'", p["where"])
                return _Resp({"features": [{"attributes": {"ParcelID": i, "TaxDist": "55"}}
                                           for i in ids]})
            if p.get("f") == "pjson":
                return _Resp({"fields": [{"name": "ZClass"}], "maxRecordCount": 1,
                              "geometryType": "esriGeometryPolygon"})
            if p.get("returnIdsOnly") == "true":
                return _Resp({"objectIds": list(range(1, 1001))})
            return _Resp({"features": [{"attributes": {"ZClass": "M-1"},
                                        "geometry": {"rings": [_ring(0, 0, 1, 1)]}}]})

        conn = StoreConn({f"fulton-{p}": (None, None) for p in pids})
        session, http = _session(World(), override=answer)
        code, out = _drive(conn, session, sources=_sources(entries))
        self.assertEqual(code, 4, out)
        for i in range(6):
            self.assertIn(f"source s{i}: FAILED, nothing used: request budget of "
                          f"{zi.MAX_REQUESTS_PER_SOURCE} exceeded", out)
        self.assertIn("parcels: 2000 Fulton parcels; 0 not found", out)
        self.assertEqual(session.max_requests, zi.request_budget(6, 2000))
        self.assertLess(session.total(), session.max_requests)
        per_host = _requests_line(out)
        for h in hosts:   # preflight + 1 tile + 400 pages, + robots.txt
            self.assertLessEqual(per_host[h], zi.MAX_REQUESTS_PER_SOURCE + 2 + 1, per_host)

    def test_forced_global_exhaustion_is_a_source_error_never_a_traceback(self):
        # Budget 3: robots + one parcel query, then the first source's robots.
        world, conn = self._fb_conn()
        with mock.patch.object(zi, "request_budget", return_value=3):
            code, out, http = _run(conn, world, max_requests=3)
        self.assertEqual(code, 4, out)
        self.assertIn("source sf_zoning: FAILED, nothing used: sf.example.test: request budget "
                      "of 3 spent", out)
        self.assertIn("source fb: FAILED, nothing used: fb.example.test: request budget of 3 "
                      "spent", out)
        self.assertEqual(len(http.raw), 3)
        self.assertEqual(conn.committed["fulton-P3"][0], "M-1")
        _assert_counts_only(self, out)
        # Budget 1: the parcel layer itself -> REFUSED, exit 1, nothing written.
        world, conn = self._fb_conn()
        with mock.patch.object(zi, "request_budget", return_value=1):
            code, out, http = _run(conn, world, max_requests=1)
        self.assertEqual(code, 1, out)
        self.assertIn("REFUSED: Fulton parcel layer unavailable (parcels.example.test: request "
                      "budget of 1 spent); nothing written.", out)
        self.assertEqual(conn.commits, 0)

    # --- ZI-05 new fail-closed outcomes ----------------------------------------
    def test_robots_failures_fail_the_source(self):
        import requests
        cases = (
            (_Resp(b"down", 503, {"Content-Type": "text/plain"}), "robots unavailable (HTTP 503)"),
            (requests.Timeout("t"), "robots unavailable (connection error after 3 attempts)"),
            (_Resp(b"<html>x</html>", 200, {"Content-Type": "text/html"}),
             "robots unavailable (robots.txt is not text)"),
            (_Resp(b"", 302, {"Location": "https://elsewhere.example.org/robots.txt"}),
             "robots unavailable (robots redirect off-site)"),
            (_Resp(b"User-agent: *\nCrawl-delay: 60\n", 200, {"Content-Type": "text/plain"}),
             "robots unavailable (Crawl-delay 60s above the cap)"),
            (_Resp(b"User-agent: Land-Research-ZoningImport\nDisallow: /\n", 200,
                   {"Content-Type": "text/plain"}),
             "robots.txt disallows this path for Land-Research-ZoningImport"),
            (_Resp(b"User-agent: ClaudeBot\nDisallow: /\n", 200, {"Content-Type": "text/plain"}),
             "robots.txt disallows this path for ClaudeBot"),
            (_Resp(b"Invalid URL", 403, {"Content-Type": "text/plain"}),
             "robots unavailable (HTTP 403)"),     # not an ArcGIS host: no D1 exception
        )
        for robots, reason in cases:
            with self.subTest(reason):
                world, conn = self._fb_conn()
                code, out, http = _run(conn, world, robots={"fb.example.test": robots})
                self._fb_failed(code, out, conn, f"fb.example.test: {reason}")
                self.assertFalse([c for c in http.calls if "fb.example.test" in c[0]])
                self.assertFalse([l for l in out.splitlines() if l.startswith("robots: ")])

    def test_robots_failure_on_the_parcel_host_refuses_the_run(self):
        world, conn = self._fb_conn()
        code, out, http = _run(conn, world, robots={
            "parcels.example.test": _Resp(b"down", 500, {"Content-Type": "text/plain"})})
        self.assertEqual(code, 1, out)
        self.assertIn("REFUSED: Fulton parcel layer unavailable (parcels.example.test: robots "
                      "unavailable (HTTP 500)); nothing written.", out)
        self.assertEqual(http.calls, [])
        self.assertEqual(conn.commits, 0)

    def test_data_redirects(self):
        cases = (("https://evil.example.org/0?f=pjson", "redirect off the registered site"),
                 ("http://fb.example.test/0?f=pjson", "redirect to a non-https URL"),
                 ("https://library.municode.com/0", "redirect to a denylisted host"))
        for loc, reason in cases:
            with self.subTest(reason):
                world, conn = self._fb_conn()

                def redirect(url, loc=loc):
                    if url.startswith("https://fb.example.test/0?"):
                        return _Resp(b"", 301, {"Location": loc})
                    return None
                code, out, http = _run(conn, world, override=redirect)
                self._fb_failed(code, out, conn, f"fb.example.test: {reason}")
                origin = f"{urlsplit(loc).scheme}://{urlsplit(loc).netloc}/"
                self.assertFalse([u for u in http.raw if u.startswith(origin)], http.raw)
        # Same registrable domain, https: followed (robots of the new host read).
        world, conn = self._fb_conn()

        def same_site(url):
            if url == "https://fb.example.test/0?f=pjson":
                return _Resp(b"", 302, {"Location": "https://fb2.example.test/0?f=pjson"})
            return None
        code, out, http = _run(conn, world, override=same_site)
        self.assertEqual(code, 0, out)
        self.assertIn("https://fb2.example.test/robots.txt", http.raw)
        self.assertEqual(conn.committed["fulton-P3"][0], "M-2")

    def test_challenge_page_and_size_cap(self):
        world, conn = self._fb_conn()
        world.fail["fb.example.test"] = _Resp({"features": [], "x": "challenge-platform"})
        code, out, _ = _run(conn, world)
        self._fb_failed(code, out, conn, "fb.example.test: challenge page")
        world, conn = self._fb_conn()
        world.fail["fb.example.test"] = _Resp({"features": [], "pad": "x" * 5000})
        with mock.patch.object(rules_scrape, "MAX_BODY_BYTES", 4000):
            code, out, _ = _run(conn, world)
        self._fb_failed(code, out, conn, "fb.example.test: answer larger than the size cap")

    def test_body_cut_off_mid_read_fails_the_source_without_traceback(self):
        import requests

        class Broken(_Resp):
            def iter_content(self, n):
                yield b'{"feat'
                raise requests.exceptions.ChunkedEncodingError("cut")

        world, conn = self._fb_conn()
        world.fail["fb.example.test"] = Broken({})
        code, out, _ = _run(conn, world)
        self._fb_failed(code, out, conn, "fb.example.test: connection error")
        world, conn = self._fb_conn()
        world.fail["parcels.example.test"] = Broken({})
        code, out, _ = _run(conn, world)
        self.assertEqual(code, 1, out)
        self.assertIn("(parcels.example.test: connection error)", out)
        self.assertEqual(conn.commits, 0)

    def test_utf8_body_parsed_like_requests(self):
        # Non-ASCII descriptions decode exactly as requests.Response.json().
        import requests
        raw = json.dumps({"d": "Café – M-1"}, ensure_ascii=False).encode("utf-8")
        for ctype in ("application/json", "application/json; charset=utf-8",
                      "text/plain; charset=utf-8", "text/plain", ""):
            r = requests.Response()
            r._content = raw
            r.headers["Content-Type"] = ctype
            r.encoding = requests.utils.get_encoding_from_headers(r.headers)
            self.assertEqual(zi._json_like_requests(raw, {"Content-Type": ctype}), r.json(), ctype)
        # Through get_json, against the 4fe8080 session on a real Response.
        import zoning_session_4fe8080 as ref_mod
        for ctype in ("application/json; charset=utf-8", "text/plain", "text/plain; charset=utf-8"):
            def mk():
                r = requests.Response()
                r.status_code, r._content, r._content_consumed = 200, raw, True
                r.headers["Content-Type"] = ctype
                r.encoding = requests.utils.get_encoding_from_headers(r.headers)
                return r

            class One:
                def get(self, url, params=None, timeout=None, **kw):
                    return ROBOTS_404 if url.endswith("/robots.txt") else mk()
            new = zi.ZoningSession(http=One(), clock=lambda: 0.0, sleep=lambda x: None)
            old = ref_mod.ZoningSession(http=One(), clock=lambda: 0.0, sleep=lambda x: None)
            self.assertEqual(new.get_json("https://a.test/0", {"f": "json"}),
                             old.get_json("https://a.test/0", {"f": "json"}), ctype)
        bom = b"\xef\xbb\xbf" + raw
        self.assertEqual(zi._json_like_requests(bom, {}), {"d": "Café – M-1"})
        with self.assertRaises(ValueError):
            zi._json_like_requests(b"<html>", {"Content-Type": "application/json"})

    # --- ZI-08 / ZI-10 -------------------------------------------------------
    def test_source_budget_counts_data_requests_only(self):
        counts = []
        for warm in (False, True):
            world = World()
            world.sf_polys = [(i, "M-1", None, [_ring(0, 0, 1, 1)]) for i in range(1, 2000)]
            session, http = _session(world)
            if warm:
                session.robots_for("https://sf.example.test")
            src = zi.validate_zoning_sources(_entries())[0]
            with mock.patch.object(zi, "MAX_REQUESTS_PER_SOURCE", 5), \
                    mock.patch.object(zi, "OID_CHUNK", 10):
                with self.assertRaises(zi.SourceError) as cm:
                    zi.fetch_features(session, src, [(-84.6, 33.5, -84.4, 33.6)])
            self.assertEqual(str(cm.exception), "request budget of 5 exceeded")
            counts.append(len(http.calls))
        self.assertEqual(counts[0], counts[1])      # robots inside the window moves nothing
        self.assertEqual(counts[0], 6)              # preflight + 5 (4fe8080: the same)

    def test_run_state_guards_precede_any_request_robots_included(self):
        for name in ("tsv_baseline", "mirror_baseline", "no_key"):
            world, conn, kw = _variant(name)
            session, http = _session(world)
            code, out = _drive(conn, session, **kw)
            self.assertEqual(code, 3 if name != "no_key" else 1, name)
            self.assertEqual(http.raw, [], name)
            self.assertEqual(session.robots_meta, {}, name)


class ImportCycleTests(unittest.TestCase):
    """ZI-01 / G1: every entry point imports in a fresh interpreter."""

    MODULES = ("rules_scrape", "zoning_import", "adjacency_import", "hazards_import", "runner",
               "research")
    CLIS = ("zoning_import.py", "rules_scrape.py", "adjacency_import.py", "hazards_import.py")

    def _py(self, *args: str):
        import os
        import subprocess
        import sys
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        return subprocess.run([sys.executable, *args], cwd=str(REPO_ROOT), env=env,
                              capture_output=True, text=True, timeout=120)

    def test_each_module_imports_first(self):
        for mod in self.MODULES:
            r = self._py("-c", f"import {mod}")
            self.assertEqual(r.returncode, 0, (mod, r.stderr[-800:]))

    def test_each_cli_help(self):
        for cli in self.CLIS:
            r = self._py(cli, "--help")
            self.assertEqual(r.returncode, 0, (cli, r.stderr[-800:]))
            self.assertIn("usage:", r.stdout, cli)

    def test_rules_scrape_as_main_reaches_zoning_import_at_call_time(self):
        # As __main__, rules_scrape's call-time import of zoning_import loads
        # a second, ordinary rules_scrape module; the helpers it calls raise
        # nothing of rules_scrape's.
        script = (
            "import runpy, sys\n"
            "g = runpy.run_path('rules_scrape.py', run_name='__rs_main__')\n"
            "class S: url = 'https://x.example/0'; case_id_field = 'C'; status_field = None\n"
            "assert g['case_features'](None, S, []) == []\n"
            "import zoning_import, rules_scrape\n"
            "assert zoning_import.rules_scrape is sys.modules['rules_scrape']\n"
            "assert g['PoliteFetcher'] is not rules_scrape.PoliteFetcher\n"
            "print('ok')\n")
        r = self._py("-c", script)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("ok", r.stdout)

    def test_rules_scrape_has_no_top_level_zoning_import(self):
        tree = ast.parse((REPO_ROOT / "rules_scrape.py").read_text(encoding="utf-8"))
        top = {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names}
        self.assertNotIn("zoning_import", top)
        self.assertIn("zoning_import", {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                                        for a in n.names})
        zi_tree = ast.parse((REPO_ROOT / "zoning_import.py").read_text(encoding="utf-8"))
        used = {"tiles_for", "esri_rings_to_geojson", "tsv_has_baseline", "mirror_blocks"}
        for fn in (n for n in zi_tree.body if isinstance(n, ast.FunctionDef) and n.name in used):
            names = {x.id for x in ast.walk(fn) if isinstance(x, ast.Name)}
            self.assertFalse(names & {"rules_scrape", "ZoningSession", "SourceError"}, fn.name)
