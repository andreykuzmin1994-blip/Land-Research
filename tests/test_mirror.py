"""Offline tests for the experiment-log durability mirror.

reviews/17_tsv_mirror/: gates G1 (never raises, incl. SystemExit),
G3 (TSV append strictly precedes the mirror call at every site),
G4 (no pipeline DELETE/UPDATE/TRUNCATE of the mirror; no reads outside
the two sanctioned constants), G7 (mirror rows are value-equal to the
TSV line written), plus R-M7 (count-based backfill dedup) and R-M8
(last-match, shape-validated marker parsing).

Hermetic per SR-6: FakeConnection only, no network, no DATABASE_URL.
The suite-wide kill switch (tests/__init__.py) is cleared inside these
tests via ``_mirror_enabled_env`` — setting the variable to the empty
string is falsy for ``os.environ.get`` truthiness, which re-enables the
mirror without unsetting the key.
"""

from __future__ import annotations

import contextlib
import os
import re
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tests  # noqa: F401 — force the package kill-switch guard to run
              # even under top-level discovery (`discover tests` imports
              # modules WITHOUT executing tests/__init__.py)

import prepare
import runner


_REPO_ROOT = Path(__file__).resolve().parent.parent

_PIPELINE_MODULES = (
    "prepare.py",
    "runner.py",
    "research.py",
    "costar_ingest.py",
    "reporting.py",
    "pipeline_common.py",
    "connector_harness.py",
    "cli.py",
)


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn

    def execute(self, sql, params=None):
        if self._conn.raise_on_execute is not None and (
            self._conn.raise_after_n_executes is None
            or len(self._conn.executed) >= self._conn.raise_after_n_executes
        ):
            raise self._conn.raise_on_execute
        self._conn.executed.append((sql, params))

    def fetchone(self):
        return self._conn.fetchone_queue.pop(0)

    def fetchall(self):
        return list(self._conn.fetchall_result)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(
        self,
        fetchone_queue=None,
        fetchall_result=None,
        raise_on_execute=None,
        raise_after_n_executes=None,
    ):
        self.executed: list[tuple[str, tuple | None]] = []
        self.fetchone_queue = list(fetchone_queue or [])
        self.fetchall_result = list(fetchall_result or [])
        self.raise_on_execute = raise_on_execute
        self.raise_after_n_executes = raise_after_n_executes
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@contextlib.contextmanager
def _fake_get_connection(conn: FakeConnection):
    yield conn


def _patched_connection(conn: FakeConnection):
    return mock.patch.object(
        prepare, "get_connection", lambda: _fake_get_connection(conn)
    )


def _mirror_enabled_env():
    """Clear the suite-wide kill switch inside a test scope."""
    return mock.patch.dict(
        os.environ, {runner._MIRROR_DISABLE_ENV_VAR: ""}, clear=False
    )


_VALID_ROW = {
    "commit": "a1b2c3d",
    "metric": 14,
    "confidence": 11.2,
    "api_calls": 847,
    "wall_clock_min": 62.4,
    "status": "baseline",
    "description": "baseline | market=atlanta | run=atl-2026-07-08",
}


class TestSuiteKillSwitch(unittest.TestCase):
    """Adversarial review F1: the suite-wide kill switch must be ACTIVE
    (truthy) at test runtime — an empty-string value would re-enable the
    mirror for every loop test on a machine with a real .env."""

    def test_kill_switch_is_truthy_at_suite_runtime(self):
        self.assertTrue(os.environ.get(runner._MIRROR_DISABLE_ENV_VAR))


class TestMirrorLogRow(unittest.TestCase):
    """G1 + R-M1 + R-M6: the mirror never raises and inserts validated values."""

    def test_kill_switch_short_circuits_before_any_dsn_probe(self):
        with mock.patch.dict(
            os.environ, {runner._MIRROR_DISABLE_ENV_VAR: "1"}, clear=False
        ):
            with mock.patch.object(prepare, "dsn_available") as probe:
                self.assertFalse(runner._mirror_log_row(_VALID_ROW))
                probe.assert_not_called()

    def test_missing_dsn_returns_false_without_connecting(self):
        with _mirror_enabled_env():
            with mock.patch.object(prepare, "dsn_available", return_value=False):
                with mock.patch.object(prepare, "get_connection") as gc:
                    self.assertFalse(runner._mirror_log_row(_VALID_ROW))
                    gc.assert_not_called()

    def test_systemexit_from_dsn_path_is_swallowed(self):
        # R-M1: prepare._get_connection_dsn sys.exit()s on a missing DSN;
        # SystemExit escapes `except Exception`. G1 demands it cannot
        # escape the mirror.
        with _mirror_enabled_env():
            with mock.patch.object(
                prepare, "dsn_available", side_effect=SystemExit(2)
            ):
                self.assertFalse(runner._mirror_log_row(_VALID_ROW))
            with mock.patch.object(prepare, "dsn_available", return_value=True):
                with mock.patch.object(
                    prepare, "get_connection", side_effect=SystemExit(2)
                ):
                    self.assertFalse(runner._mirror_log_row(_VALID_ROW))

    def test_execute_error_returns_false_and_never_commits(self):
        conn = FakeConnection(raise_on_execute=RuntimeError("boom"))
        with _mirror_enabled_env():
            with mock.patch.object(prepare, "dsn_available", return_value=True):
                with _patched_connection(conn):
                    self.assertFalse(runner._mirror_log_row(_VALID_ROW))
        self.assertEqual(conn.commits, 0)

    def test_invalid_row_returns_false(self):
        bad = dict(_VALID_ROW, status="bogus")
        with _mirror_enabled_env():
            with mock.patch.object(prepare, "dsn_available", return_value=True):
                with mock.patch.object(prepare, "get_connection") as gc:
                    self.assertFalse(runner._mirror_log_row(bad))
                    gc.assert_not_called()  # validation precedes connection

    def test_happy_path_statement_timeout_then_insert_then_commit(self):
        conn = FakeConnection()
        with _mirror_enabled_env():
            with mock.patch.object(prepare, "dsn_available", return_value=True):
                with _patched_connection(conn):
                    ok = runner._mirror_log_row(
                        _VALID_ROW,
                        run_tag="atl-2026-07-08",
                        experiment_id="exp-20260708T120000Z-abc123",
                    )
        self.assertTrue(ok)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.executed[0][0], runner._MIRROR_STATEMENT_TIMEOUT)
        sql, params = conn.executed[1]
        self.assertEqual(sql, runner._SQL_INSERT_LOG_MIRROR)
        self.assertEqual(
            params,
            (
                "live",
                "atl-2026-07-08",
                "exp-20260708T120000Z-abc123",
                "a1b2c3d",
                14,
                Decimal("11.20"),
                847,
                Decimal("62.4"),
                "baseline",
                "baseline | market=atlanta | run=atl-2026-07-08",
            ),
        )

    def test_mirrored_values_equal_tsv_line(self):
        # G7 / R-M6: what lands in the mirror is value-equal to what the
        # canonical TSV holds — including sanitization and formatting.
        messy = {
            "commit": "a1b2c3d",
            "metric": 14,
            "confidence": 11.2,
            "api_calls": 847,
            "wall_clock_min": 62.4,
            "status": "keep",
            "description": "  tab\there |\nnewline   collapsed  ",
        }
        conn = FakeConnection()
        with TemporaryDirectory() as tmp:
            tsv = Path(tmp) / "experiment_log.tsv"
            runner.append_experiment_log_row(messy, tsv)
            with _mirror_enabled_env():
                with mock.patch.object(prepare, "dsn_available", return_value=True):
                    with _patched_connection(conn):
                        self.assertTrue(runner._mirror_log_row(messy))
            tsv_row = runner.read_experiment_log(tsv)[0]
        _, params = conn.executed[1]
        self.assertEqual(tsv_row["commit"], params[3])
        self.assertEqual(tsv_row["metric"], str(params[4]))
        self.assertEqual(tsv_row["confidence"], str(params[5]))
        self.assertEqual(tsv_row["api_calls"], str(params[6]))
        self.assertEqual(tsv_row["wall_clock_min"], str(params[7]))
        self.assertEqual(tsv_row["status"], params[8])
        self.assertEqual(tsv_row["description"], params[9])


class TestHaltRowOrdering(unittest.TestCase):
    """G3 at the halt site: TSV append first; mirror only on append success."""

    def test_append_precedes_mirror_and_threads_run_tag(self):
        manager = mock.Mock()
        with mock.patch.object(
            runner, "append_experiment_log_row", manager.append
        ):
            with mock.patch.object(runner, "_mirror_log_row", manager.mirror):
                with mock.patch.object(
                    runner, "_git_head_commit", return_value="pending"
                ):
                    runner._record_halt_row("atlanta", "unit test", "atl-x")
        names = [c[0] for c in manager.mock_calls]
        self.assertEqual(names, ["append", "mirror"])
        _, kwargs = manager.mirror.call_args
        self.assertEqual(kwargs["run_tag"], "atl-x")
        self.assertIsNone(kwargs["experiment_id"])

    def test_no_mirror_when_tsv_append_fails(self):
        with mock.patch.object(
            runner, "append_experiment_log_row", side_effect=OSError("disk")
        ):
            with mock.patch.object(runner, "_mirror_log_row") as mirror:
                with mock.patch.object(
                    runner, "_git_head_commit", return_value="pending"
                ):
                    runner._record_halt_row("atlanta", "unit test", "atl-x")
                mirror.assert_not_called()


class TestMirrorCallSitePairing(unittest.TestCase):
    """R-M15(a): every runner TSV-append call site is paired with a mirror
    call that follows it — a future 5th append site that forgets the
    mirror fails this test instead of silently un-mirroring."""

    def test_appends_and_mirrors_alternate(self):
        src = (_REPO_ROOT / "runner.py").read_text(encoding="utf-8")
        # Documented exception: restore_experiment_log_from_mirror writes
        # the TSV FROM the mirror — mirroring those appends back would be
        # circular. Excise that function's body before scanning.
        start = src.index("def restore_experiment_log_from_mirror")
        end = src.index("\n# ---", start)
        src = src[:start] + src[end:]
        events: list[tuple[int, str]] = []
        for m in re.finditer(
            r"^\s*append_experiment_log_row\(", src, re.MULTILINE
        ):
            events.append((m.start(), "append"))
        for m in re.finditer(r"^\s*_mirror_log_row\(", src, re.MULTILINE):
            events.append((m.start(), "mirror"))
        ordered = [kind for _, kind in sorted(events)]
        appends = ordered.count("append")
        mirrors = ordered.count("mirror")
        self.assertEqual(appends, 4, f"expected 4 TSV append sites: {ordered}")
        self.assertEqual(appends, mirrors, f"unpaired sites: {ordered}")
        self.assertEqual(
            ordered,
            ["append", "mirror"] * appends,
            "every TSV append must be immediately paired with a mirror "
            f"call (G3 ordering): {ordered}",
        )


class TestMirrorStaticGuards(unittest.TestCase):
    """G4 / R-M5 / R-M12: append-only with no sanctioned deletion; the
    mirror is never read outside the two sanctioned runner constants."""

    def test_no_pipeline_delete_update_truncate_of_mirror(self):
        pattern = re.compile(
            r"(DELETE\s+FROM|UPDATE|TRUNCATE)\s+experiment_log_mirror",
            re.IGNORECASE,
        )
        for name in _PIPELINE_MODULES:
            src = (_REPO_ROOT / name).read_text(encoding="utf-8")
            self.assertIsNone(
                pattern.search(src),
                f"{name} mutates experiment_log_mirror (SR-13: the mirror "
                "has NO sanctioned deletion — the purge never touches it)",
            )

    def test_mirror_reads_only_via_sanctioned_constants(self):
        for name in _PIPELINE_MODULES:
            src = (_REPO_ROOT / name).read_text(encoding="utf-8")
            reads = re.findall(r"FROM\s+experiment_log_mirror", src)
            if name == "runner.py":
                self.assertEqual(
                    len(reads),
                    2,
                    "runner.py must read the mirror exactly twice: the "
                    "backfill COUNT and the restore SELECT (SR-15 fence)",
                )
            elif name == "prepare.py":
                continue  # DDL mentions the table; CREATE only, no FROM
            else:
                self.assertEqual(
                    len(reads), 0, f"{name} reads the mirror (SR-15 fence)"
                )

    def test_ddl_ships_table_and_index(self):
        self.assertTrue(
            any("experiment_log_mirror" in stmt for stmt in prepare._ALL_DDL)
        )
        self.assertTrue(
            any("idx_expmirror_run_entry" in stmt for stmt in prepare._ALL_DDL)
        )

    def test_metric_functions_do_not_reference_the_mirror(self):
        src = (_REPO_ROOT / "prepare.py").read_text(encoding="utf-8")
        metric_region = src[src.index("def calculate_actionable_pipeline_count") :]
        self.assertNotIn("experiment_log_mirror", metric_region)


class TestMarkerParsing(unittest.TestCase):
    """R-M8: last match wins; shape validation; truncation yields None."""

    def test_cases(self):
        cases = [
            (
                "baseline | market=atlanta | run=atl-2026-07-08 | "
                "exp=exp-20260708T120000Z-abc123",
                ("atl-2026-07-08", "exp-20260708T120000Z-abc123"),
            ),
            # error text containing a run= token BEFORE the real marker:
            # last match must win.
            (
                "market=atlanta | ValueError: bad run=WEIRD$TAG value | "
                "run=atl-2026-07-08",
                ("atl-2026-07-08", None),
            ),
            # truncated exp id (200-char cap amputation) fails shape check.
            ("market=atlanta | exp=exp-20260708T1200…", (None, None)),
            # legacy row with no markers at all.
            ("added cobb county connector", (None, None)),
            # uppercase tag fails the shape check.
            ("run=ATL-2026", (None, None)),
            ("", (None, None)),
        ]
        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(
                    runner._parse_markers_from_description(description),
                    expected,
                )


class TestBackfill(unittest.TestCase):
    """R-M7: count-based dedup in TSV order behind the advisory lock."""

    def _write_tsv(self, tmp: str) -> Path:
        tsv = Path(tmp) / "experiment_log.tsv"
        row_a = {
            "commit": "a1b2c3d",
            "metric": 14,
            "confidence": 11.2,
            "api_calls": 847,
            "wall_clock_min": 62.4,
            "status": "baseline",
            "description": "baseline | market=atlanta | run=atl-2026-07-08 "
            "| exp=exp-20260708T120000Z-abc123",
        }
        crash = {
            "commit": "b2c3d4e",
            "metric": 0,
            "confidence": 0.0,
            "api_calls": 0,
            "wall_clock_min": 0.0,
            "status": "crash",
            "description": "market=atlanta | outer crash",
        }
        keep = {
            "commit": "c3d4e5f",
            "metric": 19,
            "confidence": 15.8,
            "api_calls": 912,
            "wall_clock_min": 68.1,
            "status": "keep",
            "description": "market=atlanta | run=atl-2026-07-08",
        }
        runner.append_experiment_log_row(row_a, tsv)
        runner.append_experiment_log_row(crash, tsv)
        runner.append_experiment_log_row(crash, tsv)  # identical twin (R-M7)
        runner.append_experiment_log_row(keep, tsv)
        return tsv

    def test_count_based_dedup_inserts_missing_copies_in_order(self):
        # Mirror already holds: 0×A, 1×crash, 1×keep → expect inserts:
        # 1×A, 1×crash (the twin), 0×keep.
        conn = FakeConnection(fetchone_queue=[(0,), (1,), (1,)])
        with TemporaryDirectory() as tmp:
            tsv = self._write_tsv(tmp)
            with _patched_connection(conn):
                summary = runner.backfill_experiment_log_mirror(tsv)
        self.assertEqual(summary["tsv_rows"], 4)
        self.assertEqual(summary["inserted"], 2)
        self.assertEqual(summary["already_present"], 2)
        self.assertEqual(summary["mirror_only"], 0)
        self.assertEqual(summary["invalid"], 0)
        self.assertEqual(conn.executed[0][0], runner._SQL_BACKFILL_ADVISORY_LOCK)
        inserts = [
            (sql, params)
            for sql, params in conn.executed
            if sql == runner._SQL_INSERT_LOG_MIRROR
        ]
        self.assertEqual(len(inserts), 2)
        # TSV order: row A first, then the crash twin. Markers parsed for
        # A; the crash row has none.
        self.assertEqual(inserts[0][1][0], "backfill")
        self.assertEqual(inserts[0][1][1], "atl-2026-07-08")
        self.assertEqual(inserts[0][1][2], "exp-20260708T120000Z-abc123")
        self.assertEqual(inserts[0][1][3], "a1b2c3d")
        self.assertEqual(inserts[1][1][3], "b2c3d4e")
        self.assertIsNone(inserts[1][1][1])
        self.assertIsNone(inserts[1][1][2])
        self.assertEqual(conn.commits, 1)

    def test_mirror_only_divergence_counter(self):
        # SR-16 canary: mirror holds MORE copies than the TSV.
        conn = FakeConnection(fetchone_queue=[(5,), (2,), (1,)])
        with TemporaryDirectory() as tmp:
            tsv = self._write_tsv(tmp)
            with _patched_connection(conn):
                summary = runner.backfill_experiment_log_mirror(tsv)
        self.assertEqual(summary["inserted"], 0)
        self.assertEqual(summary["mirror_only"], 4)

    def test_unparseable_tsv_row_is_counted_and_skipped(self):
        conn = FakeConnection(fetchone_queue=[(0,)])
        with TemporaryDirectory() as tmp:
            tsv = Path(tmp) / "experiment_log.tsv"
            runner.append_experiment_log_row(
                {
                    "commit": "a1b2c3d",
                    "metric": 1,
                    "confidence": 1.0,
                    "api_calls": 1,
                    "wall_clock_min": 1.0,
                    "status": "keep",
                    "description": "ok row",
                },
                tsv,
            )
            with tsv.open("a", encoding="utf-8") as fh:
                fh.write("zzzzzzz\tnotanint\t1.00\t1\t1.0\tkeep\tgarbage\n")
            with _patched_connection(conn):
                summary = runner.backfill_experiment_log_mirror(tsv)
        self.assertEqual(summary["invalid"], 1)
        self.assertEqual(summary["inserted"], 1)

    def test_empty_tsv_short_circuits_without_connecting(self):
        with TemporaryDirectory() as tmp:
            tsv = Path(tmp) / "experiment_log.tsv"
            with mock.patch.object(prepare, "get_connection") as gc:
                summary = runner.backfill_experiment_log_mirror(tsv)
                gc.assert_not_called()
        self.assertEqual(summary["tsv_rows"], 0)


class TestRestore(unittest.TestCase):
    """R-M10: entry_id-ordered rebuild through the validated writer."""

    def test_refuses_to_touch_existing_nonempty_tsv(self):
        with TemporaryDirectory() as tmp:
            tsv = Path(tmp) / "experiment_log.tsv"
            runner.append_experiment_log_row(
                {
                    "commit": "a1b2c3d",
                    "metric": 1,
                    "confidence": 1.0,
                    "api_calls": 1,
                    "wall_clock_min": 1.0,
                    "status": "keep",
                    "description": "existing",
                },
                tsv,
            )
            with self.assertRaises(RuntimeError):
                runner.restore_experiment_log_from_mirror(tsv)

    def test_restores_in_order_and_skips_invalid_mirror_rows(self):
        mirror_rows = [
            ("a1b2c3d", 14, Decimal("11.20"), 847, Decimal("62.4"),
             "baseline", "baseline | market=atlanta"),
            ("zzzz", 1, Decimal("1.00"), 1, Decimal("1.0"),
             "keep", "forged commit hash fails the TSV writer"),
            ("b2c3d4e", 19, Decimal("15.80"), 912, Decimal("68.1"),
             "keep", "market=atlanta | run=atl-2026-07-08"),
        ]
        conn = FakeConnection(fetchall_result=mirror_rows)
        with TemporaryDirectory() as tmp:
            tsv = Path(tmp) / "experiment_log.tsv"
            with _patched_connection(conn):
                summary = runner.restore_experiment_log_from_mirror(tsv)
            rows = runner.read_experiment_log(tsv)
        self.assertEqual(conn.executed[0][0], runner._SQL_SELECT_MIRROR_FOR_RESTORE)
        self.assertEqual(summary["mirror_rows"], 3)
        self.assertEqual(summary["rows_written"], 2)
        self.assertEqual(summary["rows_skipped"], 1)
        self.assertEqual([r["commit"] for r in rows], ["a1b2c3d", "b2c3d4e"])
        self.assertEqual(rows[0]["confidence"], "11.20")
        self.assertEqual(rows[1]["wall_clock_min"], "68.1")


if __name__ == "__main__":
    unittest.main()
