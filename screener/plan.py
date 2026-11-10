"""F12: a plain SVG site plan for each brief (reviews/34_screener_quick_wins).

Drawn per parcel from what the run already holds: the county parcel map
shape, the constrained ground (the parcel minus ``checks.map_checks``'
``_open_ground``: FEMA SFHA floodplain, NWI wetlands and water, NCED
easements, PAD-US protected land), the largest open part, and the F6
envelope (``sitefit.ENVELOPE_KEY``) when one fits, and the F7 detention
pond (``detention.POND_KEY``) when one was placed, and the F10 road
frontage (``frontage.LINE_KEY``, the parcel line within 50 ft of a GDOT
road centreline) when one was found. Plus a scale bar and a north arrow. Standard library only (shapely is already a dependency).

Report-only, like earthwork and the fit: ``report.write_all`` calls
``render`` after scores, flags and the sort, and ``render`` writes nothing
on the parcel. It never reads the owner, mailing or site address, so none
of them can reach the file (tests/test_plan.py checks).

Coordinates: one ``LocalProjection.around(geometry)`` per parcel (the same
plane sitefit fits in), metres -> feet, y flipped for SVG, north up.
Numbers are written with one fixed format, so the same parcel gives the
same bytes.
"""

from __future__ import annotations

import math
import re
import textwrap
from html import escape
from typing import Any

import shapely
from shapely.errors import GEOSException
from shapely.geometry import Polygon

from . import detention, frontage, sitefit
from .geo import FT_PER_M, LocalProjection, acres

SIZE = 600          # plan square, px
MARGIN = 20
LINE = 18           # legend line height, px
WRAP = 84           # legend characters per line (12 px sans-serif fits the width)
SCALE_GAP = 24      # px between the plan square and the legend, holds the scale bar
WIDTH = SIZE + 2 * MARGIN
MIN_SPAN_FT = 10.0
_XML_BAD = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")

PARCEL_FILL, OPEN_FILL, CONSTRAINED_FILL = "#f4f1ea", "#cfe8c4", "#9cc3e0"
ENVELOPE_FILL, ENVELOPE_STROKE, INK = "#f2c9a0", "#b5534a", "#222222"
POND_FILL, POND_STROKE = "#7fc4bd", "#1f6f68"
FRONTAGE_STROKE = "#d9480f"


def _n(v: float) -> str:
    """One decimal, never "-0.0": the stable number format."""
    s = f"{v:.1f}"
    return "0.0" if s == "-0.0" else s


def _scale_ft(span_ft: float) -> float:
    """The largest 1/2/5 x 10^k length at most 30% of the plan's span."""
    target = span_ft * 0.3
    k = math.floor(math.log10(target))
    for m in (5, 2, 1):
        if m * 10 ** k <= target:
            return float(m * 10 ** k)
    return float(10 ** k)


class _Frame:
    """Feet in the local plane -> SVG px (y flipped, plan centred)."""

    def __init__(self, bounds_ft: tuple[float, float, float, float]):
        minx, miny, maxx, maxy = bounds_ft
        self.span = max(maxx - minx, maxy - miny)
        self.s = SIZE / self.span
        self.ox = MARGIN + (SIZE - (maxx - minx) * self.s) / 2.0
        self.oy = MARGIN + (SIZE - (maxy - miny) * self.s) / 2.0
        self.minx, self.maxy = minx, maxy

    def xy(self, x: float, y: float) -> str:
        return f"{_n(self.ox + (x - self.minx) * self.s)},{_n(self.oy + (self.maxy - y) * self.s)}"

    def path(self, parts: list[Polygon]) -> str:
        rings = []
        for q in parts:
            for ring in (q.exterior, *q.interiors):
                pts = [self.xy(x, y) for x, y, *_ in ring.coords[:-1]]
                if len(pts) >= 3:
                    rings.append("M" + " L".join(pts) + " Z")
        return " ".join(rings)

    def lines(self, g) -> str:
        """Open polylines (no Z) for the line parts of g."""
        parts = []
        for ln in getattr(g, "geoms", [g]):
            if ln.geom_type == "LineString":
                pts = [self.xy(x, y) for x, y, *_ in ln.coords]
                if len(pts) >= 2:
                    parts.append("M" + " L".join(pts))
            elif hasattr(ln, "geoms"):
                parts.append(self.lines(ln))
        return " ".join(x for x in parts if x)


def _feet(g, proj: LocalProjection):
    return shapely.transform(proj(g), lambda c: c * FT_PER_M)


def _shape(d: str, fill: str, stroke: str = "none", width: float = 0.0,
           opacity: float = 1.0, cls: str = "") -> str:
    if not d:
        return ""
    return (f'<path class="{cls}" d="{d}" fill="{fill}" fill-rule="evenodd" '
            f'fill-opacity="{_n(opacity)}" stroke="{stroke}" stroke-width="{_n(width)}"/>')


def _text(x: float, y: float, s: str, size: int = 12, anchor: str = "start",
          weight: str = "normal") -> str:
    return (f'<text x="{_n(x)}" y="{_n(y)}" font-size="{size}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{_esc(s)}</text>')


def _esc(s: str) -> str:
    """XML-escaped, with characters XML 1.0 cannot hold (control bytes,
    lone surrogates) replaced by "?" so the file always parses."""
    return escape(_XML_BAD.sub("?", s), quote=True)


def _swatch(y: float, fill: str, stroke: str = INK) -> str:
    return (f'<rect x="{MARGIN}" y="{_n(y - 10)}" width="14" height="10" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1.0"/>')


def _fit_text(f: sitefit.Fit | None) -> str:
    if f is None:
        return "Building fit: not checked (fit off)"
    if f.state == sitefit.FITS:
        return (f"Envelope: {f.label} {f.length_ft:,.0f} x {f.width_ft:,.0f} ft building, "
                f"{f.building_sf:,} SF (+ truck court + trailer parking)")
    if f.state == sitefit.NONE_FOUND:
        return "Building fit: none found (a lower bound, not proof nothing fits)"
    return f"Building fit: not checked ({f.reason})"


def _pond_text(d: detention.Pond) -> str:
    if d.state == detention.FITS:
        where = ("beside the envelope" if d.beside_envelope else
                 "(no building envelope placed)")
        return (f"Detention pond {detention.POND['L']} x {detention.POND['W']} ft {where}: "
                "rough allowance, no hydrology")
    if d.state == detention.NONE_FOUND:
        return "Detention pond: no pond room found (a lower bound)"
    return f"Detention pond: not checked ({d.reason})"


def render(p: dict[str, Any]) -> tuple[str | None, str]:
    """(svg text, "") or (None, why not drawn). Reads only geometry,
    _open_ground, constrained_pct, rank, parcel_id, the F6 keys, the F7
    pond keys and the F10 frontage keys, and writes nothing on p."""
    g = p.get("geometry")
    if g is None or g.is_empty or not sitefit.polygon_parts(g):
        return None, "no parcel map shape"
    try:
        return _render(p, g)
    except GEOSException as exc:
        return None, f"geometry fault ({str(exc)[:80]})"


def _render(p: dict[str, Any], g) -> tuple[str | None, str]:
    proj = LocalProjection.around(g)
    parcel = sitefit.polygon_parts(_feet(g, proj))
    minx, miny, maxx, maxy = (min(q.bounds[i] for q in parcel) if i < 2 else
                              max(q.bounds[i] for q in parcel) for i in range(4))
    if max(maxx - minx, maxy - miny) < MIN_SPAN_FT:
        return None, "parcel map shape too small to draw"
    fr = _Frame((minx, miny, maxx, maxy))

    has_open = "_open_ground" in p and p["_open_ground"] is not None
    og = p.get("_open_ground")
    constrained, largest, largest_ac = [], [], 0.0
    if has_open:
        whole = og is g and (p.get("constrained_pct") or 0) > 0   # map_checks' fallback
        if whole:
            constrained = parcel
        else:
            og_ft = _feet(og, proj)
            constrained = sitefit.polygon_parts(_feet(g, proj).difference(og_ft))
            # Measured in metres, as sitefit does, so the legend matches the brief.
            open_m = sitefit.polygon_parts(proj(og))
            if open_m:
                big = max(open_m, key=lambda q: (q.area, q.bounds))
                largest = sitefit.polygon_parts(shapely.transform(big, lambda c: c * FT_PER_M))
                largest_ac = acres(big)

    f: sitefit.Fit | None = p.get("_envelope_detail")
    env = p.get(sitefit.ENVELOPE_KEY)
    env_parts = sitefit.polygon_parts(_feet(env, proj)) if env is not None else []

    d: detention.Pond | None = p.get(detention.DETAIL_KEY)
    pond = p.get(detention.POND_KEY)
    pond_parts = sitefit.polygon_parts(_feet(pond, proj)) if pond is not None else []

    title = f"Site plan #{p.get('rank', '')} parcel {p.get('parcel_id', '')}"
    out = [_shape(fr.path(parcel), PARCEL_FILL, cls="parcel-fill"),
           _shape(fr.path(largest), OPEN_FILL, cls="largest-open"),
           _shape(fr.path(constrained), CONSTRAINED_FILL, opacity=0.8, cls="constrained")]
    for q in env_parts:
        out.append(_shape(fr.path([q]), ENVELOPE_FILL, ENVELOPE_STROKE, 2.0, 0.8, "envelope"))
    for q in pond_parts:
        out.append(_shape(fr.path([q]), POND_FILL, POND_STROKE, 2.0, 0.9, "pond"))
    out.append(_shape(fr.path(parcel), "none", INK, 1.5, cls="parcel"))
    fd: frontage.Frontage | None = p.get(frontage.DETAIL_KEY)
    fline = p.get(frontage.LINE_KEY)
    if fline is not None and not fline.is_empty:
        d_line = fr.lines(_feet(fline, proj))
        if d_line:
            out.append(f'<path class="frontage" d="{d_line}" fill="none" '
                       f'stroke="{FRONTAGE_STROKE}" stroke-width="5.0" stroke-linecap="round"/>')
    if f is not None and f.state == sitefit.FITS and env_parts:
        c = env_parts[0].centroid
        cx, cy = (float(v) for v in fr.xy(c.x, c.y).split(","))
        label = f"{f.label} {f.building_sf:,} SF"
        half = len(label) * 3.5                     # about half the width at 11 px bold
        cx = min(max(cx, MARGIN + half), WIDTH - MARGIN - half)
        out.append(_text(cx, cy + 4, label, 11, "middle", "bold"))

    # North arrow (the local plane is north up) and a scale bar.
    ax, ay = WIDTH - MARGIN - 12, MARGIN + 6
    out.append(f'<rect x="{_n(ax - 14)}" y="{_n(ay - 4)}" width="28" height="48" '
               f'fill="#ffffff" fill-opacity="0.8"/>')
    out.append(f'<path class="north" d="M{_n(ax)},{_n(ay)} L{_n(ax + 8)},{_n(ay + 26)} '
               f'L{_n(ax)},{_n(ay + 20)} L{_n(ax - 8)},{_n(ay + 26)} Z" fill="{INK}"/>')
    out.append(_text(ax, ay + 40, "N", 12, "middle", "bold"))
    bar_ft = _scale_ft(fr.span)
    bx, by, blen = MARGIN, MARGIN + SIZE + SCALE_GAP - 6, bar_ft * fr.s
    out.append(f'<path class="scale" d="M{_n(bx)},{_n(by - 5)} L{_n(bx)},{_n(by)} '
               f'L{_n(bx + blen)},{_n(by)} L{_n(bx + blen)},{_n(by - 5)}" fill="none" '
               f'stroke="{INK}" stroke-width="2.0"/>')
    out.append(_text(bx + blen + 6, by, f"{bar_ft:,.0f} ft", 11))

    y = MARGIN * 2 + SIZE + SCALE_GAP + 6
    gis = acres(proj(g))
    rows = [(PARCEL_FILL, f"Parcel (county map) {gis:,.1f} ac")]
    if not has_open:
        rows.append((CONSTRAINED_FILL, "Constraints: not checked"))
    else:
        pct = p.get("constrained_pct") or 0.0
        rows.append((CONSTRAINED_FILL, f"Constrained {pct:.0f}%: floodplain, wetlands and "
                                       "water, easements, protected land"))
    if largest:
        rows.append((OPEN_FILL, f"Largest open part {largest_ac:,.1f} ac"))
    else:
        rows.append((OPEN_FILL, "Largest open part: none" if has_open else
                     "Largest open part: not checked"))
    rows.append((ENVELOPE_FILL, _fit_text(f)))
    if d is not None:     # absent only when report.write_all gets no F7 result
        rows.append((POND_FILL, _pond_text(d)))
    if fd is not None:    # absent only when report.write_all gets no F10 result
        rows.append((FRONTAGE_STROKE, frontage.plan_text(fd)))
    strokes = {ENVELOPE_FILL: ENVELOPE_STROKE, POND_FILL: POND_STROKE,
               FRONTAGE_STROKE: FRONTAGE_STROKE}
    for fill, label in rows:
        out.append(_swatch(y, fill, strokes.get(fill, INK)))
        for line in textwrap.wrap(label, WRAP) or [""]:
            out.append(_text(MARGIN + 20, y, line))
            y += LINE
    out.append(_text(MARGIN, y,
                     "North up. Steep ground not shown. A screening sketch, not a survey.", 11))
    height = int(y + MARGIN)
    head = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
            f'viewBox="0 0 {WIDTH} {height}" font-family="sans-serif">',
            f"<title>{_esc(title)}</title>",
            f'<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="#ffffff"/>']
    return "\n".join(x for x in head + out + ["</svg>"] if x) + "\n", ""
