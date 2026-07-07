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
experiment log — that is the exact failure mode this split removes.

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
from contextlib import contextmanager
from datetime import datetime, timezone
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

# R-703, R-704 — branch invariant.
_AUTORESEARCH_BRANCH_RE = re.compile(r"^autoresearch/[a-z0-9._-]+$")

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


def _last_baseline_or_keep(rows: Sequence[Mapping[str, str]]) -> dict[str, str] | None:
    """Find the most recent ``baseline`` or ``keep`` row (the prior
    metric anchor for the next decision).  Returns ``None`` if no anchor
    exists yet (i.e., we have not even baselined)."""
    for r in reversed(rows):
        if r.get("status") in {"baseline", "keep"}:
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
        error = "budget_exceeded"
        log.exception("evaluate.timeout market=%s", market)
    except Exception as exc:  # pylint: disable=broad-except
        status = "crash"
        metric = 0
        confidence = 0.0
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
    }


# ---------------------------------------------------------------------------
# Experiment purge (prepare-mutation 2026-07-07) — the data half of revert
# ---------------------------------------------------------------------------
_SQL_DELETE_SCORES_FOR_EXPERIMENT = (
    "DELETE FROM parcel_scores WHERE experiment_id = %s"
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
def run_baseline_experiment(market: str) -> dict[str, Any]:
    """Run the baseline experiment per AUTORESEARCH_MECHANICS.md L108.

    The baseline is ONE complete evaluate() against unmodified research.py.
    The result is written to the TSV with ``status=baseline``.  The
    composite of the baseline metric and the head commit are returned.

    Caller is responsible for invoking this ONCE per autoresearch branch
    before the experiment loop begins.  ``experiment_loop`` calls this
    automatically when no baseline row exists in the TSV.
    """
    result = evaluate(market)
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
    row = {
        "commit": _git_head_commit(),
        "metric": result["metric"],
        "confidence": result["confidence"],
        "api_calls": result["api_calls"],
        "wall_clock_min": result["wall_clock_min"],
        "status": decision,
        "description": f"baseline | market={market}",
    }
    append_experiment_log_row(row)
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

        # Setup Step 6 -- explicit confirmation gate.
        log_path = _experiment_log_path()
        rows = read_experiment_log(log_path)
        has_baseline = any(r.get("status") == "baseline" for r in rows)

        if not has_baseline:
            # Setup Step 5 -- establish baseline.  The first call to
            # experiment_loop on a fresh autoresearch branch boots the
            # baseline before iterating.  AUTORESEARCH_MECHANICS.md L114
            # requires the human to confirm the baseline before the loop
            # begins; we honour this by requiring ``confirmed=True`` OR a
            # pre-existing baseline row.
            if not confirmed:
                raise SetupError(
                    "no baseline row in experiment_log.tsv and confirmed=False. "
                    "Per AUTORESEARCH_MECHANICS.md Setup Step 6, the human must "
                    "confirm the baseline before the loop begins. Either run "
                    "run_baseline_experiment(market) and review the result, then "
                    "call experiment_loop(market, confirmed=True), OR call "
                    "experiment_loop(market, confirmed=True) directly to bootstrap."
                )
            baseline = run_baseline_experiment(market)
            log.info("baseline established: %s", baseline)
            rows = read_experiment_log(log_path)

        # Main loop.
        while True:
            if _halted():
                log.info("experiment_loop halt sentinel detected; exiting cleanly")
                _record_halt_row(market, "halt sentinel detected")
                break
            if max_iterations is not None and iters >= max_iterations:
                break
            if time.monotonic() - started > _LONG_RUN_GRACEFUL_EXIT_SECONDS:
                log.info("experiment_loop graceful 7-day exit")
                _record_halt_row(market, "graceful 7-day exit")
                break

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
                    )
                    break
                # Sleep proportional to consecutive failures, capped.
                time.sleep(min(60 * consecutive_setup_failures, 300))
                continue
            consecutive_setup_failures = 0

            # Run one experiment.
            iter_started = time.monotonic()
            try:
                result = evaluate(market)
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
                }

            # R-733: soft per-iteration budget check.
            if result.get("wall_clock_min", 0.0) * 60 > _PHASE10_BUDGET_SECONDS:
                # The evaluator already emits status=ok in this case because
                # it lacks an internal SIGALRM.  Promote to 'timeout' here.
                if result["status"] == "ok":
                    result["status"] = "timeout"

            # Decide.
            prior = _last_baseline_or_keep(rows)
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
                    # loud in the log but keep the loop alive (NEVER STOP).
                    log.exception(
                        "purge failed for experiment %s; metric residue may "
                        "inflate subsequent reads",
                        result["experiment_id"],
                    )

            row = {
                "commit": _git_head_commit(),
                "metric": result["metric"],
                "confidence": result["confidence"],
                "api_calls": result["api_calls"],
                "wall_clock_min": result["wall_clock_min"],
                "status": decision,
                "description": _format_loop_description(result),
            }
            append_experiment_log_row(row, log_path)
            rows.append({k: str(v) for k, v in row.items()})
            iters += 1

    return {
        "iterations": iters,
        "halted": _halted(),
        "wall_clock_min_total": (time.monotonic() - started) / 60.0,
        "market": market,
    }


def _record_halt_row(market: str, reason: str) -> None:
    """Append a synthetic ``status=halt`` row for accounting (R-725)."""
    try:
        append_experiment_log_row({
            "commit": _git_head_commit(allow_pending=True),
            "metric": 0,
            "confidence": 0.0,
            "api_calls": 0,
            "wall_clock_min": 0.0,
            "status": "halt",
            "description": f"halt | market={market} | {reason}",
        })
    except Exception:
        log.exception("failed to record halt row; continuing exit")


def _format_loop_description(result: Mapping[str, Any]) -> str:
    """Compose the TSV description column for a non-baseline iteration."""
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
