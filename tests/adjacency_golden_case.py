"""Shared fixture for the adjacency differential (reviews/27_site_fit_buffers, BUF-03).

``run_case(research, site_fit, reporting)`` returns everything observable
that the adjacency change could move, with every zoning jurisdiction of the
fixture VERIFIED (so BTS reads projections) and, for the projections and
the memo only, once more all-draft:

- ``scoring``: the site-fit golden case (research.score_parcel per parcel
  and run_scoring_cycle): result dicts, every executed statement and
  parameter, the parcel_scores insert tuples and the research_log notes;
- ``projections``: site_fit.project_parcels over every fixture parcel
  (every field of the 595aa08 Projection, ring text included) plus the
  statements it executed;
- ``memo``: reporting's per-parcel highlight line (used / not used for BTS),
  the Development Potential block of the snapshot, and the memo footnote.

Old code (595aa08) has no adjacency module; the fake answers the adjacency
read with no rows (the "gate on, table empty" state) when it is issued.
The frozen record of 595aa08 is
reviews/27_site_fit_buffers/golden_head_595aa08.json, checked by
tests/adjacency_golden_check.py (NOT part of the suite) and by
tests/test_adjacency_scoring.DifferentialTests (offline).
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import site_fit_golden_case as sg
import zoning_golden_case as zg
from zoning_helpers import patch_rules, verified

JURISDICTIONS = ("south_fulton", "fairburn", "atlanta", "union_city")
PROJECTION_FIELDS = ("parcel_id", "status", "reason", "rules_label", "rules_verified",
                     "setback_ft", "setback_source", "gross_sf", "buildable_sf",
                     "coverage_cap_pct", "fits", "constraints", "multipart",
                     "setback_from_rules", "flood_wetlands", "_geometry")


def _adjacency_sql(site_fit: Any) -> Any:
    adj = getattr(site_fit, "_adj", None)
    return getattr(adj, "_SQL_ADJACENCY_FOR_PARCELS", object())


def _wrap(responder: Any, site_fit: Any):
    adj_sql = _adjacency_sql(site_fit)

    def make(research: Any, **kw: Any):
        answer = responder(research, **kw)

        def ans(sql: str, params: tuple) -> list[tuple]:
            if sql == adj_sql:
                return []
            return answer(sql, params)
        return ans
    return make


def _projection(p: Any) -> dict[str, Any]:
    return zg._jsonable({f: getattr(p, f) for f in PROJECTION_FIELDS})


def _projections(research: Any, site_fit: Any, reporting: Any) -> dict[str, Any]:
    conn = zg.AnswerConn(_wrap(sg.responder, site_fit)(research))
    site_fit._FIT_CACHE.clear()
    ids = sorted(sg.PARCELS)
    projs = site_fit.project_parcels(conn, ids)
    out = {pid: _projection(projs[pid]) for pid in ids}
    memo = {}
    for pid in ids:
        z = sg.PARCELS[pid][3:5]
        parcel = {"parcel_id": pid, "zoning": z[0], "zoning_description": z[1]}
        memo[pid] = {
            "summary_used": reporting._projection_summary(projs[pid], True),
            "summary_not_used": reporting._projection_summary(projs[pid], False),
            "development_potential": reporting._render_development_potential(parcel,
                                                                              projs[pid]),
        }
    fn = getattr(reporting, "_projection_footnote", None)
    footnote = fn() if callable(fn) else reporting._PROJECTION_FOOTNOTE
    return {"projections": out, "executed": zg._jsonable([[s, list(p)]
                                                          for s, p in conn.executed]),
            "memo": memo, "footnote": footnote}


def run_case(research: Any, site_fit: Any, reporting: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, rules in (("verified", verified(*JURISDICTIONS)), ("draft", None)):
        with patch_rules(rules), \
                mock.patch.object(sg, "responder", _wrap(sg.responder, site_fit)):
            # All-draft scoring never reads a projection (the existing golden
            # checks pin it); snapshots and memos project every parcel.
            rec = {"scoring": sg.run_case(research)} if label == "verified" else {}
            rec.update(_projections(research, site_fit, reporting))
        out[label] = rec
    return out


# ---------------------------------------------------------------------------
# Comparison against the 595aa08 record (current code only)
# ---------------------------------------------------------------------------
def differences_off(want: dict[str, Any], got: dict[str, Any]) -> list[str]:
    """Gate off: everything identical."""
    bad = []
    for label in want:
        for key in want[label]:
            if got.get(label, {}).get(key) != want[label][key]:
                bad.append(f"{label}.{key}")
    return bad


def differences_on_empty(want: dict[str, Any], got: dict[str, Any], site_fit: Any,
                         adjacency: Any) -> list[str]:
    """Gate on, empty table: parcel_scores, results and notes identical; the
    executed SQL identical apart from the one adjacency read; every
    projection field identical (ring text included) apart from the
    adjacency constraint texts; memo lines identical apart from the
    adjacency clause; the footnote's RES_BUFFERS clause replaced."""
    bad: list[str] = []
    adj_sql = adjacency._SQL_ADJACENCY_FOR_PARCELS

    def strip(ex: list) -> list:
        return [e for e in ex if e[0] != adj_sql]
    w, g = want["verified"]["scoring"], got["verified"]["scoring"]
    for pid in w["per_parcel"]:
        a, b = w["per_parcel"][pid], g["per_parcel"].get(pid, {})
        for k in ("result", "parcel_score_inserts", "research_log_notes"):
            if a[k] != b.get(k):
                bad.append(f"scoring {pid} {k}")
        if strip(a["executed"]) != strip(b.get("executed", [])):
            bad.append(f"scoring {pid} executed")
    for k in ("summary", "parcel_score_inserts", "research_log_notes"):
        if w["cycle"][k] != g["cycle"][k]:
            bad.append(f"scoring cycle {k}")
    if strip(w["cycle"]["executed"]) != strip(g["cycle"]["executed"]):
        bad.append("scoring cycle executed")
    # ADJ_NO_DISTRICT (review finding 1): a parcel whose district the rules do
    # not resolve says so, never ADJ_NO_RULE.
    adj_texts = {adjacency.ADJ_NOT_IMPORTED, adjacency.ADJ_NO_RULE, adjacency.ADJ_NO_GEOMETRY,
                 adjacency.ADJ_NO_DISTRICT, adjacency.HEIGHT_NOT_APPLIED}
    for label in want:
        wp, gp = want[label], got[label]
        if strip(wp["executed"]) != strip(gp["executed"]):
            bad.append(f"{label} projections executed")
        for pid, a in wp["projections"].items():
            b = gp["projections"].get(pid, {})
            for f in PROJECTION_FIELDS:
                if f != "constraints" and a[f] != b.get(f):
                    bad.append(f"{label} {pid} {f}")
            kept = [c for c in a["constraints"] if c != site_fit.RES_BUFFERS]
            new = [c for c in b.get("constraints", []) if c not in a["constraints"]]
            if [c for c in b.get("constraints", []) if c in a["constraints"]] != kept or \
                    any(c not in adj_texts for c in new) or site_fit.RES_BUFFERS in \
                    b.get("constraints", []):
                bad.append(f"{label} {pid} constraints")
            ma, mb = wp["memo"][pid], gp["memo"][pid]
            for k in ("summary_used", "summary_not_used"):
                found = [t for t in adj_texts if t in mb[k]]
                if not found or mb[k].replace(found[0], adjacency.ADJ_OFF) != ma[k]:
                    bad.append(f"{label} {pid} memo {k}")
            la = [x for x in ma["development_potential"].splitlines()
                  if not x.startswith("- **Constraints applied**")]
            lb = [x for x in mb["development_potential"].splitlines()
                  if not x.startswith("- **Constraints applied**")]
            if la != lb:
                bad.append(f"{label} {pid} memo development_potential")
        if gp["footnote"] != wp["footnote"].replace(site_fit.RES_BUFFERS, adjacency.FOOTNOTE):
            bad.append(f"{label} footnote")
    return bad
