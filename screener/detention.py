"""Stormwater detention allowance (F7, reviews/34_screener_quick_wins).

For each ranked parcel, after scoring, the sort and the F6 building fit:

- ``detention_pct``: the copied detention pond's top-of-bank footprint
  (400 x 200 ft = 80,000 SF, about 1.84 acres) as a share of the parcel's
  open ground on the county map (``checks.map_checks``' ``_open_ground``,
  every part, map acres). Empty when there is no open ground or when
  detention is off or capped; kept when only the placement is not checked
  (the building fit off or capped), since it needs only the open ground.
- ``pond_fit``: whether that pond rectangle can be placed on the open
  ground left after the F6 envelope, inside the same 25 ft lot-line
  setback, with the copied rotated-rectangle search (``sitefit.fit_lengths``
  with one depth, 200 ft, and one length, 400 ft). States, kept apart in
  every output: "pond fits" / "no pond room found" / "not checked: <why>".
  When no envelope was found the pond is tried on the open ground alone and
  the text says so.

A ROUGH ALLOWANCE ONLY: one fixed prototype pond, no hydrology, no NOAA
Atlas 14 rainfall, no impervious-area runoff and no local detention rules.

Live-only, like earthwork and the fit: ``run.main`` calls ``attach_ponds``
only after the sort, and ``rank.py`` / ``scoring.py`` / ``screens.py`` never
see these keys (tests/test_detention.py spies on every call).

COPIED, not imported: the numbers come from root ``building_prototypes.json``
"detention" and the volume formula from root ``site_fit.detention_volume_cf``;
tests/test_detention.py compares them as text (skipped when root is absent).
Nothing here reads a root file at run time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import shapely
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from . import sitefit
from .geo import FT_PER_M, LocalProjection, acres

# Name stems of every F7 key (the static and spy guards import these).
KEY_STEMS = ("detention", "pond")
COLUMNS = ("detention_pct", "pond_fit")
POND_KEY = "_pond_polygon"      # WGS84 shapely Polygon, for the site plan (F12)
DETAIL_KEY = "_pond_detail"

FITS, NONE_FOUND, NOT_CHECKED = "pond fits", "no pond room found", "not checked"
NOT_CHECKED_PREFIX = "not checked: "

# --- Copied from root building_prototypes.json (schema_version 1, as_of
# 2026-11-05, source "operator SiteFit tool defaults"), key "detention":
# top-of-bank length and width, depth and side slope (horizontal per 1
# vertical), all in feet.
POND = {"L": 400, "W": 200, "depth": 6, "slope": 4}


def detention_volume_cf(top_l: float, top_w: float, depth: float, slope: float) -> float | None:
    """SiteFit detVolume (prismoidal): cubic feet, None when slopes meet."""
    bl, bw = top_l - 2 * slope * depth, top_w - 2 * slope * depth
    if bl <= 0 or bw <= 0:
        return None
    a1, a2 = top_l * top_w, bl * bw
    return depth / 3.0 * (a1 + a2 + math.sqrt(a1 * a2))


POND_SF = POND["L"] * POND["W"]                     # 80,000
POND_ACRES = POND_SF / 43_560.0                     # about 1.84
VOLUME_CF = detention_volume_cf(POND["L"], POND["W"], POND["depth"], POND["slope"])
VOLUME_ACRE_FT = VOLUME_CF / 43_560.0               # about 9.1
POND_SPECS = [(float(POND["W"]), [float(POND["L"])])]

CAVEAT = ("a rough allowance for one fixed prototype pond, not a stormwater design: no "
          "hydrology, no NOAA Atlas 14 rainfall, no runoff from the building's impervious "
          "area and no local detention rules; the pond may sit right against the envelope "
          "with no room left for access, grading or a buffer; the real pond may be larger, "
          "smaller, underground or shared")


@dataclass
class Pond:
    state: str                          # FITS / NONE_FOUND / NOT_CHECKED
    detention_pct: float | None = None
    beside_envelope: bool = False       # placed with the F6 envelope in place
    reason: str = ""
    simplified: bool = False
    pond: BaseGeometry | None = None    # WGS84, fits only

    def text(self) -> str:
        if self.state == FITS:
            return FITS if self.beside_envelope else f"{FITS} (no building envelope placed)"
        if self.state == NONE_FOUND:
            return NONE_FOUND
        return NOT_CHECKED_PREFIX + self.reason

    def fields(self) -> dict[str, Any]:
        """The report fields (attached after the sort, never before)."""
        out = {"detention_pct": self.detention_pct, "pond_fit": self.text(), DETAIL_KEY: self}
        if self.pond is not None:
            out[POND_KEY] = self.pond
        return out


def not_checked(reason: str, pct: float | None = None) -> Pond:
    return Pond(NOT_CHECKED, detention_pct=pct, reason=reason)


def _can_hold_pond(part_ft: Polygon) -> bool:
    return part_ft.area >= POND_SF and sitefit._min_width(part_ft) >= min(POND["L"], POND["W"])


def place_pond(geometry: BaseGeometry, open_ground: BaseGeometry | None,
               constrained_pct: float | None, fit: sitefit.Fit | None,
               envelope: BaseGeometry | None,
               setback_ft: float = sitefit.DEFAULT_SETBACK_FT) -> Pond:
    """One parcel. ``fit`` is the parcel's F6 result and ``envelope`` its
    WGS84 envelope polygon. Only GEOS faults are caught."""
    try:
        return _place(geometry, open_ground, constrained_pct, fit, envelope, setback_ft)
    except GEOSException as exc:
        return not_checked(f"geometry fault ({str(exc)[:80]})")


def _place(geometry, open_ground, constrained_pct, fit, envelope, setback_ft) -> Pond:
    if open_ground is None:
        open_ground = geometry
    no_open = open_ground is geometry and (constrained_pct or 0) > 0
    proj = LocalProjection.around(geometry)
    open_m = [] if no_open else sitefit.polygon_parts(proj(open_ground))
    open_ac = sum(acres(q) for q in open_m)
    # The share needs only the open ground, so it is kept even when the
    # placement is not checked (the building fit off or capped).
    pct = round(100.0 * POND_ACRES / open_ac, 1) if open_ac > 0 else None
    if fit is None or fit.state == sitefit.NOT_CHECKED:
        return not_checked("building fit not checked", pct)
    if fit.state == sitefit.FITS and envelope is None:
        return not_checked("building envelope missing", pct)
    if pct is None:
        return Pond(NONE_FOUND, reason="no open ground on the parcel map")
    beside = fit.state == sitefit.FITS

    inner = proj(geometry).buffer(-setback_ft / FT_PER_M)
    region = shapely.intersection(MultiPolygon(open_m) if len(open_m) > 1 else open_m[0], inner)
    region_ft = sitefit._to_feet(region)
    if beside:
        region_ft = region_ft.difference(sitefit._to_feet(proj(envelope)))
    parts = [q for q in sitefit.polygon_parts(region_ft) if _can_hold_pond(q)]
    simplified = False
    if sitefit._vertices(parts) > sitefit._SIMPLIFY_ABOVE_VERTICES:
        # Same bound as the fit: simplify inside an extra inward buffer, so
        # the simplified shape stays inside the original.
        tol = sitefit._SIMPLIFY_TOL_FT
        shrunk = shapely.union_all(parts).buffer(-2 * tol)
        parts = [q for q in sitefit.polygon_parts(shrunk.simplify(tol, preserve_topology=True))
                 if _can_hold_pond(q)]
        simplified = True
        n = sitefit._vertices(parts)
        if n > sitefit._MAX_FIT_VERTICES:
            return not_checked(f"open-ground shape too complex ({n} vertices > "
                               f"{sitefit._MAX_FIT_VERTICES})", pct)
    where = "beside the envelope" if beside else "on the open ground"
    if not parts:
        return Pond(NONE_FOUND, detention_pct=pct, beside_envelope=beside, simplified=simplified,
                    reason=f"no open-ground part {where} can hold the pond")
    rings, origin = sitefit.normalise(parts)
    got = sitefit.fit_lengths(rings, POND_SPECS)[float(POND["W"])]
    if got is None:
        return Pond(NONE_FOUND, detention_pct=pct, beside_envelope=beside, simplified=simplified,
                    reason=f"no pond placement found {where}")
    length, pl = got
    pond_ft = Polygon(sitefit.envelope_corners(length, float(POND["W"]), pl, origin))
    return Pond(FITS, detention_pct=pct, beside_envelope=beside, simplified=simplified,
                pond=sitefit._to_wgs84(pond_ft, proj))


def brief_line(p: dict[str, Any]) -> str:
    """The brief's detention line (hand-written wording only)."""
    d: Pond | None = p.get(DETAIL_KEY)
    if d is None:
        return "- Detention pond: not checked (detention off)"
    if d.state == NOT_CHECKED:
        share = (f"; the {POND_ACRES:.2f}-acre pond would be {d.detention_pct:.1f}% of the "
                 f"open ground" if d.detention_pct is not None else "")
        return f"- Detention pond: not checked ({d.reason}){share}; {CAVEAT}"
    pond = (f"a {POND['L']} x {POND['W']} ft pond (top of bank; {POND['depth']} ft deep, "
            f"{POND['slope']}:1 side slopes, about {VOLUME_ACRE_FT:.1f} acre-feet; "
            f"{POND_ACRES:.2f} acres")
    pond += (f", {d.detention_pct:.1f}% of the open ground)" if d.detention_pct is not None
             else ")")
    where = (f"inside the {sitefit.DEFAULT_SETBACK_FT} ft lot-line setback, on open ground "
             f"(the same open ground as the building fit; steep ground is not located)")
    if d.state == FITS:
        if d.beside_envelope:
            head = (f"- Detention pond (rough allowance): {pond} fits on the open ground left "
                    f"beside the fitted building envelope")
        else:
            head = (f"- Detention pond (rough allowance): {pond} fits on the open ground; no "
                    f"building envelope was placed, so this does not show room for both")
    else:
        head = (f"- Detention pond (rough allowance): no pond room found for {pond} "
                f"({d.reason}). The search is a lower bound, not proof there is no room")
        if d.beside_envelope:
            head += ("; the envelope was fitted first at its largest length, so a shorter "
                     "building may leave room")
    line = f"{head}. Checked {where}; {CAVEAT}"
    if d.simplified:
        line += f"; the shape was simplified ({sitefit._SIMPLIFY_TOL_FT:g} ft) for the search"
    return line
