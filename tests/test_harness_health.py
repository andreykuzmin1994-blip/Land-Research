"""Offline tests for the harness track: health labels, failure classes,
extra-field redaction, --verbose, and the harness-fulton CI verdict.

Risk review (harness track, Tier 2):
  - C-1: a connector is 'healthy' only when service_alive, layer_schema and
    known_good_query all passed. A connector that is not configured reports
    n_a with failure_class 'config' and exit code 1, never 'healthy'.
  - C-2 / H-2: every failed check carries failure_class transport|logic; the
    report is 'transport' only when every failed check is transport
    (connection error other than TLS, timeout, HTTP 5xx after retries).
  - H-3: connector_registry.json redaction_extra_fields is honoured in report
    sample features (SR-8), on top of the logical-name defaults.
  - H-4: --verbose logs request metadata only, never attribute values.
  - H-1 / M-4 / M-5: the verbatim run blocks of
    .github/workflows/harness-fulton.yml, run under `bash -e` against
    synthetic reports, give the documented pass / warn / fail verdicts.
  - M-1: research._harness_gate skips (and does not cache) a county whose
    connector is not configured.

Hermetic (SR-6): no network, no DATABASE_URL, nothing written into the repo
(_write_report / _write_dashboard are replaced and sleeps are no-ops).
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the harness imports cleanly even when DATABASE_URL is missing (R-01).
os.environ.pop("DATABASE_URL", None)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

import connector_harness as ch  # noqa: E402

WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "harness-fulton.yml"
HARNESS_STEP = "Run harness against Fulton (quick mode)"
CLASSIFY_STEP = "Distinguish transport vs logic failure"

# The real writer, kept for the failsafe test (setUp replaces ch._write_report).
_REAL_WRITE_REPORT = ch._write_report

ALL_CHECKS = (
    "service_alive", "layer_schema", "field_mapping", "known_good_query",
    "field_population", "owner_data_sanity", "address_parsing",
    "geometry_validation", "pagination", "performance_baseline",
)
NOT_CONFIGURED = ("cobb_ga", "gwinnett_ga", "clayton_ga", "henry_ga")
AI_FALLBACK_ONLY = ("spalding_ga", "fayette_ga")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Just enough of requests.Response for _http_get/_parse_arcgis_response."""

    def __init__(self, status_code: int, body) -> None:
        self.status_code = status_code
        if isinstance(body, str):
            self._text = body
            self.headers = {"content-type": "text/html"}
        else:
            self._text = json.dumps(body)
            self.headers = {"content-type": "application/json"}
        self.content = self._text.encode("utf-8")

    def json(self):
        return json.loads(self._text)


class _FakeSession:
    """Records every GET. `handler(url, params)` returns (status, body) or an
    exception instance, which is raised the way requests would raise it."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list = []

    def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params))
        out = self.handler(url, params)
        if isinstance(out, BaseException):
            raise out
        return _FakeResponse(*out)


def _no_network(url, params):
    # _safe would turn a raise into a failed check, so tests also assert
    # session.calls == [] rather than relying on this raise alone.
    raise AssertionError(f"network touched on a no-network path: {url}")


def _fulton() -> "ch.Connector":
    return ch.load_registry()["fulton_ga"]


def _fulton_feature(i: int, **overrides) -> dict:
    attrs = {
        "ParcelID": f"14F-0001-LL-{i:03d}",
        "Owner": "SMITH JOHN H",
        "OwnerAddr1": "4821 PEACHTREE DUNWOODY RD",
        "OwnerAddr2": "ATLANTA GA 30342",
        "Address": f"{100 + i} INDUSTRIAL BLVD",
        "LandAcres": 12.5, "LandAssess": 1000, "ImprAssess": 0,
        "TotAssess": 1000, "LUCode": "400", "ClassCode": "I4",
        "TaxDist": "05", "TaxYear": 2026, "Subdiv": None,
    }
    attrs.update(overrides)
    return {
        "attributes": attrs,
        "geometry": {"rings": [[[-84.55, 33.55], [-84.54, 33.55],
                                [-84.54, 33.56], [-84.55, 33.55]]]},
    }


def _fulton_handler(features=None, layer_fields=None, query=None):
    """Canned Fulton server. `query(url, params)` may return a response or
    exception for a /query call, or None to fall through to the features."""
    fields = layer_fields
    if fields is None:
        fields = sorted(v for v in _fulton().field_mapping.values() if v)
    feats = features if features is not None else [_fulton_feature(i) for i in range(10)]

    def handler(url, params):
        if url.endswith("/query"):
            if query is not None:
                out = query(url, params)
                if out is not None:
                    return out
            return 200, {"features": feats[:int(params.get("resultRecordCount", 10))]}
        if url.endswith("/11"):
            return 200, {"fields": [{"name": f} for f in fields],
                         "capabilities": "Map,Query,Data"}
        return 200, {"mapName": "PropertyMapViewer"}

    return handler


ENVELOPE_404 = (200, {"error": {"code": 404, "message": "Service not found", "details": []}})
RENAMED_FIELD_400 = (400, {"error": {
    "code": 400, "message": "Failed to execute query.",
    "details": ["'Invalid field: LandAcres' parameter is invalid"],
}})


def _check_results(**statuses) -> list:
    return [ch.CheckResult(n, statuses.get(n, "skipped"), {}) for n in ALL_CHECKS]


class _HermeticCase(unittest.TestCase):
    """No sleeps, no report/dashboard files, harness log lines captured."""

    def setUp(self) -> None:
        self.written: list = []
        self.log_lines: list = []
        lines = self.log_lines

        class _Capture(logging.Handler):
            def emit(self, record):
                lines.append(record.getMessage())

        for target, attr, value in (
            (ch.time, "sleep", lambda *_a, **_k: None),
            (ch, "_write_report", lambda report, *a, **k: self.written.append(report)),
            (ch, "_write_dashboard", lambda reports, *a, **k: None),
            (ch.logger, "handlers", [_Capture(level=logging.DEBUG)]),
            (ch.logger, "propagate", False),
        ):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(ch.logger.setLevel, ch.logger.level)
        # Per-host rate-limit stamps are module state; a frozen clock in one
        # test must not leave a future stamp that stalls a later real call.
        rate = patch.dict(ch._LAST_REQUEST_TIME, clear=True)
        rate.start()
        self.addCleanup(rate.stop)

    def _run(self, connector, handler, quick=False):
        session = _FakeSession(handler)
        with patch.object(ch, "_build_session", return_value=session):
            report = ch._run_all_checks(connector, quick=quick)
        return report, session

    def _main(self, argv, handler):
        session = _FakeSession(handler)
        with patch.object(ch, "_build_session", return_value=session):
            rc = ch.main(argv)
        return rc, session


# ---------------------------------------------------------------------------
# C-1: overall health
# ---------------------------------------------------------------------------


class TestOverallHealthTable(unittest.TestCase):
    """C-1 / G3: never 'healthy' unless the critical checks actually passed."""

    def test_status_table(self):
        quick_pass = {n: "pass" for n in ALL_CHECKS
                      if n not in ("geometry_validation", "performance_baseline")}
        full_pass = {n: "pass" for n in ALL_CHECKS}
        cases = [
            ("empty result list", [], "n_a"),
            ("every check skipped", _check_results(), "n_a"),
            ("service_alive only (url, no layer id)",
             _check_results(service_alive="pass"), "n_a"),
            ("service_alive only + non-critical crash",
             _check_results(service_alive="pass", pagination="fail"), "failing"),
            ("dead url, nothing else configured",
             _check_results(service_alive="fail"), "failing"),
            ("quick mode, everything that ran passed",
             _check_results(**quick_pass), "healthy"),
            ("full mode, all pass", _check_results(**full_pass), "healthy"),
            ("critical check failed",
             _check_results(**{**full_pass, "known_good_query": "fail"}), "failing"),
            ("non-critical check failed",
             _check_results(**{**full_pass, "field_population": "fail"}), "degraded"),
        ]
        for label, results, expected in cases:
            with self.subTest(label):
                self.assertEqual(ch._overall_health(results), expected)


class TestNotConfiguredConnectors(_HermeticCase):
    """C-1 / G4: unconfigured counties are n_a + config, with no network."""

    def test_unconfigured_counties_are_n_a_config_without_network(self):
        registry = ch.load_registry()
        for key in NOT_CONFIGURED:
            for quick in (False, True):
                with self.subTest(county=key, quick=quick):
                    report, session = self._run(registry[key], _no_network, quick=quick)
                    self.assertEqual(session.calls, [])
                    self.assertEqual(report["overall_health"], "n_a")
                    self.assertEqual(report["failure_class"], "config")
                    self.assertTrue(all(c["status"] == "skipped"
                                        for c in report["checks"].values()))
                    self.assertTrue(any("not configured" in e for e in report["errors"]))

    def test_ai_fallback_only_stays_n_a_without_failure_class(self):
        registry = ch.load_registry()
        for key in AI_FALLBACK_ONLY:
            with self.subTest(county=key):
                report, session = self._run(registry[key], _no_network)
                self.assertEqual(session.calls, [])
                self.assertEqual(report["overall_health"], "n_a")
                self.assertIn("failure_class", report)
                self.assertIsNone(report["failure_class"])

    def test_dekalb_dead_url_still_reports_failing(self):
        # No pre-network short-circuit: a configured-but-dead service_url is
        # the signal that the URL needs replacing (risk review decision 2).
        dekalb = ch.load_registry()["dekalb_ga"]
        report, session = self._run(dekalb, lambda u, p: ENVELOPE_404)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(report["overall_health"], "failing")
        self.assertEqual(report["failure_class"], "logic")

    def test_dekalb_url_without_layer_id_is_n_a_not_healthy(self):
        dekalb = ch.load_registry()["dekalb_ga"]
        report, _ = self._run(dekalb, lambda u, p: (200, {"mapName": "Parcels"}))
        self.assertEqual(report["checks"]["service_alive"]["status"], "pass")
        self.assertEqual(report["overall_health"], "n_a")
        self.assertEqual(report["failure_class"], "config")

    def test_main_exit_codes(self):
        cases = [
            (["--county", "cobb"], _no_network, ch.EXIT_CONFIG_ERROR),
            (["--county", "henry", "--quick"], _no_network, ch.EXIT_CONFIG_ERROR),
            (["--county", "spalding"], _no_network, ch.EXIT_OK),
            (["--county", "dekalb"], lambda u, p: ENVELOPE_404, ch.EXIT_CONNECTOR_FAILING),
        ]
        for argv, handler, expected in cases:
            with self.subTest(argv=argv):
                self.written.clear()
                rc, _ = self._main(argv, handler)
                self.assertEqual(rc, expected)
                self.assertEqual(len(self.written), 1)

    def test_aggregate_precedence(self):
        healthy = {"overall_health": "healthy", "failure_class": None}
        ai_n_a = {"overall_health": "n_a", "failure_class": None}
        config = {"overall_health": "n_a", "failure_class": "config"}
        degraded = {"overall_health": "degraded", "failure_class": "logic"}
        failing = {"overall_health": "failing", "failure_class": "transport"}
        cases = [
            ([healthy, ai_n_a], ch.EXIT_OK),
            ([healthy, config, ai_n_a], ch.EXIT_CONFIG_ERROR),
            ([config, degraded, healthy], ch.EXIT_CONNECTOR_DEGRADED),
            ([config, degraded, failing], ch.EXIT_CONNECTOR_FAILING),
        ]
        for reports, expected in cases:
            with self.subTest(reports=[r["overall_health"] for r in reports]):
                self.assertEqual(ch._aggregate_exit_code(reports), expected)
                with patch.object(ch, "run_harness_for_all_counties",
                                  return_value=reports):
                    self.assertEqual(ch.main(["--all"]), expected)


# ---------------------------------------------------------------------------
# C-2 / H-2: failure classes
# ---------------------------------------------------------------------------


class TestFailureClass(_HermeticCase):
    """C-2 / H-2 / G6: transport is an explicit allowlist; logic is the default."""

    CHECK_CASES = [
        ("ConnectionError", requests.exceptions.ConnectionError("down"), "transport"),
        ("ConnectTimeout", requests.exceptions.ConnectTimeout("slow"), "transport"),
        ("ReadTimeout", requests.exceptions.ReadTimeout("slow"), "transport"),
        ("ProxyError", requests.exceptions.ProxyError("proxy"), "transport"),
        ("HTTP 503 after retries", (503, "<html>busy</html>"), "transport"),
        ("HTTP 502 after retries", (502, "<html>bad gateway</html>"), "transport"),
        ("SSLError (expired cert)", requests.exceptions.SSLError("cert"), "logic"),
        ("MissingSchema", requests.exceptions.MissingSchema("no scheme"), "logic"),
        ("InvalidURL", requests.exceptions.InvalidURL("bad url"), "logic"),
        ("TooManyRedirects", requests.exceptions.TooManyRedirects("loop"), "logic"),
        ("200 + envelope 404 (DeKalb)", ENVELOPE_404, "logic"),
        ("200 + envelope 500", (200, {"error": {"code": 500, "message": "x"}}), "logic"),
        ("HTTP 400 + envelope (renamed field)", RENAMED_FIELD_400, "logic"),
        ("HTTP 404 html", (404, "<html>not found</html>"), "logic"),
        ("HTTP 429 html", (429, "<html>slow down</html>"), "logic"),
        ("200 non-JSON", (200, "<html>maintenance</html>"), "logic"),
        ("200 JSON list", (200, [1, 2]), "logic"),
    ]

    def test_check_level_classes(self):
        fulton = _fulton()
        for label, response, expected in self.CHECK_CASES:
            with self.subTest(label):
                session = _FakeSession(lambda u, p, r=response: r)
                result = ch.check_service_alive(fulton, session)
                self.assertEqual(result.status, "fail")
                self.assertEqual(result.details["failure_class"], expected)
                # Legacy key kept so archived reports still compare.
                self.assertIn("transport_status", result.details)

    def test_helper_allowlist(self):
        cases = [
            (("transport", "connection: ConnectionError"), "transport"),
            (("transport", "timeout: ReadTimeout"), "transport"),
            (("transport", "http_503"), "transport"),
            (("transport", "connection: SSLError"), "logic"),
            (("transport", "request: MissingSchema"), "logic"),
            (("transport", "unknown_transport_error"), "logic"),
            (("transport", None), "logic"),
            (("error", {"code": 503, "message": "x"}), "logic"),
            (("invalid", "non-JSON response"), "logic"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(ch._failure_class(*args), expected)

    def test_full_outage_is_transport_in_both_modes(self):
        outage = lambda u, p: requests.exceptions.ConnectionError("down")  # noqa: E731
        for quick in (True, False):
            with self.subTest(quick=quick):
                report, _ = self._run(_fulton(), outage, quick=quick)
                self.assertEqual(report["overall_health"], "failing")
                self.assertEqual(report["failure_class"], "transport")
                failed = {n: c for n, c in report["checks"].items() if c["status"] == "fail"}
                self.assertIn("pagination", failed)
                if not quick:
                    self.assertIn("performance_baseline", failed)
                for name, entry in failed.items():
                    self.assertEqual(entry["failure_class"], "transport", name)
                self.assertEqual(ch._aggregate_exit_code([report]),
                                 ch.EXIT_CONNECTOR_FAILING)

    def test_mixed_transport_types_stay_transport(self):
        def handler(url, params):
            if url.endswith("/query"):
                return requests.exceptions.ReadTimeout("slow")
            return 503, "<html>busy</html>"
        report, _ = self._run(_fulton(), handler)
        self.assertEqual(report["failure_class"], "transport")

    def test_transport_plus_logic_is_logic(self):
        def handler(url, params):
            if url.endswith("/11"):
                return ENVELOPE_404
            return requests.exceptions.ConnectionError("down")
        report, _ = self._run(_fulton(), handler)
        self.assertEqual(report["overall_health"], "failing")
        self.assertEqual(report["failure_class"], "logic")

    def test_dekalb_envelope_everywhere_is_logic(self):
        report, _ = self._run(_fulton(), lambda u, p: ENVELOPE_404, quick=True)
        self.assertEqual(report["overall_health"], "failing")
        self.assertEqual(report["failure_class"], "logic")
        self.assertEqual(report["checks"]["service_alive"]["failure_class"], "logic")

    def test_renamed_field_400_is_logic(self):
        fields = [f if f != "LandAcres" else "Acres"
                  for f in sorted(v for v in _fulton().field_mapping.values() if v)]
        handler = _fulton_handler(layer_fields=fields,
                                  query=lambda u, p: RENAMED_FIELD_400)
        report, _ = self._run(_fulton(), handler, quick=True)
        self.assertEqual(report["overall_health"], "failing")
        self.assertEqual(report["failure_class"], "logic")
        self.assertEqual(report["checks"]["field_mapping"]["failure_class"], "logic")
        self.assertEqual(report["checks"]["known_good_query"]["failure_class"], "logic")

    def test_pagination_carries_sub_query_class(self):
        fulton = _fulton()
        cases = [
            ("both transport", [requests.exceptions.ConnectionError("x")] * 2, "transport"),
            ("transport then envelope",
             [requests.exceptions.ConnectionError("x"), ENVELOPE_404], "logic"),
            ("ok then envelope", [(200, {"features": [{}]}), ENVELOPE_404], "logic"),
        ]
        for label, responses, expected in cases:
            with self.subTest(label):
                def handler(url, params, responses=responses):
                    first = params.get("resultRecordCount") == 1
                    return responses[0] if first else responses[1]

                result = ch.check_pagination(fulton, _FakeSession(handler))
                self.assertEqual(result.status, "fail")
                self.assertEqual(result.details["failure_class"], expected)

    def test_performance_baseline_carries_class(self):
        fulton = _fulton()
        for response, expected in (
            (requests.exceptions.ReadTimeout("slow"), "transport"),
            (ENVELOPE_404, "logic"),
        ):
            with self.subTest(expected=expected):
                result = ch.check_performance_baseline(
                    fulton, _FakeSession(lambda u, p, r=response: r))
                self.assertEqual(result.status, "fail")
                self.assertEqual(result.details["failure_class"], expected)

    def test_degraded_transport(self):
        # Critical checks pass; one pagination sub-query hits a network error.
        def query(url, params):
            if params.get("orderByFields") and params.get("resultRecordCount") == 1:
                return requests.exceptions.ConnectionError("reset")
            return None
        report, _ = self._run(_fulton(), _fulton_handler(query=query), quick=True)
        self.assertEqual(report["overall_health"], "degraded")
        self.assertEqual(report["failure_class"], "transport")
        self.assertEqual(ch._aggregate_exit_code([report]), ch.EXIT_CONNECTOR_DEGRADED)

    def test_degraded_logic(self):
        feats = [_fulton_feature(i, LUCode=None) for i in range(10)]
        report, _ = self._run(_fulton(), _fulton_handler(features=feats), quick=True)
        self.assertEqual(report["overall_health"], "degraded")
        self.assertEqual(report["failure_class"], "logic")
        self.assertEqual(report["checks"]["field_population"]["failure_class"], "logic")

    def test_check_crash_is_logic(self):
        with patch.object(ch, "check_field_mapping", side_effect=KeyError("boom")):
            report, _ = self._run(_fulton(), _fulton_handler(), quick=True)
        self.assertEqual(report["overall_health"], "degraded")
        self.assertEqual(report["checks"]["field_mapping"]["failure_class"], "logic")
        self.assertEqual(report["failure_class"], "logic")

    def test_harness_crash_stub_is_logic(self):
        with patch.object(ch, "_run_all_checks", side_effect=RuntimeError("boom")):
            report = ch._safe_run_checks(_fulton(), quick=True)
        self.assertEqual(report["overall_health"], "failing")
        self.assertEqual(report["failure_class"], "logic")

    def test_unclassified_fail_defaults_to_logic(self):
        results = _check_results(**{n: "pass" for n in ALL_CHECKS})
        results[-1] = ch.CheckResult("performance_baseline", "fail", {})
        report = ch._build_report(_fulton(), results, [], [], [])
        self.assertEqual(report["checks"]["performance_baseline"]["failure_class"], "logic")
        self.assertEqual(report["failure_class"], "logic")


class TestCannedFulton(_HermeticCase):
    """M-2: an all-pass Fulton stays healthy, exit 0, failure_class null, in
    both quick mode (CI) and full mode (research._harness_gate)."""

    def test_quick_and_full_healthy(self):
        for argv, skipped in (
            (["--county", "fulton", "--quick"], {"geometry_validation", "performance_baseline"}),
            (["--county", "fulton"], set()),
        ):
            with self.subTest(argv=argv):
                self.written.clear()
                rc, _ = self._main(argv, _fulton_handler())
                self.assertEqual(rc, ch.EXIT_OK)
                (report,) = self.written
                self.assertEqual(report["overall_health"], "healthy")
                self.assertIsNone(report["failure_class"])
                self.assertEqual(set(report["checks"]), set(ALL_CHECKS))
                for name, entry in report["checks"].items():
                    self.assertEqual(entry["status"], "skipped" if name in skipped else "pass",
                                     name)
                    self.assertNotIn("failure_class", entry)
                self.assertEqual(report["warnings"], [])
                self.assertEqual(report["errors"], [])


# ---------------------------------------------------------------------------
# H-3: redaction_extra_fields
# ---------------------------------------------------------------------------


class TestRedactionExtraFields(_HermeticCase):
    """H-3 / G7 / SR-8: honour redaction_extra_fields, union with defaults."""

    FIELD_MAP = {
        "parcel_id": "ParcelID", "owner_name": "Owner",
        "owner_mailing_address": "OwnerAddr1", "site_address": "Address",
    }
    _MISSING = object()

    def test_extra_fields_redacted_case_insensitively_any_value(self):
        feat = {"attributes": {"CO_OWNER": "DOE", "coowner2": 12345,
                               "Agent": None, "ParcelID": "14F-1"}}
        out = ch._redact_feature(feat, self.FIELD_MAP, ("co_owner", "CoOwner2", "AGENT"))
        for key in ("CO_OWNER", "coowner2", "Agent"):
            self.assertEqual(out["attributes"][key], "[REDACTED]", key)
        self.assertEqual(out["attributes"]["ParcelID"], "14F-1")
        # Input not mutated.
        self.assertEqual(feat["attributes"]["CO_OWNER"], "DOE")

    def test_defaults_kept_and_case_insensitive(self):
        feat = {"attributes": {"OWNER": "X", "owneraddr1": "Y", "ADDRESS": "Z",
                               "CoOwner": "W", "ParcelID": "1"}}
        with_extra = ch._redact_feature(feat, self.FIELD_MAP, ("CoOwner",))
        for key in ("OWNER", "owneraddr1", "ADDRESS", "CoOwner"):
            self.assertEqual(with_extra["attributes"][key], "[REDACTED]", key)
        # Two-argument call (back-compat) still redacts the defaults only.
        defaults_only = ch._redact_feature(feat, self.FIELD_MAP)
        for key in ("OWNER", "owneraddr1", "ADDRESS"):
            self.assertEqual(defaults_only["attributes"][key], "[REDACTED]", key)
        self.assertEqual(defaults_only["attributes"]["CoOwner"], "W")
        self.assertEqual(defaults_only["attributes"]["ParcelID"], "1")

    def _load(self, extra):
        overlay_entry = {} if extra is self._MISSING else {"redaction_extra_fields": extra}
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "sources.json"
            ovl = Path(td) / "connector_registry.json"
            src.write_text(json.dumps({"county_parcel_data": {
                "x_ga": {"access": "arcgis_rest", "field_mapping": {}}}}))
            ovl.write_text(json.dumps({"x_ga": overlay_entry}))
            return ch.load_registry(sources_path=src, overlay_path=ovl)["x_ga"]

    def test_registry_parsing(self):
        self.assertEqual(self._load(self._MISSING).redaction_extra_fields, ())
        self.assertEqual(self._load(None).redaction_extra_fields, ())
        self.assertEqual(self._load([]).redaction_extra_fields, ())
        self.assertEqual(
            self._load(["_comment_for_humans: names go here", "CoOwner"]).redaction_extra_fields,
            ("CoOwner",),
        )

    def test_registry_rejects_malformed_config(self):
        for bad in ("CoOwner", {"CoOwner": True}, [1], ["CoOwner", None], [""], ["  "]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    self._load(bad)
                self.assertIn("redaction_extra_fields", str(cm.exception))

    def test_real_registry_loads_and_skips_human_notes(self):
        # The live fulton_ga list holds only a "_comment_for_humans" note. Not
        # pinned to (): a human adding a real field name must keep CI green.
        registry = ch.load_registry()
        self.assertIn("fulton_ga", registry)
        for key, connector in registry.items():
            self.assertFalse(
                any(name.startswith("_") for name in connector.redaction_extra_fields), key)

    def test_run_applies_extra_fields_to_report_sample_only(self):
        feats = [_fulton_feature(i, CoOwnerName="DOE") for i in range(10)]
        plain, _ = self._run(_fulton(), _fulton_handler(features=feats), quick=True)
        self.assertEqual(plain["sample_features"][0]["attributes"]["CoOwnerName"], "DOE")

        fulton = dataclasses.replace(_fulton(), redaction_extra_fields=("coownername",))
        report, _ = self._run(fulton, _fulton_handler(features=feats), quick=True)
        self.assertEqual(report["overall_health"], "healthy")
        self.assertEqual(len(report["sample_features"]), 3)
        for feat in report["sample_features"]:
            self.assertEqual(feat["attributes"]["CoOwnerName"], "[REDACTED]")
            self.assertEqual(feat["attributes"]["Owner"], "[REDACTED]")
        self.assertEqual(report["warnings"], [])
        # Server features (what the checks saw) are never mutated in place.
        self.assertEqual(feats[0]["attributes"]["CoOwnerName"], "DOE")
        self.assertEqual(feats[0]["attributes"]["Owner"], "SMITH JOHN H")

    def test_write_report_failsafe_still_fires(self):
        bad_report = {
            "county": "fulton", "market": "atlanta",
            "timestamp": "2026-04-30T10:00:00Z", "overall_health": "healthy",
            "failure_class": None, "checks": {},
            "sample_features": [{"attributes": {"Unmapped": "Bob Jones"}}],
            "warnings": [], "errors": [],
        }
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError):
                _REAL_WRITE_REPORT(bad_report, reports_dir=Path(td))
            self.assertEqual(list(Path(td).iterdir()), [])


# ---------------------------------------------------------------------------
# H-4: --verbose
# ---------------------------------------------------------------------------


class TestVerboseMetadataOnly(_HermeticCase):
    """H-4 / G8 / SR-8: --verbose logs metadata, never attribute values."""

    PII = ("SMITH", "JOHN", "PEACHTREE", "DUNWOODY", "30342")

    def _assert_no_pii(self, lines):
        blob = "\n".join(lines)
        for needle in self.PII:
            self.assertNotIn(needle, blob)

    def test_verbose_flag_logs_metadata_and_leaves_report_identical(self):
        # A frozen clock makes response_time_ms identical across both runs.
        clock = patch.object(ch.time, "monotonic", return_value=1000.0)
        clock.start()
        self.addCleanup(clock.stop)
        ch.logger.setLevel(logging.INFO)
        rc, _ = self._main(["--county", "fulton", "--quick", "--verbose"], _fulton_handler())
        self.assertEqual(rc, ch.EXIT_OK)
        verbose_lines = list(self.log_lines)
        self.assertTrue(any("HTTP 200" in ln and "features=10" in ln for ln in verbose_lines),
                        verbose_lines)
        self.assertTrue(any("mapName" in ln for ln in verbose_lines), verbose_lines)
        self._assert_no_pii(verbose_lines)

        ch.logger.setLevel(logging.INFO)
        self.log_lines.clear()
        rc, _ = self._main(["--county", "fulton", "--quick"], _fulton_handler())
        self.assertEqual(rc, ch.EXIT_OK)
        self.assertFalse(any("HTTP 200" in ln for ln in self.log_lines), self.log_lines)

        verbose_report, plain_report = self.written
        verbose_report = dict(verbose_report, timestamp=None)
        plain_report = dict(plain_report, timestamp=None)
        self.assertEqual(verbose_report, plain_report)

    def test_debug_summary_for_envelope_transport_and_secrets(self):
        ch.logger.setLevel(logging.DEBUG)
        ch._arcgis_get(_FakeSession(lambda u, p: ENVELOPE_404),
                       "https://gis.example/MapServer?token=abc123", params={"f": "pjson"})
        ch._arcgis_get(_FakeSession(lambda u, p: requests.exceptions.ConnectionError("x")),
                       "https://gis.example/MapServer", params={"f": "pjson"})
        # A non-JSON body is described, never echoed (it could hold anything).
        ch._arcgis_get(_FakeSession(lambda u, p: (
            200, "<html>Owner: SMITH JOHN H, 4821 PEACHTREE DUNWOODY RD 30342</html>")),
            "https://gis.example/MapServer", params={"f": "pjson"})
        blob = "\n".join(self.log_lines)
        self.assertIn("404", blob)
        self.assertIn("Service not found", blob)
        self.assertIn("connection: ConnectionError", blob)
        self.assertIn("parse=invalid", blob)
        self.assertIn("token=***", blob)
        self.assertNotIn("abc123", blob)
        self._assert_no_pii(self.log_lines)

    def test_help_documents_the_deviation(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            ch._parse_args(["--help"])
        text = " ".join(out.getvalue().split())
        self.assertIn("never prints response bodies or attribute values", text.lower())


# ---------------------------------------------------------------------------
# M-1: research discovery gate
# ---------------------------------------------------------------------------


class TestResearchGateNotConfigured(_HermeticCase):
    """M-1 / G5: n_a is unknown to the gate -> 'failing' (skip), not cached."""

    def test_gate_skips_and_does_not_cache_unconfigured_county(self):
        import research  # noqa: WPS433 — imported lazily (pulls in prepare)

        self.assertIs(research.connector_harness, ch)
        research._HARNESS_GATE_CACHE.pop("cobb", None)
        self.addCleanup(research._HARNESS_GATE_CACHE.pop, "cobb", None)
        with patch.object(ch, "_build_session", return_value=_FakeSession(_no_network)) as s, \
                self.assertLogs(research.log, level="WARNING"):
            status, report = research._harness_gate("cobb", cache_ttl_seconds=900)
        self.assertEqual(s.return_value.calls, [])
        self.assertEqual(status, "failing")
        self.assertEqual(report["overall_health"], "n_a")
        self.assertEqual(report["failure_class"], "config")
        self.assertNotIn("cobb", research._HARNESS_GATE_CACHE)


# ---------------------------------------------------------------------------
# H-1 / M-4 / M-5: the harness-fulton workflow verdict
# ---------------------------------------------------------------------------


def _workflow_run_block(step_name: str) -> str:
    """The verbatim `run: |` script of a workflow step (no PyYAML, SR-10)."""
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == f"- name: {step_name}")
    run_idx = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    if any(ln.strip().startswith("- name:") for ln in lines[start + 1:run_idx]):
        raise AssertionError(f"step {step_name!r} has no run block")
    indent = len(lines[run_idx]) - len(lines[run_idx].lstrip(" "))
    body = []
    for ln in lines[run_idx + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip(" ")) <= indent:
            break
        body.append(ln)
    return textwrap.dedent("\n".join(body)).strip("\n") + "\n"


_FAKE_HARNESS = '''\
"""Stand-in for connector_harness.py in the workflow simulation."""
import json, os, sys
if sys.argv[1:] != ["--county", "fulton", "--quick"]:
    sys.exit(99)
spec = json.loads(os.environ["FAKE_HARNESS_SPEC"])
if spec["report_text"] is not None:
    os.makedirs("harness_reports", exist_ok=True)
    path = os.path.join("harness_reports", "fulton_20261028T000000Z.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(spec["report_text"])
sys.exit(spec["rc"])
'''

_BASH = shutil.which("bash")
_TIMEOUT = shutil.which("timeout")


class TestWorkflowStatic(unittest.TestCase):
    """H-1 / M-4 / G9: the workflow keys on failure_class, never the old grep."""

    def setUp(self) -> None:
        self.text = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_no_transport_status_grep(self):
        self.assertNotIn("transport_status", self.text)
        self.assertIn("failure_class", _workflow_run_block(CLASSIFY_STEP))

    def test_exit_code_captured_for_every_rc(self):
        self.assertIn("|| rc=$?", _workflow_run_block(HARNESS_STEP))

    def test_no_expression_interpolation_inside_scripts(self):
        for step in (HARNESS_STEP, CLASSIFY_STEP):
            self.assertNotIn("${{", _workflow_run_block(step), step)
        self.assertIn("HARNESS_EC: ${{ steps.harness.outputs.exit_code }}", self.text)

    def test_default_shell_semantics(self):
        # The simulation below runs `bash -e`, GitHub's default for `run:`;
        # an explicit `shell: bash` would add pipefail and change semantics.
        self.assertNotIn("shell:", self.text)

    def test_new_test_file_in_both_path_filters(self):
        self.assertEqual(self.text.count("- 'tests/test_harness_health.py'"), 2)


@unittest.skipUnless(_BASH and _TIMEOUT, "needs bash and coreutils timeout")
class TestWorkflowVerdictSimulation(_HermeticCase):
    """H-1 / M-5 / G9: run the verbatim run blocks under `bash -e` (GitHub's
    default shell) against synthetic reports and check each verdict."""

    def _simulate(self, rc, report_text, ec_override=None):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "bin").mkdir()
            os.symlink(sys.executable, tdp / "bin" / "python")
            (tdp / "connector_harness.py").write_text(_FAKE_HARNESS, encoding="utf-8")
            (tdp / "harness.sh").write_text(_workflow_run_block(HARNESS_STEP), encoding="utf-8")
            (tdp / "classify.sh").write_text(_workflow_run_block(CLASSIFY_STEP), encoding="utf-8")
            gh_output = tdp / "github_output"
            gh_output.write_text("", encoding="utf-8")
            env = {
                "PATH": f"{tdp / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
                "HOME": td, "LC_ALL": "C.UTF-8",
                "GITHUB_OUTPUT": str(gh_output),
                "FAKE_HARNESS_SPEC": json.dumps({"rc": rc, "report_text": report_text}),
            }
            if ec_override is None:
                step = subprocess.run(
                    [_BASH, "--noprofile", "--norc", "-e", "harness.sh"],
                    cwd=td, env=env, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(step.returncode, 0, step.stderr)
                outputs = dict(
                    ln.split("=", 1) for ln in gh_output.read_text().splitlines() if "=" in ln
                )
                ec = outputs.get("exit_code")
                self.assertEqual(ec, str(rc), "exit code not captured")
            else:
                ec = ec_override
            proc = subprocess.run(
                [_BASH, "--noprofile", "--norc", "-e", "classify.sh"],
                cwd=td, env={**env, "HARNESS_EC": ec},
                capture_output=True, text=True, timeout=60,
            )
        if proc.returncode != 0:
            self.assertIn("::error", proc.stdout)
            return "fail"
        return "warn" if "::warning" in proc.stdout else "pass"

    def _reports(self) -> dict:
        fulton = _fulton()

        def pagination_reset(url, params):
            if params.get("orderByFields") and params.get("resultRecordCount") == 1:
                return requests.exceptions.ConnectionError("reset")
            return None

        fields = [f if f != "LandAcres" else "Acres"
                  for f in sorted(v for v in fulton.field_mapping.values() if v)]
        built = {
            "healthy": self._run(fulton, _fulton_handler(), quick=True)[0],
            "degraded_transport": self._run(
                fulton, _fulton_handler(query=pagination_reset), quick=True)[0],
            "degraded_logic": self._run(fulton, _fulton_handler(
                features=[_fulton_feature(i, LUCode=None) for i in range(10)]), quick=True)[0],
            "failing_transport": self._run(
                fulton, lambda u, p: requests.exceptions.ConnectionError("down"), quick=True)[0],
            "failing_envelope": self._run(fulton, lambda u, p: ENVELOPE_404, quick=True)[0],
            "failing_400": self._run(fulton, _fulton_handler(
                layer_fields=fields, query=lambda u, p: RENAMED_FIELD_400), quick=True)[0],
            "config": self._run(ch.load_registry()["cobb_ga"], _no_network, quick=True)[0],
        }
        with patch.object(ch, "_run_all_checks", side_effect=RuntimeError("boom")):
            built["crash_stub"] = ch._safe_run_checks(fulton, quick=True)
        return {k: json.dumps(v, indent=2, sort_keys=True) for k, v in built.items()}

    def test_verdicts(self):
        r = self._reports()
        missing_class = json.loads(r["failing_transport"])
        del missing_class["failure_class"]
        unknown_class = dict(json.loads(r["failing_transport"]), failure_class="network")
        scenarios = [
            # (label, harness rc, report text or None, expected verdict)
            ("healthy", 0, r["healthy"], "pass"),
            ("degraded, transport", 3, r["degraded_transport"], "warn"),
            ("degraded, logic", 3, r["degraded_logic"], "fail"),
            ("failing, transport", 2, r["failing_transport"], "warn"),
            ("failing, DeKalb-style 200 envelope", 2, r["failing_envelope"], "fail"),
            ("failing, renamed field HTTP 400", 2, r["failing_400"], "fail"),
            ("not configured (n_a, config)", 1, r["config"], "fail"),
            ("harness crash stub", 2, r["crash_stub"], "fail"),
            ("crash before write", 1, None, "fail"),
            ("failing exit, no report", 2, None, "fail"),
            ("exit 0 but report n_a", 0, r["config"], "fail"),
            ("exit 0 but report degraded", 0, r["degraded_transport"], "fail"),
            ("exit 2 but report degraded", 2, r["degraded_transport"], "fail"),
            ("truncated JSON", 2, r["failing_transport"][:40], "fail"),
            ("report missing failure_class", 2, json.dumps(missing_class), "fail"),
            ("unknown failure_class", 2, json.dumps(unknown_class), "fail"),
            ("timeout 124, no report", 124, None, "warn"),
            ("timeout 124 with report", 124, r["failing_transport"], "fail"),
            ("killed 137, no report", 137, None, "fail"),
        ]
        for label, rc, text, expected in scenarios:
            with self.subTest(label):
                self.assertEqual(self._simulate(rc, text), expected)

        # Generated reports really carry the exit codes the scenarios use.
        for key, rc in (("healthy", 0), ("degraded_transport", 3), ("degraded_logic", 3),
                        ("failing_transport", 2), ("failing_envelope", 2),
                        ("failing_400", 2), ("config", 1), ("crash_stub", 2)):
            self.assertEqual(ch._aggregate_exit_code([json.loads(r[key])]), rc, key)

    def test_empty_or_garbled_exit_code_fails(self):
        r = self._reports()
        for ec in ("", "abc", " 0"):
            with self.subTest(ec=ec):
                self.assertEqual(self._simulate(0, r["healthy"], ec_override=ec), "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
