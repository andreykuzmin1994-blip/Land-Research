"""Tier-2 runner-ratchet fixes (between-runs change to runner.py + Makefile).

(a) the keep-or-revert tie-break decides at the TSV's stored precision, so
    a TSV-seeded anchor (fresh process, ``make loop MAX=1``) and an
    in-memory anchor (same process) always agree;
(b) ``verify_setup`` reports a missing DSN as a 'fail' check instead of
    letting prepare's ``SystemExit`` escape;
(c) the CoStar freshness check judges the contract's filename date and
    counts exports costar_ingest already moved to ``ARCHIVED/<type>/``;
(d) the 90-minute experiment budget is enforced in-process (runner-owned
    SIGALRM raising a BaseException the sandbox cannot swallow), with the
    runner-measured after-the-fact relabel kept as the backstop;
(e) Setup Step 6: the baseline is recorded behind verify + the loop lock,
    and ``make loop``/``loop-bg``/``daily`` only bootstrap past a missing
    baseline on an explicit command-line ``CONFIRM=1``;
(g) follow-ups (2026-10-29): a failed purge is retried whenever the DB
    check passes, on every exit, and rebuilt from the TSV by the next
    process; descriptions keep their run=/exp= markers inside the cap and
    report the real discovery counts; make log/orient/status work without
    ``column``; mirror-restore refuses in one line.

Hermetic per SR-6: the TSV and the loop lock live in tempdirs, the mirror
is off (``import tests``), and every path that could reach a database —
verify_setup, _git_head_commit, _purge_experiment_scores, dsn_available,
get_connection — is patched. (f) ``make db-stats`` is verified against a
real local PostGIS only; fake cursors return None from execute() and would
pass the broken one-liner.
"""

from __future__ import annotations

import tests  # noqa: F401 — force the package kill-switch guard to run
              # even under top-level discovery (see tests/__init__.py)

import io
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import costar_ingest
import prepare
import runner

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAKEFILE = _REPO_ROOT / "Makefile"

_SETUP_OK = {
    "status": "ok", "branch": "autoresearch/test", "tag": "test",
    "is_autoresearch_branch": True, "checks": {},
}


def _ok_result(metric: int, confidence: float, **extra: Any) -> dict[str, Any]:
    out = {
        "market": "atlanta", "status": "ok", "metric": metric,
        "confidence": confidence, "api_calls": 0, "wall_clock_min": 0.1,
        "sub_summaries": {}, "run_tag": "test",
    }
    out.update(extra)
    return out


class _TsvLoopBase(unittest.TestCase):
    """Tempdir TSV + lock, restored env, and a recorder for purges."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_env = {
            k: os.environ.get(k)
            for k in ("EXPERIMENT_LOG_PATH", "EXPERIMENT_LOOP_LOCK_PATH")
        }
        self.addCleanup(self._restore_env)
        self._point_at(Path(self.tmp.name) / "default")
        self.purged: list[tuple[str, str, str]] = []

    def _restore_env(self) -> None:
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _point_at(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        self.tsv_path = directory / "experiment_log.tsv"
        self.lock_path = directory / ".lock"
        os.environ["EXPERIMENT_LOG_PATH"] = str(self.tsv_path)
        os.environ["EXPERIMENT_LOOP_LOCK_PATH"] = str(self.lock_path)
        return self.tsv_path

    def _seed(self, status: str, metric: int, confidence: float,
              description: str = "baseline | market=atlanta | run=test") -> None:
        runner.append_experiment_log_row({
            "commit": "0000000", "metric": metric, "confidence": confidence,
            "api_calls": 0, "wall_clock_min": 0.0, "status": status,
            "description": description,
        }, path=self.tsv_path)

    def _record_purge(self, experiment_id: str, market: str, decision: str) -> int:
        self.purged.append((experiment_id, market, decision))
        return 0

    def _run_loop(self, results, max_iterations: int, **kw: Any) -> dict:
        """Run the real loop with a fake evaluate that stamps the
        runner-supplied experiment_id onto each canned result."""
        it = iter(results)

        def fake_evaluate(market, **ekw):
            out = dict(next(it))
            out.setdefault("experiment_id", ekw.get("experiment_id"))
            return out

        with mock.patch.object(runner, "verify_setup", return_value=dict(_SETUP_OK)), \
             mock.patch.object(runner, "evaluate", fake_evaluate), \
             mock.patch.object(runner, "_purge_experiment_scores", self._record_purge), \
             mock.patch.object(runner, "_git_head_commit", return_value="abcdef0"):
            return runner.experiment_loop("atlanta", max_iterations=max_iterations, **kw)

    def _rows(self) -> list[dict[str, str]]:
        return runner.read_experiment_log(self.tsv_path)


# ===========================================================================
# (a) Decision precision: TSV anchor == in-memory anchor
# ===========================================================================
class TestDecisionPrecision(_TsvLoopBase):

    def _decide_tsv_anchor(self, k_dir: str, prior_conf: float,
                           new_conf: float) -> str:
        """Fresh-process path: the anchor is read back from the TSV."""
        self._point_at(Path(self.tmp.name) / k_dir)
        self._seed("baseline", 5, prior_conf)
        self._run_loop([_ok_result(5, new_conf)], max_iterations=1)
        return self._rows()[-1]["status"]

    def _decide_memory_anchor(self, k_dir: str, prior_conf: float,
                              new_conf: float) -> str:
        """Same-process path: iteration 1 keeps (anchor appended to the
        in-memory rows), iteration 2 is decided against that anchor."""
        self._point_at(Path(self.tmp.name) / k_dir)
        self._seed("baseline", 4, 0.0)
        self._run_loop(
            [_ok_result(5, prior_conf), _ok_result(5, new_conf)],
            max_iterations=2,
        )
        rows = self._rows()
        self.assertEqual(rows[1]["status"], "keep")
        return rows[2]["status"]

    def test_identical_rerun_discards_for_every_twelfth_remainder(self) -> None:
        # k = 1, 4, 7, 10 round DOWN at .2f (e.g. 49/12 = 4.0833 -> "4.08"),
        # which used to turn an identical re-run into a 'keep' against the
        # TSV anchor while the in-memory anchor said 'discard'.
        for k in range(12):
            confidence = (48 + k) / 12
            with self.subTest(k=k, confidence=confidence):
                via_tsv = self._decide_tsv_anchor(f"tsv{k}", confidence, confidence)
                via_mem = self._decide_memory_anchor(f"mem{k}", confidence, confidence)
                self.assertEqual(via_tsv, via_mem)
                self.assertEqual(via_tsv, "discard")

    def test_genuine_gain_keeps_on_both_paths(self) -> None:
        prior, new = 49 / 12, 4.0951  # "4.08" -> "4.10"
        self.assertEqual(self._decide_tsv_anchor("g1", prior, new), "keep")
        self.assertEqual(self._decide_memory_anchor("g2", prior, new), "keep")

    def test_sub_precision_gain_ties_and_discards_on_both_paths(self) -> None:
        # Accepted behaviour change: a gain that does not move the stored
        # .2f value is a tie, and a tie reverts (simplicity criterion).
        prior, new = 49 / 12, 4.0849  # both "4.08"
        self.assertEqual(self._decide_tsv_anchor("s1", prior, new), "discard")
        self.assertEqual(self._decide_memory_anchor("s2", prior, new), "discard")

    def test_pure_decision_compares_at_tsv_precision(self) -> None:
        decide = runner.apply_keep_or_revert_decision
        for prior, new, expected in (
            (4.08, 49 / 12, "discard"),      # TSV-read prior vs full new
            (49 / 12, 49 / 12, "discard"),
            (49 / 12, 4.0849, "discard"),
            (49 / 12, 4.0951, "keep"),
            (2.0, 1.99, "discard"),
            (2.0, 2.0 + 1e-12, "discard"),   # R-715 ULP noise still ties
        ):
            with self.subTest(prior=prior, new=new):
                self.assertEqual(
                    decide(prior_metric=5, prior_confidence=prior,
                           new_metric=5, new_confidence=new, status="ok"),
                    expected,
                )

    def test_writer_confidence_strings_unchanged(self) -> None:
        # The TSV byte format is part of the contract (mirror backfill
        # dedups on exact 7-column values): .2f, nothing else.
        cases = [
            (0.0, "0.00"), (49 / 12, "4.08"), (4.0951, "4.10"),
            (2.675, "2.67"), (1.005, "1.00"), (0.005, "0.01"),
            (12.345, "12.35"), (5.0, "5.00"),
        ]
        for value, text in cases:
            self.assertEqual(runner._format_confidence(value), text)
            self.assertEqual(
                runner._validate_log_row({
                    "commit": "abcdef0", "metric": 1, "confidence": value,
                    "status": "keep", "description": "d",
                })["confidence"],
                text,
            )
        self._seed("keep", 5, 49 / 12, "d")
        self.assertEqual(
            self.tsv_path.read_bytes(),
            b"commit\tmetric\tconfidence\tapi_calls\twall_clock_min\tstatus\t"
            b"description\n0000000\t5\t4.08\t0\t0.0\tkeep\td\n",
        )

    def test_tsv_replay_reproduces_every_status(self) -> None:
        self._seed("baseline", 4, 0.0)
        confidences = [49 / 12, 4.0849, 4.0951, 4.0951 + 0.004, 4.17, 52 / 12,
                       52 / 12 + 0.003, 55 / 12, 4.58]
        results = [_ok_result(5, c) for c in confidences]
        results.insert(3, _ok_result(4, 9.0))                   # metric drop
        results.insert(5, dict(_ok_result(0, 0.0), status="crash",
                               error="boom"))
        self._run_loop(results, max_iterations=len(results))
        rows = self._rows()
        self.assertEqual(len(rows), len(results) + 1)

        replayed: list[dict[str, str]] = []
        for row in rows:
            if row["status"] in {"keep", "discard", "crash"}:
                prior = runner._last_baseline_or_keep(replayed, run_tag="test")
                decision = runner.apply_keep_or_revert_decision(
                    prior_metric=int(prior["metric"]),
                    prior_confidence=float(prior["confidence"]),
                    new_metric=int(row["metric"]),
                    new_confidence=float(row["confidence"]),
                    status="crash" if row["status"] == "crash" else "ok",
                )
                self.assertEqual(decision, row["status"], row)
            replayed.append(row)

    def test_long_description_keeps_its_markers_on_both_paths(self) -> None:
        # Thirty counties of discovery counts (the real connector shape)
        # overflow the 200-char cap. The writer's truncation used to cut
        # the trailing run= token, so the keep stopped anchoring THIS run.
        # The composer drops counters instead: the keep still anchors, and
        # the in-memory anchor (the validated row) and the TSV anchor
        # decide alike.
        per_county = {
            f"county{i:02d}": {
                "aborted": False, "per_corridor": {},
                "totals": {"discovery": i, "rejection": 0, "unmappable": 0},
            }
            for i in range(30)
        }
        long_keep = _ok_result(
            7, 1.0, sub_summaries={
                "discovery": {"aborted": False, "per_county": per_county},
            },
        )
        self._point_at(Path(self.tmp.name) / "mem")
        self._seed("baseline", 5, 1.0)
        self._run_loop([long_keep, _ok_result(6, 1.0)], max_iterations=2)
        mem_rows = self._rows()
        self.assertEqual(mem_rows[1]["status"], "keep")
        description = mem_rows[1]["description"]
        self.assertLessEqual(len(description), runner._TSV_DESCRIPTION_MAX_LEN)
        self.assertIn("discovery_county00=0", description)
        self.assertEqual(runner._row_run_tag(mem_rows[1]), "test")
        tag, experiment_id = runner._parse_markers_from_description(description)
        self.assertEqual(tag, "test")
        self.assertIsNotNone(experiment_id)
        via_mem = mem_rows[2]["status"]
        self.assertEqual(via_mem, "discard")  # 6 against the keep's 7

        self._point_at(Path(self.tmp.name) / "tsv")
        self._seed("baseline", 5, 1.0)
        keep = mem_rows[1]
        runner.append_experiment_log_row({
            "commit": keep["commit"], "metric": int(keep["metric"]),
            "confidence": float(keep["confidence"]),
            "api_calls": int(keep["api_calls"]),
            "wall_clock_min": float(keep["wall_clock_min"]),
            "status": keep["status"], "description": keep["description"],
        }, path=self.tsv_path)
        self._run_loop([_ok_result(6, 1.0)], max_iterations=1)
        via_tsv = self._rows()[-1]["status"]

        self.assertEqual(via_mem, via_tsv)


# ===========================================================================
# (b) DSN-less verify_setup returns 'fail' instead of exiting
# ===========================================================================
_FORBIDDEN_CONNECTS: list[str] = []


def _forbidden_get_connection():
    # Recorded, not just raised: the pre-fix check swallowed any Exception.
    _FORBIDDEN_CONNECTS.append("connect")
    raise AssertionError("get_connection must not be called in this test")


class _FakeCursor:
    """Answers the two setup-check queries: PostGIS version, bbox count."""

    def __init__(self):
        self._sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sql = sql

    def fetchone(self):
        return ("3.4 USE_GEOS=1",) if "POSTGIS_VERSION" in self._sql else (2,)


class _FakeConn:
    def cursor(self):
        return _FakeCursor()


class TestDbCheckWithoutDsn(_TsvLoopBase):

    def setUp(self) -> None:
        super().setUp()
        _FORBIDDEN_CONNECTS.clear()
        self.addCleanup(lambda: self.assertEqual(_FORBIDDEN_CONNECTS, []))

    def _patch_other_checks(self):
        return [
            mock.patch.object(runner, "_git_current_branch",
                              return_value="autoresearch/test"),
            mock.patch.object(runner, "_check_harness_for_market",
                              return_value={"status": "ok", "per_county": {}}),
            mock.patch.object(runner, "_check_costar_freshness",
                              return_value={"status": "ok", "fresh_files": 1}),
        ]

    def test_missing_dsn_is_a_fail_check_not_an_exit(self) -> None:
        with mock.patch.object(prepare, "dsn_available", return_value=False), \
             mock.patch.object(prepare, "get_connection", _forbidden_get_connection):
            out = runner._check_db_connection()
        self.assertEqual(out["status"], "fail")
        self.assertIn("DATABASE_URL", out["error"])

    def test_verify_setup_returns_serializable_fail(self) -> None:
        patches = self._patch_other_checks()
        with mock.patch.object(prepare, "dsn_available", return_value=False), \
             mock.patch.object(prepare, "get_connection", _forbidden_get_connection), \
             patches[0], patches[1], patches[2]:
            out = runner.verify_setup("atlanta")
        self.assertEqual(out["status"], "fail")
        self.assertEqual(out["checks"]["db"]["status"], "fail")
        self.assertEqual(out["checks"]["corridor_bbox"]["status"], "skipped")
        json.dumps(out)  # make status / make verify print this

    def test_system_exit_from_get_connection_is_a_fail_check(self) -> None:
        @contextmanager
        def exiting_connection():
            raise SystemExit(prepare.EXIT_DATABASE_URL_MISSING)
            yield  # pragma: no cover

        with mock.patch.object(prepare, "dsn_available", return_value=True), \
             mock.patch.object(prepare, "get_connection", exiting_connection):
            out = runner._check_db_connection()
        self.assertEqual(out["status"], "fail")

    def test_keyboard_interrupt_still_propagates(self) -> None:
        @contextmanager
        def interrupted_connection():
            raise KeyboardInterrupt
            yield  # pragma: no cover

        with mock.patch.object(prepare, "dsn_available", return_value=True), \
             mock.patch.object(prepare, "get_connection", interrupted_connection):
            with self.assertRaises(KeyboardInterrupt):
                runner._check_db_connection()

    def test_loop_without_dsn_raises_setup_error(self) -> None:
        patches = self._patch_other_checks()
        with mock.patch.object(prepare, "dsn_available", return_value=False), \
             mock.patch.object(prepare, "get_connection", _forbidden_get_connection), \
             patches[0], patches[1], patches[2]:
            with self.assertRaises(runner.SetupError):
                runner.experiment_loop("atlanta", max_iterations=1)

    def test_dsn_vanishing_mid_loop_trips_the_infra_breaker(self) -> None:
        self._seed("baseline", 5, 1.0)
        # Startup verify sees the DSN; every per-iteration verify does not.
        dsn = mock.Mock(side_effect=[True] + [False] * 10)

        @contextmanager
        def fake_connection():
            yield _FakeConn()

        patches = self._patch_other_checks()
        with mock.patch.object(prepare, "dsn_available", dsn), \
             mock.patch.object(prepare, "get_connection", fake_connection), \
             mock.patch.object(runner, "evaluate",
                               side_effect=AssertionError("evaluate must not run")), \
             mock.patch.object(runner, "_purge_experiment_scores", self._record_purge), \
             mock.patch.object(runner, "_git_head_commit", return_value="abcdef0"), \
             mock.patch.object(runner.time, "sleep") as sleep, \
             patches[0], patches[1], patches[2]:
            summary = runner.experiment_loop("atlanta", max_iterations=10)
        self.assertEqual(summary["iterations"], 0)
        self.assertEqual(sleep.call_count, runner._INFRA_FAILURE_THRESHOLD - 1)
        halt = [r for r in self._rows() if r["status"] == "halt"]
        self.assertEqual(len(halt), 1)
        self.assertIn("infrastructure failure", halt[0]["description"])

    def test_pending_purge_retry_needs_a_usable_db_not_a_passing_verify(self) -> None:
        # A failed purge is not retried against a DB the setup check just
        # declared unusable, but it IS retried as soon as the DB sub-check
        # passes -- even while verify_setup still fails overall on the
        # harness -- and so before the next experiment is evaluated.
        self._seed("baseline", 5, 1.0)
        calls: list[str] = []

        def setup(status: str, db: str, harness: str) -> dict[str, Any]:
            return dict(_SETUP_OK, status=status, checks={
                "db": {"status": db}, "harness": {"status": harness},
            })

        setup_seq = [setup("ok", "ok", "ok"), setup("ok", "ok", "ok"),
                     setup("fail", "fail", "ok"), setup("fail", "ok", "fail"),
                     setup("ok", "ok", "ok")]

        def fake_verify(market):
            calls.append(f"verify:db={setup_seq[0]['checks']['db']['status']}")
            return setup_seq.pop(0)

        def flaky_purge(exp_id, market, decision):
            first = not any(c.startswith(f"purge:{exp_id}") for c in calls)
            calls.append(f"purge:{exp_id}:{'fail' if first else 'ok'}")
            if first:
                raise RuntimeError("db down")
            return 0

        results = iter([_ok_result(4, 1.0, experiment_id="exp-a"),
                        _ok_result(6, 1.0, experiment_id="exp-b")])

        def fake_evaluate(market, **kw):
            out = next(results)
            calls.append(f"evaluate:{out['experiment_id']}")
            return out

        with mock.patch.object(runner, "verify_setup", fake_verify), \
             mock.patch.object(runner, "evaluate", fake_evaluate), \
             mock.patch.object(runner, "_purge_experiment_scores", flaky_purge), \
             mock.patch.object(runner, "_git_head_commit", return_value="abcdef0"), \
             mock.patch.object(runner.time, "sleep"):
            summary = runner.experiment_loop("atlanta", max_iterations=2)
        self.assertEqual(calls, [
            "verify:db=ok", "verify:db=ok", "evaluate:exp-a", "purge:exp-a:fail",
            "verify:db=fail",                       # DB unusable: no retry
            "verify:db=ok", "purge:exp-a:ok",       # harness down, DB ok: retried
            "verify:db=ok", "evaluate:exp-b",       # only then the next experiment
        ])
        self.assertEqual(summary["unpurged_experiment_ids"], [])


# ===========================================================================
# (c) CoStar freshness counts archived exports, by filename date
# ===========================================================================
_NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)


class TestCostarFreshness(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / "costar_exports"
        self.base.mkdir()
        for module in (runner, costar_ingest):
            patcher = mock.patch.object(module, "_COSTAR_BASE_DIR", self.base)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _touch(self, rel: str) -> Path:
        path = self.base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
        return path

    def _check(self) -> dict[str, Any]:
        out = runner._check_costar_freshness(now=_NOW)
        self.assertIn(out["status"], {"ok", "warning"})  # never 'fail'
        json.dumps(out)
        return out

    def test_fresh_intake_file_is_ok(self) -> None:
        self._touch("submarket_stats/submarket_stats_20261012.csv")
        out = self._check()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["fresh_files"], 1)

    def test_fresh_archived_file_is_ok(self) -> None:
        # Name it exactly the way ingestion does (real _archive_destination).
        src = self._touch("submarket_stats/submarket_stats_20261012.csv")
        archived = costar_ingest._archive_file(src)
        self.assertEqual(archived.parent, self.base.resolve() / "ARCHIVED" / "submarket_stats")
        self.assertIsNone(costar_ingest._SUBMARKET_STATS_FILENAME_RE.match(archived.name))
        self.assertFalse(src.exists())
        out = self._check()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["fresh_files"], 1)

    def test_archived_uppercase_extension_is_ok(self) -> None:
        src = self._touch("land_listings/LAND_LISTINGS_20261015.CSV")
        costar_ingest._archive_file(src)
        self.assertEqual(self._check()["status"], "ok")

    def test_failed_exports_do_not_count(self) -> None:
        src = self._touch("submarket_stats/submarket_stats_20261012.csv")
        costar_ingest._fail_file(src, {"error": "schema"})
        out = self._check()
        self.assertEqual(out["status"], "warning")
        self.assertEqual(out["fresh_files"], 0)

    def test_stale_filename_date_is_warning_despite_fresh_mtime(self) -> None:
        self._touch("submarket_stats/submarket_stats_20260801.csv")
        self.assertEqual(self._check()["status"], "warning")

    def test_monthly_name_means_first_of_month(self) -> None:
        self._touch("land_sales_comps/land_sales_comps_202609.csv")  # 09-01: 49d
        self.assertEqual(self._check()["status"], "warning")
        self._touch("land_sales_comps/land_sales_comps_202610.csv")  # 10-01: 19d
        self.assertEqual(self._check()["status"], "ok")

    def test_future_dates_are_rejected_beyond_one_day(self) -> None:
        self._touch("land_listings/land_listings_20261023.csv")
        self.assertEqual(self._check()["status"], "warning")
        self._touch("land_listings/land_listings_20261021.csv")  # clock skew
        self.assertEqual(self._check()["status"], "ok")

    def test_invalid_and_non_matching_names_are_skipped(self) -> None:
        for rel in ("submarket_stats/submarket_stats_20261399.csv",
                    "submarket_stats/submarket_stats_latest.csv",
                    "submarket_stats/notes.txt",
                    "submarket_stats/.submarket_stats_20261012.csv",
                    "ARCHIVED/submarket_stats/submarket_stats_20261399_"
                    "20261012T000000Z_ab12.csv",
                    "tenant_intel/tenant_intel_south_fulton_20261012.csv",
                    "ARCHIVED/submarket_stats_20261012.csv"):
            self._touch(rel)
        out = self._check()
        self.assertEqual(out["status"], "warning")
        self.assertEqual(out["fresh_files"], 0)

    def test_unreadable_directory_is_warning_not_exception(self) -> None:
        self._touch("submarket_stats/submarket_stats_20261012.csv")
        real_iterdir = Path.iterdir

        def guarded(path):
            if path.name == "submarket_stats":
                raise PermissionError(13, "Permission denied", str(path))
            return real_iterdir(path)

        with mock.patch.object(Path, "iterdir", guarded):
            out = self._check()
        self.assertEqual(out["status"], "warning")

    def test_file_where_directory_expected_is_warning(self) -> None:
        self._touch("submarket_stats")  # a FILE named like the type dir
        self.assertEqual(self._check()["status"], "warning")

    def test_missing_base_dir_is_warning(self) -> None:
        shutil.rmtree(self.base)
        self.assertEqual(self._check()["status"], "warning")

    def test_scan_failure_never_raises(self) -> None:
        with mock.patch.object(runner, "_costar_export_date",
                               side_effect=RuntimeError("boom")):
            self._touch("submarket_stats/submarket_stats_20261012.csv")
            self.assertEqual(self._check()["status"], "warning")


# ===========================================================================
# (d) Enforced experiment budget
# ===========================================================================
@unittest.skipUnless(hasattr(signal, "SIGALRM"), "POSIX SIGALRM required")
class _BudgetBase(_TsvLoopBase):
    """A stray SIGALRM is recorded (not fatal) and fails the test; every
    test leaves the timer disarmed and the previous handler in place."""

    def setUp(self) -> None:
        super().setUp()
        self.stray_alarms: list[int] = []

        def stray(signum, frame):
            self.stray_alarms.append(signum)

        self.guard_handler = stray
        self._prev_handler = signal.signal(signal.SIGALRM, stray)
        self.addCleanup(self._disarm)

    def _disarm(self) -> None:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self._prev_handler)

    def assertBudgetDisarmed(self) -> None:
        self.assertIs(signal.getsignal(signal.SIGALRM), self.guard_handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(self.stray_alarms, [])


def _hung_evaluate(swallow: bool = False):
    """Fake evaluate that never finishes on its own within the test's
    patience (bounded at 10s so a regression fails slowly, not forever)."""
    def fake(market, **kw):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if swallow:
                try:
                    time.sleep(0.02)
                except Exception:  # research.py-style per-item swallow
                    continue
            else:
                time.sleep(0.02)
        return _ok_result(99, 99.0, experiment_id=kw.get("experiment_id"))
    return fake


class TestExperimentBudget(_BudgetBase):

    def _loop_once(self, fake_evaluate, budget=1):
        self._seed("baseline", 5, 1.0)
        seen: list[Any] = []

        def recording(market, **kw):
            seen.append(kw.get("experiment_id"))
            return fake_evaluate(market, **kw)

        with mock.patch.object(runner, "_PHASE10_BUDGET_SECONDS", budget), \
             mock.patch.object(runner, "verify_setup", return_value=dict(_SETUP_OK)), \
             mock.patch.object(runner, "evaluate", recording), \
             mock.patch.object(runner, "_purge_experiment_scores", self._record_purge), \
             mock.patch.object(runner, "_git_head_commit", return_value="abcdef0"):
            started = time.monotonic()
            runner.experiment_loop("atlanta", max_iterations=1)
            elapsed = time.monotonic() - started
        return seen, elapsed

    def _assert_timeout_row_and_purge(self, seen) -> None:
        self.assertEqual(len(seen), 1)
        exp_id = seen[0]
        self.assertIsNotNone(exp_id, "runner must pass its experiment_id in")
        row = self._rows()[-1]
        self.assertEqual(row["status"], "timeout")
        self.assertIn(f"exp={exp_id}", row["description"])
        self.assertIn("run=test", row["description"])
        self.assertEqual(self.purged, [(exp_id, "atlanta", "timeout")])

    def test_hung_evaluate_is_interrupted_and_recorded_timeout(self) -> None:
        seen, elapsed = self._loop_once(_hung_evaluate())
        self.assertLess(elapsed, 5)
        self._assert_timeout_row_and_purge(seen)
        self.assertBudgetDisarmed()

    def test_swallowing_evaluate_is_still_interrupted(self) -> None:
        # prepare.BudgetExceeded (a RuntimeError) would be eaten here.
        seen, elapsed = self._loop_once(_hung_evaluate(swallow=True))
        self.assertLess(elapsed, 5)
        self._assert_timeout_row_and_purge(seen)
        self.assertBudgetDisarmed()

    def test_interrupt_escaping_evaluate_is_timeout_not_crash(self) -> None:
        def escaping(market, **kw):
            raise runner._ExperimentBudgetInterrupt("fired in the return tail")

        seen, _ = self._loop_once(escaping, budget=runner._PHASE10_BUDGET_SECONDS)
        self._assert_timeout_row_and_purge(seen)
        self.assertBudgetDisarmed()

    def test_cleanup_error_chained_to_interrupt_is_timeout(self) -> None:
        # e.g. psycopg raising on close after the interrupt hit a query.
        def chained(market, **kw):
            try:
                raise runner._ExperimentBudgetInterrupt("mid-query")
            finally:
                raise RuntimeError("another command is already in progress")

        seen, _ = self._loop_once(chained, budget=runner._PHASE10_BUDGET_SECONDS)
        self._assert_timeout_row_and_purge(seen)

    def test_late_ok_and_late_crash_are_relabelled_timeout(self) -> None:
        for status in ("ok", "crash"):
            with self.subTest(status=status):
                self._point_at(Path(self.tmp.name) / status)
                self.purged.clear()

                def late(market, _status=status, **kw):
                    return dict(
                        _ok_result(9, 9.0, experiment_id=kw.get("experiment_id")),
                        status=_status, error="x",
                        wall_clock_min=runner._PHASE10_BUDGET_SECONDS / 60.0 + 1,
                    )

                seen, _ = self._loop_once(late, budget=runner._PHASE10_BUDGET_SECONDS)
                self._assert_timeout_row_and_purge(seen)

    def test_relabel_uses_runner_measured_elapsed(self) -> None:
        clock = iter([1000.0])

        def fake_monotonic():
            return next(clock, 1000.0 + 5.0)

        fast = mock.Mock(return_value=_ok_result(9, 9.0, experiment_id="exp-x",
                                                 wall_clock_min=0.0))
        with mock.patch.object(runner, "_PHASE10_BUDGET_SECONDS", 5), \
             mock.patch.object(runner, "evaluate", fast), \
             mock.patch.object(runner.time, "monotonic", fake_monotonic):
            result = runner._evaluate_with_budget(
                "atlanta", run_tag="test", experiment_id="exp-x",
            )
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["experiment_id"], "exp-x")
        self.assertBudgetDisarmed()

    def test_budget_is_read_at_call_time_and_clamped(self) -> None:
        calls: list[int] = []
        real_alarm = signal.alarm

        def recording_alarm(seconds):
            calls.append(seconds)
            return real_alarm(seconds)

        fast = mock.Mock(return_value=_ok_result(1, 1.0, experiment_id="e",
                                                 wall_clock_min=0.0))
        with mock.patch.object(runner, "_PHASE10_BUDGET_SECONDS", 0.4), \
             mock.patch.object(runner, "evaluate", fast), \
             mock.patch.object(runner.signal, "alarm", recording_alarm):
            result = runner._evaluate_with_budget("atlanta", run_tag="t",
                                                  experiment_id="e")
        self.assertEqual(result["status"], "ok")
        # Armed with 1 (never alarm(0), which would mean "no alarm"), then
        # only cancellations.
        self.assertEqual(calls[0], 1)
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(set(calls[1:]), {0})
        self.assertBudgetDisarmed()

    def test_hung_baseline_is_timeout_purged_and_not_a_baseline(self) -> None:
        seen: list[Any] = []
        hung = _hung_evaluate()

        def recording(market, **kw):
            seen.append(kw.get("experiment_id"))
            return hung(market, **kw)

        with mock.patch.object(runner, "_PHASE10_BUDGET_SECONDS", 1), \
             mock.patch.object(runner, "evaluate", recording), \
             mock.patch.object(runner, "_purge_experiment_scores", self._record_purge), \
             mock.patch.object(runner, "_git_head_commit", return_value="abcdef0"):
            started = time.monotonic()
            row = runner.run_baseline_experiment("atlanta", run_tag="test")
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5)
        self.assertEqual(row["status"], "timeout")
        self.assertEqual(self.purged, [(seen[0], "atlanta", "timeout")])
        self.assertEqual([r["status"] for r in self._rows()], ["timeout"])
        self.assertBudgetDisarmed()

    def test_off_main_thread_runs_unbudgeted_with_warning(self) -> None:
        outcome: dict[str, Any] = {}
        fast = mock.Mock(return_value=_ok_result(3, 1.0, experiment_id="e"))

        def worker():
            try:
                outcome["result"] = runner._evaluate_with_budget(
                    "atlanta", run_tag="t", experiment_id="e",
                )
            except BaseException as exc:  # noqa: BLE001 — surface anything
                outcome["error"] = exc

        with mock.patch.object(runner, "evaluate", fast), \
             self.assertLogs("research", level="WARNING") as logs:
            t = threading.Thread(target=worker)
            t.start()
            t.join(10)
        self.assertNotIn("error", outcome)
        self.assertEqual(outcome["result"]["status"], "ok")
        fast.assert_called_once()
        self.assertTrue(any("budget" in m for m in logs.output))
        self.assertBudgetDisarmed()

    def test_crashed_bootstrap_baseline_then_first_ok_is_the_baseline(self) -> None:
        # RR-18 (kept, pinned): with confirmed=True the loop carries on past
        # a crashed bootstrap; the first successful (still unmodified-code)
        # iteration becomes the run's baseline.
        results = [dict(_ok_result(0, 0.0), status="crash", error="boom"),
                   _ok_result(5, 1.0)]
        self._run_loop(results, max_iterations=1, confirmed=True)
        self.assertEqual([r["status"] for r in self._rows()], ["crash", "baseline"])

    def test_budget_constant_matches_prepare(self) -> None:
        self.assertEqual(runner._PHASE10_BUDGET_SECONDS,
                         prepare.WALL_CLOCK_BUDGET_SECONDS)


# ===========================================================================
# (e) Setup Step 6 gate
# ===========================================================================
class TestGatedBaseline(_TsvLoopBase):

    def _gated(self, setup=None, **kw):
        setup = dict(_SETUP_OK) if setup is None else setup
        self.evaluate = mock.Mock(return_value=_ok_result(5, 1.0, experiment_id="exp-b"))
        with mock.patch.object(runner, "verify_setup", return_value=setup) as verify, \
             mock.patch.object(runner, "evaluate", self.evaluate), \
             mock.patch.object(runner, "_purge_experiment_scores", self._record_purge), \
             mock.patch.object(runner, "_git_head_commit", return_value="abcdef0"):
            self.verify = verify
            return runner.run_gated_baseline("atlanta", **kw)

    def test_refuses_on_verify_fail(self) -> None:
        with self.assertRaises(runner.SetupError):
            self._gated(dict(_SETUP_OK, status="fail"))
        self.evaluate.assert_not_called()
        self.assertFalse(self.tsv_path.exists())

    def test_refuses_off_an_autoresearch_branch(self) -> None:
        with self.assertRaises(runner.SetupError):
            self._gated(dict(_SETUP_OK, status="warning", tag=None, branch="main"))
        self.evaluate.assert_not_called()

    def test_refuses_while_the_loop_lock_is_held(self) -> None:
        with runner._acquire_loop_lock():
            with self.assertRaises(runner.LoopLockError):
                self._gated()
        self.verify.assert_not_called()
        self.evaluate.assert_not_called()
        self.assertFalse(self.tsv_path.exists())

    def test_refuses_a_second_baseline_for_the_run(self) -> None:
        self._seed("baseline", 5, 1.0)
        with self.assertRaises(runner.SetupError) as cm:
            self._gated()
        self.assertIn("already has a baseline", str(cm.exception))
        self.evaluate.assert_not_called()
        self.assertEqual(len(self._rows()), 1)

    def test_explicit_override_allows_rebaseline(self) -> None:
        self._seed("baseline", 5, 1.0)
        row = self._gated(allow_rebaseline=True)
        self.assertEqual(row["status"], "baseline")

    def test_retry_allowed_after_crash_or_timeout_attempts(self) -> None:
        self._seed("crash", 0, 0.0, "baseline | market=atlanta | run=test | exp=e1")
        self._seed("timeout", 0, 0.0, "baseline | market=atlanta | run=test | exp=e2")
        self._seed("baseline", 9, 9.0, "baseline | market=atlanta | run=old")
        row = self._gated()
        self.assertEqual(row["status"], "baseline")
        self.assertIn("run=test", row["description"])

    def test_proceeds_on_warning_and_threads_the_verified_tag(self) -> None:
        with mock.patch.object(runner, "run_baseline_experiment",
                               return_value={"status": "baseline"}) as baseline:
            self._gated(dict(_SETUP_OK, status="warning"))
        baseline.assert_called_once_with("atlanta", run_tag="test")

    def test_loop_bootstrap_under_the_real_lock_still_works(self) -> None:
        self._run_loop([_ok_result(5, 1.0)], max_iterations=0, confirmed=True)
        self.assertEqual([r["status"] for r in self._rows()], ["baseline"])


class TestHasRunBaseline(_TsvLoopBase):

    CASES = {
        "empty": [],
        "this_run": [("baseline", "baseline | market=atlanta | run=test")],
        "other_run": [("baseline", "baseline | market=atlanta | run=old")],
        "legacy_unstamped": [("baseline", "baseline | market=atlanta")],
        "crash_attempt": [("crash", "baseline | market=atlanta | run=test")],
        "keep_only": [("keep", "market=atlanta | run=test | exp=e")],
        "prefix_tag": [("baseline", "baseline | market=atlanta | run=test2")],
    }

    def test_helper_agrees_with_the_loop_gate(self) -> None:
        for name, rows in self.CASES.items():
            with self.subTest(case=name):
                self._point_at(Path(self.tmp.name) / name)
                for status, desc in rows:
                    self._seed(status, 1, 1.0, desc)
                helper = runner._has_run_baseline(self._rows(), "test")
                with mock.patch.object(runner, "_git_current_branch",
                                       return_value="autoresearch/test"):
                    self.assertEqual(runner.current_run_has_baseline(), helper)
                try:
                    with mock.patch.object(runner, "verify_setup",
                                           return_value=dict(_SETUP_OK)):
                        runner.experiment_loop("atlanta", max_iterations=0)
                    loop_says = True
                except runner.SetupError:
                    loop_says = False
                self.assertEqual(helper, loop_says)
                self.assertEqual(helper, name == "this_run")

    def test_off_branch_has_no_baseline(self) -> None:
        self._seed("baseline", 1, 1.0)
        with mock.patch.object(runner, "_git_current_branch", return_value="main"):
            self.assertFalse(runner.current_run_has_baseline())


class TestCliBaselineGate(unittest.TestCase):

    def _run(self, **patch_kw) -> tuple[int, str, str]:
        import cli
        out, err = io.StringIO(), io.StringIO()
        ns = cli._build_parser().parse_args(["--no-color", "baseline"])
        with mock.patch.object(runner, "run_gated_baseline", **patch_kw) as gated, \
             mock.patch.object(runner, "run_baseline_experiment",
                               side_effect=AssertionError("ungated path")), \
             redirect_stdout(out), redirect_stderr(err):
            rc = cli.cmd_baseline(ns)
        gated.assert_called_once_with("atlanta")
        return rc, out.getvalue(), err.getvalue()

    def test_setup_error_exits_two(self) -> None:
        rc, _, err = self._run(side_effect=runner.SetupError("verify failed"))
        self.assertEqual(rc, 2)
        self.assertIn("verify failed", err)

    def test_lock_held_exits_three(self) -> None:
        rc, _, err = self._run(side_effect=runner.LoopLockError("held"))
        self.assertEqual(rc, 3)

    def test_baseline_row_exits_zero(self) -> None:
        rc, out, _ = self._run(return_value={"status": "baseline", "metric": 5})
        self.assertEqual(rc, 0)

    def test_crash_or_timeout_attempt_exits_nonzero(self) -> None:
        for status in ("crash", "timeout"):
            with self.subTest(status=status):
                rc, _, _ = self._run(return_value={"status": status})
                self.assertEqual(rc, 1)


def _recipe(target: str) -> str:
    """The recipe lines of one Makefile target (static inspection)."""
    lines = _MAKEFILE.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if not inside:
            inside = bool(re.match(rf"^{re.escape(target)}\s*:", line))
            continue
        if line.startswith("\t") or (out and out[-1].endswith("\\")):
            out.append(line)
            continue
        break
    return "\n".join(out)


class TestMakefileGateStatic(unittest.TestCase):

    def test_loop_no_longer_hardcodes_confirmation(self) -> None:
        loop = _recipe("loop")
        self.assertNotIn("confirmed=True", loop)
        self.assertIn("$(CONFIRM_FLAG)", loop)
        text = _MAKEFILE.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"ifeq \(\$\(origin CONFIRM\)\|\$\(strip \$\(CONFIRM\)\),command line\|1\)",
        )

    def test_loop_bg_checks_baseline_before_spawning_tmux(self) -> None:
        recipe = _recipe("loop-bg")
        check = recipe.find("runner.current_run_has_baseline()")
        spawn = recipe.find("tmux new-session")
        self.assertNotEqual(check, -1)
        self.assertNotEqual(spawn, -1)
        self.assertLess(check, spawn)
        # Only the explicit literal is forwarded; inherited MAKEFLAGS (a
        # stale tmux server environment) is cleared.
        self.assertIn("MAKEFLAGS= make loop", recipe)
        self.assertIn("$(CONFIRM_FWD)", recipe[spawn:])

    def test_daily_delegates_to_the_gated_loop_bg(self) -> None:
        daily = _recipe("daily")
        self.assertNotIn("tmux new-session", daily)
        self.assertIn("loop-bg $(CONFIRM_FWD)", daily)

    def test_baseline_uses_the_gated_entry_point(self) -> None:
        baseline = _recipe("baseline")
        self.assertIn("cli.py", baseline)
        self.assertNotIn("run_baseline_experiment", baseline)

    def test_db_stats_no_longer_prints_cursor_reprs(self) -> None:
        self.assertNotIn("execute(q) or", _recipe("db-stats"))


@unittest.skipUnless(shutil.which("make"), "GNU make required")
class TestMakefileDryRun(unittest.TestCase):

    def _dry(self, *args: str, env_extra: dict[str, str] | None = None) -> str:
        env = {k: v for k, v in os.environ.items()
               if k not in {"CONFIRM", "MAKEFLAGS", "MFLAGS", "MAKELEVEL"}}
        env.update(env_extra or {})
        proc = subprocess.run(
            ["make", "-n", "--no-print-directory", "-f", str(_MAKEFILE), *args],
            cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def _line(self, pattern: str, output: str) -> str:
        match = re.search(pattern, output)
        self.assertIsNotNone(match, output)
        return match.group(0)

    def test_only_a_command_line_literal_one_confirms(self) -> None:
        loop_cmd = r"cli\.py --no-color loop[^\n]*"
        self.assertIn("--confirmed",
                      self._line(loop_cmd, self._dry("loop", "CONFIRM=1")))
        for args, env in ((("loop",), None),
                          (("loop", "CONFIRM=0"), None),
                          (("loop", "CONFIRM=yes"), None),
                          (("loop", "CONFIRM=true"), None),
                          (("loop",), {"CONFIRM": "1"})):
            with self.subTest(args=args, env=env):
                line = self._line(loop_cmd, self._dry(*args, env_extra=env))
                self.assertNotIn("--confirmed", line)

    def test_loop_bg_forwards_a_literal_confirm(self) -> None:
        spawn = r"MAKEFLAGS= make loop[^;\n]*"
        confirmed = self._dry("loop-bg", "CONFIRM=1")
        self.assertIn("CONFIRM=1", self._line(spawn, confirmed))
        self.assertIn('[ -z "CONFIRM=1" ]', confirmed)  # pre-flight skipped
        for env in (None, {"CONFIRM": "1"}):
            with self.subTest(env=env):
                unconfirmed = self._dry("loop-bg", env_extra=env)
                self.assertNotIn("CONFIRM", self._line(spawn, unconfirmed))
                self.assertIn('[ -z "" ]', unconfirmed)  # pre-flight runs


# ===========================================================================
# (g) Runner follow-ups: durable pending purges, the description budget, ops
# ===========================================================================
def _xid(n: int) -> str:
    """A real-shaped experiment id (``exp-<UTC stamp>-<6 hex>``)."""
    return f"exp-20261029T{n:06d}Z-{n:06x}"


def _setup(status: str = "ok", db: str = "ok", harness: str = "ok") -> dict[str, Any]:
    """verify_setup result with explicit DB and harness sub-checks."""
    return dict(_SETUP_OK, status=status, checks={
        "db": {"status": db}, "harness": {"status": harness},
    })


_HARNESS_DOWN = ("fail", "ok", "fail")   # e.g. a Fulton GIS outage; DB fine
_DB_DOWN = ("fail", "fail", "ok")

# A realistic psycopg DB-blip crash text (the row whose purge is most likely
# to fail too).
_PSYCOPG_BLIP = (
    "OperationalError: consuming input failed: server closed the connection "
    "unexpectedly\n\tThis probably means the server terminated abnormally\n\t"
    "before or while processing the request."
)


class _ResidueCursor:
    def __init__(self, db: "_ResidueDb") -> None:
        self.db = db
        self.rows: list[tuple[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.db.executes.append((sql, params))
        if sql == getattr(runner, "_SQL_SELECT_EXPERIMENT_RESIDUE", None):
            if self.db.read_fails:
                raise RuntimeError("residue read failed")
            tag, ids = params
            self.rows = [(i,) for i in ids if tag == "test" and i in self.db.residue]

    def fetchall(self):
        return list(self.rows)


class _ResidueDb:
    """Stands in for ``prepare.get_connection``: answers the residue read
    (the purge itself is patched), records every connection, and can play
    a vanished DSN (prepare's ``SystemExit``)."""

    def __init__(self) -> None:
        self.residue: set[str] = set()
        self.read_fails = False
        self.no_dsn = False
        self.executes: list[tuple[str, Any]] = []
        self.connects = 0

    @contextmanager
    def connection(self):
        self.connects += 1
        if self.no_dsn:
            raise SystemExit(prepare.EXIT_DATABASE_URL_MISSING)
        yield self

    def cursor(self):
        return _ResidueCursor(self)

    def residue_reads(self) -> list[Any]:
        sql = getattr(runner, "_SQL_SELECT_EXPERIMENT_RESIDUE", None)
        return [p for s, p in self.executes if s == sql]


class _FollowupBase(_TsvLoopBase):
    """Loop harness that records one event stream: verify, evaluate:<id>,
    purge:<id>:<outcome>, halt. ``self.plan`` scripts purge outcomes in
    call order (default ok): fail = RuntimeError (a DB blip), real = the
    REAL purge against ``self.db``."""

    def setUp(self) -> None:
        super().setUp()
        self.events: list[str] = []
        self.plan: list[str] = []
        self.db = _ResidueDb()
        self._real_purge = runner._purge_experiment_scores
        for target, attr, value, create in (
            (runner, "_SETTLED_EXPERIMENT_IDS", set(), True),
            (prepare, "get_connection", self.db.connection, False),
            (runner.time, "sleep", lambda seconds: None, False),
        ):
            patcher = mock.patch.object(target, attr, value, create=create)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _purge(self, experiment_id: str, market: str, decision: str) -> int:
        outcome = self.plan.pop(0) if self.plan else "ok"
        self.events.append(f"purge:{experiment_id}:{outcome}")
        if outcome == "fail":
            raise RuntimeError("server closed the connection unexpectedly")
        if outcome == "real":
            return self._real_purge(experiment_id, market, decision)
        had = experiment_id in self.db.residue
        self.db.residue.discard(experiment_id)
        return 3 if had else 0

    @contextmanager
    def _patched(self, results=(), setups=(), ids=None, git=None):
        results = list(results)
        setups = [_setup(*s) if isinstance(s, tuple) else s for s in setups]
        id_iter = iter(ids or [_xid(n) for n in range(1, 200)])

        def fake_verify(market):
            self.events.append("verify")
            return setups.pop(0) if setups else _setup()

        def fake_evaluate(market, **kw):
            self.events.append(f"evaluate:{kw.get('experiment_id')}")
            out = results.pop(0)
            if callable(out):
                out = out(kw)
            out = dict(out)
            out.setdefault("experiment_id", kw.get("experiment_id"))
            return out

        real_halt = runner._record_halt_row

        def halt(*args, **kwargs):
            self.events.append("halt")
            return real_halt(*args, **kwargs)

        git_patch = (
            mock.patch.object(runner, "_git_head_commit", side_effect=git)
            if git is not None
            else mock.patch.object(runner, "_git_head_commit", return_value="abcdef0")
        )
        with mock.patch.object(runner, "verify_setup", fake_verify), \
             mock.patch.object(runner, "evaluate", fake_evaluate), \
             mock.patch.object(runner, "_purge_experiment_scores", self._purge), \
             mock.patch.object(runner, "_make_experiment_id", lambda: next(id_iter)), \
             mock.patch.object(runner, "_record_halt_row", halt), \
             git_patch:
            yield

    def _loop(self, results=(), *, max_iterations=5, confirmed=False, **kw) -> dict:
        with self._patched(results, **kw):
            return runner.experiment_loop(
                "atlanta", max_iterations=max_iterations, confirmed=confirmed,
            )

    def _evaluated(self) -> list[str]:
        return [e for e in self.events if e.startswith("evaluate:")]

    def _purges(self) -> list[str]:
        return [e for e in self.events if e.startswith("purge:")]


A, B = _xid(1), _xid(2)
_DISCARD = _ok_result(4, 1.0)                 # below the seeded baseline (5)
_KEEP = _ok_result(6, 1.0)
_CRASH = dict(_ok_result(0, 0.0), status="crash", error="boom")


class TestPendingPurgeDurability(_FollowupBase):
    """(a) A failed purge is retried whenever the DB is usable, once more
    on every exit, and rebuilt from the TSV by the next process."""

    def test_retry_runs_during_a_harness_only_failure_streak(self) -> None:
        # The verified finding: A's purge hits a DB blip; verify_setup then
        # fails 3x on the harness while the DB is fine. The retry used to
        # wait for an overall pass, so the breaker halted and A's rows
        # stayed (a later experiment was credited with them).
        self._seed("baseline", 5, 1.0)
        self.db.residue.add(A)
        self.plan = ["fail"]
        summary = self._loop(
            [_DISCARD], setups=[_setup(), _setup()] + [_HARNESS_DOWN] * 3,
        )
        self.assertEqual(self.events, [
            "verify", "verify", f"evaluate:{A}", f"purge:{A}:fail",
            "verify", f"purge:{A}:ok",              # harness down, DB ok
            "verify", "verify", "halt",
        ])
        self.assertEqual(self.db.residue, set())
        last = self._rows()[-1]
        self.assertEqual(last["status"], "halt")
        self.assertIn("infrastructure failure", last["description"])
        self.assertEqual(summary.get("unpurged_experiment_ids"), [])

    def test_without_a_dsn_nothing_claims_purged(self) -> None:
        # DB down x3 with the DSN gone: no retry during the streak; the exit
        # retry (after the halt row) meets prepare's SystemExit, which ends
        # the pass without killing the loop or claiming a purge.
        self._seed("baseline", 5, 1.0)
        self.plan = ["fail", "real"]
        self.db.no_dsn = True
        with self.assertLogs("research", level="INFO") as logs:
            summary = self._loop(
                [_DISCARD], setups=[_setup(), _setup()] + [_DB_DOWN] * 3,
            )
        self.assertEqual(self.events, [
            "verify", "verify", f"evaluate:{A}", f"purge:{A}:fail",
            "verify", "verify", "verify", "halt", f"purge:{A}:real",
        ])
        self.assertEqual(summary["unpurged_experiment_ids"], [A])
        messages = [r.getMessage() for r in logs.records]
        errors = [r.getMessage() for r in logs.records if r.levelname == "ERROR"]
        self.assertTrue(any(A in m and "NOT purged" in m for m in errors), errors)
        self.assertTrue(any(A in m and "could not be purged" in m for m in errors), errors)
        self.assertFalse([m for m in messages if m.startswith("purged ")], messages)

    def test_queue_survives_the_process(self) -> None:
        # `make loop MAX=1` restarts the process every experiment, so the
        # in-memory queue never crossed one boundary; the TSV row does.
        self._seed("baseline", 5, 1.0)
        self.db.residue.add(A)
        self.plan = ["fail", "fail"]
        first = self._loop([_DISCARD], max_iterations=1, ids=[A])
        self.assertEqual(first["unpurged_experiment_ids"], [A])
        self.assertTrue(self._rows()[-1]["description"].endswith(f"exp={A}"))

        self.events.clear()
        second = self._loop([_KEEP], max_iterations=1, ids=[B])
        self.assertEqual(self.events, [
            "verify", f"purge:{A}:ok", "verify", f"evaluate:{B}",
        ])
        self.assertEqual(self.db.residue_reads(), [("test", [A])])
        self.assertEqual(second["unpurged_experiment_ids"], [])

    def test_long_db_blip_crash_row_is_rebuilt_into_the_queue(self) -> None:
        # (a) needs (b): the crash row of a DB blip carries the longest
        # error text, and its purge is the one most likely to fail too.
        self._seed("baseline", 5, 1.0)
        self.db.residue.add(A)
        self.plan = ["fail", "fail"]
        crash = dict(_ok_result(0, 0.0), status="crash", error=_PSYCOPG_BLIP * 2)
        self._loop([crash], max_iterations=1, ids=[A])
        row = self._rows()[-1]
        self.assertEqual(row["status"], "crash")
        self.assertLessEqual(len(row["description"]), runner._TSV_DESCRIPTION_MAX_LEN)
        self.assertEqual(
            runner._parse_markers_from_description(row["description"]), ("test", A),
        )
        self.events.clear()
        self._loop([_KEEP], max_iterations=1, ids=[B])
        self.assertEqual(self._purges(), [f"purge:{A}:ok"])
        self.assertEqual(self.db.residue, set())

    def test_rebuild_filters(self) -> None:
        X = [_xid(n) for n in range(100, 120)]
        spec = [
            ("baseline", f"baseline | market=atlanta | run=test | exp={X[0]}"),
            ("discard", f"market=atlanta | scored=1 | run=test | exp={X[1]}"),
            ("crash", f"market=atlanta | OperationalError: server closed | run=test | exp={X[2]}"),
            ("timeout", f"market=atlanta | budget_exceeded | run=test | exp={X[3]}"),
            ("crash", f"baseline | market=atlanta | run=test | exp={X[4]}"),
            ("discard", f"market=atlanta | run=old | exp={X[5]}"),            # other run
            ("discard", "market=atlanta | scored=1"),                          # legacy
            ("keep", f"market=atlanta | run=test | exp={X[6]}"),
            ("halt", "halt | market=atlanta | infrastructure failure x3"),
            ("discard", "market=atlanta | run=test | exp=e1"),                 # malformed
            ("discard", "market=atlanta | run=test | exp=exp-20261029T0000…"),  # truncated
            ("crash", f"market=atlanta | boom run=test | exp={X[7]} | run=old | exp={X[8]}"),
            ("crash", f"market=atlanta | ValueError run=test | exp={X[9]} | scored=2 | run=te…"),
            ("discard", f"market=atlanta | run=test | exp={X[6]}"),           # on the keep row
            ("discard", f"market=atlanta | run=test | exp={X[1]}"),           # duplicate
            ("discard", f"market=atlanta | run=test2 | exp={X[10]}"),
            ("discard", f"market=atlanta | run=tes | exp={X[11]}"),
            ("discard", f"market=atlanta | run=test | exp={X[12]} | x"),      # not the tail
            ("timeout", f"market=atlanta | run=test | exp={X[13]}"),          # settled here
            ("discard", f"market=atlanta | run=test | exp={X[0]}"),           # the baseline's id
        ]
        for status, description in spec:
            self._seed(status, 0, 0.0, description)
        rows = self._rows()          # through the real TSV writer and reader
        with mock.patch.object(runner, "_SETTLED_EXPERIMENT_IDS", {X[13]}, create=True), \
             self.assertLogs("research", level="WARNING") as logs:
            pending = runner._pending_purges_from_log(rows, "test")
        self.assertEqual(list(pending), [X[1], X[2], X[3], X[4]])
        self.assertEqual([p.decision for p in pending.values()],
                         ["discard", "crash", "timeout", "crash"])
        self.assertEqual([p.position for p in pending.values()], [1, 2, 3, 4])
        self.assertFalse(any(p.confirmed for p in pending.values()))
        self.assertTrue(any("parseable" in m for m in logs.output), logs.output)
        # Run-scoped: the forged earlier token (X7) is never a candidate.
        self.assertEqual(list(runner._pending_purges_from_log(rows, "old")),
                         [X[5], X[8]])

    # --- every exit gets one more attempt, after its halt row -------------
    def _exit_case(self, results, **kw) -> dict:
        self._seed("baseline", 5, 1.0)
        self.db.residue.add(A)
        self.plan = ["fail", "ok"]
        return self._loop(results, **kw)

    def test_halt_sentinel_exit(self) -> None:
        self.addCleanup(os.environ.pop, runner._HALT_ENV_VAR, None)

        def discard_then_halt(kw):
            os.environ[runner._HALT_ENV_VAR] = "1"
            return _DISCARD

        summary = self._exit_case([discard_then_halt])
        self.assertEqual(self.events[-3:], [f"purge:{A}:fail", "halt", f"purge:{A}:ok"])
        self.assertEqual(summary["unpurged_experiment_ids"], [])

    def test_max_iterations_exit(self) -> None:
        summary = self._exit_case([_DISCARD], max_iterations=1)
        self.assertEqual(self.events, [
            "verify", "verify", f"evaluate:{A}", f"purge:{A}:fail", f"purge:{A}:ok",
        ])
        self.assertEqual(summary["unpurged_experiment_ids"], [])

    def test_seven_day_exit(self) -> None:
        def discard_then_expire(kw):
            runner._LONG_RUN_GRACEFUL_EXIT_SECONDS = -1  # restored by the patch
            return _DISCARD

        with mock.patch.object(runner, "_LONG_RUN_GRACEFUL_EXIT_SECONDS", 3600):
            self._exit_case([discard_then_expire])
        self.assertEqual(self.events[-3:], [f"purge:{A}:fail", "halt", f"purge:{A}:ok"])
        self.assertIn("7-day", self._rows()[-1]["description"])

    def test_terminal_breaker_exit(self) -> None:
        with mock.patch.object(runner, "_TERMINAL_STATUS_BREAKER", 1):
            self._exit_case([_CRASH])
        self.assertEqual(self.events, [
            "verify", "verify", f"evaluate:{A}", f"purge:{A}:fail", "halt",
            f"purge:{A}:ok",
        ])
        self.assertEqual([r["status"] for r in self._rows()], ["baseline", "halt", "crash"])

    def test_infra_halt_on_a_failing_db(self) -> None:
        self._exit_case([_DISCARD], setups=[_setup(), _setup()] + [_DB_DOWN] * 3)
        self.assertEqual(self.events, [
            "verify", "verify", f"evaluate:{A}", f"purge:{A}:fail",
            "verify", "verify", "verify",             # DB unusable: no retry
            "halt", f"purge:{A}:ok",
        ])

    def test_exception_exit_retries_without_masking_the_exception(self) -> None:
        # The strict git lookup runs after the purge and before the TSV
        # append: the id is in neither the TSV nor (formerly) any queue.
        for second in ("ok", "fail"):
            with self.subTest(exit_retry=second):
                self._point_at(Path(self.tmp.name) / f"exc-{second}")
                self._seed("baseline", 5, 1.0)
                self.events.clear()
                self.plan = ["fail", second]
                with self.assertLogs("research", level="ERROR") as logs, \
                     self.assertRaises(runner.SetupError) as cm:
                    self._loop([_DISCARD], ids=[A],
                               git=runner.SetupError("git rev-parse HEAD failed"))
                self.assertIn("git rev-parse", str(cm.exception))
                self.assertEqual(self._purges(), [f"purge:{A}:fail", f"purge:{A}:{second}"])
                unpurged = any(A in m and "could not be purged" in m for m in logs.output)
                self.assertEqual(unpurged, second == "fail")

    def test_keyboard_interrupt_propagates_without_database_work(self) -> None:
        self._seed("baseline", 5, 1.0)
        self.plan = ["fail", "fail"]

        def interrupted(kw):
            raise KeyboardInterrupt

        with self.assertLogs("research", level="ERROR") as logs, \
             self.assertRaises(KeyboardInterrupt):
            self._loop([_DISCARD, interrupted])
        self.assertEqual(self.events, [
            "verify", "verify", f"evaluate:{A}", f"purge:{A}:fail",
            "verify", f"purge:{A}:fail", f"evaluate:{B}",   # no exit retry
        ])
        self.assertTrue(any(A in m and "interrupted" in m for m in logs.output))

    # --- no baseline over unpurged rows; the loop otherwise goes on -------
    def test_no_iteration_becomes_the_baseline_over_unpurged_rows(self) -> None:
        # A crashed bootstrap whose purge keeps failing: the next ok result
        # would be recorded as the baseline over its rows, so nothing is
        # evaluated and the infra breaker halts.
        self.db.residue.add(A)
        self.plan = ["fail"] * 10
        with self.assertLogs("research", level="ERROR") as logs:
            summary = self._loop([_CRASH], confirmed=True)
        self.assertEqual(self._evaluated(), [f"evaluate:{A}"])
        self.assertEqual([r["status"] for r in self._rows()], ["crash", "halt"])
        self.assertEqual(summary["unpurged_experiment_ids"], [A])
        self.assertTrue(any(A in m and "no baseline yet" in m for m in logs.output))

    def test_bootstrap_refused_while_an_earlier_attempt_is_unpurged(self) -> None:
        self._seed("crash", 0, 0.0, f"baseline | market=atlanta | run=test | exp={A}")
        self.db.residue.add(A)
        self.plan = ["fail"]
        with self.assertRaises(runner.SetupError) as cm:
            self._loop([_ok_result(5, 1.0)], confirmed=True, ids=[B])
        self.assertIn(A, str(cm.exception))
        self.assertEqual(self._evaluated(), [])
        self.assertEqual(len(self._rows()), 1)

    def test_bootstrap_purges_an_earlier_attempt_first(self) -> None:
        self._seed("crash", 0, 0.0, f"baseline | market=atlanta | run=test | exp={A}")
        self.db.residue.add(A)
        self._loop([_ok_result(5, 1.0)], max_iterations=0, confirmed=True, ids=[B])
        self.assertEqual(self.events, ["verify", f"purge:{A}:ok", f"evaluate:{B}"])
        self.assertEqual([r["status"] for r in self._rows()], ["crash", "baseline"])

    def test_loop_with_a_baseline_keeps_going_loudly(self) -> None:
        # Accepted residual (NEVER STOP, pinned by TestLoopPurgeWiring): with
        # the run's baseline recorded, a purge that keeps failing while the
        # DB check passes does not stop the loop; the ERROR says the next
        # metric includes the unpurged rows.
        self._seed("baseline", 5, 1.0)
        self.plan = ["fail"] * 5
        with self.assertLogs("research", level="ERROR") as logs:
            summary = self._loop([_DISCARD, _KEEP], max_iterations=2)
        self.assertEqual(summary["iterations"], 2)
        self.assertEqual(summary["unpurged_experiment_ids"], [A])
        self.assertTrue(any(A in m and "its metric includes them" in m
                            for m in logs.output))

    # --- the gated baseline (make baseline) --------------------------------
    def _gated(self, ids) -> dict:
        with self._patched([_ok_result(5, 1.0)], ids=ids):
            return runner.run_gated_baseline("atlanta")

    def test_gated_baseline_purges_an_earlier_attempt_first(self) -> None:
        self._seed("crash", 0, 0.0, f"baseline | market=atlanta | run=test | exp={A}")
        self.db.residue.add(A)
        row = self._gated([B])
        self.assertEqual(self.events, ["verify", f"purge:{A}:ok", f"evaluate:{B}"])
        self.assertEqual(row["status"], "baseline")
        self.assertEqual(row["description"], f"baseline | market=atlanta | run=test | exp={B}")

    def test_gated_baseline_refuses_over_rows_it_cannot_purge(self) -> None:
        self._seed("crash", 0, 0.0, f"baseline | market=atlanta | run=test | exp={A}")
        self.db.residue.add(A)
        self.plan = ["fail"]
        with self.assertRaises(runner.SetupError) as cm:
            self._gated([B])
        self.assertIn(A, str(cm.exception))
        self.assertEqual(self._evaluated(), [])
        self.assertEqual(len(self._rows()), 1)

    def test_gated_baseline_skips_a_clean_attempt(self) -> None:
        self._seed("crash", 0, 0.0, f"baseline | market=atlanta | run=test | exp={A}")
        self._gated([B])
        self.assertEqual(self.events, ["verify", f"evaluate:{B}"])
        self.assertEqual(self.db.residue_reads(), [("test", [A])])

    def test_operator_text_no_longer_claims_purged(self) -> None:
        self.assertNotIn("row logged + purged", _recipe("baseline"))
        cli_src = (_REPO_ROOT / "cli.py").read_text(encoding="utf-8")
        self.assertNotIn("logged (and purged)", cli_src)

    # --- one read per start; purge only what is left ----------------------
    def _seed_discards(self, ids) -> None:
        self._seed("baseline", 5, 1.0)
        for experiment_id in ids:
            self._seed("discard", 4, 1.0,
                       f"market=atlanta | scored=1 | run=test | exp={experiment_id}")

    def test_loop_start_reads_once_and_writes_nothing_for_clean_ids(self) -> None:
        ids = [_xid(n) for n in range(300, 350)]
        self._seed_discards(ids)
        summary = self._loop(max_iterations=0)
        self.assertEqual(self.db.connects, 1)
        self.assertEqual(self.db.executes[0][0], runner._RESIDUE_STATEMENT_TIMEOUT)
        self.assertEqual(self.db.residue_reads(), [("test", ids)])
        self.assertEqual(self._purges(), [])
        self.assertEqual(summary["unpurged_experiment_ids"], [])
        self._loop(max_iterations=0)        # settled: not even re-read
        self.assertEqual(self.db.connects, 1)

    def test_only_ids_with_leftover_rows_are_purged(self) -> None:
        ids = [_xid(n) for n in range(300, 350)]
        self._seed_discards(ids)
        self.db.residue.update({ids[39], ids[9]})
        summary = self._loop(max_iterations=0)
        self.assertEqual(self._purges(), [f"purge:{ids[9]}:ok", f"purge:{ids[39]}:ok"])
        self.assertEqual(summary["unpurged_experiment_ids"], [])

    def test_failed_residue_read_keeps_every_candidate_pending(self) -> None:
        ids = [_xid(n) for n in range(300, 303)]
        self._seed_discards(ids)
        self.db.residue.update(ids)
        self.db.read_fails = True
        summary = self._loop(max_iterations=0)
        self.assertEqual(self._purges(), [])
        self.assertEqual(summary["unpurged_experiment_ids"], ids)
        self.assertEqual(self.db.connects, 2)       # loop start + exit retry

    def test_late_purge_under_an_anchor_is_an_error(self) -> None:
        self._seed("baseline", 5, 1.0)
        self._seed("discard", 4, 1.0, f"market=atlanta | run=test | exp={A}")
        self._seed("keep", 6, 1.0, f"market=atlanta | run=test | exp={B}")
        self.db.residue.add(A)
        with self.assertLogs("research", level="ERROR") as logs:
            self._loop(max_iterations=0)
        self.assertEqual(self._purges(), [f"purge:{A}:ok"])
        self.assertTrue(any("late purge" in m and A in m for m in logs.output))

    def test_cli_loop_warns_about_unpurged_ids(self) -> None:
        import cli
        ns = mock.Mock(market="atlanta", max=1, confirmed=False, json=False)
        out = io.StringIO()
        with mock.patch.object(runner, "experiment_loop",
                               return_value={"iterations": 1,
                                             "unpurged_experiment_ids": [A]}), \
             redirect_stdout(out):
            rc = cli.cmd_loop(ns)
        self.assertEqual(rc, 0)
        self.assertIn(A, out.getvalue())
        self.assertIn("could not be purged", out.getvalue())


class TestDescriptionBudget(_FollowupBase):
    """(b) Real discovery counts, a formatter that never raises, and the
    trailing run=/exp= markers always inside the 200-char cap."""

    def _fulton(self, discovery: int = 7) -> dict[str, Any]:
        """The shape research._discover_fulton returns."""
        counts = {"discovery": discovery, "rejection": 2, "unmappable": 1}
        return {"aborted": False,
                "per_corridor": {"south_fulton_campbellton": dict(counts)},
                "totals": dict(counts)}

    def _describe(self, discovery=None, scoring=None, **extra) -> str:
        sub = {}
        if discovery is not None:
            sub["discovery"] = discovery
        if scoring is not None:
            sub["scoring"] = scoring
        result = _ok_result(5, 1.0, experiment_id=A, sub_summaries=sub)
        result.update(extra)
        return runner._format_loop_description(result, "test")

    def test_real_fulton_shape_reports_its_discovery_total(self) -> None:
        discovery = {
            "cycle_id": "disco-fulton-x", "market": "atlanta", "counties": ["fulton"],
            "aborted": False, "abort_reason": None, "harness_status": "healthy",
            "per_county": {"fulton": self._fulton(1075)},
        }
        scoring = {"aborted": False, "counts": {"scored": 3, "missing": 0, "error": 0}}
        self.assertEqual(
            self._describe(discovery, scoring),
            f"market=atlanta | discovery_fulton=1075 | scored=3 | run=test | exp={A}",
        )

    def test_aborted_or_skipped_county_prints_no_count(self) -> None:
        drift = {"aborted": True, "reason": "field_mapping_drift", "missing": ["PARCELID"]}
        for discovery in (
            {"aborted": False, "per_county": {"fulton": drift}},
            {"aborted": False, "per_county": {"fulton": {"skipped": True,
                                                          "reason": "no_connector"}}},
            {"aborted": True, "abort_reason": "harness_failing",
             "per_county": {"fulton": {"skipped": True, "reason": "harness_failing"}}},
            {"aborted": True, "abort_reason": "cycle_id_collision",
             "per_county": {"fulton": self._fulton()}},
        ):
            with self.subTest(discovery=discovery):
                self.assertEqual(self._describe(discovery),
                                 f"market=atlanta | run=test | exp={A}")

    def test_formatter_never_raises_and_keeps_the_tail(self) -> None:
        def county(info):
            return {"discovery": {"aborted": False, "per_county": info}}

        garbage = [
            None, [], "x", 5,
            {"discovery": 5}, {"discovery": [1]}, {"discovery": {"per_county": 5}},
            county({"fulton": 5}), county({"fulton": None}), county({"fulton": "x"}),
            county({"fulton": {"totals": 5}}),
            county({"fulton": {"totals": {"discovery": "7"}}}),
            county({"fulton": {"totals": {"discovery": True}}}),
            county({"fulton": {"totals": {"discovery": -1}}}),
            county({"x run=evil": {"totals": {"discovery": 3}}}),
            county({"fulton | exp=x": {"totals": {"discovery": 3}}}),
            county({7: {"totals": {"discovery": 3}}}),
            {"scoring": 5}, {"scoring": {"counts": 5}},
            {"scoring": {"counts": {"scored": "7"}}},
            {"scoring": {"counts": {"scored": None}}},
        ]
        for sub in garbage:
            with self.subTest(sub=sub):
                result = {"market": "atlanta", "status": "crash", "error": "boom",
                          "sub_summaries": sub, "experiment_id": A,
                          "stamp_audit_violations": "3"}
                description = runner._format_loop_description(result, "test")
                self.assertEqual(description, f"market=atlanta | boom | run=test | exp={A}")
        self.assertEqual(runner._format_loop_description(None, "test"),
                         "market=? | run=test")

    def test_markers_survive_every_length(self) -> None:
        tags = ["t", "atl-2026-10-29", "atl-2026-10-29-" + "x" * 25,
                "a" * 64]  # the longest tag the run entry points accept
        filler = (_PSYCOPG_BLIP + " ") * 5
        errors = [filler[:n] for n in (0, 1, 23, 24, 25, 80, 150, 199, 200, 201, 250, 600)]
        cases = []
        n = 0
        for tag in tags:
            for error in errors:
                for counties in (0, 1, 30):
                    for violations in (0, 7):
                        n += 1
                        per_county = {f"county{i:02d}": self._fulton(i) for i in range(counties)}
                        result = {
                            "market": "atlanta",
                            "status": "crash" if error else "ok",
                            "error": error or None,
                            "sub_summaries": {
                                "discovery": {"aborted": False, "per_county": per_county},
                                "scoring": {"aborted": False, "counts": {"scored": 12}},
                            },
                            "stamp_audit_violations": violations,
                            "experiment_id": _xid(n),
                        }
                        description = runner._format_loop_description(result, tag)
                        cases.append((tag, error, violations, _xid(n), description))
                        runner.append_experiment_log_row({
                            "commit": "abcdef0", "metric": 0, "confidence": 0.0,
                            "status": "crash" if error else "discard",
                            "description": description,
                        }, path=self.tsv_path)
        rows = self._rows()
        self.assertEqual(len(rows), len(cases))
        for row, (tag, error, violations, experiment_id, description) in zip(rows, cases):
            with self.subTest(tag=tag, error=len(error), violations=violations,
                              description=description):
                self.assertLessEqual(len(description), runner._TSV_DESCRIPTION_MAX_LEN)
                self.assertEqual(row["description"], description)  # writer did not cut
                self.assertEqual(runner._row_run_tag(row), tag)
                self.assertEqual(runner._parse_markers_from_description(row["description"]),
                                 (tag, experiment_id))
                self.assertEqual("stamp_audit_violations=7" in description, bool(violations))
                if error:
                    self.assertIn(" ".join(error.split())[:20], description)

    def test_realistic_db_blip_row_keeps_its_markers(self) -> None:
        # Unbudgeted, this row was 250 chars: the cap cut both markers.
        result = dict(_ok_result(0, 0.0), status="crash", error=_PSYCOPG_BLIP,
                      experiment_id=A, sub_summaries={
                          "scoring": {"aborted": False, "counts": {"scored": 0}},
                      })
        naive = " | ".join(["market=atlanta", " ".join(_PSYCOPG_BLIP.split()),
                            "scored=0", "run=atl-2026-10-29", f"exp={A}"])
        self.assertGreater(len(naive), runner._TSV_DESCRIPTION_MAX_LEN)
        description = runner._format_loop_description(result, "atl-2026-10-29")
        self.assertLessEqual(len(description), runner._TSV_DESCRIPTION_MAX_LEN)
        self.assertTrue(description.startswith("market=atlanta | OperationalError: "))
        self.assertTrue(description.endswith(f"… | scored=0 | run=atl-2026-10-29 | exp={A}"))

    def test_baseline_description_is_budgeted_too(self) -> None:
        description = runner._compose_description(
            ["baseline", "market=" + "m" * 300], ["run=test", f"exp={A}"],
        )
        self.assertLessEqual(len(description), runner._TSV_DESCRIPTION_MAX_LEN)
        self.assertTrue(description.startswith("baseline | market=mmm"))
        self.assertTrue(description.endswith(f"… | run=test | exp={A}"))

    def test_sanitize_unchanged_and_idempotent(self) -> None:
        # The mirror stores the sanitized text and backfill dedups on exact
        # values (SR-13): the writer's sanitizer must not change.
        ab = "ab " * 100
        corpus = {
            "": "",
            "plain": "plain",
            "a\tb\nc\r\nd\x00e": "a b c d e",
            "  lead   and  trail  ": "lead and trail",
            "x" * 200: "x" * 200,
            "x" * 201: "x" * 199 + "…",
            ab: ab.strip()[:199].rstrip() + "…",
            f"market=atlanta | run=test | exp={A}": f"market=atlanta | run=test | exp={A}",
        }
        for raw, expected in corpus.items():
            with self.subTest(raw=raw[:30]):
                self.assertEqual(runner._sanitize_description(raw), expected)
                self.assertEqual(runner._sanitize_description(expected), expected)
                if len(raw) <= runner._TSV_DESCRIPTION_MAX_LEN:
                    self.assertEqual(runner._description_piece(raw), expected)
        self.assertEqual(runner._sanitize_description(None), "")

    def test_overlong_run_tag_is_refused(self) -> None:
        self.assertEqual(runner._MAX_RUN_TAG_LEN, 64)
        tag = "a" * (runner._MAX_RUN_TAG_LEN + 1)
        setup = dict(_SETUP_OK, tag=tag, branch=f"autoresearch/{tag}")
        with mock.patch.object(runner, "verify_setup", return_value=setup), \
             mock.patch.object(runner, "evaluate",
                               side_effect=AssertionError("must not evaluate")):
            with self.assertRaises(runner.SetupError):
                runner.experiment_loop("atlanta", max_iterations=0, confirmed=True)
            with self.assertRaises(runner.SetupError):
                runner.run_gated_baseline("atlanta")
        self.assertFalse(self.tsv_path.exists())


@unittest.skipUnless(shutil.which("make"), "GNU make required")
class TestOpsTooling(unittest.TestCase):
    """(d) make log/orient/status render without `column`; a recorded
    baseline exits 0. (e) mirror-restore refuses in one line."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name) / "work"
        self.cwd.mkdir()
        self.bin = Path(self.tmp.name) / "bin"   # the only PATH entry
        self.bin.mkdir()
        # A byte-identical copy, so the recipes' own `$(MAKE) log` finds it.
        shutil.copyfile(_MAKEFILE, self.cwd / "Makefile")
        for tool in ("git", "head", "tail", "grep", "awk", "date", "cat"):
            found = shutil.which(tool)
            if found is None:
                self.skipTest(f"{tool} not installed")
            (self.bin / tool).symlink_to(found)
        self.descriptions = [
            "baseline | market=atlanta | run=test | exp=" + _xid(1),
            "market=atlanta | discovery_fulton=1075 | scored=3 | run=test | exp=" + _xid(2),
            "market=atlanta | OperationalError: boom … | run=test | exp=" + _xid(3),
        ]
        for status, description in zip(("baseline", "keep", "crash"), self.descriptions):
            runner.append_experiment_log_row({
                "commit": "abcdef0", "metric": 5, "confidence": 1.0,
                "status": status, "description": description,
            }, path=self.cwd / "experiment_log.tsv")

    def _script(self, name: str, body: str) -> None:
        path = self.bin / name
        if path.is_symlink() or path.exists():
            path.unlink()
        path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def _make(self, *targets: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if k not in {"CONFIRM", "MAKEFLAGS", "MFLAGS", "MAKELEVEL",
                            "EXPERIMENT_LOG_PATH"}}
        env["PATH"] = str(self.bin)
        return subprocess.run(
            [shutil.which("make"), "--no-print-directory", *targets],
            cwd=str(self.cwd), env=env, capture_output=True, text=True, timeout=60,
        )

    def test_log_and_orient_render_without_column(self) -> None:
        for target in ("log", "orient"):
            with self.subTest(target=target):
                proc = self._make(target)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                for description in self.descriptions:
                    self.assertIn(description, proc.stdout)
                self.assertNotIn("column", proc.stderr)

    def test_column_is_used_when_installed(self) -> None:
        self._script("column", "echo FAKE-COLUMN; exec cat")
        proc = self._make("log")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("FAKE-COLUMN"), proc.stdout)

    def test_recorded_baseline_exits_zero_even_if_the_log_cannot_render(self) -> None:
        self._script("git", "echo autoresearch/test")
        self._script("python", "exit 0")         # cli.py baseline recorded it
        self._script("awk", "exit 1")            # and nothing can render the TSV
        proc = self._make("baseline")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("could not render", proc.stdout)
        self.assertIn("Setup Step 6", proc.stdout)

    def test_refused_baseline_still_exits_nonzero(self) -> None:
        self._script("git", "echo autoresearch/test")
        self._script("python", "exit 2")
        proc = self._make("baseline")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no baseline recorded", proc.stdout)
        self.assertNotIn("Setup Step 6", proc.stdout)

    def test_static_guards(self) -> None:
        text = _MAKEFILE.read_text(encoding="utf-8")
        self.assertIn("TSV_ALIGN = { if command -v column >/dev/null 2>&1; then", text)
        for target in ("orient", "status", "log"):
            recipe = _recipe(target)
            self.assertNotIn("column", recipe, target)
            self.assertIn("$(TSV_ALIGN)", recipe, target)
        orient = _recipe("orient")
        self.assertNotIn("python", orient)       # the no-DB light-tier path
        self.assertNotIn("runner", orient)
        baseline = _recipe("baseline")
        log_line = next(l for l in baseline.splitlines()
                        if "--no-print-directory log" in l)
        self.assertIn("||", log_line)
        self.assertIn("exit $$rc", baseline)
        restore = _recipe("mirror-restore")
        self.assertIn("cli.py --no-color mirror-restore", restore)
        self.assertNotIn("python -c", restore)


class TestMirrorRestoreRefusal(_TsvLoopBase):
    """(e) A live TSV: one formatted stderr line, non-zero, no DB contact."""

    def test_refusal_is_one_line_without_traceback_or_db(self) -> None:
        import cli
        self._seed("baseline", 5, 1.0)
        before = self.tsv_path.read_bytes()
        connects: list[str] = []

        def no_connection():
            connects.append("connect")
            raise AssertionError("mirror-restore must refuse before connecting")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(prepare, "get_connection", no_connection), \
             redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--no-color", "mirror-restore"])
        self.assertEqual(rc, 2)
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("mirror-restore refused", lines[0])
        self.assertIn("non-empty", lines[0])
        self.assertNotIn("Traceback", err.getvalue())
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.tsv_path.read_bytes(), before)
        self.assertEqual(connects, [])

    def test_refusal_is_a_narrow_runtime_error(self) -> None:
        self._seed("baseline", 5, 1.0)
        with self.assertRaises(runner.RestoreRefusedError):
            runner.restore_experiment_log_from_mirror(self.tsv_path)
        self.assertTrue(issubclass(runner.RestoreRefusedError, RuntimeError))
        self.assertFalse(issubclass(runner.RestoreRefusedError, runner.SetupError))


if __name__ == "__main__":
    unittest.main()
