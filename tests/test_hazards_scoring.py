"""H4/H6 wired into scoring, discovery, site fit and reporting
(reviews/26_flood_wetlands). Offline (SR-6): answer-by-SQL fakes; the live
PostGIS checks are tests/live_hazards_check.py.

Differential, so ordinary scoring experiments stay keepable: the current
research.py with the hazard table OFF (the shipped state) against the same
code with it ON over a mixed fixture."""

from __future__ import annotations

import ast
import json
import re
import unittest
from contextlib import contextmanager
from unittest import mock

import tests  # noqa: F401  (suite guards)
import hazards
import prepare
import research
import site_fit
import site_fit_golden_case as sg
import zoning_golden_case as zg
from hazards_helpers import db_row, enabled, row
from static_guards import module_reference_problems, prepare_write_problems
from zoning_helpers import REPO_ROOT

RESEARCH_SRC = (REPO_ROOT / "research.py").read_text(encoding="utf-8")
ALLOWED = ("enabled", "assess_parcels", "GAP_RESOLUTION", "REVIEW_FLAG_TYPE",
           "REVIEW_RESOLUTION")

AE = dict(sfha_sf=5000, nfhl_zone_sf={"AE||T": 5000, "X|AREA OF MINIMAL FLOOD HAZARD|F": 95000},
          zones=["AE", "X"])
# parcel -> read-row overrides (None = no parcel_hazards row at all)
MIXED = {
    "fulton-Z06": AE,                                                   # PASS >= 70 -> FAIL
    "fulton-Z11": dict(sfha_sf=3000, floodway_sf=3000,
                       nfhl_zone_sf={"AE|FLOODWAY|T": 3000}),            # PASS >= 70 -> FAIL
    "fulton-S05": dict(wetland_sf=25000),                               # PASS < 70 -> FAIL
    "fulton-Z02": dict(AE, lomr_status="intersects",
                       nfhl_zone_lomr_sf={"AE||T": 5000}),               # review, stays PASS
    "fulton-Z01": dict(x500_sf=40000),                                  # passes
    "fulton-Z12": dict(AE, geom_md5="changed"),                         # stale: gap pass
    "fulton-Z05": None,                                                 # absent: gap pass
    "fulton-S08": dict(wetland_sf=10000),                               # minor fringe review
    "fulton-Z03": AE,                                                   # already a block
}
FAILS = {"fulton-Z06": "H4: FEMA SFHA zone AE", "fulton-Z11": "H4: FEMA regulatory floodway",
         "fulton-S05": "H6: NWI-mapped wetlands cover more than 20%"}


def hazard_rows(ids, mixed=MIXED, open_reviews=None):
    out = []
    for pid in sorted(ids):
        spec = mixed.get(pid, {})
        if spec is None:
            r = row(parcel_id=pid, nfhl_md5=None, nfhl_fetched_at=None, nwi_md5=None,
                    nwi_fetched_at=None)
        else:
            r = row(parcel_id=pid, **spec)
        if open_reviews:
            r["open_reviews"] = open_reviews.get(pid)
        out.append(db_row(r))
    return out


def responder(mixed=MIXED, open_reviews=None, seen=None):
    base = sg.responder(research)

    def answer(sql, params):
        if sql == hazards._SQL_HAZARDS_FOR_PARCELS:
            if seen is not None:
                seen.append(params)
            return hazard_rows(params[-1], mixed, open_reviews)
        return base(sql, params)
    return answer


def run_both(answer):
    """Per-parcel and cycle path over the golden parcels (site-fit golden case)."""
    params = prepare.get_parameters()
    research._OZ_TRACTS_CACHE = None
    out = {"per_parcel": {}, "cycle": None}
    for pid in sorted(sg.PARCELS):
        conn = zg.AnswerConn(answer)
        res = research.score_parcel(pid, conn=conn, cycle_id="score-atlanta-hz", params=params,
                                    run_tag="golden", experiment_id="exp-golden")
        out["per_parcel"][pid] = {"result": zg._jsonable(res), **zg.observe(conn, research)}
    conn = zg.AnswerConn(answer)

    @contextmanager
    def gc():
        yield conn
    with mock.patch.object(prepare, "get_connection", gc), \
            mock.patch.object(research, "_make_scoring_cycle_id", return_value="score-hz-cycle"):
        summary = research.run_scoring_cycle("atlanta", run_tag="golden", experiment_id="exp-golden")
    out["cycle"] = {"summary": zg._jsonable(summary), **zg.observe(conn, research)}
    return out


def metric(inserts):
    thr = prepare.get_parameters()["composite_threshold"]
    return sum(1 for p in inserts if p[3] == "PASS" and p[1] is not None and p[1] >= thr)


def flags(rec, flag_type=None):
    return [p for s, p in rec["executed"] if "INSERT INTO flagged_items" in s
            and (flag_type is None or p[0] == flag_type)]


# ---------------------------------------------------------------------------
class AbsentTests(unittest.TestCase):
    """FW-03: with the gate off nothing hazard-related is reachable."""

    def test_no_hazard_sql_and_no_hazard_call(self):
        with mock.patch.object(hazards, "assess_parcels", side_effect=AssertionError("called")):
            got = run_both(sg.responder(research))
        for rec in list(got["per_parcel"].values()) + [got["cycle"]]:
            for sql, _ in rec["executed"]:
                self.assertNotIn("parcel_hazards", sql)
                self.assertNotIn("flag_type = 'review'", sql)

    def test_discovery_stubs_unchanged(self):
        r4 = research._h4_flag({}, None, {})
        r6 = research._h6_filter({}, None, {})
        self.assertEqual((r4.action, r4.reason),
                         ("flag", "H4 flood unjoined: pending FEMA NFIP wiring (Phase 5+)"))
        self.assertEqual((r6.action, r6.reason),
                         ("flag", "H6 wetlands unjoined: pending USGS NWI mapper wiring (Phase 5+)"))

    def test_site_fit_and_reporting_texts_unchanged(self):
        import reporting
        self.assertEqual(site_fit._flood_text(None), site_fit.FLOOD_WETLANDS)
        self.assertEqual(reporting._flood_lines({}), (
            "not yet wired (Phase 11+ wires FEMA NFIP)", "not yet wired (Phase 11+ wires USGS NWI)"))
        self.assertNotIn(hazards.STREAM_BUFFERS, site_fit._base_constraints(None))


class PresentTests(unittest.TestCase):
    """FW-02 / FW-04 / FW-17: the gate on, a mixed fixture, both paths."""

    @classmethod
    def setUpClass(cls):
        cls.off = run_both(sg.responder(research))
        with enabled():
            cls.on = run_both(responder())

    def test_hazard_fails_through_gate_4_on_both_paths(self):
        for pid, want in FAILS.items():
            res = self.on["per_parcel"][pid]["result"]
            self.assertEqual(res["actionability"], "FAIL:deal_killer", pid)
            self.assertTrue(res["actionability_blockers"]["deal_killer"].startswith(want), pid)
        cyc = {r["parcel_id"]: r for r in self.on["cycle"]["summary"]["parcels"]}
        for pid in sg.PARCELS:
            self.assertEqual(cyc[pid]["actionability"],
                             self.on["per_parcel"][pid]["result"]["actionability"], pid)
            self.assertEqual(cyc[pid]["actionability_blockers"],
                             self.on["per_parcel"][pid]["result"]["actionability_blockers"], pid)

    def test_everything_else_identical(self):
        for pid in sg.PARCELS:
            a = self.off["per_parcel"][pid]["parcel_score_inserts"][0]
            b = self.on["per_parcel"][pid]["parcel_score_inserts"][0]
            if pid in FAILS:
                self.assertEqual([a[i] for i in (0, 1, 2, 5, 6, 7, 9, 10)],
                                 [b[i] for i in (0, 1, 2, 5, 6, 7, 9, 10)], pid)
            else:
                self.assertEqual(a, b, pid)

    def test_metric_drops_by_the_hazard_fails_only(self):
        off = [r["parcel_score_inserts"][0] for r in self.off["per_parcel"].values()]
        on = [r["parcel_score_inserts"][0] for r in self.on["per_parcel"].values()]
        thr = prepare.get_parameters()["composite_threshold"]
        expected = sum(1 for p in FAILS
                       if self.off["per_parcel"][p]["result"]["actionability"] == "PASS"
                       and self.off["per_parcel"][p]["result"]["composite_score"] >= thr)
        self.assertEqual(expected, 2)
        self.assertEqual(metric(off) - metric(on), expected)
        coff = [p for s, p in self.off["cycle"]["executed"] if s == research._SQL_INSERT_PARCEL_SCORE]
        con = [p for s, p in self.on["cycle"]["executed"] if s == research._SQL_INSERT_PARCEL_SCORE]
        self.assertEqual(metric(coff) - metric(con), expected)

    def test_review_flags_never_change_actionability(self):
        for pid in ("fulton-Z02", "fulton-S08"):
            self.assertEqual(self.on["per_parcel"][pid]["result"]["actionability"], "PASS")
            rv = flags(self.on["per_parcel"][pid], hazards.REVIEW_FLAG_TYPE)
            self.assertTrue(rv, pid)
            self.assertFalse(flags(self.on["per_parcel"][pid], "actionability_block"))

    def test_gaps_name_the_cause(self):
        dg = [p[3] for p in flags(self.on["per_parcel"]["fulton-Z05"], "data_gap")]
        self.assertTrue(any(d.endswith("H4 flood: no hazards import for this parcel "
                                       "(FEMA NFHL not assessed)") for d in dg))
        dg = [p[3] for p in flags(self.on["per_parcel"]["fulton-Z12"], "data_gap")]
        self.assertTrue(any("stale" in d for d in dg))
        self.assertEqual(self.on["per_parcel"]["fulton-Z12"]["result"]["actionability"],
                         self.off["per_parcel"]["fulton-Z12"]["result"]["actionability"])

    def test_one_hazard_read_per_cycle(self):
        n = sum(1 for s, _ in self.on["cycle"]["executed"] if s == hazards._SQL_HAZARDS_FOR_PARCELS)
        self.assertEqual(n, 1)

    def test_present_and_empty_changes_only_flag_text(self):
        empty = {pid: None for pid in sg.PARCELS}
        with enabled():
            got = run_both(responder(empty))
        for pid in sg.PARCELS:
            self.assertEqual(got["per_parcel"][pid]["parcel_score_inserts"],
                             self.off["per_parcel"][pid]["parcel_score_inserts"], pid)
            self.assertEqual(got["per_parcel"][pid]["research_log_notes"],
                             self.off["per_parcel"][pid]["research_log_notes"], pid)

    def test_partial_import_changes_only_imported_parcels(self):
        partial = {pid: (MIXED.get(pid) if pid in ("fulton-Z06",) else None) for pid in sg.PARCELS}
        with enabled():
            got = run_both(responder(partial))
        for pid in sg.PARCELS:
            same = (got["per_parcel"][pid]["parcel_score_inserts"]
                    == self.off["per_parcel"][pid]["parcel_score_inserts"])
            self.assertEqual(same, pid != "fulton-Z06", pid)

    def test_open_reviews_are_not_rewritten(self):
        texts = {}
        for pid in ("fulton-Z02", "fulton-S08"):
            texts[pid] = [p[3].split("; ", 1)[1]
                          for p in flags(self.on["per_parcel"][pid], hazards.REVIEW_FLAG_TYPE)]
        with enabled():
            got = run_both(responder(open_reviews=texts))
        for pid in texts:
            self.assertEqual(flags(got["per_parcel"][pid], hazards.REVIEW_FLAG_TYPE), [], pid)
            self.assertEqual(flags(got["cycle"], hazards.REVIEW_FLAG_TYPE)
                             and [p for p in flags(got["cycle"], hazards.REVIEW_FLAG_TYPE)
                                  if p[1] == pid], [], pid)

    def test_discovery_emits_nothing_for_h4_h6(self):
        with enabled():
            self.assertEqual(research._h4_flag({}, None, {}).action, "pass")
            self.assertEqual(research._h6_filter({}, None, {}).action, "pass")


class StaticResearchTests(unittest.TestCase):
    """FW-05: research holds no hazard fact, threshold or SQL."""

    def test_reaches_hazards_only_through_allowed_names(self):
        self.assertEqual(module_reference_problems(RESEARCH_SRC, "hazards", ALLOWED), [])

    def test_no_thresholds_or_zone_lists(self):
        for bad in ("flood_zones_blocked", "wetlands_max_pct_of_parcel",
                    "flood_zones_flag_for_review", "AE_with_LOMR"):
            self.assertNotIn(bad, RESEARCH_SRC)
        tree = ast.parse(RESEARCH_SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotRegex(node.value, r"parcel_hazards|sfha_|wetland_sf|FLD_ZONE")

    def test_scoring_wiring_is_the_documented_switch(self):
        body = RESEARCH_SRC.split("def _fetch_hazards(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if not hazards.enabled():\n        return None", body)
        self.assertIn("hazards.assess_parcels(conn, [parcel_id])", body)
        sp = RESEARCH_SRC.split("def score_parcel(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("hazard.blocker if hazard is not None else None", sp)
        self.assertNotIn("site_fit", RESEARCH_SRC.split("def _h6_filter(", 1)[1].split("\ndef ", 1)[0])

    def test_research_never_writes_or_aliases_prepare(self):
        """Review finding 2: research.py reaches prepare only as a read of
        ``prepare.<attr>``: no store, delete, augmented assignment,
        setattr/delattr, alias or from-import, so no runtime rebinding of a
        prepare attribute (the hazard gate constant included)."""
        self.assertEqual(prepare_write_problems(RESEARCH_SRC), [])

    def test_prepare_write_guard_catches_the_probe(self):
        base = RESEARCH_SRC
        for bad in (
            "prepare._DDL_PARCEL_HAZARDS = 'parcel_' + 'hazards '\n",
            "prepare.get_parameters = lambda: {}\n",
            "del prepare.get_parameters\n",
            "prepare.x += 'a'\n",
            "setattr(prepare, 'x', 1)\n",
            "p = prepare\n",
            "from prepare import get_parameters\n",
            "import prepare as pp\n",
            "def f():\n    (prepare.x, y) = (1, 2)\n",
            "def f():\n    for prepare.x in (1,):\n        pass\n",
            "def f():\n    with open('a') as prepare.x:\n        pass\n",
        ):
            self.assertNotEqual(prepare_write_problems(base + "\n" + bad), [], bad)

    def test_research_never_names_the_gate(self):
        for bad in ("GATE_CONSTANT", "_SOURCE_DDL", "_DDL_PARCEL_HAZARDS ", "ddl_from_prepare"):
            self.assertNotIn(bad, RESEARCH_SRC)
        tree = ast.parse(RESEARCH_SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotIn("_DDL_PARCEL_HAZARDS", node.value)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("_DDL_PARCEL_HAZARDS", "GATE_CONSTANT",
                                             "_SOURCE_DDL"))

    def test_static_guard_lists(self):
        import static_guards
        self.assertIn("hazards", static_guards.PROTECTED_NAMES)
        self.assertIn("hazards_import", static_guards.REEXPORTING_MODULES)


class SiteFitTests(unittest.TestCase):
    """FW-14 / FW-23 offline: the hazard SQL, its texts, the fallback."""

    def proj(self, states, fail_hazards=False, extra=None):
        from test_site_fit import FakeConn, inner_rect

        class HzConn(FakeConn):
            def cursor(self_inner):
                cur = FakeConn.cursor(self_inner)
                orig = cur.execute

                def execute(sql, params=()):
                    if sql == site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS:
                        self_inner.executed.append((sql, params))
                        if fail_hazards:
                            raise RuntimeError("relation parcel_hazards does not exist")
                        cur.rows = [("a", 1_000_000 * sg.M2_PER_SQFT, False, 322_000.0,
                                     inner_rect(700, 460), False, states[0], states[1], 0.0)
                                    + tuple(extra or ())]
                        return
                    orig(sql, params)
                cur.execute = execute
                return cur
        site_fit._FIT_CACHE.clear()
        conn = HzConn({"a": sg.SF_M1}, {"a": (1_000_000, inner_rect(700, 460), 322_000.0)})
        return site_fit.project_parcel(conn, "a"), conn

    def test_states_and_texts(self):
        with enabled():
            p, conn = self.proj(("fresh", "fresh"))
            self.assertEqual(p.flood_wetlands, hazards.deduction_text("fresh", "fresh"))
            self.assertIn(hazards.STREAM_BUFFERS, p.constraints)
            self.assertNotIn(site_fit.FLOOD_WETLANDS, p.constraints)
            p, _ = self.proj(("absent", "stale"))
            self.assertTrue(p.flood_wetlands.startswith("flood/wetlands not deducted"))
            p, conn = self.proj(("fresh", "fresh"), fail_hazards=True)
            self.assertEqual(p.status, site_fit.OK)
            self.assertEqual(p.flood_wetlands, site_fit.FLOOD_UNREADABLE)
            self.assertIn(site_fit._SQL_BUFFERED_FOR_FIT, [s for s, _ in conn.executed])

    def test_texts_say_what_was_actually_deducted(self):
        # Review finding 9: no "deducted" claim for a hazard the parcel lacks,
        # and the no-FEMA-determination part is named.
        with enabled():
            p, conn = self.proj(("fresh", "fresh"), extra=(False, False, False))
            self.assertIn("no SFHA on parcel", p.flood_wetlands)
            self.assertIn("no NWI wetlands on parcel", p.flood_wetlands)
            self.assertNotIn("SFHA incl. floodway deducted", p.flood_wetlands)
            self.assertNotIn("NWI wetlands deducted", p.flood_wetlands)
            params = [pr for s_, pr in conn.executed
                      if s_ == site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS][0]
            self.assertEqual(len(params), site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS.count("%s"))
            self.assertEqual(params[12:15], (hazards.NOISE_FLOOR_SF,) * 3)
            p, _ = self.proj(("fresh", "fresh"), extra=(True, True, True))
            self.assertIn("SFHA incl. floodway deducted", p.flood_wetlands)
            self.assertIn("NWI wetlands deducted", p.flood_wetlands)
            self.assertIn("part of the parcel has no FEMA flood determination",
                          p.flood_wetlands)
            p, _ = self.proj(("fresh", "absent"), extra=(True, False, False))
            self.assertIn("SFHA incl. floodway deducted", p.flood_wetlands)
            self.assertIn("wetlands not deducted", p.flood_wetlands)
            self.assertNotIn("no FEMA", p.flood_wetlands)

    def test_hazard_sql_parameter_order(self):
        # Review finding 8: the comment names all 16 placeholders.
        sql = site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS
        self.assertEqual(sql.count("%s"), 16)
        self.assertEqual(len(site_fit._buffered_params_hazards(10.0, ["a"])), 16)
        src = (REPO_ROOT / "site_fit.py").read_text(encoding="utf-8")
        self.assertIn("# Parameters (16): (vertex limit, setback, t, setback, t, t, t, "
                      "vertex limit,\n# setback, setback, t, t, floor, floor, floor, ids).", src)

    def test_stream_buffers_in_every_status_when_enabled(self):
        with enabled():
            for st in site_fit.STATUSES:
                p = site_fit._empty("a", st, "x", None)
                self.assertIn(hazards.STREAM_BUFFERS, p.constraints, st)
        p = site_fit._empty("a", site_fit.ERROR, "x", None)
        self.assertNotIn(hazards.STREAM_BUFFERS, p.constraints)
        self.assertIn(site_fit.FLOOD_WETLANDS, p.constraints)

    def test_hazard_sql_shape(self):
        sql = site_fit._SQL_BUFFERED_FOR_FIT_HAZARDS
        self.assertEqual(sql.count("%s"), len(site_fit._buffered_params_hazards(25.0, ["a"])))
        self.assertIn("md5(ST_AsBinary(geometry))", sql)
        self.assertIn("ST_Buffer(hz_raw, 2.0 * %s::float8, 'quad_segs=2')", sql)
        self.assertIn("CASE WHEN hz IS NULL THEN ST_Buffer(t, -1.0 * %s::float8)", sql)
        self.assertNotIn("x500", sql)
        self.assertNotRegex(sql, r"ST_Intersects|ST_DWithin|\{")


class ReportingTests(unittest.TestCase):
    """FW-18: snapshot lines for absent, clear, AE, floodway, wetlands 25%, stale."""

    def lines(self, **kw):
        import reporting
        a = hazards.assess_row(row(**kw), prepare.get_parameters())
        return reporting._flood_lines({"hazard": a})

    def test_golden_renders(self):
        f, w = self.lines()
        self.assertEqual(f, "FEMA zones X; SFHA 0.00% (floodway 0.00%), 0.2% annual chance "
                            "0.00%; BFE —; NFHL 13121C 1.1.1.0; H4 pass; LOMA not checked; "
                            "screening only; not an official determination (the effective "
                            "FIRM governs)")
        self.assertEqual(w, "none: NWI 0.00% of parcel; H6 pass; NWI as of dbf 2026-05-01; NWI "
                            "is not a jurisdictional delineation (1:24,000 screening scale)")
        f, _ = self.lines(**AE, bfe_ft=812.5)
        self.assertEqual(f, "FEMA zones AE, X; SFHA 5.00% (floodway 0.00%), 0.2% annual chance "
                            "0.00%; BFE 812.5 ft; NFHL 13121C 1.1.1.0; H4 block; LOMA not "
                            "checked; screening only; not an official determination (the "
                            "effective FIRM governs)")
        f, _ = self.lines(sfha_sf=3000, floodway_sf=3000, nfhl_zone_sf={"AE|FLOODWAY|T": 3000},
                          zones=["AE"])
        self.assertIn("SFHA 3.00% (floodway 3.00%)", f)
        self.assertIn("H4 block", f)
        _, w = self.lines(wetland_sf=25000, wetland_type_sf={"Freshwater Emergent Wetland": 25000})
        self.assertEqual(w, "significant: NWI 25.00% of parcel (Freshwater Emergent Wetland "
                            "25.00%); H6 fail; NWI as of dbf 2026-05-01; NWI is not a "
                            "jurisdictional delineation (1:24,000 screening scale)")
        f, w = self.lines(geom_md5="new")
        self.assertEqual(f, "— (hazard data stale: parcel geometry changed since the import; "
                            "H4 not assessed)")
        self.assertTrue(w.startswith("— (hazard data stale"))

    def test_snapshot_template_uses_the_lines(self):
        src = (REPO_ROOT / "reporting.py").read_text(encoding="utf-8")
        self.assertIn("- **Flood zone**: {flood_md}", src)
        self.assertIn("- **Wetlands**: {wetlands_md}", src)

    def test_bundle_reads_hazards_only_when_enabled(self):
        import reporting
        src = (REPO_ROOT / "reporting.py").read_text(encoding="utf-8")
        body = src.split("def _fetch_snapshot_data(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("    if hazards.enabled():\n        bundle[\"hazard\"] = "
                      "hazards.assess_parcels(conn, [parcel_id])", body)
        self.assertEqual(reporting._flood_lines({"hazard": None})[0],
                         "— (hazard data unavailable; H4 not assessed)")


class ProtectionTests(unittest.TestCase):
    """FW-05 / FW-06 / FW-26: every protected-file list names the new
    modules; the human-owned files are byte-identical to 8ddaf69."""

    def test_docs_name_the_protected_files(self):
        for doc in ("CLAUDE.md", "home_machine/agent_prompt.md", "AUTORESEARCH_MECHANICS.md",
                    "STANDING_RISKS.md", "START_HERE.md", "README.md"):
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            for needle in ("hazards.py", "hazards_import.py"):
                self.assertIn(needle, text, (doc, needle))
        for doc in ("CLAUDE.md", "AUTORESEARCH_MECHANICS.md", "STANDING_RISKS.md"):
            self.assertIn("reviews/26_flood_wetlands", (REPO_ROOT / doc).read_text(), doc)
        sr1 = [l for l in (REPO_ROOT / "STANDING_RISKS.md").read_text().splitlines()
               if l.startswith("| SR-1 ")][0]
        self.assertIn("hazards.py", sr1)
        self.assertIn("test_hazards_scoring.ProtectionTests", sr1)
        self.assertIn("hazards_import.py", research.__doc__)

    def test_lists_in_code(self):
        import rules_scrape
        self.assertIn("hazards.py", rules_scrape.PROTECTED_MODULES)
        self.assertIn("hazards_import.py", rules_scrape.PROTECTED_MODULES)
        wf = (REPO_ROOT / ".github" / "workflows" / "discovery-fulton.yml").read_text()
        self.assertEqual(wf.count("- 'hazards.py'"), 2)
        self.assertEqual(wf.count("- 'hazards_import.py'"), 2)
        mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("hazards-import:", mk)
        self.assertIn("python hazards_import.py", mk)
        gi = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("\nhazards_inbox/\n", gi)

    def test_human_owned_and_prepare_byte_identical_to_base(self):
        import subprocess
        for name in ("prepare.py", "parameters.json", "sources.json", "requirements.txt",
                     "program.md", "zoning_rules.json", "building_prototypes.json",
                     "rules_sources.json"):
            try:
                base = subprocess.run(["git", "show", f"8ddaf69:{name}"], cwd=REPO_ROOT,
                                      capture_output=True, check=True).stdout
            except (subprocess.CalledProcessError, OSError):
                self.skipTest("git history unavailable")
            self.assertEqual((REPO_ROOT / name).read_bytes(), base, name)

    def test_no_pipeline_module_writes_human_owned_files(self):
        writers = {"write_text", "write_bytes", "dump", "replace", "rename", "copy",
                   "copyfile", "copy2", "move", "touch", "unlink"}
        for name in ("hazards.py", "hazards_import.py"):
            tree = ast.parse((REPO_ROOT / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in writers:
                    self.fail(f"{name}:{node.lineno} writes a file")


if __name__ == "__main__":
    unittest.main()
