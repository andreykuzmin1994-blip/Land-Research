"""zoning.py: rules freeze, schema, stored encoding, classification (reviews/21_zoning).

Offline (SR-6). Risk ids (Z-xx) refer to reviews/21_zoning/02_risk_review.md.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import tests  # noqa: F401 — suite guards (REAL_VERIFY_RULES_COMMITTED)
import zoning
from zoning_helpers import REPO_ROOT, patch_rules, repo_rules, state_from, stored, verified

FIXTURES = Path(__file__).parent / "fixtures" / "zoning"
SNIPPET = REPO_ROOT / "reviews" / "21_zoning" / "sources_json_snippet.json"


class RulesFileSchemaTests(unittest.TestCase):
    """Z-02(c), Z-07, Z-08, Z-22: the repo's zoning_rules.json."""

    def setUp(self) -> None:
        self.data = repo_rules()

    def test_parses_and_validates(self):
        self.assertEqual(zoning.validate_rules(self.data), [])
        self.assertIsNone(zoning.rules_problem())

    def test_every_jurisdiction_is_draft_in_this_change(self):
        # OPERATOR STEP: when a jurisdiction is verified (between runs, with a
        # new run + baseline), update this expectation in the same commit.
        statuses = {k: j["status"] for k, j in self.data["jurisdictions"].items()}
        self.assertEqual(set(statuses.values()), {"draft"}, statuses)
        self.assertFalse(zoning.any_verified())

    def test_keys_are_known_and_declare_required_fields(self):
        for key, j in self.data["jurisdictions"].items():
            self.assertIn(key, zoning.KNOWN_JURISDICTIONS)
            self.assertIn(j["unlisted_districts"], zoning.UNLISTED_SETTINGS, key)
            for f in ("as_of", "verified_by", "verified_on"):
                self.assertIn(f, j, key)

    def test_every_status_is_in_the_vocabulary_with_a_cite(self):
        for key, j in self.data["jurisdictions"].items():
            for code, d in j["districts"].items():
                wd = d["warehouse_distribution"]
                self.assertIn(wd["status"], zoning.STATUS_TO_CLASS, (key, code))
                self.assertTrue(wd["cite"].strip(), (key, code))

    def test_fid_declares_governing_rules_and_conversion(self):
        fid = self.data["jurisdictions"]["fid"]
        self.assertEqual(fid["governing_jurisdiction"], "south_fulton")
        self.assertEqual(fid["district_conversions"], {"M-1A": "M-1"})
        self.assertEqual(fid["status"], "draft")

    def test_rules_cover_exactly_the_snippet_zoning_jurisdictions(self):
        # Z-02(b): every jurisdiction with a zoning source has rules, and back.
        snip = json.loads(SNIPPET.read_text(encoding="utf-8"))
        with_source = {j for s in snip["zoning_sources"] if s["role"] == "zoning"
                       for j in s["jurisdictions"]}
        self.assertEqual(with_source, set(self.data["jurisdictions"]))

    def test_schema_violations_are_reported(self):
        bad = verified("atlanta")
        bad["jurisdictions"]["atlanta"]["verified_by"] = None
        bad["jurisdictions"]["fairburn"]["status"] = "approved"
        del bad["jurisdictions"]["union_city"]["unlisted_districts"]
        bad["jurisdictions"]["south_fulton"]["districts"]["M-1"]["warehouse_distribution"]["status"] = "maybe"
        bad["jurisdictions"]["south_fulton"]["districts"]["M-2"]["warehouse_distribution"]["cite"] = " "
        bad["jurisdictions"]["nowhere"] = bad["jurisdictions"]["east_point"]
        errs = zoning.validate_rules(bad)
        self.assertEqual(len(errs), 6, errs)
        with self.assertRaises(zoning.ZoningRulesError):
            zoning.parse_rules_bytes(json.dumps(bad).encode())


class FreezeTests(unittest.TestCase):
    """Z-01: loaded once, frozen, SHA-256 sentinel."""

    def test_load_rules_returns_the_frozen_mapping_never_a_reread(self):
        a = zoning.load_rules()
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("re-read")):
            b = zoning.load_rules()
        self.assertIs(a, b)
        self.assertIsInstance(a, types.MappingProxyType)
        with self.assertRaises(TypeError):
            a["jurisdictions"]["atlanta"]["status"] = "verified"  # type: ignore[index]

    def test_changed_bytes_after_load_raise(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "zoning_rules.json"
            p.write_text(json.dumps(repo_rules()), encoding="utf-8")
            st = zoning._load_state(p)
            with mock.patch.object(zoning, "_STATE", st):
                zoning.verify_rules_unchanged(p)          # unchanged: passes
                data = verified("south_fulton")
                p.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(zoning.ZoningRulesChanged):
                    zoning.verify_rules_unchanged(p)
                p.unlink()
                with self.assertRaises(zoning.ZoningRulesChanged):
                    zoning.verify_rules_unchanged(p)

    def test_missing_file_stays_missing_passes_and_degrades(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "zoning_rules.json"
            st = zoning._load_state(p)
            self.assertIsNone(st.rules)
            with mock.patch.object(zoning, "_STATE", st):
                zoning.verify_rules_unchanged(p)
                self.assertEqual(zoning.classify("south_fulton", "M-1").cls, zoning.UNKNOWN)
                with self.assertRaises(zoning.ZoningRulesError):
                    zoning.load_rules()
                p.write_text("{}", encoding="utf-8")       # appearing mid-run is a change
                with self.assertRaises(zoning.ZoningRulesChanged):
                    zoning.verify_rules_unchanged(p)

    def test_malformed_file_degrades_never_raises_on_load(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "zoning_rules.json"
            p.write_text("{not json", encoding="utf-8")
            st = zoning._load_state(p)
            self.assertIsNone(st.rules)
            self.assertIn("not valid", st.problem or "")
            self.assertIsNotNone(st.sha256)


class NormalizeTests(unittest.TestCase):
    """Z-08: table-driven over every distinct spike code."""

    EXPECTED = {
        "I-1-C": ("I-1", True), "I-Mix-C": ("I-MIX", True), "C-1-C": ("C-1", True),
        "MRC-3-C": ("MRC-3", True), "RG-2-C": ("RG-2", True), "MRC-1": ("MRC-1", False),
        "R-2 OVERLAY": ("R-2", False), "r-3 overlay": ("R-3", False), " m-1 ": ("M-1", False),
        "SPI-4 SA1": ("SPI-4SA1", False), "I1C": ("I1", True), "GC": ("GC", False),
        "NC": ("NC", False), "M-1A": ("M-1A", False), "O&I": ("O&I", False),
        None: (None, False), "": (None, False), "NULL": (None, False),
    }

    def test_table(self):
        for raw, want in self.EXPECTED.items():
            self.assertEqual(zoning.normalize(raw), want, raw)

    def test_every_spike_code_normalizes(self):
        codes = json.loads((FIXTURES / "spike_codes.json").read_text())["codes"]
        self.assertEqual(sum(c["parcels"] for c in codes), 1075)
        for c in codes:
            base, cond = zoning.normalize(c["code"])
            if c["code"] is None:
                self.assertIsNone(base)
                continue
            self.assertTrue(base and base == base.upper() and " " not in base, c)
            self.assertEqual(cond, c["code"].upper().endswith("-C"), c)


class ClassifyTests(unittest.TestCase):
    INDUSTRIAL = {("atlanta", "I-1"), ("atlanta", "I-2"), ("atlanta", "I-1-C"),
                  ("fairburn", "M-1"), ("fairburn", "M-2"), ("union_city", "M-1"),
                  ("south_fulton", "M-1"), ("south_fulton", "M-2"), ("fid", "M-2")}
    REZONING = {("south_fulton", "AG-1"), ("fairburn", "PD")}

    def test_every_spike_code_with_all_rules_verified(self):
        codes = json.loads((FIXTURES / "spike_codes.json").read_text())["codes"]
        allv = verified(*repo_rules()["jurisdictions"])
        with patch_rules(allv):
            for c in codes:
                got = zoning.classify(c["jurisdiction"], c["code"])
                key = (c["jurisdiction"], c["code"])
                if key in self.INDUSTRIAL:
                    want = zoning.BY_RIGHT
                elif key in self.REZONING:
                    want = zoning.REZONING
                else:
                    want = zoning.UNKNOWN       # unlisted_districts = unknown everywhere
                self.assertEqual(got.cls, want, key)
                self.assertEqual(got.verified, c["code"] is not None, key)

    def test_draft_rules_classify_but_never_report_verified(self):
        self.assertEqual(zoning.classify("south_fulton", "M-1"),
                         zoning.Classification(zoning.BY_RIGHT, False,
                                               "warehouse/distribution permitted_by_right"))
        for setting in ("not_permitted", "unknown"):
            data = repo_rules()
            data["jurisdictions"]["south_fulton"]["unlisted_districts"] = setting
            with patch_rules(data):
                self.assertFalse(zoning.classify("south_fulton", "R-3").verified)

    def test_unlisted_district_follows_the_human_setting(self):
        for setting, want in (("not_permitted", zoning.REZONING), ("unknown", zoning.UNKNOWN)):
            data = verified("south_fulton")
            data["jurisdictions"]["south_fulton"]["unlisted_districts"] = setting
            with patch_rules(data):
                c = zoning.classify("south_fulton", "R-3A")
                self.assertEqual((c.cls, c.verified), (want, True), setting)

    def test_same_code_in_two_jurisdictions_uses_its_own_rules(self):
        # Z-02(d): M-1 in Fairburn and in Union City; PD only in Fairburn.
        def mut(d):
            d["jurisdictions"]["union_city"]["districts"]["M-1"]["warehouse_distribution"]["status"] = "use_permit"
        with patch_rules(verified("fairburn", "union_city", mutate=mut)):
            self.assertEqual(zoning.classify("fairburn", "M-1").cls, zoning.BY_RIGHT)
            self.assertEqual(zoning.classify("union_city", "M-1").cls, zoning.CONDITIONAL)
            self.assertEqual(zoning.classify("fairburn", "PD").cls, zoning.REZONING)
            self.assertEqual(zoning.classify("union_city", "PD").cls, zoning.UNKNOWN)

    def test_vocabulary_and_unrecognised_status(self):
        for status, want in (("permitted_by_right", "by_right"), ("special_use_permit", "conditional"),
                             ("use_permit", "conditional"), ("conditional_use", "conditional"),
                             ("not_permitted", "rezoning")):
            self.assertEqual(zoning.STATUS_TO_CLASS[status], want)
        data = verified("atlanta")
        data["jurisdictions"]["atlanta"]["districts"]["I-1"]["warehouse_distribution"]["status"] = "by right-ish"
        st = zoning._State(zoning._deep_freeze(data), "x", None)   # bypass the validator
        with mock.patch.object(zoning, "_STATE", st):
            self.assertEqual(zoning.classify("atlanta", "I-1").cls, zoning.UNKNOWN)

    def test_conditions_suffix_never_downgrades(self):
        with patch_rules(verified("atlanta")):
            c = zoning.classify("atlanta", "I-1-C")
            self.assertEqual((c.cls, c.has_conditions), (zoning.BY_RIGHT, True))

    def test_location_condition_effect(self):
        def mut(d):
            d["jurisdictions"]["atlanta"]["districts"]["I-2"]["location_conditions"][0]["effect"] = \
                "downgrade_to_conditional"
        with patch_rules(verified("atlanta", mutate=mut)):
            self.assertEqual(zoning.classify("atlanta", "I-2").cls, zoning.CONDITIONAL)
            self.assertEqual(zoning.classify("atlanta", "I-1").cls, zoning.BY_RIGHT)

    def test_fid_resolves_to_governing_rules_with_conversion(self):
        # Z-18
        with patch_rules(verified("fid", "south_fulton")):
            c = zoning.classify("fid", "M-1A")
            self.assertEqual((c.cls, c.verified), (zoning.BY_RIGHT, True))
            self.assertEqual(zoning.classify("fid", "AG-1").cls, zoning.REZONING)
        with patch_rules(verified("fid")):                 # governing still draft
            self.assertFalse(zoning.classify("fid", "M-1A").verified)

        def own(d):
            d["jurisdictions"]["fid"]["unincorporated_parcel_ids"] = ["fulton-X1"]
            d["jurisdictions"]["fid"]["districts"]["M-1A"]["warehouse_distribution"]["status"] = "use_permit"
        with patch_rules(verified("fid", "south_fulton", mutate=own)):
            self.assertEqual(zoning.classify("fid", "M-1A", "fulton-X1").cls, zoning.CONDITIONAL)
            self.assertEqual(zoning.classify("fid", "M-1A", "fulton-X2").cls, zoning.BY_RIGHT)

        def nomap(d):
            del d["jurisdictions"]["fid"]["governing_jurisdiction"]
        with patch_rules(verified("fid", "south_fulton", mutate=nomap)):
            # Without the human mapping FID uses its own county table: M-1A is
            # listed there, but an unlisted South Fulton code is unknown.
            self.assertEqual(zoning.classify("fid", "AG-1").cls, zoning.UNKNOWN)

    def test_uncovered_and_unknown_jurisdictions(self):
        # Z-19
        for j in ("college_park", "palmetto", "hapeville", "chattahoochee_hills", None, "district 99"):
            c = zoning.classify(j, "M-1")
            self.assertEqual((c.cls, c.verified), (zoning.UNKNOWN, False), j)


class StoredEncodingTests(unittest.TestCase):
    """Z-02(a), Z-06: what zoning_import writes is what parse_stored reads."""

    def test_round_trip(self):
        z, d = stored("south_fulton", "south_fulton_zoning", "M-1", True, "Light | Industrial\n")
        self.assertEqual(z, "M-1")
        self.assertNotIn("\n", d)
        st = zoning.parse_stored(z, d)
        self.assertEqual(st, zoning.Stored("south_fulton", "south_fulton_zoning", "M-1", True))
        self.assertEqual(zoning.classify_stored(z, d).cls, zoning.BY_RIGHT)

    def test_no_source_round_trip(self):
        d = zoning.no_source_description("college_park")
        self.assertEqual(zoning.parse_stored(None, d), zoning.Stored("college_park", None, None, False))
        self.assertEqual(zoning.classify_stored(None, d).cls, zoning.UNKNOWN)
        self.assertIn("not machine-readable for college_park", zoning.h3_description(None, d))

    def test_values_without_provenance_are_unknown(self):
        z, d = stored("south_fulton", "south_fulton_zoning", "AG-1")
        with patch_rules(verified("south_fulton")):
            self.assertEqual(zoning.classify_stored(z, d).cls, zoning.REZONING)
            for zz, dd in (("M-2", d), ("M-2", None), ("M-2", "Heavy Industrial"),
                           ("M-1", d.replace("south_fulton |", "atlantis |"))):
                self.assertEqual(zoning.classify_stored(zz, dd).cls, zoning.UNKNOWN, (zz, dd))

    def test_encode_refuses_bad_keys(self):
        for args in (("atlantis", "s", "M-1"), ("atlanta", "Bad Key", "M-1"), ("atlanta", "s", " ")):
            with self.assertRaises(ValueError):
                zoning.encode_stored(args[0], args[1], args[2], False, None)

    def test_h3_text_is_stable_and_short(self):
        z, d = stored("atlanta", "atlanta_zoning", "I-1-C", True, "x" * 500)
        t = zoning.h3_description(z, d)
        self.assertEqual(t, "H3 zoning I-1-C (atlanta): by_right, rules draft "
                            "(conditions of zoning, split-zoned)")
        self.assertNotIn("; ", t)
        self.assertLess(len(t), 160)
        self.assertEqual(zoning.h3_description(None, None),
                         zoning.H3_NOT_IMPORTED)


class CommittedRulesTests(unittest.TestCase):
    """Review finding 3: the runner compares the file with git HEAD."""

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    def test_clean_dirty_untracked_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self._git(repo, "init", "-q")
            rules = repo / "zoning_rules.json"
            tests.REAL_VERIFY_RULES_COMMITTED(rules)            # absent on disk and in HEAD
            rules.write_bytes(b'{"a": 1}\n')
            with self.assertRaises(zoning.ZoningRulesChanged):
                tests.REAL_VERIFY_RULES_COMMITTED(rules)        # untracked
            self._git(repo, "add", "zoning_rules.json")
            self._git(repo, "commit", "-q", "-m", "rules")
            tests.REAL_VERIFY_RULES_COMMITTED(rules)            # committed == disk
            rules.write_bytes(b'{"a": 2}\n')
            with self.assertRaises(zoning.ZoningRulesChanged):
                tests.REAL_VERIFY_RULES_COMMITTED(rules)        # uncommitted edit
            rules.unlink()
            with self.assertRaises(zoning.ZoningRulesChanged):
                tests.REAL_VERIFY_RULES_COMMITTED(rules)        # deleted

    def test_outside_a_repository_fails_loud(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(Path(td).parent)}):
                with self.assertRaises(zoning.ZoningRulesChanged):
                    tests.REAL_VERIFY_RULES_COMMITTED(Path(td) / "zoning_rules.json")

    def test_the_repo_file_is_committed(self):
        tests.REAL_VERIFY_RULES_COMMITTED()


class MemoTextTests(unittest.TestCase):
    """Findings 9/11: the memo never shows the raw provenance string."""

    def test_imported_value_is_decoded(self):
        z, d = stored("atlanta", "atlanta_zoning", "I-1-C", True, "Light Industrial")
        t = zoning.memo_text(z, d)
        self.assertNotIn(zoning.STORED_PREFIX, t)
        self.assertNotIn(" | ", t)
        self.assertEqual(t, "I-1-C (atlanta, conditions of zoning, split-zoned): by_right, "
                            "rules draft; source atlanta_zoning: Light Industrial")
        t = zoning.memo_text(None, zoning.no_source_description("palmetto"))
        self.assertEqual(t, "no machine-readable zoning for palmetto (check with the city)")
        self.assertEqual(zoning.memo_text("AG-1", "Agricultural"), "AG-1 — Agricultural")


class BoundaryTests(unittest.TestCase):
    """Z-23: protected modules import neither the sandbox, the runner nor screener."""

    def test_imports(self):
        for name in ("zoning.py", "zoning_import.py"):
            tree = ast.parse((REPO_ROOT / name).read_text(encoding="utf-8"))
            mods = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    mods.add((node.module or "").split(".")[0])
            self.assertFalse(mods & {"research", "runner", "screener", "shapely", "costar_import"},
                             (name, mods))
        zmods = {n.names[0].name for n in ast.walk(ast.parse((REPO_ROOT / "zoning.py").read_text()))
                 if isinstance(n, ast.Import)}
        self.assertNotIn("zoning_import", zmods)
        self.assertNotIn("prepare", zmods)   # zoning.py is a leaf: no DB, no network


if __name__ == "__main__":
    unittest.main()
