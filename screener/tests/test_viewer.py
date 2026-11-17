"""Site Plan Viewer: owner guard, SVG sanitiser, page escaping, joins and
paths (reviews/37 R1, R3, R10, R11, R27, R28).

Offline: synthetic run folders in a temp dir with made-up owners; every
HTTP call fails the test. Nothing reads screener/output or screener-data.

    python -m unittest discover -s screener/tests -t .
"""

from __future__ import annotations

import ast
import contextlib
import csv
import html
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import requests

from screener import arcgis, viewer

OWNER = "TEST OWNER ALPHA LLC"
MAILING = "123 SECRET MAIL WAY, NOWHERE GA 30000"
COLUMNS = ["rank", "parcel_id", "jurisdiction", "site_address", "acres", "entitlement", "zoning",
           "envelope_fit", "envelope_building_sf", "pond_fit", "largest_open_acres", "flood_pct",
           "wetland_pct", "steep_pct", "frontage_ft", "frontage_road", "exit_label", "exit_miles",
           "lead_grade", "owner", "owner_mailing", "maps_link", "decision"]
PARCELS = [("1", "99F000100010001", "100 TEST RD", OWNER, MAILING),
           ("2", "99F0001  LL0001", "200 SAMPLE DR", "OTHER HOLDER BETA INC", "PO BOX 999, ELSEWHERE TX 75000"),
           ("3", "07 999900000001", "0 EXAMPLE PKWY", "THIRD PARTY GAMMA LP", "9 GAMMA CT, SOMEWHERE GA 30001")]


def svg_doc(title: str = "Site plan", extra: str = "") -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="700" viewBox="0 0 640 700" '
            f'font-family="sans-serif"><title>{title}</title>'
            '<defs><pattern id="tx" width="8" height="8" patternUnits="userSpaceOnUse" '
            'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="#e8b04b" '
            'stroke-width="3"/></pattern></defs>'
            '<rect x="0" y="0" width="640" height="700" fill="#ffffff"/>'
            '<path d="M10,10 L100,10 L100,100 Z" fill="url(#tx)" fill-rule="evenodd"/>'
            '<path class="scale" d="M20.0,650.0 L20.0,656.0 L120.0,656.0 L120.0,650.0" fill="none" stroke="#222"/>'
            '<text x="20" y="670" font-size="12">500 ft</text>'
            '<text x="20" y="690" font-size="12">Parcel (county map) 10.4 ac</text>'
            f'{extra}</svg>')


def make_run(root: Path, parcels=PARCELS) -> Path:
    run = root / "run-2026-01-01"
    (run / "briefs").mkdir(parents=True)
    with open(run / "ranked.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, COLUMNS)
        w.writeheader()
        for rank, pid, addr, owner, mail in parcels:
            w.writerow({"rank": rank, "parcel_id": pid, "jurisdiction": "Union City",
                        "site_address": addr, "acres": "10.4", "entitlement": "by_right",
                        "zoning": "M-1", "envelope_fit": "cross-dock 600 x 520 ft",
                        "envelope_building_sf": "312000", "pond_fit": "pond fits",
                        "largest_open_acres": "9.9", "flood_pct": "0.0", "wetland_pct": "1.5",
                        "steep_pct": "3.0", "frontage_ft": "420", "frontage_road": "SR 138",
                        "exit_label": "I-85 exit 64", "exit_miles": "1.2", "lead_grade": "B",
                        "owner": owner, "owner_mailing": mail,
                        "maps_link": "https://www.google.com/maps/search/?api=1&query=33.6,-84.5",
                        "decision": ""})
    for rank, pid, *_ in parcels[:2]:
        name = f"{int(rank):03d}_{pid.replace(' ', '_')}.svg"
        (run / "briefs" / name).write_text(svg_doc(f"Site plan #{rank}"), encoding="utf-8")
    return run


def plan_record(key: str, pids, svg_name: str, **kw) -> dict:
    rec = {"id": key.replace("+", "-"), "key": key, "rank": key, "parcel_ids": pids,
           "title": f"#{key} 100 TEST RD, Union City", "fits": True, "hypothetical": False,
           "feasible": True, "configuration": "rear-load 260 deep, court + trailer row",
           "zoning": {"name": "union city", "status": "draft", "values": {}},
           "building": {"L_ft": 600, "D_ft": 260, "sf": 156000, "clear_height_ft": 36},
           "total_sf": 156000, "total_sf_if_access": 156000, "doors": 30,
           "trailers": {"court_rows": 30, "drop_yards": 0, "total": 30, "target": 30},
           "cars": {"provided": 90, "required": 86, "ada": 4, "stall": "9 x 18 ft", "basis": "draft"},
           "coverage": {"gross_ac": 10.4, "sf_per_gross_ac": 15000, "gross_far": 0.34,
                        "impervious_ac": 6.1, "impervious_pct": 59},
           "detention": {"storage_acft": 2.1, "ponds": 1, "fits": True, "basis": "screening"},
           "access": {"truck_driveway": True, "car_driveway": True, "frontage_ft": 420,
                      "road": "SR 138", "truck_route_ft": 200, "car_route_ft": 300},
           "grading": {"relief_ft": 12, "cut_fill_cy": 20000, "steep_share_pct": 1, "basis": "plane"},
           "flags": {"1": [], "2": [{"flag": "ZONING_DRAFT", "note": "draft values"}], "3": []},
           "alternatives": [{"variant": "rear-load 260", "sf": 156000, "feasible": True,
                             "route_problem": False, "L": 600}],
           "svg": svg_name}
    rec.update(kw)
    return rec


def write_plans(run: Path, plans_dir: Path, records: list[dict], fingerprint: str | None = "auto",
                svgs: dict[str, str] | None = None) -> Path:
    plans_dir.mkdir(parents=True, exist_ok=True)
    body: object
    if fingerprint is None:
        body = records
    else:
        fp = viewer.ranked_sha256(run) if fingerprint == "auto" else fingerprint
        body = {"schema_version": 1, "run": {"name": run.name, "ranked_csv_sha256": fp},
                "plans": records}
    (plans_dir / "plans.json").write_text(json.dumps(body), encoding="utf-8")
    for name, text in (svgs if svgs is not None else {r["svg"]: svg_doc(r["title"]) for r in records}).items():
        (plans_dir / name).write_text(text, encoding="utf-8")
    return plans_dir


def page_data(page: str) -> list[dict]:
    m = re.search(r"const PLANS = (.*?);\n", page, re.S)
    return json.loads(m.group(1))


class Offline(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.patch(arcgis, "CACHE_DIR", self.tmp / "http")
        boom = mock.Mock(side_effect=AssertionError("network call in an offline test"))
        self.patch(requests.Session, "request", boom)
        self.patch(arcgis, "_session", boom)
        self.run_dir = make_run(self.tmp)
        self.out = self.tmp / "out" / "viewer.html"

    def patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)

    def build(self, **kw) -> tuple[int, int]:
        return viewer.build(viewer.Inputs(run_dir=self.run_dir, **kw), self.out)

    def page(self) -> str:
        return self.out.read_text(encoding="utf-8")

    def assert_refused(self, exc=viewer.Refused, **kw):
        with self.assertRaises(exc) as cm:
            self.build(**kw)
        self.assertFalse(self.out.exists(), "a refused page must not be written")
        return cm.exception


class SanitiserTest(unittest.TestCase):
    BAD = {
        "script": '<script>alert(1)</script>',
        "foreignObject": '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>',
        "img onerror": '<img src="x" onerror="alert(1)"/>',
        "event attribute": '<rect width="1" height="1" onload="alert(1)"/>',
        "event attribute, odd case": '<rect width="1" height="1" OnClick="alert(1)"/>',
        "href": '<a href="javascript:alert(1)"><text>x</text></a>',
        "href attr on text": '<text href="http://x">x</text>',
        "xlink href": '<use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#tx"/>',
        "use": '<use href="#tx"/>',
        "style element": '<style>@import url(http://x/y.css);</style>',
        "style attribute": '<rect width="1" height="1" style="fill:url(http://x)"/>',
        "animate href": '<animate attributeName="href" to="javascript:alert(1)"/>',
        "set": '<set attributeName="onmouseover" to="alert(1)"/>',
        "javascript in a value": '<rect width="1" height="1" fill="javascript:alert(1)"/>',
        "external url": '<rect width="1" height="1" fill="url(http://evil/x#a)"/>',
        "data url": '<rect width="1" height="1" fill="url(data:image/svg+xml;base64,AAAA)"/>',
        "html namespace": '<x:div xmlns:x="http://www.w3.org/1999/xhtml">x</x:div>',
        "id with quote": '<rect id="a&quot; onload=&quot;x" width="1" height="1"/>',
        "transform with url": '<g transform="url(#tx)"/>',
        "text in rect": '<rect width="1" height="1">hello</rect>',
    }

    def test_rejects_each_bypass(self):
        for name, extra in self.BAD.items():
            with self.subTest(name), self.assertRaises(viewer.UnsafeSVG):
                viewer.sanitise_svg(svg_doc(extra=extra))

    def test_rejects_doctype_and_entities(self):
        for doc in ('<!DOCTYPE svg [<!ENTITY x "y">]>' + svg_doc(),
                    '<!doctype svg>' + svg_doc(),
                    '<?xml version="1.0"?><!ENTITY x SYSTEM "file:///etc/passwd">' + svg_doc()):
            with self.subTest(doc[:30]), self.assertRaises(viewer.UnsafeSVG):
                viewer.sanitise_svg(doc)

    def test_rejects_non_svg_root_and_garbage(self):
        for doc in ("<html><body/></html>", "not xml", "", None):
            with self.subTest(doc), self.assertRaises(viewer.UnsafeSVG):
                viewer.sanitise_svg(doc)

    def test_keeps_a_plan_and_re_escapes_text(self):
        doc = svg_doc(title="#1 A &amp; B &lt;script&gt;alert(1)&lt;/script&gt;",
                      extra='<text x="1" y="2"><![CDATA[<img src=x onerror=alert(1)>]]></text>'
                            '<!-- <script>alert(2)</script> --><?pi x?>')
        out = viewer.sanitise_svg(doc)
        root = ET.fromstring(out)        # the re-written SVG parses
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)
        self.assertNotIn("<!--", out)
        self.assertNotIn("<?", out)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", out)
        self.assertIn('fill="url(#tx)"', out)
        # the scale bar and legend survive for svg_meta
        self.assertEqual(viewer.svg_meta(out), (10.4, 5.0))

    def test_xml_invalid_characters_refused(self):
        with self.assertRaises(viewer.UnsafeSVG):
            viewer.sanitise_svg(svg_doc(title="bad \x01 char"))


class GuardTest(unittest.TestCase):
    rows = [{"owner": OWNER, "owner_mailing": MAILING, "site_address": "100 TEST RD",
             "jurisdiction": "Union City"},
            {"owner": "UNION CITY", "owner_mailing": "100 TEST RD, UNION CITY GA 30291",
             "site_address": "100 TEST RD REAR", "jurisdiction": "Union City"},
            {"owner": "CEMETERY", "owner_mailing": "", "site_address": "1 X", "jurisdiction": "Union City"}]

    def setUp(self):
        self.guard = viewer.OwnerGuard.from_rows(self.rows)

    def test_finds_owner_in_any_case_spacing_and_escaping(self):
        for text in ("test owner alpha llc", "Test  Owner-Alpha, L.L.C.", "TEST OWNER ALPHA LLC",
                     "x Test Owner Alpha y",                       # core name, suffix gone
                     "<text>TEST OWNER ALPHA &amp; co</text>", json.dumps({"n": "Test\nOwner Alpha"}),
                     "\\u0054EST OWNER ALPHA", "ｔｅｓｔ ｏｗｎｅｒ ａｌｐｈａ"):  # NFKC full-width
            with self.subTest(text):
                self.assertEqual(self.guard.hits(text).get("owner"), 1, text)

    def test_finds_mailing_and_its_street(self):
        self.assertEqual(self.guard.hits("mail: 123 Secret Mail Way, Nowhere GA 30000"),
                         {"owner_mailing": 1})
        self.assertEqual(self.guard.hits("123 secret mail way; again 123 Secret Mail Way"),
                         {"owner_mailing": 2})
        self.assertEqual(self.guard.hits("123 SECRET MAIL WAY"), {"owner_mailing": 1})

    def test_word_boundaries_and_public_names(self):
        self.assertFalse(self.guard.hits("contest owner alphabet"))   # not on word boundaries
        self.assertFalse(self.guard.hits("Union City, a cemetery next door"))  # place, generic word
        self.assertFalse(self.guard.hits("100 TEST RD UNION CITY"))   # a site address, not mail
        self.assertFalse(self.guard.hits("a plan of 100 TEST RD"))

    def test_public_addresses_of_the_same_artifact(self):
        guard = viewer.OwnerGuard.from_rows([{"owner": "X", "owner_mailing": "77 HOME ST, A GA 1",
                                              "site_address": "0 LAND RD", "jurisdiction": "Q"}])
        self.assertEqual(guard.hits("record at 77 HOME ST"), {"owner_mailing": 1})
        self.assertFalse(guard.hits("record at 77 HOME ST", public=["77 HOME ST"]))

    def test_message_holds_no_owner_text(self):
        with self.assertRaises(viewer.OwnerDataFound) as cm:
            self.guard.check("TEST OWNER ALPHA LLC", "file.json")
        self.assertNotIn("ALPHA", str(cm.exception).upper())
        self.assertIn("owner x1", str(cm.exception))


class PageTest(Offline):
    def test_clean_run_builds_with_no_owner_and_no_note_by_default(self):
        (self.tmp / "decisions.csv").write_text(
            "parcel_id,decision,date,note\n99F000100010001,maybe,2026-01-01,probably taken by a builder\n",
            encoding="utf-8")
        size, n = self.build(decisions=self.tmp / "decisions.csv")
        self.assertEqual(n, 2)
        page = self.page()
        self.assertEqual(size, len(page.encode("utf-8")))
        data = page_data(page)
        self.assertEqual({p["status"] for p in data}, {"taken", "unchecked"})
        self.assertTrue(all("note" not in p and "headline" not in p for p in data))
        self.assertNotIn("builder", page)
        self.assertIn("DRAFT", page)
        self.assertIn('http-equiv="Content-Security-Policy"', page)
        self.assertIn("script-src 'sha256-", html.unescape(page))

    def test_missing_optional_inputs(self):
        size, n = self.build()
        self.assertEqual(n, 2)
        (self.run_dir / "briefs" / "001_99F000100010001.svg").unlink()
        (self.run_dir / "briefs" / "002_99F0001__LL0001.svg").unlink()
        size, n = self.build()
        self.assertEqual(n, 0)

    def test_note_with_owner_refused_only_when_included(self):
        (self.tmp / "d.csv").write_text(
            "parcel_id,decision,date,note\n99F000100010001,maybe,2026-01-01,owner is Test Owner  alpha llc\n",
            encoding="utf-8")
        self.build(decisions=self.tmp / "d.csv")          # the note is not used: clean
        self.out.unlink()
        err = self.assert_refused(viewer.OwnerDataFound, decisions=self.tmp / "d.csv", include_notes=True)
        self.assertNotIn("alpha", str(err).lower())

    def test_headline_with_owner_refused(self):
        leads = self.tmp / "leads"
        d = leads / "99F000100010001"
        d.mkdir(parents=True)
        (d / "findings.checked.json").write_text(json.dumps(
            {"parcel_id": "99F000100010001", "skeptic": {"verdict": "weakened"}}), encoding="utf-8")
        (d / "lead.md").write_text("**In one line (not checked):** bought by TEST OWNER ALPHA in 2001\n",
                                   encoding="utf-8")
        self.build(leads=[leads])
        data = page_data(self.page())
        self.assertEqual(data[0]["skeptic"], "weakened")
        self.assertNotIn("headline", data[0])
        self.out.unlink()
        self.assert_refused(viewer.OwnerDataFound, leads=[leads], include_headlines=True)

    def test_lead_folder_must_hold_the_parcel_and_verdict_enum(self):
        leads = self.tmp / "leads"
        d = leads / "99F000100010001"
        d.mkdir(parents=True)
        (d / "lead.md").write_text("x", encoding="utf-8")
        (d / "findings.checked.json").write_text(json.dumps(
            {"parcel_id": "OTHER", "skeptic": {"verdict": "weakened"}}), encoding="utf-8")
        self.assertEqual(viewer.lead_check("99F000100010001", [leads], True), (None, None))
        (d / "findings.checked.json").write_text(json.dumps(
            {"parcel_id": "99F000100010001", "skeptic": {"verdict": "<b>sure</b>"}}), encoding="utf-8")
        self.assertEqual(viewer.lead_check("99F000100010001", [leads], False), (None, None))
        for pid in ("../../tmp", "a/b", "..", "x\x00y"):
            with self.subTest(pid):
                self.assertEqual(viewer.lead_check(pid, [leads], True), (None, None))

    def test_svg_with_owner_text_refused(self):
        brief = self.run_dir / "briefs" / "001_99F000100010001.svg"
        brief.write_text(svg_doc(extra='<text x="1" y="1">test owner ALPHA llc</text>'), encoding="utf-8")
        self.assert_refused(viewer.OwnerDataFound)

    def test_unsafe_brief_refused(self):
        brief = self.run_dir / "briefs" / "001_99F000100010001.svg"
        brief.write_text(svg_doc(extra='<image href="x" onerror="alert(1)"/>'), encoding="utf-8")
        self.assert_refused(viewer.UnsafeSVG)

    def test_script_breakout_and_placeholder_in_data(self):
        rows = [("1", "99F000100010001", "</script><!--<script>alert(1)//", OWNER, MAILING),
                ("2", "99F0001  LL0001", "__DATA__ __RUN__ __CSP__  ", "B HOLDER", "")]
        run = make_run(self.tmp / "evil", rows)
        evil = run.with_name("run__DATA__")
        run.rename(evil)
        out = self.tmp / "evil.html"
        viewer.build(viewer.Inputs(run_dir=evil), out)
        page = out.read_text(encoding="utf-8")
        script = re.findall(r"<script>(.*?)</script>", page, re.S)
        self.assertEqual(len(script), 1)
        m = re.search(r"const PLANS = (.*?);\n", script[0], re.S)
        self.assertNotIn("<", m.group(1))
        self.assertNotIn(">", m.group(1))
        self.assertNotIn(" ", m.group(1))
        data = json.loads(m.group(1))
        self.assertEqual(data[0]["title"], "#1 </script><!--<script>alert(1)//")
        self.assertIn("__DATA__ __RUN__", data[1]["title"])
        self.assertEqual(page.count("const PLANS = "), 1)
        self.assertIn("Run run__DATA__ ·", page)          # the run name, escaped, once
        self.assertNotIn('content="__CSP__"', page)
        self.assertEqual(page.count("__CSP__"), 1)         # the one in the data

    def test_template_must_hold_each_placeholder_once(self):
        tpl = viewer.TEMPLATE.read_text(encoding="utf-8")
        with self.assertRaises(viewer.Refused):
            viewer.render_page([], "r", tpl.replace("__RUN__", "__RUN__ __RUN__"))
        with self.assertRaises(viewer.Refused):
            viewer.render_page([], "r", tpl.replace("</script>", "</script><script></script>"))

    def test_page_size_cap(self):
        with self.assertRaises(viewer.Refused):
            viewer.build(viewer.Inputs(run_dir=self.run_dir), self.out, max_bytes=1000)
        self.assertFalse(self.out.exists())

    def test_atomic_write_keeps_the_old_page(self):
        self.build()
        old = self.out.read_bytes()
        (self.run_dir / "briefs" / "002_99F0001__LL0001.svg").unlink()
        with mock.patch.object(viewer.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.build()
        self.assertEqual(self.out.read_bytes(), old)
        self.assertEqual([p.name for p in self.out.parent.iterdir()], ["viewer.html"])

    def test_out_path_checks(self):
        with self.assertRaises(viewer.Refused):
            viewer.check_out_path(self.run_dir, self.run_dir)
        # inside the repo and tracked: refused; under screener/output: ignored, fine
        with self.assertRaises(viewer.Refused):
            viewer.check_out_path(viewer.REPO_ROOT / "screener" / "viewer-test.html")
        viewer.check_out_path(viewer.REPO_ROOT / "screener" / "output" / "viewer-test.html")

    def test_brief_name_must_match_the_run(self):
        (self.run_dir / "briefs" / "003_SOMEONE_ELSE.svg").write_text(svg_doc(), encoding="utf-8")
        self.assert_refused()


class PlansTest(Offline):
    def setUp(self):
        super().setUp()
        self.plans = self.tmp / "plans"

    def test_planner_plans_join_by_parcel_id(self):
        write_plans(self.run_dir, self.plans, [
            plan_record("1", ["99F000100010001"], "plan-1.svg"),
            plan_record("north", ["99F0001  LL0001", "07 999900000001"], "plan-north.svg",
                        feasible=None, title="north assemblage")])
        size, n = self.build(plans_dir=self.plans)
        data = {p["id"]: p for p in page_data(self.page())}
        self.assertEqual(n, 4)
        self.assertEqual(data["plan-1"]["related"], "run-1")
        self.assertIs(data["plan-1"]["feasible"], True)
        north = data["plan-north"]
        self.assertIsNone(north["feasible"])
        self.assertEqual(north["rank"], 2)
        self.assertEqual(north["pids"], ["99F0001  LL0001", "07 999900000001"])
        self.assertIsNone(north["taxAc"])
        self.assertEqual(north["zoningStatus"], "draft")

    def test_screener_siteplan_schema_round_trip(self):
        """The header and record shape screener.siteplan writes: a slugged
        key ("1-3") with the rank list in "rank", flags keyed by severity,
        a not-checked input (feasible None), a no-fit record without an SVG."""
        recs = [plan_record("1-3", ["99F000100010001", "07 999900000001"], "plan-1-3.svg", rank="1+3",
                            feasible=None, inputs={"frontage": "not_checked"},
                            flags={"1": [{"flag": "FRONTAGE_NOT_CHECKED", "note": "fetch failed"}],
                                   "2": [{"flag": "ZONING_DRAFT", "note": "draft"}], "3": []}),
                plan_record("2", ["99F0001  LL0001"], "plan-2.svg", rank="2",
                            gated=["USE_PERMIT_REQUIRED", "<b>x</b>"])]
        nofit = plan_record("3", ["07 999900000001"], "", rank="3", fits=False, feasible=False)
        del nofit["svg"]
        write_plans(self.run_dir, self.plans, recs)
        body = json.loads((self.plans / "plans.json").read_text(encoding="utf-8"))
        body.update({"tool": "screener.siteplan", "code_sha256": "0" * 16, "generated": "2026-01-01",
                     "options": {"parking": "max"}, "meta": {"zoning_status": "draft"}})
        body["plans"].append(nofit)
        (self.plans / "plans.json").write_text(json.dumps(body), encoding="utf-8")
        size, n = self.build(plans_dir=self.plans)
        data = {p["id"]: p for p in page_data(self.page())}
        self.assertEqual(n, 4)                     # two briefs, two drawn plans; the no-fit is skipped
        a = data["plan-1-3"]
        self.assertEqual((a["rank"], a["key"], a["related"]), (1, "1-3", "run-1"))
        self.assertIsNone(a["feasible"])
        self.assertEqual(a["flags1"][0]["flag"], "FRONTAGE_NOT_CHECKED")
        self.assertEqual(data["plan-2"]["related"], "run-2")
        self.assertEqual(data["plan-2"]["gated"], ["USE_PERMIT_REQUIRED"])   # names only
        # a rank list whose parcel ids do not hold those ranks' parcels: refused
        body["plans"][0]["parcel_ids"] = ["99F000100010001", "99F0001  LL0001"]
        (self.plans / "plans.json").write_text(json.dumps(body), encoding="utf-8")
        self.out.unlink()
        self.assert_refused(plans_dir=self.plans)

    def test_numbers_are_coerced_and_extra_keys_dropped(self):
        rec = plan_record("1", ["99F000100010001"], "plan-1.svg", doors="<b>30</b>",
                          owner=OWNER, inputs={"x": OWNER})
        rec["cars"]["provided"] = "<img src=x>"
        rec["building"]["note"] = OWNER
        write_plans(self.run_dir, self.plans, [rec])
        self.build(plans_dir=self.plans)
        p = [x for x in page_data(self.page()) if x["kind"] == "plan"][0]
        self.assertIsNone(p["v4"]["doors"])
        self.assertIsNone(p["v4"]["cars"]["provided"])
        self.assertNotIn("note", p["v4"]["building"])

    def test_owner_in_plan_title_refused(self):
        write_plans(self.run_dir, self.plans, [plan_record(
            "1", ["99F000100010001"], "plan-1.svg", title="#1 (Test Owner Alpha)")])
        self.assert_refused(viewer.OwnerDataFound, plans_dir=self.plans)

    def test_foreign_run_refused(self):
        write_plans(self.run_dir, self.plans, [plan_record("1", ["99F000100010001"], "plan-1.svg")],
                    fingerprint="0" * 64)
        self.assert_refused(plans_dir=self.plans)

    def test_rank_and_parcel_mismatch_refused(self):
        write_plans(self.run_dir, self.plans, [plan_record("1", ["99F0001  LL0001"], "plan-1.svg")])
        self.assert_refused(plans_dir=self.plans)

    def test_legacy_plans_need_join_by_rank(self):
        rec = plan_record("1", None, "plan4-1.svg")
        del rec["parcel_ids"]
        write_plans(self.run_dir, self.plans, [rec], fingerprint=None)
        self.assert_refused(plans_dir=self.plans)
        self.build(plans_dir=self.plans, join_by_rank=True)
        self.assertEqual(len(page_data(self.page())), 3)

    def test_paths_refused(self):
        outside = self.tmp / "secret.svg"
        outside.write_text(svg_doc("secret"), encoding="utf-8")
        for name in ("../secret.svg", "../../etc/passwd", str(outside), "plan-1.svg\x00.txt",
                     "plan-../../x.svg", "sub/plan-1.svg"):
            with self.subTest(name):
                write_plans(self.run_dir, self.plans, [plan_record("1", ["99F000100010001"], name)],
                            svgs={})
                self.assert_refused(plans_dir=self.plans)

    def test_keys_refused(self):
        for key in ("../x", "a/b", "x\x00", "", "a" * 65, "x y"):
            with self.subTest(key):
                write_plans(self.run_dir, self.plans,
                            [plan_record(key, ["99F000100010001"], "plan-1.svg")])
                self.assert_refused(plans_dir=self.plans)

    def test_unsafe_plan_svg_refused(self):
        write_plans(self.run_dir, self.plans, [plan_record("1", ["99F000100010001"], "plan-1.svg")],
                    svgs={"plan-1.svg": svg_doc(extra="<script>alert(1)</script>")})
        self.assert_refused(viewer.UnsafeSVG, plans_dir=self.plans)


class LandTest(Offline):
    def land_file(self, parcels, fingerprint="auto") -> Path:
        body = {"schema_version": 1, "parcels": parcels}
        if fingerprint:
            body["run"] = {"ranked_csv_sha256": viewer.ranked_sha256(self.run_dir)
                           if fingerprint == "auto" else fingerprint}
        path = self.tmp / "land_today.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_land_joined_by_parcel(self):
        path = self.land_file([{"rank": 1, "parcel_id": "99F000100010001", "land_today": "raw_land",
                                "what_is_there": "woods " * 300}])
        self.build(land=path)
        data = {p["id"]: p for p in page_data(self.page())}
        self.assertEqual(data["run-1"]["land"], "raw_land")
        self.assertLessEqual(len(data["run-1"]["landWhat"]), viewer.TEXT_CAP)
        self.assertIsNone(data["run-2"]["land"])

    def test_land_refusals(self):
        cases = {
            "foreign run": ([{"rank": 1, "parcel_id": "99F000100010001", "land_today": "raw_land"}], "f" * 64),
            "rank mismatch": ([{"rank": 2, "parcel_id": "99F000100010001", "land_today": "raw_land"}], "auto"),
            "unknown parcel": ([{"rank": 1, "parcel_id": "NOPE", "land_today": "raw_land"}], "auto"),
            "unknown class": ([{"rank": 1, "parcel_id": "99F000100010001", "land_today": "<b>"}], "auto"),
        }
        for name, (parcels, fp) in cases.items():
            with self.subTest(name):
                self.assert_refused(land=self.land_file(parcels, fp))

    def test_owner_in_land_text_refused(self):
        path = self.land_file([{"rank": 2, "parcel_id": "99F0001  LL0001", "land_today": "unclear",
                                "what_is_there": "sign says Test Owner Alpha"}])
        self.assert_refused(viewer.OwnerDataFound, land=path)


class ReviewFixesTest(Offline):
    """reviews/37 Agent 2 fixes: the floor-area wording, layout checks, a
    missing SVG or input file, no-fit plans, and the guard's name forms."""

    def setUp(self):
        super().setUp()
        self.plans = self.tmp / "plans"

    def test_sf_text_for_gated_incomplete_hypothetical_and_blocked(self):
        t = viewer.plan_sf_text
        self.assertEqual(t(True, [], False, 156000, 156000), "156,000 SF")
        gated = t(True, ["USE_PERMIT_REQUIRED"], False, 0, 254000)
        self.assertIn("254,000 SF", gated)
        self.assertIn("gated: USE_PERMIT_REQUIRED", gated)
        self.assertNotIn("no building fit", gated)
        self.assertIn("unchecked inputs", t(None, [], False, 0, 61000))
        self.assertIn("road access", t(False, [], True, 0, 61000))
        blocked = t(False, [], False, 0, 146000)
        self.assertIn("severity-1 flags", blocked)
        self.assertNotIn("access", blocked)
        self.assertEqual(t(False, [], False, 0, 0), "no building fit")

    def test_gated_plan_page_shows_its_floor_area(self):
        write_plans(self.run_dir, self.plans, [plan_record(
            "1", ["99F000100010001"], "plan-1.svg", total_sf=0, total_sf_if_access=254000,
            gated=["USE_PERMIT_REQUIRED"],
            alternatives=[{"variant": "rear-load 260", "sf": 254000, "geometry_ok": True, "route_problem": False}])])
        self.build(plans_dir=self.plans)
        p = {x["id"]: x for x in page_data(self.page())}["plan-1"]
        self.assertIn("254,000 SF (gated: USE_PERMIT_REQUIRED", p["sfText"])
        self.assertEqual(p["alternatives"][0]["geometryOk"], True)
        self.assertNotIn("feasible", p["alternatives"][0])
        tpl = viewer.TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn('"no building fit"', tpl)       # the page uses sfText, worded in viewer.py
        self.assertIn("Layout checks (not site feasibility)", tpl)

    def test_prototype_alternatives_still_read(self):
        write_plans(self.run_dir, self.plans, [plan_record("1", ["99F000100010001"], "plan-1.svg")])
        self.build(plans_dir=self.plans)
        p = {x["id"]: x for x in page_data(self.page())}["plan-1"]
        self.assertIs(p["alternatives"][0]["geometryOk"], True)    # the old "feasible" key

    def test_missing_plan_svg_refused_with_its_name(self):
        write_plans(self.run_dir, self.plans, [plan_record("1", ["99F000100010001"], "plan-1.svg")], svgs={})
        exc = self.assert_refused(plans_dir=self.plans)
        self.assertIn("plan-1.svg", str(exc))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = viewer.main([str(self.run_dir), "--plans", str(self.plans), "--out", str(self.out)])
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())

    def test_missing_or_bad_input_files_exit_2(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        empty = self.tmp / "norun"
        empty.mkdir()
        for argv in ([str(self.run_dir), "--land", str(self.tmp / "nope.json")],
                     [str(self.run_dir), "--land", str(bad)],
                     [str(self.run_dir), "--decisions", str(self.tmp / "nope.csv")],
                     [str(empty)]):
            with self.subTest(argv[1:]):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    code = viewer.main(argv + ["--out", str(self.out)])
                self.assertEqual(code, 2, err.getvalue())
                self.assertIn("cannot read an input", err.getvalue())
                self.assertFalse(self.out.exists())

    def test_no_fit_plans_are_named_on_stderr(self):
        write_plans(self.run_dir, self.plans, [
            plan_record("1", ["99F000100010001"], "plan-1.svg"),
            {"key": "3", "rank": "3", "parcel_ids": ["07 999900000001"], "fits": False, "svg": None}],
            svgs={"plan-1.svg": svg_doc("plan 1")})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.build(plans_dir=self.plans)
        self.assertIn("1 planned site(s) without a drawing", err.getvalue())
        self.assertIn(": 3", err.getvalue())

    def test_guard_name_forms_po_box_and_split_markup(self):
        rows = [{"owner": "SMITH JOHN A", "owner_mailing": "PO BOX 4471, ATLANTA GA 30301",
                 "site_address": "1 X RD", "jurisdiction": "Union City"},
                {"owner": OWNER, "owner_mailing": "", "site_address": "2 X RD", "jurisdiction": "Union City"},
                {"owner": "FULTON COUNTY BOARD OF EDUCATION", "owner_mailing": "", "site_address": "3 X RD",
                 "jurisdiction": "Union City"}]
        g = viewer.OwnerGuard.from_rows(rows)
        for text in ("John Smith", "SMITH JOHN", "Smith, John A.", "a note on john  smith's land"):
            with self.subTest(text):
                self.assertEqual(g.hits(text), {"owner": 1})
        for text in ("P.O. Box 4471", "p o box 4471", "PO BOX 4471"):
            with self.subTest(text):
                self.assertEqual(g.hits(text), {"owner_mailing": 1})
        split = "<text>TEST <tspan>OWNER</tspan> ALPHA</text>"
        self.assertFalse(g.hits(split))                       # markup tokens break the run...
        self.assertEqual(g.hits(viewer.markup_text(split)), {"owner": 1})   # ...its text does not
        self.assertEqual(viewer.person_name_forms("FULTON COUNTY BOARD OF EDUCATION"), [])
        self.assertEqual(viewer.person_name_forms(OWNER), [])
        self.assertFalse(g.hits("board of education"))

    def test_owner_split_across_svg_elements_refused(self):
        write_plans(self.run_dir, self.plans, [plan_record("1", ["99F000100010001"], "plan-1.svg")],
                    svgs={"plan-1.svg": svg_doc("plan", extra="<text>TEST <tspan>OWNER</tspan> ALPHA</text>")})
        self.assert_refused(viewer.OwnerDataFound, plans_dir=self.plans)


class RoundOneFixesTest(Offline):
    """reviews/37 Agent 2, round 1: the guard's per-row skip, name forms with
    initials L/C and ET AL, address spellings, an assemblage's worst member,
    budget-cut sites and a missing --leads folder."""

    def setUp(self):
        super().setUp()
        self.plans = self.tmp / "plans"

    def test_another_rows_site_address_does_not_unguard_an_owner(self):
        rows = [{"owner": "LEE DONALD R", "owner_mailing": "77 QUIET WAY, MARIETTA GA 30060",
                 "site_address": "0 X RD", "jurisdiction": "Fairburn"}]
        text = "note: Donald Lee, mail to 77 Quiet Way"
        alone = viewer.OwnerGuard.from_rows(rows).hits(text)
        self.assertEqual(alone, {"owner": 1, "owner_mailing": 1})
        # two unrelated (rejected, never published) rows whose site addresses
        # are that mailing street and a street named like the owner
        more = rows + [{"site_address": "77 QUIET WAY", "jurisdiction": "Fairburn"},
                       {"site_address": "100 DONALD LEE HOLLOWELL PKWY", "jurisdiction": "Atlanta"}]
        self.assertEqual(viewer.OwnerGuard.from_rows(more).hits(text), alone)
        # an artifact that itself writes that site address passes it as public
        self.assertEqual(viewer.OwnerGuard.from_rows(more).hits(text, public=["77 QUIET WAY"]), {"owner": 1})
        # the row's own site address and the run's place names are still skipped
        own = viewer.OwnerGuard.from_rows([{"owner": "FAIRBURN", "owner_mailing": "5 MAIN ST, X GA",
                                            "site_address": "5 MAIN ST REAR", "jurisdiction": "Union City"},
                                           {"site_address": "1 A RD", "jurisdiction": "Fairburn"}])
        self.assertFalse(own.hits("#1 5 MAIN ST REAR, Fairburn"))

    def test_natural_order_name_with_initials_and_et_al(self):
        for owner in ("WALKER DONALD A", "WALKER DONALD L", "WALKER DONALD C", "WALKER DONALD ET AL",
                      "WALKER DONALD L ET AL", "WALKER DONALD JR", "WALKER DONALD & BETTY"):
            with self.subTest(owner):
                g = viewer.OwnerGuard.from_rows([{"owner": owner, "owner_mailing": "", "site_address": "1 X RD",
                                                  "jurisdiction": "Union City"}])
                self.assertEqual(g.hits("Donald Walker has held it since 1990"), {"owner": 1})
        for company in ("WALKER DONALD L.L.C.", "WALKER HOLDINGS LLC", "SMITH & JONES INVESTMENTS LLC"):
            self.assertEqual(viewer.person_name_forms(company), [], company)

    def test_mailing_spellings_suite_and_ampersand(self):
        g = viewer.OwnerGuard.from_rows([{"owner": "SMITH & JONES INVESTMENTS LLC",
                                          "owner_mailing": "4410 OLD STAGE RD STE 300, ATLANTA GA 30303",
                                          "site_address": "0 X RD", "jurisdiction": "Fairburn"}])
        for text in ("owner mails from 4410 Old Stage Rd, Atlanta", "4410 Old Stage Road Suite 300",
                     "4410 OLD STAGE ROAD"):
            with self.subTest(text):
                self.assertEqual(g.hits(text), {"owner_mailing": 1})
        self.assertEqual(g.hits("Smith and Jones Investments bought it"), {"owner": 1})
        self.assertEqual(g.hits("Smith & Jones Investments bought it"), {"owner": 1})
        self.assertFalse(g.hits("a truck court on Old Stage, 4410 ft of frontage"))
        self.assertEqual(viewer.street_base("4410 OLD STAGE RD # 300"), ["4410", "old", "stage", "rd"])

    def test_assemblage_shows_its_worst_member(self):
        land = self.tmp / "land.json"
        land.write_text(json.dumps({"run": {"ranked_csv_sha256": viewer.ranked_sha256(self.run_dir)}, "parcels": [
            {"rank": 1, "parcel_id": "99F000100010001", "land_today": "raw_land", "what_is_there": "woods"},
            {"rank": 2, "parcel_id": "99F0001  LL0001", "land_today": "not_raw_land",
             "what_is_there": "operating truck terminal"}]}), encoding="utf-8")
        dec = self.tmp / "decisions.csv"
        dec.write_text("parcel_id,decision,note\n99F000100010001,pursue,\n99F0001  LL0001,pass,\n", encoding="utf-8")
        write_plans(self.run_dir, self.plans, [plan_record("n", ["99F000100010001", "99F0001  LL0001"], "plan-n.svg",
                                                           rank="n", title="n assemblage")])
        self.build(plans_dir=self.plans, land=land, decisions=dec)
        data = {p["id"]: p for p in page_data(self.page())}
        self.assertEqual((data["run-1"]["land"], data["run-1"]["status"]), ("raw_land", "open"))
        n = data["plan-n"]
        self.assertEqual((n["land"], n["status"]), ("not_raw_land", "drop"))
        self.assertIn("#1 raw land: woods", n["landWhat"])
        self.assertIn("#2 not raw land: operating truck terminal", n["landWhat"])
        self.assertEqual(viewer.LAND_WORST_FIRST[-1], "raw_land")

    def test_budget_cut_site_is_not_called_a_no_fit(self):
        write_plans(self.run_dir, self.plans, [
            plan_record("1", ["99F000100010001"], "plan-1.svg"),
            {"key": "3", "rank": "3", "parcel_ids": ["07 999900000001"], "fits": False, "svg": None,
             "flags": {"1": [{"flag": "PLAN_BUDGET_EXCEEDED", "note": "not a no-fit"}], "2": [], "3": []}},
            {"key": "2", "rank": "2", "parcel_ids": ["99F0001  LL0001"], "fits": False, "svg": None,
             "flags": {"1": [{"flag": "NO_FIT", "note": "x"}], "2": [], "3": []}}],
            svgs={"plan-1.svg": svg_doc("plan 1")})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.build(plans_dir=self.plans)
        self.assertIn("1 planned site(s) without a drawing (no building fits) are not shown: 2\n", err.getvalue())
        self.assertIn("1 site(s) not planned (the search budget ran out", err.getvalue())
        self.assertRegex(err.getvalue(), r"re-run the planner\) are not shown: 3")

    def test_missing_leads_folder_refused(self):
        for bad in (self.tmp / "no-such-leads", self.run_dir / "ranked.csv"):
            with self.subTest(str(bad)):
                self.assert_refused(leads=[bad])
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = viewer.main([str(self.run_dir), "--out", str(self.out), "--leads", str(self.tmp / "typo")])
        self.assertEqual(code, 2)
        self.assertIn("not a folder", err.getvalue())


class CliAndStaticTest(Offline):
    def test_cli_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(viewer.main([str(self.run_dir), "--out", str(self.out)]), 0)
        brief = self.run_dir / "briefs" / "001_99F000100010001.svg"
        brief.write_text(svg_doc(extra='<text>TEST OWNER ALPHA LLC</text>'), encoding="utf-8")
        self.out.unlink()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(viewer.main([str(self.run_dir), "--out", str(self.out)]), 2)
        self.assertFalse(self.out.exists())
        self.assertNotIn("ALPHA", err.getvalue().upper())

    def test_imports_and_globals(self):
        code = ("import screener.arcgis as a; v = a.cache_days; import screener.viewer, "
                "screener.builtcheck; assert a.cache_days == v, a.cache_days")
        done = subprocess.run([sys.executable, "-c", code], cwd=viewer.REPO_ROOT,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        tree = ast.parse(Path(viewer.__file__).read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree)
                  if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        self.assertFalse(names & {"requests", "PIL", "numpy", "screener"}, names)
        src = Path(viewer.__file__).read_text(encoding="utf-8")
        self.assertNotIn("building_prototypes.json", src)
        self.assertNotIn("sys.path", src)

    def test_template_is_inert_without_data(self):
        tpl = viewer.TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(tpl.count("__CSP__"), 1)
        self.assertEqual(tpl.count("__DATA__"), 1)
        self.assertEqual(tpl.count("__RUN__"), 1)
        self.assertNotRegex(tpl, r"<[^>]+\son\w+\s*=")      # no inline handlers (CSP)
        self.assertNotIn("http://", tpl)
        self.assertNotRegex(tpl, r"<script[^>]+src")

    def test_marks_stay_in_the_page_until_saved(self):
        """The mark buttons write no file and ask no server: marks live in
        the browser's own storage and leave only as a download whose
        columns are the ones ``decide --import`` reads."""
        tpl = viewer.TEMPLATE.read_text(encoding="utf-8")
        script = re.findall(r"<script>(.*?)</script>", tpl, re.S)[0]
        for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource",
                       "document.cookie", "<form", "import(", "eval(", "new Function"):
            self.assertNotIn(banned, tpl, banned)
        self.assertIn('["parcel_id","decision","date","note","run"]', script)
        from screener import run as screener_run
        dec = re.search(r"const DEC = \{(.*?)\};", script).group(1)
        self.assertEqual(tuple(sorted(re.findall(r"(\w+):", dec))), tuple(sorted(screener_run.DECISION_VALUES)))
        self.assertIn("PIDS.has(pid)", script)                 # only this page's parcels can be marked
        self.assertIn('id="savemarks"', tpl)
        # The run name the marks file carries is read from the header the
        # one __RUN__ fills (escaped by render_page), not from a second copy.
        self.assertIn('/^Run (.+?) · screener plans/', script)
        self.assertIn("Run __RUN__ · screener plans", tpl)


class RoundFourFixesTest(Offline):
    """reviews/37 round 4: the guard's length limits and name variants,
    the prototype-fit filter, the built check's incomplete state."""

    def test_core_length_is_measured_before_street_words_fold(self):
        g = viewer.OwnerGuard.from_rows([{"owner": o} for o in ("MILL ROAD LLC", "WEST END LLC", "SOUTH PARK LP")])
        for text in ("a note: Mill Road owns the land", "West End next door", "South Park owns it",
                     "mill rd", "W. End"):
            with self.subTest(text):
                self.assertEqual(g.hits(text), {"owner": 1})

    def test_name_variants(self):
        g = viewer.OwnerGuard.from_rows([{"owner": o} for o in (
            "WALKER DONALD & BETTY", "ACE LLC", "O'BRIEN PATRICK J", "O BRIEN SEAN", "DE LA CRUZ MARIA",
            "KING ROSA & JONES ALAN")])
        for text in ("Betty Walker", "Donald and Betty Walker", "Donald & Betty Walker", "Donald Walker",
                     "Ace, L.L.C.", "ACE L L C", "Ace LLC", "Patrick O'Brien", "Patrick O\u2019Brien",
                     "Patrick OBrien", "Sean O'Brien", "Maria de la Cruz", "Alan Jones", "Rosa King"):
            with self.subTest(text):
                self.assertGreaterEqual(g.hits(text).get("owner", 0), 1)
        for text in ("an ace in the hole", "the road west", "Smith's land", "a lot on the left"):
            with self.subTest(text):
                self.assertEqual(g.hits(text), {})
        self.assertEqual(viewer.normal_tokens("Smith's L.L.C."), ["smith", "s", "llc"])

    def test_prototype_fit_filter_reads_the_plan(self):
        alt = lambda op, ok: {"variant": "v", "operator": op, "sf": 1, "geometry_ok": ok, "route_problem": False}
        self.assertIs(viewer.plan_std_fits({"configuration": "x", "operator_prototype": True}), True)
        self.assertIs(viewer.plan_std_fits({"configuration": "x", "operator_prototype": False,
                                            "alternatives": [alt(False, True), alt(True, False)]}), False)
        self.assertIs(viewer.plan_std_fits({"configuration": "x", "operator_prototype": False,
                                            "alternatives": [alt(True, True)]}), True)
        self.assertIs(viewer.plan_std_fits({"configuration": None, "operator_prototype": None}), False)
        self.assertIsNone(viewer.plan_std_fits({"configuration": "x"}))
        plans = self.tmp / "plans"
        write_plans(self.run_dir, plans, [
            plan_record("1", ["99F000100010001"], "plan-1.svg", operator_prototype=False,
                        alternatives=[{"variant": "rear-load 310", "operator": False, "sf": 1, "geometry_ok": True}]),
            plan_record("2", ["99F0001  LL0001"], "plan-2.svg", operator_prototype=True)])
        self.build(plans_dir=plans)
        data = {p["id"]: p for p in page_data(self.page())}
        self.assertIs(data["plan-1"]["stdFits"], False)
        self.assertIs(data["plan-2"]["stdFits"], True)
        tpl = (Path(viewer.__file__).parent / "viewer_template.html").read_text(encoding="utf-8")
        self.assertIn('f==="none" && p.stdFits!==false', tpl)

    def test_land_from_an_incomplete_built_check_says_so(self):
        body = {"schema_version": 1, "run": {"ranked_csv_sha256": viewer.ranked_sha256(self.run_dir)},
                "parcels": [{"rank": 1, "parcel_id": "99F000100010001", "land_today": "unclear",
                             "what_is_there": "houses", "footprint_check": "not checked",
                             "why": "footprint check not completed: partly usable not confirmed"},
                            {"rank": 2, "parcel_id": "99F0001  LL0001", "land_today": "raw_land",
                             "what_is_there": "woods", "footprint_check": "no building found", "why": ""}]}
        path = self.tmp / "land_today.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        self.build(land=path)
        data = {p["id"]: p for p in page_data(self.page())}
        self.assertIn("built check incomplete", data["run-1"]["landWhat"])
        self.assertTrue(data["run-1"]["landWhat"].startswith("houses"))
        self.assertEqual(data["run-2"]["landWhat"], "woods")

if __name__ == "__main__":
    unittest.main()
