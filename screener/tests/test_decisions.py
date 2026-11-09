"""F14 Phase A: maybe marks, the decide CLI and the per-run feature snapshot
(reviews/34_screener_quick_wins, gates H1-H9). Offline, on the
test_integrity fakes.

Decisions are labels, so neither a decision nor the snapshot may be on a
parcel while rank.py or scoring.py run; the spy tests below check that.

    python -m unittest discover -s screener/tests -t .
"""

from __future__ import annotations

import ast
import contextlib
import copy
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from screener import decide, rank, run, scoring, snapshot
from screener.autoresearch import build_eval
from screener.tests.test_integrity import (
    A, B, SETTINGS_FILE, Offline, RunHarness, county_parcel,
)

SCREENER = Path(run.__file__).resolve().parent
REAL_DECISIONS = SCREENER / "inputs" / "decisions.csv"
REAL_OUTPUT = SCREENER / "output"
SPACED = "14F0063  LL0219"  # two spaces inside, as some county ids have
SNAP_KEYS = {"features", "live_only", "records", "meta", "run_meta", "snapshot",
             "nonfinite_as_null", "settings_sha256", "code_commit"}
LIMIT_KEYS = {"parcel_id", "lon", "lat", "features", "live_only"}


def _state(path):
    if not path.exists():
        return None
    if path.is_dir():
        return sorted((str(p.relative_to(path)), p.stat().st_mtime_ns) for p in path.rglob("*"))
    st = path.stat()
    return (st.st_mtime_ns, st.st_size, hashlib.sha256(path.read_bytes()).hexdigest())


_REAL_BEFORE = {}


def setUpModule():
    for path in (REAL_DECISIONS, REAL_OUTPUT, REAL_DECISIONS.with_name(decide.STORE_NAME)):
        _REAL_BEFORE[path] = _state(path)


def tearDownModule():
    # H8: the suite never touches the real decisions file, store or output/.
    for path, before in _REAL_BEFORE.items():
        if _state(path) != before:
            raise AssertionError(f"the test suite changed the real {path}")


def git(*args):
    try:
        return subprocess.run(["git", "-C", str(SCREENER), *args],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_keys(v)


class DecisionHarness(RunHarness):
    """Four parcels; output under run.HERE/output so decide finds it."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "output" / "fulton-2026-11-09"
        self.parcels[:] = [county_parcel("14F0001 0001", A),
                           county_parcel("14F0002 0002", B, owner="TEST OWNER BETA"),
                           county_parcel("14F0003 0003", (-84.47, 33.6)),
                           county_parcel(SPACED, (-84.46, 33.6))]
        self.fresh = copy.deepcopy(self.parcels)

    def rerun(self, *args):
        """run.main on fresh copies of the parcels, as a real run fetches them."""
        self.parcels[:] = copy.deepcopy(self.fresh)
        return self.main(*args)

    def write_decisions(self, text, encoding="utf-8"):
        run.DECISIONS_FILE.write_bytes(text.encode(encoding))

    def ranked(self):
        with open(self.out / "ranked.csv", newline="") as fh:
            return {r["parcel_id"]: r for r in csv.DictReader(fh)}

    def rejected(self):
        with open(self.out / "rejected.csv", newline="") as fh:
            return {r["parcel_id"]: r for r in csv.DictReader(fh)}

    def snap(self, out=None):
        return json.loads(((out or self.out) / snapshot.FILE_NAME).read_text())

    def decide(self, *args):
        so, se = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            try:
                code = decide.main(list(args))
            except SystemExit as exc:
                code = exc.code
        return code, so.getvalue(), se.getvalue()

    def store(self):
        path = decide.store_file()
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


class LeakTests(DecisionHarness):
    """H1, H2: no decision or snapshot key reaches scoring; maybe is a mark only."""

    def setUp(self):
        super().setUp()
        self.seen = []
        for obj, name in ((rank, "learned_score"), (scoring, "fixed_score")):
            real = getattr(obj, name)

            def spy(p, *a, _real=real, _name=name, **k):
                self.seen.append((_name, frozenset(p)))
                return _real(p, *a, **k)
            self.patch(obj, name, spy)

    def assert_clean(self):
        self.assertEqual({n for n, _ in self.seen}, {"learned_score", "fixed_score"})
        for name, keys in self.seen:
            for key in keys:
                self.assertNotIn("decision", key, f"{key} seen by {name}")
                self.assertNotIn(key, SNAP_KEYS, f"{key} seen by {name}")

    def test_spy_with_every_decision(self):
        self.write_decisions("parcel_id,decision,date,note\n"
                             "14F0001 0001,pursue,2026-11-09,a\n"
                             "14F0002 0002,maybe,2026-11-09,b\n"
                             "14F0003 0003,listed,2026-11-09,c\n"
                             f"{SPACED},pass,2026-11-09,d\n")
        for args in ((), ("--no-slope",)):
            self.seen.clear()
            code, _ = self.rerun(*args)
            self.assertEqual(code, 0)
            self.assert_clean()
            ranked, rejected = self.ranked(), self.rejected()
            self.assertEqual(ranked["14F0001 0001"]["decision"], "pursue")
            self.assertEqual(ranked["14F0002 0002"]["decision"], "maybe")
            self.assertEqual(set(ranked), {"14F0001 0001", "14F0002 0002"})
            self.assertEqual(rejected[SPACED]["decision"], "pass")
            self.assertIn("you passed on this", rejected[SPACED]["reasons"])
            # listed is unchanged: dropped with its own reason (exclude_listed).
            self.assertEqual(rejected["14F0003 0003"]["decision"], "listed")
            self.assertIn("marked as listed for sale", rejected["14F0003 0003"]["reasons"])

    def test_maybe_changes_no_score_flag_or_order(self):
        def facts():
            return {pid: (r["rank"], r["score"], r["fixed_score"], r["learned_score"],
                          r["flags"], r["why_site"], r["why_seller"])
                    for pid, r in self.ranked().items()}
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        without, snap_without = facts(), self.snap()["records"]
        self.write_decisions("parcel_id,decision,date,note\n"
                             + "".join(f"{p['parcel_id']},maybe,2026-11-09,\n" for p in self.fresh))
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        self.assertEqual(facts(), without)
        self.assertEqual(self.snap()["records"], snap_without)
        self.assertEqual({r["decision"] for r in self.ranked().values()}, {"maybe"})
        self.assertEqual(self.rejected(), {})
        self.assert_clean()

    def test_load_decisions_accepts_maybe_and_ignores_unknown_values(self):
        self.write_decisions("parcel_id,decision,date,note\nP1,MAYBE,,\nP2,skip,,\nP3,listed,,\n")
        self.assertEqual({k: v["decision"] for k, v in run.load_decisions().items()},
                         {"P1": "maybe", "P3": "listed"})


class SnapshotTests(DecisionHarness):
    """H4: allow-listed, pre-score, plain JSON with nulls counted."""

    def test_keys_and_meta(self):
        self.assertEqual(self.rerun()[0], 0)
        snap = self.snap()
        ranked = self.ranked()
        self.assertEqual(snap["meta"]["records"], len(ranked))
        self.assertEqual(len(snap["records"]), len(ranked))
        self.assertEqual({r["parcel_id"] for r in snap["records"]}, set(ranked))
        for r in snap["records"]:
            self.assertEqual(set(r), LIMIT_KEYS)
            self.assertEqual(list(r["features"]), build_eval.FEATURES)
            self.assertEqual(list(r["live_only"]), build_eval.BANNED_AS_NONE)
            self.assertIsInstance(r["lon"], float)
            self.assertIsInstance(r["lat"], float)
            self.assertEqual(r["features"]["acres"], 20.0)
            self.assertEqual(r["live_only"]["jurisdiction"], "College Park")
            self.assertIsNotNone(r["features"]["usable_after_slope"])
        for key in all_keys(snap["records"]):
            for word in ("earthwork", "cut_fill", "geometry", "decision", "mailing", "address"):
                self.assertNotIn(word, key)
            self.assertNotIn(key, {"owner", "score", "fixed_score", "learned_score", "flags"})
        self.assertNotIn("TEST OWNER", json.dumps(snap))
        meta = snap["meta"]
        self.assertEqual(meta["county"], "Fulton")
        self.assertEqual(meta["limit"], None)
        self.assertIs(meta["slope"], True)
        self.assertEqual(meta["settings_sha256"],
                         hashlib.sha256(SETTINGS_FILE.read_bytes()).hexdigest())
        self.assertTrue(meta["code_commit"] is None or len(meta["code_commit"]) == 40)
        self.assertEqual(meta["nonfinite_as_null"], 0)

    def test_imported_not_copied(self):
        self.assertIs(snapshot.FEATURES, build_eval.FEATURES)
        self.assertIs(snapshot.BANNED_AS_NONE, build_eval.BANNED_AS_NONE)

    def test_trial_and_no_slope_runs_are_labelled(self):
        self.assertEqual(self.rerun("--no-slope", "--limit", "3")[0], 0)
        meta = self.snap()["meta"]
        self.assertEqual(meta["limit"], 3)
        self.assertIs(meta["slope"], False)
        for r in self.snap()["records"]:
            self.assertIsNone(r["live_only"]["steep_pct"])

    def test_taken_before_scoring(self):
        real = rank.learned_score

        def mutating(p, *a, **k):
            p["acres"] = 999.0
            p["owner_type"] = "changed"
            return real(p, *a, **k)
        self.patch(rank, "learned_score", mutating)
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        self.assertEqual({r["acres"] for r in self.ranked().values()}, {"999.0"})
        for r in self.snap()["records"]:
            self.assertEqual(r["features"]["acres"], 20.0)
            self.assertEqual(r["features"]["owner_type"], "individual")

    def test_nan_is_null_and_counted(self):
        self.fresh[0]["land_value"] = float("nan")
        self.fresh[1]["acres"] = float("inf")
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        text = (self.out / snapshot.FILE_NAME).read_text()
        for token in ("NaN", "Infinity"):
            self.assertNotIn(token, text)
        snap = json.loads(text)
        by_id = {r["parcel_id"]: r for r in snap["records"]}
        self.assertIsNone(by_id["14F0001 0001"]["features"]["land_value"])
        self.assertIsNone(by_id["14F0002 0002"]["features"]["acres"])
        self.assertEqual(by_id["14F0003 0003"]["features"]["acres"], 20.0)
        self.assertGreaterEqual(snap["meta"]["nonfinite_as_null"], 2)
        n = snap["meta"]["nonfinite_as_null"]
        self.assertIn(f"{n} non-finite values (NaN or infinity) were written as null",
                      self.notes())

    def test_plain_values(self):
        bad = [0]
        self.assertEqual(snapshot._plain([1.5, float("nan"), (True, None)], bad),
                         [1.5, None, [True, None]])
        self.assertEqual(bad, [1])
        self.assertEqual(snapshot._plain(Path("x"), bad), "x")
        self.assertIs(snapshot._plain(False, bad), False)

    def test_code_commit_never_crashes(self):
        with mock.patch.object(snapshot.subprocess, "run", side_effect=OSError("no git")):
            self.assertIsNone(snapshot.code_commit())
        failed = subprocess.CompletedProcess([], 128, stdout="", stderr="not a repo")
        with mock.patch.object(snapshot.subprocess, "run", return_value=failed):
            self.assertIsNone(snapshot.code_commit())
        with mock.patch.object(snapshot.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("git", 10)):
            self.assertIsNone(snapshot.code_commit())

    def test_crash_before_replace_leaves_only_ignored_files(self):
        real = os.replace

        def replace(src, dst):  # the cache's own atomic writes still work
            if Path(src).name == snapshot.TEMP_NAME:
                raise OSError("disk full")
            return real(src, dst)
        with mock.patch.object(snapshot.os, "replace", replace):
            with self.assertRaises(OSError):
                self.rerun("--no-slope")
        self.assertFalse((self.out / snapshot.FILE_NAME).exists())
        left = [p.relative_to(self.tmp) for p in (self.tmp / "output").rglob("*") if p.is_file()]
        self.assertIn(Path("output") / self.out.name / snapshot.TEMP_NAME, left)
        r = git("rev-parse", "--is-inside-work-tree")
        if r is None or r.returncode != 0:
            self.skipTest("git is unavailable; check-ignore not run")
        for path in left:
            r = git("check-ignore", "-q", str(path))
            self.assertEqual(r.returncode, 0, f"{path} would not be ignored under screener/")

    def test_failed_rerun_never_pairs_new_ranked_with_old_snapshot(self):
        # Reviewer fix: a same-day re-run rewrites ranked.csv in place. If it
        # then fails before the snapshot lands, the first run's features.json
        # must not survive next to the new ranked.csv, or decide.py would
        # store the earlier run's features for a decision made on this one.
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        self.assertEqual(self.snap()["records"][0]["features"]["acres"], 20.0)
        self.fresh[0]["acres"] = 30.0
        with mock.patch.object(snapshot, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.rerun("--no-slope")
        self.assertEqual(float(self.ranked()["14F0001 0001"]["acres"]), 30.0)
        self.assertFalse((self.out / snapshot.FILE_NAME).exists())
        code, _, err = self.decide("14F0001 0001", "pursue")
        self.assertEqual(code, 2)
        self.assertIn("--no-features", err)
        self.assertEqual(self.store(), [])

    def test_clear_is_a_no_op_without_a_snapshot(self):
        self.out.mkdir(parents=True)
        snapshot.clear(self.out)  # no error when nothing is there
        self.assertEqual(list(self.out.iterdir()), [])


class IgnoreTests(unittest.TestCase):
    """H3: every path the new code can write is ignored; the README is tracked."""

    def setUp(self):
        r = git("rev-parse", "--is-inside-work-tree")
        if r is None or r.returncode != 0:
            self.skipTest("git is unavailable; check-ignore not run")

    def test_written_paths_are_ignored(self):
        inputs = Path("inputs")
        run_dir = Path("output") / "fulton-2026-11-09"
        paths = [inputs / run.DECISIONS_FILE.name, inputs / decide.STORE_NAME,
                 run_dir / snapshot.FILE_NAME, run_dir / snapshot.TEMP_NAME,
                 # what a spreadsheet or editor leaves next to the file
                 inputs / (run.DECISIONS_FILE.name + ".tmp"),
                 inputs / (run.DECISIONS_FILE.name + ".bak"),
                 inputs / ("~$" + run.DECISIONS_FILE.name),
                 inputs / (decide.STORE_NAME + ".tmp")]
        for path in paths:
            self.assertEqual(git("check-ignore", "-q", str(path)).returncode, 0,
                             f"{path} is not ignored")

    def test_readme_stays_tracked(self):
        self.assertEqual(git("ls-files", "--error-unmatch", "inputs/README.md").returncode, 0)
        self.assertEqual(git("check-ignore", "-q", "inputs/README.md").returncode, 1)


class FeatureStoreTests(DecisionHarness):
    """H5: a decision's features outlive the run folder."""

    def test_features_survive_rerun_deletion_and_pass(self):
        self.assertEqual(self.rerun()[0], 0)
        code, out, _ = self.decide("14F0001 0001", "pursue", "called", "owner")
        self.assertEqual(code, 0, out)
        code, out, _ = self.decide("14F0002 0002", "pass")
        self.assertEqual(code, 0)
        stored = decide.store_file().read_bytes()
        entries = self.store()
        self.assertEqual([e["parcel_id"] for e in entries], ["14F0001 0001", "14F0002 0002"])
        for e in entries:
            [rec] = e["records"]
            self.assertEqual(list(rec["features"]), build_eval.FEATURES)
            self.assertEqual(e["run"], self.out.name)
            self.assertEqual(e["run_meta"]["county"], "Fulton")
            self.assertNotIn("note", e)  # the reason stays in decisions.csv only
        self.assertNotIn("called", decide.store_file().read_text())

        # (a) a same-day re-run overwrites the folder, with different data
        self.fresh[0]["acres"] = 30.0
        self.assertEqual(self.rerun()[0], 0)
        self.assertEqual(decide.store_file().read_bytes(), stored)
        # (c) the passed parcel is no longer in the new run at all
        self.assertNotIn("14F0002 0002", self.ranked())
        self.assertNotIn("14F0002 0002", {r["parcel_id"] for r in self.snap()["records"]})
        # (b) the output folder is deleted
        shutil.rmtree(self.tmp / "output")
        self.assertEqual(decide.store_file().read_bytes(), stored)
        self.assertEqual(self.store()[0]["records"][0]["features"]["acres"], 20.0)


class DecisionsFileTests(DecisionHarness):
    """H6: decide.py and run.py on hand-edited decisions files."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.rerun("--no-slope")[0], 0)

    def loaded(self):
        return run.load_decisions()

    def test_created_with_the_run_header_when_absent(self):
        run.DECISIONS_FILE.unlink()
        self.assertEqual(self.decide("14F0001 0001", "maybe")[0], 0)
        lines = run.DECISIONS_FILE.read_text().splitlines()
        self.assertEqual(lines[0], ",".join(run.DECISIONS_HEADER))
        self.assertEqual(sum(1 for x in lines if x.startswith("parcel_id")), 1)
        self.assertEqual(self.loaded()["14F0001 0001"]["decision"], "maybe")

    def test_empty_file_gets_a_header(self):
        run.DECISIONS_FILE.write_bytes(b"")
        self.assertEqual(self.decide("14F0001 0001", "maybe")[0], 0)
        self.assertEqual(self.loaded()["14F0001 0001"]["decision"], "maybe")

    def test_missing_final_newline(self):
        self.write_decisions("parcel_id,decision,date,note\r\n14F0003 0003,pursue,2026-11-01,hi")
        self.assertEqual(self.decide("14F0001 0001", "maybe")[0], 0)
        got = self.loaded()
        self.assertEqual(got["14F0003 0003"], {"decision": "pursue", "date": "2026-11-01",
                                               "note": "hi"})
        self.assertEqual(got["14F0001 0001"]["decision"], "maybe")

    def test_lf_only_file(self):
        self.write_decisions("parcel_id,decision,date,note\n14F0003 0003,pursue,2026-11-01,hi\n")
        self.assertEqual(self.decide("14F0001 0001", "maybe")[0], 0)
        self.assertEqual(set(self.loaded()), {"14F0003 0003", "14F0001 0001"})

    def test_crlf_file(self):
        self.write_decisions("parcel_id,decision,date,note\r\n14F0003 0003,pursue,2026-11-01,hi\r\n")
        self.assertEqual(self.decide("14F0001 0001", "maybe")[0], 0)
        self.assertEqual(set(self.loaded()), {"14F0003 0003", "14F0001 0001"})

    def test_reason_with_comma_quote_and_newline(self):
        reason = 'owner said "maybe, in spring"\nsecond line'
        self.assertEqual(self.decide("14F0001 0001", "pursue", reason)[0], 0)
        self.assertEqual(self.loaded()["14F0001 0001"]["note"], reason)

    def test_bom_file_is_read_and_its_pass_honoured(self):
        self.write_decisions("﻿parcel_id,decision,date,note\r\n"
                             "14F0002 0002,pass,2026-11-01,easement\r\n", encoding="utf-8")
        self.assertEqual(self.loaded()["14F0002 0002"]["decision"], "pass")
        self.assertEqual(self.decide("14F0001 0001", "maybe")[0], 0)
        self.assertEqual(set(self.loaded()), {"14F0002 0002", "14F0001 0001"})
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        self.assertIn("14F0002 0002", self.rejected())
        self.assertNotIn("14F0002 0002", self.ranked())
        self.assertEqual(self.ranked()["14F0001 0001"]["decision"], "maybe")

    def test_old_four_column_header(self):
        self.write_decisions("parcel_id,decision,date,note\n14F0003 0003,pass,2026-11-01,x\n")
        self.assertEqual(self.decide("14F0001 0001", "pursue", "r")[0], 0)
        with open(run.DECISIONS_FILE, newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0], run.DECISIONS_HEADER)
        self.assertTrue(all(len(r) == 4 for r in rows))
        self.assertEqual(self.loaded()["14F0001 0001"]["note"], "r")

    def test_reordered_or_wider_header_lines_up(self):
        self.write_decisions("note,parcel_id,extra,decision,date\nx,14F0003 0003,,pass,2026-11-01\n")
        self.assertEqual(self.decide("14F0001 0001", "pursue", "r")[0], 0)
        got = self.loaded()
        self.assertEqual(got["14F0001 0001"]["note"], "r")
        self.assertEqual(got["14F0001 0001"]["decision"], "pursue")
        self.assertEqual(got["14F0003 0003"]["decision"], "pass")

    def test_padded_header_names_are_read_and_the_pass_honoured(self):
        # Post-change review round 1: decide.py strips header names when it
        # checks and appends; load_decisions must read the same file the
        # same way, or a recorded pass is silently never applied.
        self.write_decisions("parcel_id, decision, date, note\n"
                             "14F0003 0003, pursue ,2026-11-01,hi\n")
        code, out, _ = self.decide("14F0002 0002", "pass", "--no-features")
        self.assertEqual(code, 0)
        self.assertIn("dropped from future runs", out)
        got = self.loaded()
        self.assertEqual(got["14F0002 0002"]["decision"], "pass")
        self.assertEqual(got["14F0003 0003"], {"decision": "pursue", "date": "2026-11-01",
                                               "note": "hi"})
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        self.assertIn("14F0002 0002", self.rejected())
        self.assertNotIn("14F0002 0002", self.ranked())
        self.assertEqual(self.ranked()["14F0003 0003"]["decision"], "pursue")

    def test_padded_header_with_bom_and_tabs(self):
        self.write_decisions("\ufeff parcel_id ,\tdecision\t,date,note\n"
                             "14F0002 0002,pass,2026-11-01,x\n")
        self.assertEqual(self.loaded()["14F0002 0002"]["decision"], "pass")
        self.assertEqual(self.decide("14F0001 0001", "maybe")[0], 0)
        self.assertEqual(set(self.loaded()), {"14F0002 0002", "14F0001 0001"})

    def test_header_only_file_loads_nothing(self):
        self.write_decisions("parcel_id, decision, date, note\n")
        self.assertEqual(self.loaded(), {})

    def test_headerless_file_is_refused_and_nothing_written(self):
        self.write_decisions("14F0003 0003,pass,2026-11-01,x\n")
        before = run.DECISIONS_FILE.read_bytes()
        code, _, err = self.decide("14F0001 0001", "pursue")
        self.assertEqual(code, 2)
        self.assertIn("header", err)
        self.assertEqual(run.DECISIONS_FILE.read_bytes(), before)
        self.assertFalse(decide.store_file().exists())


class CliTests(DecisionHarness):
    """H7: exact ids, refusals, run choice, values, consequences, last wins."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        self.before = run.DECISIONS_FILE.read_bytes()

    def assert_nothing_written(self):
        self.assertEqual(run.DECISIONS_FILE.read_bytes(), self.before)
        self.assertFalse(decide.store_file().exists())

    def test_internal_spaces_are_kept(self):
        self.assertIn(SPACED, self.ranked())
        code, _, err = self.decide("14F0063 LL0219", "pursue")
        self.assertEqual(code, 2)
        self.assertIn(repr(SPACED), err)  # shown as a close match, never picked
        self.assert_nothing_written()
        code, out, _ = self.decide(f"  {SPACED} ", "pursue")
        self.assertEqual(code, 0, out)
        self.assertEqual(run.load_decisions()[SPACED]["decision"], "pursue")
        self.assertEqual(self.store()[0]["parcel_id"], SPACED)

    def test_unknown_id_refuses(self):
        code, _, err = self.decide("99X9999 9999", "maybe")
        self.assertEqual(code, 2)
        self.assertIn("not in this run", err)
        self.assert_nothing_written()

    def test_no_run_refuses(self):
        shutil.rmtree(self.tmp / "output")
        code, _, err = self.decide("14F0001 0001", "maybe")
        self.assertEqual(code, 2)
        self.assertIn("no run found", err)
        self.assert_nothing_written()

    def test_run_override_and_latest_choice(self):
        older = self.out
        self.out = self.tmp / "output" / "fulton-2026-11-10"
        self.fresh = [p for p in self.fresh if p["parcel_id"] != "14F0003 0003"]
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        os.utime(older / "ranked.csv", ns=(1, 1))
        # newest run lacks 14F0003 0003
        code, out, err = self.decide("14F0003 0003", "maybe")
        self.assertEqual(code, 2)
        self.assertIn(str(self.out), out)
        code, out, _ = self.decide("14F0003 0003", "maybe", "--run", str(older))
        self.assertEqual(code, 0, out)
        self.assertIn(f"run: {older}", out)
        self.assertEqual(self.store()[0]["run"], older.name)
        code, _, err = self.decide("14F0001 0001", "maybe", "--run", str(self.tmp / "nowhere"))
        self.assertEqual(code, 2)
        self.assertIn("no ranked.csv", err)

    def test_tied_latest_runs_refuse(self):
        other = self.tmp / "output" / "dekalb-2026-11-09"
        shutil.copytree(self.out, other)
        st = (self.out / "ranked.csv").stat()
        os.utime(other / "ranked.csv", ns=(st.st_atime_ns, st.st_mtime_ns))
        code, _, err = self.decide("14F0001 0001", "maybe")
        self.assertEqual(code, 2)
        self.assertIn("tie", err)
        self.assert_nothing_written()

    def test_every_value_and_an_invalid_one(self):
        for value in ("pursue", "PASS", "Maybe", "listed"):
            code, out, _ = self.decide("14F0001 0001", value)
            self.assertEqual(code, 0, out)
            self.assertIn(decide.CONSEQUENCE[value.lower()], out)
            self.assertEqual(run.load_decisions()["14F0001 0001"]["decision"], value.lower())
        written = run.DECISIONS_FILE.read_bytes()
        for value in ("skip", "", "pursued"):
            code, _, err = self.decide("14F0001 0001", value)
            self.assertEqual(code, 2)
            self.assertIn("invalid choice", err)
        self.assertEqual(run.DECISIONS_FILE.read_bytes(), written)

    def test_pass_prints_its_consequence(self):
        code, out, _ = self.decide("14F0001 0001", "pass")
        self.assertEqual(code, 0)
        self.assertIn("will be dropped from future runs", out)

    def test_append_only_last_wins(self):
        self.decide("14F0001 0001", "pursue", "first")
        self.decide("14F0001 0001", "pursue", "first")
        self.decide("14F0001 0001", "pass", "second")
        with open(run.DECISIONS_FILE, newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([r["decision"] for r in rows], ["pursue", "pursue", "pass"])
        self.assertTrue(run.DECISIONS_FILE.read_bytes().startswith(self.before))
        self.assertEqual(run.load_decisions()["14F0001 0001"],
                         {"decision": "pass", "date": rows[-1]["date"], "note": "second"})
        self.assertEqual(len(self.store()), 3)

    def test_rejected_parcel_needs_no_features_flag(self):
        self.write_decisions("parcel_id,decision,date,note\n14F0003 0003,listed,2026-11-01,\n")
        self.assertEqual(self.rerun("--no-slope")[0], 0)
        self.before = run.DECISIONS_FILE.read_bytes()
        self.assertIn("14F0003 0003", self.rejected())
        code, _, err = self.decide("14F0003 0003", "pass")
        self.assertEqual(code, 2)
        self.assertIn("--no-features", err)
        self.assert_nothing_written()
        code, out, _ = self.decide("14F0003 0003", "pass", "--no-features")
        self.assertEqual(code, 0)
        self.assertIn("missing: " + ", ".join(build_eval.FEATURES), out)
        self.assertEqual(run.load_decisions()["14F0003 0003"]["decision"], "pass")
        self.assertEqual(self.store()[0]["records"], [])

    def test_run_without_snapshot(self):
        (self.out / snapshot.FILE_NAME).unlink()
        code, _, err = self.decide("14F0001 0001", "maybe")
        self.assertEqual(code, 2)
        self.assertIn(f"no {snapshot.FILE_NAME}", err)
        self.assert_nothing_written()
        self.assertEqual(self.decide("14F0001 0001", "maybe", "--no-features")[0], 0)
        self.assertIsNone(self.store()[0]["run_meta"])

    def test_unreadable_snapshot_refuses(self):
        (self.out / snapshot.FILE_NAME).write_text("{not json")
        code, _, err = self.decide("14F0001 0001", "maybe", "--no-features")
        self.assertEqual(code, 2)
        self.assertIn("unreadable", err)
        self.assert_nothing_written()

    def test_no_rank_shorthand(self):
        code, _, err = self.decide("1", "pursue")
        self.assertEqual(code, 2)
        self.assertIn("not in this run", err)
        self.assert_nothing_written()


class OfflineImportTests(unittest.TestCase):
    """H8: decide.py and snapshot.py are stdlib plus screener modules only."""

    def test_imports(self):
        stdlib = {"argparse", "csv", "json", "os", "sys", "time", "datetime", "pathlib",
                  "typing", "hashlib", "math", "subprocess", "__future__"}
        for name in ("decide.py", "snapshot.py"):
            tree = ast.parse((SCREENER / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertIn(a.name.split(".")[0], stdlib, f"{name}: {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0:
                        self.assertIn(node.module.split(".")[0], stdlib, f"{name}: {node.module}")
                    else:
                        self.assertIn(node.module, (None, "autoresearch.build_eval"),
                                      f"{name}: from .{node.module}")

    def test_paths_resolve_at_call_time(self):
        with mock.patch.object(run, "DECISIONS_FILE", Path("/x/y/decisions.csv")), \
                mock.patch.object(run, "HERE", Path("/x")):
            self.assertEqual(decide.decisions_file(), Path("/x/y/decisions.csv"))
            self.assertEqual(decide.store_file(), Path("/x/y") / decide.STORE_NAME)
            self.assertEqual(decide.output_root(), Path("/x/output"))


if __name__ == "__main__":
    unittest.main()
