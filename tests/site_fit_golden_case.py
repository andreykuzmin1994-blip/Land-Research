"""Shared fixture for the site-fit scoring tests (reviews/22_site_fit, SF-01/02).

``run_case(research)`` scores a fixed set of parcels through
``research.score_parcel`` (no cache) and ``research.run_scoring_cycle`` (the
cached path) against an answer-by-SQL fake connection and returns everything
observable: result dicts, every executed statement with its parameters, the
parcel_scores insert tuples and the research_log notes (the
zoning_golden_case pattern, whose fake it reuses).

The parcels are the zoning fixture's twelve plus eight that matter to the
projection: acreage straddling the 8.61 ac BTS proxy (8.60 / 8.62) in
several jurisdictions, null geometry, a multipart buildable area, a parcel
that is empty after the setback buffer, a parcel with no zoning, and large
by-right parcels whose projected building is just below / at 150K SF.

Old code (28d3205) never asks for a projection, so the fake answers the
site_fit queries only when they are issued. The frozen record of 28d3205 is
reviews/22_site_fit/golden_scores_head_28d3205.json, checked by
tests/site_fit_golden_check.py (NOT part of the suite).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest import mock

import prepare
import zoning_golden_case as zg

_V = "zoning_import v1"
M2_PER_SQFT = 0.3048 * 0.3048
X0, Y0 = 2_000_000.0, 1_300_000.0


def _desc(j: str, src: str, code: str) -> str:
    return " | ".join((_V, j, src, code, "-", "-"))


SF_M1 = ("M-1", _desc("south_fulton", "south_fulton_zoning", "M-1"))
FB_M1 = ("M-1", _desc("fairburn", "fairburn_zoning", "M-1"))
AT_I1 = ("I-1", _desc("atlanta", "atlanta_zoning", "I-1"))
AT_I2 = ("I-2", _desc("atlanta", "atlanta_zoning", "I-2"))
UC_M1 = ("M-1", _desc("union_city", "union_city_zoning", "M-1"))

# parcel_id -> (submarket, acreage, assessed, zoning, zoning_description, lng, lat)
EXTRA: dict[str, tuple] = {
    "fulton-S01": ("atlanta__sf", 8.60, 1_100_000, *SF_M1, -84.551, 33.551),
    "fulton-S02": ("atlanta__sf", 8.62, 1_100_000, *SF_M1, -84.552, 33.552),
    "fulton-S03": ("atlanta__fb", 8.60, 700_000, *FB_M1, -84.601, 33.551),
    "fulton-S04": ("atlanta__wa", 8.62, 2_000_000, *AT_I1, -84.501, 33.751),
    "fulton-S05": ("atlanta__sf", 30.0, 1_500_000, *SF_M1, -84.553, 33.553),
    "fulton-S06": ("atlanta__wa", 12.0, 2_500_000, *AT_I2, -84.502, 33.752),
    "fulton-S07": ("atlanta__uc", 5.5, 800_000, *UC_M1, -84.541, 33.581),
    "fulton-S08": ("atlanta__sf", 20.0, 900_000, None, None, -84.554, 33.554),
    "fulton-S09": ("atlanta__sf", 12.0, 1_300_000, *SF_M1, -84.555, 33.555),
    "fulton-S10": ("atlanta__sf", 12.0, 1_300_000, *SF_M1, -84.556, 33.556),
}
PARCELS: dict[str, tuple] = {**zg.PARCELS, **EXTRA}

# Geometry for the site_fit queries: parcel_id -> spec. 'rect' parcels are
# axis-aligned W x H rectangles (feet), so the inward buffer is exact.
# S09/S10: a front-load envelope is 445 ft deep, so with the South Fulton
# setback (40 ft) a 530 ft deep lot holds it (450 ft inside); 640.1 ft of
# frontage leaves 560.1 ft (560 -> 145,600 SF, below 150K) and 680.1 ft
# leaves 600.1 ft (600 -> 156,000 SF, at least 150K).
GEOMETRY: dict[str, tuple] = {
    "fulton-S01": ("rect", 700.0, 535.0),
    "fulton-S02": ("rect", 760.0, 520.0),
    "fulton-S03": ("rect", 760.0, 525.0),
    "fulton-S04": ("rect", 760.0, 530.0),
    "fulton-S05": ("none",),
    "fulton-S06": ("multi",),
    "fulton-S07": ("empty",),
    "fulton-S09": ("rect", 640.1, 530.0),
    "fulton-S10": ("rect", 680.1, 530.0),
}


def _parcel_row(pid: str) -> tuple:
    sub, ac, assessed, _z, _d, lng, lat = PARCELS[pid]
    return (pid, "atlanta", sub, "GA", ac, None, None, assessed, lng, lat)


def _rect_json(x0: float, y0: float, x1: float, y1: float) -> list:
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]


def buffered_row(pid: str, setback: float) -> tuple | None:
    """What _SQL_BUFFERED_FOR_FIT returns for one parcel (None = no row)."""
    spec = GEOMETRY.get(pid, ("rect", 600.0, 600.0))
    if spec[0] == "none":
        return None
    if spec[0] == "empty":
        return (pid, 5.5 * 43_560 * M2_PER_SQFT, True, 0.0,
                json.dumps({"type": "Polygon", "coordinates": []}))
    if spec[0] == "multi":
        a = _rect_json(X0, Y0, X0 + 700, Y0 + 300)
        b = _rect_json(X0 + 800, Y0, X0 + 1500, Y0 + 300)
        return (pid, 12 * 43_560 * M2_PER_SQFT, False, 2 * 700 * 300.0,
                json.dumps({"type": "MultiPolygon", "coordinates": [a, b]}))
    _, w, h = spec
    iw, ih = w - 2 * setback, h - 2 * setback
    if iw <= 0 or ih <= 0:
        return (pid, w * h * M2_PER_SQFT, True, 0.0,
                json.dumps({"type": "Polygon", "coordinates": []}))
    return (pid, w * h * M2_PER_SQFT, False, iw * ih,
            json.dumps({"type": "Polygon", "coordinates": _rect_json(
                X0 + setback, Y0 + setback, X0 + setback + iw, Y0 + setback + ih)}))


def responder(research: Any, *, site_fit_errors: bool = False):
    """(sql, params) -> rows, for both old and new research.py."""
    base_parcels = zg.PARCELS
    zsql = {getattr(research, n, None) for n in (
        "_SQL_STORED_ZONING", "_SQL_STORED_ZONING_BATCH", "_SQL_STORED_ZONING_FOR_MARKET")}
    zsql.discard(None)
    sf = getattr(research, "site_fit", None)
    sf_zoning = getattr(sf, "_SQL_ZONING_FOR_FIT", object())
    sf_buffer = getattr(sf, "_SQL_BUFFERED_FOR_FIT", object())

    def answer(sql: str, params: tuple) -> list[tuple]:
        if sql == research._SQL_LIST_PARCELS_FOR_SCORING_RUN_SCOPED or \
                sql == research._SQL_LIST_PARCELS_FOR_SCORING:
            return [(pid,) for pid in sorted(PARCELS)]
        if sql == research._SQL_FETCH_PARCEL:
            return [_parcel_row(params[0])] if params[0] in PARCELS else []
        if sql == research._SQL_S2_GEOMETRY:
            if GEOMETRY.get(params[0], ("rect",))[0] == "none":
                return []
            ac = PARCELS[params[0]][1]
            return [(ac * 4046.86, ac * 4046.86 * 1.3, 1.8)]
        if sql == research._SQL_DISTINCT_SUBMARKETS_FOR_PARCELS:
            return sorted({(PARCELS[p][0],) for p in params[0] if PARCELS[p][0]})
        if sql in zsql:
            ids = params[0] if isinstance(params[0], list) else [params[0]]
            return [((p,) if isinstance(params[0], list) else ()) + PARCELS[p][3:5]
                    for p in ids if p in PARCELS]
        if sql == sf_zoning:
            if site_fit_errors:
                raise RuntimeError("injected")
            return [(p,) + PARCELS[p][3:5] for p in sorted(params[0]) if p in PARCELS]
        if sql == sf_buffer:
            if site_fit_errors:
                raise RuntimeError("injected")
            setback = float(params[1] if len(params) > 2 else params[0])
            rows = [buffered_row(p, setback) for p in params[-1]]
            return [r for r in rows if r is not None]
        with mock.patch.dict(base_parcels, PARCELS, clear=False):
            return zg.responder(research)(sql, params)
    return answer


def run_case(research: Any, *, site_fit_errors: bool = False) -> dict[str, Any]:
    """Score every fixture parcel both ways; return the observable record."""
    params = prepare.get_parameters()
    out: dict[str, Any] = {"per_parcel": {}, "cycle": {}}
    research._OZ_TRACTS_CACHE = None
    answer = responder(research, site_fit_errors=site_fit_errors)
    for pid in sorted(PARCELS):
        conn = zg.AnswerConn(answer)
        result = research.score_parcel(
            pid, conn=conn, cycle_id="score-atlanta-sitefit", params=params,
            run_tag="golden", experiment_id="exp-golden")
        out["per_parcel"][pid] = {"result": zg._jsonable(result), **zg.observe(conn, research)}

    conn = zg.AnswerConn(answer)

    @contextmanager
    def _get_connection():
        yield conn

    with mock.patch.object(prepare, "get_connection", _get_connection), \
            mock.patch.object(research, "_make_scoring_cycle_id",
                              return_value="score-atlanta-sitefit-cycle"):
        summary = research.run_scoring_cycle(
            "atlanta", run_tag="golden", experiment_id="exp-golden")
    out["cycle"] = {"summary": zg._jsonable(summary), **zg.observe(conn, research)}
    return out
