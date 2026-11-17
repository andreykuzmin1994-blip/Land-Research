"""Site planner v4 port (reviews/37_screener_site_planner): offline tests.

Hermetic: requests and the arcgis session raise, the arcgis cache points
at a temp folder, and every site is synthetic (axis-aligned rectangles a
few acres in size near Atlanta) or a trimmed, owner-free fixture of a
reference site. For the synthetic rectangles the fit search's angle list
is cut to 0 and 90 degrees (the only angles an axis-aligned rectangle
needs), which keeps a plan under a second.
"""

from __future__ import annotations

import ast
import csv
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import requests
import shapely
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box, mapping, shape

from screener import arcgis, frontage, layers, sitefit
from screener import siteplan as sp
from screener import siteplan_inputs as si
from screener import siteplan_render as sr
from screener.geo import FT_PER_M, LocalProjection

HERE = Path(__file__).resolve().parent
SCREENER = HERE.parent
FIXTURES = HERE / "fixtures" / "siteplan"
LON0, LAT0 = -84.55, 33.60
PJ = LocalProjection(LON0, LAT0)
OWNER = "TEST OWNER ALPHA LLC"
STATES = ("layers", "transmission", "neighbours", "frontage", "dem")


def wgs(g_ft):
    def inv(c):
        o = c / FT_PER_M
        o[:, 0] = o[:, 0] / PJ.kx + LON0
        o[:, 1] = o[:, 1] / PJ.ky + LAT0
        return o
    return shapely.transform(g_ft, inv)


def local(g_ft, g_site):
    """A test-frame feet shape in the planner's plane for the site."""
    return sp.Plane(g_site).ft(wgs(g_ft))


def flat(x, y):
    return 900.0


def site(w=600, h=700, *, shapes=None, front=None, front_state="checked", road="local road, test",
         z=flat, dem_state="checked", jur="South Fulton / unincorporated", hsg="B", zoning="M-1",
         states=None, assembled=False, **kw):
    """A synthetic site: a w x h ft rectangle (or the given feet shapes),
    frontage along y=0 unless `front` (a feet line, or None for none)."""
    g_ft = shapely.union_all(shapes) if shapes else box(0, 0, w, h)
    g = wgs(g_ft)
    plane = sp.Plane(g)
    parcel, _ = sp.site_parcel(plane, g, assembled, sp.PlanOptions())
    grid = sp.dem_points(parcel)
    back = PJ(plane.wgs(shapely.MultiPoint([p for _, p in grid]))) if grid else None
    here = [(c.x * FT_PER_M, c.y * FT_PER_M) for c in back.geoms] if grid else []
    dem = sp.Dem(points=dict(grid), elev={} if dem_state == "not_checked" else {ij: z(*q) for (ij, _), q in zip(grid, here)},
                 state=dem_state)
    if dem_state == "partial":
        dem.elev = dict(list(dem.elev.items())[: len(dem.elev) // 2])
    if front is None and front_state == "checked":
        front = LineString([(0, 0), (w, 0)])
    if front_state == "checked":
        fr = sp.FrontageInput("checked", wgs(front), int(front.length), road)
    elif front_state == "none":
        fr = sp.FrontageInput("none", None, 0, None, "no GDOT road within about 500 ft")
    else:
        fr = sp.FrontageInput("not_checked", None, None, None, "road service failed (test)")
    st = {k: sp.InputState("checked") for k in STATES}
    st["frontage"] = sp.InputState(fr.state, fr.reason if fr.state == "not_checked" else "")
    st["dem"] = sp.InputState(dem_state, "test" if dem_state != "checked" else "")
    st.update(states or {})
    return sp.SiteInputs(kw.pop("key", "t1"), kw.pop("rank", "1"), kw.pop("title", "#1 1 TEST RD, test"), kw.pop("parcel_ids", ["T1"]), g,
                         {"jurisdiction": jur, "hsg": hsg, "zoning": zoning}, kw.pop("tax_acres", None), assembled=assembled,
                         frontage=fr, dem=dem, states=st, **kw)


def names(rec, sev=None):
    return {f["flag"] for s, fl in rec["flags"].items() if sev is None or int(s) == sev for f in fl}


def assert_invariant(tc, rec):
    """R4: feasible follows the flags (default behaviour)."""
    blocking = {f["flag"] for f in rec["flags"][1]} - sp.GATE_FLAGS
    if names(rec) & sp.NOT_CHECKED_FLAGS:
        tc.assertIsNone(rec["feasible"])
    else:
        tc.assertEqual(rec["feasible"], not blocking, rec["flags"][1])
    if rec["feasible"] is not True:
        tc.assertEqual(rec["total_sf"], 0)
    if rec["feasible"] is False:
        tc.assertTrue(blocking or "NO_FIT" in names(rec))
    tc.assertIn("ZONING_DRAFT", names(rec))
    tc.assertEqual(rec["zoning"]["status"], "draft")


class Offline(unittest.TestCase):
    """No network, no sleeps, the arcgis cache in a temp folder."""

    angles = True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.patch(arcgis, "CACHE_DIR", self.tmp / "http")
        self.patch(arcgis.time, "sleep", lambda s: None)
        self.patch(requests.Session, "request", mock.Mock(side_effect=AssertionError("network call in an offline test")))
        self.patch(arcgis, "_session", mock.Mock(side_effect=AssertionError("network call in an offline test")))
        if self.angles:
            self.patch(sitefit, "_angles", lambda rings: [0.0, 90.0])

    def patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)


_CACHE: dict = {}


def planned(key, maker, opts=sp.PlanOptions()):
    """One plan per synthetic case per test process (they are deterministic)."""
    k = (key, opts)
    if k not in _CACHE:
        with mock.patch.object(sitefit, "_angles", lambda rings: [0.0, 90.0]):
            _CACHE[k] = sp.plan_site(maker(), opts)
    return _CACHE[k]


# ---------------------------------------------------------------------------
# Numbers: detention, parking, trailers, voltage, soils, the plane
# ---------------------------------------------------------------------------
class Numbers(unittest.TestCase):

    def test_pond_holds_its_volume(self):
        for v in (0.5, 1.9, 6.5, 12.0):
            w0, Lf, Wf, vv = sp.pond_dims(v)
            held = sp.pond_volume(w0) / sp.SQFT_PER_AC
            self.assertGreaterEqual(held, v * 0.999)
            self.assertLess(held, v * 1.01)
            ht = sp.POND_DEPTH + sp.FREEBOARD
            self.assertAlmostEqual(Wf, w0 + 2 * sp.POND_Z * ht + 2 * sp.RIM)
            self.assertAlmostEqual(Lf, 2 * w0 + 2 * sp.POND_Z * ht + 2 * sp.RIM)

    def test_detention_formula(self):
        self.assertAlmostEqual(sp.detention_storage(10, 4, "B"), 1.15 * (0.30 * 10 + 0.07 * 4))
        self.assertAlmostEqual(sp.detention_storage(10, 4, "D"), 1.15 * (0.20 * 10 + 0.10 * 4))
        self.assertAlmostEqual(sp.detention_storage(10, 4, "?"), sp.detention_storage(10, 4, "B"))
        self.assertEqual([sp.pond_count(v) for v in (6.5, 10.0, 10.4, 20.0, 20.1)], [1, 1, 2, 2, 3])

    def test_parking_is_max_of_code_and_market_at_the_bands(self):
        o = sp.PlanOptions()
        for sf, ratio in ((99_999, 1.0), (100_000, 0.70), (249_999, 0.70), (250_000, 0.55), (400_000, 0.55)):
            market = math.ceil(ratio * sf / 1000)
            self.assertEqual(sp.parking_market(sf), market)
            code, _ = sp.parking_code("fulton", sf)
            req, basis = sp.required_cars("fulton", sf, o)
            self.assertEqual(req, max(code, market))
            self.assertIn(f"market {market}", basis)
        code_only = sp.PlanOptions(parking="code")
        self.assertEqual(sp.required_cars("fulton", 300_000, code_only)[0], sp.parking_code("fulton", 300_000)[0])
        self.assertEqual(sp.required_cars("fulton", 300_000, sp.PlanOptions(parking="market"))[0], sp.parking_market(300_000))

    def test_atlanta_parking_decision(self):
        _, rules, _ = sp.jur_rules("Atlanta")
        self.assertEqual(sp.parking_rule_for("atlanta", rules, sp.PlanOptions()), "market")
        self.assertEqual(sp.parking_rule_for("atlanta", rules, sp.PlanOptions(atlanta_parking="code")), "atlanta")
        self.assertEqual(sp.required_cars("atlanta", 60_000, sp.PlanOptions(atlanta_parking="code"))[0], max(100, 60))

    def test_trailer_target_and_option(self):
        self.assertEqual(sp.trailer_target(200_000, 40), 20)
        self.assertEqual(sp.trailer_target(250_000, 50), 50)
        self.assertEqual(sp.trailer_target(450_000, 90), 117)
        tiers = sp.trailer_tiers_from("1.0")
        self.assertEqual(sp.trailer_target(200_000, 40, tiers), 40)
        tiers3 = sp.trailer_tiers_from("0.2,0.4,0.6")
        self.assertEqual(sp.trailer_target(500_000, 10, tiers3), 6)
        for bad in ("", "1,2", "9", "a"):
            with self.assertRaises(ValueError):
                sp.trailer_tiers_from(bad)

    def test_voltage(self):
        for v in (None, "", "NOT AVAILABLE", -999999, float("nan")):
            self.assertEqual(sp.corridor_halfwidth(v), (50, True))
        self.assertEqual(sp.corridor_halfwidth(69), (30, False))
        self.assertEqual(sp.corridor_halfwidth("230"), (65, False))
        self.assertEqual(sp.corridor_halfwidth(500), (90, False))

    def test_dual_soil_group(self):
        self.assertEqual(sp.hsg_letter("B/D", fixes=False), ("B", None))
        letter, note = sp.hsg_letter("B/D", fixes=True)
        self.assertEqual(letter, "B")                  # the letter giving the larger pond
        self.assertIn("dual", note)
        self.assertNotIn("undrained", note)
        # A dual group never gives less storage than either of its letters
        # (D nets out more pre-development runoff, so it shrinks the pond).
        for dual in ("A/D", "B/D", "C/D"):
            got, _ = sp.hsg_letter(dual, True)
            for imp in (1.0, 6.0, 17.5, 40.0):
                perv = imp * (sp.DISTURBED_FACTOR - 1)
                v = sp.detention_storage(imp, perv, got)
                for k in dual.split("/"):
                    self.assertGreaterEqual(v + 1e-12, sp.detention_storage(imp, perv, k), (dual, k))
        self.assertEqual(sp.hsg_letter("none rated", True)[0], "B")
        self.assertEqual(sp.hsg_letter("C", True), ("C", None))

    def test_plane_area_and_round_trip(self):
        side_m = math.sqrt(10 * 4046.8564224)
        g = Polygon([(LON0, LAT0), (LON0 + side_m / PJ.kx, LAT0), (LON0 + side_m / PJ.kx, LAT0 + side_m / PJ.ky),
                     (LON0, LAT0 + side_m / PJ.ky)])
        plane = sp.Plane(g)
        ft = plane.ft(g)
        self.assertAlmostEqual(ft.area / sp.SQFT_PER_AC, 10.0, delta=0.05)
        back = plane.ft(plane.wgs(ft))
        self.assertLess(max(abs(a - b) for p, q in zip(ft.exterior.coords, back.exterior.coords) for a, b in zip(p, q)), 0.01)

    def test_ada(self):
        self.assertEqual([sp.ada_count(n) for n in (25, 26, 150, 500, 501, 1000, 1001)], [1, 2, 5, 9, 11, 20, 21])


class Variants(unittest.TestCase):

    def test_operator_variants_come_from_sitefit(self):
        vs = sp.variants()
        op = [v for v in vs if v["operator"]]
        self.assertEqual({(v["type"], v["D"], v["Lrange"]) for v in op}, {("rear", 260, (400, 640)), ("cross", 520, (600, 1000))})
        self.assertEqual(len(vs), 28)
        self.assertEqual(sp.length_range(op[0]), (400, 640))
        self.assertNotIn("front", {v["type"] for v in vs})
        patched = {k: dict(v) for k, v in sitefit.PROTOTYPES.items()}
        patched["rear"]["W"], patched["rear"]["L"] = 250, 700
        with mock.patch.object(sitefit, "PROTOTYPES", patched):
            op2 = [v for v in sp.variants() if v["operator"] and v["type"] == "rear"]
        self.assertEqual({(v["D"], v["Lrange"]) for v in op2}, {(250, (400, 700))})
        self.assertTrue(all(v["name"].startswith("operator rear-load 250") for v in op2))

    def test_market_lengths(self):
        v = {"type": "rear", "D": 160, "operator": False}
        self.assertEqual(sp.length_range(v), (240, 720))
        v = {"type": "cross", "D": 600, "operator": False}
        self.assertEqual(sp.length_range(v), (1200, 1600))


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
class GeometryHelpers(unittest.TestCase):

    def dem_plane(self, slope_pct):
        region = box(0, 0, 800, 800)
        grid = sp.dem_points(region)
        return sp.Dem(points=dict(grid), elev={ij: x * slope_pct / 100.0 for ij, (x, y) in grid})

    def test_steep_cells(self):
        d20 = self.dem_plane(20)
        steep = sp.steep_cells(d20)
        interior = box(40, 40, 760, 760)
        self.assertGreater(steep.intersection(interior).area / interior.area, 0.95)
        self.assertTrue(sp.steep_cells(self.dem_plane(5)).is_empty)
        # the prototype's float-key lookup gives the same cells on a regular grid
        self.assertAlmostEqual(sp.steep_cells(d20, float_keys=True).area, steep.area)

    def test_dem_grid_has_integer_keys(self):
        grid = sp.dem_points(box(0, 0, 400, 400))
        self.assertTrue(all(isinstance(i, int) and isinstance(j, int) for (i, j), _ in grid))
        pts = dict(grid)
        (i, j), (x, y) = grid[5]
        if (i + 1, j) in pts:
            self.assertAlmostEqual(pts[(i + 1, j)][0] - x, sp.DEM_STEP_FT)
        self.assertEqual(sp.dem_points(Polygon()), [])

    def test_frontage_runs_drops_points(self):
        gc = GeometryCollection([LineString([(0, 0), (500, 0)]), Point(800, 5)])
        runs = sp.frontage_runs(gc)
        self.assertEqual(len(runs), 1)
        self.assertAlmostEqual(runs[0].length, 500)
        self.assertEqual(sp.frontage_runs(GeometryCollection([Point(1, 1)])), [])
        self.assertEqual(sp.frontage_runs(None), [])

    def test_grid_route_around_a_wall(self):
        walk = box(0, 0, 1000, 600).difference(box(480, 0, 520, 500))
        rt = sp.grid_route(Point(100, 100), Point(900, 100), walk)
        self.assertIsNotNone(rt)
        self.assertLess(sp.route_outside_ft(rt, walk), sp.ROUTE_TOL_FT)
        self.assertGreater(rt.length, 800)
        closed = box(0, 0, 1000, 600).difference(box(480, -1, 520, 601))
        self.assertIsNone(sp.grid_route(Point(100, 100), Point(900, 100), closed))

    def test_grid_route_unreachable_large(self):
        walk = box(0, 0, 6000, 6000).difference(box(2980, -1, 3020, 6001))
        t = time.monotonic()
        self.assertIsNone(sp.grid_route(Point(100, 100), Point(5900, 5900), walk))
        self.assertLess(time.monotonic() - t, 3.0)

    def test_routes_in_rotated_corridors(self):
        for ang in (0, 30, 60, 85):
            corridor = shapely.affinity.rotate(box(0, 0, 1400, 120).difference(box(650, 0, 700, 60)), ang, origin=(0, 0))
            a = shapely.affinity.rotate(Point(40, 60), ang, origin=(0, 0))
            b = shapely.affinity.rotate(Point(1360, 60), ang, origin=(0, 0))
            rt = sp.grid_route(a, b, corridor)
            self.assertIsNotNone(rt, ang)
            self.assertLess(sp.route_outside_ft(rt, corridor), sp.ROUTE_TOL_FT, ang)

    def test_pond_does_not_fit_region(self):
        d = sp.Dem()
        self.assertIsNone(sp.place_sized_pond(box(0, 0, 100, 100), 300, 150, d, [0, 90], Point(0, 0), None, None))
        r = sp.place_sized_pond(box(0, 0, 500, 400), 300, 150, d, [0, 90], Point(0, 0), None, None)
        self.assertTrue(box(0, 0, 500, 400).contains(r))

    def test_overlap_wrapper_pins_checks(self):
        # the one private helper of checks the port uses (R5): its contract
        g = box(LON0, LAT0, LON0 + 0.002, LAT0 + 0.002)
        idx = layers.Index([(box(LON0 + 0.001, LAT0, LON0 + 0.003, LAT0 + 0.002), {})])
        pj = LocalProjection.around(g)
        x = si._overlap_wgs(g, pj, pj(g).area, idx)
        self.assertAlmostEqual(x.area / g.area, 0.5, places=6)
        self.assertIsNone(si._overlap_wgs(g, pj, pj(g).area, layers.Index([])))

    def test_seam_closing(self):
        a, b = box(0, 0, 600, 800), box(600.5, 0, 1200.5, 800)
        g = wgs(shapely.union_all([a, b]))
        plane = sp.Plane(g)
        closed, sf = sp.site_parcel(plane, g, True, sp.PlanOptions())
        self.assertEqual(len(sp.parts(closed)), 1)
        self.assertGreater(sf, 300)
        self.assertLess(sf, 600)
        edges = sp.classify_edges(closed, plane, [], plane.ft(wgs(LineString([(0, 0), (1200.5, 0)]))))
        other = shapely.union_all([ln for k, ln in edges if k == "OTHER"])
        self.assertTrue(other.intersection(local(box(590, 10, 611, 790), g)).is_empty)
        apart, _ = sp.site_parcel(sp.Plane(wgs(shapely.union_all([a, box(800, 0, 1400, 800)]))),
                                  wgs(shapely.union_all([a, box(800, 0, 1400, 800)])), True, sp.PlanOptions())
        self.assertEqual(len(sp.parts(apart)), 2)
        proto, sf0 = sp.site_parcel(plane, g, True, sp.PlanOptions(behaviour="prototype"))
        self.assertEqual((len(sp.parts(proto)), sf0), (2, 0.0))


# ---------------------------------------------------------------------------
# Whole plans
# ---------------------------------------------------------------------------
class Plans(Offline):

    def test_basic_plan(self):
        rec, d = planned("basic", lambda: site(600, 700))
        self.assertTrue(rec["fits"])
        self.assertIs(rec["feasible"], True)
        self.assertGreater(rec["total_sf"], 50_000)
        self.assertEqual(rec["total_sf"], rec["total_sf_if_access"])
        self.assertTrue(rec["access"]["truck_driveway"])
        self.assertEqual(rec["plan_checks"], [])
        self.assertEqual(rec["parcel_ids"], ["T1"])
        self.assertEqual(rec["operator_decisions"]["parking_rule"], "max")
        b = rec["building"]
        self.assertEqual(b["sf"], sp.rnd(b["L_ft"] * b["D_ft"], 1000))
        self.assertGreaterEqual(rec["cars"]["provided"], rec["cars"]["required"])
        self.assertTrue(d.p.building.within(d.parcel))
        for pond in d.o["ponds"]:
            self.assertTrue(pond.disjoint(d.p.module))
        assert_invariant(self, rec)

    def test_no_frontage_is_hypothetical(self):
        rec, _ = planned("nofront", lambda: site(450, 520, front_state="none"))
        self.assertTrue(rec["hypothetical"])
        self.assertIn("ACCESS_NONE", names(rec, 1))
        self.assertIs(rec["feasible"], False)
        self.assertEqual(rec["total_sf"], 0)
        self.assertGreater(rec["total_sf_if_access"], 0)
        assert_invariant(self, rec)

    def test_frontage_not_checked_is_incomplete(self):
        rec, d = planned("frontnc", lambda: site(450, 520, front_state="not_checked", dem_state="partial"))
        self.assertIsNone(rec["feasible"])
        self.assertIsNone(rec["hypothetical"])        # access unknown, not landlocked
        self.assertNotIn("ACCESS_NONE", names(rec))
        self.assertTrue({"FRONTAGE_NOT_CHECKED", "DEM_PARTIAL"} <= names(rec, 1))
        self.assertEqual(rec["total_sf"], 0)
        svg = sr.render(d, rec)
        self.assertIn("INCOMPLETE", svg)
        assert_invariant(self, rec)

    def test_input_flags_for_every_state(self):
        for key, flag in (("neighbours", "NEIGHBOURS_NOT_CHECKED"), ("transmission", "TRANSMISSION_NOT_CHECKED"),
                          ("layers", "LAYERS_NOT_CHECKED"), ("frontage", "FRONTAGE_NOT_CHECKED"), ("dem", "DEM_NOT_CHECKED")):
            inp = site(300, 300, states={key: sp.InputState("not_checked", "x")})
            got = {f for f, s, n in sp._input_flags(inp)}
            self.assertEqual(got, {flag}, key)
            fe, _ = sp.decide([(flag, 1, "x"), ("ZONING_DRAFT", 2, "x")], True, False, sp.PlanOptions())
            self.assertIsNone(fe)
            fe, _ = sp.decide([(flag, 1, "x")], True, False, sp.PlanOptions(behaviour="prototype"))
            self.assertIsNone(fe)

    def test_dem_not_checked_plan(self):
        rec, _ = planned("demnc", lambda: site(450, 520, dem_state="not_checked"))
        self.assertIn("DEM_NOT_CHECKED", names(rec, 1))
        self.assertIsNone(rec["feasible"])
        self.assertEqual(rec["inputs"]["dem_points"], 0)

    def test_route_infeasible_two_pieces(self):
        # the frontage touches only a small piece; the building fits only on the big one (R13)
        shapes = [box(0, 0, 320, 260), box(0, 500, 700, 1200)]
        rec, _ = planned("twopiece", lambda: site(shapes=shapes, front=LineString([(0, 0), (320, 0)])))
        self.assertTrue(rec["fits"])
        self.assertIn("ROUTE_INFEASIBLE", names(rec, 1))
        self.assertIs(rec["feasible"], False)
        self.assertEqual(rec["total_sf"], 0)
        assert_invariant(self, rec)

    def test_transmission_corridor_cut(self):
        line = wgs(LineString([(120, -100), (120, 900)]))
        rec, d = planned("trans", lambda: site(700, 700, transmission=[({"VOLTAGE": 230}, line)]))
        self.assertIn("TRANSMISSION_EASEMENT_ASSUMED", names(rec))
        self.assertGreater(rec["inputs"]["transmission_ac"], 1.9)
        self.assertTrue(rec["fits"])
        self.assertLess(d.p.building.intersection(d.trans).area, 1.0)
        for pond in d.o["ponds"]:
            self.assertLess(pond.intersection(d.trans).area, 1.0)
        line2 = wgs(LineString([(300, -100), (300, 1100)]))
        rec2, _ = sp.plan_site(site(300, 300, transmission=[({"VOLTAGE": "NOT AVAILABLE"}, line2)]))
        self.assertIn("VOLTAGE_ASSUMED", names(rec2))

    def test_steep_ground(self):
        def z(x, y):
            return 900.0 + (0.3 * (x - 400) if x > 400 else 0.0)
        rec, d = planned("steep", lambda: site(600, 700, z=z))
        self.assertGreater(rec["inputs"]["steep_ac"], 2.0)
        self.assertGreater(rec["grading"]["relief_ft"], 10)

    def test_too_small_and_narrow(self):
        for w, h in ((150, 150), (1000, 20), (3000, 90)):
            rec, d = sp.plan_site(site(w, h))
            self.assertFalse(rec["fits"])
            self.assertIn("NO_FIT", names(rec, 1))
            self.assertIs(rec["feasible"], False)
            self.assertIsNone(d)
        with self.assertRaises(sp.PlanError):
            sp.plan_site(sp.SiteInputs("x", "x", "x", [], Polygon(), {}))

    def test_pond_that_cannot_fit(self):
        def nopond(*a, **k):
            return None
        control, _ = planned("basic", lambda: site(600, 700))
        self.assertTrue(control["detention"]["fits"])             # without the patch the pond fits
        self.assertIs(control["feasible"], True)
        with mock.patch.object(sp, "place_sized_pond", nopond):
            rec, _ = sp.plan_site(site(600, 700))
            proto, _ = sp.plan_site(site(600, 700), sp.PlanOptions(behaviour="prototype"))
        self.assertIn("POND_DOES_NOT_FIT", names(rec, 1))
        self.assertIs(rec["feasible"], False)
        self.assertFalse(rec["detention"]["fits"])
        self.assertEqual(rec["detention"]["ponds"], 0)
        assert_invariant(self, rec)
        self.assertIn("POND_DOES_NOT_FIT", names(proto, 2))     # the prototype's severity
        self.assertIs(proto["feasible"], False)

    def test_second_building(self):
        two = shapely.MultiLineString([[(0, 0), (1600, 0)], [(0, 1000), (1600, 1000)]])
        rec, d = planned("second", lambda: site(1600, 1000, front=two))
        self.assertIn("second_building", rec, [f["flag"] for f in rec["flags"][1]])
        q, o2 = d.second
        self.assertLess(q.module.intersection(d.p.module).area, 1.0)
        for pond in d.o["ponds"]:
            self.assertLess(q.module.intersection(pond).area, 1.0)
        self.assertEqual(rec["total_sf"], sp.rnd(d.p.sf + q.sf, 1000))
        self.assertEqual(rec["totals_both_buildings"]["doors"], d.p.doors + q.doors)
        self.assertEqual(rec["detention"]["ponds"], len(d.o["ponds"]) + len(o2["ponds"]))
        self.assertEqual(rec["plan_checks"], [])
        assert_invariant(self, rec)

    def test_determinism(self):
        a = sp.plan_site(site(450, 520))
        b = sp.plan_site(site(450, 520))
        self.assertEqual(json.dumps(a[0], sort_keys=True, default=str), json.dumps(b[0], sort_keys=True, default=str))
        self.assertEqual(sr.render(a[1], a[0]), sr.render(b[1], b[0]))

    def test_determinism_across_hash_seeds(self):
        script = ("import json,sys\nfrom unittest import mock\nsys.path.insert(0, %r)\n"
                  "from screener.tests.test_siteplan import site\nfrom screener import siteplan as sp, siteplan_render as sr, sitefit\n"
                  "with mock.patch.object(sitefit, '_angles', lambda r: [0.0, 90.0]):\n"
                  "    rec, d = sp.plan_site(site(450, 520))\n"
                  "print(json.dumps(rec, default=str)); print(sr.render(d, rec))\n") % str(SCREENER.parent)
        outs = []
        for seed in ("1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=seed, HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9")
            res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(res.returncode, 0, res.stderr[-800:])
            outs.append(res.stdout)
        self.assertEqual(outs[0], outs[1])

    def test_jurisdiction_rules(self):
        rec, _ = planned("atl", lambda: site(450, 520, jur="Atlanta"))
        self.assertIn("ATL_PARKING_CODE", names(rec))
        self.assertIn("market ratio", rec["cars"]["basis"])
        rec2, _ = planned("unknown", lambda: site(450, 520, jur="Nowhere City"))
        self.assertIn("JURISDICTION_DEFAULTED", names(rec2))
        self.assertEqual(rec2["zoning"]["name"], "south fulton (default)")
        self.assertEqual(rec2["zoning"]["status"], "draft")
        rec3, _ = planned("ep", lambda: site(450, 520, jur="East Point", zoning="I-2"))
        self.assertEqual(rec3["gated"], ["USE_PERMIT_REQUIRED"])
        self.assertEqual(rec3["total_sf"], 0)
        assert_invariant(self, rec3)
        for t in sp.JUR_DRAFT.values():
            self.assertEqual(t["status"], "draft")

    def test_coverage_cap_picks_the_compliant_variant(self):
        rules = dict(sp.JUR_DRAFT)
        rules["testville"] = dict(rules["south fulton"], cover=("building", 0.20), height=None)
        with mock.patch.object(sp, "JUR_DRAFT", rules):
            rec, d = sp.plan_site(site(700, 800, jur="Testville"))
            proto, pd = sp.plan_site(site(700, 800, jur="Testville"), sp.PlanOptions(behaviour="prototype"))
        self.assertIn("COVERAGE_OVER_CAP", names(proto, 1))
        self.assertGreater(pd.p.building.area / pd.parcel.area, 0.20)
        self.assertNotIn("COVERAGE_OVER_CAP", names(rec))
        self.assertLessEqual(d.p.building.area / d.parcel.area, 0.20)
        self.assertLess(rec["building"]["sf"], proto["building"]["sf"])
        self.assertIs(rec["feasible"], True)
        assert_invariant(self, rec)

    def test_height_cap_blocks_but_does_not_shrink(self):
        # A 35 ft draft height cap leaves 25 ft clear on any building: severity
        # 1 whatever the length. The default rule makes the site infeasible
        # but must not shrink the building for it (no futile refits; #44
        # College Park in the replay): the plan is the one drawn without the
        # cap.
        rules = dict(sp.JUR_DRAFT)
        rules["capped"] = dict(rules["south fulton"], height=35, cover=None)
        rules["open"] = dict(rules["south fulton"], height=None, cover=None)
        with mock.patch.object(sp, "JUR_DRAFT", rules):
            rec, _ = sp.plan_site(site(900, 900, jur="Capped"))
            free, _ = sp.plan_site(site(900, 900, jur="Open"))
        self.assertIn("HEIGHT_CAP", names(rec, 1))
        self.assertIs(rec["feasible"], False)
        self.assertNotIn("HEIGHT_CAP", names(free))
        drop = lambda b: {k: v for k, v in b.items() if k != "clear_height_ft"}
        self.assertEqual(drop(rec["building"]), drop(free["building"]))      # same building, lower clear height
        self.assertEqual(rec["building"]["clear_height_ft"], 25)
        self.assertEqual([a["sf"] for a in rec["alternatives"]], [a["sf"] for a in free["alternatives"]])
        assert_invariant(self, rec)

    def test_trailer_ratio_is_consistent(self):
        o = sp.PlanOptions(trailer_tiers=sp.trailer_tiers_from("1.3"))
        # 1000 x 1000 ft: the plan drawn has a drop yard (the ranking no longer
        # trades floor area for trailer stalls, so 800 x 800 draws none)
        rec, d = planned("tr13c", lambda: site(1000, 1000), o)
        self.assertGreater(rec["trailers"]["drop_yards"], 0)     # the drop-yard branch runs
        self.assertEqual(rec["trailers"]["target"], math.ceil(1.3 * rec["doors"]))
        self.assertEqual(rec["operator_decisions"]["trailer_target_tiers"][0][1], 1.3)
        short = rec["trailers"]["total"] < rec["trailers"]["target"]
        self.assertEqual(short, "TRAILER_SHORT" in names(rec))
        if rec["trailers"]["drop_yards"]:
            self.assertEqual(rec["trailers"]["total"], rec["trailers"]["target"])

    def test_budget(self):
        t = [0.0]

        def clock():
            t[0] += 0.5
            return t[0]
        rec, _ = sp.plan_site(site(450, 520), sp.PlanOptions(max_seconds=3.0), clock=clock)
        self.assertIn("PLAN_BUDGET_EXCEEDED", names(rec, 1))
        self.assertIs(rec["feasible"], False)
        # Out of time before any plan was finished: that says nothing about
        # fit, so the record is not a NO_FIT (reference #48 at 120 s).
        t[0] = 0.0
        rec, d = sp.plan_site(site(450, 520), sp.PlanOptions(max_seconds=0.1), clock=clock)
        self.assertIsNone(d)
        self.assertIn("PLAN_BUDGET_EXCEEDED", names(rec, 1))
        self.assertNotIn("NO_FIT", names(rec))
        self.assertIn("not a no-fit", rec["flags"][1][0]["note"])
        self.assertIs(rec["feasible"], False)
        self.assertEqual(rec["total_sf"], 0)

    def test_budget_in_the_second_building_search_keeps_building_1(self):
        """The budget running out only in the optional second-building
        search leaves building 1, which was fully searched, feasible and
        counted; the plan says SECOND_BUILDING_NOT_SEARCHED (severity 2)."""
        two = shapely.MultiLineString([[(0, 0), (1600, 0)], [(0, 1000), (1600, 1000)]])
        t = [0.0]
        real_finish = sp.finish

        def finish(p, ctx, others=None):
            if others is not None:
                t[0] = 1e9             # the deadline passes inside the second-building search only
            return real_finish(p, ctx, others)
        with mock.patch.object(sp, "finish", finish):
            rec, d = sp.plan_site(site(1600, 1000, front=two), sp.PlanOptions(max_seconds=100.0), clock=lambda: t[0])
        self.assertIs(rec["feasible"], True, rec["flags"][1])
        self.assertNotIn("PLAN_BUDGET_EXCEEDED", names(rec))
        self.assertIn("SECOND_BUILDING_NOT_SEARCHED", names(rec, 2))
        self.assertNotIn("second_building", rec)
        self.assertEqual(rec["total_sf"], rec["building"]["sf"])
        self.assertGreater(rec["total_sf"], 0)
        assert_invariant(self, rec)

    def test_budget_in_the_smaller_layout_search_is_a_note(self):
        """The budget running out in the extra search for smaller layouts
        (after the pond blocked every explored plan) keeps the plan as found
        and says SMALLER_LAYOUTS_NOT_ALL_TRIED, not PLAN_BUDGET_EXCEEDED."""
        t = [0.0]
        real = sp._blocking

        def blocking(o):
            t[0] = 1e9                 # _blocking is asked only when the rescue pass is considered
            return real(o)
        # This site needs the rescue pass only without the room-for-the-pond
        # search (a 4:1 pond fits it at 151,000 SF on the first pass,
        # reviews/40): both are switched off here, as this tests the pass.
        with mock.patch.object(sp, "_blocking", blocking), mock.patch.object(sp, "POND_FALLBACK_RATIOS", ()), \
                mock.patch.object(sp, "RELOCATE_MAX_CHECKS", 0):
            rec, _ = sp.plan_site(site(700, 700), sp.PlanOptions(max_seconds=100.0), clock=lambda: t[0])
        self.assertIn("SMALLER_LAYOUTS_NOT_ALL_TRIED", names(rec, 2))
        self.assertNotIn("PLAN_BUDGET_EXCEEDED", names(rec))
        self.assertIn("POND_DOES_NOT_FIT", names(rec, 1))
        self.assertIs(rec["feasible"], False)
        assert_invariant(self, rec)

    def test_alternatives_are_layout_checks_not_feasibility(self):
        rec, _ = planned("nofront", lambda: site(450, 520, front_state="none"))
        self.assertIs(rec["feasible"], False)
        self.assertTrue(rec["alternatives"])
        for a in rec["alternatives"]:
            self.assertIn("geometry_ok", a)
            self.assertNotIn("feasible", a)

    def test_prototype_mode_budget_and_gate_wording(self):
        proto = sp.PlanOptions(behaviour="prototype")
        self.assertEqual(sp.decide([("PLAN_BUDGET_EXCEEDED", 1, "x")], True, False, proto), (False, []))
        self.assertEqual(sp.decide([("USE_PERMIT_REQUIRED", 1, "x")], True, False, proto), (True, ["USE_PERMIT_REQUIRED"]))
        rec, d = planned("basic", lambda: site(600, 700))
        counted = dict(rec, gated=["USE_PERMIT_REQUIRED"])
        self.assertIn("floor area counted", sr.render(d, counted))
        self.assertIn("floor area not counted", sr.render(d, dict(counted, total_sf=0)))

    def test_frontage_geometry_error_is_not_hypothetical(self):
        def boom(front):
            raise sp.FrontageGeometryError("test")
        with mock.patch.object(sp, "frontage_runs", boom):
            rec, _ = sp.plan_site(site(600, 700))
        self.assertIn("FRONTAGE_GEOMETRY", names(rec, 1))
        self.assertIsNone(rec["hypothetical"])
        self.assertIsNone(rec["feasible"])
        self.assertNotIn("ACCESS_NONE", names(rec))

    def test_a_larger_parcel_is_not_worse_than_a_smaller_one_inside_it(self):
        """Monotonicity: same frontage line, same setbacks, flat ground. The
        prototype read 700 x 700 and 1100 x 700 as infeasible (the pond)
        while 600 x 700 inside them was feasible."""
        small, _ = planned("basic", lambda: site(600, 700))
        self.assertIs(small["feasible"], True)
        for w, h in ((700, 700), (1100, 700)):
            with self.subTest(size=(w, h)):
                rec, _ = planned(f"mono{w}x{h}", lambda w=w, h=h: site(w, h))
                self.assertIs(rec["feasible"], True, rec["flags"][1])
                self.assertGreater(rec["total_sf"], 0)
                assert_invariant(self, rec)
        proto, _ = sp.plan_site(site(700, 700), sp.PlanOptions(behaviour="prototype"))
        self.assertIs(proto["feasible"], False)       # the prototype's answer, kept for parity

    def test_clean_rectangles_raise_no_route_outside_flag(self):
        """The joins from the driveway to the first grid cell are measured
        against what they must not cross, not against the grid's walk area."""
        for w, h in ((600, 500), (600, 600), (650, 550), (750, 700), (1000, 900)):
            with self.subTest(size=(w, h)):
                rec, _ = planned(f"route{w}x{h}", lambda w=w, h=h: site(w, h))
                self.assertNotIn("ROUTE_OUTSIDE_ENVELOPE", names(rec), rec["plan_checks"])
        walk = box(0, 2, 100, 100).difference(box(0, 2, 40, 30))
        line = LineString([(10, 0), (60, 40), (90, 90)])      # the join cuts the notch's corner
        joins = (((10, 0), (60, 40)), ((90, 90), (90, 90)))
        self.assertGreater(sp.route_outside_ft(line, walk), sp.ROUTE_TOL_FT)
        self.assertLessEqual(sp.route_stray_ft(line, walk, Polygon(), joins), sp.ROUTE_TOL_FT)
        self.assertGreater(sp.route_stray_ft(line, walk, box(20, 0, 60, 60), joins), sp.ROUTE_TOL_FT)

    def test_neighbours_without_a_shape_fail_closed(self):
        ff = FakeFetch(lambda url, p: {"features": [{"attributes": {"ParcelID": "07 9", "LUCode": "101", "ClassCode": "R3"},
                                                     "geometry": None}]})
        nb, st = si.county_near(G, ff)
        self.assertEqual((nb, st.state), ([], "partial"))
        rec, _ = sp.plan_site(site(600, 700, states={"neighbours": st}))
        self.assertIn("NEIGHBOURS_NOT_CHECKED", names(rec, 1))
        self.assertIsNone(rec["feasible"])
        self.assertEqual(rec["total_sf"], 0)

    def test_no_fit_with_a_missing_input_stays_false(self):
        # A missing input only enlarges the envelope, so NO_FIT stands
        # (documented in SITE_PLANNING.md); every other record follows R4.
        for key in STATES:
            with self.subTest(key):
                rec, _ = sp.plan_site(site(150, 150, states={key: sp.InputState("not_checked", "x")}))
                self.assertIn("NO_FIT", names(rec, 1))
                self.assertIs(rec["feasible"], False)
                self.assertEqual(rec["total_sf"], 0)

    def test_nan_elevation_is_nodata(self):
        plane = sp.Plane(G)
        parcel, _ = sp.site_parcel(plane, G, False, sp.PlanOptions())

        def answers(url, p):
            n = len(json.loads(p["geometry"])["points"])
            return {"samples": [{"locationId": i, "value": v} for i, v in zip(range(n), ("NaN", "nan", "Infinity", "-inf") * n)]}
        d = si.dem(plane, parcel, FakeFetch(answers))
        self.assertEqual((d.state, d.elev), ("not_checked", {}))

    def test_soil_value_blank_or_not_checked_is_flagged(self):
        self.assertEqual(sp.hsg_letter("", True), ("B", "no soil value in the run: B assumed"))
        letter, note = sp.hsg_letter("not checked: outside the top 250 ([soils] max_parcels)", True)
        self.assertEqual(letter, "B")
        self.assertTrue(note.startswith("soil group not checked"))
        self.assertNotIn("no soil group rated", note)
        self.assertEqual(sp.hsg_letter("", False), ("B", None))
        rec, _ = planned("hsgblank", lambda: site(600, 700, hsg=""))
        self.assertIn("HSG_ASSUMED", names(rec, 3))
        self.assertIn("HSG B (see HSG_ASSUMED)", rec["detention"]["basis"])
        rec, _ = planned("hsgdual", lambda: site(600, 700, hsg="B/D"))
        self.assertIn("HSG B (see HSG_ASSUMED)", rec["detention"]["basis"])
        self.assertEqual(si._meta({"soil_hsg": ""})["hsg"], "")

    def test_fairburn_district_buffer_is_flagged(self):
        rec, _ = planned("fairburn", lambda: site(600, 700, jur="Fairburn"))
        self.assertIn("DISTRICT_BUFFER_NOT_DRAWN", names(rec, 2))
        rec, _ = planned("basic", lambda: site(600, 700))
        self.assertNotIn("DISTRICT_BUFFER_NOT_DRAWN", names(rec))

    def test_water_buffer_before_clipping(self):
        wet = wgs(box(-200, 200, -5, 400))            # wetland 5 ft outside the west line
        mk = lambda: site(450, 520, constraints_wgs=[], constraints_near_wgs=[wet])
        fixed, d = sp.plan_site(mk())
        proto, pd = sp.plan_site(mk(), sp.PlanOptions(behaviour="prototype"))
        g = mk().geometry
        strip, probe = local(box(0, 200, 30, 400), g), local(box(12, 220, 14, 380), g)
        self.assertGreater(pd.P.intersection(probe).area, 0)        # prototype: no buffer inside
        self.assertTrue(d.P.intersection(probe).is_empty)           # fixes: about 20 ft taken
        self.assertLess(d.P.intersection(strip).area, pd.P.intersection(strip).area)

    def test_wetland_in_the_access_path_keeps_routes_out_or_flags(self):
        # R17: a mapped wetland inside the line where the driveway throat or a
        # court entry would go. The routes stay out of its 25 ft buffer
        # (within the conflict tolerance) or the plan says so with a
        # severity-1 flag and is not feasible. The first wetland sits across
        # the frontage centre (the route is forced through: flagged), the
        # second at a front corner (the route passes within tolerance).
        seen = set()
        for wet_ft in (box(250, 20, 450, 80), box(0, 60, 120, 140)):
            wet = wgs(wet_ft)
            rec, d = sp.plan_site(site(700, 700, constraints_wgs=[wet], constraints_near_wgs=[wet]))
            self.assertTrue(rec["fits"])
            wet_buf = d.constraints.buffer(sp.WATER_BUFFER_FT)
            routes = shapely.union_all([pp for _, pp in d.o["route_polys"]])
            inside = routes.intersection(wet_buf).area
            flagged = any("route in a water buffer" in f["note"] for f in rec["flags"][1])
            self.assertTrue(inside <= sp.CONFLICT_TOL_SF or flagged, (inside, rec["flags"][1]))
            if flagged:
                self.assertIn("PLAN_GEOMETRY_CONFLICT", names(rec, 1))
                self.assertIs(rec["feasible"], False)
            seen.add(flagged)
            self.assertLess(d.p.module.intersection(d.constraints).area, 1.0)
            for pond in d.o["ponds"]:
                self.assertLess(pond.intersection(wet_buf).area, sp.CONFLICT_TOL_SF)
            assert_invariant(self, rec)
        self.assertEqual(seen, {True, False})       # both branches exercised

    def test_assemblage_seam_gives_the_solid_plan(self):
        solid, _ = planned("solid", lambda: site(shapes=[box(0, 0, 700.5, 700)], front=LineString([(0, 0), (700.5, 0)])))
        two = [box(0, 0, 350, 700), box(350.5, 0, 700.5, 700)]
        seam, _ = planned("seam", lambda: site(shapes=two, front=LineString([(0, 0), (700.5, 0)]), assembled=True))
        self.assertGreater(seam["inputs"]["seam_sf_closed"], 300)
        unclosed, _ = sp.plan_site(site(shapes=two, front=LineString([(0, 0), (700.5, 0)]), assembled=True),
                                   sp.PlanOptions(behaviour="prototype"))
        self.assertLess(unclosed["building"]["sf"], solid["building"]["sf"])     # the prototype's dead strip
        self.assertNotIn("ASSEMBLAGE_NOT_CONTIGUOUS", names(seam))
        self.assertEqual(seam["building"], solid["building"])
        self.assertEqual(seam["total_sf"], solid["total_sf"])
        apart = [box(0, 0, 300, 300), box(500, 0, 800, 300)]
        rec, _ = sp.plan_site(site(shapes=apart, front=LineString([(0, 0), (800, 0)]), assembled=True))
        self.assertIn("ASSEMBLAGE_NOT_CONTIGUOUS", names(rec, 1))

    def test_geometry_collection_frontage_does_not_raise(self):
        gc = GeometryCollection([LineString([(0, 0), (450, 0)]), Point(450, 520)])
        rec, _ = sp.plan_site(site(450, 520, front=gc))
        self.assertTrue(rec["fits"])
        self.assertNotIn("ACCESS_NONE", names(rec))
        self.assertTrue(rec["access"]["truck_driveway"])

    def test_all_wetland_and_degenerate(self):
        whole = wgs(box(-50, -50, 500, 570))
        rec, d = sp.plan_site(site(450, 520, constraints_wgs=[whole], constraints_near_wgs=[whole]))
        self.assertFalse(rec["fits"])
        self.assertIsNone(d)
        self.assertIn("ZONING_DRAFT", names(rec))

    def test_large_sites_keep_to_the_budget(self):
        for w, h in ((3615, 3615), (1000, 20000)):
            t = time.monotonic()
            rec, _ = sp.plan_site(site(w, h, z=lambda x, y: 900.0), sp.PlanOptions(max_seconds=0.25))
            self.assertLess(time.monotonic() - t, 8.0, (w, h))
            if "PLAN_BUDGET_EXCEEDED" in names(rec):
                self.assertIs(rec["feasible"], False)

    def test_svg_is_safe(self):
        rec, d = planned("basic", lambda: site(600, 700))
        d2 = sp.Drawing(**{**d.__dict__, "title": 'A "<&\x01 title   </svg><script>alert(1)</script>'})
        svg = sr.render(d2, rec)
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        low = svg.lower()
        self.assertNotIn("<script", low)
        self.assertNotIn("href", low)
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", low))
        self.assertNotIn("<style", low)
        refs = set(re.findall(r"url\(([^)]*)\)", svg))
        self.assertEqual(refs - {"#tx", "#sx", "#cx", "#wx", "#gx"}, set())
        # every pattern referenced is defined in the file itself
        self.assertLessEqual({r[1:] for r in refs}, {e.get("id") for e in root.iter() if e.tag.endswith("pattern")})
        self.assertIn("DRAFT", svg)
        tags = {e.tag.split("}")[-1] for e in root.iter()}
        self.assertLessEqual(tags, {"svg", "title", "rect", "defs", "pattern", "line", "path", "circle", "text"})


# ---------------------------------------------------------------------------
# Inputs: requests, fail closed, politeness
# ---------------------------------------------------------------------------
class FakeFetch(si.Fetcher):
    def __init__(self, answers):
        super().__init__(sleep=lambda s: None)
        self.answers = answers
        self.seen = []

    def call(self, url, params, *, check, max_age_days=None):
        self.seen.append((url, dict(params), max_age_days))
        a = self.answers(url, params)
        if isinstance(a, Exception):
            raise a
        problem = check(a)
        if problem:
            raise arcgis.BadAnswer(problem)
        return a


G = wgs(box(0, 0, 600, 700))


def esri_poly(g):
    return {"rings": [list(map(list, g.exterior.coords))]}


class Inputs(Offline):
    angles = False

    def test_prototype_requests(self):
        ff = FakeFetch(lambda url, p: {"features": []} if "query" in url else {"samples": []})
        si.county_near(G, ff)
        si.transmission(G, ff)
        plane = sp.Plane(G)
        parcel, _ = sp.site_parcel(plane, G, False, sp.PlanOptions())
        si.dem(plane, parcel, ff)
        (u1, p1, _), (u2, p2, _) = ff.seen[:2]
        b = G.bounds
        self.assertEqual(u1, si.COUNTY_PARCELS_URL + "/query")
        self.assertEqual(p1["outFields"], "ParcelID,LUCode,ClassCode")
        self.assertEqual(p1["geometry"], f"{b[0]-0.0015},{b[1]-0.0015},{b[2]+0.0015},{b[3]+0.0015}")
        self.assertEqual(p2["geometry"], f"{b[0]-0.002},{b[1]-0.002},{b[2]+0.002},{b[3]+0.002}")
        self.assertEqual(p2["outFields"], "VOLTAGE,VOLT_CLASS,STATUS")
        dem_calls = ff.seen[2:]
        n = len(sp.dem_points(parcel))
        self.assertEqual(len(dem_calls), math.ceil(n / 400))
        url, p, age = dem_calls[0]
        self.assertEqual((url, p["geometryType"], p["returnFirstValueOnly"], age),
                         (si.DEM_URL, "esriGeometryMultipoint", "true", 3650.0))
        pts = json.loads(p["geometry"])["points"]
        self.assertEqual(len(pts), min(400, n))
        self.assertTrue(all(round(c, 7) == c for pt in pts for c in pt))

    def test_no_owner_fields_requested(self):
        seen = []

        def spy(url, params, **kw):
            seen.append(params)
            return {"features": [], "samples": []}
        self.patch(arcgis, "request_json", spy)
        self.patch(arcgis, "cached_json", lambda *a, **k: None)
        f = si.Fetcher(sleep=lambda s: None)
        si.county_near(G, f)
        si.transmission(G, f)
        with self.assertRaises(sp.PlanError):
            si.county_parcels(["07 1234"], f)
        for p in seen:
            self.assertIsNone(re.search(r"(?i)owner|mail", str(p.get("outFields", ""))))
        self.assertLessEqual({p["outFields"] for p in seen}, si.OUT_FIELDS_ALLOWED)

    def test_fail_closed(self):
        bad = [arcgis.ArcGISError("down"), {}, {"features": [], "exceededTransferLimit": True}]
        for ans in bad:
            ff = FakeFetch(lambda url, p, a=ans: a)
            self.assertEqual(si.county_near(G, ff)[1].state, "not_checked")
            self.assertEqual(si.transmission(G, ff)[1].state, "not_checked")
        ff = FakeFetch(lambda url, p: {"features": [{"attributes": {"VOLTAGE": 115}, "geometry": None}]})
        lines, st = si.transmission(G, ff)
        self.assertEqual((lines, st.state), ([], "not_checked"))
        ff = FakeFetch(lambda url, p: {"features": []})
        self.assertEqual(si.transmission(G, ff)[1].state, "none")

    def test_dem_partial_and_failed(self):
        plane = sp.Plane(wgs(box(0, 0, 1000, 1000)))
        parcel = plane.ft(wgs(box(0, 0, 1000, 1000)))
        calls = [0]

        def answers(url, p):
            calls[0] += 1
            n = len(json.loads(p["geometry"])["points"])
            if calls[0] % 2 == 0:
                return arcgis.ArcGISError("timeout")
            return {"samples": [{"locationId": i, "value": "300.5" if i % 7 else "NoData"} for i in range(n)]}
        d = si.dem(plane, parcel, FakeFetch(answers))
        self.assertEqual(d.state, "partial")
        self.assertLess(d.coverage, 0.95)
        d2 = si.dem(plane, parcel, FakeFetch(lambda u, p: arcgis.ArcGISError("down")))
        self.assertEqual((d2.state, d2.elev), ("not_checked", {}))
        self.assertIn("failed", d2.reason)

    def test_frontage_states(self):
        self.patch(frontage, "check_parcel", lambda g: frontage.not_checked(f"{frontage.SERVICE_FAILED} (x)"))
        f = si.Fetcher(stop_after=2)
        for _ in range(3):
            fr = si.frontage_input(G, f)
            self.assertEqual((fr.state, fr.line_wgs), ("not_checked", None))
        self.assertIn("maps.itos.uga.edu", f.down)

    def test_layers_failure(self):
        def boom(layer, tiles, region):
            raise arcgis.ArcGISError("layer down")
        self.patch(layers, "fetch", boom)
        clipped, near, st = si.constraints(G)
        self.assertEqual(st.state, "not_checked")
        self.assertIn("flood", st.reason)

    def test_throttle_and_stop(self):
        t = [100.0]
        slept = []
        self.patch(arcgis, "request_json", lambda url, params, **kw: {"features": []})
        cache = {}
        self.patch(arcgis, "cached_json", lambda url, params, **kw: cache.get(json.dumps(params, sort_keys=True)))
        f = si.Fetcher(sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
        for k in range(5):
            f.call(si.COUNTY_PARCELS_URL + "/query", {"where": f"x={k}"}, check=si._features_check)
        self.assertGreaterEqual(sum(slept), 4.0)
        cache[json.dumps({"where": "x=0"}, sort_keys=True)] = ({"features": []}, 0)
        n = len(slept)
        f.call(si.COUNTY_PARCELS_URL + "/query", {"where": "x=0"}, check=si._features_check)
        self.assertEqual(len(slept), n)          # a cached answer does not wait
        self.patch(arcgis, "request_json", mock.Mock(side_effect=arcgis.ArcGISError("down")))
        g = si.Fetcher(sleep=lambda s: None, stop_after=3)
        for k in range(3):
            with self.assertRaises(arcgis.ArcGISError):
                g.call(si.TRANSMISSION_URL + "/query", {"k": k}, check=si._features_check)
        with self.assertRaises(si.HostDown):
            g.call(si.TRANSMISSION_URL + "/query", {"k": 9}, check=si._features_check)
        self.assertEqual(arcgis.request_json.call_count, 3)
        lines, st = si.transmission(G, g)
        self.assertEqual(st.state, "not_checked")

    def test_where_clause_ids(self):
        self.assertEqual(si._where_ids(["14F0053  LL0275"]), "ParcelID = '14F0053  LL0275'")
        self.assertEqual(si._where_ids(["07 1", "07 2"]), "ParcelID IN ('07 1','07 2')")
        for bad in ("A') OR (1=1", "x;", "a", "07 1 ", " 07", "07\x001", "0" * 40):
            with self.assertRaises(sp.PlanError):
                si._where_ids([bad])

    def test_county_parcels_exactly_one(self):
        g1 = esri_poly(box(LON0, LAT0, LON0 + 0.001, LAT0 + 0.001))
        ff = FakeFetch(lambda u, p: {"features": [{"attributes": {"ParcelID": "07 1"}, "geometry": g1},
                                                  {"attributes": {"ParcelID": "07 1"}, "geometry": g1}]})
        with self.assertRaises(sp.PlanError):
            si.county_parcels(["07 1"], ff)
        ff = FakeFetch(lambda u, p: {"features": [{"attributes": {"ParcelID": "14F0053  LL0275"}, "geometry": g1}]})
        got = si.county_parcels(["14F0053  LL0275"], ff)
        self.assertEqual(list(got), ["14F0053  LL0275"])
        self.assertIn("'14F0053  LL0275'", ff.seen[0][1]["where"])
        self.assertEqual(ff.seen[0][1]["outFields"], "ParcelID")


# ---------------------------------------------------------------------------
# Owner guard, files and the CLI
# ---------------------------------------------------------------------------
def fake_run(root: Path, owner=OWNER, title_owner=False) -> Path:
    run = root / "run-x"
    run.mkdir()
    rows = [{"rank": "1", "parcel_id": "T1", "jurisdiction": "South Fulton / unincorporated",
             "site_address": "1 TEST RD" if not title_owner else "1 test  owner alpha rd", "acres": "9.6", "soil_hsg": "B",
             "zoning": "M-1", "lead_grade": "B", "owner": owner, "owner_mailing": "99 HIDDEN LANE BOX 7, ATLANTA GA"},
            {"rank": "2", "parcel_id": "T2", "jurisdiction": "Fairburn", "site_address": "2 TEST RD", "acres": "4.0",
             "soil_hsg": "B/D", "zoning": "M-1", "lead_grade": "C", "owner": "OTHER PARTY HOLDINGS LLC",
             "owner_mailing": "2 TEST RD"}]
    with open(run / "ranked.csv", "w", encoding="utf-8", newline="") as fh:
        import csv
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    feats = [{"type": "Feature", "properties": {"rank": 1, "parcel_id": "T1"}, "geometry": mapping(G)},
             {"type": "Feature", "properties": {"rank": 2, "parcel_id": "T2"}, "geometry": mapping(wgs(box(2000, 0, 2400, 400)))}]
    (run / "ranked.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return run


class OwnerGuard(unittest.TestCase):

    def test_hits_with_case_spacing_and_suffix(self):
        terms = sp.owner_terms([{"owner": OWNER, "owner_mailing": "99 Hidden Lane, Atlanta GA", "site_address": "1 X", "jurisdiction": "Fairburn"}])
        for planted in ("test owner alpha", "Test  Owner-Alpha, L.L.C.", "TEST OWNER ALPHA llc", "ＴＥＳＴ OWNER ALPHA"):
            self.assertEqual(owner_hit_fields(planted, terms), {"owner"}, planted)
        self.assertEqual(owner_hit_fields("99 HIDDEN LANE ATLANTA GA", terms), {"owner_mailing"})
        self.assertEqual(sp.owner_hits("a test owner alphabet", terms), {})
        self.assertEqual(sp.owner_hits("Fairburn site", terms), {})

    def test_place_names_and_site_addresses_skipped(self):
        rows = [{"owner": "SOUTH FULTON CITY OF", "owner_mailing": "6 MAIN ST", "site_address": "6 MAIN ST", "jurisdiction": "South Fulton / unincorporated"},
                {"owner": "CITY OF SOUTH FULTON", "owner_mailing": "", "site_address": "", "jurisdiction": "Atlanta"}]
        terms = sp.owner_terms(rows)
        self.assertEqual(sp.owner_hits("#1 6 MAIN ST, South Fulton / unincorporated", terms), {})
        self.assertEqual(sp.owner_hits("owned by city of south fulton", terms), {"owner": 1})

    def test_write_refuses_and_writes_nothing(self):
        terms = sp.owner_terms([{"owner": OWNER, "owner_mailing": "", "site_address": "", "jurisdiction": "x"}])
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            for payload in ('<svg><text>Test Owner  ALPHA</text></svg>', json.dumps({"title": "#1 test owner alpha llc"})):
                with self.assertRaises(sp.OwnerDataFound) as cm:
                    sp.write_atomic(base / "plans.json", payload.encode(), base, terms)
                self.assertNotIn("alpha", str(cm.exception).lower())
            self.assertEqual(list(base.iterdir()), [])


def owner_hit_fields(text, terms):
    return set(sp.owner_hits(text, terms))


class Files(unittest.TestCase):

    def test_slugs_and_names(self):
        self.assertEqual(sp.slug("115+170"), "115-170")
        for bad in ("../x", "a/b", "x\x00", "", "a" * 65, "a b"):
            with self.assertRaises(sp.PlanError):
                sp.slug(bad)
        self.assertEqual(sp.svg_name("115-170"), "plan-115-170.svg")
        with self.assertRaises(sp.PlanError):
            sp.svg_name("../x")

    def test_parse_options(self):
        got = sp._parse_assemble(["north=07 1,07 2"])
        self.assertEqual(got, [("north", ["07 1", "07 2"])])
        for bad in ("../x=07 1,07 2", "a/b=07 1,07 2", "n=07 1", "n=07 1,07 1", "n=" + ",".join(f"07 {k}" for k in range(13)),
                    "n=A') OR (1=1,07 2", "n=07 1;07 2"):
            with self.assertRaises(sp.PlanError, msg=bad):
                sp._parse_assemble([bad])
        self.assertEqual(sp._parse_cut(["25=09F350101520017:100"]), {"25": ("09F350101520017", 100.0)})
        for bad in ("25=X:abc", "25=X", "../2=X:10", "25=x y:10", "25=X:-1", "25=X:nan"):
            with self.assertRaises(sp.PlanError, msg=bad):
                sp._parse_cut([bad])

    def test_atomic_write_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            f = base / "plans.json"
            f.write_bytes(b"old")
            with mock.patch.object(os, "replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    sp.write_atomic(f, b"new", base, None)
            self.assertEqual(f.read_bytes(), b"old")
            self.assertEqual(sorted(p.name for p in base.iterdir()), ["plans.json"])
            with self.assertRaises(sp.PlanError):
                sp.write_atomic(base / ".." / "escape.json", b"x", base, None)

    def test_out_dir_refusals(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run"
            run.mkdir()
            for out in (run, run / "plans"):
                with self.assertRaises(sp.PlanError):
                    sp.check_out_dir(out, run)
            sp.check_out_dir(Path(td) / "elsewhere", run)
        with self.assertRaises(sp.PlanError):
            sp.check_out_dir(SCREENER / "tests" / "no_such_out", Path("/nonexistent-run"))
        sp.check_out_dir(SCREENER / "output" / "x", Path("/nonexistent-run"))


class Cli(Offline):
    title = None          # a test may plant a title (owner-guard tests)

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sp.main(argv)
        return code, out.getvalue(), err.getvalue()

    def fake_inputs(self):
        def gather(spec, fetch, opts):
            # 600 x 700 ft: a site that is feasible without any patch, so the
            # CLI and viewer round trips carry a counted plan end to end.
            inp = site(600, 700, key=spec.key, rank=spec.name, title=self.title or spec.title,
                       parcel_ids=spec.parcel_ids, tax_acres=spec.tax_acres, assembled=spec.assembled)
            inp.meta = spec.meta
            return inp
        self.patch(si, "gather", gather)
        self.patch(si, "sources", lambda: {"test": "offline"})

    def test_end_to_end_and_merge(self):
        self.fake_inputs()
        run = fake_run(self.tmp)
        out = self.tmp / "out"
        code, so, se = self.run_cli([str(run), "--ranks", "1", "--out", str(out)])
        self.assertEqual(code, 0, se)
        body = json.loads((out / "plans.json").read_text())
        self.assertEqual(body["schema_version"], sp.SCHEMA_VERSION)
        self.assertEqual(body["run"]["name"], "run-x")
        self.assertEqual(len(body["run"]["ranked_csv_sha256"]), 64)
        rec = body["plans"][0]
        self.assertEqual((rec["rank"], rec["key"], rec["parcel_ids"], rec["svg"]), ("1", "1", ["T1"], "plan-1.svg"))
        self.assertIs(rec["feasible"], True)
        self.assertGreater(rec["total_sf"], 0)
        svg = (out / "plan-1.svg").read_text()
        self.assertIn("DRAFT", svg)
        ET.fromstring(svg)
        # merge keeps the done site; a plain re-run replaces the file and drops stale plans
        code, so, _ = self.run_cli([str(run), "--ranks", "1,2", "--out", str(out), "--merge"])
        self.assertEqual(code, 0)
        body2 = json.loads((out / "plans.json").read_text())
        self.assertEqual([r["rank"] for r in body2["plans"]], ["1", "2"])
        self.assertEqual(so.count("feasible"), 1)
        hsg2 = [f["flag"] for f in sum(body2["plans"][1]["flags"].values(), [])]
        self.assertIn("HSG_ASSUMED", hsg2)
        code, _, _ = self.run_cli([str(run), "--ranks", "2", "--out", str(out)])
        body3 = json.loads((out / "plans.json").read_text())
        self.assertEqual([r["rank"] for r in body3["plans"]], ["2"])
        self.assertFalse((out / "plan-1.svg").exists())
        code, _, se = self.run_cli([str(run), "--ranks", "2", "--out", str(out), "--merge", "--parking", "code"])
        self.assertEqual(code, 2)
        self.assertIn("refused", se)

    def test_plans_round_trip_into_the_viewer(self):
        """The planner's real output (plans.json + SVGs) builds a viewer
        page with no join flag: the run fingerprint and parcel ids join it,
        the facts the page shows are all present, and DRAFT is kept."""
        from screener import viewer
        self.fake_inputs()
        run = fake_run(self.tmp)
        out = self.tmp / "out"
        code, _, se = self.run_cli([str(run), "--ranks", "1,2", "--out", str(out)])
        self.assertEqual(code, 0, se)
        page = self.tmp / "page.html"
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            vcode = viewer.main([str(run), "--plans", str(out), "--out", str(page)])
        self.assertEqual(vcode, 0, err.getvalue())
        html_text = page.read_text(encoding="utf-8")
        data = json.loads(re.search(r"const PLANS = (.*?);\n", html_text, re.S).group(1))
        plans = {p["id"]: p for p in data}
        body = json.loads((out / "plans.json").read_text())
        self.assertEqual(set(plans), {"plan-" + r["key"] for r in body["plans"] if r.get("svg")})
        self.assertTrue(any(r["feasible"] is True and r["total_sf"] > 0 for r in body["plans"]))
        for rec in body["plans"]:
            if not rec.get("svg"):
                continue
            p = plans["plan-" + rec["key"]]
            self.assertEqual(p["sfText"], f"{rec['total_sf']:,} SF" if rec["total_sf"] else p["sfText"])
            self.assertEqual((p["pids"], p["zoningStatus"], p["sf"]), (rec["parcel_ids"], "draft", rec["total_sf"]))
            self.assertEqual(p["feasible"], rec["feasible"])
            for section, spec in viewer.PLAN_FACTS.items():
                if isinstance(rec.get(section), dict):
                    self.assertEqual(set(spec) - set(rec[section]), set(), section)   # every shown fact is written
            self.assertEqual(p["v4"]["doors"], rec["doors"])
            self.assertIn("DRAFT", p["svg"])
            self.assertTrue(p["v4"]["operator_decisions"])
        self.assertIn("DRAFT", html_text)

    def test_owner_in_title_writes_nothing(self):
        self.fake_inputs()
        run = fake_run(self.tmp)
        self.title = "#1 1 TEST RD, held by Test Owner Alpha LLC"
        out = self.tmp / "out"
        code, so, se = self.run_cli([str(run), "--ranks", "1", "--out", str(out)])
        self.assertEqual(code, 3)
        self.assertNotIn("alpha", (so + se).lower())
        self.assertEqual([p.name for p in out.rglob("*")], [])

    def test_owner_name_in_a_site_address_is_the_site_address(self):
        # The shared guard's rule: an owner string that is part of a site
        # address of the run is that site's published address, not owner data.
        self.fake_inputs()
        run = fake_run(self.tmp, title_owner=True)
        code, _, se = self.run_cli([str(run), "--ranks", "1", "--out", str(self.tmp / "out")])
        self.assertEqual(code, 0, se)

    def owner_refused(self, run, argv, title):
        self.title = title
        out = Path(tempfile.mkdtemp(dir=self.tmp)) / "out-guard"
        code, so, se = self.run_cli([str(run), *argv, "--out", str(out)])
        self.assertEqual(code, 3, se)
        self.assertEqual([p.name for p in out.rglob("*")], [], "a refused site writes no file")
        return so + se

    def test_owner_guard_is_the_shared_guard(self):
        """R1: XML-escaped & and ', a mailing street line, an accented name
        (JSON \\u escapes) and an assemblage member from rejected.csv are all
        caught, and --out stays empty."""
        self.fake_inputs()
        run = fake_run(self.tmp, owner="O'NEAL & SONS DEVELOPMENT LLC")
        with open(run / "ranked.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        rows[1]["owner"], rows[1]["owner_mailing"] = "CAFÉ ÉLAN HOLDINGS LLC", "55 ELM ST, ATLANTA GA 30301"
        with open(run / "ranked.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        with open(run / "rejected.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["parcel_id", "owner", "owner_mailing", "site_address", "jurisdiction"])
            w.writeheader()
            w.writerow({"parcel_id": "07 3", "owner": "REJECTED MEMBER HOLDINGS LLC", "owner_mailing": "7 QUIET WAY, X GA",
                        "site_address": "9 OTHER RD", "jurisdiction": "Fairburn"})
        g3 = esri_poly(wgs(box(600, 0, 900, 700)))
        self.patch(si, "county_parcels", lambda pids, fetch: {p: shapely.geometry.shape({"type": "Polygon", "coordinates": g3["rings"]}) for p in pids})
        cases = [(["--ranks", "1"], "#1 1 TEST RD, O'Neal & Sons Development LLC"),     # & and ' escaped in the SVG
                 (["--ranks", "2"], "#2 2 TEST RD, mail to 55 Elm St"),                  # the mailing street line
                 (["--ranks", "2"], "#2 2 TEST RD, Café Élan Holdings"),                 # \u escapes in plans.json
                 (["--assemble", "n=T1,07 3"], "n: Rejected Member Holdings")]          # rejected.csv owner
        for argv, title in cases:
            with self.subTest(title=title):
                text = self.owner_refused(run, argv, title).lower()
                for bad in ("neal", "elm st", "élan", "rejected member"):
                    self.assertNotIn(bad, text)
        # the plans.json body is checked before the SVG is written: owner text
        # only in the record leaves no SVG behind (R1)
        self.patch(sr, "render", lambda d, rec: '<svg xmlns="http://www.w3.org/2000/svg"><text>clean</text></svg>')
        self.owner_refused(run, ["--ranks", "1"], "#1 O'Neal & Sons Development")
        self.owner_refused(run, ["--ranks", "2"], "#2 Café Élan Holdings")      # only as \u escapes in plans.json

    def test_fetch_failure_through_the_real_gather_exits_1(self):
        """R26: the real gather turns a failed fetch into a not-checked input;
        the plan is written (feasible null) and the CLI exits 1."""
        run = fake_run(self.tmp)
        self.patch(si, "sources", lambda: {})
        self.patch(si.Fetcher, "call", mock.Mock(side_effect=arcgis.ArcGISError("host down (test)")))
        self.patch(layers, "fetch", mock.Mock(side_effect=arcgis.ArcGISError("layer down (test)")))
        self.patch(frontage, "check_parcel", lambda g: frontage.not_checked(f"{frontage.SERVICE_FAILED} (test)"))
        out = self.tmp / "o"
        code, so, se = self.run_cli([str(run), "--ranks", "1", "--out", str(out)])
        self.assertEqual(code, 1, se)
        self.assertIn("incomplete", se)
        rec = json.loads((out / "plans.json").read_text())["plans"][0]
        self.assertIn(rec["feasible"], (None, False))
        self.assertTrue({"TRANSMISSION_NOT_CHECKED", "LAYERS_NOT_CHECKED", "NEIGHBOURS_NOT_CHECKED"}
                        <= {f["flag"] for f in rec["flags"]["1"]})
        self.assertEqual(rec["total_sf"], 0)

    def test_bad_arguments_refused_before_any_request(self):
        run = fake_run(self.tmp)
        spy = mock.Mock(side_effect=AssertionError("asked"))
        self.patch(arcgis, "request_json", spy)
        for argv in (["--assemble", "x=A') OR (1=1,07 2"], ["--assemble", "../x=07 1,07 2"], ["--ranks", "1;2"],
                     ["--assemble", "n=" + ",".join(f"07 {k}" for k in range(13))], ["--cut-parcel", "9=07 1:10", "--ranks", "1"],
                     ["--ranks", "1", "--trailer-ratio", "x"]):
            code, _, _ = self.run_cli([str(run), "--out", str(self.tmp / "o2")] + argv)
            self.assertEqual(code, 2, argv)
        code, _, _ = self.run_cli([str(run), "--ranks", "1", "--out", str(run)])
        self.assertEqual(code, 2)
        spy.assert_not_called()
        self.assertFalse((self.tmp / "o2").exists())

    def test_unknown_rank_and_fetch_failure_exit_1(self):
        run = fake_run(self.tmp)

        def gather(spec, fetch, opts):
            raise arcgis.ArcGISError("county down")
        self.patch(si, "gather", gather)
        self.patch(si, "sources", lambda: {})
        code, _, se = self.run_cli([str(run), "--ranks", "1,77", "--out", str(self.tmp / "o")])
        self.assertEqual(code, 1)
        self.assertIn("77", se)
        self.assertEqual(json.loads((self.tmp / "o" / "plans.json").read_text())["plans"], [])

    def test_planner_value_error_propagates(self):
        self.fake_inputs()
        run = fake_run(self.tmp)
        self.patch(sp, "plan_site", mock.Mock(side_effect=ValueError("bug")))
        with self.assertRaises(ValueError):
            self.run_cli([str(run), "--ranks", "1", "--out", str(self.tmp / "o")])

    def test_assemblage_spec(self):
        run = si.load_run(fake_run(self.tmp))
        g3 = esri_poly(wgs(box(600, 0, 900, 700)))
        ff = FakeFetch(lambda u, p: {"features": [{"attributes": {"ParcelID": "07 3"}, "geometry": g3}]})
        spec = si.site_spec(run, "north+x", ["T1", "07 3"], None, ff, sp.PlanOptions())
        self.assertEqual((spec.key, spec.parcel_ids, spec.assembled, spec.tax_acres), ("north-x", ["T1", "07 3"], True, None))
        self.assertNotIn("owner", spec.title.lower())
        self.assertAlmostEqual(sp.Plane(spec.geometry).ft(spec.geometry).area, 900 * 700, delta=900 * 700 * 0.002)
        self.assertEqual(spec.meta["jurisdiction"], "South Fulton / unincorporated")
        spec2 = si.site_spec(run, "both", ["T1", "T2"], None, ff, sp.PlanOptions())
        self.assertAlmostEqual(spec2.tax_acres, 13.6)
        self.assertEqual(len(ff.seen), 1)


# ---------------------------------------------------------------------------
# Reference sites (trimmed, owner-free fixtures of the 2026-11-13 run)
# ---------------------------------------------------------------------------
def load_fixture(name):
    d = json.loads((FIXTURES / f"{name}.json").read_text())
    dem = sp.Dem(step=d["dem"]["step"], points={tuple(k): tuple(p) for k, p in d["dem"]["points"]},
                 elev={tuple(k): z for k, z in d["dem"]["elev"]}, state="checked")
    fr = d["frontage"]
    inp = sp.SiteInputs(d["key"], d["rank"], d["title"], d["parcel_ids"], shape(d["geometry"]), d["meta"], d["tax_acres"],
                        cut_wgs=shape(d["cut"]) if d.get("cut") else None,
                        constraints_wgs=[shape(x) for x in d["constraints"]], constraints_near_wgs=[shape(x) for x in d["near"]],
                        transmission=[(a, shape(x)) for a, x in d["transmission"]],
                        frontage=sp.FrontageInput(fr["state"], shape(fr["line"]) if fr["line"] else None, fr["feet"], fr["road"]),
                        neighbours=[(a, shape(x)) for a, x in d["neighbours"]], dem=dem,
                        states={k: sp.InputState("checked") for k in STATES})
    return inp, d["expected"]


@unittest.skipUnless(FIXTURES.is_dir(), "no reference fixtures")
class ReferenceSites(Offline):
    angles = False

    def test_reference_sites_match_the_prototype(self):
        for name in sorted(p.stem for p in FIXTURES.glob("*.json")):
            inp, exp = load_fixture(name)
            rec, _ = sp.plan_site(inp, sp.PlanOptions(behaviour="prototype"))
            got = {"configuration": rec["configuration"], "L": rec["building"]["L_ft"], "D": rec["building"]["D_ft"],
                   "sf": rec["building"]["sf"], "doors": rec["doors"], "trailers": rec["trailers"]["total"],
                   "cars": rec["cars"]["provided"], "ponds": rec["detention"]["ponds"], "storage": rec["detention"]["storage_acft"],
                   "feasible": rec["feasible"], "flags": sorted(names(rec))}
            self.assertEqual(got, exp, name)


# ---------------------------------------------------------------------------
# Room for the pond (reviews/40): move the building before shortening it;
# other pond proportions where a 2:1 pond fits nowhere
# ---------------------------------------------------------------------------
class PondShapes(unittest.TestCase):

    def test_other_proportions_hold_the_same_volume(self):
        for v in (0.8, 1.9, 4.5):
            self.assertEqual(sp.pond_dims(v), sp.pond_dims(v, ratio=2.0))          # the default is the old 2:1, to the bit
            for k in sp.POND_FALLBACK_RATIOS:
                w0, lf, wf, _ = sp.pond_dims(v, ratio=k)
                a = lambda h: (k * w0 + 2 * sp.POND_Z * h) * (w0 + 2 * sp.POND_Z * h)
                vol = sp.POND_DEPTH * (a(0) + a(sp.POND_DEPTH) + 4 * a(sp.POND_DEPTH / 2)) / 6
                self.assertAlmostEqual(vol / sp.SQFT_PER_AC, v, places=3, msg=(v, k))
                ht = sp.POND_DEPTH + sp.FREEBOARD
                self.assertAlmostEqual(lf, k * w0 + 2 * sp.POND_Z * ht + 2 * sp.RIM)
                self.assertAlmostEqual(wf, w0 + 2 * sp.POND_Z * ht + 2 * sp.RIM)

    def test_a_finer_position_finds_room_between_samples(self):
        # samples every 40 ft at 20, 60, 100, ...; a strip 200 ft wide from
        # x = 0 to 200 holds a 190 ft wide pond only centred at x = 100 (a
        # sample) or near it; shift the strip 20 ft and only a centre between
        # two samples (x = 120 + 20 ... ) works.
        dem = sp.Dem(step=40.0, points={(i, j): (20 + 40 * i, 20 + 40 * j) for i in range(12) for j in range(12)},
                     elev={(i, j): 900.0 + i for i in range(12) for j in range(12)}, state="checked")
        strip = box(20, 0, 220, 480)                         # centre x = 120, between samples 100 and 140
        near = box(300, 0, 400, 480)
        self.assertIsNone(sp.place_sized_pond(strip, 300, 199, dem, [0, 90], near, None, None))
        got = sp.place_sized_pond(strip, 300, 199, dem, [0, 90], near, None, None, fine=True)
        self.assertIsNotNone(got)
        self.assertTrue(strip.contains(got))

    def test_the_basis_says_when_a_pond_is_not_2_to_1(self):
        self.assertEqual(sp._pond_shape_text({"pond_ratios": [2.0]}, None), "L=2W")
        self.assertEqual(sp._pond_shape_text({}, None), "L=2W")
        txt = sp._pond_shape_text({"pond_ratios": [2.0]}, (None, {"pond_ratios": [3.0]}))
        self.assertIn("L=3W", txt)
        self.assertIn("a 2:1 pond did not fit", txt)


@unittest.skipUnless(FIXTURES.is_dir(), "no reference fixtures")
class PondRoom(Offline):
    angles = False          # the real search angles, as in ReferenceSites

    def test_the_building_moves_to_leave_its_pond_room(self):
        # reference site #21: the longest fit left the pond no room and the
        # building used to be shortened where it stood (68,000 SF); the
        # same layout moved leaves the pond room at 440 ft (reviews/40)
        inp, _ = load_fixture("rank21")
        rec, _ = sp.plan_site(inp)
        self.assertIs(rec["feasible"], True)
        self.assertEqual((rec["building"]["L_ft"], rec["building"]["D_ft"], rec["building"]["sf"]), (440.0, 190, 84000))
        self.assertEqual(rec["configuration"], "rear-load 190 deep, court only, cars along office wall")
        self.assertGreaterEqual(rec["inputs"]["pond_relocation"]["moved"], 1)
        self.assertEqual(rec["detention"]["ponds"], 1)
        # without the move, the old plan
        with mock.patch.object(sp, "RELOCATE_MAX_CHECKS", 0):
            old, _ = sp.plan_site(inp)
        self.assertEqual(old["building"]["sf"], 68000)
        self.assertEqual(old["inputs"]["pond_relocation"]["checks"], 0)

    def test_a_move_does_not_turn_the_docks_to_the_street(self):
        # reference site #25: a 72,000 SF layout exists with its truck court
        # on the arterial and no trailer stalls; the planner keeps its plan
        inp, _ = load_fixture("rank25")
        rec, _ = sp.plan_site(inp)
        self.assertEqual((rec["building"]["sf"], rec["trailers"]["total"]), (67000, 7))
        self.assertEqual(rec["configuration"], "rear-load 160 deep, court + trailer row, cars along office wall")

    def test_the_prototype_behaviour_does_not_move(self):
        inp, exp = load_fixture("rank21")
        rec, _ = sp.plan_site(inp, sp.PlanOptions(behaviour="prototype"))
        self.assertEqual(rec["building"]["sf"], exp["sf"])
        self.assertNotIn("pond_relocation", rec["inputs"])

    def test_the_allowance_is_a_count(self):
        inp, _ = load_fixture("rank21")
        with mock.patch.object(sp, "RELOCATE_MAX_CHECKS", 3):
            rec, _ = sp.plan_site(inp)
        self.assertLessEqual(rec["inputs"]["pond_relocation"]["checks"], 3)
        with mock.patch.object(sp, "RELOCATE_MAX_CHECKS", 3):
            again, _ = sp.plan_site(inp)
        self.assertEqual(json.dumps(rec, sort_keys=True, default=str), json.dumps(again, sort_keys=True, default=str))

# ---------------------------------------------------------------------------
# Static guards
# ---------------------------------------------------------------------------
NEW = ("siteplan.py", "siteplan_inputs.py", "siteplan_render.py", "siteplan_assess.py")


class Static(unittest.TestCase):

    def src(self, name):
        return (SCREENER / name).read_text(encoding="utf-8")

    def imports(self, name):
        out = set()
        for node in ast.walk(ast.parse(self.src(name))):
            if isinstance(node, ast.Import):
                out |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mod = ("." * node.level) + (node.module or "")
                out |= {f"{mod}:{a.name}" for a in node.names} | {mod}
        return out

    def module_level_imports(self, name):
        out = set()
        for node in ast.parse(self.src(name)).body:
            if isinstance(node, ast.Import):
                out |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                out |= {f"{'.' * node.level}{node.module or ''}:{a.name}" for a in node.names}
        return out

    def test_pure_modules_do_not_import_the_network(self):
        for name in ("siteplan.py", "siteplan_render.py", "siteplan_assess.py"):
            imps = self.module_level_imports(name)
            for bad in ("requests", ".:arcgis", ".:layers", ".:frontage", ".:checks", ".:siteplan_inputs", ".frontage:check_parcel"):
                self.assertNotIn(bad, imps, name)
            self.assertFalse(any(i.startswith(".arcgis") or i.startswith(".layers") for i in imps), name)

    def test_dependencies(self):
        allowed_top = {"__future__", "argparse", "ast", "copy", "csv", "hashlib", "heapq", "json", "math", "os", "re", "subprocess",
                       "sys", "textwrap", "time", "unicodedata", "dataclasses", "pathlib", "typing", "urllib", "shapely", "requests"}
        for name in NEW:
            for i in self.imports(name):
                if i.startswith("."):
                    continue
                top = i.split(":")[0].split(".")[0]
                self.assertIn(top, allowed_top, (name, i))
                if name != "siteplan_inputs.py":
                    self.assertNotEqual(top, "requests", name)
            s = self.src(name)
            self.assertIsNone(re.search(r"requests\.(get|post)\(", s))
            self.assertNotIn("ThreadPoolExecutor", s)
            self.assertIsNone(re.search(r"arcgis\.cache_days\s*=(?!=)", s))
            for bad in ("PIL", "numpy", "sys.path", "building_prototypes.json", "zoning_rules.json", "import site_fit", "ATL_PARKING_VERIFY", "startswith(\"BR"):
                self.assertNotIn(bad, s, (name, bad))
            self.assertIsNone(re.search(r"(?i)owner[\"'\]]?\)?\s*\.?\s*(upper|lower)?\(?\)?\.startswith", s))

    def test_private_access_allow_list(self):
        allowed = {("checks", "_overlap"), ("plan", "_esc"), ("arcgis", "_session"),
                   ("frontage", "_params"), ("frontage", "_answer_problem")}
        for name in NEW:
            tree = ast.parse(self.src(name))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr.startswith("_") and not node.attr.startswith("__"):
                    base = node.value.id if isinstance(node.value, ast.Name) else None
                    if base in {"sp", "si", "self", "sr", None} or base and base[0] == "_":
                        continue
                    self.assertIn((base, node.attr), allowed, name)
                if isinstance(node, ast.ImportFrom) and node.module in ("plan",) and node.level == 1:
                    self.assertEqual([a.name for a in node.names], ["_esc"])

    def test_out_fields_have_no_owner(self):
        for val in si.OUT_FIELDS_ALLOWED:
            self.assertIsNone(re.search(r"(?i)owner|mail|\*", val))
        tree = ast.parse(self.src("siteplan_inputs.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "outFields":
                        self.assertIsInstance(v, ast.Name)       # a named, allow-listed constant
                        self.assertIn(getattr(si, v.id), si.OUT_FIELDS_ALLOWED)

    def test_no_reference_ids_or_paths(self):
        for name in NEW:
            s = self.src(name)
            for bad in ("07 2600013", "09F350101520017", "screener-data", "/tmp/", "scratchpad", "plans4"):
                self.assertNotIn(bad, s, (name, bad))

    def test_import_leaves_arcgis_globals(self):
        before = arcgis.cache_days
        code = ("import screener.arcgis as a; d = a.cache_days; import screener.siteplan, screener.siteplan_inputs, "
                "screener.siteplan_render; print(a.cache_days == d)")
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(SCREENER.parent), timeout=60)
        self.assertEqual(res.stdout.strip(), "True", res.stderr[-400:])
        self.assertEqual(arcgis.cache_days, before)

    def test_existing_screener_files_do_not_name_the_new_modules(self):
        for f in ("rank.py", "scoring.py", "screens.py", "run.py", "report.py", "autoresearch/evaluate.py", "autoresearch/build_eval.py"):
            p = SCREENER / f
            if p.exists():
                s = p.read_text(encoding="utf-8")
                self.assertNotIn("siteplan", s, f)
        self.assertFalse(any("elevation.nationalmap" in l.url or "Transmission" in l.url for l in layers.LAYERS.values()))



# ---------------------------------------------------------------------------
# Review round 1 fixes (reviews/37_screener_site_planner, Agent 2)
# ---------------------------------------------------------------------------
TWO_FRONTS = shapely.MultiLineString([[(0, 0), (1600, 0)], [(0, 1000), (1600, 1000)]])


class LayoutVersusSiteFlags(Offline):
    """A flag no other layout can clear (a draft height cap, a frontage too
    short for a truck driveway) blocks the site, not the layout: the plan
    drawn, the second-building search, the rescue pass and geometry_ok are
    those of the same site without the flag."""

    def rules(self):
        r = dict(sp.JUR_DRAFT)
        r["capped"] = dict(r["south fulton"], height=35, cover=None)
        r["nocap"] = dict(r["south fulton"], height=None, cover=None)
        r["cpark"] = dict(r["college park"])
        r["cpfree"] = dict(r["college park"], height=None)
        return r

    def test_height_cap_keeps_the_second_building(self):
        with mock.patch.object(sp, "JUR_DRAFT", self.rules()):
            capped, _ = sp.plan_site(site(1600, 1000, front=TWO_FRONTS, jur="Capped"))
            free, _ = sp.plan_site(site(1600, 1000, front=TWO_FRONTS, jur="NoCap"))
        self.assertIn("second_building", free)
        self.assertIn("second_building", capped, names(capped, 1))   # was never searched under the cap
        self.assertEqual(capped["second_building"]["sf"], free["second_building"]["sf"])
        self.assertEqual(capped["total_sf_if_access"], free["total_sf_if_access"])
        self.assertEqual([a["geometry_ok"] for a in capped["alternatives"]], [a["geometry_ok"] for a in free["alternatives"]])
        self.assertTrue(any(a["geometry_ok"] for a in capped["alternatives"]))
        self.assertIn("HEIGHT_CAP", names(capped, 1))
        self.assertIs(capped["feasible"], False)
        self.assertEqual(capped["total_sf"], 0)
        assert_invariant(self, capped)

    def test_height_cap_does_not_pick_a_broken_larger_layout(self):
        # 700 x 800 College Park: the largest layout fails its pond, a route
        # and the 70% impervious cap; a smaller one fails only the draft
        # 35 ft height cap, and it is the one drawn (as without the cap).
        with mock.patch.object(sp, "JUR_DRAFT", self.rules()):
            capped, _ = sp.plan_site(site(700, 800, jur="cpark"))
            free, _ = sp.plan_site(site(700, 800, jur="cpfree"))
        self.assertEqual(names(capped, 1), {"HEIGHT_CAP"})
        self.assertTrue(capped["detention"]["fits"])
        self.assertEqual(capped["configuration"], free["configuration"])
        self.assertEqual(capped["total_sf_if_access"], free["total_sf_if_access"])
        self.assertEqual(capped["plan_checks"], [])
        assert_invariant(self, capped)

    def test_short_frontage_does_not_pick_a_broken_larger_layout(self):
        landlocked, _ = planned("ll700x800", lambda: site(700, 800, front_state="none"))
        self.assertTrue(landlocked["detention"]["fits"])
        for ft in (5, 150):
            with self.subTest(frontage_ft=ft):
                rec, _ = sp.plan_site(site(700, 800, front=LineString([(0, 0), (ft, 0)])))
                self.assertEqual(names(rec, 1), {"ACCESS_CONSTRAINED"})       # no avoidable POND_DOES_NOT_FIT
                self.assertTrue(rec["detention"]["fits"])
                self.assertEqual(rec["total_sf_if_access"], landlocked["total_sf_if_access"])
                self.assertIs(rec["feasible"], False)
                assert_invariant(self, rec)

    def test_rescue_and_second_gate_ignore_length_independent_flags(self):
        o = {"flags": [("HEIGHT_CAP", 1, "x"), ("ACCESS_CONSTRAINED", 1, "y"), ("POND_DOES_NOT_FIT", 1, "z"), ("RELIEF", 2, "r")]}
        self.assertEqual(sp._blocking(o), {"POND_DOES_NOT_FIT"})
        self.assertEqual(sp.LENGTH_INDEPENDENT_FLAGS, {"HEIGHT_CAP", "ACCESS_CONSTRAINED"})
        # decide() still blocks the site on them
        self.assertEqual(sp.decide([("HEIGHT_CAP", 1, "x")], True, False, sp.PlanOptions()), (False, []))


class DetentionAndSoils(Offline):

    def test_reported_storage_is_what_the_ponds_hold(self):
        """No drop yard placed: the volume re-computed without it is smaller,
        and the ponds are re-sized to it, so storage, count and drawn ponds
        agree (they kept the first, larger estimate before)."""
        o = sp.PlanOptions(trailer_tiers=sp.trailer_tiers_from("5"))
        vols = []
        real = sp.detention_storage

        def spy(*a):
            vols.append(real(*a))
            return vols[-1]
        with mock.patch.object(sp, "place_near", lambda *a, **k: None), mock.patch.object(sp, "detention_storage", spy):
            rec, d = sp.plan_site(site(800, 800), o)
        self.assertIn("TRAILER_SHORT", names(rec))
        self.assertEqual(rec["trailers"]["drop_yards"], 0)
        self.assertTrue(any(b < a - 1e-6 for a, b in zip(vols[::2], vols[1::2])))   # the smaller re-computed volume
        v = d.o["v_det"]
        self.assertEqual(rec["detention"]["storage_acft"], round(v, 1))
        self.assertTrue(rec["detention"]["fits"])
        if rec["detention"]["fits"]:
            n = len(d.o["ponds"])
            self.assertEqual(n, sp.pond_count(v))
            _, Lf, Wf, _ = sp.pond_dims(v / n)
            for pond in d.o["ponds"]:
                self.assertAlmostEqual(pond.area, Lf * Wf, delta=0.01 * Lf * Wf)

    def test_assemblage_rules_are_compared(self):
        meta = {"jurisdiction": "South Fulton / unincorporated", "hsg": "D", "zoning": "M-1",
                "members": [{"rank": "1", "jurisdiction": "South Fulton / unincorporated", "hsg": "D", "zoning": "M-1"},
                            {"rank": "2", "jurisdiction": "East Point", "hsg": "B", "zoning": "I-2"}],
                "members_unranked": 1}
        flags = sp.assemblage_flags(meta, "south fulton")
        self.assertIn(("ASSEMBLAGE_MIXED_RULES", 1), [(f, s) for f, s, _ in flags])
        self.assertIn(("ASSEMBLAGE_MEMBER_NOT_RANKED", 2), [(f, s) for f, s, _ in flags])
        self.assertEqual(sp.assemblage_hsg(meta, "D", None)[0], "B")      # the larger pond
        inp = site(600, 700, assembled=True)
        inp.meta = meta
        rec, _ = sp.plan_site(inp)
        self.assertIn("ASSEMBLAGE_MIXED_RULES", names(rec, 1))
        self.assertEqual(rec["gated"], ["USE_PERMIT_REQUIRED"])            # the East Point member's gate
        self.assertIn("member #2", " ".join(f["note"] for f in rec["flags"][1] if f["flag"] == "USE_PERMIT_REQUIRED"))
        self.assertIn("HSG B", rec["detention"]["basis"])
        self.assertIs(rec["feasible"], False)
        assert_invariant(self, rec)
        same = dict(meta, members=[dict(m, jurisdiction="Fairburn", zoning="M-1") for m in meta["members"]], members_unranked=0)
        self.assertEqual(sp.assemblage_flags(same, "fairburn"), [])
        zoned = dict(same, members=[same["members"][0], dict(same["members"][1], zoning="R-1")])
        self.assertEqual([(f, s) for f, s, _ in sp.assemblage_flags(zoned, "fairburn")], [("ASSEMBLAGE_MIXED_RULES", 2)])

    def test_assemblage_spec_carries_every_member(self):
        run = si.load_run(fake_run(self.tmp))
        spec = si.site_spec(run, "both", ["T1", "T2"], None, FakeFetch(lambda u, p: {"features": []}), sp.PlanOptions())
        self.assertEqual([(m["rank"], m["jurisdiction"], m["hsg"]) for m in spec.meta["members"]],
                         [("1", "South Fulton / unincorporated", "B"), ("2", "Fairburn", "B/D")])
        self.assertEqual(spec.meta["members_unranked"], 0)
        self.assertNotIn("owner", json.dumps(spec.meta).lower())


class FailClosedDefaults(Offline):

    def test_site_inputs_with_default_fields_are_incomplete(self):
        inp = site(600, 700)
        bare = sp.SiteInputs("k", "1", "t", ["T1"], inp.geometry, inp.meta)
        rec, _ = sp.plan_site(bare)
        self.assertIsNone(rec["feasible"])
        self.assertEqual(rec["total_sf"], 0)
        self.assertTrue({"FRONTAGE_NOT_CHECKED", "DEM_NOT_CHECKED", "LAYERS_NOT_CHECKED"} <= names(rec, 1))
        self.assertEqual(rec["inputs"]["states"]["frontage"]["state"], "not_checked")
        # a states entry that disagrees with the frontage object fails closed too
        st = dict(inp.states, frontage=sp.InputState("checked"))
        rec2, _ = sp.plan_site(site(600, 700, front_state="not_checked", states=st))
        self.assertIn("FRONTAGE_NOT_CHECKED", names(rec2, 1))
        self.assertIsNone(rec2["feasible"])


class RealGather(Offline):
    """The real siteplan_inputs.gather with every fetch faked: states, the
    near-water shapes (R21) and the --cut-parcel construction."""

    def fakes(self, water):
        def lfetch(layer, tiles, region):
            if layer is layers.LAYERS["wetlands"]:
                return layers.Index([(water, {})])
            return layers.Index([])
        self.patch(layers, "fetch", lfetch)
        front = wgs(LineString([(0, 0), (600, 0)]))
        self.patch(frontage, "check_parcel", lambda g: frontage.Frontage(frontage.CHECKED, feet=600, road="local road, test", line=front))

        def answers(url, p):
            if url == si.DEM_URL:
                n = len(json.loads(p["geometry"])["points"])
                return {"samples": [{"locationId": i, "value": "275.0"} for i in range(n)]}
            if "where" in p:
                return {"features": [{"attributes": {"ParcelID": "07 9"}, "geometry": esri_poly(wgs(box(0, 600, 100, 700)))}]}
            return {"features": []}
        return FakeFetch(answers)

    def test_gather_states_near_water_and_cut(self):
        water = wgs(box(-200, 0, -2, 700))               # a wetland 2 ft outside the west line
        ff = self.fakes(water)
        run = si.load_run(fake_run(self.tmp))
        spec = si.site_spec(run, "1", None, ("07 9", 50.0), ff, sp.PlanOptions())
        inp = si.gather(spec, ff, sp.PlanOptions())
        self.assertEqual({k: v.state for k, v in inp.states.items()},
                         {"layers": "none", "transmission": "none", "neighbours": "checked", "frontage": "checked", "dem": "checked"})
        self.assertEqual(inp.constraints_wgs, [])
        self.assertEqual(len(inp.constraints_near_wgs), 1)
        # the cut: the county shape (100 x 100 ft corner) plus 50 ft, in the site plane
        plane = sp.Plane(inp.geometry)
        cut = plane.ft(inp.cut_wgs)
        self.assertAlmostEqual(cut.area, box(0, 600, 100, 700).buffer(50).area, delta=0.01 * cut.area)
        rec, _ = sp.plan_site(inp)
        self.assertIs(rec["feasible"], True, rec["flags"][1])
        clean = sp.plan_site(dataclasses_replace(inp, constraints_near_wgs=[]))[0]
        self.assertLess(rec["inputs"]["paving_envelope_ac"], clean["inputs"]["paving_envelope_ac"])    # the 25 ft buffer reaches in
        self.assertGreater(rec["inputs"]["test_cut_ac"], 0)
        hit = si.constraints(wgs(box(-50, 100, 600, 700)))
        self.assertEqual(hit[2].state, "checked")                    # the wetland inside the line
        self.assertEqual(len(hit[0]), 1)

    def test_near_water_across_a_tile_edge_is_fetched(self):
        asked = []

        def lfetch(layer, tiles, region):
            asked.append(list(tiles))
            return layers.Index([])
        self.patch(layers, "fetch", lfetch)
        edge = -84.40
        g = box(edge - 0.004, 33.61, edge - 0.00002, 33.613)       # ends about 6 ft short of the 0.05 deg tile line
        si.constraints(g)
        east_tile = any(t[0] == round(edge, 10) or abs(t[0] - edge) < 1e-9 for t in asked[0])
        self.assertTrue(east_tile, asked[0])


def dataclasses_replace(obj, **kw):
    import dataclasses
    return dataclasses.replace(obj, **kw)


class FetcherCache(Offline):

    def test_cached_answer_is_used_after_the_host_is_down(self):
        cache = {json.dumps({"k": "cached"}, sort_keys=True): ({"features": []}, 0)}
        self.patch(arcgis, "cached_json", lambda url, params, **kw: cache.get(json.dumps(params, sort_keys=True)))
        self.patch(arcgis, "request_json", mock.Mock(side_effect=arcgis.ArcGISError("down")))
        f = si.Fetcher(sleep=lambda s: None, stop_after=2)
        for k in range(2):
            with self.assertRaises(arcgis.ArcGISError):
                f.call(si.TRANSMISSION_URL + "/query", {"k": k}, check=si._features_check)
        self.assertTrue(f.down)
        self.assertEqual(f.call(si.TRANSMISSION_URL + "/query", {"k": "cached"}, check=si._features_check), {"features": []})
        with self.assertRaises(si.HostDown):
            f.call(si.TRANSMISSION_URL + "/query", {"k": "new"}, check=si._features_check)
        self.assertEqual(arcgis.request_json.call_count, 2)


class CliRound1(Cli):

    def test_merge_plans_an_incomplete_site_again(self):
        self.fake_inputs()
        run = fake_run(self.tmp)
        out = self.tmp / "out"
        self.assertEqual(self.run_cli([str(run), "--ranks", "1,2", "--out", str(out)])[0], 0)
        body = json.loads((out / "plans.json").read_text())
        for r in body["plans"]:
            r["feasible"] = None
            r["flags"]["1"].append({"flag": "DEM_NOT_CHECKED", "note": "test"})
        (out / "plans.json").write_text(json.dumps(body))
        code, so, se = self.run_cli([str(run), "--ranks", "1", "--out", str(out), "--merge"])
        self.assertEqual(code, 1, se)                       # rank 2 is still incomplete in --out
        self.assertRegex(so, r"(?m)^1 .* feasible True")    # rank 1 planned again
        self.assertIn("incomplete plan(s) kept by --merge", se)
        recs = {r["rank"]: r for r in json.loads((out / "plans.json").read_text())["plans"]}
        self.assertIs(recs["1"]["feasible"], True)
        self.assertIsNone(recs["2"]["feasible"])
        code, _, se = self.run_cli([str(run), "--ranks", "2", "--out", str(out), "--merge"])
        self.assertEqual(code, 0, se)

    def test_rank_like_assemble_names_and_key_collisions(self):
        self.fake_inputs()
        run = fake_run(self.tmp)
        out = self.tmp / "out"
        for bad in (["--assemble", "2=T1,T2"], ["--assemble", "3+4=T1,T2"]):
            code, _, se = self.run_cli([str(run), "--out", str(out)] + bad)
            self.assertEqual(code, 2, (bad, se))
            self.assertIn("ranks", se)
        code, _, se = self.run_cli([str(run), "--out", str(out), "--assemble", "1+2=T1,T2"])
        self.assertEqual(code, 0, se)                      # a rank name listing exactly those parcels
        code, _, se = self.run_cli([str(run), "--out", str(out), "--assemble", "a+b=T1,T2"])
        self.assertEqual(code, 0, se)
        code, so, se = self.run_cli([str(run), "--out", str(out), "--assemble", "a-b=T1,T2", "--merge"])
        self.assertEqual(code, 2, se)                      # slugs to the kept plan's key "a-b"
        self.assertIn("already a plan", se)

    def test_time_budget_must_be_a_positive_number(self):
        run = fake_run(self.tmp)
        for v in ("nan", "inf", "0", "-5", "1e9"):
            code, _, se = self.run_cli([str(run), "--ranks", "1", "--out", str(self.tmp / "o"), "--max-seconds-per-site", v])
            self.assertEqual(code, 2, v)
            self.assertIn("max-seconds", se)

    def test_code_hash_and_options_cover_the_prototypes(self):
        o = sp.PlanOptions()
        self.assertIn("sitefit.py", sp.CODE_FILES)
        before = (sp.code_hash(), o.decisions())
        rear = dict(sitefit.PROTOTYPES["rear"])
        with mock.patch.dict(sitefit.PROTOTYPES, {"rear": dict(rear, L=rear["L"] + 200)}):
            self.assertNotEqual(o.decisions(), before[1])     # --merge refuses: the options differ
        self.assertEqual(o.decisions()["prototype_dims"]["rear"]["L"], rear["L"])

    def test_python_m_keeps_planning_after_a_bad_rank(self):
        run = fake_run(self.tmp)
        env = dict(os.environ, HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9")
        res = subprocess.run([sys.executable, "-m", "screener.siteplan", str(run), "--ranks", "99999,88888",
                              "--out", str(self.tmp / "o")], capture_output=True, text=True, cwd=str(SCREENER.parent),
                             env=env, timeout=120)
        self.assertEqual(res.returncode, 1, res.stderr[-800:])
        self.assertNotIn("Traceback", res.stderr)
        self.assertIn("99999: rank 99999 is not in the run", res.stderr)
        self.assertIn("88888: rank 88888 is not in the run", res.stderr)   # the loop went on


# ---------------------------------------------------------------------------
# Review round 4 (reviews/37): fixes and their regression tests
# ---------------------------------------------------------------------------
def rotated_site(w, h, ang, **kw):
    """A w x h rectangle and its frontage on y=0, turned together by ang
    degrees about the origin."""
    from shapely import affinity
    g = affinity.rotate(box(0, 0, w, h), ang, origin=(0, 0))
    fr = affinity.rotate(LineString([(0, 0), (w, 0)]), ang, origin=(0, 0))
    return site(shapes=[g], front=fr, **kw)


class RotatedModules(Offline):
    angles = False

    def test_road_court_seam_is_closed_on_a_rotated_module(self):
        """A point on the court just past the end-road seam is truck
        pavement at any angle; the prototype's plain inset lost a 12 ft band
        there once rotated (the union of the rectangles kept a crack)."""
        prog = sp.Program("south fulton", sp.jur_rules("south fulton")[1], "fulton", sp.PlanOptions())
        v = [x for x in sp.variants() if x["type"] == "rear" and x["D"] == 230 and not x["row"] and x["cars"] == "side"][0]
        cd = sp.car_rows_layout(1)[1]
        cracked = 0
        for ang in (0.0, 33.0, 61.7, 90.0, 123.4):
            for fv in (False, True):
                for fu in (False, True):
                    depth, lmod = sp.geometry_of(v, 500, 1)
                    fr = sp.Frame(lmod, depth, sitefit.Placement(1000.0, ang, 0.0, 0.0), (1234.5, -987.25), fv, fu)
                    p = sp.build(v, 500, 1, fr, prog, draw=False)
                    q = Point(fr.pt(sp.TRUCK_ROAD - 11, cd + 230 + 0.9))
                    with self.subTest(angle=ang, flipv=fv, flipu=fu):
                        self.assertTrue(sp.own_truck_area(p, True).contains(q))
                    cracked += not sp.own_truck_area(p, False).contains(q)
        self.assertGreater(cracked, 0)          # the prototype inset, kept for parity, has the crack

    def test_rotated_rectangle_gives_no_route_outside_flag(self):
        """A plain rectangle turned 33 degrees (fit angles along its sides)
        plans like the same rectangle on the axes, with no stray route."""
        axis, _ = planned("route700x600", lambda: site(700, 600))
        with mock.patch.object(sitefit, "_angles", lambda rings: [33.0, 123.0]):
            rec, _ = sp.plan_site(rotated_site(700, 600, 33.0))
        self.assertNotIn("ROUTE_OUTSIDE_ENVELOPE", names(rec), rec["plan_checks"])
        self.assertFalse([c for c in rec["plan_checks"] if "route outside" in c["check"]])
        self.assertIs(rec["feasible"], True)
        self.assertGreaterEqual(rec["total_sf"], 0.95 * axis["total_sf"])

    def test_route_stray_is_reported_with_one_decimal(self):
        from types import SimpleNamespace as NS
        big = box(0, 0, 1000, 1000)
        for fixes, want in ((True, 5.1), (False, 5)):
            ctx = NS(B=big, P=big, parcel=big, wet_buf=Polygon(), trans=Polygon(),
                     prog=NS(opts=sp.PlanOptions(behaviour="fixes" if fixes else "prototype")))
            p = NS(building=box(100, 100, 200, 200), module=box(90, 90, 210, 210))
            o = {"ponds": [], "drops": [], "route_polys": [], "n_main_routes": 0, "route_outside": [("truck", 5.14)]}
            got = sp.check_plan(p, o, ctx)
            self.assertEqual(got, [{"check": "truck route outside its paving envelope", "ft": want}])

    def test_yield_spread_over_rotations_is_bounded(self):
        """Orientation sensitivity (documented in SITE_PLANNING.md, about
        +/-30%): with the real fit angles the same parcel turned on the map
        stays feasible and within that band."""
        totals = []
        for ang in (0.0, 30.0):
            rec, _ = sp.plan_site(rotated_site(600, 700, ang))
            self.assertIs(rec["feasible"], True, ang)
            totals.append(rec["total_sf"])
        self.assertGreaterEqual(min(totals), 0.7 * max(totals), totals)


class MultipartParcel(Offline):

    def test_pond_and_yard_stay_on_the_buildings_piece(self):
        """One parcel split by a 60 ft road: the pond does not go across
        the road from the building, and the parcel is flagged."""
        from shapely.geometry import MultiLineString
        shapes = [box(0, 0, 600, 700), box(660, 0, 960, 700)]
        fr = MultiLineString([[(600, 0), (600, 700)], [(660, 0), (660, 700)]])
        rec, d = planned("multipart-road", lambda: site(shapes=shapes, front=fr))
        self.assertIn("PARCEL_MULTIPART", names(rec, 3))
        assert_invariant(self, rec)
        home = [g for g in sp.parts(d.parcel) if g.intersects(d.p.module)]
        self.assertEqual(len(home), 1)
        for g in d.o["ponds"] + d.o["drops"]:
            self.assertGreater(g.intersection(home[0]).area, 0.99 * g.area)
        # the same west piece on its own, with the same road side (the open
        # yard applies to the other three sides, so the road side matters)
        alone, _ = planned("west-piece-alone", lambda: site(600, 700, front=LineString([(600, 0), (600, 700)])))
        self.assertLessEqual(rec["total_sf"], alone["total_sf"] * 1.05)   # the far piece adds no building area
        assembled, _ = planned("multipart-assembled", lambda: site(shapes=shapes, front=fr, assembled=True))
        self.assertNotIn("PARCEL_MULTIPART", names(assembled))
        self.assertIn("ASSEMBLAGE_NOT_CONTIGUOUS", names(assembled, 1))


class FrontageCacheAfterDown(Offline):

    def test_cached_frontage_is_measured_after_gdot_is_down(self):
        f = si.Fetcher(sleep=lambda s: None, stop_after=1)
        f.down.add(f._host(frontage.IDENTIFY_URL))
        ask = mock.Mock(side_effect=AssertionError("asked GDOT"))
        self.patch(frontage, "check_parcel", ask)
        kept = {}

        def cached(url, params, **kw):
            self.assertEqual(url, frontage.IDENTIFY_URL)
            self.assertIs(kw.get("check"), frontage._answer_problem)
            return kept.get(json.dumps(params, sort_keys=True))
        self.patch(arcgis, "cached_json", cached)
        miss = f.frontage(G)
        self.assertEqual(miss.state, frontage.NOT_CHECKED)
        self.assertIn(frontage.SERVICE_FAILED, miss.reason)
        kept[json.dumps(frontage._params(G), sort_keys=True)] = ({"results": []}, 0)
        hit = f.frontage(G)
        self.assertEqual(hit.state, frontage.NONE)
        ask.assert_not_called()


class CliRound4(Offline):
    # Cli's helpers without re-running Cli's own tests.
    title = None
    run_cli = Cli.run_cli
    fake_inputs = Cli.fake_inputs

    def fake_spec(self):
        seen = []

        def spec(run, name, pids, cut, fetch, opts):
            seen.append((name, pids, cut))
            return si.SiteSpec(name, sp.slug(name), f"#{name}", list(pids or [run.rows[name]["parcel_id"]]), G,
                               {"jurisdiction": "South Fulton / unincorporated", "hsg": "B", "zoning": "M-1"},
                               None, bool(pids))
        self.patch(si, "site_spec", spec)
        return seen

    def test_merge_plans_a_site_again_when_its_definition_changed(self):
        self.fake_inputs()
        seen = self.fake_spec()
        run = fake_run(self.tmp)
        out = self.tmp / "out"
        code, _, se = self.run_cli([str(run), "--ranks", "1", "--assemble", "east=T1,T2", "--out", str(out)])
        self.assertEqual(code, 0, se)
        # the same definition (members in another order): kept, nothing planned
        seen.clear()
        code, _, se = self.run_cli([str(run), "--ranks", "1", "--assemble", "east=T2,T1", "--out", str(out), "--merge"])
        self.assertEqual((code, seen), (0, []), se)
        # a new cut and other members: both sites planned again
        code, _, se = self.run_cli([str(run), "--ranks", "1", "--cut-parcel", "1=T2:50", "--assemble", "east=T1,T2,T9",
                                    "--out", str(out), "--merge"])
        self.assertEqual(code, 0, se)
        self.assertEqual(sorted(n for n, _, _ in seen), ["1", "east"])
        self.assertIn("planned again", se)
        body = json.loads((out / "plans.json").read_text())
        got = {r["key"]: r["site_definition"] for r in body["plans"]}
        self.assertEqual(got, {"1": {"members": None, "cut": ["T2", 50.0]},
                               "east": {"members": ["T1", "T2", "T9"], "cut": None}})

    def test_cut_parcel_given_twice_for_a_site_refused(self):
        with self.assertRaises(sp.PlanError):
            sp._parse_cut(["4=14F001 LL0010:50", "4=14F001 LL0020:100"])
        self.assertEqual(sp._parse_cut(["4=14F001 LL0010:50", "5=14F001 LL0020:100"]),
                         {"4": ("14F001 LL0010", 50.0), "5": ("14F001 LL0020", 100.0)})
        run = fake_run(self.tmp)
        code, _, se = self.run_cli([str(run), "--ranks", "1", "--cut-parcel", "1=T2:50", "--cut-parcel", "1=T2:80",
                                    "--out", str(self.tmp / "o")])
        self.assertEqual(code, 2, se)
        self.assertIn("given twice", se)

    def test_cache_days_must_be_a_finite_number_not_below_zero(self):
        run = fake_run(self.tmp)
        spy = mock.Mock(side_effect=AssertionError("asked"))
        self.patch(arcgis, "request_json", spy)
        for bad in ("nan", "inf", "-1"):
            code, _, se = self.run_cli([str(run), "--ranks", "1", "--cache-days", bad, "--out", str(self.tmp / "o")])
            self.assertEqual(code, 2, bad)
            self.assertIn("--cache-days", se)
        spy.assert_not_called()
        self.assertFalse((self.tmp / "o").exists())

    def test_code_files_cover_every_imported_screener_module(self):
        for name in ("frontage.py", "checks.py", "layers.py", "arcgis.py", "sitefit.py", "geo.py", "plan.py", "viewer.py"):
            self.assertIn(name, sp.CODE_FILES)
        for name in sp.CODE_FILES:
            tree = ast.parse((SCREENER / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1:
                    mods = [node.module.split(".")[0]] if node.module else [a.name for a in node.names]
                    for m in mods:
                        if (SCREENER / f"{m}.py").exists():
                            self.assertIn(f"{m}.py", sp.CODE_FILES, name)

if __name__ == "__main__":
    unittest.main()
