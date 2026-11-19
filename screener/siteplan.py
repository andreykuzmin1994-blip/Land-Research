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
- ``siteplan_assess`` reads a finished plan out (pure, like this module):
  one status, the next steps, where the land goes, the plan against its
  draft zoning values, a takeoff, why this layout. It changes no layout,
  flag, feasibility or floor area.
- ``siteplan_render`` draws the test-fit sheet (SVG).

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

OPERATOR DECISIONS still open (named constants recorded in every plan's
``operator_decisions``; the parking and trailer ones are also CLI options,
the prototype set, market depths and edge strip are constants only):

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

DECIDED by the operator on 2026-11-17 (default behaviour only; reviews/38):

- ``RANKING``: no floor area for trailer stalls. The largest layout that
  passes is drawn; a shortfall is flagged ``TRAILER_SHORT``.
- ``UNDERGROUND_DETENTION`` = False: not in a market where land is cheap
  (``--underground-detention`` for one where it is dear).
- ``JUR_TIGHTENED`` and ``OPEN_YARD_RULE_FT`` = 60: every value that read
  looser than the codes, tightened, and the building-code open yard at
  the building. Still draft.

BEHAVIOUR. ``--prototype-behaviour`` reproduces the v4 prototype's numbers
(the parity proof; the draft labels, the input states and a search cut by
the time budget are reported the same way in both modes). The default adds the reviewed fixes, each a listed
delta in reviews/37: feasibility follows a declared flag rule (R4),
coverage and height caps per alternative (R4), route and plan geometry
checks (R16, R17), impervious area recomputed after the drop-yard links
and only placed yards counted, the ponds re-sized to it (R17), water
buffers built before clipping to the parcel (R21), assemblage seams closed
(R13), dual soil groups read as the letter giving the larger pond (R24),
an assemblage's members' rules compared (ASSEMBLAGE_MIXED_RULES). A
blocking flag no other layout can clear (``LENGTH_INDEPENDENT_FLAGS``: a
draft height cap, a frontage too short for a truck driveway) blocks the
site but not the layout: it does not shrink the building and stays out of
the layout's own check (``geometry_ok``), the ranking of layouts, the
rescue pass and the second-building search. When the pond or a route blocks every explored plan, the
search also tries the shorter lengths and the remaining fits (so a larger
parcel never reads worse than a smaller one inside it). Missing inputs
fail closed in both modes (R2): ``feasible`` is None ("incomplete"),
never a clean result; the one exception is NO_FIT, which stays False
because a missing input can only enlarge the envelope. A search cut by the time
budget (``--max-seconds-per-site``) is flagged ``PLAN_BUDGET_EXCEEDED``
and is not feasible; when it is cut before any plan is finished the
record says so rather than ``NO_FIT``; when it is cut only in the optional
second-building search, building 1 stands and the plan says
``SECOND_BUILDING_NOT_SEARCHED``.

Owner names and mailing addresses never enter a plan: assemblages are
explicit parcel-id lists, no query this tool writes asks for a parcel
owner or mailing field, and the shared owner guard (``viewer.OwnerGuard``,
built from ranked.csv and rejected.csv) scans the final bytes of every
file of a site, as written and with its markup removed, before any of
them is written.
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
from dataclasses import dataclass, field, replace as dataclass_replace
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
from . import siteplan_assess as assess
from . import siteplan_truck as truck
from .geo import FT_PER_M, LocalProjection
from .viewer import OwnerGuard, csv_cell, markup_text

# 2: every record adds the read-out of siteplan_assess (assessment,
# site_data, compliance, quantities, selection), richer alternatives, the
# pad elevation and its cut and fill, and the sheet's scale.
SCHEMA_VERSION = 2
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
ROOF_ALLOWANCE_FT = 10  # building height read as clear height + 10 ft against a draft height cap
CLEAR_BELOW_MARKET_FT = 28
COMP_BAND_SF_PER_AC = (10500, 15400)   # Georgia comps, SF per gross acre
# The ranking of finished layouts (plan_site's score): floor area, marked
# down for steep ground under the pad, for relief past a free allowance
# and for each trailer stall short of the target.
# Operator decision of 2026-11-17: a trailer stall short of the target is
# NOT worth floor area, so the default ranking has no trailer mark (the
# shortfall is still flagged, TRAILER_SHORT). The prototype behaviour keeps
# the prototype's 2,000 SF per stall, for parity.
RANKING = {"steep_share": 0.6, "relief_free_ft": 25, "relief_sf_per_ft": 400, "trailer_sf": 0}
PROTOTYPE_RANKING = {**RANKING, "trailer_sf": 2000}
# Per-site search budget (R19). The offline replay of the 33 reference sites
# needs up to 193 s (#48) and 848 s (the 77.6 ac 115+170 assemblage) on this
# container; at 120 s #48 would come out with no plan. 900 s lets every
# reference site finish; --max-seconds-per-site changes it.
MAX_SECONDS_PER_SITE = 900.0
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
# Operator decision of 2026-11-17: no underground detention in a market
# where land is cheap (Atlanta); yes where it is dear (infill Chicago, New
# Jersey). Off for this county's planner; --underground-detention turns it
# on for a market that uses it. It changes what the read-out recommends
# when the surface pond holds the building, not the layout: the planner
# draws surface ponds only.
UNDERGROUND_DETENTION = False
# Operator decision of 2026-11-17 ("tighten all of them", reviews/38): the
# building stands this far from every lot line that is not a road. The
# building code's unlimited-area rule wants a 60 ft yard or public way all
# round a one-storey sprinklered warehouse (40 ft with 3-hour walls on part
# of the perimeter). Applied to every building, whatever its size.
OPEN_YARD_RULE_FT = 60
MARKET_REAR_DEPTHS = (160, 190, 230, 270, 310)   # market comps, operator=False
MARKET_CROSS_DEPTHS = (470, 600)
OTHER_EDGE_MIN_PAVE_FT = 10
# A dual soil group ('B/D') is read as the letter that gives the larger
# detention storage (with HSG_K that is the drained letter, as the prototype
# read it): the conservative choice for pond size. Reading D would shrink
# the pond about 27% (the shortcut nets out pre-development runoff).
DUAL_HSG_USE = "larger storage"

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
# Tightened draft values (operator decision of 2026-11-17, reviews/38
# 02_draft_offsets_check.md): where the codes read so far say more than the
# draft table did. Default behaviour only; the prototype behaviour keeps
# JUR_DRAFT as it was. Still DRAFT: none of this is verified against the
# adopted code.
#   OTHER_REAR  a rear lot line that is not residential (several codes have
#               a deeper rear yard than side yard)
#   COMM        a lot line toward a commercial or institutional parcel
#               (county tax class C or E)
#   ag_buffer   agricultural neighbours trigger the residential buffer
#   truck_space_sf   a code minimum of one truck space per this floor area
#   row_by_zoning    a deeper front yard in a named district
#   corridor    a corridor overlay on named state routes
#   use_check   a reported restriction on the use, to confirm
JUR_TIGHTENED: dict[str, dict[str, Any]] = {
    "south fulton": {"ag_buffer": True,
                     "why": ["agricultural (AG-1) neighbours trigger the 50 / 100 ft buffer, as residential ones do"]},
    "fulton industrial": {"ag_buffer": True,
                          "why": ["agricultural (AG-1) neighbours trigger the 50 / 100 ft buffer, as residential ones do"]},
    "union city": {"ag_buffer": True, "why": ["agricultural neighbours trigger the 75 ft buffer, as residential ones do"]},
    "fairburn": {"bldg": {"OTHER_REAR": 30, "COMM": 50}, "pave": {"COMM": 50}, "truck_space_sf": 5000,
                 "why": ["rear yard 30 ft (side stays at the draft 25)",
                         "50 ft buffer drawn toward commercial and institutional parcels (the code's buffer to other districts)",
                         "one oversized (truck) space per 5,000 SF of warehouse is a minimum for the trailer count"]},
    "atlanta": {"bldg": {"RES_SIDE": 31, "RES_REAR": 31},
                "why": ["31 ft from a residential line: the 20 ft transitional yard plus 11 ft for the 45-degree height "
                        "plane at a 46 ft building"]},
    "east point": {"bldg": {"OTHER_REAR": 30, "COMM": 50}, "pave": {"COMM": 50}, "row_by_zoning": {"BP": 45},
                   "why": ["rear yard 30 ft (2019 text)", "50 ft landscaped strip toward any non-industrial parcel, drawn "
                           "toward commercial and institutional parcels", "front yard 45 ft in the B-P district"]},
    "palmetto": {"bldg": {"COMM": 20}, "pave": {"COMM": 20},
                 "corridor": {"routes": ("SR 14", "SR 154"), "bldg": 95, "pave": 35,
                              "roads": "US 29 (SR 14), SR 154, Weldon, Johnson and Tommy Lee Cook roads"},
                 "why": ["20 ft buffer toward commercial parcels (2013 text)",
                         "arterial corridor overlay: 95 ft building and 35 ft paving at the road on US 29 and SR 154"]},
    "college park": {"use_check": ("College Park put a moratorium on applications for warehousing and distribution centers in "
                                   "2024 (a secondary source); what replaced it is not known"),
                     "why": ["a reported 2024 moratorium on warehouse applications is flagged"]},
}
EDGE_KINDS = ("ROW", "RES_SIDE", "RES_REAR", "COMM", "OTHER_REAR", "OTHER")
EDGE_STUB_FT = 25.0     # a side or rear stub up to this long joins the run beside it (a neighbour is read 20 ft round a corner)
# County tax classes (Georgia digest) read off a neighbour's record.
RES_CLASS, AG_CLASSES, COMM_CLASSES = "R", ("A", "P", "V", "J", "W"), ("C", "E")
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
# Severity-1 flags a smaller layout can clear: when one of these blocks every
# explored plan, the default search also tries shorter lengths and the fits
# it left out (a parcel never reads worse than a smaller parcel inside it).
RESCUE_FLAGS = frozenset({"POND_DOES_NOT_FIT", "ROUTE_INFEASIBLE", "CAR_ROUTE_NONE", "ROUTE_OUTSIDE_ENVELOPE",
                          "PLAN_GEOMETRY_CONFLICT"})
RESCUE_MAX_FITS = 12
# Default behaviour: after the search (and its rescue), a pass that only
# adds layouts (reviews/40). Each layout the pond alone blocked is tried
# again with other pond shapes, then moved to leave its pond room
# (relocate_for_pond: one of the RELOCATE_SPOTS lowest pond spots reserved
# at a time). The plan drawn is the best of everything by the usual
# ranking, so no site reads worse than without the pass.
RELOCATE_SPOTS = 6
RELOCATE_ROUNDS = 3        # re-sizing the reserved pond for the building that fits around it
# The room-for-the-pond pass: when no 2:1 pond fits, the same volume as
# (bottom length / width, finer positions): the other proportions at the
# lidar samples first, then each at 20 ft offsets from them. Not below
# 1.5:1 (a short flow path; a design manual's minimum length-to-width is
# for the civil engineer, not checked here).
POND_FALLBACK_SHAPES = ((1.5, False), (3.0, False), (4.0, False), (2.0, True), (1.5, True), (3.0, True), (4.0, True))
POND_FALLBACK_RATIOS = tuple(sorted({k for k, _ in POND_FALLBACK_SHAPES}))
# Drainage (reviews/41): a pond on ground more than this above the pad is
# uphill of the paving it drains (POND_UPHILL). Up to this much is taken
# as a pond dug deeper (its footprint then 15 to 30% larger than drawn:
# POND_ABOVE_PAD says so from 1 ft).
POND_UPHILL_TOL_FT = 3.0
# Grading (reviews/42). A dock building is graded as a stepped pad: the
# floor at one level, the truck court DOCK_DROP_FT below it (a 48 in dock).
# Ground that rises DOCKS_UPHILL_FT or more from the building to its court
# puts the court on the high side (DOCKS_UPHILL). The pass turns the docks
# to the low side of the same footprint when that saves at least
# DOCK_TURN_MIN_SAVING of the earthwork and DOCK_TURN_MIN_CY.
DOCK_DROP_FT = 4.0
DOCKS_UPHILL_FT = 3.0
DOCK_TURN_MIN_SAVING = 0.10
DOCK_TURN_MIN_CY = 2000.0
# The turn is tried at most twice (the office at either end), on its own
# count (not the pond pass's allowance). It never puts the loading on the
# street side of the building where it was not, and never lengthens the
# cars' way in beyond DOCK_TURN_CAR_ROUTE x and + DOCK_TURN_CAR_ROUTE_FT.
DOCK_TURN_TRIES = 2
DOCK_TURN_CAR_ROUTE = 1.5
DOCK_TURN_CAR_ROUTE_FT = 200.0
STREET_SIDE_TOL_FT = 40.0
# Design for the truck (reviews/43; default behaviour). The truck route of
# the plan drawn is laid out again as a path a WB-67 can steer
# (siteplan_truck) and its swept path checked. In the court the centreline
# keeps DOCK_ZONE_FT off the dock wall (trailers stand at the doors) and
# runs TRUCK_LANE_FT beyond the court's middle; the driveway's opening is
# DRIVEWAY_MOUTH_FT round its point on the street. The best plan's trucks
# are sent to the end of the court from which they back in on the driver's
# side (sight side) when that costs nothing else: the trucks' way in no
# longer than SIGHT_ROUTE x and + SIGHT_ROUTE_FT.
DOCK_ZONE_FT = 60.0
TRUCK_LANE_FT = 30.0
COURT_RUN_IN_FT = 100.0
DRIVEWAY_MOUTH_FT = 60.0
THROAT_MIN_FT = 100.0
DRIVEWAY_END_FT = 110.0             # a truck driveway's centre from a frontage run's end (pick_driveways)
TRUCK_TIER_MAX = 8                  # layouts whose way in is laid out to settle the ranking
TRUCK_FIRST_MAX_TRIES = 48          # fits and full checks of the driveway-first step
SIGHT_ROUTE = 1.5
SIGHT_ROUTE_FT = 200.0
# The stepped pad is not figured where the lidar samples cover less than
# this share of the module (a partial figure would read as a small one).
STEPPED_MIN_COVER = 0.8
POND_ROOM_KEEP = 3                # plans the pass adds to the results (the best by the ranking)
DRAIN_FIRST_SPOTS = 2             # pond-first layouts: the lowest pond spots reserved, every cross-section fitted round each
POND_ROOM_MAX_TRIES = 90          # fits and full checks per site, all layouts together (a count: the same site, the same plan)
POND_ROOM_MAX_LAYOUTS = 6         # pond-blocked layouts the pass starts from, largest first
POND_ROOM_TIME_SHARE = 0.5        # of the time left after the search: the rest stays for the second building
POND_ROOM_MIN_LEFT = 0.5          # the pass runs only with this share of the site's budget still left (a slow site plans as before)
# Severity-1 flags no other layout of the same site can clear. The default
# rule makes them block feasibility (decide()), but they stay out of the
# layout's own check (o["ok"], geometry_ok), the ranking, the rescue pass and
# the second-building gate, so the plan drawn is the one drawn without them
# (the prototype checked caps only on the plan it kept). HEIGHT_CAP depends
# on the jurisdiction's draft height, ACCESS_CONSTRAINED on the frontage
# runs, neither on the layout.
LENGTH_INDEPENDENT_FLAGS = frozenset({"HEIGHT_CAP", "ACCESS_CONSTRAINED"})


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
    underground: bool = UNDERGROUND_DETENTION

    @property
    def fixes(self) -> bool:
        return self.behaviour != "prototype"

    @property
    def ranking(self) -> dict[str, Any]:
        return RANKING if self.fixes else PROTOTYPE_RANKING

    def decisions(self) -> dict[str, Any]:
        return {"parking_rule": self.parking, "atlanta_parking": self.atlanta_parking,
                "market_parking_tiers": [list(t) for t in MARKET_PARKING_TIERS],
                "trailer_target_tiers": [list(t) for t in self.trailer_tiers],
                "prototypes": [f"{k} {int(sitefit.PROTOTYPES[k]['W'])}" for k in PLANNER_PROTOTYPES],
                "market_depths": {"rear": list(MARKET_REAR_DEPTHS), "cross": list(MARKET_CROSS_DEPTHS)},
                "other_edge_min_pave_ft": OTHER_EDGE_MIN_PAVE_FT,
                "dual_hsg": DUAL_HSG_USE if self.fixes else "drained (first letter)",
                "underground_detention": ("yes" if self.underground else "no") if self.fixes else "not set (prototype)",
                "ranking": dict(self.ranking),
                "open_yard_ft": OPEN_YARD_RULE_FT if self.fixes else None,
                "tightened_rules": self.fixes,
                "prototype_dims": {k: {d: sitefit.PROTOTYPES[k].get(d) for d in ("W", "L", "min_length_ft")}
                                   for k in PLANNER_PROTOTYPES},
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
def jur_rules(name: str, tightened: bool = False) -> tuple[str, dict[str, Any], bool]:
    """(rules name, a copy of the DRAFT rules, defaulted?). tightened (the
    default behaviour): with JUR_TIGHTENED laid over the draft table, a
    value for every edge kind, and the open yard at the building
    (``open_yard_ft``, cut by plan_site)."""
    n = (name or "").lower()
    key, defaulted = next(((k, False) for k in JUR_DRAFT if k in n), (DEFAULT_JURISDICTION, True))
    rules = copy.deepcopy(JUR_DRAFT[key])
    if tightened:
        extra = copy.deepcopy(JUR_TIGHTENED.get(key, {}))
        for table in ("bldg", "pave"):
            rules[table].update(extra.pop(table, {}))
            rules[table].setdefault("OTHER_REAR", rules[table]["OTHER"])
            rules[table].setdefault("COMM", max(rules[table]["OTHER"], rules[table]["OTHER_REAR"]))
        rules["tightened"] = extra.pop("why", [])
        rules.update(extra)
        # The open yard is not a zoning value: plan_site cuts it out of the
        # building envelope in one piece, so the table keeps the codes' own
        # numbers (a 30 ft rear yard stays 30 ft in the record).
        rules["open_yard_ft"] = OPEN_YARD_RULE_FT
        rules["tightened"].append(f"the building stands {OPEN_YARD_RULE_FT} ft from every lot line that is not a road "
                                  "(the building code's open yard)")
    return (f"{DEFAULT_JURISDICTION} (default)" if defaulted else key), rules, defaulted


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


def pond_dims(v_acft: float, d: float = POND_DEPTH, z: float = POND_Z, ratio: float = 2.0) -> tuple[float, float, float, float]:
    """Bottom width W0 (bottom length ratio x W0, 2 by default) holding
    v_acft at depth d by the prismoidal formula; returns (W0, footprint
    length, footprint width, v) with freeboard and the rim included in the
    footprint."""
    v = v_acft * SQFT_PER_AC

    def vol(w0):
        a = lambda h: (ratio * w0 + 2 * z * h) * (w0 + 2 * z * h)
        return d * (a(0) + a(d) + 4 * a(d / 2)) / 6
    lo, hi = 1.0, 2000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if vol(mid) < v else (lo, mid)
    w0 = hi
    ht = d + FREEBOARD
    wt, lt = w0 + 2 * z * ht, ratio * w0 + 2 * z * ht
    return w0, lt + 2 * RIM, wt + 2 * RIM, v_acft


def pond_volume(w0: float, d: float = POND_DEPTH, z: float = POND_Z) -> float:
    """Volume (cubic ft) of a pond with bottom w0 x 2 w0 (for tests)."""
    a = lambda h: (2 * w0 + 2 * z * h) * (w0 + 2 * z * h)
    return d * (a(0) + a(d) + 4 * a(d / 2)) / 6


def hsg_letter(hsg: str | None, fixes: bool) -> tuple[str, str | None]:
    """(soil group letter used, note when it was a choice). The default
    behaviour notes every assumed B: a blank value, a soil check that was
    not run ("not checked: ...") and "none rated" each say which."""
    given = (hsg or "").strip()
    raw = given.upper()
    if not fixes:
        raw = raw or "B"
        return (raw[:1] if raw[:1] in HSG_K else "B"), None
    if re.fullmatch(r"[ABC]/D", raw):
        letter = dual_hsg_letter(raw)
        return letter, f"dual soil group {raw}: read as {letter} (the letter giving the larger pond)"
    if raw in HSG_K:
        return raw, None
    if not raw:
        return "B", "no soil value in the run: B assumed"
    if raw.startswith("NOT CHECKED"):
        return "B", f"soil group {given[:120]}; B assumed"
    return "B", "no soil group rated: B assumed"


def hsg_storage_rank(letter: str) -> float:
    """Detention storage per impervious acre for a soil letter, with the
    disturbed pervious area the planner uses (DISTURBED_FACTOR)."""
    ki, kp = HSG_K.get(letter, HSG_K["B"])
    return ki + kp * (DISTURBED_FACTOR - 1)


def dual_hsg_letter(raw: str) -> str:
    """The letter of a dual group ('A/D', 'B/D', 'C/D') that gives the
    larger storage (DUAL_HSG_USE)."""
    return max(raw.split("/"), key=lambda k: (hsg_storage_rank(k), k))


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
    state: str = "not_checked"                     # checked | partial | not_checked (fail closed: R2)
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
def residential_neighbour(a: dict[str, Any], rules: dict[str, Any] | None) -> bool:
    """Whether a county neighbour record counts as residential: a
    residential land-use or tax class, or, where the rules say
    ``ag_buffer``, an agricultural one. The one test both the edges and
    the sheet's neighbour shading use."""
    lu, cls = str(a.get("LUCode") or ""), str(a.get("ClassCode") or "")
    ag = bool(rules and rules.get("ag_buffer"))
    return lu.startswith("1") or cls.startswith(RES_CLASS) or (ag and cls.startswith(AG_CLASSES))


def classify_edges(parcel_ft, plane, neighbours, front_ft, rules: dict[str, Any] | None = None):
    """Boundary pieces labelled ROW / RES_SIDE / RES_REAR / OTHER.

    rules (the default behaviour passes its tightened rules; the prototype
    behaviour passes none and gets the four kinds above): also COMM, a
    line toward a commercial or institutional parcel, and OTHER_REAR, a
    rear line that is none of the others; and where the rules say
    ``ag_buffer``, an agricultural neighbour counts as a residential one.
    Neighbours are told apart by county tax class, the only thing the
    county record gives: it is a stand-in for their zoning."""
    edge = parcel_ft.boundary
    out = []
    row = edge.intersection(front_ft.buffer(5)) if front_ft is not None and not front_ft.is_empty else LineString()
    if not row.is_empty:
        out.append(("ROW", row))
    rest = edge.difference(front_ft.buffer(5)) if not row.is_empty else edge
    res_zone, comm_zone = [], []
    for a, s in neighbours:
        if residential_neighbour(a, rules):
            res_zone.append(plane.ft(s).buffer(20))
        elif rules is not None and str(a.get("ClassCode") or "").startswith(COMM_CLASSES):
            comm_zone.append(plane.ft(s).buffer(20))
    res_u = unary_union(res_zone) if res_zone else None
    dmax = (max(row.distance(Point(c)) for q in sitefit.polygon_parts(parcel_ft) for c in q.exterior.coords)
            if not row.is_empty else None)

    def side_and_rear(g):
        """The straight stretches of `g`, as (side line, rear line): a
        stretch is a rear one when its middle lies further from the road
        than half the parcel's depth. Each is merged back into runs, so an
        offset from it keeps its corners."""
        rear, side = [], []
        for ln in _lines_of(g):
            pts = list(ln.coords)
            for q0, q1 in zip(pts, pts[1:]):
                seg = LineString([q0, q1])
                if seg.length > 0:
                    (rear if row.distance(seg.interpolate(0.5, normalized=True)) > 0.5 * dmax else side).append(seg)
        # A neighbour is read 20 ft round a corner (the buffer above). That
        # stub is not a rear (or side) line of its own: it joins the run
        # beside it, so a 21 ft "rear" stub does not ask for the rear buffer.
        if side and rear:
            if sum(x.length for x in rear) <= EDGE_STUB_FT:
                side, rear = side + rear, []
            elif sum(x.length for x in side) <= EDGE_STUB_FT:
                side, rear = [], rear + side
        return _merged(side), _merged(rear)

    if res_u is not None:
        res = rest.intersection(res_u)
        rest = rest.difference(res_u)
        if row.is_empty:
            out.append(("RES_REAR", res))
        elif rules is not None:
            # per straight stretch: a neighbour along a whole side is a side
            # line, though it is read 20 ft round the back corner
            side, rear = side_and_rear(res)
            out += [(k, g) for k, g in (("RES_SIDE", side), ("RES_REAR", rear)) if not g.is_empty]
        else:
            for seg in getattr(res, "geoms", [res]):
                # Points from touching lines carry no length (the prototype
                # crashed on them in interpolate).
                if seg.is_empty or seg.geom_type not in ("LineString", "MultiLineString", "LinearRing"):
                    continue
                mid = seg.interpolate(0.5, normalized=True)
                kind = "RES_REAR" if row.distance(mid) > 0.5 * dmax else "RES_SIDE"
                out.append((kind, seg))
    if rules is not None:
        if comm_zone:
            comm_u = unary_union(comm_zone)
            comm = unary_union(_lines_of(rest.intersection(comm_u)))
            rest = rest.difference(comm_u)
            if not comm.is_empty:
                out.append(("COMM", _merged(_lines_of(comm))))
        if dmax is not None:
            rest, rear = side_and_rear(rest)
            if not rear.is_empty:
                out.append(("OTHER_REAR", rear))
    if not rest.is_empty:
        out.append(("OTHER", rest))
    return out


def _merged(lines: list) -> BaseGeometry:
    """Line pieces joined into runs (so an offset keeps its corners); an
    empty line for none. linemerge takes only a multi-part line."""
    if not lines:
        return LineString()
    u = unary_union(lines)
    return linemerge(u) if u.geom_type == "MultiLineString" else u


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
    code_trailers: int = 0      # a draft code minimum of truck spaces (0: the rules have none)
    flips: list = field(default_factory=list)   # the same fit turned the other ways round, best first


def build(v, L, r, fr: Frame, prog: Program, draw: bool = True) -> Plan:
    """One variant at length L with r car rows in frame fr. draw=False
    leaves out the stall ticks and door marks (drawing only, hundreds of
    lines): the fit search builds four frames per candidate length, and
    plan_site rebuilds only the plans it draws."""
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
    code_min = math.ceil(sf / prog.rules["truck_space_sf"]) if prog.rules.get("truck_space_sf") else 0
    target = max(target, code_min)       # a draft code minimum of truck spaces, where the rules have one
    if v["type"] == "rear":
        cd = car_rows_layout(r)[1]
        if v["cars"] == "side":
            uL, vB = TRUCK_ROAD, cd
            ue0, ue1 = 0, Lmod
            cars.append(fr.rect(uL, uL + L, 0, cd))
            for vr in car_rows_layout(r)[0]:
                for u in range(int(uL + 24), int(uL + L - 24), int(sw * 2)):
                    if draw:
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
                    if draw:
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
            for k in range(n + 1 if draw else 0):
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
            for k in range(n + 1 if draw else 0):
                ticks += [fr.line(u0 + 12 * k, 0, u0 + 12 * k, rowd), fr.line(u0 + 12 * k, depth - rowd, u0 + 12 * k, depth)]
            court_stalls = 2 * n
        roads += [fr.rect(0, uL, vB, vB + D), fr.rect(uL + L, uL + L + TRUCK_ROAD, 0, depth)]
        cars.append(fr.rect(uL + L + TRUCK_ROAD, Lmod, vB, vB + D))
        for vr in car_rows_layout(r)[0]:
            u0 = uL + L + TRUCK_ROAD + vr
            for vv in range(int(vB + 24), int(vB + D - 24), int(sw * 2)):
                if draw:
                    ticks.append(fr.line(u0, vv, u0 + CAR_ROW, vv))
        tin += [P(0, rowd + COURT / 2), P(0, depth - rowd - COURT / 2)]
        fb = [P(uL + L + TRUCK_ROAD / 2, 0), P(uL + L + TRUCK_ROAD / 2, depth)]
        cin.append(P(Lmod, vB + D / 2))
        pod = min(90.0, math.sqrt(OFFICE_SHARE * L * D / 2))
        offices += [fr.rect(uL + L - pod, uL + L, vB + D / 2 - pod, vB + D / 2), fr.rect(uL + L - pod, uL + L, vB + D / 2, vB + D / 2 + pod)]
        dock_v = [(vB, -1), (vB + D, 1)]
    per_wall = [doors // walls + (1 if k < doors % walls else 0) for k in range(walls)]
    for (vw, sg), nd in zip(dock_v, per_wall):
        for k in range(nd if draw else 0):
            u = uL + END_CLEAR + (L - 2 * END_CLEAR) * (k + 0.5) / max(nd, 1)
            doors_g.append(fr.line(u, vw, u, vw + sg * 12))
    req, rule = required_cars(prog.prule, sf, prog.opts)
    capc = int(r * car_per_row(v, L, sw) * prog.island)
    return Plan(v, L, r, fr, fr.rect(0, Lmod, 0, depth), b, courts, rows, cars, roads, offices, ticks, doors_g, tin, cin, fb,
                sf, doors, court_stalls, capc, req, rule, target, 32 if sf < 200000 else 36, code_min)


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
                    p = build(v, L, r, fr, prog, draw=False)
                    if B.buffer(0.5).contains(p.building):
                        cands.append((orient_score(p, row_line, res_line), p))
            if cands:
                found = max(cands, key=lambda t: t[0])[1]
                # The other ways round the same fit stands (docks to the
                # other side, the office to the other end), for a layout
                # whose routes fail the way it was first turned.
                found.flips = [q for _, q in sorted(cands, key=lambda t: -t[0]) if q is not found]
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


class PondRoom:
    """The room-for-the-pond pass's allowance (reviews/40): a count of
    tries (each fit around a reserved pond spot and each full check), so
    the same site gives the same plan, and its own deadline, so the pass
    never costs building 1 or the second-building search their time."""

    def __init__(self, search: "Search", max_tries: int | None = None, share: float | None = None):
        self.max_tries = POND_ROOM_MAX_TRIES if max_tries is None else max_tries
        self.tries = 0
        self.layouts = 0
        self.found = 0
        self.cut_short = False
        left = search.deadline - search.clock()
        own = Search(0.0, search.clock)
        own.deadline = search.clock() + max(0.0, left - (1.0 - (POND_ROOM_TIME_SHARE if share is None else share)) * left)
        own.memo = search.memo            # the same fits, memoised once
        self.search = own

    @property
    def spent(self) -> bool:
        return self.tries >= self.max_tries

    def take(self) -> bool:
        """Count one try; False once the allowance is spent."""
        if self.spent:
            return False
        self.tries += 1
        return True


def loading_faces_homes(p: "Plan", res_line) -> bool:
    """A truck court or trailer row within 150 ft of a residential edge
    (the planner's LOADING_FACES_RES test)."""
    return res_line is not None and any(c.distance(res_line) < 150 for c in p.courts + p.rows)


def faces_street(p: "Plan", row_line) -> bool:
    """A truck court or trailer row within 100 ft of the street frontage
    (the planner's STREET_FACING_DOCKS test)."""
    return row_line is not None and any(c.distance(row_line) < 100 for c in p.courts + p.rows)


def pond_rect(x: float, y: float, angle: float, v_acft: float) -> Polygon:
    """The footprint of one 2:1 pond of v_acft centred at (x, y), its length at `angle`."""
    _, Lf, Wf, _ = pond_dims(v_acft)
    return shapely.affinity.translate(shapely.affinity.rotate(box(-Lf / 2, -Wf / 2, Lf / 2, Wf / 2), angle, origin=(0, 0)), x, y)


def pond_spots(ctx: "Ctx", P: BaseGeometry, near_angle: float, v_acft: float) -> list[tuple[float, float, float]]:
    """Where a pond of v_acft (one pond, 2:1) could go, best first, as
    (x, y, angle): the lowest lidar samples on open ground inside the
    parcel (clear of water, corridors and the test cut), at the module's
    angles and square to the map, at most RELOCATE_SPOTS, each a pond
    length or more from the others. A smaller pond at the same spot fits
    too (it lies inside the larger one)."""
    _, Lf, Wf, _ = pond_dims(v_acft)
    room = (ctx.parcel.buffer(-10).difference(ctx.wet_buf).difference(ctx.trans).difference(ctx.cut)
            .intersection(P.buffer(ctx.rules["pave"].get("ROW", 10) + 1)))
    if room.is_empty:
        return []
    inside = prep(room)
    base = box(-Lf / 2, -Wf / 2, Lf / 2, Wf / 2)
    turned = [shapely.affinity.rotate(base, a, origin=(0, 0)) for a in (near_angle, near_angle + 90.0, 0.0, 90.0)]
    pts = sorted(((z, p) for p, z in ctx.dem.coords() if inside.contains(Point(p))), key=lambda t: (t[0], t[1]))
    angles = (near_angle, near_angle + 90.0, 0.0, 90.0)
    out: list[tuple[float, float, float]] = []
    for _z, (x, y) in pts:
        if any(math.hypot(x - qx, y - qy) < Lf for qx, qy, _ in out):
            continue
        for a, rb in zip(angles, turned):
            if inside.contains(shapely.affinity.translate(rb, x, y)):
                out.append((x, y, a))
                break
        if len(out) >= RELOCATE_SPOTS:
            break
    return out


def drainage_first(fits0: list, v_each: float, near_angle: float, target_sf: float, P: BaseGeometry, B: BaseGeometry,
                   ctx: "Ctx", row_line, res_line, room: PondRoom, keep: Callable[["Plan"], bool], out: list) -> None:
    """Pond first, building second (reviews/41): the order a civil engineer
    works in. The lowest open spots a pond could use (pond_spots, at most
    DRAIN_FIRST_SPOTS) are reserved in turn, and every cross-section is
    fitted round the reserved pond, the ones that fit largest on the open
    parcel first (`fits0`, the search's own fits: an upper bound on what
    fits round a pond). Each fit larger than `target_sf` (and than what
    this pass has found) that `keep` takes is checked in full, its pond
    sited only where the pad drains to it; when the pond then needs more
    room than was reserved, the spot is reserved again at that size and
    the fit tried once more. Plans that pass with a pond that drains are
    appended to `out` as they are found."""
    order = sorted(fits0, key=lambda q: -q.sf)
    best_sf = target_sf
    for x, y, a in pond_spots(ctx, P, near_angle, v_each)[:DRAIN_FIRST_SPOTS]:
        for q0 in order:
            if q0.sf <= best_sf:
                break                           # no later cross-section can beat what is in hand
            v_try = v_each
            for _round in range(2):
                if not room.take():
                    return
                hold = pond_rect(x, y, a, v_try).buffer(25)
                P2, B2 = clean(P.difference(hold)), clean(B.difference(hold))
                q = fit_variant(q0.v, P2, B2, ctx.prog, row_line, res_line, room.search)
                if q is None or q.sf <= best_sf or not keep(q):
                    break
                if not room.take():
                    return
                o2 = finish(q, ctx)
                if o2["ok"] and o2.get("drains", True):
                    out.append((q, o2))
                    room.found += 1
                    best_sf = q.sf
                    break
                v_next = o2["v_det"] / max(pond_count(o2["v_det"]), 1)
                if o2["pond_ok"] or v_next <= v_try + 0.01:
                    break                       # not the reserved room that failed it
                v_try = v_next


def relocate_for_pond(cur: "Plan", o: dict[str, Any], P: BaseGeometry, B: BaseGeometry, ctx: "Ctx", row_line, res_line,
                      room: PondRoom, keep: Callable[["Plan"], bool], out: list) -> None:
    """The same layout, moved to leave room for its pond; every plan found
    is appended to `out` as it is found (a cut keeps what was found).

    The search fits each layout as long as it goes wherever it fits
    longest, and checks the pond only after; when the pond then has no
    room it shortens the building where it stands, while the same
    building elsewhere on the parcel may leave room for it (reference site
    #21: 68,000 SF drawn, 84,000 SF with the pond moved; reviews/40). Here
    each low spot a pond could use (pond_spots) is reserved in turn, the
    variant is fitted around it no longer than `cur` (the planner's own
    fit), and each distinct fit is checked in full (finish). The pond
    reserved is first sized for `cur`; a shorter building needs a smaller
    one, so the spot is reserved again at the size the last fit needs and
    fitted again, up to RELOCATE_ROUNDS times while that frees room. A fit
    `keep` refuses (one that turns its docks to the street) is not
    checked."""
    count = max(pond_count(o["v_det"]), 1)
    v_each = o["v_det"] / count
    seen = set()
    for x, y, a in pond_spots(ctx, P, cur.frame.pl.angle_deg, v_each):
        v_try = v_each
        for _round in range(RELOCATE_ROUNDS):
            if not room.take():
                return
            hold = pond_rect(x, y, a, v_try).buffer(25)
            P2, B2 = clean(P.difference(hold)), clean(B.difference(hold))
            q = fit_variant(cur.v, P2, B2, ctx.prog, row_line, res_line, room.search, length_cap=int(cur.L))
            if q is None:
                break
            key = (q.L, q.r, tuple(round(c, 1) for c in q.module.bounds))
            if key in seen or not keep(q):
                break
            seen.add(key)
            if not room.take():
                return
            o2 = finish(q, ctx)
            if o2["ok"]:
                out.append((q, o2))
                room.found += 1
            v_next = o2["v_det"] / count
            if v_next >= v_try - 0.01:
                break
            v_try = v_next


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


def grid_route(start, goal, walk, cost_zone=None, step=GRID, stats: dict | None = None, simplify: float = 0.9,
               clean_joins: bool = False):
    """Shortest 8-connected path over `walk` from start to goal (Dijkstra,
    with steep cells costing STEEP_COST times more and a small bend
    penalty). Returns a LineString or None. Kept byte-equal to the
    prototype for parity; the diagonal cost is a listed later delta (R16).

    clean_joins (default behaviour): the straight joins from the start and
    goal points to their grid cells stay inside the walk area (no further
    out than the point itself, plus 1 ft). The prototype took the first
    walkable cell of the nearest ring, whose join can cut a corner of an
    excluded area by more than ROUTE_TOL_FT: the planner's own grid error,
    which then read as ROUTE_OUTSIDE_ENVELOPE on a plain rectangle."""
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
        first = None
        limit = walk.distance(p) + 1.0 if clean_joins else None
        for rad in range(0, 8):
            for di in range(-rad, rad + 1):
                for dj in range(-rad, rad + 1):
                    if max(abs(di), abs(dj)) == rad and 0 <= i0 + di < nx and 0 <= j0 + dj < ny and cell(i0 + di, j0 + dj):
                        if limit is None:
                            return (i0 + di, j0 + dj)
                        first = first or (i0 + di, j0 + dj)
                        q = (minx + (i0 + di) * step, miny + (j0 + dj) * step)
                        if route_outside_ft(LineString([(p.x, p.y), q]), walk) <= limit:
                            return (i0 + di, j0 + dj)
        return first
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
    if stats is not None:
        # The straight joins from the given points to their grid cells, for
        # route_stray_ft (the default behaviour measures them on their own).
        stats["joins"] = ((pts[0], pts[1]), (pts[-2], pts[-1]))
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


def route_stray_ft(route: LineString, walk: BaseGeometry, hard: BaseGeometry | None, joins) -> float:
    """The default behaviour's route check (R16). The grid part of the
    route is measured against its walk area as in route_outside_ft. The two
    straight joins from the driveway (or entry) points to their first grid
    cells are a drawing of the throat, not part of the search, and are
    measured against what they must not cross (`hard`: buildings, the wrong
    kind of pavement, water buffers, the test cut): the ft of join inside
    it. Without joins it is route_outside_ft."""
    if route is None or route.is_empty:
        return 0.0
    if not joins:
        return route_outside_ft(route, walk)
    js = [LineString(j) for j in joins if j[0] != j[1]]
    body = route.difference(unary_union([j.buffer(0.5) for j in js])) if js else route
    stray = route_outside_ft(body, walk) if not body.is_empty else 0.0
    if hard is not None and not hard.is_empty:
        edge = hard.boundary
        for j in js:
            inside = j.intersection(hard)
            if inside.is_empty:
                continue
            # how deep the join goes into what it must not cross, sampled every 2 ft
            dense = shapely.segmentize(inside, 2.0)
            stray = max([stray] + [edge.distance(Point(c)) for ln in _lines_of(dense) for c in ln.coords])
    return stray


def checked_route(start, goal, walk, ctx: "Ctx"):
    """grid_route, and in the default behaviour the unsimplified grid path
    when the simplified line cuts outside the walk area (the 0.9-cell
    simplify can cut corners by up to 18 ft)."""
    fixes = ctx.prog.opts.fixes
    rt = grid_route(start, goal, walk, ctx.steep, stats=ctx.route_steps, clean_joins=fixes)
    if rt is not None and fixes and route_outside_ft(rt, walk) > ROUTE_TOL_FT:
        rt = grid_route(start, goal, walk, ctx.steep, stats=ctx.route_steps, simplify=0.0, clean_joins=True)
    return rt


def place_sized_pond(region, Lf, Wf, dem: Dem, angles, near, pad_z, barrier, max_pts=700, fine=False, max_z=None):
    """Best pond rectangle (Lf x Wf) in region: low, close to `near`, on the
    same side of `barrier` (mapped wetlands). Distinct from
    detention.place_pond, which places the screener's fixed pond.
    `fine` also tries each centre moved half a sample step (20 ft) each
    way, at that sample's elevation: a tight piece of ground often holds
    the pond only between two samples (the lowest max_pts samples, nine
    positions each). `max_z`: no pond centred on ground above it (the
    drainage-first pass: a pond the paving can drain to)."""
    if region.is_empty:
        return None
    base = box(-Lf / 2, -Wf / 2, Lf / 2, Wf / 2)
    # Prepared contains is the same exact predicate, only faster; the
    # rotated rectangle does not depend on the point, so it is made once.
    inside = prep(region)
    turned = [shapely.affinity.rotate(base, a, origin=(0, 0)) for a in angles]
    pts = [(z, p) for p, z in dem.coords() if inside.contains(Point(p))]
    if not pts and max_z is not None:
        return None                 # no measured ground here: nothing to hold against the ceiling
    if not pts:
        minx, miny, maxx, maxy = region.bounds
        pts = [(0.0, (x, y)) for x in range(int(minx), int(maxx), 40) for y in range(int(miny), int(maxy), 40) if inside.contains(Point(x, y))]
    pts.sort(key=lambda t: t[0])
    if max_z is not None:
        pts = [t for t in pts if t[0] <= max_z]
    pts = pts[:max_pts]
    if fine:
        h = dem.step / 2
        pts = [(z, (x + dx, y + dy)) for z, (x, y) in pts for dx in (0.0, -h, h) for dy in (0.0, -h, h)]
    best = None
    for z, (x, y) in pts:
        for rb in turned:
            r = shapely.affinity.translate(rb, x, y)
            if not inside.contains(r):
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


def stepped_grading(p: "Plan", dem: Dem, drop: float = DOCK_DROP_FT) -> dict[str, Any] | None:
    """A stepped pad for one layout, from the lidar samples on its module:
    the building, car field and end roads at the floor level, the truck
    courts and trailer rows `drop` ft below it, the floor set where cut
    equals fill. Returns the floor and court elevations, the cut and fill
    (cubic yards; `moved_cy` is their mean, the earth that changes
    place), and how far the ground falls from the building to the courts
    (positive: the courts are on the low side, where the ground gives the
    dock its drop; negative: they are cut into the high side; `falls` has
    one figure per court, for a building with courts on two sides). None
    with fewer than four samples on the module, none on the building or
    on a court, or samples on less than STEPPED_MIN_COVER of the module.
    A screening figure: each sample stands for an equal share of the
    module's area (as the one-plane earthwork does); ground of the module
    beside a trailer row is graded at the floor; no slopes on the paving,
    no walls, no swell or shrink; nothing outside the module (the pond's
    excavation, drop yards, driveways)."""
    mod, low, bld = prep(p.module), prep(unary_union(p.courts + p.rows)), prep(p.building)
    courts = [prep(c) for c in p.courts]
    zs, lows, zb, zc = [], [], [], []
    per_court: list[list[float]] = [[] for _ in courts]
    for (x, y), z in dem.coords():
        pt = Point(x, y)
        if not mod.contains(pt):
            continue
        is_low = low.contains(pt)
        zs.append(z)
        lows.append(is_low)
        if is_low:
            zc.append(z)
            for k, c in enumerate(courts):
                if c.contains(pt):
                    per_court[k].append(z)
                    break
        elif bld.contains(pt):
            zb.append(z)
    if len(zs) < 4 or not zc or not zb or len(zs) * dem.step ** 2 < STEPPED_MIN_COVER * p.module.area:
        return None
    floor = (sum(zs) + drop * sum(lows)) / len(zs)          # cut = fill
    cell = p.module.area / len(zs) / 27.0
    cut = sum(max(z - (floor - drop * k), 0.0) for z, k in zip(zs, lows)) * cell
    fill = sum(max((floor - drop * k) - z, 0.0) for z, k in zip(zs, lows)) * cell
    zb_mean = sum(zb) / len(zb)
    return {"floor": floor, "court": floor - drop, "cut_cy": cut, "fill_cy": fill, "moved_cy": (cut + fill) / 2,
            "fall_to_court": zb_mean - sum(zc) / len(zc), "falls": [zb_mean - sum(c) / len(c) for c in per_court if c],
            "courts": len(p.courts), "step": dem.step}


def court_on_other_side(p: "Plan", prog: "Program", flipu: bool | None = None) -> "Plan":
    """The same layout on the same footprint with its court on the other
    side of the building (and, with `flipu`, the office at that end)."""
    fr = p.frame
    return build(p.v, p.L, p.r, Frame(fr.Lmod, fr.depth, fr.pl, fr.origin, not fr.flip, fr.flipu if flipu is None else flipu),
                 prog, draw=False)


def loading_on_street_side(p: "Plan", row_line) -> bool:
    """A truck court between the building and the street, at any distance:
    its middle is more than STREET_SIDE_TOL_FT nearer the street frontage
    than the building's (a court beside the building is not)."""
    if row_line is None:
        return False
    d0 = p.building.centroid.distance(row_line)
    return any(c.centroid.distance(row_line) < d0 - STREET_SIDE_TOL_FT for c in p.courts)


def car_route_ft(o: dict[str, Any]) -> float:
    return sum(ln.length for k, ln in o["routes"] if k == "car")


def dock_turn_saves(sg0: dict[str, Any] | None, sg: dict[str, Any] | None) -> bool:
    """The stepped pad `sg` moves materially less earth than `sg0`: by
    DOCK_TURN_MIN_SAVING of it and DOCK_TURN_MIN_CY at least."""
    return (sg0 is not None and sg is not None
            and sg0["moved_cy"] - sg["moved_cy"] >= max(DOCK_TURN_MIN_CY, DOCK_TURN_MIN_SAVING * sg0["moved_cy"]))


def dock_turn_blocked(q0: "Plan", q: "Plan", B0: BaseGeometry, row_line, res_line) -> str | None:
    """Why the footprint q0 turned (q) is not laid out at all, before any
    check of its routes and pond; None when it may be tried."""
    if not B0.buffer(0.5).contains(q.building):
        return "the building would leave the buildable area"
    if faces_street(q, row_line) and not faces_street(q0, row_line):
        return "it would turn the docks to the street"
    if loading_on_street_side(q, row_line) and not loading_on_street_side(q0, row_line):
        return "it would put the loading between the building and the street"
    if loading_faces_homes(q, res_line) and not loading_faces_homes(q0, res_line):
        return "it would turn the loading to homes"
    return None


TRUCK_OWN_FLAGS = {"PARKING_SHORT_FOR_LEAD_IN", "SHORT_THROAT", "DEAD_END_ACCESS"}


def layout_no_worse(o0: dict[str, Any], o2: dict[str, Any], truck: bool = False) -> str | None:
    """Why the same footprint finished another way (o2) is worse than the
    plan (o0); None when it is not. It must pass in full, its pond must
    drain where the plan's does, with no fewer trailer stalls, no new
    severity-2 flag and no much longer way in for cars. `truck`: o2 has
    its truck road laid out and o0 does not, so the conditions that road
    itself states (measured on the grid route in o0, or not at all) do
    not count as new."""
    sev2 = lambda o: {f for f, sv, _ in o["flags"] if sv == 2} - (TRUCK_OWN_FLAGS if truck else set())
    if not o2["ok"]:
        return "it fails its checks (" + (", ".join(sorted(f for f, sv, _ in o2["flags"] if sv == 1)) or "routes") + ")"
    if o0.get("drains", True) and not o2.get("drains", True):
        return "its pond would not drain"
    if o2["trailers"] < o0["trailers"]:
        return "it holds fewer trailer stalls"
    if sev2(o2) - sev2(o0):
        return "it adds a condition (" + ", ".join(sorted(sev2(o2) - sev2(o0))) + ")"
    c0, c2 = car_route_ft(o0), car_route_ft(o2)
    if c2 > max(DOCK_TURN_CAR_ROUTE * c0, c0 + DOCK_TURN_CAR_ROUTE_FT):
        return "the cars' way in would be much longer"
    return None


def dock_turn_refused(o0: dict[str, Any], o2: dict[str, Any]) -> str | None:
    """Why the turned layout, finished as o2, does not take the place of
    the plan (o0); None when it does. Nothing else may be worse
    (layout_no_worse), and its stepped pad must move materially less
    earth."""
    why = layout_no_worse(o0, o2)
    if why:
        return why
    if not dock_turn_saves(o0.get("stepped"), o2.get("stepped")):
        return "it saves too little earthwork"
    return None


def ponds_over_pad(ponds: list, dem: Dem, pad_z: float | None) -> list[float | None]:
    """For each pond, how far its ground (the mean of the lidar samples on
    its footprint) stands above the pad, in ft; negative when it is below.
    None for a pond with no sample on it, or with no pad elevation."""
    out: list[float | None] = []
    for r in ponds:
        inside = prep(r)
        zs = [z for (x, y), z in dem.coords() if inside.contains(Point(x, y))] if pad_z is not None else []
        out.append(sum(zs) / len(zs) - pad_z if zs else None)
    return out


def pond_over_pad(ponds: list, dem: Dem, pad_z: float | None) -> float | None:
    """The highest pond's figure of ponds_over_pad; None when none was measured."""
    measured = [x for x in ponds_over_pad(ponds, dem, pad_z) if x is not None]
    return max(measured) if measured else None


def court_frame(p: "Plan", court) -> tuple[tuple[float, float], tuple[float, float], float]:
    """A court's long axis (unit vector), the unit vector across it that
    points away from the building, and its length."""
    c = list(court.minimum_rotated_rectangle.exterior.coords)[:4]
    e1, e2 = (c[1][0] - c[0][0], c[1][1] - c[0][1]), (c[2][0] - c[1][0], c[2][1] - c[1][1])
    ax = e1 if math.hypot(*e1) >= math.hypot(*e2) else e2
    length = math.hypot(*ax)
    ax = (ax[0] / length, ax[1] / length)
    cc, bc = court.centroid, p.building.centroid
    n = (-ax[1], ax[0])
    if (cc.x - bc.x) * n[0] + (cc.y - bc.y) * n[1] < 0:
        n = (-n[0], -n[1])
    return ax, n, length


def dock_zone(p: "Plan") -> BaseGeometry:
    """The strip of each court in front of its dock wall, DOCK_ZONE_FT
    deep, where trailers stand at the doors: the building's own width
    carried out from the wall (not round its corners, where trucks turn
    in from the end roads)."""
    out = []
    for court in p.courts:
        _, n, _ = court_frame(p, court)
        moved = shapely.affinity.translate(p.building, n[0] * DOCK_ZONE_FT, n[1] * DOCK_ZONE_FT)
        out.append(unary_union([p.building, moved]).convex_hull.intersection(court))
    return unary_union(out) if out else Polygon()


def court_goal(p: "Plan", entry) -> dict[str, Any]:
    """Where a truck that reaches `entry` is going: the court nearest it,
    the direction along that court from its near end, a point on the
    truck lane COURT_RUN_IN_FT in (TRUCK_LANE_FT beyond the court's
    middle, away from the dock wall), and whether the docks are then on
    the driver's side (`sight`: the building on the left of the way in,
    so the driver backs in seeing the trailer's path)."""
    court = min(p.courts, key=lambda c: c.distance(entry))
    ax, n, length = court_frame(p, court)
    cc = court.centroid
    if (cc.x - entry.x) * ax[0] + (cc.y - entry.y) * ax[1] < 0:
        ax = (-ax[0], -ax[1])                       # from the end nearest the entry, inward
    run = min(COURT_RUN_IN_FT, 0.35 * length)
    g = (cc.x - ax[0] * (length / 2 - run) + n[0] * TRUCK_LANE_FT, cc.y - ax[1] * (length / 2 - run) + n[1] * TRUCK_LANE_FT)
    sight = (-ax[1]) * (-n[0]) + ax[0] * (-n[1]) > 0        # the building (at -n) on the left of the way in
    return {"court": court, "dir": math.atan2(ax[1], ax[0]), "goal": g, "sight": sight, "length": length}


def sight_entry(p: "Plan", entry) -> bool:
    return court_goal(p, entry)["sight"]


def truck_route_ft(o: dict[str, Any]) -> float:
    return next((ln.length for k, ln in o["routes"] if k == "truck"), 0.0)


def truck_side_refused(p: "Plan", o0: dict[str, Any], o2: dict[str, Any]) -> str | None:
    """Why the plan finished with its trucks sent to the sight-side end of
    the court (o2) does not take the place of the plan as it was (o0);
    None when it does. Nothing else may be worse (layout_no_worse), the
    trucks must now reach that end, and their way in may not be much
    longer."""
    why = layout_no_worse(o0, o2)
    if why:
        return why
    if o2.get("truck_entry") is None or not sight_entry(p, o2["truck_entry"]):
        return "no truck route reaches the sight-side end of the court"
    t0, t2 = truck_route_ft(o0), truck_route_ft(o2)
    if t2 > max(SIGHT_ROUTE * t0, t0 + SIGHT_ROUTE_FT):
        return "the trucks' way in would be much longer"
    return None


def truck_obstacles(p: "Plan", o: dict[str, Any], ctx: "Ctx", other: tuple | None = None) -> dict[str, BaseGeometry]:
    """What a truck's body must stay off, by kind, for the swept-path
    check of a drawn plan: the buildings, the parked cars and trailers,
    the ponds and drop yards, the car route where cars have their own
    driveway, the strips where the draft rules allow no paving, and the
    water buffers and test cut."""
    def u(gs):
        gs = [g for g in gs if g is not None and not g.is_empty]
        return unary_union(gs) if gs else Polygon()
    plans = [(p, o)] + ([other] if other else [])
    kinds = {"the building": u([q.building for q, _ in plans]),
             "the car parking": u([g for q, _ in plans for g in q.cars]),
             "the trailer stalls": u([g for q, _ in plans for g in q.rows] + [g for _, oo in plans for g in oo["drops"]]),
             "the pond": u([g for _, oo in plans for g in oo["ponds"]]),
             "the car route": u([pp for _, oo in plans if oo["cpt"] is not None for k, pp in oo["route_polys"] if k == "car"]),
             "ground where water or the test cut allows no paving": u([ctx.wet_buf, ctx.cut]).intersection(ctx.parcel),
             "the paving setback": ctx.parcel.difference(ctx.P.buffer(0.5))}
    return {k: g for k, g in kinds.items() if not g.is_empty}


def truck_design_for(p: "Plan", o: dict[str, Any], ctx: "Ctx", other: tuple | None = None) -> dict[str, Any] | None:
    """The truck path of one finished layout (reviews/43): a path a WB-67
    can steer from its driveway to the truck lane of its court, with the
    ground its body sweeps going in and coming back out checked against
    what the plan has placed (truck_obstacles; `other`: the other
    building's plan, for a second building). None when the layout has no
    truck route to lay out. Otherwise {"got": siteplan_truck.design's
    result or None, "goal", "kinds", "tpt"}. Computed once per layout
    (kept on `o`) when there is no other building."""
    cache = other is None
    other = other or None
    if cache and "_truck" in o:
        return o["_truck"]
    tpt, entry = o.get("tpt"), o.get("truck_entry")
    if tpt is None or entry is None or not ctx.runs or not any(k == "truck" for k, _ in o["routes"]):
        res = None
    else:
        parcel = ctx.parcel
        run = min(ctx.runs, key=lambda ln: ln.distance(tpt))
        sa = run.project(tpt)
        a, b = run.interpolate(max(sa - 1.0, 0.0)), run.interpolate(min(sa + 1.0, run.length))
        tx, ty = b.x - a.x, b.y - a.y
        m = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / m, tx / m
        if not parcel.buffer(1.0).contains(Point(tpt.x + nx * 25.0, tpt.y + ny * 25.0)):
            nx, ny = -nx, -ny                           # the normal that points into the site
        goal = court_goal(p, entry)
        kinds = truck_obstacles(p, o, ctx, other)
        mouth = tpt.buffer(DRIVEWAY_MOUTH_FT)
        if "the paving setback" in kinds:               # the driveway crosses the setback strip at its own opening
            kinds["the paving setback"] = kinds["the paving setback"].difference(mouth)
        hard = unary_union(list(kinds.values())) if kinds else Polygon()
        keep_off = unary_union([dock_zone(q) for q, _ in [(p, o)] + ([other] if other else [])])
        start = (tpt.x + nx * 12.0, tpt.y + ny * 12.0)
        got = truck.design(start, math.atan2(ny, nx), goal["goal"], goal["dir"], parcel, hard, keep_off, mouth)
        over_cars = False
        if (got is None or not got["ok"]) and p.cars:
            # The lead-in over the corner of the car field: where the
            # driveway stands in front of the car parking and to one side
            # of the truck road, the trucks' way in takes the stalls in
            # its path (counted in apply_truck_path), as an engineer would
            # re-lay the car field before shortening the building.
            own = unary_union(p.cars)
            kinds2 = dict(kinds)
            rest = kinds2.pop("the car parking").difference(own.buffer(0.5))
            if not rest.is_empty:
                kinds2["the car parking"] = rest
            hard2 = unary_union(list(kinds2.values())) if kinds2 else Polygon()
            got2 = truck.design(start, math.atan2(ny, nx), goal["goal"], goal["dir"], parcel, hard2, keep_off, mouth, dear=own.buffer(truck.BODY_CLEAR))
            if got2 is not None and got2["ok"]:
                got, kinds, over_cars = got2, kinds2, True
        res = {"got": got, "goal": goal, "kinds": kinds, "tpt": tpt, "over_cars": over_cars}
    if cache:
        o["_truck"] = res
    return res


def truck_line_and_pave(p: "Plan", d: dict[str, Any], parcel: BaseGeometry) -> tuple[LineString, BaseGeometry]:
    """The designed way in as the plan carries it: the centreline from the
    driveway's point on the street, and its pavement (the 40 ft truck
    road widened to the swept path, off everything a truck's body must
    stay off)."""
    got, kinds, tpt = d["got"], d["kinds"], d["tpt"]
    line = LineString([(tpt.x, tpt.y)] + list(got["line"].coords))
    solid = unary_union([g for k, g in kinds.items() if k != "the paving setback"]) if kinds else Polygon()
    pave = unary_union([line.buffer(TRUCK_ROAD / 2, cap_style=2), got["swept"].buffer(2.0)]).intersection(parcel).difference(solid)
    return line, pave


def truck_record(p: "Plan", o: dict[str, Any], d: dict[str, Any], parcel: BaseGeometry, grid_ft: float, grid_pave_sf: float) -> dict[str, Any]:
    """Write a layout's truck design onto its plan `o`: `o["truck_path"]`
    for the record and the flags it raises (in place of the grid route's
    SHORT_THROAT and DEAD_END_ACCESS, which are measured again on the
    path). `o["routes"]` already holds the designed line when one
    exists."""
    got, goal, kinds = d["got"], d["goal"], d["kinds"]
    info: dict[str, Any] = {"designed": got is not None, "vehicle": truck.VEHICLE_NAME, "backing": "sight" if goal["sight"] else "blind",
                            "courts": len(p.courts), "grid_route_ft": round(grid_ft)}
    flags = [t for t in o["flags"] if t[0] not in ("SHORT_THROAT", "DEAD_END_ACCESS")]
    if got is None:
        info["reason"] = "no path"
        flags.append(("TRUCK_PATH_NONE", 2, f"no path a {truck.VEHICLE_NAME} can steer ({truck.R_MIN:.0f} ft radius) from the driveway to the "
                                            "truck court on the ground this plan leaves: the driveway does not line up with the "
                                            "truck road, or what stands between them leaves no room to turn; the route drawn is "
                                            "the search's own line"))
        flags += [t for t in o["flags"] if t[0] == "SHORT_THROAT"]
        length = grid_ft
    else:
        line = next(ln for k, ln in o["routes"] if k == "truck")
        pave = next(pp for k, pp in o["route_polys"] if k == "truck")
        length = line.length
        # the throat: from the street to where the path first enters a truck court
        courts_u = unary_union(p.courts)
        throat = next((line.project(Point(c)) for c in shapely.segmentize(line, 5.0).coords if courts_u.contains(Point(c))), length)
        info.update({"length_ft": round(length), "throat_ft": round(throat), "bends": got["bends"], "reverse_bends": got["reverse"],
                     "min_radius_ft": got["min_radius_ft"], "swept_clip_ft": round(got["clip_ft"], 1), "swept_ok": got["ok"],
                     "pavement_sf": rnd(pave.area, 100), "grid_pavement_sf": rnd(grid_pave_sf, 100)})
        if not got["ok"]:
            at = Point(got["clip_at"])
            what = min(kinds, key=lambda k: kinds[k].distance(at)) if kinds else "the property line"
            if not parcel.buffer(1.0).contains(at):
                what = "the property line"
            info["swept_clips"] = what
            flags.append(("TRUCK_SWEPT_PATH", 2, f"a {truck.VEHICLE_NAME} cannot make the way in as drawn: at a bend about "
                                                 f"{line.project(at):,.0f} ft from the street its body sweeps about {got['clip_ft']:.0f} ft "
                                                 f"over {what}"))
        if d["over_cars"]:
            own = unary_union(p.cars)
            taken = math.ceil(pave.intersection(own).area / (own.area / max(p.car_cap, 1)))
            left = p.car_cap - taken
            o["car_stalls_taken"] = taken
            info["car_stalls_taken"] = taken
            if left >= p.cars_required:
                flags.append(("TRUCK_LEAD_IN_OVER_PARKING", 3, f"the driveway stands in front of the car field, to one side of the truck road: "
                                                               f"the trucks' way in takes the corner of the car field, about {taken} car "
                                                               f"stalls ({left} left in the rows, {p.cars_required} required)"))
            else:
                flags.append(("PARKING_SHORT_FOR_LEAD_IN", 2, f"the trucks' way in from the driveway takes the corner of the car field, about "
                                                              f"{taken} car stalls: {left} are left of the {p.cars_required} required (another "
                                                              "car row, or a shorter building)"))
        if got["reverse"]:
            flags.append(("TRUCK_REVERSE_BEND", 3, f"{got['reverse']} reverse bend(s) on the trucks' way in with less than "
                                                   f"{truck.REVERSE_MIN_FT:.0f} ft of straight between (a truck's length): passable at "
                                                   "walking speed, better straightened"))
        if throat < THROAT_MIN_FT:
            flags.append(("SHORT_THROAT", 2, f"truck throat about {throat:.0f} ft from the street to the court (100 ft wanted for stacking)"))
    if o["cpt"] is None and length > 750:
        flags.append(("DEAD_END_ACCESS", 2, f"single access about {length:,.0f} ft long (over 750 ft needs fire-official approval)"))
    if len(p.courts) == 1 and not goal["sight"]:
        flags.append(("BLIND_SIDE_BACKING", 3, "trucks enter the court with the dock doors on their right, so they back in on the "
                                               "blind side; the other end of the court is the driver's side"))
    o["flags"][:] = flags       # in place: finish goes on adding to the same list
    o["truck_path"] = info
    return info


def apply_truck_path(p: "Plan", o: dict[str, Any], ctx: "Ctx", other: tuple | None = None) -> dict[str, Any]:
    """The truck path of a plan that was finished without one (the search's
    own plans; a second building): laid out round what the plan has
    placed (truck_design_for) and put on `o`, once. The ponds, yards and
    detention stay as the search placed them. Returns o["truck_path"]."""
    if "truck_path" in o:
        return o["truck_path"]
    d = truck_design_for(p, o, ctx, other)
    if d is None:
        o["truck_path"] = {"designed": False, "reason": "no truck route"}
        return o["truck_path"]
    old_rt = next(ln for k, ln in o["routes"] if k == "truck")
    old_pave = next(pp for k, pp in o["route_polys"] if k == "truck")
    if d["got"] is not None:
        line, pave = truck_line_and_pave(p, d, ctx.parcel)
        i = next(k for k, t in enumerate(o["routes"]) if t[0] == "truck")
        j = next(k for k, t in enumerate(o["route_polys"]) if t[0] == "truck")
        o["routes"][i] = ("truck", line)
        o["route_polys"][j] = ("truck", pave)
    return truck_record(p, o, d, ctx.parcel, old_rt.length, old_pave.area)


def driveway_strips(runs: list, lead: float) -> list[tuple[BaseGeometry, Any]]:
    """For each end of each frontage run long enough for a truck driveway:
    the strip of the plane within `lead` ft of that end, measured along
    the run, and the nearest spot on the run where a truck driveway can
    go (DRIVEWAY_END_FT from the end). A module kept out of the strip has
    its end road in line with that driveway."""
    out = []
    for ln in runs:
        if ln.length < 2 * DRIVEWAY_END_FT:
            continue
        for end, nxt, spot in ((ln.coords[0], ln.interpolate(1.0), ln.interpolate(DRIVEWAY_END_FT)),
                               (ln.coords[-1], ln.interpolate(ln.length - 1.0), ln.interpolate(ln.length - DRIVEWAY_END_FT))):
            tx, ty = nxt.x - end[0], nxt.y - end[1]
            m = math.hypot(tx, ty) or 1.0
            tx, ty = tx / m, ty / m
            big = 1e5
            a0, a1 = (end[0] - tx * big, end[1] - ty * big), (end[0] + tx * lead, end[1] + ty * lead)
            strip = Polygon([(a0[0] - ty * big, a0[1] + tx * big), (a1[0] - ty * big, a1[1] + tx * big),
                             (a1[0] + ty * big, a1[1] - tx * big), (a0[0] + ty * big, a0[1] - tx * big)])
            out.append((strip, spot))
    return out


def truck_first(fits0: list, target_sf: float, P: BaseGeometry, B: BaseGeometry, ctx: "Ctx", row_line, res_line,
                tries: list[int], keep: Callable[["Plan"], bool], out: list) -> None:
    """Driveway first (reviews/43). A truck driveway cannot stand nearer a
    frontage corner than DRIVEWAY_END_FT (its curb radii), and a layout
    whose end road lies nearer the side line than that has no way in a
    truck can steer. For each end of the frontage, then for all of them
    at once, the module is kept out of the strip beside that corner, so
    its end road lines up with a driveway that can be built; every cross-section is fitted to what is
    left (largest on the open parcel first, stopping when none left can
    beat what is in hand), finished with its trucks sent to that end, and
    kept when it passes in full, its pond drains and a WB-67 can drive
    it. `tries` ([left]) is this step's own count of fits and checks."""
    order = sorted(fits0, key=lambda q: -q.sf)
    best_sf = target_sf
    strips = driveway_strips(ctx.runs, DRIVEWAY_END_FT - TRUCK_ROAD / 2)
    if len(strips) > 1:
        # ... and out of every such strip at once: wherever the fit then
        # stands the module, an end road is in line with a driveway
        strips.append((unary_union([g for g, _ in strips]), None))
    for strip, spot in strips:
        P2, B2 = clean(P.difference(strip)), clean(B.difference(strip))
        ctx2 = dataclass_replace(ctx, truck_target=spot, truck_design=True)
        for q0 in order:
            if q0.sf <= best_sf:
                break                           # no later cross-section can beat what is in hand
            if tries[0] <= 0:
                return
            tries[0] -= 1
            q = fit_variant(q0.v, P2, B2, ctx.prog, row_line, res_line, ctx.search)
            if q is None or q.sf <= best_sf:
                continue
            for cand in [q] + q.flips:          # the same fit the other ways round, when this way faces the street or has no way in
                if not keep(cand):
                    continue
                if tries[0] <= 0:
                    return
                tries[0] -= 1
                o2 = finish(cand, ctx2)
                if o2["ok"] and o2.get("drains", True) and o2.get("truck_ok"):
                    o2["drivable"] = True
                    out.append((cand, o2))
                    best_sf = cand.sf
                    break
            if best_sf >= q0.sf:
                break


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
    inside = prep(region)
    pts = []
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            p = Point(x, y)
            if inside.contains(p):
                pts.append((p.distance(target), (x, y)))
            x += step
        y += step
    pts.sort()
    base = box(-Lr / 2, -Wr / 2, Lr / 2, Wr / 2)
    turned = [shapely.affinity.rotate(base, a, origin=(0, 0)) for a in angles]
    for _, (x, y) in pts[:1500]:
        for rb in turned:
            r = shapely.affinity.translate(rb, x, y)
            if inside.contains(r):
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
    if rules.get("height") and clear + ROOF_ALLOWANCE_FT > rules["height"]:
        clear = rules["height"] - ROOF_ALLOWANCE_FT
        sev = 1 if clear < CLEAR_BELOW_MARKET_FT else 2
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
            # one decimal in the default behaviour: a 5.1 ft stray over the
            # 5 ft tolerance must not read as "(5 ft)"
            out.append({"check": f"{kind} route outside its paving envelope",
                        "ft": round(ft, 1) if ctx.prog.opts.fixes else round(ft)})
    return out


def _blocking(o: dict[str, Any]) -> set[str]:
    """The severity-1 flag names of one finished plan that another layout
    could clear (LENGTH_INDEPENDENT_FLAGS left out)."""
    return {f for f, sv, _ in o["flags"] if sv == 1} - LENGTH_INDEPENDENT_FLAGS


def assemblage_flags(meta: dict[str, Any], jname: str) -> list[tuple[str, int, str]]:
    """Flags for an assemblage whose members' rules differ. The planner can
    apply one jurisdiction's draft rules to the whole site (the best-ranked
    member's); members under other draft rules make that a severity-1
    flag (the plan is drawn, not counted: a human decides which code
    governs), a different zoning in the same jurisdiction a severity-2 one,
    and members not in ranked.csv (rules unknown) a severity-2 one."""
    out: list[tuple[str, int, str]] = []
    members = [m for m in meta.get("members") or [] if isinstance(m, dict)]
    rules = {}
    for m in members:
        rules.setdefault(jur_rules(m.get("jurisdiction", ""))[0], []).append(f"#{m.get('rank')}")
    if len(rules) > 1:
        listed = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(rules.items()))
        out.append(("ASSEMBLAGE_MIXED_RULES", 1, f"the members fall under different draft rules ({listed}); "
                                                  f"the plan uses {jname}'s for the whole site: which code governs is not known"))
    else:
        zones = {}
        for m in members:
            zones.setdefault((m.get("zoning") or "unknown").upper(), []).append(f"#{m.get('rank')}")
        if len(zones) > 1:
            listed = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(zones.items()))
            out.append(("ASSEMBLAGE_MIXED_RULES", 2, f"the members are zoned differently ({listed}); "
                                                      "the draft rules used are the jurisdiction's, not per district: verify"))
    n = meta.get("members_unranked") or 0
    if n:
        out.append(("ASSEMBLAGE_MEMBER_NOT_RANKED", 2, f"{n} member(s) not in ranked.csv: their jurisdiction, zoning and "
                                                        "soil group are not known here and not compared"))
    return out


def assemblage_hsg(meta: dict[str, Any], letter: str, note: str | None) -> tuple[str, str | None]:
    """The soil letter of an assemblage: the members' letter that gives the
    larger pond (default behaviour), with a note when the members differ."""
    letters = {}
    for m in meta.get("members") or []:
        if isinstance(m, dict):
            k, _ = hsg_letter(m.get("hsg"), True)
            letters.setdefault(k, []).append(f"#{m.get('rank')}")
    if len(letters) <= 1:
        return letter, note
    best = max([letter] + list(letters), key=lambda k: (hsg_storage_rank(k), k))
    listed = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(letters.items()))
    return best, f"members' soil groups differ ({listed}): {best} used, the letter giving the larger pond"


def decide(flags: list[tuple[str, int, str]], o_ok: bool, hypothetical: bool, opts: PlanOptions) -> tuple[bool | None, list[str]]:
    """(feasible, gated flags). Incomplete inputs give None in both modes;
    the prototype rule is o_ok and access; the default rule is no
    severity-1 flag outside GATE_FLAGS (R4)."""
    names = {f for f, _, _ in flags}
    gated = sorted(names & GATE_FLAGS)
    if names & NOT_CHECKED_FLAGS:
        return None, gated
    if not opts.fixes:
        # The prototype had no time budget; a search cut short is never
        # feasible in either mode.
        return (o_ok and not hypothetical and "PLAN_BUDGET_EXCEEDED" not in names), gated
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
    roads: list = field(default_factory=list)     # labels of every road fronted, longest first (may be empty)


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
    pond_room: bool = False      # the room-for-the-pond pass (reviews/40): other pond shapes when no 2:1 pond fits
    sight_first: bool = False    # trucks to the court's sight-side end first (reviews/43)
    truck_target: Any = None     # the truck driveway sought near this point (driveway first, reviews/43)
    truck_design: bool = False   # the truck route laid out as a WB-67 steers it, before the pond is sited (reviews/43)


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
    B: BaseGeometry | None = None          # building envelope (draft building setbacks)
    edges: list = field(default_factory=list)   # [(ROW | RES_SIDE | RES_REAR | OTHER, line)]
    # Drawing only (nothing in the record reads them): the lidar samples,
    # for the sheet's contour lines, and the neighbouring county parcels,
    # [(RES | OTHER, feet shape)], drawn as context around the site.
    dem: Dem | None = None
    context: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# One plan: driveways, routes, detention, yards, grading
# ---------------------------------------------------------------------------
def home_pieces(parcel: BaseGeometry, module: BaseGeometry) -> BaseGeometry | None:
    """The pieces of a parcel in separate pieces that the building module
    is on, where its ponds and drop yards go (a pond across a road from
    the building is not on the site the building can drain to or reach);
    None for a parcel in one piece."""
    pieces = parts(parcel)
    if len(pieces) < 2:
        return None
    on = [g for g in pieces if g.intersects(module)]
    return unary_union(on) if on else None


def own_truck_area(p: Plan, fixes: bool) -> BaseGeometry:
    """The module's own end roads and court, inset 6 ft (half a truck lane
    less the curb), where truck routes may run.

    The end-road and court rectangles share an edge only in exact
    arithmetic: once rotated, their union is often 2-3 polygons with a
    hairline crack along the seam, and buffer(-6) erodes 6 ft on each side
    of it, a 12 ft band right where every truck route enters the court
    (ROUTE_OUTSIDE_ENVELOPE on a plain rotated rectangle). The default
    behaviour closes the seams first (+0.5 ft, then -6.5 ft, mitred so the
    rectangles keep their corners); the prototype behaviour keeps the
    prototype's plain inset for parity."""
    u = unary_union(p.roads + p.courts)
    if not fixes:
        return u.buffer(-6)
    return u.buffer(0.5, join_style=2).buffer(-6.5, join_style=2)


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
    access_constrained = False
    if runs:
        ttarget = min(p.truck_entries, key=lambda q: q.distance(unary_union(runs)))
        if ctx.truck_target is not None:        # the end road nearest a driveway that can be built (reviews/43)
            ttarget = min(p.truck_entries + p.fallback_entries, key=lambda q: q.distance(ctx.truck_target))
        if ctx.sight_first:     # the driveway toward the court's sight-side end (reviews/43)
            ttarget = next((q for q in p.truck_entries if sight_entry(p, q)), ttarget)
        tpt, cpt, okrun = pick_driveways(runs, ttarget, p.car_entries[0], sp, taken)
        if not okrun:
            access_constrained = True
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
    own_truck = own_truck_area(p, fixes)    # the module's own end roads and court carry trucks too
    walk_t = (base.buffer(-20).union(throat_zone(tpt, parcel, unary_union([wet_buf, cut]))).difference(occupied.buffer(-2))
              .difference(unary_union(p.cars).buffer(8)).union(entries.difference(unary_union(p.cars))).union(own_truck))
    rt = t_entry = None
    if tpt is not None:
        for group, note in ((p.truck_entries, None), (p.fallback_entries, "trucks reach the court along the building's end road from the office side")):
            order = sorted(group, key=lambda q: q.distance(tpt))
            if ctx.sight_first:
                order = sorted(order, key=lambda q: not sight_entry(p, q))      # stable: the sight-side end first
            for tgt in order:
                ctx.search.check()
                rt = checked_route(tpt, tgt, walk_t, ctx)
                if rt is not None:
                    t_entry = tgt
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
            if fixes:
                hard_t = unary_union([occupied.buffer(-2).difference(own_truck.buffer(2)), wet_buf, cut]
                                     + ([others["blocked"]] if others is not None else []))
                route_outside.append(("truck", route_stray_ft(rt, walk_t, hard_t, ctx.route_steps.get("joins"))))
            else:
                route_outside.append(("truck", route_outside_ft(rt, walk_t)))
    # Truck first, then the pond (reviews/43): the way in as a WB-67 steers
    # it, laid out round the building and its car and trailer stalls only.
    # The car route, the ponds and the drop yards below then go round the
    # truck road as it will be built. (Building 1 only; a second building's
    # way in is laid out afterwards, round what stands.)
    t_design = t_grid = None
    if fixes and ctx.truck_design and rt is not None and others is None:
        t_design = truck_design_for(p, {"tpt": tpt, "truck_entry": t_entry, "routes": routes, "route_polys": [], "ponds": [], "drops": [],
                                        "cpt": None}, ctx, other=())
        if t_design is not None and t_design["got"] is not None and t_design["got"]["ok"]:
            t_grid = (rt.length, route_polys[-1][1].area)
            rt, t_pave = truck_line_and_pave(p, t_design, parcel)
            routes[-1], route_polys[-1], route_outside[-1] = ("truck", rt), ("truck", t_pave), ("truck", 0.0)
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
            if fixes:
                hard_c = unary_union([occupied.buffer(-2).difference(unary_union(p.cars).buffer(2)),
                                      unary_union(p.courts + p.roads + p.rows), wet_buf, cut]
                                     + ([truck_poly] if cpt is not None else [])
                                     + ([others["blocked"]] if others is not None else []))
                route_outside.append(("car", route_stray_ft(rc, walk_c, hard_c, ctx.route_steps.get("joins"))))
            else:
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
    home = home_pieces(parcel, p.module) if fixes else None
    if home is not None:
        region0 = region0.intersection(home)
    if others is not None:
        region0 = region0.difference(others["blocked"])
    angles = [0, 90, 45, 135, p.frame.pl.angle_deg, p.frame.pl.angle_deg + 90]
    # The drainage-first pass (reviews/41) sites ponds only where the pad
    # can drain to them: on ground no higher than the pad plus what a
    # deeper pond takes up.
    max_z = pad_z + POND_UPHILL_TOL_FT if (ctx.pond_room and pad_z is not None) else None

    def site_ponds(region, n, Lf_, Wf_, v_each):
        """[(pond, bottom length / width)]."""
        out = []
        for _k in range(n):
            ctx.search.check()
            r, k_used = place_sized_pond(region, Lf_, Wf_, dem, angles, p.module, pad_z, constraints, max_z=max_z), 2.0
            if r is None and ctx.pond_room and not problem:
                # The room-for-the-pond pass only (reviews/40): no 2:1 pond
                # fits at the samples, so the same volume in other
                # proportions, then at a finer spacing. Not for a layout a
                # route already fails.
                for k, fine in POND_FALLBACK_SHAPES:
                    _, Lk, Wk, _ = pond_dims(v_each, ratio=k)
                    r = place_sized_pond(region, Lk, Wk, dem, angles, p.module, pad_z, constraints, fine=fine, max_z=max_z)
                    if r is not None:
                        k_used = k
                        break
            if r is None:
                break
            out.append((r, k_used))
            region = region.difference(r.buffer(20))
        return out
    placed = site_ponds(region0, npd, Lf, Wf, v / npd)
    ponds = [r for r, _ in placed]
    pond_ok = len(ponds) == npd
    # drop yard near the court, counted only with a truck route
    drops, trailers = [], p.court_stalls
    if ds:
        free = (P.difference(occupied.buffer(20)).difference(rp.buffer(8)).difference(wet_buf).difference(cut)
                .difference(unary_union(ponds).buffer(20) if ponds else Polygon()))
        if others is not None:
            free = free.difference(others["blocked"])
        if home is not None:
            free = free.intersection(home)
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
        if abs(v - v_sized) > 1e-9:
            # The ponds are re-sited for the new volume, larger or smaller,
            # so the storage, the pond count and the drawn ponds agree.
            npd2 = pond_count(v)
            _, Lf2, Wf2, _ = pond_dims(v / npd2)
            region = region0.difference(all_rp.buffer(10))
            for d in drops:
                region = region.difference(d.buffer(20))
            placed2 = site_ponds(region, npd2, Lf2, Wf2, v / npd2)
            ponds2 = [r for r, _ in placed2]
            if v > v_sized or len(ponds2) == npd2:
                ponds, placed, npd, v_sized = ponds2, placed2, npd2, v
                pond_ok = len(ponds) == npd
            # else: smaller ponds did not fit where the larger ones are
            # (the yard links took the room); keep the larger ponds and
            # report the volume they were sized for (v_sized).
    # Gravity (reviews/41): the paving drains to the pond only if the pond
    # is lower. The pad is one plane at the module's mean ground; a pond
    # on ground well above it cannot fill by gravity as drawn.
    pond_each = ponds_over_pad(ponds, dem, pad_z)
    pond_over = max([x for x in pond_each if x is not None], default=None)
    uphill = pond_ok and pond_over is not None and pond_over > POND_UPHILL_TOL_FT
    if uphill and fixes:
        flags.append(("POND_UPHILL", 2, f"the pond stands on ground about {pond_over:.0f} ft above the building pad, so the paving "
                                        "cannot drain to it by gravity as drawn: a pond on the low side of the site, a much "
                                        "deeper and larger pond, or pumping"))
    elif fixes and pond_ok and pond_over is not None and pond_over >= 1.0:
        flags.append(("POND_ABOVE_PAD", 3, f"the pond's ground is about {pond_over:.0f} ft above the building pad, inside the "
                                           f"{POND_UPHILL_TOL_FT:.0f} ft the planner takes as a deeper pond: dug that much deeper at "
                                           "4:1 slopes its footprint grows by roughly 15 to 30%, which is not drawn"))
    # Grading (reviews/42): the stepped pad, and a court on the high side.
    stepped = stepped_grading(p, dem) if fixes else None
    if stepped is not None and len(p.courts) == 1 and stepped["fall_to_court"] <= -DOCKS_UPHILL_FT:
        # (a building with courts on two sides has one on each side of any slope: its cost is in the stepped earthwork)
        other = stepped_grading(court_on_other_side(p, ctx.prog), dem)
        extra = stepped["moved_cy"] - other["moved_cy"] if other is not None else 0.0
        flags.append(("DOCKS_UPHILL", 3, f"the ground rises about {-stepped['fall_to_court']:.0f} ft from the building to its truck court, "
                                         f"which sits {DOCK_DROP_FT:.0f} ft below the floor: the court is cut into the high side"
                                         + (f" (about {rnd(extra, 1000):,} CY more earthwork than the same footprint with its "
                                            "court on the other side, a layout not checked here)" if extra >= 1000 else "")))
    if not pond_ok:
        way_out = ("underground detention (vaults) or a smaller building" if not fixes or ctx.prog.opts.underground
                   else "a smaller building or detention off site (underground detention is not used in this market)")
        flags.append(("POND_DOES_NOT_FIT", 1 if fixes else 2, f"{npd} pond(s) of {v_sized / npd:.1f} ac-ft do not fit on the development side: {way_out}"))
    # grading over the whole module
    mod_pts = pad
    relief = (max(mod_pts) - min(mod_pts)) if len(mod_pts) > 3 else None
    cutfill = None
    pad_elev = max_cut = max_fill = None
    if len(mod_pts) > 3:
        mean = sum(mod_pts) / len(mod_pts)
        cutfill = p.module.area * (sum(abs(z - mean) for z in mod_pts) / len(mod_pts)) / 2 / 27
        # One level pad at the mean ground: the deepest cut and fill it needs.
        pad_elev, max_cut, max_fill = mean, max(mod_pts) - mean, mean - min(mod_pts)
    steep_mod = (p.module.intersection(steep).area / p.module.area * 100) if not steep.is_empty else 0.0
    # The layout's own check. In the default behaviour a frontage too short
    # for any truck driveway (ACCESS_CONSTRAINED, the same for every layout)
    # does not fail it: decide() still blocks the site on that flag.
    feasible = pond_ok and not problem and (tpt is not None or not runs or (fixes and access_constrained))
    o = {"ok": feasible, "pond_ok": pond_ok, "route_problem": problem, "tpt": tpt, "cpt": cpt, "shared_access": shared,
         "truck_entry": t_entry, "pond_room_mode": ctx.pond_room,
         "routes": routes, "route_polys": route_polys, "route_outside": route_outside, "n_main_routes": n_main_routes,
         "drops": drops, "trailers": trailers, "imp_ac": imp_ac, "v_det": v_sized if fixes else v, "ponds": ponds, "relief": relief,
         "cutfill_cy": cutfill, "steep_module_pct": steep_mod, "dem_points_module": len(mod_pts), "flags": flags,
         "clear": p.clear,
         # read-outs for siteplan_assess; no decision reads them
         "pad_elev": pad_elev, "max_cut": max_cut, "max_fill": max_fill, "disturbed_ac": disturbed}
    if fixes:
        o["pond_ratios"] = [k for _, k in placed]
        o["pond_over_pad"], o["pond_over_each"], o["drains"] = pond_over, pond_each, not uphill
        o["stepped"] = stepped
    if fixes and ctx.truck_design and others is None:
        o["truck_ok"] = t_grid is not None
        if t_grid is not None:
            truck_record(p, o, t_design, parcel, *t_grid)
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
        # Geometry and caps apart: a flag no other layout can clear (a draft
        # height cap, a frontage too short for a truck driveway) does not
        # fail the layout's own check; decide() applies it to the site.
        o["ok"] = o["ok"] and not any(s == 1 and f not in LENGTH_INDEPENDENT_FLAGS for f, s, _ in flags)
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


INPUT_KEYS = ("frontage", "neighbours", "transmission", "layers", "dem")
_BAD_STATES = ("not_checked", "partial")


def input_state(inp: SiteInputs, key: str) -> InputState:
    """The state of one input, failing closed (R2): a missing entry in
    ``inp.states`` is not checked, and the frontage and lidar objects' own
    state wins when it is worse than the entry."""
    st = inp.states.get(key)
    if st is None:
        st = InputState("not_checked", "no state given for this input")
    own = {"frontage": (inp.frontage.state, inp.frontage.reason), "dem": (inp.dem.state, inp.dem.reason)}.get(key)
    if own is not None and own[0] in _BAD_STATES and st.state not in _BAD_STATES:
        st = InputState(own[0], own[1] or f"the {key} input says {own[0].replace('_', ' ')}")
    return st


def _input_flags(inp: SiteInputs) -> list[tuple[str, int, str]]:
    """Named flags for inputs that were not checked (R2), never a clean result."""
    out = []
    names = {"frontage": "FRONTAGE_NOT_CHECKED", "neighbours": "NEIGHBOURS_NOT_CHECKED",
             "transmission": "TRANSMISSION_NOT_CHECKED", "layers": "LAYERS_NOT_CHECKED"}
    for key, flag in names.items():
        st = input_state(inp, key)
        if st.state in _BAD_STATES:
            what = "not checked" if st.state == "not_checked" else "only partly checked"
            out.append((flag, 1, f"{key} {what} ({st.reason or 'no answer'}): the plan is incomplete"))
    dem = input_state(inp, "dem")
    if dem.state == "not_checked":
        out.append(("DEM_NOT_CHECKED", 1, f"lidar elevation not checked ({dem.reason or 'no answer'}): no slope, relief or pond siting from ground"))
    elif dem.state == "partial":
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
    jname, rules, defaulted = jur_rules(meta.get("jurisdiction", ""), tightened=opts.fixes)
    for kind in ("OTHER", "OTHER_REAR", "COMM"):
        if kind in rules["pave"]:
            rules["pave"][kind] = max(rules["pave"].get(kind, 0), OTHER_EDGE_MIN_PAVE_FT)
    prog = Program(jname, rules, parking_rule_for(jname, rules, opts), opts)
    hsg, hsg_note = hsg_letter(meta.get("hsg"), opts.fixes)
    if opts.fixes and inp.assembled:
        hsg, hsg_note = assemblage_hsg(meta, hsg, hsg_note)
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
        late += assemblage_flags(meta, jname)
    elif opts.fixes and len(parts(parcel)) > 1:
        # One county parcel in separate pieces (often split by a road): the
        # ponds and drop yards stay on the piece the building is on
        # (home_pieces in finish).
        late.append(("PARCEL_MULTIPART", 3, f"the parcel is {len(parts(parcel))} separate pieces (a road or a gap between "
                     "them); ponds and drop yards are kept on the building's piece"))
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
    # A deeper front yard in a named district, and a corridor overlay on
    # named state routes (the tightened rules; default behaviour only).
    zone = re.sub(r"[^A-Z0-9]", "", (meta.get("zoning") or "").upper())
    for prefix, feet in (rules.get("row_by_zoning") or {}).items():
        if zone.startswith(prefix):
            rules["bldg"]["ROW"] = max(rules["bldg"]["ROW"], feet)
            rules["tightened"].append(f"front yard {feet} ft applied: zoned {meta.get('zoning')}")
    corridor = rules.get("corridor")
    if corridor:
        fronted = [fr.road or ""] + [str(x) for x in fr.roads]
        on = [r for r in corridor["routes"] if any(re.search(rf"\b{re.escape(r)}\b", lab) for lab in fronted)]
        if on:
            rules["bldg"]["ROW"] = max(rules["bldg"]["ROW"], corridor["bldg"])
            rules["pave"]["ROW"] = max(rules["pave"]["ROW"], corridor["pave"])
            late.append(("CORRIDOR_OVERLAY", 2, f"the site fronts {on[0]}: the corridor overlay's {corridor['bldg']} ft "
                         f"building and {corridor['pave']} ft paving setbacks at the road are applied (draft reading)"))
        else:
            late.append(("CORRIDOR_OVERLAY", 2, f"a corridor overlay sets {corridor['bldg']} ft building and {corridor['pave']} ft "
                         f"paving setbacks at the road on {corridor['roads']}; this frontage is not read as one of the state "
                         "routes, and the county roads carry no name here: check the road"))
    if rules.get("use_check"):
        late.append(("USE_RESTRICTION_REPORTED", 2, f"{rules['use_check']}: confirm the use is allowed before relying on this plan"))
    edges = classify_edges(parcel, plane, neigh, front_ft, rules if opts.fixes else None)
    res_line = unary_union([ln for k, ln in edges if k.startswith("RES")]) if any(k.startswith("RES") for k, _ in edges) else None
    row_line = front_ft if front_ft is not None and not front_ft.is_empty else None
    if res_line is not None:
        whose = "residential or agricultural parcels (county tax classes)" if rules.get("ag_buffer") else "residential parcels (county land-use codes)"
        flags.append(("RESIDENTIAL_EDGE", 2, f"about {res_line.length:,.0f} ft of boundary touches {whose}; draft buffers applied"))
    P = clean(envelope(parcel, edges, rules["pave"]).difference(wet_buf).difference(cut))
    B = envelope(parcel, edges, rules["bldg"]).difference(wet_buf).difference(trans).difference(cut)
    if rules.get("open_yard_ft"):
        # The open yard, as one band round every lot line that is not a
        # road: round at the bends and the ends, so an inside corner of the
        # parcel and the end of a side line at the road are held too.
        off_road = [ln for k, ln in edges if k != "ROW" and not ln.is_empty]
        if off_road:
            B = B.difference(unary_union(off_road).buffer(rules["open_yard_ft"]))
    B = clean(B)
    dem = inp.dem
    steep = steep_cells(dem, float_keys=not opts.fixes).intersection(parcel) if dem.elev else Polygon()
    try:
        runs = frontage_runs(front_ft)
    except FrontageGeometryError as exc:
        runs = []
        late.append(("FRONTAGE_GEOMETRY", 1, f"the frontage line could not be merged ({exc}): access not checked"))
    # hypothetical: True only when the frontage was checked and has no usable
    # run (ACCESS_NONE); None when access is unknown (frontage not checked,
    # or its line could not be merged), never a landlocked signal from an outage.
    access_unknown = fr.state == "not_checked" or any(f == "FRONTAGE_GEOMETRY" for f, _, _ in late)
    hypothetical = False if runs else (None if access_unknown else True)
    if hypothetical:
        flags.append(("ACCESS_NONE", 1, "no public road frontage from the GDOT check: landlocked or access by easement only; the plan is hypothetical and its floor area is not counted"))
    # Entitlement gates of every member (an assemblage carries each member's).
    members = [m for m in meta.get("members") or [] if isinstance(m, dict)]
    for m in members or [meta]:
        zl = (m.get("zoning") or "").upper()
        mj = jur_rules(m.get("jurisdiction", ""))[0]
        who = f" (member #{m['rank']})" if len(members) > 1 and m.get("rank") else ""
        if mj.startswith("east point") and zl in ("I-1", "I1", "I-2", "I2"):
            flags.append(("USE_PERMIT_REQUIRED", 1, "East Point: warehousing is use-permit only in I-1/I-2 and needs 5,280 ft from residential (draft reading)" + who))
        if "chattahoochee" in (m.get("jurisdiction") or "").lower():
            flags.append(("USE_PERMIT_REQUIRED", 1, "Chattahoochee Hills: no by-right warehousing" + who))
    if jname.startswith("fairburn") and fr.road and "local" in fr.road:
        flags.append(("COLLECTOR_FRONTAGE", 2, "Fairburn M-districts require collector-street frontage; this frontage reads as a local road"))
    if opts.fixes and jname.startswith("fairburn") and any(k in ("OTHER", "OTHER_REAR") and not ln.is_empty for k, ln in edges):
        late.append(("DISTRICT_BUFFER_NOT_DRAWN", 2, "Fairburn M-districts: a 50 ft buffer to every other district (draft reading) is not drawn; "
                                                     "lot lines toward non-residential land carry only the draft 'other' setback, so the yield may be overstated"))
    if prog.prule == "market":
        flags.append(("ATL_PARKING_CODE", 2, "parking at the market ratio; Atlanta's 1/600 SF code minimum (unverified after the parking reforms) would need far more"))
    elif jname.startswith("atlanta"):
        flags.append(("ATL_PARKING_CODE", 2, "Atlanta's 1/600 SF code minimum used (draft; unverified after the parking reforms)"))
    small = [q for q in parts(P) if q.area <= MIN_PART_SF]
    ctx = Ctx(parcel, P, B, wet_buf, trans, cut, steep, dem, meta, rules, runs, jname, constraints, fr.road, hsg, prog, search)
    VARIANTS = variants()
    results, excluded = [], Polygon()
    # Layouts that passed every check but the surface pond, at the length
    # first tried (the search goes on to shorten them). Kept for the
    # read-out only: what underground or off-site detention would add.
    pond_limited: list[tuple[Plan, dict[str, Any]]] = []
    first: tuple | None = None          # (Pe, Be, fits, picked, explore) of the first attempt, for the rescue pass
    try:
        for attempt in range(3):
            Pe, Be = clean(P.difference(excluded)), clean(B.difference(excluded))
            fits = [q for q in (fit_variant(v, Pe, Be, prog, row_line, res_line, search) for v in VARIANTS) if q is not None]
            if not fits:
                break

            def prelim(q):
                st = q.module.intersection(steep).area / q.module.area if not steep.is_empty else 0.0
                return q.sf * (1 - opts.ranking["steep_share"] * st)
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
            def explore(q, Pe=Pe, Be=Be, shorten_routes=False):
                cur = q
                for _i in range(10):
                    o = finish(cur, ctx)
                    if o["ok"]:
                        results.append((cur, o))
                        break
                    if (not o["pond_ok"] and not o["route_problem"] and not o["violations"]
                            and not ({f for f, sv, _ in o["flags"] if sv == 1} - {"POND_DOES_NOT_FIT"} - LENGTH_INDEPENDENT_FLAGS)):
                        pond_limited.append((cur, o))      # a note for the read-out; the search goes on unchanged
                    if o["route_problem"] and opts.fixes:
                        # Turn the same fit round before giving it up or
                        # shortening it. The first turn keeps docks away
                        # from homes and the street; with homes behind, that
                        # puts the court on the street and the cars at the
                        # back, where no car route reaches them. Default
                        # behaviour only, in the first pass and the rescue.
                        try:
                            turned = next(((alt, o2) for alt in cur.flips for o2 in [finish(alt, ctx)] if o2["ok"]), None)
                        except _BudgetExceeded:
                            results.append((cur, o))        # out of time mid-turn: the finished layout is kept
                            raise
                        if turned is not None:
                            results.append(turned)
                            break
                    if o["route_problem"] and not shorten_routes:
                        results.append((cur, o))
                        break
                    if opts.fixes and not o["route_problem"] and not (
                            {f for f, sv, _ in o["flags"] if sv == 1} - LENGTH_INDEPENDENT_FLAGS):
                        # Blocked only by what a shorter building cannot fix
                        # (a height cap, a short frontage): keep this length
                        # (default only; o["ok"] already says so).
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
            if first is None:
                first = (Pe, Be, fits, picked, explore)
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
    rescue_cut = False
    if (opts.fixes and first is not None and results and not search.exceeded
            and not any(o["ok"] for _, o in results)
            and any(_blocking(o) and _blocking(o) <= RESCUE_FLAGS for _, o in results)):
        # Every explored plan failed on its pond or a route (R4 monotonicity):
        # a smaller parcel inside this one could still read feasible. Try the
        # explored variants shorter than a route problem stopped them, then
        # the fits the first attempt did not explore (shallower and shorter
        # variants), on the full envelopes. Default behaviour only.
        P0, B0, fits0, picked0, explore0 = first
        tried = list(results)
        try:
            for q, o in tried:
                if o["route_problem"]:
                    nxt = fit_variant(q.v, P0, B0, prog, row_line, res_line, search, length_cap=int(q.L - 40))
                    if nxt is not None:
                        explore0(nxt, P0, B0, shorten_routes=True)
            for q in [q for q in fits0 if all(q is not x for x in picked0)][:RESCUE_MAX_FITS]:
                explore0(q, P0, B0, shorten_routes=True)
        except _BudgetExceeded:
            rescue_cut = True
    budget_flag = ("PLAN_BUDGET_EXCEEDED", 1, f"the search stopped after {opts.max_seconds:.0f} s; the plan shown is the best found so far")
    if not results:
        return _no_fit(inp, jname, rules, flags + late, opts, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, small,
                       cut_short=search.exceeded), None

    def rank_sf(q, o, st, rel):
        rk = opts.ranking
        return (q.sf * (1 - rk["steep_share"] * st) - rk["relief_sf_per_ft"] * max(rel - rk["relief_free_ft"], 0)
                - rk["trailer_sf"] * max(0, q.trailers_target - o["trailers"]))

    def score(t):
        q, o = t
        st = o["steep_module_pct"] / 100
        rel = (o["relief"] or 0)
        if not opts.fixes:
            return (o["ok"], not o["route_problem"], rank_sf(q, o, st, rel))
        # A layout that meets a draft code minimum of truck spaces (where
        # the rules have one) before one that does not, as code car
        # parking is met by every layout; then floor area. The shortfall
        # against the market target comes last: it only settles a tie.
        # A pond the paving can drain to by gravity before one it cannot
        # (reviews/41): floor area drawn round a pond that stands above
        # the pad is not buildable as drawn.
        # A way in a WB-67 can drive before a larger layout without one
        # (reviews/43): judged only for the layouts at the top of the
        # ranking (`drivable` is set on those; unset counts as yes).
        return (o["ok"], not o["route_problem"], o["trailers"] >= q.code_trailers, not o["ok"] or o.get("drains", True),
                not o["ok"] or o.get("drivable", True),
                rank_sf(q, o, st, rel), -max(0, q.trailers_target - o["trailers"]))
    room = None
    added: list[tuple[Plan, dict[str, Any]]] = []
    turned = turned_from = None                                # the docks-downhill step (reviews/42)
    dock_side: dict[str, Any] = {"tried": 0, "turned": False, "saved_cy": 0, "outcome": None, "refused": []}
    truck_first_rec: dict[str, Any] = {"judged": 0, "tries": 0, "found": 0, "drawn": False, "outcome": None}
    lined_up: list[tuple[Plan, dict[str, Any]]] = []
    room_skipped = bool(opts.fixes and first is not None and not search.exceeded
                        and search.deadline - search.clock() < POND_ROOM_MIN_LEFT * opts.max_seconds)
    if opts.fixes and first is not None and not search.exceeded and not room_skipped:
        # Room for the pond (reviews/40): a pass that only adds layouts. The
        # layouts the pond alone blocked, largest first, are tried again
        # with other pond shapes, then moved to leave their pond room. One
        # that turns its docks to the street or its loading to homes is
        # taken only when the best plan does so anyway; nor one with no more
        # floor area that has fewer trailer stalls (a small relief or
        # steep-ground mark alone does not trade them away). The plan drawn
        # is then the best of all by the same ranking: never below the
        # plan without the pass.
        # Drainage first (reviews/41): the pass also starts from layouts
        # that passed with their pond above the pad. It sites ponds only on
        # ground the pad can drain to (ctx.pond_room), lowest spots first,
        # and adds no plan whose pond is uphill.
        room = PondRoom(search)
        best0 = max(results, key=score)
        b0_drains = best0[1].get("drains", True)

        def gains(t):
            q, o = t
            if not o.get("drains", True):
                return False
            if not b0_drains:
                return True                 # a pond that drains, where the best plan's does not
            return q.sf > best0[0].sf or (q.sf == best0[0].sf and o["trailers"] >= best0[1]["trailers"])
        ctx_room = dataclass_replace(ctx, pond_room=True, search=room.search)
        P0, B0 = first[0], first[1]

        def pond_only(o):
            return ({f for f, sv, _ in o["flags"] if sv == 1} - LENGTH_INDEPENDENT_FLAGS == {"POND_DOES_NOT_FIT"}
                    and not o["route_problem"])

        def uphill(o):
            return o["ok"] and not o.get("drains", True)
        start, seen_l = [], set()
        for q, o in sorted(pond_limited + [t for t in results if pond_only(t[1]) or uphill(t[1])], key=lambda t: -t[0].sf):
            key = (q.v["name"], q.L, q.r, tuple(round(c, 1) for c in q.module.bounds))
            if key in seen_l:
                continue
            # worth a try: it would rank above the best plan so far with its pond placed
            if ((pond_only(o) and score((q, {**o, "ok": True, "drains": True})) > score(best0))
                    or (uphill(o) and score((q, {**o, "drains": True})) > score(best0))):
                seen_l.add(key)
                start.append((q, o))

        def sweep(keep, best_now, first_round=True):
            """One round of the pass with the layouts `keep` takes: pond
            first, then the pond-blocked and uphill layouts. The best plan
            it found per starting point, of those that gain."""
            found_by: list[list[tuple[Plan, dict[str, Any]]]] = []
            try:
                # Pond first, building second: when the best plan's pond
                # does not drain, or a larger cross-section fitted the open
                # parcel than the best plan holds.
                v0 = best_now[1]["v_det"] / max(pond_count(best_now[1]["v_det"]), 1)
                target = best_now[0].sf if best_now[1].get("drains", True) else 0
                if best_now[1]["ok"] and any(q.sf > target for q in first[2]):
                    mine0: list[tuple[Plan, dict[str, Any]]] = []
                    found_by.append(mine0)
                    drainage_first(first[2], v0, best_now[0].frame.pl.angle_deg, target, P0, B0, ctx_room, row_line, res_line,
                                   room, keep, mine0)
                for q, o in start[:POND_ROOM_MAX_LAYOUTS]:
                    if room.spent:
                        break
                    room.layouts += first_round         # each starting layout counted once
                    mine: list[tuple[Plan, dict[str, Any]]] = []
                    found_by.append(mine)
                    if keep(q) and room.take():
                        o2 = finish(q, ctx_room)        # the same layout, its pond on low ground or in another shape
                        if o2["ok"] and o2.get("drains", True):
                            mine.append((q, o2))
                            room.found += 1
                            continue
                    relocate_for_pond(q, o, P0, B0, ctx_room, row_line, res_line, room, keep, mine)
            except _BudgetExceeded:
                room.cut_short = True                   # what was found stands; building 1's search was complete
            # Per starting point: its best plan, and its best plan that
            # meets its trailer target (the others are the same layout a
            # little moved).
            out = []
            for mine in found_by:
                ok = [t for t in mine if gains(t)]
                if ok:
                    top = max(ok, key=score)
                    out.append(top)
                    full = [t for t in ok if t[1]["trailers"] >= t[0].trailers_target]
                    if full and top[1]["trailers"] < top[0].trailers_target:
                        out.append(max(full, key=score))
            return out

        # First only layouts with their docks off the street and their
        # loading away from homes. Layouts that face either are tried
        # after, and only where the best plan before the pass faced it
        # already, unless the first round found a plan that faces neither
        # and is no smaller than that plan. So the pass never turns docks
        # to the street where the plan before did not, and never gives up
        # floor area to turn them away where it did.
        clear_of = lambda q: not faces_street(q, row_line) and not loading_faces_homes(q, res_line)
        added = sweep(clear_of, best0)
        best1 = max(results + added, key=score)
        was_street, was_homes = faces_street(best0[0], row_line), loading_faces_homes(best0[0], res_line)
        settled = clear_of(best1[0]) and best1[0].sf >= best0[0].sf
        if (was_street or was_homes) and not settled and not room.spent and not room.cut_short:
            added += sweep(lambda q: (not clear_of(q) and (was_street or not faces_street(q, row_line))
                                      and (was_homes or not loading_faces_homes(q, res_line))), best1, first_round=False)
        # No stalls traded between the pass's own plans (review 41/02,
        # finding 1): a plan gives way to another of the pass with at
        # least its floor area and more trailer stalls.
        # (only to one that itself ranks above the plan before the pass:
        # otherwise neither would be drawn and the gain lost)
        added = [t for t in added
                 if not any(u is not t and u[0].sf >= t[0].sf and u[1]["trailers"] > t[1]["trailers"] and score(u) > score(best0)
                            for u in added)]
        # At most three of them go on (the best by the ranking): more
        # would crowd the layouts compared (the first ten results) out of
        # the read-out.
        added = sorted(added, key=score, reverse=True)
        with_stalls = [t for t in added if t[1]["trailers"] >= t[0].trailers_target]
        kept = added[:POND_ROOM_KEEP]
        if with_stalls and not any(t is k for t in with_stalls for k in kept):
            kept = kept[:POND_ROOM_KEEP - 1] + [with_stalls[0]]     # the read-out's "meets its trailer target" layout
        added = kept
        results += added
        # Design for the truck (reviews/43). The ranking's top layout must
        # have a way in a WB-67 can drive. Each layout that reaches the top
        # has its truck path laid out (at most TRUCK_TIER_MAX); one without
        # a path gives way to the next. Where the top layout before this
        # step has none, driveway-first layouts are added first
        # (truck_first): never one that newly faces the street or homes.
        def with_truck(t: tuple[Plan, dict[str, Any]]) -> tuple[Plan, dict[str, Any]] | None:
            """The layout finished again with its truck road laid out
            first (the pond and yards then go round it); None when no
            WB-67 can drive it, or when the plan is then worse."""
            q, o = t
            o2 = finish(q, dataclass_replace(ctx, pond_room=o.get("pond_room_mode", False), truck_design=True))
            if o2.get("truck_ok") and not layout_no_worse(o, o2, truck=True):
                o2["drivable"] = True
                return q, o2
            # ... or, where the plan is then worse (its pond no longer
            # fits), the way in laid out round the plan as it stands
            d = truck_design_for(q, o, ctx)
            if d is not None and d["got"] is not None and d["got"]["ok"]:
                apply_truck_path(q, o, ctx)
                o["drivable"] = True
                return q, o
            return None

        def settle() -> tuple[Plan, dict[str, Any]]:
            while True:
                top = max(results, key=score)
                if not top[1]["ok"] or "drivable" in top[1] or truck_first_rec["judged"] >= TRUCK_TIER_MAX:
                    return top
                truck_first_rec["judged"] += 1
                got = with_truck(top)
                if got is None:
                    top[1]["drivable"] = False
                else:
                    results[next(k for k, t in enumerate(results) if t is top)] = got
                    for lst in (added,):        # a plan of the pass stays one of the pass's
                        for k, t in enumerate(lst):
                            if t is top:
                                lst[k] = got
        try:
            top0 = max(results, key=score)
            if not ctx.runs:
                raise LookupError           # no frontage: no driveway, no truck route to lay out
            top = settle()
            if top0[1]["ok"] and top0[1].get("drivable") is False:
                left = [TRUCK_FIRST_MAX_TRIES]
                t_street, t_homes = faces_street(top0[0], row_line), loading_faces_homes(top0[0], res_line)
                truck_first(first[2], 0, P0, B0, dataclass_replace(ctx, pond_room=True), row_line, res_line, left,
                            lambda q: (t_street or not faces_street(q, row_line)) and (t_homes or not loading_faces_homes(q, res_line)),
                            lined_up)
                truck_first_rec.update(tries=TRUCK_FIRST_MAX_TRIES - left[0], found=len(lined_up))
                results += lined_up
                top = settle()
            if top[1]["ok"] and top[1].get("drivable") is not True:
                # none of the layouts judged has a way in: the ranking stands as it was, and the plan drawn says so
                for _, o_ in results:
                    if o_.get("drivable") is False:
                        del o_["drivable"]
                truck_first_rec["outcome"] = "none_found"
            elif top0[1].get("drivable") is False:
                truck_first_rec["outcome"] = "another_layout"
            else:
                truck_first_rec["outcome"] = "drivable" if top[1].get("drivable") else "not_judged"
        except LookupError:
            truck_first_rec["outcome"] = "no_frontage"
        except _BudgetExceeded:
            search.exceeded = False     # only this step ran out of time: building 1's search was complete
            for _, o_ in results:
                if o_.get("drivable") is False:
                    del o_["drivable"]
            truck_first_rec["outcome"] = "no_time"
        # Docks downhill (reviews/42). The best plan's footprint is tried
        # with its court on the other side (the office at either end) when
        # that stepped pad moves materially less earth. It takes the plan's
        # place only when dock_turn_blocked and dock_turn_refused find
        # nothing against it, and (below) when it costs no second building.
        # Its own tries, on what is left of the pass's time.
        base = max(results, key=score)
        q0, o0 = base
        sg0 = o0.get("stepped")
        if not o0["ok"]:
            dock_side["outcome"] = "no_plan"
        elif sg0 is None:
            dock_side["outcome"] = "no_lidar"
        elif len(q0.courts) != 1:
            dock_side["outcome"] = "courts_on_both_sides"
        else:
            cands, seen_q = [], set()
            dock_side["outcome"] = "not_cheaper"
            try:
                for fu in (q0.frame.flipu, not q0.frame.flipu):
                    q = court_on_other_side(q0, prog, fu)
                    shape = tuple(round(c, 1) for g in (q.courts + q.cars + q.offices) for c in g.bounds)
                    sg = stepped_grading(q, dem)
                    if shape in seen_q or not dock_turn_saves(sg0, sg):
                        continue                        # the same layout again, or no material saving: nothing to try
                    seen_q.add(shape)
                    office = "same end" if fu == q0.frame.flipu else "other end"
                    would = rnd(sg0["moved_cy"] - sg["moved_cy"], 1000)
                    why = dock_turn_blocked(q0, q, B0, row_line, res_line)
                    if why is None and (room.cut_short or dock_side["tried"] >= DOCK_TURN_TRIES):
                        why = "no time left to lay it out"
                    if why is None:
                        dock_side["tried"] += 1
                        o2 = finish(q, dataclass_replace(ctx_room, truck_design=bool(o0.get("drivable"))))
                        why = dock_turn_refused(o0, o2)
                        if why is None and o0.get("drivable") and not o2.get("truck_ok"):
                            why = "a truck could not drive its way in"
                        if why is None:
                            if o0.get("drivable"):
                                o2["drivable"] = True
                            # (the office's end does not change the stepped earthwork: the first that passes is taken,
                            # the office where it was before the other end)
                            cands.append((q, o2))
                            break
                    dock_side["refused"].append({"office": office, "why": why, "would_save_cy": would})
            except _BudgetExceeded:
                dock_side["refused"].append({"office": office, "why": "no time left to lay it out", "would_save_cy": would})
            if cands:
                turned, turned_from = cands[0], base
                results.append(turned)
                dock_side.update(outcome="turned", turned=True,
                                 saved_cy=rnd(sg0["moved_cy"] - turned[1]["stepped"]["moved_cy"], 1000))
            elif dock_side["refused"]:
                dock_side["outcome"] = "refused"
    elif opts.fixes:
        dock_side["outcome"] = "no_time" if room_skipped or search.exceeded else "no_plan"
    results.sort(key=score, reverse=True)
    if turned is not None:
        # the same footprint and floor area: it takes the place of the plan it was turned from
        results.remove(turned)
        results.insert(0, turned)
    def ready(q: Plan, o: dict[str, Any], other: tuple | None = None) -> None:
        """Design for the truck (reviews/43): the layout's truck route as
        a path a WB-67 can steer, with its swept path checked, put on the
        plan once. Like the read-out, it must never cost a plan."""
        if not opts.fixes or not o["ok"] or "truck_path" in o:
            return
        try:
            apply_truck_path(q, o, ctx, other)
        except Exception as exc:        # noqa: BLE001
            o["truck_path"] = {"designed": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}

    def second_for(best_p: Plan, best_o: dict[str, Any]) -> tuple[tuple | None, bool]:
        """A second building beside the plan (best_p, best_o), and whether
        the budget ran out while looking (the optional search only)."""
        if not best_o["ok"] or search.exceeded:
            return None, False
        ready(best_p, best_o)       # the second building goes round building 1's truck road as a truck drives it
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
                        return (q, o2), False
        except _BudgetExceeded:
            # Building 1 was fully searched before this point (the block
            # runs only when its search finished in time), so its result
            # stands: the cut search is a note, not a blocking flag.
            return None, True
        return None, False

    best_p, best_o = results[0]
    # Sight-side entry (reviews/43). Where the best plan's trucks enter its
    # court with the dock doors on their right, the same layout is finished
    # once more with its driveway and truck route sent to the other end of
    # the court. It takes the plan's place only when nothing else is worse
    # (truck_side_refused) and, below, when it costs no second building.
    truck_side: dict[str, Any] = {"outcome": None, "why": None}
    changes: list[tuple[str, tuple]] = [("turn", turned_from)] if turned is not None else []
    if opts.fixes:
        if not best_o["ok"] or best_o.get("truck_entry") is None:
            truck_side["outcome"] = "no_plan"
        elif len(best_p.courts) != 1:
            truck_side["outcome"] = "courts_on_both_sides"
        elif sight_entry(best_p, best_o["truck_entry"]):
            truck_side["outcome"] = "sight"
        elif search.exceeded:
            truck_side.update(outcome="blind", why="no time left to lay it out")
        else:
            try:
                o_s = finish(best_p, dataclass_replace(ctx, sight_first=True, pond_room=best_o.get("pond_room_mode", False), truck_design=True))
                why = truck_side_refused(best_p, best_o, o_s)
                if why is None and best_o.get("drivable") and not o_s.get("truck_ok"):
                    why = "a truck could not drive its way in"
                if why is None and o_s.get("truck_ok"):
                    o_s["drivable"] = True
            except _BudgetExceeded:
                search.exceeded = False     # only this optional step ran out of time: the plan itself was searched in full
                o_s, why = None, "no time left to lay it out"
            if why is None:
                changes.append(("sight", (best_p, best_o)))
                results[0] = (best_p, o_s)
                best_o = o_s
                truck_side["outcome"] = "turned_to_sight"
            else:
                truck_side.update(outcome="blind", why=why)
    second, second_cut = second_for(best_p, best_o)
    # A change to the best plan must cost no floor area in a second
    # building either (review 42/02, finding 3): the plan it was changed
    # from is searched too, most recent change first, and stands if its
    # second building is the larger (or, where the changed plan has none,
    # if time ran out before that was known).
    sec_sf = lambda t: t[0].sf if t else 0
    while changes and not second_cut:
        kind, before = changes.pop()
        sec0, cut0 = second_for(*before)
        if sec_sf(sec0) > sec_sf(second) or (cut0 and second is None):
            if kind == "sight":
                results[0] = before
                truck_side.update(outcome="blind", why="it would cost the second building")
            else:
                results.remove(before)
                results.insert(0, before)
                dock_side.update(outcome="refused", turned=False, saved_cy=0)
                dock_side["refused"].append({"office": "either end", "why": "it would cost the second building",
                                             "would_save_cy": rnd(before[1]["stepped"]["moved_cy"] - turned[1]["stepped"]["moved_cy"], 1000)})
                turned = None
                if truck_side["outcome"] == "turned_to_sight":
                    truck_side.update(outcome="not_tried", why="it was laid out for the turned plan, which was not kept")
            best_p, best_o, second, second_cut = *before, sec0, cut0
        elif cut0:
            search.exceeded = False     # only this comparison ran out of time: the plan drawn and its second building were searched in full
            (truck_side if kind == "sight" else dock_side)["second_building_compared"] = False
            break
    if turned is None and dock_side["refused"]:
        # the drawn plan's own note says why its court stays on the high side
        why = "; ".join(dict.fromkeys(r["why"] for r in dock_side["refused"]))
        best_o["flags"] = [(f, sv, note.replace(", a layout not checked here)", f": not taken, {why})") if f == "DOCKS_UPHILL" else note)
                           for f, sv, note in best_o["flags"]]
    if rescue_cut:
        late.append(("SMALLER_LAYOUTS_NOT_ALL_TRIED", 2,
                     f"the search stopped after {opts.max_seconds:.0f} s while trying shorter and shallower layouts after the "
                     "pond or a route blocked every plan it explored first; the plan shown is the best found by then "
                     "(re-run with a larger --max-seconds-per-site)"))
    elif second_cut:
        late.append(("SECOND_BUILDING_NOT_SEARCHED", 2,
                     f"the search stopped after {opts.max_seconds:.0f} s while looking for a second building; "
                     "building 1 is complete, a second building may still fit (re-run with a larger --max-seconds-per-site)"))
    elif search.exceeded:
        late.append(budget_flag)
    ready(best_p, best_o)
    if second is not None:
        ready(second[0], second[1], (best_p, best_o))
    rec = report(inp, best_p, best_o, second, parcel, P, B, rules, jname, flags, late, fr, hypothetical, res_line, row_line, opts)
    gross_ac = parcel.area / SQFT_PER_AC
    rec["alternatives"] = [_alternative(q, o, gross_ac) for q, o in results[:10]]
    if turned is not None:
        rec["alternatives"][0]["docks_turned"] = True       # the next row with its footprint is the plan it was turned from
    rec["inputs"] = _inputs_block(inp, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, small, search, ctx)
    if opts.fixes:
        rec["inputs"]["dock_side"] = dock_side
        rec["inputs"]["truck_side"] = truck_side
        truck_first_rec["drawn"] = any(best_p is q for q, _ in lined_up)
        rec["inputs"]["truck_first"] = truck_first_rec
        src = turned_from[0] if turned is not None else best_p      # a turned plan came from the pass when its plan did
        if room is not None:
            rec["inputs"]["fit_searches"] = rec["inputs"].get("fit_searches", 0) + room.search.fit_calls
        rec["inputs"]["pond_room"] = ({"layouts": room.layouts, "tries": room.tries, "max_tries": room.max_tries, "found": room.found,
                                       "drawn": any(src is q for q, _ in added), "cut_short": room.cut_short, "skipped_for_time": False}
                                      if room is not None else {"layouts": 0, "tries": 0, "max_tries": POND_ROOM_MAX_TRIES, "found": 0,
                                                                "drawn": False, "cut_short": False, "skipped_for_time": room_skipped})
    try:
        _read_out(rec, [(best_p, best_o)] + ([second] if second else []), parcel, P, edges, rules, res_line, fr,
                  [_alternative(q, o, gross_ac) for q, o in pond_limited], opts)
    except Exception as exc:        # noqa: BLE001 - the read-out is advisory: it must never cost a plan
        # The plan above is complete without it. Say what is missing, and
        # keep the part of the read-out that needs only the record.
        rec["read_out"] = {"complete": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
        rec["assessment"] = assess.assessment(rec, COMP_BAND_SF_PER_AC)
    # The search keeps undrawn plans (build(draw=False)); only the plans
    # shown are rebuilt with their stall ticks and door marks.
    shown = build(best_p.v, best_p.L, best_p.r, best_p.frame, prog)
    if second is not None:
        q = second[0]
        second = (build(q.v, q.L, q.r, q.frame, prog), second[1])
    context = [("RES" if residential_neighbour(a, rules if opts.fixes else None) else "OTHER", plane.ft(s)) for a, s in neigh]
    drawing = Drawing(inp.title, parcel, P, constraints, trans, cut, steep, front_ft, shown, best_o, second, B=B, edges=edges,
                      dem=dem, context=context)
    return rec, drawing


def _stepped_record(sg: dict[str, Any] | None) -> dict[str, Any] | None:
    """The stepped pad of building 1 for the record (stepped_grading), or
    None. `court_side`: where the one court lies on the ground (a rise or
    fall of 1 ft or more from the building: high or low); `both` for a
    building with courts on two sides, each court's fall listed."""
    if sg is None:
        return None
    fall, both = sg["fall_to_court"], sg.get("courts", 1) > 1
    return {"floor_ft": round(sg["floor"]), "court_ft": round(sg["court"]), "dock_drop_ft": DOCK_DROP_FT,
            "cut_cy": rnd(sg["cut_cy"], 1000), "fill_cy": rnd(sg["fill_cy"], 1000), "moved_cy": rnd(sg["moved_cy"], 1000),
            "ground_fall_to_court_ft": None if both else round(fall, 1),
            "ground_fall_to_courts_ft": [round(x, 1) for x in sg.get("falls", [fall])],
            "court_side": "both" if both else "high" if fall <= -1.0 else "low" if fall >= 1.0 else "level",
            "basis": f"floor at one level, truck courts and trailer rows {DOCK_DROP_FT:.0f} ft below it, floor set where cut equals "
                     f"fill; lidar samples at {sg.get('step', 40.0):.0f} ft over the layout's own ground (module); the rest of that "
                     "ground at the floor; no paving slopes, walls, swell or shrink; no pond excavation, drop yards or driveways"}


def _drainage_record(o: dict[str, Any], second: tuple | None) -> dict[str, Any]:
    """The ponds' ground against their pads, building 1's then a second
    building's (each against its own pad): every pond's figure, the
    highest, and whether all drain. None where nothing was measured (no
    pond, no lidar under it): not a pond that drains."""
    each = list(o.get("pond_over_each") or []) + (list(second[1].get("pond_over_each") or []) if second else [])
    measured = [x for x in each if x is not None]
    placed = o.get("pond_ok", True) and (second[1].get("pond_ok", True) if second else True)
    if measured and max(measured) > POND_UPHILL_TOL_FT:
        drains: bool | None = False                 # one pond above its pad is enough
    else:
        drains = True if (measured and len(measured) == len(each) and placed) else None
    return {"pond_over_pad_ft": round(max(measured)) if measured else None,
            "ponds_over_pad_ft": [round(x) if x is not None else None for x in each],
            "drains_by_gravity": drains}


def _pond_shape_text(o: dict[str, Any], second: tuple | None) -> str:
    """'L=2W', or the proportions used where a 2:1 pond did not fit."""
    ratios = list(o.get("pond_ratios") or []) + (list(second[1].get("pond_ratios") or []) if second else [])
    other = sorted({k for k in ratios if k != 2.0})
    if not other:
        return "L=2W"
    return "L=2W where it fits, else " + ", ".join(f"L={k:g}W" for k in other) + " (a 2:1 pond did not fit)"


def _alternative(q: Plan, o: dict[str, Any], gross_ac: float) -> dict[str, Any]:
    """One finished layout of the search, with what it holds and what
    blocks it (the plan drawn is the first of the list)."""
    return {"variant": q.v["name"], "operator": q.v["operator"], "sf": rnd(q.sf, 1000), "L": q.L, "D": q.v["D"],
            "geometry_ok": o["ok"], "route_problem": o["route_problem"],
            "doors": q.doors, "trailers": o["trailers"], "trailers_target": q.trailers_target,
            "code_trailers": q.code_trailers,
            "sf_per_gross_ac": rnd(q.sf / gross_ac, 100) if gross_ac else None,
            "detention_acft": round(o["v_det"], 1), "pond_fits": o["pond_ok"],
            "relief_ft": round(o["relief"]) if o["relief"] else None, "steep_pct": round(o["steep_module_pct"]),
            "cut_fill_cy": rnd(o["cutfill_cy"], 1000) if o["cutfill_cy"] else None,
            "blocking": sorted({f for f, sv, _ in o["flags"] if sv == 1}),
            **({"drains": o["drains"]} if "drains" in o else {}),
            **({"stepped_cy": rnd(o["stepped"]["moved_cy"], 1000)} if o.get("stepped") else {}),
            **({"drivable": o["drivable"]} if "drivable" in o else {})}


def _read_out(rec: dict[str, Any], layouts: list[tuple[Plan, dict[str, Any]]], parcel, P, edges, rules: dict[str, Any],
              res_line, fr: FrontageInput, pond_limited: list[dict[str, Any]], opts: PlanOptions) -> None:
    """Add the read-out of siteplan_assess to a drawn plan's record: where
    the land goes, the plan against its draft rules, a takeoff, why this
    layout, and the assessment. Derived from the finished plan only; it
    changes no flag, feasibility or floor area."""
    p, o = layouts[0]
    shapes = assess.use_shapes(layouts, parcel)
    land = assess.land_budget(parcel, P, envelope(parcel, edges, rules["pave"]).area, shapes)
    total = sum(q.sf for q, _ in layouts)
    doors = sum(q.doors for q, _ in layouts)
    trailers = sum(oo["trailers"] for _, oo in layouts)
    net_ac = P.area / SQFT_PER_AC
    bldg_area = sum(q.building.area for q, _ in layouts)
    rec["site_data"] = {
        "land": land, "buildings": len(layouts),
        "building_coverage_pct": round(100 * bldg_area / parcel.area, 1),
        "sf_per_net_ac": rnd(total / net_ac, 100) if net_ac else None,
        "doors_per_10k_sf": round(doors * 10000 / total, 2) if total else None,
        "trailers_per_door": round(trailers / doors, 2) if doors else None,
        "court_depth_ft": COURT, "trailer_row_ft": TRAILER_ROW, "truck_road_ft": TRUCK_ROAD,
    }
    cover = None
    if rules.get("cover"):
        kind, capv = rules["cover"]
        cover = (kind, capv, coverage_value(kind, bldg_area, sum(c.area for q, _ in layouts for c in q.cars),
                                            sum(oo["imp_ac"] for _, oo in layouts), parcel.area))
    rec["compliance"] = {
        "status": "draft", "source": rules.get("source"),
        "rows": assess.compliance(
            rules, edges, layouts, clear_ft=rec["building"]["clear_height_ft"], clear_assumed_ft=p.clear,
            roof_allowance_ft=ROOF_ALLOWANCE_FT, cover=cover, building_pct=100 * bldg_area / parcel.area,
            impervious_pct=rec["coverage"]["impervious_pct"], cars_required=rec["cars"]["required"],
            cars_provided=rec["cars"]["provided"], cars_basis=rec["cars"]["basis"], gated=rec["gated"],
            truck_spaces=((math.ceil(sum(q.sf for q, _ in layouts) / rules["truck_space_sf"]),
                           sum(oo["trailers"] for _, oo in layouts)) if rules.get("truck_space_sf") else None))}
    rec["quantities"] = assess.quantities(shapes, layouts, building_sf=rec["total_sf_if_access"], frontage_ft=fr.feet,
                                          residential_edge_ft=res_line.length if res_line is not None else 0.0, net_ac=net_ac)
    rec["selection"] = assess.selection(rec["alternatives"], opts.ranking, pond_limited,
                                        underground=opts.underground if opts.fixes else None)
    rec["assessment"] = assess.assessment(rec, COMP_BAND_SF_PER_AC)


def _inputs_block(inp, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, small, search=None, ctx=None):
    out = summary_inputs(parcel, P, B, constraints, trans, cut, steep, fr, edges)
    out["states"] = {k: input_state(inp, k).as_dict() for k in sorted(set(inp.states) | set(INPUT_KEYS))}
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
                       "values": {k: rules[k] for k in ("bldg", "pave", "cover", "height", "parking", "stall_w", "island")},
                       # what the default behaviour draws beyond the draft table, and why (still draft)
                       **({"tightened": list(rules["tightened"])} if rules.get("tightened") else {})},
            "operator_decisions": opts.decisions()}


def _no_fit(inp, jname, rules, flags, opts, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, small=(),
            cut_short: bool = False):
    """A record with no plan. cut_short: the time budget stopped the search
    before any plan was finished, which says nothing about fit, so the
    record carries PLAN_BUDGET_EXCEEDED instead of NO_FIT (not feasible,
    and not a no-fit either: re-run with a larger --max-seconds-per-site)."""
    flags = list(flags) + [("ZONING_DRAFT", 2, f"setbacks and buffers are draft screening values for {jname}; {ZONING_AUTHORITY}")]
    if cut_short:
        first = {"flag": "PLAN_BUDGET_EXCEEDED",
                 "note": f"the search stopped after {opts.max_seconds:.0f} s before any plan was finished; "
                         "not a no-fit: re-run with a larger --max-seconds-per-site"}
    else:
        first = {"flag": "NO_FIT", "note": "no building variant fits the envelope with its truck court, parking and pond"}
    sev = {1: [first], 2: [], 3: []}
    for f, s, n in flags:
        sev[s].append({"flag": f, "note": n})
    rec = _base_record(inp, jname, rules, opts)
    # Missing inputs only ever enlarge the envelope, so NO_FIT stands.
    rec.update({"fits": False, "feasible": False, "hypothetical": None, "jurisdiction_rules": jname, "total_sf": 0,
                "gated": [], "flags": sev,
                "inputs": _inputs_block(inp, parcel, P, B, constraints, trans, cut, steep, fr, edges, seam_sf, list(small))})
    if not parcel.is_empty:
        # Where the land goes even with no plan: it often says why nothing fits.
        rec["site_data"] = {"land": assess.land_budget(parcel, P, envelope(parcel, edges, rules["pave"]).area,
                                                       assess.use_shapes([], parcel)), "buildings": 0}
    rec["assessment"] = assess.assessment(rec, COMP_BAND_SF_PER_AC)
    return rec


def _hsg_basis(meta: dict[str, Any], fixes: bool, assembled: bool = False) -> str:
    """The soil group named in the detention basis: the run's value in the
    prototype behaviour, the letter actually used in the default."""
    hsg = meta.get("hsg")
    if not fixes:
        return hsg or "B"
    letter, note = hsg_letter(hsg, True)
    if assembled:
        letter, note = assemblage_hsg(meta, letter, note)
    return letter + (" (see HSG_ASSUMED)" if note else "")


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
        floor = (f" (the draft code minimum here is one truck space per {rules['truck_space_sf']:,} SF)"
                 if rules.get("truck_space_sf") else "")
        flags.append(("TRAILER_SHORT", 2, f"{o['trailers']} trailer stalls against a target of {p.trailers_target}{floor}"))
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
    if sfa < COMP_BAND_SF_PER_AC[0]:
        flags.append(("BELOW_COMP_BAND", 3, f"{sfa:,.0f} SF per gross acre; Georgia comps run about {COMP_BAND_SF_PER_AC[0]:,}-{COMP_BAND_SF_PER_AC[1]:,}"))
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
    car_cap = p.car_cap - (o.get("car_stalls_taken", 0) if fixes else 0)       # less the stalls the trucks' lead-in takes (reviews/43)
    cars_prov = max(min(car_cap, math.ceil(p.cars_required / 5) * 5), p.cars_required) if car_cap >= p.cars_required else car_cap
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
        "cars": {"provided": cars_prov, "row_capacity": car_cap, "required": p.cars_required, "basis": p.cars_rule, "ada": ada_count(cars_prov),
                 "per_1000_sf": round(cars_prov / p.sf * 1000, 2), "stall": f"{rules['stall_w']} x 18 ft"},
        "coverage": {"gross_ac": round(gross, 1), "sf_per_gross_ac": rnd(sfa, 100), "gross_far": round(total / (gross * SQFT_PER_AC), 2),
                     "impervious_ac": round(imp_total, 1), "impervious_pct": round(imp_total / gross * 100), "paving_envelope_ac": round(P.area / SQFT_PER_AC, 1)},
        "detention": {"storage_acft": round(v_total, 1), "ponds": len(o["ponds"]) + (len(second[1]["ponds"]) if second else 0),
                      "fits": o["pond_ok"] if fixes else o["ok"],
                      **(_drainage_record(o, second) if fixes else {}),
                      "basis": f"1.15 x (k_imp x impervious + k_perv x disturbed pervious), HSG {_hsg_basis(meta, fixes, inp.assembled)}; 6 ft deep, 4:1 slopes, {_pond_shape_text(o, second)}, 1 ft freeboard, 15 ft rim; screening only (+/-30%)"},
        "access": {"truck_driveway": o["tpt"] is not None, "car_driveway": o["cpt"] is not None, "frontage_ft": fr.feet, "road": fr.road,
                   "frontage_state": fr.state,
                   "truck_route_ft": round(sum(ln.length for k, ln in o["routes"] if k == "truck")), "car_route_ft": round(sum(ln.length for k, ln in o["routes"] if k == "car")),
                   **({"truck_path": o["truck_path"]} if fixes and "truck_path" in o else {})},
        "grading": {"relief_ft": round(o["relief"]) if o["relief"] else None, "cut_fill_cy": rnd(o["cutfill_cy"], 1000) if o["cutfill_cy"] else None,
                    "steep_share_pct": round(o["steep_module_pct"]), "dem_points": o["dem_points_module"],
                    "pad_elev_ft": round(o["pad_elev"]) if o.get("pad_elev") is not None else None,
                    "max_cut_ft": round(o["max_cut"]) if o.get("max_cut") is not None else None,
                    "max_fill_ft": round(o["max_fill"]) if o.get("max_fill") is not None else None,
                    **({"stepped_pad": _stepped_record(o.get("stepped"))} if fixes else {}),
                    "basis": "one balanced plane over the building, courts and parking (lidar sampled at 40 ft); no terraces"},
        "flags": sevd,
        "plan_checks": o["violations"] + ([dict(v, building=2) for v in second[1]["violations"]] if second else []),
    })
    if second:
        q, o2 = second
        rec["second_building"] = {"configuration": q.v["name"], "L_ft": q.L, "D_ft": q.v["D"], "sf": rnd(q.sf, 1000), "doors": q.doors,
                                  "trailers": o2["trailers"], "trailers_target": q.trailers_target, "cars_capacity": q.car_cap, "cars_required": q.cars_required,
                                  "own_driveways": not o2["shared_access"], "route_ft": round(sum(ln.length for k, ln in o2["routes"] if k == "truck")),
                                  **({"pad_elev_ft": round(o2["pad_elev"])} if fixes and o2.get("pad_elev") is not None else {}),
                                  **({"truck_path": o2["truck_path"]} if fixes and "truck_path" in o2 else {})}
        rec["totals_both_buildings"] = {"doors": p.doors + q.doors, "trailers": o["trailers"] + o2["trailers"],
                                        "trailers_target": p.trailers_target + q.trailers_target, "cars_capacity": p.car_cap + q.car_cap}
    return rec


# ---------------------------------------------------------------------------
# Owner guard (SR-8): the one shared guard (viewer.OwnerGuard, also used by
# screener.builtcheck and the viewer) on the final bytes of every artifact
# ---------------------------------------------------------------------------
def owner_terms(rows: Iterable[dict[str, Any]]) -> OwnerGuard:
    """The shared owner guard for these rows (owner and mailing strings,
    the core name without company suffixes, the mailing street line, an
    individual's name in both orders; site addresses and place names of
    the rows are skipped). The CLI builds it with ``OwnerGuard.from_run``,
    which also reads rejected.csv (an assemblage member may sit there)."""
    return OwnerGuard.from_rows(list(rows))


def owner_hits(payload: str | bytes, guard: OwnerGuard, public: Iterable[str] = ()) -> dict[str, int]:
    """{field: number of owner strings found}, never the strings. The text
    is read as written (entities and JSON escapes decoded by the guard) and
    once more with its markup removed, so a name split across SVG elements
    or wrapped over two text lines is found too. `public`: the site
    addresses this artifact writes (its sites' own addresses)."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    public = list(public)
    counts = guard.hits(text, public)
    for k, n in guard.hits(markup_text(text), public).items():
        counts[k] = max(counts.get(k, 0), n)
    return dict(counts)


# ---------------------------------------------------------------------------
# Files: slugs, containment, atomic writes
# ---------------------------------------------------------------------------
SLUG = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NAME = re.compile(r"^[A-Za-z0-9_+-]{1,64}$")
PID = re.compile(r"^[A-Z0-9][A-Z0-9 ]{0,30}[A-Z0-9]$")
SVG_NAME = re.compile(r"^plan-[A-Za-z0-9_-]+\.svg$")
PLANS_FILE = "plans.json"
PIPELINE_FILE = "plans.csv"      # one row per site: status, yield, first step


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


def write_atomic(path: Path, data: bytes, base: Path, terms: OwnerGuard | None, public: Iterable[str] = ()) -> None:
    """Owner guard on the final bytes, then a temp file and os.replace."""
    target = contained(path, base)
    if terms is not None:
        hits = owner_hits(data, terms, public)
        if hits:
            raise OwnerDataFound(f"{target.name}: owner data found ({', '.join(f'{k}: {v}' for k, v in sorted(hits.items()))}); nothing written")
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


PLANNER_FILES = ("siteplan.py", "siteplan_inputs.py", "siteplan_render.py")


def _code_files(here: Path | None = None) -> tuple[str, ...]:
    """Every screener file a plan depends on: the planner files and each
    screener module they import, directly or through another screener
    module (frontage, checks, layers, arcgis, sitefit, geo, plan, viewer,
    ...). Read from the files' relative imports, so a new import is
    covered without editing a list."""
    import ast
    here = here or Path(__file__).resolve().parent
    seen: list[str] = []
    todo = list(PLANNER_FILES)
    while todo:
        name = todo.pop(0)
        if name in seen or not (here / name).exists():
            continue
        seen.append(name)
        tree = ast.parse((here / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                mods = [node.module.split(".")[0]] if node.module else [a.name for a in node.names]
                todo += [f"{m}.py" for m in mods]
    return tuple(sorted(seen))


# --merge mixes plans only when all of these are unchanged.
CODE_FILES = _code_files()


def code_hash() -> str:
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in CODE_FILES:
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


RANK_KEY = re.compile(r"\d{1,6}(\+\d{1,6})*")


def check_assemble_names(assembles: list[tuple[str, list[str]]], rows: dict[str, dict[str, Any]]) -> None:
    """An --assemble NAME that reads as ranks ("2", "115+170") must list
    exactly those ranks' parcels: the viewer and --merge read such a name
    as ranks of the run."""
    for name, pids in assembles:
        if RANK_KEY.fullmatch(name):
            want = [(rows.get(rk) or {}).get("parcel_id") for rk in name.split("+")]
            if None in want or sorted(want) != sorted(pids):
                raise PlanError(f"--assemble {name}: a name made of ranks must list exactly those ranks' parcels; "
                                "use a name with a letter")


def is_incomplete(rec: dict[str, Any]) -> bool:
    """A written plan with an input that was not checked (R2)."""
    flags = rec.get("flags") or {}
    sev1 = flags.get(1, flags.get("1", [])) if isinstance(flags, dict) else []
    return any(isinstance(f, dict) and f.get("flag") in NOT_CHECKED_FLAGS for f in sev1 or [])


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
        if key in out:
            # One cut per site: a second would silently replace the first.
            raise PlanError(f"--cut-parcel {key}: given twice (one test cut per site)")
        out[key] = (check_pid(pid), feet)
    return out


def site_definition(name: str, assembles: list[tuple[str, list[str]]],
                    cuts: dict[str, tuple[str, float]]) -> dict[str, Any]:
    """What a call asks for one site beyond its name: the --assemble
    members (sorted; None for a rank, whose parcel is fixed by the run) and
    the --cut-parcel test cut. --merge keeps a plan only for the same
    definition."""
    members = dict(assembles).get(name)
    cut = cuts.get(name)
    return {"members": sorted(members) if members is not None else None,
            "cut": [cut[0], float(cut[1])] if cut else None}


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
    ap.add_argument("--max-seconds-per-site", type=float, default=MAX_SECONDS_PER_SITE,
                    help=f"search budget per site (default {MAX_SECONDS_PER_SITE:.0f} s); past it the plan is flagged PLAN_BUDGET_EXCEEDED")
    ap.add_argument("--parking", choices=("max", "code", "market"), default=PARKING_RULE)
    ap.add_argument("--atlanta-parking", choices=("market", "code"), default=ATLANTA_PARKING)
    ap.add_argument("--trailer-ratio", default=None, help="trailer stalls per door: one number, or three (below 250k, below 450k, above)")
    ap.add_argument("--underground-detention", action="store_true",
                    help="a market where land is dear enough for underground detention: the read-out prices it; the layout still draws surface ponds")
    ap.add_argument("--prototype-behaviour", action="store_true", help="reproduce the v4 prototype exactly (parity)")
    return ap


def main(argv: list[str] | None = None) -> int:
    from . import siteplan_inputs as si       # the network layer, only for the CLI
    from . import siteplan_render as sr

    args = build_parser().parse_args(argv)
    try:
        if not (math.isfinite(args.max_seconds_per_site) and 0 < args.max_seconds_per_site <= 86400):
            raise PlanError("--max-seconds-per-site: a number of seconds above 0, up to 86400")
        if args.cache_days is not None and not (math.isfinite(args.cache_days) and args.cache_days >= 0):
            # nan would keep every cached answer forever (no age check passes).
            raise PlanError("--cache-days: a number of days, 0 or more")
        opts = PlanOptions(behaviour="prototype" if args.prototype_behaviour else "fixes", parking=args.parking,
                           atlanta_parking=args.atlanta_parking,
                           trailer_tiers=trailer_tiers_from(args.trailer_ratio) if args.trailer_ratio else TRAILER_TARGET_TIERS,
                           max_seconds=args.max_seconds_per_site, underground=args.underground_detention)
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
        check_assemble_names(assembles, run.rows)
    except (PlanError, ValueError, OSError) as exc:
        print(f"siteplan: {exc}", file=sys.stderr)
        return 2
    out = args.out
    try:
        # The one shared guard: owners and mailing addresses of ranked.csv
        # and rejected.csv (an --assemble or --cut-parcel member may be a
        # rejected parcel).
        terms = OwnerGuard.from_run(run_dir)
    except (OSError, ValueError) as exc:
        print(f"siteplan: cannot read the run's owner columns ({exc})", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
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
        keys_now = {slug(n) for n in names}
        kept = [r for r in old.get("plans", []) if args.redo is False or r.get("key") not in keys_now]
        for r in kept:
            # A name of this call that slugs to a kept plan's key but is
            # another site ("a+b" then "a-b", or rank 2 then an --assemble 2).
            k = r.get("key")
            if k in keys_now and not any(slug(n) == k and n == r.get("rank") for n in names):
                print(f"siteplan: --merge refused: {k!r} is already a plan of another site in --out; "
                      "use another name", file=sys.stderr)
                return 2
    # A kept plan of a site this call names with another definition (other
    # --assemble members, a new or changed --cut-parcel) is dropped and the
    # site planned again: the kept plan answers another question.
    defs = {slug(n): site_definition(n, assembles, cuts) for n in names}
    changed = [r for r in kept if r.get("key") in defs and r.get("site_definition") != defs[r.get("key")]]
    if changed:
        kept = [r for r in kept if not any(r is c for c in changed)]
        print("siteplan: --merge: planned again, the call defines them differently (members or test cut): "
              + ", ".join(str(r.get("rank")) for r in changed), file=sys.stderr)
    # A kept plan that is incomplete (an input not checked) is planned again
    # when this call names it; one this call does not name keeps the exit
    # code at 1 (the folder still holds an incomplete plan).
    done = {r.get("key") for r in kept if not is_incomplete(r)}
    stale = [str(r.get("rank")) for r in kept if is_incomplete(r) and r.get("key") not in {slug(n) for n in names}]
    fetch = si.Fetcher(cache_days=args.cache_days)
    failed = 0
    incomplete: list[str] = []

    def body_of(recs):
        body = dict(header, generated=time.strftime("%Y-%m-%d"), plans=recs)
        return (json.dumps(body, indent=1, default=str) + "\n").encode("utf-8")

    def table_of(recs_: list[dict[str, Any]]) -> bytes:
        """plans.csv: the planned sites side by side (parcel ids and
        numbers; no title or address)."""
        return assess.pipeline_csv(recs_, csv_cell).encode("utf-8")

    def published(recs_: list[dict[str, Any]]) -> list[str]:
        """The site addresses the files write: a single ranked parcel's title
        holds its own site address."""
        out_ = []
        for r in recs_:
            row = run.rows.get(str(r.get("rank")))
            if row is not None and r.get("parcel_ids") == [row.get("parcel_id")]:
                out_.append(row.get("site_address") or "")
        return out_

    def refuse(data: bytes, name: str, public: list[str]) -> None:
        hits = owner_hits(data, terms, public)
        if hits:
            raise OwnerDataFound(f"{name}: owner data found ({', '.join(f'{k}: {v}' for k, v in sorted(hits.items()))}); "
                                 "nothing written for this site")

    recs = list(kept)
    for name in names:
        if slug(name) in done:
            continue
        svg_bytes, fname = None, None
        try:
            spec = si.site_spec(run, name, dict(assembles).get(name), cuts.get(name), fetch, opts)
            inp = si.gather(spec, fetch, opts)
            rec, drawing = plan_site(inp, opts)
            rec["site_definition"] = defs[slug(name)]
            if drawing is not None:
                rec["sheet"] = sr.sheet_info(drawing)
                svg_bytes = sr.render(drawing, rec).encode("utf-8")
                fname = svg_name(inp.key)
                rec["svg"] = fname
            new_recs = [r for r in recs if r.get("key") != rec.get("key")] + [rec]
            body = body_of(new_recs)
            table = table_of(new_recs)
            # Every artifact of this site is guarded before any is written
            # (R1): a refusal leaves --out as it was before this site.
            pub_site, pub_all = published([rec]), published(new_recs)
            if svg_bytes is not None:
                refuse(svg_bytes, fname, pub_site)
            refuse(body, PLANS_FILE, pub_all)
            refuse(table, PIPELINE_FILE, pub_all)
            if svg_bytes is not None:
                write_atomic(out / fname, svg_bytes, out, terms, pub_site)
            write_atomic(plans_path, body, out, terms, pub_all)
            write_atomic(out / PIPELINE_FILE, table, out, terms, pub_all)
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
        recs = new_recs
        if is_incomplete(rec):
            incomplete.append(name)
        b = rec.get("building", {})
        print(name, rec.get("configuration"), b.get("sf"), "feasible", rec.get("feasible"), "doors", rec.get("doors"),
              "trailers", (rec.get("trailers") or {}).get("total"), "cars", (rec.get("cars") or {}).get("provided"), flush=True)
    if not recs:
        write_atomic(plans_path, body_of([]), out, terms)
        write_atomic(out / PIPELINE_FILE, table_of([]), out, terms)
    keep = {r.get("svg") for r in recs if r.get("svg")}
    for f in out.glob("plan-*.svg"):
        if SVG_NAME.match(f.name) and f.name not in keep:
            f.unlink()           # stale plan from an earlier call (R11)
    if incomplete:
        # Written, but an input fetch failed for these sites (feasible is
        # null, or a NO_FIT with a not-checked input): run them again later.
        print(f"siteplan: {len(incomplete)} plan(s) incomplete (an input was not checked; run again later): "
              + ", ".join(incomplete), file=sys.stderr)
    if stale:
        print(f"siteplan: --out still holds {len(stale)} incomplete plan(s) kept by --merge (not named in this call; "
              "name them again, with --merge, to plan them again): " + ", ".join(stale), file=sys.stderr)
    return 1 if failed or incomplete or stale else 0


if __name__ == "__main__":
    # Run the package module's main, not this __main__ copy's: the fetch
    # layer raises screener.siteplan.PlanError, which main() must catch.
    from . import siteplan as _siteplan
    sys.exit(_siteplan.main())
