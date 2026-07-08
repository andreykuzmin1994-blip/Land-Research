"""runner.py — the AutoResearch experiment loop, setup phase, and TSV I/O (Phase 10).

Extracted verbatim from research.py as part of the sandbox split (see
reviews/14_streamlining_review/00_streamlining_review.md Finding B). This is
the harness that RUNS and JUDGES experiments: setup verification, the
evaluator, baseline bootstrap, keep-or-revert decision recording, the
append-only experiment_log.tsv, the advisory loop lock, and the .halt
sentinel.

============================================================================
IMMUTABILITY DURING A RUN
============================================================================
Same status as prepare.py (AUTORESEARCH_MECHANICS.md, Five-File Contract):
neither the human nor the agent edits this module during a run. The agent
experiments by editing research.py ONLY. If the loop, evaluator, or TSV
logic needs to change, halt the run, change it between runs under the
tiered review process, and start a fresh run. An agent that can edit the
code that evaluates its own experiments can silently corrupt the
experiment log — this split removes the FILE-LEVEL version of that
failure mode. Runtime rebinding from sandbox code executing in the same
interpreter remains possible and is an ACCEPTED, monitored risk: see
STANDING_RISKS.md SR-16 (stamp audit in evaluate; subprocess-isolated
metric read is the earmarked hardening if it ever fires).

Review history: reviews/12_phase10_experiment_loop/.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
import secrets
import subprocess
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import prepare
import costar_ingest
import reporting
import research
from pipeline_common import _COSTAR_BASE_DIR, _REPO_ROOT

log = logging.getLogger("research")



# ===========================================================================
# Phase 10 — The experiment loop, setup phase, and experiment_log.tsv I/O
# ===========================================================================
# Per AUTORESEARCH_MECHANICS.md "The Setup Phase" + "The Experiment Loop" +
# "The Git Ratchet" + "The Experiment Log".  Designed by Agent 1 risk review
# at reviews/12_phase10_experiment_loop/01_risk_review.md (R-701..R-733).
#
# This block provides:
#
#   - evaluate(market)                 -- one full discovery+scoring+memo cycle
#                                         + metric read via prepare.calculate_*
#   - apply_keep_or_revert_decision()  -- pure decision function (R-713)
#   - read_experiment_log(path)        -- TSV reader (R-722)
#   - append_experiment_log_row(...)   -- TSV append-only writer (R-716..R-721)
#   - verify_setup(market)             -- AUTORESEARCH_MECHANICS Setup Step 4
#   - run_baseline_experiment(market)  -- AUTORESEARCH_MECHANICS Setup Step 5
#   - experiment_loop(market, ...)     -- the NEVER STOP loop (R-723..R-732)
#
# What this block DOES NOT do (R-723):
#
#   - It does not call ``git reset --hard HEAD~1`` from Python.  The Karpathy
#     pattern has the AGENT (Claude Code) modify research.py + commit + invoke
#     evaluate + read result + decide + revert.  Phase 10 provides the
#     helpers; the agent invokes them between iterations and performs the
#     git operation in its own tool calls.
#   - It does not auto-create the autoresearch/<tag> branch.  Setup Step 2
#     requires a human to ``git checkout -b autoresearch/<tag>`` from main.
#   - It does not modify research.py.  Hypothesis generation is the agent's
#     job; Phase 10 just observes the outcome.
# ---------------------------------------------------------------------------
_EXPERIMENT_LOG_FILENAME = "experiment_log.tsv"
_EXPERIMENT_LOG_PATH = _REPO_ROOT / _EXPERIMENT_LOG_FILENAME

# AUTORESEARCH_MECHANICS.md L300-L310 — exact 7-column schema.
_TSV_COLUMNS: tuple[str, ...] = (
    "commit",
    "metric",
    "confidence",
    "api_calls",
    "wall_clock_min",
    "status",
    "description",
)

# R-719 — schema validation enums and patterns.
_TSV_STATUSES: frozenset[str] = frozenset(
    {"baseline", "keep", "discard", "crash", "timeout", "halt"}
)
_TSV_COMMIT_RE = re.compile(r"^([0-9a-f]{7,40}|pending)$")

# R-718 — description sanitization caps.
_TSV_DESCRIPTION_MAX_LEN = 200

# R-703, R-704 — branch invariant. Single-sourced from prepare so the
# runner and the metric layer can never disagree on branch grammar (F9).
_AUTORESEARCH_BRANCH_RE = re.compile(prepare._AUTORESEARCH_BRANCH_RE)

# R-725, R-728 — halt sentinel.
_HALT_SENTINEL_PATH = _REPO_ROOT / ".halt"
_HALT_ENV_VAR = "EXPERIMENT_LOOP_HALT"

# R-729 — advisory lock.
_LOOP_LOCK_PATH = _REPO_ROOT / ".experiment_loop.lock"
_LOOP_LOCK_ENV_VAR = "EXPERIMENT_LOOP_LOCK_PATH"

# R-733 — soft per-iteration budget.  AUTORESEARCH_MECHANICS.md L153 says 90
# minutes; Phase 10's in-process loop measures wall-clock and emits a
# ``status=timeout`` row if elapsed exceeds.  OS-level enforcement requires
# launching the evaluator as a subprocess wrapped by
# ``prepare.run_with_os_timeout`` -- documented but not the default.
_PHASE10_BUDGET_SECONDS = 90 * 60

# R-731 — catastrophic failure detection.
_INFRA_FAILURE_THRESHOLD = 3

# Tier-2 review F5 — consecutive crash/timeout iterations before the loop
# halts instead of purging and repeating the identical workload forever.
_TERMINAL_STATUS_BREAKER = 5

# R-732 — long-run graceful conclusion.
_LONG_RUN_GRACEFUL_EXIT_SECONDS = 7 * 24 * 60 * 60

# R-706 — CoStar staleness threshold (informational only).
_COSTAR_STALENESS_DAYS = 30


class SetupError(RuntimeError):
    """Raised by ``verify_setup`` and ``experiment_loop`` when an
    AUTORESEARCH_MECHANICS.md Setup Phase precondition is not satisfied
    (R-703, R-705).  Always carries an actionable message."""


class LoopLockError(RuntimeError):
    """Raised when a second ``experiment_loop`` attempts to start while
    another one already holds the advisory lock (R-729)."""


# ---------------------------------------------------------------------------
# Git plumbing (R-704)
# ---------------------------------------------------------------------------
def _git_current_branch() -> str:
    """Return the current branch name via ``git rev-parse --abbrev-ref HEAD``.

    Returns the literal string ``"HEAD"`` for a detached-HEAD checkout.
    Raises ``SetupError`` if git is not available or the working directory
    is not a git repo.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            check=True,
            timeout=10,
        )
    except FileNotFoundError as exc:  # pragma: no cover -- git always present in CI
        raise SetupError("git command not found") from exc
    except subprocess.CalledProcessError as exc:
        raise SetupError(
            f"git rev-parse failed: {exc.stderr.strip() or 'unknown error'}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SetupError("git rev-parse timed out") from exc
    return proc.stdout.strip()


def _git_head_commit(*, allow_pending: bool = False) -> str:
    """Return the 7-char short SHA at HEAD.

    Streamlining cleanup (2026-07-07): the old behavior silently returned
    ``"pending"`` on ANY git failure, writing ratchet rows that cannot be
    mapped back to a revision. Now a git failure raises ``SetupError`` on
    the experiment/baseline paths (fail loudly, keep the ratchet
    auditable); only best-effort accounting rows (``_record_halt_row``)
    pass ``allow_pending=True`` to keep the old fallback.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            check=True,
            timeout=10,
        )
        sha = proc.stdout.strip()
        if _TSV_COMMIT_RE.match(sha) and sha != "pending":
            return sha
        failure = f"unparseable git HEAD sha: {sha!r}"
    except (
        subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired,
    ) as exc:
        failure = f"git rev-parse HEAD failed: {exc}"
    if allow_pending:
        return "pending"
    raise SetupError(
        f"{failure} — refusing to write an experiment row that cannot be "
        "mapped to a commit (the git ratchet depends on it)"
    )


def _parse_tag_from_branch(branch: str) -> str | None:
    """Extract the tag from an ``autoresearch/<tag>`` branch name.

    Returns ``None`` if the branch is not in the autoresearch namespace.
    Used by the setup phase to surface the run tag in logs and the
    strategy memo header.
    """
    if not _AUTORESEARCH_BRANCH_RE.match(branch):
        return None
    return branch.split("/", 1)[1]


def _assert_autoresearch_branch() -> str:
    """Refuse to proceed unless on an ``autoresearch/<tag>`` branch (R-703).

    Returns the branch name on success.  Raises ``SetupError`` with a
    message that explains the AUTORESEARCH_MECHANICS.md branch rule.
    """
    branch = _git_current_branch()
    if not _AUTORESEARCH_BRANCH_RE.match(branch):
        raise SetupError(
            f"current branch {branch!r} is not an autoresearch branch. "
            "Per AUTORESEARCH_MECHANICS.md 'The Git Ratchet', the experiment "
            "loop runs only on a branch named 'autoresearch/<tag>' cut from "
            "a clean main. Run: git checkout -b autoresearch/<tag>"
        )
    return branch


# ---------------------------------------------------------------------------
# Experiment log TSV I/O (R-716 .. R-722)
# ---------------------------------------------------------------------------
def _experiment_log_path() -> Path:
    """Return the TSV path.  Tests can set ``EXPERIMENT_LOG_PATH`` env var
    to redirect to a tempdir."""
    override = os.environ.get("EXPERIMENT_LOG_PATH")
    return Path(override) if override else _EXPERIMENT_LOG_PATH


def _sanitize_description(raw: str) -> str:
    """Strip tabs / newlines / NULs and truncate per R-718.

    AUTORESEARCH_MECHANICS.md L309-310: "no tabs, no commas in description".
    We keep commas (we are TSV, not CSV) but normalize whitespace and cap
    length.  Tabs and newlines would corrupt the parser.
    """
    if raw is None:
        return ""
    s = str(raw)
    # Collapse any whitespace run (incl. tab, newline, CR) to a single space.
    s = re.sub(r"[\t\r\n\x00]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > _TSV_DESCRIPTION_MAX_LEN:
        s = s[: _TSV_DESCRIPTION_MAX_LEN - 1].rstrip() + "…"
    return s


def _validate_log_row(row: Mapping[str, Any]) -> dict[str, str]:
    """Validate a candidate TSV row and return the stringified column tuple.

    Raises ``ValueError`` per R-719 for any schema violation.  Numeric
    coercion happens here so the writer never sees a non-string value.
    """
    out: dict[str, str] = {}

    commit = str(row.get("commit", "")).strip()
    if not _TSV_COMMIT_RE.match(commit):
        raise ValueError(
            f"commit must match {_TSV_COMMIT_RE.pattern!r} (got {commit!r})"
        )
    out["commit"] = commit

    metric_raw = row.get("metric")
    if isinstance(metric_raw, bool) or not isinstance(metric_raw, int):
        # Reject booleans and non-int (incl. floats) per R-719.
        raise ValueError(f"metric must be int (got {type(metric_raw).__name__})")
    if metric_raw < 0:
        raise ValueError(f"metric must be non-negative (got {metric_raw})")
    out["metric"] = str(metric_raw)

    confidence_raw = row.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"confidence must be a float (got {confidence_raw!r})") from exc
    if not math.isfinite(confidence) or confidence < 0:
        raise ValueError(
            f"confidence must be finite and non-negative (got {confidence!r})"
        )
    out["confidence"] = f"{confidence:.2f}"

    api_calls = row.get("api_calls", 0)
    if isinstance(api_calls, bool) or not isinstance(api_calls, int):
        raise ValueError(f"api_calls must be int (got {type(api_calls).__name__})")
    if api_calls < 0:
        raise ValueError(f"api_calls must be non-negative (got {api_calls})")
    out["api_calls"] = str(api_calls)

    wc_raw = row.get("wall_clock_min", 0.0)
    try:
        wc = float(wc_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"wall_clock_min must be a float (got {wc_raw!r})") from exc
    if not math.isfinite(wc) or wc < 0:
        raise ValueError(
            f"wall_clock_min must be finite and non-negative (got {wc!r})"
        )
    out["wall_clock_min"] = f"{wc:.1f}"

    status = str(row.get("status", "")).strip()
    if status not in _TSV_STATUSES:
        raise ValueError(
            f"status must be in {sorted(_TSV_STATUSES)} (got {status!r})"
        )
    out["status"] = status

    out["description"] = _sanitize_description(row.get("description", ""))
    return out


def read_experiment_log(path: Path | str | None = None) -> list[dict[str, str]]:
    """Read the experiment log TSV and return rows as dicts (R-722).

    Returns an empty list if the file does not exist or contains only the
    header.  The reader skips a leading row that exactly matches
    ``_TSV_COLUMNS`` so cross-run accumulated logs (R-720) parse cleanly.
    """
    log_path = Path(path) if path is not None else _experiment_log_path()
    if not log_path.exists():
        return []
    rows: list[dict[str, str]] = []
    with log_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        first = True
        for raw in reader:
            if not raw:
                continue
            if first:
                first = False
                if tuple(raw) == _TSV_COLUMNS:
                    continue
            if len(raw) != len(_TSV_COLUMNS):
                # Skip malformed rows rather than crashing the loop driver.
                # The strategy memo can flag the corruption out-of-band.
                log.warning(
                    "experiment_log.tsv row has %d columns (expected %d): %r",
                    len(raw), len(_TSV_COLUMNS), raw,
                )
                continue
            rows.append(dict(zip(_TSV_COLUMNS, raw)))
    return rows


def append_experiment_log_row(
    row: Mapping[str, Any],
    path: Path | str | None = None,
) -> None:
    """Append a single validated row to the TSV (R-716 .. R-721).

    - Bootstrap: if the file does not exist or is empty, write the header
      first (R-717).
    - Validate: every column is schema-checked and stringified per R-719.
    - Atomic single-line append + ``flush`` + ``fsync`` (R-721).
    - APPEND-ONLY: opens with ``"a"``, never ``"w"`` (R-716).
    """
    log_path = Path(path) if path is not None else _experiment_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    validated = _validate_log_row(row)
    line_values = [validated[col] for col in _TSV_COLUMNS]
    line = "\t".join(line_values) + "\n"

    needs_header = (not log_path.exists()) or log_path.stat().st_size == 0
    header_line = "\t".join(_TSV_COLUMNS) + "\n"

    # Open for append in binary mode so a single os.write delivers the
    # bytes atomically on Linux (PIPE_BUF guarantee for writes <= 4096B).
    payload = (header_line + line) if needs_header else line
    fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Experiment-log durability mirror (prepare-mutation 2026-07-08;
# reviews/17_tsv_mirror/). The TSV above is CANONICAL; the mirror exists so
# container reclaim cannot destroy the firm's experimental history.
# Contract (gates G1-G3): mirror calls NEVER raise, NEVER block
# unboundedly, and ALWAYS run AFTER the TSV append at every call site —
# the mirror may lag the TSV (warned), but must never contain a live row
# the canonical log lacks. The mirror is never a decision input: anchor
# selection (_last_baseline_or_keep) reads the TSV only (SR-15).
# ---------------------------------------------------------------------------
_SQL_INSERT_LOG_MIRROR = (
    "INSERT INTO experiment_log_mirror "
    "(source, run_tag, experiment_id, commit_hash, metric, confidence, "
    "api_calls, wall_clock_min, status, description) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
# Backfill dedup is COUNT-based over the full 7-column row value (R-M7):
# legitimately identical TSV rows exist (repeated outer-crash or halt rows
# carry no exp= token) and must each backfill exactly once.
_SQL_COUNT_MIRROR_ROWS = (
    "SELECT COUNT(*) FROM experiment_log_mirror "
    "WHERE commit_hash = %s AND metric = %s AND confidence = %s "
    "AND api_calls = %s AND wall_clock_min = %s AND status = %s "
    "AND description = %s"
)
# Restore reads in entry_id order — the authoritative ordering (logged_at
# lies for backfilled rows, R-M13). This constant and the COUNT above are
# the ONLY pipeline reads of the mirror (SR-15 fence; enforced by
# TestMirrorStaticGuards in tests/test_mirror.py).
_SQL_SELECT_MIRROR_FOR_RESTORE = (
    "SELECT commit_hash, metric, confidence, api_calls, wall_clock_min, "
    "status, description FROM experiment_log_mirror ORDER BY entry_id"
)
# Serializes operator backfills (R-M7 concurrent-backfill race).
_SQL_BACKFILL_ADVISORY_LOCK = "SELECT pg_advisory_xact_lock(724500108)"
# Bounds lock-waits and slow statements server-side to 5s (R-M3); connect
# is bounded by connect_timeout=10 in prepare.get_connection. A true
# network partition after connect is still bounded only by TCP timeouts —
# the same accepted exposure class as every pre-existing DB call in the
# loop (adversarial review F4). SET LOCAL scopes the timeout to the
# mirror's own transaction.
_MIRROR_STATEMENT_TIMEOUT = "SET LOCAL statement_timeout = '5s'"
# Kill switch (R-M2): tests/__init__.py sets this for the whole offline
# suite so loop tests on developer machines with a real .env never open
# connections or write to the live mirror (SR-6). Operators may also set
# it to run mirror-less.
_MIRROR_DISABLE_ENV_VAR = "EXPERIMENT_LOG_MIRROR_DISABLE"

# R-M8: forensic marker parsing for BACKFILL only. Distinct from
# _DESCRIPTION_RUN_RE (anchor selection, first-match, defined below) on
# purpose: crash descriptions embed free error text BEFORE the trailing
# run=/exp= tokens, so backfill must take the LAST match and validate the
# shape — a truncated or forged token becomes NULL, never a wrong value.
_DESC_RUN_TOKEN_RE = re.compile(r"run=([^\s|]+)")
_DESC_EXP_TOKEN_RE = re.compile(r"exp=([^\s|]+)")
_RUN_TAG_SHAPE_RE = re.compile(r"^[a-z0-9._-]+$")
_EXPERIMENT_ID_SHAPE_RE = re.compile(r"^exp-\d{8}T\d{6}Z-[0-9a-f]{6}$")


def _parse_markers_from_description(description: str) -> tuple[str | None, str | None]:
    """Best-effort ``(run_tag, experiment_id)`` from a TSV description.

    Backfill annotation only — live rows thread the real values
    explicitly (F2 precedent) and are authoritative. Last match wins;
    shape-validation failures (mid-token truncation by the 200-char
    description cap, error text that happens to contain ``run=``) yield
    ``None`` rather than a wrong value (R-M8).
    """
    text = description or ""
    run_tag: str | None = None
    experiment_id: str | None = None
    runs = _DESC_RUN_TOKEN_RE.findall(text)
    if runs and _RUN_TAG_SHAPE_RE.match(runs[-1]):
        run_tag = runs[-1]
    exps = _DESC_EXP_TOKEN_RE.findall(text)
    if exps and _EXPERIMENT_ID_SHAPE_RE.match(exps[-1]):
        experiment_id = exps[-1]
    return run_tag, experiment_id


def _mirror_log_row(
    row: Mapping[str, Any],
    *,
    run_tag: str | None = None,
    experiment_id: str | None = None,
    source: str = "live",
) -> bool:
    """Best-effort INSERT of one TSV row into ``experiment_log_mirror``.

    G1: never raises — including ``SystemExit`` from the DSN path
    (R-M1) — and never blocks unboundedly (connect_timeout in
    prepare.get_connection + SET LOCAL statement_timeout, R-M3). G3:
    callers invoke this AFTER ``append_experiment_log_row`` succeeded, so
    the mirror can never contain a live row the canonical TSV lacks.
    Returns True only when the row was durably committed; False otherwise
    (loud warning, never fatal — the TSV remains canonical either way).

    Inserts the ``_validate_log_row`` OUTPUT — sanitized and canonically
    formatted — not the caller's raw dict, so mirror rows are value-equal
    to the TSV line that was just written (R-M6). ``run_tag`` /
    ``experiment_id`` are threaded explicitly by callers, never derived
    here (F2 precedent).
    """
    if os.environ.get(_MIRROR_DISABLE_ENV_VAR):
        return False
    try:
        if not prepare.dsn_available():
            log.warning(
                "experiment_log_mirror skipped: no DATABASE_URL "
                "(TSV remains canonical)"
            )
            return False
        validated = _validate_log_row(row)
        params = (
            source,
            run_tag,
            experiment_id,
            validated["commit"],
            int(validated["metric"]),
            Decimal(validated["confidence"]),
            int(validated["api_calls"]),
            Decimal(validated["wall_clock_min"]),
            validated["status"],
            validated["description"],
        )
        with prepare.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_MIRROR_STATEMENT_TIMEOUT)
                cur.execute(_SQL_INSERT_LOG_MIRROR, params)
            conn.commit()
        return True
    except BaseException:  # noqa: BLE001 — G1: SystemExit included (R-M1)
        log.warning(
            "experiment_log_mirror insert failed; TSV remains canonical",
            exc_info=True,
        )
        return False


def _coerce_tsv_row(raw: Mapping[str, str]) -> dict[str, str]:
    """Coerce a ``read_experiment_log`` string row back into the typed
    shape ``_validate_log_row`` expects, returning the validated dict.
    Raises ``ValueError`` on garbage — backfill counts and skips such
    rows rather than aborting (a corrupted TSV line must not block the
    durability of every other line)."""
    try:
        return _validate_log_row(
            {
                "commit": raw.get("commit", ""),
                "metric": int(raw.get("metric", "")),
                "confidence": float(raw.get("confidence", "")),
                "api_calls": int(raw.get("api_calls", "")),
                "wall_clock_min": float(raw.get("wall_clock_min", "")),
                "status": raw.get("status", ""),
                "description": raw.get("description", ""),
            }
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unbackfillable TSV row: {exc}") from exc


def backfill_experiment_log_mirror(
    path: Path | str | None = None,
) -> dict[str, int]:
    """Operator one-shot (``make mirror-backfill``): reconcile TSV → mirror.

    COUNT-based per full 7-column row value (R-M7): for each distinct
    tuple, insert ``tsv_count − mirror_count`` copies in TSV order, so
    identical legitimate rows each backfill once and re-runs are
    idempotent. Runs in ONE transaction behind ``pg_advisory_xact_lock``
    (concurrent backfills serialize). Parsed ``run=``/``exp=`` markers are
    best-effort annotations (R-M8); live-threaded values on
    ``source='live'`` rows are the authoritative ones.

    While the TSV is alive this doubles as the SR-16 divergence canary:
    ``mirror_only > 0`` with an intact TSV means something wrote the
    mirror out-of-band. Unlike ``_mirror_log_row``, this is an operator
    tool and fails LOUDLY (raises) when the database is unreachable.
    """
    rows = read_experiment_log(path)
    summary = {
        "tsv_rows": len(rows),
        "inserted": 0,
        "already_present": 0,
        "mirror_only": 0,
        "invalid": 0,
    }
    ordered: list[dict[str, str]] = []
    for raw in rows:
        try:
            ordered.append(_coerce_tsv_row(raw))
        except ValueError:
            summary["invalid"] += 1
            log.warning("backfill skipping unparseable TSV row: %r", raw)
    if not ordered:
        return summary

    def _key(v: Mapping[str, str]) -> tuple:
        return tuple(v[c] for c in _TSV_COLUMNS)

    def _count_params(key: tuple) -> tuple:
        commit, metric, confidence, api_calls, wall_clock, status, desc = key
        return (
            commit,
            int(metric),
            Decimal(confidence),
            int(api_calls),
            Decimal(wall_clock),
            status,
            desc,
        )

    tsv_counts: Counter = Counter(_key(v) for v in ordered)
    with prepare.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_BACKFILL_ADVISORY_LOCK)
            need: dict[tuple, int] = {}
            for key, tsv_n in tsv_counts.items():
                cur.execute(_SQL_COUNT_MIRROR_ROWS, _count_params(key))
                mirror_n = int(cur.fetchone()[0])
                need[key] = max(0, tsv_n - mirror_n)
                summary["already_present"] += min(tsv_n, mirror_n)
                if mirror_n > tsv_n:
                    summary["mirror_only"] += mirror_n - tsv_n
            for v in ordered:
                key = _key(v)
                if need.get(key, 0) <= 0:
                    continue
                need[key] -= 1
                run_tag, experiment_id = _parse_markers_from_description(
                    v["description"]
                )
                cur.execute(
                    _SQL_INSERT_LOG_MIRROR,
                    (
                        "backfill",
                        run_tag,
                        experiment_id,
                        v["commit"],
                        int(v["metric"]),
                        Decimal(v["confidence"]),
                        int(v["api_calls"]),
                        Decimal(v["wall_clock_min"]),
                        v["status"],
                        v["description"],
                    ),
                )
                summary["inserted"] += 1
        conn.commit()
    return summary


def restore_experiment_log_from_mirror(
    path: Path | str | None = None,
) -> dict[str, int]:
    """Disaster recovery (``make mirror-restore``): rebuild a MISSING
    ``experiment_log.tsv`` from the mirror, in ``entry_id`` order, through
    the validated TSV writer (R-M10).

    Refuses to touch an existing non-empty TSV (SR-13 append-only) —
    restore is for the container-reclaim case where the file is GONE;
    reconciling a live file is ``backfill_experiment_log_mirror``'s job,
    in the other direction. Restored history is only as trustworthy as
    the mirror (SR-16): rows failing TSV validation are skipped and
    counted, never written.
    """
    log_path = Path(path) if path is not None else _experiment_log_path()
    if log_path.exists() and log_path.stat().st_size > 0:
        raise RuntimeError(
            f"{log_path} exists and is non-empty; restore refuses to touch "
            "a live TSV. To reconcile a live TSV into the mirror, run "
            "backfill_experiment_log_mirror (make mirror-backfill) instead. "
            "If the file is a hand-created header-only stub, delete it "
            "first (adversarial review F7)."
        )
    with prepare.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_SELECT_MIRROR_FOR_RESTORE)
            mirror_rows = cur.fetchall()
    summary = {
        "mirror_rows": len(mirror_rows),
        "rows_written": 0,
        "rows_skipped": 0,
    }
    for (
        commit_hash,
        metric,
        confidence,
        api_calls,
        wall_clock_min,
        status,
        description,
    ) in mirror_rows:
        try:
            append_experiment_log_row(
                {
                    "commit": str(commit_hash),
                    "metric": int(metric),
                    "confidence": float(confidence),
                    "api_calls": int(api_calls),
                    "wall_clock_min": float(wall_clock_min),
                    "status": str(status),
                    "description": str(description),
                },
                log_path,
            )
            summary["rows_written"] += 1
        except (TypeError, ValueError):
            summary["rows_skipped"] += 1
            log.warning("restore skipped an invalid mirror row", exc_info=True)
    return summary


# ---------------------------------------------------------------------------
# Keep-or-revert decision (R-713 .. R-715)
# ---------------------------------------------------------------------------
def apply_keep_or_revert_decision(
    *,
    prior_metric: int | None,
    prior_confidence: float | None,
    new_metric: int,
    new_confidence: float,
    status: str,
) -> str:
    """Pure function implementing AUTORESEARCH_MECHANICS.md 'Keep-or-Revert'.

    R-713: deterministic, side-effect-free, fully unit-tested.
    R-714: confidence tiebreaker uses STRICT ``>``.  Equal confidence on a
           tied metric reverts (Karpathy simplicity criterion).
    R-715: float comparison on confidence uses ``math.isclose`` to absorb
           ULP-level noise.

    Inputs:
        ``status``           -- evaluator's status: 'ok', 'crash', 'timeout'.
        ``prior_metric``     -- last 'baseline' or 'keep' row's metric, or
                                None for the very first row.
        ``prior_confidence`` -- same.
        ``new_metric``       -- the just-computed metric.
        ``new_confidence``   -- the just-computed confidence.

    Returns one of: ``'baseline'``, ``'keep'``, ``'discard'``, ``'crash'``,
    ``'timeout'``.  The caller writes this back into the TSV row's
    ``status`` field.
    """
    if status == "crash":
        return "crash"
    if status == "timeout":
        return "timeout"
    if status not in {"ok"}:
        raise ValueError(
            f"unrecognised evaluator status {status!r}; expected one of "
            "{'ok', 'crash', 'timeout'}"
        )

    if prior_metric is None or prior_confidence is None:
        return "baseline"

    if new_metric > prior_metric:
        return "keep"

    if new_metric < prior_metric:
        return "discard"

    # Metrics are equal.  Use confidence as a STRICT tiebreaker (R-714).
    confidence_equal = math.isclose(
        new_confidence, prior_confidence, rel_tol=1e-9, abs_tol=1e-9
    )
    if confidence_equal:
        return "discard"
    if new_confidence > prior_confidence:
        return "keep"
    return "discard"


_DESCRIPTION_RUN_RE = re.compile(r"(?:^|\s)run=([a-z0-9._-]+)")


def _row_run_tag(row: Mapping[str, str]) -> str | None:
    """Extract the ``run=<tag>`` marker from a TSV row's description.

    Tier-2 review F1: the TSV accumulates across runs by design
    (AUTORESEARCH_MECHANICS.md "Cross-Run Aggregation"), so baseline
    detection and anchor selection must be scoped to the CURRENT run or a
    new run silently anchors against a prior, non-comparable run's metric
    and never establishes its own baseline. Rows written before this fix
    carry no marker and return ``None`` — they belong to no current run,
    which forces a fresh baseline exactly as the mutation protocol
    requires.
    """
    match = _DESCRIPTION_RUN_RE.search(row.get("description", "") or "")
    return match.group(1) if match else None


def _last_baseline_or_keep(
    rows: Sequence[Mapping[str, str]], run_tag: str | None = None
) -> dict[str, str] | None:
    """Find the most recent ``baseline`` or ``keep`` row (the prior
    metric anchor for the next decision).  Returns ``None`` if no anchor
    exists yet (i.e., we have not even baselined).

    With ``run_tag`` set, only rows stamped ``run=<tag>`` anchor — a
    prior run's rows are not comparable (F1). ``run_tag=None`` keeps the
    legacy unscoped scan for ad-hoc/direct callers.
    """
    for r in reversed(rows):
        if r.get("status") in {"baseline", "keep"}:
            if run_tag is not None and _row_run_tag(r) != run_tag:
                continue
            return dict(r)
    return None


# ---------------------------------------------------------------------------
# verify_setup (AUTORESEARCH_MECHANICS Setup Step 4)
# ---------------------------------------------------------------------------
def _check_db_connection() -> dict[str, Any]:
    """R-708 / Setup Step 4a -- DB ping with PostGIS sanity check."""
    try:
        with prepare.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT POSTGIS_VERSION()")
                row = cur.fetchone()
        return {
            "status": "ok",
            "postgis_version": row[0] if row else None,
        }
    except Exception as exc:
        return {"status": "fail", "error": str(exc)}


def _check_corridor_bbox(market: str) -> dict[str, Any]:
    """R-707 / Setup Step 4d -- informational corridor bbox seed check."""
    try:
        with prepare.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM submarkets s "
                    "JOIN markets m ON s.market_id = m.market_id "
                    "WHERE m.market_id = %s AND s.bbox IS NOT NULL",
                    (market,),
                )
                row = cur.fetchone()
        count = int(row[0]) if row and row[0] is not None else 0
        return {
            "status": "ok" if count > 0 else "warning",
            "seeded_count": count,
            "note": (
                None
                if count > 0
                else f"no corridor bbox seeded for market={market!r}; "
                "scoring sub-scores depending on submarket lookup will be null"
            ),
        }
    except Exception as exc:
        return {"status": "fail", "error": str(exc)}


def _check_costar_freshness() -> dict[str, Any]:
    """R-706 / Setup Step 4c -- CoStar staleness check (informational)."""
    if not _COSTAR_BASE_DIR.exists():
        return {
            "status": "warning",
            "note": (
                f"CoStar export directory {_COSTAR_BASE_DIR} does not exist; "
                "AUTORESEARCH_MECHANICS.md permits baselining with stale or "
                "missing CoStar data, the strategy memo will flag the gap"
            ),
        }
    cutoff = time.time() - _COSTAR_STALENESS_DAYS * 86400
    fresh: list[str] = []
    for sub in _COSTAR_BASE_DIR.iterdir():
        if not sub.is_dir():
            continue
        for f in sub.glob("*.csv"):
            try:
                if f.stat().st_mtime >= cutoff:
                    fresh.append(f.name)
            except OSError:
                continue
    return {
        "status": "ok" if fresh else "warning",
        "fresh_files": len(fresh),
        "note": (
            None
            if fresh
            else f"no CoStar exports within the last {_COSTAR_STALENESS_DAYS} "
            "days; the strategy memo will flag the staleness"
        ),
    }


# Streamlining cleanup (2026-07-07): verify_setup runs every loop iteration;
# serve positive harness verdicts from a 15-minute cache instead of live-
# probing county endpoints each time (failing verdicts always re-probe).
_HARNESS_HEALTH_TTL_SECONDS = 15 * 60


def _check_harness_for_market(market: str) -> dict[str, Any]:
    """R-708 / Setup Step 4b -- harness gate for at least one county."""
    counties = research._MARKET_TO_COUNTIES.get(market, [])
    if not counties:
        return {
            "status": "fail",
            "error": f"market={market!r} has no configured counties",
        }
    per_county: dict[str, str] = {}
    overall = "ok"
    for county in counties:
        try:
            harness_status, _ = research._harness_gate(
                county, cache_ttl_seconds=_HARNESS_HEALTH_TTL_SECONDS,
            )
        except Exception as exc:
            per_county[county] = f"error: {exc}"
            overall = "fail"
            continue
        per_county[county] = harness_status
        if harness_status == "failing":
            overall = "fail"
        elif harness_status == "degraded" and overall == "ok":
            overall = "warning"
    return {"status": overall, "per_county": per_county}


def verify_setup(market: str) -> dict[str, Any]:
    """Run every programmatic Setup Step 4 sub-check and aggregate.

    Returns a dict with keys ``status`` (``ok`` | ``warning`` | ``fail``),
    ``branch``, ``tag``, and per-check sub-dicts.  ``status='fail'`` means
    the loop must not start; ``status='warning'`` means proceed with a
    flag in the strategy memo; ``status='ok'`` is fully green.

    R-705 -- idempotent.  Calling twice on a healthy environment returns
    equivalent shapes.
    """
    branch = _git_current_branch()
    is_autoresearch = bool(_AUTORESEARCH_BRANCH_RE.match(branch))
    tag = _parse_tag_from_branch(branch) if is_autoresearch else None

    db = _check_db_connection()
    harness = _check_harness_for_market(market)
    bbox = _check_corridor_bbox(market) if db["status"] == "ok" else {
        "status": "skipped", "note": "DB unreachable; bbox check skipped",
    }
    costar = _check_costar_freshness()

    statuses = [db["status"], harness["status"], bbox["status"], costar["status"]]
    if not is_autoresearch:
        statuses.append("fail")
    if any(s == "fail" for s in statuses):
        overall = "fail"
    elif any(s == "warning" for s in statuses):
        overall = "warning"
    else:
        overall = "ok"

    return {
        "status": overall,
        "branch": branch,
        "tag": tag,
        "is_autoresearch_branch": is_autoresearch,
        "checks": {
            "db": db,
            "harness": harness,
            "corridor_bbox": bbox,
            "costar_freshness": costar,
        },
    }


# ---------------------------------------------------------------------------
# evaluate(market) -- one full cycle + metric read (R-701, R-709, R-710)
# ---------------------------------------------------------------------------
def _make_experiment_id() -> str:
    """Unique id stamped on every parcel_scores row an experiment writes.

    prepare-mutation (2026-07-07): the id is how a discarded experiment's
    rows are found and purged, so the data ratchet mirrors the git ratchet.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"exp-{ts}-{secrets.token_hex(3)}"


def evaluate(
    market: str,
    *,
    skip_ingestion: bool = False,
    skip_discovery: bool = False,
    skip_scoring: bool = False,
    skip_memo: bool = False,
    run_tag: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """Run one full Karpathy 'evaluate' cycle and return the metric.

    Order (R-709): ingestion -> discovery -> scoring -> memo -> metric.
    Each sub-cycle uses its own connection (existing public-API contract);
    R-710 documents that this is intentionally non-transactional across
    sub-cycles.

    R-701: metric read goes through ``prepare.calculate_actionable_pipeline_count``
    and ``prepare.calculate_confidence_weighted_pipeline``.  No reimplementation.
    R-702: ``prepare.verify_parameters_unchanged`` runs at the start.

    prepare-mutation (2026-07-07): ``run_tag`` (None -> derived from the
    current git branch) scopes both the scoring cycle and the metric read
    to THIS RUN's rows. ``experiment_id`` (None -> generated) is stamped
    on every parcel_scores row this evaluate writes, so the loop can purge
    the rows of a discarded/crashed/timed-out experiment.

    The ``skip_*`` flags exist for the test suite -- callers in production
    should leave them False.

    Returns a dict shaped for direct consumption by
    ``append_experiment_log_row`` after the loop driver fills in
    ``commit`` and ``description``.
    """
    prepare.verify_parameters_unchanged()

    if run_tag is None:
        run_tag = prepare.current_run_tag()
    if experiment_id is None:
        experiment_id = _make_experiment_id()

    started = time.monotonic()
    api_calls_before = research.get_api_call_count()
    started_iso = datetime.now(timezone.utc).isoformat()
    log.info(
        "evaluate.start market=%s started_at=%s run_tag=%s experiment_id=%s "
        "skip_ingestion=%s skip_discovery=%s skip_scoring=%s skip_memo=%s",
        market, started_iso, run_tag, experiment_id,
        skip_ingestion, skip_discovery, skip_scoring, skip_memo,
    )

    sub_summaries: dict[str, Any] = {}
    status = "ok"
    error: str | None = None

    try:
        if not skip_ingestion:
            sub_summaries["ingestion"] = costar_ingest.run_ingestion_cycle()

        if not skip_discovery:
            sub_summaries["discovery"] = research.run_discovery_cycle(market)

        if not skip_scoring:
            sub_summaries["scoring"] = research.run_scoring_cycle(
                market, run_tag=run_tag, experiment_id=experiment_id,
            )

        if not skip_memo:
            try:
                memo_path = reporting.generate_strategy_memo(market)
                sub_summaries["memo"] = {"path": str(memo_path)}
            except Exception:
                # Memo failure is non-fatal -- the metric is still readable.
                # Log and continue so the loop captures the metric movement.
                log.exception("memo generation failed; continuing to metric read")
                sub_summaries["memo"] = {"path": None, "failed": True}

        with prepare.get_connection() as conn:
            # Tier-2 review F3(b) — stamp audit. The purge trusts
            # experiment_id stamps written by the sandbox; a research.py
            # edit that mis-stamps rows would make a discarded experiment's
            # rows survive the purge. Count rows written during THIS
            # evaluate that carry the wrong stamp and surface the number
            # loudly (log + TSV description). Nonzero means the sandbox is
            # drifting from the contract — halt and inspect the exp commits.
            stamp_violations = 0
            if run_tag is not None:
                try:
                    with conn.cursor() as cur:
                        cur.execute(_SQL_STAMP_AUDIT, (run_tag, started_iso, experiment_id))
                        audit_row = cur.fetchone()
                    stamp_violations = int(audit_row[0]) if audit_row and audit_row[0] else 0
                except Exception:
                    log.exception("stamp audit query failed; continuing")
                if stamp_violations:
                    log.error(
                        "STAMP AUDIT: %d parcel_scores rows written during "
                        "experiment %s carry a different/NULL experiment_id — "
                        "possible purge evasion; inspect the exp commit diff",
                        stamp_violations, experiment_id,
                    )
            metric = prepare.calculate_actionable_pipeline_count(
                conn, run_tag=run_tag,
            )
            confidence = prepare.calculate_confidence_weighted_pipeline(
                conn, run_tag=run_tag,
            )
    except prepare.BudgetExceeded:
        status = "timeout"
        metric = 0
        confidence = 0.0
        stamp_violations = 0
        error = "budget_exceeded"
        log.exception("evaluate.timeout market=%s", market)
    except Exception as exc:  # pylint: disable=broad-except
        status = "crash"
        metric = 0
        confidence = 0.0
        stamp_violations = 0
        error = f"{type(exc).__name__}: {exc}"
        log.exception("evaluate.crash market=%s", market)

    elapsed = time.monotonic() - started
    elapsed_min = elapsed / 60.0
    log.info(
        "evaluate.end market=%s status=%s metric=%s confidence=%.2f "
        "wall_clock_min=%.1f",
        market, status, metric, confidence, elapsed_min,
    )

    return {
        "market": market,
        "status": status,
        "metric": int(metric),
        "confidence": float(confidence),
        # R-712 closed (2026-07-07): real count of outbound discovery HTTP
        # attempts during this evaluate (soft-constraint signal for the log).
        "api_calls": research.get_api_call_count() - api_calls_before,
        "wall_clock_min": float(elapsed_min),
        "sub_summaries": sub_summaries,
        "error": error,
        "started_at": started_iso,
        "run_tag": run_tag,
        "experiment_id": experiment_id,
        "stamp_audit_violations": stamp_violations,
    }


# ---------------------------------------------------------------------------
# Experiment purge (prepare-mutation 2026-07-07) — the data half of revert
# ---------------------------------------------------------------------------
_SQL_DELETE_SCORES_FOR_EXPERIMENT = (
    "DELETE FROM parcel_scores WHERE experiment_id = %s"
)

# F3(b): rows written to this run during the current evaluate window whose
# experiment_id is not the one this evaluate stamped (NULL included).
_SQL_STAMP_AUDIT = (
    "SELECT COUNT(*) FROM parcel_scores "
    "WHERE run_tag = %s AND scored_at >= %s "
    "AND experiment_id IS DISTINCT FROM %s"
)

_SQL_INSERT_RESEARCH_LOG_PURGE = (
    "INSERT INTO research_log (cycle_id, action_type, market, notes) "
    "VALUES (%s, %s, %s, %s)"
)


def _purge_experiment_scores(
    experiment_id: str, market: str, decision: str
) -> int:
    """Delete a non-kept experiment's parcel_scores rows.

    `git reset --hard` reverts the CODE of a discarded experiment;
    this purge reverts its DATA. Without it, a discarded experiment's
    scores persist and inflate every subsequent metric read, breaking the
    single-change attribution the git ratchet exists to provide. Runs in
    its own connection/transaction; the deletion is logged to
    research_log (action_type='experiment_purge'). Returns rows deleted.

    Purges ONLY rows stamped with this experiment's id — baseline rows,
    kept experiments' rows, and ad-hoc (experiment_id NULL) rows are
    untouchable by construction.
    """
    with prepare.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_DELETE_SCORES_FOR_EXPERIMENT, (experiment_id,))
            deleted = cur.rowcount if cur.rowcount is not None else 0
        with conn.cursor() as cur:
            cur.execute(
                _SQL_INSERT_RESEARCH_LOG_PURGE,
                (
                    experiment_id,
                    "experiment_purge",
                    market,
                    f"decision={decision}; deleted {deleted} parcel_scores "
                    f"rows for experiment_id={experiment_id}",
                ),
            )
        conn.commit()
    log.info(
        "purged %d parcel_scores rows for %s experiment %s",
        deleted, decision, experiment_id,
    )
    return int(deleted)


# ---------------------------------------------------------------------------
# Baseline experiment (Setup Step 5)
# ---------------------------------------------------------------------------
def run_baseline_experiment(
    market: str, *, run_tag: str | None = None
) -> dict[str, Any]:
    """Run the baseline experiment per AUTORESEARCH_MECHANICS.md L108.

    The baseline is ONE complete evaluate() against unmodified research.py.
    The result is written to the TSV with ``status=baseline`` and a
    ``run=<tag>`` marker so baseline detection is per-run (F1).  The
    composite of the baseline metric and the head commit are returned.

    Caller is responsible for invoking this ONCE per autoresearch branch
    before the experiment loop begins.  ``experiment_loop`` calls this
    automatically when no baseline row FOR THE CURRENT RUN exists in the
    TSV (the file accumulates across runs by design).
    """
    if run_tag is None:
        # Derive cwd-pinned from THIS repo's branch (F2); off-branch direct
        # calls proceed unscoped with a loud warning.
        run_tag = _parse_tag_from_branch(_git_current_branch())
        if run_tag is None:
            log.warning(
                "run_baseline_experiment called outside an autoresearch/<tag> "
                "branch; the baseline row will carry no run marker and the "
                "metric read is UNSCOPED (informational only)"
            )
    result = evaluate(market, run_tag=run_tag)
    decision = apply_keep_or_revert_decision(
        prior_metric=None,
        prior_confidence=None,
        new_metric=result["metric"],
        new_confidence=result["confidence"],
        status=result["status"],
    )
    # prepare-mutation (2026-07-07): a crashed/timed-out baseline attempt
    # must not leave partial rows behind — the retry needs a clean slate.
    if decision in {"crash", "timeout"} and result.get("experiment_id"):
        try:
            _purge_experiment_scores(result["experiment_id"], market, decision)
        except Exception:
            log.exception(
                "purge failed for baseline attempt %s", result["experiment_id"],
            )
    description = f"baseline | market={market}"
    if run_tag:
        description += f" | run={run_tag}"
    if result.get("experiment_id"):
        # F4: record the id so orphaned rows are discoverable from the TSV.
        description += f" | exp={result['experiment_id']}"
    row = {
        "commit": _git_head_commit(),
        "metric": result["metric"],
        "confidence": result["confidence"],
        "api_calls": result["api_calls"],
        "wall_clock_min": result["wall_clock_min"],
        "status": decision,
        "description": description,
    }
    append_experiment_log_row(row)
    # G3: mirror strictly after the canonical TSV append.
    _mirror_log_row(row, run_tag=run_tag, experiment_id=result.get("experiment_id"))
    return row


# ---------------------------------------------------------------------------
# Loop driver helpers (R-725, R-728, R-729)
# ---------------------------------------------------------------------------
def _halted() -> bool:
    """Halt sentinel detection (R-725, R-728)."""
    if os.environ.get(_HALT_ENV_VAR):
        return True
    if _HALT_SENTINEL_PATH.exists():
        return True
    return False


def _loop_lock_path() -> Path:
    override = os.environ.get(_LOOP_LOCK_ENV_VAR)
    return Path(override) if override else _LOOP_LOCK_PATH


@contextmanager
def _acquire_loop_lock() -> Iterator[None]:
    """Advisory exclusive lock on the experiment loop (R-729).

    Uses ``fcntl.flock`` with ``LOCK_NB`` so a second invocation fails
    immediately with ``LoopLockError`` rather than blocking.  Falls back
    to a no-op when fcntl is unavailable (Windows) -- in that case we
    rely on humans to run only one loop at a time.
    """
    try:
        import fcntl  # POSIX-only.
    except ImportError:  # pragma: no cover -- Windows.
        log.warning("fcntl unavailable; loop lock is a no-op on this platform")
        yield
        return

    path = _loop_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise LoopLockError(
                f"another experiment_loop is already running "
                f"(lock held at {path}); refuse to start a second concurrent loop"
            ) from exc
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
        except OSError:
            pass  # Best-effort PID write; the lock itself is what matters.
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# experiment_loop -- the NEVER STOP runner
# ---------------------------------------------------------------------------
def experiment_loop(
    market: str,
    *,
    max_iterations: int | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """The Karpathy NEVER STOP loop, Phase 10 implementation.

    Per AUTORESEARCH_MECHANICS.md "The Experiment Loop".  Runs setup,
    bootstraps a baseline if missing, then loops calling ``evaluate(market)``
    + ``apply_keep_or_revert_decision`` + ``append_experiment_log_row`` per
    iteration.

    R-723: this loop does NOT call ``git reset --hard HEAD~1`` or any
    other git-mutating subprocess.  The keep-or-revert STATUS is recorded
    in the TSV; the AGENT (Claude Code) reads the TSV after each iteration
    and performs the corresponding git operation in its own tool calls.
    Auto-reverting from a long-running Python loop is a footgun -- it
    fights the agent's own git operations.

    Halt conditions (AUTORESEARCH_MECHANICS.md L340-345):
        - explicit halt: ``.halt`` file in repo root OR
          ``EXPERIMENT_LOOP_HALT=1`` env var
        - ``max_iterations`` reached (test ergonomic; production is None)
        - ``LONG_RUN_GRACEFUL_EXIT_SECONDS`` exceeded (graceful 7-day cap)
        - ``_INFRA_FAILURE_THRESHOLD`` consecutive setup failures

    Returns a summary dict with the count and final status.
    """
    started = time.monotonic()
    iters = 0
    consecutive_setup_failures = 0

    with _acquire_loop_lock():
        # Setup Step 4 -- verify infrastructure (R-705, R-708).
        setup = verify_setup(market)
        if setup["status"] == "fail":
            raise SetupError(
                f"verify_setup failed: {json.dumps(setup, default=str)}"
            )
        run_tag = setup.get("tag")
        if not run_tag:
            # F2: the ratchet must never run unscoped. verify_setup derives
            # the tag cwd-pinned from the branch; no tag means no run.
            raise SetupError(
                "experiment_loop requires an autoresearch/<tag> branch; "
                f"current branch is {setup.get('branch')!r} (no run tag)"
            )

        # Setup Step 6 -- explicit confirmation gate.
        log_path = _experiment_log_path()
        rows = read_experiment_log(log_path)
        # F1: the TSV accumulates across runs; only a baseline stamped with
        # THIS run's tag counts. A prior run's baseline must not suppress
        # re-baselining (its metric is not comparable under the new tag).
        has_baseline = any(
            r.get("status") == "baseline" and _row_run_tag(r) == run_tag
            for r in rows
        )

        if not has_baseline:
            # Setup Step 5 -- establish baseline.  The first call to
            # experiment_loop on a fresh autoresearch branch boots the
            # baseline before iterating.  AUTORESEARCH_MECHANICS.md L114
            # requires the human to confirm the baseline before the loop
            # begins; we honour this by requiring ``confirmed=True`` OR a
            # pre-existing baseline row FOR THIS RUN.
            if not confirmed:
                raise SetupError(
                    "no baseline row for run "
                    f"'{run_tag}' in experiment_log.tsv and confirmed=False. "
                    "Per AUTORESEARCH_MECHANICS.md Setup Step 6, the human must "
                    "confirm the baseline before the loop begins. Either run "
                    "run_baseline_experiment(market) and review the result, then "
                    "call experiment_loop(market, confirmed=True), OR call "
                    "experiment_loop(market, confirmed=True) directly to bootstrap."
                )
            baseline = run_baseline_experiment(market, run_tag=run_tag)
            log.info("baseline established: %s", baseline)
            rows = read_experiment_log(log_path)

        # Main loop.
        pending_purges: list[tuple[str, str]] = []  # F4: (experiment_id, decision)
        consecutive_terminal = 0  # F5: consecutive crash/timeout iterations
        while True:
            if _halted():
                log.info("experiment_loop halt sentinel detected; exiting cleanly")
                _record_halt_row(market, "halt sentinel detected", run_tag)
                break
            if max_iterations is not None and iters >= max_iterations:
                break
            if time.monotonic() - started > _LONG_RUN_GRACEFUL_EXIT_SECONDS:
                log.info("experiment_loop graceful 7-day exit")
                _record_halt_row(market, "graceful 7-day exit", run_tag)
                break

            # F4: retry purges that failed at their own iteration boundary —
            # unpurged residue contaminates every later metric read, so keep
            # trying until the DB accepts the delete.
            still_pending: list[tuple[str, str]] = []
            for exp_id, exp_decision in pending_purges:
                try:
                    _purge_experiment_scores(exp_id, market, exp_decision)
                except Exception:
                    log.exception("purge retry failed for experiment %s", exp_id)
                    still_pending.append((exp_id, exp_decision))
            pending_purges = still_pending

            # Per-iteration setup re-check (R-731).
            iter_setup = verify_setup(market)
            if iter_setup["status"] == "fail":
                consecutive_setup_failures += 1
                log.warning(
                    "iteration %d: verify_setup=fail (%d consecutive)",
                    iters, consecutive_setup_failures,
                )
                if consecutive_setup_failures >= _INFRA_FAILURE_THRESHOLD:
                    _record_halt_row(
                        market,
                        f"infrastructure failure x{_INFRA_FAILURE_THRESHOLD}",
                        run_tag,
                    )
                    break
                # Sleep proportional to consecutive failures, capped.
                time.sleep(min(60 * consecutive_setup_failures, 300))
                continue
            consecutive_setup_failures = 0

            # Run one experiment. F2: the tag is threaded explicitly — the
            # ratchet path never depends on process cwd for its scope.
            iter_started = time.monotonic()
            try:
                result = evaluate(market, run_tag=run_tag)
            except Exception as exc:  # pylint: disable=broad-except
                # R-726: a crash inside evaluate is caught and recorded; the
                # loop keeps going.  evaluate() already catches its own
                # exceptions; this outer except is defense in depth.
                log.exception("iteration %d: outer evaluate crash", iters)
                result = {
                    "market": market,
                    "status": "crash",
                    "metric": 0,
                    "confidence": 0.0,
                    "api_calls": 0,
                    "wall_clock_min": (time.monotonic() - iter_started) / 60.0,
                    "error": f"{type(exc).__name__}: {exc}",
                    "run_tag": run_tag,
                    "experiment_id": None,
                }

            # R-733: soft per-iteration budget check.
            if result.get("wall_clock_min", 0.0) * 60 > _PHASE10_BUDGET_SECONDS:
                # The evaluator already emits status=ok in this case because
                # it lacks an internal SIGALRM.  Promote to 'timeout' here.
                if result["status"] == "ok":
                    result["status"] = "timeout"

            # Decide.
            prior = _last_baseline_or_keep(rows, run_tag=run_tag)
            decision = apply_keep_or_revert_decision(
                prior_metric=int(prior["metric"]) if prior else None,
                prior_confidence=float(prior["confidence"]) if prior else None,
                new_metric=result["metric"],
                new_confidence=result["confidence"],
                status=result["status"],
            )

            # prepare-mutation (2026-07-07): the data half of revert. The
            # agent reverts the CODE via `git reset --hard HEAD~1` (R-723);
            # the runner purges the DATA a non-kept experiment wrote.
            if decision in {"discard", "crash", "timeout"} and result.get(
                "experiment_id"
            ):
                try:
                    _purge_experiment_scores(
                        result["experiment_id"], market, decision,
                    )
                except Exception:
                    # A failed purge contaminates later metric reads; make it
                    # loud, queue a retry for the next iteration boundary
                    # (F4), and keep the loop alive (NEVER STOP).
                    log.exception(
                        "purge failed for experiment %s; queued for retry",
                        result["experiment_id"],
                    )
                    pending_purges.append((result["experiment_id"], decision))

            # F5: consecutive crash/timeout breaker. A workload that
            # deterministically exceeds the budget would otherwise purge and
            # repeat the identical run forever; treat N in a row as the
            # catastrophic-failure halt AUTORESEARCH_MECHANICS.md allows.
            if result["status"] in {"crash", "timeout"}:
                consecutive_terminal += 1
                if consecutive_terminal >= _TERMINAL_STATUS_BREAKER:
                    _record_halt_row(
                        market,
                        f"{consecutive_terminal} consecutive "
                        f"crash/timeout iterations — breaker tripped",
                        run_tag,
                    )
                    row = {
                        "commit": _git_head_commit(allow_pending=True),
                        "metric": result["metric"],
                        "confidence": result["confidence"],
                        "api_calls": result.get("api_calls", 0),
                        "wall_clock_min": result["wall_clock_min"],
                        "status": decision,
                        "description": _format_loop_description(result, run_tag),
                    }
                    append_experiment_log_row(row, log_path)
                    # G3: mirror strictly after the canonical TSV append.
                    _mirror_log_row(
                        row,
                        run_tag=run_tag,
                        experiment_id=result.get("experiment_id"),
                    )
                    iters += 1
                    break
            else:
                consecutive_terminal = 0

            row = {
                "commit": _git_head_commit(),
                "metric": result["metric"],
                "confidence": result["confidence"],
                "api_calls": result["api_calls"],
                "wall_clock_min": result["wall_clock_min"],
                "status": decision,
                "description": _format_loop_description(result, run_tag),
            }
            append_experiment_log_row(row, log_path)
            # G3: mirror strictly after the canonical TSV append.
            _mirror_log_row(
                row,
                run_tag=run_tag,
                experiment_id=result.get("experiment_id"),
            )
            rows.append({k: str(v) for k, v in row.items()})
            iters += 1

    return {
        "iterations": iters,
        "halted": _halted(),
        "wall_clock_min_total": (time.monotonic() - started) / 60.0,
        "market": market,
    }


def _record_halt_row(market: str, reason: str, run_tag: str | None = None) -> None:
    """Append a synthetic ``status=halt`` row for accounting (R-725).

    R-M15(b): the LIVE mirror row carries ``run_tag`` (threaded by the
    loop) even though the halt description has no ``run=`` token, so a
    BACKFILLED halt row has ``run_tag=NULL``. Known asymmetry — the
    live-threaded value is authoritative. G3: mirror only after the TSV
    append succeeded.
    """
    try:
        row = {
            "commit": _git_head_commit(allow_pending=True),
            "metric": 0,
            "confidence": 0.0,
            "api_calls": 0,
            "wall_clock_min": 0.0,
            "status": "halt",
            "description": f"halt | market={market} | {reason}",
        }
        append_experiment_log_row(row)
    except Exception:
        log.exception("failed to record halt row; continuing exit")
        return
    _mirror_log_row(row, run_tag=run_tag, experiment_id=None)


def _format_loop_description(
    result: Mapping[str, Any], run_tag: str | None = None
) -> str:
    """Compose the TSV description column for a non-baseline iteration.

    F1/F4: the trailing ``run=<tag>`` and ``exp=<experiment_id>`` tokens
    make baseline/anchor selection run-aware and make every experiment's
    rows discoverable (and purgeable) from the TSV alone, even after the
    writing process is gone.
    """
    pieces = [f"market={result.get('market', '?')}"]
    if result.get("status") in {"crash", "timeout"} and result.get("error"):
        pieces.append(str(result["error"]))
    sub = result.get("sub_summaries") or {}
    disc = sub.get("discovery") or {}
    score = sub.get("scoring") or {}
    if disc and not disc.get("aborted"):
        per_county = disc.get("per_county") or {}
        for c, info in per_county.items():
            ins = (info or {}).get("inserted")
            if ins is not None:
                pieces.append(f"discovery_{c}={ins}")
    if score and not score.get("aborted"):
        counts = score.get("counts") or {}
        if counts:
            scored = counts.get("scored", 0)
            pieces.append(f"scored={scored}")
    if result.get("stamp_audit_violations"):
        pieces.append(f"stamp_audit_violations={result['stamp_audit_violations']}")
    effective_run_tag = run_tag if run_tag is not None else result.get("run_tag")
    if effective_run_tag:
        # The loop passes its authoritative tag explicitly so every row it
        # writes carries the marker even when the result dict (e.g. the
        # outer-crash fallback) lacks one.
        pieces.append(f"run={effective_run_tag}")
    if result.get("experiment_id"):
        pieces.append(f"exp={result['experiment_id']}")
    return " | ".join(pieces)


# ---------------------------------------------------------------------------
# CLI demonstration (Phase 1 holdover, retained for sandbox smoke checks)
# ---------------------------------------------------------------------------
def _print_phase10_status() -> None:
    """Print enough state to prove the immutable layer is wired correctly."""
    params = prepare.get_parameters()
    threshold = params["composite_threshold"]
    print(
        "runner.py -- experiment loop, setup-phase verifier, "
        "evaluate(market), append-only TSV writer, and pure decision "
        "function are wired. Sandbox logic lives in research.py; "
        "CoStar ETL in costar_ingest.py; snapshots + memos in "
        "reporting.py. The agent (Claude Code) drives the loop via "
        "runner.experiment_loop(market, confirmed=True) on an "
        "autoresearch/<tag> branch."
    )
    print(f"composite_threshold (from parameters.json, frozen): {threshold}")


if __name__ == "__main__":
    _print_phase10_status()
