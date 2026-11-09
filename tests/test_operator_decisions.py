"""Operator decisions of 2026-11-09 (reviews/29_operator_decisions).

Offline (SR-6). D1: the narrow ArcGIS robots exception in the ONE shared
robots verdict (rules_scrape.robots_verdict). D2: flood stays 'flag, don't
block' (pins only). D3: a verified, LISTED district without setbacks counts
for BTS with the default plus an 'investigate further' data_gap flag. D4:
the Zoneomics cache/quotes and the terms block are unchanged (pins only).
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import tempfile
import shutil
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode, urlparse

import requests

import tests  # noqa: F401 — suite guards
import adjacency_import as ai
import hazards_import as hi
import pipeline_common
import prepare
import reporting
import research
import rules_scrape as rs
import site_fit
import zoning
import zoning_import as zi
import site_fit_golden_case as golden
from zoning_helpers import REPO_ROOT, patch_rules, verified

SVC = "services3.arcgis.com"
LAYER = f"https://{SVC}/y2BJK2GUfoTwH7py/arcgis/rest/services/Zoning/FeatureServer/0"
META = LAYER + "?f=json"
QUERY = LAYER + "/query?" + urlencode({"where": "1=1", "returnIdsOnly": "true", "f": "json"})
EXC = "robots unavailable (403): ArcGIS public REST accepted by operator 2026-11-09"
INVESTIGATE = ("investigate further: no district setbacks in zoning_rules.json; "
               "25 ft default used")
FEMA_ROBOTS = ("User-agent: *\nDisallow: /*?*\nDisallow: /arcgis\nDisallow: /nri/report/viewer\n"
               "Disallow: /nri/report/viewer?\n"
               "Disallow: /flood-maps/archived-flood-data/guided-requests\n")


# ---------------------------------------------------------------------------
# HTTP fakes
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


def text(status, body="", ctype="text/plain", **h):
    return Resp(status, body, {"Content-Type": ctype, **h})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Http:
    """routes: url -> Resp | Exception | callable; anything else 404."""

    def __init__(self, routes, clock):
        self.routes, self.clock = dict(routes), clock
        self.calls, self.at = [], []
        self.headers = {}

    def get(self, url, **kw):
        self.calls.append(url)
        self.at.append(self.clock.t)
        r = self.routes.get(url)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(url)
        if r is None:
            if urlparse(url).path == "/robots.txt":
                return text(404, "nf")
            return Resp(200, json.dumps({"objectIds": [1, 2, 3], "name": "Zoning"}))
        return r


FETCHERS = (rs.PoliteFetcher, ai.AdjacencyFetcher, hi.HazardsFetcher, zi.ZoningSession)


def make(cls, routes):
    c = Clock()
    http = Http(routes, c)
    return cls(http, clock=c.clock, sleep=c.sleep), http, c


def data_calls(http):
    return [u for u in http.calls if not u.endswith("/robots.txt")]


def robots_403(host=SVC, status=403):
    return {f"https://{host}/robots.txt": text(status, "Invalid URL")}


# ---------------------------------------------------------------------------
# D1-01: one shared verdict, three fetchers
# ---------------------------------------------------------------------------
class SharedVerdictTests(unittest.TestCase):
    def test_arcgis_403_passes_in_all_three_fetchers(self):
        for cls in FETCHERS:
            f, http, _ = make(cls, robots_403())
            self.assertEqual(f.fetch(META).status, 200, cls.__name__)
            self.assertEqual(f.fetch(QUERY).status, 200, cls.__name__)
            self.assertEqual(data_calls(http), [META, QUERY], cls.__name__)
            meta = f.robots_meta[SVC]
            self.assertTrue(meta["deny_all"], cls.__name__)          # D1-07: still deny_all
            self.assertEqual(meta["exception"], EXC, cls.__name__)

    def test_non_arcgis_403_refuses_in_all_three(self):
        url = "https://gis.county.example/arcgis/rest/services/Zoning/FeatureServer/0?f=json"
        for cls in FETCHERS:
            f, http, _ = make(cls, robots_403("gis.county.example"))
            with self.assertRaises(rs.Unreachable) as cm:
                f.fetch(url)
            self.assertEqual(cm.exception.reason, "robots unavailable (HTTP 403)", cls.__name__)
            self.assertEqual(data_calls(http), [], cls.__name__)
            self.assertNotIn("exception", f.robots_meta["gis.county.example"])

    def test_tools_define_no_verdict_of_their_own(self):
        # zoning_import (reviews/32, D1-11): no _check of its own either, so
        # it calls robots_verdict zero times; the inherited _check calls it.
        for mod, n_calls in (("adjacency_import.py", 1), ("hazards_import.py", 1),
                             ("zoning_import.py", 0)):
            tree = ast.parse((REPO_ROOT / mod).read_text(encoding="utf-8"))
            defs = [n.name for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                    and getattr(n, "name", "") == "robots_verdict"]
            self.assertEqual(defs, [], mod)
            names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                     for t in n.targets if isinstance(t, ast.Name)}
            self.assertNotIn("robots_verdict", names, mod)
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute) and n.func.attr == "robots_verdict"]
            self.assertEqual(len(calls), n_calls, mod)
            self.assertTrue(all(c.func.value.id == "rules_scrape" for c in calls), mod)
            self.assertNotIn("arcgis_exception", (REPO_ROOT / mod).read_text(), mod)
        self.assertIs(ai.AdjacencyFetcher.robots_for, rs.PoliteFetcher.robots_for)
        self.assertIs(hi.HazardsFetcher.robots_for, rs.PoliteFetcher.robots_for)
        self.assertIs(zi.ZoningSession.robots_for, rs.PoliteFetcher.robots_for)
        self.assertIs(zi.ZoningSession._check, rs.PoliteFetcher._check)
        self.assertEqual(zi.ZoningSession.robots_token, zi.UA_TOKEN)
        self.assertEqual(ai.AdjacencyFetcher.robots_token, ai.UA_TOKEN)
        self.assertEqual(hi.HazardsFetcher.robots_token, hi.UA_TOKEN)

    def test_each_tool_keeps_its_own_token(self):
        rb = rs.Robots(f"User-agent: {ai.UA_TOKEN}\nDisallow: /\n")
        self.assertIsNotNone(rs.robots_verdict(rb, "/x", ai.UA_TOKEN))
        self.assertIsNone(rs.robots_verdict(rb, "/x", hi.UA_TOKEN))
        self.assertIsNone(rb.verdict("/x"))           # rules_scrape's own token


# ---------------------------------------------------------------------------
# D1-02: only a plain 401/403 of the origin's own robots.txt on hop 0
# ---------------------------------------------------------------------------
class FailClosedTableTests(unittest.TestCase):
    ROBOTS = f"https://{SVC}/robots.txt"

    def try_fetch(self, answer, extra=None):
        routes = {self.ROBOTS: answer, **(extra or {})}
        f, http, _ = make(rs.PoliteFetcher, routes)
        try:
            f.fetch(META)
        except rs.Unreachable as exc:
            return False, exc.reason, data_calls(http)
        return True, "", data_calls(http)

    def test_401_and_403_are_accepted(self):
        for st in (401, 403):
            ok, why, calls = self.try_fetch(text(st, "Invalid URL"))
            self.assertTrue(ok, (st, why))
            self.assertEqual(calls, [META])

    def test_everything_else_refuses_with_zero_data_requests(self):
        cases = {
            "407": text(407, "proxy"),
            "429 no Retry-After": text(429, "slow down"),
            "500": text(500, "err"),
            "503": text(503, "busy"),
            "timeout": requests.Timeout("t"),
            "connection error": requests.ConnectionError("c"),
            "Cloudflare 403": text(403, "x", Server="cloudflare"),
            "Akamai 403": text(403, "x", Server="AkamaiGHost"),
            "403 cf-mitigated": text(403, "x", **{"cf-mitigated": "challenge"}),
            "403 challenge page": text(403, "<html><title>Just a moment...</title></html>",
                                       "text/html"),
            "403 WAF header": text(403, "x", **{"x-amzn-waf-action": "block"}),
            "cf-mitigated 200": text(200, "User-agent: *\n", **{"cf-mitigated": "challenge"}),
            "HTML 200": text(200, "<html></html>", "text/html"),
            "301 off-site": text(301, "", Location="https://www.example.org/robots.txt"),
            "Crawl-delay 120": text(200, "User-agent: *\nCrawl-delay: 120\n"),
        }
        for name, answer in cases.items():
            ok, why, calls = self.try_fetch(answer)
            self.assertFalse(ok, name)
            self.assertEqual(calls, [], name)

    def test_redirect_to_services2_that_then_403s_refuses(self):
        r2 = "https://services2.arcgis.com/robots.txt"
        ok, why, calls = self.try_fetch(text(302, "", Location=r2), {r2: text(403, "Invalid URL")})
        self.assertFalse(ok)
        self.assertEqual(calls, [])
        self.assertEqual(why, "robots unavailable (HTTP 403)")

    def test_status_is_structured_not_parsed_from_the_reason(self):
        f, _, _ = make(rs.PoliteFetcher, robots_403())
        rb = f.robots_for(f"https://{SVC}")
        self.assertEqual((rb.deny_all, rb.unavailable_status, rb.origin_netloc), (True, 403, SVC))
        forged = rs.Robots(deny_all=True, reason="HTTP 403", origin_netloc=SVC)
        self.assertIsNotNone(rs.robots_verdict(forged, "/o/arcgis/rest/services/X/FeatureServer/0?f=json"))
        # allow_all never carries a status: no path to an exception from it.
        self.assertIsNone(rs.Robots(allow_all=True, unavailable_status=403).unavailable_status)

    def test_404_is_still_allow_all(self):
        ok, _, calls = self.try_fetch(text(404, "nf"))
        self.assertTrue(ok)
        f, _, _ = make(rs.PoliteFetcher, {self.ROBOTS: text(404, "nf")})
        f.fetch(META)
        self.assertNotIn("exception", f.robots_meta[SVC])


# ---------------------------------------------------------------------------
# D1-03: readable robots win (FEMA), USFWS for its real reason
# ---------------------------------------------------------------------------
class ReadableRobotsWinTests(unittest.TestCase):
    def test_fema_disallow_wins_even_if_listed(self):
        url = ("https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query?"
               + urlencode({"where": "1=1", "f": "json"}))
        for hosts in (frozenset(), frozenset({"hazards.fema.gov"})):
            with mock.patch.object(rs, "OPERATOR_ACCEPTED_ARCGIS_HOSTS", hosts):
                f, http, _ = make(hi.HazardsFetcher, {
                    "https://hazards.fema.gov/robots.txt": text(200, FEMA_ROBOTS)})
                with self.assertRaises(rs.Unreachable) as cm:
                    f.fetch(url)
                self.assertIn("robots.txt disallows", cm.exception.reason)
                self.assertEqual(data_calls(http), [])

    def test_usfws_offsite_redirect_stays_refused(self):
        base = "https://fwspublicservices.wim.usgs.gov"
        url = base + "/wetlandsmapservice/rest/services/Wetlands/MapServer/0/query?f=json"
        f, http, _ = make(hi.HazardsFetcher, {base + "/robots.txt": text(
            301, "<h1>Object Moved</h1>", "text/html",
            Location="https://www.fws.gov/program/national-wetlands-inventory/wetlands-mapper")})
        with self.assertRaises(rs.Unreachable) as cm:
            f.fetch(url)
        self.assertEqual(cm.exception.reason, "robots unavailable (robots redirect off-site)")
        self.assertEqual(data_calls(http), [])

    def test_readable_disallow_on_a_services_host_refuses(self):
        for body in ("User-agent: *\nDisallow: /\n",
                     f"User-agent: {rs.AI_AGENT_TOKENS[0]}\nDisallow: /\n",
                     "Content-Signal: ai-input=no\nUser-agent: *\nAllow: /\n",
                     "User-agent: *\nContent-Signal: ai-input=no\nAllow: /\n"):
            for cls in FETCHERS:
                f, http, _ = make(cls, {f"https://{SVC}/robots.txt": text(200, body)})
                with self.assertRaises(rs.Unreachable, msg=(body, cls.__name__)):
                    f.fetch(META)
                self.assertEqual(data_calls(http), [], (body, cls.__name__))


# ---------------------------------------------------------------------------
# D1-04: host match
# ---------------------------------------------------------------------------
class HostMatchTests(unittest.TestCase):
    def test_negatives(self):
        hub = set()
        src = json.loads((REPO_ROOT / "sources.json").read_text(encoding="utf-8"))

        def walk(x):
            if isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            elif isinstance(x, str) and x.startswith("https://") and "arcgis.com" in x:
                hub.add(urlparse(x).netloc)
        walk(src)
        self.assertTrue([h for h in hub if h.endswith("hub.arcgis.com")])
        bad = sorted(hub) + [
            "www.arcgis.com", "tiles.arcgis.com", "services.arcgis.com.evil.test",
            "xservices.arcgis.com", "services3.arcgis.com:8443", "user@services3.arcgis.com",
            "services.arcgis.com\n", "acme.maps.arcgis.com", "services3.arcgis.com%2e",
            "ѕervices3.arcgis.com", "services3a.arcgis.com", "services3.arcgis.co",
            "[::1]", "", "services3.arcgis.com:", "services.arcgis.com.", ]
        for h in bad[:-1]:
            self.assertIsNone(rs.arcgis_exception_host(h), repr(h))

    def test_positives(self):
        for h, want in (("services.arcgis.com", "services.arcgis.com"),
                        ("services8.arcgis.com", "services8.arcgis.com"),
                        ("SERVICES3.ARCGIS.COM", SVC), ("services3.arcgis.com.", SVC),
                        ("services3.arcgis.com:443", SVC)):
            self.assertEqual(rs.arcgis_exception_host(h), want, h)

    def test_port_and_userinfo_refused_end_to_end(self):
        for netloc in ("services3.arcgis.com:8443",):
            url = f"https://{netloc}/o/arcgis/rest/services/Z/FeatureServer/0?f=json"
            f, http, _ = make(rs.PoliteFetcher, {f"https://{netloc}/robots.txt":
                                                 text(403, "Invalid URL")})
            with self.assertRaises(rs.Unreachable):
                f.fetch(url)
            self.assertEqual(data_calls(http), [])

    def test_each_redirect_hop_is_re_evaluated(self):
        tiles = "https://tiles.arcgis.com/o/arcgis/rest/services/Z/FeatureServer/0?f=json"
        f, http, _ = make(rs.PoliteFetcher, {
            **robots_403(), "https://tiles.arcgis.com/robots.txt": text(403, "Invalid URL"),
            META: text(302, "", Location=tiles)})
        with self.assertRaises(rs.Unreachable) as cm:
            f.fetch(META)
        self.assertEqual(cm.exception.reason, "robots unavailable (HTTP 403)")
        self.assertEqual(data_calls(http), [META])

    def test_org_host_constant_is_empty_exact_and_frozen(self):
        c = rs.OPERATOR_ACCEPTED_ARCGIS_HOSTS
        self.assertIsInstance(c, frozenset)
        self.assertEqual(c, frozenset())          # D1-06: shipped empty
        for h in c:                               # pragma: no cover - empty today
            self.assertNotIn("*", h)
            self.assertEqual(h, rs.norm_host(h))


# ---------------------------------------------------------------------------
# D1-05: paths
# ---------------------------------------------------------------------------
class PathTests(unittest.TestCase):
    BASE = "/y2BJK2GUfoTwH7py/arcgis/rest/services"

    def ok(self, p):
        return rs.arcgis_public_rest_path(SVC, p)

    def test_used_paths_pass(self):
        lay = self.BASE + "/Zoning/FeatureServer/0"
        for p in (lay + "?f=json", lay + "?f=pjson",
                  lay + "/query?" + urlencode({"where": "1=1", "returnIdsOnly": "true",
                                               "f": "json"}),
                  lay + "/query?" + urlencode({
                      "where": "1=1", "geometry": "-84.6,33.5,-84.5,33.6",
                      "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                      "spatialRel": "esriSpatialRelIntersects", "returnIdsOnly": "true",
                      "f": "json"}),
                  lay + "/query?" + urlencode({"objectIds": "1,2", "outFields": "OBJECTID,ZClass",
                                               "returnGeometry": "true", "outSR": 4326,
                                               "f": "geojson"}),
                  self.BASE + "/Union_City_Data_WFL1/FeatureServer/14?f=json",
                  self.BASE + "/public/NFHL/MapServer/28/query?f=json"):
            self.assertTrue(self.ok(p), p)

    def test_everything_else_refused(self):
        lay = self.BASE + "/Zoning/FeatureServer/0"
        for p in (self.BASE + "/Zoning/FeatureServer?f=json", lay + "/exportImage?f=json",
                  self.BASE + "/Zoning/MapServer/export?f=json", lay + "/tile/0/0/0?f=json",
                  lay + "/applyEdits?f=json", lay + "/queryRelatedRecords?f=json",
                  "/y2BJK2GUfoTwH7py/arcgis/rest/admin/services?f=json",
                  "/sharing/rest/content/items/abc?f=json", lay + "?f=html",
                  lay + "?f=geojson", lay + "/query?f=html", lay + "/query",
                  lay + "?f=json&f=pjson", lay + "?f=json&token=abc", lay + "/query?f=json&TOKEN=x",
                  self.BASE + "/%2e%2e/Zoning/FeatureServer/0?f=json",
                  self.BASE + "/Zoning%2FX/FeatureServer/0?f=json",
                  lay + "/query/..%2F..%2Fadmin?f=json", self.BASE + "/Zoning/featureserver/0?f=json",
                  self.BASE + "/../Zoning/FeatureServer/0?f=json",
                  self.BASE + "//Zoning/FeatureServer/0?f=json",
                  self.BASE + "/a/b/c/FeatureServer/0?f=json", lay + "/?f=json",
                  lay + "x?f=json", "/arcgis/rest/services/Zoning/FeatureServer/0?f=json",
                  lay + "/query?f=json&x", "/y2BJK2GUfoTwH7py/arcgis/rest/services/Z/FeatureServer/١?f=json"):
            self.assertFalse(self.ok(p), p)

    def test_params_on_any_segment_refused_end_to_end(self):
        # Round 2 F1: urlparse would split ';x' off the last segment and hide
        # it from the guard while the request still carries it.
        for url in (LAYER + "/query;x?f=json", LAYER + ";x?f=json",
                    LAYER + "/query;../../x?f=json", LAYER + "/query;x?" + urlencode(
                        {"where": "1=1", "returnIdsOnly": "true", "f": "json"})):
            for cls in FETCHERS:
                f, http, _ = make(cls, robots_403())
                with self.assertRaises(rs.Unreachable, msg=f"{cls.__name__} {url}") as cm:
                    f.fetch(url)
                self.assertEqual(cm.exception.reason, "robots unavailable (HTTP 403)")
                self.assertEqual(data_calls(http), [], f"{cls.__name__} {url}")
                self.assertNotIn("exception", f.robots_meta[SVC], cls.__name__)

    def test_robots_path_keeps_params(self):
        self.assertEqual(rs.robots_path(LAYER + "/query;x?f=json"),
                         urlparse(LAYER).path + "/query;x?f=json")
        self.assertEqual(rs.robots_path(f"https://{SVC}"), "/")

    def test_readable_robots_matches_the_params_path(self):
        # A ';' path is matched as fetched by ordinary robots rules too.
        robots = "User-agent: *\nDisallow: /a/b;x\n"
        for cls in FETCHERS:
            f, http, _ = make(cls, {"https://gis.county.example/robots.txt": text(200, robots)})
            with self.assertRaises(rs.Unreachable, msg=cls.__name__):
                f.fetch("https://gis.county.example/a/b;x?f=json")
            self.assertEqual(data_calls(http), [], cls.__name__)

    def test_exception_recorded_only_after_a_read(self):
        # Round 2 F5: a host whose every path was refused is not named.
        for cls in FETCHERS:
            f, http, _ = make(cls, robots_403())
            with self.assertRaises(rs.Unreachable):
                f.fetch(LAYER + "?f=html")
            self.assertNotIn("exception", f.robots_meta[SVC], cls.__name__)
            self.assertEqual(rs.robots_exception_lines(f), [], cls.__name__)
            f.fetch(META)
            self.assertEqual(f.robots_meta[SVC]["exception"], EXC, cls.__name__)
            self.assertEqual(rs.robots_exception_lines(f), [f"robots: {SVC}: {EXC}"])

    def test_refused_path_on_an_accepted_host_makes_no_request(self):
        for url in (LAYER + "/applyEdits?f=json", LAYER.rsplit("/", 1)[0] + "?f=json",
                    LAYER + "?f=json&token=x"):
            f, http, _ = make(ai.AdjacencyFetcher, robots_403())
            with self.assertRaises(rs.Unreachable):
                f.fetch(url)
            self.assertEqual(data_calls(http), [], url)


# ---------------------------------------------------------------------------
# D1-08: spacing on one shared key
# ---------------------------------------------------------------------------
class SpacingTests(unittest.TestCase):
    def test_interleaved_services_hosts_are_spaced_one_second_in_total(self):
        l8 = "https://services8.arcgis.com/Zv/arcgis/rest/services/Z/FeatureServer/0?f=json"
        f, http, c = make(rs.PoliteFetcher, {**robots_403(), **robots_403("services8.arcgis.com")})
        for u in (META, l8, META, l8):
            f.fetch(u)
        gaps = [b - a for a, b in zip(http.at, http.at[1:])]
        self.assertEqual(len(http.calls), 6)
        self.assertTrue(all(g >= 1.0 for g in gaps), gaps)

    def test_zoning_session_shares_the_arcgis_key(self):
        # reviews/32, ZI-11: services3 then services8 through get_json.
        l8 = "https://services8.arcgis.com/Zv/arcgis/rest/services/Z/FeatureServer/0"
        f, http, c = make(zi.ZoningSession, {**robots_403(), **robots_403("services8.arcgis.com")})
        for u in (LAYER, l8, LAYER, l8):
            f.get_json(u, {"f": "pjson"})
        gaps = [b - a for a, b in zip(http.at, http.at[1:])]
        self.assertEqual(len(http.calls), 6)
        self.assertTrue(all(g >= 1.0 for g in gaps), gaps)

    def test_other_hosts_keep_their_own_key(self):
        f, http, c = make(rs.PoliteFetcher, {})
        for h in ("a.example", "b.example"):
            f._space(h)
        self.assertEqual(c.t, 1000.0)           # two hosts: no wait (RS-05 unchanged)
        f._space("services.arcgis.com")
        f._space("services7.arcgis.com")
        self.assertEqual(c.t, 1001.0)


# ---------------------------------------------------------------------------
# D1-07 / D1-10: outputs and the loop lock
# ---------------------------------------------------------------------------
class OutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="opdec-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _registry(self):
        import test_rules_scrape as trs
        reg = trs.base_registry()
        reg["hosts"][SVC] = {"terms_url": None, "terms_reviewed_by": "test",
                             "terms_reviewed_on": "2026-11-09", "note": "fixture"}
        reg["jurisdictions"]["south_fulton"]["change_signals"] = [
            {"key": "sf_zoning_layer", "enabled": True, "url": LAYER, "kind": "zoning"},
            {"key": "sf_cases", "enabled": True,
             "url": f"https://{SVC}/y2BJK2GUfoTwH7py/arcgis/rest/services/ZoningCases/"
                    "FeatureServer/0",
             "kind": "cases", "case_id_field": "Case_", "status_field": "STATUS",
             "approved_statuses": ["Approved"]}]
        p = self.tmp / "rules_sources.json"
        p.write_text(json.dumps(reg), encoding="utf-8")
        return trs, p

    def test_watch_reads_and_names_the_exception_once_per_host(self):
        trs, p = self._registry()
        routes = trs.routes_for(trs.ALL_PAGES)
        routes[f"https://{SVC}/robots.txt"] = trs.FakeResp(403, "Invalid URL",
                                                          {"Content-Type": "text/plain"})
        routes[META] = trs.FakeResp(200, json.dumps({"editingInfo": {"lastEditDate": 17}}),
                                    {"Content-Type": "application/json"})
        routes[QUERY] = trs.FakeResp(200, json.dumps({"objectIds": [4, 9]}),
                                     {"Content-Type": "application/json"})
        f, http, _ = trs.fetcher(routes)
        args = rs.build_parser().parse_args(["--out", str(self.tmp / "cand.json"), "--report",
                                             str(self.tmp / "report.md"), "--watch"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rs.run(args, fetcher=f, cache=rs.Cache(self.tmp / "cache"), registry_path=p,
                   loop_lock=self.tmp / "no-lock")
        out = buf.getvalue()
        self.assertEqual(out.count(f"robots: {SVC}: {EXC}"), 1, out)
        self.assertIn(META, http.calls)
        self.assertNotIn("?", "".join(l for l in out.splitlines() if l.startswith("robots:")))
        self.assertNotIn("objectIds", out)
        state = json.loads((self.tmp / "watch_state.json").read_text())
        self.assertEqual(state["robots_exceptions"], {SVC: EXC})
        self.assertEqual(state["gis"]["sf_zoning_layer"], [17, 2, 9])
        report = (self.tmp / "report.md").read_text()
        self.assertIn(f"| gis count/max id | sf_zoning_layer | unknown | read under D1 exception: {EXC} |",
                      report)
        self.assertNotIn("robots unavailable (HTTP 403)", report)

    def test_case_layers_still_skipped_while_the_loop_runs(self):
        import fcntl
        trs, p = self._registry()
        lock = self.tmp / ".experiment_loop.lock"
        lock.write_text("")
        fd = os.open(str(lock), os.O_WRONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertTrue(rs.loop_running(lock))
        reg = rs.load_registry(p)
        routes = {f"https://{SVC}/robots.txt": trs.FakeResp(403, "Invalid URL")}
        f, http, _ = trs.fetcher(routes)
        conn = trs.FakeConn([trs.stored_row("fulton-1", "south_fulton", "M-1")])
        fr = rs.compute_flags(conn, reg, f, loop_running=rs.loop_running(lock))
        self.assertEqual(fr.layer_failures,
                         ["sf_cases: not queried (experiment loop lock present)"])
        self.assertEqual(http.calls, [])
        args = rs.build_parser().parse_args(["--out", str(self.tmp / "cand.json"), "--report",
                                             str(self.tmp / "report.md"), "--watch"])
        f2, http2, _ = trs.fetcher({**trs.routes_for(trs.ALL_PAGES), **routes})
        with redirect_stdout(io.StringIO()):
            rs.run(args, fetcher=f2, cache=rs.Cache(self.tmp / "cache"), registry_path=p,
                   loop_lock=lock)
        self.assertFalse([u for u in http2.calls if SVC in u])

    def test_adjacency_import_names_the_exception_and_counts_copyright(self):
        import test_adjacency_import as tai
        from adjacency_helpers import enabled
        url = f"https://{SVC}/Org1/arcgis/rest/services/SF_Zoning/FeatureServer/0"
        src = tai.sources_file(self.tmp, [
            {"key": "city_zoning", "name": "City zoning", "role": "zoning", "kind": "overlay",
             "jurisdictions": ["south_fulton"], "url": url, "zoning_field": "ZClass",
             "desc_field": "ZClassDesc"}])
        layer = tai.Layer(n=3, robots=lambda u: tai.text(403, "Invalid URL"))
        f, http, _ = tai.fetcher(layer)
        conn = tai.FakeConn(tai.answer_factory())
        with enabled():
            rc, out = tai.run_tool(conn, sources_path=src, fetcher=f, check_open_run=False,
                                   log_path=self.tmp / "experiment_log.tsv")
        self.assertEqual(out.count(f"robots: {SVC}: {EXC}"), 1, out)
        self.assertIn("layers with a non-empty copyrightText: 0 of 1", out)
        self.assertIn("source city_zoning: 3 polygons", out)
        self.assertNotIn("?", "".join(l for l in out.splitlines() if l.startswith("robots:")))
        self.assertNotIn(tai.SECRET_PID, out)
        self.assertEqual(rc, ai.EXIT_OK)

    def test_zoning_import_names_the_exception_once_per_host(self):
        # reviews/32 (D1-11): zoning_import on the shared fetcher prints the
        # D1 line once per host read under it, without URL or query; a
        # readable FEMA-style disallow and a non-ArcGIS 403 refuse with no
        # data request.
        import test_zoning_import as tzi
        svc8 = "services8.arcgis.com"
        entries = tzi._entries()
        entries[2]["url"] = f"https://{SVC}/Org1/arcgis/rest/services/FB_Zoning/FeatureServer/0"
        entries.insert(3, dict(entries[2], key="fb2", name="Fairburn 2",
                               url=f"https://{svc8}/Org8/arcgis/rest/services/FB/FeatureServer/0"))
        r403 = tzi._Resp(b"Invalid URL", 403, {"Content-Type": "text/plain"})
        conn = tzi.StoreConn(tzi._parcels(), overlay=tzi._sf_overlay())
        code, out, http = tzi._run(conn, tzi.World(), sources=tzi._sources(entries),
                                   robots={SVC: r403, svc8: r403})
        self.assertEqual(code, 0, out)
        for h in (SVC, svc8):
            self.assertEqual(out.count(f"robots: {h}: {EXC}"), 1, out)
        rob = [l for l in out.splitlines() if l.startswith("robots:")]
        self.assertEqual(len(rob), 2)
        self.assertNotIn("?", "".join(rob))
        lines = out.splitlines()
        self.assertLess(max(lines.index(l) for l in rob),
                        [i for i, l in enumerate(lines) if l.startswith("requests: ")][0])
        self.assertGreater(min(lines.index(l) for l in rob),
                           [i for i, l in enumerate(lines) if l.startswith("written: ")][0])
        self.assertEqual(conn.committed["fulton-P3"][0], "M-2")
        for host, robots, reason in (
                (SVC, tzi._Resp(FEMA_ROBOTS.encode(), 200, {"Content-Type": "text/plain"}),
                 "robots.txt disallows this path for Land-Research-ZoningImport"),
                ("gis.county.example", r403, "robots unavailable (HTTP 403)")):
            ents = tzi._entries()
            ents[2]["url"] = f"https://{host}/arcgis/rest/services/FB/FeatureServer/0"
            conn = tzi.StoreConn(tzi._parcels(), overlay=tzi._sf_overlay())
            code, out, http = tzi._run(conn, tzi.World(), sources=tzi._sources(ents),
                                       robots={host: robots})
            self.assertEqual(code, 4, out)
            self.assertIn(f"source fb: FAILED, nothing used: {host}: {reason}", out)
            self.assertFalse([c for c in http.calls if host in c[0]], host)
            self.assertNotIn("robots: ", out)

    def test_hazards_import_names_the_exception(self):
        import test_hazards_import as thi
        from hazards_helpers import enabled
        f, http, _ = thi.fetcher(lambda u: thi.text(403, "Invalid URL")
                                 if urlparse(u).path == "/robots.txt" else thi.js({"layers": []}))
        nfhl = f"https://{SVC}/Org1/arcgis/rest/services/public/NFHL/MapServer"
        nwi = f"https://{SVC}/Org1/arcgis/rest/services/Wetlands/MapServer"
        conn = thi.FakeConn(thi.answer_factory())
        with enabled(), mock.patch.object(hi, "NFHL_SERVICE", nfhl), \
                mock.patch.object(hi, "NWI_SERVICE", nwi):
            rc, out = thi.run_tool(conn, mode="rest", fetcher=f, check_open_run=False,
                                   log_path=self.tmp / "experiment_log.tsv")
        self.assertEqual(out.count(f"robots: {SVC}: {EXC}"), 1, out)


# ---------------------------------------------------------------------------
# D2 / D4 pins (no code change)
# ---------------------------------------------------------------------------
class PinTests(unittest.TestCase):
    # Round 2 F6 pinned zoning_import's hosts while it stayed outside the
    # shared robots policy. D1-11 is closed (reviews/32_zoning_import_fetcher):
    # ZoningSession is a PoliteFetcher in FETCHERS above. The pin is kept:
    # every layer it can be pointed at is an ArcGIS REST layer, on an ArcGIS
    # Online services host or one of the two registered org hosts, so the D1
    # path guard admits each request it makes.
    ZONING_ORG_HOSTS = frozenset({"gismaps.fultoncountyga.gov", "gis.atlantaga.gov"})

    def test_zoning_import_hosts_are_arcgis_rest_only(self):
        import zoning_import as zi
        from urllib.parse import urlsplit
        snippet = json.loads((REPO_ROOT / "reviews/21_zoning/sources_json_snippet.json")
                             .read_text(encoding="utf-8"))
        fulton = json.loads((REPO_ROOT / "sources.json").read_text(encoding="utf-8"))[
            "county_parcel_data"]["fulton_ga"]
        srcs = zi.validate_zoning_sources(snippet["zoning_sources"])
        if "zoning_sources" in fulton:
            srcs += zi.validate_zoning_sources(fulton["zoning_sources"])
        urls = [f"{fulton['service_url'].rstrip('/')}/{int(fulton['parcel_layer_id'])}"]
        urls += [s.url for s in srcs]
        self.assertGreater(len(urls), 5)
        for u in urls:
            p = urlsplit(u)
            self.assertEqual(p.scheme, "https", u)
            self.assertFalse(p.query or p.fragment or p.port or "@" in p.netloc, u)
            self.assertTrue(rs.arcgis_exception_host(p.netloc) is not None
                            or p.hostname in self.ZONING_ORG_HOSTS, u)
            self.assertTrue(rs.arcgis_public_rest_path(p.hostname, p.path + "?f=json"), u)
            self.assertTrue(rs.arcgis_public_rest_path(p.hostname, p.path + "/query?f=json"), u)

    def test_flood_thresholds_are_the_human_values(self):
        hf = json.loads((REPO_ROOT / "parameters.json").read_text(encoding="utf-8"))["hard_filters"]
        self.assertEqual(hf["flood_zones_blocked"], ["A", "AE"])
        self.assertEqual(hf["flood_zones_flag_for_review"], ["AE_with_LOMR"])

    def test_terms_block_and_denylist_unchanged(self):
        self.assertEqual(rs.TERMS_BLOCKED_DOMAINS, frozenset({"zoneomics.com"}))
        self.assertEqual(rs.MIRROR_HOSTS, frozenset({"www.zoneomics.com", "zoneomics.com"}))
        self.assertEqual(rs._DENY_HOST_RE.pattern, r"(?:^|\.)municode\.com$|municode|mcclibrary")
        self.assertEqual(rs._DENY_HOSTS, frozenset({"mcclibrary.blob.core.usgovcloudapi.net"}))
        for host in ("www.zoneomics.com", "zoneomics.com"):
            self.assertIsNone(rs.arcgis_exception_host(host))
        f, http, _ = make(rs.PoliteFetcher, {})
        with self.assertRaises(rs.Unreachable):
            f.fetch("https://www.zoneomics.com/code/x")
        with self.assertRaises(rs.Unreachable):
            f.fetch("https://library.municode.com/ga/x")
        self.assertEqual(http.calls, [])


# ---------------------------------------------------------------------------
# D3: listed district without setbacks counts, with the investigate flag
# ---------------------------------------------------------------------------
_V = "zoning_import v1"
FB_PD = ("PD", " | ".join((_V, "fairburn", "fairburn_zoning", "PD", "-", "-")))
FB_R1 = ("R-1", " | ".join((_V, "fairburn", "fairburn_zoning", "R-1", "-", "-")))
D3_PARCELS = {"fulton-D01": ("atlanta__fb", 31.0, 900_000, *FB_PD, -84.611, 33.561),
              "fulton-D02": ("atlanta__fb", 31.0, 900_000, *FB_R1, -84.612, 33.562)}
D3_GEOMETRY = {"fulton-D01": ("rect", 1300.0, 1100.0), "fulton-D02": ("rect", 1300.0, 1100.0)}


def _unlisted_not_permitted(data):
    data["jurisdictions"]["fairburn"]["unlisted_districts"] = "not_permitted"


@contextmanager
def d3_fixture(open_gap=False):
    with mock.patch.dict(golden.PARCELS, D3_PARCELS), \
            mock.patch.dict(golden.GEOMETRY, D3_GEOMETRY):
        if not open_gap:
            yield golden.responder
            return
        real = golden.responder

        def responder(research_mod, **kw):
            inner = real(research_mod, **kw)

            def answer(sql, params):
                if sql in (research_mod._SQL_OPEN_DATA_GAP_KEYS_FOR_MARKET,
                           research_mod._SQL_OPEN_DATA_GAP_KEYS_FOR_PARCELS):
                    return [("fulton-D01", INVESTIGATE)]
                return inner(sql, params)
            return answer
        with mock.patch.object(golden, "responder", responder):
            yield responder


def _canon(x):
    return json.loads(json.dumps(x, sort_keys=True, default=str))


def _flags(rec, pid=None):
    return [p for s, p in rec["executed"] if s == pipeline_common._SQL_INSERT_FLAG
            and (pid is None or p[1] == pid)]


class D3ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with d3_fixture():
            site_fit._FIT_CACHE.clear()
            cls.draft = _canon(golden.run_case(research))
            with patch_rules(verified("fairburn", mutate=_unlisted_not_permitted)):
                site_fit._FIT_CACHE.clear()
                cls.ver = _canon(golden.run_case(research))
                conn = golden.zg.AnswerConn(golden.responder(research))
                site_fit._FIT_CACHE.clear()
                cls.proj = site_fit.project_parcels(conn, ["fulton-D01", "fulton-D02"])
        with d3_fixture(open_gap=True):
            with patch_rules(verified("fairburn", mutate=_unlisted_not_permitted)):
                site_fit._FIT_CACHE.clear()
                cls.again = _canon(golden.run_case(research))

    def bts(self, rec, pid):
        return rec["per_parcel"][pid]["result"]["strategy_fit"]["bts"]

    def test_listed_default_setback_district_gets_bts_from_the_projection(self):
        p = self.proj["fulton-D01"]
        self.assertTrue(site_fit.meets_bts_footprint(p))
        self.assertEqual(site_fit.bts_review_text(p), INVESTIGATE)
        self.assertNotEqual(self.bts(self.ver, "fulton-D01"), "N/A")
        cyc = {x["parcel_id"]: x["strategy_fit"] for x in self.ver["cycle"]["summary"]["parcels"]}
        self.assertEqual(cyc["fulton-D01"]["bts"], self.bts(self.ver, "fulton-D01"))

    def test_unlisted_district_stays_na(self):
        # D3-01: R-1 is unlisted in Fairburn; with unlisted_districts
        # 'not_permitted' it reaches the projection, and stays BTS N/A.
        p = self.proj["fulton-D02"]
        self.assertEqual(site_fit.max_building_sf(p), 520_000)
        self.assertFalse(site_fit.meets_bts_footprint(p))
        self.assertIsNone(site_fit.bts_review_text(p))
        self.assertEqual(self.bts(self.ver, "fulton-D02"), "N/A")
        self.assertFalse([f for f in _flags(self.ver["per_parcel"]["fulton-D02"])
                          if INVESTIGATE in f[3]])

    def test_flag_once_data_gap_never_a_block_on_both_paths(self):
        for rec in (self.ver["per_parcel"]["fulton-D01"], self.ver["cycle"]):
            mine = [f for f in _flags(rec, "fulton-D01") if INVESTIGATE in f[3]]
            self.assertEqual(len(mine), 1)
            self.assertEqual(mine[0][0], "data_gap")
            self.assertTrue(mine[0][3].endswith("; " + INVESTIGATE))
            self.assertFalse([f for f in _flags(rec) if f[0] == "actionability_block"])
        notes = self.ver["per_parcel"]["fulton-D01"]["research_log_notes"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].count(INVESTIGATE), 1)
        cyc_notes = [n for n in self.ver["cycle"]["research_log_notes"] if INVESTIGATE in n]
        self.assertEqual(len(cyc_notes), 1)

    def test_not_rewritten_while_open(self):
        for rec in (self.again["per_parcel"]["fulton-D01"], self.again["cycle"]):
            self.assertFalse([f for f in _flags(rec, "fulton-D01") if INVESTIGATE in f[3]])
        self.assertEqual(self.bts(self.again, "fulton-D01"), self.bts(self.ver, "fulton-D01"))

    def test_draft_is_unchanged(self):
        for pid in D3_PARCELS:
            rec = self.draft["per_parcel"][pid]
            self.assertFalse([f for f in _flags(rec) if "investigate" in f[3]], pid)
            self.assertFalse([n for n in rec["research_log_notes"] if "investigate" in n], pid)
        self.assertFalse([n for n in self.draft["cycle"]["research_log_notes"]
                          if "investigate" in n])

    def test_memo_lines(self):
        p = self.proj["fulton-D01"]
        want = f"yes (default setback; {INVESTIGATE})"
        self.assertEqual(reporting._bts_use_text(p, True), want)
        self.assertIn(f"used for BTS: {want};", reporting._projection_summary(p, True))
        u = self.proj["fulton-D02"]
        self.assertEqual(reporting._bts_use_text(u, True),
                         "yes, rated N/A (default setback is not a verified rule)")
        self.assertIn("used for BTS: yes, rated N/A (default setback);",
                      reporting._projection_summary(u, True))
        with d3_fixture():
            conn = golden.zg.AnswerConn(golden.responder(research))
            site_fit._FIT_CACHE.clear()
            d = site_fit.project_parcels(conn, ["fulton-D01"])["fulton-D01"]
        self.assertEqual(reporting._bts_use_text(d, False), "no (draft rules)")
        self.assertNotIn("investigate", reporting._projection_summary(d, False))
        self.assertNotIn("investigate", " ".join(d.constraints))


if __name__ == "__main__":
    unittest.main()
