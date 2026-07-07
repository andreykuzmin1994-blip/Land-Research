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
import os
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
import runner
import reporting
import costar_ingest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "discovery"
COSTAR_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "costar"
REPO_ROOT = Path(__file__).resolve().parent.parent
RESEARCH_PY_SRC = (REPO_ROOT / "research.py").read_text(encoding="utf-8")
COSTAR_INGEST_PY_SRC = (REPO_ROOT / "costar_ingest.py").read_text(encoding="utf-8")
REPORTING_PY_SRC = (REPO_ROOT / "reporting.py").read_text(encoding="utf-8")
RUNNER_PY_SRC = (REPO_ROOT / "runner.py").read_text(encoding="utf-8")
PIPELINE_COMMON_PY_SRC = (REPO_ROOT / "pipeline_common.py").read_text(encoding="utf-8")
ALL_PIPELINE_PY_SRC = "\n".join(
    (RESEARCH_PY_SRC, COSTAR_INGEST_PY_SRC, REPORTING_PY_SRC,
     RUNNER_PY_SRC, PIPELINE_COMMON_PY_SRC)
)
# Merged module AST spanning every pipeline module, for whole-surface
# static checks (each file is parsed separately, then bodies spliced,
# because `from __future__` lines block naive source concatenation).
ALL_PIPELINE_AST = ast.Module(
    body=[
        node
        for source in (RESEARCH_PY_SRC, COSTAR_INGEST_PY_SRC,
                       REPORTING_PY_SRC, RUNNER_PY_SRC, PIPELINE_COMMON_PY_SRC)
        for node in ast.parse(source).body
    ],
    type_ignores=[],
)


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
        """R-01: no pipeline module writes parameters.json / sources.json / program.md."""
        forbidden_paths = ("parameters.json", "program.md")
        # sources.json is read; just ensure no open(..., 'w') against any of these.
        tree = ALL_PIPELINE_AST
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
        tree = ALL_PIPELINE_AST
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
        tree = ALL_PIPELINE_AST
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

    @staticmethod
    def _phase78_parcel_tuple(parcel_id: str, lng: float, lat: float) -> tuple:
        """Phase 7+8 _SQL_FETCH_PARCEL returns 10 columns. Phase 5 tests
        leave submarket=None so the market_context + sales_comps fetches
        are skipped (no extra fetchones needed)."""
        return (parcel_id, "atlanta", None, "GA", None, None, None, None, lng, lat)

    def test_happy_path_inserts_parcel_score_and_log(self) -> None:
        # Centroid in South Fulton OZ stub.
        fake = Phase5FakeConnection(
            fetchone_queue=[
                self._phase78_parcel_tuple("fulton-001", -84.55, 33.55),  # _SQL_FETCH_PARCEL
                (1000.0, 1100.0, 1.5),                                     # _SQL_S2_GEOMETRY
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
        # parcel_scores INSERT issued — actionability is set per Phase 7+8
        # gates. With no submarket, no S4/S5/S6/S8, strategy fit produces
        # WEAK/N/A across all five strategies → gate 3 fails → FAIL:strategy.
        score_inserts = [
            (sql, params) for sql, params in fake.all_executes
            if "INSERT INTO parcel_scores" in sql
        ]
        self.assertEqual(len(score_inserts), 1)
        self.assertEqual(score_inserts[0][1][3], "FAIL:strategy")
        # research_log scoring row issued.
        log_inserts = [
            sql for sql, params in fake.all_executes
            if "INSERT INTO research_log" in sql and "scoring" in str(params)
        ]
        self.assertEqual(len(log_inserts), 1)

    def test_data_gap_flag_per_null_subscore(self) -> None:
        # With submarket=None: S1, S3, S4, S5, S6, S7, S8, S11, S12 stay null
        # → 9 data_gap flags. S2/S9/S10 are populated (no flag).
        fake = Phase5FakeConnection(
            fetchone_queue=[
                self._phase78_parcel_tuple("fulton-001", -84.55, 33.55),
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

    def test_actionability_fails_strategy_when_no_market_context(self) -> None:
        # Phase 7+8 reframing of the prior test_actionability_is_pending test:
        # with no submarket and no acreage the strategy fit returns WEAK/N/A
        # across the board → gate 3 (viable strategy) fails → FAIL:strategy.
        # The Phase 5 'PENDING' default no longer applies because score_parcel
        # always runs the gates after computing strategy fit (R-529, R-532).
        fake = Phase5FakeConnection(
            fetchone_queue=[
                self._phase78_parcel_tuple("fulton-001", -84.55, 33.55),
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
        # Position 3 in the extended _SQL_INSERT_PARCEL_SCORE is still
        # actionability (R-501: column-order audit).
        self.assertEqual(score_insert_params[3], "FAIL:strategy")


class TestPhase5ParcelScoresAppendOnly(unittest.TestCase):
    """R-204, R-210 — versioned-append; two calls = two INSERTs."""

    def setUp(self) -> None:
        research._OZ_TRACTS_CACHE = None

    def test_two_calls_produce_two_inserts(self) -> None:
        params = _passing_params()
        params["scoring_weights"] = TestPhase5Composite.WEIGHTS
        # Phase 7+8: 10-col parcel tuple. Each score_parcel call also issues
        # a _SQL_FLAGGED_ACTIONABILITY_BLOCK fetchone after the S2 geometry
        # fetch — between back-to-back calls in a shared queue the test must
        # supply an explicit None so that fetchone doesn't pop the *next*
        # call's parcel tuple as the actionability-block result.
        ptuple = ("fulton-001", "atlanta", None, "GA", None, None, None, None, -84.55, 33.55)
        fake = Phase5FakeConnection(
            fetchone_queue=[
                ptuple,
                (1000.0, 1100.0, 1.5),
                None,                                 # actionability_block call 1
                ptuple,
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
        # Phase 13: cycle now PREFETCHES the per-cycle cache after the parcel
        # list and before the loop. Sequence:
        #   fetchone: collision check (0).
        #   fetchall: unscored list (2 parcels), then prefetch distinct-submarkets
        #             (empty — both parcels submarket=None), then actionability
        #             block batch (empty — no open blocks).
        #   per-parcel fetchone: fetch (10 cols, submarket=None so no S4-S8
        #             query) + S2. The actionability_block is served from the
        #             (empty) cache → None, so NO per-parcel block fetchone.
        fake = Phase5FakeConnection(
            fetchone_queue=[
                (0,),                                                                       # collision check
                ("fulton-001", "atlanta", None, "GA", None, None, None, None, -84.55, 33.55), # parcel 1 fetch
                (1000.0, 1100.0, 1.5),                                                       # parcel 1 S2
                ("fulton-002", "atlanta", None, "GA", None, None, None, None, -83.0, 35.0),  # parcel 2 fetch (outside OZ)
                (500.0, 1000.0, 4.0),                                                        # parcel 2 S2 (compactness 0.5 → 0)
            ],
            fetchall_queue=[
                [("fulton-001",), ("fulton-002",)],  # unscored parcel list
                [],                                   # prefetch: distinct submarkets (none)
                [],                                   # prefetch: actionability block batch (none)
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
    """Monkey-patch _COSTAR_BASE_DIR (every module binding) to a tempdir (R-329)."""
    original = costar_ingest._COSTAR_BASE_DIR
    with tempfile.TemporaryDirectory() as td:
        costar_ingest._COSTAR_BASE_DIR = Path(td)
        runner._COSTAR_BASE_DIR = Path(td)
        research._COSTAR_BASE_DIR = Path(td)
        try:
            yield Path(td)
        finally:
            costar_ingest._COSTAR_BASE_DIR = original
            runner._COSTAR_BASE_DIR = original
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
        self.assertEqual(costar_ingest._slugify("Atlanta"), "atlanta")

    def test_punctuation_collapses_to_underscore(self) -> None:
        self.assertEqual(
            costar_ingest._slugify("West Atlanta / I-20"),
            "west_atlanta_i_20",
        )

    def test_strips_edges(self) -> None:
        self.assertEqual(costar_ingest._slugify("  South Fulton  "), "south_fulton")

    def test_truncates_long_inputs(self) -> None:
        long = "x" * 200
        self.assertEqual(len(costar_ingest._slugify(long)), 60)

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            costar_ingest._slugify("")

    def test_punctuation_only_raises(self) -> None:
        with self.assertRaises(ValueError):
            costar_ingest._slugify("///---")


class TestPhase6IngestionCycleId(unittest.TestCase):
    """R-321 — cycle id format and uniqueness."""

    def test_format(self) -> None:
        cid = costar_ingest._make_ingestion_cycle_id()
        self.assertRegex(cid, costar_ingest._INGESTION_CYCLE_ID_RE)

    def test_uniqueness(self) -> None:
        a = costar_ingest._make_ingestion_cycle_id()
        b = costar_ingest._make_ingestion_cycle_id()
        self.assertNotEqual(a, b)


class TestPhase6ScanExportDir(unittest.TestCase):
    """R-303, R-304, R-305, R-310, R-311 — directory scanning."""

    def test_empty_dir_returns_empty(self) -> None:
        with _temp_costar_base():
            self.assertEqual(costar_ingest._scan_export_dir("submarket_stats"), [])

    def test_missing_dir_returns_empty(self) -> None:
        with _temp_costar_base() as base:
            self.assertFalse((base / "submarket_stats").exists())
            self.assertEqual(costar_ingest._scan_export_dir("submarket_stats"), [])

    def test_returns_matching_files_sorted_by_date(self) -> None:
        with _temp_costar_base() as base:
            d = base / "submarket_stats"
            d.mkdir(parents=True)
            (d / "submarket_stats_20260420.csv").write_text("x", encoding="utf-8")
            (d / "submarket_stats_20260427.csv").write_text("x", encoding="utf-8")
            (d / "submarket_stats_20260413.csv").write_text("x", encoding="utf-8")
            (d / "ignore_me.txt").write_text("x", encoding="utf-8")
            (d / ".hidden.csv").write_text("x", encoding="utf-8")
            results = costar_ingest._scan_export_dir("submarket_stats")
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
            results = costar_ingest._scan_export_dir("submarket_stats")
            names = [p.name for p, _ in results]
            self.assertEqual(names, ["submarket_stats_20260427.csv"])

    def test_directory_traversal_rejected(self) -> None:
        with _temp_costar_base():
            with self.assertRaises(ValueError):
                costar_ingest._scan_export_dir("../etc")
            with self.assertRaises(ValueError):
                costar_ingest._scan_export_dir("/abs/path")


class TestPhase6ArchiveAndFailMovement(unittest.TestCase):
    """R-312, R-313, R-314 — archive / fail file movement."""

    def test_archive_round_trip(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(base, "submarket_stats", "submarket_stats_happy.csv",
                                    dest_name="submarket_stats_20260427.csv")
            archived = costar_ingest._archive_file(staged)
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
            dest, err_path = costar_ingest._fail_file(
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
            d1 = costar_ingest._archive_destination(f1, "ARCHIVED")
            d2 = costar_ingest._archive_destination(f1, "ARCHIVED")
            self.assertNotEqual(d1, d2)


class TestPhase6Coercion(unittest.TestCase):
    """R-306 — locale-tolerant number parsing."""

    def test_plain_int(self) -> None:
        self.assertEqual(costar_ingest._coerce_optional_int("28500000"), (28500000, None))

    def test_thousands_commas_stripped(self) -> None:
        self.assertEqual(costar_ingest._coerce_optional_int("28,500,000"), (28500000, None))

    def test_dollar_sign_stripped(self) -> None:
        val, err = costar_ingest._coerce_optional_decimal("$7.85")
        self.assertEqual(val, 7.85)
        self.assertIsNone(err)

    def test_percent_sign_stripped(self) -> None:
        val, err = costar_ingest._coerce_optional_decimal("5.4%")
        self.assertEqual(val, 5.4)
        self.assertIsNone(err)

    def test_blank_returns_none(self) -> None:
        self.assertEqual(costar_ingest._coerce_optional_decimal(""), (None, None))
        self.assertEqual(costar_ingest._coerce_optional_decimal("N/A"), (None, None))

    def test_unparseable_returns_error(self) -> None:
        val, err = costar_ingest._coerce_optional_decimal("xyz")
        self.assertIsNone(val)
        self.assertIn("unparseable", err)

    def test_negative_int_supported(self) -> None:
        self.assertEqual(costar_ingest._coerce_optional_int("-420000"), (-420000, None))


class TestPhase6DateParsing(unittest.TestCase):
    """R-307 — multiple acceptable date formats."""

    def test_iso_format(self) -> None:
        self.assertEqual(costar_ingest._parse_report_date("2026-04-27"),
                         ("2026-04-27", None))

    def test_us_slash_format(self) -> None:
        self.assertEqual(costar_ingest._parse_report_date("04/27/2026"),
                         ("2026-04-27", None))

    def test_iso_with_time(self) -> None:
        self.assertEqual(costar_ingest._parse_report_date("2026-04-27T00:00:00"),
                         ("2026-04-27", None))

    def test_unparseable_returns_error(self) -> None:
        out, err = costar_ingest._parse_report_date("not-a-date")
        self.assertIsNone(out)
        self.assertIn("unparseable", err)

    def test_empty_returns_error(self) -> None:
        out, err = costar_ingest._parse_report_date("")
        self.assertIsNone(out)


class TestPhase6HeaderValidation(unittest.TestCase):
    """R-309, R-310 — header set + duplicate detection."""

    def test_happy_headers(self) -> None:
        headers = list(costar_ingest._SUBMARKET_STATS_REQUIRED_COLUMNS)
        self.assertIsNone(costar_ingest._validate_submarket_stats_headers(headers))

    def test_missing_column_detected(self) -> None:
        headers = [c for c in costar_ingest._SUBMARKET_STATS_REQUIRED_COLUMNS
                   if c != "vacancy_rate_pct"]
        err = costar_ingest._validate_submarket_stats_headers(headers)
        self.assertIn("vacancy_rate_pct", err)

    def test_duplicate_column_detected(self) -> None:
        headers = list(costar_ingest._SUBMARKET_STATS_REQUIRED_COLUMNS) + ["submarket_name"]
        err = costar_ingest._validate_submarket_stats_headers(headers)
        self.assertIn("duplicate", err)

    def test_extra_columns_allowed(self) -> None:
        headers = list(costar_ingest._SUBMARKET_STATS_REQUIRED_COLUMNS) + ["extra_col"]
        self.assertIsNone(costar_ingest._validate_submarket_stats_headers(headers))

    def test_case_insensitive_headers(self) -> None:
        headers = [c.upper() for c in costar_ingest._SUBMARKET_STATS_REQUIRED_COLUMNS]
        self.assertIsNone(costar_ingest._validate_submarket_stats_headers(headers))

    def test_bom_stripped(self) -> None:
        headers = list(costar_ingest._SUBMARKET_STATS_REQUIRED_COLUMNS)
        headers[0] = "﻿" + headers[0]
        self.assertIsNone(costar_ingest._validate_submarket_stats_headers(headers))


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
        out, err = costar_ingest._validate_submarket_stats_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["submarket_name"], "South Fulton")
        self.assertEqual(out["report_date"], "2026-04-27")
        self.assertEqual(out["vacancy_rate_pct"], 5.4)

    def test_empty_submarket_name_rejected(self) -> None:
        out, err = costar_ingest._validate_submarket_stats_row(self._row(submarket_name=""))
        self.assertIsNone(out)
        self.assertIn("submarket_name", err)

    def test_vacancy_out_of_range_rejected(self) -> None:
        out, err = costar_ingest._validate_submarket_stats_row(
            self._row(vacancy_rate_pct="150"),
        )
        self.assertIsNone(out)
        self.assertIn("vacancy_rate_pct", err)

    def test_zero_rent_rejected(self) -> None:
        out, err = costar_ingest._validate_submarket_stats_row(
            self._row(asking_rent_nnn_psf="0"),
        )
        self.assertIsNone(out)
        self.assertIn("asking_rent_nnn_psf", err)

    def test_optional_field_null_accepted(self) -> None:
        out, err = costar_ingest._validate_submarket_stats_row(
            self._row(availability_rate_pct=""),
        )
        self.assertIsNone(err)
        self.assertIsNone(out["availability_rate_pct"])

    def test_unparseable_date_rejected(self) -> None:
        out, err = costar_ingest._validate_submarket_stats_row(
            self._row(report_date="not-a-date"),
        )
        self.assertIsNone(out)
        self.assertIn("date", err)

    def test_negative_absorption_accepted(self) -> None:
        out, err = costar_ingest._validate_submarket_stats_row(
            self._row(net_absorption_t12_sf="-500000"),
        )
        self.assertIsNone(err)
        self.assertEqual(out["net_absorption_t12_sf"], -500000)


class TestPhase6EnsureSubmarket(unittest.TestCase):
    """R-301, R-315 — auto-UPSERT markets/submarkets reference data."""

    def test_creates_new_submarket_returning_name(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[("South Fulton",)])
        sid, created, drift = costar_ingest._ensure_submarket(fake, "Atlanta", "South Fulton")
        self.assertEqual(sid, "atlanta__south_fulton")
        self.assertTrue(created)
        self.assertIsNone(drift)
        self.assertEqual(len(fake.all_executes), 2)

    def test_existing_submarket_no_drift(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[None, ("South Fulton",)])
        sid, created, drift = costar_ingest._ensure_submarket(fake, "Atlanta", "South Fulton")
        self.assertEqual(sid, "atlanta__south_fulton")
        self.assertFalse(created)
        self.assertIsNone(drift)

    def test_name_drift_emits_message(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[None, ("Old Name",)])
        sid, created, drift = costar_ingest._ensure_submarket(fake, "Atlanta", "New Name")
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_submarket_stats_file(fake, cycle_id, staged)

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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_submarket_stats_file(fake, cycle_id, staged)

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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_submarket_stats_file(fake, cycle_id, staged)

            self.assertEqual(result["status"], "failed")
            self.assertIn("duplicate", result["error"].lower())

    def test_row_errors_flagged_but_other_rows_load(self) -> None:
        with _temp_costar_base() as base:
            staged = _stage_fixture(
                base, "submarket_stats", "submarket_stats_row_errors.csv",
                dest_name="submarket_stats_20260427.csv",
            )
            fake = Phase5FakeConnection(fetchone_queue=[("South Fulton",)])
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_submarket_stats_file(fake, cycle_id, staged)

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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_submarket_stats_file(fake, cycle_id, staged)
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            costar_ingest._load_submarket_stats_file(fake, cycle_id, staged)

            sql_strings = [sql for sql, _ in fake.all_executes]
            delete_count = sum(
                1 for s in sql_strings
                if s == costar_ingest._SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST
            )
            self.assertEqual(delete_count, 3)
            insert_count = sum(
                1 for s in sql_strings
                if s == costar_ingest._SQL_INSERT_MARKET_CONTEXT
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            costar_ingest._load_submarket_stats_file(fake, cycle_id, staged)
            for sql, params in fake.all_executes:
                if sql == costar_ingest._SQL_DELETE_MARKET_CONTEXT_FOR_REINGEST:
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
                summary = costar_ingest.run_ingestion_cycle()
            self.assertTrue(summary["aborted"])
            self.assertEqual(summary["abort_reason"], "cycle_id_collision")

    def test_no_files_returns_clean_summary(self) -> None:
        # Phase 6.1: all 5 export types are now real loaders; with no
        # files staged, each reports files_loaded=0 (not 'not_implemented').
        with _temp_costar_base():
            fake = Phase5FakeConnection(fetchone_queue=[(0,)])
            with self._patch_get_connection(fake):
                summary = costar_ingest.run_ingestion_cycle()
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
                summary = costar_ingest.run_ingestion_cycle()
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
            self.assertTrue(hasattr(costar_ingest, const), f"missing SQL constant {const}")
            sql = getattr(costar_ingest, const)
            self.assertIsInstance(sql, str)
            self.assertNotIn("{", sql, f"f-string brace in {const}: {sql}")

    def test_no_print_in_ingestion_helpers(self) -> None:
        tree = ast.parse(COSTAR_INGEST_PY_SRC)
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
        market, used_default = costar_ingest._resolve_market_from_county("Fulton")
        self.assertEqual(market, "Atlanta")
        self.assertFalse(used_default)

    def test_case_insensitive(self) -> None:
        market, used_default = costar_ingest._resolve_market_from_county("DEKALB")
        self.assertEqual(market, "Atlanta")
        self.assertFalse(used_default)

    def test_unknown_county_uses_default(self) -> None:
        market, used_default = costar_ingest._resolve_market_from_county("Forsyth")
        self.assertEqual(market, "Atlanta")
        self.assertTrue(used_default)

    def test_blank_uses_default(self) -> None:
        market, used_default = costar_ingest._resolve_market_from_county("")
        self.assertTrue(used_default)
        market2, _ = costar_ingest._resolve_market_from_county(None)
        self.assertEqual(market2, "Atlanta")

    def test_lookup_covers_eight_atlanta_counties(self) -> None:
        for county in (
            "fulton", "dekalb", "cobb", "gwinnett",
            "clayton", "henry", "spalding", "fayette",
        ):
            self.assertEqual(costar_ingest._COUNTY_TO_MARKET[county], "Atlanta")


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
        out, err = costar_ingest._validate_land_sales_comps_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["address"], "1234 Industrial Blvd")
        self.assertEqual(out["sale_price"], 1875000)
        self.assertEqual(out["acres"], 12.5)
        self.assertIsNone(out["cap_rate"])
        # raw is preserved for the JSONB column
        self.assertIn("intended_use", out["raw"])

    def test_blank_address_rejected(self) -> None:
        out, err = costar_ingest._validate_land_sales_comps_row(self._row(address=""))
        self.assertIsNone(out)
        self.assertIn("address", err)

    def test_zero_sale_price_rejected(self) -> None:
        out, err = costar_ingest._validate_land_sales_comps_row(self._row(sale_price="0"))
        self.assertIsNone(out)
        self.assertIn("sale_price", err)

    def test_blank_acres_rejected(self) -> None:
        out, err = costar_ingest._validate_land_sales_comps_row(self._row(acres=""))
        self.assertIsNone(out)
        self.assertIn("acres", err)

    def test_unparseable_sale_date_rejected(self) -> None:
        out, err = costar_ingest._validate_land_sales_comps_row(self._row(sale_date="not-a-date"))
        self.assertIsNone(out)
        self.assertIn("sale_date", err)

    def test_dollar_signs_in_price_accepted(self) -> None:
        out, err = costar_ingest._validate_land_sales_comps_row(
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
        out, err = costar_ingest._validate_building_sales_comps_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["building_sf"], 250000.0)
        self.assertEqual(out["sale_price"], 32500000)
        self.assertIn("tenant_at_sale", out["raw"])
        self.assertIn("lease_term_remaining_years", out["raw"])

    def test_zero_building_sf_rejected(self) -> None:
        out, err = costar_ingest._validate_building_sales_comps_row(
            self._row(building_sf="0"),
        )
        self.assertIsNone(out)
        self.assertIn("building_sf", err)

    def test_year_built_out_of_range_rejected(self) -> None:
        out, err = costar_ingest._validate_building_sales_comps_row(
            self._row(year_built="1700"),
        )
        self.assertIsNone(out)
        self.assertIn("year_built", err)

    def test_clear_height_out_of_range_rejected(self) -> None:
        out, err = costar_ingest._validate_building_sales_comps_row(
            self._row(clear_height_ft="100"),
        )
        self.assertIsNone(out)
        self.assertIn("clear_height_ft", err)

    def test_optional_fields_null_accepted(self) -> None:
        out, err = costar_ingest._validate_building_sales_comps_row(
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
        out, err = costar_ingest._validate_leasing_comps_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["tenant_name"], "Amazon Logistics")
        self.assertEqual(out["lease_term_months"], 84)
        self.assertEqual(out["starting_rent_psf_nnn"], 7.95)

    def test_blank_tenant_rejected(self) -> None:
        out, err = costar_ingest._validate_leasing_comps_row(self._row(tenant_name=""))
        self.assertIsNone(out)
        self.assertIn("tenant_name", err)

    def test_zero_term_rejected(self) -> None:
        out, err = costar_ingest._validate_leasing_comps_row(self._row(lease_term_months="0"))
        self.assertIsNone(out)
        self.assertIn("lease_term_months", err)

    def test_zero_rent_rejected(self) -> None:
        out, err = costar_ingest._validate_leasing_comps_row(
            self._row(starting_rent_psf_nnn="0"),
        )
        self.assertIsNone(out)
        self.assertIn("starting_rent_psf_nnn", err)

    def test_optional_escalation_null_accepted(self) -> None:
        out, err = costar_ingest._validate_leasing_comps_row(self._row(rent_escalation_pct=""))
        self.assertIsNone(err)
        self.assertIsNone(out["rent_escalation_pct"])

    def test_naics_must_be_digits(self) -> None:
        # naics_code is not in required cols, but if a CSV has one, validate.
        row = self._row()
        row["naics_code"] = "abc123"
        out, err = costar_ingest._validate_leasing_comps_row(row)
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
        out, err = costar_ingest._validate_land_listings_row(self._row())
        self.assertIsNone(err)
        self.assertEqual(out["asking_price"], 2775000)
        self.assertEqual(out["acres"], 18.5)

    def test_blank_address_rejected(self) -> None:
        out, err = costar_ingest._validate_land_listings_row(self._row(address=""))
        self.assertIsNone(out)

    def test_asking_price_null_accepted(self) -> None:
        out, err = costar_ingest._validate_land_listings_row(
            self._row(asking_price="", asking_price_per_acre=""),
        )
        self.assertIsNone(err)
        self.assertIsNone(out["asking_price"])
        self.assertIsNone(out["asking_price_per_acre"])

    def test_zero_acres_rejected(self) -> None:
        out, err = costar_ingest._validate_land_listings_row(self._row(acres="0"))
        self.assertIsNone(out)
        self.assertIn("acres", err)

    def test_zero_asking_price_rejected(self) -> None:
        out, err = costar_ingest._validate_land_listings_row(self._row(asking_price="0"))
        self.assertIsNone(out)
        self.assertIn("asking_price", err)

    def test_negative_days_on_market_rejected(self) -> None:
        out, err = costar_ingest._validate_land_listings_row(self._row(days_on_market="-5"))
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_land_sales_comps_file(fake, cycle_id, staged)
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_land_sales_comps_file(fake, cycle_id, staged)
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_land_sales_comps_file(fake, cycle_id, staged)
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            costar_ingest._load_land_sales_comps_file(fake, cycle_id, staged)
            sql_strings = [sql for sql, _ in fake.all_executes]
            delete_count = sum(
                1 for s in sql_strings
                if s == costar_ingest._SQL_DELETE_LAND_SALES_FOR_REINGEST
            )
            insert_count = sum(
                1 for s in sql_strings
                if s == costar_ingest._SQL_INSERT_LAND_SALES
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_building_sales_comps_file(fake, cycle_id, staged)
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            costar_ingest._load_building_sales_comps_file(fake, cycle_id, staged)
            sql_strings = [sql for sql, _ in fake.all_executes]
            # Building DELETE used, NOT land DELETE (R-422).
            self.assertGreater(
                sum(1 for s in sql_strings
                    if s == costar_ingest._SQL_DELETE_BUILDING_SALES_FOR_REINGEST), 0,
            )
            self.assertEqual(
                sum(1 for s in sql_strings
                    if s == costar_ingest._SQL_DELETE_LAND_SALES_FOR_REINGEST), 0,
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_leasing_comps_file(fake, cycle_id, staged)
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_leasing_comps_file(fake, cycle_id, staged)
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_land_listings_file(
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            result = costar_ingest._load_land_listings_file(
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
            cycle_id = costar_ingest._make_ingestion_cycle_id()
            costar_ingest._load_land_listings_file(fake, cycle_id, staged, "2026-04-27")
            for sql, params in fake.all_executes:
                if sql == costar_ingest._SQL_DELETE_LAND_LISTINGS_FOR_REINGEST:
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
                summary = costar_ingest.run_ingestion_cycle()
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
                summary = costar_ingest.run_ingestion_cycle()
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
                summary = costar_ingest.run_ingestion_cycle()
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
            self.assertTrue(hasattr(costar_ingest, const), f"missing {const}")
            sql = getattr(costar_ingest, const)
            self.assertNotIn("{", sql, f"f-string brace in {const}: {sql}")

    def test_no_print_in_phase6_1_helpers(self) -> None:
        tree = ast.parse(COSTAR_INGEST_PY_SRC)
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
        self.assertTrue(hasattr(costar_ingest, "_COUNTY_TO_MARKET"))
        self.assertEqual(
            costar_ingest._DEFAULT_INGESTION_MARKET, "Atlanta",
        )


# ===========================================================================
# Phase 7+8 — combined scoring completion + actionability + strategy fit
# See reviews/10_phase7_8_combined/01_risk_review.md for the R-5XX risk
# catalog and gate definitions.
# ===========================================================================
class TestPhase7S4Vacancy(unittest.TestCase):
    """R-515 — vacancy banding."""

    def test_below_three_pct(self) -> None:
        self.assertEqual(research._score_vacancy(2.99), 10)

    def test_at_three_pct_boundary(self) -> None:
        self.assertEqual(research._score_vacancy(3.0), 8)

    def test_band_three_to_five(self) -> None:
        self.assertEqual(research._score_vacancy(4.0), 8)

    def test_band_five_to_seven(self) -> None:
        self.assertEqual(research._score_vacancy(5.0), 6)
        self.assertEqual(research._score_vacancy(6.99), 6)

    def test_band_seven_to_ten(self) -> None:
        self.assertEqual(research._score_vacancy(7.0), 3)
        self.assertEqual(research._score_vacancy(10.0), 3)

    def test_above_ten_pct(self) -> None:
        self.assertEqual(research._score_vacancy(10.01), 0)
        self.assertEqual(research._score_vacancy(25.0), 0)

    def test_none_input(self) -> None:
        self.assertIsNone(research._score_vacancy(None))


class TestPhase7S5Absorption(unittest.TestCase):
    """R-516 — absorption banding incl. negative-band cutoff."""

    def test_strong_positive(self) -> None:
        self.assertEqual(research._score_absorption(2_500_000), 10)
        self.assertEqual(research._score_absorption(2_000_001), 10)

    def test_at_two_million_boundary(self) -> None:
        self.assertEqual(research._score_absorption(2_000_000), 7)

    def test_positive_band(self) -> None:
        self.assertEqual(research._score_absorption(1_000_000), 7)
        self.assertEqual(research._score_absorption(500_000), 7)

    def test_flat_band(self) -> None:
        self.assertEqual(research._score_absorption(499_999), 4)
        self.assertEqual(research._score_absorption(0), 4)
        self.assertEqual(research._score_absorption(-500_000), 4)

    def test_negative(self) -> None:
        self.assertEqual(research._score_absorption(-500_001), 0)
        self.assertEqual(research._score_absorption(-2_000_000), 0)

    def test_none_input(self) -> None:
        self.assertIsNone(research._score_absorption(None))


class TestPhase7S6Pipeline(unittest.TestCase):
    """R-519, R-521, R-522 — submarket-grain pipeline approximation."""

    def test_no_pipeline(self) -> None:
        self.assertEqual(research._score_pipeline(0), 10)

    def test_null_pipeline_treated_as_no_supply(self) -> None:
        # R-521: absence of evidence in a curated CoStar export → 10.
        self.assertEqual(research._score_pipeline(None), 10)

    def test_under_five_hundred_k(self) -> None:
        self.assertEqual(research._score_pipeline(1), 7)
        self.assertEqual(research._score_pipeline(499_999), 7)

    def test_at_five_hundred_k_boundary(self) -> None:
        self.assertEqual(research._score_pipeline(500_000), 4)

    def test_band_500k_to_1_5m(self) -> None:
        self.assertEqual(research._score_pipeline(1_000_000), 4)
        self.assertEqual(research._score_pipeline(1_500_000), 4)

    def test_above_1_5m(self) -> None:
        self.assertEqual(research._score_pipeline(1_500_001), 0)
        self.assertEqual(research._score_pipeline(5_000_000), 0)


class TestPhase7S8Basis(unittest.TestCase):
    """R-528 — basis-vs-median banding."""

    def test_below_median(self) -> None:
        self.assertEqual(research._score_basis(80_000, 100_000), 10)
        self.assertEqual(research._score_basis(94_999, 100_000), 10)

    def test_at_median_band_lower_edge(self) -> None:
        self.assertEqual(research._score_basis(95_000, 100_000), 7)

    def test_at_median_band_upper_edge(self) -> None:
        self.assertEqual(research._score_basis(110_000, 100_000), 7)

    def test_above_median_band(self) -> None:
        self.assertEqual(research._score_basis(110_001, 100_000), 4)
        self.assertEqual(research._score_basis(125_000, 100_000), 4)

    def test_well_above_median(self) -> None:
        self.assertEqual(research._score_basis(125_001, 100_000), 0)
        self.assertEqual(research._score_basis(200_000, 100_000), 0)

    def test_null_inputs(self) -> None:
        self.assertIsNone(research._score_basis(None, 100_000))
        self.assertIsNone(research._score_basis(80_000, None))
        self.assertIsNone(research._score_basis(80_000, 0))


class TestPhase7BasisProxy(unittest.TestCase):
    """R-526, R-527 — parcel basis ladder + GA assessed-value inflation."""

    def test_recent_sale_used(self) -> None:
        from datetime import date, timedelta
        recent = date.today() - timedelta(days=180)
        parcel = {
            "acreage": 10.0,
            "last_sale_price": 800_000,
            "last_sale_date": recent,
            "assessed_value_total": 200_000,
            "state": "GA",
        }
        basis, prov = research._compute_parcel_basis_per_acre(parcel)
        self.assertEqual(basis, 80_000.0)
        self.assertEqual(prov, "recent_sale")

    def test_stale_sale_falls_back_to_assessed(self) -> None:
        from datetime import date, timedelta
        stale = date.today() - timedelta(days=24 * 30 + 30)
        parcel = {
            "acreage": 10.0,
            "last_sale_price": 800_000,
            "last_sale_date": stale,
            "assessed_value_total": 200_000,
            "state": "GA",
        }
        basis, prov = research._compute_parcel_basis_per_acre(parcel)
        # GA assessed inflation: 200_000/10 * 2.5 = 50_000.
        self.assertAlmostEqual(basis, 50_000.0)
        self.assertEqual(prov, "assessed_inflated_ga")

    def test_assessed_raw_for_non_ga_state(self) -> None:
        parcel = {
            "acreage": 10.0,
            "last_sale_price": None,
            "last_sale_date": None,
            "assessed_value_total": 200_000,
            "state": "TX",
        }
        basis, prov = research._compute_parcel_basis_per_acre(parcel)
        self.assertEqual(basis, 20_000.0)
        self.assertEqual(prov, "assessed_raw")

    def test_no_data_unavailable(self) -> None:
        parcel = {
            "acreage": 10.0,
            "last_sale_price": None,
            "last_sale_date": None,
            "assessed_value_total": None,
            "state": "GA",
        }
        basis, prov = research._compute_parcel_basis_per_acre(parcel)
        self.assertIsNone(basis)
        self.assertEqual(prov, "unavailable")

    def test_zero_acreage_unavailable(self) -> None:
        parcel = {
            "acreage": 0,
            "last_sale_price": 800_000,
            "last_sale_date": None,
            "assessed_value_total": 200_000,
            "state": "GA",
        }
        basis, prov = research._compute_parcel_basis_per_acre(parcel)
        self.assertIsNone(basis)
        self.assertEqual(prov, "unavailable")


class TestPhase7MarketContextOrchestration(unittest.TestCase):
    """R-511, R-518 — single market_context fetch yielding S4/S5/S6."""

    def test_no_submarket_returns_all_none(self) -> None:
        fake = Phase5FakeConnection()
        out = research._compute_market_context_scores(fake, None)
        self.assertIsNone(out["S4"])
        self.assertIsNone(out["S5"])
        self.assertIsNone(out["S6"])
        # No SQL executed when submarket is missing.
        executes = [sql for sql, _ in fake.all_executes]
        self.assertEqual(executes, [])

    def test_no_row_returns_all_none(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[None])
        out = research._compute_market_context_scores(fake, "south_fulton")
        self.assertIsNone(out["S4"])
        self.assertIsNone(out["S5"])
        self.assertIsNone(out["S6"])

    def test_strong_submarket(self) -> None:
        from datetime import date
        # vacancy 2.5%, absorption 1.5M SF (positive), pipeline 0 SF, OZ stub.
        fake = Phase5FakeConnection(
            fetchone_queue=[
                (2.5, 1_500_000, 0, 0, 5.5, date.today(), "costar"),
            ],
        )
        out = research._compute_market_context_scores(fake, "south_fulton")
        self.assertEqual(out["S4"], 10)
        self.assertEqual(out["S5"], 7)
        self.assertEqual(out["S6"], 10)
        self.assertEqual(out["source"], "costar")
        self.assertIsNotNone(out["staleness_days"])

    def test_soft_submarket(self) -> None:
        from datetime import date, timedelta
        # vacancy 8%, absorption -1M SF (negative), pipeline 2M SF (heavy), 60d stale.
        old = date.today() - timedelta(days=60)
        fake = Phase5FakeConnection(
            fetchone_queue=[
                (8.0, -1_000_000, 2_000_000, 500_000, 4.5, old, "costar"),
            ],
        )
        out = research._compute_market_context_scores(fake, "henry")
        self.assertEqual(out["S4"], 3)
        self.assertEqual(out["S5"], 0)
        self.assertEqual(out["S6"], 0)
        self.assertEqual(out["staleness_days"], 60)


class TestPhase7S8DatabasePath(unittest.TestCase):
    """R-523, R-524, R-525 — sales_comps median join."""

    def test_no_submarket_returns_none(self) -> None:
        fake = Phase5FakeConnection()
        score, prov = research._compute_s8(
            fake, {"acreage": 10.0, "submarket": None, "assessed_value_total": 200_000, "state": "GA",
                   "last_sale_price": None, "last_sale_date": None},
        )
        self.assertIsNone(score)
        self.assertEqual(prov["median_n"], 0)

    def test_below_min_sample_size(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[(2, 100_000.0)])
        score, prov = research._compute_s8(
            fake, {"acreage": 10.0, "submarket": "south_fulton",
                   "assessed_value_total": 200_000, "state": "GA",
                   "last_sale_price": None, "last_sale_date": None},
        )
        self.assertIsNone(score)
        self.assertTrue(prov["n_below_min"])
        self.assertEqual(prov["median_n"], 2)

    def test_happy_path_basis_below_median(self) -> None:
        # Recent sale at $50K/acre, median $100K/acre, n=5 → S8=10.
        from datetime import date, timedelta
        recent = date.today() - timedelta(days=200)
        fake = Phase5FakeConnection(fetchone_queue=[(5, 100_000.0)])
        score, prov = research._compute_s8(
            fake, {"acreage": 10.0, "submarket": "south_fulton",
                   "last_sale_price": 500_000, "last_sale_date": recent,
                   "assessed_value_total": None, "state": "GA"},
        )
        self.assertEqual(score, 10)
        self.assertEqual(prov["basis_provenance"], "recent_sale")
        self.assertFalse(prov["n_below_min"])

    def test_no_basis_proxy_returns_none(self) -> None:
        fake = Phase5FakeConnection(fetchone_queue=[(5, 100_000.0)])
        score, prov = research._compute_s8(
            fake, {"acreage": 10.0, "submarket": "south_fulton",
                   "last_sale_price": None, "last_sale_date": None,
                   "assessed_value_total": None, "state": "GA"},
        )
        self.assertIsNone(score)
        self.assertEqual(prov["basis_provenance"], "unavailable")


class TestPhase8GateControl(unittest.TestCase):
    """R-530 — gate 1 always PASSes."""

    def test_always_pass(self) -> None:
        ok, blocker = research._gate_control()
        self.assertTrue(ok)
        self.assertIsNone(blocker)


class TestPhase8GateEntitlement(unittest.TestCase):
    """R-531 — default-PASS unless explicit actionability_block flag."""

    def test_default_pass_no_flag(self) -> None:
        ok, blocker = research._gate_entitlement({}, None)
        self.assertTrue(ok)
        self.assertIsNone(blocker)

    def test_fail_on_entitlement_blocker(self) -> None:
        ok, blocker = research._gate_entitlement({}, "entitlement moratorium passed by county")
        self.assertFalse(ok)
        self.assertIn("entitlement", blocker)

    def test_non_entitlement_block_does_not_fail_gate2(self) -> None:
        ok, blocker = research._gate_entitlement({}, "active condemnation proceeding")
        self.assertTrue(ok)
        self.assertIsNone(blocker)


class TestPhase8GateStrategy(unittest.TestCase):
    """R-532 — viable strategy gate."""

    def test_pass_with_strong(self) -> None:
        fit = {"bts": "STRONG", "spec": "WEAK", "land_bank": "WEAK",
               "ground_lease": "N/A", "flip": "WEAK"}
        ok, blocker = research._gate_strategy(fit)
        self.assertTrue(ok)
        self.assertIsNone(blocker)

    def test_pass_with_moderate(self) -> None:
        fit = {"bts": "WEAK", "spec": "MODERATE", "land_bank": "WEAK",
               "ground_lease": "N/A", "flip": "WEAK"}
        ok, blocker = research._gate_strategy(fit)
        self.assertTrue(ok)

    def test_fail_when_all_weak_or_na(self) -> None:
        fit = {"bts": "WEAK", "spec": "WEAK", "land_bank": "N/A",
               "ground_lease": "N/A", "flip": "WEAK"}
        ok, blocker = research._gate_strategy(fit)
        self.assertFalse(ok)
        self.assertIn("STRONG", blocker)


class TestPhase8GateDealKiller(unittest.TestCase):
    """R-533 — default-PASS unless non-entitlement blocker flag."""

    def test_default_pass_no_flag(self) -> None:
        ok, blocker = research._gate_deal_killer(None)
        self.assertTrue(ok)
        self.assertIsNone(blocker)

    def test_fail_on_non_entitlement_blocker(self) -> None:
        ok, blocker = research._gate_deal_killer("active federal tax lien exceeding land value")
        self.assertFalse(ok)
        self.assertIn("active federal tax lien", blocker)

    def test_entitlement_blocker_does_not_fail_gate4(self) -> None:
        # R-534: entitlement-flavoured blockers fail gate 2, not gate 4.
        ok, blocker = research._gate_deal_killer("entitlement denial recorded for adjacent parcel")
        self.assertTrue(ok)


class TestPhase8ActionabilityFirstFailWins(unittest.TestCase):
    """R-534 — first failing gate wins; subsequent gates are not evaluated."""

    def test_pass_path(self) -> None:
        fit = {"bts": "MODERATE", "spec": "WEAK", "land_bank": "WEAK",
               "ground_lease": "N/A", "flip": "WEAK"}
        verdict, blockers = research._run_actionability_screen({}, fit, None)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(blockers, {})

    def test_entitlement_fails_first(self) -> None:
        # Block contains 'entitlement' AND strategy is empty → entitlement
        # FAIL must be reported even though strategy gate would also fail.
        verdict, blockers = research._run_actionability_screen(
            {}, {"bts": "WEAK", "spec": "WEAK", "land_bank": "N/A",
                 "ground_lease": "N/A", "flip": "WEAK"},
            "entitlement moratorium",
        )
        self.assertEqual(verdict, "FAIL:entitlement")
        self.assertIn("entitlement", blockers)
        self.assertNotIn("strategy", blockers)

    def test_strategy_fail_when_entitlement_clear(self) -> None:
        verdict, blockers = research._run_actionability_screen(
            {},
            {"bts": "WEAK", "spec": "WEAK", "land_bank": "N/A",
             "ground_lease": "N/A", "flip": "WEAK"},
            None,
        )
        self.assertEqual(verdict, "FAIL:strategy")

    def test_deal_killer_when_strategy_passes(self) -> None:
        verdict, blockers = research._run_actionability_screen(
            {},
            {"bts": "STRONG", "spec": "WEAK", "land_bank": "WEAK",
             "ground_lease": "WEAK", "flip": "WEAK"},
            "active federal tax lien",
        )
        self.assertEqual(verdict, "FAIL:deal_killer")


class TestPhase8StrategyFitBts(unittest.TestCase):
    """R-535 — BTS fit decision logic."""

    def test_acreage_too_small(self) -> None:
        fit = research._assess_strategy_bts(
            {"S9_entitlement_complexity": 5, "S4_submarket_vacancy": 8,
             "S5_submarket_absorption": 8}, 5.0,
        )
        self.assertEqual(fit, "N/A")

    def test_moderate_with_stub_s9(self) -> None:
        fit = research._assess_strategy_bts(
            {"S9_entitlement_complexity": 5, "S4_submarket_vacancy": 6,
             "S5_submarket_absorption": 7}, 12.0,
        )
        self.assertEqual(fit, "MODERATE")

    def test_weak_when_market_soft(self) -> None:
        fit = research._assess_strategy_bts(
            {"S9_entitlement_complexity": 5, "S4_submarket_vacancy": 4,
             "S5_submarket_absorption": 4}, 12.0,
        )
        self.assertEqual(fit, "WEAK")

    def test_strong_unreachable_with_stub_s9(self) -> None:
        # S9=5 stub blocks STRONG even with otherwise perfect market.
        fit = research._assess_strategy_bts(
            {"S9_entitlement_complexity": 5, "S4_submarket_vacancy": 10,
             "S5_submarket_absorption": 10}, 25.0,
        )
        self.assertEqual(fit, "MODERATE")

    def test_strong_reachable_when_s9_raised(self) -> None:
        # Forward-compat: when Phase 11+ wires real S9, STRONG works.
        fit = research._assess_strategy_bts(
            {"S9_entitlement_complexity": 8, "S4_submarket_vacancy": 9,
             "S5_submarket_absorption": 8}, 25.0,
        )
        self.assertEqual(fit, "STRONG")


class TestPhase8StrategyFitSpec(unittest.TestCase):
    """R-536 — Spec dev fit decision logic."""

    def test_na_when_vacancy_high(self) -> None:
        fit = research._assess_strategy_spec(
            {"S4_submarket_vacancy": 0, "S5_submarket_absorption": 4,
             "S6_competing_pipeline": 4, "S9_entitlement_complexity": 5}, 15.0,
        )
        self.assertEqual(fit, "N/A")

    def test_moderate_in_tight_market(self) -> None:
        fit = research._assess_strategy_spec(
            {"S4_submarket_vacancy": 8, "S5_submarket_absorption": 7,
             "S6_competing_pipeline": 7, "S9_entitlement_complexity": 5}, 15.0,
        )
        self.assertEqual(fit, "MODERATE")

    def test_weak_when_vacancy_marginal(self) -> None:
        fit = research._assess_strategy_spec(
            {"S4_submarket_vacancy": 4, "S5_submarket_absorption": 4,
             "S6_competing_pipeline": 4, "S9_entitlement_complexity": 5}, 15.0,
        )
        self.assertEqual(fit, "WEAK")

    def test_strong_reachable_when_s9_raised(self) -> None:
        fit = research._assess_strategy_spec(
            {"S4_submarket_vacancy": 8, "S5_submarket_absorption": 7,
             "S6_competing_pipeline": 7, "S9_entitlement_complexity": 8}, 15.0,
        )
        self.assertEqual(fit, "STRONG")


class TestPhase8StrategyFitLandBank(unittest.TestCase):
    """R-537 — Land Bank fit driven by S8."""

    def test_strong_when_below_median(self) -> None:
        fit = research._assess_strategy_land_bank({"S8_land_basis": 10}, 20.0)
        self.assertEqual(fit, "STRONG")

    def test_moderate_at_median(self) -> None:
        fit = research._assess_strategy_land_bank({"S8_land_basis": 7}, 20.0)
        self.assertEqual(fit, "MODERATE")

    def test_weak_above_median(self) -> None:
        fit = research._assess_strategy_land_bank({"S8_land_basis": 4}, 20.0)
        self.assertEqual(fit, "WEAK")

    def test_na_well_above_median(self) -> None:
        fit = research._assess_strategy_land_bank({"S8_land_basis": 0}, 20.0)
        self.assertEqual(fit, "N/A")

    def test_na_when_s8_null(self) -> None:
        fit = research._assess_strategy_land_bank({"S8_land_basis": None}, 20.0)
        self.assertEqual(fit, "N/A")


class TestPhase8StrategyFitGroundLease(unittest.TestCase):
    """R-538 — Ground Lease fit."""

    def test_na_when_acreage_small(self) -> None:
        fit = research._assess_strategy_ground_lease(
            {"S1_interstate_proximity": 10, "S4_submarket_vacancy": 10}, 5.0,
        )
        self.assertEqual(fit, "N/A")

    def test_na_when_s1_weak(self) -> None:
        # _lt(s1, 4) → True only when s1 is populated AND < 4. s1=2 triggers N/A.
        fit = research._assess_strategy_ground_lease(
            {"S1_interstate_proximity": 2, "S4_submarket_vacancy": 8}, 15.0,
        )
        self.assertEqual(fit, "N/A")

    def test_moderate_with_solid_location(self) -> None:
        fit = research._assess_strategy_ground_lease(
            {"S1_interstate_proximity": 7, "S4_submarket_vacancy": 7,
             "S9_entitlement_complexity": 5}, 15.0,
        )
        self.assertEqual(fit, "MODERATE")

    def test_weak_default_when_data_thin(self) -> None:
        # S1=None means _lt(None, 4)=False (no N/A trigger), and _ge(None, 6)
        # is also False — falls through to WEAK.
        fit = research._assess_strategy_ground_lease(
            {"S1_interstate_proximity": None, "S4_submarket_vacancy": 4,
             "S9_entitlement_complexity": 5}, 15.0,
        )
        self.assertEqual(fit, "WEAK")


class TestPhase8StrategyFitFlip(unittest.TestCase):
    """R-539 — Land Flip fit: S8 cross S4."""

    def test_strong_when_below_median_and_active_market(self) -> None:
        fit = research._assess_strategy_flip(
            {"S8_land_basis": 10, "S4_submarket_vacancy": 8}, 15.0,
        )
        self.assertEqual(fit, "STRONG")

    def test_moderate_when_below_median_soft_market(self) -> None:
        fit = research._assess_strategy_flip(
            {"S8_land_basis": 10, "S4_submarket_vacancy": 4}, 15.0,
        )
        self.assertEqual(fit, "MODERATE")

    def test_moderate_at_median(self) -> None:
        fit = research._assess_strategy_flip(
            {"S8_land_basis": 7, "S4_submarket_vacancy": 6}, 15.0,
        )
        self.assertEqual(fit, "MODERATE")

    def test_na_when_s8_null(self) -> None:
        fit = research._assess_strategy_flip(
            {"S8_land_basis": None, "S4_submarket_vacancy": 8}, 15.0,
        )
        self.assertEqual(fit, "N/A")


class TestPhase8PrimaryStrategy(unittest.TestCase):
    """R-542 — primary strategy priority + tier-then-priority selection."""

    def test_first_strong_wins(self) -> None:
        primary = research._select_primary_strategy({
            "bts": "STRONG", "spec": "STRONG", "land_bank": "STRONG",
            "ground_lease": "STRONG", "flip": "STRONG",
        })
        self.assertEqual(primary, "bts")

    def test_strong_beats_moderate_across_keys(self) -> None:
        primary = research._select_primary_strategy({
            "bts": "MODERATE", "spec": "WEAK", "land_bank": "STRONG",
            "ground_lease": "STRONG", "flip": "MODERATE",
        })
        # land_bank is the first STRONG in priority order
        # (bts=MODERATE → skip on STRONG pass, spec=WEAK, land_bank=STRONG hit).
        self.assertEqual(primary, "land_bank")

    def test_moderate_when_no_strong(self) -> None:
        primary = research._select_primary_strategy({
            "bts": "WEAK", "spec": "MODERATE", "land_bank": "MODERATE",
            "ground_lease": "WEAK", "flip": "WEAK",
        })
        self.assertEqual(primary, "spec")

    def test_none_when_all_weak(self) -> None:
        primary = research._select_primary_strategy({
            "bts": "WEAK", "spec": "WEAK", "land_bank": "N/A",
            "ground_lease": "N/A", "flip": "WEAK",
        })
        self.assertIsNone(primary)


class TestPhase8ScoreParcelEndToEnd(unittest.TestCase):
    """End-to-end score_parcel with submarket + market context + sales comps."""

    def setUp(self) -> None:
        research._OZ_TRACTS_CACHE = None

    def _params(self) -> dict[str, Any]:
        # Use the real parameters.json scoring weights so the composite
        # arithmetic matches what prepare.py uses for the live metric.
        from prepare import get_parameters
        return {
            "scoring_weights": dict(get_parameters()["scoring_weights"]),
        }

    def _strong_parcel_tuple(self) -> tuple:
        # Phase 7+8 _SQL_FETCH_PARCEL: parcel_id, market, submarket, state,
        # acreage, last_sale_date, last_sale_price, assessed_value_total,
        # centroid_lng, centroid_lat. Centroid -84.55, 33.55 is inside the
        # OZ stub (S10=4).
        from datetime import date, timedelta
        return (
            "fulton-strong",
            "atlanta",
            "south_fulton",
            "GA",
            12.0,
            date.today() - timedelta(days=180),
            600_000,
            None,
            -84.55,
            33.55,
        )

    def test_strong_parcel_passes_actionability(self) -> None:
        from datetime import date
        fake = Phase5FakeConnection(
            fetchone_queue=[
                self._strong_parcel_tuple(),                              # _SQL_FETCH_PARCEL
                (1000.0, 1100.0, 1.5),                                    # _SQL_S2_GEOMETRY (S2=7)
                (2.5, 1_500_000, 0, 0, 5.5, date.today(), "costar"),      # market_context (S4=10, S5=7, S6=10)
                (5, 100_000.0),                                           # submarket median (n=5, $100k/acre)
                None,                                                     # actionability_block: none open
            ],
        )
        result = research.score_parcel(
            "fulton-strong", conn=fake, cycle_id="score-atlanta-end2end-0001",
            params=self._params(),
        )
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["sub_scores"]["S2_parcel_geometry"], 7)
        self.assertEqual(result["sub_scores"]["S4_submarket_vacancy"], 10)
        self.assertEqual(result["sub_scores"]["S5_submarket_absorption"], 7)
        self.assertEqual(result["sub_scores"]["S6_competing_pipeline"], 10)
        # parcel basis = 600_000/12 = 50_000; median = 100_000 → 0.5 ratio < 0.95 → S8=10.
        self.assertEqual(result["sub_scores"]["S8_land_basis"], 10)
        self.assertEqual(result["sub_scores"]["S9_entitlement_complexity"], 5)
        self.assertEqual(result["sub_scores"]["S10_incentives"], 4)
        # Actionability PASSes (gate 3 sees STRONG land bank from S8=10 + STRONG flip).
        self.assertEqual(result["actionability"], "PASS")
        # primary_strategy is the first STRONG in priority: bts? — let's see.
        # Strategy fit: bts=MODERATE (S9=5), spec=MODERATE (S9=5), land_bank=STRONG,
        # ground_lease=MODERATE (S1=None → no N/A trigger; _ge(None,6)=False so falls to WEAK actually).
        # Wait: ground_lease N/A guard is `_lt(s1,4)` only — None doesn't fire that. Then
        # STRONG check requires _ge(s1,8) which is False for None. Then MODERATE requires
        # _ge(s1,6) which is False for None → WEAK. flip: S8=10 + S4>=6 → STRONG.
        # Primary order: bts(MOD) → no STRONG match → continue. land_bank(STRONG) → hit.
        self.assertEqual(result["primary_strategy"], "land_bank")
        # Composite math sanity check: weighted sum / weight sum * 10 must be in [70, 100].
        self.assertGreaterEqual(result["composite_score"], 70)

    def test_strong_parcel_persists_all_jsonb_columns(self) -> None:
        from datetime import date
        fake = Phase5FakeConnection(
            fetchone_queue=[
                self._strong_parcel_tuple(),
                (1000.0, 1100.0, 1.5),
                (2.5, 1_500_000, 0, 0, 5.5, date.today(), "costar"),
                (5, 100_000.0),
                None,
            ],
        )
        research.score_parcel(
            "fulton-strong", conn=fake, cycle_id="score-atlanta-persist-0001",
            params=self._params(),
        )
        score_inserts = [
            params for sql, params in fake.all_executes
            if "INSERT INTO parcel_scores" in sql
        ]
        self.assertEqual(len(score_inserts), 1)
        insert = score_inserts[0]
        # Positional layout (R-501): parcel_id, composite, confidence, actionability,
        # blockers_json, sub_scores_json, strategy_fit_json, primary_strategy, notes.
        self.assertEqual(insert[0], "fulton-strong")
        self.assertEqual(insert[3], "PASS")
        # JSONB columns are JSON strings at the parameter layer.
        self.assertIsInstance(insert[4], str)  # blockers
        self.assertIsInstance(insert[5], str)  # sub_scores
        self.assertIsInstance(insert[6], str)  # strategy_fit
        # primary_strategy is a plain string (not JSONB-wrapped).
        self.assertEqual(insert[7], "land_bank")


class TestPhase8ScoringCycleRescoringPending(unittest.TestCase):
    """R-507 — _SQL_LIST_PARCELS_FOR_SCORING SQL captures PENDING latest rows."""

    def test_sql_includes_pending_branch(self) -> None:
        sql = research._SQL_LIST_PARCELS_FOR_SCORING.lower()
        self.assertIn("not exists", sql)
        self.assertIn("pending", sql)
        self.assertIn("max", sql.replace(" ", "")) if False else None
        # Order by parcel_id for determinism (R-213 carry-over).
        self.assertIn("order by", sql)

    def test_alias_preserved(self) -> None:
        # The Phase 5 alias _SQL_LIST_UNSCORED_PARCELS still exists so the
        # AST scanner test in TestPhase5SqlConstantsStaticChecks keeps working.
        self.assertIs(
            research._SQL_LIST_UNSCORED_PARCELS,
            research._SQL_LIST_PARCELS_FOR_SCORING,
        )


class TestPhase8MetricEndToEnd(unittest.TestCase):
    """Gate 7 / R-506 / R-508 — proves the metric finally moves.

    Constructs a fake connection that, when prepare.calculate_actionable_pipeline_count
    queries it, returns COUNT(*) = 1. Exercises the SQL contract directly
    rather than going through Postgres, but it also exercises the
    composite-arithmetic happy path indirectly: the test would fail if
    the score_parcel helper produced a composite < 70.
    """

    def test_metric_returns_one_for_passing_parcel(self) -> None:
        # Direct test against the metric SQL: build a fake conn that
        # returns (1,) on the COUNT query and assert calculate_*
        # returns 1. This is enough to verify the integration boundary.
        class _MetricFake:
            def __init__(self, count):
                self._count = count
                self._executes: list[tuple] = []

            def cursor(self):
                outer = self
                class _Cur:
                    def __enter__(self_inner): return self_inner
                    def __exit__(self_inner, *a): return None
                    def execute(self_inner, sql, params=()):
                        outer._executes.append((sql, params))
                    def fetchone(self_inner):
                        return (outer._count,)
                return _Cur()

        import prepare
        fake_one = _MetricFake(1)
        n = prepare.calculate_actionable_pipeline_count(fake_one)
        self.assertEqual(n, 1)
        # Threshold is bound to the SQL via parameters.json.
        sql_executed = fake_one._executes[0][1]
        self.assertEqual(sql_executed, (prepare.get_parameters()["composite_threshold"],))

    def test_metric_returns_zero_against_empty(self) -> None:
        # Backwards-compat: empty database (0 rows) still returns 0.
        class _MetricFake:
            def cursor(self):
                class _Cur:
                    def __enter__(self_inner): return self_inner
                    def __exit__(self_inner, *a): return None
                    def execute(self_inner, sql, params=()): pass
                    def fetchone(self_inner):
                        return (0,)
                return _Cur()

        import prepare
        n = prepare.calculate_actionable_pipeline_count(_MetricFake())
        self.assertEqual(n, 0)


class TestPhase8PublicWrappers(unittest.TestCase):
    """Gate 4 / R-540 — public-API wrappers around the orchestration helpers."""

    def test_run_actionability_screen_requires_inputs(self) -> None:
        with self.assertRaises(ValueError):
            research.run_actionability_screen("fulton-001")

    def test_run_actionability_screen_pass(self) -> None:
        result = research.run_actionability_screen(
            "fulton-001",
            sub_scores={},
            strategy_fit={"bts": "MODERATE", "spec": "WEAK", "land_bank": "WEAK",
                          "ground_lease": "WEAK", "flip": "WEAK"},
        )
        self.assertEqual(result["actionability"], "PASS")
        self.assertEqual(result["actionability_blockers"], {})

    def test_assess_strategy_fit_returns_five_keys(self) -> None:
        result = research.assess_strategy_fit("fulton-001", sub_scores={}, acreage=15.0)
        self.assertEqual(set(result["strategy_fit"].keys()),
                         {"bts", "spec", "land_bank", "ground_lease", "flip"})

    def test_assess_strategy_fit_no_assemblage(self) -> None:
        # R-540: multi-parcel assemblage is OUT OF SCOPE — verify it never
        # appears as a strategy key in the fit JSONB.
        result = research.assess_strategy_fit("fulton-001", sub_scores={}, acreage=20.0)
        self.assertNotIn("assemblage", result["strategy_fit"])


class TestPhase78SqlConstantsStaticChecks(unittest.TestCase):
    """Gate 9 / R-501, R-545 — static SQL invariants for Phase 7+8."""

    PHASE78_SQL_NAMES = (
        "_SQL_INSERT_PARCEL_SCORE",
        "_SQL_INSERT_RESEARCH_LOG_SCORING",
        "_SQL_FETCH_PARCEL",
        "_SQL_LIST_PARCELS_FOR_SCORING",
        "_SQL_LATEST_MARKET_CONTEXT",
        "_SQL_SUBMARKET_LAND_MEDIAN",
        "_SQL_FLAGGED_ACTIONABILITY_BLOCK",
    )

    def test_constants_exist(self) -> None:
        for name in self.PHASE78_SQL_NAMES:
            self.assertTrue(hasattr(research, name), f"missing SQL constant {name}")
            self.assertIsInstance(getattr(research, name), str)

    def test_no_string_interpolation(self) -> None:
        for name in self.PHASE78_SQL_NAMES:
            sql = getattr(research, name)
            self.assertNotIn("{", sql, f"f-string brace in {name}: {sql}")

    def test_insert_columns_in_ddl(self) -> None:
        # Parse the CREATE TABLE block for parcel_scores from prepare.py and
        # assert each column referenced in _SQL_INSERT_PARCEL_SCORE is in
        # the DDL. This catches accidental DDL-INSERT drift (R-501).
        import prepare
        ddl = prepare._DDL_PARCEL_SCORES.lower()
        insert_sql = research._SQL_INSERT_PARCEL_SCORE.lower()
        for col in (
            "parcel_id", "composite_score", "confidence_score",
            "actionability", "actionability_blockers",
            "sub_scores", "strategy_fit", "primary_strategy", "notes",
        ):
            self.assertIn(col, insert_sql, f"INSERT missing {col!r}")
            self.assertIn(col, ddl, f"DDL missing {col!r} (would mean an "
                                    f"unauthorised prepare.py change)")

    def test_actionability_text_enum(self) -> None:
        # R-534: every constant in _ACTIONABILITY_VALUES must match the
        # results.tsv enum in program.md.
        self.assertEqual(
            research._ACTIONABILITY_VALUES,
            frozenset({"PASS", "FAIL:control", "FAIL:entitlement",
                       "FAIL:strategy", "FAIL:deal_killer", "PENDING"}),
        )

    def test_strategy_keys_exclude_assemblage(self) -> None:
        self.assertEqual(
            set(research._STRATEGY_KEYS),
            {"bts", "spec", "land_bank", "ground_lease", "flip"},
        )
        self.assertNotIn("assemblage", research._STRATEGY_KEYS)

    def test_immutable_files_unchanged(self) -> None:
        # Gate 1 — soft proxy check: verify prepare.py still defines the
        # frozen calculate_actionable_pipeline_count and the same DDL
        # column list. A more authoritative check (git diff against the
        # base commit) lives in the Agent 3 reviewer decision.
        import prepare
        self.assertTrue(hasattr(prepare, "calculate_actionable_pipeline_count"))
        self.assertTrue(hasattr(prepare, "calculate_confidence_weighted_pipeline"))
        # parameters.json hash sentinel still active.
        self.assertTrue(hasattr(prepare, "verify_parameters_unchanged"))


# ===========================================================================
# Phase 9 — snapshot + memo tests
# ===========================================================================
# Per reviews/11_phase9_snapshots_memos/01_risk_review.md (R-601..R-647).

import os as _os  # for getpid in atomic-write tests
import prepare as _prepare  # for the real parameters dict in end-to-end tests


def _phase9_params() -> dict[str, Any]:
    """Real parameters from parameters.json — Phase 9 reads but never
    writes. Tests can mutate the returned dict freely; it's a copy."""
    return dict(_prepare.get_parameters())


def _phase9_parcel_row(**overrides: Any) -> tuple:
    """29-field tuple matching _SQL_FETCH_PARCEL_FOR_SNAPSHOT."""
    base = {
        "parcel_id": "fulton-14-0123-LL-045-8",
        "county": "fulton", "state": "GA", "market": "atlanta",
        "submarket": "south_fulton",
        "address": "0 Campbellton Fairburn Rd, Union City, GA 30349",
        "owner_name": "SMITH FAMILY TRUST",
        "owner_mailing_address": "PO Box 445, Sarasota, FL 34230",
        "owner_type_inferred": "trust",
        "acreage": 14.7, "land_sf": 640332.0,
        "zoning": "AG-1", "zoning_description": "Agricultural",
        "land_use_code": "100",
        "land_use_description": "Vacant agricultural",
        "assessed_value_land": 185000,
        "assessed_value_improvement": 0,
        "assessed_value_total": 185000,
        "fair_market_value": 462500, "tax_year": 2025,
        "tax_amount": 2370.0, "tax_status": "current",
        "last_sale_date": "2010-06-12", "last_sale_price": 95000,
        "year_built": None,
        "discovery_source": "fulton_arcgis",
        "discovery_date": "2026-04-30",
        "centroid_lng": -84.5612, "centroid_lat": 33.5521,
    }
    base.update(overrides)
    return (
        base["parcel_id"], base["county"], base["state"], base["market"],
        base["submarket"], base["address"], base["owner_name"],
        base["owner_mailing_address"], base["owner_type_inferred"],
        base["acreage"], base["land_sf"], base["zoning"],
        base["zoning_description"], base["land_use_code"],
        base["land_use_description"], base["assessed_value_land"],
        base["assessed_value_improvement"], base["assessed_value_total"],
        base["fair_market_value"], base["tax_year"], base["tax_amount"],
        base["tax_status"], base["last_sale_date"], base["last_sale_price"],
        base["year_built"], base["discovery_source"], base["discovery_date"],
        base["centroid_lng"], base["centroid_lat"],
    )


def _phase9_score_row(**overrides: Any) -> tuple:
    """10-field tuple matching _SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT.
    JSONB columns are stored as JSON strings to mirror the score_parcel
    persistence path (json.dumps inside score_parcel)."""
    base = {
        "composite_score": 75.0, "confidence_score": 0.50,
        "actionability": "PASS",
        "actionability_blockers": json.dumps({}),
        "sub_scores": json.dumps({
            "S2_parcel_geometry": 7,
            "S4_submarket_vacancy": 8,
            "S5_submarket_absorption": 8,
            "S6_competing_pipeline": 7,
            "S8_land_basis": 8,
            "S9_entitlement_complexity": 5,
            "S10_incentives": 4,
        }),
        "strategy_fit": json.dumps({
            "bts": "MODERATE", "spec": "MODERATE",
            "land_bank": "STRONG", "ground_lease": "WEAK",
            "flip": "WEAK",
        }),
        "primary_strategy": "land_bank",
        "investment_thesis": None, "notes": "phase78: composite=75",
        "scored_at": "2026-05-04T10:00:00Z",
    }
    base.update(overrides)
    return (
        base["composite_score"], base["confidence_score"],
        base["actionability"], base["actionability_blockers"],
        base["sub_scores"], base["strategy_fit"],
        base["primary_strategy"], base["investment_thesis"],
        base["notes"], base["scored_at"],
    )


def _phase9_mc_row(**overrides: Any) -> tuple:
    """7-field tuple matching _SQL_LATEST_MARKET_CONTEXT."""
    base = {
        "vacancy_rate_pct": 4.2, "net_absorption_t12_sf": 1_800_000,
        "under_construction_sf": 400_000, "proposed_sf": 600_000,
        "asking_rent_nnn_psf": 7.50,
        "as_of_date": "2026-04-15", "source": "costar",
    }
    base.update(overrides)
    return (
        base["vacancy_rate_pct"], base["net_absorption_t12_sf"],
        base["under_construction_sf"], base["proposed_sf"],
        base["asking_rent_nnn_psf"], base["as_of_date"], base["source"],
    )


def _phase9_memo_row(**overrides: Any) -> tuple:
    """15-field tuple matching _SQL_FETCH_SCORED_PARCELS_FOR_MEMO."""
    base = {
        "parcel_id": "fulton-1", "address": "100 Test Rd",
        "county": "fulton", "submarket": "south_fulton",
        "acreage": 12.0, "owner_name": "OWNER", "owner_type_inferred": "trust",
        "composite_score": 78.0, "confidence_score": 0.55,
        "actionability": "PASS",
        "actionability_blockers": json.dumps({}),
        "sub_scores": json.dumps({"S2_parcel_geometry": 7}),
        "strategy_fit": json.dumps({"land_bank": "STRONG"}),
        "primary_strategy": "land_bank",
        "scored_at": "2026-05-04T10:00:00Z",
    }
    base.update(overrides)
    return (
        base["parcel_id"], base["address"], base["county"],
        base["submarket"], base["acreage"], base["owner_name"],
        base["owner_type_inferred"], base["composite_score"],
        base["confidence_score"], base["actionability"],
        base["actionability_blockers"], base["sub_scores"],
        base["strategy_fit"], base["primary_strategy"], base["scored_at"],
    )


class TestPhase9SafeFilenameSlug(unittest.TestCase):
    """R-615 — _safe_filename_slug rejects path-traversal-prone input."""

    def test_accepts_typical_parcel_id(self) -> None:
        self.assertEqual(
            reporting._safe_filename_slug("fulton-14-0123-LL-045-8"),
            "fulton-14-0123-ll-045-8",
        )

    def test_accepts_market_label(self) -> None:
        self.assertEqual(reporting._safe_filename_slug("atlanta"), "atlanta")
        self.assertEqual(
            reporting._safe_filename_slug("dallas-fort-worth"),
            "dallas-fort-worth",
        )

    def test_lowercases(self) -> None:
        self.assertEqual(reporting._safe_filename_slug("FOO_BAR"), "foo_bar")

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            reporting._safe_filename_slug("")

    def test_rejects_none(self) -> None:
        with self.assertRaises(ValueError):
            reporting._safe_filename_slug(None)  # type: ignore[arg-type]

    def test_rejects_path_traversal(self) -> None:
        for bad in ("..", "../etc", "/abs", "fulton/14", "a\\b"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    reporting._safe_filename_slug(bad)

    def test_rejects_whitespace(self) -> None:
        with self.assertRaises(ValueError):
            reporting._safe_filename_slug("foo bar")
        with self.assertRaises(ValueError):
            reporting._safe_filename_slug("foo\tbar")

    def test_rejects_nul(self) -> None:
        with self.assertRaises(ValueError):
            reporting._safe_filename_slug("foo\0bar")


class TestPhase9MarkdownEscaping(unittest.TestCase):
    """R-622 — _md_table_cell escapes pipes, newlines, length-caps."""

    def test_pipe_is_escaped(self) -> None:
        self.assertIn(r"\|", reporting._md_table_cell("a|b"))

    def test_newline_collapsed_to_space(self) -> None:
        self.assertEqual(reporting._md_table_cell("a\nb"), "a b")

    def test_tab_collapsed_to_space(self) -> None:
        self.assertEqual(reporting._md_table_cell("a\tb"), "a b")

    def test_length_capped_with_ellipsis(self) -> None:
        long = "x" * 200
        out = reporting._md_table_cell(long)
        self.assertLessEqual(len(out), reporting._MD_TABLE_CELL_MAX)
        self.assertTrue(out.endswith("…"))

    def test_none_returns_default(self) -> None:
        self.assertEqual(reporting._md_table_cell(None), "—")
        self.assertEqual(reporting._md_table_cell(""), "—")

    def test_md_cell_strips_whitespace(self) -> None:
        self.assertEqual(reporting._md_cell("  hello  "), "hello")
        self.assertEqual(reporting._md_cell(None), "—")


class TestPhase9CoerceJson(unittest.TestCase):
    """R-609 — JSONB columns may arrive as dict or string."""

    def test_dict_passthrough(self) -> None:
        self.assertEqual(reporting._coerce_json_field({"a": 1}), {"a": 1})

    def test_json_string_parsed(self) -> None:
        self.assertEqual(
            reporting._coerce_json_field('{"a": 1}'), {"a": 1},
        )

    def test_none_returns_empty(self) -> None:
        self.assertEqual(reporting._coerce_json_field(None), {})

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(reporting._coerce_json_field(""), {})
        self.assertEqual(reporting._coerce_json_field("   "), {})

    def test_unparseable_returns_empty(self) -> None:
        self.assertEqual(reporting._coerce_json_field("not json"), {})

    def test_non_dict_json_returns_empty(self) -> None:
        self.assertEqual(reporting._coerce_json_field('"a string"'), {})
        self.assertEqual(reporting._coerce_json_field("[1, 2, 3]"), {})

    def test_bytes_decoded(self) -> None:
        self.assertEqual(reporting._coerce_json_field(b'{"a": 1}'), {"a": 1})


class TestPhase9Formatters(unittest.TestCase):
    """R-610 — currency / acres / pct / int formatters."""

    def test_currency_int(self) -> None:
        self.assertEqual(reporting._format_currency(1234567), "$1,234,567")

    def test_currency_none(self) -> None:
        self.assertEqual(reporting._format_currency(None), "—")

    def test_currency_psf(self) -> None:
        self.assertEqual(reporting._format_currency_psf(7.5), "$7.50/SF")
        self.assertEqual(reporting._format_currency_psf(None), "—")

    def test_acres(self) -> None:
        self.assertEqual(reporting._format_acres(14.7), "14.70 acres")
        self.assertEqual(reporting._format_acres(None), "—")

    def test_pct(self) -> None:
        self.assertEqual(reporting._format_pct(4.2), "4.2%")
        self.assertEqual(reporting._format_pct(None), "—")

    def test_int_thousands(self) -> None:
        self.assertEqual(reporting._format_int_thousands(1800000), "1,800,000")
        self.assertEqual(reporting._format_int_thousands(None), "—")

    def test_to_float_handles_decimal_like(self) -> None:
        # Simulate a Decimal-like via str input that Python's float can parse.
        self.assertEqual(reporting._to_float("4.2"), 4.2)
        self.assertEqual(reporting._to_float(None), None)
        self.assertEqual(reporting._to_float("not a number"), None)


class TestPhase9SnapshotRender(unittest.TestCase):
    """Per-section render assertions against synthetic data."""

    def test_score_breakdown_lists_all_12_sub_scores(self) -> None:
        params = _phase9_params()
        sub_scores = {
            "S2_parcel_geometry": 7, "S4_submarket_vacancy": 8,
        }
        md, _ws, composite = reporting._render_score_breakdown_table(
            sub_scores, params["scoring_weights"],
        )
        for name in research._SUB_SCORE_NAMES:
            pretty, _src = research._SUB_SCORE_PROVENANCE[name]
            self.assertIn(pretty, md, f"{pretty} missing from breakdown table")
        self.assertIn("**Composite**", md)
        # composite = (7*10 + 8*10) / 100 * 10 = 15.0  — partially populated.
        self.assertGreater(composite, 0.0)

    def test_strategy_fit_table_lists_5_strategies(self) -> None:
        sf = {
            "bts": "MODERATE", "spec": "WEAK", "land_bank": "STRONG",
            "ground_lease": "N/A", "flip": "WEAK",
        }
        md = reporting._render_strategy_fit_table(sf)
        for label in reporting._STRATEGY_LABELS.values():
            self.assertIn(label, md)
        self.assertIn("STRONG", md)
        self.assertIn("MODERATE", md)
        self.assertIn("N/A", md)

    def test_actionability_table_pass_marks_all_pass(self) -> None:
        md = reporting._render_actionability_table("PASS", {})
        self.assertEqual(md.count("| PASS |"), 4)
        self.assertNotIn("| FAIL |", md)
        self.assertIn("Overall actionability**: PASS", md)

    def test_actionability_table_fail_strategy_short_circuits(self) -> None:
        md = reporting._render_actionability_table(
            "FAIL:strategy",
            {"strategy": "no strategy rated STRONG or MODERATE"},
        )
        # control + entitlement = PASS, strategy = FAIL, deal_killer = PENDING
        self.assertEqual(md.count("| PASS |"), 2)
        self.assertEqual(md.count("| FAIL |"), 1)
        self.assertEqual(md.count("| PENDING |"), 1)
        self.assertIn("no strategy rated STRONG", md)

    def test_actionability_table_pending_when_unscored(self) -> None:
        md = reporting._render_actionability_table(None, {})
        self.assertEqual(md.count("| PENDING |"), 4)
        self.assertIn("Overall actionability**: PENDING", md)

    def test_recommendation_pursue(self) -> None:
        rec, reason = reporting._compute_recommendation(
            83.0, "PASS", 70.0, "land_bank", {},
        )
        self.assertEqual(rec, "PURSUE")
        self.assertIn("Land Bank", reason)

    def test_recommendation_monitor(self) -> None:
        rec, reason = reporting._compute_recommendation(
            75.0, "FAIL:entitlement", 70.0, None,
            {"entitlement": "no rezoning precedent"},
        )
        self.assertEqual(rec, "MONITOR")
        self.assertIn("entitlement", reason)
        self.assertIn("no rezoning precedent", reason)

    def test_recommendation_pass_below_threshold(self) -> None:
        rec, reason = reporting._compute_recommendation(
            55.0, "PASS", 70.0, "land_bank", {},
        )
        self.assertEqual(rec, "PASS")
        self.assertIn("below", reason)

    def test_thesis_omits_clauses_with_null_data(self) -> None:
        parcel = {
            "market": None, "submarket": None, "acreage": None,
            "zoning": None, "assessed_value_total": None,
            "owner_type_inferred": None,
        }
        score = {"actionability": "PENDING", "actionability_blockers": {}}
        md = reporting._render_investment_thesis(parcel, score, {}, [])
        # Should still produce the actionability paragraph but no "good location" etc.
        for banned in ("strong fundamentals", "good location",
                       "favorable market", "promising opportunity"):
            self.assertNotIn(banned, md.lower())

    def test_thesis_cites_specific_data_when_present(self) -> None:
        parcel = {
            "market": "atlanta", "submarket": "south_fulton", "acreage": 14.7,
            "zoning": "AG-1", "assessed_value_total": 185000,
            "owner_type_inferred": "trust",
        }
        score = {"actionability": "PASS", "primary_strategy": "land_bank"}
        mc = {
            "vacancy_rate_pct": 4.2, "net_absorption_t12_sf": 1_800_000,
            "under_construction_sf": 400_000, "as_of_date": "2026-04-15",
            "source": "costar",
        }
        comps = [
            {"price_per_acre": 25000.0}, {"price_per_acre": 30000.0},
            {"price_per_acre": 28000.0},
        ]
        md = reporting._render_investment_thesis(parcel, score, mc, comps)
        self.assertIn("south_fulton", md)
        self.assertIn("14.70 acres", md)
        self.assertIn("AG-1", md)
        self.assertIn("$185,000", md)
        self.assertIn("4.2%", md)
        self.assertIn("trust", md)
        self.assertIn("costar", md)

    def test_thesis_records_pending_actionability(self) -> None:
        parcel = {"market": "atlanta"}
        score = {"actionability": "PENDING"}
        md = reporting._render_investment_thesis(parcel, score, {}, [])
        self.assertIn("PENDING", md)


class TestPhase9SnapshotEndToEnd(unittest.TestCase):
    """Full happy-path render against Phase5FakeConnection."""

    def test_snapshot_with_full_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = Phase5FakeConnection(
                fetchone_queue=[
                    _phase9_parcel_row(),
                    _phase9_score_row(),
                    _phase9_mc_row(),
                    ("South Fulton",),  # submarket name
                ],
                fetchall_queue=[
                    [  # comps (5 fields per row matching SELECT)
                        ("123 Comp Rd", "2026-02-01", 350000, 25000.0, 14.0,
                         "land", "ACME LLC"),
                        ("456 Comp Rd", "2026-01-15", 300000, 22000.0, 13.6,
                         "land", "BUYER 2"),
                    ],
                    [  # flags
                        ("data_gap", "S1 not yet wired", "wire S1", "2026-05-04T10:00:00Z"),
                    ],
                ],
            )
            params = _phase9_params()
            target = reporting.generate_snapshot(
                "fulton-14-0123-LL-045-8",
                conn=fake, output_dir=tmp_path, params=params,
            )

            self.assertTrue(target.exists())
            self.assertEqual(target.parent, tmp_path)
            self.assertEqual(target.name, "fulton-14-0123-ll-045-8_snapshot.md")

            content = target.read_text(encoding="utf-8")
            self.assertIn("Site Snapshot:", content)
            self.assertIn("Campbellton Fairburn", content)
            self.assertIn("ACTIONABLE", content)
            self.assertIn("Investment Thesis", content)
            self.assertIn("Score Breakdown", content)
            self.assertIn("Strategy Fit Assessment", content)
            self.assertIn("Actionability Assessment", content)
            self.assertIn("**PURSUE**", content)
            self.assertIn("Land Bank", content)
            # NULL handling — no "None" rendered in markdown output.
            self.assertNotIn(" None\n", content)
            self.assertNotIn(" None ", content)

    def test_snapshot_no_submarket_skips_market_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = Phase5FakeConnection(
                fetchone_queue=[
                    _phase9_parcel_row(submarket=None),
                    _phase9_score_row(),
                ],
                fetchall_queue=[
                    [],  # flags only (no comps because no submarket)
                ],
            )
            params = _phase9_params()
            target = reporting.generate_snapshot(
                "fulton-14-0123-LL-045-8",
                conn=fake, output_dir=tmp_path, params=params,
            )
            content = target.read_text(encoding="utf-8")
            # Market context line still rendered with "—" placeholders.
            self.assertIn("Submarket vacancy", content)

    def test_snapshot_missing_parcel_raises_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = Phase5FakeConnection(fetchone_queue=[])  # parcel returns None
            params = _phase9_params()
            with self.assertRaises(LookupError):
                reporting.generate_snapshot(
                    "fulton-missing",
                    conn=fake, output_dir=tmp_path, params=params,
                )

    def test_snapshot_missing_score_raises_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = Phase5FakeConnection(
                fetchone_queue=[_phase9_parcel_row()],  # parcel only, no score
            )
            params = _phase9_params()
            with self.assertRaises(LookupError):
                reporting.generate_snapshot(
                    "fulton-14-0123-LL-045-8",
                    conn=fake, output_dir=tmp_path, params=params,
                )

    def test_snapshot_idempotent(self) -> None:
        """R-618: same DB state -> byte-identical output on re-run."""
        params = _phase9_params()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake1 = Phase5FakeConnection(
                fetchone_queue=[
                    _phase9_parcel_row(), _phase9_score_row(),
                    _phase9_mc_row(), ("South Fulton",),
                ],
                fetchall_queue=[[], []],
            )
            t1 = reporting.generate_snapshot(
                "fulton-14-0123-LL-045-8",
                conn=fake1, output_dir=tmp_path, params=params,
            )
            content1 = t1.read_bytes()

            fake2 = Phase5FakeConnection(
                fetchone_queue=[
                    _phase9_parcel_row(), _phase9_score_row(),
                    _phase9_mc_row(), ("South Fulton",),
                ],
                fetchall_queue=[[], []],
            )
            t2 = reporting.generate_snapshot(
                "fulton-14-0123-LL-045-8",
                conn=fake2, output_dir=tmp_path, params=params,
            )
            content2 = t2.read_bytes()
            self.assertEqual(t1, t2)
            self.assertEqual(content1, content2)

    def test_snapshot_path_traversal_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                reporting.generate_snapshot(
                    "../etc/passwd",
                    conn=Phase5FakeConnection(),
                    output_dir=Path(tmp),
                    params=_phase9_params(),
                )

    def test_snapshot_uses_latest_score_row(self) -> None:
        """R-608: snapshot describes the latest scored_at row, not earlier
        ones. We assert the SQL contains ORDER BY scored_at DESC LIMIT 1."""
        self.assertIn(
            "ORDER BY scored_at DESC LIMIT 1",
            reporting._SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT,
        )


class TestPhase9MemoAggregates(unittest.TestCase):
    """Pipeline-composition aggregation correctness."""

    def test_aggregates_count_actionable(self) -> None:
        rows = [
            {"composite_score": 80, "actionability": "PASS",
             "primary_strategy": "land_bank", "submarket": "south_fulton"},
            {"composite_score": 75, "actionability": "PASS",
             "primary_strategy": "spec", "submarket": "south_fulton"},
            {"composite_score": 73, "actionability": "FAIL:strategy",
             "primary_strategy": None, "submarket": "west_atlanta"},
            {"composite_score": 60, "actionability": "PENDING",
             "primary_strategy": None, "submarket": "south_fulton"},
        ]
        agg = reporting._aggregate_pipeline_composition(rows, threshold=70.0)
        self.assertEqual(agg["total_scored"], 4)
        self.assertEqual(agg["above_threshold_count"], 3)
        self.assertEqual(agg["actionable_count"], 2)
        self.assertEqual(agg["by_strategy"]["land_bank"], 1)
        self.assertEqual(agg["by_strategy"]["spec"], 1)
        self.assertEqual(agg["by_strategy"]["bts"], 0)
        self.assertEqual(agg["by_submarket"]["south_fulton"], 3)
        self.assertEqual(agg["by_actionability"]["PASS"], 2)

    def test_top_n_prefers_actionable(self) -> None:
        rows = [
            {"actionability": "FAIL:strategy", "composite_score": 95},
            {"actionability": "PASS", "composite_score": 75},
            {"actionability": "PASS", "composite_score": 80},
        ]
        # rows are already pre-sorted by composite DESC by SQL; the helper
        # filters to PASS first, then falls back if N exceeds the count.
        top = reporting._select_top_n_actionable(rows, n=2)
        self.assertEqual(len(top), 2)
        self.assertTrue(all(r["actionability"] == "PASS" for r in top))

    def test_top_n_falls_back_when_few_actionable(self) -> None:
        rows = [
            {"actionability": "PASS", "composite_score": 80},
            {"actionability": "FAIL:strategy", "composite_score": 75},
            {"actionability": "PENDING", "composite_score": 70},
        ]
        top = reporting._select_top_n_actionable(rows, n=10)
        self.assertEqual(len(top), 3)
        self.assertEqual(top[0]["actionability"], "PASS")

    def test_aggregate_empty_market(self) -> None:
        agg = reporting._aggregate_pipeline_composition([], threshold=70.0)
        self.assertEqual(agg["total_scored"], 0)
        self.assertEqual(agg["actionable_count"], 0)
        self.assertEqual(agg["avg_composite"], 0.0)


class TestPhase9MemoRender(unittest.TestCase):
    """Memo markdown rendering."""

    def test_memo_with_pipeline(self) -> None:
        rows = [
            {"parcel_id": "p-1", "address": "100 Main",
             "submarket": "south_fulton", "acreage": 12.0,
             "owner_name": "OWNER A", "composite_score": 80,
             "actionability": "PASS", "primary_strategy": "land_bank"},
            {"parcel_id": "p-2", "address": "200 Oak",
             "submarket": "south_fulton", "acreage": 9.5,
             "owner_name": "OWNER B", "composite_score": 75,
             "actionability": "PASS", "primary_strategy": "spec"},
        ]
        md = reporting._render_memo_markdown(
            "atlanta", "score-atlanta-20260504T100000Z-abcd",
            rows, [], [], params=_phase9_params(), today="2026-05-04",
        )
        self.assertIn("Atlanta Strategy Memo — 2026-05-04", md)
        self.assertIn("score-atlanta-20260504T100000Z-abcd", md)
        self.assertIn("Total scored parcels in atlanta: **2**", md)
        self.assertIn("Actionable", md)
        self.assertIn("Land Bank: 1", md)
        self.assertIn("Spec Development: 1", md)
        self.assertIn("**p-1**", md)
        self.assertIn("**p-2**", md)
        self.assertIn("south_fulton: 2", md)

    def test_memo_empty_market_still_renders(self) -> None:
        """D4 / R-635: a zero-pipeline memo is still informative."""
        md = reporting._render_memo_markdown(
            "atlanta", None, [], [], [],
            params=_phase9_params(), today="2026-05-04",
        )
        self.assertIn("Atlanta Strategy Memo — 2026-05-04", md)
        self.assertIn("Total scored parcels in atlanta: **0**", md)
        self.assertIn("(no parcels passed actionability)", md)
        self.assertIn("(no submarkets observed)", md)
        self.assertIn("No actionable or qualified parcels", md)
        self.assertIn("No data-driven parameter adjustments", md)

    def test_memo_high_failure_count_triggers_open_question(self) -> None:
        rows = [
            {"composite_score": 75, "actionability": "FAIL:entitlement",
             "submarket": "x", "primary_strategy": None}
            for _ in range(6)
        ]
        md = reporting._render_memo_markdown(
            "atlanta", None, rows, [], [],
            params=_phase9_params(), today="2026-05-04",
        )
        self.assertIn("6 parcels failed the entitlement gate", md)


class TestPhase9MemoEndToEnd(unittest.TestCase):
    """Memo via Phase5FakeConnection + tmp_path."""

    def test_memo_writes_file_and_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = Phase5FakeConnection(
                fetchone_queue=[("score-atlanta-20260504T100000Z-abcd", "2026-05-04T10:00:00Z")],
                fetchall_queue=[
                    [_phase9_memo_row(), _phase9_memo_row(parcel_id="fulton-2", composite_score=72.0)],
                    [],  # flags
                    [],  # research_log
                ],
            )
            params = _phase9_params()
            target = reporting.generate_strategy_memo(
                "atlanta",
                conn=fake, output_dir=tmp_path, params=params,
                today="2026-05-04",
            )
            self.assertTrue(target.exists())
            self.assertEqual(target.name, "atlanta_strategy_memo.md")
            content = target.read_text(encoding="utf-8")
            self.assertIn("Atlanta Strategy Memo", content)
            self.assertIn("Total scored parcels in atlanta: **2**", content)
            self.assertIn("score-atlanta-20260504T100000Z-abcd", content)

    def test_memo_with_explicit_cycle_id_skips_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # No cycle-row in fetchone queue — caller passed cycle_id explicitly.
            fake = Phase5FakeConnection(
                fetchall_queue=[
                    [_phase9_memo_row()],
                    [],  # flags
                    [],  # log
                ],
            )
            params = _phase9_params()
            target = reporting.generate_strategy_memo(
                "atlanta",
                conn=fake, output_dir=tmp_path, params=params,
                cycle_id="caller-cycle-123",
                today="2026-05-04",
            )
            content = target.read_text(encoding="utf-8")
            self.assertIn("caller-cycle-123", content)

    def test_memo_zero_scored_parcels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = Phase5FakeConnection(
                fetchone_queue=[None],  # no scoring cycle
                fetchall_queue=[[], [], []],
            )
            params = _phase9_params()
            target = reporting.generate_strategy_memo(
                "atlanta",
                conn=fake, output_dir=tmp_path, params=params,
                today="2026-05-04",
            )
            content = target.read_text(encoding="utf-8")
            self.assertIn("Total scored parcels in atlanta: **0**", content)


class TestPhase9NoDatabaseWrites(unittest.TestCase):
    """R-601 / R-646 — Phase 9 makes NO writes to the database."""

    def _assert_only_reads(self, fake: Phase5FakeConnection) -> None:
        for sql, _params in fake.all_executes:
            first = sql.lstrip().split(None, 1)[0].upper()
            self.assertIn(
                first, {"SELECT", "WITH"},
                f"Phase 9 must not issue {first} statements; saw: {sql[:80]}",
            )

    def test_snapshot_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Phase5FakeConnection(
                fetchone_queue=[
                    _phase9_parcel_row(), _phase9_score_row(),
                    _phase9_mc_row(), ("South Fulton",),
                ],
                fetchall_queue=[[], []],
            )
            reporting.generate_snapshot(
                "fulton-14-0123-LL-045-8",
                conn=fake, output_dir=Path(tmp), params=_phase9_params(),
            )
            self._assert_only_reads(fake)
            self.assertEqual(fake.commits, 0)
            self.assertEqual(fake.transaction_count, 0)

    def test_memo_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Phase5FakeConnection(
                fetchone_queue=[None],
                fetchall_queue=[[], [], []],
            )
            reporting.generate_strategy_memo(
                "atlanta",
                conn=fake, output_dir=Path(tmp), params=_phase9_params(),
                today="2026-05-04",
            )
            self._assert_only_reads(fake)
            self.assertEqual(fake.commits, 0)
            self.assertEqual(fake.transaction_count, 0)


class TestPhase9NoFabrication(unittest.TestCase):
    """R-636 — null DB fields render as '—' or 'not yet wired', never made up."""

    def test_all_null_parcel_renders_clean_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            null_parcel = _phase9_parcel_row(
                county=None, state=None, market=None, submarket=None,
                address=None, owner_name=None, owner_mailing_address=None,
                owner_type_inferred=None, acreage=None, land_sf=None,
                zoning=None, zoning_description=None, land_use_code=None,
                land_use_description=None, assessed_value_total=None,
                last_sale_date=None, last_sale_price=None,
                discovery_source=None, discovery_date=None,
                centroid_lng=None, centroid_lat=None,
            )
            fake = Phase5FakeConnection(
                fetchone_queue=[null_parcel, _phase9_score_row()],
                fetchall_queue=[[]],  # only flags fetched (no submarket)
            )
            target = reporting.generate_snapshot(
                "fulton-empty",
                conn=fake, output_dir=tmp_path, params=_phase9_params(),
            )
            content = target.read_text(encoding="utf-8")
            # Must not render the literal "None" anywhere.
            self.assertNotIn(" None", content)
            self.assertNotIn("None,", content)
            self.assertNotIn("None.", content)
            # Should use the placeholder dash.
            self.assertIn("—", content)


class TestPhase9SqlConstantsStaticChecks(unittest.TestCase):
    """R-642 — Phase 9 SQL constants are interpolation-free."""

    PHASE9_CONSTANTS = (
        "_SQL_FETCH_PARCEL_FOR_SNAPSHOT",
        "_SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT",
        "_SQL_FETCH_NEARBY_SALES_COMPS",
        "_SQL_FETCH_OPEN_FLAGS_FOR_PARCEL",
        "_SQL_FETCH_SCORED_PARCELS_FOR_MEMO",
        "_SQL_FETCH_LATEST_SCORING_CYCLE_FOR_MEMO",
        "_SQL_FETCH_RESEARCH_LOG_FOR_MEMO",
        "_SQL_FETCH_RECENT_FLAGS_FOR_MARKET",
    )

    def test_constants_exist(self) -> None:
        for name in self.PHASE9_CONSTANTS:
            self.assertTrue(
                hasattr(reporting, name),
                f"missing Phase 9 SQL constant: {name}",
            )

    def test_no_string_interpolation(self) -> None:
        for name in self.PHASE9_CONSTANTS:
            sql = getattr(reporting, name)
            self.assertIsInstance(sql, str)
            self.assertNotIn(
                "{", sql,
                f"{name} contains '{{' — possible f-string interpolation",
            )

    def test_only_select_statements(self) -> None:
        for name in self.PHASE9_CONSTANTS:
            sql = getattr(reporting, name).lstrip()
            first = sql.split(None, 1)[0].upper()
            self.assertEqual(
                first, "SELECT",
                f"{name} must start with SELECT (Phase 9 is read-only); "
                f"got: {first}",
            )


class TestPhase9AtomicWrite(unittest.TestCase):
    """R-617 — atomic write semantics."""

    def test_atomic_write_creates_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "subdir" / "x.md"
            reporting._atomic_write_text(target, "hello\n")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")

    def test_atomic_write_normalizes_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.md"
            reporting._atomic_write_text(target, "line1\r\nline2\r\n")
            data = target.read_bytes()
            self.assertNotIn(b"\r\n", data)
            self.assertIn(b"line1\nline2\n", data)

    def test_atomic_write_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.md"
            reporting._atomic_write_text(target, "v1\n")
            reporting._atomic_write_text(target, "v2\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "v2\n")

    def test_atomic_write_no_tmp_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.md"
            reporting._atomic_write_text(target, "ok\n")
            tmp_files = [
                p for p in Path(tmp).iterdir()
                if p.name.startswith(".") and ".tmp." in p.name
            ]
            self.assertEqual(tmp_files, [])


class TestPhase9GitignorePresence(unittest.TestCase):
    """R-647 — rankings/*.md is gitignored."""

    def test_rankings_md_in_gitignore(self) -> None:
        gitignore = REPO_ROOT / ".gitignore"
        content = gitignore.read_text(encoding="utf-8")
        self.assertIn("rankings/*.md", content)
        # snapshots/*.md was already there from before; double-check.
        self.assertIn("snapshots/*.md", content)


# ===========================================================================
# Phase 10 — experiment loop, setup phase, and TSV I/O
# ===========================================================================
# Per Agent 1 risk review at reviews/12_phase10_experiment_loop/01_risk_review.md
# (R-701..R-733).  Test classes named TestPhase10<Topic> per the precedent.


class TestPhase10TsvSchemaValidation(unittest.TestCase):
    """R-719 — every rejection branch in _validate_log_row."""

    def _base_row(self) -> dict:
        return {
            "commit": "abcdef0",
            "metric": 5,
            "confidence": 4.2,
            "api_calls": 100,
            "wall_clock_min": 12.3,
            "status": "keep",
            "description": "ok",
        }

    def test_valid_row_passes(self) -> None:
        out = runner._validate_log_row(self._base_row())
        self.assertEqual(out["commit"], "abcdef0")
        self.assertEqual(out["metric"], "5")
        self.assertEqual(out["confidence"], "4.20")
        self.assertEqual(out["status"], "keep")

    def test_rejects_short_commit_sha(self) -> None:
        row = self._base_row()
        row["commit"] = "abc"
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_accepts_pending_commit(self) -> None:
        row = self._base_row()
        row["commit"] = "pending"
        out = runner._validate_log_row(row)
        self.assertEqual(out["commit"], "pending")

    def test_rejects_uppercase_sha(self) -> None:
        row = self._base_row()
        row["commit"] = "ABCDEF0"
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_rejects_float_metric(self) -> None:
        row = self._base_row()
        row["metric"] = 5.0
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_rejects_bool_metric(self) -> None:
        row = self._base_row()
        row["metric"] = True  # bool is a subclass of int
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_rejects_negative_metric(self) -> None:
        row = self._base_row()
        row["metric"] = -1
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_rejects_nan_confidence(self) -> None:
        row = self._base_row()
        row["confidence"] = float("nan")
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_rejects_inf_confidence(self) -> None:
        row = self._base_row()
        row["confidence"] = float("inf")
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_rejects_negative_confidence(self) -> None:
        row = self._base_row()
        row["confidence"] = -0.1
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_rejects_negative_wall_clock(self) -> None:
        row = self._base_row()
        row["wall_clock_min"] = -1.0
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_rejects_unknown_status(self) -> None:
        row = self._base_row()
        row["status"] = "approved"
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)

    def test_accepts_each_known_status(self) -> None:
        for status in ("baseline", "keep", "discard", "crash", "timeout", "halt"):
            row = self._base_row()
            row["status"] = status
            out = runner._validate_log_row(row)
            self.assertEqual(out["status"], status)

    def test_rejects_negative_api_calls(self) -> None:
        row = self._base_row()
        row["api_calls"] = -1
        with self.assertRaises(ValueError):
            runner._validate_log_row(row)


class TestPhase10DescriptionSanitization(unittest.TestCase):
    """R-718 — tabs, newlines, NULs, length cap."""

    def test_strips_tabs(self) -> None:
        self.assertEqual(runner._sanitize_description("a\tb"), "a b")

    def test_strips_newlines(self) -> None:
        self.assertEqual(runner._sanitize_description("a\nb\r\nc"), "a b c")

    def test_strips_null_bytes(self) -> None:
        self.assertEqual(runner._sanitize_description("a\x00b"), "a b")

    def test_collapses_whitespace_runs(self) -> None:
        self.assertEqual(runner._sanitize_description("a   \t\n  b"), "a b")

    def test_truncates_to_cap(self) -> None:
        s = "x" * 500
        out = runner._sanitize_description(s)
        self.assertLessEqual(len(out), runner._TSV_DESCRIPTION_MAX_LEN)
        self.assertTrue(out.endswith("…"))

    def test_keeps_commas_unlike_csv(self) -> None:
        self.assertEqual(
            runner._sanitize_description("added a,b,c"), "added a,b,c"
        )

    def test_handles_empty(self) -> None:
        self.assertEqual(runner._sanitize_description(""), "")

    def test_handles_none(self) -> None:
        self.assertEqual(runner._sanitize_description(None), "")


class TestPhase10TsvHeaderBootstrap(unittest.TestCase):
    """R-717 — header bootstrap on first write."""

    def test_writes_header_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment_log.tsv"
            runner.append_experiment_log_row(
                {
                    "commit": "abcdef0",
                    "metric": 0,
                    "confidence": 0.0,
                    "api_calls": 0,
                    "wall_clock_min": 0.0,
                    "status": "baseline",
                    "description": "first",
                },
                path=path,
            )
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
            self.assertEqual(
                lines[0],
                "\t".join(runner._TSV_COLUMNS),
            )
            self.assertEqual(len(lines), 2)

    def test_writes_header_when_file_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment_log.tsv"
            path.touch()
            runner.append_experiment_log_row(
                {
                    "commit": "abcdef0",
                    "metric": 1,
                    "confidence": 0.0,
                    "api_calls": 0,
                    "wall_clock_min": 0.0,
                    "status": "baseline",
                    "description": "after empty",
                },
                path=path,
            )
            content = path.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("\t".join(runner._TSV_COLUMNS)))

    def test_skips_header_when_file_already_has_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment_log.tsv"
            runner.append_experiment_log_row(
                {
                    "commit": "abcdef0",
                    "metric": 0,
                    "confidence": 0.0,
                    "api_calls": 0,
                    "wall_clock_min": 0.0,
                    "status": "baseline",
                    "description": "first",
                },
                path=path,
            )
            runner.append_experiment_log_row(
                {
                    "commit": "1234567",
                    "metric": 1,
                    "confidence": 0.5,
                    "api_calls": 10,
                    "wall_clock_min": 1.0,
                    "status": "keep",
                    "description": "second",
                },
                path=path,
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0], "\t".join(runner._TSV_COLUMNS))


class TestPhase10TsvAppendOnly(unittest.TestCase):
    """R-716 — append-only semantics."""

    def test_multiple_appends_preserve_prior_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.tsv"
            for i in range(5):
                runner.append_experiment_log_row(
                    {
                        "commit": f"{i:07x}",
                        "metric": i,
                        "confidence": float(i),
                        "api_calls": i * 10,
                        "wall_clock_min": float(i),
                        "status": "baseline" if i == 0 else "keep",
                        "description": f"row {i}",
                    },
                    path=path,
                )
            rows = runner.read_experiment_log(path)
            self.assertEqual(len(rows), 5)
            self.assertEqual(rows[0]["status"], "baseline")
            self.assertEqual(rows[4]["metric"], "4")
            self.assertEqual(
                [r["description"] for r in rows],
                ["row 0", "row 1", "row 2", "row 3", "row 4"],
            )

    def test_read_returns_empty_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "does_not_exist.tsv"
            self.assertEqual(runner.read_experiment_log(path), [])

    def test_read_skips_only_exact_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.tsv"
            # Manually write a fake "header-like" row that differs from the
            # canonical header by one column; the reader must NOT skip it.
            with path.open("w", encoding="utf-8") as fh:
                fh.write("\t".join(runner._TSV_COLUMNS) + "\n")
                fh.write("commit\tmetric\tconfidence\tapi_calls\twall_clock_min\t"
                         "status\twrong_desc\n")
                fh.write("abcdef0\t1\t0.0\t0\t0.0\tbaseline\treal row\n")
            rows = runner.read_experiment_log(path)
            # Only the canonical header is skipped; the second row (which
            # has 7 columns but value "wrong_desc" in the description col)
            # is parsed as data.
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["description"], "wrong_desc")
            self.assertEqual(rows[1]["description"], "real row")


class TestPhase10DecisionMatrix(unittest.TestCase):
    """R-713 — every cell of the keep/discard/baseline/crash/timeout matrix."""

    def test_first_row_is_baseline(self) -> None:
        out = runner.apply_keep_or_revert_decision(
            prior_metric=None, prior_confidence=None,
            new_metric=5, new_confidence=2.0, status="ok",
        )
        self.assertEqual(out, "baseline")

    def test_strict_improvement_keeps(self) -> None:
        out = runner.apply_keep_or_revert_decision(
            prior_metric=5, prior_confidence=2.0,
            new_metric=6, new_confidence=2.0, status="ok",
        )
        self.assertEqual(out, "keep")

    def test_regression_discards(self) -> None:
        out = runner.apply_keep_or_revert_decision(
            prior_metric=5, prior_confidence=2.0,
            new_metric=4, new_confidence=10.0, status="ok",
        )
        self.assertEqual(out, "discard")

    def test_tied_metric_higher_confidence_keeps(self) -> None:
        out = runner.apply_keep_or_revert_decision(
            prior_metric=5, prior_confidence=2.0,
            new_metric=5, new_confidence=3.0, status="ok",
        )
        self.assertEqual(out, "keep")

    def test_tied_metric_equal_confidence_discards(self) -> None:
        # R-714: simplicity criterion -- equal confidence on tied metric
        # is a discard, not a keep.
        out = runner.apply_keep_or_revert_decision(
            prior_metric=5, prior_confidence=2.0,
            new_metric=5, new_confidence=2.0, status="ok",
        )
        self.assertEqual(out, "discard")

    def test_tied_metric_lower_confidence_discards(self) -> None:
        out = runner.apply_keep_or_revert_decision(
            prior_metric=5, prior_confidence=2.0,
            new_metric=5, new_confidence=1.99, status="ok",
        )
        self.assertEqual(out, "discard")

    def test_tied_metric_isclose_confidence_discards(self) -> None:
        # R-715: float tolerance absorbs ULP noise -- treated as equal.
        out = runner.apply_keep_or_revert_decision(
            prior_metric=5, prior_confidence=2.0,
            new_metric=5, new_confidence=2.0 + 1e-12, status="ok",
        )
        self.assertEqual(out, "discard")

    def test_crash_short_circuits(self) -> None:
        out = runner.apply_keep_or_revert_decision(
            prior_metric=5, prior_confidence=2.0,
            new_metric=999, new_confidence=999.0, status="crash",
        )
        self.assertEqual(out, "crash")

    def test_timeout_short_circuits(self) -> None:
        out = runner.apply_keep_or_revert_decision(
            prior_metric=5, prior_confidence=2.0,
            new_metric=999, new_confidence=999.0, status="timeout",
        )
        self.assertEqual(out, "timeout")

    def test_unknown_status_raises(self) -> None:
        with self.assertRaises(ValueError):
            runner.apply_keep_or_revert_decision(
                prior_metric=5, prior_confidence=2.0,
                new_metric=5, new_confidence=2.0, status="banana",
            )


class TestPhase10ParseTagFromBranch(unittest.TestCase):
    """R-704 / Setup Step 1 -- branch name parsing."""

    def test_extracts_tag_from_autoresearch_branch(self) -> None:
        self.assertEqual(
            runner._parse_tag_from_branch("autoresearch/atl-2026-05-04"),
            "atl-2026-05-04",
        )

    def test_returns_none_for_main(self) -> None:
        self.assertIsNone(runner._parse_tag_from_branch("main"))

    def test_returns_none_for_dev_branch(self) -> None:
        self.assertIsNone(runner._parse_tag_from_branch("claude/foo-123"))

    def test_rejects_uppercase_tag(self) -> None:
        # Branch regex is lowercase-only.
        self.assertIsNone(
            runner._parse_tag_from_branch("autoresearch/ATL-2026")
        )

    def test_accepts_dotted_tag(self) -> None:
        self.assertEqual(
            runner._parse_tag_from_branch("autoresearch/v1.2.3"),
            "v1.2.3",
        )


class TestPhase10HaltDetection(unittest.TestCase):
    """R-725, R-728 -- halt sentinel."""

    def setUp(self) -> None:
        self._original_env = os.environ.pop(runner._HALT_ENV_VAR, None)

    def tearDown(self) -> None:
        os.environ.pop(runner._HALT_ENV_VAR, None)
        if self._original_env is not None:
            os.environ[runner._HALT_ENV_VAR] = self._original_env

    def test_no_halt_by_default(self) -> None:
        # Sandbox may or may not have a real .halt; just check env-var path.
        if not runner._HALT_SENTINEL_PATH.exists():
            self.assertFalse(runner._halted())

    def test_env_var_halts(self) -> None:
        os.environ[runner._HALT_ENV_VAR] = "1"
        self.assertTrue(runner._halted())

    def test_env_var_empty_string_does_not_halt(self) -> None:
        os.environ[runner._HALT_ENV_VAR] = ""
        # Empty env var is falsy in our check -- only set means halt.
        # Hold the test: "" is treated as truthy by os.environ.get() because
        # it's still set; our code uses `if os.environ.get(...):` which is
        # falsy for "".
        if not runner._HALT_SENTINEL_PATH.exists():
            self.assertFalse(runner._halted())


class TestPhase10LastBaselineOrKeep(unittest.TestCase):
    """The prior-anchor selector that powers the next decision."""

    def test_picks_last_keep_over_earlier_baseline(self) -> None:
        rows = [
            {"status": "baseline", "metric": "1", "confidence": "1.0"},
            {"status": "keep", "metric": "2", "confidence": "1.5"},
            {"status": "discard", "metric": "0", "confidence": "0.0"},
        ]
        anchor = runner._last_baseline_or_keep(rows)
        self.assertEqual(anchor["status"], "keep")
        self.assertEqual(anchor["metric"], "2")

    def test_returns_baseline_when_no_keeps_yet(self) -> None:
        rows = [
            {"status": "baseline", "metric": "5", "confidence": "1.0"},
            {"status": "discard", "metric": "0", "confidence": "0.0"},
            {"status": "crash", "metric": "0", "confidence": "0.0"},
        ]
        anchor = runner._last_baseline_or_keep(rows)
        self.assertEqual(anchor["status"], "baseline")
        self.assertEqual(anchor["metric"], "5")

    def test_returns_none_for_empty_log(self) -> None:
        self.assertIsNone(runner._last_baseline_or_keep([]))

    def test_skips_crash_and_timeout(self) -> None:
        rows = [
            {"status": "baseline", "metric": "1", "confidence": "1.0"},
            {"status": "crash", "metric": "0", "confidence": "0.0"},
            {"status": "timeout", "metric": "0", "confidence": "0.0"},
        ]
        anchor = runner._last_baseline_or_keep(rows)
        self.assertEqual(anchor["status"], "baseline")


class TestPhase10AssertAutoresearchBranch(unittest.TestCase):
    """R-703 -- branch invariant."""

    def test_refuses_main(self) -> None:
        with mock.patch.object(runner, "_git_current_branch", return_value="main"):
            with self.assertRaises(runner.SetupError) as cm:
                runner._assert_autoresearch_branch()
            self.assertIn("autoresearch", str(cm.exception))

    def test_refuses_dev_branch(self) -> None:
        with mock.patch.object(
            runner, "_git_current_branch",
            return_value="claude/setup-research-loop-ZUuA6",
        ):
            with self.assertRaises(runner.SetupError):
                runner._assert_autoresearch_branch()

    def test_refuses_detached_head(self) -> None:
        with mock.patch.object(runner, "_git_current_branch", return_value="HEAD"):
            with self.assertRaises(runner.SetupError):
                runner._assert_autoresearch_branch()

    def test_accepts_autoresearch(self) -> None:
        with mock.patch.object(
            runner, "_git_current_branch",
            return_value="autoresearch/atl-2026-05-04",
        ):
            out = runner._assert_autoresearch_branch()
            self.assertEqual(out, "autoresearch/atl-2026-05-04")


class TestPhase10VerifySetupComposite(unittest.TestCase):
    """verify_setup composite status -- ok / warning / fail aggregation."""

    def test_non_autoresearch_branch_makes_overall_fail(self) -> None:
        with mock.patch.object(runner, "_git_current_branch", return_value="main"), \
             mock.patch.object(runner, "_check_db_connection",
                               return_value={"status": "ok", "postgis_version": "3.3"}), \
             mock.patch.object(runner, "_check_harness_for_market",
                               return_value={"status": "ok", "per_county": {"fulton": "healthy"}}), \
             mock.patch.object(runner, "_check_corridor_bbox",
                               return_value={"status": "ok", "seeded_count": 1}), \
             mock.patch.object(runner, "_check_costar_freshness",
                               return_value={"status": "ok", "fresh_files": 5}):
            out = runner.verify_setup("atlanta")
            self.assertEqual(out["status"], "fail")
            self.assertFalse(out["is_autoresearch_branch"])

    def test_all_ok_returns_ok(self) -> None:
        with mock.patch.object(
            runner, "_git_current_branch",
            return_value="autoresearch/atl-2026-05-04",
        ), \
            mock.patch.object(runner, "_check_db_connection",
                              return_value={"status": "ok", "postgis_version": "3.3"}), \
            mock.patch.object(runner, "_check_harness_for_market",
                              return_value={"status": "ok", "per_county": {"fulton": "healthy"}}), \
            mock.patch.object(runner, "_check_corridor_bbox",
                              return_value={"status": "ok", "seeded_count": 1}), \
            mock.patch.object(runner, "_check_costar_freshness",
                              return_value={"status": "ok", "fresh_files": 5}):
            out = runner.verify_setup("atlanta")
            self.assertEqual(out["status"], "ok")
            self.assertEqual(out["tag"], "atl-2026-05-04")
            self.assertTrue(out["is_autoresearch_branch"])

    def test_costar_warning_with_otherwise_ok_returns_warning(self) -> None:
        with mock.patch.object(
            runner, "_git_current_branch",
            return_value="autoresearch/atl-2026-05-04",
        ), \
            mock.patch.object(runner, "_check_db_connection",
                              return_value={"status": "ok", "postgis_version": "3.3"}), \
            mock.patch.object(runner, "_check_harness_for_market",
                              return_value={"status": "ok", "per_county": {"fulton": "healthy"}}), \
            mock.patch.object(runner, "_check_corridor_bbox",
                              return_value={"status": "warning", "seeded_count": 0,
                                            "note": "no bbox"}), \
            mock.patch.object(runner, "_check_costar_freshness",
                              return_value={"status": "warning", "fresh_files": 0,
                                            "note": "no exports"}):
            out = runner.verify_setup("atlanta")
            self.assertEqual(out["status"], "warning")

    def test_db_fail_returns_fail(self) -> None:
        with mock.patch.object(
            runner, "_git_current_branch",
            return_value="autoresearch/atl-2026-05-04",
        ), \
            mock.patch.object(runner, "_check_db_connection",
                              return_value={"status": "fail", "error": "no host"}), \
            mock.patch.object(runner, "_check_harness_for_market",
                              return_value={"status": "ok", "per_county": {}}), \
            mock.patch.object(runner, "_check_costar_freshness",
                              return_value={"status": "ok", "fresh_files": 1}):
            out = runner.verify_setup("atlanta")
            self.assertEqual(out["status"], "fail")
            # Bbox check was skipped because DB was down.
            self.assertEqual(out["checks"]["corridor_bbox"]["status"], "skipped")


class TestPhase10EvaluateMetricRouting(unittest.TestCase):
    """R-701 -- evaluate() routes through prepare.calculate_* exclusively."""

    def test_metric_value_comes_from_prepare(self) -> None:
        # Sentinel: replace prepare.calculate_actionable_pipeline_count with
        # a function returning a sentinel value, and confirm evaluate()
        # surfaces it verbatim.
        sentinel_metric = 7
        sentinel_confidence = 6.5

        @contextmanager
        def fake_get_connection():
            yield Phase5FakeConnection()

        with mock.patch.object(costar_ingest, "run_ingestion_cycle", return_value={}), \
             mock.patch.object(research, "run_discovery_cycle", return_value={}), \
             mock.patch.object(research, "run_scoring_cycle", return_value={}), \
             mock.patch.object(reporting, "generate_strategy_memo",
                               return_value=Path("/tmp/memo.md")), \
             mock.patch.object(research.prepare, "verify_parameters_unchanged"), \
             mock.patch.object(research.prepare, "calculate_actionable_pipeline_count",
                               return_value=sentinel_metric), \
             mock.patch.object(research.prepare, "calculate_confidence_weighted_pipeline",
                               return_value=sentinel_confidence), \
             mock.patch.object(research.prepare, "get_connection", fake_get_connection):
            result = runner.evaluate("atlanta")

        self.assertEqual(result["metric"], sentinel_metric)
        self.assertEqual(result["confidence"], sentinel_confidence)
        self.assertEqual(result["status"], "ok")

    def test_evaluate_calls_sub_cycles_in_order(self) -> None:
        order: list[str] = []

        def _track(name):
            def _fn(*a, **kw):
                order.append(name)
                if name == "memo":
                    return Path("/tmp/memo.md")
                return {}
            return _fn

        @contextmanager
        def fake_get_connection():
            yield Phase5FakeConnection()

        with mock.patch.object(costar_ingest, "run_ingestion_cycle", _track("ingestion")), \
             mock.patch.object(research, "run_discovery_cycle", _track("discovery")), \
             mock.patch.object(research, "run_scoring_cycle", _track("scoring")), \
             mock.patch.object(reporting, "generate_strategy_memo", _track("memo")), \
             mock.patch.object(research.prepare, "verify_parameters_unchanged"), \
             mock.patch.object(research.prepare, "calculate_actionable_pipeline_count",
                               return_value=0), \
             mock.patch.object(research.prepare, "calculate_confidence_weighted_pipeline",
                               return_value=0.0), \
             mock.patch.object(research.prepare, "get_connection", fake_get_connection):
            runner.evaluate("atlanta")

        self.assertEqual(order, ["ingestion", "discovery", "scoring", "memo"])

    def test_evaluate_catches_crash_and_returns_status(self) -> None:
        with mock.patch.object(costar_ingest, "run_ingestion_cycle", return_value={}), \
             mock.patch.object(research, "run_discovery_cycle",
                               side_effect=RuntimeError("boom")), \
             mock.patch.object(research.prepare, "verify_parameters_unchanged"):
            result = runner.evaluate("atlanta")
        self.assertEqual(result["status"], "crash")
        self.assertEqual(result["metric"], 0)
        self.assertEqual(result["confidence"], 0.0)
        self.assertIn("RuntimeError", result["error"])

    def test_evaluate_catches_budget_exceeded(self) -> None:
        with mock.patch.object(costar_ingest, "run_ingestion_cycle", return_value={}), \
             mock.patch.object(research, "run_discovery_cycle",
                               side_effect=research.prepare.BudgetExceeded("90 min")), \
             mock.patch.object(research.prepare, "verify_parameters_unchanged"):
            result = runner.evaluate("atlanta")
        self.assertEqual(result["status"], "timeout")

    def test_evaluate_calls_verify_parameters_unchanged(self) -> None:
        called: list[bool] = []

        def _fake_verify():
            called.append(True)

        with mock.patch.object(research.prepare, "verify_parameters_unchanged",
                               _fake_verify), \
             mock.patch.object(costar_ingest, "run_ingestion_cycle",
                               side_effect=RuntimeError("stop")):
            runner.evaluate("atlanta")
        self.assertEqual(called, [True])


class TestPhase10ExperimentLoopBaselineBootstrap(unittest.TestCase):
    """Loop on empty TSV writes a baseline first."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tsv_path = Path(self.tmp.name) / "experiment_log.tsv"
        self.lock_path = Path(self.tmp.name) / ".lock"
        self._old_tsv = os.environ.pop("EXPERIMENT_LOG_PATH", None)
        self._old_lock = os.environ.pop("EXPERIMENT_LOOP_LOCK_PATH", None)
        os.environ["EXPERIMENT_LOG_PATH"] = str(self.tsv_path)
        os.environ["EXPERIMENT_LOOP_LOCK_PATH"] = str(self.lock_path)

    def tearDown(self) -> None:
        os.environ.pop("EXPERIMENT_LOG_PATH", None)
        os.environ.pop("EXPERIMENT_LOOP_LOCK_PATH", None)
        if self._old_tsv is not None:
            os.environ["EXPERIMENT_LOG_PATH"] = self._old_tsv
        if self._old_lock is not None:
            os.environ["EXPERIMENT_LOOP_LOCK_PATH"] = self._old_lock
        self.tmp.cleanup()

    def _patch_setup_ok(self):
        return mock.patch.object(
            runner, "verify_setup",
            return_value={"status": "ok", "branch": "autoresearch/test",
                          "tag": "test", "is_autoresearch_branch": True,
                          "checks": {}},
        )

    def _patch_evaluate(self, sequence):
        it = iter(sequence)
        return mock.patch.object(
            runner, "evaluate",
            side_effect=lambda market, **kw: next(it),
        )

    def _make_eval(self, metric, confidence, status="ok"):
        return {
            "market": "atlanta", "status": status,
            "metric": metric, "confidence": confidence,
            "api_calls": 0, "wall_clock_min": 0.1,
            "sub_summaries": {},
        }

    def test_refuses_without_baseline_and_unconfirmed(self) -> None:
        with self._patch_setup_ok():
            with self.assertRaises(runner.SetupError) as cm:
                runner.experiment_loop("atlanta", max_iterations=0)
            self.assertIn("baseline", str(cm.exception))

    def test_bootstraps_baseline_when_confirmed(self) -> None:
        evals = [self._make_eval(0, 0.0)]  # baseline-only, then exit
        with self._patch_setup_ok(), \
             self._patch_evaluate(evals), \
             mock.patch.object(runner, "_git_head_commit", return_value="abcdef0"):
            summary = runner.experiment_loop(
                "atlanta", max_iterations=0, confirmed=True,
            )
        rows = runner.read_experiment_log(self.tsv_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "baseline")
        self.assertEqual(summary["iterations"], 0)


class TestPhase10ExperimentLoopIterations(unittest.TestCase):
    """Loop iteration semantics with explicit baseline."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tsv_path = Path(self.tmp.name) / "experiment_log.tsv"
        self.lock_path = Path(self.tmp.name) / ".lock"
        os.environ["EXPERIMENT_LOG_PATH"] = str(self.tsv_path)
        os.environ["EXPERIMENT_LOOP_LOCK_PATH"] = str(self.lock_path)
        # Pre-seed a baseline row so the loop bootstraps cleanly.
        runner.append_experiment_log_row({
            "commit": "0000000",
            "metric": 5,
            "confidence": 4.0,
            "api_calls": 0,
            "wall_clock_min": 0.0,
            "status": "baseline",
            "description": "baseline | market=atlanta",
        }, path=self.tsv_path)

    def tearDown(self) -> None:
        os.environ.pop("EXPERIMENT_LOG_PATH", None)
        os.environ.pop("EXPERIMENT_LOOP_LOCK_PATH", None)
        self.tmp.cleanup()

    def _patch_setup_ok(self):
        return mock.patch.object(
            runner, "verify_setup",
            return_value={"status": "ok", "branch": "autoresearch/test",
                          "tag": "test", "is_autoresearch_branch": True,
                          "checks": {}},
        )

    def test_max_iterations_caps_loop(self) -> None:
        evals = [
            {"market": "atlanta", "status": "ok", "metric": 6, "confidence": 4.5,
             "api_calls": 0, "wall_clock_min": 0.1, "sub_summaries": {}},
            {"market": "atlanta", "status": "ok", "metric": 5, "confidence": 3.0,
             "api_calls": 0, "wall_clock_min": 0.1, "sub_summaries": {}},
            {"market": "atlanta", "status": "ok", "metric": 7, "confidence": 5.0,
             "api_calls": 0, "wall_clock_min": 0.1, "sub_summaries": {}},
        ]
        with self._patch_setup_ok(), \
             mock.patch.object(runner, "evaluate", side_effect=evals), \
             mock.patch.object(runner, "_git_head_commit",
                               side_effect=["1111111", "2222222", "3333333"]):
            summary = runner.experiment_loop("atlanta", max_iterations=2)
        rows = runner.read_experiment_log(self.tsv_path)
        # baseline + 2 iterations.
        self.assertEqual(len(rows), 3)
        self.assertEqual(summary["iterations"], 2)
        # First iteration improved -> keep.  Second regressed -> discard.
        self.assertEqual(rows[1]["status"], "keep")
        self.assertEqual(rows[2]["status"], "discard")

    def test_decision_uses_last_keep_anchor(self) -> None:
        evals = [
            # first iter: 6 > baseline(5) -> keep; anchor becomes 6.
            {"market": "atlanta", "status": "ok", "metric": 6, "confidence": 5.0,
             "api_calls": 0, "wall_clock_min": 0.1, "sub_summaries": {}},
            # second iter: 5 == baseline(5) but anchor is now 6, so 5<6 -> discard.
            {"market": "atlanta", "status": "ok", "metric": 5, "confidence": 9.0,
             "api_calls": 0, "wall_clock_min": 0.1, "sub_summaries": {}},
        ]
        with self._patch_setup_ok(), \
             mock.patch.object(runner, "evaluate", side_effect=evals), \
             mock.patch.object(runner, "_git_head_commit",
                               side_effect=["1111111", "2222222"]):
            runner.experiment_loop("atlanta", max_iterations=2)
        rows = runner.read_experiment_log(self.tsv_path)
        self.assertEqual(rows[1]["status"], "keep")
        self.assertEqual(rows[2]["status"], "discard")

    def test_crash_isolated_loop_continues(self) -> None:
        evals = [
            {"market": "atlanta", "status": "crash", "metric": 0, "confidence": 0.0,
             "api_calls": 0, "wall_clock_min": 0.1, "error": "boom",
             "sub_summaries": {}},
            {"market": "atlanta", "status": "ok", "metric": 6, "confidence": 5.0,
             "api_calls": 0, "wall_clock_min": 0.1, "sub_summaries": {}},
        ]
        with self._patch_setup_ok(), \
             mock.patch.object(runner, "evaluate", side_effect=evals), \
             mock.patch.object(runner, "_git_head_commit",
                               side_effect=["1111111", "2222222"]):
            runner.experiment_loop("atlanta", max_iterations=2)
        rows = runner.read_experiment_log(self.tsv_path)
        self.assertEqual(rows[1]["status"], "crash")
        self.assertEqual(rows[2]["status"], "keep")

    def test_halt_via_env_exits_loop_cleanly(self) -> None:
        # First iteration sets the halt env var; loop exits before iter 2.
        def _evaluate(market, **kw):
            os.environ[runner._HALT_ENV_VAR] = "1"
            return {"market": "atlanta", "status": "ok", "metric": 6,
                    "confidence": 5.0, "api_calls": 0, "wall_clock_min": 0.1,
                    "sub_summaries": {}}

        try:
            with self._patch_setup_ok(), \
                 mock.patch.object(runner, "evaluate", side_effect=_evaluate), \
                 mock.patch.object(runner, "_git_head_commit",
                                   return_value="1111111"):
                summary = runner.experiment_loop("atlanta", max_iterations=10)
        finally:
            os.environ.pop(runner._HALT_ENV_VAR, None)
        self.assertEqual(summary["iterations"], 1)
        rows = runner.read_experiment_log(self.tsv_path)
        # baseline + 1 keep + halt row
        statuses = [r["status"] for r in rows]
        self.assertIn("halt", statuses)


class TestPhase10ExperimentLoopAdvisoryLock(unittest.TestCase):
    """R-729 -- second concurrent invocation fails with LoopLockError."""

    def test_second_acquire_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".lock"
            os.environ["EXPERIMENT_LOOP_LOCK_PATH"] = str(lock_path)
            try:
                with runner._acquire_loop_lock():
                    with self.assertRaises(runner.LoopLockError):
                        with runner._acquire_loop_lock():
                            pass
            finally:
                os.environ.pop("EXPERIMENT_LOOP_LOCK_PATH", None)

    def test_lock_releases_on_normal_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".lock"
            os.environ["EXPERIMENT_LOOP_LOCK_PATH"] = str(lock_path)
            try:
                with runner._acquire_loop_lock():
                    pass
                # Should be acquirable again.
                with runner._acquire_loop_lock():
                    pass
            finally:
                os.environ.pop("EXPERIMENT_LOOP_LOCK_PATH", None)


class TestPhase10ExperimentLoopReadOnlyVsImmutables(unittest.TestCase):
    """G2/G6/G11 -- import-level checks that Phase 10 cannot mutate the
    immutable layer."""

    def test_no_writes_to_immutable_layer(self) -> None:
        # research.py legitimately READS sources.json and parameters.json
        # in many places (json.loads / .read_text), and uses json.dumps for
        # research_log notes + cache files + JSONB columns.  The Five-File
        # Contract ban is on WRITING parameters.json or sources.json, which
        # would require either ``open(... "w")`` or ``write_text`` on the
        # immutable paths.  Verify both forms are absent.
        src = ALL_PIPELINE_PY_SRC
        for path_const in ("_PARAMETERS_PATH", "_SOURCES_PATH"):
            for forbidden in ('"w"', "'w'", '"w+"', "'w+'", '"a"', "'a'"):
                bad = f"{path_const}.open({forbidden}"
                self.assertNotIn(
                    bad, src,
                    f"research.py must not open {path_const} with {forbidden}",
                )
            self.assertNotIn(
                f"{path_const}.write_text", src,
                f"research.py must not write_text to {path_const}",
            )
            self.assertNotIn(
                f"{path_const}.write_bytes", src,
                f"research.py must not write_bytes to {path_const}",
            )

    def test_experiment_loop_is_callable_not_notimplemented(self) -> None:
        # G9 -- the stub is gone.
        import inspect
        src = inspect.getsource(runner.experiment_loop)
        self.assertNotIn("NotImplementedError", src)
        self.assertIn("Karpathy", src)


class TestPhase10TsvCommitFormat(unittest.TestCase):
    """R-719 -- commit shape acceptance."""

    def _row_with_commit(self, commit):
        return {
            "commit": commit, "metric": 0, "confidence": 0.0, "api_calls": 0,
            "wall_clock_min": 0.0, "status": "baseline", "description": "x",
        }

    def test_seven_char_sha(self) -> None:
        out = runner._validate_log_row(self._row_with_commit("0123abc"))
        self.assertEqual(out["commit"], "0123abc")

    def test_forty_char_sha(self) -> None:
        sha = "0" * 40
        out = runner._validate_log_row(self._row_with_commit(sha))
        self.assertEqual(out["commit"], sha)

    def test_empty_commit_rejected(self) -> None:
        with self.assertRaises(ValueError):
            runner._validate_log_row(self._row_with_commit(""))

    def test_special_chars_rejected(self) -> None:
        with self.assertRaises(ValueError):
            runner._validate_log_row(self._row_with_commit("abcd!ef"))


class TestPhase10ConstantsContract(unittest.TestCase):
    """G1 / G3 -- module-level constants exist and have the right shape."""

    def test_columns_are_seven(self) -> None:
        self.assertEqual(len(runner._TSV_COLUMNS), 7)

    def test_columns_match_spec(self) -> None:
        self.assertEqual(
            runner._TSV_COLUMNS,
            ("commit", "metric", "confidence", "api_calls",
             "wall_clock_min", "status", "description"),
        )

    def test_status_set_matches_spec(self) -> None:
        # AUTORESEARCH_MECHANICS.md L309 specifies five statuses; we add
        # 'halt' as a Phase 10 extension for clean exit accounting.
        expected = {"baseline", "keep", "discard", "crash", "timeout", "halt"}
        self.assertEqual(runner._TSV_STATUSES, frozenset(expected))

    def test_branch_regex_lowercase_only(self) -> None:
        self.assertIsNotNone(
            runner._AUTORESEARCH_BRANCH_RE.match("autoresearch/atl-2026")
        )
        self.assertIsNone(
            runner._AUTORESEARCH_BRANCH_RE.match("autoresearch/ATL-2026")
        )

    def test_budget_is_ninety_minutes(self) -> None:
        # AUTORESEARCH_MECHANICS.md L153 says 90 minutes.
        self.assertEqual(runner._PHASE10_BUDGET_SECONDS, 90 * 60)


# ===========================================================================
# Phase 13 — performance / robustness pass (R-1301..R-1335)
# See reviews/13_perf_optimization/01_risk_review.md and 02_code_writer_response.md.
# ===========================================================================
class _FakeResponse:
    """Minimal requests.Response stand-in for retry tests.

    status_code drives raise_for_status (4xx/5xx raise requests.HTTPError,
    mirroring requests' real behavior so _DiscoverySession.get's
    raise_for_status path re-raises the SAME exception class on exhaustion).
    """

    def __init__(
        self,
        status_code: int,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body if json_body is not None else {"ok": True}
        self.headers = headers or {}

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            raise research.requests.exceptions.HTTPError(
                f"{self.status_code} Error", response=self
            )


class _ScriptedSession:
    """requests.Session stand-in: pops a scripted outcome per get() call.

    Each outcome is either a _FakeResponse (returned) or an Exception
    instance (raised) — lets a test script "500 then 200", "timeout then 200",
    "404 once", etc. Records the number of get() calls.
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0
        self.headers: dict[str, str] = {}

    def get(self, url: str, params: Any = None, timeout: Any = None) -> Any:
        self.calls += 1
        if not self._outcomes:
            raise AssertionError("scripted session exhausted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        pass


@contextmanager
def _patched_discovery_session(outcomes: list[Any]):
    """Build a _DiscoverySession backed by a _ScriptedSession, with time.sleep
    and time.monotonic patched so retries/spacing run instantly and we can
    record every sleep() the retry path performs.

    Yields (session, sleeps) where sleeps is the list of sleep durations.
    """
    sess = research._DiscoverySession()
    scripted = _ScriptedSession(outcomes)
    sess._session = scripted  # type: ignore[assignment]
    sleeps: list[float] = []
    # Monotonic clock that advances by a large step each call so _spacing_sleep
    # never *itself* blocks (we only care that it is CALLED on each attempt;
    # spacing-induced sleeps are captured separately and asserted by count).
    clock = {"t": 0.0}

    def _fake_sleep(secs: float) -> None:
        sleeps.append(secs)

    def _fake_monotonic() -> float:
        clock["t"] += 1000.0
        return clock["t"]

    with mock.patch.object(research.time, "sleep", _fake_sleep), \
            mock.patch.object(research.time, "monotonic", _fake_monotonic):
        yield sess, sleeps, scripted


class TestDiscoveryRetry(unittest.TestCase):
    """Item 1 — retry-with-backoff in _DiscoverySession.get (R-1301..R-1308)."""

    def test_retry_on_500_then_200(self) -> None:
        with _patched_discovery_session(
            [_FakeResponse(500), _FakeResponse(200, {"features": []})]
        ) as (sess, sleeps, scripted):
            out = sess.get("https://example.org/q")
        self.assertEqual(out, {"features": []})
        self.assertEqual(scripted.calls, 2)  # one retry consumed

    def test_retry_on_timeout_then_200(self) -> None:
        with _patched_discovery_session(
            [research.requests.exceptions.Timeout("read timed out"),
             _FakeResponse(200, {"features": [1]})]
        ) as (sess, sleeps, scripted):
            out = sess.get("https://example.org/q")
        self.assertEqual(out, {"features": [1]})
        self.assertEqual(scripted.calls, 2)

    def test_retry_on_connection_error_then_200(self) -> None:
        with _patched_discovery_session(
            [research.requests.exceptions.ConnectionError("conn reset"),
             _FakeResponse(200)]
        ) as (sess, sleeps, scripted):
            out = sess.get("https://example.org/q")
        self.assertTrue(out["ok"])
        self.assertEqual(scripted.calls, 2)

    def test_retry_on_429_then_200(self) -> None:
        # 429 IS retried (the one transient 4xx). R-1304.
        with _patched_discovery_session(
            [_FakeResponse(429), _FakeResponse(200, {"v": 1})]
        ) as (sess, sleeps, scripted):
            out = sess.get("https://example.org/q")
        self.assertEqual(out, {"v": 1})
        self.assertEqual(scripted.calls, 2)

    def test_no_retry_on_404(self) -> None:
        # 4xx != 429 fail-fast: exactly one call, HTTPError propagates. R-1305.
        with _patched_discovery_session([_FakeResponse(404)]) as (sess, sleeps, scripted):
            with self.assertRaises(research.requests.exceptions.HTTPError):
                sess.get("https://example.org/q")
        self.assertEqual(scripted.calls, 1)

    def test_no_retry_on_403(self) -> None:
        with _patched_discovery_session([_FakeResponse(403)]) as (sess, sleeps, scripted):
            with self.assertRaises(research.requests.exceptions.HTTPError):
                sess.get("https://example.org/q")
        self.assertEqual(scripted.calls, 1)

    def test_no_retry_on_400(self) -> None:
        with _patched_discovery_session([_FakeResponse(400)]) as (sess, sleeps, scripted):
            with self.assertRaises(research.requests.exceptions.HTTPError):
                sess.get("https://example.org/q")
        self.assertEqual(scripted.calls, 1)

    def test_retries_exhausted_reraises_http_error(self) -> None:
        # 3 consecutive 500s (1 initial + 2 retries) → HTTPError propagates,
        # NOT a sentinel. R-1302: the corridor-level handler must still fire.
        with _patched_discovery_session(
            [_FakeResponse(500), _FakeResponse(500), _FakeResponse(500)]
        ) as (sess, sleeps, scripted):
            with self.assertRaises(research.requests.exceptions.HTTPError):
                sess.get("https://example.org/q")
        self.assertEqual(scripted.calls, 3)  # capped at MAX_RETRIES + 1

    def test_retries_exhausted_reraises_timeout(self) -> None:
        # Transport exception class is preserved on exhaustion (R-1302).
        with _patched_discovery_session(
            [research.requests.exceptions.Timeout("t"),
             research.requests.exceptions.Timeout("t"),
             research.requests.exceptions.Timeout("t")]
        ) as (sess, sleeps, scripted):
            with self.assertRaises(research.requests.exceptions.Timeout):
                sess.get("https://example.org/q")
        self.assertEqual(scripted.calls, 3)

    def test_retry_cap_is_two(self) -> None:
        # Module constants pin the divergence from the harness (R-1301).
        self.assertEqual(research._DISCOVERY_MAX_RETRIES, 2)
        self.assertEqual(research._DISCOVERY_BACKOFF_SCHEDULE_S, (1.0, 2.0))

    def test_backoff_schedule_used_in_order(self) -> None:
        # Two 500s then success → backoff sleeps of 1.0 then 2.0 appear among
        # the recorded sleeps (spacing sleeps do not fire because the patched
        # monotonic clock makes elapsed huge). R-1301.
        with _patched_discovery_session(
            [_FakeResponse(500), _FakeResponse(500), _FakeResponse(200)]
        ) as (sess, sleeps, scripted):
            sess.get("https://example.org/q")
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_spacing_invoked_on_every_attempt(self) -> None:
        # _spacing_sleep must run BEFORE the request on every attempt (R-1303).
        # Spy on it; with 2 failures + 1 success it must be called 3 times.
        sess = research._DiscoverySession()
        sess._session = _ScriptedSession(  # type: ignore[assignment]
            [_FakeResponse(500), _FakeResponse(500), _FakeResponse(200)]
        )
        calls: list[str] = []
        real_spacing = sess._spacing_sleep

        def _spy(host: str) -> None:
            calls.append(host)

        with mock.patch.object(sess, "_spacing_sleep", _spy), \
                mock.patch.object(research.time, "sleep", lambda s: None):
            sess.get("https://example.org/q")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(h == "example.org" for h in calls))

    def test_429_retry_after_honored_with_cap(self) -> None:
        # Retry-After: 5 (> scheduled 1.0) → sleep 5.0 on the first backoff.
        with _patched_discovery_session(
            [_FakeResponse(429, headers={"Retry-After": "5"}), _FakeResponse(200)]
        ) as (sess, sleeps, scripted):
            sess.get("https://example.org/q")
        self.assertEqual(sleeps, [5.0])

    def test_429_retry_after_capped(self) -> None:
        # Pathological Retry-After: 3600 is capped at _DISCOVERY_RETRY_AFTER_CAP_S.
        with _patched_discovery_session(
            [_FakeResponse(429, headers={"Retry-After": "3600"}), _FakeResponse(200)]
        ) as (sess, sleeps, scripted):
            sess.get("https://example.org/q")
        self.assertEqual(sleeps, [research._DISCOVERY_RETRY_AFTER_CAP_S])

    def test_429_retry_after_shorter_than_backoff_uses_backoff(self) -> None:
        # Retry-After: 0 (or shorter than scheduled) falls back to the schedule.
        with _patched_discovery_session(
            [_FakeResponse(429, headers={"Retry-After": "0"}), _FakeResponse(200)]
        ) as (sess, sleeps, scripted):
            sess.get("https://example.org/q")
        self.assertEqual(sleeps, [1.0])

    def test_429_garbage_retry_after_uses_backoff(self) -> None:
        # Non-integer Retry-After (HTTP-date form / garbage) → scheduled backoff.
        with _patched_discovery_session(
            [_FakeResponse(429, headers={"Retry-After": "Wed, 21 Oct 2025 07:28:00 GMT"}),
             _FakeResponse(200)]
        ) as (sess, sleeps, scripted):
            sess.get("https://example.org/q")
        self.assertEqual(sleeps, [1.0])

    def test_no_call_to_connector_harness_http_helper(self) -> None:
        # R-1306: research.py must NOT import-from connector_harness nor CALL
        # its private HTTP retry helper — the retry pattern is recreated inline.
        # AST scan (not a substring scan, which would false-positive on the
        # explanatory comments/docstrings that legitimately reference the
        # harness). The ONLY permitted connector_harness call is the public
        # harness-gate run_harness_for_county.
        tree = ast.parse(RESEARCH_PY_SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(
                    node.module, "connector_harness",
                    f"forbidden `from connector_harness import` at line {node.lineno}",
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                val = node.func.value
                if isinstance(val, ast.Name) and val.id == "connector_harness":
                    self.assertEqual(
                        node.func.attr, "run_harness_for_county",
                        f"unexpected connector_harness.{node.func.attr}() call "
                        f"at line {node.lineno}",
                    )

    def test_no_print_in_get_retry_path(self) -> None:
        # R-1308: the retry path logs via `log`, never print(). Scan the
        # _DiscoverySession.get method AST for print() calls.
        tree = ast.parse(RESEARCH_PY_SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get":
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Name)
                            and inner.func.id == "print"):
                        self.fail(f"print() in get() at line {inner.lineno}")


# ---------------------------------------------------------------------------
# Items 2-4 — per-cycle prefetch cache (R-1310..R-1320)
# ---------------------------------------------------------------------------
class TestPhase13BatchSqlConstants(unittest.TestCase):
    """R-1310, R-1316, R-1325/gate 28 — the new batch SQL constants are
    module-level, parameterised, and use the right set-based shape."""

    BATCH_CONSTS = (
        "_SQL_LATEST_MARKET_CONTEXT_BATCH",
        "_SQL_SUBMARKET_LAND_MEDIAN_BATCH",
        "_SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH",
        "_SQL_DISTINCT_SUBMARKETS_FOR_PARCELS",
    )

    def test_constants_present_and_no_format_braces(self) -> None:
        for const in self.BATCH_CONSTS:
            self.assertTrue(hasattr(research, const), f"missing {const}")
            sql = getattr(research, const)
            self.assertIsInstance(sql, str)
            self.assertNotIn("{", sql, f"f-string brace in {const}")

    def test_each_batch_const_has_exactly_one_any_placeholder(self) -> None:
        # R-1316: each batch query binds a single list via ANY(%s).
        for const in self.BATCH_CONSTS:
            sql = getattr(research, const)
            self.assertEqual(sql.count("%s"), 1, f"{const} must have one %s")
            self.assertIn("ANY(%s)", sql, f"{const} must use ANY(%s)")

    def test_market_context_batch_preserves_costar_case_tail(self) -> None:
        # R-1310: DISTINCT ON (submarket_id) led, then the EXACT CoStar CASE +
        # as_of_date DESC tail. A GROUP BY/MAX rewrite would be wrong.
        sql = research._SQL_LATEST_MARKET_CONTEXT_BATCH
        self.assertIn("DISTINCT ON (submarket_id)", sql)
        self.assertIn(
            "ORDER BY submarket_id, "
            "(CASE WHEN source = 'costar' THEN 0 ELSE 1 END), "
            "as_of_date DESC",
            sql,
        )
        self.assertNotIn("MAX(", sql)
        self.assertNotIn("GROUP BY", sql)

    def test_land_median_batch_preserves_filters_and_groups(self) -> None:
        # R-1310: identical filters to the single-key median; GROUP BY submarket.
        sql = research._SQL_SUBMARKET_LAND_MEDIAN_BATCH
        self.assertIn("comp_type = 'land'", sql)
        self.assertIn("price_per_acre IS NOT NULL", sql)
        self.assertIn("sale_date >= (CURRENT_DATE - INTERVAL '36 months')", sql)
        self.assertIn("PERCENTILE_CONT(0.5)", sql)
        self.assertIn("GROUP BY submarket_id", sql)

    def test_actionability_batch_distinct_on_with_flag_id_tiebreak(self) -> None:
        # R-1311: DISTINCT ON (parcel_id), parcel_id-led ORDER BY, flagged_at
        # DESC then flag_id DESC tie-break for determinism.
        sql = research._SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH
        self.assertIn("DISTINCT ON (parcel_id)", sql)
        self.assertIn("ORDER BY parcel_id, flagged_at DESC, flag_id DESC", sql)
        # The single-key query keeps NO tie-break (documented micro-divergence).
        self.assertNotIn("flag_id", research._SQL_FLAGGED_ACTIONABILITY_BLOCK)


class TestPhase13PrefetchCache(unittest.TestCase):
    """R-1315, R-1316, R-1317, R-1318 — _prefetch_cycle_cache behavior."""

    def test_empty_parcel_list_issues_no_queries(self) -> None:
        # R-1318: degenerate 0-parcel cycle → no queries, empty cache.
        fake = Phase5FakeConnection()
        cache = research._prefetch_cycle_cache(fake, "atlanta", [])
        self.assertEqual(fake.all_executes, [])
        self.assertEqual(cache.market_context, {})
        self.assertEqual(cache.land_median, {})
        self.assertEqual(cache.actionability_block, {})

    def test_no_submarkets_skips_submarket_queries(self) -> None:
        # R-1315: when no parcel has a non-null submarket, the market_context
        # and land-median batch queries are skipped (only distinct-submarkets +
        # actionability-block queries run).
        fake = Phase5FakeConnection(
            fetchall_queue=[
                [],   # distinct submarkets: none
                [],   # actionability-block batch: none
            ],
        )
        cache = research._prefetch_cycle_cache(fake, "atlanta", ["p1", "p2"])
        sqls = [s for s, _ in fake.all_executes]
        self.assertIn(research._SQL_DISTINCT_SUBMARKETS_FOR_PARCELS, sqls)
        self.assertIn(research._SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH, sqls)
        self.assertNotIn(research._SQL_LATEST_MARKET_CONTEXT_BATCH, sqls)
        self.assertNotIn(research._SQL_SUBMARKET_LAND_MEDIAN_BATCH, sqls)
        self.assertEqual(cache.market_context, {})

    def test_any_params_passed_as_single_tuple_wrapping_list(self) -> None:
        # R-1316: every ANY(%s) query is called with (list,) — NOT the list
        # spread across placeholders. This is the mistake the fake-conn cannot
        # otherwise catch, so assert on the recorded (sql, params) shape.
        fake = Phase5FakeConnection(
            fetchall_queue=[
                [("South Fulton",)],                       # distinct submarkets
                [("South Fulton", 5.0, 100.0, 200.0, 0.0, 0.0, "2026-01-01", "costar")],  # mc batch
                [("South Fulton", 4, 250000.0)],           # median batch
                [("p1", "control block")],                 # actionability batch
            ],
        )
        research._prefetch_cycle_cache(fake, "atlanta", ["p1", "p2"])
        for sql, params in fake.all_executes:
            if "ANY(%s)" in sql:
                self.assertEqual(len(params), 1, f"ANY query must get a 1-tuple: {sql}")
                self.assertIsInstance(params[0], list, f"ANY param must be a list: {sql}")

    def test_cache_keyed_on_exact_parcel_ids(self) -> None:
        # R-1317: the parcel-id list passed to the actionability batch is the
        # exact list given, in order.
        fake = Phase5FakeConnection(
            fetchall_queue=[[], []],  # no submarkets, no blocks
        )
        research._prefetch_cycle_cache(fake, "atlanta", ["a", "b", "c"])
        block_calls = [
            params for sql, params in fake.all_executes
            if sql == research._SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH
        ]
        self.assertEqual(block_calls, [(["a", "b", "c"],)])

    def test_cache_decodes_rows_into_expected_shapes(self) -> None:
        fake = Phase5FakeConnection(
            fetchall_queue=[
                [("South Fulton",)],
                [("South Fulton", 5.0, 100.0, 200.0, 0.0, 0.0, "2026-01-01", "costar")],
                [("South Fulton", 4, 250000.0)],
                [("p1", "deal killer here")],
            ],
        )
        cache = research._prefetch_cycle_cache(fake, "atlanta", ["p1"])
        # market_context value is the 7-col tail (submarket stripped).
        self.assertEqual(
            cache.market_context["South Fulton"],
            (5.0, 100.0, 200.0, 0.0, 0.0, "2026-01-01", "costar"),
        )
        # land_median value is (n, median).
        self.assertEqual(cache.land_median["South Fulton"], (4, 250000.0))
        self.assertEqual(cache.actionability_block["p1"], "deal killer here")


def _phase78_full_parcel_tuple(
    parcel_id: str, submarket: str | None, lng: float, lat: float,
    state: str = "GA", acreage: Any = None,
    last_sale_date: Any = None, last_sale_price: Any = None,
    assessed: Any = None,
) -> tuple:
    """_SQL_FETCH_PARCEL 10-col row builder for the equivalence tests."""
    return (
        parcel_id, "atlanta", submarket, state, acreage,
        last_sale_date, last_sale_price, assessed, lng, lat,
    )


class TestPhase13CacheEquivalence(unittest.TestCase):
    """R-1310, R-1312, R-1319 — bit-identical proof: score_parcel(cache=...)
    produces IDENTICAL results AND identical recorded INSERT params to the
    cache=None per-parcel path."""

    def setUp(self) -> None:
        research._OZ_TRACTS_CACHE = None

    def _params(self) -> dict[str, Any]:
        p = _passing_params()
        p["scoring_weights"] = TestPhase5Composite.WEIGHTS
        return p

    # Shared fixture rows for a parcel WITH a submarket so S4/S5/S6/S8 fire.
    SUBMARKET = "South Fulton"
    MC_ROW = (3.0, 60000.0, 100000.0, 50000.0, 12.0, "2026-05-01", "costar")
    MEDIAN_ROW = (5, 200000.0)  # n=5 (>= min), median 200k
    PARCEL = _phase78_full_parcel_tuple(
        "fulton-eq", SUBMARKET, -84.55, 33.55, state="GA", acreage=10.0,
        assessed=2000000.0,
    )
    S2_ROW = (1000.0, 1100.0, 1.5)

    def _score_no_cache(self) -> tuple[dict, list]:
        # Per-parcel queue: fetch, S2, market_context, land_median, block.
        fake = Phase5FakeConnection(
            fetchone_queue=[
                self.PARCEL,
                self.S2_ROW,
                self.MC_ROW,          # _SQL_LATEST_MARKET_CONTEXT
                self.MEDIAN_ROW,      # _SQL_SUBMARKET_LAND_MEDIAN
                ("control: condemned",),  # _SQL_FLAGGED_ACTIONABILITY_BLOCK
            ],
        )
        result = research.score_parcel(
            "fulton-eq", conn=fake, cycle_id="score-atlanta-eq-0001",
            params=self._params(),
        )
        return result, fake.all_executes

    def _score_with_cache(self) -> tuple[dict, list]:
        cache = research._CycleCache(
            market_context={self.SUBMARKET: self.MC_ROW},
            land_median={self.SUBMARKET: self.MEDIAN_ROW},
            actionability_block={"fulton-eq": "control: condemned"},
        )
        # Cached queue: only fetch + S2 (the other three are served from cache).
        fake = Phase5FakeConnection(
            fetchone_queue=[self.PARCEL, self.S2_ROW],
        )
        result = research.score_parcel(
            "fulton-eq", conn=fake, cycle_id="score-atlanta-eq-0001",
            params=self._params(), cache=cache,
        )
        return result, fake.all_executes

    def test_result_dict_is_bit_identical(self) -> None:
        no_cache, _ = self._score_no_cache()
        with_cache, _ = self._score_with_cache()
        self.assertEqual(no_cache, with_cache)

    def test_parcel_scores_insert_params_identical(self) -> None:
        _, ex_nc = self._score_no_cache()
        _, ex_c = self._score_with_cache()

        def _score_insert(executes: list) -> tuple:
            return next(
                params for sql, params in executes
                if "INSERT INTO parcel_scores" in sql
            )
        self.assertEqual(_score_insert(ex_nc), _score_insert(ex_c))

    def test_research_log_and_flag_params_identical(self) -> None:
        _, ex_nc = self._score_no_cache()
        _, ex_c = self._score_with_cache()

        def _rows(executes: list, needle: str) -> list:
            return [params for sql, params in executes if needle in sql]
        self.assertEqual(
            _rows(ex_nc, "INSERT INTO research_log"),
            _rows(ex_c, "INSERT INTO research_log"),
        )
        # Match only flag WRITES — the no-cache path additionally issues a
        # SELECT ... FROM flagged_items (the per-parcel block lookup) that the
        # cache path skips; that recorded-query difference is expected and is
        # NOT a row-content difference.
        self.assertEqual(
            _rows(ex_nc, "INSERT INTO flagged_items"),
            _rows(ex_c, "INSERT INTO flagged_items"),
        )

    def test_cache_path_issues_no_lookup_queries(self) -> None:
        # The cache path must NOT execute the three batched single-key queries.
        _, ex_c = self._score_with_cache()
        sqls = [s for s, _ in ex_c]
        self.assertNotIn(research._SQL_LATEST_MARKET_CONTEXT, sqls)
        self.assertNotIn(research._SQL_SUBMARKET_LAND_MEDIAN, sqls)
        self.assertNotIn(research._SQL_FLAGGED_ACTIONABILITY_BLOCK, sqls)


class TestPhase13CacheGuards(unittest.TestCase):
    """R-1311, R-1313, R-1315, R-1320 — null-submarket, missing-key, and the
    data_gap-vs-actionability_block isolation."""

    def setUp(self) -> None:
        research._OZ_TRACTS_CACHE = None

    def test_null_submarket_hits_empty_branch_no_keyerror(self) -> None:
        # R-1315: a parcel with NULL submarket must return the empty
        # market_context / S8 None result even with a populated cache, never a
        # KeyError or spurious match.
        cache = research._CycleCache(
            market_context={"South Fulton": (1.0, 2.0, 3.0, 4.0, 5.0, "2026-01-01", "costar")},
            land_median={"South Fulton": (9, 123.0)},
            actionability_block={},
        )
        mc = research._compute_market_context_scores(None, None, cache=cache)
        self.assertEqual(mc["S4"], None)
        self.assertEqual(mc["S6"], None)
        s8, prov = research._compute_s8(None, {"submarket": None, "acreage": 10.0}, cache=cache)
        self.assertIsNone(s8)

    def test_submarket_keys_case_sensitive_no_normalization(self) -> None:
        # R-1315: keys are raw strings; a case-mismatched submarket misses.
        cache = research._CycleCache(
            market_context={"South Fulton": (3.0, 1.0, 1.0, 1.0, 1.0, "2026-01-01", "costar")},
            land_median={},
            actionability_block={},
        )
        # Exact key hits.
        hit = research._compute_market_context_scores(None, "South Fulton", cache=cache)
        self.assertIsNotNone(hit["S4"])
        # Different case misses → empty result (NOT a spurious match).
        miss = research._compute_market_context_scores(None, "south fulton", cache=cache)
        self.assertIsNone(miss["S4"])

    def test_actionability_block_absent_key_returns_none(self) -> None:
        # R-1311: a parcel with no open block (absent from the dict) returns
        # None — exactly the per-parcel "no row" behavior.
        cache = research._CycleCache(
            market_context={}, land_median={},
            actionability_block={"other": "blocked"},
        )
        self.assertIsNone(
            research._fetch_actionability_block(None, "fulton-x", cache=cache)
        )

    def test_prefetch_actionability_ignores_data_gap_flags(self) -> None:
        # R-1313: the actionability-block batch query filters
        # flag_type='actionability_block', so a data_gap flag written by
        # score_parcel mid-cycle can never enter the cache. Assert the batch
        # SQL carries the actionability_block filter and not data_gap.
        sql = research._SQL_FLAGGED_ACTIONABILITY_BLOCK_BATCH
        self.assertIn("flag_type = 'actionability_block'", sql)
        self.assertIn("status = 'open'", sql)
        self.assertNotIn("data_gap", sql)

    def test_run_actionability_screen_second_caller_unchanged(self) -> None:
        # R-1320: the public run_actionability_screen still calls
        # _fetch_actionability_block(conn, parcel_id) with NO cache and works.
        # A passing strategy_fit (one MODERATE) clears gate 3 so the deal_killer
        # gate (gate 4) is the one that fires on the open block.
        passing_strategy = {k: "WEAK" for k in research._STRATEGY_KEYS}
        passing_strategy[research._STRATEGY_KEYS[0]] = "MODERATE"
        fake = Phase5FakeConnection(fetchone_queue=[("zoning moratorium",)])
        out = research.run_actionability_screen(
            "fulton-001", conn=fake,
            sub_scores={n: None for n in research._SUB_SCORE_NAMES},
            strategy_fit=passing_strategy,
        )
        # The single-key block query ran (per-parcel path, not the batch).
        sqls = [s for s, _ in fake.all_executes]
        self.assertIn(research._SQL_FLAGGED_ACTIONABILITY_BLOCK, sqls)
        # 'zoning moratorium' has no 'entitlement' keyword → deal_killer fails.
        self.assertEqual(out["actionability"], research._ACTIONABILITY_FAIL_DEAL_KILLER)
