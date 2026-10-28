"""Ranking: how good a site is, and how likely the owner is to sell.

This is the one file the AutoResearch loop is allowed to edit
(screener/autoresearch/). Keep score() pure: it reads the parcel record and
returns numbers and reasons, with no I/O. Features a parcel lacks (for
example zoning in the historical evaluation set) arrive as None and must
be handled.
"""

from __future__ import annotations

from typing import Any

SITE_WEIGHTS = {
    "access": 25,          # distance to an interstate exit
    "entitlement": 25,     # zoning / future land use path
    "usable_land": 15,     # acres left after flood, wetlands, easements, slope
    "topography": 10,      # share of the site steeper than 15%
    "industrial_area": 10, # industrial parcels within a mile
    "rail": 5,
    "utilities": 5,
    "incentives": 5,
}
SITE_SHARE = 0.7  # final score = 70% site quality + 30% seller motivation

ENTITLEMENT_VALUE = {"by_right": 1.0, "likely": 0.8, "possible": 0.5,
                     "unknown": 0.4, "hard": 0.0, None: 0.4}


def _access(miles: float | None) -> float:
    if miles is None:
        return 0.0
    if miles <= 1:
        return 1.0
    if miles <= 3:
        return 1.0 - 0.25 * (miles - 1)
    if miles <= 6:
        return 0.5 - (miles - 3) / 6
    return 0.0


def _utilities(p: dict[str, Any]) -> float:
    water, sewer = (p.get("water") or "").lower(), (p.get("sewer") or "").lower()
    if not water and not sewer:
        return 0.4
    if "septic" in water or "well" in water or "septic" in sewer:
        return 0.2
    if "all public" in water or "underground" in sewer or "public sewer" in water:
        return 1.0
    return 0.6


def site_score(p: dict[str, Any]) -> tuple[float, list[str]]:
    usable = p.get("usable_after_slope", p.get("usable_acres")) or 0.0
    steep = p.get("steep_pct")
    rail = p.get("rail_ft")
    parts = {
        "access": _access(p.get("exit_miles")),
        "entitlement": ENTITLEMENT_VALUE.get(p.get("entitlement"), 0.4),
        "usable_land": min(usable / 15.0, 1.0),
        "topography": 0.5 if steep is None else max(0.0, 1.0 - steep / 40.0),
        "industrial_area": min((p.get("industrial_within_1mi") or 0) / 15.0, 1.0),
        "rail": 1.0 if rail is not None and rail <= 300 else 0.5 if rail is not None and rail <= 1500 else 0.0,
        "utilities": _utilities(p),
        "incentives": 1.0 if p.get("opportunity_zone") else 0.0,
    }
    score = sum(SITE_WEIGHTS[k] * v for k, v in parts.items())
    why = []
    if p.get("exit_miles") is not None:
        why.append(f"{p['exit_miles']:.1f} mi to {p.get('exit_label') or 'an interstate exit'}")
    if p.get("entitlement_note"):
        why.append(p["entitlement_note"])
    why.append(f"{usable:.0f} usable acres")
    if parts["industrial_area"] >= 0.6:
        why.append(f"{p.get('industrial_within_1mi')} industrial parcels within 1 mile")
    if parts["rail"] == 1.0:
        why.append(f"rail-adjacent ({p.get('rail_owner') or 'rail'} within 300 ft)")
    if p.get("opportunity_zone"):
        why.append("in a federal Opportunity Zone")
    return round(score, 1), why


def motivation_score(p: dict[str, Any]) -> tuple[float, list[str]]:
    score, why = 0.0, []
    years = p.get("years_held")
    if years is None and p.get("sales_count") == 0:
        score += 25
        why.append("no recorded sale on file (likely long-held)")
    elif years is not None and years >= 20:
        score += 35
        why.append(f"same owner for {years:.0f} years")
    elif years is not None and years >= 10:
        score += 20
        why.append(f"same owner for {years:.0f} years")
    elif years is not None and years < 3:
        score -= 15
        why.append("bought within the last 3 years")
    if p.get("inherited") or p.get("owner_type") == "estate":
        score += 25
        why.append("inherited / estate ownership")
    if p.get("owner_state") and p["owner_state"] != "GA":
        score += 15
        why.append(f"owner mails from out of state ({p['owner_state']})")
    if p.get("owner_type") in ("individual", "trust", "estate"):
        score += 10
        why.append(f"{p['owner_type']} owner")
    if p.get("distress_owned"):
        score += 10
        why.append("current owner took title by foreclosure or tax deed")
    if (p.get("class_code") or "").startswith("V"):
        score -= 10
    return max(0.0, min(100.0, score)), why


def score(p: dict[str, Any]) -> dict[str, Any]:
    site, site_why = site_score(p)
    motive, motive_why = motivation_score(p)
    return {
        "score": round(SITE_SHARE * site + (1 - SITE_SHARE) * motive, 1),
        "site_score": site,
        "motivation_score": motive,
        "why_site": site_why,
        "why_seller": motive_why,
    }
