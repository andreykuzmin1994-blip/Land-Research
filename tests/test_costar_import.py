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
                 ["Alpha Park", "99Z-0001-LL-001-1"]]
        locator = mock.Mock()
        locator.locate.return_value = {}
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


if __name__ == "__main__":
    unittest.main()
