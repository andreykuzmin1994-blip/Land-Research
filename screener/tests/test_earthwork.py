"""F8: the rough cut/fill estimate from the 3DEP elevation histogram
(reviews/34_screener_quick_wins). Offline, on the test_integrity fakes.

The estimate is a live-only fact the frozen evaluation never measures, so it
must not be on a parcel while it is screened, flagged or scored: run.main
attaches it only after the sort. The spy tests below are the real guard; the
static test is a backstop.

    python -m unittest discover -s screener/tests -t .
"""

from __future__ import annotations

import ast
import contextlib
import copy
import csv
import io
import json
import math
import unittest
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon

from screener import arcgis, checks, geo, rank, report, run, scoring, screens
from screener.tests.test_integrity import (
    A, C, EMPTY_SLOPE, GOOD_SLOPE, NINE_M2, Offline, RunHarness, county_parcel, square,
)

SCREENER = Path(run.__file__).resolve().parent
EARTH_WORDS = ("earthwork", "cut_fill")
SLOPE_FIELDS = ("relief_ft", "steep_pct", "mean_slope_pct")


def sized_answer(geom, base=GOOD_SLOPE):
    """base with band-1 counts in the same 1:2:2:1 shape (MAD 2.5 m) but as
    many pixels as geom's open ground holds, so the coverage check passes.
    Band 2 is scaled alike, so steep_pct stays 3.3."""
    n = checks.slope_area_m2(geom) / checks._nominal_pixel_m2(geom)
    k = max(1, round(n / 6))
    out = copy.deepcopy(base)
    out["histograms"][0]["counts"] = [k, 2 * k, 2 * k, k]
    out["histograms"][1]["counts"] = [c * k for c in (30, 20, 8, 2)]
    for h in out["histograms"]:
        h["size"] = len(h["counts"])
    return out


EARTH_SLOPE = sized_answer(square(*A))
AREA_A = checks.slope_area_m2(square(*A))          # about 81,200 m²
PIXEL_A = checks._nominal_pixel_m2(square(*A))     # about 69.4 m² at 33.6 N


def band1(**changes):
    h = copy.deepcopy(GOOD_SLOPE["histograms"][0])
    h.update(changes)
    return h


class EarthworkMathTests(unittest.TestCase):
    """G4: the maths, by hand, on GOOD_SLOPE's band 1 (250-262 m, 1:2:2:1)."""

    def test_good_slope_hand_value(self):
        area = 60 * PIXEL_A                           # coverage exactly 1
        cut, mad = checks._balanced_cut(GOOD_SLOPE["histograms"][0], area, PIXEL_A)
        self.assertAlmostEqual(mad, 2.5)              # centres 251.5..260.5, mean 256
        self.assertAlmostEqual(cut / area, 1.25)      # area x MAD / 2
        out = checks._hist_balanced_earthwork(GOOD_SLOPE["histograms"][0], area, PIXEL_A)
        self.assertEqual(out["mean_cut_fill_ft"], 8.2)
        self.assertEqual(out["earthwork_cy"], checks._round_sig(area * 1.25 / 0.764554857984))

    def test_cut_equals_fill_at_the_histogram_mean(self):
        hist = {"min": 100.0, "max": 130.0, "counts": [5, 0, 17, 3, 40, 1]}
        area = sum(hist["counts"]) * 50.0
        cut, mad = checks._balanced_cut(hist, area, 50.0)
        width = 5.0
        centres = [100.0 + (i + 0.5) * width for i in range(6)]
        n = sum(hist["counts"])
        mean = sum(c * k for c, k in zip(centres, hist["counts"])) / n
        above = sum(k * (c - mean) for c, k in zip(centres, hist["counts"]) if c > mean)
        below = sum(k * (mean - c) for c, k in zip(centres, hist["counts"]) if c < mean)
        self.assertAlmostEqual(above, below)
        self.assertAlmostEqual(cut, area * above / n)
        self.assertAlmostEqual(cut, area * mad / 2)

    def test_linear_in_area(self):
        hist = GOOD_SLOPE["histograms"][0]
        one = checks._balanced_cut(hist, 6000.0, 100.0)[0]
        for factor in (0.6, 1.5, 1.9):
            with self.subTest(factor=factor):
                cut = checks._balanced_cut(hist, 6000.0 * factor, 100.0)[0]
                self.assertAlmostEqual(cut, one * factor)

    def test_flat_ground_is_zero(self):
        cases = {
            "single bin": {"min": 250.0, "max": 251.0, "counts": [60]},
            "max equals min": {"min": 250.0, "max": 250.0, "counts": [10, 20, 30]},
            "one bin holds every pixel": {"min": 250.0, "max": 262.0, "counts": [0, 60, 0, 0]},
        }
        for name, hist in cases.items():
            with self.subTest(name):
                out = checks._hist_balanced_earthwork(hist, 6000.0, 100.0)
                self.assertEqual(out, {"earthwork_cy": 0, "mean_cut_fill_ft": 0.0})

    def test_units(self):
        self.assertEqual(checks.M3_PER_CY, 0.764554857984)
        self.assertAlmostEqual(checks.M3_PER_CY, 0.9144 ** 3, places=15)
        self.assertIs(checks.FT_PER_M, geo.FT_PER_M)
        # 1 m MAD over 2 x 7645.54857984 m² is exactly 10,000 cy of cut.
        hist = {"min": 0.0, "max": 4.0, "counts": [1, 0, 0, 1]}   # centres 0.5, 3.5: MAD 1.5
        area = 2 * 0.764554857984 * 10_000 / 1.5
        out = checks._hist_balanced_earthwork(hist, area, area / 2)
        self.assertEqual(out["earthwork_cy"], 10_000)
        self.assertEqual(out["mean_cut_fill_ft"], round(1.5 * geo.FT_PER_M, 1))

    def test_rounding(self):
        self.assertEqual(checks._round_sig(132_796.4), 130_000)
        self.assertEqual(checks._round_sig(1_249.0), 1_200)
        self.assertEqual(checks._round_sig(96.0), 96)
        self.assertEqual(checks._round_sig(0.4), 0)
        self.assertEqual(checks._round_sig(0), 0)

    def test_rounding_below_ten_rounds_not_truncates(self):
        # Under 10**digits the result is the nearest whole number, never the
        # fraction cut off (post-change round 1, finding 1).
        for x, want in ((0.6, 1), (0.96, 1), (1.5, 2), (1.4, 1), (4.6, 5), (9.6, 10),
                        (12.4, 12), (99.6, 100), (0.2, 0)):
            with self.subTest(x=x):
                self.assertEqual(checks._round_sig(x), want)
                self.assertIsInstance(checks._round_sig(x), int)

    def test_near_flat_sub_yard_estimate_is_not_zero(self):
        area = 6000.0
        # MAD 0.0002 m over 6,000 m2: 0.6 m3, about 0.78 cubic yard.
        hist = {"min": 250.0, "max": 250.0008, "counts": [30, 30]}
        cut_m3, _ = checks._balanced_cut(hist, area, 100.0)
        self.assertGreater(cut_m3 / checks.M3_PER_CY, 0.5)
        self.assertEqual(checks._hist_balanced_earthwork(hist, area, 100.0)["earthwork_cy"], 1)

    def test_nominal_pixel(self):
        self.assertAlmostEqual(PIXEL_A, 100 * math.cos(math.radians(33.6013)) ** 2, places=2)
        self.assertIsNone(checks._nominal_pixel_m2(Polygon()))
        self.assertIsNone(checks._nominal_pixel_m2(None))


class EarthworkNeverRaisesTests(unittest.TestCase):
    """G3: any unreadable input gives None, never an exception."""

    def test_unreadable_inputs(self):
        area, px = 6000.0, 100.0
        nan, inf = float("nan"), float("inf")
        hists = {
            "not a dict": [1, 2, 3],
            "None": None,
            "no counts": band1(counts=None),
            "empty counts": band1(counts=[]),
            "counts not a list": band1(counts="10,20"),
            "a None count": band1(counts=[10, None, 20, 10]),
            "a negative count": band1(counts=[10, -20, 20, 10]),
            "a NaN count": band1(counts=[10, nan, 20, 10]),
            "an infinite count": band1(counts=[10, inf, 20, 10]),
            "a string count": band1(counts=[10, "20", 20, 10]),
            "a bool count": band1(counts=[10, True, 20, 10]),
            "size mismatch": band1(size=5),
            "max below min": band1(min=262.0, max=250.0),
            "no min": {k: v for k, v in band1().items() if k != "min"},
            "NaN max": band1(max=nan),
            "string min": band1(min="250"),
            "no pixels": band1(counts=[0, 0, 0, 0]),
            "overflowing counts": band1(counts=[1e308] * 4),
        }
        for name, hist in hists.items():
            with self.subTest(name):
                self.assertIsNone(checks._hist_balanced_earthwork(hist, area, px))
                self.assertIsNone(checks._balanced_cut(hist, area, px))
        good = band1()
        for name, (a, p) in {"area 0": (0.0, px), "negative area": (-6000.0, px),
                             "NaN area": (nan, px), "None area": (None, px),
                             "infinite area": (inf, px), "no pixel size": (area, None),
                             "pixel size 0": (area, 0.0), "NaN pixel size": (area, nan)}.items():
            with self.subTest(name):
                self.assertIsNone(checks._hist_balanced_earthwork(good, a, p))

    def test_coverage_check(self):
        good = band1()                                  # 60 pixels
        self.assertIsNotNone(checks._hist_balanced_earthwork(good, 6000.0, 100.0))
        self.assertIsNotNone(checks._hist_balanced_earthwork(good, 12000.0, 100.0))  # 0.5x
        self.assertIsNotNone(checks._hist_balanced_earthwork(good, 3000.0, 100.0))   # 2x
        self.assertIsNone(checks._hist_balanced_earthwork(good, 12001.0, 100.0))     # too few
        self.assertIsNone(checks._hist_balanced_earthwork(good, 2999.0, 100.0))      # too many
        # GOOD_SLOPE's 60 pixels on a 20-acre square: a NoData gap, not an estimate.
        self.assertIsNone(checks._hist_balanced_earthwork(good, AREA_A, PIXEL_A))

    def test_partial_coverage_extrapolates_to_full_area(self):
        # Inside the band the volume is taken over the whole open ground: a
        # 40% NoData gap gives the full-area volume from the pixels seen
        # (post-change round 1, finding 2; documented in the README).
        good = band1()                                  # 60 pixels
        full = checks._balanced_cut(good, 6000.0, 100.0)    # 100% covered
        gap = checks._balanced_cut(good, 10000.0, 100.0)    # 60% covered
        self.assertIsNotNone(gap)
        self.assertAlmostEqual(gap[1], full[1])              # same MAD
        self.assertAlmostEqual(gap[0], full[0] * 10000.0 / 6000.0)  # scaled to area


class EarthworkSlopeCheckTests(Offline):
    """G3, G6: earthwork never changes the slope fields or the requests."""

    def run_checks(self, answer, geometry=None):
        # A fresh cache each call: subtests reuse one geometry (one cache key).
        self.calls = getattr(self, "calls", 0) + 1
        self.patch(arcgis, "CACHE_DIR", self.tmp / f"http{self.calls}")
        self.clear_cache()                      # the kept slope answers too
        self.serve({checks.SLOPE_URL: lambda params: copy.deepcopy(answer)})
        p = {"parcel_id": "P1", "geometry": geometry or square(*A)}
        estimates = checks.slope_checks([p], workers=1)
        return p, estimates

    def assert_off_parcel(self, p):
        for key in p:
            self.assertFalse(any(w in key for w in EARTH_WORDS), key)

    def test_sized_answer_gives_the_estimate_off_the_parcel(self):
        p, estimates = self.run_checks(EARTH_SLOPE)
        self.assertEqual(estimates, [{"earthwork_cy": 130_000, "mean_cut_fill_ft": 8.2}])
        self.assertEqual(checks._round_sig(AREA_A * 1.25 / checks.M3_PER_CY), 130_000)
        self.assertEqual(p["steep_pct"], 3.3)
        self.assertNotIn("slope_error", p)
        self.assert_off_parcel(p)
        self.assertEqual(self.session.count(checks.SLOPE_URL), 1)

    def test_good_slope_is_unchanged_and_gives_no_estimate(self):
        p, estimates = self.run_checks(GOOD_SLOPE)
        self.assertEqual(estimates, [None])             # 60 pixels for 20 acres
        self.assertEqual(p["steep_pct"], 3.3)
        self.assertEqual({k: p[k] for k in SLOPE_FIELDS}, checks._slope_from(GOOD_SLOPE))
        self.assertEqual(self.session.count(checks.SLOPE_URL), 1)
        self.assert_off_parcel(p)

    def test_band1_faults_leave_slope_fields_as_before(self):
        faults = {
            "size mismatch": {"size": 9},
            "max below min": {"min": 262.0, "max": 250.0},
            "negative count": {"counts": [k * 195 for k in (1, -2, 2, 1)]},
            "NaN count": {"counts": [195, float("nan"), 390, 195]},
            "count/area mismatch": {"counts": [10, 20, 20, 10], "size": 4},
        }
        for name, change in faults.items():
            with self.subTest(name):
                answer = copy.deepcopy(EARTH_SLOPE)
                answer["histograms"][0].update(change)
                try:
                    before = checks._slope_from(copy.deepcopy(answer))
                except (ValueError, TypeError):
                    before = None                       # base would fail the check too
                p, estimates = self.run_checks(answer)
                self.assertEqual(estimates, [None])
                if before is not None and before["relief_ft"] is not None:
                    self.assertNotIn("slope_error", p)
                    self.assertEqual({k: p[k] for k in SLOPE_FIELDS}, before)
                self.assert_off_parcel(p)

    def test_unreadable_histograms_still_fail_as_before(self):
        null_counts = copy.deepcopy(EARTH_SLOPE)
        null_counts["histograms"][0]["counts"] = [None, 3]
        for answer in (EMPTY_SLOPE, null_counts):
            with self.subTest(answer=answer):
                p, estimates = self.run_checks(answer)
                self.assertTrue(p.get("slope_error"))
                self.assertEqual(estimates, [None])

    def test_helper_errors_never_fail_the_slope_check(self):
        for exc in (ValueError("bad"), ZeroDivisionError(), OverflowError(), KeyError("x")):
            with self.subTest(exc=type(exc).__name__):
                def boom(*a, exc=exc):
                    raise exc
                self.patch(checks, "_hist_balanced_earthwork", boom)
                p, estimates = self.run_checks(EARTH_SLOPE)
                self.assertNotIn("slope_error", p)
                self.assertEqual(p["steep_pct"], 3.3)
                self.assertEqual(estimates, [None])

    def test_skipped_parcel_gives_none(self):
        p, estimates = self.run_checks(EMPTY_SLOPE, square(*A, side_m=NINE_M2))
        self.assertTrue(p.get("slope_skipped"))
        self.assertEqual(estimates, [None])
        self.assertEqual(self.session.count(checks.SLOPE_URL), 0)

    def test_multipolygon_with_a_hole(self):
        x, y = A
        outer = square(x, y, side_m=300.0)
        hole = square(x + 0.0005, y + 0.0005, side_m=80.0)
        ring = Polygon(outer.exterior.coords, [hole.exterior.coords])
        other = square(x + 0.01, y, side_m=150.0)
        geom = MultiPolygon([ring, other])
        area = checks.slope_area_m2(geom)
        self.assertAlmostEqual(area, ring.area / outer.area * checks.slope_area_m2(outer)
                               + checks.slope_area_m2(other), delta=area * 0.01)
        answer = sized_answer(geom)
        p, estimates = self.run_checks(answer, geom)
        counts = answer["histograms"][0]["counts"]
        cut, mad = checks._balanced_cut(answer["histograms"][0], area,
                                        checks._nominal_pixel_m2(geom))
        self.assertAlmostEqual(mad, 2.5)
        self.assertAlmostEqual(cut, area * 1.25)
        self.assertEqual(estimates[0]["earthwork_cy"], checks._round_sig(cut / checks.M3_PER_CY))
        self.assertTrue(all(math.isfinite(v) for v in estimates[0].values()))
        self.assertEqual(sum(counts), 6 * counts[0])
        self.assertEqual(len(json.loads(self.session.calls[0][1]["geometry"])["rings"]), 3)

    def test_estimates_follow_input_order_with_threads(self):
        parcels, answers = [], {}
        for i in range(8):
            g = square(-84.5 + 0.01 * i, 33.6, side_m=150.0 + 40 * i)
            parcels.append({"parcel_id": "SAME", "geometry": g})   # ids need not be unique
            answers[round(g.bounds[0], 5)] = sized_answer(g)

        def route(params):
            west = json.loads(params["geometry"])["rings"][0][0][0]
            return copy.deepcopy(min(answers.items(), key=lambda kv: abs(kv[0] - west))[1])
        self.serve({checks.SLOPE_URL: route})
        estimates = checks.slope_checks(parcels, workers=4)
        expected = [checks._hist_balanced_earthwork(
            sized_answer(p["geometry"])["histograms"][0], checks.slope_area_m2(p["geometry"]),
            checks._nominal_pixel_m2(p["geometry"])) for p in parcels]
        self.assertEqual(estimates, expected)
        self.assertEqual(len({e["earthwork_cy"] for e in estimates}), 8)


class EarthworkRunTests(RunHarness):
    """G1, G5, G6, G9: run.main end to end."""

    def setUp(self):
        super().setUp()
        self.routes[checks.SLOPE_URL] = lambda params: copy.deepcopy(EARTH_SLOPE)
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

    def earthwork_line(self, pid):
        [line] = [x for x in self.brief_text(pid).splitlines() if "Earthwork" in x]
        return line

    def assert_never_seen_by_scoring(self):
        called = {name for name, _ in self.seen}
        self.assertTrue({"learned_score", "fixed_score", "site_rejects", "flags"} <= called)
        for name, keys in self.seen:
            for key in keys:
                self.assertFalse(any(w in key for w in EARTH_WORDS), f"{key} seen by {name}")

    def test_spy_slope_on(self):
        code, _ = self.main()
        self.assertEqual(code, 0)
        self.assert_never_seen_by_scoring()
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        for r in rows.values():
            self.assertEqual(r["earthwork_cy"], "130000")
            self.assertEqual(r["mean_cut_fill_ft"], "8.2")
            self.assertEqual(r["steep_pct"], "3.3")
        self.assertEqual(self.session.count(checks.SLOPE_URL), 2)  # one per parcel

    def test_spy_no_slope(self):
        code, _ = self.main("--no-slope")
        self.assertEqual(code, 0)
        self.assert_never_seen_by_scoring()
        self.assertEqual(self.session.count(checks.SLOPE_URL), 0)
        for pid, r in self.rows().items():
            self.assertEqual(r["earthwork_cy"], "")
            self.assertEqual(r["mean_cut_fill_ft"], "")
            self.assertIn("Earthwork: not computed (slope not checked)", self.earthwork_line(pid))

    def test_slope_off_in_settings(self):
        settings = self.tmp / "settings.toml"
        text = (SCREENER / "settings.toml").read_text()
        self.assertIn("slope = true", text)
        settings.write_text(text.replace("slope = true", "slope = false", 1))
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            code = run.main(["--settings", str(settings), "--out", str(self.out)])
        self.assertEqual(code, 0)
        self.assertEqual(self.session.count(checks.SLOPE_URL), 0)
        for pid, r in self.rows().items():
            self.assertEqual(r["earthwork_cy"], "")
            self.assertIn("not computed (slope not checked)", self.earthwork_line(pid))
        self.assert_never_seen_by_scoring()

    def test_scores_and_order_do_not_depend_on_the_estimate(self):
        # run.main attaches the estimate to the parcel dicts it was given, and
        # this harness hands both runs the same dicts; a real run fetches
        # fresh ones. Give the second run fresh parcels so it is a clean
        # "no estimate" run and the spy check covers it.
        fresh = copy.deepcopy(self.parcels)
        self.main()
        with_estimate = {pid: (r["rank"], r["score"], r["flags"], r["why_site"])
                         for pid, r in self.rows().items()}
        self.patch(checks, "_hist_balanced_earthwork", lambda *a: None)
        self.parcels[:] = fresh
        self.main()
        without = {pid: (r["rank"], r["score"], r["flags"], r["why_site"])
                   for pid, r in self.rows().items()}
        self.assertEqual(with_estimate, without)
        self.assertTrue(all(r["earthwork_cy"] == "" for r in self.rows().values()))
        self.assert_never_seen_by_scoring()

    def test_columns_and_brief(self):
        self.main()
        with open(self.out / "ranked.csv", newline="") as fh:
            header = next(csv.reader(fh))
        i = header.index("relief_ft")
        self.assertEqual(header[i + 1:i + 3], ["earthwork_cy", "mean_cut_fill_ft"])
        with open(self.out / "rejected.csv", newline="") as fh:
            self.assertEqual(next(csv.reader(fh)), ["parcel_id", "jurisdiction", "site_address",
                                                    "acres", "owner", "decision", "reasons"])
        props = json.loads((self.out / "ranked.geojson").read_text())["features"][0]["properties"]
        self.assertEqual(props["earthwork_cy"], 130000)
        line = self.earthwork_line("14F0001 0001")
        self.assertIn("about 130,000 cubic yards of cut balanced by the same fill", line)
        self.assertIn("8.2 ft average cut or fill", line)
        for words in ("not a grading plan", "drainage slope", "terracing", "shrink/swell",
                      "topsoil", "rock", "mean elevation"):
            self.assertIn(words, line)
        self.assertNotIn("$", line)
        text = self.brief_text("14F0001 0001")
        why = text.split("## Why it ranks here", 1)[1]
        watch = text.split("## Watch-outs", 1)[1] if "## Watch-outs" in text else ""
        self.assertNotIn("arthwork", why.split("## Next steps")[0] + watch)

    def test_failed_slope_check_is_empty_not_zero(self):
        def slope(params):
            west = json.loads(params["geometry"])["rings"][0][0][0]
            return copy.deepcopy(EMPTY_SLOPE if west > -84.49 else EARTH_SLOPE)
        self.routes[checks.SLOPE_URL] = slope
        self.main()
        rows = self.rows()
        self.assertEqual(rows["14F0001 0001"]["earthwork_cy"], "130000")
        self.assertEqual(rows["14F0002 0002"]["earthwork_cy"], "")
        self.assertEqual(rows["14F0002 0002"]["mean_cut_fill_ft"], "")
        self.assertIn("not computed (slope check failed)", self.earthwork_line("14F0002 0002"))
        self.assert_never_seen_by_scoring()

    def test_skipped_parcel_is_empty_not_zero(self):
        tiny = county_parcel("14F0003 0003", C, owner="TEST OWNER GAMMA")
        tiny["geometry"] = square(*C, side_m=NINE_M2)
        self.parcels.append(tiny)
        self.main()
        r = self.rows()["14F0003 0003"]
        self.assertEqual((r["earthwork_cy"], r["mean_cut_fill_ft"]), ("", ""))
        self.assertIn("not computed (open ground under one lidar pixel)",
                      self.earthwork_line("14F0003 0003"))
        self.assertEqual(self.session.count(checks.SLOPE_URL), 2)

    def test_helper_none_is_empty_not_zero(self):
        self.routes[checks.SLOPE_URL] = lambda params: copy.deepcopy(GOOD_SLOPE)
        self.main()
        for pid, r in self.rows().items():
            self.assertEqual(r["steep_pct"], "3.3")
            self.assertEqual((r["earthwork_cy"], r["mean_cut_fill_ft"]), ("", ""))
            self.assertIn("not computed (the lidar answer didn't support an estimate)",
                          self.earthwork_line(pid))
        self.assert_never_seen_by_scoring()

    def test_flat_ground_reads_zero_not_empty(self):
        flat = copy.deepcopy(EARTH_SLOPE)
        flat["histograms"][0].update({"min": 250.0, "max": 250.0})
        self.routes[checks.SLOPE_URL] = lambda params: copy.deepcopy(flat)
        self.main()
        for r in self.rows().values():
            self.assertEqual((r["earthwork_cy"], r["mean_cut_fill_ft"]), ("0", "0.0"))


def named_estimate(tree: ast.AST) -> list[str]:
    """Every string constant, name or attribute in tree that mentions the
    estimate (the static guard's one rule, shared by its self-test)."""
    hits = []
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        if text is not None and any(w in text.lower() for w in EARTH_WORDS):
            hits.append(text)
    return hits


class EarthworkStaticGuardTests(unittest.TestCase):
    """G2, backstop only: no string constant or name in rank.py or scoring.py
    mentions the estimate. A string built at runtime ("earth" + "work_cy")
    gets past this; the run.main spy tests above, which check the keys on
    every parcel the scorers see, are what hold."""

    def test_rank_and_scoring_never_name_the_estimate(self):
        for name in ("rank.py", "scoring.py"):
            with self.subTest(file=name):
                self.assertEqual(named_estimate(ast.parse((SCREENER / name).read_text())), [])

    def test_the_guard_catches_a_named_read(self):
        cases = {
            'def learned_score(p):\n    return p.get("earthwork_cy", 0)\n': ["earthwork_cy"],
            'def learned_score(p):\n    return p["Mean_Cut_Fill_ft"]\n': ["Mean_Cut_Fill_ft"],
            'def learned_score(p):\n    return checks.EARTHWORK_KEYS\n': ["EARTHWORK_KEYS"],
            'def learned_score(p):\n    earthwork = 1\n    return earthwork\n':
                ["earthwork", "earthwork"],
            'def learned_score(p):\n    return p.get("earth" + "work_cy", 0)\n': [],
        }
        for src, hits in cases.items():
            with self.subTest(src=src):
                self.assertEqual(named_estimate(ast.parse(src)), hits)


if __name__ == "__main__":
    unittest.main()
