"""Import a CoStar Excel export: submarket stats CSV, submarket areas, land comps.

Operator tool, run BETWEEN runs only (never by the agent, never mid-run):

    python costar_import.py path/to/ATL_export.xlsx [--no-areas]
    make costar-import FILE=path/to/ATL_export.xlsx

It refuses (exit 3) once the current autoresearch/<tag> run has its
baseline row: new stats and areas change what every later experiment
scores, so the jump would be credited to whichever experiment ran next
(reviews/19_costar_import, risk 2). home_machine/autopilot.sh runs it after
a NEW run's branch is cut and before its baseline. Immutable during a run,
like costar_ingest.py.

What it does with the workbook (sheets are found by their headers):

1. Stats sheet (CoStar's long layout: 'Geography Name' x 'Concept Name' x
   quarter columns) -> costar_exports/submarket_stats/
   submarket_stats_<quarter end>.csv in the COSTAR_INGESTION_CONTRACT.md
   Export 1 columns. The runner's ingestion loads it at the next
   evaluate(). The LAST FULL QUARTER is used ('2026 Q3'), never 'QTD' or
   'EST' (a forecast); report_date is that quarter's end, so re-importing
   the same workbook is idempotent in the loader (source, submarket,
   as_of_date). Rates given as fractions become percents.
   Approximations, documented in COSTAR_EXPORTS_README.md:
   availability_rate_pct <- 'Vacant Available %', asking_rent_nnn_psf <-
   'All Service Type Rent Overall' (not strictly NNN), proposed_sf blank.

2. Sale comps sheet ('Submarket Name', 'Parcel Number 1 (Min)') -> where
   each submarket's comps sit. Fulton-format parcel numbers are located
   through the Fulton parcel layer registered in sources.json (fulton_ga,
   read-only here); each submarket with at least MIN_POINTS located comps
   gets submarkets.bbox = a 300 m buffer around their convex hull (a comp
   with no other comp of its submarket within OUTLIER_KM is dropped).
   research.py assigns each parcel to the nearest area at read time;
   nothing is stored on parcels.

3. Land sales sheet (a CoStar Land export: 'Land Area AC', 'Sale Price',
   'Sale Date', 'Zoning', 'Latitude', 'Longitude', with no parcel-number or
   building columns) -> S8 land comps (reviews/20_next_improvements/05_*).
   Only SOLD land sales of 5-50 acres on INDUSTRIAL zoning count (zoning,
   not CoStar's 'Secondary Type': entitlement drives price); portfolio rows,
   duplicates and rows whose $/acre disagrees with price / acres are
   dropped. Each sale goes to the area research.py would give a parcel at
   the same point (nearest within 3 km), so CoStar's land submarket names
   are never used. Written straight to sales_comps in ONE transaction that
   replaces this tool's earlier land rows (raw import_origin); hand-
   delivered land_sales_comps CSVs are untouched. The kept sales are saved
   in costar_exports/.land_sales_snapshot.json: with no areas yet they wait
   there (exit 0), and every later workbook that redraws the areas assigns
   them again.
Areas are written in ONE transaction, only over areas this tool wrote
before (notes start with AREA_NOTE_PREFIX) or empty ones: a human-seeded
bbox is never overwritten. A tool-written area whose submarket no longer
qualifies in the new export is cleared, so no area outlives its data.
Without a database the CSV is still written and the tool exits 2 ("areas
not written").

CoStar data is licensed: everything lands under costar_exports/
(gitignored); the console shows counts only, never names or values.

Depends only on prepare, costar_ingest and pipeline_common -- never on
research.py (the agent's sandbox) or runner.py (which imports it).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
import requests

import costar_ingest
import prepare
from pipeline_common import _COSTAR_BASE_DIR, _REPO_ROOT

MARKET_NAME = "Atlanta"
GEOGRAPHY_PREFIX = "Atlanta - GA USA - "
PROPERTY_CLASS = "Industrial"
MIN_POINTS = 3
OUTLIER_KM = 8.0
AREA_BUFFER_M = 300.0
AREA_NOTE_PREFIX = "costar_import area"
REQUEST_INTERVAL_S = 1.0          # SR-11: 1 request/second per county host
MAX_RETRIES = 3
MAX_PAGES_PER_LOT = 20
MAX_XLSX_UNCOMPRESSED = 200 * 1024 * 1024
HEADER_SEARCH_ROWS = 10
FULTON_ENVELOPE = (-84.95, 33.35, -84.0, 34.25)   # loose lon/lat sanity box
LOCATION_CACHE = _COSTAR_BASE_DIR / ".comp_parcel_locations.json"
EXPERIMENT_LOG = _REPO_ROOT / "experiment_log.tsv"
# Same marker runner._DESCRIPTION_RUN_RE reads (pinned by a test).
_RUN_MARKER_RE = re.compile(r"(?:^|\s)run=([a-z0-9._-]+)")

STATS_OUT_COLUMNS = (
    "submarket_name", "market", "total_inventory_sf", "vacancy_rate_pct",
    "availability_rate_pct", "net_absorption_t12_sf", "under_construction_sf",
    "proposed_sf", "asking_rent_nnn_psf", "report_date",
)
# CoStar 'Concept Name' -> (contract column, is a rate that may be a fraction)
CONCEPTS = {
    "Inventory SF": ("total_inventory_sf", False),
    "Vacancy Rate": ("vacancy_rate_pct", True),
    "Vacant Available %": ("availability_rate_pct", True),
    "Net Absorption SF 12 Mo": ("net_absorption_t12_sf", False),
    "Under Construction SF": ("under_construction_sf", False),
    "Proposed SF": ("proposed_sf", False),
    "All Service Type Rent Overall": ("asking_rent_nnn_psf", False),
}
INT_COLUMNS = {"total_inventory_sf", "net_absorption_t12_sf",
               "under_construction_sf", "proposed_sf"}
QUARTER_RE = re.compile(r"^(\d{4}) Q([1-4])$")
# Fulton parcel ids: 2-digit district (+ optional letter), 4-digit land lot,
# then the rest, once spaces and dashes are removed ('14F0134LL0195').
# Other counties' ids can share this shape; they simply are not found.
FULTON_ID_RE = re.compile(r"^(\d{2}[A-Z]?)(\d{4})([A-Z0-9]{3,12})$")

# Land sales sheet (reviews/20_next_improvements/05_*). Industrial land
# only (zoning decides), 5-50 acres per COSTAR_INGESTION_CONTRACT.md Export 2.
LAND_HEADERS = ("Land Area AC", "Sale Price", "Sale Date", "Zoning", "Latitude", "Longitude")
# A sheet with any of these is a building/area-comps sheet, never Land (R-1).
LAND_EXCLUDING_HEADERS = ("Parcel Number 1 (Min)", "RBA", "Building SF", "Year Built")
LAND_MIN_AC = 5.0
LAND_MAX_AC = 50.0
PER_ACRE_TOLERANCE = 0.05
GEORGIA_BOX = (-85.7, 30.3, -80.8, 35.1)          # lon_min, lat_min, lon_max, lat_max
LAND_ASSIGN_RADIUS_M = 3000                        # = research._SQL_RESOLVED_SUBMARKET
LAND_ORIGIN = "costar_import_land"                 # raw->>'import_origin' of our rows
LAND_SNAPSHOT_NAME = ".land_sales_snapshot.json"   # under gitignored costar_exports/
_ZONING_SPLIT_RE = re.compile(r"[\s,/&;()]+")
_INDUSTRIAL_TOKEN_RE = re.compile(
    r"^(M|M-?\d{1,2}[A-Z]?|LM|LI(-?\d)?|L-I|IL|I-L|HI|IH|I|I-?\d{1,2}[A-Z]?|GI|MIA|"
    r"P-M|PD-I|I-MIX|IND|IG|IP)$")
_BARE_TOKENS = ("I", "M")                          # count only as the first token
# 'O&I', 'O / I', 'O-I' (office-institutional) are one code, never a bare 'I'.
_OFFICE_INSTITUTIONAL_RE = re.compile(r"\bO\s*[&/-]\s*I\b")
# Proposed, pending or negated entitlement is not entitlement (review #4).
_NOT_ENTITLED_RE = re.compile(r"\b(REZON\w*|PROPOSED|PENDING|NON-?\s*INDUSTRIAL|NOT\s+INDUSTRIAL)\b")
LAND_MAX_PRICE = 10_000_000_000                   # $10B: far above any land sale
LAND_MAX_PER_ACRE = 20_000_000                    # $20M/acre
_PORTFOLIO_RE = re.compile(r"(?i)\bpart of an? \d+ property")

_SQL_COUNT_MARKET_AREAS = (
    "SELECT COUNT(*) FROM submarkets WHERE market_id = %s AND bbox IS NOT NULL"
)
# Same rule as research._SQL_RESOLVED_SUBMARKET (R-7): nearest area of the
# market within the radius, ties by area size, then id. Pinned by a test
# here and checked live in tests/live_submarket_check.py.
_SQL_ASSIGN_POINTS_TO_AREAS = (
    "SELECT t.i, (SELECT s.submarket_id FROM submarkets s "
    "WHERE s.market_id = %s AND s.bbox IS NOT NULL "
    "AND s.bbox && ST_Expand(t.pt, 0.05) "
    "AND ST_DWithin(s.bbox::geography, t.pt::geography, %s) "
    "ORDER BY ST_Distance(s.bbox::geography, t.pt::geography), "
    "ST_Area(s.bbox::geography), s.submarket_id LIMIT 1) "
    "FROM (SELECT u.i, ST_SetSRID(ST_MakePoint(u.x, u.y), 4326) AS pt "
    "FROM unnest(%s::float8[], %s::float8[]) WITH ORDINALITY AS u(x, y, i)) t "
    "ORDER BY t.i"
)
# Land rows delivered another way (a land_sales_comps CSV): never counted twice.
_SQL_OTHER_LAND_KEYS = (
    "SELECT address, sale_date FROM sales_comps WHERE comp_type = 'land' "
    "AND COALESCE(raw->>'import_origin', '') <> %s "
    "AND submarket_id IN (SELECT submarket_id FROM submarkets WHERE market_id = %s)"
)
_SQL_DELETE_TOOL_LAND_COMPS = (
    "DELETE FROM sales_comps WHERE comp_type = 'land' AND raw->>'import_origin' = %s "
    "AND submarket_id IN (SELECT submarket_id FROM submarkets WHERE market_id = %s)"
)

_SQL_AREA_FROM_POINTS = (
    "SELECT ST_AsEWKT(a), GeometryType(a), ST_IsValid(a), ST_SRID(a), "
    "ST_Area(a::geography) "
    "FROM (SELECT ST_Buffer(ST_ConvexHull(ST_Collect("
    "ST_SetSRID(ST_MakePoint(x, y), 4326)))::geography, %s)::geometry AS a "
    "FROM unnest(%s::float8[], %s::float8[]) AS t(x, y)) q"
)
_SQL_FETCH_SUBMARKET_AREA_NOTE = (
    "SELECT bbox IS NOT NULL, notes FROM submarkets WHERE submarket_id = %s"
)
_SQL_UPDATE_SUBMARKET_AREA = (
    "UPDATE submarkets SET bbox = ST_GeomFromEWKT(%s), notes = %s "
    "WHERE submarket_id = %s"
)
# Tool-written areas of this market that the new export no longer supports.
_SQL_CLEAR_STALE_AREAS = (
    "UPDATE submarkets SET bbox = NULL, notes = NULL "
    "WHERE market_id = %s AND bbox IS NOT NULL AND notes LIKE %s "
    "AND NOT (submarket_id = ANY(%s))"
)


class ImportError_(Exception):
    """A workbook, sheet or state this tool refuses (operator-facing message)."""


# ---------------------------------------------------------------------------
# Run-state guard (risk 2)
# ---------------------------------------------------------------------------
def run_has_baseline(log_path: Path = EXPERIMENT_LOG) -> bool:
    """Does the CURRENT autoresearch/<tag> run already have its baseline row?

    Mirrors runner.current_run_has_baseline without importing runner
    (which imports research, the agent's sandbox).
    """
    tag = prepare.current_run_tag()
    if not tag or not log_path.exists():
        return False
    with log_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            m = _RUN_MARKER_RE.search(row.get("description") or "")
            if row.get("status") == "baseline" and m and m.group(1) == tag:
                return True
    return False


# ---------------------------------------------------------------------------
# Reading .xlsx with the standard library (SR-10: no new dependency)
# ---------------------------------------------------------------------------
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _local(tag: str) -> str:
    """Element name without its namespace (transitional or strict OOXML)."""
    return tag.rsplit("}", 1)[-1]


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text(el: ET.Element | None) -> str:
    """Text of a shared/inline string: <t>, or the <t> of each rich-text run
    <r>; phonetic runs (<rPh>) are not part of the value."""
    if el is None:
        return ""
    parts = []
    for child in el:
        name = _local(child.tag)
        if name == "t":
            parts.append(child.text or "")
        elif name == "r":
            parts.extend(t.text or "" for t in child if _local(t.tag) == "t")
    return "".join(parts)


def _child(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if _local(c.tag) == name), None)


def _attr(el: ET.Element, name: str) -> str | None:
    """Attribute by local name (r:id lives in a namespace)."""
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def read_xlsx(path: Path) -> dict[str, list[list[Any]]]:
    """Return {sheet name: rows}. Cells are str, float, bool or None."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ImportError_(f"{path.name} is not an .xlsx workbook") from exc
    with zf:
        if sum(i.file_size for i in zf.infolist()) > MAX_XLSX_UNCOMPRESSED:
            raise ImportError_(f"{path.name} is larger than this tool accepts")
        names = set(zf.namelist())
        try:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                shared = [_text(si) for si in root if _local(si.tag) == "si"]
            rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            targets = {r.get("Id"): r.get("Target", "") for r in rels_root
                       if _local(r.tag) == "Relationship"}
            wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        except (KeyError, ET.ParseError) as exc:
            raise ImportError_(f"{path.name} is not a readable workbook") from exc

        sheets: dict[str, list[list[Any]]] = {}
        sheets_el = _child(wb_root, "sheets")
        for sheet in (sheets_el if sheets_el is not None else []):
            target = targets.get(_attr(sheet, "id"), "").lstrip("/")
            member = target if target.startswith("xl/") else f"xl/{target}"
            if member not in names:
                continue
            try:
                sheets[sheet.get("name", "")] = _read_sheet(zf.read(member), shared)
            except ET.ParseError as exc:
                raise ImportError_(f"a sheet of {path.name} is not readable") from exc
        return sheets


def _cell_value(c: ET.Element, shared: Sequence[str]) -> Any:
    kind = c.get("t", "n")
    v = _child(c, "v")
    raw = v.text if v is not None else None
    if kind == "s" and raw is not None:
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return None
    if kind == "inlineStr":
        return _text(_child(c, "is"))
    if kind == "b":
        return raw == "1"
    if raw in (None, ""):
        return None
    if kind == "n":
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw                       # str, e (error), d (ISO date): text


def _read_sheet(xml: bytes, shared: Sequence[str]) -> list[list[Any]]:
    rows: dict[int, dict[int, Any]] = {}
    next_row = 1
    for row in ET.fromstring(xml).iter():
        if _local(row.tag) != "row":
            continue
        r = int(row.get("r") or next_row)
        next_row = r + 1
        col = 0
        for c in row:
            if _local(c.tag) != "c":
                continue
            m = _CELL_REF_RE.match(c.get("r", ""))
            if m:
                col = _col_index(m.group(1))
            rows.setdefault(r, {})[col] = _cell_value(c, shared)
            col += 1
    out = []
    for r in sorted(rows):
        cells = rows[r]
        out.append([cells.get(i) for i in range(max(cells) + 1)])
    return out


def read_tables(path: Path) -> list[list[list[Any]]]:
    """Every table in the file: each sheet of an .xlsx, or one .csv."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return [[[c if c != "" else None for c in row] for row in csv.reader(fh)]]
    return list(read_xlsx(path).values())


def find_header(rows: list[list[Any]], *required: str) -> tuple[dict[str, int], int] | None:
    """({header: column}, index of the header row) for the first row among
    the top HEADER_SEARCH_ROWS that holds every ``required`` header."""
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        hdr = {str(h).strip(): j for j, h in enumerate(row) if h is not None}
        if all(r in hdr for r in required):
            return hdr, i
    return None


# ---------------------------------------------------------------------------
# Stats sheet -> contract CSV
# ---------------------------------------------------------------------------
@dataclass
class StatsResult:
    report_date: date
    quarter: str
    rows: list[dict[str, Any]]
    skipped_clusters: int = 0


def _quarter_end(year: int, q: int) -> date:
    return {1: date(year, 3, 31), 2: date(year, 6, 30),
            3: date(year, 9, 30), 4: date(year, 12, 31)}[q]


def _num(value: Any) -> tuple[float | None, bool]:
    """(number, written with a % sign)."""
    if value is None or value == "":
        return None, False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (float(value) if math.isfinite(value) else None), False
    s = str(value).strip()
    pct = "%" in s
    try:
        x = float(s.replace(",", "").replace("$", "").replace("%", "").strip())
    except ValueError:
        return None, pct
    return (x if math.isfinite(x) else None), pct


def convert_stats(rows: list[list[Any]]) -> StatsResult:
    found = find_header(rows, "Geography Name", "Concept Name")
    if not found:
        raise ImportError_("no stats sheet (needs 'Geography Name' and 'Concept Name' columns)")
    hdr, start = found
    quarters = []
    for name, idx in hdr.items():
        m = QUARTER_RE.match(name)
        if m:
            quarters.append(((int(m.group(1)), int(m.group(2))), name, idx))
    if not quarters:
        raise ImportError_("stats sheet has no full-quarter column like '2026 Q3'")
    (year, q), qname, qcol = max(quarters)
    cls = hdr.get("Property Class Name")

    values: dict[str, dict[str, float | None]] = {}
    percent_cols: set[str] = set()
    clusters: set[str] = set()
    for row in rows[start + 1:]:
        cell = lambda i: row[i] if i is not None and i < len(row) else None  # noqa: E731
        if cls is not None and str(cell(cls) or "").strip() != PROPERTY_CLASS:
            continue
        geo = str(cell(hdr["Geography Name"]) or "")
        if not geo.startswith(GEOGRAPHY_PREFIX):
            continue                        # market totals, other markets
        name = geo[len(GEOGRAPHY_PREFIX):].strip()
        if not name:
            continue
        if name.endswith("(Cluster)"):
            clusters.add(name)
            continue
        concept = str(cell(hdr["Concept Name"]) or "").strip()
        if concept not in CONCEPTS:
            continue
        column = CONCEPTS[concept][0]
        sub = values.setdefault(name, {})
        if column in sub:
            raise ImportError_("stats sheet repeats a concept for one submarket")
        sub[column], pct = _num(cell(qcol))
        if pct:
            percent_cols.add(column)

    if not values:
        raise ImportError_("stats sheet has no Atlanta industrial submarket rows")

    # A rate column holds fractions when every value is <= 1 and none was
    # written with a % sign (CoStar's 0.0867 for 8.67%); otherwise it
    # already is a percent.
    for column, is_rate in CONCEPTS.values():
        if not is_rate or column in percent_cols:
            continue
        seen = [v[column] for v in values.values() if v.get(column) is not None]
        if seen and all(0.0 <= x <= 1.0 for x in seen):
            for v in values.values():
                if v.get(column) is not None:
                    v[column] = round(v[column] * 100.0, 4)

    report = _quarter_end(year, q)
    out = []
    for name in sorted(values):
        row = {"submarket_name": name, "market": MARKET_NAME,
               "report_date": report.isoformat()}
        for column in STATS_OUT_COLUMNS[2:-1]:
            v = values[name].get(column)
            if v is None:
                row[column] = ""
            elif column in INT_COLUMNS:
                row[column] = str(int(round(v)))
            else:
                row[column] = f"{v:.4f}".rstrip("0").rstrip(".")
        out.append(row)
    return StatsResult(report, qname, out, len(clusters))


def write_stats_csv(result: StatsResult, base: Path = _COSTAR_BASE_DIR) -> Path:
    """Atomic write (SR-7): a half-written file is never picked up."""
    dest_dir = base / "submarket_stats"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"submarket_stats_{result.report_date:%Y%m%d}.csv"
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=dest_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=STATS_OUT_COLUMNS)
            w.writeheader()
            w.writerows(result.rows)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


# ---------------------------------------------------------------------------
# Comps sheet -> located comp parcels per submarket
# ---------------------------------------------------------------------------
def comp_parcels(rows: list[list[Any]]) -> dict[str, set[str]]:
    """{submarket name: normalized Fulton-format parcel ids}."""
    found = find_header(rows, "Submarket Name")
    if not found:
        return {}
    hdr, start = found
    cols = [hdr[c] for c in ("Parcel Number 1 (Min)", "Parcel Number 2 (Max)") if c in hdr]
    out: dict[str, set[str]] = {}
    for row in rows[start + 1:]:
        i = hdr["Submarket Name"]
        name = str((row[i] if i < len(row) else None) or "").strip()
        if not name:
            continue
        for c in cols:
            pid = normalize_parcel_id(row[c] if c < len(row) else None)
            if pid:
                out.setdefault(name, set()).add(pid)
    return out


def normalize_parcel_id(raw: Any) -> str | None:
    """'14F-0134-LL-019-5' -> '14F0134LL0195' when it is Fulton-shaped."""
    if raw is None:
        return None
    s = re.sub(r"[^0-9A-Z]", "", str(raw).upper())
    return s if FULTON_ID_RE.match(s) else None


def _fulton_layer_url() -> str:
    reg = json.loads((_REPO_ROOT / "sources.json").read_text(encoding="utf-8"))
    entry = reg["county_parcel_data"]["fulton_ga"]
    return f"{entry['service_url'].rstrip('/')}/{int(entry['parcel_layer_id'])}/query"


def _polygon_centroid(rings: Sequence[Sequence[Sequence[float]]]) -> tuple[float, float] | None:
    """Area-weighted centroid of the largest ring."""
    best = None
    for ring in rings:
        a = cx = cy = 0.0
        for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
            f = x0 * y1 - x1 * y0
            a += f
            cx += (x0 + x1) * f
            cy += (y0 + y1) * f
        if a and (best is None or abs(a) > abs(best[0])):
            best = (a, cx / (3 * a), cy / (3 * a))
    return (best[1], best[2]) if best else None


class FultonLocator:
    """Locates normalized Fulton parcel ids, one land lot per request."""

    def __init__(self, session: Any = None, cache_path: Path = LOCATION_CACHE,
                 sleep: Any = time.sleep) -> None:
        self.session = session or requests.Session()
        self.cache_path = cache_path
        self.sleep = sleep
        self.url = _fulton_layer_url()
        self.cache: dict[str, Any] = {}
        if cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.cache = {}
        self.requests = 0

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, MAX_RETRIES + 1):
            if self.requests:
                self.sleep(REQUEST_INTERVAL_S)
            self.requests += 1
            try:
                resp = self.session.get(self.url, params=params, timeout=60)
                resp.raise_for_status()
                body = resp.json()
                if isinstance(body, dict) and "error" not in body:
                    return body
            except (requests.RequestException, ValueError):
                pass
            self.sleep(2 ** attempt)
        raise ImportError_("the Fulton parcel service did not answer; try again later")

    def _fetch_land_lot(self, district: str, land_lot: str) -> dict[str, list[list[float]]]:
        # district/land_lot come from FULTON_ID_RE (alphanumerics only).
        where = f"ParcelID LIKE '{district}%{land_lot}%'"
        found: dict[str, list[list[float]]] = {}
        offset = 0
        previous_first = None
        for _page in range(MAX_PAGES_PER_LOT):
            body = self._get({
                "where": where, "outFields": "ParcelID", "returnGeometry": "true",
                "outSR": 4326, "f": "json", "resultOffset": offset,
                "resultRecordCount": 2000,
            })
            feats = body.get("features") or []
            first = str(feats[0].get("attributes", {}).get("ParcelID")) if feats else None
            if feats and first == previous_first:
                break                      # server ignored resultOffset
            previous_first = first
            for f in feats:
                pid = re.sub(r"[^0-9A-Z]", "", str(f.get("attributes", {}).get("ParcelID", "")).upper())
                pt = _polygon_centroid((f.get("geometry") or {}).get("rings") or [])
                if pid and pt:
                    found.setdefault(pid, []).append([round(pt[0], 6), round(pt[1], 6)])
            if not body.get("exceededTransferLimit") or not feats:
                break
            offset += len(feats)
        return found

    def locate(self, ids: Iterable[str]) -> dict[str, tuple[float, float]]:
        wanted = sorted(set(ids))
        lots = sorted({m.group(1, 2) for m in (FULTON_ID_RE.match(i) for i in wanted)
                       if f"lot:{m.group(1)}{m.group(2)}" not in self.cache})
        for district, land_lot in lots:
            got = self._fetch_land_lot(district, land_lot)
            for pid, pts in got.items():
                # Exactly one polygon per id, inside Fulton (outSR honoured).
                ok = (len(pts) == 1
                      and FULTON_ENVELOPE[0] <= pts[0][0] <= FULTON_ENVELOPE[2]
                      and FULTON_ENVELOPE[1] <= pts[0][1] <= FULTON_ENVELOPE[3])
                self.cache[f"id:{pid}"] = pts[0] if ok else None
            if got:
                # An empty answer may be transient: ask again next import.
                self.cache[f"lot:{district}{land_lot}"] = True
        self._save()
        out = {}
        for i in wanted:
            pt = self.cache.get(f"id:{i}")
            if pt:
                out[i] = (pt[0], pt[1])
        return out

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=self.cache_path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.cache, fh)
        os.replace(tmp, self.cache_path)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    dx = (a[0] - b[0]) * 111.32 * math.cos(lat)
    dy = (a[1] - b[1]) * 110.57
    return math.hypot(dx, dy)


def trim_points(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop isolated points: no other point of the submarket within OUTLIER_KM.

    A lone mis-filed comp across the county would otherwise stretch the
    hull. Distance to the median is the wrong test: elongated submarkets
    (Airport/North Clayton runs from the airport into South Fulton) have
    legitimate clusters far from their median.
    """
    pts = list(points)
    return [p for i, p in enumerate(pts)
            if any(_km(p, q) <= OUTLIER_KM for j, q in enumerate(pts) if j != i)]


@dataclass
class AreaPlan:
    points: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    too_few: dict[str, int] = field(default_factory=dict)      # 1..MIN_POINTS-1 kept
    none_located: list[str] = field(default_factory=list)


def plan_areas(parcels: dict[str, set[str]], located: dict[str, tuple[float, float]]) -> AreaPlan:
    plan = AreaPlan()
    for name, ids in sorted(parcels.items()):
        raw = [located[i] for i in sorted(ids) if i in located]
        pts = trim_points(raw)
        if len(pts) >= MIN_POINTS:
            plan.points[name] = pts
        elif raw:
            plan.too_few[name] = len(pts)
        else:
            plan.none_located.append(name)
    return plan


def write_areas(conn: Any, plan: AreaPlan, today: date,
                market_name: str = MARKET_NAME) -> dict[str, int]:
    """All areas in one transaction (SR-5); never over a human-seeded bbox.

    Tool-written areas of this market whose submarket is not in ``plan``
    are cleared in the same transaction. Returns counts only (SR-8).
    """
    counts = {"written": 0, "kept_human_seeded": 0, "cleared_stale": 0, "name_drift": 0}
    if not plan.points:
        return counts                # never clear every area on an empty plan (R-14)
    ours: list[str] = []
    with conn.transaction():
        for name, pts in sorted(plan.points.items()):
            sub_id, _created, drift = costar_ingest._ensure_submarket(conn, market_name, name)
            ours.append(sub_id)
            counts["name_drift"] += bool(drift)
            with conn.cursor() as cur:
                cur.execute(_SQL_FETCH_SUBMARKET_AREA_NOTE, (sub_id,))
                has_bbox, notes = cur.fetchone() or (False, None)
            if has_bbox and not str(notes or "").startswith(AREA_NOTE_PREFIX):
                counts["kept_human_seeded"] += 1
                continue
            with conn.cursor() as cur:
                cur.execute(_SQL_AREA_FROM_POINTS,
                            (AREA_BUFFER_M, [p[0] for p in pts], [p[1] for p in pts]))
                ewkt, gtype, valid, srid, _area_m2 = cur.fetchone()
            if gtype != "POLYGON" or not valid or srid != 4326:
                raise ImportError_(f"a computed area is not a valid polygon ({gtype})")
            note = f"{AREA_NOTE_PREFIX} {today.isoformat()} from {len(pts)} located comps"
            with conn.cursor() as cur:
                cur.execute(_SQL_UPDATE_SUBMARKET_AREA, (ewkt, note, sub_id))
            counts["written"] += 1
        with conn.cursor() as cur:
            cur.execute(_SQL_CLEAR_STALE_AREAS,
                        (costar_ingest._slugify(market_name), AREA_NOTE_PREFIX + "%", ours))
            counts["cleared_stale"] = max(int(getattr(cur, "rowcount", 0) or 0), 0)
    return counts


# ---------------------------------------------------------------------------
# Land sales sheet -> industrial land comps, assigned to areas
# (reviews/20_next_improvements/05_*)
# ---------------------------------------------------------------------------
def is_industrial_zoning(zoning: Any) -> bool:
    """True when the zoning text carries an industrial entitlement.

    Entitlement drives land price, so CoStar's 'Secondary Type' is not used.
    'industrial' anywhere counts; otherwise one token (split on spaces and
    , / & ; ( )) must be an industrial code: 'M1/C1', 'LI & GC' and
    'M, DeKalb County' count; 'O-I', 'O&I', 'MU', 'MHP' and 'MD' do not. A
    bare 'I' or 'M' counts only as the first token ('PUD Phase I' does not),
    and proposed, pending or negated text ('rezoning to M-1',
    'non-industrial') never counts.
    """
    text = str(zoning or "").strip().upper()
    if not text or _NOT_ENTITLED_RE.search(text):
        return False
    if "INDUSTRIAL" in text:
        return True
    tokens = [t for t in _ZONING_SPLIT_RE.split(_OFFICE_INSTITUTIONAL_RE.sub("OI", text)) if t]
    return any(_INDUSTRIAL_TOKEN_RE.match(t) and (t not in _BARE_TOKENS or i == 0)
               for i, t in enumerate(tokens))


def is_land_sheet(rows: list[list[Any]]) -> bool:
    """A CoStar Land sales sheet: land headers and no building/parcel ones (R-1)."""
    found = find_header(rows, *LAND_HEADERS)
    return bool(found) and not any(m in found[0] for m in LAND_EXCLUDING_HEADERS)


def xlsx_date1904(path: Path) -> bool:
    """True when the workbook uses Excel's 1904 date system."""
    if path.suffix.lower() != ".xlsx":
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("xl/workbook.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return False
    pr = _child(root, "workbookPr")
    return pr is not None and str(_attr(pr, "date1904") or "").lower() in ("1", "true")


def _sale_date(value: Any, date1904: bool) -> date | None:
    """An Excel serial or 'YYYY-MM-DD' / 'M/D/YYYY' text -> date (R-4)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < 1:
            return None
        base = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
        try:
            return base + timedelta(days=int(value))
        except OverflowError:
            return None
    s = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s) or None
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
        if m:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    return None


def _finite(value: Any) -> float | None:
    v, _ = _num(value)
    return v if v is not None and math.isfinite(v) else None


def _address_key(address: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(address or "").upper())


@dataclass
class LandResult:
    rows: list[dict[str, Any]]
    dropped: dict[str, int] = field(default_factory=dict)


def convert_land(rows: list[list[Any]], *, today: date, date1904: bool = False) -> LandResult:
    """Industrial land sales that may serve as S8 comps. Counts drops by reason."""
    found = find_header(rows, *LAND_HEADERS)
    if not found:
        raise ImportError_("no land sales sheet (needs " + ", ".join(LAND_HEADERS) + ")")
    hdr, start = found
    if "Sale Status" not in hdr:
        raise ImportError_("the land sales sheet needs a 'Sale Status' column "
                           "(without it, listings and pending deals would count as sales)")

    def get(row: list[Any], name: str) -> Any:
        i = hdr.get(name)
        return row[i] if i is not None and i < len(row) else None

    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, date, float]] = set()
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for row in rows[start + 1:]:
        if not any(c not in (None, "") for c in row):
            continue
        ptype = get(row, "Property Type")
        if "Property Type" in hdr and str(ptype or "").strip().lower() != "land":
            drop("not land"); continue
        if str(get(row, "Sale Status") or "").strip().lower() != "sold":
            drop("not sold"); continue
        sold = _sale_date(get(row, "Sale Date"), date1904)
        if sold is None:
            drop("no sale date"); continue
        if sold > today:
            drop("future sale date"); continue
        price, acres = _finite(get(row, "Sale Price")), _finite(get(row, "Land Area AC"))
        if price is None or price <= 0 or acres is None or acres <= 0:
            drop("no price or acres"); continue
        if price > LAND_MAX_PRICE or price / acres > LAND_MAX_PER_ACRE:
            drop("implausible price"); continue
        if not LAND_MIN_AC <= acres <= LAND_MAX_AC:
            drop("outside 5-50 acres"); continue
        lat, lon = _finite(get(row, "Latitude")), _finite(get(row, "Longitude"))
        if lat is None or lon is None:
            drop("no location"); continue
        if not (GEORGIA_BOX[0] <= lon <= GEORGIA_BOX[2] and GEORGIA_BOX[1] <= lat <= GEORGIA_BOX[3]):
            drop("location outside Georgia"); continue
        zoning = str(get(row, "Zoning") or "").strip()
        if not is_industrial_zoning(zoning):
            drop("not industrially zoned"); continue
        address = str(get(row, "Property Address") or "").strip()
        label = f"{address} {get(row, 'Property Name') or ''}"
        if _PORTFOLIO_RE.search(label):
            drop("part of a portfolio or multi-property sale"); continue
        per_acre = price / acres
        costar_per_acre = _finite(get(row, "Price Per AC Land"))
        if costar_per_acre is not None and abs(costar_per_acre - per_acre) > PER_ACRE_TOLERANCE * per_acre:
            drop("price per acre disagrees with price / acres"); continue
        key = (_address_key(address) or f"{lat:.5f},{lon:.5f}", sold, round(acres, 1))
        if key in seen:
            drop("duplicate address and date"); continue
        seen.add(key)
        county = re.sub(r"(?i)\s+county$", "", str(get(row, "Property County") or "").strip())
        kept.append({
            "address": address or None,
            "county": county or None,
            "acres": round(acres, 6),
            "sale_date": sold.isoformat(),
            "sale_price": int(round(price)),
            "price_per_acre": round(per_acre, 2),
            "buyer_name": str(get(row, "Buyer (True) Company") or "").strip() or None,
            "seller_name": str(get(row, "Seller (True) Company") or "").strip() or None,
            "zoning": zoning,
            "intended_use": str(get(row, "Proposed Use") or "").strip() or None,
            "latitude": lat,
            "longitude": lon,
        })
    return LandResult(kept, dropped)


def save_land_snapshot(rows: list[dict[str, Any]], base: Path, today: date) -> Path:
    """Atomic write under gitignored costar_exports/ (licensed data, SR-7)."""
    base.mkdir(parents=True, exist_ok=True)
    dest = base / LAND_SNAPSHOT_NAME
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=base)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "saved_on": today.isoformat(), "rows": rows}, fh)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def load_land_snapshot(base: Path) -> list[dict[str, Any]] | None:
    path = base / LAND_SNAPSHOT_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["rows"] if data.get("version") == 1 else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ImportError_("the saved land sales snapshot is unreadable; re-import the Land export")
    if not isinstance(rows, list):
        raise ImportError_("the saved land sales snapshot is unreadable; re-import the Land export")
    return rows


def write_land(conn: Any, rows: list[dict[str, Any]], today: date,
               market_name: str = MARKET_NAME) -> dict[str, int]:
    """Replace this tool's land comps in ONE transaction (R-2). Counts only.

    Each sale goes to the market's area that research.py would give a parcel
    at the same point (nearest area within 3 km; ties by area size, then id).
    The assignment is frozen until the next import. With no areas at all,
    nothing is touched ('deferred': the snapshot is assigned later).
    """
    market_id = costar_ingest._slugify(market_name)
    counts = {"deferred": 0, "assigned": 0, "no_area": 0, "submarkets": 0, "replaced": 0,
              "already_delivered": 0, "kept_previous": 0}
    if not rows:
        raise ImportError_("no industrial land sale qualifies; land comps left unchanged")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(_SQL_COUNT_MARKET_AREAS, (market_id,))
            (n_areas,) = cur.fetchone()
        if not n_areas:
            counts["deferred"] = 1
            return counts
        with conn.cursor() as cur:
            cur.execute(_SQL_ASSIGN_POINTS_TO_AREAS,
                        (market_id, LAND_ASSIGN_RADIUS_M,
                         [float(r["longitude"]) for r in rows],
                         [float(r["latitude"]) for r in rows]))
            assigned = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute(_SQL_OTHER_LAND_KEYS, (LAND_ORIGIN, market_id))
            delivered = {(_address_key(a), str(d)) for a, d in cur.fetchall()}
        todo: list[tuple[dict[str, Any], str]] = []
        for ordinal, submarket_id in assigned:
            r = rows[int(ordinal) - 1]
            if submarket_id is None:
                counts["no_area"] += 1
            elif (_address_key(r.get("address")), str(r["sale_date"])) in delivered:
                counts["already_delivered"] += 1
            else:
                todo.append((r, submarket_id))
        if not todo:
            # Never trade the earlier comps for nothing (review #1).
            counts["kept_previous"] = 1
            return counts
        with conn.cursor() as cur:
            cur.execute(_SQL_DELETE_TOOL_LAND_COMPS, (LAND_ORIGIN, market_id))
            counts["replaced"] = max(int(getattr(cur, "rowcount", 0) or 0), 0)
        subs: set[str] = set()
        for r, submarket_id in todo:
            raw = {"import_origin": LAND_ORIGIN, "imported_on": today.isoformat(),
                   "latitude": r["latitude"], "longitude": r["longitude"],
                   "intended_use": r.get("intended_use")}
            params = costar_ingest._land_sales_insert_params(
                {**r, "parcel_id": None, "cap_rate": None, "raw": raw}, submarket_id)
            with conn.cursor() as cur:
                cur.execute(costar_ingest._SQL_INSERT_LAND_SALES, params)
            counts["assigned"] += 1
            subs.add(submarket_id)
        counts["submarkets"] = len(subs)
    return counts


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run(path: Path, *, areas: bool = True, locator: Any = None,
        base: Path = _COSTAR_BASE_DIR, today: date | None = None) -> int:
    today = today or date.today()
    if run_has_baseline():
        print("REFUSED: this run already has its baseline. Importing now would change "
              "what later experiments score. Start a new run first, then import before "
              "its baseline (Start New Run on the home machine).")
        return 3
    tables = read_tables(path)
    stats_rows = next((t for t in tables if find_header(t, "Geography Name", "Concept Name")), None)
    # An area-comps sheet needs parcel numbers: a Land sheet also has
    # 'Submarket Name', and an empty plan must never clear areas (R-14).
    comps_rows = next((t for t in tables
                       if find_header(t, "Submarket Name", "Parcel Number 1 (Min)")), None)
    land_sheet = next((t for t in tables if is_land_sheet(t)), None)
    if stats_rows is None and comps_rows is None and land_sheet is None:
        print("REFUSED: no CoStar stats, sale-comps or land-sales sheet found in this file.")
        return 1

    land: LandResult | None = None
    if land_sheet is not None:
        land = convert_land(land_sheet, today=today, date1904=xlsx_date1904(path))
        print(f"land: {len(land.rows)} industrial land sales kept; dropped "
              + (", ".join(f"{n} {why}" for why, n in sorted(land.dropped.items())) or "none"))
        if not land.rows:
            raise ImportError_("no industrial land sale qualifies; land comps left unchanged")

    if stats_rows is not None:
        stats = convert_stats(stats_rows)
        write_stats_csv(stats, base)
        print(f"stats: {len(stats.rows)} submarkets from {stats.quarter} "
              f"({stats.skipped_clusters} clusters skipped) -> costar_exports/submarket_stats/")
    if not areas:
        if land is not None:
            print("land: not written (--no-areas: no database writes).")
        return 0
    plan: AreaPlan | None = None
    if comps_rows is not None:
        parcels = comp_parcels(comps_rows)
        n_ids = sum(len(v) for v in parcels.values())
        print(f"areas: {n_ids} Fulton-shaped comp parcel numbers in {len(parcels)} submarkets")
        locator = locator or FultonLocator(cache_path=base / LOCATION_CACHE.name)
        located = locator.locate(i for ids in parcels.values() for i in ids)
        plan = plan_areas(parcels, located)
        share = round(100 * len(located) / n_ids) if n_ids else 0
        print(f"areas: located {len(located)} of {n_ids} ({share}%; other counties' parcels "
              f"are not in Fulton's records)")
        print(f"areas: {len(plan.points)} submarkets get an area; {len(plan.too_few)} have "
              f"fewer than {MIN_POINTS} located comps and {len(plan.none_located)} have none "
              "(no area: their parcels score submarket data as missing)")
        if not plan.points:
            print("areas: no submarket qualifies; existing areas left unchanged.")
            plan = None
    if plan is None and land is None:
        return 0
    # Saved land sales go again when the areas move (R-3). Read BEFORE the
    # areas are written: an unreadable snapshot must not block every Start.
    land_rows: list[dict[str, Any]] | None = land.rows if land is not None else None
    if land_rows is None:
        try:
            land_rows = load_land_snapshot(base)
        except ImportError_ as exc:
            print(f"land: {exc}; the areas are imported without it.")

    if not prepare.dsn_available():
        print("areas and land comps not written: no DATABASE_URL"
              + (" (the stats CSV was written)." if stats_rows is not None else "."))
        return 2
    try:
        with prepare.get_connection() as conn:
            if plan is not None:
                counts = write_areas(conn, plan, today)
                conn.commit()
                print(f"areas: wrote {counts['written']}, kept {counts['kept_human_seeded']} "
                      f"human-seeded, cleared {counts['cleared_stale']} stale"
                      + (f", {counts['name_drift']} name drifts (see submarkets table)"
                         if counts["name_drift"] else ""))
            if land_rows:
                lc = write_land(conn, land_rows, today)
                conn.commit()
                if land is not None:          # only once it was written or deferred
                    save_land_snapshot(land.rows, base, today)
                if lc["deferred"]:
                    print("land: saved; no submarket areas exist yet, so the sales are "
                          "assigned when a workbook with sale comps is imported.")
                elif lc["kept_previous"]:
                    print(f"land: no sale lies within 3 km of an area ({lc['no_area']}) or each "
                          f"was already delivered ({lc['already_delivered']}); the earlier "
                          "land comps are kept unchanged.")
                else:
                    print(f"land: {lc['assigned']} sales in {lc['submarkets']} submarket areas "
                          f"({lc['no_area']} farther than 3 km from every area; "
                          f"{lc['already_delivered']} already delivered by a land_sales_comps "
                          f"CSV; {lc['replaced']} earlier tool-imported land comps replaced)")
    except psycopg.DataError as exc:
        print(f"REFUSED: a value in the workbook does not fit the database ({type(exc).__name__}); "
              "nothing more was written.")
        return 1
    except (psycopg.Error, OSError) as exc:
        print(f"areas or land comps not written: database error ({type(exc).__name__})."
              + (" The stats CSV was written." if stats_rows is not None else ""))
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file", type=Path, help="CoStar .xlsx (or a .csv of one sheet)")
    ap.add_argument("--no-areas", action="store_true",
                    help="only write the stats CSV; do not locate comps")
    args = ap.parse_args(argv)
    if not args.file.is_file():
        print(f"REFUSED: {args.file} not found")
        return 1
    try:
        return run(args.file, areas=not args.no_areas)
    except (ImportError_, ValueError) as exc:
        # ValueError: e.g. a submarket name with no letters or digits
        # (_slugify). Message only, never cell values in a traceback.
        print(f"REFUSED: {exc if isinstance(exc, ImportError_) else type(exc).__name__ + ' in the workbook'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
