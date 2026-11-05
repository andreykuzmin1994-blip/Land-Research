"""research.py zoning wiring: S9, H3, upsert, freeze, protection (reviews/21_zoning).

Offline (SR-6). The headline guarantee (Z-03): with every jurisdiction's
rules 'draft', scoring is byte-identical to research.py with zoning absent
(a differential test on the CURRENT research.py, so scoring experiments
stay keepable; review finding 1). The one-off proof against the
pre-zoning commit ba4a780 is tests/zoning_golden_check.py (not in the
suite).
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import tests  # noqa: F401 — suite guards (mirror kill switch, sources/ redirect)
import prepare
import research
import zoning
import zoning_golden_case as golden
from zoning_helpers import REPO_ROOT, patch_rules, stored, verified

def _canon(x):
    return json.loads(json.dumps(x, sort_keys=True, default=str))


def _fold(node):
    """The string value of a constant expression ('a' 'b', 'a' + 'b',
    f-string literal parts joined), or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _fold(node.left), _fold(node.right)
        return a + b if a is not None and b is not None else (a or b)
    if isinstance(node, ast.JoinedStr):
        return " ".join(v.value for v in node.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def _all_strings(tree):
    out = []
    for n in ast.walk(tree):
        v = _fold(n)
        if v:
            out.append(v)
    return out


@contextmanager
def zoning_absent():
    """research.py as if zoning did not exist: nothing verified, every
    classification unknown. The rules state itself is untouched (no
    'rules unavailable' cycle event)."""
    unk = zoning.Classification(zoning.UNKNOWN, False, "zoning absent (test)")
    with mock.patch.object(zoning, "any_verified", lambda: False), \
            mock.patch.object(zoning, "classify_stored", lambda *a, **k: unk), \
            mock.patch.object(zoning, "classify", lambda *a, **k: unk), \
            mock.patch.object(zoning, "jurisdiction_status", lambda *a, **k: "none"):
        yield


class DraftEqualsAbsentTests(unittest.TestCase):
    """Z-03, Z-26 as a DIFFERENTIAL test (review finding 1).

    Each test compares the CURRENT research.py with itself: once with the
    real all-draft zoning_rules.json, once with zoning absent. So it pins
    'draft rules change nothing' without freezing the rest of scoring: an
    experiment that changes S2/S8/S10, the composite, actionability, notes
    or the scoring queries moves both sides alike and still passes. (The
    one-off proof against the pre-zoning commit is
    tests/zoning_golden_check.py, a review artefact outside the suite.)
    """

    @classmethod
    def setUpClass(cls):
        with zoning_absent():
            cls.absent = _canon(golden.run_case(research))

    def test_repo_rules_all_draft_equal_zoning_absent_exactly(self):
        self.assertFalse(zoning.any_verified())
        got = _canon(golden.run_case(research))
        self.assertEqual(got["per_parcel"], self.absent["per_parcel"])
        self.assertEqual(got["cycle"], self.absent["cycle"])

    def _assert_scores_identical(self, got, *, same_queries=True):
        for pid, rec in self.absent["per_parcel"].items():
            g = got["per_parcel"][pid]
            self.assertEqual(g["result"], rec["result"], pid)
            self.assertEqual(g["parcel_score_inserts"], rec["parcel_score_inserts"], pid)
            self.assertEqual(g["research_log_notes"], rec["research_log_notes"], pid)
            if same_queries:
                self.assertEqual(g["executed"], rec["executed"], pid)
        gc, rc = got["cycle"], self.absent["cycle"]
        self.assertEqual(gc["summary"], rc["summary"])
        self.assertEqual(gc["parcel_score_inserts"], rc["parcel_score_inserts"])
        self.assertEqual(gc["research_log_notes"], rc["research_log_notes"])

    def _assert_one_extra_cycle_event(self, got):
        gc, rc = got["cycle"], self.absent["cycle"]
        extra = [e for e in gc["executed"] if e not in rc["executed"]]
        self.assertEqual(len(extra), 1, extra)            # the one cycle-level event
        self.assertIn("zoning rules unavailable", extra[0][1][3])
        self.assertIsNone(extra[0][1][1])                 # parcel_id NULL: a cycle event
        self.assertEqual([e for e in gc["executed"] if e != extra[0]], rc["executed"])

    def test_missing_rules_file_degrades_to_the_stub(self):
        with patch_rules(problem="zoning_rules.json is missing"):
            got = _canon(golden.run_case(research))
        self._assert_scores_identical(got)
        self._assert_one_extra_cycle_event(got)

    def test_malformed_rules_file_degrades_to_the_stub(self):
        with patch_rules(problem="zoning_rules.json is not valid UTF-8 JSON"):
            got = _canon(golden.run_case(research))
        self._assert_scores_identical(got)
        self._assert_one_extra_cycle_event(got)

    def test_draft_classes_never_reach_a_score_even_when_read(self):
        # Force the zoning reads on while every jurisdiction stays draft: the
        # stored districts ARE read and classified, and no score moves.
        with mock.patch.object(zoning, "any_verified", lambda: True):
            got = _canon(golden.run_case(research))
        self._assert_scores_identical(got, same_queries=False)
        sqls = [e[0] for e in got["cycle"]["executed"]]
        self.assertIn(research._SQL_STORED_ZONING_BATCH, sqls)

    def test_verified_rules_elsewhere_do_not_move_these_parcels(self):
        # A verified jurisdiction with none of the fixture's districts in it
        # adds the batched zoning read, but no score changes.
        with patch_rules(verified("east_point")):
            got = _canon(golden.run_case(research))
        for pid, rec in self.absent["per_parcel"].items():
            # East Point 'I1' is unlisted -> unknown -> stub anyway
            self.assertEqual(got["per_parcel"][pid]["result"], rec["result"], pid)

    def test_the_suite_pins_no_frozen_scoring_golden(self):
        # Finding 1: no suite test compares research.py's scoring output to a
        # frozen file; the frozen proof lives outside unittest discovery.
        needles = ("golden_scores" + "_head", "GOLDEN" + "_NAME")
        for f in Path(__file__).parent.glob("test_*.py"):
            text = f.read_text(encoding="utf-8")
            for n in needles:
                self.assertFalse(n in text, (f.name, n))


class ComputeS9Tests(unittest.TestCase):
    """Z-21: the verified-only gate, protected by this test."""

    CASES = {
        "by_right": stored("south_fulton", "south_fulton_zoning", "M-1"),
        "rezoning": stored("south_fulton", "south_fulton_zoning", "AG-1"),
        "conditional_like": stored("atlanta", "atlanta_zoning", "I-1-C"),
        "unlisted": stored("south_fulton", "south_fulton_zoning", "R-3"),
        "fid": stored("fid", "fulton_layer34_zoning", "M-1A"),
        "no_source": (None, zoning.no_source_description("palmetto")),
        "forged": ("M-2", "Heavy Industrial"),
        "null": (None, None),
    }

    def test_repo_rules_all_draft_give_the_stub_for_every_class(self):
        for name, st in self.CASES.items():
            v = research._compute_s9(st, parcel_id="fulton-x", has_precedent=lambda: True)
            self.assertEqual(v, 5, name)
            self.assertIs(type(v), int, name)
        self.assertEqual(research._compute_s9(), 5)
        self.assertIs(type(research._compute_s9()), int)

    def test_verified_values_follow_the_program_table(self):
        def cond(d):
            d["jurisdictions"]["union_city"]["districts"]["M-1"]["warehouse_distribution"]["status"] = \
                "special_use_permit"
        with patch_rules(verified("south_fulton", "fid", "union_city", mutate=cond)):
            s9 = research._compute_s9
            self.assertEqual(s9(self.CASES["by_right"]), 10)
            self.assertEqual(s9(stored("union_city", "union_city_zoning", "M-1")), 7)
            self.assertEqual(s9(self.CASES["rezoning"], has_precedent=lambda: True), 4)
            self.assertEqual(s9(self.CASES["rezoning"], has_precedent=lambda: False), 1)
            self.assertEqual(s9(self.CASES["rezoning"]), 1)
            self.assertEqual(s9(self.CASES["unlisted"], has_precedent=lambda: True), 5)
            self.assertEqual(s9(self.CASES["fid"]), 10)
            self.assertEqual(s9(self.CASES["forged"]), 5)
            self.assertEqual(s9(self.CASES["null"]), 5)
            self.assertEqual(s9(self.CASES["conditional_like"]), 5)   # Atlanta still draft

    def test_precedent_is_asked_only_for_verified_rezoning(self):
        asked = []
        with patch_rules(verified("south_fulton")):
            for st in self.CASES.values():
                research._compute_s9(st, has_precedent=lambda: asked.append(1) or False)
        self.assertEqual(len(asked), 1)


class VerifiedScoringTests(unittest.TestCase):
    """S9 end to end with South Fulton verified, both scoring paths."""

    NEIGHBOURS = {  # target -> neighbour rows (parcel_id, zoning, description)
        "fulton-Z02": [("fulton-Z01",) + golden.PARCELS["fulton-Z01"][3:5]],
        "fulton-Z03": [],
    }

    def _answer(self):
        base = golden.responder(research)

        def answer(sql, params):
            if sql == research._SQL_ZONING_PRECEDENT:
                self.assertEqual(params[:2], (0.03, 1609.344))
                return self.NEIGHBOURS.get(params[2], [])
            if sql == research._SQL_ZONING_PRECEDENT_BATCH:
                self.assertEqual(params[:2], (0.03, 1609.344))
                return [(t,) + r for t in params[2] for r in self.NEIGHBOURS.get(t, [])]
            return base(sql, params)
        return answer

    def test_both_paths_agree_and_follow_the_rules(self):
        data = verified("south_fulton")
        data["jurisdictions"]["south_fulton"]["unlisted_districts"] = "not_permitted"
        with patch_rules(data):
            per = {}
            for pid in ("fulton-Z01", "fulton-Z02", "fulton-Z03", "fulton-Z11", "fulton-Z10"):
                conn = golden.AnswerConn(self._answer())
                r = research.score_parcel(pid, conn=conn, cycle_id="score-atlanta-v",
                                          params=prepare.get_parameters(), run_tag="v",
                                          experiment_id="exp-v")
                per[pid] = r["sub_scores"]["S9_entitlement_complexity"]
                sqls = [s for s, _ in conn.executed]
                self.assertEqual(research._SQL_ZONING_PRECEDENT in sqls,
                                 pid in ("fulton-Z02", "fulton-Z03"), pid)
            self.assertEqual(per, {"fulton-Z01": 10, "fulton-Z02": 4, "fulton-Z03": 1,
                                   "fulton-Z11": 5, "fulton-Z10": 5})

            conn = golden.AnswerConn(self._answer())

            @contextmanager
            def gc():
                yield conn
            with mock.patch.object(prepare, "get_connection", gc):
                summary = research.run_scoring_cycle("atlanta", run_tag="v", experiment_id="exp-v")
            cyc = {p["parcel_id"]: p["sub_scores"]["S9_entitlement_complexity"]
                   for p in summary["parcels"]}
            for pid, v in per.items():
                self.assertEqual(cyc[pid], v, pid)
            sqls = [s for s, _ in conn.executed]
            self.assertEqual(sqls.count(research._SQL_STORED_ZONING_BATCH), 1)
            self.assertEqual(sqls.count(research._SQL_ZONING_PRECEDENT_BATCH), 1)
            self.assertNotIn(research._SQL_ZONING_PRECEDENT, sqls)
            self.assertNotIn(research._SQL_STORED_ZONING, sqls)

    def test_precedent_sql_is_metres_on_geography_with_an_index_prefilter(self):
        # Z-09 degree-unit regression (the live check runs the SQL itself).
        for sql in (research._SQL_ZONING_PRECEDENT, research._SQL_ZONING_PRECEDENT_BATCH):
            self.assertIn("ST_DWithin(p1.geometry::geography, p2.geometry::geography, %s)", sql)
            self.assertIn("p2.geometry && ST_Expand(p1.geometry, %s)", sql)
            self.assertIn("p2.parcel_id <> p1.parcel_id", sql)
        self.assertEqual(research._S9_PRECEDENT_RADIUS_M, 1609.344)

    def test_a_draft_neighbour_is_not_precedent(self):
        with patch_rules(verified("south_fulton")):
            self.assertTrue(research._is_industrial_precedent(
                ("fulton-a",) + stored("south_fulton", "s", "M-2")))
            self.assertFalse(research._is_industrial_precedent(
                ("fulton-b",) + stored("fairburn", "s", "M-2")))       # draft jurisdiction
            self.assertFalse(research._is_industrial_precedent(("fulton-c", "M-2", None)))


class CycleEntryFreezeTests(unittest.TestCase):
    """Z-01(b): both cycles verify the rules before touching the database."""

    def test_scoring_cycle_verifies_before_connecting(self):
        order = []
        with mock.patch.object(zoning, "verify_rules_unchanged", lambda: order.append("verify")), \
                mock.patch.object(prepare, "get_connection",
                                  side_effect=lambda: order.append("connect") or (_ for _ in ()).throw(
                                      RuntimeError("stop"))):
            with self.assertRaises(RuntimeError):
                research.run_scoring_cycle("atlanta", run_tag="t", experiment_id="e")
        self.assertEqual(order, ["verify", "connect"])

    def test_changed_rules_abort_both_cycles_before_any_work(self):
        boom = zoning.ZoningRulesChanged("changed")
        for fn in (lambda: research.run_scoring_cycle("atlanta", run_tag="t", experiment_id="e"),
                   lambda: research.run_discovery_cycle("atlanta")):
            with mock.patch.object(zoning, "verify_rules_unchanged", side_effect=boom), \
                    mock.patch.object(prepare, "get_connection",
                                      side_effect=AssertionError("connected")):
                with self.assertRaises(zoning.ZoningRulesChanged):
                    fn()


class DiscoveryTests(unittest.TestCase):
    """Z-06, Z-15, decision 5a."""

    def test_upsert_cannot_write_zoning(self):
        # Review finding 2: the upsert does not name the zoning columns at
        # all, so a new row gets NULL and a conflict keeps the stored value
        # whatever the row dict carries (decision 5a, made structural).
        sql = research._SQL_UPSERT_PARCEL
        self.assertFalse(re.search(r"(?i)zoning", sql), "upsert names a zoning column")
        cols = sql.split("(", 1)[1].split(")", 1)[0]
        n_cols = len([c for c in cols.split(",") if c.strip()])
        values = sql.split("VALUES (", 1)[1].split(") ON CONFLICT", 1)[0]
        self.assertEqual(n_cols, values.count("%s") + values.count("NOW()"))

    def test_upsert_ignores_zoning_in_the_row(self):
        executed = []

        class Cur:
            def execute(self, sql, params=()):
                executed.append((sql, tuple(params)))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

        class Conn:
            def cursor(self):
                return Cur()

        z, d = stored("atlanta", "atlanta_zoning", "I-1", desc="Light Industrial")
        row = {"parcel_id": "fulton-x", "county": "fulton", "state": "GA", "market": "atlanta",
               "zoning": z, "zoning_description": d}
        research._upsert_parcel(Conn(), row, "POLYGON((0 0,0 1,1 1,0 0))")
        (sql, params), = executed
        self.assertEqual(sql, research._SQL_UPSERT_PARCEL)
        self.assertEqual(len(params), sql.count("%s"))
        self.assertNotIn(z, params)
        self.assertNotIn(d, params)

    def test_research_never_builds_import_provenance(self):
        # Finding 2(b): the provenance encoders are the import tool's alone.
        src = (REPO_ROOT / "research.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                 and isinstance(n.value, ast.Name) and n.value.id == "zoning"}
        for banned in ("encode_stored", "no_source_description", "STORED_PREFIX", "_FIELD_SEP",
                       "_clean", "__file__", "__spec__", "__loader__", "_STATE", "_load_state"):
            self.assertNotIn(banned, attrs, banned)
        self.assertFalse([s for s in _all_strings(tree) if zoning.STORED_PREFIX in s])
        self.assertNotIn("getattr(zoning", src.replace(" ", ""))
        self.assertNotIn("vars(zoning", src.replace(" ", ""))
        self.assertNotIn("zoning.__dict__", src)

    def test_no_research_string_writes_parcels_zoning(self):
        # Finding 2(c): EVERY string in research.py (module constants, inline
        # execute() literals, constant concatenations, f-string parts) -- no
        # UPDATE of parcels and no assignment to a zoning column anywhere.
        tree = ast.parse((REPO_ROOT / "research.py").read_text(encoding="utf-8"))
        for text in _all_strings(tree):
            self.assertFalse(re.search(r"(?i)\bUPDATE\s+(ONLY\s+)?(public\.)?parcels\b", text),
                             text[:80])
            self.assertFalse(re.search(r"(?i)\bzoning(_description)?\s*=", text), text[:80])
            self.assertFalse(re.search(r"(?i)INSERT\s+INTO\s+(public\.)?parcels\b[^;]*\bzoning",
                                       text), text[:80])

    def test_mapped_row_never_carries_zoning(self):
        reg = json.loads((REPO_ROOT / "sources.json").read_text(encoding="utf-8"))
        mapping = dict(reg["county_parcel_data"]["fulton_ga"]["field_mapping"])
        feature = {"attributes": {"ParcelID": "14 0001 0001", "ZClass": "M-2", "Zoning": "I2",
                                  "ClassCode": "I2", "LUCode": "400", "LandAcres": 20.0},
                   "geometry": {"rings": [[[-84.55, 33.55], [-84.55, 33.56], [-84.54, 33.56],
                                           [-84.54, 33.55], [-84.55, 33.55]]]}}
        for m in (mapping, {**mapping, "zoning": "ClassCode"}):
            row, *_ = research._map_feature_to_parcel(
                feature, m, {}, "atlanta", "south_fulton_campbellton", "/tmp/x.json")
            self.assertIsNone(row["zoning"])
            self.assertIsNone(row["zoning_description"])

    def test_module_sql_constants_never_assign_zoning(self):
        for name in dir(research):
            sql = getattr(research, name)
            if name.startswith("_SQL_") and isinstance(sql, str):
                self.assertFalse(re.search(r"(?i)\bzoning(_description)?\s*=", sql), name)
                self.assertFalse(re.search(r"(?i)UPDATE\s+parcels", sql), name)

    def test_h3_never_rejects_and_reads_the_stored_zoning(self):
        params = prepare.get_parameters()
        z, d = stored("south_fulton", "south_fulton_zoning", "M-1")
        for row in ({}, {"_stored_zoning": (z, d)}, {"_stored_zoning": ("M-1", None)},
                    {"_stored_zoning": None, "zoning": "M-2"}):
            self.assertEqual(research._h3_flag(row, None, params).action, "flag")
        self.assertEqual(research._h3_flag({"_stored_zoning": (z, d), "zoning": None}, None, params).reason,
                         "H3 zoning M-1 (south_fulton): by_right, rules draft")
        self.assertEqual(research._h3_flag({"zoning": "M-2"}, None, params).reason,
                         zoning.H3_NOT_IMPORTED)
        self.assertIn("not imported or not matched", zoning.H3_NOT_IMPORTED)

    def test_h3_flag_is_a_deduplicated_data_gap_across_cycles(self):
        from test_data_gap_flags import _Conn  # answer-by-SQL fake
        import pipeline_common
        feature = json.loads((Path(__file__).parent / "fixtures" / "discovery" /
                              "arcgis_query_two_features.json").read_text())["features"][0]
        reg = json.loads((REPO_ROOT / "sources.json").read_text(encoding="utf-8"))
        mapping = reg["county_parcel_data"]["fulton_ga"]["field_mapping"]
        params = dict(prepare.get_parameters())
        pid = "fulton-" + str(feature["attributes"][mapping["parcel_id"]]).strip()
        z, d = stored("fairburn", "fairburn_zoning", "M-2")
        conn = _Conn()
        open_gaps: set = set()
        for i in range(2):
            cid = research._make_cycle_id("fulton")
            research._OPEN_DATA_GAPS_BY_CYCLE[cid] = open_gaps
            research._STORED_ZONING_BY_CYCLE[cid] = {pid: (z, d)}
            try:
                st = research._process_parcel(feature, conn, cid, "south_fulton_campbellton",
                                              "atlanta", mapping, params["owner_classification"],
                                              params, raw_response_path="/tmp/x.json")
            finally:
                research._OPEN_DATA_GAPS_BY_CYCLE.pop(cid, None)
                research._STORED_ZONING_BY_CYCLE.pop(cid, None)
            self.assertEqual(st, "discovery")
        h3 = [p for s, p in conn.executed if s == pipeline_common._SQL_INSERT_FLAG
              and "H3 " in p[3]]
        self.assertEqual(len(h3), 1)
        self.assertEqual(h3[0][0], "data_gap")
        self.assertTrue(h3[0][3].endswith("H3 zoning M-2 (fairburn): by_right, rules draft"))

    def test_discovery_cycle_reads_stored_zoning_once(self):
        sqls = []
        self.assertIn("_SQL_STORED_ZONING_FOR_MARKET", (REPO_ROOT / "research.py").read_text())
        src = ast.parse((REPO_ROOT / "research.py").read_text())
        for node in ast.walk(src):
            if isinstance(node, ast.FunctionDef) and node.name == "run_discovery_cycle":
                sqls = [n.func.id for n in ast.walk(node)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertEqual(sqls.count("_load_stored_zoning"), 1)

    def test_research_flags_are_data_gap_only(self):
        # Z-15: no actionability_block (or other) flag writes from research.py.
        tree = ast.parse((REPO_ROOT / "research.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_flag":
                self.assertEqual(ast.literal_eval(node.args[4]), "data_gap", node.lineno)
        self.assertNotRegex((REPO_ROOT / "research.py").read_text(),
                            r"(?i)INSERT\s+INTO\s+flagged_items")


class RunnerAndReportingTests(unittest.TestCase):
    """Findings 3, 9, 11 (protected modules, between-runs change)."""

    def test_evaluate_checks_the_committed_rules_before_any_cycle(self):
        import runner
        boom = zoning.ZoningRulesChanged("differs from git HEAD")
        with mock.patch.object(zoning, "verify_rules_committed", side_effect=boom) as v, \
                mock.patch.object(research, "run_discovery_cycle",
                                  side_effect=AssertionError("discovery ran")), \
                mock.patch.object(research, "run_scoring_cycle",
                                  side_effect=AssertionError("scoring ran")):
            with self.assertRaises(zoning.ZoningRulesChanged):
                runner.evaluate("atlanta", skip_ingestion=True, skip_memo=True,
                                run_tag="t", experiment_id="e")
        v.assert_called_once_with()
        src = (REPO_ROOT / "runner.py").read_text(encoding="utf-8")
        body = src.split("def evaluate(", 1)[1].split("\n    try:", 1)[0]
        self.assertLess(body.index("prepare.verify_parameters_unchanged()"),
                        body.index("zoning.verify_rules_committed()"))

    def test_memo_renders_imported_zoning_decoded(self):
        import reporting
        z, d = stored("atlanta", "atlanta_zoning", "I-1", desc="Light Industrial")
        line = reporting._zoning_line({"zoning": z, "zoning_description": d})
        self.assertEqual(line, "I-1 (atlanta): by_right, rules draft; source atlanta_zoning: "
                               "Light Industrial")
        self.assertEqual(reporting._zoning_line({"zoning": "AG-1",
                                                 "zoning_description": "Agricultural"}),
                         "AG-1 — Agricultural")
        self.assertEqual(reporting._zoning_line({}), "— — —")
        src = (REPO_ROOT / "reporting.py").read_text(encoding="utf-8")
        self.assertIn("- **Current zoning**: {_zoning_line(parcel)}", src)


class ProtectionTests(unittest.TestCase):
    """Z-14, Z-23: zoning_rules.json is human-only; the lists name it."""

    PIPELINE = ("research.py", "runner.py", "costar_ingest.py", "costar_import.py",
                "reporting.py", "pipeline_common.py", "zoning.py", "zoning_import.py",
                "connector_harness.py", "cli.py", "prepare.py", "site_fit.py")

    def test_no_pipeline_module_writes_zoning_rules(self):
        writers = {"write_text", "write_bytes", "dump", "replace", "rename", "copy",
                   "copyfile", "copy2", "move", "touch", "unlink"}
        for name in self.PIPELINE:
            tree = ast.parse((REPO_ROOT / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                consts = [n.value for n in ast.walk(node)
                          if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                names = [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]
                mentions = any("zoning_rules" in c for c in consts) or "RULES_PATH" in names
                if not mentions:
                    continue
                f = node.func
                if isinstance(f, ast.Name) and f.id == "open":
                    mode = node.args[1].value if len(node.args) > 1 and isinstance(
                        node.args[1], ast.Constant) else "r"
                    self.assertFalse(set(str(mode)) & set("wax+"), (name, node.lineno))
                if isinstance(f, ast.Attribute):
                    self.assertNotIn(f.attr, writers, (name, node.lineno))
                    if f.attr == "open" and node.args and isinstance(node.args[0], ast.Constant):
                        self.assertFalse(set(str(node.args[0].value)) & set("wax+"), (name, node.lineno))

    def test_research_reaches_the_rules_only_through_zoning(self):
        src = (REPO_ROOT / "research.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # String constants in code (calls, assignments), not docstrings/comments.
        consts = [c.value for n in ast.walk(tree) if isinstance(n, (ast.Call, ast.Assign, ast.AnnAssign))
                  for c in ast.walk(n) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
        self.assertFalse([c for c in consts if "zoning_rules" in c])
        self.assertNotIn("_STATE", src)
        self.assertNotIn("RULES_PATH", src)
        mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertIn("zoning", mods)
        self.assertNotIn("zoning_import", mods)

    def test_docs_name_the_protected_files(self):
        for doc in ("CLAUDE.md", "home_machine/agent_prompt.md", "AUTORESEARCH_MECHANICS.md",
                    "STANDING_RISKS.md"):
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            for needle in ("zoning_rules.json", "zoning.py", "zoning_import.py"):
                self.assertIn(needle, text, (doc, needle))

    def test_autopilot_lets_the_agent_edit_research_py_only(self):
        sh = (REPO_ROOT / "home_machine" / "autopilot.sh").read_text(encoding="utf-8")
        allowed = re.findall(r'"((?:Edit|Write)\([^)]*\))"', sh.split("local allowed=(", 1)[1].split(")\n", 1)[0] + ")")
        self.assertEqual(sorted(allowed), ["Edit(./research.py)", "Write(./research.py)"])

    def test_makefile_target(self):
        mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("zoning-import:", mk)
        self.assertIn("python zoning_import.py", mk)


if __name__ == "__main__":
    unittest.main()
