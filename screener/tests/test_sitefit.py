"""F6: constraint-aware building fit (reviews/34_screener_quick_wins).
Offline, on the test_integrity fakes and hand-made shapes in feet.

The fit is a live-only report fact: run.main computes it after the sort, so
no scorer or screen ever sees it. The spy tests are the real guard; the
static stem test is a backstop.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import csv
import io
import json
import math
import re
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import shapely
from shapely import affinity
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon, box

from screener import checks, geo, layers, rank, report, run, scoring, screens, sitefit
from screener.tests.test_integrity import (
    A, B, C, RunHarness, county_parcel, indexes, square,
)

SCREENER = Path(run.__file__).resolve().parent
ROOT = SCREENER.parent
F6_KEYS = sitefit.KEY_STEMS


def in_degrees(g_ft, at=A):
    """A shape drawn in feet, placed in WGS84 with its origin at ``at``."""
    proj = geo.LocalProjection(*at)

    def fwd(c):
        out = c / geo.FT_PER_M
        out[:, 0] = out[:, 0] / proj.kx + proj.lon0
        out[:, 1] = out[:, 1] / proj.ky + proj.lat0
        return out
    return shapely.transform(g_ft, fwd)


def fit(g_ft, open_ft=None, constrained=0.0, **kw):
    geom = in_degrees(g_ft)
    og = geom if open_ft is None else in_degrees(open_ft)
    return sitefit.fit_parcel(geom, og, constrained, **kw)


def lengths(g_ft):
    rings, _ = sitefit.normalise(sitefit.polygon_parts(g_ft))
    return {d: (v[0] if v else None) for d, v in sitefit.fit_lengths(rings, sitefit.specs()).items()}


class CopyTests(unittest.TestCase):
    """K5: a faithful copy of the root numbers and search, never a read of them."""

    def test_shapes_and_lattices(self):
        self.assertEqual(sitefit.specs(), [
            (445.0, [400.0 + 40 * i for i in range(7)]),
            (890.0, [600.0 + 40 * i for i in range(11)]),
        ])
        self.assertEqual(sitefit.SMALLEST, (400.0, 445.0))
        self.assertEqual(sitefit.DEFAULT_SETBACK_FT, 25)

    def test_drift_against_root_prototypes(self):
        path = ROOT / "building_prototypes.json"
        if not path.exists():
            self.skipTest("root building_prototypes.json not present")
        data = json.loads(path.read_text())
        self.assertEqual(data["step_ft"], sitefit.STEP_FT)
        self.assertEqual(data["default_setback_ft"], sitefit.DEFAULT_SETBACK_FT)
        self.assertEqual(sorted(data["prototypes"]), sorted(sitefit.PROTOTYPES))
        for key, copied in sitefit.PROTOTYPES.items():
            with self.subTest(key=key):
                for field, value in copied.items():
                    self.assertEqual(data["prototypes"][key][field], value, field)

    def test_drift_against_root_search_constants(self):
        path = ROOT / "site_fit.py"
        if not path.exists():
            self.skipTest("root site_fit.py not present")
        text = path.read_text()     # read as text only: never imported
        for name in ("TOL_FT", "_ANGLE_GRID_DEG", "_EDGE_ANGLES_MAX", "_EDGE_ANGLE_MIN_LEN_FT",
                     "_Y_SAMPLES", "_PROBE_FT", "_SIMPLIFY_ABOVE_VERTICES", "_SIMPLIFY_TOL_FT",
                     "_MAX_FIT_VERTICES"):
            with self.subTest(name=name):
                m = re.search(rf"^{name} = ([0-9.]+)$", text, re.M)
                self.assertIsNotNone(m)
                self.assertEqual(float(m.group(1)), float(getattr(sitefit, name)))
        self.assertIn("return float(p[\"W\"]) + _sides(p) * (max(0.0, float(p[\"court\"])) + t)",
                      text)

    def test_golden_lengths(self):
        # Root site_fit.fit_lengths gave the same lengths AND placements on
        # these rings (run once outside the suite; see 08_f6 response).
        cases = {
            "square 1000": (box(0, 0, 1000, 1000), {445.0: 640.0, 890.0: 960.0}),
            "square 1000 at 30 deg": (affinity.rotate(box(0, 0, 1000, 1000), 30, origin=(0, 0)),
                                      {445.0: 640.0, 890.0: 960.0}),
            "1060 x 900 at 45 deg": (affinity.rotate(box(0, 0, 1060, 900), 45, origin=(0, 0)),
                                     {445.0: 640.0, 890.0: 1000.0}),
            "square 1000, centre hole": (
                Polygon(box(0, 0, 1000, 1000).exterior.coords,
                        [box(450, 450, 550, 550).exterior.coords]),
                {445.0: 640.0, 890.0: None}),
            "two parts": (MultiPolygon([box(0, 0, 700, 500), box(2000, 0, 2700, 950)]),
                          {445.0: 640.0, 890.0: 680.0}),
            "L shape": (Polygon([(0, 0), (1100, 0), (1100, 500), (500, 500), (500, 1100),
                                 (0, 1100)]), {445.0: 640.0, 890.0: None}),
            "strip 3000 x 440": (box(0, 0, 3000, 440), {445.0: 400.0, 890.0: None}),
            "strip 3000 x 460": (box(0, 0, 3000, 460), {445.0: 640.0, 890.0: None}),
            "square 600": (box(0, 0, 600, 600), {445.0: 560.0, 890.0: None}),
        }
        for name, (g, want) in cases.items():
            with self.subTest(name):
                self.assertEqual(lengths(g), want)

    def test_imports_stay_inside_the_screener(self):
        stdlib = set(sys.stdlib_module_names)
        for name in ("sitefit.py", "run.py", "report.py"):
            tree = ast.parse((SCREENER / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    mods = [node.module.split(".")[0]]
                else:
                    continue
                for m in mods:
                    with self.subTest(file=name, module=m):
                        self.assertTrue(m in stdlib or m in ("shapely", "screener"), m)
        text = (SCREENER / "sitefit.py").read_text()
        for word in ("building_prototypes.json\")", "open(", "read_text", "import site_fit"):
            self.assertNotIn(word, text)
        tree = ast.parse(text)
        rel = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level}
        self.assertEqual(rel, {"geo"})

    def test_eval_files_do_not_import_it(self):
        for name in ("autoresearch/build_eval.py", "autoresearch/evaluate.py", "screens.py",
                     "rank.py", "scoring.py", "snapshot.py"):
            path = SCREENER / name
            if path.exists():
                with self.subTest(file=name):
                    self.assertNotIn("sitefit", path.read_text())


class GeometryTests(unittest.TestCase):
    """K3, K7, F6-4: the open-ground part, units, setback and states."""

    def test_unconstrained_parcel_uses_the_whole_part(self):
        f = fit(box(0, 0, 1050, 1050))
        self.assertEqual(f.state, sitefit.FITS)
        self.assertEqual(f.label, "cross-dock")
        self.assertEqual((f.length_ft, f.width_ft, f.building_sf), (960.0, 520.0, 499_200))
        self.assertAlmostEqual(f.largest_open_acres, 1050 ** 2 / 43_560, delta=0.1)

    def test_fully_constrained_fallback_reads_no_open_ground(self):
        g = square(*A)
        idx = indexes()
        idx["flood"] = layers.Index([(g.buffer(0.001), {"FLD_ZONE": "AE", "ZONE_SUBTY": ""})])
        p = county_parcel("P1", A)
        checks.map_checks([p], idx, [(-84.49, 33.605)], 500.0)
        self.assertIs(p["_open_ground"], p["geometry"])        # map_checks' fallback
        self.assertGreater(p["constrained_pct"], 99)
        before = copy.copy(p)
        f = sitefit.fit_parcel(p["geometry"], p["_open_ground"], p["constrained_pct"])
        self.assertEqual(p, before)                            # the fit writes nothing
        self.assertEqual((f.state, f.largest_open_acres), (sitefit.NONE_FOUND, 0.0))
        self.assertEqual(f.fields()["envelope_fit"], "none found")
        self.assertIn("no open ground", f.reason)

    def test_no_constraint_map_checks_gives_the_whole_parcel(self):
        g = square(-84.3, 33.6)
        p = county_parcel("P1", (-84.3, 33.6))
        checks.map_checks([p], indexes(), [(-84.49, 33.605)], 500.0)
        self.assertEqual(p["constrained_pct"], 0.0)
        f = sitefit.fit_parcel(p["geometry"], p["_open_ground"], p["constrained_pct"])
        self.assertAlmostEqual(f.largest_open_acres, p["gis_acres"], delta=0.1)
        self.assertTrue(g.equals(p["geometry"]))

    def test_two_parts_report_the_larger(self):
        a12 = math.sqrt(12 * 43_560)
        a8 = math.sqrt(8 * 43_560)
        open_ft = MultiPolygon([box(0, 0, a12, a12), box(a12 + 200, 0, a12 + 200 + a8, a8)])
        f = fit(box(0, 0, a12 + 200 + a8, a12), open_ft, constrained=20.0)
        self.assertEqual(f.largest_open_acres, 12.0)

    def test_collection_keeps_the_polygon_only(self):
        side = math.sqrt(10 * 43_560)
        coll = GeometryCollection([box(0, 0, side, side), LineString([(0, 0), (900, 900)])])
        geom = in_degrees(box(0, 0, side, side))
        f = sitefit.fit_parcel(geom, in_degrees(coll), 5.0)
        self.assertEqual(f.largest_open_acres, 10.0)

    def test_degrees_are_not_feet(self):
        ew = fit(box(0, 0, 700, 520))
        ns = fit(box(0, 0, 520, 700))
        self.assertEqual((ew.state, ew.label, ew.length_ft), (sitefit.FITS, "front/rear-load", 640.0))
        self.assertEqual((ns.state, ns.label, ns.length_ft), (ew.state, ew.label, ew.length_ft))

    def test_rotated_rectangle_fits_at_full_length(self):
        f = fit(affinity.rotate(box(0, 0, 1110, 950), 45, origin=(0, 0)))
        self.assertEqual((f.label, f.length_ft), ("cross-dock", 1000.0))

    def test_blocking_hole(self):
        whole = box(0, 0, 1050, 1050)
        holed = Polygon(whole.exterior.coords, [box(475, 475, 575, 575).exterior.coords])
        self.assertEqual(fit(whole).label, "cross-dock")
        f = fit(whole, holed, constrained=1.0)
        self.assertEqual((f.state, f.label, f.length_ft), (sitefit.FITS, "front/rear-load", 640.0))

    def test_setback_is_applied(self):
        # 480 ft square: 400 x 445 fits on the raw shape, not inside 25 ft.
        self.assertEqual(fit(box(0, 0, 480, 480), setback_ft=0).state, sitefit.FITS)
        f = fit(box(0, 0, 480, 480))
        self.assertEqual(f.state, sitefit.NONE_FOUND)
        self.assertEqual(f.fields()["envelope_fit"], "none found")
        self.assertIsNone(f.fields()["envelope_building_sf"])

    def test_envelope_polygon_sits_inside_the_setback(self):
        geom = in_degrees(box(0, 0, 1050, 1050))
        f = sitefit.fit_parcel(geom, geom, 0.0)
        env = f.fields()[sitefit.ENVELOPE_KEY]
        proj = geo.LocalProjection.around(geom)
        env_m, geom_m = proj(env), proj(geom)
        self.assertTrue(env_m.within(geom_m))
        self.assertGreater(env_m.distance(geom_m.exterior) * geo.FT_PER_M, 24.9)
        self.assertAlmostEqual(env_m.area * geo.FT_PER_M ** 2, 960 * 890, delta=960 * 890 * 0.005)

    def test_prunes_parts_too_small_for_any_envelope(self):
        self.assertFalse(sitefit.can_hold_smallest(box(0, 0, 399, 2000)))   # too narrow
        self.assertFalse(sitefit.can_hold_smallest(box(0, 0, 420, 420)))    # too little area
        self.assertTrue(sitefit.can_hold_smallest(box(0, 0, 450, 450)))
        f = fit(box(0, 0, 300, 3000))
        self.assertEqual(f.state, sitefit.NONE_FOUND)
        self.assertIn("smallest envelope", f.reason)

    def test_geos_fault_is_not_checked_and_other_errors_raise(self):
        with mock.patch.object(sitefit.shapely, "intersection",
                               side_effect=GEOSException("TopologyException")):
            f = fit(box(0, 0, 1050, 1050))
        self.assertEqual(f.state, sitefit.NOT_CHECKED)
        self.assertTrue(f.fields()["envelope_fit"].startswith("not checked: geometry fault"))
        with mock.patch.object(sitefit.shapely, "intersection", side_effect=ValueError("bug")):
            with self.assertRaises(ValueError):
                fit(box(0, 0, 1050, 1050))


def sawtooth(teeth, amp, side=1500.0):
    """A square whose bottom edge is a sawtooth of ``teeth`` teeth."""
    step = side / teeth
    bottom = [(i * step + (step / 2 if k else 0), -amp * k) for i in range(teeth) for k in (0, 1)]
    return Polygon(bottom + [(side, 0), (side, side), (0, side)])


class CostTests(unittest.TestCase):
    """K6: vertex bound, simplification inside, no time budget, determinism."""

    def test_too_complex_is_not_checked(self):
        # 169 small holes (constraints): they survive the setback and a 1 ft
        # simplify, so the shape stays above 600 vertices.
        holes = [box(x, y, x + 5, y + 5).exterior.coords
                 for x in range(100, 1400, 100) for y in range(100, 1400, 100)]
        f = fit(box(0, 0, 1500, 1500),
                Polygon(box(0, 0, 1500, 1500).exterior.coords, holes), constrained=1.0)
        self.assertEqual(f.state, sitefit.NOT_CHECKED)
        self.assertIn("too complex", f.reason)
        self.assertIsNotNone(f.largest_open_acres)

    def test_simplified_shape_stays_inside(self):
        g = sawtooth(250, 0.4)                  # 500 vertices, removed by a 1 ft simplify
        geom = in_degrees(g)
        f = sitefit.fit_parcel(geom, geom, 0.0)
        self.assertTrue(f.simplified)
        self.assertEqual(f.state, sitefit.FITS)
        self.assertTrue(f.envelope.within(geom))

    def test_worst_case_timing(self):
        holes = [box(x, y, x + 20, y + 20).exterior.coords
                 for x in range(150, 1270, 200) for y in range(150, 1270, 200)]
        g = Polygon(box(0, 0, 1320, 1320).exterior.coords, holes)   # nothing fits
        started = time.monotonic()
        # About 0.7 s each here (every angle is tried when nothing fits);
        # 50 of them took about 35 s, measured once outside the suite.
        for _ in range(4):
            f = fit(box(0, 0, 1320, 1320), g, constrained=2.0)
            self.assertEqual(f.state, sitefit.NONE_FOUND)
        self.assertLess(time.monotonic() - started, 20.0)

    def test_no_wall_clock_in_the_fit(self):
        self.assertNotIn("time", {n.id for n in ast.walk(ast.parse(
            (SCREENER / "sitefit.py").read_text())) if isinstance(n, ast.Name)})


def named_f6(tree: ast.AST) -> list[str]:
    hits = []
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        if text is not None and any(w in text.lower() for w in F6_KEYS):
            hits.append(text)
    return hits


class StaticGuardTests(unittest.TestCase):
    """K2, backstop only: a string built at runtime ("enve" + "lope") gets
    past this; the spy tests below hold."""

    def test_rank_and_scoring_never_name_the_fit(self):
        for name in ("rank.py", "scoring.py"):
            with self.subTest(file=name):
                self.assertEqual(named_f6(ast.parse((SCREENER / name).read_text())), [])

    def test_every_column_carries_a_stem(self):
        for key in (*sitefit.COLUMNS, sitefit.ENVELOPE_KEY, "_envelope_detail"):
            self.assertTrue(any(w in key for w in F6_KEYS), key)

    def test_the_guard_catches_a_named_read(self):
        self.assertEqual(named_f6(ast.parse('p.get("largest_open_acres")')), ["largest_open_acres"])
        self.assertEqual(named_f6(ast.parse('p.get("enve" + "lope_fit")')), [])


class FitRunTests(RunHarness):
    """K1, K4, K8, K9: run.main end to end."""

    def setUp(self):
        super().setUp()
        self.seen: list[tuple[str, frozenset]] = []
        for obj, name in ((rank, "learned_score"), (scoring, "fixed_score"),
                          (screens, "site_rejects"), (screens, "flags")):
            real = getattr(obj, name)

            def spy(p, *a, _real=real, _name=name, **k):
                self.seen.append((_name, frozenset(p)))
                return _real(p, *a, **k)
            self.patch(obj, name, spy)

    def rows(self):
        with open(self.out / "ranked.csv", newline="") as fh:
            return {r["parcel_id"]: r for r in csv.DictReader(fh)}

    def fit_line(self, pid):
        name = report._safe_name(pid)
        [path] = [f for f in (self.out / "briefs").iterdir() if f.name.endswith(name + ".md")]
        [line] = [x for x in path.read_text().splitlines() if "Building fit" in x]
        return line

    def assert_never_seen(self):
        called = {name for name, _ in self.seen}
        self.assertTrue({"learned_score", "fixed_score", "site_rejects", "flags"} <= called)
        for name, keys in self.seen:
            for key in keys:
                self.assertFalse(any(w in key for w in F6_KEYS), f"{key} seen by {name}")

    def test_spy_slope_on_and_off(self):
        for args in ((), ("--no-slope",)):
            with self.subTest(args=args):
                self.seen.clear()
                self.parcels[:] = [county_parcel("14F0001 0001", A),
                                   county_parcel("14F0002 0002", B,
                                                 owner="TEST OWNER BETA")]
                code, _ = self.main(*args)
                self.assertEqual(code, 0)
                self.assert_never_seen()
                for r in self.rows().values():
                    self.assertEqual(r["envelope_fit"], "front/rear-load 640 x 260 ft")
                    self.assertEqual(r["envelope_building_sf"], "166400")
                    self.assertTrue(19.0 < float(r["largest_open_acres"]) < 20.5)

    def test_scores_order_and_requests_do_not_depend_on_the_fit(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        calls = len(self.session.calls)
        keep = ("rank", "score", "flags", "why_site", "why_seller")
        on = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        self.patch(sitefit, "fit_parcel", lambda *a, **k: sitefit.not_checked("patched off"))
        self.parcels[:] = fresh
        shutil.rmtree(self.tmp / "http", ignore_errors=True)   # same requests again
        self.main()
        off = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        self.assertEqual(on, off)
        self.assertEqual(len(self.session.calls), calls)
        self.assertTrue(all(r["envelope_fit"] == "not checked: patched off"
                            for r in self.rows().values()))
        self.assert_never_seen()

    def test_three_states_in_csv_and_brief(self):
        small = county_parcel("14F0003 0003", C, owner="TEST OWNER GAMMA")
        small["geometry"] = square(*C, side_m=142.0)     # about 5 map acres vs 20 on the roll
        self.parcels.append(small)
        self.main()
        rows = self.rows()
        self.assertEqual(rows["14F0001 0001"]["envelope_fit"], "front/rear-load 640 x 260 ft")
        self.assertEqual(rows["14F0003 0003"]["envelope_fit"], "none found")
        self.assertEqual(rows["14F0003 0003"]["envelope_building_sf"], "")
        self.assertTrue(4.5 < float(rows["14F0003 0003"]["largest_open_acres"]) < 5.5)
        fits = self.fit_line("14F0001 0001")
        none = self.fit_line("14F0003 0003")
        for words in ("envelope (building + truck court + trailer parking)", "lower bound",
                      "25 ft lot-line setback", "conservation easements (NCED)",
                      "steep ground is not located", "floodplain (FEMA SFHA)"):
            self.assertIn(words, fits)
        self.assertIn("166,400 SF", fits)
        self.assertIn("no fit found", none)
        self.assertIn("not proof that nothing fits", none)
        self.assertIn("parcel map disagrees with the tax record", none)
        for pid in rows:
            self.assertNotIn("does not fit", self.fit_line(pid))
            self.assertNotIn("too small", self.fit_line(pid))

        code = self.run_with_section("enabled = false\n")
        self.assertEqual(code, 0)
        for pid, r in self.rows().items():
            self.assertEqual(r["envelope_fit"], "not checked: fit switched off in settings")
            self.assertEqual((r["largest_open_acres"], r["envelope_building_sf"]), ("", ""))
            self.assertIn("Building fit: not checked (fit switched off", self.fit_line(pid))
        self.assertIn("Building fit not checked for 3 of 3", self.notes())

    def run_with_section(self, body):
        settings = self.tmp / "settings.toml"
        text = (SCREENER / "settings.toml").read_text()
        head, _ = text.split("[fit]", 1)
        settings.write_text(head + "[fit]\n" + body)
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            return run.main(["--settings", str(settings), "--out", str(self.out)])

    def test_max_parcels_cap_is_by_rank(self):
        self.assertEqual(self.run_with_section("max_parcels = 1\n"), 0)
        rows = sorted(self.rows().values(), key=lambda r: int(r["rank"]))
        self.assertEqual(rows[0]["envelope_fit"], "front/rear-load 640 x 260 ft")
        self.assertEqual(rows[1]["envelope_fit"], "not checked: outside the top 1 ([fit] max_parcels)")
        self.assertIn("Building fit not checked for 1 of 2", self.notes())

    def test_old_settings_without_fit_table_and_bad_values(self):
        settings = self.tmp / "settings.toml"
        settings.write_text((SCREENER / "settings.toml").read_text().split("[fit]", 1)[0])
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["--settings", str(settings), "--out", str(self.out)]), 0)
        self.assertEqual(self.rows()["14F0001 0001"]["envelope_fit"],
                         "front/rear-load 640 x 260 ft")
        for body in ("max_parcels = -1\n", "max_parcels = true\n", "enabled = 1\n",
                     "max_parcels = 2.5\n"):
            with self.subTest(body=body):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    self.run_with_section(body)
                self.assertEqual(e.exception.code, 2)

    def test_geometry_fault_is_counted_and_others_still_fit(self):
        real = sitefit._fit
        first = {"done": False}

        def flaky(*a, **k):
            if not first["done"]:
                first["done"] = True
                raise GEOSException("TopologyException: side location conflict")
            return real(*a, **k)
        self.patch(sitefit, "_fit", flaky)
        code, log = self.main()
        self.assertEqual(code, 0)
        states = sorted(r["envelope_fit"].split(" (")[0] for r in self.rows().values())
        self.assertEqual(states, ["front/rear-load 640 x 260 ft", "not checked: geometry fault"])
        self.assertIn("Building fit not checked for 1 of 2 ranked parcels: geometry fault",
                      self.notes())
        self.assertNotIn("Traceback", log)

    def test_columns_outputs_and_private_envelope(self):
        self.main()
        with open(self.out / "ranked.csv", newline="") as fh:
            header = next(csv.reader(fh))
        i = header.index("mean_cut_fill_ft")
        self.assertEqual(header[i + 1:i + 4], list(sitefit.COLUMNS))
        with open(self.out / "rejected.csv", newline="") as fh:
            self.assertEqual(next(csv.reader(fh)), ["parcel_id", "jurisdiction", "site_address",
                                                    "acres", "owner", "decision", "reasons"])
        props = json.loads((self.out / "ranked.geojson").read_text())["features"][0]["properties"]
        self.assertEqual(props["envelope_fit"], "front/rear-load 640 x 260 ft")
        self.assertNotIn(sitefit.ENVELOPE_KEY, props)
        self.assertIn("front/rear-load 640 x 260 ft", (self.out / "map.html").read_text())
        for p in self.parcels:
            env = p[sitefit.ENVELOPE_KEY]
            self.assertTrue(env.within(p["geometry"]))

    def test_runs_are_deterministic(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        first = (self.out / "ranked.csv").read_bytes()
        self.parcels[:] = fresh
        self.main()
        self.assertEqual((self.out / "ranked.csv").read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
