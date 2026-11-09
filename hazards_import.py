"""Import FEMA NFHL flood zones and USFWS NWI wetlands per parcel.

Operator tool, run BETWEEN runs only (never by the agent, never mid-run):

    python hazards_import.py                 make hazards-import
    python hazards_import.py --mode rest     make hazards-import ARGS="--mode rest"

Design and risk register: reviews/26_flood_wetlands/. It writes ONLY the
proposed ``parcel_hazards`` table, and refuses (exit 1) until a human has
added that table to prepare.py through the prepare-mutation protocol
(``hazards.enabled()``, reviews/26_flood_wetlands/01_prepare_mutation_
proposal.md). The first populated import changes H4/H6 and site fit, so the
metric: import before a new run's baseline, never after it. It refuses
(exit 3) once the TSV or the database's experiment_log_mirror shows the
run's baseline (zoning_import's guard, read-only).

Two data routes
---------------
``--mode files`` (the default): the operator downloads, in a browser, the
NFHL county package for Fulton (13121C) from the FEMA Map Service Center
and the NWI state shapefile for Georgia from the USFWS download page, and
unpacks them under the gitignored ``hazards_inbox/``:

    hazards_inbox/nfhl/S_FLD_HAZ_AR.shp (.dbf, .prj; optional S_LOMR.*)
    hazards_inbox/nwi/<any name>.shp    (.dbf, .prj; exactly one .shp, or
                                         several with one *_Wetlands.shp)

They are read with a pure standard-library .shp/.dbf reader (no new
dependency, SR-10): polygon records only, ring orientation as the format
defines it (outer rings clockwise, holes counter-clockwise), the
coordinate system from the .prj (NAD83 geographic, WGS84 geographic or
NAD83 Conus Albers; anything else, Web Mercator included, is refused).

``--mode rest``: the public ArcGIS services (NFHL MapServer layer 28 and the
LOMR layer located by name; NWI Wetlands MapServer layer 0), through
rules_scrape's robots.txt parser and fetch policy UNCHANGED (FW-01): robots
for this tool's own token and the AI-agent tokens, checked before any query;
404/410 allows all, anything that is not a clean text 2xx denies all, a
redirect off the registrable domain denies all. On 2026-11-06 both hosts
deny the query paths (hazards.fema.gov: ``Disallow: /arcgis`` and
``Disallow: /*?*``; fwspublicservices.wim.usgs.gov answers 301 to an HTML
page on www.fws.gov), so this mode fails both sources with exit 4 and
makes no data request. It exists for the day the hosts allow it. The robots
verdict is the ONE shared rules_scrape.robots_verdict; its only exception
(operator decision D1, reviews/29_operator_decisions, 2026-11-09: an ArcGIS
Online services host whose robots.txt answers a plain 401/403) never applies
to a readable robots.txt, so neither FEMA nor USFWS is read.

What it computes (in PostGIS, SRID 2240 US survey feet)
-------------------------------------------------------
Per parcel (Fulton, geometry inside EPSG:2240's area of use), over the
SAME made-valid parcel geometry as site_fit and S2: the union per NFHL
category with SiteFit's precedence (floodway, then SFHA including floodway,
then the 0.2% zone outside the SFHA), so categories are disjoint and no
share exceeds 100%; per-zone-key union areas; the area no NFHL polygon
covers or Zone D / AREA NOT INCLUDED covers; whether the union of the LOMR
polygons meets the parcel by at least 1 SF, and per zone key the part of
that zone inside the LOMR union (``nfhl_zone_lomr_sf``: AE_with_LOMR relaxes
AE only when the LOMRs cover the parcel's whole AE area); the highest static BFE (the -9999
sentinel dropped); the union of every NWI polygon and per WETLAND_TYPE; the
clipped SFHA and wetland geometry for the site-fit deduction; and
md5(ST_AsBinary(geometry)) of the parcel as stored, so a later geometry
change reads as stale. Service fields Shape_Area, Shape_Length and ACRES
are never requested or read.

Writes: one transaction per source, committed only after every page of
that source arrived and every computation succeeded; that source's column
group of ``parcel_hazards`` is replaced in full for every covered parcel
(UPSERT; a row whose content is unchanged is not touched, so a re-run with
the same inputs changes 0 rows, SR-9). A failed source keeps all of its
previous values and the other source still commits.

Output: counts only (parcels, features, share bands, H4/H6 outcomes, request
counts) -- never a parcel id, an owner, an address or a feature attribute.

Exit codes: 0 imported; 1 refused (hazard table not enabled or not in the
database, bad arguments); 2 no database / database error (it can follow a
partial commit: the last line then names the source(s) already committed
and the one(s) not written); 3 a run has its
baseline; 4 at least one source FAILED (named on the last line; its
previous values kept), robots refusals included.

Imports prepare, hazards, zoning_import (the run-state guard) and
rules_scrape (the robots policy) -- never research.py or runner.py.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlencode, urlparse

import requests

import hazards
import prepare
import rules_scrape
import zoning_import

REPO_ROOT = Path(__file__).resolve().parent
INBOX = REPO_ROOT / "hazards_inbox"
EXPERIMENT_LOG = REPO_ROOT / "experiment_log.tsv"

EXIT_OK, EXIT_REFUSED, EXIT_DB, EXIT_RUN_OPEN, EXIT_SOURCE_FAILED = 0, 1, 2, 3, 4

# --- HTTP identity (generic, no personal contact) --------------------------
USER_AGENT = ("Land-Research-HazardsImport/1.0 (between-runs operator tool; "
              "1 req/s per host; honours robots.txt)")
UA_TOKEN = "Land-Research-HazardsImport"
MAX_REQUESTS = 3000

NFHL_SERVICE = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
NFHL_LAYER = 28
NFHL_LAYER_NAME = "Flood Hazard Zones"
LOMR_LAYER_NAME = "LOMRs"
NWI_SERVICE = ("https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/"
               "Wetlands/MapServer")
NWI_LAYER = 0
NWI_LAYER_NAME = "Wetlands"
NFHL_FIELDS = ("FLD_ZONE", "ZONE_SUBTY", "SFHA_TF", "STATIC_BFE", "DFIRM_ID", "VERSION_ID")
NWI_FIELDS = ("WETLAND_TYPE", "ATTRIBUTE")
GEOMETRY_PRECISION = 7          # decimal degrees: ~1 cm
TILE_DEG = 0.02
OID_CHUNK = 100

NFHL_FILE = "S_FLD_HAZ_AR"
LOMR_FILE = "S_LOMR"
SOURCES = ("nfhl", "nwi")
SHARE_BANDS = ((">0-0.1%", 0, 0.1), ("0.1-1%", 0.1, 1), ("1-5%", 1, 5), ("5-20%", 5, 20),
               (">20%", 20, None))


class Refused(Exception):
    """Operator-facing refusal (exit 1)."""


class SourceError(Exception):
    """One source failed. The message names a host or file, a status and counts only."""


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
@dataclass
class Feature:
    fid: str
    attrs: dict[str, Any]
    geojson: str
    srid: int


@dataclass
class SourceData:
    key: str
    features: list[Feature] = field(default_factory=list)
    lomr: list[Feature] | None = None      # None = LOMR layer not read
    as_of: str = ""
    origin: str = ""
    covered: list[str] = field(default_factory=list)
    failed: str | None = None


def sort_features(feats: Iterable[Feature]) -> list[Feature]:
    """Deduplicate by id and sort, so server order never changes a row (FW-24)."""
    seen: dict[str, Feature] = {}
    for f in feats:
        seen.setdefault(f.fid, f)

    def key(f: Feature) -> tuple:
        return (0, int(f.fid), "") if f.fid.isdigit() else (1, 0, f.fid)
    return [seen[k] for k in sorted(seen, key=lambda k: key(seen[k]))]


# ---------------------------------------------------------------------------
# HTTP: rules_scrape's robots parser and fetch policy, this tool's identity
# ---------------------------------------------------------------------------
class HazardsFetcher(rules_scrape.PoliteFetcher):
    """rules_scrape.PoliteFetcher (robots for every token, 1 req/s per host,
    bounded retries, Retry-After capped, manual redirects, no cookies) with
    this tool's User-Agent and robots token. The robots policy itself
    (``robots_for``) is inherited unchanged and the verdict is the shared
    rules_scrape.robots_verdict (D1); no per-host override exists here."""

    robots_token = UA_TOKEN

    def __init__(self, http: Any = None, **kw: Any) -> None:
        kw.setdefault("max_requests", MAX_REQUESTS)
        super().__init__(http, **kw)
        if hasattr(self.http, "headers"):
            self.http.headers.clear()
            self.http.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json",
                                      "Accept-Encoding": "gzip, deflate"})

    def _raw_get(self, url: str) -> Any:
        host = urlparse(url).hostname or "?"
        if rules_scrape.host_denied(host) or rules_scrape.url_terms_blocked(url):
            raise rules_scrape.Unreachable("denylisted host")
        if self.total() >= self.max_requests:
            raise rules_scrape.BudgetExceeded(f"request budget of {self.max_requests} spent")
        self._space(host)
        self.requests[host] = self.requests.get(host, 0) + 1
        self.calls.append(url)
        return self.http.get(url, timeout=rules_scrape.HTTP_TIMEOUT_S, allow_redirects=False,
                             stream=True, headers={"User-Agent": USER_AGENT})

    def _check(self, url: str) -> None:
        p = urlparse(url)
        if p.scheme != "https" or not p.hostname:
            raise rules_scrape.Unreachable("not an https URL")
        rb = self.robots_for(f"https://{p.netloc}")
        why = rules_scrape.robots_verdict(rb, rules_scrape.robots_path(url), UA_TOKEN)
        if why:
            raise rules_scrape.Unreachable(why)
        rules_scrape.note_robots_exception(self, p.hostname, rb)

    def data_calls(self) -> list[str]:
        return [u for u in self.calls if not u.endswith("/robots.txt")]

    def get_json(self, url: str, params: Mapping[str, Any]) -> dict[str, Any]:
        full = url + ("?" + urlencode(dict(params)) if params else "")
        host = urlparse(url).hostname or "?"
        try:
            got = self.fetch(full)
        except rules_scrape.Unreachable as exc:
            raise SourceError(f"{host}: {exc.reason}") from None
        except rules_scrape.BudgetExceeded as exc:
            raise SourceError(f"{host}: {exc}") from None
        try:
            body = json.loads(got.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise SourceError(f"{host}: answer is not JSON") from None
        if not isinstance(body, dict):
            raise SourceError(f"{host}: answer is not a JSON object")
        if "error" in body:
            raise SourceError(f"{host}: service error")
        return body


# reviews/29_operator_decisions (D1-01): the robots verdict is the ONE shared
# rules_scrape.robots_verdict, called with this tool's own token (_check);
# this module defines no verdict of its own.


def tiles_for(bboxes: Iterable[Sequence[float]], step: float = TILE_DEG) -> list[tuple]:
    return zoning_import.tiles_for(bboxes, step)


def _layer_check(meta: Mapping[str, Any], name: str, fields: Sequence[str], where: str) -> int:
    """Layer drift is a source failure (FW-12). Returns the page size."""
    if meta.get("name") != name or meta.get("type") != "Feature Layer":
        raise SourceError(f"{where}: layer name or type changed")
    if meta.get("geometryType") != "esriGeometryPolygon":
        raise SourceError(f"{where}: not a polygon layer")
    names = {f.get("name") for f in meta.get("fields") or [] if isinstance(f, dict)}
    missing = [f for f in fields if f not in names]
    if missing:
        raise SourceError(f"{where}: {len(missing)} expected field(s) missing")
    mrc = meta.get("maxRecordCount")
    return max(1, min(OID_CHUNK, int(mrc))) if isinstance(mrc, int) and mrc > 0 else OID_CHUNK


def _layer_ids(f: HazardsFetcher, url: str, bboxes: Sequence[Sequence[float]]) -> list[int]:
    oids: set[int] = set()
    for env in tiles_for(bboxes):
        body = f.get_json(f"{url}/query", {
            "where": "1=1", "geometry": ",".join(f"{v:.6f}" for v in env),
            "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "returnIdsOnly": "true", "f": "json"})
        if body.get("exceededTransferLimit"):
            raise SourceError(f"{urlparse(url).hostname}: object id list truncated")
        ids = body.get("objectIds")
        if ids is None:
            ids = []
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise SourceError(f"{urlparse(url).hostname}: object id answer malformed")
        oids.update(ids)
    return sorted(oids)


def _layer_features(f: HazardsFetcher, url: str, ids: Sequence[int], fields: Sequence[str],
                    page: int) -> list[Feature]:
    """Features by objectIds in pages; every page checked (FW-12). GeoJSON
    output, so the server converts rings (FW-13); WGS84 requested."""
    host = urlparse(url).hostname
    out: list[Feature] = []
    for i in range(0, len(ids), page):
        chunk = list(ids[i:i + page])
        body = f.get_json(f"{url}/query", {
            "objectIds": ",".join(str(o) for o in chunk), "outFields": ",".join(fields) or "",
            "returnGeometry": "true", "outSR": 4326, "geometryPrecision": GEOMETRY_PRECISION,
            "f": "geojson"})
        props = body.get("properties") if isinstance(body.get("properties"), dict) else {}
        if body.get("exceededTransferLimit") or props.get("exceededTransferLimit"):
            raise SourceError(f"{host}: a feature page came back truncated")
        got = body.get("features")
        if not isinstance(got, list) or len(got) != len(chunk):
            raise SourceError(f"{host}: a feature page came back short")
        for ft in got:
            if not isinstance(ft, dict) or not isinstance(ft.get("geometry"), dict):
                raise SourceError(f"{host}: a feature without geometry")
            attrs = ft.get("properties") or {}
            if any(k not in attrs for k in fields):
                raise SourceError(f"{host}: a feature lacks an expected field")
            fid = ft.get("id")
            if fid is None:
                raise SourceError(f"{host}: a feature without an id")
            out.append(Feature(str(fid), {k: attrs.get(k) for k in fields},
                               json.dumps(ft["geometry"], sort_keys=True), 4326))
    if len(out) != len(ids):
        raise SourceError(f"{host}: {len(out)} features for {len(ids)} ids")
    return out


def _as_of_from_ms(ms: Any) -> str | None:
    if isinstance(ms, (int, float)) and ms > 0:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date().isoformat()
    return None


def fetch_rest_nfhl(f: HazardsFetcher, bboxes: Sequence[Sequence[float]]) -> SourceData:
    root = f.get_json(NFHL_SERVICE, {"f": "json"})
    layers = {l.get("name"): l.get("id") for l in root.get("layers") or [] if isinstance(l, dict)}
    if layers.get(NFHL_LAYER_NAME) != NFHL_LAYER:
        raise SourceError("hazards.fema.gov: flood hazard layer moved or renamed")
    url = f"{NFHL_SERVICE}/{NFHL_LAYER}"
    meta = f.get_json(url, {"f": "json"})
    page = _layer_check(meta, NFHL_LAYER_NAME, NFHL_FIELDS, "hazards.fema.gov")
    feats = _layer_features(f, url, _layer_ids(f, url, bboxes), NFHL_FIELDS, page)
    data = SourceData("nfhl", sort_features(feats), origin="NFHL REST layer 28")
    versions = sorted({_poly_params("nfhl", ft)[5] for ft in feats} - {None})
    data.as_of = (", ".join(versions) if versions else "") or _as_of_from_ms(
        (meta.get("editingInfo") or {}).get("lastEditDate")) or ""
    lomr_id = layers.get(LOMR_LAYER_NAME)
    if isinstance(lomr_id, int):
        lurl = f"{NFHL_SERVICE}/{lomr_id}"
        lmeta = f.get_json(lurl, {"f": "json"})
        lpage = _layer_check(lmeta, LOMR_LAYER_NAME, (), "hazards.fema.gov LOMR")
        data.lomr = sort_features(_layer_features(f, lurl, _layer_ids(f, lurl, bboxes), (),
                                                  lpage))
    return data


def fetch_rest_nwi(f: HazardsFetcher, bboxes: Sequence[Sequence[float]]) -> SourceData:
    url = f"{NWI_SERVICE}/{NWI_LAYER}"
    meta = f.get_json(url, {"f": "json"})
    page = _layer_check(meta, NWI_LAYER_NAME, NWI_FIELDS, "fwspublicservices.wim.usgs.gov")
    feats = _layer_features(f, url, _layer_ids(f, url, bboxes), NWI_FIELDS, page)
    data = SourceData("nwi", sort_features(feats), origin="NWI REST layer 0")
    data.as_of = _as_of_from_ms((meta.get("editingInfo") or {}).get("lastEditDate")) or ""
    return data


# ---------------------------------------------------------------------------
# Local files: a pure-stdlib .shp/.dbf/.prj reader (SR-10)
# ---------------------------------------------------------------------------
_POLYGON_TYPES = (5, 15, 25)


def ring_signed_area(ring: Sequence[Sequence[float]]) -> float:
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _in_ring(x: float, y: float, ring: Sequence[Sequence[float]]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-300) + x1:
            inside = not inside
    return inside


def rings_to_geojson(rings: Sequence[Sequence[Sequence[float]]]) -> str | None:
    """Shapefile/Esri rings -> GeoJSON MultiPolygon: clockwise rings are
    outer rings, counter-clockwise rings holes, each hole assigned to the
    smallest outer ring containing it (FW-13). SiteFit's fallback: no
    clockwise ring at all -> every ring is an outer ring."""
    closed = []
    for r in rings:
        pts = [(float(p[0]), float(p[1])) for p in r]
        if len(pts) < 3:
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        if len(pts) >= 4:
            closed.append(pts)
    outers = [r for r in closed if ring_signed_area(r) < 0]
    holes = [r for r in closed if ring_signed_area(r) >= 0]
    if not outers:
        outers, holes = holes, []
    polys = [[o] for o in outers]
    for h in holes:
        hosts = [i for i, o in enumerate(outers) if _in_ring(h[0][0], h[0][1], o)]
        if hosts:
            best = min(hosts, key=lambda i: abs(ring_signed_area(outers[i])))
            polys[best].append(h)
    if not polys:
        return None
    return json.dumps({"type": "MultiPolygon",
                       "coordinates": [[[list(p) for p in r] for r in poly] for poly in polys]})


@dataclass
class DbfField:
    name: str
    ftype: str
    length: int
    decimals: int


class Dbf:
    """dBASE III reader: header, field descriptors, random access by record."""

    def __init__(self, path: Path, encoding: str = "latin-1") -> None:
        self.path, self.encoding = path, encoding
        self.fh = path.open("rb")
        head = self.fh.read(32)
        if len(head) < 32:
            raise SourceError(f"{path.name}: not a dBASE file")
        yy, mm, dd = head[1], head[2], head[3]
        self.date = f"{1900 + yy:04d}-{mm:02d}-{dd:02d}" if 1 <= mm <= 12 and 1 <= dd <= 31 else ""
        self.count, self.header_len, self.record_len = struct.unpack("<IHH", head[4:12])
        self.fields: list[DbfField] = []
        while True:
            d = self.fh.read(32)
            if not d or d[0] == 0x0D:
                break
            if len(d) < 32:
                raise SourceError(f"{path.name}: truncated field descriptors")
            name = d[:11].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
            self.fields.append(DbfField(name, chr(d[11]), d[16], d[17]))
        if sum(f.length for f in self.fields) + 1 != self.record_len:
            raise SourceError(f"{path.name}: record length does not match its fields")

    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    def resolve(self, wanted: Sequence[str]) -> dict[str, str]:
        """Wanted field -> its dBASE name. dBASE names hold 10 characters,
        so the NWI download stores WETLAND_TYPE as WETLAND_TY."""
        names = {n.upper(): n for n in self.names()}
        out = {}
        for w in wanted:
            hit = names.get(w.upper()) or names.get(w.upper()[:10])
            if hit is not None:
                out[w] = hit
        return out

    def record(self, i: int, wanted: Sequence[str]) -> dict[str, Any] | None:
        """The wanted fields of record ``i``, or None when it is flagged
        deleted (first byte '*')."""
        if not 0 <= i < self.count:
            raise SourceError(f"{self.path.name}: record {i} out of range")
        self.fh.seek(self.header_len + i * self.record_len)
        raw = self.fh.read(self.record_len)
        if len(raw) != self.record_len:
            raise SourceError(f"{self.path.name}: truncated record")
        if raw[:1] == b"*":
            return None      # dBASE deleted-record flag (review finding 12)
        out: dict[str, Any] = {}
        pos = 1
        for fd in self.fields:
            chunk = raw[pos:pos + fd.length]
            pos += fd.length
            if fd.name not in wanted:
                continue
            text = chunk.decode(self.encoding, "replace").strip()
            if fd.ftype in "NF":
                try:
                    out[fd.name] = float(text) if text else None
                except ValueError:
                    out[fd.name] = None
            else:
                out[fd.name] = text
        return out

    def close(self) -> None:
        self.fh.close()


def shp_header(path: Path) -> tuple[int, tuple[float, float, float, float]]:
    with path.open("rb") as fh:
        head = fh.read(100)
    if len(head) < 100 or struct.unpack(">i", head[:4])[0] != 9994:
        raise SourceError(f"{path.name}: not a shapefile")
    shape_type = struct.unpack("<i", head[32:36])[0]
    bbox = struct.unpack("<4d", head[36:68])
    return shape_type, bbox


def iter_shp(path: Path, keep: Callable[[tuple[float, float, float, float]], bool]
             ) -> Iterator[tuple[int, list[list[tuple[float, float]]]]]:
    """(record index, rings) of every polygon record whose bbox ``keep``
    accepts. Other records are skipped by their header, unread."""
    shape_type, _ = shp_header(path)
    if shape_type not in _POLYGON_TYPES:
        raise SourceError(f"{path.name}: not a polygon shapefile")
    with path.open("rb") as fh:
        fh.seek(100)
        idx = 0
        while True:
            rh = fh.read(8)
            if not rh:
                return
            if len(rh) < 8:
                raise SourceError(f"{path.name}: truncated record header")
            _num, words = struct.unpack(">ii", rh)
            n = words * 2
            content = fh.read(n)
            if len(content) != n:
                raise SourceError(f"{path.name}: truncated record")
            st = struct.unpack("<i", content[:4])[0]
            if st == 0:
                idx += 1
                continue
            if st not in _POLYGON_TYPES:
                raise SourceError(f"{path.name}: mixed shape types")
            box = struct.unpack("<4d", content[4:36])
            if keep(box):
                nparts, npts = struct.unpack("<ii", content[36:44])
                parts = list(struct.unpack(f"<{nparts}i", content[44:44 + 4 * nparts]))
                off = 44 + 4 * nparts
                xy = struct.unpack(f"<{2 * npts}d", content[off:off + 16 * npts])
                pts = list(zip(xy[0::2], xy[1::2]))
                bounds = parts + [npts]
                yield idx, [pts[bounds[k]:bounds[k + 1]] for k in range(nparts)]
            idx += 1


def _prj_number(text: str, name: str) -> float | None:
    m = re.search(r'PARAMETER\["' + name + r'",(-?[0-9.]+)\]', text, re.I)
    return float(m.group(1)) if m else None


def srid_from_prj(text: str) -> int:
    """The SRID of a .prj (ESRI WKT). Only the systems these downloads use;
    anything else -- Web Mercator included -- is refused (FW-11)."""
    t = re.sub(r"\s+", "", text or "")
    u = t.upper()
    if not u:
        raise SourceError("missing .prj")
    if "MERCATOR" in u:
        raise SourceError("Web Mercator .prj refused")
    nad83 = "NORTH_AMERICAN_1983" in u or "NAD_1983" in u or "NAD83" in u
    if u.startswith("GEOGCS"):
        if nad83 and "HARN" not in u and "CSRS" not in u and "2011" not in u:
            return 4269
        if "WGS_1984" in u or "WGS84" in u:
            return 4326
    if u.startswith("PROJCS") and "ALBERS" in u and nad83 and "2011" not in u:
        want = {"CENTRAL_MERIDIAN": -96.0, "STANDARD_PARALLEL_1": 29.5,
                "STANDARD_PARALLEL_2": 45.5, "LATITUDE_OF_ORIGIN": 23.0,
                "FALSE_EASTING": 0.0, "FALSE_NORTHING": 0.0}
        if all(_prj_number(t, k) == v for k, v in want.items()) and "METER" in u:
            return 5070
    raise SourceError("unsupported coordinate system in .prj")


def _sidecar(shp: Path, ext: str) -> Path:
    for cand in (shp.with_suffix(ext), shp.with_suffix(ext.upper())):
        if cand.exists():
            return cand
    raise SourceError(f"{shp.name}: {ext} file missing")


def _encoding(shp: Path) -> str:
    for cand in (shp.with_suffix(".cpg"), shp.with_suffix(".CPG")):
        if cand.exists():
            name = cand.read_text(encoding="ascii", errors="replace").strip().lower()
            return {"utf-8": "utf-8", "utf8": "utf-8", "65001": "utf-8"}.get(name, "latin-1")
    return "latin-1"


class _Grid:
    """Bbox intersection test against many small boxes (parcel envelopes)."""

    def __init__(self, boxes: Sequence[Sequence[float]]) -> None:
        self.boxes = [tuple(b) for b in boxes]
        if not self.boxes:
            self.cell, self.cells = 1.0, {}
            return
        span = max(max(b[2] - b[0], b[3] - b[1]) for b in self.boxes)
        self.cell = max(span, 1e-9) * 4
        self.cells: dict[tuple[int, int], list[int]] = {}
        for i, b in enumerate(self.boxes):
            for k in self._keys(b):
                self.cells.setdefault(k, []).append(i)

    def _keys(self, b: Sequence[float]) -> Iterator[tuple[int, int]]:
        c = self.cell
        for ix in range(int(b[0] // c), int(b[2] // c) + 1):
            for iy in range(int(b[1] // c), int(b[3] // c) + 1):
                yield ix, iy

    def hits(self, b: Sequence[float]) -> bool:
        c = self.cell
        if (b[2] - b[0]) / c > 2000 or (b[3] - b[1]) / c > 2000:   # a huge polygon
            return any(not (o[2] < b[0] or o[0] > b[2] or o[3] < b[1] or o[1] > b[3])
                       for o in self.boxes)
        for k in self._keys(b):
            for i in self.cells.get(k, ()):
                o = self.boxes[i]
                if not (o[2] < b[0] or o[0] > b[2] or o[3] < b[1] or o[1] > b[3]):
                    return True
        return False


def read_shapefile(shp: Path, fields: Sequence[str],
                   boxes_for: Callable[[int], Sequence[Sequence[float]]]) -> tuple[
                       list[Feature], int, str, tuple[float, ...]]:
    """(features, srid, dbf date, file bbox) of every polygon whose record
    bbox meets a parcel envelope (given in the file's own SRID)."""
    srid = srid_from_prj(_sidecar(shp, ".prj").read_text(encoding="latin-1", errors="replace"))
    _, fbox = shp_header(shp)
    dbf = Dbf(_sidecar(shp, ".dbf"), _encoding(shp))
    try:
        names = dbf.resolve(fields)
        missing = [f for f in fields if f not in names]
        if missing:
            raise SourceError(f"{shp.name}: {len(missing)} expected field(s) missing")
        grid = _Grid(boxes_for(srid))
        feats = []
        for idx, rings in iter_shp(shp, grid.hits):
            gj = rings_to_geojson(rings)
            if gj is None:
                continue
            rec = dbf.record(idx, list(names.values()))
            if rec is None:
                continue
            feats.append(Feature(str(idx), {f: rec.get(n) for f, n in names.items()}, gj, srid))
        return feats, srid, dbf.date, fbox
    finally:
        dbf.close()


def _single_shp(d: Path) -> Path:
    """The one .shp in ``d``; when there are several (the USFWS state
    package ships project-metadata and historic-map shapefiles next to the
    wetlands), the single one whose name ends in ``_Wetlands`` (review
    finding 10). Anything else is ambiguous and fails the source."""
    shps = sorted(p for p in d.glob("*") if p.suffix.lower() == ".shp")
    if len(shps) == 1:
        return shps[0]
    wet = [p for p in shps if p.stem.lower().endswith("_wetlands")]
    if len(wet) == 1:
        return wet[0]
    raise SourceError(f"{d.name}/: expected exactly one .shp (or one *_Wetlands.shp), "
                      f"found {len(shps)}")


# ---------------------------------------------------------------------------
# SQL (SR-2: module-level constants, %s placeholders only)
# ---------------------------------------------------------------------------
_SQL_TABLE_EXISTS = "SELECT to_regclass('parcel_hazards') IS NOT NULL"
# Fulton parcels with geometry; centroid for EPSG:2240's area of use (FW-11)
# and envelope, both of the SAME made-valid polygonal geometry every
# computation uses (review finding 5: a self-intersecting ring's raw
# centroid can fall far outside the parcel), plus whether that repaired
# geometry is empty (counted apart, never called "outside the area of use").
_SQL_LIST_PARCELS = (
    "SELECT parcel_id, ST_X(ST_Centroid(v)), ST_Y(ST_Centroid(v)), "
    "ST_XMin(v), ST_YMin(v), ST_XMax(v), ST_YMax(v), ST_IsEmpty(v) FROM ("
    "SELECT parcel_id, ST_CollectionExtract(ST_MakeValid(geometry), 3) AS v "
    "FROM parcels WHERE county = %s AND geometry IS NOT NULL) s ORDER BY parcel_id"
)
# Parcel envelopes (padded by 0.0005 deg) in a file's own SRID, of the
# made-valid polygonal geometry.
_SQL_BOXES_IN_SRID = (
    "SELECT parcel_id, ST_XMin(b), ST_YMin(b), ST_XMax(b), ST_YMax(b) FROM ("
    "SELECT parcel_id, ST_Transform(ST_Expand(ST_Envelope("
    "ST_CollectionExtract(ST_MakeValid(geometry), 3)), 0.0005), %s) AS b "
    "FROM parcels WHERE parcel_id = ANY(%s)) e WHERE NOT ST_IsEmpty(b)"
)
_SQL_CREATE_TEMP = (
    "CREATE TEMP TABLE IF NOT EXISTS hazards_import_polys ("
    "fid TEXT NOT NULL, src TEXT NOT NULL, cat TEXT, zkey TEXT, bfe NUMERIC, asof TEXT, "
    "wtype TEXT, deep BOOLEAN, geom geometry) ON COMMIT DROP"
)
# Made valid, polygons only, to SRID 2240, subdivided for the clips (FW-19).
_SQL_INSERT_POLY = (
    "INSERT INTO hazards_import_polys (fid, src, cat, zkey, bfe, asof, wtype, deep, geom) "
    "SELECT %s, %s, %s, %s, %s, %s, %s, %s, ST_Subdivide(g, 256) FROM ("
    "SELECT ST_CollectionExtract(ST_MakeValid(ST_Transform(ST_CollectionExtract("
    "ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), %s)), 3), 2240)), 3) AS g) s "
    "WHERE NOT ST_IsEmpty(g)"
)
_SQL_INDEX_TEMP = "CREATE INDEX ON hazards_import_polys USING GIST (geom)"
_SQL_ANALYZE_TEMP = "ANALYZE hazards_import_polys"

# The parcel geometry every computation uses: S2's / site_fit's made-valid
# polygonal part, in SRID 2240, plus the md5 of the stored geometry.
_SQL_PG_CTE = (
    "pg AS (SELECT parcel_id, md5(ST_AsBinary(geometry)) AS gmd5, "
    "ST_Transform(ST_CollectionExtract(ST_MakeValid(geometry), 3), 2240) AS g "
    "FROM parcels WHERE parcel_id = ANY(%s) AND geometry IS NOT NULL)"
)
# Parameters: (ids, noise floor [LOMR hit], noise floor [zones], LOMR status
# when no LOMR hit, source as-of, noise floor [stored geometry], source label).
_SQL_UPSERT_NFHL = (
    "WITH " + _SQL_PG_CTE + ", "
    "pc AS (SELECT pg.parcel_id, z.cat, z.zkey, z.bfe, z.asof, "
    "ST_CollectionExtract(ST_Intersection(pg.g, z.geom), 3) AS ig "
    "FROM pg JOIN hazards_import_polys z ON z.src = 'nfhl' AND z.geom && pg.g "
    "AND ST_Intersects(z.geom, pg.g)), "
    "pk AS (SELECT parcel_id, zkey, ST_Area(ST_Union(ig)) AS sf FROM pc GROUP BY parcel_id, zkey), "
    "agg AS (SELECT parcel_id, "
    "ST_Union(ig) FILTER (WHERE cat = 'floodway') AS fw, "
    "ST_Union(ig) FILTER (WHERE cat IN ('floodway', 'sfha')) AS sh, "
    "ST_Union(ig) FILTER (WHERE cat = 'x500') AS x5, "
    "ST_Union(ig) FILTER (WHERE cat <> 'undetermined') AS mp, "
    "ST_Union(ig) FILTER (WHERE cat = 'undetermined') AS ud, "
    "MAX(bfe) FILTER (WHERE bfe > -9000 AND cat IN ('floodway', 'sfha')) AS bfe, "
    "array_agg(DISTINCT asof ORDER BY asof) FILTER (WHERE asof IS NOT NULL AND asof <> '') "
    "AS asof FROM pc GROUP BY parcel_id), "
    "lu AS (SELECT pg.parcel_id, ST_Union(ST_CollectionExtract("
    "ST_Intersection(pg.g, z.geom), 3)) AS lg "
    "FROM pg JOIN hazards_import_polys z ON z.src = 'lomr' AND z.geom && pg.g "
    "AND ST_Intersects(z.geom, pg.g) GROUP BY pg.parcel_id), "
    "lm AS (SELECT parcel_id, ST_Area(lg) >= %s AS hit FROM lu), "
    "pz AS (SELECT parcel_id, zkey, ST_Union(ig) AS u FROM pc GROUP BY parcel_id, zkey), "
    "pl AS (SELECT pz.parcel_id, pz.zkey, "
    "ST_Area(ST_CollectionExtract(ST_Intersection(pz.u, lu.lg), 3)) AS sf "
    "FROM pz JOIN lu ON lu.parcel_id = pz.parcel_id), "
    "r AS (SELECT pg.parcel_id, pg.gmd5, ST_Area(pg.g) AS psf, "
    "COALESCE(ST_Area(agg.mp), 0) AS msf, COALESCE(ST_Area(agg.ud), 0) AS usf, "
    "COALESCE(ST_Area(agg.sh), 0) AS ssf, COALESCE(ST_Area(agg.fw), 0) AS fsf, "
    "CASE WHEN agg.x5 IS NULL THEN 0 WHEN agg.sh IS NULL THEN ST_Area(agg.x5) "
    "ELSE ST_Area(ST_Difference(agg.x5, agg.sh)) END AS xsf, "
    "(SELECT jsonb_object_agg(pk.zkey, round(pk.sf::numeric, 2)) FROM pk "
    "WHERE pk.parcel_id = pg.parcel_id) AS zmap, "
    "(SELECT jsonb_object_agg(pl.zkey, round(pl.sf::numeric, 2)) FROM pl "
    "WHERE pl.parcel_id = pg.parcel_id AND pl.sf > 0) AS lmap, "
    "COALESCE((SELECT array_agg(DISTINCT split_part(pk.zkey, '|', 1) ORDER BY "
    "split_part(pk.zkey, '|', 1)) FROM pk WHERE pk.parcel_id = pg.parcel_id "
    "AND pk.sf >= %s), ARRAY[]::text[]) AS zones, "
    "agg.bfe, CASE WHEN lm.hit THEN 'intersects' ELSE %s END AS lomr, "
    "COALESCE(array_to_string(agg.asof, ', '), %s) AS asof, "
    "CASE WHEN agg.sh IS NULL OR ST_Area(agg.sh) < %s THEN NULL "
    "ELSE ST_Multi(ST_CollectionExtract(ST_MakeValid(agg.sh), 3)) END AS sgeom "
    "FROM pg LEFT JOIN agg ON agg.parcel_id = pg.parcel_id "
    "LEFT JOIN lm ON lm.parcel_id = pg.parcel_id) "
    "INSERT INTO parcel_hazards (parcel_id, nfhl_parcel_geom_md5, nfhl_parcel_sf, "
    "nfhl_mapped_sf, nfhl_undetermined_sf, sfha_sf, floodway_sf, x500_sf, sfha_pct, "
    "floodway_pct, x500_pct, nfhl_zone_sf, nfhl_zone_lomr_sf, zones, bfe_ft, lomr_status, "
    "nfhl_as_of, nfhl_source, nfhl_fetched_at, sfha_geom) "
    "SELECT parcel_id, gmd5, round(psf::numeric, 2), round(msf::numeric, 2), "
    "round(usf::numeric, 2), round(ssf::numeric, 2), round(fsf::numeric, 2), "
    "round(xsf::numeric, 2), "
    "CASE WHEN psf > 0 THEN round((100 * ssf / psf)::numeric, 4) END, "
    "CASE WHEN psf > 0 THEN round((100 * fsf / psf)::numeric, 4) END, "
    "CASE WHEN psf > 0 THEN round((100 * xsf / psf)::numeric, 4) END, "
    "COALESCE(zmap, '{}'::jsonb), COALESCE(lmap, '{}'::jsonb), zones, bfe, lomr, asof, %s, "
    "now(), sgeom FROM r "
    "ON CONFLICT (parcel_id) DO UPDATE SET "
    "nfhl_parcel_geom_md5 = EXCLUDED.nfhl_parcel_geom_md5, "
    "nfhl_parcel_sf = EXCLUDED.nfhl_parcel_sf, nfhl_mapped_sf = EXCLUDED.nfhl_mapped_sf, "
    "nfhl_undetermined_sf = EXCLUDED.nfhl_undetermined_sf, sfha_sf = EXCLUDED.sfha_sf, "
    "floodway_sf = EXCLUDED.floodway_sf, x500_sf = EXCLUDED.x500_sf, "
    "sfha_pct = EXCLUDED.sfha_pct, floodway_pct = EXCLUDED.floodway_pct, "
    "x500_pct = EXCLUDED.x500_pct, nfhl_zone_sf = EXCLUDED.nfhl_zone_sf, "
    "nfhl_zone_lomr_sf = EXCLUDED.nfhl_zone_lomr_sf, "
    "zones = EXCLUDED.zones, bfe_ft = EXCLUDED.bfe_ft, lomr_status = EXCLUDED.lomr_status, "
    "nfhl_as_of = EXCLUDED.nfhl_as_of, nfhl_source = EXCLUDED.nfhl_source, "
    "nfhl_fetched_at = EXCLUDED.nfhl_fetched_at, sfha_geom = EXCLUDED.sfha_geom "
    "WHERE (parcel_hazards.nfhl_parcel_geom_md5, parcel_hazards.nfhl_parcel_sf, "
    "parcel_hazards.nfhl_mapped_sf, parcel_hazards.nfhl_undetermined_sf, "
    "parcel_hazards.sfha_sf, parcel_hazards.floodway_sf, parcel_hazards.x500_sf, "
    "parcel_hazards.nfhl_zone_sf, parcel_hazards.nfhl_zone_lomr_sf, parcel_hazards.zones, "
    "parcel_hazards.bfe_ft, "
    "parcel_hazards.lomr_status, parcel_hazards.nfhl_as_of, parcel_hazards.nfhl_source, "
    "ST_AsEWKB(parcel_hazards.sfha_geom)) IS DISTINCT FROM "
    "(EXCLUDED.nfhl_parcel_geom_md5, EXCLUDED.nfhl_parcel_sf, EXCLUDED.nfhl_mapped_sf, "
    "EXCLUDED.nfhl_undetermined_sf, EXCLUDED.sfha_sf, EXCLUDED.floodway_sf, "
    "EXCLUDED.x500_sf, EXCLUDED.nfhl_zone_sf, EXCLUDED.nfhl_zone_lomr_sf, EXCLUDED.zones, "
    "EXCLUDED.bfe_ft, "
    "EXCLUDED.lomr_status, EXCLUDED.nfhl_as_of, EXCLUDED.nfhl_source, "
    "ST_AsEWKB(EXCLUDED.sfha_geom)) "
    "RETURNING parcel_id, nfhl_as_of"
)
# Parameters: (ids, noise floor, source as-of, source label).
_SQL_UPSERT_NWI = (
    "WITH " + _SQL_PG_CTE + ", "
    "pc AS (SELECT pg.parcel_id, z.wtype, z.deep, "
    "ST_CollectionExtract(ST_Intersection(pg.g, z.geom), 3) AS ig "
    "FROM pg JOIN hazards_import_polys z ON z.src = 'nwi' AND z.geom && pg.g "
    "AND ST_Intersects(z.geom, pg.g)), "
    "pt AS (SELECT parcel_id, wtype, ST_Area(ST_Union(ig)) AS sf FROM pc "
    "GROUP BY parcel_id, wtype), "
    "agg AS (SELECT parcel_id, ST_Union(ig) AS wu, ST_Union(ig) FILTER (WHERE deep) AS du "
    "FROM pc GROUP BY parcel_id), "
    "r AS (SELECT pg.parcel_id, pg.gmd5, ST_Area(pg.g) AS psf, "
    "COALESCE(ST_Area(agg.wu), 0) AS wsf, COALESCE(ST_Area(agg.du), 0) AS dsf, "
    "(SELECT jsonb_object_agg(pt.wtype, round(pt.sf::numeric, 2)) FROM pt "
    "WHERE pt.parcel_id = pg.parcel_id) AS tmap, "
    "CASE WHEN agg.wu IS NULL OR ST_Area(agg.wu) < %s THEN NULL "
    "ELSE ST_Multi(ST_CollectionExtract(ST_MakeValid(agg.wu), 3)) END AS wgeom "
    "FROM pg LEFT JOIN agg ON agg.parcel_id = pg.parcel_id) "
    "INSERT INTO parcel_hazards (parcel_id, nwi_parcel_geom_md5, nwi_parcel_sf, wetland_sf, "
    "wetland_pct, wetland_deep_excavated_sf, wetland_type_sf, nwi_as_of, nwi_source, "
    "nwi_fetched_at, wetland_geom) "
    "SELECT parcel_id, gmd5, round(psf::numeric, 2), round(wsf::numeric, 2), "
    "CASE WHEN psf > 0 THEN round((100 * wsf / psf)::numeric, 4) END, "
    "round(dsf::numeric, 2), COALESCE(tmap, '{}'::jsonb), %s, %s, now(), wgeom FROM r "
    "ON CONFLICT (parcel_id) DO UPDATE SET "
    "nwi_parcel_geom_md5 = EXCLUDED.nwi_parcel_geom_md5, "
    "nwi_parcel_sf = EXCLUDED.nwi_parcel_sf, wetland_sf = EXCLUDED.wetland_sf, "
    "wetland_pct = EXCLUDED.wetland_pct, "
    "wetland_deep_excavated_sf = EXCLUDED.wetland_deep_excavated_sf, "
    "wetland_type_sf = EXCLUDED.wetland_type_sf, nwi_as_of = EXCLUDED.nwi_as_of, "
    "nwi_source = EXCLUDED.nwi_source, nwi_fetched_at = EXCLUDED.nwi_fetched_at, "
    "wetland_geom = EXCLUDED.wetland_geom "
    "WHERE (parcel_hazards.nwi_parcel_geom_md5, parcel_hazards.nwi_parcel_sf, "
    "parcel_hazards.wetland_sf, parcel_hazards.wetland_deep_excavated_sf, "
    "parcel_hazards.wetland_type_sf, parcel_hazards.nwi_as_of, parcel_hazards.nwi_source, "
    "ST_AsEWKB(parcel_hazards.wetland_geom)) IS DISTINCT FROM "
    "(EXCLUDED.nwi_parcel_geom_md5, EXCLUDED.nwi_parcel_sf, EXCLUDED.wetland_sf, "
    "EXCLUDED.wetland_deep_excavated_sf, EXCLUDED.wetland_type_sf, EXCLUDED.nwi_as_of, "
    "EXCLUDED.nwi_source, ST_AsEWKB(EXCLUDED.wetland_geom)) "
    "RETURNING parcel_id, nwi_as_of"
)
_SQL_COUNT_PARCELS = "SELECT COUNT(*) FROM parcels"
# Review finding 12: parcels this source imported before that the current
# input does not cover (their previous values are kept, and still read as
# fresh while the geometry is unchanged). Parameters: (covered ids,).
_SQL_PREVIOUS_NOT_COVERED_NFHL = (
    "SELECT COUNT(*) FROM parcel_hazards WHERE nfhl_fetched_at IS NOT NULL "
    "AND NOT (parcel_id = ANY(%s))"
)
_SQL_PREVIOUS_NOT_COVERED_NWI = (
    "SELECT COUNT(*) FROM parcel_hazards WHERE nwi_fetched_at IS NOT NULL "
    "AND NOT (parcel_id = ANY(%s))"
)


# ---------------------------------------------------------------------------
# Writing one source (FW-16: one transaction, all or nothing)
# ---------------------------------------------------------------------------
def _poly_params(src: str, f: Feature) -> tuple:
    a = f.attrs
    if src == "nfhl":
        cat = hazards.classify_nfhl(a.get("FLD_ZONE"), a.get("ZONE_SUBTY"), a.get("SFHA_TF"))
        zkey = hazards.zone_key(a.get("FLD_ZONE"), a.get("ZONE_SUBTY"), a.get("SFHA_TF"))
        bfe = a.get("STATIC_BFE")
        bfe = float(bfe) if isinstance(bfe, (int, float)) else None
        dfirm = " ".join(str(a.get("DFIRM_ID") or "").split())
        ver = " ".join(str(a.get("VERSION_ID") or "").split())
        asof = f"{dfirm} {ver}".strip() or None
        return (f.fid, src, cat, zkey, bfe, asof, None, None, f.geojson, f.srid)
    if src == "nwi":
        wtype = " ".join(str(a.get("WETLAND_TYPE") or "").split()) or "Wetland"
        deep = hazards.is_deepwater(wtype) or hazards.is_excavated(a.get("ATTRIBUTE"))
        return (f.fid, src, None, None, None, None, wtype, deep, f.geojson, f.srid)
    return (f.fid, src, None, None, None, None, None, None, f.geojson, f.srid)


def write_source(conn: Any, data: SourceData) -> dict[str, int]:
    """Load the features, compute and UPSERT this source's column group for
    the covered parcels, commit. Raises on any failure (caller rolls back)."""
    if not data.as_of.strip():
        raise SourceError(f"{data.key}: no as-of date or version; nothing written (FW-20)")
    ids = sorted(data.covered)
    with conn.cursor() as cur:
        cur.execute(_SQL_CREATE_TEMP)
        for f in data.features:
            cur.execute(_SQL_INSERT_POLY, _poly_params(data.key, f))
        for f in data.lomr or ():
            cur.execute(_SQL_INSERT_POLY, _poly_params("lomr", f))
        cur.execute(_SQL_INDEX_TEMP)
        cur.execute(_SQL_ANALYZE_TEMP)
        fl = hazards.NOISE_FLOOR_SF
        if data.key == "nfhl":
            lomr_default = "none" if data.lomr is not None else "not_read"
            cur.execute(_SQL_UPSERT_NFHL, (ids, fl, fl, lomr_default, data.as_of, fl,
                                           data.origin))
        else:
            cur.execute(_SQL_UPSERT_NWI, (ids, fl, data.as_of, data.origin))
        changed = cur.fetchall()
        if data.key == "nfhl":
            cur.execute(_SQL_PREVIOUS_NOT_COVERED_NFHL, (ids,))
        else:
            cur.execute(_SQL_PREVIOUS_NOT_COVERED_NWI, (ids,))
        prev = cur.fetchone()
    if any(not (r[1] or "").strip() for r in changed):
        raise SourceError(f"{data.key}: a row without as-of; nothing written (FW-20)")
    return {"covered": len(ids), "changed": len(changed),
            "unchanged": len(ids) - len(changed), "features": len(data.features),
            "not_covered_kept": int(prev[0]) if prev and prev[0] is not None else 0}


def _connection_unusable(conn: Any) -> bool:
    return zoning_import._connection_unusable(conn)


# ---------------------------------------------------------------------------
# Counts (FW-10, FW-21: counts only)
# ---------------------------------------------------------------------------
def band_of(pct: float | None) -> str | None:
    if pct is None or pct <= 0:
        return None
    for label, lo, hi in SHARE_BANDS:
        if pct > lo and (hi is None or pct <= hi):
            return label
    return None


def summarise(assess: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Counts-only summary lines from the post-import assessments."""
    lines = []
    sfha_b: dict[str, int] = {}
    wet_b: dict[str, int] = {}
    any_sfha = any_fw = any_x5 = any_wet = 0
    for pid, r in rows.items():
        a = assess.get(pid)
        if a is None:
            continue
        if a.flood_state == hazards.FRESH:
            ps = r.get("nfhl_parcel_sf")
            if hazards.at_least(r.get("sfha_sf")):
                any_sfha += 1
            if hazards.at_least(r.get("floodway_sf")):
                any_fw += 1
            if hazards.at_least(r.get("x500_sf")):
                any_x5 += 1
            if hazards.at_least(r.get("sfha_sf")) and ps:
                b = band_of(100.0 * float(r["sfha_sf"]) / float(ps))
                if b:
                    sfha_b[b] = sfha_b.get(b, 0) + 1
        if a.wetland_state == hazards.FRESH:
            ps = r.get("nwi_parcel_sf")
            if hazards.at_least(r.get("wetland_sf")):
                any_wet += 1
                if ps:
                    b = band_of(100.0 * float(r["wetland_sf"]) / float(ps))
                    if b:
                        wet_b[b] = wet_b.get(b, 0) + 1
    order = [b[0] for b in SHARE_BANDS]
    lines.append(f"parcels with any SFHA {any_sfha}, floodway {any_fw}, 0.2% zone {any_x5}, "
                 f"NWI wetlands {any_wet}")
    lines.append("SFHA share bands: " + ", ".join(f"{b} {sfha_b.get(b, 0)}" for b in order))
    lines.append("wetland share bands: " + ", ".join(f"{b} {wet_b.get(b, 0)}" for b in order))
    h4: dict[str, int] = {}
    h6: dict[str, int] = {}
    for a in assess.values():
        h4[a.h4] = h4.get(a.h4, 0) + 1
        h6[a.h6] = h6.get(a.h6, 0) + 1
    lines.append("H4 outcomes: " + ", ".join(f"{k} {h4.get(k, 0)}" for k in (
        hazards.BLOCK, hazards.REVIEW, hazards.GAP, hazards.PASS)))
    lines.append("H6 outcomes: " + ", ".join(f"{k} {h6.get(k, 0)}" for k in (
        hazards.FAIL, hazards.REVIEW, hazards.GAP, hazards.PASS)))
    return lines


_SQL_READ_ROWS = (
    "SELECT parcel_id, nfhl_parcel_sf, sfha_sf, floodway_sf, x500_sf, nwi_parcel_sf, "
    "wetland_sf FROM parcel_hazards WHERE parcel_id = ANY(%s)"
)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def _gather_files(conn: Any, inbox: Path, ids: Sequence[str]) -> dict[str, SourceData]:
    """Read the local files for both sources (FW-01's honest data route)."""
    boxes_cache: dict[int, dict[str, tuple]] = {}

    def boxes_in(srid: int) -> dict[str, tuple]:
        if srid not in boxes_cache:
            with conn.cursor() as cur:
                cur.execute(_SQL_BOXES_IN_SRID, (srid, list(ids)))
                boxes_cache[srid] = {r[0]: tuple(float(v) for v in r[1:5])
                                     for r in cur.fetchall()
                                     if all(v is not None for v in r[1:5])}
        return boxes_cache[srid]

    def covered(srid: int, fbox: Sequence[float]) -> list[str]:
        return sorted(p for p, b in boxes_in(srid).items()
                      if b[0] >= fbox[0] and b[1] >= fbox[1] and b[2] <= fbox[2]
                      and b[3] <= fbox[3])

    out: dict[str, SourceData] = {}
    # NFHL county package
    d = SourceData("nfhl")
    try:
        nd = inbox / "nfhl"
        shp = nd / f"{NFHL_FILE}.shp"
        if not shp.exists():
            raise SourceError(f"hazards_inbox/nfhl/{NFHL_FILE}.shp not found")
        feats, srid, ddate, fbox = read_shapefile(
            shp, NFHL_FIELDS, lambda s: list(boxes_in(s).values()))
        d.features, d.covered = sort_features(feats), covered(srid, fbox)
        d.origin = f"NFHL county file {NFHL_FILE}"
        versions = sorted({_poly_params("nfhl", f)[5] for f in feats} - {None})
        d.as_of = (", ".join(versions) if versions else "") or (
            f"dbf {ddate}" if ddate else "")
        lshp = nd / f"{LOMR_FILE}.shp"
        if lshp.exists():
            lf, _, _, _ = read_shapefile(lshp, (), lambda s: list(boxes_in(s).values()))
            d.lomr = sort_features(lf)
    except (SourceError, OSError, struct.error) as exc:
        d.failed = str(exc) if isinstance(exc, SourceError) else f"file read failed ({type(exc).__name__})"
    out["nfhl"] = d
    # NWI state shapefile
    w = SourceData("nwi")
    try:
        wd = inbox / "nwi"
        if not wd.is_dir():
            raise SourceError("hazards_inbox/nwi/ not found")
        shp = _single_shp(wd)
        feats, srid, ddate, fbox = read_shapefile(
            shp, NWI_FIELDS, lambda s: list(boxes_in(s).values()))
        w.features, w.covered = sort_features(feats), covered(srid, fbox)
        w.origin = "NWI state file"
        w.as_of = f"dbf {ddate}" if ddate else ""
    except (SourceError, OSError, struct.error) as exc:
        w.failed = str(exc) if isinstance(exc, SourceError) else f"file read failed ({type(exc).__name__})"
    out["nwi"] = w
    return out


def _gather_rest(fetcher: HazardsFetcher, ids: Sequence[str],
                 bbox: Mapping[str, Sequence[float]]) -> dict[str, SourceData]:
    out: dict[str, SourceData] = {}
    boxes = [bbox[p] for p in ids if p in bbox]
    for key, fn in (("nfhl", fetch_rest_nfhl), ("nwi", fetch_rest_nwi)):
        try:
            data = fn(fetcher, boxes)
            data.covered = list(ids)
        except SourceError as exc:
            data = SourceData(key, failed=str(exc))
        except (ValueError, TypeError, KeyError, AttributeError):
            data = SourceData(key, failed="malformed answer")
        out[key] = data
    return out


def run(*, mode: str = "files", inbox: Path = INBOX, fetcher: HazardsFetcher | None = None,
        check_open_run: bool = True, log_path: Path = EXPERIMENT_LOG,
        clock: Callable[[], float] = time.monotonic) -> int:
    """0 imported, 1 refused, 2 database, 3 run has its baseline, 4 a source FAILED."""
    started = clock()
    tag = prepare.current_run_tag()
    if zoning_import.tsv_has_baseline(tag, log_path):
        print("REFUSED: this run already has its baseline. Importing flood/wetland data now "
              "would change what later experiments score. Start a new run first, then import "
              "before its baseline.")
        return EXIT_RUN_OPEN
    if mode not in ("files", "rest"):
        print("REFUSED: --mode must be files or rest")
        return EXIT_REFUSED
    if not hazards.enabled():
        print("REFUSED: the parcel_hazards table is not in prepare.py. It is a proposal "
              "(reviews/26_flood_wetlands/01_prepare_mutation_proposal.md) that only a human "
              "applies, between runs.")
        return EXIT_REFUSED
    if not prepare.dsn_available():
        print("hazards not imported: no DATABASE_URL.")
        return EXIT_DB
    print(f"hazards_import: mode {mode}; as of {date.today().isoformat()}")
    # Pass 1 (read-only): run-state guard, table, parcels.
    try:
        with prepare.get_connection() as conn:
            why = zoning_import.mirror_blocks(conn, tag) if (check_open_run or tag) else None
            exists, parcels = False, []
            if not why:
                with conn.cursor() as cur:
                    cur.execute(_SQL_TABLE_EXISTS)
                    exists = bool((cur.fetchone() or (False,))[0])
                if exists:
                    with conn.cursor() as cur:
                        cur.execute(_SQL_LIST_PARCELS, (hazards.SUPPORTED_COUNTIES[0],))
                        parcels = list(cur.fetchall())
            conn.rollback()
    except Exception as exc:
        print(f"hazards not imported: database error ({type(exc).__name__}); nothing written.")
        return EXIT_DB
    if why:
        print(f"REFUSED: {why}. Importing flood/wetland data now would change what later "
              "experiments score. Stop the run (make halt) or start a new run first.")
        return EXIT_RUN_OPEN
    if not exists:
        print("REFUSED: the database has no parcel_hazards table (apply prepare.py's schema "
              "first: make db-check).")
        return EXIT_REFUSED
    empty = [r for r in parcels if len(r) > 7 and r[7]]
    usable = [r for r in parcels if not (len(r) > 7 and r[7])]
    inside = [r for r in usable if hazards.in_area_of_use(r[1], r[2])]
    ids = [str(r[0]) for r in inside]
    bbox = {str(r[0]): tuple(float(v) for v in r[3:7]) for r in inside}
    print(f"parcels: {len(parcels)} Fulton parcels with geometry; {len(usable) - len(ids)} "
          f"outside EPSG:2240's area of use (not imported); {len(empty)} with no polygonal "
          "area after repair (not imported)")
    if not ids:
        print("hazards_import: no parcels to import; nothing to do.")
        return EXIT_OK
    fetcher = fetcher if fetcher is not None else (HazardsFetcher() if mode == "rest" else None)
    results: dict[str, dict[str, int]] = {}
    failed: dict[str, str] = {}
    committed: list[str] = []
    try:
        with prepare.get_connection() as conn:
            if mode == "files":
                gathered = _gather_files(conn, inbox, ids)
                conn.rollback()
            else:
                gathered = _gather_rest(fetcher, ids, bbox)
            for key in SOURCES:
                data = gathered[key]
                if data.failed:
                    failed[key] = data.failed
                    print(f"source {key}: FAILED, nothing written: {data.failed}")
                    continue
                try:
                    n_before = _count_parcels(conn)
                    res = write_source(conn, data)
                    if _count_parcels(conn) != n_before:
                        raise SourceError(f"{key}: the parcel count changed during the import")
                    conn.commit()
                    committed.append(key)
                    results[key] = res
                    print(f"source {key}: {res['features']} features, {res['covered']} parcels "
                          f"covered, {res['changed']} changed, {res['unchanged']} unchanged; "
                          f"{len(data.lomr) if data.lomr is not None else 'no'} LOMR features; "
                          f"{res['not_covered_kept']} previously imported parcels not covered "
                          "(previous values kept)"
                          if key == "nfhl" else
                          f"source {key}: {res['features']} features, {res['covered']} parcels "
                          f"covered, {res['changed']} changed, {res['unchanged']} unchanged; "
                          f"{res['not_covered_kept']} previously imported parcels not covered "
                          "(previous values kept)")
                except Exception as exc:
                    conn.rollback()
                    if _connection_unusable(conn):
                        raise
                    msg = str(exc) if isinstance(exc, SourceError) else (
                        f"computation failed ({type(exc).__name__})")
                    failed[key] = msg
                    print(f"source {key}: FAILED, nothing written: {msg}")
            assess = hazards.assess_parcels(conn, ids)
            with conn.cursor() as cur:
                cur.execute(_SQL_READ_ROWS, (ids,))
                rows = {str(r[0]): dict(zip(("nfhl_parcel_sf", "sfha_sf", "floodway_sf",
                                             "x500_sf", "nwi_parcel_sf", "wetland_sf"), r[1:]))
                        for r in cur.fetchall()}
            conn.rollback()
    except Exception as exc:
        if committed:
            # Review finding 6: a source already committed is durable.
            rest = [k for k in SOURCES if k not in committed and k not in failed]
            print(f"hazards import: database error ({type(exc).__name__}) after source(s) "
                  f"{', '.join(committed)} committed; "
                  + (f"{', '.join(rest)} not written. " if rest else "")
                  + "The committed data changes H4/H6 and site fit: start a new run and "
                  "baseline after a complete import; never import mid-run.")
        else:
            print(f"hazards not imported: database error ({type(exc).__name__}).")
        return EXIT_DB
    for line in summarise(assess, rows):
        print(line)
    if fetcher is not None:
        for line in rules_scrape.robots_exception_lines(fetcher):
            print(line)
        print("requests: " + (", ".join(f"{h} {k}" for h, k in sorted(fetcher.requests.items()))
                              or "none"))
    print(f"wall time {clock() - started:.0f} s")
    print("The first populated import changes H4/H6 and site fit: start a new run and "
          "baseline after it; never import mid-run.")
    if failed:
        print(f"{len(failed)} source(s) FAILED: {', '.join(sorted(failed))}; their previous "
              "values were kept. Exit 4.")
        return EXIT_SOURCE_FAILED
    return EXIT_OK


def _count_parcels(conn: Any) -> int:
    with conn.cursor() as cur:
        cur.execute(_SQL_COUNT_PARCELS)
        return int((cur.fetchone() or (0,))[0])


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=("files", "rest"), default="files",
                    help="files: read hazards_inbox/ (default); rest: the public services, "
                         "robots.txt permitting")
    ap.add_argument("--no-open-run-check", action="store_true",
                    help="off a run branch: skip the 'newest mirror row is not a halt' check")
    args = ap.parse_args(argv)
    return run(mode=args.mode, check_open_run=not args.no_open_run_check)


if __name__ == "__main__":
    sys.exit(main())
