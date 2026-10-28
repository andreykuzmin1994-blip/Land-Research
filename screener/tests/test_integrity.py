"""Integrity tests: a bad or partial map answer stops the run instead of
reading as "clear", failed checks are counted in summary.md, and county
strings can't break the map page or the brief file names.

Offline: every HTTP call goes to a fake session, the cache and all outputs
to a temp dir, and backoff sleeps are skipped.

    python -m unittest discover -s screener/tests -t .
"""

from __future__ import annotations

import contextlib
import copy
import csv
import io
import json
import math
import re
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import requests
from shapely.geometry import box
from shapely.geometry.polygon import orient

from screener import arcgis, checks, fulton, layers, report, run, scoring, screens

SETTINGS_FILE = Path(run.__file__).resolve().parent / "settings.toml"
REAL_LOAD_CAMA = fulton.load_cama
LAYER = layers.LAYERS["flood"]
QUERY = f"{LAYER.url}/query"
ENV = (-84.55, 33.55, -84.5, 33.6)
A, B = (-84.5, 33.6), (-84.48, 33.6)  # south-west corners of the two test parcels

GOOD_SLOPE = {
    "histograms": [{"size": 4, "min": 250.0, "max": 262.0, "counts": [10, 20, 20, 10]},
                   {"size": 4, "min": 0.0, "max": 20.0, "counts": [30, 20, 8, 2]}],
    "statistics": [{"mean": 256.0}, {"mean": 6.0}],
}
EMPTY_SLOPE = {"statistics": []}  # a real 3DEP answer (HTTP 200) seen in the cache


class FakeResponse:
    def __init__(self, body=None, status=200, content=b""):
        self.body, self.status_code, self.content = body, status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return copy.deepcopy(self.body)

    def iter_content(self, size):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Answers each URL from its route; any other request fails the test."""

    def __init__(self, routes, downloads=None):
        self.routes, self.downloads = routes, downloads or {}
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def post(self, url, data=None, timeout=None):
        with self._lock:
            self.calls.append((url, dict(data or {})))
        if url not in self.routes:
            raise AssertionError(f"unexpected request to {url}")
        answer = self.routes[url](dict(data or {}))
        return answer if isinstance(answer, FakeResponse) else FakeResponse(answer)

    def get(self, url, stream=False, timeout=None):
        with self._lock:
            self.calls.append((url, {}))
        if url not in self.downloads:
            raise AssertionError(f"unexpected download of {url}")
        return self.downloads[url]()

    def count(self, url):
        return sum(1 for u, _ in self.calls if u == url)


_DEFAULT = object()


class FakeLayer:
    """One ArcGIS layer: answers id queries and feature fetches by object id."""

    def __init__(self, feats=None, ids_answer=_DEFAULT, short_by=0, short_times=0,
                 extra=None):
        self.feats = dict(feats or {})
        self.ids_answer = ids_answer
        # Drop short_by features from the first short_times feature answers (-1: all).
        self.short_by, self.short_times = short_by, short_times
        self.extra = extra or {}
        self.id_calls = self.feature_calls = 0

    def __call__(self, params):
        if params.get("returnIdsOnly") == "true":
            self.id_calls += 1
            if self.ids_answer is not _DEFAULT:
                return copy.deepcopy(self.ids_answer)
            return {"objectIdFieldName": "OBJECTID", "objectIds": sorted(self.feats)}
        self.feature_calls += 1
        wanted = sorted({int(i) for i in params["objectIds"].split(",")})
        out = [self.feats[i] for i in wanted if i in self.feats]
        if self.short_by and (self.short_times < 0 or self.feature_calls <= self.short_times):
            out = out[self.short_by:]
        return {"features": out, "spatialReference": {"wkid": 4326, "latestWkid": 4326},
                **self.extra}


def square(lon, lat, side_m=285.0):
    """About 20 acres with its south-west corner at (lon, lat)."""
    dlon = side_m / (111_320 * math.cos(math.radians(lat)))
    return box(lon, lat, lon + dlon, lat + side_m / 110_574)


def polygon_feature(geom, **attrs):
    ring = [list(xy) for xy in orient(geom, sign=-1.0).exterior.coords]
    return {"attributes": attrs, "geometry": {"rings": [ring]}}


def point_feature(x, y, **attrs):
    return {"attributes": attrs, "geometry": {"x": x, "y": y}}


def small_polygons(n, start=1):
    return {i: polygon_feature(box(-84.52 + i * 1e-4, 33.56, -84.52 + i * 1e-4 + 5e-5, 33.5601), N=i)
            for i in range(start, start + n)}


def id_params(where, env):
    """What query_ids sends (the cache key depends on it)."""
    return {"where": where, "returnIdsOnly": "true", **arcgis._envelope_params(env), "f": "json"}


def world():
    """One feature per map layer near the test parcels, so no layer is empty."""
    far = box(-84.45, 33.65, -84.449, 33.651)
    return {
        "flood": FakeLayer({1: polygon_feature(box(A[0], A[1], A[0] + 0.0005, A[1] + 0.0005),
                                               FLD_ZONE="AE", ZONE_SUBTY="")}),
        "wetlands": FakeLayer({1: polygon_feature(far, ATTRIBUTE="PFO1A",
                                                  WETLAND_TYPE="Freshwater Forested/Shrub Wetland")}),
        "easements": FakeLayer({1: polygon_feature(far, sitename="TEST EASEMENT")}),
        "protected": FakeLayer({1: polygon_feature(far, Category="Fee", Unit_Nm="TEST PARK")}),
        "oz": FakeLayer({1: polygon_feature(far, GEOID10="13121000000")}),
        "epa": FakeLayer({1: point_feature(-84.40, 33.65, LOCATION_ADDRESS="1 TEST SITE RD",
                                           CAT_NAME="Brownfields")}),
        "hsi": FakeLayer({1: point_feature(-84.41, 33.66, Facility_Name="TEST HSI SITE")}),
        "exits": FakeLayer({1: point_feature(-84.49, 33.61, FED_ASSIGNED_INTERSTATE_NUMBER="85",
                                             SIGNED_EXIT_NUMBER="64")}),
        "rail": FakeLayer({1: {"attributes": {"RROWNER1": "TEST RAILROAD", "NET": "M"},
                               "geometry": {"paths": [[[-84.42, 33.5], [-84.42, 33.7]]]}}}),
    }


def county_parcel(pid, corner, jurisdiction="College Park", owner="TEST OWNER ALPHA"):
    """A parcel as fulton.fetch_parcels returns it (synthetic owner)."""
    return {
        "parcel_id": pid, "county": "Fulton", "geometry": square(*corner),
        "site_address": f"{pid} TEST RD", "jurisdiction": jurisdiction, "acres": 20.0,
        "class_code": "R5", "land_use_code": "", "exempt_code": "",
        "land_value": 400000.0, "improvement_value": 0.0, "total_value": 400000.0,
        "owner": owner, "owner_mailing": "1 TEST ST, ATLANTA GA 30303",
        "owner_type": "individual", "industrial_named": False, "owner_state": "GA",
        "owner_po_box": False, "assessor_portal": "https://example.invalid/",
    }


def cama_zip(rows=1, sales=True):
    lines = ["P1\t2001-08-10 00:00:00\t90000\tWarranty Deed\tTEST SELLER\n"]
    lines += [f"X{i:06d}\t2001-01-01\t{i * 7 % 99991}\tWarranty Deed\tTEST SELLER {i}\n"
              for i in range(rows - 1)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if sales:
            z.writestr("Tax_Sales.txt", "ParcelID\tSaleDate\tPrice\tInstrType\tOldOwner\n"
                       + "".join(lines))
        z.writestr("Tax_ParcelSupplement.txt", "ParcelID\tStreet1\tTopo\tWater\tSewer\tFronting\n"
                   "P1\tPaved\tLevel\tAll Public\tUnderground\t\n")
    return buf.getvalue()


def corrupt(data):
    """Damage the first member's compressed bytes; the zip directory stays intact."""
    data = bytearray(data)
    data[200:260] = b"\xff" * 60
    return bytes(data)


class Offline(unittest.TestCase):
    """No network, no sleeps; cache, decisions file and outputs in a temp dir."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.patch(arcgis, "CACHE_DIR", self.tmp / "http")
        self.patch(arcgis, "cache_days", 7.0)
        self.patch(arcgis.time, "sleep", lambda s: None)
        self.patch(run, "DECISIONS_FILE", self.tmp / "decisions.csv")
        # A request that misses the fake session fails the test.
        self.patch(requests.Session, "request",
                   mock.Mock(side_effect=AssertionError("network call in an offline test")))
        self.session = None

    def patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)

    def serve(self, routes, downloads=None):
        session = FakeSession(routes, downloads)
        self.patch(arcgis, "_session", lambda: session)
        self.session = session
        return session

    def cached(self):
        """Every answer in the temp cache."""
        return [json.loads(f.read_text()) for f in (self.tmp / "http").rglob("*.json")]


class ZeroMatchTests(Offline):
    """No matches is a normal answer; an answer without an id list is not."""

    def check_no_matches(self, answer):
        server = FakeLayer(ids_answer=answer)
        self.serve({QUERY: server})
        self.assertEqual(arcgis.query_ids(LAYER.url, LAYER.where, ENV), [])
        self.assertEqual(len(layers.fetch(LAYER, [ENV], ENV)), 0)
        self.assertEqual(server.feature_calls, 0)

    def test_mapserver_null_means_no_matches(self):
        # Live USFWS wetlands MapServer answer to an empty envelope, 2026-10-28.
        self.check_no_matches({"objectIdFieldName": "OBJECTID", "objectIds": None})

    def test_hosted_empty_list_means_no_matches(self):
        # Live hosted FEMA flood layer answer to an empty envelope, 2026-10-28.
        self.check_no_matches({"objectIdFieldName": "OBJECTID", "objectIds": []})

    def test_answer_without_a_usable_id_list_raises(self):
        for answer in ({"objectIdFieldName": "OBJECTID"},
                       {"objectIdFieldName": "OBJECTID", "objectIds": "1,2"},
                       {"objectIdFieldName": "OBJECTID", "objectIds": {"a": 1}},
                       {"objectIdFieldName": "OBJECTID", "objectIds": [1, 2],
                        "exceededTransferLimit": True}):
            with self.subTest(answer=answer):
                server = FakeLayer(ids_answer=answer)
                self.serve({QUERY: server})
                with self.assertRaises(arcgis.ArcGISError):
                    arcgis.query_ids(LAYER.url, LAYER.where, ENV)
                self.assertEqual(server.id_calls, 2)  # asked once more, then reported
        self.assertEqual(self.cached(), [])

    def test_non_object_json_raises_arcgis_error(self):
        for body in (None, [1, 2]):
            with self.subTest(body=body):
                self.serve({QUERY: lambda params, body=body: body})
                with self.assertRaises(arcgis.ArcGISError):
                    arcgis.query_ids(LAYER.url, LAYER.where, ENV)
        self.assertEqual(self.cached(), [])


class CacheTests(Offline):
    def seed(self, text):
        path = arcgis._cache_path(QUERY, id_params(LAYER.where, ENV))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_bad_cached_answer_is_asked_again(self):
        path = self.seed(json.dumps({"objectIdFieldName": "OBJECTID"}))
        server = FakeLayer({7: polygon_feature(box(-84.52, 33.56, -84.51, 33.57))})
        self.serve({QUERY: server})
        self.assertEqual(arcgis.query_ids(LAYER.url, LAYER.where, ENV), [7])
        self.assertEqual(server.id_calls, 1)
        self.assertEqual(json.loads(path.read_text())["objectIds"], [7])

    def test_damaged_cache_file_is_asked_again(self):
        self.seed('{"objectIdFieldName": "OBJ')
        server = FakeLayer({7: polygon_feature(box(-84.52, 33.56, -84.51, 33.57))})
        self.serve({QUERY: server})
        self.assertEqual(arcgis.query_ids(LAYER.url, LAYER.where, ENV), [7])

    def test_good_answers_keep_their_requests_and_cache_keys(self):
        session = self.serve({QUERY: FakeLayer(small_polygons(2))})
        self.assertEqual(len(layers.fetch(LAYER, [ENV], ENV)), 2)
        self.assertEqual(session.calls[0][1], id_params(LAYER.where, ENV))
        self.assertEqual(session.calls[1][1], {
            "objectIds": "1,2", "outFields": LAYER.fields, "returnGeometry": "true",
            "outSR": 4326, "maxAllowableOffset": LAYER.max_offset, "f": "json"})
        # Same keys as before this change, so existing caches stay warm.
        self.assertEqual(arcgis._cache_path(QUERY, session.calls[0][1]).name,
                         "4458ef04f6b0a6721a0eb0d397ffed6b9050b2905c291ac1e960e2aae552a2a0.json")
        self.assertEqual(arcgis._cache_path(QUERY, session.calls[1][1]).name,
                         "8c1d5140ecb72acba40dd801da537841cbf5170e8299fbdd1f4d6fa01450704b.json")
        self.assertEqual(len(layers.fetch(LAYER, [ENV], ENV)), 2)
        self.assertEqual(len(session.calls), 2)  # second fetch came from the cache


class PartialFetchTests(Offline):
    def test_transient_short_chunk_recovers_and_is_not_cached(self):
        server = FakeLayer(small_polygons(3), short_by=1, short_times=1)
        self.serve({QUERY: server})
        self.assertEqual(len(layers.fetch(LAYER, [ENV], ENV)), 3)
        self.assertEqual(server.feature_calls, 2)
        self.assertEqual([len(a["features"]) for a in self.cached() if "features" in a], [3])

    def test_persistent_short_chunk_raises_after_bounded_tries(self):
        server = FakeLayer(small_polygons(3), short_by=1, short_times=-1)
        self.serve({QUERY: server})
        with self.assertRaises(arcgis.ArcGISError) as ctx:
            layers.fetch(LAYER, [ENV], ENV)
        self.assertIn("2 of 3", str(ctx.exception))
        self.assertIn("--refresh", str(ctx.exception))
        self.assertLessEqual(server.id_calls + server.feature_calls, 6)
        self.assertEqual([a for a in self.cached() if "features" in a], [])

    def test_transfer_limit_counts_as_short(self):
        server = FakeLayer(small_polygons(3), extra={"exceededTransferLimit": True})
        self.serve({QUERY: server})
        with self.assertRaises(arcgis.ArcGISError):
            layers.fetch(LAYER, [ENV], ENV)

    def test_stale_cached_ids_recover(self):
        self.serve({QUERY: FakeLayer(small_polygons(3))})
        arcgis.query_ids(LAYER.url, LAYER.where, ENV)  # cached ids 1-3
        # The layer is re-published and renumbered: ids 1-3 no longer exist.
        self.serve({QUERY: FakeLayer(small_polygons(3, start=11))})
        self.assertEqual(len(layers.fetch(LAYER, [ENV], ENV)), 3)
        self.serve({})  # the fresh id list replaced the stale one in the cache
        self.assertEqual(arcgis.query_ids(LAYER.url, LAYER.where, ENV), [11, 12, 13])

    def test_duplicate_ids_are_counted_once(self):
        self.serve({QUERY: FakeLayer(small_polygons(2))})
        self.assertEqual(len(arcgis.fetch_by_ids(LAYER.url, [1, 1, 2], LAYER.fields)), 2)

    def test_features_without_geometry_raise(self):
        server = FakeLayer({1: {"attributes": {"N": 1}, "geometry": None},
                            2: {"attributes": {"N": 2}}})
        self.serve({QUERY: server})
        with self.assertRaises(arcgis.ArcGISError) as ctx:
            layers.fetch(LAYER, [ENV], ENV)
        # This answer is cached (it passes arcgis's checks), so say --refresh.
        self.assertIn("--refresh", str(ctx.exception))

    def test_features_in_another_projection_raise(self):
        server = FakeLayer(small_polygons(2),
                           extra={"spatialReference": {"wkid": 102100, "latestWkid": 3857}})
        self.serve({QUERY: server})
        with self.assertRaises(arcgis.ArcGISError):
            layers.fetch(LAYER, [ENV], ENV)


class ZoningSourceTests(Offline):
    def test_bad_answer_is_a_failed_zoning_check(self):
        src = {"name": "Test zoning", "kind": "overlay", "jurisdictions": {"Atlanta"},
               "url": "https://example.invalid/arcgis/rest/services/Zoning/MapServer/0",
               "zoning_field": "ZONECLASS"}
        self.serve({src["url"] + "/query": FakeLayer(ids_answer={"objectIdFieldName": "OBJECTID"})})
        p = county_parcel("P1", A, jurisdiction="Atlanta")
        with contextlib.redirect_stdout(io.StringIO()):
            checks.zoning_checks([p], [src], lambda group: [ENV])
        self.assertEqual(p["zoning_problem"], "Test zoning was unavailable this run")
        self.assertEqual(p["entitlement"], "unknown")
        self.assertIn("zoning check failed", p["entitlement_note"])


class SlopeTests(Offline):
    def slope(self, answer):
        self.serve({checks.SLOPE_URL: lambda params: copy.deepcopy(answer)})
        p = {"parcel_id": "P1", "geometry": square(*A)}
        checks.slope_checks([p], workers=1)
        return p

    def test_empty_answer_flags_the_parcel(self):
        p = self.slope(EMPTY_SLOPE)
        self.assertIsNone(p["steep_pct"])
        self.assertTrue(p.get("slope_error"))
        self.assertIn("slope check failed for this parcel", screens.flags(p))
        self.assertLessEqual(self.session.count(checks.SLOPE_URL), 2)
        self.assertEqual(self.cached(), [])

    def test_unreadable_histograms_do_not_crash(self):
        no_min = copy.deepcopy(GOOD_SLOPE)
        del no_min["histograms"][1]["min"]
        null_counts = copy.deepcopy(GOOD_SLOPE)
        null_counts["histograms"][0]["counts"] = [None, 3]
        no_pixels = copy.deepcopy(GOOD_SLOPE)
        no_pixels["histograms"][1]["counts"] = [0, 0, 0, 0]
        for answer in (no_min, null_counts, no_pixels):
            with self.subTest(answer=answer):
                p = self.slope(answer)
                self.assertTrue(p.get("slope_error"))
                self.assertIsNone(p["steep_pct"])

    def test_good_answer(self):
        p = self.slope(GOOD_SLOPE)
        self.assertNotIn("slope_error", p)
        self.assertEqual(p["steep_pct"], 3.3)  # 2 of 60 pixels above 15%
        self.assertEqual(self.session.count(checks.SLOPE_URL), 1)


class CamaTests(Offline):
    def setUp(self):
        super().setUp()
        self.cache = self.tmp / "cache"
        self.zip_path = self.cache / "fulton_cama.zip"

    def test_non_zip_download_is_not_kept(self):
        session = self.serve({}, {fulton.CAMA_URL: lambda: FakeResponse(
            content=b"<html>down for maintenance</html>")})
        with self.assertRaises(arcgis.ArcGISError):
            fulton.read_cama(self.cache, {"P1"})
        self.assertEqual(list(self.cache.iterdir()), [])  # no zip, no .part
        self.assertLessEqual(session.count(fulton.CAMA_URL), 4)

    def test_damaged_cached_zip_is_downloaded_again(self):
        self.cache.mkdir()
        self.zip_path.write_bytes(b"not a zip")
        self.serve({}, {fulton.CAMA_URL: lambda: FakeResponse(content=cama_zip())})
        sales, supplement = fulton.read_cama(self.cache, {"P1"})
        self.assertEqual(len(sales["P1"]), 1)
        self.assertEqual(supplement["P1"]["water"], "All Public")

    def test_unreadable_zip_is_a_clean_error(self):
        self.cache.mkdir()
        for name, data in (("missing member", cama_zip(sales=False)),
                           ("damaged member", corrupt(cama_zip(rows=5000)))):
            with self.subTest(name):
                self.zip_path.write_bytes(data)
                self.serve({}, {})  # a fresh cached zip is not downloaded again
                with self.assertRaises(arcgis.ArcGISError) as ctx:
                    fulton.read_cama(self.cache, {"P1"})
                self.assertIn("--refresh", str(ctx.exception))


class RunTests(Offline):
    """run.main end to end on two synthetic parcels."""

    def setUp(self):
        super().setUp()
        self.patch(run, "HERE", self.tmp)  # deed-file cache goes to the temp dir
        self.out = self.tmp / "out"
        self.parcels = [county_parcel("14F0001 0001", A),
                        county_parcel("14F0002 0002", B, owner="TEST OWNER BETA")]
        self.fetch_parcels = mock.Mock(side_effect=lambda *a, **k: self.parcels)
        self.load_cama = mock.Mock(return_value={})
        self.industrial = mock.Mock(return_value=[(-84.49, 33.605)])
        self.patch(fulton, "fetch_parcels", self.fetch_parcels)
        self.patch(fulton, "load_cama", self.load_cama)
        self.patch(fulton, "fetch_industrial_points", self.industrial)
        self.routes = {f"{layers.LAYERS[k].url}/query": v for k, v in world().items()}
        self.routes[checks.SLOPE_URL] = lambda params: GOOD_SLOPE
        self.downloads = {}

    def set_layer(self, key, fake):
        self.routes[f"{layers.LAYERS[key].url}/query"] = fake

    def main(self, *args):
        self.serve(self.routes, self.downloads)
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            code = run.main(["--settings", str(SETTINGS_FILE), "--out", str(self.out), *args])
        return code, log.getvalue()

    def notes(self):
        return (self.out / "summary.md").read_text().split("## Notes", 1)[1]

    def assert_stopped(self, code, log):
        self.assertEqual(code, 1)
        self.assertIn("Stopping:", log)
        self.assertNotIn("Traceback", log)
        self.assertFalse(self.out.exists())
        self.assertNotIn("TEST OWNER", log)

    def test_clean_run_has_no_failure_notes(self):
        code, _ = self.main()
        self.assertEqual(code, 0)
        notes = self.notes()
        self.assertNotIn("FAILED", notes)
        for layer in layers.LAYERS.values():
            self.assertNotIn(layer.name, notes)

    def test_parcel_layer_failure_stops_cleanly(self):
        self.fetch_parcels.side_effect = arcgis.ArcGISError(
            "giving up after 5 tries: https://example.invalid/MapServer/11/query (HTTP 503)")
        self.assert_stopped(*self.main("--no-slope"))

    def test_deed_file_download_failure_stops_cleanly(self):
        self.load_cama.side_effect = REAL_LOAD_CAMA
        self.downloads[fulton.CAMA_URL] = lambda: FakeResponse(status=503)
        self.assert_stopped(*self.main("--no-slope"))
        self.assertFalse((self.tmp / "cache" / "fulton_cama.zip").exists())

    def test_damaged_deed_file_stops_cleanly(self):
        self.load_cama.side_effect = REAL_LOAD_CAMA
        self.downloads[fulton.CAMA_URL] = lambda: FakeResponse(content=corrupt(cama_zip(rows=5000)))
        code, log = self.main("--no-slope")
        self.assert_stopped(code, log)
        self.assertIn("--refresh", log)

    def test_industrial_parcels_failure_stops_cleanly(self):
        self.industrial.side_effect = arcgis.ArcGISError("download failed")
        self.assert_stopped(*self.main("--no-slope"))

    def test_malformed_map_layer_stops_the_run(self):
        self.set_layer("flood", FakeLayer(ids_answer={"objectIdFieldName": "OBJECTID"}))
        code, log = self.main("--no-slope")
        self.assert_stopped(code, log)
        self.assertIn(LAYER.name, log)

    def test_short_map_layer_stops_the_run(self):
        wetlands = world()["wetlands"]
        wetlands.short_by, wetlands.short_times = 1, -1
        self.set_layer("wetlands", wetlands)
        self.assert_stopped(*self.main("--no-slope"))

    def test_empty_layers_finish_with_a_note(self):
        # The observed failure: valid-but-empty flood and wetland answers.
        self.set_layer("flood", FakeLayer(ids_answer={"objectIdFieldName": "OBJECTID",
                                                      "objectIds": []}))
        self.set_layer("wetlands", FakeLayer(ids_answer={"objectIdFieldName": "OBJECTID",
                                                         "objectIds": None}))
        self.industrial.return_value = []
        code, _ = self.main("--no-slope")
        self.assertEqual(code, 0)
        notes = self.notes()
        self.assertIn(layers.LAYERS["flood"].name, notes)
        self.assertIn(layers.LAYERS["wetlands"].name, notes)
        self.assertIn("industrial parcels", notes)
        self.assertNotIn(layers.LAYERS["exits"].name, notes)
        self.assertNotIn("TEST OWNER", notes)

    def test_failed_slope_checks_are_counted(self):
        def slope(params):
            west = json.loads(params["geometry"])["rings"][0][0][0]
            return EMPTY_SLOPE if west > -84.49 else GOOD_SLOPE  # parcel B fails
        self.routes[checks.SLOPE_URL] = slope
        code, _ = self.main()
        self.assertEqual(code, 0)
        notes = self.notes()
        self.assertIn("SLOPE CHECK FAILED for 1 of 2 parcels", notes)
        self.assertIn("steep-ground screen was not applied", notes)
        self.assertNotIn("TEST OWNER", notes)
        with open(self.out / "ranked.csv", newline="") as fh:
            flags = {r["parcel_id"]: r["flags"] for r in csv.DictReader(fh)}
        self.assertIn("slope check failed", flags["14F0002 0002"])
        self.assertNotIn("slope check failed", flags["14F0001 0001"])
        b_calls = [c for u, c in self.session.calls if u == checks.SLOPE_URL
                   and json.loads(c["geometry"])["rings"][0][0][0] > -84.49]
        self.assertLessEqual(len(b_calls), 2)

    def test_malformed_zoning_answer_is_a_failed_zoning_check(self):
        self.parcels = [county_parcel("14F0001 0001", A, jurisdiction="Atlanta")]
        zoning, flu = [s for s in fulton.ZONING_SOURCES if "Atlanta" in s["jurisdictions"]]
        self.routes[f"{zoning['url']}/query"] = FakeLayer(ids_answer={"objectIdFieldName": "OBJECTID"})
        self.routes[f"{flu['url']}/query"] = FakeLayer(ids_answer={"objectIdFieldName": "OBJECTID",
                                                                   "objectIds": None})
        code, _ = self.main("--no-slope")
        self.assertEqual(code, 0)
        notes = self.notes()
        self.assertIn(f"ZONING CHECK FAILED for 1 parcels: {zoning['name']} was unavailable", notes)
        self.assertNotIn(f"{flu['name']} was unavailable", notes)


class MapPageTests(unittest.TestCase):
    def test_county_strings_cannot_break_out_of_the_script(self):
        props = {"rank": 1, "score": 50.0, "owner": "</script><script>alert(1)</script><!--",
                 "site_address": "1 </SCRIPT > RD", "zoning": "M-1 & <!-- B",
                 "owner_type": "SMITH & SONS", "flags": "line sep end"}
        features = [{"type": "Feature", "properties": props,
                     "geometry": {"type": "Point", "coordinates": [-84.5, 33.6]}}]
        page = report._map_html(features, {"county": "Fulton", "run_date": "2026-10-28"})
        self.assertEqual(len(re.findall(r"</script", page, re.I)), 2)
        inline = page.split("<script>", 1)[1].rsplit("</script>", 1)[0]
        self.assertNotIn("<!--", inline)
        data = re.search(r"^const data = (.*);$", inline, re.M).group(1)
        self.assertEqual(json.loads(data), {"type": "FeatureCollection", "features": features})


class BriefNameTests(unittest.TestCase):
    def test_parcel_ids_cannot_escape_the_briefs_folder(self):
        ids = ["07 030000811192", "a/b", "../../x", "..", "a\\b", 'x:y*?"<>|', "Z" * 300,
               "Ñandú 1", "nul\x00byte"]
        ranked = []
        for rank, pid in enumerate(ids, 1):
            p = county_parcel(pid, A)
            p.update({"rank": rank, "lat": A[1], "lon": A[0], "usable_acres": 18.0, "flags": []})
            p.update(scoring.score(p))
            ranked.append(p)
        summary = {"county": "Fulton", "run_date": "2026-10-28", "funnel": [("ranked", len(ids))],
                   "reject_reasons": [], "notes": []}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            report.write_all(out, ranked, [], summary, top_n=len(ranked))
            briefs = out / "briefs"
            written = sorted(briefs.iterdir())
            self.assertEqual(len(written), len(ids))
            self.assertTrue(all(f.is_file() and f.parent == briefs for f in written))
            self.assertTrue(all(len(f.name) <= 120 for f in written))
            # Ids with only spaces keep the names they had before.
            self.assertTrue((briefs / "001_07_030000811192.md").is_file())
            self.assertEqual({p.relative_to(tmp).parts[0] for p in Path(tmp).rglob("*")}, {"out"})


if __name__ == "__main__":
    unittest.main()
