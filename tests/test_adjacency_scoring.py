"""The adjacency gate in site_fit, reporting and the runner; the differential
against 595aa08; the protected-file contract (reviews/27_site_fit_buffers).
Offline (SR-6): fakes only; the PostGIS side is tests/live_adjacency_check.py."""

from __future__ import annotations

import ast
import json
import subprocess
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  (suite guards)
import adjacency
import costar_ingest
import hazards
import prepare
import reporting
import research
import runner
import site_fit
import adjacency_golden_case as golden
import site_fit_golden_case as sg
from adjacency_helpers import MAX, enabled, with_classes
from hazards_helpers import enabled as hz_enabled
from zoning_helpers import REPO_ROOT, patch_rules, stored, verified

BASE = "595aa08"
RECORD = REPO_ROOT / "reviews" / "27_site_fit_buffers" / "golden_head_595aa08.json"
RESEARCH_SRC = (REPO_ROOT / "research.py").read_text(encoding="utf-8")


class DifferentialTests(unittest.TestCase):
    """BUF-03: gate off == 595aa08; gate on + empty table changes only the
    adjacency texts and adds one read (tests/adjacency_golden_check.py)."""

    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(RECORD.read_text(encoding="utf-8"))
        # reviews/30_d3_count_unlisted (D3U-12): the record + the explicit
        # expected diff, never a regenerated record.
        cls.want = golden.expected_after_d3u(cls.raw)

    def current(self):
        return json.loads(json.dumps(golden.run_case(research, site_fit, reporting),
                                     sort_keys=True, default=str))

    def test_gate_off_identical_to_base(self):
        self.assertFalse(adjacency.enabled())
        self.assertEqual(golden.differences_off(self.want, self.current()), [])

    def test_gate_on_empty_table(self):
        with enabled():
            got = self.current()
        self.assertEqual(golden.differences_on_empty(self.want, got, site_fit, adjacency), [])
        reads = [e for e in got["verified"]["executed"]
                 if e[0] == adjacency._SQL_ADJACENCY_FOR_PARCELS]
        self.assertEqual(len(reads), 1)       # one batched read, = ANY(%s)

    def test_d3u_expected_diff_is_exactly_the_change(self):
        # reviews/30_d3_count_unlisted: against the raw record only the two
        # listed sections differ, on exactly the listed parcels and fields.
        got = self.current()
        self.assertEqual(golden.differences_off(self.raw, got),
                         ["verified.memo", "verified.projections"])
        changed = set()
        for sec in ("projections", "memo"):
            for pid, a in self.raw["verified"][sec].items():
                for k, v in a.items():
                    if got["verified"][sec][pid][k] != v:
                        changed.add(("verified", sec, pid, k))
        self.assertEqual(changed, {e[:4] for e in golden.D3U_EXPECTED_DIFF})
        self.assertEqual(len(golden.D3U_EXPECTED_DIFF), 6)
        self.assertEqual(self.raw["draft"], got["draft"])
        self.assertEqual(self.raw["verified"]["scoring"], got["verified"]["scoring"])
        # The edit refuses a record that is not the expected base.
        with self.assertRaises(AssertionError):
            golden.expected_after_d3u(self.want)

    def test_record_is_the_base(self):
        try:
            base = subprocess.run(["git", "show", f"{BASE}:site_fit.py"], cwd=REPO_ROOT,
                                  capture_output=True, check=True, text=True).stdout
        except (subprocess.CalledProcessError, OSError):
            self.skipTest("git history unavailable")
        self.assertNotIn("import adjacency", base)
        self.assertIn("verified", self.want)
        self.assertIn("draft", self.want)


# ---------------------------------------------------------------------------
class FakeConn:
    """site_fit's reads: zoning, adjacency rows, the four buffer statements."""

    def __init__(self, zoning_rows, adj_rows=(), fail=()):
        self.zoning_rows, self.adj_rows, self.fail = zoning_rows, list(adj_rows), set(fail)
        self.executed = []

    def cursor(self):
        conn = self

        class Cur:
            rows: list = []

            def execute(self, sql, params=()):
                conn.executed.append((sql, params))
                if sql in conn.fail:
                    raise RuntimeError("injected")
                if sql == site_fit._SQL_ZONING_FOR_FIT:
                    self.rows = [(p,) + conn.zoning_rows[p] for p in params[0]
                                 if p in conn.zoning_rows]
                elif sql == adjacency._SQL_ADJACENCY_FOR_PARCELS:
                    self.rows = [r for r in conn.adj_rows if r[1] in params[0]]
                elif sql in (site_fit._SQL_BUFFERED_FOR_FIT, site_fit._SQL_BUFFERED_FOR_FIT_ADJ,
                             site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS,
                             site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS_ADJ):
                    ids = params[-1]
                    setback = 40.0
                    reduce = 110.0 if sql in (site_fit._SQL_BUFFERED_FOR_FIT_ADJ,
                                              site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS_ADJ) else 40.0
                    out = []
                    for p in ids:
                        w, h = 1000.0 - 2 * setback, 1000.0 - setback - reduce
                        gj = json.dumps({"type": "Polygon", "coordinates": [[
                            [2e6, 1e6], [2e6 + w, 1e6], [2e6 + w, 1e6 + h], [2e6, 1e6 + h],
                            [2e6, 1e6]]]})
                        r = (p, 1_000_000 * sg.M2_PER_SQFT, False, w * h, gj, False)
                        if "hz_sf" in sql:
                            r = r + ("absent", "absent", 0.0, False, False, True)
                        out.append(r)
                    self.rows = out
                else:
                    self.rows = []

            def fetchall(self):
                return list(self.rows)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return Cur()


def adj_row(aid, pid, district="R-3", kind="zoned", fresh=True):
    return (aid, pid, kind, district, "south_fulton" if kind == "zoned" else None,
            "south_fulton_zoning" if kind == "zoned" else "coverage", "2026-11-01",
            "2026-11-06", fresh, 1000.0)


SF = stored("south_fulton", "south_fulton_zoning", "M-1")
CLASSES = verified("south_fulton", mutate=with_classes("south_fulton", residential=["R-*"],
                                                     readings=MAX))


def project(conn, ids=("a", "b"), rules=CLASSES):
    site_fit._FIT_CACHE.clear()
    with patch_rules(rules):
        return site_fit.project_parcels(conn, list(ids))


class SiteFitTests(unittest.TestCase):
    """BUF-11: statement choice, named parameters, fallbacks, texts."""

    def test_gate_off_no_adjacency_sql(self):
        conn = FakeConn({"a": SF, "b": SF}, [adj_row(1, "a")])
        out = project(conn)
        sqls = [s for s, _ in conn.executed]
        self.assertNotIn(adjacency._SQL_ADJACENCY_FOR_PARCELS, sqls)
        self.assertEqual(sqls, [site_fit._SQL_ZONING_FOR_FIT, site_fit._SQL_BUFFERED_FOR_FIT])
        self.assertIn(site_fit.RES_BUFFERS, out["a"].constraints)
        self.assertEqual(out["a"].adjacency, adjacency.ADJ_OFF)
        self.assertEqual(site_fit.adjacency_text(out["a"]), "adjacency buffers not applied")

    def test_split_by_deduction_and_named_params(self):
        conn = FakeConn({"a": SF, "b": SF}, [adj_row(7, "a"), adj_row(8, "b", "M-1")])
        with enabled():
            out = project(conn)
        calls = [(s, p) for s, p in conn.executed]
        sqls = [s for s, _ in calls]
        self.assertEqual(sqls, [site_fit._SQL_ZONING_FOR_FIT, adjacency._SQL_ADJACENCY_FOR_PARCELS,
                                site_fit._SQL_BUFFERED_FOR_FIT, site_fit._SQL_BUFFERED_FOR_FIT_ADJ])
        plain_params = calls[2][1]
        self.assertEqual(plain_params, site_fit._buffered_params(40.0, ["b"]))
        adj_params = calls[3][1]
        self.assertEqual(len(adj_params), site_fit._SQL_BUFFERED_FOR_FIT_ADJ.count("%s"))
        named = dict(zip(site_fit._BUFFERED_ADJ_NAMES, adj_params))
        self.assertEqual(named["adj_ids"], [7])
        self.assertEqual(named["adj_dists"], [110.0])
        self.assertEqual(named["vertex_factor"], adjacency.VERTEX_FACTOR)
        self.assertEqual(named["ids"], ["a"])
        self.assertEqual(named["setback"], 40.0)
        self.assertIn("residential 110 ft on 1 side", out["a"].adjacency)
        self.assertNotIn(site_fit.RES_BUFFERS, out["a"].constraints)
        self.assertFalse(any(c.startswith("buffers listed for ") for c in out["a"].constraints))
        self.assertTrue(out["b"].adjacency.startswith("adjacency buffers applied"))
        self.assertLess(out["a"].buildable_sf, out["b"].buildable_sf)

    def test_hazards_and_adjacency_statement(self):
        conn = FakeConn({"a": SF}, [adj_row(7, "a")])
        with enabled(), hz_enabled():
            out = project(conn, ("a",))
        sqls = [s for s, _ in conn.executed]
        self.assertIn(site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS_ADJ, sqls)
        p = [pr for s, pr in conn.executed if s == site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS_ADJ][0]
        named = dict(zip(site_fit._BUFFERED_HAZARDS_ADJ_NAMES, p))
        self.assertEqual(named["floor"], hazards.NOISE_FLOOR_SF)
        self.assertEqual(named["adj_dists"], [110.0])
        self.assertEqual(out["a"].status, site_fit.OK)
        self.assertIn(hazards.STREAM_BUFFERS, out["a"].constraints)

    def test_adjacency_statement_failure_falls_back_labelled(self):
        conn = FakeConn({"a": SF}, [adj_row(7, "a")], fail={site_fit._SQL_BUFFERED_FOR_FIT_ADJ})
        with enabled():
            out = project(conn, ("a",))
        self.assertEqual(out["a"].status, site_fit.OK)
        self.assertEqual(out["a"].adjacency, adjacency.ADJ_UNREADABLE)
        self.assertIn(site_fit._SQL_BUFFERED_FOR_FIT, [s for s, _ in conn.executed])

    def test_read_failure_unreadable_no_deduction(self):
        conn = FakeConn({"a": SF}, [adj_row(7, "a")],
                        fail={adjacency._SQL_ADJACENCY_FOR_PARCELS})
        with enabled():
            out = project(conn, ("a",))
        self.assertEqual(out["a"].adjacency, adjacency.ADJ_UNREADABLE)
        self.assertNotIn(site_fit._SQL_BUFFERED_FOR_FIT_ADJ, [s for s, _ in conn.executed])

    def test_schema_problem_no_read(self):
        conn = FakeConn({"a": SF}, [adj_row(7, "a")])
        with enabled("CREATE TABLE parcel_adjacency (x int)"):
            out = project(conn, ("a",))
        self.assertNotIn(adjacency._SQL_ADJACENCY_FOR_PARCELS, [s for s, _ in conn.executed])
        self.assertEqual(out["a"].adjacency, adjacency.ADJ_UNREADABLE)

    def test_stale_and_unknown_deduct_nothing(self):
        conn = FakeConn({"a": SF, "b": SF}, [adj_row(7, "a", fresh=False),
                                             adj_row(8, "b", None, kind="uncovered")])
        with enabled():
            out = project(conn)
        self.assertNotIn(site_fit._SQL_BUFFERED_FOR_FIT_ADJ, [s for s, _ in conn.executed])
        self.assertEqual(out["a"].adjacency, adjacency.ADJ_STALE)
        self.assertIn("1 side adjacency unknown", out["b"].adjacency)

    def test_every_status_carries_the_adjacency_text_when_enabled(self):
        with enabled():
            for st in site_fit.STATUSES:
                p = site_fit._empty("a", st, "x", None)
                self.assertIn(adjacency.ADJ_NO_GEOMETRY, p.constraints, st)
                self.assertNotIn(site_fit.RES_BUFFERS, p.constraints)
        p = site_fit._empty("a", site_fit.ERROR, "x", None)
        self.assertIn(site_fit.RES_BUFFERS, p.constraints)

    def test_gate_off_texts_pinned_verbatim(self):
        self.assertEqual(site_fit.RES_BUFFERS, (
            "adjacency buffers not applied (residential or other-district neighbours; "
            "neighbour zoning in the DB is incomplete: only candidate parcels are stored)"))
        self.assertEqual(site_fit.NOT_MODELLED, (
            site_fit.RES_BUFFERS, "flood/wetlands not deducted (H4/H6 not wired)",
            "stormwater detention not deducted (no detention pond placed)",
            "car parking not modelled"))
        self.assertEqual(adjacency.ADJ_OFF, "adjacency buffers not applied")


class ComposedSqlTests(unittest.TestCase):
    """BUF-11 / SR-2: the adjacency statements are composed from the frozen
    ones, by name, and stay module-level %s constants."""

    def test_named_construction_reproduces_the_frozen_order(self):
        v = site_fit._buffer_values(25.0, ["a", "b"])
        self.assertEqual(site_fit._named_params(site_fit._BUFFERED_NAMES, v),
                         site_fit._buffered_params(25.0, ["a", "b"]))
        self.assertEqual(site_fit._named_params(site_fit._BUFFERED_HAZARDS_NAMES, v),
                         site_fit._buffered_params_hazards(25.0, ["a", "b"]))
        self.assertEqual(len(site_fit._BUFFERED_NAMES), site_fit._SQL_BUFFERED_FOR_FIT.count("%s"))
        self.assertEqual(len(site_fit._BUFFERED_HAZARDS_NAMES),
                         site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS.count("%s"))

    def test_composed_statements_embed_the_frozen_ones(self):
        for base, comp, names in (
                (site_fit._SQL_BUFFERED_FOR_FIT, site_fit._SQL_BUFFERED_FOR_FIT_ADJ,
                 site_fit._BUFFERED_ADJ_NAMES),
                (site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS, site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS_ADJ,
                 site_fit._BUFFERED_HAZARDS_ADJ_NAMES)):
            body = base[base.index(" FROM (SELECT parcel_id, gross_m2, inner_g"):]
            self.assertIn("(SELECT *" + body + ") c1", comp)
            self.assertEqual(comp.count("%s"), len(names))
            self.assertNotIn("{", comp)
            # review findings 5/6: flat ends, round joins, tight vertex polygons
            self.assertIn("'endcap=flat join=round quad_segs=2'", comp)
            self.assertIn(f"'quad_segs={adjacency.VERTEX_QUAD_SEGS}'", comp)
            self.assertNotIn("ST_Buffer(ST_Points(", comp)
            self.assertIn("unnest(%s::bigint[], %s::float8[])", comp)
            self.assertEqual(names[:3], ("adj_ids", "adj_dists", "vertex_factor"))
        with self.assertRaises(ValueError):
            site_fit._with_adjacency(("SELECT 1", ()), False)

    def test_compile_rejects_nothing_but_keeps_order(self):
        sql, names = site_fit._compile("a {x} b ", ("c %s", ("y",)), " {z}")
        self.assertEqual((sql, names), ("a %s b c %s %s", ("x", "y", "z")))
        with self.assertRaises(KeyError):
            site_fit._named_params(("missing",), {})


class ReportingTests(unittest.TestCase):
    """BUF-24: both footnote variants, the highlight clause."""

    def test_footnote_variants(self):
        self.assertEqual(reporting._projection_footnote(), reporting._PROJECTION_FOOTNOTE)
        with hz_enabled():
            self.assertEqual(reporting._projection_footnote(),
                             reporting._PROJECTION_FOOTNOTE_HAZARDS)
        with enabled():
            f = reporting._projection_footnote()
            self.assertIn(adjacency.FOOTNOTE, f)
            self.assertNotIn(site_fit.RES_BUFFERS, f)
            self.assertEqual(f, reporting._PROJECTION_FOOTNOTE.replace(site_fit.RES_BUFFERS,
                                                                       adjacency.FOOTNOTE))
            with hz_enabled():
                f = reporting._projection_footnote()
                self.assertIn(adjacency.FOOTNOTE, f)
                self.assertIn(hazards.STREAM_BUFFERS, f)

    def test_summary_clause(self):
        conn = FakeConn({"a": SF}, [adj_row(7, "a")])
        out = project(conn, ("a",))
        self.assertTrue(reporting._projection_summary(out["a"], True).endswith(
            "; adjacency buffers not applied)."))
        with enabled():
            out = project(conn, ("a",))
            line = reporting._projection_summary(out["a"], True)
        self.assertIn("residential 110 ft on 1 side", line)
        self.assertNotIn("fulton-", line)


# ---------------------------------------------------------------------------
class _RCur:
    def __init__(self, conn):
        self.conn, self._one = conn, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.conn.executes.append(sql)
        if sql == runner._SQL_ADJACENCY_FINGERPRINT:
            if self.conn.error:
                raise RuntimeError("relation parcel_adjacency does not exist")
            fps = self.conn.fingerprints
            self._one = fps.pop(0) if len(fps) > 1 else fps[0]
        elif "IS DISTINCT FROM" in sql:
            self._one = (0,)
        else:
            raise AssertionError(f"unexpected SQL: {sql[:80]}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return [self._one]


class _RConn:
    def __init__(self, fingerprints=((3, "a"),), error=False):
        self.fingerprints, self.error, self.executes, self.connections = \
            list(fingerprints), error, [], 0

    def cursor(self):
        return _RCur(self)

    def rollback(self):
        pass


def run_evaluate(conn):
    @contextmanager
    def get_connection():
        conn.connections += 1
        yield conn
    with mock.patch.object(costar_ingest, "run_ingestion_cycle", return_value={}), \
            mock.patch.object(research, "run_discovery_cycle", return_value={}), \
            mock.patch.object(research, "run_scoring_cycle", return_value={}), \
            mock.patch.object(reporting, "generate_strategy_memo",
                              return_value=Path("/tmp/memo.md")), \
            mock.patch.object(prepare, "verify_parameters_unchanged"), \
            mock.patch.object(prepare, "calculate_actionable_pipeline_count",
                              lambda c, run_tag=None: 2), \
            mock.patch.object(prepare, "calculate_confidence_weighted_pipeline",
                              lambda c, run_tag=None: 0.5), \
            mock.patch.object(prepare, "get_connection", get_connection):
        return runner.evaluate("atlanta", run_tag="t1", experiment_id="exp-1")


class RunnerAuditTests(unittest.TestCase):
    """BUF-12: a parcel_adjacency change during an experiment crashes it."""

    def test_gate_off_runs_nothing(self):
        conn = _RConn()
        result = run_evaluate(conn)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn(runner._SQL_ADJACENCY_FINGERPRINT, conn.executes)
        self.assertNotIn("adjacency_audit", result["sub_summaries"])
        self.assertEqual(conn.connections, 1)

    def test_unchanged_passes(self):
        conn = _RConn()
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["metric"], 2)
        self.assertEqual(result["sub_summaries"]["adjacency_audit"], {"rows": 3})
        self.assertEqual(conn.executes.count(runner._SQL_ADJACENCY_FINGERPRINT), 2)

    def test_row_change_mid_experiment_crashes(self):
        conn = _RConn(fingerprints=[(3, "a"), (3, "b")])
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "crash")
        self.assertEqual(result["metric"], 0)
        self.assertIn("parcel_adjacency changed during the experiment", result["error"])

    def test_fingerprint_covers_the_writing_transaction(self):
        """Review finding 2: a delete-and-restore with the same ids changes
        every restored row's xmin, so the fingerprint (live check (j))."""
        sql = runner._SQL_ADJACENCY_FINGERPRINT
        self.assertIn("a.xmin::text", sql)
        self.assertIn("a::text", sql)

    def test_unreadable_crashes(self):
        conn = _RConn(error=True)
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "crash")
        self.assertIn("unreadable", result["error"])


# ---------------------------------------------------------------------------
class StaticResearchTests(unittest.TestCase):
    """BUF-12: research.py never names the adjacency modules or table."""

    BANNED = ("parcel_adjacency", "adjacency_import", "_DDL_PARCEL_ADJACENCY")
    FRAGMENTS = ("adjacen",)
    # Today's harmless uses in research.py (S11 sub-score name and label, the
    # H5 docstring); anything else holding "adjacen" fails.
    ALLOWED = ("S11_rail_adjacency", "rail adjacency", "500 ft adjacency buffer")

    def problems(self, src: str) -> list[str]:
        import static_guards
        return static_guards.absent_module_problems(
            src, ("adjacency", "adjacency_import"), self.BANNED, self.FRAGMENTS, self.ALLOWED)

    def test_research_is_clean(self):
        self.assertEqual(self.problems(RESEARCH_SRC), [])

    def test_composed_strings_are_caught(self):
        """Review finding 2: no single literal names the table."""
        for bad in ('X = "DELETE FROM parcel_" + "adjacency"\n',
                    'X = "DELETE FROM parcel_%s" % "adjacency"\n',
                    'X = "".join(["parcel_", "adjacency"])\n',
                    'X = "DELETE FROM {}".format("parcel_" "adj" "acency")\n',
                    'A = "parcel"\nB = "_adj"\nX = f"DELETE FROM {A}{B}acency"\n',
                    'X = "DELETE FROM PARCEL_ADJACENCY"\n',
                    'X = "parcel_"\n',
                    'X = "adja" + "cency"\n',
                    'def f(cur):\n    t = "adjacency"\n    cur.execute("DELETE FROM parcel_" + t)\n'):
            self.assertNotEqual(self.problems(RESEARCH_SRC + "\n" + bad), [], bad)
        # the allow-list is exact: a new use of the word fails
        self.assertNotEqual(self.problems(RESEARCH_SRC + '\nX = "adjacency buffers"\n'), [])

    def test_planted_references_are_caught(self):
        import static_guards
        for bad in ("import adjacency\n", "import adjacency_import as x\n",
                    "from adjacency import plan_parcel\n", "x = site_fit._adj\n",
                    "SQL = 'SELECT * FROM parcel_adjacency'\n",
                    "def f():\n    return adjacency.enabled()\n",
                    "import site_fit as adjacency\n"):
            self.assertNotEqual(self.problems(RESEARCH_SRC + "\n" + bad), [], bad)

    def test_static_guard_lists(self):
        import static_guards
        self.assertIn("adjacency", static_guards.PROTECTED_NAMES)
        self.assertIn("adjacency_import", static_guards.REEXPORTING_MODULES)

    def test_site_fit_reaches_adjacency_only_through_its_alias(self):
        src = (REPO_ROOT / "site_fit.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("import adjacency as _adj\n"), 1)
        self.assertNotIn("adjacency_import", src)
        for name in ("reporting.py", "runner.py"):
            self.assertNotIn("import adjacency_import", (REPO_ROOT / name).read_text())


class ProtectionTests(unittest.TestCase):
    """BUF-22 / BUF-21: every protected-file list names the new modules; the
    human-owned files are byte-identical to the base."""

    def test_docs_name_the_protected_files(self):
        for doc in ("CLAUDE.md", "home_machine/agent_prompt.md", "AUTORESEARCH_MECHANICS.md",
                    "STANDING_RISKS.md", "START_HERE.md", "README.md"):
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            for needle in ("adjacency.py", "adjacency_import.py"):
                self.assertIn(needle, text, (doc, needle))
        for doc in ("CLAUDE.md", "AUTORESEARCH_MECHANICS.md", "STANDING_RISKS.md"):
            self.assertIn("reviews/27_site_fit_buffers", (REPO_ROOT / doc).read_text(), doc)
        sr1 = [l for l in (REPO_ROOT / "STANDING_RISKS.md").read_text().splitlines()
               if l.startswith("| SR-1 ")][0]
        self.assertIn("adjacency.py", sr1)
        self.assertIn("test_adjacency_scoring.ProtectionTests", sr1)

    def test_lists_in_code(self):
        import rules_scrape
        self.assertIn("adjacency.py", rules_scrape.PROTECTED_MODULES)
        self.assertIn("adjacency_import.py", rules_scrape.PROTECTED_MODULES)
        wf = (REPO_ROOT / ".github" / "workflows" / "discovery-fulton.yml").read_text()
        self.assertEqual(wf.count("- 'adjacency.py'"), 2)
        self.assertEqual(wf.count("- 'adjacency_import.py'"), 2)
        mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("adjacency-import:", mk)
        self.assertIn("python adjacency_import.py", mk)
        mirror = (REPO_ROOT / "tests" / "test_mirror.py").read_text(encoding="utf-8")
        self.assertIn('"adjacency.py"', mirror)
        self.assertIn('"adjacency_import.py"', mirror)

    def test_human_owned_and_prepare_byte_identical_to_base(self):
        for name in ("prepare.py", "parameters.json", "sources.json", "requirements.txt",
                     "program.md", "zoning_rules.json", "building_prototypes.json",
                     "rules_sources.json", "screener/rank.py"):
            try:
                base = subprocess.run(["git", "show", f"{BASE}:{name}"], cwd=REPO_ROOT,
                                      capture_output=True, check=True).stdout
            except (subprocess.CalledProcessError, OSError):
                self.skipTest("git history unavailable")
            self.assertEqual((REPO_ROOT / name).read_bytes(), base, name)

    def test_no_pipeline_module_writes_human_owned_files(self):
        writers = {"write_text", "write_bytes", "dump", "replace", "rename", "copy",
                   "copyfile", "copy2", "move", "touch", "unlink"}
        for name in ("adjacency.py", "adjacency_import.py"):
            tree = ast.parse((REPO_ROOT / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in writers:
                    self.fail(f"{name}:{node.lineno} writes a file")


if __name__ == "__main__":
    unittest.main()
