"""F7: stormwater detention allowance (reviews/34_screener_quick_wins).
Offline, on the test_integrity fakes and hand-made shapes in feet.

The pond is a live-only report fact: run.main places it after the sort and
the F6 fit, so no scorer or screen ever sees it. The spy tests are the real
guard; the static stem test is a backstop.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import csv
import io
import json
import re
import shutil
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import shapely
from shapely.errors import GEOSException
from shapely.geometry import Polygon, box

from screener import detention, geo, plan, rank, report, run, scoring, screens, sitefit
from screener.tests.test_integrity import C, RunHarness, county_parcel, map_checked, square
from screener.tests.test_sitefit import in_degrees

SCREENER = Path(run.__file__).resolve().parent
ROOT = SCREENER.parent
STEMS = detention.KEY_STEMS
NS = "{http://www.w3.org/2000/svg}"


def place(g_ft, open_ft=None, constrained=0.0, fit=None):
    """Fit the F6 envelope (unless ``fit`` is given), then the pond."""
    geom = in_degrees(g_ft)
    og = geom if open_ft is None else in_degrees(open_ft)
    f = fit if fit is not None else sitefit.fit_parcel(geom, og, constrained)
    return f, detention.place_pond(geom, og, constrained, f, f.envelope)


def feet(g_wgs, around):
    proj = geo.LocalProjection.around(around)
    return shapely.transform(proj(g_wgs), lambda c: c * geo.FT_PER_M)


class CopyTests(unittest.TestCase):
    """A faithful copy of the root numbers and formula, never a read of them."""

    def test_numbers(self):
        self.assertEqual(detention.POND, {"L": 400, "W": 200, "depth": 6, "slope": 4})
        self.assertEqual(detention.POND_SF, 80_000)
        self.assertAlmostEqual(detention.VOLUME_CF, 397_856.3, places=1)
        self.assertTrue(9.0 < detention.VOLUME_ACRE_FT < 9.2)
        self.assertTrue(1.83 < detention.POND_ACRES < 1.84)
        self.assertIsNone(detention.detention_volume_cf(100, 40, 6, 4))   # slopes meet

    def test_drift_against_root_prototypes(self):
        path = ROOT / "building_prototypes.json"
        if not path.exists():
            self.skipTest("root building_prototypes.json not present")
        self.assertEqual(json.loads(path.read_text())["detention"], detention.POND)

    def test_drift_against_root_volume_formula(self):
        path = ROOT / "site_fit.py"
        if not path.exists():
            self.skipTest("root site_fit.py not present")

        def body(text):
            for node in ast.parse(text).body:
                if isinstance(node, ast.FunctionDef) and node.name == "detention_volume_cf":
                    return ast.unparse(node)
            return None
        root = body(path.read_text())          # read as text only: never imported
        self.assertIsNotNone(root)
        self.assertEqual(body((SCREENER / "detention.py").read_text()), root)

    def test_imports_stay_inside_the_screener(self):
        text = (SCREENER / "detention.py").read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                mods = [node.module.split(".")[0]]
            else:
                continue
            for m in mods:
                self.assertTrue(m in sys.stdlib_module_names or m == "shapely", m)
        rel = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level}
        self.assertEqual(rel, {None, "geo"})      # "from . import sitefit" + geo
        for word in ("building_prototypes.json\")", "open(", "read_text", "import site_fit",
                     "requests", "http"):
            self.assertNotIn(word, text)

    def test_eval_files_do_not_import_it(self):
        for name in ("autoresearch/build_eval.py", "autoresearch/evaluate.py", "screens.py",
                     "rank.py", "scoring.py", "snapshot.py"):
            path = SCREENER / name
            if path.exists():
                with self.subTest(file=name):
                    self.assertNotIn("detention", path.read_text())


class GeometryTests(unittest.TestCase):

    def test_pond_fits_beside_the_envelope(self):
        g = box(0, 0, 935, 935)
        f, d = place(g)
        self.assertEqual(f.state, sitefit.FITS)
        self.assertEqual((d.state, d.text(), d.beside_envelope), ("pond fits", "pond fits", True))
        geom = in_degrees(g)
        pond, env = feet(d.pond, geom), feet(f.envelope, geom)
        self.assertAlmostEqual(pond.area, 80_000, delta=1)
        self.assertLess(pond.intersection(env).area, 1.0)                 # no overlap
        inner = feet(geom, geom).buffer(-sitefit.DEFAULT_SETBACK_FT + 0.1)
        self.assertTrue(pond.within(inner))                               # inside the setback
        # 1.84 ac of about 20.07 open acres
        self.assertTrue(9.0 < d.detention_pct < 9.5)

    def test_envelope_leaves_no_room(self):
        # A 1050 ft square takes a 960 ft cross-dock: the strips left over
        # are under 200 ft wide.
        f, d = place(box(0, 0, 1050, 1050))
        self.assertEqual(f.label, "cross-dock")
        self.assertEqual(d.state, detention.NONE_FOUND)
        self.assertEqual(d.text(), "no pond room found")
        self.assertIsNone(d.pond)
        self.assertIsNotNone(d.detention_pct)
        self.assertIn("beside the envelope", d.reason)

    def test_no_envelope_tries_the_open_ground_alone(self):
        f, d = place(box(0, 0, 470, 470))    # 420 ft inside the setback: no envelope
        self.assertEqual(f.state, sitefit.NONE_FOUND)
        self.assertEqual(d.state, detention.FITS)
        self.assertFalse(d.beside_envelope)
        self.assertEqual(d.text(), "pond fits (no building envelope placed)")

    def test_pond_stays_off_constrained_ground(self):
        # Open ground is a 1000 x 150 strip (too narrow) plus nothing else.
        g = box(0, 0, 1000, 1000)
        f, d = place(g, open_ft=box(0, 0, 1000, 150), constrained=85.0)
        self.assertEqual(f.state, sitefit.NONE_FOUND)
        self.assertEqual(d.state, detention.NONE_FOUND)
        self.assertIn("can hold the pond", d.reason)

    def test_fully_constrained_fallback(self):
        geom = in_degrees(box(0, 0, 1000, 1000))
        f = sitefit.fit_parcel(geom, geom, 100.0)
        d = detention.place_pond(geom, geom, 100.0, f, f.envelope)
        self.assertEqual((d.state, d.detention_pct), (detention.NONE_FOUND, None))
        self.assertEqual(d.reason, "no open ground on the parcel map")

    def test_fit_not_checked_means_pond_not_checked(self):
        geom = in_degrees(box(0, 0, 935, 935))
        for f in (None, sitefit.not_checked("fit switched off in settings")):
            d = detention.place_pond(geom, geom, 0.0, f, None)
            self.assertEqual(d.text(), "not checked: building fit not checked")
            self.assertIsNone(d.pond)
            # The share needs only the open ground, so it is still reported.
            self.assertTrue(9.0 < d.detention_pct < 9.5)
            self.assertIn("% of the open ground",
                          detention.brief_line({detention.DETAIL_KEY: d}))
        fits = sitefit.fit_parcel(geom, geom, 0.0)
        d = detention.place_pond(geom, geom, 0.0, fits, None)
        self.assertEqual(d.text(), "not checked: building envelope missing")
        # Fully constrained fallback with the fit off: no share either.
        d = detention.place_pond(geom, geom, 100.0, None, None)
        self.assertEqual((d.state, d.detention_pct), (detention.NOT_CHECKED, None))

    def test_geos_fault_is_not_checked_and_other_errors_raise(self):
        geom = in_degrees(box(0, 0, 935, 935))
        f = sitefit.fit_parcel(geom, geom, 0.0)
        real = detention._place
        try:
            detention._place = lambda *a, **k: (_ for _ in ()).throw(GEOSException("boom"))
            d = detention.place_pond(geom, geom, 0.0, f, f.envelope)
            self.assertEqual(d.text(), "not checked: geometry fault (boom)")
            detention._place = lambda *a, **k: (_ for _ in ()).throw(KeyError("bug"))
            with self.assertRaises(KeyError):
                detention.place_pond(geom, geom, 0.0, f, f.envelope)
        finally:
            detention._place = real

    def test_worst_case_timing(self):
        # The F6 worst case (100 small holes, under the vertex cap only after
        # simplify, nothing fits): every angle is tried for the pond too.
        # Measured about 3.5 s.
        import time
        holes = [box(x, y, x + 20, y + 20).exterior.coords
                 for x in range(100, 1400, 130) for y in range(100, 1400, 130)]
        started = time.monotonic()
        _, d = place(box(0, 0, 1400, 1400), Polygon(box(0, 0, 1400, 1400).exterior.coords,
                                                     holes), constrained=2.0)
        self.assertEqual(d.state, detention.NONE_FOUND)
        self.assertTrue(d.simplified)
        self.assertLess(time.monotonic() - started, 30.0)

    def test_brief_lines_three_states(self):
        f, fits = place(box(0, 0, 935, 935))
        _, none = place(box(0, 0, 1050, 1050))
        lines = {
            "fits": detention.brief_line({detention.DETAIL_KEY: fits}),
            "none": detention.brief_line({detention.DETAIL_KEY: none}),
            "not": detention.brief_line({detention.DETAIL_KEY: detention.not_checked("x")}),
            "off": detention.brief_line({}),
        }
        self.assertIn("fits on the open ground left beside the fitted building envelope",
                      lines["fits"])
        self.assertIn("400 x 200 ft pond", lines["fits"])
        self.assertIn("9.1 acre-feet", lines["fits"])
        self.assertIn("no pond room found", lines["none"])
        self.assertIn("a shorter building may leave room", lines["none"])
        self.assertTrue(lines["not"].startswith("- Detention pond: not checked (x)"))
        self.assertEqual(lines["off"], "- Detention pond: not checked (detention off)")
        for key in ("fits", "none", "not"):
            for words in ("rough allowance", "no hydrology", "NOAA Atlas 14",
                          "right against the envelope"):
                self.assertIn(words, lines[key])
        for line in lines.values():
            self.assertNotIn("does not fit", line)


class PlanTests(unittest.TestCase):
    """The pond in the F12 site plan."""

    def drawn(self, side_m):
        p = map_checked(square(-84.5, 33.6, side_m=side_m), acres=20.0)
        f = sitefit.fit_parcel(p["geometry"], p["_open_ground"], p["constrained_pct"])
        p.update(f.fields())
        p.update(detention.place_pond(p["geometry"], p["_open_ground"], p["constrained_pct"],
                                      f, f.envelope).fields())
        p["rank"] = 1
        return p

    def test_pond_drawn_inside_the_plan_with_a_legend_row(self):
        p = self.drawn(285.0)
        self.assertEqual(p["pond_fit"], "pond fits")
        svg = plan.render(p)[0]
        root = ET.fromstring(svg)
        paths = {e.get("class"): e for e in root.iter(f"{NS}path") if e.get("class")}
        self.assertIn("pond", paths)
        lo, hi = plan.MARGIN, plan.MARGIN + plan.SIZE
        for pt in paths["pond"].get("d").replace("M", " ").replace("L", " ").replace(
                "Z", " ").split():
            x, y = map(float, pt.split(","))
            self.assertTrue(lo < x < hi and lo < y < hi, pt)
        words = [t.text for t in root.iter(f"{NS}text")]
        self.assertIn("Detention pond 400 x 200 ft beside the envelope: rough allowance, "
                      "no hydrology", words)
        self.assertEqual(plan.render(copy.deepcopy(p))[0], svg)

    def test_no_room_draws_no_pond(self):
        p = self.drawn(320.0)
        self.assertEqual(p["pond_fit"], "no pond room found")
        root = ET.fromstring(plan.render(p)[0])
        self.assertNotIn("pond", {e.get("class") for e in root.iter(f"{NS}path")})
        self.assertIn("Detention pond: no pond room found (a lower bound)",
                      [t.text for t in root.iter(f"{NS}text")])

    def test_render_writes_nothing_on_the_parcel(self):
        p = self.drawn(285.0)
        keys = set(p)
        plan.render(p)
        self.assertEqual(set(p), keys)


def named(tree: ast.AST) -> list[str]:
    hits = []
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        if text is not None and any(w in text.lower() for w in STEMS):
            hits.append(text)
    return hits


class StaticGuardTests(unittest.TestCase):
    """Backstop only; the spy tests below hold."""

    def test_rank_scoring_and_screens_never_name_it(self):
        for name in ("rank.py", "scoring.py", "screens.py"):
            with self.subTest(file=name):
                self.assertEqual(named(ast.parse((SCREENER / name).read_text())), [])

    def test_every_key_carries_a_stem(self):
        for key in (*detention.COLUMNS, detention.POND_KEY, detention.DETAIL_KEY):
            self.assertTrue(any(w in key for w in STEMS), key)


class PondRunTests(RunHarness):
    """run.main end to end."""

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

    def brief_text(self, pid):
        name = report._safe_name(pid)
        [path] = [f for f in (self.out / "briefs").iterdir() if f.name.endswith(name + ".md")]
        return path.read_text()

    def pond_line(self, pid):
        [line] = [x for x in self.brief_text(pid).splitlines() if "Detention pond" in x]
        return line

    def assert_never_seen(self):
        called = {name for name, _ in self.seen}
        self.assertTrue({"learned_score", "fixed_score", "site_rejects", "flags"} <= called)
        for name, keys in self.seen:
            for key in keys:
                self.assertFalse(any(w in key.lower() for w in STEMS), f"{key} seen by {name}")

    def run_with(self, body, table="detention"):
        settings = self.tmp / "settings.toml"
        text = (SCREENER / "settings.toml").read_text()
        head, tail = text.split(f"[{table}]", 1)
        rest = tail.split("\n[", 1)
        settings.write_text(head + f"[{table}]\n" + body
                            + ("\n[" + rest[1] if len(rest) > 1 else ""))
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            return run.main(["--settings", str(settings), "--out", str(self.out)])

    def test_spy_slope_on_and_off(self):
        for args in ((), ("--no-slope",)):
            with self.subTest(args=args):
                self.seen.clear()
                self.parcels[:] = [county_parcel("14F0001 0001", (-84.5, 33.6)),
                                   county_parcel("14F0002 0002", (-84.48, 33.6),
                                                 owner="TEST OWNER BETA")]
                code, _ = self.main(*args)
                self.assertEqual(code, 0)
                self.assert_never_seen()
                for r in self.rows().values():
                    self.assertEqual(r["pond_fit"], "pond fits")
                    self.assertTrue(8.0 < float(r["detention_pct"]) < 10.5)
                for p in self.parcels:
                    self.assertIn(detention.POND_KEY, p)

    def test_scores_order_and_requests_do_not_depend_on_the_pond(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        calls = len(self.session.calls)
        keep = ("rank", "score", "flags", "why_site", "why_seller", "envelope_fit")
        on = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        self.patch(detention, "place_pond", lambda *a, **k: detention.not_checked("patched off"))
        self.parcels[:] = fresh
        self.clear_cache()
        self.main()
        off = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        self.assertEqual(on, off)
        self.assertEqual(len(self.session.calls), calls)     # no new service is called
        self.assertTrue(all(r["pond_fit"] == "not checked: patched off"
                            for r in self.rows().values()))
        self.assert_never_seen()

    def test_three_states_in_csv_and_brief(self):
        big = county_parcel("14F0003 0003", C, owner="TEST OWNER GAMMA")
        big["geometry"] = square(*C, side_m=320.0)       # a cross-dock leaves no pond room
        self.parcels.append(big)
        self.main()
        rows = self.rows()
        self.assertEqual(rows["14F0001 0001"]["pond_fit"], "pond fits")
        self.assertEqual(rows["14F0003 0003"]["pond_fit"], "no pond room found")
        self.assertNotEqual(rows["14F0003 0003"]["detention_pct"], "")
        self.assertIn("Detention pond (rough allowance, no hydrology): pond fits on 2 of 3 "
                      "ranked parcels, no pond room found on 1", self.notes())
        self.assertIn("beside the fitted building envelope", self.pond_line("14F0001 0001"))
        self.assertIn("no pond room found", self.pond_line("14F0003 0003"))
        for pid in rows:
            self.assertNotIn("does not fit", self.pond_line(pid))
            self.assertIn("rough allowance", self.pond_line(pid))
        svg = (self.out / "briefs" / "001_14F0001_0001.svg").read_text()
        self.assertIn('class="pond"', svg)

        self.assertEqual(self.run_with("enabled = false\n"), 0)
        for pid, r in self.rows().items():
            self.assertEqual(r["pond_fit"], "not checked: detention switched off in settings")
            self.assertEqual(r["detention_pct"], "")
            self.assertIn("Detention pond: not checked (detention switched off",
                          self.pond_line(pid))
        self.assertIn("Detention pond not checked for 3 of 3 ranked parcels", self.notes())

    def test_fit_off_means_pond_not_checked(self):
        self.assertEqual(self.run_with("enabled = false\n", table="fit"), 0)
        for r in self.rows().values():
            self.assertEqual(r["pond_fit"], "not checked: building fit not checked")
            self.assertTrue(8.0 < float(r["detention_pct"]) < 10.5)
        self.assertNotIn("Detention pond (rough allowance", self.notes())
        self.assertIn("Detention pond not checked for 2 of 2 ranked parcels: building fit "
                      "not checked", self.notes())

    def test_max_parcels_cap_is_by_rank(self):
        self.assertEqual(self.run_with("max_parcels = 1\n"), 0)
        rows = sorted(self.rows().values(), key=lambda r: int(r["rank"]))
        self.assertEqual(rows[0]["pond_fit"], "pond fits")
        self.assertEqual(rows[1]["pond_fit"],
                         "not checked: outside the top 1 ([detention] max_parcels)")

    def test_old_settings_without_the_table_and_bad_values(self):
        settings = self.tmp / "settings.toml"
        settings.write_text((SCREENER / "settings.toml").read_text().split("[detention]", 1)[0])
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["--settings", str(settings), "--out", str(self.out)]), 0)
        self.assertEqual(self.rows()["14F0001 0001"]["pond_fit"], "pond fits")
        for body in ("max_parcels = -1\n", "enabled = 1\n", "max_parcels = 2.5\n"):
            with self.subTest(body=body):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    self.run_with(body)
                self.assertEqual(e.exception.code, 2)

    def test_columns_and_private_keys(self):
        self.main()
        with open(self.out / "ranked.csv", newline="") as fh:
            header = next(csv.reader(fh))
        i = header.index("envelope_building_sf")
        self.assertEqual(header[i + 1:i + 3], list(detention.COLUMNS))
        props = json.loads((self.out / "ranked.geojson").read_text())["features"][0]["properties"]
        self.assertEqual(props["pond_fit"], "pond fits")
        self.assertNotIn(detention.POND_KEY, props)
        self.assertNotIn(detention.DETAIL_KEY, props)
        for p in self.parcels:
            self.assertTrue(p[detention.POND_KEY].within(p["geometry"]))

    def test_runs_are_deterministic(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        first = (self.out / "ranked.csv").read_bytes()
        self.parcels[:] = fresh
        self.main()
        self.assertEqual((self.out / "ranked.csv").read_bytes(), first)

    def test_readme_and_settings_name_it(self):
        text = (SCREENER / "README.md").read_text()
        for words in ("pond_fit", "detention_pct", "no pond room found", "[detention]",
                      "Atlas 14"):
            self.assertIn(words, text)
        self.assertIsNone(re.search(r"pond[^.\n]*does not fit", text))


if __name__ == "__main__":
    unittest.main()
