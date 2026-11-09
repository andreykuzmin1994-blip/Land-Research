"""F12: a plain SVG site plan for each brief (reviews/34_screener_quick_wins).

Drawn per parcel from what the run already holds: the county parcel map
shape, the constrained ground (the parcel minus ``checks.map_checks``'
``_open_ground``: FEMA SFHA floodplain, NWI wetlands and water, NCED
easements, PAD-US protected land), the largest open part, and the F6
envelope (``sitefit.ENVELOPE_KEY``) when one fits. Plus a scale bar and a
north arrow. Standard library only (shapely is already a dependency).

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
from html import escape
from typing import Any

import shapely
from shapely.errors import GEOSException
from shapely.geometry import Polygon

from . import sitefit
from .geo import FT_PER_M, SQM_PER_ACRE, LocalProjection, acres

SIZE = 600          # plan square, px
MARGIN = 20
LINE = 18           # legend line height, px
LEGEND_LINES = 5
WIDTH = SIZE + 2 * MARGIN
HEIGHT = SIZE + 2 * MARGIN + LEGEND_LINES * LINE + 8
MIN_SPAN_FT = 10.0

PARCEL_FILL, OPEN_FILL, CONSTRAINED_FILL = "#f4f1ea", "#cfe8c4", "#9cc3e0"
ENVELOPE_FILL, ENVELOPE_STROKE, INK = "#f2c9a0", "#b5534a", "#222222"


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
            f'font-weight="{weight}">{escape(s, quote=True)}</text>')


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


def render(p: dict[str, Any]) -> tuple[str | None, str]:
    """(svg text, "") or (None, why not drawn). Reads only geometry,
    _open_ground, constrained_pct, rank, parcel_id and the F6 keys, and
    writes nothing on p."""
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
    constrained, largest = [], []
    if has_open:
        whole = og is g and (p.get("constrained_pct") or 0) > 0   # map_checks' fallback
        if whole:
            constrained = parcel
        else:
            og_ft = _feet(og, proj)
            open_ft = sitefit.polygon_parts(og_ft)
            constrained = sitefit.polygon_parts(_feet(g, proj).difference(og_ft))
            if open_ft:
                largest = [max(open_ft, key=lambda q: (q.area, q.bounds))]

    f: sitefit.Fit | None = p.get("_envelope_detail")
    env = p.get(sitefit.ENVELOPE_KEY)
    env_parts = sitefit.polygon_parts(_feet(env, proj)) if env is not None else []

    title = f"Site plan #{p.get('rank', '')} parcel {p.get('parcel_id', '')}"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
           f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="sans-serif">',
           f"<title>{escape(title, quote=True)}</title>",
           f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>',
           _shape(fr.path(parcel), PARCEL_FILL, cls="parcel-fill"),
           _shape(fr.path(largest), OPEN_FILL, cls="largest-open"),
           _shape(fr.path(constrained), CONSTRAINED_FILL, opacity=0.8, cls="constrained")]
    for q in env_parts:
        out.append(_shape(fr.path([q]), ENVELOPE_FILL, ENVELOPE_STROKE, 2.0, 0.8, "envelope"))
    out.append(_shape(fr.path(parcel), "none", INK, 1.5, cls="parcel"))
    if f is not None and f.state == sitefit.FITS and env_parts:
        c = env_parts[0].centroid
        cx, cy = (float(v) for v in fr.xy(c.x, c.y).split(","))
        out.append(_text(cx, cy + 4, f"{f.label} {f.building_sf:,} SF", 11, "middle", "bold"))

    # North arrow (the local plane is north up) and a scale bar.
    ax, ay = WIDTH - MARGIN - 12, MARGIN + 6
    out.append(f'<path class="north" d="M{_n(ax)},{_n(ay)} L{_n(ax + 8)},{_n(ay + 26)} '
               f'L{_n(ax)},{_n(ay + 20)} L{_n(ax - 8)},{_n(ay + 26)} Z" fill="{INK}"/>')
    out.append(_text(ax, ay + 40, "N", 12, "middle", "bold"))
    bar_ft = _scale_ft(fr.span)
    bx, by, blen = MARGIN, MARGIN + SIZE - 6, bar_ft * fr.s
    out.append(f'<path class="scale" d="M{_n(bx)},{_n(by - 5)} L{_n(bx)},{_n(by)} '
               f'L{_n(bx + blen)},{_n(by)} L{_n(bx + blen)},{_n(by - 5)}" fill="none" '
               f'stroke="{INK}" stroke-width="2.0"/>')
    out.append(_text(bx + blen + 6, by, f"{bar_ft:,.0f} ft", 11))

    y = MARGIN * 2 + SIZE + 6
    gis = acres(proj(g))
    rows = [(PARCEL_FILL, f"Parcel (county map) {gis:,.1f} ac")]
    if not has_open:
        rows.append((CONSTRAINED_FILL, "Constraints: not checked"))
    else:
        pct = p.get("constrained_pct") or 0.0
        rows.append((CONSTRAINED_FILL, f"Constrained {pct:.0f}%: floodplain, wetlands and "
                                       "water, easements, protected land"))
    if largest:
        rows.append((OPEN_FILL, f"Largest open part {largest[0].area / FT_PER_M ** 2 / SQM_PER_ACRE:,.1f} ac"))
    else:
        rows.append((OPEN_FILL, "Largest open part: none" if has_open else
                     "Largest open part: not checked"))
    rows.append((ENVELOPE_FILL, _fit_text(f)))
    for i, (fill, label) in enumerate(rows):
        yy = y + i * LINE
        out.append(_swatch(yy, fill, ENVELOPE_STROKE if fill == ENVELOPE_FILL else INK))
        out.append(_text(MARGIN + 20, yy, label))
    out.append(_text(MARGIN, y + len(rows) * LINE,
                     "North up. Steep ground not shown. A screening sketch, not a survey.", 11))
    out.append("</svg>")
    return "\n".join(x for x in out if x) + "\n", ""
