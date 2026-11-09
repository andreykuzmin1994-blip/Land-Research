"""The hazard table ENABLED in the shipped repo (reviews/33_hazards_enabled).

The operator-authorized prepare-mutation of 2026-11-09 added
``_DDL_PARCEL_HAZARDS`` and ``_DDL_PARCEL_HAZARDS_INDEXES`` to prepare.py,
exactly as proposed in reviews/26_flood_wetlands/01_prepare_mutation_proposal.md.
These tests pin, on the SHIPPED state (no gate patch):

- G3: the gate is on and the schema matches hazards.py;
- G2: the prepare.py diff is exactly the proposal and the metric SQL is
  byte-identical to the base;
- G8: the three copies of the DDL (hazards.PROPOSED_DDL, prepare.py,
  STORAGE_ARCHITECTURE.md) cannot drift, and the gate reads the real
  constant from the source text;
- G5: with the table EMPTY, scores are unchanged against the 8ddaf69
  record; only the documented flag text moves.

Offline (SR-6): no DATABASE_URL, answer-by-SQL fakes. The live checks are
recorded in reviews/33_hazards_enabled/03_decision_note.md.
"""

from __future__ import annotations

import ast
import json
import subprocess
import unittest

import tests  # noqa: F401  (suite guards)
import hazards
import prepare
import reporting
import research
import site_fit
import hazards_golden_case as golden
from hazards_helpers import disabled, enabled, with_prepare_mutation
from zoning_helpers import REPO_ROOT

BASE = "4fe8080"   # main before the 2026-11-09 prepare-mutation
RECORD = REPO_ROOT / "reviews" / "26_flood_wetlands" / "golden_head_8ddaf69.json"
RECORD_DATE = (2026, 11, 6)
PREPARE_SRC = (REPO_ROOT / "prepare.py").read_text(encoding="utf-8")


def git_show(rev: str, name: str) -> bytes | None:
    try:
        return subprocess.run(["git", "show", f"{rev}:{name}"], cwd=REPO_ROOT,
                              capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, OSError):
        return None


class _RecordClock(research.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return cls(*RECORD_DATE, 12, 0, 0, tzinfo=tz)


def current_case() -> dict:
    real = research.datetime
    research.datetime = _RecordClock
    try:
        return json.loads(json.dumps(golden.run_case(research), sort_keys=True, default=str))
    finally:
        research.datetime = real


# ---------------------------------------------------------------------------
class ShippedStateTests(unittest.TestCase):
    """G3, and the inverse of every off-by-design test (R-02 kind a)."""

    def test_gate_on_and_schema_readable(self):
        self.assertTrue(hazards.enabled())
        self.assertIsNone(hazards.schema_problem())
        self.assertEqual(hazards._SOURCE_DDL, prepare._DDL_PARCEL_HAZARDS)

    def test_discovery_h4_h6_pass_with_empty_reason(self):
        for f, fid in ((research._h4_flag, "H4"), (research._h6_filter, "H6")):
            r = f({}, None, prepare.get_parameters())
            self.assertEqual((r.action, r.filter_id, r.reason), ("pass", fid, ""))

    def test_site_fit_and_reporting_name_the_hazard_variant(self):
        self.assertIn(hazards.STREAM_BUFFERS, site_fit._base_constraints(None))
        self.assertEqual(reporting._projection_footnote(),
                         reporting._PROJECTION_FOOTNOTE_HAZARDS)

    def test_disabled_restores_the_off_state_and_nests(self):
        with disabled():
            self.assertFalse(hazards.enabled())
            self.assertEqual(hazards.schema_problem(), "no hazard table in prepare.py")
            self.assertEqual(research._h4_flag({}, None, {}).action, "flag")
            with enabled():
                self.assertTrue(hazards.enabled())
            self.assertFalse(hazards.enabled())
        self.assertTrue(hazards.enabled())

    def test_disabled_is_never_suite_wide(self):
        # R-02: no module-level statement (import side effect) and no
        # setUpModule may switch the shipped gate off; only class/method
        # decorators and per-class setUpClass starts with a cleanup.
        def calls_disabled(node):
            return any(isinstance(c, ast.Call)
                       and getattr(c.func, "id", getattr(c.func, "attr", "")).endswith("disabled")
                       for c in ast.walk(node))
        for path in sorted((REPO_ROOT / "tests").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    if getattr(node, "name", "") == "setUpModule":
                        self.assertFalse(calls_disabled(node), path.name)
                    continue
                self.assertFalse(calls_disabled(node), f"{path.name}:{node.lineno}")
        self.assertNotIn("_SOURCE_DDL", (REPO_ROOT / "tests" / "__init__.py").read_text())


class DriftPinTests(unittest.TestCase):
    """G8 (R-07 / R-08): three copies of the DDL, one text."""

    def test_prepare_constant_equals_hazards_proposed_ddl(self):
        self.assertEqual(prepare._DDL_PARCEL_HAZARDS.strip(), hazards.PROPOSED_DDL.strip())
        self.assertEqual(prepare._DDL_PARCEL_HAZARDS_INDEXES, hazards.PROPOSED_INDEXES)

    def test_thirty_columns_two_geometries_srid_2240(self):
        body = prepare._DDL_PARCEL_HAZARDS.split("(", 1)[1].rsplit(")", 1)[0]
        cols = [ln.strip().split()[0] for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("--")]
        cols = [c for c in cols if c not in ("PRIMARY", "REFERENCES")]
        self.assertEqual(tuple(cols), hazards.COLUMNS)
        self.assertEqual(len(cols), 30)
        self.assertEqual(prepare._DDL_PARCEL_HAZARDS.count("geometry(MultiPolygon, 2240)"), 2)
        self.assertIn("REFERENCES parcels(parcel_id) ON DELETE CASCADE",
                      prepare._DDL_PARCEL_HAZARDS)
        for stmt in (prepare._DDL_PARCEL_HAZARDS, *prepare._DDL_PARCEL_HAZARDS_INDEXES):
            self.assertRegex(stmt, r"CREATE (TABLE|INDEX) IF NOT EXISTS ")   # SR-9

    def test_all_ddl_order(self):
        ddl = prepare._ALL_DDL
        i_mirror = ddl.index(prepare._DDL_EXPERIMENT_LOG_MIRROR)
        i_table = ddl.index(prepare._DDL_PARCEL_HAZARDS)
        self.assertLess(ddl.index(prepare._DDL_PARCELS), i_table)       # the FK target first
        self.assertEqual(i_table, i_mirror + 1)
        n = len(prepare._DDL_PARCEL_HAZARDS_INDEXES)
        self.assertEqual(ddl[i_table + 1:i_table + 1 + n], prepare._DDL_PARCEL_HAZARDS_INDEXES)
        self.assertEqual(ddl[i_table + 1 + n:], prepare._DDL_INDEXES)
        self.assertEqual(sum(1 for s in ddl if "parcel_hazards" in s), 1 + n)

    def test_storage_architecture_carries_the_sql_verbatim(self):
        doc = (REPO_ROOT / "STORAGE_ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn(prepare._DDL_PARCEL_HAZARDS.strip(), doc)
        for stmt in prepare._DDL_PARCEL_HAZARDS_INDEXES:
            self.assertIn(stmt, doc)
        self.assertIn("reviews/33_hazards_enabled", doc)

    def test_proposal_sql_block_is_the_prepare_text(self):
        doc = (REPO_ROOT / "reviews" / "26_flood_wetlands" /
               "01_prepare_mutation_proposal.md").read_text(encoding="utf-8")
        sql = doc.split("```sql\n", 1)[1].split("```", 1)[0]
        want = prepare._DDL_PARCEL_HAZARDS.strip() + "\n\n" + \
            "\n".join(prepare._DDL_PARCEL_HAZARDS_INDEXES)
        self.assertEqual(sql.strip(), want)

    def test_gate_reads_the_real_constant_as_a_plain_literal(self):
        self.assertEqual(hazards.ddl_from_prepare_source(), prepare._DDL_PARCEL_HAZARDS)
        self.assertEqual(hazards.ddl_from_prepare_source(REPO_ROOT / "prepare.py"),
                         prepare._DDL_PARCEL_HAZARDS)
        tree = ast.parse(PREPARE_SRC)
        nodes = [n for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == hazards.GATE_CONSTANT
                         for t in n.targets)]
        self.assertEqual(len(nodes), 1)
        self.assertIsInstance(nodes[0].value, ast.Constant)    # R-08: no f-string, no call
        self.assertIsInstance(nodes[0].value.value, str)

    def test_apply_schema_runs_the_hazard_ddl_in_order_and_rolls_back_on_error(self):
        class Cur:
            def __init__(self, conn):
                self.conn = conn

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                self.conn.executed.append(sql)
                if sql == self.conn.fail:
                    raise RuntimeError("boom")

        class Conn:
            info = None

            def __init__(self, fail=None):
                self.executed, self.fail, self.commits, self.rollbacks = [], fail, 0, 0

            def cursor(self):
                return Cur(self)

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

        ok = Conn()
        prepare.apply_schema(ok)
        self.assertEqual(ok.executed, list(prepare._ALL_DDL))
        self.assertEqual((ok.commits, ok.rollbacks), (1, 0))
        bad = Conn(fail=prepare._DDL_PARCEL_HAZARDS)
        with self.assertLogs(prepare.log, "ERROR"), \
                self.assertRaisesRegex(RuntimeError, "schema apply failed"):
            prepare.apply_schema(bad)
        self.assertEqual((bad.commits, bad.rollbacks), (0, 1))
        self.assertEqual(bad.executed[-1], prepare._DDL_PARCEL_HAZARDS)


class ExactDiffTests(unittest.TestCase):
    """G2 (R-01): the prepare.py diff is the proposal and nothing else."""

    def base(self) -> bytes:
        b = git_show(BASE, "prepare.py")
        if b is None:
            self.skipTest("git history unavailable")
        return b

    def test_prepare_is_base_plus_exactly_the_mutation(self):
        self.assertEqual((REPO_ROOT / "prepare.py").read_bytes(),
                         with_prepare_mutation(self.base()))

    def test_metric_region_and_schema_version_byte_identical(self):
        base = self.base().decode("utf-8")
        marker = "def calculate_actionable_pipeline_count"
        self.assertEqual(PREPARE_SRC.split(marker, 1)[1], base.split(marker, 1)[1])
        sv = [ln for ln in PREPARE_SRC.splitlines() if ln.startswith("SCHEMA_VERSION")]
        self.assertEqual(sv, [ln for ln in base.splitlines() if ln.startswith("SCHEMA_VERSION")])
        self.assertNotIn("parcel_hazards", prepare._LATEST_SCORE_FILTER)
        self.assertNotIn("parcel_hazards", prepare._metric_sql_and_params("parcel_id", "t")[0])

    def test_the_helper_refuses_a_base_without_the_anchors(self):
        with self.assertRaises(ValueError):
            with_prepare_mutation(b"X = 1\n")
        once = with_prepare_mutation(self.base())
        with self.assertRaises(ValueError):          # already applied: anchors gone
            with_prepare_mutation(once)


class EmptyTableDifferentialTests(unittest.TestCase):
    """G5 (R-04): the shipped state against the 8ddaf69 record."""

    @classmethod
    def setUpClass(cls):
        cls.want = json.loads(RECORD.read_text(encoding="utf-8"))
        cls.on = current_case()
        with disabled():
            cls.off = current_case()

    def test_shipped_state_differs_only_by_the_documented_delta(self):
        self.assertEqual(golden.differences_on_empty(self.want, self.on,
                                                     hazards._SQL_HAZARDS_FOR_PARCELS), [])

    def test_scores_inserts_and_notes_identical(self):
        ws, gs = self.want["scoring"], self.on["scoring"]
        self.assertEqual(len(ws["per_parcel"]), 22)
        for pid, w in ws["per_parcel"].items():
            for k in ("result", "parcel_score_inserts", "research_log_notes"):
                self.assertEqual(gs["per_parcel"][pid][k], w[k], (pid, k))
        for k in ("summary", "parcel_score_inserts", "research_log_notes"):
            self.assertEqual(gs["cycle"][k], ws["cycle"][k], k)

    def test_discovery_counts(self):
        wd, gd = self.want["discovery"], self.on["discovery"]
        self.assertEqual(sum(1 for k in wd["filters"] if wd["filters"][k] != gd["filters"][k]), 7)
        self.assertEqual(len(wd["process"]), 16)
        for k, w in wd["process"].items():
            self.assertEqual(gd["process"][k]["status"], w["status"], k)
            self.assertIn(len(w["executed"]) - len(gd["process"][k]["executed"]), (0, 2), k)
        self.assertEqual(sum(1 for k, w in wd["process"].items()
                             if len(w["executed"]) - len(gd["process"][k]["executed"]) == 2), 10)

    def test_gate_off_is_still_the_record(self):
        self.assertEqual(self.off, self.want)

    def test_the_rules_are_not_vacuous(self):
        # Failure modes: the off state does not satisfy the on rules, and a
        # changed insert, note or extra statement is caught.
        sql = hazards._SQL_HAZARDS_FOR_PARCELS
        self.assertTrue(golden.differences_on_empty(self.want, self.off, sql))
        for mutate in (
            lambda g: g["scoring"]["per_parcel"]["fulton-Z06"]["parcel_score_inserts"][0]
            .__setitem__(1, 0.0),
            lambda g: g["scoring"]["per_parcel"]["fulton-S01"]["research_log_notes"].append("x"),
            lambda g: g["scoring"]["cycle"]["executed"].append(["SELECT 1", []]),
            lambda g: g["discovery"]["process"][next(iter(g["discovery"]["process"]))]
            .__setitem__("status", "rejected"),
            lambda g: g["discovery"]["filters"]["arcgis_query_two_features.json#0"][3]
            .__setitem__(0, "flag"),
        ):
            got = json.loads(json.dumps(self.on))
            mutate(got)
            self.assertTrue(golden.differences_on_empty(self.want, got, sql))


if __name__ == "__main__":
    unittest.main()
