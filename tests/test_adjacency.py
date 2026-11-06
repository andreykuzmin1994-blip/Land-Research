"""adjacency.py: the storage gate, the buffer-key allow-list and composition,
neighbour classification, the per-parcel plan and its texts
(reviews/27_site_fit_buffers). Offline (SR-6)."""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  (suite guards)
import adjacency as adj
import prepare
import zoning
from adjacency_helpers import LITERAL, MAX, chain, enabled, row, with_classes
from zoning_helpers import REPO_ROOT, patch_rules, repo_rules, stored, verified

SF = stored("south_fulton", "south_fulton_zoning", "M-1")
FID = stored("fid", "fulton_layer34_zoning", "M-1A")
FB1 = stored("fairburn", "fairburn_zoning", "M-1")
FB2 = stored("fairburn", "fairburn_zoning", "M-2")
UC = stored("union_city", "union_city_zoning", "M-1")
ATL = stored("atlanta", "atlanta_zoning", "I-1")


def dims(j: str, d: str):
    return repo_rules()["jurisdictions"][j]["districts"][d]["dimensions"]


def raw(j: str, d: str):
    return dims(j, d)["residential_adjacency_buffer_ft"]["value"]


class GateTests(unittest.TestCase):
    """BUF-03 / BUF-18: source-text gate, no SQL, the proposal doc."""

    def test_off_in_the_repo(self):
        self.assertFalse(adj.enabled())
        self.assertEqual(adj.schema_problem(), "no adjacency table in prepare.py")
        self.assertIsNone(adj.ddl_from_prepare_source())

    def test_runtime_assignment_cannot_switch_it(self):
        with mock.patch.object(prepare, adj.GATE_CONSTANT, adj.PROPOSED_DDL, create=True):
            self.assertFalse(adj.enabled())

    def test_source_text_reader(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "prepare.py"
            p.write_text("_DDL_PARCEL_ADJACENCY = (\"CREATE TABLE IF NOT EXISTS \"\n"
                         "    \"parcel_adjacency (x int)\")\n", encoding="utf-8")
            self.assertEqual(adj.ddl_from_prepare_source(p),
                             "CREATE TABLE IF NOT EXISTS parcel_adjacency (x int)")
            p.write_text("def f():\n    _DDL_PARCEL_ADJACENCY = 'parcel_adjacency'\n")
            self.assertIsNone(adj.ddl_from_prepare_source(p))
            p.write_text("not python (")
            self.assertIsNone(adj.ddl_from_prepare_source(p))

    def test_schema_problem(self):
        with enabled():
            self.assertTrue(adj.enabled())
            self.assertIsNone(adj.schema_problem())
        with enabled("CREATE TABLE parcel_adjacency (parcel_id TEXT)"):
            self.assertIn("lacks", adj.schema_problem())
        with enabled(adj.PROPOSED_DDL.replace("MultiLineString, 2240", "MultiLineString, 4326")):
            self.assertIn("MultiLineString, 2240", adj.schema_problem())

    def test_ddl_shape(self):
        ddl = adj.PROPOSED_DDL
        for needle in ("CREATE TABLE IF NOT EXISTS parcel_adjacency",
                       "REFERENCES parcels(parcel_id) ON DELETE CASCADE",
                       "segment geometry(MultiLineString, 2240) NOT NULL CHECK (ST_SRID(segment) = 2240)",
                       "neighbour_kind IN ('zoned', 'row', 'uncovered')",
                       "parcel_geom_md5 TEXT NOT NULL", "fetched_at TIMESTAMPTZ NOT NULL"):
            self.assertIn(needle, ddl)
        self.assertNotIn("ALTER", ddl.upper())
        self.assertTrue(all("IF NOT EXISTS" in s for s in adj.PROPOSED_INDEXES))
        self.assertIn("USING GIST(segment)", adj.PROPOSED_INDEXES[1])
        for c in adj.COLUMNS:
            self.assertIn(c, ddl)

    def test_proposal_doc_carries_the_same_ddl(self):
        doc = (REPO_ROOT / "reviews" / "27_site_fit_buffers" /
               "01_prepare_mutation_proposal.md").read_text(encoding="utf-8")
        self.assertIn(adj.PROPOSED_DDL.strip(), doc)
        for s in adj.PROPOSED_INDEXES:
            self.assertIn(s, doc)
        self.assertIn(adj.GATE_CONSTANT, doc)
        self.assertIn("neighbour_classes", doc)

    def test_prepare_has_no_adjacency_table(self):
        src = (REPO_ROOT / "prepare.py").read_text(encoding="utf-8")
        self.assertNotIn("parcel_adjacency", src)
        self.assertNotIn(adj.GATE_CONSTANT, src)


class CompositionTests(unittest.TestCase):
    """BUF-02 / BUF-06 / BUF-07: one table-driven case per jurisdiction with
    the REAL rules values, the frontage-unknown rule, unknown keys."""

    CASES = (
        # (rules table, district, class, side, rear, effective, uniform setback, per side)
        ("south_fulton", "M-1", adj.RESIDENTIAL, 60.0, 110.0, 110.0, 40.0, 110.0),
        ("south_fulton", "M-2", adj.AGRICULTURAL, 60.0, 110.0, 110.0, 40.0, 110.0),
        ("fid", "M-1", adj.RESIDENTIAL, 60.0, 110.0, 110.0, 40.0, 110.0),
        ("union_city", "M-1", adj.RESIDENTIAL, 95.0, 95.0, 95.0, 50.0, 95.0),
        ("union_city", "M-1", adj.AGRICULTURAL, 95.0, 95.0, 95.0, 50.0, 95.0),
        ("fairburn", "M-1", adj.RESIDENTIAL, 50.0, 50.0, 50.0, 35.0, 50.0),
        ("fairburn", "M-2", adj.OTHER_DISTRICT, 50.0, 50.0, 50.0, 40.0, 50.0),
        ("fairburn", "M-1", adj.ROW, 25.0, 25.0, 25.0, 35.0, 35.0),
        ("atlanta", "I-1", adj.RESIDENTIAL, 20.0, 20.0, 20.0, 40.0, 40.0),
    )

    def test_table(self):
        for table, d, cls, side, rear, eff, setback, per_side in self.CASES:
            t = adj.buffer_terms(table, raw(table, d))
            with self.subTest(table=table, d=d, cls=cls):
                self.assertEqual(adj.component_distance(t, cls, adj.SIDE), side)
                self.assertEqual(adj.component_distance(t, cls, adj.REAR), rear)
                self.assertEqual(adj.effective_distance(t, {cls})[0], eff)
                self.assertEqual(adj.side_distance(t, {cls}, setback), per_side)

    def test_fairburn_not_additive_to_yards(self):
        # Sec. 80-369: buffers may be counted within required yards: max,
        # never 35 + 50.
        t = adj.buffer_terms("fairburn", raw("fairburn", "M-1"))
        self.assertEqual(adj.side_distance(t, {adj.RESIDENTIAL, adj.OTHER_DISTRICT}, 35.0), 50.0)
        self.assertFalse(any(b.role == adj.ADDITIVE for b, _ in t.terms))

    def test_union_city_and_south_fulton_additive(self):
        t = adj.buffer_terms("union_city", raw("union_city", "M-1"))
        self.assertEqual(adj.effective_distance(t, {adj.RESIDENTIAL}), (95.0, adj.RESIDENTIAL))
        t = adj.buffer_terms("south_fulton", raw("south_fulton", "M-1"))
        self.assertEqual(adj.effective_distance(t, {adj.RESIDENTIAL})[0], 100.0 + 10.0)

    def test_atlanta_screen_is_named_not_applied(self):
        t = adj.buffer_terms("atlanta", raw("atlanta", "I-1"))
        self.assertEqual(len(t.ignored), 1)
        self.assertIn("screen", t.ignored[0])
        self.assertIn("not a distance", t.ignored[0])

    def test_unknown_key_and_string_value_are_noops_with_a_note(self):
        t = adj.buffer_terms("south_fulton", {"frontage_buffer": 300, "side": "50 ft"})
        self.assertEqual(t.terms, ())
        self.assertEqual(len(t.ignored), 2)
        self.assertTrue(any("key not in the adjacency allow-list" in x for x in t.ignored))
        self.assertTrue(any("not a distance" in x for x in t.ignored))
        self.assertEqual(adj.side_distance(t, {adj.RESIDENTIAL}, 40.0), 40.0)
        t = adj.buffer_terms("south_fulton", {"side": True, "rear": float("nan")})
        self.assertEqual(t.terms, ())
        # a key allow-listed for one table is not read for another
        t = adj.buffer_terms("atlanta", {"plus_improvement_setback": 10})
        self.assertEqual(t.terms, ())

    def test_buffers_never_relax_the_setback(self):
        for table, d, cls, *_ in self.CASES:
            t = adj.buffer_terms(table, raw(table, d))
            for s in (0.0, 10.0, 40.0, 500.0):
                self.assertGreaterEqual(adj.side_distance(t, {cls}, s), s)
                self.assertGreaterEqual(adj.side_distance(t, set(), s), s)

    def test_every_allow_list_entry_cites_rule_text_from_the_file(self):
        rules_text = (REPO_ROOT / "zoning_rules.json").read_text(encoding="utf-8")
        self.assertTrue(adj.BUFFER_KEYS)
        for (table, key), b in adj.BUFFER_KEYS.items():
            self.assertIn(table, repo_rules()["jurisdictions"])
            self.assertTrue(any(key in (raw(table, d) or {}) for d in
                                repo_rules()["jurisdictions"][table]["districts"]
                                if isinstance(dims(table, d).get(
                                    "residential_adjacency_buffer_ft", {}).get("value"), dict)),
                            (table, key))
            quotes = re.findall(r"'([^']{12,})'", b.cite)
            for q in quotes:
                self.assertIn(q, rules_text, (table, key, q))


class ClassificationTests(unittest.TestCase):
    """BUF-01 / BUF-13: classes only from the human table; identities."""

    def test_no_table_in_the_real_rules(self):
        for j in repo_rules()["jurisdictions"]:
            self.assertNotIn("neighbour_classes", repo_rules()["jurisdictions"][j])
        cls, why = adj.classify_neighbour([("south_fulton", "R-3")])
        self.assertIsNone(cls)
        self.assertEqual(why, adj.CLASS_TABLE_MISSING)

    def test_table_classifies_literally(self):
        rules = verified("south_fulton", mutate=with_classes(
            "south_fulton", residential=["R-*", "TR"], agricultural=["AG-1"],
            non_residential=["M-1", "M-2", "C-1"]))
        with patch_rules(rules):
            self.assertEqual(adj.classify_neighbour([("south_fulton", "R-3")])[0],
                             adj.RESIDENTIAL)
            self.assertEqual(adj.classify_neighbour([("south_fulton", "AG-1")])[0],
                             adj.AGRICULTURAL)
            self.assertEqual(adj.classify_neighbour([("south_fulton", "M1")])[0],
                             adj.NON_RESIDENTIAL)
            self.assertIsNone(adj.classify_neighbour([("south_fulton", "RM")])[0])
            self.assertIsNone(adj.classify_neighbour([("south_fulton", "CUP")])[0])
            self.assertEqual(adj.classify_neighbour([("south_fulton", "CUP")])[1],
                             "neighbour district not classified in the rules")

    def test_prefix_entries_ignore_hyphens(self):
        """Review finding 11: 'R-*' matches 'R4' as it matches 'R-4'; a hyphen
        ending the prefix still ends the letters (never 'RM', 'RG-3')."""
        e = frozenset({"R-*"})
        for code, want in (("R-4", True), ("R4", True), ("R-4A", True), ("RM", False),
                           ("RG-3", False), ("RG3", False), ("M-1", False)):
            self.assertEqual(adj._matches(code, e), want, code)
        self.assertTrue(adj._matches("RG3", frozenset({"RG-*"})))
        self.assertTrue(adj._matches("MR-3", frozenset({"MR*"})))
        self.assertTrue(adj._matches("M1A", frozenset({"M-1*"})))

    def test_malformed_table(self):
        def bad(data):
            data["jurisdictions"]["south_fulton"]["neighbour_classes"] = {"residential": "R-3"}
        with patch_rules(verified("south_fulton", mutate=bad)):
            self.assertEqual(adj.classify_neighbour([("south_fulton", "R-3")]),
                             (None, "neighbour classification malformed in the rules"))

    def test_identity_follows_zoning(self):
        self.assertEqual(adj.identity("fid", "M-1A"), ("south_fulton", "M-1"))
        self.assertEqual(adj.identity("atlanta", "I-1-C"), ("atlanta", "I-1"))
        self.assertEqual(adj.identity("fairburn", " m-1 "), ("fairburn", "M-1"))
        self.assertIsNone(adj.identity("fairburn", None))
        d = zoning.district_for_stored(*FID, "x")
        self.assertTrue(adj.same_district(adj.identity("fid", "M-1A"),
                                          (d.table_jurisdiction, d.district)))
        self.assertTrue(adj.same_district(("fairburn", "M-1"), ("fairburn", "M1")))
        self.assertFalse(adj.same_district(("fairburn", "M-1"), ("fairburn", "M-2")))
        self.assertFalse(adj.same_district(("fairburn", "M-1"), ("south_fulton", "M-1")))

    def test_strictest_class_across_identities(self):
        rules = verified("south_fulton", mutate=chain(
            with_classes("south_fulton", residential=["R-3"]),
            with_classes("fid", non_residential=["R-3"])))
        with patch_rules(rules):
            self.assertEqual(adj.classify_neighbour([("fid", "R-3"), ("south_fulton", "R-3")])[0],
                             adj.RESIDENTIAL)


def plan(stored_, rows, setback, rules=None):
    if rules is None:
        return adj.plan_parcel("a", stored_, rows, setback)
    with patch_rules(rules):
        return adj.plan_parcel("a", stored_, rows, setback)


SF_CLASSES = verified("south_fulton", mutate=with_classes(
    "south_fulton", residential=["R-*"], agricultural=["AG-1"], non_residential=["M-2"],
    readings=MAX))
FB_LITERAL = verified("fairburn", mutate=with_classes("fairburn", readings=LITERAL))


class PlanTests(unittest.TestCase):
    """The per-parcel plan: what is deducted and what is said."""

    def test_real_rules_apply_no_residential_buffer(self):
        p = plan(SF, [row(1, district="R-3"), row(2, district="M-1")], 40.0)
        self.assertEqual(p.deductions, ())
        self.assertIn("none applied", p.summary)
        self.assertIn(f"residential/agricultural buffer not applied on 1 side "
                      f"({adj.CLASS_TABLE_MISSING})", p.lines)

    def test_class_table_applies_rear_value(self):
        p = plan(SF, [row(1, district="R-3"), row(2, district="M-1")], 40.0, SF_CLASSES)
        self.assertEqual(p.deductions, ((1, 110.0),))
        self.assertIn("residential 110 ft on 1 side", p.summary)
        self.assertIn(adj.REAR_APPLIED, p.lines)
        self.assertEqual(p.counts["same_district"], 1)
        self.assertEqual((p.counts["residential"], p.counts["other_district"]), (1, 1))
        p = plan(SF, [row(3, district="AG-1")], 40.0, SF_CLASSES)
        self.assertEqual(p.deductions, ((3, 110.0),))
        self.assertIn("agricultural 110 ft on 1 side", p.summary)

    def test_south_fulton_other_district_alone_deducts_nothing(self):
        p = plan(SF, [row(1, district="M-2")], 40.0, SF_CLASSES)
        self.assertEqual(p.deductions, ())
        self.assertNotIn(adj.FAIRBURN_LITERAL, p.lines)

    def test_fid_parcel_resolves_to_south_fulton_rules(self):
        rules = chain(with_classes("south_fulton", residential=["R-*"], readings=MAX))
        p = plan(FID, [row(1, district="R-4", jurisdictions=("south_fulton", "fid")),
                       row(2, district="M-1A", jurisdictions=("south_fulton", "fid"))],
                 40.0, verified("south_fulton", "fid", mutate=rules))
        self.assertEqual(p.deductions, ((1, 110.0),))
        self.assertEqual(p.counts["same_district"], 1)

    def test_union_city(self):
        rules = verified("union_city", mutate=with_classes("union_city", residential=["R-*"],
                                                           agricultural=["AG"]))
        p = plan(UC, [row(1, district="R-2", jurisdictions=("union_city",)),
                      row(2, district="AG", jurisdictions=("union_city",))], 50.0, rules)
        self.assertEqual(p.deductions, ((1, 95.0), (2, 95.0)))
        self.assertIn(adj.UNION_CITY_FRONT, p.lines)
        self.assertNotIn(adj.REAR_APPLIED, p.lines)
        self.assertNotIn(adj.HEIGHT_NOT_APPLIED, p.lines)

    def test_fairburn_literal_reading(self):
        p = plan(FB1, [row(1, district="M-2", jurisdictions=("fairburn",)),
                       row(2, district="M1", jurisdictions=("fairburn",))], 35.0, FB_LITERAL)
        self.assertEqual(p.deductions, ((1, 50.0),))
        self.assertIn("other_district 50 ft on 1 side", p.summary)
        self.assertIn(adj.FAIRBURN_LITERAL, p.lines)
        self.assertEqual(p.counts["same_district"], 1)
        # BUF-14 counting: Fairburn M-2 is an industrial district of the rules
        # (warehouse/distribution by right).
        self.assertEqual(p.counts["other_industrial"], 1)
        self.assertIn("ROW buffer not applied (no ROW source); 25 ft is within the 35 ft setback "
                      "anyway", p.lines)
        self.assertNotIn(adj.HEIGHT_NOT_APPLIED, p.lines)

    def test_fairburn_own_zoning_change_recomputes(self):
        rows = [row(1, district="M-2", jurisdictions=("fairburn",)),
                row(2, district="M-1", jurisdictions=("fairburn",))]
        self.assertEqual(plan(FB1, rows, 35.0, FB_LITERAL).deductions, ((1, 50.0),))
        self.assertEqual(plan(FB2, rows, 40.0, FB_LITERAL).deductions, ((2, 50.0),))

    def test_other_jurisdiction_is_another_district(self):
        p = plan(FB1, [row(1, district="M-1", jurisdictions=("south_fulton",))], 35.0,
                 FB_LITERAL)
        self.assertEqual(p.deductions, ((1, 50.0),))

    def test_atlanta_buffer_within_setback(self):
        rules = verified("atlanta", mutate=with_classes("atlanta", residential=["R-*"]))
        p = plan(ATL, [row(1, district="R-4", jurisdictions=("atlanta",))], 40.0, rules)
        self.assertEqual(p.deductions, ())
        self.assertIn("residential buffer 20 ft within the 40 ft setback on 1 side", p.lines)
        self.assertIn(adj.ACROSS_STREET, p.lines)
        self.assertIn(adj.HEIGHT_NOT_APPLIED, p.lines)
        self.assertTrue(any("screen" in x and "not a distance" in x for x in p.lines))

    def test_uncovered_is_unknown_never_row_or_none(self):
        p = plan(SF, [row(1, kind="uncovered", district=None, jurisdictions=(),
                          source="coverage")], 40.0, SF_CLASSES)
        self.assertEqual(p.deductions, ())
        self.assertIn("1 side adjacency unknown (no zoning polygon within 20 ft; not treated as "
                      "ROW or as no neighbour)", p.summary)
        self.assertEqual(p.counts["unknown"], 1)

    def test_states_without_a_plan(self):
        self.assertEqual(plan(SF, None, 40.0).summary, adj.ADJ_UNREADABLE)
        self.assertEqual(plan(SF, [], 40.0).summary, adj.ADJ_NOT_IMPORTED)
        self.assertEqual(plan(SF, [row(1), row(2, fresh=False)], 40.0, SF_CLASSES).summary,
                         adj.ADJ_STALE)
        self.assertEqual(plan(SF, [row(1, fresh=False)], 40.0, SF_CLASSES).deductions, ())
        pd = stored("fairburn", "fairburn_zoning", "PD")
        self.assertEqual(plan(pd, [row(1)], 25.0).summary, adj.ADJ_NO_RULE)

    def test_unknown_district_is_never_no_rule(self):
        """Review finding 1: no stored row, an unparsed or unlisted code, or no
        rules: nothing was checked, so the text never says 'none listed'."""
        unlisted = stored("fairburn", "fairburn_zoning", "ZZ-9")
        for st_ in (None, ("ZZ-9 unknown", None), unlisted):
            for rows_ in ([], [row(1)]):
                with self.subTest(stored=st_, rows=len(rows_)):
                    p = plan(st_, rows_, 25.0)
                    self.assertEqual((p.summary, p.deductions), (adj.ADJ_NO_DISTRICT, ()))
        with mock.patch.object(adj, "_rules", return_value=None):
            self.assertEqual(adj.plan_parcel("a", SF, [row(1)], 40.0).summary,
                             adj.ADJ_RULES_UNAVAILABLE)
        self.assertNotIn("none listed", adj.ADJ_NO_DISTRICT)

    def test_split_parcel(self):
        split = stored("south_fulton", "south_fulton_zoning", "M-1", split=True)
        p = plan(split, [row(1, district="R-3"), row(2, district="M-1")], 40.0, SF_CLASSES)
        self.assertEqual(p.deductions, ((1, 110.0),))   # residential is not relative
        self.assertIn("adjacency unknown on internal district lines (split-zoned parcel)",
                      p.lines)
        fsplit = stored("fairburn", "fairburn_zoning", "M-1", split=True)
        p = plan(fsplit, [row(1, district="M-2", jurisdictions=("fairburn",))], 35.0, FB_LITERAL)
        self.assertEqual(p.deductions, ())
        self.assertIn("other-district buffer not applied on 1 side (adjacency unknown "
                      "(split-zoned parcel))", p.lines)

    def test_use_and_height_notes(self):
        """BUF-15 / BUF-16: present for South Fulton, FID and Atlanta (height),
        South Fulton and FID (use); absent where the rules value is null."""
        sf = plan(SF, [row(1)], 40.0)
        fid = plan(FID, [row(1, jurisdictions=("fid",))], 40.0)
        atl = plan(ATL, [row(1, jurisdictions=("atlanta",))], 40.0)
        fb = plan(FB1, [row(1, jurisdictions=("fairburn",))], 35.0)
        uc = plan(UC, [row(1, jurisdictions=("union_city",))], 50.0)
        for p in (sf, fid):
            self.assertIn(adj.USE_NOT_CHECKED, p.lines)
        for p in (sf, fid, atl):
            self.assertIn(adj.HEIGHT_NOT_APPLIED, p.lines)
        for p in (fb, uc):
            self.assertNotIn(adj.HEIGHT_NOT_APPLIED, p.lines)
        for p in (atl, fb, uc):
            self.assertNotIn(adj.USE_NOT_CHECKED, p.lines)
        self.assertIn(adj.HEIGHT_NOT_APPLIED, plan(SF, [], 40.0).lines)

    def test_texts_name_no_parcel_and_no_district(self):
        p = plan(SF, [row(1, parcel_id="fulton-SECRET", district="R-3")], 40.0, SF_CLASSES)
        for t in (p.summary,) + p.lines:
            self.assertNotIn("SECRET", t)
            self.assertNotIn("R-3", t)

    def test_strictest_wins_on_a_side_facing_two_classes(self):
        rules = verified("fairburn", mutate=with_classes("fairburn", residential=["R-*"],
                                                         readings=LITERAL))
        p = plan(FB1, [row(1, district="R-3", jurisdictions=("fairburn",))], 35.0, rules)
        self.assertEqual(p.deductions, ((1, 50.0),))
        self.assertIn("residential 50 ft on 1 side", p.summary)


    def test_height_note_whenever_a_height_rule_exists(self):
        """Review finding 9: a numeric height_based_extra_setback is named too."""
        def numeric(data):
            data["jurisdictions"]["union_city"]["districts"]["M-1"]["dimensions"][
                "height_based_extra_setback"] = {"value": 12, "cite": "test"}
        p = plan(UC, [row(1, jurisdictions=("union_city",))], 50.0,
                 verified("union_city", mutate=numeric))
        self.assertIn(adj.HEIGHT_NOT_APPLIED, p.lines)

    def test_unreadable_neighbour_code_is_unknown(self):
        """Review finding 10: 'UNKNOWN', 'TBD', 'N/A' are no district."""
        for code in ("UNKNOWN", "TBD", "N/A", "?", " "):
            with self.subTest(code=code):
                p = plan(FB1, [row(1, district=code, jurisdictions=("fairburn",))], 35.0,
                         FB_LITERAL)
                self.assertEqual(p.deductions, ())
                self.assertEqual((p.counts["code_unreadable"], p.counts["other_district"]),
                                 (1, 0))
                self.assertIn(f"1 side adjacency unknown ({adj.CODE_UNREADABLE})", p.summary)
                self.assertFalse(any(adj.CLASS_TABLE_MISSING in x for x in p.lines))
        self.assertFalse(adj.readable_code("TBD"))
        self.assertTrue(adj.readable_code("R-4A"))

    def test_fid_parcel_on_its_own_table(self):
        """Review finding 12: an unincorporated FID parcel compares county
        neighbours under the FID table too (same code = same district)."""
        def unincorporated(data):
            data["jurisdictions"]["fid"]["unincorporated_parcel_ids"] = ["a"]
        rules = verified("south_fulton", "fid", mutate=chain(
            unincorporated, with_classes("fid", residential=["R-*"], readings=MAX),
            with_classes("south_fulton", non_residential=["R-4"], readings=MAX)))
        fid_m1 = stored("fid", "fulton_layer34_zoning", "M-1")
        with patch_rules(rules):
            own = zoning.district_for_stored(*fid_m1, "a")
        self.assertEqual((own.table_jurisdiction, own.district), ("fid", "M-1"))
        p = plan(fid_m1, [row(1, district="M-1", jurisdictions=("south_fulton", "fid")),
                       row(2, district="R-4", jurisdictions=("south_fulton", "fid"))],
                 40.0, rules)
        self.assertEqual(p.counts["same_district"], 1)
        # classified under both tables; the strictest (FID: residential) wins
        self.assertEqual(p.deductions, ((2, 110.0),))


class ReadingTests(unittest.TestCase):
    """Review finding 3: ambiguous readings are human switches in the rules
    (neighbour_classes.readings), never defaulted in code."""

    def test_fairburn_other_district_needs_the_reading(self):
        rows = [row(1, district="M-2", jurisdictions=("fairburn",))]
        for rules in (None, verified("fairburn", mutate=with_classes("fairburn")),
                      verified("fairburn", mutate=with_classes(
                          "fairburn", readings={"other_district": "everything"}))):
            p = plan(FB1, rows, 35.0, rules)
            self.assertEqual(p.deductions, ())
            self.assertIn("other-district buffer not applied on 1 side (which other districts: "
                          f"{adj.READING_UNDECIDED})", p.lines)
            self.assertNotIn(adj.FAIRBURN_LITERAL, p.lines)
            self.assertEqual(p.counts["other_undecided"], 1)

    def test_fairburn_non_industrial_reading(self):
        rules = verified("fairburn", mutate=with_classes(
            "fairburn", non_residential=["C-1"], industrial=["M-*"],
            readings={"other_district": "non_industrial"}))
        rows = [row(1, district="M-2", jurisdictions=("fairburn",)),
                row(2, district="C-1", jurisdictions=("fairburn",)),
                row(3, district="PD", jurisdictions=("fairburn",))]
        p = plan(FB1, rows, 35.0, rules)
        self.assertEqual(p.deductions, ((2, 50.0),))
        self.assertIn(adj.FAIRBURN_NON_INDUSTRIAL, p.lines)
        self.assertIn("other-district buffer not applied on 1 side (industrial neighbour)",
                      p.lines)
        self.assertIn("other-district buffer not applied on 1 side (neighbour district not "
                      "classified in the rules)", p.lines)

    def test_side_rear_needs_the_reading(self):
        no_reading = verified("south_fulton", mutate=with_classes(
            "south_fulton", residential=["R-*"]))
        p = plan(SF, [row(1, district="R-3")], 40.0, no_reading)
        self.assertEqual(p.deductions, ())
        self.assertIn("residential/agricultural buffer not applied on 1 side (side/rear "
                      f"{adj.READING_UNDECIDED})", p.lines)
        side = verified("south_fulton", mutate=with_classes(
            "south_fulton", residential=["R-*"], readings={"side_rear": "side"}))
        p = plan(SF, [row(1, district="R-3")], 40.0, side)
        self.assertEqual(p.deductions, ((1, 60.0),))      # side 50 + improvement 10
        self.assertIn(adj.SIDE_APPLIED, p.lines)
        self.assertNotIn(adj.REAR_APPLIED, p.lines)
        p = plan(SF, [row(1, district="R-3")], 40.0, SF_CLASSES)
        self.assertEqual(p.deductions, ((1, 110.0),))
        self.assertIn(adj.REAR_APPLIED, p.lines)
        self.assertIn(adj.STRIP_ENDS, p.lines)

    def test_buffer_only_tables_need_no_side_rear_reading(self):
        rules = verified("union_city", mutate=with_classes("union_city", residential=["R-*"]))
        p = plan(UC, [row(1, district="R-2", jurisdictions=("union_city",))], 50.0, rules)
        self.assertEqual(p.deductions, ((1, 95.0),))
        atl = verified("atlanta", mutate=with_classes("atlanta", residential=["R-*"]))
        p = plan(ATL, [row(1, district="R-4", jurisdictions=("atlanta",))], 40.0, atl)
        self.assertEqual(p.counts["side_rear_undecided"], 0)   # side 20 == rear 20

    def test_reading_requires_a_wellformed_table(self):
        def bad(data):
            data["jurisdictions"]["fairburn"]["neighbour_classes"] = {
                "readings": {"other_district": "literal"}}      # no cite
        with patch_rules(verified("fairburn", mutate=bad)):
            self.assertIsNone(adj.reading("fairburn", adj.READING_OTHER))
        with patch_rules(FB_LITERAL):
            self.assertEqual(adj.reading("fairburn", adj.READING_OTHER), "literal")
            self.assertIsNone(adj.reading("fairburn", adj.READING_SIDE_REAR))


class StaticTests(unittest.TestCase):
    """BUF-01 / BUF-22: no district-code literal invented in code, imports."""

    # District codes of the rules file and of its cite prose (South Fulton
    # Sec. 405.01(b)(1), Atlanta Sec. 16-16.006(2)).
    CODES = sorted({k for j in repo_rules()["jurisdictions"].values()
                    for k in (j.get("districts") or {})} |
                   {"AG-1", "R-*", "TR", "A-L", "NUP", "CUP", "MIX", "RG", "MR", "SPI", "RM",
                    "C-1", "C-2", "R-1", "R-2", "R-3", "R-4", "R-5"}, key=len, reverse=True)
    CODE_RE = re.compile(r"(?<![A-Za-z0-9-])(?:" + "|".join(re.escape(c) for c in CODES)
                         + r")(?![A-Za-z0-9])")

    def literals(self, name: str, skip: set[str] = frozenset()) -> list[tuple[int, str]]:
        tree = ast.parse((REPO_ROOT / name).read_text(encoding="utf-8"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body \
                    and isinstance(node.body[0], ast.Expr) and \
                    isinstance(node.body[0].value, ast.Constant):
                docs.add(id(node.body[0].value))
        cites = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in skip
                                                    for t in node.targets):
                for n in ast.walk(node.value):
                    cites.add(id(n))
        return [(n.lineno, n.value) for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docs and id(n) not in cites]

    def test_no_district_code_literal_in_code(self):
        cite_names = {n for n in vars(adj) if n.startswith("_") and n.endswith(
            ("_CITE", "_IMP", "_ADD", "_RES", "_OTHER", "_ROW"))}
        for name, skip in (("adjacency.py", cite_names), ("adjacency_import.py", set()),
                           ("site_fit.py", set())):
            for line, value in self.literals(name, skip):
                self.assertIsNone(self.CODE_RE.search(value), f"{name}:{line}: {value[:60]}")

    def test_imports_prepare_and_zoning_only(self):
        tree = ast.parse((REPO_ROOT / "adjacency.py").read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0])
        self.assertEqual(mods - {"__future__", "ast", "math", "dataclasses", "pathlib",
                                 "typing"}, {"prepare", "zoning"})

    def test_constants_are_fixed_feet(self):
        self.assertEqual(adj.SRID_FEET, 2240)
        self.assertEqual(adj.ABUT_TOL_FT, 20.0)
        self.assertEqual((adj.MIN_SLIVER_WIDTH_FT, adj.MIN_OVERLAP_AREA_SF,
                          adj.MIN_SEGMENT_LEN_FT), (3.0, 50.0, 10.0))
        self.assertEqual((adj.QUAD_SEGS, adj.VERTEX_QUAD_SEGS), (2, 16))
        self.assertAlmostEqual(adj.VERTEX_FACTOR, 1.0012059, places=6)
        # review finding 5: the vertex polygons overshoot d by under 0.5 ft
        # at the largest buffer in the rules (110 ft).
        self.assertLess(110.0 * (adj.VERTEX_FACTOR - 1.0), 0.5)
        self.assertEqual(adj.MISALIGNMENT_ALLOWANCE_FT, 5.0)
        self.assertNotRegex((REPO_ROOT / "adjacency.py").read_text(), r"3857|900913")

    def test_read_sql_is_batched_and_fresh(self):
        sql = adj._SQL_ADJACENCY_FOR_PARCELS
        self.assertIn("a.parcel_id = ANY(%s)", sql)
        self.assertIn("md5(ST_AsBinary(p.geometry))", sql)
        self.assertEqual(sql.count("%s"), 1)


if __name__ == "__main__":
    unittest.main()
