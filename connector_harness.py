"""
connector_harness.py — Land Site Selector connector test harness.

PHASE 2 SCOPE. Validates county ArcGIS parcel connectors against a standardized
suite of 10 health checks and emits machine-readable JSON reports plus a
markets-wide markdown dashboard. Seeded with Fulton County only.

============================================================================
HARD CONSTRAINTS — DO NOT VIOLATE WITHOUT A NEW THREE-AGENT REVIEW.
============================================================================

1. THIS MODULE DOES NOT TOUCH POSTGRES. It does not import prepare.py. It does
   not open psycopg or SQLAlchemy connections. The harness is the diagnostic
   that runs WHEN PRODUCTION QUERIES FAIL — if the harness depended on the
   same DB layer, that diagnostic role would collapse the moment Postgres is
   the failure mode. See appendix_a_county_connectors.md L897-L903 and
   reviews/03_phase2_connector_harness/01_risk_review.md §4.1 (R-01/R-02).

2. THIS MODULE NEVER WRITES TO sources.json. sources.json is read-only. The
   machine-generated artifact lives in harness_reports/, not in the curated
   connector inventory. See risk review §4.2.

3. PII REDACTION IS STRICT-BY-DEFAULT. Owner names and owner mailing addresses
   are replaced with "[REDACTED]" before any sample feature is serialized to
   disk. A regex-based failsafe runs as the last step before file write; if
   any English-name pattern survives, it is replaced with "[REDACTION_FAILSAFE]"
   and logged as a warning. There is no --no-redact flag. See risk review
   §4.6 (R-03) and open question 9.a.

4. THE HARNESS'S ONLY WRITE SINKS ARE harness_reports/{county}_{ts}.json AND
   harness_reports/markets_dashboard.md. The optional --output PATH flag adds
   a Markdown summary at PATH but never replaces the per-county JSON.

============================================================================
THREE INTEGRATION POINTS (callable from research.py — appendix L897-L903):
============================================================================

  run_harness_for_all_counties()        # on agent startup
  run_harness_for_county(name)          # before a discovery cycle
  diagnose_failure(county)              # on any production query failure

All three return Python dict report objects (the same shape that gets
serialized to JSON). Callers must not assume the dicts are mutated in place.

============================================================================
ARCHITECTURE (single file, per risk review §6.1):
============================================================================

  1. Constants
  2. Dataclasses (Connector, CheckResult, Report)
  3. Registry loader (sources.json + connector_registry.json overlay)
  4. HTTP layer (rate-limited session, retry-with-backoff)
  5. ArcGIS helpers (error-envelope check, response parser)
  6. 10 check functions
  7. Redaction + report writer
  8. Orchestrator (the three integration-point functions)
  9. CLI (argparse, main)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Third-party. The harness depends on `requests` only (risk review §1.5).
# httpx and the Esri arcgis SDK are deliberately avoided.
try:
    import requests
    from requests.adapters import HTTPAdapter
except ImportError:  # pragma: no cover — surfaces a clear error in CI
    requests = None  # type: ignore[assignment]
    HTTPAdapter = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
REPORTS_DIR = REPO_ROOT / "harness_reports"
SOURCES_PATH = REPO_ROOT / "sources.json"
REGISTRY_OVERLAY_PATH = REPO_ROOT / "connector_registry.json"

USER_AGENT = "Land-Research-Harness/0.1 (+contact: COUNTY_HARNESS_CONTACT)"

# HTTP timeouts (connect, read) — risk review §1.1.
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 15.0  # 15s total per the prompt; 30s for slow checks below
SLOW_READ_TIMEOUT = 30.0
MAX_RETRIES = 3
BACKOFF_SCHEDULE_S = (1.0, 2.0, 4.0)  # exponential
RATE_LIMIT_PER_HOST_S = 1.0  # 1 req/sec/host minimum spacing

# Redaction tokens — risk review §3.2.
REDACTION_TOKENS = frozenset({
    "REDACTED", "PROTECTED", "CONFIDENTIAL", "DANIELSLAW",
    "ACT200", "WITHHELDPERLAW", "NAMEONFILE", "NOTPUBLIC", "PRIVATE",
})

# US state codes for address parsing.
US_STATE_CODES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR",
})

# Logical names whose values are PII and must be redacted before disk write.
PII_LOGICAL_NAMES = frozenset({
    "owner_name", "owner_mailing_address", "owner_mailing_address_2",
})

# Failsafe regex: detects "Firstname Lastname" English name patterns that
# might have leaked through the field-map-driven redaction. Risk review §4.6
# requires this assertion to fire on any sample-feature value that looks like
# a real person's name.
NAME_PATTERN = re.compile(
    r"\b[A-Z][a-z]{1,}\s+[A-Z][a-z]{1,}\b"   # Mixed-case "John Smith"
    r"|\b[A-Z]{2,}\s+[A-Z]{2,}(?:\s+[A-Z]{1,3})?\b",  # "SMITH JOHN H" all-caps
    re.UNICODE,
)
# Owner-redaction sentinels: never match these as "real names".
REDACTION_SENTINELS = frozenset({"[REDACTED]", "[REDACTION_FAILSAFE]"})

# Exit codes — consistent with prepare.py convention (risk review §4.4 / prompt).
EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_CONNECTOR_FAILING = 2
EXIT_CONNECTOR_DEGRADED = 3

# Sample size for known-good query / population check.
KNOWN_GOOD_SAMPLE_SIZE = 10
POPULATION_THRESHOLD = 0.80
ADDRESS_PARSE_THRESHOLD = 0.80


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("connector_harness")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Connector:
    """Per-county harness configuration. Built from sources.json + registry overlay."""
    county: str
    state: str
    market: str
    access: str  # arcgis_rest | arcgis_rest_with_fallback | ai_fallback_only
    service_url: Optional[str]
    parcel_layer_id: Optional[int]
    field_mapping: Dict[str, str]
    test_bbox: Optional[Dict[str, float]]
    test_acreage: Optional[Dict[str, float]]
    expected_bbox: Optional[Dict[str, float]]
    parcel_id_field: Optional[str]
    owner_field: Optional[str]
    fallback_portal: Optional[str]
    notes: str = ""
    max_record_count: int = 1000
    # Optional API field names whose 0% population is informational rather than a
    # connector failure (e.g., Subdiv on raw industrial land). Reported in
    # field_population.rates but excluded from low_population_fields.
    optional_fields: Tuple[str, ...] = ()


@dataclass
class CheckResult:
    """A single check's outcome."""
    name: str
    status: str  # pass | fail | skipped
    details: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry loader
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_registry(
    sources_path: Path = SOURCES_PATH,
    overlay_path: Path = REGISTRY_OVERLAY_PATH,
) -> Dict[str, Connector]:
    """
    Read sources.json (canonical) and connector_registry.json (harness overlay)
    and return {county_key: Connector}. Risk review §6.2 hybrid pattern.

    sources.json is read-only here. Per risk review §4.2, we open it with
    mode="r" only.
    """
    sources = _load_json(sources_path)
    try:
        overlay = _load_json(overlay_path)
    except FileNotFoundError:
        overlay = {}

    parcel_block = sources.get("county_parcel_data", {})
    out: Dict[str, Connector] = {}

    for key, src in parcel_block.items():
        if key.startswith("_"):
            continue
        ovl = overlay.get(key) or {}
        # Strip overlay metadata.
        ovl = {k: v for k, v in ovl.items() if not k.startswith("_")}

        out[key] = Connector(
            county=ovl.get("county", key.split("_")[0]),
            state=ovl.get("state", key.split("_")[-1].upper() if "_" in key else "GA"),
            market=ovl.get("market", "atlanta"),
            access=src.get("access", "unknown"),
            service_url=src.get("service_url"),
            parcel_layer_id=src.get("parcel_layer_id"),
            field_mapping=src.get("field_mapping", {}),
            test_bbox=ovl.get("test_bbox"),
            test_acreage=ovl.get("test_acreage"),
            expected_bbox=ovl.get("expected_bbox"),
            parcel_id_field=ovl.get("parcel_id_field"),
            owner_field=ovl.get("owner_field"),
            fallback_portal=src.get("fallback_portal"),
            notes=src.get("_notes", ""),
            max_record_count=src.get("max_record_count", 1000),
            optional_fields=tuple(ovl.get("optional_fields") or ()),
        )

    # Validate overlay keys exist in sources.json (risk review 9.j). A typo
    # in connector_registry.json would otherwise drop a connector silently
    # and only surface as a KeyError later when someone runs
    # `--county <typo>`. Fail loudly at load time instead.
    orphans = [
        key for key in overlay
        if not key.startswith("_") and key not in parcel_block
    ]
    if orphans:
        raise ValueError(
            "connector_registry.json has overlay keys with no matching "
            f"entry in sources.json: {sorted(orphans)}. Either add the "
            "source to sources.json or remove the overlay key."
        )

    return out


# ---------------------------------------------------------------------------
# HTTP layer (risk review §1) — rate-limited, retry-with-backoff, no DB.
# ---------------------------------------------------------------------------

# Per-host last-request timestamp for the 1 req/sec/host minimum spacing.
_LAST_REQUEST_TIME: Dict[str, float] = {}

# Sensitive query params to strip from log messages (risk review R-18).
_SENSITIVE_QUERY_KEYS = ("token", "apikey", "api_key", "key", "auth", "secret")


def _strip_sensitive_query_params(url: str) -> str:
    """Strip credential-bearing query params from a URL for logging (R-18)."""
    if "?" not in url:
        return url
    base, qs = url.split("?", 1)
    parts: List[str] = []
    for chunk in qs.split("&"):
        k = chunk.split("=", 1)[0].lower()
        if k in _SENSITIVE_QUERY_KEYS:
            parts.append(f"{chunk.split('=', 1)[0]}=***")
        else:
            parts.append(chunk)
    return f"{base}?{'&'.join(parts)}"


def _build_session() -> "requests.Session":
    """Build a requests.Session with the harness User-Agent. Retries are manual."""
    if requests is None:
        raise RuntimeError(
            "The harness requires the 'requests' library. "
            "Run: pip install -r requirements.txt"
        )
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def _rate_limit(host: str) -> None:
    """Enforce minimum spacing between requests to the same host."""
    now = time.monotonic()
    last = _LAST_REQUEST_TIME.get(host, 0.0)
    delta = now - last
    if delta < RATE_LIMIT_PER_HOST_S:
        time.sleep(RATE_LIMIT_PER_HOST_S - delta)
    _LAST_REQUEST_TIME[host] = time.monotonic()


def _http_get(
    session: "requests.Session",
    url: str,
    params: Optional[Dict[str, Any]] = None,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
) -> Tuple[Optional[int], Optional["requests.Response"], Optional[str]]:
    """
    Polite GET with retry-with-backoff. Returns (status_code, response, error_message).

    Retry policy (risk review §1.1):
      - Transient errors (ConnectionError, Timeout, gaierror, HTTP 5xx):
        retry up to MAX_RETRIES times with BACKOFF_SCHEDULE_S spacing.
      - HTTP 4xx: fail-fast.
      - DNS failures: one extra retry on socket.gaierror (R-22).
    """
    host = urlparse(url).hostname or "unknown"
    timeout_pair = (DEFAULT_CONNECT_TIMEOUT, read_timeout)
    last_err: Optional[str] = None
    for attempt in range(MAX_RETRIES + 1):
        _rate_limit(host)
        try:
            resp = session.get(url, params=params, timeout=timeout_pair)
        except requests.exceptions.ConnectionError as e:  # type: ignore[union-attr]
            last_err = f"connection: {e.__class__.__name__}"
        except requests.exceptions.Timeout as e:  # type: ignore[union-attr]
            last_err = f"timeout: {e.__class__.__name__}"
        except requests.exceptions.RequestException as e:  # type: ignore[union-attr]
            last_err = f"request: {e.__class__.__name__}"
        else:
            if 200 <= resp.status_code < 400:
                return resp.status_code, resp, None
            if 400 <= resp.status_code < 500:
                # Fail-fast on 4xx (no retry — won't recover).
                return resp.status_code, resp, f"http_{resp.status_code}"
            # 5xx falls through to retry.
            last_err = f"http_{resp.status_code}"

        if attempt < MAX_RETRIES:
            backoff = BACKOFF_SCHEDULE_S[min(attempt, len(BACKOFF_SCHEDULE_S) - 1)]
            logger.info(
                "GET %s failed (%s), retrying in %.1fs (attempt %d/%d)",
                _strip_sensitive_query_params(url), last_err, backoff,
                attempt + 1, MAX_RETRIES,
            )
            time.sleep(backoff)
    return None, None, last_err


# ---------------------------------------------------------------------------
# ArcGIS helpers (risk review §2) — error-envelope check is critical (R-04).
# ---------------------------------------------------------------------------


def _parse_arcgis_response(
    resp: "requests.Response",
) -> Tuple[str, Any]:
    """
    Parse an ArcGIS REST response. Returns (status, payload).
      status="ok"     -> payload is the parsed JSON dict
      status="error"  -> payload is the error dict from the body
      status="invalid"-> payload is a string describing the parse failure

    R-04: ArcGIS returns HTTP 200 with {"error": {...}} on query failure. Without
    this check the harness would silently treat broken connectors as healthy.
    """
    try:
        data = resp.json()
    except ValueError:
        return "invalid", f"non-JSON response (content-type={resp.headers.get('content-type', 'unknown')})"
    if not isinstance(data, dict):
        return "invalid", f"top-level is {type(data).__name__}, expected dict"
    if "error" in data:
        return "error", data["error"]
    return "ok", data


def _arcgis_get(
    session: "requests.Session",
    url: str,
    params: Optional[Dict[str, Any]] = None,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
) -> Tuple[str, Any, Optional[int], Optional[float]]:
    """
    Combine _http_get with _parse_arcgis_response.
    Returns (status, payload, http_status, elapsed_seconds).
    """
    t0 = time.monotonic()
    code, resp, err = _http_get(session, url, params=params, read_timeout=read_timeout)
    elapsed = time.monotonic() - t0
    if resp is None:
        return "transport", err or "unknown_transport_error", code, elapsed
    parse_status, payload = _parse_arcgis_response(resp)
    return parse_status, payload, code, elapsed


# ---------------------------------------------------------------------------
# The 10 standard validation checks (appendix lines ~870-884).
# ---------------------------------------------------------------------------


def check_service_alive(connector: Connector, session: "requests.Session") -> CheckResult:
    """1. {service_url}?f=pjson returns 200 with valid service metadata."""
    if not connector.service_url:
        return CheckResult("service_alive", "skipped", {"reason": "no service_url"})
    status, payload, code, elapsed = _arcgis_get(
        session, connector.service_url, params={"f": "pjson"}
    )
    if status == "ok":
        return CheckResult("service_alive", "pass", {
            "response_time_ms": int(elapsed * 1000),
            "service_name": payload.get("mapName") or payload.get("name") or "unknown",
        })
    return CheckResult("service_alive", "fail", {
        "transport_status": status,
        "http_code": code,
        "error": payload if status == "error" else str(payload),
    })


def check_layer_schema(
    connector: Connector, session: "requests.Session"
) -> Tuple[CheckResult, Optional[Dict[str, Any]]]:
    """2. {service_url}/{layer_id}?f=pjson returns layer metadata. Returns
    (CheckResult, layer_schema_dict_or_None) so downstream checks can inspect."""
    if not connector.service_url or connector.parcel_layer_id is None:
        return CheckResult("layer_schema", "skipped",
                           {"reason": "missing service_url or parcel_layer_id"}), None
    layer_url = f"{connector.service_url.rstrip('/')}/{connector.parcel_layer_id}"
    status, payload, code, _ = _arcgis_get(session, layer_url, params={"f": "pjson"})
    if status != "ok":
        return CheckResult("layer_schema", "fail", {
            "transport_status": status, "http_code": code,
            "error": payload if status == "error" else str(payload),
        }), None
    fields = payload.get("fields") or []
    capabilities = (payload.get("capabilities") or "").lower()
    needs_query = "query" in capabilities
    return CheckResult("layer_schema", "pass" if needs_query else "fail", {
        "fields_found": len(fields),
        "capabilities": payload.get("capabilities", ""),
    }), payload


def check_field_mapping(
    connector: Connector, layer_schema: Optional[Dict[str, Any]]
) -> CheckResult:
    """3. Every field name in connector.field_mapping exists in the layer
    schema. R-07: report case-difference hint when names differ only by case."""
    if not layer_schema:
        return CheckResult("field_mapping", "skipped", {"reason": "no layer schema"})
    # An empty or all-null field_mapping is an invalid configuration: nothing
    # would be ingested from this layer. Treat as a hard fail so the
    # misconfiguration surfaces during harness runs instead of silently
    # passing.
    non_null = [v for v in connector.field_mapping.values() if v]
    if not non_null:
        return CheckResult("field_mapping", "fail",
                           {"reason": "field_mapping is empty or all-null"})
    server_fields = {f.get("name") for f in layer_schema.get("fields") or []}
    server_fields_lower = {n.lower(): n for n in server_fields if n}
    missing: List[str] = []
    case_hints: List[Dict[str, str]] = []
    for logical, server_name in connector.field_mapping.items():
        if not server_name:
            continue
        if server_name in server_fields:
            continue
        lower = server_name.lower()
        if lower in server_fields_lower:
            case_hints.append({
                "logical": logical,
                "configured": server_name,
                "actual": server_fields_lower[lower],
            })
        else:
            missing.append(server_name)
    return CheckResult("field_mapping",
                       "fail" if missing else "pass",
                       {"missing_fields": missing, "case_hints": case_hints})


def _build_known_good_query_params(
    connector: Connector, where: str = "1=1", count: int = KNOWN_GOOD_SAMPLE_SIZE,
    out_fields: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the standard query params. R-13: NO runtime override of
    test bbox/acreage; we use connector.test_bbox / connector.test_acreage as
    snapshotted into the report."""
    params: Dict[str, Any] = {
        "f": "pjson", "where": where,
        "outFields": out_fields or ",".join(connector.field_mapping.values()) or "*",
        "outSR": 4326, "returnGeometry": "true",
        "resultRecordCount": count,
    }
    bbox = connector.test_bbox or {}
    if all(k in bbox for k in ("xmin", "ymin", "xmax", "ymax")):
        params["geometry"] = f"{bbox['xmin']},{bbox['ymin']},{bbox['xmax']},{bbox['ymax']}"
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = 4326
        params["spatialRel"] = "esriSpatialRelIntersects"
    acreage = connector.test_acreage or {}
    acres_field = connector.field_mapping.get("acreage")
    if acres_field and "min" in acreage and "max" in acreage:
        params["where"] = (
            f"{acres_field} >= {acreage['min']} AND {acres_field} <= {acreage['max']}"
        )
    return params


def check_known_good_query(
    connector: Connector, session: "requests.Session"
) -> Tuple[CheckResult, List[Dict[str, Any]]]:
    """4. Spatial query against test bbox returns features. Returns
    (CheckResult, features_list)."""
    if not connector.service_url or connector.parcel_layer_id is None:
        return CheckResult("known_good_query", "skipped",
                           {"reason": "missing service_url or parcel_layer_id"}), []
    query_url = f"{connector.service_url.rstrip('/')}/{connector.parcel_layer_id}/query"
    params = _build_known_good_query_params(connector)
    status, payload, code, _ = _arcgis_get(session, query_url, params=params)
    if status != "ok":
        return CheckResult("known_good_query", "fail", {
            "transport_status": status, "http_code": code,
            "error": payload if status == "error" else str(payload),
        }), []
    features = payload.get("features") or []
    return CheckResult("known_good_query",
                       "pass" if features else "fail",
                       {"features_returned": len(features)}), features


def check_field_population(
    features: List[Dict[str, Any]],
    field_mapping: Dict[str, str],
    optional_fields: Tuple[str, ...] = (),
) -> CheckResult:
    """5. Per-field non-null fraction. R-14: sample size is whatever the
    known-good query returned, threshold POPULATION_THRESHOLD.
    Phase 2 fix-forward: fields listed in `optional_fields` (e.g., Subdiv on
    raw industrial land) are reported in `rates` but excluded from
    `low_population_fields` — their absence is informational, not a connector
    failure."""
    if not features:
        return CheckResult("field_population", "skipped",
                           {"reason": "no features from known_good_query"})
    rates: Dict[str, float] = {}
    n = len(features)
    target_fields = [v for v in field_mapping.values() if v]
    for fname in target_fields:
        non_null = sum(
            1 for f in features
            if (f.get("attributes") or {}).get(fname) not in (None, "", "Null", "<Null>")
        )
        rates[fname] = round(non_null / n, 3) if n else 0.0
    optional_set = set(optional_fields or ())
    low = [
        fname for fname, r in rates.items()
        if r < POPULATION_THRESHOLD and fname not in optional_set
    ]
    return CheckResult("field_population",
                       "fail" if low else "pass",
                       {
                           "sample_size": n,
                           "rates": rates,
                           "low_population_fields": low,
                           "optional_fields": sorted(optional_set & set(rates)),
                       })


def check_owner_data_sanity(
    features: List[Dict[str, Any]], owner_field: Optional[str],
) -> CheckResult:
    """6. Owner names look real (not redacted, not redaction-token placeholder,
    length > 3). R-09: all-uppercase ALONE is not a redaction signal — only
    explicit redaction-token match is."""
    if not features or not owner_field:
        return CheckResult("owner_data_sanity", "skipped",
                           {"reason": "no features or owner field configured"})
    redacted = 0
    too_short = 0
    sample_size = len(features)
    for f in features:
        v = (f.get("attributes") or {}).get(owner_field)
        if not v:
            continue
        v = str(v).strip()
        if len(v) <= 3:
            too_short += 1
            continue
        # Strip non-alpha for token comparison.
        compact = re.sub(r"[^A-Z]", "", v.upper())
        if any(tok in compact for tok in REDACTION_TOKENS):
            redacted += 1
    redacted_rate = redacted / sample_size if sample_size else 0.0
    return CheckResult("owner_data_sanity",
                       "fail" if redacted_rate > 0 else "pass",
                       {
                           "sample_size": sample_size,
                           "redacted_count": redacted,
                           "too_short_count": too_short,
                           "redaction_detected": redacted_rate > 0,
                       })


_PO_BOX_PAT = re.compile(r"\bP\.?O\.?\s*BOX\b", re.IGNORECASE)
# Allow comma OR whitespace (or mixed) between state and ZIP — Fulton and
# other portals store mailing addresses as "ATLANTA, GA 30303" or "ATLANTA,
# GA, 30303" interchangeably.
_STATE_ZIP_PAT = re.compile(r"\b([A-Z]{2})[,\s]+\d{5}(?:-\d{4})?\b")
# Match a street-number pattern anywhere in the string (not just at start) so
# attention-line, C/O, or "Suite NNN" prefixes don't fail an otherwise-valid
# mailing address.
_STREET_NUMBER_PAT = re.compile(r"\b\d+\s+\S")
# Lenient site-address heuristic: county portals usually store site addresses
# as STREET-ONLY strings (no city/state/ZIP), so the strict state+ZIP check
# would 0% legitimate sites. Accept either a leading street number followed by
# a road word, OR an unnumbered named road (e.g., "0 CAMPBELLTON FAIRBURN RD"
# is a real Fulton site address for raw land parcels).
_SITE_ROAD_WORDS = (
    r"RD|ROAD|ST|STREET|AVE|AVENUE|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|"
    r"CT|COURT|PL|PLACE|HWY|HIGHWAY|PKWY|PARKWAY|WAY|TRL|TRAIL|"
    r"CIR|CIRCLE|TER|TERRACE|LOOP|RUN|RIDGE|RDG"
)
_SITE_STREET_PAT = re.compile(
    rf"^\s*(\d+\s+\S+|[A-Z][A-Z0-9 \-'\.]*\s+(?:{_SITE_ROAD_WORDS})\b)",
    re.IGNORECASE,
)


def _mailing_parses_cleanly(addr: str) -> bool:
    """Mailing address: must have a US state + ZIP; PO-Box bypass on street
    rule. Designed to run on EITHER a single combined string OR the
    concatenation of OwnerAddr1 + OwnerAddr2 (county portals frequently split
    mailing addresses across two fields). The street-number probe is a
    `search` (not `match`) so attention-line / C/O / Suite prefixes don't
    fail an otherwise-valid mailing address."""
    if not addr:
        return False
    addr = addr.strip()
    state_match = _STATE_ZIP_PAT.search(addr.upper())
    if not state_match:
        return False
    if state_match.group(1) not in US_STATE_CODES:
        return False
    if _PO_BOX_PAT.search(addr):
        return True
    return bool(_STREET_NUMBER_PAT.search(addr))


def _site_parses_cleanly(addr: str) -> bool:
    """Site address: no state/ZIP required (most county portals store street-
    only strings here). Accept leading street number OR named road word."""
    if not addr:
        return False
    return bool(_SITE_STREET_PAT.match(addr.strip()))


# Backwards-compatible shim — older code paths and tests call this with
# allow_po_box=True for mailing addresses, allow_po_box=False for site.
def _address_parses_cleanly(addr: str, allow_po_box: bool = False) -> bool:
    if allow_po_box:
        return _mailing_parses_cleanly(addr)
    return _site_parses_cleanly(addr)


def check_address_parsing(
    features: List[Dict[str, Any]], field_mapping: Dict[str, str],
) -> CheckResult:
    """7. Site addresses + mailing addresses parse cleanly.
    R-16: PO-Box bypass on the mailing-address branch.
    Phase 2 fix-forward: site addresses use a street-only heuristic; mailing
    addresses concatenate OwnerAddr1 + OwnerAddr2 (or the configured logical
    `owner_mailing_address` and `owner_mailing_address_2` fields) when both
    are present, because county portals typically split the mailing address
    across two fields."""
    if not features:
        return CheckResult("address_parsing", "skipped", {"reason": "no features"})
    site_field = field_mapping.get("site_address")
    mail_field = field_mapping.get("owner_mailing_address")
    mail_field_2 = field_mapping.get("owner_mailing_address_2")
    if not site_field and not mail_field:
        return CheckResult("address_parsing", "skipped",
                           {"reason": "no address fields configured"})
    site_ok = mail_ok = 0
    site_total = mail_total = 0
    for f in features:
        attrs = f.get("attributes") or {}
        if site_field:
            v = attrs.get(site_field)
            if v:
                site_total += 1
                if _site_parses_cleanly(str(v)):
                    site_ok += 1
        if mail_field:
            v1 = attrs.get(mail_field)
            v2 = attrs.get(mail_field_2) if mail_field_2 else None
            combined: Optional[str]
            if v1 and v2:
                combined = f"{v1} {v2}"
            elif v1:
                combined = str(v1)
            elif v2:
                combined = str(v2)
            else:
                combined = None
            if combined:
                mail_total += 1
                if _mailing_parses_cleanly(combined):
                    mail_ok += 1
    site_rate = site_ok / site_total if site_total else 1.0
    mail_rate = mail_ok / mail_total if mail_total else 1.0
    overall = min(site_rate, mail_rate)
    return CheckResult("address_parsing",
                       "fail" if overall < ADDRESS_PARSE_THRESHOLD else "pass",
                       {
                           "site_address_parse_rate": round(site_rate, 3),
                           "mailing_address_parse_rate": round(mail_rate, 3),
                       })


def check_geometry_validation(
    features: List[Dict[str, Any]], expected_bbox: Optional[Dict[str, float]],
) -> CheckResult:
    """8. R-06 + R-15: assert WGS84 range; assert within expected_bbox if given;
    count empty geometries separately."""
    if not features:
        return CheckResult("geometry_validation", "skipped", {"reason": "no features"})
    valid = empty = out_of_range = out_of_bbox = 0
    for f in features:
        geom = f.get("geometry") or {}
        rings = geom.get("rings") or []
        if not rings or not rings[0]:
            empty += 1
            continue
        # Check first ring's first coordinate as a representative.
        first_coord = rings[0][0]
        if not first_coord or len(first_coord) < 2:
            empty += 1
            continue
        x, y = first_coord[0], first_coord[1]
        # WGS84 sanity: longitude in [-180, 180], latitude in [-90, 90].
        if not (-180 <= x <= 180 and -90 <= y <= 90):
            out_of_range += 1
            continue
        if expected_bbox:
            if not (expected_bbox["xmin"] - 0.5 <= x <= expected_bbox["xmax"] + 0.5
                    and expected_bbox["ymin"] - 0.5 <= y <= expected_bbox["ymax"] + 0.5):
                out_of_bbox += 1
                continue
        valid += 1
    n = len(features)
    empty_rate = empty / n if n else 0.0
    fail = out_of_range > 0 or empty_rate >= 0.05 or out_of_bbox > 0
    return CheckResult("geometry_validation",
                       "fail" if fail else "pass",
                       {
                           "valid": valid, "empty": empty,
                           "out_of_range": out_of_range, "out_of_bbox": out_of_bbox,
                           "empty_rate": round(empty_rate, 3),
                       })


def check_pagination(connector: Connector, session: "requests.Session") -> CheckResult:
    """9. Pagination: request 1 record, then 10. R-08: include orderByFields
    so ordering is deterministic."""
    if not connector.service_url or connector.parcel_layer_id is None:
        return CheckResult("pagination", "skipped",
                           {"reason": "missing service_url or parcel_layer_id"})
    parcel_id_field = connector.parcel_id_field or connector.field_mapping.get("parcel_id")
    if not parcel_id_field:
        return CheckResult("pagination", "skipped",
                           {"reason": "no parcel_id field configured for orderBy"})
    query_url = f"{connector.service_url.rstrip('/')}/{connector.parcel_layer_id}/query"
    base = _build_known_good_query_params(connector, count=10)
    base["orderByFields"] = parcel_id_field
    base["resultRecordCount"] = 1
    s1, p1, _, _ = _arcgis_get(session, query_url, params=base)
    base["resultRecordCount"] = 10
    s2, p2, _, _ = _arcgis_get(session, query_url, params=base)
    if s1 != "ok" or s2 != "ok":
        return CheckResult("pagination", "fail", {"reason": "query failed"})
    n1 = len(p1.get("features") or [])
    n2 = len(p2.get("features") or [])
    return CheckResult("pagination",
                       "pass" if n1 == 1 and 1 < n2 <= 10 else "fail",
                       {"count_for_1": n1, "count_for_10": n2})


def check_performance_baseline(
    connector: Connector, session: "requests.Session",
) -> CheckResult:
    """10. R-10: measure but don't fail on a single run. Trend logic is the
    job of a separate analyzer reading 90 days of harness_reports/."""
    if not connector.service_url or connector.parcel_layer_id is None:
        return CheckResult("performance_baseline", "skipped",
                           {"reason": "missing service_url or parcel_layer_id"})
    query_url = f"{connector.service_url.rstrip('/')}/{connector.parcel_layer_id}/query"
    status, _, _, elapsed = _arcgis_get(
        session, query_url,
        params=_build_known_good_query_params(connector, count=10),
        read_timeout=SLOW_READ_TIMEOUT,
    )
    return CheckResult("performance_baseline", "pass" if status == "ok" else "fail", {
        "response_time_ms": int(elapsed * 1000),
        "note": "Trend detection requires history; single-run pass.",
    })


# ---------------------------------------------------------------------------
# Redaction + report writer (risk review §3, §4.6 — R-03 strict-by-default).
# ---------------------------------------------------------------------------


def _redact_feature(
    feature: Dict[str, Any], field_mapping: Dict[str, str],
) -> Dict[str, Any]:
    """Replace PII fields with [REDACTED] before any disk write."""
    pii_server_fields = set()
    for logical, server_name in field_mapping.items():
        if logical in PII_LOGICAL_NAMES or "address" in logical.lower():
            if server_name:
                pii_server_fields.add(server_name)
    attrs = dict(feature.get("attributes") or {})
    for k in list(attrs.keys()):
        if k in pii_server_fields:
            attrs[k] = "[REDACTED]"
    out = dict(feature)
    out["attributes"] = attrs
    return out


def _failsafe_check(
    features: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Last-ditch regex sweep for English-name patterns. If any survive after
    field-map redaction, replace with [REDACTION_FAILSAFE] and emit a warning.
    R-03 — this is the worst-case backstop."""
    warnings_out: List[str] = []
    sanitized: List[Dict[str, Any]] = []
    for feat in features:
        attrs = dict(feat.get("attributes") or {})
        for k, v in list(attrs.items()):
            if not isinstance(v, str):
                continue
            if v in REDACTION_SENTINELS:
                continue
            if NAME_PATTERN.search(v):
                attrs[k] = "[REDACTION_FAILSAFE]"
                warnings_out.append(
                    f"redaction failsafe triggered on field {k!r}; "
                    f"name pattern matched a residual value"
                )
        new = dict(feat)
        new["attributes"] = attrs
        sanitized.append(new)
    return sanitized, warnings_out


def _overall_health(check_results: List[CheckResult]) -> str:
    """Compute the overall health label from the 10 check results."""
    statuses = [c.status for c in check_results]
    fails = sum(1 for s in statuses if s == "fail")
    if fails == 0:
        return "healthy"
    # Service or layer or known-good failing -> failing overall.
    critical_names = {"service_alive", "layer_schema", "known_good_query"}
    if any(c.status == "fail" and c.name in critical_names for c in check_results):
        return "failing"
    # Otherwise some non-critical checks failed -> degraded.
    return "degraded"


def _build_report(
    connector: Connector,
    check_results: List[CheckResult],
    sample_features: List[Dict[str, Any]],
    warnings_out: List[str],
    errors: List[str],
) -> Dict[str, Any]:
    """Assemble the final report dict per appendix lines 909-936."""
    return {
        "county": connector.county,
        "market": connector.market,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "overall_health": _overall_health(check_results),
        "checks": {c.name: {"status": c.status, **c.details} for c in check_results},
        "sample_features": sample_features,
        "warnings": warnings_out,
        "errors": errors,
        "connector_config_snapshot": {
            "access": connector.access,
            "service_url": connector.service_url,
            "parcel_layer_id": connector.parcel_layer_id,
            "test_bbox": connector.test_bbox,
            "test_acreage": connector.test_acreage,
        },
    }


def _write_report(report: Dict[str, Any], reports_dir: Path = REPORTS_DIR) -> Path:
    """R-03 enforcement: a final assertion that no sample-feature value
    contains a residual English-name pattern. If the assertion fails the
    write is aborted and the harness exits non-zero — corrupting downstream
    is worse than missing a report."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    for feat in report.get("sample_features") or []:
        for k, v in (feat.get("attributes") or {}).items():
            if isinstance(v, str) and v not in REDACTION_SENTINELS:
                if NAME_PATTERN.search(v):
                    raise RuntimeError(
                        f"redaction failsafe assertion failed: field {k!r} "
                        f"still contains a name pattern after _failsafe_check"
                    )
    ts = report["timestamp"].replace(":", "").replace("-", "")
    out_path = reports_dir / f"{report['county']}_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    return out_path


# ---------------------------------------------------------------------------
# Orchestrator — three integration points (appendix lines 897-903).
# ---------------------------------------------------------------------------


def _run_all_checks(connector: Connector, quick: bool = False) -> Dict[str, Any]:
    """Run the 10 checks for a single connector. Returns the report dict.
    R-20: ai_fallback_only connectors emit an n/a stub report."""
    if connector.access == "ai_fallback_only":
        return {
            "county": connector.county, "market": connector.market,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "overall_health": "n_a",
            "checks": {}, "sample_features": [], "warnings": [],
            "errors": ["connector type is ai_fallback_only; ArcGIS harness not applicable"],
            "connector_config_snapshot": {"access": connector.access,
                                          "fallback_portal": connector.fallback_portal},
        }
    session = _build_session()
    results: List[CheckResult] = []
    errors: List[str] = []

    def _safe(name: str, fn, *args, **kwargs) -> CheckResult:
        """Run a check; convert any unhandled exception into a fail result.
        Without this, a NoneType/KeyError in one check would crash the whole
        connector run instead of being recorded as a single failed check."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — intentional broad catch
            errors.append(f"{name} crashed: {e.__class__.__name__}: {e}")
            return CheckResult(name, "fail",
                               {"crash": f"{e.__class__.__name__}: {e}"})

    def _safe_pair(name: str, fn, *args, **kwargs):
        """Variant for checks that return (CheckResult, payload). On crash
        return a fail CheckResult and an empty payload of the appropriate
        shape (None for schema, [] for features)."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name} crashed: {e.__class__.__name__}: {e}")
            payload: Any = [] if name == "known_good_query" else None
            return CheckResult(name, "fail",
                               {"crash": f"{e.__class__.__name__}: {e}"}), payload

    results.append(_safe("service_alive", check_service_alive, connector, session))
    r2, layer_schema = _safe_pair("layer_schema", check_layer_schema, connector, session)
    results.append(r2)
    results.append(_safe("field_mapping", check_field_mapping, connector, layer_schema))
    r4, features = _safe_pair("known_good_query", check_known_good_query, connector, session)
    results.append(r4)
    results.append(_safe(
        "field_population", check_field_population, features,
        connector.field_mapping, optional_fields=connector.optional_fields,
    ))
    results.append(_safe(
        "owner_data_sanity", check_owner_data_sanity, features,
        connector.owner_field or connector.field_mapping.get("owner_name"),
    ))
    results.append(_safe(
        "address_parsing", check_address_parsing, features, connector.field_mapping,
    ))
    if quick:
        results.append(CheckResult("geometry_validation", "skipped", {"reason": "quick mode"}))
        results.append(_safe("pagination", check_pagination, connector, session))
        results.append(CheckResult("performance_baseline", "skipped", {"reason": "quick mode"}))
    else:
        results.append(_safe(
            "geometry_validation", check_geometry_validation,
            features, connector.expected_bbox,
        ))
        results.append(_safe("pagination", check_pagination, connector, session))
        results.append(_safe(
            "performance_baseline", check_performance_baseline, connector, session,
        ))

    # Redact sample features (max 3 for the report) before any serialization.
    sample = features[:3]
    redacted = [_redact_feature(f, connector.field_mapping) for f in sample]
    sanitized, fs_warnings = _failsafe_check(redacted)
    return _build_report(connector, results, sanitized, fs_warnings, errors)


def _safe_run_checks(connector: Connector, quick: bool) -> Dict[str, Any]:
    """Wrap _run_all_checks so an unexpected crash becomes a failing report
    instead of propagating up and aborting a multi-connector run."""
    try:
        return _run_all_checks(connector, quick=quick)
    except Exception as e:
        return {
            "county": connector.county, "market": connector.market,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "overall_health": "failing",
            "checks": {}, "sample_features": [], "warnings": [],
            "errors": [f"harness crash: {e.__class__.__name__}: {e}"],
            "connector_config_snapshot": {"access": connector.access},
        }


def run_harness_for_county(name: str, quick: bool = False) -> Dict[str, Any]:
    """Integration point #2: before-discovery health check for one county."""
    registry = load_registry()
    if name not in registry:
        # Tolerate "fulton" as alias for "fulton_ga".
        candidates = [k for k in registry if k.startswith(name + "_")]
        if len(candidates) == 1:
            name = candidates[0]
        else:
            raise KeyError(f"connector {name!r} not in registry "
                           f"(known: {sorted(registry)})")
    report = _safe_run_checks(registry[name], quick=quick)
    _write_report(report)
    return report


def run_harness_for_all_counties(quick: bool = False) -> List[Dict[str, Any]]:
    """Integration point #1: agent startup — validate every connector."""
    registry = load_registry()
    out: List[Dict[str, Any]] = []
    for name, connector in registry.items():
        report = _safe_run_checks(connector, quick=quick)
        _write_report(report)
        out.append(report)
    _write_dashboard(out)
    return out


def diagnose_failure(county: str) -> Dict[str, Any]:
    """Integration point #3: alias for run_harness_for_county. The agent calls
    this when a production query against this county failed; a passing harness
    means the failure was transient (retry); a failing harness means the
    connector itself broke (switch to fallback)."""
    return run_harness_for_county(county, quick=False)


# Markets-wide dashboard generator (appendix lines 938-953).
_DASHBOARD_HEADER = (
    "| County | Market | Status | Last Check | Pop Rate | Response Time | Notes |\n"
    "|--------|--------|--------|------------|----------|---------------|-------|\n"
)


def _build_dashboard(reports: List[Dict[str, Any]]) -> str:
    rows = [_DASHBOARD_HEADER.rstrip()]
    for r in reports:
        checks = r.get("checks") or {}
        pop = checks.get("field_population") or {}
        rates = pop.get("rates") or {}
        avg_pop = round(sum(rates.values()) / len(rates), 2) if rates else "—"
        perf = checks.get("performance_baseline") or {}
        rt = perf.get("response_time_ms")
        rt_str = f"{rt}ms" if isinstance(rt, int) else "—"
        notes = "; ".join(r.get("warnings") or []) or "; ".join(r.get("errors") or []) or ""
        rows.append(
            f"| {r['county']} | {r['market']} | {r['overall_health']} | "
            f"{r['timestamp']} | {avg_pop} | {rt_str} | {notes} |"
        )
    return "\n".join(rows) + "\n"


def _write_dashboard(reports: List[Dict[str, Any]],
                     reports_dir: Path = REPORTS_DIR) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    md = _build_dashboard(reports)
    out = reports_dir / "markets_dashboard.md"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(md)
    return out


# ---------------------------------------------------------------------------
# CLI (risk review §6.4 — argparse, traversal-guarded --output).
# ---------------------------------------------------------------------------


def _validate_output_path(path_str: str, repo_root: Path = REPO_ROOT) -> Path:
    """R-05: reject path-traversal and non-.md outputs. Resolves and asserts
    the result is under the repo root."""
    if not path_str.endswith(".md"):
        raise ValueError(f"--output must end in .md (got {path_str!r})")
    if ".." in Path(path_str).parts:
        raise ValueError(f"--output may not contain '..' (got {path_str!r})")
    candidate = (repo_root / path_str).resolve() if not Path(path_str).is_absolute() \
        else Path(path_str).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        raise ValueError(f"--output must be under {repo_root} (got {candidate})") from None
    return candidate


def _emit_markdown_summary(reports: List[Dict[str, Any]], path: Path) -> None:
    """R-11: --output supplements JSON, never replaces it. Writes a Markdown
    summary at PATH; the per-county JSON files are still written."""
    md = _build_dashboard(reports)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Connector Harness Summary\n\n")
        fh.write(md)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="connector_harness",
        description="Connector test harness for the Land Site Selector. "
                    "DOES NOT TOUCH POSTGRES.",
    )
    g = p.add_mutually_exclusive_group(required=False)
    g.add_argument("--all", action="store_true",
                   help="Run every connector in the registry (default).")
    g.add_argument("--county", help="Run a single connector by name (e.g., fulton or fulton_ga).")
    g.add_argument("--market", help="Run all connectors in a given market (e.g., atlanta).")
    p.add_argument("--quick", action="store_true",
                   help="Skip slower checks (geometry_validation, performance_baseline).")
    p.add_argument("--verbose", action="store_true",
                   help="Print raw API response summaries to stderr.")
    p.add_argument("--output",
                   help="Path under repo root (must end in .md) to write a "
                        "human-readable Markdown summary in addition to JSON.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    output_path: Optional[Path] = None
    if args.output:
        try:
            output_path = _validate_output_path(args.output)
        except ValueError as e:
            logger.error("invalid --output: %s", e)
            return EXIT_CONFIG_ERROR

    try:
        if args.county:
            report = run_harness_for_county(args.county, quick=args.quick)
            reports = [report]
        elif args.market:
            registry = load_registry()
            reports = []
            for name, conn in registry.items():
                if conn.market != args.market:
                    continue
                reports.append(_safe_run_checks(conn, quick=args.quick))
                _write_report(reports[-1])
            _write_dashboard(reports)
        else:
            reports = run_harness_for_all_counties(quick=args.quick)
    except KeyError as e:
        logger.error("connector not found: %s", e)
        return EXIT_CONFIG_ERROR
    except FileNotFoundError as e:
        logger.error("required file missing: %s", e)
        return EXIT_CONFIG_ERROR

    if output_path:
        _emit_markdown_summary(reports, output_path)
        logger.info("markdown summary written to %s", output_path)

    # Aggregate exit code: failing > degraded > healthy.
    healths = [r.get("overall_health") for r in reports]
    if any(h == "failing" for h in healths):
        return EXIT_CONNECTOR_FAILING
    if any(h == "degraded" for h in healths):
        return EXIT_CONNECTOR_DEGRADED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
