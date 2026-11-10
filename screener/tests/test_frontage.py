"""F10: road frontage from GDOT road centrelines (reviews/34_screener_quick_wins).
Offline: every GDOT answer comes from a fake route (test_integrity.fake_roads).

Frontage is a live-only report fact: run.main asks for it after the sort,
the fit, the pond and the soils, so no scorer or screen ever sees it. The
spy tests are the real guard; the static stem test is a backstop.
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
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import shapely
from shapely.geometry import LineString, MultiPolygon

from screener import frontage, layers, plan, rank, report, run, scoring, screens
from screener.tests.test_integrity import (
    A, B, C, FakeResponse, Offline, RunHarness, county_parcel, fake_roads, identify_bounds,
    square,
)

SCREENER = Path(run.__file__).resolve().parent
STEMS = frontage.KEY_STEMS
SIDE_FT = 285.0 * 3.28084          # test_integrity.square's side


def along(offset_m):
    """Expected frontage (ft) for a road offset_m south of the square, running
    past both corners: the south edge plus the ends of the two side edges
    that lie within 50 ft of the centreline (the documented corner allowance)."""
    return SIDE_FT + 2 * (frontage.BUFFER_FT - offset_m * 3.28084)


def result(layer=3, rid="1000100001400INC", paths=None, sr=4326, attrs=True):
    r = {"layerId": layer, "layerName": "x", "geometryType": "esriGeometryPolyline",
         "geometry": {"paths": paths if paths is not None else [[[-84.6, 33.5], [-84.4, 33.5]]],
                      "spatialReference": {"wkid": sr}}}
    if attrs:
        r["attributes"] = {"route_id": rid}
    return r


class ParcelCheckTests(Offline):
    """frontage.check_parcel against fake identify answers."""

    def ask(self, route, geom=None):
        shutil.rmtree(self.tmp / "http", ignore_errors=True)  # good answers are cached
        self.serve({frontage.IDENTIFY_URL: route})
        return frontage.check_parcel(geom if geom is not None else square(*A))

    def test_values_and_the_request(self):
        f = self.ask(fake_roads())
        self.assertEqual(f.state, frontage.CHECKED)
        self.assertAlmostEqual(f.feet, along(10.0), delta=2)
        self.assertEqual(f.road, "minor arterial, SR 14")
        self.assertEqual(f.limited_feet, 0)
        self.assertEqual(f.fields()["frontage_road"], "minor arterial, SR 14")
        # The drawn line is the parcel's south edge (plus the side-line ends
        # within 50 ft of the road), in WGS84.
        lx0, ly0, lx1, ly1 = f.line.bounds
        px0, py0, px1, _ = square(*A).bounds
        reach = (frontage.BUFFER_FT / 3.28084 - 10.0) / 110_574
        for got, want in ((lx0, px0), (ly0, py0), (lx1, px1), (ly1, py0 + reach)):
            self.assertAlmostEqual(got, want, places=6)
        [(url, params)] = self.session.calls
        self.assertEqual(url, frontage.IDENTIFY_URL)
        self.assertEqual(params["layers"], "all:0,1,2,3,4,5,6")
        self.assertEqual((params["sr"], params["returnGeometry"], params["tolerance"]),
                         (4326, "true", 0))
        xmin, ymin, xmax, ymax = identify_bounds(params)
        for got, want in zip((xmin, ymin, xmax, ymax), square(*A).bounds):
            self.assertAlmostEqual(got, want, places=6)
        frontage.check_parcel(square(*A))
        self.assertEqual(len(self.session.calls), 1)        # a good answer is cached

    def test_beyond_fifty_feet_is_none_with_the_nearest_road(self):
        f = self.ask(fake_roads([(6, "1121200054600INC", 30.0)]))      # about 98 ft
        self.assertEqual((f.state, f.feet, f.limited_feet), (frontage.NONE, 0, 0))
        self.assertAlmostEqual(f.nearest_ft, 98, delta=2)
        fields = f.fields()
        self.assertEqual((fields["frontage_ft"], fields["frontage_road"]),
                         (0, frontage.NONE_LABEL))
        self.assertIsNone(fields[frontage.LINE_KEY])
        self.assertIn("nearest non-freeway GDOT road about 98 ft", frontage.brief_line(fields))

    def test_no_road_at_all_is_none(self):
        f = self.ask(lambda params: {"results": []})
        self.assertEqual((f.state, f.feet), (frontage.NONE, 0))
        self.assertIn("no GDOT road within about 500 ft", frontage.brief_line(f.fields()))

    def test_limited_access_is_not_access_frontage(self):
        f = self.ask(fake_roads([(0, "1000100040200INC", 5.0)]))
        self.assertEqual((f.state, f.feet), (frontage.NONE, 0))
        self.assertAlmostEqual(f.limited_feet, along(5.0), delta=2)
        line = frontage.brief_line(f.fields())
        self.assertIn("along an interstate or freeway/expressway (access controlled: a driveway is unlikely)", line)
        self.assertIn("only interstates or freeways nearby", line)

    def test_longest_road_wins_and_ties_are_deterministic(self):
        f = self.ask(fake_roads([(6, "1121200054600INC", 5.0), (3, "1000100001400INC", 10.0)]))
        self.assertEqual(f.state, frontage.CHECKED)
        self.assertEqual(f.road, "local road, GDOT route 1121200054600")   # tie: by label
        self.assertEqual([lab for lab, _ in f.roads],
                         ["local road, GDOT route 1121200054600", "minor arterial, SR 14"])
        self.assertAlmostEqual(f.feet, along(5.0), delta=2)   # union, not the sum

    def test_divided_road_carriageways_are_one_road(self):
        f = self.ask(fake_roads([(6, "1121200054600INC", 5.0), (6, "1121200054600DEC", 12.0)]))
        self.assertEqual(f.state, frontage.CHECKED)
        self.assertEqual([lab for lab, _ in f.roads], ["local road, GDOT route 1121200054600"])

    def test_a_measuring_failure_is_not_checked(self):
        def boom(geometry, results):
            raise shapely.errors.GEOSException("TopologyException: side location conflict")
        self.patch(frontage, "measure", boom)
        f = self.ask(fake_roads())
        self.assert_not_checked(f, "could not measure the road answer")

    def assert_not_checked(self, f, words):
        self.assertEqual(f.state, frontage.NOT_CHECKED)
        self.assertIn(words, f.reason)
        fields = f.fields()
        self.assertTrue(fields["frontage_road"].startswith(frontage.NOT_CHECKED_PREFIX))
        self.assertIsNone(fields["frontage_ft"])
        self.assertIsNone(fields["frontage_limited_ft"])

    def test_unusable_answers_are_not_checked_and_never_cached(self):
        cases = {
            "no results list (header only)": ({"fields": []}, "no results list"),
            "another layer": ({"results": [result(layer=9)]}, "unexpected layer"),
            "a bool layer id": ({"results": [result(layer=True)]}, "unexpected layer"),
            "no paths": ({"results": [{**result(), "geometry": {"x": 1, "y": 2}}]},
                         "without polyline"),
            "web mercator": ({"results": [result(sr=3857)]}, "WGS84"),
            "a one-point path": ({"results": [result(paths=[[[-84.5, 33.6]]])]}, "malformed"),
            "text coordinates": ({"results": [result(paths=[[["a", 1], [2, 3]]])]},
                                 "malformed"),
            "no paths at all": ({"results": [result(paths=[])]}, "malformed"),
            "NaN coordinates": ({"results": [result(paths=[[[float("nan"), 33.5],
                                                           [-84.4, 33.5]]])]}, "malformed"),
            "infinite coordinates": ({"results": [result(paths=[[[-84.6, float("inf")],
                                                                [-84.4, 33.5]]])]},
                                     "malformed"),
        }
        for name, (answer, words) in cases.items():
            with self.subTest(name):
                f = self.ask(lambda params, a=answer: copy.deepcopy(a))
                self.assert_not_checked(f, words)
                self.assertIn("no usable road answer", f.reason)
                self.assertLessEqual(len(self.session.calls), frontage.TRIES)
                self.assertEqual(self.cached(), [])

    def test_result_cap(self):
        self.patch(frontage, "RESULT_CAP", 2)
        f = self.ask(lambda params: {"results": [result(), result()]})
        self.assert_not_checked(f, "result cap")

    def test_http_errors_are_not_checked(self):
        for status in (500, 503, 404):
            with self.subTest(status=status):
                f = self.ask(lambda params, s=status: FakeResponse(status=s))
                self.assert_not_checked(f, frontage.SERVICE_FAILED)
                self.assertEqual(len(self.session.calls), frontage.TRIES)
        f = self.ask(lambda params: {"error": {"code": 400, "message": "Failed"}})
        self.assert_not_checked(f, frontage.SERVICE_FAILED)

    def test_non_areal_shapes(self):
        two = MultiPolygon([square(*A, side_m=100.0), square(*B, side_m=100.0)])
        self.assertEqual(self.ask(fake_roads(), two).state, frontage.CHECKED)
        for g in (None, LineString([A, B]), shapely.Point(A)):
            with self.subTest(g=g):
                f = frontage.check_parcel(g)
                self.assertEqual((f.state, f.reason), (frontage.NOT_CHECKED,
                                                       "no usable parcel shape"))

    def test_road_labels(self):
        self.assertEqual(frontage.road_label(3, "1000100001400INC"), "minor arterial, SR 14")
        self.assertEqual(frontage.road_label(2, "11211000014ALINC"),
                         "principal arterial, SR 14 AL")
        self.assertEqual(frontage.road_label(0, "1000100040200INC"), "interstate, SR 402")
        self.assertEqual(frontage.road_label(6, "1121200054600INC"),
                         "local road, GDOT route 1121200054600")
        self.assertEqual(frontage.road_label(6, None), "local road, unnamed route")
        self.assertEqual(frontage.road_label(6, "=cmd|' /C calc'!A0 <b>"),
                         "local road, GDOT route CMDCCALCA0B")

    def test_stops_asking_after_failures_in_a_row(self):
        self.serve({frontage.IDENTIFY_URL: lambda params: FakeResponse(status=503)})
        shapes = [square(-84.5 + i * 0.01, 33.6) for i in range(frontage.STOP_AFTER_FAILURES + 3)]
        out = frontage.check_all(shapes, workers=1)
        self.assertEqual([f.reason.startswith(frontage.SERVICE_FAILED) for f in out],
                         [True] * frontage.STOP_AFTER_FAILURES + [False] * 3)
        self.assertTrue(all("failures in a row" in f.reason for f in out[-3:]))
        self.assertEqual(len(self.session.calls),
                         frontage.STOP_AFTER_FAILURES * frontage.TRIES)

    def test_workers_are_capped_below_the_shared_six(self):
        self.assertLessEqual(frontage.MAX_WORKERS, 3)
        seen = []
        real = frontage.ThreadPoolExecutor
        self.patch(frontage, "ThreadPoolExecutor",
                   lambda max_workers: seen.append(max_workers) or real(max_workers=max_workers))
        self.serve({frontage.IDENTIFY_URL: fake_roads()})
        frontage.check_all([square(*A)], workers=6)
        self.assertEqual(seen, [frontage.MAX_WORKERS])

    def test_settings(self):
        self.assertEqual(frontage.settings({}), (True, 0, 3))
        self.assertEqual(frontage.settings({"frontage": {"enabled": False, "max_parcels": 9,
                                                         "workers": 1}}), (False, 9, 1))
        for bad in ({"enabled": "yes"}, {"max_parcels": -1}, {"max_parcels": True},
                    {"workers": 4}, {"workers": 0}, {"workers": 2.0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                frontage.settings({"frontage": bad})

    def test_brief_lines_three_states_and_the_curb_cut(self):
        good = frontage.brief_line(self.ask(fake_roads()).fields())
        for words in ("Road frontage (GDOT road centrelines): about 969 ft",
                      "longest on minor arterial, SR 14 (969 ft)", "within 50 ft",
                      "Curb cut / driveway access not checked: a manual next step"):
            self.assertIn(words, good)
        none = frontage.brief_line(self.ask(fake_roads([(6, "1121200054600INC", 30.0)])).fields())
        self.assertIn("none within 50 ft", none)
        self.assertIn("Curb cut", none)
        self.assertEqual(frontage.brief_line(frontage.not_checked("x y").fields()),
                         f"- Road frontage: not checked (x y). {frontage.CURB_CUT}")
        self.assertEqual(frontage.brief_line({}), "- Road frontage: not checked (frontage off)")

    def test_plan_draws_the_frontage(self):
        p = {"geometry": square(*A), "rank": 1, "parcel_id": "P1",
             **self.ask(fake_roads()).fields()}
        svg, why = plan.render(p)
        self.assertEqual(why, "")
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        [path] = [e for e in root.iter(f"{ns}path") if e.get("class") == "frontage"]
        self.assertTrue(path.get("d").startswith("M"))
        self.assertNotIn("Z", path.get("d"))
        words = " ".join(t.text or "" for t in root.iter(f"{ns}text"))
        self.assertIn("Road frontage about 969 ft", words)
        p.update(frontage.not_checked("patched").fields())
        svg2, _ = plan.render(p)
        self.assertNotIn('class="frontage"', svg2)
        self.assertIn("Road frontage: not checked (patched)", svg2)


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
                text = (SCREENER / name).read_text()
                self.assertEqual(named(ast.parse(text)), [])
                self.assertNotIn("import frontage", text)
                self.assertNotIn("maps.itos", text)

    def test_every_key_carries_a_stem(self):
        for key in (*frontage.COLUMNS, frontage.DETAIL_KEY, frontage.LINE_KEY):
            self.assertTrue(any(w in key for w in STEMS), key)

    def test_not_a_map_layer_and_not_in_the_eval(self):
        # build_eval copies every layers.LAYERS entry: the roads must not be one.
        for layer in layers.LAYERS.values():
            self.assertNotIn("maps.itos", layer.url)
            self.assertNotIn("FunctionalClass", layer.url)
        for path in (SCREENER / "autoresearch").glob("*.py"):
            with self.subTest(file=path.name):
                text = path.read_text()
                self.assertNotIn("frontage", text)
                self.assertNotIn("maps.itos", text)
        self.assertNotIn("frontage", (SCREENER / "checks.py").read_text())


class FrontageRunTests(RunHarness):
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

    def front_line(self, pid):
        [line] = [x for x in self.brief_text(pid).splitlines() if x.startswith("- Road frontage")]
        return line

    def assert_never_seen(self):
        called = {name for name, _ in self.seen}
        self.assertTrue({"learned_score", "fixed_score", "site_rejects", "flags"} <= called)
        for name, keys in self.seen:
            for key in keys:
                self.assertFalse(any(w in key.lower() for w in STEMS), f"{key} seen by {name}")

    def run_with(self, body):
        settings = self.tmp / "settings.toml"
        head = (SCREENER / "settings.toml").read_text().split("[frontage]", 1)[0]
        settings.write_text(head + "[frontage]\n" + body)
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            return run.main(["--settings", str(settings), "--out", str(self.out)])

    def test_spy_slope_on_and_off(self):
        for args in ((), ("--no-slope",)):
            with self.subTest(args=args):
                self.seen.clear()
                self.parcels[:] = [county_parcel("14F0001 0001", A),
                                   county_parcel("14F0002 0002", B, owner="TEST OWNER BETA")]
                code, _ = self.main(*args)
                self.assertEqual(code, 0)
                self.assert_never_seen()
                for r in self.rows().values():
                    self.assertEqual(r["frontage_road"], "minor arterial, SR 14")
                    self.assertAlmostEqual(int(r["frontage_ft"]), along(10.0), delta=2)
                    self.assertEqual(r["frontage_limited_ft"], "0")
                for p in self.parcels:
                    self.assertIn(frontage.DETAIL_KEY, p)

    def test_scores_and_order_do_not_depend_on_the_frontage(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        keep = ("rank", "score", "flags", "why_site", "why_seller", "envelope_fit", "pond_fit",
                "soil_hsg")
        on = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        before = sorted(json.dumps(c, sort_keys=True, default=str)
                        for c in self.session.calls if c[0] != frontage.IDENTIFY_URL)
        self.patch(frontage, "check_parcel", lambda g: frontage.not_checked("patched off"))
        self.parcels[:] = fresh
        shutil.rmtree(self.tmp / "http", ignore_errors=True)
        self.main()
        off = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        self.assertEqual(on, off)
        after = sorted(json.dumps(c, sort_keys=True, default=str) for c in self.session.calls)
        self.assertEqual(after, before)       # no GDOT request; the others are unchanged
        self.assertTrue(all(r["frontage_road"] == "not checked: patched off"
                            for r in self.rows().values()))
        self.assert_never_seen()

    def test_three_states_in_csv_and_brief(self):
        self.parcels.append(county_parcel("14F0003 0003", C, owner="TEST OWNER GAMMA"))
        good, far = fake_roads(), fake_roads([(6, "1121200054600INC", 30.0)])

        def route(params):
            west = identify_bounds(params)[0]
            if abs(west - A[0]) < 1e-6:
                return good(params)
            if abs(west - B[0]) < 1e-6:
                return far(params)
            return FakeResponse(status=503)
        self.routes[frontage.IDENTIFY_URL] = route
        self.main()
        rows = self.rows()
        a, b, c = rows["14F0001 0001"], rows["14F0002 0002"], rows["14F0003 0003"]
        self.assertEqual(a["frontage_road"], "minor arterial, SR 14")
        self.assertEqual([b[k] for k in frontage.COLUMNS], ["0", frontage.NONE_LABEL, "0"])
        self.assertTrue(c["frontage_road"].startswith("not checked: road service failed"))
        self.assertEqual((c["frontage_ft"], c["frontage_limited_ft"]), ("", ""))
        self.assertIn("about 969 ft", self.front_line("14F0001 0001"))
        self.assertIn("none within 50 ft", self.front_line("14F0002 0002"))
        self.assertIn("Road frontage: not checked (road service failed",
                      self.front_line("14F0003 0003"))
        for pid in rows:
            text = self.brief_text(pid)
            self.assertIn("- Confirm a curb cut / driveway permit", text.split("## Next steps")[1])
        notes = self.notes()
        self.assertIn("frontage on 1 of 3 ranked parcels, none within 50 ft on 1", notes)
        self.assertIn("Road frontage not checked for 1 of 3 ranked parcels: road service failed",
                      notes)
        self.assertIn("ROAD SERVICE FAILURES", notes)

        self.assertEqual(self.run_with("enabled = false\n"), 0)
        for pid, r in self.rows().items():
            self.assertEqual(r["frontage_road"], "not checked: frontage switched off in settings")
            self.assertIn("Road frontage: not checked (frontage switched off",
                          self.front_line(pid))
        self.assertIn("Road frontage not checked for 3 of 3 ranked parcels", self.notes())
        self.assertNotIn("ROAD SERVICE FAILURES", self.notes())

    def test_max_parcels_cap_is_by_rank(self):
        self.assertEqual(self.run_with("max_parcels = 1\n"), 0)
        rows = sorted(self.rows().values(), key=lambda r: int(r["rank"]))
        self.assertEqual(rows[0]["frontage_road"], "minor arterial, SR 14")
        self.assertEqual(rows[1]["frontage_road"],
                         "not checked: outside the top 1 ([frontage] max_parcels)")
        self.assertEqual(self.session.count(frontage.IDENTIFY_URL), 1)

    def test_old_settings_without_the_table_and_bad_values(self):
        settings = self.tmp / "settings.toml"
        settings.write_text((SCREENER / "settings.toml").read_text().split("[frontage]", 1)[0])
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["--settings", str(settings), "--out", str(self.out)]), 0)
        self.assertEqual(self.rows()["14F0001 0001"]["frontage_road"], "minor arterial, SR 14")
        for body in ("max_parcels = -1\n", "enabled = 1\n", "workers = 6\n", "workers = 0\n"):
            with self.subTest(body=body):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    self.run_with(body)
                self.assertEqual(e.exception.code, 2)

    def test_columns_private_keys_and_plan(self):
        self.main()
        with open(self.out / "ranked.csv", newline="") as fh:
            header = next(csv.reader(fh))
        i = header.index("soil_dwellings_limited_pct")
        self.assertEqual(header[i + 1:i + 4], list(frontage.COLUMNS))
        props = json.loads((self.out / "ranked.geojson").read_text())["features"][0]["properties"]
        self.assertEqual(props["frontage_road"], "minor arterial, SR 14")
        self.assertNotIn(frontage.DETAIL_KEY, props)
        self.assertNotIn(frontage.LINE_KEY, props)
        for svg in (self.out / "briefs").glob("*.svg"):
            self.assertIn('class="frontage"', svg.read_text())

    def test_runs_are_deterministic(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        first = {n: (self.out / n).read_bytes() for n in ("ranked.csv", "ranked.geojson")}
        svgs = {f.name: f.read_bytes() for f in (self.out / "briefs").iterdir()}
        self.parcels[:] = fresh
        shutil.rmtree(self.out)
        self.main()
        for n, b in first.items():
            self.assertEqual((self.out / n).read_bytes(), b, n)
        self.assertEqual({f.name: f.read_bytes() for f in (self.out / "briefs").iterdir()}, svgs)

    def test_readme_and_settings_name_it(self):
        text = (SCREENER / "README.md").read_text()
        for words in ("frontage_ft", "frontage_road", "frontage_limited_ft", "[frontage]",
                      "none within 50 ft", "curb cut", "robots.txt", "maps.itos.uga.edu"):
            self.assertIn(words, text)
        settings = (SCREENER / "settings.toml").read_text()
        self.assertIsNotNone(re.search(r"\[frontage\][^\[]*workers = [123]\b", settings))
        self.assertIsNotNone(re.search(r"\[frontage\][^\[]*enabled = true", settings))


if __name__ == "__main__":
    unittest.main()
