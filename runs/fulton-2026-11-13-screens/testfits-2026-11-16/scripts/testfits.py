"""Scenario test fits for selected Fulton leads, using the screener's own
constraint layers, rectangle search, pond placement and plan renderer.
Reads repo code; writes only under OUT. Run from the repo root:
    python3 SCRATCH/testfits.py OUT_DIR
"""
import json, math, sys
from pathlib import Path

import shapely
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely.ops import unary_union

sys.path.insert(0, ".")
from screener import arcgis, layers, sitefit, detention, plan, checks
from screener.geo import LocalProjection, acres, FT_PER_M, esri_to_shape, tile_envelopes, expand

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
RUN = Path("/home/user/screener-data/runs/fulton-2026-11-13-screens")
PARCELS = "https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11"
ATL_ZONING = "https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/MapServer/22"
arcgis.cache_days = 7.0

ranked = {f["properties"]["parcel_id"]: f for f in json.load(open(RUN / "ranked.geojson"))["features"]}

def ranked_geom(pid):
    return shape(ranked[pid]["geometry"])

def county(where):
    d = arcgis.request_json(PARCELS + "/query", {"where": where, "outFields": "ParcelID,Address,Owner,LandAcres,LUCode,ClassCode",
                                                 "returnGeometry": "true", "outSR": 4326})
    return [(f["attributes"], esri_to_shape(f["geometry"])) for f in d.get("features", [])]

def county_box(env):
    d = arcgis.request_json(PARCELS + "/query", {"geometry": ",".join(map(str, env)), "geometryType": "esriGeometryEnvelope",
                                                 "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                                                 "outFields": "ParcelID,Address,Owner,LandAcres,LUCode,ClassCode",
                                                 "returnGeometry": "true", "outSR": 4326})
    return [(f["attributes"], esri_to_shape(f["geometry"])) for f in d.get("features", [])]

# ---- constraint layers (the four the screen subtracts) --------------------
_layer_cache = {}
def constraints(g):
    tiles = tile_envelopes([g.bounds])
    region = expand(g.bounds, 0.02)
    proj = LocalProjection.around(g)
    total = proj(g).area or 1.0
    got = []
    for key in ("flood", "wetlands", "easements", "protected"):
        idx = layers.fetch(layers.LAYERS[key], tiles, region)
        if key == "wetlands":
            a, _, _ = checks._overlap(g, proj, total, idx, keep=lambda a: a.get("WETLAND_TYPE") not in checks.WATER_TYPES)
            b, _, _ = checks._overlap(g, proj, total, idx, keep=lambda a: a.get("WETLAND_TYPE") in checks.WATER_TYPES)
            got += [x for x in (a, b) if x is not None]
        else:
            x, _, _ = checks._overlap(g, proj, total, idx)
            if x is not None: got.append(x)
    return unary_union(got) if got else None

# ---- fitting ---------------------------------------------------------------
import textwrap
from shapely.geometry import mapping

def to_wgs(gm, pj):
    def inv(c):
        o = c.copy(); o[:, 0] = o[:, 0] / pj.kx + pj.lon0; o[:, 1] = o[:, 1] / pj.ky + pj.lat0; return o
    return shapely.transform(gm, inv)

def buffer_ft(geom, ft, pj):
    return to_wgs(pj(geom).buffer(ft / FT_PER_M), pj)

# ---- candidate buildings ----------------------------------------------------
def lattice(lo, hi, step):
    out, x = [], float(lo)
    while x <= hi + 1e-9:
        out.append(x); x += step
    return out

OPERATOR = []   # the operator's prototypes, as sitefit groups them (cross first)
for key, label in sitefit.GROUPS:
    p = sitefit.PROTOTYPES[key]
    OPERATOR.append({"name": label, "label": label, "W": float(p["W"]), "depth": sitefit.envelope_depth(p),
                     "lattice": sitefit._lattice(p, sitefit.STEP_FT), "operator": True,
                     "extra": "+ truck court + trailer parking" + (" both sides" if key == "cross" else "")})
SMALL = [   # illustrative test buildings, NOT the operator's prototypes
    {"name": "front-load 200 ft deep, 130 ft truck court + 55 ft trailer row", "W": 200, "depth": 385, "extra": "+ 130 ft truck court + 55 ft trailer row"},
    {"name": "front-load 160 ft deep, 130 ft truck court + 55 ft trailer row", "W": 160, "depth": 345, "extra": "+ 130 ft truck court + 55 ft trailer row"},
    {"name": "front-load 160 ft deep, 130 ft truck court, no trailer row", "W": 160, "depth": 290, "extra": "+ 130 ft truck court, no trailer row"},
    {"name": "small-bay flex 120 ft deep, 120 ft court", "W": 120, "depth": 240, "extra": "+ 120 ft court, no trailer row"},
]
SHORT = {385: "front-load 200 ft deep + trailer row (test)", 345: "front-load 160 ft deep + trailer row (test)",
         290: "front-load 160 ft deep, no trailer row (test)", 240: "small-bay flex 120 ft deep (test)"}
for v in SMALL:
    v.update({"label": SHORT[v["depth"]], "lattice": lattice(200, 1200, 20), "operator": False})
CAP = 1200

def parts_of(g, open_g, setback=sitefit.DEFAULT_SETBACK_FT):
    pj = LocalProjection.around(g)
    open_m = sitefit.polygon_parts(pj(open_g))
    if not open_m:
        return pj, []
    inner = pj(g).buffer(-setback / FT_PER_M)
    reg = shapely.intersection(MultiPolygon(open_m) if len(open_m) > 1 else open_m[0], inner)
    parts = [q for q in sitefit.polygon_parts(sitefit._to_feet(reg)) if q.area > 20000]
    if sitefit._vertices(parts) > 400:
        parts = [q for q in sitefit.polygon_parts(shapely.union_all(parts).buffer(-2).simplify(1.0, preserve_topology=True)) if q.area > 20000]
    return pj, parts

def place(g, open_g, c, cap=None):
    """Largest length of candidate c (<= cap) on open_g: a Fit, or None."""
    pj, parts = parts_of(g, open_g)
    if not parts:
        return None
    lat = [x for x in c["lattice"] if cap is None or x <= cap]
    if not lat:
        return None
    rings, origin = sitefit.normalise(parts)
    got = sitefit.fit_lengths(rings, [(c["depth"], lat)]).get(c["depth"])
    if not got:
        return None
    L, pl = got
    env = to_wgs(sitefit._to_feet(Polygon(sitefit.envelope_corners(L, c["depth"], pl, origin))).buffer(0), pj) if False else \
          sitefit._to_wgs84(Polygon(sitefit.envelope_corners(L, c["depth"], pl, origin)), pj)
    f = sitefit.Fit(sitefit.FITS, label=c["label"], length_ft=L, width_ft=c["W"], building_sf=int(round(L * c["W"])), envelope=env)
    f._extra = c["extra"]; f._cand = c
    return f

def pond_first(g, open_g, cpct, c):
    """Place the pond on the open ground first, then the largest building of c on what is left."""
    pond = detention.place_pond(g, open_g, cpct, sitefit.Fit(sitefit.NONE_FOUND), None)
    if pond.state != detention.FITS or pond.pond is None:
        return None, None
    f = place(g, open_g.difference(pond.pond), c)
    return (f, pond) if f is not None else (None, None)

def place_with_pond(g, open_g, cpct, c):
    """(max fit ignoring the pond, largest fit that leaves room for the pond, its pond):
    the better of shortening the building until the pond fits beside it, and
    placing the pond first. Both are lower bounds."""
    top, fp, pond = _shorten(g, open_g, cpct, c)
    f2, p2 = pond_first(g, open_g, cpct, c)
    if f2 is not None and (fp is None or f2.building_sf > fp.building_sf):
        fp, pond = f2, p2
    return top, fp, pond

def _shorten(g, open_g, cpct, c):
    top = place(g, open_g, c)
    if top is None:
        return None, None, None
    cap = top.length_ft
    while cap is not None:
        f = place(g, open_g, c, cap)
        if f is None:
            break
        pond = detention.place_pond(g, open_g, cpct, f, f.envelope)
        if pond.state == detention.FITS:
            return top, f, pond
        smaller = [x for x in c["lattice"] if x < f.length_ft]
        cap = smaller[-1] if smaller else None
    return top, None, None

def best_of(g, open_g, cpct, cands):
    rows, best, best_any = [], None, None
    for c in cands:
        top, fp, pond = place_with_pond(g, open_g, cpct, c)
        rows.append({"building": c["name"], "operator_prototype": c["operator"],
                     "max_length_ft": top.length_ft if top else None, "max_sf": top.building_sf if top else None,
                     "at_cap": bool(top and top.length_ft >= max(c["lattice"])),
                     "with_pond_length_ft": fp.length_ft if fp else None, "with_pond_sf": fp.building_sf if fp else None})
        if fp and (best is None or (c["operator"] and not best[0]._cand["operator"]) or
                   (c["operator"] == best[0]._cand["operator"] and fp.building_sf > best[0].building_sf)):
            best = (fp, pond); fp._top = top
        if top and (best_any is None or top.building_sf > best_any.building_sf):
            best_any = top
    return rows, best, best_any

# ---- plan drawing ------------------------------------------------------------
def add_legend(svg, rows):
    import re as _re
    m = _re.search(r'height="(\d+)"', svg); H = int(m.group(1))
    foot = _re.search(r'<text x="[^"]*" y="([^"]*)"[^>]*>North up\. Steep ground not shown\. A screening sketch, not a survey\.</text>', svg)
    y = float(foot.group(1))
    svg = svg.replace(foot.group(0), "")
    out = []
    for fill, label in rows:
        out.append(plan._swatch(y, fill, "#c92a2a" if fill.startswith("url") else plan.INK))
        for line in textwrap.wrap(label, plan.WRAP) or [""]:
            out.append(plan._text(plan.MARGIN + 20, y, line)); y += plan.LINE
    out.append(plan._text(plan.MARGIN, y, "North up. Steep ground not shown. A screening sketch, not a survey.", 11))
    H2 = int(y + plan.MARGIN)
    svg = svg.replace(f'height="{H}" viewBox="0 0 {plan.WIDTH} {H}"', f'height="{H2}" viewBox="0 0 {plan.WIDTH} {H2}"', 1)
    svg = svg.replace(f'<rect x="0" y="0" width="{plan.WIDTH}" height="{H}"', f'<rect x="0" y="0" width="{plan.WIDTH}" height="{H2}"', 1)
    return svg.replace("</svg>", "\n".join(out) + "\n</svg>")

HATCH = ('<defs><pattern id="hx" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
         '<line x1="0" y1="0" x2="0" y2="8" stroke="#c92a2a" stroke-width="2"/></pattern></defs>')

def draw(sid, g, open_g, base_pct, fit1, pond, label, extra_excl=None, extra_rows=(), fit2=None, pond2=None):
    env = fit1.envelope if fit1 else None
    if fit2 is not None:
        env = unary_union([fit1.envelope, fit2.envelope])
    p = {"geometry": g, "_open_ground": open_g, "constrained_pct": base_pct, "rank": label[0], "parcel_id": label[1],
         "_envelope_detail": fit1 if fit1 else sitefit.Fit(sitefit.NONE_FOUND)}
    if env is not None:
        p[sitefit.ENVELOPE_KEY] = env
    if pond is not None:
        p.update(pond.fields())
        if pond2 is not None and pond2.pond is not None:
            p[detention.POND_KEY] = unary_union([pond.pond, pond2.pond])
    svg, why = plan.render(p)
    if not svg:
        return None, why
    pj = LocalProjection.around(g)
    parcel_ft = sitefit.polygon_parts(plan._feet(g, pj))
    bb = [min(q.bounds[i] for q in parcel_ft) if i < 2 else max(q.bounds[i] for q in parcel_ft) for i in range(4)]
    fr = plan._Frame(tuple(bb))
    if extra_excl is not None and not extra_excl.is_empty:
        ex = sitefit.polygon_parts(plan._feet(extra_excl.intersection(g), pj))
        if ex:
            svg = svg.replace("</title>", "</title>" + HATCH, 1)
            svg = svg.replace('<path class="parcel"', f'<path class="scenario-exclusion" d="{fr.path(ex)}" fill="url(#hx)" fill-opacity="0.7" stroke="#c92a2a" stroke-width="1.5"/><path class="parcel"', 1)
    if fit2 is not None:
        c = sitefit.polygon_parts(plan._feet(fit2.envelope, pj))[0].centroid
        cx, cy = (float(v) for v in fr.xy(c.x, c.y).split(","))
        svg = svg.replace("</svg>", plan._text(cx, cy + 4, f"2nd: {fit2.label} {fit2.building_sf:,} SF", 11, "middle", "bold") + "</svg>")
    svg = add_legend(svg, extra_rows)
    (OUT / f"{sid}.svg").write_text(svg, encoding="utf-8")
    return f"{sid}.svg", ""

_orig_fit_text = plan._fit_text
def _fit_text(f):
    if f is not None and f.state == sitefit.FITS and hasattr(f, "_extra"):
        return (f"Envelope: {f.label} {f.length_ft:,.0f} x {f.width_ft:,.0f} ft building, "
                f"{f.building_sf:,} SF ({f._extra})")
    return _orig_fit_text(f)
plan._fit_text = _fit_text

# ---- zoning -------------------------------------------------------------------
def atl_zones(g, pad_deg=0.003):
    b = g.bounds
    env = (b[0] - pad_deg, b[1] - pad_deg, b[2] + pad_deg, b[3] + pad_deg)
    z = arcgis.request_json(ATL_ZONING + "/query", {"geometry": ",".join(map(str, env)), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                                                   "spatialRel": "esriSpatialRelIntersects", "outFields": "ZONECLASS", "returnGeometry": "true", "outSR": 4326})
    return [(f["attributes"].get("ZONECLASS") or "", esri_to_shape(f["geometry"])) for f in z.get("features", []) if f.get("geometry")]

def is_residential(zc):
    z = zc.upper()
    return z.startswith(("R-", "RG", "R4", "R3", "R5", "MR", "PD-H", "RLC")) or z in ("R-1", "R-2", "R-3", "R-4", "R-4A", "R-4B", "R-5")

def zoning_on(g, zones):
    pj = LocalProjection.around(g); out = {}
    for zc, s in zones:
        if s is not None and s.intersects(g):
            a = acres(pj(s.intersection(g)))
            if a > 0.01: out[zc] = round(out.get(zc, 0) + a, 2)
    return out

# ---- one scenario ---------------------------------------------------------------
def scenario(sid, title, g, *, run_fit=None, cands=None, extra_excl=None, excl_label=None, note=None, label=("", ""), second=False,
             tax_acres=None, steep_pct=None, explain_rows=()):
    g = shapely.make_valid(g)
    if g.geom_type == "GeometryCollection":
        g = unary_union([x for x in g.geoms if x.area > 0])
    pj = LocalProjection.around(g)
    base = constraints(g)
    base_pct = round(100 * pj(base.intersection(g)).area / pj(g).area, 1) if base is not None else 0.0
    allc = unary_union([x for x in (base, extra_excl) if x is not None and not x.is_empty]) if (base is not None or extra_excl is not None) else None
    open_g = g.difference(allc) if allc is not None else g
    cut_pct = round(100 * pj(extra_excl.intersection(g)).area / pj(g).area, 1) if extra_excl is not None else 0.0
    cpct = round(100 * pj(allc.intersection(g)).area / pj(g).area, 1) if allc is not None else 0.0
    std = sitefit.fit_parcel(g, open_g, cpct)
    rows, best, best_any = best_of(g, open_g, cpct, cands or OPERATOR)
    fit1, pond1 = best if best else (None, None)
    if fit1 is None:
        pond1 = detention.place_pond(g, open_g, cpct, sitefit.Fit(sitefit.NONE_FOUND), None)
    fit2 = pond2 = None
    if second and fit1 is not None:
        used = unary_union([fit1.envelope, pond1.pond])
        open2 = open_g.difference(buffer_ft(used, 80, pj))
        _, b2, _ = best_of(g, open2, cpct, OPERATOR)
        if b2:
            fit2, pond2 = b2
    legend = []
    if extra_excl is not None:
        legend.append(("url(#hx)", f"Test cut {cut_pct:.0f}% of the parcel (red hatch): {excl_label}. Not a mapped constraint; it is also shaded blue."))
    if fit2 is not None:
        legend.append((plan.ENVELOPE_FILL, f"Second building: {fit2.label} {fit2.length_ft:,.0f} x {fit2.width_ft:,.0f} ft, {fit2.building_sf:,} SF, "
                                           f"with its own pond (80 ft drive strip around the first building and pond). Total {fit1.building_sf + fit2.building_sf:,} SF."))
    own_top = getattr(fit1, "_top", None) if fit1 is not None else None
    if own_top is not None and own_top.building_sf > fit1.building_sf:
        legend.append((plan.PARCEL_FILL, f"The same building without leaving room for the pond: up to {own_top.building_sf:,} SF "
                                         f"({own_top.length_ft:,.0f} ft long). Sizes are lower bounds (two search orders tried)."))
    elif best_any is not None and fit1 is None:
        legend.append((plan.PARCEL_FILL, f"Up to {best_any.building_sf:,} SF ({best_any.label}) fits, but none of these sizes leaves room for the pond."))
    acre_line = f"Acres: {acres(pj(g)):,.1f} county map" + (f", {tax_acres:,.1f} tax roll" if tax_acres else "") + \
                (f"; steep share (run's lidar check) {steep_pct}: steep ground not located" if steep_pct not in (None, "") else "")
    legend.append((plan.PARCEL_FILL, acre_line))
    legend += list(explain_rows)
    svg_name, why = draw(sid, g, open_g, base_pct, fit1, pond1, label, extra_excl, legend, fit2, pond2)
    open_parts = sitefit.polygon_parts(pj(open_g))
    rec = {"id": sid, "title": title, "parcel_acres_map": round(acres(pj(g)), 1), "parcel_acres_tax_roll": tax_acres,
           "steep_pct": steep_pct, "map_constrained_pct": base_pct, "test_cut_pct": cut_pct,
           "largest_open_acres": round(max((acres(q) for q in open_parts), default=0), 1),
           "standard_fit_after_test_cut": {"state": std.state, "text": std.fields()["envelope_fit"], "sf": std.building_sf},
           "run_envelope_fit": run_fit,
           "candidates": rows,
           "drawn": {"label": fit1.label if fit1 else None, "length_ft": fit1.length_ft if fit1 else None,
                     "width_ft": fit1.width_ft if fit1 else None, "sf": fit1.building_sf if fit1 else None,
                     "operator_prototype": bool(fit1 and fit1._cand["operator"])},
           "max_sf_ignoring_pond_same_building": (getattr(fit1, "_top", None).building_sf if fit1 is not None and getattr(fit1, "_top", None) else None),
           "max_sf_ignoring_pond_any_building": best_any.building_sf if best_any else None,
           "pond": pond1.state if pond1 else None,
           "second_building": ({"label": fit2.label, "length_ft": fit2.length_ft, "sf": fit2.building_sf,
                                "pond": pond2.state} if fit2 else None),
           "total_sf": (fit1.building_sf if fit1 else 0) + (fit2.building_sf if fit2 else 0),
           "note": note, "svg": svg_name, "why_not_drawn": why,
           "geo": {"parcel": mapping(g), "open": mapping(open_g), "env1": mapping(fit1.envelope) if fit1 else None,
                   "env2": mapping(fit2.envelope) if fit2 else None, "pond": mapping(pond1.pond) if pond1 and pond1.pond is not None else None,
                   "pond2": mapping(pond2.pond) if pond2 and pond2.pond is not None else None}}
    print(json.dumps({k: rec[k] for k in ("id", "parcel_acres_map", "test_cut_pct", "drawn", "max_sf_ignoring_pond_same_building", "pond", "second_building", "total_sf")}))
    return rec

rows_run = {r["parcel_id"]: r for r in __import__("csv").DictReader(open(RUN / "ranked.csv", encoding="utf-8-sig"))}
def tax(pid): return float(rows_run[pid]["acres"])
def steep(pid): return f"{rows_run[pid]['steep_pct']}%"
def runfit(pid): return rows_run[pid]["envelope_fit"]
def steep_weighted(pids, total_tax):
    have = [(float(rows_run[p]["acres"]), float(rows_run[p]["steep_pct"])) for p in pids if p in rows_run and rows_run[p]["steep_pct"] != ""]
    ac = sum(a for a, _ in have)
    if not have: return "not checked"
    w = sum(a * s for a, s in have) / ac
    return f"{w:.1f}% area-weighted over the {ac:.1f} tax-roll ac checked ({max(total_tax - ac, 0):.1f} ac not checked)"
recs = []
RES_BUF = 100

# #21 alone: operator prototypes + smaller test buildings; residential districts nearby buffered 100 ft
g21 = ranked_geom("14 006800060376")
z21 = atl_zones(g21)
res21 = unary_union([s for zc, s in z21 if s is not None and is_residential(zc)])
cut21 = buffer_ft(res21, RES_BUF, LocalProjection.around(g21))
recs.append(scenario("21-small", "#21 0 Mount Zion Rd SW: smaller buildings, 100 ft residential test buffer", g21,
                     cands=OPERATOR + SMALL, extra_excl=cut21, excl_label=f"residential zoning nearby (city layer, measured from the zoning line, which runs to the street centre) + {RES_BUF} ft test buffer",
                     note={"zoning_on_parcel": zoning_on(g21, z21)}, label=("21", "14 006800060376 (test fit)"),
                     tax_acres=tax("14 006800060376"), steep_pct=steep("14 006800060376"), run_fit=runfit("14 006800060376")))

# #21 + the same owner's lots; the lots' own zoning is ignored (assumed rezoned), residential land outside buffered
box = county_box((-84.3974, 33.6729, -84.3950, 33.6756))
same = [(a, s) for a, s in box if (a.get("Owner") or "").strip().upper().startswith("ZION SEVENTY FIVE PROPERTY")]
g21a = unary_union([s for _, s in same] + [g21])
z21a = atl_zones(g21a)
res21a = unary_union([s for zc, s in z21a if s is not None and is_residential(zc)]).difference(g21a)
cut21a = buffer_ft(res21a, RES_BUF, LocalProjection.around(g21a))
lots = [{"parcel": a["ParcelID"], "address": a["Address"], "class": a.get("ClassCode"), "land_use": a.get("LUCode"),
         "zoning": zoning_on(s, z21a)} for a, s in sorted(same, key=lambda t: t[0]["ParcelID"])]
recs.append(scenario("21-assemblage", "#21 with the same owner's lots (assumes the house lots are rezoned)", g21a,
                     cands=OPERATOR + SMALL, extra_excl=cut21a, excl_label=f"residential zoning outside the assemblage (city layer, measured from the zoning line) + {RES_BUF} ft test buffer",
                     note={"lots": lots, "box_query": "county parcels in a fixed box around #21; same-owner lots outside it are missed"},
                     label=("21+", "assemblage (test fit)"), tax_acres=round(sum(float(a.get("LandAcres") or 0) for a, _ in same), 1),
                     steep_pct=steep_weighted([a["ParcelID"] for a, _ in same], sum(float(a.get("LandAcres") or 0) for a, _ in same)),
                     run_fit=runfit("14 006800060376"), explain_rows=[(plan.PARCEL_FILL, "Assumes the two R-4 house lots and the vacant R-4 lot (1.3 ac together) are bought and rezoned industrial; the rear lot is already I-1.")]))

# #11: operator prototypes + smaller test buildings
g11 = ranked_geom("09F110000440513")
recs.append(scenario("11-small", "#11 0 Graham Rd: smaller buildings", g11, cands=OPERATOR + SMALL,
                     label=("11", "09F110000440513 (test fit)"), tax_acres=tax("09F110000440513"), steep_pct=steep("09F110000440513"), run_fit=runfit("09F110000440513")))

# #115 + #170 + the partnership's two other Herndon Rd parcels; two buildings, two ponds
cand = county("ParcelID IN ('07 260001350151','07 260001221014','07 260001351027','07 260001350904','07 260001350169')")
br = [(a, s) for a, s in cand if (a.get("Owner") or "").strip().upper().startswith("BR FAIRBURN")]
recs.append(scenario("115-170-assemblage", "#115 + #170 + the same partnership's Herndon Rd parcels", unary_union([s for _, s in br]),
                     second=True, label=("115+170", "assemblage (test fit)"),
                     note={"parcels": [{"parcel": a["ParcelID"], "address": a["Address"]} for a, _ in sorted(br, key=lambda t: t[0]["ParcelID"])],
                           "left_out": "7635 Bishop Rd (a different owner)"},
                     tax_acres=round(sum(float(a.get("LandAcres") or 0) for a, _ in br), 1),
                     steep_pct=steep_weighted([a["ParcelID"] for a, _ in br], sum(float(a.get("LandAcres") or 0) for a, _ in br)),
                     run_fit=f"#115 {runfit('07 260001350151')}; #170 {runfit('07 260001221014')}",
                     explain_rows=[(plan.PARCEL_FILL, "No residential check here: only Atlanta's zoning layer is read, and this site is in South Fulton.")]))

# #25: the neighbouring cemetery parcel + 100 ft test buffer
g25 = ranked_geom("09F350101520306"); pj25 = LocalProjection.around(g25)
cem_g = unary_union([s for _, s in county("ParcelID = '09F350101520017'")])
cut25 = buffer_ft(cem_g, 100, pj25)
shared_ft = round(pj25(g25).boundary.intersection(pj25(cem_g).buffer(0.5)).length * FT_PER_M)
recs.append(scenario("25-cemetery", "#25 0 Welcome All Rd: 100 ft cemetery test buffer, room for the pond", g25,
                     cands=OPERATOR + SMALL, extra_excl=cut25, excl_label="100 ft test buffer from the neighbouring cemetery parcel",
                     note={"cemetery_parcel": "09F350101520017", "relation": "neighbouring parcel; no overlap with #25",
                           "shared_boundary_ft": shared_ft, "buffer_ft": 100,
                           "buffer_overlap_acres": round(acres(pj25(cut25.intersection(g25))), 2)},
                     label=("25", "09F350101520306 (test fit)"), tax_acres=tax("09F350101520306"), steep_pct=steep("09F350101520306"), run_fit=runfit("09F350101520306")))

# #65: residential-zoned part cut, plus 100 ft test buffer from all residential zoning
g65 = ranked_geom("14 0180  LL0368")
z65 = atl_zones(g65)
res65 = unary_union([s for zc, s in z65 if s is not None and is_residential(zc)])
cut65 = buffer_ft(res65, RES_BUF, LocalProjection.around(g65))
recs.append(scenario("65-residential", "#65 0 Verbena St NW: residential zoning cut + 100 ft test buffer, room for the pond", g65,
                     cands=OPERATOR + SMALL, extra_excl=cut65, excl_label=f"residential zoning (city layer, measured from the zoning line) + {RES_BUF} ft test buffer",
                     note={"zoning_on_parcel": zoning_on(g65, z65)}, label=("65", "14 0180  LL0368 (test fit)"),
                     tax_acres=tax("14 0180  LL0368"), steep_pct=steep("14 0180  LL0368"), run_fit=runfit("14 0180  LL0368")))

# #170: the screen's standard fit, drawn (outside the run's briefed set)
recs.append(scenario("170-standard", "#170 7500 Herndon Rd: operator prototypes, drawn this session", ranked_geom("07 260001221014"),
                     label=("170", "07 260001221014"), tax_acres=tax("07 260001221014"), steep_pct=steep("07 260001221014"), run_fit=runfit("07 260001221014")))

for f in OUT.glob("*.svg"):
    if f.stem not in {r["id"] for r in recs}:
        f.unlink()
(OUT / "testfits.json").write_text(json.dumps(recs, indent=1, default=str))
print("wrote", OUT)
