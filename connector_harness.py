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
        )

    # Validate overlay keys exist in sources.json (risk review 9.j).
    for key in overlay:
        if key.startswith("_"):
            continue
        if key not in parcel_block:
            logger.warning(
                "connector_registry.json key %r has no matching entry in sources.json", key
            )

    return out
