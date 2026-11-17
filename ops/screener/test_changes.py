"""Offline tests for changes.py: small made-up run folders, no network.

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
import changes  # noqa: E402

RANKED = ["rank", "score", "parcel_id", "jurisdiction", "site_address", "acres", "entitlement",
          "zoning", "envelope_fit", "envelope_building_sf", "lead_grade", "lead_found",
          "lead_not_checked", "owner", "owner_mailing", "last_sale_date", "last_sale_price",
          "decision"]
REJECTED = ["parcel_id", "jurisdiction", "site_address", "acres", "owner", "decision", "reasons"]
OWNER = "ZEBULON QUARTERMAINE HOLDINGS LLC"
MAILING = "77 SECRET MAILING LN"


def parcel(rank, pid, grade="C", **kw):
    row = {"rank": rank, "score": round(90 - rank * 0.1, 1), "parcel_id": pid,
           "jurisdiction": "Union City", "site_address": f"{rank} TEST RD", "acres": 12.5,
           "entitlement": "by_right", "zoning": "M-1", "envelope_fit": "fits",
           "envelope_building_sf": 150000, "lead_grade": grade, "lead_found": "",
           "lead_not_checked": "", "owner": OWNER, "owner_mailing": MAILING,
           "last_sale_date": "2001-01-01", "last_sale_price": "0.0", "decision": ""}
    row.update(kw)
    return row


def make_run(root: Path, name: str, ranked, rejected=(), *, county="Fulton",
             run_time="2026-11-10T12:00:00+0000", limit=None, notes=(), meta=True,
             commit="aaaaaaaaaaaa", settings="ssssssssssss") -> Path:
    d = root / name
    d.mkdir(parents=True)
    with open(d / "ranked.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, RANKED)
        w.writeheader()
        w.writerows(ranked)
    with open(d / "rejected.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, REJECTED)
        w.writeheader()
        w.writerows(rejected)
    (d / "summary.md").write_text("# run\n\n## Notes\n\n" + "".join(f"- {n}\n" for n in notes))
    if meta:
        (d / "features.json").write_text(json.dumps({"meta": {
            "county": county, "run_time": run_time, "limit": limit, "slope": True,
            "code_commit": commit, "settings_sha256": settings}, "records": []}))
    return d


def run_main(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = changes.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "output"
        self.root.mkdir()
        self.addCleanup(self._tmp.cleanup)


class PreviousRunTests(Base):
    def test_newest_earlier_run_of_the_same_county(self):
        make_run(self.root, "fulton-2026-11-01", [parcel(1, "A1")], run_time="2026-11-01T12:00:00+0000")
        mid = make_run(self.root, "fulton-2026-11-08", [parcel(1, "A1")], run_time="2026-11-08T12:00:00+0000")
        make_run(self.root, "will-2026-11-09", [parcel(1, "A1")], county="Will",
                 run_time="2026-11-09T12:00:00+0000")
        make_run(self.root, "fulton-2026-11-20", [parcel(1, "A1")], run_time="2026-11-20T12:00:00+0000")
        cur = make_run(self.root, "fulton-2026-11-15", [parcel(1, "A1")], run_time="2026-11-15T12:00:00+0000")
        self.assertEqual(changes.find_previous(cur), mid.resolve())

    def test_a_trial_run_is_not_the_run_before_a_full_one(self):
        full = make_run(self.root, "fulton-2026-11-01", [parcel(1, "A1")], run_time="2026-11-01T12:00:00+0000")
        trial = make_run(self.root, "fulton-2026-11-08", [parcel(1, "A1")], limit=200,
                         run_time="2026-11-08T12:00:00+0000")
        cur = make_run(self.root, "fulton-2026-11-15", [parcel(1, "A1")], run_time="2026-11-15T12:00:00+0000")
        self.assertEqual(changes.find_previous(cur), full.resolve())
        cur_trial = make_run(self.root, "fulton-2026-11-16", [parcel(1, "A1")], limit=200,
                             run_time="2026-11-16T12:00:00+0000")
        self.assertEqual(changes.find_previous(cur_trial), cur.resolve())
        self.assertIsNotNone(trial)

    def test_without_meta_the_folder_name_gives_the_county_and_the_file_time_the_order(self):
        old = make_run(self.root, "fulton-2026-11-01", [parcel(1, "A1")], meta=False)
        other = make_run(self.root, "will-2026-11-02", [parcel(1, "A1")], meta=False)
        cur = make_run(self.root, "fulton-2026-11-15", [parcel(1, "A1")], meta=False)
        os.utime(old / "ranked.csv", (1_000_000, 1_000_000))
        os.utime(other / "ranked.csv", (2_000_000, 2_000_000))
        os.utime(cur / "ranked.csv", (3_000_000, 3_000_000))
        self.assertEqual(changes.find_previous(cur), old.resolve())

    def test_first_run_has_none_and_still_writes(self):
        cur = make_run(self.root, "fulton-2026-11-15", [parcel(1, "A1", "A"), parcel(2, "B1", "B")])
        self.assertIsNone(changes.find_previous(cur))
        rc, out, _ = run_main(str(cur))
        self.assertEqual(rc, 0)
        text = (cur / "changes.md").read_text()
        self.assertIn("no earlier run", text)
        self.assertIn("## Grade A (1)", text)
        self.assertIn("first run", out)


class CompareTests(Base):
    def setUp(self):
        super().setUp()
        old_ranked = [
            parcel(1, "KEEP A", "A"),
            parcel(2, "A TO C", "A"),
            parcel(3, "A REJECTED", "A", decision="pursue"),
            parcel(4, "B GONE", "B"),
            parcel(5, "MARKED MOVES", "C", decision="maybe"),
            parcel(6, "DEED", "C"),
            *[parcel(r, f"FILL{r:03d}") for r in range(7, 61)],
        ]
        new_ranked = [
            parcel(1, "KEEP A", "A"),
            parcel(2, "NEW  A", "A"),                       # two spaces: must survive
            parcel(3, "C TO B", "B", lead_found="no pond room"),
            parcel(4, "A TO C", "C", lead_found="no frontage; no pond room"),
            parcel(5, "DEED", "C", last_sale_date="2026-10-30", last_sale_price="1200000.0",
                   owner="SOMEONE NEW LLC"),
            *[parcel(r, f"FILL{r - 1:03d}") for r in range(6, 30)],
            parcel(30, "MARKED MOVES", "C", decision="maybe"),
            *[parcel(r, f"FILL{r - 2:03d}") for r in range(31, 61)],
        ]
        old_rejected = [{"parcel_id": "NEW  A", "reasons": "already built on (improvements $90,000)"}]
        new_rejected = [{"parcel_id": "A REJECTED", "decision": "pursue",
                         "reasons": "already owned by an industrial developer / REIT"}]
        old_ranked.append(parcel(61, "C TO B", "C"))
        self.old = make_run(self.root, "fulton-2026-11-08", old_ranked, old_rejected,
                            run_time="2026-11-08T12:00:00+0000")
        self.new = make_run(self.root, "fulton-2026-11-15", new_ranked, new_rejected,
                            run_time="2026-11-15T12:00:00+0000", commit="bbbbbbbbbbbb",
                            notes=["SOIL SERVICE FAILURES: USDA Soil Data Access is down; re-run later.",
                                   "Soils not checked for 1332 of 1582 ranked parcels: outside the top 250"])
        rc, self.out, self.err = run_main(str(self.new))
        self.assertEqual(rc, 0, self.err)
        self.text = (self.new / "changes.md").read_text()

    def section(self, title: str) -> str:
        start = self.text.index("## " + title)
        end = self.text.find("\n## ", start + 1)
        return self.text[start:end if end > 0 else None]

    def test_new_leads_with_where_they_came_from(self):
        a = self.section("New grade A leads")
        self.assertIn("(1)", a.splitlines()[0])
        self.assertIn("`NEW  A`", a)                      # the id as written, spaces kept
        self.assertIn("screened out: already built on", a)
        self.assertNotIn("KEEP A", a)
        b = self.section("New grade B leads")
        self.assertIn("`C TO B`", b)
        self.assertIn("rank 61, grade C", b)
        self.assertIn("found: no pond room", b)

    def test_leads_that_are_gone_say_why(self):
        gone = self.section("No longer grade A or B")
        self.assertIn("(3)", gone.splitlines()[0])
        self.assertIn("rank 4, grade C (found: no frontage; no pond room)", gone)
        self.assertIn("screened out: already owned by an industrial developer / REIT", gone)
        self.assertIn("not in this run", gone)            # B GONE is in neither file

    def test_top_changes(self):
        self.assertIn("`NEW  A`", self.section("New in the top 50"))
        left = self.section("Left the top 50")
        self.assertIn("`A REJECTED`", left)
        self.assertIn("`B GONE`", left)
        moved = self.section("Moved 10 or more places in the top 50")
        self.assertIn("`MARKED MOVES`", moved)
        self.assertNotIn("FILL", moved)                   # a shift of one or two places is not listed

    def test_new_deed_and_owner_without_the_owner_name(self):
        deed = self.section("New deed or new owner on the tax roll")
        self.assertIn("`DEED`", deed)
        self.assertIn("2026-10-30, $1,200,000", deed)
        self.assertIn("changed", deed)
        self.assertNotIn("SOMEONE NEW", self.text)

    def test_marked_parcels(self):
        marked = self.section("Parcels you marked")
        self.assertIn("(2 changed)", marked.splitlines()[0])
        self.assertIn("pursue", marked)
        self.assertIn("screened out: already owned by an industrial developer", marked)
        self.assertIn("rank 30, grade C", marked)

    def test_failed_checks_are_listed_and_caps_are_not(self):
        notes = self.section("Checks that failed or did not run")
        self.assertIn("SOIL SERVICE FAILURES", notes)
        self.assertNotIn("outside the top 250", notes)

    def test_code_change_is_named(self):
        self.assertIn("The code changed between the two runs", self.text)
        self.assertNotIn("The settings file changed", self.text)

    def test_no_owner_name_or_mailing_address_anywhere(self):
        for text in (self.text, self.out):
            self.assertNotIn(OWNER, text)
            self.assertNotIn("QUARTERMAINE", text)
            self.assertNotIn(MAILING, text)

    def test_the_alert_summary_has_counts_only(self):
        first = self.out.splitlines()[0]
        self.assertIn("grade A 2 (1 new)", first)
        self.assertIn("grade B 1 (1 new)", first)
        self.assertIn("3 no longer A or B", first)
        self.assertNotIn("TEST RD", first)                # no site address
        self.assertNotIn("NEW  A", first)                 # no parcel id

    def test_an_explicit_previous_run_and_out_file(self):
        out = self.root / "elsewhere.md"
        rc, stdout, _ = run_main(str(self.new), "--previous", str(self.old), "--out", str(out))
        self.assertEqual(rc, 0)
        self.assertEqual(out.read_text(), self.text)
        self.assertIn(str(out), stdout)


class SafetyTests(Base):
    def test_county_text_cannot_add_markup_or_a_column(self):
        make_run(self.root, "fulton-2026-11-08", [parcel(1, "X1", "C")], run_time="2026-11-08T12:00:00+0000")
        cur = make_run(self.root, "fulton-2026-11-15",
                       [parcel(1, "X1", "A", site_address="1 MAIN | [x](http://evil.example) <b>`",
                               zoning="M-1\n| injected | row |")],
                       run_time="2026-11-15T12:00:00+0000")
        self.assertEqual(run_main(str(cur))[0], 0)
        text = (cur / "changes.md").read_text()
        self.assertNotIn("](http", text)
        self.assertNotIn("<b>", text)
        row = next(l for l in text.splitlines() if "1 MAIN" in l)
        head = next(l for l in text.splitlines() if l.startswith("| # | Score"))
        self.assertEqual(row.count("|"), head.count("|"))

    def test_an_odd_parcel_id_is_not_a_code_span(self):
        self.assertEqual(changes.pid_cell("14F0063  LL0219"), "`14F0063  LL0219`")
        self.assertNotIn("`", changes.pid_cell("AB`|C"))
        self.assertNotIn("|", changes.pid_cell("AB`|C"))

    def test_refusals_write_nothing(self):
        empty = self.root / "fulton-2026-11-15"
        empty.mkdir()
        rc, _, err = run_main(str(empty))
        self.assertEqual(rc, 2)
        self.assertIn("no ranked.csv", err)
        cur = make_run(self.root, "fulton-2026-11-16", [parcel(1, "X1")])
        for bad in (["--previous", str(cur)], ["--previous", str(empty)],
                    ["--leads", str(self.root / "nope")], ["--top", "0"]):
            rc, _, err = run_main(str(cur), *bad)
            self.assertEqual(rc, 2, bad)
            self.assertFalse((cur / "changes.md").exists())

    def test_no_temp_file_is_left(self):
        cur = make_run(self.root, "fulton-2026-11-15", [parcel(1, "X1")])
        self.assertEqual(run_main(str(cur))[0], 0)
        self.assertEqual(sorted(p.name for p in cur.iterdir() if p.name.startswith(".")), [])


class LeadCheckTests(Base):
    def lead(self, leads: Path, folder: str, pid: str, verdict):
        d = leads / folder
        d.mkdir(parents=True)
        doc = {"parcel_id": pid, "sections": {}}
        if verdict is not None:
            doc["skeptic"] = {"verdict": verdict}
        (d / "findings.checked.json").write_text(json.dumps(doc))

    def test_verdicts_by_parcel(self):
        leads = self.root.parent / "leads"
        self.lead(leads, "14F_1", "14F 1", "killed")
        self.lead(leads, "X2", "X2", None)
        self.lead(leads, "X3", "SOMETHING ELSE", "survives")   # another parcel's file
        self.lead(leads, "X4", "X4", "<script>")                # not a verdict
        self.assertEqual(changes.lead_verdict("14F 1", leads), "killed")
        self.assertEqual(changes.lead_verdict("X2", leads), "checked, no skeptic")
        self.assertEqual(changes.lead_verdict("X3", leads), "")
        self.assertEqual(changes.lead_verdict("X4", leads), "checked, no skeptic")
        self.assertEqual(changes.lead_verdict("X5", leads), "")
        self.assertEqual(changes.lead_verdict("../X2", leads), "")
        self.assertEqual(changes.lead_verdict("X2", None), "")

    def test_a_linked_lead_folder_is_not_followed_out_of_the_root(self):
        leads = self.root.parent / "leads"
        outside = self.root.parent / "outside"
        self.lead(outside, "X9", "X9", "killed")
        leads.mkdir(exist_ok=True)
        os.symlink(outside / "X9", leads / "X9")
        self.assertEqual(changes.lead_verdict("X9", leads), "")

    def test_the_digest_shows_and_counts_them(self):
        leads = self.root.parent / "leads"
        self.lead(leads, "A_1", "A 1", "killed")
        self.lead(leads, "B1", "B1", "weakened")
        make_run(self.root, "fulton-2026-11-08", [parcel(1, "Z9", "C")], run_time="2026-11-08T12:00:00+0000")
        cur = make_run(self.root, "fulton-2026-11-15",
                       [parcel(1, "A 1", "A"), parcel(2, "B1", "B"), parcel(3, "B2", "B")],
                       run_time="2026-11-15T12:00:00+0000")
        rc, out, _ = run_main(str(cur), "--leads", str(leads))
        self.assertEqual(rc, 0)
        text = (cur / "changes.md").read_text()
        self.assertIn("0 survives, 1 weakened, 1 killed; 1 not checked yet", text)
        row = next(l for l in text.splitlines() if "`A 1`" in l)
        self.assertIn("killed", row)
        self.assertIn("not checked yet", next(l for l in text.splitlines() if "`B2`" in l))
        self.assertIn("0 survives, 1 weakened, 1 killed", out)


if __name__ == "__main__":
    unittest.main()
