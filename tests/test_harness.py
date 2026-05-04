"""Offline tests for connector_harness. No live network. Stdlib unittest.

Covers Agent 1's S1 (HIGH) risks:
  - R-01/R-02: harness module imports cleanly with DATABASE_URL unset
  - R-03: PII redaction is strict-by-default; failsafe assertion fires on residual
  - R-04: ArcGIS HTTP 200 + error-envelope JSON treated as fail, not pass
  - R-05: --output path-traversal rejected

Plus selected S2 (MEDIUM):
  - R-08: pagination check uses orderByFields
  - R-09: owner sanity passes ALL-CAPS legitimate names; fails redaction tokens
  - R-13: test bbox/acreage NOT overridable from CLI/env
  - R-18: log helper strips sensitive query params
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the harness imports cleanly even when DATABASE_URL is missing (R-01).
os.environ.pop("DATABASE_URL", None)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import connector_harness as ch  # noqa: E402


class TestImportSafety(unittest.TestCase):
    """R-01/R-02: module imports without DATABASE_URL or any DB activity."""

    def test_no_prepare_or_psycopg_imports(self):
        # Verify the import boundary of connector_harness in a CLEAN process —
        # checking sys.modules in this process is brittle because any other
        # test that imports research/prepare (e.g. test_discovery, test_prepare)
        # leaves prepare/psycopg in sys.modules regardless of what
        # connector_harness itself imports.
        probe = textwrap.dedent(
            """
            import sys
            sys.path.insert(0, %r)
            import connector_harness  # noqa: F401
            assert "prepare" not in sys.modules, "connector_harness imports prepare"
            assert "psycopg" not in sys.modules, "connector_harness imports psycopg"
            """ % str(REPO_ROOT)
        )
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(result.returncode, 0,
                         msg=f"stdout={result.stdout!r} stderr={result.stderr!r}")

    def test_module_has_no_db_globals(self):
        for name in ("DATABASE_URL", "engine", "_pool", "session_factory"):
            self.assertFalse(hasattr(ch, name),
                             f"unexpected DB-flavored attribute: {name}")


class TestArcGISErrorEnvelope(unittest.TestCase):
    """R-04: HTTP 200 with {"error": ...} body must NOT be treated as success."""

    def _resp(self, body):
        m = MagicMock()
        m.status_code = 200
        m.headers = {"content-type": "application/json"}
        m.json.return_value = body
        return m

    def test_error_envelope_returns_error_status(self):
        resp = self._resp({"error": {"code": 400, "message": "Invalid query"}})
        status, payload = ch._parse_arcgis_response(resp)
        self.assertEqual(status, "error")
        self.assertEqual(payload, {"code": 400, "message": "Invalid query"})

    def test_normal_response_returns_ok(self):
        resp = self._resp({"features": [], "spatialReference": {"wkid": 4326}})
        status, payload = ch._parse_arcgis_response(resp)
        self.assertEqual(status, "ok")
        self.assertIn("features", payload)

    def test_non_json_returns_invalid(self):
        m = MagicMock()
        m.status_code = 200
        m.headers = {"content-type": "text/html"}
        m.json.side_effect = ValueError("no json")
        status, payload = ch._parse_arcgis_response(m)
        self.assertEqual(status, "invalid")


class TestRedaction(unittest.TestCase):
    """R-03: PII redaction strict-by-default with regex failsafe."""

    FIELD_MAP = {
        "parcel_id": "ParcelID",
        "owner_name": "Owner",
        "owner_mailing_address": "OwnerAddr1",
        "site_address": "Address",
        "acreage": "LandAcres",
    }

    def test_owner_field_replaced_with_redacted(self):
        feat = {"attributes": {"Owner": "Smith Family Trust",
                               "OwnerAddr1": "123 Main St, Atlanta GA 30303",
                               "ParcelID": "07-1234", "LandAcres": 14.7}}
        out = ch._redact_feature(feat, self.FIELD_MAP)
        self.assertEqual(out["attributes"]["Owner"], "[REDACTED]")
        self.assertEqual(out["attributes"]["OwnerAddr1"], "[REDACTED]")
        # Non-PII fields preserved.
        self.assertEqual(out["attributes"]["ParcelID"], "07-1234")
        self.assertEqual(out["attributes"]["LandAcres"], 14.7)

    def test_failsafe_fires_on_residual_name(self):
        # Simulate an unmapped field that contains a real name.
        bad_feat = {"attributes": {"ExtraField": "Jane Doe", "ParcelID": "07-9999"}}
        sanitized, warnings = ch._failsafe_check([bad_feat])
        self.assertEqual(sanitized[0]["attributes"]["ExtraField"], "[REDACTION_FAILSAFE]")
        self.assertTrue(any("ExtraField" in w for w in warnings))

    def test_failsafe_passes_clean_features(self):
        clean = {"attributes": {"Owner": "[REDACTED]", "LandAcres": 14.7}}
        sanitized, warnings = ch._failsafe_check([clean])
        self.assertEqual(sanitized[0]["attributes"]["Owner"], "[REDACTED]")
        self.assertEqual(warnings, [])

    def test_write_report_assertion_fails_on_residual_name(self):
        # _write_report does a final pre-write assertion (R-03 backstop).
        bad_report = {
            "county": "fulton", "market": "atlanta",
            "timestamp": "2026-04-30T10:00:00Z",
            "overall_health": "healthy", "checks": {},
            "sample_features": [{"attributes": {"X": "Bob Jones"}}],
            "warnings": [], "errors": [],
        }
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError) as cm:
                ch._write_report(bad_report, reports_dir=Path(td))
            self.assertIn("redaction failsafe", str(cm.exception))


class TestOwnerSanity(unittest.TestCase):
    """R-09: ALL-CAPS legitimate names pass; redaction-token strings fail."""

    def _features(self, owner_values):
        return [{"attributes": {"Owner": v}} for v in owner_values]

    def test_legit_all_caps_passes(self):
        feats = self._features(["SMITH JOHN H", "JONES MARY ANN", "BROWN FAMILY TRUST"])
        result = ch.check_owner_data_sanity(feats, "Owner")
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.details["redacted_count"], 0)

    def test_redaction_token_fails(self):
        feats = self._features(["[REDACTED]", "PROTECTED PURSUANT TO DANIELSLAW"])
        result = ch.check_owner_data_sanity(feats, "Owner")
        self.assertEqual(result.status, "fail")
        self.assertGreater(result.details["redacted_count"], 0)


class TestPathTraversalGuard(unittest.TestCase):
    """R-05: --output rejects traversal and non-.md."""

    def test_rejects_dotdot(self):
        with self.assertRaises(ValueError):
            ch._validate_output_path("../../etc/foo.md")

    def test_rejects_non_md(self):
        with self.assertRaises(ValueError):
            ch._validate_output_path("report.txt")

    def test_accepts_clean(self):
        p = ch._validate_output_path("harness_reports/summary.md")
        self.assertTrue(str(p).endswith("summary.md"))


class TestSensitiveQueryStrip(unittest.TestCase):
    """R-18: log-safe URL strips token, key, secret query params."""

    def test_strips_token(self):
        out = ch._strip_sensitive_query_params(
            "https://example.com/api?token=abc123&q=hello"
        )
        self.assertIn("token=***", out)
        self.assertIn("q=hello", out)
        self.assertNotIn("abc123", out)

    def test_passthrough_when_no_secrets(self):
        out = ch._strip_sensitive_query_params("https://example.com/api?f=pjson")
        self.assertEqual(out, "https://example.com/api?f=pjson")


class TestQueryParamsHonorRegistry(unittest.TestCase):
    """R-13: bbox/acreage are NOT overridable from CLI; come from connector only."""

    def test_query_params_use_connector_bbox(self):
        c = ch.Connector(
            county="fulton", state="GA", market="atlanta", access="arcgis_rest",
            service_url="https://x", parcel_layer_id=11,
            field_mapping={"parcel_id": "P", "acreage": "A"},
            test_bbox={"xmin": -84.6, "ymin": 33.5, "xmax": -84.5, "ymax": 33.6},
            test_acreage={"min": 5, "max": 50},
            expected_bbox=None, parcel_id_field="P", owner_field="O",
            fallback_portal=None,
        )
        params = ch._build_known_good_query_params(c)
        self.assertIn("geometry", params)
        self.assertEqual(params["inSR"], 4326)
        self.assertEqual(params["outSR"], 4326)
        # Acreage filter encoded in WHERE.
        self.assertIn(">=", params["where"])
        self.assertIn("<=", params["where"])


class TestPaginationOrderBy(unittest.TestCase):
    """R-08: pagination check must include orderByFields."""

    def test_pagination_includes_orderby(self):
        c = ch.Connector(
            county="fulton", state="GA", market="atlanta", access="arcgis_rest",
            service_url="https://x", parcel_layer_id=11,
            field_mapping={"parcel_id": "P", "acreage": "A"},
            test_bbox={"xmin": -84.6, "ymin": 33.5, "xmax": -84.5, "ymax": 33.6},
            test_acreage={"min": 5, "max": 50},
            expected_bbox=None, parcel_id_field="P", owner_field=None,
            fallback_portal=None,
        )
        captured = {}

        def fake_arcgis(session, url, params=None, **kw):
            captured["params"] = dict(params or {})
            return ("ok", {"features": [{}] if params.get("resultRecordCount", 0) >= 1 else []},
                    200, 0.01)

        with patch.object(ch, "_arcgis_get", side_effect=fake_arcgis), \
             patch.object(ch, "_build_session", return_value=MagicMock()):
            ch.check_pagination(c, ch._build_session())
        self.assertIn("orderByFields", captured["params"])
        self.assertEqual(captured["params"]["orderByFields"], "P")


class TestFieldPopulation(unittest.TestCase):
    """Sanity: 0 when all-null, 1.0 when all-set."""

    def test_zero_when_all_null(self):
        feats = [{"attributes": {"Owner": None, "ParcelID": "1"}} for _ in range(5)]
        out = ch.check_field_population(feats, {"owner_name": "Owner", "parcel_id": "ParcelID"})
        self.assertEqual(out.details["rates"]["Owner"], 0.0)
        self.assertEqual(out.details["rates"]["ParcelID"], 1.0)

    def test_one_when_all_set(self):
        feats = [{"attributes": {"Owner": "X", "ParcelID": "1"}} for _ in range(5)]
        out = ch.check_field_population(feats, {"owner_name": "Owner", "parcel_id": "ParcelID"})
        self.assertEqual(out.details["rates"]["Owner"], 1.0)


class TestAFOnlyDispatch(unittest.TestCase):
    """R-20: ai_fallback_only connector yields n_a stub, not crash."""

    def test_ai_fallback_only_stub(self):
        c = ch.Connector(
            county="spalding", state="GA", market="atlanta", access="ai_fallback_only",
            service_url=None, parcel_layer_id=None, field_mapping={},
            test_bbox=None, test_acreage=None, expected_bbox=None,
            parcel_id_field=None, owner_field=None,
            fallback_portal="https://qpublic.example/spalding",
        )
        report = ch._run_all_checks(c, quick=False)
        self.assertEqual(report["overall_health"], "n_a")


class TestAddressParsingPhase2Fixes(unittest.TestCase):
    """Phase 2 fix-forward — Fix A: site addresses use street-only heuristic;
    mailing addresses concat OwnerAddr1 + OwnerAddr2."""

    FIELD_MAP_FULTON = {
        "site_address": "Address",
        "owner_mailing_address": "OwnerAddr1",
        "owner_mailing_address_2": "OwnerAddr2",
    }

    def test_site_address_numbered_street_passes(self):
        feats = [{"attributes": {"Address": "0 CAMPBELLTON FAIRBURN RD"}}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.details["site_address_parse_rate"], 1.0)

    def test_site_address_unnumbered_named_road_passes(self):
        # Some Fulton parcels have just "Campbellton Fairburn Rd" with no number.
        feats = [{"attributes": {"Address": "CAMPBELLTON FAIRBURN RD"}}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.details["site_address_parse_rate"], 1.0)

    def test_site_address_garbage_fails(self):
        feats = [{"attributes": {"Address": "XYZ123"}}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.details["site_address_parse_rate"], 0.0)

    def test_mailing_address_concat_two_fields_parses(self):
        # OwnerAddr1 = street; OwnerAddr2 = "City, State ZIP" — must be combined.
        feats = [{"attributes": {
            "OwnerAddr1": "PO BOX 445",
            "OwnerAddr2": "SARASOTA FL 34230",
        }}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.details["mailing_address_parse_rate"], 1.0)

    def test_mailing_address_owneraddr1_alone_fails_without_concat(self):
        # OwnerAddr1 alone has no state/ZIP — would have parsed at 0% before
        # the fix; with the fix, parse rate is 0% UNLESS OwnerAddr2 is also
        # present in the field_mapping AND the feature carries it.
        feats = [{"attributes": {"OwnerAddr1": "123 MAIN ST"}}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        # OwnerAddr2 is in the field_mapping but absent from the feature -> the
        # combined string falls back to OwnerAddr1 alone, which has no state.
        self.assertEqual(result.details["mailing_address_parse_rate"], 0.0)

    def test_mailing_address_combined_with_state_passes(self):
        feats = [{"attributes": {
            "OwnerAddr1": "123 MAIN ST",
            "OwnerAddr2": "ATLANTA GA 30303",
        }}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.details["mailing_address_parse_rate"], 1.0)

    def test_overall_passes_when_both_branches_clean(self):
        feats = [{"attributes": {
            "Address": "123 INDUSTRIAL BLVD",
            "OwnerAddr1": "PO BOX 445",
            "OwnerAddr2": "SARASOTA FL 34230",
        }}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.status, "pass")

    def test_mailing_attn_prefix_passes(self):
        # ATTN: prefix used to fail because the regex required the street
        # number at the start. Now `_STREET_NUMBER_PAT.search(addr)` finds it
        # anywhere in the string.
        feats = [{"attributes": {
            "OwnerAddr1": "ATTN: BOB JONES",
            "OwnerAddr2": "123 MAIN ST ATLANTA GA 30303",
        }}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.details["mailing_address_parse_rate"], 1.0)

    def test_mailing_co_prefix_passes(self):
        feats = [{"attributes": {
            "OwnerAddr1": "C/O JANE SMITH",
            "OwnerAddr2": "456 OAK AVE NEW YORK NY 10001",
        }}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.details["mailing_address_parse_rate"], 1.0)

    def test_mailing_comma_separated_state_zip_passes(self):
        # "ATLANTA, GA, 30303" with comma after state — common variant.
        feats = [{"attributes": {
            "OwnerAddr1": "123 MAIN ST",
            "OwnerAddr2": "ATLANTA, GA, 30303",
        }}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.details["mailing_address_parse_rate"], 1.0)

    def test_mailing_no_state_still_fails(self):
        # Backstop: if there's genuinely no state code, the parser correctly
        # rejects the address even with the looser rules.
        feats = [{"attributes": {
            "OwnerAddr1": "123 MAIN ST",
            "OwnerAddr2": "SOMEWHERE 99999",
        }}] * 5
        result = ch.check_address_parsing(feats, self.FIELD_MAP_FULTON)
        self.assertEqual(result.details["mailing_address_parse_rate"], 0.0)


class TestOptionalFieldsExclusion(unittest.TestCase):
    """Phase 2 fix-forward — Fix B: optional_fields are reported but excluded
    from low_population_fields so their 0% doesn't fail the check."""

    FIELD_MAP = {
        "parcel_id": "ParcelID",
        "owner_name": "Owner",
        "subdivision": "Subdiv",
    }

    def _features_with_subdiv_null(self, n=10):
        return [{"attributes": {"ParcelID": f"07-{i}", "Owner": f"X{i}",
                                "Subdiv": None}} for i in range(n)]

    def test_subdiv_null_fails_without_optional_marker(self):
        feats = self._features_with_subdiv_null()
        result = ch.check_field_population(feats, self.FIELD_MAP)
        self.assertEqual(result.status, "fail")
        self.assertIn("Subdiv", result.details["low_population_fields"])

    def test_subdiv_null_passes_with_optional_marker(self):
        feats = self._features_with_subdiv_null()
        result = ch.check_field_population(feats, self.FIELD_MAP,
                                           optional_fields=("Subdiv",))
        self.assertEqual(result.status, "pass")
        self.assertNotIn("Subdiv", result.details["low_population_fields"])
        # Rate is still reported — humans can see Subdiv at 0.0.
        self.assertEqual(result.details["rates"]["Subdiv"], 0.0)
        self.assertIn("Subdiv", result.details["optional_fields"])

    def test_other_low_fields_still_fail_even_when_subdiv_optional(self):
        # Owner field at 0% should still fail; only Subdiv is excluded.
        feats = [{"attributes": {"ParcelID": f"07-{i}", "Owner": None,
                                 "Subdiv": None}} for i in range(10)]
        result = ch.check_field_population(feats, self.FIELD_MAP,
                                           optional_fields=("Subdiv",))
        self.assertEqual(result.status, "fail")
        self.assertIn("Owner", result.details["low_population_fields"])
        self.assertNotIn("Subdiv", result.details["low_population_fields"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
