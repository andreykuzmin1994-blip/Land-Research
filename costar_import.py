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
   Only SOLD land sales of 10-50 acres from the last 22 months on
   INDUSTRIAL zoning count (zoning,
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
4. Building sales sheet (a CoStar Sale Comps export of industrial
   buildings: 'Sale Price', 'Sale Date', 'RBA', 'Sale Status', 'Latitude',
   'Longitude', ...) and lease comps sheet (a CoStar Lease Comps export:
   'SF Leased', a starting rent, 'Sign Date', 'Service Type', ...) ->
   sales_comps (comp_type 'building') and leasing_comps, for the memo's
   market rent and exit-value lines only (reviews/28_building_comps). They
   are NO scoring input. Headers match exactly (case and surrounding spaces
   aside) against the alias lists BUILDING_COLUMNS / LEASE_COLUMNS; a sheet
   that looks like one of them but lacks a required column is REFUSED with
   the names of the missing expected columns. Each sheet has exactly one
   role; two sheets with the same role are refused. A sheet with
   'Submarket Name' and 'Parcel Number 1 (Min)' keeps the area role exactly
   as before; when it also carries every building column it ALSO yields
   building comps, assigned after its areas are written (said on the
   console). A lease sheet is never an area sheet. Located from the
   export's Latitude/Longitude only (no network call), assigned like the
   land sales, written in one transaction per table that replaces only this
   tool's earlier rows (raw import_origin), saved in gitignored JSON
   snapshots and reassigned whenever the areas are redrawn.
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
# only (zoning decides), 10-50 acres, sold in the last 22 months: the
# operator's choice (smaller sites are not targets; older sales predate the
# current rate environment). COSTAR_INGESTION_CONTRACT.md Export 2.
LAND_HEADERS = ("Land Area AC", "Sale Price", "Sale Date", "Zoning", "Latitude", "Longitude")
# A sheet with any of these is a building/area-comps sheet, never Land (R-1).
LAND_EXCLUDING_HEADERS = ("Parcel Number 1 (Min)", "RBA", "Building SF", "Year Built")
LAND_MIN_AC = 10.0
LAND_MAX_AC = 50.0
LAND_MAX_AGE_MONTHS = 22                          # counted back from the import date
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


def months_before(day: date, months: int) -> date:
    """The same day ``months`` calendar months earlier (clamped to month end)."""
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    m += 1
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return date(y, m, min(day.day, (nxt - timedelta(days=1)).day))


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

    oldest = months_before(today, LAND_MAX_AGE_MONTHS)
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
        if sold < oldest:
            drop(f"older than {LAND_MAX_AGE_MONTHS} months"); continue
        price, acres = _finite(get(row, "Sale Price")), _finite(get(row, "Land Area AC"))
        if price is None or price <= 0 or acres is None or acres <= 0:
            drop("no price or acres"); continue
        if price > LAND_MAX_PRICE or price / acres > LAND_MAX_PER_ACRE:
            drop("implausible price"); continue
        if not LAND_MIN_AC <= acres <= LAND_MAX_AC:
            drop(f"outside {LAND_MIN_AC:g}-{LAND_MAX_AC:g} acres"); continue
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


def refilter_snapshot(rows: list[dict[str, Any]], today: date) -> tuple[list[dict[str, Any]], int]:
    """Saved sales replayed later must still pass today's size and age rules."""
    oldest = months_before(today, LAND_MAX_AGE_MONTHS).isoformat()
    keep = [r for r in rows
            if isinstance(r, dict)
            and isinstance(r.get("acres"), (int, float))
            and LAND_MIN_AC <= r["acres"] <= LAND_MAX_AC
            and oldest <= str(r.get("sale_date") or "") <= today.isoformat()]
    return keep, len(rows) - len(keep)


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
# Building sales and lease comps -> sales_comps (comp_type 'building') and
# leasing_comps, for the memo only (reviews/28_building_comps). Nothing
# here is a scoring input: research.py reads neither (S8 and every other
# sales_comps reader filter comp_type = 'land'; research.py has no
# leasing_comps reader), pinned by tests/test_building_comps.py.
# ---------------------------------------------------------------------------
BUILDING_ORIGIN = "costar_import_building"         # raw->>'import_origin' of our sales
LEASE_ORIGIN = "costar_import_lease"               # raw->>'import_origin' of our leases
BUILDING_SNAPSHOT_NAME = ".building_sales_snapshot.json"   # gitignored costar_exports/
LEASE_SNAPSHOT_NAME = ".lease_comps_snapshot.json"
# The operator's prototypes project 104,000 SF (400 x 260 front/rear-load)
# up to 520,000 SF (1,000 x 520 cross-dock); comps from 100K to 1M SF are
# the same bulk-distribution product. Smaller buildings and leases rent and
# trade at different $/SF; larger ones are rare one-offs.
BUILDING_MIN_SF = 100_000
BUILDING_MAX_SF = 1_000_000
# Sales are sparser than leases, so their window is longer; 36 months back
# from late 2026 starts after the 2022-23 rate reset that moved cap rates.
BUILDING_SALE_WINDOW_MONTHS = 36
# Leases: 24 months keeps the rent current (Atlanta bulk rents moved several
# percent a year) while leaving enough signed leases of 100K+ SF.
LEASE_WINDOW_MONTHS = 24
# Exit comps describe a NEW building: older stock has lower clear heights
# and trades at lower $/SF and higher cap rates. 15 years = built 2011 on
# for a 2026 import, roughly the 32-36 ft clear generation.
BUILDING_MAX_AGE_YEARS = 15
# One stored convention: sales_comps.cap_rate of OUR rows is a FRACTION
# (0.055 = 5.5%). 3-12% brackets every plausible stabilised industrial cap
# rate; outside it the value is a unit error or a non-comparable deal.
CAP_RATE_MIN = 0.03
CAP_RATE_MAX = 0.12
# $/SF of RBA: below $30 is land value or distress, above $400 is a data
# centre or cold storage filed as Industrial, for bulk warehouse in Georgia.
SALE_PSF_MIN = 30.0
SALE_PSF_MAX = 400.0
PRICE_PSF_TOLERANCE = 0.05                         # CoStar 'Price Per SF' vs price / RBA
BUILDING_MAX_PRICE = 5_000_000_000
# Annual NNN $/SF/yr: Georgia bulk rents run about $4-$10; $2 catches a
# monthly rent ($0.55/SF/mo) typed into the annual column, $25 a total or
# an office rate.
NNN_RENT_MIN = 2.0
NNN_RENT_MAX = 25.0
# A median from fewer than 5 comps moves with one deal; below this the
# memo shows the market-wide value, labelled, or nothing.
MIN_COMPS = 5
BUILDING_SECONDARY_TYPES = ("warehouse", "distribution")
# Excluded, as CoStar's 'Sale Condition' words them (case-insensitive,
# whole phrase). Each skews price or cap rate against an arm's-length sale
# of a whole, stabilised building.
SALE_CONDITION_EXCLUSIONS: tuple[tuple[str, str], ...] = (
    ("portfolio", r"portfolio|bulk"),
    ("partial interest", r"partial interest"),
    ("condo", r"condo"),
    ("sale-leaseback", r"sale[\s-]*leaseback"),
    ("distressed", r"distress|bankruptcy|\breo\b|short sale|foreclos|receivership"),
    ("auction", r"auction"),
    ("redevelopment or land value", r"redevelopment|land value|excess land|assemblage"),
    ("non-arm's length", r"non[\s-]*arm|related party|recapitali[sz]ation"),
    ("leased fee or ground lease", r"leased fee|ground lease"),
    ("business value included", r"business value"),
)
_SALE_CONDITION_RES = tuple((label, re.compile(rx, re.I)) for label, rx in SALE_CONDITION_EXCLUSIONS)
_SUBLEASE_RE = re.compile(r"(?i)\bsub[\s-]*(lease|let)")
_RENEWAL_RE = re.compile(r"(?i)\b(renewal|extension|renew)")
_NNN_RE = re.compile(r"(?i)^\s*(nnn|triple[\s-]*net|net[\s-]*net[\s-]*net)\s*$")
_TERM_YEARS_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b")
_TERM_MONTHS_RE = re.compile(r"(?i)(\d+)\s*(?:months?|mos?)\b")
_DEAL_TYPES = ("new", "expansion", "relet", "renewal", "extension")

# Exact header aliases (strip + case-fold only; never substrings, so 'Price
# Per SF Land' is never the building $/SF). The first alias present wins:
# for the building SF, 'RBA' (CoStar's rentable building area) before
# 'Building SF' before 'Total SF'.
BUILDING_COLUMNS: dict[str, tuple[str, ...]] = {
    "address": ("Property Address", "Address"),
    "property_name": ("Property Name", "Building Name"),
    "property_type": ("Property Type",),
    "secondary_type": ("Secondary Type",),
    "sale_status": ("Sale Status",),
    "sale_date": ("Sale Date",),
    "sale_price": ("Sale Price",),
    "building_sf": ("RBA", "Building SF", "Total SF"),
    "price_psf": ("Price Per SF", "Price/SF", "Sale Price Per SF"),
    "cap_rate": ("Actual Cap Rate", "Cap Rate"),
    "sale_condition": ("Sale Condition", "Sale Conditions"),
    "num_properties": ("Number Of Properties", "Number Of Properties In Sale"),
    "year_built": ("Year Built",),
    "clear_height_ft": ("Ceiling Height", "Clear Height", "Ceiling Ht"),
    "latitude": ("Latitude",),
    "longitude": ("Longitude",),
}
BUILDING_REQUIRED = ("address", "property_type", "secondary_type", "sale_status", "sale_date",
                     "sale_price", "building_sf", "sale_condition", "year_built",
                     "latitude", "longitude")
LEASE_COLUMNS: dict[str, tuple[str, ...]] = {
    "address": ("Property Address", "Address"),
    "property_type": ("Property Type",),
    "secondary_type": ("Secondary Type",),
    "sf_leased": ("SF Leased", "Square Feet Leased", "Leased SF"),
    "sign_date": ("Sign Date", "Lease Sign Date"),
    "start_date": ("Start Date", "Lease Start Date", "Commencement Date", "Move In Date",
                   "Move-In Date"),
    "starting_rent": ("Starting Rent/SF/Yr", "Starting Rent/SF", "Starting Rent"),
    "annual_rent": ("Rent/SF/Yr",),
    "asking_rent": ("Asking Rent/SF/Yr", "Asking Rent/SF", "Asking Rent"),
    "rent_type": ("Rent Type",),
    "service_type": ("Service Type", "Rent Service Type"),
    "lease_type": ("Lease Type",),
    "deal_type": ("Deal Type", "Transaction Type"),
    "term_text": ("Lease Term", "Term"),
    "term_months": ("Term (Months)", "Lease Term (Months)", "Term Months"),
    "building_rba": ("RBA", "Building SF"),
    "year_built": ("Year Built",),
    "latitude": ("Latitude",),
    "longitude": ("Longitude",),
}
LEASE_REQUIRED = ("address", "property_type", "secondary_type", "sf_leased", "sign_date",
                  "service_type", "latitude", "longitude")
# Marker columns: a sheet with one of these is a lease sheet (checked first)
# or, with 'Sale Price' too, a building sales sheet.
_LEASE_MARKERS = LEASE_COLUMNS["sf_leased"]
_BUILDING_MARKERS = BUILDING_COLUMNS["building_sf"]

# Raw JSONB allowlists (BC-05): nothing else from the export is stored --
# no contacts, brokers, phone numbers, e-mails, tenants or comments.
BUILDING_RAW_KEYS = frozenset({"import_origin", "imported_on", "latitude", "longitude",
                               "year_built", "clear_height_ft", "secondary_type",
                               "cap_rate_convention"})
LEASE_RAW_KEYS = frozenset({"import_origin", "imported_on", "latitude", "longitude",
                            "sign_date", "service_type", "rent_basis", "deal_type",
                            "building_rba", "year_built"})

# This tool's own rows: its origin, its imported_on key, and NO 'submarket'
# key -- every CSV-delivered row carries one (a required CSV column), so a
# CSV whose header row forges import_origin and imported_on is still not
# ours (BC-09).
_SQL_TOOL_ROW = ("(raw->>'import_origin' = %s AND raw ? 'imported_on' "
                 "AND NOT (raw ? 'submarket'))")
_SQL_IN_MARKET = "submarket_id IN (SELECT submarket_id FROM submarkets WHERE market_id = %s)"
_SQL_DELETE_TOOL_BUILDING_COMPS = (
    "DELETE FROM sales_comps WHERE comp_type = 'building' AND " + _SQL_TOOL_ROW
    + " AND " + _SQL_IN_MARKET
)
_SQL_DELETE_TOOL_LEASE_COMPS = (
    "DELETE FROM leasing_comps WHERE " + _SQL_TOOL_ROW + " AND " + _SQL_IN_MARKET
)
# Building and lease rows delivered another way (a CSV): never stored twice.
_SQL_OTHER_BUILDING_KEYS = (
    "SELECT address, sale_date FROM sales_comps WHERE comp_type = 'building' "
    "AND NOT COALESCE(" + _SQL_TOOL_ROW + ", FALSE) AND " + _SQL_IN_MARKET
)
_SQL_OTHER_LEASE_KEYS = (
    "SELECT address, lease_start_date, building_sf_leased FROM leasing_comps "
    "WHERE NOT COALESCE(" + _SQL_TOOL_ROW + ", FALSE) AND " + _SQL_IN_MARKET
)
# Same text as research._SQL_RESOLVED_SUBMARKET (pinned equal by a test):
# reporting resolves a parcel's submarket for the building-comps lines from
# this protected copy, never from research.py at run time (BC-03).
_SQL_RESOLVED_SUBMARKET = (
    "COALESCE(("
    "SELECT s.submarket_id FROM submarkets s "
    "WHERE s.market_id = p.market AND s.bbox IS NOT NULL "
    "AND p.centroid IS NOT NULL "
    "AND s.bbox && ST_Expand(p.centroid, 0.05) "
    "AND ST_DWithin(s.bbox::geography, p.centroid::geography, 3000) "
    "ORDER BY ST_Distance(s.bbox::geography, p.centroid::geography), "
    "ST_Area(s.bbox::geography), s.submarket_id LIMIT 1"
    "), p.submarket)"
)


def _fold(name: Any) -> str:
    return str(name).strip().casefold()


def _folded_header(rows: list[list[Any]], markers: Iterable[Sequence[str]]
                   ) -> tuple[dict[str, int], int] | None:
    """({folded header: column}, header row index) for the first of the top
    HEADER_SEARCH_ROWS rows holding one alias of every marker group."""
    groups = [{_fold(a) for a in g} for g in markers]
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        hdr: dict[str, int] = {}
        for j, h in enumerate(row):
            if h is not None and _fold(h) not in hdr:
                hdr[_fold(h)] = j
        if all(g & hdr.keys() for g in groups):
            return hdr, i
    return None


def _columns(hdr: dict[str, int], spec: dict[str, tuple[str, ...]]) -> dict[str, int]:
    """{field: column} for the fields whose alias is present (first alias wins)."""
    out = {}
    for name, aliases in spec.items():
        for a in aliases:
            if _fold(a) in hdr:
                out[name] = hdr[_fold(a)]
                break
    return out


def _missing(cols: dict[str, int], spec: dict[str, tuple[str, ...]],
             required: Sequence[str]) -> list[str]:
    """The EXPECTED names of every required field that is absent -- never a
    cell of the sheet (BC-05)."""
    return [" or ".join(f"'{a}'" for a in spec[f]) for f in required if f not in cols]


def _lease_missing(cols: dict[str, int]) -> list[str]:
    miss = _missing(cols, LEASE_COLUMNS, LEASE_REQUIRED)
    if "starting_rent" not in cols:
        if "annual_rent" not in cols:
            miss.append(" or ".join(f"'{a}'" for a in LEASE_COLUMNS["starting_rent"]
                                    + LEASE_COLUMNS["annual_rent"]))
        elif "rent_type" not in cols:
            miss.append("'Rent Type' (needed with 'Rent/SF/Yr' to tell starting from asking rent)")
    return miss


@dataclass
class SheetRoles:
    stats: list[list[Any]] | None = None
    areas: list[list[Any]] | None = None
    land: list[list[Any]] | None = None
    building: list[list[Any]] | None = None
    lease: list[list[Any]] | None = None
    building_from_areas: bool = False
    # An area sheet that looks like building sales but lacks columns: the
    # expected names it lacks (areas are imported as before).
    areas_building_missing: list[str] = field(default_factory=list)

    def any(self) -> bool:
        return any(x is not None for x in (self.stats, self.areas, self.land,
                                           self.building, self.lease))


def sheet_role(rows: list[list[Any]]) -> tuple[str | None, list[str]]:
    """(role, missing building columns of an area sheet). Exactly one role:
    'stats', 'lease', 'areas', 'areas+building', 'building', 'land' or None.

    Raises ImportError_ for a sheet that looks like lease or building comps
    but lacks a required column (the message names only expected headers).
    """
    if find_header(rows, "Geography Name", "Concept Name"):
        return "stats", []
    lease = _folded_header(rows, [_LEASE_MARKERS])
    if lease:
        cols = _columns(lease[0], LEASE_COLUMNS)
        miss = _lease_missing(cols)
        if miss:
            raise ImportError_("a sheet looks like CoStar lease comps ('SF Leased') but lacks "
                               "the expected column(s) " + ", ".join(miss)
                               + "; add them in CoStar's column picker "
                               "(COSTAR_EXPORTS_README.md)")
        return "lease", []
    building = _folded_header(rows, [_BUILDING_MARKERS, ("Sale Price",)])
    miss = _missing(_columns(building[0], BUILDING_COLUMNS), BUILDING_COLUMNS,
                    BUILDING_REQUIRED) if building else []
    if find_header(rows, "Submarket Name", "Parcel Number 1 (Min)"):
        if building and not miss:
            return "areas+building", []
        return "areas", miss
    if is_land_sheet(rows):
        return "land", []
    if building:
        if miss:
            raise ImportError_("a sheet looks like CoStar building sale comps ('RBA' and "
                               "'Sale Price') but lacks the expected column(s) "
                               + ", ".join(miss) + "; add them in CoStar's column picker "
                               "(COSTAR_EXPORTS_README.md)")
        return "building", []
    return None, []


def classify_tables(tables: Sequence[list[list[Any]]]) -> SheetRoles:
    """Every sheet's one role; two sheets with the same role are refused
    rather than the first one taken (BC-01)."""
    roles = SheetRoles()
    seen: dict[str, int] = {}
    for rows in tables:
        role, miss = sheet_role(rows)
        if role is None:
            continue
        for r in role.split("+"):
            seen[r] = seen.get(r, 0) + 1
            if seen[r] > 1:
                raise ImportError_(f"two sheets match the {_ROLE_LABELS[r]} layout; keep one "
                                   "per workbook (import the other workbook separately)")
        if role == "areas+building":
            roles.areas = roles.building = rows
            roles.building_from_areas = True
        elif role == "areas":
            roles.areas = rows
            roles.areas_building_missing = miss
        else:
            setattr(roles, role, rows)
    return roles


_ROLE_LABELS = {"stats": "submarket stats", "areas": "area sale comps", "land": "land sales",
                "building": "building sale comps", "lease": "lease comps"}
EXPECTED_LAYOUTS = (
    "stats ('Geography Name', 'Concept Name'); area sale comps ('Submarket Name', "
    "'Parcel Number 1 (Min)'); land sales (" + ", ".join(f"'{h}'" for h in LAND_HEADERS)
    + "); building sale comps ('RBA', 'Sale Price', ...); lease comps ('SF Leased', ...)"
)


def cap_rate_fraction(value: Any) -> float | None:
    """CoStar's cap rate as a fraction: '5.50%' or 5.5 (percent, since above
    1) and the Excel fraction 0.055 all give 0.055. None when blank or not a
    number; the band check is the caller's."""
    v, pct = _num(value)
    if v is None:
        return None
    return round(v / 100.0 if (pct or v > 1) else v, 6)


def lease_term_months(text: Any, months: Any) -> int | None:
    """'5 Years 3 Months' -> 63, '63 Months' -> 63, '7.5 yrs' -> 90; a
    number in a months column -> that number. A bare number in a 'Lease
    Term' column could be years or months: None rather than a guess."""
    m = _finite(months)
    if m is not None:
        return int(round(m)) if 0 < m <= 600 else None
    if text is None or isinstance(text, (int, float)):
        return None
    s = str(text)
    y, mo = _TERM_YEARS_RE.search(s), _TERM_MONTHS_RE.search(s)
    if not y and not mo:
        return None
    total = round(float(y.group(1)) * 12) if y else 0
    total += int(mo.group(1)) if mo else 0
    return total if 0 < total <= 600 else None


@dataclass
class CompsResult:
    rows: list[dict[str, Any]]
    dropped: dict[str, int] = field(default_factory=dict)
    cap_dropped: dict[str, int] = field(default_factory=dict)   # sale kept, cap rate not


def _in_georgia(lat: float, lon: float) -> bool:
    return GEORGIA_BOX[0] <= lon <= GEORGIA_BOX[2] and GEORGIA_BOX[1] <= lat <= GEORGIA_BOX[3]


def _cell(row: list[Any], cols: dict[str, int], name: str) -> Any:
    i = cols.get(name)
    return row[i] if i is not None and i < len(row) else None


def convert_building(rows: list[list[Any]], *, today: date, date1904: bool = False) -> CompsResult:
    """Comparable industrial building sales (BC-07). Counts drops by reason."""
    found = _folded_header(rows, [_BUILDING_MARKERS, ("Sale Price",)])
    cols = _columns(found[0], BUILDING_COLUMNS) if found else {}
    miss = _missing(cols, BUILDING_COLUMNS, BUILDING_REQUIRED)
    if not found or miss:
        raise ImportError_("no building sale comps sheet (missing " + ", ".join(miss or ["'RBA'"])
                           + ")")
    start = found[1]
    oldest = months_before(today, BUILDING_SALE_WINDOW_MONTHS)
    min_year = today.year - BUILDING_MAX_AGE_YEARS
    out = CompsResult([])
    seen: set[tuple[str, date, int]] = set()

    def drop(reason: str) -> None:
        out.dropped[reason] = out.dropped.get(reason, 0) + 1

    get = lambda row, name: _cell(row, cols, name)  # noqa: E731
    for row in rows[start + 1:]:
        if not any(c not in (None, "") for c in row):
            continue
        if _fold(get(row, "property_type") or "") != "industrial":
            drop("not industrial"); continue
        secondary = _fold(get(row, "secondary_type") or "")
        if secondary not in BUILDING_SECONDARY_TYPES:
            drop("secondary type not warehouse or distribution"); continue
        if _fold(get(row, "sale_status") or "") != "sold":
            drop("not sold"); continue
        sold = _sale_date(get(row, "sale_date"), date1904)
        if sold is None:
            drop("no sale date"); continue
        if sold > today:
            drop("future sale date"); continue
        if sold < oldest:
            drop(f"older than {BUILDING_SALE_WINDOW_MONTHS} months"); continue
        condition = str(get(row, "sale_condition") or "")
        hit = next((label for label, rx in _SALE_CONDITION_RES if rx.search(condition)), None)
        if hit:
            drop(f"sale condition: {hit}"); continue
        address = str(get(row, "address") or "").strip()
        label = f"{address} {get(row, 'property_name') or ''}"
        n_props = _finite(get(row, "num_properties"))
        if _PORTFOLIO_RE.search(label) or (n_props is not None and n_props > 1):
            drop("part of a portfolio or multi-property sale"); continue
        sf = _finite(get(row, "building_sf"))
        if sf is None or sf <= 0:
            drop("no building SF"); continue
        if not BUILDING_MIN_SF <= sf <= BUILDING_MAX_SF:
            drop(f"outside {BUILDING_MIN_SF:,}-{BUILDING_MAX_SF:,} SF"); continue
        price = _finite(get(row, "sale_price"))
        if price is None or price <= 0:
            drop("no sale price"); continue
        if price > BUILDING_MAX_PRICE:
            drop("implausible price"); continue
        built = _finite(get(row, "year_built"))
        if built is None:
            drop("no year built"); continue
        if built < min_year or built > today.year + 1:
            drop(f"not built within {BUILDING_MAX_AGE_YEARS} years"); continue
        psf = price / sf
        costar_psf = _finite(get(row, "price_psf"))
        if costar_psf is not None and abs(costar_psf - psf) > PRICE_PSF_TOLERANCE * psf:
            drop("price per SF disagrees with price / building SF"); continue
        if not SALE_PSF_MIN <= psf <= SALE_PSF_MAX:
            drop(f"price per SF outside ${SALE_PSF_MIN:g}-${SALE_PSF_MAX:g}"); continue
        lat, lon = _finite(get(row, "latitude")), _finite(get(row, "longitude"))
        if lat is None or lon is None:
            drop("no location"); continue
        if not _in_georgia(lat, lon):
            drop("location outside Georgia"); continue
        key = (_address_key(address) or f"{lat:.5f},{lon:.5f}", sold, int(round(sf)))
        if key in seen:
            drop("duplicate address, date and size"); continue
        seen.add(key)
        cap = cap_rate_fraction(get(row, "cap_rate"))
        if cap is None:
            out.cap_dropped["cap rate blank"] = out.cap_dropped.get("cap rate blank", 0) + 1
        elif not CAP_RATE_MIN <= cap <= CAP_RATE_MAX:
            why = f"cap rate outside {CAP_RATE_MIN:.0%}-{CAP_RATE_MAX:.0%}"
            out.cap_dropped[why] = out.cap_dropped.get(why, 0) + 1
            cap = None
        clear = _finite(get(row, "clear_height_ft"))
        out.rows.append({
            "address": address or None,
            "building_sf": int(round(sf)),
            "sale_date": sold.isoformat(),
            "sale_price": int(round(price)),
            "price_psf": round(psf, 2),
            "cap_rate": cap,
            "year_built": int(built),
            "clear_height_ft": clear if clear is not None and 8 <= clear <= 80 else None,
            "secondary_type": secondary,
            "latitude": lat,
            "longitude": lon,
        })
    return out


def convert_lease(rows: list[list[Any]], *, today: date, date1904: bool = False) -> CompsResult:
    """Signed NNN starting rents of comparable industrial leases (BC-06,
    BC-19). Counts drops by reason."""
    found = _folded_header(rows, [_LEASE_MARKERS])
    cols = _columns(found[0], LEASE_COLUMNS) if found else {}
    miss = _lease_missing(cols)
    if not found or miss:
        raise ImportError_("no lease comps sheet (missing " + ", ".join(miss or ["'SF Leased'"])
                           + ")")
    start = found[1]
    rent_col = "starting_rent" if "starting_rent" in cols else "annual_rent"
    oldest = months_before(today, LEASE_WINDOW_MONTHS)
    out = CompsResult([])
    seen: set[tuple[str, date, int]] = set()

    def drop(reason: str) -> None:
        out.dropped[reason] = out.dropped.get(reason, 0) + 1

    get = lambda row, name: _cell(row, cols, name)  # noqa: E731
    for row in rows[start + 1:]:
        if not any(c not in (None, "") for c in row):
            continue
        if _fold(get(row, "property_type") or "") != "industrial":
            drop("not industrial"); continue
        if _fold(get(row, "secondary_type") or "") not in BUILDING_SECONDARY_TYPES:
            drop("secondary type not warehouse or distribution"); continue
        kinds = f"{get(row, 'lease_type') or ''} {get(row, 'deal_type') or ''}"
        if _SUBLEASE_RE.search(kinds):
            drop("sublease"); continue
        if _RENEWAL_RE.search(kinds):
            drop("renewal or extension"); continue
        signed = _sale_date(get(row, "sign_date"), date1904)
        if signed is None:
            drop("no sign date"); continue
        if signed > today:
            drop("future sign date"); continue
        if signed < oldest:
            drop(f"signed more than {LEASE_WINDOW_MONTHS} months ago"); continue
        sf = _finite(get(row, "sf_leased"))
        if sf is None or sf <= 0:
            drop("no SF leased"); continue
        if not BUILDING_MIN_SF <= sf <= BUILDING_MAX_SF:
            drop(f"outside {BUILDING_MIN_SF:,}-{BUILDING_MAX_SF:,} SF leased"); continue
        if not _NNN_RE.match(str(get(row, "service_type") or "")):
            drop("service type not NNN"); continue
        rent_type = _fold(get(row, "rent_type") or "")
        rent = _finite(get(row, rent_col))
        if rent_col == "annual_rent" and not rent_type:
            drop("rent type not stated"); continue
        if rent_type and rent_type != "starting":
            drop("asking rent only" if rent_type == "asking" else "not a starting rent"); continue
        if rent is None:
            asking = _finite(get(row, "asking_rent"))
            drop("asking rent only" if asking is not None else "rent withheld or not disclosed")
            continue
        if not NNN_RENT_MIN <= rent <= NNN_RENT_MAX:
            drop(f"rent outside ${NNN_RENT_MIN:g}-${NNN_RENT_MAX:g}/SF/yr (monthly or implausible)")
            continue
        lat, lon = _finite(get(row, "latitude")), _finite(get(row, "longitude"))
        if lat is None or lon is None:
            drop("no location"); continue
        if not _in_georgia(lat, lon):
            drop("location outside Georgia"); continue
        address = str(get(row, "address") or "").strip()
        key = (_address_key(address) or f"{lat:.5f},{lon:.5f}", signed, int(round(sf)))
        if key in seen:
            drop("duplicate address, sign date and SF"); continue
        seen.add(key)
        started = _sale_date(get(row, "start_date"), date1904)
        deal = next((d for d in _DEAL_TYPES if d in _fold(kinds)), None)
        rba, built = _finite(get(row, "building_rba")), _finite(get(row, "year_built"))
        out.rows.append({
            "address": address or None,
            "sf_leased": int(round(sf)),
            "sign_date": signed.isoformat(),
            "start_date": started.isoformat() if started else None,
            "term_months": lease_term_months(get(row, "term_text"), get(row, "term_months")),
            "rent_psf_nnn": round(rent, 4),
            "deal_type": deal,
            "building_rba": int(round(rba)) if rba is not None and rba > 0 else None,
            "year_built": int(built) if built is not None and 1800 < built < 2200 else None,
            "latitude": lat,
            "longitude": lon,
        })
    return out


def refilter_building(rows: list[Any], today: date) -> tuple[list[dict[str, Any]], int]:
    """Saved sales replayed later must still pass today's size, age and band rules."""
    oldest = months_before(today, BUILDING_SALE_WINDOW_MONTHS).isoformat()
    keep = []
    for r in rows:
        try:
            ok = (BUILDING_MIN_SF <= r["building_sf"] <= BUILDING_MAX_SF
                  and oldest <= str(r["sale_date"]) <= today.isoformat()
                  and r["year_built"] >= today.year - BUILDING_MAX_AGE_YEARS
                  and SALE_PSF_MIN <= r["price_psf"] <= SALE_PSF_MAX
                  and _in_georgia(float(r["latitude"]), float(r["longitude"])))
        except (TypeError, KeyError, ValueError):
            ok = False
        if ok:
            cap = r.get("cap_rate")
            if not (isinstance(cap, (int, float)) and CAP_RATE_MIN <= cap <= CAP_RATE_MAX):
                r = {**r, "cap_rate": None}
            keep.append(r)
    return keep, len(rows) - len(keep)


def refilter_lease(rows: list[Any], today: date) -> tuple[list[dict[str, Any]], int]:
    oldest = months_before(today, LEASE_WINDOW_MONTHS).isoformat()
    keep = []
    for r in rows:
        try:
            ok = (BUILDING_MIN_SF <= r["sf_leased"] <= BUILDING_MAX_SF
                  and oldest <= str(r["sign_date"]) <= today.isoformat()
                  and NNN_RENT_MIN <= r["rent_psf_nnn"] <= NNN_RENT_MAX
                  and _in_georgia(float(r["latitude"]), float(r["longitude"])))
        except (TypeError, KeyError, ValueError):
            ok = False
        if ok:
            keep.append(r)
    return keep, len(rows) - len(keep)


def save_comps_snapshot(name: str, rows: list[dict[str, Any]], base: Path, today: date) -> Path:
    """Atomic write under gitignored costar_exports/ (licensed data, SR-7).
    The rows hold only the allowlisted fields convert_* produced."""
    base.mkdir(parents=True, exist_ok=True)
    dest = base / name
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=base)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "saved_on": today.isoformat(), "rows": rows}, fh)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def load_comps_snapshot(name: str, base: Path, label: str) -> list[dict[str, Any]] | None:
    path = base / name
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["rows"] if data.get("version") == 1 else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        rows = None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ImportError_(f"the saved {label} snapshot is unreadable; re-import that export")
    return rows


def _building_params(r: dict[str, Any], submarket_id: str, today: date) -> tuple:
    raw = {"import_origin": BUILDING_ORIGIN, "imported_on": today.isoformat(),
           "latitude": r["latitude"], "longitude": r["longitude"],
           "year_built": r.get("year_built"), "clear_height_ft": r.get("clear_height_ft"),
           "secondary_type": r.get("secondary_type"), "cap_rate_convention": "fraction"}
    return costar_ingest._building_sales_insert_params(
        {"address": r.get("address"), "building_sf": r["building_sf"],
         "sale_date": r["sale_date"], "sale_price": r["sale_price"],
         "price_psf": r["price_psf"], "cap_rate": r.get("cap_rate"),
         "buyer_name": None, "seller_name": None, "raw": raw}, submarket_id)


def _lease_params(r: dict[str, Any], submarket_id: str, today: date) -> tuple:
    raw = {"import_origin": LEASE_ORIGIN, "imported_on": today.isoformat(),
           "latitude": r["latitude"], "longitude": r["longitude"],
           "sign_date": r["sign_date"], "service_type": "NNN", "rent_basis": "starting",
           "deal_type": r.get("deal_type"), "building_rba": r.get("building_rba"),
           "year_built": r.get("year_built")}
    return costar_ingest._leasing_insert_params(
        {"address": r.get("address"), "tenant_name": None, "tenant_industry": None,
         "naics_code": None, "lease_start_date": r.get("start_date"),
         "lease_term_months": r.get("term_months"), "building_sf_leased": r["sf_leased"],
         "starting_rent_psf_nnn": r["rent_psf_nnn"], "rent_escalation_pct": None,
         "lease_type": "NNN", "raw": raw}, submarket_id)


@dataclass(frozen=True)
class _CompsKind:
    label: str
    origin: str
    other_keys_sql: str
    delete_sql: str
    insert_sql: str
    params: Any                                    # (row, submarket_id, today) -> tuple
    key: Any                                       # row -> cross-origin key
    other_key: Any                                 # fetched tuple -> cross-origin key


def _num_key(v: Any) -> int | None:
    f = _finite(v)
    return int(round(f)) if f is not None else None


BUILDING_KIND = _CompsKind(
    "building sales", BUILDING_ORIGIN, _SQL_OTHER_BUILDING_KEYS,
    _SQL_DELETE_TOOL_BUILDING_COMPS, costar_ingest._SQL_INSERT_BUILDING_SALES, _building_params,
    lambda r: (_address_key(r.get("address")), str(r["sale_date"])),
    lambda t: (_address_key(t[0]), str(t[1])))
LEASE_KIND = _CompsKind(
    "lease comps", LEASE_ORIGIN, _SQL_OTHER_LEASE_KEYS,
    _SQL_DELETE_TOOL_LEASE_COMPS, costar_ingest._SQL_INSERT_LEASING_COMP, _lease_params,
    lambda r: (_address_key(r.get("address")), str(r.get("start_date")), _num_key(r["sf_leased"])),
    lambda t: (_address_key(t[0]), str(t[1]), _num_key(t[2])))


def write_comps(conn: Any, kind: _CompsKind, rows: list[dict[str, Any]], today: date,
                market_name: str = MARKET_NAME) -> dict[str, int]:
    """Replace this tool's building or lease rows in ONE transaction. Counts only.

    Mirrors write_land: each comp goes to the area a parcel at the same
    point resolves to (nearest within 3 km); no areas at all -> nothing
    touched ('deferred'); nothing assignable -> the earlier rows are kept
    ('kept_previous'); comps another origin already delivered are skipped.
    Only rows matching _SQL_TOOL_ROW with this kind's origin are deleted.
    """
    market_id = costar_ingest._slugify(market_name)
    counts = {"deferred": 0, "assigned": 0, "no_area": 0, "submarkets": 0, "replaced": 0,
              "already_delivered": 0, "kept_previous": 0}
    if not rows:
        raise ImportError_(f"no {kind.label} qualify; left unchanged")
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
            cur.execute(kind.other_keys_sql, (kind.origin, market_id))
            delivered = {kind.other_key(t) for t in cur.fetchall()}
        todo: list[tuple[dict[str, Any], str]] = []
        for ordinal, submarket_id in assigned:
            r = rows[int(ordinal) - 1]
            if submarket_id is None:
                counts["no_area"] += 1
            elif kind.key(r) in delivered:
                counts["already_delivered"] += 1
            else:
                todo.append((r, submarket_id))
        if not todo:
            counts["kept_previous"] = 1
            return counts
        with conn.cursor() as cur:
            cur.execute(kind.delete_sql, (kind.origin, market_id))
            counts["replaced"] = max(int(getattr(cur, "rowcount", 0) or 0), 0)
        subs: set[str] = set()
        for r, submarket_id in todo:
            with conn.cursor() as cur:
                cur.execute(kind.insert_sql, kind.params(r, submarket_id, today))
            counts["assigned"] += 1
            subs.add(submarket_id)
        counts["submarkets"] = len(subs)
    return counts


def _drops_text(res: CompsResult) -> str:
    return ", ".join(f"{n} {why}" for why, n in sorted(res.dropped.items())) or "none"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _comps_line(label: str, c: dict[str, int], what: str) -> str:
    if c["deferred"]:
        return (f"{label}: saved; no submarket areas exist yet, so the {what} are assigned "
                "when a workbook with sale comps is imported.")
    if c["kept_previous"]:
        return (f"{label}: no {what[:-1]} lies within 3 km of an area ({c['no_area']}) or each "
                f"was already delivered ({c['already_delivered']}); the earlier {label} comps "
                "are kept unchanged.")
    return (f"{label}: {c['assigned']} {what} in {c['submarkets']} submarket areas "
            f"({c['no_area']} farther than 3 km from every area; {c['already_delivered']} "
            f"already delivered by a CSV; {c['replaced']} earlier tool-imported rows replaced)")


def _replay(name: str, label: str, base: Path, today: date, refilter: Any
            ) -> list[dict[str, Any]] | None:
    """Saved comps assigned again after the areas are redrawn (BC-10). An
    unreadable snapshot never blocks the area import."""
    try:
        rows = load_comps_snapshot(name, base, label)
    except ImportError_ as exc:
        print(f"{label}: {exc}; the areas are imported without it.")
        return None
    if not rows:
        return None
    rows, n_old = refilter(rows, today)
    if n_old:
        print(f"{label}: {n_old} saved comps no longer pass the size, age or band rules "
              "and are left out.")
    if not rows:
        print(f"{label}: no saved comp still qualifies; left as they are.")
    return rows


def run(path: Path, *, areas: bool = True, locator: Any = None,
        base: Path = _COSTAR_BASE_DIR, today: date | None = None) -> int:
    today = today or date.today()
    if run_has_baseline():
        # Every sheet type, building and lease included (BC-17): memo-only
        # data still changes memos between experiments.
        print("REFUSED: this run already has its baseline. Importing now would change "
              "what later experiments score. Start a new run first, then import before "
              "its baseline (Start New Run on the home machine).")
        return 3
    tables = read_tables(path)
    roles = classify_tables(tables)
    stats_rows, comps_rows, land_sheet = roles.stats, roles.areas, roles.land
    if not roles.any():
        print("REFUSED: no CoStar sheet found in this file. Expected one of: "
              + EXPECTED_LAYOUTS + ".")
        return 1
    date1904 = xlsx_date1904(path)

    land: LandResult | None = None
    if land_sheet is not None:
        land = convert_land(land_sheet, today=today, date1904=date1904)
        print(f"land: {len(land.rows)} industrial land sales kept; dropped "
              + (", ".join(f"{n} {why}" for why, n in sorted(land.dropped.items())) or "none"))
        if not land.rows:
            raise ImportError_("no industrial land sale qualifies; land comps left unchanged")

    building: CompsResult | None = None
    if roles.building is not None:
        if roles.building_from_areas:
            print("building: the area sale-comps sheet also carries every building sale "
                  "column; its areas are drawn first (as before), then its sales are "
                  "imported as building comps (memo only, no scoring input).")
        building = convert_building(roles.building, today=today, date1904=date1904)
        n_cap = sum(1 for r in building.rows if r["cap_rate"] is not None)
        print(f"building: {len(building.rows)} comparable sales kept ({n_cap} with a cap rate); "
              f"dropped {_drops_text(building)}; cap rates left out: "
              + (", ".join(f"{n} {w}" for w, n in sorted(building.cap_dropped.items())) or "none"))
    elif roles.areas_building_missing:
        print("building: the area sale-comps sheet lacks " + ", ".join(roles.areas_building_missing)
              + "; no building comps are taken from it (its areas are imported as before).")
    lease: CompsResult | None = None
    if roles.lease is not None:
        lease = convert_lease(roles.lease, today=today, date1904=date1904)
        print(f"lease: {len(lease.rows)} signed NNN leases kept; dropped {_drops_text(lease)}")

    if stats_rows is not None:
        stats = convert_stats(stats_rows)
        write_stats_csv(stats, base)
        print(f"stats: {len(stats.rows)} submarkets from {stats.quarter} "
              f"({stats.skipped_clusters} clusters skipped) -> costar_exports/submarket_stats/")
    if not areas:
        if land is not None:
            print("land: not written (--no-areas: no database writes).")
        if building is not None or lease is not None:
            print("building and lease comps: not written (--no-areas: no database writes).")
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
    if plan is None and land is None and building is None and lease is None:
        return 0
    # Saved comps go again when the areas move (R-3, BC-10). Read BEFORE the
    # areas are written: an unreadable snapshot must not block every Start.
    land_rows: list[dict[str, Any]] | None = land.rows if land is not None else None
    if land_rows is None and plan is not None:
        try:
            land_rows = load_land_snapshot(base)
        except ImportError_ as exc:
            print(f"land: {exc}; the areas are imported without it.")
        if land_rows:
            land_rows, n_old = refilter_snapshot(land_rows, today)
            if n_old:
                print(f"land: {n_old} saved sales no longer pass the size or "
                      f"{LAND_MAX_AGE_MONTHS}-month rule and are left out.")
            if not land_rows:
                print("land: no saved sale still qualifies; the land comps are left as they are. "
                      "Import a new Land export.")
    building_rows = building.rows if building is not None else (
        _replay(BUILDING_SNAPSHOT_NAME, "building", base, today, refilter_building)
        if plan is not None else None)
    lease_rows = lease.rows if lease is not None else (
        _replay(LEASE_SNAPSHOT_NAME, "lease", base, today, refilter_lease)
        if plan is not None else None)
    if building is not None and not building.rows:
        print("building: no sale qualifies; the building comps are left unchanged.")
    if lease is not None and not lease.rows:
        print("lease: no lease qualifies; the lease comps are left unchanged.")

    if not prepare.dsn_available():
        print("areas and comps not written: no DATABASE_URL"
              + (" (the stats CSV was written)." if stats_rows is not None else "."))
        return 2
    done: list[str] = []
    try:
        with prepare.get_connection() as conn:
            if plan is not None:
                counts = write_areas(conn, plan, today)
                conn.commit()
                done.append("areas")
                print(f"areas: wrote {counts['written']}, kept {counts['kept_human_seeded']} "
                      f"human-seeded, cleared {counts['cleared_stale']} stale"
                      + (f", {counts['name_drift']} name drifts (see submarkets table)"
                         if counts["name_drift"] else ""))
            if land_rows:
                lc = write_land(conn, land_rows, today)
                conn.commit()
                done.append("land comps")
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
            for kind, rows_, result, snap, label, what in (
                    (BUILDING_KIND, building_rows, building, BUILDING_SNAPSHOT_NAME,
                     "building", "sales"),
                    (LEASE_KIND, lease_rows, lease, LEASE_SNAPSHOT_NAME, "lease", "leases")):
                if not rows_:
                    continue
                c = write_comps(conn, kind, rows_, today)
                conn.commit()
                done.append(f"{label} comps")
                if result is not None:        # only once written or deferred
                    save_comps_snapshot(snap, result.rows, base, today)
                print(_comps_line(label, c, what))
    except psycopg.DataError as exc:
        print(f"REFUSED: a value in the workbook does not fit the database ({type(exc).__name__}); "
              "nothing more was written"
              + (f" (committed before it: {', '.join(done)})." if done else "."))
        return 1
    except (psycopg.Error, OSError) as exc:
        print(f"areas or comps not written: database error ({type(exc).__name__})."
              + (f" Committed before it: {', '.join(done)}." if done else "")
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
