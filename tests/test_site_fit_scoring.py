"""research.py / runner.py / reporting.py wiring of site_fit (reviews/22_site_fit).

Offline (SR-6). The headline guarantee (SF-01): while no jurisdiction is
verified, scoring issues exactly the statements it issued before site_fit
existed, writes the same rows and flags, and is identical whether the
prototypes file is present, missing or malformed (a DIFFERENTIAL test on the
current research.py, so scoring experiments stay keepable). The one-off
proof against commit 28d3205 is tests/site_fit_golden_check.py (not in the
suite).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import tests  # noqa: F401 — suite guards
import prepare
import research
import site_fit
import site_fit_golden_case as golden
import zoning
from hazards_helpers import disabled as hazards_disabled
from zoning_helpers import REPO_ROOT, patch_rules, stored, verified

PROTOS_PATH = REPO_ROOT / "building_prototypes.json"


def _canon(x):
    return json.loads(json.dumps(x, sort_keys=True, default=str))


@contextmanager
def prototypes_state(problem: str):
    """site_fit as if building_prototypes.json were missing/malformed; the
    state keeps the real file's SHA so the cycle-entry sentinel passes."""
    real_sha = hashlib.sha256(PROTOS_PATH.read_bytes()).hexdigest()
    with mock.patch.object(site_fit, "_STATE", site_fit._State(None, real_sha, problem)):
        yield


@contextmanager
def site_fit_absent():
    """research.py as if site_fit did not exist: any projection call fails
    the test."""
    def boom(*a, **k):
        raise AssertionError("site_fit called")
    with mock.patch.object(site_fit, "project_parcel", boom), \
            mock.patch.object(site_fit, "project_parcels", boom), \
            mock.patch.object(site_fit, "meets_bts_footprint", boom):
        yield


def _site_fit_sqls():
    return {v for k, v in vars(site_fit).items() if k.startswith("_SQL_")}


class DraftDifferentialTests(unittest.TestCase):
    """SF-01: all-draft scoring never touches site_fit, in any file state."""

    @classmethod
    def setUpClass(cls):
        site_fit._FIT_CACHE.clear()
        with site_fit_absent():
            cls.absent = _canon(golden.run_case(research))

    def _assert_identical(self, got):
        self.assertEqual(got["per_parcel"], self.absent["per_parcel"])
        self.assertEqual(got["cycle"], self.absent["cycle"])
        sqls = _site_fit_sqls()
        for rec in list(got["per_parcel"].values()) + [got["cycle"]]:
            self.assertFalse([s for s, _ in rec["executed"] if s in sqls])

    def test_repo_files_all_draft_equal_site_fit_absent(self):
        self.assertFalse(zoning.any_verified())
        self._assert_identical(_canon(golden.run_case(research)))

    def test_missing_prototypes_file_changes_nothing(self):
        with prototypes_state("building_prototypes.json is missing"):
            self._assert_identical(_canon(golden.run_case(research)))

    def test_malformed_prototypes_file_changes_nothing(self):
        with prototypes_state("building_prototypes.json is not valid UTF-8 JSON"):
            self._assert_identical(_canon(golden.run_case(research)))

    def test_no_flag_text_mentions_site_fit_while_draft(self):
        got = _canon(golden.run_case(research))
        for rec in list(got["per_parcel"].values()) + [got["cycle"]]:
            for _, params in rec["executed"]:
                self.assertFalse(any("site fit" in str(p) for p in params))

    def test_cache_has_no_projection_while_draft(self):
        conn = golden.zg.AnswerConn(golden.responder(research))
        cache = research._prefetch_cycle_cache(conn, "atlanta", sorted(golden.PARCELS))
        self.assertIsNone(cache.projection)


def _verified_sf():
    return patch_rules(verified("south_fulton"))


# hazards is not the subject here: the gate is ON in the shipped repo since the
# operator-authorized prepare-mutation of 2026-11-09 (reviews/33_hazards_enabled);
# these fakes/records predate the hazard read, so they run with it OFF.
class VerifiedBtsTests(unittest.TestCase):
    """SF-02: with South Fulton verified, BTS for its parcels follows the
    projection; every other parcel and every other strategy is unchanged."""

    @classmethod
    def setUpClass(cls):
        hz = hazards_disabled()     # for setUpClass AND every test (R-02)
        hz.start()
        cls.addClassCleanup(hz.stop)
        site_fit._FIT_CACHE.clear()
        cls.draft = _canon(golden.run_case(research))
        with _verified_sf():
            site_fit._FIT_CACHE.clear()
            cls.mixed = _canon(golden.run_case(research))
            site_fit._FIT_CACHE.clear()
            cls.errors = _canon(golden.run_case(research, site_fit_errors=True))
            # verified S9 but no projection: isolates what site_fit changes
            with mock.patch.object(research, "_fetch_projection", lambda *a, **k: None):
                cls.noproj = _canon(golden.run_case(research))

    def bts(self, rec, pid):
        return rec["per_parcel"][pid]["result"]["strategy_fit"]["bts"]

    def test_projection_decides_bts_for_verified_parcels(self):
        d, m = self.draft, self.mixed
        # 8.60 ac but a 600 x 445 envelope fits: the proxy said N/A
        self.assertEqual(self.bts(d, "fulton-S01"), "N/A")
        self.assertNotEqual(self.bts(m, "fulton-S01"), "N/A")
        # 8.62 ac but too shallow for any envelope: the proxy said MODERATE
        self.assertEqual(self.bts(d, "fulton-S02"), "MODERATE")
        self.assertEqual(self.bts(m, "fulton-S02"), "N/A")
        # 145,600 SF projected (< 150K) -> N/A; 156,000 SF -> rated
        self.assertEqual(self.bts(m, "fulton-S09"), "N/A")
        self.assertEqual(self.bts(m, "fulton-S10"), self.bts(d, "fulton-S10"))
        # null geometry on a verified parcel: N/A, never the acreage proxy
        self.assertEqual(self.bts(d, "fulton-S05"), "MODERATE")
        self.assertEqual(self.bts(m, "fulton-S05"), "N/A")

    def test_draft_parcels_in_a_verified_cycle_are_unchanged(self):
        sf = {p for p, v in golden.PARCELS.items()
              if v[4] and "| south_fulton |" in v[4]}
        self.assertIn("fulton-S01", sf)
        for pid, rec in self.draft["per_parcel"].items():
            if pid in sf:
                continue
            g = self.mixed["per_parcel"][pid]
            self.assertEqual(g["result"], rec["result"], pid)
            self.assertEqual(g["parcel_score_inserts"], rec["parcel_score_inserts"], pid)
            self.assertEqual(g["research_log_notes"], rec["research_log_notes"], pid)
            self.assertFalse([s for s, _ in g["executed"] if s in _site_fit_sqls()], pid)

    def test_ground_lease_keeps_the_acreage_proxy(self):
        # Against verified zoning WITHOUT projections (S9 moves either way):
        # the projection changes BTS only, never ground lease or the others.
        for pid in golden.PARCELS:
            for k in ("ground_lease", "spec", "land_bank", "flip"):
                self.assertEqual(
                    self.mixed["per_parcel"][pid]["result"]["strategy_fit"][k],
                    self.noproj["per_parcel"][pid]["result"]["strategy_fit"][k], (pid, k))
            self.assertEqual(self.mixed["per_parcel"][pid]["result"]["sub_scores"],
                             self.noproj["per_parcel"][pid]["result"]["sub_scores"], pid)

    def test_both_paths_agree_and_the_cycle_batches(self):
        per = {p: r["result"]["strategy_fit"] for p, r in self.mixed["per_parcel"].items()}
        cyc = {p["parcel_id"]: p["strategy_fit"] for p in self.mixed["cycle"]["summary"]["parcels"]}
        self.assertEqual(per, cyc)
        sqls = [s for s, _ in self.mixed["cycle"]["executed"]]
        self.assertEqual(sqls.count(site_fit._SQL_ZONING_FOR_FIT), 1)
        self.assertGreaterEqual(sqls.count(site_fit._SQL_BUFFERED_FOR_FIT), 1)

    def test_failed_reads_only_lower_bts_and_flag_once(self):
        for pid, rec in self.errors["per_parcel"].items():
            if self.bts(self.mixed, pid) == "N/A":
                self.assertEqual(self.bts(self.errors, pid), "N/A", pid)
        self.assertEqual(self.bts(self.errors, "fulton-S01"), "N/A")
        self.assertEqual(self.bts(self.errors, "fulton-S10"), "N/A")
        flags = [p for s, p in self.errors["per_parcel"]["fulton-S10"]["executed"]
                 if any("site fit projection error" in str(x) for x in p)]
        self.assertEqual(len(flags), 1)
        flags = [p for s, p in self.mixed["per_parcel"]["fulton-S05"]["executed"]
                 if any("site fit projection no_geometry" in str(x) for x in p)]
        self.assertEqual(len(flags), 1)
        self.assertEqual(self.errors["cycle"]["summary"]["counts"]["error"], 0)


class TruthTableTests(unittest.TestCase):
    """SF-02 truth table: verified / draft / unknown x acreage x projected SF."""

    SUB = {"S4_submarket_vacancy": 8, "S5_submarket_absorption": 8,
           "S9_entitlement_complexity": 5, "S1_interstate_proximity": None}

    def proj(self, sf):
        fits = ()
        if sf is not None:
            fits = (site_fit.PrototypeFit("front", "front-load", True, False, 600.0, 260.0, 445.0,
                                          sf, 41, 50, 10.0, "geometry", 0.0),)
        return site_fit._issue(site_fit.Projection(
            "p", "ok", "projected", "verified rules", True, 40.0, "", 10 ** 6, 10 ** 6, None,
            fits, (), setback_from_rules=True))

    def test_table(self):
        for gate in ("verified", "draft", "unknown"):
            for acres in (8.60, 8.62):
                for sf in (149_999, 150_000, None):
                    projection = self.proj(sf) if gate == "verified" else None
                    fit = research._compute_strategy_fit(self.SUB, acres, projection=projection)
                    if gate == "verified":
                        want = "MODERATE" if sf == 150_000 else "N/A"
                    else:
                        want = "MODERATE" if acres >= 8.61 else "N/A"
                    self.assertEqual(fit["bts"], want, (gate, acres, sf))
                    self.assertEqual(fit["ground_lease"], "WEAK" if acres >= 8.61 else "N/A",
                                     (gate, acres, sf))

    def test_without_projection_identical_to_the_old_signature(self):
        for acres in (None, 0, 5.0, 8.60, 8.62, 30.0):
            self.assertEqual(research._compute_strategy_fit(self.SUB, acres),
                             research._compute_strategy_fit(self.SUB, acres, projection=None))

    def test_gate_is_the_s9_gate(self):
        with _verified_sf():
            self.assertTrue(research._zoning_verified(stored("south_fulton", "s", "M-1"), "p"))
            # R-3 is unlisted and South Fulton's unlisted_districts is 'unknown'
            self.assertFalse(research._zoning_verified(stored("south_fulton", "s", "R-3"), "p"))
            self.assertTrue(research._zoning_verified(stored("south_fulton", "s", "AG-1"), "p"))
            self.assertFalse(research._zoning_verified(stored("fairburn", "s", "M-1"), "p"))
            self.assertFalse(research._zoning_verified(None, "p"))
        with patch_rules(verified("east_point")):     # verified but class unknown
            self.assertFalse(research._zoning_verified(stored("east_point", "s", "I1"), "p"))


class CycleEntryTests(unittest.TestCase):
    """SF-04: a changed prototypes file aborts both cycles before connecting;
    runner.evaluate checks the committed file before any sub-cycle."""

    def test_changed_prototypes_abort_both_cycles_before_any_work(self):
        boom = site_fit.PrototypesChanged("changed")
        for fn in (lambda: research.run_scoring_cycle("atlanta", run_tag="t", experiment_id="e"),
                   lambda: research.run_discovery_cycle("atlanta")):
            with mock.patch.object(site_fit, "verify_prototypes_unchanged", side_effect=boom), \
                    mock.patch.object(prepare, "get_connection",
                                      side_effect=AssertionError("connected")):
                with self.assertRaises(site_fit.PrototypesChanged):
                    fn()

    def test_score_parcel_verifies_before_reading(self):
        conn = golden.zg.AnswerConn(golden.responder(research))
        with mock.patch.object(site_fit, "verify_prototypes_unchanged",
                               side_effect=site_fit.PrototypesChanged("x")):
            with self.assertRaises(site_fit.PrototypesChanged):
                research.score_parcel("fulton-S01", conn=conn)
        self.assertEqual(conn.executed, [])

    def test_evaluate_checks_the_committed_prototypes_before_any_cycle(self):
        import runner
        boom = site_fit.PrototypesChanged("differs from git HEAD")
        with mock.patch.object(site_fit, "verify_prototypes_committed", side_effect=boom) as v, \
                mock.patch.object(research, "run_discovery_cycle",
                                  side_effect=AssertionError("discovery ran")), \
                mock.patch.object(research, "run_scoring_cycle",
                                  side_effect=AssertionError("scoring ran")):
            with self.assertRaises(site_fit.PrototypesChanged):
                runner.evaluate("atlanta", skip_ingestion=True, skip_memo=True,
                                run_tag="t", experiment_id="e")
        v.assert_called_once_with()
        src = (REPO_ROOT / "runner.py").read_text(encoding="utf-8")
        body = src.split("def evaluate(", 1)[1].split("\n    try:", 1)[0]
        self.assertLess(body.index("zoning.verify_rules_committed()"),
                        body.index("site_fit.verify_prototypes_committed()"))
        self.assertLess(body.index("site_fit.verify_prototypes_committed()"),
                        body.index("research.get_api_call_count()"))


class StaticResearchTests(unittest.TestCase):
    """SF-03 / SF-16: research.py cannot compute, override or inflate the
    projection, and never touches the prototypes file."""

    SRC = (REPO_ROOT / "research.py").read_text(encoding="utf-8")
    TREE = ast.parse(SRC)
    # reviews/29_operator_decisions (D3-05): bts_review_text is the ONLY
    # static-guard change of that round.
    ALLOWED = {"project_parcel", "project_parcels", "meets_bts_footprint", "data_gap_text",
               "verify_prototypes_unchanged", "bts_review_text"}
    FIELDS = {f for cls in (site_fit.Projection, site_fit.PrototypeFit)
              for f in cls.__dataclass_fields__} - {"status", "reason", "label", "key"}

    def test_only_the_public_api_is_used(self):
        used = {n.attr for n in ast.walk(self.TREE)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "site_fit"}
        self.assertEqual(used - self.ALLOWED, set())
        self.assertIn("meets_bts_footprint", used)

    def test_no_assignment_to_site_fit_or_a_result(self):
        for node in ast.walk(self.TREE):
            targets = []
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Attribute):
                        self.assertFalse(isinstance(sub.value, ast.Name) and
                                         sub.value.id == "site_fit", node.lineno)
                        self.assertNotIn(sub.attr, self.FIELDS, node.lineno)
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id == "setattr" and node.args:
                    self.assertFalse(isinstance(node.args[0], ast.Name) and
                                     node.args[0].id == "site_fit", node.lineno)
                if isinstance(f, ast.Attribute) and f.attr in ("__setattr__", "_replace"):
                    self.fail(f"{f.attr} at line {node.lineno}")
                if isinstance(f, ast.Attribute) and f.attr == "replace" and \
                        isinstance(f.value, ast.Name) and f.value.id == "dataclasses":
                    self.fail(f"dataclasses.replace at line {node.lineno}")

    def test_no_arithmetic_or_comparison_on_projection_fields(self):
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, self.FIELDS, node.lineno)
        self.assertNotIn("_FIT_CACHE", self.SRC)
        self.assertNotIn("_STATE", self.SRC)

    def test_projection_calls_pass_only_conn_and_parcel_ids(self):
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                    isinstance(node.func.value, ast.Name) and node.func.value.id == "site_fit":
                if node.func.attr in ("project_parcel", "project_parcels"):
                    self.assertEqual(len(node.args), 2, node.lineno)
                    self.assertEqual(node.keywords, [], node.lineno)

    def test_never_opens_or_loads_the_prototypes_file(self):
        consts = [c.value for n in ast.walk(self.TREE) if isinstance(n, (ast.Call, ast.Assign))
                  for c in ast.walk(n) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
        self.assertFalse([c for c in consts if "building_prototypes" in c])
        self.assertNotIn("PROTOTYPES_PATH", self.SRC)
        self.assertNotIn("load_prototypes", self.SRC)
        self.assertNotIn("_BTS_MIN_FOOTPRINT", self.SRC)
        # the 150K threshold lives in site_fit; research.py's only 150,000 is
        # the unchanged acreage proxy _BTS_MIN_ACRES
        proxy_lines = {n.lineno for n in ast.walk(self.TREE) if isinstance(n, ast.AnnAssign)
                       and getattr(n.target, "id", "") == "_BTS_MIN_ACRES"}
        lines = {c.lineno for c in ast.walk(self.TREE) if isinstance(c, ast.Constant)
                 and not isinstance(c.value, bool) and c.value in (150_000, 150_000.0)}
        self.assertEqual(len(proxy_lines), 1)
        self.assertEqual(lines, proxy_lines)

    # Review findings 1 and 3: the checks above see only ``site_fit.<attr>``,
    # so research.py must reach site_fit in no other way (tests/static_guards).
    BANNED = {"Projection", "PrototypeFit", "_Placement", "_State", "_Rules", "_ISSUED",
              "_issue", "_FIT_CACHE", "_STATE", "BTS_MIN_FOOTPRINT_SF"}
    REPROS = {
        "alias": "import site_fit as _sf\n_sf.BTS_MIN_FOOTPRINT_SF = 1\n",
        "from_import": "def _inflate(pid):\n    from site_fit import Projection as _P, "
                       "PrototypeFit as _F\n    return _P(pid)\n",
        "from_cache": "from site_fit import _FIT_CACHE\n",
        "importlib": "import importlib\n_m = importlib.import_module('site_fit')\n",
        "sys_modules": "import sys\nsys.modules['site_fit'].BTS_MIN_FOOTPRINT_SF = 1\n",
        "getattr_computed": "_s = getattr(site_fit, '_ST' + 'ATE')\n",
        "bare_ref": "x = site_fit\n",
        "vars": "vars(site_fit)['BTS_MIN_FOOTPRINT_SF'] = 1\n",
        "dunder_dict": "site_fit.project_parcel.__globals__['BTS_MIN_FOOTPRINT_SF'] = 1\n",
        "reexport": "import reporting\nreporting.site_fit.BTS_MIN_FOOTPRINT_SF = 1\n",
        "reexport_getattr": "import connector_harness\ngetattr(connector_harness, 'site_fit')\n",
        "dunder_import": "__import__('site_fit').BTS_MIN_FOOTPRINT_SF = 1\n",
        "exec": "exec('import site_fit as s')\n",
        "shadow": "def f(site_fit):\n    return site_fit.project_parcel(1, 2)\n",
        "construct_by_type": "def f(p):\n    return type(p)(*p.__dict__.values())\n",
    }

    def problems(self, src):
        import static_guards
        return static_guards.module_reference_problems(src, "site_fit", self.ALLOWED,
                                                       self.BANNED)

    def test_research_reaches_site_fit_only_through_the_allowed_attributes(self):
        self.assertEqual(self.problems(self.SRC), [])

    def test_the_reviewer_repros_fail_the_guard(self):
        for name, extra in self.REPROS.items():
            src = self.SRC.replace("import site_fit  #", extra + "import site_fit  #", 1)
            self.assertNotEqual(self.problems(src), [], name)
        self.assertNotEqual(self.problems(self.SRC.replace("import site_fit  #", "#", 1)), [])

    def test_a_forged_projection_never_passes_at_runtime(self):
        # Finding 3's exact repro, executed: built outside site_fit, it fails.
        ns = {}
        exec("from site_fit import Projection as _P, PrototypeFit as _F\n"
             "def _inflate(pid):\n"
             "    f = _F('cross', 'cross-dock', True, True, 1000.0, 520.0, 890.0, 520000, 148, "
             "166, 10.0, 'full size', 0.0)\n"
             "    return _P(pid, 'ok', 'projected', 'verified rules', True, 25.0, '', 1, 1, "
             "None, (f,), (), setback_from_rules=True)\n", ns)
        self.assertFalse(site_fit.meets_bts_footprint(ns["_inflate"]("x")))

    def test_bts_branch_is_one_guarded_switch(self):
        body = self.SRC.split("def _assess_strategy_bts(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if projection is not None:\n        if not site_fit.meets_bts_footprint"
                      "(projection):\n            return \"N/A\"\n    elif not acreage or "
                      "float(acreage) < _BTS_MIN_ACRES:\n        return \"N/A\"", body)
        gl = self.SRC.split("def _assess_strategy_ground_lease(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("projection", gl)
        self.assertIn("_BTS_MIN_ACRES", gl)


class ProtectionTests(unittest.TestCase):
    """SF-04 / SF-19 / SF-20: the file is human-only; every list names it."""

    PIPELINE = ("research.py", "runner.py", "costar_ingest.py", "costar_import.py",
                "reporting.py", "pipeline_common.py", "zoning.py", "zoning_import.py",
                "connector_harness.py", "cli.py", "prepare.py", "site_fit.py",
                "rules_scrape.py", "hazards.py", "hazards_import.py", "adjacency.py",
                "adjacency_import.py")

    def test_no_pipeline_module_writes_the_prototypes_file(self):
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
                attrs = [n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)]
                if not (any("building_prototypes" in c for c in consts)
                        or "PROTOTYPES_PATH" in names + attrs):
                    continue
                f = node.func
                if isinstance(f, ast.Name) and f.id == "open":
                    mode = node.args[1].value if len(node.args) > 1 and isinstance(
                        node.args[1], ast.Constant) else "r"
                    self.assertFalse(set(str(mode)) & set("wax+"), (name, node.lineno))
                if isinstance(f, ast.Attribute):
                    self.assertNotIn(f.attr, writers, (name, node.lineno))

    def test_site_fit_sql_is_constant_and_parameterised(self):
        tree = ast.parse((REPO_ROOT / "site_fit.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                    node.func.attr == "execute":
                self.assertIsInstance(node.args[0], (ast.Name, ast.Attribute, ast.Constant),
                                      node.lineno)

    def test_docs_name_the_protected_files(self):
        for doc in ("CLAUDE.md", "home_machine/agent_prompt.md", "AUTORESEARCH_MECHANICS.md",
                    "STANDING_RISKS.md", "START_HERE.md", "README.md"):
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            for needle in ("building_prototypes.json", "site_fit.py"):
                self.assertIn(needle, text, (doc, needle))
        for doc in ("CLAUDE.md", "AUTORESEARCH_MECHANICS.md", "STANDING_RISKS.md"):
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            self.assertIn("reviews/22_site_fit", text, doc)
        sr1 = [l for l in (REPO_ROOT / "STANDING_RISKS.md").read_text().splitlines()
               if l.startswith("| SR-1 ")][0]
        self.assertIn("building_prototypes.json", sr1)
        self.assertIn("site_fit.py", sr1)
        self.assertIn("test_site_fit_scoring.ProtectionTests", sr1)

    def test_ci_path_filters(self):
        wf = (REPO_ROOT / ".github" / "workflows" / "discovery-fulton.yml").read_text()
        self.assertEqual(wf.count("- 'site_fit.py'"), 2)
        self.assertEqual(wf.count("- 'building_prototypes.json'"), 2)
        self.assertIn("python tests/live_site_fit_check.py", wf)

    def test_human_owned_hash_list_includes_the_file(self):
        import zoning_helpers
        self.assertIn("building_prototypes.json", zoning_helpers.HUMAN_OWNED)


# hazards is not the subject here: the gate is ON in the shipped repo since the
# operator-authorized prepare-mutation of 2026-11-09 (reviews/33_hazards_enabled);
# these fakes/records predate the hazard read, so they run with it OFF.
@hazards_disabled()
class ReportingTests(unittest.TestCase):
    """SF-15: snapshot development potential and memo highlight lines."""

    def proj(self, conn_inner, zoning_row=golden.SF_M1):
        from test_site_fit import FakeConn
        site_fit._FIT_CACHE.clear()
        return site_fit.project_parcel(FakeConn({"a": zoning_row}, conn_inner), "a")

    def render(self, p, zoning_row=golden.SF_M1):
        import reporting
        parcel = {"parcel_id": "a", "zoning": zoning_row[0], "zoning_description": zoning_row[1]}
        return reporting._render_development_potential(parcel, p)

    def test_golden_markdown(self):
        from test_site_fit import inner_rect
        p = self.proj({"a": (1_000_000, inner_rect(700, 460), 322_000.0)})
        want_draft = "\n".join([
            "- **Estimated buildable area**: 40% coverage of land per program.md: 400,000 SF; "
            "net of setbacks: 322,000 SF",
            "- **Estimated max building SF**: 166,400 SF (front-load, 640 x 260 ft, draft rules)",
            "- **Building projection** (draft rules; used for BTS: no (draft rules); "
            "flood/wetlands not deducted (H4/H6 not wired)):",
            "",
            "| Prototype | Building SF | L x W (ft) | Docks | Trailer stalls | Coverage | Fit |",
            "|---|---|---|---|---|---|---|",
            "| cross-dock | — | — | — | — | — | does not fit: geometry (needs 600 x 890 ft "
            "envelope) |",
            "| front-load | 166,400 | 640 x 260 | 44 | 53 | 16.6% | full size |",
            "| rear-load | 166,400 | 640 x 260 | 44 | 53 | 16.6% | full size |",
            "",
            "- **Constraints applied**: setback 40 ft all sides = max(front/side/rear), "
            "south_fulton M-1, draft rules; full envelope (building + truck courts + trailer "
            "parking) inside the setback line, stricter than SiteFit, which lets courts and "
            "trailer parking sit in the setback; truck drive and circulation not modelled; no "
            "lot coverage cap in the rules; buffers listed for south_fulton M-1, not applied: "
            "plus_improvement_setback 10 ft (within the 40 ft setback), rear 100 ft, side 50 ft; "
            "height not checked (clear 40/36 ft; district max 100 ft or 8 stories, whichever is "
            "higher); adjacency buffers not applied (residential or other-district neighbours; "
            "neighbour zoning in the DB is incomplete: only candidate parcels are stored); "
            "flood/wetlands not deducted (H4/H6 not wired); stormwater detention not deducted "
            "(no detention pond placed); car parking not modelled",
        ])
        self.assertEqual(self.render(p), want_draft)
        self.assertEqual(self.render(p), self.render(p))          # deterministic
        with _verified_sf():
            pv = self.proj({"a": (1_000_000, inner_rect(700, 460), 322_000.0)})
            got = self.render(pv)
        self.assertIn("(verified rules; used for BTS: yes (verified rules); flood/wetlands", got)
        self.assertIn("166,400 SF (front-load, 640 x 260 ft, verified rules)", got)

    def test_no_geometry_and_empty(self):
        p = self.proj({})
        out = self.render(p)
        self.assertIn("- **Estimated buildable area**: 40% coverage of land per program.md: —; "
                      "net of setbacks: —", out)
        self.assertIn("- **Estimated max building SF**: — (no parcel geometry)", out)
        self.assertIn("  - — (no parcel geometry)", out)
        self.assertIn(site_fit.FLOOD_WETLANDS, out)
        self.assertIn(site_fit.RES_BUFFERS, out)
        p = self.proj({"a": (300_000, None, 0.0)})
        out = self.render(p)
        self.assertIn("net of setbacks: 0 SF", out)
        self.assertIn("— (nothing left inside the setbacks)", out)
        import reporting
        self.assertEqual(reporting._render_development_potential({}, None),
                         "- **Building projection**: — (projection unavailable)")

    def test_used_for_bts_label_matches_the_scoring_gate(self):
        # Review finding 9: the memo's protected copy of the gate must agree
        # with research._zoning_verified on every class and rules status.
        import reporting
        from zoning_helpers import patch_rules, stored, verified
        rules = zoning.load_rules()["jurisdictions"]
        cases = [(stored(j, f"{j}_zoning", c), f"{j}-{c}") for j, jj in rules.items()
                 for c in list(jj["districts"]) + ["R-4", "ZZ-9"]]
        cases += [(("M-1", None), "raw"), ((None, None), "none"), (("", "-"), "blank")]
        seen = set()
        for data in (None, verified(*rules), verified("south_fulton", "fairburn")):
            with patch_rules(data):
                for (z, d), pid in cases:
                    want = research._zoning_verified((z, d), pid)
                    got = reporting._used_for_bts({"parcel_id": pid, "zoning": z,
                                                   "zoning_description": d})
                    self.assertEqual(got, want, (pid, data is None))
                    seen.add(want)
        self.assertEqual(seen, {True, False})

    def test_memo_footnote_lists_what_projections_leave_out(self):
        # Review finding 8: buffers (and the other not-modelled items) are
        # stated in the memo, once, under the highlights.
        import reporting
        for needle in (site_fit.RES_BUFFERS, site_fit.FLOOD_WETLANDS, site_fit.DETENTION,
                       site_fit.CAR_PARKING, site_fit.FIT_RULE, "height not checked"):
            self.assertIn(needle, reporting._PROJECTION_FOOTNOTE)
        src = (REPO_ROOT / "reporting.py").read_text(encoding="utf-8")
        self.assertIn("if projections is not None:\n            top_lines += [\"\", "
                      "_PROJECTION_FOOTNOTE]", src)

    def test_memo_line(self):
        import reporting
        from test_site_fit import inner_rect
        p = self.proj({"a": (1_000_000, inner_rect(700, 460), 322_000.0)})
        self.assertEqual(reporting._projection_summary(p, False),
                         "Projected max building: 166,400 SF front-load (draft rules; setback "
                         "40 ft all sides; used for BTS: no; flood/wetlands not deducted (H4/H6 "
                         "not wired); adjacency buffers not applied).")
        self.assertEqual(reporting._projection_summary(None, False),
                         "Projected max building: — (projection unavailable).")

    def test_snapshot_template_has_the_section(self):
        src = (REPO_ROOT / "reporting.py").read_text(encoding="utf-8")
        self.assertIn("### Development Potential\n{dev_md}\n\n### Strategy Fit Assessment", src)


if __name__ == "__main__":
    unittest.main()
