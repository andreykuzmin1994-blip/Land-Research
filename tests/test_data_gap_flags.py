"""Idempotent data_gap flags (reviews/20_next_improvements/02_*).

A parcel's data_gap flag is written only when no OPEN data_gap flag with the
same parcel and description exists. Offline (SR-6): a small fake connection
answers by SQL statement. The live round trip of the description-stripping
SQL is checked in tests/test_postgis_smoke.py (step 4b).
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any
from unittest import mock

import pipeline_common
import prepare
import research


INSERT_FLAG = pipeline_common._SQL_INSERT_FLAG


class _Cursor:
    def __init__(self, conn: "_Conn") -> None:
        self.conn = conn
        self._rows: list[tuple] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        params = tuple(params or ())
        self.conn.executed.append((sql, params))
        if self.conn.fail_on is not None and self.conn.fail_on(sql, params):
            raise RuntimeError("injected failure")
        self._rows = list(self.conn.answer(sql, params))

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *a: Any) -> None:
        return None


class _Conn:
    """Answers each statement from ``answers`` (sql -> rows); records all."""

    def __init__(self, answers: dict[str, list[tuple]] | None = None, fail_on=None) -> None:
        self.answers = answers or {}
        self.fail_on = fail_on
        self.executed: list[tuple[str, tuple]] = []
        self.commits = 0

    def answer(self, sql: str, params: tuple) -> list[tuple]:
        return self.answers.get(sql, [])

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self

    def commit(self) -> None:
        self.commits += 1

    def flag_descriptions(self) -> list[str]:
        """Descriptions of inserted flags, without the 'cycle=<id>; ' prefix."""
        return [p[3].split("; ", 1)[1] for s, p in self.executed if s == INSERT_FLAG]


def _scoring_answers(stale_days: int = 40) -> dict[str, list[tuple]]:
    """One parcel with a submarket whose market_context row is stale."""
    as_of = date.today() - timedelta(days=stale_days)
    return {
        research._SQL_FETCH_PARCEL: [(
            "fulton-P1", "atlanta", "atlanta__south_fulton", "GA", 12.0,
            None, None, 400000, -84.55, 33.55,
        )],
        research._SQL_S2_GEOMETRY: [(40000.0, 50000.0, 1.5)],
        research._SQL_LATEST_MARKET_CONTEXT: [(5.4, 1_850_000, 400_000, 0, 7.85, as_of, "costar")],
        research._SQL_SUBMARKET_LAND_MEDIAN: [(0, None)],
        research._SQL_INSERT_PARCEL_SCORE: [(1,)],
    }


def _score(conn: _Conn, cycle_id: str = "score-atlanta-t") -> dict[str, Any]:
    return research.score_parcel(
        "fulton-P1", conn=conn, cycle_id=cycle_id,
        params=prepare.get_parameters(), run_tag="t", experiment_id="exp-t",
    )


class TestFlagDataGapOnce(unittest.TestCase):
    def test_skips_open_key_writes_new_and_none_means_always(self) -> None:
        conn = _Conn()
        open_gaps = {("p1", "old gap")}
        written: list[tuple[str, str]] = []
        research._flag_data_gap_once(conn, "c1", "p1", "atlanta", "old gap", "r", open_gaps, written)
        research._flag_data_gap_once(conn, "c1", "p1", "atlanta", "new gap", "r", open_gaps, written)
        research._flag_data_gap_once(conn, "c1", "p1", "atlanta", "new gap", "r", open_gaps, written)
        research._flag_data_gap_once(conn, "c1", "p2", "atlanta", "old gap", "r", open_gaps, written)
        self.assertEqual(conn.flag_descriptions(), ["new gap", "old gap"])
        self.assertEqual(written, [("p1", "new gap"), ("p2", "old gap")])
        self.assertEqual(open_gaps, {("p1", "old gap")})   # caller adds after commit
        research._flag_data_gap_once(conn, "c1", "p1", "atlanta", "old gap", "r", None, [])
        self.assertEqual(conn.flag_descriptions()[-1], "old gap")

    def test_stored_description_strips_back_to_the_key(self) -> None:
        # The SQL key strips everything up to the first '; '. That only works
        # while _flag prefixes exactly 'cycle=<id>; ' and cycle ids have no '; '.
        conn = _Conn()
        desc = "S8 land-basis median has only 0 comps; extra (< 3 required)"
        cid = research._make_scoring_cycle_id("atlanta")
        pipeline_common._flag(conn, cid, "p1", "atlanta", "data_gap", desc, "r")
        stored = conn.executed[-1][1][3]
        self.assertTrue(stored.startswith(f"cycle={cid}; "))
        self.assertNotIn("; ", cid)
        self.assertEqual(stored[stored.index("; ") + 2:], desc)
        self.assertIn("strpos(description, '; ')", research._SQL_OPEN_DATA_GAP_DESCRIPTION)
        self.assertIn("status = 'open'", research._SQL_OPEN_DATA_GAP_KEYS_FOR_MARKET)
        self.assertIn("flag_type = 'data_gap'", research._SQL_OPEN_DATA_GAP_KEYS_FOR_PARCELS)

    def test_load_returns_keys_and_skips_empty_parcel_list(self) -> None:
        conn = _Conn({research._SQL_OPEN_DATA_GAP_KEYS_FOR_PARCELS: [("p1", "a"), (None, "x")]})
        self.assertEqual(research._load_open_data_gaps(conn, parcel_ids=["p1"]), {("p1", "a")})
        self.assertEqual(research._load_open_data_gaps(conn, parcel_ids=[]), set())
        self.assertEqual(len(conn.executed), 1)


class TestScoreParcelFlags(unittest.TestCase):
    def test_second_scoring_adds_no_flags(self) -> None:
        conn = _Conn(_scoring_answers())
        first = _score(conn)
        self.assertEqual(first["status"], "scored")
        written = conn.flag_descriptions()
        self.assertGreater(len(written), 5)
        # Same parcel, flags now open in the database.
        keys = [("fulton-P1", d) for d in written]
        answers = _scoring_answers()
        answers[research._SQL_OPEN_DATA_GAP_KEYS_FOR_PARCELS] = keys
        conn2 = _Conn(answers)
        second = _score(conn2)
        self.assertEqual(second["status"], "scored")
        self.assertEqual(conn2.flag_descriptions(), [])
        # Scores are unaffected by the flag dedup.
        for k in ("composite_score", "confidence_score", "actionability", "sub_scores"):
            self.assertEqual(first[k], second[k])

    def test_only_the_missing_flag_is_written(self) -> None:
        conn = _Conn(_scoring_answers())
        _score(conn)
        written = conn.flag_descriptions()
        answers = _scoring_answers()
        answers[research._SQL_OPEN_DATA_GAP_KEYS_FOR_PARCELS] = [
            ("fulton-P1", d) for d in written[1:]
        ]
        conn2 = _Conn(answers)
        _score(conn2)
        self.assertEqual(conn2.flag_descriptions(), written[:1])

    def test_staleness_description_does_not_change_day_to_day(self) -> None:
        import datetime as dt
        answers = _scoring_answers()
        as_of = date(2026, 1, 1)
        row = answers[research._SQL_LATEST_MARKET_CONTEXT][0]
        answers[research._SQL_LATEST_MARKET_CONTEXT] = [row[:5] + (as_of,) + row[6:]]
        descs = []
        for fixed_day in (date(2026, 3, 1), date(2026, 3, 2)):
            class _Today(dt.date):
                @classmethod
                def today(cls, _t=fixed_day):
                    return _t
            conn = _Conn(answers)
            with mock.patch.object(dt, "date", _Today):
                _score(conn)
            descs.append([d for d in conn.flag_descriptions() if d.startswith("market_context")])
        self.assertEqual(len(descs[0]), 1)
        self.assertEqual(descs[0], descs[1])
        self.assertIn("older than 30d", descs[0][0])
        self.assertIn("as_of=2026-01-01", descs[0][0])

    def test_registered_cycle_set_is_used_and_updated(self) -> None:
        conn = _Conn(_scoring_answers())
        cid = "score-atlanta-reg"
        gaps: set[tuple[str, str]] = set()
        with mock.patch.dict(research._OPEN_DATA_GAPS_BY_CYCLE, {cid: gaps}):
            _score(conn, cid)
            n = len(conn.flag_descriptions())
            _score(conn, cid)                      # same cycle, same parcel
        self.assertEqual(len(conn.flag_descriptions()), n)
        self.assertEqual(len(gaps), n)
        # The registered set replaced the per-parcel read.
        self.assertFalse(any(s == research._SQL_OPEN_DATA_GAP_KEYS_FOR_PARCELS
                             for s, _ in conn.executed))

    def test_failed_transaction_does_not_mark_keys_present(self) -> None:
        cid = "score-atlanta-fail"
        gaps: set[tuple[str, str]] = set()
        flag_inserts: list[int] = []

        def fail_on(sql: str, params: tuple) -> bool:
            # Fail the SECOND flag insert: one flag was already written
            # inside the transaction that then rolls back.
            if sql == INSERT_FLAG:
                flag_inserts.append(1)
                return len(flag_inserts) == 2
            return False

        conn = _Conn(_scoring_answers(), fail_on=fail_on)
        with mock.patch.dict(research._OPEN_DATA_GAPS_BY_CYCLE, {cid: gaps}), \
                mock.patch.object(research, "_abort_cycle_after_failed_block", return_value=False):
            result = _score(conn, cid)
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(flag_inserts), 2)
        self.assertEqual(gaps, set())


class TestCycleRegistry(unittest.TestCase):
    def test_scoring_cycle_registers_then_removes_even_on_error(self) -> None:
        conn = _Conn({
            research._SQL_COUNT_LOG_FOR_SCORING_CYCLE: [(0,)],
            research._SQL_LIST_PARCELS_FOR_SCORING_RUN_SCOPED: [("fulton-P1",)],
        })
        seen: list[bool] = []

        def boom(pid, **kw):
            seen.append(kw["cycle_id"] in research._OPEN_DATA_GAPS_BY_CYCLE)
            raise RuntimeError("abort")

        @contextmanager
        def _ctx():
            yield conn

        with mock.patch.object(prepare, "get_connection", _ctx), \
                mock.patch.object(prepare, "verify_parameters_unchanged"), \
                mock.patch.object(research, "score_parcel", boom):
            with self.assertRaises(RuntimeError):
                research.run_scoring_cycle("atlanta", run_tag="t", experiment_id="e")
        self.assertEqual(seen, [True])
        self.assertFalse(any(k.startswith("score-") for k in research._OPEN_DATA_GAPS_BY_CYCLE))
        reads = [p for s, p in conn.executed if s == research._SQL_OPEN_DATA_GAP_KEYS_FOR_PARCELS]
        self.assertEqual(reads, [(["fulton-P1"],)])


if __name__ == "__main__":
    unittest.main()
