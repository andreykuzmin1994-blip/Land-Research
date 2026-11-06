"""Offline tests for CoStar building sale and lease comps (reviews/28_building_comps).

costar_import.py imports them (memo only, no scoring input); reporting.py
shows the market rent and an exit-value estimate. Hermetic (SR-6): every
workbook is generated here with obviously fake rows ('1 Test St', parcel
ids starting 99Z); the database is a fake. Test names carry the risk id
(BC-xx) they dispose of.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import re
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

import costar_import as ci
import costar_ingest
import reporting
import research
import site_fit
import static_guards
from test_costar_import import LAND_HEADER, _LandConn, land_row, make_xlsx, stats_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 2)


def serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


BUILDING_HEADER = [
    "Property Address", "Property Name", "Property Type", "Secondary Type", "Sale Status",
    "Sale Date", "Sale Price", "RBA", "Price Per SF", "Actual Cap Rate", "Sale Condition",
    "Number Of Properties", "Year Built", "Ceiling Height", "Land Area AC", "Price Per SF Land",
    "Submarket Name", "Buyer (True) Company", "True Buyer Contact", "Buyer Phone",
    "Buyer Email", "Seller (True) Company", "Seller Contact", "Listing Broker Agent",
    "Comments", "Latitude", "Longitude",
]
# Cell values the console, raw JSONB, snapshot files and memo must never show (BC-05).
SECRET_CELLS = ("Test Bldg Alpha", "Buyer Co Alpha", "Jane Doe Contact", "555-0100",
                "jane@example.invalid", "Seller Co Alpha", "John Roe Contact",
                "Broker Person Alpha", "secret comment alpha", "Tenant Person Alpha",
                "Rep Co Alpha", "Tenant Contact Alpha", "Agent Name Alpha", "555-0199",
                "tenant@example.invalid", "lease comment alpha", "1 Test St", "2 Test St")


def building_row(**kw):
    row = {"Property Address": "1 Test St", "Property Name": "Test Bldg Alpha",
           "Property Type": "Industrial", "Secondary Type": "Warehouse", "Sale Status": "Sold",
           "Sale Date": serial(date(2026, 3, 2)), "Sale Price": 20_000_000, "RBA": 200_000,
           "Price Per SF": 100, "Actual Cap Rate": 0.055,
           "Sale Condition": "Investment Triple Net", "Number Of Properties": 1,
           "Year Built": 2018, "Ceiling Height": 36, "Land Area AC": 15,
           "Price Per SF Land": 30.6, "Submarket Name": "Somewhere",
           "Buyer (True) Company": "Buyer Co Alpha", "True Buyer Contact": "Jane Doe Contact",
           "Buyer Phone": "555-0100", "Buyer Email": "jane@example.invalid",
           "Seller (True) Company": "Seller Co Alpha", "Seller Contact": "John Roe Contact",
           "Listing Broker Agent": "Broker Person Alpha", "Comments": "secret comment alpha",
           "Latitude": 33.70, "Longitude": -84.50}
    row.update(kw)
    return [row.get(h) for h in BUILDING_HEADER]


LEASE_HEADER = [
    "Property Address", "Property Type", "Secondary Type", "Tenant Name", "Tenant Rep Company",
    "Tenant Contact", "Landlord Rep Agent", "Phone", "Email", "Comments", "Sign Date",
    "Start Date", "SF Leased", "Starting Rent/SF/Yr", "Asking Rent/SF/Yr", "Rent Type",
    "Service Type", "Lease Type", "Deal Type", "Lease Term", "RBA", "Year Built",
    "Submarket Name", "Latitude", "Longitude",
]


def lease_row(**kw):
    row = {"Property Address": "2 Test St", "Property Type": "Industrial",
           "Secondary Type": "Distribution", "Tenant Name": "Tenant Person Alpha",
           "Tenant Rep Company": "Rep Co Alpha", "Tenant Contact": "Tenant Contact Alpha",
           "Landlord Rep Agent": "Agent Name Alpha", "Phone": "555-0199",
           "Email": "tenant@example.invalid", "Comments": "lease comment alpha",
           "Sign Date": serial(date(2026, 5, 1)), "Start Date": serial(date(2026, 7, 1)),
           "SF Leased": 250_000, "Starting Rent/SF/Yr": 6.5, "Asking Rent/SF/Yr": None,
           "Rent Type": "Starting", "Service Type": "NNN", "Lease Type": "Direct",
           "Deal Type": "New", "Lease Term": "5 Years 3 Months", "RBA": 400_000,
           "Year Built": 2020, "Submarket Name": "Somewhere", "Latitude": 33.70,
           "Longitude": -84.50}
    row.update(kw)
    return [row.get(h) for h in LEASE_HEADER]


AREA_ONLY = [["Submarket Name", "Parcel Number 1 (Min)"],
             ["Alpha Park", "99Z-0001-LL-001-1"], ["Alpha Park", "99Z-0001-LL-002-1"],
             ["Alpha Park", "99Z-0001-LL-003-1"]]


def _locator():
    loc = mock.Mock()
    loc.locate.return_value = {"99Z0001LL0011": (-84.45, 33.62),
                               "99Z0001LL0021": (-84.46, 33.63),
                               "99Z0001LL0031": (-84.44, 33.61)}
    return loc


class _CompsConn(_LandConn):
    """_LandConn that also answers the other-origin key reads."""

    def __init__(self, other_building=(), other_lease=(), **kw):
        super().__init__(**kw)
        self.other_building = list(other_building)
        self.other_lease = list(other_lease)

    def answer(self, sql, params):
        if sql == ci._SQL_OTHER_BUILDING_KEYS:
            return self.other_building
        if sql == ci._SQL_OTHER_LEASE_KEYS:
            return self.other_lease
        return super().answer(sql, params)


def run_import(case, sheets, conn=None, dsn=True, baseline=False, base=None, **kw):
    d = tempfile.mkdtemp()
    case.addCleanup(__import__("shutil").rmtree, d, True)
    path = make_xlsx(Path(d) / "x.xlsx", sheets)
    base = base or Path(d) / "costar_exports"

    @contextlib.contextmanager
    def get_conn():
        yield conn

    out = io.StringIO()
    with mock.patch.object(ci.prepare, "dsn_available", return_value=dsn), \
         mock.patch.object(ci.prepare, "get_connection", get_conn), \
         mock.patch.object(ci, "run_has_baseline", return_value=baseline), \
         contextlib.redirect_stdout(out):
        try:
            rc = ci.run(path, base=base, today=TODAY, **kw)
        except ci.ImportError_ as exc:
            print(f"REFUSED: {exc}")
            rc = 1
    return rc, base, out.getvalue()


def _sqls(conn):
    return [s for s, _ in conn.executed]


# ---------------------------------------------------------------------------
# BC-01 sheet roles
# ---------------------------------------------------------------------------
class SheetRoleTests(unittest.TestCase):
    SHAPES = {
        "stats": (lambda: stats_rows(), "stats"),
        "area-only": (lambda: AREA_ONLY, "areas"),
        "land": (lambda: [LAND_HEADER, land_row()], "land"),
        "building": (lambda: [BUILDING_HEADER, building_row()], "building"),
        "lease": (lambda: [LEASE_HEADER, lease_row()], "lease"),
        "building with parcel numbers": (
            lambda: [BUILDING_HEADER + ["Parcel Number 1 (Min)"],
                     building_row() + ["99Z-0001-LL-001-1"]], "areas+building"),
        "lease with parcel numbers": (
            lambda: [LEASE_HEADER + ["Parcel Number 1 (Min)"],
                     lease_row() + ["99Z-0001-LL-001-1"]], "lease"),
    }
    CLASSIFIERS = {
        "stats": lambda r: ci.sheet_role(r)[0] == "stats",
        "areas": lambda r: ci.sheet_role(r)[0] in ("areas", "areas+building"),
        "land": lambda r: ci.sheet_role(r)[0] == "land",
        "building": lambda r: ci.sheet_role(r)[0] in ("building", "areas+building"),
        "lease": lambda r: ci.sheet_role(r)[0] == "lease",
    }

    def test_bc01_every_shape_has_exactly_one_role(self):
        for shape, (rows, role) in self.SHAPES.items():
            with self.subTest(shape=shape):
                self.assertEqual(ci.sheet_role(rows())[0], role)
                hits = {name for name, f in self.CLASSIFIERS.items() if f(rows())}
                self.assertEqual(hits, set(role.split("+")))
                # The old land classifier never takes a building or lease sheet.
                self.assertEqual(ci.is_land_sheet(rows()), role == "land")

    def test_bc01_area_rows_never_come_from_a_lease_sheet(self):
        roles = ci.classify_tables([self.SHAPES["lease with parcel numbers"][0]()])
        self.assertIsNone(roles.areas)
        self.assertIsNotNone(roles.lease)

    def test_bc01_area_sheet_without_building_columns_keeps_only_its_area_role(self):
        rows = [["Submarket Name", "Parcel Number 1 (Min)", "RBA", "Sale Price"],
                ["Alpha Park", "99Z-0001-LL-001-1", 200000, 1]]
        role, missing = ci.sheet_role(rows)
        self.assertEqual(role, "areas")
        self.assertIn("'Sale Status'", missing)

    def test_bc01_two_sheets_with_one_role_are_refused(self):
        for a, b in (("lease", "lease"), ("area-only", "area-only"),
                     ("building with parcel numbers", "building"), ("land", "land"),
                     ("area-only", "building with parcel numbers")):
            with self.subTest(pair=(a, b)), self.assertRaises(ci.ImportError_) as cm:
                ci.classify_tables([self.SHAPES[a][0](), self.SHAPES[b][0]()])
            self.assertIn("two sheets match", str(cm.exception))

    def test_bc01_refusal_through_run_writes_nothing(self):
        conn = _CompsConn()
        rc, _, out = run_import(self, {"a": [LEASE_HEADER, lease_row()],
                                       "b": [LEASE_HEADER, lease_row()]}, conn)
        self.assertEqual(rc, 1)
        self.assertIn("two sheets match the lease comps layout", out)
        self.assertEqual(conn.executed, [])

    def test_bc01_lease_or_building_only_import_never_touches_submarkets(self):
        for sheets in ({"l": [LEASE_HEADER, lease_row()]},
                       {"b": [BUILDING_HEADER, building_row()]},
                       {"l": [LEASE_HEADER + ["Parcel Number 1 (Min)"],
                              lease_row() + ["99Z-0001-LL-001-1"]]}):
            conn = _CompsConn()
            with self.subTest(sheet=list(sheets)), \
                 mock.patch.object(ci, "write_areas") as wa, \
                 mock.patch.object(costar_ingest, "_ensure_submarket") as ens:
                rc, _, _ = run_import(self, sheets, conn, locator=_locator())
            self.assertEqual(rc, 0)
            wa.assert_not_called()
            ens.assert_not_called()
            for sql in _sqls(conn):
                self.assertNotRegex(sql, r"(?i)(UPDATE|INSERT INTO|DELETE FROM)\s+submarkets")
            self.assertTrue(any(s in (ci._SQL_DELETE_TOOL_BUILDING_COMPS,
                                      ci._SQL_DELETE_TOOL_LEASE_COMPS) for s in _sqls(conn)))

    def test_bc01_dual_sheet_writes_areas_first_then_building_comps(self):
        order = []
        conn = _CompsConn()
        real = ci.write_comps

        def comps(*a, **k):
            order.append("building")
            return real(*a, **k)

        def areas(*a, **k):
            order.append("areas")
            return {"written": 1, "kept_human_seeded": 0, "cleared_stale": 0, "name_drift": 0}

        rows = [BUILDING_HEADER + ["Parcel Number 1 (Min)"]] + [
            building_row(**{"Property Address": f"{i} Test St"}) + [f"99Z-0001-LL-00{i}-1"]
            for i in (1, 2, 3)]
        with mock.patch.object(ci, "write_areas", side_effect=areas) as wa, \
             mock.patch.object(ci, "write_comps", side_effect=comps):
            rc, _, out = run_import(self, {"c": rows}, conn, locator=_locator())
        self.assertEqual(rc, 0)
        self.assertEqual(order, ["areas", "building"])
        # The area plan is exactly what the parcel numbers alone give.
        plan = wa.call_args[0][1]
        self.assertEqual(plan, ci.plan_areas(ci.comp_parcels(rows), _locator().locate.return_value))
        self.assertIn("its areas are drawn first", out)


# ---------------------------------------------------------------------------
# BC-02 cap-rate units
# ---------------------------------------------------------------------------
class CapRateTests(unittest.TestCase):
    def test_bc02_three_input_forms_store_one_fraction(self):
        for v in ("5.50%", 5.5, 0.055, "5.5", " 5.50 % "):
            self.assertEqual(ci.cap_rate_fraction(v), 0.055, v)
        rows = [BUILDING_HEADER] + [building_row(**{"Actual Cap Rate": v,
                                                    "Property Address": f"{i} Test St"})
                                    for i, v in enumerate(("5.50%", 5.5, 0.055))]
        caps = {r["cap_rate"] for r in ci.convert_building(rows, today=TODAY).rows}
        self.assertEqual(caps, {0.055})

    def test_bc02_bad_values_are_dropped_with_counted_reasons(self):
        rows = [BUILDING_HEADER] + [building_row(**{"Actual Cap Rate": v,
                                                    "Property Address": f"{i} Test St"})
                                    for i, v in enumerate((0, -1, 55, None, "n/a"))]
        res = ci.convert_building(rows, today=TODAY)
        self.assertEqual(len(res.rows), 5)                   # the sales stay ($/SF comps)
        self.assertTrue(all(r["cap_rate"] is None for r in res.rows))
        self.assertEqual(res.cap_dropped, {"cap rate outside 3%-12%": 3, "cap rate blank": 2})

    def test_bc02_exit_value_never_divides_by_a_zero_or_missing_cap(self):
        proj = _projection()
        rent = _stat(6.5)
        self.assertIsNone(reporting.exit_value_parts(proj, rent, None, None))
        self.assertIsNone(reporting.exit_value_parts(proj, rent, _stat(0.0), None))
        self.assertIsNone(reporting.exit_value_parts(proj, rent, _stat(-0.05), None))

    def test_bc02_csv_row_cap_55_never_reaches_the_median(self):
        # The read is scoped to the tool's rows; the band is re-applied per row.
        self.assertIn(ci._SQL_TOOL_ROW, reporting._SQL_BC_SALES)
        grouped = reporting._group([("s1", 100.0, 5.5), ("s1", 100.0, 0.06)], 2,
                                   ci.CAP_RATE_MIN, ci.CAP_RATE_MAX)
        self.assertEqual(grouped, {"s1": [0.06]})


# ---------------------------------------------------------------------------
# BC-03 resolved submarket
# ---------------------------------------------------------------------------
class _ReadConn:
    """Answers the building-comps reads by SQL text; counts them."""

    def __init__(self, leases=(), sales=(), resolved=(), names=(), fail=None, info=None):
        self.answers = {reporting._SQL_BC_LEASES: list(leases),
                        reporting._SQL_BC_SALES: list(sales),
                        reporting._SQL_BC_RESOLVED_SUBMARKETS: list(resolved),
                        reporting._SQL_BC_SUBMARKET_NAMES: list(names)}
        self.fail = fail
        self.executed = []
        self.rollbacks = 0
        self.savepoints = 0
        if info is not None:
            self.info = info

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                conn.executed.append((sql, params))
                if conn.fail is not None and sql == conn.fail:
                    raise RuntimeError("injected")
                self._r = conn.answers.get(sql, [])

            def fetchall(self):
                return list(self._r)

        return Cur()

    def rollback(self):
        self.rollbacks += 1

    @contextlib.contextmanager
    def transaction(self):
        self.savepoints += 1
        yield


def _leases(sub, rents):
    return [(sub, r) for r in rents]


def _sales(sub, psfs, caps=None):
    caps = caps or [None] * len(psfs)
    return [(sub, p, c) for p, c in zip(psfs, caps)]


def _stat(v, n=5, label="Alpha Park submarket"):
    return reporting.CompStat(v, n, label, (date(2024, 11, 6), date(2026, 11, 6)), "signed")


def _projection(status=site_fit.OK, sf=312_000, label="draft rules", fits=True):
    fit = site_fit.PrototypeFit(
        key="cross", label="cross-dock", fits=fits, full_size=False, length_ft=600.0,
        width_ft=520.0, depth_ft=520.0, building_sf=sf if fits else None, docks=40,
        trailer_stalls=50, coverage_pct=30.0, binding="", angle_deg=0.0)
    return site_fit.Projection(
        parcel_id="99Z-1", status=status, reason="projected", rules_label=label,
        rules_verified=label != "draft rules", setback_ft=25.0, setback_source="default",
        gross_sf=1_000_000, buildable_sf=800_000, coverage_cap_pct=None, fits=(fit,),
        constraints=())


class ResolvedSubmarketTests(unittest.TestCase):
    def test_bc03_resolver_copy_equals_research(self):
        self.assertEqual(ci._SQL_RESOLVED_SUBMARKET, research._SQL_RESOLVED_SUBMARKET)
        self.assertIn(ci._SQL_RESOLVED_SUBMARKET, reporting._SQL_BC_RESOLVED_SUBMARKETS)

    def test_bc03_parcel_shows_its_resolved_areas_rent_not_its_stored_one(self):
        # Stored submarket 'atlanta__alpha' (corridor), resolved to 'atlanta__beta'.
        conn = _ReadConn(
            leases=_leases("atlanta__alpha", [9.0] * 5) + _leases("atlanta__beta", [5.0] * 5),
            resolved=[("99Z-1", "atlanta__beta")],
            names=[("atlanta__beta", "Beta Yard")])
        bc = reporting.fetch_building_comps(conn, "atlanta", ["99Z-1"], date(2026, 11, 6))
        bundle = {"parcel": {"parcel_id": "99Z-1", "submarket": "atlanta__alpha"},
                  "building_comps": bc, "projection": None}
        md = reporting._building_comps_snapshot_lines(bundle)
        self.assertIn("$5.00/SF/yr (n=5, Beta Yard submarket", md)
        self.assertNotIn("$9.00", md)

    def test_bc03_existing_land_comps_and_market_lines_keep_the_stored_submarket(self):
        # Only the new section resolves; the old reads still bind the stored value.
        self.assertNotIn("COALESCE", reporting._SQL_FETCH_NEARBY_SALES_COMPS)
        src = (REPO_ROOT / "reporting.py").read_text(encoding="utf-8")
        self.assertIn('submarket_id = parcel.get("submarket")', src)


# ---------------------------------------------------------------------------
# BC-04 scoring untouched
# ---------------------------------------------------------------------------
def _sql_constants(module):
    return {n: v for n, v in vars(module).items() if n.startswith("_SQL") and isinstance(v, str)}


class ScoringUntouchedTests(unittest.TestCase):
    def test_bc04_every_sales_comps_select_filters_comp_type(self):
        for module in (research, reporting):
            for name, sql in _sql_constants(module).items():
                if re.search(r"(?i)\bFROM\s+sales_comps\b", sql) and sql.lstrip().upper().startswith("SELECT"):
                    with self.subTest(module=module.__name__, sql=name):
                        self.assertRegex(sql, r"comp_type = '(land|building)'")

    def test_bc04_research_reads_no_leasing_comps_and_never_names_the_tool(self):
        for name, sql in _sql_constants(research).items():
            self.assertNotIn("leasing_comps", sql, name)
        src = (REPO_ROOT / "research.py").read_text(encoding="utf-8")
        self.assertEqual(static_guards.absent_module_problems(src, ["costar_import"]), [])
        bad = src.replace("import hazards", "import costar_import\nimport hazards", 1)
        self.assertTrue(static_guards.absent_module_problems(bad, ["costar_import"]))

    def test_bc04_land_readers_still_filter_land(self):
        for sql in (research._SQL_SUBMARKET_LAND_MEDIAN, research._SQL_SUBMARKET_LAND_MEDIAN_BATCH,
                    reporting._SQL_FETCH_NEARBY_SALES_COMPS):
            self.assertIn("comp_type = 'land'", sql)


# ---------------------------------------------------------------------------
# BC-05 PII and licensed detail
# ---------------------------------------------------------------------------
class PrivacyTests(unittest.TestCase):
    def _import_both(self):
        conn = _CompsConn()
        rc, base, out = run_import(self, {"b": [BUILDING_HEADER, building_row()],
                                          "l": [LEASE_HEADER, lease_row()]}, conn)
        self.assertEqual(rc, 0)
        return conn, base, out

    def test_bc05_raw_holds_only_allowlisted_keys_and_no_contact(self):
        conn, base, _ = self._import_both()
        (b,) = conn.run(costar_ingest._SQL_INSERT_BUILDING_SALES)
        (l,) = conn.run(costar_ingest._SQL_INSERT_LEASING_COMP)
        self.assertLessEqual(set(json.loads(b[-1])), ci.BUILDING_RAW_KEYS)
        self.assertLessEqual(set(json.loads(l[-1])), ci.LEASE_RAW_KEYS)
        self.assertEqual((b[7], b[8]), (None, None))         # buyer, seller
        self.assertEqual((l[2], l[3], l[4]), (None, None, None))   # tenant, industry, naics
        for cell in SECRET_CELLS[:-2]:                       # addresses are stored, not shown
            self.assertNotIn(cell, json.dumps([b, l], default=str))
        for snap in (ci.BUILDING_SNAPSHOT_NAME, ci.LEASE_SNAPSHOT_NAME):
            text = (base / snap).read_text(encoding="utf-8")
            for cell in SECRET_CELLS[:-2]:
                self.assertNotIn(cell, text, snap)

    def test_bc05_console_shows_counts_and_reasons_only(self):
        _, _, out = self._import_both()
        for cell in SECRET_CELLS:
            self.assertNotIn(cell, out)
        self.assertNotIn("20000000", out)
        self.assertNotIn("6.5", out)

    def test_bc05_no_header_row_refusal_names_only_expected_headers(self):
        rows = [building_row() for _ in range(3)]            # data rows, no header
        rc, _, out = run_import(self, {"x": rows}, _CompsConn())
        self.assertEqual(rc, 1)
        self.assertIn("'SF Leased'", out)
        for cell in SECRET_CELLS + ("Industrial", "Warehouse", "Investment Triple Net"):
            self.assertNotIn(cell, out)

    def test_bc05_missing_column_refusal_names_only_expected_headers(self):
        header = [h for h in BUILDING_HEADER if h != "Sale Condition"]
        row = [v for h, v in zip(BUILDING_HEADER, building_row()) if h != "Sale Condition"]
        rc, _, out = run_import(self, {"x": [header, row]}, _CompsConn())
        self.assertEqual(rc, 1)
        self.assertIn("'Sale Condition' or 'Sale Conditions'", out)
        for cell in SECRET_CELLS:
            self.assertNotIn(cell, out)

    def test_bc05_memo_lines_are_aggregates_only(self):
        conn = _ReadConn(leases=_leases("atlanta__alpha", [5, 6, 7, 8, 9]),
                         sales=_sales("atlanta__alpha", [90, 100, 110, 120, 130]),
                         resolved=[("99Z-1", "atlanta__alpha")],
                         names=[("atlanta__alpha", "Alpha Park")])
        bc = reporting.fetch_building_comps(conn, "atlanta", ["99Z-1"], date(2026, 11, 6))
        md = reporting._render_building_comps_section(
            bc, [{"parcel_id": "99Z-1"}], {"99Z-1": _projection()})
        for cell in SECRET_CELLS:
            self.assertNotIn(cell, md)
        self.assertNotIn("$90/SF", md)                       # no single-comp price
        self.assertIn("n=5", md)


# ---------------------------------------------------------------------------
# BC-06 rent units and service type; BC-19 lease types
# ---------------------------------------------------------------------------
class LeaseFilterTests(unittest.TestCase):
    def test_bc06_each_non_comparable_rent_is_dropped_with_its_reason(self):
        rows = [LEASE_HEADER,
                lease_row(**{"Property Address": "a", "Starting Rent/SF/Yr": 0.55}),
                lease_row(**{"Property Address": "b", "Service Type": "Industrial Gross"}),
                lease_row(**{"Property Address": "c", "Service Type": "Modified Gross"}),
                lease_row(**{"Property Address": "d", "Starting Rent/SF/Yr": None,
                             "Asking Rent/SF/Yr": 7.0}),
                lease_row(**{"Property Address": "e", "Starting Rent/SF/Yr": "Withheld"}),
                lease_row(**{"Property Address": "f", "Rent Type": "Asking"}),
                lease_row(**{"Property Address": "g", "Rent Type": "Effective"})]
        res = ci.convert_lease(rows, today=TODAY)
        self.assertEqual(res.rows, [])
        self.assertEqual(res.dropped, {
            "rent outside $2-$25/SF/yr (monthly or implausible)": 1,
            "service type not NNN": 2, "asking rent only": 2,
            "rent withheld or not disclosed": 1, "not a starting rent": 1})

    def test_bc06_median_of_kept_rents_is_hand_computed(self):
        rents = (5.0, 9.0, 6.0, 7.5)
        rows = [LEASE_HEADER] + [lease_row(**{"Property Address": f"{i} Test St",
                                              "Starting Rent/SF/Yr": r,
                                              "Service Type": ("Triple Net", "NNN")[i % 2]})
                                 for i, r in enumerate(rents)]
        kept = [r["rent_psf_nnn"] for r in ci.convert_lease(rows, today=TODAY).rows]
        self.assertEqual(sorted(kept), [5.0, 6.0, 7.5, 9.0])
        self.assertEqual(reporting.comp_median(kept), 6.75)  # (6.0 + 7.5) / 2

    def test_bc06_generic_annual_rent_needs_a_rent_type(self):
        header = [("Rent/SF/Yr" if h == "Starting Rent/SF/Yr" else h) for h in LEASE_HEADER]
        res = ci.convert_lease([header, lease_row(), lease_row(**{
            "Property Address": "z", "Rent Type": None})], today=TODAY)
        self.assertEqual(len(res.rows), 1)
        self.assertEqual(res.dropped, {"rent type not stated": 1})
        no_type = [h for h in header if h != "Rent Type"]
        with self.assertRaises(ci.ImportError_) as cm:
            ci.sheet_role([no_type])
        self.assertIn("'Rent Type'", str(cm.exception))

    def test_bc06_snapshot_rent_lines_carry_distinct_labels(self):
        conn = _ReadConn(leases=_leases("atlanta__alpha", [5, 6, 7, 8, 9]),
                         resolved=[("99Z-1", "atlanta__alpha")])
        bundle = _snapshot_bundle(reporting.fetch_building_comps(
            conn, "atlanta", ["99Z-1"], date(2026, 11, 6)))
        md = reporting._render_snapshot_markdown(bundle, params=_params())
        self.assertIn("- **Submarket asking rent (NNN)**: $8.25", md)
        self.assertIn("- **Signed NNN starting rent from lease comps**: $7.00/SF/yr (n=5", md)

    def test_bc19_suites_duplicates_subleases_and_renewals(self):
        rows = [LEASE_HEADER,
                lease_row(),
                lease_row(**{"SF Leased": 150_000}),            # another suite: kept
                lease_row(),                                     # exact duplicate
                lease_row(**{"Property Address": "s", "Lease Type": "Sublet"}),
                lease_row(**{"Property Address": "t", "Deal Type": "Sublease"}),
                lease_row(**{"Property Address": "r", "Deal Type": "Renewal"}),
                lease_row(**{"Property Address": "x", "Deal Type": "Extension"})]
        res = ci.convert_lease(rows, today=TODAY)
        self.assertEqual(len(res.rows), 2)
        self.assertEqual(res.dropped, {"duplicate address, sign date and SF": 1,
                                       "sublease": 2, "renewal or extension": 2})


def _params():
    return {"scoring_weights": {}, "composite_threshold": 70,
            "hard_filters": {"acreage_min": 10, "acreage_max": 50},
            "discovery": {"off_market_discovery_pct_minimum": 50}}


def _snapshot_bundle(bc=None, projection=None):
    bundle = {
        "parcel": {"parcel_id": "99Z-1", "market": "atlanta", "submarket": "atlanta__alpha",
                   "address": "99 Test St", "acreage": 20.0},
        "score": {"composite_score": 75.0, "actionability": "PASS", "sub_scores": {},
                  "strategy_fit": {}, "primary_strategy": "bts"},
        "market_context": {"asking_rent_nnn_psf": 8.25, "as_of_date": "2026-09-30",
                           "source": "costar"},
        "comps": [], "flags": [], "submarket_name": "Alpha Park",
        "projection": projection,
    }
    if bc is not None:
        bundle["building_comps"] = bc
    return bundle


# ---------------------------------------------------------------------------
# BC-07 comparable building sales
# ---------------------------------------------------------------------------
class BuildingFilterTests(unittest.TestCase):
    def test_bc07_each_exclusion_is_dropped_with_its_reason(self):
        cases = {
            "not industrial": {"Property Type": "Office"},
            "secondary type not warehouse or distribution": {"Secondary Type": "Data Hosting"},
            "not sold": {"Sale Status": "Under Contract"},
            "older than 36 months": {"Sale Date": serial(date(2023, 10, 1))},
            "sale condition: portfolio": {"Sale Condition": "Bulk/Portfolio Sale"},
            "sale condition: partial interest": {"Sale Condition": "Partial Interest"},
            "sale condition: condo": {"Sale Condition": "Condo Conversion"},
            "sale condition: sale-leaseback": {"Sale Condition": "Sale Leaseback"},
            "sale condition: distressed": {"Sale Condition": "REO Sale"},
            "sale condition: auction": {"Sale Condition": "Auction Sale"},
            "sale condition: redevelopment or land value": {"Sale Condition": "Redevelopment Project"},
            "sale condition: non-arm's length": {"Sale Condition": "Non-Arm's Length"},
            "sale condition: leased fee or ground lease": {"Sale Condition": "Ground Lease (Leased Fee)"},
            "sale condition: business value included": {"Sale Condition": "Business Value Included"},
            "part of a portfolio or multi-property sale": {"Property Name": "Part of a 4 Property Sale"},
            "outside 100,000-1,000,000 SF": {"RBA": 60_000, "Price Per SF": None,
                                             "Sale Price": 6_000_000},
            "not built within 15 years": {"Year Built": 1995},
            "price per SF disagrees with price / building SF": {"Price Per SF": 140},
            "price per SF outside $30-$400": {"Sale Price": 4_000_000, "Price Per SF": None},
            "location outside Georgia": {"Latitude": -84.5, "Longitude": 33.7},
        }
        multi = building_row(**{"Number Of Properties": 3, "Property Address": "m"})
        flex = building_row(**{"Secondary Type": "Flex", "Property Address": "f"})
        truck = building_row(**{"Secondary Type": "Truck Terminal", "Property Address": "t"})
        rows = [BUILDING_HEADER] + [building_row(**{**kw, "Property Address": f"{i} x"})
                                    for i, kw in enumerate(cases.values())] + [multi, flex, truck]
        res = ci.convert_building(rows, today=TODAY)
        self.assertEqual(res.rows, [])
        expected = {k: 1 for k in cases}
        expected["part of a portfolio or multi-property sale"] = 2
        expected["secondary type not warehouse or distribution"] = 3
        self.assertEqual(res.dropped, expected)

    def test_bc07_kept_medians_are_hand_computed(self):
        spec = [(90, 0.05), (100, 0.055), (120, 0.06), (150, None)]
        rows = [BUILDING_HEADER] + [building_row(**{
            "Property Address": f"{i} Test St", "Sale Price": psf * 200_000, "Price Per SF": psf,
            "Actual Cap Rate": cap}) for i, (psf, cap) in enumerate(spec)]
        kept = ci.convert_building(rows, today=TODAY).rows
        self.assertEqual(reporting.comp_median([r["price_psf"] for r in kept]), 110.0)
        self.assertEqual(reporting.comp_median([r["cap_rate"] for r in kept if r["cap_rate"]]),
                         0.055)


# ---------------------------------------------------------------------------
# BC-08 exit value
# ---------------------------------------------------------------------------
class ExitValueTests(unittest.TestCase):
    def test_bc08_no_projection_or_nothing_fits_gives_no_line(self):
        rent, cap, psf = _stat(6.5), _stat(0.055), _stat(120.0)
        self.assertIsNone(reporting.exit_value_text(None, rent, cap, psf))
        self.assertIsNone(reporting.exit_value_text(_projection(status=site_fit.EMPTY),
                                                    rent, cap, psf))
        self.assertIsNone(reporting.exit_value_text(_projection(fits=False), rent, cap, psf))

    def test_bc08_draft_projection_carries_its_label(self):
        text = reporting.exit_value_text(_projection(), _stat(6.5), _stat(0.055), None)
        self.assertTrue(text.startswith("Exit value estimate (draft rules): $36.9M"))
        self.assertIn("an estimate, not an appraisal", text)

    def test_bc08_no_cap_rates_falls_back_to_sale_price_per_sf(self):
        text = reporting.exit_value_text(_projection(), _stat(6.5), None, _stat(120.0))
        self.assertIn("$37.4M = the projected 312,000 SF cross-dock x building sale price $120/SF",
                      text)
        self.assertIn("no income method", text)

    def test_bc08_too_few_comps_gives_no_line(self):
        conn = _ReadConn(leases=_leases("atlanta__alpha", [6] * 4),
                         sales=_sales("atlanta__alpha", [100] * 4, [0.05] * 4),
                         resolved=[("99Z-1", "atlanta__alpha")])
        bc = reporting.fetch_building_comps(conn, "atlanta", ["99Z-1"], date(2026, 11, 6))
        rent, _ = reporting.comp_stat(bc, "rent", "atlanta__alpha")
        cap, _ = reporting.comp_stat(bc, "cap", "atlanta__alpha")
        psf, _ = reporting.comp_stat(bc, "psf", "atlanta__alpha")
        self.assertEqual((rent, cap, psf), (None, None, None))
        self.assertIsNone(reporting.exit_value_text(_projection(), rent, cap, psf))

    def test_bc08_exact_rendered_snapshot_lines(self):
        conn = _ReadConn(
            leases=_leases("atlanta__alpha", [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]),
            sales=_sales("atlanta__alpha", [100.0, 110.0, 120.0, 130.0, 140.0],
                         [0.05, 0.055, 0.06, None, 0.07]) + _sales("atlanta__beta", [90.0], [0.065]),
            resolved=[("99Z-1", "atlanta__alpha")], names=[("atlanta__alpha", "Alpha Park")])
        bc = reporting.fetch_building_comps(conn, "atlanta", ["99Z-1"], date(2026, 11, 6))
        md = reporting._building_comps_snapshot_lines(_snapshot_bundle(bc, _projection()))
        self.assertEqual(md, "\n".join([
            "",
            "- **Signed NNN starting rent from lease comps**: $7.50/SF/yr (n=6, Alpha Park "
            "submarket, signed 2024-11-06 to 2026-11-06)",
            "- **Building sale comps**: $120/SF (n=5, Alpha Park submarket, sold 2023-11-06 to "
            "2026-11-06); cap rate 6.0% (n=5, Atlanta market-wide; 4 in the submarket, sold "
            "2023-11-06 to 2026-11-06)",
            "- **Exit value estimate** (draft rules): $39.0M = the projected 312,000 SF "
            "cross-dock x signed NNN starting rent from lease comps $7.50/SF/yr / cap rate 6.0% "
            "(rent n=6, Alpha Park submarket, signed 2024-11-06 to 2026-11-06; cap rate n=5, "
            "Atlanta market-wide; 4 in the submarket, sold 2023-11-06 to 2026-11-06); an "
            "estimate, not an appraisal",
        ]))


# ---------------------------------------------------------------------------
# BC-09 origins, deletes, duplicates
# ---------------------------------------------------------------------------
class OriginTests(unittest.TestCase):
    def rows(self):
        return ci.convert_building([BUILDING_HEADER, building_row(),
                                    building_row(**{"Property Address": "3 Test St"})],
                                   today=TODAY).rows

    def test_bc09_delete_is_scoped_to_type_origin_tool_rows_and_market(self):
        self.assertNotEqual(ci.BUILDING_ORIGIN, ci.LEASE_ORIGIN)
        self.assertNotIn(ci.LAND_ORIGIN, (ci.BUILDING_ORIGIN, ci.LEASE_ORIGIN))
        for sql in (ci._SQL_DELETE_TOOL_BUILDING_COMPS, ci._SQL_DELETE_TOOL_LEASE_COMPS):
            self.assertIn(ci._SQL_TOOL_ROW, sql)
            self.assertIn("raw ? 'imported_on'", sql)
            self.assertIn("NOT (raw ? 'submarket')", sql)
            self.assertIn(ci._SQL_IN_MARKET, sql)
        self.assertIn("comp_type = 'building'", ci._SQL_DELETE_TOOL_BUILDING_COMPS)
        conn = _CompsConn()
        ci.write_comps(conn, ci.BUILDING_KIND, self.rows(), TODAY)
        self.assertEqual(conn.run(ci._SQL_DELETE_TOOL_BUILDING_COMPS),
                         [(ci.BUILDING_ORIGIN, "atlanta")])
        self.assertFalse(conn.run(ci._SQL_DELETE_TOOL_LAND_COMPS))
        self.assertEqual(conn.transactions, 1)

    def test_bc09_a_comp_another_origin_delivered_is_stored_once(self):
        conn = _CompsConn(other_building=[("1 TEST ST", date(2026, 3, 2))])
        out = ci.write_comps(conn, ci.BUILDING_KIND, self.rows(), TODAY)
        self.assertEqual((out["assigned"], out["already_delivered"]), (1, 1))
        (ins,) = conn.run(costar_ingest._SQL_INSERT_BUILDING_SALES)
        self.assertEqual(ins[0], "3 Test St")
        lease = ci.convert_lease([LEASE_HEADER, lease_row()], today=TODAY).rows
        conn = _CompsConn(other_lease=[("2 Test St", date(2026, 7, 1), 250000.0)])
        out = ci.write_comps(conn, ci.LEASE_KIND, lease, TODAY)
        self.assertEqual((out["already_delivered"], out["kept_previous"]), (1, 1))
        self.assertFalse(conn.run(ci._SQL_DELETE_TOOL_LEASE_COMPS))

    def test_bc09_reimport_is_idempotent(self):
        a, b = _CompsConn(), _CompsConn()
        ci.write_comps(a, ci.BUILDING_KIND, self.rows(), TODAY)
        ci.write_comps(b, ci.BUILDING_KIND, self.rows(), TODAY)
        self.assertEqual(a.executed, b.executed)

    def test_bc09_the_importer_never_drops_csvs_into_the_ingest_folders(self):
        conn = _CompsConn()
        _, base, _ = run_import(self, {"b": [BUILDING_HEADER, building_row()],
                                       "l": [LEASE_HEADER, lease_row()]}, conn)
        self.assertEqual(sorted(p.name for p in base.iterdir()),
                         sorted([ci.BUILDING_SNAPSHOT_NAME, ci.LEASE_SNAPSHOT_NAME]))


# ---------------------------------------------------------------------------
# BC-10 snapshots, reassignment, transactions
# ---------------------------------------------------------------------------
class SnapshotTests(unittest.TestCase):
    AREAS = {"written": 1, "kept_human_seeded": 0, "cleared_stale": 0, "name_drift": 0}

    def test_bc10_redrawn_areas_reassign_saved_building_and_lease_rows(self):
        rc, base, _ = run_import(self, {"b": [BUILDING_HEADER, building_row()],
                                        "l": [LEASE_HEADER, lease_row()]}, _CompsConn())
        self.assertEqual(rc, 0)
        conn = _CompsConn()
        with mock.patch.object(ci, "write_areas", return_value=self.AREAS) as wa:
            rc, _, _ = run_import(self, {"c": AREA_ONLY}, conn, base=base, locator=_locator())
        self.assertEqual(rc, 0)
        wa.assert_called_once()
        self.assertEqual(len(conn.run(costar_ingest._SQL_INSERT_BUILDING_SALES)), 1)
        self.assertEqual(len(conn.run(costar_ingest._SQL_INSERT_LEASING_COMP)), 1)

    def test_bc10_saved_rows_are_refiltered_for_age(self):
        rows = ci.convert_lease([LEASE_HEADER, lease_row()], today=TODAY).rows
        keep, n_old = ci.refilter_lease(rows, date(2028, 6, 1))
        self.assertEqual((keep, n_old), ([], 1))
        b = ci.convert_building([BUILDING_HEADER, building_row()], today=TODAY).rows
        self.assertEqual(ci.refilter_building(b, TODAY), (b, 0))
        self.assertEqual(ci.refilter_building([{"junk": 1}, "x"], TODAY), ([], 2))

    def test_bc10_no_areas_defers_and_touches_nothing(self):
        conn = _CompsConn(n_areas=0)
        rc, base, out = run_import(self, {"l": [LEASE_HEADER, lease_row()]}, conn)
        self.assertEqual(rc, 0)
        self.assertEqual(_sqls(conn), [ci._SQL_COUNT_MARKET_AREAS])
        self.assertTrue((base / ci.LEASE_SNAPSHOT_NAME).is_file())
        self.assertIn("no submarket areas exist yet", out)

    def test_bc10_nothing_assignable_keeps_the_earlier_rows(self):
        conn = _CompsConn(assign=lambda n: [(i, None) for i in range(1, n + 1)])
        out = ci.write_comps(conn, ci.LEASE_KIND,
                             ci.convert_lease([LEASE_HEADER, lease_row()], today=TODAY).rows, TODAY)
        self.assertEqual(out["kept_previous"], 1)
        self.assertFalse(conn.run(ci._SQL_DELETE_TOOL_LEASE_COMPS))

    def test_bc10_a_failed_write_rolls_back_that_table_and_reports_what_committed(self):
        import psycopg

        class Boom(_CompsConn):
            rolled_back = 0

            @contextlib.contextmanager
            def transaction(self):
                self.transactions += 1
                try:
                    yield
                except Exception:
                    Boom.rolled_back += 1
                    raise

            def answer(self, sql, params):
                if sql == costar_ingest._SQL_INSERT_LEASING_COMP:
                    raise psycopg.OperationalError("injected")
                return super().answer(sql, params)

        conn = Boom()
        rc, base, out = run_import(self, {"b": [BUILDING_HEADER, building_row()],
                                          "l": [LEASE_HEADER, lease_row()]}, conn)
        self.assertEqual(rc, 2)
        self.assertEqual(Boom.rolled_back, 1)
        self.assertEqual(conn.commits, 1)                    # the building table only
        self.assertIn("Committed before it: building comps.", out)
        self.assertTrue((base / ci.BUILDING_SNAPSHOT_NAME).is_file())
        self.assertFalse((base / ci.LEASE_SNAPSHOT_NAME).exists())

    def test_bc10_unreadable_snapshot_never_blocks_the_area_import(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, d, True)
        (d / ci.BUILDING_SNAPSHOT_NAME).write_text("{not json", encoding="utf-8")
        (d / ci.LEASE_SNAPSHOT_NAME).write_text('{"version": 1, "rows": [1]}', encoding="utf-8")
        conn = _CompsConn()
        with mock.patch.object(ci, "write_areas", return_value=self.AREAS) as wa:
            rc, _, out = run_import(self, {"c": AREA_ONLY}, conn, base=d, locator=_locator())
        self.assertEqual(rc, 0)
        wa.assert_called_once()
        self.assertEqual(conn.executed, [])
        self.assertIn("building snapshot is unreadable", out)
        self.assertIn("lease snapshot is unreadable", out)


# ---------------------------------------------------------------------------
# BC-11 units, CRS, dates, terms, SF columns
# ---------------------------------------------------------------------------
class UnitsTests(unittest.TestCase):
    def test_bc11_swapped_coordinates_are_outside_georgia(self):
        res = ci.convert_lease([LEASE_HEADER, lease_row(Latitude=-84.5, Longitude=33.7)],
                               today=TODAY)
        self.assertEqual(res.dropped, {"location outside Georgia": 1})

    def test_bc11_points_go_through_the_parcel_rule_as_lon_lat(self):
        conn = _CompsConn()
        ci.write_comps(conn, ci.LEASE_KIND,
                       ci.convert_lease([LEASE_HEADER, lease_row()], today=TODAY).rows, TODAY)
        (assign,) = conn.run(ci._SQL_ASSIGN_POINTS_TO_AREAS)
        self.assertEqual(assign, ("atlanta", 3000, [-84.5], [33.7]))

    def test_bc11_1904_workbook(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, d, True)
        row = lease_row(**{"Sign Date": serial(date(2026, 5, 1)) - 1462,
                           "Start Date": serial(date(2026, 7, 1)) - 1462})
        path = make_xlsx(d / "x.xlsx", {"l": [LEASE_HEADER, row]})
        with zipfile.ZipFile(path) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
            b"<sheets>", b'<workbookPr date1904="1"/><sheets>')
        with zipfile.ZipFile(path, "w") as z:
            for n, b in parts.items():
                z.writestr(n, b)
        self.assertTrue(ci.xlsx_date1904(path))
        rows = list(ci.read_xlsx(path).values())[0]
        (kept,) = ci.convert_lease(rows, today=TODAY, date1904=True).rows
        self.assertEqual((kept["sign_date"], kept["start_date"]), ("2026-05-01", "2026-07-01"))

    def test_bc11_text_dates_and_sign_date_for_recency(self):
        (kept,) = ci.convert_lease([LEASE_HEADER, lease_row(**{
            "Sign Date": "5/1/2026", "Start Date": "2026-07-01"})], today=TODAY).rows
        self.assertEqual((kept["sign_date"], kept["start_date"]), ("2026-05-01", "2026-07-01"))
        # Signed 25 months ago, started 20 months ago: too old (sign date decides).
        res = ci.convert_lease([LEASE_HEADER, lease_row(**{
            "Sign Date": "2024-10-01", "Start Date": "2025-03-01"})], today=TODAY)
        self.assertEqual(res.dropped, {"signed more than 24 months ago": 1})

    def test_bc11_term_strings(self):
        for text, months, want in (("5 Years 3 Months", None, 63), ("63 Months", None, 63),
                                   ("7.5 yrs", None, 90), ("10 Years", None, 120),
                                   ("62", None, None), (62, None, None), (None, 84, 84),
                                   ("forever", None, None), (None, None, None),
                                   ("999 Years", None, None)):
            self.assertEqual(ci.lease_term_months(text, months), want, (text, months))

    def test_bc11_sf_column_precedence(self):
        header = BUILDING_HEADER + ["Building SF", "Total SF"]
        row = building_row() + [500_000, 600_000]            # RBA 200,000 wins
        (kept,) = ci.convert_building([header, row], today=TODAY).rows
        self.assertEqual(kept["building_sf"], 200_000)
        only_total = [("Total SF" if h == "RBA" else h) for h in BUILDING_HEADER]
        (kept,) = ci.convert_building([only_total, building_row()], today=TODAY).rows
        self.assertEqual(kept["building_sf"], 200_000)
        # A lease's SF is the SF leased, never the building's RBA.
        (lease,) = ci.convert_lease([LEASE_HEADER, lease_row()], today=TODAY).rows
        self.assertEqual((lease["sf_leased"], lease["building_rba"]), (250_000, 400_000))


# ---------------------------------------------------------------------------
# BC-12 exact headers, aliases, refusals
# ---------------------------------------------------------------------------
class HeaderTests(unittest.TestCase):
    def test_bc12_every_building_alias_is_read(self):
        base_names = {f: a[0] for f, a in ci.BUILDING_COLUMNS.items()}
        for f, aliases in ci.BUILDING_COLUMNS.items():
            for alias in aliases:
                header = [alias if h == base_names[f] else h for h in BUILDING_HEADER]
                if base_names[f] not in BUILDING_HEADER:
                    continue
                with self.subTest(alias=alias):
                    shouted = [f"  {h.upper()} " for h in header]
                    cols = ci._columns(ci._folded_header([shouted], [ci._BUILDING_MARKERS])[0],
                                       ci.BUILDING_COLUMNS)
                    self.assertEqual(cols[f], header.index(alias))
                    (kept,) = ci.convert_building([shouted, building_row()], today=TODAY).rows
                    self.assertEqual(kept["price_psf"], 100.0)

    def test_bc12_every_lease_alias_is_read(self):
        base_names = {f: a[0] for f, a in ci.LEASE_COLUMNS.items()}
        for f, aliases in ci.LEASE_COLUMNS.items():
            if base_names[f] not in LEASE_HEADER:
                continue
            for alias in aliases:
                header = [alias if h == base_names[f] else h for h in LEASE_HEADER]
                with self.subTest(alias=alias):
                    lower = [h.lower() for h in header]
                    cols = ci._columns(ci._folded_header([lower], [ci._LEASE_MARKERS])[0],
                                       ci.LEASE_COLUMNS)
                    self.assertEqual(cols[f], header.index(alias))
                    res = ci.convert_lease([lower, lease_row()], today=TODAY)
                    self.assertEqual(len(res.rows), 1, res.dropped)

    def test_bc12_no_substring_matching(self):
        header = [("Starting Rent/SF/Yr (Est.)" if h == "Starting Rent/SF/Yr" else h)
                  for h in LEASE_HEADER]
        with self.assertRaises(ci.ImportError_) as cm:
            ci.sheet_role([header, lease_row()])
        self.assertIn("'Starting Rent/SF/Yr' or 'Starting Rent/SF' or 'Starting Rent'",
                      str(cm.exception))

    def test_bc12_price_per_sf_land_is_never_the_building_price_per_sf(self):
        header = [h for h in BUILDING_HEADER if h != "Price Per SF"]
        row = [v for h, v in zip(BUILDING_HEADER, building_row(**{"Price Per SF Land": 7}))
               if h != "Price Per SF"]
        cols = ci._columns(ci._folded_header([header], [ci._BUILDING_MARKERS])[0],
                           ci.BUILDING_COLUMNS)
        self.assertNotIn("price_psf", cols)
        (kept,) = ci.convert_building([header, row], today=TODAY).rows
        self.assertEqual(kept["price_psf"], 100.0)

    def test_bc12_lookalike_sheet_missing_columns_is_refused_exit_1(self):
        rc, _, out = run_import(self, {"x": [["RBA", "Sale Price", "Sale Date"]]}, _CompsConn())
        self.assertEqual(rc, 1)
        self.assertIn("looks like CoStar building sale comps", out)
        self.assertIn("'Sale Status'", out)
        rc, _, out = run_import(self, {"x": [["SF Leased", "Property Address"]]}, _CompsConn())
        self.assertEqual(rc, 1)
        self.assertIn("'Sign Date' or 'Lease Sign Date'", out)


# ---------------------------------------------------------------------------
# BC-13 minimum count, scope, window
# ---------------------------------------------------------------------------
class ScopeTests(unittest.TestCase):
    def _bc(self, sub_n, other_n):
        conn = _ReadConn(leases=_leases("atlanta__alpha", [6.0] * sub_n)
                         + _leases("atlanta__beta", [8.0] * other_n),
                         resolved=[("99Z-1", "atlanta__alpha")],
                         names=[("atlanta__alpha", "Alpha Park")])
        return reporting.fetch_building_comps(conn, "atlanta", ["99Z-1"], date(2026, 11, 6)), conn

    def test_bc13_min_minus_one_gives_no_submarket_value(self):
        bc, _ = self._bc(ci.MIN_COMPS - 1, 0)
        stat, why = reporting.comp_stat(bc, "rent", "atlanta__alpha")
        self.assertIsNone(stat)
        self.assertEqual(why, "n=4 in the submarket, 4 market-wide; at least 5 needed")
        bc, _ = self._bc(ci.MIN_COMPS, 0)
        self.assertEqual(reporting.comp_stat(bc, "rent", "atlanta__alpha")[0].label,
                         "Alpha Park submarket")

    def test_bc13_market_wide_fallback_is_labelled(self):
        bc, _ = self._bc(2, 4)
        stat, _ = reporting.comp_stat(bc, "rent", "atlanta__alpha")
        self.assertEqual((stat.n, stat.label, stat.value),
                         (6, "Atlanta market-wide; 2 in the submarket", 8.0))   # 6, 6, 8, 8, 8, 8

    def test_bc13_windows_are_parameters_counted_from_the_injected_day(self):
        _, conn = self._bc(5, 0)
        params = dict(conn.executed)
        self.assertEqual(params[reporting._SQL_BC_LEASES][-2:], ("2024-11-06", "2026-11-06"))
        self.assertEqual(params[reporting._SQL_BC_SALES][-2:],
                         (date(2023, 11, 6), date(2026, 11, 6)))
        for sql in (reporting._SQL_BC_LEASES, reporting._SQL_BC_SALES):
            self.assertNotIn("CURRENT_DATE", sql)
            self.assertNotIn("NOW()", sql.upper())

    def test_bc13_import_window_boundary(self):
        day = ci.months_before(TODAY, ci.LEASE_WINDOW_MONTHS)
        res = ci.convert_lease([LEASE_HEADER, lease_row(**{"Sign Date": day.isoformat()}),
                                lease_row(**{"Sign Date": date.fromordinal(day.toordinal() - 1)
                                             .isoformat(), "Property Address": "q"})],
                               today=TODAY)
        self.assertEqual(len(res.rows), 1)
        self.assertEqual(res.dropped, {"signed more than 24 months ago": 1})


# ---------------------------------------------------------------------------
# BC-15 bounded reads, failure isolation; BC-16 byte identity
# ---------------------------------------------------------------------------
class MemoReadTests(unittest.TestCase):
    def test_bc15_read_count_is_independent_of_the_parcel_count(self):
        counts = []
        for n in (1, 10, 200):
            ids = [f"99Z-{i}" for i in range(n)]
            conn = _ReadConn(leases=_leases("atlanta__alpha", [6.0] * 5),
                             resolved=[(i, "atlanta__alpha") for i in ids],
                             names=[("atlanta__alpha", "Alpha Park")])
            reporting.fetch_building_comps(conn, "atlanta", ids, date(2026, 11, 6))
            counts.append(len(conn.executed))
        self.assertEqual(counts, [4, 4, 4])
        conn = _ReadConn()
        self.assertIsNone(reporting.fetch_building_comps(conn, "atlanta", ["a"], date(2026, 11, 6)))
        self.assertEqual(len(conn.executed), 2)               # no tool rows: two reads only

    def test_bc15_an_sql_error_renders_nothing_and_leaves_the_connection_usable(self):
        class Info:
            transaction_status = 2
        conn = _ReadConn(leases=_leases("atlanta__alpha", [6.0] * 5),
                         fail=reporting._SQL_BC_SALES, info=Info())
        self.assertIsNone(reporting.fetch_building_comps(conn, "atlanta", ["a"], date(2026, 11, 6)))
        self.assertEqual(conn.savepoints, 2)                  # each read in a SAVEPOINT

        class Idle:
            transaction_status = 0
        conn = _ReadConn(fail=reporting._SQL_BC_LEASES, info=Idle())
        self.assertIsNone(reporting.fetch_building_comps(conn, "atlanta", ["a"], date(2026, 11, 6)))
        self.assertEqual(conn.rollbacks, 1)
        md = reporting._render_memo_markdown("atlanta", None, [], [], [], params=_params(),
                                             today="2026-11-06", building_comps=None)
        self.assertNotIn("Building Comps", md)

    def test_bc16_no_tool_rows_is_byte_identical(self):
        rows = [{"parcel_id": "99Z-1", "composite_score": 80.0, "actionability": "PASS",
                 "primary_strategy": "bts", "submarket": "atlanta__alpha", "address": "a",
                 "acreage": 20.0, "owner_name": "o"}]
        kw = dict(params=_params(), today="2026-11-06", projections={"99Z-1": _projection()})
        plain = reporting._render_memo_markdown("atlanta", "c1", rows, [], [], **kw)
        empty = reporting._render_memo_markdown(
            "atlanta", "c1", rows, [], [],
            building_comps=reporting.fetch_building_comps(_ReadConn(), "atlanta", ["99Z-1"],
                                                          date(2026, 11, 6)), **kw)
        self.assertEqual(plain, empty)
        snap = reporting._render_snapshot_markdown(_snapshot_bundle(), params=_params())
        self.assertIn("- **As of**: 2026-09-30 (costar)\n\n### Ownership", snap)
        self.assertNotIn("lease comps", snap)
        with_rows = reporting._render_memo_markdown(
            "atlanta", "c1", rows, [], [],
            building_comps=reporting.fetch_building_comps(
                _ReadConn(leases=_leases("atlanta__alpha", [6.0] * 5),
                          resolved=[("99Z-1", "atlanta__alpha")]),
                "atlanta", ["99Z-1"], date(2026, 11, 6)), **kw)
        self.assertIn("## Building Comps (CoStar; estimates)", with_rows)
        self.assertEqual(with_rows.replace(with_rows[with_rows.index("## Building Comps"):
                                                     with_rows.index("## Open Questions")], ""),
                         plain)


# ---------------------------------------------------------------------------
# BC-17 run state; BC-18 no network; BC-14 contract
# ---------------------------------------------------------------------------
class ContractTests(unittest.TestCase):
    def test_bc17_building_and_lease_imports_are_refused_after_the_baseline(self):
        for sheets in ({"b": [BUILDING_HEADER, building_row()]},
                       {"l": [LEASE_HEADER, lease_row()]}):
            conn = _CompsConn()
            rc, base, out = run_import(self, sheets, conn, baseline=True)
            self.assertEqual(rc, 3)
            self.assertEqual(conn.executed, [])
            self.assertFalse(base.exists())

    def test_bc18_building_and_lease_imports_make_no_network_call(self):
        with mock.patch.object(ci.requests, "Session") as sess, \
             mock.patch.object(ci.requests, "get") as get, \
             mock.patch.object(ci, "FultonLocator") as loc:
            rc, _, _ = run_import(self, {"b": [BUILDING_HEADER, building_row()],
                                         "l": [LEASE_HEADER, lease_row()]}, _CompsConn())
        self.assertEqual(rc, 0)
        sess.assert_not_called()
        get.assert_not_called()
        loc.assert_not_called()

    def test_bc18_fixtures_are_obviously_synthetic(self):
        self.assertTrue(building_row()[0].endswith("Test St"))
        self.assertTrue(all(str(r[1]).startswith("99Z") for r in AREA_ONLY[1:]))

    def test_bc14_no_new_module_and_tool_stays_out_of_the_sandbox(self):
        self.assertFalse((REPO_ROOT / "building_comps.py").exists())
        tree = ast.parse((REPO_ROOT / "costar_import.py").read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertFalse(names & {"research", "runner", "reporting"})

    def test_bc14_new_sql_is_module_level_constants(self):
        for module in (ci, reporting):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "execute" and node.args):
                    self.assertIsInstance(node.args[0], (ast.Name, ast.Attribute, ast.Constant),
                                          ast.dump(node.args[0])[:80])


if __name__ == "__main__":
    unittest.main()
