"""SSURGO soils from USDA NRCS Soil Data Access (F9, reviews/34_screener_quick_wins).

For each ranked parcel, after scoring, flags, the sort, the F6 fit and the
F7 pond, one POST to the SDA tabular service (``post.rest``) asks which
SSURGO map units intersect the parcel shape (WKT, lon/lat, WGS84), clips
them to it (SDA's own ``GetClippedMapunits`` macro), sums each unit's
clipped area on the ellipsoid, and joins ``muaggatt``, the one-row-per-unit
aggregate table SDA keeps for every map unit:

- ``hydgrpdcd``: hydrologic soil group, dominant condition (A, B, C, D,
  A/D, B/D, C/D; the dual groups are "D unless drained").
- ``drclassdcd``: drainage class, dominant condition.
- ``hydclprs``: percent of the map unit's components that are hydric.
- ``engdwobdcd``: "ENG - Dwellings W/O Basements" rating, dominant
  condition (Not / Somewhat / Very limited). It is the closest building
  rating muaggatt carries (slab-on-grade, light structure; flooding, ponding,
  wetness, shrink-swell, slope, depth to rock). "Small Commercial Buildings"
  lives only in the per-component cointerp table and is not asked for.

Report fields (shares are of the parcel's mapped soil area, map-unit level):

- ``soil_hsg``: the hydrologic group covering the most area, or
  "none rated", or "not checked: <why>".
- ``soil_hydric_pct``: area-weighted hydric share; ``soil_poorly_drained_pct``:
  share mapped poorly or very poorly drained; ``soil_dwellings_limited_pct``:
  share rated "Very limited" for dwellings without basements. Each is a
  number, or "none rated" when no intersecting unit carries the rating, or
  empty when not checked.

Fail soft: an HTTP error, an empty ``{}`` answer (what SDA sends when no map
unit matches), a header-only table, a malformed row, an answer at the
100,000-row cap, or mapped soil covering under half (or over 1.5 times) the
parcel shape means "not checked", never a made-up value. Bad answers are
never cached (arcgis.request_json's check).

Live-only, like earthwork, the fit and the pond: ``run.main`` calls
``attach_soils`` only after the sort, and ``rank.py`` / ``scoring.py`` /
``screens.py`` never see these keys (tests/test_soils.py spies on every call).

SDA terms (checked 2026-11-10): robots.txt is absent (HTTP 404, so no
crawl rule applies), and the NRCS help pages publish ``post.rest`` as a
public web service for ad hoc, real-time queries of any area, capped at
100,000 rows per answer, unavailable nightly about 00:00-02:00 Mountain.
The screener sends its own User-Agent, at most 3 parallel requests, one
request per parcel, cached like every other answer.
"""

from __future__ import annotations

import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from . import arcgis
from .geo import LocalProjection

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
ROW_CAP = 100_000            # SDA's documented per-answer row limit
MAX_WORKERS = 3              # below the shared 6: a second, smaller service
TRIES = 2                    # an SDA error rarely clears within seconds
STOP_AFTER_FAILURES = 5      # this many failures in a row: stop asking this run
COVERAGE_BAND = (0.5, 1.5)   # mapped soil area / parcel-shape area

# Name stem of every F9 key (the static and spy guards import it).
KEY_STEMS = ("soil",)
COLUMNS = ("soil_hsg", "soil_hydric_pct", "soil_poorly_drained_pct",
           "soil_dwellings_limited_pct")
DETAIL_KEY = "_soil_detail"

CHECKED, NOT_CHECKED = "checked", "not checked"
NONE_RATED = "none rated"
NOT_CHECKED_PREFIX = "not checked: "
SERVICE_FAILED = "soil service failed"

HEADER = ["mukey", "area_m2", "hydgrpdcd", "drclassdcd", "hydclprs", "engdwobdcd"]
POOR_DRAINAGE = ("Poorly drained", "Very poorly drained")
VERY_LIMITED = "Very limited"
_WKT_OK = re.compile(r"^(MULTI)?POLYGON \(\([-0-9., ()]+\)\)$")

CAVEAT = ("SSURGO is mapped at about 1:12,000 to 1:24,000 and rated by map unit, not "
          "this site: small wet spots, fill and inclusions go unmapped, and it is no "
          "substitute for a geotechnical report or a wetland delineation")

QUERY = """~DeclareGeometry(@aoi)~
select @aoi = geometry::STGeomFromText('{wkt}', 4326).MakeValid()
~DeclareIdGeomTable(@clipped)~
~GetClippedMapunits(@aoi,polygon,geo,@clipped)~
~DeclareIdGeogTable(@clippedGeog)~
~GetGeogFromGeomWgs84(@clipped,@clippedGeog)~
select a.mukey, a.area_m2, ma.hydgrpdcd, ma.drclassdcd, ma.hydclprs, ma.engdwobdcd
from (select id as mukey, sum(geog.STArea()) as area_m2 from @clippedGeog group by id) a
left join muaggatt ma on ma.mukey = a.mukey
order by a.area_m2 desc, a.mukey"""


@dataclass
class Soils:
    state: str                                  # CHECKED / NOT_CHECKED
    reason: str = ""
    hsg: str | None = None                      # dominant group, or NONE_RATED
    hydric_pct: float | str | None = None       # number, NONE_RATED, or None
    poorly_drained_pct: float | str | None = None
    dwellings_limited_pct: float | str | None = None
    hsg_shares: dict[str, float] = field(default_factory=dict)
    unrated_pct: dict[str, float] = field(default_factory=dict)
    units: int = 0
    coverage_pct: float | None = None

    def fields(self) -> dict[str, Any]:
        """The report fields (attached after the sort, never before)."""
        if self.state == NOT_CHECKED:
            return {"soil_hsg": NOT_CHECKED_PREFIX + self.reason, "soil_hydric_pct": None,
                    "soil_poorly_drained_pct": None, "soil_dwellings_limited_pct": None,
                    DETAIL_KEY: self}
        return {"soil_hsg": self.hsg, "soil_hydric_pct": self.hydric_pct,
                "soil_poorly_drained_pct": self.poorly_drained_pct,
                "soil_dwellings_limited_pct": self.dwellings_limited_pct, DETAIL_KEY: self}


def not_checked(reason: str) -> Soils:
    return Soils(NOT_CHECKED, reason=reason)


def parcel_wkt(geometry: BaseGeometry | None) -> str | None:
    """The parcel shape as 2-D WKT in lon/lat order, or None if it isn't an
    areal shape. Only digits, signs, points, commas, spaces and brackets can
    reach the SQL text (checked, not assumed)."""
    if not isinstance(geometry, (Polygon, MultiPolygon)) or geometry.is_empty:
        return None
    wkt = shapely.to_wkt(geometry, rounding_precision=7, output_dimension=2)
    return wkt if _WKT_OK.match(wkt) else None


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _rows_problem(data: dict[str, Any]) -> str | None:
    table = data.get("Table")
    if not isinstance(table, list) or not table:
        return "no map units in the answer"
    if table[0] != HEADER:
        return "unexpected columns in the answer"
    rows = table[1:]
    if not rows:
        return "header-only answer (no map units)"
    if len(rows) >= ROW_CAP:
        return f"the answer reached SDA's {ROW_CAP:,}-row cap"
    for row in rows:
        if not isinstance(row, list) or len(row) != len(HEADER):
            return "a malformed row in the answer"
        area = _num(row[1])
        if not row[0] or area is None or area < 0:
            return "a map unit without a usable area"
        h = row[4]
        if h is not None and (_num(h) is None or not 0 <= _num(h) <= 100):
            return "a hydric percent outside 0-100"
        if any(v is not None and not isinstance(v, str) for v in (row[2], row[3], row[5])):
            return "a malformed rating in the answer"
    return None


def _share(part: float, total: float) -> float:
    return round(100.0 * part / total, 1)


def summarise(rows: list[list[Any]], parcel_m2: float) -> Soils:
    """Area-weighted facts from validated rows (see _rows_problem)."""
    units = [(r[0], float(r[1]), r[2], r[3], _num(r[4]), r[5]) for r in rows]
    total = sum(u[1] for u in units)
    if total <= 0 or parcel_m2 <= 0:
        return not_checked("the soil map units have no area on the parcel")
    coverage = total / parcel_m2
    if not COVERAGE_BAND[0] <= coverage <= COVERAGE_BAND[1]:
        return not_checked(f"the soil map covers {100 * coverage:.0f}% of the parcel shape")
    by_hsg: dict[str, float] = {}
    for _, area, hsg, *_ in units:
        if hsg:
            by_hsg[hsg] = by_hsg.get(hsg, 0.0) + area
    unrated = {}
    if by_hsg:
        dominant = sorted(by_hsg.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    else:
        dominant = NONE_RATED
    unrated["hsg"] = _share(total - sum(by_hsg.values()), total)

    def rated_share(rated: Callable[[tuple], bool], poor: Callable[[tuple], bool], key: str):
        rated_units = [u for u in units if rated(u)]
        unrated[key] = _share(total - sum(u[1] for u in rated_units), total)
        if not rated_units:
            return NONE_RATED
        return _share(sum(u[1] * poor(u) for u in rated_units), total)

    hydric = rated_share(lambda u: u[4] is not None, lambda u: u[4] / 100.0, "hydric")
    drained = rated_share(lambda u: bool(u[3]), lambda u: u[3] in POOR_DRAINAGE, "drainage")
    dwell = rated_share(lambda u: bool(u[5]) and u[5] != "Not rated",
                        lambda u: u[5] == VERY_LIMITED, "dwellings")
    return Soils(CHECKED, hsg=dominant, hydric_pct=hydric, poorly_drained_pct=drained,
                 dwellings_limited_pct=dwell,
                 hsg_shares={k: _share(v, total) for k, v in sorted(by_hsg.items())},
                 unrated_pct=unrated, units=len(units),
                 coverage_pct=round(100.0 * coverage, 1))


def check_parcel(geometry: BaseGeometry | None) -> Soils:
    """One parcel, one SDA request. Never raises for a service failure."""
    wkt = parcel_wkt(geometry)
    if wkt is None:
        return not_checked("no usable parcel shape")
    try:
        data = arcgis.request_json(SDA_URL, {"query": QUERY.format(wkt=wkt),
                                             "format": "JSON+COLUMNNAME"},
                                   retries=TRIES, timeout=60, check=_rows_problem)
    except arcgis.BadAnswer as exc:
        # Well-formed but unusable ({} for no map units, header only, cap).
        return not_checked(f"no usable soil answer ({_short(exc)})")
    except arcgis.ArcGISError as exc:
        return not_checked(f"{SERVICE_FAILED} ({_short(exc)})")
    parcel_m2 = LocalProjection.around(geometry)(geometry).area
    return summarise(data["Table"][1:], parcel_m2)


def _short(exc: Exception) -> str:
    text = str(exc).replace(SDA_URL + ": ", "").replace(SDA_URL, "SDA")
    return text[:120]


def check_all(geometries: list[BaseGeometry | None], workers: int) -> list[Soils]:
    """Soils for each geometry, in input order. After STOP_AFTER_FAILURES
    service failures in a row the rest are not asked for (the service is
    likely down, e.g. its nightly update) and read "not checked"."""
    lock = threading.Lock()
    streak = [0]

    def work(g):
        with lock:
            if streak[0] >= STOP_AFTER_FAILURES:
                return not_checked(f"soil service skipped after {STOP_AFTER_FAILURES} "
                                   "failures in a row")
        s = check_parcel(g)
        failed = s.state == NOT_CHECKED and s.reason.startswith(SERVICE_FAILED)
        with lock:
            streak[0] = streak[0] + 1 if failed else 0
        return s

    with ThreadPoolExecutor(max_workers=max(1, min(workers, MAX_WORKERS))) as pool:
        return list(pool.map(work, geometries))


def settings(cfg: dict[str, Any]) -> tuple[bool, int, int]:
    """[soils] enabled (default true), max_parcels (default 0 = all; the top
    N by rank are checked) and workers (default and ceiling 3)."""
    t = cfg.get("soils", {}) or {}
    enabled = t.get("enabled", True)
    cap = t.get("max_parcels", 0)
    workers = t.get("workers", MAX_WORKERS)
    if not isinstance(enabled, bool):
        raise ValueError("[soils] enabled must be true or false")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        raise ValueError("[soils] max_parcels must be a whole number, 0 or more")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"[soils] workers must be a whole number from 1 to {MAX_WORKERS}")
    return enabled, cap, workers


def _pct(v: Any) -> str:
    return v if isinstance(v, str) else f"{v:.0f}%"


def brief_line(p: dict[str, Any]) -> str:
    """The brief's soils line (hand-written wording only)."""
    s: Soils | None = p.get(DETAIL_KEY)
    if s is None:
        return "- Soils: not checked (soils off)"
    if s.state == NOT_CHECKED:
        return f"- Soils: not checked ({s.reason})"
    if s.hsg == NONE_RATED:
        hsg = "no hydrologic soil group rated"
    else:
        others = ", ".join(f"{k} {v:.0f}%" for k, v in sorted(s.hsg_shares.items(),
                                                             key=lambda kv: (-kv[1], kv[0]))
                           if k != s.hsg)
        # "mostly" only when the group really covers most of the mapped soil;
        # under urban land or water the largest rated group can be a minority.
        lead = "mostly" if s.hsg_shares[s.hsg] > 50 else "largest rated group"
        hsg = (f"hydrologic soil group {lead} {s.hsg} ({s.hsg_shares[s.hsg]:.0f}% of the "
               f"mapped soil" + (f"; also {others}" if others else "") + ")")
    parts = [hsg,
             f"hydric components {_pct(s.hydric_pct)}",
             f"poorly or very poorly drained {_pct(s.poorly_drained_pct)}",
             f"very limited for dwellings without basements (a slab-on-grade proxy) "
             f"{_pct(s.dwellings_limited_pct)}"]
    labels = {"hsg": "soil group", "hydric": "hydric", "drainage": "drainage",
              "dwellings": "dwellings rating"}
    unrated = [f"{labels.get(k, k)} {v:.0f}%" for k, v in s.unrated_pct.items() if v >= 1]
    tail = f"; unrated share of the mapped soil: {', '.join(unrated)}" if unrated else ""
    return (f"- Soils (USDA SSURGO, {s.units} map unit{'s' if s.units != 1 else ''}, "
            f"{s.coverage_pct:.0f}% of the parcel shape mapped): {'; '.join(parts)}{tail}. "
            f"{CAVEAT}")
