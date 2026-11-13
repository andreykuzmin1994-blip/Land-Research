"""Neighbours of any size, for the lead grade: the owner's own operation
next door, and a listed contamination site on a bordering parcel.

The run fetches only 5-50 acre parcels, so the same-owner check in
owner_clusters cannot see a 94-acre distribution centre or a quarry pit
next to a lead. Here each of the top-ranked parcels asks the county parcel
layer for the parcels touching it, whatever their size, and follows the
same owner's parcels outward a few steps (owners.same_owner, the loose
name rule: a "possibly" line, not a claim about title).

Live-only and read after the sort, like the grade itself: nothing here is a
score, a screen or an evaluation feature, and the keys it puts on a parcel
carry grade's "lead_" stem (the spy tests in test_grade.py hold).

Limits: the county layer's polygons only (no deeds); a neighbour counts as
built from its assessed improvements over the screen's cap, unless its land
use code is residential (a family's own house next to its land is not an
operating site); a listed site counts when the
site's mapped point falls on (or within the gap of) a touching parcel, so a
site drawn at a street address off that parcel is missed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from shapely import STRtree
from shapely.geometry import box
from shapely.errors import GEOSException

from . import arcgis
from .geo import FT_PER_M, LocalProjection, esri_to_shape
from .owners import same_owner

OWNER_KEY = "_lead_owner_built_any_size"        # list of Built, or None: not checked
SITES_KEY = "_lead_contaminated_neighbour"      # list of site names, or None: not checked
REASON_KEY = "_lead_next_door_not_checked"      # why, when the two above are None
FIELDS = "ParcelID,Owner,ImprAppr,LandAcres,LUCode"
PAD_DEG = 0.0003            # about 100 ft: the bbox pad for the touching query
MAX_HOPS = 3                # same-owner steps outward from the lead
MAX_VISITED = 30            # same-owner parcels followed per lead
MAX_WORKERS = 4


@dataclass(frozen=True)
class Built:
    parcel_id: str
    acres: float | None
    improvement_value: float


class Sites:
    """The listed sites the run fetched, indexed by location."""

    def __init__(self, items: list[tuple[Any, str]]):
        self.items = items
        self.tree = STRtree([s for s, _ in items]) if items else None

    def near(self, geom, pad_deg: float = PAD_DEG) -> list[tuple[Any, str]]:
        if self.tree is None:
            return []
        x0, y0, x1, y1 = geom.bounds
        hits = self.tree.query(box(x0 - pad_deg, y0 - pad_deg, x1 + pad_deg, y1 + pad_deg))
        return [self.items[int(i)] for i in hits]


@dataclass
class Result:
    built: list[Built] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)


def _touching(url: str, geom, gap_ft: float) -> list[tuple[dict[str, Any], Any]]:
    """(attributes, shape) of the county parcels within gap_ft of geom."""
    x0, y0, x1, y1 = geom.bounds
    env = (x0 - PAD_DEG, y0 - PAD_DEG, x1 + PAD_DEG, y1 + PAD_DEG)
    proj = LocalProjection.around(geom)
    gm = proj(geom)
    out = []
    for f in arcgis.features_in(url, "1=1", [env], FIELDS):
        shape = esri_to_shape(f.get("geometry"))
        if shape is None or shape.is_empty:
            continue
        if proj(shape).distance(gm) * FT_PER_M <= gap_ft:
            out.append((f.get("attributes") or {}, shape))
    return out


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def check_one(p: dict[str, Any], url: str, gap_ft: float, built_value: float,
              sites: Sites) -> Result:
    """Walk the same owner's touching parcels (any size) out to MAX_HOPS and
    look for listed sites on the parcels touching the lead itself (a site
    on the lead is checks.map_checks' business, not a neighbour's)."""
    pid, owner = p["parcel_id"], p.get("owner") or ""
    res = Result()
    own = p["geometry"]
    nearby = [(pt, name) for pt, name in sites.near(own, PAD_DEG * 20)
              if not own.intersects(pt)]
    seen = {pid}
    frontier = [p["geometry"]]
    first = True
    for _ in range(MAX_HOPS):
        nxt = []
        for geom in frontier:
            for attrs, shape in _touching(url, geom, gap_ft):
                other = (attrs.get("ParcelID") or "").strip()
                if not other or other in seen:
                    continue
                if first:
                    _sites_on(shape, nearby, gap_ft, res)
                if not owner or not same_owner(attrs.get("Owner"), owner):
                    continue
                seen.add(other)
                imp = _num(attrs.get("ImprAppr")) or 0.0
                # A house is not an operating site: residential land-use codes
                # (Fulton's 1xx) never count, however valuable.
                residential = (attrs.get("LUCode") or "").strip().startswith("1")
                if imp > built_value and not residential:
                    res.built.append(Built(other, _num(attrs.get("LandAcres")), imp))
                if len(seen) <= MAX_VISITED:
                    nxt.append(shape)
            first = False
        frontier = nxt
        if not frontier:
            break
    res.sites = sorted(set(res.sites))
    return res


def _sites_on(shape, site_points: list[tuple[Any, str]], gap_ft: float, res: Result) -> None:
    proj = LocalProjection.around(shape)
    sm = proj(shape)
    for point, name in site_points:
        try:
            if proj(point).distance(sm) * FT_PER_M <= gap_ft:
                res.sites.append(name)
        except GEOSException:
            continue


def site_points(idx: dict[str, Any]) -> Sites:
    """(shape, name) of every listed site the run fetched (EPA and GA EPD
    HSI layers), named as checks.map_checks names them."""
    out = []
    for key in ("epa", "hsi"):
        layer = idx.get(key)
        if layer is None:
            continue
        for shape, a in layer.items:
            name = (a.get("Facility_Name") or a.get("LOCATION_ADDRESS") or "site").strip()
            out.append((shape, name))
    return Sites(out)


def attach(ranked: list[dict[str, Any]], url: str, *, enabled: bool, max_parcels: int,
           gap_ft: float, built_value: float, sites: Sites,
           workers: int, notes: list[str],
           log: Callable[[str], None] = lambda _m: None) -> None:
    """Put OWNER_KEY / SITES_KEY on each ranked parcel: a list when checked,
    None (with REASON_KEY) when switched off, outside the cap or failed.
    Called only after scores, flags and the sort."""
    todo = [p for i, p in enumerate(ranked, 1)
            if enabled and (not max_parcels or i <= max_parcels)]
    for p in ranked:
        p[OWNER_KEY] = p[SITES_KEY] = None
        p[REASON_KEY] = ("switched off" if not enabled
                         else f"only checked for the top {max_parcels} ([grade] "
                              "next_door_max_parcels)")
    if not todo:
        return

    def work(p: dict[str, Any]) -> Result | str:
        try:
            return check_one(p, url, gap_ft, built_value, sites)
        except arcgis.ArcGISError as exc:
            return f"the county parcel layer failed ({type(exc).__name__})"
        except GEOSException:
            return "a geometry fault"

    with ThreadPoolExecutor(max_workers=max(1, min(workers, MAX_WORKERS))) as pool:
        results = list(pool.map(work, todo))
    failed = 0
    for p, r in zip(todo, results):
        if isinstance(r, Result):
            p[OWNER_KEY], p[SITES_KEY] = r.built, r.sites
            p.pop(REASON_KEY, None)
        else:
            p[REASON_KEY] = r
            failed += 1
    log(f"  neighbours checked for {len(todo) - failed:,} of {len(todo):,} parcels")
    if failed:
        notes.append(f"NEIGHBOUR CHECK FAILED for {failed} of {len(todo)} parcels (county "
                     "parcel layer): their same-owner and next-door contamination items are "
                     "\"not checked\". Re-run later.")


def flag_lines(p: dict[str, Any]) -> list[str]:
    """Flag lines for the reports (call after attach)."""
    out = []
    built = p.get(OWNER_KEY) or []
    if built:
        shown = ", ".join(
            f"{b.parcel_id} ({b.acres:,.1f} ac, ${b.improvement_value:,.0f} improvements)"
            if b.acres else f"{b.parcel_id} (${b.improvement_value:,.0f} improvements)"
            for b in built[:3])
        more = f", and {len(built) - 3} more" if len(built) > 3 else ""
        out.append(f"Same owner, any size: the owner's name is on {len(built)} built-on "
                   f"parcel{'s' if len(built) != 1 else ''} touching this one, directly or "
                   f"through its other parcels: {shown}{more}. Possibly buffer or expansion "
                   "land of an operating site")
    for name in (p.get(SITES_KEY) or [])[:3]:
        out.append(f"a bordering parcel holds a listed contamination site: {name}")
    return out
