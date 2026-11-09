"""Constraint-aware building fit (F6, reviews/34_screener_quick_wins).

For each ranked parcel, after scoring and the sort:

- ``largest_open_acres``: map acres of the largest contiguous part of the
  open ground ``checks.map_checks`` leaves (parcel minus FEMA SFHA
  floodplain, NWI wetlands and water, NCED conservation easements and PAD-US
  protected land). Steep ground is NOT located: the screener only has a
  slope histogram, so steep ground may lie inside the open area.
- the largest copied prototype envelope (building + truck court + trailer
  parking per dock side) found to fit on that open ground inside a 25 ft
  lot-line setback, with a rotated-rectangle search copied from the root
  ``site_fit.py`` (a lower bound: "none found" is not proof nothing fits).

Three states, kept apart in every output: fits / none found / not checked.

This is a live-only report fact. It is never on a parcel while it is
screened, flagged or scored: ``run.main`` calls ``fit_parcel`` only after
the sort, and ``rank.py`` / ``scoring.py`` must never read it (the run.main
spy tests in tests/test_sitefit.py hold that; the static stem check there
is a backstop and cannot see a string built at runtime).

COPIED, not imported: the screener shares no code or files with the root
system (CLAUDE.md scope note), so nothing here imports root modules or reads
``building_prototypes.json`` at run time. tests/test_sitefit.py compares
these constants with the root file (skipped when it is absent).
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Any, Sequence

import shapely
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from .geo import FT_PER_M, LocalProjection, acres

# Name stems of every F6 key (columns and the private envelope key). The
# static guard imports these; do not shorten them to "fit" / "open".
KEY_STEMS = ("largest_open", "envelope")
COLUMNS = ("largest_open_acres", "envelope_fit", "envelope_building_sf")
ENVELOPE_KEY = "_envelope_polygon"   # WGS84 shapely Polygon, for the map (F12)

FITS, NONE_FOUND, NOT_CHECKED = "fits", "none found", "not checked"
NONE_TEXT = "none found"
NOT_CHECKED_PREFIX = "not checked: "

# --- Copied from root building_prototypes.json (schema_version 1, as_of
# 2026-11-05, source "operator SiteFit tool defaults"), the keys the fit
# needs only. Envelope depth per root site_fit.envelope_depth:
# W + sides * (court + trailer), sides 2 for "cross", else 1.
STEP_FT = 40
DEFAULT_SETBACK_FT = 25
PROTOTYPES: dict[str, dict[str, Any]] = {
    "cross": {"label": "cross-dock", "type": "cross", "L": 1000, "W": 520, "court": 130,
              "trailerOn": True, "trailer": 55, "min_length_ft": 600},
    "front": {"label": "front-load", "type": "front", "L": 640, "W": 260, "court": 130,
              "trailerOn": True, "trailer": 55, "min_length_ft": 400},
    "rear": {"label": "rear-load", "type": "rear", "L": 640, "W": 260, "court": 130,
             "trailerOn": True, "trailer": 55, "min_length_ft": 400},
}
# front and rear share one depth, so they are fitted (and named) together.
# Every cross-dock length (>= 312,000 SF) beats every front/rear length
# (<= 166,400 SF), so cross is tried first.
GROUPS = (("cross", "cross-dock"), ("front", "front/rear-load"))

# --- Copied from root site_fit.py (fit search constants and vertex bounds).
TOL_FT = 0.01
_ANGLE_GRID_DEG = 2.0
_EDGE_ANGLES_MAX = 32
_EDGE_ANGLE_MIN_LEN_FT = 30.0
_Y_SAMPLES = 64
_PROBE_FT = 25.0
_SIMPLIFY_ABOVE_VERTICES = 400
_SIMPLIFY_TOL_FT = 1.0
_MAX_FIT_VERTICES = 600

EXCLUDES = ("floodplain (FEMA SFHA), mapped wetlands and water (NWI), conservation "
            "easements (NCED) and protected land (PAD-US)")
STEEP_NOTE = ("steep ground is not located (slope is applied only as a share, in "
              "usable_after_slope), so steep ground may lie inside the open area")
NOT_MODELLED = ("car parking, drives, detention, access, coverage caps and zoning setbacks "
                "beyond the 25 ft lot-line setback are not modelled")


def _sides(p: dict[str, Any]) -> int:
    return 2 if p["type"] == "cross" else 1


def envelope_depth(p: dict[str, Any]) -> float:
    """Root site_fit.envelope_depth: W + sides * (court + trailer)."""
    t = float(p["trailer"]) if p["trailerOn"] else 0.0
    return float(p["W"]) + _sides(p) * (max(0.0, float(p["court"])) + t)


def _lattice(proto: dict[str, Any], step: float) -> list[float]:
    """Root site_fit._lattice without the coverage cap (the screener has none)."""
    lo, hi = float(proto["min_length_ft"]), float(proto["L"])
    vals, v = [], lo
    while v <= hi + 1e-9:
        vals.append(v)
        v += step
    return vals


def specs() -> list[tuple[float, list[float]]]:
    """[(depth, lattice ascending)] merged by depth, as root _fits_for does."""
    by_depth: dict[float, set[float]] = {}
    for p in PROTOTYPES.values():
        by_depth.setdefault(envelope_depth(p), set()).update(_lattice(p, STEP_FT))
    return sorted((d, sorted(v)) for d, v in by_depth.items())


SMALLEST = min((min(lat), d) for d, lat in specs())   # (400.0, 445.0)


# ---------------------------------------------------------------------------
# Rectangle search, copied verbatim from root site_fit.py (do not "improve"
# it here: two fitters that disagree would be worse than one lower bound).
# ---------------------------------------------------------------------------
def _ring_area(ring: Sequence[tuple[float, float]]) -> float:
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _inside(x: float, y: float, rings: Sequence[Sequence[tuple[float, float]]]) -> bool:
    """Even-odd over every ring (outer rings and holes of all parts)."""
    inside = False
    for ring in rings:
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def _angles(rings: Sequence[Sequence[tuple[float, float]]]) -> list[float]:
    """Fixed 2-degree grid plus the directions of the 32 longest edges (each
    edge's direction and its +90), rounded to 0.1 degree, in [0, 180)."""
    out = {round(k * _ANGLE_GRID_DEG, 1) for k in range(int(180 / _ANGLE_GRID_DEG))}
    edges = []
    for ring in rings:
        n = len(ring)
        for i in range(n):
            (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % n]
            ln = math.hypot(x2 - x1, y2 - y1)
            if ln >= _EDGE_ANGLE_MIN_LEN_FT:
                a = round(math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180.0, 1) % 180.0
                edges.append((-round(ln, 2), a))
    for _, a in sorted(edges)[:_EDGE_ANGLES_MAX]:
        out.add(a)
        out.add(round((a + 90.0) % 180.0, 1) % 180.0)
    return sorted(out)


@dataclass(frozen=True)
class Placement:
    length_free: float   # free length of the slab gap (>= the fitted length)
    angle_deg: float     # rectangle long axis, CCW from +x of the feet plane
    xc: float            # gap centre, in the rotated frame
    y0: float            # envelope bottom, in the rotated frame


def _slab_gaps(edges_sorted, lo_keys, s0, s1, tol):
    ivs = []
    for k in range(bisect_right(lo_keys, s1)):
        ax, ay, bx, by, lo, hi = edges_sorted[k]
        if hi < s0:
            continue
        if ay == by:
            xa, xb = (ax, bx) if ax <= bx else (bx, ax)
        else:
            t0 = min(1.0, max(0.0, (s0 - ay) / (by - ay)))
            t1 = min(1.0, max(0.0, (s1 - ay) / (by - ay)))
            xa, xb = ax + (bx - ax) * t0, ax + (bx - ax) * t1
            if xa > xb:
                xa, xb = xb, xa
        ivs.append((xa - tol, xb + tol))
    if not ivs:
        return []
    ivs.sort()
    gaps = []
    cur_hi = ivs[0][1]
    for a, b in ivs[1:]:
        if a > cur_hi:
            gaps.append((cur_hi, a))
        if b > cur_hi:
            cur_hi = b
    return gaps


def _probe_chords(edges_sorted, lines_y):
    out = []
    active = []
    k, n = 0, len(edges_sorted)
    for y in lines_y:
        while k < n and edges_sorted[k][4] <= y:
            active.append(edges_sorted[k])
            k += 1
        active = [e for e in active if e[5] >= y]
        xs = []
        for ax, ay, bx, by, _lo, _hi in active:
            if (ay > y) != (by > y):
                xs.append(ax + (y - ay) * (bx - ax) / (by - ay))
        xs.sort()
        best = 0.0
        for i in range(0, len(xs) - 1, 2):
            if xs[i + 1] - xs[i] > best:
                best = xs[i + 1] - xs[i]
        out.append(best)
    return out


def _bad_prefix(chords, target):
    out = [0]
    for ch in chords:
        out.append(out[-1] + (1 if ch < target else 0))
    return out


def _candidate_ys(vys, depth, tol, lo_y, hi_y):
    events = sorted({round(v, 6) for y in vys for v in (y + 2 * tol, y - depth - 2 * tol)
                     if lo_y <= v <= hi_y})
    out = set(events)
    span = hi_y - lo_y
    if span >= 0:
        out.update(round(lo_y + span * k / _Y_SAMPLES, 6) for k in range(_Y_SAMPLES + 1))
    return sorted(v for v in out if lo_y <= v <= hi_y)


def fit_lengths(rings, specs_, tol: float = TOL_FT):
    """Largest lattice length that fits, per envelope depth (root
    site_fit.fit_lengths): {depth: (length, Placement) | None}."""
    pts = [p for r in rings for p in r]
    result = {d: None for d, _ in specs_}
    if len(pts) < 3:
        return result
    todo = {d: list(lat) for d, lat in specs_ if lat}
    for ang in _angles(rings):
        if not todo:
            break
        rad = math.radians(ang)
        c, s = math.cos(rad), math.sin(rad)
        rr = [[(x * c + y * s, -x * s + y * c) for x, y in ring] for ring in rings]
        xs = [p[0] for r in rr for p in r]
        ys = [p[1] for r in rr for p in r]
        width, ymin, ymax = max(xs) - min(xs), min(ys), max(ys)
        edges = []
        for ring in rr:
            n = len(ring)
            for i in range(n):
                (ax, ay), (bx, by) = ring[i], ring[(i + 1) % n]
                edges.append((ax, ay, bx, by, min(ay, by), max(ay, by)))
        edges.sort(key=lambda e: e[4])
        lo_keys = [e[4] for e in edges]
        vys = sorted(set(ys))
        nlines = int((ymax - ymin) / _PROBE_FT)
        lines_y = [ymin + _PROBE_FT * (i + 0.5) for i in range(nlines)]
        chords = _probe_chords(edges, lines_y)
        for depth in sorted(todo):
            lattice = todo[depth]
            best = result[depth]
            have = best[0] if best else None
            nxt = [v for v in lattice if have is None or v > have]
            if not nxt:
                continue
            target = nxt[0]
            if width < target + 2 * tol or ymax - ymin < depth + 4 * tol:
                continue
            lo_y, hi_y = ymin + 2 * tol, ymax - depth - 2 * tol
            cands = _candidate_ys(vys, depth, tol, lo_y, hi_y)
            bad = _bad_prefix(chords, target)
            for y0 in cands:
                i0 = bisect_left(lines_y, y0)
                i1 = bisect_right(lines_y, y0 + depth)
                if bad[i1] - bad[i0]:
                    continue
                gaps = _slab_gaps(edges, lo_keys, y0 - tol, y0 + depth + tol, tol)
                gaps = sorted((g for g in gaps if g[1] - g[0] >= target),
                              key=lambda g: (-(g[1] - g[0]), g[0]))
                for g0, g1 in gaps:
                    if not _inside((g0 + g1) / 2.0, y0 + depth / 2.0, rr):
                        continue
                    free = g1 - g0
                    got = max(v for v in lattice if v <= free)
                    result[depth] = (got, Placement(free, ang, (g0 + g1) / 2.0, y0))
                    nxt = [v for v in lattice if v > got]
                    target = nxt[0] if nxt else None
                    if target is not None:
                        bad = _bad_prefix(chords, target)
                    break
                if target is None:
                    break
            if target is None:
                del todo[depth]
    return result


def normalise(parts_ft: list[Polygon]) -> tuple[list[list[tuple[float, float]]],
                                                tuple[float, float]]:
    """Root's ring normalisation (_rings_from_geojson + _fits_for): open rings
    rounded to 0.01 ft, parts sorted by (outer area desc, first vertex),
    shifted to a floor-of-min origin. Returns (rings, origin)."""
    parts = []
    for poly in parts_ft:
        rings = []
        for ring in (poly.exterior, *poly.interiors):
            pts = [(round(float(x), 2), round(float(y), 2)) for x, y, *_ in ring.coords]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(set(pts)) >= 3:
                rings.append(pts)
        if rings:
            parts.append(rings)
    parts.sort(key=lambda part: (-round(abs(_ring_area(part[0])), 2), min(part[0])))
    allx = [x for part in parts for r in part for x, _ in r]
    ally = [y for part in parts for r in part for _, y in r]
    origin = (float(math.floor(min(allx))), float(math.floor(min(ally)))) if allx else (0.0, 0.0)
    rings = [[(round(x - origin[0], 2), round(y - origin[1], 2)) for x, y in r]
             for part in parts for r in part]
    return rings, origin


# ---------------------------------------------------------------------------
# Screener side: geometry in, report fields out.
# ---------------------------------------------------------------------------
def polygon_parts(geom: BaseGeometry | None) -> list[Polygon]:
    """Non-empty Polygon parts of a Polygon / MultiPolygon / collection (holes
    kept; lines and points dropped)."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon) or hasattr(geom, "geoms"):
        return [q for g in geom.geoms for q in polygon_parts(g)]
    return []


def _vertices(parts: list[Polygon]) -> int:
    return sum(len(r.coords) - 1 for q in parts for r in (q.exterior, *q.interiors))


def _min_width(poly: Polygon) -> float:
    """Minimum width of the convex hull (exact, over its edges)."""
    hull = poly.convex_hull
    if not isinstance(hull, Polygon):
        return 0.0
    pts = list(hull.exterior.coords)[:-1]
    best = math.inf
    for i in range(len(pts)):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % len(pts)]
        ln = math.hypot(x2 - x1, y2 - y1)
        if ln == 0:
            continue
        far = max(abs((x2 - x1) * (y1 - y) - (x1 - x) * (y2 - y1)) / ln for x, y in pts)
        best = min(best, far)
    return 0.0 if best is math.inf else best


def can_hold_smallest(part_ft: Polygon) -> bool:
    """A part that can't hold the smallest envelope (400 x 445 ft) is pruned:
    too little area, or its convex hull is narrower than 400 ft somewhere."""
    length, depth = SMALLEST
    return part_ft.area >= length * depth and _min_width(part_ft) >= min(length, depth)


def _to_feet(g: BaseGeometry) -> BaseGeometry:
    return shapely.transform(g, lambda c: c * FT_PER_M)


def _to_wgs84(g_ft: BaseGeometry, proj: LocalProjection) -> BaseGeometry:
    def inv(c):
        out = c / FT_PER_M
        out[:, 0] = out[:, 0] / proj.kx + proj.lon0
        out[:, 1] = out[:, 1] / proj.ky + proj.lat0
        return out
    return shapely.transform(g_ft, inv)


def envelope_corners(length: float, depth: float, pl: Placement,
                     origin: tuple[float, float]) -> list[tuple[float, float]]:
    """The fitted envelope's corners in the feet plane (root building_corners
    with the whole envelope instead of the building)."""
    half = length / 2.0
    local = [(pl.xc - half, pl.y0), (pl.xc + half, pl.y0), (pl.xc + half, pl.y0 + depth),
             (pl.xc - half, pl.y0 + depth)]
    rad = math.radians(pl.angle_deg)
    c, s = math.cos(rad), math.sin(rad)
    ox, oy = origin
    return [(x * c - y * s + ox, x * s + y * c + oy) for x, y in local]


@dataclass
class Fit:
    state: str                              # FITS / NONE_FOUND / NOT_CHECKED
    largest_open_acres: float | None = None
    label: str | None = None                # "cross-dock" / "front/rear-load"
    length_ft: float | None = None
    width_ft: float | None = None           # building depth W
    building_sf: int | None = None
    reason: str = ""                        # why none found / not checked
    simplified: bool = False
    envelope: BaseGeometry | None = None    # WGS84, fits only
    map_mismatch: bool = False              # set by run.main for the brief

    def fields(self) -> dict[str, Any]:
        """The report fields (attached after the sort, never before)."""
        if self.state == FITS:
            text = f"{self.label} {self.length_ft:.0f} x {self.width_ft:.0f} ft"
        elif self.state == NONE_FOUND:
            text = NONE_TEXT
        else:
            text = NOT_CHECKED_PREFIX + self.reason
        out = {"largest_open_acres": self.largest_open_acres, "envelope_fit": text,
               "envelope_building_sf": self.building_sf,
               "_envelope_detail": self}
        if self.envelope is not None:
            out[ENVELOPE_KEY] = self.envelope
        return out


def not_checked(reason: str, largest: float | None = None) -> Fit:
    return Fit(NOT_CHECKED, largest_open_acres=largest, reason=reason)


def fit_parcel(geometry: BaseGeometry, open_ground: BaseGeometry | None,
               constrained_pct: float | None, setback_ft: float = DEFAULT_SETBACK_FT) -> Fit:
    """One parcel. ``open_ground`` is ``p["_open_ground"]``; when it IS the
    parcel geometry while constraints exist, map_checks fell back to the
    whole parcel because nothing was left: that reads as no open ground.
    Only GEOS faults are caught (a bug must fail the tests)."""
    try:
        return _fit(geometry, open_ground, constrained_pct, setback_ft)
    except GEOSException as exc:
        return not_checked(f"geometry fault ({str(exc)[:80]})")


def _fit(geometry, open_ground, constrained_pct, setback_ft) -> Fit:
    if open_ground is None:
        open_ground = geometry
    if open_ground is geometry and (constrained_pct or 0) > 0:
        return Fit(NONE_FOUND, largest_open_acres=0.0, reason="no open ground on the parcel map")
    proj = LocalProjection.around(geometry)
    open_m = polygon_parts(proj(open_ground))
    if not open_m:
        return Fit(NONE_FOUND, largest_open_acres=0.0, reason="no open ground on the parcel map")
    largest = round(max(acres(q) for q in open_m), 1)

    inner = proj(geometry).buffer(-setback_ft / FT_PER_M)
    region = shapely.intersection(MultiPolygon(open_m) if len(open_m) > 1 else open_m[0], inner)
    parts = [q for q in polygon_parts(_to_feet(region)) if can_hold_smallest(q)]
    simplified = False
    if _vertices(parts) > _SIMPLIFY_ABOVE_VERTICES:
        # Root's bound: simplify inside an extra 2 x tol inward buffer, so the
        # simplified shape stays inside the original.
        shrunk = shapely.union_all(parts).buffer(-2 * _SIMPLIFY_TOL_FT)
        parts = [q for q in polygon_parts(shrunk.simplify(_SIMPLIFY_TOL_FT,
                                                          preserve_topology=True))
                 if can_hold_smallest(q)]
        simplified = True
        n = _vertices(parts)
        if n > _MAX_FIT_VERTICES:
            return not_checked(f"open-ground shape too complex ({n} vertices > "
                               f"{_MAX_FIT_VERTICES})", largest)
    if not parts:
        return Fit(NONE_FOUND, largest_open_acres=largest, simplified=simplified,
                   reason="no open-ground part can hold the smallest envelope")
    rings, origin = normalise(parts)
    found = fit_lengths(rings, specs())
    for key, label in GROUPS:
        p = PROTOTYPES[key]
        depth = envelope_depth(p)
        got = found.get(depth)
        if got is None:
            continue
        length, pl = got
        env_ft = Polygon(envelope_corners(length, depth, pl, origin))
        return Fit(FITS, largest_open_acres=largest, label=label, length_ft=length,
                   width_ft=float(p["W"]), building_sf=int(round(length * float(p["W"]))),
                   simplified=simplified, envelope=_to_wgs84(env_ft, proj))
    return Fit(NONE_FOUND, largest_open_acres=largest, simplified=simplified,
               reason="no envelope placement found")


def brief_line(p: dict[str, Any]) -> str:
    """The brief's site-fit line (hand-written wording only)."""
    f: Fit | None = p.get("_envelope_detail")
    if f is None:
        return "- Building fit: not checked (fit off)"
    if f.state == NOT_CHECKED:
        return f"- Building fit: not checked ({f.reason})"
    open_part = f"largest open-ground part {f.largest_open_acres:.1f} acres"
    where = (f"on open ground inside a {DEFAULT_SETBACK_FT} ft lot-line setback; open ground "
             f"excludes {EXCLUDES}; {STEEP_NOTE}; {NOT_MODELLED}")
    if f.state == FITS:
        head = (f"- Building fit (lower bound): a {f.label} envelope (building + truck court + "
                f"trailer parking) fits with a {f.length_ft:,.0f} x {f.width_ft:,.0f} ft "
                f"building ({f.building_sf:,} SF); {open_part}")
        if f.label.startswith("cross"):
            head += "; a cross-dock fit means the front/rear-load envelope fits too"
    else:
        head = (f"- Building fit: no fit found for the smallest envelope (building + truck "
                f"court + trailer parking, {SMALLEST[0]:.0f} x {SMALLEST[1]:.0f} ft) "
                f"({f.reason}); {open_part}. The search is a lower bound, so this is not "
                f"proof that nothing fits")
    line = f"{head}. Checked {where}"
    if f.simplified:
        line += f"; the shape was simplified ({_SIMPLIFY_TOL_FT:g} ft) for the search"
    if f.map_mismatch:
        line += "; parcel map disagrees with the tax record, the fit uses the map shape"
    return line
