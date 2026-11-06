"""Site fit: project the largest operator-prototype building a parcel can hold.

Protected module (reviews/22_site_fit/): immutable during a run, like
zoning.py. It imports only the standard library, ``zoning`` and the
flood/wetland facts module (aliased ``_hz``, reviews/26_flood_wetlands); never
``research`` (the agent's sandbox), ``runner``, ``reporting``,
``zoning_import`` or anything under screener/.

What it owns
------------
1. ``building_prototypes.json`` (repo root, HUMAN-OWNED): the operator's
   building prototypes, copied from their SiteFit tool's defaults. Loaded
   ONCE per process at import, validated, deep-frozen and fingerprinted by
   SHA-256. ``verify_prototypes_unchanged()`` re-hashes the file at cycle
   entry; ``verify_prototypes_committed()`` (called by runner.evaluate)
   compares it with git HEAD. A missing or invalid file never raises at
   import: every projection is then ``unavailable``.

2. The projection. ``project_parcel(conn, parcel_id)`` takes NO tunables:
   the module reads the parcel's geometry and stored zoning itself, picks
   the setback and coverage cap from zoning_rules.json (through
   ``zoning.district_for_stored``), buffers the parcel INWARD in PostGIS
   (SRID 2240, NAD83 / Georgia West, US survey feet) and fits each
   prototype's rectangular envelope in pure Python. The result is a frozen
   ``Projection``; ``meets_bts_footprint`` is the only BTS test.

Fit rule (operator decision, reviews/22_site_fit/03_decision_note.md)
---------------------------------------------------------------------
The full envelope (building L x W plus, per dock side, truck court and
trailer parking) must lie inside the setback-buffered area. Each prototype
keeps its depth and shrinks its length in ``step_ft`` steps from L to
``min_length_ft``, at the best orientation. Stricter than SiteFit's own
check (which tests only the building against the setback line); truck
drives and circulation are not modelled.

Setbacks: parcel frontage is unknown, so the uniform setback is the
largest non-null front/side/rear value of the parcel's district, applied on
every side; 25 ft (the file's ``default_setback_ft``) when no district rule
applies. A default setback is never a BTS input, even in a verified
jurisdiction. Adjacency buffers (residential or other-district neighbours)
are NOT applied: the database stores only candidate parcels, so neighbour
zoning is incomplete; each buffer the district's rules list is named in the
projection. Stormwater detention is NOT deducted; every projection says so.
Flood (H4) and wetlands (H6) are not deducted either until a human enables
the proposed hazard table (reviews/26_flood_wetlands): from then on the
FRESH SFHA (floodway included) and NWI wetland geometry of the parcel is
differenced from the setback buffer in PostGIS, the 0.2% zone is not, and
each projection states what was deducted plus "stream/state-waters buffers
not deducted". BTS still moves only through ``meets_bts_footprint`` for
verified zoning. Coverage is building SF over the parcel's gross geography
area.

The fitted length is a lower bound: the slab sweep tries every vertex event
plus a fixed grid of positions, each checked exactly, so a
reported fit always holds, but an optimum between those positions can be
missed by up to one grid step. Buffered rings above
``_SIMPLIFY_ABOVE_VERTICES`` are simplified in PostGIS inside a deeper
buffer (conservative); above ``_MAX_FIT_VERTICES`` the projection is an
error ("too complex") instead of an unbounded search.

Units: SRID 2240 is in US survey feet; treating them as international feet
is off by 2 ppm (0.002 ft on a 1,000 ft wall). Web Mercator is never used
(its scale at 33.6 N is 1.20).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import types
import weakref
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import zoning
# reviews/26_flood_wetlands (FW-14, FW-26): the flood/wetland storage gate and
# texts. Deliberate change to this module's import allowlist; no FEMA/NWI
# host is ever named or contacted here.
import hazards as _hz

PROTOTYPES_PATH = Path(__file__).resolve().parent / "building_prototypes.json"

SOURCE_TEXT = "operator SiteFit tool defaults"
PROTOTYPE_KEYS = ("cross", "front", "rear")
# program.md BTS STRONG: "parcel accommodates >= 150K SF footprint".
BTS_MIN_FOOTPRINT_SF = 150_000
# SiteFit's office corner pod is min(60, L/4, W/3) <= 60 ft on the front
# wall. With endClr >= 60 the pods never split a dock run, so the dock
# formula below is exactly SiteFit's (reviews/22_site_fit, SF-07).
MAX_OFFICE_POD_FT = 60.0
SRID_FEET = 2240
SQFT_PER_M2 = 1.0 / (0.3048 * 0.3048)
TOL_FT = 0.01
_ANGLE_GRID_DEG = 2.0
_EDGE_ANGLES_MAX = 32
_EDGE_ANGLE_MIN_LEN_FT = 30.0
_FIT_CACHE_MAX = 4096
# Extra slab positions per angle and depth (reviews/22_site_fit, finding 4):
# a fixed grid over the feasible y-range, added to the vertex events.
_Y_SAMPLES = 64
# Complexity bounds (finding 5): rings with more vertices than this are
# simplified in PostGIS (tolerance _SIMPLIFY_TOL_FT inside an extra
# 2 x tolerance inward buffer, so the result stays inside the setback line);
# a ring still above _MAX_FIT_VERTICES is not searched.
_SIMPLIFY_ABOVE_VERTICES = 400
_SIMPLIFY_TOL_FT = 1.0
_MAX_FIT_VERTICES = 600

FLOOD_WETLANDS = "flood/wetlands not deducted (H4/H6 not wired)"
RES_BUFFERS = ("adjacency buffers not applied (residential or other-district neighbours; "
               "neighbour zoning in the DB is incomplete: only candidate parcels are stored)")
DETENTION = "stormwater detention not deducted (no detention pond placed)"
CAR_PARKING = "car parking not modelled"
FIT_RULE = ("full envelope (building + truck courts + trailer parking) inside the setback "
            "line, stricter than SiteFit, which lets courts and trailer parking sit in the "
            "setback; truck drive and circulation not modelled")
SIMPLIFIED = (f"buffered ring simplified for fitting ({_SIMPLIFY_TOL_FT:g} ft, inside an extra "
              f"{2 * _SIMPLIFY_TOL_FT:g} ft setback)")
# Every projection carries these, whatever its status (tests pin it).
NOT_MODELLED = (RES_BUFFERS, FLOOD_WETLANDS, DETENTION, CAR_PARKING)
# reviews/26_flood_wetlands: once the hazard table is enabled, FLOOD_WETLANDS
# is replaced on each projection by what was actually deducted, and the
# stream/state-waters buffer note is added (FW-14, FW-23). A projection
# that never read hazard geometry says so.
FLOOD_NOT_READ = "flood/wetlands not deducted (no buildable geometry read)"
FLOOD_UNREADABLE = "flood/wetlands not deducted (hazard data unreadable)"

OK = "ok"
EMPTY = "empty_after_setbacks"
NO_GEOMETRY = "no_geometry"
ERROR = "error"
UNAVAILABLE = "unavailable"
STATUSES = (OK, EMPTY, NO_GEOMETRY, ERROR, UNAVAILABLE)


class PrototypesError(Exception):
    """building_prototypes.json is missing, not JSON, or violates the schema."""


class PrototypesChanged(Exception):
    """building_prototypes.json changed since this process loaded it."""


# ---------------------------------------------------------------------------
# Loading, validation, freeze (the zoning.py pattern)
# ---------------------------------------------------------------------------
def _deep_freeze(obj: Any) -> Any:
    if isinstance(obj, dict):
        return types.MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


def _pos(v: Any) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and v > 0)


def validate_prototypes(data: Any) -> list[str]:
    """Schema problems of a parsed prototypes file (empty list = valid)."""
    if not isinstance(data, dict):
        return ["top level is not an object"]
    errs: list[str] = []
    if data.get("source") != SOURCE_TEXT:
        errs.append(f"source must be {SOURCE_TEXT!r}")
    try:
        date.fromisoformat(str(data.get("as_of")))
    except ValueError:
        errs.append("as_of must be an ISO date")
    step = data.get("step_ft")
    if not (_pos(step) and float(step).is_integer()):
        errs.append("step_ft must be a positive whole number")
        step = None
    if not _pos(data.get("default_setback_ft")):
        errs.append("default_setback_ft must be a positive number")
    protos = data.get("prototypes")
    if not isinstance(protos, dict) or sorted(protos) != sorted(PROTOTYPE_KEYS):
        return errs + [f"prototypes must hold exactly {PROTOTYPE_KEYS}"]
    for key in PROTOTYPE_KEYS:
        p = protos[key]
        where = f"prototype {key!r}"
        if not isinstance(p, dict):
            errs.append(f"{where}: not an object")
            continue
        if p.get("type") != key:
            errs.append(f"{where}: type must be {key!r}")
        if not isinstance(p.get("label"), str) or not p["label"].strip():
            errs.append(f"{where}: label missing")
        for f in ("L", "W", "clear", "court", "trailer", "stall", "doorSp", "endClr",
                  "min_length_ft"):
            if not _pos(p.get(f)):
                errs.append(f"{where}: {f} must be a positive number")
        if not isinstance(p.get("trailerOn"), bool):
            errs.append(f"{where}: trailerOn must be true or false")
        if _pos(p.get("endClr")) and p["endClr"] < MAX_OFFICE_POD_FT:
            errs.append(f"{where}: endClr must be >= {MAX_OFFICE_POD_FT:g} (office pod)")
        if _pos(p.get("L")) and _pos(p.get("min_length_ft")):
            if p["min_length_ft"] > p["L"]:
                errs.append(f"{where}: min_length_ft is above L")
            elif step and (p["L"] - p["min_length_ft"]) % step != 0:
                errs.append(f"{where}: L - min_length_ft must be a multiple of step_ft")
    for sect in ("parking", "detention", "road"):
        if not isinstance(data.get(sect), dict):
            errs.append(f"{sect} must be an object")
    return errs


@dataclass(frozen=True)
class _State:
    data: Mapping[str, Any] | None
    sha256: str | None
    problem: str | None


def _file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def parse_prototypes_bytes(raw: bytes) -> Mapping[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PrototypesError("building_prototypes.json is not valid UTF-8 JSON") from None
    errs = validate_prototypes(data)
    if errs:
        raise PrototypesError(
            f"building_prototypes.json schema: {errs[0]} ({len(errs)} problem(s))")
    return _deep_freeze(data)


def _load_state(path: Path) -> _State:
    """Read the file once. Never raises: a problem makes projections unavailable."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _State(None, None, "building_prototypes.json is missing")
    except OSError as exc:
        return _State(None, None, f"building_prototypes.json unreadable ({type(exc).__name__})")
    sha = hashlib.sha256(raw).hexdigest()
    try:
        return _State(parse_prototypes_bytes(raw), sha, None)
    except PrototypesError as exc:
        return _State(None, sha, str(exc))


_STATE: _State = _load_state(PROTOTYPES_PATH)


def load_prototypes() -> Mapping[str, Any]:
    if _STATE.data is None:
        raise PrototypesError(_STATE.problem or "prototypes unavailable")
    return _STATE.data


def prototypes_problem() -> str | None:
    return _STATE.problem


def prototypes_sha256() -> str | None:
    return _STATE.sha256


def verify_prototypes_unchanged(path: Path | None = None) -> None:
    """Raise PrototypesChanged if the file's bytes differ from import time
    (including appearing or disappearing). Call at cycle entry."""
    if _file_sha(path or PROTOTYPES_PATH) != _STATE.sha256:
        raise PrototypesChanged(
            "building_prototypes.json changed since this process loaded it. Edits to the "
            "building prototypes are between-runs only: stop the loop, start a new run and "
            "baseline (reviews/22_site_fit/02_risk_review.md, SF-04)."
        )


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                             text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def verify_prototypes_committed(path: Path | None = None) -> None:
    """Raise PrototypesChanged unless the file on disk is the committed one
    (git blob id == HEAD:building_prototypes.json). runner.evaluate calls it
    before every experiment, next to zoning.verify_rules_committed()."""
    p = (path or PROTOTYPES_PATH).resolve()
    repo = p.parent
    top = _git(["rev-parse", "--show-toplevel"], repo)
    if top is None:
        raise PrototypesChanged(
            "cannot confirm building_prototypes.json against git HEAD (git unavailable or "
            "not a repository); the runner needs the committed prototypes file")
    rel = p.relative_to(Path(top).resolve()).as_posix()
    committed = _git(["rev-parse", "--verify", "--quiet", f"HEAD:{rel}"], repo)
    on_disk = _git(["hash-object", "--", str(p)], repo) if p.exists() else None
    if committed is None and on_disk is None:
        return
    if committed != on_disk:
        raise PrototypesChanged(
            "building_prototypes.json differs from the committed version (git HEAD). Edits "
            "are between-runs only: commit them, then start a new run and baseline; or "
            "restore the file (git checkout -- building_prototypes.json)."
        )


# ---------------------------------------------------------------------------
# SiteFit formulas (ported literally; line numbers in reviews/22_site_fit)
# ---------------------------------------------------------------------------
def _sides(p: Mapping[str, Any]) -> int:
    return 2 if p["type"] == "cross" else 1


def dock_doors(p: Mapping[str, Any], length: float) -> int:
    """geomBuilding: e = min(endClr, L/2); sp = max(doorSp, 8); per dock
    side n = floor((L - 2e) / sp) + 1 when the run is >= 0, else 0."""
    e = min(max(0.0, float(p["endClr"])), length / 2.0)
    sp = max(float(p["doorSp"]), 8.0)
    span = length - 2.0 * e
    n = math.floor(span / sp) + 1 if span >= 0 else 0
    return max(0, n) * _sides(p)


def trailer_stalls(p: Mapping[str, Any], length: float) -> int:
    """geomBuilding: nT = floor(L / max(stall, 8)) per dock side (no end
    clearance), 0 when trailer parking is off."""
    t = float(p["trailer"]) if p["trailerOn"] else 0.0
    if t <= 0:
        return 0
    return math.floor(length / max(float(p["stall"]), 8.0)) * _sides(p)


def envelope_depth(p: Mapping[str, Any]) -> float:
    """geomBuilding stats.depthTotal = W + sides * (court + trailer)."""
    t = float(p["trailer"]) if p["trailerOn"] else 0.0
    return float(p["W"]) + _sides(p) * (max(0.0, float(p["court"])) + t)


def park_rows(depth: float, stall_depth: float, aisle: float) -> int:
    """SiteFit parkRows: number of stall rows in a lot of the given depth."""
    rows, y = 0, 0.0
    if stall_depth <= 0:
        return 0
    while depth - y >= 2 * stall_depth + aisle - 1e-6:
        rows += 2
        y += 2 * stall_depth + aisle
    if depth - y >= stall_depth + aisle - 1e-6:
        rows += 1
    return rows


def lot_stalls(width: float, depth: float, stall_w: float, stall_d: float,
               aisle: float) -> int:
    """SiteFit geomParking (orient 'W'): rows * floor(W / stall width)."""
    return park_rows(depth, stall_d, aisle) * math.floor(width / max(stall_w, 1.0))


def detention_volume_cf(top_l: float, top_w: float, depth: float, slope: float) -> float | None:
    """SiteFit detVolume (prismoidal): cubic feet, None when slopes meet."""
    bl, bw = top_l - 2 * slope * depth, top_w - 2 * slope * depth
    if bl <= 0 or bw <= 0:
        return None
    a1, a2 = top_l * top_w, bl * bw
    return depth / 3.0 * (a1 + a2 + math.sqrt(a1 * a2))


# ---------------------------------------------------------------------------
# Rectangle fitting (pure Python, deterministic)
# ---------------------------------------------------------------------------
Ring = list  # list[tuple[float, float]], open (no closing vertex)


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
class _Placement:
    length_free: float   # free length of the slab gap (>= the fitted length)
    angle_deg: float     # rectangle long axis, CCW from +x of the feet plane
    xc: float            # gap centre, in the rotated frame
    y0: float            # envelope bottom, in the rotated frame


def _slab_gaps(edges_sorted, lo_keys, s0, s1, tol):
    """Free x-gaps of the horizontal slab [s0, s1]: the complement of every
    boundary edge's x-extent within the slab, each widened by tol."""
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


_PROBE_FT = 25.0


def _probe_chords(edges_sorted, lines_y):
    """Longest inside chord (even-odd) of each horizontal probe line, by a
    sweep over the edges sorted by their lower y. Exact per line; used only
    as a NECESSARY condition (every line through a fitted rectangle carries
    an inside chord at least as long as the rectangle)."""
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
    """Prefix counts of probe lines whose longest chord is shorter than target."""
    out = [0]
    for ch in chords:
        out.append(out[-1] + (1 if ch < target else 0))
    return out


def _candidate_ys(vys: Sequence[float], depth: float, tol: float, lo_y: float,
                  hi_y: float) -> list[float]:
    """Slab bottoms to try: every vertex event (a vertex on the slab's top or
    bottom edge) and a fixed grid of ``_Y_SAMPLES`` steps over
    [lo_y, hi_y] (always a superset of the old event-only candidates, so no
    fit gets shorter). Interior optima of V-sided
    shapes fall between events (finding 4); the extra positions find them to
    within one step. Each position is still checked exactly."""
    events = sorted({round(v, 6) for y in vys for v in (y + 2 * tol, y - depth - 2 * tol)
                     if lo_y <= v <= hi_y})
    out = set(events)
    span = hi_y - lo_y
    if span >= 0:
        out.update(round(lo_y + span * k / _Y_SAMPLES, 6) for k in range(_Y_SAMPLES + 1))
    return sorted(v for v in out if lo_y <= v <= hi_y)


def fit_lengths(rings: Sequence[Sequence[tuple[float, float]]],
                specs: Sequence[tuple[float, Sequence[float]]],
                tol: float = TOL_FT) -> dict[float, tuple[float, _Placement] | None]:
    """Largest lattice length that fits, per envelope depth.

    ``specs`` is [(depth, lattice ascending)]. A placement is accepted only
    when the rectangle, widened by ``tol`` on every side, has no boundary
    edge of ANY ring inside it and its centre is inside the region (even-odd,
    so holes and multipart parcels are respected): exact containment, on the
    conservative side. Candidate positions are ``_candidate_ys`` (vertex
    events and a fixed grid) at every angle of ``_angles``,
    so the result is a lower bound; first placement found wins ties (angle
    ascending, then y ascending), so results are deterministic.
    """
    pts = [p for r in rings for p in r]
    result: dict[float, tuple[float, _Placement] | None] = {d: None for d, _ in specs}
    if len(pts) < 3:
        return result
    todo = {d: list(lat) for d, lat in specs if lat}
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
                    result[depth] = (got, _Placement(free, ang, (g0 + g1) / 2.0, y0))
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


# ---------------------------------------------------------------------------
# Rules -> setback, coverage cap, notes
# ---------------------------------------------------------------------------
def _value(dims: Mapping[str, Any] | None, key: str) -> Any:
    if dims is None:
        return None
    v = dims.get(key)
    return v.get("value") if isinstance(v, Mapping) else v


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


_SAFE_RE = re.compile(r"[^A-Za-z0-9 &/().,+'#:%_-]")


def _clean(text: Any, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", _SAFE_RE.sub(" ", str(text))).strip()[:limit]


def _fmt_ft(v: float) -> str:
    return f"{v:g}"


@dataclass(frozen=True)
class _Rules:
    setback_ft: float
    setback_source: str
    cap_pct: float | None
    cap_text: str
    verified: bool
    label: str
    notes: tuple[str, ...]
    from_rules: bool = False   # the setback is a district value, not the default


VERIFIED_LABEL = "verified rules"
DRAFT_LABEL = "draft rules"
VERIFIED_DEFAULT_LABEL = "verified jurisdiction, default setback"
NO_RULE_LABEL = "no district rule"
RULES_UNAVAILABLE_LABEL = "zoning rules unavailable"


def _height_note(data: Mapping[str, Any], height: Any) -> str:
    """Height is never checked; the note is on every projection (finding 13)."""
    clears = sorted({_fmt_ft(float(data["prototypes"][k]["clear"])) for k in PROTOTYPE_KEYS},
                    key=float, reverse=True)
    limit = (f"district max {_clean(height, 50)}" if height is not None
             else "no district maximum in the rules")
    return f"height not checked (clear {'/'.join(clears)} ft; {limit})"


def _buffer_notes(dims: Mapping[str, Any], where: str, setback: float) -> list[str]:
    """Name every buffer the district's rules list (finding 2): none is
    applied, because neighbour zoning is incomplete in the database."""
    raw = _value(dims, "residential_adjacency_buffer_ft")
    if raw is None:
        return []
    items = raw.items() if isinstance(raw, Mapping) else [("buffer", raw)]
    parts = []
    for k, v in sorted(items, key=lambda kv: str(kv[0])):
        n = _num(v)
        if n is None:
            parts.append(f"{_clean(k, 40)} {_clean(v, 40)}")
        elif n <= setback:
            parts.append(f"{_clean(k, 40)} {_fmt_ft(n)} ft (within the {_fmt_ft(setback)} ft "
                         f"setback)")
        else:
            parts.append(f"{_clean(k, 40)} {_fmt_ft(n)} ft")
    return [f"buffers listed for {where}, not applied: " + ", ".join(parts)]


def _rules_for(stored: tuple[Any, Any] | None, parcel_id: str,
               data: Mapping[str, Any]) -> _Rules:
    """SF-09: uniform setback = max(non-null front/side/rear) on all sides;
    the 25 ft default when no district rule applies. Buffer keys are never
    folded in (SF-10), but each is named. A default setback is never a
    verified value (finding 6): ``from_rules`` is False and the label says so."""
    default = float(data["default_setback_ft"])
    dist = zoning.district_for_stored(stored[0], stored[1], parcel_id) if stored else None
    notes: list[str] = []
    if dist is None:
        problem = zoning.rules_problem()
        if problem is not None:
            return _Rules(default, f"default (zoning rules unavailable: {_clean(problem, 80)})",
                          None, "no lot coverage cap (zoning rules unavailable)", False,
                          RULES_UNAVAILABLE_LABEL, (_height_note(data, None),))
        return _Rules(default, "default (no district rule)", None,
                      "no lot coverage cap (no district rule)", False, NO_RULE_LABEL,
                      (_height_note(data, None),))
    where = f"{dist.table_jurisdiction} {_clean(dist.district, 40)}"
    dims = dist.dimensions
    if dims is None:
        label = VERIFIED_DEFAULT_LABEL if dist.verified else DRAFT_LABEL
        return _Rules(default, f"default (district {where} not listed in the rules)", None,
                      "no lot coverage cap in the rules", dist.verified, label,
                      (_height_note(data, None),))
    vals = []
    for key in ("front_setback_ft", "side_setback_ft", "rear_setback_ft"):
        raw = _value(dims, key)
        if raw is None:
            continue
        v = _num(raw)
        if v is None or v < 0:
            notes.append(f"{key} of {where} is not a number: not applied")
        else:
            vals.append(v)
    if vals:
        setback, source, from_rules = max(vals), f"max(front/side/rear), {where}", True
    else:
        setback = default
        source = f"default ({where} has no numeric front/side/rear setback)"
        from_rules = False
    if dist.verified:
        label = VERIFIED_LABEL if from_rules else VERIFIED_DEFAULT_LABEL
    else:
        label = DRAFT_LABEL
    caps = []
    cov = _num(_value(dims, "max_lot_coverage_pct"))
    if cov is not None and 0 < cov <= 100:
        caps.append((cov, f"max lot coverage {_fmt_ft(cov)}% of gross parcel area, {where}"))
    elif _value(dims, "max_lot_coverage_pct") is not None:
        notes.append(f"max_lot_coverage_pct of {where} is not a usable number: not applied")
    far = _num(_value(dims, "max_far"))
    # A FAR of 1.0 or more never limits a one-story footprint, so it is no cap
    # (finding 12: Atlanta's FAR 2.0 is not a "200%" coverage cap).
    if far is not None and 0 < far < 1.0:
        caps.append((far * 100.0, f"max FAR {_fmt_ft(far)} (one-story footprint), {where}"))
    elif far is not None and far >= 1.0:
        notes.append(f"max FAR {_fmt_ft(far)} of {where} does not limit a one-story footprint")
    if caps:
        cap_pct, cap_text = min(caps)
    else:
        cap_pct, cap_text = None, "no lot coverage cap in the rules"
    notes.extend(_buffer_notes(dims, where, setback))
    notes.append(_height_note(data, _value(dims, "max_height_ft")))
    return _Rules(setback, source, cap_pct, cap_text, dist.verified, label, tuple(notes),
                  from_rules)


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PrototypeFit:
    key: str
    label: str
    fits: bool
    full_size: bool
    length_ft: float | None
    width_ft: float
    depth_ft: float
    building_sf: int | None
    docks: int | None
    trailer_stalls: int | None
    coverage_pct: float | None
    binding: str
    angle_deg: float | None
    _xc: float | None = None
    _y0: float | None = None


@dataclass(frozen=True)
class Projection:
    parcel_id: str
    status: str
    reason: str
    rules_label: str
    rules_verified: bool
    setback_ft: float | None
    setback_source: str
    gross_sf: int | None
    buildable_sf: int | None
    coverage_cap_pct: float | None
    fits: tuple[PrototypeFit, ...]
    constraints: tuple[str, ...]
    multipart: bool = False
    setback_from_rules: bool = False
    flood_wetlands: str = FLOOD_WETLANDS
    _origin: tuple[float, float] | None = None
    _geometry: str | None = field(default=None, repr=False, compare=False)


# Projections this module built (finding 3). meets_bts_footprint accepts
# only these, so a Projection constructed anywhere else (research.py, a
# copy, dataclasses.replace) can never pass the BTS test. Keyed by id() with
# weak values: an entry disappears with its object, so a reused id never
# matches a different object.
_ISSUED: "weakref.WeakValueDictionary[int, Projection]" = weakref.WeakValueDictionary()


def _issue(p: Projection) -> Projection:
    _ISSUED[id(p)] = p
    return p


def _flood_text(flood: str | None) -> str:
    """The flood/wetland constraint: FLOOD_WETLANDS while the hazard table
    is not enabled (byte-identical to before reviews/26_flood_wetlands)."""
    if not _hz.enabled():
        return FLOOD_WETLANDS
    return flood or FLOOD_NOT_READ


def _base_constraints(rules: _Rules | None, flood: str | None = None) -> list[str]:
    out = []
    if rules is not None:
        out.append(f"setback {_fmt_ft(rules.setback_ft)} ft all sides = {rules.setback_source}, "
                   f"{rules.label}")
        out.append(FIT_RULE)
        out.append(rules.cap_text)
        out.extend(rules.notes)
    if not _hz.enabled():
        out.extend(NOT_MODELLED)
        return out
    out.extend(_flood_text(flood) if c == FLOOD_WETLANDS else c for c in NOT_MODELLED)
    out.append(_hz.STREAM_BUFFERS)
    return out


def flood_wetlands_text(p: Any) -> str:
    """What a projection deducted for flood and wetlands (memo, CLI)."""
    if isinstance(p, Projection):
        return p.flood_wetlands
    return _flood_text(None)


def _empty(pid: str, status: str, reason: str, rules: _Rules | None,
           gross: int | None = None, extra: Sequence[str] = (),
           flood: str | None = None) -> Projection:
    return _issue(Projection(
        parcel_id=pid, status=status, reason=reason,
        rules_label=rules.label if rules else NO_RULE_LABEL,
        rules_verified=rules.verified if rules else False,
        setback_ft=rules.setback_ft if rules else None,
        setback_source=rules.setback_source if rules else "",
        gross_sf=gross, buildable_sf=0 if status == EMPTY else None,
        coverage_cap_pct=rules.cap_pct if rules else None, fits=(),
        constraints=tuple(_base_constraints(rules, flood)) + tuple(extra),
        setback_from_rules=rules.from_rules if rules else False,
        flood_wetlands=_flood_text(flood)))


def max_building_sf(p: Any) -> int | None:
    """Largest fitted building SF of a projection, None when nothing fits."""
    if not isinstance(p, Projection) or p.status != OK:
        return None
    sfs = [f.building_sf for f in p.fits if f.fits and f.building_sf is not None]
    return max(sfs) if sfs else None


def meets_bts_footprint(p: Any) -> bool:
    """program.md BTS: the parcel accommodates a >= 150K SF footprint. True
    ONLY for a projection this module built (``_ISSUED``), with status ok,
    rules verified, the setback taken from the district's rules (never the
    default, finding 6), and a fitted prototype of at least
    BTS_MIN_FOOTPRINT_SF."""
    if type(p) is not Projection or _ISSUED.get(id(p)) is not p:
        return False
    if not p.rules_verified or not p.setback_from_rules:
        return False
    best = max_building_sf(p)
    return best is not None and best >= BTS_MIN_FOOTPRINT_SF


def data_gap_text(p: Any) -> str | None:
    """Stable data_gap text when a projection could not be computed (no
    geometry, error, prototypes unavailable); None otherwise."""
    if type(p) is not Projection:
        return "site fit projection unavailable: BTS rated N/A"
    if p.status in (NO_GEOMETRY, ERROR, UNAVAILABLE):
        return f"site fit projection {p.status} ({_clean(p.reason, 120)}): BTS rated N/A"
    return None


# ---------------------------------------------------------------------------
# Geometry from PostGIS
# ---------------------------------------------------------------------------
_SQL_ZONING_FOR_FIT = (
    "SELECT parcel_id, zoning, zoning_description FROM parcels WHERE parcel_id = ANY(%s) "
    "ORDER BY parcel_id"
)
# Inward setback buffer in PostGIS (Z-10: spatial work belongs in PostGIS):
# made valid like S2, transformed to SRID 2240 (US survey feet), buffered by
# -setback, returned as GeoJSON rounded to 0.01 ft. Gross area is geography
# (square metres) of the same made-valid geometry; the buildable area is the
# unsimplified buffer. A ring with more than _SIMPLIFY_ABOVE_VERTICES points
# (finding 5) is fitted on ST_SimplifyPreserveTopology(tolerance t) of the
# parcel buffered by -(setback + 2t): every point of that simplification is
# within 2t of the deeper buffer, so it stays inside the setback line.
# Parameters: (setback, vertex limit, setback, t, t, vertex limit, ids).
_SQL_BUFFERED_FOR_FIT = (
    "SELECT parcel_id, gross_m2, ST_IsEmpty(fit_g), ST_Area(inner_g), "
    "ST_AsGeoJSON(fit_g, 2), simplified "
    "FROM (SELECT parcel_id, gross_m2, inner_g, "
    "CASE WHEN ST_NPoints(inner_g) > %s THEN ST_SimplifyPreserveTopology("
    "ST_Buffer(t, -1.0 * (%s::float8 + 2.0 * %s::float8)), %s::float8) ELSE inner_g END AS fit_g, "
    "(ST_NPoints(inner_g) > %s) AS simplified "
    "FROM (SELECT parcel_id, t, ST_Area(geography(g)) AS gross_m2, "
    "ST_Buffer(t, -1.0 * %s::float8) AS inner_g "
    "FROM (SELECT parcel_id, g, ST_Transform(g, 2240) AS t "
    "FROM (SELECT parcel_id, ST_CollectionExtract(ST_MakeValid(geometry), 3) AS g "
    "FROM parcels WHERE parcel_id = ANY(%s) AND geometry IS NOT NULL) v "
    "WHERE NOT ST_IsEmpty(g)) w) b) c"
)


def _buffered_params(setback: float, ids: Sequence[str]) -> tuple:
    return (_SIMPLIFY_ABOVE_VERTICES, setback, _SIMPLIFY_TOL_FT, _SIMPLIFY_TOL_FT,
            _SIMPLIFY_ABOVE_VERTICES, setback, list(ids))


# reviews/26_flood_wetlands (FW-14): the same buffer with the parcel's FRESH
# hazard geometry differenced out, read here (hook semantics: never passed in
# by a caller), and only while the hazard table is enabled. SFHA (floodway
# included) and NWI wetlands are deducted; the 0.2% zone is not. A source is
# fresh when its row's stored md5(ST_AsBinary(geometry)) equals the parcel's
# current one; a stale or absent source deducts nothing and says so. The
# hazard geometry is first expanded outward (buffer +2t, then simplified by t,
# so it still covers the original: conservative), and the simplified branch
# differences it buffered by another 2t, so a fitted envelope never enters a
# deducted area. With no fresh hazard geometry the buffer is computed exactly
# as _SQL_BUFFERED_FOR_FIT computes it, so the ring text (the fit cache key)
# is unchanged; any deduction changes the ring text and so the cache key.
# Parameters: (vertex limit, setback, t, t, t, vertex limit, setback, t, t, ids).
_SQL_BUFFERED_FOR_FIT_HAZARDS = (
    "SELECT parcel_id, gross_m2, ST_IsEmpty(fit_g), ST_Area(inner_g), "
    "ST_AsGeoJSON(fit_g, 2), simplified, flood_state, wet_state, hz_sf "
    "FROM (SELECT parcel_id, gross_m2, inner_g, flood_state, wet_state, hz_sf, "
    "CASE WHEN ST_NPoints(inner_g) > %s THEN ST_SimplifyPreserveTopology("
    "CASE WHEN hz IS NULL THEN ST_Buffer(t, -1.0 * (%s::float8 + 2.0 * %s::float8)) "
    "ELSE ST_CollectionExtract(ST_Difference(ST_Buffer(t, -1.0 * (%s::float8 + 2.0 * "
    "%s::float8)), ST_Buffer(hz, 2.0 * %s::float8, 'quad_segs=2')), 3) END, %s::float8) "
    "ELSE inner_g END AS fit_g, "
    "(ST_NPoints(inner_g) > %s) AS simplified "
    "FROM (SELECT parcel_id, t, gross_m2, flood_state, wet_state, hz, "
    "COALESCE(ST_Area(hz_raw), 0.0) AS hz_sf, "
    "CASE WHEN hz IS NULL THEN ST_Buffer(t, -1.0 * %s::float8) "
    "ELSE ST_CollectionExtract(ST_Difference(ST_Buffer(t, -1.0 * %s::float8), hz), 3) "
    "END AS inner_g "
    "FROM (SELECT parcel_id, t, gross_m2, flood_state, wet_state, hz_raw, "
    "CASE WHEN hz_raw IS NULL OR ST_IsEmpty(hz_raw) THEN NULL "
    "ELSE ST_SimplifyPreserveTopology(ST_Buffer(hz_raw, 2.0 * %s::float8, 'quad_segs=2'), "
    "%s::float8) END AS hz "
    "FROM (SELECT parcel_id, t, ST_Area(geography(g)) AS gross_m2, flood_state, wet_state, "
    "CASE WHEN flood_state = 'fresh' AND wet_state = 'fresh' "
    "THEN ST_Union(COALESCE(sg, wg), COALESCE(wg, sg)) "
    "WHEN flood_state = 'fresh' THEN sg WHEN wet_state = 'fresh' THEN wg END AS hz_raw "
    "FROM (SELECT v.parcel_id, v.g, ST_Transform(v.g, 2240) AS t, "
    "CASE WHEN h.parcel_id IS NULL OR h.nfhl_fetched_at IS NULL "
    "OR h.nfhl_parcel_geom_md5 IS NULL THEN 'absent' "
    "WHEN h.nfhl_parcel_geom_md5 = v.gmd5 THEN 'fresh' ELSE 'stale' END AS flood_state, "
    "CASE WHEN h.parcel_id IS NULL OR h.nwi_fetched_at IS NULL "
    "OR h.nwi_parcel_geom_md5 IS NULL THEN 'absent' "
    "WHEN h.nwi_parcel_geom_md5 = v.gmd5 THEN 'fresh' ELSE 'stale' END AS wet_state, "
    "h.sfha_geom AS sg, h.wetland_geom AS wg "
    "FROM (SELECT parcel_id, ST_CollectionExtract(ST_MakeValid(geometry), 3) AS g, "
    "md5(ST_AsBinary(geometry)) AS gmd5 "
    "FROM parcels WHERE parcel_id = ANY(%s) AND geometry IS NOT NULL) v "
    "LEFT JOIN parcel_hazards h ON h.parcel_id = v.parcel_id "
    "WHERE NOT ST_IsEmpty(v.g)) w) x) y) b) c"
)


def _buffered_params_hazards(setback: float, ids: Sequence[str]) -> tuple:
    t = _SIMPLIFY_TOL_FT
    return (_SIMPLIFY_ABOVE_VERTICES, setback, t, setback, t, t, t, _SIMPLIFY_ABOVE_VERTICES,
            setback, setback, t, t, list(ids))


def _rings_from_geojson(text: Any) -> list[list[list[tuple[float, float]]]]:
    """Parts (each [outer, *holes]) as open rings rounded to 0.01 ft, sorted
    by (outer area desc, first vertex) so part order never matters."""
    g = json.loads(text) if isinstance(text, str) else text
    if not isinstance(g, dict):
        return []
    if g.get("type") == "Polygon":
        polys = [g.get("coordinates") or []]
    elif g.get("type") == "MultiPolygon":
        polys = g.get("coordinates") or []
    else:
        return []
    parts = []
    for poly in polys:
        rings = []
        for ring in poly or []:
            pts = [(round(float(x), 2), round(float(y), 2)) for x, y, *_ in ring]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(set(pts)) >= 3:
                rings.append(pts)
        if rings:
            parts.append(rings)

    def key(part):
        outer = part[0]
        return (-round(abs(_ring_area(outer)), 2), min(outer))
    return sorted(parts, key=key)


def _lattice(proto: Mapping[str, Any], step: float, cap_len: float | None) -> list[float]:
    lo, hi = float(proto["min_length_ft"]), float(proto["L"])
    vals = []
    v = lo
    while v <= hi + 1e-9:
        if cap_len is None or v <= cap_len + 1e-9:
            vals.append(v)
        v += step
    return vals


_FIT_CACHE: dict[str, tuple] = {}


def _fits_for(rings_text: str, gross_sf: float, rules: _Rules,
              data: Mapping[str, Any]) -> tuple[tuple[PrototypeFit, ...], bool,
                                                 tuple[float, float]]:
    """All prototype fits for one buffered geometry (cached per process,
    keyed by the geometry text, the cap, and both files' SHA-256)."""
    key = hashlib.sha256(json.dumps(
        [rings_text, round(gross_sf, 2), rules.cap_pct, _STATE.sha256,
         zoning.rules_sha256()]).encode()).hexdigest()
    hit = _FIT_CACHE.get(key)
    if hit is not None:
        return hit
    parts = _rings_from_geojson(rings_text)
    allx = [x for part in parts for r in part for x, _ in r]
    ally = [y for part in parts for r in part for _, y in r]
    origin = (float(math.floor(min(allx))), float(math.floor(min(ally)))) if allx else (0.0, 0.0)
    rings = [[(round(x - origin[0], 2), round(y - origin[1], 2)) for x, y in r]
             for part in parts for r in part]
    step = float(data["step_ft"])
    protos = data["prototypes"]
    lattices = {}
    for k in PROTOTYPE_KEYS:
        p = protos[k]
        cap_len = None
        if rules.cap_pct is not None:
            cap_len = rules.cap_pct / 100.0 * gross_sf / float(p["W"])
        lattices[k] = _lattice(p, step, cap_len)
    by_depth: dict[float, list[float]] = {}
    for k in PROTOTYPE_KEYS:
        d = envelope_depth(protos[k])
        merged = sorted(set(by_depth.get(d, [])) | set(lattices[k]))
        by_depth[d] = merged
    found = fit_lengths(rings, sorted(by_depth.items()))
    fits = []
    for k in PROTOTYPE_KEYS:
        p = protos[k]
        d = envelope_depth(p)
        lat = lattices[k]
        got = found.get(d)
        length = None
        if got is not None and lat:
            usable = [v for v in lat if v <= got[1].length_free + 1e-9]
            length = max(usable) if usable else None
        w = float(p["W"])
        if length is None:
            if not lat:
                binding = "does not fit: coverage cap below the minimum length"
            else:
                binding = (f"does not fit: geometry (needs {_fmt_ft(lat[0])} x {_fmt_ft(d)} ft "
                           f"envelope)")
            fits.append(PrototypeFit(k, p["label"], False, False, None, w, d, None, None, None,
                                     None, binding, None))
            continue
        full = length == float(p["L"])
        if full:
            binding = "full size"
        elif lat and length == lat[-1] and lat[-1] < float(p["L"]):
            binding = "coverage cap"
        elif length == float(p["min_length_ft"]):
            binding = "geometry (at minimum length)"
        else:
            binding = "geometry"
        sf = int(round(length * w))
        cov = round(100.0 * sf / gross_sf, 1) if gross_sf > 0 else None
        pl = got[1]
        fits.append(PrototypeFit(k, p["label"], True, full, length, w, d, sf,
                                 dock_doors(p, length), trailer_stalls(p, length), cov, binding,
                                 pl.angle_deg, pl.xc, pl.y0))
    out = (tuple(fits), len(parts) > 1, origin)
    if len(_FIT_CACHE) >= _FIT_CACHE_MAX:
        _FIT_CACHE.clear()
    _FIT_CACHE[key] = out
    return out


def _row_flood_text(row: Sequence[Any] | None, flood: str | None) -> str | None:
    """reviews/26_flood_wetlands: the deduction text of a hazards-SQL row
    (columns 6 and 7 are the NFHL and NWI states), else ``flood``."""
    if flood is None and row is not None and len(row) > 7:
        return _hz.deduction_text(str(row[6]), str(row[7]))
    return flood


def _project_row(pid: str, rules: _Rules, row: Sequence[Any] | None,
                 data: Mapping[str, Any], flood: str | None = None) -> Projection:
    """Pure: one buffered-geometry row -> Projection."""
    flood = _row_flood_text(row, flood)
    if row is None or row[1] is None:
        return _empty(pid, NO_GEOMETRY, "no parcel geometry", rules, flood=flood)
    gross = float(row[1]) * SQFT_PER_M2
    if gross <= 0:
        return _empty(pid, NO_GEOMETRY, "parcel geometry has no area", rules, flood=flood)
    simplified = bool(row[5]) if len(row) > 5 else False
    extra = (SIMPLIFIED,) if simplified else ()
    if row[2] or row[4] is None:
        return _empty(pid, EMPTY, "nothing left inside the setbacks", rules, int(round(gross)),
                      extra, flood=flood)
    nverts = sum(len(r) for part in _rings_from_geojson(str(row[4])) for r in part)
    if nverts > _MAX_FIT_VERTICES:
        return _empty(pid, ERROR, f"buffered geometry too complex ({nverts} vertices > "
                      f"{_MAX_FIT_VERTICES})", rules, int(round(gross)), extra, flood=flood)
    fits, multipart, origin = _fits_for(str(row[4]), gross, rules, data)
    constraints = _base_constraints(rules, flood) + list(extra)
    if multipart:
        constraints.append("multipart buildable area: each envelope fitted inside one part")
    return _issue(Projection(
        parcel_id=pid, status=OK, reason="projected", rules_label=rules.label,
        rules_verified=rules.verified, setback_ft=rules.setback_ft,
        setback_source=rules.setback_source, gross_sf=int(round(gross)),
        buildable_sf=int(round(float(row[3] or 0.0))), coverage_cap_pct=rules.cap_pct,
        fits=fits, constraints=tuple(constraints), multipart=multipart,
        setback_from_rules=rules.from_rules, flood_wetlands=_flood_text(flood),
        _origin=origin, _geometry=str(row[4])))


def _flood_wetland_exclusions(parcel_id: str) -> list[Any]:
    """Hook for H4 (FEMA flood) and H6 (NWI wetlands). Returns no Python
    geometry, ever: the exclusion polygons are read in PostGIS by
    _SQL_BUFFERED_FOR_FIT_HAZARDS (never passed in by a caller) and
    differenced from the buildable area there, only while the hazard table
    is enabled (reviews/26_flood_wetlands, FW-14)."""
    return []


_INTRANS, _IDLE = 2, 0  # psycopg.pq.TransactionStatus values (no import needed)


def _query(conn: Any, sql: str, params: tuple) -> list[tuple]:
    """One read that never poisons the caller's connection (SF-14).

    Inside an open transaction the read runs in a SAVEPOINT
    (``conn.transaction()`` nests as one), so a failure rolls back to it and
    the caller's transaction stays usable. On an idle connection the read
    runs plainly and a failure is followed by ``rollback()`` (there was
    nothing to lose). A connection without ``info`` (the suite's fakes) runs
    the read plainly; the exception reaches the caller either way.
    """
    status = getattr(getattr(conn, "info", None), "transaction_status", None)
    status = int(status) if status is not None else None
    if status == _INTRANS:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    except Exception:
        if status == _IDLE:
            conn.rollback()
        raise


def project_parcels(conn: Any, parcel_ids: Iterable[str]) -> Mapping[str, Projection]:
    """Projections for many parcels: one zoning read plus one buffered
    geometry read per distinct setback. Never raises; a failed read gives
    status 'error' for the parcels it covered."""
    ids = sorted({str(p) for p in parcel_ids})
    out: dict[str, Projection] = {}
    if not ids:
        return types.MappingProxyType(out)
    data = _STATE.data
    if data is None:
        for pid in ids:
            out[pid] = _empty(pid, UNAVAILABLE, _STATE.problem or "prototypes unavailable", None)
        return types.MappingProxyType(out)
    try:
        zrows = {r[0]: (r[1], r[2]) for r in _query(conn, _SQL_ZONING_FOR_FIT, (ids,))
                 if len(r) >= 3}
    except Exception as exc:
        for pid in ids:
            out[pid] = _empty(pid, ERROR, f"zoning read failed ({type(exc).__name__})", None)
        return types.MappingProxyType(out)
    rules = {pid: _rules_for(zrows.get(pid), pid, data) for pid in ids}
    groups: dict[float, list[str]] = {}
    for pid in ids:
        _flood_wetland_exclusions(pid)
        groups.setdefault(rules[pid].setback_ft, []).append(pid)
    use_hazards = _hz.enabled()
    for setback in sorted(groups):
        group = groups[setback]
        flood = None
        try:
            rows = None
            if use_hazards:
                # reviews/26_flood_wetlands: a failed hazard read falls back
                # to the plain buffer, labelled (never an error status).
                try:
                    rows = {r[0]: r for r in _query(conn, _SQL_BUFFERED_FOR_FIT_HAZARDS,
                                                    _buffered_params_hazards(setback, group))
                            if len(r) >= 5}
                except Exception:
                    rows, flood = None, FLOOD_UNREADABLE
            if rows is None:
                rows = {r[0]: r for r in _query(conn, _SQL_BUFFERED_FOR_FIT,
                                                _buffered_params(setback, group))
                        if len(r) >= 5}
        except Exception as exc:
            for pid in group:
                out[pid] = _empty(pid, ERROR, f"geometry read failed ({type(exc).__name__})",
                                  rules[pid], flood=flood)
            continue
        for pid in group:
            try:
                out[pid] = _project_row(pid, rules[pid], rows.get(pid), data, flood)
            except Exception as exc:
                out[pid] = _empty(pid, ERROR, f"fit failed ({type(exc).__name__})", rules[pid],
                                  flood=flood)
    return types.MappingProxyType(out)


def project_parcel(conn: Any, parcel_id: str) -> Projection:
    """The projection for one parcel (see the module docstring). Never raises."""
    return project_parcels(conn, [parcel_id])[str(parcel_id)]


# ---------------------------------------------------------------------------
# Operator export (SiteFit study JSON) and read helpers for the CLI
# ---------------------------------------------------------------------------
_SQL_TOP_SCORED = (
    "SELECT parcel_id FROM (SELECT DISTINCT ON (s.parcel_id) s.parcel_id, s.composite_score "
    "FROM parcel_scores s JOIN parcels p ON p.parcel_id = s.parcel_id WHERE p.market = %s "
    "ORDER BY s.parcel_id, s.scored_at DESC, s.score_id DESC) l "
    "ORDER BY composite_score DESC NULLS LAST, parcel_id LIMIT %s"
)
_SQL_EXPORT_GEOMETRY = (
    "SELECT ST_AsGeoJSON(ST_Transform(ST_GeomFromText(%s, 2240), 4326), 9), "
    "(SELECT ST_AsGeoJSON(d.geom, 9) FROM ST_Dump(ST_CollectionExtract("
    "ST_MakeValid(p.geometry), 3)) d ORDER BY ST_Area(d.geom) DESC LIMIT 1) "
    "FROM parcels p WHERE p.parcel_id = %s AND p.geometry IS NOT NULL"
)


def top_scored_parcel_ids(conn: Any, market: str, n: int) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(_SQL_TOP_SCORED, (market, int(n)))
        return [str(r[0]) for r in cur.fetchall()]


def best_fit(p: Projection) -> PrototypeFit | None:
    """The largest fitted prototype (ties: file order cross, front, rear)."""
    best = None
    for f in p.fits:
        if f.fits and (best is None or (f.building_sf or 0) > (best.building_sf or 0)):
            best = f
    return best


def building_corners(p: Projection, f: PrototypeFit, margin: float = 0.0) -> list[tuple]:
    """The fitted building's four corners in SRID 2240 feet, in SiteFit's
    element order (front wall first: front = -y of the element frame).

    With ``margin`` > 0 the envelope is re-fitted, inside the LARGEST part of
    the buildable area only, with ``margin`` ft of clearance on every side,
    so SiteFit's own setback line (a different plane) does not flag a
    sliver; when that fails, the projection's own placement is used."""
    if p._origin is None or f._xc is None or f._y0 is None or f.length_ft is None:
        raise ValueError("no placement")
    xc, y0, ang = f._xc, f._y0, f.angle_deg or 0.0
    if margin > 0 and p._geometry:
        ox, oy = p._origin
        parts = _rings_from_geojson(p._geometry)
        rings = [[(round(x - ox, 2), round(y - oy, 2)) for x, y in r] for r in parts[0]]
        got = fit_lengths(rings, [(f.depth_ft + 2 * margin, [f.length_ft + 2 * margin])])
        hit = got.get(f.depth_ft + 2 * margin)
        if hit is not None:
            xc, y0, ang = hit[1].xc, hit[1].y0 + margin, hit[1].angle_deg
    ct = f.depth_ft - f.width_ft
    if f.key == "cross":
        yb = y0 + ct / 2.0
    elif f.key == "front":
        yb = y0 + ct
    else:
        yb = y0
    half = f.length_ft / 2.0
    local = [(xc - half, yb), (xc + half, yb), (xc + half, yb + f.width_ft),
             (xc - half, yb + f.width_ft)]
    rad = math.radians(ang)
    c, s = math.cos(rad), math.sin(rad)
    ox, oy = p._origin
    return [(x * c - y * s + ox, x * s + y * c + oy) for x, y in local]


# SiteFit's local plane (mpd/toLocal/toLL), ported for the export.
_FT = 0.3048


def _mpd(lat: float) -> tuple[float, float]:
    r = math.radians(lat)
    return (111132.92 - 559.82 * math.cos(2 * r) + 1.175 * math.cos(4 * r),
            111412.84 * math.cos(r) - 93.5 * math.cos(3 * r))


def to_local(ll: Sequence[float], o: Sequence[float]) -> tuple[float, float]:
    mlat, mlng = _mpd(o[0])
    return ((ll[1] - o[1]) * mlng / _FT, (ll[0] - o[0]) * mlat / _FT)


def to_ll(pt: Sequence[float], o: Sequence[float]) -> tuple[float, float]:
    mlat, mlng = _mpd(o[0])
    return (o[0] + pt[1] * _FT / mlat, o[1] + pt[0] * _FT / mlng)


def study_document(p: Projection, f: PrototypeFit, boundary_ll: Sequence[Sequence[float]],
                   corners_ll: Sequence[Sequence[float]]) -> dict[str, Any]:
    """A SiteFit study (saveStudy/loadStudy shape, .sitefit.json download
    form) holding the parcel boundary and the fitted building. Pure.

    ``boundary_ll`` is the largest part's outer ring as [lat, lng] (closing
    vertex dropped here); ``corners_ll`` the building corners as [lat, lng]
    in building_corners order."""
    ring = [[round(float(a), 8), round(float(b), 8)] for a, b in boundary_ll]
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    n = len(ring)
    clat = sum(c[0] for c in corners_ll) / 4.0
    clng = sum(c[1] for c in corners_ll) / 4.0
    center = [round(clat, 9), round(clng, 9)]
    a = to_local(corners_ll[0], center)
    b = to_local(corners_ll[1], center)
    rot = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    rot = round(((rot + 180.0) % 360.0) - 180.0, 3)
    proto = load_prototypes()["prototypes"][f.key]
    fields = ("type", "L", "W", "clear", "court", "trailerOn", "trailer", "stall", "doorSp",
              "endClr")
    params = {k: proto[k] for k in fields}
    params["L"] = f.length_ft
    sid = "lr-" + hashlib.sha256(p.parcel_id.encode()).hexdigest()[:12]
    notes = [f"{f.label} {f.building_sf:,} SF, {f.docks} docks, {f.trailer_stalls} trailer "
             f"stalls ({p.rules_label})"] + list(p.constraints)
    if p.multipart:
        notes.append("largest part only")
    return {
        "app": "sitefit", "v": 1, "id": sid, "name": p.parcel_id, "sample": False,
        "boundary": ring, "setbacks": [p.setback_ft] * n, "edgeTags": [""] * n,
        "defaultSetback": 25,
        "elements": [{"id": sid + "-b", "kind": "building",
                      "name": f"{f.label} projection", "p": params,
                      "center": center, "rot": rot}],
        "view": None, "notes": notes,
    }


def export_geometry(conn: Any, p: Projection, f: PrototypeFit,
                    margin: float = 2.0) -> tuple[list, list]:
    """(boundary [lat,lng] ring of the largest part, building corners
    [lat,lng]) via PostGIS; see building_corners for ``margin``."""
    corners = building_corners(p, f, margin)
    wkt = "POLYGON((" + ", ".join(f"{x:.4f} {y:.4f}" for x, y in corners + corners[:1]) + "))"
    with conn.cursor() as cur:
        cur.execute(_SQL_EXPORT_GEOMETRY, (wkt, p.parcel_id))
        row = cur.fetchone()
    if not row or row[0] is None or row[1] is None:
        raise ValueError("no geometry")
    b = json.loads(row[0])["coordinates"][0][:4]
    ring = json.loads(row[1])["coordinates"][0]
    return ([[lat, lng] for lng, lat, *_ in ring], [[lat, lng] for lng, lat, *_ in b])
