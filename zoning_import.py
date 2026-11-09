"""Import each parcel's zoning district from public city/county GIS layers.

Operator tool, run BETWEEN runs only (never by the agent, never mid-run):

    python zoning_import.py
    make zoning-import

It refuses (exit 3) once a run has its baseline: new zoning changes what
S9 and H3 see, so the jump would be credited to whichever experiment ran
next (reviews/21_zoning/02_risk_review.md, Z-05). The refusal checks the
TSV (as costar_import does) AND the experiment_log_mirror table of the
target database, read-only, so a lost TSV or a second working copy cannot
slip past it. Immutable during a run, like costar_import.py.

What it does
------------
1. Reads the zoning sources from sources.json at
   county_parcel_data.fulton_ga.zoning_sources (HUMAN-OWNED; the snippet to
   paste is reviews/21_zoning/sources_json_snippet.json). Absent key or a
   malformed entry: exit 1, naming the entry index only.
2. Finds each Fulton parcel's jurisdiction from its TaxDist on the Fulton
   parcel layer registered in sources.json (outFields ParcelID,TaxDist only:
   no owner fields are ever requested, SR-8).
3. For every role="zoning" source, in snippet order (the precedence order),
   fetches the source's features around the parcels of the jurisdictions it
   covers (objectIds by envelope, then features by objectIds, every page
   checked; a failed, truncated or short page fails the whole source and
   nothing of it is used), and assigns a district:
     - kind "join": by normalized parcel id (id_trim_last for East Point);
       a PIN shared by two parcels is a collision and assigns neither;
       below a 50% match rate the source is refused for this import.
     - kind "overlay": in PostGIS (TEMP table, ST_MakeValid,
       ST_Area(ST_Intersection(...)::geography)), the district with the
       largest overlap wins, ties broken by district code; under 60% of
       the parcel is counted 'split' (the majority district is still
       written and marked split); under 20% is a sliver and assigns
       nothing.
   role="flu" (future land use) sources are never written to
   parcels.zoning.
4. Writes ONLY ``UPDATE parcels SET zoning, zoning_description WHERE
   parcel_id`` in ONE transaction committed at the end (SR-5). No INSERT,
   no DELETE, no other column; last_updated is deliberately NOT touched (it
   records discovery refreshes). A parcel without a match keeps its value
   (unmatched_kept). A parcel in a city with no machine-readable zoning
   keeps zoning NULL and gets a 'no machine-readable zoning' description
   (only when both columns are empty). Re-running with the same inputs
   changes 0 rows (SR-9).
5. Prints counts only (as-of date, per jurisdiction, per source, per
   entitlement class against zoning_rules.json) -- never a parcel id, an
   owner, or a feature attribute.

Exit codes: 0 imported; 1 refused (sources.json key missing/malformed,
Fulton parcel layer down); 2 no database / database error; 3 a run has its
baseline; 4 imported, but at least one zoning source with candidate
parcels FAILED (named on the last line; its parcels kept their previous
values), so a wrapper that checks the exit code cannot mistake a partial
import for a full one.

HTTP (SR-11; reviews/32_zoning_import_fetcher, D1-11): ``ZoningSession`` is
rules_scrape.PoliteFetcher, the fetcher of rules_scrape, adjacency_import and
hazards_import, with this tool's User-Agent and robots token: robots.txt of
every origin read once and honoured for this token and every AI-agent token
through the ONE shared verdict rules_scrape.robots_verdict (the D1 ArcGIS
exception included; its line is printed once per host read under it), 1
request/second per host (Crawl-delay when larger; the ArcGIS Online
services hosts share one key), 2 retries with back-off on 5xx / connection
errors, 429 retried only with a Retry-After of at most 30 s, redirects
followed by hand only to https on the same registrable domain (5 hops at
most), the denylist and terms block applied to the prepared URL, challenge
pages and answers over 25 MB refused, no cookies, no cache. Each of these
fails the source (or refuses the parcel layer) and never writes a guess:
parcels keep their values.

Depends only on prepare, zoning and rules_scrape (its fetcher and robots
verdict) -- never on research.py (the agent's sandbox), runner.py (which
imports it), or screener/ (a separate tool).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode, urlparse

import requests
from requests.structures import CaseInsensitiveDict

import prepare
import rules_scrape
import zoning

_REPO_ROOT = Path(__file__).resolve().parent
SOURCES_PATH = _REPO_ROOT / "sources.json"
EXPERIMENT_LOG = _REPO_ROOT / "experiment_log.tsv"
SOURCES_KEY_PATH = "county_parcel_data.fulton_ga.zoning_sources"

USER_AGENT = "Land-Research-ZoningImport/1.0 (between-runs operator tool; 1 req/s per host)"
UA_TOKEN = "Land-Research-ZoningImport"     # this tool's robots.txt token
MAX_REQUESTS_PER_SOURCE = 400     # budget guard: a runaway source fails, it never hammers
ID_CHUNK = 60                     # ParcelIDs per IN-list query (URL stays under ~2 kB)
OID_CHUNK = 150                   # objectIds per feature page (URL stays under ~2 kB)
TILE_DEG = 0.05                   # envelope grid for objectId queries
SPLIT_SHARE = 0.60                # majority district under this share -> 'split'
MIN_COVER_SHARE = 0.20            # under this, the overlap is a sliver: no district
JOIN_MATCH_FLOOR = 0.50           # join sources below this match rate are refused
_RUN_MARKER_RE = re.compile(r"(?:^|\s)run=([a-z0-9._-]+)")
_SAFE_ID_RE = re.compile(r"^[0-9A-Z .-]+$")
_SOURCE_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# First two characters of Fulton TaxDist -> stable jurisdiction key (the
# Fulton Tyler_Cities codes; copied, not imported, from the screener).
TAXDIST_TO_JURISDICTION: Mapping[str, str] = {
    "05": "atlanta", "10": "alpharetta", "15": "college_park", "20": "east_point",
    "25": "fairburn", "30": "hapeville", "35": "mountain_park", "40": "palmetto",
    "45": "roswell", "50": "union_city", "55": "south_fulton", "56": "milton",
    "57": "johns_creek", "59": "sandy_springs", "65": "chattahoochee_hills",
    "70": "fid",
}

# --- SQL (SR-2: module-level constants, %s placeholders only) --------------
_SQL_LIST_PARCELS = (
    "SELECT parcel_id, zoning, zoning_description FROM parcels "
    "WHERE county = 'fulton' ORDER BY parcel_id"
)
_SQL_COUNT_PARCELS = "SELECT COUNT(*) FROM parcels"
_SQL_PARCEL_BBOXES = (
    "SELECT parcel_id, ST_XMin(geometry), ST_YMin(geometry), "
    "ST_XMax(geometry), ST_YMax(geometry) FROM parcels "
    "WHERE parcel_id = ANY(%s) AND geometry IS NOT NULL"
)
_SQL_CREATE_TEMP = (
    "CREATE TEMP TABLE IF NOT EXISTS zoning_import_polys ("
    "src TEXT NOT NULL, district TEXT NOT NULL, descr TEXT, geom geometry"
    ") ON COMMIT DROP"
)
_SQL_INSERT_POLY = (
    "INSERT INTO zoning_import_polys (src, district, descr, geom) VALUES "
    "(%s, %s, %s, ST_CollectionExtract(ST_MakeValid("
    "ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3))"
)
# Per parcel and district: the area of the UNION of that district's pieces
# inside the parcel (so overlapping polygons of one district are counted
# once: review finding 4) and the parcel's own area, both on the spheroid
# (geography, square metres).
_SQL_OVERLAY = (
    "WITH pg AS ("
    "  SELECT parcel_id, ST_CollectionExtract(ST_MakeValid(geometry), 3) AS g "
    "  FROM parcels WHERE parcel_id = ANY(%s) AND geometry IS NOT NULL"
    ") "
    "SELECT pg.parcel_id, z.district, MIN(z.descr), "
    "ST_Area(ST_Union(ST_CollectionExtract("
    "ST_Intersection(pg.g, z.geom), 3))::geography), "
    "MAX(ST_Area(pg.g::geography)) "
    "FROM pg JOIN zoning_import_polys z "
    "ON z.src = %s AND z.geom && pg.g AND ST_Intersects(z.geom, pg.g) "
    "GROUP BY pg.parcel_id, z.district"
)
_SQL_UPDATE_ZONING = (
    "UPDATE parcels SET zoning = %s, zoning_description = %s WHERE parcel_id = %s"
)
# Run-state guard (Z-05): read-only, the mirror is append-only (SR-13).
_SQL_MIRROR_EXISTS = "SELECT to_regclass('experiment_log_mirror') IS NOT NULL"
_SQL_MIRROR_BASELINE_FOR_TAG = (
    "SELECT COUNT(*) FROM experiment_log_mirror "
    "WHERE run_tag = %s AND status = 'baseline'"
)
_SQL_MIRROR_LATEST_RUN = (
    "SELECT m.run_tag, m.status, EXISTS (SELECT 1 FROM experiment_log_mirror b "
    "WHERE b.run_tag = m.run_tag AND b.status = 'baseline') "
    "FROM experiment_log_mirror m ORDER BY m.entry_id DESC LIMIT 1"
)


class Refused(Exception):
    """A configuration or state this tool refuses (operator-facing, exit 1)."""


class SourceError(Exception):
    """One source failed. The message names a host, a status and counts only."""


# ---------------------------------------------------------------------------
# Run-state guard (Z-05)
# ---------------------------------------------------------------------------
def tsv_has_baseline(tag: str | None, log_path: Path = EXPERIMENT_LOG) -> bool:
    """Does the TSV hold a baseline row for ``tag``? (costar_import's rule)."""
    if not tag or not log_path.exists():
        return False
    with log_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            m = _RUN_MARKER_RE.search(row.get("description") or "")
            if row.get("status") == "baseline" and m and m.group(1) == tag:
                return True
    return False


def mirror_blocks(conn: Any, tag: str | None) -> str | None:
    """Why the database's run history forbids an import now, or None.

    On a run branch: refuse when the mirror has that run's baseline. Off a
    run branch (e.g. main, while the loop may run from another checkout):
    refuse when the newest mirror row belongs to a baselined run and is not
    a 'halt' row (stop the loop with make halt first, or pass
    --no-open-run-check when that run is truly over).
    """
    with conn.cursor() as cur:
        cur.execute(_SQL_MIRROR_EXISTS)
        row = cur.fetchone()
    if not row or not row[0]:
        return None
    if tag:
        with conn.cursor() as cur:
            cur.execute(_SQL_MIRROR_BASELINE_FOR_TAG, (tag,))
            row = cur.fetchone()
        if row and int(row[0] or 0) > 0:
            return "the database's experiment history has this run's baseline"
        return None
    with conn.cursor() as cur:
        cur.execute(_SQL_MIRROR_LATEST_RUN)
        row = cur.fetchone()
    if row and row[2] and row[1] != "halt":
        return "the database's newest experiment row is from a run that was not halted"
    return None


# ---------------------------------------------------------------------------
# sources.json (read-only) and its zoning_sources entries (Z-17)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ZoningSource:
    key: str
    name: str
    role: str
    kind: str
    jurisdictions: tuple[str, ...]
    url: str
    id_field: str | None
    zoning_field: str | None
    desc_field: str | None
    id_trim_last: bool


def validate_zoning_sources(entries: Any) -> list[ZoningSource]:
    """Parse the zoning_sources list; Refused names the bad index only."""
    if not isinstance(entries, list) or not entries:
        raise Refused(f"{SOURCES_KEY_PATH} must be a non-empty list")
    out: list[ZoningSource] = []
    seen: set[str] = set()
    for i, e in enumerate(entries):
        bad = Refused(f"{SOURCES_KEY_PATH}[{i}] is malformed; see "
                      "reviews/21_zoning/sources_json_snippet.json for the shape")
        if not isinstance(e, dict):
            raise bad
        key, role, kind = e.get("key"), e.get("role"), e.get("kind")
        url, jurs = e.get("url"), e.get("jurisdictions")
        if not (isinstance(key, str) and _SOURCE_KEY_RE.match(key)) or key in seen:
            raise bad
        if role not in ("zoning", "flu") or kind not in ("join", "overlay"):
            raise bad
        if not isinstance(e.get("name"), str) or not e["name"].strip():
            raise bad
        if not (isinstance(url, str) and url.startswith("https://") and urlparse(url).hostname):
            raise bad
        if not (isinstance(jurs, list) and jurs
                and all(j in zoning.KNOWN_JURISDICTIONS for j in jurs)):
            raise bad
        fields = {f: e.get(f) for f in ("id_field", "zoning_field", "desc_field", "flu_field")}
        if any(v is not None and not (isinstance(v, str) and _FIELD_RE.match(v))
               for v in fields.values()):
            raise bad
        if role == "zoning" and not fields["zoning_field"]:
            raise bad
        if role == "flu" and not fields["flu_field"]:
            raise bad
        if kind == "join" and not fields["id_field"]:
            raise bad
        if not isinstance(e.get("id_trim_last", False), bool):
            raise bad
        seen.add(key)
        out.append(ZoningSource(
            key=key, name=e["name"].strip(), role=role, kind=kind,
            jurisdictions=tuple(jurs), url=url.rstrip("/"),
            id_field=fields["id_field"], zoning_field=fields["zoning_field"],
            desc_field=fields["desc_field"], id_trim_last=bool(e.get("id_trim_last", False)),
        ))
    return out


def load_config(path: Path = SOURCES_PATH) -> tuple[str, list[ZoningSource]]:
    """(Fulton parcel layer URL, zoning sources). sources.json is only read."""
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
        fulton = reg["county_parcel_data"]["fulton_ga"]
        layer_url = f"{fulton['service_url'].rstrip('/')}/{int(fulton['parcel_layer_id'])}"
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("sources.json has no readable county_parcel_data.fulton_ga parcel layer")
    if "zoning_sources" not in fulton:
        raise Refused(
            f"sources.json has no {SOURCES_KEY_PATH}. sources.json is human-only: paste "
            "the 'zoning_sources' list from reviews/21_zoning/sources_json_snippet.json "
            "inside county_parcel_data.fulton_ga (README.md, 'Zoning import')."
        )
    return layer_url, validate_zoning_sources(fulton["zoning_sources"])


# ---------------------------------------------------------------------------
# HTTP (SR-11)
# ---------------------------------------------------------------------------
class ZoningSession(rules_scrape.PoliteFetcher):
    """rules_scrape.PoliteFetcher with this tool's User-Agent and robots
    token (D1-11). ``robots_for``, ``_check`` and ``fetch`` are inherited
    unchanged: the robots verdict is the shared rules_scrape.robots_verdict
    and this module defines none of its own. ``get_json`` maps every refusal
    to SourceError, so a failure fails one source and never escapes run()."""

    robots_token = UA_TOKEN
    user_agent = USER_AGENT

    def __init__(self, http: Any = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, **kw: Any) -> None:
        # run() raises the global budget to request_budget() (ZI-02).
        super().__init__(http, clock=clock, sleep=sleep, **kw)
        if hasattr(self.http, "headers"):
            self.http.headers["Accept"] = "application/json"
        self.last_headers: Mapping[str, Any] = {}
        self._robots_seen = 0
        self._scanned = 0

    def _get(self, url: str) -> tuple[Any, bytes]:
        # Recorded only, so get_json decodes the body exactly as
        # requests.Response.json() did before (charset from the headers).
        resp, body = super()._get(url)
        self.last_headers = getattr(resp, "headers", None) or {}
        return resp, body

    def total_requests(self) -> int:
        """Data requests (robots.txt GETs excluded, ZI-08), retries and
        redirect hops included: what MAX_REQUESTS_PER_SOURCE counts. A
        request is excluded by its URL ending in ``/robots.txt``, so a
        robots.txt redirect hop that lands on another ``.../robots.txt`` is
        excluded too (at most MAX_REDIRECTS per host); the global
        PoliteFetcher budget still counts every request."""
        for u in self.calls[self._scanned:]:
            if u.endswith("/robots.txt"):
                self._robots_seen += 1
        self._scanned = len(self.calls)
        return len(self.calls) - self._robots_seen

    def get_json(self, url: str, params: Mapping[str, Any]) -> dict[str, Any]:
        full = url + ("?" + urlencode(dict(params)) if params else "")
        host = urlparse(url).hostname or "?"
        try:
            got = self.fetch(full)
        except rules_scrape.Unreachable as exc:
            raise SourceError(f"{host}: {exc.reason}") from None
        except rules_scrape.BudgetExceeded as exc:
            raise SourceError(f"{host}: {exc}") from None
        except requests.RequestException:     # e.g. a body cut off mid-read (ZI-09)
            raise SourceError(f"{host}: connection error") from None
        try:
            body = _json_like_requests(got.body, self.last_headers)
        except ValueError:
            raise SourceError(f"{host}: answer is not JSON") from None
        if not isinstance(body, dict):
            raise SourceError(f"{host}: answer is not a JSON object")
        if "error" in body:
            code = (body.get("error") or {}).get("code") if isinstance(
                body.get("error"), dict) else None
            raise SourceError(f"{host}: service error {code if isinstance(code, int) else '?'}")
        return body


def _json_like_requests(body: bytes, headers: Mapping[str, Any]) -> Any:
    """``body`` parsed by requests.Response.json() itself, with the encoding
    requests derives from ``headers`` (as the session's answers were parsed
    before D1-11). ValueError when it is not JSON."""
    r = requests.models.Response()
    r._content = body
    r.headers = CaseInsensitiveDict(dict(headers or {}))
    r.encoding = requests.utils.get_encoding_from_headers(r.headers)
    return r.json()


def request_budget(n_sources: int, n_parcels: int) -> int:
    """The global PoliteFetcher budget for one run (ZI-02): a backstop that
    can never bind before MAX_REQUESTS_PER_SOURCE. A source makes at most
    MAX_REQUESTS_PER_SOURCE + 1 get_json calls (each but a final failing one
    makes at least one data request), the parcel layer ceil(parcels /
    ID_CHUNK); one call makes at most (MAX_REDIRECTS + 1) hops of
    (MAX_RETRIES + 1) attempts, plus that many robots.txt reads per hop."""
    hop = (rules_scrape.MAX_REDIRECTS + 1) * (rules_scrape.MAX_RETRIES + 1)
    per_call = hop + (rules_scrape.MAX_REDIRECTS + 1) * hop
    calls = n_sources * (MAX_REQUESTS_PER_SOURCE + 1) + math.ceil(max(0, n_parcels) / ID_CHUNK)
    return max(1, calls) * per_call


# ---------------------------------------------------------------------------
# Ids, jurisdictions
# ---------------------------------------------------------------------------
def normalize_id(raw: Any, trim_last: bool = False) -> str | None:
    """Comparable parcel id: no 'fulton-' prefix, trimmed, single spaces, upper."""
    if raw is None:
        return None
    s = str(raw)
    if s.startswith("fulton-"):
        s = s[len("fulton-"):]
    s = re.sub(r"\s+", " ", s.strip()).upper()
    if trim_last:
        s = s[:-1].rstrip()
    return s or None


def jurisdiction_for(tax_dist: Any) -> str | None:
    code = str(tax_dist or "").strip()
    return TAXDIST_TO_JURISDICTION.get(code[:2])


def in_list_where(field_name: str, ids: Sequence[str]) -> tuple[str, int]:
    """'<field> IN (...)' for ids that pass the strict id regex (Z-25).

    Returns (where, skipped). Quotes cannot pass the regex; they are escaped
    anyway, belt and braces.
    """
    if not _FIELD_RE.match(field_name):
        raise ValueError("bad field name")
    ok = [i for i in ids if isinstance(i, str) and _SAFE_ID_RE.match(i)]
    skipped = len(ids) - len(ok)
    if not ok:
        return "", skipped
    quoted = ",".join("'" + i.replace("'", "''") + "'" for i in ok)
    return f"{field_name} IN ({quoted})", skipped


def fetch_jurisdictions(session: ZoningSession, layer_url: str,
                        parcel_ids: Sequence[str]) -> tuple[dict[str, str | None], dict[str, int]]:
    """parcel_id -> jurisdiction key (None = unknown TaxDist) from layer 11."""
    raw = {pid: normalize_id(pid) for pid in parcel_ids}
    # Query with the EXACT stored id (internal runs of spaces matter to the
    # server: '13 0129  LL0273'); compare answers in normalized form.
    keys = sorted({(pid[len("fulton-"):] if pid.startswith("fulton-") else pid).strip()
                   for pid in parcel_ids})
    found: dict[str, set[str]] = {}
    counts = {"unsafe_ids": 0, "taxdist_conflicts": 0}
    for i in range(0, len(keys), ID_CHUNK):
        where, skipped = in_list_where("ParcelID", keys[i:i + ID_CHUNK])
        counts["unsafe_ids"] += skipped
        if not where:
            continue
        body = session.get_json(f"{layer_url}/query", {
            "where": where, "outFields": "ParcelID,TaxDist", "returnGeometry": "false",
            "f": "json",
        })
        if body.get("exceededTransferLimit"):
            raise SourceError(f"{urlparse(layer_url).hostname}: parcel answer truncated")
        for f in body.get("features") or []:
            a = f.get("attributes") or {}
            k = normalize_id(a.get("ParcelID"))
            if k:
                found.setdefault(k, set()).add(str(a.get("TaxDist") or "").strip())
    out: dict[str, str | None] = {}
    for pid, r in raw.items():
        tds = sorted(found.get(r or "", set()))
        if len(tds) > 1:
            counts["taxdist_conflicts"] += 1
        out[pid] = jurisdiction_for(tds[0]) if tds else None
    counts["not_on_parcel_layer"] = sum(1 for r in raw.values() if not found.get(r or ""))
    return out, counts


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _signed_area(ring: Sequence[Sequence[float]]) -> float:
    return sum(x0 * y1 - x1 * y0 for (x0, y0, *_), (x1, y1, *_) in zip(ring, ring[1:])) / 2.0


def _in_ring(x: float, y: float, ring: Sequence[Sequence[float]]) -> bool:
    inside = False
    for (x0, y0, *_), (x1, y1, *_) in zip(ring, ring[1:]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / ((y1 - y0) or 1e-30) + x0:
            inside = not inside
    return inside


def esri_rings_to_geojson(rings: Any) -> str | None:
    """Esri polygon rings (outer clockwise, holes counter-clockwise) -> GeoJSON."""
    if not isinstance(rings, list):
        return None
    clean = []
    for r in rings:
        if isinstance(r, list) and len(r) >= 4:
            pts = [[float(p[0]), float(p[1])] for p in r if isinstance(p, list) and len(p) >= 2]
            if len(pts) >= 4:
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                clean.append(pts)
    if not clean:
        return None
    outers = [r for r in clean if _signed_area(r) < 0]
    holes = [r for r in clean if _signed_area(r) >= 0]
    if not outers:                       # orientation not Esri's: all are shells
        outers, holes = clean, []
    polys = [[o] for o in outers]
    for h in holes:
        for p in polys:
            if _in_ring(h[0][0], h[0][1], p[0]):
                p.append(h)
                break
    return json.dumps({"type": "MultiPolygon", "coordinates": polys})


def tiles_for(bboxes: Iterable[Sequence[float]], step: float = TILE_DEG) -> list[tuple[float, float, float, float]]:
    """Grid cells (step degrees) covering every bbox, deterministic order."""
    cells: set[tuple[int, int]] = set()
    for xmin, ymin, xmax, ymax in bboxes:
        for ix in range(int(xmin // step), int(xmax // step) + 1):
            for iy in range(int(ymin // step), int(ymax // step) + 1):
                cells.add((ix, iy))
    return [(ix * step, iy * step, (ix + 1) * step, (iy + 1) * step) for ix, iy in sorted(cells)]


# ---------------------------------------------------------------------------
# Fetching one source (Z-04, Z-10, Z-12)
# ---------------------------------------------------------------------------
@dataclass
class SourceResult:
    key: str
    candidates: int = 0
    matched: int = 0
    unmatched: int = 0
    collisions: int = 0
    conflicting_duplicates: int = 0
    split: int = 0
    slivers: int = 0
    failed: str | None = None
    assigned: dict[str, tuple[str, str | None, bool]] = field(default_factory=dict)


def _budget(session: ZoningSession, start: int) -> None:
    if session.total_requests() - start > MAX_REQUESTS_PER_SOURCE:
        raise SourceError(f"request budget of {MAX_REQUESTS_PER_SOURCE} exceeded")


def preflight(session: ZoningSession, src: ZoningSource) -> int:
    """Layer check before any query (the harness gate for these layers).
    Returns the page size to use."""
    meta = session.get_json(src.url, {"f": "pjson"})
    names = {f.get("name") for f in meta.get("fields") or [] if isinstance(f, dict)}
    needed = [f for f in (src.id_field, src.zoning_field, src.desc_field) if f]
    if not all(f in names for f in needed):
        raise SourceError(f"layer is missing {sum(f not in names for f in needed)} expected field(s)")
    if src.kind == "overlay" and meta.get("geometryType") != "esriGeometryPolygon":
        raise SourceError("layer is not a polygon layer")
    mrc = meta.get("maxRecordCount")
    return max(1, min(OID_CHUNK, int(mrc))) if isinstance(mrc, int) and mrc > 0 else OID_CHUNK


def fetch_features(session: ZoningSession, src: ZoningSource,
                   bboxes: Sequence[Sequence[float]]) -> list[dict[str, Any]]:
    """Every feature of ``src`` touching the parcels' grid cells, or SourceError."""
    start = session.total_requests()
    page = preflight(session, src)
    oids: set[int] = set()
    for env in tiles_for(bboxes):
        _budget(session, start)
        body = session.get_json(f"{src.url}/query", {
            "where": "1=1", "geometry": ",".join(f"{v:.6f}" for v in env),
            "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "returnIdsOnly": "true", "f": "json",
        })
        if body.get("exceededTransferLimit"):
            raise SourceError("object id list truncated")
        ids = body.get("objectIds")
        if ids is None:
            ids = []
        if not isinstance(ids, list):
            raise SourceError("object id answer malformed")
        oids.update(int(i) for i in ids)
    fields = ",".join(f for f in (src.id_field, src.zoning_field, src.desc_field) if f)
    ordered = sorted(oids)
    feats: list[dict[str, Any]] = []
    for i in range(0, len(ordered), page):
        _budget(session, start)
        chunk = ordered[i:i + page]
        params = {"objectIds": ",".join(str(o) for o in chunk), "outFields": fields,
                  "returnGeometry": "true" if src.kind == "overlay" else "false",
                  "f": "json"}
        if src.kind == "overlay":
            params["outSR"] = 4326
        body = session.get_json(f"{src.url}/query", params)
        got = body.get("features")
        if body.get("exceededTransferLimit") or not isinstance(got, list) or len(got) != len(chunk):
            raise SourceError("a feature page came back truncated or short")
        feats.extend(got)
    return feats


def _val(attrs: Mapping[str, Any], name: str | None) -> str | None:
    if not name:
        return None
    v = attrs.get(name)
    s = str(v).strip() if v is not None else ""
    return s or None


def match_join(src: ZoningSource, feats: Sequence[Mapping[str, Any]],
               group: Sequence[str]) -> SourceResult:
    res = SourceResult(src.key, candidates=len(group))
    table: dict[str, set[tuple[str, str | None]]] = {}
    for f in feats:
        a = f.get("attributes") or {}
        k = normalize_id(_val(a, src.id_field))
        z = _val(a, src.zoning_field)
        if k and z:
            table.setdefault(k, set()).add((z, _val(a, src.desc_field)))
    keys = {pid: normalize_id(pid, src.id_trim_last) for pid in group}
    owners: dict[str, int] = {}
    for k in keys.values():
        if k:
            owners[k] = owners.get(k, 0) + 1
    for pid in sorted(group):
        k = keys[pid]
        rows = table.get(k or "")
        if not rows:
            res.unmatched += 1
            continue
        if owners.get(k or "", 0) > 1:
            res.collisions += 1
            res.unmatched += 1
            continue
        if len({z for z, _ in rows}) > 1:
            res.conflicting_duplicates += 1
        z, d = sorted(rows, key=lambda t: (t[0], t[1] or ""))[0]
        res.assigned[pid] = (z, d, False)
        res.matched += 1
    if group and res.matched < JOIN_MATCH_FLOOR * len(group):
        res.failed = (f"only {res.matched} of {len(group)} parcels matched by id "
                      f"(floor {int(JOIN_MATCH_FLOOR * 100)}%); nothing used")
        res.assigned = {}
    return res


def rank_overlay(rows: Iterable[Sequence[Any]]) -> dict[str, tuple[str, str | None, float]]:
    """parcel_id -> (district, desc, share) from _SQL_OVERLAY rows.

    Largest overlap wins; ties go to the alphabetically first district
    (deterministic). The share is clamped to 1.0 as a backstop: the SQL
    already unions each district's pieces, so only rounding can push it
    over."""
    per: dict[str, list[tuple[float, str, str | None, float]]] = {}
    for pid, district, desc, area, parea in rows:
        per.setdefault(pid, []).append((float(area or 0.0), str(district),
                                        desc, float(parea or 0.0)))
    out = {}
    for pid, items in per.items():
        items.sort(key=lambda t: (-t[0], t[1]))
        area, district, desc, parea = items[0]
        out[pid] = (district, desc, min(1.0, area / parea) if parea > 0 else 0.0)
    return out


def match_overlay(conn: Any, src: ZoningSource, feats: Sequence[Mapping[str, Any]],
                  group: Sequence[str]) -> SourceResult:
    res = SourceResult(src.key, candidates=len(group))
    with conn.transaction():           # SAVEPOINT: a failed overlay drops only this source
        with conn.cursor() as cur:
            cur.execute(_SQL_CREATE_TEMP)
        with conn.cursor() as cur:
            for f in feats:
                a = f.get("attributes") or {}
                z = _val(a, src.zoning_field)
                gj = esri_rings_to_geojson((f.get("geometry") or {}).get("rings"))
                if z and gj:
                    cur.execute(_SQL_INSERT_POLY, (src.key, z, _val(a, src.desc_field), gj))
        with conn.cursor() as cur:
            cur.execute(_SQL_OVERLAY, (list(group), src.key))
            rows = cur.fetchall()
    ranked = rank_overlay(rows)
    for pid in sorted(group):
        r = ranked.get(pid)
        if r is None:
            res.unmatched += 1
            continue
        district, desc, share = r
        if share < MIN_COVER_SHARE:
            res.slivers += 1
            res.unmatched += 1
            continue
        split = share < SPLIT_SHARE
        res.split += int(split)
        res.assigned[pid] = (district, desc, split)
        res.matched += 1
    return res


# ---------------------------------------------------------------------------
# Plan + write
# ---------------------------------------------------------------------------
@dataclass
class Plan:
    updates: list[tuple[str, str | None, str]] = field(default_factory=list)  # (zoning, desc, pid)
    counts: dict[str, int] = field(default_factory=dict)
    per_jurisdiction: dict[str, dict[str, int]] = field(default_factory=dict)
    per_class: dict[str, int] = field(default_factory=dict)


def build_plan(stored: Mapping[str, tuple[Any, Any]], juris: Mapping[str, str | None],
               sources: Sequence[ZoningSource], results: Mapping[str, SourceResult]) -> Plan:
    """Decide every parcel's write from the source results (pure)."""
    plan = Plan()
    c = {k: 0 for k in ("changed", "unchanged", "unmatched_kept", "no_source",
                        "unknown_jurisdiction", "drift", "precedence_conflicts")}
    covered = {j for s in sources if s.role == "zoning" for j in s.jurisdictions}
    for pid in sorted(stored):
        j = juris.get(pid)
        pj = plan.per_jurisdiction.setdefault(j or "unknown", {
            "parcels": 0, "matched": 0, "unmatched": 0, "no_source": 0})
        pj["parcels"] += 1
        old_z, old_d = stored[pid]
        new: tuple[str, str] | None = None
        if j is None:
            c["unknown_jurisdiction"] += 1
        elif j not in covered:
            pj["no_source"] += 1
            c["no_source"] += 1
            if old_z is None and old_d is None:
                new = (None, zoning.no_source_description(j))  # type: ignore[assignment]
        else:
            winner = None
            for s in sources:
                r = results.get(s.key)
                if s.role != "zoning" or j not in s.jurisdictions or r is None or r.failed:
                    continue
                a = r.assigned.get(pid)
                if a is None:
                    continue
                if winner is None:
                    winner = (s, a)
                elif a[0] != winner[1][0]:
                    c["precedence_conflicts"] += 1
            if winner is None:
                pj["unmatched"] += 1
                c["unmatched_kept"] += 1
            else:
                s, (code, desc, split) = winner
                new = zoning.encode_stored(j, s.key, code, split, desc)
                pj["matched"] += 1
                prev = zoning.parse_stored(old_z, old_d)
                if old_z is not None and (prev is None or prev.district != new[0]):
                    c["drift"] += 1
        final = new if new is not None else (old_z, old_d)
        cls = zoning.classify_stored(final[0], final[1], pid).cls
        plan.per_class[cls] = plan.per_class.get(cls, 0) + 1
        if new is None:
            continue
        if (old_z, old_d) == new:
            c["unchanged"] += 1
        else:
            c["changed"] += 1
            plan.updates.append((new[0], new[1], pid))
    plan.counts = c
    return plan


def write_plan(conn: Any, plan: Plan) -> int:
    with conn.cursor() as cur:
        for z, d, pid in plan.updates:
            cur.execute(_SQL_UPDATE_ZONING, (z, d, pid))
    return len(plan.updates)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _connection_unusable(conn: Any) -> bool:
    if getattr(conn, "broken", False) is True or getattr(conn, "closed", False) is True:
        return True
    status = getattr(getattr(conn, "info", None), "transaction_status", None)
    return getattr(status, "name", None) in ("INERROR", "UNKNOWN")


def _read_parcels(conn: Any) -> tuple[dict[str, tuple[Any, Any]], dict[str, tuple[float, ...]]]:
    with conn.cursor() as cur:
        cur.execute(_SQL_LIST_PARCELS)
        stored = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    bbox: dict[str, tuple[float, ...]] = {}
    if stored:
        with conn.cursor() as cur:
            cur.execute(_SQL_PARCEL_BBOXES, (list(stored),))
            bbox = {r[0]: tuple(float(v) for v in r[1:5]) for r in cur.fetchall()
                    if all(v is not None for v in r[1:5])}
    return stored, bbox


def _count_parcels(conn: Any) -> int:
    with conn.cursor() as cur:
        cur.execute(_SQL_COUNT_PARCELS)
        return int((cur.fetchone() or (0,))[0])


def _print_source(res: "SourceResult") -> None:
    if res.failed:
        print(f"source {res.key}: FAILED, nothing used: {res.failed}")
    else:
        print(f"source {res.key}: {res.candidates} candidates, {res.matched} matched, "
              f"{res.unmatched} unmatched, {res.collisions} id collisions, "
              f"{res.conflicting_duplicates} conflicting duplicates, {res.split} split, "
              f"{res.slivers} slivers")


def run(*, sources_path: Path = SOURCES_PATH, session: ZoningSession | None = None,
        today: date | None = None, check_open_run: bool = True,
        log_path: Path = EXPERIMENT_LOG) -> int:
    """0 = imported, 1 = refused (config / parcel layer), 2 = database
    unavailable or error, 3 = refused: a run has its baseline, 4 = imported
    but at least one zoning source with candidate parcels FAILED (its
    parcels kept their previous values; review finding 5)."""
    today = today or date.today()
    tag = prepare.current_run_tag()
    if tsv_has_baseline(tag, log_path):
        print("REFUSED: this run already has its baseline. Importing zoning now would change "
              "what later experiments score. Start a new run first, then import before its "
              "baseline.")
        return 3
    try:
        layer_url, sources = load_config(sources_path)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 1
    if not prepare.dsn_available():
        print("zoning not imported: no DATABASE_URL.")
        return 2
    session = session or ZoningSession()
    print(f"zoning_import: as of {today.isoformat()}; "
          f"{sum(s.role == 'zoning' for s in sources)} zoning sources, "
          f"{sum(s.role == 'flu' for s in sources)} future-land-use sources (never written)")
    # Pass 1 (read-only): run-state guard, the parcels and their extents.
    try:
        with prepare.get_connection() as conn:
            why = mirror_blocks(conn, tag) if (check_open_run or tag) else None
            stored, bbox = ({}, {}) if why else _read_parcels(conn)
            conn.rollback()
    except Exception as exc:
        print(f"zoning not imported: database error ({type(exc).__name__}); nothing written.")
        return 2
    if why:
        print(f"REFUSED: {why}. Importing zoning now would change what later "
              "experiments score. Stop the run (make halt) or start a new run first.")
        return 3
    if not stored:
        print("zoning_import: no Fulton parcels in the database; nothing to do.")
        return 0
    # Network (no database transaction held open meanwhile). The global
    # budget is a backstop that never binds before the per-source one (ZI-02).
    session.max_requests = max(session.max_requests, request_budget(
        sum(s.role == "zoning" for s in sources), len(stored)))
    try:
        juris, jcounts = fetch_jurisdictions(session, layer_url, list(stored))
    except SourceError as exc:
        print(f"REFUSED: Fulton parcel layer unavailable ({exc}); nothing written.")
        return 1
    print(f"parcels: {len(stored)} Fulton parcels; {jcounts['not_on_parcel_layer']} not "
          f"found on the parcel layer; {jcounts['unsafe_ids']} ids skipped as unsafe; "
          f"{jcounts['taxdist_conflicts']} with conflicting TaxDist")
    fetched: dict[str, tuple[list[str], list[dict[str, Any]] | None, str | None]] = {}
    for s in sources:
        if s.role != "zoning":
            continue
        group = sorted(p for p, j in juris.items() if j in s.jurisdictions)
        if not group:
            print(f"source {s.key}: 0 candidate parcels; not queried")
            continue
        try:
            feats = fetch_features(session, s, [bbox[p] for p in group if p in bbox])
            fetched[s.key] = (group, feats, None)
        except SourceError as exc:
            fetched[s.key] = (group, None, str(exc))
        except (ValueError, TypeError, KeyError):
            fetched[s.key] = (group, None, "malformed answer")
    # Pass 2: overlays, plan and the writes in ONE transaction (SR-5).
    try:
        with prepare.get_connection() as conn:
            n_before = _count_parcels(conn)
            stored, _ = _read_parcels(conn)
            results: dict[str, SourceResult] = {}
            for s in sources:
                if s.key not in fetched:
                    continue
                group, feats, err = fetched[s.key]
                group = [p for p in group if p in stored]
                if err is not None or feats is None:
                    res = SourceResult(s.key, candidates=len(group), failed=err)
                elif s.kind == "join":
                    res = match_join(s, feats, group)
                else:
                    try:
                        res = match_overlay(conn, s, feats, group)
                    except Exception as exc:
                        if _connection_unusable(conn):
                            raise
                        res = SourceResult(s.key, candidates=len(group),
                                           failed=f"overlay failed ({type(exc).__name__})")
                results[s.key] = res
                _print_source(res)
            plan = build_plan(stored, juris, sources, results)
            n = write_plan(conn, plan)
            if _count_parcels(conn) != n_before:
                conn.rollback()
                print("REFUSED: the parcel count changed during the import; nothing written.")
                return 2
            conn.commit()
    except Exception as exc:  # psycopg errors and the like: the class name only
        print(f"zoning not imported: database error ({type(exc).__name__}); nothing written.")
        return 2
    for j in sorted(plan.per_jurisdiction):
        v = plan.per_jurisdiction[j]
        print(f"jurisdiction {j}: {v['parcels']} parcels, {v['matched']} matched, "
              f"{v['unmatched']} unmatched, {v['no_source']} no_source")
    print("classes (against zoning_rules.json): " + ", ".join(
        f"{k} {plan.per_class.get(k, 0)}" for k in zoning.CLASSES))
    c = plan.counts
    print(f"written: {n} changed, {c['unchanged']} unchanged, {c['unmatched_kept']} "
          f"unmatched_kept, {c['no_source']} no_source, {c['unknown_jurisdiction']} unknown "
          f"jurisdiction, {c['drift']} drift, {c['precedence_conflicts']} precedence conflicts")
    for line in rules_scrape.robots_exception_lines(session):    # D1-07: once per host
        print(line)
    print("requests: " + ", ".join(f"{h} {k}" for h, k in sorted(session.requests.items())))
    print("Re-run make zoning-import at every new run; zoning_rules.json edits are between "
          "runs only.")
    failed = sorted(k for k, r in results.items() if r.failed and r.candidates)
    if failed:
        print(f"{len(failed)} zoning source(s) FAILED: {', '.join(failed)}; their parcels kept "
              "their previous zoning. Exit 4.")
        return 4
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-open-run-check", action="store_true",
                    help="off a run branch: skip the 'newest mirror row is not a halt' check "
                         "(the run-branch baseline checks always apply)")
    args = ap.parse_args(argv)
    return run(check_open_run=not args.no_open_run_check)


if __name__ == "__main__":
    sys.exit(main())
