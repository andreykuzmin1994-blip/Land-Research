"""The evaluation key and its score (reviews/36_screener_eval_cohorts_metric):
several as-of years, the sale-before-the-building rule, site weights, the
graded score and the keep rule. Offline; synthetic pools, plus shape checks
of the committed data files that read no feature of any positive."""

from __future__ import annotations

import contextlib
import csv
import gzip
import io
import json
import math
import random
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from screener.autoresearch import build_eval as be
from screener.autoresearch import evaluate as ev

SETTINGS = {"max_flood_pct": 40, "max_floodway_pct": 10, "max_wetland_pct": 20,
            "max_protected_pct": 10, "max_steep_pct": 40, "min_usable_acres": 4}
CLEAN = {"flood_pct": 0.0, "floodway_pct": 0.0, "wetland_pct": 0.0, "protected_pct": 0.0,
         "easement_pct": 0.0, "usable_acres": 20.0, "steep_pct": None}


def deed(day, price, old_owner, instrument="Warranty Deed"):
    return {"date": date.fromisoformat(day), "price": float(price), "instrument": instrument,
            "old_owner": old_owner}


def pool(years=(2015, 2016), per_year=100, blocks=10):
    """Synthetic records: `signal` is the true order; site i is the same
    ground in every year (same block)."""
    records = []
    for year in years:
        for i in range(per_year):
            records.append({**CLEAN, "key": f"{year}-{i:03d}", "cohort": year, "split": "dev",
                            "block": f"b{i % blocks}", "signal": float(i)})
    return records


class CreditTests(unittest.TestCase):
    def test_curve(self):
        self.assertEqual(ev.credit(0, 4), 1.0)
        self.assertEqual(ev.credit(3, 4), 1.0)                 # the last of the top K
        self.assertAlmostEqual(ev.credit(4, 4), 1 - math.log(5 / 4) / math.log(10))
        self.assertAlmostEqual(ev.credit(39, 4), 0.0)          # position 10 x K
        self.assertEqual(ev.credit(400, 4), 0.0)
        steps = [ev.credit(a, 4) for a in range(60)]
        self.assertEqual(steps, sorted(steps, reverse=True))   # never rises with rank
        # Half credit near 3.2 x K.
        self.assertAlmostEqual(ev.credit(round(4 * 10 ** 0.5) - 1, 4), 0.5, delta=0.02)

    def test_nothing_is_paid_below_ten_times_k(self):
        order = [f"k{i}" for i in range(1000)]
        counts = {0: {k: 1 for k in order}}                    # K = 20
        for position, expected in ((198, True), (199, False), (600, False)):   # 0-based
            hits, graded, k = ev.score_orders({0: order}, counts, {order[position]: 1.0})
            self.assertEqual(k, 20)
            self.assertEqual(graded > 0, expected, position)
            self.assertEqual(hits, 0)


class ScoreTests(unittest.TestCase):
    def test_weights_make_a_site_sum_to_one(self):
        records = pool()
        # One site, positive in both years, ranked first in each: one site of credit.
        weights = {"2015-099": 0.5, "2016-099": 0.5}
        res = ev.evaluate(records, weights, "dev", scorer=lambda f: {"score": f["signal"]},
                          settings=SETTINGS, fields=("signal",))
        self.assertEqual((res["positives"], res["sites"]), (2, 1.0))
        self.assertAlmostEqual(res["hits"], 1.0)
        self.assertAlmostEqual(res["graded"], 1.0)
        self.assertEqual(res["k"], 4)                           # 2% of 100, in each of two years

    def test_ranking_is_within_each_year(self):
        records = pool()
        # 2016 scores are all far above 2015's; within-year order is what counts.
        def scorer(f):
            return {"score": f["signal"] / 10.0 + (50.0 if f["year_hint"] else 0.0)}
        for r in records:
            r["year_hint"] = r["cohort"] == 2016
        res = ev.evaluate(records, {"2015-099": 1.0}, "dev", scorer=scorer, settings=SETTINGS,
                          fields=("signal", "year_hint"))
        self.assertAlmostEqual(res["graded"], 1.0)
        self.assertEqual(sorted(res["order"]), [2015, 2016])
        self.assertEqual(res["order"][2015][0], "2015-099")

    def test_scorer_gets_only_the_allow_list(self):
        records = pool()
        for r in records:
            r.update(weight=1.0, site="s", secret="x")
        seen = []

        def spy(features):
            seen.append(tuple(sorted(features)))
            return {"score": 0.0}
        ev.evaluate(records, set(), "dev", scorer=spy, settings=SETTINGS,
                    fields=("signal", "flood_pct", "not_in_record"))
        self.assertEqual(set(seen), {("flood_pct", "not_in_record", "signal")})
        with self.assertRaises(TypeError):          # no default: the old deny-list is gone
            ev.evaluate(records, set(), "dev", scorer=spy, settings=SETTINGS)
        with self.assertRaises(TypeError):
            ev.ranking(records, "dev", spy, SETTINGS)

    def test_screened_out_positive_earns_nothing(self):
        records = pool()
        next(r for r in records if r["key"] == "2015-099")["flood_pct"] = 90.0
        res = ev.evaluate(records, {"2015-099": 1.0}, "dev",
                          scorer=lambda f: {"score": f["signal"]}, settings=SETTINGS,
                          fields=("signal",))
        self.assertEqual((res["hits"], res["graded"], res["positives_screened_out"]), (0, 0, 1))

    def test_bootstrap_copies(self):
        order = ["a", "b", "c", "d"]
        counts = {0: {"a": 2, "c": 98}}                         # K = 2
        hits, graded, _ = ev.score_orders({0: order}, counts, {"a": 1.0})
        self.assertEqual((hits, graded), (2.0, 2.0))
        counts = {0: {"b": 1, "a": 3, "c": 96}}                 # "a" after one parcel
        hits, graded, _ = ev.score_orders({0: ["b", "a", "c"]}, counts, {"a": 1.0})
        self.assertEqual(hits, 1.0)                             # one copy inside the top 2
        self.assertAlmostEqual(graded, 1 + ev.credit(2, 2) + ev.credit(3, 2))

    def test_a_cell_is_drawn_with_all_its_years(self):
        records = pool()
        blocks = ev._blocks(records, "dev")
        self.assertEqual(sorted(blocks["b3"]), [2015, 2016])
        for counts in list(ev._draws(blocks, random.Random(1)))[:20]:
            a = {k.split("-")[1]: n for k, n in counts[2015].items()}
            b = {k.split("-")[1]: n for k, n in counts[2016].items()}
            self.assertEqual(a, b)                              # the same sites, the same copies


class KeepRuleTests(unittest.TestCase):
    def setUp(self):
        self.records = pool(per_year=200, blocks=20)
        dev = sorted((r for r in self.records), key=lambda r: -r["signal"])
        self.pos = {r["key"]: 0.5 for r in dev if r["signal"] >= 190}     # 10 sites, both years
        self.perfect = self.orders(lambda r: -r["signal"])
        self.worst = self.orders(lambda r: r["signal"])

    def orders(self, sort_key):
        out = {}
        for year in (2015, 2016):
            rows = [r for r in self.records if r["cohort"] == year]
            out[year] = [r["key"] for r in sorted(rows, key=sort_key)]
        return out

    def test_clear_improvement_is_kept(self):
        v = ev.keep_verdict(self.records, self.pos, self.perfect, self.worst)
        self.assertTrue(v["keep"])
        self.assertGreater(v["gain"], ev.MIN_GAIN)
        self.assertEqual(v["win_share"], 1.0)

    def test_same_ranking_and_worse_ranking_are_not_kept(self):
        self.assertFalse(ev.keep_verdict(self.records, self.pos, self.perfect, self.perfect)["keep"])
        v = ev.keep_verdict(self.records, self.pos, self.worst, self.perfect)
        self.assertFalse(v["keep"])
        self.assertLess(v["gain"], 0)

    def test_gain_below_the_floor_is_not_kept(self):
        # One site dropped from first place to 30th in both years: putting
        # it back is a real gain, but under MIN_GAIN.
        base = {y: list(o) for y, o in self.perfect.items()}
        worse = {y: o[1:30] + o[:1] + o[30:] for y, o in base.items()}
        v = ev.keep_verdict(self.records, self.pos, base, worse)
        self.assertGreater(v["gain"], 0)
        self.assertLess(v["gain"], ev.MIN_GAIN)
        self.assertFalse(v["keep"])

    def test_hits_may_not_fall(self):
        with mock.patch.object(ev, "score_orders",
                               side_effect=[(1.0, 9.0, 8), (2.0, 5.0, 8)]), \
                mock.patch.object(ev, "paired_win_share", return_value=1.0):
            v = ev.keep_verdict(self.records, self.pos, self.perfect, self.worst)
        self.assertEqual((v["gain"], v["hits_change"], v["keep"]), (4.0, -1.0, False))

    def test_win_share_must_reach_the_bar(self):
        with mock.patch.object(ev, "paired_win_share", return_value=ev.WIN_SHARE_TO_KEEP - 0.01):
            self.assertFalse(ev.keep_verdict(self.records, self.pos, self.perfect,
                                             self.worst)["keep"])

    def test_thresholds_are_the_reviewed_ones(self):
        # Changing these needs the null test re-run and recorded (reviews/36).
        self.assertEqual((ev.MIN_GAIN, ev.WIN_SHARE_TO_KEEP, ev.CREDIT_SPAN, ev.K_SHARE,
                          ev.BOOTSTRAP), (2.0, 0.99, 10.0, 0.02, 500))
        self.assertGreaterEqual(ev.NULL_TRIALS, 1000)

    def test_null_test_is_deterministic_and_counts_false_keeps(self):
        base = {r["key"]: r["signal"] / 2.0 for r in self.records}
        with mock.patch.object(ev, "BOOTSTRAP", 20):
            a = ev.null_test(self.records, self.pos, base, 6, 4.0)
            b = ev.null_test(self.records, self.pos, base, 6, 4.0)
        self.assertEqual(a, b)
        self.assertEqual((a["trials"], a["noise"], a["mode"]), (6, 4.0, "independent"))
        self.assertTrue(0 <= a["false_keeps"] <= 6)

    def test_null_test_compares_two_different_rankings_and_ties_are_not_wins(self):
        base = {r["key"]: r["signal"] / 2.0 for r in self.records}
        seen = []
        real = ev.keep_verdict

        def spy(records, positives, new, best, split="dev"):
            seen.append(new != best)
            return real(records, positives, new, best, split)

        with mock.patch.object(ev, "BOOTSTRAP", 5), mock.patch.object(ev, "keep_verdict", spy):
            for mode in ev.NULL_MODES:
                ev.null_test(self.records, self.pos, base, 3, 8.0, mode=mode)
        self.assertEqual(seen, [True] * 6)
        with self.assertRaises(ValueError):
            ev.null_test(self.records, self.pos, base, 3, 8.0, mode="other")
        # A ranking never beats itself: a tie is not a win.
        self.assertEqual(ev.paired_win_share(self.records, self.pos, self.perfect, self.perfect), 0.0)

    def test_cell_shared_noise_moves_a_cell_together_in_every_year(self):
        base = {r["key"]: 50.0 for r in self.records}
        orders = []
        with mock.patch.object(ev, "keep_verdict",
                               side_effect=lambda rec, pos, new, best, split="dev": (
                                   orders.extend([new, best]) or {"keep": False, "gain": 0.0})):
            ev.null_test(self.records, self.pos, base, 1, 1000.0, mode="cell-shared")
        block = {r["key"]: r["block"] for r in self.records}

        def best_cell(order):
            places: dict[str, list[int]] = {}
            for place, key in enumerate(order):
                places.setdefault(block[key], []).append(place)
            return min(places, key=lambda b: sum(places[b]))

        # Half the noise is one draw per cell, the same in every year, so the
        # cell that comes out on top is the same cell in both years.
        self.assertEqual(best_cell(orders[0][2015]), best_cell(orders[0][2016]))
        self.assertEqual(best_cell(orders[1][2015]), best_cell(orders[1][2016]))
        self.assertNotEqual(orders[0][2015], orders[1][2015])


class DecideMechanicsTests(unittest.TestCase):
    """decide() pieces that are new with the graded score."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name, "experiments.tsv")
        self.best = Path(self.tmp.name, "best_ranking.json")
        self.records = pool(per_year=200, blocks=20)
        self.pos = {r["key"]: 0.5 for r in self.records if r["signal"] >= 190}
        self.commits = ["aaa"]
        for p in (mock.patch.object(ev, "LOG_FILE", self.log),
                  mock.patch.object(ev, "BEST_FILE", self.best),
                  mock.patch.object(ev, "fingerprint", return_value="fp1"),
                  mock.patch.object(ev, "REVIEW_PENDING", ""),
                  mock.patch.object(ev, "_is_ancestor", side_effect=lambda c: c in self.commits),
                  mock.patch.object(ev, "_git", side_effect=self.git),
                  mock.patch("sys.stdout", new_callable=io.StringIO)):
            self.out = p.start()
            self.addCleanup(p.stop)

    def git(self, *args):
        if args[:2] == ("rev-parse", "--short"):
            back = 1 if args[2] == "HEAD~1" else 0
            return self.commits[-1 - back] if len(self.commits) > back else ""
        if args[0] == "status":
            return "## main"
        if args[0] == "diff":
            return "screener/rank.py" if "HEAD~1" in args else ""
        return "exp: test"

    def dev(self, sort_key):
        orders = {}
        for year in (2015, 2016):
            rows = [r for r in self.records if r["cohort"] == year]
            orders[year] = [r["key"] for r in sorted(rows, key=sort_key)]
        hits, graded, k = ev.score_orders(orders, ev.split_pools(self.records, "dev"), self.pos)
        return {"hits": hits, "graded": graded, "sites": 10.0, "positives": 20, "k": k,
                "score_range": [0.0, 90.0], "order": orders, "nonfinite_scores": 0}

    def test_baseline_keep_and_discard_with_rounded_log(self):
        self.assertEqual(ev.decide(self.records, self.pos, self.dev(lambda r: r["signal"]), False), 3)
        self.assertEqual(ev.decide(self.records, self.pos, self.dev(lambda r: r["signal"]), True), 0)
        self.commits.append("bbb")
        self.assertEqual(ev.decide(self.records, self.pos, self.dev(lambda r: -r["signal"]), False), 0)
        self.commits.append("ccc")
        self.assertEqual(ev.decide(self.records, self.pos, self.dev(lambda r: -r["signal"]), False), 0)
        with open(self.log, newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
        self.assertEqual([r["status"] for r in rows], ["baseline", "keep", "discard"])
        self.assertEqual(list(rows[0]), ev.LOG_COLUMNS)
        for r in rows:                                   # one decimal, never more
            self.assertRegex(r["graded"], r"^\d+\.\d$")
            self.assertRegex(r["hits"], r"^\d+\.\d$")
        saved = json.loads(self.best.read_text())
        self.assertEqual((saved["commit"], sorted(saved["order"])), ("bbb", ["2015", "2016"]))
        text = self.out.getvalue()
        self.assertIn("DECISION: KEEP", text)
        self.assertIn("needs +2 graded, hits not lower, and a win in 99% of resamples", text)
        self.assertNotIn("wins", text)

    def test_a_log_from_the_old_evaluator_is_refused(self):
        self.log.write_text("time\tcommit\tparent\tfingerprint\thits\tpositives\tk\trecall\t"
                            "win_share\tstatus\tdescription\n")
        before = self.log.read_text()
        self.assertEqual(ev.decide(self.records, self.pos, self.dev(lambda r: r["signal"]), False), 3)
        self.assertIn("another version of this evaluator", self.out.getvalue())
        self.assertEqual(self.log.read_text(), before)
        self.assertFalse(self.best.exists())
        # Not even --new-baseline appends under the wrong columns.
        self.assertEqual(ev.decide(self.records, self.pos, self.dev(lambda r: r["signal"]), True), 3)

    def test_a_changed_key_needs_a_human_baseline(self):
        self.assertEqual(ev.decide(self.records, self.pos, self.dev(lambda r: r["signal"]), True), 0)
        self.commits.append("bbb")
        with mock.patch.object(ev, "fingerprint", return_value="fp2"):
            self.assertEqual(ev.decide(self.records, self.pos, self.dev(lambda r: -r["signal"]),
                                       False), 3)
            self.assertIn("the evaluation set changed", self.out.getvalue())
            self.assertEqual(ev.decide(self.records, self.pos, self.dev(lambda r: -r["signal"]),
                                       True), 0)

    def test_a_pending_review_refuses_every_decide_and_writes_nothing(self):
        with mock.patch.object(ev, "REVIEW_PENDING", "review pending: stop"):
            for new_baseline in (False, True):
                self.assertEqual(ev.decide(self.records, self.pos,
                                           self.dev(lambda r: -r["signal"]), new_baseline), 3)
        self.assertIn("REFUSED: review pending: stop.", self.out.getvalue())
        self.assertFalse(self.log.exists())
        self.assertFalse(self.best.exists())

    def test_saved_best_must_hold_per_year_rankings(self):
        for bad in ('{"commit": "a", "order": ["k1"]}', '{"commit": "a", "order": {"x": ["k"]}}',
                    '{"commit": "a", "order": {"2015": 3}}', "{"):
            with self.subTest(bad=bad):
                self.best.write_text(bad)
                self.assertIsNone(ev._saved_best())
        self.best.write_text('{"commit": "a", "order": {"2015": ["k1"]}}')
        self.assertEqual(ev._saved_best()["order"], {2015: ["k1"]})


class MainTests(unittest.TestCase):
    """main() on the committed key, with a spy for rank.learned_score and the
    log in a temporary folder. Reads no feature of any positive."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name, "experiments.tsv")
        self.seen: set[tuple[str, ...]] = set()

        def spy(features):
            self.seen.add(tuple(sorted(features)))
            return {"score": 50.0 + (features.get("acres") or 0.0) / 7.0}

        self.spy = spy
        for p in (mock.patch.object(ev, "LOG_FILE", self.log),
                  mock.patch.object(ev, "BEST_FILE", Path(self.tmp.name, "best_ranking.json")),
                  mock.patch.object(ev, "foreign_modules", return_value=[]),
                  mock.patch.object(ev, "load_scorer", side_effect=lambda source: self.spy),
                  mock.patch.object(ev, "second_pass", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ev.main(list(argv))
        return code, out.getvalue()

    def rows(self):
        with open(self.log, newline="") as fh:
            return list(csv.DictReader(fh, delimiter="\t"))

    def test_rank_py_is_handed_exactly_the_allow_list(self):
        code, _ = self.run_main()
        self.assertEqual(code, 0)
        meta = ev.load_meta()
        self.assertEqual(self.seen, {tuple(sorted(meta["features"] + meta["banned"]))})
        self.assertFalse(set(ev.HIDDEN) & set(meta["features"] + meta["banned"]))

    def test_a_look_shows_one_decimal_and_leaves_a_row(self):
        code, text = self.run_main()
        shown = json.loads(text)["dev"]
        for name in ("graded", "hits"):
            self.assertEqual(shown[name], round(shown[name], 1))
        self.assertFalse({"recall", "order", "score_span"} & set(shown))
        self.assertEqual(shown["unstable_scores"], 0)
        row, = self.rows()
        self.assertEqual((row["status"], row["win_share"]), ("look", ""))
        self.assertRegex(row["description"], r"^rank\.py [0-9a-f]{12}( \(uncommitted\))?$")
        self.assertEqual(float(row["graded"]), shown["graded"])
        self.run_main()
        self.assertEqual([r["status"] for r in self.rows()], ["look", "look"])

    def test_a_look_is_refused_when_it_cannot_be_logged(self):
        self.log.write_text("time\tcommit\tstatus\n")
        code, text = self.run_main()
        self.assertEqual(code, 3)
        self.assertIn("REFUSED", text)
        self.assertNotIn("graded", text)

    def test_a_look_at_an_uncommitted_rank_py_says_so(self):
        real = ev._git
        with mock.patch.object(ev, "_git", side_effect=lambda *a: (
                " M screener/rank.py" if a[0] == "status" else real(*a))):
            self.run_main()
        self.assertTrue(self.rows()[-1]["description"].endswith(" (uncommitted)"))
        with mock.patch.object(ev, "_git", side_effect=lambda *a: (
                "" if a[0] == "status" else real(*a))):
            self.run_main()
        self.assertNotIn("uncommitted", self.rows()[-1]["description"])

    def test_looks_are_not_decisions(self):
        self.run_main()
        self.assertEqual(ev._decisions(), [])
        self.assertEqual(ev._kept_rows(ev.fingerprint()), [])

    def test_a_score_that_changes_between_passes_is_refused_in_every_mode(self):
        calls = []

        def counter(features):
            calls.append(1)
            return {"score": float(len(calls) % 97)}

        self.spy = counter
        with mock.patch.object(ev, "REVIEW_PENDING", ""), \
                mock.patch.object(ev, "BUILDER_META", ()):
            for argv in ((), ("--test",), ("--decide",), ("--null-test", "20")):
                code, text = self.run_main(*argv)
                self.assertEqual(code, 3, argv)
                self.assertIn("a different score the second time", text)
                self.assertNotIn("graded", text)
        self.assertFalse(self.log.exists())
        # The sealed split is held to it too.
        real = ev.evaluate

        def only_test_unstable(*a, **kw):
            out = real(*a, **kw)
            out["unstable_scores"] = 3 if a[2] == "test" else 0
            return out
        self.spy = lambda f: {"score": 50.0}
        with mock.patch.object(ev, "evaluate", only_test_unstable):
            code, text = self.run_main("--test")
        self.assertEqual(code, 3)
        self.assertNotIn("graded", text)

    def test_a_refused_decide_shows_no_score_and_test_does_not_go_with_it(self):
        with mock.patch.object(ev, "REVIEW_PENDING", ""), \
                mock.patch.object(ev, "BUILDER_META", ()), \
                mock.patch.object(ev, "_git", return_value=""):       # git gives nothing: refused
            code, text = self.run_main("--decide")
        self.assertEqual(code, 3)
        self.assertTrue(text.startswith("REFUSED"))
        self.assertNotIn("graded", text)
        self.assertNotIn("hits", text)
        self.assertFalse(self.log.exists())
        code, text = self.run_main("--decide", "--test")
        self.assertEqual(code, 2)
        self.assertNotIn("graded", text)
        with mock.patch.object(ev, "REVIEW_PENDING", "pending"):
            code, text = self.run_main("--decide")
        self.assertEqual((code, text), (3, "REFUSED: pending.\n"))

    def test_a_key_from_before_the_builder_rules_decides_nothing(self):
        real = ev.load

        def old_key():
            records, positives, meta = real()
            return records, positives, {k: v for k, v in meta.items() if k not in ev.BUILDER_META}

        with mock.patch.object(ev, "REVIEW_PENDING", ""), mock.patch.object(ev, "load", old_key):
            code, text = self.run_main("--decide", "--new-baseline")
            self.assertEqual(code, 3)
            self.assertIn("built before the 2026-11-11 builder rules", text)
            self.assertEqual(self.run_main()[0], 0)                    # a look still works
        self.assertEqual([r["status"] for r in self.rows()], ["look"])

    def test_a_null_test_leaves_a_row_before_it_runs(self):
        with mock.patch.object(ev.concurrent.futures, "ProcessPoolExecutor",
                               side_effect=RuntimeError("stopped here")):
            with self.assertRaises(RuntimeError):
                self.run_main("--null-test", "20")
        self.assertEqual([r["status"] for r in self.rows()], ["null"])

    def test_main_uses_the_fresh_process_pass_and_says_when_it_cannot_run(self):
        asked = []
        with mock.patch.object(ev, "second_pass",
                               return_value=lambda rows: asked.append(len(rows)) or
                               [float(self.spy(row)["score"]) for row in rows]):
            self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(len(asked), 1)
        self.assertGreater(asked[0], 5000)
        with mock.patch.object(ev, "second_pass", return_value=lambda rows: None):
            code, text = self.run_main()
        self.assertEqual(code, 3)
        self.assertIn("could not run, so nothing was scored", text)
        self.assertNotIn("different score", text)
        self.assertEqual(len(self.rows()), 1)

    def test_the_unstable_refusal_gives_no_count(self):
        self.spy = lambda f, box=[]: box.append(1) or {"score": float(len(box) % 97)}
        code, text = self.run_main()
        self.assertEqual(code, 3)
        self.assertIn("gave some records a different score", text)
        self.assertNotRegex(text, r"\d")

    def test_a_module_from_the_repository_outside_screener_refuses_every_mode(self):
        with mock.patch.object(ev, "foreign_modules", return_value=["random", "csv"]):
            for argv in ((), ("--control",), ("--decide",), ("--null-test", "20")):
                code, text = self.run_main(*argv)
                self.assertEqual(code, 3, argv)
                self.assertIn("outside screener/ (random, csv)", text)
        self.assertFalse(self.log.exists())

    def test_new_baseline_needs_decide(self):
        code, text = self.run_main("--new-baseline")
        self.assertEqual(code, 2)
        self.assertFalse(self.log.exists())


class BuiltKeyTests(unittest.TestCase):
    """The committed key, as the 2026-11-11 builder writes it. Reads keys,
    cells, splits, weights and site hashes: no feature of any record."""

    def setUp(self):
        self.meta = ev.load_meta()
        if not all(self.meta.get(k) for k in ev.BUILDER_META):
            self.skipTest("the key in data/ has not been rebuilt with the 2026-11-11 builder "
                          "rules yet (evaluate --decide refuses it)")
        self.records, self.weights, _ = ev.load()
        self.sites = {}
        for year in ev.COHORTS:
            self.sites.update(json.loads(ev.labels_file(year).read_text())["sites"])

    def test_meta_names_the_window_and_the_deed_file(self):
        self.assertEqual(self.meta["window_start"], be.WINDOW_START.isoformat())
        self.assertRegex(self.meta["deed_file_sha256"], r"^[0-9a-f]{64}$")
        for year in ev.COHORTS:
            with gzip.open(ev.features_file(year), "rb") as fh:
                self.assertEqual(fh.read(0), b"")
            self.assertEqual(ev.features_file(year).read_bytes()[4:8], b"\0\0\0\0")   # no gzip time

    def test_every_positive_has_a_site_and_a_sites_weights_sum_to_one(self):
        self.assertEqual(set(self.sites), set(self.weights))
        totals: dict[str, float] = {}
        for key, site in self.sites.items():
            totals[site] = totals.get(site, 0.0) + self.weights[key]
        for site, total in totals.items():
            self.assertAlmostEqual(total, 1.0, places=4, msg=site)

    def test_a_site_sits_in_one_cell_and_one_split(self):
        where = {r["key"]: (r["block"], r["split"]) for r in self.records}
        places: dict[str, set] = {}
        for key, site in self.sites.items():
            places.setdefault(site, set()).add(where[key])
        self.assertTrue(all(len(p) == 1 for p in places.values()))
        test_blocks = set(json.loads(ev.TEST_BLOCKS_FILE.read_text())["test_blocks"])
        for block, split in where.values():
            self.assertEqual(split == "test", block in test_blocks)


class CheckedBytesTests(unittest.TestCase):
    """The rank.py that is checked is the rank.py that runs, and a fresh
    copy of it scores every record the same."""

    SOURCE = (b"def learned_score(p):\n"
              b"    return {'score': 30.0 + (p.get('acres') or 0.0)}\n")

    def test_the_scorer_is_made_from_the_bytes_not_from_the_import_cache(self):
        import importlib
        import os
        import screener.rank as live
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp, "rank.py")
            for weight in (30, 90):                           # same size, same second
                fake.write_bytes(self.SOURCE.replace(b"30.0", b"%d.0" % weight))
                os.utime(fake, (1_700_000_000, 1_700_000_000))
                with mock.patch.object(ev, "RANK_FILE", fake):
                    scorer = ev.load_scorer(fake.read_bytes())
                self.assertEqual(scorer({"acres": 1.0})["score"], weight + 1.0)
        self.assertIs(importlib.import_module("screener.rank"), live)   # the import is untouched
        self.assertNotIn("p.get('acres')", Path(live.__file__).read_text())

    def test_rank_source_gives_the_checked_bytes_and_refuses_a_link(self):
        problems, source = ev.rank_source()
        self.assertEqual((problems, source), ([], ev.RANK_FILE.read_bytes()))
        self.assertEqual(ev.rank_import_problems(), [])
        with tempfile.TemporaryDirectory() as tmp:
            target, link = Path(tmp, "elsewhere.py"), Path(tmp, "rank.py")
            target.write_bytes(self.SOURCE)
            link.symlink_to(target)
            with mock.patch.object(ev, "RANK_FILE", link), \
                    mock.patch.object(ev, "_rank_origin", return_value=str(link)):
                problems, source = ev.rank_source()
        self.assertEqual((source, len(problems)), (b"", 1))
        self.assertIn("link", problems[0])

    def second_pass_unstable(self, body: bytes) -> int:
        source = b"SEEN = {}\nBOX = []\n\ndef learned_score(p):\n" + body
        records = pool(years=(2015,), per_year=40)
        for i, r in enumerate(records):
            r["acres"] = float(i)
        scorer = ev.load_scorer(source)
        return ev.ranking(records, "dev", scorer, SETTINGS, ("acres",),
                          ev.second_pass(source))[3]

    def test_a_fresh_process_catches_what_a_second_call_cannot(self):
        honest = b"    return {'score': 10.0 + p['acres'] / 3.0}\n"
        memo = b"    return {'score': float(SEEN.setdefault(p['acres'], len(SEEN)))}\n"
        counter = b"    BOX.append(1)\n    return {'score': float(len(BOX))}\n"
        salted = b"    return {'score': float(sorted({'a', 'b', 'c', 'd', 'e', 'f', 'g'}, key=repr)[0] != list({'a', 'b', 'c', 'd', 'e', 'f', 'g'})[0]) + float(len(repr(BOX)) + len(repr(p.get)) % 7)}\n"
        raises = b"    return {'score': 1.0 / (len(SEEN) - len(SEEN))}\n"
        self.assertEqual(self.second_pass_unstable(honest), 0)
        self.assertGreater(self.second_pass_unstable(memo), 30)      # index of first sight
        self.assertGreater(self.second_pass_unstable(counter), 30)
        del salted
        with self.assertRaises(ZeroDivisionError):
            self.second_pass_unstable(raises)

    def test_a_second_pass_that_cannot_run_counts_every_record(self):
        records = pool(years=(2015,), per_year=40)
        recheck = ev.second_pass(self.SOURCE)
        scorer = ev.load_scorer(self.SOURCE)

        class Done:
            def __init__(self, code, out):
                self.returncode, self.stdout = code, out

        good = json.dumps([float(31).hex()] * 40)
        for outcome in (OSError("no python"), Done(1, good), Done(0, "not json"),
                        Done(0, json.dumps([float(31).hex()] * 39))):
            with self.subTest(str(outcome)[:30]), mock.patch.object(
                    ev.subprocess, "run", side_effect=[outcome]
                    if isinstance(outcome, Exception) else None,
                    return_value=None if isinstance(outcome, Exception) else outcome):
                with self.assertRaises(ev.SecondPassFailed):
                    ev.ranking(records, "dev", scorer, SETTINGS, ("acres",), recheck)
        with self.assertRaises(ev.SecondPassFailed):
            ev.ranking(records, "dev", scorer, SETTINGS, ("acres",), lambda rows: [1.0])

    def test_a_row_that_raises_in_the_fresh_process_counts_as_different(self):
        source = b"def learned_score(p):\n    return {'score': 10.0 / (p['acres'] - 7.0)}\n"
        scores = ev.second_pass(source)([{"acres": 1.0}, {"acres": 7.0}, {"acres": 9.0}])
        self.assertEqual(scores, [10.0 / -6.0, math.inf, 5.0])

    def test_nan_twice_is_not_a_difference(self):
        records = pool(years=(2015,), per_year=40)
        result = ev.ranking(records, "dev", lambda f: {"score": float("nan")}, SETTINGS,
                            ("signal",))
        self.assertEqual((result[2], result[3]), (40, 0))

    def test_the_fresh_process_gets_no_pythonpath(self):
        captured = {}

        def run(cmd, **kw):
            captured.update(kw["env"])
            raise OSError
        with mock.patch.dict("os.environ", {"PYTHONPATH": ".", "PYTHONSTARTUP": "x.py"}), \
                mock.patch.object(ev.subprocess, "run", run):
            self.assertIsNone(ev.second_pass(self.SOURCE)([{"acres": 1.0}]))
        self.assertNotIn("PYTHONPATH", captured)
        self.assertNotIn("PYTHONSTARTUP", captured)

    def test_the_second_pass_uses_another_order_and_another_hash_seed(self):
        records = pool(years=(2015,), per_year=40)
        seen = []
        ev.ranking(records, "dev", lambda f: {"score": 1.0}, SETTINGS, ("signal",),
                   lambda rows: seen.extend(r["signal"] for r in rows) or [1.0] * len(rows))
        self.assertEqual(sorted(seen), [float(i) for i in range(40)])
        self.assertNotEqual(seen, [float(i) for i in range(40)])
        again = []
        ev.ranking(records, "dev", lambda f: {"score": 1.0}, SETTINGS, ("signal",),
                   lambda rows: again.extend(r["signal"] for r in rows) or [1.0] * len(rows))
        self.assertNotEqual(seen, again)                       # drawn fresh each run
        captured = {}

        def run(cmd, **kw):
            captured.update(kw["env"], cmd=cmd, cwd=kw["cwd"])
            raise OSError
        with mock.patch.object(ev.subprocess, "run", run):
            ev.second_pass(self.SOURCE)([{"acres": 1.0}])
        self.assertIn(captured["PYTHONHASHSEED"], ("1", "2"))
        self.assertNotEqual(captured["PYTHONHASHSEED"], __import__("os").environ.get("PYTHONHASHSEED"))
        self.assertIn("-P", captured["cmd"])

    def test_foreign_modules_sees_a_repository_file_outside_screener(self):
        import sys
        import types
        self.assertEqual(ev.foreign_modules(), [])
        before = ev.foreign_modules()
        stray = types.ModuleType("stray_csv")
        stray.__file__ = str(ev.ROOT / "csv.py")
        fine = types.ModuleType("fine_mod")
        fine.__file__ = str(ev.ROOT / "screener" / "x.py")
        venv = types.ModuleType("venv_mod")
        venv.__file__ = str(ev.ROOT / ".venv" / "lib" / "site-packages" / "y.py")
        with mock.patch.dict(sys.modules, {"stray_csv": stray, "fine_mod": fine, "venv_mod": venv}):
            self.assertEqual(sorted(set(ev.foreign_modules()) - set(before)), ["stray_csv"])
        # A root-level file named like a standard module is seen whatever it
        # says about itself once loaded.
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("csv.py", "json.pyc", "prepare.py", ".gitignore", "CSV.txt",
                         "math" + __import__("importlib.machinery").machinery.EXTENSION_SUFFIXES[0]):
                Path(tmp, name).write_text("")
            for folder in ("random", "venv", "site", "types", "screener", "tests"):
                Path(tmp, folder).mkdir()
            Path(tmp, "random", "__init__.py").write_text("")
            with mock.patch.object(ev, "ROOT", Path(tmp)):
                found = sorted(n.split(" ")[0] for n in ev.foreign_modules() if "shadows" in n)
            self.assertEqual(len(found), 4)
            self.assertEqual([n for n in found if not n.startswith("math")],
                             ["csv.py", "json.pyc", "random"])

    def test_git_runs_in_the_repository_root(self):
        seen = []

        class Done:
            stdout, returncode = "ok\n", 0

        def run(cmd, **kw):
            seen.append(kw.get("cwd"))
            return Done()
        with mock.patch.object(ev.subprocess, "run", run):
            self.assertEqual(ev._git("status"), "ok")
            self.assertTrue(ev._is_ancestor("abc"))
        self.assertEqual(seen, [str(ev.ROOT), str(ev.ROOT)])
        self.assertTrue((ev.ROOT / "screener" / "rank.py").is_file())

    def test_hash_and_id_are_refused(self):
        for src in ("x = hash('a')\n", "y = id(object())\n"):
            self.assertTrue(ev.rank_problems(src), src)


class BuildRuleTests(unittest.TestCase):
    A, DEV, REIT = "SMITH JOHN", "ACME LAND HOLDINGS LLC", "BIG REIT LP"

    def chain(self):
        """A (landowner) -> developer in 2017; developer -> REIT in 2020."""
        return [deed("2001-05-01", 90000, "OLD FARM OWNER"),
                deed("2017-06-01", 900000, self.A),
                deed("2020-09-01", 30000000, self.DEV)]

    def test_built_window(self):
        self.assertEqual(be.built_window(2019), (date(2019, 1, 1), date(2019, 1, 1)))
        self.assertEqual(be.built_window(2015), (date(2015, 1, 1), date(2015, 1, 1)))
        # No 2022 digest: a building first shown in 2023 went up in 2021 or 2022.
        self.assertEqual(be.built_window(2023), (date(2022, 1, 1), date(2023, 1, 1)))

    def test_sale_before_matches_sold_after_without_a_bound(self):
        from screener import fulton
        for owner in (self.A, self.DEV, self.REIT, "NOBODY"):
            for after in (date(2015, 1, 1), date(2018, 1, 1), date(2021, 1, 1)):
                self.assertEqual(be.sale_before(self.chain(), owner, after, date.max, 50000),
                                 fulton.sold_after(self.chain(), owner, after, 50000),
                                 (owner, after))

    def test_landowner_counts_and_the_developers_resale_does_not(self):
        shares = {y: (1.0 if y >= 2019 else 0.0) for y in be.BUILT_YEARS}   # first shown 2019
        for year in (2015, 2016, 2017):          # A owns the land in January
            self.assertEqual(be.classify(shares, self.chain(), self.A, date(year, 1, 1)),
                             "positive", year)
        for year in (2018, 2019):                # the developer owns it and later sells the building
            self.assertEqual(be.classify(shares, self.chain(), self.DEV, date(year, 1, 1)),
                             "left out: bought at a real price since 2015", year)
        self.assertEqual(be.classify(shares, self.chain(), self.REIT, date(2021, 1, 1)),
                         "left out: bought at a real price since 2015")
        # A developer who bought before the window, built, and sold the building.
        early = [deed("2013-06-01", 900000, self.A), deed("2020-09-01", 30000000, self.DEV)]
        self.assertEqual(be.classify(shares, early, self.DEV, date(2019, 1, 1)),
                         "left out: sold after the building")
        self.assertEqual(be.classify(shares, early, self.REIT, date(2021, 1, 1)),
                         "left out: bought at a real price since 2015")

    def test_a_buyer_inside_the_window_is_never_the_seller(self):
        """reviews/36, 05, finding 4: the landowner sells in June 2016; the
        developer builds in 2018 and sells the finished building in November
        2018; the 2019 digest is the first to show the building. The digests
        alone put the developer's sale "before the building"."""
        shares = {y: (1.0 if y >= 2019 else 0.0) for y in be.BUILT_YEARS}
        sales = [deed("2001-05-01", 90000, "OLD FARM OWNER"), deed("2016-06-15", 900000, self.A),
                 deed("2018-11-20", 30000000, self.DEV)]
        for year in (2015, 2016):
            self.assertEqual(be.classify(shares, sales, self.A, date(year, 1, 1)), "positive", year)
        for year in (2017, 2018):
            self.assertEqual(be.classify(shares, sales, self.DEV, date(year, 1, 1)),
                             "left out: bought at a real price since 2015", year)
        self.assertTrue(be.sale_before(sales, self.DEV, date(2017, 1, 1), date(2019, 1, 1), 50000))
        # A middleman's land flip is left out the same way (operator decision, 2026-11-11).
        flip = [deed("2015-03-01", 400000, self.A), deed("2016-08-01", 900000, "FLIPPER LLC")]
        self.assertEqual(be.classify(shares, flip, "FLIPPER LLC", date(2016, 1, 1)),
                         "left out: bought at a real price since 2015")
        # What does not make a buyer: a deed before the window, a token price,
        # a right-of-way deed, the owner's own deed, a deed on or after the as-of date.
        for other in ([deed("2014-12-31", 900000, "X")], [deed("2015-06-01", 49999, "X")],
                      [deed("2015-06-01", 900000, "X", "Right of Way/Dept of Trans")],
                      [deed("2015-06-01", 900000, self.A)], [deed("2016-06-01", 900000, "X")]):
            self.assertFalse(be.bought_in_window(other, self.A, date(2016, 1, 1)), other)
        self.assertTrue(be.bought_in_window([deed("2015-01-01", 50000, "X")], self.A,
                                            date(2016, 1, 1)))
        # A parcel that was never built on stays a negative whoever bought it.
        none = {y: 0.0 for y in be.BUILT_YEARS}
        self.assertEqual(be.classify(none, sales, self.DEV, date(2018, 1, 1)), "negative")

    def test_a_first_quarter_buyer_shown_by_the_digest_is_left_out_built_or_not(self):
        why = "left out: the digest shows the buyer of a first-quarter sale"
        built = {y: (1.0 if y >= 2019 else 0.0) for y in be.BUILT_YEARS}
        none = {y: 0.0 for y in be.BUILT_YEARS}
        feb = [deed("2017-02-10", 900000, self.A)]
        for shares in (built, none):
            self.assertEqual(be.classify(shares, feb, "BUYER LLC", date(2017, 1, 1)), why)
        # The January owner is the seller of that deed: the record is sound.
        self.assertEqual(be.classify(built, feb, self.A, date(2017, 1, 1)), "positive")
        self.assertEqual(be.classify(none, feb, self.A, date(2017, 1, 1)), "negative")
        # The January owner sells and the buyer resells within the quarter:
        # only the first deed says who owned it in January.
        resold = [deed("2017-02-10", 900000, self.A), deed("2017-03-15", 950000, "BUYER LLC")]
        self.assertEqual(be.classify(built, resold, self.A, date(2017, 1, 1)), "positive")
        self.assertEqual(be.classify(built, resold, "BUYER LLC", date(2017, 1, 1)), why)
        # Two deeds on one day have no order in the file: either way round,
        # the January owner is the seller of one of them.
        same_day = [deed("2017-02-10", 950000, "BUYER LLC"), deed("2017-02-10", 900000, self.A)]
        for sales in (same_day, same_day[::-1]):
            self.assertEqual(be.classify(built, sales, self.A, date(2017, 1, 1)), "positive")
            self.assertEqual(be.classify(built, sales, "THIRD PARTY LLC", date(2017, 1, 1)), why)
        # A deed dated 1 January itself counts, and the price need not reach MIN_PRICE.
        self.assertEqual(be.classify(none, [deed("2017-01-01", 900000, self.A)], "BUYER LLC",
                                     date(2017, 1, 1)), why)
        self.assertEqual(be.classify(none, [deed("2017-02-10", 40000, self.A)], "BUYER LLC",
                                     date(2017, 1, 1)), why)
        # Outside the quarter, unpriced, or a right-of-way deed: not a sign of a late digest.
        for sales in ([deed("2017-04-03", 900000, self.A)], [deed("2016-12-31", 900000, self.A)],
                      [deed("2017-02-10", 0, self.A)],
                      [deed("2017-02-10", 900000, self.A, "Right of Way/Dept of Trans")]):
            self.assertEqual(be.classify(none, sales, "BUYER LLC", date(2017, 1, 1)), "negative")

    def test_never_built_is_a_negative_and_owner_user_is_left_out(self):
        none = {y: 0.0 for y in be.BUILT_YEARS}
        self.assertEqual(be.classify(none, self.chain(), self.A, date(2016, 1, 1)), "negative")
        self.assertEqual(be.classify({be.LABEL_YEAR: 0.4}, [], self.A, date(2016, 1, 1)),
                         "negative")
        built = {y: (1.0 if y >= 2018 else 0.0) for y in be.BUILT_YEARS}
        self.assertEqual(be.classify(built, [deed("2001-05-01", 90000, "X")], self.A,
                                     date(2016, 1, 1)),
                         "left out: built but not sold by the as-of owner")

    def test_a_sale_in_the_missing_digest_year_is_left_out(self):
        shares = {y: (1.0 if y >= 2023 else 0.0) for y in be.BUILT_YEARS}
        sold_2021 = [deed("2021-03-01", 500000, self.A)]
        sold_2022 = [deed("2022-03-01", 500000, self.A)]
        self.assertEqual(be.classify(shares, sold_2021, self.A, date(2019, 1, 1)), "positive")
        self.assertEqual(be.classify(shares, sold_2022, self.A, date(2019, 1, 1)),
                         "left out: sale and building can't be ordered (no 2022 digest)")

    def test_cheap_right_of_way_and_authority_sales_do_not_count(self):
        shares = {y: (1.0 if y >= 2019 else 0.0) for y in be.BUILT_YEARS}
        cases = ([deed("2017-06-01", 10, self.A)],
                 [deed("2017-06-01", 900000, self.A, "Right of Way/Dept of Trans")],
                 [deed("2017-06-01", 900000, self.A),
                  deed("2018-02-01", 0, "DEVELOPMENT AUTHORITY OF FULTON COUNTY")])
        for sales in cases:
            self.assertEqual(be.classify(shares, sales, self.A, date(2016, 1, 1)),
                             "left out: built but not sold by the as-of owner")

    def test_snapshot_date_check_counts(self):
        as_of = date(2017, 1, 1)
        parcels = [{"parcel_id": "p1", "owner": "BUYER ONE LLC"},      # sold in November, buyer shown
                   {"parcel_id": "p2", "owner": "SELLER TWO"},         # sold in February, seller shown
                   {"parcel_id": "p3", "owner": "BUYER THREE LLC"},    # sold in February, buyer shown
                   {"parcel_id": "p4", "owner": "X"}]                  # two deeds: not counted
        sales = {"p1": [deed("2016-11-15", 100000, "SELLER ONE")],
                 "p2": [deed("2017-02-10", 100000, "SELLER TWO")],
                 "p3": [deed("2017-02-10", 100000, "SELLER THREE")],
                 "p4": [deed("2016-12-01", 5, "A"), deed("2017-01-05", 5, "B")]}
        self.assertEqual(be.snapshot_date_check(parcels, sales, as_of),
                         {"quarter_before": [0, 1], "quarter_after": [1, 1]})

    def test_one_cell_per_site_from_its_first_year(self):
        cohorts = {2016: [{"parcel_id": "p1", "lon": -84.52, "lat": 33.61},
                          {"parcel_id": "p2", "lon": -84.52, "lat": 33.61}],
                   2015: [{"parcel_id": "p1", "lon": -84.48, "lat": 33.61}],
                   2017: [{"parcel_id": "p1", "lon": -84.61, "lat": 33.70}]}
        cells = be.site_cells(cohorts)
        self.assertEqual(cells["p1"], be.cell_for(-84.48, 33.61))      # 2015, not a later centroid
        self.assertEqual(cells["p2"], be.cell_for(-84.52, 33.61))
        self.assertNotEqual(cells["p1"], cells["p2"])

    def test_a_dev_parcel_on_test_ground_is_dropped(self):
        from shapely.geometry import box
        kept = [{"parcel_id": "t1", "geometry": box(0, 0, 10, 10)},
                {"parcel_id": "d1", "geometry": box(5, 5, 12, 12)},     # the same ground, a new id
                {"parcel_id": "d2", "geometry": box(10, 0, 20, 10)},    # only shares an edge
                {"parcel_id": "d3", "geometry": box(40, 40, 50, 50)}]
        is_test = {"t1": True, "d1": False, "d2": False, "d3": False}
        self.assertEqual(be.dev_on_test_ground(kept, is_test), {"d1"})
        self.assertEqual(be.dev_on_test_ground(kept[1:], is_test), set())

    def test_a_sites_weights_sum_to_one(self):
        kept = [{"parcel_id": "p1", "label": "positive"}, {"parcel_id": "p1", "label": "positive"},
                {"parcel_id": "p1", "label": "negative"}, {"parcel_id": "p2", "label": "positive"},
                {"parcel_id": "p3", "label": "positive"}, {"parcel_id": "p4", "label": "negative"}]
        self.assertEqual(be.positive_years(kept, {"p3"}), {"p1": 2, "p2": 1})

    def test_the_features_file_has_the_same_bytes_every_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp, "a.gz"), Path(tmp, "b.gz")
            be.write_gz(a, {"records": [1, 2]})
            with mock.patch("time.time", return_value=2_000_000_000.0):
                be.write_gz(b, {"records": [1, 2]})
            self.assertEqual(a.read_bytes(), b.read_bytes())
            with gzip.open(a, "rt") as fh:
                self.assertEqual(json.load(fh), {"records": [1, 2]})

    def test_keys_and_urls(self):
        self.assertEqual(be.key_for("14F0001 0001"), be.key_for("14F0001 0001", 2016))
        keys = {be.key_for("14F0001 0001", y) for y in be.COHORTS}
        self.assertEqual(len(keys), len(be.COHORTS))
        self.assertTrue(be.snapshot_url(2019).endswith("/Tax_Parcels2019/FeatureServer/0"))
        self.assertTrue(be.snapshot_url(2025).endswith("/Tax_Parcels_2025/FeatureServer/0"))
        self.assertEqual(be.COHORTS, ev.COHORTS)
        self.assertNotIn(2022, be.BUILT_YEARS)

    def test_missing_test_blocks_file_stops_the_build(self):
        with mock.patch.object(be, "TEST_BLOCKS_FILE", Path("/nonexistent/test_blocks.json")), \
                self.assertRaises(SystemExit):
            be.load_test_blocks()


class CommittedKeyTests(unittest.TestCase):
    """Shape of the data files in git. Reads no feature of any positive."""

    @classmethod
    def setUpClass(cls):
        cls.records, cls.weights, cls.meta = ev.load()
        cls.test_blocks = set(json.loads(ev.TEST_BLOCKS_FILE.read_text())["test_blocks"])

    def test_records_carry_only_the_declared_fields(self):
        allowed = {"key", "block", "split", "cohort", *self.meta["features"], *self.meta["banned"]}
        self.assertEqual({k for r in self.records for k in r}, allowed)
        self.assertEqual(self.meta["features"], be.FEATURES)
        self.assertEqual(self.meta["banned"], be.BANNED_AS_NONE)
        for r in self.records[:500]:
            self.assertTrue(all(r[f] is None for f in self.meta["banned"]))

    def test_keys_are_unique_and_years_complete(self):
        keys = [r["key"] for r in self.records]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sorted({r["cohort"] for r in self.records}), list(ev.COHORTS))

    def test_the_sealed_cells_are_the_frozen_ones_in_every_year(self):
        for r in self.records:
            self.assertEqual(r["split"] == "test", r["block"] in self.test_blocks, r["key"])
        by_block = {}
        for r in self.records:
            by_block.setdefault(r["block"], set()).add(r["split"])
        self.assertTrue(all(len(s) == 1 for s in by_block.values()))

    def test_weights(self):
        keys = {r["key"] for r in self.records}
        self.assertTrue(set(self.weights) <= keys)
        for w in self.weights.values():
            self.assertTrue(0 < w <= 1.0)
        dev = sum(self.weights[r["key"]] for r in self.records
                  if r["split"] == "dev" and r["key"] in self.weights)
        test = sum(self.weights[r["key"]] for r in self.records
                   if r["split"] == "test" and r["key"] in self.weights)
        self.assertAlmostEqual(dev, round(dev), places=2)   # whole sites on each side
        self.assertAlmostEqual(test, round(test), places=2)

    def test_fingerprint_covers_every_file_and_its_name(self):
        self.assertEqual(len(ev.data_files()), 2 * len(ev.COHORTS) + 1)
        with tempfile.TemporaryDirectory() as tmp:
            for i, name in enumerate(("a", "b")):
                Path(tmp, name).write_bytes(b"same")
            with mock.patch.object(ev, "data_files", return_value=[Path(tmp, "a")]):
                one = ev.fingerprint()
            with mock.patch.object(ev, "data_files", return_value=[Path(tmp, "b")]):
                other = ev.fingerprint()
        self.assertNotEqual(one, other)                      # same bytes, another name


class LoadGuardTests(unittest.TestCase):
    def write(self, tmp, year, meta, records, positives):
        with gzip.open(Path(tmp, f"fulton_{year}_features.json.gz"), "wt") as fh:
            json.dump({"meta": meta, "records": records}, fh)
        Path(tmp, f"fulton_{year}_labels.json").write_text(json.dumps(
            {"meta": meta, "positives": positives}))

    def test_missing_mismatched_or_old_format_files_are_refused(self):
        meta = {"cohorts": [2015, 2016], "features": ["a"], "banned": [], "screens": {},
                "label_year": 2025, "min_price": 1, "built_years": [2015]}
        rec = lambda y: [{"key": f"k{y}", "cohort": y, "split": "dev", "block": "b", "a": 1}]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ev, "DATA", Path(tmp)), mock.patch.object(ev, "COHORTS", (2015, 2016)):
            self.write(tmp, 2015, meta, rec(2015), {"k2015": 1.0})
            with self.assertRaises(ev.EvalSetError):          # 2016 missing
                ev.load()
            self.write(tmp, 2016, dict(meta, screens={"x": 1}), rec(2016), {})
            with self.assertRaises(ev.EvalSetError):          # built with other screens
                ev.load()
            self.write(tmp, 2016, meta, rec(2015), {})
            with self.assertRaises(ev.EvalSetError):          # records of another year
                ev.load()
            self.write(tmp, 2016, meta, rec(2016), ["k2016"])
            with self.assertRaises(ev.EvalSetError):          # the old list-of-keys labels
                ev.load()
            self.write(tmp, 2016, meta, rec(2016), {"k2016": 0.5})
            records, weights, _ = ev.load()
            self.assertEqual((len(records), weights), (2, {"k2015": 1.0, "k2016": 0.5}))

    def test_main_reports_an_unusable_key(self):
        with mock.patch.object(ev, "load", side_effect=ev.EvalSetError("x")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ev.main([]), 2)
        self.assertIn("can't be used", out.getvalue())


if __name__ == "__main__":
    unittest.main()
