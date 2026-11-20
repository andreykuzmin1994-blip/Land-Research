"""The read-out of a site plan: what it says, what limits it, what to do next.

``siteplan`` finds a layout and lists flags. This module turns that into
what a land acquisition review asks of a test fit:

- ``site_data``: where every acre goes (building, truck paving, trailer
  and car parking, detention, land outside the paving envelope, land left
  unused) and the ratios read off it;
- ``compliance``: the plan measured against the DRAFT zoning values it was
  drawn under (setbacks by edge, height, coverage, parking, use), with the
  margin on each line;
- ``quantities``: a takeoff of the drawn plan (paving by duty, earthwork,
  detention, routes), with no prices;
- ``selection``: why the plan drawn ranks above the other layouts tried,
  and the larger layout that passed its checks, when there is one;
- ``assessment``: one status, a headline, each flag as a condition with a
  workstream, a stage, an action and what closes it, and the next steps
  in order.

It changes no layout, flag, feasibility or floor area: every value here is
derived from the plan ``siteplan`` already made. It is pure (geometry and
plain data in, plain data out) and reads no network and no owner field.

DRAFT. The compliance table checks the plan against ``siteplan.JUR_DRAFT``
(draft screening values), not against an adopted code. "Meets" means the
drawing honours the draft value, nothing more.

The actions in ``PLAYBOOK`` are the usual next step for each flag. They
are prompts for a person, not findings: none of them was checked against a
document.
"""

from __future__ import annotations

import csv
from typing import Any, Callable, Iterable

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

SQFT_PER_AC = 43560.0

# ---------------------------------------------------------------------------
# Status of a test fit (one word for the list, one label for the sheet)
# ---------------------------------------------------------------------------
STATUS_LABELS = {
    "fits": "FITS: STANDARD CHECKS ONLY",
    "fits_with_conditions": "FITS WITH CONDITIONS",
    "gated": "FITS: ENTITLEMENT GATE",
    "hypothetical": "NO ROAD ACCESS: HYPOTHETICAL",
    "blocked": "BLOCKED AS TESTED",
    "incomplete": "INCOMPLETE: RUN AGAIN",
    "no_fit": "NO BUILDING FITS",
    "not_planned": "NOT PLANNED: RUN AGAIN",
}
STATUS_ORDER = tuple(STATUS_LABELS)        # best first
# Whether the drawn floor area is one to carry into a pipeline total.
COUNTED_STATUSES = frozenset({"fits", "fits_with_conditions"})

# Stages, in the order the work is done: what a desk check answers first,
# what has to be known before a price is offered, what third parties do
# once the site is under contract.
STAGES = (("desk", "Desk check"), ("offer", "Before an offer"), ("diligence", "Due diligence"))
STAGE_LABEL = dict(STAGES)
STAGE_ORDER = {k: i for i, (k, _) in enumerate(STAGES)}

W_ZONING = "Zoning and entitlement"
W_ACCESS = "Access and traffic"
W_FIRE = "Fire access"
W_GRADING = "Grading"
W_STORM = "Stormwater"
W_ENV = "Environmental"
W_UTIL = "Utilities and easements"
W_PROGRAM = "Program and layout"
W_TITLE = "Title and survey"
W_DATA = "Planner inputs"

# Flags that stand on every plan (or on every plan with a pond): the limits
# of the method, not a finding about this site.
STANDING_FLAGS = frozenset({"ZONING_DRAFT", "STREAMS_PIPELINES", "POND_SITE_FROM_LIDAR"})
GATE_FLAGS = frozenset({"USE_PERMIT_REQUIRED"})
RERUN_FLAGS = frozenset({
    "FRONTAGE_NOT_CHECKED", "FRONTAGE_GEOMETRY", "DEM_NOT_CHECKED", "DEM_PARTIAL", "NEIGHBOURS_NOT_CHECKED",
    "TRANSMISSION_NOT_CHECKED", "LAYERS_NOT_CHECKED", "PLAN_BUDGET_EXCEEDED", "SECOND_BUILDING_NOT_SEARCHED",
    "SMALLER_LAYOUTS_NOT_ALL_TRIED"})

_RERUN = (W_DATA, "desk", "Run the planner again for this site: a map service did not answer, so this is not a result yet.",
          "a complete plan (feasible true or false)")
_BUDGET = (W_DATA, "desk", "Run the planner again with a larger --max-seconds-per-site.", "a search that finished")
_GUARD = (W_DATA, "desk", "The planner's own layout check failed. Do not rely on this plan; run it again and report it if it repeats.",
          "a plan with no layout conflict")

# flag -> (workstream, stage, action, what closes it)
PLAYBOOK: dict[str, tuple[str, str, str, str]] = {
    # access
    "ACCESS_NONE": (W_ACCESS, "desk", "Check the road map by hand first: the frontage check reads GDOT's road layer, which can "
                    "miss a road. If there is none, look for an easement of record, a strip to a road, or a neighbour to buy "
                    "from, and carry no floor area until access is in hand.",
                    "a road on the plat, a recorded access easement, or a contract on the access parcel"),
    "ACCESS_CONSTRAINED": (W_ACCESS, "offer", "Measure the frontage on the plat. The planner allows about 190 ft of curb for a "
                           "truck driveway (its own assumption); ask the road authority what it requires, and look for a "
                           "shared driveway or a second frontage.",
                           "frontage length from a plat or survey, and the road authority's driveway requirements"),
    "ROUTE_INFEASIBLE": (W_PROGRAM, "desk", "No truck route reaches the court inside the paving area. Look at the drawing, then "
                         "plan it again with a test cut (--cut-parcel) or as an assemblage (--assemble).",
                         "a layout with a truck route"),
    "CAR_ROUTE_NONE": (W_PROGRAM, "desk", "Cars cannot reach the car field without crossing truck pavement. Look at the drawing, "
                       "then plan it again with a test cut (--cut-parcel) or as an assemblage (--assemble).",
                       "a layout with a separate car route"),
    "ROUTE_OUTSIDE_ENVELOPE": _GUARD,
    "PLAN_GEOMETRY_CONFLICT": _GUARD,
    "NOT_FEASIBLE_REASON": _GUARD,
    "FIRE_SECOND_ACCESS": (W_FIRE, "offer", "Ask the fire marshal whether a second, emergency-only access is required and where "
                           "it could go. It can need land or an easement, so ask before an offer.",
                           "the fire marshal's comment on the concept"),
    "DEAD_END_ACCESS": (W_FIRE, "diligence", "Ask the fire marshal about the dead-end length and the turnaround it needs.",
                        "the fire marshal's comment on the concept"),
    "SHORT_THROAT": (W_ACCESS, "diligence", "Have the civil engineer check truck stacking between the street and the court; move "
                     "the gate or lengthen the throat.", "a driveway and gate layout with the stacking length"),
    "TRUCK_PATH_NONE": (W_ACCESS, "offer", "No tractor-trailer can drive from the driveway to the truck court as laid out. "
                        "Have the civil engineer lay out the truck entrance: a driveway in line with the truck road, or a "
                        "shorter building at that end.", "a truck-turning layout (WB-67) from the street to the docks"),
    "TRUCK_ENTRANCE_S_BEND": (W_ACCESS, "offer", "Trucks make a tight S-bend as they come off the street, because the driveway is "
                              "to one side of the truck road. Have the civil engineer lay out the entrance: a driveway in line "
                              "with the truck road, or a shorter building at that end.",
                              "a truck entrance layout (WB-67) from the street to the truck road"),
    "TRUCK_SWEPT_PATH": (W_ACCESS, "offer", "A tractor-trailer's body sweeps off the truck pavement at a bend on its way "
                         "in. Have the civil engineer widen the bend or move what stands in it.",
                         "a truck-turning layout (WB-67) from the street to the docks"),
    "PARKING_SHORT_FOR_LEAD_IN": (W_PROGRAM, "diligence", "The trucks' way in takes car stalls and leaves fewer than required. Have "
                                  "the civil engineer re-lay the car field (another row or a deeper front yard), or shorten "
                                  "the building.", "a car-parking layout that meets the count with the truck entrance in place"),
    "TRUCK_LEAD_IN_OVER_PARKING": (W_ACCESS, "diligence", "The trucks' way in crosses the corner of the car field. Have the civil "
                                   "engineer re-lay that corner so trucks and parked cars are apart.",
                                   "a site layout with the truck entrance and car field separated"),
    "TRUCK_REVERSE_BEND": (W_ACCESS, "diligence", "The trucks' way in has a reverse bend with little straight between. Have the "
                           "civil engineer straighten it or move the driveway into line with the truck road.",
                           "a truck-turning layout (WB-67) from the street to the docks"),
    "BLIND_SIDE_BACKING": (W_ACCESS, "diligence", "Trucks enter the court with the docks on their right and back in on the blind "
                           "side. Ask the civil engineer whether the driveway can go to the other end so they circulate "
                           "counter-clockwise.", "a circulation layout with the dock doors on the driver's side"),
    "ACCESS_SHARED": (W_ACCESS, "diligence", "One driveway serves trucks and cars. Ask the road authority whether a second "
                      "driveway can be permitted; if not, design the split inside the throat.",
                      "the road authority's answer on a second driveway"),
    "TRUCK_ENTRY_OFFICE_SIDE": (W_ACCESS, "diligence", "Trucks pass the office side to reach the court. Confirm the tenant accepts it.",
                                "the tenant's or broker's view"),
    "COLLECTOR_FRONTAGE": (W_ZONING, "desk", "Check the road's classification with Fairburn: its M districts need collector "
                           "frontage (draft reading).", "the city's road classification"),
    # zoning and entitlement
    "USE_PERMIT_REQUIRED": (W_ZONING, "offer", "Ask planning staff whether warehousing is by right, by use permit or prohibited "
                            "in this district, and how long a permit takes. Make any offer conditional on it.",
                            "pre-application meeting notes, then the use permit"),
    "HEIGHT_CAP": (W_ZONING, "desk", "Check the height limit in the adopted code, and whether a variance or another district "
                   "lifts it. The clear height drawn is lowered to fit the draft limit.", "the code section"),
    "COVERAGE_OVER_CAP": (W_ZONING, "desk", "Check the coverage limit in the adopted code. If it stands, the limit sets the yield.",
                          "the code section"),
    "ZONING_DRAFT": (W_ZONING, "desk", "Check the setbacks, buffers, parking, coverage and height used here against the adopted "
                     "code, and ask the jurisdiction whether the parcel carries conditions from an earlier rezoning.",
                     "the code sections, and a zoning verification letter for the district, the use and any conditions"),
    "JURISDICTION_DEFAULTED": (W_ZONING, "desk", "The planner has no draft rules for this place and used South Fulton's. Read the "
                               "district's standards before relying on the layout.", "the district standards"),
    "ATL_PARKING_CODE": (W_ZONING, "desk", "Confirm Atlanta's current parking minimum for this use. At 1 per 600 SF the car field "
                         "grows and the building shrinks.", "the current code section"),
    "DISTRICT_BUFFER_NOT_DRAWN": (W_ZONING, "desk", "Check Fairburn's buffer toward other districts. If it applies, plan the site "
                                  "again with it: the yield falls.", "the code section"),
    "RESIDENTIAL_EDGE": (W_ZONING, "desk", "Confirm the neighbours' zoning (the edge is read from county land-use codes) and the "
                         "buffer the code requires toward it.", "the zoning map and the buffer section of the code"),
    "LOADING_FACES_RES": (W_ZONING, "offer", "A truck court or trailer row sits within 150 ft of land coded residential. Check "
                          "what is there and the code's loading rules toward it; a wider buffer, a wall or berm may be asked for.",
                          "the code section and planning staff's view"),
    "STREET_FACING_DOCKS": (W_ZONING, "desk", "Check whether the code allows dock doors facing the street and what screening it "
                            "asks for.", "the code section"),
    "ASSEMBLAGE_MIXED_RULES": (W_ZONING, "offer", "The members fall under different rules. Ask planning staff which code would "
                               "govern the combined site (annexation or rezoning), then plan it again under that code.",
                               "planning staff's answer on the governing code"),
    # stormwater, grading, environmental
    "POND_DOES_NOT_FIT": (W_STORM, "offer", "Have a civil engineer test a smaller building, and ask whether a regional or "
                          "off-site pond serves the site.", "a detention concept sized under the local ordinance"),
    "CORRIDOR_OVERLAY": (W_ZONING, "desk", "Check whether the road is in the corridor overlay. On an overlay road the front "
                         "setbacks are much deeper and the yield falls; the plan applies them only on the state routes it "
                         "can read.", "the overlay map or the code's list of roads"),
    "USE_RESTRICTION_REPORTED": (W_ZONING, "desk", "Ask planning staff whether warehousing is allowed here today and what "
                                 "replaced the moratorium. Do not rely on this plan until that is answered.",
                                 "planning staff's answer, or the adopted text amendment"),
    "POND_UPHILL": (W_STORM, "offer", "The pond is drawn on ground above the building pad, so the paving cannot drain to it "
                    "by gravity. Have a civil engineer lay the site out from its low point: the pond where the water "
                    "leaves, the building above it.", "a stormwater concept that drains by gravity, with pad and pond elevations"),
    "POND_ABOVE_PAD": (W_STORM, "diligence", "The pond is drawn on ground a little above the building pad. Have the civil "
                       "engineer set the pad and pond elevations; a deeper pond takes more room than is drawn.",
                       "a stormwater concept with pad and pond elevations"),
    "DOCKS_UPHILL": (W_GRADING, "diligence", "The truck court is on the high side of the building and must be cut down to dock "
                     "height. Have the civil engineer price that cut and its slope or wall. Turning the building round is an "
                     "option only where the street, neighbours and routes allow (see the note).",
                     "a grading concept with floor and court elevations"),
    "POND_SHAPED_TO_SITE": (W_STORM, "diligence", "The pond is shaped to the ground the layout leaves, not a rectangle. Have the "
                            "civil engineer confirm its volume, its banks and the access to maintain it.",
                            "a stormwater concept with the pond's grading"),
    "ACCESS_THROUGH_FIRST": (W_ACCESS, "offer", "The rear building is reached only through the front building's truck road and "
                             "court. Have the civil engineer lay out a shared drive, and check the fire access it gives.",
                             "a site layout with the shared drive to the rear building"),
    "POND_SITE_FROM_LIDAR": (W_STORM, "diligence", "Have the civil engineer confirm the pond location, outfall and volume under "
                             "the local ordinance. The size here is a screening estimate.", "a stormwater concept"),
    "HSG_ASSUMED": (W_STORM, "diligence", "The soil group behind the pond size was assumed. Run the soil check for this parcel, "
                    "then confirm with borings.", "the soil survey reading, then a geotechnical report"),
    "GRADING_RISK": (W_GRADING, "diligence", "Order a topographic survey and a mass-grading estimate. Steep ground under the pad "
                     "can mean walls, rock and soil hauled in or out.", "a grading concept with cut, fill and wall quantities"),
    "RELIEF": (W_GRADING, "diligence", "Have the civil engineer test a stepped pad or retaining walls and price the earthwork; "
               "ask the geotechnical engineer about rock.", "a grading concept with cut, fill and wall quantities"),
    "STREAMS_PIPELINES": (W_ENV, "desk", "Check for streams and petroleum pipelines, which the planner does not map: the county "
                          "stream layer, the USGS topographic map and the national pipeline map. A stream adds buffers that can "
                          "cut the site.", "a stream and pipeline check on public maps, then a delineation"),
    "TRANSMISSION_EASEMENT_ASSUMED": (W_UTIL, "offer", "Get the recorded easement from title and the utility's rules for paving "
                                      "under the line. The corridor width here is assumed from the voltage.",
                                      "the easement document and the utility's written consent"),
    "VOLTAGE_ASSUMED": (W_UTIL, "offer", "The line's voltage is not known, so its corridor is a guess. Find the line's owner and "
                        "the recorded easement width.", "the easement document"),
    # program and layout
    "NO_FIT": (W_PROGRAM, "desk", "No tested building fits. Consider outdoor storage, an assemblage with a neighbour, or dropping "
               "the site.", "a decision to assemble, re-purpose or drop"),
    "TRAILER_SHORT": (W_PROGRAM, "offer", "Confirm how many trailer stalls the target tenant needs. If the target stands, the "
                      "building shrinks or land is added.", "the tenant's or broker's trailer requirement"),
    "DROP_YARD_UNREACHABLE": (W_PROGRAM, "diligence", "The spare ground for a trailer yard has no truck route. Count only the "
                              "trailer stalls in the court rows.", "a revised trailer count"),
    "BELOW_COMP_BAND": (W_PROGRAM, "offer", "The yield is below Georgia comps. Price the land on this floor area, not on gross "
                        "acres.", "a land price per buildable SF"),
    "LD_OUT_OF_BAND": (W_PROGRAM, "diligence", "The building's proportions are outside the usual range. Have the architect "
                       "confirm it works for the tenant.", "the architect's test fit"),
    "DOOR_DENSITY_HIGH": (W_PROGRAM, "diligence", "The dock wall is tight for the door count. Have the architect confirm the "
                          "door spacing.", "the architect's test fit"),
    "IMPERVIOUS_RATIO": (W_PROGRAM, "diligence", "The paving is unusually large or small for the building. Check the drawing "
                         "for long routes or a thin car field.", "a reviewed layout"),
    # title and survey
    "ACREAGE_MISMATCH": (W_TITLE, "offer", "The county map and the tax roll disagree on the acreage. Get the deed and the plat, "
                         "and price on surveyed acres.", "the deed and plat, then a boundary survey"),
    "PARCEL_MULTIPART": (W_TITLE, "desk", "The parcel is in separate pieces. Find out what lies between them (a road, another "
                         "owner) before counting the far piece.", "the plat or the tax map"),
    "ASSEMBLAGE_NOT_CONTIGUOUS": (W_TITLE, "desk", "The parcels listed do not touch. Add the parcel between them, or plan them "
                                  "apart.", "a parcel list that forms one site"),
    "ASSEMBLAGE_MEMBER_NOT_RANKED": (W_DATA, "desk", "A member is outside the run's ranked list. Check its jurisdiction, zoning "
                                     "and soils by hand.", "the member's zoning and soil group"),
    # planner inputs
    "FRONTAGE_NOT_CHECKED": _RERUN, "DEM_NOT_CHECKED": _RERUN,
    # Two that a second run may not clear: the cause can be in the data.
    "FRONTAGE_GEOMETRY": (W_DATA, "desk", "The frontage line could not be read as one run, so access was not checked. Run the "
                          "planner again; if it repeats, check the frontage on the plat by hand.",
                          "a complete plan, or the frontage read from the plat"),
    "DEM_PARTIAL": (W_DATA, "desk", "Part of the site has no lidar ground values. Run the planner again; if the gap repeats it is "
                    "in the lidar, and slope, relief and the pond location are unknown for that part.",
                    "a complete plan, or a topographic survey of the gap"),
    "NEIGHBOURS_NOT_CHECKED": _RERUN, "TRANSMISSION_NOT_CHECKED": _RERUN, "LAYERS_NOT_CHECKED": _RERUN,
    "PLAN_BUDGET_EXCEEDED": _BUDGET, "SECOND_BUILDING_NOT_SEARCHED": _BUDGET, "SMALLER_LAYOUTS_NOT_ALL_TRIED": _BUDGET,
}
_UNKNOWN = (W_DATA, "desk", "Read this flag against the drawing.", "")
# By the market's decision on underground detention (an operator decision:
# "no" where land is cheap, "yes" where it is dear). A record that states
# neither (the prototype behaviour) gets the plain entry above.
PLAYBOOK_BY_MARKET: dict[str, dict[str, tuple[str, str, str, str]]] = {
    "no": {"POND_DOES_NOT_FIT": (W_STORM, "offer", "Have a civil engineer test a smaller building, and ask whether a regional "
                                 "or off-site pond serves the site. Underground detention is not used in this market.",
                                 "a detention concept sized under the local ordinance")},
    "yes": {"POND_DOES_NOT_FIT": (W_STORM, "offer", "Have a civil engineer price underground detention against the floor area "
                                  "it frees, and ask whether a regional or off-site pond serves the site.",
                                  "a detention concept sized under the local ordinance, with a vault cost")},
}
# A flag raised at two severities for two different facts.
PLAYBOOK_AT_SEVERITY: dict[tuple[str, int], tuple[str, str, str, str]] = {
    ("ASSEMBLAGE_MIXED_RULES", 2): (W_ZONING, "desk", "The members are zoned differently within one jurisdiction, and the draft "
                                    "rules used are the jurisdiction's. Read each district's standards before relying on "
                                    "the layout.", "the district standards of each member"),
}


def playbook_for(flag: str, severity: int | None = None, market: str | None = None) -> tuple[str, str, str, str]:
    """The playbook entry of a flag. A second building's flag
    (``SECOND_<flag>``) takes the entry of the flag it repeats. market:
    the record's decision on underground detention ("yes" / "no")."""
    base = flag[7:] if flag.startswith("SECOND_") and flag[7:] in PLAYBOOK else flag
    if base in PLAYBOOK_BY_MARKET.get(market or "", {}):
        return PLAYBOOK_BY_MARKET[market][base]
    if (base, severity) in PLAYBOOK_AT_SEVERITY:
        return PLAYBOOK_AT_SEVERITY[(base, severity)]
    return PLAYBOOK.get(base, _UNKNOWN)


# ---------------------------------------------------------------------------
# Geometry read-outs (called by siteplan with the plan's own shapes)
# ---------------------------------------------------------------------------
def _union(geoms: Iterable[BaseGeometry | None]) -> BaseGeometry:
    keep = [g for g in geoms if g is not None and not g.is_empty]
    return unary_union(keep) if keep else Polygon()


def _ac(g: BaseGeometry) -> float:
    return g.area / SQFT_PER_AC


def _polys(g: BaseGeometry) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [q for x in getattr(g, "geoms", []) for q in _polys(x)]


# The uses of land, in the order a shape is assigned when two overlap (a
# truck route runs through its own court; the court keeps the area).
USES = (("building", "Building footprint"), ("trailers", "Trailer stalls and drop yards"), ("cars", "Car parking"),
        ("truck", "Truck courts, roads and routes"), ("car_drives", "Car drives"), ("ponds", "Detention ponds, with rim"))
UNUSED_MIN_WIDTH_FT = 40.0     # a remainder narrower than this is not a usable piece


def use_shapes(layouts: list[tuple[Any, dict[str, Any]]], parcel: BaseGeometry) -> dict[str, BaseGeometry]:
    """The shapes of each land use over every building of the plan, clipped
    to the parcel, with no area in two uses."""
    raw = {
        "building": _union(p.building for p, _ in layouts),
        "trailers": _union([g for p, o in layouts for g in list(p.rows) + list(o["drops"])]),
        "cars": _union(g for p, _ in layouts for g in p.cars),
        "truck": _union([g for p, o in layouts for g in list(p.courts) + list(p.roads)
                         + [rp for kind, rp in o["route_polys"] if kind == "truck"]]),
        "car_drives": _union(rp for _, o in layouts for kind, rp in o["route_polys"] if kind == "car"),
        "ponds": _union(g for _, o in layouts for g in o["ponds"]),
    }
    out: dict[str, BaseGeometry] = {}
    taken: BaseGeometry = Polygon()
    for key, _ in USES:
        g = raw[key].intersection(parcel)
        if not taken.is_empty:
            g = g.difference(taken)
        out[key] = g
        taken = _union([taken, g])
    return out


def land_budget(parcel: BaseGeometry, P: BaseGeometry, envelope0_sf: float,
                shapes: dict[str, BaseGeometry]) -> dict[str, Any]:
    """Where every acre of the site goes. ``shapes``: ``use_shapes`` of the
    plan (empty shapes for a site with no plan). ``envelope0_sf``: the
    area inside the draft setbacks and buffers before any mapped
    constraint is taken out (so the two losses can be told apart). The
    uses, the unused net area and the unused area outside it add up to the
    gross area."""
    gross = _ac(parcel)
    net = _ac(P)
    used = _union(shapes.values())
    uses = [{"use": key, "label": label, "ac": round(_ac(shapes[key]), 2),
             "pct_gross": round(100 * _ac(shapes[key]) / gross, 1) if gross else 0.0} for key, label in USES]
    free = P.difference(used) if not used.is_empty else P
    outside = parcel.difference(P)
    outside_free = outside.difference(used) if not used.is_empty else outside
    # A usable remainder: what is left once narrow strips are dropped.
    half = UNUSED_MIN_WIDTH_FT / 2
    pieces = _polys(free.buffer(-half).buffer(half)) if not free.is_empty else []
    setbacks = max(gross - envelope0_sf / SQFT_PER_AC, 0.0)
    return {
        "gross_ac": round(gross, 2), "net_ac": round(net, 2), "net_pct": round(100 * net / gross, 1) if gross else 0.0,
        "setbacks_buffers_ac": round(setbacks, 2),
        "constraints_ac": round(max(gross - net - setbacks, 0.0), 2),
        "uses": uses,
        "used_ac": round(_ac(used), 2),
        "unused_net_ac": round(_ac(free), 2),
        "largest_unused_ac": round(max((_ac(q) for q in pieces), default=0.0), 2),
        "outside_net_unused_ac": round(_ac(outside_free), 2),
        "basis": ("net = inside the draft paving setbacks and buffers, outside mapped water, wetlands, floodplain, "
                  "easements and protected land (with a 25 ft buffer) and any test cut; a remainder counts when it is "
                  f"at least {UNUSED_MIN_WIDTH_FT:.0f} ft wide"),
    }


EDGE_LABELS = (("ROW", "road"), ("RES_SIDE", "residential side"), ("RES_REAR", "residential rear"),
               ("COMM", "commercial or institutional"), ("OTHER_REAR", "rear lot line"), ("OTHER", "other lot lines"))
SETBACK_TOL_FT = 1.0
SETBACK_REACH_FT = 1600.0     # past this a shape is not in front of the lot line at all


def _line_parts(g: BaseGeometry) -> list[BaseGeometry]:
    if g is None or g.is_empty:
        return []
    if g.geom_type in ("LineString", "LinearRing"):
        return [g]
    return [q for x in getattr(g, "geoms", []) for q in _line_parts(x)]


def band_clearance(shape: BaseGeometry, line: BaseGeometry, reach: float = SETBACK_REACH_FT) -> float | None:
    """How far `shape` stands off the lot line `line`, measured square to
    the line: the distance from each stretch of the line to the part of
    the shape that lies in front of it, and to the line's bends. Along
    the line and at its bends that is the plain distance; past an end of
    the line it is not counted. None when no part of the shape is in
    front of the line within `reach`.

    Plain distance alone would be wrong at the end of a line: a
    residential edge is read 20 ft past the corner, and a module 115 ft
    off the rear line is under 100 ft from that stub's end without being
    behind it. The planner's envelope is square at the ends too
    (``siteplan.envelope``), so a plan it placed does not read short here
    for that reason. (The envelope is mitred at bends, which pushes a
    plan further back than this measure asks.)"""
    best: float | None = None
    for part in _line_parts(line):
        pts = [(c[0], c[1]) for c in part.coords]
        closed = len(pts) > 2 and pts[0] == pts[-1]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            n = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
            if n < 1e-9:
                continue
            nx, ny = -(by - ay) / n * reach, (bx - ax) / n * reach
            strip = Polygon([(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)])
            front = shape.intersection(strip)
            if not front.is_empty:
                d = front.distance(LineString([(ax, ay), (bx, by)]))
                best = d if best is None else min(best, d)
        for x, y in (pts[:-1] if closed else pts[1:-1]):          # the bends: plain distance
            d = shape.distance(Point(x, y))
            if d <= reach:
                best = d if best is None else min(best, d)
    return best


def setback_rows(rules: dict[str, Any], edges: list[tuple[str, BaseGeometry]], buildings: BaseGeometry,
                 paving: BaseGeometry) -> list[dict[str, Any]]:
    """The building and the module's paving measured to each kind of lot
    line, against the draft offsets, the way the envelope applied them
    (``band_clearance``). Driveways and routes are left out: a driveway
    has to cross the road setback."""
    rows = []
    for kind, label in EDGE_LABELS:
        line = _union(ln for k, ln in edges if k == kind)
        if line.is_empty:
            continue
        for what, shape, table in (("Building", buildings, rules["bldg"]), ("Paving", paving, rules["pave"])):
            if shape.is_empty:
                continue
            need = float(table.get(kind, 0) or 0)
            got = band_clearance(shape, line)
            if got is None:
                rows.append({"item": f"{what} setback, {label}", "required": f"{need:.0f} ft", "provided": "well clear of this line",
                             "margin_ft": None, "status": "meets"})
                continue
            rows.append({"item": f"{what} setback, {label}", "required": f"{need:.0f} ft", "provided": f"{got:.0f} ft",
                         "margin_ft": round(got - need), "status": "meets" if got >= need - SETBACK_TOL_FT else "short"})
    return rows


# Building code, not zoning: a one-storey sprinklered warehouse larger than
# the code's tabular area is built under the unlimited-area rule, which
# wants open space (a yard or a public way) 60 ft wide all round it; 40 ft
# is allowed along part of the perimeter with 3-hour walls. From the
# International Building Code, Section 507, as this repository's
# human-owned facts file reads it (medium confidence there: verify with
# the building official). A street counts as open space, so the row
# measures to the lot lines that are not a road.
OPEN_YARD_FT = 60.0
OPEN_YARD_REDUCED_FT = 40.0


def open_yard_row(edges: list[tuple[str, BaseGeometry]], buildings: BaseGeometry) -> dict[str, Any] | None:
    """The narrowest yard between a building and a lot line that is not a
    road, against the building code's unlimited-area open space."""
    lines = _union(ln for k, ln in edges if k != "ROW")
    if lines.is_empty or buildings.is_empty:
        return None
    got = buildings.distance(lines)
    status = "meets" if got >= OPEN_YARD_FT - SETBACK_TOL_FT else ("limited" if got >= OPEN_YARD_REDUCED_FT else "short")
    row = {"item": "Open yard at the building (building code)",
           "required": f"{OPEN_YARD_FT:.0f} ft ({OPEN_YARD_REDUCED_FT:.0f} ft with 3-hour walls)",
           "provided": f"{got:.0f} ft to the nearest lot line", "margin_ft": round(got - OPEN_YARD_FT), "status": status}
    if status != "meets":
        row["note"] = ("under the unlimited-area rule a yard this narrow needs rated walls" if status == "limited"
                       else "under the unlimited-area rule this yard is too narrow: a smaller or area-limited building")
    return row


def compliance(rules: dict[str, Any], edges: list[tuple[str, BaseGeometry]], layouts: list[tuple[Any, dict[str, Any]]],
               *, clear_ft: int, clear_assumed_ft: int, roof_allowance_ft: int, cover: tuple[str, float, float] | None,
               building_pct: float, impervious_pct: float, cars_required: int, cars_provided: int, cars_basis: str,
               gated: list[str], truck_spaces: tuple[int, int] | None = None) -> list[dict[str, Any]]:
    """The plan against the DRAFT zoning values it was drawn under, one row
    per rule: item, the draft value, what the plan provides, the margin and
    a status (meets, short, over, limited, gate, verify, no draft value).
    One row is building code, not zoning: the open yard round the building
    (``open_yard_row``).

    cover: (kind, cap, value) when the draft table has a coverage cap."""
    buildings = _union(p.building for p, _ in layouts)
    paving = _union([p.module for p, _ in layouts] + [g for _, o in layouts for g in o["drops"]])
    rows = setback_rows(rules, edges, buildings, paving)
    yard = open_yard_row(edges, buildings)
    if yard:
        rows.append(yard)
    one = " (building 1)" if len(layouts) > 1 else ""
    cap = rules.get("height")
    height = clear_ft + roof_allowance_ft
    if cap:
        lowered = clear_ft < clear_assumed_ft
        rows.append({"item": "Building height" + one, "required": f"{cap} ft max",
                     "provided": f"{height} ft ({clear_ft} ft clear + {roof_allowance_ft} ft roof)",
                     "margin_ft": round(cap - height),
                     "status": "limited" if lowered else ("meets" if height <= cap else "over"),
                     **({"note": f"clear height lowered from {clear_assumed_ft} ft to fit the draft limit"} if lowered else {})})
    else:
        rows.append({"item": "Building height" + one, "required": "no limit in the draft table",
                     "provided": f"{height} ft ({clear_ft} ft clear + {roof_allowance_ft} ft roof)", "status": "no draft value"})
    if cover:
        kind, capv, val = cover
        rows.append({"item": f"Coverage ({kind})", "required": f"{capv:.0%} max", "provided": f"{val:.0%}",
                     "status": "meets" if val <= capv else "over"})
    else:
        rows.append({"item": "Coverage", "required": "no limit in the draft table",
                     "provided": f"building {building_pct:.0f}%, impervious {impervious_pct:.0f}%", "status": "no draft value"})
    rows.append({"item": "Car parking" + one, "required": f"{cars_required} stalls", "provided": f"{cars_provided} stalls",
                 "status": "meets" if cars_provided >= cars_required else "short", "note": cars_basis})
    if truck_spaces:
        need, got = truck_spaces
        rows.append({"item": "Truck spaces", "required": f"{need} spaces", "provided": f"{got} trailer stalls",
                     "status": "meets" if got >= need else "short", "note": "a draft code minimum, apart from the trailer target"})
    if gated:
        rows.append({"item": "Use", "required": "use permit (draft reading)", "provided": "not in hand", "status": "gate"})
    else:
        rows.append({"item": "Use", "required": "not checked by the planner", "provided": "see the zoning path in the brief",
                     "status": "verify"})
    return rows


def quantities(shapes: dict[str, BaseGeometry], layouts: list[tuple[Any, dict[str, Any]]], *, building_sf: int,
               frontage_ft: Any, residential_edge_ft: float, net_ac: float, round_to: int = 1000) -> dict[str, Any]:
    """A takeoff of the drawn plan for a first budget: areas by paving
    duty, earthwork, detention and route lengths. No prices."""
    def rnd(x: float, base: int = round_to) -> int:
        return int(base * round(float(x) / base))
    heavy = shapes["truck"].area + shapes["trailers"].area
    light = shapes["cars"].area + shapes["car_drives"].area
    first = layouts[0][1]
    earth = first.get("cutfill_cy")
    return {
        "building_sf": building_sf,
        "heavy_paving_sf": rnd(heavy), "light_paving_sf": rnd(light),
        "pond_footprint_ac": round(_ac(shapes["ponds"]), 2),
        "detention_acft": round(sum(o["v_det"] for _, o in layouts), 1),
        "ponds": sum(len(o["ponds"]) for _, o in layouts),
        "earthwork_cy": rnd(earth) if earth else None,
        # each building's figure is capped at the net area; so is their sum
        "disturbed_ac": round(min(sum(o.get("disturbed_ac") or 0.0 for _, o in layouts), net_ac), 1),
        "driveways": sum((o["tpt"] is not None) + (o["cpt"] is not None) for _, o in layouts if not o.get("shared_access")),
        "truck_route_lf": round(sum(ln.length for _, o in layouts for k, ln in o["routes"] if k == "truck")),
        "car_route_lf": round(sum(ln.length for _, o in layouts for k, ln in o["routes"] if k == "car")),
        "frontage_ft": frontage_ft, "residential_edge_lf": round(residential_edge_ft),
        "basis": ("measured off the drawn test fit, no prices: heavy paving is truck courts, truck roads and routes, trailer "
                  "stalls and drop yards; light paving is car parking and car drives; earthwork is one balanced pad under "
                  "building 1 and its paving, without pond excavation, walls or rock"),
    }


# ---------------------------------------------------------------------------
# Why this layout
# ---------------------------------------------------------------------------
def _marks(a: dict[str, Any], w: dict[str, Any]) -> dict[str, float]:
    """The ranking's mark-downs of one layout, in SF (siteplan.RANKING)."""
    short = max((a.get("trailers_target") or 0) - (a.get("trailers") or 0), 0)
    return {"trailer": w["trailer_sf"] * short,
            "steep": (a.get("sf") or 0) * w["steep_share"] * (a.get("steep_pct") or 0) / 100,
            "relief": w["relief_sf_per_ft"] * max((a.get("relief_ft") or 0) - w["relief_free_ft"], 0)}


MARK_MIN_SF = 500      # a mark-down smaller than this is not named as a reason
POND_GAIN_MIN = 1.05   # a layout held only by the pond is reported from this multiple of the plan drawn


def _reasons(a: dict[str, Any], chosen: dict[str, Any], w: dict[str, Any]) -> list[str]:
    """Why a larger layout that passed its checks ranks below the plan
    drawn: the mark-downs it carries beyond the plan's own."""
    mine, theirs = _marks(a, w), _marks(chosen, w)
    out = []
    code = a.get("code_trailers") or 0
    if code and (a.get("trailers") or 0) < code and (chosen.get("trailers") or 0) >= (chosen.get("code_trailers") or 0):
        # the one trailer count that still steers the choice: a draft code minimum
        out.append(f"it has {a.get('trailers') or 0} of the {code} truck spaces the draft code asks for, and a layout that "
                   "meets that minimum is drawn first")
    if a.get("drains") is False and chosen.get("drains", True):
        out.append("its pond stands above the pad it would drain, and a layout that drains by gravity is drawn first")
    if a.get("drivable") is False and chosen.get("drivable", True):
        out.append("no tractor-trailer can drive from its driveway to its truck court as laid out, and a layout with a way in "
                   "is drawn first")
    if mine["trailer"] - theirs["trailer"] >= MARK_MIN_SF:
        target, got = a.get("trailers_target") or 0, a.get("trailers") or 0
        out.append((f"it has none of its {target} target trailer stalls" if not got
                    else f"it has {got} of its {target} target trailer stalls")
                   + f" (the ranking counts each missing stall as {w['trailer_sf']:,} SF)")
    if (a.get("steep_pct") or 0) > (chosen.get("steep_pct") or 0) and mine["steep"] - theirs["steep"] >= MARK_MIN_SF:
        out.append(f"more of its pad is on steep ground ({a.get('steep_pct') or 0:.0f}% against "
                   f"{chosen.get('steep_pct') or 0:.0f}%)")
    if (a.get("relief_ft") or 0) > (chosen.get("relief_ft") or 0) and mine["relief"] - theirs["relief"] >= MARK_MIN_SF:
        out.append(f"its pad has more relief ({a.get('relief_ft') or 0:.0f} ft against {chosen.get('relief_ft') or 0:.0f} ft)")
    return out


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _fail_text(a: dict[str, Any]) -> str:
    names = [str(x).replace("_", " ").lower() for x in a.get("blocking") or []]
    if names:
        return ", ".join(names)
    return "no route" if a.get("route_problem") else "layout check failed"


def selection(alternatives: list[dict[str, Any]], weights: dict[str, Any],
              pond_limited: Iterable[dict[str, Any]] = (), underground: bool | None = False) -> dict[str, Any]:
    """Why the plan drawn (the first alternative) ranks first; the largest
    layout that passed its checks when it is larger; the largest that
    meets its trailer target when the plan drawn does not; and what the
    surface pond costs.

    weights: the ranking's own numbers (``siteplan.RANKING``), so a trade
    is stated in the ranking's terms. pond_limited: layouts that passed
    every layout check but the surface pond, at the length first tried.
    underground: whether the market uses underground detention (an
    operator decision); it decides what is said about the pond, nothing
    else. None (the prototype behaviour) says nothing about the market
    and reports every larger pond-limited layout."""
    alts = [a for a in alternatives if isinstance(a, dict)]
    out: dict[str, Any] = {"basis": "", "upside": None, "with_trailers": None, "without_pond": None, "larger_failed": 0}
    if not alts:
        return out
    chosen = alts[0]
    larger_ok = [a for a in alts[1:] if a.get("geometry_ok") and (a.get("sf") or 0) > (chosen.get("sf") or 0)]
    larger_bad = [a for a in alts[1:] if not a.get("geometry_ok") and (a.get("sf") or 0) > (chosen.get("sf") or 0)]
    out["larger_failed"] = len(larger_bad)
    if not chosen.get("geometry_ok"):
        out["basis"] = "No layout passed its own checks; the plan drawn is the one that ranks best of those tried."
        return out
    parts = []
    if larger_ok:
        up = max(larger_ok, key=lambda a: a.get("sf") or 0)
        gain = (up.get("sf") or 0) - (chosen.get("sf") or 0)
        pct = 100 * gain / chosen["sf"] if chosen.get("sf") else 0.0
        why = _reasons(up, chosen, weights)
        trailer = any("trailer stalls" in r for r in why)
        out["upside"] = {"variant": up.get("variant"), "sf": up.get("sf"), "gain_sf": gain, "gain_pct": round(pct),
                         "reasons": why}
        text = (f"A larger layout also passed its checks: {up.get('variant')}, {up.get('sf'):,} SF "
                f"({gain:+,} SF, {pct:+.0f}%). It ranks below the plan drawn")
        if why:
            text += " because " + _listed(why)
        else:
            text += " on the ranking's marks for steep ground and relief, by a small margin" if not (
                (up.get("code_trailers") or 0) > (up.get("trailers") or 0)) else " for its truck spaces"
        text += "."
        if trailer:
            text += " If the tenant does not need that trailer storage, it is the higher-yield scheme."
        parts.append(text)
    else:
        parts.append("The plan drawn is the largest layout tried that passed its own checks.")
    short = (chosen.get("trailers_target") or 0) - (chosen.get("trailers") or 0)
    if short > 0:
        # The ranking gives no floor area for trailer stalls (an operator
        # decision), so say what meeting the target would cost.
        full = [a for a in alts[1:] if a.get("geometry_ok") and (a.get("trailers") or 0) >= (a.get("trailers_target") or 0)
                # not one whose pond cannot drain, when the plan drawn drains
                and (a.get("drains", True) or not chosen.get("drains", True))]
        text = f"It has {chosen.get('trailers') or 0} of its {chosen.get('trailers_target')} target trailer stalls"
        if weights.get("trailer_sf"):
            text += "."
        elif chosen.get("code_trailers"):
            text += (f"; the ranking does not trade floor area for trailer stalls beyond the draft code minimum of truck "
                     f"spaces ({chosen['code_trailers']} here).")
        else:
            text += "; the ranking does not trade floor area for trailer stalls."
        if full:
            wt = max(full, key=lambda a: a.get("sf") or 0)
            loss = (wt.get("sf") or 0) - (chosen.get("sf") or 0)
            out["with_trailers"] = {"variant": wt.get("variant"), "sf": wt.get("sf"), "gain_sf": loss,
                                    "trailers": wt.get("trailers")}
            text += (f" The largest layout tried that meets its trailer target is {wt.get('variant')}, {wt.get('sf'):,} SF "
                     f"({loss:+,} SF), with {wt.get('trailers')} stalls.")
        else:
            text += " No layout tried meets its trailer target."
        parts.append(text)
    if larger_bad:
        counts: dict[str, int] = {}
        for a in larger_bad:
            counts[_fail_text(a)] = counts.get(_fail_text(a), 0) + 1
        listed = "; ".join(f"{k} ({n})" for k, n in sorted(counts.items(), key=lambda t: (-t[1], t[0])))
        parts.append(f"{len(larger_bad)} larger layout{'s' if len(larger_bad) != 1 else ''} failed: {listed}.")
    best_ok = max([chosen.get("sf") or 0] + [a.get("sf") or 0 for a in larger_ok])
    # worth a line only when it is a real step up (5% of the plan drawn)
    floor = POND_GAIN_MIN if underground is not None else 1.0
    no_pond = [a for a in pond_limited if isinstance(a, dict)
               and (a.get("sf") or 0) > best_ok and (a.get("sf") or 0) >= floor * (chosen.get("sf") or 0)]
    if no_pond:
        np_ = max(no_pond, key=lambda a: a.get("sf") or 0)
        gain = np_["sf"] - (chosen.get("sf") or 0)
        pct = 100 * gain / chosen["sf"] if chosen.get("sf") else 0.0
        out["without_pond"] = {"variant": np_.get("variant"), "sf": np_["sf"], "gain_sf": gain, "gain_pct": round(pct),
                               "detention_acft": np_.get("detention_acft"), "trailers": np_.get("trailers"),
                               "trailers_target": np_.get("trailers_target"), "underground": underground}
        np_short = (np_.get("trailers_target") or 0) - (np_.get("trailers") or 0)
        text = (f"The surface pond is what holds the building: {np_.get('variant')} at {np_['sf']:,} SF ({gain:+,} SF, "
                f"{pct:+.0f}%) passed every layout check but the pond"
                + (f", with {np_.get('trailers') or 0} of its {np_.get('trailers_target')} target trailer stalls" if np_short > 0 else "")
                + ".")
        if underground is None:
            text += f" Its {np_.get('detention_acft')} ac-ft would have to go underground or off site."
        elif underground:
            text += (f" Its {np_.get('detention_acft')} ac-ft would have to go underground or off site: price that against "
                     "the floor area.")
        else:
            text += (" Underground detention is not used in this market (operator decision), so the plan drawn stands; "
                     "a pond off site would be the only way to that floor area.")
        parts.append(text)
    out["basis"] = " ".join(parts)
    return out


# ---------------------------------------------------------------------------
# The assessment (from a record alone, so it also reads a kept plan)
# ---------------------------------------------------------------------------
def _sev(rec: dict[str, Any], level: int) -> list[dict[str, str]]:
    flags = rec.get("flags") if isinstance(rec.get("flags"), dict) else {}
    got = flags.get(level, flags.get(str(level))) or []
    return [f for f in got if isinstance(f, dict) and f.get("flag")]


def status_of(rec: dict[str, Any]) -> str:
    """One status for a plan record, from its own verdict fields."""
    first = {f["flag"] for f in _sev(rec, 1)}
    if rec.get("fits") is False:
        return "not_planned" if "PLAN_BUDGET_EXCEEDED" in first else "no_fit"
    feasible = rec.get("feasible")
    if feasible is None or "PLAN_BUDGET_EXCEEDED" in first:
        # an input was not read, or the search was cut short: not a result
        return "incomplete"
    if rec.get("hypothetical"):
        return "hypothetical"
    if feasible is False:
        return "blocked"
    if rec.get("gated"):
        return "gated"
    site = [f for f in _sev(rec, 2) if f["flag"] not in STANDING_FLAGS]
    return "fits_with_conditions" if site else "fits"


def conditions(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every flag of a record as a condition: what it is, whose work it
    is, when it is dealt with, what to do and what closes it. Blocking
    flags first, then by stage, then by severity."""
    out = []
    # The record's own verdict decides what blocks: under the prototype
    # behaviour a severity-1 flag does not always make a plan infeasible,
    # and a plan the record counts has no blocking condition.
    counted = rec.get("feasible") is True
    market = (rec.get("operator_decisions") or {}).get("underground_detention")
    for level in (1, 2, 3):
        for f in _sev(rec, level):
            name = f["flag"]
            ws, stage, action, closes = playbook_for(name, level, market)
            if level == 1:
                kind = "gate" if name in GATE_FLAGS else ("site" if counted else "blocking")
            else:
                kind = "standing" if name in STANDING_FLAGS else ("site" if level == 2 else "information")
            out.append({"flag": name, "severity": level, "kind": kind, "workstream": ws, "stage": stage,
                        "stage_label": STAGE_LABEL[stage], "finding": f.get("note") or "", "action": action,
                        "closes_with": closes})
    kind_order = {"blocking": 0, "gate": 1, "site": 2, "standing": 3, "information": 4}
    out.sort(key=lambda c: (kind_order[c["kind"]], STAGE_ORDER[c["stage"]], c["severity"]))
    for n, c in enumerate(out, 1):
        c["n"] = n
    return out


NEXT_STEPS_MAX = 6


def next_steps(conds: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    """The first things to do, in order: what blocks the plan, the gate,
    then the rest by stage (desk checks, before an offer, due diligence).
    One line per distinct action."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    first = [c for c in conds if c["kind"] in ("blocking", "gate")]
    rest = [c for c in conds if c["kind"] == "site" or (c["kind"] == "standing" and c["stage"] == "desk")]
    # The cheap checks first: a desk check that kills the site saves the
    # survey. Within a stage, the site's own conditions before the standing ones.
    rest.sort(key=lambda c: (STAGE_ORDER[c["stage"]], c["kind"] != "site", c["severity"]))
    pool = first + rest
    if status in ("incomplete", "not_planned"):
        # Nothing else is worth doing until the plan is a result.
        pool = [c for c in pool if c["flag"] in RERUN_FLAGS] or pool
    for c in pool:
        if c["action"] in seen:
            continue
        seen.add(c["action"])
        out.append({"n": len(out) + 1, "stage": c["stage"], "stage_label": c["stage_label"], "workstream": c["workstream"],
                    "action": c["action"], "closes_with": c["closes_with"], "flags": [c["flag"]]})
        if len(out) >= NEXT_STEPS_MAX:
            break
    for c in pool:
        for step in out:
            if step["action"] == c["action"] and c["flag"] not in step["flags"]:
                step["flags"].append(c["flag"])
    return out


NOTE_MIN_AC = 0.05     # a loss smaller than this is not named (slivers)


def yield_notes(rec: dict[str, Any], comp_band: tuple[int, int]) -> dict[str, Any]:
    """Where the yield stands against the comp band, and what holds it."""
    cov = rec.get("coverage") or {}
    sd = rec.get("site_data") or {}
    land = sd.get("land") or {}
    sfa = cov.get("sf_per_gross_ac")
    lo, hi = comp_band
    position = None if sfa is None else ("below" if sfa < lo else "above" if sfa > hi else "within")
    notes = []
    gross, net = land.get("gross_ac"), land.get("net_ac")
    drawn = rec.get("fits") is not False
    if gross and net is not None and gross - net >= 0.1 * gross:
        bits = []
        if (land.get("setbacks_buffers_ac") or 0) >= NOTE_MIN_AC:
            bits.append(f"draft setbacks and buffers {land['setbacks_buffers_ac']:.1f} ac")
        if (land.get("constraints_ac") or 0) >= NOTE_MIN_AC:
            bits.append(f"mapped constraints and any test cut {land['constraints_ac']:.1f} ac")
        notes.append(f"{gross - net:.1f} ac ({100 * (gross - net) / gross:.0f}%) of the site is outside the paving envelope"
                     + (": " + ", ".join(bits) if bits else "") + ".")
    unused, largest = land.get("unused_net_ac") or 0.0, land.get("largest_unused_ac") or 0.0
    if drawn and net and (unused >= 1.0 or unused >= 0.15 * net):
        notes.append(f"{unused:.1f} ac inside the paving envelope is left unused ({100 * unused / net:.0f}% of it); the largest "
                     f"piece is {largest:.1f} ac.")
    gr = rec.get("grading") or {}
    if gr.get("max_cut_ft") is not None and gr.get("max_fill_ft") is not None and (gr.get("relief_ft") or 0) > 10:
        notes.append(f"One level pad needs up to {gr['max_cut_ft']:.0f} ft of cut and {gr['max_fill_ft']:.0f} ft of fill.")
    # What another layout would add is said only of a plan that is a
    # result and has road access.
    if rec.get("feasible") is not None and not rec.get("hypothetical"):
        up = (rec.get("selection") or {}).get("upside")
        if up:
            why = [r for r in up.get("reasons") or [] if r.startswith("its pond stands above")]
            notes.append(f"A {up['sf']:,} SF layout ({up['gain_sf']:+,} SF) passed its checks but ranks lower"
                         + (": " + why[0] + "." if why else "."))
        np_ = (rec.get("selection") or {}).get("without_pond")
        if np_:
            market = {True: " Price underground or off-site detention against that floor area.",
                      False: " Underground detention is not used in this market, so that is not on offer."}
            notes.append(f"The surface pond sets the size: without it {np_['sf']:,} SF ({np_['gain_sf']:+,} SF) passed every "
                         "other layout check." + market.get(np_.get("underground"), ""))
        wt = (rec.get("selection") or {}).get("with_trailers")
        if wt:
            notes.append(f"Meeting the trailer target would cost floor area: {wt['sf']:,} SF ({wt['gain_sf']:+,} SF) is the "
                         "largest layout tried that does.")
    return {"sf_per_gross_ac": sfa, "comp_band": [lo, hi], "position": position, "notes": notes}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def headline(rec: dict[str, Any], status: str, conds: list[dict[str, Any]]) -> str:
    """One or two sentences: what fits, at what yield, and what stands in
    the way."""
    b = rec.get("building") or {}
    cov = rec.get("coverage") or {}
    n_block = sum(c["kind"] == "blocking" for c in conds)
    n_site = sum(c["kind"] == "site" for c in conds)
    first_block = next((c for c in conds if c["kind"] == "blocking"), None)
    if status == "no_fit":
        unread = any(f["flag"] in RERUN_FLAGS for f in _sev(rec, 1))
        return ("No tested building fits the paving envelope with its truck court, parking and pond."
                + (" An input was not read, which can only make the envelope larger, so the no-fit stands." if unread else ""))
    if status == "not_planned":
        return "The search ran out of time before a plan was finished. This says nothing about fit."
    drawn = rec.get("total_sf_if_access") or b.get("sf") or 0
    second = rec.get("second_building")
    what = (f"{drawn:,} SF in two buildings" if second else
            f"{drawn:,} SF {'cross-dock' if 'cross' in str(rec.get('configuration')) else 'rear-load'} building "
            f"({int(b.get('L_ft') or 0)} x {int(b.get('D_ft') or 0)} ft)")
    where = f"on {cov.get('gross_ac')} ac: {cov.get('sf_per_gross_ac') or 0:,} SF per gross acre, FAR {cov.get('gross_far')}"
    if status == "incomplete":
        return (f"{what} {where}, but an input was not read or the search was cut short, so this is not a result. Run it "
                "again before using any number here.")
    if status == "hypothetical":
        return f"{what} would fit {where}, but the site has no road frontage. No floor area is counted."
    if status == "blocked":
        if not n_block:
            # the prototype behaviour: not feasible by the layout's own check, with no severity-1 flag to name
            return f"{what} was drawn {where}, but the layout failed its own checks (see the flags). No floor area is counted."
        return (f"{what} was drawn {where}, but {_plural(n_block, 'blocking item')} stand"
                f"{'s' if n_block == 1 else ''}, first: {first_block['finding']}. No floor area is counted.")
    if status == "gated":
        counted = bool(rec.get("total_sf"))
        return (f"{what} fits {where}. The use needs a permit ({', '.join(rec.get('gated') or [])}), so the floor area is "
                f"{'counted, subject to the permit' if counted else 'not counted until the permit is in hand'}.")
    tail = (f"{_plural(n_site, 'site condition')} to clear, plus the standard checks." if n_site
            else "Only the standard checks remain.")
    return f"{what} fits {where}. {tail}"


def assessment(rec: dict[str, Any], comp_band: tuple[int, int] = (10500, 15400)) -> dict[str, Any]:
    """The assessment of one plan record (a drawn plan or a no-fit)."""
    status = status_of(rec)
    conds = conditions(rec)
    return {
        "status": status, "label": STATUS_LABELS[status],
        "counted": status in COUNTED_STATUSES or (status == "gated" and bool(rec.get("total_sf"))),
        "headline": headline(rec, status, conds),
        "counts": {k: sum(c["kind"] == k for c in conds) for k in ("blocking", "gate", "site", "standing", "information")},
        "yield": yield_notes(rec, comp_band),
        "next_steps": next_steps(conds, status),
        "conditions": conds,
        "basis": ("status from the plan's own feasibility; conditions are the plan's flags with the usual next step "
                  "for each; nothing here was checked against a document"),
    }


# ---------------------------------------------------------------------------
# The pipeline table (one row per planned site)
# ---------------------------------------------------------------------------
PIPELINE_COLUMNS = (
    "site", "parcel_ids", "rules", "status", "counted_sf", "drawn_sf", "layout", "length_ft", "depth_ft", "clear_ft",
    "dock_doors", "trailer_stalls", "trailer_target", "car_stalls", "gross_ac", "net_ac", "sf_per_gross_ac", "far",
    "impervious_pct", "detention_acft", "ponds", "relief_ft", "max_cut_ft", "max_fill_ft", "earthwork_cy",
    "unused_net_ac", "larger_passing_sf", "meets_trailer_target_sf", "without_pond_sf", "blocking", "site_conditions",
    "first_step")


def pipeline_row(rec: dict[str, Any]) -> dict[str, Any]:
    """One site as a row of the pipeline table. Parcel ids and numbers
    only: no title or address."""
    a = rec.get("assessment") or assessment(rec)
    b, tr, ca = rec.get("building") or {}, rec.get("trailers") or {}, rec.get("cars") or {}
    cov, de, gr = rec.get("coverage") or {}, rec.get("detention") or {}, rec.get("grading") or {}
    land = (rec.get("site_data") or {}).get("land") or {}
    conds = a.get("conditions") or []
    steps = a.get("next_steps") or []
    sel = rec.get("selection") or {}
    return {
        "site": rec.get("rank"), "parcel_ids": " | ".join(str(p) for p in rec.get("parcel_ids") or []),
        "rules": rec.get("jurisdiction_rules"), "status": a.get("status"),
        "counted_sf": rec.get("total_sf") or 0, "drawn_sf": rec.get("total_sf_if_access") or 0,
        "layout": rec.get("configuration") or "", "length_ft": b.get("L_ft"), "depth_ft": b.get("D_ft"),
        "clear_ft": b.get("clear_height_ft"), "dock_doors": rec.get("doors"),
        "trailer_stalls": tr.get("total"), "trailer_target": tr.get("target"), "car_stalls": ca.get("provided"),
        "gross_ac": cov.get("gross_ac", (rec.get("inputs") or {}).get("parcel_ac_map")), "net_ac": land.get("net_ac"),
        "sf_per_gross_ac": cov.get("sf_per_gross_ac"), "far": cov.get("gross_far"),
        "impervious_pct": cov.get("impervious_pct"), "detention_acft": de.get("storage_acft"), "ponds": de.get("ponds"),
        "relief_ft": gr.get("relief_ft"), "max_cut_ft": gr.get("max_cut_ft"), "max_fill_ft": gr.get("max_fill_ft"),
        "earthwork_cy": gr.get("cut_fill_cy"), "unused_net_ac": land.get("unused_net_ac"),
        # a larger layout that passed its checks but ranks lower, and the
        # largest that passed every check but the surface pond
        "larger_passing_sf": (sel.get("upside") or {}).get("sf"),
        "meets_trailer_target_sf": (sel.get("with_trailers") or {}).get("sf"),
        "without_pond_sf": (sel.get("without_pond") or {}).get("sf"),
        "blocking": " ".join(c["flag"] for c in conds if c["kind"] in ("blocking", "gate")),
        "site_conditions": " ".join(c["flag"] for c in conds if c["kind"] == "site"),
        "first_step": steps[0]["action"] if steps else "",
    }


def pipeline_csv(recs: Iterable[dict[str, Any]], cell: Callable[[Any], Any] = lambda v: v) -> str:
    """The pipeline table as CSV text. ``cell``: the caller's guard for a
    text cell a spreadsheet would read as a formula (``viewer.csv_cell``)."""
    lines: list[str] = []

    class _Sink:
        def write(self, text: str) -> None:
            lines.append(text)

    w = csv.writer(_Sink(), lineterminator="\n")
    w.writerow(PIPELINE_COLUMNS)
    for rec in recs:
        row = pipeline_row(rec)
        w.writerow([cell("" if row[c] is None else row[c]) for c in PIPELINE_COLUMNS])
    return "".join(lines)
