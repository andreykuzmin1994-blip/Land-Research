"""Live-PostgreSQL check of the per-file / per-parcel transaction boundaries.

The offline suite (tests/test_txn_integrity.py) drives a psycopg-faithful
fake. This script complements it by running the real pipeline entry points
against a real PostGIS service, through the real ``prepare.get_connection``
(autocommit=False, closed without commit), so savepoint-versus-top-level
semantics are PostgreSQL's own:

  (a) ``costar_ingest.run_ingestion_cycle``: a server-side failure in the
      middle file loses only that file, earlier and later files' rows
      persist, and a file is moved to ARCHIVED/ only after its rows are
      committed. Out-of-range and non-finite cells are row-level validation
      errors, never an uncaught exception.
  (b) ``research.run_scoring_cycle`` / ``research._process_parcel``: one
      failing parcel (or feature) drops only itself; a NaN sub-score is
      stored as null and no ``composite_score = 'NaN'`` row is written.
  (c) the strategy memo picks exactly one latest row per parcel with the
      metric's tie-break (scored_at DESC, score_id DESC) and stays unscoped.

Invoked from ``.github/workflows/discovery-fulton.yml`` AFTER
``tests/test_postgis_smoke.py`` (the smoke test asserts a global, unscoped
pipeline count, and applies the schema this script relies on; run
``python prepare.py`` first when running this standalone). Every row
written here carries a per-run token and is deleted in ``finally``; files
go only to temp dirs; no network calls.

Safety (this script writes rows and runs a scoring cycle): DATABASE_URL is
read from the process environment only, never from ``.env``, and must name
a local server (localhost / 127.0.0.1 / ::1 / a unix-socket directory),
re-checked after connecting. Set ``LIVE_TXN_CHECK_ALLOW_NONLOCAL_DB=1`` to
override deliberately. The scoring step also refuses to run if the cycle
would select any parcel this script did not create.

Exit code 0 = pass, 1 = a check failed, 2 = refused to run.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import os
import secrets
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import psycopg
from psycopg import conninfo

REPO_ROOT = Path(__file__).resolve().parent.parent

_OPT_IN_ENV = "LIVE_TXN_CHECK_ALLOW_NONLOCAL_DB"
_LOCAL_HOST_NAMES = frozenset({"localhost"})
_TOKEN_PREFIX = "livetxn"

# All SQL lives in module-level constants with %s placeholders (SR-2 style).
_SQL_INJECTED_FAULT = "SELECT 1/0"
_SQL_MARKET_EXISTS = "SELECT 1 FROM markets WHERE market_id = %s"
_SQL_INSERT_PARCEL = (
    "INSERT INTO parcels (parcel_id, county, state, market, acreage, "
    "assessed_value_total, geometry, centroid) "
    "VALUES (%s, 'fulton', 'GA', %s, %s, %s, ST_GeomFromText(%s, 4326), "
    "ST_Centroid(ST_GeomFromText(%s, 4326)))"
)
_SQL_COUNT_INGESTION_LOG_FOR_FILE = (
    "SELECT COUNT(*) FROM research_log "
    "WHERE cycle_id = %s AND action_type = 'ingestion' AND notes LIKE %s"
)
_SQL_COUNT_MARKET_CONTEXT = (
    "SELECT COUNT(*) FROM market_context WHERE submarket_id = ANY(%s)"
)
_SQL_COUNT_SALES_COMPS = (
    "SELECT COUNT(*) FROM sales_comps WHERE submarket_id = ANY(%s)"
)
_SQL_COUNT_LEASING_COMPS = (
    "SELECT COUNT(*) FROM leasing_comps WHERE submarket_id = ANY(%s)"
)
_SQL_COUNT_LAND_LISTINGS = (
    "SELECT COUNT(*) FROM land_listings WHERE submarket_id = ANY(%s)"
)
_SQL_SCORE_ROWS_PER_PARCEL = (
    "SELECT parcel_id, COUNT(*) FROM parcel_scores "
    "WHERE parcel_id = ANY(%s) GROUP BY parcel_id"
)
_SQL_LATEST_SCORE_DETAIL = (
    "SELECT composite_score, confidence_score, sub_scores->'S10_incentives' "
    "FROM parcel_scores WHERE parcel_id = %s "
    "ORDER BY scored_at DESC, score_id DESC LIMIT 1"
)
_SQL_COUNT_NONFINITE_SCORES = (
    "SELECT COUNT(*) FROM parcel_scores WHERE parcel_id = ANY(%s) "
    "AND (composite_score = 'NaN'::numeric OR confidence_score = 'NaN'::numeric)"
)
_SQL_EXISTING_PARCELS = "SELECT parcel_id FROM parcels WHERE parcel_id = ANY(%s)"
_SQL_DISCOVERY_LOG_PARCELS = (
    "SELECT parcel_id FROM research_log "
    "WHERE cycle_id = %s AND action_type = 'discovery'"
)
_SQL_INSERT_SCORE_NOW = (
    "INSERT INTO parcel_scores (parcel_id, scored_at, composite_score, "
    "confidence_score, actionability, run_tag) "
    "VALUES (%s, NOW(), %s, %s, %s, %s)"
)
_SQL_INSERT_SCORE_EARLIER = (
    "INSERT INTO parcel_scores (parcel_id, scored_at, composite_score, "
    "confidence_score, actionability, run_tag) "
    "VALUES (%s, NOW() - INTERVAL '1 hour', %s, %s, %s, %s)"
)
_SQL_DISTINCT_ON_LATEST = (
    "SELECT DISTINCT ON (parcel_id) parcel_id, confidence_score "
    "FROM parcel_scores WHERE parcel_id = ANY(%s) "
    "ORDER BY parcel_id, scored_at DESC, score_id DESC"
)

# Cleanup, child tables first (FK order).
_SQL_DELETE_FLAGS_FOR_PARCELS = "DELETE FROM flagged_items WHERE parcel_id = ANY(%s)"
_SQL_DELETE_FLAGS_FOR_CYCLE = "DELETE FROM flagged_items WHERE description LIKE %s"
_SQL_DELETE_LOG = (
    "DELETE FROM research_log WHERE cycle_id = ANY(%s) OR parcel_id = ANY(%s)"
)
_SQL_DELETE_SCORES = "DELETE FROM parcel_scores WHERE parcel_id = ANY(%s)"
_SQL_DELETE_PARCELS = "DELETE FROM parcels WHERE parcel_id = ANY(%s)"
_SQL_DELETE_MARKET_CONTEXT = "DELETE FROM market_context WHERE submarket_id = ANY(%s)"
_SQL_DELETE_SALES_COMPS = "DELETE FROM sales_comps WHERE submarket_id = ANY(%s)"
_SQL_DELETE_LEASING_COMPS = "DELETE FROM leasing_comps WHERE submarket_id = ANY(%s)"
_SQL_DELETE_LAND_LISTINGS = "DELETE FROM land_listings WHERE submarket_id = ANY(%s)"
_SQL_DELETE_SUBMARKETS = "DELETE FROM submarkets WHERE submarket_id = ANY(%s)"
_SQL_DELETE_MARKET_IF_UNUSED = (
    "DELETE FROM markets WHERE market_id = %s "
    "AND NOT EXISTS (SELECT 1 FROM submarkets WHERE market_id = %s)"
)

_HEADERS: Mapping[str, tuple[str, ...]] = {
    "submarket_stats": (
        "submarket_name", "market", "total_inventory_sf", "vacancy_rate_pct",
        "availability_rate_pct", "net_absorption_t12_sf",
        "under_construction_sf", "proposed_sf", "asking_rent_nnn_psf",
        "report_date",
    ),
    "land_sales_comps": (
        "address", "parcel_id", "county", "submarket", "acres", "sale_date",
        "sale_price", "price_per_acre", "buyer_name", "seller_name",
        "zoning", "intended_use", "cap_rate",
    ),
    "building_sales_comps": (
        "address", "submarket", "building_sf", "year_built",
        "clear_height_ft", "sale_date", "sale_price", "price_psf",
        "cap_rate", "noi_at_sale", "buyer_name", "seller_name",
        "tenant_at_sale", "lease_term_remaining_years",
    ),
    "leasing_comps": (
        "address", "submarket", "tenant_name", "tenant_industry",
        "lease_start_date", "lease_term_months", "building_sf_leased",
        "starting_rent_psf_nnn", "rent_escalation_pct", "lease_type",
    ),
    "land_listings": (
        "address", "parcel_id", "county", "submarket", "acres", "zoning",
        "topography_notes", "asking_price", "asking_price_per_acre",
        "listing_date", "days_on_market", "listing_broker",
        "listing_broker_firm", "utilities_status", "entitlement_status",
    ),
}

# Same discovery mapping/classification the smoke test uses.
_MAPPING = {
    "parcel_id": "ParcelID", "owner_name": "Owner",
    "owner_mailing_address": "OwnerAddr1",
    "owner_mailing_address_2": "OwnerAddr2",
    "site_address": "Address", "acreage": "LandAcres",
    "land_value": "LandAssess", "improvement_value": "ImprAssess",
    "total_value": "TotAssess", "land_use_code": "LUCode",
    "tax_year": "TaxYear",
}
_CLASSIFICATION = {
    "trust_keywords": ["TRUST", "TRUSTEE", "TR "],
    "estate_keywords": ["ESTATE", "ESTATE OF", "DECD"],
    "llc_keywords": ["LLC", "L L C", "LP", "LTD"],
    "corporate_keywords": ["INC", "CORP", "CO ", "GROUP"],
    "government_keywords": ["COUNTY", "CITY OF", "STATE OF", "UNITED STATES", "BOARD OF"],
}


# ---------------------------------------------------------------------------
# Local-target guard
# ---------------------------------------------------------------------------
class UnsafeTarget(Exception):
    """DATABASE_URL is missing or does not provably name a local server."""


def _is_loopback_address(value: str) -> bool:
    try:
        return ipaddress.ip_address(value.strip()).is_loopback
    except ValueError:
        return False


def _is_local_host(value: str) -> bool:
    host = value.strip()
    if not host:
        return False
    # An absolute path is a unix-socket directory on this machine.
    return host.startswith("/") or host in _LOCAL_HOST_NAMES or _is_loopback_address(host)


def check_dsn_is_local(
    dsn: str | None, environ: Mapping[str, str],
) -> dict[str, Any]:
    """Return the parsed conninfo for a local target, else raise UnsafeTarget.

    ``dsn`` must come from the process environment: this never falls back to
    ``.env`` (prepare's DSN lookup does, which is why the guard runs first).
    libpq fills parameters the DSN leaves out from PG* environment variables
    and service files, so ``service`` / ``PGSERVICE`` and a non-loopback
    ``hostaddr`` / ``PGHOSTADDR`` are refused too.
    """
    if not dsn:
        raise UnsafeTarget(
            "DATABASE_URL is not set in the process environment "
            "(this check never reads .env)"
        )
    try:
        parts = conninfo.conninfo_to_dict(dsn)
    except psycopg.Error as exc:
        raise UnsafeTarget(f"DATABASE_URL does not parse ({type(exc).__name__})") from None
    if environ.get(_OPT_IN_ENV) == "1":
        return parts
    if parts.get("service") or environ.get("PGSERVICE"):
        raise UnsafeTarget("service= / PGSERVICE can redirect the host; refused")
    hosts = str(parts.get("host") or "")
    if not hosts:
        raise UnsafeTarget("DATABASE_URL names no explicit host; refused")
    for host in hosts.split(","):
        if not _is_local_host(host):
            raise UnsafeTarget(f"host {host.strip()!r} is not local; refused")
    hostaddr = str(parts.get("hostaddr") or environ.get("PGHOSTADDR") or "")
    for addr in filter(None, (a.strip() for a in hostaddr.split(","))):
        if not _is_loopback_address(addr):
            raise UnsafeTarget(f"hostaddr {addr!r} is not loopback; refused")
    return parts


def check_connection_is_local(conn: Any, environ: Mapping[str, str]) -> None:
    """Re-check the server the connection actually reached (post-resolution)."""
    if environ.get(_OPT_IN_ENV) == "1":
        return
    host = str(conn.info.host or "")
    if host.startswith("/"):
        return
    if not _is_loopback_address(str(conn.info.hostaddr or "")):
        raise UnsafeTarget(
            f"connected server address {conn.info.hostaddr!r} is not loopback; refused"
        )


def describe_target(parts: Mapping[str, Any]) -> str:
    """Printable target summary: never includes the user or password."""
    return (
        f"host={parts.get('host', '')} port={parts.get('port', '')} "
        f"dbname={parts.get('dbname', '')}"
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
class _Results:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        if ok:
            print(f"OK: {label}")
        else:
            print(f"FAIL: {label}" + (f" -- {detail}" if detail else ""))
            self.failures.append(label)
        return ok


@contextmanager
def _patched(module: Any, name: str, replacement: Any) -> Iterator[None]:
    """Temporarily rebind ``module.name``; always restored."""
    original = getattr(module, name)
    setattr(module, name, replacement)
    try:
        yield
    finally:
        setattr(module, name, original)


def _scalar(dsn: str, sql: str, params: tuple) -> Any:
    """One autocommit read from a SEPARATE connection: sees committed rows only."""
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _rows(dsn: str, sql: str, params: tuple) -> list[tuple]:
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        return list(conn.execute(sql, params).fetchall())


def _write_csv(base: Path, export_type: str, name: str, rows: list[dict[str, str]]) -> Path:
    target_dir = base / export_type
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / name
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        header = _HEADERS[export_type]
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(col, "") for col in header])
    return path


def _square_wkt(lng: float, lat: float, half: float = 0.001) -> str:
    x0, x1, y0, y1 = lng - half, lng + half, lat - half, lat + half
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def _feature(raw_id: str, lng: float, lat: float) -> dict[str, Any]:
    half = 0.001
    ring = [
        [lng - half, lat - half], [lng + half, lat - half],
        [lng + half, lat + half], [lng - half, lat + half],
        [lng - half, lat - half],
    ]
    return {
        "attributes": {
            "ParcelID": raw_id, "Owner": "LIVE CHECK HOLDINGS LLC",
            "OwnerAddr1": "1 TEST WAY", "OwnerAddr2": "ATLANTA GA 30301",
            "Address": f"{raw_id} TEST RD", "LandAcres": 14.7,
            "LandAssess": 185000, "ImprAssess": 0, "TotAssess": 185000,
            "LUCode": "100", "TaxYear": 2025,
        },
        "geometry": {"rings": [ring]},
    }


class _Created:
    """Everything this run writes, for scoped assertions and cleanup."""

    def __init__(self) -> None:
        self.parcel_ids: list[str] = []
        self.submarket_ids: list[str] = []
        self.market_ids_created: list[str] = []
        self.cycle_ids: list[str] = []


# ---------------------------------------------------------------------------
# (a) CoStar ingestion
# ---------------------------------------------------------------------------
@contextmanager
def _costar_base(costar_ingest: Any, research: Any, base: Path) -> Iterator[None]:
    """Point every module binding of _COSTAR_BASE_DIR at ``base`` (SR-7)."""
    modules = [m for m in (costar_ingest, research) if hasattr(m, "_COSTAR_BASE_DIR")]
    originals = [m._COSTAR_BASE_DIR for m in modules]
    for m in modules:
        m._COSTAR_BASE_DIR = base
    try:
        yield
    finally:
        for m, original in zip(modules, originals):
            m._COSTAR_BASE_DIR = original


def _run_ingestion(costar_ingest: Any, created: _Created, archive_hook=None) -> dict[str, Any]:
    """run_ingestion_cycle with its cycle id recorded (and an optional archive probe)."""
    original_make_id = costar_ingest._make_ingestion_cycle_id

    def _recording_make_id() -> str:
        cycle_id = original_make_id()
        created.cycle_ids.append(cycle_id)
        return cycle_id

    with _patched(costar_ingest, "_make_ingestion_cycle_id", _recording_make_id):
        if archive_hook is None:
            return costar_ingest.run_ingestion_cycle()
        with _patched(costar_ingest, "_archive_file", archive_hook):
            return costar_ingest.run_ingestion_cycle()


def _per_file(summary: Mapping[str, Any], export_type: str) -> list[dict[str, Any]]:
    return list(summary["per_export_type"][export_type]["per_file"])


def check_ingestion_isolation(
    res: _Results, created: _Created, dsn: str, token: str,
    costar_ingest: Any, research: Any,
) -> None:
    """(a) stats OK, land_sales fails on the server, leasing OK."""
    slug = costar_ingest._slugify
    market = f"LiveTxn {token}"
    stats_subs = [f"LiveTxn {token} Stats A", f"LiveTxn {token} Stats B"]
    land_sub = f"LiveTxn {token} Land"
    lease_sub = f"LiveTxn {token} Lease"
    stats_ids = [f"{slug(market)}__{slug(s)}" for s in stats_subs]
    land_id = f"atlanta__{slug(land_sub)}"
    lease_id = f"atlanta__{slug(lease_sub)}"
    created.submarket_ids += [*stats_ids, land_id, lease_id]

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        stats = _write_csv(base, "submarket_stats", "submarket_stats_20260105.csv", [
            {"submarket_name": s, "market": market, "total_inventory_sf": "1000000",
             "vacancy_rate_pct": "5.0", "availability_rate_pct": "6.0",
             "net_absorption_t12_sf": "250000", "under_construction_sf": "0",
             "proposed_sf": "0", "asking_rent_nnn_psf": "7.50",
             "report_date": "2026-01-05"}
            for s in stats_subs
        ])
        # A NUL in a text column that only reaches the `raw` JSONB payload:
        # json.dumps writes it as \u0000, which PostgreSQL rejects on the
        # SERVER (UntranslatableCharacter) inside the file's transaction.
        land = _write_csv(base, "land_sales_comps", "land_sales_comps_202601.csv", [
            {"address": f"{token} Fault Rd", "county": "Fulton", "submarket": land_sub,
             "acres": "12.5", "sale_date": "2026-01-10", "sale_price": "1875000",
             "price_per_acre": "150000", "zoning": "M-2",
             "intended_use": "distribution\x00"},
        ])
        lease = _write_csv(base, "leasing_comps", "leasing_comps_202601.csv", [
            {"address": f"{token} Lease Dr", "submarket": lease_sub,
             "tenant_name": "Live Check Tenant", "tenant_industry": "3PL",
             "lease_start_date": "2026-01-15", "lease_term_months": "84",
             "building_sf_leased": "250000", "starting_rent_psf_nnn": "7.95",
             "rent_escalation_pct": "3.0", "lease_type": "NNN"},
        ])

        durable_at_archive: dict[str, int] = {}
        original_archive = costar_ingest._archive_file

        def _probing_archive(source: Path) -> Path:
            # Count this file's ingestion log row from ANOTHER connection at
            # the moment of the move: nonzero only if it is already committed.
            durable_at_archive[source.name] = int(_scalar(
                dsn, _SQL_COUNT_INGESTION_LOG_FOR_FILE,
                (created.cycle_ids[-1], f"%file={source.name} %"),
            ) or 0)
            return original_archive(source)

        with _costar_base(costar_ingest, research, base):
            summary = _run_ingestion(costar_ingest, created, _probing_archive)

        statuses = {
            "submarket_stats": [f["status"] for f in _per_file(summary, "submarket_stats")],
            "land_sales_comps": [f["status"] for f in _per_file(summary, "land_sales_comps")],
            "leasing_comps": [f["status"] for f in _per_file(summary, "leasing_comps")],
        }
        res.check(
            statuses == {"submarket_stats": ["loaded"], "land_sales_comps": ["failed"],
                         "leasing_comps": ["loaded"]},
            "(a) per-file statuses: stats loaded, land_sales failed, leasing loaded",
            repr(statuses),
        )
        land_error = (_per_file(summary, "land_sales_comps") or [{}])[0].get("error", "")
        res.check("Unicode escape" in land_error,
                  "(a) the middle file failed on the server (jsonb \\u0000)",
                  land_error.splitlines()[0] if land_error else "no error recorded")

        n_stats = _scalar(dsn, _SQL_COUNT_MARKET_CONTEXT, (stats_ids,))
        n_land = _scalar(dsn, _SQL_COUNT_SALES_COMPS, ([land_id],))
        n_lease = _scalar(dsn, _SQL_COUNT_LEASING_COMPS, ([lease_id],))
        res.check(n_stats == 2, "(a) EARLIER file's rows persist (market_context)",
                  f"market_context rows={n_stats}, expected 2")
        res.check(n_lease == 1, "(a) LATER file's rows persist (leasing_comps)",
                  f"leasing_comps rows={n_lease}, expected 1")
        res.check(n_land == 0, "(a) failing file left no rows (sales_comps)",
                  f"sales_comps rows={n_land}, expected 0")

        res.check(
            durable_at_archive == {stats.name: 1, lease.name: 1},
            "(a) each file was archived only after its rows were committed",
            f"committed log rows visible at archive time: {durable_at_archive}",
        )
        archived = sorted(p.name for p in (base / "ARCHIVED").rglob("*.csv"))
        failed_csv = sorted(p.name for p in (base / "FAILED").rglob("*.csv"))
        failed_err = sorted(p.name for p in (base / "FAILED").rglob("*.error.json"))
        intake_left = [p for p in (stats, land, lease) if p.exists()]
        res.check(
            len(archived) == 2 and len(failed_csv) == 1 and len(failed_err) == 1
            and failed_csv[0].startswith("land_sales_comps_202601") and not intake_left,
            "(a) ARCHIVED/ has the two good files; FAILED/ has the bad one + .error.json",
            f"archived={archived} failed={failed_csv} errors={failed_err} intake={intake_left}",
        )


def check_ingestion_validation(
    res: _Results, created: _Created, dsn: str, token: str,
    costar_ingest: Any, research: Any,
) -> None:
    """(a) overflow / int4 / non-finite cells are row errors, never a crash."""
    slug = costar_ingest._slugify
    market = f"LiveTxn {token}"
    sub = f"LiveTxn {token} Valid"
    stats_id = f"{slug(market)}__{slug(sub)}"
    comps_id = f"atlanta__{slug(sub)}"
    created.submarket_ids += [stats_id, comps_id]

    def stats_row(report_date: str, **over: str) -> dict[str, str]:
        row = {"submarket_name": sub, "market": market, "total_inventory_sf": "1000000",
               "vacancy_rate_pct": "5.0", "availability_rate_pct": "6.0",
               "net_absorption_t12_sf": "250000", "under_construction_sf": "0",
               "proposed_sf": "0", "asking_rent_nnn_psf": "7.50",
               "report_date": report_date}
        row.update(over)
        return row

    land = {"address": f"{token} Land", "county": "Fulton", "submarket": sub, "acres": "10",
            "sale_date": "2026-02-01", "sale_price": "1500000", "price_per_acre": "150000"}
    bldg = {"address": f"{token} Bldg", "submarket": sub, "building_sf": "100000",
            "sale_date": "2026-02-01", "sale_price": "9000000", "price_psf": "90"}
    lease = {"address": f"{token} Lease", "submarket": sub, "tenant_name": "T",
             "lease_start_date": "2026-02-01", "lease_term_months": "60",
             "building_sf_leased": "50000", "starting_rent_psf_nnn": "7.0"}
    listing = {"address": f"{token} Listing", "county": "Fulton", "submarket": sub,
               "acres": "20", "asking_price": "3000000", "listing_date": "2026-02-01",
               "days_on_market": "30"}

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        # The original repro: file 2 carries a bigint-overflow absorption.
        _write_csv(base, "submarket_stats", "submarket_stats_20260112.csv", [
            stats_row("2026-01-12"),
        ])
        _write_csv(base, "submarket_stats", "submarket_stats_20260119.csv", [
            stats_row("2026-01-19"),
            stats_row("2026-01-18", net_absorption_t12_sf="99999999999999999999"),
            stats_row("2026-01-17", under_construction_sf="nan"),
            stats_row("2026-01-16", asking_rent_nnn_psf="inf"),
            stats_row("2026-01-15", net_absorption_t12_sf="-9223372036854775807"),
        ])
        _write_csv(base, "land_sales_comps", "land_sales_comps_202602.csv", [
            land, {**land, "address": f"{token} L2", "sale_price": "1e19"},
            {**land, "address": f"{token} L3", "acres": "NaN"},
        ])
        _write_csv(base, "building_sales_comps", "building_sales_comps_202602.csv", [
            bldg, {**bldg, "address": f"{token} B2", "sale_price": "inf"},
        ])
        _write_csv(base, "leasing_comps", "leasing_comps_202602.csv", [
            lease, {**lease, "address": f"{token} E2", "lease_term_months": "3000000000"},
            {**lease, "address": f"{token} E3", "starting_rent_psf_nnn": "-inf"},
        ])
        _write_csv(base, "land_listings", "land_listings_20260216.csv", [
            listing, {**listing, "address": f"{token} S2", "days_on_market": "3000000000"},
            {**listing, "address": f"{token} S3", "asking_price": "1e400"},
        ])

        try:
            with _costar_base(costar_ingest, research, base):
                summary = _run_ingestion(costar_ingest, created)
        except Exception as exc:  # noqa: BLE001 — a crash IS the failure being checked
            res.check(False, "(a) validation cycle completes without an uncaught exception",
                      f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
            return
        res.check(True, "(a) validation cycle completes without an uncaught exception")

        expected = {
            "submarket_stats": [("loaded", 0), ("loaded", 3)],
            "land_sales_comps": [("loaded", 2)],
            "building_sales_comps": [("loaded", 1)],
            "leasing_comps": [("loaded", 2)],
            "land_listings": [("loaded", 2)],
        }
        got = {
            et: [(f["status"], f.get("rows_failed")) for f in _per_file(summary, et)]
            for et in expected
        }
        res.check(got == expected,
                  "(a) overflow/int4/non-finite cells are row-level errors; files still load",
                  f"(status, rows_failed) per file: {got}")
        counts = (
            _scalar(dsn, _SQL_COUNT_MARKET_CONTEXT, ([stats_id],)),
            _scalar(dsn, _SQL_COUNT_SALES_COMPS, ([comps_id],)),
            _scalar(dsn, _SQL_COUNT_LEASING_COMPS, ([comps_id],)),
            _scalar(dsn, _SQL_COUNT_LAND_LISTINGS, ([comps_id],)),
        )
        # stats: 1 (file 1) + 2 (file 2: plain + int8-min absorption);
        # sales_comps: 1 land + 1 building; leasing 1; listings 1.
        res.check(counts == (3, 2, 1, 1),
                  "(a) the valid rows of every file persisted (incl. both stats files)",
                  f"(market_context, sales_comps, leasing_comps, land_listings)={counts}")


# ---------------------------------------------------------------------------
# (b) scoring and discovery
# ---------------------------------------------------------------------------
def _insert_parcels(dsn: str, created: _Created, market: str,
                    parcels: list[tuple[str, float, float]]) -> None:
    with psycopg.connect(dsn, autocommit=False, connect_timeout=10) as conn:
        for pid, lng, lat in parcels:
            wkt = _square_wkt(lng, lat)
            conn.execute(_SQL_INSERT_PARCEL, (pid, market, 12.0, 185000, wkt, wkt))
            created.parcel_ids.append(pid)
        conn.commit()


def _scoring_selection_is_ours(dsn: str, research: Any, created: _Created) -> list[str]:
    """Parcels the unscoped scoring cycle would select that we did not create."""
    selected = [r[0] for r in _rows(dsn, research._SQL_LIST_PARCELS_FOR_SCORING, ("atlanta",))]
    return [pid for pid in selected if pid not in set(created.parcel_ids)]


def _score_counts(dsn: str, pids: list[str]) -> dict[str, int]:
    found = dict(_rows(dsn, _SQL_SCORE_ROWS_PER_PARCEL, (pids,)))
    return {pid: int(found.get(pid, 0)) for pid in pids}


def check_scoring_isolation(
    res: _Results, created: _Created, dsn: str, token: str, research: Any,
) -> None:
    """(b) a server-side failure in parcel 2's write block drops only parcel 2."""
    pids = [f"{_TOKEN_PREFIX}-{token}-s{i}" for i in (1, 2, 3)]
    _insert_parcels(dsn, created, "atlanta",
                    [(pid, -84.554 + i * 0.003, 33.554) for i, pid in enumerate(pids)])
    foreign = _scoring_selection_is_ours(dsn, research, created)
    if foreign:
        res.check(False, "(b) scoring selection contains only this check's parcels",
                  f"refusing to score {len(foreign)} parcel(s) this check did not create")
        return

    original_flag = research._flag

    def _faulting_flag(conn, cycle_id, parcel_id, *args, **kwargs):
        if parcel_id == pids[1]:
            with conn.cursor() as cur:
                cur.execute(_SQL_INJECTED_FAULT)  # server-side DivisionByZero
        return original_flag(conn, cycle_id, parcel_id, *args, **kwargs)

    with _patched(research, "_flag", _faulting_flag):
        summary = research.run_scoring_cycle("atlanta")
    created.cycle_ids.append(summary["cycle_id"])
    by_pid = {p["parcel_id"]: p["status"] for p in summary["parcels"]}
    statuses = {pid: by_pid.get(pid) for pid in pids}
    res.check(statuses == {pids[0]: "scored", pids[1]: "error", pids[2]: "scored"},
              "(b) scoring statuses: scored / error / scored", repr(statuses))
    counts = _score_counts(dsn, pids)
    res.check(counts == {pids[0]: 1, pids[1]: 0, pids[2]: 1},
              "(b) the failing parcel drops only itself (earlier + later rows persist)",
              f"parcel_scores rows per parcel: {counts}")


def check_scoring_nan(
    res: _Results, created: _Created, dsn: str, token: str, research: Any,
) -> None:
    """(b) a NaN sub-score is stored as null; nothing non-finite is written."""
    pids = [f"{_TOKEN_PREFIX}-{token}-n{i}" for i in (1, 2, 3)]
    nan_lng = -84.5401
    _insert_parcels(dsn, created, "atlanta", [
        (pids[0], -84.545, 33.556), (pids[1], nan_lng, 33.556), (pids[2], -84.535, 33.556),
    ])
    foreign = _scoring_selection_is_ours(dsn, research, created)
    if foreign:
        res.check(False, "(b) scoring selection contains only this check's parcels",
                  f"refusing to score {len(foreign)} parcel(s) this check did not create")
        return

    original_s10 = research._compute_s10

    def _nan_s10(lng, lat):
        if lng is not None and abs(lng - nan_lng) < 1e-6:
            return float("nan")
        return original_s10(lng, lat)

    with _patched(research, "_compute_s10", _nan_s10):
        summary = research.run_scoring_cycle("atlanta")
    created.cycle_ids.append(summary["cycle_id"])
    # The cycle may also re-select an earlier check's unscored parcel; only
    # this check's parcels are asserted on.
    by_pid = {p["parcel_id"]: p["status"] for p in summary["parcels"]}
    statuses = {pid: by_pid.get(pid) for pid in pids}
    res.check(statuses == {pid: "scored" for pid in pids},
              "(b) NaN sub-score parcel is scored, not an error", repr(statuses))
    counts = _score_counts(dsn, pids)
    res.check(counts == {pid: 1 for pid in pids},
              "(b) every parcel of the NaN cycle persisted", f"rows per parcel: {counts}")
    detail = _rows(dsn, _SQL_LATEST_SCORE_DETAIL, (pids[1],))
    s10_null = bool(detail) and detail[0][2] is None
    res.check(s10_null, "(b) the NaN sub-score was stored as JSON null",
              f"latest row (composite, confidence, S10)={detail}")
    n_nonfinite = _scalar(dsn, _SQL_COUNT_NONFINITE_SCORES, (pids,))
    res.check(n_nonfinite == 0, "(b) no composite/confidence = 'NaN' row was written",
              f"NaN rows={n_nonfinite}")


def check_discovery_isolation(
    res: _Results, created: _Created, dsn: str, token: str,
    prepare: Any, research: Any,
) -> None:
    """(b) a server-side failure in feature 2's write block drops only feature 2."""
    raw_ids = [f"LIVETXN-{token}-D{i}" for i in (1, 2, 3)]
    pids = [f"fulton-{rid}" for rid in raw_ids]
    created.parcel_ids += pids
    features = [_feature(rid, -84.560 + i * 0.004, 33.545) for i, rid in enumerate(raw_ids)]
    params = {"hard_filters": {"acreage_min": 5, "acreage_max": 50},
              "owner_classification": _CLASSIFICATION}
    cycle_id = research._make_cycle_id("fulton")
    created.cycle_ids.append(cycle_id)

    original_upsert = research._upsert_parcel

    def _faulting_upsert(conn, row, wkt):
        if row["parcel_id"] == pids[1]:
            with conn.cursor() as cur:
                cur.execute(_SQL_INJECTED_FAULT)  # server-side DivisionByZero
        return original_upsert(conn, row, wkt)

    with tempfile.TemporaryDirectory() as td, \
            _patched(research, "_upsert_parcel", _faulting_upsert):
        # Same shape as run_discovery_cycle: collision SELECT (opens the
        # cycle's outer transaction), per-parcel blocks, one final commit.
        with prepare.get_connection() as conn:
            research._count_log_rows(conn, cycle_id)
            statuses = [
                research._process_parcel(
                    feat, conn, cycle_id, "south_fulton_campbellton", "atlanta",
                    _MAPPING, _CLASSIFICATION, params,
                    raw_response_path=str(Path(td) / "cache.json"),
                )
                for feat in features
            ]
            conn.commit()
    res.check(statuses == ["discovery", "skip_unmappable", "discovery"],
              "(b) discovery statuses: discovery / skip_unmappable / discovery",
              repr(statuses))
    present = sorted(r[0] for r in _rows(dsn, _SQL_EXISTING_PARCELS, (pids,)))
    logged = sorted(r[0] for r in _rows(dsn, _SQL_DISCOVERY_LOG_PARCELS, (cycle_id,)))
    res.check(present == [pids[0], pids[2]] and logged == [pids[0], pids[2]],
              "(b) the failing feature drops only itself (earlier + later parcels persist)",
              f"parcels={present} discovery_log={logged}")


# ---------------------------------------------------------------------------
# (c) strategy memo
# ---------------------------------------------------------------------------
def check_memo_latest_row(
    res: _Results, created: _Created, dsn: str, token: str,
    prepare: Any, reporting: Any,
) -> None:
    """(c) one latest row per parcel, metric tie-break, unscoped."""
    market = f"{_TOKEN_PREFIX}-{token}-memo"
    pids = [f"{_TOKEN_PREFIX}-{token}-m{i}" for i in (1, 2, 3)]
    _insert_parcels(dsn, created, market,
                    [(pid, -84.50 + i * 0.003, 33.70) for i, pid in enumerate(pids)])
    passing = float(prepare.get_parameters()["composite_threshold"]) + 5
    with psycopg.connect(dsn, autocommit=False, connect_timeout=10) as conn:
        # One transaction => identical now() => tied scored_at; the higher
        # score_id (second insert) is the row the metric counts.
        for pid, conf_a, conf_b in ((pids[0], 0.3, 0.7), (pids[1], 0.2, 0.8)):
            conn.execute(_SQL_INSERT_SCORE_NOW, (pid, passing, conf_a, "PASS", None))
            conn.execute(_SQL_INSERT_SCORE_NOW, (pid, passing, conf_b, "PASS", None))
        # Rows from two other runs: the memo is deliberately unscoped.
        conn.execute(_SQL_INSERT_SCORE_EARLIER,
                     (pids[2], passing, 0.1, "FAIL:strategy", f"{_TOKEN_PREFIX}-run-a"))
        conn.execute(_SQL_INSERT_SCORE_NOW,
                     (pids[2], passing, 0.6, "PASS", f"{_TOKEN_PREFIX}-run-b"))
        conn.commit()

    memo_rows = _rows(dsn, reporting._SQL_FETCH_SCORED_PARCELS_FOR_MEMO, (market,))
    memo_pick = sorted((r[0], float(r[8])) for r in memo_rows)
    metric_pick = sorted(
        (pid, float(conf)) for pid, conf in _rows(dsn, _SQL_DISTINCT_ON_LATEST, (pids,))
    )
    res.check(memo_pick == metric_pick and len(memo_rows) == 3,
              "(c) memo selects exactly the metric's latest row per parcel (unscoped)",
              f"memo rows={memo_pick} metric DISTINCT ON={metric_pick}")
    with tempfile.TemporaryDirectory() as td:
        path = reporting.generate_strategy_memo(market, output_dir=Path(td))
        text = path.read_text(encoding="utf-8")
    line = f"- Total scored parcels in {market}: **3**"
    res.check(line in text, "(c) rendered memo counts each parcel once",
              next((ln for ln in text.splitlines() if "Total scored parcels" in ln), ""))


# ---------------------------------------------------------------------------
# Cleanup + driver
# ---------------------------------------------------------------------------
def _cleanup(dsn: str, created: _Created) -> None:
    with psycopg.connect(dsn, autocommit=False, connect_timeout=10) as conn:
        conn.execute(_SQL_DELETE_FLAGS_FOR_PARCELS, (created.parcel_ids,))
        for cycle_id in created.cycle_ids:
            conn.execute(_SQL_DELETE_FLAGS_FOR_CYCLE, (f"cycle={cycle_id};%",))
        conn.execute(_SQL_DELETE_LOG, (created.cycle_ids, created.parcel_ids))
        conn.execute(_SQL_DELETE_SCORES, (created.parcel_ids,))
        conn.execute(_SQL_DELETE_PARCELS, (created.parcel_ids,))
        for sql in (_SQL_DELETE_MARKET_CONTEXT, _SQL_DELETE_SALES_COMPS,
                    _SQL_DELETE_LEASING_COMPS, _SQL_DELETE_LAND_LISTINGS,
                    _SQL_DELETE_SUBMARKETS):
            conn.execute(sql, (created.submarket_ids,))
        for market_id in created.market_ids_created:
            conn.execute(_SQL_DELETE_MARKET_IF_UNUSED, (market_id, market_id))
        conn.commit()


def main(environ: Mapping[str, str] = os.environ) -> int:
    dsn = environ.get("DATABASE_URL")
    try:
        parts = check_dsn_is_local(dsn, environ)
        if os.environ.get("DATABASE_URL") != dsn:
            raise UnsafeTarget(
                "the pipeline reads DATABASE_URL from os.environ, which "
                "differs from the checked DSN"
            )
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
            check_connection_is_local(conn, environ)
    except UnsafeTarget as exc:
        print(f"REFUSED: {exc}")
        return 2
    if environ.get(_OPT_IN_ENV) == "1":
        print(f"WARNING: {_OPT_IN_ENV}=1 -- local-target guard bypassed")
    print(f"target: {describe_target(parts)}")

    # Imported only after the guard: nothing below may resolve a DSN first.
    sys.path.insert(0, str(REPO_ROOT))
    import costar_ingest  # noqa: E402
    import prepare  # noqa: E402
    import reporting  # noqa: E402
    import research  # noqa: E402

    # prepare's DSN lookup loads .env (never overriding DATABASE_URL, but it
    # can add PG* variables libpq honours). Load it now, then re-check the
    # environment and the server reached through prepare.get_connection --
    # the exact path every pipeline entry point below uses.
    prepare.dsn_available()
    try:
        check_dsn_is_local(os.environ.get("DATABASE_URL"), os.environ)
        with prepare.get_connection() as conn:
            check_connection_is_local(conn, os.environ)
    except UnsafeTarget as exc:
        print(f"REFUSED: {exc}")
        return 2

    if prepare.current_run_tag() is not None:
        print("REFUSED: an autoresearch/<tag> branch is checked out; scoring "
              "here would stamp rows into that run")
        return 2

    res = _Results()
    created = _Created()
    token = secrets.token_hex(3)
    print(f"run token: {token}")
    created.market_ids_created += [
        m for m in (costar_ingest._slugify(f"LiveTxn {token}"), "atlanta")
        if not _scalar(dsn, _SQL_MARKET_EXISTS, (m,))
    ]

    checks: list[tuple[str, Callable[[], None]]] = [
        ("(a) ingestion isolation", lambda: check_ingestion_isolation(
            res, created, dsn, token, costar_ingest, research)),
        ("(a) ingestion validation", lambda: check_ingestion_validation(
            res, created, dsn, token, costar_ingest, research)),
        ("(b) scoring isolation", lambda: check_scoring_isolation(
            res, created, dsn, token, research)),
        ("(b) scoring NaN", lambda: check_scoring_nan(
            res, created, dsn, token, research)),
        ("(b) discovery isolation", lambda: check_discovery_isolation(
            res, created, dsn, token, prepare, research)),
        ("(c) memo latest row", lambda: check_memo_latest_row(
            res, created, dsn, token, prepare, reporting)),
    ]
    try:
        for label, run in checks:
            try:
                run()
            except Exception as exc:  # noqa: BLE001 — report and keep checking
                res.check(False, f"{label} raised",
                          f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
    finally:
        _cleanup(dsn, created)
        print("OK: cleaned up this run's rows")

    if res.failures:
        print(f"FAIL: {len(res.failures)} live transaction-integrity check(s) failed")
        return 1
    print("OK: live transaction-integrity check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
