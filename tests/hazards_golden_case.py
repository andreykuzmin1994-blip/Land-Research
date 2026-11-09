"""Shared fixture for the flood/wetland golden checks (reviews/26_flood_wetlands, FW-03/25).

``run_case(research)`` records everything observable on BOTH paths that the
hazard change touches, for any research.py (old or new):

- discovery: every hard filter's result on each fixture feature, and
  ``_process_parcel`` over every discovery fixture feature (status, every
  executed statement with its parameters), once with the idempotent
  open-gap set registered for the cycle and once without;
- scoring: the site-fit golden case (``site_fit_golden_case.run_case``:
  per-parcel and cycle path, every statement, the parcel_scores inserts and
  the research_log notes).

The frozen record of research.py at 8ddaf69 (before the hazards module
existed) is reviews/26_flood_wetlands/golden_head_8ddaf69.json, checked by
tests/hazards_golden_check.py (NOT part of the suite). Old code has no
``hazards`` attribute; the fake answers no hazard query.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import prepare
import site_fit_golden_case as sg
import zoning_golden_case as zg

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "discovery"
FIXTURE_FILES = (
    "arcgis_query_two_features.json", "arcgis_query_multipolygon.json",
    "arcgis_query_under_acreage.json", "arcgis_query_outside_envelope.json",
    "arcgis_query_state_plane.json", "arcgis_query_pagination_page1.json",
)
CYCLE_ID = "disco-fulton-20261106T000000Z-abcd"


def _features() -> list[tuple[str, int, dict[str, Any]]]:
    out = []
    for name in FIXTURE_FILES:
        data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        for i, feat in enumerate(data.get("features") or []):
            out.append((name, i, feat))
    return out


def _sources() -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    return json.loads((root / "sources.json").read_text(encoding="utf-8"))


def run_discovery(research: Any) -> dict[str, Any]:
    params = prepare.get_parameters()
    mapping = _sources()["county_parcel_data"]["fulton_ga"]["field_mapping"]
    classification = params["owner_classification"]
    out: dict[str, Any] = {"filters": {}, "process": {}}
    for name, i, feat in _features():
        key = f"{name}#{i}"
        row, _wkt, skip, _multi = research._map_feature_to_parcel(
            feat, mapping, classification, "atlanta", "south_fulton_campbellton",
            "/tmp/cache/x.json")
        if row is not None:
            row = dict(row)
            row["_stored_zoning"] = None
            out["filters"][key] = [
                [r.action, r.filter_id, r.reason]
                for r in (f(row, None, params) for f in research._HARD_FILTERS)]
        else:
            out["filters"][key] = ["unmappable", str(skip)]
        for mode in ("dedup", "nodedup"):
            conn = zg.AnswerConn(lambda sql, p: [])
            if mode == "dedup":
                research._OPEN_DATA_GAPS_BY_CYCLE[CYCLE_ID] = set()
            try:
                status = research._process_parcel(
                    feat, conn, CYCLE_ID, "south_fulton_campbellton", "atlanta",
                    mapping, classification, params, raw_response_path="/tmp/cache/x.json")
            finally:
                research._OPEN_DATA_GAPS_BY_CYCLE.pop(CYCLE_ID, None)
            out["process"][f"{key}/{mode}"] = {
                "status": status,
                "executed": zg._jsonable([[s, list(p)] for s, p in conn.executed]),
            }
    return out


def run_case(research: Any) -> dict[str, Any]:
    return {"discovery": run_discovery(research), "scoring": sg.run_case(research)}


# ---------------------------------------------------------------------------
# The gate ON with the table EMPTY (the shipped state since the operator-
# authorized prepare-mutation of 2026-11-09, reviews/33_hazards_enabled; the
# fake above answers the hazard read with no rows). Against the 8ddaf69
# record the ONLY allowed differences are the documented flag-text delta:
# - discovery: H4/H6 ``flag`` (the "unjoined" stubs) -> ``pass`` with an
#   empty reason; ``_process_parcel`` statuses identical; its statements
#   identical apart from the two stub flagged_items INSERTs, now absent;
# - scoring (per parcel and cycle): results, parcel_scores inserts and
#   research_log notes identical; statements identical apart from the one
#   batched hazard read per path and the two "no hazards import for this
#   parcel" data_gap flagged_items INSERTs per parcel, now present.
H4_STUB = "H4 flood unjoined: pending FEMA NFIP wiring (Phase 5+)"
H6_STUB = "H6 wetlands unjoined: pending USGS NWI mapper wiring (Phase 5+)"
H4_GAP = "H4 flood: no hazards import for this parcel (FEMA NFHL not assessed)"
H6_GAP = "H6 wetlands: no hazards import for this parcel (USFWS NWI not assessed)"


def _flag_ending(entry: Any, endings: tuple[str, ...], flag_type: str) -> bool:
    sql, params = entry
    return ("INSERT INTO flagged_items" in sql and len(params) > 3
            and params[0] == flag_type and str(params[3]).endswith(endings))


def differences_on_empty(want: dict[str, Any], got: dict[str, Any],
                         hazard_read_sql: str) -> list[str]:
    """The cases where ``got`` (gate on, empty table) differs from ``want``
    (the 8ddaf69 record) beyond the documented delta. Empty = proven."""
    bad: list[str] = []
    wf, gf = want["discovery"]["filters"], got["discovery"]["filters"]
    if sorted(wf) != sorted(gf):
        bad.append("<discovery filters keys>")
    for k, w in wf.items():
        g = gf.get(k)
        if not isinstance(w, list) or not w or w[0] == "unmappable":
            if g != w:
                bad.append(f"discovery filters {k}")
            continue
        expect = [["pass", fid, ""] if fid in ("H4", "H6") and act == "flag" else [act, fid, why]
                  for act, fid, why in w]
        if g != expect:
            bad.append(f"discovery filters {k}")
    wp, gp = want["discovery"]["process"], got["discovery"]["process"]
    if sorted(wp) != sorted(gp):
        bad.append("<discovery process keys>")
    for k, w in wp.items():
        g = gp.get(k) or {}
        stubs = [e for e in w["executed"] if _flag_ending(e, (H4_STUB, H6_STUB), "data_gap")]
        rest = [e for e in w["executed"] if e not in stubs]
        if g.get("status") != w["status"] or g.get("executed") != rest \
                or len(stubs) not in (0, 2):
            bad.append(f"discovery process {k}")
    ws, gs = want["scoring"], got["scoring"]
    if sorted(ws["per_parcel"]) != sorted(gs["per_parcel"]):
        bad.append("<scoring parcel set>")

    def scoring_same(w: dict[str, Any], g: dict[str, Any], keys: tuple[str, ...],
                     parcels: int) -> bool:
        if any(g.get(x) != w[x] for x in keys):
            return False
        reads = [e for e in g["executed"] if e[0] == hazard_read_sql]
        gaps = [e for e in g["executed"] if _flag_ending(e, (H4_GAP, H6_GAP), "data_gap")]
        rest = [e for e in g["executed"] if e[0] != hazard_read_sql and e not in gaps]
        return len(reads) == 1 and len(gaps) == 2 * parcels and rest == w["executed"]

    for pid, w in ws["per_parcel"].items():
        g = gs["per_parcel"].get(pid)
        if g is None or not scoring_same(
                w, g, ("result", "parcel_score_inserts", "research_log_notes"), 1):
            bad.append(f"scoring {pid}")
    if not scoring_same(ws["cycle"], gs["cycle"],
                        ("summary", "parcel_score_inserts", "research_log_notes"),
                        len(ws["per_parcel"])):
        bad.append("<scoring cycle>")
    return bad
