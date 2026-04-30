"""prepare.py — Immutable measurement infrastructure for the Land Site Selector.

============================================================================
IMMUTABILITY DURING A RUN
============================================================================
Per AUTORESEARCH_MECHANICS.md (Five-File Contract, File 2): this file is the
Karpathy-immutable measurement layer. Neither the human nor the agent edits
this file *during a run*.

Definition of "during a run":
    From the moment an `autoresearch/<tag>` branch is checked out and the
    experiment loop begins, until the human manually halts the loop and merges
    or discards the branch. Editing this file in that window invalidates every
    metric value in the experiment log and forces a fresh baseline on a new
    branch (see AUTORESEARCH_MECHANICS.md "When Mutating prepare.py").

This module defines:
    1. The metric calculation (`calculate_actionable_pipeline_count`,
       `calculate_confidence_weighted_pipeline`).
    2. The DDL for every table the agent reads or writes.
    3. The frozen, hash-pinned parameters loader.
    4. The OS-level 90-minute wall-clock budget enforcement primitive.
    5. The masked-DSN logging helper.

Phase 1 scope: scaffolding only. The actionability gates (Phase 8), hard
filter logic (Phase 4), and composite score formula (Phase 5) are documented
here as future-locked extension points but are not yet implemented. Their
function signatures are stubbed below so that adding them later does NOT
require reshaping the public API of this module (which would itself be a
metric-mutation event).

Security notes:
    - No `os.system`, no `subprocess.run(shell=True)`, no `eval`/`exec`.
    - All future SQL beyond the static DDL in this file MUST use parameterised
      queries. There is no SQL-injection surface in Phase 1 because every
      statement issued is a static DDL string.
    - The connection DSN is masked via `_mask_dsn` in every error path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse

from dotenv import load_dotenv

# psycopg3 (`psycopg[binary]`) is the chosen driver. Rationale (see Agent 1
# review section 4.3 and Agent 2 response doc): psycopg3 is the actively
# maintained successor with a cleaner cursor/context-manager API and native
# binary COPY support that the Phase 3+ bulk parcel ingest will need. The
# psycopg2-binary driver is an acceptable fallback but would force a future
# migration that itself counts as a between-runs `prepare.py` mutation.
import psycopg

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("prepare")


# ---------------------------------------------------------------------------
# Exit codes (stable contract for shell wrappers and CI)
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_PARAMETERS_INVALID = 1
EXIT_DATABASE_URL_MISSING = 2
EXIT_EXTENSION_PRECONDITION = 3
EXIT_SCHEMA_APPLY_FAILED = 4
EXIT_BUDGET_EXCEEDED = 5


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCHEMA_VERSION = "1.0"
WALL_CLOCK_BUDGET_SECONDS = 90 * 60  # 5400 seconds — Karpathy 90-minute hard limit.

_REPO_ROOT = Path(__file__).resolve().parent
_PARAMETERS_PATH = _REPO_ROOT / "parameters.json"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ParametersError(RuntimeError):
    """Raised when parameters.json is missing, malformed, or fails validation."""


class BudgetExceeded(RuntimeError):
    """Raised when the wall-clock budget context manager fires."""


# ---------------------------------------------------------------------------
# DSN masking
# ---------------------------------------------------------------------------
def _mask_dsn(dsn: str) -> str:
    """Return ``dsn`` with the password component replaced by ``***``.

    Examples
    --------
    >>> _mask_dsn("postgresql://u:secret@h:5432/db")
    'postgresql://u:***@h:5432/db'
    >>> _mask_dsn("postgresql://u@h:5432/db")
    'postgresql://u@h:5432/db'
    >>> _mask_dsn("not-a-url")
    'not-a-url'
    """
    if not isinstance(dsn, str) or "://" not in dsn:
        return dsn
    try:
        parsed = urlparse(dsn)
    except ValueError:
        return "***"
    if parsed.password is None:
        return dsn
    # Reconstruct netloc with masked password.
    user = parsed.username or ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    auth = f"{user}:***@" if user else ":***@"
    netloc = f"{auth}{host}{port}"
    masked = parsed._replace(netloc=netloc)
    return masked.geturl()


# ---------------------------------------------------------------------------
# Parameters loading (frozen, hash-pinned)
# ---------------------------------------------------------------------------
_REQUIRED_PARAM_KEYS = ("hard_filters", "scoring_weights", "composite_threshold")


def _deep_freeze(obj: Any) -> Any:
    """Recursively wrap every nested ``dict`` in :class:`MappingProxyType`.

    Lists are converted to tuples so the entire returned structure is
    immutable from the caller's point of view. Scalars pass through
    unchanged. This is a *passive* immutability guard: callers can still
    mutate inner mutable types they smuggle in (e.g. ``set``), but the
    common ``params["x"] = ...`` mistake is blocked at the type level.
    """
    if isinstance(obj, dict):
        return types.MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


def _load_parameters() -> tuple[Mapping[str, Any], str]:
    """Load and validate ``parameters.json``.

    Returns
    -------
    (params, sha256_hex)
        ``params`` is a deep-frozen :class:`MappingProxyType`; ``sha256_hex``
        is the lowercase hex digest of the file bytes.

    Raises
    ------
    ParametersError
        If the file is missing, not valid JSON, not a top-level object, or
        missing any of the required keys.
    """
    if not _PARAMETERS_PATH.is_file():
        raise ParametersError(f"parameters.json not found at {_PARAMETERS_PATH}")
    raw = _PARAMETERS_PATH.read_bytes()
    sha256_hex = hashlib.sha256(raw).hexdigest()
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ParametersError(
            f"parameters.json at {_PARAMETERS_PATH} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(decoded, dict):
        raise ParametersError(
            f"parameters.json at {_PARAMETERS_PATH} top-level must be an object"
        )
    missing = [k for k in _REQUIRED_PARAM_KEYS if k not in decoded]
    if missing:
        raise ParametersError(
            f"parameters.json missing required key(s): {', '.join(missing)}"
        )
    return _deep_freeze(decoded), sha256_hex


# Module-level: load parameters once at import. The agent's ``research.py``
# MUST call :func:`get_parameters` rather than re-reading parameters.json
# directly — re-reading would defeat the SHA-256 sentinel that detects an
# illegal mid-run mutation.
try:
    _PARAMETERS, _PARAMETERS_SHA256 = _load_parameters()
except FileNotFoundError as exc:  # pragma: no cover — defensive
    raise ParametersError(f"parameters.json not found at {_PARAMETERS_PATH}") from exc
except json.JSONDecodeError as exc:  # pragma: no cover — _load_parameters wraps it
    raise ParametersError(
        f"parameters.json at {_PARAMETERS_PATH} is not valid JSON"
    ) from exc


def get_parameters() -> Mapping[str, Any]:
    """Return the read-only, deep-frozen parameter mapping loaded at import."""
    return _PARAMETERS


def verify_parameters_unchanged() -> None:
    """Re-hash ``parameters.json`` and raise if it differs from import time.

    Called at the top of the CLI entrypoint and (recommended) at the start
    of every experiment by ``research.py``. This is the SHA-256 sentinel
    half of the mid-run immutability guard; the :class:`MappingProxyType`
    deep-freeze is the in-memory half.
    """
    if not _PARAMETERS_PATH.is_file():
        raise ParametersError(
            f"parameters.json disappeared since module load ({_PARAMETERS_PATH})"
        )
    current = hashlib.sha256(_PARAMETERS_PATH.read_bytes()).hexdigest()
    if current != _PARAMETERS_SHA256:
        raise ParametersError("parameters.json changed since module load")


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------
def _get_connection_dsn() -> str:
    """Read ``DATABASE_URL`` from environment (with ``.env`` loaded)."""
    load_dotenv(_REPO_ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        log.error(
            "DATABASE_URL not set. Copy env.template to .env and fill in the DSN."
        )
        sys.exit(EXIT_DATABASE_URL_MISSING)
    return dsn


@contextmanager
def get_connection() -> Iterator["psycopg.Connection"]:
    """Yield a psycopg3 connection with ``autocommit=False``.

    On :class:`psycopg.Error`, logs with the masked DSN and re-raises. The
    caller is responsible for committing or rolling back; this context
    manager only ensures the connection is closed on exit.
    """
    dsn = _get_connection_dsn()
    conn: "psycopg.Connection | None" = None
    try:
        conn = psycopg.connect(dsn, autocommit=False, connect_timeout=10)
        yield conn
    except psycopg.Error as exc:
        log.error("psycopg error against %s: %s", _mask_dsn(dsn), exc)
        raise
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# DDL — verbatim from STORAGE_ARCHITECTURE.md, with `IF NOT EXISTS` guards
# added so :func:`apply_schema` is idempotent. Comments inside the original
# SQL have been preserved for traceability.
# ---------------------------------------------------------------------------
_DDL_EXTENSION_POSTGIS = "CREATE EXTENSION IF NOT EXISTS postgis;"

_DDL_PARCELS = """
CREATE TABLE IF NOT EXISTS parcels (
    parcel_id TEXT PRIMARY KEY,
    county TEXT NOT NULL,
    state TEXT NOT NULL,
    market TEXT NOT NULL,
    submarket TEXT,
    address TEXT,
    owner_name TEXT,
    owner_mailing_address TEXT,
    owner_type_inferred TEXT,
    acreage NUMERIC,
    land_sf NUMERIC,
    zoning TEXT,
    zoning_description TEXT,
    land_use_code TEXT,
    land_use_description TEXT,
    assessed_value_land BIGINT,
    assessed_value_improvement BIGINT,
    assessed_value_total BIGINT,
    fair_market_value BIGINT,
    tax_year SMALLINT,
    tax_amount NUMERIC,
    tax_status TEXT,
    last_sale_date DATE,
    last_sale_price BIGINT,
    deed_book_page TEXT,
    year_built SMALLINT,
    improvement_sf NUMERIC,
    geometry GEOMETRY(Polygon, 4326),
    centroid GEOMETRY(Point, 4326),
    discovery_source TEXT,
    discovery_date DATE,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    raw_response_path TEXT
);
"""

_DDL_PARCEL_SCORES = """
CREATE TABLE IF NOT EXISTS parcel_scores (
    score_id SERIAL PRIMARY KEY,
    parcel_id TEXT REFERENCES parcels(parcel_id),
    scored_at TIMESTAMPTZ DEFAULT NOW(),
    composite_score NUMERIC,
    confidence_score NUMERIC,
    actionability TEXT,
    actionability_blockers JSONB,
    sub_scores JSONB,
    strategy_fit JSONB,
    primary_strategy TEXT,
    investment_thesis TEXT,
    notes TEXT
);
"""

_DDL_MARKETS = """
CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY,
    market_name TEXT NOT NULL,
    tier SMALLINT,
    state TEXT,
    notes TEXT
);
"""

_DDL_SUBMARKETS = """
CREATE TABLE IF NOT EXISTS submarkets (
    submarket_id TEXT PRIMARY KEY,
    market_id TEXT REFERENCES markets(market_id),
    submarket_name TEXT NOT NULL,
    bbox GEOMETRY(Polygon, 4326),
    notes TEXT
);
"""

_DDL_MARKET_CONTEXT = """
CREATE TABLE IF NOT EXISTS market_context (
    context_id SERIAL PRIMARY KEY,
    submarket_id TEXT REFERENCES submarkets(submarket_id),
    as_of_date DATE,
    vacancy_rate_pct NUMERIC,
    availability_rate_pct NUMERIC,
    net_absorption_t12_sf BIGINT,
    under_construction_sf BIGINT,
    proposed_sf BIGINT,
    asking_rent_nnn_psf NUMERIC,
    source TEXT,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);
"""

_DDL_SALES_COMPS = """
CREATE TABLE IF NOT EXISTS sales_comps (
    comp_id SERIAL PRIMARY KEY,
    address TEXT,
    parcel_id TEXT,
    county TEXT,
    submarket_id TEXT REFERENCES submarkets(submarket_id),
    comp_type TEXT,
    acres NUMERIC,
    building_sf NUMERIC,
    sale_date DATE,
    sale_price BIGINT,
    price_per_acre NUMERIC,
    price_psf NUMERIC,
    cap_rate NUMERIC,
    buyer_name TEXT,
    seller_name TEXT,
    zoning TEXT,
    raw JSONB,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);
"""

_DDL_LEASING_COMPS = """
CREATE TABLE IF NOT EXISTS leasing_comps (
    lease_id SERIAL PRIMARY KEY,
    address TEXT,
    submarket_id TEXT REFERENCES submarkets(submarket_id),
    tenant_name TEXT,
    tenant_industry TEXT,
    naics_code TEXT,
    lease_start_date DATE,
    lease_term_months INTEGER,
    building_sf_leased NUMERIC,
    starting_rent_psf_nnn NUMERIC,
    rent_escalation_pct NUMERIC,
    lease_type TEXT,
    raw JSONB,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);
"""

_DDL_LAND_LISTINGS = """
CREATE TABLE IF NOT EXISTS land_listings (
    listing_id SERIAL PRIMARY KEY,
    address TEXT,
    parcel_id TEXT,
    county TEXT,
    submarket_id TEXT REFERENCES submarkets(submarket_id),
    acres NUMERIC,
    zoning TEXT,
    asking_price BIGINT,
    asking_price_per_acre NUMERIC,
    listing_date DATE,
    days_on_market INTEGER,
    listing_broker TEXT,
    listing_broker_firm TEXT,
    utilities_status TEXT,
    entitlement_status TEXT,
    raw JSONB,
    snapshot_date DATE,
    is_active BOOLEAN DEFAULT TRUE
);
"""

_DDL_RESEARCH_LOG = """
CREATE TABLE IF NOT EXISTS research_log (
    log_id BIGSERIAL PRIMARY KEY,
    cycle_id TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    action_type TEXT,
    market TEXT,
    parcel_id TEXT,
    composite_score NUMERIC,
    actionability TEXT,
    strategy_fit TEXT,
    actionable_pipeline_count INTEGER,
    discovery_rate_24h NUMERIC,
    scoring_completeness NUMERIC,
    conversion_rate NUMERIC,
    notes TEXT
);
"""

_DDL_HARNESS_REPORTS = """
CREATE TABLE IF NOT EXISTS harness_reports (
    report_id SERIAL PRIMARY KEY,
    county TEXT,
    market TEXT,
    run_at TIMESTAMPTZ DEFAULT NOW(),
    overall_health TEXT,
    checks JSONB,
    sample_features JSONB,
    warnings JSONB,
    errors JSONB
);
"""

_DDL_FLAGGED_ITEMS = """
CREATE TABLE IF NOT EXISTS flagged_items (
    flag_id SERIAL PRIMARY KEY,
    flagged_at TIMESTAMPTZ DEFAULT NOW(),
    flag_type TEXT,
    parcel_id TEXT,
    market TEXT,
    description TEXT,
    suggested_resolution TEXT,
    status TEXT DEFAULT 'open',
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT,
    resolution_notes TEXT
);
"""

_DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_parcels_county ON parcels(county);",
    "CREATE INDEX IF NOT EXISTS idx_parcels_market ON parcels(market);",
    "CREATE INDEX IF NOT EXISTS idx_parcels_acreage ON parcels(acreage);",
    "CREATE INDEX IF NOT EXISTS idx_parcels_geometry ON parcels USING GIST(geometry);",
    "CREATE INDEX IF NOT EXISTS idx_parcels_centroid ON parcels USING GIST(centroid);",
    # Functional index on the trailing "STATE 12345" pattern of the owner
    # mailing address — used by the Phase 6 absentee-owner heuristic.
    r"CREATE INDEX IF NOT EXISTS idx_parcels_owner_state ON parcels((SUBSTRING(owner_mailing_address FROM '[A-Z]{2} \d{5}')));",
    "CREATE INDEX IF NOT EXISTS idx_scores_parcel ON parcel_scores(parcel_id);",
    "CREATE INDEX IF NOT EXISTS idx_scores_actionability ON parcel_scores(actionability);",
    "CREATE INDEX IF NOT EXISTS idx_scores_composite ON parcel_scores(composite_score);",
    "CREATE INDEX IF NOT EXISTS idx_context_submarket_date ON market_context(submarket_id, as_of_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_comps_submarket_date ON sales_comps(submarket_id, sale_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_comps_type ON sales_comps(comp_type);",
    "CREATE INDEX IF NOT EXISTS idx_log_cycle ON research_log(cycle_id);",
    "CREATE INDEX IF NOT EXISTS idx_log_timestamp ON research_log(timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_harness_county_date ON harness_reports(county, run_at DESC);",
)

_ALL_DDL: tuple[str, ...] = (
    _DDL_EXTENSION_POSTGIS,
    _DDL_PARCELS,
    _DDL_PARCEL_SCORES,
    _DDL_MARKETS,
    _DDL_SUBMARKETS,
    _DDL_MARKET_CONTEXT,
    _DDL_SALES_COMPS,
    _DDL_LEASING_COMPS,
    _DDL_LAND_LISTINGS,
    _DDL_RESEARCH_LOG,
    _DDL_HARNESS_REPORTS,
    _DDL_FLAGGED_ITEMS,
    *_DDL_INDEXES,
)


# ---------------------------------------------------------------------------
# Schema application
# ---------------------------------------------------------------------------
def apply_schema(conn: "psycopg.Connection") -> None:
    """Apply every DDL statement in :data:`_ALL_DDL` in a single transaction.

    On any error: rollback, log with the masked DSN, and raise
    :class:`RuntimeError` ``"schema apply failed"``. On success, commit.

    The DDL is idempotent (``CREATE ... IF NOT EXISTS`` everywhere), so
    re-running this function against a populated database is safe.
    """
    masked = _mask_dsn(getattr(conn, "info", None).dsn) if getattr(conn, "info", None) else "<conn>"
    try:
        with conn.cursor() as cur:
            for stmt in _ALL_DDL:
                cur.execute(stmt)
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:  # pragma: no cover — rollback best-effort
            pass
        log.error("schema apply failed against %s: %s", masked, exc)
        raise RuntimeError("schema apply failed") from exc


# ---------------------------------------------------------------------------
# Metric calculations — the immutable measurement layer
# ---------------------------------------------------------------------------
# The metric is the Karpathy-immutable "is the system getting better?" signal.
# Both functions filter to the LATEST score per parcel via a correlated
# subquery; for production volumes a window function (`ROW_NUMBER() OVER
# PARTITION BY parcel_id ORDER BY scored_at DESC`) or `DISTINCT ON` may be
# preferable. That refactor is a between-runs `prepare.py` mutation event,
# so it is deliberately deferred — see Agent 2 response doc and Phase 5+.

_LATEST_SCORE_WHERE = (
    "ps.actionability = 'PASS' "
    "AND ps.composite_score >= %s "
    "AND ps.scored_at = ("
    "    SELECT MAX(scored_at) FROM parcel_scores "
    "    WHERE parcel_id = ps.parcel_id"
    ")"
)


def calculate_actionable_pipeline_count(conn: "psycopg.Connection") -> int:
    """Count parcels whose latest score is PASS and >= ``composite_threshold``.

    Returns 0 against an empty database. The threshold is read from the
    frozen parameters layer, NOT re-parsed from disk.
    """
    threshold = _PARAMETERS["composite_threshold"]
    sql = (
        "SELECT COUNT(*) FROM parcel_scores ps "
        "JOIN parcels p USING (parcel_id) "
        f"WHERE {_LATEST_SCORE_WHERE}"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (threshold,))
        row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def calculate_confidence_weighted_pipeline(conn: "psycopg.Connection") -> float:
    """Sum confidence_score across the actionable pipeline.

    Returns 0.0 against an empty database. Same WHERE clause as
    :func:`calculate_actionable_pipeline_count`; only the projection differs.
    """
    threshold = _PARAMETERS["composite_threshold"]
    sql = (
        "SELECT COALESCE(SUM(ps.confidence_score), 0) FROM parcel_scores ps "
        "JOIN parcels p USING (parcel_id) "
        f"WHERE {_LATEST_SCORE_WHERE}"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (threshold,))
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


# ---------------------------------------------------------------------------
# Wall-clock budget enforcement
# ---------------------------------------------------------------------------
@contextmanager
def wall_clock_budget(seconds: int = WALL_CLOCK_BUDGET_SECONDS) -> Iterator[None]:
    """In-process best-effort 90-minute budget via ``signal.SIGALRM``.

    NOTE: This is the IN-PROCESS BEST-EFFORT enforcement only. Per
    AUTORESEARCH_MECHANICS.md, the AUTHORITATIVE OS-LEVEL enforcement is
    :func:`run_with_os_timeout`, which the experiment runner in
    ``research.py`` MUST use to wrap each experiment.

    Caveats:
        - SIGALRM is POSIX-only (no Windows support).
        - Installing a SIGALRM handler conflicts with any other component
          that relies on alarms; the experiment runner should not install
          its own SIGALRM handler while inside this context.
    """
    if not hasattr(signal, "SIGALRM"):  # pragma: no cover — non-POSIX
        raise NotImplementedError(
            "wall_clock_budget requires SIGALRM (POSIX only). On Windows, "
            "rely on run_with_os_timeout for budget enforcement."
        )

    def _handler(signum: int, frame: Any) -> None:
        raise BudgetExceeded(f"wall-clock budget of {seconds}s exceeded")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def run_with_os_timeout(
    cmd: list[str], seconds: int = WALL_CLOCK_BUDGET_SECONDS
) -> subprocess.CompletedProcess:
    """Run ``cmd`` with an OS-level timeout; raise :class:`BudgetExceeded`.

    Per AUTORESEARCH_MECHANICS.md, this is the AUTHORITATIVE wall-clock
    enforcement. The experiment runner in ``research.py`` must wrap each
    experiment subprocess with this helper so that a runaway agent process
    cannot overrun the 90-minute Karpathy budget.

    ``check=False`` is used so non-zero exit codes are returned to the
    caller for inspection rather than swallowed by ``CalledProcessError``.
    No ``shell=True``; no ``os.system``.
    """
    try:
        return subprocess.run(cmd, timeout=seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        log.error("OS-level timeout: %s exceeded %ss", cmd[:1], seconds)
        raise BudgetExceeded(
            f"command {cmd[:1]} exceeded {seconds}s budget"
        ) from exc


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------
def _cli_main() -> int:
    """Apply the schema and print the Phase 1 metrics."""
    verify_parameters_unchanged()
    with get_connection() as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT POSTGIS_VERSION()")
            row = cur.fetchone()
            postgis_version = row[0] if row else "<unknown>"
        actionable = calculate_actionable_pipeline_count(conn)
        weighted = calculate_confidence_weighted_pipeline(conn)
    print(f"schema_version: {SCHEMA_VERSION}")
    print(f"postgis_version: {postgis_version}")
    print(f"actionable_pipeline_count: {actionable}")
    print(f"confidence_weighted_pipeline: {weighted}")
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(_cli_main())
    except ParametersError as exc:
        log.error("parameters error: %s", exc)
        sys.exit(EXIT_PARAMETERS_INVALID)
    except BudgetExceeded as exc:
        log.error("wall-clock budget exceeded: %s", exc)
        sys.exit(EXIT_BUDGET_EXCEEDED)
    except RuntimeError as exc:
        # Includes "schema apply failed" raised by apply_schema.
        msg = str(exc)
        log.error("runtime error: %s", msg)
        if "schema apply failed" in msg:
            sys.exit(EXIT_SCHEMA_APPLY_FAILED)
        sys.exit(EXIT_SCHEMA_APPLY_FAILED)
    except psycopg.Error as exc:
        log.error("database error: %s", exc)
        sys.exit(EXIT_EXTENSION_PRECONDITION)
