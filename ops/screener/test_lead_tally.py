"""Offline tests for lead_tally.py: made-up lead folders, no network.

    python -m unittest discover -s ops/screener -p 'test_*.py'
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lead_tally  # noqa: E402

UNKNOWN = {"zoning": "unknown", "plans": "unknown", "utilities": "unknown", "market": "unknown",
           "owner": "unknown"}


def run_main(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = lead_tally.main([str(a) for a in argv])
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.leads = self.tmp / "leads"
        self.leads.mkdir()

    def lead(self, pid, verdict="weakened", objection="", folder=None, supported=True, **sections):
        d = self.leads / (folder or pid.replace(" ", "_"))
        d.mkdir()
        doc = {"schema": 1, "parcel_id": pid, "run": "fulton-2026-11-10",
               "sections": {k: {"verdict": v, "claims": [], "open_items": []}
                            for k, v in {**UNKNOWN, **sections}.items()}}
        if verdict is not None:
            doc["skeptic"] = {"verdict": verdict, "objection": objection, "supported": supported}
        (d / "findings.checked.json").write_text(json.dumps(doc))
        return d

    def run_dir(self, name, ranked, rejected=()):
        d = self.tmp / "output" / name
        d.mkdir(parents=True)
        with open(d / "ranked.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, ["rank", "parcel_id", "lead_grade"])
            w.writeheader()
            w.writerows(ranked)
        with open(d / "rejected.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, ["parcel_id", "reasons"])
            w.writeheader()
            w.writerows(rejected)
        return d

    def text(self):
        return (self.leads / lead_tally.FILE_NAME).read_text()

    def row(self, label):
        return next(l for l in self.text().splitlines() if l.startswith(f"| {label}"))


class ReasonTests(Base):
    def reasons(self, objection, **sections):
        lead = {"objection": objection, "sections": {**UNKNOWN, **sections}}
        return lead_tally.reasons_of(lead)

    def test_reasons_from_the_skeptics_words(self):
        cases = {
            "This parcel is not raw land. Half of it is a paved trailer yard.": ["built"],
            "This is not land: it is the site of an operating manufacturing building.": ["built"],
            "A developer applied for the rezoning and won it on a revote.": ["developer"],
            "The tax-roll owner carries the quarry operator's name; it is buffer land.": ["operator"],
            "The grade rests on a future land use label the city has deprecated.": ["plan"],
            "A warehouse here is not by right: it is a use-permit use.": ["zoning"],
            "The lot is a curved strip; it cannot hold a warehouse.": ["shape"],
            "A cemetery shares the north lot line.": ["neighbour"],
            "The seller has not been tested.": [],
            "It is neither vacant nor idle: two buildings stand on this parcel.": ["built"],
            "The site is operated by a waste hauler.": ["operator"],
            "It would need a rezoning: its use list bars wholesaling.": ["zoning"],
            "Single-family homes and apartments line the east side.": ["neighbour"],
            "The rest is a wooded, sloping strip.": [],
            "It is the site of a data center project that is permitted, not built.": [],
        }
        for objection, want in cases.items():
            self.assertEqual(self.reasons(objection)[0], want, objection)

    def test_words_that_named_a_neighbours_site_are_not_read_as_built(self):
        # Each of these matched "built" before the patterns were tightened
        # on the 29 real leads (reviews/41).
        for objection in ("The parcel borders a 310-unit apartment complex and its leasing facility.",
                          "The county shows 1.5 million dollars of improvements on the parcel next door.",
                          "A developer applied to build a 280,000 sq ft building; staff recommended denial.",
                          "The owner is an arm of an operating railroad."):
            self.assertNotIn("built", self.reasons(objection)[0], objection)

    def test_a_sentence_that_found_nothing_names_no_reason(self):
        words, _ = self.reasons("Nothing found shows a building, a developer or a sale on this parcel, "
                                "and the maps still show Industrial future land use. But it is a narrow wedge.")
        self.assertEqual(words, ["shape"])
        # ...but what follows ", so" is the skeptic's own finding again.
        words, _ = self.reasons("No document found (site plan, permit) shows what the owner plans, so "
                                "'expansion or buffer land, not available' is still an inference.")
        self.assertEqual(words, ["operator"])

    def test_reasons_from_the_checked_sections(self):
        _, by = self.reasons("", market="signs_under_contract", plans="against", zoning="rezoning_needed",
                             owner="operator")
        self.assertEqual(by, ["developer", "operator", "plan", "zoning"])
        self.assertEqual(self.reasons("", market="no_sign_found", plans="supports", zoning="by_right")[1], [])

    def test_every_pattern_compiles_and_every_reason_says_what_to_look_at(self):
        import re
        for code, r in lead_tally.REASONS.items():
            self.assertTrue(r["label"] and r["look"], code)
            for pattern in r["words"]:
                re.compile(pattern)
            self.assertLessEqual(set(r["sections"]), set(lead_tally.SECTIONS))


class TallyTests(Base):
    def setUp(self):
        super().setUp()
        self.lead("A 1", "killed", "This is not raw land. It is the site of an operating plant.")
        self.lead("A2", "weakened", "This parcel is not vacant: a paved trailer yard covers half of it.")
        self.lead("A3", "weakened", "A developer bought it in 2024.", market="signs_developer_pursuing")
        self.lead("A4", "survives", "Nothing found works against the lead.")
        self.lead("A5", None)
        self.lead("A6", "weakened", "The seller is the open question.")
        self.run = self.run_dir("fulton-2026-11-17",
                                [{"rank": 1, "parcel_id": "A 1", "lead_grade": "A"},
                                 {"rank": 9, "parcel_id": "A3", "lead_grade": "C"},
                                 {"rank": 12, "parcel_id": "A6", "lead_grade": "not graded: soils"}],
                                [{"parcel_id": "A2", "reasons": "already built on (improvements $900,000)"}])

    def test_counts_and_the_summary_line(self):
        rc, out, err = run_main(self.leads, "--run", self.run)
        self.assertEqual(rc, 0, err)
        text = self.text()
        self.assertIn("6 leads have a document check: 1 killed, 3 weakened, 1 survives, 1 without a skeptic verdict.", text)
        self.assertIn("1 of the 4 killed or weakened leads are still grade A or B there.", text)
        self.assertEqual(self.row("Not raw land"), "| Not raw land: a building, a yard or an operating site "
                         "is on it | 1 | 1 | 2 / n/a | 1 |")
        self.assertIn("| 0 | 1 | 1 / 1 | 0 |", self.row("A developer or investor"))
        self.assertIn("| No reason found | 0 | 1 | n/a | 0 |", text)
        first = out.splitlines()[0]
        self.assertIn("6 leads checked so far: 1 killed, 3 weakened, 1 survive.", first)
        self.assertIn("(2 leads, 1 still grade A or B)", first)
        for pid in ("A 1", "A2", "A3"):
            self.assertNotIn(pid, first)                       # counts only

    def test_a_proposal_needs_two_leads_and_says_whether_the_gap_is_open(self):
        run_main(self.leads, "--run", self.run)
        text = self.text()
        what = text[text.index("## What to look at"):text.index("## Every lead")]
        self.assertIn("### Not raw land", what)
        self.assertIn("2 leads (1 killed); 1 of them still come through as grade A or B in the newest run "
                      "that has them.", what)
        self.assertIn("built_min_building_sf", what)
        self.assertIn("`A 1` (still A/B), `A2`.", what)
        self.assertNotIn("### A developer", what)              # one lead only
        self.assertIn("screened out: already built on (improvements $900,000) (fulton-2026-11-17)", text)

    def test_without_a_run_nothing_is_compared(self):
        self.assertEqual(run_main(self.leads)[0], 0)
        text = self.text()
        self.assertIn("not compared", self.row("Not raw land"))
        self.assertIn("not compared with a run (pass --run).", text)
        self.assertNotIn("Today |", text)

    def test_the_newest_run_that_knows_a_parcel_is_used(self):
        older = self.run_dir("fulton-2026-11-10", [{"rank": 3, "parcel_id": "A2", "lead_grade": "B"},
                                                   {"rank": 4, "parcel_id": "A7", "lead_grade": "A"}])
        self.lead("A7", "killed", "This is not raw land.")
        run_main(self.leads, "--run", older, "--run", self.run)
        text = self.text()
        self.assertIn("screened out: already built on", next(l for l in text.splitlines() if l.startswith("| `A2`")))
        self.assertIn("rank 4, grade A (fulton-2026-11-10)", next(l for l in text.splitlines() if l.startswith("| `A7`")))

    def test_a_reason_already_closed_reads_as_closed(self):
        closed = self.run_dir("fulton-2026-11-24", [{"rank": 1, "parcel_id": "OTHER", "lead_grade": "A"}],
                              [{"parcel_id": "A 1", "reasons": "already built on"},
                                                        {"parcel_id": "A2", "reasons": "already built on"}])
        run_main(self.leads, "--run", closed)
        self.assertIn("none of them is grade A or B in the newest run that has them: screened out, "
                      "regraded, or marked.", self.text())
        rc, out, _ = run_main(self.leads, "--run", closed)
        self.assertNotIn("still coming through", out)             # nothing of it comes through

    def test_a_reason_from_a_checked_section_alone_is_counted(self):
        # reviews/41: such a lead was in no row at all.
        self.lead("S1", "weakened", "The seller has not been tested.", owner="operator")
        self.lead("S2", "killed", "", owner="operator")
        run_main(self.leads, "--run", self.run)
        self.assertIn("| 1 | 1 | 0 / 2 | 0 (2 in none of the runs) |", self.row("Held by an operating company"))
        self.assertIn("| No reason found | 0 | 1 | n/a | 0 |", self.text())
        self.assertIn("none of them is in the runs given, so nothing can be said about today.", self.text())

    def test_leads_in_none_of_the_runs_are_not_read_as_screened_out(self):
        elsewhere = self.run_dir("cobb-2026-11-17", [{"rank": 1, "parcel_id": "ZZ", "lead_grade": "A"}])
        run_main(self.leads, "--run", elsewhere)
        self.assertIn("| 1 | 1 | 2 / n/a | 0 (2 in none of the runs) |", self.row("Not raw land"))
        self.assertIn("none of them is in the runs given", self.text())

    def test_runs_are_ordered_by_their_date_whatever_the_county(self):
        self.run_dir("cobb-2026-11-24", [{"rank": 7, "parcel_id": "A3", "lead_grade": "A"}])
        run_main(self.leads, "--run", self.tmp / "output" / "cobb-2026-11-24", "--run", self.run)
        self.assertIn("rank 7, grade A (cobb-2026-11-24)", next(l for l in self.text().splitlines()
                                                                 if l.startswith("| `A3`")))

    def test_a_run_that_lists_no_parcel_is_refused(self):
        for name, text in (("fulton-2026-12-01", ""), ("fulton-2026-12-02", "rank,lead_grade\n1,A\n")):
            d = self.tmp / "output" / name
            d.mkdir(parents=True)
            (d / "ranked.csv").write_text(text)
            rc, _, err = run_main(self.leads, "--run", d)
            self.assertEqual(rc, 2)
            self.assertIn("lists no parcel", err)


class SafetyTests(Base):
    def test_agent_words_are_reduced_to_plain_text(self):
        self.lead("X1", "killed", "See [here](https://evil.example/x) <script>alert(1)</script> | a | b |\n\n"
                                  "# Heading `code` www.evil.example and clerk.zone or mailto:a@b.c **bold**")
        self.lead("X2", "weakened", "x")
        self.assertEqual(run_main(self.leads)[0], 0)
        row = next(l for l in self.text().splitlines() if l.startswith("| `X1`"))
        for bad in ("](", "<", ">", "`code`", "https", "www.", "**", "mailto", "clerk.zone", "@"):
            self.assertNotIn(bad, row, bad)
        self.assertEqual(row.count("|"), next(l for l in self.text().splitlines()
                                              if l.startswith("| Parcel |")).count("|"))
        self.assertEqual([l for l in self.text().splitlines() if l.startswith("# ")],
                         ["# Lead checks: why leads fail"])

    def test_plain(self):
        self.assertEqual(lead_tally.plain("a\nb  c", 50), "a b c")
        self.assertEqual(lead_tally.plain("x" * 400, 10), "x" * 10 + " ...")
        self.assertEqual(lead_tally.plain("visit http://a.b/c now", 80), "visit a. b/c now")
        for hostile in ('see https"://203.0.113.5/login now', "ht*tps://3405803781/x", "h_t_t_p://evil.example",
                        "ftps://x.y", "see www.evil.example", "a://b"):
            got = lead_tally.plain(hostile, 80)
            self.assertNotRegex(got, r"://|www\.|\d\.\d", hostile)
        self.assertEqual(lead_tally.plain(None, 10), "")
        # reviews/41, second look: an entity is an address or a column to whatever renders the file.
        for hostile in ("&#104;ttps&#58;//evil.example/a", "a &#124; b", "&lt;b&gt;"):
            self.assertNotIn("&", lead_tally.plain(hostile, 80), hostile)
        self.assertEqual(lead_tally.plain("Smith & Sons", 80), "Smith and Sons")

    def test_a_long_objection_is_cut(self):
        self.lead("X1", "killed", "This is not raw land. " + "word " * 500)
        run_main(self.leads)
        row = next(l for l in self.text().splitlines() if l.startswith("| `X1`"))
        self.assertLess(len(row), 600)

    def test_folders_that_are_not_this_parcels_lead_are_left_out(self):
        self.lead("G1", "killed", "This is not raw land.")
        self.lead("OTHER", "killed", "not raw land", folder="G2")                # about another parcel
        (self.leads / "G3").mkdir()                                                # no checked file
        (self.leads / "G4").mkdir()
        (self.leads / "G4" / "findings.checked.json").write_text("{nope")
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "findings.checked.json").write_text(json.dumps({"parcel_id": "G5", "skeptic": {"verdict": "killed"}}))
        os.symlink(outside, self.leads / "G5")                                    # a linked folder
        (self.leads / "G6").mkdir()
        os.symlink(outside / "findings.checked.json", self.leads / "G6" / "findings.checked.json")
        (self.leads / ".incoming-G7-1").mkdir()
        (self.leads / "notes.txt").write_text("x")
        rc, out, err = run_main(self.leads)
        self.assertEqual(rc, 0, err)
        text = self.text()
        self.assertIn("1 leads have a document check: 1 killed", text)
        self.assertIn("4 lead folders have no readable checked file and are left out.", text)
        self.assertNotIn("G5", text)

    def test_odd_verdicts_and_section_values_are_not_copied(self):
        d = self.lead("V1", "killed", "ok")
        doc = json.loads((d / "findings.checked.json").read_text())
        doc["skeptic"]["verdict"] = "<b>fine</b>"
        doc["sections"]["zoning"]["verdict"] = "by_right | [x](y)"
        doc["sections"]["plans"] = "not a dict"
        (d / "findings.checked.json").write_text(json.dumps(doc))
        self.assertEqual(run_main(self.leads)[0], 0)
        row = next(l for l in self.text().splitlines() if l.startswith("| `V1`"))
        self.assertIn("| no skeptic |", row)
        self.assertIn("unknown / unknown / unknown / unknown / unknown", row)

    def test_run_and_reject_text_is_plain_too(self):
        self.lead("M1", "killed", "This is not raw land.")
        self.lead("M2", "weakened", "This is not raw land.")
        d = self.tmp / "output" / "fulton-2026-11-17 [x](http:evil) `c`"
        d.mkdir(parents=True)
        (d / "ranked.csv").write_text("rank,parcel_id,lead_grade\n1 | [r](u),M1,A <b>\n")
        (d / "rejected.csv").write_text('parcel_id,reasons\nM2,"built | <i>x</i> [l](https://evil.example) ~~s~~ &#124;"\n')
        self.assertEqual(run_main(self.leads, "--run", d)[0], 0)
        text = self.text()
        for bad in ("](", "<b>", "<i>", "evil.example", "~~", "&#124;"):
            self.assertNotIn(bad, text, bad)
        head = next(l for l in text.splitlines() if l.startswith("| Parcel |"))
        for pid in ("M1", "M2"):
            self.assertEqual(next(l for l in text.splitlines() if l.startswith(f"| `{pid}`")).count("|"), head.count("|"))

    def test_an_odd_section_verdict_cannot_be_emphasis(self):
        d = self.lead("V2", "killed", "x")
        doc = json.loads((d / "findings.checked.json").read_text())
        doc["sections"]["zoning"]["verdict"] = "_a_"
        doc["sections"]["plans"]["verdict"] = "x" * 41
        (d / "findings.checked.json").write_text(json.dumps(doc))
        run_main(self.leads)
        self.assertIn("unknown / unknown / unknown / unknown / unknown",
                      next(l for l in self.text().splitlines() if l.startswith("| `V2`")))

    def test_the_output_is_never_written_over_something_that_is_not_a_file(self):
        self.lead("W1", "killed", "x")
        folder = self.tmp / "afolder"
        folder.mkdir()
        self.assertEqual(run_main(self.leads, "--out", folder)[0], 2)
        (self.leads / lead_tally.FILE_NAME).mkdir()
        self.assertEqual(run_main(self.leads)[0], 2)
        (self.leads / lead_tally.FILE_NAME).rmdir()
        self.assertEqual(run_main(self.leads)[0], 0)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["afolder", "leads"])
        self.assertEqual(sorted(p.name for p in self.leads.iterdir()), ["W1", lead_tally.FILE_NAME])

    def test_refusals_write_nothing(self):
        self.assertEqual(run_main(self.leads)[0], 2)                       # no lead at all
        self.assertFalse((self.leads / lead_tally.FILE_NAME).exists())
        self.assertEqual(run_main(self.tmp / "nope")[0], 2)
        self.lead("R1", "killed", "x")
        self.assertEqual(run_main(self.leads, "--run", self.tmp / "norun")[0], 2)
        self.assertFalse((self.leads / lead_tally.FILE_NAME).exists())

    def test_the_file_is_not_written_through_a_link_and_leaves_no_temp(self):
        self.lead("R1", "killed", "x")
        target = self.tmp / "target.md"
        target.write_text("mine")
        os.symlink(target, self.leads / lead_tally.FILE_NAME)
        self.assertEqual(run_main(self.leads)[0], 2)
        self.assertEqual(target.read_text(), "mine")
        os.unlink(self.leads / lead_tally.FILE_NAME)
        # an old temp file of the same name is not written through either
        os.link(target, self.leads / (lead_tally.FILE_NAME + ".tmp"))
        self.assertEqual(run_main(self.leads)[0], 0)
        self.assertEqual(target.read_text(), "mine")
        os.unlink(self.leads / (lead_tally.FILE_NAME + ".tmp"))
        self.assertEqual(sorted(p.name for p in self.leads.iterdir()), ["R1", lead_tally.FILE_NAME])

    def test_the_tally_file_is_not_a_lead_folder_name(self):
        self.assertIsNone(lead_tally.SLUG.fullmatch(lead_tally.FILE_NAME))


if __name__ == "__main__":
    unittest.main()
