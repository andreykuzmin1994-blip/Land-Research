"""Live-PostGIS smoke test for the Phase 3 Fulton discovery connector.

Phase 3.1 §6.B / §10.5: the offline test suite uses ``FakeConnection`` and
never sends real SQL to a database. This script complements that by running
a single happy-path UPSERT against a live PostGIS service so the
``_SQL_UPSERT_PARCEL`` statement, the ``ST_GeomFromText`` / ``ST_Centroid``
calls, the ``ON CONFLICT`` clause, and the ``GEOMETRY(Polygon, 4326)``
column type are actually exercised together.

Invoked from ``.github/workflows/discovery-fulton.yml`` against a postgres
service container with PostGIS installed.

It WRITES (parcels, research_log, flagged_items, PASS parcel_scores rows),
so it is meant for a throwaway database and refuses to run unless
``DATABASE_URL`` points at this machine (localhost / 127.0.0.1 / ::1 or a
Unix socket). Set ``POSTGIS_SMOKE_ALLOW_NONLOCAL=1`` to override that
deliberately; never point it at the shared Supabase project.

Exit code 0 = pass. Non-zero = fail, with a printed diagnostic.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

# Ensure repo root on sys.path so ``research`` and ``prepare`` import.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import prepare  # noqa: E402
import research  # noqa: E402

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ALLOW_NONLOCAL_ENV_VAR = "POSTGIS_SMOKE_ALLOW_NONLOCAL"


def _fail(msg: str) -> "psycopg.Connection | None":
    print(f"FAIL: {msg}")
    sys.exit(1)


def _nonlocal_hosts(dsn: str) -> list[str]:
    """Return every host ``dsn`` would reach that is not this machine.

    Follows libpq: ``host`` and ``hostaddr`` may each be comma-separated
    lists and fall back to ``PGHOST`` / ``PGHOSTADDR`` when absent from
    the DSN; no host at all (or a ``/``- or ``@``-prefixed one) is a Unix
    socket. A ``service`` entry resolves from a file this script does not
    read, so it counts as non-local. Raises ``psycopg.ProgrammingError``
    on an unparseable DSN. Callers that print the result check
    ``_has_stray_at`` first: a returned "host" can be a password fragment.
    """
    params = conninfo_to_dict(dsn)
    service = params.get("service") or os.environ.get("PGSERVICE")
    if service:
        return [f"service={service}"]
    found = []
    for key, env_var in (("host", "PGHOST"), ("hostaddr", "PGHOSTADDR")):
        value = params.get(key) or os.environ.get(env_var) or ""
        for host in str(value).split(","):
            host = host.strip()
            if host and host not in _LOCAL_HOSTS and not host.startswith(("/", "@")):
                found.append(host)
    return found


def _has_stray_at(dsn: str) -> bool:
    """True when an '@' sits where libpq's URL split can leave a password tail.

    libpq splits a URL's ``user:password`` at the FIRST '@', so a password
    holding an unencoded '@' spills its tail into the host
    (``wordXYZ@db.example.com``) or, when the tail holds ':' / '/' / '?',
    makes the host itself a password fragment and pushes the '@' into the
    port, dbname or a query parameter. Printing a host name then prints
    part of the password (SR-8). A lone leading '@' on a host is an
    abstract Unix-socket name, not a spill. Raises
    ``psycopg.ProgrammingError`` on an unparseable DSN.
    """
    params = dict(conninfo_to_dict(dsn))
    for key, env_var in (("host", "PGHOST"), ("hostaddr", "PGHOSTADDR")):
        params[key] = params.get(key) or os.environ.get(env_var)
    for key, value in params.items():
        if key in ("user", "password") or value is None:
            continue
        text = str(value)
        if key == "host":
            text = ",".join(host.strip()[1:] for host in text.split(","))
        if "@" in text:
            return True
    return False


def _require_local_dsn(dsn: str) -> None:
    """Exit unless ``dsn`` is local or the explicit opt-in is set.

    Only host names are printed, never the DSN: ``prepare._mask_dsn``
    masks URL-form DSNs only, and the parser's error text can quote
    key=value fragments such as a password (SR-8). A DSN with a stray '@'
    (see ``_has_stray_at``) is refused without printing any host name,
    since the "host" may be the tail of the password.
    """
    if os.environ.get(_ALLOW_NONLOCAL_ENV_VAR) == "1":
        print(f"WARN: {_ALLOW_NONLOCAL_ENV_VAR}=1 — local-host guard skipped")
        return
    try:
        stray_at = _has_stray_at(dsn)
        remote = _nonlocal_hosts(dsn)
    except psycopg.Error:
        _fail("DATABASE_URL is not a valid libpq connection string")
    if stray_at:
        _fail(
            "DATABASE_URL has an '@' after its user:password part (an "
            "unencoded '@' in the password?), so the host is not printed. "
            "URL-encode '@' in a password as %40, and point DATABASE_URL at "
            f"a throwaway local PostGIS (or set {_ALLOW_NONLOCAL_ENV_VAR}=1 "
            f"to override deliberately)."
        )
    if remote:
        _fail(
            f"refusing to write smoke-test rows to non-local host(s) "
            f"{', '.join(remote)}. Point DATABASE_URL at a throwaway local "
            f"PostGIS, or set {_ALLOW_NONLOCAL_ENV_VAR}=1 to override "
            f"deliberately."
        )


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        _fail("DATABASE_URL not set; cannot run live-PostGIS smoke test")
        return 1
    _require_local_dsn(dsn)

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

    # Step 5 (Phase 13, R-1323): exercise the metric's DISTINCT ON tie-break
    # for real. The fake-cursor offline suite cannot run SQL, so the live DB is
    # the only place the latest-per-parcel selection is validated. Insert THREE
    # parcel_scores rows for ONE parcel: two PASS rows at the IDENTICAL
    # scored_at (the tie case) and one PASS row at an EARLIER scored_at. The old
    # MAX(scored_at) correlated subquery would have counted the parcel TWICE
    # (both tied rows pass); the DISTINCT ON (parcel_id) ORDER BY parcel_id,
    # scored_at DESC, score_id DESC selects EXACTLY ONE row — the highest
    # score_id among the tie — so the count is 1.
    #
    # The rows carry a fresh smoke-only run_tag and the metric is read with
    # that tag EXPLICITLY. With run_tag=None prepare derives the scope from
    # the checked-out branch: on autoresearch/<tag> the run-scoped CTE
    # filtered these (NULL-tagged) rows out and the count was a false 0;
    # on any other branch it counted the whole database. The explicit tag
    # makes the check independent of both, and exercises the run-scoped
    # CTE the ratchet uses.
    smoke_run_tag = f"smoke-{secrets.token_hex(4)}"
    threshold = prepare.get_parameters()["composite_threshold"]
    pass_score = threshold + 5  # comfortably over the PASS threshold
    with psycopg.connect(dsn, autocommit=False, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT parcel_id FROM parcels WHERE parcel_id LIKE %s LIMIT 1",
                ("fulton-%",),
            )
            row = cur.fetchone()
        if not row:
            _fail("no fulton-% parcel available for the tie-break test")
        tie_parcel = row[0]
        with conn.cursor() as cur:
            # Earlier (loser) PASS row.
            cur.execute(
                "INSERT INTO parcel_scores "
                "(parcel_id, scored_at, composite_score, confidence_score, "
                "actionability, run_tag) "
                "VALUES (%s, NOW() - INTERVAL '1 hour', %s, %s, 'PASS', %s)",
                (tie_parcel, pass_score, 1.0, smoke_run_tag),
            )
            # Two rows tied at the SAME scored_at; the higher score_id (the
            # SECOND insert) must win. Distinct confidence so we can confirm
            # which row DISTINCT ON picked.
            cur.execute(
                "INSERT INTO parcel_scores "
                "(parcel_id, scored_at, composite_score, confidence_score, "
                "actionability, run_tag) "
                "VALUES (%s, NOW(), %s, %s, 'PASS', %s), "
                "(%s, NOW(), %s, %s, 'PASS', %s)",
                (tie_parcel, pass_score, 3.0, smoke_run_tag,
                 tie_parcel, pass_score, 9.0, smoke_run_tag),
            )
        conn.commit()

        # Determine the highest score_id among the tied (latest) rows — that is
        # the row DISTINCT ON must select.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT score_id, confidence_score FROM parcel_scores "
                "WHERE parcel_id = %s AND run_tag = %s "
                "ORDER BY scored_at DESC, score_id DESC LIMIT 1",
                (tie_parcel, smoke_run_tag),
            )
            winner_score_id, winner_conf = cur.fetchone()

        count = prepare.calculate_actionable_pipeline_count(
            conn, run_tag=smoke_run_tag
        )
        if count != 1:
            _fail(
                f"DISTINCT ON tie-break: expected count==1 for one parcel with "
                f"two tied PASS rows, got {count} (double-count regression?)"
            )
        # The confidence-weighted sum must reflect the SINGLE winning row, i.e.
        # the highest-score_id tied row's confidence (9.0), not a double count.
        weighted = prepare.calculate_confidence_weighted_pipeline(
            conn, run_tag=smoke_run_tag
        )
        if abs(weighted - float(winner_conf)) > 1e-9:
            _fail(
                f"DISTINCT ON tie-break: confidence_weighted={weighted} did not "
                f"match the winning row's confidence {winner_conf} "
                f"(score_id={winner_score_id})"
            )
        print(
            f"OK: DISTINCT ON tie-break selects exactly one row "
            f"(run_tag={smoke_run_tag}, score_id={winner_score_id}, "
            f"confidence={winner_conf}); count=1"
        )

    print("OK: live-PostGIS smoke test complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
