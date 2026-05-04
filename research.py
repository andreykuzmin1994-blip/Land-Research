"""research.py — The autonomous agent's sandbox.

============================================================================
THE ONLY FILE THE AGENT EDITS DURING A RUN
============================================================================
Per AUTORESEARCH_MECHANICS.md (Five-File Contract, File 4): this is the only
Python file the agent edits while the experiment loop is active. The agent:

    - reads parameters via :func:`prepare.get_parameters`,
    - never re-parses ``parameters.json`` directly,
    - never re-defines symbols imported from :mod:`prepare`,
    - never modifies :mod:`prepare` or any spec ``.md`` file.

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

import csv
import json
import logging
import math
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
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

log = logging.getLogger("research")

# ---------------------------------------------------------------------------
# Repo paths (R-40 — paths constructed for the raw-response cache must
# resolve under repo_root/sources/. Defense-in-depth.)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent
_SOURCES_DIR = _REPO_ROOT / "sources"

# Phase 6 (R-303, R-304, R-332): root of the CoStar ingestion staging tree.
# The directory is gitignored (.gitignore line 53). The agent creates
# subdirectories on demand. Tests monkey-patch this path inside a tempdir
# (R-329) so the offline suite never touches the real repo path.
_COSTAR_BASE_DIR = _REPO_ROOT / "costar_exports"

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

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        timeout: float = _DISCOVERY_HTTP_TIMEOUT_S,
    ) -> dict[str, Any]:
        """GET with rate limit + JSON parse. Raises on HTTP error."""
        host = urlparse(url).hostname or ""
        self._spacing_sleep(host)
        resp = self._session.get(url, params=dict(params or {}), timeout=timeout)
        resp.raise_for_status()
        return resp.json()

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

_SQL_INSERT_FLAG = (
    "INSERT INTO flagged_items "
    "(flag_type, parcel_id, market, description, suggested_resolution) "
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
    "sub_scores, strategy_fit, primary_strategy, notes"
    ") VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s) "
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
_SQL_S2_GEOMETRY = (
    "WITH g AS ("
    "  SELECT geometry AS geom, ST_Envelope(geometry) AS bbox "
    "  FROM parcels WHERE parcel_id = %s"
    ") "
    "SELECT "
    "  ST_Area(geom::geography) AS area_m2, "
    "  ST_Area(bbox::geography) AS bbox_area_m2, "
    "  GREATEST(ST_XMax(bbox)-ST_XMin(bbox), ST_YMax(bbox)-ST_YMin(bbox)) "
    "  / NULLIF(LEAST(ST_XMax(bbox)-ST_XMin(bbox), "
    "                 ST_YMax(bbox)-ST_YMin(bbox)), 0) AS aspect_ratio "
    "FROM g WHERE geom IS NOT NULL"
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
    "    ORDER BY ps.scored_at DESC LIMIT 1"
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

# Phase 7 (R-511..R-515, R-516..R-518, R-519..R-522): latest market_context
# row per submarket, with CoStar preferred when sources tie on as_of_date.
# Returns vacancy/absorption/under_construction/proposed/asking_rent and the
# as_of_date so the caller can apply the staleness flag (R-514).
_SQL_LATEST_MARKET_CONTEXT = (
    "SELECT vacancy_rate_pct, net_absorption_t12_sf, "
    "under_construction_sf, proposed_sf, asking_rent_nnn_psf, "
    "as_of_date, source "
    "FROM market_context "
    "WHERE submarket_id = %s "
    "ORDER BY (CASE WHEN source = 'costar' THEN 0 ELSE 1 END), "
    "         as_of_date DESC "
    "LIMIT 1"
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

# Phase 6 — CoStar ingestion (R-321, R-326). All statements use %s
# placeholders and live as module-level constants for the AST scanner.
_SQL_UPSERT_MARKETS_REF = (
    "INSERT INTO markets (market_id, market_name, notes) "
    "VALUES (%s, %s, %s) "
    "ON CONFLICT (market_id) DO NOTHING"
)

_SQL_UPSERT_SUBMARKETS_REF = (
    "INSERT INTO submarkets (submarket_id, market_id, submarket_name) "
    "VALUES (%s, %s, %s) "
    "ON CONFLICT (submarket_id) DO NOTHING "
    "RETURNING submarket_name"
)

_SQL_FETCH_SUBMARKET_NAME = (
    "SELECT submarket_name FROM submarkets WHERE submarket_id = %s"
)

# R-302 / R-324: idempotent re-ingest is DELETE-then-INSERT inside one
# transaction, keyed on (submarket_id, as_of_date, source).
_SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST = (
    "DELETE FROM market_context "
    "WHERE source = %s AND submarket_id = %s AND as_of_date = %s"
)

_SQL_INSERT_MARKET_CONTEXT = (
    "INSERT INTO market_context ("
    "submarket_id, as_of_date, vacancy_rate_pct, availability_rate_pct, "
    "net_absorption_t12_sf, under_construction_sf, proposed_sf, "
    "asking_rent_nnn_psf, source"
    ") VALUES ("
    "%s, %s, %s, %s, %s, %s, %s, %s, %s"
    ")"
)

_SQL_INSERT_RESEARCH_LOG_INGESTION = (
    "INSERT INTO research_log "
    "(cycle_id, action_type, market, parcel_id, notes) "
    "VALUES (%s, %s, %s, %s, %s)"
)

_SQL_COUNT_LOG_FOR_INGESTION_CYCLE = (
    "SELECT COUNT(*) FROM research_log "
    "WHERE cycle_id = %s AND action_type = 'ingestion'"
)

# Phase 6.1 — comps and listings idempotent re-ingest (R-402, R-422).
# `sales_comps` shares one table for land + building rows; the DELETE
# clauses include `comp_type = %s` so a building re-ingest doesn't blow
# away land rows for the same (submarket, address, sale_date) tuple.
_SQL_DELETE_LAND_SALES_FOR_REINGEST = (
    "DELETE FROM sales_comps "
    "WHERE comp_type = 'land' "
    "AND submarket_id = %s AND address = %s AND sale_date = %s"
)

_SQL_INSERT_LAND_SALES = (
    "INSERT INTO sales_comps ("
    "address, parcel_id, county, submarket_id, comp_type, acres, "
    "sale_date, sale_price, price_per_acre, cap_rate, "
    "buyer_name, seller_name, zoning, raw"
    ") VALUES ("
    "%s, %s, %s, %s, 'land', %s, "
    "%s, %s, %s, %s, "
    "%s, %s, %s, %s::jsonb"
    ")"
)

_SQL_DELETE_BUILDING_SALES_FOR_REINGEST = (
    "DELETE FROM sales_comps "
    "WHERE comp_type = 'building' "
    "AND submarket_id = %s AND address = %s AND sale_date = %s"
)

_SQL_INSERT_BUILDING_SALES = (
    "INSERT INTO sales_comps ("
    "address, submarket_id, comp_type, building_sf, "
    "sale_date, sale_price, price_psf, cap_rate, "
    "buyer_name, seller_name, raw"
    ") VALUES ("
    "%s, %s, 'building', %s, "
    "%s, %s, %s, %s, "
    "%s, %s, %s::jsonb"
    ")"
)

_SQL_DELETE_LEASING_COMPS_FOR_REINGEST = (
    "DELETE FROM leasing_comps "
    "WHERE submarket_id = %s AND address = %s "
    "AND tenant_name = %s AND lease_start_date = %s"
)

_SQL_INSERT_LEASING_COMP = (
    "INSERT INTO leasing_comps ("
    "address, submarket_id, tenant_name, tenant_industry, naics_code, "
    "lease_start_date, lease_term_months, building_sf_leased, "
    "starting_rent_psf_nnn, rent_escalation_pct, lease_type, raw"
    ") VALUES ("
    "%s, %s, %s, %s, %s, "
    "%s, %s, %s, "
    "%s, %s, %s, %s::jsonb"
    ")"
)

# Snapshot semantics for land_listings (R-426): re-ingest of the same
# weekly snapshot replaces ALL rows with that snapshot_date.
_SQL_DELETE_LAND_LISTINGS_FOR_REINGEST = (
    "DELETE FROM land_listings "
    "WHERE snapshot_date = %s AND address = %s"
)

_SQL_INSERT_LAND_LISTING = (
    "INSERT INTO land_listings ("
    "address, parcel_id, county, submarket_id, acres, zoning, "
    "asking_price, asking_price_per_acre, listing_date, days_on_market, "
    "listing_broker, listing_broker_firm, utilities_status, "
    "entitlement_status, raw, snapshot_date, is_active"
    ") VALUES ("
    "%s, %s, %s, %s, %s, %s, "
    "%s, %s, %s, %s, "
    "%s, %s, %s, "
    "%s, %s::jsonb, %s, TRUE"
    ")"
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


def _flag(
    conn: Any,
    cycle_id: str,
    parcel_id: str | None,
    market: str,
    flag_type: str,
    description: str,
    suggested_resolution: str,
) -> None:
    """Insert one flagged_items row. Embeds cycle_id into description (R-38).

    ``parcel_id`` is ``None`` for cycle-level flag rows that aren't tied to a
    specific parcel (e.g. ``harness=degraded`` or partial-corridor abort).
    psycopg sends Python ``None`` as SQL NULL; using a sentinel string like
    ``"(none)"`` would break future joins against the parcels table.
    """
    cycle_prefixed = f"cycle={cycle_id}; {description}"
    with conn.cursor() as cur:
        cur.execute(
            _SQL_INSERT_FLAG,
            (flag_type, parcel_id, market, cycle_prefixed, suggested_resolution),
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
                raise
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
# Phase 7 conn-bound score orchestrators
# ---------------------------------------------------------------------------
def _compute_market_context_scores(
    conn: Any,
    submarket: str | None,
) -> dict[str, Any]:
    """Fetch the latest market_context row and produce S4/S5/S6 + flags.

    Returns a dict with keys S4, S5, S6 (each int | None), plus
    `staleness_days` (int | None) and `provenance` (str | None) for the
    caller to thread into the data_gap flag emission.
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
) -> tuple[int | None, dict[str, Any]]:
    """S8 — refined land basis. Returns (score, provenance dict).

    Provenance dict keys: basis_per_acre, basis_provenance, median,
    median_n, n_below_min (bool — true when < _S8_MIN_LAND_COMPS comps).
    """
    basis, basis_prov = _compute_parcel_basis_per_acre(parcel)
    submarket = parcel.get("submarket")
    median: float | None = None
    n: int = 0
    if submarket:
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


def _fetch_actionability_block(conn: Any, parcel_id: str) -> str | None:
    """Return the description of an open actionability_block flag, if any."""
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
) -> dict[str, Any]:
    """Compute sub-scores S1..S12, strategy fit, and actionability for one
    parcel; persist all of it in a single transaction.

    Phase 7+8 wires the CoStar-dependent S4/S5/S6 + refined S8 on top of
    the Phase 5 S2/S9/S10 core, then runs the strategy fit engine and
    the four-gate actionability screen (R-501..R-545). The persisted
    parcel_scores row carries actionability, actionability_blockers,
    strategy_fit, and primary_strategy in addition to the Phase 5 fields.

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
        mc = _compute_market_context_scores(conn, parcel.get("submarket"))
        sub_scores["S4_submarket_vacancy"] = mc["S4"]
        sub_scores["S5_submarket_absorption"] = mc["S5"]
        sub_scores["S6_competing_pipeline"] = mc["S6"]

        # R-523..R-528: refined S8 from sales_comps + parcel basis proxy.
        s8_score, s8_prov = _compute_s8(conn, parcel)
        sub_scores["S8_land_basis"] = s8_score

        composite = _compute_composite(sub_scores, weights)
        confidence = _compute_confidence(sub_scores)

        # R-529: strategy fit before actionability — gate 3 consumes it.
        strategy_fit = _compute_strategy_fit(sub_scores, parcel.get("acreage"))
        primary_strategy = _select_primary_strategy(strategy_fit)

        # R-533: synthetic deal-killer evidence from flagged_items.
        flag_block = _fetch_actionability_block(conn, parcel_id)
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
def run_scoring_cycle(market: str) -> dict[str, Any]:
    """Score every unscored OR PENDING-latest-row parcel in the given market.

    Phase 7+8 (R-507, R-510): _SQL_LIST_PARCELS_FOR_SCORING returns
    parcels with no parcel_scores rows AND parcels whose latest row has
    actionability='PENDING' (the Phase 5 default). Each scoring run
    APPENDS a new parcel_scores row — never UPDATEs in place — so
    prepare.calculate_actionable_pipeline_count's MAX(scored_at) selector
    sees the freshest verdict.
    """
    if market not in _MARKET_TO_COUNTIES:
        raise NotImplementedError(
            f"market={market!r} not configured for Phase 5; only 'atlanta' is supported"
        )

    prepare.verify_parameters_unchanged()
    params = prepare.get_parameters()
    cycle_id = _make_scoring_cycle_id(market)

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
            cur.execute(_SQL_LIST_PARCELS_FOR_SCORING, (market,))
            parcel_ids = [r[0] for r in cur.fetchall()]

        for pid in parcel_ids:
            result = score_parcel(pid, conn=conn, cycle_id=cycle_id, params=params)
            status = result.get("status", "error")
            summary["counts"][status] = summary["counts"].get(status, 0) + 1
            summary["parcels"].append(result)

    return summary


# ===========================================================================
# Phase 6 — CoStar ingestion (Option A: framework + submarket_stats only)
# ===========================================================================
# See reviews/08_phase6_costar_ingestion/01_risk_review.md for the full risk
# model (R-301 .. R-335). Option A wires submarket_stats end-to-end and
# leaves the other four recurring export types as registered no-ops so
# Phase 6.1+ adds them by replacing the placeholder with a real loader.

_COSTAR_SOURCE = "costar"

# R-401 / R-404 / R-405: county→market lookup. Atlanta is the only Phase
# 6.1 target market; Phase 11+ multi-market expansion adds more counties
# here. Loaders for the comps export types that have a `county` column
# (land_sales_comps, land_listings) use this lookup; loaders without a
# `county` column (building_sales_comps, leasing_comps) fall back to
# _DEFAULT_INGESTION_MARKET.
_COUNTY_TO_MARKET: dict[str, str] = {
    "fulton": "Atlanta",
    "dekalb": "Atlanta",
    "cobb": "Atlanta",
    "gwinnett": "Atlanta",
    "clayton": "Atlanta",
    "henry": "Atlanta",
    "spalding": "Atlanta",
    "fayette": "Atlanta",
}
_DEFAULT_INGESTION_MARKET = "Atlanta"

# R-302 / R-307: filename pattern for the weekly submarket_stats export per
# COSTAR_INGESTION_CONTRACT.md §Export 1.
_SUBMARKET_STATS_FILENAME_RE = re.compile(
    r"^submarket_stats_(\d{8})\.csv$", re.IGNORECASE,
)

# Phase 6.1: filename patterns for the four other recurring CoStar export
# types. Monthly exports use YYYYMM, weekly exports use YYYYMMDD.
_LAND_SALES_COMPS_FILENAME_RE = re.compile(
    r"^land_sales_comps_(\d{6})\.csv$", re.IGNORECASE,
)
_BUILDING_SALES_COMPS_FILENAME_RE = re.compile(
    r"^building_sales_comps_(\d{6})\.csv$", re.IGNORECASE,
)
_LEASING_COMPS_FILENAME_RE = re.compile(
    r"^leasing_comps_(\d{6})\.csv$", re.IGNORECASE,
)
_LAND_LISTINGS_FILENAME_RE = re.compile(
    r"^land_listings_(\d{8})\.csv$", re.IGNORECASE,
)

# Required columns per COSTAR_INGESTION_CONTRACT.md §Export 2..5.
_LAND_SALES_COMPS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "address", "parcel_id", "county", "submarket", "acres",
    "sale_date", "sale_price", "price_per_acre",
    "buyer_name", "seller_name", "zoning", "intended_use", "cap_rate",
)
_BUILDING_SALES_COMPS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "address", "submarket", "building_sf", "year_built", "clear_height_ft",
    "sale_date", "sale_price", "price_psf",
    "cap_rate", "noi_at_sale",
    "buyer_name", "seller_name",
    "tenant_at_sale", "lease_term_remaining_years",
)
_LEASING_COMPS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "address", "submarket",
    "tenant_name", "tenant_industry",
    "lease_start_date", "lease_term_months",
    "building_sf_leased",
    "starting_rent_psf_nnn", "rent_escalation_pct",
    "lease_type",
)
_LAND_LISTINGS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "address", "parcel_id", "county", "submarket",
    "acres", "zoning", "topography_notes",
    "asking_price", "asking_price_per_acre",
    "listing_date", "days_on_market",
    "listing_broker", "listing_broker_firm",
    "utilities_status", "entitlement_status",
)

# Required column set per COSTAR_INGESTION_CONTRACT.md §Export 1. Order is
# documentary; presence is what's checked. Headers are case-insensitive in
# the validator (CoStar exports occasionally capitalise differently).
_SUBMARKET_STATS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "submarket_name",
    "market",
    "total_inventory_sf",
    "vacancy_rate_pct",
    "availability_rate_pct",
    "net_absorption_t12_sf",
    "under_construction_sf",
    "proposed_sf",
    "asking_rent_nnn_psf",
    "report_date",
)

_INGESTION_CYCLE_ID_RE = re.compile(
    r"^ingest-\d{8}T\d{6}Z-[0-9a-f]{4}$"
)


def _make_ingestion_cycle_id() -> str:
    """Generate a unique sortable ingestion cycle id (R-321)."""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(2)
    return f"ingest-{now}-{suffix}"


# R-301 / R-315: deterministic slug for market_id and submarket_id
# derivation. Lowercase, non-alphanumeric runs collapsed to `_`, edges
# stripped, length-bounded.
_SLUG_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    """Return a stable lowercase slug for an id component (R-301)."""
    if not isinstance(value, str):
        raise ValueError(f"_slugify expected str, got {type(value).__name__}")
    lowered = value.strip().lower()
    if not lowered:
        raise ValueError("_slugify cannot derive an id from an empty/whitespace string")
    slug = _SLUG_NONWORD_RE.sub("_", lowered).strip("_")
    if not slug:
        raise ValueError(f"_slugify produced empty slug from {value!r}")
    return slug[:60]


def _resolve_market_from_county(
    county: str | None, default_market: str = _DEFAULT_INGESTION_MARKET,
) -> tuple[str, bool]:
    """Look up market for a county. Returns (market, used_default) (R-401, R-404)."""
    if not county or not isinstance(county, str):
        return default_market, True
    key = county.strip().lower()
    if not key:
        return default_market, True
    if key in _COUNTY_TO_MARKET:
        return _COUNTY_TO_MARKET[key], False
    return default_market, True


def _resolve_costar_subdir(subdir: str) -> Path:
    """Resolve costar_exports/{subdir} and reject directory traversal (R-303, R-305)."""
    if not isinstance(subdir, str) or not subdir or subdir.startswith((".", "/")):
        raise ValueError(f"invalid CoStar subdir: {subdir!r}")
    base = _COSTAR_BASE_DIR.resolve(strict=False)
    candidate = (base / subdir).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"refused CoStar subdir outside {base}: {subdir!r}"
        ) from exc
    return candidate


def _scan_export_dir(
    subdir: str,
    pattern: re.Pattern[str] = _SUBMARKET_STATS_FILENAME_RE,
) -> list[tuple[Path, str]]:
    """List intake CSVs under costar_exports/{subdir}.

    Returns ``[(path, parsed_date_str), ...]`` sorted ascending by parsed
    date. Files in ``ARCHIVED/`` and ``FAILED/`` are skipped (R-305).
    Hidden files and non-matching files are silently skipped (R-310, R-311).
    """
    target = _resolve_costar_subdir(subdir)
    if not target.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    base = _COSTAR_BASE_DIR.resolve(strict=False)
    for entry in target.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if name.startswith("."):
            continue
        try:
            entry_resolved = entry.resolve(strict=False)
            entry_resolved.relative_to(base)
        except ValueError:
            log.warning("ingestion: skipping %s (outside base dir)", entry)
            continue
        match = pattern.match(name)
        if not match:
            continue
        out.append((entry, match.group(1)))
    out.sort(key=lambda t: t[1])
    return out


def _archive_destination(source: Path, kind: str) -> Path:
    """Compute a unique destination path under ARCHIVED/ or FAILED/ (R-313)."""
    if kind not in {"ARCHIVED", "FAILED"}:
        raise ValueError(f"unknown archive kind: {kind!r}")
    base = _COSTAR_BASE_DIR.resolve(strict=False)
    subdir = source.parent.name
    dest_dir = (base / kind / subdir).resolve(strict=False)
    try:
        dest_dir.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"refused archive destination outside {base}: {dest_dir}"
        ) from exc
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(2)
    return dest_dir / f"{source.stem}_{stamp}_{suffix}{source.suffix}"


def _move_file(source: Path, dest: Path) -> None:
    """Atomic-when-possible move (R-312); fall back to copy+unlink cross-device."""
    try:
        source.replace(dest)
    except OSError:
        shutil.copy2(source, dest)
        try:
            source.unlink()
        except OSError:
            log.warning("ingestion: could not unlink original %s after copy", source)


def _archive_file(source: Path) -> Path:
    """Move ``source`` into ``ARCHIVED/{subdir}/`` and return the new path."""
    dest = _archive_destination(source, "ARCHIVED")
    _move_file(source, dest)
    return dest


def _fail_file(source: Path, error_summary: dict[str, Any]) -> tuple[Path, Path]:
    """Move ``source`` into ``FAILED/{subdir}/`` and write a sibling .error.json."""
    dest = _archive_destination(source, "FAILED")
    _move_file(source, dest)
    error_path = dest.with_suffix(dest.suffix + ".error.json")
    payload = {
        "file": source.name,
        "moved_to": str(dest),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        **error_summary,
    }
    error_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return dest, error_path


# ---------------------------------------------------------------------------
# Schema validation — submarket_stats
# ---------------------------------------------------------------------------
def _normalize_header(value: str) -> str:
    """Lowercase + strip whitespace + strip BOM (R-308)."""
    return (value or "").lstrip("﻿").strip().lower()


def _validate_submarket_stats_headers(
    headers: Sequence[str],
) -> str | None:
    """Return None if headers are valid, else an error string (R-309, R-310)."""
    if not headers:
        return "empty header row"
    normalized = [_normalize_header(h) for h in headers]
    seen: set[str] = set()
    duplicates: list[str] = []
    for h in normalized:
        if h in seen:
            duplicates.append(h)
        seen.add(h)
    if duplicates:
        return f"duplicate column header(s): {sorted(set(duplicates))}"
    missing = [c for c in _SUBMARKET_STATS_REQUIRED_COLUMNS if c not in seen]
    if missing:
        return f"missing required column(s): {missing}"
    return None


def _coerce_optional_decimal(raw: Any) -> tuple[float | None, str | None]:
    """Parse a CoStar number cell (R-306). Returns (value_or_None, error_or_None)."""
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        return float(raw), None
    s = str(raw).strip()
    if s == "" or s.upper() in {"N/A", "NA", "NULL", "-"}:
        return None, None
    cleaned = s.replace("$", "").replace(",", "").replace("%", "").strip()
    if cleaned in {"", "-"}:
        return None, None
    try:
        return float(cleaned), None
    except ValueError:
        return None, f"unparseable number: {raw!r}"


def _coerce_optional_int(raw: Any) -> tuple[int | None, str | None]:
    val, err = _coerce_optional_decimal(raw)
    if err or val is None:
        return None, err
    return int(round(val)), None


_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d",
)


def _parse_report_date(raw: Any) -> tuple[str | None, str | None]:
    """Parse a date cell with the formats CoStar exports use (R-307)."""
    if raw is None:
        return None, "report_date missing"
    s = str(raw).strip()
    if not s:
        return None, "report_date empty"
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            return d.isoformat(), None
        except ValueError:
            continue
    return None, f"unparseable date: {raw!r}"


def _validate_submarket_stats_row(
    row: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one CSV row. Returns (parsed_row, error_or_None)."""
    norm = {_normalize_header(k): v for k, v in row.items()}

    submarket_name = (norm.get("submarket_name") or "").strip()
    market = (norm.get("market") or "").strip()
    if not submarket_name:
        return None, "submarket_name is empty"
    if not market:
        return None, "market is empty"

    report_date, date_err = _parse_report_date(norm.get("report_date"))
    if date_err:
        return None, date_err

    total_inventory_sf, e1 = _coerce_optional_int(norm.get("total_inventory_sf"))
    vacancy_rate_pct, e2 = _coerce_optional_decimal(norm.get("vacancy_rate_pct"))
    availability_rate_pct, e3 = _coerce_optional_decimal(
        norm.get("availability_rate_pct")
    )
    net_absorption_t12_sf, e4 = _coerce_optional_int(
        norm.get("net_absorption_t12_sf")
    )
    under_construction_sf, e5 = _coerce_optional_int(
        norm.get("under_construction_sf")
    )
    proposed_sf, e6 = _coerce_optional_int(norm.get("proposed_sf"))
    asking_rent_nnn_psf, e7 = _coerce_optional_decimal(
        norm.get("asking_rent_nnn_psf")
    )
    coercion_errors = [e for e in (e1, e2, e3, e4, e5, e6, e7) if e]
    if coercion_errors:
        return None, "; ".join(coercion_errors)

    # Range checks per COSTAR_INGESTION_CONTRACT.md §Validation rules.
    if vacancy_rate_pct is not None and not (0.0 <= vacancy_rate_pct <= 100.0):
        return None, f"vacancy_rate_pct out of range [0,100]: {vacancy_rate_pct}"
    if availability_rate_pct is not None and not (
        0.0 <= availability_rate_pct <= 100.0
    ):
        return None, (
            f"availability_rate_pct out of range [0,100]: {availability_rate_pct}"
        )
    if asking_rent_nnn_psf is not None and asking_rent_nnn_psf <= 0:
        return None, f"asking_rent_nnn_psf must be > 0: {asking_rent_nnn_psf}"
    for label, val in (
        ("total_inventory_sf", total_inventory_sf),
        ("under_construction_sf", under_construction_sf),
        ("proposed_sf", proposed_sf),
    ):
        if val is not None and val < 0:
            return None, f"{label} must be >= 0: {val}"

    return (
        {
            "submarket_name": submarket_name,
            "market": market,
            "total_inventory_sf": total_inventory_sf,
            "vacancy_rate_pct": vacancy_rate_pct,
            "availability_rate_pct": availability_rate_pct,
            "net_absorption_t12_sf": net_absorption_t12_sf,
            "under_construction_sf": under_construction_sf,
            "proposed_sf": proposed_sf,
            "asking_rent_nnn_psf": asking_rent_nnn_psf,
            "report_date": report_date,
        },
        None,
    )


# ---------------------------------------------------------------------------
# Phase 6.1 — per-export-type row validators
# ---------------------------------------------------------------------------
def _require_field(value: Any, name: str) -> tuple[str | None, str | None]:
    """Return (cleaned_str, None) or (None, error). Used for required text fields."""
    if value is None:
        return None, f"{name} is empty"
    s = str(value).strip()
    if not s:
        return None, f"{name} is empty"
    return s, None


def _validate_land_sales_comps_row(
    row: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one land_sales_comps row. R-406, R-407."""
    norm = {_normalize_header(k): v for k, v in row.items()}

    submarket_name, e = _require_field(norm.get("submarket"), "submarket")
    if e:
        return None, e
    address, e = _require_field(norm.get("address"), "address")
    if e:
        return None, e

    sale_date, e = _parse_report_date(norm.get("sale_date"))
    if e:
        return None, f"sale_date: {e}"

    sale_price, e = _coerce_optional_int(norm.get("sale_price"))
    if e:
        return None, f"sale_price: {e}"
    if sale_price is None or sale_price <= 0:
        return None, f"sale_price must be > 0: {sale_price}"

    acres, e = _coerce_optional_decimal(norm.get("acres"))
    if e:
        return None, f"acres: {e}"
    if acres is None or acres <= 0:
        return None, f"acres must be > 0: {acres}"

    price_per_acre, e = _coerce_optional_decimal(norm.get("price_per_acre"))
    if e:
        return None, f"price_per_acre: {e}"
    cap_rate, e = _coerce_optional_decimal(norm.get("cap_rate"))
    if e:
        return None, f"cap_rate: {e}"

    return (
        {
            "submarket_name": submarket_name,
            "address": address,
            "parcel_id": (str(norm.get("parcel_id") or "").strip() or None),
            "county": (str(norm.get("county") or "").strip() or None),
            "acres": acres,
            "sale_date": sale_date,
            "sale_price": sale_price,
            "price_per_acre": price_per_acre,
            "cap_rate": cap_rate,
            "buyer_name": (str(norm.get("buyer_name") or "").strip() or None),
            "seller_name": (str(norm.get("seller_name") or "").strip() or None),
            "zoning": (str(norm.get("zoning") or "").strip() or None),
            "intended_use": (str(norm.get("intended_use") or "").strip() or None),
            "raw": {k: norm.get(k) for k in norm},
        },
        None,
    )


def _validate_building_sales_comps_row(
    row: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one building_sales_comps row. R-406, R-408."""
    norm = {_normalize_header(k): v for k, v in row.items()}

    submarket_name, e = _require_field(norm.get("submarket"), "submarket")
    if e:
        return None, e
    address, e = _require_field(norm.get("address"), "address")
    if e:
        return None, e

    sale_date, e = _parse_report_date(norm.get("sale_date"))
    if e:
        return None, f"sale_date: {e}"

    sale_price, e = _coerce_optional_int(norm.get("sale_price"))
    if e:
        return None, f"sale_price: {e}"
    if sale_price is None or sale_price <= 0:
        return None, f"sale_price must be > 0: {sale_price}"

    building_sf, e = _coerce_optional_decimal(norm.get("building_sf"))
    if e:
        return None, f"building_sf: {e}"
    if building_sf is None or building_sf <= 0:
        return None, f"building_sf must be > 0: {building_sf}"

    price_psf, e = _coerce_optional_decimal(norm.get("price_psf"))
    if e:
        return None, f"price_psf: {e}"
    cap_rate, e = _coerce_optional_decimal(norm.get("cap_rate"))
    if e:
        return None, f"cap_rate: {e}"
    year_built, e = _coerce_optional_int(norm.get("year_built"))
    if e:
        return None, f"year_built: {e}"
    if year_built is not None and not (1850 <= year_built <= datetime.now(timezone.utc).year + 2):
        return None, f"year_built out of range [1850, current+2]: {year_built}"
    clear_height_ft, e = _coerce_optional_decimal(norm.get("clear_height_ft"))
    if e:
        return None, f"clear_height_ft: {e}"
    if clear_height_ft is not None and not (8 <= clear_height_ft <= 80):
        return None, f"clear_height_ft out of range [8, 80]: {clear_height_ft}"

    return (
        {
            "submarket_name": submarket_name,
            "address": address,
            "building_sf": building_sf,
            "sale_date": sale_date,
            "sale_price": sale_price,
            "price_psf": price_psf,
            "cap_rate": cap_rate,
            "buyer_name": (str(norm.get("buyer_name") or "").strip() or None),
            "seller_name": (str(norm.get("seller_name") or "").strip() or None),
            "raw": {k: norm.get(k) for k in norm},
        },
        None,
    )


def _validate_leasing_comps_row(
    row: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one leasing_comps row. R-409, R-410."""
    norm = {_normalize_header(k): v for k, v in row.items()}

    submarket_name, e = _require_field(norm.get("submarket"), "submarket")
    if e:
        return None, e
    address, e = _require_field(norm.get("address"), "address")
    if e:
        return None, e
    tenant_name, e = _require_field(norm.get("tenant_name"), "tenant_name")
    if e:
        return None, e

    lease_start_date, e = _parse_report_date(norm.get("lease_start_date"))
    if e:
        return None, f"lease_start_date: {e}"

    lease_term_months, e = _coerce_optional_int(norm.get("lease_term_months"))
    if e:
        return None, f"lease_term_months: {e}"
    if lease_term_months is None or lease_term_months <= 0:
        return None, f"lease_term_months must be > 0: {lease_term_months}"

    building_sf_leased, e = _coerce_optional_decimal(norm.get("building_sf_leased"))
    if e:
        return None, f"building_sf_leased: {e}"
    if building_sf_leased is None or building_sf_leased <= 0:
        return None, f"building_sf_leased must be > 0: {building_sf_leased}"

    starting_rent_psf_nnn, e = _coerce_optional_decimal(
        norm.get("starting_rent_psf_nnn"),
    )
    if e:
        return None, f"starting_rent_psf_nnn: {e}"
    if starting_rent_psf_nnn is None or starting_rent_psf_nnn <= 0:
        return None, (
            f"starting_rent_psf_nnn must be > 0: {starting_rent_psf_nnn}"
        )

    rent_escalation_pct, e = _coerce_optional_decimal(
        norm.get("rent_escalation_pct"),
    )
    if e:
        return None, f"rent_escalation_pct: {e}"

    naics_code = (str(norm.get("naics_code") or "").strip() or None)
    if naics_code is not None and not naics_code.isdigit():
        return None, f"naics_code must be all digits: {naics_code!r}"

    return (
        {
            "submarket_name": submarket_name,
            "address": address,
            "tenant_name": tenant_name,
            "tenant_industry": (str(norm.get("tenant_industry") or "").strip() or None),
            "naics_code": naics_code,
            "lease_start_date": lease_start_date,
            "lease_term_months": lease_term_months,
            "building_sf_leased": building_sf_leased,
            "starting_rent_psf_nnn": starting_rent_psf_nnn,
            "rent_escalation_pct": rent_escalation_pct,
            "lease_type": (str(norm.get("lease_type") or "").strip() or None),
            "raw": {k: norm.get(k) for k in norm},
        },
        None,
    )


def _validate_land_listings_row(
    row: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one land_listings row. R-411, R-412."""
    norm = {_normalize_header(k): v for k, v in row.items()}

    submarket_name, e = _require_field(norm.get("submarket"), "submarket")
    if e:
        return None, e
    address, e = _require_field(norm.get("address"), "address")
    if e:
        return None, e

    listing_date, e = _parse_report_date(norm.get("listing_date"))
    if e:
        return None, f"listing_date: {e}"

    acres, e = _coerce_optional_decimal(norm.get("acres"))
    if e:
        return None, f"acres: {e}"
    if acres is None or acres <= 0:
        return None, f"acres must be > 0: {acres}"

    asking_price, e = _coerce_optional_int(norm.get("asking_price"))
    if e:
        return None, f"asking_price: {e}"
    if asking_price is not None and asking_price <= 0:
        return None, f"asking_price must be > 0 if populated: {asking_price}"

    asking_price_per_acre, e = _coerce_optional_decimal(
        norm.get("asking_price_per_acre"),
    )
    if e:
        return None, f"asking_price_per_acre: {e}"
    if asking_price_per_acre is not None and asking_price_per_acre <= 0:
        return None, (
            f"asking_price_per_acre must be > 0 if populated: "
            f"{asking_price_per_acre}"
        )

    days_on_market, e = _coerce_optional_int(norm.get("days_on_market"))
    if e:
        return None, f"days_on_market: {e}"
    if days_on_market is not None and days_on_market < 0:
        return None, f"days_on_market must be >= 0: {days_on_market}"

    return (
        {
            "submarket_name": submarket_name,
            "address": address,
            "parcel_id": (str(norm.get("parcel_id") or "").strip() or None),
            "county": (str(norm.get("county") or "").strip() or None),
            "acres": acres,
            "zoning": (str(norm.get("zoning") or "").strip() or None),
            "asking_price": asking_price,
            "asking_price_per_acre": asking_price_per_acre,
            "listing_date": listing_date,
            "days_on_market": days_on_market,
            "listing_broker": (str(norm.get("listing_broker") or "").strip() or None),
            "listing_broker_firm": (str(norm.get("listing_broker_firm") or "").strip() or None),
            "utilities_status": (str(norm.get("utilities_status") or "").strip() or None),
            "entitlement_status": (str(norm.get("entitlement_status") or "").strip() or None),
            "raw": {k: norm.get(k) for k in norm},
        },
        None,
    )


# ---------------------------------------------------------------------------
# markets / submarkets reference upsert (R-301)
# ---------------------------------------------------------------------------
def _ensure_submarket(
    conn: Any, market_name: str, submarket_name: str,
) -> tuple[str, bool, str | None]:
    """Idempotently UPSERT markets+submarkets. Returns (id, created, drift_msg)."""
    market_id = _slugify(market_name)
    submarket_id = f"{market_id}__{_slugify(submarket_name)}"
    with conn.cursor() as cur:
        cur.execute(
            _SQL_UPSERT_MARKETS_REF,
            (market_id, market_name, f"auto-created from CoStar ingestion"),
        )
    with conn.cursor() as cur:
        cur.execute(
            _SQL_UPSERT_SUBMARKETS_REF,
            (submarket_id, market_id, submarket_name),
        )
        returned = cur.fetchone()
    created = returned is not None
    drift_msg: str | None = None
    if not created:
        # ON CONFLICT DO NOTHING returns no row; check existing name (R-315).
        with conn.cursor() as cur:
            cur.execute(_SQL_FETCH_SUBMARKET_NAME, (submarket_id,))
            existing = cur.fetchone()
        if existing and existing[0] and existing[0] != submarket_name:
            drift_msg = (
                f"submarket_id={submarket_id} name drift: "
                f"existing={existing[0]!r} incoming={submarket_name!r}"
            )
    return submarket_id, created, drift_msg


# ---------------------------------------------------------------------------
# Per-file loader — submarket_stats
# ---------------------------------------------------------------------------
def _read_csv_with_bom(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV at ``path`` tolerating UTF-8 BOM (R-308)."""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            raw_headers = next(reader)
        except StopIteration:
            return [], []
        headers = [_normalize_header(h) for h in raw_headers]
        rows: list[dict[str, str]] = []
        for raw_row in reader:
            if not any((cell or "").strip() for cell in raw_row):
                continue
            padded = list(raw_row) + [""] * max(0, len(headers) - len(raw_row))
            rows.append({headers[i]: padded[i] for i in range(len(headers))})
        return raw_headers, rows


def _load_submarket_stats_file(
    conn: Any, cycle_id: str, path: Path,
) -> dict[str, Any]:
    """Validate and load one submarket_stats CSV (R-318, R-302)."""
    summary: dict[str, Any] = {
        "file": path.name,
        "status": "loaded",
        "rows_loaded": 0,
        "rows_failed": 0,
        "row_errors": [],
        "submarkets_auto_created": [],
        "submarket_name_drifts": [],
    }

    try:
        raw_headers, raw_rows = _read_csv_with_bom(path)
    except OSError as exc:
        summary["status"] = "failed"
        summary["error"] = f"read failed: {exc}"
        _fail_file(path, {"errors": [summary["error"]]})
        return summary

    header_error = _validate_submarket_stats_headers(raw_headers)
    if header_error is not None:
        summary["status"] = "failed"
        summary["error"] = header_error
        dest, error_path = _fail_file(
            path, {"errors": [header_error], "headers": raw_headers},
        )
        summary["moved_to"] = str(dest)
        summary["error_path"] = str(error_path)
        return summary

    parsed_rows: list[dict[str, Any]] = []
    for line_num, raw_row in enumerate(raw_rows, start=2):  # +1 for header
        parsed, err = _validate_submarket_stats_row(raw_row)
        if err is not None:
            summary["rows_failed"] += 1
            summary["row_errors"].append({"line": line_num, "error": err})
            continue
        parsed_rows.append(parsed)

    try:
        with conn.transaction():
            ensured: dict[str, str] = {}  # (market, submarket_name) -> submarket_id
            for row in parsed_rows:
                key = f"{row['market']}__{row['submarket_name']}"
                if key in ensured:
                    submarket_id = ensured[key]
                else:
                    submarket_id, created, drift_msg = _ensure_submarket(
                        conn, row["market"], row["submarket_name"],
                    )
                    ensured[key] = submarket_id
                    if created:
                        summary["submarkets_auto_created"].append(submarket_id)
                        _flag(
                            conn, cycle_id, None, row["market"], "data_gap",
                            (
                                f"ingestion: auto-created submarket "
                                f"submarket_id={submarket_id} from CoStar export "
                                f"{path.name}; bbox is NULL — backfill from "
                                f"STORAGE_ARCHITECTURE.md corridor bounding boxes"
                            ),
                            (
                                f"Phase 6+: human seed submarkets.bbox for "
                                f"{submarket_id} so corridor-based queries work"
                            ),
                        )
                    if drift_msg is not None:
                        summary["submarket_name_drifts"].append(drift_msg)
                        _flag(
                            conn, cycle_id, None, row["market"], "conflict",
                            f"ingestion: {drift_msg}",
                            "review CoStar saved-search submarket naming",
                        )
                row["submarket_id"] = submarket_id

            # R-302: idempotent re-ingest — DELETE any prior rows for the
            # (submarket_id, as_of_date, source) tuples we're about to insert.
            dedup_keys = {
                (r["submarket_id"], r["report_date"]) for r in parsed_rows
            }
            with conn.cursor() as cur:
                for submarket_id, as_of_date in dedup_keys:
                    cur.execute(
                        _SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST,
                        (_COSTAR_SOURCE, submarket_id, as_of_date),
                    )

            with conn.cursor() as cur:
                for row in parsed_rows:
                    cur.execute(
                        _SQL_INSERT_MARKET_CONTEXT,
                        (
                            row["submarket_id"],
                            row["report_date"],
                            row["vacancy_rate_pct"],
                            row["availability_rate_pct"],
                            row["net_absorption_t12_sf"],
                            row["under_construction_sf"],
                            row["proposed_sf"],
                            row["asking_rent_nnn_psf"],
                            _COSTAR_SOURCE,
                        ),
                    )
                    summary["rows_loaded"] += 1

            with conn.cursor() as cur:
                cur.execute(
                    _SQL_INSERT_RESEARCH_LOG_INGESTION,
                    (
                        cycle_id, "ingestion", None, None,
                        (
                            f"submarket_stats: file={path.name} "
                            f"rows_loaded={summary['rows_loaded']} "
                            f"rows_failed={summary['rows_failed']}"
                        ),
                    ),
                )

            for err in summary["row_errors"]:
                _flag(
                    conn, cycle_id, None, "",
                    "data_gap",
                    (
                        f"ingestion: row-level validation failure in "
                        f"{path.name} line {err['line']}: {err['error']}"
                    ),
                    "fix CoStar saved-search filter or re-deliver corrected file",
                )
    except Exception as exc:
        log.exception("ingestion transaction failed for %s", path.name)
        summary["status"] = "failed"
        summary["error"] = f"db transaction failed: {exc}"
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            dest, error_path = _fail_file(
                path, {"errors": [summary["error"]]},
            )
            summary["moved_to"] = str(dest)
            summary["error_path"] = str(error_path)
        except Exception:
            log.exception("ingestion: failed to quarantine %s", path)
        return summary

    archived = _archive_file(path)
    summary["moved_to"] = str(archived)
    return summary


# ---------------------------------------------------------------------------
# Phase 6.1 — per-export-type loaders (R-402, R-422)
# ---------------------------------------------------------------------------
def _ingest_one_comp_file(
    conn: Any,
    cycle_id: str,
    path: Path,
    *,
    export_type: str,
    required_columns: tuple[str, ...],
    row_validator,
    market_resolver,                 # row -> (market, used_default, county)
    insert_sql: str,
    insert_params_builder,           # (row, submarket_id) -> tuple
    delete_sql: str,
    delete_params_builder,           # (row, submarket_id) -> tuple
) -> dict[str, Any]:
    """Generic per-file ingest for the four comp/listing export types.

    Mirrors the shape of _load_submarket_stats_file but is parameterised
    over: (a) the validator, (b) per-row market resolution, (c) the
    INSERT and DELETE SQL constants and their per-row parameter builders.
    All loaders flow through this helper so the transaction shape, the
    archive/quarantine logic, the data_gap flag emission, and the
    research_log row are identical across export types.
    """
    summary: dict[str, Any] = {
        "file": path.name,
        "status": "loaded",
        "rows_loaded": 0,
        "rows_failed": 0,
        "row_errors": [],
        "submarkets_auto_created": [],
        "submarket_name_drifts": [],
        "default_market_used_for": [],
    }

    try:
        raw_headers, raw_rows = _read_csv_with_bom(path)
    except OSError as exc:
        summary["status"] = "failed"
        summary["error"] = f"read failed: {exc}"
        _fail_file(path, {"errors": [summary["error"]]})
        return summary

    header_error = _validate_headers_against_required(raw_headers, required_columns)
    if header_error is not None:
        summary["status"] = "failed"
        summary["error"] = header_error
        dest, error_path = _fail_file(
            path, {"errors": [header_error], "headers": raw_headers},
        )
        summary["moved_to"] = str(dest)
        summary["error_path"] = str(error_path)
        return summary

    parsed_rows: list[dict[str, Any]] = []
    for line_num, raw_row in enumerate(raw_rows, start=2):
        parsed, err = row_validator(raw_row)
        if err is not None:
            summary["rows_failed"] += 1
            summary["row_errors"].append({"line": line_num, "error": err})
            continue
        parsed_rows.append(parsed)

    try:
        with conn.transaction():
            ensured: dict[str, str] = {}
            for row in parsed_rows:
                market, used_default, county = market_resolver(row)
                key = f"{market}__{row['submarket_name']}"
                if key in ensured:
                    submarket_id = ensured[key]
                else:
                    submarket_id, created, drift_msg = _ensure_submarket(
                        conn, market, row["submarket_name"],
                    )
                    ensured[key] = submarket_id
                    if created:
                        summary["submarkets_auto_created"].append(submarket_id)
                        _flag(
                            conn, cycle_id, None, market, "data_gap",
                            (
                                f"ingestion ({export_type}): auto-created "
                                f"submarket submarket_id={submarket_id} from "
                                f"{path.name}; bbox is NULL — backfill from "
                                f"STORAGE_ARCHITECTURE.md corridor bounding boxes"
                            ),
                            (
                                f"Phase 6+: human seed submarkets.bbox for "
                                f"{submarket_id}"
                            ),
                        )
                    if drift_msg is not None:
                        summary["submarket_name_drifts"].append(drift_msg)
                        _flag(
                            conn, cycle_id, None, market, "conflict",
                            f"ingestion ({export_type}): {drift_msg}",
                            "review CoStar saved-search submarket naming",
                        )
                    if used_default and county:
                        summary["default_market_used_for"].append(county)
                        _flag(
                            conn, cycle_id, None, market, "data_gap",
                            (
                                f"ingestion ({export_type}): county={county!r} "
                                f"not in _COUNTY_TO_MARKET; defaulted to "
                                f"{market}"
                            ),
                            "expand _COUNTY_TO_MARKET when adding new markets",
                        )
                row["submarket_id"] = submarket_id

            with conn.cursor() as cur:
                seen_dedup_keys: set[tuple] = set()
                for row in parsed_rows:
                    dkey = delete_params_builder(row, row["submarket_id"])
                    if dkey in seen_dedup_keys:
                        continue
                    seen_dedup_keys.add(dkey)
                    cur.execute(delete_sql, dkey)

            with conn.cursor() as cur:
                for row in parsed_rows:
                    cur.execute(
                        insert_sql,
                        insert_params_builder(row, row["submarket_id"]),
                    )
                    summary["rows_loaded"] += 1

            with conn.cursor() as cur:
                cur.execute(
                    _SQL_INSERT_RESEARCH_LOG_INGESTION,
                    (
                        cycle_id, "ingestion", None, None,
                        (
                            f"{export_type}: file={path.name} "
                            f"rows_loaded={summary['rows_loaded']} "
                            f"rows_failed={summary['rows_failed']}"
                        ),
                    ),
                )

            for err in summary["row_errors"]:
                _flag(
                    conn, cycle_id, None, "",
                    "data_gap",
                    (
                        f"ingestion ({export_type}): row-level validation "
                        f"failure in {path.name} line {err['line']}: "
                        f"{err['error']}"
                    ),
                    "fix CoStar saved-search filter or re-deliver corrected file",
                )
    except Exception as exc:
        log.exception("ingestion transaction failed for %s (%s)", path.name, export_type)
        summary["status"] = "failed"
        summary["error"] = f"db transaction failed: {exc}"
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            dest, error_path = _fail_file(
                path, {"errors": [summary["error"]]},
            )
            summary["moved_to"] = str(dest)
            summary["error_path"] = str(error_path)
        except Exception:
            log.exception("ingestion: failed to quarantine %s", path)
        return summary

    archived = _archive_file(path)
    summary["moved_to"] = str(archived)
    return summary


def _validate_headers_against_required(
    headers: Sequence[str], required_columns: tuple[str, ...],
) -> str | None:
    """Generalised header validator (mirrors _validate_submarket_stats_headers)."""
    if not headers:
        return "empty header row"
    normalized = [_normalize_header(h) for h in headers]
    seen: set[str] = set()
    duplicates: list[str] = []
    for h in normalized:
        if h in seen:
            duplicates.append(h)
        seen.add(h)
    if duplicates:
        return f"duplicate column header(s): {sorted(set(duplicates))}"
    missing = [c for c in required_columns if c not in seen]
    if missing:
        return f"missing required column(s): {missing}"
    return None


def _market_resolver_with_county(
    row: Mapping[str, Any],
) -> tuple[str, bool, str | None]:
    """Resolve market from row's county field (used by land sales + listings)."""
    county = row.get("county")
    market, used_default = _resolve_market_from_county(county)
    return market, used_default, county if used_default and county else None


def _market_resolver_default(
    row: Mapping[str, Any],
) -> tuple[str, bool, str | None]:
    """Resolve market via default (used by building sales + leasing comps)."""
    return _DEFAULT_INGESTION_MARKET, False, None


def _land_sales_insert_params(row, submarket_id):
    return (
        row["address"], row["parcel_id"], row["county"], submarket_id,
        row["acres"],
        row["sale_date"], row["sale_price"], row["price_per_acre"],
        row["cap_rate"],
        row["buyer_name"], row["seller_name"], row["zoning"],
        json.dumps(row["raw"]),
    )


def _land_sales_delete_params(row, submarket_id):
    return (submarket_id, row["address"], row["sale_date"])


def _building_sales_insert_params(row, submarket_id):
    return (
        row["address"], submarket_id, row["building_sf"],
        row["sale_date"], row["sale_price"], row["price_psf"], row["cap_rate"],
        row["buyer_name"], row["seller_name"],
        json.dumps(row["raw"]),
    )


def _building_sales_delete_params(row, submarket_id):
    return (submarket_id, row["address"], row["sale_date"])


def _leasing_insert_params(row, submarket_id):
    return (
        row["address"], submarket_id, row["tenant_name"],
        row["tenant_industry"], row["naics_code"],
        row["lease_start_date"], row["lease_term_months"],
        row["building_sf_leased"],
        row["starting_rent_psf_nnn"], row["rent_escalation_pct"],
        row["lease_type"],
        json.dumps(row["raw"]),
    )


def _leasing_delete_params(row, submarket_id):
    return (
        submarket_id, row["address"], row["tenant_name"],
        row["lease_start_date"],
    )


def _land_listings_insert_params(row, submarket_id):
    return (
        row["address"], row["parcel_id"], row["county"], submarket_id,
        row["acres"], row["zoning"],
        row["asking_price"], row["asking_price_per_acre"],
        row["listing_date"], row["days_on_market"],
        row["listing_broker"], row["listing_broker_firm"],
        row["utilities_status"], row["entitlement_status"],
        json.dumps(row["raw"]),
        row["snapshot_date"],
    )


def _land_listings_delete_params(row, submarket_id):
    # Snapshot semantics (R-426) — keyed on snapshot_date + address, not
    # submarket_id (a listing's submarket assignment can change between
    # snapshots if CoStar re-classifies; we still want the prior row gone).
    return (row["snapshot_date"], row["address"])


def _load_land_sales_comps_file(
    conn: Any, cycle_id: str, path: Path,
) -> dict[str, Any]:
    return _ingest_one_comp_file(
        conn, cycle_id, path,
        export_type="land_sales_comps",
        required_columns=_LAND_SALES_COMPS_REQUIRED_COLUMNS,
        row_validator=_validate_land_sales_comps_row,
        market_resolver=_market_resolver_with_county,
        insert_sql=_SQL_INSERT_LAND_SALES,
        insert_params_builder=_land_sales_insert_params,
        delete_sql=_SQL_DELETE_LAND_SALES_FOR_REINGEST,
        delete_params_builder=_land_sales_delete_params,
    )


def _load_building_sales_comps_file(
    conn: Any, cycle_id: str, path: Path,
) -> dict[str, Any]:
    return _ingest_one_comp_file(
        conn, cycle_id, path,
        export_type="building_sales_comps",
        required_columns=_BUILDING_SALES_COMPS_REQUIRED_COLUMNS,
        row_validator=_validate_building_sales_comps_row,
        market_resolver=_market_resolver_default,
        insert_sql=_SQL_INSERT_BUILDING_SALES,
        insert_params_builder=_building_sales_insert_params,
        delete_sql=_SQL_DELETE_BUILDING_SALES_FOR_REINGEST,
        delete_params_builder=_building_sales_delete_params,
    )


def _load_leasing_comps_file(
    conn: Any, cycle_id: str, path: Path,
) -> dict[str, Any]:
    return _ingest_one_comp_file(
        conn, cycle_id, path,
        export_type="leasing_comps",
        required_columns=_LEASING_COMPS_REQUIRED_COLUMNS,
        row_validator=_validate_leasing_comps_row,
        market_resolver=_market_resolver_default,
        insert_sql=_SQL_INSERT_LEASING_COMP,
        insert_params_builder=_leasing_insert_params,
        delete_sql=_SQL_DELETE_LEASING_COMPS_FOR_REINGEST,
        delete_params_builder=_leasing_delete_params,
    )


def _load_land_listings_file(
    conn: Any, cycle_id: str, path: Path, snapshot_date: str,
) -> dict[str, Any]:
    """Load one weekly land_listings snapshot. snapshot_date comes from filename."""
    # Wrap the row validator to stamp snapshot_date on each parsed row.
    def _validator_with_snapshot(raw_row):
        parsed, err = _validate_land_listings_row(raw_row)
        if err is None and parsed is not None:
            parsed["snapshot_date"] = snapshot_date
        return parsed, err

    return _ingest_one_comp_file(
        conn, cycle_id, path,
        export_type="land_listings",
        required_columns=_LAND_LISTINGS_REQUIRED_COLUMNS,
        row_validator=_validator_with_snapshot,
        market_resolver=_market_resolver_with_county,
        insert_sql=_SQL_INSERT_LAND_LISTING,
        insert_params_builder=_land_listings_insert_params,
        delete_sql=_SQL_DELETE_LAND_LISTINGS_FOR_REINGEST,
        delete_params_builder=_land_listings_delete_params,
    )


def _make_simple_loader(file_loader):
    """Wrap a per-file loader into the (conn, cycle_id, files) -> summary signature."""
    def _driver(conn, cycle_id, files):
        per_file: list[dict[str, Any]] = []
        rows_loaded = 0
        rows_failed = 0
        files_loaded = 0
        files_failed = 0
        for path, _date in files:
            result = file_loader(conn, cycle_id, path)
            per_file.append(result)
            rows_loaded += result.get("rows_loaded", 0)
            rows_failed += result.get("rows_failed", 0)
            if result.get("status") == "loaded":
                files_loaded += 1
            else:
                files_failed += 1
        return {
            "status": "loaded",
            "files_loaded": files_loaded,
            "files_failed": files_failed,
            "rows_loaded": rows_loaded,
            "rows_failed": rows_failed,
            "per_file": per_file,
        }
    return _driver


def _load_submarket_stats(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    """Driver-side wrapper that iterates files for the wired loader."""
    out = _make_simple_loader(_load_submarket_stats_file)(conn, cycle_id, files)
    out["export_type"] = "submarket_stats"
    return out


def _load_land_sales_comps(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    out = _make_simple_loader(_load_land_sales_comps_file)(conn, cycle_id, files)
    out["export_type"] = "land_sales_comps"
    return out


def _load_building_sales_comps(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    out = _make_simple_loader(_load_building_sales_comps_file)(conn, cycle_id, files)
    out["export_type"] = "building_sales_comps"
    return out


def _load_leasing_comps(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    out = _make_simple_loader(_load_leasing_comps_file)(conn, cycle_id, files)
    out["export_type"] = "leasing_comps"
    return out


def _load_land_listings(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    """Land listings driver — passes the filename's parsed date as snapshot_date."""
    per_file: list[dict[str, Any]] = []
    rows_loaded = 0
    rows_failed = 0
    files_loaded = 0
    files_failed = 0
    for path, date_str in files:
        # Filename pattern is YYYYMMDD; convert to ISO YYYY-MM-DD for snapshot_date.
        try:
            snapshot_date = datetime.strptime(date_str, "%Y%m%d").date().isoformat()
        except ValueError:
            files_failed += 1
            per_file.append({
                "file": path.name, "status": "failed",
                "error": f"unparseable filename date: {date_str}",
            })
            continue
        result = _load_land_listings_file(conn, cycle_id, path, snapshot_date)
        per_file.append(result)
        rows_loaded += result.get("rows_loaded", 0)
        rows_failed += result.get("rows_failed", 0)
        if result.get("status") == "loaded":
            files_loaded += 1
        else:
            files_failed += 1
    return {
        "status": "loaded",
        "export_type": "land_listings",
        "files_loaded": files_loaded,
        "files_failed": files_failed,
        "rows_loaded": rows_loaded,
        "rows_failed": rows_failed,
        "per_file": per_file,
    }


# Registry-style dispatch — Phase 6.1 wires all 5 recurring export types
# end-to-end. Tenant intel (Export 6) is on-demand and remains
# unregistered until Phase 8+.
_INGESTION_LOADERS: dict[str, dict[str, Any]] = {
    "submarket_stats": {
        "pattern": _SUBMARKET_STATS_FILENAME_RE,
        "loader": _load_submarket_stats,
    },
    "land_sales_comps": {
        "pattern": _LAND_SALES_COMPS_FILENAME_RE,
        "loader": _load_land_sales_comps,
    },
    "building_sales_comps": {
        "pattern": _BUILDING_SALES_COMPS_FILENAME_RE,
        "loader": _load_building_sales_comps,
    },
    "leasing_comps": {
        "pattern": _LEASING_COMPS_FILENAME_RE,
        "loader": _load_leasing_comps,
    },
    "land_listings": {
        "pattern": _LAND_LISTINGS_FILENAME_RE,
        "loader": _load_land_listings,
    },
}


def _count_log_rows_for_ingestion_cycle(conn: Any, cycle_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(_SQL_COUNT_LOG_FOR_INGESTION_CYCLE, (cycle_id,))
        row = cur.fetchone()
    return int(row[0]) if row else 0


def run_ingestion_cycle() -> dict[str, Any]:
    """Scan all configured CoStar export folders and ingest each new file.

    Phase 6 Option A — only ``submarket_stats`` is wired end-to-end. The
    other four registered export types report ``status='not_implemented'``
    and list any files they saw, so the human knows files are accumulating
    but no destructive action is taken (R-322). See
    ``reviews/08_phase6_costar_ingestion/01_risk_review.md``.
    """
    prepare.verify_parameters_unchanged()
    cycle_id = _make_ingestion_cycle_id()
    summary: dict[str, Any] = {
        "cycle_id": cycle_id,
        "aborted": False,
        "abort_reason": None,
        "per_export_type": {},
    }

    with prepare.get_connection() as conn:
        if _count_log_rows_for_ingestion_cycle(conn, cycle_id) > 0:
            summary["aborted"] = True
            summary["abort_reason"] = "cycle_id_collision"
            return summary

        for export_type, spec in _INGESTION_LOADERS.items():
            files = _scan_export_dir(export_type, spec["pattern"])
            summary["per_export_type"][export_type] = spec["loader"](
                conn, cycle_id, files,
            )

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


# ===========================================================================
# Phase 9 — Per-parcel snapshots and per-market strategy memos
# ===========================================================================
# Per reviews/11_phase9_snapshots_memos/01_risk_review.md (R-601..R-647).
# These functions READ the database (parcels, parcel_scores, market_context,
# sales_comps, flagged_items, submarkets, research_log) and WRITE markdown
# to the filesystem (snapshots/, rankings/). They make NO writes to the
# database — Phase 9 has no path back into parcel_scores or the metric.

_DEFAULT_SNAPSHOTS_DIR: Path = _REPO_ROOT / "snapshots"
_DEFAULT_RANKINGS_DIR: Path = _REPO_ROOT / "rankings"

# R-615: path-traversal defense for filename slugs.
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._\-]+$")

# R-622: cap markdown table cell length so a pathological owner_name can't
# blow up rendering.
_MD_TABLE_CELL_MAX = 120

# R-630: capped output sizes for the memo's bounded sections.
_MEMO_TOP_N = 10

# R-628 / R-629: human-readable strategy labels and rationale lookups.
_STRATEGY_LABELS: Mapping[str, str] = {
    "bts": "BTS Development",
    "spec": "Spec Development",
    "land_bank": "Land Bank",
    "ground_lease": "Ground Lease",
    "flip": "Land Flip / Disposition",
}

# R-627: deterministic rationale per (strategy, rating). Each sentence
# traces to the program.md fit-criteria entry for that strategy/rating
# (see program.md L330-L391).
_STRATEGY_RATIONALES: Mapping[tuple[str, str], str] = {
    ("bts", "STRONG"): "By-right entitlement, utilities at boundary, identifiable tenant signal, and accommodates >=150K SF footprint.",
    ("bts", "MODERATE"): "Entitlement path clear but not yet by-right; submarket vacancy and absorption support tenant search.",
    ("bts", "WEAK"): "Rezoning required with uncertain outcome, or geometry/utilities limit the buildable footprint.",
    ("bts", "N/A"): "Below the 8.6-acre minimum implied by a 150K SF footprint at 40% coverage.",
    ("spec", "STRONG"): "Submarket vacancy <5%, positive net absorption, limited competing pipeline, near-by-right entitlements.",
    ("spec", "MODERATE"): "Vacancy 5-7%, positive absorption, entitlement path clear within 6 months.",
    ("spec", "WEAK"): "Vacancy >7% or weak absorption depresses development feasibility.",
    ("spec", "N/A"): "Market fundamentals do not support new spec construction at this time.",
    ("land_bank", "STRONG"): "Below-median basis on an emerging corridor; appreciation potential supports a 3-5 year hold.",
    ("land_bank", "MODERATE"): "Plausible corridor trajectory with a moderate basis discount; some entitlement work likely.",
    ("land_bank", "WEAK"): "Uncertain corridor maturation timeline or carry costs heavy relative to projected appreciation.",
    ("land_bank", "N/A"): "Already at developed-market pricing or in a mature submarket.",
    ("ground_lease", "STRONG"): "Prime location with by-right entitlements; basis supports 5-7% ground rent yield.",
    ("ground_lease", "MODERATE"): "Good location and basis support ground lease yields if developer demand exists.",
    ("ground_lease", "WEAK"): "Submarket fundamentals do not command ground lease premiums today.",
    ("ground_lease", "N/A"): "Submarket where developers default to fee-simple acquisitions.",
    ("flip", "STRONG"): "Off-market basis >=25% below comps with active developer demand and a clean title path.",
    ("flip", "MODERATE"): "Off-market discount 10-25%; some entitlement or marketing work needed before disposition.",
    ("flip", "WEAK"): "Marginal discount or limited buyer pool; risk-adjusted return uncertain.",
    ("flip", "N/A"): "Listed on-market at fair value -- no basis advantage to flip.",
}

_RECOMMENDATION_PURSUE = "PURSUE"
_RECOMMENDATION_MONITOR = "MONITOR"
_RECOMMENDATION_PASS = "PASS"


# ---------------------------------------------------------------------------
# Phase 9 SQL constants (R-606)
# ---------------------------------------------------------------------------
_SQL_FETCH_PARCEL_FOR_SNAPSHOT = (
    "SELECT parcel_id, county, state, market, submarket, "
    "address, owner_name, owner_mailing_address, owner_type_inferred, "
    "acreage, land_sf, zoning, zoning_description, "
    "land_use_code, land_use_description, "
    "assessed_value_land, assessed_value_improvement, assessed_value_total, "
    "fair_market_value, tax_year, tax_amount, tax_status, "
    "last_sale_date, last_sale_price, year_built, "
    "discovery_source, discovery_date, "
    "ST_X(centroid)::float AS centroid_lng, "
    "ST_Y(centroid)::float AS centroid_lat "
    "FROM parcels WHERE parcel_id = %s"
)

# R-608: same latest-row predicate the metric uses (prepare._LATEST_SCORE_WHERE).
_SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT = (
    "SELECT composite_score, confidence_score, actionability, "
    "actionability_blockers, sub_scores, strategy_fit, primary_strategy, "
    "investment_thesis, notes, scored_at "
    "FROM parcel_scores WHERE parcel_id = %s "
    "ORDER BY scored_at DESC LIMIT 1"
)

# R-637: bounded sales comps for the thesis's basis clause.
_SQL_FETCH_NEARBY_SALES_COMPS = (
    "SELECT address, sale_date, sale_price, price_per_acre, acres, "
    "comp_type, buyer_name "
    "FROM sales_comps "
    "WHERE submarket_id = %s "
    "  AND comp_type = 'land' "
    "  AND sale_date >= (CURRENT_DATE - INTERVAL '24 months') "
    "ORDER BY sale_date DESC LIMIT 5"
)

# Open flagged_items rows for the snapshot's "Flags / Open Items" section.
_SQL_FETCH_OPEN_FLAGS_FOR_PARCEL = (
    "SELECT flag_type, description, suggested_resolution, flagged_at "
    "FROM flagged_items "
    "WHERE parcel_id = %s AND status = 'open' "
    "ORDER BY flagged_at DESC LIMIT 25"
)

# R-613: top-N memo highlights ordered by composite_score then scored_at.
_SQL_FETCH_SCORED_PARCELS_FOR_MEMO = (
    "SELECT ps.parcel_id, p.address, p.county, p.submarket, p.acreage, "
    "p.owner_name, p.owner_type_inferred, "
    "ps.composite_score, ps.confidence_score, ps.actionability, "
    "ps.actionability_blockers, ps.sub_scores, ps.strategy_fit, "
    "ps.primary_strategy, ps.scored_at "
    "FROM parcel_scores ps "
    "JOIN parcels p USING (parcel_id) "
    "WHERE p.market = %s "
    "AND ps.scored_at = ("
    "  SELECT MAX(scored_at) FROM parcel_scores "
    "  WHERE parcel_id = ps.parcel_id"
    ") "
    "ORDER BY ps.composite_score DESC NULLS LAST, ps.scored_at DESC"
)

# D5: most recent scoring cycle for the market when caller passes cycle_id=None.
_SQL_FETCH_LATEST_SCORING_CYCLE_FOR_MEMO = (
    "SELECT cycle_id, MAX(timestamp) AS last_seen "
    "FROM research_log "
    "WHERE market = %s AND action_type = 'scoring' "
    "GROUP BY cycle_id "
    "ORDER BY last_seen DESC LIMIT 1"
)

# R-614: recent research_log entries for the memo's narrative.
_SQL_FETCH_RESEARCH_LOG_FOR_MEMO = (
    "SELECT cycle_id, timestamp, action_type, parcel_id, "
    "composite_score, actionability, notes "
    "FROM research_log "
    "WHERE market = %s "
    "ORDER BY timestamp DESC LIMIT 50"
)

_SQL_FETCH_RECENT_FLAGS_FOR_MARKET = (
    "SELECT flag_type, parcel_id, description, suggested_resolution, "
    "flagged_at, status "
    "FROM flagged_items "
    "WHERE market = %s "
    "  AND flagged_at >= (CURRENT_DATE - INTERVAL '7 days') "
    "ORDER BY flagged_at DESC LIMIT 25"
)


# ---------------------------------------------------------------------------
# Phase 9 helpers (R-609, R-610, R-615, R-622, R-623)
# ---------------------------------------------------------------------------
def _safe_filename_slug(s: str) -> str:
    """R-615: assert s is a safe filename component, return it lowercased.

    Raises ValueError on path-traversal-prone input. Explicitly forbids
    "." and ".." (and any all-dots input) even though the character class
    is otherwise permitted, because POSIX treats them specially.
    """
    if not isinstance(s, str) or not s:
        raise ValueError(
            f"slug must be a non-empty str, got {type(s).__name__}"
        )
    if not _SAFE_FILENAME_RE.match(s):
        raise ValueError(
            f"slug contains characters outside [A-Za-z0-9._-]: {s!r}"
        )
    if set(s) == {"."}:
        raise ValueError(f"slug cannot be all dots: {s!r}")
    return s.lower()


def _md_table_cell(value: Any, default: str = "—") -> str:
    """R-622: escape pipes/whitespace, cap length, return a Markdown-safe cell."""
    if value is None or value == "":
        return default
    s = str(value)
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("|", r"\|").replace("`", "")
    if len(s) > _MD_TABLE_CELL_MAX:
        s = s[: _MD_TABLE_CELL_MAX - 1] + "…"
    return s if s else default


def _md_cell(value: Any, default: str = "—") -> str:
    """R-623: NULL-safe rendering for non-table prose / list contexts."""
    if value is None or value == "":
        return default
    s = str(value).strip()
    return s if s else default


def _coerce_json_field(v: Any) -> dict[str, Any]:
    """R-609: psycopg returns JSONB as dict; the test fakes return strings.
    Accept either; return {} on missing or unparseable input.
    """
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, (bytes, bytearray)):
        try:
            v = v.decode("utf-8")
        except Exception:
            return {}
    if isinstance(v, str):
        if not v.strip():
            return {}
        try:
            parsed = json.loads(v)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _to_float(v: Any) -> float | None:
    """R-610: NUMERIC/Decimal/int/str -> float | None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _format_currency(v: Any, *, default: str = "—") -> str:
    n = _to_int(v)
    if n is None:
        return default
    return f"${n:,}"


def _format_currency_psf(v: Any, *, default: str = "—") -> str:
    n = _to_float(v)
    if n is None:
        return default
    return f"${n:.2f}/SF"


def _format_acres(v: Any, *, default: str = "—") -> str:
    n = _to_float(v)
    if n is None:
        return default
    return f"{n:.2f} acres"


def _format_pct(v: Any, *, default: str = "—") -> str:
    n = _to_float(v)
    if n is None:
        return default
    return f"{n:.1f}%"


def _format_int_thousands(v: Any, *, default: str = "—") -> str:
    n = _to_int(v)
    if n is None:
        return default
    return f"{n:,}"


def _format_date(v: Any, *, default: str = "—") -> str:
    if v is None or v == "":
        return default
    return str(v)


# ---------------------------------------------------------------------------
# Phase 9 data fetch
# ---------------------------------------------------------------------------
def _fetch_snapshot_data(conn: Any, parcel_id: str) -> dict[str, Any] | None:
    """Read every row Phase 9 needs for the per-parcel snapshot.

    Returns None if the parcel is not in the parcels table or has no
    parcel_scores row yet. Both are caller errors and surface as a
    LookupError in :func:`generate_snapshot`.
    """
    with conn.cursor() as cur:
        cur.execute(_SQL_FETCH_PARCEL_FOR_SNAPSHOT, (parcel_id,))
        prow = cur.fetchone()
    if not prow:
        return None
    parcel = {
        "parcel_id": prow[0], "county": prow[1], "state": prow[2],
        "market": prow[3], "submarket": prow[4], "address": prow[5],
        "owner_name": prow[6], "owner_mailing_address": prow[7],
        "owner_type_inferred": prow[8],
        "acreage": _to_float(prow[9]), "land_sf": _to_float(prow[10]),
        "zoning": prow[11], "zoning_description": prow[12],
        "land_use_code": prow[13], "land_use_description": prow[14],
        "assessed_value_land": _to_int(prow[15]),
        "assessed_value_improvement": _to_int(prow[16]),
        "assessed_value_total": _to_int(prow[17]),
        "fair_market_value": _to_int(prow[18]),
        "tax_year": _to_int(prow[19]),
        "tax_amount": _to_float(prow[20]),
        "tax_status": prow[21],
        "last_sale_date": prow[22],
        "last_sale_price": _to_int(prow[23]),
        "year_built": _to_int(prow[24]),
        "discovery_source": prow[25], "discovery_date": prow[26],
        "centroid_lng": _to_float(prow[27]),
        "centroid_lat": _to_float(prow[28]),
    }

    with conn.cursor() as cur:
        cur.execute(_SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT, (parcel_id,))
        srow = cur.fetchone()
    if not srow:
        return None
    score = {
        "composite_score": _to_float(srow[0]),
        "confidence_score": _to_float(srow[1]),
        "actionability": srow[2],
        "actionability_blockers": _coerce_json_field(srow[3]),
        "sub_scores": _coerce_json_field(srow[4]),
        "strategy_fit": _coerce_json_field(srow[5]),
        "primary_strategy": srow[6],
        "investment_thesis": srow[7],
        "notes": srow[8],
        "scored_at": srow[9],
    }

    submarket_id = parcel.get("submarket")
    mc: dict[str, Any] = {}
    comps: list[dict[str, Any]] = []
    submarket_name: str | None = None
    if submarket_id:
        with conn.cursor() as cur:
            cur.execute(_SQL_LATEST_MARKET_CONTEXT, (submarket_id,))
            mcrow = cur.fetchone()
        if mcrow:
            mc = {
                "vacancy_rate_pct": _to_float(mcrow[0]),
                "net_absorption_t12_sf": _to_int(mcrow[1]),
                "under_construction_sf": _to_int(mcrow[2]),
                "proposed_sf": _to_int(mcrow[3]),
                "asking_rent_nnn_psf": _to_float(mcrow[4]),
                "as_of_date": mcrow[5],
                "source": mcrow[6],
            }
        with conn.cursor() as cur:
            cur.execute(_SQL_FETCH_NEARBY_SALES_COMPS, (submarket_id,))
            for crow in cur.fetchall():
                comps.append({
                    "address": crow[0],
                    "sale_date": crow[1],
                    "sale_price": _to_int(crow[2]),
                    "price_per_acre": _to_float(crow[3]),
                    "acres": _to_float(crow[4]),
                    "comp_type": crow[5],
                    "buyer_name": crow[6],
                })
        with conn.cursor() as cur:
            cur.execute(_SQL_FETCH_SUBMARKET_NAME, (submarket_id,))
            row = cur.fetchone()
        if row and row[0]:
            submarket_name = str(row[0])

    flags: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(_SQL_FETCH_OPEN_FLAGS_FOR_PARCEL, (parcel_id,))
        for frow in cur.fetchall():
            flags.append({
                "flag_type": frow[0],
                "description": frow[1],
                "suggested_resolution": frow[2],
                "flagged_at": frow[3],
            })

    return {
        "parcel": parcel, "score": score, "market_context": mc,
        "comps": comps, "flags": flags,
        "submarket_name": submarket_name,
    }


# ---------------------------------------------------------------------------
# Phase 9 render helpers — snapshot
# ---------------------------------------------------------------------------
def _render_score_breakdown_table(
    sub_scores: Mapping[str, Any],
    weights: Mapping[str, Any],
) -> tuple[str, float, float]:
    """R-626: 12-row breakdown table; iterate _SUB_SCORE_NAMES so no row is
    omitted. Null sub-scores render as '—' with weighted contribution 0.
    """
    lines = [
        "| Parameter | Sub-Score | Weight | Weighted |",
        "|-----------|-----------|--------|----------|",
    ]
    total_weight = 0.0
    weighted_sum = 0.0
    for name in _SUB_SCORE_NAMES:
        pretty, _src = _SUB_SCORE_PROVENANCE[name]
        w = _to_float(weights.get(name)) or 0.0
        total_weight += w
        s = _to_float(sub_scores.get(name))
        if s is None:
            cell_score = "—"
            cell_weighted = "0.00"
        else:
            weighted_sum += s * w
            cell_score = f"{int(s)}/10"
            cell_weighted = f"{s * w:.2f}"
        lines.append(
            f"| {_md_table_cell(pretty)} | {cell_score} | {w:g} | {cell_weighted} |"
        )
    composite = (weighted_sum / total_weight) * 10.0 if total_weight else 0.0
    lines.append(f"| **Composite** |  |  | **{composite:.1f}/100** |")
    return "\n".join(lines), weighted_sum, composite


def _render_strategy_fit_table(strategy_fit: Mapping[str, Any]) -> str:
    """R-627: STRONG/MODERATE/WEAK/N/A with deterministic rationale."""
    lines = [
        "| Strategy | Fit | Rationale |",
        "|----------|-----|-----------|",
    ]
    for key in _STRATEGY_KEYS:
        label = _STRATEGY_LABELS[key]
        rating = str(strategy_fit.get(key) or "N/A")
        if rating not in ("STRONG", "MODERATE", "WEAK", "N/A"):
            rating = "N/A"
        rationale = _STRATEGY_RATIONALES.get((key, rating), "—")
        lines.append(f"| {label} | {rating} | {_md_table_cell(rationale)} |")
    return "\n".join(lines)


_GATE_ORDER: dict[str, int] = {
    "control": 0, "entitlement": 1, "strategy": 2, "deal_killer": 3,
}


def _render_actionability_table(
    actionability: str | None,
    blockers: Mapping[str, Any],
) -> str:
    """R-628: 4-row gate table + overall verdict line.
    First-failing-gate-wins (Phase 7+8 R-534): the failing gate is FAIL,
    earlier gates PASS, later gates PENDING.
    """
    fail_gate: str | None = None
    if actionability and actionability.startswith("FAIL:"):
        candidate = actionability.split(":", 1)[1]
        if candidate in _GATE_ORDER:
            fail_gate = candidate

    rows = [
        ("control", "Path to control"),
        ("entitlement", "Path to entitlement"),
        ("strategy", "Viable strategy with next step"),
        ("deal_killer", "No deal-killers"),
    ]
    lines = [
        "| Gate | Status | Detail |",
        "|------|--------|--------|",
    ]
    for key, label in rows:
        if fail_gate is not None:
            if key == fail_gate:
                status = "FAIL"
                detail = _md_table_cell(blockers.get(key)) if blockers else "—"
            elif _GATE_ORDER[key] < _GATE_ORDER[fail_gate]:
                status = "PASS"
                detail = "—"
            else:
                status = "PENDING"
                detail = "—"
        elif actionability == "PASS":
            status = "PASS"
            detail = "—"
        else:
            status = "PENDING"
            detail = "—"
        lines.append(f"| {label} | {status} | {detail} |")
    lines.append("")
    lines.append(f"**Overall actionability**: {actionability or 'PENDING'}")
    return "\n".join(lines)


def _render_investment_thesis(
    parcel: Mapping[str, Any],
    score: Mapping[str, Any],
    mc: Mapping[str, Any],
    comps: Sequence[Mapping[str, Any]],
) -> str:
    """R-624 / R-625: deterministic templated narrative; no LLM, no fabrication.
    Each clause is gated on the data points behind it; if a clause's data is
    null, the clause is omitted entirely rather than rendered generically.
    """
    paragraphs: list[str] = []

    # 1. Location story
    loc_clauses: list[str] = []
    submarket = parcel.get("submarket")
    market = parcel.get("market")
    if market and submarket:
        loc_clauses.append(
            f"The parcel sits in the {submarket} submarket of the {market} "
            f"industrial market"
        )
    elif market:
        loc_clauses.append(f"The parcel sits in the {market} industrial market")
    if parcel.get("acreage"):
        loc_clauses.append(f"on a {_format_acres(parcel.get('acreage'))} site")
    if parcel.get("zoning"):
        loc_clauses.append(f"zoned {parcel['zoning']}")
    if loc_clauses:
        paragraphs.append(", ".join(loc_clauses) + ".")

    # 2. Opportunity angle (basis vs. comps + ownership signal)
    opp_clauses: list[str] = []
    avt = parcel.get("assessed_value_total")
    acreage = parcel.get("acreage")
    if avt and acreage and acreage > 0:
        per_ac = avt / acreage
        opp_clauses.append(
            f"County assessed value of {_format_currency(avt)} implies "
            f"~{_format_currency(int(per_ac))}/acre on the tax roll"
        )
    if comps:
        prices = [c["price_per_acre"] for c in comps if c.get("price_per_acre")]
        if prices:
            median = sorted(prices)[len(prices) // 2]
            opp_clauses.append(
                f"recent submarket land comps (n={len(prices)}) "
                f"transacted near {_format_currency(int(median))}/acre"
            )
    owner_type = parcel.get("owner_type_inferred")
    if owner_type and owner_type in {
        "trust", "estate", "trust_absentee", "absentee", "estate_absentee",
    }:
        opp_clauses.append(
            f"owner is classified as {owner_type} -- typically motivated "
            f"for a clean disposition"
        )
    if opp_clauses:
        paragraphs.append(". ".join(opp_clauses) + ".")

    # 3. Market timing (vacancy + absorption from market_context)
    if mc:
        timing_clauses: list[str] = []
        if mc.get("vacancy_rate_pct") is not None:
            timing_clauses.append(
                f"submarket vacancy is {_format_pct(mc['vacancy_rate_pct'])}"
            )
        if mc.get("net_absorption_t12_sf") is not None:
            absorption = mc["net_absorption_t12_sf"]
            direction = "positive" if absorption >= 0 else "negative"
            timing_clauses.append(
                f"trailing-12-month net absorption is "
                f"{_format_int_thousands(absorption)} SF ({direction})"
            )
        if mc.get("under_construction_sf") is not None:
            timing_clauses.append(
                f"under-construction pipeline is "
                f"{_format_int_thousands(mc['under_construction_sf'])} SF"
            )
        if timing_clauses:
            as_of = _format_date(mc.get("as_of_date"))
            src = mc.get("source") or "submarket data"
            paragraphs.append(
                "On market timing: " + ", ".join(timing_clauses)
                + f" (as of {as_of}, {src})."
            )

    # 4. Risk note from actionability
    actionability = score.get("actionability") or "PENDING"
    blockers = score.get("actionability_blockers") or {}
    if actionability == "PASS":
        primary = score.get("primary_strategy")
        primary_label = _STRATEGY_LABELS.get(primary or "", primary or "—")
        paragraphs.append(
            f"Actionability passes all four gates with primary strategy "
            f"{primary_label}; the recommendation below captures the "
            f"specific next step."
        )
    elif actionability and actionability.startswith("FAIL:"):
        gate = actionability.split(":", 1)[1]
        blocker = blockers.get(gate) if isinstance(blockers, Mapping) else None
        suffix = f": {blocker}." if blocker else "."
        paragraphs.append(
            f"Actionability fails at the {gate} gate{suffix} "
            "Remediating that single blocker would move this parcel into "
            "the actionable pipeline."
        )
    else:
        paragraphs.append(
            "Actionability is PENDING -- additional data sources or scoring "
            "passes are required before this parcel can be classified."
        )

    return "\n\n".join(paragraphs)


def _compute_recommendation(
    composite_score: float | None,
    actionability: str | None,
    threshold: float,
    primary_strategy: str | None,
    blockers: Mapping[str, Any],
) -> tuple[str, str]:
    """R-629: PURSUE / MONITOR / PASS plus a one-sentence rationale."""
    cs = composite_score if composite_score is not None else -1.0
    if cs < threshold:
        return (
            _RECOMMENDATION_PASS,
            f"Composite score {cs:.1f} is below the {threshold:.0f} qualification threshold.",
        )
    if actionability == "PASS":
        primary_label = _STRATEGY_LABELS.get(
            primary_strategy or "", primary_strategy or "—"
        )
        return (
            _RECOMMENDATION_PURSUE,
            f"Composite {cs:.1f} clears threshold and all four actionability "
            f"gates pass. Primary strategy: {primary_label}.",
        )
    fail_gate = (
        actionability.split(":", 1)[1]
        if actionability and actionability.startswith("FAIL:")
        else "(unknown)"
    )
    blocker = (
        blockers.get(fail_gate)
        if isinstance(blockers, Mapping) and fail_gate in blockers
        else None
    )
    suffix = f": {blocker}." if blocker else "."
    return (
        _RECOMMENDATION_MONITOR,
        f"Composite {cs:.1f} clears threshold but {fail_gate} gate fails{suffix}",
    )


def _render_snapshot_markdown(
    bundle: Mapping[str, Any],
    *,
    params: Mapping[str, Any],
) -> str:
    """Assemble the full snapshot per program.md L411-L524."""
    parcel = bundle["parcel"]
    score = bundle["score"]
    mc = bundle["market_context"] or {}
    comps = bundle["comps"] or []
    flags = bundle["flags"] or []
    submarket_name = bundle.get("submarket_name") or parcel.get("submarket") or "—"

    weights = params["scoring_weights"]
    threshold = float(params["composite_threshold"])

    actionability = score.get("actionability") or "PENDING"
    composite = _to_float(score.get("composite_score"))
    if actionability == "PASS":
        overall_status = "ACTIONABLE"
    elif composite is not None and composite >= threshold:
        overall_status = "QUALIFIED — NOT ACTIONABLE"
    else:
        overall_status = "BELOW THRESHOLD"

    breakdown_md, _weighted_sum, _displayed_composite = (
        _render_score_breakdown_table(score.get("sub_scores") or {}, weights)
    )
    fit_md = _render_strategy_fit_table(score.get("strategy_fit") or {})
    actionability_md = _render_actionability_table(
        actionability, score.get("actionability_blockers") or {},
    )
    thesis_md = _render_investment_thesis(parcel, score, mc, comps)

    rec, rec_reason = _compute_recommendation(
        composite, actionability, threshold,
        score.get("primary_strategy"),
        score.get("actionability_blockers") or {},
    )

    primary_label = _STRATEGY_LABELS.get(
        score.get("primary_strategy") or "",
        score.get("primary_strategy") or "—",
    )

    if parcel.get("centroid_lat") is not None and parcel.get("centroid_lng") is not None:
        centroid = f"{parcel['centroid_lat']:.6f}, {parcel['centroid_lng']:.6f}"
    else:
        centroid = "—"

    flags_md_lines: list[str] = []
    for f in flags:
        ft = _md_cell(f.get("flag_type"))
        desc = _md_cell(f.get("description"))
        flags_md_lines.append(f"- **{ft}**: {desc}")
    flags_md = "\n".join(flags_md_lines) if flags_md_lines else "- (no open flags)"

    composite_str = f"{composite:.1f}/100" if composite is not None else "—/100"
    confidence = _to_float(score.get("confidence_score"))
    confidence_str = f"{confidence:.2f}" if confidence is not None else "—"

    address_label = _md_cell(parcel.get("address"))
    market_label = _md_cell(parcel.get("market"))

    md = f"""# Site Snapshot: {address_label}
## {market_label} — {submarket_name} | {_format_acres(parcel.get("acreage"))} | Score: {composite_str} | {overall_status}

### Investment Thesis
{thesis_md}

### Location
- **Coordinates**: {centroid}
- **County**: {_md_cell(parcel.get("county"))}
- **State**: {_md_cell(parcel.get("state"))}
- **Parcel ID**: {_md_cell(parcel.get("parcel_id"))}
- **Discovery source**: {_md_cell(parcel.get("discovery_source"))}
- **Discovery date**: {_format_date(parcel.get("discovery_date"))}

### Physical Characteristics
- **Acreage**: {_format_acres(parcel.get("acreage"))}
- **Land SF**: {_format_int_thousands(parcel.get("land_sf"))}
- **Geometry**: not yet wired (Phase 11+ adds parcel-shape analysis)
- **Topography**: not yet wired (Phase 11+ wires USGS 3DEP)
- **Frontage**: not yet wired (Phase 11+ wires DOT road classification)

### Zoning & Entitlements
- **Current zoning**: {_md_cell(parcel.get("zoning"))} — {_md_cell(parcel.get("zoning_description"))}
- **Land use code**: {_md_cell(parcel.get("land_use_code"))} — {_md_cell(parcel.get("land_use_description"))}
- **Required action**: not yet wired (Phase 11+ wires zoning ordinance review)
- **Estimated entitlement timeline**: —

### Utilities
- **Water / Sewer / Electric / Gas / Fiber**: not yet wired (Phase 11+ wires utility provider service maps)

### Environmental
- **Flood zone**: not yet wired (Phase 11+ wires FEMA NFIP)
- **Wetlands**: not yet wired (Phase 11+ wires USGS NWI)
- **EPA flags**: not yet wired (Phase 11+ wires EPA Envirofacts)

### Market Context
- **Submarket vacancy**: {_format_pct(mc.get("vacancy_rate_pct"))}
- **Submarket absorption (T12)**: {_format_int_thousands(mc.get("net_absorption_t12_sf"))} SF
- **Competing pipeline (under construction)**: {_format_int_thousands(mc.get("under_construction_sf"))} SF
- **Submarket asking rent (NNN)**: {_format_currency_psf(mc.get("asking_rent_nnn_psf"))}
- **As of**: {_format_date(mc.get("as_of_date"))} ({_md_cell(mc.get("source"))})

### Ownership & Off-Market Signals
- **Owner**: {_md_cell(parcel.get("owner_name"))}
- **Owner type**: {_md_cell(parcel.get("owner_type_inferred"), default="(not classified)")}
- **Owner mailing address**: {_md_cell(parcel.get("owner_mailing_address"))}
- **Listed**: not yet wired (Phase 11+ joins land_listings)
- **Last sale**: {_format_date(parcel.get("last_sale_date"))} for {_format_currency(parcel.get("last_sale_price"))}
- **Assessed value (total)**: {_format_currency(parcel.get("assessed_value_total"))}
- **Tax status**: {_md_cell(parcel.get("tax_status"))} ({_md_cell(parcel.get("tax_year"))})

### Strategy Fit Assessment
{fit_md}

**Primary recommended strategy**: {primary_label}

### Score Breakdown
{breakdown_md}

- **Confidence score**: {confidence_str}

### Actionability Assessment
{actionability_md}

### Flags / Open Items
{flags_md}

### Recommendation
**{rec}** — {rec_reason}
"""
    return md


# ---------------------------------------------------------------------------
# Phase 9 render helpers — strategy memo
# ---------------------------------------------------------------------------
def _aggregate_pipeline_composition(
    rows: Sequence[Mapping[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    """Counts and breakdowns for the memo's Pipeline Composition section."""
    total = len(rows)
    actionable = [r for r in rows if r.get("actionability") == "PASS"]
    by_strategy: dict[str, int] = {k: 0 for k in _STRATEGY_KEYS}
    by_submarket: dict[str, int] = {}
    by_actionability: dict[str, int] = {}
    above_threshold = 0
    composite_sum = 0.0
    composite_n = 0
    for r in rows:
        cs = _to_float(r.get("composite_score"))
        if cs is not None:
            composite_sum += cs
            composite_n += 1
            if cs >= threshold:
                above_threshold += 1
        ab = r.get("actionability") or "PENDING"
        by_actionability[ab] = by_actionability.get(ab, 0) + 1
        if r.get("actionability") == "PASS":
            ps = r.get("primary_strategy")
            if ps in by_strategy:
                by_strategy[ps] = by_strategy[ps] + 1
        sub = r.get("submarket") or "(unset)"
        by_submarket[sub] = by_submarket.get(sub, 0) + 1
    avg_composite = (composite_sum / composite_n) if composite_n else 0.0
    return {
        "total_scored": total,
        "actionable_count": len(actionable),
        "above_threshold_count": above_threshold,
        "by_strategy": by_strategy,
        "by_submarket": by_submarket,
        "by_actionability": by_actionability,
        "avg_composite": avg_composite,
    }


def _select_top_n_actionable(
    rows: Sequence[Mapping[str, Any]],
    n: int = _MEMO_TOP_N,
) -> list[Mapping[str, Any]]:
    """Top-N rows already sorted by composite_score DESC, scored_at DESC.
    Filter to actionability=PASS first; if fewer than N, fall back to highest-
    composite QUALIFIED parcels so the memo is informative even on thin runs.
    """
    actionable = [r for r in rows if r.get("actionability") == "PASS"]
    if len(actionable) >= n:
        return list(actionable[:n])
    rest = [r for r in rows if r.get("actionability") != "PASS"]
    return list(actionable) + list(rest[: max(0, n - len(actionable))])


def _render_memo_markdown(
    market: str,
    cycle_id: str | None,
    rows: Sequence[Mapping[str, Any]],
    flags: Sequence[Mapping[str, Any]],
    log_entries: Sequence[Mapping[str, Any]],
    *,
    params: Mapping[str, Any],
    today: str,
) -> str:
    """Render the strategy memo per program.md L757-L807. Always renders,
    even when ``rows`` is empty -- the "no pipeline this cycle" memo is
    itself useful (D4)."""
    threshold = float(params["composite_threshold"])
    agg = _aggregate_pipeline_composition(rows, threshold)
    top10 = _select_top_n_actionable(rows)

    cycle_str = cycle_id or "(none — no scoring activity logged for this market)"

    if log_entries:
        action_counts: dict[str, int] = {}
        for e in log_entries:
            at = e.get("action_type") or "(unset)"
            action_counts[at] = action_counts.get(at, 0) + 1
        breakdown = ", ".join(
            f"{k}={v}" for k, v in sorted(action_counts.items())
        )
        approach = (
            f"Recent {market} activity (last {len(log_entries)} log entries): "
            f"{breakdown}."
        )
    else:
        approach = (
            f"No recent research_log entries for {market}. This is either a "
            f"first cycle or the log was rotated."
        )

    criteria_md = (
        f"- Acreage range (parameters.json): "
        f"{params['hard_filters']['acreage_min']}–"
        f"{params['hard_filters']['acreage_max']} acres\n"
        f"- Composite threshold: {threshold:.0f}\n"
        f"- Off-market discovery target: "
        f"{params['discovery']['off_market_discovery_pct_minimum']}%\n"
        f"- Scoring weights: as configured in parameters.json (no per-cycle "
        f"deviations applied)"
    )

    obs_lines = [
        f"- Total scored parcels in {market}: **{agg['total_scored']}**",
        f"- Above composite threshold ({threshold:.0f}): "
        f"**{agg['above_threshold_count']}**",
        f"- Actionable (passes all four gates): "
        f"**{agg['actionable_count']}**",
        f"- Average composite score: **{agg['avg_composite']:.1f}**",
    ]
    actionability_breakdown = ", ".join(
        f"{k}={v}" for k, v in sorted(agg["by_actionability"].items())
    )
    if actionability_breakdown:
        obs_lines.append(f"- Actionability breakdown: {actionability_breakdown}")

    strategy_lines = [
        f"- {_STRATEGY_LABELS.get(k, k)}: {v}"
        for k, v in agg["by_strategy"].items()
        if v > 0
    ]
    if not strategy_lines:
        strategy_lines = ["- (no parcels passed actionability)"]
    submarket_lines = [
        f"- {sub}: {n}"
        for sub, n in sorted(
            agg["by_submarket"].items(),
            key=lambda kv: -kv[1],
        )[:10]
    ]
    if not submarket_lines:
        submarket_lines = ["- (no submarkets observed)"]

    if top10:
        top_lines: list[str] = []
        for r in top10:
            cs = _to_float(r.get("composite_score"))
            cs_str = f"{cs:.1f}" if cs is not None else "—"
            ps = r.get("primary_strategy")
            ps_label = _STRATEGY_LABELS.get(ps or "", ps or "—")
            ab = r.get("actionability") or "PENDING"
            sub = r.get("submarket") or "(unset)"
            addr = _md_cell(r.get("address"))
            owner = _md_cell(r.get("owner_name"))
            acres = _format_acres(r.get("acreage"))
            top_lines.append(
                f"- **{r.get('parcel_id')}** — {addr} ({sub}, {acres}). "
                f"Composite {cs_str}, {ab}, primary={ps_label}. Owner: {owner}."
            )
        top_md = "\n".join(top_lines)
    else:
        top_md = "_No actionable or qualified parcels in this cycle._"

    open_q_lines: list[str] = []
    fail_entitlement = agg["by_actionability"].get("FAIL:entitlement", 0)
    fail_strategy = agg["by_actionability"].get("FAIL:strategy", 0)
    fail_deal = agg["by_actionability"].get("FAIL:deal_killer", 0)
    if fail_entitlement >= 5:
        open_q_lines.append(
            f"- {fail_entitlement} parcels failed the entitlement gate. "
            "Review entitlement-block flags before next cycle."
        )
    if fail_strategy >= 5:
        open_q_lines.append(
            f"- {fail_strategy} parcels failed the strategy gate (no STRONG/"
            "MODERATE strategy fit). Phase 11+ improvements to S1/S3/S7/"
            "S11/S12 will lift composite scores and unblock more strategies."
        )
    if fail_deal >= 5:
        open_q_lines.append(
            f"- {fail_deal} parcels failed the deal-killer gate via a "
            "non-entitlement actionability_block flag — review flagged_items."
        )
    if not open_q_lines:
        open_q_lines.append(
            "- No high-volume gate failures observed this cycle."
        )

    rec_lines: list[str] = []
    if agg["actionable_count"] == 0 and agg["above_threshold_count"] > 0:
        rec_lines.append(
            f"- {agg['above_threshold_count']} parcels cleared the composite "
            "threshold but none passed actionability. Inspect the failing "
            "gates before tightening thresholds."
        )
    if agg["above_threshold_count"] == 0 and agg["total_scored"] >= 5:
        rec_lines.append(
            f"- 0 of {agg['total_scored']} scored parcels cleared composite "
            f"≥ {threshold:.0f}. Wiring S1/S3/S7/S11/S12 (Phase 11+) is the "
            "expected lift; consider lowering composite_threshold for this "
            "market only if the gap persists for 2+ cycles."
        )
    if not rec_lines:
        rec_lines.append(
            "- No data-driven parameter adjustments triggered this cycle."
        )

    flag_md_lines: list[str] = []
    for f in flags:
        flag_md_lines.append(
            f"- **{_md_cell(f.get('flag_type'))}** "
            f"({_md_cell(f.get('parcel_id'), default='(market-level)')}): "
            f"{_md_cell(f.get('description'))}"
        )
    flag_md = "\n".join(flag_md_lines) if flag_md_lines else "- (no recent flags)"

    obs_block = "\n".join(obs_lines)
    strategy_block = "\n".join(strategy_lines)
    submarket_block = "\n".join(submarket_lines)
    open_q_block = "\n".join(open_q_lines)
    rec_block = "\n".join(rec_lines)
    top_count = min(_MEMO_TOP_N, len(top10)) if top10 else 0

    md = f"""# {market.title()} Strategy Memo — {today}

> **Cycle**: {cycle_str}
> **Threshold**: composite ≥ {threshold:.0f} AND actionability = PASS
> **Phase**: 9 (deterministic memo; LLM-driven narrative deferred to a later phase)

## This Cycle's Approach

{approach}

## Criteria Applied

{criteria_md}

## Pipeline Observations

{obs_block}

## Pipeline Composition

**By primary strategy (actionable parcels):**
{strategy_block}

**By submarket (top 10 by parcel count):**
{submarket_block}

## Top {top_count} Highlights

{top_md}

## Open Questions and Recommended Human Decisions

{open_q_block}

## Recommended Adjustments for Next Cycle

{rec_block}

## Recent Flags (last 7 days)

{flag_md}
"""
    return md


# ---------------------------------------------------------------------------
# Phase 9 atomic write (R-617)
# ---------------------------------------------------------------------------
def _atomic_write_text(path: Path, content: str) -> None:
    """Write to a sibling .tmp.{pid} file then os.replace to final path.
    os.replace is atomic on POSIX — readers see either the previous file
    contents or the new file, never a half-written tmp.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    data = content.replace("\r\n", "\n").encode("utf-8")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Phase 9 public API
# ---------------------------------------------------------------------------
def generate_snapshot(
    parcel_id: str,
    *,
    conn: Any = None,
    output_dir: Path | str | None = None,
    params: Mapping[str, Any] | None = None,
) -> Path:
    """Render and persist the per-parcel investment thesis snapshot.

    Reads parcels + latest parcel_scores row + market_context + sales_comps
    + flagged_items, renders the program.md template (L411-L524), writes to
    ``{output_dir}/{slug}_snapshot.md`` (atomic), returns the resolved Path.

    The snapshot is generated for ANY parcel with a parcel_scores row,
    regardless of actionability or composite score. The recommendation
    field captures the PURSUE / MONITOR / PASS verdict.

    Raises:
        ValueError: parcel_id contains characters outside [A-Za-z0-9._-].
        LookupError: parcel does not exist in the database, or no parcel_scores
            row exists yet (call score_parcel first).
    """
    slug = _safe_filename_slug(parcel_id)

    if params is None:
        prepare.verify_parameters_unchanged()
        params = prepare.get_parameters()

    out_dir = Path(output_dir) if output_dir is not None else _DEFAULT_SNAPSHOTS_DIR

    own_conn = False
    ctx = None
    if conn is None:
        own_conn = True
        ctx = prepare.get_connection()
        conn = ctx.__enter__()
    try:
        bundle = _fetch_snapshot_data(conn, parcel_id)
        if bundle is None:
            raise LookupError(
                f"snapshot requires a scored parcel; parcel_id={parcel_id!r} "
                "has no parcels row or no parcel_scores row"
            )
        markdown = _render_snapshot_markdown(bundle, params=params)
    finally:
        if own_conn and ctx is not None:
            ctx.__exit__(None, None, None)

    target = out_dir / f"{slug}_snapshot.md"
    _atomic_write_text(target, markdown)
    return target


def generate_strategy_memo(
    market: str,
    *,
    conn: Any = None,
    output_dir: Path | str | None = None,
    cycle_id: str | None = None,
    params: Mapping[str, Any] | None = None,
    today: str | None = None,
) -> Path:
    """Render and persist the per-market strategy memo per program.md L757-L807.

    Reads all parcels in ``market`` (latest score per parcel), recent
    research_log + flagged_items rows, aggregates by submarket and strategy
    fit, renders the memo template, writes to
    ``{output_dir}/{market_slug}_strategy_memo.md`` (atomic), returns the Path.

    The memo always renders, even with zero scored parcels (D4) -- a "no
    pipeline this cycle" memo is informative for next-cycle planning.
    """
    slug = _safe_filename_slug(market)

    if params is None:
        prepare.verify_parameters_unchanged()
        params = prepare.get_parameters()

    out_dir = Path(output_dir) if output_dir is not None else _DEFAULT_RANKINGS_DIR

    if today is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    own_conn = False
    ctx = None
    if conn is None:
        own_conn = True
        ctx = prepare.get_connection()
        conn = ctx.__enter__()
    try:
        if cycle_id is None:
            with conn.cursor() as cur:
                cur.execute(_SQL_FETCH_LATEST_SCORING_CYCLE_FOR_MEMO, (market,))
                row = cur.fetchone()
            if row and row[0] is not None:
                cycle_id = str(row[0])

        with conn.cursor() as cur:
            cur.execute(_SQL_FETCH_SCORED_PARCELS_FOR_MEMO, (market,))
            rows: list[dict[str, Any]] = []
            for r in cur.fetchall():
                rows.append({
                    "parcel_id": r[0],
                    "address": r[1],
                    "county": r[2],
                    "submarket": r[3],
                    "acreage": _to_float(r[4]),
                    "owner_name": r[5],
                    "owner_type_inferred": r[6],
                    "composite_score": _to_float(r[7]),
                    "confidence_score": _to_float(r[8]),
                    "actionability": r[9],
                    "actionability_blockers": _coerce_json_field(r[10]),
                    "sub_scores": _coerce_json_field(r[11]),
                    "strategy_fit": _coerce_json_field(r[12]),
                    "primary_strategy": r[13],
                    "scored_at": r[14],
                })

        with conn.cursor() as cur:
            cur.execute(_SQL_FETCH_RECENT_FLAGS_FOR_MARKET, (market,))
            flags: list[dict[str, Any]] = []
            for r in cur.fetchall():
                flags.append({
                    "flag_type": r[0],
                    "parcel_id": r[1],
                    "description": r[2],
                    "suggested_resolution": r[3],
                    "flagged_at": r[4],
                    "status": r[5],
                })

        with conn.cursor() as cur:
            cur.execute(_SQL_FETCH_RESEARCH_LOG_FOR_MEMO, (market,))
            log_entries: list[dict[str, Any]] = []
            for r in cur.fetchall():
                log_entries.append({
                    "cycle_id": r[0],
                    "timestamp": r[1],
                    "action_type": r[2],
                    "parcel_id": r[3],
                    "composite_score": _to_float(r[4]),
                    "actionability": r[5],
                    "notes": r[6],
                })

        markdown = _render_memo_markdown(
            market, cycle_id, rows, flags, log_entries,
            params=params, today=today,
        )
    finally:
        if own_conn and ctx is not None:
            ctx.__exit__(None, None, None)

    target = out_dir / f"{slug}_strategy_memo.md"
    _atomic_write_text(target, markdown)
    return target


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


def _git_head_commit() -> str:
    """Return the 7-char short SHA at HEAD, or ``"pending"`` if HEAD has
    no commits yet (e.g., fresh repo).  Used in ``status=crash`` rows where
    no experiment commit was produced."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return "pending"
    sha = proc.stdout.strip()
    return sha if _TSV_COMMIT_RE.match(sha) else "pending"


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


def _check_harness_for_market(market: str) -> dict[str, Any]:
    """R-708 / Setup Step 4b -- harness gate for at least one county."""
    counties = _MARKET_TO_COUNTIES.get(market, [])
    if not counties:
        return {
            "status": "fail",
            "error": f"market={market!r} has no configured counties",
        }
    per_county: dict[str, str] = {}
    overall = "ok"
    for county in counties:
        try:
            harness_status, _ = _harness_gate(county)
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
def evaluate(
    market: str,
    *,
    skip_ingestion: bool = False,
    skip_discovery: bool = False,
    skip_scoring: bool = False,
    skip_memo: bool = False,
) -> dict[str, Any]:
    """Run one full Karpathy 'evaluate' cycle and return the metric.

    Order (R-709): ingestion -> discovery -> scoring -> memo -> metric.
    Each sub-cycle uses its own connection (existing public-API contract);
    R-710 documents that this is intentionally non-transactional across
    sub-cycles.

    R-701: metric read goes through ``prepare.calculate_actionable_pipeline_count``
    and ``prepare.calculate_confidence_weighted_pipeline``.  No reimplementation.
    R-702: ``prepare.verify_parameters_unchanged`` runs at the start.

    The ``skip_*`` flags exist for the test suite -- callers in production
    should leave them False.

    Returns a dict shaped for direct consumption by
    ``append_experiment_log_row`` after the loop driver fills in
    ``commit`` and ``description``.
    """
    prepare.verify_parameters_unchanged()

    started = time.monotonic()
    started_iso = datetime.now(timezone.utc).isoformat()
    log.info(
        "evaluate.start market=%s started_at=%s skip_ingestion=%s "
        "skip_discovery=%s skip_scoring=%s skip_memo=%s",
        market, started_iso,
        skip_ingestion, skip_discovery, skip_scoring, skip_memo,
    )

    sub_summaries: dict[str, Any] = {}
    status = "ok"
    error: str | None = None

    try:
        if not skip_ingestion:
            sub_summaries["ingestion"] = run_ingestion_cycle()

        if not skip_discovery:
            sub_summaries["discovery"] = run_discovery_cycle(market)

        if not skip_scoring:
            sub_summaries["scoring"] = run_scoring_cycle(market)

        if not skip_memo:
            try:
                memo_path = generate_strategy_memo(market)
                sub_summaries["memo"] = {"path": str(memo_path)}
            except Exception:
                # Memo failure is non-fatal -- the metric is still readable.
                # Log and continue so the loop captures the metric movement.
                log.exception("memo generation failed; continuing to metric read")
                sub_summaries["memo"] = {"path": None, "failed": True}

        with prepare.get_connection() as conn:
            metric = prepare.calculate_actionable_pipeline_count(conn)
            confidence = prepare.calculate_confidence_weighted_pipeline(conn)
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
        "api_calls": 0,  # R-712: placeholder, populate in Phase 11+.
        "wall_clock_min": float(elapsed_min),
        "sub_summaries": sub_summaries,
        "error": error,
        "started_at": started_iso,
    }


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
            "commit": _git_head_commit(),
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
        "research.py -- Phase 10: experiment loop, setup-phase verifier, "
        "evaluate(market), append-only TSV writer, and pure decision "
        "function are wired. Phase 9 (per-parcel snapshots + per-market "
        "strategy memos) remains in place. Phases 6.1, 7+8, 5, 4, 3 are "
        "the supporting pipeline. The agent (Claude Code) drives the "
        "loop via experiment_loop(market, confirmed=True) on an "
        "autoresearch/<tag> branch."
    )
    print(f"composite_threshold (from parameters.json, frozen): {threshold}")


if __name__ == "__main__":
    _print_phase10_status()
