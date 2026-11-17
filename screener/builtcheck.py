"""Built check: is a ranked parcel really land today?

    python -m screener.builtcheck RUN_DIR --out DIR [--top N]
        [--visual visual.json] [--visual-by-rank]
        [--second-looks-from land_today.json] [--decisions CSV]
        [--cache-days D]

The screen drops parcels whose own tax record shows buildings. It cannot
see a building on a separate record, or a paved yard, outdoor storage, a
quarry or a landfill. For the top N ranked parcels this reads, without any
owner data:

1. FEMA USA Structures footprints inside the parcel's OUTLINE (holes in the
   county shape filled in): total SF, count, the largest, and how much sits
   inside a hole. A building in a hole is often on a separate
   improvements-only tax record (#16 of the 2026-11-13 run).
2. Holes in the county parcel shape.
3. Fulton tax records whose polygon lies mostly (80%) inside the outline
   (other parcel ids), with class, land use and improvement value.

and writes OUT/builtcheck.csv/json (``built_check``: built, check, no
building found, or not checked) and OUT/land_today.csv/json.

``land_today`` (raw_land, partly_usable, not_raw_land, unclear) rests on a
look at the 2024 county aerial photo. That look is a manual step done by
reviewers who see the image (the screener has no image tooling, by its
dependency rule: reviews/37 R25), recorded in a visual.json file passed
with --visual. Its format (``load_visual``):

    {"run": {"name": ..., "ranked_csv_sha256": ...},          # optional
     "visual": {"<rank>": {"rank": 12, "parcel_id": "...",    # parcel_id optional
                           "state": one of AERIAL_STATES,
                           "usable_as_raw_land": yes|partly|no|unclear,
                           "building_share_pct": 0-100, "paved_share_pct": 0-100,
                           "evidence": "what the photo shows",
                           "confidence": high|medium|low}},
     "second_look": {"<rank>": {"rank": 12, "parcel_id": "...",
                                "land_today": one of LAND_CLASSES,
                                "what_is_there": "...", "why": "...",
                                "confidence": high|medium|low}}}   # optional

The rule (``classify_land``) reproduces how the 2026-11-16 table was made:
where the photo review and the footprints agree, the photo's verdict
stands (yes -> raw_land, partly -> partly_usable, no -> not_raw_land);
where they disagree (footprints say built but the photo shows open or
graded ground, or no footprint but the photo shows houses, a yard or
another use), or the photo is unclear or low-confidence, a second reviewer
looks again with both in hand and that verdict stands. Without a second
look such a parcel stays ``unclear``. No photo review, a failed fetch or a
footprint check that could not run never gives raw_land: it is
``unclear``.

Fetching is polite (reviews/37 R14): answers come from the shared cache
when young enough, a request to a host waits at least MIN_GAP_S after the
last one, and after MAX_HOST_FAILURES failures in a row a host is not
asked again; its parcels read "not checked".
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import shapely
from shapely.errors import GEOSException
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from . import arcgis
from .geo import LocalProjection, acres, esri_to_shape
from .viewer import (LAND_CLASSES, SCHEMA_VERSION, OwnerGuard, Refused, atomic_write, cap,
                     check_out_path, csv_cell, read_ranked, run_identity)

# Sources and the fields asked of them: named constants, no owner or
# mailing field (reviews/37 R1, R22).
STRUCTURES_URL = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
                  "USA_Structures_View/FeatureServer/0")
STRUCTURE_FIELDS = "OCC_CLS,PRIM_OCC,SQFEET,IMAGE_DATE"
PARCELS_URL = ("https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/"
               "PropertyMapViewer/MapServer/11")
PARCEL_FIELDS = "ParcelID,Address,ClassCode,LUCode,LandAppr,ImprAppr,LandAcres"
ID_CHUNK = 250                  # arcgis.fetch_by_ids' chunk, so cached answers are shared

# Thresholds of the 2026-11-16 check (prototype builtcheck.py).
SQFT_PER_M2 = 10.7639
SLIVER_SF = 200                 # footprint pieces smaller than this along a shared line are ignored
HOLE_BUILT_SF = 2_500           # building in a hole of the parcel shape -> built
OUTLINE_BUILT_SF = 10_000       # footprints inside the outline -> built
OUTLINE_CHECK_SF = 2_500        # footprints inside the outline -> check
IMPROVEMENTS_BUILT_USD = 250_000  # a separate record inside the outline with this much -> built
INSIDE_SHARE = 0.8              # a record counts as inside when this share of it is
DEFAULT_TOP = 250

# Politeness (reviews/37 R14).
MIN_GAP_S = 1.0
MAX_HOST_FAILURES = 5
DEFAULT_CACHE_DAYS = 7.0        # arcgis' own default; never assigned globally

# Built-check states.
BUILT, CHECK, NONE_FOUND, NOT_CHECKED = "built", "check", "no building found", "not checked"
CHECKED, DEGRADED, FAILED = "checked", "degraded", "not checked"

# Aerial review vocabulary (the 2026-11-16 visual.json).
AERIAL_STATES = ("vacant_wooded", "vacant_open", "under_construction_or_graded", "partly_built",
                 "houses", "operating_building", "paved_yard_or_storage", "other_use", "unclear")
USABLE = {"yes": "raw_land", "partly": "partly_usable", "no": "not_raw_land", "unclear": "unclear"}
CONFIDENCE = ("high", "medium", "low")
# States in which the photo shows the ground open, or in use; a footprint
# signal that says the opposite sends the parcel to a second look.
OPEN_STATES = frozenset({"vacant_wooded", "vacant_open", "under_construction_or_graded",
                         "partly_built"})
USED_STATES = frozenset({"houses", "operating_building", "paved_yard_or_storage", "other_use"})
EVIDENCE_CAP = 600

SOURCE_AGREES = "aerial review (agrees with footprints)"
SOURCE_SECOND = "second look"
# A second look whose raw_land was held back because an input of the check
# failed: not carried by --second-looks-from as a reviewer's verdict.
SOURCE_SECOND_HELD = "second look (held: an input failed)"
SOURCE_PENDING = "needs a second look"
SOURCE_NONE = "none"

# Output columns: explicit allow-lists (reviews/37 R1).
BUILT_COLUMNS = ["rank", "parcel_id", "address", "jurisdiction", "acres", "lead_grade",
                 "built_check", "why", "footprints", "footprint_sf_in_outline",
                 "footprint_sf_in_holes", "largest_structure_sf", "largest_sf_in_outline",
                 "hole_acres", "improvement_value_roll", "footprint_state", "records_state"]
BUILT_JSON_EXTRA = ["occupancy", "imagery_dates", "records_inside", "records_note",
                    "footprint_note"]
RECORD_KEYS = ["parcel_id", "address", "class", "land_use", "improvements", "land", "acres_map"]
ADDRESS_WITHHELD = "(withheld)"
LAND_COLUMNS = ["rank", "parcel_id", "address", "jurisdiction", "acres", "lead_grade",
                "listed_lead", "decision", "land_today", "what_is_there", "why", "confidence",
                "source", "aerial_state", "footprint_check", "footprint_note"]


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

class HostStopped(arcgis.ArcGISError):
    """A host failed MAX_HOST_FAILURES times in a row and is not asked again."""


@dataclass
class Fetcher:
    """Polite access to the ArcGIS hosts, through the shared arcgis cache.

    A cached answer young enough is used without waiting. Otherwise the
    request waits until MIN_GAP_S has passed since the last request to that
    host. clock and sleep are injectable for tests."""
    cache_days: float = DEFAULT_CACHE_DAYS
    min_gap_s: float = MIN_GAP_S
    max_failures: int = MAX_HOST_FAILURES
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    last: dict[str, float] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    asked: Counter = field(default_factory=Counter)

    def get(self, url: str, params: dict[str, Any],
            check: Callable[[dict[str, Any]], str | None]) -> dict[str, Any]:
        host = urlsplit(url).netloc
        hit = arcgis.cached_json(url, params, max_age_days=self.cache_days, check=check)
        if hit is not None:
            return hit[0]
        if self.failures.get(host, 0) >= self.max_failures:
            raise HostStopped(f"{host}: stopped after {self.max_failures} failures in a row")
        wait = self.last.get(host, -1e18) + self.min_gap_s - self.clock()
        if wait > 0:
            self.sleep(wait)
        self.asked[host] += 1
        try:
            data = arcgis.request_json(url, params, check=check, max_age_days=self.cache_days)
        except arcgis.ArcGISError:
            self.failures[host] = self.failures.get(host, 0) + 1
            raise
        finally:
            self.last[host] = self.clock()
        self.failures[host] = 0
        return data


def _ids_problem(data: dict[str, Any]) -> str | None:
    if "objectIds" not in data:
        return "no objectIds in the answer"
    if not isinstance(data["objectIds"], (list, type(None))):
        return "objectIds is not a list"
    if data.get("exceededTransferLimit"):
        return "the id list was cut short"
    return None


def _features_problem(want: int | None, wgs84: bool = False) -> Callable[[dict[str, Any]], str | None]:
    """want: the number of features asked for (by id). wgs84: check the
    answer's spatial reference even when no count is known (the county
    records query: shapes in State Plane feet would never fall inside the
    outline)."""
    def problem(data: dict[str, Any]) -> str | None:
        feats = data.get("features")
        if not isinstance(feats, list):
            return "no features in the answer"
        if data.get("exceededTransferLimit"):
            return "the server cut the answer short"
        if want is not None and len(feats) < want:
            return f"{len(feats)} of {want} features came back"
        if (want is not None or wgs84) and feats:
            # As arcgis.fetch_by_ids checks: shapes must come back in WGS84.
            sr = data.get("spatialReference") if isinstance(data.get("spatialReference"), dict) else {}
            if 4326 not in (sr.get("wkid"), sr.get("latestWkid")):
                return "the features did not come back in WGS84"
        return None
    return problem


def _envelope_params(env: tuple[float, float, float, float]) -> dict[str, Any]:
    """The parameters arcgis.query_ids sends, so cached answers are shared."""
    xmin, ymin, xmax, ymax = env
    return {"where": "1=1", "returnIdsOnly": "true",
            "geometry": json.dumps({"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
                                    "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects"}


@dataclass
class Footprints:
    items: list[tuple[BaseGeometry, dict[str, Any]]]
    state: dict[str, str]          # parcel id -> CHECKED / DEGRADED / FAILED
    note: dict[str, str]


def fetch_footprints(fetcher: Fetcher, geoms: dict[str, BaseGeometry]) -> Footprints:
    """USA Structures footprints in each parcel's bounds. A parcel whose id
    query or any of whose footprint chunks failed is FAILED (not checked);
    one whose footprints lack SQFEET or OCC_CLS is DEGRADED."""
    url = f"{STRUCTURES_URL}/query"
    ids_of: dict[str, set[int]] = {}
    state: dict[str, str] = {}
    note: dict[str, str] = {}
    for pid, g in geoms.items():
        try:
            d = fetcher.get(url, _envelope_params(g.bounds), _ids_problem)
            ids_of[pid] = set(int(i) for i in (d["objectIds"] or []))
        except arcgis.ArcGISError as exc:
            state[pid], note[pid] = FAILED, f"footprint query failed: {str(exc)[:80]}"
    all_ids = sorted(set().union(*ids_of.values())) if ids_of else []
    items: list[tuple[BaseGeometry, dict[str, Any]]] = []
    failed_ids: set[int] = set()
    shapeless_ids: set[int] = set()
    for i in range(0, len(all_ids), ID_CHUNK):
        part = all_ids[i:i + ID_CHUNK]
        params = {"objectIds": ",".join(map(str, part)), "outFields": STRUCTURE_FIELDS,
                  "returnGeometry": "true", "outSR": 4326}
        try:
            d = fetcher.get(url, params, _features_problem(len(set(part))))
        except arcgis.ArcGISError:
            failed_ids.update(part)
            continue
        for f in d["features"]:
            s = esri_to_shape(f.get("geometry"))
            if s is not None and not s.is_empty:
                items.append((s, f.get("attributes") or {}))
            else:
                # A footprint without a usable shape could be the building on
                # the parcel: its parcels read not checked, never "no
                # building". The answer names no object id (STRUCTURE_FIELDS
                # is the prototype's, for a shared cache), so every id of
                # the chunk counts.
                a = f.get("attributes") or {}
                oid = next((a[k] for k in ("OBJECTID", "objectid", "FID") if isinstance(a.get(k), int)), None)
                shapeless_ids.update([oid] if oid is not None else part)
    for pid, ids in ids_of.items():
        if ids & failed_ids:
            state[pid], note[pid] = FAILED, "a footprint request failed"
        elif ids & shapeless_ids:
            state[pid], note[pid] = FAILED, "a footprint came back without a shape"
        else:
            state[pid] = CHECKED
    return Footprints(items, state, note)


def records_params(outline_geom: BaseGeometry) -> dict[str, Any]:
    """The county query of the prototype, byte for byte (shared cache)."""
    rings = ([list(map(list, outline_geom.exterior.coords))] if outline_geom.geom_type == "Polygon"
             else [list(map(list, p.exterior.coords)) for p in outline_geom.geoms])
    return {"geometry": json.dumps({"rings": rings, "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryPolygon", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": PARCEL_FIELDS,
            "returnGeometry": "true", "outSR": 4326}


def fetch_records(fetcher: Fetcher, outline_geom: BaseGeometry) -> list[dict[str, Any]]:
    """County parcel features touching the outline (raises ArcGISError)."""
    d = fetcher.get(f"{PARCELS_URL}/query", records_params(outline_geom), _features_problem(None, wgs84=True))
    return d["features"]


# --------------------------------------------------------------------------
# Measuring (pure)
# --------------------------------------------------------------------------

def outline(g: BaseGeometry) -> BaseGeometry:
    """The parcel with its holes filled in."""
    parts = [g] if g.geom_type == "Polygon" else [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
    return unary_union([Polygon(p.exterior) for p in parts])


def _image_date(v: Any) -> str | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    m = re.match(r"\d{4}-\d{2}-\d{2}", str(v))
    return m.group(0) if m else None


def measure_footprints(g: BaseGeometry, items: list[tuple[BaseGeometry, dict[str, Any]]],
                       tree: shapely.STRtree | None = None) -> dict[str, Any]:
    """Footprint numbers for one parcel (the prototype's measurement)."""
    o = outline(g)
    pj = LocalProjection.around(g)
    hole = o.difference(g)
    hole_ac = round(acres(pj(hole)), 2) if not hole.is_empty else 0.0
    tree = tree if tree is not None else shapely.STRtree([s for s, _ in items])
    on, in_hole, n, largest, largest_in, occ = 0.0, 0.0, 0, None, 0, set()
    degraded, dates = False, []
    for i in tree.query(o):
        s, a = items[int(i)]
        if not s.intersects(o):
            continue
        a_sf = pj(s.intersection(o)).area * SQFT_PER_M2
        if a_sf < SLIVER_SF:
            continue
        n += 1
        on += a_sf
        largest_in = max(largest_in, round(a_sf))
        if not hole.is_empty:
            in_hole += pj(s.intersection(hole)).area * SQFT_PER_M2
        if "SQFEET" not in a or "OCC_CLS" not in a:
            degraded = True
        if a.get("SQFEET") is not None:
            try:
                largest = max(largest or 0, int(float(a["SQFEET"])))
            except (TypeError, ValueError):
                degraded = True
        occ.add(a.get("OCC_CLS") or "?")
        d = _image_date(a.get("IMAGE_DATE"))
        if d:
            dates.append(d)
    if n == 0:
        largest = 0
    return {"footprints": n, "footprint_sf_in_outline": round(on),
            "footprint_sf_in_holes": round(in_hole), "largest_structure_sf": largest,
            "largest_sf_in_outline": largest_in, "occupancy": sorted(occ), "hole_acres": hole_ac,
            "imagery_dates": [min(dates), max(dates)] if dates else None, "degraded": degraded}


def records_inside(pid: str, g: BaseGeometry, feats: list[dict[str, Any]]
                   ) -> tuple[list[dict[str, Any]], Any, str | None]:
    """(other records mostly inside the outline, the parcel's own ImprAppr,
    a degradation note or None). A missing ImprAppr is None, never $0."""
    o = outline(g)
    pj = LocalProjection.around(g)
    inside, own, problems = [], None, []
    seen_own = False
    for f in feats:
        a = f.get("attributes") or {}
        if a.get("ParcelID") == pid:
            own = a.get("ImprAppr")
            seen_own = True
            continue
        s = esri_to_shape(f.get("geometry"))
        if s is None or s.is_empty:
            problems.append("a county record came back without a shape")
            continue
        share = s.intersection(o).area / s.area if s.area else 0
        if share >= INSIDE_SHARE:
            if "ImprAppr" not in a:
                problems.append("a record inside has no improvement field")
            inside.append({"parcel_id": a.get("ParcelID"), "address": a.get("Address"),
                           "class": a.get("ClassCode"), "land_use": a.get("LUCode"),
                           "improvements": a.get("ImprAppr"), "land": a.get("LandAppr"),
                           "acres_map": round(acres(pj(s)), 2)})
    if not seen_own:
        # The parcel's own record always touches its outline: an answer
        # without it is a broken query, not "nothing inside".
        problems.append("the parcel's own county record did not come back")
    return inside, own, ("; ".join(sorted(set(problems))) or None)


def _money(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def verdict(x: dict[str, Any], imagery: str | None = None) -> tuple[str, list[str]]:
    """built / check / no building found / not checked, and why.

    The prototype's rule, plus: an input that was not checked (a failed
    fetch, a host stopped, a record without a shape) turns "no building
    found" into "not checked"; a positive finding still stands."""
    why = []
    rec = x.get("records_inside") if isinstance(x.get("records_inside"), list) else []
    if x["footprint_sf_in_holes"] >= HOLE_BUILT_SF:
        why.append(f"{x['footprint_sf_in_holes']:,} SF of building in a hole of the parcel shape")
    if x["footprint_sf_in_outline"] >= OUTLINE_BUILT_SF:
        why.append(f"{x['footprint_sf_in_outline']:,} SF of building footprints inside the outline")
    big = [r for r in rec if (_money(r.get("improvements")) or 0) >= IMPROVEMENTS_BUILT_USD]
    if big:
        why.append("separate tax record(s) inside the outline with improvements: "
                   + "; ".join(f"{r['parcel_id']} ${_money(r['improvements']):,.0f}" for r in big))
    if why:
        return BUILT, why
    if x["footprint_sf_in_outline"] >= OUTLINE_CHECK_SF or rec:
        return CHECK, [f"{x['footprint_sf_in_outline']:,} SF of footprints in the outline"
                       if x["footprint_sf_in_outline"] else "",
                       f"{len(rec)} other tax record(s) inside the outline" if rec else ""]
    missing = [n for n in (x.get("footprint_note"), x.get("records_note")) if n]
    if x.get("footprint_state") == FAILED or x.get("records_state") == FAILED or (
            x.get("records_state") == DEGRADED):
        return NOT_CHECKED, missing or ["an input was not checked"]
    return NONE_FOUND, [f"no mapped footprint in the outline (USA Structures imagery {imagery})"
                        if imagery else "no mapped footprint in the outline"]


# --------------------------------------------------------------------------
# Aerial review and land today (pure)
# --------------------------------------------------------------------------

def _int_pct(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
        raise Refused(f"{where}: a share must be a number from 0 to 100")
    return int(v)


VISUAL_KEYS = {"rank", "parcel_id", "state", "usable_as_raw_land", "building_share_pct",
               "paved_share_pct", "evidence", "confidence"}
SECOND_KEYS = {"rank", "parcel_id", "land_today", "what_is_there", "why", "confidence"}


def load_visual(data: Any, rows: list[dict[str, str]], identity: dict[str, str],
                by_rank: bool) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """(first look, second look) keyed by parcel id, checked against a
    fixed schema and joined to this run (reviews/37 R9, R10, R27).

    Refused: an unknown key or enum, a share outside 0-100, a rank not in
    the run, a parcel id that does not match its rank, a file that names
    another run, and a file with neither a run nor parcel ids unless
    by_rank (the operator vouches it was made from this run). Free text is
    cut to EVIDENCE_CAP characters."""
    if not isinstance(data, dict) or not isinstance(data.get("visual"), dict):
        raise Refused("visual file: expected an object with a 'visual' object")
    extra = set(data) - {"visual", "second_look", "run", "schema_version", "note"}
    if extra:
        raise Refused(f"visual file: unknown top-level keys {sorted(extra)}")
    run = data.get("run")
    if run is not None:
        if not isinstance(run, dict) or run.get("ranked_csv_sha256") != identity["ranked_csv_sha256"]:
            raise Refused("visual file was made from another run (ranked.csv differs)")
    rank_to_pid = {r["rank"]: r["parcel_id"] for r in rows}

    def join(key: str, e: Any, keys: set[str], what: str) -> str:
        if not isinstance(e, dict):
            raise Refused(f"{what} {key}: not an object")
        if set(e) - keys:
            raise Refused(f"{what} {key}: unknown keys {sorted(set(e) - keys)}")
        if str(e.get("rank")) != str(key) or str(key) not in rank_to_pid:
            raise Refused(f"{what} {key}: rank not in this run's ranked.csv")
        pid = rank_to_pid[str(key)]
        if "parcel_id" in e:
            if e["parcel_id"] != pid:
                raise Refused(f"{what} {key}: names another parcel than this run's rank {key}")
        elif run is None and not by_rank:
            raise Refused(f"{what} {key}: no parcel id and no run named; pass --visual-by-rank "
                          "if you know it was made from this run")
        if e.get("confidence") not in CONFIDENCE:
            raise Refused(f"{what} {key}: unknown confidence")
        return pid

    first: dict[str, dict[str, Any]] = {}
    for key, e in data["visual"].items():
        pid = join(key, e, VISUAL_KEYS, "visual")
        if e.get("state") not in AERIAL_STATES:
            raise Refused(f"visual {key}: unknown state")
        if e.get("usable_as_raw_land") not in USABLE:
            raise Refused(f"visual {key}: unknown usable_as_raw_land")
        if not isinstance(e.get("evidence", ""), str):
            raise Refused(f"visual {key}: evidence is not text")
        first[pid] = {"state": e["state"], "usable": e["usable_as_raw_land"],
                      "building_share_pct": _int_pct(e.get("building_share_pct", 0), f"visual {key}"),
                      "paved_share_pct": _int_pct(e.get("paved_share_pct", 0), f"visual {key}"),
                      "evidence": cap(e.get("evidence") or "", EVIDENCE_CAP),
                      "confidence": e["confidence"]}
    second: dict[str, dict[str, Any]] = {}
    for key, e in (data.get("second_look") or {}).items():
        pid = join(key, e, SECOND_KEYS, "second_look")
        if e.get("land_today") not in LAND_CLASSES:
            raise Refused(f"second_look {key}: unknown land_today")
        for k in ("what_is_there", "why"):
            if not isinstance(e.get(k, ""), str):
                raise Refused(f"second_look {key}: {k} is not text")
        second[pid] = {"land_today": e["land_today"],
                       "what_is_there": cap(e.get("what_is_there") or "", EVIDENCE_CAP),
                       "why": cap(e.get("why") or "", EVIDENCE_CAP), "confidence": e["confidence"]}
    return first, second


def needs_second_look(footprint_check: str, v: dict[str, Any]) -> bool:
    """The photo and the footprints disagree, or the photo is unsure."""
    if v["state"] == "unclear" or v["confidence"] == "low":
        return True
    if footprint_check == BUILT and v["state"] in OPEN_STATES:
        return True
    if footprint_check == NONE_FOUND and v["state"] in USED_STATES:
        return True
    return False


def inputs_complete(x: dict[str, Any]) -> bool:
    """Every input of the built check ran (no failed footprint or records
    fetch, no degraded records answer). A verdict made with an input
    missing can still say built or check; it can never support raw_land."""
    return not (x.get("footprint_state") == FAILED or x.get("records_state") in (FAILED, DEGRADED))


def classify_land(footprint_check: str, v: dict[str, Any] | None,
                  second: dict[str, Any] | None, complete: bool = True) -> dict[str, str]:
    """land_today, what is there, why, confidence, source.

    raw_land only from a photo review: the first look when it agrees with
    a completed footprint check that is not "built", or a second look
    (which saw the footprints) when the check ran. Anything else that
    would be raw_land is "unclear". `complete` False (an input of the
    built check failed, whatever the verdict) is read like a check that
    did not run: a missing records or footprint answer could have made
    the parcel built."""
    if v is None and second is None:
        return {"land_today": "unclear", "what_is_there": "no aerial review", "why": "",
                "confidence": "", "source": SOURCE_NONE, "aerial_state": ""}
    state = v["state"] if v else ""
    if footprint_check == NOT_CHECKED or not complete:
        # The footprints could not be compared: keep a negative photo finding,
        # never a positive one. Without a second look, a first look that
        # reads raw_land or partly_usable is "unclear" (a completed check
        # could have sent it to a second look); only not_raw_land is kept,
        # so a failed fetch never reads better than a completed check.
        base = second["land_today"] if second else USABLE[v["usable"]]
        if second:
            land = "unclear" if base == "raw_land" else base
        else:
            land = base if base == "not_raw_land" else "unclear"
        what = second["what_is_there"] if second else v["evidence"]
        why = "footprint check not completed" if footprint_check == NOT_CHECKED else "an input of the built check failed"
        return {"land_today": land, "what_is_there": what,
                "why": why + ("" if land == base else f": {base.replace('_', ' ')} not confirmed"),
                "confidence": (second or v)["confidence"],
                "source": (SOURCE_SECOND if land == base else SOURCE_SECOND_HELD) if second else SOURCE_PENDING,
                "aerial_state": state}
    if second is not None:
        return {"land_today": second["land_today"], "what_is_there": second["what_is_there"],
                "why": second["why"], "confidence": second["confidence"],
                "source": SOURCE_SECOND, "aerial_state": state}
    land = USABLE[v["usable"]]
    if needs_second_look(footprint_check, v) or (land == "raw_land" and footprint_check == BUILT):
        return {"land_today": "unclear", "what_is_there": v["evidence"],
                "why": f"the photo review (raw land: {v['usable']}; {state}) and the footprints "
                       f"({footprint_check}) disagree, or the photo is unsure: a second look decides",
                "confidence": v["confidence"], "source": SOURCE_PENDING, "aerial_state": state}
    return {"land_today": land, "what_is_there": v["evidence"], "why": "",
            "confidence": v["confidence"], "source": SOURCE_AGREES, "aerial_state": state}


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def listed_ranks(run_dir: Path) -> set[str]:
    """Ranks on the run's lead list (leads.md table rows "| N |"). Only the
    rank column is read."""
    path = Path(run_dir) / "leads.md"
    if not path.exists():
        return set()
    return set(re.findall(r"^\| (\d+) \|", path.read_text(encoding="utf-8"), re.M))


def load_geoms(run_dir: Path) -> dict[str, BaseGeometry]:
    data = json.loads((Path(run_dir) / "ranked.geojson").read_text(encoding="utf-8"))
    out = {}
    for f in data.get("features", []):
        pid = (f.get("properties") or {}).get("parcel_id")
        if pid and f.get("geometry"):
            out[pid] = shape(f["geometry"])
    return out


@dataclass
class Result:
    built: list[dict[str, Any]]
    land: list[dict[str, Any]]
    meta: dict[str, Any]


def run_check(run_dir: Path, *, top: int = DEFAULT_TOP, visual: Any = None,
              visual_by_rank: bool = False, decisions: dict[str, str] | None = None,
              fetcher: Fetcher | None = None) -> Result:
    """The built check and land_today for the top `top` ranked parcels."""
    run_dir = Path(run_dir)
    fetcher = fetcher or Fetcher()
    identity = run_identity(run_dir)
    rows = sorted(read_ranked(run_dir), key=lambda r: int(r["rank"]))[:max(0, top)]
    first, second = (load_visual(visual, read_ranked(run_dir), identity, visual_by_rank)
                     if visual is not None else ({}, {}))
    geoms_all = load_geoms(run_dir)
    geoms = {r["parcel_id"]: geoms_all[r["parcel_id"]] for r in rows if r["parcel_id"] in geoms_all}
    fp = fetch_footprints(fetcher, geoms)
    tree = shapely.STRtree([s for s, _ in fp.items])
    all_dates: list[str] = []
    built: list[dict[str, Any]] = []
    for r in rows:
        pid = r["parcel_id"]
        x: dict[str, Any] = {"rank": int(r["rank"]), "parcel_id": pid, "address": r["site_address"],
                             "jurisdiction": r["jurisdiction"], "acres": float(r["acres"]),
                             "lead_grade": r["lead_grade"], "improvement_value_roll": None}
        g = geoms.get(pid)
        if g is None:
            x.update({"footprints": 0, "footprint_sf_in_outline": 0, "footprint_sf_in_holes": 0,
                      "largest_structure_sf": None, "largest_sf_in_outline": 0, "occupancy": [],
                      "hole_acres": 0.0, "imagery_dates": None, "footprint_state": FAILED,
                      "footprint_note": "no parcel shape in ranked.geojson",
                      "records_state": FAILED, "records_inside": None,
                      "records_note": "no parcel shape in ranked.geojson"})
            built.append(x)
            continue
        m = measure_footprints(g, fp.items, tree)
        degraded = m.pop("degraded")
        x.update(m)
        st = fp.state.get(pid, FAILED)
        x["footprint_state"] = DEGRADED if st == CHECKED and degraded else st
        x["footprint_note"] = fp.note.get(pid) or (
            "a footprint lacks SQFEET or OCC_CLS" if x["footprint_state"] == DEGRADED else None)
        if m["imagery_dates"]:
            all_dates += m["imagery_dates"]
        try:
            feats = fetch_records(fetcher, outline(g))
            inside, own, problem = records_inside(pid, g, feats)
            x["records_inside"], x["improvement_value_roll"] = inside, own
            x["records_state"], x["records_note"] = (DEGRADED, problem) if problem else (CHECKED, None)
        except (arcgis.ArcGISError, GEOSException) as exc:
            x["records_inside"], x["records_state"] = None, FAILED
            x["records_note"] = f"county records query failed: {str(exc)[:80]}"
        built.append(x)
    imagery = f"{min(all_dates)[:4]}-{max(all_dates)[:4]}" if all_dates else None
    for x in built:
        v, why = verdict(x, imagery)
        x["built_check"], x["why"] = v, "; ".join(w for w in why if w)

    listed = listed_ranks(run_dir)
    by_pid = {r["parcel_id"]: r for r in rows}
    land = []
    for x in built:
        pid = x["parcel_id"]
        r = by_pid[pid]
        complete = inputs_complete(x)
        c = classify_land(x["built_check"], first.get(pid), second.get(pid), complete)
        note = x["why"]
        if not complete:
            # the failure shows in land_today too, not only in builtcheck.json
            note = "; ".join(n for n in (note, x.get("footprint_note"), x.get("records_note")) if n)
        dec = (decisions or {}).get(pid) or r.get("decision") or ""
        land.append({"rank": x["rank"], "parcel_id": pid, "address": x["address"],
                     "jurisdiction": x["jurisdiction"], "acres": x["acres"],
                     "lead_grade": x["lead_grade"], "listed_lead": r["rank"] in listed,
                     "decision": dec, **{k: c[k] for k in ("land_today", "what_is_there", "why",
                                                           "confidence", "source", "aerial_state")},
                     "footprint_check": x["built_check"], "footprint_note": note})
    meta = {"checked": date.today().isoformat(), "top": top,
            "sources": {"structures": STRUCTURES_URL, "structure_fields": STRUCTURE_FIELDS,
                        "parcels": PARCELS_URL, "parcel_fields": PARCEL_FIELDS},
            "structures_imagery": [min(all_dates), max(all_dates)] if all_dates else None,
            "thresholds": {"hole_built_sf": HOLE_BUILT_SF, "outline_built_sf": OUTLINE_BUILT_SF,
                           "outline_check_sf": OUTLINE_CHECK_SF,
                           "improvements_built_usd": IMPROVEMENTS_BUILT_USD,
                           "inside_share": INSIDE_SHARE, "sliver_sf": SLIVER_SF},
            "aerial_review": ("given" if visual is not None else "none: no parcel can read raw_land"),
            "requests_sent": dict(fetcher.asked)}
    return Result(built, land, meta)


def _rows_csv(columns: list[str], records: Iterable[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for x in records:
        w.writerow([csv_cell("" if x.get(c) is None else x.get(c)) for c in columns])
    return buf.getvalue()


def _record_out(r: dict[str, Any], guard: OwnerGuard | None) -> dict[str, Any]:
    out = {k: r.get(k) for k in RECORD_KEYS}
    # The site address of a record inside the outline is public county data,
    # but when it reads as an owner's mailing address (an owner-occupied
    # house) it is withheld: no output carries an owner mailing string, even
    # one that is also a site address (reviews/37 R1). The parcel id stays.
    if guard is not None and isinstance(out.get("address"), str) and guard.hits(out["address"]):
        out["address"] = ADDRESS_WITHHELD
    return out


def artifacts(result: Result, identity: dict[str, str],
              guard: OwnerGuard | None = None) -> dict[str, str]:
    """The four output files' text, from allow-listed fields only."""
    head = {"schema_version": SCHEMA_VERSION, "run": identity, "meta": result.meta}
    built = [{**{c: x.get(c) for c in BUILT_COLUMNS + BUILT_JSON_EXTRA},
              "records_inside": ([_record_out(r, guard) for r in x["records_inside"]]
                                 if isinstance(x.get("records_inside"), list) else None)}
             for x in result.built]
    land = [{c: x.get(c) for c in LAND_COLUMNS} for x in result.land]
    return {
        "builtcheck.json": json.dumps({**head, "parcels": built}, indent=1, ensure_ascii=False) + "\n",
        "builtcheck.csv": _rows_csv(BUILT_COLUMNS, built),
        "land_today.json": json.dumps({**head, "parcels": land}, indent=1, ensure_ascii=False) + "\n",
        "land_today.csv": _rows_csv(LAND_COLUMNS, land),
    }


def write_outputs(result: Result, run_dir: Path, out: Path) -> list[Path]:
    """Guard every file for owner data first, then write them all atomically;
    a refusal writes nothing. A re-run replaces the four files."""
    guard = OwnerGuard.from_run(run_dir)
    files = artifacts(result, run_identity(run_dir), guard)
    # The checked parcels' own site addresses are written in every file.
    public = [x.get("address") or "" for x in result.built]
    for name, body in files.items():
        guard.check(body, name, public)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, body in files.items():
        atomic_write(out / name, body)
        paths.append(out / name)
    return paths


def second_looks_from(data: Any, path: str = "land_today file") -> dict[str, dict[str, Any]]:
    """The second-look verdicts of an earlier land_today (this tool's
    land_today.json, or the 2026-11-16 one, a bare list), as a visual
    file's ``second_look`` section keyed by rank with parcel ids. Only rows
    whose source is SOURCE_SECOND are taken (a SOURCE_SECOND_HELD row, held
    back because an input failed, is not a reviewer's verdict: carry it from
    the original file); load_visual then checks each rank and parcel id
    against this run (a file that names another run is refused there and
    here)."""
    items = data.get("parcels") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise Refused(f"{path}: no list of parcels")
    out: dict[str, dict[str, Any]] = {}
    for x in items:
        if not isinstance(x, dict) or x.get("source") != SOURCE_SECOND:
            continue
        if not isinstance(x.get("parcel_id"), str) or x.get("rank") is None:
            raise Refused(f"{path}: a second look without rank and parcel id")
        out[str(x["rank"])] = {"rank": x["rank"], "parcel_id": x["parcel_id"],
                               **{k: x.get(k) for k in ("land_today", "what_is_there", "why", "confidence")}}
    return out


def with_second_looks(visual: Any, earlier: Any, identity: dict[str, str], path: str) -> dict[str, Any]:
    """visual.json with the second looks of an earlier land_today added
    (an entry already in visual.json's own second_look wins)."""
    if not isinstance(visual, dict):
        raise Refused("--second-looks-from needs --visual (the first looks it goes with)")
    if isinstance(earlier, dict):
        run = earlier.get("run")
        if isinstance(run, dict) and run.get("ranked_csv_sha256") not in (None, identity["ranked_csv_sha256"]):
            raise Refused(f"{path} was made from another run (ranked.csv differs)")
    carried = second_looks_from(earlier, path)
    if not carried:
        raise Refused(f"{path}: no 'second look' rows to carry (is it a land_today.json?)")
    items = earlier.get("parcels") if isinstance(earlier, dict) else earlier
    held = sum(1 for x in items if isinstance(x, dict) and x.get("source") == SOURCE_SECOND_HELD)
    if held:
        print(f"builtcheck: note: {held} second look(s) in {path} were held back by a failed input and are "
              "not carried; carry them from the file they were first recorded in", file=sys.stderr)
    merged = dict(visual)
    merged["second_look"] = {**carried, **(visual.get("second_look") or {})}
    return merged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m screener.builtcheck",
                                 description="Built check and land today for a screener run.")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--top", type=int, default=DEFAULT_TOP, help="parcels to check, by rank")
    ap.add_argument("--visual", type=Path, help="the aerial review (visual.json); without it "
                    "no parcel reads raw_land")
    ap.add_argument("--visual-by-rank", action="store_true",
                    help="accept a visual file with no run and no parcel ids, joined by rank")
    ap.add_argument("--second-looks-from", type=Path,
                    help="an earlier land_today.json whose 'second look' rows are carried into this check "
                         "(joined by rank and parcel id; refused when it names another run)")
    ap.add_argument("--decisions", type=Path, help="decisions.csv (only the decision value is used)")
    ap.add_argument("--cache-days", type=float, default=DEFAULT_CACHE_DAYS,
                    help="reuse cached map answers up to this age")
    args = ap.parse_args(argv)
    try:
        if args.top < 1:
            raise Refused("--top must be 1 or more")
        if not (math.isfinite(args.cache_days) and args.cache_days >= 0):
            # nan would keep every cached answer forever (no age check passes).
            raise Refused("--cache-days must be a number of days, 0 or more")
        out = check_out_path(args.out, args.run_dir)
        try:
            # Every input is read before any fetch or write.
            identity = run_identity(args.run_dir)
            read_ranked(args.run_dir)
            load_geoms(args.run_dir)
            visual = json.loads(args.visual.read_text(encoding="utf-8")) if args.visual else None
            if args.second_looks_from:
                earlier = json.loads(args.second_looks_from.read_text(encoding="utf-8"))
                visual = with_second_looks(visual, earlier, identity, str(args.second_looks_from))
            decisions = None
            if args.decisions:
                with open(args.decisions, encoding="utf-8-sig", newline="") as fh:
                    decisions = {r["parcel_id"]: (r.get("decision") or "").strip()
                                 for r in csv.DictReader(fh) if r.get("parcel_id")}
        except (OSError, ValueError, KeyError, csv.Error) as exc:
            # A missing or unreadable input (no ranked.csv, a --visual or
            # --decisions file that is not there or not valid JSON / CSV).
            raise Refused(f"cannot read an input ({type(exc).__name__}: {str(exc)[:200]})") from None
        result = run_check(args.run_dir, top=args.top, visual=visual,
                           visual_by_rank=args.visual_by_rank, decisions=decisions,
                           fetcher=Fetcher(cache_days=args.cache_days))
        write_outputs(result, args.run_dir, out)
    except Refused as exc:
        print(f"builtcheck: refused: {exc}", file=sys.stderr)
        return 2
    counts = Counter(x["built_check"] for x in result.built)
    land = Counter(x["land_today"] for x in result.land)
    print(f"{out}: {len(result.built)} parcels; built check {dict(counts)}; land today {dict(land)}")
    not_checked = counts.get(NOT_CHECKED, 0)
    partial = sum(1 for x in result.built if x["built_check"] != NOT_CHECKED and not inputs_complete(x))
    if not_checked or partial:
        print(f"builtcheck: {not_checked} parcel(s) not checked and {partial} checked with an input missing "
              "(a fetch failed; none of them reads raw_land); re-run later", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
