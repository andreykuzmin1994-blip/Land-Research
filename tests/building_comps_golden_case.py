"""Shared fixture for the building-comps byte-identity check (reviews/28_building_comps, BC-16).

``run_case(reporting)`` renders, through the public entry points
(``generate_strategy_memo`` and ``generate_snapshot``), one fixed memo and
one fixed snapshot against a fake connection that answers every read by its
SQL text and holds NO building or lease comps of this tool. It returns the
two markdown texts and the statements each path executed. The base commit's
reporting.py (3898047, before building comps existed) is the reference:
reviews/28_building_comps/golden_head_3898047.json, checked by
tests/building_comps_golden_check.py (NOT part of the suite). The new code
must render the same two texts; its only extra statements are the two
building-comps reads at the end of each path.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import prepare
from pipeline_common import _SQL_FETCH_SUBMARKET_NAME, _SQL_LATEST_MARKET_CONTEXT

TODAY = "2026-11-06"
PARCEL = ("99Z-0001-LL-001-1", "fulton", "GA", "atlanta", "atlanta__alpha_park",
          "1 Test St, Testville, GA", "TEST OWNER TRUST", "PO Box 1, Testville, FL",
          "trust", 25.0, 1089000.0, "M-1", "Light industrial", "400", "Vacant industrial",
          250000, 0, 250000, 600000, 2025, 3000.0, "current", "2015-06-12", 900000, None,
          "fulton_arcgis", "2026-04-30", -84.5, 33.7)
SCORE = (78.0, 0.55, "PASS", json.dumps({}), json.dumps({"S2_parcel_geometry": 7}),
         json.dumps({"bts": "MODERATE", "spec": "WEAK", "land_bank": "STRONG",
                     "ground_lease": "WEAK", "flip": "WEAK"}),
         "land_bank", None, "golden", "2026-11-01T10:00:00Z")
MEMO_ROWS = [
    ("99Z-0001-LL-001-1", "1 Test St", "fulton", "atlanta__alpha_park", 25.0, "TEST OWNER",
     "trust", 78.0, 0.55, "PASS", json.dumps({}), json.dumps({"S2_parcel_geometry": 7}),
     json.dumps({"land_bank": "STRONG"}), "land_bank", "2026-11-01T10:00:00Z"),
    ("99Z-0001-LL-002-1", "2 Test St", "fulton", "atlanta__beta_yard", 15.0, "OTHER OWNER",
     "llc", 72.0, 0.5, "FAIL:entitlement", json.dumps({"entitlement": "rezoning"}),
     json.dumps({}), json.dumps({}), None, "2026-11-01T10:00:00Z"),
]


class _Cur:
    def __init__(self, conn: "AnswerConn") -> None:
        self.conn = conn
        self.rows: list[tuple] = []

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.executed.append(sql)
        self.rows = self.conn.answer(sql)

    def fetchone(self) -> tuple | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple]:
        return list(self.rows)


class AnswerConn:
    def __init__(self, reporting: Any) -> None:
        self.r = reporting
        self.executed: list[str] = []

    def answer(self, sql: str) -> list[tuple]:
        r = self.r
        table = {
            r._SQL_FETCH_PARCEL_FOR_SNAPSHOT: [PARCEL],
            r._SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT: [SCORE],
            _SQL_LATEST_MARKET_CONTEXT: [(6.1, 1500000, 900000, None, 7.25, "2026-09-30",
                                          "costar")],
            r._SQL_FETCH_NEARBY_SALES_COMPS: [("9 Test St", "2026-02-01", 2000000, 100000.0,
                                               20.0, "land", "Buyer")],
            _SQL_FETCH_SUBMARKET_NAME: [("Alpha Park",)],
            r._SQL_FETCH_LATEST_SCORING_CYCLE_FOR_MEMO: [("score-golden", "2026-11-01")],
            r._SQL_FETCH_SCORED_PARCELS_FOR_MEMO: MEMO_ROWS,
        }
        return list(table.get(sql, []))

    def cursor(self) -> _Cur:
        return _Cur(self)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def transaction(self) -> Any:
        import contextlib
        return contextlib.nullcontext()


def run_case(reporting: Any) -> dict[str, Any]:
    params = dict(prepare.get_parameters())
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as d:
        conn = AnswerConn(reporting)
        path = reporting.generate_strategy_memo("atlanta", conn=conn, output_dir=d,
                                                params=params, today=TODAY)
        out["memo"] = Path(path).read_text(encoding="utf-8")
        out["memo_sql"] = conn.executed
        conn = AnswerConn(reporting)
        kw = {"today": TODAY} if "today" in reporting.generate_snapshot.__code__.co_varnames else {}
        path = reporting.generate_snapshot(PARCEL[0], conn=conn, output_dir=d, params=params, **kw)
        out["snapshot"] = Path(path).read_text(encoding="utf-8")
        out["snapshot_sql"] = conn.executed
    return out
