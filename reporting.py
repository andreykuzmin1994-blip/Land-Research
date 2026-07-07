"""reporting.py — per-parcel snapshots and per-market strategy memos (Phase 9).

Extracted verbatim from research.py as part of the sandbox split (see
reviews/14_streamlining_review/00_streamlining_review.md Finding B).
Pure-read rendering layer: fetches scored parcel data and market context,
renders deterministic markdown, writes atomically to snapshots/ and
rankings/. Never writes to the database.

Mutability: infrastructure, not experiment surface. The agent's experiment
loop CALLS generate_strategy_memo/generate_snapshot but does not edit this
module during a run. Review history: reviews/11_phase9_snapshots_memos/.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import prepare
import research
from pipeline_common import (
    _REPO_ROOT,
    _SQL_FETCH_SUBMARKET_NAME,
    _SQL_LATEST_MARKET_CONTEXT,
)

log = logging.getLogger("research")



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

# R-608: latest-row-per-parcel for the Phase 9 snapshot. Streamlining
# cleanup (2026-07-07, closing the R-1328/R-1329 follow-up): the score_id
# DESC tie-break now matches prepare.py's DISTINCT ON metric selector, so
# on a tied scored_at the snapshot renders the SAME row the metric counted.
_SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT = (
    "SELECT composite_score, confidence_score, actionability, "
    "actionability_blockers, sub_scores, strategy_fit, primary_strategy, "
    "investment_thesis, notes, scored_at "
    "FROM parcel_scores WHERE parcel_id = %s "
    "ORDER BY scored_at DESC, score_id DESC LIMIT 1"
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
    """R-626: 12-row breakdown table; iterate research._SUB_SCORE_NAMES so no row is
    omitted. Null sub-scores render as '—' with weighted contribution 0.
    """
    lines = [
        "| Parameter | Sub-Score | Weight | Weighted |",
        "|-----------|-----------|--------|----------|",
    ]
    total_weight = 0.0
    weighted_sum = 0.0
    for name in research._SUB_SCORE_NAMES:
        pretty, _src = research._SUB_SCORE_PROVENANCE[name]
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
    for key in research._STRATEGY_KEYS:
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
    by_strategy: dict[str, int] = {k: 0 for k in research._STRATEGY_KEYS}
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
