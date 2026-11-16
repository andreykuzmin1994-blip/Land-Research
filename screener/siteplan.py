"""Screening-level industrial site plans for chosen parcels (planner v4).

For a parcel (or an assemblage of parcels) from a finished run, this draws
one explicit industrial cross-section on the open ground inside DRAFT
zoning setbacks and buffers, and reports what it holds: building size,
dock doors, trailer and car stalls, truck and car routes from two
driveways on the GDOT frontage, detention ponds sized from the impervious
area, a drop-trailer yard, an optional second building, grading, and a
list of flags. It is a screening test fit from public data, not a survey,
a civil design or a zoning determination.

Port of the session's v4 prototype (reviews/37_screener_site_planner/
01_risk_review.md lists every risk the port addresses). The design basis
is in screener/SITE_PLANNING.md: the research spec, its critic and two
rounds of engineering review of drawn plans.

Layout of the code (so the core is testable offline):

- this module is pure geometry: it takes plain data (``SiteInputs``) and
  never touches the network. Its CLI entry (``main``) imports the fetch
  layer only inside the function.
- ``siteplan_inputs`` does every fetch (county parcels and neighbours,
  HIFLD transmission lines, USGS 3DEP elevation, GDOT frontage, the map
  layers) and says for each input whether it was checked.
- ``siteplan_render`` draws the SVG.

A plan is one cross-section placed with ``sitefit.fit_lengths`` (the
rectangle search copied from the root site fit):

- rear-load, cars along the office wall: car rows (the wall-side aisle is
  the aerial fire lane) + depth D + 130 ft court + optional 55 ft trailer
  row; a 40 ft truck road at each end;
- rear-load, cars at the office end: a 31 ft fire lane along the office
  wall, the car field at the office end, a truck road at the far end;
- cross-dock: trailer row + court + D + court + trailer row; a truck road
  joins the courts at one end; the car field at the office end.

DRAFT ZONING. Every setback, buffer, parking code, coverage cap and height
cap is in ``JUR_DRAFT``, one table whose every entry has status "draft"
(mostly from code mirrors in the research spec). The root's human-owned
zoning rules are the authority; nothing here reads them. Every record
carries ``zoning.status == "draft"`` and the ``ZONING_DRAFT`` flag, and
every SVG says DRAFT.

OPERATOR DECISIONS still open (named constants, each a CLI option, and
recorded in every plan's ``operator_decisions``):

- ``PARKING_RULE`` = "max": cars required = max(draft code minimum, market
  ratio). ``MARKET_PARKING_TIERS``: 0.55 per 1,000 SF at 250k SF and up,
  0.70 from 100k, 1.0 below (``--parking {max,code,market}``).
- ``ATLANTA_PARKING`` = "market": Atlanta uses the market ratio while its
  1/600 SF code minimum is unverified; flagged ``ATL_PARKING_CODE``
  (``--atlanta-parking {market,code}``).
- ``TRAILER_TARGET_TIERS``: 0.5 trailer stalls per dock door below 250k
  SF, 1.0 below 450k SF, 1.3 above (``--trailer-ratio``).
- ``PLANNER_PROTOTYPES`` = ("rear", "cross"): the operator's building
  prototypes are read (read-only) from ``sitefit.PROTOTYPES``; front-load
  is not drawn. ``MARKET_REAR_DEPTHS`` / ``MARKET_CROSS_DEPTHS`` are market
  comps (operator=False) that fill the 166k-312k SF gap.
- ``OTHER_EDGE_MIN_PAVE_FT`` = 10: a landscape strip on other lot lines.

BEHAVIOUR. ``--prototype-behaviour`` reproduces the v4 prototype exactly
(the parity proof). The default adds the reviewed fixes, each a listed
delta in reviews/37: feasibility follows a declared flag rule (R4),
coverage and height caps per alternative (R4), route and plan geometry
checks (R16, R17), impervious area recomputed after the drop-yard links
and only placed yards counted (R17), water buffers built before clipping
to the parcel (R21), assemblage seams closed (R13), dual soil groups read
as the undrained group (R24). Missing inputs fail closed in both modes
(R2): ``feasible`` is None ("incomplete"), never a clean result.

Owner names and mailing addresses never enter a plan: assemblages are
explicit parcel-id lists, no query asks for an owner field, and
``owner_guard`` scans the final bytes of every file before it is written.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import heapq
import json
import math
import os
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import shapely
import shapely.affinity
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, nearest_points, snap, unary_union
from shapely.prepared import prep

from . import sitefit
from .geo import FT_PER_M, LocalProjection

SCHEMA_VERSION = 1
SQFT_PER_AC = 43560.0

# ---------------------------------------------------------------------------
# Cross-section dimensions, ft (research spec section 1; critic corrections)
# ---------------------------------------------------------------------------
COURT = 130            # maneuvering court, dock face to trailer row
TRAILER_ROW = 55
TRUCK_ROAD = 40        # 30 ft two-way widened toward 40-45 at bends (critic A10)
CAR_ROW, CAR_AISLE, WALK = 18, 26, 5     # wall-side row + aisle = aerial fire lane (critic A4)
END_CLEAR = 60         # door-free wall at each end of a dock wall (critic A2)
DOOR_SP = 12           # dock door centres
SF_PER_DOOR = 5000
OFFICE_SHARE = 0.02
RIM = 15.0             # pond maintenance rim
POND_DEPTH, POND_Z, FREEBOARD = 6.0, 4.0, 1.0
# Detention: 1.15 x (k_imp x impervious + k_perv x disturbed pervious) ac-ft,
# the TR-55 shortcut on NOAA Atlas 14 depths (spec 1.6), +/-30%.
HSG_K = {"A": (0.30, 0.05), "B": (0.30, 0.07), "C": (0.23, 0.10), "D": (0.20, 0.10)}
DETENTION_FACTOR = 1.15
DISTURBED_FACTOR = 1.35          # disturbed area ~ 1.35 x impervious
POND_SPLIT_ACFT = (10.0, 20.0)   # 1 pond up to 10 ac-ft, 2 up to 20, else 3
GRID = 20.0            # route search cell, ft
GRID_MAX_CELLS = 60000
STEEP_COST = 3.0       # route cost multiplier on steep cells
DEM_STEP_FT = 40.0     # lidar sample spacing
STEEP_PCT = 15.0
DEM_PARTIAL_BELOW = 0.95         # share of samples answered
MIN_PART_SF = 40000    # paving parts smaller than this are not fitted
WATER_BUFFER_FT = 25.0
SECOND_MIN_AC = 3.5
SECOND_MIN_SF = 50000
MAX_SECONDS_PER_SITE = 120.0
SEAM_FT = 2.5          # assemblage seam closing radius (R13)
ROUTE_TOL_FT = 5.0     # route outside its walk area beyond this is a violation
CONFLICT_TOL_SF = 500.0
MAX_ASSEMBLE_IDS = 12

# ---------------------------------------------------------------------------
# Operator decisions (see the module docstring and SITE_PLANNING.md)
# ---------------------------------------------------------------------------
PARKING_RULE = "max"                       # max(code, market) | code | market
ATLANTA_PARKING = "market"                 # market | code (1/600 SF, unverified)
MARKET_PARKING_TIERS = ((250_000, 0.55), (100_000, 0.70), (0, 1.0))   # (SF at or above, per 1,000 SF)
TRAILER_TARGET_TIERS = ((250_000, 0.5), (450_000, 1.0), (None, 1.3))  # (SF below, per dock door)
PLANNER_PROTOTYPES = ("rear", "cross")     # front-load not drawn (operator decision)
MARKET_REAR_DEPTHS = (160, 190, 230, 270, 310)   # market comps, operator=False
MARKET_CROSS_DEPTHS = (470, 600)
OTHER_EDGE_MIN_PAVE_FT = 10
DUAL_HSG_USE = "undrained"                 # 'B/D' read as D (conservative); prototype read B

# ---------------------------------------------------------------------------
# DRAFT dimensional rules by jurisdiction (research spec 1.10; code mirrors,
# not verified). bldg/pave: offsets by edge kind. cover: (kind, cap).
# stall_w / island: stall width and landscape-island allowance.
# ---------------------------------------------------------------------------
_SPEC = "research spec 1.10 (code mirrors); not verified against the adopted code"
JUR_DRAFT: dict[str, dict[str, Any]] = {
    "south fulton": {"status": "draft", "source": _SPEC,
                     "bldg": {"ROW": 40, "RES_SIDE": 60, "RES_REAR": 110, "OTHER": 10},
                     "pave": {"ROW": 10, "RES_SIDE": 60, "RES_REAR": 110, "OTHER": 5},
                     "cover": None, "height": 100, "parking": "fulton", "stall_w": 9, "island": 0.88},
    "fulton industrial": {"status": "draft", "source": _SPEC,
                          "bldg": {"ROW": 40, "RES_SIDE": 60, "RES_REAR": 110, "OTHER": 10},
                          "pave": {"ROW": 10, "RES_SIDE": 60, "RES_REAR": 110, "OTHER": 5},
                          "cover": None, "height": 100, "parking": "fulton", "stall_w": 9, "island": 0.88},
    "fairburn": {"status": "draft", "source": _SPEC,
                 "bldg": {"ROW": 40, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 25},
                 "pave": {"ROW": 25, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 5},
                 "cover": ("building", 0.60), "height": 48, "parking": "fairburn", "stall_w": 10, "island": 0.92},
    "union city": {"status": "draft", "source": _SPEC,
                   "bldg": {"ROW": 50, "RES_SIDE": 95, "RES_REAR": 95, "OTHER": 20},
                   "pave": {"ROW": 10, "RES_SIDE": 75, "RES_REAR": 75, "OTHER": 5},
                   "cover": ("building+parking", 0.80), "height": 75, "parking": "union city", "stall_w": 9, "island": 1.0},
    "atlanta": {"status": "draft", "source": _SPEC,
                "bldg": {"ROW": 40, "RES_SIDE": 20, "RES_REAR": 20, "OTHER": 10},
                "pave": {"ROW": 10, "RES_SIDE": 20, "RES_REAR": 20, "OTHER": 0},
                "cover": None, "height": None, "parking": "atlanta", "stall_w": 9, "island": 1.0},
    "east point": {"status": "draft", "source": _SPEC,
                   "bldg": {"ROW": 40, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 8},
                   "pave": {"ROW": 10, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 5},
                   "cover": None, "height": 40, "parking": "fulton", "stall_w": 9, "island": 1.0},
    "college park": {"status": "draft", "source": _SPEC,
                     "bldg": {"ROW": 50, "RES_SIDE": 40, "RES_REAR": 40, "OTHER": 25},
                     "pave": {"ROW": 10, "RES_SIDE": 40, "RES_REAR": 40, "OTHER": 5},
                     "cover": ("impervious", 0.70), "height": 35, "parking": "college park", "stall_w": 9, "island": 1.0},
    "palmetto": {"status": "draft", "source": _SPEC,
                 "bldg": {"ROW": 50, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 20},
                 "pave": {"ROW": 10, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 5},
                 "cover": ("building", 0.70), "height": 40, "parking": "palmetto", "stall_w": 9, "island": 1.0},
}
DEFAULT_JURISDICTION = "south fulton"
ZONING_AUTHORITY = "the root's human-owned zoning rules are the authority"

# ---------------------------------------------------------------------------
# Flags. Severity 1: likely killed or cut; 2: verify; 3: information.
# ---------------------------------------------------------------------------
NOT_CHECKED_FLAGS = frozenset({
    "FRONTAGE_NOT_CHECKED", "FRONTAGE_GEOMETRY", "DEM_NOT_CHECKED", "DEM_PARTIAL",
    "NEIGHBOURS_NOT_CHECKED", "TRANSMISSION_NOT_CHECKED", "LAYERS_NOT_CHECKED"})
GATE_FLAGS = frozenset({"USE_PERMIT_REQUIRED"})   # entitlement gates: `gated`, not geometry
# Default behaviour: feasible == no severity-1 flag outside GATE_FLAGS (and no
# not-checked flag). POND_DOES_NOT_FIT is promoted to severity 1 there.
BLOCKING_RULE = "no severity-1 flag outside GATE_FLAGS"


class PlanError(ValueError):
    """A site cannot be planned from what was given (bad ids, no shape)."""


class OwnerDataFound(RuntimeError):
    """The owner guard found owner text in an artifact; nothing was written."""


class _BudgetExceeded(Exception):
    pass


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlanOptions:
    behaviour: str = "fixes"                       # "fixes" (default) | "prototype"
    parking: str = PARKING_RULE
    atlanta_parking: str = ATLANTA_PARKING
    trailer_tiers: tuple = TRAILER_TARGET_TIERS
    max_seconds: float = MAX_SECONDS_PER_SITE

    @property
    def fixes(self) -> bool:
        return self.behaviour != "prototype"

    def decisions(self) -> dict[str, Any]:
        return {"parking_rule": self.parking, "atlanta_parking": self.atlanta_parking,
                "market_parking_tiers": [list(t) for t in MARKET_PARKING_TIERS],
                "trailer_target_tiers": [list(t) for t in self.trailer_tiers],
                "prototypes": [f"{k} {int(sitefit.PROTOTYPES[k]['W'])}" for k in PLANNER_PROTOTYPES],
                "market_depths": {"rear": list(MARKET_REAR_DEPTHS), "cross": list(MARKET_CROSS_DEPTHS)},
                "other_edge_min_pave_ft": OTHER_EDGE_MIN_PAVE_FT,
                "dual_hsg": DUAL_HSG_USE if self.fixes else "drained (first letter)",
                "behaviour": self.behaviour}


def trailer_tiers_from(text: str) -> tuple:
    """--trailer-ratio: one number for every size, or three (below 250k,
    below 450k, above)."""
    vals = [float(x) for x in text.split(",") if x.strip()]
    if len(vals) == 1:
        vals = vals * 3
    if len(vals) != 3 or any(not (0 <= v <= 5) for v in vals):
        raise ValueError("--trailer-ratio takes one number or three, each 0-5")
    return tuple((lim, v) for (lim, _), v in zip(TRAILER_TARGET_TIERS, vals))


# ---------------------------------------------------------------------------
# Program rules
# ---------------------------------------------------------------------------
def jur_rules(name: str) -> tuple[str, dict[str, Any], bool]:
    """(rules name, a copy of the DRAFT rules, defaulted?)."""
    n = (name or "").lower()
    for k in JUR_DRAFT:
        if k in n:
            return k, copy.deepcopy(JUR_DRAFT[k]), False
    return f"{DEFAULT_JURISDICTION} (default)", copy.deepcopy(JUR_DRAFT[DEFAULT_JURISDICTION]), True


def parking_code(rule: str, sf: float) -> tuple[int, str]:
    """Draft code minimum and its basis."""
    office = OFFICE_SHARE * sf
    wh = sf - office
    if rule == "fulton":
        return math.ceil(wh / 2000 + office * 3 / 1000), "warehouse 1/2,000 SF + office 3/1,000 (Fulton / South Fulton / East Point code, draft)"
    if rule == "fairburn":
        n = wh / 2500 if wh <= 150000 else 60 + (wh - 150000) / 7500
        return math.ceil(n), "Fairburn warehouse formula (draft)"
    if rule == "union city":
        return math.ceil(sf / 2500), "employees proxy SF/2,500 (Union City, draft)"
    if rule == "college park":
        return math.ceil(0.10 * sf / 330), "College Park 10% of floor area / 330 (draft)"
    if rule == "palmetto":
        return math.ceil(sf / 1500), "Palmetto floor area / 1,500 (draft)"
    if rule == "atlanta":
        return math.ceil(sf / 600), "Atlanta 1/600 SF (draft; flag ATL_PARKING_CODE)"
    return 0, "none"


def parking_market(sf: float) -> int:
    for floor, ratio in MARKET_PARKING_TIERS:
        if sf >= floor:
            return math.ceil(ratio * sf / 1000)
    return 0


def parking_rule_for(jname: str, rules: dict[str, Any], opts: PlanOptions) -> str:
    if jname.startswith("atlanta") and opts.atlanta_parking == "market":
        return "market"
    return rules["parking"]


def required_cars(prule: str, sf: float, opts: PlanOptions) -> tuple[int, str]:
    if prule == "market" or opts.parking == "market":
        basis = ("market ratio (Atlanta's 1/600 code minimum unverified)" if prule == "market"
                 else "market ratio (operator option)")
        return parking_market(sf), basis
    req, txt = parking_code(prule, sf)
    if opts.parking == "code":
        return req, txt
    return max(req, parking_market(sf)), txt + f"; market {parking_market(sf)}"


def ada_count(n: int) -> int:
    for top, k in ((25, 1), (50, 2), (75, 3), (100, 4), (150, 5), (200, 6), (300, 7), (400, 8), (500, 9)):
        if n <= top:
            return k
    return math.ceil(0.02 * n) if n <= 1000 else 20 + math.ceil((n - 1000) / 100)


def trailer_target(sf: float, doors: int, tiers: tuple = TRAILER_TARGET_TIERS) -> int:
    for limit, k in tiers:
        if limit is None or sf < limit:
            return math.ceil(k * doors)
    return math.ceil(tiers[-1][1] * doors)


def pond_dims(v_acft: float, d: float = POND_DEPTH, z: float = POND_Z) -> tuple[float, float, float, float]:
    """Bottom width W0 (bottom length 2 W0) holding v_acft at depth d by the
    prismoidal formula; returns (W0, footprint length, footprint width, v)
    with freeboard and the rim included in the footprint."""
    v = v_acft * SQFT_PER_AC

    def vol(w0):
        a = lambda h: (2 * w0 + 2 * z * h) * (w0 + 2 * z * h)
        return d * (a(0) + a(d) + 4 * a(d / 2)) / 6
    lo, hi = 1.0, 2000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if vol(mid) < v else (lo, mid)
    w0 = hi
    ht = d + FREEBOARD
    wt, lt = w0 + 2 * z * ht, 2 * w0 + 2 * z * ht
    return w0, lt + 2 * RIM, wt + 2 * RIM, v_acft


def pond_volume(w0: float, d: float = POND_DEPTH, z: float = POND_Z) -> float:
    """Volume (cubic ft) of a pond with bottom w0 x 2 w0 (for tests)."""
    a = lambda h: (2 * w0 + 2 * z * h) * (w0 + 2 * z * h)
    return d * (a(0) + a(d) + 4 * a(d / 2)) / 6


def hsg_letter(hsg: str | None, fixes: bool) -> tuple[str, str | None]:
    """(soil group letter used, note when it was a choice)."""
    raw = (hsg or "B").strip().upper()
    if not fixes:
        return (raw[:1] if raw[:1] in HSG_K else "B"), None
    if re.fullmatch(r"[ABC]/D", raw):
        return ("D" if DUAL_HSG_USE == "undrained" else raw[0]), f"dual soil group {raw}: read as {'D (undrained, conservative)' if DUAL_HSG_USE == 'undrained' else raw[0]}"
    if raw in HSG_K:
        return raw, None
    return "B", "no soil group rated: B assumed"


def detention_storage(imp_ac: float, perv_ac: float, letter: str) -> float:
    ki, kp = HSG_K.get(letter, HSG_K["B"])
    return DETENTION_FACTOR * (ki * imp_ac + kp * perv_ac)


def pond_count(v: float) -> int:
    return 1 if v <= POND_SPLIT_ACFT[0] else 2 if v <= POND_SPLIT_ACFT[1] else 3


def corridor_halfwidth(volt: Any) -> tuple[float, bool]:
    """Assumed easement half-width (ft) by voltage, and whether the voltage
    was assumed (missing, negative or text: 115 kV)."""
    try:
        v = float(volt)
        assumed = not math.isfinite(v) or v <= 0
    except (TypeError, ValueError):
        v, assumed = 115.0, True
    if assumed:
        v = 115.0
    return (30 if v <= 69 else 50 if v <= 161 else 65 if v <= 230 else 90), assumed


def rnd(x: float, base: int) -> int:
    return int(base * round(float(x) / base))


# ---------------------------------------------------------------------------
# Geometry helpers (a local feet plane per site)
# ---------------------------------------------------------------------------
class Plane:
    """WGS84 <-> a local feet plane (geo.LocalProjection metres x FT_PER_M)."""

    def __init__(self, g: BaseGeometry):
        self.pj = LocalProjection.around(g)

    def ft(self, g: BaseGeometry) -> BaseGeometry:
        return shapely.transform(self.pj(g), lambda c: c * FT_PER_M)

    def wgs(self, gft: BaseGeometry) -> BaseGeometry:
        pj = self.pj

        def inv(c):
            o = c / FT_PER_M
            o[:, 0] = o[:, 0] / pj.kx + pj.lon0
            o[:, 1] = o[:, 1] / pj.ky + pj.lat0
            return o
        return shapely.transform(gft, inv)


def parts(g: BaseGeometry | None) -> list[Polygon]:
    return [p for p in sitefit.polygon_parts(g) if p.area > 1]


def clean(g: BaseGeometry, tol: float = 15.0) -> BaseGeometry:
    """Valid polygonal area with slivers under 2 x tol removed (an opening)."""
    g = shapely.make_valid(g)
    g = unary_union([p for p in sitefit.polygon_parts(g)])
    return g.buffer(-tol).buffer(tol) if not g.is_empty else g


def site_parcel(plane: Plane, g_wgs: BaseGeometry, assembled: bool, opts: PlanOptions) -> tuple[BaseGeometry, float]:
    """The parcel in feet, and the area closed across assemblage seams (SF)."""
    parcel = clean(plane.ft(shapely.make_valid(g_wgs)), 1.0)
    if assembled and opts.fixes and not parcel.is_empty:
        # Closing (dilate, then erode) joins members across sub-5 ft seams
        # without moving the outer line (R13).
        closed = unary_union(sitefit.polygon_parts(parcel.buffer(SEAM_FT, join_style=2).buffer(-SEAM_FT, join_style=2)))
        closed = unary_union([closed, parcel])
        return closed, max(0.0, closed.area - parcel.area)
    return parcel, 0.0


class Frame:
    """Module-local (u along the length from the module's left end, v across
    the depth from side 0) to the feet plane; flipv/flipu mirror the module."""

    def __init__(self, Lmod, depth, pl, origin, flipv=False, flipu=False):
        self.Lmod, self.depth, self.pl, self.origin = Lmod, depth, pl, origin
        self.flip, self.flipu = flipv, flipu
        a = math.radians(pl.angle_deg)
        self.c, self.s = math.cos(a), math.sin(a)

    def pt(self, u, v):
        if self.flipu:
            u = self.Lmod - u
        if self.flip:
            v = self.depth - v
        x = self.pl.xc - self.Lmod / 2.0 + u
        y = self.pl.y0 + v
        ox, oy = self.origin
        return (x * self.c - y * self.s + ox, x * self.s + y * self.c + oy)

    def rect(self, u0, u1, v0, v1):
        return Polygon([self.pt(u0, v0), self.pt(u1, v0), self.pt(u1, v1), self.pt(u0, v1)])

    def line(self, u0, v0, u1, v1):
        return LineString([self.pt(u0, v0), self.pt(u1, v1)])


# ---------------------------------------------------------------------------
# Elevation (integer-keyed sample grid, R20)
# ---------------------------------------------------------------------------
@dataclass
class Dem:
    step: float = DEM_STEP_FT
    points: dict = field(default_factory=dict)     # (i, j) -> (x, y) feet
    elev: dict = field(default_factory=dict)       # (i, j) -> z feet, in answer order
    state: str = "checked"                         # checked | partial | not_checked
    reason: str = ""

    def coords(self) -> Iterable[tuple[tuple[float, float], float]]:
        for ij, z in self.elev.items():
            yield self.points[ij], z

    @property
    def coverage(self) -> float:
        return len(self.elev) / len(self.points) if self.points else 0.0


def dem_points(region_ft: BaseGeometry, step: float = DEM_STEP_FT) -> list[tuple[tuple[int, int], tuple[float, float]]]:
    """The sampling grid: cell centres over the bounds, kept within one step
    of the region. Coordinates are built by repeated addition exactly as the
    prototype did (so the requests and the cache keys stay the same); the
    keys are integer (i, j)."""
    if region_ft.is_empty:
        return []
    minx, miny, maxx, maxy = region_ft.bounds
    pts = []
    y, j = miny + step / 2, 0
    while y < maxy:
        x, i = minx + step / 2, 0
        while x < maxx:
            pts.append(((i, j), (x, y)))
            x += step
            i += 1
        y += step
        j += 1
    keep = prep(region_ft.buffer(step))
    return [(ij, p) for ij, p in pts if keep.contains(Point(p))]


def steep_cells(dem: Dem, threshold: float = STEEP_PCT, float_keys: bool = False) -> BaseGeometry:
    """Cells whose central-difference slope exceeds threshold percent.

    float_keys reproduces the prototype, which looked neighbours up by
    float coordinates (x - step need not equal the previous x exactly); the
    default uses the integer keys."""
    step = dem.step
    cells = []
    if float_keys:
        by_xy = {dem.points[ij]: z for ij, z in dem.elev.items()}
        for (x, y), z in by_xy.items():
            gx = [by_xy.get((x + step, y)), by_xy.get((x - step, y))]
            gy = [by_xy.get((x, y + step)), by_xy.get((x, y - step))]
            if None in gx or None in gy:
                continue
            if 100 * math.hypot((gx[0] - gx[1]) / (2 * step), (gy[0] - gy[1]) / (2 * step)) > threshold:
                cells.append(box(x - step / 2, y - step / 2, x + step / 2, y + step / 2))
    else:
        e = dem.elev
        for (i, j), z in e.items():
            gx = [e.get((i + 1, j)), e.get((i - 1, j))]
            gy = [e.get((i, j + 1)), e.get((i, j - 1))]
            if None in gx or None in gy:
                continue
            if 100 * math.hypot((gx[0] - gx[1]) / (2 * step), (gy[0] - gy[1]) / (2 * step)) > threshold:
                x, y = dem.points[(i, j)]
                cells.append(box(x - step / 2, y - step / 2, x + step / 2, y + step / 2))
    return unary_union(cells) if cells else Polygon()


# ---------------------------------------------------------------------------
# Edge classification and envelopes
# ---------------------------------------------------------------------------
def classify_edges(parcel_ft, plane, neighbours, front_ft):
    """Boundary pieces labelled ROW / RES_SIDE / RES_REAR / OTHER."""
    edge = parcel_ft.boundary
    out = []
    row = edge.intersection(front_ft.buffer(5)) if front_ft is not None and not front_ft.is_empty else LineString()
    if not row.is_empty:
        out.append(("ROW", row))
    rest = edge.difference(front_ft.buffer(5)) if not row.is_empty else edge
    res_zone = []
    for a, s in neighbours:
        lu, cls = str(a.get("LUCode") or ""), str(a.get("ClassCode") or "")
        if lu.startswith("1") or cls.startswith("R"):
            res_zone.append(plane.ft(s).buffer(20))
    res_u = unary_union(res_zone) if res_zone else None
    if res_u is not None:
        res = rest.intersection(res_u)
        rest = rest.difference(res_u)
        if not row.is_empty:
            dmax = max(row.distance(Point(c)) for q in sitefit.polygon_parts(parcel_ft) for c in q.exterior.coords)
            for seg in getattr(res, "geoms", [res]):
                # Points from touching lines carry no length (the prototype
                # crashed on them in interpolate).
                if seg.is_empty or seg.geom_type not in ("LineString", "MultiLineString", "LinearRing"):
                    continue
                mid = seg.interpolate(0.5, normalized=True)
                kind = "RES_REAR" if row.distance(mid) > 0.5 * dmax else "RES_SIDE"
                out.append((kind, seg))
        else:
            out.append(("RES_REAR", res))
    if not rest.is_empty:
        out.append(("OTHER", rest))
    return out


def envelope(parcel_ft, edges, offsets):
    cut = [ln.buffer(offsets[k], cap_style=2, join_style=2) for k, ln in edges if offsets.get(k, 0) > 0 and not ln.is_empty]
    return parcel_ft.difference(unary_union(cut)) if cut else parcel_ft


# ---------------------------------------------------------------------------
# Variants (operator prototypes from sitefit, read-only; market comps)
# ---------------------------------------------------------------------------
def _row_text(row: bool, cross: bool) -> str:
    if cross:
        return "courts + trailer rows" if row else "courts only"
    return "court + trailer row" if row else "court only"


def variants() -> list[dict[str, Any]]:
    """Every cross-section tried, in the prototype's order. The operator's
    rear-load and cross-dock come from sitefit.PROTOTYPES (depth W, lengths
    min_length_ft..L); front-load is not drawn (PLANNER_PROTOTYPES)."""
    out = []
    for D in MARKET_REAR_DEPTHS:
        for row in (True, False):
            for cars in ("side", "end"):
                out.append({"type": "rear", "D": D, "row": row, "cars": cars, "operator": False,
                            "name": f"rear-load {D} deep, {_row_text(row, False)}, cars {'along office wall' if cars == 'side' else 'at office end'}"})
    if "rear" in PLANNER_PROTOTYPES:
        pr = sitefit.PROTOTYPES["rear"]
        D = int(pr["W"])
        for row in (True, False):
            out.append({"type": "rear", "D": D, "row": row, "cars": "side", "operator": True,
                        "Lrange": (int(pr["min_length_ft"]), int(pr["L"])),
                        "name": f"operator rear-load {D}, {_row_text(row, False)}, cars along office wall"})
    pc = sitefit.PROTOTYPES["cross"] if "cross" in PLANNER_PROTOTYPES else None
    op_d = int(pc["W"]) if pc else None
    for D in sorted(set(MARKET_CROSS_DEPTHS) | ({op_d} if op_d else set())):
        for row in (True, False):
            v = {"type": "cross", "D": D, "row": row, "cars": "end", "operator": D == op_d,
                 "name": f"{'operator ' if D == op_d else ''}cross-dock {D} deep, {_row_text(row, True)}, cars at office end"}
            if D == op_d:
                v["Lrange"] = (int(pc["min_length_ft"]), int(pc["L"]))
            out.append(v)
    return out


def length_range(v: dict[str, Any]) -> tuple[int, int]:
    if v["operator"]:
        return v["Lrange"]
    D = v["D"]
    k = 1.6 if D <= 190 else 2.0
    return max(220, int(k * D) // 20 * 20), min(1600, int(4.5 * D) // 20 * 20)


def car_rows_layout(r: int) -> tuple[list[float], float]:
    """Row offsets and total depth for r car rows: the wall row + its aisle
    (the aerial fire lane), then rows alternately facing the previous aisle
    or back to back with a new 24 ft aisle."""
    rows, v = [WALK], WALK + CAR_ROW + CAR_AISLE
    for k in range(2, r + 1):
        rows.append(v)
        v += CAR_ROW
        if k % 2 == 1:
            v += 24
    return rows, v


def geometry_of(v, L, r):
    """(module depth, module length) for variant v, building length L, r car rows."""
    D = v["D"]
    row = TRAILER_ROW if v["row"] else 0
    cd = car_rows_layout(r)[1]
    if v["type"] == "rear":
        if v["cars"] == "side":
            return cd + D + COURT + row, L + 2 * TRUCK_ROAD
        fs = WALK + CAR_AISLE
        return fs + D + COURT + row, cd + L + TRUCK_ROAD
    return row + COURT + D + COURT + row, TRUCK_ROAD + L + TRUCK_ROAD + cd


def car_per_row(v, L, sw):
    if v["type"] == "rear" and v["cars"] == "side":
        return int((L - 48) // sw)
    if v["type"] == "rear":
        return int((WALK + CAR_AISLE + v["D"] - 24) // sw)
    return int((v["D"] - 48) // sw)


@dataclass
class Program:
    """What the site's draft rules and the operator options say."""
    jname: str
    rules: dict[str, Any]
    prule: str
    opts: PlanOptions

    @property
    def stall_w(self) -> int:
        return self.rules["stall_w"]

    @property
    def island(self) -> float:
        return self.rules["island"]


@dataclass
class Plan:
    v: dict
    L: float
    r: int
    frame: Frame
    module: Polygon
    building: Polygon
    courts: list
    rows: list
    cars: list
    roads: list
    offices: list
    ticks: list
    doors_geom: list
    truck_entries: list
    car_entries: list
    fallback_entries: list
    sf: int
    doors: int
    court_stalls: int
    car_cap: int
    cars_required: int
    cars_rule: str
    trailers_target: int
    clear: int


def build(v, L, r, fr: Frame, prog: Program) -> Plan:
    D = v["D"]
    rowd = TRAILER_ROW if v["row"] else 0
    depth, Lmod = fr.depth, fr.Lmod
    P = lambda u, w: Point(fr.pt(u, w))
    courts, rows, cars, roads, offices, ticks, doors_g, tin, cin = [], [], [], [], [], [], [], [], []
    sw = prog.stall_w
    sf = int(round(L * D))
    walls = 1 if v["type"] == "rear" else 2
    cap = max(0, int((L - 2 * END_CLEAR) // DOOR_SP))
    doors = min(round(sf / SF_PER_DOOR), cap * walls)
    target = trailer_target(sf, doors, prog.opts.trailer_tiers)
    if v["type"] == "rear":
        cd = car_rows_layout(r)[1]
        if v["cars"] == "side":
            uL, vB = TRUCK_ROAD, cd
            ue0, ue1 = 0, Lmod
            cars.append(fr.rect(uL, uL + L, 0, cd))
            for vr in car_rows_layout(r)[0]:
                for u in range(int(uL + 24), int(uL + L - 24), int(sw * 2)):
                    ticks.append(fr.line(u, vr, u, vr + CAR_ROW))
            cin.append(P(uL + L / 2, 0))
            # end roads with a lead-in beside the car field (curbed)
            roads += [fr.rect(0, uL, 0, vB + D), fr.rect(uL + L, Lmod, 0, vB + D)]
            pod = min(80.0, math.sqrt(OFFICE_SHARE * L * D / 2))
            offices += [fr.rect(uL, uL + pod, vB, vB + pod), fr.rect(uL + L - pod, uL + L, vB, vB + pod)]
        else:
            fs = WALK + CAR_AISLE
            uL, vB = cd, fs
            ue0, ue1 = 0, Lmod
            cars.append(fr.rect(0, cd, 0, fs + D))
            for vr in car_rows_layout(r)[0]:
                u0 = cd - vr - CAR_ROW
                for vv in range(24, int(fs + D - 24), int(sw * 2)):
                    ticks.append(fr.line(u0, vv, u0 + CAR_ROW, vv))
            cin.append(P(cd / 2, 0))
            # fire lane along the office wall; far end road
            roads += [fr.rect(cd, cd + L, 0, fs), fr.rect(cd + L, Lmod, 0, vB + D)]
            pod = min(90.0, math.sqrt(OFFICE_SHARE * L * D))
            offices.append(fr.rect(uL, uL + pod, vB, vB + pod))
        b = fr.rect(uL, uL + L, vB, vB + D)
        courts.append(fr.rect(ue0, ue1, vB + D, vB + D + COURT))
        court_stalls = 0
        if rowd:
            n = min(int((L - 80) // 12), target)
            rl = n * 12
            u0 = uL + (L - rl) / 2
            rows.append(fr.rect(u0, u0 + rl, vB + D + COURT, depth))
            for k in range(n + 1):
                ticks.append(fr.line(u0 + 12 * k, vB + D + COURT, u0 + 12 * k, depth))
            court_stalls = n
        tin += [P(ue0, vB + D + COURT / 2), P(ue1, vB + D + COURT / 2)]
        fb = ([P(TRUCK_ROAD / 2, 0), P(Lmod - TRUCK_ROAD / 2, 0)] if v["cars"] == "side" else [P(Lmod - TRUCK_ROAD / 2, 0)])
        dock_v = [(vB + D, 1)]
    else:
        cd = car_rows_layout(r)[1]
        uL, vB = TRUCK_ROAD, rowd + COURT
        b = fr.rect(uL, uL + L, vB, vB + D)
        courts += [fr.rect(0, uL + L + TRUCK_ROAD, rowd, rowd + COURT), fr.rect(0, uL + L + TRUCK_ROAD, vB + D, vB + D + COURT)]
        court_stalls = 0
        if rowd:
            n = min(int((L - 80) // 12), math.ceil(target / 2))
            rl = n * 12
            u0 = uL + (L - rl) / 2
            rows += [fr.rect(u0, u0 + rl, 0, rowd), fr.rect(u0, u0 + rl, depth - rowd, depth)]
            for k in range(n + 1):
                ticks += [fr.line(u0 + 12 * k, 0, u0 + 12 * k, rowd), fr.line(u0 + 12 * k, depth - rowd, u0 + 12 * k, depth)]
            court_stalls = 2 * n
        roads += [fr.rect(0, uL, vB, vB + D), fr.rect(uL + L, uL + L + TRUCK_ROAD, 0, depth)]
        cars.append(fr.rect(uL + L + TRUCK_ROAD, Lmod, vB, vB + D))
        for vr in car_rows_layout(r)[0]:
            u0 = uL + L + TRUCK_ROAD + vr
            for vv in range(int(vB + 24), int(vB + D - 24), int(sw * 2)):
                ticks.append(fr.line(u0, vv, u0 + CAR_ROW, vv))
        tin += [P(0, rowd + COURT / 2), P(0, depth - rowd - COURT / 2)]
        fb = [P(uL + L + TRUCK_ROAD / 2, 0), P(uL + L + TRUCK_ROAD / 2, depth)]
        cin.append(P(Lmod, vB + D / 2))
        pod = min(90.0, math.sqrt(OFFICE_SHARE * L * D / 2))
        offices += [fr.rect(uL + L - pod, uL + L, vB + D / 2 - pod, vB + D / 2), fr.rect(uL + L - pod, uL + L, vB + D / 2, vB + D / 2 + pod)]
        dock_v = [(vB, -1), (vB + D, 1)]
    per_wall = [doors // walls + (1 if k < doors % walls else 0) for k in range(walls)]
    for (vw, sg), nd in zip(dock_v, per_wall):
        for k in range(nd):
            u = uL + END_CLEAR + (L - 2 * END_CLEAR) * (k + 0.5) / max(nd, 1)
            doors_g.append(fr.line(u, vw, u, vw + sg * 12))
    req, rule = required_cars(prog.prule, sf, prog.opts)
    capc = int(r * car_per_row(v, L, sw) * prog.island)
    return Plan(v, L, r, fr, fr.rect(0, Lmod, 0, depth), b, courts, rows, cars, roads, offices, ticks, doors_g, tin, cin, fb,
                sf, doors, court_stalls, capc, req, rule, target, 32 if sf < 200000 else 36)


def orient_score(p: Plan, row_line, res_line) -> float:
    """Courts and trailer rows away from the street and (more) from
    residential edges; car and truck entries near the street."""
    s = 0.0
    for c in p.courts + p.rows:
        if row_line is not None:
            s += min(c.distance(row_line), 1500)
        if res_line is not None:
            s += 1.5 * min(c.distance(res_line), 1500)
    if row_line is not None:
        s -= 0.5 * min(p.car_entries[0].distance(row_line), 1500)
        s -= 0.3 * min(min(q.distance(row_line) for q in p.truck_entries), 1500)
    return s


class Search:
    """Per-site search state: memoised fits and the time budget (R19)."""

    def __init__(self, max_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.deadline = clock() + max_seconds
        self.memo: dict = {}
        self.fit_calls = 0
        self.exceeded = False

    def check(self) -> None:
        if self.clock() > self.deadline:
            self.exceeded = True
            raise _BudgetExceeded()

    def fit_lengths(self, rings, depth: float, lat: list[float]):
        key = (tuple(tuple(r) for r in rings), depth, tuple(lat))
        if key not in self.memo:
            self.check()
            self.fit_calls += 1
            self.memo[key] = sitefit.fit_lengths(rings, [(depth, lat)]).get(depth)
        return self.memo[key]


def fit_variant(v, P, B, prog: Program, row_line, res_line, search: Search, length_cap=None) -> Plan | None:
    """The longest building of variant v whose module fits P and whose
    building fits B, with its car demand met (more car rows if needed)."""
    pp = [q for q in parts(P) if q.area > MIN_PART_SF]
    if not pp:
        return None
    rings, origin = sitefit.normalise(pp)
    lo, hi = length_range(v)
    if length_cap is not None:
        hi = min(hi, length_cap)
    if hi < lo:
        return None
    Ls = list(range(lo, hi + 1, 20))
    for r in range(1, 14):
        depth, _ = geometry_of(v, Ls[0], r)
        lat = sorted({float(geometry_of(v, L, r)[1]) for L in Ls})
        cap, found = None, None
        for _ in range(60):
            lat_c = [x for x in lat if cap is None or x <= cap]
            if not lat_c:
                break
            got = search.fit_lengths(rings, float(depth), lat_c)
            if not got:
                break
            Lmod, pl = got
            L = Lmod - geometry_of(v, 0, r)[1]
            cands = []
            for fv in (False, True):
                for fu in (False, True):
                    fr = Frame(Lmod, depth, pl, origin, fv, fu)
                    p = build(v, L, r, fr, prog)
                    if B.buffer(0.5).contains(p.building):
                        cands.append((orient_score(p, row_line, res_line), p))
            if cands:
                found = max(cands, key=lambda t: t[0])[1]
                break
            smaller = [x for x in lat if x < Lmod]
            cap = smaller[-1] if smaller else None
            if cap is None:
                break
        if found is None:
            return None
        if found.car_cap >= found.cars_required:
            return found
    return None


# ---------------------------------------------------------------------------
# Frontage runs, driveways, routes, ponds, yards
# ---------------------------------------------------------------------------
class FrontageGeometryError(ValueError):
    pass


def _lines_of(g: BaseGeometry) -> list[LineString]:
    if g is None or g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    if hasattr(g, "geoms"):
        return [q for x in g.geoms for q in _lines_of(x)]
    return []


def frontage_runs(front_ft: BaseGeometry | None) -> list[LineString]:
    """Merged runs of frontage line, longest first. Points and other
    non-line parts of a GeometryCollection are dropped first (R18)."""
    if front_ft is None or front_ft.is_empty:
        return []
    if front_ft.geom_type not in ("LineString", "MultiLineString"):
        lines = _lines_of(front_ft)
        if not lines:
            return []
        front_ft = MultiLineString(lines) if len(lines) > 1 else lines[0]
    try:
        m = linemerge(snap(front_ft, front_ft, 3.0)) if front_ft.geom_type != "LineString" else front_ft
    except GEOSException as exc:
        raise FrontageGeometryError(str(exc)[:120]) from None
    runs = [ln for ln in getattr(m, "geoms", [m]) if ln.geom_type == "LineString" and ln.length > 1]
    return sorted(runs, key=lambda ln: -ln.length)


def spacing_for(road: str | None) -> float:
    """Driveway spacing (Fulton manual): 300 ft on collectors and arterials."""
    r = (road or "").lower()
    return 300.0 if any(k in r for k in ("arterial", "collector", "sr ", "us ")) else 250.0


def pick_driveways(runs, truck_target, car_target, spacing, taken=()):
    """T on the longest run nearest the court end (>= 110 ft from run ends
    for the 75 ft radii); C >= spacing from T and from existing driveways,
    nearest the car entry, preferring separation."""
    ok_runs = [ln for ln in runs if ln.length >= 220]
    if not ok_runs:
        return None, None, False

    def tscore(ln):
        return ln.distance(truck_target) - 0.15 * ln.length
    best = min(ok_runs, key=tscore)
    s = best.project(truck_target)
    tp = best.interpolate(min(max(s, 110), best.length - 110))
    if any(tp.distance(q) < spacing for q in taken):
        alts = [best.interpolate(k) for k in range(110, int(best.length) - 109, 20)]
        alts = [q for q in alts if all(q.distance(t) >= spacing for t in taken)]
        tp = min(alts, key=lambda q: q.distance(truck_target)) if alts else None
    cp = None
    if tp is not None:
        cands = []
        for ln in runs:
            if ln.length < 110:
                continue
            for k in range(55, int(ln.length) - 54, 15):
                q = ln.interpolate(k)
                if q.distance(tp) >= spacing and all(q.distance(t) >= spacing for t in taken):
                    cands.append((q.distance(car_target) - 0.1 * min(q.distance(tp), 1000), q))
        if cands:
            cp = min(cands, key=lambda t: t[0])[1]
    return tp, cp, True


def grid_route(start, goal, walk, cost_zone=None, step=GRID, stats: dict | None = None, simplify: float = 0.9):
    """Shortest 8-connected path over `walk` from start to goal (Dijkstra,
    with steep cells costing STEEP_COST times more and a small bend
    penalty). Returns a LineString or None. Kept byte-equal to the
    prototype for parity; the diagonal cost is a listed later delta (R16)."""
    if walk.is_empty:
        return None
    minx, miny, maxx, maxy = walk.bounds
    nx, ny = int((maxx - minx) / step) + 1, int((maxy - miny) / step) + 1
    if nx * ny > GRID_MAX_CELLS:
        step *= math.sqrt(nx * ny / float(GRID_MAX_CELLS))
        nx, ny = int((maxx - minx) / step) + 1, int((maxy - miny) / step) + 1
    if stats is not None:
        stats["step"] = max(stats.get("step", 0.0), step)
    pw = prep(walk)
    prepc = prep(cost_zone) if cost_zone is not None and not cost_zone.is_empty else None
    ok, cost = {}, {}

    def cell(i, j):
        if (i, j) not in ok:
            pt = Point(minx + i * step, miny + j * step)
            ok[(i, j)] = pw.contains(pt)
            cost[(i, j)] = STEEP_COST if (prepc is not None and prepc.contains(pt)) else 1.0
        return ok[(i, j)]

    def idx(p):
        return (int(round((p.x - minx) / step)), int(round((p.y - miny) / step)))

    def nearest_ok(p):
        i0, j0 = idx(p)
        for rad in range(0, 8):
            for di in range(-rad, rad + 1):
                for dj in range(-rad, rad + 1):
                    if max(abs(di), abs(dj)) == rad and 0 <= i0 + di < nx and 0 <= j0 + dj < ny and cell(i0 + di, j0 + dj):
                        return (i0 + di, j0 + dj)
        return None
    s, g = nearest_ok(start), nearest_ok(goal)
    if s is None or g is None:
        return None
    dist = {s: 0.0}
    prev = {}
    pq = [(0.0, s)]
    nb = [(1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == g:
            break
        if d > dist.get(u, 1e18):
            continue
        for di, dj, w in nb:
            q = (u[0] + di, u[1] + dj)
            if not (0 <= q[0] < nx and 0 <= q[1] < ny) or not cell(*q):
                continue
            nd = d + w * step * (cost[u] + cost[q]) / 2 + (step * 0.6 if di and dj else 0)   # a little bend penalty
            if nd < dist.get(q, 1e18):
                dist[q] = nd
                prev[q] = u
                heapq.heappush(pq, (nd, q))
    if g not in dist:
        return None
    path = [g]
    while path[-1] != s:
        path.append(prev[path[-1]])
    pts = [(minx + i * step, miny + j * step) for i, j in reversed(path)]
    pts = [(start.x, start.y)] + pts + [(goal.x, goal.y)]
    line = LineString(pts)
    return line.simplify(step * simplify) if simplify else line


def route_outside_ft(route: LineString, walk: BaseGeometry) -> float:
    """How far (ft) a route strays outside its walk area at worst (R16),
    sampled every 2 ft along the parts outside."""
    if route is None or route.is_empty:
        return 0.0
    out = route.difference(walk)
    if out.is_empty:
        return 0.0
    dense = shapely.segmentize(out, 2.0)
    return max((walk.distance(Point(c)) for ln in _lines_of(dense) for c in ln.coords), default=0.0)


def checked_route(start, goal, walk, ctx: "Ctx"):
    """grid_route, and in the default behaviour the unsimplified grid path
    when the simplified line cuts outside the walk area (the 0.9-cell
    simplify can cut corners by up to 18 ft)."""
    rt = grid_route(start, goal, walk, ctx.steep, stats=ctx.route_steps)
    if rt is not None and ctx.prog.opts.fixes and route_outside_ft(rt, walk) > ROUTE_TOL_FT:
        rt = grid_route(start, goal, walk, ctx.steep, stats=ctx.route_steps, simplify=0.0)
    return rt


def place_sized_pond(region, Lf, Wf, dem: Dem, angles, near, pad_z, barrier, max_pts=700):
    """Best pond rectangle (Lf x Wf) in region: low, close to `near`, on the
    same side of `barrier` (mapped wetlands). Distinct from
    detention.place_pond, which places the screener's fixed pond."""
    if region.is_empty:
        return None
    base = box(-Lf / 2, -Wf / 2, Lf / 2, Wf / 2)
    pts = [(z, p) for p, z in dem.coords() if region.contains(Point(p))]
    if not pts:
        minx, miny, maxx, maxy = region.bounds
        pts = [(0.0, (x, y)) for x in range(int(minx), int(maxx), 40) for y in range(int(miny), int(maxy), 40) if region.contains(Point(x, y))]
    pts.sort(key=lambda t: t[0])
    best = None
    for z, (x, y) in pts[:max_pts]:
        for a in angles:
            r = shapely.affinity.translate(shapely.affinity.rotate(base, a, origin=(0, 0)), x, y)
            if not region.contains(r):
                continue
            d = r.distance(near)
            link = LineString(nearest_points(r, near))
            crosses = barrier is not None and not barrier.is_empty and link.length > 1 and link.crosses(barrier)
            if crosses:
                break
            score = z + 0.04 * d + (8.0 if (pad_z is not None and z > pad_z) else 0.0)
            if best is None or score < best[0]:
                best = (score, r)
            break
    return best[1] if best else None


def throat_zone(pt, parcel, avoid=None):
    if pt is None:
        return Polygon()
    z = pt.buffer(90).intersection(parcel.buffer(-2))
    return z.difference(avoid) if avoid is not None and not avoid.is_empty else z


def drop_size(need: int) -> tuple[float, float] | None:
    if need <= 0:
        return None
    if need <= 10:
        return need * 12 + 40, 135          # one 55 ft row on an 80 ft aisle
    return math.ceil(need / 2) * 12 + 80, 190


def place_near(region, Lr, Wr, target, angles, step=30.0):
    if region.is_empty:
        return None
    minx, miny, maxx, maxy = region.bounds
    pts = []
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            p = Point(x, y)
            if region.contains(p):
                pts.append((p.distance(target), (x, y)))
            x += step
        y += step
    pts.sort()
    base = box(-Lr / 2, -Wr / 2, Lr / 2, Wr / 2)
    for _, (x, y) in pts[:1500]:
        for a in angles:
            r = shapely.affinity.translate(shapely.affinity.rotate(base, a, origin=(0, 0)), x, y)
            if region.contains(r):
                return r
    return None


# ---------------------------------------------------------------------------
# Caps, checks, feasibility
# ---------------------------------------------------------------------------
def coverage_value(kind: str, bldg_area: float, cars_area: float, imp_ac: float, parcel_area: float) -> float:
    if kind == "building":
        return bldg_area / parcel_area
    if kind == "building+parking":
        return (bldg_area + cars_area) / parcel_area
    return imp_ac / (parcel_area / SQFT_PER_AC)


def cap_flags(clear: int, rules: dict[str, Any], bldg_area: float, cars_area: float, imp_ac: float,
              parcel_area: float, ratio: dict | None = None) -> tuple[int, list[tuple[str, int, str]]]:
    """(clear height after a draft height cap, cap flags). `ratio`, when
    given, receives cap / value for a coverage cap that is exceeded."""
    flags = []
    if rules.get("height") and clear + 10 > rules["height"]:
        clear = rules["height"] - 10
        sev = 1 if clear < 28 else 2
        flags.append(("HEIGHT_CAP", sev, f"draft height cap {rules['height']} ft: clear height lowered to {clear} ft" + (" - below market for distribution" if sev == 1 else "")))
    cov = rules.get("cover")
    if cov:
        kind, capv = cov
        val = coverage_value(kind, bldg_area, cars_area, imp_ac, parcel_area)
        if val > capv:
            flags.append(("COVERAGE_OVER_CAP", 1, f"{kind} coverage {val:.0%} over the draft cap {capv:.0%}"))
            if ratio is not None:
                ratio["cover"] = capv / val
    return clear, flags


def check_plan(p: Plan, o: dict[str, Any], ctx: "Ctx", others: dict | None = None) -> list[dict[str, Any]]:
    """Physical checks on a finished plan (R17). Returns violations."""
    out = []

    def overlap(name, a, b, tol=CONFLICT_TOL_SF):
        if a is None or b is None or a.is_empty or b.is_empty:
            return
        x = a.intersection(b).area
        if x > tol:
            out.append({"check": name, "sf": round(x)})

    if not ctx.B.buffer(1).contains(p.building):
        out.append({"check": "building outside the building envelope", "sf": round(p.building.difference(ctx.B).area)})
    if not ctx.P.buffer(1).contains(p.module):
        out.append({"check": "module outside the paving envelope", "sf": round(p.module.difference(ctx.P).area)})
    ponds = unary_union(o["ponds"]) if o["ponds"] else Polygon()
    yards = unary_union(o["drops"]) if o["drops"] else Polygon()
    routes = unary_union([pp for _, pp in o["route_polys"]]) if o["route_polys"] else Polygon()
    occupied = p.module if others is None else unary_union([p.module, others["occupied"]])
    overlap("pond on a building module", ponds, occupied)
    overlap("pond on a route", ponds, routes)
    overlap("pond on a drop yard", ponds, yards)
    overlap("pond in a water buffer", ponds, ctx.wet_buf)
    overlap("pond under a transmission corridor", ponds, ctx.trans)
    main = o["route_polys"][:o["n_main_routes"]]
    overlap("drop yard on a route", yards, unary_union([pp for _, pp in main]) if main else Polygon())
    overlap("route in a water buffer", routes, ctx.wet_buf)
    if others is not None:
        overlap("second building on building 1's module", p.module, others["occupied"])
        overlap("second building on building 1's ponds, yards or routes", p.module, others["blocked_raw"])
    whole = unary_union([p.module, ponds, yards])
    outside = whole.difference(ctx.parcel.buffer(1)).area
    if outside > CONFLICT_TOL_SF:
        out.append({"check": "outside the parcel", "sf": round(outside)})
    for kind, ft in o.get("route_outside", []):
        if ft > ROUTE_TOL_FT:
            out.append({"check": f"{kind} route outside its paving envelope", "ft": round(ft)})
    return out


def decide(flags: list[tuple[str, int, str]], o_ok: bool, hypothetical: bool, opts: PlanOptions) -> tuple[bool | None, list[str]]:
    """(feasible, gated flags). Incomplete inputs give None in both modes;
    the prototype rule is o_ok and access; the default rule is no
    severity-1 flag outside GATE_FLAGS (R4)."""
    names = {f for f, _, _ in flags}
    gated = sorted(names & GATE_FLAGS)
    if names & NOT_CHECKED_FLAGS:
        return None, gated
    if not opts.fixes:
        return (o_ok and not hypothetical), gated
    return (not any(s == 1 and f not in GATE_FLAGS for f, s, _ in flags)), gated


# ---------------------------------------------------------------------------
# Inputs (plain data; siteplan_inputs builds them)
# ---------------------------------------------------------------------------
@dataclass
class InputState:
    state: str            # checked | none | not_checked | partial
    reason: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"state": self.state, **({"reason": self.reason} if self.reason else {})}


@dataclass
class FrontageInput:
    state: str                          # checked | none | not_checked
    line_wgs: BaseGeometry | None = None
    feet: int | None = None
    road: str | None = None
    reason: str = ""


@dataclass
class SiteInputs:
    key: str                            # slug: file names and record id
    rank: str                           # rank, or the --assemble NAME
    title: str
    parcel_ids: list[str]
    geometry: BaseGeometry              # WGS84 union of the members
    meta: dict[str, Any]                # jurisdiction, hsg, zoning
    tax_acres: float | None = None
    assembled: bool = False
    cut_wgs: BaseGeometry | None = None
    constraints_wgs: list = field(default_factory=list)       # per layer, clipped to the parcel
    constraints_near_wgs: list = field(default_factory=list)  # raw shapes near the parcel (R21)
    transmission: list = field(default_factory=list)          # [(attrs, WGS line)]
    frontage: FrontageInput = field(default_factory=lambda: FrontageInput("not_checked", reason="not asked"))
    neighbours: list = field(default_factory=list)            # [(attrs, WGS shape)]
    dem: Dem = field(default_factory=Dem)
    states: dict = field(default_factory=dict)                # name -> InputState


@dataclass
class Ctx:
    parcel: BaseGeometry
    P: BaseGeometry
    B: BaseGeometry
    wet_buf: BaseGeometry
    trans: BaseGeometry
    cut: BaseGeometry
    steep: BaseGeometry
    dem: Dem
    meta: dict[str, Any]
    rules: dict[str, Any]
    runs: list
    jname: str
    constraints: BaseGeometry
    road: str | None
    hsg: str
    prog: Program
    search: Search
    route_steps: dict = field(default_factory=dict)


@dataclass
class Drawing:
    """Everything siteplan_render draws (feet plane)."""
    title: str
    parcel: BaseGeometry
    P: BaseGeometry
    constraints: BaseGeometry
    trans: BaseGeometry
    cut: BaseGeometry
    steep: BaseGeometry
    front_ft: BaseGeometry | None
    p: Plan
    o: dict
    second: tuple | None


# ---------------------------------------------------------------------------
# One plan: driveways, routes, detention, yards, grading
# ---------------------------------------------------------------------------
def finish(p: Plan, ctx: Ctx, others: dict | None = None) -> dict[str, Any]:
    parcel, P, wet_buf, trans, cut, steep, dem, meta, rules, runs = (
        ctx.parcel, ctx.P, ctx.wet_buf, ctx.trans, ctx.cut, ctx.steep, ctx.dem, ctx.meta, ctx.rules, ctx.runs)
    constraints = ctx.constraints
    fixes = ctx.prog.opts.fixes
    ctx.search.check()
    flags = []
    occupied = p.module if others is None else unary_union([p.module, others["occupied"]])
    sp = spacing_for(ctx.road)
    taken = [] if others is None else [q for q in (others["tpt"], others["cpt"]) if q is not None]
    tpt = cpt = None
    shared = False
    if runs:
        ttarget = min(p.truck_entries, key=lambda q: q.distance(unary_union(runs)))
        tpt, cpt, okrun = pick_driveways(runs, ttarget, p.car_entries[0], sp, taken)
        if not okrun:
            flags.append(("ACCESS_CONSTRAINED", 1, "no frontage run can hold a truck driveway (about 190 ft of curb with 75 ft radii, 110 ft from corners)"))
        if others is not None and tpt is None:
            tpt, cpt, shared = others["tpt"], others["cpt"], True
    # routes inside the paving envelope (inset by half width) plus the driveway throats
    base = P.difference(wet_buf).difference(cut)
    if others is not None:
        base = base.difference(others["blocked"])
    entries = unary_union([q.buffer(32) for q in p.truck_entries + p.car_entries + p.fallback_entries]).intersection(parcel.buffer(-2))
    if fixes:   # entry discs stay out of the water buffer and the test cut (R17)
        entries = entries.difference(wet_buf).difference(cut)
    route_polys, routes, problem = [], [], False
    route_outside = []
    own_truck = unary_union(p.roads + p.courts).buffer(-6)          # the module's own end roads and court carry trucks too
    walk_t = (base.buffer(-20).union(throat_zone(tpt, parcel, unary_union([wet_buf, cut]))).difference(occupied.buffer(-2))
              .difference(unary_union(p.cars).buffer(8)).union(entries.difference(unary_union(p.cars))).union(own_truck))
    rt = None
    if tpt is not None:
        for group, note in ((p.truck_entries, None), (p.fallback_entries, "trucks reach the court along the building's end road from the office side")):
            for tgt in sorted(group, key=lambda q: q.distance(tpt)):
                ctx.search.check()
                rt = checked_route(tpt, tgt, walk_t, ctx)
                if rt is not None:
                    break
            if rt is not None:
                if note:
                    flags.append(("TRUCK_ENTRY_OFFICE_SIDE", 3, note))
                break
        if rt is None:
            problem = True
            flags.append(("ROUTE_INFEASIBLE", 1, "no truck route inside the paving envelope from the driveway to the court"))
        else:
            routes.append(("truck", rt))
            route_polys.append(("truck", rt.buffer(20, cap_style=2).intersection(parcel)))
            route_outside.append(("truck", route_outside_ft(rt, walk_t)))
    truck_poly = unary_union([pp for _, pp in route_polys]) if route_polys else Polygon()
    start_c = cpt if cpt is not None else tpt
    if start_c is not None:
        walk_c = (base.buffer(-12).union(throat_zone(start_c, parcel, unary_union([wet_buf, cut]))).difference(occupied.buffer(-2))
                  .difference(unary_union(p.courts + p.roads + p.rows).buffer(4)).union(p.car_entries[0].buffer(32).intersection(parcel.buffer(-2)))
                  .union(unary_union(p.cars).buffer(-4)))
        if cpt is not None:
            walk_c = walk_c.difference(truck_poly.buffer(6))
        ctx.search.check()
        rc = checked_route(start_c, p.car_entries[0], walk_c, ctx)
        if rc is None:
            problem = True
            flags.append(("CAR_ROUTE_NONE", 1, "no car route from a driveway to the car field without crossing truck pavement"))
        else:
            routes.append(("car", rc))
            route_polys.append(("car", rc.buffer(12, cap_style=2).intersection(parcel)))
            route_outside.append(("car", route_outside_ft(rc, walk_c)))
        if cpt is None:
            flags.append(("ACCESS_SHARED", 2, "one driveway: cars split from trucks inside the throat"))
    rp = unary_union([pp for _, pp in route_polys]) if route_polys else Polygon()
    n_main_routes = len(route_polys)
    if rt is not None:
        if cpt is None and rt.length > 750:
            flags.append(("DEAD_END_ACCESS", 2, f"single access about {rt.length:,.0f} ft long (over 750 ft needs fire-official approval)"))
        if rt.length < 100:
            flags.append(("SHORT_THROAT", 2, f"truck throat about {rt.length:.0f} ft from the street to the court (100 ft wanted for stacking)"))
    if runs and others is None:
        nd = (tpt is not None) + (cpt is not None)
        diag = math.hypot(p.L, p.v["D"])
        if nd < 2:
            flags.append(("FIRE_SECOND_ACCESS", 2, f"{nd} driveway(s): a second fire access (emergency-only link) or fire-official approval needed"))
        elif tpt.distance(cpt) < diag / 2:
            flags.append(("FIRE_SECOND_ACCESS", 2, f"driveways {tpt.distance(cpt):,.0f} ft apart, under half the building diagonal ({diag/2:,.0f} ft)"))
    # detention: ponds before drop yards, out of water buffers and transmission corridors
    need = max(0, p.trailers_target - p.court_stalls)
    ds = drop_size(need)
    drop_ac = (ds[0] * ds[1] / SQFT_PER_AC) if ds else 0.0
    imp_geom = unary_union([p.module, rp])
    imp_ac = imp_geom.area / SQFT_PER_AC + drop_ac
    disturbed = max(min(P.area / SQFT_PER_AC, imp_ac * DISTURBED_FACTOR), imp_ac)
    v = detention_storage(imp_ac, max(disturbed - imp_ac, 0), ctx.hsg)
    npd = pond_count(v)
    w0, Lf, Wf, _ = pond_dims(v / npd)
    pad = [z for (x, y), z in dem.coords() if p.module.contains(Point(x, y))]
    pad_z = sum(pad) / len(pad) if pad else None
    region0 = (parcel.buffer(-10).difference(wet_buf).difference(trans).difference(cut).difference(occupied.buffer(15))
               .difference(rp.buffer(10)).intersection(P.buffer(rules["pave"].get("ROW", 10) + 1)))
    if others is not None:
        region0 = region0.difference(others["blocked"])
    angles = [0, 90, 45, 135, p.frame.pl.angle_deg, p.frame.pl.angle_deg + 90]

    def site_ponds(region, n, Lf_, Wf_):
        out = []
        for _k in range(n):
            ctx.search.check()
            r = place_sized_pond(region, Lf_, Wf_, dem, angles, p.module, pad_z, constraints)
            if r is None:
                break
            out.append(r)
            region = region.difference(r.buffer(20))
        return out
    ponds = site_ponds(region0, npd, Lf, Wf)
    pond_ok = len(ponds) == npd
    # drop yard near the court, counted only with a truck route
    drops, trailers = [], p.court_stalls
    if ds:
        free = (P.difference(occupied.buffer(20)).difference(rp.buffer(8)).difference(wet_buf).difference(cut)
                .difference(unary_union(ponds).buffer(20) if ponds else Polygon()))
        if others is not None:
            free = free.difference(others["blocked"])
        court_u = unary_union(p.courts)
        ctx.search.check()
        yd = place_near(free, ds[0], ds[1], court_u.centroid, [p.frame.pl.angle_deg, p.frame.pl.angle_deg + 90, 0, 90, 45, 135])
        if yd is not None:
            a, b2 = nearest_points(yd, court_u)
            link_walk = (base.buffer(-15).union(yd).union(court_u.buffer(5)).difference(occupied.difference(court_u).buffer(-1))
                         .difference(unary_union(ponds).buffer(5) if ponds else Polygon()))
            if a.distance(b2) < 20:
                link = LineString([a, b2])
            else:
                link = checked_route(Point(yd.centroid), b2, link_walk, ctx)
            if link is not None:
                drops.append(yd)
                trailers += need
                if link.length > 20:
                    route_polys.append(("truck", link.buffer(15, cap_style=2).intersection(parcel)))
            else:
                flags.append(("DROP_YARD_UNREACHABLE", 2, "a drop-trailer yard fits only where no truck route reaches the court"))
    v_sized = v
    if fixes:
        # Impervious after the yard links, with only placed yards (R17); a
        # larger need re-sites the ponds clear of the yards and links.
        all_rp = unary_union([pp for _, pp in route_polys]) if route_polys else Polygon()
        imp_ac = unary_union([p.module, all_rp] + drops).area / SQFT_PER_AC
        disturbed = max(min(P.area / SQFT_PER_AC, imp_ac * DISTURBED_FACTOR), imp_ac)
        v = detention_storage(imp_ac, max(disturbed - imp_ac, 0), ctx.hsg)
        if v > v_sized + 1e-9:
            npd = pond_count(v)
            _, Lf, Wf, _ = pond_dims(v / npd)
            region = region0.difference(all_rp.buffer(10))
            for d in drops:
                region = region.difference(d.buffer(20))
            ponds = site_ponds(region, npd, Lf, Wf)
            pond_ok = len(ponds) == npd
            v_sized = v
    if not pond_ok:
        flags.append(("POND_DOES_NOT_FIT", 1 if fixes else 2, f"{npd} pond(s) of {v_sized / npd:.1f} ac-ft do not fit on the development side: underground detention (vaults) or a smaller building"))
    # grading over the whole module
    mod_pts = pad
    relief = (max(mod_pts) - min(mod_pts)) if len(mod_pts) > 3 else None
    cutfill = None
    if len(mod_pts) > 3:
        mean = sum(mod_pts) / len(mod_pts)
        cutfill = p.module.area * (sum(abs(z - mean) for z in mod_pts) / len(mod_pts)) / 2 / 27
    steep_mod = (p.module.intersection(steep).area / p.module.area * 100) if not steep.is_empty else 0.0
    feasible = pond_ok and not problem and (tpt is not None or not runs)
    o = {"ok": feasible, "pond_ok": pond_ok, "route_problem": problem, "tpt": tpt, "cpt": cpt, "shared_access": shared,
         "routes": routes, "route_polys": route_polys, "route_outside": route_outside, "n_main_routes": n_main_routes,
         "drops": drops, "trailers": trailers, "imp_ac": imp_ac, "v_det": v, "ponds": ponds, "relief": relief,
         "cutfill_cy": cutfill, "steep_module_pct": steep_mod, "dem_points_module": len(mod_pts), "flags": flags,
         "clear": p.clear}
    o["violations"] = check_plan(p, o, ctx, others)
    if fixes:
        for viol in o["violations"]:
            amount = f"{viol['ft']:,} ft" if "ft" in viol else f"{viol['sf']:,} SF"
            name = "ROUTE_OUTSIDE_ENVELOPE" if "route outside" in viol["check"] else "PLAN_GEOMETRY_CONFLICT"
            flags.append((name, 1, f"{viol['check']} ({amount})"))
        bl = p.building.area + (others["building_area"] if others else 0.0)
        ca = sum(c.area for c in p.cars) + (others["cars_area"] if others else 0.0)
        ratio: dict = {}
        clear, cflags = cap_flags(p.clear, rules, bl, ca, imp_ac + (others["imp_ac"] if others else 0.0), parcel.area, ratio)
        o["clear"] = clear
        o["cover_ratio"] = ratio.get("cover")
        flags += cflags
        o["ok"] = o["ok"] and not any(s == 1 for _, s, _ in flags)
    return o


# ---------------------------------------------------------------------------
# A site
# ---------------------------------------------------------------------------
def summary_inputs(parcel, P, B, constraints, trans, cut, steep, fr: FrontageInput, edges) -> dict[str, Any]:
    kinds = sorted({k for k, _ in edges})
    return {"parcel_ac_map": round(parcel.area / SQFT_PER_AC, 1), "paving_envelope_ac": round(P.area / SQFT_PER_AC, 1),
            "building_envelope_ac": round(B.area / SQFT_PER_AC, 1), "mapped_constraints_ac": round(constraints.area / SQFT_PER_AC, 2),
            "transmission_ac": round(trans.area / SQFT_PER_AC, 2), "test_cut_ac": round(cut.area / SQFT_PER_AC, 2),
            "steep_ac": round(steep.area / SQFT_PER_AC, 2), "frontage_ft": fr.feet, "frontage_road": fr.road,
            "edges_ft": {k: round(sum(ln.length for kk, ln in edges if kk == k)) for k in kinds}}


def _sev(flags: list[tuple[str, int, str]]) -> dict[int, list[dict[str, str]]]:
    sevd: dict[int, list[dict[str, str]]] = {1: [], 2: [], 3: []}
    seen = set()
    for f, s, n in flags:
        if (f, n) not in seen:
            seen.add((f, n))
            sevd[s].append({"flag": f, "note": n})
    return sevd


def _input_flags(inp: SiteInputs) -> list[tuple[str, int, str]]:
    """Named flags for inputs that were not checked (R2), never a clean result."""
    out = []
    names = {"frontage": "FRONTAGE_NOT_CHECKED", "neighbours": "NEIGHBOURS_NOT_CHECKED",
             "transmission": "TRANSMISSION_NOT_CHECKED", "layers": "LAYERS_NOT_CHECKED"}
    for key, flag in names.items():
        st = inp.states.get(key)
        if st is not None and st.state == "not_checked":
            out.append((flag, 1, f"{key} not checked ({st.reason or 'no answer'}): the plan is incomplete"))
    dem = inp.states.get("dem")
    if dem is not None and dem.state == "not_checked":
        out.append(("DEM_NOT_CHECKED", 1, f"lidar elevation not checked ({dem.reason or 'no answer'}): no slope, relief or pond siting from ground"))
    elif dem is not None and dem.state == "partial":
        out.append(("DEM_PARTIAL", 1, f"lidar elevation answered for {inp.dem.coverage:.0%} of the samples ({dem.reason}): slope and relief are incomplete"))
    return out


def plan_site(inp: SiteInputs, opts: PlanOptions = PlanOptions(), clock: Callable[[], float] = time.monotonic
              ) -> tuple[dict[str, Any], Drawing | None]:
    """The best plan for one site from plain inputs: (record, drawing or None)."""
    g_wgs = inp.geometry
    if g_wgs is None or g_wgs.is_empty or g_wgs.geom_type not in ("Polygon", "MultiPolygon"):
        raise PlanError(f"{inp.rank}: no usable parcel shape")
    search = Search(opts.max_seconds, clock)
    plane = Plane(g_wgs)
    parcel, seam_sf = site_parcel(plane, g_wgs, inp.assembled, opts)
    meta = inp.meta
    jname, rules, defaulted = jur_rules(meta.get("jurisdiction", ""))
    rules["pave"]["OTHER"] = max(rules["pave"].get("OTHER", 0), OTHER_EDGE_MIN_PAVE_FT)
    prog = Program(jname, rules, parking_rule_for(jname, rules, opts), opts)
    hsg, hsg_note = hsg_letter(meta.get("hsg"), opts.fixes)
    flags: list[tuple[str, int, str]] = []
    late: list[tuple[str, int, str]] = list(_input_flags(inp))   # appended after the prototype's flags
    if defaulted:
        late.append(("JURISDICTION_DEFAULTED", 2, f"no draft rules for '{meta.get('jurisdiction') or 'unknown'}': South Fulton draft rules used"))
    if hsg_note:
        late.append(("HSG_ASSUMED", 3, hsg_note))
    if inp.assembled:
        n_parts = len(parts(parcel))
        if n_parts > 1:
            late.append(("ASSEMBLAGE_NOT_CONTIGUOUS", 1, f"the assembled parcels form {n_parts} separate pieces"))
    if parcel.is_empty or parcel.area < 1:
        return _no_fit(inp, jname, rules, flags + late, opts, parcel, Polygon(), Polygon(), Polygon(), Polygon(),
                       Polygon(), Polygon(), inp.frontage, [], seam_sf), None
    # constraints (screener layers)
    if opts.fixes and inp.constraints_near_wgs:
        # Buffer the water/wetland shapes before clipping to the parcel, so
        # a creek just outside the line still pushes paving back (R21).
        near = unary_union([plane.ft(x) for x in inp.constraints_near_wgs]).intersection(parcel.buffer(WATER_BUFFER_FT))
        constraints = near.intersection(parcel)
        wet_buf = near.buffer(WATER_BUFFER_FT) if not near.is_empty else Polygon()
    else:
        cons = [plane.ft(x) for x in inp.constraints_wgs]
        constraints = unary_union(cons) if cons else Polygon()
        wet_buf = constraints.buffer(WATER_BUFFER_FT) if not constraints.is_empty else Polygon()
    tl = []
    volts_assumed = 0
    for a, s in inp.transmission:
        sf_ = plane.ft(s)
        if sf_.distance(parcel) < 300:
            hw, assumed = corridor_halfwidth(a.get("VOLTAGE"))
            volts_assumed += assumed
            tl.append((a, sf_.buffer(hw, cap_style=2)))
    trans = unary_union([c for _, c in tl]).intersection(parcel) if tl else Polygon()
    if not trans.is_empty:
        flags.append(("TRANSMISSION_EASEMENT_ASSUMED", 2, f"{len(tl)} mapped transmission line(s); corridor widths assumed by voltage; no building or pond under the lines, paving only with the utility's consent"))
    if volts_assumed:
        late.append(("VOLTAGE_ASSUMED", 3, f"{volts_assumed} line(s) without a usable voltage: 115 kV (50 ft half-width) assumed"))
    cut = plane.ft(inp.cut_wgs).intersection(parcel) if inp.cut_wgs is not None else Polygon()
    fr = inp.frontage
    front_ft = plane.ft(fr.line_wgs) if fr.line_wgs is not None else None
    neigh = [(a, s) for a, s in inp.neighbours if s is not None and not s.equals(g_wgs) and s.intersection(g_wgs).area < 0.5 * s.area]
    edges = classify_edges(parcel, plane, neigh, front_ft)
    res_line = unary_union([ln for k, ln in edges if k.startswith("RES")]) if any(k.startswith("RES") for k, _ in edges) else None
    row_line = front_ft if front_ft is not None and not front_ft.is_empty else None
    if res_line is not None:
        flags.append(("RESIDENTIAL_EDGE", 2, f"about {res_line.length:,.0f} ft of boundary touches residential parcels (county land-use codes); draft buffers applied"))
    P = clean(envelope(parcel, edges, rules["pave"]).difference(wet_buf).difference(cut))
    B = clean(envelope(parcel, edges, rules["bldg"]).difference(wet_buf).difference(trans).difference(cut))
    dem = inp.dem
    steep = steep_cells(dem, float_keys=not opts.fixes).intersection(parcel) if dem.elev else Polygon()
    try:
        runs = frontage_runs(front_ft)
    except FrontageGeometryError as exc:
        runs = []
        late.append(("FRONTAGE_GEOMETRY", 1, f"the frontage line could not be merged ({exc}): access not checked"))
    hypothetical = not runs
    if hypothetical and fr.state != "not_checked" and not any(f == "FRONTAGE_GEOMETRY" for f, _, _ in late):
        flags.append(("ACCESS_NONE", 1, "no public road frontage from the GDOT check: landlocked or access by easement only; the plan is hypothetical and its floor area is not counted"))
    zl = (meta.get("zoning") or "").upper()
    if jname.startswith("east point") and zl in ("I-1", "I1", "I-2", "I2"):
        flags.append(("USE_PERMIT_REQUIRED", 1, "East Point: warehousing is use-permit only in I-1/I-2 and needs 5,280 ft from residential (draft reading)"))
    if "chattahoochee" in (meta.get("jurisdiction") or "").lower():
        flags.append(("USE_PERMIT_REQUIRED", 1, "Chattahoochee Hills: no by-right warehousing"))
    if jname.startswith("fairburn") and fr.road and "local" in fr.road:
        flags.append(("COLLECTOR_FRONTAGE", 2, "Fairburn M-districts require collector-street frontage; this frontage reads as a local road"))
    if prog.prule == "market":
        flags.append(("ATL_PARKING_CODE", 2, "parking at the market ratio; Atlanta's 1/600 SF code minimum (unverified after the parking reforms) would need far more"))
    elif jname.startswith("atlanta"):
        flags.append(("ATL_PARKING_CODE", 2, "Atlanta's 1/600 SF code minimum used (draft; unverified after the parking reforms)"))
    small = [q for q in parts(P) if q.area <= MIN_PART_SF]
    ctx = Ctx(parcel, P, B, wet_buf, trans, cut, steep, dem, meta, rules, runs, jname, constraints, fr.road, hsg, prog, search)
    VARIANTS = variants()
    results, excluded = [], Polygon()
    try:
        for attempt in range(3):
            Pe, Be = clean(P.difference(excluded)), clean(B.difference(excluded))
            fits = [q for q in (fit_variant(v, Pe, Be, prog, row_line, res_line, search) for v in VARIANTS) if q is not None]
            if not fits:
                break

            def prelim(q):
                st = q.module.intersection(steep).area / q.module.area if not steep.is_empty else 0.0
                return q.sf * (1 - 0.6 * st)
            fits.sort(key=prelim, reverse=True)
            picked, seen = [], set()
            for q in fits:
                key = (q.v["type"], q.v["row"], q.v["cars"])
                if key in seen and len(picked) >= 4:
                    continue
                picked.append(q)
                seen.add(key)
                if len(picked) >= 8:
                    break
            def explore(q):
                cur = q
                for _i in range(10):
                    o = finish(cur, ctx)
                    if o["ok"]:
                        results.append((cur, o))
                        break
                    if o["route_problem"]:
                        results.append((cur, o))
                        break
                    cap_len = int(cur.L - 40)
                    if o.get("cover_ratio"):
                        # Over a coverage cap: go straight to a length that can
                        # meet it, not 40 ft at a time (R4; default only).
                        cap_len = min(cap_len, int(cur.L * o["cover_ratio"]) // 20 * 20)
                    nxt = fit_variant(cur.v, Pe, Be, prog, row_line, res_line, search, length_cap=cap_len)
                    if nxt is None:
                        results.append((cur, o))
                        break
                    cur = nxt
                else:
                    results.append((cur, o))
            for q in picked:
                explore(q)
            if opts.fixes and not any(o["ok"] for _, o in results) and any(o.get("cover_ratio") for _, o in results):
                # Only caps failed: try the smaller variants that can meet the
                # cap, which the eight largest left out (R4).
                kind, capv = rules["cover"]
                lower = [q for q in fits if q not in picked
                         and coverage_value(kind, q.building.area, sum(c.area for c in q.cars),
                                            q.building.area / SQFT_PER_AC, parcel.area) <= capv]
                for q in lower[:8]:
                    explore(q)
            if any(o["ok"] for _, o in results):
                break
            bad = [t for t in results if t[1]["route_problem"]]
            if not bad:
                break
            excluded = unary_union([excluded] + [t[0].module.buffer(60) for t in bad])   # unreachable pads: try elsewhere
    except _BudgetExceeded:
        pass
    budget_flag = ("PLAN_BUDGET_EXCEEDED", 1, f"the search stopped after {opts.max_seconds:.0f} s; the plan shown is the best found so far")
    if not results:
        fl = flags + late + ([budget_flag] if search.exceeded else [])
        return _no_fit(inp, jname, rules, fl, opts, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, small), None

    def score(t):
        q, o = t
        st = o["steep_module_pct"] / 100
        rel = (o["relief"] or 0)
        return (o["ok"], not o["route_problem"], q.sf * (1 - 0.6 * st) - 400 * max(rel - 25, 0) - 2000 * max(0, q.trailers_target - o["trailers"]))
    results.sort(key=score, reverse=True)
    best_p, best_o = results[0]
    second = None
    if best_o["ok"] and not search.exceeded:
        try:
            blocked = unary_union([best_p.module.buffer(40)] + [d.buffer(25) for d in best_o["drops"]] + [r.buffer(20) for r in best_o["ponds"]]
                                  + [pp.buffer(15) for _, pp in best_o["route_polys"]])
            P2, B2 = clean(P.difference(blocked)), clean(B.difference(blocked))
            if P2.area > SECOND_MIN_AC * SQFT_PER_AC:
                c2 = [q for q in (fit_variant(v, P2, B2, prog, row_line, res_line, search) for v in VARIANTS) if q is not None and q.sf >= SECOND_MIN_SF]
                others = {"occupied": best_p.module, "blocked": blocked, "tpt": best_o["tpt"], "cpt": best_o["cpt"],
                          "blocked_raw": unary_union(best_o["drops"] + best_o["ponds"] + [pp for _, pp in best_o["route_polys"]]),
                          "building_area": best_p.building.area, "cars_area": sum(c.area for c in best_p.cars),
                          "imp_ac": best_o["imp_ac"]}
                for q in sorted(c2, key=lambda q: -q.sf)[:6]:
                    o2 = finish(q, ctx, others)
                    if o2["ok"]:
                        second = (q, o2)
                        break
        except _BudgetExceeded:
            second = None
    if search.exceeded:
        late.append(budget_flag)
    rec = report(inp, best_p, best_o, second, parcel, P, B, rules, jname, flags, late, fr, hypothetical, res_line, row_line, opts)
    rec["alternatives"] = [{"variant": q.v["name"], "operator": q.v["operator"], "sf": rnd(q.sf, 1000), "L": q.L, "D": q.v["D"],
                            "feasible": o["ok"], "route_problem": o["route_problem"]} for q, o in results[:10]]
    rec["inputs"] = _inputs_block(inp, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, small, search, ctx)
    drawing = Drawing(inp.title, parcel, P, constraints, trans, cut, steep, front_ft, best_p, best_o, second)
    return rec, drawing


def _inputs_block(inp, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, small, search=None, ctx=None):
    out = summary_inputs(parcel, P, B, constraints, trans, cut, steep, fr, edges)
    out["states"] = {k: inp.states[k].as_dict() for k in sorted(inp.states)}
    out["dem_points"] = len(inp.dem.elev)
    out["dem_coverage_pct"] = round(100 * inp.dem.coverage, 1)
    out["paving_parts_dropped"] = {"count": len(small), "ac": round(sum(q.area for q in small) / SQFT_PER_AC, 2)}
    if inp.assembled:
        out["seam_sf_closed"] = round(seam_sf)
    if search is not None:
        out["fit_searches"] = search.fit_calls
    if ctx is not None and ctx.route_steps:
        out["route_grid_ft"] = round(ctx.route_steps.get("step", GRID), 1)
    return out


def _base_record(inp: SiteInputs, jname: str, rules: dict[str, Any], opts: PlanOptions) -> dict[str, Any]:
    return {"id": inp.key, "key": inp.key, "rank": inp.rank, "parcel_ids": list(inp.parcel_ids), "title": inp.title,
            "zoning": {"name": jname, "status": "draft", "source": rules.get("source"),
                       "values": {k: rules[k] for k in ("bldg", "pave", "cover", "height", "parking", "stall_w", "island")}},
            "operator_decisions": opts.decisions()}


def _no_fit(inp, jname, rules, flags, opts, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, small=()):
    flags = list(flags) + [("ZONING_DRAFT", 2, f"setbacks and buffers are draft screening values for {jname}; {ZONING_AUTHORITY}")]
    sev = {1: [{"flag": "NO_FIT", "note": "no building variant fits the envelope with its truck court, parking and pond"}], 2: [], 3: []}
    for f, s, n in flags:
        sev[s].append({"flag": f, "note": n})
    rec = _base_record(inp, jname, rules, opts)
    # Missing inputs only ever enlarge the envelope, so NO_FIT stands.
    rec.update({"fits": False, "feasible": False, "hypothetical": None, "jurisdiction_rules": jname, "total_sf": 0,
                "gated": [], "flags": sev,
                "inputs": _inputs_block(inp, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, list(small))})
    return rec


def report(inp, p, o, second, parcel, P, B, rules, jname, flags0, late, fr, hypothetical, res_line, row_line, opts):
    fixes = opts.fixes
    meta = inp.meta
    tax_acres = inp.tax_acres
    flags = list(flags0) + o["flags"] + ([("SECOND_" + f, max(s, 2) if fixes else s, "second building: " + n)
                                          for f, s, n in second[1]["flags"]] if second else [])
    gross = parcel.area / SQFT_PER_AC
    total = p.sf + (second[0].sf if second else 0)
    if fixes:
        clear = o["clear"]
    else:
        bl = p.building.area + (second[0].building.area if second else 0)
        clear, cflags = cap_flags(p.clear, rules, bl, sum(c.area for c in p.cars), o["imp_ac"], parcel.area)
        hc = [f for f in cflags if f[0] == "HEIGHT_CAP"]
        flags += hc
    if o["trailers"] < p.trailers_target:
        flags.append(("TRAILER_SHORT", 2, f"{o['trailers']} trailer stalls against a target of {p.trailers_target}"))
    for c in p.courts + p.rows:
        if res_line is not None and c.distance(res_line) < 150:
            flags.append(("LOADING_FACES_RES", 2, "a truck court or trailer row is within 150 ft of a residential edge (50 ft loading strip and screening likely)"))
            break
    for c in p.courts + p.rows:
        if row_line is not None and c.distance(row_line) < 100:
            flags.append(("STREET_FACING_DOCKS", 2, "a truck court faces the street within 100 ft (screening; some codes forbid it)"))
            break
    if not fixes:
        flags += [f for f in cflags if f[0] == "COVERAGE_OVER_CAP"]
    if o["steep_module_pct"] > 10:
        flags.append(("GRADING_RISK", 2, f"{o['steep_module_pct']:.0f}% of the graded area is steeper than 15% (lidar)"))
    if o["relief"] and o["relief"] > 25:
        flags.append(("RELIEF", 2, f"about {o['relief']:.0f} ft of relief across the graded area: terraces or retaining walls likely"))
    sfa = total / gross
    if sfa < 10500:
        flags.append(("BELOW_COMP_BAND", 3, f"{sfa:,.0f} SF per gross acre; Georgia comps run about 10,500-15,400"))
    ld = p.L / p.v["D"]
    if ld < 2.0 or ld > 3.8:
        flags.append(("LD_OUT_OF_BAND", 3, f"length/depth {ld:.1f} (comps 2.0-3.8)"))
    walls = 1 if p.v["type"] == "rear" else 2
    if p.doors and (p.L - 2 * END_CLEAR) * walls / p.doors < 14:
        flags.append(("DOOR_DENSITY_HIGH", 3, "under 14 ft of dock wall per door"))
    imp_fp = o["imp_ac"] * SQFT_PER_AC / p.building.area
    if imp_fp < 1.8 or imp_fp > 2.5:
        flags.append(("IMPERVIOUS_RATIO", 3, f"impervious / building footprint {imp_fp:.1f} (comps about 2.0-2.1)"))
    if tax_acres and abs(gross - tax_acres) / tax_acres > 0.15:
        flags.append(("ACREAGE_MISMATCH", 2, f"county map {gross:.1f} ac vs tax roll {tax_acres:.1f} ac"))
    flags.append(("ZONING_DRAFT", 2, f"setbacks and buffers are draft screening values for {jname}; {ZONING_AUTHORITY}"))
    flags.append(("STREAMS_PIPELINES", 2, "stream lines and petroleum pipelines are not mapped; mapped water, wetlands and floodplain get a 25 ft buffer"))
    if o["ponds"]:
        flags.append(("POND_SITE_FROM_LIDAR", 3, "pond placed low, near the module and on its side of mapped wetlands; no outfall, maintenance drive or grading design"))
    flags += late
    feasible, gated = decide(flags, o["ok"], hypothetical, opts)
    if fixes and feasible is False and not any(s == 1 and f not in GATE_FLAGS for f, s, _ in flags):
        flags.append(("NOT_FEASIBLE_REASON", 1, "the plan failed a check without a named flag"))
    counted = feasible is True and not (fixes and gated)
    sevd = _sev(flags)
    cars_prov = max(min(p.car_cap, math.ceil(p.cars_required / 5) * 5), p.cars_required) if p.car_cap >= p.cars_required else p.car_cap
    v_total = o["v_det"] + (second[1]["v_det"] if second else 0)
    imp_total = o["imp_ac"] + (second[1]["imp_ac"] if second else 0)
    rec = _base_record(inp, jname, rules, opts)
    rec.update({
        "fits": True, "hypothetical": hypothetical, "feasible": feasible, "gated": gated, "jurisdiction_rules": jname,
        "configuration": p.v["name"], "operator_prototype": p.v["operator"],
        "building": {"L_ft": p.L, "D_ft": p.v["D"], "sf": rnd(p.sf, 1000), "office_sf": rnd(OFFICE_SHARE * p.sf, 500), "clear_height_ft": clear},
        "total_sf": rnd(total, 1000) if counted else 0, "total_sf_if_access": rnd(total, 1000),
        "doors": p.doors,
        "trailers": {"court_rows": p.court_stalls, "drop_yards": o["trailers"] - p.court_stalls, "total": o["trailers"], "target": p.trailers_target},
        "cars": {"provided": cars_prov, "row_capacity": p.car_cap, "required": p.cars_required, "basis": p.cars_rule, "ada": ada_count(cars_prov),
                 "per_1000_sf": round(cars_prov / p.sf * 1000, 2), "stall": f"{rules['stall_w']} x 18 ft"},
        "coverage": {"gross_ac": round(gross, 1), "sf_per_gross_ac": rnd(sfa, 100), "gross_far": round(total / (gross * SQFT_PER_AC), 2),
                     "impervious_ac": round(imp_total, 1), "impervious_pct": round(imp_total / gross * 100), "paving_envelope_ac": round(P.area / SQFT_PER_AC, 1)},
        "detention": {"storage_acft": round(v_total, 1), "ponds": len(o["ponds"]) + (len(second[1]["ponds"]) if second else 0),
                      "fits": o["pond_ok"] if fixes else o["ok"],
                      "basis": f"1.15 x (k_imp x impervious + k_perv x disturbed pervious), HSG {meta.get('hsg') or 'B'}; 6 ft deep, 4:1 slopes, L=2W, 1 ft freeboard, 15 ft rim; screening only (+/-30%)"},
        "access": {"truck_driveway": o["tpt"] is not None, "car_driveway": o["cpt"] is not None, "frontage_ft": fr.feet, "road": fr.road,
                   "frontage_state": fr.state,
                   "truck_route_ft": round(sum(ln.length for k, ln in o["routes"] if k == "truck")), "car_route_ft": round(sum(ln.length for k, ln in o["routes"] if k == "car"))},
        "grading": {"relief_ft": round(o["relief"]) if o["relief"] else None, "cut_fill_cy": rnd(o["cutfill_cy"], 1000) if o["cutfill_cy"] else None,
                    "steep_share_pct": round(o["steep_module_pct"]), "dem_points": o["dem_points_module"],
                    "basis": "one balanced plane over the building, courts and parking (lidar sampled at 40 ft); no terraces"},
        "flags": sevd,
        "plan_checks": o["violations"] + ([dict(v, building=2) for v in second[1]["violations"]] if second else []),
    })
    if second:
        q, o2 = second
        rec["second_building"] = {"configuration": q.v["name"], "L_ft": q.L, "D_ft": q.v["D"], "sf": rnd(q.sf, 1000), "doors": q.doors,
                                  "trailers": o2["trailers"], "trailers_target": q.trailers_target, "cars_capacity": q.car_cap, "cars_required": q.cars_required,
                                  "own_driveways": not o2["shared_access"], "route_ft": round(sum(ln.length for k, ln in o2["routes"] if k == "truck"))}
        rec["totals_both_buildings"] = {"doors": p.doors + q.doors, "trailers": o["trailers"] + o2["trailers"],
                                        "trailers_target": p.trailers_target + q.trailers_target, "cars_capacity": p.car_cap + q.car_cap}
    return rec


# ---------------------------------------------------------------------------
# Owner guard (SR-8): one check on the final bytes of every artifact
# ---------------------------------------------------------------------------
_ENTITY = re.compile(r"\b(?:l ?l ?c|inc|incorporated|corp|corporation|co|company|l ?p|l ?l ?p|l ?l ?l ?p|ltd|limited|"
                     r"trust|trustee|trustees|tr|etal|et al|partnership|partners|holdings|properties|pllc|pc|the)\b")
OWNER_MIN_CHARS = 8


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


@dataclass
class OwnerTerms:
    terms: dict = field(default_factory=dict)   # normalised term -> field name

    def __len__(self) -> int:
        return len(self.terms)


def owner_terms(rows: Iterable[dict[str, Any]]) -> OwnerTerms:
    """Normalised owner and mailing strings from a run's rows, plus each
    owner's core name without entity suffixes (8 characters or more). A
    mailing string equal to a site address of the run is skipped: a site
    address is shown as the site's own address, not as owner data. So is a
    term that is part of one of the run's jurisdiction names (a public
    owner such as a city reads like the place: "South Fulton")."""
    rows = list(rows)
    sites = {_norm(str(r.get("site_address") or "")) for r in rows}
    places = [f" {_norm(str(r.get('jurisdiction') or ''))} " for r in rows]
    places = sorted(set(places))
    out: dict[str, str] = {}

    def add(term: str, fld: str) -> None:
        if len(term) >= OWNER_MIN_CHARS and not any(f" {term} " in pl for pl in places):
            out.setdefault(term, fld)
    for r in rows:
        own = _norm(str(r.get("owner") or ""))
        add(own, "owner")
        add(" ".join(_ENTITY.sub(" ", own).split()), "owner")
        mail = _norm(str(r.get("owner_mailing") or ""))
        if mail not in sites:
            add(mail, "owner_mailing")
    return OwnerTerms(out)


def owner_hits(payload: str | bytes, terms: OwnerTerms) -> dict[str, int]:
    """{field: number of distinct owner strings found}, on word boundaries.
    Never returns the strings themselves."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    hay = f" {_norm(text)} "
    hits: dict[str, int] = {}
    for term, fld in terms.terms.items():
        if f" {term} " in hay:
            hits[fld] = hits.get(fld, 0) + 1
    return hits


# ---------------------------------------------------------------------------
# Files: slugs, containment, atomic writes
# ---------------------------------------------------------------------------
SLUG = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NAME = re.compile(r"^[A-Za-z0-9_+-]{1,64}$")
PID = re.compile(r"^[A-Z0-9][A-Z0-9 ]{0,30}[A-Z0-9]$")
SVG_NAME = re.compile(r"^plan-[A-Za-z0-9_-]+\.svg$")
PLANS_FILE = "plans.json"


def slug(name: str) -> str:
    s = name.replace("+", "-")
    if not NAME.match(name) or not SLUG.match(s):
        raise PlanError(f"not a usable site name: {name!r} (letters, digits, _ - +, up to 64)")
    return s


def check_pid(pid: str) -> str:
    """A Fulton parcel id, exactly as given (inner and doubled spaces kept)."""
    if not isinstance(pid, str) or not PID.match(pid):
        raise PlanError(f"not a parcel id: {pid!r} (upper-case letters, digits and spaces)")
    return pid


def contained(path: Path, base: Path) -> Path:
    rp, rb = path.resolve(), base.resolve()
    if not rp.is_relative_to(rb):
        raise PlanError(f"{path} is outside {base}")
    return rp


def write_atomic(path: Path, data: bytes, base: Path, terms: OwnerTerms | None) -> None:
    """Owner guard on the final bytes, then a temp file and os.replace."""
    target = contained(path, base)
    if terms is not None:
        hits = owner_hits(data, terms)
        if hits:
            raise OwnerDataFound(f"{target.name}: owner data found ({', '.join(f'{k}: {v}' for k, v in sorted(hits.items()))}); nothing written")
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def code_hash() -> str:
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in ("siteplan.py", "siteplan_inputs.py", "siteplan_render.py"):
        p = here / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def run_identity(run_dir: Path) -> dict[str, str]:
    return {"name": run_dir.name,
            "ranked_csv_sha256": hashlib.sha256((run_dir / "ranked.csv").read_bytes()).hexdigest()}


def check_out_dir(out: Path, run_dir: Path, repo: Path | None = None) -> None:
    """Refuse --out equal to (or inside) the run folder, or a folder in the
    repository that git does not ignore (R26)."""
    o, r = out.resolve(), run_dir.resolve()
    if o == r or o.is_relative_to(r):
        raise PlanError("--out must not be the run folder or inside it")
    repo = (repo or Path(__file__).resolve().parents[1]).resolve()
    if o.is_relative_to(repo):
        probe = o / "plans.json"
        res = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", str(probe)],
                             capture_output=True, check=False)
        if res.returncode != 0:
            raise PlanError(f"--out {out} is inside the repository and not ignored by git")


def svg_name(key: str) -> str:
    name = f"plan-{key}.svg"
    if not SVG_NAME.match(name):
        raise PlanError(f"bad plan file name {name!r}")
    return name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_assemble(items: list[str]) -> list[tuple[str, list[str]]]:
    out = []
    for item in items:
        name, sep, rest = item.partition("=")
        if not sep:
            raise PlanError(f"--assemble takes NAME=pid1,pid2: {item!r}")
        slug(name)
        pids = [check_pid(x) for x in rest.split(",")]
        if not 2 <= len(pids) <= MAX_ASSEMBLE_IDS or len(set(pids)) != len(pids):
            raise PlanError(f"--assemble {name}: 2 to {MAX_ASSEMBLE_IDS} distinct parcel ids")
        out.append((name, pids))
    return out


def _parse_cut(items: list[str]) -> dict[str, tuple[str, float]]:
    out = {}
    for item in items:
        key, sep, rest = item.partition("=")
        pid, sep2, ft = rest.rpartition(":")
        if not sep or not sep2:
            raise PlanError(f"--cut-parcel takes RANK=PID:FT: {item!r}")
        if not NAME.match(key):
            raise PlanError(f"--cut-parcel: bad site {key!r}")
        try:
            feet = float(ft)
        except ValueError:
            raise PlanError(f"--cut-parcel: bad buffer {ft!r}") from None
        if not (0 <= feet <= 1000) or not math.isfinite(feet):
            raise PlanError("--cut-parcel: buffer 0-1000 ft")
        out[key] = (check_pid(pid), feet)
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m screener.siteplan", description="Screening-level industrial site plans (DRAFT zoning).")
    ap.add_argument("run_dir", type=Path, help="a finished run folder (ranked.csv, ranked.geojson)")
    ap.add_argument("--ranks", default="", help="comma-separated ranks, e.g. 4,33")
    ap.add_argument("--assemble", action="append", default=[], metavar="NAME=PID,PID", help="plan several parcels as one site")
    ap.add_argument("--cut-parcel", action="append", default=[], metavar="RANK=PID:FT", help="test cut: a parcel plus a buffer, removed from the site")
    ap.add_argument("--out", type=Path, required=True, help="output folder (outside the run folder; ignored by git if in the repo)")
    ap.add_argument("--merge", action="store_true", help="keep plans already in --out (same schema, run and options)")
    ap.add_argument("--redo", action="store_true", help="with --merge, plan sites again even if done")
    ap.add_argument("--cache-days", type=float, default=None, help="cache lifetime for this tool's county and HIFLD requests")
    ap.add_argument("--max-seconds-per-site", type=float, default=MAX_SECONDS_PER_SITE)
    ap.add_argument("--parking", choices=("max", "code", "market"), default=PARKING_RULE)
    ap.add_argument("--atlanta-parking", choices=("market", "code"), default=ATLANTA_PARKING)
    ap.add_argument("--trailer-ratio", default=None, help="trailer stalls per door: one number, or three (below 250k, below 450k, above)")
    ap.add_argument("--prototype-behaviour", action="store_true", help="reproduce the v4 prototype exactly (parity)")
    return ap


def main(argv: list[str] | None = None) -> int:
    from . import siteplan_inputs as si       # the network layer, only for the CLI
    from . import siteplan_render as sr

    args = build_parser().parse_args(argv)
    try:
        opts = PlanOptions(behaviour="prototype" if args.prototype_behaviour else "fixes", parking=args.parking,
                           atlanta_parking=args.atlanta_parking,
                           trailer_tiers=trailer_tiers_from(args.trailer_ratio) if args.trailer_ratio else TRAILER_TARGET_TIERS,
                           max_seconds=args.max_seconds_per_site)
        ranks = [r.strip() for r in args.ranks.split(",") if r.strip()]
        for r in ranks:
            if not re.fullmatch(r"\d{1,6}", r):
                raise PlanError(f"--ranks: not a rank: {r!r}")
        assembles = _parse_assemble(args.assemble)
        cuts = _parse_cut(args.cut_parcel)
        names = ranks + [n for n, _ in assembles]
        if len(set(slug(n) for n in names)) != len(names):
            raise PlanError("a site is named twice")
        for k in cuts:
            if k not in names:
                raise PlanError(f"--cut-parcel {k}: not a site of this call")
        if not names:
            raise PlanError("nothing to plan: give --ranks or --assemble")
        run_dir = args.run_dir
        check_out_dir(args.out, run_dir)
        run = si.load_run(run_dir)
    except (PlanError, ValueError, OSError) as exc:
        print(f"siteplan: {exc}", file=sys.stderr)
        return 2
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    terms = owner_terms(run.rows.values())
    header = {"schema_version": SCHEMA_VERSION, "tool": "screener.siteplan", "code_sha256": code_hash(),
              "run": run_identity(run_dir), "options": opts.decisions(),
              "meta": {"sources": si.sources(), "zoning_status": "draft"}}
    plans_path = out / PLANS_FILE
    kept: list[dict[str, Any]] = []
    if args.merge and plans_path.exists():
        old = json.loads(plans_path.read_text(encoding="utf-8"))
        same = all(old.get(k) == header[k] for k in ("schema_version", "code_sha256", "run", "options"))
        if not same:
            print("siteplan: --merge refused: the plans in --out come from another schema, code, run or options", file=sys.stderr)
            return 2
        kept = [r for r in old.get("plans", []) if args.redo is False or r.get("rank") not in names]
    done = {r.get("rank") for r in kept}
    fetch = si.Fetcher(cache_days=args.cache_days)
    failed = 0

    def save(recs):
        body = dict(header, generated=time.strftime("%Y-%m-%d"), plans=recs)
        write_atomic(plans_path, (json.dumps(body, indent=1, default=str) + "\n").encode("utf-8"), out, terms)

    recs = list(kept)
    for name in names:
        if name in done:
            continue
        try:
            spec = si.site_spec(run, name, dict(assembles).get(name), cuts.get(name), fetch, opts)
            inp = si.gather(spec, fetch, opts)
            rec, drawing = plan_site(inp, opts)
            if drawing is not None:
                svg = sr.render(drawing, rec)
                fname = svg_name(inp.key)
                write_atomic(out / fname, svg.encode("utf-8"), out, terms)
                rec["svg"] = fname
        except (si.ArcGISError, GEOSException) as exc:
            failed += 1
            print(f"siteplan: {name}: input failed ({type(exc).__name__}: {str(exc)[:160]})", file=sys.stderr)
            continue
        except PlanError as exc:
            failed += 1
            print(f"siteplan: {name}: {exc}", file=sys.stderr)
            continue
        except OwnerDataFound as exc:
            print(f"siteplan: {exc}", file=sys.stderr)
            return 3
        recs = [r for r in recs if r.get("rank") != name] + [rec]
        try:
            save(recs)
        except OwnerDataFound as exc:
            print(f"siteplan: {exc}", file=sys.stderr)
            return 3
        b = rec.get("building", {})
        print(name, rec.get("configuration"), b.get("sf"), "feasible", rec.get("feasible"), "doors", rec.get("doors"),
              "trailers", (rec.get("trailers") or {}).get("total"), "cars", (rec.get("cars") or {}).get("provided"), flush=True)
    if not recs:
        save([])
    keep = {r.get("svg") for r in recs if r.get("svg")}
    for f in out.glob("plan-*.svg"):
        if SVG_NAME.match(f.name) and f.name not in keep:
            f.unlink()           # stale plan from an earlier call (R11)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
