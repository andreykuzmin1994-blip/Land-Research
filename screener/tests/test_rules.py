"""Offline tests for the owner, zoning and scoring rules and the AutoResearch
evaluator's guards (no network, no data files, never the repo's own git).

    python -m unittest discover -s screener/tests -t .

Individual owner names here are made up.
"""

import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from screener import rank, scoring, screens, zoning
from screener.autoresearch import evaluate as ev
from screener.fulton import acquisition, sold_after
from screener.owners import owner_type, same_owner
from screener.tests.test_screener import SETTINGS, parcel


class AssociationOwnerTests(unittest.TestCase):
    def test_associations(self):
        for name in (
            "OAKS AT CEDAR GROVE HOMEOWNERS ASSOCIATION INC",
            "AMHURST HOMEOWNERS ASSN INC",
            "AMHURST HOMEOWNERS ASSOC INC",
            "GLENRIDGE PLACE HOME OWNERS ASSOCIATION INC",
            "DEEP CREEK HOMEOWNER'S ASSOCIATION INC",
            "MALLORY PARK HOMEOWNER ASSOCIATION PHASE 2 INC",
            "HOMEOWNERS ASSOCIATION OF DEERFIELD INC",
            "CASCADE PARC PROPERTY OWNERS ASSOCIATION INC",
            "AUTUMN LAKES COMMUNITY ASSOCIATION INC",
            "CHESTNUT RIDGE COMMUNITY ASSN INC",
            "ACADEMY PARK NEIGHBORHOOD ASSOC INC",
            "LAKES AT CEDAR GROVE NBHD ASSOC INC",
            "HERITAGE COMMONS CONDOMINIUM ASSOCIATION",
            "EASTSIDE CIVIC ASSOCIATION INC",
            "ARTISAN TOWNHOME ASSOCIATION INC",
            "JACKSON TRACE TOWNHOUSE ASSOC INC",
            "WILLOW CREEK HOMEOWNERS ASSOC",
            "HIGH GROVE HOA INC",
            "COTTAGES AT CASCADE HOA LLC THE",
        ):
            self.assertEqual(owner_type(name), "association", name)

    def test_not_associations(self):
        # HOA is also a given name, POA usually means power of attorney,
        # and ASSOC / ASSOCIATION alone name businesses and trade bodies.
        cases = {
            "NGUYEN HOA": "individual",
            "NGUYEN THI HOA": "individual",
            "HOA NGUYEN LLC": "entity",
            "DOE JOHN POA": "individual",
            "POA HOLDINGS LLC": "entity",
            "FEDERAL NATIONAL MORTGAGE ASSOCIATION": "entity",
            "WELLS FARGO BANK NATIONAL ASSOCIATION": "entity",
            "GEORGIA MUNICIPAL ASSOCIATION INC": "entity",
            "COMMUNITY ASSOCIATES LLC": "entity",
            "SMITH & ASSOCIATES LLC": "entity",
            "SOUTH VIEW CEMETERY ASSN": "religious",
        }
        for name, expected in cases.items():
            self.assertEqual(owner_type(name), expected, name)
        for name in ("ASSOCIATED CREDIT UNION", "CHESTER INDUSTRIAL PARK ASSOC L P",
                     "FOUR FIFTY PIEDMONT ASSOC"):
            self.assertNotEqual(owner_type(name), "association", name)

    def test_association_is_screened_out(self):
        hoa = parcel(owner="OAKS AT CEDAR GROVE HOMEOWNERS ASSOCIATION INC",
                     owner_type="association")
        # The existing settings dict (no association key) must work as is.
        why = screens.attribute_rejects(hoa, SETTINGS, {})
        self.assertEqual(len(why), 1)
        self.assertIn("association", why[0])
        off = dict(SETTINGS, exclude_association_owned=False)
        self.assertEqual(screens.attribute_rejects(hoa, off, {}), [])


class DeveloperOwnerTests(unittest.TestCase):
    def test_surnames_alone_are_people(self):
        for name in ("SCANNELL MARY", "DERMODY PATRICK", "BREIT JOHN", "SEEFRIED JAMES",
                     "WEEKS ROBINSON", "HILLWOOD JANE", "PANATTONI LUCA", "DALFEN MARY",
                     "EXETER SAMUEL R", "DOE JANE & SCANNELL MARY"):
            self.assertEqual(owner_type(name), "individual", name)
        self.assertEqual(owner_type("SCANNELL MARY ESTATE OF"), "estate")
        self.assertEqual(owner_type("DERMODY PATRICK REVOCABLE TRUST"), "trust")

    def test_developer_entities(self):
        for name in ("SCANNELL PROPERTIES #512 LLC", "DERMODY OPERATING CO",
                     "BREIT INDUSTRIAL CANYON GA1B01 LLC", "SEEFRIED INDUSTRIAL PROPERTIES INC",
                     "PANATTONI DEVELOPMENT CO", "HILLWOOD ENTERPRISES L P",
                     "DALFEN AMERICA CORP", "EXETER 5400 LLC", "EXETER 650 SELIG L P",
                     "WEEKS ROBINSON PROPERTIES", "PROLOGIS EXCHANGE", "PROLOGIS LP"):
            self.assertEqual(owner_type(name), "developer", name)


class GovernmentEstateTests(unittest.TestCase):
    def test_federal_agencies_are_government(self):
        for name in ("VETERANS ADMINISTRATION", "ADMINISTRATOR OF VETERANS AFFAIRS",
                     "SECRETARY OF VETERANS AFFAIRS",
                     "SECRETARY OF HOUSING AND URBAN DEVELOPMENT",
                     "SMALL BUSINESS ADMINISTRATION", "GENERAL SERVICES ADMINISTRATION",
                     "FEDERAL AVIATION ADMINISTRATION", "UNITED STATES OF AMERICA"):
            self.assertEqual(owner_type(name), "government", name)

    def test_estates_keep_their_type(self):
        for name in ("DOE JOHN ESTATE OF", "DOE JOHN EST OF", "ROE MARY ADMINISTRATOR",
                     "ROE MARY ADMINISTRATRIX", "ROE MARY & ROE PAUL ADMINISTRATORS",
                     "DOE JANE EXECUTRIX"):
            self.assertEqual(owner_type(name), "estate", name)


class SameOwnerTests(unittest.TestCase):
    def test_spec_example(self):
        self.assertFalse(same_owner("SOUTH FULTON LAND PARTNERS LLC", "SOUTH FULTON LAND CO"))
        self.assertFalse(same_owner("SOUTH FULTON LAND CO", "SOUTH FULTON LAND PARTNERS LLC"))

    def test_same_owner_written_differently(self):
        for a, b in (
            ("SABEN L L C", "SABEN, LLC"),                                   # legal form spelling
            ("ACME LAND INC", "ACME LAND INCORPORATED"),
            ("ACME FAIRBURN LIMITED PARTNERSHIP", "ACME FAIRBURN L P"),
            ("SMITH FAMILY TRUST THE", "SMITH FAMILY TRUST"),
            ("SOUTH FULTON LAND PARTNERS L L", "SOUTH FULTON LAND PARTNERS LLC"),   # deed cut at 30
            ("PIEDMONT INDUSTRIAL HOLDINGS L", "PIEDMONT INDUSTRIAL HOLDINGS LLC"),
            ("DOE JOHN", "JOHN DOE REVOCABLE TRUST THE"),                     # word order
            ("DOE JOHN", "DOE JOHN W"),                                       # initials
            ("DOE JOHN W", "DOE JOHN WILLIAM"),
            ("DOE JOHN", "DOE JOHN JR"),
            ("DOE JOHN", "DOE JOHN & MARY"),                                  # co-owner
            ("DOE MARY K", "DOE ROBERT T & MARY K"),
            ("DOE JOHN W", "JOHN W & MARY DOE LIVING TRUST THE"),             # own trust
            ("DOE JOHN ET AL", "DOE JOHN"),                                   # alias tails
            ("DOE JOHN  ET", "DOE JOHN"),
            ("DOE JOHN A/K/A DOE JON", "DOE JOHN"),
            ("JOHN DOE, AS EXECUTOR", "DOE JOHN"),                            # role words
            ("DOE JANE EXTR", "DOE JANE"),
            ("ROE-DOE MARY", "ROE DOE MARY"),                                 # punctuation
            ("MC ROE JOHN", "MCROE JOHN"),
            ("DOE JONATHAN", "DOE JONATHON"),                                 # typo
            ("ACME LAND LLC & DOE JOHN", "ACME LAND LLC"),                    # co-owned entity
            ("ACME LAND, LLC, A GEORGIA LIMI", "ACME LAND LLC"),
        ):
            self.assertTrue(same_owner(a, b), (a, b))
            self.assertTrue(same_owner(b, a), (b, a))

    def test_different_owners(self):
        for a, b in (
            ("SOUTH FULTON LAND LLC", "SOUTH FULTON LAND OWNER LLC"),         # another SPE
            ("FULTON INDUSTRIAL PARTNERS LLC", "FULTON INDUSTRIES INC"),      # 12-char prefix
            ("OAK RIDGE LLC", "OAK RIDGE HOMEOWNERS ASSOCIATION INC"),        # developer -> HOA
            ("JOHNS CREEK LLC", "OAKS AT JOHNS CREEK LLC"),
            ("EXETER 5400 LLC", "EXETER 5410 LLC"),                           # numbered vehicles
            ("RIVER EXCHANGE 6 LLC", "RIVER EXCHANGE DR LLC"),
            ("ACME FUND I LLC", "ACME FUND II LLC"),
            ("CITY OF ATLANTA", "BOARD OF EDUCATION OF THE CITY OF ATLANTA"),
            ("ROE JOHN", "ROE JAMES"),                                        # other people
            ("DOE JOHN R", "DOE JOHN W"),
            ("DOE JOHN JR", "DOE JOHN SR"),
            ("DOE JOHNATHAN R", "DOE JOHNATHAN W"),                         # not a typo
            ("DOE JOHNATHAN JR", "DOE JOHNATHAN SR"),
            ("ACME PARTNERS A LLC", "ACME PARTNERS B LLC"),
            ("DOE JANE M", "DOE JOHN R"),
            ("DOE JOHN", "DOE FAMILY TRUST"),
            ("PALMETTO BECKNELL INDUSTRI", "BOWEN ROAD ASSOCIATES LLC"),
            ("", "DOE JOHN"),
        ):
            self.assertFalse(same_owner(a, b), (a, b))
            self.assertFalse(same_owner(b, a), (b, a))

    def test_acquisition_keeps_the_purchase_from_a_look_alike(self):
        sales = [
            {"date": date(1990, 5, 1), "price": 100_000, "instrument": "Warranty Deed",
             "old_owner": "ROE MARY"},
            {"date": date(2015, 6, 1), "price": 2_000_000, "instrument": "Limited Warranty Deed",
             "old_owner": "SOUTH FULTON LAND PARTNERS LLC"},
        ]
        self.assertEqual(acquisition(sales, "SOUTH FULTON LAND CO")["date"], date(2015, 6, 1))
        # A different entity's sale is not the 2016 owner's sale.
        later = [{"date": date(2018, 3, 1), "price": 1_000_000, "instrument": "",
                  "old_owner": "SOUTH FULTON LAND PARTNERS LLC"}]
        self.assertFalse(sold_after(later, "SOUTH FULTON LAND CO", date(2016, 1, 1), 50_000))
        self.assertTrue(sold_after(later, "SOUTH FULTON LAND PARTNERS LLC", date(2016, 1, 1),
                                   50_000))


# Every future-land-use label the configured city layers returned when
# cached (2026-10-28), with the category it must map to.
REAL_FLU_LABELS = {
    # South Fulton FLUM 2045
    "Business Park": "industrial", "Community Live Work": "other",
    "Conserve/ Preserve": "conservation", "Industrial Mix-Use Zone": "industrial",
    "Industrial Zone": "industrial", "Regional Live Work": "other",
    "Rural/ Agricultural Neighborhood": "rural", "Suburban II Neighborhood": "residential",
    "Suburban Neighborhood": "residential", "VIllage Mixed Use": "other",
    # Fulton Industrial District land uses
    "Industrial, Open Space": "industrial",
    "Live-Work Community, Public, Semi-Public and Institutional, Open Space": "conservation",
    "Live-Work Regional, Public, Semi-Public and Institutional, Open Space": "conservation",
    "Retail Industrial, Commercial, Business Park, Public, Semi-Public and Institutional, "
    "Industrial, Open Space": "industrial",
    # Fairburn
    "Greenspace": "conservation", "Highway Mixed Use": "other", "Industrial": "industrial",
    "Low Density Residential": "residential", "Medium Density Residential": "residential",
    "Neighborhood Commercial": "other", "Office": "other", "Office/Industrial": "industrial",
    "Rural Residential": "rural", "TCMU": "other",
    # East Point
    "": "unknown", " ": "unknown", " MDR": "residential", " OP": "other", "BP": "industrial",
    "C": "other", "HC": "other", "HDR": "residential", "I": "industrial", "MDR": "residential",
    "MXD": "other", "MXDTC": "other", "NC": "other", "OP": "other", "PI": "other",
    "PRC": "other", "R": "residential", "TCU": "other",
    # Atlanta
    "Community Facilities": "other", "High-Density Commercial": "other",
    "High-Density Mixed Use": "other", "High-Density Mixed-Use": "other",
    "High-Density Residential": "residential", "Industrial-Mixed Use": "industrial",
    "Low-Density Commercial": "other", "Low-Density Mixed Use": "other",
    "Low-Density Mixed-Use": "other", "Low-Density Residential": "residential",
    "Medium-Density Mixed Use": "other", "Medium-Density Mixed-Use": "other",
    "Medium-Density Residential": "residential", "Mixed Use-High Density": "other",
    "Mixed Use-Low Density": "other", "Mixed Use-Medium Density": "other", "Mixed-Use": "other",
    "Mixed-Use High-Density": "other", "Mixed-Use Low-Density": "other",
    "Mixed-Use Medium-Density": "other", "Office/Institutional": "other",
    "Office/Institutional/Residential": "residential", "Open Space": "conservation",
    "Private Open Space": "conservation", "Single Family Residential": "residential",
    "Single-Family Residential": "residential",
    "Transportation/Communications/Utilities": "other",
    "Very High-Density Residential": "residential",
}


class FutureLandUseTests(unittest.TestCase):
    def test_real_vocabulary(self):
        for label, expected in REAL_FLU_LABELS.items():
            self.assertEqual(zoning.flu_category(label), expected, repr(label))

    def test_park_and_neighborhood_words(self):
        cases = {
            "Office Park": "other", "Parkway Commercial": "other", "Parking": "other",
            "Industrial Park": "industrial", "Business Park": "industrial",
            "Mobile Home Park": "residential", "Manufactured Home Park": "residential",
            "Light Manufacturing": "industrial", "Neighborhood Commercial": "other",
            "Neighborhood Mixed Use": "other", "Neighborhood Center": "other",
            "Traditional Neighborhood": "residential", "Neighborhood": "residential",
            "Park": "conservation", "Parks": "conservation", "Parks & Recreation": "conservation",
            "Parks/Open Space": "conservation", "Park and Open Space": "conservation",
            "Conservation": "conservation",
        }
        for label, expected in cases.items():
            self.assertEqual(zoning.flu_category(label), expected, label)

    def test_entitlement_no_longer_hard(self):
        for label in ("Office Park", "Parkway Commercial", "Neighborhood Commercial"):
            path = zoning.entitlement("rezonable", zoning.flu_category(label), 0)[0]
            self.assertEqual(path, "possible", label)


class LearnedScoreGuardTests(unittest.TestCase):
    def _score_with(self, value):
        out = {"score": value, "motivation": 10.0, "why_site": ["near an exit"], "why_seller": []}
        with mock.patch.object(rank, "learned_score", return_value=out):
            return scoring.score(parcel())

    def test_unusable_learned_score_counts_as_zero(self):
        fixed = scoring.fixed_score(parcel())[0]
        for bad in (float("nan"), float("inf"), float("-inf"), None, "high"):
            res = self._score_with(bad)
            self.assertTrue(math.isfinite(res["score"]), bad)
            self.assertEqual(res["score"], round(scoring.FIXED_SHARE * fixed, 1), bad)
            self.assertEqual(res["learned_score"], 0.0, bad)
            self.assertTrue(any("counted as 0" in w for w in res["why_site"]), bad)

    def test_normal_score_unchanged(self):
        fixed = scoring.fixed_score(parcel())[0]
        res = self._score_with(50.0)
        expected = scoring.FIXED_SHARE * fixed + (1 - scoring.FIXED_SHARE) * 50.0
        self.assertEqual(res["score"], round(expected, 1))
        self.assertEqual(res["learned_score"], 50.0)
        self.assertEqual(res["why_site"][-1], "near an exit")


def _pool():
    """200 synthetic records; positives are the ten highest 'signal' values in dev."""
    records = []
    for i in range(200):
        rec = parcel(exit_miles=0.5 + (i % 50) / 10.0)
        rec.update({"key": f"k{i:03d}", "split": "dev" if i % 4 else "test", "signal": i,
                    "block": f"b{i % 10}"})
        records.append(rec)
    dev = [r for r in records if r["split"] == "dev"]
    return records, {r["key"] for r in sorted(dev, key=lambda r: -r["signal"])[:10]}


class NonFiniteScoreTests(unittest.TestCase):
    def test_nan_mid_pool_is_counted(self):
        records, positives = _pool()

        def scorer(f):
            return {"score": float("nan") if f["signal"] == 101 else float(f["signal"])}

        res = ev.evaluate(records, positives, "dev", scorer=scorer, settings=SETTINGS)
        self.assertEqual(res["nonfinite_scores"], 1)
        self.assertTrue(all(math.isfinite(x) for x in res["score_range"]))
        for bad in (float("inf"), float("-inf")):
            res = ev.evaluate(records, positives, "dev", scorer=lambda f, b=bad: {"score": b},
                              settings=SETTINGS)
            self.assertEqual(res["nonfinite_scores"], 150)

    def test_finite_scores_report_zero(self):
        records, positives = _pool()
        res = ev.evaluate(records, positives, "dev", scorer=lambda f: {"score": f["signal"]},
                          settings=SETTINGS)
        self.assertEqual(res["nonfinite_scores"], 0)


CURRENT_RANK = ev.RANK_FILE.read_text() if hasattr(ev, "RANK_FILE") else ""


class RankPurityTests(unittest.TestCase):
    def test_current_rank_passes(self):
        self.assertEqual(ev.rank_problems(CURRENT_RANK), [])
        self.assertEqual(ev.rank_import_problems(), [])

    def test_math_is_allowed(self):
        self.assertEqual(ev.rank_problems(CURRENT_RANK + "\nimport math\nfrom math import exp\n"
                                          "X = math.log(2) + exp(1)\n"), [])
        doc = '"""Reads nothing from screener/autoresearch/data/."""\nW = 1\n'
        self.assertEqual(ev.rank_problems(doc), [])

    def test_refused_sources(self):
        for src in (
            "import os", "import sys", "import json", "import pathlib", "import importlib",
            "import gzip", "from os import path", "from . import scoring",
            "from screener import scoring", "from typing import sys", "from typing import *",
            "import typing\nx = typing.sys", "import typing as t\nx = t.collections",
            "x = open('k')", "x = eval('1')", "exec('1')", "x = compile('1', 'f', 'exec')",
            "x = __import__('os')", "x = getattr(1, 'real')", "x = globals()", "x = vars()",
            "x = locals()", "breakpoint()", "x = ().__class__", "x = __builtins__",
            "x = (1).real._y", "from typing import get_type_hints",
            "W = 1\ndef f():\n    global W\n    W = 2",
            "def f():\n    x = 1\n    def g():\n        nonlocal x\n",
            "P = 'screener/autoresearch/data/fulton_2016_labels.json'",
            "P = 'experiments.tsv'", "P = 'best_ranking.json'", "def f(:",
            "from typing import _eval_type",
            # The scorer's caller frames hold the evaluator's records and answer key.
            "def g():\n    me = yield\n    yield me.gi_frame.f_back.f_back.f_locals",
            "def g():\n    me = yield\n    yield '{0.gi_frame.f_back}'.format(me)",
            "x = '{0.f_back}'.format_map",
            "match len:\n    case object(__self__=b):\n        pass",
        ):
            self.assertTrue(ev.rank_problems(src), src)

    def test_shadowed_rank_module_is_refused(self):
        shadow = "/elsewhere/rank.cpython-313-x86_64-linux-gnu.so"
        with mock.patch.object(ev, "_rank_origin", return_value=shadow):
            self.assertTrue(ev.rank_import_problems())
        with mock.patch.object(ev, "_rank_origin", return_value=None):
            self.assertTrue(ev.rank_import_problems())

    def test_refused_before_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp, "ran")
            fixture = Path(tmp, "rank.py")
            fixture.write_text(f"import pathlib\npathlib.Path({str(marker)!r}).write_text('x')\n")
            for argv in ([], ["--decide"], ["--test"]):
                with mock.patch.object(ev, "RANK_FILE", fixture), \
                        mock.patch.object(ev, "_rank_origin", return_value=str(fixture)), \
                        mock.patch.object(ev, "evaluate") as scored, \
                        mock.patch.object(ev, "load") as loaded, \
                        mock.patch.object(ev, "decide") as decided, \
                        mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                    self.assertEqual(ev.main(argv), 3, argv)
                    self.assertIn("REFUSED", out.getvalue())
                    scored.assert_not_called()
                    loaded.assert_not_called()
                    decided.assert_not_called()
            self.assertFalse(marker.exists())


class FakeRepo:
    """Linear git history for decide(): commits with the files each touched."""

    def __init__(self):
        self.history: list[tuple[str, list[str]]] = []
        self.dirty: list[str] = []
        self.broken = False

    def commit(self, name, *files):
        self.history.append((name, list(files) or ["screener/rank.py"]))

    def reset(self, name):
        names = [c for c, _ in self.history]
        self.history = self.history[:names.index(name) + 1]

    def git(self, *args):
        if self.broken:
            return ""
        names = [c for c, _ in self.history]
        under = "--" in args and args[args.index("--") + 1:] == ("screener",)
        if args[:2] == ("rev-parse", "--short"):
            back = 1 if args[2] == "HEAD~1" else 0
            return names[-1 - back] if len(names) > back else ""
        if args[0] == "status":
            lines = [f for f in self.dirty
                     if "--untracked-files=all" in args or not f.startswith("??")]
            return "\n".join((["## main"] if "--branch" in args else []) + lines)
        if args[0] == "diff":
            if args[2] == "HEAD~1":
                files = self.history[-1][1]
            else:
                after = self.history[names.index(args[2]) + 1:] if args[2] in names else []
                files = sorted({f for _, fs in after for f in fs})
            return "\n".join(f for f in files if not under or f.startswith("screener/"))
        if args[0] == "log":
            return "exp: test"
        return ""

    def is_ancestor(self, commit):
        return commit in [c for c, _ in self.history]


class DecideTests(unittest.TestCase):
    """decide() against a fake git history and a temporary log (SR-6: never the real files)."""

    def setUp(self):
        self.records, self.positives = _pool()
        dev = [r for r in self.records if r["split"] == "dev"]
        self.good = [r["key"] for r in sorted(dev, key=lambda r: -r["signal"])]
        self.bad = list(reversed(self.good))
        self.repo = FakeRepo()
        self.tmp = tempfile.TemporaryDirectory()
        self.real = [(p, p.exists()) for p in (ev.LOG_FILE, ev.BEST_FILE)]
        self.log = Path(self.tmp.name, "experiments.tsv")
        self.best = Path(self.tmp.name, "best_ranking.json")
        patches = [mock.patch("sys.stdout", new_callable=io.StringIO),
                   mock.patch.object(ev, "LOG_FILE", self.log),
                   mock.patch.object(ev, "BEST_FILE", self.best),
                   mock.patch.object(ev, "fingerprint", return_value="fp1"),
                   mock.patch.object(ev, "_git", side_effect=lambda *a: self.repo.git(*a)),
                   mock.patch.object(ev, "_is_ancestor",
                                     side_effect=lambda c: self.repo.is_ancestor(c))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()
        for path, existed in self.real:
            self.assertEqual(path.exists(), existed, f"{path} was touched")

    def fresh(self):
        self.repo = FakeRepo()
        for path in (self.log, self.best):
            if path.exists():
                path.unlink()

    def run_decide(self, hits, order=None, new_baseline=False, **extra):
        dev = {"hits": hits, "positives": 10, "k": 3, "recall": round(hits / 10, 3),
               "score_range": [0.0, 90.0], "order": order or self.bad, "nonfinite_scores": 0}
        dev.update(extra)
        return ev.decide(self.records, self.positives, dev, new_baseline)

    def statuses(self):
        return [r["status"] for r in ev._rows()]

    def assert_refused_without_writes(self, hits=3, **kw):
        before_log = self.log.read_text() if self.log.exists() else None
        before_best = self.best.read_text() if self.best.exists() else None
        self.assertEqual(self.run_decide(hits, order=self.good, **kw), 3)
        self.assertEqual(self.log.read_text() if self.log.exists() else None, before_log)
        self.assertEqual(self.best.read_text() if self.best.exists() else None, before_best)

    def baseline_then_experiment(self):
        self.repo.commit("aaa")
        self.assertEqual(self.run_decide(2), 0)
        self.repo.commit("bbb")

    def test_first_run_records_baseline_then_keeps(self):
        self.baseline_then_experiment()
        self.assertEqual(self.run_decide(3, order=self.good), 0)
        self.assertEqual(self.statuses(), ["baseline", "keep"])
        self.assertEqual(json.loads(self.best.read_text())["commit"], "bbb")
        self.repo.commit("ccc")
        self.assertEqual(self.run_decide(3, order=self.good), 0)
        self.assertEqual(self.statuses(), ["baseline", "keep", "discard"])

    def test_new_baseline_resets_the_bar(self):
        for old_hits, new_hits in ((2, 2), (5, 2)):
            with self.subTest(old_hits=old_hits):
                self.fresh()
                self.repo.commit("aaa")
                self.assertEqual(self.run_decide(old_hits), 0)
                self.repo.commit("bbb", "screener/autoresearch/evaluate.py", "screener/screens.py")
                self.assert_refused_without_writes()
                self.assertEqual(self.run_decide(new_hits, new_baseline=True), 0)
                self.repo.commit("ccc")
                self.assertEqual(self.run_decide(new_hits + 1, order=self.good), 0)
                self.assertEqual(self.statuses(), ["baseline", "baseline", "keep"])

    def test_missing_corrupt_or_mismatched_best_file_is_refused(self):
        for damage in ("missing", "corrupt", "other commit", "other set"):
            with self.subTest(damage=damage):
                self.fresh()
                self.baseline_then_experiment()
                saved = json.loads(self.best.read_text())
                if damage == "missing":
                    self.best.unlink()
                elif damage == "corrupt":
                    self.best.write_text("{")
                elif damage == "other commit":
                    self.best.write_text(json.dumps(dict(saved, commit="zzz")))
                else:
                    self.best.write_text(json.dumps(dict(saved, fingerprint="fp0")))
                self.assert_refused_without_writes()
                # The human's escape hatch still records.
                self.assertEqual(self.run_decide(3, new_baseline=True), 0)
                self.assertEqual(self.statuses(), ["baseline", "baseline"])
                self.assertEqual(json.loads(self.best.read_text())["commit"], "bbb")

    def test_rows_outside_history_are_refused(self):
        self.repo.commit("aaa")
        self.assertEqual(self.run_decide(2), 0)
        self.repo.history = [("xxx", ["screener/rank.py"])]          # branch from before aaa
        self.repo.commit("yyy")
        self.assert_refused_without_writes()

    def test_deleted_log_with_best_file_is_refused(self):
        self.baseline_then_experiment()
        self.log.unlink()
        self.assert_refused_without_writes()

    def test_new_baseline_allowed_on_a_scored_commit(self):
        self.repo.commit("aaa")
        self.assertEqual(self.run_decide(2), 0)
        self.assertEqual(self.run_decide(2), 3)
        self.assertEqual(self.run_decide(2, new_baseline=True), 0)

    def test_git_failure_fails_closed(self):
        self.repo.commit("aaa")
        self.repo.broken = True
        self.assert_refused_without_writes()

    def test_dirty_or_untracked_files_are_refused(self):
        self.baseline_then_experiment()
        for line in ("?? screener/helper.py", " M screener/rank.py"):
            self.repo.dirty = [line]
            self.assert_refused_without_writes()

    def test_commit_must_touch_only_rank(self):
        self.repo.commit("aaa")
        self.assertEqual(self.run_decide(2), 0)
        self.repo.commit("bbb", "helper.py", "screener/rank.py")
        self.assert_refused_without_writes()

    def test_nonfinite_scores_are_refused(self):
        self.repo.commit("aaa")
        self.assert_refused_without_writes(nonfinite_scores=1)
        self.assert_refused_without_writes(new_baseline=True, nonfinite_scores=1)

    def test_status_command_lists_untracked_but_not_ignored(self):
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        with tempfile.TemporaryDirectory() as tmp:
            def status():
                subprocess.run(["git", "init", "-q"], cwd=tmp, env=env, check=True)
                out = subprocess.run(["git", *ev.STATUS_ARGS], cwd=tmp, env=env,
                                     capture_output=True, text=True, check=True).stdout
                return out.splitlines()
            Path(tmp, ".gitignore").write_text("cache/\n*.tsv\n__pycache__/\n")
            Path(tmp, "screener", "cache").mkdir(parents=True)
            Path(tmp, "screener", "cache", "helper.py").write_text("")
            Path(tmp, "screener", "experiments.tsv").write_text("")
            lines = status()
            self.assertTrue(lines[0].startswith("##"))
            self.assertEqual(lines[1:], [])
            Path(tmp, "screener", "helper.py").write_text("")
            self.assertEqual(status()[1:], ["?? screener/helper.py"])


class OwnersWordAssociationTests(unittest.TestCase):
    """Owners' associations the first rule missed among ranked parcels of a
    full Fulton run, and the partnerships and church bodies that share
    their words."""

    def test_owners_word_before_association(self):
        # Misspelt and glued HOMEOWNERS, and OWNERS after any name.
        for name in (
            "ASBURY PARK HOMEOWNRS ASSOCIATION INC",
            "CREEL WALK HOMEOWERS ASSOCIATION INC",
            "DUPONT COMMONS HOMESOWNERS ASSN INC",
            "COMMUNITIES AT PRINCETON LAKES HMOWNERS ASSN INC THE",
            "FIELDSTONE MANOR TOWNHOMEOWNERS ASSOCIATION INC",
            "CASCADE UNIT OWNERS ASSOCIATION INC",
            "GIVERNY AT LE JARDIN OWNERS ASSN INC",
            "TAPESTRY AT LE JARDIN OWNERS ASSOCIATION INC",
            "VILLAGES OF EAST POINT OWNERS ASSOCIATION INC THE",
            "OAKLEY INDUSTRIAL PARK OWNERS ASSOCIATION INC",
            "MAPLE RIDGE LOT OWNERS ASSOC",
            "DISTRICT OWNERS' ASSOCIATION, INC.",
            "BIRCH HILL UNIT OWNER'S ASSN INC",
            "OWNERS ASSOCIATION OF BIRCH HILL INC",
        ):
            self.assertEqual(owner_type(name), "association", name)

    def test_town_association(self):
        # A planned community's town association, also glued to its name.
        for name in ("SERENBE TOWN ASSOCIATION", "SERENBE TOWN ASSOCIATION INC",
                     "SERENBE TOWN ASSOCIATION, INC.", "SERENBETOWN ASSOCIATION INC",
                     "MAPLE TOWN ASSN INC"):
            self.assertEqual(owner_type(name), "association", name)

    def test_partnerships_stay_out(self):
        # ASSOC after a place or a town's name is usually ASSOCIATES; a
        # partnership's legal form wins over any association word.
        cases = {
            "ACKERMAN MIDTOWN ASSOC LTD": "entity",
            "ACME MIDTOWN ASSOC II LLC": "entity",
            "OLD TOWN ASSOC": "entity",
            "CHESTER INDUSTRIAL PARK ASSOC": "entity",
            "CHESTER INDUSTRIAL PARK ASSOC L P": "entity",
            "CHESTER INDUSTRIAL PARK ASSOCIATES L P": "entity",
            "FOUR FIFTY PIEDMONT ASSOC": "entity",
            "LENOX TOWERS ASSOC LTD PART": "entity",
            "RIVER TOWERS ASSOCIATION INC": "entity",
            "JONES & BOWERS ASSOC": "entity",
            "ACME HOMES ASSOC L P": "entity",
            "ACME OWNERS ASSOC LLLP": "entity",
            "MAPLE COMMUNITY ASSOC L L P": "entity",
        }
        for name, expected in cases.items():
            self.assertEqual(owner_type(name), expected, name)

    def test_conference_association_is_religious(self):
        for name in ("SOUTH ATLANTIC CONFERENCE ASSOC",
                     "SOUTH ATLANTIC CONFERENCE ASSN OF SEVENTH DAY ADVENTISTS",
                     "SOUTH ATLANTIC CONFERENCE ASSOC OF SEVENTH DAY AVENTISTS",
                     "SOUTH ATLANTIC CONF ASSN OF SEVENTH DAY ADVENTIST"):
            self.assertEqual(owner_type(name), "religious", name)
        # CONFERENCE alone is not a church word.
        self.assertEqual(owner_type("EXECUTIVE CONFERENCE CTR INC"), "entity")
        self.assertEqual(owner_type("MAPLE CONFERENCE CENTER LLC"), "entity")

    def test_place_name_association_stays_entity(self):
        # No owners' or community word: the name alone can't tell an HOA
        # from a trade body or club, so it keeps the conservative type.
        for name in ("COLDWATER CREEK ASSOCIATION INC", "GEORGIA MUNICIPAL ASSOCIATION INC",
                     "BRIGHTER DAYS ASSOCIATION INC"):
            self.assertEqual(owner_type(name), "entity", name)

    def test_new_forms_are_screened_out(self):
        for name in ("OAKLEY INDUSTRIAL PARK OWNERS ASSOCIATION INC",
                     "SERENBETOWN ASSOCIATION INC"):
            p = parcel(owner=name, owner_type=owner_type(name))
            why = screens.attribute_rejects(p, SETTINGS, {})
            self.assertEqual(len(why), 1, name)
            self.assertIn("association", why[0])
        p = parcel(owner="CHESTER INDUSTRIAL PARK ASSOC L P",
                   owner_type=owner_type("CHESTER INDUSTRIAL PARK ASSOC L P"))
        self.assertEqual(screens.attribute_rejects(p, SETTINGS, {}), [])


if __name__ == "__main__":
    unittest.main()
