"""Truck design for a drawn test fit: a path a tractor-semitrailer can
steer from the street to the truck court, and the ground its body sweeps.

Pure geometry on the feet plane (shapely and math only; no network, no
planner state). `screener.siteplan` calls it for the plan it draws, in the
default behaviour only; the search's own routes (a shortest path on a 20 ft
grid, `grid_route`) are untouched.

Three parts:

- `sweep`: the swept path of the design vehicle whose steer axle follows a
  centreline. Each unit trails the one ahead at a fixed length (the
  low-speed off-tracking of a tractor and a semitrailer); the swept path
  is the ground the two bodies cover.
- `drive`: a centreline from a start pose to a goal line that turns no
  tighter than the vehicle can steer. A search over poses (x, y, heading)
  with three moves: straight on, and an arc to either hand at one of two
  radii. Ground near an obstacle costs more, so the path keeps clear of
  corners where there is room and still passes where there is not.
- `bends`: the bends of that centreline, and the reverse bends (one hand
  then the other with less than a truck's length of straight between).

A screening check, not a design: one vehicle, forward only, at walking
speed; no backing manoeuvre, no opposing truck in a bend, no grades.
"""
from __future__ import annotations

import heapq
import math
from typing import Any

import shapely
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# The design vehicle: AASHTO WB-67, the interstate semitrailer (53 ft
# trailer). Feet. `front` and `rear` are the overhangs ahead of the steer
# axle and behind the trailer's axle group; `wb1` the tractor's wheelbase
# to the fifth wheel, `wb2` the kingpin to the middle of the trailer's
# axles; `nose` the trailer ahead of its kingpin, `tail` the tractor
# behind its drive axles. Sources and what was checked against them:
# reviews/43_site_plan_trucks/01_decision_note.md.
WB67: dict[str, float] = {"front": 4.0, "wb1": 19.5, "wb2": 45.5, "rear": 4.5, "nose": 3.0, "tail": 2.5, "width": 8.5}
VEHICLE_NAME = "WB-67"
LENGTH_FT = WB67["front"] + WB67["wb1"] + WB67["wb2"] + WB67["rear"]      # 73.5

# The tightest centreline the path may take (the steer axle's centre): the
# vehicle's minimum design turning radius is 45 ft at its outer front
# wheel, about 41 ft at the centre of the axle. 45 ft leaves a little in
# hand. The second radius is what the search takes where there is room.
R_MIN = 45.0
R_EASY = 90.0
TURN = math.radians(15.0)           # each arc move turns this much
HEADINGS = 24                       # 360 / 15
STEP = 10.0                         # a straight move, ft
CELL = 8.0                          # poses closer than this (same heading) are one pose
MASK = 5.0                          # the ground is sampled on this grid, ft
ARC_COST = {R_MIN: 1.5, R_EASY: 1.15}       # a tight arc costs more than its length
NEAR_FT = 25.0                      # ground this near an obstacle (beyond the body's own clearance) costs NEAR_COST
NEAR_COST = 1.6
DEAR_COST = 3.0                     # ground the path may take only at a price (car parking given up to the trucks' lead-in)
BODY_CLEAR = WB67["width"] / 2 + 2.0        # the centreline keeps half a body and 2 ft off every obstacle
MAX_EXPAND = 120000                 # poses expanded before the search gives up (a fixed count: the same site, the same path)
GREED = 1.25                        # the straight-line distance still to go counts this much (fewer poses tried; a path at most that much dearer than the best)
BEND_MIN_DEG = 30.0                 # arcs to one hand adding up to less are a lane shift, not a bend
REVERSE_MIN_FT = LENGTH_FT          # a reverse bend: less straight than this between two bends to opposite hands
CLIP_TOL_FT = 2.0                   # the swept path may brush an obstacle's edge by this much
SWING_TRIES = 3                     # tries at a wider line where the body cut a corner
SWING_FT = 12.0


def _rect(a: tuple[float, float], b: tuple[float, float], back: float, fwd: float, w: float) -> list[tuple[float, float]]:
    """Corners of the body lying along a -> b: from `back` behind a to `fwd` ahead of b, `w` wide."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    px, py = -uy * w / 2, ux * w / 2
    a2, b2 = (a[0] - ux * back, a[1] - uy * back), (b[0] + ux * fwd, b[1] + uy * fwd)
    return [(a2[0] + px, a2[1] + py), (b2[0] + px, b2[1] + py), (b2[0] - px, b2[1] - py), (a2[0] - px, a2[1] - py)]


def sweep(path: LineString, veh: dict[str, float] = WB67, ds: float = 3.0) -> tuple[BaseGeometry, LineString]:
    """The swept path of `veh` driving `path` forward (its steer axle's
    centre on the line), and the track of the middle of the trailer's
    axles. The vehicle starts straight, in line with the first leg and
    behind its first point. Each unit trails the one ahead: the drive
    axles stay `wb1` behind the steer axle, the trailer's axles `wb2`
    behind the fifth wheel (taken over the drive axles)."""
    if path is None or path.is_empty or path.length < 1.0:
        return Polygon(), LineString()
    n = max(2, int(math.ceil(path.length / ds)) + 1)
    pts = [path.interpolate(path.length * k / (n - 1)) for k in range(n)]
    f = (pts[0].x, pts[0].y)
    q = path.interpolate(min(1.0, path.length))
    m = math.hypot(q.x - f[0], q.y - f[1]) or 1.0
    ux, uy = (q.x - f[0]) / m, (q.y - f[1]) / m
    r1 = (f[0] - ux * veh["wb1"], f[1] - uy * veh["wb1"])
    r2 = (r1[0] - ux * veh["wb2"], r1[1] - uy * veh["wb2"])
    shapes, prev, track = [], None, [r2]
    for p in pts:
        f = (p.x, p.y)
        vx, vy = r1[0] - f[0], r1[1] - f[1]
        m = math.hypot(vx, vy) or 1.0
        r1 = (f[0] + vx / m * veh["wb1"], f[1] + vy / m * veh["wb1"])
        vx, vy = r2[0] - r1[0], r2[1] - r1[1]
        m = math.hypot(vx, vy) or 1.0
        r2 = (r1[0] + vx / m * veh["wb2"], r1[1] + vy / m * veh["wb2"])
        track.append(r2)
        tractor = _rect(r1, f, veh["tail"], veh["front"], veh["width"])
        trailer = _rect(r2, r1, veh["rear"], veh["nose"], veh["width"])
        if prev is not None:
            shapes.append(MultiPoint(prev[0] + tractor).convex_hull)
            shapes.append(MultiPoint(prev[1] + trailer).convex_hull)
        prev = (tractor, trailer)
    return unary_union(shapes), LineString(track)


class _Mask:
    """Where the centreline may go, sampled on a MASK ft grid: 0 not at
    all, 1 freely, 2 near an obstacle (dearer), 3 on ground it may take
    only at a price."""

    def __init__(self, free: BaseGeometry, near: BaseGeometry | None, dear: BaseGeometry | None = None):
        self.minx, self.miny, maxx, maxy = free.bounds
        self.nx = int((maxx - self.minx) / MASK) + 2
        self.ny = int((maxy - self.miny) / MASK) + 2
        xs = [self.minx + i * MASK for j in range(self.ny) for i in range(self.nx)]
        ys = [self.miny + j * MASK for j in range(self.ny) for i in range(self.nx)]
        self.v = [1 if a else 0 for a in shapely.contains_xy(free, xs, ys).tolist()]
        for level, zone in ((2, near), (3, dear)):
            if zone is not None and not zone.is_empty:
                self.v = [level if (a and c) else a for a, c in zip(self.v, shapely.contains_xy(zone, xs, ys).tolist())]

    def at(self, x: float, y: float) -> int:
        i, j = int((x - self.minx) / MASK + 0.5), int((y - self.miny) / MASK + 0.5)
        if i < 0 or j < 0 or i >= self.nx or j >= self.ny:
            return 0
        return self.v[j * self.nx + i]


_COST = (0.0, 1.0, NEAR_COST, DEAR_COST)


def _moves(h0: float) -> list[list[tuple]]:
    """For each heading index, the moves from a pose at the origin:
    (dx, dy, dk, cost, hand, radius, samples)."""
    out = []
    for k in range(HEADINGS):
        h = h0 + k * TURN
        c, s = math.cos(h), math.sin(h)
        mv = [(STEP * c, STEP * s, 0, STEP, 0, 0.0, ((STEP / 2 * c, STEP / 2 * s), (STEP * c, STEP * s)))]
        for R in (R_MIN, R_EASY):
            for hand in (1, -1):
                cx, cy = hand * R * -s, hand * R * c
                pts = []
                nseg = 2 if R == R_MIN else 4
                for t in range(1, nseg + 1):
                    hh = h + hand * TURN * t / nseg
                    pts.append((cx - hand * R * -math.sin(hh), cy - hand * R * math.cos(hh)))
                mv.append((pts[-1][0], pts[-1][1], hand, R * TURN * ARC_COST[R], hand, R, tuple(pts)))
        out.append(mv)
    return out


def drive(start: tuple[float, float], heading: float, goal: tuple[float, float], goal_dir: float,
          free: BaseGeometry, near: BaseGeometry | None = None, lateral: float = 12.0, along: float = 160.0,
          max_expand: int = MAX_EXPAND, dear: BaseGeometry | None = None) -> dict[str, Any] | None:
    """A centreline from the pose (start, heading, radians) to the goal
    line: a point within `lateral` ft of the line through `goal` along
    `goal_dir`, no more than `along` ft past it, heading within half a
    TURN of it. It stays on `free` (dearer on `near`, dearer still on
    `dear`) and turns no tighter than R_MIN. Returns {"line", "moves", "expanded"} or None when there
    is no such path within max_expand poses."""
    if free is None or free.is_empty:
        return None
    mask = _Mask(free, near, dear)
    if not mask.at(*start):
        return None
    moves = _moves(heading)
    gx, gy = goal
    gc, gs = math.cos(goal_dir), math.sin(goal_dir)
    half = TURN / 2 + 1e-9

    def done(x, y, k):
        d = (heading + k * TURN - goal_dir + math.pi) % (2 * math.pi) - math.pi
        if abs(d) > half:
            return False
        a = (x - gx) * gc + (y - gy) * gs
        return 0.0 <= a <= along and abs(-(x - gx) * gs + (y - gy) * gc) <= lateral

    key0 = (int(start[0] // CELL), int(start[1] // CELL), 0)
    best = {key0: 0.0}
    came: dict[tuple, tuple] = {}
    pose = {key0: (start[0], start[1], 0)}
    pq = [(GREED * math.hypot(gx - start[0], gy - start[1]), 0, key0)]
    n, tick, end = 0, 0, None
    while pq:
        _, _, key = heapq.heappop(pq)
        x, y, k = pose[key]
        g = best[key]
        if done(x, y, k):
            end = key
            break
        n += 1
        if n > max_expand:
            return None
        for idx, (dx, dy, dk, cost, hand, R, samples) in enumerate(moves[k]):
            worst = 1
            for sx, sy in samples:
                v = mask.at(x + sx, y + sy)
                if v == 0:
                    worst = 0
                    break
                if v > worst:
                    worst = v
            if worst == 0:
                continue
            nx_, ny_, nk = x + dx, y + dy, (k + dk) % HEADINGS
            ng = g + cost * _COST[worst]
            nkey = (int(nx_ // CELL), int(ny_ // CELL), nk)
            if ng < best.get(nkey, 1e18) - 1e-9:
                best[nkey] = ng
                pose[nkey] = (nx_, ny_, nk)
                came[nkey] = (key, idx)
                tick += 1
                heapq.heappush(pq, (ng + GREED * math.hypot(gx - nx_, gy - ny_), tick, nkey))
    if end is None:
        return None
    chain = []
    key = end
    while key in came:
        key, idx = came[key]
        chain.append((pose[key], idx))
    chain.reverse()
    pts, mv = [start], []
    for (x, y, k), idx in chain:
        dx, dy, dk, cost, hand, R, samples = moves[k][idx]
        mv.append((hand, R, STEP if hand == 0 else R * TURN, (x, y)))
        pts += [(x + sx, y + sy) for sx, sy in samples]
    # run on a body length along the goal's own direction, so the path ends square to it
    lx, ly = pts[-1]
    pts.append((lx + 30.0 * gc, ly + 30.0 * gs))
    mv.append((0, 0.0, 30.0, (lx, ly)))
    return {"line": LineString(pts), "moves": mv, "expanded": n}


def bends(moves: list[tuple]) -> dict[str, Any]:
    """The bends of a driven path: arcs to one hand (with only short
    straights between) that add up to BEND_MIN_DEG or more. Each has its
    angle, its tightest radius and where it starts. `reverse`: pairs of
    consecutive bends to opposite hands with less than REVERSE_MIN_FT of
    straight between them."""
    groups: list[dict[str, Any]] = []
    cur = None
    gap = 0.0           # straight since the last arc
    dist = 0.0
    for hand, R, length, at in moves:
        if hand == 0:
            gap += length
        else:
            if cur is not None and cur["hand"] == hand and gap < 2 * STEP:
                cur["deg"] += math.degrees(TURN)
                cur["radius"] = min(cur["radius"], R)
            else:
                cur = {"hand": hand, "deg": math.degrees(TURN), "radius": R, "at": at, "from_start_ft": dist, "straight_before_ft": gap}
                groups.append(cur)
            cur["end_ft"] = dist + length
            gap = 0.0
        dist += length
    real = [b for b in groups if b["deg"] >= BEND_MIN_DEG - 1e-6]
    reverse = 0
    for a, b in zip(real, real[1:]):
        if a["hand"] != b["hand"] and b["from_start_ft"] - a["end_ft"] < REVERSE_MIN_FT:
            reverse += 1
    return {"bends": [{"deg": round(b["deg"]), "radius_ft": b["radius"], "hand": "left" if b["hand"] > 0 else "right",
                       "from_start_ft": round(b["from_start_ft"])} for b in real],
            "reverse": reverse, "min_radius_ft": min((b["radius"] for b in real), default=None)}


def clip(swept: BaseGeometry, allowed: BaseGeometry) -> tuple[float, tuple[float, float] | None]:
    """How far (ft) a swept path goes off the ground it may use, and
    where: the point of it farthest outside `allowed`."""
    if swept.is_empty:
        return 0.0, None
    over = swept.difference(allowed)
    if over.is_empty or over.area < 1.0:
        return 0.0, None
    worst, where = 0.0, None
    for g in getattr(over, "geoms", [over]):
        if g.geom_type != "Polygon" or g.is_empty:
            continue
        for c in shapely.segmentize(g.exterior, 4.0).coords:
            d = allowed.distance(Point(c))
            if d > worst:
                worst, where = d, (c[0], c[1])
    return worst, where


def design(start: tuple[float, float], heading: float, goal: tuple[float, float], goal_dir: float,
           ground: BaseGeometry, hard: BaseGeometry, keep_off: BaseGeometry | None = None,
           mouth: BaseGeometry | None = None, dear: BaseGeometry | None = None) -> dict[str, Any] | None:
    """One truck path with its check: a centreline from the street to the
    court that a WB-67 can steer, and its swept path in and back out. The
    body must stay on `ground` (the site) and off `hard` (buildings,
    parked cars and trailers, ponds, the setback strips, water), except
    in `mouth` (the driveway's own opening onto the street). `keep_off`
    is ground the centreline stays off though the body may pass over it
    (the strip of court in front of the dock doors); `dear` is ground
    it may cross only at a price. None when no such centreline exists. Otherwise {"line", "swept", "bends", "reverse",
    "min_radius_ft", "clip_ft", "clip_at", "ok", "expanded"}."""
    block = hard.buffer(BODY_CLEAR) if not hard.is_empty else hard
    free = ground.buffer(-BODY_CLEAR).difference(block)
    if mouth is not None and not mouth.is_empty:
        free = free.union(mouth.intersection(ground).difference(block))
    if keep_off is not None and not keep_off.is_empty:
        free = free.difference(keep_off)
    edge = ground.boundary.buffer(BODY_CLEAR + NEAR_FT)
    near = unary_union([hard.buffer(BODY_CLEAR + NEAR_FT), edge]).intersection(ground)
    allowed = ground.difference(hard)
    if mouth is not None and not mouth.is_empty:
        allowed = allowed.union(mouth)
    best = None
    for attempt in range(SWING_TRIES + 1):
        got = drive(start, heading, goal, goal_dir, free, near, dear=dear)
        if got is None:
            break
        line = got["line"]
        s_in, _ = sweep(line)
        s_out, _ = sweep(LineString(list(line.coords)[::-1]))
        swept = unary_union([s_in, s_out])
        depth, where = clip(swept, allowed)
        b = bends(got["moves"])
        cur = {"line": line, "swept": swept, "bends": b["bends"], "reverse": b["reverse"], "min_radius_ft": b["min_radius_ft"],
               "clip_ft": depth, "clip_at": where, "ok": depth <= CLIP_TOL_FT, "expanded": got["expanded"], "swings": attempt}
        if best is None or depth < best["clip_ft"] - 0.5:
            best = cur
        if cur["ok"] or where is None:
            break
        # Swing wide: the body cut a corner there, so the centreline keeps
        # further from it on the next try (as a driver would).
        free = free.difference(Point(where).buffer(BODY_CLEAR + depth + SWING_FT * (attempt + 1)))
    return best
