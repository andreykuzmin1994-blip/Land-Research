"""F2: same-owner groups and adjacent same-owner parcels, phase 1
(reviews/34_screener_quick_wins). Offline, on the test_integrity fakes.

The groups are a live-only fact the frozen evaluation never measures, so no
parcel may carry them while it is screened, flagged or scored: run.main keeps
them in a local variable and only adds flag lines after the sort. The spy
tests below are the real guard; the static test is a backstop.

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
import shutil
import time
import unittest
import unittest.mock
from pathlib import Path

from shapely.errors import GEOSException
from shapely.geometry import box

from screener import geo, owner_clusters as oc, owners, rank, run, scoring, screens
from screener.tests.test_integrity import A, SETTINGS_FILE, RunHarness, county_parcel

SCREENER = Path(run.__file__).resolve().parent
LON0, LAT0 = -84.5, 33.6
KX = 111_320.0 * math.cos(math.radians(LAT0))
KY = 110_574.0
FT = 1 / geo.FT_PER_M  # metres in a foot
SIDE_M = 285.0  # about 20 acres
BANNED_WORDS = ("owns", "portfolio of the owner", "contiguous")


def rect(x_m, y_m, w_m=SIDE_M, h_m=SIDE_M):
    """A rectangle placed in metres east / north of (LON0, LAT0)."""
    return box(LON0 + x_m / KX, LAT0 + y_m / KY, LON0 + (x_m + w_m) / KX, LAT0 + (y_m + h_m) / KY)


def parcel(pid, geom=None, owner="DELTA LAND LLC", mailing="9 OAK AVE, ATLANTA GA 30310",
           acres=20.0):
    return {"parcel_id": pid, "owner": owner, "owner_mailing": mailing, "acres": acres,
            "geometry": geom if geom is not None else rect(0, 0)}


def far(i):
    """Squares 2 km apart: same owner, never adjacent."""
    return rect(i * 2000.0, 0)


class KeyTests(unittest.TestCase):
    """J3: the two exact keys and their normalisation, case by case."""

    def test_name_spacing_punctuation_and_legal_forms(self):
        self.assertEqual(oc.name_key("  delta   land, llc "), "DELTA LAND LLC")
        self.assertEqual(oc.name_key("DELTA LAND L.L.C."), "DELTA LAND LLC")
        self.assertEqual(oc.name_key("DELTA LAND L L C"), "DELTA LAND LLC")
        self.assertEqual(oc.name_key("DELTA LAND L P"), "DELTA LAND LP")
        self.assertEqual(oc.name_key("DELTA LAND L.L.L.P."), "DELTA LAND LLLP")
        self.assertEqual(oc.name_key("DELTA LAND L L P"), "DELTA LAND LLP")
        # Legal forms, roles and numbers are kept.
        self.assertEqual(oc.name_key("DELTA FUND II LLC"), "DELTA FUND II LLC")
        self.assertEqual(oc.name_key("SMITH JOHN TRUSTEE"), "SMITH JOHN TRUSTEE")

    def test_blank_and_placeholder_names_have_no_key(self):
        for name in (None, "", "   ", "unknown", "Owner Unknown", "NOT AVAILABLE",
                     "current owner", "N/A", "."):
            with self.subTest(name=name):
                self.assertIsNone(oc.name_key(name))

    def test_address_normalisation(self):
        self.assertEqual(oc.address_key("9 Oak Ave., Atlanta GA 30310-1234"),
                         oc.address_key("9 OAK  AVE, ATLANTA, GA 30310"))
        self.assertEqual(oc.address_key("9 OAK AVE, ATLANTA GA 303101234"),
                         oc.address_key("9 OAK AVE, ATLANTA GA 30310"))
        self.assertNotEqual(oc.address_key("9 OAK AVE STE 100, ATLANTA GA 30310"),
                            oc.address_key("9 OAK AVE STE 200, ATLANTA GA 30310"))
        self.assertNotEqual(oc.address_key("9 OAK AVE, ATLANTA GA 30310"),
                            oc.address_key("9 OAK AVE, ATLANTA GA 30311"))

    def test_space_separated_zip4_is_cut_not_dropped(self):
        five = oc.address_key("9 OAK AVE, ATLANTA GA 30303")
        self.assertIsNotNone(five)
        for mailing in ("9 OAK AVE, ATLANTA GA 30303 1234", "9 OAK AVE, ATLANTA GA 30303  1234",
                        "9 OAK AVE, ATLANTA GA 30303-1234", "9 OAK AVE, ATLANTA GA 303031234"):
            with self.subTest(mailing=mailing):
                self.assertEqual(oc.address_key(mailing), five)
        # A trailing four-digit number with no ZIP5 before it is still no ZIP.
        self.assertIsNone(oc.address_key("9 OAK AVE, ATLANTA GA 1234"))
        # Two different ZIP5s stay apart whatever the +4.
        self.assertNotEqual(oc.address_key("9 OAK AVE, ATLANTA GA 30303 1234"),
                            oc.address_key("9 OAK AVE, ATLANTA GA 30304 1234"))

    def test_documented_limits_of_normalisation(self):
        """The README's 'Exact match only' bullet: only commas, periods,
        case and runs of spaces are normalised (plus spaced legal forms).
        Hyphens, apostrophes, '&' and '#' are kept with their spacing, so
        these pairs do NOT join (accepted under-merge, never a false join)."""
        self.assertEqual(oc.name_key("Smith,  Jones L.L.C."), oc.name_key("SMITH JONES LLC"))
        for a, b in (("SMITH-JONES LLC", "SMITH JONES LLC"), ("O'NEAL FARMS", "ONEAL FARMS"),
                     ("A&B LAND LLC", "A & B LAND LLC")):
            with self.subTest(a=a, b=b):
                self.assertNotEqual(oc.name_key(a), oc.name_key(b))
        self.assertNotEqual(oc.address_key("9 OAK AVE #200, ATLANTA GA 30303"),
                            oc.address_key("9 OAK AVE # 200, ATLANTA GA 30303"))

    def test_no_street_line_or_no_zip_has_no_key(self):
        for mailing in (None, "", "ATLANTA GA 30310", "9 OAK AVE", "9 OAK AVE, ATLANTA GA",
                        ", ATLANTA GA 30310"):
            with self.subTest(mailing=mailing):
                self.assertIsNone(oc.address_key(mailing))


class MatchingTests(unittest.TestCase):
    """J3: exact equality on both keys; nothing fuzzy, nothing name-only."""

    def groups(self, *parcels):
        return oc.build(parcels).groups

    def test_both_keys_equal_groups(self):
        self.assertEqual(self.groups(parcel("P1", far(0)), parcel("P2", far(1), owner="Delta Land, L.L.C.",
                                                                    mailing="9 Oak Ave, Atlanta GA 30310-0001")),
                         [("P1", "P2")])

    def test_name_only_and_address_only_matches_give_no_group(self):
        self.assertEqual(self.groups(parcel("P1", far(0)),
                                     parcel("P2", far(1), mailing="7 ELM ST, ATLANTA GA 30310")), [])
        self.assertEqual(self.groups(parcel("P1", far(0)),
                                     parcel("P2", far(1), owner="OMEGA LAND LLC")), [])

    def test_numbers_and_legal_forms_keep_owners_apart(self):
        self.assertEqual(self.groups(parcel("P1", far(0), owner="DELTA FUND I LLC"),
                                     parcel("P2", far(1), owner="DELTA FUND II LLC")), [])
        self.assertEqual(self.groups(parcel("P1", far(0), owner="DELTA LAND LLC"),
                                     parcel("P2", far(1), owner="DELTA LAND LP")), [])

    def test_different_suites_stay_apart(self):
        self.assertEqual(self.groups(
            parcel("P1", far(0), mailing="9 OAK AVE SUITE 100, ATLANTA GA 30310"),
            parcel("P2", far(1), mailing="9 OAK AVE SUITE 200, ATLANTA GA 30310")), [])

    def test_keyless_parcels_stay_alone(self):
        res = oc.build([parcel("P1", far(0), owner="UNKNOWN"), parcel("P2", far(1), owner="UNKNOWN"),
                        parcel("P3", far(2), mailing="ATLANTA GA 30310"),
                        parcel("P4", far(3), mailing="ATLANTA GA 30310"),
                        parcel("P5", far(4), owner=""), parcel("P6", far(5), owner="")])
        self.assertEqual(res.groups, [])
        self.assertEqual(res.keyless, 6)
        self.assertIn("not built", " ".join(oc.summary_notes(res)))

    def test_same_owner_true_pairs_are_not_grouped(self):
        for a, b in (("SMITH JOHN", "SMITH JOHN W"), ("SABEN LLC", "SABEN INC"),
                     ("HARVISCHAK DENNIS", "DENNIS HARVISCHAK REVOCABLE TRUST")):
            with self.subTest(a=a, b=b):
                self.assertTrue(owners.same_owner(a, b))
                self.assertEqual(self.groups(parcel("P1", far(0), owner=a),
                                             parcel("P2", far(1), owner=b)), [])

    def test_module_never_uses_the_name_only_rules(self):
        tree = ast.parse((SCREENER / "owner_clusters.py").read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        self.assertFalse(names & {"same_owner", "_same_name", "_words_agree", "_near_spelling",
                                  "SequenceMatcher"})

    def test_imports_are_stdlib_shapely_and_two_screener_modules(self):
        tree = ast.parse((SCREENER / "owner_clusters.py").read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add(("." * n.level) + (n.module or ""))
        self.assertEqual(mods - {"__future__", "math", "re", "dataclasses", "typing"},
                         {"shapely", "shapely.errors", ".geo", ".owners"})

    def test_build_writes_nothing_to_the_parcels(self):
        ps = [parcel("P1", rect(0, 0)), parcel("P2", rect(SIDE_M, 0)), parcel("P3", far(3))]
        before = [dict(p) for p in ps]
        oc.build(ps)
        self.assertEqual(ps, before)

    def test_duplicate_ids_are_left_out_and_counted(self):
        res = oc.build([parcel("P1", far(0)), parcel("P1", far(1)), parcel("P2", far(2)),
                        parcel("P3", far(3))])
        self.assertEqual(res.groups, [("P2", "P3")])
        self.assertEqual(res.duplicate_ids, 2)
        self.assertIn("2 parcel rows share a parcel id", " ".join(oc.summary_notes(res)))


class AdjacencyTests(unittest.TestCase):
    """J4: adjacency in metres through LocalProjection."""

    def comp(self, *geoms, gap_ft=10.0, **kw):
        res = oc.build([parcel(f"P{i}", g) for i, g in enumerate(geoms, 1)], gap_ft, **kw)
        return res, res.members["P1"].component

    def test_shared_edge(self):
        self.assertEqual(self.comp(rect(0, 0), rect(SIDE_M, 0))[1], ("P1", "P2"))

    def test_five_feet_yes_fifty_feet_no_both_directions(self):
        for name, offset in (("east", lambda g: rect(SIDE_M + g, 0)),
                             ("north", lambda g: rect(0, SIDE_M + g))):
            with self.subTest(direction=name):
                self.assertEqual(self.comp(rect(0, 0), offset(5 * FT))[1], ("P1", "P2"))
                self.assertEqual(self.comp(rect(0, 0), offset(50 * FT))[1], ("P1",))
                # Just either side of the 10 ft default.
                self.assertEqual(self.comp(rect(0, 0), offset(9.5 * FT))[1], ("P1", "P2"))
                self.assertEqual(self.comp(rect(0, 0), offset(10.5 * FT))[1], ("P1",))

    def test_gap_is_feet_not_degrees(self):
        # 0.0001 degree of longitude is about 30 ft here, of latitude about 36 ft:
        # neither is within 10 ft, and a 40 ft setting takes both.
        a = box(LON0, LAT0, LON0 + 0.003, LAT0 + 0.003)
        east = box(LON0 + 0.0031, LAT0, LON0 + 0.006, LAT0 + 0.003)
        north = box(LON0, LAT0 + 0.0031, LON0 + 0.003, LAT0 + 0.006)
        for other in (east, north):
            self.assertEqual(self.comp(a, other)[1], ("P1",))
            self.assertEqual(self.comp(a, other, gap_ft=40)[1], ("P1", "P2"))

    def test_corner_touch_counts(self):
        self.assertEqual(self.comp(rect(0, 0), rect(SIDE_M, SIDE_M))[1], ("P1", "P2"))

    def test_chain(self):
        res, comp = self.comp(rect(0, 0), rect(SIDE_M, 0), rect(2 * SIDE_M, 0), far(5))
        self.assertEqual(comp, ("P1", "P2", "P3"))
        self.assertEqual(res.members["P3"].component, ("P1", "P2", "P3"))
        self.assertEqual(res.members["P4"].component, ("P4",))
        self.assertEqual(res.members["P4"].group, ("P1", "P2", "P3", "P4"))

    def test_never_across_groups(self):
        res = oc.build([parcel("P1", rect(0, 0)), parcel("P2", rect(SIDE_M, 0), owner="OMEGA LLC"),
                        parcel("P3", far(4)), parcel("P4", far(5), owner="OMEGA LLC")])
        self.assertEqual(res.members["P1"].component, ("P1",))
        self.assertEqual(res.members["P2"].component, ("P2",))

    def test_band(self):
        two = oc.build([parcel("P1", rect(0, 0)), parcel("P2", rect(SIDE_M, 0))])
        lines = oc.describe("P1", two, lambda pid: "ranked #2")
        self.assertIn("40 ac together with this one, inside the 5-50 ac search band: "
                      "a possible assemblage", lines[1])
        three = oc.build([parcel("P1", rect(0, 0)), parcel("P2", rect(SIDE_M, 0)),
                          parcel("P3", rect(2 * SIDE_M, 0))])
        line = oc.describe("P1", three, lambda pid: "ranked #2")[1]
        self.assertIn("60 ac together with this one, larger than the 5-50 ac search band", line)
        self.assertNotIn("assemblage", line)
        # The band comes from the run's [area] settings, not a fixed 5/50.
        narrow = oc.build([parcel("P1", rect(0, 0)), parcel("P2", rect(SIDE_M, 0))],
                          min_acres=10, max_acres=30)
        self.assertIn("larger than the 10-30 ac search band", oc.describe("P1", narrow, str)[1])

    def test_settings_key(self):
        self.assertEqual(oc.gap_ft_setting({}), 10.0)
        self.assertEqual(oc.gap_ft_setting({"owners": {}}), 10.0)
        self.assertEqual(oc.gap_ft_setting({"owners": {"adjacency_gap_ft": 25}}), 25.0)
        self.assertEqual(oc.gap_ft_setting({"owners": {"adjacency_gap_ft": 0}}), 0.0)
        for bad in (-1, "10", True, float("nan"), float("inf"), None):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                oc.gap_ft_setting({"owners": {"adjacency_gap_ft": bad}})
        # owners = 10 at the top level (a key, not a table) is refused, not a crash.
        for bad in (10, "x", [1]):
            with self.subTest(table=bad), self.assertRaises(ValueError):
                oc.gap_ft_setting({"owners": bad})


class CostAndFailureTests(unittest.TestCase):
    """J6: a big group stays fast; a shape that can't be measured is
    flagged, not fatal, and not hidden."""

    def grid(self, n=2000, cols=50):
        return [parcel(f"G{i:05d}", rect((i % cols) * SIDE_M, (i // cols) * SIDE_M))
                for i in range(n)]

    def test_two_thousand_parcels_in_one_group(self):
        ps = self.grid()
        t0 = time.monotonic()
        res = oc.build(ps)
        self.assertLess(time.monotonic() - t0, 5.0)
        self.assertEqual(len(res.groups), 1)
        self.assertEqual(len(res.members["G00000"].component), 2000)
        line = oc.describe("G00000", res, lambda pid: "ranked #1")[1]
        self.assertIn("40,000 ac together with this one, larger than", line)

    def test_projection_error_marks_one_parcel(self):
        ps = self.grid(200, cols=20)
        broken = ps[0]["geometry"]
        real = oc._project

        def project(proj, g):
            if g is broken:
                raise GEOSException("TopologyException: test")
            return real(proj, g)
        with unittest.mock.patch.object(oc, "_project", project):
            res = oc.build(ps)
        self.assertEqual(res.not_checked, 1)
        self.assertTrue(res.members["G00000"].not_checked)
        self.assertEqual(res.members["G00000"].component, ("G00000",))
        self.assertEqual(len(res.members["G00001"].component), 199)
        self.assertEqual(len(res.members["G00000"].group), 200)  # still counted
        lines = oc.describe("G00000", res, str)
        self.assertIn("adjacency to the other parcels not checked", lines[-1])
        self.assertIn("Same-owner adjacency not checked for 1 parcels",
                      " ".join(oc.summary_notes(res)))

    def test_distance_error_marks_both_ends(self):
        ps = self.grid(4, cols=4)  # a row of four: G0-G1-G2-G3
        middle = {id(ps[1]["geometry"]), id(ps[2]["geometry"])}
        real_project, real_distance = oc._project, oc._distance
        source = {}

        def project(proj, g):
            out = real_project(proj, g)
            source[id(out)] = id(g)
            return out

        def distance(x, y):
            if {source[id(x)], source[id(y)]} == middle:
                raise GEOSException("test")
            return real_distance(x, y)
        with unittest.mock.patch.object(oc, "_project", project), \
                unittest.mock.patch.object(oc, "_distance", distance):
            res = oc.build(ps)
        self.assertEqual(res.not_checked, 2)
        self.assertEqual([m for m in sorted(res.members) if res.members[m].not_checked],
                         ["G00001", "G00002"])
        self.assertEqual(res.members["G00000"].component, ("G00000", "G00001"))
        self.assertEqual(res.members["G00003"].component, ("G00002", "G00003"))

    def test_other_errors_are_not_swallowed(self):
        with unittest.mock.patch.object(oc, "_distance", side_effect=ZeroDivisionError):
            with self.assertRaises(ZeroDivisionError):
                oc.build([parcel("P1", rect(0, 0)), parcel("P2", rect(SIDE_M, 0))])

    def test_no_broad_except(self):
        tree = ast.parse((SCREENER / "owner_clusters.py").read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.ExceptHandler):
                self.assertIsInstance(n.type, ast.Name)
                self.assertEqual(n.type.id, "GEOSException")


class DescribeTests(unittest.TestCase):
    """J5, J7: wording, cap, statuses, determinism."""

    def test_cap_order_and_statuses(self):
        ps = [parcel("P00", rect(0, 0)), parcel("P99", rect(SIDE_M, 0))]
        ps += [parcel(f"P{i:02d}", far(i + 1)) for i in range(1, 12)]
        res = oc.build(ps)
        status = {"P99": "ranked #4", "P01": "screened out: built on"}
        line = oc.describe("P00", res, lambda pid: status.get(pid, "not screened in this trial run"))[0]
        self.assertIn("on 12 other 5-50 ac parcels in the jurisdictions searched (260 ac with this one)",
                      line)
        listed = line.split(": ", 2)[2]
        self.assertTrue(listed.startswith("P99 (ranked #4), P01 (screened out: built on), "
                                          "P02 (not screened in this trial run), P03 ("))
        self.assertIn("P04 (not screened in this trial run), and 7 more.", listed)
        self.assertNotIn("P05", listed)
        self.assertEqual(oc.describe("P00", oc.build(list(reversed(ps))),
                                     lambda pid: status.get(pid, "not screened in this trial run")),
                         oc.describe("P00", res,
                                     lambda pid: status.get(pid, "not screened in this trial run")))

    def test_wording(self):
        ps = [parcel("P1", rect(0, 0)), parcel("P2", rect(SIDE_M, 0)), parcel("P3", far(3))]
        res = oc.build(ps, gap_ft=10)
        lines = oc.describe("P1", res, str, mismatch_of=lambda pid: pid == "P2")
        self.assertEqual(len(lines), 2)
        text = " ".join(lines)
        for words in ("tax-roll owner name and mailing address", "5-50 ac",
                      "jurisdictions searched", "Counts cover only that band and area",
                      "touching or within 10 ft", "county parcel map",
                      "acres from the tax record, the parcel map disagrees for some",
                      "a corner touch is not frontage"):
            self.assertIn(words, text)
        for words in BANNED_WORDS:
            self.assertNotIn(words, text.lower())
        self.assertNotIn("DELTA", text)
        self.assertNotIn("OAK AVE", text)
        self.assertEqual(oc.describe("P1", oc.build([ps[0]]), str), [])
        self.assertEqual(len(oc.describe("P3", res, str)), 1)  # not adjacent: count line only

    def test_summary_note_is_counts_only(self):
        ps = [parcel("P1", rect(0, 0)), parcel("P2", rect(SIDE_M, 0)), parcel("P3", far(3)),
              parcel("Q1", far(5), owner="OMEGA LLC"), parcel("Q2", far(6), owner="OMEGA LLC"),
              parcel("S1", far(8), owner="SOLO LLC")]
        [note] = oc.summary_notes(oc.build(ps))
        self.assertIn("2 groups of 2 or more parcels (5 parcels, largest 3)", note)
        self.assertIn("among the 6 parcels of 5-50 ac fetched", note)
        self.assertIn("1 adjacent sets (touching or within 10 ft), 1 inside the band and 0 larger",
                      note)
        for words in ("P1", "DELTA", "OMEGA", "OAK"):
            self.assertNotIn(words, note)


# Fixture for the run.main tests: distinct owners and mailings, unlike the
# shared default in test_integrity.county_parcel.
DELTA = ("DELTA LAND LLC", "9 OAK AVE, ATLANTA GA 30310")
OMEGA = ("OMEGA HOLDINGS LP", "500 PEACHTREE ST STE 100, ATLANTA GA 30308")
EAST_OF_A = (A[0] + SIDE_M / (111_320 * math.cos(math.radians(A[1]))), A[1])
WEIRD = 'X<b>"1";2'


def run_parcel(pid, corner, who, **changes):
    p = county_parcel(pid, corner, owner=who[0])
    p["owner_mailing"] = who[1]
    p.update(changes)
    return p


class RunTests(RunHarness):
    """J1, J5, J7, J8: run.main end to end."""

    def setUp(self):
        super().setUp()
        self.parcels[:] = [
            run_parcel("14F0001 0001", A, DELTA),                      # adjacent pair, with 0002
            run_parcel("14F0001 0002", EAST_OF_A, DELTA),
            run_parcel("14F0003 0003", (-84.46, 33.6), DELTA),         # same owner, far away
            run_parcel("14F0004 0004", (-84.45, 33.6), DELTA,          # same owner, built on
                       improvement_value=900000.0, total_value=1300000.0),
            run_parcel("14F0005 0005", (-84.44, 33.6), OMEGA),         # non-adjacent pair
            run_parcel(WEIRD, (-84.43, 33.6), OMEGA),
            run_parcel("14F0007 0007", (-84.48, 33.6), ("SOLO LLC", "1 PINE RD, ATLANTA GA 30311")),
            run_parcel("14F0008 0008", (-84.42, 33.6), ("UNKNOWN", DELTA[1])),
        ]
        self.fresh = copy.deepcopy(self.parcels)
        self.seen: list[tuple[str, frozenset]] = []
        for obj, name in ((rank, "learned_score"), (scoring, "fixed_score"),
                          (screens, "site_rejects"), (screens, "flags")):
            real = getattr(obj, name)

            def spy(p, *a, _real=real, _name=name, **k):
                self.seen.append((_name, frozenset(p), tuple(p.get("flags") or ())))
                return _real(p, *a, **k)
            self.patch(obj, name, spy)

    def rows(self):
        with open(self.out / "ranked.csv", newline="") as fh:
            return {r["parcel_id"]: r for r in csv.DictReader(fh)}

    def requests(self):
        """Every request this run made, as a sorted list (slope requests run
        in threads, so their order varies)."""
        return sorted((u, json.dumps(c, sort_keys=True, default=str)) for u, c in self.session.calls)

    def owner_lines(self, row):
        return [x for x in row["flags"].split("; ") if x.startswith(oc.FLAG_PREFIX)] \
            if row["flags"] else []

    def assert_never_seen_by_scoring(self):
        called = {name for name, _, _ in self.seen}
        self.assertTrue({"learned_score", "fixed_score", "site_rejects", "flags"} <= called)
        for name, keys, flags in self.seen:
            for key in keys:
                self.assertFalse(any(w in key.lower() for w in oc.KEY_STEMS), f"{key} seen by {name}")
            self.assertFalse(any(oc.FLAG_PREFIX in f for f in flags), name)

    def check_expected_lines(self):
        rows = self.rows()
        self.assertEqual(set(rows), {"14F0001 0001", "14F0001 0002", "14F0003 0003",
                                     "14F0005 0005", WEIRD, "14F0007 0007", "14F0008 0008"})
        for pid in ("14F0001 0001", "14F0001 0002"):
            lines = self.owner_lines(rows[pid])
            self.assertEqual(len(lines), 2, pid)
            self.assertIn("on 3 other 5-50 ac parcels", lines[0])
            self.assertIn("14F0004 0004 (screened out: ", lines[0])
            self.assertIn("within 10 ft of 1 of them", lines[1])
            self.assertIn("40 ac together with this one, inside the 5-50 ac search band", lines[1])
        # The adjacent sibling is listed first.
        self.assertTrue(self.owner_lines(rows["14F0001 0001"])[0].split(": ", 2)[2]
                        .startswith("14F0001 0002 (ranked #"))
        [line] = self.owner_lines(rows["14F0003 0003"])
        self.assertIn("on 3 other", line)
        [line] = self.owner_lines(rows["14F0005 0005"])
        self.assertIn("on 1 other 5-50 ac parcel in", line)
        self.assertIn(WEIRD + " (ranked #", line)
        for pid in ("14F0007 0007", "14F0008 0008"):
            self.assertEqual(self.owner_lines(rows[pid]), [], pid)
        return rows

    def test_spy_slope_on(self):
        code, _ = self.main()
        self.assertEqual(code, 0)
        self.assert_never_seen_by_scoring()
        self.check_expected_lines()

    def test_spy_no_slope(self):
        code, _ = self.main("--no-slope")
        self.assertEqual(code, 0)
        self.assert_never_seen_by_scoring()
        self.check_expected_lines()

    def test_scores_order_and_requests_do_not_depend_on_the_groups(self):
        for args in ((), ("--no-slope",)):
            with self.subTest(args=args):
                # Empty the HTTP cache before each run so both runs make
                # (and log) the same requests rather than reading the cache.
                shutil.rmtree(self.tmp / "http", ignore_errors=True)
                self.parcels[:] = copy.deepcopy(self.fresh)
                self.main(*args)
                calls_on = self.requests()
                on = {pid: (r["rank"], r["score"], r["why_site"], r["why_seller"],
                            "; ".join(x for x in r["flags"].split("; ")
                                      if not x.startswith(oc.FLAG_PREFIX)))
                      for pid, r in self.rows().items()}
                self.assertTrue(any(self.owner_lines(r) for r in self.rows().values()))
                with unittest.mock.patch.object(oc, "build", lambda *a, **k: oc.Result()):
                    shutil.rmtree(self.tmp / "http", ignore_errors=True)
                    self.parcels[:] = copy.deepcopy(self.fresh)
                    self.main(*args)
                off = {pid: (r["rank"], r["score"], r["why_site"], r["why_seller"], r["flags"])
                       for pid, r in self.rows().items()}
                self.assertEqual(on, off)
                self.assertFalse(any(self.owner_lines(r) for r in self.rows().values()))
                self.assertEqual(self.requests(), calls_on)
                self.assertTrue(calls_on)
                self.assert_never_seen_by_scoring()

    def test_limit_gives_the_same_counts_and_labels_the_rest(self):
        self.main("--no-slope")
        full = self.owner_lines(self.rows()["14F0001 0001"])
        self.parcels[:] = copy.deepcopy(self.fresh)
        self.main("--no-slope", "--limit", "2")
        rows = self.rows()
        self.assertEqual(set(rows), {"14F0001 0001", "14F0001 0002"})
        trial = self.owner_lines(rows["14F0001 0001"])
        self.assertEqual(trial[0].split(": ", 2)[1], full[0].split(": ", 2)[1])  # counts, acres
        self.assertEqual(trial[1], full[1])
        self.assertIn("14F0003 0003 (not screened in this trial run)", trial[0])
        self.assertIn("14F0004 0004 (not screened in this trial run)", trial[0])
        self.assertIn("among the 8 parcels of 5-50 ac fetched (before any screen or trial limit)",
                      self.notes())

    def test_pass_decision_shows_as_screened_out(self):
        run.DECISIONS_FILE.write_text("parcel_id,decision,date,note\n14F0003 0003,pass,,\n")
        self.main("--no-slope")
        line = self.owner_lines(self.rows()["14F0001 0001"])[0]
        self.assertIn("14F0003 0003 (screened out: ", line)
        self.assertNotIn("14F0003 0003 (ranked", line)

    def test_runs_are_deterministic(self):
        self.main("--no-slope")
        first = (self.out / "ranked.csv").read_bytes()
        self.parcels[:] = copy.deepcopy(list(reversed(self.fresh)))
        self.main("--no-slope")
        self.assertEqual((self.out / "ranked.csv").read_bytes(), first)

    def test_outputs_escape_odd_ids(self):
        self.main("--no-slope")
        row = self.rows()["14F0005 0005"]
        self.assertIn(WEIRD, row["flags"])
        page = (self.out / "map.html").read_text()
        script = page.split("const data = ", 1)[1].split(";\nconst map", 1)[0]
        self.assertNotIn("<b>", script)
        data = json.loads(script)
        props = {f["properties"]["parcel_id"]: f["properties"] for f in data["features"]}
        self.assertIn(WEIRD, props["14F0005 0005"]["flags"])
        with open(self.out / "rejected.csv", newline="") as fh:
            self.assertEqual(next(csv.reader(fh)), ["parcel_id", "jurisdiction", "site_address",
                                                    "acres", "owner", "decision", "reasons"])
        with open(self.out / "ranked.csv", newline="") as fh:
            header = next(csv.reader(fh))
        from screener import report
        self.assertEqual(header, report.RANKED_COLUMNS)
        brief = next(f for f in (self.out / "briefs").iterdir()
                     if "14F0001_0001" in f.name and f.suffix == ".md")
        watch = brief.read_text().split("## Watch-outs", 1)[1]
        self.assertIn("Same owner, adjacent: touching or within 10 ft", watch)

    def test_summary_notes(self):
        self.main("--no-slope")
        notes = self.notes()
        self.assertIn("Same-owner groups: 2 groups of 2 or more parcels (6 parcels, largest 4)", notes)
        for words in ("DELTA", "OMEGA", "OAK AVE"):
            self.assertNotIn(words, notes)

    def test_measuring_error_is_flagged_and_the_run_goes_on(self):
        real = oc._project

        def project(proj, g):
            if g.bounds[0] == EAST_OF_A[0]:
                raise GEOSException("test")
            return real(proj, g)
        self.patch(oc, "_project", project)
        code, log = self.main("--no-slope")
        self.assertEqual(code, 0)
        self.assertNotIn("Stopping:", log)
        lines = self.owner_lines(self.rows()["14F0001 0002"])
        self.assertIn("adjacency to the other parcels not checked", lines[-1])
        self.assertEqual(len(self.owner_lines(self.rows()["14F0001 0001"])), 1)  # no pair now
        self.assertIn("Same-owner adjacency not checked for 1 parcels", self.notes())

    def settings_with(self, transform):
        path = self.tmp / "settings.toml"
        path.write_text(transform(SETTINGS_FILE.read_text()))
        return path

    def run_with(self, path, *args):
        self.serve(self.routes, self.downloads)
        log = io.StringIO()
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            code = run.main(["--settings", str(path), "--out", str(self.out), *args])
        return code, log.getvalue()

    def test_old_settings_file_without_owners_table(self):
        text = SETTINGS_FILE.read_text()
        self.assertIn("\n[owners]\n", text)
        path = self.settings_with(lambda t: t.split("\n[owners]\n", 1)[0] + "\n")
        code, _ = self.run_with(path, "--no-slope")
        self.assertEqual(code, 0)
        self.assertIn("within 10 ft", self.owner_lines(self.rows()["14F0001 0001"])[1])

    def test_gap_and_band_come_from_settings(self):
        path = self.settings_with(lambda t: t.replace("adjacency_gap_ft = 10", "adjacency_gap_ft = 25")
                                  .replace("max_acres = 50", "max_acres = 30"))
        code, _ = self.run_with(path, "--no-slope")
        self.assertEqual(code, 0)
        line = self.owner_lines(self.rows()["14F0001 0001"])[1]
        self.assertIn("touching or within 25 ft", line)
        self.assertIn("larger than the 5-30 ac search band", line)

    def test_bad_gap_setting_is_refused_before_any_fetch(self):
        path = self.settings_with(lambda t: t.replace("adjacency_gap_ft = 10", "adjacency_gap_ft = -5"))
        with self.assertRaises(SystemExit) as cm:
            self.run_with(path)
        self.assertEqual(cm.exception.code, 2)
        self.fetch_parcels.assert_not_called()

    def test_owners_not_a_table_is_refused_before_any_fetch(self):
        # A top-level "owners = 10" (a key, not the [owners] table).
        path = self.settings_with(lambda t: "owners = 10\n" + t.split("\n[owners]\n", 1)[0] + "\n")
        with self.assertRaises(SystemExit) as cm:
            self.run_with(path)
        self.assertEqual(cm.exception.code, 2)
        self.fetch_parcels.assert_not_called()


def named_group_key(tree: ast.AST) -> list[str]:
    """Every string constant, name or attribute in tree that mentions a key
    stem (the static guard's one rule, shared by its self-test)."""
    hits = []
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        if text is not None and any(w in text.lower() for w in oc.KEY_STEMS):
            hits.append(text)
    return hits


class StaticGuardTests(unittest.TestCase):
    """J2, backstop only: no string constant or name in rank.py or scoring.py
    mentions a group key stem. A string built at runtime ("clus" + "ter")
    gets past this; the run.main spy tests above, which check the keys on
    every parcel the scorers see, are what hold. "adjacen" is deliberately
    not a stem: rank.py already says "rail-adjacent"."""

    def test_stems(self):
        self.assertEqual(oc.KEY_STEMS, ("cluster", "portfolio", "assembl"))
        self.assertIn("adjacent", (SCREENER / "rank.py").read_text())

    def test_rank_and_scoring_never_name_the_groups(self):
        for name in ("rank.py", "scoring.py"):
            with self.subTest(file=name):
                self.assertEqual(named_group_key(ast.parse((SCREENER / name).read_text())), [])

    def test_the_guard_catches_a_named_read(self):
        cases = {
            'def learned_score(p):\n    return p.get("portfolio_parcels", 0)\n': ["portfolio_parcels"],
            'def learned_score(p):\n    return p["Assemblage_Acres"]\n': ["Assemblage_Acres"],
            'def learned_score(p):\n    return run.owner_cluster\n': ["owner_cluster"],
            'def learned_score(p):\n    return p.get("clus" + "ter", 0)\n': [],
        }
        for src, hits in cases.items():
            with self.subTest(src=src):
                self.assertEqual(named_group_key(ast.parse(src)), hits)


if __name__ == "__main__":
    unittest.main()
