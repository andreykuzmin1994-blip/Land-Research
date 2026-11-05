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

import connector_harness
import prepare
import zoning
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
    def __init__(self, body: Any = None, status: int = 200, headers: dict | None = None) -> None:
        self._body, self.status_code, self.headers = body, status, headers or {}

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeHttp:
    """Serves the layers of the fixture world; records every request."""

    def __init__(self, world: "World") -> None:
        self.world = world
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict | None = None, timeout: Any = None) -> _Resp:
        params = dict(params or {})
        self.calls.append((url, params))
        return self.world.answer(url, params)


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


def _session(world: World) -> tuple[zi.ZoningSession, FakeHttp]:
    http = FakeHttp(world)
    return zi.ZoningSession(http=http, clock=lambda: 0.0, sleep=lambda s: None), http


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
         tsv: str | None = None, check_open_run: bool = True) -> tuple[int, str, FakeHttp]:
    session, http = _session(world)

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
    return code, out.getvalue(), http


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
        self.assertIn("parcels.example.test: 500 after 4 attempts", out)
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
    """Z-12: 1 req/s per host, bounded retries, Retry-After capped."""

    class _Seq:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append(url)
            return self.responses.pop(0)

    def test_same_host_spaced_different_hosts_not_serialised(self):
        now = [100.0]
        sleeps: list[float] = []

        def sleep(s):
            sleeps.append(s)
            now[0] += s
        http = self._Seq([_Resp({}), _Resp({}), _Resp({}), _Resp({})])
        s = zi.ZoningSession(http=http, clock=lambda: now[0], sleep=sleep)
        s.get_json("https://a.test/x", {})
        s.get_json("https://b.test/x", {})
        self.assertEqual(sleeps, [])
        s.get_json("https://a.test/y", {})
        self.assertEqual(sleeps, [1.0])
        now[0] += 5.0
        s.get_json("https://a.test/z", {})
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(s.requests, {"a.test": 3, "b.test": 1})

    def test_retry_after_is_capped_and_retries_are_bounded(self):
        sleeps: list[float] = []
        http = self._Seq([_Resp(None, 429, {"Retry-After": "3600"})] * 4)
        s = zi.ZoningSession(http=http, clock=lambda: 0.0, sleep=sleeps.append)
        with self.assertRaises(zi.SourceError) as cm:
            s.get_json("https://a.test/x", {})
        self.assertEqual(len(http.calls), zi.MAX_RETRIES + 1)
        self.assertTrue(all(x <= zi.RETRY_AFTER_CAP_S for x in sleeps))
        self.assertIn(zi.RETRY_AFTER_CAP_S, sleeps)
        self.assertEqual(str(cm.exception), "a.test: 429 after 4 attempts")

    def test_4xx_fails_fast_and_messages_carry_no_body(self):
        http = self._Seq([_Resp({"error": {"code": 498, "message": OWNER}}), _Resp(None, 404)])
        s = zi.ZoningSession(http=http, clock=lambda: 0.0, sleep=lambda x: None)
        with self.assertRaises(zi.SourceError) as cm:
            s.get_json("https://a.test/x", {})
        self.assertEqual(str(cm.exception), "a.test: service error 498")
        with self.assertRaises(zi.SourceError) as cm:
            s.get_json("https://a.test/x", {})
        self.assertEqual(str(cm.exception), "a.test: HTTP 404")
        self.assertEqual(len(http.calls), 2)

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
