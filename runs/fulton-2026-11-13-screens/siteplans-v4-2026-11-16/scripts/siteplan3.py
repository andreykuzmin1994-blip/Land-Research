"""Screening-level industrial site planner, version 3.

Version 2 reviewed by industrial civil-engineer / developer reviewers
(scratchpad/research/review_syn.md). This version fixes the systemic problems
they found:
  1. cross-section variants: court-only or court + trailer row, cars along the
     office wall or at the building end, depths 160-400 (rear-load) and
     470-600 (cross-dock), lengths by L/D 2.0-4.2, partial last car row;
  2. truck and car routes found by grid search inside the parcel, around
     ponds, wetlands and buildings, priced up on steep ground, added to impervious;
  3. a gate: no road frontage = hypothetical plan, fire check whenever < 2 driveways;
  4. trucks enter the court ends at the front corners, cars reach the car field
     from its open edge without crossing truck pavement;
  5. driveways on merged frontage runs, full spacing, radius clearances;
  6. ponds on the development's side of mapped wetlands, below the pad, close by;
  7. second building gets its own route or is not counted;
  8. 25 ft buffer on mapped water/wetland/floodplain for paving; transmission
     corridors cut from the building envelope only;
  9. docks oriented away from residential and street edges, with flags;
 10. earthwork over the whole module; steep ground penalises the ranking;
 11. comp bands, L/D, door density, acreage mismatch flags;
 12. per-jurisdiction stall width and island allowance; 13. use-permit gates.
Everything zoning-related remains a DRAFT placeholder (zoning_rules.json is the authority).
"""
from __future__ import annotations
import heapq, json, math, sys, textwrap
from dataclasses import dataclass, field
from pathlib import Path

import shapely
from shapely.prepared import prep as _prep
from shapely.geometry import Polygon, MultiPolygon, Point, LineString, box, MultiLineString
from shapely.ops import unary_union, nearest_points, linemerge, snap
from shapely import affinity

sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, ".")
import siteplan2 as v2
from siteplan2 import (Plane, Frame, parts, clean, county_near, transmission, corridor_halfwidth, dem_samples, steep_cells,
                       classify_edges, envelope, pond_dims, detention, parking_required, parking_market, ada_count,
                       trailer_target, jur_rules, rnd, SQFT_PER_AC, sitefit, frontage, layers, checks, tile_envelopes, expand)

COURT, TRAILER_ROW, TRUCK_ROAD = 130, 55, 40
CAR_ROW, CAR_AISLE, WALK = 18, 26, 5
END_CLEAR, DOOR_SP, SF_PER_DOOR, OFFICE_SHARE = 60, 12, 5000, 0.02
DROP_MODULE, DROP_HALF = (600, 190, 100), (300, 190, 50)
RIM = 15.0
GRID = 20.0

def stall_width(jname):
    return 10 if jname.startswith("fairburn") else 9

def island_factor(jname):
    return 0.88 if jname.startswith(("south fulton", "fulton")) else 0.92 if jname.startswith("fairburn") else 1.0

def car_rows_layout(r):
    rows, v = [WALK], WALK + CAR_ROW + CAR_AISLE
    for k in range(2, r + 1):
        rows.append(v); v += CAR_ROW
        if k % 2 == 1:
            v += 24
    return rows, v

VARIANTS = []
for D in (160, 190, 230, 270, 310, 350, 400):
    for row in (True, False):
        for cars in ("side", "end"):
            VARIANTS.append({"type": "rear", "D": D, "row": row, "cars": cars, "operator": D == 260,
                             "name": f"rear-load {D} deep, {'court + trailer row' if row else 'court only'}, cars {'along office wall' if cars == 'side' else 'at building end'}"})
for row in (True, False):
    VARIANTS.append({"type": "rear", "D": 260, "row": row, "cars": "side", "operator": True,
                     "name": f"operator rear-load 260, {'court + trailer row' if row else 'court only'}"})
for D in (470, 520, 600):
    for row in (True, False):
        VARIANTS.append({"type": "cross", "D": D, "row": row, "cars": "end", "operator": D == 520,
                         "name": f"{'operator ' if D == 520 else ''}cross-dock {D} deep, {'courts + trailer rows' if row else 'courts only'}, cars at end"})

def length_range(v):
    D = v["D"]
    lo = max(220, int(1.5 * D) // 20 * 20)
    hi = min(1600, int(4.2 * D) // 20 * 20)
    if v["operator"] and v["type"] == "rear":
        lo, hi = 400, 640
    if v["operator"] and v["type"] == "cross":
        lo, hi = 600, 1000
    return lo, hi

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
    truck_entries: list      # points where the truck route must arrive (court-end corners)
    car_entries: list        # points on the car field's open edge
    sf: int
    doors: int
    court_stalls: int
    car_cap: int
    cars_required: int
    cars_rule: str
    trailers_target: int

def geometry_of(v, L, r):
    """(depth, module length) for variant v, building length L, r car rows."""
    D = v["D"]; row = TRAILER_ROW if v["row"] else 0
    cd = car_rows_layout(r)[1]
    if v["type"] == "rear":
        if v["cars"] == "side":
            return cd + D + COURT + row, L + 2 * TRUCK_ROAD
        fs = WALK + CAR_AISLE
        return fs + D + COURT + row, cd + 2 * TRUCK_ROAD + L
    return row + COURT + D + COURT + row, TRUCK_ROAD + L + TRUCK_ROAD + cd

def car_per_row(v, L, jname):
    sw = stall_width(jname)
    if v["type"] == "rear" and v["cars"] == "side":
        return int((L - 48) // sw)
    if v["type"] == "rear":
        return int((WALK + CAR_AISLE + v["D"] - 24) // sw)
    return int((v["D"] - 48) // sw)

def build(v, L, r, fr, jname, parking_rule):
    D = v["D"]; row = TRAILER_ROW if v["row"] else 0
    depth = fr.depth; Lmod = fr.Lmod
    courts, rows, cars, roads, offices, ticks, doors_g, tin, cin = [], [], [], [], [], [], [], [], []
    sw = stall_width(jname)
    if v["type"] == "rear":
        if v["cars"] == "side":
            cd = car_rows_layout(r)[1]
            uL, vB = TRUCK_ROAD, cd
            cars.append(fr.rect(uL, uL + L, 0, cd))
            for vr in car_rows_layout(r)[0]:
                for u in range(int(uL + 24), int(uL + L - 24), int(sw * 2)):
                    ticks.append(fr.line(u, vr, u, vr + CAR_ROW))
            cin.append(Point(fr.pt(uL + L / 2, 0)))
        else:
            cd = car_rows_layout(r)[1]
            fs = WALK + CAR_AISLE
            uL, vB = cd + TRUCK_ROAD, fs
            cars.append(fr.rect(0, cd, 0, fs + D))
            for vr in car_rows_layout(r)[0]:
                u0 = cd - vr - CAR_ROW
                for vv in range(24, int(fs + D - 24), int(sw * 2)):
                    ticks.append(fr.line(u0, vv, u0 + CAR_ROW, vv))
            cin.append(Point(fr.pt(0, (fs + D) / 2)))
            roads.append(fr.rect(cd + TRUCK_ROAD, cd + TRUCK_ROAD + L, 0, fs))     # aerial / fire lane along the office wall
        b = fr.rect(uL, uL + L, vB, vB + D)
        ue0, ue1 = uL - TRUCK_ROAD, uL + L + TRUCK_ROAD
        roads += [fr.rect(ue0, uL, 0, vB + D), fr.rect(uL + L, ue1, 0, vB + D)]
        courts.append(fr.rect(ue0, ue1, vB + D, vB + D + COURT))
        if row:
            rows.append(fr.rect(uL, uL + L, vB + D + COURT, depth))
            for u in range(int(uL), int(uL + L), 24):
                ticks.append(fr.line(u, vB + D + COURT, u, depth))
        tin += [Point(fr.pt(ue0 + TRUCK_ROAD / 2, 0)), Point(fr.pt(ue1 - TRUCK_ROAD / 2, 0))]
        pod = min(80.0, math.sqrt(OFFICE_SHARE * L * D / 2))
        offices += [fr.rect(uL, uL + pod, vB, vB + pod), fr.rect(uL + L - pod, uL + L, vB, vB + pod)]
        walls = 1
        n = 0
        cap = max(0, int((L - 2 * END_CLEAR) // DOOR_SP))
        court_stalls = int((L - 80) // 12) if row else 0
        dock_v = [(vB + D, 1)]
    else:
        cd = car_rows_layout(r)[1]
        uL = TRUCK_ROAD
        vB = row + COURT
        b = fr.rect(uL, uL + L, vB, vB + D)
        courts += [fr.rect(0, uL + L + TRUCK_ROAD, row, row + COURT), fr.rect(0, uL + L + TRUCK_ROAD, vB + D, vB + D + COURT)]
        if row:
            rows += [fr.rect(uL, uL + L, 0, row), fr.rect(uL, uL + L, depth - row, depth)]
            for u in range(int(uL), int(uL + L), 24):
                ticks += [fr.line(u, 0, u, row), fr.line(u, depth - row, u, depth)]
        roads += [fr.rect(0, uL, 0, depth), fr.rect(uL + L, uL + L + TRUCK_ROAD, 0, depth)]
        cars.append(fr.rect(uL + L + TRUCK_ROAD, Lmod, vB, vB + D))
        for vr in car_rows_layout(r)[0]:
            u0 = uL + L + TRUCK_ROAD + vr
            for vv in range(int(vB + 24), int(vB + D - 24), int(sw * 2)):
                ticks.append(fr.line(u0, vv, u0 + CAR_ROW, vv))
        tin += [Point(fr.pt(TRUCK_ROAD / 2, 0)), Point(fr.pt(TRUCK_ROAD / 2, depth))]
        cin.append(Point(fr.pt(Lmod, vB + D / 2)))
        pod = min(90.0, math.sqrt(OFFICE_SHARE * L * D / 2))
        offices += [fr.rect(uL + L - pod, uL + L, vB + D / 2 - pod, vB + D / 2), fr.rect(uL + L - pod, uL + L, vB + D / 2, vB + D / 2 + pod)]
        walls = 2
        cap = max(0, int((L - 2 * END_CLEAR) // DOOR_SP))
        court_stalls = 2 * int((L - 80) // 12) if row else 0
        dock_v = [(vB, -1), (vB + D, 1)]
    sf = int(round(L * D))
    doors = min(round(sf / SF_PER_DOOR), cap * walls)
    per_wall = [doors // walls + (1 if k < doors % walls else 0) for k in range(walls)]
    for (vw, sg), nd in zip(dock_v, per_wall):
        for k in range(nd):
            u = uL + END_CLEAR + (L - 2 * END_CLEAR) * (k + 0.5) / max(nd, 1)
            doors_g.append(fr.line(u, vw, u, vw + sg * 12))
    req_code, rule = parking_required(parking_rule, sf)
    req = max(req_code, parking_market(sf))
    capc = int(r * car_per_row(v, L, jname) * island_factor(jname))
    return Plan(v, L, r, fr, fr.rect(0, Lmod, 0, depth), b, courts, rows, cars, roads, offices, ticks, doors_g, tin, cin,
                sf, doors, court_stalls, capc, req, rule + f"; market {parking_market(sf)}", trailer_target(sf, doors))

def orient_score(p, row_line, res_line):
    s = 0.0
    for c in p.courts + p.rows:
        if row_line is not None:
            s += min(c.distance(row_line), 400)
        if res_line is not None:
            s += 1.5 * min(c.distance(res_line), 400)
    for c in p.cars:
        if row_line is not None:
            s -= 0.3 * min(c.distance(row_line), 400)
    return s

def fit_variant(v, P, B, jname, rules, row_line, res_line, length_cap=None, steep=None):
    pp = [q for q in parts(P) if q.area > 40000]
    if not pp:
        return None
    rings, origin = sitefit.normalise(pp)
    lo, hi = length_range(v)
    if length_cap is not None:
        hi = min(hi, length_cap)
    if hi < lo:
        return None
    Ls = list(range(lo, hi + 1, 20))
    for r in range(1, 13):
        depth, _ = geometry_of(v, Ls[0], r)
        lat = sorted({float(geometry_of(v, L, r)[1]) for L in Ls})
        cap = None
        found = None
        for _ in range(60):
            lat_c = [x for x in lat if cap is None or x <= cap]
            if not lat_c:
                break
            got = sitefit.fit_lengths(rings, [(float(depth), lat_c)]).get(float(depth))
            if not got:
                break
            Lmod, pl = got
            L = Lmod - (geometry_of(v, 0, r)[1])
            cands = []
            for flip in (False, True):
                fr = Frame(Lmod, depth, pl, origin, flip)
                p = build(v, L, r, fr, jname, rules["parking"])
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
            return None              # deeper car blocks will not fit either
        if found.car_cap >= found.cars_required:
            return found
    return None

# ---------------------------------------------------------------------------
# Frontage runs, driveways, routes
# ---------------------------------------------------------------------------
def frontage_runs(front_ft):
    if front_ft is None or front_ft.is_empty:
        return []
    m = linemerge(snap(front_ft, front_ft, 3.0)) if front_ft.geom_type != "LineString" else front_ft
    runs = [ln for ln in getattr(m, "geoms", [m]) if ln.geom_type == "LineString" and ln.length > 1]
    return sorted(runs, key=lambda ln: -ln.length)

def pick_driveways(runs, truck_target, car_target, spacing=250.0):
    """Truck driveway on a run that can hold ~190 ft of curb (40 ft + two 75 ft radii) plus clearance;
    car driveway >= spacing away, on any run that can hold ~100 ft."""
    tp = None
    good = [ln for ln in runs if ln.length >= 200]
    pool = good or runs
    if pool:
        best = min(pool, key=lambda ln: ln.distance(truck_target))
        s = best.project(truck_target)
        half = 100 if best.length >= 200 else best.length / 2
        tp = best.interpolate(min(max(s, half), best.length - half))
    cp = None
    if tp is not None:
        cands = []
        for ln in runs:
            if ln.length < 100:
                continue
            for k in range(55, int(ln.length) - 54, 15):
                q = ln.interpolate(k)
                if q.distance(tp) >= spacing:
                    cands.append((q.distance(car_target), q))
        if cands:
            cp = min(cands, key=lambda t: t[0])[1]
    return tp, cp, (bool(good))

def grid_route(start, goal, walk, cost_zone=None, step=GRID):
    """Shortest 8-connected path over `walk` (shapely area) from start to goal. Returns LineString or None."""
    if walk.is_empty:
        return None
    minx, miny, maxx, maxy = walk.bounds
    nx, ny = int((maxx - minx) / step) + 1, int((maxy - miny) / step) + 1
    if nx * ny > 60000:
        step *= math.sqrt(nx * ny / 60000.0)
        nx, ny = int((maxx - minx) / step) + 1, int((maxy - miny) / step) + 1
    prep = _prep(walk)
    prepc = _prep(cost_zone) if cost_zone is not None and not cost_zone.is_empty else None
    ok, cost = {}, {}
    def cell(i, j):
        if (i, j) not in ok:
            pt = Point(minx + i * step, miny + j * step)
            ok[(i, j)] = prep.contains(pt)
            cost[(i, j)] = 3.0 if (prepc is not None and prepc.contains(pt)) else 1.0
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
    dist = {s: 0.0}; prev = {}; pq = [(0.0, s)]
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
                dist[q] = nd; prev[q] = u; heapq.heappush(pq, (nd, q))
    if g not in dist:
        return None
    path = [g]
    while path[-1] != s:
        path.append(prev[path[-1]])
    pts = [(minx + i * step, miny + j * step) for i, j in reversed(path)]
    pts = [(start.x, start.y)] + pts + [(goal.x, goal.y)]
    return LineString(pts).simplify(step * 0.9)

def place_pond(region, Lf, Wf, elev, angles, near, pad_z, barrier, max_pts=700):
    """Best pond rectangle in region: low, close to `near`, on the same side of `barrier`."""
    if region.is_empty:
        return None
    base = box(-Lf / 2, -Wf / 2, Lf / 2, Wf / 2)
    pts = [(z, p) for p, z in elev.items() if region.contains(Point(p))]
    if not pts:
        minx, miny, maxx, maxy = region.bounds
        pts = [(0.0, (x, y)) for x in range(int(minx), int(maxx), 40) for y in range(int(miny), int(maxy), 40) if region.contains(Point(x, y))]
    pts.sort(key=lambda t: t[0])
    best = None
    for z, (x, y) in pts[:max_pts]:
        for a in angles:
            r = affinity.translate(affinity.rotate(base, a, origin=(0, 0)), x, y)
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

# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------
def plan_site(sid, title, g_wgs, meta, extra_cut_wgs=None, tax_acres=None):
    plane = Plane(g_wgs)
    parcel = clean(plane.ft(shapely.make_valid(g_wgs)), 1.0)
    jname, rules = jur_rules(meta.get("jurisdiction", ""))
    rules = json.loads(json.dumps(rules))
    if jname.startswith("atlanta"):
        rules["pave"]["OTHER"] = 5
    flags = []
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
    tl = []
    for a, s in transmission(g_wgs):
        sf_ = plane.ft(s)
        if sf_.distance(parcel) < 300:
            tl.append((a, sf_.buffer(corridor_halfwidth(a.get("VOLTAGE")), cap_style=2)))
    trans = unary_union([c for _, c in tl]).intersection(parcel) if tl else Polygon()
    if not trans.is_empty:
        flags.append(("TRANSMISSION_EASEMENT_ASSUMED", 2, f"{len(tl)} mapped transmission line(s); corridor widths assumed by voltage; no building under the lines, paving allowed with the utility's consent"))
    cut = plane.ft(extra_cut_wgs).intersection(parcel) if extra_cut_wgs is not None else Polygon()
    fr = frontage.check_parcel(g_wgs)
    front_ft = plane.ft(fr.line) if fr.line is not None else None
    neigh = [(a, s) for a, s in county_near(g_wgs) if s is not None and not s.equals(g_wgs) and s.intersection(g_wgs).area < 0.5 * s.area]
    edges = classify_edges(parcel, plane, neigh, front_ft)
    res_line = unary_union([ln for k, ln in edges if k.startswith("RES")]) if any(k.startswith("RES") for k, _ in edges) else None
    row_line = front_ft if front_ft is not None and not front_ft.is_empty else None
    if res_line is not None:
        flags.append(("RESIDENTIAL_EDGE", 2, f"about {res_line.length:,.0f} ft of boundary touches residential parcels (county land-use codes); draft buffers applied"))
    wet_buf = constraints.buffer(25) if not constraints.is_empty else Polygon()
    P = clean(envelope(parcel, edges, rules["pave"]).difference(wet_buf).difference(cut))
    B = clean(envelope(parcel, edges, rules["bldg"]).difference(wet_buf).difference(trans).difference(cut))
    elev, step = dem_samples(plane, parcel)
    steep = steep_cells(elev, step).intersection(parcel) if elev else Polygon()
    runs = frontage_runs(front_ft)
    hypothetical = not runs
    if hypothetical:
        flags.append(("ACCESS_NONE", 1, "no public road frontage from the GDOT check: landlocked or access by easement only; the plan below is hypothetical and its floor area is not counted"))
    # gates on use
    zl = (meta.get("zoning") or "").upper()
    if jname.startswith("east point") and zl in ("I-1", "I1", "I-2", "I2"):
        flags.append(("USE_PERMIT_REQUIRED", 1, "East Point: warehousing is use-permit only in I-1/I-2 and needs 5,280 ft from residential (draft reading)"))
    if "chattahoochee" in (meta.get("jurisdiction") or "").lower():
        flags.append(("USE_PERMIT_REQUIRED", 1, "Chattahoochee Hills: no by-right warehousing"))
    if jname.startswith("fairburn") and fr.road and "local" in fr.road:
        flags.append(("COLLECTOR_FRONTAGE", 2, "Fairburn M-districts require collector-street frontage; this frontage reads as a local road"))
    # fit every variant, then finish the best few with routes and ponds
    fits = []
    for v in VARIANTS:
        p = fit_variant(v, P, B, jname, rules, row_line, res_line)
        if p is not None:
            fits.append(p)
    if not fits:
        rec = {"id": sid, "title": title, "fits": False, "jurisdiction_rules": jname,
               "flags": {1: [{"flag": "NO_FIT", "note": "no building variant fits the envelope (with its truck court and parking)"}] + [{"flag": f, "note": n} for f, s, n in flags if s == 1],
                         2: [{"flag": f, "note": n} for f, s, n in flags if s == 2], 3: []},
               "inputs": summary_inputs(parcel, P, B, constraints, trans, cut, steep, fr, edges)}
        return rec, None
    def prelim(p):
        st = p.module.intersection(steep).area / p.module.area if not steep.is_empty else 0.0
        return p.sf * (1 - 0.6 * st)
    fits.sort(key=prelim, reverse=True)
    results = []
    picked, seen_types = [], set()
    for p in fits:                       # the best few, keeping variety (row / no row, side / end cars)
        key = (p.v["type"], p.v["row"], p.v["cars"])
        if key in seen_types and len(picked) >= 4:
            continue
        picked.append(p); seen_types.add(key)
        if len(picked) >= 8:
            break
    for p in picked:
        cap = None
        cur = p
        for attempt in range(12):
            o = finish(cur, parcel, P, B, constraints, wet_buf, trans, cut, steep, elev, meta, rules, runs, jname, plane)
            if o["ok"]:
                results.append((cur, o)); break
            nxt = fit_variant(cur.v, P, B, jname, rules, row_line, res_line, length_cap=int(cur.L - 40), steep=steep)
            if nxt is None:
                results.append((cur, o)); break
            cur = nxt
        else:
            results.append((cur, o))
    def final_score(t):
        p, o = t
        st = o["steep_module_pct"] / 100
        short = o["trailers"] < p.trailers_target
        return (o["ok"], p.sf * (1 - 0.6 * st) * (0.6 if o["route_problem"] else 1.0) * (0.8 if short else 1.0))
    results.sort(key=final_score, reverse=True)
    best_p, best_o = results[0]
    # second building
    second = None
    if best_o["ok"]:
        used = unary_union([best_p.module.buffer(40)] + [d.buffer(15) for d in best_o["drops"]] + [r.buffer(15) for _, r in best_o["route_polys"]])
        P2, B2 = clean(P.difference(used)), clean(B.difference(used))
        if P2.area > 3.5 * SQFT_PER_AC:
            c2 = [q for q in (fit_variant(v, P2, B2, jname, rules, row_line, res_line) for v in VARIANTS) if q is not None and q.sf >= 50000]
            for p2 in sorted(c2, key=lambda q: -q.sf)[:6]:
                o2 = finish(p2, parcel, P2, B2, constraints, wet_buf, trans, cut, steep, elev, meta, rules, runs, jname, plane,
                            others=[best_p], others_o=best_o)
                if o2["ok"] and not o2["route_problem"]:
                    second = (p2, o2); break
    rec = report(sid, title, best_p, best_o, second, parcel, P, B, meta, rules, jname, flags, fr, hypothetical, tax_acres)
    rec["alternatives"] = [{"variant": p.v["name"], "operator": p.v["operator"], "sf": rnd(p.sf, 1000), "L": p.L, "D": p.v["D"],
                            "pond_and_routes_ok": o["ok"], "route_problem": o["route_problem"]} for p, o in results]
    rec["inputs"] = summary_inputs(parcel, P, B, constraints, trans, cut, steep, fr, edges)
    svg = render(title, parcel, P, constraints, trans, cut, steep, front_ft, best_p, best_o, second, rec)
    return rec, svg

def finish(p, parcel, P, B, constraints, wet_buf, trans, cut, steep, elev, meta, rules, runs, jname, plane, others=(), others_o=None):
    flags = []
    occupied = unary_union([p.module] + [q.module for q in others])
    # driveways and routes (before ponds)
    route_polys, routes, problem = [], [], False
    if others_o is not None:
        tpt, cpt = others_o["tpt"], others_o["cpt"]
    else:
        ttarget = min(p.truck_entries, key=lambda q: q.distance(unary_union(runs))) if runs else p.truck_entries[0]
        ctarget = p.car_entries[0]
        tpt, cpt, okrun = pick_driveways(runs, ttarget, ctarget) if runs else (None, None, False)
        if runs and not okrun:
            flags.append(("ACCESS_CONSTRAINED", 1, "no frontage run can hold a truck driveway (about 190 ft of curb with 75 ft radii)"))
    walk = parcel.buffer(-3).difference(wet_buf).difference(cut)
    if others_o is not None:
        walk = walk.difference(unary_union([pp for _, pp in others_o["route_polys"]]).buffer(-1)) if False else walk
    walk_t = walk.difference(occupied.buffer(-1))
    entry_disc = unary_union([q.buffer(30) for q in p.truck_entries + p.car_entries])
    walk_t = walk_t.union(entry_disc.intersection(parcel))
    if tpt is not None:
        tgt = min(p.truck_entries, key=lambda q: q.distance(tpt))
        rt = grid_route(tpt, tgt, walk_t.difference(unary_union(p.cars).buffer(5)), steep)
        if rt is None:
            problem = True; flags.append(("ROUTE_INFEASIBLE", 1, "no truck route found inside the parcel from the driveway to the court"))
        else:
            routes.append(("truck", rt)); route_polys.append(("truck", rt.buffer(20, cap_style=2)))
    if cpt is not None and others_o is None:
        tgt = p.car_entries[0]
        avoid = unary_union([pp for _, pp in route_polys]).buffer(5) if route_polys else Polygon()
        rc = grid_route(cpt, tgt, walk_t.difference(avoid).difference(unary_union(p.courts + p.roads + p.rows).buffer(3)), steep)
        if rc is None:
            rc = grid_route(cpt, tgt, walk_t, steep)
            if rc is not None:
                flags.append(("CAR_CROSSES_TRUCK", 2, "the car route has to cross truck pavement"))
        if rc is not None:
            routes.append(("car", rc)); route_polys.append(("car", rc.buffer(12, cap_style=2)))
    elif cpt is None and others_o is None and tpt is not None:
        flags.append(("ACCESS_SHARED", 2, "one driveway only: cars split off inside the throat"))
    if tpt is not None and others_o is None:
        tl = sum(r.length for k, r in routes if k == "truck")
        if tl < 100:
            flags.append(("SHORT_THROAT", 3, f"truck throat about {tl:.0f} ft before the first conflict (100 ft wanted)"))
    nd = (tpt is not None) + (cpt is not None)
    if others_o is None:
        if nd < 2:
            flags.append(("FIRE_SECOND_ACCESS", 2, f"{nd} driveway(s): a second fire access (emergency-only link) or fire-official approval is needed"))
        elif tpt.distance(cpt) < 0.5 * math.hypot(p.building.bounds[2] - p.building.bounds[0], p.building.bounds[3] - p.building.bounds[1]):
            flags.append(("FIRE_SECOND_ACCESS", 2, f"the two driveways are {tpt.distance(cpt):,.0f} ft apart, under half the building diagonal"))
    rp = unary_union([pp for _, pp in route_polys]) if route_polys else Polygon()
    # trailer shortfall -> drop yard sized to the need (two 55 ft rows on an 80 ft aisle, 12 ft stalls)
    trailers = p.court_stalls
    need = max(0, p.trailers_target - trailers)
    drop_len = (math.ceil(need / 2) * 12 + 80) if need else 0
    drop_area_ac = drop_len * 190 / SQFT_PER_AC
    # detention (sized with the expected drop yard), placed before the drop yard
    imp_geom = unary_union([p.module] + [pp for _, pp in route_polys])
    imp_ac = imp_geom.area / SQFT_PER_AC + drop_area_ac
    if others_o is not None:
        imp_ac += others_o["imp_ac"]
    disturbed = max(P.area / SQFT_PER_AC, imp_ac)
    v = detention(imp_ac, max(disturbed - imp_ac, 0), meta.get("hsg"))
    npd = 1 if v <= 10 else 2 if v <= 20 else 3
    w0, Lf, Wf, _ = pond_dims(v / npd)
    pad = [z for (x, y), z in elev.items() if p.module.contains(Point(x, y))]
    pad_z = sum(pad) / len(pad) if pad else None
    region = (parcel.buffer(-10).difference(constraints).difference(cut).difference(occupied.buffer(15)).difference(rp.buffer(10))
              .intersection(P.buffer(rules["pave"].get("ROW", 10) + 1)))
    if others_o is not None:
        region = region.difference(unary_union(others_o["ponds"] + others_o["drops"]).buffer(20))
    ponds = []
    for k in range(npd):
        r = place_pond(region, Lf, Wf, elev, [0, 90, 45, 135, p.frame.pl.angle_deg, p.frame.pl.angle_deg + 90], imp_geom, pad_z, constraints)
        if r is None:
            break
        ponds.append(r); region = region.difference(r.buffer(20))
    pond_ok = len(ponds) == npd
    if not pond_ok:
        flags.append(("POND_DOES_NOT_FIT", 2, f"{npd} pond(s) of {v / npd:.1f} ac-ft do not fit on the development side: underground detention (vaults) or a smaller building"))
    drops = []
    if need:
        free = P.difference(occupied.buffer(20)).difference(rp.buffer(10)).difference(unary_union(ponds).buffer(20) if ponds else Polygon())
        if others_o is not None:
            free = free.difference(unary_union(others_o["ponds"] + others_o["drops"]).buffer(20))
        ang = [p.frame.pl.angle_deg, p.frame.pl.angle_deg + 90]
        r = v2.place_rect_lowest(free, drop_len, 190, {}, ang)
        if r is None and need > 8:
            half = math.ceil(need / 2)
            r1 = v2.place_rect_lowest(free, math.ceil(half / 2) * 12 + 80, 190, {}, ang)
            if r1 is not None:
                drops.append(r1); trailers += half
        elif r is not None:
            drops.append(r); trailers += need
    # grading over the whole module
    mod_pts = [z for (x, y), z in elev.items() if p.module.contains(Point(x, y))]
    relief = (max(mod_pts) - min(mod_pts)) if len(mod_pts) > 3 else None
    cutfill = None
    if len(mod_pts) > 3:
        mean = sum(mod_pts) / len(mod_pts)
        cutfill = p.module.area * (sum(abs(z - mean) for z in mod_pts) / len(mod_pts)) / 2 / 27
    steep_mod = (p.module.intersection(steep).area / p.module.area * 100) if not steep.is_empty else 0.0
    ok = pond_ok and not problem and (others_o is not None or tpt is not None or not runs)
    return {"ok": ok if runs else pond_ok, "route_problem": problem, "tpt": tpt, "cpt": cpt, "routes": routes, "route_polys": route_polys,
            "drops": drops, "trailers": trailers, "imp_ac": imp_ac, "v_det": v, "ponds": ponds, "pond_each": (w0, Lf, Wf, v / npd),
            "relief": relief, "cutfill_cy": cutfill, "steep_module_pct": steep_mod, "flags": flags}

def summary_inputs(parcel, P, B, constraints, trans, cut, steep, fr, edges):
    return {"parcel_ac_map": round(parcel.area / SQFT_PER_AC, 1), "paving_envelope_ac": round(P.area / SQFT_PER_AC, 1),
            "building_envelope_ac": round(B.area / SQFT_PER_AC, 1), "mapped_constraints_ac": round(constraints.area / SQFT_PER_AC, 2),
            "transmission_ac": round(trans.area / SQFT_PER_AC, 2), "test_cut_ac": round(cut.area / SQFT_PER_AC, 2),
            "steep_ac": round(steep.area / SQFT_PER_AC, 2), "frontage_ft": fr.feet, "frontage_road": fr.road,
            "edges_ft": {k: round(sum(ln.length for kk, ln in edges if kk == k)) for k in {k for k, _ in edges}}}

def report(sid, title, p, o, second, parcel, P, B, meta, rules, jname, flags0, fr, hypothetical, tax_acres):
    flags = list(flags0) + o["flags"]
    gross = parcel.area / SQFT_PER_AC
    total_sf = p.sf + (second[0].sf if second else 0)
    if o["trailers"] < p.trailers_target:
        flags.append(("TRAILER_SHORT", 2, f"{o['trailers']} trailer stalls against a target of {p.trailers_target}"))
    clear = 32 if p.sf < 200000 else 36
    if rules.get("height") and clear + 10 > rules["height"]:
        flags.append(("HEIGHT_CAP", 1, f"assumed building height {clear + 10} ft over the draft cap {rules['height']} ft: a lower building ({rules['height'] - 10} ft clear) or a variance"))
    cov = rules.get("cover")
    if cov:
        kind, capv = cov
        bldg_area = p.building.area + (second[0].building.area if second else 0)
        val = (bldg_area / parcel.area if kind == "building" else
               (bldg_area + sum(c.area for c in p.cars)) / parcel.area if kind == "building+parking" else o["imp_ac"] / gross)
        if val > capv:
            flags.append(("COVERAGE_OVER_CAP", 1, f"{kind} coverage {val:.0%} over the draft cap {capv:.0%}"))
    if o["steep_module_pct"] > 10:
        flags.append(("GRADING_RISK", 2, f"{o['steep_module_pct']:.0f}% of the graded area is steeper than 15% (lidar)"))
    if o["relief"] and o["relief"] > 25:
        flags.append(("RELIEF", 2, f"about {o['relief']:.0f} ft of relief across the graded area: terraces or retaining walls likely"))
    for c in p.courts + p.rows:
        pass
    res_near = any(k.startswith("RES") for k, _ in [])
    sfa = total_sf / gross
    if not hypothetical and sfa < 9000:
        flags.append(("BELOW_COMP_BAND", 3, f"{sfa:,.0f} SF per gross acre; Georgia comps run about 10,500-15,400"))
    ld = p.L / p.v["D"]
    if ld < 2.0 or ld > 4.2:
        flags.append(("LD_OUT_OF_BAND", 3, f"length/depth {ld:.1f} (comps 2.0-3.8)"))
    walls = 1 if p.v["type"] == "rear" else 2
    if p.doors and (p.L - 2 * END_CLEAR) * walls / p.doors < 14:
        flags.append(("DOOR_DENSITY_HIGH", 3, "under 14 ft of dock wall per door"))
    if tax_acres and abs(gross - tax_acres) / tax_acres > 0.15:
        flags.append(("ACREAGE_MISMATCH", 2, f"county map {gross:.1f} ac vs tax roll {tax_acres:.1f} ac"))
    if jname.startswith("atlanta"):
        flags.append(("ATL_PARKING_VERIFY", 2, "Atlanta's 1/600 SF parking minimum used; verify current code (parking reforms)"))
    flags.append(("ZONING_DRAFT", 2, f"setbacks and buffers are draft screening values for {jname}; zoning_rules.json is the authority"))
    flags.append(("STREAMS_PIPELINES", 2, "stream lines and petroleum pipelines are not mapped; mapped water, wetlands and floodplain get a 25 ft paving buffer"))
    flags.append(("POND_SITE_FROM_LIDAR", 3, "pond placed low, near and on the development's side of mapped wetlands; no outfall or grading design"))
    sev = {1: [], 2: [], 3: []}
    seen = set()
    for f, s, n in flags:
        if (f, n) in seen:
            continue
        seen.add((f, n)); sev[s].append({"flag": f, "note": n})
    cars_prov = max(min(p.car_cap, math.ceil(p.cars_required / 5) * 5), p.cars_required) if p.car_cap >= p.cars_required else p.car_cap
    rec = {
        "id": sid, "title": title, "fits": True, "hypothetical": hypothetical, "jurisdiction_rules": jname,
        "configuration": p.v["name"], "operator_prototype": p.v["operator"],
        "building": {"L_ft": p.L, "D_ft": p.v["D"], "sf": rnd(p.sf, 1000), "office_sf": rnd(OFFICE_SHARE * p.sf, 500), "clear_height_ft": clear},
        "total_sf": 0 if hypothetical else rnd(total_sf, 1000), "total_sf_if_access": rnd(total_sf, 1000),
        "doors": p.doors,
        "trailers": {"court_rows": p.court_stalls, "drop_yards": o["trailers"] - p.court_stalls, "total": o["trailers"], "target": p.trailers_target},
        "cars": {"provided": cars_prov, "row_capacity": p.car_cap, "required": p.cars_required, "basis": p.cars_rule, "ada": ada_count(cars_prov),
                 "per_1000_sf": round(cars_prov / p.sf * 1000, 2), "stall": f"{stall_width(jname)} x 18 ft"},
        "coverage": {"gross_ac": round(gross, 1), "sf_per_gross_ac": rnd(sfa, 100), "gross_far": round(total_sf / (gross * SQFT_PER_AC), 2),
                     "impervious_ac": round(o["imp_ac"] if not second else second[1]["imp_ac"], 1),
                     "impervious_pct": round((o["imp_ac"] if not second else second[1]["imp_ac"]) / gross * 100), "paving_envelope_ac": round(P.area / SQFT_PER_AC, 1)},
        "detention": {"storage_acft": round((second[1]["v_det"] if second else o["v_det"]) * 2) / 2, "ponds": len(o["ponds"]) + (len(second[1]["ponds"]) if second else 0),
                      "fits": o["ok"], "basis": f"1.15 x (k_imp x impervious + k_perv x disturbed pervious), HSG {meta.get('hsg') or 'B'}; 6 ft deep, 4:1 slopes, L=2W, 1 ft freeboard, 15 ft rim; screening only (+/-30%)"},
        "access": {"truck_driveway": o["tpt"] is not None, "car_driveway": o["cpt"] is not None, "frontage_ft": fr.feet, "road": fr.road,
                   "truck_route_ft": round(sum(l.length for k, l in o["routes"] if k == "truck")), "car_route_ft": round(sum(l.length for k, l in o["routes"] if k == "car"))},
        "grading": {"relief_ft": round(o["relief"]) if o["relief"] else None, "cut_fill_cy": rnd(o["cutfill_cy"], 1000) if o["cutfill_cy"] else None,
                    "steep_share_pct": round(o["steep_module_pct"]), "basis": "one balanced plane over the building, courts and parking (lidar 1 m sampled at 40 ft); no terraces"},
        "flags": sev,
    }
    if second:
        p2, o2 = second
        rec["second_building"] = {"configuration": p2.v["name"], "L_ft": p2.L, "D_ft": p2.v["D"], "sf": rnd(p2.sf, 1000), "doors": p2.doors,
                                  "trailers": o2["trailers"], "trailers_target": p2.trailers_target, "cars_capacity": p2.car_cap, "cars_required": p2.cars_required,
                                  "route_ft": round(sum(l.length for k, l in o2["routes"] if k == "truck"))}
    return rec

# ---------------------------------------------------------------------------
C = v2.C | {"car": "#dfe3f1", "court": "#c4c9cd"}

def render(title, parcel, P, constraints, trans, cut, steep, front_ft, p, o, second, rec):
    W, PLAN, M = 1200, 780, 20
    minx, miny, maxx, maxy = parcel.bounds
    span = max(maxx - minx, maxy - miny) * 1.04
    s = PLAN / span
    ox = M + (PLAN - (maxx - minx) * s) / 2
    oy = M + (PLAN - (maxy - miny) * s) / 2
    X = lambda x: ox + (x - minx) * s
    Y = lambda y: oy + (maxy - y) * s
    def path(g):
        out = []
        for q in (sitefit.polygon_parts(g) if g is not None and not g.is_empty else []):
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
    esc = lambda t: str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = [f'<defs><pattern id="tx" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="{C["trans"]}" stroke-width="3"/></pattern>'
           f'<pattern id="sx" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><line x1="0" y1="0" x2="0" y2="6" stroke="{C["steep"]}" stroke-width="1.2"/></pattern>'
           f'<pattern id="cx" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="{C["cut"]}" stroke-width="2"/></pattern></defs>']
    out += [poly(parcel, C["parcel"]), poly(constraints.intersection(parcel), C["cons"], op=0.85), poly(steep, "url(#sx)", op=0.9),
            poly(trans, "url(#tx)", op=0.9), poly(cut, "url(#cx)", op=0.8), poly(P, "none", C["env"], 1, extra='stroke-dasharray="6 4"')]
    plans = [(p, o)] + ([second] if second else [])
    for kind, rp in o["route_polys"] + ([] if not second else second[1]["route_polys"]):
        out.append(poly(rp, C["road"] if kind == "truck" else C["car"]))
    for pp, oo in plans:
        for g in oo["drops"]:
            out.append(poly(g, C["trailer"], C["ink"], 0.5))
        for g in pp.roads: out.append(poly(g, C["road"]))
        for g in pp.courts: out.append(poly(g, C["court"]))
        for g in pp.rows: out.append(poly(g, C["trailer"]))
        for g in pp.cars: out.append(poly(g, C["car"]))
        if pp.ticks:
            out.append(f'<path d="{lpath(unary_union(pp.ticks))}" stroke="#ffffff" stroke-width="0.8" fill="none"/>')
        out.append(poly(pp.building, C["bldg"], C["bldg_s"], 1.6))
        for g in pp.offices: out.append(poly(g, C["office"], C["bldg_s"], 0.6))
        if pp.doors_geom:
            out.append(f'<path d="{lpath(unary_union(pp.doors_geom))}" stroke="{C["bldg_s"]}" stroke-width="1.4" fill="none"/>')
        for g in oo["ponds"]:
            out.append(poly(g, "#d7ece8", C["pond_s"], 0.8)); out.append(poly(g.buffer(-RIM), C["pond"], C["pond_s"], 1.2))
    if front_ft is not None and not front_ft.is_empty:
        out.append(f'<path d="{lpath(front_ft)}" stroke="{C["row"]}" stroke-width="4" fill="none" stroke-linecap="round"/>')
    for pt, lab in ((o["tpt"], "T"), (o["cpt"], "C")):
        if pt is not None:
            out.append(f'<circle cx="{X(pt.x):.1f}" cy="{Y(pt.y):.1f}" r="9" fill="{C["drive"]}"/><text x="{X(pt.x):.1f}" y="{Y(pt.y)+4:.1f}" font-size="11" font-weight="bold" fill="#fff" text-anchor="middle">{lab}</text>')
    out.append(poly(parcel, "none", C["ink"], 1.6))
    for pp, _ in plans:
        c = pp.building.centroid
        out.append(f'<text x="{X(c.x):.1f}" y="{Y(c.y)+4:.1f}" font-size="12" font-weight="bold" text-anchor="middle" fill="{C["ink"]}">{rnd(pp.sf, 1000):,} SF</text>')
    ax, ay = M + PLAN - 20, M + 10
    out.append(f'<path d="M{ax},{ay} L{ax+8},{ay+26} L{ax},{ay+20} L{ax-8},{ay+26} Z" fill="{C["ink"]}"/><text x="{ax}" y="{ay+40}" font-size="12" text-anchor="middle" font-weight="bold">N</text>')
    bar = 10 ** math.floor(math.log10(span * 0.25)); bar = bar * (5 if bar * 5 <= span * 0.3 else 2 if bar * 2 <= span * 0.3 else 1)
    out.append(f'<path d="M{M},{M+PLAN+8} L{M+bar*s:.1f},{M+PLAN+8}" stroke="{C["ink"]}" stroke-width="3"/><text x="{M+bar*s+6:.1f}" y="{M+PLAN+12}" font-size="11">{bar:,.0f} ft</text>')
    tx, yy = M + PLAN + 22, M + 4
    b, tr, ca, cv, de, acc, gr = rec["building"], rec["trailers"], rec["cars"], rec["coverage"], rec["detention"], rec["access"], rec["grading"]
    L = [("h", ("HYPOTHETICAL: no road frontage. " if rec["hypothetical"] else "NOT FEASIBLE (see !! flags). " if rec.get("feasible") is False else "") + title),
         ("t", rec["configuration"] + (" (operator prototype)" if rec["operator_prototype"] else " (market depth, not an operator prototype)")),
         ("b", f"{b['sf']:,} SF   {int(b['L_ft'])} x {int(b['D_ft'])} ft   {b['clear_height_ft']} ft clear (assumed)")]
    if rec.get("second_building"):
        sb = rec["second_building"]
        L.append(("b", f"2nd: {sb['configuration']}, {sb['sf']:,} SF, {sb['doors']} doors, {sb['trailers']} trailers, {sb['cars_capacity']} cars; total {rec['total_sf_if_access']:,} SF"))
        L.append(("t", "Counts below are building 1 only."))
    L += [("t", f"Dock doors {rec['doors']}   office ~{b['office_sf']:,} SF"),
          ("t", f"Trailer stalls {tr['total']} (court rows {tr['court_rows']}, drop yard {tr['drop_yards']}) vs target {tr['target']}"),
          ("t", f"Car stalls {ca['provided']} (required {ca['required']}; ADA {ca['ada']}; {ca['stall']})"),
          ("t", f"Site {cv['gross_ac']} ac (county map)   {cv['sf_per_gross_ac']:,} SF/gross ac   FAR {cv['gross_far']}"),
          ("t", f"Impervious {cv['impervious_ac']} ac ({cv['impervious_pct']}%)"),
          ("t", f"Detention {de['storage_acft']} ac-ft in {de['ponds']} pond(s){'' if de['fits'] else ' - see flags'}"),
          ("t", f"Access: {'truck (T)' if acc['truck_driveway'] else 'no truck driveway'}{' + car (C)' if acc['car_driveway'] else ''}; {acc['road'] or 'no road'}; frontage {acc['frontage_ft'] or 0:,} ft")]
    if gr["relief_ft"] is not None:
        L.append(("t", f"Graded area: relief ~{gr['relief_ft']} ft, balanced cut/fill ~{gr['cut_fill_cy'] or 0:,} CY, steep {gr['steep_share_pct']}%"))
    L.append(("h2", "Flags"))
    for sv, lab in ((1, "!!"), (2, "!"), (3, "i")):
        for f in rec["flags"][sv]:
            L.append((f"f{sv}", f"{lab} {f['flag']}: {f['note']}"))
    body = []
    for kind, txt in L:
        size = {"h": 15, "h2": 13}.get(kind, 11.5)
        weight = "bold" if kind in ("h", "h2", "b") else "normal"
        col = {"f1": "#b3261e", "f2": "#8a5a00", "f3": "#5d676e"}.get(kind, C["ink"])
        for seg in textwrap.wrap(txt, 66 if size < 12 else 52) or [""]:
            yy += size + 4
            body.append(f'<text x="{tx}" y="{yy:.0f}" font-size="{size}" font-weight="{weight}" fill="{col}">{esc(seg)}</text>')
    yy += 8
    legend = [(C["bldg"], "building, office pods, dock doors"), (C["court"], "truck court"), (C["road"], "truck roads and truck route"), (C["trailer"], "trailer stalls / drop yard"),
              (C["car"], "car parking and car route"), (C["pond"], "detention pond (rim around)"), (C["cons"], "floodplain, wetlands, easements, protected"),
              ("url(#tx)", "transmission corridor (assumed width)"), ("url(#sx)", "steeper than 15% (lidar)"), ("url(#cx)", "test cut"),
              ("dash", "paving envelope (draft setbacks/buffers, 25 ft from water)"), (C["row"], "GDOT road frontage; T/C driveways")]
    for fill, txt in legend:
        yy += 16
        if fill == "dash":
            body.append(f'<path d="M{tx},{yy-5} L{tx+18},{yy-5}" stroke="{C["env"]}" stroke-dasharray="6 4" stroke-width="1.5"/>')
        else:
            body.append(f'<rect x="{tx}" y="{yy-10}" width="18" height="10" fill="{fill}" stroke="{C["ink"]}" stroke-width="0.6"/>')
        body.append(f'<text x="{tx+26}" y="{yy-1}" font-size="11">{esc(txt)}</text>')
    yy += 20
    for seg in textwrap.wrap("Screening test fit from public data: not a survey, civil design or zoning determination. Zoning values are draft; detention is a +/-30% estimate; routes, fire access and grading are indicative.", 74):
        body.append(f'<text x="{tx}" y="{yy:.0f}" font-size="10.5" fill="#5d676e">{esc(seg)}</text>'); yy += 13
    H = int(max(M + PLAN + 30, yy + 16))
    head = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="sans-serif"><title>{esc(title)}</title><rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>'
    return head + "".join(x for x in out if x) + "".join(body) + "</svg>"
