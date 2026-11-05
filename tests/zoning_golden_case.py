"""Shared fixture for the zoning scoring tests (reviews/21_zoning, Z-03).

``run_case(research)`` scores a fixed set of parcels through
``research.score_parcel`` (no cache) and ``research.run_scoring_cycle`` (the
cached production path) against an answer-by-SQL fake connection, and
returns everything observable: result dicts, every executed statement with
its parameters (so a new query shows up), the parcel_scores insert tuples
and the research_log notes.

The parcels carry imported zoning in every shape the import writes or the
corridor holds: by-right, rezoning, unlisted, '-C' and 'OVERLAY' codes, null
zoning, East Point, an uncovered city, FID's M-1A, a split parcel and a
zoning value WITHOUT import provenance (as a discovery edit would leave).

The suite uses it DIFFERENTIALLY (tests/test_zoning_scoring.py: the current
research.py with the real draft rules vs. with zoning absent), so ordinary
scoring experiments stay keepable (review finding 1). The frozen record of
the pre-zoning research.py (commit ba4a780) is a one-off review artefact,
reviews/21_zoning/golden_scores_head_ba4a780.json, checked by
tests/zoning_golden_check.py, which is NOT part of the suite.

Old code never asks for zoning, so the fake answers zoning queries only if
they are issued.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest import mock

import prepare

_V = "zoning_import v1"


def _desc(j: str, src: str, code: str, split: str = "-", d: str = "-") -> str:
    return " | ".join((_V, j, src, code, split, d))


# parcel_id -> (submarket, acreage, assessed, zoning, zoning_description, lng, lat)
PARCELS: dict[str, tuple] = {
    "fulton-Z01": ("atlanta__sf", 30.0, 1_500_000, "M-1",
                   _desc("south_fulton", "south_fulton_zoning", "M-1", d="Light Industrial"), -84.55, 33.55),
    "fulton-Z02": ("atlanta__sf", 45.0, 900_000, "AG-1",
                   _desc("south_fulton", "south_fulton_zoning", "AG-1"), -84.56, 33.56),
    "fulton-Z03": ("atlanta__sf", 12.0, 2_000_000, "R-3",
                   _desc("south_fulton", "south_fulton_zoning", "R-3"), -84.57, 33.54),
    "fulton-Z04": ("atlanta__wa", 20.0, 3_000_000, "I-1-C",
                   _desc("atlanta", "atlanta_zoning", "I-1-C"), -84.50, 33.75),
    "fulton-Z05": ("atlanta__fb", 15.0, 700_000, "R-2 OVERLAY",
                   _desc("fairburn", "fairburn_zoning", "R-2 OVERLAY"), -84.60, 33.55),
    "fulton-Z06": ("atlanta__fb", 25.0, None, None, None, -84.61, 33.56),
    "fulton-Z07": (None, 11.0, 1_000_000, "I1",
                   _desc("east_point", "east_point_zoning", "I1"), -84.44, 33.68),
    "fulton-Z08": (None, 18.0, 800_000, None,
                   " | ".join((_V, "college_park", "-", "-", "-", "no machine-readable zoning")),
                   -84.45, 33.65),
    "fulton-Z09": ("atlanta__wa", 40.0, 4_000_000, "M-1A",
                   _desc("fid", "fulton_layer34_zoning", "M-1A"), -84.52, 33.77),
    "fulton-Z10": ("atlanta__uc", 33.0, 1_200_000, "M-1",
                   _desc("union_city", "union_city_zoning", "M-1"), -84.54, 33.58),
    "fulton-Z11": ("atlanta__fb", 22.0, 600_000, "M-2", None, -84.62, 33.55),
    "fulton-Z12": ("atlanta__fb", 48.0, 2_500_000, "M-1",
                   _desc("fairburn", "fairburn_zoning", "M-1", split="split"), -84.59, 33.53),
}

MC_ROWS = {
    "atlanta__sf": (4.2, 1_850_000, 400_000, 0, 7.85, "2020-01-01", "costar"),
    "atlanta__wa": (9.5, -200_000, 2_000_000, 0, 6.10, "2020-01-01", "costar"),
    "atlanta__fb": (2.1, 3_000_000, 100_000, 0, 8.20, "2020-01-01", "costar"),
    "atlanta__uc": (6.0, 500_000, 900_000, 0, 7.00, "2020-01-01", "costar"),
}
MEDIAN_ROWS = {
    "atlanta__sf": (5, 120_000.0),
    "atlanta__wa": (2, 300_000.0),
    "atlanta__fb": (7, 90_000.0),
    "atlanta__uc": (0, None),
}
BLOCKS = {"fulton-Z03": "deal killer: pipeline easement"}


def _parcel_row(pid: str) -> tuple:
    sub, ac, assessed, _z, _d, lng, lat = PARCELS[pid]
    return (pid, "atlanta", sub, "GA", ac, None, None, assessed, lng, lat)


def responder(research: Any):
    """(sql, params) -> list of rows, for both old and new research.py."""
    zsql = {getattr(research, n, None) for n in (
        "_SQL_STORED_ZONING", "_SQL_STORED_ZONING_BATCH", "_SQL_STORED_ZONING_FOR_MARKET")}
    zsql.discard(None)

    def answer(sql: str, params: tuple) -> list[tuple]:
        if sql == research._SQL_COUNT_LOG_FOR_SCORING_CYCLE:
            return [(0,)]
        if sql in (research._SQL_LIST_PARCELS_FOR_SCORING_RUN_SCOPED,
                   research._SQL_LIST_PARCELS_FOR_SCORING):
            return [(pid,) for pid in sorted(PARCELS)]
        if sql == research._SQL_FETCH_PARCEL:
            return [_parcel_row(params[0])] if params[0] in PARCELS else []
        if sql == research._SQL_S2_GEOMETRY:
            ac = PARCELS[params[0]][1]
            return [(ac * 4046.86, ac * 4046.86 * 1.3, 1.8)]
        if sql == research._SQL_LATEST_MARKET_CONTEXT:
            return [MC_ROWS[params[0]]] if params[0] in MC_ROWS else []
        if sql == research._SQL_SUBMARKET_LAND_MEDIAN:
            return [MEDIAN_ROWS[params[0]]] if params[0] in MEDIAN_ROWS else []
        if sql == research._SQL_FLAGGED_ACTIONABILITY_BLOCK:
            return [(BLOCKS[params[0]],)] if params[0] in BLOCKS else []
        if sql == research._SQL_DISTINCT_SUBMARKETS_FOR_PARCELS:
            return sorted({(PARCELS[p][0],) for p in params[0] if PARCELS[p][0]})
        if sql == research._SQL_LATEST_MARKET_CONTEXT_BATCH:
            return [(s,) + MC_ROWS[s] for s in params[0] if s in MC_ROWS]
        if sql == research._SQL_SUBMARKET_LAND_MEDIAN_BATCH:
            return [(s,) + MEDIAN_ROWS[s] for s in params[0] if s in MEDIAN_ROWS]
        if sql == research._SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH:
            return [(p, BLOCKS[p]) for p in params[0] if p in BLOCKS]
        if sql in zsql:
            ids = params[0] if isinstance(params[0], list) else [params[0]]
            return [((p,) if isinstance(params[0], list) else ()) + PARCELS[p][3:5]
                    for p in ids if p in PARCELS]
        if sql == research._SQL_INSERT_PARCEL_SCORE:
            return [(1,)]
        return []
    return answer


class _Cursor:
    def __init__(self, conn: "AnswerConn") -> None:
        self.conn = conn
        self._rows: list[tuple] = []

    def execute(self, sql: str, params: Any = ()) -> None:
        params = tuple(params or ())
        self.conn.executed.append((sql, params))
        self._rows = list(self.conn.answer(sql, params))

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *a: Any) -> None:
        return None


class AnswerConn:
    def __init__(self, answer) -> None:
        self.answer = answer
        self.executed: list[tuple[str, tuple]] = []
        self.commits = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str, sort_keys=True))


def observe(conn: AnswerConn, research: Any) -> dict[str, Any]:
    ex = conn.executed
    return {
        "executed": _jsonable([[s, list(p)] for s, p in ex]),
        "parcel_score_inserts": _jsonable(
            [list(p) for s, p in ex if s == research._SQL_INSERT_PARCEL_SCORE]),
        "research_log_notes": [p[7] for s, p in ex if s == research._SQL_INSERT_RESEARCH_LOG_SCORING],
    }


def run_case(research: Any, conn_factory=None) -> dict[str, Any]:
    """Score every fixture parcel both ways; return the observable record."""
    params = prepare.get_parameters()
    out: dict[str, Any] = {"per_parcel": {}, "cycle": {}}
    research._OZ_TRACTS_CACHE = None
    for pid in sorted(PARCELS):
        conn = (conn_factory or AnswerConn)(responder(research))
        result = research.score_parcel(
            pid, conn=conn, cycle_id="score-atlanta-golden", params=params,
            run_tag="golden", experiment_id="exp-golden")
        out["per_parcel"][pid] = {"result": _jsonable(result), **observe(conn, research)}

    conn = (conn_factory or AnswerConn)(responder(research))

    @contextmanager
    def _get_connection():
        yield conn

    with mock.patch.object(prepare, "get_connection", _get_connection), \
            mock.patch.object(research, "_make_scoring_cycle_id",
                              return_value="score-atlanta-golden-cycle"):
        summary = research.run_scoring_cycle(
            "atlanta", run_tag="golden", experiment_id="exp-golden")
    out["cycle"] = {"summary": _jsonable(summary), **observe(conn, research)}
    return out
