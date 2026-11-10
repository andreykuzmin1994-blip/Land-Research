"""F10: road frontage from GDOT road centrelines (reviews/34_screener_quick_wins).

For each ranked parcel, after scoring, flags, the sort, the F6 fit, the F7
pond and the F9 soils, one ``identify`` request to GDOT's public Functional
Class map service asks for every GDOT road centreline in the parcel's
bounding box (padded about 500 ft) on the seven functional-class layers:

    0 interstate, 1 freeway/expressway, 2 principal arterial,
    3 minor arterial, 4 major collector, 5 minor collector, 6 local road

(GDOT's statewide LRS road network: state routes and public county / city
roads.) The parcel line within ``BUFFER_FT`` (50 ft) of a centreline counts
as frontage on that road, measured in the parcel's local plane:

- ``frontage_ft``: parcel line within 50 ft of any road that is not an
  interstate or freeway/expressway (access controlled: a driveway is
  unlikely).
- ``frontage_road``: class and route of the road with the longest frontage
  ("minor arterial, SR 14"; a county or city road shows its GDOT route id,
  since the layer carries no street names), or ``none within 50 ft``, or
  ``not checked: <why>``.
- ``frontage_limited_ft``: parcel line within 50 ft of an interstate or
  freeway (frontage that gives no access).

Three states: a number and a road; ``0`` and ``none within 50 ft``; empty
numbers and ``not checked: <why>``. Fail soft: an HTTP error, an answer
without a results list, a result from another layer, without polyline
paths or not in WGS84, or an answer at the server's 1,000-result cap means
"not checked", never a made-up value; bad answers are never cached
(arcgis.request_json's check). After 5 service failures in a row the rest
of the run is not asked.

Curb cuts are not checked: a driveway permit (GDOT for state routes, the
city or county for local roads) stays a manual next step in the brief.

Live-only, like earthwork, the fit, the pond and the soils: ``run.main``
calls ``attach_frontage`` only after the sort, and ``rank.py`` /
``scoring.py`` / ``screens.py`` never see these keys
(tests/test_frontage.py spies on every call). The roads are NOT a map
layer in ``layers.LAYERS``: build_eval copies every layer there, and this
fact stays outside the map checks and the evaluation.

Access (checked 2026-11-10): ``maps.itos.uga.edu/robots.txt`` answers HTTP
404 (no crawl rule applies); the service is GDOT's own published
Functional Class map (ArcGIS Online items by GDOT, copyright "Georgia
Department of Transportation, ITOS-CVIOG-UGA"), public, no sign-in, Query
and Data capabilities on, and no posted terms forbid automated use. The
screener sends its own User-Agent, at most 3 parallel requests, one request
per parcel, cached like every other answer.
"""

from __future__ import annotations

import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from . import arcgis
from .geo import FT_PER_M, LocalProjection

SERVICE_URL = "https://maps.itos.uga.edu/arcgis/rest/services/GDOT/GDOT_FunctionalClass/MapServer"
IDENTIFY_URL = f"{SERVICE_URL}/identify"
CLASSES = {0: "interstate", 1: "freeway/expressway", 2: "principal arterial",
           3: "minor arterial", 4: "major collector", 5: "minor collector", 6: "local road"}
LIMITED_ACCESS = frozenset({0, 1})
BUFFER_FT = 50.0
PAD_M = 150.0                # bounding-box pad for the request (about 500 ft)
RESULT_CAP = 1000            # the service's maxRecordCount
MAX_WORKERS = 3              # below the shared 6: a second, smaller service
TRIES = 2
STOP_AFTER_FAILURES = 5

# Name stem of every F10 key (the static and spy guards import it).
KEY_STEMS = ("frontage_",)
COLUMNS = ("frontage_ft", "frontage_road", "frontage_limited_ft")
DETAIL_KEY = "_frontage_detail"
LINE_KEY = "_frontage_line"   # the frontage part of the parcel line, WGS84 (plan)

CHECKED, NONE, NOT_CHECKED = "checked", "none", "not checked"
NONE_LABEL = f"none within {BUFFER_FT:.0f} ft"
NOT_CHECKED_PREFIX = "not checked: "
SERVICE_FAILED = "road service failed"

# GDOT route id (16 characters): function type, county (000 for most state
# routes), system code (1 state route, 2 public road), 6-digit route number,
# 2-character suffix (00, or AL / CO / SO ...), direction.
_ROUTE_ID = re.compile(r"^\d{5}\d{6}[A-Z0-9]{2}[A-Z]{3}$")
_SAFE = re.compile(r"[^A-Z0-9]")

CAVEAT = (f"Measured as the parcel line within {BUFFER_FT:.0f} ft of a GDOT road centreline, from the county "
          "parcel map: up to about 50 ft at each end of a frontage can be a side line near the "
          "corner, a wide right-of-way or a misaligned map can hide real frontage, and a road "
          "can be close without legal access")
CURB_CUT = ("Curb cut / driveway access not checked: a manual next step (GDOT for state "
            "routes, the city or county for local roads)")


@dataclass
class Frontage:
    state: str                                   # CHECKED / NONE / NOT_CHECKED
    reason: str = ""
    feet: int | None = None                      # access frontage (not limited access)
    limited_feet: int | None = None
    road: str | None = None                      # label of the longest-frontage road
    roads: list[tuple[str, int]] = field(default_factory=list)   # (label, ft), longest first
    nearest_ft: int | None = None                # nearest access road when NONE
    line: BaseGeometry | None = None             # access frontage, WGS84

    def fields(self) -> dict[str, Any]:
        """The report fields (attached after the sort, never before)."""
        if self.state == NOT_CHECKED:
            return {"frontage_ft": None, "frontage_road": NOT_CHECKED_PREFIX + self.reason,
                    "frontage_limited_ft": None, DETAIL_KEY: self, LINE_KEY: None}
        return {"frontage_ft": self.feet,
                "frontage_road": self.road if self.state == CHECKED else NONE_LABEL,
                "frontage_limited_ft": self.limited_feet, DETAIL_KEY: self,
                LINE_KEY: self.line}


def not_checked(reason: str) -> Frontage:
    return Frontage(NOT_CHECKED, reason=reason)


def road_label(layer_id: int, route_id: Any) -> str:
    """"minor arterial, SR 14" or "local road, GDOT route 1121200054600".

    The 3-letter direction (INC / DEC) is dropped, so the two carriageways
    of a divided road count as one road."""
    cls = CLASSES[layer_id]
    rid = _SAFE.sub("", str(route_id or "").upper())[:20]
    if not rid:
        return f"{cls}, unnamed route"
    if _ROUTE_ID.match(rid) and rid[4] == "1":
        suffix = rid[11:13]
        return f"{cls}, SR {int(rid[5:11])}" + (f" {suffix}" if suffix != "00" else "")
    if _ROUTE_ID.match(rid):
        rid = rid[:13]
    return f"{cls}, GDOT route {rid}"


def _answer_problem(data: dict[str, Any]) -> str | None:
    results = data.get("results")
    if not isinstance(results, list):
        return "no results list in the answer"
    if len(results) >= RESULT_CAP:
        return f"the answer reached the {RESULT_CAP:,}-result cap"
    for r in results:
        if not isinstance(r, dict) or r.get("layerId") not in CLASSES \
                or isinstance(r.get("layerId"), bool):
            return "a result from an unexpected layer"
        g = r.get("geometry")
        if not isinstance(g, dict) or not isinstance(g.get("paths"), list):
            return "a road without polyline geometry"
        sr = g.get("spatialReference") if isinstance(g.get("spatialReference"), dict) else {}
        if 4326 not in (sr.get("wkid"), sr.get("latestWkid")):
            return "the roads did not come back in WGS84"
        try:
            ok = all(len(path) >= 2 and all(len(pt) >= 2 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in pt[:2])
                for pt in path) for path in g["paths"])
        except TypeError:
            ok = False
        if not ok or not g["paths"]:
            return "a malformed road geometry"
        if not isinstance(r.get("attributes", {}), dict):
            return "malformed road attributes"
    return None


def _params(geometry: BaseGeometry) -> dict[str, Any]:
    proj = LocalProjection.around(geometry)
    xmin, ymin, xmax, ymax = geometry.bounds
    dx, dy = PAD_M / proj.kx, PAD_M / proj.ky
    env = (round(xmin - dx, 7), round(ymin - dy, 7), round(xmax + dx, 7), round(ymax + dy, 7))
    return {
        "geometry": ('{"xmin":%r,"ymin":%r,"xmax":%r,"ymax":%r,"spatialReference":{"wkid":4326}}'
                     % env),
        "geometryType": "esriGeometryEnvelope",
        "sr": 4326,
        "layers": "all:" + ",".join(map(str, sorted(CLASSES))),
        "tolerance": 0,
        "mapExtent": ",".join(repr(v) for v in env),
        "imageDisplay": "400,400,96",
        "returnGeometry": "true",
        "returnFieldName": "true",
        "geometryPrecision": 7,
    }


def _inverse(proj: LocalProjection):
    def back(coords):
        out = coords.copy()
        out[:, 0] = coords[:, 0] / proj.kx + proj.lon0
        out[:, 1] = coords[:, 1] / proj.ky + proj.lat0
        return out
    return back


def measure(geometry: BaseGeometry, results: list[dict[str, Any]]) -> Frontage:
    """Frontage from validated identify results (see _answer_problem)."""
    proj = LocalProjection.around(geometry)
    edge = proj(geometry).boundary
    buf_m = BUFFER_FT / FT_PER_M
    roads: dict[tuple[int, str], list[LineString]] = {}
    for r in results:
        lines = [LineString([pt[:2] for pt in path]) for path in r["geometry"]["paths"]]
        key = (r["layerId"], road_label(r["layerId"], (r.get("attributes") or {}).get("route_id")))
        roads.setdefault(key, []).extend(proj(ln) for ln in lines)
    if not roads:
        return Frontage(NONE, feet=0, limited_feet=0, reason="no GDOT road within about 500 ft")
    zones = {key: unary_union(lines).buffer(buf_m) for key, lines in roads.items()}
    per_road = []
    for (layer, label), zone in zones.items():
        if layer in LIMITED_ACCESS:
            continue
        ft = round(edge.intersection(zone).length * FT_PER_M)
        if ft >= 1:
            per_road.append((label, ft))
    per_road.sort(key=lambda kv: (-kv[1], kv[0]))
    access = [z for (layer, _), z in zones.items() if layer not in LIMITED_ACCESS]
    limited = [z for (layer, _), z in zones.items() if layer in LIMITED_ACCESS]
    access_line = edge.intersection(unary_union(access)) if access else None
    feet = round(access_line.length * FT_PER_M) if access_line is not None else 0
    limited_ft = round(edge.intersection(unary_union(limited)).length * FT_PER_M) if limited else 0
    if feet < 1 or not per_road:
        near = None
        if access:
            near_m = min(edge.distance(unary_union(roads[k])) for k in roads
                         if k[0] not in LIMITED_ACCESS)
            near = round(near_m * FT_PER_M)
        return Frontage(NONE, feet=0, limited_feet=limited_ft, nearest_ft=near)
    line = shapely.transform(access_line, _inverse(proj))
    return Frontage(CHECKED, feet=feet, limited_feet=limited_ft, road=per_road[0][0],
                    roads=per_road, line=line)


def check_parcel(geometry: BaseGeometry | None) -> Frontage:
    """One parcel, one identify request. Never raises for a service failure."""
    if not isinstance(geometry, (Polygon, MultiPolygon)) or geometry.is_empty:
        return not_checked("no usable parcel shape")
    try:
        data = arcgis.request_json(IDENTIFY_URL, _params(geometry), retries=TRIES,
                                   timeout=60, check=_answer_problem)
    except arcgis.BadAnswer as exc:
        return not_checked(f"no usable road answer ({_short(exc)})")
    except arcgis.ArcGISError as exc:
        return not_checked(f"{SERVICE_FAILED} ({_short(exc)})")
    try:
        return measure(geometry, data["results"])
    except (GEOSException, ValueError) as exc:     # never crash the run on odd geometry
        return not_checked(f"could not measure the road answer ({str(exc)[:80]})")


def _short(exc: Exception) -> str:
    return str(exc).replace(IDENTIFY_URL + ": ", "").replace(IDENTIFY_URL, "GDOT")[:120]


def check_all(geometries: list[BaseGeometry | None], workers: int) -> list[Frontage]:
    """Frontage for each geometry, in input order. After STOP_AFTER_FAILURES
    service failures in a row the rest are not asked for."""
    lock = threading.Lock()
    streak = [0]

    def work(g):
        with lock:
            if streak[0] >= STOP_AFTER_FAILURES:
                return not_checked(f"road service skipped after {STOP_AFTER_FAILURES} "
                                   "failures in a row")
        f = check_parcel(g)
        failed = f.state == NOT_CHECKED and f.reason.startswith(SERVICE_FAILED)
        with lock:
            streak[0] = streak[0] + 1 if failed else 0
        return f

    with ThreadPoolExecutor(max_workers=max(1, min(workers, MAX_WORKERS))) as pool:
        return list(pool.map(work, geometries))


def settings(cfg: dict[str, Any]) -> tuple[bool, int, int]:
    """[frontage] enabled (default true), max_parcels (default 0 = all; the
    top N by rank are checked) and workers (default and ceiling 3)."""
    t = cfg.get("frontage", {}) or {}
    enabled = t.get("enabled", True)
    cap = t.get("max_parcels", 0)
    workers = t.get("workers", MAX_WORKERS)
    if not isinstance(enabled, bool):
        raise ValueError("[frontage] enabled must be true or false")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        raise ValueError("[frontage] max_parcels must be a whole number, 0 or more")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"[frontage] workers must be a whole number from 1 to {MAX_WORKERS}")
    return enabled, cap, workers


def plan_text(f: Frontage) -> str:
    """The site-plan legend line."""
    if f.state == CHECKED:
        return f"Road frontage about {f.feet:,} ft (longest: {f.road}); curb cut not checked"
    if f.state == NONE:
        return f"Road frontage: {NONE_LABEL} of a GDOT road centreline"
    return f"Road frontage: not checked ({f.reason})"


def brief_line(p: dict[str, Any]) -> str:
    """The brief's frontage line (hand-written wording only)."""
    f: Frontage | None = p.get(DETAIL_KEY)
    if f is None:
        return "- Road frontage: not checked (frontage off)"
    if f.state == NOT_CHECKED:
        return f"- Road frontage: not checked ({f.reason}). {CURB_CUT}"
    limited = (f"; {f.limited_feet:,} ft along an interstate or freeway/expressway (access "
               "controlled: a driveway is unlikely)" if f.limited_feet else "")
    if f.state == NONE:
        if f.reason:
            where = f.reason
        elif f.nearest_ft is not None:
            where = f"nearest non-freeway GDOT road about {f.nearest_ft:,} ft from the parcel line"
        else:
            where = "only interstates or freeways nearby"
        return (f"- Road frontage (GDOT road centrelines): {NONE_LABEL} ({where}){limited}; "
                f"possibly land-locked or on a road GDOT doesn't map (a private drive), check "
                f"access easements. {CAVEAT}. {CURB_CUT}")
    others = ", ".join(f"{label} {ft:,} ft" for label, ft in f.roads[1:4])
    more = f" (+{len(f.roads) - 4} more)" if len(f.roads) > 4 else ""
    return (f"- Road frontage (GDOT road centrelines): about {f.feet:,} ft; longest on "
            f"{f.road} ({f.roads[0][1]:,} ft)" + (f"; also {others}{more}" if others else "")
            + f"{limited}. {CAVEAT}. {CURB_CUT}")
