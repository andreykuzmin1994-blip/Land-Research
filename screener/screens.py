"""Hard screens (reject with a reason) and soft flags (keep, but warn).

Thresholds come from settings.toml [screens]; nothing here is tuned by the
AutoResearch loop.
"""

from __future__ import annotations

from typing import Any


def attribute_rejects(p: dict[str, Any], s: dict[str, Any],
                      decisions: dict[str, dict[str, str]]) -> list[str]:
    """Cheap screens from the assessor record, run before any map checks."""
    why = []
    d = decisions.get(p["parcel_id"])
    if d and d["decision"] == "pass":
        why.append(f"you passed on this ({d.get('date') or 'no date'})")
    if d and d["decision"] == "listed" and s.get("exclude_listed", True):
        why.append("marked as listed for sale")
    imp, land = p.get("improvement_value") or 0, p.get("land_value") or 0
    if imp > s["max_improvement_value"] and imp > 0.5 * land:
        why.append(f"already built on (improvements ${imp:,.0f})")
    ot = p.get("owner_type")
    if p.get("class_code", "").startswith("E") and s.get("exclude_tax_exempt", True):
        why.append("tax-exempt parcel (government, institutional or abatement deal)")
    if ot == "government" and s.get("exclude_government", True):
        why.append("government-owned")
    if ot == "development_authority":
        why.append("held by a development authority (usually a developer's abatement deal)")
    if ot == "developer" and s.get("exclude_developer_owned", True):
        why.append("already owned by an industrial developer / REIT")
    if ot == "utility" and s.get("exclude_utility_owned", True):
        why.append("railroad / utility owned")
    return why


def site_rejects(p: dict[str, Any], s: dict[str, Any]) -> list[str]:
    """Screens that need the map checks (and slope, when it ran)."""
    why = []
    if p["flood_pct"] > s["max_flood_pct"]:
        why.append(f"{p['flood_pct']:.0f}% in the 100-year floodplain")
    if p["floodway_pct"] > s["max_floodway_pct"]:
        why.append(f"{p['floodway_pct']:.0f}% in a regulatory floodway")
    if p["wetland_pct"] > s["max_wetland_pct"]:
        why.append(f"{p['wetland_pct']:.0f}% mapped wetlands")
    if max(p["protected_pct"], p["easement_pct"]) > s["max_protected_pct"]:
        why.append("overlaps protected land or a conservation easement")
    steep = p.get("steep_pct")
    if steep is not None and steep > s["max_steep_pct"]:
        why.append(f"{steep:.0f}% of the site is steeper than 15%")
    if usable_after_slope(p) < s["min_usable_acres"]:
        why.append(f"only {usable_after_slope(p):.1f} usable acres after constraints")
    return why


def usable_after_slope(p: dict[str, Any]) -> float:
    """Usable acres net of steep ground. steep_pct is measured on the
    unconstrained part of the parcel, so the two don't overlap."""
    steep = p.get("steep_pct") or 0.0
    return round(p["usable_acres"] * (1 - steep / 100.0), 1)


def flags(p: dict[str, Any]) -> list[str]:
    out = []
    for site in p.get("contamination", [])[:3]:
        where = "on site" if site["feet"] == 0 else f"{site['feet']:,} ft away"
        out.append(f"listed contamination site {where}: {site['name']} ({site['kind']})")
    road = (p.get("road") or "").lower()
    if road == "land-locked":
        out.append("assessor lists the parcel as land-locked (no road frontage)")
    elif road in ("dirt", "semi-improved", "proposed"):
        out.append(f"road access is {road}")
    if p.get("class_code", "").startswith("V"):
        out.append("in a conservation-use covenant (CUVA): early breach triggers a tax penalty")
    water, sewer = (p.get("water") or "").lower(), (p.get("sewer") or "").lower()
    if "well" in water or "septic" in water or "septic" in sewer:
        out.append("assessor shows well/septic: public water or sewer may need extending")
    if p.get("entitlement") == "unknown":
        out.append("no machine-readable zoning for this city: confirm with the planning office")
    if p.get("flood_pct", 0) > 0:
        out.append(f"{p['flood_pct']:.0f}% in the 100-year floodplain")
    if 0 < p.get("wetland_pct", 0):
        out.append(f"{p['wetland_pct']:.0f}% mapped wetlands (1980s NWI; field delineation needed)")
    if p.get("acreage_mismatch"):
        out.append(f"parcel map shows {p.get('gis_acres')} acres vs {p.get('acres')} on the "
                   "tax record: map-based checks may be off")
    if (p.get("land_value") or 0) == 0:
        out.append("assessor land value is $0 (often railroad or utility land, assessed by the state)")
    if p.get("industrial_named"):
        out.append("owner name reads like an industrial company or developer vehicle")
    if p.get("owner_type") == "religious":
        out.append("owned by a religious organization")
    if p.get("slope_error"):
        out.append("slope check failed for this parcel")
    return out
