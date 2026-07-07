"""research.py — The autonomous agent's sandbox.

============================================================================
THE ONLY FILE THE AGENT EDITS DURING A RUN
============================================================================
Per AUTORESEARCH_MECHANICS.md (Five-File Contract, File 4): this is the only
Python file the agent edits while the experiment loop is active. The agent:

    - reads parameters via :func:`prepare.get_parameters`,
    - never re-parses ``parameters.json`` directly,
    - never re-defines symbols imported from :mod:`prepare`,
    - never modifies :mod:`prepare`, :mod:`runner`, :mod:`costar_ingest`,
      :mod:`reporting`, :mod:`pipeline_common`, or any spec ``.md`` file.

============================================================================
WHAT LIVES HERE (post-split experiment surface)
============================================================================
This module holds ONLY the logic the agent experiments on: discovery
heuristics + corridor selection, the H1-H10 hard-filter predicates, the
S1-S12 sub-score implementations, composite/confidence calculation, the
strategy-fit engine, and the actionability gates.

The infrastructure that used to live here was extracted (see
reviews/14_streamlining_review/00_streamlining_review.md Finding B):

    runner.py          — experiment loop, setup phase, TSV I/O (IMMUTABLE
                         during a run, same status as prepare.py)
    costar_ingest.py   — CoStar export ETL (frozen to the ingestion contract)
    reporting.py       — snapshot + strategy memo rendering
    pipeline_common.py — shared repo paths, _flag helper, shared SQL

============================================================================
PHASE 3 + PHASE 4 SCOPE — Fulton County discovery connector + H5-H10 stubs
============================================================================
This module implements the Fulton County discovery connector per
BUILD_PHASES.md Phase 3 and the Agent 1 risk review at
``reviews/04_phase3_fulton_discovery/01_risk_review.md`` (48 risks, 12
categories, GO-WITH-CONDITIONS verdict). Phase 4 added H5..H10 as
PASS-WITH-FLAG stubs per ``reviews/06_phase4_hard_filters/01_risk_review.md``
(R-101..R-114). The connector:

    1. Calls ``connector_harness.run_harness_for_county("fulton")`` BEFORE
       any production query (appendix integration point #2 — appendix
       L897-L903).  ``failing`` aborts the cycle; ``degraded`` proceeds with
       a flag; harness raise is treated as ``failing``.
    2. Iterates two corridor bounding boxes (South Fulton /
       Campbellton-Fairburn + West Atlanta / I-20 / Fulton Industrial Blvd
       — appendix L266-L283).
    3. Queries the Fulton ArcGIS parcel layer (Layer 11 of the validated
       service URL in ``sources.json``) with spatial+attribute filtering
       (acreage 5-50 from ``parameters.json.hard_filters``).
    4. Maps ArcGIS attributes + geometry to the ``parcels`` schema using
       the validated ``field_mapping`` in ``sources.json``.
    5. Runs hard filters H1 (Fulton envelope) and H2 (acreage client-side
       recheck) as deterministic reject filters.  H3..H10 are
       PASS-WITH-FLAG stubs that emit ``flagged_items`` rows of
       ``flag_type='data_gap'`` and let the parcel through pending Phase 5+
       data wiring (H3 zoning Layer 34, H4 FEMA NFIP, H5 EPA Envirofacts,
       H6 USGS NWI, H7 county roads/DOT, H8 utility provider service maps,
       H9 USGS 3DEP, H10 deed records / conservation easement registries).
    6. UPSERTs into ``parcels`` (preserves first-seen ``discovery_date``,
       bumps ``last_updated``).  Logs every action to ``research_log``.
    7. Caches raw ArcGIS responses to ``sources/{cycle_id}/{corridor}_{offset}.json``
       for audit (per STORAGE_ARCHITECTURE.md "Cached raw API responses").
       The ``sources/`` directory is gitignored.

Scoring (S1-S12), the actionability screen, snapshot generation, the
strategy memo generator, and the experiment loop are out of scope for
Phase 3 and remain ``NotImplementedError`` stubs below.

============================================================================
PII / REDACTION POLICY
============================================================================
``connector_harness.py`` redacts owner names in *reports* written to
``harness_reports/``. That redaction is REPORT-LEVEL, not data-level.
The ``parcels`` table is the canonical record of who owns each parcel —
Phase 9 snapshots, Phase 10 outreach research, and Phase 11
owner-aggregation queries all need the unredacted name. **Phase 3 stores
``owner_name`` verbatim in the ``parcels`` table and never applies
harness-style redaction to canonical records.** Output-time redaction
policy (if any) is the responsibility of the snapshot generator.

============================================================================
THREE-AGENT WORKFLOW DEVIATION (Phase 3)
============================================================================
This file was written by the orchestrator (Claude Code main session) under
explicit human authorization after multiple Agent 2 sub-agent attempts
hit stream-idle timeouts at ~270s / 36 tool calls in this sandbox
environment.  Mirrors the Phase 2 precedent at
``reviews/03_phase2_connector_harness/03_orchestrator_completion_note.md``.
The full deviation note is in
``reviews/04_phase3_fulton_discovery/02_code_writer_response.md``.

Every Agent 1 risk (R-01 .. R-48) is addressed in code or accepted with
explicit rationale in that response document.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlparse

import requests

# Importing prepare gives the agent its read-only handle to the immutable
# layer. Importing it does NOT open a database connection — the connection
# is lazy, inside :func:`prepare.get_connection`. This keeps `import research`
# safe to run in CI / smoke tests with no DATABASE_URL set.
import prepare  # noqa: F401 — re-exported intentionally for the agent.
import connector_harness
from pipeline_common import (
    _COSTAR_BASE_DIR,  # noqa: F401 — re-exported for backwards compatibility.
    _REPO_ROOT,
    _SQL_LATEST_MARKET_CONTEXT,
    _flag,
)

log = logging.getLogger("research")
_SOURCES_DIR = _REPO_ROOT / "sources"

# ---------------------------------------------------------------------------
# Phase 3 constants (Agent 1 R-04, R-14, R-20, R-31, R-35)
# ---------------------------------------------------------------------------
# Corridor bounding boxes (WGS84 / EPSG:4326). Source of truth: appendix L266-L283.
_FULTON_CORRIDORS: dict[str, dict[str, float]] = {
    "south_fulton_campbellton": {
        "xmin": -84.62, "ymin": 33.52, "xmax": -84.50, "ymax": 33.58,
    },
    "west_atlanta_i20": {
        "xmin": -84.58, "ymin": 33.72, "xmax": -84.42, "ymax": 33.79,
    },
}

# Fulton county envelope for H1 (Agent 1 R-20). Loose 0.5-degree tolerance
# matches the harness's expected_bbox documented in connector_registry.json.
# Phase 4 should replace this with a true county-polygon ST_Within check
# pulled from Georgia statewide GIS.
_FULTON_ENVELOPE: dict[str, float] = {
    "xmin": -84.65, "ymin": 33.40, "xmax": -84.05, "ymax": 34.20,
}

# Per-page record cap. Fulton's max is 2000; 1000 is a balance between
# round-trips and per-response payload size (R-14).
_FULTON_PAGE_SIZE = 1000

# Cycle wall-clock budget (R-35). Discovery is not a Karpathy experiment
# (Phase 10) so the 90-minute budget does not apply; 30 min is the soft
# ceiling for the two-corridor Fulton cycle.
_CYCLE_BUDGET_SECONDS = 30 * 60

# Per-request HTTP timeout for ArcGIS queries.
_DISCOVERY_HTTP_TIMEOUT_S = 30

# Polite-scrape minimum spacing per host (R-17, mirrors harness §1.3).
_MIN_REQUEST_SPACING_S = 1.0

# Discovery retry policy (Phase 13 R-1301..R-1305). INTENTIONALLY divergent
# from connector_harness.py's MAX_RETRIES=3 / BACKOFF_SCHEDULE_S=(1,2,4): the
# discovery cycle runs UNDER the Karpathy 90-min OS kill (AUTORESEARCH_MECHANICS
# L153) and its own 30-min soft ceiling (_CYCLE_BUDGET_SECONDS), so a smaller
# retry budget caps worst-case wall time per flapping endpoint. We recreate the
# retry pattern here rather than importing connector_harness (R-1306 / Phase 3
# R-17: contractual isolation — research.py must not import the harness's
# private HTTP helper). 2 retries = 3 total attempts (matches Phase 3 R-18).
_DISCOVERY_MAX_RETRIES = 2
_DISCOVERY_BACKOFF_SCHEDULE_S = (1.0, 2.0)
# Cap on an honored 429 Retry-After so a pathological header (e.g.
# "Retry-After: 3600") cannot blow the cycle budget (R-1304). Beyond the cap we
# fall back to the scheduled backoff; persistent rate-limiting then exhausts the
# retries and the corridor-level handler flags it (R-1302).
_DISCOVERY_RETRY_AFTER_CAP_S = 10.0

# Module-level mapping cache so we re-read sources.json once per cycle.
_SOURCES_PATH = _REPO_ROOT / "sources.json"

# Markets dispatch — Phase 11 will add more counties under "atlanta".
_MARKET_TO_COUNTIES: dict[str, list[str]] = {
    "atlanta": ["fulton"],
}

# Cycle-id format regex used by tests.
_CYCLE_ID_RE = re.compile(
    r"^disco-[a-z]+-\d{8}T\d{6}Z-[0-9a-f]{4}$"
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _FilterResult:
    """Outcome of one hard filter against one parcel.

    ``action`` is one of:
        - ``"pass"`` — parcel proceeds to the next filter
        - ``"reject"`` — parcel is rejected; no parcels insert; one
          research_log row of action_type='rejection' is written
        - ``"flag"`` — parcel proceeds AND a flagged_items row is emitted

    ``reason`` is a short human-readable string included in the rejection
    or flag row. ``filter_id`` is the H1/H2/H3/H4 designator used for
    log notes.
    """

    action: str
    filter_id: str
    reason: str


# Forward declarations are NOT used in the implementation; the filter
# callables are defined below before _HARD_FILTERS is composed.


# ---------------------------------------------------------------------------
# Cycle id, cache path, sources loader
# ---------------------------------------------------------------------------
def _make_cycle_id(county: str) -> str:
    """Generate a unique sortable cycle id (R-31).

    Format: ``disco-{county}-{ISO8601-Z}-{4-hex}``. ISO 8601 compact form
    is sortable lex == chronological. The 4-char random suffix covers the
    same-second collision case.
    """
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(2)
    return f"disco-{county.lower()}-{now}-{suffix}"


def _safe_cache_path(cycle_id: str, corridor: str, offset: int) -> Path:
    """Construct + validate the raw-response cache path (R-40).

    Asserts the resolved path is under ``_SOURCES_DIR``. Rejects any
    component containing path-traversal characters even though all
    callers pass module-controlled values.
    """
    if not _CYCLE_ID_RE.match(cycle_id):
        raise ValueError(f"unsafe cycle_id: {cycle_id!r}")
    if not re.match(r"^[a-z0-9_]+$", corridor):
        raise ValueError(f"unsafe corridor name: {corridor!r}")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError(f"unsafe offset: {offset!r}")
    candidate = (_SOURCES_DIR / cycle_id / f"{corridor}_{offset}.json").resolve()
    sources_resolved = _SOURCES_DIR.resolve()
    try:
        candidate.relative_to(sources_resolved)
    except ValueError as exc:
        raise ValueError(f"path traversal rejected: {candidate!r}") from exc
    return candidate


def _load_sources_json() -> Mapping[str, Any]:
    """Read sources.json once. Read-only — research.py never writes it (R-01)."""
    with _SOURCES_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _fulton_field_mapping(sources: Mapping[str, Any]) -> Mapping[str, str]:
    """Return the validated Fulton field mapping from sources.json (R-25)."""
    return sources["county_parcel_data"]["fulton_ga"]["field_mapping"]


def _fulton_service_url(sources: Mapping[str, Any]) -> str:
    return sources["county_parcel_data"]["fulton_ga"]["service_url"]


def _fulton_parcel_layer_id(sources: Mapping[str, Any]) -> int:
    return int(sources["county_parcel_data"]["fulton_ga"]["parcel_layer_id"])


# ---------------------------------------------------------------------------
# Discovery HTTP session — 1 req/sec per host (R-17)
# ---------------------------------------------------------------------------
class _DiscoverySession:
    """Single chokepoint for outbound HTTP from research.py (R-45).

    Tests monkeypatch the ``get`` method to return fixture JSON without
    touching the network. Production code path uses ``requests.Session``
    underneath with a polite-scrape 1-req/sec floor per host.

    Threading: the discovery cycle is single-threaded by design and this
    class is only safe to share within a single cycle. The lock-protected
    ``_last_request_at`` reservation in ``_spacing_sleep`` is correct under
    concurrent use (it stages requests at the cost of being slightly
    over-conservative when threads collide), but no production caller
    exercises that path. If a future phase introduces concurrent discovery,
    re-validate the per-host budget end-to-end rather than assuming the
    staggering is sufficient.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Land-Research-Discovery/0.1 (+phase3 fulton)",
            "Accept": "application/json",
        })
        self._last_request_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def _spacing_sleep(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            last = self._last_request_at.get(host, 0.0)
            elapsed = now - last
            if elapsed < _MIN_REQUEST_SPACING_S:
                wait = _MIN_REQUEST_SPACING_S - elapsed
            else:
                wait = 0.0
            self._last_request_at[host] = max(now, last) + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def _retry_after_delay(
        self, resp: "requests.Response", scheduled_backoff: float
    ) -> float:
        """Resolve the inter-attempt sleep for a retryable HTTP response.

        R-1304: when a 429 carries a ``Retry-After`` header longer than the
        scheduled backoff, honor it (capped at _DISCOVERY_RETRY_AFTER_CAP_S so a
        pathological value cannot blow the cycle budget). Otherwise use the
        schedule. Only the integer-seconds form of Retry-After is parsed (pure
        stdlib, no new dependency); an HTTP-date form or a missing/garbage
        header falls back to the scheduled backoff. A header value ABOVE the cap
        does NOT extend the wait — we cap and let retry-exhaustion hand off to
        the corridor handler rather than stall for minutes.
        """
        raw = resp.headers.get("Retry-After", "")
        try:
            retry_after = int(raw)
        except (TypeError, ValueError):
            return scheduled_backoff
        if retry_after <= 0:
            return scheduled_backoff
        return max(scheduled_backoff, min(float(retry_after), _DISCOVERY_RETRY_AFTER_CAP_S))

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        timeout: float = _DISCOVERY_HTTP_TIMEOUT_S,
    ) -> dict[str, Any]:
        """GET with rate limit + JSON parse. Raises on HTTP error.

        Phase 13 (R-1301..R-1305): up to _DISCOVERY_MAX_RETRIES retries on
        connection errors, timeouts, HTTP 5xx, and 429. Other 4xx fail-fast
        (no retry — they will not recover). Per-host polite spacing
        (``_spacing_sleep``) is respected on EVERY attempt, BEFORE the request;
        the backoff sleep happens AFTER a failed attempt and before the next
        loop iteration (ordering mirrors connector_harness._http_get
        L338-364 for consistency — spacing at top, backoff at bottom). On
        exhaustion the original exception class propagates unchanged (R-1302),
        so the existing corridor-level handler still aborts the corridor and
        continues the cycle rather than mistaking a dead endpoint for an empty
        one. The retry is safe because this session issues only GETs (R-1307).
        """
        host = urlparse(url).hostname or ""
        # NOTE: do NOT log the full URL with query params on retry — Fulton is
        # public today but Phase 11 / Regrid endpoints may carry an API key
        # (R-1308). We log only host + status, never the query string.
        for attempt in range(_DISCOVERY_MAX_RETRIES + 1):
            self._spacing_sleep(host)
            retryable = False
            backoff = _DISCOVERY_BACKOFF_SCHEDULE_S[
                min(attempt, len(_DISCOVERY_BACKOFF_SCHEDULE_S) - 1)
            ]
            try:
                resp = self._session.get(
                    url, params=dict(params or {}), timeout=timeout
                )
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as exc:
                # Transient transport failures retry. Other RequestException
                # subclasses (InvalidURL, TooManyRedirects, ...) are NOT
                # transient and are deliberately NOT caught here, so they
                # propagate immediately (fail-fast) — more correct and faster
                # than retrying something that cannot recover (R-1305).
                if attempt >= _DISCOVERY_MAX_RETRIES:
                    raise
                retryable = True
                reason = exc.__class__.__name__
                sleep_s = backoff
            else:
                status = resp.status_code
                if 200 <= status < 400:
                    return resp.json()
                if status == 429 or 500 <= status < 600:
                    # 429 is the one 4xx that is genuinely transient, so we
                    # retry it (unlike the harness, which fail-fasts all 4xx);
                    # honor Retry-After when present (R-1304). 5xx retries on
                    # the fixed schedule.
                    if attempt >= _DISCOVERY_MAX_RETRIES:
                        resp.raise_for_status()  # re-raise the SAME HTTPError (R-1302)
                    retryable = True
                    reason = f"http_{status}"
                    sleep_s = (
                        self._retry_after_delay(resp, backoff)
                        if status == 429 else backoff
                    )
                else:
                    # Other 4xx — fail-fast, no retry (won't recover) (R-1305).
                    resp.raise_for_status()
            if retryable:
                log.info(
                    "discovery GET host=%s failed (%s), retrying in %.1fs "
                    "(attempt %d/%d)",
                    host, reason, sleep_s, attempt + 1, _DISCOVERY_MAX_RETRIES,
                )
                time.sleep(sleep_s)
        # Unreachable: the final attempt either returns or raises above. Guard
        # against a logic change silently returning None.
        raise RuntimeError("discovery retry loop exited without return/raise")

    def close(self) -> None:
        self._session.close()


# ---------------------------------------------------------------------------
# Geometry + SRID helpers (R-07, R-08, R-09, R-16)
# ---------------------------------------------------------------------------
def _ring_signed_area(ring: Sequence[Sequence[float]]) -> float:
    """Shoelace signed area of an Esri ring. CW > 0, CCW < 0 in Esri convention."""
    n = len(ring)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        s += (x1 * y2) - (x2 * y1)
    return s * 0.5


def _arcgis_polygon_to_wkt(
    rings: Sequence[Sequence[Sequence[float]]],
) -> tuple[str, bool, Sequence[Sequence[float]]]:
    """Convert Esri JSON polygon ``rings`` to OGC WKT POLYGON (R-07, R-16).

    Returns ``(wkt, was_multipolygon_reduced, kept_outer_ring)``.

    If multiple outer rings are present (disjoint polygons in Esri JSON),
    keep the largest by absolute area and emit ``True`` for the bool. The
    dropped rings are intentionally lost from the canonical record; the
    caller flags the parcel via flagged_items so Phase 4+ can revisit.

    The third return value, ``kept_outer_ring``, is the ring whose
    coordinates appear in the WKT — callers who need a centroid for the
    H1 envelope check must use this rather than ``rings[0]`` so multi-
    polygon parcels with a non-first largest ring are evaluated
    consistently with what is stored in PostGIS (Phase 3.1 §6.B fix).
    """
    if not rings:
        raise ValueError("empty rings; cannot construct WKT POLYGON")
    # Classify each ring as outer (CW, area>0 in Esri) or hole (CCW, area<0).
    outers: list[Sequence[Sequence[float]]] = []
    holes: list[Sequence[Sequence[float]]] = []
    for ring in rings:
        if _ring_signed_area(ring) >= 0:
            outers.append(ring)
        else:
            holes.append(ring)
    if not outers:
        # Some servers return all rings CCW; fall back to first ring as outer.
        outers = [rings[0]]
        holes = list(rings[1:])
    multipolygon = len(outers) > 1
    if multipolygon:
        outers.sort(key=lambda r: abs(_ring_signed_area(r)), reverse=True)
        # Drop the smaller outer rings; keep their associated holes only if
        # they fall inside the kept outer (we can't cheaply test that here,
        # so drop all holes when reducing — this is a documented Phase 3
        # simplification, R-07).
        kept_outer = outers[0]
        kept_holes: list[Sequence[Sequence[float]]] = []
    else:
        kept_outer = outers[0]
        kept_holes = holes
    parts = [_ring_to_wkt(kept_outer), *(_ring_to_wkt(h) for h in kept_holes)]
    wkt = f"POLYGON({','.join(parts)})"
    return wkt, multipolygon, kept_outer


def _ring_to_wkt(ring: Sequence[Sequence[float]]) -> str:
    pts = ", ".join(f"{p[0]} {p[1]}" for p in ring)
    return f"({pts})"


def _ring_centroid(ring: Sequence[Sequence[float]]) -> tuple[float, float]:
    """Approximate centroid of a polygon ring via vertex average. R-08 sanity."""
    if not ring:
        raise ValueError("empty ring")
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _check_srid_sanity(centroid_lng: float, centroid_lat: float) -> bool:
    """Return True iff (lng, lat) plausibly are WGS84 degrees (R-08)."""
    if centroid_lng is None or centroid_lat is None:
        return False
    return -180.0 <= centroid_lng <= 180.0 and -90.0 <= centroid_lat <= 90.0


# ---------------------------------------------------------------------------
# Owner / address parsers (R-26, R-27, R-28)
# ---------------------------------------------------------------------------
def _compose_mailing(attrs: Mapping[str, Any], mapping: Mapping[str, str]) -> str:
    """Concatenate OwnerAddr1 + OwnerAddr2; strip ATTN/CO prefixes (R-26)."""
    a1 = attrs.get(mapping.get("owner_mailing_address", "OwnerAddr1")) or ""
    a2 = attrs.get(mapping.get("owner_mailing_address_2", "OwnerAddr2")) or ""
    a1 = str(a1).strip()
    a2 = str(a2).strip()
    if a2:
        joined = f"{a1} {a2}".strip()
    else:
        joined = a1
    # Strip ATTN: / C/O prefixes to mirror the harness fix-forward (Phase 2
    # commit 4263630). Case-insensitive match, anchored at start.
    joined = re.sub(r"^\s*(ATTN[:\s]+|C/O\s+|CO\s+)", "", joined, flags=re.IGNORECASE)
    return joined.strip()


def _infer_owner_type(
    owner_name: str | None,
    classification: Mapping[str, Sequence[str]],
) -> str:
    """Return one of: government, corporate, llc, trust, estate, individual.

    Priority order (R-27): government before corporate before llc before
    trust before estate before individual. Keyword substring match —
    keywords are loaded verbatim (no .strip()) so trailing-space tokens
    like ``"TR "`` correctly disambiguate from ``"TRUMP"``.
    """
    if not owner_name:
        return "unknown"
    name = str(owner_name).upper()
    priority: list[tuple[str, str]] = [
        ("government", "government_keywords"),
        ("corporate", "corporate_keywords"),
        ("llc", "llc_keywords"),
        ("trust", "trust_keywords"),
        ("estate", "estate_keywords"),
    ]
    for label, key in priority:
        for kw in classification.get(key, ()):
            # Do NOT .strip() the keyword — trailing-space tokens are part
            # of the disambiguation per parameters.json.
            if str(kw).upper() in name:
                return label
    return "individual"


def _coerce_int(v: Any) -> int | None:
    """Coerce a value to int or None. Empty string / non-digit → None (R-28)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v != v:  # NaN
            return None
        return int(v)
    s = str(v).strip()
    if not s or s.lower() in {"none", "null", "nan"}:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        if f != f:
            return None
        return f
    s = str(v).strip()
    if not s or s.lower() in {"none", "null", "nan"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Hard filter callables (R-20, R-21, R-22, R-23, R-24, R-42)
# ---------------------------------------------------------------------------
def _in_fulton_envelope(centroid_lng: float, centroid_lat: float) -> bool:
    """H1 envelope check (R-20). Phase 4 will replace with true polygon."""
    env = _FULTON_ENVELOPE
    return (
        env["xmin"] <= centroid_lng <= env["xmax"]
        and env["ymin"] <= centroid_lat <= env["ymax"]
    )


def _h2_pass(acreage: float | None, params: Mapping[str, Any]) -> bool:
    """H2 acreage range check (R-21). Inclusive at both endpoints."""
    if acreage is None:
        return False
    lo = float(params["hard_filters"]["acreage_min"])
    hi = float(params["hard_filters"]["acreage_max"])
    return lo <= float(acreage) <= hi


def _h1_filter(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    lng = parcel.get("centroid_lng")
    lat = parcel.get("centroid_lat")
    if lng is None or lat is None:
        return _FilterResult("reject", "H1", "missing centroid coordinates")
    if not _in_fulton_envelope(lng, lat):
        return _FilterResult(
            "reject",
            "H1",
            f"centroid ({lat:.5f},{lng:.5f}) outside Fulton envelope",
        )
    return _FilterResult("pass", "H1", "")


def _h2_filter(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    acreage = parcel.get("acreage")
    if not _h2_pass(acreage, params):
        lo = params["hard_filters"]["acreage_min"]
        hi = params["hard_filters"]["acreage_max"]
        return _FilterResult(
            "reject", "H2",
            f"acreage {acreage} outside [{lo},{hi}]",
        )
    return _FilterResult("pass", "H2", "")


def _h3_flag(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    """H3 zoning is unjoined — emit data_gap flag, parcel passes (R-22). Replace body in Phase 5+; preserve signature."""
    return _FilterResult(
        "flag", "H3",
        "H3 zoning unjoined: pending Layer 34 cross-query (Phase 5+)",
    )


def _h4_flag(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    """H4 flood is unjoined — emit data_gap flag, parcel passes (R-23). Replace body in Phase 5+; preserve signature."""
    return _FilterResult(
        "flag", "H4",
        "H4 flood unjoined: pending FEMA NFIP wiring (Phase 5+)",
    )


def _h5_filter(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    """H5 environmental contamination is unjoined — emit data_gap flag, parcel passes (Phase 4 stub). Eventual implementation will spatially join EPA Envirofacts (NPL/RCRA) + state EPD/GEOS brownfield registries with a 500 ft adjacency buffer; replace body in Phase 5+, preserve (parcel, conn, params) -> _FilterResult signature."""
    return _FilterResult(
        "flag", "H5",
        "H5 environmental unjoined: pending EPA Envirofacts + state EPD wiring (Phase 5+)",
    )


def _h6_filter(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    """H6 wetlands is unjoined — emit data_gap flag, parcel passes (Phase 4 stub). Eventual implementation will read params['hard_filters']['wetlands_max_pct_of_parcel'] (default 20) and compute ST_Area(ST_Intersection(parcel,wetland))/ST_Area(parcel) against USGS NWI; replace body in Phase 5+, preserve signature."""
    return _FilterResult(
        "flag", "H6",
        "H6 wetlands unjoined: pending USGS NWI mapper wiring (Phase 5+)",
    )


def _h7_filter(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    """H7 road access is unjoined — emit data_gap flag, parcel passes (Phase 4 stub). Eventual implementation will read params['hard_filters']['min_road_classification'] (default 'county_collector') and test ST_Touches/ST_Intersects against a county road classification + DOT layer; replace body in Phase 5+, preserve signature."""
    return _FilterResult(
        "flag", "H7",
        "H7 road access unjoined: pending county road classification + DOT layer wiring (Phase 5+)",
    )


def _h8_filter(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    """H8 utility availability is unjoined — emit data_gap flag, parcel passes (Phase 4 stub). Eventual implementation will read params['hard_filters']['max_utility_extension_ft'] (default 1500) and compute ST_Distance to the nearest water/sewer main from utility provider service maps; replace body in Phase 5+, preserve signature."""
    return _FilterResult(
        "flag", "H8",
        "H8 utility availability unjoined: pending utility provider service map + extension-distance wiring (Phase 5+)",
    )


def _h9_filter(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    """H9 topography is unjoined — emit data_gap flag, parcel passes (Phase 4 stub). Eventual implementation will read params['hard_filters']['max_grade_differential_ft'] (default 15) and compute zonal max-min on USGS 3DEP elevation rasters; Phase 5+ should share the grade-differential computation with scored parameter S3. Replace body in Phase 5+, preserve signature."""
    return _FilterResult(
        "flag", "H9",
        "H9 topography unjoined: pending USGS 3DEP elevation wiring (Phase 5+)",
    )


def _h10_filter(
    parcel: Mapping[str, Any], conn: Any, params: Mapping[str, Any]
) -> _FilterResult:
    """H10 ownership availability is unjoined — emit data_gap flag, parcel passes (Phase 4 stub). Eventual implementation will join county deed records / Clerk of Court for active conservation easements and county assessor for government-owner-without-disposition tests; do NOT short-circuit on parcels.owner_type_inferred='government' here (R-110). Replace body in Phase 5+, preserve signature."""
    return _FilterResult(
        "flag", "H10",
        "H10 ownership availability unjoined: pending deed records + conservation easement registry wiring (Phase 5+)",
    )


# Pipeline order: H1 → H2 → (insert) → H3..H10 (flag-only stubs).
# Reject filters MUST come first; PASS-WITH-FLAG stubs follow (R-102, R-24).
# Phase 4 added H5..H10 as PASS-WITH-FLAG stubs; appended at the end so the
# H1/H2 short-circuit semantics in _process_parcel are preserved. When Phase 5+
# replaces a stub with a reject-capable real filter, ordering must be revisited.
_HARD_FILTERS: list[Any] = [
    _h1_filter, _h2_filter,
    _h3_flag, _h4_flag,
    _h5_filter, _h6_filter, _h7_filter, _h8_filter, _h9_filter, _h10_filter,
]


# ---------------------------------------------------------------------------
# SQL — every statement is a module-level constant string. No f-string
# interpolation, no %-format. All bound values pass through psycopg's
# parameterized-execute path (R-05).
# ---------------------------------------------------------------------------
_SQL_INSERT_RESEARCH_LOG = (
    "INSERT INTO research_log "
    "(cycle_id, action_type, market, parcel_id, notes) "
    "VALUES (%s, %s, %s, %s, %s)"
)

_SQL_COUNT_LOG_FOR_CYCLE = (
    "SELECT COUNT(*) FROM research_log WHERE cycle_id = %s"
)

# Phase 7+8 (R-501): _SQL_INSERT_PARCEL_SCORE writes 10 columns into the
# parcel_scores DDL block defined in prepare.py:317-332. The DDL itself is
# untouched; only the INSERT projection is extended. Columns added vs.
# Phase 5: actionability_blockers (JSONB), strategy_fit (JSONB),
# primary_strategy (TEXT). The static-check test
# TestPhase78SqlConstantsStaticChecks asserts each named column in this
# string also appears in prepare._DDL_PARCEL_SCORES.
_SQL_INSERT_PARCEL_SCORE = (
    "INSERT INTO parcel_scores ("
    "parcel_id, composite_score, confidence_score, "
    "actionability, actionability_blockers, "
    "sub_scores, strategy_fit, primary_strategy, notes, "
    "run_tag, experiment_id"
    ") VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s) "
    "RETURNING score_id"
)

_SQL_INSERT_RESEARCH_LOG_SCORING = (
    "INSERT INTO research_log "
    "(cycle_id, action_type, market, parcel_id, "
    "composite_score, actionability, strategy_fit, notes) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)

# Phase 7+8 (R-526, R-527, R-535..R-539, R-544): _SQL_FETCH_PARCEL is
# extended with submarket, state, acreage, last_sale_date,
# last_sale_price, and assessed_value_total. These feed the S4/S5/S6
# market_context join (submarket), the S8 basis proxy ladder
# (last_sale_*, assessed_value_total, acreage), the GA assessed-value
# 2.5x inflation rule (state), and the BTS/ground-lease minimum-acreage
# strategy-fit branches. Phase 5 tests that queued the 4-column tuple
# are updated to queue the 9-column tuple.
_SQL_FETCH_PARCEL = (
    "SELECT parcel_id, market, submarket, state, acreage, "
    "last_sale_date, last_sale_price, assessed_value_total, "
    "ST_X(centroid)::float AS centroid_lng, "
    "ST_Y(centroid)::float AS centroid_lat "
    "FROM parcels WHERE parcel_id = %s"
)

# S2 PostGIS query — returns area, bbox area, and aspect ratio in one shot.
# NULLIF guards the divide-by-zero in degenerate (zero-extent) bbox cases.
# ST_MakeValid + ST_CollectionExtract(_, 3) repairs malformed polygons
# before the spheroid area calc -- otherwise a single self-intersecting
# parcel raises lwgeom_area_spher() returned area < 0.0 and crashes the
# entire scoring cycle. CollectionExtract type=3 keeps only the Polygon /
# MultiPolygon component of whatever ST_MakeValid produces, so the
# geography cast is unambiguous.
_SQL_S2_GEOMETRY = (
    "WITH g AS ("
    "  SELECT ST_CollectionExtract(ST_MakeValid(geometry), 3) AS geom, "
    "         ST_Envelope(geometry) AS bbox "
    "  FROM parcels WHERE parcel_id = %s"
    ") "
    "SELECT "
    "  ST_Area(geom::geography) AS area_m2, "
    "  ST_Area(bbox::geography) AS bbox_area_m2, "
    "  GREATEST(ST_XMax(bbox)-ST_XMin(bbox), ST_YMax(bbox)-ST_YMin(bbox)) "
    "  / NULLIF(LEAST(ST_XMax(bbox)-ST_XMin(bbox), "
    "                 ST_YMax(bbox)-ST_YMin(bbox)), 0) AS aspect_ratio "
    "FROM g WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom)"
)

# Phase 7+8 (R-507, R-510): include parcels whose LATEST parcel_scores
# row has actionability='PENDING'. Phase 5 left every scored parcel at
# PENDING; Phase 7+8 must re-score those so the metric SQL in prepare.py
# (which selects MAX(scored_at)) sees a PASS verdict on a fresh row.
# The new row is APPENDED — we never UPDATE in place.
_SQL_LIST_PARCELS_FOR_SCORING = (
    "SELECT p.parcel_id FROM parcels p "
    "WHERE p.market = %s "
    "AND ("
    "  NOT EXISTS ("
    "    SELECT 1 FROM parcel_scores ps WHERE ps.parcel_id = p.parcel_id"
    "  )"
    "  OR ("
    "    SELECT ps.actionability FROM parcel_scores ps "
    "    WHERE ps.parcel_id = p.parcel_id "
    "    ORDER BY ps.scored_at DESC, ps.score_id DESC LIMIT 1"
    "  ) = 'PENDING'"
    ") "
    "ORDER BY p.parcel_id"
)

# prepare-mutation (2026-07-07): run-scoped selection variant. Inside a run,
# "needs scoring" means "no row IN THIS RUN or latest row IN THIS RUN is
# PENDING" — a parcel scored PASS in a *previous* run is re-scored once per
# run so the run-scoped metric can see it. Purging a discarded experiment's
# rows returns its parcels to this selection automatically. The score_id
# DESC tie-break matches prepare.py's DISTINCT ON selector so the scoring
# layer and the metric can never disagree about which row is "latest".
# Params: (market, run_tag, run_tag).
_SQL_LIST_PARCELS_FOR_SCORING_RUN_SCOPED = (
    "SELECT p.parcel_id FROM parcels p "
    "WHERE p.market = %s "
    "AND ("
    "  NOT EXISTS ("
    "    SELECT 1 FROM parcel_scores ps "
    "    WHERE ps.parcel_id = p.parcel_id AND ps.run_tag = %s"
    "  )"
    "  OR ("
    "    SELECT ps.actionability FROM parcel_scores ps "
    "    WHERE ps.parcel_id = p.parcel_id AND ps.run_tag = %s "
    "    ORDER BY ps.scored_at DESC, ps.score_id DESC LIMIT 1"
    "  ) = 'PENDING'"
    ") "
    "ORDER BY p.parcel_id"
)

# Backwards-compat alias retained for the AST scanner test that walks
# module-level SQL constants. New callers use _SQL_LIST_PARCELS_FOR_SCORING.
_SQL_LIST_UNSCORED_PARCELS = _SQL_LIST_PARCELS_FOR_SCORING

_SQL_COUNT_LOG_FOR_SCORING_CYCLE = (
    "SELECT COUNT(*) FROM research_log "
    "WHERE cycle_id = %s AND action_type = 'scoring'"
)

# Phase 13 (R-1310): set-based form of _SQL_LATEST_MARKET_CONTEXT for the
# per-cycle prefetch. DISTINCT ON (submarket_id) reproduces the single-key
# LIMIT 1 by leading the ORDER BY with submarket_id and then carrying the
# EXACT same CoStar-preference CASE + as_of_date DESC tail, so the chosen row
# per submarket is bit-identical to the per-parcel query. A GROUP BY/MAX
# rewrite is WRONG here — it cannot reproduce the CoStar tie-break. submarket_id
# is bound as a single text[] via ANY(%s) (R-1316: pass as a 1-tuple).
_SQL_LATEST_MARKET_CONTEXT_BATCH = (
    "SELECT DISTINCT ON (submarket_id) submarket_id, "
    "vacancy_rate_pct, net_absorption_t12_sf, "
    "under_construction_sf, proposed_sf, asking_rent_nnn_psf, "
    "as_of_date, source "
    "FROM market_context "
    "WHERE submarket_id = ANY(%s) "
    "ORDER BY submarket_id, "
    "(CASE WHEN source = 'costar' THEN 0 ELSE 1 END), "
    "as_of_date DESC"
)

# Phase 7 (R-523..R-526): submarket land-only median price-per-acre
# computed over comp_type='land' rows in the last 36 months. The
# minimum sample size of 3 (R-524) is enforced in code, not SQL — we
# return both the median AND the count so the caller can decide.
_SQL_SUBMARKET_LAND_MEDIAN = (
    "SELECT "
    "  COUNT(*) AS n, "
    "  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_per_acre) "
    "    AS median_price_per_acre "
    "FROM sales_comps "
    "WHERE submarket_id = %s "
    "  AND comp_type = 'land' "
    "  AND price_per_acre IS NOT NULL "
    "  AND sale_date >= (CURRENT_DATE - INTERVAL '36 months')"
)

# Phase 13 (R-1310): set-based form of _SQL_SUBMARKET_LAND_MEDIAN for the
# per-cycle prefetch. GROUP BY submarket_id is correct here (an aggregate, not
# a latest-row pick) and the WHERE filters (comp_type='land', price_per_acre
# IS NOT NULL, 36-month window) are byte-identical to the single-key query, so
# each submarket's (n, median) is bit-identical. CURRENT_DATE is evaluated once
# per cycle instead of once per parcel — a consistency improvement that is
# identical in practice except for a cycle straddling UTC midnight (R-1314),
# which cannot occur in the fake-cursor offline tests. submarket_id is bound as
# a single text[] via ANY(%s) (R-1316).
_SQL_SUBMARKET_LAND_MEDIAN_BATCH = (
    "SELECT "
    "  submarket_id, "
    "  COUNT(*) AS n, "
    "  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_per_acre) "
    "    AS median_price_per_acre "
    "FROM sales_comps "
    "WHERE submarket_id = ANY(%s) "
    "  AND comp_type = 'land' "
    "  AND price_per_acre IS NOT NULL "
    "  AND sale_date >= (CURRENT_DATE - INTERVAL '36 months') "
    "GROUP BY submarket_id"
)

# Phase 8 (R-533): synthetic deal-killer evidence comes from open
# flagged_items rows of flag_type='actionability_block' attached to the
# parcel. Phase 11+ adds richer signals (PACER, lis pendens, etc.); for
# now this is the single channel the agent honours.
_SQL_FLAGGED_ACTIONABILITY_BLOCK = (
    "SELECT description FROM flagged_items "
    "WHERE parcel_id = %s "
    "  AND flag_type = 'actionability_block' "
    "  AND status = 'open' "
    "ORDER BY flagged_at DESC LIMIT 1"
)

# Phase 13 (R-1310, R-1311): set-based form of _SQL_FLAGGED_ACTIONABILITY_BLOCK
# for the per-cycle prefetch. DISTINCT ON (parcel_id) leads the ORDER BY with
# parcel_id, then flagged_at DESC (matching the single-key query), then a
# flag_id DESC tie-break for DETERMINISM. The single-key query above keeps NO
# tie-break, so on the vanishingly rare two-open-blocks-same-microsecond case
# the batch path is strictly MORE deterministic than the per-parcel path. This
# micro-divergence is documented and unobservable in practice (the deal-killer
# gate only asks whether ANY open block mentions a non-entitlement keyword); see
# Agent 2 response R-1311. parcel_id is bound as a single text[] via ANY(%s).
_SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH = (
    "SELECT DISTINCT ON (parcel_id) parcel_id, description FROM flagged_items "
    "WHERE parcel_id = ANY(%s) "
    "  AND flag_type = 'actionability_block' "
    "  AND status = 'open' "
    "ORDER BY parcel_id, flagged_at DESC, flag_id DESC"
)

# Phase 13 (R-1317): the distinct non-null submarkets for the cycle's parcel
# set, used to bound the market-context and land-median prefetch arrays. Keyed
# off the SAME parcel_ids the scoring loop iterates so the cache never misses a
# scored parcel's submarket. Raw submarket strings (no normalization, R-1315).
_SQL_DISTINCT_SUBMARKETS_FOR_PARCELS = (
    "SELECT DISTINCT submarket FROM parcels "
    "WHERE parcel_id = ANY(%s) AND submarket IS NOT NULL"
)


_SQL_UPSERT_PARCEL = (
    "INSERT INTO parcels ("
    "parcel_id, county, state, market, submarket, "
    "address, owner_name, owner_mailing_address, owner_type_inferred, "
    "acreage, land_sf, zoning, zoning_description, "
    "land_use_code, land_use_description, "
    "assessed_value_land, assessed_value_improvement, assessed_value_total, "
    "fair_market_value, tax_year, tax_amount, tax_status, "
    "last_sale_date, last_sale_price, deed_book_page, "
    "year_built, improvement_sf, "
    "geometry, centroid, "
    "discovery_source, discovery_date, "
    "last_updated, raw_response_path"
    ") VALUES ("
    "%s, %s, %s, %s, %s, "
    "%s, %s, %s, %s, "
    "%s, %s, %s, %s, "
    "%s, %s, "
    "%s, %s, %s, "
    "%s, %s, %s, %s, "
    "%s, %s, %s, "
    "%s, %s, "
    "ST_GeomFromText(%s, 4326), ST_Centroid(ST_GeomFromText(%s, 4326)), "
    "%s, %s, "
    "NOW(), %s"
    ") "
    "ON CONFLICT (parcel_id) DO UPDATE SET "
    "address = EXCLUDED.address, "
    "owner_name = EXCLUDED.owner_name, "
    "owner_mailing_address = EXCLUDED.owner_mailing_address, "
    "owner_type_inferred = EXCLUDED.owner_type_inferred, "
    "acreage = EXCLUDED.acreage, "
    "land_sf = EXCLUDED.land_sf, "
    "zoning = EXCLUDED.zoning, "
    "zoning_description = EXCLUDED.zoning_description, "
    "land_use_code = EXCLUDED.land_use_code, "
    "land_use_description = EXCLUDED.land_use_description, "
    "assessed_value_land = EXCLUDED.assessed_value_land, "
    "assessed_value_improvement = EXCLUDED.assessed_value_improvement, "
    "assessed_value_total = EXCLUDED.assessed_value_total, "
    "fair_market_value = EXCLUDED.fair_market_value, "
    "tax_year = EXCLUDED.tax_year, "
    "tax_amount = EXCLUDED.tax_amount, "
    "tax_status = EXCLUDED.tax_status, "
    "last_sale_date = EXCLUDED.last_sale_date, "
    "last_sale_price = EXCLUDED.last_sale_price, "
    "deed_book_page = EXCLUDED.deed_book_page, "
    "year_built = EXCLUDED.year_built, "
    "improvement_sf = EXCLUDED.improvement_sf, "
    "geometry = EXCLUDED.geometry, "
    "centroid = EXCLUDED.centroid, "
    "discovery_source = COALESCE(parcels.discovery_source, EXCLUDED.discovery_source), "
    "discovery_date = COALESCE(parcels.discovery_date, EXCLUDED.discovery_date), "
    "last_updated = NOW(), "
    "raw_response_path = EXCLUDED.raw_response_path"
)


def _log_research(
    conn: Any,
    cycle_id: str,
    action_type: str,
    market: str,
    parcel_id: str | None,
    notes: str,
) -> None:
    """Insert one research_log row. Caller controls transaction commit."""
    with conn.cursor() as cur:
        cur.execute(
            _SQL_INSERT_RESEARCH_LOG,
            (cycle_id, action_type, market, parcel_id, notes),
        )


def _count_log_rows(conn: Any, cycle_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(_SQL_COUNT_LOG_FOR_CYCLE, (cycle_id,))
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _upsert_parcel(conn: Any, row: Mapping[str, Any], wkt: str) -> None:
    """UPSERT one parcel row. Geometry passed as WKT, server-parsed (R-09)."""
    params: tuple[Any, ...] = (
        row["parcel_id"],
        row["county"],
        row["state"],
        row["market"],
        row.get("submarket"),
        row.get("address"),
        row.get("owner_name"),
        row.get("owner_mailing_address"),
        row.get("owner_type_inferred"),
        row.get("acreage"),
        row.get("land_sf"),
        row.get("zoning"),
        row.get("zoning_description"),
        row.get("land_use_code"),
        row.get("land_use_description"),
        row.get("assessed_value_land"),
        row.get("assessed_value_improvement"),
        row.get("assessed_value_total"),
        row.get("fair_market_value"),
        row.get("tax_year"),
        row.get("tax_amount"),
        row.get("tax_status"),
        row.get("last_sale_date"),
        row.get("last_sale_price"),
        row.get("deed_book_page"),
        row.get("year_built"),
        row.get("improvement_sf"),
        wkt,
        wkt,
        row.get("discovery_source"),
        row.get("discovery_date"),
        row.get("raw_response_path"),
    )
    with conn.cursor() as cur:
        cur.execute(_SQL_UPSERT_PARCEL, params)


# ---------------------------------------------------------------------------
# ArcGIS query loop (R-13, R-14, R-15, R-16, R-18, R-19, R-25)
# ---------------------------------------------------------------------------
def _check_field_mapping_drift(
    session: _DiscoverySession,
    service_url: str,
    layer_id: int,
    mapping: Mapping[str, str],
) -> tuple[bool, list[str]]:
    """Belt-and-suspenders defense for R-25.

    Fetch the Layer schema and confirm every mapped ArcGIS field name is
    present. Returns ``(ok, missing_fields)``. The harness already does
    this check on its schedule; replicating it inside the cycle catches
    drift between harness runs.
    """
    schema_url = f"{service_url.rstrip('/')}/{layer_id}"
    body = session.get(schema_url, params={"f": "pjson"})
    fields = body.get("fields") or []
    available = {f.get("name") for f in fields if f.get("name")}
    needed = {v for v in mapping.values() if v}
    missing = sorted(needed - available)
    return (not missing), missing


def _build_known_query_params(
    bbox: Mapping[str, float],
    page_size: int,
    offset: int,
    params: Mapping[str, Any],
    mapping: Mapping[str, str],
) -> dict[str, Any]:
    """Construct the corridor query params (R-15).

    The ``where`` clause uses integer-valued acreage bounds from
    parameters.json; field name comes from the mapping; both are
    whitelist-validated.
    """
    acreage_field = mapping.get("acreage", "LandAcres")
    if not re.match(r"^[A-Za-z0-9_]+$", acreage_field):
        raise ValueError(f"unsafe acreage field name: {acreage_field!r}")
    lo = int(params["hard_filters"]["acreage_min"])
    hi = int(params["hard_filters"]["acreage_max"])
    where = f"{acreage_field} BETWEEN {lo} AND {hi}"
    parcel_id_field = mapping.get("parcel_id", "ParcelID")
    if not re.match(r"^[A-Za-z0-9_]+$", parcel_id_field):
        raise ValueError(f"unsafe parcel_id field name: {parcel_id_field!r}")
    return {
        "where": where,
        "geometry": f"{bbox['xmin']},{bbox['ymin']},{bbox['xmax']},{bbox['ymax']}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
        "resultRecordCount": page_size,
        "resultOffset": offset,
        "orderByFields": parcel_id_field,
    }


def _query_arcgis_corridor(
    session: _DiscoverySession,
    service_url: str,
    layer_id: int,
    bbox: Mapping[str, float],
    mapping: Mapping[str, str],
    params: Mapping[str, Any],
    cycle_id: str,
    corridor_name: str,
    page_size: int = _FULTON_PAGE_SIZE,
) -> Iterator[dict[str, Any]]:
    """Page through ArcGIS for one corridor; yield Esri features.

    Honors ``exceededTransferLimit`` if present, falls back to
    ``len(features) < page_size`` otherwise (R-13). Caches each raw
    response under ``sources/{cycle_id}/{corridor}_{offset}.json`` for
    audit (R-30, R-40). Empty corridors are not an error (R-19).
    """
    base = f"{service_url.rstrip('/')}/{layer_id}/query"
    offset = 0
    page = 0
    while True:
        page += 1
        request_params = _build_known_query_params(
            bbox, page_size, offset, params, mapping,
        )
        body = session.get(base, params=request_params)
        cache_path = _safe_cache_path(cycle_id, corridor_name, offset)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        features = body.get("features") or []
        log.info(
            "arcgis page %s of corridor=%s offset=%s features=%s",
            page, corridor_name, offset, len(features),
        )
        for feat in features:
            yield feat
        # Termination: exceededTransferLimit==False, or short page.
        exceeded = body.get("exceededTransferLimit")
        if exceeded is False:
            break
        if exceeded is None and len(features) < page_size:
            break
        if not features:
            break
        offset += page_size


# ---------------------------------------------------------------------------
# ArcGIS feature → parcels-row mapper (R-06, R-08, R-44)
# ---------------------------------------------------------------------------
def _map_feature_to_parcel(
    feature: Mapping[str, Any],
    mapping: Mapping[str, str],
    classification: Mapping[str, Sequence[str]],
    market: str,
    corridor_name: str,
    raw_response_path: str,
    today: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None, bool]:
    """Map one Esri feature to a parcels-row dict + a WKT polygon string.

    Returns ``(row, wkt, reason_skip, multipolygon_reduced)``.
    ``row`` and ``wkt`` are None if the feature cannot be mapped (e.g.
    SRID failure, missing parcel_id, missing geometry). ``reason_skip``
    is a short string suitable for a research_log/flagged_items row.
    """
    attrs = feature.get("attributes") or {}
    geom = feature.get("geometry") or {}
    parcel_id_field = mapping.get("parcel_id", "ParcelID")
    raw_id = attrs.get(parcel_id_field)
    if raw_id is None or str(raw_id).strip() == "":
        return None, None, "missing parcel_id field", False
    # Phase 3 county-prefix discipline (R-06).
    parcel_id = f"fulton-{str(raw_id).strip()}"

    rings = geom.get("rings") or []
    if not rings:
        return None, None, "missing polygon rings", False
    try:
        wkt, multipolygon, kept_outer = _arcgis_polygon_to_wkt(rings)
    except ValueError as exc:
        return None, None, f"polygon construction failed: {exc}", False

    # Phase 3.1 §6.B fix: centroid must come from the *kept* outer ring, not
    # rings[0]. For multi-polygon parcels where the largest ring is not first,
    # using rings[0] would feed the wrong centroid into the H1 envelope check
    # while PostGIS's server-side ST_Centroid (computed from ``wkt``) sees the
    # correct one — silent client/server divergence.
    cx, cy = _ring_centroid(kept_outer)
    if not _check_srid_sanity(cx, cy):
        return None, None, "ArcGIS ignored outSR=4326 (centroid not in WGS84 range)", False

    acreage = _coerce_float(attrs.get(mapping.get("acreage", "LandAcres")))
    land_sf = acreage * 43560.0 if acreage is not None else None

    owner_name = attrs.get(mapping.get("owner_name", "Owner")) or None
    if owner_name is not None:
        owner_name = str(owner_name).strip() or None
    mailing = _compose_mailing(attrs, mapping)

    row: dict[str, Any] = {
        "parcel_id": parcel_id,
        "county": "fulton",
        "state": "GA",
        "market": market,
        "submarket": corridor_name,
        "address": _str_or_none(attrs.get(mapping.get("site_address", "Address"))),
        "owner_name": owner_name,
        "owner_mailing_address": mailing or None,
        "owner_type_inferred": _infer_owner_type(owner_name, classification),
        "acreage": acreage,
        "land_sf": land_sf,
        "zoning": _str_or_none(attrs.get(mapping.get("zoning", ""))),
        "zoning_description": None,
        "land_use_code": _str_or_none(attrs.get(mapping.get("land_use_code", "LUCode"))),
        "land_use_description": None,
        "assessed_value_land": _coerce_int(attrs.get(mapping.get("land_value", "LandAssess"))),
        "assessed_value_improvement": _coerce_int(attrs.get(mapping.get("improvement_value", "ImprAssess"))),
        "assessed_value_total": _coerce_int(attrs.get(mapping.get("total_value", "TotAssess"))),
        "fair_market_value": None,
        "tax_year": _coerce_int(attrs.get(mapping.get("tax_year", "TaxYear"))),
        "tax_amount": None,
        "tax_status": None,
        "last_sale_date": None,
        "last_sale_price": None,
        "deed_book_page": None,
        "year_built": None,
        "improvement_sf": None,
        "discovery_source": f"fulton_arcgis_layer11:{corridor_name}",
        "discovery_date": today or datetime.now(timezone.utc).date().isoformat(),
        "raw_response_path": raw_response_path,
        # Centroid carried alongside row for hard-filter access.
        "centroid_lng": cx,
        "centroid_lat": cy,
    }
    return row, wkt, None, multipolygon


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# ---------------------------------------------------------------------------
# Per-parcel processor (R-10, R-24, R-30)
# ---------------------------------------------------------------------------
def _process_parcel(
    feature: Mapping[str, Any],
    conn: Any,
    cycle_id: str,
    corridor_name: str,
    market: str,
    mapping: Mapping[str, str],
    classification: Mapping[str, Sequence[str]],
    params: Mapping[str, Any],
    raw_response_path: str,
) -> str:
    """Run the per-parcel pipeline; return summary status.

    Status one of: ``discovery``, ``rejection_h1``, ``rejection_h2``,
    ``skip_unmappable``. Each call uses its own transaction (R-10): on
    success, commit; on exception, rollback.
    """
    row, wkt, skip_reason, multipolygon = _map_feature_to_parcel(
        feature, mapping, classification, market, corridor_name, raw_response_path,
    )
    if row is None:
        # Unmappable feature: log a rejection-equivalent row but no parcel insert.
        try:
            with conn.transaction():
                _log_research(
                    conn, cycle_id, "rejection", market, None,
                    f"unmappable feature: {skip_reason}",
                )
        except Exception:
            log.exception("failed to log unmappable feature")
            try:
                conn.rollback()
            except Exception:
                pass
        return "skip_unmappable"

    parcel_id = row["parcel_id"]
    # Run hard filters once (R-24). Cache the results so the post-UPSERT flag
    # emission loop reuses them instead of re-running every filter — re-running
    # would double the DB cost and create a window where a state-dependent
    # filter could disagree between the two passes.
    filter_results = [filt(row, conn, params) for filt in _HARD_FILTERS]
    for result in filter_results:
        if result.action == "reject":
            try:
                with conn.transaction():
                    _log_research(
                        conn, cycle_id, "rejection", market, parcel_id,
                        f"{result.filter_id}: {result.reason}",
                    )
            except Exception:
                log.exception("failed to log H1/H2 rejection for %s", parcel_id)
                try:
                    conn.rollback()
                except Exception:
                    pass
            return f"rejection_{result.filter_id.lower()}"
        if result.action not in ("pass", "flag"):
            log.warning("unknown filter result action=%r for %s", result.action, parcel_id)

    # All reject filters passed. UPSERT parcel + log discovery + emit
    # accumulated flags inside one per-parcel transaction (R-10).
    try:
        with conn.transaction():
            _upsert_parcel(conn, row, wkt)
            _log_research(
                conn, cycle_id, "discovery", market, parcel_id,
                f"corridor={corridor_name}; acreage={row['acreage']}; owner_type={row['owner_type_inferred']}",
            )
            for result in filter_results:
                if result.action == "flag":
                    _flag(
                        conn, cycle_id, parcel_id, market,
                        "data_gap", result.reason,
                        f"Phase 4: resolve {result.filter_id} for parcel_id={parcel_id}",
                    )
            if multipolygon:
                _flag(
                    conn, cycle_id, parcel_id, market,
                    "data_gap",
                    "multi-polygon parcel reduced to largest outer ring (Phase 3 simplification)",
                    "Phase 4+: convert parcels.geometry column to MultiPolygon and reprocess",
                )
    except Exception:
        log.exception("transaction failed for parcel %s", parcel_id)
        try:
            conn.rollback()
        except Exception:
            pass
        return "skip_unmappable"
    return "discovery"


# ---------------------------------------------------------------------------
# Per-corridor and per-county drivers
# ---------------------------------------------------------------------------
def _discover_fulton_corridor(
    session: _DiscoverySession,
    conn: Any,
    cycle_id: str,
    corridor_name: str,
    bbox: Mapping[str, float],
    market: str,
    sources: Mapping[str, Any],
    mapping: Mapping[str, str],
    classification: Mapping[str, Sequence[str]],
    params: Mapping[str, Any],
) -> dict[str, int]:
    """Run one corridor end-to-end. Catches network failures (R-18)."""
    counts = {"discovery": 0, "rejection": 0, "unmappable": 0}
    service_url = _fulton_service_url(sources)
    layer_id = _fulton_parcel_layer_id(sources)
    offset_for_audit = 0
    try:
        empty = True
        for feat in _query_arcgis_corridor(
            session, service_url, layer_id, bbox, mapping, params,
            cycle_id, corridor_name,
        ):
            empty = False
            cache_path = _safe_cache_path(cycle_id, corridor_name, offset_for_audit)
            status = _process_parcel(
                feat, conn, cycle_id, corridor_name, market,
                mapping, classification, params,
                raw_response_path=str(cache_path),
            )
            if status == "discovery":
                counts["discovery"] += 1
            elif status.startswith("rejection_"):
                counts["rejection"] += 1
            else:
                counts["unmappable"] += 1
        if empty:
            try:
                with conn.transaction():
                    _log_research(
                        conn, cycle_id, "discovery_empty", market, None,
                        f"corridor={corridor_name} returned 0 features",
                    )
            except Exception:
                log.exception("failed to log discovery_empty for %s", corridor_name)
    except (requests.ConnectionError, requests.HTTPError, requests.Timeout, requests.RequestException) as exc:
        log.warning("corridor %s aborted on network error: %s", corridor_name, exc)
        try:
            with conn.transaction():
                _log_research(
                    conn, cycle_id, "abort", market, None,
                    f"corridor={corridor_name}: network error: {exc!r}",
                )
                _flag(
                    conn, cycle_id, None, market, "data_gap",
                    f"partial corridor: {corridor_name}; processed_parcels={counts['discovery']+counts['rejection']+counts['unmappable']}",
                    f"re-run discovery for corridor={corridor_name} when upstream is healthy",
                )
        except Exception:
            log.exception("failed to log corridor abort")
    return counts


def _discover_fulton(
    session: _DiscoverySession,
    conn: Any,
    cycle_id: str,
    market: str,
    sources: Mapping[str, Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Run all configured Fulton corridors and aggregate counts."""
    mapping = _fulton_field_mapping(sources)
    classification = params["owner_classification"]
    # R-25: defensive schema-drift check. If any mapped field is missing,
    # abort the entire Fulton run for this cycle.
    ok, missing = _check_field_mapping_drift(
        session, _fulton_service_url(sources), _fulton_parcel_layer_id(sources), mapping,
    )
    if not ok:
        with conn.transaction():
            _log_research(
                conn, cycle_id, "abort", market, None,
                f"field-mapping drift: missing={missing}",
            )
        return {"aborted": True, "reason": "field_mapping_drift", "missing": missing}

    per_corridor: dict[str, dict[str, int]] = {}
    for corridor_name, bbox in _FULTON_CORRIDORS.items():
        per_corridor[corridor_name] = _discover_fulton_corridor(
            session, conn, cycle_id, corridor_name, bbox,
            market, sources, mapping, classification, params,
        )
    totals = {"discovery": 0, "rejection": 0, "unmappable": 0}
    for c in per_corridor.values():
        totals["discovery"] += c["discovery"]
        totals["rejection"] += c["rejection"]
        totals["unmappable"] += c["unmappable"]
    return {"aborted": False, "per_corridor": per_corridor, "totals": totals}


# Phase 11+ adds more counties to this dispatch (R-43).
_DISCOVERY_CONNECTORS: dict[str, Any] = {"fulton": _discover_fulton}


# ---------------------------------------------------------------------------
# Phase 3+: discovery (PUBLIC API)
# ---------------------------------------------------------------------------
def run_discovery_cycle(market: str) -> dict[str, Any]:
    """Run one Fulton-only discovery cycle for the given market.

    Phase 3 supports only ``market="atlanta"`` and only the Fulton county
    connector. See module docstring for the full Phase 3 contract.
    """
    if market not in _MARKET_TO_COUNTIES:
        raise NotImplementedError(
            f"market={market!r} not configured for Phase 3; only 'atlanta' is supported"
        )

    # R-03: SHA-256 sentinel — fail-loud on mid-run parameters mutation.
    prepare.verify_parameters_unchanged()
    params = prepare.get_parameters()  # R-02: read once, pass through.
    sources = _load_sources_json()

    cycle_id = _make_cycle_id("fulton")
    counties = _MARKET_TO_COUNTIES[market]

    summary: dict[str, Any] = {
        "cycle_id": cycle_id,
        "market": market,
        "counties": counties,
        "aborted": False,
        "abort_reason": None,
        "harness_status": None,
        "per_county": {},
    }

    session = _DiscoverySession()
    try:
        with prepare.get_connection() as conn:  # R-12: one connection per cycle.
            # R-32: cycle_id collision guard.
            if _count_log_rows(conn, cycle_id) > 0:
                summary["aborted"] = True
                summary["abort_reason"] = "cycle_id_collision"
                return summary

            try:
                summary["per_county"] = _run_for_counties(
                    counties, session, conn, cycle_id, market, sources, params, summary,
                )
            except KeyboardInterrupt:
                # R-36: KeyboardInterrupt leaves a coherent log.
                try:
                    with conn.transaction():
                        _log_research(
                            conn, cycle_id, "abort", market, None,
                            "KeyboardInterrupt during cycle",
                        )
                except Exception:
                    log.exception("failed to log KeyboardInterrupt abort")
                summary["aborted"] = True
                summary["abort_reason"] = "keyboard_interrupt"
                # exp: commit work-so-far before re-raising. prepare.get_connection()
                # opens conn with autocommit=False and does not commit on close, so
                # without this the SAVEPOINT releases inside _process_parcel would
                # be rolled back when the connection closes.
                conn.commit()
                raise
            # exp: commit at the end of the cycle. The first DB op inside this
            # with-block is _count_log_rows (a SELECT) which starts an implicit
            # transaction under autocommit=False; subsequent `with conn.transaction()`
            # calls in _process_parcel become SAVEPOINTs whose RELEASE does not
            # commit until the outer transaction commits. prepare.get_connection()
            # does not commit on close, so without this explicit commit the entire
            # cycle's work is rolled back.
            conn.commit()
    finally:
        session.close()

    return summary


def _run_for_counties(
    counties: Sequence[str],
    session: _DiscoverySession,
    conn: Any,
    cycle_id: str,
    market: str,
    sources: Mapping[str, Any],
    params: Mapping[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Per-county harness gate + dispatch (R-34). Returns per-county results."""
    out: dict[str, Any] = {}
    for county in counties:
        # R-34: harness gate is the first non-trivial action per county.
        harness_status, harness_report = _harness_gate(county)
        summary["harness_status"] = harness_status
        if harness_status == "failing":
            try:
                with conn.transaction():
                    _log_research(
                        conn, cycle_id, "abort", market, None,
                        f"harness=failing for county={county}; cycle aborted",
                    )
            except Exception:
                log.exception("failed to log harness=failing abort")
            summary["aborted"] = True
            summary["abort_reason"] = "harness_failing"
            out[county] = {"skipped": True, "reason": "harness_failing"}
            continue

        if harness_status == "degraded":
            try:
                with conn.transaction():
                    _flag(
                        conn, cycle_id, None, market, "data_gap",
                        f"harness=degraded for county={county}; proceeding with reduced confidence",
                        "review harness_reports for cause; refresh field_mapping if needed",
                    )
            except Exception:
                log.exception("failed to flag harness=degraded")

        connector = _DISCOVERY_CONNECTORS.get(county)
        if connector is None:
            log.warning("no connector registered for county=%s", county)
            out[county] = {"skipped": True, "reason": "no_connector"}
            continue
        out[county] = connector(session, conn, cycle_id, market, sources, params)
    return out


def _harness_gate(county: str) -> tuple[str, dict[str, Any] | None]:
    """Call the harness; return (status, report). Harness raise → 'failing' (R-34)."""
    try:
        report = connector_harness.run_harness_for_county(county)
    except Exception as exc:
        log.error("harness raised for county=%s: %s", county, exc)
        return "failing", None
    status = (report or {}).get("overall_health") or "failing"
    if status not in {"healthy", "degraded", "failing"}:
        log.warning("unknown harness status %r; treating as failing", status)
        return "failing", report
    return status, report


# ---------------------------------------------------------------------------
# Phase 5: scoring engine MVP — Option B (S2 real, S9 stub-moderate, S10 OZ
# real, all other sub-scores null with flagged_items data_gap rows).
#
# See reviews/07_phase5_scoring_mvp/01_risk_review.md for the 24 R-2XX risks
# and reviews/07_phase5_scoring_mvp/02_code_writer_response.md for the
# per-risk responses.
# ---------------------------------------------------------------------------
_OZ_DATA_PATH = _REPO_ROOT / "data" / "oz_ga_stub.geojson"

# Canonical sub-score order. Names match the keys in
# parameters.json["scoring_weights"] so the composite computation can
# zip them together without a separate mapping table.
_SUB_SCORE_NAMES: tuple[str, ...] = (
    "S1_interstate_proximity",
    "S2_parcel_geometry",
    "S3_topography",
    "S4_submarket_vacancy",
    "S5_submarket_absorption",
    "S6_competing_pipeline",
    "S7_labor_pool",
    "S8_land_basis",
    "S9_entitlement_complexity",
    "S10_incentives",
    "S11_rail_adjacency",
    "S12_demand_generators",
)

# Pretty names + data-source provenance (mirrors program.md L184-L197).
# Used in flagged_items rows for null sub-scores.
_SUB_SCORE_PROVENANCE: dict[str, tuple[str, str]] = {
    "S1_interstate_proximity": ("interstate proximity", "Google Maps API / GIS"),
    "S2_parcel_geometry": ("parcel geometry", "PostGIS (parcels.geometry)"),
    "S3_topography": ("topography / grading cost", "USGS 3DEP LiDAR"),
    "S4_submarket_vacancy": ("submarket vacancy", "CoStar"),
    "S5_submarket_absorption": ("submarket net absorption (T12)", "CoStar"),
    "S6_competing_pipeline": ("competing pipeline", "CoStar / Dodge Data"),
    "S7_labor_pool": ("labor pool density", "Census LODES / OnTheMap"),
    "S8_land_basis": ("land basis ($/acre)", "CoStar Land / assessor comps"),
    "S9_entitlement_complexity": ("entitlement complexity", "zoning ordinance / municipality"),
    "S10_incentives": ("incentive availability", "HUD OZ map / state EDA / municipality"),
    "S11_rail_adjacency": ("rail adjacency", "Class I railroad maps"),
    "S12_demand_generators": ("proximity to demand generators", "GIS / facility locations"),
}

# Phase 5 fixed value for S9 — see risk review §2.C and R-218.
_S9_MODERATE_DEFAULT: int = 5

# Phase 5 OZ-only S10 score: 1 of 3 incentive criteria → 4 per program.md L196.
_S10_OZ_ONLY_SCORE: int = 4

# Cycle id format for scoring cycles (parallel to the discovery format).
_SCORING_CYCLE_ID_RE = re.compile(
    r"^score-[a-z\-]+-\d{8}T\d{6}Z-[0-9a-f]{4}$"
)


def _make_scoring_cycle_id(market: str) -> str:
    """Generate a unique sortable scoring cycle id (R-213)."""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(2)
    return f"score-{market.lower()}-{now}-{suffix}"


# ---------------------------------------------------------------------------
# Pure-Python point-in-polygon (R-206) — PNPOLY ray-casting, ~30 lines.
# Avoids the shapely / GEOS native dep for one geometric check.
# ---------------------------------------------------------------------------
def _point_in_ring(lng: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    """PNPOLY ray-casting against a single closed ring of (lng, lat) pairs."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        intersects = ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-30) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _ring_bbox(ring: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# OZ tract loader — lazy at first call so module import stays cheap (R-205).
# ---------------------------------------------------------------------------
_OZ_TRACTS_CACHE: list[tuple[tuple[float, float, float, float], list[Sequence[Sequence[float]]], dict[str, Any]]] | None = None


def _load_oz_tracts() -> list[tuple[tuple[float, float, float, float], list[Sequence[Sequence[float]]], dict[str, Any]]]:
    """Load and cache the bundled OZ tract polygons.

    Returns a list of (bbox, [ring, ...], properties) tuples — one per
    Polygon Feature in the bundled GeoJSON. The bbox is precomputed for
    the spatial pre-filter (R-219).
    """
    global _OZ_TRACTS_CACHE
    if _OZ_TRACTS_CACHE is not None:
        return _OZ_TRACTS_CACHE
    if not _OZ_DATA_PATH.is_file():
        log.warning("OZ data file missing at %s; S10 will return None", _OZ_DATA_PATH)
        _OZ_TRACTS_CACHE = []
        return _OZ_TRACTS_CACHE
    with _OZ_DATA_PATH.open("r", encoding="utf-8") as fh:
        fc = json.load(fh)
    tracts: list[tuple[tuple[float, float, float, float], list[Sequence[Sequence[float]]], dict[str, Any]]] = []
    for feat in fc.get("features") or []:
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Polygon":
            continue
        rings = geom.get("coordinates") or []
        if not rings:
            continue
        outer = rings[0]
        bbox = _ring_bbox(outer)
        tracts.append((bbox, [outer], feat.get("properties") or {}))
    _OZ_TRACTS_CACHE = tracts
    return tracts


def _check_oz(centroid_lng: float | None, centroid_lat: float | None) -> bool:
    """True iff (lng, lat) falls inside any bundled OZ tract polygon (R-205, R-219)."""
    if centroid_lng is None or centroid_lat is None:
        return False
    for bbox, rings, _props in _load_oz_tracts():
        xmin, ymin, xmax, ymax = bbox
        # Closed-interval bbox pre-filter (R-219).
        if not (xmin <= centroid_lng <= xmax and ymin <= centroid_lat <= ymax):
            continue
        if _point_in_ring(centroid_lng, centroid_lat, rings[0]):
            return True
    return False


# ---------------------------------------------------------------------------
# Sub-score computations
# ---------------------------------------------------------------------------
def _score_geometry(
    area_m2: float | None,
    bbox_area_m2: float | None,
    aspect_ratio: float | None,
) -> int | None:
    """Pure-function S2 score mapping (R-207).

    Mapping per program.md L187:
        compactness >= 0.92 AND aspect in [1, 2]   -> 10
        compactness >= 0.85 AND aspect <= 3.0      -> 7
        compactness >= 0.65                         -> 4
        else                                        -> 0
    """
    if not area_m2 or not bbox_area_m2:
        return None
    compactness = float(area_m2) / float(bbox_area_m2)
    aspect = float(aspect_ratio) if aspect_ratio else 99.0
    if compactness >= 0.92 and 1.0 <= aspect <= 2.0:
        return 10
    if compactness >= 0.85 and aspect <= 3.0:
        return 7
    if compactness >= 0.65:
        return 4
    return 0


def _compute_s2(conn: Any, parcel_id: str) -> int | None:
    """S2 — parcel geometry. Returns 0..10 or None if geometry unavailable."""
    with conn.cursor() as cur:
        cur.execute(_SQL_S2_GEOMETRY, (parcel_id,))
        row = cur.fetchone()
    if not row:
        return None
    area_m2, bbox_area_m2, aspect_ratio = row[0], row[1], row[2]
    return _score_geometry(area_m2, bbox_area_m2, aspect_ratio)


def _compute_s9() -> int:
    """S9 — entitlement complexity. Phase 5 stub returns moderate default."""
    return _S9_MODERATE_DEFAULT


def _compute_s10(centroid_lng: float | None, centroid_lat: float | None) -> int | None:
    """S10 — incentives (OZ portion only). Returns 4 if in OZ, 0 if not, None if unknown."""
    if centroid_lng is None or centroid_lat is None:
        return None
    return _S10_OZ_ONLY_SCORE if _check_oz(centroid_lng, centroid_lat) else 0


# ---------------------------------------------------------------------------
# Composite + confidence
# ---------------------------------------------------------------------------
def _compute_composite(
    sub_scores: Mapping[str, int | None],
    weights: Mapping[str, int],
) -> float | None:
    """Weighted composite per program.md L201-L203.

    Sums only non-null sub-scores; returns None when total weight is zero
    (R-203). Result is on the 0-100 scale.
    """
    weighted_sum = 0.0
    weight_sum = 0
    for name in _SUB_SCORE_NAMES:
        score = sub_scores.get(name)
        if score is None:
            continue
        w = int(weights.get(name, 0))
        if w <= 0:
            continue
        weighted_sum += float(score) * w
        weight_sum += w
    if weight_sum == 0:
        return None
    return round((weighted_sum / weight_sum) * 10.0, 2)


def _compute_confidence(sub_scores: Mapping[str, int | None]) -> float:
    """Fraction of sub-scores populated, bounded in [0, 1] (R-208)."""
    populated = sum(1 for n in _SUB_SCORE_NAMES if sub_scores.get(n) is not None)
    return min(1.0, max(0.0, populated / len(_SUB_SCORE_NAMES)))


# ===========================================================================
# Phase 7 — CoStar-dependent sub-scores (S4, S5, S6, refined S8)
# ===========================================================================
# Per reviews/10_phase7_8_combined/01_risk_review.md §3.3-§3.6 (R-511..R-528).
# All four helpers are pure-function score mappings; the conn-bound
# orchestrators below call them after a single market_context fetch (R-518)
# and a separate sales_comps fetch for S8.

# R-514: market_context staleness flag threshold. program.md L743 mandates
# refresh every 30 days; a stale row still scores but emits a data_gap flag.
_MARKET_CONTEXT_STALENESS_DAYS: int = 30

# R-524, R-525: S8 sample-size minimum and lookback window.
_S8_MIN_LAND_COMPS: int = 3
_S8_LOOKBACK_MONTHS: int = 36

# R-527: GA assesses at 40% of FMV. When falling back to assessed_value_total
# as a basis proxy, inflate by 1/0.4 = 2.5 to compare apples-to-apples
# against sale comps. Phase 14+ multi-state expansion will need a state-keyed
# table.
_GA_ASSESSMENT_RATIO: float = 0.40
_GA_BASIS_INFLATION_FACTOR: float = 1.0 / _GA_ASSESSMENT_RATIO  # = 2.5


def _score_vacancy(vacancy_rate_pct: float | None) -> int | None:
    """S4 — submarket vacancy. program.md L189 cuts:

        10 = <3%; 8 = 3-5%; 6 = 5-7%; 3 = 7-10%; 0 = >10%.

    Boundaries are STRICT inequalities at the lower edge (R-515): exactly
    3.0% maps to 8, exactly 5.0% maps to 6, exactly 7.0% maps to 3,
    exactly 10.0% maps to 3 (NOT 0 — '>10%' is strict).
    """
    if vacancy_rate_pct is None:
        return None
    v = float(vacancy_rate_pct)
    if v < 3.0:
        return 10
    if v < 5.0:
        return 8
    if v < 7.0:
        return 6
    if v <= 10.0:
        return 3
    return 0


def _score_absorption(net_absorption_t12_sf: float | None) -> int | None:
    """S5 — submarket net absorption (T12). program.md L191 cuts:

        10 = strong positive (>2M SF); 7 = positive (500K-2M);
        4 = flat (±500K); 0 = negative.

    The "negative" band per program.md is '< -500K' (so the flat ±500K
    band covers -500K..+500K) — see risk review R-516.
    """
    if net_absorption_t12_sf is None:
        return None
    a = float(net_absorption_t12_sf)
    if a > 2_000_000:
        return 10
    if a >= 500_000:
        return 7
    if a >= -500_000:
        return 4
    return 0


def _score_pipeline(under_construction_sf: float | None) -> int | None:
    """S6 — competing pipeline (submarket-grain approximation).

    program.md L192 specifies a 5-mile radius; we approximate at submarket
    grain (R-519) and emit a data_gap flag in the orchestrator. Cuts:

        10 = no spec construction; 7 = <500K SF; 4 = 500K-1.5M; 0 = >1.5M.

    Null pipeline → 10 (R-521: absence of evidence in a curated CoStar
    export is treated as no competing supply on file).
    """
    if under_construction_sf is None:
        return 10
    p = float(under_construction_sf)
    if p <= 0:
        return 10
    if p < 500_000:
        return 7
    if p <= 1_500_000:
        return 4
    return 0


def _score_basis(
    parcel_basis_per_acre: float | None,
    submarket_median_per_acre: float | None,
) -> int | None:
    """S8 — refined land basis vs. submarket median. program.md L193 cuts:

        10 = below submarket median; 7 = at median;
        4 = 10-25% above; 0 = >25% above.

    Bands are explicit (R-528): basis < 0.95*median → 10;
    0.95*median <= basis <= 1.10*median → 7;
    1.10*median < basis <= 1.25*median → 4;
    basis > 1.25*median → 0.
    """
    if parcel_basis_per_acre is None or submarket_median_per_acre is None:
        return None
    if submarket_median_per_acre <= 0:
        return None
    ratio = float(parcel_basis_per_acre) / float(submarket_median_per_acre)
    if ratio < 0.95:
        return 10
    if ratio <= 1.10:
        return 7
    if ratio <= 1.25:
        return 4
    return 0


# ---------------------------------------------------------------------------
# Phase 13 — per-cycle prefetch cache (R-1310..R-1320)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _CycleCache:
    """Per-scoring-cycle batch lookups, prefetched ONCE before the parcel loop.

    Replaces 3 of the 5 per-parcel DB round-trips (market_context, land-median,
    actionability-block) with 3 set-based queries (R-1310). Threaded into
    ``score_parcel`` / its helpers as an optional ``cache`` kwarg; when None the
    helpers fall back to their per-parcel queries (R-1312), so every existing
    direct caller of ``score_parcel`` / the helpers is unaffected.

    Each value is the SAME row shape the corresponding single-key query returns,
    so the per-parcel decode logic is byte-identical whether the row came from a
    ``fetchone()`` or from these dicts (the bit-identical guarantee):
      - ``market_context``: submarket -> 7-col latest row tuple
        (vacancy, absorption, under_constr, proposed, rent, as_of, source).
      - ``land_median``: submarket -> (n, median) tuple.
      - ``actionability_block``: parcel_id -> open-block description (str).
        Absent key == no open block == None (R-1311).

    Staleness/validity (R-1313): scoring NEVER writes ``market_context`` or
    ``sales_comps`` (only ``run_ingestion_cycle`` does), and ``score_parcel``
    writes ONLY ``flag_type='data_gap'`` flags — never ``actionability_block``
    rows — so a cycle-start prefetch is safe against the cycle's own writes.
    """

    market_context: dict[str, tuple]
    land_median: dict[str, tuple]
    actionability_block: dict[str, str]


def _prefetch_cycle_cache(
    conn: Any,
    market: str,
    parcel_ids: Sequence[str],
) -> _CycleCache:
    """Build the per-cycle batch cache for ``parcel_ids`` (R-1317).

    Keyed off the EXACT ``parcel_ids`` list the scoring loop iterates so the
    cache never misses a scored parcel's submarket/block. Short-circuits the
    submarket-keyed queries when the cycle has no non-null submarkets (R-1315,
    R-1318). ``ANY(%s)`` parameters are passed as a single-element tuple
    wrapping the Python list, which psycopg3 adapts to a Postgres array
    (R-1316).
    """
    market_context: dict[str, tuple] = {}
    land_median: dict[str, tuple] = {}
    actionability_block: dict[str, str] = {}

    pid_list = list(parcel_ids)
    if not pid_list:
        # Degenerate empty cycle — issue no queries (R-1318).
        return _CycleCache(market_context, land_median, actionability_block)

    # Distinct non-null submarkets for this cycle's parcels (R-1317). Raw
    # strings — NO case/whitespace normalization, so the dict keys match the
    # DB's `=` semantics exactly (R-1315).
    with conn.cursor() as cur:
        cur.execute(_SQL_DISTINCT_SUBMARKETS_FOR_PARCELS, (pid_list,))
        submarkets = [r[0] for r in cur.fetchall() if r[0] is not None]

    if submarkets:
        with conn.cursor() as cur:
            cur.execute(_SQL_LATEST_MARKET_CONTEXT_BATCH, (submarkets,))
            for row in cur.fetchall():
                # row[0] is submarket_id; the remaining 7 columns are the SAME
                # tuple _SQL_LATEST_MARKET_CONTEXT returns.
                market_context[row[0]] = tuple(row[1:])
        with conn.cursor() as cur:
            cur.execute(_SQL_SUBMARKET_LAND_MEDIAN_BATCH, (submarkets,))
            for row in cur.fetchall():
                # row[0] is submarket_id; (n, median) matches the single-key
                # _SQL_SUBMARKET_LAND_MEDIAN row.
                land_median[row[0]] = (row[1], row[2])

    # Actionability blocks are parcel-keyed, so they always run for a non-empty
    # cycle regardless of submarket coverage (R-1313: safe — scoring never
    # writes actionability_block rows mid-cycle).
    with conn.cursor() as cur:
        cur.execute(_SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH, (pid_list,))
        for row in cur.fetchall():
            # row[0] parcel_id, row[1] description. Mirror the per-parcel
            # decode: a NULL description maps to None (skip — absent key reads
            # back as None via dict.get).
            if row[1] is not None:
                actionability_block[row[0]] = str(row[1])

    return _CycleCache(market_context, land_median, actionability_block)


# ---------------------------------------------------------------------------
# Phase 7 conn-bound score orchestrators
# ---------------------------------------------------------------------------
def _compute_market_context_scores(
    conn: Any,
    submarket: str | None,
    *,
    cache: "_CycleCache | None" = None,
) -> dict[str, Any]:
    """Fetch the latest market_context row and produce S4/S5/S6 + flags.

    Returns a dict with keys S4, S5, S6 (each int | None), plus
    `staleness_days` (int | None) and `provenance` (str | None) for the
    caller to thread into the data_gap flag emission.

    Phase 13 (R-1312): when ``cache`` is provided (per-cycle prefetch), the
    latest market_context row is read from ``cache.market_context`` instead of
    issuing a per-parcel query; the row tuple is the SAME shape the single-key
    ``_SQL_LATEST_MARKET_CONTEXT`` returns, so the score/flag/provenance logic
    below is byte-identical. When ``cache`` is None the original per-parcel
    query path runs verbatim. The ``if not submarket`` guard fires BEFORE any
    cache/dict lookup so NULL/empty submarkets hit the same empty branch as
    today (R-1315: no KeyError, no spurious match).
    """
    out: dict[str, Any] = {
        "S4": None,
        "S5": None,
        "S6": None,
        "staleness_days": None,
        "provenance": None,
        "as_of_date": None,
        "source": None,
    }
    if not submarket:
        return out
    if cache is not None:
        row = cache.market_context.get(submarket)
    else:
        with conn.cursor() as cur:
            cur.execute(_SQL_LATEST_MARKET_CONTEXT, (submarket,))
            row = cur.fetchone()
    if not row:
        return out
    vacancy, absorption, under_constr, _proposed, _rent, as_of, source = (
        row[0], row[1], row[2], row[3], row[4], row[5], row[6],
    )
    out["S4"] = _score_vacancy(vacancy)
    out["S5"] = _score_absorption(absorption)
    out["S6"] = _score_pipeline(under_constr)
    out["as_of_date"] = as_of
    out["source"] = source
    if as_of is not None:
        try:
            from datetime import date as _date  # local to avoid module-level churn
            today = _date.today()
            delta = (today - as_of).days if hasattr(as_of, "year") else None
            out["staleness_days"] = delta
        except Exception:
            out["staleness_days"] = None
    out["provenance"] = (
        f"market_context source={source} as_of={as_of} submarket={submarket}"
    )
    return out


def _compute_parcel_basis_per_acre(parcel: Mapping[str, Any]) -> tuple[float | None, str]:
    """Resolve the parcel's $/acre basis using the R-526 fallback ladder.

    Returns (basis, provenance). provenance is one of:
    - 'recent_sale' — last_sale_date within 24 months and last_sale_price > 0
    - 'assessed_inflated_ga' — assessed_value_total / acreage * 2.5 (state=GA)
    - 'assessed_raw' — assessed_value_total / acreage (non-GA)
    - 'unavailable' — no usable proxy
    """
    acreage = parcel.get("acreage")
    if not acreage or float(acreage) <= 0:
        return None, "unavailable"
    last_sale_price = parcel.get("last_sale_price")
    last_sale_date = parcel.get("last_sale_date")
    # Recent-sale branch.
    if last_sale_price and last_sale_date is not None:
        try:
            from datetime import date as _date
            today = _date.today()
            cutoff_days = 24 * 30  # ~24 months
            age = (today - last_sale_date).days if hasattr(last_sale_date, "year") else None
            if age is not None and 0 <= age <= cutoff_days and float(last_sale_price) > 0:
                return float(last_sale_price) / float(acreage), "recent_sale"
        except Exception:
            pass
    assessed = parcel.get("assessed_value_total")
    if assessed and float(assessed) > 0:
        per_acre = float(assessed) / float(acreage)
        state = (parcel.get("state") or "").upper()
        if state == "GA":
            return per_acre * _GA_BASIS_INFLATION_FACTOR, "assessed_inflated_ga"
        return per_acre, "assessed_raw"
    return None, "unavailable"


def _compute_s8(
    conn: Any,
    parcel: Mapping[str, Any],
    *,
    cache: "_CycleCache | None" = None,
) -> tuple[int | None, dict[str, Any]]:
    """S8 — refined land basis. Returns (score, provenance dict).

    Provenance dict keys: basis_per_acre, basis_provenance, median,
    median_n, n_below_min (bool — true when < _S8_MIN_LAND_COMPS comps).

    Phase 13 (R-1312, R-1319): the parcel ``basis`` stays PER-PARCEL (it is a
    function of parcel attributes, not the submarket). Only the submarket
    ``(n, median)`` aggregate is prefetched; when ``cache`` is provided it is
    read from ``cache.land_median`` as the SAME ``(n, median)`` row the
    single-key ``_SQL_SUBMARKET_LAND_MEDIAN`` returns, so the decode and the
    n_below_min/score logic are byte-identical. None ``cache`` runs the
    per-parcel query verbatim. The ``if submarket`` guard precedes any lookup
    (R-1315).
    """
    basis, basis_prov = _compute_parcel_basis_per_acre(parcel)
    submarket = parcel.get("submarket")
    median: float | None = None
    n: int = 0
    if submarket:
        if cache is not None:
            row = cache.land_median.get(submarket)
        else:
            with conn.cursor() as cur:
                cur.execute(_SQL_SUBMARKET_LAND_MEDIAN, (submarket,))
                row = cur.fetchone()
        if row:
            n = int(row[0]) if row[0] is not None else 0
            median = float(row[1]) if row[1] is not None else None
    n_below_min = n < _S8_MIN_LAND_COMPS
    if n_below_min or median is None or basis is None:
        return None, {
            "basis_per_acre": basis,
            "basis_provenance": basis_prov,
            "median": median,
            "median_n": n,
            "n_below_min": n_below_min,
        }
    score = _score_basis(basis, median)
    return score, {
        "basis_per_acre": basis,
        "basis_provenance": basis_prov,
        "median": median,
        "median_n": n,
        "n_below_min": False,
    }


# ===========================================================================
# Phase 8 — Strategy Fit Assessment Engine
# ===========================================================================
# Per reviews/10_phase7_8_combined/01_risk_review.md §3.8 (R-535..R-540).
# Five strategy-fit functions, each returning one of {STRONG, MODERATE,
# WEAK, N/A}. Multi-parcel assemblage is OUT OF SCOPE per R-540 (Phase 11+).
#
# All five functions consume sub-scores S1..S12 + the parcel acreage. They
# do NOT consume database state — they are pure functions of the scoring
# context. This makes them trivially unit-testable.

_STRATEGY_RATINGS: tuple[str, ...] = ("STRONG", "MODERATE", "WEAK", "N/A")
_STRATEGY_KEYS: tuple[str, ...] = (
    "bts", "spec", "land_bank", "ground_lease", "flip",
)

# R-535 / R-538: BTS minimum acreage from program.md "150K SF footprint at
# 40% coverage" → 150_000 / 0.40 = 375_000 SF land = 8.6 acres (43560 SF/ac).
_BTS_MIN_ACRES: float = 150_000.0 / 0.40 / 43_560.0  # ≈ 8.61


def _ge(score: int | None, threshold: int) -> bool:
    """True iff sub-score is non-null and >= threshold."""
    return score is not None and score >= threshold


def _lt(score: int | None, threshold: int) -> bool:
    """True iff sub-score is non-null and < threshold. Null returns False."""
    return score is not None and score < threshold


def _assess_strategy_bts(
    sub_scores: Mapping[str, int | None],
    acreage: float | None,
) -> str:
    """BTS Development fit. R-535. STRONG unreachable while S9 is the
    moderate stub (=5). MODERATE: S9>=4 AND S4>=6 AND S5>=7 AND
    acreage>=8.6. Otherwise WEAK. N/A: acreage<8.6."""
    if not acreage or float(acreage) < _BTS_MIN_ACRES:
        return "N/A"
    s4 = sub_scores.get("S4_submarket_vacancy")
    s5 = sub_scores.get("S5_submarket_absorption")
    s9 = sub_scores.get("S9_entitlement_complexity")
    # STRONG branch (program.md: by-right S9>=7 + utilities + tenant signal +
    # >=150K SF footprint). Tenant signal not wired; S9 stub=5 blocks. Kept
    # for forward compatibility when Phase 11+ raises S9.
    if _ge(s9, 7) and _ge(s4, 8) and _ge(s5, 8):
        return "STRONG"
    if _ge(s9, 4) and _ge(s4, 6) and _ge(s5, 7):
        return "MODERATE"
    return "WEAK"


def _assess_strategy_spec(
    sub_scores: Mapping[str, int | None],
    acreage: float | None,
) -> str:
    """Spec Development fit. R-536. STRONG unreachable in Phase 7+8 (S9
    stub=5). N/A when S4<3 (vacancy >= 7%, oversupplied)."""
    s4 = sub_scores.get("S4_submarket_vacancy")
    s5 = sub_scores.get("S5_submarket_absorption")
    s6 = sub_scores.get("S6_competing_pipeline")
    s9 = sub_scores.get("S9_entitlement_complexity")
    if s4 is not None and s4 < 3:
        return "N/A"
    if _ge(s4, 8) and _ge(s5, 7) and _ge(s6, 7) and _ge(s9, 7):
        return "STRONG"
    if _ge(s4, 6) and _ge(s5, 7) and _ge(s9, 4):
        return "MODERATE"
    return "WEAK"


def _assess_strategy_land_bank(
    sub_scores: Mapping[str, int | None],
    acreage: float | None,
) -> str:
    """Land Bank fit. R-537. Mapped to S8 (basis advantage)."""
    s8 = sub_scores.get("S8_land_basis")
    if s8 is None:
        return "N/A"
    if s8 == 10:
        return "STRONG"
    if s8 == 7:
        return "MODERATE"
    if s8 == 4:
        return "WEAK"
    # s8 == 0 — basis is materially above median, no land-bank thesis.
    return "N/A"


def _assess_strategy_ground_lease(
    sub_scores: Mapping[str, int | None],
    acreage: float | None,
) -> str:
    """Ground Lease fit. R-538. STRONG unreachable in Phase 7+8 (S9 stub
    blocks). N/A when location is too soft or scale is sub-institutional."""
    if not acreage or float(acreage) < _BTS_MIN_ACRES:
        return "N/A"
    s1 = sub_scores.get("S1_interstate_proximity")
    s4 = sub_scores.get("S4_submarket_vacancy")
    s9 = sub_scores.get("S9_entitlement_complexity")
    # S1 is null in Phase 7+8 (still pending Phase 11+ wiring); _lt(None,4)
    # is False, so the N/A guard only fires when S1 is populated AND weak.
    if _lt(s1, 4):
        return "N/A"
    if _ge(s1, 8) and _ge(s4, 8) and _ge(s9, 7):
        return "STRONG"
    if _ge(s1, 6) and _ge(s4, 6):
        return "MODERATE"
    return "WEAK"


def _assess_strategy_flip(
    sub_scores: Mapping[str, int | None],
    acreage: float | None,
) -> str:
    """Land Flip / Disposition fit. R-539. Mapped to S8 cross S4."""
    s4 = sub_scores.get("S4_submarket_vacancy")
    s8 = sub_scores.get("S8_land_basis")
    if s8 is None:
        return "N/A"
    if s8 == 10 and _ge(s4, 6):
        return "STRONG"
    if s8 == 10:
        return "MODERATE"
    if s8 == 7:
        return "MODERATE"
    if s8 == 4:
        return "WEAK"
    return "N/A"


def _compute_strategy_fit(
    sub_scores: Mapping[str, int | None],
    acreage: float | None,
) -> dict[str, str]:
    """Aggregate the five strategy assessments into a JSONB-shaped dict.

    Shape note for downstream callers: this dict is the persistent shape for
    the parcel_scores.strategy_fit JSONB column (all five keys, every
    rating). For results.tsv (program.md L128) the column is a
    comma-separated list of ONLY strategies rated STRONG or MODERATE — the
    eventual TSV writer must filter and flatten via something like
    ``",".join(k for k, v in fit.items() if v in ("STRONG", "MODERATE"))``,
    NOT serialise the dict directly.
    """
    return {
        "bts": _assess_strategy_bts(sub_scores, acreage),
        "spec": _assess_strategy_spec(sub_scores, acreage),
        "land_bank": _assess_strategy_land_bank(sub_scores, acreage),
        "ground_lease": _assess_strategy_ground_lease(sub_scores, acreage),
        "flip": _assess_strategy_flip(sub_scores, acreage),
    }


# R-542: priority order when multiple strategies tie at the same rating.
# BTS > spec > land_bank > flip > ground_lease — this matches the deal-flow
# urgency a development team would prioritise (a tenant-led BTS is more
# valuable than a passive ground lease at the same rating).
_PRIMARY_STRATEGY_PRIORITY: tuple[str, ...] = (
    "bts", "spec", "land_bank", "flip", "ground_lease",
)


def _select_primary_strategy(strategy_fit: Mapping[str, str]) -> str | None:
    """First STRONG (in priority order), else first MODERATE, else None."""
    for tier in ("STRONG", "MODERATE"):
        for key in _PRIMARY_STRATEGY_PRIORITY:
            if strategy_fit.get(key) == tier:
                return key
    return None


# ===========================================================================
# Phase 8 — Actionability screen (4 gates)
# ===========================================================================
# Per reviews/10_phase7_8_combined/01_risk_review.md §3.7 (R-529..R-534).
# Gate ordering is FIXED: control → entitlement → strategy → deal-killer.
# First failing gate wins (R-534). Path-to-control gate is informational
# (R-530) and always PASSes — owner identity goes into the snapshot, not
# the actionability decision.

_ACTIONABILITY_PASS = "PASS"
_ACTIONABILITY_FAIL_CONTROL = "FAIL:control"
_ACTIONABILITY_FAIL_ENTITLEMENT = "FAIL:entitlement"
_ACTIONABILITY_FAIL_STRATEGY = "FAIL:strategy"
_ACTIONABILITY_FAIL_DEAL_KILLER = "FAIL:deal_killer"
_ACTIONABILITY_PENDING = "PENDING"

_ACTIONABILITY_VALUES: frozenset[str] = frozenset({
    _ACTIONABILITY_PASS,
    _ACTIONABILITY_FAIL_CONTROL,
    _ACTIONABILITY_FAIL_ENTITLEMENT,
    _ACTIONABILITY_FAIL_STRATEGY,
    _ACTIONABILITY_FAIL_DEAL_KILLER,
    _ACTIONABILITY_PENDING,
})


def _gate_control() -> tuple[bool, str | None]:
    """Gate 1 — path to control. Always PASS (informational, R-530)."""
    return True, None


def _gate_entitlement(
    sub_scores: Mapping[str, int | None],
    flag_block: str | None,
) -> tuple[bool, str | None]:
    """Gate 2 — plausible entitlement (R-531). Default-PASS unless an
    open actionability_block flag mentions 'entitlement'."""
    if flag_block and "entitlement" in flag_block.lower():
        return False, f"actionability_block: {flag_block[:160]}"
    # No affirmative evidence of a block — program.md L86 says FAIL only
    # on affirmative evidence, so PASS here. Real entitlement signal lands
    # in Phase 11+.
    return True, None


def _gate_strategy(strategy_fit: Mapping[str, str]) -> tuple[bool, str | None]:
    """Gate 3 — viable strategy with next step (R-532). PASS iff at least
    one strategy is STRONG or MODERATE."""
    for key in _STRATEGY_KEYS:
        rating = strategy_fit.get(key)
        if rating in ("STRONG", "MODERATE"):
            return True, None
    return False, "no strategy rated STRONG or MODERATE"


def _gate_deal_killer(flag_block: str | None) -> tuple[bool, str | None]:
    """Gate 4 — no deal-killers (R-533). Default-PASS unless an open
    actionability_block flag mentions a non-entitlement blocker."""
    if flag_block and "entitlement" not in flag_block.lower():
        return False, f"actionability_block: {flag_block[:160]}"
    return True, None


def _fetch_actionability_block(
    conn: Any,
    parcel_id: str,
    *,
    cache: "_CycleCache | None" = None,
) -> str | None:
    """Return the description of an open actionability_block flag, if any.

    Phase 13 (R-1312, R-1320): the signature is unchanged for the existing
    positional callers — ``score_parcel`` and the public
    ``run_actionability_screen`` (L4094) both call
    ``_fetch_actionability_block(conn, parcel_id)`` and keep working. When
    ``cache`` is provided (per-cycle prefetch from ``run_scoring_cycle``) the
    open-block description is read from ``cache.actionability_block``: a parcel
    absent from the dict has no open block and returns None, exactly matching
    the per-parcel "no row" branch (R-1311). None ``cache`` runs the per-parcel
    query verbatim, so ``run_actionability_screen`` is untouched.
    """
    if cache is not None:
        return cache.actionability_block.get(parcel_id)
    with conn.cursor() as cur:
        cur.execute(_SQL_FLAGGED_ACTIONABILITY_BLOCK, (parcel_id,))
        row = cur.fetchone()
    if not row:
        return None
    return str(row[0]) if row[0] is not None else None


def _run_actionability_screen(
    sub_scores: Mapping[str, int | None],
    strategy_fit: Mapping[str, str],
    flag_block: str | None,
) -> tuple[str, dict[str, Any]]:
    """Apply the 4 gates in order; return (verdict, blockers_dict).

    First-failing-gate-wins (R-534): we short-circuit at the first FAIL
    so the verdict is single-valued and matches the program.md
    `actionability` enum (PASS / FAIL:control / FAIL:entitlement /
    FAIL:strategy / FAIL:deal_killer).
    """
    blockers: dict[str, Any] = {}

    ok, blocker = _gate_control()
    if not ok:
        blockers["control"] = blocker
        return _ACTIONABILITY_FAIL_CONTROL, blockers

    ok, blocker = _gate_entitlement(sub_scores, flag_block)
    if not ok:
        blockers["entitlement"] = blocker
        return _ACTIONABILITY_FAIL_ENTITLEMENT, blockers

    ok, blocker = _gate_strategy(strategy_fit)
    if not ok:
        blockers["strategy"] = blocker
        return _ACTIONABILITY_FAIL_STRATEGY, blockers

    ok, blocker = _gate_deal_killer(flag_block)
    if not ok:
        blockers["deal_killer"] = blocker
        return _ACTIONABILITY_FAIL_DEAL_KILLER, blockers

    return _ACTIONABILITY_PASS, blockers


# ---------------------------------------------------------------------------
# Per-parcel scoring orchestrator (R-201, R-204, R-211)
# ---------------------------------------------------------------------------
def _fetch_parcel_for_scoring(conn: Any, parcel_id: str) -> dict[str, Any] | None:
    """Fetch the parcel attributes Phase 7+8 scoring needs.

    Returns 9 fields (R-526, R-527, R-535..R-539, R-544): parcel_id,
    market, submarket, state, acreage, last_sale_date, last_sale_price,
    assessed_value_total, plus centroid lng/lat.
    """
    with conn.cursor() as cur:
        cur.execute(_SQL_FETCH_PARCEL, (parcel_id,))
        row = cur.fetchone()
    if not row:
        return None
    return {
        "parcel_id": row[0],
        "market": row[1],
        "submarket": row[2],
        "state": row[3],
        "acreage": row[4],
        "last_sale_date": row[5],
        "last_sale_price": row[6],
        "assessed_value_total": row[7],
        "centroid_lng": row[8],
        "centroid_lat": row[9],
    }


def score_parcel(
    parcel_id: str,
    *,
    conn: Any = None,
    cycle_id: str | None = None,
    params: Mapping[str, Any] | None = None,
    cache: "_CycleCache | None" = None,
    run_tag: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """Compute sub-scores S1..S12, strategy fit, and actionability for one
    parcel; persist all of it in a single transaction.

    Phase 7+8 wires the CoStar-dependent S4/S5/S6 + refined S8 on top of
    the Phase 5 S2/S9/S10 core, then runs the strategy fit engine and
    the four-gate actionability screen (R-501..R-545). The persisted
    parcel_scores row carries actionability, actionability_blockers,
    strategy_fit, and primary_strategy in addition to the Phase 5 fields.

    Phase 13 (R-1312): ``cache`` is an OPTIONAL per-cycle prefetch threaded in
    by ``run_scoring_cycle``. When provided, the market_context, land-median,
    and actionability-block lookups read from it instead of issuing per-parcel
    queries; the produced sub-scores, composite, confidence, actionability,
    strategy_fit, notes, and all DB rows are bit-identical to the cache=None
    path. When None (every existing caller, incl. the ad-hoc and direct-test
    paths) the original per-parcel queries run verbatim.

    prepare-mutation (2026-07-07): ``run_tag`` and ``experiment_id`` are
    persisted on every parcel_scores row so the metric can be scoped to the
    active run and so runner.py can purge a discarded experiment's rows.
    ``run_tag=None`` derives from the current git branch via
    :func:`prepare.current_run_tag`; ``experiment_id=None`` (ad-hoc /
    out-of-loop scoring) is stored as SQL NULL and is never purged.

    Returns: {parcel_id, status, composite_score, confidence_score,
    sub_scores, actionability, actionability_blockers, strategy_fit,
    primary_strategy}.
    """
    own_conn = False
    ctx = None
    if conn is None:
        # Production path opens its own connection. Bind ctx before __enter__
        # so that an exception during __enter__ doesn't leave the finally
        # block referring to an unbound name.
        own_conn = True
        ctx = prepare.get_connection()
        conn = ctx.__enter__()
    if params is None:
        prepare.verify_parameters_unchanged()
        params = prepare.get_parameters()
    if cycle_id is None:
        cycle_id = _make_scoring_cycle_id("adhoc")
    if run_tag is None:
        run_tag = prepare.current_run_tag()

    weights = params["scoring_weights"]

    try:
        parcel = _fetch_parcel_for_scoring(conn, parcel_id)
        if parcel is None:
            return {
                "parcel_id": parcel_id,
                "status": "missing",
                "composite_score": None,
                "confidence_score": None,
                "sub_scores": {},
                "actionability": None,
                "actionability_blockers": {},
                "strategy_fit": {},
                "primary_strategy": None,
            }

        sub_scores: dict[str, int | None] = {n: None for n in _SUB_SCORE_NAMES}
        sub_scores["S2_parcel_geometry"] = _compute_s2(conn, parcel_id)
        sub_scores["S9_entitlement_complexity"] = _compute_s9()
        sub_scores["S10_incentives"] = _compute_s10(
            parcel.get("centroid_lng"), parcel.get("centroid_lat"),
        )

        # R-518: single market_context fetch, three sub-scores derived.
        # R-1310: served from the per-cycle prefetch when cache is present.
        mc = _compute_market_context_scores(
            conn, parcel.get("submarket"), cache=cache,
        )
        sub_scores["S4_submarket_vacancy"] = mc["S4"]
        sub_scores["S5_submarket_absorption"] = mc["S5"]
        sub_scores["S6_competing_pipeline"] = mc["S6"]

        # R-523..R-528: refined S8 from sales_comps + parcel basis proxy.
        s8_score, s8_prov = _compute_s8(conn, parcel, cache=cache)
        sub_scores["S8_land_basis"] = s8_score

        composite = _compute_composite(sub_scores, weights)
        confidence = _compute_confidence(sub_scores)

        # R-529: strategy fit before actionability — gate 3 consumes it.
        strategy_fit = _compute_strategy_fit(sub_scores, parcel.get("acreage"))
        primary_strategy = _select_primary_strategy(strategy_fit)

        # R-533: synthetic deal-killer evidence from flagged_items.
        # R-1310: served from the per-cycle prefetch when cache is present.
        flag_block = _fetch_actionability_block(conn, parcel_id, cache=cache)
        actionability, blockers = _run_actionability_screen(
            sub_scores, strategy_fit, flag_block,
        )

        notes = (
            f"phase78: composite={composite} actionability={actionability} "
            f"primary_strategy={primary_strategy} "
            f"S2={sub_scores['S2_parcel_geometry']} "
            f"S4={sub_scores['S4_submarket_vacancy']} "
            f"S5={sub_scores['S5_submarket_absorption']} "
            f"S6={sub_scores['S6_competing_pipeline']} "
            f"S8={sub_scores['S8_land_basis']} "
            f"S9={sub_scores['S9_entitlement_complexity']} "
            f"S10={sub_scores['S10_incentives']}"
        )[:480]

        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        _SQL_INSERT_PARCEL_SCORE,
                        (
                            parcel_id,
                            composite,
                            confidence,
                            actionability,
                            json.dumps(blockers),
                            json.dumps(sub_scores),
                            json.dumps(strategy_fit),
                            primary_strategy,
                            notes,
                            run_tag,
                            experiment_id,
                        ),
                    )
                with conn.cursor() as cur:
                    cur.execute(
                        _SQL_INSERT_RESEARCH_LOG_SCORING,
                        (
                            cycle_id, "scoring", parcel.get("market"), parcel_id,
                            composite, actionability,
                            json.dumps(strategy_fit), notes,
                        ),
                    )
                # One data_gap flag per null sub-score (R-220).
                for name in _SUB_SCORE_NAMES:
                    if sub_scores.get(name) is not None:
                        continue
                    pretty, source = _SUB_SCORE_PROVENANCE[name]
                    _flag(
                        conn, cycle_id, parcel_id, parcel.get("market") or "",
                        "data_gap",
                        f"{name} ({pretty}) unjoined: pending later-phase data wiring ({source})",
                        f"wire {source} for parcel_id={parcel_id}",
                    )
                # R-514: market_context staleness flag (informational only).
                staleness = mc.get("staleness_days")
                if staleness is not None and staleness > _MARKET_CONTEXT_STALENESS_DAYS:
                    _flag(
                        conn, cycle_id, parcel_id, parcel.get("market") or "",
                        "data_gap",
                        f"market_context stale by {staleness}d ({mc.get('provenance')})",
                        "trigger CoStar submarket_stats refresh",
                    )
                # R-519: S6 submarket-grain approximation flag.
                if sub_scores["S6_competing_pipeline"] is not None:
                    _flag(
                        conn, cycle_id, parcel_id, parcel.get("market") or "",
                        "data_gap",
                        "S6 approximated at submarket grain; program.md spec is 5-mi radius",
                        "Phase 11+: implement radius-search facility for S6",
                    )
                # R-524: S8 sample-size shortfall flag. Only meaningful when the
                # parcel actually has a submarket — otherwise S8's null is
                # already covered by the per-subscore data_gap flag above.
                if s8_prov.get("n_below_min") and parcel.get("submarket"):
                    _flag(
                        conn, cycle_id, parcel_id, parcel.get("market") or "",
                        "data_gap",
                        (
                            f"S8 land-basis median has only {s8_prov.get('median_n')} "
                            f"comps in submarket={parcel.get('submarket')} "
                            f"(< {_S8_MIN_LAND_COMPS} required)"
                        ),
                        "wait for additional CoStar land sales comps in submarket",
                    )
        except Exception:
            log.exception("scoring transaction failed for parcel %s", parcel_id)
            try:
                conn.rollback()
            except Exception:
                pass
            return {
                "parcel_id": parcel_id,
                "status": "error",
                "composite_score": None,
                "confidence_score": None,
                "sub_scores": sub_scores,
                "actionability": None,
                "actionability_blockers": {},
                "strategy_fit": {},
                "primary_strategy": None,
            }

        return {
            "parcel_id": parcel_id,
            "status": "scored",
            "composite_score": composite,
            "confidence_score": confidence,
            "sub_scores": sub_scores,
            "actionability": actionability,
            "actionability_blockers": blockers,
            "strategy_fit": strategy_fit,
            "primary_strategy": primary_strategy,
        }
    finally:
        if own_conn and ctx is not None:
            try:
                ctx.__exit__(None, None, None)
            except Exception:
                log.exception("scoring connection close failed")


# ---------------------------------------------------------------------------
# Scoring cycle driver (R-213)
# ---------------------------------------------------------------------------
def run_scoring_cycle(
    market: str,
    *,
    run_tag: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """Score every parcel needing a score for this run in the given market.

    Phase 7+8 (R-507, R-510): the selection SQL returns parcels with no
    parcel_scores rows AND parcels whose latest row has
    actionability='PENDING' (the Phase 5 default). Each scoring run
    APPENDS a new parcel_scores row — never UPDATEs in place — so
    prepare.calculate_actionable_pipeline_count's latest-row selector
    sees the freshest verdict.

    prepare-mutation (2026-07-07): inside a run (``run_tag`` resolved from
    the argument or the current git branch) the selection and the metric
    are scoped to THIS RUN's rows — parcels scored in prior runs are
    re-scored once for the new run. ``experiment_id`` (set by
    runner.evaluate) is stamped on every row so a discarded experiment's
    rows can be purged.
    """
    if market not in _MARKET_TO_COUNTIES:
        raise NotImplementedError(
            f"market={market!r} not configured for Phase 5; only 'atlanta' is supported"
        )

    prepare.verify_parameters_unchanged()
    params = prepare.get_parameters()
    cycle_id = _make_scoring_cycle_id(market)
    if run_tag is None:
        run_tag = prepare.current_run_tag()

    summary: dict[str, Any] = {
        "cycle_id": cycle_id,
        "market": market,
        "aborted": False,
        "abort_reason": None,
        "counts": {"scored": 0, "missing": 0, "error": 0},
        "parcels": [],
    }

    with prepare.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_COUNT_LOG_FOR_SCORING_CYCLE, (cycle_id,))
            row = cur.fetchone()
        if row and int(row[0]) > 0:
            summary["aborted"] = True
            summary["abort_reason"] = "cycle_id_collision"
            return summary

        with conn.cursor() as cur:
            if run_tag is None:
                cur.execute(_SQL_LIST_PARCELS_FOR_SCORING, (market,))
            else:
                cur.execute(
                    _SQL_LIST_PARCELS_FOR_SCORING_RUN_SCOPED,
                    (market, run_tag, run_tag),
                )
            parcel_ids = [r[0] for r in cur.fetchall()]

        # Phase 13 (R-1310, R-1317): prefetch the per-cycle market_context,
        # land-median, and actionability-block lookups ONCE — replacing 3 of the
        # 5 per-parcel DB round-trips with set-based queries. Keyed off the
        # SAME parcel_ids the loop iterates, run AFTER the collision guard and
        # BEFORE the loop. Short-circuits to empty dicts for a 0-parcel cycle
        # (R-1318). score_parcel(cache=...) produces bit-identical rows to the
        # cache=None path (the bit-identical guarantee, proven by the
        # cache-vs-no-cache equivalence tests).
        cache = _prefetch_cycle_cache(conn, market, parcel_ids)

        for pid in parcel_ids:
            result = score_parcel(
                pid, conn=conn, cycle_id=cycle_id, params=params, cache=cache,
                run_tag=run_tag, experiment_id=experiment_id,
            )
            status = result.get("status", "error")
            summary["counts"][status] = summary["counts"].get(status, 0) + 1
            summary["parcels"].append(result)

        # exp: see run_discovery_cycle for the full rationale. Commit the
        # outer transaction explicitly so SAVEPOINTs from score_parcel
        # actually persist when the connection closes.
        conn.commit()

    return summary


# ---------------------------------------------------------------------------
# Phase 8: actionability and strategy fit — public wrappers around the
# orchestration helpers above. These thin wrappers exist so the rest of
# the agent (snapshot generator in Phase 9, the experiment loop in
# Phase 10) can call a stable public API without poking at the private
# helpers. They are NOT how score_parcel reaches them — score_parcel
# inlines the helpers because it already holds the conn + sub_scores +
# parcel context inside its transaction.
# ---------------------------------------------------------------------------
def run_actionability_screen(
    parcel_id: str,
    *,
    conn: Any = None,
    sub_scores: Mapping[str, int | None] | None = None,
    strategy_fit: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Apply the four-gate actionability screen for a parcel.

    Pure-function path: callers pass sub_scores + strategy_fit and we
    skip the database hop entirely (used by tests). Database path:
    callers pass conn and we look up the open actionability_block flag
    for the parcel; sub_scores and strategy_fit must still be provided
    by the caller (typically from a freshly computed score_parcel run).
    """
    if sub_scores is None or strategy_fit is None:
        raise ValueError(
            "run_actionability_screen requires sub_scores and strategy_fit; "
            "call score_parcel for the full database-backed pipeline."
        )
    flag_block: str | None = None
    if conn is not None:
        flag_block = _fetch_actionability_block(conn, parcel_id)
    actionability, blockers = _run_actionability_screen(
        sub_scores, strategy_fit, flag_block,
    )
    return {
        "parcel_id": parcel_id,
        "actionability": actionability,
        "actionability_blockers": blockers,
    }


def assess_strategy_fit(
    parcel_id: str,
    *,
    sub_scores: Mapping[str, int | None] | None = None,
    acreage: float | None = None,
) -> dict[str, Any]:
    """Tag a parcel with strategy fit ratings + a primary strategy."""
    if sub_scores is None:
        raise ValueError(
            "assess_strategy_fit requires sub_scores; call score_parcel "
            "for the full database-backed pipeline."
        )
    fit = _compute_strategy_fit(sub_scores, acreage)
    return {
        "parcel_id": parcel_id,
        "strategy_fit": fit,
        "primary_strategy": _select_primary_strategy(fit),
    }
