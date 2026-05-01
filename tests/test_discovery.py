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
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

# Importing research is import-safe — it uses lazy DB connection. The
# psycopg dependency is required for module load.
import research

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "discovery"
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
        """Pipeline is H1 → H2 → H3-flag → H4-flag (R-24)."""
        ids = [f.__name__ for f in research._HARD_FILTERS]
        self.assertEqual(ids, ["_h1_filter", "_h2_filter", "_h3_flag", "_h4_flag"])

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
        # Each parcel got two flag rows (H3, H4).
        flag_rows = [s for s, _ in fake.all_executes if "flagged_items" in s]
        # 4 parcels x 2 flags = 8 minimum (multipolygon flag may add more).
        self.assertGreaterEqual(len(flag_rows), 8)


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
