"""Offline tests for the per-file / per-parcel transaction boundaries.

Covers three defects that only show up under real psycopg/PostgreSQL
transaction semantics, so the existing ``FakeConnection`` (which treats
every ``transaction()`` block as a self-contained commit) cannot see them:

  (a) costar_ingest: the cycle-id collision SELECT opened an outer
      transaction, every per-file ``with conn.transaction()`` became a
      SAVEPOINT, and a failing file's ``conn.rollback()`` discarded every
      earlier file of the cycle — after those files had already been moved
      to ARCHIVED/ (never rescanned). Validation also let out-of-range and
      non-finite cells through to the database (or crash the cycle).
  (b) research: the same ``conn.rollback()`` in score_parcel /
      _process_parcel wiped every earlier parcel of the cycle; a NaN
      sub-score was the realistic trigger (jsonb rejects NaN).
  (c) reporting: the strategy memo's ``MAX(scored_at)`` latest-row pick
      double-counted parcels with tied ``scored_at`` rows.

``PgFake`` below models the psycopg/PostgreSQL behaviour those defects
depend on: IDLE/INTRANS/INERROR, implicit BEGIN on the first statement,
top-level vs nested (SAVEPOINT) ``transaction()`` blocks, savepoint
rollback, commit() forbidden inside a block, COMMIT on an aborted
transaction rolling back, jsonb rejecting NaN/Infinity/\\u0000, and a
ledger of committed statements. The live counterpart is
tests/live_txn_check.py (CI live-postgis job). SR-6: no network, no DB.
"""

from __future__ import annotations

import enum
import importlib.util
import json
import logging
import math
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest import mock

import tests  # noqa: F401 — force the package kill-switch guard (see tests/__init__.py)

import costar_ingest
import pipeline_common
import prepare
import reporting
import research

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_CHECK_PATH = Path(__file__).resolve().parent / "live_txn_check.py"

# The handlers under test log full tracebacks by design; keep suite output
# readable. assertLogs still captures (it overrides the level in its scope).
_PIPELINE_LOG = logging.getLogger("research")
_SAVED_LOG_LEVEL = logging.NOTSET


def setUpModule() -> None:
    global _SAVED_LOG_LEVEL
    _SAVED_LOG_LEVEL = _PIPELINE_LOG.level
    _PIPELINE_LOG.setLevel(logging.CRITICAL)


def tearDownModule() -> None:
    _PIPELINE_LOG.setLevel(_SAVED_LOG_LEVEL)


# ===========================================================================
# psycopg-faithful fake
# ===========================================================================
class TxStatus(enum.Enum):
    """Same member names as psycopg.pq.TransactionStatus."""

    IDLE = 0
    INTRANS = 2
    INERROR = 3
    UNKNOWN = 4


class FakeServerError(Exception):
    """A statement rejected by the server: the transaction becomes INERROR."""


class FakeClientError(Exception):
    """Raised client-side before anything reaches the server."""


class FakeProgrammingError(Exception):
    """psycopg.ProgrammingError stand-in (API misuse)."""


def _reject_json_constant(token: str) -> Any:
    raise ValueError(f"invalid input syntax for type json: {token}")


class _PgFakeCursor:
    def __init__(self, conn: "PgFake") -> None:
        self._conn = conn
        self._result: Any = None

    def execute(self, sql: str, params: Any = ()) -> None:
        self._result = self._conn._execute(sql, tuple(params or ()))

    def fetchone(self) -> Any:
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def fetchall(self) -> list:
        if self._result is None:
            return []
        return list(self._result) if isinstance(self._result, list) else [self._result]

    def __enter__(self) -> "_PgFakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class PgFake:
    """psycopg.Connection stand-in with PostgreSQL transaction semantics.

    ``responder(sql, params)`` returns the rows a statement produces (a
    tuple for one row, a list for several, None for none). ``fail_when(sql,
    params)`` returns an exception to raise for that statement, or None; a
    ``FakeServerError`` also moves the transaction to INERROR.
    """

    def __init__(
        self,
        responder: Callable[[str, tuple], Any] | None = None,
        fail_when: Callable[[str, tuple], BaseException | None] | None = None,
    ) -> None:
        self.status = TxStatus.IDLE
        self.broken = False
        self.closed = False
        self.responder = responder or (lambda sql, params: None)
        self.fail_when = fail_when
        self.fail_next_block_rollback = False
        self.executed: list[tuple[str, tuple]] = []
        self.committed: list[tuple[str, tuple]] = []
        self.events: list[tuple] = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self._pending: list[tuple[str, tuple]] = []
        self._depth = 0

    @property
    def info(self) -> Any:
        return mock.Mock(transaction_status=self.status)

    def cursor(self) -> _PgFakeCursor:
        return _PgFakeCursor(self)

    # -- statements --------------------------------------------------------
    @staticmethod
    def _jsonb_error(sql: str, params: tuple) -> BaseException | None:
        slots = [part.lstrip().startswith("::jsonb") for part in sql.split("%s")[1:]]
        for is_jsonb, value in zip(slots, params):
            if not is_jsonb or not isinstance(value, str):
                continue
            if "\\u0000" in value:
                return FakeServerError("unsupported Unicode escape sequence")
            try:
                json.loads(value, parse_constant=_reject_json_constant)
            except ValueError as exc:
                return FakeServerError(str(exc))
        return None

    def _execute(self, sql: str, params: tuple) -> Any:
        self.executed.append((sql, params))
        if self.broken or self.closed:
            raise FakeServerError("server closed the connection unexpectedly")
        if self.status is TxStatus.INERROR:
            raise FakeServerError("current transaction is aborted")
        if self.status is TxStatus.IDLE:
            self.status = TxStatus.INTRANS  # implicit BEGIN (autocommit=False)
        exc = self._jsonb_error(sql, params)
        if exc is None and self.fail_when is not None:
            exc = self.fail_when(sql, params)
        if exc is not None:
            if not isinstance(exc, FakeClientError):
                self.status = TxStatus.INERROR
            raise exc
        self._pending.append((sql, params))
        return self.responder(sql, params)

    # -- transaction control ----------------------------------------------
    def _commit_pending(self) -> None:
        self.events.append(("commit", len(self._pending)))
        self.committed.extend(self._pending)
        self._pending.clear()
        self.status = TxStatus.IDLE

    @contextmanager
    def transaction(self) -> Iterator["PgFake"]:
        if self.broken or self.closed:
            raise FakeServerError("server closed the connection unexpectedly")
        top_level = self.status is TxStatus.IDLE
        if self.status is TxStatus.INERROR:
            raise FakeServerError("current transaction is aborted")
        self.status = TxStatus.INTRANS
        marker = len(self._pending)
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            if self.fail_next_block_rollback or self.broken:
                # psycopg logs the failed ROLLBACK and re-raises the original.
                self.fail_next_block_rollback = False
                self.status = TxStatus.INERROR
                raise
            if top_level:
                self._pending.clear()
                self.status = TxStatus.IDLE
                self.events.append(("block_rollback",))
            else:
                del self._pending[marker:]  # ROLLBACK TO SAVEPOINT
                self.status = TxStatus.INTRANS
            raise
        self._depth -= 1
        if top_level:
            self._commit_pending()

    def commit(self) -> None:
        self.commit_calls += 1
        if self.broken or self.closed:
            raise FakeServerError("the connection is lost")
        if self._depth:
            raise FakeProgrammingError(
                "Explicit commit() forbidden within a Transaction context"
            )
        if self.status is TxStatus.INERROR:
            # The server answers COMMIT on an aborted transaction with ROLLBACK.
            self._pending.clear()
            self.status = TxStatus.IDLE
            self.events.append(("commit_became_rollback",))
            return
        if self.status is TxStatus.IDLE:
            return
        self._commit_pending()

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.broken or self.closed:
            raise FakeServerError("the connection is lost")
        if self._depth:
            raise FakeProgrammingError(
                "Explicit rollback() forbidden within a Transaction context"
            )
        self._pending.clear()
        self.status = TxStatus.IDLE
        self.events.append(("rollback",))


@contextmanager
def _yield_conn(conn: Any) -> Iterator[Any]:
    yield conn


def _patch_connection(conn: Any):
    return mock.patch.object(prepare, "get_connection", lambda: _yield_conn(conn))


def _committed_sql(conn: PgFake, sql: str) -> list[tuple]:
    return [params for s, params in conn.committed if s == sql]


# ===========================================================================
# (a) CoStar ingestion
# ===========================================================================
_HEADERS: dict[str, tuple[str, ...]] = {
    "submarket_stats": costar_ingest._SUBMARKET_STATS_REQUIRED_COLUMNS,
    "land_sales_comps": costar_ingest._LAND_SALES_COMPS_REQUIRED_COLUMNS,
    "building_sales_comps": costar_ingest._BUILDING_SALES_COMPS_REQUIRED_COLUMNS,
    "leasing_comps": costar_ingest._LEASING_COMPS_REQUIRED_COLUMNS,
    "land_listings": costar_ingest._LAND_LISTINGS_REQUIRED_COLUMNS,
}

_GOOD_ROWS: dict[str, dict[str, str]] = {
    "submarket_stats": {
        "submarket_name": "South Fulton", "market": "Atlanta",
        "total_inventory_sf": "28500000", "vacancy_rate_pct": "5.4",
        "availability_rate_pct": "7.1", "net_absorption_t12_sf": "1850000",
        "under_construction_sf": "2400000", "proposed_sf": "1200000",
        "asking_rent_nnn_psf": "7.85", "report_date": "2026-04-27",
    },
    "land_sales_comps": {
        "address": "1234 Industrial Blvd", "parcel_id": "09F-1", "county": "Fulton",
        "submarket": "South Fulton", "acres": "12.5", "sale_date": "2026-03-14",
        "sale_price": "1875000", "price_per_acre": "150000", "buyer_name": "",
        "seller_name": "", "zoning": "M-2", "intended_use": "distribution",
        "cap_rate": "",
    },
    "building_sales_comps": {
        "address": "4040 Logistics Dr", "submarket": "South Fulton",
        "building_sf": "250000", "year_built": "2015", "clear_height_ft": "32",
        "sale_date": "2026-03-05", "sale_price": "32500000", "price_psf": "130",
        "cap_rate": "5.5", "noi_at_sale": "", "buyer_name": "", "seller_name": "",
        "tenant_at_sale": "", "lease_term_remaining_years": "",
    },
    "leasing_comps": {
        "address": "4040 Logistics Dr", "submarket": "South Fulton",
        "tenant_name": "Tenant A", "tenant_industry": "3PL",
        "lease_start_date": "2025-09-15", "lease_term_months": "84",
        "building_sf_leased": "250000", "starting_rent_psf_nnn": "7.95",
        "rent_escalation_pct": "3.0", "lease_type": "NNN",
    },
    "land_listings": {
        "address": "1500 Industrial Pkwy", "parcel_id": "09F-1500", "county": "Fulton",
        "submarket": "South Fulton", "acres": "18.5", "zoning": "M-2",
        "topography_notes": "flat", "asking_price": "2775000",
        "asking_price_per_acre": "150000", "listing_date": "2026-04-01",
        "days_on_market": "42", "listing_broker": "", "listing_broker_firm": "",
        "utilities_status": "", "entitlement_status": "",
    },
}

_VALIDATORS: dict[str, Callable[[dict], tuple]] = {
    "submarket_stats": costar_ingest._validate_submarket_stats_row,
    "land_sales_comps": costar_ingest._validate_land_sales_comps_row,
    "building_sales_comps": costar_ingest._validate_building_sales_comps_row,
    "leasing_comps": costar_ingest._validate_leasing_comps_row,
    "land_listings": costar_ingest._validate_land_listings_row,
}

_NON_FINITE_TOKENS = ("nan", "NaN", "inf", "-inf", "Infinity", "1e400", "-1e400")


@contextmanager
def _temp_costar_base() -> Iterator[Path]:
    """Point every module binding of _COSTAR_BASE_DIR at a tempdir (R-329)."""
    import runner

    original = costar_ingest._COSTAR_BASE_DIR
    with tempfile.TemporaryDirectory() as td:
        for module in (costar_ingest, research, runner):
            module._COSTAR_BASE_DIR = Path(td)
        try:
            yield Path(td)
        finally:
            for module in (costar_ingest, research, runner):
                module._COSTAR_BASE_DIR = original


def _stage(base: Path, export_type: str, name: str, rows: list[dict[str, str]]) -> Path:
    import csv

    target_dir = base / export_type
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / name
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        header = _HEADERS[export_type]
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(col, "") for col in header])
    return path


def _row(export_type: str, **over: str) -> dict[str, str]:
    row = dict(_GOOD_ROWS[export_type])
    row.update(over)
    return row


def _ingest_responder(sql: str, params: tuple) -> Any:
    if sql == costar_ingest._SQL_COUNT_LOG_FOR_INGESTION_CYCLE:
        return (0,)
    if sql == costar_ingest._SQL_UPSERT_SUBMARKETS_REF:
        return (params[2],)  # RETURNING submarket_name: newly created
    return None


def _ingestion_logged_files(rows: list[tuple[str, tuple]]) -> set[str]:
    """File names whose research_log 'ingestion' row is in ``rows``."""
    out: set[str] = set()
    for sql, params in rows:
        if sql == costar_ingest._SQL_INSERT_RESEARCH_LOG_INGESTION:
            note = params[4]
            out.add(note.split("file=", 1)[1].split(" ", 1)[0])
    return out


class _ArchiveProbe:
    """Wraps costar_ingest._archive_file: records what was durable at move time."""

    def __init__(self, conn: PgFake) -> None:
        self.conn = conn
        self.committed_at_archive: dict[str, set[str]] = {}
        self._original = costar_ingest._archive_file

    def __call__(self, source: Path) -> Path:
        self.conn.events.append(("archive", source.name))
        self.committed_at_archive[source.name] = _ingestion_logged_files(self.conn.committed)
        return self._original(source)


def _run_cycle(conn: PgFake, probe: _ArchiveProbe | None = None) -> dict[str, Any]:
    with _patch_connection(conn):
        if probe is None:
            return costar_ingest.run_ingestion_cycle()
        with mock.patch.object(costar_ingest, "_archive_file", probe):
            return costar_ingest.run_ingestion_cycle()


def _statuses(summary: dict[str, Any], export_type: str) -> list[str]:
    return [f["status"] for f in summary["per_export_type"][export_type]["per_file"]]


def _intake_files(base: Path) -> list[str]:
    return sorted(
        p.name for p in base.glob("*/*.csv")
        if p.parent.name not in ("ARCHIVED", "FAILED")
    )


def _dir_files(base: Path, kind: str, suffix: str = ".csv") -> list[str]:
    if not (base / kind).exists():
        return []
    return sorted(p.name for p in (base / kind).rglob(f"*{suffix}"))


class TestIngestionCycleIsolation(unittest.TestCase):
    """(a) a failing file loses only its own rows; archive only after commit."""

    def _stage_three(self, base: Path, *, middle: str = "server") -> tuple[Path, Path, Path]:
        first = _stage(base, "submarket_stats", "submarket_stats_20260420.csv", [
            _row("submarket_stats"),
        ])
        if middle == "server":
            # NUL in a text column that only reaches the `raw` JSONB payload:
            # PostgreSQL rejects the \u0000 escape on the server.
            second = _stage(base, "land_sales_comps", "land_sales_comps_202603.csv", [
                _row("land_sales_comps", intended_use="distribution\x00"),
            ])
        else:
            # Market that slugifies to nothing: _slugify raises ValueError
            # (a client-side error) inside the file's transaction block.
            second = _stage(base, "land_sales_comps", "land_sales_comps_202603.csv", [
                _row("land_sales_comps", submarket="---"),
            ])
        third = _stage(base, "leasing_comps", "leasing_comps_202603.csv", [
            _row("leasing_comps"),
        ])
        return first, second, third

    def _assert_isolated(self, base: Path, conn: PgFake, probe: _ArchiveProbe,
                         summary: dict[str, Any], files: tuple[Path, Path, Path]) -> None:
        first, second, third = files
        self.assertEqual(_statuses(summary, "submarket_stats"), ["loaded"])
        self.assertEqual(_statuses(summary, "land_sales_comps"), ["failed"])
        self.assertEqual(_statuses(summary, "leasing_comps"), ["loaded"])
        # Earlier AND later files' rows are committed; the failing file's are not.
        self.assertEqual(
            _ingestion_logged_files(conn.committed), {first.name, third.name},
        )
        self.assertEqual(len(_committed_sql(conn, costar_ingest._SQL_INSERT_MARKET_CONTEXT)), 1)
        self.assertEqual(len(_committed_sql(conn, costar_ingest._SQL_INSERT_LEASING_COMP)), 1)
        self.assertEqual(_committed_sql(conn, costar_ingest._SQL_INSERT_LAND_SALES), [])
        # Each file was durable before it was moved to ARCHIVED/.
        self.assertIn(first.name, probe.committed_at_archive[first.name])
        self.assertIn(third.name, probe.committed_at_archive[third.name])
        self.assertNotIn(second.name, probe.committed_at_archive)
        # The loaders never rolled back the shared connection.
        self.assertEqual(conn.rollback_calls, 0)
        # Contract: good files archived, the failing file in FAILED/ + .error.json.
        self.assertEqual(len(_dir_files(base, "ARCHIVED")), 2)
        failed = _dir_files(base, "FAILED")
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].startswith("land_sales_comps_202603"))
        self.assertEqual(len(_dir_files(base, "FAILED", ".error.json")), 1)
        self.assertEqual(_intake_files(base), [])

    def test_server_side_failure_in_middle_file_is_isolated(self) -> None:
        with _temp_costar_base() as base:
            files = self._stage_three(base, middle="server")
            conn = PgFake(responder=_ingest_responder)
            probe = _ArchiveProbe(conn)
            summary = _run_cycle(conn, probe)
            self._assert_isolated(base, conn, probe, summary, files)
            error = summary["per_export_type"]["land_sales_comps"]["per_file"][0]["error"]
            self.assertIn("Unicode escape", error)

    def test_client_side_failure_in_middle_file_is_isolated(self) -> None:
        with _temp_costar_base() as base:
            files = self._stage_three(base, middle="client")
            conn = PgFake(responder=_ingest_responder)
            probe = _ArchiveProbe(conn)
            summary = _run_cycle(conn, probe)
            self._assert_isolated(base, conn, probe, summary, files)

    def test_same_export_type_earlier_file_survives_later_failure(self) -> None:
        # The original repro shape: two files of one export type, the second fails.
        with _temp_costar_base() as base:
            first = _stage(base, "land_sales_comps", "land_sales_comps_202602.csv", [
                _row("land_sales_comps"),
            ])
            _stage(base, "land_sales_comps", "land_sales_comps_202603.csv", [
                _row("land_sales_comps", address="9 Other Rd", intended_use="x\x00"),
            ])
            conn = PgFake(responder=_ingest_responder)
            summary = _run_cycle(conn)
            self.assertEqual(_statuses(summary, "land_sales_comps"), ["loaded", "failed"])
            self.assertEqual(_ingestion_logged_files(conn.committed), {first.name})
            self.assertEqual(len(_committed_sql(conn, costar_ingest._SQL_INSERT_LAND_SALES)), 1)

    def test_every_file_commits_in_its_own_top_level_transaction(self) -> None:
        # Commit precedes each archive move, one commit per loaded file, and
        # DELETE precedes INSERT inside each file's transaction (SR-9).
        with _temp_costar_base() as base:
            _stage(base, "submarket_stats", "submarket_stats_20260420.csv", [
                _row("submarket_stats"),
            ])
            _stage(base, "submarket_stats", "submarket_stats_20260427.csv", [
                _row("submarket_stats", report_date="2026-04-27"),
            ])
            conn = PgFake(responder=_ingest_responder)
            probe = _ArchiveProbe(conn)
            _run_cycle(conn, probe)
            kinds = [e[0] for e in conn.events]
            self.assertEqual(kinds, ["commit", "commit", "archive", "commit", "archive"])
            self.assertEqual(conn.events[0], ("commit", 1))  # the collision SELECT
            sqls = [s for s, _ in conn.committed]
            deletes = [i for i, s in enumerate(sqls)
                       if s == costar_ingest._SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST]
            inserts = [i for i, s in enumerate(sqls)
                       if s == costar_ingest._SQL_INSERT_MARKET_CONTEXT]
            self.assertEqual(len(deletes), 2)
            self.assertLess(deletes[0], inserts[0])
            self.assertLess(deletes[1], inserts[1])

    def test_loader_never_rolls_back_an_outer_transaction(self) -> None:
        # Called with an outer transaction open (the shape that lost data):
        # the failing file's block is a SAVEPOINT, rolled back on its own;
        # the caller's earlier pending work must survive.
        with _temp_costar_base() as base:
            path = _stage(base, "land_sales_comps", "land_sales_comps_202603.csv", [
                _row("land_sales_comps", intended_use="x\x00"),
            ])
            conn = PgFake(responder=_ingest_responder)
            with conn.cursor() as cur:
                cur.execute("INSERT INTO earlier_work VALUES (%s)", (1,))
            result = costar_ingest._load_land_sales_comps_file(conn, "ingest-x", path)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(conn.rollback_calls, 0)
            self.assertIs(conn.status, TxStatus.INTRANS)
            conn.commit()
            self.assertIn(("INSERT INTO earlier_work VALUES (%s)", (1,)), conn.committed)


class TestIngestionConnectionFailureHandling(unittest.TestCase):
    """R6 / R14 / R16 hardening around the per-file handler."""

    def _stage_three(self, base: Path) -> tuple[Path, Path, Path]:
        return (
            _stage(base, "submarket_stats", "submarket_stats_20260420.csv",
                   [_row("submarket_stats")]),
            _stage(base, "land_sales_comps", "land_sales_comps_202603.csv",
                   [_row("land_sales_comps")]),
            _stage(base, "leasing_comps", "leasing_comps_202603.csv",
                   [_row("leasing_comps")]),
        )

    def test_broken_connection_aborts_and_leaves_files_in_intake(self) -> None:
        with _temp_costar_base() as base:
            first, second, third = self._stage_three(base)
            conn = PgFake(responder=_ingest_responder)

            def fail(sql: str, params: tuple) -> BaseException | None:
                if sql == costar_ingest._SQL_INSERT_LAND_SALES:
                    conn.broken = True
                    return FakeServerError("server closed the connection unexpectedly")
                return None

            conn.fail_when = fail
            with self.assertRaises(FakeServerError):
                _run_cycle(conn)
            # Nothing quarantined: FAILED/ is never rescanned, so a dropped
            # connection must not strand good files there.
            self.assertEqual(_dir_files(base, "FAILED"), [])
            self.assertEqual(_intake_files(base), sorted([second.name, third.name]))
            self.assertEqual(_ingestion_logged_files(conn.committed), {first.name})

    def test_inerror_after_failed_block_rolls_back_and_continues(self) -> None:
        with _temp_costar_base() as base:
            first, second, third = self._stage_three(base)
            conn = PgFake(responder=_ingest_responder)

            def fail(sql: str, params: tuple) -> BaseException | None:
                if sql == costar_ingest._SQL_INSERT_LAND_SALES:
                    conn.fail_next_block_rollback = True
                    return FakeServerError("deadlock detected")
                return None

            conn.fail_when = fail
            with self.assertLogs("research", level="ERROR") as logs:
                summary = _run_cycle(conn)
            self.assertTrue(any("roll" in line.lower() for line in logs.output))
            self.assertEqual(conn.rollback_calls, 1)
            self.assertEqual(_statuses(summary, "land_sales_comps"), ["failed"])
            self.assertEqual(_ingestion_logged_files(conn.committed), {first.name, third.name})

    def test_budget_exceeded_propagates_and_file_stays_in_intake(self) -> None:
        with _temp_costar_base() as base:
            first, second, third = self._stage_three(base)
            conn = PgFake(responder=_ingest_responder)

            def fail(sql: str, params: tuple) -> BaseException | None:
                if sql == costar_ingest._SQL_INSERT_LAND_SALES:
                    return prepare.BudgetExceeded("wall-clock budget exceeded")
                return None

            conn.fail_when = fail
            with self.assertRaises(prepare.BudgetExceeded):
                _run_cycle(conn)
            self.assertEqual(_dir_files(base, "FAILED"), [])
            self.assertIn(second.name, _intake_files(base))

    def test_archive_failure_after_commit_keeps_rows_committed(self) -> None:
        # R16 (accepted): the cycle aborts on the OSError, but the file's rows
        # are already durable and the file stays in intake for an idempotent
        # (DELETE-then-INSERT) re-ingest next cycle.
        with _temp_costar_base() as base:
            path = _stage(base, "submarket_stats", "submarket_stats_20260420.csv",
                          [_row("submarket_stats")])
            conn = PgFake(responder=_ingest_responder)
            with mock.patch.object(costar_ingest, "_archive_file",
                                   side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    _run_cycle(conn)
            self.assertEqual(_ingestion_logged_files(conn.committed), {path.name})
            self.assertTrue(path.exists())


class TestIngestionValidationBounds(unittest.TestCase):
    """(a) out-of-range and non-finite cells are row-level validation errors."""

    INT8_MAX_EXACT = "9223372036854774784"   # largest float-exact value <= 2**63-1
    INT8_MAX_TEXT = "9223372036854775807"    # parses to 2**63 -> out of range
    INT8_MIN = "-9223372036854775808"        # -2**63, float-exact
    INT8_BELOW_MIN = "-9223372036854777856"  # next float below -2**63
    INT4_MAX = "2147483647"
    INT4_OVER = "2147483648"

    def _ok(self, export_type: str, **over: str) -> dict[str, Any]:
        out, err = _VALIDATORS[export_type](_row(export_type, **over))
        self.assertIsNone(err, f"{export_type} {over} unexpectedly rejected: {err}")
        return out

    def _rejected(self, export_type: str, field: str, value: str) -> None:
        # Raising (the old 'nan'-in-an-int-column crash) fails the test too.
        out, err = _VALIDATORS[export_type](_row(export_type, **{field: value}))
        self.assertIsNone(out, f"{export_type}.{field}={value!r} was accepted")
        self.assertTrue(err, f"{export_type}.{field}={value!r}: no error message")

    def test_bigint_bounds(self) -> None:
        for export_type, field in (
            ("submarket_stats", "net_absorption_t12_sf"),
            ("submarket_stats", "under_construction_sf"),
            ("submarket_stats", "proposed_sf"),
            ("land_sales_comps", "sale_price"),
            ("building_sales_comps", "sale_price"),
            ("land_listings", "asking_price"),
        ):
            with self.subTest(export_type=export_type, field=field):
                out = self._ok(export_type, **{field: self.INT8_MAX_EXACT})
                self.assertEqual(out[field], int(self.INT8_MAX_EXACT))
                self._rejected(export_type, field, self.INT8_MAX_TEXT)
                self._rejected(export_type, field, "99999999999999999999")
                self._rejected(export_type, field, "1e19")

    def test_signed_absorption_lower_bound(self) -> None:
        out = self._ok("submarket_stats", net_absorption_t12_sf=self.INT8_MIN)
        self.assertEqual(out["net_absorption_t12_sf"], -(2 ** 63))
        out = self._ok("submarket_stats", net_absorption_t12_sf="-500000")
        self.assertEqual(out["net_absorption_t12_sf"], -500000)
        self._rejected("submarket_stats", "net_absorption_t12_sf", self.INT8_BELOW_MIN)

    def test_integer_bounds(self) -> None:
        for export_type, field in (
            ("leasing_comps", "lease_term_months"),
            ("land_listings", "days_on_market"),
        ):
            with self.subTest(export_type=export_type, field=field):
                out = self._ok(export_type, **{field: self.INT4_MAX})
                self.assertEqual(out[field], 2 ** 31 - 1)
                self._rejected(export_type, field, self.INT4_OVER)
                self._rejected(export_type, field, "3e9")

    def test_non_finite_rejected_in_every_numeric_field(self) -> None:
        numeric_fields = {
            "submarket_stats": (
                "total_inventory_sf", "vacancy_rate_pct", "availability_rate_pct",
                "net_absorption_t12_sf", "under_construction_sf", "proposed_sf",
                "asking_rent_nnn_psf",
            ),
            "land_sales_comps": ("acres", "sale_price", "price_per_acre", "cap_rate"),
            "building_sales_comps": (
                "building_sf", "sale_price", "price_psf", "cap_rate",
                "year_built", "clear_height_ft",
            ),
            "leasing_comps": (
                "lease_term_months", "building_sf_leased", "starting_rent_psf_nnn",
                "rent_escalation_pct",
            ),
            "land_listings": (
                "acres", "asking_price", "asking_price_per_acre", "days_on_market",
            ),
        }
        for export_type, fields in numeric_fields.items():
            for field in fields:
                for token in _NON_FINITE_TOKENS:
                    with self.subTest(export_type=export_type, field=field, token=token):
                        self._rejected(export_type, field, token)

    def test_unstored_total_inventory_size_unchanged(self) -> None:
        # Validated but never stored: no new size bound may drop such rows.
        out = self._ok("submarket_stats", total_inventory_sf="1e30")
        self.assertEqual(out["total_inventory_sf"], int(round(1e30)))

    def test_finite_values_parse_exactly_as_before(self) -> None:
        out = self._ok("land_sales_comps", acres="12.5", price_per_acre="$150,000",
                       cap_rate="5.5%")
        self.assertEqual((out["acres"], out["price_per_acre"], out["cap_rate"]),
                         (12.5, 150000.0, 5.5))
        self.assertEqual(costar_ingest._coerce_optional_decimal(7), (7.0, None))
        self.assertEqual(costar_ingest._coerce_optional_decimal("N/A"), (None, None))

    def test_full_cycle_with_poison_cells_in_every_export_type(self) -> None:
        # 'nan'/'inf' in int-coerced columns used to raise an uncaught
        # ValueError/OverflowError and crash the whole cycle on every
        # experiment; now they are row errors and the other rows load.
        poison = {
            "submarket_stats": [("net_absorption_t12_sf", "nan"), ("proposed_sf", "inf")],
            "land_sales_comps": [("sale_price", "nan"), ("acres", "inf")],
            "building_sales_comps": [("sale_price", "-inf"), ("year_built", "nan")],
            "leasing_comps": [("lease_term_months", "nan"), ("starting_rent_psf_nnn", "NaN")],
            "land_listings": [("days_on_market", "inf"), ("asking_price", "1e400")],
        }
        names = {
            "submarket_stats": "submarket_stats_20260420.csv",
            "land_sales_comps": "land_sales_comps_202603.csv",
            "building_sales_comps": "building_sales_comps_202603.csv",
            "leasing_comps": "leasing_comps_202603.csv",
            "land_listings": "land_listings_20260420.csv",
        }
        with _temp_costar_base() as base:
            for export_type, cells in poison.items():
                rows = [_row(export_type)]
                for i, (field, value) in enumerate(cells):
                    rows.append(_row(export_type, address=f"{i} Poison Rd",
                                     submarket_name="South Fulton", **{field: value}))
                _stage(base, export_type, names[export_type], rows)
            conn = PgFake(responder=_ingest_responder)
            summary = _run_cycle(conn)
            for export_type in poison:
                per_file = summary["per_export_type"][export_type]["per_file"]
                self.assertEqual(
                    [(f["status"], f["rows_loaded"], f["rows_failed"]) for f in per_file],
                    [("loaded", 1, 2)], export_type,
                )
            self.assertEqual(_ingestion_logged_files(conn.committed), set(names.values()))
            # Each row error is surfaced as a data_gap flag (existing contract).
            flags = [p for s, p in conn.committed if s == pipeline_common._SQL_INSERT_FLAG
                     and "row-level validation failure" in p[3]]
            self.assertEqual(len(flags), 10)


# ===========================================================================
# (b) scoring and discovery
# ===========================================================================
_GOLDEN_WEIGHTS = {
    "S1_interstate_proximity": 15, "S2_parcel_geometry": 10, "S3_topography": 8,
    "S4_submarket_vacancy": 10, "S5_submarket_absorption": 8,
    "S6_competing_pipeline": 6, "S7_labor_pool": 8, "S8_land_basis": 12,
    "S9_entitlement_complexity": 10, "S10_incentives": 4,
    "S11_rail_adjacency": 4, "S12_demand_generators": 5,
}
_GOLDEN_PARAMS = {"scoring_weights": _GOLDEN_WEIGHTS}
_GOLDEN_CYCLE_ID = "score-atlanta-20260101T000000Z-0000"
_SUB = "golden_sub"


def _scoring_responder(parcels: dict[str, tuple], geometry: dict[str, tuple | None],
                       blocks: dict[str, str] | None = None) -> Callable[[str, tuple], Any]:
    as_of = date.today() - timedelta(days=10)

    def respond(sql: str, params: tuple) -> Any:
        if sql in (research._SQL_COUNT_LOG_FOR_SCORING_CYCLE, research._SQL_COUNT_LOG_FOR_CYCLE):
            return (0,)
        if sql in (research._SQL_LIST_PARCELS_FOR_SCORING,
                   research._SQL_LIST_PARCELS_FOR_SCORING_RUN_SCOPED):
            return [(pid,) for pid in parcels]
        if sql == research._SQL_DISTINCT_SUBMARKETS_FOR_PARCELS:
            return [(_SUB,)]
        if sql == research._SQL_LATEST_MARKET_CONTEXT_BATCH:
            return [(_SUB, 4.0, 800000, 300000, 0, 7.5, as_of, "costar")]
        if sql == research._SQL_SUBMARKET_LAND_MEDIAN_BATCH:
            return [(_SUB, 5, 50000.0)]
        if sql == research._SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH:
            return [(pid, desc) for pid, desc in (blocks or {}).items()]
        if sql == research._SQL_FETCH_PARCEL:
            return parcels.get(params[0])
        if sql == research._SQL_S2_GEOMETRY:
            return geometry.get(params[0])
        return None

    return respond


def _golden_scenario() -> tuple[dict[str, tuple], dict[str, tuple | None], dict[str, str]]:
    parcels = {
        "g-1": ("g-1", "atlanta", _SUB, "GA", 14.7, None, None, 185000, -84.554, 33.554),
        "g-2": ("g-2", "atlanta", None, "GA", 9.0, None, None, None, -84.40, 33.75),
        "g-3": ("g-3", "atlanta", _SUB, "GA", 30.0, None, None, 2500000, -84.51, 33.53),
    }
    geometry = {
        "g-1": (59000.0, 60000.0, 1.2),
        "g-2": None,
        "g-3": (40000.0, 60000.0, 2.5),
    }
    blocks = {"g-3": "actionability_block: environmental deal killer"}
    return parcels, geometry, blocks


def _run_scoring(conn: PgFake) -> dict[str, Any]:
    with _patch_connection(conn), \
            mock.patch.object(prepare, "verify_parameters_unchanged"), \
            mock.patch.object(prepare, "get_parameters", return_value=_GOLDEN_PARAMS), \
            mock.patch.object(research, "_make_scoring_cycle_id",
                              return_value=_GOLDEN_CYCLE_ID):
        return research.run_scoring_cycle(
            "atlanta", run_tag="golden-run", experiment_id="exp-golden",
        )


def _sql_name(sql: str) -> str:
    for module in (research, pipeline_common):
        for name in dir(module):
            if name.startswith("_SQL_") and getattr(module, name) == sql:
                return name
    return sql


def _writes(conn: PgFake) -> list[tuple[str, tuple]]:
    return [(_sql_name(s), p) for s, p in conn.committed if s.lstrip().upper().startswith("INSERT")]


def _all_params(conn: PgFake) -> list[Any]:
    return [value for _, params in conn.executed for value in params]


def _assert_no_non_finite(case: unittest.TestCase, conn: PgFake) -> None:
    for value in _all_params(conn):
        if isinstance(value, float):
            case.assertTrue(math.isfinite(value), f"non-finite parameter {value!r}")
        if isinstance(value, str):
            for token in ("NaN", "Infinity"):
                case.assertNotIn(token, value, f"{token} reached a statement: {value[:120]}")


class TestScoringCycleIsolation(unittest.TestCase):
    """(b) a failing parcel drops only itself; one end-of-cycle commit."""

    def test_middle_parcel_failure_keeps_earlier_and_later_parcels(self) -> None:
        parcels, geometry, blocks = _golden_scenario()

        def fail(sql: str, params: tuple) -> BaseException | None:
            if sql == research._SQL_INSERT_PARCEL_SCORE and params[0] == "g-2":
                return FakeServerError("deadlock detected")
            return None

        conn = PgFake(_scoring_responder(parcels, geometry, blocks), fail)
        summary = _run_scoring(conn)
        self.assertEqual([p["status"] for p in summary["parcels"]], ["scored", "error", "scored"])
        scored = [p[0] for p in _committed_sql(conn, research._SQL_INSERT_PARCEL_SCORE)]
        self.assertEqual(scored, ["g-1", "g-3"])
        logged = [p[3] for p in _committed_sql(conn, research._SQL_INSERT_RESEARCH_LOG_SCORING)]
        self.assertEqual(logged, ["g-1", "g-3"])
        self.assertEqual(conn.rollback_calls, 0)
        # G9: commit granularity unchanged — exactly one commit, at cycle end.
        self.assertEqual(conn.commit_calls, 1)
        self.assertEqual([e[0] for e in conn.events], ["commit"])

    def test_client_side_failure_in_write_block_is_isolated(self) -> None:
        parcels, geometry, blocks = _golden_scenario()

        def fail(sql: str, params: tuple) -> BaseException | None:
            if sql == research._SQL_INSERT_PARCEL_SCORE and params[0] == "g-2":
                return FakeClientError("PostgreSQL text fields cannot contain NUL (0x00) bytes")
            return None

        conn = PgFake(_scoring_responder(parcels, geometry, blocks), fail)
        summary = _run_scoring(conn)
        self.assertEqual([p["status"] for p in summary["parcels"]], ["scored", "error", "scored"])
        scored = [p[0] for p in _committed_sql(conn, research._SQL_INSERT_PARCEL_SCORE)]
        self.assertEqual(scored, ["g-1", "g-3"])
        self.assertEqual(conn.rollback_calls, 0)


class TestScoreParcelUnrecoverableFailures(unittest.TestCase):
    """R6 / R14: failures that must abort the cycle instead of skipping a parcel."""

    def _score_g2(self, fail: Callable[[PgFake], Callable[[str, tuple], Any]]) -> PgFake:
        parcels, geometry, _ = _golden_scenario()
        conn = PgFake(_scoring_responder(parcels, geometry))
        conn.fail_when = fail(conn)
        # The cycle's collision SELECT has opened the outer transaction.
        research._count_log_rows(conn, _GOLDEN_CYCLE_ID)
        research.score_parcel(
            "g-2", conn=conn, cycle_id=_GOLDEN_CYCLE_ID, params=_GOLDEN_PARAMS,
            run_tag="golden-run", experiment_id="exp-golden",
        )
        return conn

    def test_broken_connection_raises(self) -> None:
        def fail(conn: PgFake) -> Callable[[str, tuple], Any]:
            def inner(sql: str, params: tuple) -> BaseException | None:
                if sql == research._SQL_INSERT_PARCEL_SCORE:
                    conn.broken = True
                    return FakeServerError("server closed the connection unexpectedly")
                return None
            return inner

        with self.assertRaises(FakeServerError):
            self._score_g2(fail)

    def test_aborted_outer_transaction_raises_without_rollback(self) -> None:
        # INERROR after the block means the savepoint rollback itself failed:
        # the outer transaction (holding the cycle's earlier parcels) is
        # already lost, so continuing would commit a silently partial cycle.
        def fail(conn: PgFake) -> Callable[[str, tuple], Any]:
            def inner(sql: str, params: tuple) -> BaseException | None:
                if sql == research._SQL_INSERT_PARCEL_SCORE:
                    conn.fail_next_block_rollback = True
                    return FakeServerError("deadlock detected")
                return None
            return inner

        with mock.patch.object(PgFake, "rollback", side_effect=AssertionError("rollback")):
            with self.assertRaises(FakeServerError):
                self._score_g2(fail)

    def test_budget_exceeded_propagates(self) -> None:
        def fail(conn: PgFake) -> Callable[[str, tuple], Any]:
            def inner(sql: str, params: tuple) -> BaseException | None:
                if sql == research._SQL_INSERT_PARCEL_SCORE:
                    return prepare.BudgetExceeded("wall-clock budget exceeded")
                return None
            return inner

        with self.assertRaises(prepare.BudgetExceeded):
            self._score_g2(fail)


class TestScoringNonFiniteSubScores(unittest.TestCase):
    """(b) NaN/±inf sub-scores are stored as null, before any consumer reads them."""

    def _score_one(self, s10_value: Any) -> tuple[dict[str, Any], PgFake]:
        parcels, geometry, _ = _golden_scenario()
        conn = PgFake(_scoring_responder({"g-1": parcels["g-1"]}, geometry))
        with mock.patch.object(research, "_compute_s10", return_value=s10_value):
            summary = _run_scoring(conn)
        return summary["parcels"][0], conn

    def test_non_finite_sub_score_is_stored_as_null(self) -> None:
        baseline, _ = self._score_one(None)
        finite, _ = self._score_one(4)
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                result, conn = self._score_one(value)
                self.assertEqual(result["status"], "scored")
                self.assertIsNone(result["sub_scores"]["S10_incentives"])
                # Normalized BEFORE composite/confidence/strategy/actionability:
                # identical to a genuinely unavailable S10.
                for key in ("composite_score", "confidence_score", "actionability",
                            "strategy_fit", "primary_strategy"):
                    self.assertEqual(result[key], baseline[key], key)
                self.assertTrue(math.isfinite(result["composite_score"]))
                self.assertLess(result["confidence_score"], finite["confidence_score"])
                insert = _committed_sql(conn, research._SQL_INSERT_PARCEL_SCORE)
                self.assertEqual(len(insert), 1)
                self.assertIsNone(json.loads(insert[0][5])["S10_incentives"])
                gap_flags = [p for p in _committed_sql(conn, pipeline_common._SQL_INSERT_FLAG)
                             if p[0] == "data_gap" and "S10_incentives" in p[3]]
                self.assertEqual(len(gap_flags), 1)
                _assert_no_non_finite(self, conn)

    def test_non_finite_composite_is_never_written(self) -> None:
        # A finite but absurd sub-score overflows the weighted sum to inf;
        # Postgres would store NUMERIC 'Infinity', which passes any threshold.
        result, conn = self._score_one(1e308)
        self.assertEqual(result["status"], "scored")
        self.assertIsNone(result["composite_score"])
        _assert_no_non_finite(self, conn)


# Captured from base 29c051e research.py (pre-change) with _golden_scenario():
# every INSERT's parameters, byte for byte. Finite scoring output must not move.
_GOLDEN_FINITE_WRITES: list[tuple[str, tuple]] = [
    ('_SQL_INSERT_PARCEL_SCORE',
     ('g-1',
      77.33,
      0.5833333333333334,
      'PASS',
      '{}',
      '{"S1_interstate_proximity": null, "S2_parcel_geometry": 10, "S3_topography": null, '
      '"S4_submarket_vacancy": 8, "S5_submarket_absorption": 7, "S6_competing_pipeline": '
      '7, "S7_labor_pool": null, "S8_land_basis": 10, "S9_entitlement_complexity": 5, '
      '"S10_incentives": 4, "S11_rail_adjacency": null, "S12_demand_generators": null}',
      '{"bts": "MODERATE", "spec": "MODERATE", "land_bank": "STRONG", "ground_lease": '
      '"WEAK", "flip": "STRONG"}',
      'land_bank',
      'phase78: composite=77.33 actionability=PASS primary_strategy=land_bank S2=10 S4=8 '
      'S5=7 S6=7 S8=10 S9=5 S10=4',
      'golden-run',
      'exp-golden')),
    ('_SQL_INSERT_RESEARCH_LOG_SCORING',
     ('score-atlanta-20260101T000000Z-0000',
      'scoring',
      'atlanta',
      'g-1',
      77.33,
      'PASS',
      '{"bts": "MODERATE", "spec": "MODERATE", "land_bank": "STRONG", "ground_lease": '
      '"WEAK", "flip": "STRONG"}',
      'phase78: composite=77.33 actionability=PASS primary_strategy=land_bank S2=10 S4=8 '
      'S5=7 S6=7 S8=10 S9=5 S10=4')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-1',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S1_interstate_proximity (interstate '
      'proximity) unjoined: pending later-phase data wiring (Google Maps API / GIS)',
      'wire Google Maps API / GIS for parcel_id=g-1')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-1',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S3_topography (topography / grading '
      'cost) unjoined: pending later-phase data wiring (USGS 3DEP LiDAR)',
      'wire USGS 3DEP LiDAR for parcel_id=g-1')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-1',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S7_labor_pool (labor pool density) '
      'unjoined: pending later-phase data wiring (Census LODES / OnTheMap)',
      'wire Census LODES / OnTheMap for parcel_id=g-1')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-1',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S11_rail_adjacency (rail adjacency) '
      'unjoined: pending later-phase data wiring (Class I railroad maps)',
      'wire Class I railroad maps for parcel_id=g-1')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-1',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S12_demand_generators (proximity to '
      'demand generators) unjoined: pending later-phase data wiring (GIS / facility '
      'locations)',
      'wire GIS / facility locations for parcel_id=g-1')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-1',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S6 approximated at submarket grain; '
      'program.md spec is 5-mi radius',
      'Phase 11+: implement radius-search facility for S6')),
    ('_SQL_INSERT_PARCEL_SCORE',
     ('g-2',
      35.71,
      0.16666666666666666,
      'FAIL:strategy',
      '{"strategy": "no strategy rated STRONG or MODERATE"}',
      '{"S1_interstate_proximity": null, "S2_parcel_geometry": null, "S3_topography": '
      'null, "S4_submarket_vacancy": null, "S5_submarket_absorption": null, '
      '"S6_competing_pipeline": null, "S7_labor_pool": null, "S8_land_basis": null, '
      '"S9_entitlement_complexity": 5, "S10_incentives": 0, "S11_rail_adjacency": null, '
      '"S12_demand_generators": null}',
      '{"bts": "WEAK", "spec": "WEAK", "land_bank": "N/A", "ground_lease": "WEAK", "flip": '
      '"N/A"}',
      None,
      'phase78: composite=35.71 actionability=FAIL:strategy primary_strategy=None S2=None '
      'S4=None S5=None S6=None S8=None S9=5 S10=0',
      'golden-run',
      'exp-golden')),
    ('_SQL_INSERT_RESEARCH_LOG_SCORING',
     ('score-atlanta-20260101T000000Z-0000',
      'scoring',
      'atlanta',
      'g-2',
      35.71,
      'FAIL:strategy',
      '{"bts": "WEAK", "spec": "WEAK", "land_bank": "N/A", "ground_lease": "WEAK", "flip": '
      '"N/A"}',
      'phase78: composite=35.71 actionability=FAIL:strategy primary_strategy=None S2=None '
      'S4=None S5=None S6=None S8=None S9=5 S10=0')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S1_interstate_proximity (interstate '
      'proximity) unjoined: pending later-phase data wiring (Google Maps API / GIS)',
      'wire Google Maps API / GIS for parcel_id=g-2')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S2_parcel_geometry (parcel geometry) '
      'unjoined: pending later-phase data wiring (PostGIS (parcels.geometry))',
      'wire PostGIS (parcels.geometry) for parcel_id=g-2')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S3_topography (topography / grading '
      'cost) unjoined: pending later-phase data wiring (USGS 3DEP LiDAR)',
      'wire USGS 3DEP LiDAR for parcel_id=g-2')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S4_submarket_vacancy (submarket vacancy) '
      'unjoined: pending later-phase data wiring (CoStar)',
      'wire CoStar for parcel_id=g-2')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S5_submarket_absorption (submarket net '
      'absorption (T12)) unjoined: pending later-phase data wiring (CoStar)',
      'wire CoStar for parcel_id=g-2')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S6_competing_pipeline (competing '
      'pipeline) unjoined: pending later-phase data wiring (CoStar / Dodge Data)',
      'wire CoStar / Dodge Data for parcel_id=g-2')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S7_labor_pool (labor pool density) '
      'unjoined: pending later-phase data wiring (Census LODES / OnTheMap)',
      'wire Census LODES / OnTheMap for parcel_id=g-2')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S8_land_basis (land basis ($/acre)) '
      'unjoined: pending later-phase data wiring (CoStar Land / assessor comps)',
      'wire CoStar Land / assessor comps for parcel_id=g-2')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S11_rail_adjacency (rail adjacency) '
      'unjoined: pending later-phase data wiring (Class I railroad maps)',
      'wire Class I railroad maps for parcel_id=g-2')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-2',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S12_demand_generators (proximity to '
      'demand generators) unjoined: pending later-phase data wiring (GIS / facility '
      'locations)',
      'wire GIS / facility locations for parcel_id=g-2')),
    ('_SQL_INSERT_PARCEL_SCORE',
     ('g-3',
      47.33,
      0.5833333333333334,
      'FAIL:deal_killer',
      '{"deal_killer": "actionability_block: actionability_block: environmental deal '
      'killer"}',
      '{"S1_interstate_proximity": null, "S2_parcel_geometry": 4, "S3_topography": null, '
      '"S4_submarket_vacancy": 8, "S5_submarket_absorption": 7, "S6_competing_pipeline": '
      '7, "S7_labor_pool": null, "S8_land_basis": 0, "S9_entitlement_complexity": 5, '
      '"S10_incentives": 4, "S11_rail_adjacency": null, "S12_demand_generators": null}',
      '{"bts": "MODERATE", "spec": "MODERATE", "land_bank": "N/A", "ground_lease": "WEAK", '
      '"flip": "N/A"}',
      'bts',
      'phase78: composite=47.33 actionability=FAIL:deal_killer primary_strategy=bts S2=4 '
      'S4=8 S5=7 S6=7 S8=0 S9=5 S10=4',
      'golden-run',
      'exp-golden')),
    ('_SQL_INSERT_RESEARCH_LOG_SCORING',
     ('score-atlanta-20260101T000000Z-0000',
      'scoring',
      'atlanta',
      'g-3',
      47.33,
      'FAIL:deal_killer',
      '{"bts": "MODERATE", "spec": "MODERATE", "land_bank": "N/A", "ground_lease": "WEAK", '
      '"flip": "N/A"}',
      'phase78: composite=47.33 actionability=FAIL:deal_killer primary_strategy=bts S2=4 '
      'S4=8 S5=7 S6=7 S8=0 S9=5 S10=4')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-3',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S1_interstate_proximity (interstate '
      'proximity) unjoined: pending later-phase data wiring (Google Maps API / GIS)',
      'wire Google Maps API / GIS for parcel_id=g-3')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-3',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S3_topography (topography / grading '
      'cost) unjoined: pending later-phase data wiring (USGS 3DEP LiDAR)',
      'wire USGS 3DEP LiDAR for parcel_id=g-3')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-3',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S7_labor_pool (labor pool density) '
      'unjoined: pending later-phase data wiring (Census LODES / OnTheMap)',
      'wire Census LODES / OnTheMap for parcel_id=g-3')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-3',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S11_rail_adjacency (rail adjacency) '
      'unjoined: pending later-phase data wiring (Class I railroad maps)',
      'wire Class I railroad maps for parcel_id=g-3')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-3',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S12_demand_generators (proximity to '
      'demand generators) unjoined: pending later-phase data wiring (GIS / facility '
      'locations)',
      'wire GIS / facility locations for parcel_id=g-3')),
    ('_SQL_INSERT_FLAG',
     ('data_gap',
      'g-3',
      'atlanta',
      'cycle=score-atlanta-20260101T000000Z-0000; S6 approximated at submarket grain; '
      'program.md spec is 5-mi radius',
      'Phase 11+: implement radius-search facility for S6')),
]


class TestScoringFiniteOutputUnchanged(unittest.TestCase):
    """G10: finite scoring output is byte-identical to base 29c051e."""

    def test_finite_writes_match_base_byte_for_byte(self) -> None:
        parcels, geometry, blocks = _golden_scenario()
        conn = PgFake(_scoring_responder(parcels, geometry, blocks))
        summary = _run_scoring(conn)
        self.assertEqual([p["status"] for p in summary["parcels"]], ["scored"] * 3)
        self.assertEqual(_writes(conn), _GOLDEN_FINITE_WRITES)


# ---------------------------------------------------------------------------
# _process_parcel (discovery) — all three per-parcel handlers
# ---------------------------------------------------------------------------
_DISCOVERY_PARAMS = {
    "hard_filters": {"acreage_min": 5, "acreage_max": 50},
    "owner_classification": {
        "trust_keywords": ["TRUST"], "estate_keywords": ["ESTATE"],
        "llc_keywords": ["LLC"], "corporate_keywords": ["INC"],
        "government_keywords": ["COUNTY"],
    },
}
_DISCOVERY_MAPPING = {
    "parcel_id": "ParcelID", "owner_name": "Owner", "acreage": "LandAcres",
    "site_address": "Address", "total_value": "TotAssess",
}


def _feature(raw_id: str | None, acres: float = 14.7, lng: float = -84.554) -> dict[str, Any]:
    ring = [[lng - 0.001, 33.553], [lng + 0.001, 33.553], [lng + 0.001, 33.555],
            [lng - 0.001, 33.555], [lng - 0.001, 33.553]]
    attrs = {"Owner": "TEST HOLDINGS LLC", "LandAcres": acres, "Address": "1 TEST RD",
             "TotAssess": 185000}
    if raw_id is not None:
        attrs["ParcelID"] = raw_id
    return {"attributes": attrs, "geometry": {"rings": [ring]}}


def _discover(conn: PgFake, features: list[dict[str, Any]]) -> list[str]:
    """Same shape as run_discovery_cycle: collision SELECT, blocks, one commit."""
    research._count_log_rows(conn, "disco-fulton-x")
    statuses = [
        research._process_parcel(
            feat, conn, "disco-fulton-x", "south_fulton_campbellton", "atlanta",
            _DISCOVERY_MAPPING, _DISCOVERY_PARAMS["owner_classification"],
            _DISCOVERY_PARAMS, raw_response_path="/nonexistent/cache.json",
        )
        for feat in features
    ]
    conn.commit()
    return statuses


def _upserted(conn: PgFake) -> list[str]:
    return [p[0] for p in _committed_sql(conn, research._SQL_UPSERT_PARCEL)]


class TestProcessParcelIsolation(unittest.TestCase):
    """(b) a failing feature drops only itself, in each of the three handlers."""

    def test_discovery_block_failure_is_isolated(self) -> None:
        def fail(sql: str, params: tuple) -> BaseException | None:
            if sql == research._SQL_UPSERT_PARCEL and params[0] == "fulton-B":
                return FakeServerError("deadlock detected")
            return None

        conn = PgFake(lambda s, p: (0,), fail)
        statuses = _discover(conn, [_feature("A"), _feature("B", lng=-84.55),
                                    _feature("C", lng=-84.546)])
        self.assertEqual(statuses, ["discovery", "skip_unmappable", "discovery"])
        self.assertEqual(_upserted(conn), ["fulton-A", "fulton-C"])
        self.assertEqual(conn.rollback_calls, 0)
        self.assertEqual([e[0] for e in conn.events], ["commit"])

    def test_unmappable_log_failure_is_isolated(self) -> None:
        def fail(sql: str, params: tuple) -> BaseException | None:
            if sql == research._SQL_INSERT_RESEARCH_LOG and "unmappable" in params[4]:
                return FakeServerError("deadlock detected")
            return None

        conn = PgFake(lambda s, p: (0,), fail)
        statuses = _discover(conn, [_feature("A"), _feature(None), _feature("C", lng=-84.546)])
        self.assertEqual(statuses, ["discovery", "skip_unmappable", "discovery"])
        self.assertEqual(_upserted(conn), ["fulton-A", "fulton-C"])
        self.assertEqual(conn.rollback_calls, 0)

    def test_rejection_log_failure_is_isolated(self) -> None:
        def fail(sql: str, params: tuple) -> BaseException | None:
            if sql == research._SQL_INSERT_RESEARCH_LOG and params[1] == "rejection":
                return FakeServerError("deadlock detected")
            return None

        conn = PgFake(lambda s, p: (0,), fail)
        statuses = _discover(conn, [_feature("A"), _feature("R", acres=1.0),
                                    _feature("C", lng=-84.546)])
        self.assertEqual(statuses, ["discovery", "rejection_h2", "discovery"])
        self.assertEqual(_upserted(conn), ["fulton-A", "fulton-C"])
        self.assertEqual(conn.rollback_calls, 0)

    def _process_one(self, conn: PgFake, feature: dict[str, Any]) -> str:
        research._count_log_rows(conn, "disco-fulton-x")  # opens the outer txn
        return research._process_parcel(
            feature, conn, "disco-fulton-x", "south_fulton_campbellton", "atlanta",
            _DISCOVERY_MAPPING, _DISCOVERY_PARAMS["owner_classification"],
            _DISCOVERY_PARAMS, raw_response_path="/nonexistent/cache.json",
        )

    def test_broken_connection_raises_instead_of_skipping(self) -> None:
        for sql_hit, feature in (
            (research._SQL_UPSERT_PARCEL, _feature("B")),
            (research._SQL_INSERT_RESEARCH_LOG, _feature(None)),
            (research._SQL_INSERT_RESEARCH_LOG, _feature("R", acres=1.0)),
        ):
            with self.subTest(feature=feature["attributes"].get("ParcelID")):
                conn = PgFake(lambda s, p: (0,))

                def fail(sql: str, params: tuple, conn=conn, sql_hit=sql_hit):
                    if sql == sql_hit:
                        conn.broken = True
                        return FakeServerError("server closed the connection unexpectedly")
                    return None

                conn.fail_when = fail
                with self.assertRaises(FakeServerError):
                    self._process_one(conn, feature)

    def test_aborted_outer_transaction_raises_without_rollback(self) -> None:
        conn = PgFake(lambda s, p: (0,))

        def fail(sql: str, params: tuple) -> BaseException | None:
            if sql == research._SQL_UPSERT_PARCEL:
                conn.fail_next_block_rollback = True
                return FakeServerError("deadlock detected")
            return None

        conn.fail_when = fail
        with self.assertRaises(FakeServerError):
            self._process_one(conn, _feature("B"))
        self.assertEqual(conn.rollback_calls, 0)


# ===========================================================================
# (c) strategy memo latest-row selection
# ===========================================================================
class TestMemoLatestRowSql(unittest.TestCase):
    """(c) one row per parcel, metric tie-break, still unscoped."""

    SQL = reporting._SQL_FETCH_SCORED_PARCELS_FOR_MEMO

    def test_picks_one_row_with_metric_tie_break(self) -> None:
        self.assertNotIn("MAX(scored_at)", self.SQL)
        self.assertIn("ps.score_id = (", self.SQL)
        self.assertIn("ORDER BY scored_at DESC, score_id DESC LIMIT 1", self.SQL)

    def test_stays_unscoped_read_only_and_parameterized(self) -> None:
        self.assertEqual(self.SQL.lstrip().split(None, 1)[0].upper(), "SELECT")
        self.assertNotIn("run_tag", self.SQL)
        self.assertNotIn("{", self.SQL)
        self.assertEqual(self.SQL.count("%s"), 1)
        self.assertTrue(self.SQL.rstrip().endswith(
            "ORDER BY ps.composite_score DESC NULLS LAST, ps.scored_at DESC"
        ))

    def test_projection_unchanged(self) -> None:
        projection = self.SQL.split("FROM parcel_scores ps", 1)[0]
        self.assertEqual(projection.count(","), 14)  # 15 columns


# ===========================================================================
# tests/live_txn_check.py — local-target guard
# ===========================================================================
def _load_live_check() -> Any:
    spec = importlib.util.spec_from_file_location("live_txn_check", LIVE_CHECK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestLiveCheckGuard(unittest.TestCase):
    """The live check refuses anything but a provably local database."""

    @classmethod
    def setUpClass(cls) -> None:
        with mock.patch("psycopg.connect", side_effect=AssertionError("connect at import")):
            cls.live = _load_live_check()

    def _accepts(self, dsn: str, env: dict[str, str] | None = None) -> None:
        self.live.check_dsn_is_local(dsn, env or {})

    def _refuses(self, dsn: str | None, env: dict[str, str] | None = None) -> None:
        with self.assertRaises(self.live.UnsafeTarget):
            self.live.check_dsn_is_local(dsn, env or {})

    def test_accepts_local_targets(self) -> None:
        for dsn in (
            "postgresql://lr:lr_pw@localhost:5432/lr_test",  # CI
            "postgresql://lr:lr_pw@127.0.0.1:55443/lr_test",
            "postgresql://lr:lr_pw@[::1]:5432/lr_test",
            "host=/var/run/postgresql dbname=lr_test",
            "host=localhost hostaddr=127.0.0.1 dbname=lr_test",
            "host=localhost,127.0.0.1 dbname=lr_test",
        ):
            with self.subTest(dsn=dsn):
                self._accepts(dsn)

    def test_refuses_non_local_or_ambiguous_targets(self) -> None:
        for dsn, env in (
            ("postgresql://u:p@db.example.com:5432/prod", {}),
            ("postgresql://u:p@10.0.0.5:5432/prod", {}),
            ("host=localhost,db.example.com dbname=x", {}),
            ("dbname=x", {}),
            ("postgresql:///x", {}),
            ("host=localhost hostaddr=10.0.0.5 dbname=x", {}),
            ("host=localhost dbname=x", {"PGHOSTADDR": "10.0.0.5"}),
            ("service=prod", {}),
            ("host=localhost dbname=x", {"PGSERVICE": "prod"}),
            ("", {}),
            (None, {}),
            ("not a dsn ===", {}),
        ):
            with self.subTest(dsn=dsn, env=env):
                self._refuses(dsn, env)

    def test_explicit_opt_in_allows_non_local(self) -> None:
        self._accepts("postgresql://u:p@db.example.com/prod",
                      {"LIVE_TXN_CHECK_ALLOW_NONLOCAL_DB": "1"})
        self._refuses("postgresql://u:p@db.example.com/prod",
                      {"LIVE_TXN_CHECK_ALLOW_NONLOCAL_DB": "yes"})

    def test_connected_address_is_rechecked(self) -> None:
        def conn(host: str, hostaddr: str) -> Any:
            return mock.Mock(info=mock.Mock(host=host, hostaddr=hostaddr))

        self.live.check_connection_is_local(conn("localhost", "127.0.0.1"), {})
        self.live.check_connection_is_local(conn("localhost", "::1"), {})
        self.live.check_connection_is_local(conn("/var/run/postgresql", ""), {})
        with self.assertRaises(self.live.UnsafeTarget):
            self.live.check_connection_is_local(conn("localhost", "10.1.2.3"), {})

    def test_main_refuses_without_database_url_and_never_connects(self) -> None:
        remote = {"DATABASE_URL": "postgresql://u:p@db.example.com/x"}
        local = {"DATABASE_URL": "postgresql://u:p@localhost/x"}
        with mock.patch("psycopg.connect", side_effect=AssertionError("connected")), \
                mock.patch.object(prepare, "load_dotenv",
                                  side_effect=AssertionError(".env read")):
            self.assertEqual(self.live.main({}), 2)
            self.assertEqual(self.live.main(remote), 2)
            # A local DSN that is not the one the pipeline will read is refused too.
            with mock.patch.dict("os.environ", remote):
                self.assertEqual(self.live.main(local), 2)

    def test_target_description_never_includes_credentials(self) -> None:
        parts = self.live.check_dsn_is_local("postgresql://lr:s3cret@localhost:5432/lr_test", {})
        text = self.live.describe_target(parts)
        self.assertNotIn("s3cret", text)
        self.assertNotIn("lr:", text)


if __name__ == "__main__":
    unittest.main()
