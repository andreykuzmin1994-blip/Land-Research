"""Per-parcel buildable checks against the map layers, plus slope and zoning.

Each function adds plain keys to the parcel dict, so the screens, ranking
and report all read one flat record.
"""

from __future__ import annotations

import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from shapely import unary_union
from shapely.geometry import Point, mapping
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree

from . import arcgis
from .geo import FT_PER_M, LocalProjection, acres, esri_to_shape, haversine_miles
from .layers import SYSTEM_INTERCHANGES, Index
from .zoning import entitlement, flu_category, zoning_category

WATER_TYPES = {"Lake", "Freshwater Pond", "Riverine", "Estuarine and Marine Deepwater"}
DEG_PER_FT = 1 / 364_000  # rough degrees of latitude per foot, for bbox padding

SLOPE_URL = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
             "3DEPElevation/ImageServer/computeStatisticsHistograms")
# Band 1 raw elevation (m), band 2 slope in percent rise.
SLOPE_RULE = json.dumps({
    "rasterFunction": "CompositeBand",
    "rasterFunctionArguments": {"Rasters": [
        "$$",
        {"rasterFunction": "Slope",
         "rasterFunctionArguments": {"ZFactor": 1, "SlopeType": 2, "Raster": "$$"}},
    ]},
    "outputPixelType": "F32",
})


SLOPE_PIXEL = json.dumps({"x": 10, "y": 10, "spatialReference": {"wkid": 3857}})
# One 10 m Web Mercator pixel covers at most 100 m² of ground (about 69 m²
# at Atlanta's latitude). On less open ground than that, 3DEP answers
# {"statistics": []} every time: a parcel-map problem, not a server failure,
# so slope isn't asked for (SLOPE_SKIPPED) and nothing is retried.
SLOPE_MIN_M2 = 100.0
# Slope answers are kept apart from the other map data and far longer: the
# lidar is a 2018-19 survey that does not change, the slope step is most of
# an uncached run, and repeated requests for one shape do not always agree
# (README, "Public data changes between days"), so a kept answer also keeps
# the steep-ground screen steady from run to run. The key holds the request
# (the open-ground shape, the rule and the pixel size): a changed shape is
# a new request. run.py sets the lifetime from [run] slope_cache_days;
# --refresh-slope sets it to 0 for one run. The folder is small (about 2 KB
# per parcel), so it can be carried between machines.
SLOPE_CACHE_DAYS = 3650.0
slope_cache_days: float = SLOPE_CACHE_DAYS
# What the last slope_checks call did, for the run's summary note: answers
# reused from the folder, asked of the service, taken from the folder
# because a fresh request failed, and the file time of the oldest one used.
slope_tally: dict[str, Any] = {"reused": 0, "asked": 0, "fell_back": 0, "oldest": None}
_tally_lock = threading.Lock()


def _tally(kind: str, mtime: float | None = None) -> None:
    with _tally_lock:
        slope_tally[kind] += 1
        if mtime is not None and (slope_tally["oldest"] is None or mtime < slope_tally["oldest"]):
            slope_tally["oldest"] = mtime


def slope_cache_dir():
    """<cache>/slope, beside the shared <cache>/http (read at call time, so
    tests that move the cache move this too)."""
    return arcgis.CACHE_DIR.parent / "slope"


def slope_days_setting(run_cfg: dict[str, Any]) -> float:
    """[run] slope_cache_days, or the default when the key is absent (an
    older settings file). ValueError for anything not a finite number of
    days, 0 or more (0 = ask every run)."""
    value = run_cfg.get("slope_cache_days", SLOPE_CACHE_DAYS)
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        raise ValueError(f"[run] slope_cache_days must be a number of days, 0 or more "
                         f"(got {value!r})")
    return float(value)
SLOPE_SKIPPED = "the parcel map leaves under one 10 m lidar pixel of open ground"


def _overlap(geom, proj, total_m2, index: Index, keep: Callable[[dict], bool] = lambda a: True):
    """(clipped WGS84 geometry or None, % of parcel, attrs of hits)."""
    parts, hits = [], []
    for shape, attrs in index.candidates(geom):
        if not keep(attrs):
            continue
        inter = shape.intersection(geom)
        if not inter.is_empty and inter.area > 0:
            parts.append(inter)
            hits.append(attrs)
    if not parts:
        return None, 0.0, []
    merged = unary_union(parts)
    return merged, round(100.0 * proj(merged).area / total_m2, 1), hits


def map_checks(parcels: list[dict[str, Any]], idx: dict[str, Index],
               industrial_points: list[tuple[float, float]], contamination_ft: float) -> None:
    exits = [(s.x, s.y, a) for s, a in idx["exits"].items
             if ((a.get("FED_ASSIGNED_INTERSTATE_NUMBER") or "").strip(),
                 (a.get("SIGNED_EXIT_NUMBER") or "").strip()) not in SYSTEM_INTERCHANGES]
    ind_tree = STRtree([Point(x, y) for x, y in industrial_points]) if industrial_points else None

    for p in parcels:
        g = p["geometry"]
        proj = LocalProjection.around(g)
        gm = proj(g)
        total = gm.area or 1.0
        c = g.centroid
        p["lon"], p["lat"] = round(c.x, 6), round(c.y, 6)
        p["gis_acres"] = round(acres(gm), 2)

        flood, p["flood_pct"], hits = _overlap(g, proj, total, idx["flood"])
        p["flood_zones"] = sorted({h.get("FLD_ZONE") or "?" for h in hits})
        _, p["floodway_pct"], _ = _overlap(
            g, proj, total, idx["flood"],
            keep=lambda a: "FLOODWAY" in (a.get("ZONE_SUBTY") or "").upper())
        wet, p["wetland_pct"], _ = _overlap(
            g, proj, total, idx["wetlands"],
            keep=lambda a: a.get("WETLAND_TYPE") not in WATER_TYPES)
        water, p["water_pct"], _ = _overlap(
            g, proj, total, idx["wetlands"],
            keep=lambda a: a.get("WETLAND_TYPE") in WATER_TYPES)
        ease, p["easement_pct"], ehits = _overlap(g, proj, total, idx["easements"])
        prot, p["protected_pct"], phits = _overlap(g, proj, total, idx["protected"])
        p["protected_names"] = sorted({(h.get("Unit_Nm") or h.get("sitename") or "").strip()
                                       for h in phits + ehits} - {""})

        constrained = [x for x in (flood, wet, water, ease, prot) if x is not None]
        p["constrained_pct"] = (round(100.0 * proj(unary_union(constrained)).area / total, 1)
                                if constrained else 0.0)
        p["usable_acres"] = round(p["acres"] * (1 - p["constrained_pct"] / 100.0), 1)
        # Slope is measured on the unconstrained ground only, so a steep creek
        # bank inside the floodplain isn't subtracted twice.
        open_ground = g.difference(unary_union(constrained)) if constrained else g
        p["_open_ground"] = open_ground if not open_ground.is_empty else g
        # 0.0 GIS acres (a map polygon of a few m²) is the worst mismatch of all.
        if p["acres"] and not 0.5 <= p["gis_acres"] / p["acres"] <= 2.0:
            p["acreage_mismatch"] = True

        p["opportunity_zone"] = any(s.contains(c) for s, _ in idx["oz"].candidates(c))

        # Contamination: listed sites on or within the buffer of the parcel.
        pad = g.buffer(contamination_ft * DEG_PER_FT * 1.5)
        sites = []
        for key in ("epa", "hsi"):
            for shape, a in idx[key].candidates(pad):
                d_ft = proj(shape).distance(gm) * FT_PER_M
                if d_ft <= contamination_ft:
                    name = (a.get("Facility_Name") or a.get("LOCATION_ADDRESS") or "site").strip()
                    kind = ("GA EPD HSI" if key == "hsi"
                            else (a.get("CAT_NAME") or a.get("SUB_NAME") or "EPA"))
                    sites.append({"name": name, "kind": kind, "feet": round(d_ft)})
        p["contamination"] = sorted(sites, key=lambda s: s["feet"])

        best = None
        for x, y, a in exits:
            d = haversine_miles(c.x, c.y, x, y)
            if best is None or d < best[0]:
                best = (d, a)
        if best:
            route = (best[1].get("FED_ASSIGNED_INTERSTATE_NUMBER") or "").strip()
            num = (best[1].get("SIGNED_EXIT_NUMBER") or "").strip()
            p["exit_miles"] = round(best[0], 2)
            road = f"I-{route}" if route.isdigit() else route
            p["exit_label"] = f"{road} exit {num}".strip() if road else f"exit {num}"
        else:
            p["exit_miles"], p["exit_label"] = None, None

        rail_ft = None
        for shape, a in idx["rail"].candidates(g.buffer(0.02)):
            d = proj(shape).distance(gm) * FT_PER_M
            if rail_ft is None or d < rail_ft[0]:
                rail_ft = (d, a.get("RROWNER1") or "")
        p["rail_ft"] = round(rail_ft[0]) if rail_ft else None
        p["rail_owner"] = rail_ft[1] if rail_ft else None

        n = 0
        if ind_tree is not None:
            for i in ind_tree.query(Point(c.x, c.y).buffer(0.02)):
                pt = ind_tree.geometries[i]
                if not g.contains(pt) and haversine_miles(c.x, c.y, pt.x, pt.y) <= 1.0:
                    n += 1
        p["industrial_within_1mi"] = n


def _esri_polygon(geom) -> dict[str, Any]:
    polys = getattr(geom, "geoms", [geom])
    rings = []
    for poly in polys:
        if poly.geom_type != "Polygon":
            continue
        poly = orient(poly.simplify(0.00001), sign=-1.0)  # Esri: clockwise shells
        rings.append([list(xy) for xy in poly.exterior.coords])
        rings.extend([list(xy) for xy in r.coords] for r in poly.interiors)
    return {"rings": rings, "spatialReference": {"wkid": 4326}}


def _hist_quantile(hist: dict[str, Any], q: float) -> float | None:
    counts = hist.get("counts") or []
    total = sum(counts)
    if not total:
        return None
    lo, hi = hist["min"], hist["max"]
    width = (hi - lo) / len(counts)
    target, run = q * total, 0
    for i, n in enumerate(counts):
        if run + n >= target:
            frac = (target - run) / n if n else 0
            return lo + (i + frac) * width
        run += n
    return hi


def _hist_share_above(hist: dict[str, Any], threshold: float) -> float | None:
    counts = hist.get("counts") or []
    total = sum(counts)
    if not total:
        return None
    lo, hi = hist["min"], hist["max"]
    width = (hi - lo) / len(counts) if counts else 0
    above = 0.0
    for i, n in enumerate(counts):
        b0, b1 = lo + i * width, lo + (i + 1) * width
        if b0 >= threshold:
            above += n
        elif b1 > threshold and width:
            above += n * (b1 - threshold) / width
    return 100.0 * above / total


def slope_stats(geom) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Relief and steepness from USGS 3DEP lidar at ~10 m (about 1-5 s per
    parcel), and a rough balanced cut/fill estimate from the same answer.

    Returns (slope fields, earthwork or None). The earthwork comes from the
    elevation histogram already fetched (no extra request) and never decides
    whether the answer is usable: it is None when it can't be read. Callers
    must keep it off the parcel until after scoring (slope_checks, run.main).
    An answer without usable histograms raises arcgis.BadAnswer.
    """
    params = {
        "geometry": json.dumps(_esri_polygon(geom)),
        "geometryType": "esriGeometryPolygon",
        "renderingRule": SLOPE_RULE,
        # Resample the 1 m lidar to ~10 m: at 1 m, ditches and stream banks
        # read as "steep" and overstate grading by 2-3x; relief is unchanged.
        "pixelSize": SLOPE_PIXEL,
    }
    folder = slope_cache_dir()
    kept = arcgis.cached_json(SLOPE_URL, params, max_age_days=slope_cache_days,
                              check=_slope_problem, cache_dir=folder)
    if kept is not None:
        data = kept[0]
        _tally("reused", kept[1])
    else:
        try:
            data = arcgis.request_json(SLOPE_URL, params, check=_slope_problem, fresh=True,
                                       cache_dir=folder, max_age_days=slope_cache_days)
            _tally("asked")
        except arcgis.ArcGISError:
            # The service failed. A kept answer of any age (one --refresh-slope
            # or the lifetime set aside) beats leaving the steep-ground screen
            # unapplied to a parcel that had a good reading.
            old = arcgis.cached_json(SLOPE_URL, params, max_age_days=math.inf,
                                     check=_slope_problem, cache_dir=folder)
            if old is None:
                raise
            data = old[0]
            _tally("fell_back", old[1])
    out = _slope_from(data)
    earthwork = None
    try:
        hist = (data.get("histograms") or [None])[0]
        earthwork = _hist_balanced_earthwork(hist, slope_area_m2(geom), _nominal_pixel_m2(geom))
    except _EARTHWORK_ERRORS:
        earthwork = None
    return out, earthwork


# Exactly 1 cubic yard in cubic metres (0.9144 m cubed).
M3_PER_CY = 0.764554857984
# The 3DEP request asks for 10 x 10 Web Mercator units (SLOPE_PIXEL); on the
# ground that is 10 cos(lat) m a side.
SLOPE_PIXEL_UNITS = 10.0
# The histogram's pixels times the nominal pixel area must be within this
# factor of the open-ground area, or the answer and the shape disagree (a
# NoData gap, or a mismatched geometry) and no earthwork is given. Inside the
# band the volume is still taken over the whole open-ground area: a gap of up
# to half that ground is filled by assuming it is like the ground the
# pixels do cover (the band is wide because the nominal pixel area is only
# approximate and edge pixels on small parcels count in full).
EARTHWORK_COVERAGE = (0.5, 2.0)
_EARTHWORK_ERRORS = (AttributeError, IndexError, KeyError, TypeError, ValueError,
                     ZeroDivisionError, OverflowError)


def _nominal_pixel_m2(geom) -> float | None:
    """Ground area (m²) of one requested 10 m Web Mercator pixel at geom's latitude."""
    try:
        lat = (geom.bounds[1] + geom.bounds[3]) / 2
        side = SLOPE_PIXEL_UNITS * math.cos(math.radians(lat))
    except _EARTHWORK_ERRORS:
        return None
    m2 = side * side
    return m2 if math.isfinite(m2) and m2 > 0 else None


def _finite_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _round_sig(x: float, digits: int = 2) -> int:
    """x rounded to `digits` significant figures, as a whole number. Below
    10**digits the whole-number step is coarser than `digits` figures, so x
    is rounded to the nearest whole number (0.96 gives 1, 1.5 gives 2, 0.4
    gives 0): never truncated."""
    if x == 0:
        return 0
    nd = min(0, digits - 1 - int(math.floor(math.log10(abs(x)))))
    return int(round(x, nd))


def _hist_balanced_earthwork(hist: Any, area_m2: Any, pixel_m2: Any = None) -> dict[str, Any] | None:
    """The rounded report fields of _balanced_cut (see there), or None."""
    raw = _balanced_cut(hist, area_m2, pixel_m2)
    if raw is None:
        return None
    cut_m3, mad = raw
    try:
        cy, ft = cut_m3 / M3_PER_CY, mad * FT_PER_M
        if not (math.isfinite(cy) and math.isfinite(ft)):
            return None
        return {"earthwork_cy": _round_sig(cy, 2), "mean_cut_fill_ft": round(ft, 1)}
    except _EARTHWORK_ERRORS:
        return None


def _balanced_cut(hist: Any, area_m2: Any, pixel_m2: Any) -> tuple[float, float] | None:
    """Rough balanced cut/fill to level the open ground to one flat pad at its
    mean elevation, from the band-1 (elevation, metres) histogram.

    Balance elevation = the histogram mean over bin centres, so cut equals
    fill by construction. With MAD the mean absolute deviation from it,
    cut = fill = area x MAD / 2 (per-pixel area = area / pixel count, so the
    volume does not depend on the pixel size). Bin centres put each pixel at
    most half a bin width off, so MAD is off by at most (max-min)/(2 bins):
    negligible for a real 3DEP histogram.

    Returns (cut m³, MAD m), unrounded; _hist_balanced_earthwork turns them
    into {"earthwork_cy", "mean_cut_fill_ft"} (cut volume in cubic yards to 2
    significant figures; MAD in feet, to 0.1). None, never raising, when:
    the histogram or its counts can't be read; a count is negative, None,
    non-finite or not a number; "size" disagrees with the counts; max < min;
    no pixels; the area is not positive; the pixel count times pixel_m2 is
    outside EARTHWORK_COVERAGE times the area (pixel_m2 must be given); or
    any result is non-finite.

    Inside the coverage band the volume uses the full area_m2, not the
    pixel-covered area: a parcel whose pixels cover only half (or 60%) of
    the open ground gets the full-area volume extrapolated from the pixels
    it has.

    Not a grading plan: it ignores drainage cross-slope, building-pad-only
    leveling, terracing, shrink/swell, topsoil and rock.
    """
    try:
        if not isinstance(hist, dict):
            return None
        counts, lo, hi = hist.get("counts"), hist.get("min"), hist.get("max")
        if not isinstance(counts, list) or not counts:
            return None
        if not all(_finite_number(n) and n >= 0 for n in counts):
            return None
        if "size" in hist and hist["size"] != len(counts):
            return None
        if not (_finite_number(lo) and _finite_number(hi)) or hi < lo:
            return None
        if not (_finite_number(area_m2) and area_m2 > 0):
            return None
        if not (_finite_number(pixel_m2) and pixel_m2 > 0):
            return None
        total = float(sum(counts))
        if not (math.isfinite(total) and total > 0):
            return None
        lo_f, hi_f = EARTHWORK_COVERAGE
        if not lo_f * area_m2 <= total * pixel_m2 <= hi_f * area_m2:
            return None
        width = (hi - lo) / len(counts)
        centres = [lo + (i + 0.5) * width for i in range(len(counts))]
        mean = sum(n * c for n, c in zip(counts, centres)) / total
        mad = sum(n * abs(c - mean) for n, c in zip(counts, centres)) / total
        cut_m3 = area_m2 * mad / 2
        if not (math.isfinite(mean) and math.isfinite(mad) and math.isfinite(cut_m3)):
            return None
        return cut_m3, mad
    except _EARTHWORK_ERRORS:
        return None


EARTHWORK_KEYS = ("earthwork_cy", "mean_cut_fill_ft")


def earthwork_fields(earthwork: dict[str, Any] | None) -> dict[str, Any]:
    """The ranked-output earthwork columns (None when not computed). run.main
    attaches these only after scoring, flags and the sort."""
    e = earthwork or {}
    return {k: e.get(k) for k in EARTHWORK_KEYS}


def _slope_problem(data: dict[str, Any]) -> str | None:
    # 3DEP sometimes answers HTTP 200 with no histograms ({"statistics": []}):
    # that is a failed check, not flat ground.
    try:
        out = _slope_from(data)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return "no usable slope data in the answer"
    if out["relief_ft"] is None or out["steep_pct"] is None:
        return "no usable slope data in the answer"
    return None


def _slope_from(data: dict[str, Any]) -> dict[str, Any]:
    hists = data.get("histograms") or []
    stats = data.get("statistics") or []
    p2, p98 = _hist_quantile(hists[0], 0.02), _hist_quantile(hists[0], 0.98)
    steep = _hist_share_above(hists[1], 15.0)
    return {
        "relief_ft": round((p98 - p2) * FT_PER_M, 1) if p2 is not None and p98 is not None else None,
        "steep_pct": round(steep, 1) if steep is not None else None,
        "mean_slope_pct": round(stats[1].get("mean", 0.0), 1),
    }


def slope_area_m2(geom) -> float:
    """Ground area (m²) of the polygons slope_stats sends for geom: its
    top-level Polygon parts, as _esri_polygon picks them. 0.0 for an empty,
    line-only or zero-area shape; nothing is projected or raised for those."""
    parts = [g for g in getattr(geom, "geoms", [geom]) if g.geom_type == "Polygon"]
    deg2 = sum(g.area for g in parts)
    if not deg2 > 0:
        return 0.0
    xmin, ymin, xmax, ymax = geom.bounds
    proj = LocalProjection((xmin + xmax) / 2, (ymin + ymax) / 2)
    m2 = deg2 * proj.kx * proj.ky
    return m2 if math.isfinite(m2) else 0.0


def too_small_for_slope(p: dict[str, Any]) -> bool:
    """The parcel's open ground on the county map is under one 10 m lidar pixel."""
    return slope_area_m2(p.get("_open_ground") or p["geometry"]) < SLOPE_MIN_M2


def slope_checks(parcels: list[dict[str, Any]], workers: int,
                 progress: Callable[[int, int], None] | None = None) -> list[dict[str, Any] | None]:
    """Fill each parcel's slope fields. Returns the earthwork estimates, one
    per parcel in input order (None when not computed); they are NOT put on
    the parcels, so scoring and rank.py can't read them (run.main attaches
    them after scoring)."""
    def work(p):
        if too_small_for_slope(p):
            p.update({"relief_ft": None, "steep_pct": None, "mean_slope_pct": None,
                      "slope_skipped": SLOPE_SKIPPED})
            return None
        try:
            fields, earthwork = slope_stats(p.get("_open_ground") or p["geometry"])
        except arcgis.ArcGISError as exc:
            p.update({"relief_ft": None, "steep_pct": None, "mean_slope_pct": None,
                      "slope_error": str(exc)[:200]})
            return None
        p.update(fields)
        return earthwork

    with _tally_lock:
        slope_tally.update(reused=0, asked=0, fell_back=0, oldest=None)
    done = 0
    out: list[dict[str, Any] | None] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for earthwork in pool.map(work, parcels):
            out.append(earthwork)
            done += 1
            if progress and (done % 100 == 0 or done == len(parcels)):
                progress(done, len(parcels))
    return out


def zoning_checks(parcels: list[dict[str, Any]], sources: list[dict[str, Any]],
                  tiles_for: Callable[[list[dict[str, Any]]], list]) -> None:
    """Fill zoning / future land use from the county's per-city sources."""
    for p in parcels:
        p.setdefault("zoning", None)
        p.setdefault("zoning_desc", None)
        p.setdefault("flu", None)
        p.setdefault("zoning_source", None)

    for src in sources:
        group = [p for p in parcels if p["jurisdiction"] in src["jurisdictions"]]
        if not group:
            continue
        wanted = [f for f in (src.get("id_field"), src.get("zoning_field"),
                              src.get("desc_field"), src.get("flu_field")) if f]
        try:
            feats = arcgis.features_in(src["url"], "1=1", tiles_for(group), ",".join(wanted),
                                       return_geometry=src["kind"] == "overlay",
                                       max_offset=0.00001)
        except arcgis.ArcGISError as exc:
            print(f"  ! zoning source unavailable: {src['name']} ({exc})")
            for p in group:
                p.setdefault("zoning_problem", f"{src['name']} was unavailable this run")
            continue

        if src["kind"] == "join":
            table = {(f["attributes"].get(src["id_field"]) or "").strip(): f["attributes"]
                     for f in feats if f.get("attributes")}
            matched = 0
            for p in group:
                key = p["parcel_id"][:-1] if src.get("id_trim_last") else p["parcel_id"]
                a = table.get(key)
                if a:
                    matched += 1
                    _apply_zoning(p, src, a)
            if matched < 0.5 * len(group):
                print(f"  ! {src['name']}: only {matched} of {len(group)} parcels matched "
                      "by parcel id; the source's id format may have changed")
                for p in group:
                    if not (p["zoning"] or p["flu"]):
                        p.setdefault("zoning_problem",
                                     f"{src['name']} didn't match this parcel id this run")
        else:
            items = [(esri_to_shape(f.get("geometry")), f.get("attributes") or {}) for f in feats]
            index = Index([(s, a) for s, a in items if s is not None and not s.is_empty])
            for p in group:
                g = p["geometry"]
                best = max(((s.intersection(g).area, a) for s, a in index.candidates(g)),
                           key=lambda t: t[0], default=None)
                if best and best[0] > 0:
                    _apply_zoning(p, src, best[1])

    for p in parcels:
        p["zoning_category"] = zoning_category(p["zoning"])
        p["flu_category"] = flu_category(p["flu"])
        p["entitlement"], p["entitlement_note"] = entitlement(
            p["zoning_category"], p["flu_category"], p.get("industrial_within_1mi", 0))
        if p["entitlement"] == "unknown" and p.get("zoning_problem"):
            p["entitlement_note"] = f"zoning check failed ({p['zoning_problem']}); re-run or ask the city"


def _apply_zoning(p: dict[str, Any], src: dict[str, Any], attrs: dict[str, Any]) -> None:
    def val(field):
        v = attrs.get(field) if field else None
        return str(v).strip() if v not in (None, "") and str(v).strip() else None

    z, flu = val(src.get("zoning_field")), val(src.get("flu_field"))
    if z and not p["zoning"]:
        p["zoning"] = z
        p["zoning_desc"] = val(src.get("desc_field"))
        p["zoning_source"] = src["name"]
    if flu and not p["flu"]:
        p["flu"] = flu
        p["flu_source"] = src["name"]


def geojson_geometry(geom) -> dict[str, Any]:
    return mapping(geom.simplify(0.00002))
