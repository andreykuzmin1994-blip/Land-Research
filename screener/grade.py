"""Lead grade (reviews/35_screener_lead_layer): one fixed, readable rule
that turns the report facts already on a ranked parcel into A / B / C /
blocked, and says which facts decided it.

It is NOT a score and NOT a screen. ``ranked.csv`` keeps the order
``scoring.py`` and ``rank.py`` gave it; the grade is three extra columns,
a block at the top of each brief and the order of ``leads.md``.

Every check reads one of three ways: clear, found, or not checked. Only a
state the check itself reports as good is clear. A failed service, a
check that was off, a missing result or a state this module does not know
is "not checked", never clear.

Checks that count when found OR not checked:

- zoning path: ``by_right`` / ``likely`` clear; ``possible`` found;
  ``hard`` blocks; anything else not checked.
- building fit (sitefit.py): ``fits`` clear; ``none found`` found.
- detention pond (detention.py), read only when an envelope fits: ``pond
  fits`` clear; ``no pond room found`` found. Without an envelope the pond
  says nothing about room for both, and the fit already counts.
- road frontage (frontage.py): at least ``min_frontage_ft`` of public,
  not access-controlled frontage clear; less, or none within 50 ft, found.
- slope (checks.py): a lidar reading clear (the steep-ground screen has
  already been applied); a failed or skipped reading not checked.
- soils (soils.py): checked and under both thresholds clear (an answer
  with no rating counts as checked: the map has nothing to say); a hydric
  or poorly-drained share at or above its threshold found.
- the parcel map against the tax record: an acreage mismatch means every
  map-based check may be off: not checked.
- same owner built next door (owner_clusters.py): a same-owner parcel
  adjacent to this one, directly or through other same-owner parcels,
  that the run screened out as already built on: found. Adjacency that
  could not be measured: not checked.

Watch items that count only when found (there is no "not checked" for
them: the fact is on the tax roll or it is not):

- a listed contamination site on the parcel (checks.map_checks).
- the assessor's "land-locked" road entry, when frontage did not already
  count.
- the last priced deed is more recent than ``recent_purchase_years``.

Grade: ``blocked`` if the zoning path is ``hard`` (that needs no other
check); else ``not graded: <why>`` for a parcel outside a check's
``max_parcels`` cap or run with a check switched off (what was found is
still listed); else by the number of found and not-checked items: 0 is A,
1 is B, 2 or more is C.

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

from . import detention, frontage, nextdoor, sitefit, soils
from .owners import operating_kind

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
    max_hydric_pct: float = 25.0            # 0 = off
    max_poorly_drained_pct: float = 25.0    # 0 = off
    owner_next_door: bool = True
    next_door_any_size: bool = True          # nextdoor.py: neighbours of any size
    next_door_max_parcels: int = 250         # 0 = all ranked parcels
    contamination_near_ft: float = 250.0     # a listed site this close counts (0 = off)
    operating_owner: bool = True             # owner name reads like a quarry / waste company
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
    nd_max = t.get("next_door_max_parcels", d.next_door_max_parcels)
    if isinstance(nd_max, bool) or not isinstance(nd_max, int) or nd_max < 0:
        raise ValueError(f"[grade] next_door_max_parcels must be a whole number, 0 or more "
                         f"(got {nd_max!r})")
    return Settings(
        enabled=_flag(t, "enabled", d.enabled),
        next_door_any_size=_flag(t, "next_door_any_size", d.next_door_any_size),
        next_door_max_parcels=nd_max,
        contamination_near_ft=_number(t, "contamination_near_ft", d.contamination_near_ft),
        operating_owner=_flag(t, "operating_owner", d.operating_owner),
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


def _why(detail: Any, kind: type) -> str:
    return detail.reason if isinstance(detail, kind) and detail.reason else "no result"


def _fit_and_pond(p: dict[str, Any], lead: Lead) -> None:
    fit = p.get("_envelope_detail")
    state = fit.state if isinstance(fit, sitefit.Fit) else None
    if state == sitefit.NONE_FOUND:
        lead.found.append("no building envelope found (a lower bound, not proof)")
        return
    if state != sitefit.FITS:
        lead.not_checked.append(f"building fit not checked ({_why(fit, sitefit.Fit)})")
        return
    pond = p.get(detention.DETAIL_KEY)
    state = pond.state if isinstance(pond, detention.Pond) else None
    if state == detention.NONE_FOUND:
        lead.found.append("no detention pond room found beside the building envelope")
    elif state != detention.FITS:
        lead.not_checked.append(f"detention pond not checked ({_why(pond, detention.Pond)})")


def _frontage(p: dict[str, Any], lead: Lead, s: Settings) -> bool:
    """True when frontage itself counted as found (so the assessor's
    land-locked entry is not counted a second time)."""
    f = p.get(frontage.DETAIL_KEY)
    state = f.state if isinstance(f, frontage.Frontage) else None
    if state not in (frontage.CHECKED, frontage.NONE):
        lead.not_checked.append(f"road frontage not checked ({_why(f, frontage.Frontage)})")
        return False
    if state == frontage.NONE:
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


def _slope_soils_and_map(p: dict[str, Any], lead: Lead, s: Settings) -> None:
    if p.get("slope_error"):
        lead.not_checked.append("slope check failed (the steep-ground screen was not applied)")
    elif p.get("slope_skipped"):
        lead.not_checked.append("slope not checked (under one lidar pixel of open ground)")
    elif _num(p.get("steep_pct")) is None:
        lead.not_checked.append("slope not checked")
    soil = p.get(soils.DETAIL_KEY)
    if not isinstance(soil, soils.Soils) or soil.state != soils.CHECKED:
        lead.not_checked.append(f"soils not checked ({_why(soil, soils.Soils)})")
    else:
        hydric, poor = _num(soil.hydric_pct), _num(soil.poorly_drained_pct)
        if s.max_hydric_pct and hydric is not None and hydric >= s.max_hydric_pct:
            lead.found.append(f"{hydric:.0f}% hydric soil components (SSURGO map units)")
        elif s.max_poorly_drained_pct and poor is not None and poor >= s.max_poorly_drained_pct:
            lead.found.append(f"{poor:.0f}% poorly drained soils (SSURGO map units)")
    fit = p.get("_envelope_detail")
    if p.get("acreage_mismatch") or (isinstance(fit, sitefit.Fit) and fit.map_mismatch):
        lead.not_checked.append("the county parcel map disagrees with the tax record, so the "
                                "map-based checks may be off")


def _next_door(p: dict[str, Any], lead: Lead, s: Settings) -> None:
    """NEXT_DOOR_KEY counts the built-on same-owner parcels next door: the
    run's 5-50 ac groups and, when next_door_any_size is on, the neighbours
    of any size nextdoor.py found (run.main counts each parcel once)."""
    any_size = p.get(nextdoor.OWNER_KEY)
    any_missing = s.next_door_any_size and not isinstance(any_size, list)
    why = p.get(nextdoor.REASON_KEY) or "no result"
    if s.owner_next_door:
        built = p.get(NEXT_DOOR_KEY)
        if isinstance(built, bool) or not isinstance(built, int) or built < 0:
            lead.not_checked.append("same-owner adjacency not checked")
            return
        if built > 0:
            lead.found.append(f"the same owner holds {built} built-on parcel"
                              f"{'s' if built != 1 else ''} adjacent to this one, directly or "
                              "through its other parcels (possibly spare land of an operating "
                              "site)")
            return
    if any_missing and (s.owner_next_door or s.contamination_near_ft):
        lead.not_checked.append(f"neighbouring parcels of any size not checked ({why})")


def _watch(p: dict[str, Any], lead: Lead, s: Settings, frontage_counted: bool) -> None:
    sites = [x for x in p.get("contamination") or [] if isinstance(x, dict)]
    near = sorted((_num(x.get("feet")) or 0.0, str(x.get("name") or "site")) for x in sites
                  if _num(x.get("feet")) is not None)
    bordering = p.get(nextdoor.SITES_KEY) if s.next_door_any_size else None
    if near and near[0][0] == 0:
        lead.found.append("a listed contamination site is on the parcel")
    elif s.contamination_near_ft and near and near[0][0] <= s.contamination_near_ft:
        lead.found.append(f"a listed contamination site {near[0][0]:,.0f} ft away "
                          f"({near[0][1]})")
    elif s.contamination_near_ft and isinstance(bordering, list) and bordering:
        lead.found.append(f"a bordering parcel holds a listed contamination site "
                          f"({bordering[0]})")
    kind = operating_kind(p.get("owner")) if s.operating_owner else None
    if kind:
        lead.found.append(f"the owner's name reads like {kind}: possibly buffer or expansion "
                          "land of its own operation")
    if not frontage_counted and (p.get("road") or "").lower() == "land-locked":
        lead.found.append("the assessor lists the parcel as land-locked")
    years = _num(p.get("years_held"))
    if s.recent_purchase_years and years is not None and 0 <= years < s.recent_purchase_years \
            and (_num(p.get("last_sale_price")) or 0) > 0:
        lead.found.append(f"last priced deed {years:.1f} years ago (a recent purchase, or a "
                          "transfer between related owners)")


def grade_parcel(p: dict[str, Any], s: Settings, not_graded: str = "") -> Lead:
    """The grade of one ranked parcel from the report facts on it. Reads
    only; writes nothing. ``not_graded`` is the reason a check was never
    attempted for this parcel (a cap or a switch), if any."""
    lead = Lead(A)
    _zoning(p, lead)
    _fit_and_pond(p, lead)
    counted = _frontage(p, lead, s)
    _slope_soils_and_map(p, lead, s)
    _next_door(p, lead, s)
    _watch(p, lead, s, counted)
    if lead.blocks:
        lead.grade = BLOCKED            # the zoning path needs no other check
    elif not_graded:
        lead.grade, lead.reason = NOT_GRADED, not_graded
    else:
        n = len(lead.found) + len(lead.not_checked)
        lead.grade = A if n == 0 else B if n == 1 else C
    return lead


def _outside(rank: int, caps: list[tuple[str, str, bool, int]]) -> str:
    """Why a check was never attempted at this rank ('' if all were).
    caps: (label, settings table, enabled, max_parcels) per check that can
    be switched off or capped."""
    for label, table, enabled, cap in caps:
        if not enabled:
            return f"{label} not run (switched off)"
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
        head = ("**Lead grade A**: zoning path, building fit, pond room, road frontage, "
                "slope and soils all came back clear, and no watch item was found")
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
