"""The tunable part of the score: access, land, setting and the seller.

This is the ONE file the AutoResearch loop edits (screener/autoresearch/
PROGRAM.md). The fixed part (zoning, slope, utilities, incentives) lives in
scoring.py and is not the loop's to change.

learned_score() must stay pure: read the parcel record, return numbers and
reasons, no I/O. Fields may be None (the historical answer key leaves
zoning, slope, utilities, contamination and Opportunity Zones blank), so
handle missing values.
"""

from __future__ import annotations

from typing import Any

WEIGHTS = {
    "access": 30,          # distance to an interstate exit
    "usable_land": 20,     # acres left after flood, wetlands, easements, slope
    "industrial_area": 15, # industrial parcels within a mile
    "rail": 5,
    "seller": 30,          # seller motivation (0-100, scaled)
}


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


def motivation(p: dict[str, Any]) -> tuple[float, list[str]]:
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


def learned_score(p: dict[str, Any]) -> dict[str, Any]:
    usable = p.get("usable_after_slope", p.get("usable_acres")) or 0.0
    rail = p.get("rail_ft")
    seller, why_seller = motivation(p)
    parts = {
        "access": _access(p.get("exit_miles")),
        "usable_land": min(usable / 15.0, 1.0),
        "industrial_area": min((p.get("industrial_within_1mi") or 0) / 15.0, 1.0),
        "rail": 1.0 if rail is not None and rail <= 300 else 0.5 if rail is not None and rail <= 1500 else 0.0,
        "seller": seller / 100.0,
    }
    why = []
    if p.get("exit_miles") is not None:
        why.append(f"{p['exit_miles']:.1f} mi to {p.get('exit_label') or 'an interstate exit'}")
    why.append(f"{usable:.0f} usable acres")
    if parts["industrial_area"] >= 0.6:
        why.append(f"{p.get('industrial_within_1mi')} industrial parcels within 1 mile")
    if parts["rail"] == 1.0:
        why.append(f"rail-adjacent ({p.get('rail_owner') or 'rail'} within 300 ft)")
    return {
        "score": sum(WEIGHTS[k] * v for k, v in parts.items()),
        "motivation": seller,
        "why_site": why,
        "why_seller": why_seller,
    }
