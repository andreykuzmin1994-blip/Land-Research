"""Every network fetch the site planner needs, behind small functions.

``siteplan`` is pure geometry; this module turns a site (a rank, or an
assemblage of parcel ids) into ``siteplan.SiteInputs``: the parcel shape,
the mapped constraints (``layers.fetch`` + ``checks._overlap``), HIFLD
transmission lines, county neighbours (their land-use codes classify the
lot lines), the GDOT frontage (``frontage.check_parcel``) and USGS 3DEP
lidar elevation samples.

Fail closed (R2): every input carries a state, checked / none /
not_checked (or partial for the lidar), with a reason. A failed, truncated
or malformed answer is never read as "nothing there": the planner turns a
not-checked input into a named flag and ``feasible = None``.

Polite (R14): this module never assigns ``arcgis`` globals. Each request
passes its own ``max_age_days``. Uncached requests to one host are spaced
at least ``MIN_INTERVAL_S`` apart, a host that fails
``STOP_AFTER_FAILURES`` times in a row is not asked again in this process,
and nothing runs in a thread pool. The existing USER_AGENT is kept.

No owner data (SR-8): every outFields value is a constant on
``OUT_FIELDS_ALLOWED``; none names an owner or mailing field, and
assemblages are explicit parcel-id lists, never selected by owner.
Parcel ids go into where clauses only after ``siteplan.check_pid``
(upper-case letters, digits and spaces; inner spaces kept exactly).

New endpoints live here as constants, never in ``layers.LAYERS`` (that
table is part of the AutoResearch evaluator's frozen key, R7).
"""

from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from shapely.geometry import Point, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from . import arcgis, checks, frontage, layers
from .arcgis import ArcGISError
from .geo import LocalProjection, esri_to_shape, expand, tile_envelopes
from . import siteplan as sp

__all__ = ["ArcGISError", "Fetcher", "load_run", "site_spec", "gather", "sources"]

COUNTY_PARCELS_URL = "https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11"
TRANSMISSION_URL = "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Electric_Power_Transmission_Lines/FeatureServer/0"
DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"

NEIGHBOUR_FIELDS = "ParcelID,LUCode,ClassCode"
TRANSMISSION_FIELDS = "VOLTAGE,VOLT_CLASS,STATUS"     # the prototype also asked for OWNER (the utility); dropped (R1)
PARCEL_ID_FIELDS = "ParcelID"
OUT_FIELDS_ALLOWED = frozenset({NEIGHBOUR_FIELDS, TRANSMISSION_FIELDS, PARCEL_ID_FIELDS})

NEIGHBOUR_PAD_DEG = 0.0015
TRANSMISSION_PAD_DEG = 0.002
NEAR_PAD_DEG = 0.0005          # about 150 ft: water shapes near the line (R21)
COORD_DECIMALS = 7
DEM_CHUNK = 400
DEM_CACHE_DAYS = 3650.0        # lidar does not change; one shared lifetime (R14)
MIN_INTERVAL_S = 1.0
STOP_AFTER_FAILURES = 5
CONSTRAINT_LAYERS = ("flood", "wetlands", "easements", "protected")


class HostDown(ArcGISError):
    """A host failed STOP_AFTER_FAILURES times in a row: not asked again."""


def _features_check(data: dict[str, Any]) -> str | None:
    feats = data.get("features")
    if not isinstance(feats, list):
        return "no features list in the answer"
    if data.get("exceededTransferLimit"):
        return f"the answer was cut short at {len(feats)} features (exceededTransferLimit)"
    return None


def _samples_check(n: int) -> Callable[[dict[str, Any]], str | None]:
    def problem(data: dict[str, Any]) -> str | None:
        s = data.get("samples")
        if not isinstance(s, list):
            return "no samples list in the answer"
        if len(s) > n:
            return f"{len(s)} samples for {n} points"
        return None
    return problem


class Fetcher:
    """Polite access to the hosts this tool asks directly (R14)."""

    def __init__(self, cache_days: float | None = None, min_interval: float = MIN_INTERVAL_S,
                 stop_after: int = STOP_AFTER_FAILURES, sleep: Callable[[float], None] | None = None,
                 clock: Callable[[], float] | None = None):
        self.cache_days = cache_days
        self.min_interval = min_interval
        self.stop_after = stop_after
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.last: dict[str, float] = {}
        self.failures: dict[str, int] = {}
        self.down: set[str] = set()
        self.calls = 0

    def _host(self, url: str) -> str:
        return urlsplit(url).hostname or url

    def _fail(self, host: str) -> None:
        self.failures[host] = self.failures.get(host, 0) + 1
        if self.failures[host] >= self.stop_after:
            self.down.add(host)

    def call(self, url: str, params: dict[str, Any], *, check: Callable[[dict[str, Any]], str | None],
             max_age_days: float | None = None) -> dict[str, Any]:
        host = self._host(url)
        if host in self.down:
            raise HostDown(f"{host}: {self.stop_after} failures in a row; not asked again")
        age = self.cache_days if max_age_days is None else max_age_days
        hit = arcgis.cached_json(url, params, max_age_days=age if age is not None else arcgis.cache_days, check=check)
        if hit is not None:
            return hit[0]
        wait = self.last.get(host, -math.inf) + self.min_interval - self.clock()
        if wait > 0:
            self.sleep(wait)
        self.calls += 1
        try:
            data = arcgis.request_json(url, params, check=check, max_age_days=age)
        except ArcGISError:
            self._fail(host)
            raise
        finally:
            self.last[host] = self.clock()
        self.failures[host] = 0
        return data

    def frontage(self, g: BaseGeometry) -> frontage.Frontage:
        host = self._host(frontage.IDENTIFY_URL)
        if host in self.down:
            return frontage.not_checked(f"{frontage.SERVICE_FAILED} ({self.stop_after} failures in a row)")
        fr = frontage.check_parcel(g)
        if fr.state == frontage.NOT_CHECKED and fr.reason.startswith(frontage.SERVICE_FAILED):
            self._fail(host)
        else:
            self.failures[host] = 0
        return fr


# ---------------------------------------------------------------------------
# One input each
# ---------------------------------------------------------------------------
def _envelope(g: BaseGeometry, pad: float) -> str:
    b = g.bounds
    return f"{b[0]-pad},{b[1]-pad},{b[2]+pad},{b[3]+pad}"


def county_near(g: BaseGeometry, fetch: Fetcher) -> tuple[list, sp.InputState]:
    """County parcels around the site, with land-use and class codes."""
    params = {"geometry": _envelope(g, NEIGHBOUR_PAD_DEG), "geometryType": "esriGeometryEnvelope",
              "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
              "outFields": NEIGHBOUR_FIELDS, "returnGeometry": "true", "outSR": 4326}
    try:
        d = fetch.call(COUNTY_PARCELS_URL + "/query", params, check=_features_check)
    except ArcGISError as exc:
        return [], sp.InputState("not_checked", str(exc)[:160])
    out, missing = [], 0
    for f in d["features"]:
        s = esri_to_shape(f.get("geometry")) if f.get("geometry") else None
        if s is None:
            missing += 1
            continue
        a = f.get("attributes") or {}
        out.append(({k: a.get(k) for k in ("ParcelID", "LUCode", "ClassCode")}, s))
    reason = f"{missing} record(s) without a shape ignored" if missing else ""
    return out, sp.InputState("checked", reason)


def transmission(g: BaseGeometry, fetch: Fetcher) -> tuple[list, sp.InputState]:
    """HIFLD transmission lines near the site."""
    params = {"geometry": _envelope(g, TRANSMISSION_PAD_DEG), "geometryType": "esriGeometryEnvelope",
              "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": TRANSMISSION_FIELDS,
              "returnGeometry": "true", "outSR": 4326}
    try:
        d = fetch.call(TRANSMISSION_URL + "/query", params, check=_features_check)
    except ArcGISError as exc:
        return [], sp.InputState("not_checked", str(exc)[:160])
    out = []
    for f in d["features"]:
        s = esri_to_shape(f.get("geometry"))
        if s is None:
            # A line we cannot draw could run under the building: fail closed.
            return [], sp.InputState("not_checked", "a transmission line came back without geometry")
        a = f.get("attributes") or {}
        out.append(({k: a.get(k) for k in ("VOLTAGE", "VOLT_CLASS", "STATUS")}, s))
    return out, sp.InputState("checked" if out else "none")


def _where_ids(pids: list[str]) -> str:
    q = [f"'{sp.check_pid(p).replace(chr(39), chr(39) * 2)}'" for p in pids]
    return f"ParcelID = {q[0]}" if len(q) == 1 else f"ParcelID IN ({','.join(q)})"


def county_parcels(pids: list[str], fetch: Fetcher) -> dict[str, BaseGeometry]:
    """Exactly one county shape per parcel id, or PlanError (R12)."""
    if not 1 <= len(pids) <= sp.MAX_ASSEMBLE_IDS:
        raise sp.PlanError(f"1 to {sp.MAX_ASSEMBLE_IDS} parcel ids per query")
    where = _where_ids(pids)
    d = fetch.call(COUNTY_PARCELS_URL + "/query", {"where": where, "outFields": PARCEL_ID_FIELDS,
                                                   "returnGeometry": "true", "outSR": 4326}, check=_features_check)
    got: dict[str, list] = {}
    for f in d["features"]:
        pid = (f.get("attributes") or {}).get("ParcelID")
        got.setdefault(pid, []).append(esri_to_shape(f.get("geometry")))
    out = {}
    for pid in pids:
        shapes = got.get(pid, [])
        if len(shapes) != 1 or shapes[0] is None:
            raise sp.PlanError(f"parcel {pid!r}: {len(shapes)} county shapes (exactly one wanted)")
        out[pid] = shapes[0]
    return out


def dem(plane: sp.Plane, parcel_ft: BaseGeometry, fetch: Fetcher, step: float = sp.DEM_STEP_FT) -> sp.Dem:
    """USGS 3DEP samples on the planner's grid (same requests as the
    prototype: 400 points a chunk, 7 decimals, first value only)."""
    grid = sp.dem_points(parcel_ft, step)
    out = sp.Dem(step=step, points={ij: p for ij, p in grid})
    if not grid:
        return out
    keys = [ij for ij, _ in grid]
    wg = [plane.wgs(Point(p)) for _, p in grid]
    failed = 0
    for i in range(0, len(wg), DEM_CHUNK):
        chunk = wg[i:i + DEM_CHUNK]
        geom = json.dumps({"points": [[round(p.x, COORD_DECIMALS), round(p.y, COORD_DECIMALS)] for p in chunk],
                           "spatialReference": {"wkid": 4326}})
        try:
            d = fetch.call(DEM_URL, {"geometry": geom, "geometryType": "esriGeometryMultipoint", "returnFirstValueOnly": "true"},
                           check=_samples_check(len(chunk)), max_age_days=DEM_CACHE_DAYS)
        except ArcGISError:
            failed += 1
            continue
        for s in d["samples"]:
            try:
                loc = int(s["locationId"])
                if not 0 <= loc < len(chunk):
                    continue
                out.elev[keys[i + loc]] = float(s["value"]) * sp.FT_PER_M
            except (ValueError, KeyError, TypeError):
                pass            # NoData
    chunks = math.ceil(len(wg) / DEM_CHUNK)
    if not out.elev:
        out.state, out.reason = "not_checked", f"{failed} of {chunks} request(s) failed" if failed else "no ground values"
    elif out.coverage < sp.DEM_PARTIAL_BELOW:
        out.state, out.reason = "partial", f"{failed} of {chunks} request(s) failed" if failed else "NoData samples"
    return out


def frontage_input(g: BaseGeometry, fetch: Fetcher) -> sp.FrontageInput:
    fr = fetch.frontage(g)
    state = {frontage.CHECKED: "checked", frontage.NONE: "none"}.get(fr.state, "not_checked")
    return sp.FrontageInput(state, fr.line if state == "checked" else None, fr.feet, fr.road, fr.reason)


def _overlap_wgs(g: BaseGeometry, pj: LocalProjection, total: float, idx: layers.Index) -> BaseGeometry | None:
    """The one wrapper around checks._overlap (the layer hits clipped to the parcel)."""
    x, _, _ = checks._overlap(g, pj, total, idx)
    return x


def constraints(g: BaseGeometry) -> tuple[list, list, sp.InputState]:
    """Per layer, the hits clipped to the parcel (the prototype's
    constraints) and the raw shapes within about 150 ft (for R21)."""
    pj = LocalProjection.around(g)
    tot = pj(g).area or 1
    tiles = tile_envelopes([g.bounds])
    region = expand(g.bounds, 0.02)
    near_box = box(*expand(g.bounds, NEAR_PAD_DEG))
    clipped, near, bad = [], [], []
    for key in CONSTRAINT_LAYERS:
        try:
            idx = layers.fetch(layers.LAYERS[key], tiles, region)
        except ArcGISError as exc:
            bad.append(f"{key}: {str(exc)[:80]}")
            continue
        x = _overlap_wgs(g, pj, tot, idx)
        if x is not None:
            clipped.append(x)
        for s, _a in idx.candidates(near_box):
            y = s.intersection(near_box)
            if not y.is_empty and y.area > 0:
                near.append(y)
    if bad:
        return clipped, near, sp.InputState("not_checked", "; ".join(bad))
    return clipped, near, sp.InputState("checked" if clipped else "none")


# ---------------------------------------------------------------------------
# A run, a site spec, the inputs
# ---------------------------------------------------------------------------
@dataclass
class RunData:
    run_dir: Path
    rows: dict[str, dict[str, Any]]                 # by rank (string)
    features: dict[str, dict[str, Any]]             # ranked.geojson by rank (string)
    by_pid: dict[str, list[str]]                    # parcel id -> ranks


def load_run(run_dir: Path) -> RunData:
    with open(run_dir / "ranked.csv", encoding="utf-8-sig", newline="") as fh:
        rows = {str(r["rank"]).strip(): r for r in csv.DictReader(fh)}
    gj = json.loads((run_dir / "ranked.geojson").read_text(encoding="utf-8"))
    feats: dict[str, dict[str, Any]] = {}
    by_pid: dict[str, list[str]] = {}
    for f in gj.get("features", []):
        props = f.get("properties") or {}
        rk = str(props.get("rank"))
        feats[rk] = f
        pid = props.get("parcel_id")
        if isinstance(pid, str):
            by_pid.setdefault(pid, []).append(rk)
    return RunData(run_dir, rows, feats, by_pid)


@dataclass
class SiteSpec:
    name: str
    key: str
    title: str
    parcel_ids: list[str]
    geometry: BaseGeometry
    meta: dict[str, Any]
    tax_acres: float | None
    assembled: bool
    cut: tuple[str, float] | None = None
    cut_geometry: BaseGeometry | None = None


def _acres(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) and x > 0 else None
    except (TypeError, ValueError):
        return None


def _meta(r: dict[str, Any]) -> dict[str, Any]:
    # Only these three fields of a ranked row reach the planner.
    return {"jurisdiction": r.get("jurisdiction") or "", "hsg": r.get("soil_hsg") or "B", "zoning": r.get("zoning") or ""}


def site_spec(run: RunData, name: str, assemble: list[str] | None, cut: tuple[str, float] | None,
              fetch: Fetcher, opts: sp.PlanOptions) -> SiteSpec:
    key = sp.slug(name)
    if assemble is None:
        r, f = run.rows.get(name), run.features.get(name)
        if r is None or f is None or not f.get("geometry"):
            raise sp.PlanError(f"rank {name} is not in the run")
        g = shape(f["geometry"])
        acres = _acres(r.get("acres"))
        title = f"#{name} {r.get('site_address') or ''}, {r.get('jurisdiction') or ''}" + (f" ({acres:.1f} ac tax roll)" if acres else "")
        spec = SiteSpec(name, key, title, [r["parcel_id"]], g, _meta(r), acres, False)
    else:
        pids = [sp.check_pid(p) for p in assemble]
        shapes, ranks, missing = {}, [], []
        for pid in pids:
            rks = run.by_pid.get(pid, [])
            if len(rks) > 1:
                raise sp.PlanError(f"parcel {pid!r} appears {len(rks)} times in the run")
            if rks and run.features[rks[0]].get("geometry"):
                shapes[pid] = shape(run.features[rks[0]]["geometry"])
                ranks.append(rks[0])
            else:
                missing.append(pid)
        if not opts.fixes:
            # The prototype took every member's shape from the county; the
            # default takes the run's own shape where it has one (R12).
            shapes.update(county_parcels(pids, fetch))
        elif missing:
            shapes.update(county_parcels(missing, fetch))
        g = unary_union([shapes[p] for p in pids])
        rows = sorted((run.rows[rk] for rk in ranks if rk in run.rows), key=lambda r: int(r["rank"]))
        meta = _meta(rows[0]) if rows else {"jurisdiction": "", "hsg": "B", "zoning": ""}
        tax = sum(_acres(r.get("acres")) or 0 for r in rows) if len(rows) == len(pids) else None
        listed = ", ".join(f"#{rk}" for rk in sorted(ranks, key=int))
        title = (f"{name}: {len(pids)} parcels assembled ({listed or 'none ranked'}"
                 + (f" + {len(missing)} not ranked" if missing and ranks else "") + f"), {meta['jurisdiction'] or 'jurisdiction unknown'}")
        spec = SiteSpec(name, key, title, pids, g, meta, tax or None, True)
    if cut is not None:
        pid, ft = cut
        spec.cut = cut
        spec.cut_geometry = county_parcels([pid], fetch)[pid]
        spec.title += f" - {ft:g} ft test buffer around parcel {pid}"
    return spec


def gather(spec: SiteSpec, fetch: Fetcher, opts: sp.PlanOptions) -> sp.SiteInputs:
    """Every input for one site, each with its state."""
    g = spec.geometry
    plane = sp.Plane(g)
    parcel, _ = sp.site_parcel(plane, g, spec.assembled, opts)
    clipped, near, lst = constraints(g)
    tl, tst = transmission(g, fetch)
    fr = frontage_input(g, fetch)
    nb, nst = county_near(g, fetch)
    d = dem(plane, parcel, fetch)
    cut_wgs = None
    if spec.cut_geometry is not None:
        # Same construction as the prototype: buffer in the site plane, back to WGS84.
        cut_wgs = plane.wgs(plane.ft(spec.cut_geometry).buffer(spec.cut[1]))
    states = {"layers": lst, "transmission": tst, "neighbours": nst,
              "frontage": sp.InputState(fr.state, fr.reason if fr.state == "not_checked" else ""),
              "dem": sp.InputState(d.state, d.reason)}
    return sp.SiteInputs(key=spec.key, rank=spec.name, title=spec.title, parcel_ids=spec.parcel_ids, geometry=g,
                         meta=spec.meta, tax_acres=spec.tax_acres, assembled=spec.assembled, cut_wgs=cut_wgs,
                         constraints_wgs=clipped, constraints_near_wgs=near, transmission=tl, frontage=fr,
                         neighbours=nb, dem=d, states=states)


def sources() -> dict[str, Any]:
    return {"county_parcels": COUNTY_PARCELS_URL, "transmission": TRANSMISSION_URL, "elevation": DEM_URL,
            "frontage": frontage.IDENTIFY_URL,
            "layers": {k: layers.LAYERS[k].url for k in CONSTRAINT_LAYERS},
            "query_date": time.strftime("%Y-%m-%d")}
