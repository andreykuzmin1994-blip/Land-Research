"""Built check for every ranked parcel of a screener run.

Signals (no owner data used or written):
  1. FEMA USA Structures footprints inside the parcel's OUTLINE (holes filled):
     total SF, count, largest, and how much sits inside a hole of the parcel shape.
  2. Holes in the county parcel shape (often a separate improvements-only record).
  3. County tax records whose polygon lies mostly inside the outline (other
     parcel ids), with their class, land use and improvement value.
Writes OUT/builtcheck.json and OUT/builtcheck.csv.
"""
import csv, json, sys, time
from pathlib import Path
import shapely
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely.ops import unary_union
sys.path.insert(0, ".")
from screener import arcgis
from screener.geo import esri_to_shape, LocalProjection, acres, tile_envelopes

RUN = Path(sys.argv[1]); OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
STRUCT = "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/USA_Structures_View/FeatureServer/0"
PARCELS = "https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11"
SQFT = 10.7639
arcgis.cache_days = 30.0

feats = json.load(open(RUN / "ranked.geojson"))["features"]
rows = {r["parcel_id"]: r for r in csv.DictReader(open(RUN / "ranked.csv", encoding="utf-8-sig"))}

def outline(g):
    parts = [g] if g.geom_type == "Polygon" else list(g.geoms)
    return unary_union([Polygon(p.exterior) for p in parts])

# 1) footprints, fetched per tile like the screener's own layers
geoms = {f["properties"]["parcel_id"]: shape(f["geometry"]) for f in feats}
tiles = [g.bounds for g in geoms.values()]   # one envelope per parcel
print("tiles", len(tiles), flush=True)
fps = arcgis.features_in(STRUCT, "1=1", tiles, "OCC_CLS,PRIM_OCC,SQFEET,IMAGE_DATE")
items = []
for f in fps:
    s = esri_to_shape(f.get("geometry"))
    if s is not None and not s.is_empty:
        items.append((s, f.get("attributes") or {}))
tree = shapely.STRtree([s for s, _ in items])
print("footprints", len(items), flush=True)

res = []
for pid, g in geoms.items():
    r = rows[pid]
    o = outline(g)
    pj = LocalProjection.around(g)
    hole = o.difference(g)
    hole_ac = round(acres(pj(hole)), 2) if not hole.is_empty else 0.0
    on, in_hole, n, largest, occ = 0.0, 0.0, 0, 0, set()
    for i in tree.query(o):
        s, a = items[i]
        if not s.intersects(o):
            continue
        inter = s.intersection(o)
        a_sf = pj(inter).area * SQFT
        if a_sf < 200:          # slivers along a shared line
            continue
        n += 1; on += a_sf
        if not hole.is_empty:
            in_hole += pj(s.intersection(hole)).area * SQFT
        largest = max(largest, int(a.get("SQFEET") or 0))
        occ.add(a.get("OCC_CLS") or "?")
    res.append({"parcel_id": pid, "rank": int(r["rank"]), "address": r["site_address"], "jurisdiction": r["jurisdiction"],
                "acres": float(r["acres"]), "lead_grade": r["lead_grade"], "improvement_value_roll": None,
                "footprints": n, "footprint_sf_in_outline": round(on), "footprint_sf_in_holes": round(in_hole),
                "largest_footprint_sf": largest, "occupancy": sorted(occ), "hole_acres": hole_ac})

# 2/3) tax records inside the outline: top 300 by rank (one county query each, cached)
top = sorted(res, key=lambda x: x["rank"])[:300]
for i, x in enumerate(top):
    g = geoms[x["parcel_id"]]; o = outline(g); pj = LocalProjection.around(g)
    try:
        d = arcgis.request_json(PARCELS + "/query", {"geometry": json.dumps({"rings": [list(map(list, o.exterior.coords))] if o.geom_type == "Polygon" else [list(map(list, p.exterior.coords)) for p in o.geoms], "spatialReference": {"wkid": 4326}}),
                                                     "geometryType": "esriGeometryPolygon", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                                                     "outFields": "ParcelID,Address,ClassCode,LUCode,LandAppr,ImprAppr,LandAcres", "returnGeometry": "true", "outSR": 4326})
    except arcgis.ArcGISError as exc:
        x["records_inside"] = f"query failed: {str(exc)[:80]}"; continue
    inside = []
    for f in d.get("features", []):
        a = f["attributes"]; s = esri_to_shape(f["geometry"])
        if s is None or a.get("ParcelID") == x["parcel_id"]:
            if a.get("ParcelID") == x["parcel_id"]:
                x["improvement_value_roll"] = a.get("ImprAppr")
            continue
        share = s.intersection(o).area / s.area if s.area else 0
        if share >= 0.8:
            inside.append({"parcel_id": a.get("ParcelID"), "address": a.get("Address"), "class": a.get("ClassCode"),
                           "land_use": a.get("LUCode"), "improvements": a.get("ImprAppr"), "land": a.get("LandAppr"),
                           "acres_map": round(acres(pj(s)), 2)})
    x["records_inside"] = inside
    if i % 50 == 0:
        print("records", i, flush=True)

def verdict(x):
    why = []
    if x["footprint_sf_in_holes"] >= 2500:
        why.append(f"{x['footprint_sf_in_holes']:,} SF of building in a hole of the parcel shape")
    if x["footprint_sf_in_outline"] >= 10000:
        why.append(f"{x['footprint_sf_in_outline']:,} SF of building footprints inside the outline")
    rec = x.get("records_inside") if isinstance(x.get("records_inside"), list) else []
    big = [r for r in rec if (r.get("improvements") or 0) >= 250000]
    if big:
        why.append("separate tax record(s) inside the outline with improvements: " +
                   "; ".join(f"{r['parcel_id']} ${r['improvements']:,.0f}" for r in big))
    if why:
        return "built", why
    if x["footprint_sf_in_outline"] >= 2500 or rec:
        return "check", [f"{x['footprint_sf_in_outline']:,} SF of footprints in the outline" if x["footprint_sf_in_outline"] else "",
                         f"{len(rec)} other tax record(s) inside the outline" if rec else ""]
    return "no building found", []

for x in res:
    v, why = verdict(x)
    x["built_check"] = v; x["why"] = "; ".join(w for w in why if w)
json.dump(res, open(OUT / "builtcheck.json", "w"), indent=1)
with open(OUT / "builtcheck.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["rank", "parcel_id", "address", "jurisdiction", "acres", "lead_grade", "built_check", "why", "footprints",
                "footprint_sf_in_outline", "footprint_sf_in_holes", "largest_footprint_sf", "hole_acres"])
    for x in sorted(res, key=lambda x: x["rank"]):
        w.writerow([x["rank"], x["parcel_id"], x["address"], x["jurisdiction"], x["acres"], x["lead_grade"], x["built_check"], x["why"],
                    x["footprints"], x["footprint_sf_in_outline"], x["footprint_sf_in_holes"], x["largest_footprint_sf"], x["hole_acres"]])
from collections import Counter
print(Counter(x["built_check"] for x in res))
print(Counter(x["built_check"] for x in res if x["rank"] <= 250))
