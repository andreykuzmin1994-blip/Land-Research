"""Offline unit tests for prepare.py metric functions.

The metric — `calculate_actionable_pipeline_count` and
`calculate_confidence_weighted_pipeline` — is the AutoResearch ground truth
(AUTORESEARCH_MECHANICS.md §The Metric). Until this file existed it was only
exercised end-to-end through the live-Postgres CI smoke test, which means a
silent semantic regression in the SQL or the threshold plumbing could ship
green.

These tests use a minimal FakeConnection that records every SQL statement and
parameter tuple, returns scripted fetchone results, and verifies that:

  1. `calculate_actionable_pipeline_count` issues a parameterised query whose
     WHERE clause carries the four-gate predicates and threshold from
     parameters.json (no SQL injection surface).
  2. The composite_threshold passed as a query parameter equals the value in
     parameters.json — the agent must never inline it from elsewhere.
  3. Empty result, zero result, and positive result are all handled.
  4. `calculate_confidence_weighted_pipeline` uses the SAME WHERE clause and
     returns a float (not the int from COUNT).
  5. NULL fetchone (driver returned no row) maps to 0 / 0.0, not a crash.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import prepare  # noqa: E402


class FakeCursor:
    def __init__(self, fetchone_returns: list[Any]) -> None:
        self.executes: list[tuple[str, tuple]] = []
        self._returns = list(fetchone_returns)

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executes.append((sql, tuple(params or ())))

    def fetchone(self) -> Any:
        if self._returns:
            return self._returns.pop(0)
        return None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class FakeConnection:
    def __init__(self, fetchone_returns: list[Any] | None = None) -> None:
        self._returns = list(fetchone_returns or [])
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        c = FakeCursor(self._returns)
        self.cursors.append(c)
        return c


def _last_sql(conn: FakeConnection) -> tuple[str, tuple]:
    assert conn.cursors, "no cursor was opened"
    assert conn.cursors[-1].executes, "no SQL was executed"
    return conn.cursors[-1].executes[-1]


class TestActionablePipelineCount(unittest.TestCase):
    def test_returns_int_from_fetchone(self) -> None:
        conn = FakeConnection(fetchone_returns=[(7,)])
        result = prepare.calculate_actionable_pipeline_count(conn)
        self.assertEqual(result, 7)
        self.assertIsInstance(result, int)

    def test_zero_when_no_qualifying_rows(self) -> None:
        conn = FakeConnection(fetchone_returns=[(0,)])
        self.assertEqual(prepare.calculate_actionable_pipeline_count(conn), 0)

    def test_zero_when_fetchone_is_none(self) -> None:
        # A driver could in principle return None for an empty result set
        # even on COUNT(*); the metric must not crash on it.
        conn = FakeConnection(fetchone_returns=[None])
        self.assertEqual(prepare.calculate_actionable_pipeline_count(conn), 0)

    def test_zero_when_fetchone_returns_null_count(self) -> None:
        conn = FakeConnection(fetchone_returns=[(None,)])
        self.assertEqual(prepare.calculate_actionable_pipeline_count(conn), 0)

    def test_threshold_is_passed_as_bound_parameter(self) -> None:
        # The threshold must come from the frozen parameters layer and be
        # passed as a bound parameter — never string-formatted into the SQL.
        conn = FakeConnection(fetchone_returns=[(3,)])
        prepare.calculate_actionable_pipeline_count(conn)
        sql, params = _last_sql(conn)
        self.assertEqual(params, (prepare.get_parameters()["composite_threshold"],))
        # Threshold value itself should NOT appear inline in the SQL string.
        self.assertNotIn(str(params[0]), sql)
        # %s placeholder is the canonical psycopg parameter marker.
        self.assertIn("%s", sql)

    def test_where_clause_carries_actionability_and_threshold_predicates(self) -> None:
        conn = FakeConnection(fetchone_returns=[(0,)])
        prepare.calculate_actionable_pipeline_count(conn)
        sql, _ = _last_sql(conn)
        self.assertIn("COUNT(*)", sql)
        self.assertIn("parcel_scores", sql)
        self.assertIn("actionability = 'PASS'", sql)
        self.assertIn("composite_score >= %s", sql)
        # Latest-score selector — the metric must not double-count a parcel
        # that was rescored.
        self.assertIn("MAX(scored_at)", sql)


class TestConfidenceWeightedPipeline(unittest.TestCase):
    def test_returns_float_from_fetchone(self) -> None:
        conn = FakeConnection(fetchone_returns=[(11.25,)])
        result = prepare.calculate_confidence_weighted_pipeline(conn)
        self.assertEqual(result, 11.25)
        self.assertIsInstance(result, float)

    def test_zero_float_on_empty_pipeline(self) -> None:
        # COALESCE(SUM(...), 0) means an empty pipeline returns 0, not NULL.
        conn = FakeConnection(fetchone_returns=[(0,)])
        self.assertEqual(prepare.calculate_confidence_weighted_pipeline(conn), 0.0)

    def test_zero_on_none_fetchone(self) -> None:
        conn = FakeConnection(fetchone_returns=[None])
        self.assertEqual(prepare.calculate_confidence_weighted_pipeline(conn), 0.0)

    def test_uses_same_where_clause_as_count(self) -> None:
        # The two metric functions share _LATEST_SCORE_WHERE — keep them
        # in lock-step so the secondary metric never disagrees about which
        # parcels are in the pipeline.
        conn_a = FakeConnection(fetchone_returns=[(0,)])
        conn_b = FakeConnection(fetchone_returns=[(0,)])
        prepare.calculate_actionable_pipeline_count(conn_a)
        prepare.calculate_confidence_weighted_pipeline(conn_b)
        where_a = _last_sql(conn_a)[0].split("WHERE", 1)[1]
        where_b = _last_sql(conn_b)[0].split("WHERE", 1)[1]
        self.assertEqual(where_a.strip(), where_b.strip())

    def test_threshold_is_bound_parameter(self) -> None:
        conn = FakeConnection(fetchone_returns=[(0,)])
        prepare.calculate_confidence_weighted_pipeline(conn)
        sql, params = _last_sql(conn)
        self.assertEqual(params, (prepare.get_parameters()["composite_threshold"],))
        self.assertIn("SUM(ps.confidence_score)", sql)


class TestParametersImmutabilityContract(unittest.TestCase):
    """The metric reads the threshold from a frozen parameters layer. Verify
    that prepare.get_parameters returns a value that matches what is on
    disk — guards against an accidental refactor that bypasses the frozen
    layer."""

    def test_threshold_matches_parameters_json(self) -> None:
        import json
        on_disk = json.loads((REPO_ROOT / "parameters.json").read_text(encoding="utf-8"))
        self.assertEqual(
            prepare.get_parameters()["composite_threshold"],
            on_disk["composite_threshold"],
        )

    def test_parameters_json_sha_matches_loaded(self) -> None:
        # Sanity: the SHA captured at module import equals the SHA of the
        # file we'd compute now. If this drifts, someone modified
        # parameters.json after import — a metric-corruption scenario.
        import hashlib
        on_disk = (REPO_ROOT / "parameters.json").read_bytes()
        self.assertEqual(
            prepare._PARAMETERS_SHA256,
            hashlib.sha256(on_disk).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
