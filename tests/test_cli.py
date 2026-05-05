"""Tests for cli.py — operator CLI for the autoresearch loop.

Exercises argparse wiring, ANSI color toggling, the TSV table renderer,
and the no-DB subcommands (halt / unhalt / log). Subcommands that
require a live Supabase connection are tested via mock.patch on the
research / prepare modules.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

# Repo root on sys.path so cli.py imports.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli  # noqa: E402


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------
class TestCliArgparse(unittest.TestCase):
    def test_no_args_prints_help_and_errors(self) -> None:
        parser = cli._build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_help_lists_every_subcommand(self) -> None:
        parser = cli._build_parser()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--help"])
        out = buf.getvalue()
        for sub in ("verify", "db-check", "baseline", "loop", "status",
                    "log", "db-stats", "halt", "unhalt", "tail"):
            self.assertIn(sub, out, f"--help missing subcommand: {sub}")

    def test_verify_default_market_is_atlanta(self) -> None:
        parser = cli._build_parser()
        ns = parser.parse_args(["verify"])
        self.assertEqual(ns.market, "atlanta")

    def test_loop_max_int(self) -> None:
        parser = cli._build_parser()
        ns = parser.parse_args(["loop", "--max", "3"])
        self.assertEqual(ns.max, 3)

    def test_loop_max_default_is_none(self) -> None:
        parser = cli._build_parser()
        ns = parser.parse_args(["loop"])
        self.assertIsNone(ns.max)

    def test_status_tail_default(self) -> None:
        parser = cli._build_parser()
        ns = parser.parse_args(["status"])
        self.assertEqual(ns.tail, 10)

    def test_global_no_color_flag(self) -> None:
        parser = cli._build_parser()
        ns = parser.parse_args(["--no-color", "halt"])
        self.assertTrue(ns.no_color)

    def test_global_json_flag(self) -> None:
        parser = cli._build_parser()
        ns = parser.parse_args(["--json", "log"])
        self.assertTrue(ns.json)


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
class TestCliColor(unittest.TestCase):
    def setUp(self) -> None:
        cli._set_color(True)

    def tearDown(self) -> None:
        cli._set_color(False)

    def test_color_on_wraps_with_ansi(self) -> None:
        out = cli._c("hello", cli._C.GREEN)
        self.assertIn("\033[32m", out)
        self.assertIn("\033[0m", out)
        self.assertIn("hello", out)

    def test_color_off_returns_plain(self) -> None:
        cli._set_color(False)
        out = cli._c("hello", cli._C.GREEN)
        self.assertEqual(out, "hello")

    def test_status_label_known(self) -> None:
        for s in ("ok", "warning", "fail", "skipped"):
            label = cli._status_label(s)
            self.assertIn(s.upper()[:4], label.upper())

    def test_status_label_unknown_falls_through(self) -> None:
        label = cli._status_label("foobar")
        self.assertIn("FOOBAR", label)

    def test_tsv_status_color_known(self) -> None:
        for s in ("baseline", "keep", "discard", "crash", "timeout", "halt"):
            self.assertNotEqual(cli._tsv_status_color(s), "")

    def test_tsv_status_color_unknown(self) -> None:
        self.assertEqual(cli._tsv_status_color("nonsense"), "")


# ---------------------------------------------------------------------------
# TSV table renderer
# ---------------------------------------------------------------------------
class TestCliRenderTsvTable(unittest.TestCase):
    def setUp(self) -> None:
        cli._set_color(False)

    def test_empty_rows_emits_marker(self) -> None:
        out = cli._render_tsv_table([], ["commit", "metric"])
        self.assertIn("no rows", out)

    def test_columns_aligned(self) -> None:
        rows = [
            {"commit": "0000001", "metric": "5",  "status": "baseline"},
            {"commit": "0000002", "metric": "12", "status": "keep"},
        ]
        out = cli._render_tsv_table(rows, ["commit", "metric", "status"])
        # Use rstrip("\n") not strip() -- the latter would clobber trailing
        # padding on the last data row, which is real and intentional.
        lines = out.rstrip("\n").split("\n")
        self.assertEqual(len(lines), 4)  # header + sep + 2 rows
        # Header determines the column positions; both data rows share them.
        self.assertEqual(len(lines[0]), len(lines[2]))
        self.assertEqual(len(lines[2]), len(lines[3]))
        # Status column starts at the same byte offset on every row.
        status_start = lines[0].index("status")
        self.assertTrue(lines[2][status_start:].startswith("baseline"))
        self.assertTrue(lines[3][status_start:].startswith("keep"))

    def test_header_present(self) -> None:
        rows = [{"commit": "abc1234", "metric": "1", "status": "baseline"}]
        out = cli._render_tsv_table(rows, ["commit", "metric", "status"])
        self.assertIn("commit", out)
        self.assertIn("metric", out)
        self.assertIn("status", out)


# ---------------------------------------------------------------------------
# halt / unhalt commands (no DB, sentinel filesystem only)
# ---------------------------------------------------------------------------
class TestCliHaltCommands(unittest.TestCase):
    def setUp(self) -> None:
        cli._set_color(False)
        import research
        self._orig_path = research._HALT_SENTINEL_PATH
        self._tmp = tempfile.TemporaryDirectory()
        research._HALT_SENTINEL_PATH = Path(self._tmp.name) / ".halt"

    def tearDown(self) -> None:
        import research
        research._HALT_SENTINEL_PATH = self._orig_path
        self._tmp.cleanup()

    def test_halt_creates_sentinel(self) -> None:
        import research
        ns = mock.Mock()
        rc = cli.cmd_halt(ns)
        self.assertEqual(rc, 0)
        self.assertTrue(research._HALT_SENTINEL_PATH.exists())

    def test_halt_idempotent(self) -> None:
        import research
        research._HALT_SENTINEL_PATH.touch()
        ns = mock.Mock()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_halt(ns)
        self.assertEqual(rc, 0)
        self.assertIn("already exists", buf.getvalue())

    def test_unhalt_removes_sentinel(self) -> None:
        import research
        research._HALT_SENTINEL_PATH.touch()
        ns = mock.Mock()
        rc = cli.cmd_unhalt(ns)
        self.assertEqual(rc, 0)
        self.assertFalse(research._HALT_SENTINEL_PATH.exists())

    def test_unhalt_no_sentinel_is_ok(self) -> None:
        ns = mock.Mock()
        rc = cli.cmd_unhalt(ns)
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# log command (no DB, reads TSV from disk)
# ---------------------------------------------------------------------------
class TestCliLogCommand(unittest.TestCase):
    def setUp(self) -> None:
        cli._set_color(False)
        self._tmp = tempfile.TemporaryDirectory()
        self._tsv = Path(self._tmp.name) / "experiment_log.tsv"
        self._old_env = os.environ.pop("EXPERIMENT_LOG_PATH", None)
        os.environ["EXPERIMENT_LOG_PATH"] = str(self._tsv)

    def tearDown(self) -> None:
        os.environ.pop("EXPERIMENT_LOG_PATH", None)
        if self._old_env is not None:
            os.environ["EXPERIMENT_LOG_PATH"] = self._old_env
        self._tmp.cleanup()

    def _seed(self) -> None:
        import research
        for i, status in enumerate(["baseline", "keep", "discard"]):
            research.append_experiment_log_row({
                "commit": f"{i:07x}", "metric": i, "confidence": float(i),
                "api_calls": 0, "wall_clock_min": 1.0,
                "status": status, "description": f"row {i}",
            }, path=self._tsv)

    def test_log_no_file(self) -> None:
        ns = mock.Mock(tail=None, json=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_log(ns)
        self.assertEqual(rc, 0)
        self.assertIn("no experiment_log.tsv", buf.getvalue())

    def test_log_renders_rows(self) -> None:
        self._seed()
        ns = mock.Mock(tail=None, json=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_log(ns)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("baseline", out)
        self.assertIn("keep", out)
        self.assertIn("discard", out)

    def test_log_tail_caps_rows(self) -> None:
        self._seed()
        ns = mock.Mock(tail=2, json=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_log(ns)
        out = buf.getvalue()
        # The first row "row 0" / status=baseline is dropped when tail=2.
        self.assertNotIn("row 0", out)
        self.assertIn("row 1", out)
        self.assertIn("row 2", out)

    def test_log_json_mode(self) -> None:
        self._seed()
        ns = mock.Mock(tail=None, json=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_log(ns)
        data = json.loads(buf.getvalue())
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["status"], "baseline")


# ---------------------------------------------------------------------------
# verify / status (mocked research.verify_setup)
# ---------------------------------------------------------------------------
class TestCliVerify(unittest.TestCase):
    def setUp(self) -> None:
        cli._set_color(False)

    def test_verify_ok_returns_zero(self) -> None:
        ns = mock.Mock(market="atlanta", json=False)
        with mock.patch("research.verify_setup", return_value={
            "status": "ok", "branch": "autoresearch/test", "tag": "test",
            "is_autoresearch_branch": True,
            "checks": {"db": {"status": "ok", "postgis_version": "3.3"}},
        }):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_verify(ns)
        self.assertEqual(rc, 0)
        self.assertIn("verify_setup overall: ok", buf.getvalue())

    def test_verify_warning_returns_zero(self) -> None:
        ns = mock.Mock(market="atlanta", json=False)
        with mock.patch("research.verify_setup", return_value={
            "status": "warning", "branch": "autoresearch/test", "tag": "test",
            "is_autoresearch_branch": True, "checks": {},
        }):
            with redirect_stdout(io.StringIO()):
                rc = cli.cmd_verify(ns)
        self.assertEqual(rc, 0)

    def test_verify_fail_returns_two(self) -> None:
        ns = mock.Mock(market="atlanta", json=False)
        with mock.patch("research.verify_setup", return_value={
            "status": "fail", "branch": "main", "tag": None,
            "is_autoresearch_branch": False, "checks": {},
        }):
            with redirect_stdout(io.StringIO()):
                rc = cli.cmd_verify(ns)
        self.assertEqual(rc, 2)

    def test_verify_json_mode(self) -> None:
        ns = mock.Mock(market="atlanta", json=True)
        payload = {
            "status": "ok", "branch": "autoresearch/x", "tag": "x",
            "is_autoresearch_branch": True, "checks": {},
        }
        with mock.patch("research.verify_setup", return_value=payload):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_verify(ns)
        out = buf.getvalue()
        # Must be valid JSON.
        data = json.loads(out)
        self.assertEqual(data["status"], "ok")


# ---------------------------------------------------------------------------
# loop subcommand error mapping
# ---------------------------------------------------------------------------
class TestCliLoopErrors(unittest.TestCase):
    def setUp(self) -> None:
        cli._set_color(False)

    def test_setup_error_maps_to_exit_2(self) -> None:
        import research
        ns = mock.Mock(market="atlanta", max=1, confirmed=False, json=False)
        with mock.patch.object(research, "experiment_loop",
                               side_effect=research.SetupError("nope")):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = cli.cmd_loop(ns)
        self.assertEqual(rc, 2)

    def test_lock_error_maps_to_exit_3(self) -> None:
        import research
        ns = mock.Mock(market="atlanta", max=1, confirmed=False, json=False)
        with mock.patch.object(research, "experiment_loop",
                               side_effect=research.LoopLockError("locked")):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = cli.cmd_loop(ns)
        self.assertEqual(rc, 3)

    def test_loop_success_returns_zero(self) -> None:
        import research
        ns = mock.Mock(market="atlanta", max=1, confirmed=True, json=False)
        with mock.patch.object(research, "experiment_loop",
                               return_value={"iterations": 1, "halted": False,
                                             "wall_clock_min_total": 1.0,
                                             "market": "atlanta"}):
            with redirect_stdout(io.StringIO()):
                rc = cli.cmd_loop(ns)
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# main() integration: --no-color sets color off; bare `cli halt` returns 0
# ---------------------------------------------------------------------------
class TestCliMain(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        import research
        self._orig_path = research._HALT_SENTINEL_PATH
        research._HALT_SENTINEL_PATH = Path(self._tmp.name) / ".halt"

    def tearDown(self) -> None:
        import research
        research._HALT_SENTINEL_PATH = self._orig_path
        self._tmp.cleanup()

    def test_main_halt_no_color(self) -> None:
        import research
        with redirect_stdout(io.StringIO()):
            rc = cli.main(["--no-color", "halt"])
        self.assertEqual(rc, 0)
        self.assertTrue(research._HALT_SENTINEL_PATH.exists())

    def test_main_unhalt(self) -> None:
        import research
        research._HALT_SENTINEL_PATH.touch()
        with redirect_stdout(io.StringIO()):
            rc = cli.main(["--no-color", "unhalt"])
        self.assertEqual(rc, 0)
        self.assertFalse(research._HALT_SENTINEL_PATH.exists())

    def test_main_unknown_subcommand_exits_two(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                cli.main(["--no-color", "nonexistent"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
