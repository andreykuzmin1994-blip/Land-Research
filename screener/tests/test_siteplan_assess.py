"""Tests for screener.siteplan_assess (the read-out of a site plan), the
test-fit sheet that prints it and the viewer fields that carry it.

Offline: synthetic rectangles from test_siteplan, no network.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from shapely.geometry import LineString, box

from screener import siteplan as sp
from screener import siteplan_assess as assess
from screener import siteplan_render as sr
from screener import viewer
from screener.tests import test_siteplan as ts
from screener.tests import test_viewer as tv

SCREENER = Path(__file__).resolve().parents[1]


def flags_rec(sev1=(), sev2=(), sev3=(), **kw):
    """A minimal plan record with the given flag names."""
    rec = {"fits": True, "feasible": not [f for f in sev1 if f not in assess.GATE_FLAGS], "hypothetical": False,
           "gated": sorted(set(sev1) & assess.GATE_FLAGS), "total_sf": 100000, "total_sf_if_access": 100000,
           "configuration": "rear-load 260", "building": {"L_ft": 400, "D_ft": 250, "sf": 100000},
           "coverage": {"gross_ac": 10.0, "sf_per_gross_ac": 10000, "gross_far": 0.23},
           "flags": {1: [{"flag": f, "note": f"note {f}"} for f in sev1], 2: [{"flag": f, "note": f"note {f}"} for f in sev2],
                     3: [{"flag": f, "note": f"note {f}"} for f in sev3]}}
    rec.update(kw)
    return rec


# ---------------------------------------------------------------------------
# The playbook
# ---------------------------------------------------------------------------
class Playbook(unittest.TestCase):

    def planner_flags(self) -> set[str]:
        """Every flag name the planner can raise, read from its source."""
        src = (SCREENER / "siteplan.py").read_text(encoding="utf-8")
        found = set(re.findall(r'\(\s*"([A-Z][A-Z0-9_]{3,})",\s*(?:[123]|sev|1 if|max\()', src))
        found |= set(re.findall(r'"flag":\s*"([A-Z][A-Z0-9_]{3,})"', src))
        found |= set(sp.NOT_CHECKED_FLAGS) | set(sp.GATE_FLAGS) | set(sp.RESCUE_FLAGS) | set(sp.LENGTH_INDEPENDENT_FLAGS)
        return found - set(sp.EDGE_KINDS)                       # an edge kind is not a flag

    def test_every_planner_flag_has_an_action(self):
        flags = self.planner_flags()
        self.assertGreater(len(flags), 40)                      # the scan found the planner's flags
        for must in ("ACCESS_NONE", "NO_FIT", "ZONING_DRAFT", "TRAILER_SHORT", "HSG_ASSUMED", "PLAN_BUDGET_EXCEEDED",
                     "CORRIDOR_OVERLAY", "USE_RESTRICTION_REPORTED"):
            self.assertIn(must, flags)
        self.assertEqual(sorted(f for f in flags if f not in assess.PLAYBOOK), [])

    def test_entries_are_well_formed(self):
        stages = {k for k, _ in assess.STAGES}
        for flag, (ws, stage, action, closes) in assess.PLAYBOOK.items():
            self.assertIn(stage, stages, flag)
            self.assertTrue(ws and action.endswith("."), flag)
            self.assertLess(len(action), 260, flag)
            self.assertNotIn("TODO", action)
            self.assertFalse(closes.endswith("."), flag)

    def test_second_building_flag_takes_the_base_entry(self):
        self.assertEqual(assess.playbook_for("SECOND_DROP_YARD_UNREACHABLE"), assess.PLAYBOOK["DROP_YARD_UNREACHABLE"])
        for flag, sev in assess.PLAYBOOK_AT_SEVERITY:
            self.assertIn(flag, assess.PLAYBOOK)
        self.assertEqual(assess.playbook_for("SOMETHING_NEW")[0], assess.W_DATA)

    def test_gate_and_rerun_sets_match_the_planner(self):
        self.assertEqual(assess.GATE_FLAGS, sp.GATE_FLAGS)
        self.assertLessEqual(sp.NOT_CHECKED_FLAGS, assess.RERUN_FLAGS)


# ---------------------------------------------------------------------------
# Status, conditions, next steps
# ---------------------------------------------------------------------------
class Status(unittest.TestCase):

    def test_statuses(self):
        cases = [
            (flags_rec(sev2=["ZONING_DRAFT", "STREAMS_PIPELINES"], sev3=["POND_SITE_FROM_LIDAR"]), "fits"),
            (flags_rec(sev2=["ZONING_DRAFT", "RELIEF"]), "fits_with_conditions"),
            (flags_rec(sev1=["USE_PERMIT_REQUIRED"], sev2=["ZONING_DRAFT"], total_sf=0), "gated"),
            (flags_rec(sev1=["ACCESS_NONE"], hypothetical=True, total_sf=0), "hypothetical"),
            (flags_rec(sev1=["POND_DOES_NOT_FIT"], total_sf=0), "blocked"),
            (flags_rec(sev1=["DEM_PARTIAL"], feasible=None, total_sf=0), "incomplete"),
            (flags_rec(sev1=["NO_FIT"], fits=False, feasible=False, total_sf=0), "no_fit"),
            (flags_rec(sev1=["NO_FIT", "LAYERS_NOT_CHECKED"], fits=False, feasible=False, total_sf=0), "no_fit"),
            (flags_rec(sev1=["PLAN_BUDGET_EXCEEDED"], fits=False, feasible=False, total_sf=0), "not_planned"),
        ]
        for rec, want in cases:
            a = assess.assessment(rec)
            self.assertEqual(a["status"], want)
            self.assertEqual(a["label"], assess.STATUS_LABELS[want])
            self.assertTrue(a["headline"])
        self.assertEqual(set(assess.STATUS_LABELS), set(viewer.PLAN_STATUSES))

    def test_counted_follows_the_floor_area(self):
        self.assertTrue(assess.assessment(flags_rec(sev2=["RELIEF"]))["counted"])
        self.assertFalse(assess.assessment(flags_rec(sev1=["USE_PERMIT_REQUIRED"], total_sf=0))["counted"])
        self.assertTrue(assess.assessment(flags_rec(sev1=["USE_PERMIT_REQUIRED"]))["counted"])      # prototype rule
        self.assertFalse(assess.assessment(flags_rec(sev1=["POND_DOES_NOT_FIT"], total_sf=0))["counted"])

    def test_string_keys_read_the_same(self):
        rec = flags_rec(sev1=["POND_DOES_NOT_FIT"], sev2=["RELIEF", "ZONING_DRAFT"], sev3=["HSG_ASSUMED"], total_sf=0)
        self.assertEqual(assess.assessment(json.loads(json.dumps(rec))), json.loads(json.dumps(assess.assessment(rec))))

    def test_conditions_order_and_kinds(self):
        rec = flags_rec(sev1=["USE_PERMIT_REQUIRED", "POND_DOES_NOT_FIT"], sev2=["RELIEF", "ZONING_DRAFT", "ACREAGE_MISMATCH"],
                        sev3=["HSG_ASSUMED", "POND_SITE_FROM_LIDAR"], total_sf=0)
        conds = assess.conditions(rec)
        self.assertEqual([c["n"] for c in conds], list(range(1, 8)))
        self.assertEqual([c["kind"] for c in conds],
                         ["blocking", "gate", "site", "site", "standing", "standing", "information"])
        self.assertEqual([c["flag"] for c in conds][:4], ["POND_DOES_NOT_FIT", "USE_PERMIT_REQUIRED", "ACREAGE_MISMATCH", "RELIEF"])
        for c in conds:
            self.assertTrue(c["action"] and c["workstream"] and c["stage_label"], c["flag"])
            self.assertEqual(c["finding"], f"note {c['flag']}")

    def test_next_steps_order_dedupe_and_cap(self):
        rec = flags_rec(sev1=["POND_DOES_NOT_FIT"], total_sf=0,
                        sev2=["RELIEF", "GRADING_RISK", "ZONING_DRAFT", "STREAMS_PIPELINES", "ACREAGE_MISMATCH", "RESIDENTIAL_EDGE",
                              "HEIGHT_CAP", "COVERAGE_NOTE", "FIRE_SECOND_ACCESS", "DEAD_END_ACCESS", "LOADING_FACES_RES"])
        a = assess.assessment(rec)
        steps = a["next_steps"]
        self.assertEqual(len(steps), assess.NEXT_STEPS_MAX)
        self.assertEqual(steps[0]["flags"], ["POND_DOES_NOT_FIT"])          # what blocks the plan comes first
        order = [assess.STAGE_ORDER[s["stage"]] for s in steps[1:]]
        self.assertEqual(order, sorted(order))                              # then desk, offer, diligence
        self.assertEqual(len({s["action"] for s in steps}), len(steps))
        self.assertEqual([s["n"] for s in steps], list(range(1, len(steps) + 1)))
        # two flags with one action share a step
        both = assess.next_steps(assess.conditions(flags_rec(sev2=["FIRE_SECOND_ACCESS", "ROUTE_OUTSIDE_ENVELOPE"],
                                                             sev1=["PLAN_GEOMETRY_CONFLICT"], total_sf=0)), "blocked")
        self.assertEqual(both[0]["flags"], ["PLAN_GEOMETRY_CONFLICT", "ROUTE_OUTSIDE_ENVELOPE"])

    def test_incomplete_says_run_again_and_nothing_else(self):
        rec = flags_rec(sev1=["FRONTAGE_NOT_CHECKED", "DEM_PARTIAL"], sev2=["RELIEF", "ZONING_DRAFT"], feasible=None, total_sf=0)
        a = assess.assessment(rec)
        self.assertEqual(a["status"], "incomplete")
        self.assertEqual(len(a["next_steps"]), 2)                           # the two not-read inputs, nothing else
        self.assertIn("again", a["next_steps"][0]["action"])
        self.assertEqual(a["next_steps"][1]["flags"], ["DEM_PARTIAL"])
        self.assertEqual(a["next_steps"][0]["flags"], ["FRONTAGE_NOT_CHECKED"])
        self.assertIn("not a result", a["headline"])
        # a plan drawn by a search that was cut short is not a result either
        cut = assess.assessment(flags_rec(sev1=["PLAN_BUDGET_EXCEEDED"], total_sf=0))
        self.assertEqual(cut["status"], "incomplete")
        self.assertIn("--max-seconds-per-site", cut["next_steps"][0]["action"])

    def test_same_flag_two_severities_two_actions(self):
        self.assertIn("annexation", assess.playbook_for("ASSEMBLAGE_MIXED_RULES", 1)[2])
        self.assertNotIn("annexation", assess.playbook_for("ASSEMBLAGE_MIXED_RULES", 2)[2])
        self.assertEqual(assess.playbook_for("SECOND_HEIGHT_CAP", 2), assess.PLAYBOOK["HEIGHT_CAP"])

    def test_no_fit_with_an_unread_input_says_so(self):
        a = assess.assessment(flags_rec(sev1=["NO_FIT", "LAYERS_NOT_CHECKED"], fits=False, feasible=False, total_sf=0))
        self.assertEqual(a["status"], "no_fit")
        self.assertIn("can only make the envelope larger", a["headline"])

    def test_yield_position(self):
        lo, hi = sp.COMP_BAND_SF_PER_AC
        for sfa, want in ((lo - 100, "below"), (lo, "within"), (hi, "within"), (hi + 100, "above")):
            rec = flags_rec(coverage={"gross_ac": 10.0, "sf_per_gross_ac": sfa, "gross_far": 0.2})
            self.assertEqual(assess.assessment(rec, sp.COMP_BAND_SF_PER_AC)["yield"]["position"], want)


# ---------------------------------------------------------------------------
# Why this layout
# ---------------------------------------------------------------------------
def alt(sf, ok=True, trailers=10, target=10, steep=0, relief=0, blocking=(), variant=None):
    return {"variant": variant or f"layout {sf}", "sf": sf, "geometry_ok": ok, "route_problem": False, "trailers": trailers,
            "trailers_target": target, "steep_pct": steep, "relief_ft": relief, "blocking": list(blocking),
            "detention_acft": 2.0}


class Selection(unittest.TestCase):

    def test_largest_passing_layout(self):
        s = assess.selection([alt(100000), alt(90000), alt(120000, ok=False, blocking=["POND_DOES_NOT_FIT"])], sp.RANKING)
        self.assertIsNone(s["upside"])
        self.assertEqual(s["larger_failed"], 1)
        self.assertIn("largest layout tried that passed", s["basis"])
        self.assertIn("1 larger layout failed: pond does not fit (1)", s["basis"])

    def test_upside_names_only_what_it_has_more_of(self):
        chosen = alt(100000, steep=18, relief=46)
        bigger = alt(110000, trailers=0, target=9, steep=18, relief=46)
        s = assess.selection([chosen, bigger], sp.PROTOTYPE_RANKING)
        self.assertEqual((s["upside"]["sf"], s["upside"]["gain_sf"], s["upside"]["gain_pct"]), (110000, 10000, 10))
        self.assertEqual(len(s["upside"]["reasons"]), 1)                    # the ground is the same: not a reason
        self.assertIn("none of its 9 target trailer stalls", s["basis"])
        self.assertIn(f"{sp.PROTOTYPE_RANKING['trailer_sf']:,} SF", s["basis"])
        self.assertIn("higher-yield scheme", s["basis"])
        steeper = alt(110000, steep=40, relief=46)
        s2 = assess.selection([chosen, steeper], sp.RANKING)
        self.assertEqual(len(s2["upside"]["reasons"]), 1)
        self.assertIn("steep ground (40% against 18%)", s2["basis"])
        self.assertNotIn("higher-yield scheme", s2["basis"])

    def test_default_ranking_has_no_trailer_mark(self):
        # operator decision of 2026-11-17: a trailer stall is not worth floor area
        self.assertEqual(sp.RANKING["trailer_sf"], 0)
        self.assertEqual(sp.PROTOTYPE_RANKING["trailer_sf"], 2000)
        self.assertEqual(sp.PlanOptions().ranking, sp.RANKING)
        self.assertEqual(sp.PlanOptions(behaviour="prototype").ranking, sp.PROTOTYPE_RANKING)
        # so a larger layout short of trailers is not marked down for it
        s = assess.selection([alt(100000), alt(110000, trailers=0, target=9)], sp.RANKING)
        self.assertEqual(s["upside"]["reasons"], [])

    def test_plan_short_of_trailers_names_the_layout_that_is_not(self):
        chosen = alt(68000, trailers=0, target=7)
        s = assess.selection([chosen, alt(58000, trailers=6, target=6), alt(50000, trailers=5, target=5)], sp.RANKING)
        self.assertEqual((s["with_trailers"]["sf"], s["with_trailers"]["gain_sf"], s["with_trailers"]["trailers"]), (58000, -10000, 6))
        self.assertIn("0 of its 7 target trailer stalls", s["basis"])
        self.assertIn("does not trade floor area for trailer stalls", s["basis"])
        none = assess.selection([chosen, alt(58000, trailers=1, target=6)], sp.RANKING)
        self.assertIsNone(none["with_trailers"])
        self.assertIn("No layout tried meets its trailer target", none["basis"])
        self.assertIsNone(assess.selection([alt(68000)], sp.RANKING)["with_trailers"])       # not short: nothing to say

    def test_without_pond_by_market(self):
        args = ([alt(58000), alt(68000, trailers=0)], sp.RANKING, [alt(96000, ok=False, trailers=0), alt(60000, ok=False)])
        s = assess.selection(*args)                               # the default: no underground detention here
        self.assertEqual((s["without_pond"]["sf"], s["without_pond"]["gain_sf"]), (96000, 38000))    # against the plan drawn
        self.assertIs(s["without_pond"]["underground"], False)
        self.assertIn("passed every layout check but the pond", s["basis"])
        self.assertIn("0 of its 10 target trailer stalls", s["basis"])
        self.assertIn("Underground detention is not used in this market", s["basis"])
        self.assertNotIn("price that", s["basis"])
        dear = assess.selection(*args, underground=True)          # a market where land is dear
        self.assertIs(dear["without_pond"]["underground"], True)
        self.assertIn("underground or off site: price that against the floor area", dear["basis"])
        # a pond-limited layout no larger than a passing one, or a step under 5%, is not reported
        self.assertIsNone(assess.selection([alt(58000), alt(68000)], sp.RANKING, [alt(66000, ok=False)])["without_pond"])
        self.assertIsNone(assess.selection([alt(67000)], sp.RANKING, [alt(68000, ok=False)])["without_pond"])

    def test_pond_action_by_market(self):
        cheap = flags_rec(sev1=["POND_DOES_NOT_FIT"], total_sf=0, operator_decisions={"underground_detention": "no"})
        dear = flags_rec(sev1=["POND_DOES_NOT_FIT"], total_sf=0, operator_decisions={"underground_detention": "yes"})
        a, b = assess.conditions(cheap)[0]["action"], assess.conditions(dear)[0]["action"]
        self.assertIn("not used in this market", a)
        self.assertNotIn("underground detention against", a)
        self.assertIn("price underground detention", b)
        self.assertIs(sp.UNDERGROUND_DETENTION, False)
        self.assertEqual(sp.PlanOptions().decisions()["underground_detention"], "no")
        self.assertEqual(sp.PlanOptions(underground=True).decisions()["underground_detention"], "yes")

    def test_no_layout_passed_and_empty(self):
        s = assess.selection([alt(50000, ok=False, blocking=["ROUTE_INFEASIBLE"])], sp.RANKING)
        self.assertIn("No layout passed", s["basis"])
        self.assertEqual(assess.selection([], sp.RANKING)["basis"], "")


# ---------------------------------------------------------------------------
# Geometry read-outs
# ---------------------------------------------------------------------------
class Geometry(unittest.TestCase):

    def test_setback_rows(self):
        rules = {"bldg": {"ROW": 40, "OTHER": 10}, "pave": {"ROW": 10, "OTHER": 10}}
        edges = [("ROW", LineString([(0, 0), (500, 0)])), ("OTHER", LineString([(0, 0), (0, 500)]))]
        rows = {r["item"]: r for r in assess.setback_rows(rules, edges, box(60, 45, 300, 300), box(8, 12, 400, 400))}
        self.assertEqual(rows["Building setback, road"]["status"], "meets")
        self.assertEqual(rows["Building setback, road"]["margin_ft"], 5)
        self.assertEqual(rows["Paving setback, other lot lines"]["status"], "short")       # 8 ft against 10
        self.assertEqual(rows["Paving setback, other lot lines"]["margin_ft"], -2)
        self.assertNotIn("Building setback, residential side", rows)                       # no such edge on this site

    def test_band_clearance_is_square_at_the_ends(self):
        # a lot line that stops at x = 100: a shape beside its end is not behind it
        line = LineString([(0, 0), (100, 0)])
        self.assertAlmostEqual(assess.band_clearance(box(20, 40, 60, 80), line), 40, delta=0.1)
        beside = box(130, 10, 170, 50)                       # 31.6 ft from the line's end by plain distance
        self.assertLess(beside.distance(line), 32)
        self.assertIsNone(assess.band_clearance(beside, line))
        # far off, in front of one stretch: square to that stretch, not to the far end of the line
        bent = LineString([(0, 0), (500, 0), (520, 400)])
        far = box(100, 900, 200, 1000)
        self.assertAlmostEqual(assess.band_clearance(far, bent), 900, delta=0.1)
        self.assertLess(far.distance(bent), 600)
        # at a bend it is the plain distance
        corner = box(560, -80, 600, -40)
        self.assertAlmostEqual(assess.band_clearance(corner, bent), corner.distance(bent), delta=0.1)
        # a closed line has no ends; two parts are each measured
        ring = box(0, 0, 100, 100).exterior
        self.assertAlmostEqual(assess.band_clearance(box(130, 130, 150, 150), ring), 30 * 2 ** 0.5, delta=0.1)
        two = LineString([(0, 0), (100, 0)]).union(LineString([(0, 300), (100, 300)]))
        self.assertAlmostEqual(assess.band_clearance(box(20, 200, 60, 240), two), 60, delta=0.1)

    def test_open_yard_row(self):
        edges = [("ROW", LineString([(0, 0), (500, 0)])), ("OTHER", LineString([(0, 0), (0, 500)]))]
        for x0, want in ((70, "meets"), (59.5, "meets"), (45, "limited"), (30, "short")):
            row = assess.open_yard_row(edges, box(x0, 5, 300, 300))        # 5 ft from the road: a road is open space
            self.assertEqual(row["status"], want, x0)
            self.assertEqual("note" in row, want != "meets")
        self.assertIsNone(assess.open_yard_row([("ROW", LineString([(0, 0), (500, 0)]))], box(70, 5, 300, 300)))

    def test_land_budget_adds_up_without_a_plan(self):
        parcel, P = box(0, 0, 1000, 1000), box(50, 50, 950, 950)
        land = assess.land_budget(parcel, P, 920 * 920, assess.use_shapes([], parcel))
        self.assertAlmostEqual(land["setbacks_buffers_ac"], (1e6 - 920 * 920) / 43560, places=2)
        self.assertAlmostEqual(land["gross_ac"], 1e6 / 43560, places=2)
        self.assertAlmostEqual(land["unused_net_ac"] + land["outside_net_unused_ac"], land["gross_ac"], places=1)
        self.assertEqual(land["used_ac"], 0)
        self.assertAlmostEqual(land["setbacks_buffers_ac"] + land["constraints_ac"] + land["net_ac"], land["gross_ac"], places=1)


# ---------------------------------------------------------------------------
# A planned site carries the read-out
# ---------------------------------------------------------------------------
class PlannedSite(ts.Offline):

    def basic(self):
        return ts.planned("basic", lambda: ts.site(600, 700))

    def test_record_has_the_read_out(self):
        rec, d = self.basic()
        for key in ("assessment", "site_data", "compliance", "quantities", "selection"):
            self.assertIn(key, rec)
        self.assertIn(rec["assessment"]["status"], ("fits", "fits_with_conditions"))
        self.assertIs(rec["feasible"], True)
        self.assertEqual(rec["compliance"]["status"], "draft")
        # the stored assessment is the function of the record (and of its JSON form)
        self.assertEqual(rec["assessment"], assess.assessment(rec, sp.COMP_BAND_SF_PER_AC))
        back = json.loads(json.dumps(rec, default=str))
        self.assertEqual(back["assessment"], assess.assessment(back, sp.COMP_BAND_SF_PER_AC))
        self.assertIsNotNone(d.B)
        self.assertTrue(d.edges)

    def test_land_adds_up_and_matches_the_plan(self):
        rec, d = self.basic()
        land = rec["site_data"]["land"]
        total = sum(u["ac"] for u in land["uses"]) + land["unused_net_ac"] + land["outside_net_unused_ac"]
        self.assertAlmostEqual(total, land["gross_ac"], delta=0.05)
        use = {u["use"]: u["ac"] for u in land["uses"]}
        self.assertAlmostEqual(use["building"], d.p.building.area / 43560, places=2)
        self.assertAlmostEqual(land["gross_ac"], rec["coverage"]["gross_ac"], delta=0.06)
        self.assertAlmostEqual(land["net_ac"], rec["coverage"]["paving_envelope_ac"], delta=0.06)
        self.assertGreater(use["truck"], 0)
        self.assertGreater(use["ponds"], 0)
        self.assertLessEqual(land["largest_unused_ac"], land["unused_net_ac"] + 0.01)

    def test_the_plan_honours_its_own_draft_setbacks(self):
        rec, _ = self.basic()
        rows = {r["item"]: r for r in rec["compliance"]["rows"]}
        for item, r in rows.items():
            if "setback" in item:
                self.assertEqual(r["status"], "meets", item)
                self.assertGreaterEqual(r["margin_ft"], -1, item)
        self.assertEqual(rows["Car parking"]["status"], "meets")
        self.assertEqual(rows["Use"]["status"], "verify")
        self.assertIn("Building setback, road", rows)

    def test_residential_rear_edge_does_not_read_short(self):
        # review 38, finding 1: homes along the whole rear line; the edge is
        # read 20 ft down each side line, and plain distance to that stub's
        # end made a feasible plan read "short" on the residential rear
        homes = ts.wgs(box(0, 900, 700, 1100))
        rec, _ = ts.planned("res_rear", lambda: ts.site(700, 900, neighbours=[({"LUCode": "101"}, homes)]))
        self.assertIs(rec["feasible"], True)
        self.assertIn("RESIDENTIAL_EDGE", ts.names(rec))
        rows = {r["item"]: r for r in rec["compliance"]["rows"]}
        res = [r for item, r in rows.items() if "residential" in item]
        self.assertTrue(res)
        for item, r in rows.items():
            if "setback" in item:
                self.assertEqual(r["status"], "meets", (item, r))
        self.assertIn("Open yard at the building (building code)", rows)
        self.assertGreater(rec["quantities"]["residential_edge_lf"], 600)

    def test_read_out_failure_keeps_the_plan(self):
        def boom(*a, **k):
            raise ValueError("planted")
        with ts.mock.patch.object(assess, "land_budget", boom), ts.mock.patch.object(ts.sitefit, "_angles", lambda rings: [0.0, 90.0]):
            rec, d = sp.plan_site(ts.site(600, 700))
        self.assertIs(rec["feasible"], True)
        self.assertGreater(rec["total_sf"], 0)
        self.assertEqual(rec["read_out"], {"complete": False, "reason": "ValueError: planted"})
        self.assertIn(rec["assessment"]["status"], ("fits", "fits_with_conditions"))
        self.assertNotIn("site_data", rec)
        ET.fromstring(sr.render(d, rec))                                      # the sheet still draws

    def test_prototype_behaviour_agrees_with_its_record(self):
        # review 38, finding 2: under the prototype rule a severity-1 flag
        # does not always block, and a pond that does not fit is severity 2
        proto = sp.PlanOptions(behaviour="prototype")
        cp, _ = ts.planned("cp_proto", lambda: ts.site(800, 900, jur="College Park"), proto)
        self.assertIs(cp["feasible"], True)
        self.assertIn("HEIGHT_CAP", ts.names(cp, 1))
        a = cp["assessment"]
        self.assertEqual(a["status"], "fits_with_conditions")
        self.assertEqual(a["counts"]["blocking"], 0)
        self.assertEqual(assess.pipeline_row(cp)["blocking"], "")

        def nopond(*a_, **k):
            return None
        with ts.mock.patch.object(sp, "place_sized_pond", nopond), ts.mock.patch.object(ts.sitefit, "_angles", lambda rings: [0.0, 90.0]):
            rec, _ = sp.plan_site(ts.site(600, 700), proto)
        self.assertIs(rec["feasible"], False)
        self.assertEqual(rec["assessment"]["status"], "blocked")
        self.assertNotIn("0 blocking", rec["assessment"]["headline"])
        self.assertIn("failed its own checks", rec["assessment"]["headline"])

    def test_grading_and_takeoff(self):
        def z(x, y):
            return 900.0 + 0.05 * x
        rec, _ = ts.planned("slope5", lambda: ts.site(600, 700, z=z))
        gr = rec["grading"]
        self.assertAlmostEqual(gr["max_cut_ft"] + gr["max_fill_ft"], gr["relief_ft"], delta=1)
        self.assertTrue(900 < gr["pad_elev_ft"] < 930)
        q = rec["quantities"]
        self.assertGreater(q["heavy_paving_sf"], q["light_paving_sf"])
        self.assertEqual(q["detention_acft"], rec["detention"]["storage_acft"])
        self.assertEqual(q["building_sf"], rec["total_sf_if_access"])
        self.assertEqual(q["driveways"], 2)
        flat, _ = self.basic()
        self.assertEqual((flat["grading"]["max_cut_ft"], flat["grading"]["max_fill_ft"]), (0, 0))

    def test_alternatives_carry_what_they_hold(self):
        rec, _ = self.basic()
        first = rec["alternatives"][0]
        self.assertEqual((first["sf"], first["doors"]), (rec["building"]["sf"], rec["doors"]))
        self.assertEqual(first["trailers"], rec["trailers"]["total"])
        for a in rec["alternatives"]:
            self.assertEqual(bool(a["blocking"] and set(a["blocking"]) - sp.LENGTH_INDEPENDENT_FLAGS), not a["geometry_ok"])

    def test_no_fit_has_an_assessment_and_a_land_budget(self):
        rec, d = sp.plan_site(ts.site(150, 150))
        self.assertIsNone(d)
        a = rec["assessment"]
        self.assertEqual(a["status"], "no_fit")
        self.assertEqual(a["next_steps"][0]["flags"], ["NO_FIT"])
        self.assertAlmostEqual(rec["site_data"]["land"]["gross_ac"], 150 * 150 / 43560, places=2)
        self.assertEqual(rec["site_data"]["land"]["used_ac"], 0)

    def test_hypothetical_incomplete_and_gated(self):
        hyp, _ = ts.planned("landlocked_a", lambda: ts.site(600, 700, front_state="none"))
        self.assertEqual(hyp["assessment"]["status"], "hypothetical")
        self.assertEqual(hyp["assessment"]["next_steps"][0]["flags"], ["ACCESS_NONE"])
        inc, d = ts.planned("frontnc", lambda: ts.site(450, 520, front_state="not_checked", dem_state="partial"))
        self.assertEqual(inc["assessment"]["status"], "incomplete")
        self.assertIn("INCOMPLETE", sr.render(d, inc))
        gated, d2 = ts.planned("eastpoint_a", lambda: ts.site(600, 700, jur="East Point", zoning="I-1"))
        self.assertEqual(gated["assessment"]["status"], "gated")
        self.assertFalse(gated["assessment"]["counted"])
        self.assertEqual({r["status"] for r in gated["compliance"]["rows"] if r["item"] == "Use"}, {"gate"})
        svg = sr.render(d2, gated)
        self.assertIn("ENTITLEMENT GATE", svg)
        self.assertIn("floor area not counted", svg)

    def test_pond_limited_layout_is_reported_not_counted(self):
        def nopond(*a, **k):
            return None
        with ts.mock.patch.object(sp, "place_sized_pond", nopond), ts.mock.patch.object(ts.sitefit, "_angles", lambda rings: [0.0, 90.0]):
            rec, _ = sp.plan_site(ts.site(600, 700))
        self.assertIs(rec["feasible"], False)
        self.assertEqual(rec["assessment"]["status"], "blocked")
        self.assertEqual(rec["total_sf"], 0)
        self.assertEqual(rec["assessment"]["next_steps"][0]["workstream"], assess.W_STORM)
        # every layout failed on the pond, so none "passed": no upside is claimed
        self.assertIsNone(rec["selection"]["upside"])
        self.assertIn("No layout passed", rec["selection"]["basis"])


# ---------------------------------------------------------------------------
# The tightened draft rules (operator decision of 2026-11-17, reviews/38)
# ---------------------------------------------------------------------------
class TightenedRules(ts.Offline):

    def test_table_and_prototype(self):
        # the prototype behaviour reads the draft table as it was
        for name in sp.JUR_DRAFT:
            _, proto, _ = sp.jur_rules(name)
            self.assertEqual(proto, sp.JUR_DRAFT[name])
            self.assertNotIn("OTHER_REAR", proto["bldg"])
        self.assertLessEqual(set(sp.JUR_TIGHTENED), set(sp.JUR_DRAFT))
        want = {"fairburn": ("OTHER_REAR", 30, 30), "east point": ("OTHER_REAR", 30, 30), "atlanta": ("RES_SIDE", 31, 31),
                "palmetto": ("COMM", 20, 20)}
        for name, (kind, code, _) in want.items():
            self.assertEqual(sp.JUR_TIGHTENED[name]["bldg"][kind], code)
        for name in sp.JUR_DRAFT:
            _, r, _ = sp.jur_rules(name, tightened=True)
            self.assertEqual(set(r["bldg"]), set(sp.EDGE_KINDS))
            self.assertEqual(set(r["pave"]), set(sp.EDGE_KINDS))
            self.assertEqual(r["open_yard_ft"], sp.OPEN_YARD_RULE_FT)          # cut by plan_site, not folded into the table
            for kind in sp.EDGE_KINDS:
                self.assertGreaterEqual(r["bldg"][kind], sp.JUR_DRAFT[name]["bldg"].get(kind, 0))      # never looser
                self.assertGreaterEqual(r["pave"][kind], sp.JUR_DRAFT[name]["pave"].get(kind, 0))
            self.assertTrue(r["tightened"])
        _, fair, _ = sp.jur_rules("Fairburn", tightened=True)
        self.assertEqual((fair["pave"]["COMM"], fair["truck_space_sf"], fair["pave"]["OTHER_REAR"]), (50, 5000, 5))
        # the table keeps the codes' own numbers: the 60 ft yard does not overwrite them
        self.assertEqual((fair["bldg"]["OTHER_REAR"], fair["bldg"]["OTHER"], fair["bldg"]["COMM"]), (30, 25, 50))
        self.assertEqual(sp.jur_rules("Atlanta", tightened=True)[1]["bldg"]["RES_REAR"], 31)
        self.assertEqual(sp.jur_rules("East Point", tightened=True)[1]["bldg"]["OTHER_REAR"], 30)
        _, ep, _ = sp.jur_rules("East Point", tightened=True)
        self.assertEqual((ep["pave"]["COMM"], ep["row_by_zoning"]), (50, {"BP": 45}))
        for name in ("South Fulton", "Union City"):
            self.assertTrue(sp.jur_rules(name, tightened=True)[1]["ag_buffer"])
        self.assertNotIn("ag_buffer", sp.jur_rules("Atlanta", tightened=True)[1])

    def edges_of(self, neighbours, rules):
        inp = ts.site(600, 700, neighbours=neighbours)
        plane = sp.Plane(inp.geometry)
        parcel, _ = sp.site_parcel(plane, inp.geometry, False, sp.PlanOptions())
        front = plane.ft(inp.frontage.line_wgs)
        return {k: round(ln.length) for k, ln in sp.classify_edges(parcel, plane, neighbours, front, rules)}

    def test_edge_kinds(self):
        shop = ({"ClassCode": "C3"}, ts.wgs(box(600, 0, 800, 700)))          # east side
        farm = ({"ClassCode": "A5"}, ts.wgs(box(-200, 0, 0, 700)))           # west side
        _, sf, _ = sp.jur_rules("South Fulton", tightened=True)
        _, atl, _ = sp.jur_rules("Atlanta", tightened=True)
        got = self.edges_of([shop, farm], sf)
        self.assertGreater(got["COMM"], 600)                                  # the shop's line
        self.assertGreater(got.get("RES_SIDE", 0) + got.get("RES_REAR", 0), 600)      # the farm counts as residential here
        self.assertGreater(got["OTHER_REAR"], 500)                            # the back line, far from the road
        atl_got = self.edges_of([shop, farm], atl)
        self.assertNotIn("RES_SIDE", atl_got)                                 # not where the code has no such rule
        # the prototype behaviour: the four old kinds only
        old = self.edges_of([shop, farm], None)
        self.assertEqual(set(old), {"ROW", "OTHER"})
        self.assertAlmostEqual(sum(got.values()), sum(old.values()), delta=5)         # the same boundary, re-labelled

    def test_open_yard_is_a_layout_rule(self):
        for key, kw in (("basic", dict(w=600, h=700)), ("fair_yard", dict(w=900, h=800, jur="Fairburn")),
                        ("ep_yard", dict(w=700, h=800, jur="East Point", zoning="I-2X"))):
            rec, d = ts.planned(key, lambda kw=kw: ts.site(**kw))
            self.assertTrue(rec["fits"], key)
            lines = sp.unary_union([ln for k, ln in d.edges if k != "ROW"])
            self.assertGreaterEqual(d.p.building.distance(lines), sp.OPEN_YARD_RULE_FT - 1, key)
            yard = [r for r in rec["compliance"]["rows"] if r["item"].startswith("Open yard")][0]
            self.assertEqual(yard["status"], "meets", key)
            self.assertEqual(rec["operator_decisions"]["open_yard_ft"], sp.OPEN_YARD_RULE_FT)
            self.assertTrue(rec["zoning"]["tightened"])
        proto, _ = ts.planned("basic", lambda: ts.site(600, 700), sp.PlanOptions(behaviour="prototype"))
        self.assertIsNone(proto["operator_decisions"]["open_yard_ft"])
        self.assertNotIn("tightened", proto["zoning"])
        self.assertGreater(proto["building"]["sf"], ts.planned("basic", lambda: ts.site(600, 700))[0]["building"]["sf"])

    def test_fairburn_truck_space_minimum(self):
        rec, _ = ts.planned("fair_yard", lambda: ts.site(900, 800, jur="Fairburn"))
        sf = rec["building"]["sf"]
        self.assertGreaterEqual(rec["trailers"]["target"], sf // 5000)        # the floor under the market target
        row = [r for r in rec["compliance"]["rows"] if r["item"] == "Truck spaces"][0]
        self.assertEqual(int(row["required"].split()[0]), -(-sf // 5000))
        if rec["trailers"]["total"] < rec["trailers"]["target"]:
            note = [f["note"] for f in rec["flags"][2] if f["flag"] == "TRAILER_SHORT"][0]
            self.assertIn("5,000 SF", note)
        other, _ = ts.planned("basic", lambda: ts.site(600, 700))
        self.assertFalse([r for r in other["compliance"]["rows"] if r["item"] == "Truck spaces"])

    def test_palmetto_corridor_and_college_park(self):
        on, _ = ts.planned("palm_sr14", lambda: ts.site(900, 900, jur="Palmetto", road="minor arterial, SR 14"))
        note = [f["note"] for f in on["flags"][2] if f["flag"] == "CORRIDOR_OVERLAY"][0]
        self.assertIn("are applied", note)
        self.assertEqual((on["zoning"]["values"]["bldg"]["ROW"], on["zoning"]["values"]["pave"]["ROW"]), (95, 35))
        rows = {r["item"]: r for r in on["compliance"]["rows"]}
        self.assertEqual(rows["Building setback, road"]["required"], "95 ft")
        self.assertEqual(rows["Building setback, road"]["status"], "meets")
        off, _ = ts.planned("palm_local", lambda: ts.site(900, 900, jur="Palmetto", road="local road, GDOT route 1121200054600"))
        note = [f["note"] for f in off["flags"][2] if f["flag"] == "CORRIDOR_OVERLAY"][0]
        self.assertIn("check the road", note)
        self.assertEqual(off["zoning"]["values"]["bldg"]["ROW"], 50)
        self.assertNotIn("SR 140", "SR 14")                                    # and a longer route number is not SR 14:
        sr140, _ = ts.planned("palm_sr140", lambda: ts.site(900, 900, jur="Palmetto", road="minor arterial, SR 140"))
        self.assertEqual(sr140["zoning"]["values"]["bldg"]["ROW"], 50)
        cp, _ = ts.planned("cp_use", lambda: ts.site(800, 900, jur="College Park"))
        self.assertIn("USE_RESTRICTION_REPORTED", ts.names(cp, 2))
        self.assertIn("moratorium", [c for c in cp["assessment"]["conditions"] if c["flag"] == "USE_RESTRICTION_REPORTED"][0]["action"])
        proto, _ = ts.planned("cp_use", lambda: ts.site(800, 900, jur="College Park"), sp.PlanOptions(behaviour="prototype"))
        self.assertNotIn("USE_RESTRICTION_REPORTED", ts.names(proto))
        self.assertNotIn("CORRIDOR_OVERLAY", ts.names(ts.planned("basic", lambda: ts.site(600, 700))[0]))

    def test_east_point_business_park_front_yard(self):
        bp, _ = ts.planned("ep_bp", lambda: ts.site(700, 800, jur="East Point", zoning="B-P"))
        self.assertEqual(bp["zoning"]["values"]["bldg"]["ROW"], 45)
        i2, _ = ts.planned("ep_yard", lambda: ts.site(700, 800, jur="East Point", zoning="I-2X"))
        self.assertEqual(i2["zoning"]["values"]["bldg"]["ROW"], 40)

    def test_atlanta_stands_back_from_homes(self):
        homes = ts.wgs(box(600, 0, 800, 700))
        rec, d = ts.planned("atl_homes", lambda: ts.site(600, 700, jur="Atlanta", neighbours=[({"LUCode": "101"}, homes)]))
        self.assertEqual(rec["zoning"]["values"]["bldg"]["RES_SIDE"], 31)
        self.assertEqual(rec["zoning"]["values"]["pave"]["RES_SIDE"], 20)
        self.assertTrue(rec["fits"])
        res = sp.unary_union([ln for k, ln in d.edges if k.startswith("RES")])
        self.assertGreaterEqual(d.p.building.distance(res), sp.OPEN_YARD_RULE_FT - 1)        # the open yard governs here
        # without the open yard the 31 ft of the height plane is what holds the building back:
        # the building envelope stops 31 ft from the homes' line, where the draft table stopped at 20
        with ts.mock.patch.object(sp, "OPEN_YARD_RULE_FT", 0), ts.mock.patch.object(ts.sitefit, "_angles", lambda rings: [0.0, 90.0]):
            free, d2 = sp.plan_site(ts.site(600, 700, jur="Atlanta", neighbours=[({"LUCode": "101"}, homes)]))
            proto, d3 = sp.plan_site(ts.site(600, 700, jur="Atlanta", neighbours=[({"LUCode": "101"}, homes)]),
                                     sp.PlanOptions(behaviour="prototype"))
        res2 = sp.unary_union([ln for k, ln in d2.edges if k == "RES_SIDE"])
        # (measured square to the line: the envelope is square at a line's ends)
        self.assertAlmostEqual(assess.band_clearance(d2.B, res2), 31, delta=1.5)
        res3 = sp.unary_union([ln for k, ln in d3.edges if k.startswith("RES")])
        self.assertAlmostEqual(assess.band_clearance(d3.B, res3), 20, delta=1.5)

    def test_open_yard_holds_at_an_inside_corner(self):
        # review 38/04, finding 1: a notch in the rear line; the building stood 53 ft from the notch's corner
        notch = [box(0, 0, 1000, 600).difference(box(400, 450, 600, 600))]
        rec, d = ts.planned("fair_notch", lambda: ts.site(shapes=notch, jur="Fairburn", front=LineString([(0, 0), (1000, 0)])))
        self.assertTrue(rec["fits"])
        lines = sp.unary_union([ln for k, ln in d.edges if k != "ROW"])
        self.assertGreaterEqual(d.p.building.distance(lines), sp.OPEN_YARD_RULE_FT - 0.5)
        self.assertGreaterEqual(d.B.distance(lines), sp.OPEN_YARD_RULE_FT - 0.5)             # the whole envelope, not only this plan
        yard = [r for r in rec["compliance"]["rows"] if r["item"].startswith("Open yard")][0]
        self.assertEqual(yard["status"], "meets")
        rear = [r for r in rec["compliance"]["rows"] if r["item"] == "Building setback, rear lot line"][0]
        self.assertEqual(rear["required"], "30 ft")                                          # the code's number, not the yard's

    def test_neighbour_behind_and_beside(self):
        # review 38/04, finding 2. A farm behind counts as residential in South
        # Fulton; the plan no longer fails on its car route (the fit is turned round).
        farm = ({"ClassCode": "A5"}, ts.wgs(box(0, 900, 900, 1100)))
        rec, d = ts.planned("sf_farm_behind", lambda: ts.site(900, 900, neighbours=[farm]))
        self.assertIs(rec["feasible"], True)
        self.assertIn("agricultural", [f["note"] for f in rec["flags"][2] if f["flag"] == "RESIDENTIAL_EDGE"][0])
        self.assertGreater(rec["inputs"]["edges_ft"]["RES_REAR"], 850)
        rear = sp.unary_union([ln for k, ln in d.edges if k == "RES_REAR"])
        self.assertGreaterEqual(d.p.building.distance(rear), 110 - 1)
        self.assertGreaterEqual(d.p.module.distance(rear), 110 - 1)
        # the same farm in Atlanta is not a residential edge
        atl, _ = ts.planned("atl_farm_behind", lambda: ts.site(900, 900, neighbours=[farm], jur="Atlanta"))
        self.assertNotIn("RESIDENTIAL_EDGE", ts.names(atl))
        # homes along a whole side are a side line (60 ft), though read 20 ft round the back corner
        beside = ({"LUCode": "101"}, ts.wgs(box(900, 0, 1100, 900)))
        side, _ = ts.planned("sf_homes_beside", lambda: ts.site(900, 900, neighbours=[beside]))
        self.assertGreater(side["inputs"]["edges_ft"]["RES_SIDE"], 850)
        self.assertLess(side["inputs"]["edges_ft"].get("RES_REAR", 0), 40)

    def test_fairburn_buffer_flag_covers_rear_lines(self):
        # review 38/04, finding 5: shops on both sides and industry behind
        shops = [({"ClassCode": "C3"}, ts.wgs(box(900, 0, 1100, 800))), ({"ClassCode": "C3"}, ts.wgs(box(-200, 0, 0, 800))),
                 ({"ClassCode": "I4"}, ts.wgs(box(0, 800, 900, 1000)))]
        rec, _ = ts.planned("fair_shops", lambda: ts.site(900, 800, jur="Fairburn", neighbours=shops))
        kinds = set(rec["inputs"]["edges_ft"])
        self.assertIn("COMM", kinds)
        self.assertIn("OTHER_REAR", kinds)
        self.assertIn("DISTRICT_BUFFER_NOT_DRAWN", ts.names(rec, 2))

    def test_code_truck_spaces_steer_the_choice(self):
        # review 38/04, finding 4: among passing layouts, one that meets the draft code minimum first
        shops = [({"ClassCode": "C3"}, ts.wgs(box(900, 0, 1100, 800))), ({"ClassCode": "E1"}, ts.wgs(box(-200, 0, 0, 800)))]
        rec, _ = ts.planned("fair_shops2", lambda: ts.site(900, 800, jur="Fairburn", neighbours=shops))
        need = lambda a: -(-a["sf"] // 5000)
        met = [a for a in rec["alternatives"] if a["geometry_ok"] and a["trailers"] >= need(a)]
        first = rec["alternatives"][0]
        if met:
            self.assertGreaterEqual(first["trailers"], need(first))                            # the plan drawn meets it
            self.assertEqual(first["sf"], max(a["sf"] for a in met))                           # and is the largest that does
        row = [r for r in rec["compliance"]["rows"] if r["item"] == "Truck spaces"][0]
        self.assertEqual(int(row["required"].split()[0]), -(-rec["building"]["sf"] // 5000))
        self.assertEqual(int(row["provided"].split()[0]), rec["trailers"]["total"])

    def test_corridor_on_a_shorter_frontage(self):
        # review 38/04, finding 7: the state route is not the longest frontage
        def corner():
            inp = ts.site(900, 900, jur="Palmetto", road="local road, GDOT route 1121200054600")
            inp.frontage.roads = ["local road, GDOT route 1121200054600", "minor arterial, SR 154"]
            return inp
        rec, _ = ts.planned("palm_corner", corner)
        self.assertEqual(rec["zoning"]["values"]["bldg"]["ROW"], 95)
        self.assertIn("fronts SR 154", [f["note"] for f in rec["flags"][2] if f["flag"] == "CORRIDOR_OVERLAY"][0])

    def test_prototype_read_out_states_no_default_decision(self):
        # review 38/04, finding 3
        proto = sp.PlanOptions(behaviour="prototype")

        def nopond_once(*a, **k):
            return None
        rec, _ = ts.planned("basic", lambda: ts.site(600, 700), proto)
        self.assertEqual(rec["operator_decisions"]["underground_detention"], "not set (prototype)")
        self.assertEqual(rec["operator_decisions"]["ranking"]["trailer_sf"], 2000)
        text = rec["selection"]["basis"] + " ".join(rec["assessment"]["yield"]["notes"])
        self.assertNotIn("does not trade floor area", text)
        self.assertNotIn("not used in this market", text)
        with ts.mock.patch.object(sp, "place_sized_pond", nopond_once), ts.mock.patch.object(ts.sitefit, "_angles", lambda rings: [0.0, 90.0]):
            blocked, _ = sp.plan_site(ts.site(600, 700), proto)
        acts = " ".join(c["action"] for c in blocked["assessment"]["conditions"])
        self.assertNotIn("not used in this market", acts)
        # the unit: no market stated, every larger pond-limited layout reported, the trailer sentence by the weights
        s = assess.selection([alt(67000, trailers=0)], sp.PROTOTYPE_RANKING, [alt(68000, ok=False)], underground=None)
        self.assertEqual(s["without_pond"]["sf"], 68000)
        self.assertIsNone(s["without_pond"]["underground"])
        self.assertNotIn("market", s["basis"])
        self.assertIn("It has 0 of its 10 target trailer stalls.", s["basis"])
        self.assertNotIn("does not trade", s["basis"])


@unittest.skipUnless(ts.FIXTURES.is_dir(), "no reference fixtures")
class ReferenceSite(ts.Offline):
    angles = False          # the real search angles, as in test_siteplan.ReferenceSites

    def test_reference_site_reports_the_other_layouts(self):
        # reference site #21 (a real parcel shape). With no trailer mark in
        # the ranking the largest passing layout is drawn; the read-out says
        # what meeting the trailer target would cost and what the pond holds.
        inp, _ = ts.load_fixture("rank21")
        rec, d = sp.plan_site(inp)
        sel = rec["selection"]
        self.assertIs(rec["feasible"], True)
        self.assertIn("TRAILER_SHORT", ts.names(rec, 2))
        self.assertIsNone(sel["upside"])                                     # the plan drawn is the largest that passed
        self.assertLess(sel["with_trailers"]["sf"], rec["building"]["sf"])
        self.assertGreaterEqual(sel["with_trailers"]["trailers"], 1)
        self.assertGreater(sel["without_pond"]["sf"], rec["building"]["sf"])
        self.assertIs(sel["without_pond"]["underground"], False)
        self.assertEqual(rec["total_sf"], rec["building"]["sf"])              # only the plan drawn is counted
        notes = " ".join(rec["assessment"]["yield"]["notes"])
        self.assertIn("Underground detention is not used in this market", notes)
        self.assertIn("Meeting the trailer target would cost floor area", notes)
        row = assess.pipeline_row(rec)
        self.assertEqual((row["meets_trailer_target_sf"], row["without_pond_sf"]), (sel["with_trailers"]["sf"], sel["without_pond"]["sf"]))
        svg = sr.render(d, rec)
        self.assertIn("surface pond", svg)
        self.assertIn("RESIDENTIAL EDGE", svg)
        # the open yard at the building: 60 ft or more on a plan the default behaviour draws
        yard = [r for r in rec["compliance"]["rows"] if r["item"].startswith("Open yard")][0]
        self.assertEqual(yard["status"], "meets")


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------
class Sheet(ts.Offline):

    def test_sheet_prints_the_read_out(self):
        rec, d = ts.planned("basic", lambda: ts.site(600, 700))
        svg = sr.render(d, rec)
        a = rec["assessment"]
        for must in (a["label"], "KEY NUMBERS", "NEXT STEPS", "WHERE THE LAND GOES", "PLAN AGAINST THE DRAFT ZONING VALUES",
                     "NOT THE ADOPTED CODE", "LAYOUTS COMPARED", "CONDITIONS", "LEGEND", "DRAFT", "NOT FOR CONSTRUCTION",
                     "TRUCK COURT", "DOCK DOORS", "CAR STALLS", "DETENTION POND", "TRUCK ENTRY", "FT FRONTAGE",
                     "Open yard at the building (building code)", "ZONING_DRAFT", "zoning verification letter",
                     sr.DISCLAIMER[:40]):
            self.assertIn(must, svg, must)
        root = ET.fromstring(svg)
        tags = {e.tag.split("}")[-1] for e in root.iter()}
        self.assertLessEqual(tags, {"svg", "title", "rect", "defs", "pattern", "line", "path", "circle", "text"})
        self.assertNotIn("href", svg.lower())
        self.assertEqual(float(root.get("width")), sr.W)
        self.assertEqual(sr.render(d, rec), svg)                            # the same plan, the same bytes
        self.assertEqual(set(re.findall(r"url\(([^)]*)\)", svg)), {"#sx", "#tx", "#cx", "#wx"} & set(re.findall(r"url\(([^)]*)\)", svg)))
        # the building's length and depth are dimension strings on the plan
        b = rec["building"]
        self.assertIn(f">{int(b['L_ft'])}&#x27;<", svg.replace("&apos;", "&#x27;").replace("&#39;", "&#x27;"))
        self.assertIn(f">{int(b['D_ft'])}&#x27;<", svg.replace("&apos;", "&#x27;").replace("&#39;", "&#x27;"))
        # what is what: the main uses do not share a colour
        fills = [sr.C[k] for k in ("parcel", "bldg", "court", "trailer", "car", "pond", "street", "office")]
        self.assertEqual(len(set(fills)), len(fills))
        viewer.sanitise_svg(svg)                                            # the viewer's allow-list takes it

    def test_sheet_scale_matches_the_drawing(self):
        rec, d = ts.planned("basic", lambda: ts.site(600, 700))
        info = sr.sheet_info(d)
        # 700 ft is the longer side; it spans the plan box less its label margins
        self.assertAlmostEqual(info["ft_per_px"], 700 / (sr.PLAN - 2 * sr.PAD), places=3)
        svg = sr.render(d, rec)
        bar = re.search(r">0</text><text[^>]*>[\d,]+</text><text[^>]*>([\d,]+) ft</text>", svg)
        segs = re.findall(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)" height="6"', svg)
        self.assertEqual(len(segs), 4)
        px = sum(float(w) for _, w in segs)
        self.assertAlmostEqual(float(bar.group(1).replace(",", "")) / px, info["ft_per_px"], delta=0.01)

    def test_text_stays_on_the_sheet(self):
        rec, d = ts.planned("basic", lambda: ts.site(600, 700))
        long_title = "#1 " + "LONG NAME ROAD " * 20
        d2 = sp.Drawing(**{**d.__dict__, "title": long_title})
        root = ET.fromstring(sr.render(d2, rec))
        height = float(root.get("height"))
        for e in root.iter():
            if e.tag.endswith("text") and e.get("transform") is None:
                size = float(e.get("font-size"))
                width = sr.text_width(e.text or "", size, e.get("font-weight") == "bold")
                x = float(e.get("x"))
                anchor = e.get("text-anchor", "start")
                right = x + (width if anchor == "start" else width / 2 if anchor == "middle" else 0)
                self.assertLess(right, sr.W - sr.M + 6, e.text)
                self.assertLess(float(e.get("y")), height)

    def test_wrap_keeps_to_the_width(self):
        prose = "Check the setbacks, buffers, parking, coverage and height used here against the adopted code. " * 3
        caps = "SECOND_DROP_YARD_UNREACHABLE: A FLAG NAME AND A NOTE IN CAPITALS RUN WIDER THAN PROSE DOES " * 2
        for txt in (prose, caps, "W" * 400, "one " + "x" * 300 + " two", ""):
            for bold in (False, True):
                lines = sr._wrap(txt, 300, 10.5, bold)
                self.assertTrue(lines)
                for ln in lines:
                    self.assertLessEqual(sr.text_width(ln, 10.5, bold), 300.5, ln)
                self.assertEqual("".join(lines).replace(" ", ""), txt.replace(" ", ""))      # nothing lost
        self.assertGreater(sr.text_width("WWWW", 10), sr.text_width("iiii", 10) * 3)

    def test_second_building_and_no_assessment_key(self):
        rec, d = ts.planned("second", lambda: ts.site(1600, 1000, front=ts.TWO_FRONTS))
        self.assertIn("second_building", rec)                              # the site below does hold two
        svg = sr.render(d, rec)
        self.assertIn("BUILDING 2", svg)
        self.assertEqual(rec["site_data"]["buildings"], 2)
        rows = {r["item"] for r in rec["compliance"]["rows"]}
        self.assertIn("Car parking (building 1)", rows)
        self.assertLessEqual(rec["quantities"]["disturbed_ac"], rec["site_data"]["land"]["net_ac"] + 0.05)
        land = rec["site_data"]["land"]
        self.assertAlmostEqual(sum(u["ac"] for u in land["uses"]) + land["unused_net_ac"] + land["outside_net_unused_ac"],
                               land["gross_ac"], delta=0.1)
        # a record without the stored read-out still draws (the sheet derives the assessment)
        bare = {k: v for k, v in rec.items() if k not in ("assessment", "site_data", "compliance", "quantities", "selection")}
        svg = sr.render(d, bare)
        self.assertIn("CONDITIONS", svg)
        ET.fromstring(svg)


# ---------------------------------------------------------------------------
# The pipeline table and the CLI
# ---------------------------------------------------------------------------
class PipelineTable(ts.Offline):
    title = None

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sp.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_row_and_formula_guard(self):
        rec, _ = ts.planned("basic", lambda: ts.site(600, 700))
        row = assess.pipeline_row(rec)
        self.assertEqual(set(row), set(assess.PIPELINE_COLUMNS))
        self.assertEqual((row["status"], row["counted_sf"], row["parcel_ids"]), (rec["assessment"]["status"], rec["total_sf"], "T1"))
        self.assertNotIn("TEST RD", " ".join(str(v) for v in row.values()))        # no title, no address
        evil = dict(rec, rank="=cmd|' /C calc'!A0")
        text = assess.pipeline_csv([evil], viewer.csv_cell)
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], list(assess.PIPELINE_COLUMNS))
        self.assertTrue(rows[1][0].startswith("'="))

    def test_cli_writes_the_table_and_the_sheet_scale(self):
        ts.Cli.fake_inputs(self)
        run = ts.fake_run(self.tmp)
        out = self.tmp / "out"
        code, _, se = self.run_cli([str(run), "--ranks", "1,2", "--out", str(out)])
        self.assertEqual(code, 0, se)
        body = json.loads((out / "plans.json").read_text())
        self.assertEqual(body["schema_version"], 2)
        rows = list(csv.DictReader(io.StringIO((out / "plans.csv").read_text())))
        self.assertEqual([r["site"] for r in rows], [r["rank"] for r in body["plans"]])
        for row, rec in zip(rows, body["plans"]):
            self.assertEqual(row["status"], rec["assessment"]["status"])
            self.assertEqual(int(row["counted_sf"]), rec["total_sf"])
            if rec.get("svg"):
                self.assertGreater(rec["sheet"]["ft_per_px"], 0)
        # the viewer shows the read-out and takes its scale from the sheet
        page = self.tmp / "page.html"
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(viewer.main([str(run), "--plans", str(out), "--out", str(page)]), 0, err.getvalue())
        plans = {p["id"]: p for p in tv.page_data(page.read_text(encoding="utf-8"))}
        for rec in body["plans"]:
            if not rec.get("svg"):
                continue
            p = plans["plan-" + rec["key"]]
            a = p["assessment"]
            self.assertEqual(a["status"], rec["assessment"]["status"])
            self.assertEqual(a["headline"], rec["assessment"]["headline"])
            self.assertEqual(len(a["conditions"]), len(rec["assessment"]["conditions"]))
            self.assertEqual([c["flag"] for c in a["conditions"]], [c["flag"] for c in rec["assessment"]["conditions"]])
            self.assertEqual(len(a["compliance"]), len(rec["compliance"]["rows"]))
            self.assertAlmostEqual(a["land"]["gross_ac"], rec["site_data"]["land"]["gross_ac"])
            self.assertAlmostEqual(p["fpp"], rec["sheet"]["ft_per_px"])
            self.assertEqual(p["alternatives"][0]["doors"], rec["alternatives"][0]["doors"])

    def test_empty_call_still_writes_a_header(self):
        self.assertEqual(assess.pipeline_csv([]).strip(), ",".join(assess.PIPELINE_COLUMNS))


# ---------------------------------------------------------------------------
# The viewer's copy of the read-out
# ---------------------------------------------------------------------------
class ViewerReadOut(unittest.TestCase):

    def test_legacy_record_has_no_assessment(self):
        self.assertIsNone(viewer.plan_assessment(tv.plan_record("3", ["P3"], "plan-3.svg")))
        self.assertIsNone(viewer.plan_assessment({"assessment": {"status": "made up"}}))

    def test_fields_are_coerced_and_capped(self):
        a = {"status": "blocked", "label": "X" * 500, "headline": "<b>h</b>" + "y" * 2000, "counted": 1,
             "yield": {"position": "sideways", "comp_band": [10500, "x", 3], "notes": ["n"] * 40},
             "next_steps": [{"stage_label": "Desk check", "workstream": "W", "action": "A" * 2000, "closes_with": {"no": 1}}] * 30,
             "conditions": [{"n": "1", "flag": "ROUTE_INFEASIBLE", "kind": "evil", "finding": ["x"], "action": "do"}] * 200}
        t = {"assessment": a, "site_data": {"land": {"gross_ac": "9.4", "uses": [{"label": "L", "ac": "1.5", "pct_gross": "x"}] * 40}},
             "compliance": {"rows": [{"item": "I", "status": "nope", "margin_ft": "4"}] * 90}, "selection": {"basis": "b" * 5000}}
        out = viewer.plan_assessment(t)
        self.assertLessEqual(len(out["label"]), 60)
        self.assertLessEqual(len(out["headline"]), viewer.TEXT_CAP)
        self.assertIsNone(out["position"])
        self.assertEqual(out["band"], [10500, None])
        self.assertEqual((len(out["yieldNotes"]), len(out["steps"]), len(out["conditions"])), (8, viewer.MAX_STEPS, viewer.MAX_CONDITIONS))
        self.assertIsNone(out["steps"][0]["closes"])
        self.assertEqual((out["conditions"][0]["kind"], out["conditions"][0]["finding"], out["conditions"][0]["n"]),
                         ("information", None, 1))
        self.assertEqual((out["land"]["gross_ac"], len(out["land"]["uses"]), out["land"]["uses"][0]["pct"]), (9.4, 12, None))
        self.assertEqual((len(out["compliance"]), out["compliance"][0]["status"], out["compliance"][0]["margin"]),
                         (viewer.MAX_ROWS, "verify", 4))
        self.assertLessEqual(len(out["selection"]), 900)


if __name__ == "__main__":
    unittest.main()
