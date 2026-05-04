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
import re
import secrets
import shutil
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

_SQL_INSERT_PARCEL_SCORE = (
    "INSERT INTO parcel_scores ("
    "parcel_id, composite_score, confidence_score, "
    "actionability, sub_scores, notes"
    ") VALUES (%s, %s, %s, %s, %s::jsonb, %s) "
    "RETURNING score_id"
)

_SQL_INSERT_RESEARCH_LOG_SCORING = (
    "INSERT INTO research_log "
    "(cycle_id, action_type, market, parcel_id, "
    "composite_score, actionability, notes) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

_SQL_FETCH_PARCEL = (
    "SELECT parcel_id, market, "
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

_SQL_LIST_UNSCORED_PARCELS = (
    "SELECT parcel_id FROM parcels p "
    "WHERE market = %s "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM parcel_scores ps WHERE ps.parcel_id = p.parcel_id"
    ") "
    "ORDER BY parcel_id"
)

_SQL_COUNT_LOG_FOR_SCORING_CYCLE = (
    "SELECT COUNT(*) FROM research_log "
    "WHERE cycle_id = %s AND action_type = 'scoring'"
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
    # Run reject filters (H1, H2) BEFORE any insert or flag (R-24).
    for filt in _HARD_FILTERS:
        result = filt(row, conn, params)
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
        if result.action == "flag":
            # Defer flag inserts until after the parcel UPSERT lands so
            # the foreign-key-equivalent parcel_id reference is meaningful.
            continue
        if result.action != "pass":
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
            for filt in _HARD_FILTERS:
                result = filt(row, conn, params)
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


# ---------------------------------------------------------------------------
# Per-parcel scoring orchestrator (R-201, R-204, R-211)
# ---------------------------------------------------------------------------
def _fetch_parcel_for_scoring(conn: Any, parcel_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_SQL_FETCH_PARCEL, (parcel_id,))
        row = cur.fetchone()
    if not row:
        return None
    return {
        "parcel_id": row[0],
        "market": row[1],
        "centroid_lng": row[2],
        "centroid_lat": row[3],
    }


def score_parcel(
    parcel_id: str,
    *,
    conn: Any = None,
    cycle_id: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute sub-scores S1..S12 for one parcel and persist.

    Phase 5 — Option B per BUILD_PHASES.md L84-L91. S2 and S10 are real,
    S9 is a moderate stub, all others are null with data_gap flag rows.
    Returns a status dict {parcel_id, composite_score, confidence_score,
    sub_scores, status}. ``actionability`` is set to 'PENDING' (Phase 8).
    """
    own_conn = False
    if conn is None:
        # Production path opens its own connection.
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
            }

        sub_scores: dict[str, int | None] = {n: None for n in _SUB_SCORE_NAMES}
        sub_scores["S2_parcel_geometry"] = _compute_s2(conn, parcel_id)
        sub_scores["S9_entitlement_complexity"] = _compute_s9()
        sub_scores["S10_incentives"] = _compute_s10(
            parcel.get("centroid_lng"), parcel.get("centroid_lat"),
        )

        composite = _compute_composite(sub_scores, weights)
        confidence = _compute_confidence(sub_scores)

        notes = (
            f"phase5 mvp: S2={sub_scores['S2_parcel_geometry']} "
            f"S9={sub_scores['S9_entitlement_complexity']} "
            f"S10={sub_scores['S10_incentives']}"
        )

        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        _SQL_INSERT_PARCEL_SCORE,
                        (
                            parcel_id,
                            composite,
                            confidence,
                            "PENDING",
                            json.dumps(sub_scores),
                            notes,
                        ),
                    )
                with conn.cursor() as cur:
                    cur.execute(
                        _SQL_INSERT_RESEARCH_LOG_SCORING,
                        (
                            cycle_id, "scoring", parcel.get("market"), parcel_id,
                            composite, "PENDING", notes,
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
                        f"{name} ({pretty}) unjoined: pending Phase 5+ data wiring ({source})",
                        f"Phase 5+: wire {source} for parcel_id={parcel_id}",
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
            }

        return {
            "parcel_id": parcel_id,
            "status": "scored",
            "composite_score": composite,
            "confidence_score": confidence,
            "sub_scores": sub_scores,
        }
    finally:
        if own_conn:
            try:
                ctx.__exit__(None, None, None)
            except Exception:
                log.exception("scoring connection close failed")


# ---------------------------------------------------------------------------
# Scoring cycle driver (R-213)
# ---------------------------------------------------------------------------
def run_scoring_cycle(market: str) -> dict[str, Any]:
    """Score every unscored parcel in the given market.

    Phase 5 — selects parcels with no parcel_scores row at all (no
    re-scoring in MVP). Returns a summary dict with per-status counts.
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
            cur.execute(_SQL_LIST_UNSCORED_PARCELS, (market,))
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

# R-302 / R-307: filename pattern for the weekly submarket_stats export per
# COSTAR_INGESTION_CONTRACT.md §Export 1.
_SUBMARKET_STATS_FILENAME_RE = re.compile(
    r"^submarket_stats_(\d{8})\.csv$", re.IGNORECASE,
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
# Placeholder loaders for the four deferred export types (Option A scope).
# Phase 6.1+ replaces each with a real loader following the same pattern as
# _load_submarket_stats_file (R-322).
# ---------------------------------------------------------------------------
def _load_placeholder(
    export_type: str,
    conn: Any,
    cycle_id: str,
    files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    """Report files seen but take no destructive action (R-322)."""
    return {
        "status": "not_implemented",
        "export_type": export_type,
        "files_seen": [p.name for p, _ in files],
    }


def _load_land_sales_comps_placeholder(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    return _load_placeholder("land_sales_comps", conn, cycle_id, files)


def _load_building_sales_comps_placeholder(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    return _load_placeholder("building_sales_comps", conn, cycle_id, files)


def _load_leasing_comps_placeholder(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    return _load_placeholder("leasing_comps", conn, cycle_id, files)


def _load_land_listings_placeholder(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    return _load_placeholder("land_listings", conn, cycle_id, files)


def _load_submarket_stats(
    conn: Any, cycle_id: str, files: Sequence[tuple[Path, str]],
) -> dict[str, Any]:
    """Driver-side wrapper that iterates files for the wired loader."""
    per_file: list[dict[str, Any]] = []
    rows_loaded = 0
    rows_failed = 0
    files_loaded = 0
    files_failed = 0
    for path, _date in files:
        result = _load_submarket_stats_file(conn, cycle_id, path)
        per_file.append(result)
        rows_loaded += result.get("rows_loaded", 0)
        rows_failed += result.get("rows_failed", 0)
        if result.get("status") == "loaded":
            files_loaded += 1
        else:
            files_failed += 1
    return {
        "status": "loaded",
        "export_type": "submarket_stats",
        "files_loaded": files_loaded,
        "files_failed": files_failed,
        "rows_loaded": rows_loaded,
        "rows_failed": rows_failed,
        "per_file": per_file,
    }


# Registry-style dispatch so Phase 6.1+ adds an export type by replacing
# one placeholder with a real loader (R-322).
_INGESTION_LOADERS: dict[str, dict[str, Any]] = {
    "submarket_stats": {
        "pattern": _SUBMARKET_STATS_FILENAME_RE,
        "loader": _load_submarket_stats,
    },
    "land_sales_comps": {
        "pattern": re.compile(r"^land_sales_comps_(\d{6})\.csv$", re.IGNORECASE),
        "loader": _load_land_sales_comps_placeholder,
    },
    "building_sales_comps": {
        "pattern": re.compile(r"^building_sales_comps_(\d{6})\.csv$", re.IGNORECASE),
        "loader": _load_building_sales_comps_placeholder,
    },
    "leasing_comps": {
        "pattern": re.compile(r"^leasing_comps_(\d{6})\.csv$", re.IGNORECASE),
        "loader": _load_leasing_comps_placeholder,
    },
    "land_listings": {
        "pattern": re.compile(r"^land_listings_(\d{8})\.csv$", re.IGNORECASE),
        "loader": _load_land_listings_placeholder,
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
# Phase 8: actionability and strategy fit
# ---------------------------------------------------------------------------
def run_actionability_screen(parcel_id: str) -> dict[str, Any]:
    """Apply the four-gate actionability screen. Phase 8."""
    raise NotImplementedError(
        "Actionability screen is not implemented at Phase 3; see BUILD_PHASES.md Phase 8"
    )


def assess_strategy_fit(parcel_id: str) -> dict[str, Any]:
    """Tag a parcel with strategy fit ratings. Phase 8."""
    raise NotImplementedError(
        "Strategy fit is not implemented at Phase 3; see BUILD_PHASES.md Phase 8"
    )


# ---------------------------------------------------------------------------
# Phase 9: snapshots and memos
# ---------------------------------------------------------------------------
def generate_snapshot(parcel_id: str) -> str:
    """Render the per-parcel investment thesis snapshot. Phase 9."""
    raise NotImplementedError(
        "Snapshot generation is not implemented at Phase 3; see BUILD_PHASES.md Phase 9"
    )


def generate_strategy_memo(market: str) -> str:
    """Render the per-market strategy memo. Phase 9."""
    raise NotImplementedError(
        "Strategy memo is not implemented at Phase 3; see BUILD_PHASES.md Phase 9"
    )


# ---------------------------------------------------------------------------
# Phase 10: the experiment loop
# ---------------------------------------------------------------------------
def experiment_loop() -> None:
    """The Karpathy-pattern experiment loop. Phase 10."""
    raise NotImplementedError(
        "The experiment loop is not implemented at Phase 3; see BUILD_PHASES.md Phase 10"
    )


# ---------------------------------------------------------------------------
# CLI demonstration (Phase 1 holdover)
# ---------------------------------------------------------------------------
def _print_phase1_status() -> None:
    """Print enough state to prove the immutable layer is wired correctly."""
    params = prepare.get_parameters()
    threshold = params["composite_threshold"]
    print(
        "research.py — Phase 5 scoring MVP + Phase 6 CoStar ingestion (submarket_stats); "
        "experiment loop not yet implemented."
    )
    print(f"composite_threshold (from parameters.json, frozen): {threshold}")


if __name__ == "__main__":
    _print_phase1_status()
