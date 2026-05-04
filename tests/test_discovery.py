"""Offline tests for the Phase 3 Fulton County discovery connector.

Match Phase 2's tests/test_harness.py: stdlib unittest + unittest.mock,
no new test deps. All HTTP is mocked via patching ``research._DiscoverySession.get``
or its module-level singleton; all DB is mocked via a ``FakeConnection``
fixture that records SQL statements + parameter tuples.

These tests exist to satisfy the Agent 1 acceptance tests listed at
reviews/04_phase3_fulton_discovery/01_risk_review.md §4 (gates 1-21).
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

# Importing research is import-safe — it uses lazy DB connection. The
# psycopg dependency is required for module load.
import research

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "discovery"
COSTAR_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "costar"
REPO_ROOT = Path(__file__).resolve().parent.parent
RESEARCH_PY_SRC = (REPO_ROOT / "research.py").read_text(encoding="utf-8")


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class FakeCursor:
    """Records every executed SQL statement and parameter tuple."""

    def __init__(self, fetchone_returns: list[tuple] | None = None) -> None:
        self.executes: list[tuple[str, tuple]] = []
        self._fetchone_returns = list(fetchone_returns or [])
        self.closed = False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executes.append((sql, tuple(params or ())))

    def fetchone(self) -> tuple | None:
        if self._fetchone_returns:
            return self._fetchone_returns.pop(0)
        return (0,)

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        self.closed = True


class FakeConnection:
    """Stand-in for psycopg.Connection. Records cursor.execute calls."""

    def __init__(self, fetchone_returns: list[tuple] | None = None) -> None:
        self.cursors: list[FakeCursor] = []
        self.commits = 0
        self.rollbacks = 0
        self.transaction_count = 0
        self._fetchone_returns = list(fetchone_returns or [])

    def cursor(self) -> FakeCursor:
        c = FakeCursor(self._fetchone_returns)
        self.cursors.append(c)
        return c

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    @contextmanager
    def transaction(self):
        self.transaction_count += 1
        try:
            yield self
            self.commit()
        except Exception:
            self.rollback()
            raise

    @property
    def all_executes(self) -> list[tuple[str, tuple]]:
        out: list[tuple[str, tuple]] = []
        for c in self.cursors:
            out.extend(c.executes)
        return out


@contextmanager
def fake_connection_context(fetchone_returns: list[tuple] | None = None):
    """Patch prepare.get_connection to yield a FakeConnection."""
    fake = FakeConnection(fetchone_returns=fetchone_returns)

    @contextmanager
    def _ctx():
        yield fake

    with mock.patch("research.prepare.get_connection", _ctx):
        yield fake


def _passing_params() -> dict[str, Any]:
    """Subset of parameters.json sufficient to exercise the discovery path."""
    return {
        "hard_filters": {"acreage_min": 5, "acreage_max": 50},
        "owner_classification": {
            "trust_keywords": ["TRUST", "TRUSTEE", "TR "],
            "estate_keywords": ["ESTATE", "ESTATE OF", "DECD"],
            "llc_keywords": ["LLC", "L L C", "LP", "LTD"],
            "corporate_keywords": ["INC", "CORP", "CO ", "GROUP"],
            "government_keywords": ["COUNTY", "CITY OF", "STATE OF", "UNITED STATES", "BOARD OF"],
        },
        "absentee_detection": {
            "out_of_state_threshold_state_code": "GA",
            "absentee_distance_threshold_miles": 50,
        },
        "logging": {
            "results_log_table": "research_log",
            "harness_report_retention_days": 90,
        },
    }


class TestStaticChecks(unittest.TestCase):
    """Gate items 1, 2, 13, 18, 19 — AST/source-level guarantees."""

    def test_no_immutable_writes(self) -> None:
        """R-01: research.py never writes parameters.json / sources.json / program.md."""
        forbidden_paths = ("parameters.json", "program.md")
        # sources.json is read; just ensure no open(..., 'w') against any of these.
        tree = ast.parse(RESEARCH_PY_SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                    if isinstance(mode, str) and "w" in mode:
                        if isinstance(node.args[0], ast.Constant) and any(
                            p in str(node.args[0].value) for p in forbidden_paths
                        ):
                            self.fail(f"forbidden write at line {node.lineno}")
        # Also ensure no json.dump call against the immutable files.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "dump":
                    # Best-effort check; the open() check above is the main defense.
                    pass

    def test_no_string_interpolated_sql(self) -> None:
        """R-05: every cursor.execute() first arg is a Constant or Name (module-level SQL)."""
        tree = ast.parse(RESEARCH_PY_SRC)
        violations = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "execute":
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, (ast.Constant, ast.Name, ast.Attribute)):
                continue
            violations.append((node.lineno, ast.dump(first)[:120]))
        self.assertFalse(violations, f"dynamic SQL detected: {violations}")

    def test_sources_dir_in_gitignore(self) -> None:
        """R-30: sources/ is gitignored so cached PII is not committed."""
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertTrue(
            re.search(r"^sources/", gitignore, flags=re.MULTILINE),
            f".gitignore must contain sources/ entry. Current contents:\n{gitignore}",
        )

    def test_no_print_in_run_discovery_cycle(self) -> None:
        """R-39: run_discovery_cycle and helpers do not call print()."""
        tree = ast.parse(RESEARCH_PY_SRC)
        forbidden_names = {
            "run_discovery_cycle", "_run_for_counties", "_harness_gate",
            "_discover_fulton", "_discover_fulton_corridor", "_process_parcel",
            "_upsert_parcel", "_log_research", "_flag",
            "_query_arcgis_corridor", "_check_field_mapping_drift",
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name not in forbidden_names:
                    continue
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "print":
                        self.fail(f"print() in {node.name} at line {inner.lineno}")

    def test_corridor_bboxes_match_appendix(self) -> None:
        """R-04: corridor bboxes match appendix L266-L283 verbatim."""
        self.assertEqual(
            research._FULTON_CORRIDORS["south_fulton_campbellton"],
            {"xmin": -84.62, "ymin": 33.52, "xmax": -84.50, "ymax": 33.58},
        )
        self.assertEqual(
            research._FULTON_CORRIDORS["west_atlanta_i20"],
            {"xmin": -84.58, "ymin": 33.72, "xmax": -84.42, "ymax": 33.79},
        )

    def test_dispatch_table_has_fulton(self) -> None:
        """R-43: discovery dispatch table is populated for Fulton."""
        self.assertIn("fulton", research._DISCOVERY_CONNECTORS)
        self.assertIs(research._DISCOVERY_CONNECTORS["fulton"], research._discover_fulton)


class TestCycleId(unittest.TestCase):
    """R-31, R-32 — cycle id format and collision."""

    def test_cycle_id_format(self) -> None:
        cid = research._make_cycle_id("fulton")
        self.assertRegex(cid, research._CYCLE_ID_RE)
        self.assertTrue(cid.startswith("disco-fulton-"))

    def test_cycle_id_unique_within_second(self) -> None:
        ids = {research._make_cycle_id("fulton") for _ in range(20)}
        # 4 hex chars = 65536 combinations; 20 draws should not collide.
        self.assertEqual(len(ids), 20)


class TestSafeCachePath(unittest.TestCase):
    """R-40 — path traversal rejected."""

    def test_valid_path(self) -> None:
        cid = research._make_cycle_id("fulton")
        path = research._safe_cache_path(cid, "south_fulton_campbellton", 0)
        self.assertTrue(str(path).endswith("south_fulton_campbellton_0.json"))

    def test_unsafe_cycle_id(self) -> None:
        with self.assertRaises(ValueError):
            research._safe_cache_path("../etc/passwd", "x", 0)

    def test_unsafe_corridor(self) -> None:
        cid = research._make_cycle_id("fulton")
        with self.assertRaises(ValueError):
            research._safe_cache_path(cid, "../bad", 0)

    def test_negative_offset(self) -> None:
        cid = research._make_cycle_id("fulton")
        with self.assertRaises(ValueError):
            research._safe_cache_path(cid, "south_fulton_campbellton", -1)


class TestOwnerTypeInference(unittest.TestCase):
    """R-27 — keyword-based classification with priority ordering."""

    def setUp(self) -> None:
        self.classification = _passing_params()["owner_classification"]

    def test_government_takes_priority_over_trust(self) -> None:
        # "BOARD OF" is government; even with TRUST in name, government wins.
        self.assertEqual(
            research._infer_owner_type("BOARD OF EDUCATION TRUST", self.classification),
            "government",
        )

    def test_trust_with_trailing_space_token(self) -> None:
        # "TR " (with trailing space) matches "SMITH FAMILY TR JOHN TRUSTEE".
        self.assertEqual(
            research._infer_owner_type("SMITH FAMILY TR JOHN TRUSTEE", self.classification),
            "trust",
        )

    def test_trump_is_not_trust(self) -> None:
        # "TRUMP" should not match "TR " because the trailing space disambiguates.
        result = research._infer_owner_type("TRUMP HOLDINGS LLC", self.classification)
        # "LLC" wins (higher priority than trust anyway, and TRUMP doesn't match TR  anyway).
        self.assertEqual(result, "llc")

    def test_estate(self) -> None:
        self.assertEqual(
            research._infer_owner_type("JANE DOE ESTATE", self.classification),
            "estate",
        )

    def test_individual_default(self) -> None:
        self.assertEqual(
            research._infer_owner_type("JOHN Q SMITH", self.classification),
            "individual",
        )

    def test_none_owner_returns_unknown(self) -> None:
        self.assertEqual(
            research._infer_owner_type(None, self.classification),
            "unknown",
        )


class TestCoercion(unittest.TestCase):
    """R-28 — defensive int/float coercion."""

    def test_coerce_int(self) -> None:
        self.assertEqual(research._coerce_int(2025), 2025)
        self.assertEqual(research._coerce_int("2025"), 2025)
        self.assertEqual(research._coerce_int(2025.0), 2025)
        self.assertIsNone(research._coerce_int(""))
        self.assertIsNone(research._coerce_int(None))
        self.assertIsNone(research._coerce_int("abc"))
        self.assertIsNone(research._coerce_int("None"))

    def test_coerce_float(self) -> None:
        self.assertEqual(research._coerce_float("14.7"), 14.7)
        self.assertIsNone(research._coerce_float(""))
        self.assertIsNone(research._coerce_float(None))


class TestMailingComposition(unittest.TestCase):
    """R-26 — addr1 + addr2 concatenation, ATTN/CO stripping."""

    def setUp(self) -> None:
        self.mapping = {
            "owner_mailing_address": "OwnerAddr1",
            "owner_mailing_address_2": "OwnerAddr2",
        }

    def test_addr1_only(self) -> None:
        self.assertEqual(
            research._compose_mailing({"OwnerAddr1": "PO BOX 99"}, self.mapping),
            "PO BOX 99",
        )

    def test_addr1_and_addr2(self) -> None:
        attrs = {"OwnerAddr1": "PO BOX 99", "OwnerAddr2": "ATLANTA GA 30331"}
        self.assertEqual(
            research._compose_mailing(attrs, self.mapping),
            "PO BOX 99 ATLANTA GA 30331",
        )

    def test_attn_stripped(self) -> None:
        attrs = {"OwnerAddr1": "ATTN: JANE", "OwnerAddr2": "PO BOX 1"}
        self.assertEqual(
            research._compose_mailing(attrs, self.mapping),
            "JANE PO BOX 1",
        )

    def test_co_stripped(self) -> None:
        attrs = {"OwnerAddr1": "C/O JOHN", "OwnerAddr2": "PO BOX 1"}
        self.assertEqual(
            research._compose_mailing(attrs, self.mapping),
            "JOHN PO BOX 1",
        )


class TestPolygonAndSrid(unittest.TestCase):
    """R-07, R-08, R-09, R-16 — geometry handling."""

    def test_simple_polygon_to_wkt(self) -> None:
        rings = [[
            [-84.555, 33.553],
            [-84.553, 33.553],
            [-84.553, 33.555],
            [-84.555, 33.555],
            [-84.555, 33.553],
        ]]
        wkt, multi, kept = research._arcgis_polygon_to_wkt(rings)
        self.assertFalse(multi)
        self.assertTrue(wkt.startswith("POLYGON("))
        self.assertIn("-84.555 33.553", wkt)
        # Single-polygon: kept_outer == rings[0].
        self.assertEqual(list(kept), list(rings[0]))

    def test_multipolygon_keeps_largest_outer(self) -> None:
        feature = _load_fixture("arcgis_query_multipolygon.json")["features"][0]
        wkt, multi, kept = research._arcgis_polygon_to_wkt(feature["geometry"]["rings"])
        self.assertTrue(multi)
        self.assertTrue(wkt.startswith("POLYGON("))
        # kept_outer is one of the input rings.
        self.assertIn(list(kept), [list(r) for r in feature["geometry"]["rings"]])

    def test_multipolygon_centroid_uses_kept_outer(self) -> None:
        """Phase 3.1 §6.B: when rings[0] is not the largest, the centroid
        computed from the kept_outer ring must match what PostGIS will
        derive from the WKT, not the rings[0] centroid.
        """
        small = [
            [-84.555, 33.553], [-84.553, 33.553], [-84.553, 33.555],
            [-84.555, 33.555], [-84.555, 33.553],
        ]
        large = [
            [-84.560, 33.560], [-84.500, 33.560], [-84.500, 33.580],
            [-84.560, 33.580], [-84.560, 33.560],
        ]
        rings = [small, large]
        wkt, multi, kept = research._arcgis_polygon_to_wkt(rings)
        self.assertTrue(multi)
        cx_kept, cy_kept = research._ring_centroid(kept)
        cx_first, cy_first = research._ring_centroid(rings[0])
        # Centroid from kept_outer must NOT match centroid from rings[0]
        # — that's the whole point: large ring is second, small is first.
        self.assertNotAlmostEqual(cx_kept, cx_first, places=3)
        self.assertNotAlmostEqual(cy_kept, cy_first, places=3)
        # And the WKT must contain the large ring's coordinates.
        self.assertIn("-84.56 33.56", wkt)

    def test_srid_sanity_accepts_wgs84(self) -> None:
        self.assertTrue(research._check_srid_sanity(-84.5, 33.6))

    def test_srid_sanity_rejects_state_plane(self) -> None:
        self.assertFalse(research._check_srid_sanity(2200000.0, 1400000.0))


class TestHardFilters(unittest.TestCase):
    """R-20, R-21, R-22, R-23, R-24 — filter pipeline correctness."""

    def setUp(self) -> None:
        self.params = _passing_params()

    def test_h1_inside_envelope(self) -> None:
        self.assertTrue(research._in_fulton_envelope(-84.5, 33.6))

    def test_h1_outside_envelope(self) -> None:
        self.assertFalse(research._in_fulton_envelope(-85.0, 33.0))

    def test_h2_at_lower_bound(self) -> None:
        self.assertTrue(research._h2_pass(5.0, self.params))

    def test_h2_at_upper_bound(self) -> None:
        self.assertTrue(research._h2_pass(50.0, self.params))

    def test_h2_below_bound(self) -> None:
        self.assertFalse(research._h2_pass(4.99, self.params))

    def test_h2_above_bound(self) -> None:
        self.assertFalse(research._h2_pass(50.01, self.params))

    def test_h2_none(self) -> None:
        self.assertFalse(research._h2_pass(None, self.params))

    def test_filter_pipeline_order(self) -> None:
        """Pipeline is H1 → H2 → H3-flag → H4-flag → H5..H10 stubs (R-24, R-101)."""
        ids = [f.__name__ for f in research._HARD_FILTERS]
        self.assertEqual(ids, [
            "_h1_filter", "_h2_filter",
            "_h3_flag", "_h4_flag",
            "_h5_filter", "_h6_filter", "_h7_filter", "_h8_filter", "_h9_filter", "_h10_filter",
        ])

    def test_filter_pipeline_extensible(self) -> None:
        """R-42: Phase 4+ can append H5 onwards without rewriting."""
        original = list(research._HARD_FILTERS)
        try:
            def _h5_stub(parcel, conn, params):
                return research._FilterResult("pass", "H5", "")
            research._HARD_FILTERS.append(_h5_stub)
            self.assertEqual(len(research._HARD_FILTERS), len(original) + 1)
        finally:
            research._HARD_FILTERS[:] = original


class TestQueryParamBuilder(unittest.TestCase):
    """R-15 — where clause shape and field-name whitelisting."""

    def test_where_clause_only_int_bounds(self) -> None:
        params = _passing_params()
        mapping = {"acreage": "LandAcres", "parcel_id": "ParcelID"}
        bbox = {"xmin": -84.62, "ymin": 33.52, "xmax": -84.50, "ymax": 33.58}
        q = research._build_known_query_params(bbox, 1000, 0, params, mapping)
        self.assertRegex(q["where"], r"^LandAcres BETWEEN \d+ AND \d+$")
        self.assertEqual(q["geometryType"], "esriGeometryEnvelope")
        self.assertEqual(q["outSR"], "4326")
        self.assertEqual(q["f"], "json")

    def test_unsafe_field_name_rejected(self) -> None:
        params = _passing_params()
        mapping = {"acreage": "Land Acres; DROP TABLE", "parcel_id": "ParcelID"}
        bbox = {"xmin": -84.62, "ymin": 33.52, "xmax": -84.50, "ymax": 33.58}
        with self.assertRaises(ValueError):
            research._build_known_query_params(bbox, 1000, 0, params, mapping)


class TestArcgisPagination(unittest.TestCase):
    """R-13, R-19 — pagination termination and empty-corridor handling."""

    def test_pagination_terminates_on_exceeded_false(self) -> None:
        page1 = _load_fixture("arcgis_query_pagination_page1.json")
        page2 = _load_fixture("arcgis_query_pagination_page2.json")
        responses = [page1, page2, {"features": []}]

        class _MockSession:
            def __init__(self, responses):
                self.responses = list(responses)
                self.calls = 0
            def get(self, url, params=None, timeout=None):
                self.calls += 1
                if not self.responses:
                    return {"features": [], "exceededTransferLimit": False}
                return self.responses.pop(0)

        sess = _MockSession(responses)
        gen = research._query_arcgis_corridor(
            sess, "https://example.test/MapServer", 11,
            {"xmin": -84.62, "ymin": 33.52, "xmax": -84.50, "ymax": 33.58},
            {"acreage": "LandAcres", "parcel_id": "ParcelID"},
            _passing_params(), research._make_cycle_id("fulton"),
            "south_fulton_campbellton", page_size=2,
        )
        feats = list(gen)
        # page1 had exceededTransferLimit=true (2 features), page2 had false (1 feature).
        # Generator should fetch page1 + page2 then stop.
        self.assertEqual(len(feats), 3)
        self.assertEqual(sess.calls, 2)

    def test_empty_corridor_yields_no_features(self) -> None:
        empty = _load_fixture("arcgis_query_empty.json")

        class _MockSession:
            def get(self, url, params=None, timeout=None):
                return empty

        gen = research._query_arcgis_corridor(
            _MockSession(), "https://example.test/MapServer", 11,
            {"xmin": -84.62, "ymin": 33.52, "xmax": -84.50, "ymax": 33.58},
            {"acreage": "LandAcres", "parcel_id": "ParcelID"},
            _passing_params(), research._make_cycle_id("fulton"),
            "south_fulton_campbellton", page_size=1000,
        )
        feats = list(gen)
        self.assertEqual(feats, [])


class TestParcelMapping(unittest.TestCase):
    """R-06 (county prefix), R-08 (SRID), R-44 (all mapped fields)."""

    def test_parcel_id_is_county_prefixed(self) -> None:
        feature = _load_fixture("arcgis_query_two_features.json")["features"][0]
        mapping = {
            "parcel_id": "ParcelID", "owner_name": "Owner",
            "owner_mailing_address": "OwnerAddr1", "owner_mailing_address_2": "OwnerAddr2",
            "site_address": "Address", "acreage": "LandAcres",
            "land_value": "LandAssess", "improvement_value": "ImprAssess",
            "total_value": "TotAssess", "land_use_code": "LUCode",
            "tax_year": "TaxYear",
        }
        row, wkt, skip, multi = research._map_feature_to_parcel(
            feature, mapping, _passing_params()["owner_classification"],
            "atlanta", "south_fulton_campbellton", "/tmp/cache/x.json",
        )
        self.assertIsNone(skip)
        self.assertIsNotNone(row)
        self.assertTrue(row["parcel_id"].startswith("fulton-"))
        self.assertEqual(row["county"], "fulton")
        self.assertEqual(row["state"], "GA")

    def test_state_plane_response_is_skipped(self) -> None:
        feature = _load_fixture("arcgis_query_state_plane.json")["features"][0]
        mapping = {
            "parcel_id": "ParcelID", "owner_name": "Owner",
            "owner_mailing_address": "OwnerAddr1", "owner_mailing_address_2": "OwnerAddr2",
            "site_address": "Address", "acreage": "LandAcres",
        }
        row, wkt, skip, multi = research._map_feature_to_parcel(
            feature, mapping, _passing_params()["owner_classification"],
            "atlanta", "south_fulton_campbellton", "/tmp/cache/x.json",
        )
        self.assertIsNone(row)
        self.assertIsNotNone(skip)
        self.assertIn("WGS84", skip)


class TestPiiHandling(unittest.TestCase):
    """R-29 — owner names stored verbatim in parcels (no harness redaction)."""

    def test_owner_name_passthrough(self) -> None:
        feature = _load_fixture("arcgis_query_two_features.json")["features"][0]
        mapping = {
            "parcel_id": "ParcelID", "owner_name": "Owner",
            "owner_mailing_address": "OwnerAddr1", "owner_mailing_address_2": "OwnerAddr2",
            "site_address": "Address", "acreage": "LandAcres",
        }
        row, _, _, _ = research._map_feature_to_parcel(
            feature, mapping, _passing_params()["owner_classification"],
            "atlanta", "south_fulton_campbellton", "/tmp/cache/x.json",
        )
        self.assertEqual(row["owner_name"], "SMITH FAMILY TRUST")
        self.assertNotIn("REDACTED", row["owner_name"])


class TestHarnessGate(unittest.TestCase):
    """R-34 — harness gate is the first non-trivial action; failing aborts."""

    def _patch_params(self):
        return mock.patch("research.prepare.get_parameters", return_value=_passing_params())

    def _patch_verify(self):
        return mock.patch("research.prepare.verify_parameters_unchanged", return_value=None)

    def _patch_sources(self):
        return mock.patch.object(research, "_load_sources_json", return_value={
            "county_parcel_data": {"fulton_ga": {
                "service_url": "https://example.test/MapServer",
                "parcel_layer_id": 11,
                "field_mapping": {
                    "parcel_id": "ParcelID", "owner_name": "Owner",
                    "owner_mailing_address": "OwnerAddr1",
                    "owner_mailing_address_2": "OwnerAddr2",
                    "site_address": "Address", "acreage": "LandAcres",
                    "land_value": "LandAssess", "improvement_value": "ImprAssess",
                    "total_value": "TotAssess", "land_use_code": "LUCode",
                    "tax_year": "TaxYear",
                },
            }},
        })

    def test_harness_failing_aborts_cycle(self) -> None:
        with fake_connection_context() as fake, \
                self._patch_params(), self._patch_verify(), self._patch_sources(), \
                mock.patch.object(
                    research.connector_harness, "run_harness_for_county",
                    return_value=_load_fixture("harness_failing.json"),
                ):
            summary = research.run_discovery_cycle("atlanta")
        self.assertTrue(summary["aborted"])
        self.assertEqual(summary["abort_reason"], "harness_failing")
        # research_log abort row written.
        sqls = [s for s, _ in fake.all_executes if "research_log" in s]
        self.assertTrue(any("research_log" in s for s in sqls))

    def test_harness_raise_treated_as_failing(self) -> None:
        with fake_connection_context() as fake, \
                self._patch_params(), self._patch_verify(), self._patch_sources(), \
                mock.patch.object(
                    research.connector_harness, "run_harness_for_county",
                    side_effect=RuntimeError("network down"),
                ):
            summary = research.run_discovery_cycle("atlanta")
        self.assertTrue(summary["aborted"])
        self.assertEqual(summary["abort_reason"], "harness_failing")

    def test_market_not_supported_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            research.run_discovery_cycle("dallas-fort-worth")


class TestHappyPathDryRun(unittest.TestCase):
    """End-to-end dry-run with mocked harness, mocked HTTP, fake conn."""

    def test_two_feature_happy_path(self) -> None:
        sources_payload = {
            "county_parcel_data": {"fulton_ga": {
                "service_url": "https://example.test/MapServer",
                "parcel_layer_id": 11,
                "field_mapping": {
                    "parcel_id": "ParcelID", "owner_name": "Owner",
                    "owner_mailing_address": "OwnerAddr1",
                    "owner_mailing_address_2": "OwnerAddr2",
                    "site_address": "Address", "acreage": "LandAcres",
                    "land_value": "LandAssess", "improvement_value": "ImprAssess",
                    "total_value": "TotAssess", "land_use_code": "LUCode",
                    "tax_year": "TaxYear",
                },
            }},
        }

        schema = _load_fixture("arcgis_layer11_schema.json")
        two_features = _load_fixture("arcgis_query_two_features.json")
        empty = _load_fixture("arcgis_query_empty.json")

        # Mock session: schema URL → schema; query URL → two_features (with
        # exceededTransferLimit=false, so pagination terminates after one
        # call per corridor). Two corridors → two_features twice.
        class _MockSession:
            def __init__(self):
                self.calls = 0
            def get(self, url, params=None, timeout=None):
                self.calls += 1
                if url.endswith("/11"):
                    return schema
                return two_features
            def close(self):
                pass

        with fake_connection_context() as fake, \
                mock.patch("research.prepare.get_parameters", return_value=_passing_params()), \
                mock.patch("research.prepare.verify_parameters_unchanged", return_value=None), \
                mock.patch.object(research, "_load_sources_json", return_value=sources_payload), \
                mock.patch.object(research, "_DiscoverySession", return_value=_MockSession()), \
                mock.patch.object(
                    research.connector_harness, "run_harness_for_county",
                    return_value=_load_fixture("harness_healthy.json"),
                ):
            summary = research.run_discovery_cycle("atlanta")
        self.assertFalse(summary["aborted"])
        self.assertEqual(summary["harness_status"], "healthy")
        # 2 corridors x 2 valid features = 4 discoveries upserted.
        per_county = summary["per_county"]["fulton"]
        totals = per_county["totals"]
        self.assertEqual(totals["discovery"], 4)
        self.assertEqual(totals["rejection"], 0)
        # Verify UPSERT statements were issued.
        sqls = [s for s, _ in fake.all_executes]
        self.assertTrue(any("INSERT INTO parcels" in s for s in sqls))
        self.assertTrue(any("ON CONFLICT (parcel_id) DO UPDATE" in s for s in sqls))
        # Each parcel got eight flag rows (H3, H4, H5, H6, H7, H8, H9, H10).
        flag_rows = [s for s, _ in fake.all_executes if "flagged_items" in s]
        # 4 parcels x 8 flags (H3, H4, H5, H6, H7, H8, H9, H10) = 32 minimum
        # (multipolygon flag may add more) (R-105).
        self.assertGreaterEqual(len(flag_rows), 32)


# ---------------------------------------------------------------------------
# Phase 3.1 punch-list additions
# ---------------------------------------------------------------------------
def _sources_payload() -> dict[str, Any]:
    """Shared sources.json shape for the harness/cycle integration tests."""
    return {
        "county_parcel_data": {"fulton_ga": {
            "service_url": "https://example.test/MapServer",
            "parcel_layer_id": 11,
            "field_mapping": {
                "parcel_id": "ParcelID", "owner_name": "Owner",
                "owner_mailing_address": "OwnerAddr1",
                "owner_mailing_address_2": "OwnerAddr2",
                "site_address": "Address", "acreage": "LandAcres",
                "land_value": "LandAssess", "improvement_value": "ImprAssess",
                "total_value": "TotAssess", "land_use_code": "LUCode",
                "tax_year": "TaxYear",
            },
        }},
    }


class TestPhase31ImmutableWritesStrict(unittest.TestCase):
    """Phase 3.1 §6.A: strengthen the immutable-write scan to catch
    Path.write_text, Path.open, json.dump, csv.writer patterns whose
    target string contains parameters.json / program.md / sources.json.
    The original test only caught open(<Constant>, <Constant 'w'>)."""

    FORBIDDEN = ("parameters.json", "program.md", "sources.json")

    def test_strict_no_immutable_writes(self) -> None:
        tree = ast.parse(RESEARCH_PY_SRC)
        violations: list[tuple[int, str]] = []

        def _string_constants_in(node: ast.AST) -> list[str]:
            """All string constants reachable from this AST node."""
            out: list[str] = []
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    out.append(inner.value)
            return out

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Pattern 1: open(path, "w...")
            is_open = isinstance(node.func, ast.Name) and node.func.id == "open"
            # Pattern 2: <expr>.open("w..."), <expr>.write_text(...),
            # <expr>.write_bytes(...), json.dump(obj, fh), csv.writer(fh).
            attr_call = isinstance(node.func, ast.Attribute)
            attr_name = node.func.attr if attr_call else ""
            is_pathlike_write = attr_name in {"write_text", "write_bytes"}
            is_path_open_write = False
            if attr_call and attr_name == "open":
                # Path(...).open("w") — second arg is the mode.
                if node.args and len(node.args) >= 1:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str) and "w" in first.value:
                        is_path_open_write = True
            is_jsondump = attr_call and attr_name == "dump" and isinstance(node.func.value, ast.Name) and node.func.value.id == "json"
            is_csvwriter = attr_call and attr_name == "writer" and isinstance(node.func.value, ast.Name) and node.func.value.id == "csv"

            interesting = is_open or is_pathlike_write or is_path_open_write or is_jsondump or is_csvwriter
            if not interesting:
                continue

            # Look at the call and the receiver (e.g. Path("parameters.json")
            # or _PARAMS_PATH) for any forbidden string constant.
            search_targets: list[ast.AST] = list(node.args)
            if attr_call:
                search_targets.append(node.func.value)

            for tgt in search_targets:
                for s in _string_constants_in(tgt):
                    if any(p in s for p in self.FORBIDDEN):
                        violations.append((node.lineno, s))

        self.assertFalse(
            violations,
            f"forbidden write to immutable file detected: {violations}",
        )


class TestPhase31CycleLevelFlagNullsParcelId(unittest.TestCase):
    """Phase 3.1 §6.2: cycle-level flag rows (no specific parcel) must use
    SQL NULL for parcel_id, not the string sentinel "(none)"."""

    def test_harness_degraded_emits_null_parcel_id(self) -> None:
        with fake_connection_context() as fake, \
                mock.patch("research.prepare.get_parameters", return_value=_passing_params()), \
                mock.patch("research.prepare.verify_parameters_unchanged", return_value=None), \
                mock.patch.object(research, "_load_sources_json", return_value=_sources_payload()), \
                mock.patch.object(research, "_DiscoverySession", return_value=_HappyPathSession()), \
                mock.patch.object(
                    research.connector_harness, "run_harness_for_county",
                    return_value=_load_fixture("harness_degraded.json"),
                ):
            research.run_discovery_cycle("atlanta")

        flag_inserts = [
            (sql, params) for sql, params in fake.all_executes
            if "flagged_items" in sql
        ]
        # The first flag row is the cycle-level harness=degraded row.
        # _SQL_INSERT_FLAG params order: (flag_type, parcel_id, market, description, suggested_resolution).
        cycle_level_rows = [
            params for _, params in flag_inserts
            if params and len(params) >= 5
            and isinstance(params[3], str)
            and "harness=degraded" in params[3]
        ]
        self.assertTrue(cycle_level_rows, "no harness=degraded flag row was emitted")
        for params in cycle_level_rows:
            self.assertIsNone(
                params[1],
                f"cycle-level flag row must use NULL parcel_id, got {params[1]!r}",
            )
            self.assertNotEqual(params[1], "(none)")


class TestPhase31FallbackPagination(unittest.TestCase):
    """Phase 3.1 §6.4: when ArcGIS omits exceededTransferLimit AND
    len(features) < page_size, the pagination loop must terminate via
    the short-page heuristic without an extra round-trip."""

    def test_pagination_terminates_on_short_page_when_field_absent(self) -> None:
        fixture = _load_fixture("arcgis_query_pagination_fallback.json")
        # Sanity: fixture has exactly 1 feature and no exceededTransferLimit field.
        self.assertEqual(len(fixture["features"]), 1)
        self.assertNotIn("exceededTransferLimit", fixture)

        class _MockSession:
            def __init__(self):
                self.calls = 0
            def get(self, url, params=None, timeout=None):
                self.calls += 1
                return fixture

        sess = _MockSession()
        gen = research._query_arcgis_corridor(
            sess, "https://example.test/MapServer", 11,
            {"xmin": -84.62, "ymin": 33.52, "xmax": -84.50, "ymax": 33.58},
            {"acreage": "LandAcres", "parcel_id": "ParcelID"},
            _passing_params(), research._make_cycle_id("fulton"),
            "south_fulton_campbellton",
            page_size=10,  # fixture has 1 feature; 1 < 10 triggers the fallback branch.
        )
        feats = list(gen)
        self.assertEqual(len(feats), 1)
        # Only one round-trip — the short-page heuristic terminated the loop.
        self.assertEqual(sess.calls, 1)


class TestPhase31FieldMappingDrift(unittest.TestCase):
    """Phase 3.1 §6.6: _check_field_mapping_drift detects a missing mapped
    field via the ArcGIS layer schema endpoint."""

    def test_missing_landacres_returns_false_with_field_listed(self) -> None:
        schema = _load_fixture("arcgis_layer11_schema_missing_landacres.json")

        class _MockSession:
            def get(self, url, params=None, timeout=None):
                return schema

        ok, missing = research._check_field_mapping_drift(
            _MockSession(),
            "https://example.test/MapServer",
            11,
            {
                "parcel_id": "ParcelID", "owner_name": "Owner",
                "owner_mailing_address": "OwnerAddr1",
                "acreage": "LandAcres",
            },
        )
        self.assertFalse(ok)
        self.assertIn("LandAcres", missing)

    def test_full_schema_returns_true(self) -> None:
        schema = _load_fixture("arcgis_layer11_schema.json")

        class _MockSession:
            def get(self, url, params=None, timeout=None):
                return schema

        ok, missing = research._check_field_mapping_drift(
            _MockSession(),
            "https://example.test/MapServer",
            11,
            {
                "parcel_id": "ParcelID", "owner_name": "Owner",
                "owner_mailing_address": "OwnerAddr1",
                "acreage": "LandAcres",
            },
        )
        self.assertTrue(ok)
        self.assertEqual(missing, [])


class TestPhase31CycleIdCollision(unittest.TestCase):
    """Phase 3.1 §6.6: a non-zero count of existing rows with the cycle_id
    aborts the cycle with abort_reason='cycle_id_collision'."""

    def test_cycle_id_collision_aborts(self) -> None:
        # FakeConnection.fetchone_returns drives the _count_log_rows result.
        # A non-zero row count triggers the collision abort path.
        with fake_connection_context(fetchone_returns=[(7,)]) as fake, \
                mock.patch("research.prepare.get_parameters", return_value=_passing_params()), \
                mock.patch("research.prepare.verify_parameters_unchanged", return_value=None), \
                mock.patch.object(research, "_load_sources_json", return_value=_sources_payload()):
            summary = research.run_discovery_cycle("atlanta")
        self.assertTrue(summary["aborted"])
        self.assertEqual(summary["abort_reason"], "cycle_id_collision")


class _HappyPathSession:
    """Reusable fake _DiscoverySession for the harness=degraded test."""

    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if url.endswith("/11"):
            return _load_fixture("arcgis_layer11_schema.json")
        return _load_fixture("arcgis_query_two_features.json")

    def close(self):
        pass


class TestPhase31HarnessDegradedProceeds(unittest.TestCase):
    """Phase 3.1 §6.C: harness=degraded does NOT abort the cycle; it emits
    one cycle-level flag row and proceeds into the connector."""

    def test_harness_degraded_proceeds_with_flag(self) -> None:
        with fake_connection_context() as fake, \
                mock.patch("research.prepare.get_parameters", return_value=_passing_params()), \
                mock.patch("research.prepare.verify_parameters_unchanged", return_value=None), \
                mock.patch.object(research, "_load_sources_json", return_value=_sources_payload()), \
                mock.patch.object(research, "_DiscoverySession", return_value=_HappyPathSession()), \
                mock.patch.object(
                    research.connector_harness, "run_harness_for_county",
                    return_value=_load_fixture("harness_degraded.json"),
                ):
            summary = research.run_discovery_cycle("atlanta")
        self.assertFalse(summary["aborted"])
        self.assertEqual(summary["harness_status"], "degraded")
        # Exactly one harness-degraded flag row at cycle level.
        deg_flags = [
            params for sql, params in fake.all_executes
            if "flagged_items" in sql and len(params) >= 5
            and isinstance(params[3], str) and "harness=degraded" in params[3]
        ]
        self.assertEqual(len(deg_flags), 1)
        # Connector still ran.
        self.assertIn("fulton", summary["per_county"])


class TestPhase31FilterPipelineExtensibleExecutes(unittest.TestCase):
    """Phase 3.1 §6.D: appending a new filter to _HARD_FILTERS at runtime
    must affect actual per-parcel processing, not just the list length."""

    def test_synthetic_h5_filter_emits_marker_flag(self) -> None:
        feature = _load_fixture("arcgis_query_two_features.json")["features"][0]
        mapping = _sources_payload()["county_parcel_data"]["fulton_ga"]["field_mapping"]
        params = _passing_params()
        marker = "marker_h5_synthetic"

        def _h5_test_stub(parcel, conn, p):
            return research._FilterResult("flag", "H5_TEST", marker)

        original = list(research._HARD_FILTERS)
        try:
            research._HARD_FILTERS.append(_h5_test_stub)
            fake = FakeConnection()
            cycle_id = research._make_cycle_id("fulton")
            status = research._process_parcel(
                feature, fake, cycle_id, "south_fulton_campbellton", "atlanta",
                mapping, params["owner_classification"], params,
                raw_response_path="/tmp/cache/x.json",
            )
        finally:
            research._HARD_FILTERS[:] = original

        self.assertEqual(status, "discovery")
        # The synthetic H5 filter should have produced a flagged_items insert
        # whose description contains the marker.
        flag_descriptions = [
            params[3] for sql, params in fake.all_executes
            if "flagged_items" in sql and len(params) >= 4
        ]
        self.assertTrue(
            any(marker in d for d in flag_descriptions),
            f"synthetic H5 marker not seen in flag rows: {flag_descriptions}",
        )


# ---------------------------------------------------------------------------
# Phase 4 — H5..H10 PASS-WITH-FLAG stubs
# ---------------------------------------------------------------------------
class TestPhase4HardFilterStubs(unittest.TestCase):
    """R-106: every new H5..H10 stub returns _FilterResult('flag', 'H<N>', non-empty).

    Each stub is pure (no params reads, no DB, no HTTP) per R-104, R-111.
    """

    def _assert_flag(self, filter_id: str, result: research._FilterResult, tokens: list[str]) -> None:
        self.assertEqual(result.action, "flag")
        self.assertEqual(result.filter_id, filter_id)
        self.assertTrue(result.reason, f"{filter_id} reason was empty")
        self.assertTrue(
            any(token.lower() in result.reason.lower() for token in tokens),
            f"{filter_id} reason missing expected token(s) {tokens}: {result.reason!r}",
        )

    def test_h5_returns_flag(self) -> None:
        result = research._h5_filter({}, None, _passing_params())
        self._assert_flag("H5", result, ["EPA", "Envirofacts"])

    def test_h6_returns_flag(self) -> None:
        result = research._h6_filter({}, None, _passing_params())
        self._assert_flag("H6", result, ["NWI", "wetlands", "USGS"])

    def test_h7_returns_flag(self) -> None:
        result = research._h7_filter({}, None, _passing_params())
        self._assert_flag("H7", result, ["road", "DOT"])

    def test_h8_returns_flag(self) -> None:
        result = research._h8_filter({}, None, _passing_params())
        self._assert_flag("H8", result, ["utility"])

    def test_h9_returns_flag(self) -> None:
        result = research._h9_filter({}, None, _passing_params())
        self._assert_flag("H9", result, ["topography", "USGS", "3DEP"])

    def test_h10_returns_flag(self) -> None:
        result = research._h10_filter({}, None, _passing_params())
        self._assert_flag("H10", result, ["ownership", "easement", "deed"])


class TestPhase4FilterPipelineEndToEnd(unittest.TestCase):
    """Integration-style test: _process_parcel emits one flagged_items row per
    H5..H10 stub for a happy-path parcel (R-103, R-105)."""

    def test_h5_through_h10_emit_flag_rows(self) -> None:
        feature = _load_fixture("arcgis_query_two_features.json")["features"][0]
        mapping = _sources_payload()["county_parcel_data"]["fulton_ga"]["field_mapping"]
        params = _passing_params()
        fake = FakeConnection()
        cycle_id = research._make_cycle_id("fulton")
        status = research._process_parcel(
            feature, fake, cycle_id, "south_fulton_campbellton", "atlanta",
            mapping, params["owner_classification"], params,
            raw_response_path="/tmp/cache/x.json",
        )
        self.assertEqual(status, "discovery")
        flag_descriptions = [
            params[3] for sql, params in fake.all_executes
            if "flagged_items" in sql and len(params) >= 4
        ]
        for filter_id in ("H5", "H6", "H7", "H8", "H9", "H10"):
            self.assertTrue(
                any(filter_id in d for d in flag_descriptions),
                f"{filter_id} flag row not seen in flag descriptions: {flag_descriptions}",
            )


# ---------------------------------------------------------------------------
# Phase 5 — Scoring Engine MVP (Option B)
# See reviews/07_phase5_scoring_mvp/01_risk_review.md for the R-2XX gates.
# ---------------------------------------------------------------------------
class _SharedQueueCursor:
    """Cursor backed by SHARED fetchone+fetchall queues on the parent
    connection. Lets multi-cursor scoring flows replay sequenced fixtures."""

    def __init__(self, conn: "Phase5FakeConnection") -> None:
        self.conn = conn
        self.executes: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        rec = (sql, tuple(params or ()))
        self.executes.append(rec)
        self.conn._all_executes.append(rec)

    def fetchone(self) -> tuple | None:
        if self.conn._fetchone_queue:
            return self.conn._fetchone_queue.pop(0)
        return None

    def fetchall(self) -> list[tuple]:
        if self.conn._fetchall_queue:
            return self.conn._fetchall_queue.pop(0)
        return []

    def __enter__(self) -> "_SharedQueueCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class Phase5FakeConnection:
    """psycopg.Connection stand-in for Phase 5 scoring tests with proper
    sequenced fetchone/fetchall queues shared across all cursors."""

    def __init__(
        self,
        fetchone_queue: list[tuple] | None = None,
        fetchall_queue: list[list[tuple]] | None = None,
    ) -> None:
        self._fetchone_queue: list[tuple] = list(fetchone_queue or [])
        self._fetchall_queue: list[list[tuple]] = list(fetchall_queue or [])
        self.cursors: list[_SharedQueueCursor] = []
        self._all_executes: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0
        self.transaction_count = 0

    def cursor(self) -> _SharedQueueCursor:
        c = _SharedQueueCursor(self)
        self.cursors.append(c)
        return c

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    @contextmanager
    def transaction(self):
        self.transaction_count += 1
        try:
            yield self
            self.commit()
        except Exception:
            self.rollback()
            raise

    @property
    def all_executes(self) -> list[tuple[str, tuple]]:
        return list(self._all_executes)


class TestPhase5OzPnpoly(unittest.TestCase):
    """Pure-Python point-in-polygon (R-206)."""

    SQUARE = [
        [-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0], [-1.0, -1.0],
    ]

    def test_point_inside_simple_square(self) -> None:
        self.assertTrue(research._point_in_ring(0.0, 0.0, self.SQUARE))

    def test_point_outside_simple_square(self) -> None:
        self.assertFalse(research._point_in_ring(2.0, 2.0, self.SQUARE))

    def test_point_outside_nearby(self) -> None:
        self.assertFalse(research._point_in_ring(1.5, 0.0, self.SQUARE))

    def test_degenerate_ring_returns_false(self) -> None:
        self.assertFalse(research._point_in_ring(0.0, 0.0, [[0, 0], [1, 0]]))


class TestPhase5OzCheck(unittest.TestCase):
    """S10 OZ check against the bundled stub data (R-205, R-219)."""

    def setUp(self) -> None:
        # Reset the OZ cache so each test re-loads fresh.
        research._OZ_TRACTS_CACHE = None

    def test_in_south_fulton_stub_returns_true(self) -> None:
        # Centroid in the stub South Fulton polygon (-84.62..-84.50, 33.52..33.58).
        self.assertTrue(research._check_oz(-84.55, 33.55))

    def test_in_clayton_stub_returns_true(self) -> None:
        self.assertTrue(research._check_oz(-84.37, 33.59))

    def test_outside_known_stubs_returns_false(self) -> None:
        # Far outside any stub polygon.
        self.assertFalse(research._check_oz(-83.0, 35.0))

    def test_null_centroid_returns_false(self) -> None:
        self.assertFalse(research._check_oz(None, None))


class TestPhase5S2Geometry(unittest.TestCase):
    """S2 score mapping (R-207)."""

    def test_perfect_square_scores_10(self) -> None:
        # compactness = area / bbox_area = 1.0; aspect = 1.0
        self.assertEqual(research._score_geometry(100.0, 100.0, 1.0), 10)

    def test_near_perfect_rectangle_scores_10(self) -> None:
        # compactness = 0.95, aspect = 1.5 → 10
        self.assertEqual(research._score_geometry(95.0, 100.0, 1.5), 10)

    def test_minor_irregularity_scores_7(self) -> None:
        # compactness = 0.87, aspect = 2.5 → 7
        self.assertEqual(research._score_geometry(87.0, 100.0, 2.5), 7)

    def test_significant_irregularity_scores_4(self) -> None:
        # compactness = 0.70 → 4
        self.assertEqual(research._score_geometry(70.0, 100.0, 1.5), 4)

    def test_unbuildable_scores_0(self) -> None:
        # compactness = 0.40 → 0
        self.assertEqual(research._score_geometry(40.0, 100.0, 1.5), 0)

    def test_long_thin_rectangle_drops_to_4(self) -> None:
        # aspect = 5.0 disqualifies from the >=7 tier; compactness 0.93 → 4
        # (compactness_high but aspect_too_high → falls through to else if no
        # 0.65 threshold met; with 0.93 it lands at 4).
        self.assertEqual(research._score_geometry(93.0, 100.0, 5.0), 4)

    def test_null_geometry_returns_none(self) -> None:
        self.assertIsNone(research._score_geometry(None, 100.0, 1.0))
        self.assertIsNone(research._score_geometry(100.0, None, 1.0))
        self.assertIsNone(research._score_geometry(0.0, 100.0, 1.0))


class TestPhase5S9(unittest.TestCase):
    """S9 entitlement stub (R-218)."""

    def test_returns_moderate_5(self) -> None:
        self.assertEqual(research._compute_s9(), 5)


class TestPhase5S10(unittest.TestCase):
    """S10 incentives — OZ portion only."""

    def setUp(self) -> None:
        research._OZ_TRACTS_CACHE = None

    def test_in_oz_returns_4(self) -> None:
        # In stub South Fulton OZ.
        self.assertEqual(research._compute_s10(-84.55, 33.55), 4)

    def test_outside_oz_returns_0(self) -> None:
        self.assertEqual(research._compute_s10(-83.0, 35.0), 0)

    def test_null_centroid_returns_none(self) -> None:
        self.assertIsNone(research._compute_s10(None, None))
        self.assertIsNone(research._compute_s10(-84.55, None))


class TestPhase5Composite(unittest.TestCase):
    """Composite formula edge cases (R-203)."""

    WEIGHTS = {
        "S1_interstate_proximity": 15,
        "S2_parcel_geometry": 10,
        "S3_topography": 10,
        "S4_submarket_vacancy": 10,
        "S5_submarket_absorption": 10,
        "S6_competing_pipeline": 8,
        "S7_labor_pool": 8,
        "S8_land_basis": 7,
        "S9_entitlement_complexity": 7,
        "S10_incentives": 5,
        "S11_rail_adjacency": 5,
        "S12_demand_generators": 5,
    }

    def _all_null(self) -> dict[str, int | None]:
        return {n: None for n in research._SUB_SCORE_NAMES}

    def test_all_null_returns_none(self) -> None:
        sub = self._all_null()
        self.assertIsNone(research._compute_composite(sub, self.WEIGHTS))

    def test_single_subscore(self) -> None:
        # Only S2 = 10, weight 10 → composite = (10*10/10) * 10 = 100.0
        sub = self._all_null()
        sub["S2_parcel_geometry"] = 10
        self.assertEqual(research._compute_composite(sub, self.WEIGHTS), 100.0)

    def test_phase5_mvp_scenario(self) -> None:
        # Realistic MVP: S2=10 (w=10), S9=5 (w=7), S10=4 (w=5).
        # numerator = 10*10 + 5*7 + 4*5 = 100 + 35 + 20 = 155
        # denominator = 10 + 7 + 5 = 22
        # composite = (155/22) * 10 = 70.45...
        sub = self._all_null()
        sub["S2_parcel_geometry"] = 10
        sub["S9_entitlement_complexity"] = 5
        sub["S10_incentives"] = 4
        result = research._compute_composite(sub, self.WEIGHTS)
        self.assertAlmostEqual(result, 70.45, places=1)

    def test_max_composite_is_100(self) -> None:
        sub = {n: 10 for n in research._SUB_SCORE_NAMES}
        self.assertEqual(research._compute_composite(sub, self.WEIGHTS), 100.0)

    def test_zero_subscore_contributes_zero(self) -> None:
        sub = self._all_null()
        sub["S2_parcel_geometry"] = 0
        # Only S2 populated with score 0 → composite = 0
        self.assertEqual(research._compute_composite(sub, self.WEIGHTS), 0.0)


class TestPhase5Confidence(unittest.TestCase):
    """Confidence score range (R-208, R-216)."""

    def test_zero_populated(self) -> None:
        sub = {n: None for n in research._SUB_SCORE_NAMES}
        self.assertEqual(research._compute_confidence(sub), 0.0)

    def test_full_populated(self) -> None:
        sub = {n: 5 for n in research._SUB_SCORE_NAMES}
        self.assertEqual(research._compute_confidence(sub), 1.0)

    def test_three_populated(self) -> None:
        sub = {n: None for n in research._SUB_SCORE_NAMES}
        sub["S2_parcel_geometry"] = 10
        sub["S9_entitlement_complexity"] = 5
        sub["S10_incentives"] = 4
        self.assertAlmostEqual(research._compute_confidence(sub), 3 / 12)

    def test_zero_subscore_counts_as_populated(self) -> None:
        sub = {n: None for n in research._SUB_SCORE_NAMES}
        sub["S2_parcel_geometry"] = 0
        self.assertAlmostEqual(research._compute_confidence(sub), 1 / 12)


class TestPhase5ScoreParcel(unittest.TestCase):
    """End-to-end score_parcel against Phase5FakeConnection."""

    def setUp(self) -> None:
        research._OZ_TRACTS_CACHE = None

    def _params(self) -> dict[str, Any]:
        p = _passing_params()
        p["scoring_weights"] = TestPhase5Composite.WEIGHTS
        return p

    def test_happy_path_inserts_parcel_score_and_log(self) -> None:
        # Centroid in South Fulton OZ stub.
        fake = Phase5FakeConnection(
            fetchone_queue=[
                ("fulton-001", "atlanta", -84.55, 33.55),  # _SQL_FETCH_PARCEL
                (1000.0, 1100.0, 1.5),                       # _SQL_S2_GEOMETRY
            ],
        )
        result = research.score_parcel(
            "fulton-001", conn=fake, cycle_id="score-atlanta-test-0001",
            params=self._params(),
        )
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["sub_scores"]["S2_parcel_geometry"], 7)  # 1000/1100=0.909
        self.assertEqual(result["sub_scores"]["S9_entitlement_complexity"], 5)
        self.assertEqual(result["sub_scores"]["S10_incentives"], 4)
        # parcel_scores INSERT issued with PENDING actionability.
        score_inserts = [
            (sql, params) for sql, params in fake.all_executes
            if "INSERT INTO parcel_scores" in sql
        ]
        self.assertEqual(len(score_inserts), 1)
        self.assertEqual(score_inserts[0][1][3], "PENDING")
        # research_log scoring row issued.
        log_inserts = [
            sql for sql, params in fake.all_executes
            if "INSERT INTO research_log" in sql and "scoring" in str(params)
        ]
        self.assertEqual(len(log_inserts), 1)

    def test_data_gap_flag_per_null_subscore(self) -> None:
        # 9 sub-scores stay null in MVP (S1, S3..S8, S11, S12) → 9 data_gap flags.
        fake = Phase5FakeConnection(
            fetchone_queue=[
                ("fulton-001", "atlanta", -84.55, 33.55),
                (1000.0, 1100.0, 1.5),
            ],
        )
        research.score_parcel(
            "fulton-001", conn=fake, cycle_id="score-atlanta-test-0002",
            params=self._params(),
        )
        flag_inserts = [
            params[3] for sql, params in fake.all_executes
            if "flagged_items" in sql and len(params) >= 4
        ]
        for null_subscore in (
            "S1_interstate_proximity", "S3_topography", "S4_submarket_vacancy",
            "S5_submarket_absorption", "S6_competing_pipeline", "S7_labor_pool",
            "S8_land_basis", "S11_rail_adjacency", "S12_demand_generators",
        ):
            self.assertTrue(
                any(null_subscore in d for d in flag_inserts),
                f"{null_subscore} data_gap flag not seen: {flag_inserts}",
            )
        # And no flag for the populated ones.
        for populated in (
            "S2_parcel_geometry", "S9_entitlement_complexity", "S10_incentives",
        ):
            self.assertFalse(
                any(populated in d for d in flag_inserts),
                f"unexpected data_gap flag for populated {populated}: {flag_inserts}",
            )

    def test_missing_parcel_returns_missing_status(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[])  # _SQL_FETCH_PARCEL returns None
        result = research.score_parcel(
            "fulton-nonexistent", conn=fake, cycle_id="score-atlanta-test-0003",
            params=self._params(),
        )
        self.assertEqual(result["status"], "missing")
        # No parcel_scores INSERT.
        score_inserts = [
            sql for sql, _ in fake.all_executes if "INSERT INTO parcel_scores" in sql
        ]
        self.assertEqual(score_inserts, [])

    def test_actionability_is_pending(self) -> None:
        # Phase 5 MUST set actionability='PENDING' so the metric SQL excludes
        # these rows from actionable_pipeline_count until Phase 8 runs.
        fake = Phase5FakeConnection(
            fetchone_queue=[
                ("fulton-001", "atlanta", -84.55, 33.55),
                (1000.0, 1100.0, 1.5),
            ],
        )
        research.score_parcel(
            "fulton-001", conn=fake, cycle_id="score-atlanta-test-0004",
            params=self._params(),
        )
        score_insert_params = next(
            params for sql, params in fake.all_executes
            if "INSERT INTO parcel_scores" in sql
        )
        # Position 3 in _SQL_INSERT_PARCEL_SCORE is actionability.
        self.assertEqual(score_insert_params[3], "PENDING")


class TestPhase5ParcelScoresAppendOnly(unittest.TestCase):
    """R-204, R-210 — versioned-append; two calls = two INSERTs."""

    def setUp(self) -> None:
        research._OZ_TRACTS_CACHE = None

    def test_two_calls_produce_two_inserts(self) -> None:
        params = _passing_params()
        params["scoring_weights"] = TestPhase5Composite.WEIGHTS
        fake = Phase5FakeConnection(
            fetchone_queue=[
                ("fulton-001", "atlanta", -84.55, 33.55),
                (1000.0, 1100.0, 1.5),
                ("fulton-001", "atlanta", -84.55, 33.55),
                (1000.0, 1100.0, 1.5),
            ],
        )
        research.score_parcel(
            "fulton-001", conn=fake, cycle_id="score-atlanta-call1-aaaa",
            params=params,
        )
        research.score_parcel(
            "fulton-001", conn=fake, cycle_id="score-atlanta-call2-bbbb",
            params=params,
        )
        score_inserts = [
            sql for sql, _ in fake.all_executes
            if "INSERT INTO parcel_scores" in sql
        ]
        self.assertEqual(len(score_inserts), 2)
        # And no UPDATE / DELETE against parcel_scores anywhere.
        for sql, _ in fake.all_executes:
            self.assertNotIn("UPDATE parcel_scores", sql)
            self.assertNotIn("DELETE FROM parcel_scores", sql)


class TestPhase5RunScoringCycle(unittest.TestCase):
    """run_scoring_cycle driver behavior."""

    def setUp(self) -> None:
        research._OZ_TRACTS_CACHE = None

    def test_unsupported_market_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            research.run_scoring_cycle("dallas-fort-worth")

    def test_iterates_unscored_parcels(self) -> None:
        params = _passing_params()
        params["scoring_weights"] = TestPhase5Composite.WEIGHTS
        # Cycle: collision check (None default → 0), unscored list (2 parcels),
        # then per-parcel: fetch + S2.
        fake = Phase5FakeConnection(
            fetchone_queue=[
                (0,),                                     # collision check
                ("fulton-001", "atlanta", -84.55, 33.55), # parcel 1 fetch
                (1000.0, 1100.0, 1.5),                    # parcel 1 S2
                ("fulton-002", "atlanta", -83.0, 35.0),   # parcel 2 fetch (outside OZ)
                (500.0, 1000.0, 4.0),                     # parcel 2 S2 (compactness 0.5 → 0)
            ],
            fetchall_queue=[
                [("fulton-001",), ("fulton-002",)],
            ],
        )

        @contextmanager
        def _ctx():
            yield fake

        with mock.patch("research.prepare.get_parameters", return_value=params), \
                mock.patch("research.prepare.verify_parameters_unchanged", return_value=None), \
                mock.patch("research.prepare.get_connection", _ctx):
            summary = research.run_scoring_cycle("atlanta")

        self.assertFalse(summary["aborted"])
        self.assertEqual(summary["counts"]["scored"], 2)
        self.assertEqual(len(summary["parcels"]), 2)
        # Both parcels got parcel_scores INSERTs.
        score_inserts = [
            sql for sql, _ in fake.all_executes if "INSERT INTO parcel_scores" in sql
        ]
        self.assertEqual(len(score_inserts), 2)

    def test_cycle_id_collision_aborts(self) -> None:
        params = _passing_params()
        params["scoring_weights"] = TestPhase5Composite.WEIGHTS
        fake = Phase5FakeConnection(
            fetchone_queue=[(7,)],  # collision check returns 7
        )

        @contextmanager
        def _ctx():
            yield fake

        with mock.patch("research.prepare.get_parameters", return_value=params), \
                mock.patch("research.prepare.verify_parameters_unchanged", return_value=None), \
                mock.patch("research.prepare.get_connection", _ctx):
            summary = research.run_scoring_cycle("atlanta")

        self.assertTrue(summary["aborted"])
        self.assertEqual(summary["abort_reason"], "cycle_id_collision")
        # No parcel_scores INSERTs after abort.
        score_inserts = [
            sql for sql, _ in fake.all_executes if "INSERT INTO parcel_scores" in sql
        ]
        self.assertEqual(score_inserts, [])


class TestPhase5OzDataFile(unittest.TestCase):
    """Bundled OZ stub file structure (R-205)."""

    def test_oz_stub_file_exists(self) -> None:
        self.assertTrue(research._OZ_DATA_PATH.is_file())

    def test_oz_stub_loads_as_geojson(self) -> None:
        research._OZ_TRACTS_CACHE = None
        tracts = research._load_oz_tracts()
        # Stub has 2 features; verify we got polygons.
        self.assertGreaterEqual(len(tracts), 1)
        for bbox, rings, props in tracts:
            self.assertEqual(len(bbox), 4)
            self.assertGreaterEqual(len(rings[0]), 4)  # closed ring


class TestPhase5SqlConstantsStaticChecks(unittest.TestCase):
    """R-202, R-204 — static SQL invariants."""

    def test_no_update_or_delete_against_parcel_scores(self) -> None:
        forbidden = ("UPDATE parcel_scores", "DELETE FROM parcel_scores")
        for needle in forbidden:
            self.assertNotIn(
                needle, RESEARCH_PY_SRC,
                f"Phase 5 must be append-only against parcel_scores; found {needle!r}",
            )

    def test_scoring_sql_uses_parameterized_placeholders(self) -> None:
        # Every new SQL constant uses %s placeholders, no f-string interpolation.
        for const in (
            "_SQL_INSERT_PARCEL_SCORE",
            "_SQL_INSERT_RESEARCH_LOG_SCORING",
            "_SQL_FETCH_PARCEL",
            "_SQL_S2_GEOMETRY",
            "_SQL_LIST_UNSCORED_PARCELS",
            "_SQL_COUNT_LOG_FOR_SCORING_CYCLE",
        ):
            self.assertTrue(hasattr(research, const), f"missing SQL constant {const}")
            sql = getattr(research, const)
            self.assertIsInstance(sql, str)
            # Must not contain runtime-format markers — only %s for psycopg.
            self.assertNotIn("{", sql, f"f-string brace in {const}: {sql}")


# ===========================================================================
# Phase 6 — CoStar ingestion (Option A) tests
# ===========================================================================
@contextmanager
def _temp_costar_base():
    """Monkey-patch research._COSTAR_BASE_DIR to a tempdir for the test scope (R-329)."""
    original = research._COSTAR_BASE_DIR
    with tempfile.TemporaryDirectory() as td:
        research._COSTAR_BASE_DIR = Path(td)
        try:
            yield Path(td)
        finally:
            research._COSTAR_BASE_DIR = original


def _stage_fixture(base: Path, subdir: str, fixture_name: str, dest_name: str | None = None) -> Path:
    """Copy a fixture file into the temp costar tree under subdir/."""
    target_dir = base / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (dest_name or fixture_name)
    shutil.copy2(COSTAR_FIXTURES_DIR / fixture_name, target)
    return target


class TestPhase6Slugify(unittest.TestCase):
    """R-301, R-315 — slug derivation rules."""

    def test_simple_lowercase(self) -> None:
        self.assertEqual(research._slugify("Atlanta"), "atlanta")

    def test_punctuation_collapses_to_underscore(self) -> None:
        self.assertEqual(
            research._slugify("West Atlanta / I-20"),
            "west_atlanta_i_20",
        )

    def test_strips_edges(self) -> None:
        self.assertEqual(research._slugify("  South Fulton  "), "south_fulton")

    def test_truncates_long_inputs(self) -> None:
        long = "x" * 200
        self.assertEqual(len(research._slugify(long)), 60)

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            research._slugify("")

    def test_punctuation_only_raises(self) -> None:
        with self.assertRaises(ValueError):
            research._slugify("///---")


class TestPhase6IngestionCycleId(unittest.TestCase):
    """R-321 — cycle id format and uniqueness."""

    def test_format(self) -> None:
        cid = research._make_ingestion_cycle_id()
        self.assertRegex(cid, research._INGESTION_CYCLE_ID_RE)

    def test_uniqueness(self) -> None:
        a = research._make_ingestion_cycle_id()
        b = research._make_ingestion_cycle_id()
        self.assertNotEqual(a, b)


class TestPhase6ScanExportDir(unittest.TestCase):
    """R-303, R-304, R-305, R-310, R-311 — directory scanning."""

    def test_empty_dir_returns_empty(self) -> None:
        with _temp_costar_base():
            self.assertEqual(research._scan_export_dir("submarket_stats"), [])

    def test_missing_dir_returns_empty(self) -> None:
        with _temp_costar_base() as base:
            self.assertFalse((base / "submarket_stats").exists())
            self.assertEqual(research._scan_export_dir("submarket_stats"), [])

    def test_returns_matching_files_sorted_by_date(self) -> None:
        with _temp_costar_base() as base:
            d = base / "submarket_stats"
            d.mkdir(parents=True)
            (d / "submarket_stats_20260420.csv").write_text("x", encoding="utf-8")
            (d / "submarket_stats_20260427.csv").write_text("x", encoding="utf-8")
            (d / "submarket_stats_20260413.csv").write_text("x", encoding="utf-8")
            (d / "ignore_me.txt").write_text("x", encoding="utf-8")
            (d / ".hidden.csv").write_text("x", encoding="utf-8")
            results = research._scan_export_dir("submarket_stats")
            dates = [d for _, d in results]
            self.assertEqual(dates, ["20260413", "20260420", "20260427"])

    def test_archived_and_failed_subdirs_skipped(self) -> None:
        with _temp_costar_base() as base:
            d = base / "submarket_stats"
            d.mkdir(parents=True)
            (d / "submarket_stats_20260427.csv").write_text("x", encoding="utf-8")
            archived = base / "ARCHIVED" / "submarket_stats"
            archived.mkdir(parents=True)
            (archived / "submarket_stats_20260101.csv").write_text(
                "x", encoding="utf-8",
            )
            results = research._scan_export_dir("submarket_stats")
            names = [p.name for p, _ in results]
            self.assertEqual(names, ["submarket_stats_20260427.csv"])

    def test_directory_traversal_rejected(self) -> None:
        with _temp_costar_base():
            with self.assertRaises(ValueError):
                research._scan_export_dir("../etc")
            with self.assertRaises(ValueError):
                research._scan_export_dir("/abs/path")


class TestPhase6ArchiveAndFailMovement(unittest.TestCase):
    """R-312, R-313, R-314 — archive / fail file movement."""

    def test_archive_round_trip(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(base, "submarket_stats", "submarket_stats_happy.csv",
                                    dest_name="submarket_stats_20260427.csv")
            archived = research._archive_file(staged)
            self.assertFalse(staged.exists())
            self.assertTrue(archived.exists())
            self.assertEqual(archived.parent.name, "submarket_stats")
            self.assertEqual(archived.parent.parent.name, "ARCHIVED")
            self.assertTrue(archived.name.startswith("submarket_stats_20260427_"))
            self.assertTrue(archived.name.endswith(".csv"))

    def test_fail_writes_error_json(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(base, "submarket_stats",
                                    "submarket_stats_missing_column.csv",
                                    dest_name="submarket_stats_20260427.csv")
            dest, err_path = research._fail_file(
                staged, {"errors": ["missing required column(s): vacancy_rate_pct"]}
            )
            self.assertFalse(staged.exists())
            self.assertTrue(dest.exists())
            self.assertEqual(dest.parent.parent.name, "FAILED")
            self.assertTrue(err_path.exists())
            payload = json.loads(err_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["file"], "submarket_stats_20260427.csv")
            self.assertIn("missing required column", payload["errors"][0])
            self.assertIn("ingested_at", payload)

    def test_archive_destination_collision_uniquified(self) -> None:
        with _temp_costar_base() as base:
            a_dir = base / "submarket_stats"
            a_dir.mkdir(parents=True)
            f1 = a_dir / "submarket_stats_20260427.csv"
            f1.write_text("a", encoding="utf-8")
            d1 = research._archive_destination(f1, "ARCHIVED")
            d2 = research._archive_destination(f1, "ARCHIVED")
            self.assertNotEqual(d1, d2)


class TestPhase6Coercion(unittest.TestCase):
    """R-306 — locale-tolerant number parsing."""

    def test_plain_int(self) -> None:
        self.assertEqual(research._coerce_optional_int("28500000"), (28500000, None))

    def test_thousands_commas_stripped(self) -> None:
        self.assertEqual(research._coerce_optional_int("28,500,000"), (28500000, None))

    def test_dollar_sign_stripped(self) -> None:
        val, err = research._coerce_optional_decimal("$7.85")
        self.assertEqual(val, 7.85)
        self.assertIsNone(err)

    def test_percent_sign_stripped(self) -> None:
        val, err = research._coerce_optional_decimal("5.4%")
        self.assertEqual(val, 5.4)
        self.assertIsNone(err)

    def test_blank_returns_none(self) -> None:
        self.assertEqual(research._coerce_optional_decimal(""), (None, None))
        self.assertEqual(research._coerce_optional_decimal("N/A"), (None, None))

    def test_unparseable_returns_error(self) -> None:
        val, err = research._coerce_optional_decimal("xyz")
        self.assertIsNone(val)
        self.assertIn("unparseable", err)

    def test_negative_int_supported(self) -> None:
        self.assertEqual(research._coerce_optional_int("-420000"), (-420000, None))


class TestPhase6DateParsing(unittest.TestCase):
    """R-307 — multiple acceptable date formats."""

    def test_iso_format(self) -> None:
        self.assertEqual(research._parse_report_date("2026-04-27"),
                         ("2026-04-27", None))

    def test_us_slash_format(self) -> None:
        self.assertEqual(research._parse_report_date("04/27/2026"),
                         ("2026-04-27", None))

    def test_iso_with_time(self) -> None:
        self.assertEqual(research._parse_report_date("2026-04-27T00:00:00"),
                         ("2026-04-27", None))

    def test_unparseable_returns_error(self) -> None:
        out, err = research._parse_report_date("not-a-date")
        self.assertIsNone(out)
        self.assertIn("unparseable", err)

    def test_empty_returns_error(self) -> None:
        out, err = research._parse_report_date("")
        self.assertIsNone(out)


class TestPhase6HeaderValidation(unittest.TestCase):
    """R-309, R-310 — header set + duplicate detection."""

    def test_happy_headers(self) -> None:
        headers = list(research._SUBMARKET_STATS_REQUIRED_COLUMNS)
        self.assertIsNone(research._validate_submarket_stats_headers(headers))

    def test_missing_column_detected(self) -> None:
        headers = [c for c in research._SUBMARKET_STATS_REQUIRED_COLUMNS
                   if c != "vacancy_rate_pct"]
        err = research._validate_submarket_stats_headers(headers)
        self.assertIn("vacancy_rate_pct", err)

    def test_duplicate_column_detected(self) -> None:
        headers = list(research._SUBMARKET_STATS_REQUIRED_COLUMNS) + ["submarket_name"]
        err = research._validate_submarket_stats_headers(headers)
        self.assertIn("duplicate", err)

    def test_extra_columns_allowed(self) -> None:
        headers = list(research._SUBMARKET_STATS_REQUIRED_COLUMNS) + ["extra_col"]
        self.assertIsNone(research._validate_submarket_stats_headers(headers))

    def test_case_insensitive_headers(self) -> None:
        headers = [c.upper() for c in research._SUBMARKET_STATS_REQUIRED_COLUMNS]
        self.assertIsNone(research._validate_submarket_stats_headers(headers))

    def test_bom_stripped(self) -> None:
        headers = list(research._SUBMARKET_STATS_REQUIRED_COLUMNS)
        headers[0] = "﻿" + headers[0]
        self.assertIsNone(research._validate_submarket_stats_headers(headers))


class TestPhase6RowValidation(unittest.TestCase):
    """R-306, R-307, validation rules from COSTAR_INGESTION_CONTRACT.md."""

    def _row(self, **overrides):
        base = {
            "submarket_name": "South Fulton",
            "market": "Atlanta",
            "total_inventory_sf": "28500000",
            "vacancy_rate_pct": "5.4",
            "availability_rate_pct": "7.1",
            "net_absorption_t12_sf": "1850000",
            "under_construction_sf": "2400000",
            "proposed_sf": "1200000",
            "asking_rent_nnn_psf": "7.85",
            "report_date": "2026-04-27",
        }
        base.update(overrides)
        return base

    def test_happy_row(self) -> None:
        out, err = research._validate_submarket_stats_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["submarket_name"], "South Fulton")
        self.assertEqual(out["report_date"], "2026-04-27")
        self.assertEqual(out["vacancy_rate_pct"], 5.4)

    def test_empty_submarket_name_rejected(self) -> None:
        out, err = research._validate_submarket_stats_row(self._row(submarket_name=""))
        self.assertIsNone(out)
        self.assertIn("submarket_name", err)

    def test_vacancy_out_of_range_rejected(self) -> None:
        out, err = research._validate_submarket_stats_row(
            self._row(vacancy_rate_pct="150"),
        )
        self.assertIsNone(out)
        self.assertIn("vacancy_rate_pct", err)

    def test_zero_rent_rejected(self) -> None:
        out, err = research._validate_submarket_stats_row(
            self._row(asking_rent_nnn_psf="0"),
        )
        self.assertIsNone(out)
        self.assertIn("asking_rent_nnn_psf", err)

    def test_optional_field_null_accepted(self) -> None:
        out, err = research._validate_submarket_stats_row(
            self._row(availability_rate_pct=""),
        )
        self.assertIsNone(err)
        self.assertIsNone(out["availability_rate_pct"])

    def test_unparseable_date_rejected(self) -> None:
        out, err = research._validate_submarket_stats_row(
            self._row(report_date="not-a-date"),
        )
        self.assertIsNone(out)
        self.assertIn("date", err)

    def test_negative_absorption_accepted(self) -> None:
        out, err = research._validate_submarket_stats_row(
            self._row(net_absorption_t12_sf="-500000"),
        )
        self.assertIsNone(err)
        self.assertEqual(out["net_absorption_t12_sf"], -500000)


class TestPhase6EnsureSubmarket(unittest.TestCase):
    """R-301, R-315 — auto-UPSERT markets/submarkets reference data."""

    def test_creates_new_submarket_returning_name(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[("South Fulton",)])
        sid, created, drift = research._ensure_submarket(fake, "Atlanta", "South Fulton")
        self.assertEqual(sid, "atlanta__south_fulton")
        self.assertTrue(created)
        self.assertIsNone(drift)
        self.assertEqual(len(fake.all_executes), 2)

    def test_existing_submarket_no_drift(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[None, ("South Fulton",)])
        sid, created, drift = research._ensure_submarket(fake, "Atlanta", "South Fulton")
        self.assertEqual(sid, "atlanta__south_fulton")
        self.assertFalse(created)
        self.assertIsNone(drift)

    def test_name_drift_emits_message(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[None, ("Old Name",)])
        sid, created, drift = research._ensure_submarket(fake, "Atlanta", "New Name")
        self.assertFalse(created)
        self.assertIsNotNone(drift)
        self.assertIn("Old Name", drift)
        self.assertIn("New Name", drift)


class TestPhase6LoadSubmarketStatsFile(unittest.TestCase):
    """End-to-end loader against Phase5FakeConnection + tempdir."""

    def test_happy_path_loads_and_archives(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "submarket_stats", "submarket_stats_happy.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("West Atlanta / I-20",),
                ("Clayton County",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_submarket_stats_file(fake, cycle_id, staged)

            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["rows_loaded"], 3)
            self.assertEqual(result["rows_failed"], 0)
            self.assertEqual(len(result["submarkets_auto_created"]), 3)
            self.assertEqual(fake.commits, 1)
            self.assertEqual(fake.rollbacks, 0)
            self.assertFalse(staged.exists())
            archived_dir = base / "ARCHIVED" / "submarket_stats"
            self.assertTrue(archived_dir.exists())
            self.assertTrue(any(archived_dir.iterdir()))

    def test_missing_column_quarantines_file(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "submarket_stats",
                "submarket_stats_missing_column.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            fake = Phase5FakeConnection()
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_submarket_stats_file(fake, cycle_id, staged)

            self.assertEqual(result["status"], "failed")
            self.assertIn("vacancy_rate_pct", result["error"])
            self.assertEqual(fake.commits, 0)
            self.assertFalse(staged.exists())
            failed_dir = base / "FAILED" / "submarket_stats"
            self.assertTrue(any(failed_dir.iterdir()))
            err_files = list(failed_dir.glob("*.error.json"))
            self.assertEqual(len(err_files), 1)

    def test_duplicate_header_quarantines_file(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "submarket_stats",
                "submarket_stats_duplicate_header.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            fake = Phase5FakeConnection()
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_submarket_stats_file(fake, cycle_id, staged)

            self.assertEqual(result["status"], "failed")
            self.assertIn("duplicate", result["error"].lower())

    def test_row_errors_flagged_but_other_rows_load(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "submarket_stats", "submarket_stats_row_errors.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[("South Fulton",)])
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_submarket_stats_file(fake, cycle_id, staged)

            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["rows_loaded"], 1)
            self.assertEqual(result["rows_failed"], 2)
            self.assertEqual(len(result["row_errors"]), 2)
            self.assertFalse(staged.exists())
            self.assertTrue(any((base / "ARCHIVED" / "submarket_stats").iterdir()))

    def test_bom_csv_loads_cleanly(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "submarket_stats", "submarket_stats_with_bom.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[("South Fulton",)])
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_submarket_stats_file(fake, cycle_id, staged)
            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["rows_loaded"], 1)


class TestPhase6Reingest(unittest.TestCase):
    """R-302 — DELETE-then-INSERT idempotent re-ingest."""

    def test_dedup_delete_executed_per_unique_key(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "submarket_stats", "submarket_stats_happy.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("West Atlanta / I-20",),
                ("Clayton County",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            research._load_submarket_stats_file(fake, cycle_id, staged)

            sql_strings = [sql for sql, _ in fake.all_executes]
            delete_count = sum(
                1 for s in sql_strings
                if s == research._SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST
            )
            self.assertEqual(delete_count, 3)
            insert_count = sum(
                1 for s in sql_strings
                if s == research._SQL_INSERT_MARKET_CONTEXT
            )
            self.assertEqual(insert_count, 3)

    def test_delete_uses_costar_source_param(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "submarket_stats", "submarket_stats_happy.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("West Atlanta / I-20",),
                ("Clayton County",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            research._load_submarket_stats_file(fake, cycle_id, staged)
            for sql, params in fake.all_executes:
                if sql == research._SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST:
                    self.assertEqual(params[0], "costar")


class TestPhase6RunIngestionCycle(unittest.TestCase):
    """R-321, R-322 — driver dispatch + cycle-id collision guard."""

    def _patch_get_connection(self, fake):
        @contextmanager
        def _ctx():
            yield fake
        return mock.patch("research.prepare.get_connection", _ctx)

    def test_collision_aborts(self) -> None:
        with _temp_costar_base():
            fake = Phase5FakeConnection(fetchone_queue=[(1,)])
            with self._patch_get_connection(fake):
                summary = research.run_ingestion_cycle()
            self.assertTrue(summary["aborted"])
            self.assertEqual(summary["abort_reason"], "cycle_id_collision")

    def test_no_files_returns_clean_summary(self) -> None:
        # Phase 6.1: all 5 export types are now real loaders; with no
        # files staged, each reports files_loaded=0 (not 'not_implemented').
        with _temp_costar_base():
            fake = Phase5FakeConnection(fetchone_queue=[(0,)])
            with self._patch_get_connection(fake):
                summary = research.run_ingestion_cycle()
            self.assertFalse(summary["aborted"])
            for export_type in (
                "submarket_stats", "land_sales_comps", "building_sales_comps",
                "leasing_comps", "land_listings",
            ):
                self.assertIn(export_type, summary["per_export_type"])
                self.assertEqual(
                    summary["per_export_type"][export_type]["files_loaded"], 0,
                )
                self.assertEqual(
                    summary["per_export_type"][export_type]["status"], "loaded",
                )

    def test_dispatch_processes_submarket_stats_file(self) -> None:
        with _temp_costar_base() as base:
            _stage_fixture(
                base, "submarket_stats", "submarket_stats_happy.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[
                (0,),
                ("South Fulton",),
                ("West Atlanta / I-20",),
                ("Clayton County",),
            ])
            with self._patch_get_connection(fake):
                summary = research.run_ingestion_cycle()
            ss = summary["per_export_type"]["submarket_stats"]
            self.assertEqual(ss["files_loaded"], 1)
            self.assertEqual(ss["rows_loaded"], 3)
            self.assertEqual(ss["rows_failed"], 0)


class TestPhase6SqlConstantsStaticChecks(unittest.TestCase):
    """R-326 — static SQL invariants for Phase 6."""

    def test_ingestion_sql_uses_parameterized_placeholders(self) -> None:
        for const in (
            "_SQL_UPSERT_MARKETS_REF",
            "_SQL_UPSERT_SUBMARKETS_REF",
            "_SQL_FETCH_SUBMARKET_NAME",
            "_SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST",
            "_SQL_INSERT_MARKET_CONTEXT",
            "_SQL_INSERT_RESEARCH_LOG_INGESTION",
            "_SQL_COUNT_LOG_FOR_INGESTION_CYCLE",
        ):
            self.assertTrue(hasattr(research, const), f"missing SQL constant {const}")
            sql = getattr(research, const)
            self.assertIsInstance(sql, str)
            self.assertNotIn("{", sql, f"f-string brace in {const}: {sql}")

    def test_no_print_in_ingestion_helpers(self) -> None:
        tree = ast.parse(RESEARCH_PY_SRC)
        forbidden_names = {
            "run_ingestion_cycle", "_load_submarket_stats_file",
            "_load_submarket_stats", "_load_placeholder",
            "_scan_export_dir", "_archive_file", "_fail_file",
            "_validate_submarket_stats_row",
            "_validate_submarket_stats_headers",
            "_ensure_submarket", "_count_log_rows_for_ingestion_cycle",
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name not in forbidden_names:
                    continue
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Name)
                            and inner.func.id == "print"):
                        self.fail(f"print() in {node.name} at line {inner.lineno}")


# ===========================================================================
# Phase 6.1 — CoStar comps + listings loaders tests
# ===========================================================================
class TestPhase61CountyToMarket(unittest.TestCase):
    """R-401, R-404 — county→market lookup."""

    def test_known_county_resolves(self) -> None:
        market, used_default = research._resolve_market_from_county("Fulton")
        self.assertEqual(market, "Atlanta")
        self.assertFalse(used_default)

    def test_case_insensitive(self) -> None:
        market, used_default = research._resolve_market_from_county("DEKALB")
        self.assertEqual(market, "Atlanta")
        self.assertFalse(used_default)

    def test_unknown_county_uses_default(self) -> None:
        market, used_default = research._resolve_market_from_county("Forsyth")
        self.assertEqual(market, "Atlanta")
        self.assertTrue(used_default)

    def test_blank_uses_default(self) -> None:
        market, used_default = research._resolve_market_from_county("")
        self.assertTrue(used_default)
        market2, _ = research._resolve_market_from_county(None)
        self.assertEqual(market2, "Atlanta")

    def test_lookup_covers_eight_atlanta_counties(self) -> None:
        for county in (
            "fulton", "dekalb", "cobb", "gwinnett",
            "clayton", "henry", "spalding", "fayette",
        ):
            self.assertEqual(research._COUNTY_TO_MARKET[county], "Atlanta")


class TestPhase61LandSalesCompsValidation(unittest.TestCase):
    """R-406, R-407 — land sales comps row validator."""

    def _row(self, **overrides):
        base = {
            "address": "1234 Industrial Blvd",
            "parcel_id": "09F-1234-0001",
            "county": "Fulton",
            "submarket": "South Fulton",
            "acres": "12.5",
            "sale_date": "2026-03-14",
            "sale_price": "1875000",
            "price_per_acre": "150000",
            "buyer_name": "Acme Logistics LLC",
            "seller_name": "Smith Family Trust",
            "zoning": "M-2",
            "intended_use": "distribution",
            "cap_rate": "",
        }
        base.update(overrides)
        return base

    def test_happy(self) -> None:
        out, err = research._validate_land_sales_comps_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["address"], "1234 Industrial Blvd")
        self.assertEqual(out["sale_price"], 1875000)
        self.assertEqual(out["acres"], 12.5)
        self.assertIsNone(out["cap_rate"])
        # raw is preserved for the JSONB column
        self.assertIn("intended_use", out["raw"])

    def test_blank_address_rejected(self) -> None:
        out, err = research._validate_land_sales_comps_row(self._row(address=""))
        self.assertIsNone(out)
        self.assertIn("address", err)

    def test_zero_sale_price_rejected(self) -> None:
        out, err = research._validate_land_sales_comps_row(self._row(sale_price="0"))
        self.assertIsNone(out)
        self.assertIn("sale_price", err)

    def test_blank_acres_rejected(self) -> None:
        out, err = research._validate_land_sales_comps_row(self._row(acres=""))
        self.assertIsNone(out)
        self.assertIn("acres", err)

    def test_unparseable_sale_date_rejected(self) -> None:
        out, err = research._validate_land_sales_comps_row(self._row(sale_date="not-a-date"))
        self.assertIsNone(out)
        self.assertIn("sale_date", err)

    def test_dollar_signs_in_price_accepted(self) -> None:
        out, err = research._validate_land_sales_comps_row(
            self._row(sale_price="$1,875,000")
        )
        self.assertIsNone(err)
        self.assertEqual(out["sale_price"], 1875000)


class TestPhase61BuildingSalesCompsValidation(unittest.TestCase):
    """R-406, R-408 — building sales comps row validator."""

    def _row(self, **overrides):
        base = {
            "address": "4040 Logistics Dr",
            "submarket": "South Fulton",
            "building_sf": "250000",
            "year_built": "2015",
            "clear_height_ft": "32",
            "sale_date": "2026-03-05",
            "sale_price": "32500000",
            "price_psf": "130",
            "cap_rate": "5.5",
            "noi_at_sale": "1787500",
            "buyer_name": "Industrial REIT",
            "seller_name": "Original Developer LLC",
            "tenant_at_sale": "Amazon Logistics",
            "lease_term_remaining_years": "5.2",
        }
        base.update(overrides)
        return base

    def test_happy(self) -> None:
        out, err = research._validate_building_sales_comps_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["building_sf"], 250000.0)
        self.assertEqual(out["sale_price"], 32500000)
        self.assertIn("tenant_at_sale", out["raw"])
        self.assertIn("lease_term_remaining_years", out["raw"])

    def test_zero_building_sf_rejected(self) -> None:
        out, err = research._validate_building_sales_comps_row(
            self._row(building_sf="0"),
        )
        self.assertIsNone(out)
        self.assertIn("building_sf", err)

    def test_year_built_out_of_range_rejected(self) -> None:
        out, err = research._validate_building_sales_comps_row(
            self._row(year_built="1700"),
        )
        self.assertIsNone(out)
        self.assertIn("year_built", err)

    def test_clear_height_out_of_range_rejected(self) -> None:
        out, err = research._validate_building_sales_comps_row(
            self._row(clear_height_ft="100"),
        )
        self.assertIsNone(out)
        self.assertIn("clear_height_ft", err)

    def test_optional_fields_null_accepted(self) -> None:
        out, err = research._validate_building_sales_comps_row(
            self._row(year_built="", clear_height_ft="", price_psf="", cap_rate=""),
        )
        self.assertIsNone(err)
        self.assertIsNone(out["price_psf"])
        self.assertIsNone(out["cap_rate"])


class TestPhase61LeasingCompsValidation(unittest.TestCase):
    """R-409, R-410 — leasing comps row validator."""

    def _row(self, **overrides):
        base = {
            "address": "4040 Logistics Dr",
            "submarket": "South Fulton",
            "tenant_name": "Amazon Logistics",
            "tenant_industry": "3PL",
            "lease_start_date": "2025-09-15",
            "lease_term_months": "84",
            "building_sf_leased": "250000",
            "starting_rent_psf_nnn": "7.95",
            "rent_escalation_pct": "3.0",
            "lease_type": "NNN",
        }
        base.update(overrides)
        return base

    def test_happy(self) -> None:
        out, err = research._validate_leasing_comps_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["tenant_name"], "Amazon Logistics")
        self.assertEqual(out["lease_term_months"], 84)
        self.assertEqual(out["starting_rent_psf_nnn"], 7.95)

    def test_blank_tenant_rejected(self) -> None:
        out, err = research._validate_leasing_comps_row(self._row(tenant_name=""))
        self.assertIsNone(out)
        self.assertIn("tenant_name", err)

    def test_zero_term_rejected(self) -> None:
        out, err = research._validate_leasing_comps_row(self._row(lease_term_months="0"))
        self.assertIsNone(out)
        self.assertIn("lease_term_months", err)

    def test_zero_rent_rejected(self) -> None:
        out, err = research._validate_leasing_comps_row(
            self._row(starting_rent_psf_nnn="0"),
        )
        self.assertIsNone(out)
        self.assertIn("starting_rent_psf_nnn", err)

    def test_optional_escalation_null_accepted(self) -> None:
        out, err = research._validate_leasing_comps_row(self._row(rent_escalation_pct=""))
        self.assertIsNone(err)
        self.assertIsNone(out["rent_escalation_pct"])

    def test_naics_must_be_digits(self) -> None:
        # naics_code is not in required cols, but if a CSV has one, validate.
        row = self._row()
        row["naics_code"] = "abc123"
        out, err = research._validate_leasing_comps_row(row)
        self.assertIsNone(out)
        self.assertIn("naics_code", err)


class TestPhase61LandListingsValidation(unittest.TestCase):
    """R-411, R-412 — land listings row validator."""

    def _row(self, **overrides):
        base = {
            "address": "1500 Industrial Pkwy",
            "parcel_id": "09F-1500-0007",
            "county": "Fulton",
            "submarket": "South Fulton",
            "acres": "18.5",
            "zoning": "M-2",
            "topography_notes": "gentle slope",
            "asking_price": "2775000",
            "asking_price_per_acre": "150000",
            "listing_date": "2026-04-01",
            "days_on_market": "42",
            "listing_broker": "Jane Doe",
            "listing_broker_firm": "CBRE",
            "utilities_status": "water+sewer",
            "entitlement_status": "zoned ready",
        }
        base.update(overrides)
        return base

    def test_happy(self) -> None:
        out, err = research._validate_land_listings_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["asking_price"], 2775000)
        self.assertEqual(out["acres"], 18.5)

    def test_blank_address_rejected(self) -> None:
        out, err = research._validate_land_listings_row(self._row(address=""))
        self.assertIsNone(out)

    def test_asking_price_null_accepted(self) -> None:
        out, err = research._validate_land_listings_row(
            self._row(asking_price="", asking_price_per_acre=""),
        )
        self.assertIsNone(err)
        self.assertIsNone(out["asking_price"])
        self.assertIsNone(out["asking_price_per_acre"])

    def test_zero_acres_rejected(self) -> None:
        out, err = research._validate_land_listings_row(self._row(acres="0"))
        self.assertIsNone(out)
        self.assertIn("acres", err)

    def test_zero_asking_price_rejected(self) -> None:
        out, err = research._validate_land_listings_row(self._row(asking_price="0"))
        self.assertIsNone(out)
        self.assertIn("asking_price", err)

    def test_negative_days_on_market_rejected(self) -> None:
        out, err = research._validate_land_listings_row(self._row(days_on_market="-5"))
        self.assertIsNone(out)
        self.assertIn("days_on_market", err)


class TestPhase61LandSalesCompsLoader(unittest.TestCase):
    """End-to-end land_sales_comps loader."""

    def test_happy_path_loads_and_archives(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "land_sales_comps", "land_sales_comps_happy.csv",
                dest_name="land_sales_comps_202603.csv",
            )
            # 3 unique submarkets, all new -> 3 fetchone responses.
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("Clayton County",),
                ("Henry County / I-75 South",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_land_sales_comps_file(fake, cycle_id, staged)
            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["rows_loaded"], 3)
            self.assertEqual(result["rows_failed"], 0)
            self.assertEqual(fake.commits, 1)
            self.assertFalse(staged.exists())
            self.assertTrue(any((base / "ARCHIVED" / "land_sales_comps").iterdir()))

    def test_missing_column_quarantines_file(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "land_sales_comps", "land_sales_comps_missing_column.csv",
                dest_name="land_sales_comps_202603.csv",
            )
            fake = Phase5FakeConnection()
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_land_sales_comps_file(fake, cycle_id, staged)
            self.assertEqual(result["status"], "failed")
            self.assertIn("intended_use", result["error"])
            self.assertEqual(fake.commits, 0)
            self.assertFalse(staged.exists())
            self.assertTrue(any((base / "FAILED" / "land_sales_comps").iterdir()))

    def test_row_errors_flagged_other_rows_load(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "land_sales_comps", "land_sales_comps_row_errors.csv",
                dest_name="land_sales_comps_202603.csv",
            )
            # Happy rows: row 1 (South Fulton, Fulton county - known) and
            # row 4 (Cumming Industrial, Forsyth - unknown county defaults
            # to Atlanta). Both new -> 2 fetchone responses.
            # Row 2 fails (blank address); row 3 fails (zero sale_price).
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("Cumming Industrial",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_land_sales_comps_file(fake, cycle_id, staged)
            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["rows_loaded"], 2)
            self.assertEqual(result["rows_failed"], 2)
            # The Forsyth -> Atlanta default-market flag fires too.
            self.assertIn("Forsyth", result["default_market_used_for"])

    def test_dedup_delete_executed_per_unique_key(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "land_sales_comps", "land_sales_comps_happy.csv",
                dest_name="land_sales_comps_202603.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("Clayton County",),
                ("Henry County / I-75 South",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            research._load_land_sales_comps_file(fake, cycle_id, staged)
            sql_strings = [sql for sql, _ in fake.all_executes]
            delete_count = sum(
                1 for s in sql_strings
                if s == research._SQL_DELETE_LAND_SALES_FOR_REINGEST
            )
            insert_count = sum(
                1 for s in sql_strings
                if s == research._SQL_INSERT_LAND_SALES
            )
            self.assertEqual(delete_count, 3)
            self.assertEqual(insert_count, 3)


class TestPhase61BuildingSalesCompsLoader(unittest.TestCase):
    """End-to-end building_sales_comps loader."""

    def test_happy_path_loads_and_archives(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "building_sales_comps", "building_sales_comps_happy.csv",
                dest_name="building_sales_comps_202603.csv",
            )
            # 2 unique submarkets, all new -> 2 fetchone responses.
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("West Atlanta / I-20",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_building_sales_comps_file(fake, cycle_id, staged)
            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["rows_loaded"], 2)
            self.assertEqual(result["rows_failed"], 0)

    def test_dedup_uses_building_comp_type(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "building_sales_comps", "building_sales_comps_happy.csv",
                dest_name="building_sales_comps_202603.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("West Atlanta / I-20",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            research._load_building_sales_comps_file(fake, cycle_id, staged)
            sql_strings = [sql for sql, _ in fake.all_executes]
            # Building DELETE used, NOT land DELETE (R-422).
            self.assertGreater(
                sum(1 for s in sql_strings
                    if s == research._SQL_DELETE_BUILDING_SALES_FOR_REINGEST), 0,
            )
            self.assertEqual(
                sum(1 for s in sql_strings
                    if s == research._SQL_DELETE_LAND_SALES_FOR_REINGEST), 0,
            )


class TestPhase61LeasingCompsLoader(unittest.TestCase):
    """End-to-end leasing_comps loader."""

    def test_happy_path_loads_and_archives(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "leasing_comps", "leasing_comps_happy.csv",
                dest_name="leasing_comps_202603.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("West Atlanta / I-20",),
                ("Clayton County",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_leasing_comps_file(fake, cycle_id, staged)
            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["rows_loaded"], 3)
            self.assertEqual(result["rows_failed"], 0)

    def test_missing_column_quarantines_file(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "leasing_comps", "leasing_comps_missing_column.csv",
                dest_name="leasing_comps_202603.csv",
            )
            fake = Phase5FakeConnection()
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_leasing_comps_file(fake, cycle_id, staged)
            self.assertEqual(result["status"], "failed")
            self.assertIn("lease_start_date", result["error"])


class TestPhase61LandListingsLoader(unittest.TestCase):
    """End-to-end land_listings loader (snapshot semantics R-426)."""

    def test_happy_path_loads_and_archives(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "land_listings", "land_listings_happy.csv",
                dest_name="land_listings_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("Henry County / I-75 South",),
                ("Clayton County",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_land_listings_file(
                fake, cycle_id, staged, "2026-04-27",
            )
            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["rows_loaded"], 3)
            self.assertEqual(result["rows_failed"], 0)
            self.assertFalse(staged.exists())

    def test_optional_nulls_accepted(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "land_listings", "land_listings_optional_nulls.csv",
                dest_name="land_listings_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[("South Fulton",)])
            cycle_id = research._make_ingestion_cycle_id()
            result = research._load_land_listings_file(
                fake, cycle_id, staged, "2026-04-27",
            )
            self.assertEqual(result["status"], "loaded")
            self.assertEqual(result["rows_loaded"], 1)

    def test_dedup_uses_snapshot_date_and_address(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "land_listings", "land_listings_happy.csv",
                dest_name="land_listings_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[
                ("South Fulton",),
                ("Henry County / I-75 South",),
                ("Clayton County",),
            ])
            cycle_id = research._make_ingestion_cycle_id()
            research._load_land_listings_file(fake, cycle_id, staged, "2026-04-27")
            for sql, params in fake.all_executes:
                if sql == research._SQL_DELETE_LAND_LISTINGS_FOR_REINGEST:
                    # First param is snapshot_date, second is address.
                    self.assertEqual(params[0], "2026-04-27")

    def test_driver_passes_snapshot_date_from_filename(self) -> None:
        with _temp_costar_base() as base:
            _stage_fixture(
                base, "land_listings", "land_listings_happy.csv",
                dest_name="land_listings_20260427.csv",
            )

            @contextmanager
            def _ctx():
                yield Phase5FakeConnection(fetchone_queue=[
                    (0,),  # cycle id collision check
                    ("South Fulton",),
                    ("Henry County / I-75 South",),
                    ("Clayton County",),
                ])
            with mock.patch("research.prepare.get_connection", _ctx):
                summary = research.run_ingestion_cycle()
            ll = summary["per_export_type"]["land_listings"]
            self.assertEqual(ll["files_loaded"], 1)
            self.assertEqual(ll["rows_loaded"], 3)
            # The driver derived snapshot_date from filename "20260427".
            per_file = ll["per_file"][0]
            self.assertEqual(per_file["status"], "loaded")


class TestPhase61RunIngestionCycleAllReal(unittest.TestCase):
    """R-417 — all 5 export types are now real loaders."""

    def _patch_get_connection(self, fake):
        @contextmanager
        def _ctx():
            yield fake
        return mock.patch("research.prepare.get_connection", _ctx)

    def test_mixed_files_dispatched_to_real_loaders(self) -> None:
        with _temp_costar_base() as base:
            _stage_fixture(
                base, "submarket_stats", "submarket_stats_happy.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            _stage_fixture(
                base, "leasing_comps", "leasing_comps_happy.csv",
                dest_name="leasing_comps_202603.csv",
            )
            # 3 submarket_stats submarkets + 3 leasing_comps submarkets;
            # all 6 are unique enough to need separate UPSERTs (different
            # market for atlanta__south_fulton already exists from
            # submarket_stats so leasing reuses it... but our
            # Phase5FakeConnection responds to whatever fetchone is asked
            # since there's no actual DB. We just need 1 cycle-collision
            # response + 6 submarket-name returns (one per UPSERT).
            fake = Phase5FakeConnection(fetchone_queue=[
                (0,),  # cycle-id collision
                # submarket_stats
                ("South Fulton",), ("West Atlanta / I-20",), ("Clayton County",),
                # leasing_comps (same 3 submarkets but each calls _ensure_submarket)
                ("South Fulton",), ("West Atlanta / I-20",), ("Clayton County",),
            ])
            with self._patch_get_connection(fake):
                summary = research.run_ingestion_cycle()
            self.assertEqual(
                summary["per_export_type"]["submarket_stats"]["files_loaded"], 1,
            )
            self.assertEqual(
                summary["per_export_type"]["leasing_comps"]["files_loaded"], 1,
            )
            for empty_type in ("land_sales_comps", "building_sales_comps", "land_listings"):
                self.assertEqual(
                    summary["per_export_type"][empty_type]["files_loaded"], 0,
                )

    def test_all_loaders_report_status_loaded_with_no_files(self) -> None:
        with _temp_costar_base():
            fake = Phase5FakeConnection(fetchone_queue=[(0,)])
            with self._patch_get_connection(fake):
                summary = research.run_ingestion_cycle()
            for export_type in (
                "submarket_stats", "land_sales_comps", "building_sales_comps",
                "leasing_comps", "land_listings",
            ):
                self.assertEqual(
                    summary["per_export_type"][export_type]["status"], "loaded",
                )
                self.assertEqual(
                    summary["per_export_type"][export_type]["files_loaded"], 0,
                )


class TestPhase61SqlConstantsStaticChecks(unittest.TestCase):
    """R-419, R-420 — Phase 6.1 SQL constants and print-forbidden helpers."""

    def test_phase6_1_sql_constants_present_and_parameterized(self) -> None:
        for const in (
            "_SQL_DELETE_LAND_SALES_FOR_REINGEST",
            "_SQL_INSERT_LAND_SALES",
            "_SQL_DELETE_BUILDING_SALES_FOR_REINGEST",
            "_SQL_INSERT_BUILDING_SALES",
            "_SQL_DELETE_LEASING_COMPS_FOR_REINGEST",
            "_SQL_INSERT_LEASING_COMP",
            "_SQL_DELETE_LAND_LISTINGS_FOR_REINGEST",
            "_SQL_INSERT_LAND_LISTING",
        ):
            self.assertTrue(hasattr(research, const), f"missing {const}")
            sql = getattr(research, const)
            self.assertNotIn("{", sql, f"f-string brace in {const}: {sql}")

    def test_no_print_in_phase6_1_helpers(self) -> None:
        tree = ast.parse(RESEARCH_PY_SRC)
        forbidden_names = {
            "_resolve_market_from_county",
            "_validate_headers_against_required",
            "_validate_land_sales_comps_row",
            "_validate_building_sales_comps_row",
            "_validate_leasing_comps_row",
            "_validate_land_listings_row",
            "_load_land_sales_comps_file",
            "_load_building_sales_comps_file",
            "_load_leasing_comps_file",
            "_load_land_listings_file",
            "_load_land_sales_comps",
            "_load_building_sales_comps",
            "_load_leasing_comps",
            "_load_land_listings",
            "_ingest_one_comp_file",
            "_market_resolver_with_county",
            "_market_resolver_default",
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name not in forbidden_names:
                    continue
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Name)
                            and inner.func.id == "print"):
                        self.fail(f"print() in {node.name} at line {inner.lineno}")

    def test_placeholder_helpers_removed(self) -> None:
        # Phase 6.1 deletes the Phase 6 placeholder loader scaffold.
        for name in (
            "_load_placeholder",
            "_load_land_sales_comps_placeholder",
            "_load_building_sales_comps_placeholder",
            "_load_leasing_comps_placeholder",
            "_load_land_listings_placeholder",
        ):
            self.assertFalse(
                hasattr(research, name),
                f"placeholder {name!r} should have been removed in Phase 6.1",
            )

    def test_county_to_market_lookup_constant(self) -> None:
        self.assertTrue(hasattr(research, "_COUNTY_TO_MARKET"))
        self.assertEqual(
            research._DEFAULT_INGESTION_MARKET, "Atlanta",
        )

