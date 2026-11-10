"""F9: SSURGO soils from USDA Soil Data Access (reviews/34_screener_quick_wins).
Offline: every SDA answer comes from a fake route (test_integrity.fake_sda).

Soils are a live-only report fact: run.main asks for them after the sort,
the fit and the pond, so no scorer or screen ever sees them. The spy tests
are the real guard; the static stem test is a backstop.
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
from pathlib import Path

import shapely
from shapely.geometry import LineString, MultiPolygon

from screener import rank, report, run, scoring, screens, soils
from screener.tests.test_integrity import (
    A, B, C, GOOD_SOIL_UNITS, FakeResponse, Offline, RunHarness, county_parcel, fake_sda,
    sda_wkt, square,
)

SCREENER = Path(run.__file__).resolve().parent
STEMS = soils.KEY_STEMS
NO_RATINGS = [("999", 1.0, None, None, None, None)]


class ParcelCheckTests(Offline):
    """soils.check_parcel against fake SDA answers."""

    def ask(self, route, geom=None):
        shutil.rmtree(self.tmp / "http", ignore_errors=True)  # good answers are cached
        self.serve({soils.SDA_URL: route})
        return soils.check_parcel(geom if geom is not None else square(*A))

    def test_values_and_the_request(self):
        s = self.ask(fake_sda())
        self.assertEqual(s.state, soils.CHECKED)
        self.assertEqual(s.hsg, "B")
        self.assertEqual(s.hsg_shares, {"B": 75.0, "C/D": 25.0})
        self.assertEqual(s.hydric_pct, 10.0)              # 25% of the area x 40% hydric
        self.assertEqual(s.poorly_drained_pct, 25.0)
        self.assertEqual(s.dwellings_limited_pct, 75.0)
        self.assertEqual(s.units, 2)
        self.assertAlmostEqual(s.coverage_pct, 100.0, delta=0.2)
        [(url, params)] = self.session.calls
        self.assertEqual(url, soils.SDA_URL)
        self.assertEqual(params["format"], "JSON+COLUMNNAME")
        # WKT is lon/lat: the first coordinate is the longitude.
        self.assertTrue(sda_wkt(params).startswith("POLYGON ((-84."), sda_wkt(params))
        self.assertIn("GetClippedMapunits", params["query"])
        self.assertIn("muaggatt", params["query"])
        # A good answer is cached: the second ask makes no request.
        soils.check_parcel(square(*A))
        self.assertEqual(len(self.session.calls), 1)

    def test_multipolygon_and_non_areal_shapes(self):
        two = MultiPolygon([square(*A, side_m=100.0), square(*B, side_m=100.0)])
        self.assertTrue(soils.parcel_wkt(two).startswith("MULTIPOLYGON ((("))
        self.assertEqual(self.ask(fake_sda(), two).state, soils.CHECKED)
        for g in (None, LineString([A, B]), shapely.Point(A)):
            with self.subTest(g=g):
                s = soils.check_parcel(g)
                self.assertEqual((s.state, s.reason), (soils.NOT_CHECKED,
                                                       "no usable parcel shape"))

    def test_wkt_guard_admits_only_numbers(self):
        self.assertIsNone(soils._WKT_OK.match("POLYGON ((1 2, 3 4')); drop table x --))"))
        self.assertIsNotNone(soils._WKT_OK.match(soils.parcel_wkt(square(*A))))

    def assert_not_checked(self, s, words):
        self.assertEqual(s.state, soils.NOT_CHECKED)
        self.assertIn(words, s.reason)
        f = s.fields()
        self.assertTrue(f["soil_hsg"].startswith(soils.NOT_CHECKED_PREFIX))
        for col in soils.COLUMNS[1:]:
            self.assertIsNone(f[col])

    def test_unusable_answers_are_not_checked_and_never_cached(self):
        header = [list(soils.HEADER)]
        row = ["1", "100", "B", "Well drained", "0", "Not limited"]
        cases = {
            "empty {} (SDA's no-match answer)": ({}, "no map units"),
            "header only": ({"Table": header}, "header-only"),
            "other columns": ({"Table": [["mukey", "x"], ["1", "2"]]}, "unexpected columns"),
            "short row": ({"Table": header + [row[:4]]}, "malformed row"),
            "bad area": ({"Table": header + [["1", "NaN"] + row[2:]]}, "usable area"),
            "bad hydric": ({"Table": header + [row[:4] + ["150", row[5]]]}, "outside 0-100"),
            "non-text rating": ({"Table": header + [row[:2] + [3] + row[3:]]}, "malformed rating"),
        }
        for name, (answer, words) in cases.items():
            with self.subTest(name):
                self.assert_not_checked(self.ask(lambda params, a=answer: copy.deepcopy(a)),
                                        words)
                self.assertEqual(self.cached(), [])
                self.assertLessEqual(len(self.session.calls), soils.TRIES)

    def test_row_cap(self):
        self.patch(soils, "ROW_CAP", 2)
        units = [(str(i), 1 / 3, "B", "Well drained", "0", "Not limited") for i in range(3)]
        self.assert_not_checked(self.ask(fake_sda(units)), "row cap")

    def test_http_errors_are_not_checked(self):
        for status in (400, 500, 503):
            with self.subTest(status=status):
                s = self.ask(lambda params, st=status: FakeResponse(status=st))
                self.assert_not_checked(s, soils.SERVICE_FAILED)
                self.assertEqual(len(self.session.calls), soils.TRIES)
                self.assertEqual(self.cached(), [])

    def test_coverage_outside_the_band(self):
        for share, words in ((0.3, "covers 30%"), (1.8, "covers 180%")):
            with self.subTest(share=share):
                units = [("1", share, "B", "Well drained", "0", "Not limited")]
                self.assert_not_checked(self.ask(fake_sda(units)), words)

    def test_none_rated_is_its_own_state(self):
        s = self.ask(fake_sda(NO_RATINGS))
        self.assertEqual(s.state, soils.CHECKED)
        f = s.fields()
        for col in soils.COLUMNS:
            self.assertEqual(f[col], soils.NONE_RATED)
        self.assertEqual(s.unrated_pct, {"hsg": 100.0, "hydric": 100.0, "drainage": 100.0,
                                         "dwellings": 100.0})
        # "Not rated" (urban land) is unrated, not "not limited".
        s = self.ask(fake_sda([("1", 1.0, "D", "Well drained", "0", "Not rated")]))
        self.assertEqual(s.dwellings_limited_pct, soils.NONE_RATED)
        self.assertEqual(s.hydric_pct, 0.0)               # a rated zero is a value

    def test_dominant_group_tie_is_deterministic(self):
        units = [("1", 0.5, "D", None, None, None), ("2", 0.5, "B", None, None, None)]
        self.assertEqual(self.ask(fake_sda(units)).hsg, "B")

    def test_stops_asking_after_failures_in_a_row(self):
        self.serve({soils.SDA_URL: lambda params: FakeResponse(status=503)})
        shapes = [square(-84.5 + i * 0.01, 33.6) for i in range(8)]
        out = soils.check_all(shapes, workers=1)
        self.assertEqual([s.reason.startswith(soils.SERVICE_FAILED) for s in out],
                         [True] * soils.STOP_AFTER_FAILURES + [False] * 3)
        self.assertIn("failures in a row", out[-1].reason)
        self.assertEqual(len(self.session.calls), soils.STOP_AFTER_FAILURES * soils.TRIES)

    def test_a_success_resets_the_streak_and_order_is_kept(self):
        good = fake_sda()

        def route(params):
            west = shapely.from_wkt(sda_wkt(params)).bounds[0]
            return good(params) if round((west + 84.5) / 0.01) % 2 else FakeResponse(status=500)
        self.serve({soils.SDA_URL: route})
        shapes = [square(-84.5 + i * 0.01, 33.6) for i in range(12)]
        out = soils.check_all(shapes, workers=3)
        self.assertEqual([s.state for s in out],
                         [soils.NOT_CHECKED, soils.CHECKED] * 6)

    def test_workers_are_capped_below_the_shared_six(self):
        self.assertLessEqual(soils.MAX_WORKERS, 3)
        seen = []
        real = soils.ThreadPoolExecutor
        self.patch(soils, "ThreadPoolExecutor",
                   lambda max_workers: seen.append(max_workers) or real(max_workers=max_workers))
        self.serve({soils.SDA_URL: fake_sda()})
        soils.check_all([square(*A)], workers=6)
        self.assertEqual(seen, [soils.MAX_WORKERS])

    def test_settings(self):
        self.assertEqual(soils.settings({}), (True, 0, 3))
        self.assertEqual(soils.settings({"soils": {"enabled": False, "max_parcels": 9,
                                                   "workers": 1}}), (False, 9, 1))
        for bad in ({"enabled": 1}, {"max_parcels": -1}, {"max_parcels": 2.5},
                    {"max_parcels": True}, {"workers": 4}, {"workers": 0}, {"workers": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                soils.settings({"soils": bad})

    def test_brief_lines_three_states(self):
        good = {soils.DETAIL_KEY: self.ask(fake_sda())}
        line = soils.brief_line(good)
        for words in ("USDA SSURGO, 2 map units", "mostly B (75% of the mapped soil; also C/D "
                      "25%)", "hydric components 10%", "poorly or very poorly drained 25%",
                      "dwellings without basements (a slab-on-grade proxy) 75%",
                      "geotechnical report", "wetland delineation"):
            self.assertIn(words, line)
        none = soils.brief_line({soils.DETAIL_KEY: self.ask(fake_sda(NO_RATINGS))})
        self.assertIn("no hydrologic soil group rated", none)
        self.assertIn("hydric components none rated", none)
        self.assertIn("unrated share of the mapped soil", none)
        # A minority group under mostly unrated urban land is not "mostly" anything.
        urban = [("1", 0.3, "B", "Well drained", "0", "Not limited"),
                 ("2", 0.7, None, None, "0", "Not rated")]
        minority = soils.brief_line({soils.DETAIL_KEY: self.ask(fake_sda(urban))})
        self.assertIn("largest rated group B (30% of the mapped soil)", minority)
        self.assertNotIn("mostly", minority)
        self.assertIn("unrated share of the mapped soil: soil group 70%, drainage 70%, dwellings rating 70%", minority)
        self.assertEqual(soils.brief_line({soils.DETAIL_KEY: soils.not_checked("x y")}),
                         "- Soils: not checked (x y)")
        self.assertEqual(soils.brief_line({}), "- Soils: not checked (soils off)")


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
        for key in (*soils.COLUMNS, soils.DETAIL_KEY):
            self.assertTrue(any(w in key for w in STEMS), key)

    def test_eval_files_do_not_import_it(self):
        for path in (SCREENER / "autoresearch").glob("*.py"):
            with self.subTest(file=path.name):
                self.assertNotIn("soils", path.read_text())


class SoilsRunTests(RunHarness):
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

    def soil_line(self, pid):
        name = report._safe_name(pid)
        [path] = [f for f in (self.out / "briefs").iterdir() if f.name.endswith(name + ".md")]
        [line] = [x for x in path.read_text().splitlines() if x.startswith("- Soils")]
        return line

    def assert_never_seen(self):
        called = {name for name, _ in self.seen}
        self.assertTrue({"learned_score", "fixed_score", "site_rejects", "flags"} <= called)
        for name, keys in self.seen:
            for key in keys:
                self.assertFalse(any(w in key.lower() for w in STEMS), f"{key} seen by {name}")

    def run_with(self, body):
        settings = self.tmp / "settings.toml"
        head = (SCREENER / "settings.toml").read_text().split("[soils]", 1)[0]
        settings.write_text(head + "[soils]\n" + body)
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
                    self.assertEqual(r["soil_hsg"], "B")
                    self.assertEqual(r["soil_hydric_pct"], "10.0")
                for p in self.parcels:
                    self.assertIn(soils.DETAIL_KEY, p)

    def test_scores_and_order_do_not_depend_on_the_soils(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        keep = ("rank", "score", "flags", "why_site", "why_seller", "envelope_fit", "pond_fit")
        on = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        before = sorted(json.dumps(c, sort_keys=True, default=str)
                        for c in self.session.calls if c[0] != soils.SDA_URL)
        self.patch(soils, "check_parcel", lambda g: soils.not_checked("patched off"))
        self.parcels[:] = fresh
        shutil.rmtree(self.tmp / "http", ignore_errors=True)
        self.main()
        off = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        self.assertEqual(on, off)
        after = sorted(json.dumps(c, sort_keys=True, default=str) for c in self.session.calls)
        self.assertEqual(after, before)       # no SDA request; the others are unchanged
        self.assertTrue(all(r["soil_hsg"] == "not checked: patched off"
                            for r in self.rows().values()))
        self.assert_never_seen()

    def test_three_states_in_csv_and_brief(self):
        self.parcels.append(county_parcel("14F0003 0003", C, owner="TEST OWNER GAMMA"))
        good, none = fake_sda(), fake_sda(NO_RATINGS)

        def route(params):
            west = shapely.from_wkt(sda_wkt(params)).bounds[0]
            if abs(west - A[0]) < 1e-6:
                return good(params)
            if abs(west - B[0]) < 1e-6:
                return none(params)
            return {}                                       # SDA's no-match answer
        self.routes[soils.SDA_URL] = route
        self.main()
        rows = self.rows()
        a, b, c = rows["14F0001 0001"], rows["14F0002 0002"], rows["14F0003 0003"]
        self.assertEqual([a[k] for k in soils.COLUMNS], ["B", "10.0", "25.0", "75.0"])
        self.assertEqual([b[k] for k in soils.COLUMNS], [soils.NONE_RATED] * 4)
        self.assertTrue(c["soil_hsg"].startswith("not checked: no usable soil answer"))
        self.assertEqual([c[k] for k in soils.COLUMNS[1:]], ["", "", ""])
        self.assertIn("mostly B", self.soil_line("14F0001 0001"))
        self.assertIn("none rated", self.soil_line("14F0002 0002"))
        self.assertIn("Soils: not checked (no usable soil answer", self.soil_line("14F0003 0003"))
        notes = self.notes()
        self.assertIn("checked for 2 of 3 ranked parcels", notes)
        self.assertIn("Soils not checked for 1 of 3 ranked parcels: no usable soil answer",
                      notes)
        self.assertNotIn("SOIL SERVICE FAILURES", notes)

        self.assertEqual(self.run_with("enabled = false\n"), 0)
        for pid, r in self.rows().items():
            self.assertEqual(r["soil_hsg"], "not checked: soils switched off in settings")
            self.assertIn("Soils: not checked (soils switched off", self.soil_line(pid))
        self.assertIn("Soils not checked for 3 of 3 ranked parcels", self.notes())

    def test_service_down_is_noted(self):
        self.routes[soils.SDA_URL] = lambda params: FakeResponse(status=503)
        code, _ = self.main()
        self.assertEqual(code, 0)
        for r in self.rows().values():
            self.assertTrue(r["soil_hsg"].startswith("not checked: soil service failed"))
            self.assertEqual(r["soil_hydric_pct"], "")
        self.assertIn("SOIL SERVICE FAILURES", self.notes())

    def test_max_parcels_cap_is_by_rank(self):
        self.assertEqual(self.run_with("max_parcels = 1\n"), 0)
        rows = sorted(self.rows().values(), key=lambda r: int(r["rank"]))
        self.assertEqual(rows[0]["soil_hsg"], "B")
        self.assertEqual(rows[1]["soil_hsg"],
                         "not checked: outside the top 1 ([soils] max_parcels)")
        self.assertEqual(self.session.count(soils.SDA_URL), 1)

    def test_old_settings_without_the_table_and_bad_values(self):
        settings = self.tmp / "settings.toml"
        settings.write_text((SCREENER / "settings.toml").read_text().split("[soils]", 1)[0])
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["--settings", str(settings), "--out", str(self.out)]), 0)
        self.assertEqual(self.rows()["14F0001 0001"]["soil_hsg"], "B")
        for body in ("max_parcels = -1\n", "enabled = 1\n", "workers = 6\n", "workers = 0\n"):
            with self.subTest(body=body):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    self.run_with(body)
                self.assertEqual(e.exception.code, 2)

    def test_columns_and_private_keys(self):
        self.main()
        with open(self.out / "ranked.csv", newline="") as fh:
            header = next(csv.reader(fh))
        i = header.index("pond_fit")
        self.assertEqual(header[i + 1:i + 5], list(soils.COLUMNS))
        props = json.loads((self.out / "ranked.geojson").read_text())["features"][0]["properties"]
        self.assertEqual(props["soil_hsg"], "B")
        self.assertNotIn(soils.DETAIL_KEY, props)

    def test_runs_are_deterministic(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        first = (self.out / "ranked.csv").read_bytes()
        self.parcels[:] = fresh
        self.main()
        self.assertEqual((self.out / "ranked.csv").read_bytes(), first)

    def test_readme_and_settings_name_it(self):
        text = (SCREENER / "README.md").read_text()
        for words in ("soil_hsg", "soil_hydric_pct", "soil_poorly_drained_pct",
                      "soil_dwellings_limited_pct", "[soils]", "Soil Data Access",
                      "none rated", "robots.txt"):
            self.assertIn(words, text)
        settings = (SCREENER / "settings.toml").read_text()
        self.assertIsNotNone(re.search(r"\[soils\][^\[]*workers = [123]\b", settings))


if __name__ == "__main__":
    unittest.main()
