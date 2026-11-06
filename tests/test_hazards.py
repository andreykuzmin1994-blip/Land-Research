"""hazards.py: the H4/H6 decision, the storage gate, texts, boundaries
(reviews/26_flood_wetlands). Offline (SR-6)."""

from __future__ import annotations

import ast
import re
import unittest
from unittest import mock

import tests  # noqa: F401  (suite guards)
import hazards
import prepare
from hazards_helpers import enabled, row
from zoning_helpers import REPO_ROOT

SRC = (REPO_ROOT / "hazards.py").read_text(encoding="utf-8")


def thaw(v):
    if hasattr(v, "items"):
        return {k: thaw(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [thaw(x) for x in v]
    return v


def params(**hf):
    p = thaw(prepare.get_parameters())
    p["hard_filters"] = dict(p["hard_filters"], **hf)
    return p


def zmap(*items):
    return {k: v for k, v in items}


class GateTests(unittest.TestCase):
    """FW-03 / FW-06: the gate is a human-owned constant, costing no SQL."""

    def test_off_in_the_shipped_repo(self):
        self.assertFalse(hasattr(prepare, hazards.GATE_CONSTANT))
        self.assertFalse(hazards.enabled())
        self.assertEqual(hazards.schema_problem(), "no hazard table in prepare.py")

    def test_on_only_with_the_constant(self):
        with enabled():
            self.assertTrue(hazards.enabled())
            self.assertIsNone(hazards.schema_problem())
        with enabled("CREATE TABLE IF NOT EXISTS parcel_hazards (parcel_id TEXT)"):
            self.assertTrue(hazards.enabled())
            self.assertIn("lacks", hazards.schema_problem())
        self.assertFalse(hazards.enabled())

    def test_schema_mismatch_is_a_gap_without_sql(self):
        class Boom:
            def cursor(self):
                raise AssertionError("no SQL with a mismatched schema")
        with enabled("CREATE TABLE parcel_hazards (parcel_id TEXT)"):
            got = hazards.assess_parcels(Boom(), ["a"])
        self.assertEqual(got["a"].h4, hazards.GAP)
        self.assertIsNone(got["a"].blocker)

    def test_proposal_doc_carries_the_same_ddl(self):
        doc = (REPO_ROOT / "reviews" / "26_flood_wetlands" /
               "01_prepare_mutation_proposal.md").read_text(encoding="utf-8")
        self.assertIn(hazards.PROPOSED_DDL.strip(), doc)
        for s in hazards.PROPOSED_INDEXES:
            self.assertIn(s, doc)
        self.assertIn("_DDL_PARCEL_HAZARDS", doc)
        self.assertIn("parcel_id TEXT PRIMARY KEY REFERENCES parcels(parcel_id) ON DELETE CASCADE",
                      hazards.PROPOSED_DDL)
        self.assertIn("CREATE TABLE IF NOT EXISTS", hazards.PROPOSED_DDL)
        for c in hazards.COLUMNS:
            self.assertRegex(hazards.PROPOSED_DDL, rf"\n    {c} ", c)


class ClassifyTests(unittest.TestCase):
    """FW-08: every FLD_ZONE / ZONE_SUBTY / SFHA_TF combination of the NFHL
    domain tables (FIRM Database Technical Reference), table-driven."""

    CASES = [
        ("A", "", "T", hazards.SFHA), ("AE", "", "T", hazards.SFHA),
        ("AE", "FLOODWAY", "T", hazards.FLOODWAY),
        ("AE", "FLOODWAY CONTAINED IN CHANNEL", "T", hazards.FLOODWAY),
        ("AE", "ADMINISTRATIVE FLOODWAY", "T", hazards.FLOODWAY),
        ("AE", "RIVERINE FLOODWAY SHOWN IN COASTAL ZONE", "T", hazards.FLOODWAY),
        ("AE", "COMMUNITY ENCROACHMENT AREA", "T", hazards.SFHA),
        ("AE", "1 PCT CONTAINED IN CHANNEL", "T", hazards.SFHA),
        ("AH", "", "T", hazards.SFHA), ("AO", "", "T", hazards.SFHA),
        ("A99", "", "T", hazards.SFHA), ("AR", "", "T", hazards.SFHA),
        ("V", "", "T", hazards.SFHA), ("VE", "", "T", hazards.SFHA),
        ("X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD", "F", hazards.X500),
        ("X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD CONTAINED IN CHANNEL", "F", hazards.X500),
        ("X", "AREA OF MINIMAL FLOOD HAZARD", "F", hazards.MINIMAL),
        ("X", "1 PCT FUTURE CONDITIONS", "F", hazards.MINIMAL),
        ("X", "AREA WITH REDUCED FLOOD RISK DUE TO LEVEE", "F", hazards.MINIMAL),
        ("D", "AREA OF UNDETERMINED FLOOD HAZARD", "F", hazards.UNDETERMINED),
        ("AREA NOT INCLUDED", "", "F", hazards.UNDETERMINED),
        ("", "", "", hazards.UNDETERMINED),
        ("OPEN WATER", "", "F", hazards.OPEN_WATER),
        (" ae ", " floodway ", "t", hazards.FLOODWAY),
        ("ae", None, "T", hazards.SFHA),
        (None, None, None, hazards.UNDETERMINED),
    ]

    def test_table(self):
        for z, s, t, want in self.CASES:
            self.assertEqual(hazards.classify_nfhl(z, s, t), want, (z, s, t))

    def test_zone_key_normalised(self):
        self.assertEqual(hazards.zone_key(" ae ", "floodway", "t"), "AE|FLOODWAY|T")
        self.assertEqual(hazards.zone_key("X", None, "F"), "X||F")


class FloodDecisionTests(unittest.TestCase):
    """FW-08 / FW-09 / FW-10: the H4 reading of program.md."""

    def a(self, p=None, **kw):
        return hazards.assess_row(row(**kw), p or prepare.get_parameters())

    def test_clear_x_passes(self):
        a = self.a()
        self.assertEqual(a.h4, hazards.PASS)
        self.assertIsNone(a.blocker)
        self.assertIn("LOMA not checked", a.flood_line)
        self.assertIn("not an official determination", a.flood_line)

    def test_ae_blocks_through_gate_4_text(self):
        a = self.a(sfha_sf=5000, nfhl_zone_sf=zmap(("AE||T", 5000), ("X|AREA OF MINIMAL FLOOD HAZARD|F", 95000)),
                   zones=["AE", "X"])
        self.assertEqual(a.h4, hazards.BLOCK)
        self.assertTrue(a.blocker.startswith("H4: FEMA SFHA zone AE"))
        self.assertIn("LOMA not checked", a.blocker)

    def test_lowercase_and_padded_zones_block(self):
        a = self.a(sfha_sf=5000, nfhl_zone_sf=zmap((" ae ||T", 5000)))
        self.assertEqual(a.h4, hazards.BLOCK)

    def test_zone_a_blocks(self):
        a = self.a(sfha_sf=5000, nfhl_zone_sf=zmap(("A||T", 5000)))
        self.assertIn("zone A on parcel", a.blocker)

    def test_floodway_always_blocks_even_with_lomr(self):
        a = self.a(sfha_sf=5000, floodway_sf=2000, lomr_status="intersects",
                   nfhl_zone_sf=zmap(("AE||T", 3000), ("AE|FLOODWAY|T", 2000)))
        self.assertEqual(a.h4, hazards.BLOCK)
        self.assertIn("regulatory floodway", a.blocker)
        self.assertNotIn("zone AE on parcel (flood", a.blocker)

    def test_ae_with_lomr_is_a_review_pass(self):
        a = self.a(sfha_sf=5000, lomr_status="intersects", nfhl_zone_sf=zmap(("AE||T", 5000)))
        self.assertEqual(a.h4, hazards.REVIEW)
        self.assertIsNone(a.blocker)
        self.assertTrue(any("LOMR" in r and "LOMA not checked" in r for r in a.reviews))

    def test_ae_without_lomr_data_blocks(self):
        for st in ("none", "not_read", None):
            a = self.a(sfha_sf=5000, lomr_status=st, nfhl_zone_sf=zmap(("AE||T", 5000)))
            self.assertEqual(a.h4, hazards.BLOCK, st)

    def test_a_is_not_relaxed_by_the_ae_token(self):
        a = self.a(sfha_sf=5000, lomr_status="intersects",
                   nfhl_zone_sf=zmap(("A||T", 2000), ("AE||T", 3000)))
        self.assertEqual(a.h4, hazards.BLOCK)
        self.assertIn("zone A on parcel", a.blocker)
        self.assertTrue(any("zone AE on parcel with an intersecting LOMR" in r for r in a.reviews))

    def test_other_sfha_zones_are_reviews_not_blocks(self):
        for z in ("AH", "AO", "A99", "AR", "V", "VE"):
            a = self.a(sfha_sf=5000, nfhl_zone_sf=zmap((f"{z}||T", 5000)))
            self.assertEqual(a.h4, hazards.REVIEW, z)
            self.assertIsNone(a.blocker, z)

    def test_future_conditions_levee_open_water_are_reviews(self):
        for key in ("X|1 PCT FUTURE CONDITIONS|F",
                    "X|AREA WITH REDUCED FLOOD RISK DUE TO LEVEE|F", "OPEN WATER||F"):
            a = self.a(nfhl_zone_sf=zmap((key, 5000), ("X|AREA OF MINIMAL FLOOD HAZARD|F", 95000)))
            self.assertEqual(a.h4, hazards.REVIEW, key)

    def test_x500_passes(self):
        a = self.a(x500_sf=30000, nfhl_zone_sf=zmap(("X|0.2 PCT ANNUAL CHANCE FLOOD HAZARD|F", 30000),
                                                     ("X|AREA OF MINIMAL FLOOD HAZARD|F", 70000)))
        self.assertEqual(a.h4, hazards.PASS)

    def test_unmapped_and_undetermined_are_gaps(self):
        a = self.a(nfhl_mapped_sf=0, nfhl_zone_sf={})
        self.assertEqual(a.h4, hazards.GAP)
        self.assertIn("unmapped, not clear", a.data_gaps[0])
        a = self.a(nfhl_mapped_sf=60000, nfhl_undetermined_sf=40000,
                   nfhl_zone_sf=zmap(("D|AREA OF UNDETERMINED FLOOD HAZARD|F", 40000)))
        self.assertEqual(a.h4, hazards.GAP)
        a = self.a(nfhl_mapped_sf=99990)
        self.assertEqual(a.h4, hazards.GAP)
        self.assertIsNone(a.blocker)

    def test_noise_floor_is_one_square_foot(self):
        a = self.a(sfha_sf=0.5, nfhl_zone_sf=zmap(("AE||T", 0.5)))
        self.assertEqual(a.h4, hazards.PASS)
        a = self.a(sfha_sf=1.5, nfhl_zone_sf=zmap(("AE||T", 1.5)))
        self.assertEqual(a.h4, hazards.BLOCK)
        self.assertEqual(hazards.NOISE_FLOOR_SF, 1.0)

    def test_blocked_list_comes_from_params(self):
        r = dict(sfha_sf=5000, nfhl_zone_sf=zmap(("AE||T", 5000)))
        self.assertEqual(self.a(params(flood_zones_blocked=["A"]), **r).h4, hazards.REVIEW)
        self.assertEqual(self.a(params(flood_zones_blocked=["A", "AE"],
                                       flood_zones_flag_for_review=[]),
                                lomr_status="intersects", **r).h4, hazards.BLOCK)

    def test_bfe_sentinel_dropped(self):
        self.assertIn("BFE —", self.a(bfe_ft=-9999).flood_line)
        self.assertIn("BFE 812.5 ft", self.a(bfe_ft=812.5).flood_line)


class WetlandDecisionTests(unittest.TestCase):
    """FW-07 / FW-15: strict > threshold, exact, threshold from params."""

    def a(self, wet, p=None, parcel=100000, **kw):
        return hazards.assess_row(row(wetland_sf=wet, nwi_parcel_sf=parcel, **kw),
                                  p or prepare.get_parameters())

    def test_bands(self):
        self.assertEqual(self.a(0).h6, hazards.PASS)
        self.assertIn("none:", self.a(0).wetlands_line)
        a5 = self.a(5000)
        self.assertEqual(a5.h6, hazards.REVIEW)
        self.assertIsNone(a5.blocker)
        self.assertIn("minor fringe", a5.wetlands_line)
        self.assertTrue(any("minimum footprint undefined" in r for r in a5.reviews))
        self.assertEqual(self.a(20000).h6, hazards.REVIEW)
        a25 = self.a(25000)
        self.assertEqual(a25.h6, hazards.FAIL)
        self.assertTrue(a25.blocker.startswith("H6: NWI-mapped wetlands cover more than 20%"))
        self.assertIn("significant", a25.wetlands_line)
        self.assertIn("not a jurisdictional delineation", a25.wetlands_line)

    def test_boundary_exact(self):
        self.assertEqual(self.a(20.0, parcel=100.0).h6, hazards.REVIEW)       # 20.00% passes
        self.assertEqual(self.a(20.01, parcel=100.0).h6, hazards.FAIL)        # 20.01% fails
        self.assertEqual(self.a(19996, parcel=99980).h6, hazards.REVIEW)      # exactly 20%
        self.assertEqual(self.a(19997, parcel=99980).h6, hazards.FAIL)        # 20.001%
        self.assertFalse(hazards.exceeds_pct(20, 100, 20))
        self.assertTrue(hazards.exceeds_pct("20.0001", 100, 20))
        self.assertFalse(hazards.exceeds_pct(0, 100, 20))
        self.assertFalse(hazards.exceeds_pct(10, 0, 20))

    def test_threshold_from_params_not_a_literal(self):
        self.assertEqual(self.a(15000).h6, hazards.REVIEW)
        self.assertEqual(self.a(15000, params(wetlands_max_pct_of_parcel=10)).h6, hazards.FAIL)
        self.assertIn("more than 10%", self.a(15000, params(wetlands_max_pct_of_parcel=10)).blocker)

    def test_deepwater_and_excavated_flag(self):
        a = self.a(8000, wetland_deep_excavated_sf=6000,
                   wetland_type_sf={"Freshwater Pond": 8000})
        self.assertTrue(any("deepwater or excavated" in r for r in a.reviews))
        a = self.a(8000, wetland_deep_excavated_sf=2000)
        self.assertFalse(any("deepwater or excavated" in r for r in a.reviews))
        self.assertTrue(hazards.is_excavated("PUBHx"))
        self.assertTrue(hazards.is_excavated("L1UBHhx"))
        self.assertFalse(hazards.is_excavated("PFO1A"))
        self.assertFalse(hazards.is_excavated("R2UBH"))
        self.assertTrue(hazards.is_deepwater(" Riverine"))
        self.assertTrue(hazards.is_deepwater("Lake"))
        self.assertFalse(hazards.is_deepwater("Freshwater Emergent Wetland"))

    def test_types_listed_sorted(self):
        a = self.a(8000, wetland_type_sf={"Riverine": 3000, "Freshwater Emergent Wetland": 5000})
        self.assertIn("(Freshwater Emergent Wetland 5.00%, Riverine 3.00%)", a.wetlands_line)


class StateTests(unittest.TestCase):
    """FW-04: absent, stale, fresh, unreadable."""

    def test_absent_and_stale_are_gap_passes(self):
        p = prepare.get_parameters()
        a = hazards.assess_row(row(nfhl_md5=None, nfhl_fetched_at=None, nwi_md5=None,
                                   nwi_fetched_at=None, sfha_sf=9e9), p)
        self.assertEqual((a.flood_state, a.wetland_state), (hazards.ABSENT, hazards.ABSENT))
        self.assertIsNone(a.blocker)
        self.assertEqual(a.data_gaps, (
            "H4 flood: no hazards import for this parcel (FEMA NFHL not assessed)",
            "H6 wetlands: no hazards import for this parcel (USFWS NWI not assessed)"))
        a = hazards.assess_row(row(geom_md5="new", sfha_sf=9e9, wetland_sf=9e9,
                                   nfhl_zone_sf={"AE||T": 9e9}), p)
        self.assertEqual((a.flood_state, a.wetland_state), (hazards.STALE, hazards.STALE))
        self.assertIsNone(a.blocker)
        self.assertTrue(all("stale" in g for g in a.data_gaps))
        self.assertIn("— (hazard data stale", a.flood_line)

    def test_per_source_states(self):
        a = hazards.assess_row(row(nwi_md5="old", sfha_sf=5000,
                                   nfhl_zone_sf={"AE||T": 5000}), prepare.get_parameters())
        self.assertEqual(a.h4, hazards.BLOCK)
        self.assertEqual(a.h6, hazards.GAP)

    def test_read_failure_is_a_gap_pass(self):
        class Conn:
            def cursor(self):
                raise RuntimeError("relation does not exist")
        with enabled():
            got = hazards.assess_parcels(Conn(), ["b", "a"])
        self.assertEqual(sorted(got), ["a", "b"])
        self.assertTrue(all(v.flood_state == hazards.UNREADABLE and v.blocker is None
                            for v in got.values()))

    def test_flag_texts_stable(self):
        # FW-17 / FW-24: no timestamps, no float shares in flag text.
        p = prepare.get_parameters()
        texts = []
        for kw in (dict(sfha_sf=5000.123, nfhl_zone_sf={"AE||T": 5000.123}),
                   dict(wetland_sf=7777.7), dict(wetland_sf=25000.5),
                   dict(nfhl_mapped_sf=0)):
            a = hazards.assess_row(row(**kw), p)
            texts += list(a.data_gaps) + list(a.reviews) + [a.blocker or ""]
        for t in texts:
            self.assertNotRegex(t, r"\d{4}-\d{2}-\d{2}|\d+\.\d{2,}|5000|7777")

    def test_multipart_note(self):
        a = hazards.assess_row(row(multipart=True), prepare.get_parameters())
        self.assertIn("largest outer ring", a.flood_line)
        self.assertIn("largest outer ring", a.wetlands_line)


class TextTests(unittest.TestCase):
    def test_deduction_texts(self):
        f = hazards.FRESH
        self.assertEqual(hazards.deduction_text(f, f),
                         "SFHA incl. floodway deducted (0.2% zone not deducted); NWI wetlands "
                         "deducted; fill/compensatory storage not considered")
        self.assertTrue(hazards.deduction_text("absent", "stale").startswith(
            "flood/wetlands not deducted ("))
        self.assertIn("wetlands not deducted", hazards.deduction_text(f, "absent"))


class BoundaryTests(unittest.TestCase):
    """FW-05 / FW-11 / FW-15: imports, units, no site_fit in the H6 path."""

    TREE = ast.parse(SRC)

    def test_imports_stdlib_and_prepare_only(self):
        mods = set()
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0])
        self.assertEqual(mods - {"__future__", "json", "dataclasses", "fractions", "typing",
                                 "prepare"}, set())

    def test_no_mercator_no_service_area_fields(self):
        consts = [n.value for n in ast.walk(self.TREE)
                  if isinstance(n, ast.Constant) and isinstance(n.value, (str, int))]
        for bad in (3857, 900913, "3857", "900913"):
            self.assertNotIn(bad, consts)
        self.assertNotRegex(SRC, r"3857|900913|102100")
        doc = self.TREE.body[0].value.value
        for c in consts:
            if isinstance(c, str) and c != doc:
                self.assertNotRegex(c, r"Shape_Area|Shape_Length|\bACRES\b")
        # FW-15: no site_fit (and no draft zoning) anywhere in the H6 decision.
        names = {n.id for n in ast.walk(self.TREE) if isinstance(n, ast.Name)}
        self.assertFalse(names & {"site_fit", "zoning", "research", "runner"})
        self.assertEqual(hazards.SRID_FEET, 2240)

    def test_sql_is_constant_and_read_only(self):
        sqls = [v for k, v in vars(hazards).items() if k.startswith("_SQL_")]
        self.assertTrue(sqls)
        for s in sqls:
            self.assertNotRegex(s.upper(), r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER)\b")
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "execute":
                self.assertIsInstance(node.args[0], ast.Name)

    def test_load_never_raises(self):
        import importlib
        import sys
        saved = sys.modules.pop("hazards")
        try:
            with mock.patch.object(prepare, "get_parameters", side_effect=AssertionError):
                importlib.import_module("hazards")
        finally:
            sys.modules["hazards"] = saved


if __name__ == "__main__":
    unittest.main()
