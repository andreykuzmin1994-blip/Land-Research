"""The H4/H6 metric-side audit in runner.evaluate (reviews/26_flood_wetlands,
review finding 1). Offline (SR-6): an answer-by-SQL fake connection.

Once a human has enabled the hazard table, runner.evaluate re-assesses every
parcel the metric would count through the protected hazards module; a
counted parcel with an H4/H6 blocker (a research.py edit that bypassed gate
4), an unreadable read, or a parcel_hazards change during the experiment
makes the experiment a crash, so the loop discards and purges it. While the
gate is off nothing runs."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  (suite guards)
import costar_ingest
import hazards
import prepare
import reporting
import research
import runner
from hazards_helpers import db_row, enabled, row

AE = dict(sfha_sf=5000, nfhl_zone_sf={"AE||T": 5000, "X|AREA OF MINIMAL FLOOD HAZARD|F": 95000},
          zones=["AE", "X"])


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self._one = None
        self._all: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.conn.executes.append((sql, params))
        c = self.conn
        if sql == runner._SQL_HAZARD_FINGERPRINT:
            if c.fingerprint_error:
                raise RuntimeError("relation parcel_hazards does not exist")
            fp = c.fingerprints.pop(0) if len(c.fingerprints) > 1 else c.fingerprints[0]
            self._one, self._all = fp, [fp]
        elif sql == hazards._SQL_HAZARDS_FOR_PARCELS:
            if c.read_error:
                raise RuntimeError("boom")
            ids = params[0]
            self._all = [db_row(row(parcel_id=p, **c.hazard.get(p, {}))) for p in sorted(ids)]
        elif "SELECT parcel_id FROM latest" in sql:
            self._all = [(p,) for p in c.counted]
        elif "IS DISTINCT FROM" in sql:
            self._one = (0,)
        else:
            raise AssertionError(f"unexpected SQL: {sql[:80]}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._all)


class FakeConn:
    def __init__(self, counted=(), hazard=None, fingerprints=((3, "a"),)):
        self.counted = list(counted)
        self.hazard = dict(hazard or {})
        self.fingerprints = list(fingerprints)
        self.fingerprint_error = False
        self.read_error = False
        self.executes: list = []
        self.connections = 0

    def cursor(self):
        return _Cur(self)

    def rollback(self):
        pass


def run_evaluate(conn: FakeConn, scoring=None):
    @contextmanager
    def get_connection():
        conn.connections += 1
        yield conn

    with mock.patch.object(costar_ingest, "run_ingestion_cycle", return_value={}), \
            mock.patch.object(research, "run_discovery_cycle", return_value={}), \
            mock.patch.object(research, "run_scoring_cycle",
                              side_effect=scoring or (lambda *a, **k: {})), \
            mock.patch.object(reporting, "generate_strategy_memo",
                              return_value=Path("/tmp/memo.md")), \
            mock.patch.object(prepare, "verify_parameters_unchanged"), \
            mock.patch.object(prepare, "calculate_actionable_pipeline_count",
                              lambda c, run_tag=None: len(conn.counted)), \
            mock.patch.object(prepare, "calculate_confidence_weighted_pipeline",
                              lambda c, run_tag=None: 0.5), \
            mock.patch.object(prepare, "get_connection", get_connection):
        return runner.evaluate("atlanta", run_tag="t1", experiment_id="exp-1")


class DisabledTests(unittest.TestCase):
    def test_gate_off_runs_no_hazard_sql(self):
        conn = FakeConn(counted=["fulton-A"], hazard={"fulton-A": AE})
        result = run_evaluate(conn)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["metric"], 1)
        self.assertNotIn("hazard_audit", result["sub_summaries"])
        for sql, _ in conn.executes:
            self.assertNotIn("parcel_hazards", sql)
            self.assertNotIn("SELECT parcel_id FROM latest", sql)
        self.assertEqual(conn.connections, 1)   # only the metric block's connection


class EnabledTests(unittest.TestCase):
    def test_clean_counted_set_passes_and_reports_counts(self):
        conn = FakeConn(counted=["fulton-A", "fulton-B"],
                        hazard={"fulton-B": dict(nfhl_md5=None, nfhl_fetched_at=None)})
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "ok", result["error"])
        self.assertEqual(result["metric"], 2)
        self.assertEqual(result["sub_summaries"]["hazard_audit"],
                         {"counted": 2, "blocked": 0, "stale": 0, "absent": 1})

    def test_audit_uses_the_metric_universe(self):
        conn = FakeConn(counted=["fulton-A"])
        with enabled():
            run_evaluate(conn)
        sql, params = prepare._metric_sql_and_params("parcel_id", "t1")
        self.assertIn((sql, params), conn.executes)

    def test_counted_parcel_with_h4_blocker_crashes_the_experiment(self):
        # A research.py edit that drops the hazard argument of gate 4 leaves
        # an AE parcel PASS; the runner catches it outside the sandbox.
        conn = FakeConn(counted=["fulton-A", "fulton-B"], hazard={"fulton-B": AE})
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "crash")
        self.assertEqual(result["metric"], 0)
        self.assertIn("HazardAuditFailed", result["error"])
        self.assertIn("1 counted parcel(s) carry an H4/H6 blocker", result["error"])
        self.assertNotIn("fulton-B", result["error"])            # counts only

    def test_counted_parcel_with_h6_blocker_crashes(self):
        conn = FakeConn(counted=["fulton-A"], hazard={"fulton-A": dict(wetland_sf=25000)})
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "crash")

    def test_review_and_gap_parcels_do_not_crash(self):
        conn = FakeConn(counted=["fulton-A", "fulton-B", "fulton-C"], hazard={
            "fulton-A": dict(wetland_sf=10000),
            "fulton-B": dict(AE, geom_md5="changed"),
            "fulton-C": dict(AE, lomr_status="intersects", nfhl_zone_lomr_sf={"AE||T": 5000}),
        })
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "ok", result["error"])
        self.assertEqual(result["sub_summaries"]["hazard_audit"]["stale"], 1)

    def test_table_changed_during_the_experiment_crashes(self):
        conn = FakeConn(counted=["fulton-A"], fingerprints=[(3, "a"), (2, "b")])
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "crash")
        self.assertIn("parcel_hazards changed during the experiment", result["error"])

    def test_unreadable_table_fails_closed(self):
        conn = FakeConn(counted=["fulton-A"])
        conn.fingerprint_error = True
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "crash")
        self.assertIn("unreadable", result["error"])

    def test_unreadable_hazard_read_fails_closed(self):
        conn = FakeConn(counted=["fulton-A"])
        conn.read_error = True
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "crash")
        self.assertIn("1 counted parcel(s) unreadable", result["error"])

    def test_runtime_prepare_patch_cannot_disable_the_audit(self):
        # Review finding 2 meets finding 1: the sandbox assigning the gate
        # constant away during scoring changes nothing.
        conn = FakeConn(counted=["fulton-A"], hazard={"fulton-A": AE})

        def scoring(*a, **k):
            setattr(prepare, hazards.GATE_CONSTANT, "x")
            return {}
        with enabled():
            result = run_evaluate(conn, scoring=scoring)
        self.assertEqual(result["status"], "crash")

    def test_empty_counted_set_passes(self):
        conn = FakeConn(counted=[])
        with enabled():
            result = run_evaluate(conn)
        self.assertEqual(result["status"], "ok", result["error"])
        self.assertEqual(result["sub_summaries"]["hazard_audit"]["counted"], 0)


if __name__ == "__main__":
    unittest.main()
