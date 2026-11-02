"""Offline tests for costar_import.py (reviews/19_costar_import).

Hermetic (SR-6): synthetic workbooks are built in a temp dir (*.xlsx is
gitignored, and real CoStar rows are licensed); the Fulton service and the
database are fakes.
"""

from __future__ import annotations

import ast
import contextlib
import csv
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

import costar_import as ci
import costar_ingest
import research

REPO_ROOT = Path(__file__).resolve().parent.parent

STATS_HEADER = ["Property Class Name", "As Of", "Geography Name", "Concept Name",
                "2026 Q2", "2026 Q3", "2026 Q4 QTD", "2026 Q4 EST"]
P = "Atlanta - GA USA - "


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def make_xlsx(path: Path, sheets: dict[str, list[list[object]]]) -> Path:
    """Minimal workbook: shared strings for text, numbers inline."""
    shared: list[str] = []

    def sid(text: str) -> int:
        if text not in shared:
            shared.append(text)
        return shared.index(text)

    sheet_xml = []
    for rows in sheets.values():
        body = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, v in enumerate(row):
                ref = f"{_col(c)}{r}"
                if v is None:
                    continue
                if isinstance(v, str):
                    cells.append(f'<c r="{ref}" t="s"><v>{sid(v)}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            body.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet_xml.append(
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' \
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    wb = f'<workbook {ns}><sheets>' + "".join(
        f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, n in enumerate(sheets)) + "</sheets></workbook>"
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{i + 1}.xml" '
                      'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
                      for i in range(len(sheets))) + "</Relationships>")
    sst = ('<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", sst)
        for i, x in enumerate(sheet_xml):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", x)
    return path


def stats_rows() -> list[list[object]]:
    rows = [STATS_HEADER]
    for geo in ("Alpha Park", "Beta Yard ", "Gamma (Cluster)"):
        rows += [
            ["Industrial", "2026 Q4", P + geo, "Inventory SF", 100, 1000.4, 1100, 1200],
            ["Industrial", "2026 Q4", P + geo, "Vacancy Rate", 0.05, 0.0867, 0.09, 0.5],
            ["Industrial", "2026 Q4", P + geo, "Vacant Available %", 0.04, 0.07, 0.08, None],
            ["Industrial", "2026 Q4", P + geo, "Net Absorption SF 12 Mo", 1, -250.6, 3, 4],
            ["Industrial", "2026 Q4", P + geo, "Under Construction SF", 1, 300, 3, None],
            ["Industrial", "2026 Q4", P + geo, "All Service Type Rent Overall", 7, 8.25, 9, 10],
        ]
    rows.append(["Industrial", "2026 Q4", "Atlanta - GA USA", "Inventory SF", 1, 2, 3, 4])
    rows.append(["Office", "2026 Q4", P + "Alpha Park", "Inventory SF", 9, 9, 9, 9])
    return rows


class StatsConversionTests(unittest.TestCase):
    def test_uses_last_full_quarter_and_converts_fractions(self):
        res = ci.convert_stats(stats_rows())
        self.assertEqual(res.quarter, "2026 Q3")             # never QTD or EST
        self.assertEqual(res.report_date, date(2026, 9, 30))
        self.assertEqual([r["submarket_name"] for r in res.rows], ["Alpha Park", "Beta Yard"])
        self.assertEqual(res.skipped_clusters, 1)
        alpha = res.rows[0]
        self.assertEqual(alpha["vacancy_rate_pct"], "8.67")
        self.assertEqual(alpha["availability_rate_pct"], "7")
        self.assertEqual(alpha["total_inventory_sf"], "1000")
        self.assertEqual(alpha["net_absorption_t12_sf"], "-251")
        self.assertEqual(alpha["asking_rent_nnn_psf"], "8.25")
        self.assertEqual(alpha["proposed_sf"], "")
        self.assertEqual(alpha["market"], "Atlanta")

    def test_output_passes_the_real_loader_validation(self):
        with tempfile.TemporaryDirectory() as d:
            dest = ci.write_stats_csv(ci.convert_stats(stats_rows()), Path(d))
            self.assertEqual(dest.name, "submarket_stats_20260930.csv")
            self.assertEqual(sorted(p.name for p in dest.parent.iterdir()), [dest.name])
            headers, rows = costar_ingest._read_csv_with_bom(dest)
        self.assertIsNone(costar_ingest._validate_submarket_stats_headers(headers))
        for row in rows:
            parsed, err = costar_ingest._validate_submarket_stats_row(row)
            self.assertIsNone(err)
            self.assertEqual(str(parsed["report_date"]), "2026-09-30")

    def test_percent_valued_rates_are_left_alone(self):
        rows = stats_rows()
        for r in rows[1:]:
            if r[3] == "Vacancy Rate":
                r[5] = 8.67
        self.assertEqual(ci.convert_stats(rows).rows[0]["vacancy_rate_pct"], "8.67")

    def test_duplicate_concept_for_a_submarket_is_refused(self):
        rows = stats_rows()
        rows.append(["Industrial", "2026 Q4", P + "Alpha Park", "Vacancy Rate", 0, 0.1, 0, 0])
        with self.assertRaises(ci.ImportError_):
            ci.convert_stats(rows)

    def test_no_full_quarter_column_is_refused(self):
        rows = [STATS_HEADER[:4] + ["2026 Q4 QTD"],
                ["Industrial", "x", P + "A", "Inventory SF", 1]]
        with self.assertRaises(ci.ImportError_):
            ci.convert_stats(rows)

    def test_reads_sheets_from_a_real_xlsx_container(self):
        comps = [["Property Address", "Submarket Name", "Parcel Number 1 (Min)"],
                 ["1 Test St", "Alpha Park", "99Z-0001-LL-001-1"]]
        with tempfile.TemporaryDirectory() as d:
            path = make_xlsx(Path(d) / "x.xlsx", {"stats": stats_rows(), "comps": comps})
            sheets = ci.read_xlsx(path)
        self.assertEqual(list(sheets), ["stats", "comps"])
        self.assertEqual(sheets["stats"][2][5], 0.0867)
        self.assertEqual(sheets["comps"][1][2], "99Z-0001-LL-001-1")

    def test_non_xlsx_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.xlsx"
            p.write_text("not a zip")
            with self.assertRaises(ci.ImportError_):
                ci.read_xlsx(p)


class ParcelAndAreaTests(unittest.TestCase):
    def test_parcel_number_normalization(self):
        self.assertEqual(ci.normalize_parcel_id("14F-0134-LL-019-5"), "14F0134LL0195")
        self.assertEqual(ci.normalize_parcel_id("14-0106-0011-013-4"), "14010600110134")
        for other in ("017-113", "V03-0120016", "N57-A-041", None, "", "14F' OR 1=1"):
            self.assertIsNone(ci.normalize_parcel_id(other), other)

    def test_comp_parcels_groups_by_submarket(self):
        rows = [["Submarket Name", "Parcel Number 1 (Min)", "Parcel Number 2 (Max)"],
                ["Alpha Park ", "99Z-0001-LL-001-1", "99Z-0001-LL-002-1"],
                ["Beta Yard", "017-113", None],
                [None, "99Z-0001-LL-003-1", None]]
        self.assertEqual(ci.comp_parcels(rows),
                         {"Alpha Park": {"99Z0001LL0011", "99Z0001LL0021"}})

    def test_trim_drops_isolated_points_but_keeps_far_clusters(self):
        near = [(-84.45, 33.62), (-84.46, 33.63), (-84.44, 33.61)]
        far_cluster = [(-84.59, 33.55), (-84.60, 33.56)]           # ~15 km away
        lone = [(-84.0, 34.2)]
        kept = ci.trim_points(near + far_cluster + lone)
        self.assertEqual(kept, near + far_cluster)

    def test_plan_requires_min_points(self):
        loc = {"A1": (-84.45, 33.62), "A2": (-84.46, 33.63), "A3": (-84.44, 33.61),
               "B1": (-84.5, 33.7), "B2": (-84.51, 33.7)}
        plan = ci.plan_areas({"A": {"A1", "A2", "A3"}, "B": {"B1", "B2", "B9"}}, loc)
        self.assertEqual(sorted(plan.points), ["A"])
        self.assertEqual(plan.too_few, {"B": 2})


class _Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def _feature(pid, x, y, d=0.001):
    ring = [[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]
    return {"attributes": {"ParcelID": pid}, "geometry": {"rings": [ring]}}


class FultonLocatorTests(unittest.TestCase):
    def test_pages_matches_exactly_one_and_caches(self):
        pages = [
            {"features": [_feature("99Z0001  LL0011", -84.5, 33.7)], "exceededTransferLimit": True},
            {"features": [_feature("99Z0001  LL0021", -84.5, 33.7),
                          _feature("99Z0001  LL0021", -84.6, 33.7),      # duplicate id
                          _feature("99Z0001  LL0031", 10.0, 10.0)]},     # outside Fulton
        ]
        session = mock.Mock()
        session.get.side_effect = [_Resp(b) for b in pages]
        sleeps = []
        with tempfile.TemporaryDirectory() as d:
            loc = ci.FultonLocator(session=session, cache_path=Path(d) / "c.json", sleep=sleeps.append)
            got = loc.locate(["99Z0001LL0011", "99Z0001LL0021", "99Z0001LL0031"])
            self.assertEqual(list(got), ["99Z0001LL0011"])
            self.assertAlmostEqual(got["99Z0001LL0011"][0], -84.4995, places=4)
            first = session.get.call_args_list[0].kwargs["params"]
            self.assertEqual(first["where"], "ParcelID LIKE '99Z%0001%'")
            self.assertEqual(first["outSR"], 4326)
            self.assertEqual(session.get.call_args_list[1].kwargs["params"]["resultOffset"], 1)
            self.assertIn(ci.REQUEST_INTERVAL_S, sleeps)                 # polite (SR-11)
            again = ci.FultonLocator(session=session, cache_path=Path(d) / "c.json", sleep=sleeps.append)
            self.assertEqual(again.locate(["99Z0001LL0011"]), got)
        self.assertEqual(session.get.call_count, 2)                      # cache hit, no request

    def test_url_comes_from_sources_json(self):
        self.assertTrue(ci._fulton_layer_url().endswith("/MapServer/11/query"))


class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))

    def fetchone(self):
        return self.conn.results.pop(0)


class _Conn:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []
        self.transactions = 0

    def cursor(self):
        return _Cur(self)

    @contextlib.contextmanager
    def transaction(self):
        self.transactions += 1
        yield


class WriteAreasTests(unittest.TestCase):
    def setUp(self):
        self.plan = ci.AreaPlan(points={
            "Alpha Park": [(-84.45, 33.62), (-84.46, 33.63), (-84.44, 33.61)],
            "Beta Yard": [(-84.5, 33.7), (-84.51, 33.7), (-84.5, 33.71)],
        })

    def _patched(self):
        return mock.patch.object(costar_ingest, "_ensure_submarket",
                                 side_effect=lambda c, m, n: (f"atlanta__{costar_ingest._slugify(n)}", False, None))

    def test_writes_tool_areas_keeps_human_seeded_and_clears_stale(self):
        conn = _Conn([
            (False, None), ("SRID=4326;POLYGON((0 0,1 0,1 1,0 0))", "POLYGON", True, 4326, 2e6),
            (True, "seeded by hand"),
        ])
        with self._patched():
            out = ci.write_areas(conn, self.plan, date(2026, 10, 29))
        self.assertEqual(out, {"written": 1, "kept_human_seeded": 1, "cleared_stale": 0,
                               "name_drift": 0})
        self.assertEqual(conn.transactions, 1)
        update = [e for e in conn.executed if e[0] is ci._SQL_UPDATE_SUBMARKET_AREA]
        self.assertEqual(len(update), 1)
        self.assertEqual(update[0][1][2], "atlanta__alpha_park")
        self.assertTrue(update[0][1][1].startswith(ci.AREA_NOTE_PREFIX))
        clear = [e for e in conn.executed if e[0] is ci._SQL_CLEAR_STALE_AREAS]
        self.assertEqual(clear, [(ci._SQL_CLEAR_STALE_AREAS,
                                  ("atlanta", ci.AREA_NOTE_PREFIX + "%",
                                   ["atlanta__alpha_park", "atlanta__beta_yard"]))])

    def test_invalid_geometry_aborts_the_whole_write(self):
        conn = _Conn([(False, None), ("x", "MULTIPOLYGON", True, 4326, 1.0)])
        with self._patched(), self.assertRaises(ci.ImportError_):
            ci.write_areas(conn, self.plan, date(2026, 10, 29))
        self.assertFalse([e for e in conn.executed if e[0] is ci._SQL_UPDATE_SUBMARKET_AREA])


class RunTests(unittest.TestCase):
    def test_without_database_writes_csv_and_exits_2(self):
        comps = [["Submarket Name", "Parcel Number 1 (Min)"],
                 ["Alpha Park", "99Z-0001-LL-001-1"], ["Alpha Park", "99Z-0001-LL-002-1"],
                 ["Alpha Park", "99Z-0001-LL-003-1"]]
        locator = mock.Mock()
        locator.locate.return_value = {"99Z0001LL0011": (-84.45, 33.62),
                                       "99Z0001LL0021": (-84.46, 33.63),
                                       "99Z0001LL0031": (-84.44, 33.61)}
        with tempfile.TemporaryDirectory() as d:
            path = make_xlsx(Path(d) / "x.xlsx", {"s": stats_rows(), "c": comps})
            base = Path(d) / "costar_exports"
            with mock.patch.object(ci.prepare, "dsn_available", return_value=False), \
                 mock.patch.object(ci, "run_has_baseline", return_value=False):
                rc = ci.run(path, locator=locator, base=base)
            self.assertEqual(rc, 2)
            self.assertTrue((base / "submarket_stats" / "submarket_stats_20260930.csv").exists())


class ReviewFindingTests(unittest.TestCase):
    """reviews/19_costar_import post-change findings."""

    def _log(self, d, rows):
        p = Path(d) / "experiment_log.tsv"
        with p.open("w", encoding="utf-8", newline="") as fh:
            fh.write("commit\tmetric\tconfidence\tapi_calls\twall_clock_min\tstatus\tdescription\n")
            for status, desc in rows:
                fh.write(f"abc1234\t0\t0.00\t0\t1.0\t{status}\t{desc}\n")
        return p

    def test_baseline_check_agrees_with_the_runner(self):
        import runner
        cases = [
            [],
            [("baseline", "baseline | run=atl-x | exp=e1")],
            [("baseline", "baseline | run=atl-other | exp=e1")],
            [("crash", "baseline | run=atl-x | exp=e1")],
            [("baseline", "old row without marker")],
        ]
        with tempfile.TemporaryDirectory() as d:
            for rows in cases:
                path = self._log(d, rows)
                with mock.patch.object(ci.prepare, "current_run_tag", return_value="atl-x"), \
                     mock.patch.object(runner, "_git_current_branch", return_value="autoresearch/atl-x"):
                    self.assertEqual(ci.run_has_baseline(path),
                                     runner.current_run_has_baseline(path), rows)

    def test_refuses_once_the_run_has_a_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            path = make_xlsx(Path(d) / "x.xlsx", {"s": stats_rows()})
            with mock.patch.object(ci, "run_has_baseline", return_value=True):
                self.assertEqual(ci.run(path, base=Path(d) / "out"), 3)
            self.assertFalse((Path(d) / "out").exists())

    def test_percent_strings_are_not_multiplied(self):
        rows = stats_rows()
        for r in rows[1:]:
            if r[3] == "Vacancy Rate":
                r[5] = "0.5%"
        self.assertEqual(ci.convert_stats(rows).rows[0]["vacancy_rate_pct"], "0.5")

    def test_header_need_not_be_the_first_row(self):
        rows = [["CoStar export"], []] + stats_rows()
        self.assertEqual(len(ci.convert_stats(rows).rows), 2)

    def test_cells_without_refs_rich_text_and_dates(self):
        xml = (b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
               b'<row><c t="inlineStr"><is><r><t>Sub</t></r><r><t>market</t></r>'
               b'<rPh><t>x</t></rPh></is></c><c t="d"><v>2026-09-30</v></c><c><v>1.5</v></c></row>'
               b'</sheetData></worksheet>')
        self.assertEqual(ci._read_sheet(xml, []), [["Submarket", "2026-09-30", 1.5]])

    def test_locator_does_not_cache_an_empty_lot_and_stops_on_repeats(self):
        same = {"features": [_feature("99Z0001  LL0011", -84.5, 33.7)], "exceededTransferLimit": True}
        session = mock.Mock()
        session.get.side_effect = [_Resp({"features": []}), _Resp(same), _Resp(same)]
        with tempfile.TemporaryDirectory() as d:
            loc = ci.FultonLocator(session=session, cache_path=Path(d) / "c.json", sleep=lambda s: None)
            self.assertEqual(loc.locate(["99Z0001LL0011"]), {})
            self.assertNotIn("lot:99Z0001", loc.cache)
            self.assertEqual(list(loc.locate(["99Z0001LL0011"])), ["99Z0001LL0011"])
        self.assertEqual(session.get.call_count, 3)      # repeated page ends paging

    def test_bad_names_are_refused_not_reported_as_database_errors(self):
        comps = [["Submarket Name", "Parcel Number 1 (Min)"]] + [
            ["???", f"99Z-0001-LL-00{i}-1"] for i in range(3)]
        located = {f"99Z0001LL00{i}1": (-84.5 + i * 0.001, 33.7) for i in range(3)}
        locator = mock.Mock()
        locator.locate.return_value = located
        with tempfile.TemporaryDirectory() as d:
            path = make_xlsx(Path(d) / "x.xlsx", {"c": comps})
            with mock.patch.object(ci.prepare, "dsn_available", return_value=True), \
                 mock.patch.object(ci.prepare, "get_connection",
                                   return_value=contextlib.nullcontext(_Conn([]))), \
                 mock.patch.object(ci, "run_has_baseline", return_value=False), \
                 mock.patch.object(ci, "FultonLocator", return_value=locator), \
                 mock.patch("sys.stdout"):
                self.assertEqual(ci.main([str(path)]), 1)


class ContractTests(unittest.TestCase):
    def test_ids_match_the_loader_and_the_parcels_market(self):
        self.assertEqual(costar_ingest._slugify(ci.MARKET_NAME), "atlanta")
        self.assertIn("atlanta", research._MARKET_TO_COUNTIES)

    def test_does_not_import_the_agent_sandbox(self):
        tree = ast.parse((REPO_ROOT / "costar_import.py").read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertNotIn("research", names)
        self.assertNotIn("runner", names)

    def test_run_marker_regex_matches_the_runner(self):
        import runner
        self.assertEqual(ci._RUN_MARKER_RE.pattern, runner._DESCRIPTION_RUN_RE.pattern)

    def test_research_resolves_submarket_at_read_time_in_both_paths(self):
        for sql in (research._SQL_FETCH_PARCEL, research._SQL_DISTINCT_SUBMARKETS_FOR_PARCELS):
            self.assertIn(research._SQL_RESOLVED_SUBMARKET, sql)
        self.assertTrue(research._SQL_RESOLVED_SUBMARKET.startswith("COALESCE((SELECT"))
        src = (REPO_ROOT / "research.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?i)UPDATE\s+parcels\s+SET\s+submarket")


# ---------------------------------------------------------------------------
# Land sales sheet (reviews/20_next_improvements/05_*). Synthetic rows only.
# ---------------------------------------------------------------------------
LAND_HEADER = ["Property Address", "Property Type", "Land Area AC", "Sale Price", "Sale Date",
               "Sale Status", "Price Per AC Land", "Zoning", "Submarket Name",
               "Property County", "Buyer (True) Company", "Seller (True) Company",
               "Proposed Use", "Latitude", "Longitude"]
TODAY = date(2026, 11, 2)
SERIAL_2026_03_02 = (date(2026, 3, 2) - date(1899, 12, 30)).days


def land_row(**kw):
    row = {"Property Address": "1 Test Way", "Property Type": "Land", "Land Area AC": 10,
           "Sale Price": 1_000_000, "Sale Date": SERIAL_2026_03_02, "Sale Status": "Sold",
           "Price Per AC Land": 100_000, "Zoning": "M-1", "Submarket Name": "Somewhere",
           "Property County": "Testa County", "Buyer (True) Company": "Buyer Co",
           "Seller (True) Company": "Seller Co", "Proposed Use": "Industrial",
           "Latitude": 33.70, "Longitude": -84.50}
    row.update(kw)
    return [row[h] for h in LAND_HEADER]


class _LandCur:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        self._result = self.conn.answer(sql, params)
        self.rowcount = self.conn.rowcounts.get(sql, 0)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class _LandConn:
    def __init__(self, n_areas=2, assign=None, rowcounts=None):
        self.n_areas = n_areas
        self.assign = assign
        self.rowcounts = rowcounts or {}
        self.executed = []
        self.transactions = 0
        self.commits = 0

    def answer(self, sql, params):
        if sql == ci._SQL_COUNT_MARKET_AREAS:
            return [(self.n_areas,)]
        if sql == ci._SQL_ASSIGN_POINTS_TO_AREAS:
            n = len(params[2])
            return self.assign(n) if self.assign else [(i, "atlanta__alpha") for i in range(1, n + 1)]
        return []

    def cursor(self):
        return _LandCur(self)

    @contextlib.contextmanager
    def transaction(self):
        self.transactions += 1
        yield

    def commit(self):
        self.commits += 1

    def run(self, sql):
        return [p for s, p in self.executed if s == sql]


class LandClassifierTests(unittest.TestCase):
    def test_zoning_entitlement_table(self):
        industrial = ["M-1", "M1", "M-2", "M", "LM", "LI", "L-I", "HI", "I", "I-1", "I2", "I GI",
                      "MIA", "P-M", "M-1A", "LI-1", "I-MIX", "PD-I", "IND", "Industrial",
                      "Light Industrial", "m1/c1", "LI & GC", "LI/WS", "M, Some County",
                      "I-L", "IL", "IH"]
        not_industrial = ["", None, "O-I", "OI", "MU", "MU-2", "MHP", "MD", "MIX", "H-B",
                          "R-100", "A1", "AG1", "C-2", "CUP", "N/Av", "MUR", "HDMU", "LR",
                          "Data Center Use", "TCMU", "RM8"]
        for z in industrial:
            self.assertTrue(ci.is_industrial_zoning(z), z)
        for z in not_industrial:
            self.assertFalse(ci.is_industrial_zoning(z), z)

    def test_land_sheet_detection_excludes_building_and_area_sheets(self):
        self.assertTrue(ci.is_land_sheet([LAND_HEADER]))
        for extra in ("RBA", "Building SF", "Year Built", "Parcel Number 1 (Min)"):
            self.assertFalse(ci.is_land_sheet([LAND_HEADER + [extra]]), extra)
        self.assertFalse(ci.is_land_sheet([["Submarket Name", "Parcel Number 1 (Min)"]]))


class LandConversionTests(unittest.TestCase):
    def test_keeps_only_sold_entitled_located_5_to_50_acre_land(self):
        rows = [LAND_HEADER,
                land_row(),
                land_row(**{"Property Type": "Retail", "Property Address": "2"}),
                land_row(**{"Sale Status": "Under Contract", "Property Address": "3"}),
                land_row(**{"Sale Date": None, "Property Address": "4"}),
                land_row(**{"Sale Date": "2027-01-05", "Property Address": "5"}),
                land_row(**{"Sale Price": None, "Property Address": "6"}),
                land_row(**{"Land Area AC": 60, "Price Per AC Land": None, "Property Address": "7"}),
                land_row(**{"Land Area AC": 4, "Price Per AC Land": None, "Property Address": "8"}),
                land_row(**{"Latitude": None, "Property Address": "9"}),
                land_row(**{"Latitude": 40.7, "Longitude": -74.0, "Property Address": "10"}),
                land_row(**{"Zoning": "AG1", "Property Address": "11"}),
                land_row(**{"Property Address": "12 Lot Rd (Part of a 3 Property Sale)"}),
                land_row(**{"Price Per AC Land": 150_000, "Property Address": "13"}),
                land_row(**{"Property Address": "1 TEST WAY."}),
                land_row(**{"Property Address": "14", "Sale Date": "2025-12-01"}),
                land_row(**{"Property Address": "15", "Sale Date": "6/30/2025"}),
                [None] * len(LAND_HEADER)]
        out = ci.convert_land(rows, today=TODAY)
        self.assertEqual([r["address"] for r in out.rows], ["1 Test Way", "14", "15"])
        self.assertEqual(out.dropped, {
            "not land": 1, "not sold": 1, "no sale date": 1, "future sale date": 1,
            "no price or acres": 1, "outside 5-50 acres": 2, "no location": 1,
            "location outside Georgia": 1, "not industrially zoned": 1,
            "part of a portfolio or multi-property sale": 1,
            "price per acre disagrees with price / acres": 1,
            "duplicate address and date": 1})
        first = out.rows[0]
        self.assertEqual(first["sale_date"], "2026-03-02")
        self.assertEqual(first["price_per_acre"], 100_000.0)
        self.assertEqual(first["county"], "Testa")
        self.assertEqual([r["sale_date"] for r in out.rows[1:]], ["2025-12-01", "2025-06-30"])

    def test_date_systems(self):
        self.assertEqual(ci._sale_date(SERIAL_2026_03_02, False), date(2026, 3, 2))
        self.assertEqual(ci._sale_date(SERIAL_2026_03_02 - 1462, True), date(2026, 3, 2))
        self.assertEqual(ci._sale_date("2026-03-02 00:00:00", False), date(2026, 3, 2))
        for bad in (None, True, "", "soon", "13/45/2026", float("nan"), 0):
            self.assertIsNone(ci._sale_date(bad, False), bad)

    def test_date1904_flag_is_read_from_the_workbook(self):
        with tempfile.TemporaryDirectory() as d:
            path = make_xlsx(Path(d) / "x.xlsx", {"land": [LAND_HEADER, land_row()]})
            self.assertFalse(ci.xlsx_date1904(path))
            with zipfile.ZipFile(path) as z:
                parts = {n: z.read(n) for n in z.namelist()}
            parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
                b"<sheets>", b'<workbookPr date1904="1"/><sheets>')
            with zipfile.ZipFile(path, "w") as z:
                for n, b in parts.items():
                    z.writestr(n, b)
            self.assertTrue(ci.xlsx_date1904(path))
            self.assertEqual(ci.read_xlsx(path)["land"][1][LAND_HEADER.index("Sale Date")],
                             float(SERIAL_2026_03_02))

    def test_sale_status_column_is_required(self):
        header = [h for h in LAND_HEADER if h != "Sale Status"]
        with self.assertRaises(ci.ImportError_):
            ci.convert_land([header], today=TODAY)


class WriteLandTests(unittest.TestCase):
    def rows(self, n=2):
        return [dict(ci.convert_land([LAND_HEADER, land_row()], today=TODAY).rows[0],
                     address=f"{i} Test Way") for i in range(n)]

    def test_replaces_tool_rows_in_one_transaction_and_counts(self):
        conn = _LandConn(assign=lambda n: [(1, "atlanta__alpha"), (2, None)],
                         rowcounts={ci._SQL_DELETE_TOOL_LAND_COMPS: 7})
        out = ci.write_land(conn, self.rows(), TODAY)
        self.assertEqual(out, {"deferred": 0, "assigned": 1, "no_area": 1, "submarkets": 1,
                               "replaced": 7})
        self.assertEqual(conn.transactions, 1)
        self.assertEqual(conn.run(ci._SQL_DELETE_TOOL_LAND_COMPS), [(ci.LAND_ORIGIN, "atlanta")])
        (ins,) = conn.run(costar_ingest._SQL_INSERT_LAND_SALES)
        self.assertEqual(ins[0], "0 Test Way")
        self.assertEqual(ins[3], "atlanta__alpha")
        self.assertIsNone(ins[1])                      # parcel_id
        raw = __import__("json").loads(ins[-1])
        self.assertEqual(raw["import_origin"], ci.LAND_ORIGIN)
        self.assertEqual((raw["latitude"], raw["longitude"]), (33.70, -84.50))
        (assign,) = conn.run(ci._SQL_ASSIGN_POINTS_TO_AREAS)
        self.assertEqual(assign[:2], ("atlanta", ci.LAND_ASSIGN_RADIUS_M))
        self.assertEqual(assign[2], [-84.5, -84.5])    # x = longitude
        self.assertEqual(assign[3], [33.7, 33.7])

    def test_no_areas_defers_and_touches_nothing(self):
        conn = _LandConn(n_areas=0)
        out = ci.write_land(conn, self.rows(), TODAY)
        self.assertEqual(out["deferred"], 1)
        self.assertEqual([s for s, _ in conn.executed], [ci._SQL_COUNT_MARKET_AREAS])

    def test_empty_rows_are_refused(self):
        with self.assertRaises(ci.ImportError_):
            ci.write_land(_LandConn(), [], TODAY)

    def test_assignment_rule_is_the_parcel_resolver_rule(self):
        sql = ci._SQL_ASSIGN_POINTS_TO_AREAS
        for clause in ("s.market_id = %s AND s.bbox IS NOT NULL",
                       "ST_DWithin(s.bbox::geography, t.pt::geography, %s)",
                       "ORDER BY ST_Distance(s.bbox::geography, t.pt::geography), "
                       "ST_Area(s.bbox::geography), s.submarket_id LIMIT 1",
                       "ST_MakePoint(u.x, u.y)", "WITH ORDINALITY"):
            self.assertIn(clause, sql)
        self.assertEqual(ci.LAND_ASSIGN_RADIUS_M, 3000)


class LandRunTests(unittest.TestCase):
    def _run(self, sheets, conn=None, dsn=True, baseline=False, base=None, **kw):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        path = make_xlsx(Path(d) / "x.xlsx", sheets)
        base = base or Path(d) / "costar_exports"

        @contextlib.contextmanager
        def get_conn():
            yield conn

        with mock.patch.object(ci.prepare, "dsn_available", return_value=dsn), \
             mock.patch.object(ci.prepare, "get_connection", get_conn), \
             mock.patch.object(ci, "run_has_baseline", return_value=baseline), \
             mock.patch("sys.stdout"):
            rc = ci.run(path, base=base, today=TODAY, **kw)
        return rc, base

    def test_land_only_workbook_writes_comps_and_never_touches_areas(self):
        conn = _LandConn()
        rc, base = self._run({"land": [LAND_HEADER, land_row()]}, conn)
        self.assertEqual(rc, 0)
        sqls = [s for s, _ in conn.executed]
        self.assertNotIn(ci._SQL_CLEAR_STALE_AREAS, sqls)
        self.assertNotIn(ci._SQL_UPDATE_SUBMARKET_AREA, sqls)
        self.assertEqual(len(conn.run(costar_ingest._SQL_INSERT_LAND_SALES)), 1)
        self.assertTrue((base / ci.LAND_SNAPSHOT_NAME).is_file())
        self.assertEqual(conn.commits, 1)

    def test_refused_after_baseline_before_any_land_work(self):
        conn = _LandConn()
        rc, base = self._run({"land": [LAND_HEADER, land_row()]}, conn, baseline=True)
        self.assertEqual(rc, 3)
        self.assertEqual(conn.executed, [])
        self.assertFalse((base / ci.LAND_SNAPSHOT_NAME).exists())

    def test_without_database_saves_snapshot_and_exits_2(self):
        rc, base = self._run({"land": [LAND_HEADER, land_row()]}, None, dsn=False)
        self.assertEqual(rc, 2)
        self.assertTrue((base / ci.LAND_SNAPSHOT_NAME).is_file())

    def test_no_areas_flag_skips_land_entirely(self):
        conn = _LandConn()
        rc, base = self._run({"land": [LAND_HEADER, land_row()]}, conn, areas=False)
        self.assertEqual(rc, 0)
        self.assertEqual(conn.executed, [])
        self.assertFalse((base / ci.LAND_SNAPSHOT_NAME).exists())

    def test_nothing_qualifying_is_refused_and_writes_nothing(self):
        conn = _LandConn()
        with self.assertRaises(ci.ImportError_):
            self._run({"land": [LAND_HEADER, land_row(Zoning="R-100")]}, conn)
        self.assertEqual(conn.executed, [])

    def test_redrawn_areas_reassign_the_saved_land_sales(self):
        conn = _LandConn()
        rc, base = self._run({"land": [LAND_HEADER, land_row()]}, conn)
        comps = [["Submarket Name", "Parcel Number 1 (Min)"],
                 ["Alpha Park", "99Z-0001-LL-001-1"], ["Alpha Park", "99Z-0001-LL-002-1"],
                 ["Alpha Park", "99Z-0001-LL-003-1"]]
        locator = mock.Mock()
        locator.locate.return_value = {"99Z0001LL0011": (-84.45, 33.62),
                                       "99Z0001LL0021": (-84.46, 33.63),
                                       "99Z0001LL0031": (-84.44, 33.61)}
        conn2 = _LandConn()
        with mock.patch.object(ci, "write_areas", return_value={
                "written": 1, "kept_human_seeded": 0, "cleared_stale": 0, "name_drift": 0}) as wa:
            rc, _ = self._run({"c": comps}, conn2, base=base, locator=locator)
        self.assertEqual(rc, 0)
        wa.assert_called_once()
        self.assertEqual(len(conn2.run(costar_ingest._SQL_INSERT_LAND_SALES)), 1)
        self.assertEqual(conn2.run(ci._SQL_DELETE_TOOL_LAND_COMPS), [(ci.LAND_ORIGIN, "atlanta")])

    def test_unlocatable_area_comps_leave_areas_and_land_alone(self):
        comps = [["Submarket Name", "Parcel Number 1 (Min)"], ["Alpha Park", "99Z-0001-LL-001-1"]]
        locator = mock.Mock()
        locator.locate.return_value = {}
        conn = _LandConn()
        rc, _ = self._run({"c": comps}, conn, locator=locator)
        self.assertEqual(rc, 0)
        self.assertEqual(conn.executed, [])

    def test_write_areas_with_an_empty_plan_writes_and_clears_nothing(self):
        conn = _Conn([])
        self.assertEqual(ci.write_areas(conn, ci.AreaPlan(), TODAY)["cleared_stale"], 0)
        self.assertEqual(conn.executed, [])


if __name__ == "__main__":
    unittest.main()
