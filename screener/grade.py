"""Lead grade (reviews/35_screener_lead_layer): one fixed, readable rule
that turns the report facts already on a ranked parcel into A / B / C /
blocked, and says which facts decided it.

It is NOT a score and NOT a screen. ``ranked.csv`` keeps the order
``scoring.py`` and ``rank.py`` gave it; the grade is three extra columns,
a line at the top of each brief and the order of ``leads.md``.

The rule reads four core checks and five watch items.

Core checks (each is ok, found, or not checked):

- zoning path: ``by_right`` / ``likely`` ok; ``possible`` found (rezoning
  needed); ``hard`` blocks; anything else is not checked.
- building fit (sitefit.py): ``fits`` ok; ``none found`` found; else not
  checked.
- detention pond (detention.py), read only when an envelope fits: ``pond
  fits`` ok; ``no pond room found`` found; else not checked. Without an
  envelope the pond says nothing about room for both, and the fit already
  counts.
- road frontage (frontage.py): at least ``min_frontage_ft`` of public,
  not access-controlled frontage ok; less, or none within 50 ft, found;
  else not checked.

Watch items (count only when found; an unchecked one is not counted):

- soils (soils.py): hydric share or poorly-drained share at or above its
  threshold.
- a listed contamination site on the parcel (checks.map_checks).
- the assessor's "land-locked" road entry, when frontage did not already
  count.
- the same owner holds a built-on parcel next door (owner_clusters.py):
  possibly spare land of an operating site.
- a purchase within ``recent_purchase_years``: a recent buyer rarely sells.

Grade: ``blocked`` if anything blocks; else by the number of found and
not-checked items: 0 is A, 1 is B, 2 or more is C. A parcel outside a
core check's ``max_parcels`` cap, or run with a core check switched off,
reads ``not graded: <why>`` (its found items are still listed).

"Found" is what the screen found, with every limit of the check behind
it: "no building envelope found" is a lower bound, not proof nothing
fits. The grade sorts a reading list; it decides nothing.

Live-only, like the facts it reads: ``run.main`` calls ``attach`` after
scoring, flags, the sort and every report check, and ``rank.py`` /
``scoring.py`` / ``screens.py`` never see these keys (tests/test_grade.py
spies on every call). Nothing here asks a web service.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import detention, frontage, sitefit, soils

# Name stem of every key this module puts on a parcel (the static and spy
# guards import it).
KEY_STEMS = ("lead_",)
COLUMNS = ("lead_grade", "lead_found", "lead_not_checked")
DETAIL_KEY = "_lead_detail"
NEXT_DOOR_KEY = "_lead_owner_built_next_door"   # set by run.main, after the sort

A, B, C, BLOCKED, NOT_GRADED = "A", "B", "C", "blocked", "not graded"
GRADES = (A, B, C, BLOCKED)
NOT_GRADED_PREFIX = "not graded: "
OK_ENTITLEMENT = ("by_right", "likely")

CAVEAT = ("The grade is a fixed rule over this screen's own checks, each with the limits "
          "given beside it in the brief: \"found\" is what the screen found, not proof (a "
          "missing building fit is a lower bound; frontage is measured to a road centreline; "
          "soils are map-unit level). It orders a reading list. It is not a score, a screen "
          "or a recommendation, and it does not change the ranking")


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    min_frontage_ft: float = 50.0
    max_hydric_pct: float = 25.0
    max_poorly_drained_pct: float = 25.0
    owner_next_door: bool = True
    recent_purchase_years: float = 5.0      # 0 = off
    digest_max: int = 40


def _number(table: dict[str, Any], key: str, default: float, lo: float = 0.0) -> float:
    v = table.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < lo:
        raise ValueError(f"[grade] {key} must be a number, {lo:g} or more (got {v!r})")
    return float(v)


def _flag(table: dict[str, Any], key: str, default: bool) -> bool:
    v = table.get(key, default)
    if not isinstance(v, bool):
        raise ValueError(f"[grade] {key} must be true or false (got {v!r})")
    return v


def settings(cfg: dict[str, Any]) -> Settings:
    """[grade], or the defaults when the table or a key is absent (an older
    settings file). ValueError for a value of the wrong kind."""
    t = cfg.get("grade", {})
    if not isinstance(t, dict):
        raise ValueError(f"[grade] must be a table (got {t!r})")
    d = Settings()
    digest_max = t.get("digest_max", d.digest_max)
    if isinstance(digest_max, bool) or not isinstance(digest_max, int) or digest_max < 0:
        raise ValueError(f"[grade] digest_max must be a whole number, 0 or more "
                         f"(got {digest_max!r})")
    return Settings(
        enabled=_flag(t, "enabled", d.enabled),
        min_frontage_ft=_number(t, "min_frontage_ft", d.min_frontage_ft),
        max_hydric_pct=_number(t, "max_hydric_pct", d.max_hydric_pct),
        max_poorly_drained_pct=_number(t, "max_poorly_drained_pct", d.max_poorly_drained_pct),
        owner_next_door=_flag(t, "owner_next_door", d.owner_next_door),
        recent_purchase_years=_number(t, "recent_purchase_years", d.recent_purchase_years),
        digest_max=digest_max,
    )


@dataclass
class Lead:
    grade: str                                   # A / B / C / BLOCKED / NOT_GRADED
    blocks: list[str] = field(default_factory=list)
    found: list[str] = field(default_factory=list)
    not_checked: list[str] = field(default_factory=list)
    reason: str = ""                             # why not graded

    def text(self) -> str:
        return NOT_GRADED_PREFIX + self.reason if self.grade == NOT_GRADED else self.grade

    def fields(self) -> dict[str, Any]:
        """The report fields (attached after the sort, never before)."""
        return {"lead_grade": self.text(), "lead_found": self.blocks + self.found,
                "lead_not_checked": list(self.not_checked), DETAIL_KEY: self}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _zoning(p: dict[str, Any], lead: Lead) -> None:
    path, note = p.get("entitlement"), p.get("entitlement_note") or "no note"
    if path in OK_ENTITLEMENT:
        return
    if path == "hard":
        lead.blocks.append(f"entitlement unlikely ({note})")
    elif path == "possible":
        lead.found.append(f"zoning path only possible ({note})")
    else:
        lead.not_checked.append(f"zoning path not known ({note})")


def _fit_and_pond(p: dict[str, Any], lead: Lead) -> None:
    fit = p.get("_envelope_detail")
    if not isinstance(fit, sitefit.Fit) or fit.state == sitefit.NOT_CHECKED:
        why = fit.reason if isinstance(fit, sitefit.Fit) and fit.reason else "no result"
        lead.not_checked.append(f"building fit not checked ({why})")
        return
    if fit.state != sitefit.FITS:
        lead.found.append("no building envelope found (a lower bound, not proof)")
        return
    pond = p.get(detention.DETAIL_KEY)
    if not isinstance(pond, detention.Pond) or pond.state == detention.NOT_CHECKED:
        why = pond.reason if isinstance(pond, detention.Pond) and pond.reason else "no result"
        lead.not_checked.append(f"detention pond not checked ({why})")
    elif pond.state != detention.FITS:
        lead.found.append("no detention pond room found beside the building envelope")


def _frontage(p: dict[str, Any], lead: Lead, s: Settings) -> bool:
    """True when frontage itself counted as found (so the assessor's
    land-locked entry is not counted a second time)."""
    f = p.get(frontage.DETAIL_KEY)
    if not isinstance(f, frontage.Frontage) or f.state == frontage.NOT_CHECKED:
        why = f.reason if isinstance(f, frontage.Frontage) and f.reason else "no result"
        lead.not_checked.append(f"road frontage not checked ({why})")
        return False
    if f.state == frontage.NONE:
        extra = []
        if f.private_feet:
            extra.append(f"{f.private_feet:,} ft on a private road")
        if f.limited_feet:
            extra.append(f"{f.limited_feet:,} ft on an access-controlled road")
        lead.found.append(f"no public road frontage within {frontage.BUFFER_FT:.0f} ft"
                          + (f" ({'; '.join(extra)})" if extra else ""))
        return True
    feet = _num(f.feet) or 0.0
    if feet < s.min_frontage_ft:
        lead.found.append(f"only {feet:,.0f} ft of public road frontage "
                          f"(under {s.min_frontage_ft:g} ft)")
        return True
    return False


def _watch(p: dict[str, Any], lead: Lead, s: Settings, frontage_counted: bool) -> None:
    soil = p.get(soils.DETAIL_KEY)
    if isinstance(soil, soils.Soils) and soil.state == soils.CHECKED:
        hydric, poor = _num(soil.hydric_pct), _num(soil.poorly_drained_pct)
        if hydric is not None and hydric >= s.max_hydric_pct:
            lead.found.append(f"{hydric:.0f}% hydric soil components (SSURGO map units)")
        elif poor is not None and poor >= s.max_poorly_drained_pct:
            lead.found.append(f"{poor:.0f}% poorly drained soils (SSURGO map units)")
    sites = p.get("contamination") or []
    if any(isinstance(x, dict) and x.get("feet") == 0 for x in sites):
        lead.found.append("a listed contamination site is on the parcel")
    if not frontage_counted and (p.get("road") or "").lower() == "land-locked":
        lead.found.append("the assessor lists the parcel as land-locked")
    built = p.get(NEXT_DOOR_KEY)
    if s.owner_next_door and isinstance(built, int) and not isinstance(built, bool) and built > 0:
        lead.found.append(f"the same owner holds {built} built-on parcel"
                          f"{'s' if built != 1 else ''} next door (possibly spare land of an "
                          "operating site)")
    years = _num(p.get("years_held"))
    if s.recent_purchase_years and years is not None and years < s.recent_purchase_years \
            and (_num(p.get("last_sale_price")) or 0) > 0:
        lead.found.append(f"bought {years:.1f} years ago (a recent buyer rarely sells)")


def grade_parcel(p: dict[str, Any], s: Settings, not_graded: str = "") -> Lead:
    """The grade of one ranked parcel from the report facts on it. Reads
    only; writes nothing. ``not_graded`` is the reason a core check was
    never attempted for this parcel (a cap or a switch), if any."""
    lead = Lead(A)
    _zoning(p, lead)
    _fit_and_pond(p, lead)
    counted = _frontage(p, lead, s)
    _watch(p, lead, s, counted)
    if not_graded:
        lead.grade, lead.reason = NOT_GRADED, not_graded
    elif lead.blocks:
        lead.grade = BLOCKED
    else:
        n = len(lead.found) + len(lead.not_checked)
        lead.grade = A if n == 0 else B if n == 1 else C
    return lead


def _outside(rank: int, caps: list[tuple[str, str, bool, int]]) -> str:
    """Why a core check was never attempted at this rank ('' if all were).
    caps: (label, settings table, enabled, max_parcels) per capped check."""
    for label, table, enabled, cap in caps:
        if not enabled:
            return f"{label} switched off in settings"
    for label, table, enabled, cap in caps:
        if cap and rank > cap:
            return f"{label} only checked for the top {cap} ([{table}] max_parcels)"
    return ""


def attach(ranked: list[dict[str, Any]], s: Settings,
           caps: list[tuple[str, str, bool, int]], notes: list[str]) -> None:
    """Grade each ranked parcel and attach the report fields. Called only
    after scores, flags, the sort and every report check."""
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for i, p in enumerate(ranked, 1):
        if not s.enabled:
            lead = Lead(NOT_GRADED, reason="grading switched off in settings")
        else:
            lead = grade_parcel(p, s, _outside(i, caps))
        if lead.grade == NOT_GRADED:
            reasons[lead.reason] = reasons.get(lead.reason, 0) + 1
        else:
            counts[lead.grade] = counts.get(lead.grade, 0) + 1
        p.update(lead.fields())
    if counts:
        shown = ", ".join(f"{g} {counts.get(g, 0)}" for g in GRADES)
        notes.append(f"Lead grade (a fixed rule over this screen's checks; not a score or a "
                     f"screen, the ranking is unchanged): {shown} of {len(ranked)} ranked "
                     "parcels; see leads.md")
    for why, n in sorted(reasons.items()):
        notes.append(f"Lead grade not given for {n} of {len(ranked)} ranked parcels: {why}")


def brief_lines(p: dict[str, Any]) -> list[str]:
    """The brief's grade block (hand-written wording only)."""
    lead: Lead | None = p.get(DETAIL_KEY)
    if lead is None:
        return ["**Lead grade: not graded** (grading off)"]
    if lead.grade == NOT_GRADED:
        head = f"**Lead grade: not graded** ({lead.reason})"
    elif lead.grade == A:
        head = ("**Lead grade A**: zoning path, building fit, pond room and road frontage "
                "all came back clear, and no watch item was found")
    elif lead.grade == BLOCKED:
        head = "**Lead grade: blocked**"
    else:
        head = f"**Lead grade {lead.grade}**"
    lines = [head]
    if lead.blocks:
        lines.append("- Blocks: " + "; ".join(lead.blocks))
    if lead.found:
        lines.append("- Found: " + "; ".join(lead.found))
    if lead.not_checked:
        lines.append("- Not checked: " + "; ".join(lead.not_checked))
    return lines
