"""Lead grade, the leads.md digest, the report facts and the kept slope
answers (reviews/35_screener_lead_layer). Offline.

The grade is a live-only report fact: run.main attaches it after the sort
and every report check, so no scorer or screen ever sees it. The spy tests
are the real guard; the static stem test is a backstop.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import csv
import io
import json
import os
import time
import unittest
from pathlib import Path

from screener import (arcgis, checks, decide, detention, digest, facts, frontage, grade,
                      nextdoor, owner_clusters, rank, report, run, scoring, screens, sitefit, soils)
from screener.tests.test_integrity import (
    A, B, C, GOOD_SLOPE, FakeResponse, Offline, RunHarness, county_parcel, fake_roads,
    fake_sda, square,
)

SCREENER = Path(run.__file__).resolve().parent
STEMS = grade.KEY_STEMS
DRY_SOIL = [("1654021", 1.0, "B", "Well drained", "0", "Not limited")]
S = grade.Settings()


def clear_parcel(**over):
    """A parcel on which every core check came back clear."""
    p = {
        "entitlement": "by_right", "entitlement_note": "zoned industrial",
        "_envelope_detail": sitefit.Fit(sitefit.FITS, label="cross-dock", length_ft=640.0,
                                        width_ft=520.0, building_sf=332800),
        detention.DETAIL_KEY: detention.Pond(detention.FITS, beside_envelope=True),
        frontage.DETAIL_KEY: frontage.Frontage(frontage.CHECKED, feet=600, limited_feet=0,
                                               private_feet=0, road="local road, GDOT route 1"),
        soils.DETAIL_KEY: soils.Soils(soils.CHECKED, hsg="B", hydric_pct=0.0,
                                      poorly_drained_pct=0.0, dwellings_limited_pct=0.0),
        "contamination": [], "road": "Paved", "years_held": 20.0, "last_sale_price": 90000.0,
        "steep_pct": 4.0, grade.NEXT_DOOR_KEY: 0,
        nextdoor.OWNER_KEY: [], nextdoor.SITES_KEY: [],
    }
    p.update(over)
    return p


class RuleTests(unittest.TestCase):
    """grade.grade_parcel on hand-built report facts."""

    def g(self, **over):
        return grade.grade_parcel(clear_parcel(**over), S)

    def test_all_clear_is_a(self):
        lead = self.g()
        self.assertEqual((lead.grade, lead.blocks, lead.found, lead.not_checked),
                         (grade.A, [], [], []))
        self.assertEqual(lead.fields()["lead_grade"], "A")
        self.assertEqual(self.g(entitlement="likely").grade, grade.A)

    def test_zoning_path(self):
        self.assertEqual(self.g(entitlement="possible").grade, grade.B)
        self.assertEqual(self.g(entitlement="possible", entitlement_note="rezoning needed").found,
                         ["zoning path only possible (rezoning needed)"])
        hard = self.g(entitlement="hard", entitlement_note="zoned and planned residential")
        self.assertEqual(hard.grade, grade.BLOCKED)
        # Blocked rests on the zoning path alone, so a cap does not hide it.
        capped = grade.grade_parcel(clear_parcel(entitlement="hard"), S, "soils only checked")
        self.assertEqual(capped.grade, grade.BLOCKED)
        self.assertEqual(hard.blocks, ["entitlement unlikely (zoned and planned residential)"])
        self.assertEqual(hard.fields()["lead_found"], hard.blocks)
        for path in ("unknown", None, "something new"):
            with self.subTest(path=path):
                lead = self.g(entitlement=path)
                self.assertEqual(lead.grade, grade.B)
                self.assertIn("zoning path not known", lead.not_checked[0])

    def test_building_fit_three_states(self):
        none = self.g(_envelope_detail=sitefit.Fit(sitefit.NONE_FOUND))
        self.assertEqual(none.grade, grade.B)
        self.assertEqual(none.found, ["no building envelope found (a lower bound, not proof)"])
        off = self.g(_envelope_detail=sitefit.not_checked("fit switched off in settings"))
        self.assertEqual((off.grade, off.found), (grade.B, []))
        self.assertEqual(off.not_checked,
                         ["building fit not checked (fit switched off in settings)"])
        missing = self.g(_envelope_detail=None)
        self.assertEqual(missing.not_checked, ["building fit not checked (no result)"])
        # A state this module does not know is not checked, never clear.
        odd = self.g(_envelope_detail=sitefit.Fit("some new state"))
        self.assertEqual((odd.grade, odd.not_checked),
                         (grade.B, ["building fit not checked (no result)"]))
        odd_pond = self.g(**{detention.DETAIL_KEY: detention.Pond("some new state")})
        self.assertEqual(odd_pond.not_checked, ["detention pond not checked (no result)"])

    def test_pond_is_read_only_beside_an_envelope(self):
        no_room = self.g(**{detention.DETAIL_KEY: detention.Pond(detention.NONE_FOUND)})
        self.assertEqual(no_room.found,
                         ["no detention pond room found beside the building envelope"])
        unchecked = self.g(**{detention.DETAIL_KEY: detention.not_checked("geometry fault")})
        self.assertEqual(unchecked.not_checked, ["detention pond not checked (geometry fault)"])
        # No envelope: the fit counts once, the pond is not counted as well.
        both = self.g(_envelope_detail=sitefit.Fit(sitefit.NONE_FOUND),
                      **{detention.DETAIL_KEY: detention.Pond(detention.NONE_FOUND)})
        self.assertEqual((both.grade, len(both.found)), (grade.B, 1))

    def test_frontage_three_states_and_the_minimum(self):
        none = self.g(**{frontage.DETAIL_KEY: frontage.Frontage(
            frontage.NONE, feet=0, limited_feet=300, private_feet=120)})
        self.assertEqual(none.found, ["no public road frontage within 50 ft (120 ft on a "
                                      "private road; 300 ft on an access-controlled road)"])
        short = self.g(**{frontage.DETAIL_KEY: frontage.Frontage(
            frontage.CHECKED, feet=49, limited_feet=0, private_feet=0, road="x")})
        self.assertEqual(short.found, ["only 49 ft of public road frontage (under 50 ft)"])
        at = self.g(**{frontage.DETAIL_KEY: frontage.Frontage(
            frontage.CHECKED, feet=50, limited_feet=0, private_feet=0, road="x")})
        self.assertEqual(at.grade, grade.A)
        off = self.g(**{frontage.DETAIL_KEY: frontage.not_checked("road service failed (x)")})
        self.assertEqual(off.not_checked, ["road frontage not checked (road service failed (x))"])
        self.assertEqual(self.g(**{frontage.DETAIL_KEY: None}).not_checked,
                         ["road frontage not checked (no result)"])
        odd = self.g(**{frontage.DETAIL_KEY: frontage.Frontage("some new state", feet=900)})
        self.assertEqual((odd.grade, odd.not_checked),
                         (grade.B, ["road frontage not checked (no result)"]))

    def test_soils_three_states(self):
        def soil(**kw):
            return {soils.DETAIL_KEY: soils.Soils(soils.CHECKED, hsg="B", **kw)}
        self.assertEqual(self.g(**soil(hydric_pct=25.0, poorly_drained_pct=0.0)).found,
                         ["25% hydric soil components (SSURGO map units)"])
        self.assertEqual(self.g(**soil(hydric_pct=24.9, poorly_drained_pct=30.0)).found,
                         ["30% poorly drained soils (SSURGO map units)"])
        # One soils line at most, even when both shares are high.
        self.assertEqual(len(self.g(**soil(hydric_pct=40.0, poorly_drained_pct=40.0)).found), 1)
        # An answer that came back with no rating is checked: nothing to count.
        self.assertEqual(self.g(**soil(hydric_pct=soils.NONE_RATED,
                                       poorly_drained_pct=soils.NONE_RATED)).grade, grade.A)
        # A failed, capped, switched-off or missing soils check is NOT clear.
        failed = self.g(**{soils.DETAIL_KEY: soils.not_checked("soil service failed (503)")})
        self.assertEqual((failed.grade, failed.not_checked),
                         (grade.B, ["soils not checked (soil service failed (503))"]))
        self.assertEqual(self.g(**{soils.DETAIL_KEY: None}).not_checked,
                         ["soils not checked (no result)"])
        self.assertEqual(self.g(**{soils.DETAIL_KEY: soils.Soils("some new state")}).grade,
                         grade.B)

    def test_slope_and_the_parcel_map(self):
        failed = self.g(steep_pct=None, slope_error="giving up after 5 tries")
        self.assertEqual((failed.grade, failed.not_checked), (grade.B, [
            "slope check failed (the steep-ground screen was not applied)"]))
        tiny = self.g(steep_pct=None, slope_skipped="x")
        self.assertEqual(tiny.not_checked,
                         ["slope not checked (under one lidar pixel of open ground)"])
        self.assertEqual(self.g(steep_pct=None).not_checked, ["slope not checked"])
        self.assertEqual(self.g(steep_pct=0.0).grade, grade.A)
        off = self.g(acreage_mismatch=True)
        self.assertEqual(off.grade, grade.B)
        self.assertIn("parcel map disagrees with the tax record", off.not_checked[0])
        fit = sitefit.Fit(sitefit.FITS, label="x", length_ft=1.0, width_ft=1.0, map_mismatch=True)
        self.assertEqual(self.g(_envelope_detail=fit).grade, grade.B)

    def test_same_owner_next_door(self):
        door = self.g(**{grade.NEXT_DOOR_KEY: 2})
        self.assertIn("the same owner holds 2 built-on parcels adjacent to this one, directly "
                      "or through its other parcels", door.found[0])
        for unknown in (None, True, -1, "2"):
            with self.subTest(unknown=unknown):
                lead = self.g(**{grade.NEXT_DOOR_KEY: unknown})
                self.assertEqual((lead.grade, lead.not_checked),
                                 (grade.B, ["same-owner adjacency not checked"]))
        p = clear_parcel()
        del p[grade.NEXT_DOOR_KEY]
        self.assertEqual(grade.grade_parcel(p, S).not_checked,
                         ["same-owner adjacency not checked"])
        off = grade.Settings(owner_next_door=False)
        self.assertEqual(grade.grade_parcel(clear_parcel(**{grade.NEXT_DOOR_KEY: None}),
                                            off).grade, grade.A)

    def test_other_watch_items(self):
        on_site = self.g(contamination=[{"feet": 0, "name": "X", "kind": "Brownfields"}])
        self.assertEqual(on_site.found, ["a listed contamination site is on the parcel"])
        self.assertEqual(self.g(contamination=[{"feet": 300, "name": "X", "kind": "k"}]).grade,
                         grade.A)
        locked = self.g(road="Land-Locked")
        self.assertEqual(locked.found, ["the assessor lists the parcel as land-locked"])
        # Not counted twice when frontage itself already counted.
        twice = self.g(road="Land-Locked", **{frontage.DETAIL_KEY: frontage.Frontage(
            frontage.NONE, feet=0, limited_feet=0, private_feet=0)})
        self.assertEqual(len(twice.found), 1)
        recent = self.g(years_held=2.24)
        self.assertEqual(recent.found, ["last priced deed 2.2 years ago (a recent purchase, or "
                                        "a transfer between related owners)"])
        self.assertEqual(self.g(years_held=5.0).grade, grade.A)
        self.assertEqual(self.g(years_held=-0.3).grade, grade.A)   # a future-dated deed
        self.assertEqual(self.g(years_held=1.0, last_sale_price=0).grade, grade.A)  # no sale
        self.assertEqual(self.g(years_held=None).grade, grade.A)

    def test_switches_and_thresholds(self):
        zero = grade.Settings(max_hydric_pct=0, max_poorly_drained_pct=0)   # 0 = off
        wet = clear_parcel()
        wet[soils.DETAIL_KEY] = soils.Soils(soils.CHECKED, hsg="D", hydric_pct=90.0,
                                            poorly_drained_pct=90.0)
        self.assertEqual(grade.grade_parcel(wet, zero).grade, grade.A)
        s = grade.Settings(owner_next_door=False, recent_purchase_years=0, min_frontage_ft=700,
                           max_hydric_pct=5)
        p = clear_parcel(years_held=1.0, **{grade.NEXT_DOOR_KEY: 3})
        p[soils.DETAIL_KEY] = soils.Soils(soils.CHECKED, hsg="B", hydric_pct=6.0,
                                          poorly_drained_pct=0.0)
        lead = grade.grade_parcel(p, s)
        self.assertEqual(lead.found, ["only 600 ft of public road frontage (under 700 ft)",
                                      "6% hydric soil components (SSURGO map units)"])
        self.assertEqual(lead.grade, grade.C)

    def test_counting_and_not_graded(self):
        two = self.g(entitlement="possible", years_held=1.0)
        self.assertEqual(two.grade, grade.C)
        ng = grade.grade_parcel(clear_parcel(entitlement="possible"), S, "x only checked")
        self.assertEqual(ng.grade, grade.NOT_GRADED)
        self.assertEqual(ng.fields()["lead_grade"], "not graded: x only checked")
        self.assertEqual(len(ng.found), 1)            # what was found is still listed

    def test_reads_only(self):
        p = clear_parcel()
        before = dict(p)
        grade.grade_parcel(p, S)
        self.assertEqual(p, before)

    def test_outside(self):
        caps = [("building fit", "fit", True, 0), ("road frontage", "frontage", True, 250)]
        self.assertEqual(grade._outside(250, caps), "")
        self.assertEqual(grade._outside(251, caps),
                         "road frontage only checked for the top 250 ([frontage] max_parcels)")
        caps[0] = ("building fit", "fit", False, 0)
        self.assertEqual(grade._outside(1, caps), "building fit not run (switched off)")

    def test_settings(self):
        self.assertEqual(grade.settings({}), grade.Settings())
        got = grade.settings({"grade": {"min_frontage_ft": 100, "digest_max": 5,
                                        "owner_next_door": False}})
        self.assertEqual((got.min_frontage_ft, got.digest_max, got.owner_next_door),
                         (100.0, 5, False))
        for bad in ({"grade": 3}, {"grade": {"enabled": 1}}, {"grade": {"min_frontage_ft": -1}},
                    {"grade": {"max_hydric_pct": "25"}}, {"grade": {"digest_max": 2.5}},
                    {"grade": {"digest_max": -1}}, {"grade": {"recent_purchase_years": True}},
                    {"grade": {"max_poorly_drained_pct": float("nan")}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                grade.settings(bad)

    def test_brief_lines(self):
        self.assertEqual(grade.brief_lines({}), ["**Lead grade: not graded** (grading off)"])
        a = grade.brief_lines(grade.grade_parcel(clear_parcel(), S).fields())
        self.assertEqual(len(a), 1)
        self.assertTrue(a[0].startswith("**Lead grade A**"))
        c = grade.brief_lines(grade.grade_parcel(
            clear_parcel(entitlement="unknown", years_held=1.0), S).fields())
        self.assertEqual(c[0], "**Lead grade C**")
        self.assertTrue(c[1].startswith("- Found: last priced deed 1.0 years ago"))
        self.assertTrue(c[2].startswith("- Not checked: zoning path not known"))
        b = grade.brief_lines(grade.grade_parcel(clear_parcel(entitlement="hard"), S).fields())
        self.assertEqual(b[0], "**Lead grade: blocked**")
        self.assertTrue(b[1].startswith("- Blocks: entitlement unlikely"))


class BuiltNextDoorTests(unittest.TestCase):
    def test_adjacent_built_on_siblings_only(self):
        def parcel(pid, corner, owner="TEST OWNER ALPHA"):
            return {"parcel_id": pid, "owner": owner, "owner_mailing": "1 TEST ST, ATLANTA GA 30303",
                    "acres": 20.0, "geometry": square(*corner)}
        side = square(*A).bounds[2] - A[0]
        touching = (A[0] + side, A[1])
        res = owner_clusters.build([parcel("P1", A), parcel("P2", touching), parcel("P3", B),
                                    parcel("P4", (A[0], A[1] + 0.01), owner="TEST OWNER BETA")])
        built = {"P2", "P3", "P4"}.__contains__
        self.assertEqual(owner_clusters.built_next_door("P1", res, built), ["P2"])  # P3 is far
        self.assertEqual(owner_clusters.built_next_door("P1", res, lambda x: False), [])
        self.assertEqual(owner_clusters.built_next_door("P4", res, built), [])      # no group
        self.assertEqual(owner_clusters.built_next_door("nope", res, built), [])
        line = owner_clusters.built_line(["P2"])
        self.assertTrue(line.startswith("Same owner, adjacent and built on: 1 same-owner parcel "
                                        "adjacent to this one, directly or through each other, "
                                        "is already built on (P2)"))
        many = owner_clusters.built_line([f"P{i}" for i in range(7)])
        self.assertIn("7 same-owner parcels adjacent to this one, directly or through each "
                      "other, are already built on", many)
        self.assertFalse(owner_clusters.adjacency_not_checked("P1", res))
        self.assertFalse(owner_clusters.adjacency_not_checked("nope", res))
        self.assertIn("and 2 more", many)


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
        for key in (*grade.COLUMNS, grade.DETAIL_KEY, grade.NEXT_DOOR_KEY):
            self.assertTrue(any(w in key for w in STEMS), key)

    def test_eval_files_do_not_import_the_new_modules(self):
        for path in (SCREENER / "autoresearch").glob("*.py"):
            text = path.read_text()
            tree = ast.parse(text)
            imported = {a.name.split(".")[-1] for n in ast.walk(tree)
                        if isinstance(n, ast.Import) for a in n.names}
            imported |= {a.name for n in ast.walk(tree)
                         if isinstance(n, ast.ImportFrom) for a in n.names}
            imported |= {(n.module or "").split(".")[-1] for n in ast.walk(tree)
                         if isinstance(n, ast.ImportFrom)}
            with self.subTest(file=path.name):
                self.assertFalse(imported & {"grade", "digest", "facts"})
                self.assertNotIn(facts.FILE_NAME, text)
                self.assertNotIn(facts.STORE_NAME, text)

    def test_facts_never_carry_identity_or_geometry(self):
        self.assertFalse(set(facts.COLUMNS) & set(facts.NEVER))
        self.assertTrue(set(grade.COLUMNS) <= set(facts.COLUMNS))
        self.assertTrue(set(facts.COLUMNS) <= set(report.RANKED_COLUMNS))


class GradeRunTests(RunHarness):
    """run.main end to end."""

    def setUp(self):
        super().setUp()
        self.routes[soils.SDA_URL] = fake_sda(DRY_SOIL)
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

    def assert_never_seen(self):
        called = {name for name, _ in self.seen}
        self.assertTrue({"learned_score", "fixed_score", "site_rejects", "flags"} <= called)
        for name, keys in self.seen:
            for key in keys:
                self.assertFalse(any(w in key.lower() for w in STEMS), f"{key} seen by {name}")

    def run_with(self, body, table="grade", *args):
        settings = self.tmp / "settings.toml"
        head = (SCREENER / "settings.toml").read_text()
        if f"[{table}]" in head and table == "grade":
            head = head.split("[grade]", 1)[0]
            settings.write_text(head + "[grade]\n" + body)
        else:
            settings.write_text(head + body)
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            return run.main(["--settings", str(settings), "--out", str(self.out), *args])

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
                    # Every check clear but the zoning (College Park has no
                    # machine-readable zoning): one item not checked.
                    self.assertEqual(r["lead_found"], "")
                    self.assertTrue(r["lead_not_checked"].startswith("zoning path not known"))
                    if args:      # without the slope check nothing is graded
                        self.assertEqual(r["lead_grade"], "not graded: the lidar slope check "
                                                          "not run (switched off)")
                        self.assertIn("slope not checked", r["lead_not_checked"])
                    else:
                        self.assertEqual(r["lead_grade"], "B")
                for p in self.parcels:
                    self.assertIn(grade.DETAIL_KEY, p)
                    self.assertEqual(p[grade.NEXT_DOOR_KEY], 0)

    def test_scores_order_and_requests_do_not_depend_on_the_grade(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        keep = [c for c in report.RANKED_COLUMNS if c not in grade.COLUMNS]
        on = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        before = sorted(json.dumps(c, sort_keys=True, default=str) for c in self.session.calls)
        self.parcels[:] = fresh
        self.clear_cache()
        self.assertEqual(self.run_with("enabled = false\n"), 0)
        off = {pid: tuple(r[k] for k in keep) for pid, r in self.rows().items()}
        self.assertEqual(on, off)
        after = sorted(json.dumps(c, sort_keys=True, default=str) for c in self.session.calls)
        self.assertEqual(after, before)                    # the grade asks no service
        for r in self.rows().values():
            self.assertEqual(r["lead_grade"], "not graded: grading switched off in settings")
        self.assertIn("Lead grade not given for 2 of 2 ranked parcels: grading switched off",
                      self.notes())
        self.assertIn("0 of 2 ranked parcels were graded", (self.out / "leads.md").read_text())
        self.assert_never_seen()

    def test_columns_private_keys_and_brief(self):
        self.main()
        with open(self.out / "ranked.csv", newline="") as fh:
            header = next(csv.reader(fh))
        i = header.index("frontage_private_ft")
        self.assertEqual(header[i + 1:i + 4], list(grade.COLUMNS))
        props = json.loads((self.out / "ranked.geojson").read_text())["features"][0]["properties"]
        self.assertEqual(props["lead_grade"], "B")
        for key in (grade.DETAIL_KEY, grade.NEXT_DOOR_KEY):
            self.assertNotIn(key, props)
        [brief] = sorted((self.out / "briefs").glob("001_*.md"))
        lines = brief.read_text().splitlines()
        i = lines.index("**Lead grade B**")
        self.assertLess(i, lines.index("## The site"))
        self.assertTrue(lines[i + 1].startswith("- Not checked: zoning path not known"))
        self.assertIn("Lead grade (a fixed rule over this screen's checks", self.notes())
        self.assertIn("A 0, B 2, C 0, blocked 0 of 2 ranked parcels", self.notes())

    def test_capped_checks_are_not_graded(self):
        settings = self.tmp / "settings.toml"
        text = (SCREENER / "settings.toml").read_text()
        head, tail = text.split("[frontage]", 1)
        tail = tail.replace("max_parcels = 250", "max_parcels = 1", 1)
        settings.write_text(head + "[frontage]" + tail)
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["--settings", str(settings), "--out", str(self.out)]), 0)
        rows = sorted(self.rows().values(), key=lambda r: int(r["rank"]))
        self.assertEqual(rows[0]["lead_grade"], "B")
        self.assertEqual(rows[1]["lead_grade"], "not graded: road frontage only checked for "
                                                "the top 1 ([frontage] max_parcels)")
        self.assertIn("Lead grade not given for 1 of 2 ranked parcels: road frontage only "
                      "checked for the top 1", self.notes())
        text = (self.out / "leads.md").read_text()
        self.assertIn("1 of 2 ranked parcels were graded", text)
        self.assertIn("1 not graded", text)

    def test_failed_service_is_not_checked_not_clear(self):
        for url, words in ((frontage.IDENTIFY_URL, "road frontage not checked (road service "
                                                   "failed"),
                           (soils.SDA_URL, "soils not checked (soil service failed"),
                           (checks.SLOPE_URL, "slope check failed (the steep-ground screen "
                                              "was not applied)")):
            with self.subTest(url=url):
                self.parcels[:] = [county_parcel("14F0001 0001", A),
                                   county_parcel("14F0002 0002", B, owner="TEST OWNER BETA")]
                self.clear_cache()
                good = self.routes[url]
                self.routes[url] = lambda params: FakeResponse(status=503)
                code, _ = self.main()
                self.routes[url] = good
                self.assertEqual(code, 0)
                for r in self.rows().values():
                    self.assertEqual(r["lead_grade"], "C")  # the zoning and this: not checked
                    self.assertIn(words, r["lead_not_checked"])
                text = (self.out / digest.FILE_NAME).read_text()
                self.assertIn("A 0, B 0, C 2", text)

    def test_capped_or_switched_off_soils_are_not_graded(self):
        settings = self.tmp / "settings.toml"
        text = (SCREENER / "settings.toml").read_text()
        head, tail = text.split("[soils]", 1)
        settings.write_text(head + "[soils]" + tail.replace("enabled = true",
                                                            "enabled = false", 1))
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["--settings", str(settings), "--out", str(self.out)]), 0)
        for r in self.rows().values():
            self.assertEqual(r["lead_grade"], "not graded: soils not run (switched off)")

    def test_built_next_door_flag_and_grade(self):
        side = square(*A).bounds[2] - A[0]
        plant = county_parcel("14F0001 0009", (A[0] + side, A[1]))
        plant.update(improvement_value=5_000_000.0, total_value=5_400_000.0)
        self.parcels.append(plant)
        code, _ = self.main()
        self.assertEqual(code, 0)
        rows = self.rows()
        self.assertNotIn("14F0001 0009", rows)             # built on: screened out
        a, b = rows["14F0001 0001"], rows["14F0002 0002"]
        self.assertIn("Same owner, adjacent and built on: 1 same-owner parcel adjacent to "
                      "this one, directly or through each other, is already built on "
                      "(14F0001 0009)", a["flags"])
        self.assertIn("the same owner holds 1 built-on parcel adjacent to this one",
                      a["lead_found"])
        self.assertEqual(a["lead_grade"], "C")
        self.assertEqual((b["lead_grade"], b["lead_found"]), ("B", ""))
        self.assertNotIn("built on", b["flags"])
        self.assert_never_seen()
        self.assertEqual(self.run_with("owner_next_door = false\n"), 0)
        a = self.rows()["14F0001 0001"]
        self.assertEqual((a["lead_grade"], a["lead_found"]), ("B", ""))
        self.assertIn("Same owner, adjacent and built on", a["flags"])   # the flag stays

    def test_digest_lists_leads_and_they_get_briefs(self):
        self.parcels.append(county_parcel("14F0003 0003", C, owner="TEST | OWNER\nGAMMA"))
        settings = self.tmp / "settings.toml"
        settings.write_text((SCREENER / "settings.toml").read_text()
                            .replace("briefs = 25", "briefs = 1", 1))
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["--settings", str(settings), "--out", str(self.out)]), 0)
        text = (self.out / digest.FILE_NAME).read_text()
        self.assertIn("3 of 3 ranked parcels were graded: A 0, B 3, C 0, blocked 0.", text)
        self.assertIn("## Grade B: one item open (3 listed of 3)", text)
        rows = [x for x in text.splitlines() if x.startswith("| ") and "TEST RD" in x]
        self.assertEqual(len(rows), 3)
        self.assertEqual([r.split("|")[1].strip() for r in rows], ["1", "2", "3"])  # rank order
        for r in rows:
            self.assertEqual(r.count("|"), 11)             # county text can't add a column
            self.assertIn("not checked: zoning path not known", r)
            self.assertRegex(r, r"\[brief\]\(briefs/00\d_14F000\d_000\d\.md\)")
        self.assertIn("TEST / OWNER GAMMA (individual)", text)
        # Link, HTML, code and emphasis markup in county text is neutralised.
        hostile = digest._cell("[x](http://evil) <b>`a`</b> *b* _c_ \\| d")
        self.assertEqual(hostile, "(x)(http://evil) (b)'a'(/b) b c // d")
        # briefs = 1, yet every listed lead has its brief and plan.
        names = sorted(f.name for f in (self.out / "briefs").iterdir())
        self.assertEqual(len([n for n in names if n.endswith(".md")]), 3)
        self.assertIn("## How to read this", text)
        self.assertIn(grade.CAVEAT, text)

    def test_digest_max_caps_the_list_and_zero_lists_none(self):
        self.assertEqual(self.run_with("digest_max = 1\n"), 0)
        text = (self.out / digest.FILE_NAME).read_text()
        self.assertIn("## Grade B: one item open (1 listed of 2)", text)
        self.assertEqual(self.run_with("digest_max = 0\n"), 0)
        text = (self.out / digest.FILE_NAME).read_text()
        self.assertIn("(0 listed of 2)", text)
        self.assertIn("None listed.", text)

    def test_bad_grade_settings_stop_before_any_request(self):
        for body in ("enabled = 1\n", "min_frontage_ft = -5\n", "digest_max = 1.5\n"):
            with self.subTest(body=body):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    self.run_with(body)
                self.assertEqual(e.exception.code, 2)
                self.assertEqual(self.session.calls, [])

    def test_old_settings_without_the_table(self):
        settings = self.tmp / "settings.toml"
        settings.write_text((SCREENER / "settings.toml").read_text().split("[grade]", 1)[0]
                            .replace("slope_cache_days", "# slope_cache_days", 1))
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["--settings", str(settings), "--out", str(self.out)]), 0)
        self.assertEqual(self.rows()["14F0001 0001"]["lead_grade"], "B")
        self.assertEqual(checks.slope_cache_days, checks.SLOPE_CACHE_DAYS)

    def test_runs_are_deterministic(self):
        fresh = copy.deepcopy(self.parcels)
        self.main()
        first = [(self.out / n).read_bytes() for n in ("ranked.csv", digest.FILE_NAME)]
        self.parcels[:] = fresh
        self.main()
        self.assertEqual([(self.out / n).read_bytes()
                          for n in ("ranked.csv", digest.FILE_NAME)], first)

    def test_readme_and_settings_name_it(self):
        text = (SCREENER / "README.md").read_text()
        for words in ("lead_grade", "lead_found", "lead_not_checked", "[grade]", "leads.md",
                      "facts.json", "decision_facts.jsonl", "slope_cache_days",
                      "--refresh-slope", "not graded"):
            self.assertIn(words, text)
        settings = (SCREENER / "settings.toml").read_text()
        for key in ("min_frontage_ft", "max_hydric_pct", "max_poorly_drained_pct",
                    "owner_next_door", "recent_purchase_years", "digest_max",
                    "slope_cache_days"):
            self.assertIn(key, settings)


class StaleBriefTests(RunHarness):
    def test_a_rerun_removes_only_what_the_earlier_run_wrote(self):
        self.parcels.append(county_parcel("14F0003 0003", C, owner="TEST OWNER GAMMA"))
        self.main()
        briefs = self.out / "briefs"
        first = sorted(f.name for f in briefs.iterdir())
        self.assertEqual(len(first), 6)
        self.assertEqual(json.loads((self.out / report.BRIEFS_LIST).read_text()), first)
        mine = ["notes.txt", "my plan.md", "2026_site_visit_notes.md", "100_acre_comparison.svg",
                "001_14F0001_0001_my_notes.md"]       # some are named like a brief
        for name in mine:
            (briefs / name).write_text("mine")
        self.parcels[:] = [county_parcel("14F0002 0002", B, owner="TEST OWNER BETA")]
        self.main()
        names = sorted(f.name for f in briefs.iterdir())
        self.assertEqual(names, sorted(["001_14F0002_0002.md", "001_14F0002_0002.svg", *mine]))
        self.assertEqual(json.loads((self.out / report.BRIEFS_LIST).read_text()),
                         ["001_14F0002_0002.md", "001_14F0002_0002.svg"])

    def test_a_tampered_or_missing_list_deletes_nothing_outside_briefs(self):
        self.main()
        outside = self.out / "ranked.csv"
        victim = self.tmp / "victim.txt"
        victim.write_text("keep")
        (self.out / report.BRIEFS_LIST).write_text(json.dumps(
            ["../ranked.csv", "../../victim.txt", "/etc/hostname", "", ".", 7]))
        self.parcels[:] = [county_parcel("14F0002 0002", B, owner="TEST OWNER BETA")]
        self.main()
        self.assertTrue(outside.is_file())
        self.assertEqual(victim.read_text(), "keep")
        (self.out / report.BRIEFS_LIST).write_text("{not json")
        self.main()                                   # unreadable list: nothing removed
        self.assertTrue((self.out / "briefs" / "001_14F0002_0002.md").is_file())


class FactsTests(RunHarness):
    def setUp(self):
        super().setUp()
        self.patch(run, "DECISIONS_FILE", self.tmp / "inputs" / "decisions.csv")

    def decide(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = decide.main([*args, "--run", str(self.out)])
        return code, out.getvalue() + err.getvalue()

    def test_file_shape_and_allow_list(self):
        self.main()
        data = json.loads((self.out / facts.FILE_NAME).read_text())
        self.assertEqual(data["meta"]["records"], 2)
        self.assertEqual(data["meta"]["schema"], facts.SCHEMA)
        self.assertIn("not evaluation features", data["meta"]["kind"])
        snap = json.loads((self.out / "features.json").read_text())
        self.assertEqual(data["meta"]["settings_sha256"], snap["meta"]["settings_sha256"])
        rec = {r["parcel_id"]: r for r in data["records"]}["14F0001 0001"]
        self.assertEqual(sorted(rec), ["facts", "parcel_id"])
        self.assertEqual(sorted(rec["facts"]), sorted(facts.COLUMNS))
        self.assertEqual(rec["facts"]["rank"], 1)
        self.assertEqual(rec["facts"]["lead_grade"], "C")   # RunHarness soils: 25% poorly drained
        self.assertIsInstance(rec["facts"]["lead_found"], list)
        self.assertIsInstance(rec["facts"]["frontage_ft"], int)
        text = (self.out / facts.FILE_NAME).read_text()
        for private in ("TEST OWNER", "1 TEST ST", "TEST RD"):
            self.assertNotIn(private, text)
        self.assertEqual(list(self.out.glob(".facts*")), [])

    def test_failed_rerun_never_pairs_new_ranked_with_old_facts(self):
        self.main()
        self.assertTrue((self.out / facts.FILE_NAME).is_file())
        self.patch(report, "write_all", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
        with self.assertRaises(OSError):
            self.main()
        self.assertFalse((self.out / facts.FILE_NAME).exists())

    def test_decide_keeps_the_facts_in_their_own_store(self):
        self.main()
        code, text = self.decide("14F0001 0001", "pursue", "called owner")
        self.assertEqual(code, 0, text)
        store = self.tmp / "inputs" / facts.STORE_NAME
        [entry] = [json.loads(x) for x in store.read_text().splitlines()]
        self.assertEqual((entry["parcel_id"], entry["decision"]), ("14F0001 0001", "pursue"))
        self.assertEqual(entry["records"][0]["facts"]["lead_grade"], "C")
        features = (self.tmp / "inputs" / decide.STORE_NAME).read_text()
        self.assertNotIn("lead_grade", features)            # the two stores stay apart
        self.assertNotIn("frontage_ft", features)

    def test_decide_without_facts_still_records(self):
        self.main()
        (self.out / facts.FILE_NAME).unlink()
        code, text = self.decide("14F0001 0001", "maybe")
        self.assertEqual(code, 0, text)
        self.assertIn("no report facts kept for this decision", text)
        self.assertFalse((self.tmp / "inputs" / facts.STORE_NAME).exists())
        (self.out / facts.FILE_NAME).write_text("{not json")
        code, text = self.decide("14F0002 0002", "maybe")
        self.assertEqual(code, 0, text)
        self.assertIn("is unreadable", text)
        rows = (self.tmp / "inputs" / "decisions.csv").read_text().splitlines()
        self.assertEqual(len(rows), 3)                      # header + two decisions

    def test_a_facts_failure_never_costs_the_decision(self):
        self.main()
        # NaN is not standard JSON: the store refuses it, the decision is still recorded.
        path = self.out / facts.FILE_NAME
        path.write_text(path.read_text().replace('"rank": 1,', '"rank": NaN,', 1))
        code, text = self.decide("14F0001 0001", "pursue")
        self.assertEqual(code, 0, text)
        self.assertIn("no report facts kept for this decision (could not be written", text)
        self.assertFalse((self.tmp / "inputs" / facts.STORE_NAME).exists())   # no empty file
        # An unwritable store (a folder in its place) does not block it either.
        (self.tmp / "inputs" / facts.STORE_NAME).mkdir()
        path.write_text(path.read_text().replace('"rank": NaN,', '"rank": 1,', 1))
        code, text = self.decide("14F0002 0002", "pass")
        self.assertEqual(code, 0, text)
        self.assertIn("could not be written", text)
        rows = (self.tmp / "inputs" / "decisions.csv").read_text().splitlines()
        self.assertEqual(len(rows), 3)
        features = (self.tmp / "inputs" / decide.STORE_NAME).read_text().splitlines()
        self.assertEqual(len(features), 2)

    def test_store_path_is_ignored_by_git(self):
        import subprocess
        try:
            r = subprocess.run(["git", "-C", str(SCREENER), "check-ignore", "-q",
                                f"inputs/{facts.STORE_NAME}"], timeout=10)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git is unavailable")
        if subprocess.run(["git", "-C", str(SCREENER), "rev-parse"],
                          capture_output=True).returncode != 0:
            self.skipTest("not a git checkout")
        self.assertEqual(r.returncode, 0)


class SlopeCacheTests(Offline):
    """Slope answers live in their own folder with their own lifetime."""

    def ask(self):
        p = {"parcel_id": "P1", "geometry": square(*A)}
        checks.slope_checks([p], workers=1)
        return p

    def test_kept_apart_and_past_the_map_data_lifetime(self):
        self.serve({checks.SLOPE_URL: lambda params: copy.deepcopy(GOOD_SLOPE)})
        self.ask()
        self.assertEqual(list((self.tmp / "http").rglob("*.json")), [])
        [kept] = list((self.tmp / "slope").rglob("*.json"))
        self.assertEqual(kept.parent.parent, checks.slope_cache_dir())
        old = time.time() - 400 * 86400                    # far past cache_days (7)
        os.utime(kept, (old, old))
        self.ask()
        self.assertEqual(self.session.count(checks.SLOPE_URL), 1)   # reused
        # --refresh (cache_days = 0) leaves the slope answers alone.
        self.patch(arcgis, "cache_days", 0)
        self.ask()
        self.assertEqual(self.session.count(checks.SLOPE_URL), 1)

    def test_lifetime_and_refresh_slope(self):
        self.serve({checks.SLOPE_URL: lambda params: copy.deepcopy(GOOD_SLOPE)})
        self.ask()
        [kept] = list((self.tmp / "slope").rglob("*.json"))
        old = time.time() - 40 * 86400
        os.utime(kept, (old, old))
        self.patch(checks, "slope_cache_days", 30.0)
        self.ask()
        self.assertEqual(self.session.count(checks.SLOPE_URL), 2)   # too old: asked again
        self.patch(checks, "slope_cache_days", 0.0)         # what --refresh-slope sets
        self.ask()
        self.assertEqual(self.session.count(checks.SLOPE_URL), 3)
        self.assertEqual(len(list((self.tmp / "slope").rglob("*.json"))), 1)  # and kept again

    def test_refresh_slope_falls_back_on_the_kept_answer_when_the_service_fails(self):
        self.serve({checks.SLOPE_URL: lambda params: copy.deepcopy(GOOD_SLOPE)})
        self.ask()
        self.assertEqual(checks.slope_tally["asked"], 1)
        self.patch(checks, "slope_cache_days", 0.0)         # --refresh-slope
        self.serve({checks.SLOPE_URL: lambda params: FakeResponse(status=503)})
        p = self.ask()
        self.assertNotIn("slope_error", p)                  # screened on the kept reading
        self.assertEqual(p["steep_pct"], 3.3)
        self.assertEqual((checks.slope_tally["fell_back"], checks.slope_tally["asked"],
                          checks.slope_tally["reused"]), (1, 0, 0))
        # With nothing kept, a failing service is still a failed check.
        self.clear_cache()
        self.assertTrue(self.ask().get("slope_error"))

    def test_tally_counts_reused_answers_and_their_oldest_date(self):
        self.serve({checks.SLOPE_URL: lambda params: copy.deepcopy(GOOD_SLOPE)})
        self.ask()
        self.assertEqual((checks.slope_tally["reused"], checks.slope_tally["oldest"]), (0, None))
        [kept] = list((self.tmp / "slope").rglob("*.json"))
        old = time.time() - 30 * 86400
        os.utime(kept, (old, old))
        self.ask()
        self.assertEqual((checks.slope_tally["reused"], checks.slope_tally["asked"]), (1, 0))
        self.assertAlmostEqual(checks.slope_tally["oldest"], old, delta=2)

    def test_bad_answers_are_never_kept(self):
        self.serve({checks.SLOPE_URL: lambda params: {"statistics": []}})
        p = self.ask()
        self.assertTrue(p.get("slope_error"))
        self.assertEqual(self.cached(), [])

    def test_setting(self):
        self.assertEqual(checks.slope_days_setting({}), checks.SLOPE_CACHE_DAYS)
        self.assertEqual(checks.slope_days_setting({"slope_cache_days": 0}), 0.0)
        for bad in (-1, True, "7", float("inf")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                checks.slope_days_setting({"slope_cache_days": bad})


class SlopeCacheRunTests(RunHarness):
    def test_second_run_asks_no_slope_and_refresh_slope_asks_again(self):
        self.main()
        first = self.session.count(checks.SLOPE_URL)
        self.assertEqual(first, 2)
        self.parcels[:] = [county_parcel("14F0001 0001", A),
                           county_parcel("14F0002 0002", B, owner="TEST OWNER BETA")]
        self.main("--refresh")
        self.assertEqual(self.session.count(checks.SLOPE_URL), 0)
        self.assertIn("Slope: 2 of 2 lidar answers were reused from cache/slope (oldest kept "
                      + time.strftime("%Y-%m-%d", time.gmtime()), self.notes())
        self.parcels[:] = [county_parcel("14F0001 0001", A),
                           county_parcel("14F0002 0002", B, owner="TEST OWNER BETA")]
        self.main("--refresh-slope")
        self.assertEqual(self.session.count(checks.SLOPE_URL), 2)

    def test_bad_setting_stops_before_any_request(self):
        settings = self.tmp / "settings.toml"
        settings.write_text((SCREENER / "settings.toml").read_text()
                            .replace("slope_cache_days = 3650", "slope_cache_days = -1", 1))
        self.serve(self.routes, self.downloads)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            run.main(["--settings", str(settings), "--out", str(self.out)])
        self.assertEqual(e.exception.code, 2)
        self.assertEqual(self.session.calls, [])


if __name__ == "__main__":
    unittest.main()
