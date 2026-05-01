"""Live-PostGIS smoke test for the Phase 3 Fulton discovery connector.

Phase 3.1 §6.B / §10.5: the offline test suite uses ``FakeConnection`` and
never sends real SQL to a database. This script complements that by running
a single happy-path UPSERT against a live PostGIS service so the
``_SQL_UPSERT_PARCEL`` statement, the ``ST_GeomFromText`` / ``ST_Centroid``
calls, the ``ON CONFLICT`` clause, and the ``GEOMETRY(Polygon, 4326)``
column type are actually exercised together.

Invoked from ``.github/workflows/discovery-fulton.yml`` against a postgres
service container with PostGIS installed.

Exit code 0 = pass. Non-zero = fail, with a printed diagnostic.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

# Ensure repo root on sys.path so ``research`` and ``prepare`` import.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import prepare  # noqa: E402
import research  # noqa: E402


def _fail(msg: str) -> "psycopg.Connection | None":
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        _fail("DATABASE_URL not set; cannot run live-PostGIS smoke test")
        return 1

    # Step 1: schema apply.
    with psycopg.connect(dsn, autocommit=False, connect_timeout=10) as conn:
        prepare.apply_schema(conn)
        print("OK: schema applied (idempotent)")

    # Step 2: run one parcel through _process_parcel against the live DB.
    fixture_path = REPO_ROOT / "tests" / "fixtures" / "discovery" / "arcgis_query_two_features.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    feature = fixture["features"][0]

    mapping = {
        "parcel_id": "ParcelID", "owner_name": "Owner",
        "owner_mailing_address": "OwnerAddr1",
        "owner_mailing_address_2": "OwnerAddr2",
        "site_address": "Address", "acreage": "LandAcres",
        "land_value": "LandAssess", "improvement_value": "ImprAssess",
        "total_value": "TotAssess", "land_use_code": "LUCode",
        "tax_year": "TaxYear",
    }
    classification = {
        "trust_keywords": ["TRUST", "TRUSTEE", "TR "],
        "estate_keywords": ["ESTATE", "ESTATE OF", "DECD"],
        "llc_keywords": ["LLC", "L L C", "LP", "LTD"],
        "corporate_keywords": ["INC", "CORP", "CO ", "GROUP"],
        "government_keywords": ["COUNTY", "CITY OF", "STATE OF", "UNITED STATES", "BOARD OF"],
    }
    params = {
        "hard_filters": {"acreage_min": 5, "acreage_max": 50},
        "owner_classification": classification,
    }
    cycle_id = research._make_cycle_id("fulton")

    with psycopg.connect(dsn, autocommit=False, connect_timeout=10) as conn:
        status = research._process_parcel(
            feature, conn, cycle_id, "south_fulton_campbellton", "atlanta",
            mapping, classification, params,
            raw_response_path="/tmp/cache/smoke.json",
        )
        if status != "discovery":
            _fail(f"_process_parcel returned {status!r}; expected 'discovery'")

        # Step 3: assert geometry validity + centroid containment.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT parcel_id, ST_IsValid(geometry), "
                "ST_Within(centroid, geometry), ST_SRID(geometry) "
                "FROM parcels WHERE parcel_id LIKE %s",
                ("fulton-%",),
            )
            rows = cur.fetchall()
        if not rows:
            _fail("no parcel was inserted into parcels table")
        for parcel_id, is_valid, centroid_within, srid in rows:
            if not is_valid:
                _fail(f"ST_IsValid(geometry)=false for {parcel_id}")
            if not centroid_within:
                _fail(f"ST_Within(centroid, geometry)=false for {parcel_id}")
            if srid != 4326:
                _fail(f"ST_SRID(geometry)={srid} for {parcel_id}; expected 4326")
        print(f"OK: {len(rows)} parcel(s) passed ST_IsValid + ST_Within + SRID checks")

        # Step 4: assert at least one cycle-level flag row was emitted with
        # parcel_id NULL (currently none — happy path doesn't trigger the
        # cycle-level flag — so just assert no "(none)" sentinels exist).
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM flagged_items WHERE parcel_id = %s",
                ("(none)",),
            )
            (sentinel_count,) = cur.fetchone()
        if sentinel_count > 0:
            _fail(
                f"{sentinel_count} flagged_items rows still use the deprecated "
                "'(none)' sentinel; should be NULL"
            )
        print("OK: no deprecated '(none)' sentinels in flagged_items")

    print("OK: live-PostGIS smoke test complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
