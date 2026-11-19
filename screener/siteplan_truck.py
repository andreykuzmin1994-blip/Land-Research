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
AVOID_COST = 8.0                    # ground round a corner the body cut on an earlier try (swing wide)
DEAR_COST = 3.0                     # ground the path may take only at a price (car parking given up to the trucks' lead-in)
BODY_CLEAR = WB67["width"] / 2 + 2.0        # the centreline keeps half a body and 2 ft off every obstacle
MAX_EXPAND = 200000                 # poses expanded before the search gives up (a fixed count: the same site, the same path)
GREED = 1.15                        # the distance still to go (round what is in the way) counts this much: fewer poses tried
BEND_MIN_DEG = 30.0                 # arcs to one hand adding up to less are a lane shift, not a bend
REVERSE_MIN_FT = LENGTH_FT          # a reverse bend: less straight than this between two bends to opposite hands
CLIP_TOL_FT = 2.0                   # the swept path may brush an obstacle's edge by this much
SWING_TRIES = 3                     # tries at a wider line where the body cut a corner
SWING_FT = 12.0
SWING_MAX_LONGER = 1.25             # a wider line is at most this much longer than the first; more is a different way round


def _rect(a: tuple[float, float], b: tuple[float, float], back: float, fwd: float, w: float) -> list[tuple[float, float]]:
    """Corners of the body lying along a -> b: from `back` behind a to `fwd` ahead of b, `w` wide."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    px, py = -uy * w / 2, ux * w / 2
    a2, b2 = (a[0] - ux * back, a[1] - uy * back), (b[0] + ux * fwd, b[1] + uy * fwd)
    return [(a2[0] + px, a2[1] + py), (b2[0] + px, b2[1] + py), (b2[0] - px, b2[1] - py), (a2[0] - px, a2[1] - py)]


def sweep(path: LineString, veh: dict[str, float] = WB67, ds: float = 3.0, sub: int = 6) -> tuple[BaseGeometry, LineString]:
    """The swept path of `veh` driving `path` forward (its steer axle's
    centre on the line), and the track of the middle of the trailer's
    axles. The vehicle starts straight, in line with the first leg and
    behind its first point. Each unit trails the one ahead: the drive
    axles stay `wb1` behind the steer axle, the trailer's axles `wb2`
    behind the fifth wheel (taken over the drive axles). The bodies are
    drawn every `ds` ft; the trailing is stepped `sub` times finer, so
    the track does not depend on how often they are drawn."""
    if path is None or path.is_empty or path.length < 1.0:
        return Polygon(), LineString()
    n = max(2, int(math.ceil(path.length / ds)) + 1)
    m_ = (n - 1) * sub
    f = path.interpolate(0.0)
    f = (f.x, f.y)
    q = path.interpolate(min(1.0, path.length))
    m = math.hypot(q.x - f[0], q.y - f[1]) or 1.0
    ux, uy = (q.x - f[0]) / m, (q.y - f[1]) / m
    r1 = (f[0] - ux * veh["wb1"], f[1] - uy * veh["wb1"])
    r2 = (r1[0] - ux * veh["wb2"], r1[1] - uy * veh["wb2"])
    shapes, prev, track = [], None, [r2]
    for i in range(m_ + 1):
        p = path.interpolate(path.length * i / m_)
        f = (p.x, p.y)
        vx, vy = r1[0] - f[0], r1[1] - f[1]
        m = math.hypot(vx, vy) or 1.0
        r1 = (f[0] + vx / m * veh["wb1"], f[1] + vy / m * veh["wb1"])
        vx, vy = r2[0] - r1[0], r2[1] - r1[1]
        m = math.hypot(vx, vy) or 1.0
        r2 = (r1[0] + vx / m * veh["wb2"], r1[1] + vy / m * veh["wb2"])
        if i % sub:
            continue
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
    only at a price, 4 round a corner the body cut on an earlier try."""

    def __init__(self, free: BaseGeometry, near: BaseGeometry | None, dear: BaseGeometry | None = None,
                 avoid: BaseGeometry | None = None):
        self.minx, self.miny, maxx, maxy = free.bounds
        self.nx = int((maxx - self.minx) / MASK) + 2
        self.ny = int((maxy - self.miny) / MASK) + 2
        xs = [self.minx + i * MASK for j in range(self.ny) for i in range(self.nx)]
        ys = [self.miny + j * MASK for j in range(self.ny) for i in range(self.nx)]
        self.v = [1 if a else 0 for a in shapely.contains_xy(free, xs, ys).tolist()]
        for level, zone in ((2, near), (3, dear), (4, avoid)):
            if zone is not None and not zone.is_empty:
                self.v = [level if (a and c) else a for a, c in zip(self.v, shapely.contains_xy(zone, xs, ys).tolist())]

    def at(self, x: float, y: float) -> int:
        i, j = int((x - self.minx) / MASK + 0.5), int((y - self.miny) / MASK + 0.5)
        if i < 0 or j < 0 or i >= self.nx or j >= self.ny:
            return 0
        return self.v[j * self.nx + i]


    def reach(self, lane: LineString, lateral: float) -> Any:
        """How far each spot of the ground is from the goal lane, going
        round what is in the way: shortest paths on every other sample
        (10 ft, eight neighbours) from the samples on the lane. Returns
        a function (x, y) -> ft, or None for a spot no path reaches. It
        steers the pose search and ends it at once where the goal is cut
        off."""
        step = 2 * MASK
        nx, ny = (self.nx + 1) // 2, (self.ny + 1) // 2
        ok = [self.v[(2 * j) * self.nx + 2 * i] > 0 for j in range(ny) for i in range(nx)]
        dist = [math.inf] * (nx * ny)
        pq = []
        for j in range(ny):
            for i in range(nx):
                if ok[j * nx + i] and lane.distance(Point(self.minx + i * step, self.miny + j * step)) <= lateral + step:
                    dist[j * nx + i] = 0.0
                    pq.append((0.0, i, j))
        heapq.heapify(pq)
        nb = ((1, 0, step), (-1, 0, step), (0, 1, step), (0, -1, step),
              (1, 1, step * 1.4142), (1, -1, step * 1.4142), (-1, 1, step * 1.4142), (-1, -1, step * 1.4142))
        while pq:
            d, i, j = heapq.heappop(pq)
            if d > dist[j * nx + i]:
                continue
            for di, dj, w in nb:
                a, b = i + di, j + dj
                if 0 <= a < nx and 0 <= b < ny and ok[b * nx + a] and d + w < dist[b * nx + a]:
                    dist[b * nx + a] = d + w
                    heapq.heappush(pq, (d + w, a, b))
        minx, miny = self.minx, self.miny

        def h(x: float, y: float) -> float | None:
            u, w = (x - minx) / step, (y - miny) / step
            best = math.inf
            for i in (int(u), int(u) + 1):
                for j in (int(w), int(w) + 1):
                    if 0 <= i < nx and 0 <= j < ny:
                        d = dist[j * nx + i]
                        if d < best:
                            best = d
            return None if best == math.inf else best
        return h


_COST = (0.0, 1.0, NEAR_COST, DEAR_COST, AVOID_COST)


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


def drive(start: tuple[float, float], heading: float, lane: tuple[tuple[float, float], tuple[float, float]],
          free: BaseGeometry, near: BaseGeometry | None = None, lateral: float = 12.0, way: int = 0,
          max_expand: int = MAX_EXPAND, dear: BaseGeometry | None = None,
          avoid: BaseGeometry | None = None) -> dict[str, Any] | None:
    """A centreline from the pose (start, heading, radians) onto the goal
    lane: the segment `lane`, reached within `lateral` ft of it and
    heading along it within half a TURN, either way (`way` 0) or only
    from its first point to its second (+1) or back (-1). It stays on
    `free` (dearer on `near`, dearer still on `dear`) and turns no tighter
    than R_MIN; it ends with the small arc that squares it to the lane
    and a body length of run-on. Returns {"line", "moves", "expanded",
    "way"} or None when the lane cannot be reached or no such path was
    found within max_expand poses."""
    if free is None or free.is_empty:
        return None
    mask = _Mask(free, near, dear, avoid)
    if not mask.at(*start):
        return None
    (ax, ay), (bx, by) = lane
    length = math.hypot(bx - ax, by - ay)
    if length < 1.0:
        return None
    gc, gs = (bx - ax) / length, (by - ay) / length
    lane_dir = math.atan2(gs, gc)
    reach = mask.reach(LineString(lane), lateral)
    h0 = reach(*start)
    if h0 is None:
        return None                         # the lane is cut off from the start: no pose search will find it
    moves = _moves(heading)
    half = TURN / 2 + 1e-9

    def done(x, y, k):
        a = (x - ax) * gc + (y - ay) * gs
        if not (0.0 <= a <= length) or abs(-(x - ax) * gs + (y - ay) * gc) > lateral:
            return 0
        d = (heading + k * TURN - lane_dir + math.pi) % (2 * math.pi) - math.pi
        if abs(d) <= half and way >= 0:
            return 1
        if abs(abs(d) - math.pi) <= half and way <= 0:
            return -1
        return 0

    # Every pose tried is kept as its own record (place, heading, the pose
    # it came from, the move that made it) and never changed. A cheaper way
    # to the same cell and heading makes a new record; what was built on
    # the old one still points at the old one. So the line built at the
    # end is exactly the moves that were searched (review 43/02, finding 1).
    key0 = (int(start[0] // CELL), int(start[1] // CELL), 0)
    nodes: list[tuple] = [(start[0], start[1], 0, -1, -1)]
    best = {key0: 0.0}
    pq = [(GREED * h0, 0, 0, 0.0)]
    n, tick, end, went = 0, 0, None, 0
    while pq:
        _, _, node, g = heapq.heappop(pq)
        x, y, k, _, _ = nodes[node]
        if g > best[(int(x // CELL), int(y // CELL), k)] + 1e-9:
            continue                        # a cheaper way to this cell and heading was found since
        went = done(x, y, k)
        if went:
            end = node
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
            nkey = (int(nx_ // CELL), int(ny_ // CELL), nk)
            ng = g + cost * _COST[worst]
            if ng < best.get(nkey, 1e18) - 1e-9:
                hh = reach(nx_, ny_)
                if hh is None:
                    continue
                best[nkey] = ng
                nodes.append((nx_, ny_, nk, node, idx))
                tick += 1
                heapq.heappush(pq, (ng + GREED * hh, tick, len(nodes) - 1, ng))
    if end is None:
        return None
    chain = []
    node = end
    while nodes[node][3] >= 0:
        _, _, _, parent, idx = nodes[node]
        chain.append((nodes[parent][:3], idx))
        node = parent
    chain.reverse()
    pts, mv = [start], []
    for (x, y, k), idx in chain:
        dx, dy, dk, cost, hand, R, samples = moves[k][idx]
        mv.append((hand, R, STEP if hand == 0 else R * TURN, (x, y)))
        pts += [(x + sx, y + sy) for sx, sy in samples]
    # square to the lane: the last few degrees on an easy arc, then a body length of run-on
    x, y, k = nodes[end][:3]
    h_end = heading + k * TURN
    target = lane_dir if went > 0 else lane_dir + math.pi
    d = (target - h_end + math.pi) % (2 * math.pi) - math.pi
    if abs(d) > 1e-6:
        hand = 1 if d > 0 else -1
        cx, cy = x + hand * R_EASY * -math.sin(h_end), y + hand * R_EASY * math.cos(h_end)
        for t in (0.5, 1.0):
            hh = h_end + d * t
            pts.append((cx - hand * R_EASY * -math.sin(hh), cy - hand * R_EASY * math.cos(hh)))
        mv.append((hand, R_EASY, R_EASY * abs(d), (x, y)))
    lx, ly = pts[-1]
    pts.append((lx + 30.0 * math.cos(target), ly + 30.0 * math.sin(target)))
    mv.append((0, 0.0, 30.0, (lx, ly)))
    return {"line": LineString(pts), "moves": mv, "expanded": n, "way": went}


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
            deg = math.degrees(length / R)
            if cur is not None and cur["hand"] == hand and gap < 2 * STEP:
                cur["deg"] += deg
                cur["radius"] = min(cur["radius"], R)
            else:
                cur = {"hand": hand, "deg": deg, "radius": R, "at": at, "from_start_ft": dist, "straight_before_ft": gap}
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


def design(start: tuple[float, float], heading: float, lane: tuple[tuple[float, float], tuple[float, float]],
           ground: BaseGeometry, hard: BaseGeometry, keep_off: BaseGeometry | None = None,
           mouth: BaseGeometry | None = None, dear: BaseGeometry | None = None, way: int = 0) -> dict[str, Any] | None:
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
    avoid = None
    for attempt in range(SWING_TRIES + 1):
        got = drive(start, heading, lane, free, near, way=way, dear=dear, avoid=avoid)
        if got is None:
            break
        line = got["line"]
        if best is not None and line.length > SWING_MAX_LONGER * best["line"].length:
            break                           # a way round, not a wider line: the corner stays as it was
        s_in, _ = sweep(line)
        s_out, _ = sweep(LineString(list(line.coords)[::-1]))
        swept = unary_union([s_in, s_out])
        depth, where = clip(swept, allowed)
        b = bends(got["moves"])
        cur = {"line": line, "swept": swept, "bends": b["bends"], "reverse": b["reverse"], "min_radius_ft": b["min_radius_ft"],
               "clip_ft": depth, "clip_at": where, "ok": depth <= CLIP_TOL_FT, "expanded": got["expanded"], "swings": attempt,
               "way": got["way"]}
        if best is None or depth < best["clip_ft"] - 0.5:
            best = cur
        if cur["ok"] or where is None:
            break
        # Swing wide: the body cut a corner there, so the ground round it
        # is made dear and the centreline keeps further off on the next
        # try where it can (as a driver would).
        disc = Point(where).buffer(BODY_CLEAR + depth + SWING_FT * (attempt + 1))
        avoid = disc if avoid is None else unary_union([avoid, disc])
    return best
