"""Import a CoStar Excel export: submarket stats CSV + submarket areas.

Operator tool, run between runs (never by the agent, never mid-run):

    python costar_import.py path/to/ATL_export.xlsx [--no-areas]
    make costar-import FILE=path/to/ATL_export.xlsx

home_machine/autopilot.sh runs it before a NEW run's baseline, so a run's
baseline and all its experiments see the same CoStar data
(reviews/19_costar_import, risk 2). Immutable during a run, like
costar_ingest.py.

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
   gets submarkets.bbox = a 300 m buffer around their convex hull (a comp with
   no other comp of its submarket within OUTLIER_KM is dropped). research.py assigns
   each parcel to the nearest area at read time; nothing is stored on
   parcels.

Areas are written in ONE transaction, only over areas this tool wrote
before (notes start with AREA_NOTE_PREFIX) or empty ones: a human-seeded
bbox is never overwritten. Without a database the CSV is still written and
the tool exits 2 ("areas not written").

CoStar data is licensed: everything lands under costar_exports/
(gitignored); the console shows counts only.

Depends only on prepare, costar_ingest and pipeline_common -- never on
research.py, the agent's sandbox.
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
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

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
MAX_XLSX_UNCOMPRESSED = 200 * 1024 * 1024
FULTON_ENVELOPE = (-84.95, 33.35, -84.0, 34.25)   # loose lon/lat sanity box
LOCATION_CACHE = _COSTAR_BASE_DIR / ".comp_parcel_locations.json"

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
FULTON_ID_RE = re.compile(r"^(\d{2}[A-Z]?)(\d{4})([A-Z0-9]{3,12})$")

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


class ImportError_(Exception):
    """A workbook or sheet this tool refuses (message is operator-facing)."""


# ---------------------------------------------------------------------------
# Reading .xlsx with the standard library (SR-10: no new dependency)
# ---------------------------------------------------------------------------
_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(t.text or "" for t in el.iter(f"{{{_NS['m']}}}t"))


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

        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in root.findall("m:si", _NS)]

        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target", "") for r in rels_root.findall("rel:Relationship", _NS)}
        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))

        sheets: dict[str, list[list[Any]]] = {}
        for sheet in wb_root.findall("m:sheets/m:sheet", _NS):
            target = targets.get(sheet.get(f"{{{_NS['r']}}}id"), "")
            target = target.lstrip("/")
            member = target if target.startswith("xl/") else f"xl/{target}"
            if member not in names:
                continue
            sheets[sheet.get("name", "")] = _read_sheet(zf.read(member), shared)
        return sheets


def _read_sheet(xml: bytes, shared: Sequence[str]) -> list[list[Any]]:
    rows: dict[int, dict[int, Any]] = {}
    for row in ET.fromstring(xml).iter(f"{{{_NS['m']}}}row"):
        for c in row.findall("m:c", _NS):
            m = _CELL_REF_RE.match(c.get("r", ""))
            if not m:
                continue
            col, r = _col_index(m.group(1)), int(m.group(2))
            kind = c.get("t", "n")
            v = c.find("m:v", _NS)
            if kind == "s" and v is not None:
                value: Any = shared[int(v.text)]
            elif kind == "inlineStr":
                value = _text(c.find("m:is", _NS))
            elif kind in ("str", "e"):
                value = v.text if v is not None else None
            elif kind == "b":
                value = v is not None and v.text == "1"
            elif v is not None and v.text not in (None, ""):
                value = float(v.text)
            else:
                value = None
            rows.setdefault(r, {})[col] = value
    out = []
    for r in sorted(rows):
        cells = rows[r]
        width = max(cells) + 1
        out.append([cells.get(i) for i in range(width)])
    return out


def read_tables(path: Path) -> list[list[list[Any]]]:
    """Every table in the file: each sheet of an .xlsx, or one .csv."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return [[[c if c != "" else None for c in row] for row in csv.reader(fh)]]
    return list(read_xlsx(path).values())


def _header_index(rows: list[list[Any]]) -> dict[str, int] | None:
    if not rows:
        return None
    return {str(h).strip(): i for i, h in enumerate(rows[0]) if h is not None}


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


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def convert_stats(rows: list[list[Any]]) -> StatsResult:
    hdr = _header_index(rows)
    if not hdr or "Geography Name" not in hdr or "Concept Name" not in hdr:
        raise ImportError_("no stats sheet (needs 'Geography Name' and 'Concept Name' columns)")
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
    clusters: set[str] = set()
    for row in rows[1:]:
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
            raise ImportError_(f"stats sheet repeats '{concept}' for one submarket")
        sub[column] = _num(cell(qcol))

    if not values:
        raise ImportError_("stats sheet has no Atlanta industrial submarket rows")

    # A rate column holds fractions when every value is <= 1 (CoStar's
    # 0.0867 for 8.67%); otherwise it already is a percent.
    for concept, (column, is_rate) in CONCEPTS.items():
        if not is_rate:
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
    hdr = _header_index(rows)
    if not hdr or "Submarket Name" not in hdr:
        return {}
    cols = [hdr[c] for c in ("Parcel Number 1 (Min)", "Parcel Number 2 (Max)") if c in hdr]
    out: dict[str, set[str]] = {}
    for row in rows[1:]:
        sub = row[hdr["Submarket Name"]] if hdr["Submarket Name"] < len(row) else None
        name = str(sub or "").strip()
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
                if "error" not in body:
                    return body
            except (requests.RequestException, ValueError):
                pass
            self.sleep(2 ** attempt)
        raise ImportError_("the Fulton parcel service did not answer; try again later")

    def _fetch_land_lot(self, district: str, land_lot: str) -> dict[str, list[float]]:
        # district/land_lot come from FULTON_ID_RE (alphanumerics only).
        where = f"ParcelID LIKE '{district}%{land_lot}%'"
        found: dict[str, list[list[float]]] = {}
        offset = 0
        while True:
            body = self._get({
                "where": where, "outFields": "ParcelID", "returnGeometry": "true",
                "outSR": 4326, "f": "json", "resultOffset": offset,
                "resultRecordCount": 2000,
            })
            feats = body.get("features") or []
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
            for pid, pts in self._fetch_land_lot(district, land_lot).items():
                # Exactly one polygon per id, inside Fulton (outSR honoured).
                ok = (len(pts) == 1
                      and FULTON_ENVELOPE[0] <= pts[0][0] <= FULTON_ENVELOPE[2]
                      and FULTON_ENVELOPE[1] <= pts[0][1] <= FULTON_ENVELOPE[3])
                self.cache[f"id:{pid}"] = pts[0] if ok else None
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
    too_few: dict[str, int] = field(default_factory=dict)


def plan_areas(parcels: dict[str, set[str]], located: dict[str, tuple[float, float]]) -> AreaPlan:
    plan = AreaPlan()
    for name, ids in sorted(parcels.items()):
        pts = trim_points([located[i] for i in sorted(ids) if i in located])
        if len(pts) >= MIN_POINTS:
            plan.points[name] = pts
        elif pts:
            plan.too_few[name] = len(pts)
    return plan


def write_areas(conn: Any, plan: AreaPlan, today: date) -> dict[str, Any]:
    """All areas in one transaction (SR-5); never over a human-seeded bbox."""
    written: dict[str, float] = {}
    kept_human: list[str] = []
    with conn.transaction():
        for name, pts in sorted(plan.points.items()):
            sub_id, _created, _drift = costar_ingest._ensure_submarket(conn, MARKET_NAME, name)
            with conn.cursor() as cur:
                cur.execute(_SQL_FETCH_SUBMARKET_AREA_NOTE, (sub_id,))
                has_bbox, notes = cur.fetchone() or (False, None)
            if has_bbox and not str(notes or "").startswith(AREA_NOTE_PREFIX):
                kept_human.append(name)
                continue
            with conn.cursor() as cur:
                cur.execute(_SQL_AREA_FROM_POINTS,
                            (AREA_BUFFER_M, [p[0] for p in pts], [p[1] for p in pts]))
                ewkt, gtype, valid, srid, area_m2 = cur.fetchone()
            if gtype != "POLYGON" or not valid or srid != 4326:
                raise ImportError_(f"area for {name!r} is not a valid polygon ({gtype})")
            note = f"{AREA_NOTE_PREFIX} {today.isoformat()} from {len(pts)} located comps"
            with conn.cursor() as cur:
                cur.execute(_SQL_UPDATE_SUBMARKET_AREA, (ewkt, note, sub_id))
            written[name] = round(float(area_m2) / 1e6, 1)
    return {"written_km2": written, "kept_human_seeded": kept_human}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run(path: Path, *, areas: bool = True, locator: Any = None,
        base: Path = _COSTAR_BASE_DIR, today: date | None = None) -> int:
    today = today or date.today()
    tables = read_tables(path)
    stats_rows = next((t for t in tables if (_header_index(t) or {}).keys() >= {"Geography Name", "Concept Name"}), None)
    comps_rows = next((t for t in tables if "Submarket Name" in (_header_index(t) or {})), None)
    if stats_rows is None and comps_rows is None:
        print("REFUSED: no CoStar stats or sale-comps sheet found in this file.")
        return 1

    if stats_rows is not None:
        stats = convert_stats(stats_rows)
        dest = write_stats_csv(stats, base)
        print(f"stats: {len(stats.rows)} submarkets from {stats.quarter} "
              f"(clusters skipped: {stats.skipped_clusters}) -> "
              f"{dest.relative_to(base.parent) if base.parent in dest.parents else dest}")
    if comps_rows is None or not areas:
        return 0

    parcels = comp_parcels(comps_rows)
    n_ids = sum(len(v) for v in parcels.values())
    print(f"areas: {n_ids} Fulton-format comp parcel numbers in {len(parcels)} submarkets")
    locator = locator or FultonLocator(cache_path=base / LOCATION_CACHE.name)
    located = locator.locate(i for ids in parcels.values() for i in ids)
    plan = plan_areas(parcels, located)
    print(f"areas: located {len(located)}; {len(plan.points)} submarkets have "
          f"{MIN_POINTS}+ located comps; {len(plan.too_few)} have fewer (no area)")

    if not prepare.dsn_available():
        print("areas not written: no DATABASE_URL (the stats CSV was written).")
        return 2
    try:
        with prepare.get_connection() as conn:
            result = write_areas(conn, plan, today)
            conn.commit()
    except ImportError_:
        raise
    except Exception as exc:                      # DB unreachable etc.
        print(f"areas not written: database error ({type(exc).__name__}). "
              "The stats CSV was written.")
        return 2
    for name, km2 in sorted(result["written_km2"].items()):
        print(f"  area {name}: {km2} km2")
    for name in result["kept_human_seeded"]:
        print(f"  kept the human-seeded area of {name}")
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
    except ImportError_ as exc:
        print(f"REFUSED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
