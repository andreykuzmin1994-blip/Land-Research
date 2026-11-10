"""F12: the SVG site plan in each brief (reviews/34_screener_quick_wins).
Offline, on the test_integrity fakes.

The plan is report-only: report.write_all draws it after scores, flags and
the sort, and plan.render writes nothing on the parcel. The spy and order
tests below are the real guard; the static test is a backstop.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import shutil
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, LineString, Polygon

from screener import plan, rank, report, run, scoring, screens, sitefit
from screener.tests.test_integrity import A, RunHarness, county_parcel, map_checked, square

SCREENER = Path(run.__file__).resolve().parent
NS = "{http://www.w3.org/2000/svg}"
PII = ("SECRET OWNER", "99 HIDDEN LANE", "7 PRIVATE MAIL ST")
PLAN_STEMS = ("plan", "svg")


def fitted(side_m=400.0, **extra):
    """A map-checked parcel (the world() floodplain sits on its south-west
    corner) with the F6 fit attached, as report.write_all receives it."""
    p = map_checked(square(*A, side_m=side_m), acres=40.0)
    p.update(sitefit.fit_parcel(p["geometry"], p["_open_ground"], p["constrained_pct"]).fields())
    p.update({"rank": 1, "owner": PII[0], "site_address": PII[1], "owner_mailing": PII[2]})
    p.update(extra)
    return p


def parse(svg):
    root = ET.fromstring(svg)
    return root, {e.get("class"): e for e in root.iter(f"{NS}path") if e.get("class")}


def ys(path_el):
    return [float(pt.split(",")[1]) for pt in path_el.get("d").replace("M", " ").replace(
        "L", " ").replace("Z", " ").split()]


def texts(root):
    return [t.text for t in root.iter(f"{NS}text")]


class RenderTests(unittest.TestCase):

    def test_structure(self):
        svg, why = plan.render(fitted())
        self.assertEqual(why, "")
        root, paths = parse(svg)
        self.assertEqual(root.get("viewBox"), f"0 0 {plan.WIDTH} {root.get('height')}")
        for cls in ("parcel-fill", "parcel", "constrained", "largest-open", "envelope",
                    "north", "scale"):
            self.assertIn(cls, paths)
        words = " ".join(texts(root))
        for w in ("N", "200 ft", "Parcel (county map) 39.5 ac", "Largest open part 38.9 ac",
                  "Envelope: cross-dock 1,000 x 520 ft building, 520,000 SF",
                  "cross-dock 520,000 SF", "Steep ground not shown", "not a survey"):
            self.assertIn(w, words)

    def test_y_is_flipped_north_up(self):
        # The floodplain is the parcel's south-west corner: bottom-left in SVG.
        _, paths = parse(plan.render(fitted())[0])
        self.assertGreater(min(ys(paths["constrained"])), plan.MARGIN + plan.SIZE / 2)
        self.assertEqual(max(ys(paths["parcel"])), plan.MARGIN + plan.SIZE)

    def test_envelope_sits_inside_the_drawn_parcel(self):
        _, paths = parse(plan.render(fitted())[0])
        lo, hi = plan.MARGIN, plan.MARGIN + plan.SIZE
        for pt in paths["envelope"].get("d").replace("M", " ").replace("L", " ").replace(
                "Z", " ").split():
            x, y = map(float, pt.split(","))
            self.assertTrue(lo < x < hi and lo < y < hi, pt)

    def test_deterministic_and_pinned(self):
        p = fitted()
        svg = plan.render(p)[0]
        self.assertEqual(plan.render(copy.deepcopy(p))[0], svg)
        self.assertEqual(plan.render(fitted())[0], svg)
        self.assertNotIn("-0.0", svg)
        self.assertEqual(hashlib.sha256(svg.encode()).hexdigest()[:16], "40af15d63e7dcbf7")

    def test_render_writes_nothing_on_the_parcel(self):
        p = fitted()
        before = {k: (id(v), copy.deepcopy(v) if not hasattr(v, "geom_type") else v.wkb)
                  for k, v in p.items() if k != "_envelope_detail"}
        plan.render(p)
        after = {k: (id(v), copy.deepcopy(v) if not hasattr(v, "geom_type") else v.wkb)
                 for k, v in p.items() if k != "_envelope_detail"}
        self.assertEqual(before, after)

    def test_no_owner_or_address_in_the_svg(self):
        svg = plan.render(fitted())[0]
        for s in PII:
            self.assertNotIn(s, svg)
            self.assertNotIn(s.split()[1], svg)

    def test_escaping(self):
        p = fitted(parcel_id='14<F>&"1\'')
        p["_envelope_detail"] = sitefit.not_checked('geometry fault (<a href="x">&</a>)')
        svg = plan.render(p)[0]
        root, _ = parse(svg)                           # still well-formed XML
        self.assertNotIn("<a href", svg)
        self.assertIn("14&lt;F&gt;&amp;&quot;1&#x27;", svg)
        self.assertIn('Building fit: not checked (geometry fault (<a href="x">&</a>))',
                      texts(root))
        self.assertIn('parcel 14<F>&"1\'', root.find(f"{NS}title").text)

    def test_long_legend_lines_wrap_inside_the_width(self):
        p = fitted()
        p["_envelope_detail"] = sitefit.not_checked("short")
        short = ET.fromstring(plan.render(p)[0])
        p["_envelope_detail"] = sitefit.not_checked("geometry fault (" + "x " * 40 + ")")
        root = ET.fromstring(plan.render(p)[0])
        self.assertTrue(all(len(t) <= plan.WRAP for t in texts(root)))
        self.assertGreater(int(root.get("height")), int(short.get("height")))
        footer = next(t for t in root.iter(f"{NS}text") if "not a survey" in t.text)
        self.assertLess(float(footer.get("y")), int(root.get("height")))

    def test_legend_largest_open_matches_the_fit(self):
        p = fitted()
        want = f"Largest open part {p['_envelope_detail'].largest_open_acres:,.1f} ac"
        self.assertIn(want, texts(ET.fromstring(plan.render(p)[0])))

    def test_scale_bar_sits_below_the_plan_square(self):
        _, paths = parse(plan.render(fitted())[0])
        self.assertGreater(min(ys(paths["scale"])), plan.MARGIN + plan.SIZE)

    def test_control_characters_still_parse(self):
        svg = plan.render(fitted(parcel_id="14\x01F\x0b2\ud800"))[0]
        root = ET.fromstring(svg)
        self.assertIn("14?F?2?", root.find(f"{NS}title").text)

    def test_missing_geometry_draws_nothing(self):
        for g in (None, Polygon(), GeometryCollection([LineString([(0, 0), (1, 1)])])):
            with self.subTest(g=g):
                self.assertEqual(plan.render({"geometry": g, "parcel_id": "X"}),
                                 (None, "no parcel map shape"))
        tiny = map_checked(square(*A, side_m=2.0), acres=7.7)
        self.assertEqual(plan.render(tiny), (None, "parcel map shape too small to draw"))

    def test_not_checked_parcel_is_a_plain_outline(self):
        p = county_parcel("P9", A)                     # never map-checked, never fitted
        root, paths = parse(plan.render(p)[0])
        words = texts(root)
        self.assertIn("Constraints: not checked", words)
        self.assertIn("Largest open part: not checked", words)
        self.assertIn("Building fit: not checked (fit off)", words)
        self.assertNotIn("constrained", paths)
        self.assertNotIn("envelope", paths)
        self.assertIn("parcel", paths)

    def test_none_found_and_whole_parcel_constrained(self):
        p = fitted(side_m=142.0)                       # about 5 acres: nothing fits
        root, paths = parse(plan.render(p)[0])
        self.assertIn("Building fit: none found (a lower bound, not proof nothing fits)",
                      texts(root))
        self.assertNotIn("envelope", paths)
        # map_checks' fallback: _open_ground IS the geometry while constraints exist.
        p = fitted(constrained_pct=100.0)
        p["_open_ground"] = p["geometry"]
        p.pop(sitefit.ENVELOPE_KEY)
        root, paths = parse(plan.render(p)[0])
        self.assertEqual(paths["constrained"].get("d"), paths["parcel"].get("d"))
        self.assertNotIn("largest-open", paths)
        self.assertIn("Largest open part: none", texts(root))

    def test_holes_use_evenodd(self):
        p = fitted()
        p["_open_ground"] = p["geometry"].difference(p["geometry"].centroid.buffer(0.0005))
        p["constrained_pct"] = 5.0
        _, paths = parse(plan.render(p)[0])
        self.assertEqual(paths["largest-open"].get("d").count("M"), 2)   # shell + hole
        self.assertEqual(paths["largest-open"].get("fill-rule"), "evenodd")

    def test_geometry_fault_is_not_drawn(self):
        p = fitted()
        real = plan._render

        def boom(*a, **k):
            raise GEOSException("TopologyException: side location conflict")
        plan._render = boom
        try:
            svg, why = plan.render(p)
        finally:
            plan._render = real
        self.assertIsNone(svg)
        self.assertTrue(why.startswith("geometry fault"))

    def test_scale_bar_lengths(self):
        for span, want in ((1312.0, 200.0), (30.0, 5.0), (3300.0, 500.0), (700.0, 200.0),
                           (100.0, 20.0)):
            with self.subTest(span=span):
                self.assertEqual(plan._scale_ft(span), want)

    def test_standard_library_and_screener_only(self):
        tree = ast.parse((SCREENER / "plan.py").read_text())
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        self.assertEqual(mods - {"__future__", "math", "html", "re", "textwrap", "typing"},
                         {"shapely"})


class StaticGuardTests(unittest.TestCase):
    """Backstop: rank.py and scoring.py never import or name the plan."""

    def test_rank_and_scoring_never_name_it(self):
        for name in ("rank.py", "scoring.py"):
            with self.subTest(file=name):
                tree = ast.parse((SCREENER / name).read_text())
                for node in ast.walk(tree):
                    text = (node.id if isinstance(node, ast.Name) else
                            node.attr if isinstance(node, ast.Attribute) else
                            node.value if isinstance(node, ast.Constant)
                            and isinstance(node.value, str) else
                            " ".join(a.name for a in node.names)
                            if isinstance(node, (ast.Import, ast.ImportFrom)) else None)
                    if text is not None:
                        self.assertFalse(any(w in text.lower() for w in PLAN_STEMS), text)


class PlanRunTests(RunHarness):
    """run.main end to end: drawn after scoring, beside the brief, no PII."""

    def setUp(self):
        super().setUp()
        self.events: list[tuple[str, frozenset]] = []
        for obj, name in ((rank, "learned_score"), (scoring, "fixed_score"),
                          (screens, "site_rejects"), (screens, "flags")):
            real = getattr(obj, name)

            def spy(p, *a, _real=real, _name=name, **k):
                self.events.append((_name, frozenset(p)))
                return _real(p, *a, **k)
            self.patch(obj, name, spy)
        real_render = plan.render

        def render_spy(p):
            self.events.append(("render", frozenset(p)))
            return real_render(p)
        self.patch(plan, "render", render_spy)

    def test_drawn_after_every_score_and_never_seen(self):
        for args in ((), ("--no-slope",)):
            with self.subTest(args=args):
                self.events.clear()
                self.assertEqual(self.main(*args)[0], 0)
                names = [n for n, _ in self.events]
                self.assertIn("render", names)
                first = names.index("render")
                self.assertEqual(set(names[first:]), {"render"})
                self.assertTrue({"learned_score", "fixed_score", "site_rejects", "flags"}
                                <= set(names[:first]))
                for name, keys in self.events[:first]:
                    for key in keys:
                        self.assertFalse(any(w in key.lower() for w in PLAN_STEMS), key)
                # The parcel holds no plan key afterwards either.
                for p in self.parcels:
                    self.assertFalse([k for k in p if any(w in k.lower() for w in PLAN_STEMS)])

    def test_svg_beside_brief_and_no_pii(self):
        self.main()
        briefs = self.out / "briefs"
        svgs = sorted(f.name for f in briefs.iterdir() if f.suffix == ".svg")
        self.assertEqual(svgs, ["001_14F0001_0001.svg", "002_14F0002_0002.svg"])   # score tie: by id
        for name in svgs:
            text = (briefs / name).read_text()
            ET.fromstring(text)
            for s in ("TEST OWNER", "TEST RD", "TEST ST", "ATLANTA"):
                self.assertNotIn(s, text)
            brief = (briefs / name.replace(".svg", ".md")).read_text()
            self.assertIn(f"## Site plan\n![Site plan]({name})\n", brief)
            self.assertIn("A screening sketch, not a survey.", brief)
            self.assertIn("front/rear-load 166,400 SF", text)

    def test_csv_and_geojson_do_not_depend_on_the_plan(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        outputs = {n: (self.out / n).read_bytes() for n in ("ranked.csv", "ranked.geojson",
                                                            "rejected.csv", "summary.md")}
        self.patch(plan, "render", lambda p: (None, "patched off"))
        self.parcels[:] = fresh
        shutil.rmtree(self.out)
        self.clear_cache()
        self.main()
        for n, b in outputs.items():
            self.assertEqual((self.out / n).read_bytes(), b, n)
        briefs = list((self.out / "briefs").iterdir())
        self.assertFalse([f for f in briefs if f.suffix == ".svg"])
        for f in briefs:
            self.assertIn("## Site plan\n- Not drawn (patched off)", f.read_text())

    def test_stale_plan_is_removed_when_not_drawn(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        self.assertTrue(list((self.out / "briefs").glob("*.svg")))
        self.patch(plan, "render", lambda p: (None, "patched off"))
        self.parcels[:] = fresh
        self.clear_cache()
        self.main()                                    # same output folder, kept
        self.assertFalse(list((self.out / "briefs").glob("*.svg")))

    def test_svg_bytes_are_deterministic_across_runs(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        first = {f.name: f.read_bytes() for f in (self.out / "briefs").iterdir()}
        self.parcels[:] = fresh
        shutil.rmtree(self.out)
        self.main()
        self.assertEqual({f.name: f.read_bytes() for f in (self.out / "briefs").iterdir()},
                         first)

    def test_readme_names_the_plan(self):
        text = (SCREENER / "README.md").read_text()
        self.assertIn("Site plan", text)
        self.assertIn(".svg", text)


if __name__ == "__main__":
    unittest.main()
