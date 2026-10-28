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
    baseline on an explicit command-line ``CONFIRM=1``.

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

    def test_truncated_description_anchors_identically_on_both_paths(self) -> None:
        # >200 chars of description: the TSV truncation drops the trailing
        # run= token, so that keep row no longer anchors THIS run. The
        # in-memory anchor must see exactly the TSV row (validated), not
        # the raw untruncated dict.
        per_county = {f"county{i:02d}": {"inserted": i} for i in range(30)}
        long_keep = _ok_result(
            7, 1.0, sub_summaries={"discovery": {"per_county": per_county}},
        )
        self._point_at(Path(self.tmp.name) / "mem")
        self._seed("baseline", 5, 1.0)
        self._run_loop([long_keep, _ok_result(6, 1.0)], max_iterations=2)
        mem_rows = self._rows()
        self.assertEqual(mem_rows[1]["status"], "keep")
        self.assertIsNone(runner._row_run_tag(mem_rows[1]))
        via_mem = mem_rows[2]["status"]

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

    def test_pending_purge_retry_waits_for_a_passing_verify(self) -> None:
        # A failed purge is retried only once verify_setup passes again —
        # never against a DB the setup check just declared unusable.
        self._seed("baseline", 5, 1.0)
        calls: list[str] = []
        setup_seq = [dict(_SETUP_OK), dict(_SETUP_OK),
                     dict(_SETUP_OK, status="fail"), dict(_SETUP_OK)]

        def fake_verify(market):
            calls.append("verify")
            return setup_seq.pop(0)

        def flaky_purge(exp_id, market, decision):
            calls.append(f"purge:{exp_id}")
            if calls.count(f"purge:{exp_id}") == 1:
                raise RuntimeError("db down")
            return 0

        results = iter([_ok_result(4, 1.0, experiment_id="exp-a"),
                        _ok_result(6, 1.0, experiment_id="exp-b")])
        with mock.patch.object(runner, "verify_setup", fake_verify), \
             mock.patch.object(runner, "evaluate",
                               side_effect=lambda m, **kw: next(results)), \
             mock.patch.object(runner, "_purge_experiment_scores", flaky_purge), \
             mock.patch.object(runner, "_git_head_commit", return_value="abcdef0"), \
             mock.patch.object(runner.time, "sleep"):
            runner.experiment_loop("atlanta", max_iterations=2)
        self.assertEqual(
            calls,
            ["verify", "verify", "purge:exp-a", "verify", "verify", "purge:exp-a"],
        )


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


if __name__ == "__main__":
    unittest.main()
