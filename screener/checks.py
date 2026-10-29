"""Per-parcel buildable checks against the map layers, plus slope and zoning.

Each function adds plain keys to the parcel dict, so the screens, ranking
and report all read one flat record.
"""

from __future__ import annotations

import json
import math
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


def slope_stats(geom) -> dict[str, Any]:
    """Relief and steepness from USGS 3DEP lidar at ~10 m (about 1-5 s per parcel).

    An answer without usable histograms raises arcgis.BadAnswer.
    """
    data = arcgis.request_json(SLOPE_URL, {
        "geometry": json.dumps(_esri_polygon(geom)),
        "geometryType": "esriGeometryPolygon",
        "renderingRule": SLOPE_RULE,
        # Resample the 1 m lidar to ~10 m: at 1 m, ditches and stream banks
        # read as "steep" and overstate grading by 2-3x; relief is unchanged.
        "pixelSize": SLOPE_PIXEL,
    }, check=_slope_problem)
    return _slope_from(data)


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
                 progress: Callable[[int, int], None] | None = None) -> None:
    def work(p):
        if too_small_for_slope(p):
            p.update({"relief_ft": None, "steep_pct": None, "mean_slope_pct": None,
                      "slope_skipped": SLOPE_SKIPPED})
            return p
        try:
            p.update(slope_stats(p.get("_open_ground") or p["geometry"]))
        except arcgis.ArcGISError as exc:
            p.update({"relief_ft": None, "steep_pct": None, "mean_slope_pct": None,
                      "slope_error": str(exc)[:200]})
        return p

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for _ in pool.map(work, parcels):
            done += 1
            if progress and (done % 100 == 0 or done == len(parcels)):
                progress(done, len(parcels))


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
