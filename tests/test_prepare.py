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
from unittest import mock

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


class _UnscopedMetricTestCase(unittest.TestCase):
    """Base for the legacy (unscoped) metric tests.

    prepare-mutation (2026-07-07): with no explicit run_tag the metric
    derives the scope from the current git branch. These tests assert the
    UNSCOPED query shape, so pin current_run_tag() to None — otherwise the
    same tests would flip behavior when run from an autoresearch/<tag>
    checkout.
    """

    def setUp(self) -> None:
        self._tag_patch = mock.patch.object(
            prepare, "current_run_tag", return_value=None
        )
        self._tag_patch.start()

    def tearDown(self) -> None:
        self._tag_patch.stop()


class TestActionablePipelineCount(_UnscopedMetricTestCase):
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
        # Phase 13 mutation: latest-score selector is now a DISTINCT ON
        # (parcel_id) CTE (replacing the old MAX(scored_at) correlated
        # subquery), so the metric selects EXACTLY ONE deterministic row per
        # parcel and cannot double-count a rescored parcel. Assert the new
        # mechanism with equal strictness — the parcel_id-led ORDER BY with the
        # scored_at DESC, score_id DESC tie-break is mandatory (R-1322, R-1324).
        self.assertIn("DISTINCT ON (parcel_id)", sql)
        self.assertIn("ORDER BY parcel_id, scored_at DESC, score_id DESC", sql)
        # The old correlated-subquery form must be gone.
        self.assertNotIn("MAX(scored_at)", sql)


class TestConfidenceWeightedPipeline(_UnscopedMetricTestCase):
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

    def test_uses_same_latest_score_cte_and_filter(self) -> None:
        # Phase 13 mutation: the two metric functions now compose the SAME
        # shared _LATEST_SCORE_CTE (latest row per parcel) and the SAME
        # _LATEST_SCORE_FILTER (PASS + threshold), so they can never disagree
        # about which parcels are in the pipeline. Assert BOTH emitted SQLs
        # embed both shared constants verbatim — this fails if either function's
        # parcel-selection CTE or its PASS/threshold filter drifts from the
        # other's (R-1322). Replaces the old split-on-first-WHERE comparison,
        # which is structurally invalid once a CTE owns the leading clause.
        conn_a = FakeConnection(fetchone_returns=[(0,)])
        conn_b = FakeConnection(fetchone_returns=[(0,)])
        prepare.calculate_actionable_pipeline_count(conn_a)
        prepare.calculate_confidence_weighted_pipeline(conn_b)
        sql_a = _last_sql(conn_a)[0]
        sql_b = _last_sql(conn_b)[0]
        for sql in (sql_a, sql_b):
            self.assertIn(prepare._LATEST_SCORE_CTE, sql)
            self.assertIn(prepare._LATEST_SCORE_FILTER, sql)
        # Both must select latest-per-parcel rows from the shared `latest` CTE.
        self.assertIn("FROM latest", sql_a)
        self.assertIn("FROM latest", sql_b)

    def test_threshold_is_bound_parameter(self) -> None:
        conn = FakeConnection(fetchone_returns=[(0,)])
        prepare.calculate_confidence_weighted_pipeline(conn)
        sql, params = _last_sql(conn)
        # Single bound threshold param preserved across the Phase 13 mutation —
        # the CTE adds NO new placeholder (the filter stays the one %s).
        self.assertEqual(params, (prepare.get_parameters()["composite_threshold"],))
        # Projection is SUM(confidence_score) over the shared `latest` CTE rows
        # (the `ps.` alias is gone now the SUM reads from the CTE, not the base
        # table).
        self.assertIn("SUM(confidence_score)", sql)


class TestPhase13MetricMutationShape(unittest.TestCase):
    """Phase 13 prepare-mutation (R-1321, R-1324, R-1327): the latest-score
    selection is a DISTINCT ON (parcel_id) CTE with a deterministic,
    parcel_id-led tie-break, shared verbatim by both metric functions."""

    def test_latest_score_cte_uses_distinct_on_with_exact_order_by(self) -> None:
        # The fake cursor cannot execute SQL, so the offline guard is an exact
        # substring assertion. parcel_id MUST lead the ORDER BY (a DISTINCT ON
        # requirement; without it Postgres errors), then scored_at DESC
        # (latest), then score_id DESC (deterministic tie-break). Dropping any
        # term is a correctness bug (R-1324).
        cte = prepare._LATEST_SCORE_CTE
        self.assertIn("DISTINCT ON (parcel_id)", cte)
        self.assertIn("ORDER BY parcel_id, scored_at DESC, score_id DESC", cte)
        self.assertNotIn("MAX(scored_at)", cte)

    def test_latest_score_cte_projects_all_needed_columns(self) -> None:
        # The CTE must project every column either metric function needs:
        # composite_score + actionability for the filter, confidence_score for
        # the SUM projection. A missing confidence_score would make the
        # confidence-weighted metric reference a column not in `latest` (R-1327).
        cte = prepare._LATEST_SCORE_CTE
        for col in ("parcel_id", "composite_score", "confidence_score", "actionability"):
            self.assertIn(col, cte)

    def test_filter_carries_pass_and_single_threshold_placeholder(self) -> None:
        flt = prepare._LATEST_SCORE_FILTER
        self.assertIn("actionability = 'PASS'", flt)
        self.assertIn("composite_score >= %s", flt)
        # Exactly ONE bound placeholder in the filter (the threshold) so the
        # single-bound-param invariant holds (R-1322).
        self.assertEqual(flt.count("%s"), 1)

    def test_no_runtime_format_braces_in_metric_constants(self) -> None:
        # The shared constants are composed with f-strings at call time, but the
        # constants themselves must carry no `{` runtime-format markers — only
        # %s for psycopg (mirrors the research.py SQL-constant static checks).
        for const in (prepare._LATEST_SCORE_CTE, prepare._LATEST_SCORE_FILTER):
            self.assertNotIn("{", const)


class TestPhase13IndexDDL(unittest.TestCase):
    """Phase 13 (R-1325): the composite latest-score index is a PLAIN
    CREATE INDEX (apply_schema runs all DDL in one transaction, so
    CONCURRENTLY would be illegal)."""

    def test_index_present_in_all_ddl(self) -> None:
        joined = "\n".join(prepare._ALL_DDL)
        self.assertIn(
            "idx_scores_parcel_scored_at ON parcel_scores"
            "(parcel_id, scored_at DESC, score_id DESC)",
            joined,
        )

    def test_index_is_not_concurrent(self) -> None:
        # CREATE INDEX CONCURRENTLY cannot run inside a transaction block;
        # apply_schema wraps all of _ALL_DDL in one transaction. Assert NO DDL
        # statement uses CONCURRENTLY (R-1325).
        for stmt in prepare._ALL_DDL:
            self.assertNotIn("CONCURRENTLY", stmt.upper())

    def test_index_is_idempotent(self) -> None:
        # IF NOT EXISTS keeps re-running apply_schema safe (R-1335).
        idx = next(s for s in prepare._ALL_DDL if "idx_scores_parcel_scored_at" in s)
        self.assertIn("IF NOT EXISTS", idx)


class TestRunScopedMetric(unittest.TestCase):
    """prepare-mutation (2026-07-07): the evaluation universe is scoped to
    the active run's parcel_scores rows, restoring the canonical
    AUTORESEARCH_MECHANICS.md definition ('all scored parcels in the active
    experiment branch')."""

    def test_explicit_run_tag_scopes_the_cte(self) -> None:
        conn = FakeConnection(fetchone_returns=[(2,)])
        prepare.calculate_actionable_pipeline_count(conn, run_tag="atl-test")
        sql, params = _last_sql(conn)
        self.assertIn("WHERE run_tag = %s", sql)
        self.assertIn("DISTINCT ON (parcel_id)", sql)
        # Param order is (run_tag, threshold): the run scope binds inside the
        # CTE, the threshold binds in the outer filter.
        self.assertEqual(
            params,
            ("atl-test", prepare.get_parameters()["composite_threshold"]),
        )

    def test_run_tag_derived_from_branch_when_omitted(self) -> None:
        conn = FakeConnection(fetchone_returns=[(0,)])
        with mock.patch.object(
            prepare, "current_run_tag", return_value="atl-derived"
        ):
            prepare.calculate_actionable_pipeline_count(conn)
        sql, params = _last_sql(conn)
        self.assertIn("WHERE run_tag = %s", sql)
        self.assertEqual(params[0], "atl-derived")

    def test_unscoped_fallback_off_branch(self) -> None:
        conn = FakeConnection(fetchone_returns=[(0,)])
        with mock.patch.object(prepare, "current_run_tag", return_value=None):
            prepare.calculate_actionable_pipeline_count(conn)
        sql, params = _last_sql(conn)
        self.assertNotIn("run_tag", sql)
        self.assertEqual(len(params), 1)

    def test_confidence_metric_shares_run_scope(self) -> None:
        conn = FakeConnection(fetchone_returns=[(4.5,)])
        prepare.calculate_confidence_weighted_pipeline(conn, run_tag="atl-test")
        sql, params = _last_sql(conn)
        self.assertIn("WHERE run_tag = %s", sql)
        self.assertIn("SUM(confidence_score)", sql)
        self.assertEqual(params[0], "atl-test")
        # Both metric functions must embed the SAME run-scoped CTE so they
        # can never disagree about the pipeline membership.
        self.assertIn(prepare._LATEST_SCORE_CTE_RUN_SCOPED, sql)

    def test_run_scoped_cte_keeps_deterministic_tie_break(self) -> None:
        cte = prepare._LATEST_SCORE_CTE_RUN_SCOPED
        self.assertIn("DISTINCT ON (parcel_id)", cte)
        self.assertIn("ORDER BY parcel_id, scored_at DESC, score_id DESC", cte)
        self.assertNotIn("{", cte)


class TestCurrentRunTag(unittest.TestCase):
    """current_run_tag() derives the run scope from the git branch."""

    def _proc(self, returncode: int, stdout: str) -> Any:
        class _P:
            pass
        p = _P()
        p.returncode = returncode
        p.stdout = stdout
        return p

    def test_autoresearch_branch_yields_tag(self) -> None:
        with mock.patch.object(
            prepare.subprocess, "run",
            return_value=self._proc(0, "autoresearch/atl-2026-07-07\n"),
        ):
            self.assertEqual(prepare.current_run_tag(), "atl-2026-07-07")

    def test_main_branch_yields_none(self) -> None:
        with mock.patch.object(
            prepare.subprocess, "run", return_value=self._proc(0, "main\n"),
        ):
            self.assertIsNone(prepare.current_run_tag())

    def test_git_failure_yields_none(self) -> None:
        with mock.patch.object(
            prepare.subprocess, "run", return_value=self._proc(128, ""),
        ):
            self.assertIsNone(prepare.current_run_tag())


class TestRunColumnsDDL(unittest.TestCase):
    """prepare-mutation (2026-07-07): run/experiment attribution columns."""

    def test_create_table_carries_run_columns(self) -> None:
        ddl = prepare._DDL_PARCEL_SCORES.lower()
        self.assertIn("run_tag text", ddl)
        self.assertIn("experiment_id text", ddl)

    def test_alter_statements_converge_existing_databases(self) -> None:
        joined = "\n".join(prepare._ALL_DDL)
        self.assertIn(
            "ALTER TABLE parcel_scores ADD COLUMN IF NOT EXISTS run_tag TEXT",
            joined,
        )
        self.assertIn(
            "ALTER TABLE parcel_scores ADD COLUMN IF NOT EXISTS experiment_id TEXT",
            joined,
        )

    def test_run_scoped_index_present_and_idempotent(self) -> None:
        idx = next(
            s for s in prepare._ALL_DDL if "idx_scores_run_parcel_scored_at" in s
        )
        self.assertIn("IF NOT EXISTS", idx)
        self.assertIn(
            "(run_tag, parcel_id, scored_at DESC, score_id DESC)", idx,
        )


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
