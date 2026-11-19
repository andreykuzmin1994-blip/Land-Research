"""The test-fit sheet: one SVG per site plan from ``siteplan`` (planner v4).

A sheet a reviewer can read without the JSON beside it:

- a header with the site, the layout and one status;
- the plan, drawn as published industrial site plans are (open ground
  green, the building one warm solid, truck paving grey, car paving
  blue-grey, trailer stalls dark, water blue, the street a dark band,
  dimension strings in red) and labelled: building size, length and
  depth, truck court depth, dock doors, stall counts, pond storage,
  driveways, frontage, residential edges and both setback lines, with
  a north arrow and a scale bar;
- key numbers, the next steps in order, where the land goes, the plan
  against its DRAFT zoning values, and the layouts compared;
- a register of conditions: each flag with its workstream, when it is
  dealt with, what to do and what closes it;
- the legend, the basis of the estimates and the disclaimer.

Self-contained: it draws only what ``siteplan.plan_site`` returns (a
``Drawing`` in the site's feet plane, plus the record) and imports only
pure helpers (``plan._esc`` for text, ``sitefit.polygon_parts``, the
read-out in ``siteplan_assess``). It is not the run-brief renderer
``plan.render`` and does not change it (R5).

Safe output (R3): every text goes through ``plan._esc`` (XML-escaped,
quotes included, characters XML cannot hold replaced), and the drawing
uses only plain shapes: no script, no href / xlink:href, no event
attributes, no style element, and ``url()`` only as ``url(#id)`` for the
five patterns defined in the file itself (four hatches and the grass
stipple).

Two things on the plan are context, drawn from inputs the planner already
read and changing nothing in the record: the neighbouring county parcels
(residential ones tinted, by the same test as the residential edges) and
contour lines of the existing ground, traced from the lidar samples
(``Drawing.dem``, a 40 ft grid, so the lines are smoothed estimates). Numbers have one fixed
format, so the same plan gives the same bytes.

Every sheet says DRAFT (the zoning values are draft) and, when an input
was not checked, INCOMPLETE with the list of missing inputs.
"""

from __future__ import annotations

import math
from typing import Any

from shapely.affinity import translate
from shapely.geometry import LineString, Point, box
from shapely import make_valid
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring, unary_union

from . import sitefit
from . import siteplan_assess as assess
from .plan import _esc
from .siteplan import COMP_BAND_SF_PER_AC, COURT, RIM, TRAILER_ROW, Drawing, rnd

# The plan's colours follow published industrial site plans, in the muted
# tones of a presentation plan: open ground a soft green with a grass
# stipple, the building a pale roof with a cast shadow, truck paving
# asphalt grey, car paving blue-grey, trailer stalls dark, water blue with
# a deeper middle, the street asphalt with a centre line; the parcels
# round the site in outline on a paper ground, and the lidar contours.
C = {"bg": "#ffffff", "parcel": "#dde7cf", "grass": "#c3d3ad", "ink": "#1f2a30", "env": "#4d5b63", "cons": "#dcebf8",
     "cons_s": "#3f7fb8", "trans": "#d9942b", "cut": "#c92a2a", "steep": "#8a5a2b", "bldg": "#ede2cd", "bldg_s": "#3b3128",
     "office": "#fbf8f1", "court": "#8e959b", "road": "#8e959b", "trailer": "#59616a", "car": "#b8c2cf", "pond": "#86bfdf",
     "pond_deep": "#5fa3cc", "pond_s": "#2a6a93", "pond_rim": "#c6dcb2", "street": "#6c7278", "centre": "#f0c24b",
     "door": "#2c2620", "shadow": "#1f2a30", "paper": "#f5f4ef", "ctx": "#efeee8", "ctx_res": "#ede4ef", "ctx_s": "#c2bdb1",
     "contour": "#8c7454", "drive": "#d9480f", "dim": "#b3261e",
     "mute": "#5d676e", "rule": "#c9ced2", "band": "#f3f5f6", "benv": "#a0421f", "res": "#7b3fa0",
     "good": "#1f7a4d", "warn": "#8a6d00", "bad": "#b3261e"}
SHADOW_PX = 5.0                # the building's cast shadow, down and right, in sheet px
CONTOUR_INDEX_EVERY = 5        # every fifth contour is an index contour, heavier and labelled
STREET_FT = 34.0               # width of the street band drawn outside the frontage
STREET_TRIM_FT = 36.0          # cut from each end of a frontage run before the band is drawn
W, PLAN, M = 1600, 820, 24
PAD = 40                       # room inside the plan box for labels at the parcel's edge
COL_X = M + PLAN + 24
COL_W = W - M - COL_X
FONT = "Helvetica, Arial, sans-serif"
# Advance widths of the sheet's font (Helvetica and its metric-compatible
# stand-ins), in thousandths of an em, so text is wrapped by its width and
# not by a character count: a line of capitals is a third wider than prose.
_WIDE = {**dict.fromkeys(" .,:;/!|", 278), **dict.fromkeys("-()[]\"rf", 333), **dict.fromkeys("ijl'", 222), "t": 278,
         **dict.fromkeys("ckszvxy", 500), **dict.fromkeys("mM", 833), "w": 722, "W": 944, "I": 278, "J": 500, "L": 556,
         **dict.fromkeys("FTZ", 611), **dict.fromkeys("CDHNRU", 722), **dict.fromkeys("GOQ", 778), "%": 889, "+": 584,
         **dict.fromkeys("=<>~", 584), "&": 667}
_UPPER, _OTHER = 667, 556      # other capitals; lower case, digits and the rest
BOLD = 1.06
DISCLAIMER = ("Screening test fit from public data: not a survey, civil design or zoning determination. Zoning values are draft; "
              "detention is a +/-30% estimate; routes, fire access and grading are indicative.")
STATUS_FILL = {"fits": C["good"], "fits_with_conditions": C["warn"], "gated": C["warn"], "hypothetical": C["bad"],
               "blocked": C["bad"], "no_fit": C["bad"], "incomplete": C["mute"], "not_planned": C["mute"]}
KIND_FILL = {"blocking": C["bad"], "gate": C["warn"], "site": C["warn"], "standing": C["env"], "information": C["rule"]}
ROW_STATUS_FILL = {"meets": C["good"], "short": C["bad"], "over": C["bad"], "limited": C["warn"], "gate": C["warn"],
                   "verify": C["warn"], "no draft value": C["mute"]}
NEXT_STEPS_SHOWN = 5
LAYOUTS_SHOWN = 5


def _scale(d: Drawing) -> tuple[float, float, float, float, float]:
    """(px per ft, x offset, y offset, min x, max y) of the plan box."""
    minx, miny, maxx, maxy = d.parcel.bounds
    span = max(maxx - minx, maxy - miny)
    s = (PLAN - 2 * PAD) / span
    ox = M + (PLAN - (maxx - minx) * s) / 2
    oy = M + (PLAN - (maxy - miny) * s) / 2
    return s, ox, oy, minx, maxy


def sheet_info(d: Drawing) -> dict[str, Any]:
    """What a reader of the sheet needs to compare two of them: the
    plan's scale (ft per SVG px) and the sheet's width."""
    s = _scale(d)[0]
    return {"ft_per_px": round(1 / s, 4), "width_px": W, "plan_px": PLAN}


def _num(x: Any, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def _int(x: Any) -> str:
    return "n/a" if x is None else f"{x:,.0f}"


def text_width(txt: str, size: float, bold: bool = False) -> float:
    """The width of a line of the sheet's font, in px."""
    em = sum(_WIDE.get(ch, _UPPER if ch.isupper() else _OTHER) for ch in txt) / 1000.0
    return em * size * (BOLD if bold else 1.0)


def _wrap(txt: str, px: float, size: float, bold: bool = False) -> list[str]:
    """Lines of `txt` no wider than `px` (a word wider than the line is
    broken)."""
    lines: list[str] = []
    line = ""
    for word in str(txt).split():
        while text_width(word, size, bold) > px and len(word) > 1:
            # a word longer than the line: cut it where it stops fitting
            head = (line + " " if line else "")
            k = len(word)
            while k > 1 and text_width(head + word[:k], size, bold) > px:
                k -= 1
            if line and k <= 1:
                lines.append(line)
                line = ""
                continue
            lines.append(head + word[:k])
            line, word = "", word[k:]
        trial = f"{line} {word}" if line else word
        if text_width(trial, size, bold) <= px:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines or [""]


def _upright(deg: float) -> float:
    """A text angle kept readable: from -90 (reads upward, as on a
    drawing) to just under 90."""
    deg = (deg + 180.0) % 360.0 - 180.0
    if deg >= 89.5:
        deg -= 180.0
    elif deg < -90.5:
        deg += 180.0
    return deg


def contour_interval(relief_ft: float) -> int:
    """Contour interval (ft) for the relief of a site: about 6 to 15 lines across it."""
    for step in (1, 2, 5, 10):
        if relief_ft <= step * 15:
            return step
    return 20


def contours(dem: Any, region: BaseGeometry, chaikin: int = 2) -> tuple[int, list[tuple[float, BaseGeometry]]]:
    """Contour lines of the existing ground from the lidar samples:
    (interval ft, [(elevation, line clipped to `region`)]), elevations in
    ascending order. Marching squares on the sample grid (a cell with a
    sample missing at a corner is skipped; a saddle is split by the
    mean of the four corners), the pieces joined into lines and
    smoothed by corner cutting (Chaikin). (0, []) when there is too
    little to draw (no samples, or ground flat within one interval)."""
    if dem is None or not getattr(dem, "elev", None) or region is None or region.is_empty:
        return 0, []
    pts = {(int(round(i)), int(round(j))): p for (i, j), p in dem.points.items()}
    z = {}
    for (i, j), v in dem.elev.items():
        try:
            zf = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(zf):
            z[(int(round(i)), int(round(j)))] = zf
    inside = [v for k, v in z.items() if k in pts and region.contains(Point(pts[k]))]
    if len(inside) < 4:
        return 0, []
    step = contour_interval(max(inside) - min(inside))
    lo, hi = math.ceil(min(z.values()) / step) * step, math.floor(max(z.values()) / step) * step
    segs: dict[int, list[tuple[tuple[float, float], tuple[float, float]]]] = {}
    for (i, j) in sorted(z):
        corners = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
        if not all(c in z and c in pts for c in corners):
            continue
        zs = [z[c] for c in corners]
        for level in range(int(max(lo, math.ceil(min(zs) / step) * step)), int(min(hi, max(zs))) + 1, step):
            above = [v >= level for v in zs]
            if all(above) or not any(above):
                continue
            # the crossing on each side: bottom, right, top, left (the
            # side's ends in a fixed order, so both cells sharing it agree)
            cross: dict[int, tuple[float, float]] = {}
            for k in range(4):
                c0, c1 = sorted((corners[k], corners[(k + 1) % 4]))
                z0, z1 = z[c0], z[c1]
                if (z0 >= level) != (z1 >= level):
                    t = (level - z0) / (z1 - z0)
                    (x0, y0), (x1, y1) = pts[c0], pts[c1]
                    cross[k] = (round(x0 + (x1 - x0) * t, 3), round(y0 + (y1 - y0) * t, 3))
            ks = sorted(cross)
            if len(ks) == 2:
                pairs = [(ks[0], ks[1])]
            else:
                # a saddle: cut off the two corners on the other side from the middle
                cut_below = sum(zs) / 4 >= level
                a_cut = above[0] != cut_below
                pairs = [(3, 0), (1, 2)] if a_cut else [(0, 1), (2, 3)]
            for k0, k1 in pairs:
                segs.setdefault(level, []).append((cross[k0], cross[k1]))
    out: list[tuple[float, BaseGeometry]] = []
    for level in sorted(segs):
        for line in _chains(segs[level]):
            for _ in range(chaikin):
                line = _chaikin(line)
            if len(line) < 2:
                continue
            g = LineString(line).intersection(region)
            if not g.is_empty and g.length > 0:
                out.append((float(level), g))
    return (step, out) if out else (0, [])


def _chains(segs: list) -> list[list[tuple[float, float]]]:
    """Segments sharing ends joined into lines (closed lines repeat their first point)."""
    ends: dict[tuple[float, float], list[int]] = {}
    for n, (p, q) in enumerate(segs):
        ends.setdefault(p, []).append(n)
        ends.setdefault(q, []).append(n)
    used = [False] * len(segs)

    def walk(line: list) -> None:
        while True:
            tip = line[-1]
            nxt = next((m for m in ends.get(tip, []) if not used[m]), None)
            if nxt is None:
                return
            used[nxt] = True
            p, q = segs[nxt]
            line.append(q if p == tip else p)

    out = []
    # open lines first, from an end that only one segment touches, then loops
    order = [n for n, (p, q) in enumerate(segs) if len(ends[p]) == 1 or len(ends[q]) == 1] + list(range(len(segs)))
    for n in order:
        if used[n]:
            continue
        used[n] = True
        p, q = segs[n]
        if len(ends[q]) == 1 and len(ends[p]) != 1:
            p, q = q, p
        line = [p, q]
        walk(line)
        out.append(line)
    return out


def _chaikin(line: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """One round of corner cutting; a closed line stays closed, an open one keeps its ends."""
    if len(line) < 3:
        return line
    closed = line[0] == line[-1]
    out = [] if closed else [line[0]]
    for (x0, y0), (x1, y1) in zip(line, line[1:]):
        out += [(0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1), (0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1)]
    if closed:
        out.append(out[0])
    else:
        out[1:2] = []                     # the first cut point next to the kept end
        out[-1:] = [line[-1]]
    return out


def render(d: Drawing, rec: dict) -> str:
    parcel, P, constraints, trans, cut, steep, front_ft = d.parcel, d.P, d.constraints, d.trans, d.cut, d.steep, d.front_ft
    p, o, second, title = d.p, d.o, d.second, d.title
    s, ox, oy, minx, maxy = _scale(d)
    X = lambda x: ox + (x - minx) * s
    # The assessment is read from the record as given (a caller may pass a
    # record it changed), by the same function plan_site used.
    a = assess.assessment(rec, COMP_BAND_SF_PER_AC)
    status = a["status"]

    def path(g: BaseGeometry | None) -> str:
        out = []
        for q in (sitefit.polygon_parts(g) if g is not None and not g.is_empty else []):
            for ring in (q.exterior, *q.interiors):
                pts = [f"{X(x):.1f},{Y(y):.1f}" for x, y, *_ in ring.coords[:-1]]
                if len(pts) >= 3:
                    out.append("M" + " L".join(pts) + " Z")
        return " ".join(out)

    def lpath(g: BaseGeometry) -> str:
        out = []
        for ln in getattr(g, "geoms", [g]):
            if ln.geom_type in ("LineString", "LinearRing") and len(ln.coords) > 1:
                out.append("M" + " L".join(f"{X(x):.1f},{Y(y):.1f}" for x, y, *_ in ln.coords))
            elif hasattr(ln, "geoms"):
                out.append(lpath(ln))
        return " ".join(x for x in out if x)

    def poly(g, fill, stroke="none", sw=0, op=1.0, extra=""):
        dd = path(g)
        return f'<path d="{dd}" fill="{fill}" fill-opacity="{op}" stroke="{stroke}" stroke-width="{sw}" fill-rule="evenodd" {extra}/>' if dd else ""

    def text(x, y, txt, size=11.0, weight="normal", fill=C["ink"], anchor="start", rot=None, spacing=None, style=None,
             pivot_dy=0.0):
        tf = f' transform="rotate({rot:.1f} {x:.1f} {y + pivot_dy:.1f})"' if rot else ""
        sp = f' letter-spacing="{spacing}"' if spacing else ""
        st = f' font-style="{style}"' if style else ""
        an = f' text-anchor="{anchor}"' if anchor != "start" else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" fill="{fill}"{an}{sp}{st}{tf}>'
                f'{_esc(txt)}</text>')

    def label(x, y, lines, rot=0.0, fill=C["ink"]):
        """Centred lines of (text, size, weight) at a plan point, turned
        with the shape, each over a white halo so it reads on any fill."""
        total = sum(sz * 1.18 for _, sz, _ in lines)
        yy = y - total / 2
        parts = []
        for txt, sz, wt in lines:
            yy += sz * 1.18
            base = (f'x="{x:.1f}" y="{yy - sz * 0.22:.1f}" font-size="{sz}" font-weight="{wt}" text-anchor="middle"'
                    + (f' transform="rotate({rot:.1f} {x:.1f} {y:.1f})"' if rot else ""))
            parts.append(f'<text {base} fill="#ffffff" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" '
                         f'opacity="0.85">{_esc(txt)}</text><text {base} fill="{fill}">{_esc(txt)}</text>')
        return "".join(parts)

    def edge_angle(a0, a1) -> float:
        """Screen angle (degrees, upright) of the feet-plane segment a0-a1."""
        return _upright(math.degrees(math.atan2(-(a1[1] - a0[1]), a1[0] - a0[0])))

    def box_axes(g) -> tuple[float, float, float]:
        """(long side px, short side px, screen angle of the long side) of
        a rectangle built by the planner (its first two edges)."""
        c = list(g.exterior.coords)
        e1 = math.hypot(c[1][0] - c[0][0], c[1][1] - c[0][1])
        e2 = math.hypot(c[2][0] - c[1][0], c[2][1] - c[1][1])
        if e1 >= e2:
            return e1 * s, e2 * s, edge_angle(c[0], c[1])
        return e2 * s, e1 * s, edge_angle(c[1], c[2])

    # --------------------------------------------------------------- header
    b, tr, ca, cv, de, gr = (rec.get(k) or {} for k in ("building", "trailers", "cars", "coverage", "detention", "grading"))
    acc = rec.get("access") or {}
    sd = rec.get("site_data") or {}
    land = sd.get("land") or {}
    qn = rec.get("quantities") or {}
    both = rec.get("totals_both_buildings")
    sb = rec.get("second_building")
    states = (rec.get("inputs") or {}).get("states") or {}
    missing = sorted(k for k, v in states.items() if v.get("state") in ("not_checked", "partial"))
    head: list[str] = []
    pill_w = 330
    hy = M - 2
    head.append(text(M, hy + 6, "INDUSTRIAL TEST FIT  /  CONCEPT ONLY, NOT FOR CONSTRUCTION  /  DRAFT ZONING VALUES", 10,
                     fill=C["mute"], spacing="0.8"))
    ty = hy + 6
    title_lines = _wrap(title, W - 2 * M - pill_w - 20, 20, True)
    if len(title_lines) > 2:
        # a title longer than two lines is cut, and says so
        title_lines = [title_lines[0], _wrap(title_lines[1] + " " + title_lines[2], W - 2 * M - pill_w - 60, 20, True)[0] + " ..."]
    for seg_ in title_lines:
        ty += 24
        head.append(text(M, ty, seg_, 20, "bold"))
    ty += 18
    head.append(text(M, ty, rec["configuration"] + (" (operator prototype)" if rec["operator_prototype"]
                                                     else " (market depth, not an operator prototype)"), 12.5, fill=C["mute"]))
    px = W - M - pill_w
    head.append(f'<rect x="{px}" y="{hy + 2:.1f}" width="{pill_w}" height="30" rx="4" fill="{STATUS_FILL[status]}"/>')
    head.append(text(px + pill_w / 2, hy + 22, a["label"], 13.5, "bold", "#ffffff", "middle", spacing="0.5"))
    head.append(text(W - M, hy + 48, f"draft rules: {str(rec['zoning']['name'])[:40]}", 10.5, fill=C["mute"], anchor="end"))
    ty += 12
    head.append(f'<path d="M{M},{ty:.1f} L{W - M},{ty:.1f}" stroke="{C["ink"]}" stroke-width="1.2"/>')
    # the headline, and what holds the floor area back
    notes = [("b", a["headline"])]
    if rec.get("feasible") is None:
        notes.append(("f1", "INCOMPLETE: inputs not checked" + (": " + ", ".join(missing) if missing else "")
                      + " (floor area not counted)."))
    if rec.get("gated"):
        # Worded from total_sf: the default rule does not count a gated
        # plan, the prototype rule (--prototype-behaviour) does.
        counted = bool(rec.get("total_sf"))
        notes.append(("f1", "Gated: " + ", ".join(rec["gated"])
                      + (" (entitlement gate; floor area counted)." if counted else " (floor area not counted).")))
    for kind, txt in notes:
        for seg_ in _wrap(txt, W - 2 * M, 13, kind == "b"):
            ty += 18
            head.append(text(M, ty, seg_, 13, "bold" if kind == "b" else "normal", C["bad"] if kind == "f1" else C["ink"]))
    top = ty + 14                       # y of the plan box and the right column
    shift = top - M
    Y = lambda y: oy + shift + (maxy - y) * s


    # ----------------------------------------------------------------- plan
    # Drawn the way published industrial site plans are, in the muted tones
    # of a presentation plan: the neighbouring parcels in outline on a paper
    # ground, open ground green with a grass stipple, contours of the
    # existing ground, the building a pale roof with a cast shadow and its
    # dock doors dark on the wall, truck paving asphalt grey, car paving
    # blue-grey, trailer stalls dark with white stripes, water blue with a
    # deeper middle, the property line heavy, the street asphalt with a
    # centre line, dimension strings in red.
    out = [f'<defs><pattern id="tx" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="{C["trans"]}" stroke-width="3"/></pattern>'
           f'<pattern id="sx" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><line x1="0" y1="0" x2="0" y2="6" stroke="{C["steep"]}" stroke-width="1.3"/></pattern>'
           f'<pattern id="cx" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="{C["cut"]}" stroke-width="2"/></pattern>'
           f'<pattern id="wx" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect x="0" y="0" width="7" height="7" fill="{C["cons"]}"/><line x1="0" y1="0" x2="0" y2="7" stroke="{C["cons_s"]}" stroke-width="2"/></pattern>'
           f'<pattern id="gx" width="9" height="9" patternUnits="userSpaceOnUse"><rect x="0" y="0" width="9" height="9" fill="{C["parcel"]}"/>'
           f'<circle cx="2" cy="2.5" r="0.75" fill="{C["grass"]}"/><circle cx="6.5" cy="7" r="0.75" fill="{C["grass"]}"/></pattern></defs>']
    out.append(f'<rect x="{M}" y="{top:.1f}" width="{PLAN}" height="{PLAN}" fill="{C["paper"]}" stroke="{C["rule"]}" stroke-width="1"/>')
    # the plan box in the feet plane, which the context is clipped to
    view = box(minx + (M + 1 - ox) / s, maxy - (top + PLAN - 1 - oy - shift) / s,
               minx + (M + PLAN - 1 - ox) / s, maxy - (top + 1 - oy - shift) / s)
    marks = [q for q in (o["tpt"], o["cpt"]) if q is not None]
    pc = parcel.centroid
    # the neighbouring county parcels, in outline (residential ones tinted)
    drawn_ctx: set[str] = set()
    for kind, g in sorted(d.context or [], key=lambda t: t[0]):
        if g is None or g.is_empty:
            continue
        try:
            shown_g = make_valid(g).intersection(view).difference(parcel)
        except GEOSException:          # a shape GEOS cannot cut is context: leave it out, never the sheet
            continue
        piece = poly(shown_g, C["ctx_res"] if kind == "RES" else C["ctx"], C["ctx_s"], 0.7, extra='stroke-linejoin="round"')
        if piece:
            out.append(piece)
            drawn_ctx.add("RES" if kind == "RES" else "OTHER")
    # the street: an asphalt band outside the parcel along the frontage
    runs = ([ln for ln in getattr(front_ft, "geoms", [front_ft]) if ln.geom_type == "LineString" and ln.length > 1]
            if front_ft is not None and not front_ft.is_empty else [])
    # (each run less its ends: a frontage line hooks round the corner by a few feet)
    body = [substring(ln, STREET_TRIM_FT, ln.length - STREET_TRIM_FT) for ln in runs if ln.length > 4 * STREET_TRIM_FT]
    street = (unary_union(body).buffer(STREET_FT, cap_style=2, join_style=2).difference(parcel.buffer(0.5)) if body else None)
    out.append(poly(street, C["street"]))
    if body:
        # its centre line, dashed yellow: each run moved half the street's
        # width to the side away from the parcel, kept only on the street
        mid = []
        for ln in body:
            sides = [ln.offset_curve(k * STREET_FT / 2, join_style=2) for k in (1, -1)]
            sides = [g for g in sides if not g.is_empty]
            if sides:
                far = max(sides, key=lambda g: parcel.distance(g.interpolate(0.5, normalized=True)))
                cl = far.intersection(street.buffer(0.5)).intersection(view) if street is not None else far
                mid += [x for x in getattr(cl, "geoms", [cl]) if x.geom_type == "LineString" and x.length > 2 * STREET_FT]
        if mid:
            out.append(f'<path d="{lpath(unary_union(mid))}" stroke="{C["centre"]}" stroke-width="1.1" stroke-dasharray="9 7" '
                       f'fill="none"/>')
    out += [poly(parcel, "url(#gx)"), poly(constraints.intersection(parcel), "url(#wx)", C["cons_s"], 0.6),
            poly(steep, "url(#sx)", op=0.85), poly(trans, "url(#tx)", op=0.9), poly(cut, "url(#cx)", op=0.85)]
    plans = [(p, o)] + ([second] if second else [])
    for kind, rp in o["route_polys"] + ([] if not second else second[1]["route_polys"]):
        out.append(poly(rp, C["road"] if kind == "truck" else C["car"]))
    for pp, oo in plans:
        for g in pp.roads:
            out.append(poly(g, C["road"]))
        for g in pp.courts:
            out.append(poly(g, C["court"]))
        for g in pp.cars:
            out.append(poly(g, C["car"]))
        for g in oo["drops"]:
            out.append(poly(g, C["trailer"], C["ink"], 0.6))
        for g in pp.rows:
            out.append(poly(g, C["trailer"]))
        if pp.ticks:
            out.append(f'<path d="{lpath(unary_union(pp.ticks))}" stroke="#ffffff" stroke-width="1" fill="none" opacity="0.9"/>')
        for g in oo["ponds"]:
            # the grassed rim, the top of the bank, the water and its deeper middle
            water = g.buffer(-RIM)
            out.append(poly(g, C["pond_rim"], C["pond_s"], 0.6))
            out.append(poly(g.buffer(-RIM / 2), "none", C["pond_s"], 0.5, extra='stroke-dasharray="3 3" stroke-opacity="0.6"'))
            out.append(poly(water, C["pond"], C["pond_s"], 1.2))
            out.append(poly(water.buffer(-min(10.0, 0.18 * math.sqrt(max(water.area, 0.0)))), C["pond_deep"], op=0.55))
    # contours of the existing ground, over the paving (the grading it
    # implies reads through) and under the buildings
    every: int = 0
    lines_z: list[tuple[float, BaseGeometry]] = []
    dem_state = getattr(d.dem, "state", None) if d.dem is not None else None
    if dem_state == "checked" and getattr(d.dem, "elev", None):
        # (a partial answer is not drawn: unsampled ground would read as level)
        every, lines_z = contours(d.dem, parcel)
    # their labels only on open ground, clear of the paving and its labels
    keep_off = unary_union([g.buffer(10) for pp, oo in plans
                            for g in [pp.building, *pp.courts, *pp.cars, *pp.roads, *pp.rows, *oo["drops"], *oo["ponds"]]]
                           + [rp.buffer(10) for _, rp in o["route_polys"] + ([] if not second else second[1]["route_polys"])])
    contour_labels: list[str] = []
    for z, g in lines_z:
        index = round(z) % (every * CONTOUR_INDEX_EVERY) == 0
        out.append(f'<path d="{lpath(g)}" stroke="{C["contour"]}" stroke-width="{1.0 if index else 0.55}" fill="none" '
                   f'stroke-opacity="{0.85 if index else 0.7}" stroke-dasharray="{"7 3" if index else "4 3"}" stroke-linejoin="round"/>')
        if index:
            piece = max(getattr(g, "geoms", [g]), key=lambda ln: ln.length)
            if piece.geom_type == "LineString" and piece.length * s >= 120:
                for f in (0.5, 0.3, 0.7, 0.15, 0.85):
                    q = piece.interpolate(f, normalized=True)
                    room = (text_width(f"{z:.0f}", 7.5, True) / 2 + 4) / s
                    if not keep_off.intersects(q.buffer(room)):
                        q0 = piece.interpolate(max(f - 0.03, 0), normalized=True)
                        q1 = piece.interpolate(min(f + 0.03, 1), normalized=True)
                        contour_labels.append(label(X(q.x), Y(q.y), [(f"{z:.0f}", 7.5, "bold")],
                                                    edge_angle((q0.x, q0.y), (q1.x, q1.y)), C["contour"]))
                        break
    for pp, oo in plans:
        # the roof: a cast shadow, the roof itself, a parapet line just inside its edge
        out.append(poly(translate(pp.building, SHADOW_PX / s, -SHADOW_PX / s), C["shadow"], op=0.16))
        out.append(poly(pp.building, C["bldg"], C["bldg_s"], 1.8, extra='stroke-linejoin="round"'))
        out.append(poly(pp.building.buffer(-3.0 / s, join_style=2), "none", C["bldg_s"], 0.5, extra='stroke-opacity="0.45"'))
        for g in pp.offices:
            out.append(poly(g, C["office"], C["bldg_s"], 0.8))
        if pp.doors_geom:
            out.append(f'<path d="{lpath(unary_union(pp.doors_geom))}" stroke="{C["door"]}" stroke-width="3" fill="none" '
                       f'stroke-linecap="butt"/>')
    # the two setback lines, over the paving so they read everywhere
    out += [poly(P, "none", C["env"], 1.1, extra='stroke-dasharray="6 4"'),
            poly(d.B, "none", C["benv"], 1.1, extra='stroke-dasharray="10 3 2 3"')]
    res_lines = [ln for k, ln in d.edges if k.startswith("RES") and not ln.is_empty]
    if res_lines:
        out.append(f'<path d="{lpath(unary_union(res_lines))}" stroke="{C["res"]}" stroke-width="4.5" fill="none" '
                   f'stroke-dasharray="9 5"/>')
    # the property line: heavy, long dash and two short
    out.append(poly(parcel, "none", "#ffffff", 3.4))
    out.append(poly(parcel, "none", C["ink"], 2.2, extra='stroke-dasharray="20 4 3 4 3 4"'))

    # -- labels and dimension strings (each only where its shape is large enough to hold it)
    labels: list[str] = list(contour_labels)

    def dim(q0, q1, txt, at=0.5):
        """A dimension string between two feet-plane points: a line with
        an arrowhead at each end and the figure on it (`at`: how far
        along, so it can stand clear of a label at the middle)."""
        x0, y0, x1, y1 = X(q0[0]), Y(q0[1]), X(q1[0]), Y(q1[1])
        n = math.hypot(x1 - x0, y1 - y0)
        if n < 46:
            return ""
        ux, uy = (x1 - x0) / n, (y1 - y0) / n
        heads = ""
        for (hx, hy_), sg in (((x0, y0), 1.0), ((x1, y1), -1.0)):
            bx_, by_ = hx + sg * ux * 7, hy_ + sg * uy * 7
            heads += (f'<path d="M{hx:.1f},{hy_:.1f} L{bx_ - uy * 2.6:.1f},{by_ + ux * 2.6:.1f} L{bx_ + uy * 2.6:.1f},{by_ - ux * 2.6:.1f} Z" '
                      f'fill="{C["dim"]}"/>')
        line = f'<path d="M{x0:.1f},{y0:.1f} L{x1:.1f},{y1:.1f}" stroke="{C["dim"]}" stroke-width="1.1" fill="none"/>'
        return line + heads + label(x0 + (x1 - x0) * at, y0 + (y1 - y0) * at, [(txt, 10, "bold")], edge_angle(q0, q1), C["dim"])

    def lerp(q0, q1, t):
        return (q0[0] + (q1[0] - q0[0]) * t, q0[1] + (q1[1] - q0[1]) * t)

    for i, (pp, oo) in enumerate(plans, 1):
        cb = list(pp.building.exterior.coords)
        long_px, short_px = pp.L * s, pp.v["D"] * s
        ang = edge_angle(cb[0], cb[1])
        bc = pp.building.centroid
        name = f"BUILDING {i}" if len(plans) > 1 else "BUILDING"
        clear = (rec.get("building") or {}).get("clear_height_ft") if i == 1 else None
        if long_px >= 120 and short_px >= 58:
            lines = [(name, 10, "bold"), (f"{rnd(pp.sf, 1000):,} SF", 16, "bold")]
            if clear:
                lines.append((f"{clear} FT CLEAR", 9.5, "normal"))
            # (the default behaviour's sheet: the record then carries the ponds' ground against the pad)
            pad_el = ((gr.get("pad_elev_ft") if i == 1 else (sb or {}).get("pad_elev_ft")) if "ponds_over_pad_ft" in de else None)
            if pad_el is not None and short_px >= 70:
                # the level pad's elevation, to read against the contours and the pond
                lines.append((f"PAD EL {pad_el:.0f}", 8.5, "normal"))
            labels.append(label(X(bc.x), Y(bc.y), lines, 0.0 if short_px >= 104 else ang))
        else:
            labels.append(label(X(bc.x), Y(bc.y), [(f"{rnd(pp.sf, 1000):,} SF", 12, "bold")], ang if long_px >= 70 else 0.0))
        if long_px >= 150 and short_px >= 80:
            # the building's length and depth, as dimension strings across it
            labels.append(dim(lerp(cb[0], cb[3], 0.8), lerp(cb[1], cb[2], 0.8), f"{pp.L:.0f}'", at=0.27))
            labels.append(dim(lerp(cb[0], cb[1], 0.1), lerp(cb[3], cb[2], 0.1), f"{pp.v['D']:.0f}'"))
        per_wall = [pp.doors // len(pp.courts) + (1 if k < pp.doors % len(pp.courts) else 0) for k in range(len(pp.courts))]
        for g, n_doors in zip(pp.courts, per_wall):
            cc = list(g.exterior.coords)
            lp, sp_, an = box_axes(g)
            cen = g.centroid
            if lp >= 130 and sp_ >= 26:
                labels.append(label(X(cen.x), Y(cen.y), [("TRUCK COURT", 9.5, "bold")], an))
            if lp >= 150 and sp_ >= 46:
                # its depth, dock wall to the far edge, near one end
                labels.append(dim(lerp(cc[0], cc[1], 0.07), lerp(cc[3], cc[2], 0.07), f"{COURT}'"))
            if lp >= 150 and sp_ >= 50 and n_doors:
                # the door count, along the long edge that is the dock wall
                e = min(((cc[0], cc[1]), (cc[3], cc[2])), key=lambda t: bc.distance(_mid(t)))
                mx, my = (e[0][0] + e[1][0]) / 2, (e[0][1] + e[1][1]) / 2
                dx, dy = cen.x - mx, cen.y - my
                n = math.hypot(dx, dy) or 1.0
                labels.append(label(X(mx + dx / n * 20 / s), Y(my + dy / n * 20 / s),
                                    [(f"{n_doors} DOCK DOORS", 8.5, "normal")], an))
        if pp.rows and pp.court_stalls:
            per = pp.court_stalls // len(pp.rows)
            for g in pp.rows:
                lp, sp_, an = box_axes(g)
                if lp >= 86 and sp_ >= 13:
                    cc = g.centroid
                    labels.append(label(X(cc.x), Y(cc.y), [(f"{per} TRAILER STALLS", 9, "bold")], an))
        # building 1's stalls as the record states them; a second building's by the same rule
        cars_n = (rec.get("cars") or {}).get("provided") if i == 1 else _cars_provided(pp)
        for g in pp.cars:
            lp, sp_, an = box_axes(g)
            if cars_n and lp >= 80 and sp_ >= 14:
                cc = g.centroid
                labels.append(label(X(cc.x), Y(cc.y), [(f"{cars_n} CAR STALLS", 9.5, "bold")], an))
        n_drop = oo["trailers"] - pp.court_stalls
        for g in oo["drops"]:
            lp, sp_, an = box_axes(g)
            cc = g.centroid
            if lp >= 70 and sp_ >= 26:
                labels.append(label(X(cc.x), Y(cc.y), [("DROP YARD", 9, "bold"), (f"{n_drop} TRAILER STALLS", 9, "normal")], an))
        for g in oo["ponds"]:
            lp, sp_, an = box_axes(g)
            cc = g.centroid
            each = oo["v_det"] / max(len(oo["ponds"]), 1)
            if lp >= 80 and sp_ >= 34:
                pond_lines = [("DETENTION POND", 9, "bold"), (f"{each:.1f} AC-FT", 10.5, "bold")]
                # this pond's own figure: building 1's ponds, then a second building's, in the record's order
                overs = de.get("ponds_over_pad_ft") or []
                k_pond = sum(len(o_["ponds"]) for _, o_ in plans[:i - 1]) + oo["ponds"].index(g)
                over = overs[k_pond] if k_pond < len(overs) else None
                if over is not None and sp_ >= 60 and lp >= 115:
                    # where the pond's ground stands against the pad: the paving drains to it only from above
                    pond_lines.append((f"GROUND {abs(over):.0f} FT {'ABOVE' if over > 0 else 'BELOW'} PAD" if over else "GROUND LEVEL WITH PAD",
                                       8, "normal"))
                labels.append(label(X(cc.x), Y(cc.y), pond_lines, an, C["pond_s"]))
            elif lp >= 40:
                labels.append(label(X(cc.x), Y(cc.y), [("POND", 9, "bold")], an, C["pond_s"]))
    if res_lines:
        longest = max((ln for g in res_lines for ln in getattr(g, "geoms", [g]) if ln.geom_type == "LineString"),
                      key=lambda ln: ln.length, default=None)
        if longest is not None and longest.length * s >= 110:
            m0, m1 = longest.interpolate(0.45, normalized=True), longest.interpolate(0.55, normalized=True)
            mid = longest.interpolate(0.5, normalized=True)
            dx, dy = pc.x - mid.x, pc.y - mid.y
            n = math.hypot(dx, dy) or 1.0
            labels.append(label(X(mid.x + dx / n * 14 / s), Y(mid.y + dy / n * 14 / s), [("RESIDENTIAL EDGE", 9, "bold")],
                                edge_angle((m0.x, m0.y), (m1.x, m1.y)), C["res"]))
    acc = rec.get("access") or {}
    run = max(runs, key=lambda ln: ln.length, default=None)
    if run is not None and run.length * s >= 150:
        # the street's class and the frontage, in white on its band: where
        # the words fit on the band, as far from the driveway markers as they can be
        road = str(acc.get("road") or "").split(",")[0].strip().upper()
        for txt in ((road or "ROAD") + f"  /  {acc.get('frontage_ft') or 0:,} FT FRONTAGE", road or "ROAD"):
            need_ft = (text_width(txt, 9, True) + len(txt) + 16) / s          # the words, their letter-spacing, a margin
            edge = (STREET_TRIM_FT + need_ft / 2) / run.length
            if edge <= 0.5:
                break
        else:
            txt = ""
        if txt:
            spots = [min(max(f, edge), 1 - edge) for f in (0.5, 0.3, 0.7, 0.2, 0.8)]
            at = max(spots, key=lambda f: min((run.interpolate(f, normalized=True).distance(q) for q in marks), default=0.0))
            m0, m1 = run.interpolate(at - 0.02, normalized=True), run.interpolate(at + 0.02, normalized=True)
            mid = run.interpolate(at, normalized=True)
            dx, dy = mid.x - pc.x, mid.y - pc.y
            n = math.hypot(dx, dy) or 1.0
            off = STREET_FT / 2
            word = text(X(mid.x + dx / n * off), Y(mid.y + dy / n * off) + 3, txt, 9, "bold", "#ffffff", "middle",
                        rot=edge_angle((m0.x, m0.y), (m1.x, m1.y)), spacing="1", pivot_dy=-3)
            # over a halo of the street's own colour, so the centre line stops short of the words
            labels.append(word.replace('fill="#ffffff"', f'fill="{C["street"]}" stroke="{C["street"]}" stroke-width="6" '
                                                         'stroke-linejoin="round"', 1) + word)
    for pt, lab, word in ((o["tpt"], "T", "TRUCK ENTRY"), (o["cpt"], "C", "CAR ENTRY")):
        if pt is not None:
            out.append(f'<circle cx="{X(pt.x):.1f}" cy="{Y(pt.y):.1f}" r="10" fill="{C["drive"]}" stroke="#ffffff" stroke-width="1.6"/><text x="{X(pt.x):.1f}" y="{Y(pt.y)+4:.1f}" font-size="11" font-weight="bold" fill="#fff" text-anchor="middle">{lab}</text>')
            # the word sits inside the site, toward its middle (the street band is outside)
            dx, dy = pc.x - pt.x, pc.y - pt.y
            n = math.hypot(dx, dy) or 1.0
            labels.append(label(X(pt.x) + dx / n * 34, Y(pt.y) - dy / n * 34 + 3, [(word, 8.5, "bold")], 0.0, C["drive"]))
    out += labels
    # north arrow and scale bar, inside the plan box
    ax, ay = M + PLAN - 26, top + 14
    out.append(f'<path d="M{ax},{ay:.1f} L{ax+8},{ay+26:.1f} L{ax},{ay+20:.1f} L{ax-8},{ay+26:.1f} Z" fill="{C["ink"]}"/><text x="{ax}" y="{ay+40:.1f}" font-size="12" text-anchor="middle" font-weight="bold">N</text>')
    span = (PLAN - 2 * PAD) / s
    bar = 10 ** math.floor(math.log10(span * 0.25))
    bar = bar * (5 if bar * 5 <= span * 0.3 else 2 if bar * 2 <= span * 0.3 else 1)
    bx, by = M + 14, top + PLAN - 22
    seg = bar * s / 4
    for k in range(4):
        out.append(f'<rect x="{bx + k * seg:.1f}" y="{by:.1f}" width="{seg:.1f}" height="6" fill="{C["ink"] if k % 2 == 0 else "#ffffff"}" '
                   f'stroke="{C["ink"]}" stroke-width="0.8"/>')
    out.append(text(bx, by - 4, "0", 10))
    out.append(text(bx + 2 * seg, by - 4, f"{bar / 2:,.0f}", 10, anchor="middle"))
    out.append(text(bx + 4 * seg, by - 4, f"{bar:,.0f} ft", 10, anchor="middle"))

    # --------------------------------------------------------- right column
    col: list[str] = []
    cy = top

    def heading(x, y, txt, w):
        return (text(x, y + 11, txt, 10.5, "bold", C["ink"], spacing="0.8")
                + f'<path d="M{x:.1f},{y + 16:.1f} L{x + w:.1f},{y + 16:.1f}" stroke="{C["rule"]}" stroke-width="1"/>'), y + 24

    def tiles(y):
        tw_, th, gap = (COL_W - 3 * 8) / 4, 54, 8
        sfa = cv.get("sf_per_gross_ac")
        pos = (a.get("yield") or {}).get("position")
        lo, hi = COMP_BAND_SF_PER_AC
        doors = both["doors"] if both else rec.get("doors")
        t_tot, t_tgt = (both["trailers"], both["trailers_target"]) if both else (tr.get("total"), tr.get("target"))
        items = [
            ("BUILDING", f"{rec.get('total_sf_if_access') or b.get('sf') or 0:,} SF",
             "2 buildings" if sb else f"{_int(b.get('L_ft'))} x {_int(b.get('D_ft'))} ft"),
            ("YIELD", f"{_int(sfa)} SF/ac", f"{pos or 'against'} comps {lo:,}-{hi:,}"),
            ("FAR / BUILDING COVER", f"{cv.get('gross_far')} / {_num(sd.get('building_coverage_pct'), 0)}%",
             f"impervious {cv.get('impervious_pct')}%"),
            ("CLEAR HEIGHT", f"{b.get('clear_height_ft')} ft", "assumed for the size" if not _lowered(rec) else "lowered by the draft cap"),
            ("DOCK DOORS", _int(doors), f"{_num(sd.get('doors_per_10k_sf'))} per 10,000 SF"),
            ("TRAILER STALLS", f"{_int(t_tot)} / {_int(t_tgt)}", "provided / target"),
            ("CAR STALLS", f"{_int(ca.get('provided'))} / {_int(ca.get('required'))}",
             "provided / required" + (", building 1" if sb else "")),
            ("DETENTION", f"{de.get('storage_acft')} ac-ft",
             f"{de.get('ponds')} pond{'s' if de.get('ponds') != 1 else ''}, " + ("fits" if de.get("fits") else "does not fit")),
        ]
        res = []
        for k, (lab, val, sub) in enumerate(items):
            x, yy = COL_X + (k % 4) * (tw_ + gap), y + (k // 4) * (th + gap)
            res.append(f'<rect x="{x:.1f}" y="{yy:.1f}" width="{tw_:.1f}" height="{th}" fill="{C["band"]}" stroke="{C["rule"]}" stroke-width="0.8"/>')
            res.append(text(x + 8, yy + 14, lab, 8.5, fill=C["mute"], spacing="0.6"))
            res.append(text(x + 8, yy + 34, val, 16, "bold"))
            res.append(text(x + 8, yy + 47, sub, 9.5, fill=C["mute"]))
        return "".join(res), y + 2 * th + gap + 14

    def kv(x, y, w, rows, size=10.5):
        """Label left, value right, one line each (values are short)."""
        res = []
        for k, (lab, val, strong) in enumerate(rows):
            if k % 2 == 0:
                res.append(f'<rect x="{x:.1f}" y="{y - 10:.1f}" width="{w:.1f}" height="14" fill="{C["band"]}"/>')
            res.append(text(x + 4, y, lab, size, "bold" if strong else "normal"))
            res.append(text(x + w - 4, y, val, size, "bold" if strong else "normal", anchor="end"))
            y += 14
        return "".join(res), y

    def table(x, y, cols, rows, size=10.5, lh=13.5):
        """cols: [(title, width px, anchor)]; rows: [[cell | (cell, fill, weight)]]. Cells wrap."""
        res = []
        xs = [x]
        for _, w, _ in cols:
            xs.append(xs[-1] + w)
        for (ttl, w, an), x0 in zip(cols, xs):
            res.append(text(x0 + (w - 4 if an == "end" else 4), y, ttl, 9, "bold", C["mute"], an, spacing="0.5"))
        y += 5
        res.append(f'<path d="M{x:.1f},{y:.1f} L{xs[-1]:.1f},{y:.1f}" stroke="{C["rule"]}" stroke-width="1"/>')
        for k, row in enumerate(rows):
            cells = []
            for cell, (_, w, an) in zip(row, cols):
                txt, fill, wt = cell if isinstance(cell, tuple) else (cell, C["ink"], "normal")
                cells.append((_wrap(txt, w - 8, size, wt == "bold"), fill, wt))
            h = max(len(c[0]) for c in cells) * lh + 5
            if k % 2 == 1:
                res.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{xs[-1] - x:.1f}" height="{h:.1f}" fill="{C["band"]}"/>')
            for (lines, fill, wt), (_, w, an), x0 in zip(cells, cols, xs):
                for j, seg_ in enumerate(lines):
                    res.append(text(x0 + (w - 4 if an == "end" else 4), y + 12 + j * lh, seg_, size, wt, fill, an))
            y += h
        return "".join(res), y

    hd, cy = heading(COL_X, cy, "KEY NUMBERS", COL_W)
    col.append(hd)
    t, cy = tiles(cy)
    col.append(t)

    steps = a.get("next_steps") or []
    hd, cy = heading(COL_X, cy, "NEXT STEPS, IN ORDER", COL_W)
    col.append(hd)
    cy -= 4
    for st in steps[:NEXT_STEPS_SHOWN]:
        lead = f"{st['n']}.  {st['stage_label']}  /  {st['workstream']}"
        cy += 14
        col.append(text(COL_X + 4, cy, lead, 10.5, "bold"))
        for seg_ in _wrap(st["action"], COL_W - 26, 10.5):
            cy += 13.5
            col.append(text(COL_X + 22, cy, seg_, 10.5))
        cy += 3
    if len(steps) > NEXT_STEPS_SHOWN:
        cy += 13
        col.append(text(COL_X + 22, cy, f"and {len(steps) - NEXT_STEPS_SHOWN} more: see the conditions below", 10, fill=C["mute"]))
    if not steps:
        cy += 14
        col.append(text(COL_X + 4, cy, "Nothing beyond the conditions listed below.", 10.5))
    cy += 16

    if land:
        half = (COL_W - 16) / 2
        hd, y1 = heading(COL_X, cy, "WHERE THE LAND GOES (ACRES, % OF GROSS)", half)
        col.append(hd)
        gross = land.get("gross_ac") or 0
        pct = lambda v: f"{_num(v)}  ({100 * v / gross:.0f}%)" if gross and v is not None else _num(v)
        use = {u["use"]: u["ac"] for u in land.get("uses") or []}
        rows = [("Gross site, county map", _num(gross), True),
                ("Draft setbacks and buffers", pct(land.get("setbacks_buffers_ac")), False),
                ("Mapped constraints, test cut", pct(land.get("constraints_ac")), False),
                ("Net: inside the paving envelope", pct(land.get("net_ac")), True),
                ("Building footprint", pct(use.get("building")), False),
                ("Truck courts, roads and routes", pct(use.get("truck")), False),
                ("Trailer stalls and drop yards", pct(use.get("trailers")), False),
                ("Car parking and drives", pct((use.get("cars") or 0) + (use.get("car_drives") or 0)), False),
                ("Detention ponds, with rim", pct(use.get("ponds")), False),
                ("Left unused inside the envelope", pct(land.get("unused_net_ac")), True),
                ("  largest unused piece", _num(land.get("largest_unused_ac")), False)]
        t, y1 = kv(COL_X, y1 + 2, half, rows)
        col.append(t)
        x2 = COL_X + half + 16
        hd, y2 = heading(x2, cy, "PROGRAM AND TAKEOFF (NO PRICES)", half)
        col.append(hd)
        road = str((rec.get("access") or {}).get("road") or "no road").split(",")[0]
        pad = (f"elev {_int(gr.get('pad_elev_ft'))} ft; cut {_int(gr.get('max_cut_ft'))}, fill {_int(gr.get('max_fill_ft'))} ft"
               if gr.get("pad_elev_ft") is not None else "no lidar")
        rows = [("Building", f"{_int(b.get('L_ft'))} x {_int(b.get('D_ft'))} ft, office {_int(b.get('office_sf'))} SF", True),
                ("Trailers per dock door", _num(sd.get("trailers_per_door"), 2), False),
                ("Cars per 1,000 SF", f"{_num(ca.get('per_1000_sf'), 2)}  (ADA {ca.get('ada')}, {ca.get('stall')})", False),
                ("Frontage", f"{_int(acc.get('frontage_ft') or 0)} ft, {road}", False),
                ("Driveways", ("truck" if acc.get("truck_driveway") else "no truck") + (" + car" if acc.get("car_driveway") else ""), False),
                ("Truck / car route", f"{_int(acc.get('truck_route_ft'))} / {_int(acc.get('car_route_ft'))} ft", False),
                ("Level pad", pad, True),
                ("Earthwork, balanced", f"{_int(gr.get('cut_fill_cy'))} CY; relief {_int(gr.get('relief_ft'))} ft"
                 if gr.get("relief_ft") is not None else "no lidar", False),
                ("Heavy / light paving", f"{_int(qn.get('heavy_paving_sf'))} / {_int(qn.get('light_paving_sf'))} SF", False),
                ("Disturbed area", f"{_num(qn.get('disturbed_ac'))} ac", False),
                ("SF per net acre", _int(sd.get("sf_per_net_ac")), False)]
        st = gr.get("stepped_pad")
        if st:
            # the stepped pad a dock building is graded to (default behaviour's record), after the level pad's rows
            side = {"low": "court on the low side", "high": "court on the HIGH side", "level": "court on level ground"}[st["court_side"]]
            k = [r[0] for r in rows].index("Earthwork, balanced") + 1
            rows[k:k] = [("Stepped pad", f"floor {_int(st['floor_ft'])}, court {_int(st['court_ft'])} ft; {side}", True),
                         ("Earthwork, stepped", f"{_int(st['moved_cy'])} CY (cut {_int(st['cut_cy'])}, fill {_int(st['fill_cy'])})", False)]
        if sb:
            rows.insert(1, ("Building 2", f"{sb['sf']:,} SF, {sb['doors']} doors, {sb['trailers']} trailers", True))
        t, y2 = kv(x2, y2 + 2, half, rows)
        col.append(t)
        cy = max(y1, y2) + 10

    comp = (rec.get("compliance") or {}).get("rows") or []
    if comp:
        hd, cy = heading(COL_X, cy, f"PLAN AGAINST THE DRAFT ZONING VALUES ({str(rec['zoning']['name']).upper()}; NOT THE ADOPTED CODE)", COL_W)
        col.append(hd)
        rows = []
        for r in comp:
            margin = f"{r['margin_ft']:+d} ft" if r.get("margin_ft") is not None else ""
            rows.append([r["item"], r["required"], r["provided"], margin,
                         (str(r["status"]), ROW_STATUS_FILL.get(r["status"], C["ink"]), "bold")])
        t, cy = table(COL_X, cy, [("ITEM", 208, "start"), ("DRAFT VALUE", 150, "start"), ("PLAN", 196, "start"),
                                   ("MARGIN", 60, "end"), ("STATUS", COL_W - 614, "start")], rows)
        col.append(t)
        cy += 14

    alts = [x for x in rec.get("alternatives") or [] if isinstance(x, dict)]
    if alts:
        hd, cy = heading(COL_X, cy, "LAYOUTS COMPARED (THE PLAN DRAWN IS FIRST)", COL_W)
        col.append(hd)
        rows = []
        earth = any(x.get("earthwork_cy") is not None for x in alts[:LAYOUTS_SHOWN])
        for k, x in enumerate(alts[:LAYOUTS_SHOWN]):
            ok = x.get("geometry_ok")
            if ok:
                result = ("passes its checks", C["good"], "bold")
            else:
                why = ", ".join(str(f).replace("_", " ").lower() for f in x.get("blocking") or []) or (
                    "no route" if x.get("route_problem") else "fails its checks")
                result = (why, C["bad"], "normal")
            wt = "bold" if k == 0 else "normal"
            rows.append([(str(x.get("variant")), C["ink"], wt), (_int(x.get("sf")), C["ink"], wt), _int(x.get("doors")),
                         f"{_int(x.get('trailers'))} / {_int(x.get('trailers_target'))}", _int(x.get("sf_per_gross_ac")), result])
            if earth:
                # the stepped pad's earthwork of each layout (default behaviour's record), to weigh against its floor area
                rows[-1].insert(5, _int(x.get("earthwork_cy")) if x.get("earthwork_cy") is not None else "")
        cols = [("LAYOUT", 300, "start"), ("SF", 64, "end"), ("DOCKS", 50, "end"), ("TRAILERS", 70, "end"), ("SF/AC", 60, "end"),
                ("LAYOUT CHECKS", COL_W - 544, "start")]
        if earth:
            cols = [("LAYOUT", 270, "start"), *cols[1:5], ("EARTH CY", 66, "end"), ("  LAYOUT CHECKS", COL_W - 580, "start")]
        t, cy = table(COL_X, cy, cols, rows, size=10)
        col.append(t)
        basis = (rec.get("selection") or {}).get("basis")
        if basis:
            cy += 4
            for seg_ in _wrap(basis, COL_W - 8, 10.5):
                cy += 13.5
                col.append(text(COL_X + 4, cy, seg_, 10.5))
        cy += 12

    # ------------------------------------ under the plan: what holds the yield
    foot: list[str] = []
    ly = top + PLAN + 16
    ynotes = (a.get("yield") or {}).get("notes") or []
    if ynotes:
        hd, ly = heading(M, ly, "WHAT HOLDS THE YIELD", PLAN)
        foot.append(hd)
        ly -= 6
        for note in ynotes:
            for k, seg_ in enumerate(_wrap(note, PLAN - 22, 11)):
                ly += 14.5
                if k == 0:
                    foot.append(f'<rect x="{M + 4}" y="{ly - 7:.1f}" width="4" height="4" fill="{C["ink"]}"/>')
                foot.append(text(M + 16, ly, seg_, 11))
            ly += 3

    # ------------------------------------------------- conditions, full width
    yy = max(ly, cy) + 20
    conds = a.get("conditions") or []
    counts = a.get("counts") or {}
    summary = ", ".join(f"{counts.get(k, 0)} {lab}" for k, lab in (("blocking", "blocking"), ("gate", "gate"), ("site", "site-specific"),
                                                                   ("standing", "standard"), ("information", "for information")) if counts.get(k))
    hd, yy = heading(M, yy, "CONDITIONS: WHAT TO CHECK, WHEN, AND WHAT CLOSES IT" + (f"   ({summary})" if summary else ""), W - 2 * M)
    foot.append(hd)
    cols = [("#", 30, "start"), ("WHEN", 176, "start"), ("WORKSTREAM", 150, "start"), ("FINDING", 420, "start"),
            ("ACTION", 490, "start"), ("CLOSES WITH", W - 2 * M - 1266, "start")]
    rows = []
    for c in conds:
        kind = {"blocking": "BLOCKS THE PLAN", "gate": "GATE", "site": "", "standing": "every site", "information": "for information"}[c["kind"]]
        when = c["stage_label"] + (f" ({kind})" if kind else "")
        rows.append([(str(c["n"]), KIND_FILL[c["kind"]] if c["kind"] != "information" else C["mute"], "bold"),
                     (when, C["bad"] if c["kind"] == "blocking" else C["ink"], "bold" if c["kind"] in ("blocking", "gate") else "normal"),
                     c["workstream"], f"{c['flag']}: {c['finding']}", c["action"], c["closes_with"]])
    y_rows = yy
    t, yy = table(M, yy, cols, rows, size=10.5)
    foot.append(t)
    # a colour bar down the left of each row, by kind
    yk = y_rows + 5
    for c, row in zip(conds, rows):
        h = max(len(_wrap(cell[0] if isinstance(cell, tuple) else cell, w - 8, 10.5, isinstance(cell, tuple) and cell[2] == "bold"))
                for cell, (_, w, _) in zip(row, cols)) * 13.5 + 5
        foot.append(f'<rect x="{M - 5}" y="{yk:.1f}" width="3" height="{h:.1f}" fill="{KIND_FILL[c["kind"]]}"/>')
        yk += h
    # legend
    yy += 22
    hd, yy = heading(M, yy, "LEGEND", W - 2 * M)
    foot.append(hd)
    yy += 8
    legend = [(C["bldg"], "building; dock doors are the dark marks on its wall"), (C["office"], "office"),
              (C["court"], "truck court, truck roads and truck route"), (C["trailer"], "trailer stalls / drop yard"),
              (C["car"], "car parking and car route"), (C["pond"], "detention pond (grassed rim around)"),
              ("url(#gx)", "open ground"), (C["street"], "street along the GDOT road frontage; T/C driveways"),
              *([(C["ctx"], "neighbouring parcel (county parcel map)")] if "OTHER" in drawn_ctx else []),
              *([(C["ctx_res"], "neighbouring parcel classed residential (county land-use / tax class)")] if "RES" in drawn_ctx else []),
              ("contour", f"existing ground contour (lidar, every {every} ft; index labelled)" if every
               else "existing ground contour: none drawn (" + ("lidar incomplete" if dem_state in ("partial", "not_checked")
                                                               else "no lidar samples" if not getattr(d.dem, "elev", None)
                                                               else "level ground, or too few samples on the site") + ")"),
              ("url(#wx)", "floodplain, wetlands, easements, protected land"), ("url(#tx)", "transmission corridor (assumed width)"),
              ("url(#sx)", "steeper than 15% (lidar)"), ("url(#cx)", "test cut"),
              ("prop", "property line (county parcel map)"), ("dash", "paving setback line (draft; 25 ft from water)"),
              ("dashdot", "building setback line (draft)"), ("res", "boundary toward residential parcels (county land-use codes)"),
              ("dim", "dimension, ft")]
    lw = (W - 2 * M) / 4
    for k, (fill, txt) in enumerate(legend):
        x, y = M + (k % 4) * lw, yy + (k // 4) * 17
        if fill == "dash":
            foot.append(f'<path d="M{x:.1f},{y - 4:.1f} L{x + 18:.1f},{y - 4:.1f}" stroke="{C["env"]}" stroke-dasharray="6 4" stroke-width="1.5"/>')
        elif fill == "dashdot":
            foot.append(f'<path d="M{x:.1f},{y - 4:.1f} L{x + 18:.1f},{y - 4:.1f}" stroke="{C["benv"]}" stroke-dasharray="10 3 2 3" stroke-width="1.5"/>')
        elif fill == "res":
            foot.append(f'<path d="M{x:.1f},{y - 4:.1f} L{x + 18:.1f},{y - 4:.1f}" stroke="{C["res"]}" stroke-dasharray="9 5" stroke-width="3"/>')
        elif fill == "prop":
            foot.append(f'<path d="M{x - 6:.1f},{y - 4:.1f} L{x + 22:.1f},{y - 4:.1f}" stroke="{C["ink"]}" stroke-dasharray="20 4 3 4 3 4" stroke-width="2.2"/>')
        elif fill == "contour":
            foot.append(f'<path d="M{x:.1f},{y - 4:.1f} L{x + 18:.1f},{y - 4:.1f}" stroke="{C["contour"]}" stroke-dasharray="7 3" '
                        f'stroke-width="1"/>')
        elif fill == "dim":
            foot.append(f'<path d="M{x:.1f},{y - 4:.1f} L{x + 18:.1f},{y - 4:.1f}" stroke="{C["dim"]}" stroke-width="1.1"/>'
                        f'<path d="M{x:.1f},{y - 4:.1f} L{x + 6:.1f},{y - 6.6:.1f} L{x + 6:.1f},{y - 1.4:.1f} Z" fill="{C["dim"]}"/>'
                        f'<path d="M{x + 18:.1f},{y - 4:.1f} L{x + 12:.1f},{y - 6.6:.1f} L{x + 12:.1f},{y - 1.4:.1f} Z" fill="{C["dim"]}"/>')
        else:
            foot.append(f'<rect x="{x:.1f}" y="{y - 10:.1f}" width="18" height="10" fill="{fill}" stroke="{C["ink"]}" stroke-width="0.6"/>')
        foot.append(text(x + 26, y - 1, txt, 10.5))
    yy += ((len(legend) + 3) // 4) * 17 + 8
    # the basis of the estimates, then the disclaimer
    basis_lines = [f"Detention: {de.get('basis')}" if de.get("basis") else "", f"Grading: {gr.get('basis')}" if gr.get("basis") else "",
                   f"Takeoff: {qn.get('basis')}" if qn.get("basis") else "", f"Conditions: {a.get('basis')}",
                   (f"Contours: existing ground every {every} ft, traced from the lidar samples on a {d.dem.step:.0f} ft grid and "
                    "smoothed, stopping up to one grid step inside the property line (indicative; not a topographic survey)")
                   if every else "",
                   ("Neighbours: county parcel map; the tint is the planner's residential-edge test (residential land-use or tax "
                    "class, and agricultural where the draft rules buffer it), not zoning") if drawn_ctx else "", DISCLAIMER,
                   f"Sheet SP-{rec.get('key', '')}"]
    for line in basis_lines:
        for seg_ in _wrap(line, W - 2 * M, 10) if line else []:
            yy += 13
            foot.append(text(M, yy, seg_, 10, fill=C["mute"]))
    H = int(yy + 20)
    plan_el = "".join(x for x in out if x)
    head_el = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">'
               f'<title>{_esc(title)}</title><rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')
    return head_el + "".join(head) + plan_el + "".join(col) + "".join(foot) + "</svg>"


def _mid(edge) -> Any:
    """The midpoint of a feet-plane edge, as a shapely point."""
    return Point((edge[0][0] + edge[1][0]) / 2, (edge[0][1] + edge[1][1]) / 2)


def _lowered(rec: dict) -> bool:
    """Whether a draft height cap lowered the clear height."""
    flags = rec.get("flags") or {}
    return any(f.get("flag") == "HEIGHT_CAP" for lvl in (1, 2, "1", "2") for f in flags.get(lvl) or [])


def _cars_provided(pp: Any) -> int:
    """Car stalls a layout provides, by the rule the record uses for
    building 1: the requirement rounded up to five, within the rows'
    capacity."""
    if pp.car_cap < pp.cars_required:
        return pp.car_cap
    return max(min(pp.car_cap, math.ceil(pp.cars_required / 5) * 5), pp.cars_required)
