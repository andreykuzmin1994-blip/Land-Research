"""The final score: a fixed, human-owned part plus the part AutoResearch tunes.

    score = FIXED_SHARE * fixed_score + (1 - FIXED_SHARE) * rank.learned_score

fixed_score covers what the historical answer key can't measure: zoning /
future land use, slope, utilities and incentives. Their 2016 values are
missing or were changed for the very projects in the key. Keeping them in
this file, which the loop may not edit, means the loop can never quietly
shrink their weight in the live list.
"""

from __future__ import annotations

import math
from typing import Any

from . import rank

FIXED_SHARE = 0.35
FIXED_WEIGHTS = {"entitlement": 55, "topography": 25, "utilities": 10, "incentives": 10}
ENTITLEMENT_VALUE = {"by_right": 1.0, "likely": 0.8, "possible": 0.5,
                     "unknown": 0.4, "hard": 0.0, None: 0.4}


def _utilities(p: dict[str, Any]) -> float:
    water, sewer = (p.get("water") or "").lower(), (p.get("sewer") or "").lower()
    if not water and not sewer:
        return 0.4
    if "septic" in water or "well" in water or "septic" in sewer:
        return 0.2
    if "all public" in water or "underground" in sewer or "public sewer" in water:
        return 1.0
    return 0.6


def fixed_score(p: dict[str, Any]) -> tuple[float, list[str]]:
    steep = p.get("steep_pct")
    parts = {
        "entitlement": ENTITLEMENT_VALUE.get(p.get("entitlement"), 0.4),
        "topography": 0.5 if steep is None else max(0.0, 1.0 - steep / 40.0),
        "utilities": _utilities(p),
        "incentives": 1.0 if p.get("opportunity_zone") else 0.0,
    }
    why = []
    if p.get("entitlement_note"):
        why.append(p["entitlement_note"])
    if steep is not None and steep <= 10:
        why.append("mostly gentle ground")
    if p.get("opportunity_zone"):
        why.append("in a federal Opportunity Zone")
    return round(sum(FIXED_WEIGHTS[k] * v for k, v in parts.items()), 1), why


def score(p: dict[str, Any]) -> dict[str, Any]:
    fixed, fixed_why = fixed_score(p)
    learned = rank.learned_score(p)
    try:
        raw = float(learned["score"])
    except (TypeError, ValueError):
        raw = math.nan
    # min/max would turn NaN or infinity into 100 (or 0). An unusable
    # learned score counts as 0, and the brief says so.
    why_learned = []
    if not math.isfinite(raw):
        why_learned = [f"learned score unusable ({learned['score']!r}): counted as 0"]
        raw = 0.0
    # rank.py must stay on a 0-100 scale (evaluate.py enforces it); clamp
    # anyway so the fixed share can't be diluted.
    tuned = max(0.0, min(100.0, raw))
    return {
        "score": round(FIXED_SHARE * fixed + (1 - FIXED_SHARE) * tuned, 1),
        "fixed_score": fixed,
        "learned_score": round(raw, 1),
        "motivation_score": round(learned["motivation"], 1),
        "why_site": fixed_why + why_learned + learned["why_site"],
        "why_seller": learned["why_seller"],
    }
