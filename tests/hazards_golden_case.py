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
