"""costar_ingest.py — CoStar export ingestion pipeline (Phase 6 / 6.1).

Extracted verbatim from research.py as part of the sandbox split (see
reviews/14_streamlining_review/00_streamlining_review.md Finding B). This
module implements COSTAR_INGESTION_CONTRACT.md: folder scan, per-export-type
CSV schema validation, idempotent DELETE-then-INSERT loads, archive/fail
file movement, and ingestion logging.

Mutability: IMMUTABLE DURING A RUN. The ingestion behavior is frozen by
COSTAR_INGESTION_CONTRACT.md; the agent does not modify this module inside
an autoresearch run. Changes are between-runs work under the tiered review
process. The original three-agent review history is in
reviews/08_phase6_costar_ingestion/ and reviews/09_phase6_1_costar_loaders/.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import prepare
from pipeline_common import (
    _COSTAR_BASE_DIR,
    _SQL_FETCH_SUBMARKET_NAME,
    _flag,
)

log = logging.getLogger("research")


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

        # exp: see run_discovery_cycle for the full rationale. Commit the
        # outer transaction explicitly. The individual loaders do their own
        # `with conn.transaction()` blocks, but those become SAVEPOINTs once
        # the cycle_id-collision SELECT has started the outer transaction.
        conn.commit()

    return summary
