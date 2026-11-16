"""Screening-level industrial site planner, version 4.

Second engineering review (scratchpad/research/review_syn_v3.md) fixes:
 1. trucks enter at the court ends; end roads start at the building wall; car
    field reached from its own front edge; T near the court, C near the cars;
 2. drop-trailer yards sized to the shortfall, placed near the court, counted
    only with a truck route to the court;
 3. routes confined to the paving envelope (half-width inset), plus throat zones;
 4. lexicographic ranking (feasible first), unreachable pads excluded and refit,
    floor area not counted without a route;
 5. second building avoids building 1's ponds, parking, routes and drop yards,
    gets its own driveways when the frontage allows, incremental detention;
 6. a failed car route is a severity-1 failure; one-driveway sites route cars from T;
 7. building-end parking sits against the office end; four orientations scored;
 8. dead-end, throat, fire-separation (rotated diagonal) checks;
 9. ponds kept out of water buffers and transmission corridors; 10 ft minimum
    landscape strip on other lot lines; 11. depth/length bounds closer to market;
    partial trailer rows; Atlanta parking at the market ratio while the 1/600 code
    is unverified; clear height lowered under a height cap;
12. dock-face flags, comp bands with the right thresholds, impervious/footprint band.
Zoning values remain DRAFT placeholders; zoning_rules.json is the authority.
"""
from __future__ import annotations
import json, math, sys
from dataclasses import dataclass
from pathlib import Path

import shapely
from shapely.geometry import Polygon, Point, LineString, box
from shapely.ops import unary_union, nearest_points

sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, ".")
import siteplan2 as v2
import siteplan3 as v3
from siteplan2 import (Plane, parts, clean, county_near, transmission, corridor_halfwidth, dem_samples, steep_cells,
                       classify_edges, envelope, pond_dims, detention, parking_required, parking_market, ada_count,
                       trailer_target, jur_rules, rnd, SQFT_PER_AC, sitefit, frontage, layers, checks, tile_envelopes, expand)
from siteplan3 import (grid_route, place_pond, frontage_runs, car_rows_layout, stall_width, island_factor, summary_inputs,
                       COURT, TRAILER_ROW, TRUCK_ROAD, CAR_ROW, CAR_AISLE, WALK, END_CLEAR, DOOR_SP, SF_PER_DOOR, OFFICE_SHARE, RIM)

class Frame4(v2.Frame):
    def __init__(self, Lmod, depth, pl, origin, flipv=False, flipu=False):
        super().__init__(Lmod, depth, pl, origin, flipv)
        self.flipu = flipu
    def pt(self, u, v):
        if self.flipu:
            u = self.Lmod - u
        return super().pt(u, v)

VARIANTS = []
for D in (160, 190, 230, 270, 310):
    for row in (True, False):
        for cars in ("side", "end"):
            VARIANTS.append({"type": "rear", "D": D, "row": row, "cars": cars, "operator": False,
                             "name": f"rear-load {D} deep, {'court + trailer row' if row else 'court only'}, cars {'along office wall' if cars == 'side' else 'at office end'}"})
for row in (True, False):
    VARIANTS.append({"type": "rear", "D": 260, "row": row, "cars": "side", "operator": True,
                     "name": f"operator rear-load 260, {'court + trailer row' if row else 'court only'}, cars along office wall"})
for D in (470, 520, 600):
    for row in (True, False):
        VARIANTS.append({"type": "cross", "D": D, "row": row, "cars": "end", "operator": D == 520,
                         "name": f"{'operator ' if D == 520 else ''}cross-dock {D} deep, {'courts + trailer rows' if row else 'courts only'}, cars at office end"})

def length_range(v):
    D = v["D"]
    if v["operator"]:
        return (400, 640) if v["type"] == "rear" else (600, 1000)
    k = 1.6 if D <= 190 else 2.0
    return max(220, int(k * D) // 20 * 20), min(1600, int(4.5 * D) // 20 * 20)

def geometry_of(v, L, r):
    D = v["D"]; row = TRAILER_ROW if v["row"] else 0
    cd = car_rows_layout(r)[1]
    if v["type"] == "rear":
        if v["cars"] == "side":
            return cd + D + COURT + row, L + 2 * TRUCK_ROAD
        fs = WALK + CAR_AISLE
        return fs + D + COURT + row, cd + L + TRUCK_ROAD
    return row + COURT + D + COURT + row, TRUCK_ROAD + L + TRUCK_ROAD + cd

def car_per_row(v, L, jname):
    sw = stall_width(jname)
    if v["type"] == "rear" and v["cars"] == "side":
        return int((L - 48) // sw)
    if v["type"] == "rear":
        return int((WALK + CAR_AISLE + v["D"] - 24) // sw)
    return int((v["D"] - 48) // sw)

@dataclass
class Plan:
    v: dict; L: float; r: int; frame: Frame4; module: Polygon; building: Polygon
    courts: list; rows: list; cars: list; roads: list; offices: list; ticks: list; doors_geom: list
    truck_entries: list; car_entries: list; fallback_entries: list
    sf: int; doors: int; court_stalls: int; car_cap: int; cars_required: int; cars_rule: str; trailers_target: int; clear: int

def parking_rule_for(jname, rules):
    if jname.startswith("atlanta"):
        return "market"
    return rules["parking"]

def required_cars(rule, sf):
    if rule == "market":
        return parking_market(sf), "market ratio (Atlanta's 1/600 code minimum unverified)"
    req, txt = parking_required(rule, sf)
    return max(req, parking_market(sf)), txt + f"; market {parking_market(sf)}"

def build(v, L, r, fr, jname, prule):
    D = v["D"]; rowd = TRAILER_ROW if v["row"] else 0
    depth, Lmod = fr.depth, fr.Lmod
    P = lambda u, w: Point(fr.pt(u, w))
    courts, rows, cars, roads, offices, ticks, doors_g, tin, cin = [], [], [], [], [], [], [], [], []
    sw = stall_width(jname)
    sf = int(round(L * D))
    walls = 1 if v["type"] == "rear" else 2
    cap = max(0, int((L - 2 * END_CLEAR) // DOOR_SP))
    doors = min(round(sf / SF_PER_DOOR), cap * walls)
    target = trailer_target(sf, doors)
    if v["type"] == "rear":
        if v["cars"] == "side":
            cd = car_rows_layout(r)[1]
            uL, vB = TRUCK_ROAD, cd
            ue0, ue1 = 0, Lmod
            cars.append(fr.rect(uL, uL + L, 0, cd))
            for vr in car_rows_layout(r)[0]:
                for u in range(int(uL + 24), int(uL + L - 24), int(sw * 2)):
                    ticks.append(fr.line(u, vr, u, vr + CAR_ROW))
            cin.append(P(uL + L / 2, 0))
            roads += [fr.rect(0, uL, 0, vB + D), fr.rect(uL + L, Lmod, 0, vB + D)]   # end roads with a lead-in beside the car field (curbed)
            pod = min(80.0, math.sqrt(OFFICE_SHARE * L * D / 2))
            offices += [fr.rect(uL, uL + pod, vB, vB + pod), fr.rect(uL + L - pod, uL + L, vB, vB + pod)]
        else:
            cd = car_rows_layout(r)[1]
            fs = WALK + CAR_AISLE
            uL, vB = cd, fs
            ue0, ue1 = 0, Lmod
            cars.append(fr.rect(0, cd, 0, fs + D))
            for vr in car_rows_layout(r)[0]:
                u0 = cd - vr - CAR_ROW
                for vv in range(24, int(fs + D - 24), int(sw * 2)):
                    ticks.append(fr.line(u0, vv, u0 + CAR_ROW, vv))
            cin.append(P(cd / 2, 0))
            roads += [fr.rect(cd, cd + L, 0, fs), fr.rect(cd + L, Lmod, 0, vB + D)]     # fire lane along the office wall; far end road
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
            rl = n * 12; u0 = uL + (L - rl) / 2
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
    req, rule = required_cars(prule, sf)
    capc = int(r * car_per_row(v, L, jname) * island_factor(jname))
    return Plan(v, L, r, fr, fr.rect(0, Lmod, 0, depth), b, courts, rows, cars, roads, offices, ticks, doors_g, tin, cin, fb,
                sf, doors, court_stalls, capc, req, rule, target, 32 if sf < 200000 else 36)

def orient_score(p, row_line, res_line):
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

def fit_variant(v, P, B, jname, prule, row_line, res_line, length_cap=None):
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
    for r in range(1, 14):
        depth, _ = geometry_of(v, Ls[0], r)
        lat = sorted({float(geometry_of(v, L, r)[1]) for L in Ls})
        cap, found = None, None
        for _ in range(60):
            lat_c = [x for x in lat if cap is None or x <= cap]
            if not lat_c:
                break
            got = sitefit.fit_lengths(rings, [(float(depth), lat_c)]).get(float(depth))
            if not got:
                break
            Lmod, pl = got
            L = Lmod - geometry_of(v, 0, r)[1]
            cands = []
            for fv in (False, True):
                for fu in (False, True):
                    fr = Frame4(Lmod, depth, pl, origin, fv, fu)
                    p = build(v, L, r, fr, jname, prule)
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

def spacing_for(road):
    r = (road or "").lower()
    return 300.0 if any(k in r for k in ("arterial", "collector", "sr ", "us ")) else 250.0

def pick_driveways(runs, truck_target, car_target, spacing, taken=()):
    """T on the longest run nearest the court end (>= 100 ft from run ends for the 75 ft radii);
    C >= spacing from T and from existing driveways, nearest the car entry, preferring separation."""
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

def throat_zone(pt, parcel, avoid=None):
    if pt is None:
        return Polygon()
    z = pt.buffer(90).intersection(parcel.buffer(-2))
    return z.difference(avoid) if avoid is not None and not avoid.is_empty else z

def drop_size(need):
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

import shapely.affinity

def finish(p, ctx, others=None):
    parcel, P, wet_buf, trans, cut, steep, elev, meta, rules, runs, jname = (ctx[k] for k in
        ("parcel", "P", "wet_buf", "trans", "cut", "steep", "elev", "meta", "rules", "runs", "jname"))
    constraints = ctx["constraints"]
    flags = []
    occupied = p.module if others is None else unary_union([p.module, others["occupied"]])
    sp = spacing_for(ctx["road"])
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
    route_polys, routes, problem = [], [], False
    own_truck = unary_union(p.roads + p.courts).buffer(-6)          # the module's own end roads and court carry trucks too
    walk_t = (base.buffer(-20).union(throat_zone(tpt, parcel, unary_union([wet_buf, cut]))).difference(occupied.buffer(-2))
              .difference(unary_union(p.cars).buffer(8)).union(entries.difference(unary_union(p.cars))).union(own_truck))
    rt = None
    if tpt is not None:
        for group, note in ((p.truck_entries, None), (p.fallback_entries, "trucks reach the court along the building's end road from the office side")):
            for tgt in sorted(group, key=lambda q: q.distance(tpt)):
                rt = grid_route(tpt, tgt, walk_t, steep)
                if rt is not None:
                    break
            if rt is not None:
                if note:
                    flags.append(("TRUCK_ENTRY_OFFICE_SIDE", 3, note))
                break
        if rt is None:
            problem = True; flags.append(("ROUTE_INFEASIBLE", 1, "no truck route inside the paving envelope from the driveway to the court"))
        else:
            routes.append(("truck", rt)); route_polys.append(("truck", rt.buffer(20, cap_style=2).intersection(parcel)))
    truck_poly = unary_union([pp for _, pp in route_polys]) if route_polys else Polygon()
    start_c = cpt if cpt is not None else tpt
    if start_c is not None:
        walk_c = (base.buffer(-12).union(throat_zone(start_c, parcel, unary_union([wet_buf, cut]))).difference(occupied.buffer(-2))
                  .difference(unary_union(p.courts + p.roads + p.rows).buffer(4)).union(p.car_entries[0].buffer(32).intersection(parcel.buffer(-2)))
                  .union(unary_union(p.cars).buffer(-4)))
        if cpt is not None:
            walk_c = walk_c.difference(truck_poly.buffer(6))
        rc = grid_route(start_c, p.car_entries[0], walk_c, steep)
        if rc is None:
            problem = True; flags.append(("CAR_ROUTE_NONE", 1, "no car route from a driveway to the car field without crossing truck pavement"))
        else:
            routes.append(("car", rc)); route_polys.append(("car", rc.buffer(12, cap_style=2).intersection(parcel)))
        if cpt is None:
            flags.append(("ACCESS_SHARED", 2, "one driveway: cars split from trucks inside the throat"))
    rp = unary_union([pp for _, pp in route_polys]) if route_polys else Polygon()
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
    disturbed = max(min(P.area / SQFT_PER_AC, imp_ac * 1.35), imp_ac)
    v = detention(imp_ac, max(disturbed - imp_ac, 0), meta.get("hsg"))
    npd = 1 if v <= 10 else 2 if v <= 20 else 3
    w0, Lf, Wf, _ = pond_dims(v / npd)
    pad = [z for (x, y), z in elev.items() if p.module.contains(Point(x, y))]
    pad_z = sum(pad) / len(pad) if pad else None
    region = (parcel.buffer(-10).difference(wet_buf).difference(trans).difference(cut).difference(occupied.buffer(15))
              .difference(rp.buffer(10)).intersection(P.buffer(rules["pave"].get("ROW", 10) + 1)))
    if others is not None:
        region = region.difference(others["blocked"])
    ponds = []
    for _k in range(npd):
        r = place_pond(region, Lf, Wf, elev, [0, 90, 45, 135, p.frame.pl.angle_deg, p.frame.pl.angle_deg + 90], p.module, pad_z, constraints)
        if r is None:
            break
        ponds.append(r); region = region.difference(r.buffer(20))
    pond_ok = len(ponds) == npd
    if not pond_ok:
        flags.append(("POND_DOES_NOT_FIT", 2, f"{npd} pond(s) of {v / npd:.1f} ac-ft do not fit on the development side: underground detention (vaults) or a smaller building"))
    # drop yard near the court, counted only with a truck route
    drops, trailers = [], p.court_stalls
    if ds:
        free = (P.difference(occupied.buffer(20)).difference(rp.buffer(8)).difference(wet_buf).difference(cut)
                .difference(unary_union(ponds).buffer(20) if ponds else Polygon()))
        if others is not None:
            free = free.difference(others["blocked"])
        court_u = unary_union(p.courts)
        yd = place_near(free, ds[0], ds[1], court_u.centroid, [p.frame.pl.angle_deg, p.frame.pl.angle_deg + 90, 0, 90, 45, 135])
        if yd is not None:
            a, b2 = nearest_points(yd, court_u)
            link_walk = (base.buffer(-15).union(yd).union(court_u.buffer(5)).difference(occupied.difference(court_u).buffer(-1))
                         .difference(unary_union(ponds).buffer(5) if ponds else Polygon()))
            if a.distance(b2) < 20:
                link = LineString([a, b2])
            else:
                link = grid_route(Point(yd.centroid), b2, link_walk, steep)
            if link is not None:
                drops.append(yd); trailers += need
                if link.length > 20:
                    route_polys.append(("truck", link.buffer(15, cap_style=2).intersection(parcel)))
            else:
                flags.append(("DROP_YARD_UNREACHABLE", 2, "a drop-trailer yard fits only where no truck route reaches the court"))
    # grading over the whole module
    mod_pts = [z for (x, y), z in elev.items() if p.module.contains(Point(x, y))]
    relief = (max(mod_pts) - min(mod_pts)) if len(mod_pts) > 3 else None
    cutfill = None
    if len(mod_pts) > 3:
        mean = sum(mod_pts) / len(mod_pts)
        cutfill = p.module.area * (sum(abs(z - mean) for z in mod_pts) / len(mod_pts)) / 2 / 27
    steep_mod = (p.module.intersection(steep).area / p.module.area * 100) if not steep.is_empty else 0.0
    feasible = pond_ok and not problem and (tpt is not None or not runs)
    return {"ok": feasible, "route_problem": problem, "tpt": tpt, "cpt": cpt, "shared_access": shared, "routes": routes, "route_polys": route_polys,
            "drops": drops, "trailers": trailers, "imp_ac": imp_ac, "v_det": v, "ponds": ponds, "relief": relief, "cutfill_cy": cutfill,
            "steep_module_pct": steep_mod, "flags": flags}

def plan_site(sid, title, g_wgs, meta, extra_cut_wgs=None, tax_acres=None):
    plane = Plane(g_wgs)
    parcel = clean(plane.ft(shapely.make_valid(g_wgs)), 1.0)
    jname, rules = jur_rules(meta.get("jurisdiction", ""))
    rules = json.loads(json.dumps(rules))
    for k in ("pave",):
        rules[k]["OTHER"] = max(rules[k].get("OTHER", 0), 10)
    prule = parking_rule_for(jname, rules)
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
        flags.append(("TRANSMISSION_EASEMENT_ASSUMED", 2, f"{len(tl)} mapped transmission line(s); corridor widths assumed by voltage; no building or pond under the lines, paving only with the utility's consent"))
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
        flags.append(("ACCESS_NONE", 1, "no public road frontage from the GDOT check: landlocked or access by easement only; the plan is hypothetical and its floor area is not counted"))
    zl = (meta.get("zoning") or "").upper()
    if jname.startswith("east point") and zl in ("I-1", "I1", "I-2", "I2"):
        flags.append(("USE_PERMIT_REQUIRED", 1, "East Point: warehousing is use-permit only in I-1/I-2 and needs 5,280 ft from residential (draft reading)"))
    if "chattahoochee" in (meta.get("jurisdiction") or "").lower():
        flags.append(("USE_PERMIT_REQUIRED", 1, "Chattahoochee Hills: no by-right warehousing"))
    if jname.startswith("fairburn") and fr.road and "local" in fr.road:
        flags.append(("COLLECTOR_FRONTAGE", 2, "Fairburn M-districts require collector-street frontage; this frontage reads as a local road"))
    if prule == "market":
        flags.append(("ATL_PARKING_CODE", 2, "parking at the market ratio; Atlanta's 1/600 SF code minimum (unverified after the parking reforms) would need far more"))
    ctx = {"parcel": parcel, "P": P, "wet_buf": wet_buf, "trans": trans, "cut": cut, "steep": steep, "elev": elev, "meta": meta,
           "rules": rules, "runs": runs, "jname": jname, "constraints": constraints, "road": fr.road}
    results, excluded = [], Polygon()
    for attempt in range(3):
        Pe, Be = clean(P.difference(excluded)), clean(B.difference(excluded))
        fits = [q for q in (fit_variant(v, Pe, Be, jname, prule, row_line, res_line) for v in VARIANTS) if q is not None]
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
            picked.append(q); seen.add(key)
            if len(picked) >= 8:
                break
        for q in picked:
            cur = q
            for _i in range(10):
                o = finish(cur, ctx)
                if o["ok"]:
                    results.append((cur, o)); break
                if o["route_problem"]:
                    results.append((cur, o)); break
                nxt = fit_variant(cur.v, Pe, Be, jname, prule, row_line, res_line, length_cap=int(cur.L - 40))
                if nxt is None:
                    results.append((cur, o)); break
                cur = nxt
            else:
                results.append((cur, o))
        if any(o["ok"] for _, o in results):
            break
        bad = [t for t in results if t[1]["route_problem"]]
        if not bad:
            break
        excluded = unary_union([excluded] + [t[0].module.buffer(60) for t in bad])   # unreachable pads: try elsewhere
    if not results:
        sev = {1: [{"flag": "NO_FIT", "note": "no building variant fits the envelope with its truck court, parking and pond"}], 2: [], 3: []}
        for f, s, n in flags:
            sev[s].append({"flag": f, "note": n})
        return {"id": sid, "title": title, "fits": False, "jurisdiction_rules": jname, "flags": sev,
                "inputs": summary_inputs(parcel, P, B, constraints, trans, cut, steep, fr, edges)}, None
    def score(t):
        q, o = t
        st = o["steep_module_pct"] / 100
        rel = (o["relief"] or 0)
        return (o["ok"], not o["route_problem"], q.sf * (1 - 0.6 * st) - 400 * max(rel - 25, 0) - 2000 * max(0, q.trailers_target - o["trailers"]))
    results.sort(key=score, reverse=True)
    best_p, best_o = results[0]
    second = None
    if best_o["ok"]:
        blocked = unary_union([best_p.module.buffer(40)] + [d.buffer(25) for d in best_o["drops"]] + [r.buffer(20) for r in best_o["ponds"]]
                              + [pp.buffer(15) for _, pp in best_o["route_polys"]])
        P2, B2 = clean(P.difference(blocked)), clean(B.difference(blocked))
        if P2.area > 3.5 * SQFT_PER_AC:
            c2 = [q for q in (fit_variant(v, P2, B2, jname, prule, row_line, res_line) for v in VARIANTS) if q is not None and q.sf >= 50000]
            others = {"occupied": best_p.module, "blocked": blocked, "tpt": best_o["tpt"], "cpt": best_o["cpt"]}
            for q in sorted(c2, key=lambda q: -q.sf)[:6]:
                o2 = finish(q, ctx, others)
                if o2["ok"]:
                    second = (q, o2); break
    rec = report(sid, title, best_p, best_o, second, parcel, P, B, meta, rules, jname, flags, fr, hypothetical, tax_acres, res_line, row_line)
    rec["alternatives"] = [{"variant": q.v["name"], "operator": q.v["operator"], "sf": rnd(q.sf, 1000), "L": q.L, "D": q.v["D"],
                            "feasible": o["ok"], "route_problem": o["route_problem"]} for q, o in results[:10]]
    rec["inputs"] = summary_inputs(parcel, P, B, constraints, trans, cut, steep, fr, edges)
    svg = v3.render(title, parcel, P, constraints, trans, cut, steep, front_ft, best_p, best_o, second, rec)
    return rec, svg

def report(sid, title, p, o, second, parcel, P, B, meta, rules, jname, flags0, fr, hypothetical, tax_acres, res_line, row_line):
    flags = list(flags0) + o["flags"] + ([("SECOND_" + f, s, "second building: " + n) for f, s, n in second[1]["flags"]] if second else [])
    gross = parcel.area / SQFT_PER_AC
    feasible = o["ok"] and not hypothetical
    total = p.sf + (second[0].sf if second else 0)
    clear = p.clear
    if rules.get("height") and clear + 10 > rules["height"]:
        clear = rules["height"] - 10
        sev = 1 if clear < 28 else 2
        flags.append(("HEIGHT_CAP", sev, f"draft height cap {rules['height']} ft: clear height lowered to {clear} ft" + (" - below market for distribution" if sev == 1 else "")))
    if o["trailers"] < p.trailers_target:
        flags.append(("TRAILER_SHORT", 2, f"{o['trailers']} trailer stalls against a target of {p.trailers_target}"))
    for c in p.courts + p.rows:
        if res_line is not None and c.distance(res_line) < 150:
            flags.append(("LOADING_FACES_RES", 2, "a truck court or trailer row is within 150 ft of a residential edge (50 ft loading strip and screening likely)")); break
    for c in p.courts + p.rows:
        if row_line is not None and c.distance(row_line) < 100:
            flags.append(("STREET_FACING_DOCKS", 2, "a truck court faces the street within 100 ft (screening; some codes forbid it)")); break
    cov = rules.get("cover")
    if cov:
        kind, capv = cov
        bl = p.building.area + (second[0].building.area if second else 0)
        val = bl / parcel.area if kind == "building" else (bl + sum(c.area for c in p.cars)) / parcel.area if kind == "building+parking" else o["imp_ac"] / gross
        if val > capv:
            flags.append(("COVERAGE_OVER_CAP", 1, f"{kind} coverage {val:.0%} over the draft cap {capv:.0%}"))
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
    flags.append(("ZONING_DRAFT", 2, f"setbacks and buffers are draft screening values for {jname}; zoning_rules.json is the authority"))
    flags.append(("STREAMS_PIPELINES", 2, "stream lines and petroleum pipelines are not mapped; mapped water, wetlands and floodplain get a 25 ft buffer"))
    if o["ponds"]:
        flags.append(("POND_SITE_FROM_LIDAR", 3, "pond placed low, near the module and on its side of mapped wetlands; no outfall, maintenance drive or grading design"))
    sevd = {1: [], 2: [], 3: []}; seen = set()
    for f, s, n in flags:
        if (f, n) not in seen:
            seen.add((f, n)); sevd[s].append({"flag": f, "note": n})
    cars_prov = max(min(p.car_cap, math.ceil(p.cars_required / 5) * 5), p.cars_required) if p.car_cap >= p.cars_required else p.car_cap
    v_total = o["v_det"] + (second[1]["v_det"] if second else 0)
    imp_total = o["imp_ac"] + (second[1]["imp_ac"] if second else 0)
    rec = {
        "id": sid, "title": title, "fits": True, "hypothetical": hypothetical, "feasible": feasible, "jurisdiction_rules": jname,
        "configuration": p.v["name"], "operator_prototype": p.v["operator"],
        "building": {"L_ft": p.L, "D_ft": p.v["D"], "sf": rnd(p.sf, 1000), "office_sf": rnd(OFFICE_SHARE * p.sf, 500), "clear_height_ft": clear},
        "total_sf": rnd(total, 1000) if feasible else 0, "total_sf_if_access": rnd(total, 1000),
        "doors": p.doors,
        "trailers": {"court_rows": p.court_stalls, "drop_yards": o["trailers"] - p.court_stalls, "total": o["trailers"], "target": p.trailers_target},
        "cars": {"provided": cars_prov, "row_capacity": p.car_cap, "required": p.cars_required, "basis": p.cars_rule, "ada": ada_count(cars_prov),
                 "per_1000_sf": round(cars_prov / p.sf * 1000, 2), "stall": f"{stall_width(jname)} x 18 ft"},
        "coverage": {"gross_ac": round(gross, 1), "sf_per_gross_ac": rnd(sfa, 100), "gross_far": round(total / (gross * SQFT_PER_AC), 2),
                     "impervious_ac": round(imp_total, 1), "impervious_pct": round(imp_total / gross * 100), "paving_envelope_ac": round(P.area / SQFT_PER_AC, 1)},
        "detention": {"storage_acft": round(v_total, 1), "ponds": len(o["ponds"]) + (len(second[1]["ponds"]) if second else 0), "fits": o["ok"],
                      "basis": f"1.15 x (k_imp x impervious + k_perv x disturbed pervious), HSG {meta.get('hsg') or 'B'}; 6 ft deep, 4:1 slopes, L=2W, 1 ft freeboard, 15 ft rim; screening only (+/-30%)"},
        "access": {"truck_driveway": o["tpt"] is not None, "car_driveway": o["cpt"] is not None, "frontage_ft": fr.feet, "road": fr.road,
                   "truck_route_ft": round(sum(l.length for k, l in o["routes"] if k == "truck")), "car_route_ft": round(sum(l.length for k, l in o["routes"] if k == "car"))},
        "grading": {"relief_ft": round(o["relief"]) if o["relief"] else None, "cut_fill_cy": rnd(o["cutfill_cy"], 1000) if o["cutfill_cy"] else None,
                    "steep_share_pct": round(o["steep_module_pct"]), "basis": "one balanced plane over the building, courts and parking (lidar sampled at 40 ft); no terraces"},
        "flags": sevd,
    }
    if second:
        q, o2 = second
        rec["second_building"] = {"configuration": q.v["name"], "L_ft": q.L, "D_ft": q.v["D"], "sf": rnd(q.sf, 1000), "doors": q.doors,
                                  "trailers": o2["trailers"], "trailers_target": q.trailers_target, "cars_capacity": q.car_cap, "cars_required": q.cars_required,
                                  "own_driveways": not o2["shared_access"], "route_ft": round(sum(l.length for k, l in o2["routes"] if k == "truck"))}
        rec["totals_both_buildings"] = {"doors": p.doors + q.doors, "trailers": o["trailers"] + o2["trailers"],
                                        "trailers_target": p.trailers_target + q.trailers_target, "cars_capacity": p.car_cap + q.car_cap}
    return rec
