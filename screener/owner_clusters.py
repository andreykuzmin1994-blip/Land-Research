"""Same-owner groups on the tax roll: how many parcels one owner name and
mailing address hold in the run's acreage band, and which of them lie next
to each other (a possible assemblage).

Informational only. Nothing here is a score, a screen or a feature: the
frozen evaluation knows no such fact, so run.main keeps the result in a
local variable and adds the flag lines only after the parcels are scored and
sorted (reviews/34_screener_quick_wins, F2).

Matching is exact on two keys, with no fuzzy step: the normalised owner name
(legal forms, numbers and suites kept) AND the normalised mailing address
(ZIP+4 cut to ZIP5). A parcel without both keys joins nothing. This is NOT
owners.same_owner, whose loose name-only rules suit a deed grantor check,
not a claim about who holds what.

Stdlib + shapely only.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import shapely
from shapely import STRtree
from shapely.errors import GEOSException

from .geo import FT_PER_M, LocalProjection
from .owners import normalize

# Word stems a key name would carry. Nothing in this module puts a key on a
# parcel; the static test checks rank.py and scoring.py never name these.
KEY_STEMS = ("cluster", "portfolio", "assembl")
DEFAULT_GAP_FT = 10.0
MAX_LISTED = 5
FLAG_PREFIX = "Same owner"

PLACEHOLDER_NAMES = frozenset({
    "UNKNOWN", "OWNER UNKNOWN", "UNKNOWN OWNER", "NOT AVAILABLE", "CURRENT OWNER",
    "OWNER", "N/A", "NA", "NONE", "TBD", "NO OWNER", "UNKNOWN OWNERS", "OWNERS UNKNOWN",
})
# Spaced legal forms, longest first ("L L L P" before "L L P" before "L P").
_LEGAL_FORMS = ((re.compile(r"\bL L L P\b"), "LLLP"), (re.compile(r"\bL L P\b"), "LLP"),
                (re.compile(r"\bL L C\b"), "LLC"), (re.compile(r"\bL P\b"), "LP"))
_ZIP_END_RE = re.compile(r"\b(\d{5})(?:-?\d{4})?$")


def name_key(owner: str | None) -> str | None:
    """Upper case, commas and periods to spaces, whitespace collapsed, spaced
    legal forms joined up. None for a blank or placeholder name."""
    n = normalize(owner)
    for pattern, joined in _LEGAL_FORMS:
        n = pattern.sub(joined, n)
    if not n or n in PLACEHOLDER_NAMES:
        return None
    return n


def address_key(mailing: str | None) -> str | None:
    """The whole mailing address normalised like the name, ZIP+4 cut to
    ZIP5; suite and unit numbers are kept. fulton.py joins the street line
    and the city line with ", ", so a key needs that join, a street line
    with a number in it ("9 OAK AVE", "PO BOX 12") and a ZIP at the end.
    None otherwise: "ATLANTA, GA 30310" alone says only which city."""
    text = (mailing or "").strip()
    street = text.split(", ", 1)[0] if ", " in text else ""
    n = normalize(text)
    m = _ZIP_END_RE.search(n)
    if not re.search(r"\d", street) or not m or not normalize(street) or n == normalize(street):
        return None
    return n[:m.start()] + m.group(1)


@dataclass(frozen=True)
class Membership:
    group: tuple[str, ...]        # every member's parcel id, sorted, this one included
    component: tuple[str, ...]    # members adjacent to this one, directly or in a chain
    not_checked: bool             # adjacency could not be measured for this parcel


@dataclass
class Result:
    members: dict[str, Membership] = field(default_factory=dict)
    acres: dict[str, float] = field(default_factory=dict)
    gap_ft: float = DEFAULT_GAP_FT
    min_acres: float = 5.0
    max_acres: float = 50.0
    parcels: int = 0
    keyless: int = 0
    duplicate_ids: int = 0
    not_checked: int = 0

    @property
    def groups(self) -> list[tuple[str, ...]]:
        return sorted({m.group for m in self.members.values()})

    @property
    def components(self) -> list[tuple[str, ...]]:
        return sorted({m.component for m in self.members.values() if len(m.component) > 1})

    def band(self, total: float) -> str:
        if total > self.max_acres:
            return "above"
        return "inside" if total >= self.min_acres else "below"


def gap_ft_setting(cfg: dict[str, Any]) -> float:
    """[owners] adjacency_gap_ft, or the default when the table or key is
    absent (an older settings file). ValueError for anything not a finite
    number of feet >= 0."""
    table = cfg.get("owners", {})
    if not isinstance(table, dict):
        raise ValueError(f"[owners] must be a table (got {table!r})")
    value = table.get("adjacency_gap_ft", DEFAULT_GAP_FT)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value < 0:
        raise ValueError(f"[owners] adjacency_gap_ft must be a number of feet, 0 or more "
                         f"(got {value!r})")
    return float(value)


def _project(proj: LocalProjection, geom):
    return proj(geom)


def _distance(a, b) -> float:
    return a.distance(b)


def build(parcels: Iterable[dict[str, Any]], gap_ft: float = DEFAULT_GAP_FT,
          min_acres: float = 5.0, max_acres: float = 50.0) -> Result:
    """Group parcels by (name_key, address_key) and find adjacent members.
    Reads parcel_id, owner, owner_mailing, acres and geometry; writes nothing
    to the parcels."""
    parcels = list(parcels)
    res = Result(gap_ft=gap_ft, min_acres=min_acres, max_acres=max_acres, parcels=len(parcels))
    seen: dict[str, int] = {}
    for p in parcels:
        seen[p["parcel_id"]] = seen.get(p["parcel_id"], 0) + 1
    dup = {pid for pid, n in seen.items() if n > 1}
    res.duplicate_ids = sum(seen[pid] for pid in dup)
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for p in parcels:
        if p["parcel_id"] in dup:
            continue
        nk, ak = name_key(p.get("owner")), address_key(p.get("owner_mailing"))
        if nk is None or ak is None:
            res.keyless += 1
            continue
        by_key.setdefault((nk, ak), []).append(p)
    gap_m = gap_ft / FT_PER_M
    for key in sorted(by_key):
        group = sorted(by_key[key], key=lambda p: p["parcel_id"])
        if len(group) < 2:
            continue
        ids = tuple(p["parcel_id"] for p in group)
        for p in group:
            res.acres[p["parcel_id"]] = float(p.get("acres") or 0.0)
        comps, bad = _components(group, gap_m)
        res.not_checked += len(bad)
        for p in group:
            pid = p["parcel_id"]
            res.members[pid] = Membership(ids, comps[pid], pid in bad)
    return res


def _components(group: list[dict[str, Any]], gap_m: float):
    """Connected components of 'within gap_m metres' inside one group, and
    the ids whose adjacency could not be (fully) measured."""
    ids = [p["parcel_id"] for p in group]
    bad: set[str] = set()
    centres: dict[int, tuple[float, float]] = {}
    for i, p in enumerate(group):
        try:
            c = p["geometry"].centroid
            centres[i] = (c.x, c.y)
        except GEOSException:
            bad.add(ids[i])
    shapes: dict[int, Any] = {}
    if centres:
        # One flat projection per group: groups are local (a county spans
        # well under 100 km, where the equirectangular error is under 1%).
        proj = LocalProjection(sum(x for x, _ in centres.values()) / len(centres),
                               sum(y for _, y in centres.values()) / len(centres))
        for i in sorted(centres):
            try:
                shapes[i] = _project(proj, group[i]["geometry"])
            except GEOSException:
                bad.add(ids[i])
    parent = list(range(len(group)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(shapes)
    tree = STRtree([shapes[i] for i in order])
    for pos, i in enumerate(order):
        xmin, ymin, xmax, ymax = shapes[i].bounds
        for hit in tree.query(shapely.box(xmin - gap_m, ymin - gap_m, xmax + gap_m, ymax + gap_m)):
            j = order[int(hit)]
            if j <= i:
                continue
            try:
                near = _distance(shapes[i], shapes[j]) <= gap_m
            except GEOSException:
                bad.update((ids[i], ids[j]))
                continue
            if near:
                parent[find(i)] = find(j)
    roots: dict[int, list[str]] = {}
    for i in range(len(group)):
        roots.setdefault(find(i), []).append(ids[i])
    comps = {pid: tuple(sorted(members)) for members in roots.values() for pid in members}
    return comps, bad


def _acres(x: float) -> str:
    return f"{x:,.1f}".rstrip("0").rstrip(".")


def _listed(ids: list[str], status_of: Callable[[str], str]) -> str:
    shown = ", ".join(f"{pid} ({status_of(pid)})" for pid in ids[:MAX_LISTED])
    more = len(ids) - MAX_LISTED
    return shown + (f", and {more} more" if more > 0 else "")


def describe(pid: str, res: Result, status_of: Callable[[str], str],
             mismatch_of: Callable[[str], bool] = lambda pid: False) -> list[str]:
    """Flag lines for one parcel (none when it is in no group of two or
    more). Siblings are listed adjacent first, then by id, at most
    MAX_LISTED, each with its status in this run."""
    m = res.members.get(pid)
    if m is None:
        return []
    band = f"{_acres(res.min_acres)}-{_acres(res.max_acres)} ac"
    others = [x for x in m.group if x != pid]
    near = [x for x in m.component if x != pid]
    ordered = near + [x for x in others if x not in near]
    total = sum(res.acres[x] for x in m.group)
    lines = [f"{FLAG_PREFIX}: the same tax-roll owner name and mailing address is on "
             f"{len(others)} other {band} parcel{'s' if len(others) != 1 else ''} in the "
             f"jurisdictions searched ({_acres(total)} ac with this one): "
             f"{_listed(ordered, status_of)}. Counts cover only that band and area"]
    if near:
        comp_acres = sum(res.acres[x] for x in m.component)
        where = res.band(comp_acres)
        fit = (f"inside the {band} search band: a possible assemblage" if where == "inside"
               else f"larger than the {band} search band" if where == "above"
               else f"below the {band} search band")
        line = (f"{FLAG_PREFIX}, adjacent: touching or within {_acres(res.gap_ft)} ft of "
                f"{len(near)} of them on the county parcel map, directly or through each "
                f"other: {_acres(comp_acres)} ac together with this one, {fit}")
        if any(mismatch_of(x) for x in m.component):
            line += "; acres from the tax record, the parcel map disagrees for some"
        lines.append(line + ". Check the shared boundary: a corner touch is not frontage")
    if m.not_checked:
        lines.append(f"{FLAG_PREFIX}: adjacency to the other parcels not checked for this one "
                     "(its county map shape could not be measured)")
    return lines


def summary_notes(res: Result) -> list[str]:
    """Counts only: no ids, names or addresses."""
    notes = []
    groups = res.groups
    if groups:
        comps = res.components
        inside = sum(1 for c in comps if res.band(sum(res.acres[x] for x in c)) == "inside")
        above = sum(1 for c in comps if res.band(sum(res.acres[x] for x in c)) == "above")
        notes.append(
            f"Same-owner groups: {len(groups)} groups of 2 or more parcels "
            f"({sum(len(g) for g in groups)} parcels, largest {max(len(g) for g in groups)}) "
            f"share an exact tax-roll owner name and mailing address among the "
            f"{res.parcels} parcels of {_acres(res.min_acres)}-{_acres(res.max_acres)} ac "
            f"fetched (before any screen or trial limit); {len(comps)} adjacent sets "
            f"(touching or within {_acres(res.gap_ft)} ft), {inside} inside the band and "
            f"{above} larger. Different spellings or addresses of one owner are not joined")
    if res.not_checked:
        notes.append(f"Same-owner adjacency not checked for {res.not_checked} parcels: their "
                     "county map shapes could not be measured (each is flagged)")
    if res.duplicate_ids:
        notes.append(f"{res.duplicate_ids} parcel rows share a parcel id and were left out of "
                     "the same-owner groups")
    if res.parcels and res.keyless == res.parcels:
        notes.append("Same-owner groups not built: no parcel had both an owner name and a "
                     "street mailing address")
    return notes
