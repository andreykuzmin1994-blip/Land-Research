"""Import, per parcel, which boundary parts face which neighbour zoning.

Operator tool, run BETWEEN runs only (never by the agent, never mid-run):

    python adjacency_import.py
    make adjacency-import

Design and risk register: reviews/27_site_fit_buffers/. It writes ONLY the
proposed ``parcel_adjacency`` table, and refuses (exit 1) until a human has
added that table to prepare.py through the prepare-mutation protocol
(``adjacency.enabled()``, reviews/27_site_fit_buffers/01_prepare_mutation_
proposal.md). Once any zoning jurisdiction is verified, the first populated
import changes site fit and so BTS and the metric: import before a new run's
baseline, never after it. It refuses (exit 3) once the TSV or the database's
experiment_log_mirror shows the run's baseline (zoning_import's guard,
read-only), checked before any request.

What it fetches
---------------
Only the role="zoning" layers a HUMAN registered in sources.json at
county_parcel_data.fulton_ga.zoning_sources (read through
``zoning_import.load_config``, never written). For each, around every
Fulton parcel (envelope tiles, ids by envelope, then features by ids, every
page checked): the polygon geometry and two fields, the layer's object-id
field and the source's zoning field. No owner, address, sale or parcel-id
field is ever requested, and no neighbour parcel id is stored (BUF-05, SR-8).
A "join" source (a parcel-keyed attribute layer) is used only when its layer
has polygon geometry; otherwise it is skipped (named) and its territory
reads as uncovered, so "adjacency unknown". Each source has a budget of
``MAX_REQUESTS_PER_SOURCE`` requests: a failed, truncated or short page, a
missing field or a spent budget fails the WHOLE source (nothing of it is
used; exit 4, named on the last line). Zoneomics, Municode and listing
sites are never contacted (rules_scrape's denylist).

HTTP: rules_scrape's PoliteFetcher and robots.txt policy UNCHANGED (robots
for this tool's own token and the AI-agent tokens, checked before any query;
404/410 allows all, anything that is not a clean text 2xx denies all), 1
request/second per host, bounded retries, Retry-After honoured and capped,
generic User-Agent with no personal contact (SR-11, BUF-19).

What it computes (in PostGIS, SRID 2240 US survey feet, ONE transaction)
------------------------------------------------------------------------
Over the SAME made-valid parcel geometry site fit buffers: the outward ring
buffer(parcel, ABUT_TOL_FT) - parcel; per (source, district) the part of
the ring its polygons cover, OPENED by MIN_SLIVER_WIDTH_FT (eroded by half
of it, dilated back), so every part under that width is a sliver (counted,
never stored) even where the same district is solid elsewhere on the ring;
an opened cover under MIN_OVERLAP_AREA_SF is a sliver too. Each part of the
parcel boundary (outer rings and holes of every part) whose outward band
(the one-sided flat buffer, ABUT_TOL_FT deep, on the outside of each
boundary edge) meets an opened cover WITHIN REACH is stored as a 'zoned'
segment with the RAW district code, the source key and the source's
jurisdictions (only the part of the edge that faces the neighbour: no wrap
around corners, no diagonal neighbours). Within reach = within
MISALIGNMENT_ALLOWANCE_FT of the edge, or of a gap of the source's covers
that touches the edge: a neighbour behind more than that of another
polygon of the same layer (a parcel's own district digitised to a street
centreline) does not abut; the polygon in between does. Boundary parts that
face no cover within reach are stored as 'uncovered' (adjacency unknown;
never ROW, never "no neighbour", BUF-04). Pieces shorter than
MIN_SEGMENT_LEN_FT are dropped. A feature whose district code is not
readable ('UNKNOWN', 'TBD', ...; ``adjacency.readable_code``) is no polygon:
its area reads uncovered. Every row carries
md5(ST_AsBinary(geometry)) of the parcel as stored, so a later geometry
change reads stale (BUF-13). Spatial work runs in TEMP tables with GiST
indexes (BUF-23). A parcel's rows are replaced only when their content
changed, so a re-run with the same inputs changes 0 rows (SR-9).

Output: counts only (per jurisdiction and per class) -- never a parcel id,
an owner, an address or a feature attribute (BUF-20).

Exit codes: 0 imported; 1 refused (adjacency table not enabled or not in the
database, sources.json key missing or malformed, bad arguments); 2 no
database / database error (nothing written: one transaction); 3 a run has
its baseline; 4 imported, but at least one source FAILED (named on the last
line); its territory reads as adjacency unknown.

Imports prepare, zoning, adjacency, hazards (EPSG:2240's area of use),
zoning_import (the run-state guard, sources.json reader, tiles, Esri rings)
and rules_scrape (the robots policy) -- never research.py, runner.py,
site_fit.py or screener/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode, urlparse

import adjacency
import hazards
import prepare
import rules_scrape
import zoning
import zoning_import

REPO_ROOT = Path(__file__).resolve().parent
EXPERIMENT_LOG = REPO_ROOT / "experiment_log.tsv"
SOURCES_PATH = REPO_ROOT / "sources.json"

EXIT_OK, EXIT_REFUSED, EXIT_DB, EXIT_RUN_OPEN, EXIT_SOURCE_FAILED = 0, 1, 2, 3, 4

USER_AGENT = ("Land-Research-AdjacencyImport/1.0 (between-runs operator tool; "
              "1 req/s per host; honours robots.txt)")
UA_TOKEN = "Land-Research-AdjacencyImport"
MAX_REQUESTS = 3000
MAX_REQUESTS_PER_SOURCE = 400      # zoning_import's budget; a runaway source fails
TILE_DEG = 0.01                    # envelope grid for the objectId queries
PAD_DEG = 0.001                    # parcel envelope padding (~100 m >> ABUT_TOL_FT)
OID_CHUNK = 100
GEOMETRY_PRECISION = 7
COUNTY = "fulton"
COVERAGE_SOURCE = "coverage"       # source label of 'uncovered' rows


class Refused(Exception):
    """Operator-facing refusal (exit 1)."""


class SourceError(Exception):
    """One source failed. The message names a host, a status and counts only."""


# ---------------------------------------------------------------------------
# HTTP: rules_scrape's robots parser and fetch policy, this tool's identity
# ---------------------------------------------------------------------------
class AdjacencyFetcher(rules_scrape.PoliteFetcher):
    """rules_scrape.PoliteFetcher (robots for every token, 1 req/s per host,
    bounded retries, Retry-After capped, manual redirects, no cookies) with
    this tool's User-Agent and robots token. ``robots_for`` is inherited
    unchanged; no per-host override exists."""

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
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        why = robots_verdict(rb, path)
        if why:
            raise rules_scrape.Unreachable(why)

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


def robots_verdict(rb: "rules_scrape.Robots", path: str) -> str | None:
    """rules_scrape.Robots.verdict for this tool's token plus the AI-agent
    tokens rules_scrape honours (hazards_import's rule, same order)."""
    if rb.deny_all:
        return f"robots unavailable ({rb.reason})"
    for tok in (UA_TOKEN,) + tuple(rules_scrape.AI_AGENT_TOKENS):
        if not rb.allowed(path, tok):
            return f"robots.txt disallows this path for {tok}"
    if rb.global_signals.get("ai-input") == "no":
        return "robots.txt Content-Signal ai-input=no"
    for tok in (UA_TOKEN,) + tuple(rules_scrape.AI_AGENT_TOKENS):
        for g in rb._groups_for(tok):
            if g.signals.get("ai-input") == "no":
                return "robots.txt Content-Signal ai-input=no"
    return None


# ---------------------------------------------------------------------------
# Fetching one source (BUF-05)
# ---------------------------------------------------------------------------
@dataclass
class Feature:
    oid: int
    district: str
    geojson: str


@dataclass
class SourceData:
    key: str
    jurisdictions: tuple[str, ...]
    features: list[Feature] = field(default_factory=list)
    as_of: str = ""
    failed: str | None = None
    skipped: str | None = None
    rejected_outside: int = 0
    no_district: int = 0
    requests: int = 0


class _Budget:
    def __init__(self, fetcher: AdjacencyFetcher, limit: int = MAX_REQUESTS_PER_SOURCE) -> None:
        self.f, self.limit, self.start = fetcher, limit, fetcher.total()

    def get(self, url: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if self.f.total() - self.start >= self.limit:
            raise SourceError(f"request budget of {self.limit} exceeded")
        return self.f.get_json(url, params)

    def used(self) -> int:
        return self.f.total() - self.start


def _as_of(meta: Mapping[str, Any]) -> str:
    ms = (meta.get("editingInfo") or {}).get("lastEditDate") if isinstance(
        meta.get("editingInfo"), dict) else None
    if isinstance(ms, (int, float)) and ms > 0:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date().isoformat()
    return ""


def _in_area(rings: Any) -> bool:
    for r in rings or []:
        for p in r or []:
            if not (isinstance(p, list) and len(p) >= 2 and hazards.in_area_of_use(p[0], p[1])):
                return False
    return True


def _oid_field(meta: Mapping[str, Any]) -> str | None:
    """The layer's object-id field: ``objectIdField`` (FeatureServer), else
    the one field typed esriFieldTypeOID (MapServer layers list it only so)."""
    oid = meta.get("objectIdField")
    if isinstance(oid, str) and oid:
        return oid
    typed = [f.get("name") for f in meta.get("fields") or []
             if isinstance(f, dict) and f.get("type") == "esriFieldTypeOID"]
    return typed[0] if len(typed) == 1 and isinstance(typed[0], str) else None


def fetch_source(f: AdjacencyFetcher, src: "zoning_import.ZoningSource",
                 bboxes: Sequence[Sequence[float]]) -> SourceData:
    """Every polygon of ``src`` around the parcels, or a failed/skipped source."""
    data = SourceData(src.key, tuple(src.jurisdictions))
    b = _Budget(f)
    host = urlparse(src.url).hostname or "?"
    try:
        meta = b.get(src.url, {"f": "json"})
        names = {x.get("name") for x in meta.get("fields") or [] if isinstance(x, dict)}
        if src.zoning_field not in names:
            raise SourceError(f"{host}: zoning field missing from the layer")
        if meta.get("geometryType") != "esriGeometryPolygon":
            if src.kind == "join":
                data.skipped = "join layer has no polygon geometry (neighbours read as unknown)"
                data.requests = b.used()
                return data
            raise SourceError(f"{host}: not a polygon layer")
        oid_field = _oid_field(meta)
        if not (isinstance(oid_field, str) and zoning_import._FIELD_RE.match(oid_field)):
            raise SourceError(f"{host}: layer has no object-id field")
        mrc = meta.get("maxRecordCount")
        page = max(1, min(OID_CHUNK, int(mrc))) if isinstance(mrc, int) and mrc > 0 \
            else OID_CHUNK
        data.as_of = _as_of(meta)
        padded = [(x0 - PAD_DEG, y0 - PAD_DEG, x1 + PAD_DEG, y1 + PAD_DEG)
                  for x0, y0, x1, y1 in bboxes]
        oids: set[int] = set()
        for env in zoning_import.tiles_for(padded, TILE_DEG):
            body = b.get(f"{src.url}/query", {
                "where": "1=1", "geometry": ",".join(f"{v:.6f}" for v in env),
                "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects", "returnIdsOnly": "true", "f": "json"})
            if body.get("exceededTransferLimit"):
                raise SourceError(f"{host}: object id list truncated")
            ids = body.get("objectIds")
            if ids is None:
                ids = []
            if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
                raise SourceError(f"{host}: object id answer malformed")
            oids.update(ids)
        ordered = sorted(oids)
        out_fields = f"{oid_field},{src.zoning_field}"
        feats: dict[int, Feature] = {}
        for i in range(0, len(ordered), page):
            chunk = ordered[i:i + page]
            body = b.get(f"{src.url}/query", {
                "objectIds": ",".join(str(o) for o in chunk), "outFields": out_fields,
                "returnGeometry": "true", "outSR": 4326,
                "geometryPrecision": GEOMETRY_PRECISION, "f": "json"})
            got = body.get("features")
            if body.get("exceededTransferLimit") or not isinstance(got, list) \
                    or len(got) != len(chunk):
                raise SourceError(f"{host}: a feature page came back truncated or short")
            for ft in got:
                a = (ft or {}).get("attributes") if isinstance(ft, dict) else None
                if not isinstance(a, dict) or oid_field not in a or src.zoning_field not in a:
                    raise SourceError(f"{host}: a feature lacks an expected field")
                rings = (ft.get("geometry") or {}).get("rings")
                gj = zoning_import.esri_rings_to_geojson(rings)
                if gj is None:
                    raise SourceError(f"{host}: a feature without polygon geometry")
                z = zoning_import._val(a, src.zoning_field)
                if z is None or not adjacency.readable_code(z):
                    data.no_district += 1
                    continue
                if not _in_area(rings):
                    data.rejected_outside += 1
                    continue
                oid = a[oid_field]
                if not isinstance(oid, int):
                    raise SourceError(f"{host}: object id malformed")
                feats.setdefault(oid, Feature(oid, z, gj))
        data.features = [feats[k] for k in sorted(feats)]
    except SourceError as exc:
        data.failed, data.features = str(exc), []
    except (ValueError, TypeError, KeyError, AttributeError):
        data.failed, data.features = "malformed answer", []
    data.requests = b.used()
    return data


# ---------------------------------------------------------------------------
# SQL (SR-2: module-level constants, %s placeholders only)
# ---------------------------------------------------------------------------
_SQL_TABLE_EXISTS = "SELECT to_regclass('parcel_adjacency') IS NOT NULL"
# Fulton parcels with geometry: made-valid centroid (EPSG:2240 area of use),
# envelope, empty-after-repair flag, and the stored zoning (counts only).
_SQL_LIST_PARCELS = (
    "SELECT parcel_id, ST_X(ST_Centroid(v)), ST_Y(ST_Centroid(v)), "
    "ST_XMin(v), ST_YMin(v), ST_XMax(v), ST_YMax(v), ST_IsEmpty(v), zoning, "
    "zoning_description FROM ("
    "SELECT parcel_id, zoning, zoning_description, "
    "ST_CollectionExtract(ST_MakeValid(geometry), 3) AS v "
    "FROM parcels WHERE county = %s AND geometry IS NOT NULL) s ORDER BY parcel_id"
)
_SQL_COUNT_PARCELS = "SELECT COUNT(*) FROM parcels"
_SQL_CREATE_POLYS = (
    "CREATE TEMP TABLE IF NOT EXISTS adjacency_import_polys ("
    "src TEXT NOT NULL, oid BIGINT NOT NULL, district TEXT NOT NULL, jurs TEXT NOT NULL, "
    "asof TEXT NOT NULL, geom geometry) ON COMMIT DROP"
)
_SQL_INSERT_POLY = (
    "INSERT INTO adjacency_import_polys (src, oid, district, jurs, asof, geom) "
    "SELECT %s, %s, %s, %s, %s, ST_Subdivide(g, 256) FROM ("
    "SELECT ST_CollectionExtract(ST_MakeValid(ST_Transform(ST_CollectionExtract("
    "ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3), 2240)), 3) AS g) s "
    "WHERE NOT ST_IsEmpty(g)"
)
_SQL_INDEX_POLYS = "CREATE INDEX ON adjacency_import_polys USING GIST (geom)"
_SQL_ANALYZE_POLYS = "ANALYZE adjacency_import_polys"
_SQL_CREATE_PARCELS = (
    "CREATE TEMP TABLE IF NOT EXISTS adjacency_import_parcels ("
    "parcel_id TEXT PRIMARY KEY, gmd5 TEXT NOT NULL, g geometry, bd geometry, ring geometry"
    ") ON COMMIT DROP"
)
# The parcel geometry site fit buffers (made valid, polygonal, SRID 2240),
# its boundary (outer rings and holes of every part) and the outward ring.
# Parameters: (tolerance, ids).
_SQL_INSERT_PARCELS = (
    "INSERT INTO adjacency_import_parcels (parcel_id, gmd5, g, bd, ring) "
    "SELECT parcel_id, gmd5, g, ST_Boundary(g), ST_Difference(ST_Buffer(g, %s::float8), g) "
    "FROM (SELECT parcel_id, md5(ST_AsBinary(geometry)) AS gmd5, "
    "ST_Transform(ST_CollectionExtract(ST_MakeValid(geometry), 3), 2240) AS g "
    "FROM parcels WHERE parcel_id = ANY(%s) AND geometry IS NOT NULL) s "
    "WHERE NOT ST_IsEmpty(g)"
)
_SQL_INDEX_PARCELS = "CREATE INDEX ON adjacency_import_parcels USING GIST (ring)"
_SQL_CREATE_COVER = (
    "CREATE TEMP TABLE IF NOT EXISTS adjacency_import_cover ("
    "parcel_id TEXT NOT NULL, src TEXT NOT NULL, district TEXT NOT NULL, jurs TEXT NOT NULL, "
    "asof TEXT NOT NULL, c geometry, raw geometry, solid BOOLEAN) ON COMMIT DROP"
)
# Per parcel and (source, district): the ring part its polygons cover (raw),
# that part opened by the sliver width (c: every part under the width
# removed, review finding 4), and whether the opened part is solid (area
# floor; BUF-08). Parameters: (min area, min width, min width).
_SQL_INSERT_COVER = (
    "INSERT INTO adjacency_import_cover (parcel_id, src, district, jurs, asof, c, raw, solid) "
    "SELECT parcel_id, src, district, jurs, asof, o, c, "
    "(NOT ST_IsEmpty(o) AND ST_Area(o) >= %s::float8) FROM ("
    "SELECT parcel_id, src, district, jurs, asof, c, ST_CollectionExtract(ST_Buffer(ST_Buffer("
    "c, -0.5 * %s::float8), 0.5 * %s::float8), 3) AS o "
    "FROM (SELECT pr.parcel_id, z.src, z.district, z.jurs, z.asof, "
    "ST_CollectionExtract(ST_Union(ST_CollectionExtract(ST_Intersection(pr.ring, z.geom), 3) "
    "ORDER BY z.oid, ST_AsBinary(z.geom)), 3) AS c "
    "FROM adjacency_import_parcels pr JOIN adjacency_import_polys z "
    "ON z.geom && pr.ring AND ST_Intersects(z.geom, pr.ring) "
    "GROUP BY pr.parcel_id, z.src, z.district, z.jurs, z.asof) s1 "
    "WHERE c IS NOT NULL AND NOT ST_IsEmpty(c)) s"
)
_SQL_CREATE_EDGES = (
    "CREATE TEMP TABLE IF NOT EXISTS adjacency_import_edges ("
    "eid BIGSERIAL PRIMARY KEY, parcel_id TEXT NOT NULL, e geometry, band geometry"
    ") ON COMMIT DROP"
)
# Every boundary edge (outer rings and holes of every part) of the polygon
# forced to exterior-CCW / holes-CW, so the polygon interior is always on the
# edge's left; its outward band is the one-sided flat buffer on the right,
# ABUT_TOL_FT deep. A neighbour polygon only in the wedge at a convex corner
# (a diagonal neighbour) meets no band. Parameters: (tolerance).
_SQL_INSERT_EDGES = (
    "INSERT INTO adjacency_import_edges (parcel_id, e, band) "
    "SELECT parcel_id, e, ST_Buffer(e, %s::float8, 'side=right endcap=flat') FROM ("
    "SELECT parcel_id, (ST_DumpSegments(ST_ForcePolygonCCW(g))).geom AS e "
    "FROM adjacency_import_parcels) s WHERE ST_Length(e) > 0 ORDER BY parcel_id, e"
)
_SQL_INDEX_EDGES = "CREATE INDEX ON adjacency_import_edges USING GIST (band)"
_SQL_CREATE_REACH = (
    "CREATE TEMP TABLE IF NOT EXISTS adjacency_import_reach ("
    "eid BIGINT NOT NULL, src TEXT NOT NULL, reach geometry) ON COMMIT DROP"
)
# Per edge and source (review finding 7): the part of the edge's band a
# neighbour must reach to abut it: the strip within the misalignment
# allowance of the edge, plus the allowance around every gap of the
# source's opened covers that touches the edge (a layer offset from the
# parcel fabric). A polygon behind more than the allowance of another
# polygon of the same layer (an own district digitised to a street
# centreline) is out of reach. Parameters: (allowance, allowance).
_SQL_INSERT_REACH = (
    "INSERT INTO adjacency_import_reach (eid, src, reach) "
    "SELECT q.eid, q.src, ST_CollectionExtract(ST_Intersection(q.band, CASE WHEN q.gap IS NULL "
    "THEN q.near ELSE ST_Union(q.near, ST_Buffer(q.gap, %s::float8)) END), 3) FROM ("
    "SELECT e.eid, u.src, e.band, ST_Buffer(e.e, %s::float8, 'side=right endcap=flat') AS near, "
    "(SELECT ST_Union(d.geom ORDER BY ST_AsBinary(d.geom)) FROM ST_Dump(ST_CollectionExtract("
    "ST_Difference(e.band, u.u), 3)) d WHERE ST_DWithin(d.geom, e.e, 0.01)) AS gap "
    "FROM adjacency_import_edges e JOIN ("
    "SELECT parcel_id, src, ST_Union(c ORDER BY district) AS u FROM adjacency_import_cover "
    "GROUP BY parcel_id, src) u ON u.parcel_id = e.parcel_id) q"
)
_SQL_INDEX_REACH = "CREATE INDEX ON adjacency_import_reach (eid, src)"
_SQL_CREATE_NEW = (
    "CREATE TEMP TABLE IF NOT EXISTS adjacency_import_new ("
    "parcel_id TEXT NOT NULL, kind TEXT NOT NULL, district TEXT, jurs TEXT, source TEXT NOT NULL, "
    "asof TEXT, gmd5 TEXT NOT NULL, segment geometry) ON COMMIT DROP"
)
# Per edge and per solid opened cover: each polygon piece of (band
# intersect cover intersect reach) is projected back onto the edge (its
# extreme ST_LineLocatePoint fractions), so only the part of the edge that
# faces the neighbour is stored, and two separate pieces of one edge are
# never bridged. Merged per (source, district); pieces under the length
# floor dropped. Parameters: (min length).
_SQL_INSERT_ZONED = (
    "INSERT INTO adjacency_import_new (parcel_id, kind, district, jurs, source, asof, gmd5, "
    "segment) "
    "SELECT parcel_id, 'zoned', district, jurs, src, asof, gmd5, "
    "ST_Multi(ST_LineMerge(ST_Collect(piece ORDER BY ST_AsBinary(piece)))) FROM ("
    "SELECT parcel_id, district, jurs, src, asof, gmd5, "
    "(ST_Dump(ST_LineMerge(ST_Union(sub ORDER BY ST_AsBinary(sub))))).geom AS piece FROM ("
    "SELECT c.parcel_id, c.district, c.jurs, c.src, c.asof, pr.gmd5, ST_LineSubstring(e.e, "
    "MIN(ST_LineLocatePoint(e.e, pt.geom)), MAX(ST_LineLocatePoint(e.e, pt.geom))) AS sub "
    "FROM adjacency_import_cover c "
    "JOIN adjacency_import_parcels pr ON pr.parcel_id = c.parcel_id "
    "JOIN adjacency_import_edges e ON e.parcel_id = c.parcel_id AND e.band && c.c "
    "JOIN adjacency_import_reach r ON r.eid = e.eid AND r.src = c.src "
    "CROSS JOIN LATERAL ST_Dump(ST_CollectionExtract(ST_Intersection(ST_CollectionExtract("
    "ST_Intersection(e.band, c.c), 3), r.reach), 3)) part "
    "CROSS JOIN LATERAL ST_DumpPoints(part.geom) pt "
    "WHERE c.solid AND ST_Area(part.geom) > 0.01 "
    "GROUP BY c.parcel_id, c.district, c.jurs, c.src, c.asof, pr.gmd5, e.eid, e.e, part.path"
    ") s GROUP BY parcel_id, district, jurs, src, asof, gmd5) q "
    "WHERE ST_Length(piece) >= %s::float8 "
    "GROUP BY parcel_id, district, jurs, src, asof, gmd5"
)
# Boundary not covered by any zoned segment: adjacency unknown (BUF-04).
# Parameters: (min length).
_SQL_INSERT_UNCOVERED = (
    "INSERT INTO adjacency_import_new (parcel_id, kind, district, jurs, source, asof, gmd5, "
    "segment) "
    "SELECT parcel_id, 'uncovered', NULL, NULL, '" + COVERAGE_SOURCE + "', NULL, gmd5, "
    "ST_Multi(ST_LineMerge(ST_Collect(piece ORDER BY ST_AsBinary(piece)))) FROM ("
    "SELECT pr.parcel_id, pr.gmd5, (ST_Dump(ST_LineMerge(ST_CollectionExtract("
    "CASE WHEN zu.s IS NULL THEN pr.bd "
    "ELSE ST_Difference(pr.bd, ST_Buffer(zu.s, 0.01)) END, 2)))).geom AS piece "
    "FROM adjacency_import_parcels pr LEFT JOIN ("
    "SELECT parcel_id, ST_Union(segment ORDER BY source, district) AS s "
    "FROM adjacency_import_new WHERE kind = 'zoned' GROUP BY parcel_id) zu "
    "ON zu.parcel_id = pr.parcel_id) q "
    "WHERE ST_Length(piece) >= %s::float8 GROUP BY parcel_id, gmd5"
)
# Counts only: covered ring parts that are slivers (never a buffer): whole
# covers with no solid opened part, and thin parts removed from a solid one
# (each at least the length floor long: perimeter >= twice it).
# Parameters: (min length).
_SQL_SLIVERS = (
    "SELECT COUNT(*), COUNT(DISTINCT parcel_id) FROM ("
    "SELECT parcel_id FROM adjacency_import_cover WHERE NOT solid UNION ALL "
    "SELECT cv.parcel_id FROM adjacency_import_cover cv CROSS JOIN LATERAL ST_Dump("
    "ST_CollectionExtract(ST_Difference(cv.raw, ST_Buffer(cv.c, 0.01)), 3)) d "
    "WHERE cv.solid AND ST_Perimeter(d.geom) >= 2.0 * %s::float8) s"
)
# Pairs of zoned rows of one parcel with different district codes that
# share a boundary part (both rows are stored; the read applies the
# stricter). Classified in Python against the parcel's own district (review
# finding 8): a pair with the parcel's own code is its own polygon
# overhanging the band, not two neighbours. Parameters: (min length).
_SQL_AMBIGUOUS = (
    "SELECT DISTINCT a.parcel_id, a.district, a.jurs, b.district, b.jurs "
    "FROM adjacency_import_new a "
    "JOIN adjacency_import_new b ON a.parcel_id = b.parcel_id AND a.kind = 'zoned' "
    "AND b.kind = 'zoned' AND a.district < b.district "
    "AND ST_Length(ST_CollectionExtract(ST_Intersection(a.segment, "
    "ST_Buffer(b.segment, 0.01)), 2)) >= %s::float8 "
    "ORDER BY 1, 2, 3, 4, 5"
)
_SQL_SRID_CHECK = (
    "SELECT COUNT(*) FROM adjacency_import_new WHERE ST_SRID(segment) <> 2240 "
    "OR GeometryType(segment) <> 'MULTILINESTRING'"
)
# Parcels whose new row set differs from the stored one (row text: kind,
# district, jurisdictions, source, as-of, parcel md5, segment WKB). Parameters:
# (ids, ids).
_SQL_CHANGED = (
    "WITH n AS (SELECT parcel_id, md5(string_agg(t, ',' ORDER BY t)) AS h FROM ("
    "SELECT parcel_id, concat_ws('|', kind, COALESCE(district, '-'), COALESCE(jurs, '-'), "
    "source, COALESCE(asof, '-'), gmd5, encode(ST_AsBinary(segment), 'hex')) AS t "
    "FROM adjacency_import_new) s GROUP BY parcel_id), "
    "o AS (SELECT parcel_id, md5(string_agg(t, ',' ORDER BY t)) AS h FROM ("
    "SELECT parcel_id, concat_ws('|', neighbour_kind, COALESCE(neighbour_district, '-'), "
    "COALESCE(neighbour_jurisdictions, '-'), source, COALESCE(source_as_of, '-'), "
    "parcel_geom_md5, encode(ST_AsBinary(segment), 'hex')) AS t "
    "FROM parcel_adjacency WHERE parcel_id = ANY(%s)) s GROUP BY parcel_id), "
    "ids AS (SELECT unnest(%s::text[]) AS parcel_id) "
    "SELECT ids.parcel_id FROM ids LEFT JOIN n ON n.parcel_id = ids.parcel_id "
    "LEFT JOIN o ON o.parcel_id = ids.parcel_id WHERE n.h IS DISTINCT FROM o.h "
    "ORDER BY ids.parcel_id"
)
_SQL_DELETE_ROWS = "DELETE FROM parcel_adjacency WHERE parcel_id = ANY(%s)"
_SQL_INSERT_ROWS = (
    "INSERT INTO parcel_adjacency (parcel_id, segment, neighbour_kind, neighbour_district, "
    "neighbour_jurisdictions, source, source_as_of, parcel_geom_md5, fetched_at) "
    "SELECT parcel_id, segment, kind, district, jurs, source, asof, gmd5, now() "
    "FROM adjacency_import_new WHERE parcel_id = ANY(%s) "
    "ORDER BY parcel_id, kind, source, district, ST_AsBinary(segment)"
)
# Per processed parcel, for the counts: (parcel_id, kind, district, jurs).
_SQL_NEW_SUMMARY = (
    "SELECT parcel_id, kind, district, jurs FROM adjacency_import_new "
    "ORDER BY parcel_id, kind, district"
)


# ---------------------------------------------------------------------------
# The write (one transaction, SR-5)
# ---------------------------------------------------------------------------
@dataclass
class WriteResult:
    processed: int = 0
    changed: int = 0
    unchanged: int = 0
    slivers: int = 0
    sliver_parcels: int = 0
    ambiguous_pairs: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    summary: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)


def compute_and_write(conn: Any, ids: Sequence[str], sources: Sequence[SourceData]) -> WriteResult:
    """Everything in the caller's open transaction; the caller commits."""
    res = WriteResult()
    tol, a = adjacency.ABUT_TOL_FT, adjacency
    with conn.cursor() as cur:
        cur.execute(_SQL_CREATE_POLYS)
        for s in sources:
            if s.failed or s.skipped:
                continue
            jurs = ",".join(sorted(s.jurisdictions))
            for ft in s.features:
                cur.execute(_SQL_INSERT_POLY, (s.key, ft.oid, ft.district, jurs, s.as_of,
                                               ft.geojson))
        cur.execute(_SQL_INDEX_POLYS)
        cur.execute(_SQL_ANALYZE_POLYS)
        cur.execute(_SQL_CREATE_PARCELS)
        cur.execute(_SQL_INSERT_PARCELS, (tol, list(ids)))
        cur.execute(_SQL_INDEX_PARCELS)
        cur.execute(_SQL_CREATE_COVER)
        cur.execute(_SQL_INSERT_COVER, (a.MIN_OVERLAP_AREA_SF, a.MIN_SLIVER_WIDTH_FT,
                                        a.MIN_SLIVER_WIDTH_FT))
        cur.execute(_SQL_CREATE_EDGES)
        cur.execute(_SQL_INSERT_EDGES, (tol,))
        cur.execute(_SQL_INDEX_EDGES)
        cur.execute(_SQL_CREATE_REACH)
        cur.execute(_SQL_INSERT_REACH, (a.MISALIGNMENT_ALLOWANCE_FT, a.MISALIGNMENT_ALLOWANCE_FT))
        cur.execute(_SQL_INDEX_REACH)
        cur.execute(_SQL_CREATE_NEW)
        cur.execute(_SQL_INSERT_ZONED, (a.MIN_SEGMENT_LEN_FT,))
        cur.execute(_SQL_INSERT_UNCOVERED, (a.MIN_SEGMENT_LEN_FT,))
        cur.execute(_SQL_SRID_CHECK)
        bad = int((cur.fetchone() or (0,))[0] or 0)
        if bad:
            raise SourceError(f"{bad} computed segment(s) not MultiLineString in SRID 2240")
        cur.execute(_SQL_SLIVERS, (a.MIN_SEGMENT_LEN_FT,))
        row = cur.fetchone() or (0, 0)
        res.slivers, res.sliver_parcels = int(row[0] or 0), int(row[1] or 0)
        cur.execute(_SQL_AMBIGUOUS, (a.MIN_SEGMENT_LEN_FT,))
        res.ambiguous_pairs = [tuple(str(v) if v is not None else "" for v in r[:5])
                               for r in cur.fetchall()]
        cur.execute(_SQL_CHANGED, (list(ids), list(ids)))
        changed = [str(r[0]) for r in cur.fetchall()]
        if changed:
            cur.execute(_SQL_DELETE_ROWS, (changed,))
            cur.execute(_SQL_INSERT_ROWS, (changed,))
        cur.execute(_SQL_NEW_SUMMARY)
        res.summary = [(str(r[0]), str(r[1]), r[2], r[3]) for r in cur.fetchall()]
    res.processed = len(ids)
    res.changed = len(changed)
    res.unchanged = len(ids) - len(changed)
    return res


# ---------------------------------------------------------------------------
# Counts (BUF-20: never an id or an attribute)
# ---------------------------------------------------------------------------
def summarise(stored: Mapping[str, tuple[Any, Any]], summary: Iterable[Sequence[Any]]
              ) -> list[str]:
    """Per jurisdiction: parcels with an other-district side, a residential /
    agricultural side (only through the human class table), an unknown
    side; codes are classified at READ time, so this mirrors the read."""
    per: dict[str, dict[str, set]] = {}
    for pid, kind, district, jurs in summary:
        z = stored.get(pid, (None, None))
        st = zoning.parse_stored(z[0], z[1])
        j = st.jurisdiction if st is not None else "unknown"
        b = per.setdefault(j, {k: set() for k in ("parcels", "other", "residential",
                                                  "agricultural", "unknown", "unclassified")})
        b["parcels"].add(pid)
        if kind == adjacency.KIND_UNCOVERED:
            b["unknown"].add(pid)
            continue
        dist = zoning.district_for_stored(z[0], z[1], pid)
        own = (dist.table_jurisdiction, dist.district) if dist is not None else None
        ids = adjacency.neighbour_identities([x for x in str(jurs or "").split(",") if x],
                                             district, own[0] if own is not None else None)
        if own is not None and ids and any(adjacency.same_district(i, own) for i in ids):
            continue
        b["other"].add(pid)
        cls, _ = adjacency.classify_neighbour(ids)
        if cls in (adjacency.RESIDENTIAL, adjacency.AGRICULTURAL):
            b[cls].add(pid)
        elif cls is None:
            b["unclassified"].add(pid)
    out = []
    for j in sorted(per):
        v = per[j]
        out.append(f"jurisdiction {j}: {len(v['parcels'])} parcels; {len(v['other'])} with an "
                   f"other-district side; {len(v['residential'])} residential and "
                   f"{len(v['agricultural'])} agricultural (human class table); "
                   f"{len(v['unclassified'])} with an unclassified neighbour; "
                   f"{len(v['unknown'])} with an unknown side")
    return out


def ambiguity_counts(stored: Mapping[str, tuple[Any, Any]],
                     pairs: Iterable[Sequence[Any]]) -> tuple[int, int]:
    """(parcels with a boundary part facing two NEIGHBOUR districts, parcels
    where the part faces the parcel's own district polygon overhanging the
    band and a neighbour beyond it). Own = the code resolves to the parcel's
    current stored district (adjacency.neighbour_identities, same_district),
    as the read decides it. Counts only."""
    two: set[str] = set()
    own_overhang: set[str] = set()
    for pid, da, ja, db, jb in pairs:
        z = stored.get(pid, (None, None))
        dist = zoning.district_for_stored(z[0], z[1], pid)
        own = (dist.table_jurisdiction, dist.district) if dist is not None else None

        def is_own(code: Any, jurs: Any) -> bool:
            ids = adjacency.neighbour_identities(
                [x for x in str(jurs or "").split(",") if x], code,
                own[0] if own is not None else None)
            return own is not None and any(adjacency.same_district(i, own) for i in ids)
        if is_own(da, ja) or is_own(db, jb):
            own_overhang.add(pid)
        else:
            two.add(pid)
    return len(two), len(own_overhang - two)


def _bucket(sf: int | None) -> str:
    if sf is None:
        return "none"
    for lo, label in ((300_000, ">=300K"), (150_000, "150-300K"), (100_000, "100-150K"),
                      (50_000, "50-100K")):
        if sf >= lo:
            return label
    return "<50K"


BUCKETS = (">=300K", "150-300K", "100-150K", "50-100K", "<50K", "none")


def projection_report(before: Mapping[str, tuple[str, int | None, str]],
                      after: Mapping[str, tuple[str, int | None, str]]) -> list[str]:
    """Counts-only comparison of two projection sets (live report helper):
    each value is (status, max building SF or None, jurisdiction). Lines per
    jurisdiction: status transitions, SF bucket distribution before/after,
    parcels dropping below 150K, any parcel gaining SF (must be 0)."""
    out = []
    juris = sorted({v[2] for v in before.values()} | {v[2] for v in after.values()})
    for j in juris:
        ids = sorted(p for p in before if before[p][2] == j and p in after)
        trans: dict[tuple[str, str], int] = {}
        b_hist = {k: 0 for k in BUCKETS}
        a_hist = {k: 0 for k in BUCKETS}
        drop150 = gained = lowered = 0
        for p in ids:
            (bs, bsf, _), (as_, asf, _) = before[p], after[p]
            if bs != as_:
                trans[(bs, as_)] = trans.get((bs, as_), 0) + 1
            b_hist[_bucket(bsf)] += 1
            a_hist[_bucket(asf)] += 1
            if (bsf or 0) >= 150_000 > (asf or 0):
                drop150 += 1
            if (asf or 0) > (bsf or 0):
                gained += 1
            if (asf or 0) < (bsf or 0):
                lowered += 1
        out.append(f"jurisdiction {j}: {len(ids)} parcels; {lowered} with a lower max building "
                   f"SF; {drop150} dropped below 150K SF; {gained} gained SF")
        out.append(f"  max building SF before: " + ", ".join(f"{k} {b_hist[k]}" for k in BUCKETS))
        out.append(f"  max building SF after:  " + ", ".join(f"{k} {a_hist[k]}" for k in BUCKETS))
        if trans:
            out.append("  status transitions: " + ", ".join(
                f"{a} -> {b} {n}" for (a, b), n in sorted(trans.items())))
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _connection_unusable(conn: Any) -> bool:
    if getattr(conn, "broken", False) is True or getattr(conn, "closed", False) is True:
        return True
    status = getattr(getattr(conn, "info", None), "transaction_status", None)
    return getattr(status, "name", None) in ("INERROR", "UNKNOWN")


def _count_parcels(conn: Any) -> int:
    with conn.cursor() as cur:
        cur.execute(_SQL_COUNT_PARCELS)
        return int((cur.fetchone() or (0,))[0])


def run(*, sources_path: Path = SOURCES_PATH, fetcher: AdjacencyFetcher | None = None,
        check_open_run: bool = True, log_path: Path = EXPERIMENT_LOG,
        clock: Callable[[], float] = time.monotonic) -> int:
    """0 imported, 1 refused, 2 database, 3 run has its baseline, 4 a source FAILED."""
    started = clock()
    tag = prepare.current_run_tag()
    if zoning_import.tsv_has_baseline(tag, log_path):
        print("REFUSED: this run already has its baseline. Importing adjacency now would "
              "change what later experiments score. Start a new run first, then import before "
              "its baseline.")
        return EXIT_RUN_OPEN
    if not adjacency.enabled():
        print("REFUSED: the parcel_adjacency table is not in prepare.py. It is a proposal "
              "(reviews/27_site_fit_buffers/01_prepare_mutation_proposal.md) that only a human "
              "applies, between runs.")
        return EXIT_REFUSED
    if adjacency.schema_problem() is not None:
        print(f"REFUSED: {adjacency.schema_problem()}.")
        return EXIT_REFUSED
    try:
        _layer, zsources = zoning_import.load_config(sources_path)
    except zoning_import.Refused as exc:
        print(f"REFUSED: {exc}")
        return EXIT_REFUSED
    zsources = [s for s in zsources if s.role == "zoning"]
    if not prepare.dsn_available():
        print("adjacency not imported: no DATABASE_URL.")
        return EXIT_DB
    print(f"adjacency_import: as of {date.today().isoformat()}; {len(zsources)} zoning sources; "
          f"tolerance {adjacency.ABUT_TOL_FT:g} ft")
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
                        cur.execute(_SQL_LIST_PARCELS, (COUNTY,))
                        parcels = list(cur.fetchall())
            conn.rollback()
    except Exception as exc:
        print(f"adjacency not imported: database error ({type(exc).__name__}); nothing written.")
        return EXIT_DB
    if why:
        print(f"REFUSED: {why}. Importing adjacency now would change what later experiments "
              "score. Stop the run (make halt) or start a new run first.")
        return EXIT_RUN_OPEN
    if not exists:
        print("REFUSED: the database has no parcel_adjacency table (apply prepare.py's schema "
              "first: make db-check).")
        return EXIT_REFUSED
    empty = [r for r in parcels if r[7]]
    usable = [r for r in parcels if not r[7]]
    inside = [r for r in usable if hazards.in_area_of_use(r[1], r[2])]
    ids = [str(r[0]) for r in inside]
    stored = {str(r[0]): (r[8], r[9]) for r in inside}
    boxes = [tuple(float(v) for v in r[3:7]) for r in inside]
    print(f"parcels: {len(parcels)} Fulton parcels with geometry; {len(usable) - len(ids)} "
          f"outside EPSG:2240's area of use (not imported); {len(empty)} with no polygonal "
          "area after repair (not imported)")
    if not ids:
        print("adjacency_import: no parcels to import; nothing to do.")
        return EXIT_OK
    fetcher = fetcher if fetcher is not None else AdjacencyFetcher()
    gathered = [fetch_source(fetcher, s, boxes) for s in zsources]
    for d in gathered:
        if d.failed:
            print(f"source {d.key}: FAILED, nothing used: {d.failed} ({d.requests} requests)")
        elif d.skipped:
            print(f"source {d.key}: skipped: {d.skipped}")
        else:
            print(f"source {d.key}: {len(d.features)} polygons; {d.rejected_outside} outside "
                  f"EPSG:2240's area of use; {d.no_district} without a district; "
                  f"{d.requests} requests")
    try:
        with prepare.get_connection() as conn:
            n_before = _count_parcels(conn)
            res = compute_and_write(conn, ids, gathered)
            if _count_parcels(conn) != n_before:
                conn.rollback()
                print("REFUSED: the parcel count changed during the import; nothing written.")
                return EXIT_DB
            conn.commit()
    except Exception as exc:
        print(f"adjacency not imported: database error ({type(exc).__name__}); nothing written.")
        return EXIT_DB
    two, overhang = ambiguity_counts(stored, res.ambiguous_pairs)
    print(f"written: {res.processed} parcels; {res.changed} changed, {res.unchanged} unchanged; "
          f"{res.slivers} sliver pieces on {res.sliver_parcels} parcels (never a buffer); "
          f"{two} parcels with a side facing two neighbour districts (stricter applied at "
          f"read); {overhang} more where the parcel's own district polygon overhangs a "
          "neighbour's side (own coverage reads as the same district)")
    for line in summarise(stored, res.summary):
        print(line)
    print("requests: " + (", ".join(f"{h} {k}" for h, k in sorted(fetcher.requests.items()))
                          or "none"))
    print(f"wall time {clock() - started:.0f} s")
    print("Once any jurisdiction is verified, a populated import changes site fit and BTS: "
          "start a new run and baseline after it; never import mid-run.")
    failed = sorted(d.key for d in gathered if d.failed)
    if failed:
        print(f"{len(failed)} source(s) FAILED: {', '.join(failed)}; their territory reads as "
              "adjacency unknown. Exit 4.")
        return EXIT_SOURCE_FAILED
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-open-run-check", action="store_true",
                    help="off a run branch: skip the 'newest mirror row is not a halt' check "
                         "(the run-branch baseline checks always apply)")
    args = ap.parse_args(argv)
    return run(check_open_run=not args.no_open_run_check)


if __name__ == "__main__":
    sys.exit(main())
