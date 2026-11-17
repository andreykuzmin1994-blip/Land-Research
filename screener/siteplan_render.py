"""SVG drawing of one site plan from ``siteplan`` (planner v4).

Self-contained: it draws only what ``siteplan.plan_site`` returns (a
``Drawing`` in the site's feet plane, plus the record) and imports only
pure helpers (``plan._esc`` for text, ``sitefit.polygon_parts``). It is
not the run-brief renderer ``plan.render`` and does not change it (R5).

Safe output (R3): every text goes through ``plan._esc`` (XML-escaped,
quotes included, characters XML cannot hold replaced), and the drawing
uses only plain shapes: no script, no href / xlink:href, no event
attributes, no style element, and ``url()`` only as ``url(#id)`` for the
three hatch patterns defined in the file itself. Numbers have one fixed
format, so the same plan gives the same bytes.

Every plan says DRAFT (the zoning values are draft) and, when an input
was not checked, INCOMPLETE with the list of missing inputs.
"""

from __future__ import annotations

import math
import textwrap

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from . import sitefit
from .plan import _esc
from .siteplan import RIM, Drawing, rnd

C = {"bg": "#ffffff", "parcel": "#f4f1ea", "ink": "#1f2a30", "env": "#7a8a94", "cons": "#9cc3e0", "trans": "#e8b04b", "cut": "#c92a2a",
     "steep": "#b07d4f", "bldg": "#e9e4da", "bldg_s": "#4a3f35", "office": "#c9b8a0", "court": "#c4c9cd", "apron": "#b8bec3",
     "trailer": "#a7adb3", "car": "#dfe3f1", "road": "#bfc5ca", "pond": "#8ccfc4", "pond_s": "#2a7f74", "drive": "#d9480f", "row": "#d9480f"}
W, PLAN, M = 1200, 780, 20
DISCLAIMER = ("Screening test fit from public data: not a survey, civil design or zoning determination. Zoning values are draft; "
              "detention is a +/-30% estimate; routes, fire access and grading are indicative.")


def render(d: Drawing, rec: dict) -> str:
    parcel, P, constraints, trans, cut, steep, front_ft = d.parcel, d.P, d.constraints, d.trans, d.cut, d.steep, d.front_ft
    p, o, second, title = d.p, d.o, d.second, d.title
    minx, miny, maxx, maxy = parcel.bounds
    span = max(maxx - minx, maxy - miny) * 1.04
    s = PLAN / span
    ox = M + (PLAN - (maxx - minx) * s) / 2
    oy = M + (PLAN - (maxy - miny) * s) / 2
    X = lambda x: ox + (x - minx) * s
    Y = lambda y: oy + (maxy - y) * s

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
            if ln.geom_type == "LineString" and len(ln.coords) > 1:
                out.append("M" + " L".join(f"{X(x):.1f},{Y(y):.1f}" for x, y, *_ in ln.coords))
            elif hasattr(ln, "geoms"):
                out.append(lpath(ln))
        return " ".join(out)

    def poly(g, fill, stroke="none", sw=0, op=1.0, extra=""):
        dd = path(g)
        return f'<path d="{dd}" fill="{fill}" fill-opacity="{op}" stroke="{stroke}" stroke-width="{sw}" fill-rule="evenodd" {extra}/>' if dd else ""
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
        for g in pp.roads:
            out.append(poly(g, C["road"]))
        for g in pp.courts:
            out.append(poly(g, C["court"]))
        for g in pp.rows:
            out.append(poly(g, C["trailer"]))
        for g in pp.cars:
            out.append(poly(g, C["car"]))
        if pp.ticks:
            out.append(f'<path d="{lpath(unary_union(pp.ticks))}" stroke="#ffffff" stroke-width="0.8" fill="none"/>')
        out.append(poly(pp.building, C["bldg"], C["bldg_s"], 1.6))
        for g in pp.offices:
            out.append(poly(g, C["office"], C["bldg_s"], 0.6))
        if pp.doors_geom:
            out.append(f'<path d="{lpath(unary_union(pp.doors_geom))}" stroke="{C["bldg_s"]}" stroke-width="1.4" fill="none"/>')
        for g in oo["ponds"]:
            out.append(poly(g, "#d7ece8", C["pond_s"], 0.8))
            out.append(poly(g.buffer(-RIM), C["pond"], C["pond_s"], 1.2))
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
    bar = 10 ** math.floor(math.log10(span * 0.25))
    bar = bar * (5 if bar * 5 <= span * 0.3 else 2 if bar * 2 <= span * 0.3 else 1)
    out.append(f'<path d="M{M},{M+PLAN+8} L{M+bar*s:.1f},{M+PLAN+8}" stroke="{C["ink"]}" stroke-width="3"/><text x="{M+bar*s+6:.1f}" y="{M+PLAN+12}" font-size="11">{bar:,.0f} ft</text>')
    tx, yy = M + PLAN + 22, M + 4
    b, tr, ca, cv, de, acc, gr = rec["building"], rec["trailers"], rec["cars"], rec["coverage"], rec["detention"], rec["access"], rec["grading"]
    states = (rec.get("inputs") or {}).get("states") or {}
    missing = sorted(k for k, v in states.items() if v.get("state") in ("not_checked", "partial"))
    if rec.get("feasible") is None:
        head = "INCOMPLETE: inputs not checked. "
    elif rec["hypothetical"]:
        head = "HYPOTHETICAL: no road frontage. "
    elif rec.get("feasible") is False:
        head = "NOT FEASIBLE (see !! flags). "
    else:
        head = ""
    L = [("h", head + title),
         ("t", rec["configuration"] + (" (operator prototype)" if rec["operator_prototype"] else " (market depth, not an operator prototype)")),
         ("f2", f"DRAFT zoning values ({rec['zoning']['name']}): setbacks, buffers, parking codes and height caps are screening placeholders"),
         ("b", f"{b['sf']:,} SF   {int(b['L_ft'])} x {int(b['D_ft'])} ft   {b['clear_height_ft']} ft clear (assumed)")]
    if missing:
        L.insert(1, ("f1", "Not checked: " + ", ".join(missing) + " (floor area not counted)"))
    if rec.get("gated"):
        # Worded from total_sf: the default rule does not count a gated
        # plan, the prototype rule (--prototype-behaviour) does.
        counted = bool(rec.get("total_sf"))
        L.insert(1, ("f1", "Gated: " + ", ".join(rec["gated"]) + (" (entitlement gate; floor area counted)" if counted else " (floor area not counted)")))
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
            body.append(f'<text x="{tx}" y="{yy:.0f}" font-size="{size}" font-weight="{weight}" fill="{col}">{_esc(seg)}</text>')
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
        body.append(f'<text x="{tx+26}" y="{yy-1}" font-size="11">{_esc(txt)}</text>')
    yy += 20
    for seg in textwrap.wrap(DISCLAIMER, 74):
        body.append(f'<text x="{tx}" y="{yy:.0f}" font-size="10.5" fill="#5d676e">{_esc(seg)}</text>')
        yy += 13
    H = int(max(M + PLAN + 30, yy + 16))
    head_el = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="sans-serif"><title>{_esc(title)}</title><rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>'
    return head_el + "".join(x for x in out if x) + "".join(body) + "</svg>"
