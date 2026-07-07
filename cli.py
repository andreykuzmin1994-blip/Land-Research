"""cli.py — Operator command-line interface for the autoresearch loop.

Thin argparse wrapper around research.py's public API. Provides
structured output, ANSI-colored status indicators, exit codes suitable
for scripting, and a --json mode for machine-readable output.

Configuration / ergonomic plumbing only -- does not contain
autoresearch logic. The Makefile provides the equivalent shell-only
access pattern; cli.py is for operators who prefer Python invocation,
scripted tooling, or richer output.

Subcommands (run ``python cli.py --help`` for the full list):

    verify              Pretty-print verify_setup(MARKET) results
    db-check            Run prepare.py (Supabase + PostGIS sanity ping)
    baseline            Bootstrap the baseline experiment row
    loop                Run experiment_loop with structured output
    status              verify_setup + last 10 TSV rows
    log                 Pretty-print experiment_log.tsv
    db-stats            Per-table row counts and research_log breakdown
    halt / unhalt       Sentinel manipulation
    tail                Live-stream experiment_log.tsv

Exit codes:
    0   success
    1   generic failure
    2   setup error (e.g. wrong branch, DATABASE_URL missing)
    3   lock contention (loop already running)
"""

from __future__ import annotations


import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


# ---------------------------------------------------------------------------
# ANSI colour helpers (TTY-detected; can be forced off via --no-color)
# ---------------------------------------------------------------------------
class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"


_USE_COLOR = True


def _set_color(on: bool) -> None:
    global _USE_COLOR
    _USE_COLOR = on


def _c(text: str, code: str) -> str:
    if not _USE_COLOR:
        return text
    return f"{code}{text}{_C.RESET}"


def _status_label(status: str) -> str:
    """Return a colored fixed-width label for an ok/warning/fail status."""
    s = (status or "").lower()
    if s == "ok":
        return _c("[OK]   ", _C.GREEN)
    if s == "warning":
        return _c("[WARN] ", _C.YELLOW)
    if s == "fail":
        return _c("[FAIL] ", _C.RED)
    if s == "skipped":
        return _c("[SKIP] ", _C.DIM)
    return f"[{s.upper()}] "


def _tsv_status_color(status: str) -> str:
    """Map an experiment_log status to a color code."""
    return {
        "baseline": _C.CYAN,
        "keep": _C.GREEN,
        "discard": _C.DIM,
        "crash": _C.RED,
        "timeout": _C.YELLOW,
        "halt": _C.MAGENTA,
    }.get(status, "")


# ---------------------------------------------------------------------------
# TSV table rendering
# ---------------------------------------------------------------------------
def _render_tsv_table(
    rows: Sequence[Mapping[str, str]],
    columns: Sequence[str],
) -> str:
    """Column-aligned text table. Header underlined, status column colored."""
    if not rows:
        return _c("  (no rows)", _C.DIM) + "\n"
    widths: dict[str, int] = {}
    for col in columns:
        w = len(col)
        for r in rows:
            w = max(w, len(str(r.get(col, ""))))
        widths[col] = w

    out: list[str] = []
    header = "  ".join(_c(col.ljust(widths[col]), _C.BOLD) for col in columns)
    out.append(header)
    out.append("  ".join("-" * widths[col] for col in columns))
    for r in rows:
        cells: list[str] = []
        for col in columns:
            value = str(r.get(col, ""))
            padded = value.ljust(widths[col])
            if col == "status":
                code = _tsv_status_color(value.strip())
                if code:
                    padded = _c(padded, code)
            cells.append(padded)
        out.append("  ".join(cells))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------
def _print_setup_dict(setup: Mapping[str, Any]) -> None:
    overall = setup.get("status", "unknown")
    print(_status_label(overall) + _c(f"verify_setup overall: {overall}", _C.BOLD))
    branch = setup.get("branch")
    tag = setup.get("tag")
    is_ar = setup.get("is_autoresearch_branch")
    print(f"  branch:                {branch}  {'(autoresearch)' if is_ar else ''}")
    print(f"  tag:                   {tag}")
    checks = setup.get("checks", {}) or {}
    for name, info in checks.items():
        info_status = (info or {}).get("status", "unknown")
        print(_status_label(info_status) + f"check.{name}")
        for k, v in (info or {}).items():
            if k == "status":
                continue
            v_str = json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v)
            if v_str and v_str != "null":
                print(f"    {k}: {v_str}")


def cmd_verify(args: argparse.Namespace) -> int:
    import research  # deferred so --help works without DATABASE_URL
    import runner  # deferred so --help works without DATABASE_URL
    setup = runner.verify_setup(args.market)
    if args.json:
        print(json.dumps(setup, indent=2, default=str))
    else:
        _print_setup_dict(setup)
    return 0 if setup.get("status") in {"ok", "warning"} else 2


def cmd_db_check(args: argparse.Namespace) -> int:
    import subprocess
    cwd = Path(__file__).resolve().parent
    proc = subprocess.run(
        [sys.executable, str(cwd / "prepare.py")],
        cwd=str(cwd),
    )
    return proc.returncode


def cmd_baseline(args: argparse.Namespace) -> int:
    import research
    import runner
    print(_c(f"==> running baseline experiment for market={args.market}", _C.BOLD))
    row = runner.run_baseline_experiment(args.market)
    if args.json:
        print(json.dumps(row, indent=2, default=str))
    else:
        _print_baseline_row(row)
    return 0


def _print_baseline_row(row: Mapping[str, Any]) -> None:
    print(_status_label("ok") + _c("baseline row written", _C.BOLD))
    for k in ("commit", "metric", "confidence", "wall_clock_min", "status", "description"):
        print(f"  {k}: {row.get(k)}")


def cmd_loop(args: argparse.Namespace) -> int:
    import research
    import runner
    print(_c(
        f"==> experiment_loop(market={args.market}, max_iterations={args.max if args.max else 'None (NEVER STOP)'})",
        _C.BOLD,
    ))
    print(_c(
        "==> halt with: python cli.py halt  (or set EXPERIMENT_LOOP_HALT=1)",
        _C.DIM,
    ))
    try:
        summary = runner.experiment_loop(
            args.market,
            max_iterations=args.max,
            confirmed=args.confirmed,
        )
    except runner.SetupError as exc:
        print(_status_label("fail") + f"SetupError: {exc}", file=sys.stderr)
        return 2
    except runner.LoopLockError as exc:
        print(_status_label("fail") + f"LoopLockError: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(_status_label("ok") + _c("loop ended", _C.BOLD))
        for k, v in summary.items():
            print(f"  {k}: {v}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    import research
    import runner
    setup = runner.verify_setup(args.market)
    print(_c(f"==> verify_setup(market={args.market})", _C.BOLD))
    if args.json:
        print(json.dumps(setup, indent=2, default=str))
    else:
        _print_setup_dict(setup)
    print()
    log_path = runner._experiment_log_path()
    rows = runner.read_experiment_log(log_path)
    print(_c(f"==> last {min(args.tail, len(rows)) if rows else 0} of {len(rows)} TSV rows", _C.BOLD))
    if rows:
        cols = ["commit", "metric", "confidence", "api_calls",
                "wall_clock_min", "status", "description"]
        print(_render_tsv_table(rows[-args.tail :], cols), end="")
    else:
        print("  (no experiment_log.tsv yet)")
    halt_path = runner._HALT_SENTINEL_PATH
    if halt_path.exists():
        print()
        print(_status_label("warning") + ".halt sentinel is set")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    import research
    import runner
    log_path = runner._experiment_log_path()
    rows = runner.read_experiment_log(log_path)
    if not rows:
        print("(no experiment_log.tsv yet)")
        return 0
    if args.tail:
        rows = rows[-args.tail :]
    cols = ["commit", "metric", "confidence", "api_calls",
            "wall_clock_min", "status", "description"]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(_render_tsv_table(rows, cols), end="")
    return 0


def cmd_db_stats(args: argparse.Namespace) -> int:
    import prepare
    queries = [
        ("parcels",                  "SELECT COUNT(*) FROM parcels"),
        ("parcel_scores",            "SELECT COUNT(*) FROM parcel_scores"),
        ("parcel_scores actionable",
         "SELECT COUNT(*) FROM parcel_scores "
         "WHERE actionability='PASS' AND composite_score >= 70"),
        ("research_log",             "SELECT COUNT(*) FROM research_log"),
        ("flagged_items",            "SELECT COUNT(*) FROM flagged_items"),
        ("submarkets",               "SELECT COUNT(*) FROM submarkets"),
    ]
    counts: dict[str, int] = {}
    breakdown: list[tuple[str, int]] = []
    with prepare.get_connection() as conn:
        with conn.cursor() as cur:
            for name, q in queries:
                cur.execute(q)
                row = cur.fetchone()
                counts[name] = int(row[0]) if row and row[0] is not None else 0
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action_type, COUNT(*) FROM research_log "
                "GROUP BY action_type ORDER BY 2 DESC"
            )
            breakdown = [(r[0], int(r[1])) for r in cur.fetchall()]

    if args.json:
        print(json.dumps({"counts": counts, "research_log_by_action": dict(breakdown)},
                         indent=2))
        return 0

    print(_c("Per-table row counts:", _C.BOLD))
    for name in counts:
        print(f"  {name:30s}{counts[name]}")
    print()
    print(_c("research_log by action_type:", _C.BOLD))
    if breakdown:
        for action, n in breakdown:
            print(f"  {action:25s}{n}")
    else:
        print("  (no log rows yet)")
    return 0


def cmd_halt(args: argparse.Namespace) -> int:
    import research
    import runner
    p = runner._HALT_SENTINEL_PATH
    if p.exists():
        print(_status_label("warning") + f".halt already exists at {p}")
        return 0
    p.touch()
    print(_status_label("ok") + ".halt sentinel created -- loop exits on next iteration boundary")
    return 0


def cmd_unhalt(args: argparse.Namespace) -> int:
    import research
    import runner
    p = runner._HALT_SENTINEL_PATH
    if p.exists():
        p.unlink()
        print(_status_label("ok") + ".halt removed")
    else:
        print(_status_label("ok") + ".halt was not set; nothing to do")
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    import research
    import runner
    log_path = runner._experiment_log_path()
    if not log_path.exists():
        print("(waiting for experiment_log.tsv to be created; Ctrl-C to exit)")
        try:
            while not log_path.exists():
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            fh.seek(0, 2)
            while True:
                line = fh.readline()
                if line:
                    print(line.rstrip("\n"))
                else:
                    time.sleep(1)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Operator CLI for the Land Research autoresearch loop",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="disable ANSI color output (forced off when stdout is not a TTY)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of formatted text",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("verify", help="run verify_setup and pretty-print the result")
    p.add_argument("--market", default="atlanta")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("db-check", help="run prepare.py (Supabase + PostGIS ping)")
    p.set_defaults(fn=cmd_db_check)

    p = sub.add_parser("baseline", help="run the baseline experiment for a market")
    p.add_argument("--market", default="atlanta")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("loop", help="run the experiment loop")
    p.add_argument("--market", default="atlanta")
    p.add_argument(
        "--max", type=int, default=None,
        help="cap iterations (default: NEVER STOP)",
    )
    p.add_argument(
        "--confirmed", action="store_true",
        help="bypass the Setup Step 6 baseline-confirmation gate",
    )
    p.set_defaults(fn=cmd_loop)

    p = sub.add_parser("status", help="verify_setup + last N TSV rows")
    p.add_argument("--market", default="atlanta")
    p.add_argument("--tail", type=int, default=10)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("log", help="pretty-print experiment_log.tsv")
    p.add_argument("--tail", type=int, default=None,
                   help="show only the last N rows")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("db-stats", help="per-table row counts and research_log breakdown")
    p.set_defaults(fn=cmd_db_stats)

    p = sub.add_parser("halt", help="create .halt sentinel (loop exits cleanly)")
    p.set_defaults(fn=cmd_halt)

    p = sub.add_parser("unhalt", help="remove .halt sentinel")
    p.set_defaults(fn=cmd_unhalt)

    p = sub.add_parser("tail", help="live-stream experiment_log.tsv")
    p.set_defaults(fn=cmd_tail)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    use_color = (
        sys.stdout.isatty()
        and not args.no_color
        and not args.json
    )
    _set_color(use_color)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
