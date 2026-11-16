"""Screening-level industrial site planner, version 2.

Built from the research spec (scratchpad/research/spec.md) and its critic.
It reuses the land screener's own constraint layers, rectangle search,
frontage check and geometry helpers; it changes no repo code.

A plan is one explicit cross-section placed on the parcel:
  rear-load:  [walk 5 | car row 18 | aisle = aerial fire lane 26 | n x 60 ft double car bays]
              + building depth D + truck court 130 + trailer row 55
              along the length: building L + a 40 ft truck road at each end (loop + fire reach)
  cross-dock: trailer row 55 + court 130 + D + court 130 + trailer row 55 across the depth;
              along the length: 40 ft truck road + car field at one end + L + 40 ft truck road
then doors, trailer stalls, car stalls, a detention pond sized from the impervious
area (placed at the lowest lidar ground that fits), drop-trailer yards if the
court rows fall short, two driveways on the GDOT frontage, and checks.

All zoning numbers here are DRAFT screening placeholders from the research
(mostly code mirrors); the human-owned zoning_rules.json is the authority.
"""
from __future__ import annotations
import csv, json, math, sys, textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import Polygon, MultiPolygon, Point, LineString, box, shape, mapping
from shapely.ops import unary_union, nearest_points
from shapely import affinity

sys.path.insert(0, ".")
from screener import arcgis, layers, sitefit, checks, frontage
from screener.geo import LocalProjection, acres, FT_PER_M, esri_to_shape, tile_envelopes, expand

PARCELS = "https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11"
TRANS = "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Electric_Power_Transmission_Lines/FeatureServer/0"
DEM = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
SQFT_PER_AC = 43560.0
arcgis.cache_days = 30.0

# ---------------------------------------------------------------------------
# Parameters (spec section 1; critic corrections applied)
# ---------------------------------------------------------------------------
COURT = 130            # maneuvering court, dock face to trailer row
TRAILER_ROW = 55
TRUCK_ROAD = 40        # 30 ft two-way widened toward 40-45 at bends (critic A10)
CAR_ROW, CAR_AISLE, WALK = 18, 26, 5     # wall-side row + aisle that doubles as aerial fire lane (critic A4)
DOUBLE_BAY = 60
STALL_W = 9
END_CLEAR = 60         # door-free wall at each end (on the building; critic A2)
DOOR_SP = 12
SF_PER_DOOR = 5000
OFFICE_SHARE = 0.02
SF_PER_STALL_NOTE = "9 x 18 ft stalls, 24-26 ft aisles"
DROP_MODULE = (600, 190, 100)   # length, depth, stalls (55 + 80 aisle + 55)
DROP_HALF = (300, 190, 50)
POND_DEPTH, POND_Z, FREEBOARD, RIM = 6.0, 4.0, 1.0, 15.0
HSG_K = {"A": (0.30, 0.05), "B": (0.30, 0.07), "C": (0.23, 0.10), "D": (0.20, 0.10)}

OPERATOR = [  # the operator's prototypes (building_prototypes.json), read-only here
    {"name": "operator rear-load 260", "type": "rear", "D": 260, "Lmin": 400, "Lmax": 640, "operator": True},
    {"name": "operator cross-dock 520", "type": "cross", "D": 520, "Lmin": 600, "Lmax": 1000, "operator": True},
]
MARKET = [   # Georgia comps by depth (spec 1.1); not operator prototypes
    {"name": "rear-load 190 deep", "type": "rear", "D": 190, "Lmin": 220, "Lmax": 700, "operator": False},
    {"name": "rear-load 230 deep", "type": "rear", "D": 230, "Lmin": 300, "Lmax": 800, "operator": False},
    {"name": "rear-load 270 deep", "type": "rear", "D": 270, "Lmin": 500, "Lmax": 1000, "operator": False},
    {"name": "rear-load 310 deep", "type": "rear", "D": 310, "Lmin": 600, "Lmax": 1150, "operator": False},
    {"name": "cross-dock 470 deep", "type": "cross", "D": 470, "Lmin": 900, "Lmax": 1300, "operator": False},
    {"name": "cross-dock 600 deep", "type": "cross", "D": 600, "Lmin": 1100, "Lmax": 1500, "operator": False},
]

# Draft dimensional rules by jurisdiction (spec 1.10). bldg/pave offsets by edge kind.
JUR = {
    "south fulton": {"bldg": {"ROW": 40, "RES_SIDE": 60, "RES_REAR": 110, "OTHER": 10}, "pave": {"ROW": 10, "RES_SIDE": 60, "RES_REAR": 110, "OTHER": 5},
                     "cover": None, "height": 100, "parking": "fulton"},
    "fulton industrial": {"bldg": {"ROW": 40, "RES_SIDE": 60, "RES_REAR": 110, "OTHER": 10}, "pave": {"ROW": 10, "RES_SIDE": 60, "RES_REAR": 110, "OTHER": 5},
                          "cover": None, "height": 100, "parking": "fulton"},
    "fairburn": {"bldg": {"ROW": 40, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 25}, "pave": {"ROW": 25, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 5},
                 "cover": ("building", 0.60), "height": 48, "parking": "fairburn"},
    "union city": {"bldg": {"ROW": 50, "RES_SIDE": 95, "RES_REAR": 95, "OTHER": 20}, "pave": {"ROW": 10, "RES_SIDE": 75, "RES_REAR": 75, "OTHER": 5},
                   "cover": ("building+parking", 0.80), "height": 75, "parking": "union city"},
    "atlanta": {"bldg": {"ROW": 40, "RES_SIDE": 20, "RES_REAR": 20, "OTHER": 10}, "pave": {"ROW": 10, "RES_SIDE": 20, "RES_REAR": 20, "OTHER": 0},
                "cover": None, "height": None, "parking": "atlanta"},
    "east point": {"bldg": {"ROW": 40, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 8}, "pave": {"ROW": 10, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 5},
                   "cover": None, "height": 40, "parking": "fulton"},
    "college park": {"bldg": {"ROW": 50, "RES_SIDE": 40, "RES_REAR": 40, "OTHER": 25}, "pave": {"ROW": 10, "RES_SIDE": 40, "RES_REAR": 40, "OTHER": 5},
                     "cover": ("impervious", 0.70), "height": 35, "parking": "college park"},
    "palmetto": {"bldg": {"ROW": 50, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 20}, "pave": {"ROW": 10, "RES_SIDE": 50, "RES_REAR": 50, "OTHER": 5},
                 "cover": ("building", 0.70), "height": 40, "parking": "palmetto"},
}

def jur_rules(name: str) -> tuple[str, dict]:
    n = (name or "").lower()
    for k in JUR:
        if k in n:
            return k, JUR[k]
    return "south fulton (default)", JUR["south fulton"]

def parking_required(rule: str, sf: float) -> tuple[int, str]:
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
        return math.ceil(sf / 600), "Atlanta 1/600 SF (draft; flag ATL_PARKING_VERIFY)"
    return 0, "none"

def parking_market(sf: float) -> int:
    ratio = 0.55 if sf >= 250000 else 0.70 if sf >= 100000 else 1.0
    return math.ceil(ratio * sf / 1000)

def ada_count(n: int) -> int:
    for top, k in ((25, 1), (50, 2), (75, 3), (100, 4), (150, 5), (200, 6), (300, 7), (400, 8), (500, 9)):
        if n <= top:
            return k
    return math.ceil(0.02 * n) if n <= 1000 else 20 + math.ceil((n - 1000) / 100)

def trailer_target(sf: float, doors: int) -> int:
    k = 0.5 if sf < 250000 else 1.0 if sf < 450000 else 1.3
    return math.ceil(k * doors)

def pond_dims(v_acft: float, d: float = POND_DEPTH, z: float = POND_Z) -> tuple[float, float, float, float]:
    """Bottom width W0 (L0 = 2 W0) holding v_acft at depth d; returns (W0, L_top, W_top, footprint incl. freeboard+rim as (Lf, Wf))."""
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

# ---------------------------------------------------------------------------
# Geometry helpers (a local feet plane per site)
# ---------------------------------------------------------------------------
class Plane:
    def __init__(self, g):
        self.pj = LocalProjection.around(g)
    def ft(self, g):
        return shapely.transform(self.pj(g), lambda c: c * FT_PER_M)
    def wgs(self, gft):
        pj = self.pj
        def inv(c):
            o = c / FT_PER_M
            o[:, 0] = o[:, 0] / pj.kx + pj.lon0
            o[:, 1] = o[:, 1] / pj.ky + pj.lat0
            return o
        return shapely.transform(gft, inv)

def parts(g) -> list[Polygon]:
    return [p for p in sitefit.polygon_parts(g) if p.area > 1]

def clean(g, tol=15.0):
    g = shapely.make_valid(g)
    g = unary_union([p for p in sitefit.polygon_parts(g)])
    return g.buffer(-tol).buffer(tol) if not g.is_empty else g

class Frame:
    """Module-local (u along length from the module's left end, v across depth from side 0) to plane."""
    def __init__(self, Lmod, depth, pl, origin, flip=False):
        self.Lmod, self.depth, self.pl, self.origin, self.flip = Lmod, depth, pl, origin, flip
        a = math.radians(pl.angle_deg)
        self.c, self.s = math.cos(a), math.sin(a)
    def pt(self, u, v):
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
# Inputs
# ---------------------------------------------------------------------------
def county_near(g, pad=0.0015):
    b = g.bounds
    d = arcgis.request_json(PARCELS + "/query", {"geometry": f"{b[0]-pad},{b[1]-pad},{b[2]+pad},{b[3]+pad}", "geometryType": "esriGeometryEnvelope",
                                                 "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                                                 "outFields": "ParcelID,LUCode,ClassCode", "returnGeometry": "true", "outSR": 4326})
    return [(f["attributes"], esri_to_shape(f["geometry"])) for f in d.get("features", []) if f.get("geometry")]

def transmission(g, pad=0.002):
    b = g.bounds
    d = arcgis.request_json(TRANS + "/query", {"geometry": f"{b[0]-pad},{b[1]-pad},{b[2]+pad},{b[3]+pad}", "geometryType": "esriGeometryEnvelope",
                                               "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "VOLTAGE,VOLT_CLASS,OWNER,STATUS",
                                               "returnGeometry": "true", "outSR": 4326})
    out = []
    for f in d.get("features", []):
        s = esri_to_shape(f.get("geometry"))
        if s is not None:
            out.append((f["attributes"], s))
    return out

def corridor_halfwidth(volt) -> float:
    v = float(volt) if volt not in (None, "", -999999) else 115.0
    if v < 0: v = 115.0
    return 30 if v <= 69 else 50 if v <= 161 else 65 if v <= 230 else 90   # assumed easement half-widths, ft

def dem_samples(plane: Plane, region_ft, step=40.0):
    minx, miny, maxx, maxy = region_ft.bounds
    pts = []
    y = miny + step / 2
    while y < maxy:
        x = minx + step / 2
        while x < maxx:
            pts.append((x, y)); x += step
        y += step
    pts = [p for p in pts if region_ft.buffer(step).contains(Point(p))]
    wg = [plane.wgs(Point(p)) for p in pts]
    elev = {}
    for i in range(0, len(wg), 400):
        chunk = wg[i:i + 400]
        geom = json.dumps({"points": [[round(p.x, 7), round(p.y, 7)] for p in chunk], "spatialReference": {"wkid": 4326}})
        try:
            d = arcgis.request_json(DEM, {"geometry": geom, "geometryType": "esriGeometryMultipoint", "returnFirstValueOnly": "true"},
                                    max_age_days=3650)
        except arcgis.ArcGISError:
            continue
        for s in d.get("samples", []):
            try:
                elev[pts[i + int(s["locationId"])]] = float(s["value"]) * FT_PER_M
            except (ValueError, KeyError, IndexError):
                pass
    return elev, step

def steep_cells(elev, step, threshold=15.0):
    cells = []
    for (x, y), z in elev.items():
        gx = [elev.get((x + step, y)), elev.get((x - step, y))]
        gy = [elev.get((x, y + step)), elev.get((x, y - step))]
        dzx = (gx[0] - gx[1]) / (2 * step) if None not in gx else None
        dzy = (gy[0] - gy[1]) / (2 * step) if None not in gy else None
        if dzx is None or dzy is None:
            continue
        if 100 * math.hypot(dzx, dzy) > threshold:
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
        far = None
        if not row.is_empty:
            dmax = max(row.distance(Point(c)) for q in sitefit.polygon_parts(parcel_ft) for c in q.exterior.coords)
            for seg in getattr(res, "geoms", [res]):
                if seg.is_empty: continue
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
# One configuration on one region
# ---------------------------------------------------------------------------
@dataclass
class Plan:
    cfg: dict
    L: float
    D: float
    n_bays: int
    frame: Frame
    module: Polygon
    building: Polygon
    courts: list
    trailer_rows: list
    car_fields: list
    roads: list
    offices: list
    sf: int
    doors: int
    court_stalls: int
    car_stalls: int
    cars_required: int
    cars_rule: str
    trailers_target: int
    flags: list = field(default_factory=list)

def car_rows_layout(r):
    """Row start offsets and total depth for r car rows: wall row + aerial-lane aisle, then rows
    alternately facing the previous aisle or back-to-back with a new 24 ft aisle."""
    rows, v = [WALK], WALK + CAR_ROW + CAR_AISLE
    for k in range(2, r + 1):
        rows.append(v); v += CAR_ROW
        if k % 2 == 1:
            v += 24
    return rows, v

def cross_section(cfg, n):
    if cfg["type"] == "rear":
        car = car_rows_layout(n)[1]
        return car + cfg["D"] + COURT + TRAILER_ROW, car
    return TRAILER_ROW + COURT + cfg["D"] + COURT + TRAILER_ROW, None

def module_length(cfg, L, n):
    if cfg["type"] == "rear":
        return L + 2 * TRUCK_ROAD
    car_w = car_rows_layout(n)[1]
    return L + car_w + 2 * TRUCK_ROAD

def build_plan(cfg, L, n, frame, depth, car_depth, rules_parking):
    D = cfg["D"]
    Lmod = frame.Lmod
    courts, rows, cars, roads, offices = [], [], [], [], []
    if cfg["type"] == "rear":
        u0, u1 = TRUCK_ROAD, TRUCK_ROAD + L
        b = frame.rect(u0, u1, car_depth, car_depth + D)
        courts.append(frame.rect(0, Lmod, car_depth + D, car_depth + D + COURT))
        rows.append(frame.rect(TRUCK_ROAD, Lmod - TRUCK_ROAD, car_depth + D + COURT, depth))
        cars.append(frame.rect(u0, u1, 0, car_depth))
        roads += [frame.rect(0, TRUCK_ROAD, 0, car_depth + D), frame.rect(Lmod - TRUCK_ROAD, Lmod, 0, car_depth + D)]
        pod = min(80.0, math.sqrt(OFFICE_SHARE * L * D / 2))
        offices += [frame.rect(u0, u0 + pod, car_depth, car_depth + pod), frame.rect(u1 - pod, u1, car_depth, car_depth + pod)]
        walls = 1
        car_len = L - 2 * 24
        car_stalls = n * int(car_len // STALL_W)
        court_stalls = int((Lmod - 2 * TRUCK_ROAD - 80) // 12)
    else:
        car_w = car_rows_layout(n)[1]
        u0 = TRUCK_ROAD + car_w
        u1 = u0 + L
        b = frame.rect(u0, u1, TRAILER_ROW + COURT, TRAILER_ROW + COURT + D)
        courts += [frame.rect(TRUCK_ROAD, Lmod - TRUCK_ROAD, TRAILER_ROW, TRAILER_ROW + COURT),
                   frame.rect(TRUCK_ROAD, Lmod - TRUCK_ROAD, TRAILER_ROW + COURT + D, depth - TRAILER_ROW)]
        rows += [frame.rect(u0, Lmod - TRUCK_ROAD, 0, TRAILER_ROW), frame.rect(u0, Lmod - TRUCK_ROAD, depth - TRAILER_ROW, depth)]
        cars.append(frame.rect(TRUCK_ROAD, u0, TRAILER_ROW + COURT, TRAILER_ROW + COURT + D))
        roads += [frame.rect(0, TRUCK_ROAD, 0, depth), frame.rect(Lmod - TRUCK_ROAD, Lmod, 0, depth)]
        pod = min(90.0, math.sqrt(OFFICE_SHARE * L * D / 2))
        offices += [frame.rect(u0, u0 + pod, TRAILER_ROW + COURT + D / 2 - pod, TRAILER_ROW + COURT + D / 2),
                    frame.rect(u0, u0 + pod, TRAILER_ROW + COURT + D / 2, TRAILER_ROW + COURT + D / 2 + pod)]
        walls = 2
        car_stalls = n * int((D - 48) // STALL_W)
        court_stalls = 2 * int((L - 80) // 12)
    sf = int(round(L * D))
    cap = max(0, int((L - 2 * END_CLEAR) // DOOR_SP))
    doors = min(round(sf / SF_PER_DOOR), cap * walls)
    req_code, rule = parking_required(rules_parking, sf)
    req = max(req_code, parking_market(sf))
    return Plan(cfg, L, D, n, frame, frame.rect(0, Lmod, 0, depth), b, courts, rows, cars, roads, offices, sf, doors,
                court_stalls, car_stalls, req, rule + f"; market {parking_market(sf)}", trailer_target(sf, doors))

def fit_config(cfg, P_ft, B_ft, rules, prefer_dock_away=None, max_bays=8, length_cap=None):
    """Largest building of cfg whose module fits P and building fits B, with car demand met."""
    pp = [q for q in parts(P_ft) if q.area > 50000]
    if not pp:
        return None
    rings, origin = sitefit.normalise(pp)
    best = None
    for n in range(1, max_bays + 1):
        depth, car_depth = cross_section(cfg, n)
        Ls = [x for x in range(int(cfg["Lmin"]), int(cfg["Lmax"]) + 1, 20) if length_cap is None or x <= length_cap]
        if not Ls:
            return None
        lat = sorted({float(module_length(cfg, L, n)) for L in Ls})
        cap = None
        for _ in range(40):
            lat_c = [x for x in lat if cap is None or x <= cap]
            if not lat_c:
                break
            got = sitefit.fit_lengths(rings, [(float(depth), lat_c)]).get(float(depth))
            if not got:
                break
            Lmod, pl = got
            L = Lmod - (module_length(cfg, 0, n))
            cands = []
            for flip in (False, True):
                fr = Frame(Lmod, depth, pl, origin, flip)
                p = build_plan(cfg, L, n, fr, depth, car_depth, rules["parking"])
                if not B_ft.buffer(0.5).contains(p.building):
                    continue
                score = 0.0
                if prefer_dock_away is not None and p.courts:
                    score = min(c.centroid.distance(prefer_dock_away) for c in p.courts)
                cands.append((score, p))
            if cands:
                p = max(cands, key=lambda t: t[0])[1]
                if p.car_stalls >= p.cars_required:
                    if best is None or p.sf > best.sf:
                        best = p
                    break
                break   # car demand unmet at this n: try more bays
            smaller = [x for x in lat if x < Lmod]
            cap = smaller[-1] if smaller else None
            if cap is None:
                break
        if best is not None:
            return best
    return best

# ---------------------------------------------------------------------------
# Pond, drop yards, access
# ---------------------------------------------------------------------------
def place_rect_lowest(region, Lr, Wr, elev, angles, step=40.0):
    if region.is_empty:
        return None
    pts = sorted(((z, p) for p, z in elev.items() if region.contains(Point(p))), key=lambda t: t[0])
    if not pts:   # no DEM: scan a grid
        minx, miny, maxx, maxy = region.bounds
        pts = [(0, (x, y)) for x in range(int(minx), int(maxx), 40) for y in range(int(miny), int(maxy), 40)]
    base = box(-Lr / 2, -Wr / 2, Lr / 2, Wr / 2)
    for _, (x, y) in pts[:1500]:
        for a in angles:
            r = affinity.translate(affinity.rotate(base, a, origin=(0, 0)), x, y)
            if region.contains(r):
                return r
    return None

def detention(imp_ac, perv_ac, hsg):
    ki, kp = HSG_K.get((hsg or "B")[:1], HSG_K["B"])
    return 1.15 * (ki * imp_ac + kp * perv_ac)

def driveway_points(front_ft, target_pt, other_pt, spacing):
    """Truck point nearest target, car point nearest other at >= spacing along the frontage."""
    if front_ft is None or front_ft.is_empty:
        return None, None, "no frontage"
    lines = [ln for ln in getattr(front_ft, "geoms", [front_ft]) if ln.length > 1]
    best_t = min(((ln.distance(target_pt), ln) for ln in lines), key=lambda t: t[0])[1]
    t_s = best_t.project(target_pt)
    t_s = min(max(t_s, 95), best_t.length - 95) if best_t.length >= 190 else best_t.length / 2
    tpt = best_t.interpolate(t_s)
    cands = []
    for ln in lines:
        for k in range(0, int(ln.length) + 1, 20):
            q = ln.interpolate(k)
            if ln is best_t and abs(k - t_s) < spacing:
                continue
            if ln is not best_t and q.distance(tpt) < 0.5 * spacing:
                continue
            if 45 <= k <= ln.length - 45:
                cands.append((q.distance(other_pt), q))
    cpt = min(cands, key=lambda t: t[0])[1] if cands else None
    note = "" if best_t.length >= 190 else f"truck driveway frontage only {best_t.length:.0f} ft (needs ~190 ft with 75 ft radii)"
    return tpt, cpt, note

# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------
def plan_site(sid, title, g_wgs, meta, extra_cut_wgs=None, configs=None, second_building=True):
    plane = Plane(g_wgs)
    parcel = clean(plane.ft(shapely.make_valid(g_wgs)), 1.0)
    jname, rules = jur_rules(meta.get("jurisdiction", ""))
    flags, notes = [], []
    # constraints (screener layers)
    pj = plane.pj
    tot = pj(g_wgs).area or 1
    tiles = tile_envelopes([g_wgs.bounds]); region = expand(g_wgs.bounds, 0.02)
    cons = []
    for key in ("flood", "wetlands", "easements", "protected"):
        idx = layers.fetch(layers.LAYERS[key], tiles, region)
        x, _, _ = checks._overlap(g_wgs, pj, tot, idx)
        if x is not None:
            cons.append(plane.ft(x))
    constraints = unary_union(cons) if cons else Polygon()
    # transmission corridors
    tl = []
    for a, s in transmission(g_wgs):
        sf_ = plane.ft(s)
        if sf_.distance(parcel) < 300:
            tl.append((a, sf_.buffer(corridor_halfwidth(a.get("VOLTAGE")), cap_style=2)))
    trans = unary_union([c for _, c in tl]).intersection(parcel) if tl else Polygon()
    if not trans.is_empty:
        flags.append(("TRANSMISSION_EASEMENT_ASSUMED", 2, f"{len(tl)} mapped transmission line(s) cross or touch the parcel; corridor widths assumed by voltage ({', '.join(str(a.get('VOLTAGE')) + ' kV' for a, _ in tl)})"))
    cut = plane.ft(extra_cut_wgs).intersection(parcel) if extra_cut_wgs is not None else Polygon()
    # frontage
    fr = frontage.check_parcel(g_wgs)
    front_ft = plane.ft(fr.line) if fr.line is not None else None
    # neighbours
    neigh = [(a, s) for a, s in county_near(g_wgs) if s is not None and not s.equals(g_wgs) and s.intersection(g_wgs).area < 0.5 * s.area]
    edges = classify_edges(parcel, plane, neigh, front_ft)
    res_len = sum(ln.length for k, ln in edges if k.startswith("RES"))
    if res_len:
        flags.append(("RESIDENTIAL_EDGE", 2, f"about {res_len:,.0f} ft of the boundary touches residential parcels (county land-use codes); draft buffers applied"))
    # envelopes
    P = envelope(parcel, edges, rules["pave"]).difference(constraints).difference(trans).difference(cut)
    B = envelope(parcel, edges, rules["bldg"]).difference(constraints).difference(trans).difference(cut)
    P, B = clean(P), clean(B)
    # DEM
    elev, step = dem_samples(plane, parcel)
    steep = steep_cells(elev, step).intersection(parcel) if elev else Polygon()
    away = front_ft if front_ft is not None and not front_ft.is_empty else None
    cfgs = configs or (OPERATOR + MARKET)
    results = []
    for cfg in cfgs:
        cap, first = None, None
        for attempt in range(25):
            p = fit_config(cfg, P, B, rules, prefer_dock_away=away, length_cap=cap)
            if p is None:
                break
            out = finish(p, parcel, P, constraints, trans, cut, steep, elev, step, meta, rules, front_ft, plane)
            if first is None:
                first = (p, out)
            if out["pond_ok"]:
                results.append((p, out)); break
            cap = p.L - 40   # shrink until the pond fits
        else:
            p = None
        if (not results or results[-1][0].cfg is not cfg) and first is not None:
            results.append(first)   # largest plan, flagged: the sized pond does not fit beside it
        # second order: pond first, sized from a conservative impervious estimate (80% of the paving envelope)
        pa = P.area / SQFT_PER_AC
        v_est = detention(0.8 * pa, max(parcel.area / SQFT_PER_AC - 0.8 * pa, 0), meta.get("hsg"))
        npd = 1 if v_est <= 12 else 2
        w0, Lf, Wf, _ = pond_dims(v_est / npd)
        preg = parcel.buffer(-10).difference(constraints).difference(cut).intersection(P.buffer(rules["pave"].get("ROW", 10) + 1))
        fixed = []
        for _k in range(npd):
            r = place_rect_lowest(preg, Lf, Wf, elev, [0, 90, 45, 135, 30, 60, 120, 150])
            if r is None:
                break
            fixed.append(r); preg = preg.difference(r.buffer(20))
        if len(fixed) == npd:
            P2 = clean(P.difference(unary_union(fixed).buffer(15)))
            B2 = clean(B.difference(unary_union(fixed).buffer(15)))
            p2 = fit_config(cfg, P2, B2, rules, prefer_dock_away=away)
            if p2 is not None:
                out2 = finish(p2, parcel, P2, constraints, trans, cut, steep, elev, step, meta, rules, front_ft, plane,
                              fixed_ponds=fixed, fixed_cap=v_est)
                out2["pond_first"] = True
                prev = [t for t in results if t[0].cfg is cfg]
                if not prev or (out2["pond_ok"] and (not prev[-1][1]["pond_ok"] or p2.sf > prev[-1][0].sf)):
                    results = [t for t in results if t[0].cfg is not cfg] + [(p2, out2)]
    if not results:
        return {"id": sid, "title": title, "fits": False, "jurisdiction_rules": jname, "flags": flags + [("NO_FIT", 1, "no configuration fits the envelope")],
                "inputs": inputs_summary(parcel, P, B, constraints, trans, cut, steep, front_ft, fr, edges)}, None
    def key(t):
        p, o = t
        return (o["pond_ok"], p.sf)
    results.sort(key=key, reverse=True)
    best_p, best_o = results[0]
    op = [t for t in results if t[0].cfg["operator"]]
    second = None
    if second_building and best_o["pond_ok"]:
        # order A: second building first (ignoring pond 1), then re-site ponds for the combined runoff
        usedA = unary_union([best_p.module.buffer(40)] + [d.buffer(15) for d in best_o["drops"]])
        PA, BA = clean(P.difference(usedA)), clean(B.difference(usedA))
        if PA.area > 4 * SQFT_PER_AC:
            candsA = [q for q in (fit_config(c, PA, BA, rules, prefer_dock_away=away) for c in cfgs) if q is not None and q.sf >= 50000]
            for p2 in sorted(candsA, key=lambda q: -q.sf):
                imp = best_o["imp_ac"] + p2.module.area / SQFT_PER_AC
                v_tot = detention(imp, max(parcel.area / SQFT_PER_AC - imp, 0), meta.get("hsg"))
                npd = 2 if v_tot <= 16 else 3
                _, Lf, Wf, _ = pond_dims(v_tot / npd)
                reg = (parcel.buffer(-10).difference(constraints).difference(cut).difference(usedA).difference(p2.module.buffer(15))
                       .intersection(P.buffer(rules["pave"].get("ROW", 10) + 1)))
                placed = []
                for _k in range(npd):
                    r = place_rect_lowest(reg, Lf, Wf, elev, [0, 90, 45, 135, p2.frame.pl.angle_deg, p2.frame.pl.angle_deg + 90])
                    if r is None:
                        break
                    placed.append(r); reg = reg.difference(r.buffer(20))
                if len(placed) == npd:
                    second = {"plan": p2, "ponds": placed, "v_tot": v_tot, "imp_ac": imp, "need": v_tot, "replace_ponds": True}
                    break
    if second is None and second_building and best_o["pond_ok"]:
        used = unary_union([best_p.module.buffer(40)] + [d.buffer(15) for d in best_o["drops"]] + [q.buffer(15) for q in best_o["ponds"]])
        P_rem, B_rem = clean(P.difference(used)), clean(B.difference(used))
        if P_rem.area > 4 * SQFT_PER_AC:
            cands2 = [q for q in (fit_config(c, P_rem, B_rem, rules, prefer_dock_away=away) for c in cfgs) if q is not None and q.sf >= 50000]
            for p2 in sorted(cands2, key=lambda q: -q.sf):
                imp = best_o["imp_ac"] + p2.module.area / SQFT_PER_AC
                v_tot = detention(imp, max(parcel.area / SQFT_PER_AC - imp, 0), meta.get("hsg"))
                need = max(v_tot - best_o["pond_cap"], 0.5)
                _, Lf, Wf, _ = pond_dims(need)
                reg = (parcel.buffer(-10).difference(constraints).difference(cut).difference(used).difference(p2.module.buffer(15))
                       .intersection(P.buffer(rules["pave"].get("ROW", 10) + 1)))
                r = place_rect_lowest(reg, Lf, Wf, elev, [p2.frame.pl.angle_deg, p2.frame.pl.angle_deg + 90, 0, 90, 45, 135])
                if r is not None:
                    second = {"plan": p2, "ponds": [r], "v_tot": v_tot, "imp_ac": imp, "need": need, "replace_ponds": False}
                    break
    rec = report(sid, title, best_p, best_o, parcel, P, B, meta, rules, jname, flags, fr, elev, steep)
    if second:
        p2 = second["plan"]
        rec["second_building"] = {"configuration": p2.cfg["name"], "operator_prototype": p2.cfg["operator"], "L_ft": p2.L, "D_ft": p2.D,
                                  "sf": rnd(p2.sf, 1000), "doors": p2.doors, "trailers_court_rows": p2.court_stalls, "trailers_target": p2.trailers_target,
                                  "cars": p2.car_stalls, "cars_required": p2.cars_required, "pond_storage_basis": "ponds re-sited for both buildings" if second["replace_ponds"] else f"extra pond {round(second['need'] * 2) / 2} ac-ft"}
        rec["total_sf"] = rnd(best_p.sf + p2.sf, 1000)
        rec["coverage"]["impervious_ac"] = round(second["imp_ac"], 1)
        rec["coverage"]["impervious_pct"] = round(second["imp_ac"] / (parcel.area / SQFT_PER_AC) * 100)
        rec["coverage"]["sf_per_gross_ac"] = rnd((best_p.sf + p2.sf) / (parcel.area / SQFT_PER_AC), 100)
        rec["coverage"]["gross_far"] = round((best_p.building.area + p2.building.area) / parcel.area, 2)
        rec["detention"]["storage_acft"] = round(second["v_tot"] * 2) / 2
        if second["replace_ponds"]:
            best_o["ponds"] = list(second["ponds"])
        else:
            best_o["ponds"] = list(best_o["ponds"]) + list(second["ponds"])
        rec["detention"]["ponds"] = len(best_o["ponds"])
        rec["detention"]["footprint_ac_each"] = round(sum(q.area for q in best_o["ponds"]) / len(best_o["ponds"]) / SQFT_PER_AC, 1)
        rec["flags"][2].append({"flag": "SECOND_BUILDING_ACCESS", "note": "the second building shares the site driveways; its internal truck and car connection and its own fire access are not drawn"})
        if p2.trailers_target > p2.court_stalls:
            rec["flags"][2].append({"flag": "TRAILER_SHORT_2", "note": f"second building: {p2.court_stalls} court-row trailer stalls against a target of {p2.trailers_target}"})
    else:
        rec["total_sf"] = rec["building"]["sf"]
    rec["alternatives"] = [{"config": p.cfg["name"], "operator": p.cfg["operator"], "sf": round(p.sf, -3), "L": p.L, "D": p.D,
                            "pond_fits": o["pond_ok"], "doors": p.doors, "trailers": o["trailers_total"], "cars": p.car_stalls} for p, o in results]
    rec["operator_best"] = rec["alternatives"][[i for i, t in enumerate(results) if t[0].cfg["operator"]][0]] if op else None
    rec["inputs"] = inputs_summary(parcel, P, B, constraints, trans, cut, steep, front_ft, fr, edges)
    svg = render(sid, title, parcel, P, B, constraints, trans, cut, steep, edges, front_ft, best_p, best_o, rec, plane,
                 extra=[second["plan"]] if second else [])
    return rec, svg

def finish(p, parcel, P, constraints, trans, cut, steep, elev, step, meta, rules, front_ft, plane, fixed_ponds=None, fixed_cap=None):
    used = p.module
    # drop yards for trailer shortfall
    trailers = p.court_stalls
    drops = []
    free = P.difference(used.buffer(20))
    angles = [p.frame.pl.angle_deg, p.frame.pl.angle_deg + 90]
    need = max(0, p.trailers_target - trailers)
    for (Lr, Wr, n) in (DROP_MODULE, DROP_MODULE, DROP_HALF, DROP_HALF):
        if need <= 0:
            break
        r = place_rect_lowest(free, Lr, Wr, {}, angles)
        if r is None:
            continue
        drops.append(r); trailers += n; need -= n
        free = free.difference(r.buffer(30))
    imp = unary_union([used] + drops)
    imp_ac = imp.area / SQFT_PER_AC
    perv_ac = max(parcel.area / SQFT_PER_AC - imp_ac, 0)
    v = detention(imp_ac, perv_ac, meta.get("hsg"))
    nponds = 1 if v <= 12 else 2
    w0, Lf, Wf, _ = pond_dims(v / nponds)
    pond_region = parcel.buffer(-10).difference(used.buffer(15)).difference(constraints).difference(cut)
    for d in drops:
        pond_region = pond_region.difference(d.buffer(15))
    # ponds may not sit in residential buffers; reuse the paving envelope's edge offsets but allow ROW strips
    pond_region = pond_region.intersection(P.buffer(rules["pave"].get("ROW", 10) + 1))
    ponds = []
    region = pond_region
    if fixed_ponds is not None:
        ponds = list(fixed_ponds)
        pond_ok = fixed_cap is not None and fixed_cap >= v
        nponds = len(ponds)
    for k in range(nponds if fixed_ponds is None else 0):
        r = place_rect_lowest(region, Lf, Wf, elev, [p.frame.pl.angle_deg, p.frame.pl.angle_deg + 90, 0, 90, 45, 135])
        if r is None:
            break
        ponds.append(r); region = region.difference(r.buffer(20))
    if fixed_ponds is None:
        pond_ok = len(ponds) == nponds
    # access
    court_ends = [Point(q) for c in p.courts for q in c.exterior.coords[:4]]
    car_c = p.car_fields[0].centroid if p.car_fields else used.centroid
    target = min(court_ends, key=lambda q: q.distance(front_ft)) if front_ft is not None and not front_ft.is_empty else used.centroid
    tpt, cpt, note = driveway_points(front_ft, target, car_c, 250)
    flags = []
    if tpt is None:
        flags.append(("ACCESS_NONE", 1, "no public road frontage from the GDOT check"))
    elif note:
        flags.append(("ACCESS_CONSTRAINED", 2, note))
    if tpt is not None and cpt is None:
        flags.append(("ACCESS_SHARED", 2, "one driveway only: cars split off inside the throat"))
    routes = []
    if tpt is not None:
        a, b = nearest_points(tpt, unary_union(p.roads + p.courts))
        routes.append(("truck", LineString([tpt, b])))
    if cpt is not None:
        a, b = nearest_points(cpt, unary_union(p.car_fields))
        routes.append(("car", LineString([cpt, b])))
    # fire: two accesses separated by half the diagonal
    diag = math.hypot(P.bounds[2] - P.bounds[0], P.bounds[3] - P.bounds[1])
    if tpt is not None and cpt is not None and tpt.distance(cpt) < 0.5 * diag:
        flags.append(("FIRE_SECOND_ACCESS", 2, f"the two driveways are {tpt.distance(cpt):,.0f} ft apart, under half the site diagonal ({0.5*diag:,.0f} ft): an emergency-only link or fire-official approval needed"))
    # pad relief
    pad = [z for (x, y), z in elev.items() if p.building.contains(Point(x, y))]
    relief = (max(pad) - min(pad)) if len(pad) > 3 else None
    cutfill = None
    if len(pad) > 3:
        mean = sum(pad) / len(pad)
        cutfill = p.building.area * (sum(abs(z - mean) for z in pad) / len(pad)) / 2 / 27   # cubic yards, balanced, building pad only
    steep_bldg = (p.building.intersection(steep).area / p.building.area * 100) if not steep.is_empty else 0.0
    return {"pond_cap": (fixed_cap if fixed_ponds is not None else v), "drops": drops, "trailers_total": trailers, "imp_ac": imp_ac, "perv_ac": perv_ac, "v_det": v, "ponds": ponds,
            "pond_ok": pond_ok, "pond_each": (w0, Lf, Wf, v / nponds), "tpt": tpt, "cpt": cpt, "routes": routes,
            "flags": flags, "relief": relief, "cutfill_cy": cutfill, "steep_bldg_pct": steep_bldg}

def inputs_summary(parcel, P, B, constraints, trans, cut, steep, front_ft, fr, edges):
    return {"parcel_ac": round(parcel.area / SQFT_PER_AC, 1), "pave_envelope_ac": round(P.area / SQFT_PER_AC, 1),
            "building_envelope_ac": round(B.area / SQFT_PER_AC, 1), "mapped_constraints_ac": round(constraints.area / SQFT_PER_AC, 2),
            "transmission_ac": round(trans.area / SQFT_PER_AC, 2), "test_cut_ac": round(cut.area / SQFT_PER_AC, 2),
            "steep_ac": round(steep.area / SQFT_PER_AC, 2), "frontage_ft": fr.feet, "frontage_road": fr.road,
            "edges_ft": {k: round(sum(ln.length for kk, ln in edges if kk == k)) for k in {k for k, _ in edges}}}

def rnd(x, base):
    return int(base * round(float(x) / base))

def report(sid, title, p, o, parcel, P, B, meta, rules, jname, flags0, fr, elev, steep):
    flags = list(flags0) + o["flags"]
    gross_ac = parcel.area / SQFT_PER_AC
    if not o["pond_ok"]:
        flags.append(("POND_DOES_NOT_FIT", 1 if gross_ac > 25 else 2, "no surface pond fits beside this building: underground detention (vaults, allowed up to 25 ac drainage, no runoff-reduction credit, costly) or a smaller building"))
    if o["trailers_total"] < p.trailers_target:
        flags.append(("TRAILER_SHORT", 2, f"{o['trailers_total']} trailer stalls against a target of {p.trailers_target}"))
    h = (32 if p.sf < 200000 else 36) + 10
    if rules.get("height") and h > rules["height"]:
        flags.append(("HEIGHT_VARIANCE", 1, f"assumed building height {h} ft over the draft cap {rules['height']} ft"))
    cov = rules.get("cover")
    if cov:
        kind, capv = cov
        val = p.building.area / parcel.area if kind == "building" else (p.building.area + sum(c.area for c in p.car_fields)) / parcel.area if kind == "building+parking" else o["imp_ac"] / gross_ac
        if val > capv:
            flags.append(("COVERAGE_OVER_CAP", 1, f"{kind} coverage {val:.0%} over the draft cap {capv:.0%}"))
    if p.cfg["type"] == "rear" and p.D > 300 and False:
        pass
    if o["steep_bldg_pct"] > 10:
        flags.append(("GRADING_RISK", 2, f"{o['steep_bldg_pct']:.0f}% of the building pad is steeper than 15% (lidar)"))
    if o["relief"] and o["relief"] > 25:
        flags.append(("PAD_RELIEF", 2, f"about {o['relief']:.0f} ft of relief across the building pad"))
    if jname.startswith("atlanta"):
        flags.append(("ATL_PARKING_VERIFY", 2, "Atlanta's 1/600 SF parking minimum governs here; verify current code"))
    flags.append(("POND_SITE_FROM_LIDAR", 3, "pond placed on the lowest lidar ground that fits; no outfall or grading design"))
    flags.append(("ZONING_DRAFT", 2, f"setbacks and buffers are draft screening values for {jname}; zoning_rules.json is the authority"))
    flags.append(("STREAM_PIPELINE_UNMODELLED", 2, "stream buffers and petroleum pipelines are not mapped here"))
    sev = {1: [], 2: [], 3: []}
    for f in flags:
        sev[f[1]].append({"flag": f[0], "note": f[2]})
    imp_pct = o["imp_ac"] / gross_ac * 100
    return {
        "id": sid, "title": title, "fits": True, "jurisdiction_rules": jname,
        "configuration": p.cfg["name"], "operator_prototype": p.cfg["operator"],
        "building": {"L_ft": p.L, "D_ft": p.D, "sf": rnd(p.sf, 1000), "sf_exact": p.sf, "office_sf": rnd(OFFICE_SHARE * p.sf, 500),
                     "clear_height_ft": 32 if p.sf < 200000 else 36},
        "doors": p.doors, "door_cap": int((p.L - 2 * END_CLEAR) // DOOR_SP) * (1 if p.cfg["type"] == "rear" else 2),
        "trailers": {"court_rows": p.court_stalls, "drop_yards": o["trailers_total"] - p.court_stalls, "total": o["trailers_total"],
                     "target": p.trailers_target},
        "cars": {"provided": p.car_stalls, "required": p.cars_required, "basis": p.cars_rule, "ada": ada_count(p.car_stalls), "per_1000_sf": round(p.car_stalls / p.sf * 1000, 2)},
        "coverage": {"gross_ac": round(gross_ac, 1), "building_ac": round(p.building.area / SQFT_PER_AC, 1),
                     "sf_per_gross_ac": rnd(p.sf / gross_ac, 100), "gross_far": round(p.building.area / parcel.area, 2),
                     "impervious_ac": round(o["imp_ac"], 1), "impervious_pct": round(imp_pct), "net_ac": round(P.area / SQFT_PER_AC, 1)},
        "detention": {"storage_acft": round(o["v_det"] * 2) / 2, "ponds": len(o["ponds"]), "pond_bottom_ft": [round(o["pond_each"][0]), round(2 * o["pond_each"][0])],
                      "footprint_ac_each": round(o["pond_each"][1] * o["pond_each"][2] / SQFT_PER_AC, 1), "fits": o["pond_ok"],
                      "basis": f"1.15 x (k_imp x impervious + k_perv x pervious), HSG {meta.get('hsg') or 'B'}; 6 ft deep, 4:1, L=2W, 1 ft freeboard, 15 ft rim; screening only (+/-30%)"},
        "access": {"truck_driveway": o["tpt"] is not None, "car_driveway": o["cpt"] is not None,
                   "frontage_ft": fr.feet, "road": fr.road,
                   "truck_route_ft": round(sum(l.length for k, l in o["routes"] if k == "truck"))},
        "grading": {"pad_relief_ft": round(o["relief"]) if o["relief"] else None, "pad_cut_fill_cy": rnd(o["cutfill_cy"], 1000) if o["cutfill_cy"] else None,
                    "steep_share_of_pad_pct": round(o["steep_bldg_pct"])},
        "flags": sev,
    }

# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
C = {"bg": "#ffffff", "parcel": "#f4f1ea", "ink": "#1f2a30", "env": "#7a8a94", "cons": "#9cc3e0", "trans": "#e8b04b", "cut": "#c92a2a",
     "steep": "#b07d4f", "bldg": "#e9e4da", "bldg_s": "#4a3f35", "office": "#c9b8a0", "court": "#c4c9cd", "apron": "#b8bec3",
     "trailer": "#a7adb3", "car": "#dfe3f1", "road": "#bfc5ca", "pond": "#8ccfc4", "pond_s": "#2a7f74", "drive": "#d9480f", "row": "#d9480f"}

def render(sid, title, parcel, P, B, constraints, trans, cut, steep, edges, front_ft, p0, o, rec, plane, extra=()):
    W, PLAN, M = 1180, 760, 20
    minx, miny, maxx, maxy = parcel.bounds
    span = max(maxx - minx, maxy - miny) * 1.04
    s = PLAN / span
    ox = M + (PLAN - (maxx - minx) * s) / 2
    oy = M + (PLAN - (maxy - miny) * s) / 2
    X = lambda x: ox + (x - minx) * s
    Y = lambda y: oy + (maxy - y) * s
    def path(g):
        out = []
        for q in sitefit.polygon_parts(g) if g is not None and not g.is_empty else []:
            for ring in (q.exterior, *q.interiors):
                pts = [f"{X(x):.1f},{Y(y):.1f}" for x, y, *_ in ring.coords[:-1]]
                if len(pts) >= 3:
                    out.append("M" + " L".join(pts) + " Z")
        return " ".join(out)
    def lpath(g):
        out = []
        for ln in getattr(g, "geoms", [g]):
            if ln.geom_type == "LineString" and len(ln.coords) > 1:
                out.append("M" + " L".join(f"{X(x):.1f},{Y(y):.1f}" for x, y, *_ in ln.coords))
            elif hasattr(ln, "geoms"):
                out.append(lpath(ln))
        return " ".join(out)
    def poly(g, fill, stroke="none", sw=0, op=1.0, extra=""):
        d = path(g)
        return f'<path d="{d}" fill="{fill}" fill-opacity="{op}" stroke="{stroke}" stroke-width="{sw}" fill-rule="evenodd" {extra}/>' if d else ""
    def esc(t):
        return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = [f'<defs><pattern id="tx" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="{C["trans"]}" stroke-width="3"/></pattern>'
           f'<pattern id="sx" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><line x1="0" y1="0" x2="0" y2="6" stroke="{C["steep"]}" stroke-width="1.2"/></pattern>'
           f'<pattern id="cx" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="{C["cut"]}" stroke-width="2"/></pattern></defs>']
    out.append(poly(parcel, C["parcel"]))
    out.append(poly(constraints.intersection(parcel), C["cons"], op=0.85))
    out.append(poly(steep, "url(#sx)", op=0.9))
    out.append(poly(trans, "url(#tx)", op=0.9))
    out.append(poly(cut, "url(#cx)", op=0.8))
    out.append(poly(P, "none", C["env"], 1, extra='stroke-dasharray="6 4"'))
    for g in o["drops"]:
        out.append(poly(g, C["trailer"], C["ink"], 0.5))
    for p in [p0, *extra]:
        for g in p.roads:
            out.append(poly(g, C["road"]))
        for g in p.courts:
            out.append(poly(g, C["court"]))
            # apron: first 60 ft of the court along the dock wall
        for g in p.trailer_rows:
            out.append(poly(g, C["trailer"]))
        for g in p.car_fields:
            out.append(poly(g, C["car"]))
        # stall ticks: trailer rows and car fields (every ~12/9 ft, drawn every 4th to keep the file light)
        fr = p.frame
        ticks = []
        if p.cfg["type"] == "rear":
            dep, car_d = cross_section(p.cfg, p.n_bays)
            v0 = car_d + p.D + COURT
            for u in range(int(TRUCK_ROAD), int(fr.Lmod - TRUCK_ROAD), 24):
                ticks.append(fr.line(u, v0, u, v0 + TRAILER_ROW))
            row_starts = car_rows_layout(p.n_bays)[0]
            for vrow in row_starts:
                for u in range(int(TRUCK_ROAD + 24), int(TRUCK_ROAD + p.L - 24), 18):
                    ticks.append(fr.line(u, vrow, u, vrow + CAR_ROW))
        else:
            dep, _ = cross_section(p.cfg, p.n_bays)
            for u in range(int(fr.Lmod - TRUCK_ROAD - p.L), int(fr.Lmod - TRUCK_ROAD), 24):
                ticks += [fr.line(u, 0, u, TRAILER_ROW), fr.line(u, dep - TRAILER_ROW, u, dep)]
        out.append(f'<path d="{lpath(unary_union(ticks))}" stroke="#ffffff" stroke-width="0.8" fill="none"/>' if ticks else "")
        out.append(poly(p.building, C["bldg"], C["bldg_s"], 1.6))
        for g in p.offices:
            out.append(poly(g, C["office"], C["bldg_s"], 0.6))
        # dock doors: short ticks along the dock wall(s)
        doors = []
        if p.cfg["type"] == "rear":
            dep, car_d = cross_section(p.cfg, p.n_bays)
            vw = car_d + p.D
            n = p.doors
            if n:
                u0 = TRUCK_ROAD + END_CLEAR; span_u = p.L - 2 * END_CLEAR
                for k in range(n):
                    u = u0 + span_u * (k + 0.5) / n
                    doors.append(fr.line(u, vw, u, vw + 12))
        else:
            uL = fr.Lmod - TRUCK_ROAD - p.L
            for side_v, sgn in ((TRAILER_ROW + COURT, -1), (TRAILER_ROW + COURT + p.D, 1)):
                n = p.doors // 2
                for k in range(n):
                    u = uL + END_CLEAR + (p.L - 2 * END_CLEAR) * (k + 0.5) / max(n, 1)
                    doors.append(fr.line(u, side_v, u, side_v + sgn * 12))
        if doors:
            out.append(f'<path d="{lpath(unary_union(doors))}" stroke="{C["bldg_s"]}" stroke-width="1.4" fill="none"/>')
    for g in o["ponds"]:
        inner = g.buffer(-RIM)
        out.append(poly(g, "#d7ece8", C["pond_s"], 0.8))
        out.append(poly(inner, C["pond"], C["pond_s"], 1.2))
    if front_ft is not None and not front_ft.is_empty:
        out.append(f'<path d="{lpath(front_ft)}" stroke="{C["row"]}" stroke-width="4" fill="none" stroke-linecap="round"/>')
    for kind, ln in o["routes"]:
        out.append(f'<path d="{lpath(ln)}" stroke="{"#5c6670" if kind == "truck" else "#8a949c"}" stroke-width="{max(2.5, (40 if kind == "truck" else 24) * s)}" fill="none"/>')
    for pt, lab in ((o["tpt"], "T"), (o["cpt"], "C")):
        if pt is not None:
            out.append(f'<circle cx="{X(pt.x):.1f}" cy="{Y(pt.y):.1f}" r="9" fill="{C["drive"]}"/><text x="{X(pt.x):.1f}" y="{Y(pt.y)+4:.1f}" font-size="11" font-weight="bold" fill="#fff" text-anchor="middle">{lab}</text>')
    out.append(poly(parcel, "none", C["ink"], 1.6))
    for p in [p0, *extra]:
        c = p.building.centroid
        out.append(f'<text x="{X(c.x):.1f}" y="{Y(c.y)+4:.1f}" font-size="12" font-weight="bold" text-anchor="middle" fill="{C["ink"]}">{rnd(p.sf, 1000):,} SF</text>')
    # north arrow, scale
    ax, ay = M + PLAN - 20, M + 10
    out.append(f'<path d="M{ax},{ay} L{ax+8},{ay+26} L{ax},{ay+20} L{ax-8},{ay+26} Z" fill="{C["ink"]}"/><text x="{ax}" y="{ay+40}" font-size="12" text-anchor="middle" font-weight="bold">N</text>')
    bar = 10 ** math.floor(math.log10(span * 0.25)); bar = bar * (5 if bar * 5 <= span * 0.3 else 2 if bar * 2 <= span * 0.3 else 1)
    out.append(f'<path d="M{M},{M+PLAN+8} L{M+bar*s:.1f},{M+PLAN+8}" stroke="{C["ink"]}" stroke-width="3"/><text x="{M+bar*s+6:.1f}" y="{M+PLAN+12}" font-size="11">{bar:,.0f} ft</text>')
    # right panel
    tx, ty = M + PLAN + 24, M + 6
    lines = []
    b = rec["building"]; tr = rec["trailers"]; ca = rec["cars"]; cv = rec["coverage"]; de = rec["detention"]; acc = rec["access"]; gr = rec["grading"]
    lines.append(("h", title))
    lines.append(("t", f"{rec['configuration']}{' (operator prototype)' if rec['operator_prototype'] else ' (market depth, not an operator prototype)'}"))
    lines.append(("b", f"{b['sf']:,} SF   {int(b['L_ft'])} x {int(b['D_ft'])} ft   {b['clear_height_ft']} ft clear (assumed)"))
    lines.append(("t", f"Dock doors {rec['doors']} (wall holds {rec['door_cap']})   office ~{b['office_sf']:,} SF"))
    if rec.get("second_building"):
        sb = rec["second_building"]
        lines.append(("b", f"2nd building: {sb['configuration']}, {sb['sf']:,} SF ({int(sb['L_ft'])} x {int(sb['D_ft'])} ft), {sb['doors']} doors, {sb['cars']} cars; total {rec['total_sf']:,} SF"))
    lines.append(("t", f"Trailer stalls {tr['total']} (court rows {tr['court_rows']}, drop yard {tr['drop_yards']}) vs target {tr['target']}"))
    lines.append(("t", f"Car stalls {ca['provided']} (required {ca['required']}; ADA {ca['ada']}); {ca['per_1000_sf']}/1,000 SF"))
    lines.append(("t", f"Site {cv['gross_ac']} ac   {cv['sf_per_gross_ac']:,} SF per gross ac   FAR {cv['gross_far']}"))
    lines.append(("t", f"Impervious {cv['impervious_ac']} ac ({cv['impervious_pct']}%)"))
    lines.append(("t", f"Detention {de['storage_acft']} ac-ft in {de['ponds']} pond(s), ~{de['footprint_ac_each']} ac each{'' if de['fits'] else ' - DOES NOT FIT'}"))
    lines.append(("t", f"Access: {'truck (T)' if acc['truck_driveway'] else 'no truck driveway'}{' + car (C)' if acc['car_driveway'] else ''} on {acc['road'] or 'no road'}; frontage {acc['frontage_ft'] or 0:,} ft"))
    if gr["pad_relief_ft"] is not None:
        lines.append(("t", f"Pad relief ~{gr['pad_relief_ft']} ft; balanced pad cut/fill ~{gr['pad_cut_fill_cy'] or 0:,} CY; steep pad {gr['steep_share_of_pad_pct']}%"))
    lines.append(("h2", "Flags"))
    for sv, lab in ((1, "!!"), (2, "!"), (3, "i")):
        for f in rec["flags"][sv]:
            if f["flag"] in ("ZONING_DRAFT", "STREAM_PIPELINE_UNMODELLED", "POND_SITE_FROM_LIDAR"):
                continue
            lines.append(("f" + str(sv), f"{lab} {f['flag']}: {f['note']}"))
    lines.append(("h2", "Legend"))
    legend = [(C["bldg"], "building (office pods darker), dock doors as ticks"), (C["court"], "truck court and truck roads"), (C["trailer"], "trailer stalls / drop yard"),
              (C["car"], "car parking (stall ticks)"), (C["pond"], "detention pond (rim around)"), (C["cons"], "floodplain, wetlands, easements, protected"),
              ("url(#tx)", "transmission corridor (width assumed)"), ("url(#sx)", "steeper than 15% (lidar)"), ("url(#cx)", "test cut"),
              ("dash", "paving envelope after draft setbacks/buffers"), (C["row"], "GDOT road frontage; T/C driveways")]
    yy = ty
    body = []
    for kind, txt in lines:
        size = {"h": 15, "h2": 13}.get(kind, 12)
        weight = "bold" if kind in ("h", "h2", "b") else "normal"
        col = {"f1": "#b3261e", "f2": "#8a5a00"}.get(kind, C["ink"])
        for k, seg in enumerate(textwrap.wrap(txt, 62 if size == 12 else 52) or [""]):
            yy += size + 5
            body.append(f'<text x="{tx}" y="{yy}" font-size="{size}" font-weight="{weight}" fill="{col}">{esc(seg)}</text>')
        if kind in ("h2",):
            yy += 2
    for fill, txt in legend:
        yy += 18
        if fill == "dash":
            body.append(f'<path d="M{tx},{yy-5} L{tx+18},{yy-5}" stroke="{C["env"]}" stroke-dasharray="6 4" stroke-width="1.5"/>')
        else:
            body.append(f'<rect x="{tx}" y="{yy-11}" width="18" height="11" fill="{fill}" stroke="{C["ink"]}" stroke-width="0.6"/>')
        body.append(f'<text x="{tx+26}" y="{yy-1}" font-size="11.5">{esc(txt)}</text>')
    yy += 22
    for seg in textwrap.wrap("Screening test fit from public data: not a survey, civil design, or zoning determination. Zoning values are draft; detention is a +/-30% estimate; access, fire and grading are indicative.", 70):
        body.append(f'<text x="{tx}" y="{yy}" font-size="11" fill="#5d676e">{esc(seg)}</text>'); yy += 14
    H = int(max(M + PLAN + 30, yy + 20))
    head = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="sans-serif"><title>{esc(title)}</title><rect x="0" y="0" width="{W}" height="{H}" fill="{C["bg"]}"/>'
    return head + "".join(x for x in out if x) + "".join(body) + "</svg>"
