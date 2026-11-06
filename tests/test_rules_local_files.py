"""rules_scrape.py, terms block and local official files
(reviews/24_rules_local_files): offline tests, one or more per risk-register
item LF-01..LF-23. Every fixture is a short SYNTHETIC excerpt with invented
values, shaped like a Municode HTML export or a city PDF, never a copied
page (LF-09). No network, no DATABASE_URL (SR-6)."""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import requests

import tests  # noqa: F401  (suite guards: mirror kill-switch, committed checks)
import rules_scrape as rs
from test_rules_scrape import (COUNTY, MIRROR, PROSE_HTML, FakeResp, base_registry, fetcher,
                               registry_obj, robots, routes_for)
from zoning_helpers import REPO_ROOT

PATCH_PATH = REPO_ROOT / "reviews" / "24_rules_local_files" / "rules_sources_patch.json"
REVIEW_DIR = REPO_ROOT / "reviews" / "24_rules_local_files"
ZONE = "www.zoneomics.com"
SENTINEL = "SENTINEL-Q7X-CONTENT"

# ---------------------------------------------------------------------------
# Synthetic fixtures (invented values; shaped like official exports)
# ---------------------------------------------------------------------------
MUNI_HTML = textwrap.dedent("""\
    <!-- synthetic fixture: invented text and values, shaped like a code-publisher HTML export -->
    <html><head><title>Code of Ordinances (synthetic)</title></head><body>
    <div class="chunk"><div class="chunk-title">Sec. 80-84. - C-9—Invented Commercial District.</div>
    <div class="chunk-content"><p>C-9—Development standards.</p><div><table><tbody>
    <tr><td><p>Minimum front yard setback</p></td><td><p>91 feet</p></td></tr></tbody></table></div>
    <p>C-9—Design standards.</p></div></div>
    <div class="chunk"><div class="chunk-title">Sec. 80-85. - M-1—Light Industrial Zoning District.</div>
    <div class="chunk-content"><p>(f) M-1—Development standards.</p>
    <div class="table-wrap"><div><table class="table"><tbody>
    <tr><td><p>Minimum front yard setback</p></td><td><p>47 feet</p></td></tr>
    <tr><td><p>Minimum side yard setback</p></td><td><p>13 feet<sup>1</sup></p></td></tr>
    <tr><td><p>Minimum rear yard setback</p></td><td><p>29 feet</p></td></tr>
    </tbody></table></div></div>
    <p><sup>1</sup> Synthetic footnote text.</p>
    <p>(g) M-1—Design standards.</p></div></div>
    <div class="chunk"><div class="chunk-title">Sec. 80-86. - M-2—Heavy Industrial Zoning District.</div>
    <div class="chunk-content"><p>(f) M-2—Development standards.</p><div><table><tbody>
    <tr><td><p>Minimum front yard setback</p></td><td><p>61 feet</p></td></tr></tbody></table></div>
    <p>(g) M-2—Design standards.</p></div></div>
    </body></html>""")

# A heading-chain case in the same export shape: the M-2 block has no
# 'Development standards.' line, the next block has one (round-1 F1).
MUNI_CHAIN_HTML = textwrap.dedent("""\
    <!-- synthetic fixture -->
    <html><body>
    <div class="chunk-title">Sec. 80-85. - M-1—Light Industrial Zoning District.</div>
    <div><p>Development standards.</p><table><tr><td>Minimum rear yard setback</td><td>37 feet</td></tr></table>
    <p>Design standards.</p></div>
    <div class="chunk-title">Sec. 80-86. - M-2—Heavy Industrial Zoning District.</div>
    <div><p>(a) Purpose. Invented.</p><p>Design standards.</p></div>
    <div class="chunk-title">Sec. 80-87. - M-3—Invented District.</div>
    <div><p>Development standards.</p><table><tr><td>Minimum rear yard setback</td><td>155 feet</td></tr></table>
    <p>Design standards.</p></div>
    </body></html>""")

CITY_PDF_LINES = [
    "ARTICLE 9. INDUSTRIAL DISTRICTS (synthetic)",
    "9.2.3. DEVELOPMENT STANDARDS.",
    "  A.   Minimum Front Yard - 43 feet",
    "  B.   Minimum Rear Yard - 27 feet",
    "  C.   Minimum Lot Area - 2 acres",
    "9.2.4. OTHER STANDARDS.",
]

CITY_TXT = "\n".join(["synthetic fixture: invented values", *CITY_PDF_LINES]) + "\n"


def make_pdf(lines=(), hex_lines=(), info: bytes = b"") -> bytes:
    """A one-page PDF with a Courier (WinAnsi, so em dashes work) text layer
    (or none). hex_lines are
    written as hex strings, so they appear only in the text layer, not as
    plain bytes in the file."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("cp1252")
    ops = b"".join(b"(" + esc(ln) + b") Tj T* " for ln in lines)
    ops += b"".join(b"<" + ln.encode("cp1252").hex().encode() + b"> Tj T* " for ln in hex_lines)
    content = (b"BT /F1 10 Tf 12 TL 40 760 Td " + ops + b"ET") if (lines or hex_lines) else b"q Q"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
            b"<< " + info + b" >>"]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 6 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, xref)
    return bytes(out)


LABELS_M1 = [{"label": "Minimum front yard setback", "field": "front_setback_ft"},
             {"label": "Minimum side yard setback", "field": "side_setback_ft"},
             {"label": "Minimum rear yard setback", "field": "rear_setback_ft"}]


def local_src(key, path, fmt="html", parser="label_value", sections=None, **over):
    s = {"key": key, "kind": "local_file", "enabled": True, "path": path, "format": fmt,
         "source_class": "official", "parser": parser,
         "title": "Synthetic Code of Ordinances, Chapter 80",
         "obtained_from": "City Clerk (synthetic)", "obtained_on": "2026-11-01",
         "sha256": rs.LOCAL_TOFU,
         "sections": sections or [
             {"id": "m1", "label": "Sec. 80-85(f)",
              "start": ["Sec. 80-85. - M-1—Light Industrial Zoning District.",
                        "M-1—Development standards."],
              "end": "M-1—Design standards.", "district": "M-1",
              "config": {"labels": LABELS_M1}}]}
    s.update(over)
    return s


def pdf_src(key="fid_local", path="fid/art9.pdf", **over):
    return local_src(key, path, "pdf", sections=[
        {"id": "m1", "label": "Sec. 9.2.3", "start": "9.2.3. DEVELOPMENT STANDARDS.",
         "end": "9.2.4.", "district": "M-1",
         "config": {"labels": [{"label": "Minimum Front Yard", "field": "front_setback_ft"},
                               {"label": "Minimum Rear Yard", "field": "rear_setback_ft"}]}}],
        **over)


def local_registry(*sources, extra_url_sources=()):
    """A registry with only local sources (fairburn) plus optional url ones."""
    d = base_registry()
    d["jurisdictions"] = {"fairburn": {"name": "FB", "priority": 1,
                                       "conditions_suffix_regex": None, "change_signals": [],
                                       "sources": list(sources)}}
    if extra_url_sources:
        d["jurisdictions"]["fid"] = {"name": "FID", "priority": 1,
                                     "conditions_suffix_regex": None, "change_signals": [],
                                     "sources": list(extra_url_sources)}
    return d


def county_src(key="fc", path="/art10.html"):
    return {"key": key, "enabled": True, "url": f"https://{COUNTY}{path}", "format": "html",
            "source_class": "official", "parser": "label_value",
            "sections": [{"id": "m1", "label": "Sec. 9-1", "start": "Sec. 9-1. - M-1.",
                          "end": "Sec. 9-2.", "district": "M-1",
                          "config": {"labels": [{"label": "Minimum front yard",
                                                 "field": "front_setback_ft"}]}}]}


COUNTY_HTML = ("<!-- synthetic fixture --><html><body><p>Sec. 9-1. - M-1.</p>"
               "<p>Minimum front yard: 44 feet</p><p>Sec. 9-2. - Other.</p></body></html>")


def zone_src(key="zm", path="/code/fairburn-GA/chapter_2"):
    s = county_src(key)
    s.update({"url": f"https://{ZONE}{path}", "source_class": "mirror"})
    return s


def zone_hosts(d, reviewed=False):
    d["hosts"][ZONE] = {"terms_url": None, "terms_reviewed_by": "op" if reviewed else None,
                        "terms_reviewed_on": "2026-11-05" if reviewed else None, "note": "x"}
    return d


def apply_patch(data, patch, optional=False):
    """Apply reviews/24's registry patch to a COPY of the registry dict."""
    d = copy.deepcopy(data)
    off = set(patch["disable_sources"])
    for j in d["jurisdictions"].values():
        for s in j.get("sources") or []:
            if s["key"] in off:
                s["enabled"] = False
    for h, meta in patch["set_hosts"].items():
        d["hosts"][h] = copy.deepcopy(meta)
    for j, note in patch["set_jurisdiction_notes"].items():
        d["jurisdictions"][j]["note"] = note
    for j, srcs in patch["add_sources"].items():
        d["jurisdictions"][j].setdefault("sources", []).extend(copy.deepcopy(srcs))
    if optional:
        keys = set(patch["optional_disable_change_signals"]["keys"])
        for j in d["jurisdictions"].values():
            for g in j.get("change_signals") or []:
                if g["key"] in keys:
                    g["enabled"] = False
    return d


class InboxTest(unittest.TestCase):
    """A throwaway git checkout with a gitignored rules_inbox/."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self.tmp = Path(tempfile.mkdtemp(prefix="rules-local-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        (self.root / ".gitignore").write_text("rules_inbox/\n.env\n")
        (self.root / ".env").write_text(f"DATABASE_URL=decoy-{SENTINEL}\n")
        self.inbox_dir = self.root / "rules_inbox"
        self.inbox_dir.mkdir()
        self.inbox = rs.LocalInbox(self.inbox_dir, self.root)
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.cache = rs.Cache(self.tmp / "cache")

    def put(self, rel, data):
        p = self.inbox_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def reg(self, data):
        return registry_obj(data)

    def write_registry(self, data):
        p = self.tmp / "rules_sources.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def args(self, *extra):
        return rs.build_parser().parse_args(
            ["--out", str(self.out / "cand.json"), "--report", str(self.out / "report.md"),
             *extra])

    def run_tool(self, data, *extra, fetch=None):
        p = self.write_registry(data)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rs.run(self.args(*extra), fetcher=fetch, cache=self.cache, registry_path=p,
                          loop_lock=self.tmp / "nolock", inbox=self.inbox)
        return code, out.getvalue()

    def scrape(self, data, **kw):
        return rs.scrape(self.reg(data), fetcher=kw.pop("fetcher", None), cache=self.cache,
                         offline=kw.pop("offline", False), inbox=self.inbox, **kw)


# ---------------------------------------------------------------------------
# LF-01 / LF-21: path traversal, symlinks, links, special files
# ---------------------------------------------------------------------------
class PathSafetyTests(InboxTest):
    def test_lexical_paths_refused_at_load(self):  # LF-01
        for bad in ("../.env", "/home/user/.ssh/id_rsa", "sub/../../research.py", "a\\..\\b",
                    "a/./b", ".", "", "a//b", "C:x.pdf", "x\x00.pdf", ".env", "sub/.git/x",
                    "a b.pdf", "x" * 300):
            d = local_registry(local_src("fb", bad))
            with self.assertRaises(rs.Refused, msg=repr(bad)) as cm:
                rs.validate_registry(d)
            if len(bad) > 3:
                self.assertNotIn(bad, str(cm.exception))
        rs.validate_registry(local_registry(local_src("fb", "fairburn/ch-80_v2.html")))

    def refused(self, path, expect, data=None):
        """Run one source; assert the refusal, zero bytes read, no values,
        and a console with neither the path, the file name nor its bytes."""
        d = data or local_registry(local_src("fb", path))
        with mock.patch.object(rs, "_read_fd", wraps=rs._read_fd) as rd:
            res = self.scrape(d)
        o = res.outcomes[0]
        self.assertEqual(rd.call_count, 0, path)
        self.assertEqual(res.emitted, [], path)
        self.assertIn(expect, f"{o.status}: {o.reason}", path)
        code, console = self.run_tool(d)
        self.assertEqual(code, rs.EXIT_PARTIAL, path)
        for leak in (str(self.inbox_dir), str(self.root), path, path.rsplit("/", 1)[-1],
                     SENTINEL):
            self.assertNotIn(leak, console, (path, leak))      # LF-21
        self.assertIn("fb", console)
        return o

    def test_symlink_file_refused(self):
        target = self.root / ".env"
        (self.inbox_dir / "link.html").symlink_to(target)
        self.refused("link.html", "refused: file is a symlink")

    def test_symlink_into_a_subdirectory_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "f.html").write_text(MUNI_HTML + SENTINEL)
        (self.inbox_dir / "sub").symlink_to(outside, target_is_directory=True)
        o = self.refused("sub/f.html", "refused: ")
        # git itself refuses a path beyond a symlink; the O_NOFOLLOW walk is the second line.
        self.assertIn(o.reason, ("a folder on the path is a symlink or not a folder",
                                 "the inbox folder or the file is not gitignored"))
        with mock.patch.object(rs, "_git_ignored", return_value=True):
            o = self.scrape(local_registry(local_src("fb", "sub/f.html"))).outcomes[0]
        self.assertEqual(o.reason, "a folder on the path is a symlink or not a folder")

    def test_symlinked_inbox_refused(self):
        real = self.tmp / "Downloads"
        real.mkdir()
        (real / "f.html").write_text(MUNI_HTML)
        shutil.rmtree(self.inbox_dir)
        self.inbox_dir.symlink_to(real, target_is_directory=True)
        self.refused("f.html", "refused: the inbox folder is a symlink")

    def test_hard_link_to_a_decoy_env_refused(self):
        os.link(self.root / ".env", self.inbox_dir / "f.html")
        self.refused("f.html", "refused: file has more than one hard link")

    def test_fifo_refused_without_blocking(self):
        os.mkfifo(self.inbox_dir / "f.html")
        t0 = time.monotonic()
        self.refused("f.html", "refused: not a regular file")
        self.assertLess(time.monotonic() - t0, 10)

    def test_directory_refused(self):
        (self.inbox_dir / "f.html").mkdir()
        self.refused("f.html", "refused: not a regular file")

    def test_device_refused(self):
        try:
            os.mknod(self.inbox_dir / "f.html", 0o600 | stat.S_IFCHR, os.makedev(1, 3))
        except (PermissionError, OSError):
            self.skipTest("cannot create a device node here")
        self.refused("f.html", "refused: not a regular file")

    def test_oversize_refused(self):
        self.put("f.html", b"x" * 1025)
        with mock.patch.object(rs, "MAX_LOCAL_BYTES", 1024):
            self.refused("f.html", "refused: file larger than the size cap")
        self.put("g.html", b"<p>x</p>" + b" " * 1016)
        with mock.patch.object(rs, "MAX_LOCAL_BYTES", 1024):
            o = self.scrape(local_registry(local_src("fb", "g.html"))).outcomes[0]
        self.assertEqual(o.status, "read")              # exactly at the cap: read

    def test_inbox_outside_the_repo_or_not_ignored_refused(self):
        other = self.tmp / "rules_inbox"
        other.mkdir()
        (other / "f.html").write_text(MUNI_HTML)
        d = local_registry(local_src("fb", "f.html"))
        res = rs.scrape(self.reg(d), fetcher=None, cache=self.cache, offline=False,
                        inbox=rs.LocalInbox(other, self.root))
        self.assertEqual(res.outcomes[0].reason, "the inbox folder is not inside the repository")
        (self.root / ".gitignore").write_text(".env\n")
        self.put("f.html", MUNI_HTML)
        self.refused("f.html", "refused: the inbox folder or the file is not gitignored")

    def test_swap_between_check_and_read_refused(self):  # LF-01 TOCTOU
        self.put("f.html", MUNI_HTML)
        self.put("evil.html", f"<p>{SENTINEL}</p>")
        real_open = os.open

        def swapping(path, flags, mode=0o777, *, dir_fd=None):
            if path == "f.html" and dir_fd is not None:
                os.replace(self.inbox_dir / "evil.html", self.inbox_dir / "f.html")
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(rs.os, "open", side_effect=swapping):
            res = self.scrape(local_registry(local_src("fb", "f.html")))
        o = res.outcomes[0]
        self.assertEqual((o.status, o.reason), ("refused", "file changed between check and read"))
        self.assertEqual(res.emitted, [])

    def test_missing_file_is_unreachable_exit_4_no_traceback(self):  # LF-11
        d = local_registry(local_src("fb", "absent.html"))
        o = self.scrape(d).outcomes[0]
        self.assertEqual(f"{o.status}: {o.reason}", "unreachable: file not in rules_inbox/")
        code, console = self.run_tool(d)
        self.assertEqual(code, rs.EXIT_PARTIAL)
        self.assertNotIn("Traceback", console)


# ---------------------------------------------------------------------------
# LF-02 / LF-15 / LF-20: provenance sniff and format checks
# ---------------------------------------------------------------------------
class ProvenanceTests(InboxTest):
    def outcome(self, rel, data, fmt="html", src=None):
        self.put(rel, data)
        res = self.scrape(local_registry(src or local_src("fb", rel, fmt)))
        return res, res.outcomes[0]

    def test_mirror_markers_refused(self):  # LF-02
        prov = "provenance check failed: the file references a terms-blocked or mirror host"
        cases = {
            "canonical": '<link rel="canonical" href="https://www.zoneomics.com/code/fairburn-GA/chapter_2">',
            "saved_from": "<!-- saved from url=(0062)https://www.zoneomics.com/code/fairburn-GA/chapter_2 -->",
            "og_url": '<meta property="og:url" content="https://zoneomics.com/code/x">',
            "base": '<base href="https://api.zoneomics.com/">',
            "img": '<img src="https://cdn.zoneomics.com/logo.png">',
            "entity": '<a href="https://www.zoneomics&#46;com/x">x</a>',
        }
        for name, marker in cases.items():
            html = MUNI_HTML.replace("<head>", "<head>" + marker)
            res, o = self.outcome(f"{name}.html", html)
            self.assertEqual((o.status, o.reason), ("refused", prov), name)
            self.assertEqual(res.emitted, [], name)

    def test_pdf_footer_url_refused(self):  # LF-02
        if rs.pdftotext_info() is None:
            self.skipTest("pdftotext not installed")
        pdf = make_pdf(CITY_PDF_LINES, hex_lines=["https://www.zoneomics.com/code/x 1/9"])
        self.assertNotIn(b"zoneomics", pdf)                 # only in the text layer
        res, o = self.outcome("p.pdf", pdf, "pdf", pdf_src(path="p.pdf"))
        self.assertEqual(o.status, "refused")
        self.assertIn("provenance check failed", o.reason)
        pdf = make_pdf(CITY_PDF_LINES, info=b"/Producer (Zoneomics print)")
        _res, o = self.outcome("q.pdf", pdf, "pdf", pdf_src(path="q.pdf"))
        self.assertEqual(o.status, "refused")

    def test_obtained_from_and_title_checked_at_load(self):  # LF-02 / LF-08 / LF-15
        for field, bad in (("obtained_from", "https://zoneomics.com/code/fairburn"),
                           ("obtained_from", "www.zoneomics.com"),
                           ("obtained_from", "clerk@fairburnga.example"),
                           ("obtained_from", "/home/user/Downloads/x"),
                           ("obtained_from", "\\\\server\\share"),
                           ("obtained_from", "file:///tmp/x"),
                           ("obtained_from", "https://library.municode.com/ga/fairburn"),
                           ("obtained_from", "x" * 121),
                           ("obtained_from", "copy in rules_inbox/fb"),
                           ("title", "f.html"),
                           ("title", "~/Downloads/f")):
            d = local_registry(local_src("fb", "f.html", **{field: bad}))
            with self.assertRaises(rs.Refused, msg=bad) as cm:
                rs.validate_registry(d)
            self.assertNotIn(bad, str(cm.exception))
        rs.validate_registry(local_registry(local_src(
            "fb", "f.html", obtained_from="Municode export downloaded by operator")))

    def test_clean_municode_and_city_files_pass(self):  # LF-02
        res, o = self.outcome("m.html", MUNI_HTML)
        self.assertEqual((o.status, o.provenance_check), ("read", "passed"))
        self.assertTrue(res.emitted)
        if rs.pdftotext_info() is not None:
            res, o = self.outcome("c.pdf", make_pdf(CITY_PDF_LINES), "pdf", pdf_src(path="c.pdf"))
            self.assertEqual((o.status, o.provenance_check), ("read", "passed"))

    def test_format_mismatch_and_nul_refused(self):  # LF-20
        _r, o = self.outcome("h.pdf", MUNI_HTML, "pdf", pdf_src(path="h.pdf"))
        self.assertEqual((o.status, o.reason), ("refused", "format mismatch (not a PDF)"))
        _r, o = self.outcome("n.txt", "Sec. 80-85. a\x00b\n", "txt",
                             local_src("fb", "n.txt", "txt"))
        self.assertEqual(o.reason, "format mismatch (NUL bytes in a text file)")
        _r, o = self.outcome("p.html", b"%PDF-1.4 x", "html")
        self.assertEqual(o.reason, "format mismatch (a PDF registered as text)")
        _r, o = self.outcome("l.html", b"\xff\xfe<html>", "html")
        self.assertEqual(o.reason, "format mismatch (not UTF-8 text)")
        _r, o = self.outcome("t.txt", "<!DOCTYPE html><html></html>", "txt",
                             local_src("fb", "t.txt", "txt"))
        self.assertEqual(o.reason, "format mismatch (HTML registered as txt)")

    def test_one_megabyte_single_line_is_bounded(self):  # LF-20
        html = "<html><body><p>" + "Minimum front yard setback 12 feet " * 30000 + "</p></body></html>"
        t0 = time.monotonic()
        _r, o = self.outcome("big.html", html)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual((o.status, o.reason),
                         ("refused", "document above the parser caps (a text line is too long)"))

    def test_pdftotext_timeout_and_output_cap(self):  # LF-20
        slow = self.tmp / "slow-pdftotext"
        slow.write_text("#!/bin/sh\nsleep 20\n")
        slow.chmod(0o755)
        loud = self.tmp / "loud-pdftotext"
        loud.write_text("#!/bin/sh\nhead -c 300000 /dev/zero | tr '\\0' 'a'\n")
        loud.chmod(0o755)
        info = {"exe": str(slow), "version": "x", "argv": ["pdftotext"]}
        t0 = time.monotonic()
        with mock.patch.object(rs, "PDFTEXT_TIMEOUT_S", 0.5):
            self.assertIsNone(rs.pdf_document(make_pdf(CITY_PDF_LINES), info))
        self.assertLess(time.monotonic() - t0, 10)
        with mock.patch.object(rs, "MAX_PDFTEXT_BYTES", 1000):
            self.assertIsNone(rs.pdf_document(make_pdf(CITY_PDF_LINES), dict(info, exe=str(loud))))
        self.put("c.pdf", make_pdf(CITY_PDF_LINES))
        with mock.patch.object(rs, "pdftotext_info", return_value=info), \
                mock.patch.object(rs, "PDFTEXT_TIMEOUT_S", 0.5):
            res = self.scrape(local_registry(pdf_src(path="c.pdf")))
        self.assertEqual({s.status for s in res.outcomes[0].sections}, {"pdf text unavailable"})


# ---------------------------------------------------------------------------
# LF-03 / LF-04 / LF-05 / LF-06 / LF-07: the terms block
# ---------------------------------------------------------------------------
class TermsBlockTests(InboxTest):
    def test_predicate(self):  # LF-03
        for h in ("zoneomics.com", "www.zoneomics.com", "api.zoneomics.com", "a.b.zoneomics.com",
                  "ZONEOMICS.COM", "Www.ZoneOmics.Com", "zoneomics.com.", "www.zoneomics.com:443",
                  "xn--bcher-kva.zoneomics.com", "bücher.zoneomics.com",
                  "ｚｏｎｅｏｍｉｃｓ．ｃｏｍ", "user@zoneomics.com"):
            self.assertTrue(rs.terms_blocked(h), h)
        for h in ("notzoneomics.com", "zoneomics.com.evil.example", "zoneomics.co", "",
                  None, "www.fultoncountyga.gov"):
            self.assertFalse(rs.terms_blocked(h), h)
        self.assertTrue(rs.url_terms_blocked("https://ZONEOMICS.COM.:443/code/x"))
        self.assertTrue(rs.url_terms_blocked("https://evil.example\\@zoneomics.com/x"))
        self.assertFalse(rs.url_terms_blocked("https://www.fultoncountyga.gov/x?q=zoneomics.com"))
        self.assertIsInstance(rs.TERMS_BLOCKED_DOMAINS, frozenset)
        self.assertIn("zoneomics.com", rs.TERMS_BLOCKED_DOMAINS)
        self.assertFalse(rs.host_denied("www.zoneomics.com"))      # distinct from the denylist

    def mixed_registry(self, reviewed=False):
        d = zone_hosts(local_registry(zone_src("zm"), zone_src("zapi", "/x"),
                                      extra_url_sources=[county_src()]), reviewed)
        d["hosts"]["api.zoneomics.com"] = d["hosts"][ZONE]
        d["jurisdictions"]["fairburn"]["sources"][1]["url"] = "https://api.zoneomics.com/x"
        return d

    def zone_routes(self):
        r = routes_for({f"https://{COUNTY}/art10.html": COUNTY_HTML})
        r[f"https://{ZONE}/robots.txt"] = robots()
        r[f"https://{ZONE}/code/fairburn-GA/chapter_2"] = FakeResp(200, COUNTY_HTML)
        return r

    def test_zero_requests_to_a_blocked_host(self):  # LF-03 / LF-06
        d = self.mixed_registry(reviewed=True)
        f, http, _ = fetcher(self.zone_routes())
        res = self.scrape(d, fetcher=f, allow_unreviewed_terms=True)
        st = {o.source.key: (o.status, o.reason) for o in res.outcomes}
        self.assertEqual(st["zm"], (rs.STATUS_BLOCKED, rs.TERMS_BLOCKED))
        self.assertEqual(st["zapi"], (rs.STATUS_BLOCKED, rs.TERMS_BLOCKED))
        self.assertEqual(st["fc"][0], "fetched")
        self.assertFalse([c for c in http.calls if "zoneomics" in c])
        self.assertEqual(http.calls, [f"https://{COUNTY}/robots.txt", f"https://{COUNTY}/art10.html"])
        self.assertTrue(any(e.record.source_key == "fc" for e in res.emitted))
        # Direct fetcher use is refused before robots.txt too.
        for u in (f"https://{ZONE}/code/x", "https://API.zoneomics.com./x"):
            with self.assertRaises(rs.Unreachable) as cm:
                f.fetch(u)
            self.assertEqual(cm.exception.reason, rs.TERMS_BLOCKED)
        self.assertEqual(f.robots_for(f"https://{ZONE}").reason, rs.TERMS_BLOCKED)
        self.assertFalse([c for c in http.calls if "zoneomics" in c])

    def test_redirect_to_a_blocked_host_never_followed(self):  # LF-03
        r = routes_for({})
        r[f"https://{COUNTY}/art10.html"] = FakeResp(301, b"", {
            "Location": "https://www.county.example.zoneomics.com/x"})
        r[f"https://{COUNTY}/a.html"] = FakeResp(302, b"", {"Location": "https://zoneomics.com/x"})
        f, http, _ = fetcher(r)
        for u in (f"https://{COUNTY}/art10.html", f"https://{COUNTY}/a.html"):
            with self.assertRaises(rs.Unreachable) as cm:
                f.fetch(u)
            self.assertEqual(cm.exception.reason, rs.TERMS_BLOCKED)
        r2 = {f"https://{COUNTY}/robots.txt": FakeResp(301, b"", {
            "Location": "https://zoneomics.com/robots.txt"})}
        f2, http2, _ = fetcher(r2)
        self.assertEqual(f2.robots_for(f"https://{COUNTY}").reason, rs.TERMS_BLOCKED)
        self.assertFalse([c for c in http.calls + http2.calls if "zoneomics" in c])

    def test_signals_on_a_blocked_host_skipped(self):  # LF-03 / LF-05
        d = zone_hosts(local_registry(county_src()))
        d["jurisdictions"]["fairburn"]["change_signals"] = [
            {"key": "zsig", "enabled": True, "url": f"https://{ZONE}/arcgis/rest/services/Z/FeatureServer/0",
             "kind": "cases", "case_id_field": "C"}]
        reg = self.reg(d)
        sig = reg.signals[0]
        self.assertEqual(sig.blocked_reason, rs.TERMS_BLOCKED)
        f, http, _ = fetcher(self.zone_routes())
        g = rs.gis_signal(f, sig)
        self.assertEqual((g["reason"], g.get("skipped")), (rs.TERMS_BLOCKED, True))
        self.assertEqual(http.calls, [])
        from test_rules_scrape import FakeConn, stored_row
        conn = FakeConn([stored_row("p1", "fairburn", "M-1")])
        fr = rs.compute_flags(conn, reg, f, False)
        self.assertEqual(fr.layer_failures, [f"zsig: skipped ({rs.TERMS_BLOCKED})"])
        self.assertEqual(http.calls, [])

    def seed_zone_cache(self, url, final_url=None, key_url=None):
        body = COUNTY_HTML.encode()
        b, m = self.cache._paths(key_url or url)
        self.cache.root.mkdir(parents=True, exist_ok=True)
        b.write_bytes(body)
        m.write_text(json.dumps({"url": url, "final_url": final_url or url, "status": 200,
                                 "content_type": "text/html", "fetched_at": "2026-11-05",
                                 "sha256": hashlib.sha256(body).hexdigest()}))

    def test_offline_replay_of_a_blocked_page_refused(self):  # LF-04
        d = zone_hosts(local_registry(zone_src("zm")))
        self.seed_zone_cache(f"https://{ZONE}/code/fairburn-GA/chapter_2")
        with mock.patch.object(rs, "_document_for", wraps=rs._document_for) as doc, \
                mock.patch.object(rs, "cut_section", wraps=rs.cut_section) as cut:
            res = self.scrape(d, offline=True)
        o = res.outcomes[0]
        self.assertEqual((o.status, o.reason), (rs.STATUS_BLOCKED, rs.TERMS_BLOCKED))
        self.assertEqual((res.emitted, res.rejected, o.sections), ([], [], []))
        self.assertEqual((doc.call_count, cut.call_count), (0, 0))
        with self.assertRaises(rs.Unreachable) as cm:
            self.cache.load(f"https://{ZONE}/code/fairburn-GA/chapter_2")
        self.assertEqual(cm.exception.reason, rs.TERMS_BLOCKED)
        with self.assertRaises(rs.Unreachable):
            self.cache.store(rs.Fetched(f"https://{ZONE}/x", f"https://{ZONE}/x", 200, "", b"", ""))

    def test_official_cache_entry_with_a_blocked_final_url_refused(self):  # LF-04
        u = f"https://{COUNTY}/art10.html"
        self.seed_zone_cache(u, final_url="https://www.zoneomics.com/code/x")
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("body read")):
            with self.assertRaises(rs.Unreachable) as cm:
                self.cache.load(u)
        self.assertEqual(cm.exception.reason, rs.TERMS_BLOCKED)
        res = self.scrape(local_registry(county_src()), offline=True)
        self.assertEqual((res.outcomes[0].status, res.outcomes[0].reason),
                         ("unreachable", rs.TERMS_BLOCKED))

    def test_offline_llm_never_reads_the_llm_cache_for_a_blocked_source(self):  # LF-04
        d = zone_hosts(local_registry(dict(zone_src("zm"), parser="llm_prose", sections=[
            {"id": "p", "label": "Sec. 9-1", "start": "Sec. 9-1.", "end": "Sec. 9-2.",
             "district": "M-1", "config": {"fields": ["front_setback_ft"]}}])))
        self.seed_zone_cache(f"https://{ZONE}/code/fairburn-GA/chapter_2")
        llm = rs.LlmExtractor(self.cache, offline=True, exe="/nonexistent/claude")
        with mock.patch.object(rs.Cache, "llm_path", side_effect=AssertionError("llm cache")) as lp, \
                mock.patch.object(llm, "extract", side_effect=AssertionError("extract")):
            res = self.scrape(d, offline=True, llm=llm)
        self.assertEqual(lp.call_count, 0)
        self.assertEqual(res.outcomes[0].status, rs.STATUS_BLOCKED)

    def test_watch_skips_blocked_sources_and_keeps_their_baseline(self):  # LF-05
        d = zone_hosts(local_registry(zone_src("zm"), extra_url_sources=[county_src()]), True)
        prev = {"sources": {"zm": {"raw_sha256": "a" * 64, "sections": {"m1": "b" * 64},
                                   "ord": None}}, "gis": {}}
        (self.out / "watch_state.json").write_text(json.dumps(prev))
        f, http, _ = fetcher(self.zone_routes())
        code, console = self.run_tool(d, "--watch", "--allow-unreviewed-terms", fetch=f)
        self.assertEqual(code, rs.EXIT_OK)
        self.assertFalse([c for c in http.calls if "zoneomics" in c])
        self.assertIn(f"https://{COUNTY}/art10.html", http.calls)
        state = json.loads((self.out / "watch_state.json").read_text())
        self.assertEqual(state["sources"]["zm"], prev["sources"]["zm"])
        self.assertIn("fc", state["sources"])
        self.assertIn(f"1 enabled source(s) skipped ({rs.TERMS_BLOCKED}): zm.", console)
        self.assertIn("skipped", console)
        report = (self.out / "report.md").read_text()
        self.assertIn(f"| section | zm/m1 | skipped | {rs.TERMS_BLOCKED} |", report)
        self.assertIn("| section | fc/m1 | unknown |", report)    # fetched, first watch

    def test_live_registry_loads_with_blocked_sources(self):  # LF-06 / LF-23
        live = json.loads(rs.REGISTRY_PATH.read_text(encoding="utf-8"))
        reg = rs.load_registry()
        blocked = [s.key for s in reg.sources if s.blocked_reason]
        self.assertEqual(len([s for s in reg.sources if s.blocked_reason and s.enabled]), 7)
        self.assertIn("cp_ch3_mirror", blocked)
        self.assertFalse(reg.source("cp_ch3_mirror").enabled)
        for k in ("fc_art10_pdf", "uc_zoning_ordinance_pdf"):
            self.assertIsNone(reg.source(k).blocked_reason)
            self.assertTrue(reg.source(k).enabled)
        raw = {s["key"]: s for j in live["jurisdictions"].values() for s in j.get("sources") or []}
        for s in reg.sources:                          # same SourceDefs as before (LF-23)
            r = raw[s.key]
            self.assertEqual((s.kind, s.url, s.format, s.source_class, s.parser, s.enabled,
                              s.path, s.title, s.sha256_pin),
                             ("url", r["url"], r["format"], r["source_class"], r["parser"],
                              r["enabled"], None, None, None))
            self.assertEqual(bool(s.blocked_reason), "zoneomics" in r["url"])

    def test_live_registry_reviewed_and_flag_still_zero_requests(self):  # LF-06
        live = json.loads(rs.REGISTRY_PATH.read_text(encoding="utf-8"))
        live["hosts"][ZONE].update({"terms_reviewed_by": "op", "terms_reviewed_on": "2026-11-06"})
        reg = rs.validate_registry(live)
        self.assertTrue(reg.terms_reviewed(ZONE))
        f, http, _ = fetcher({})
        res = rs.scrape(reg, fetcher=f, cache=self.cache, offline=False,
                        allow_unreviewed_terms=True, inbox=self.inbox)
        self.assertFalse([c for c in http.calls if "zoneomics" in c])
        self.assertEqual(sum(o.status == rs.STATUS_BLOCKED for o in res.outcomes), 7)
        self.assertTrue({"www.fultoncountyga.gov", "www.unioncityga.gov"} >= set(f.requests))

    def test_mirror_relabel_still_refuses_the_registry(self):  # LF-06 (F6 kept)
        for host in (ZONE, "api.zoneomics.com", "ZoneOmics.com"):
            d = zone_hosts(local_registry(dict(zone_src("zm"), source_class="official")))
            d["hosts"][host] = d["hosts"][ZONE]
            d["jurisdictions"]["fairburn"]["sources"][0]["url"] = f"https://{host}/x"
            with self.assertRaises(rs.Refused):
                rs.validate_registry(d)

    def test_no_switch_lifts_the_block(self):  # LF-06 / LF-07 static
        src = (REPO_ROOT / "rules_scrape.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        assigns = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                   and any(isinstance(t, ast.Name) and t.id == "TERMS_BLOCKED_DOMAINS"
                           for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
        self.assertEqual(len(assigns), 1)
        self.assertFalse([n for n in ast.walk(tree) if isinstance(n, ast.Global)
                          and "TERMS_BLOCKED_DOMAINS" in n.names])
        # Environment: only the variables read before this change.
        env_funcs = set()
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv") \
                        and isinstance(node.value, ast.Name) and node.value.id == "os":
                    env_funcs.add(fn.name)
        self.assertEqual(env_funcs, {"loop_running", "_invoke"})
        self.assertNotIn("getenv", src)
        env_consts = re.findall(r"os\.environ\.get\(\"([A-Z_]+)\"", src)
        self.assertEqual(env_consts, ["EXPERIMENT_LOOP_LOCK_PATH"])
        # Parameters and flags: nothing about 'block'; the only 'terms' one is
        # the existing review override, which does not lift the block (above).
        import inspect
        for f in (rs.scrape, rs.run):
            for p in inspect.signature(f).parameters:
                self.assertNotIn("block", p.lower(), (f.__name__, p))
                if "terms" in p.lower():
                    self.assertEqual(p, "allow_unreviewed_terms")
        opts = [o for a in rs.build_parser()._actions for o in a.option_strings]
        self.assertFalse([o for o in opts if "block" in o])
        self.assertEqual([o for o in opts if "terms" in o], ["--allow-unreviewed-terms"])
        # Registry: no key can say 'terms ok' (host keys unchanged).
        self.assertEqual(rs._HOST_KEYS, {"terms_url", "terms_reviewed_by", "terms_reviewed_on",
                                         "note"})
        for ks in (rs._SRC_KEYS, rs._LOCAL_SRC_KEYS, rs._JUR_KEYS, rs._SIG_KEYS, rs._TOP_KEYS):
            self.assertFalse([k for k in ks if "block" in k or "terms" in k])
        d = zone_hosts(local_registry(zone_src("zm")))
        d["hosts"][ZONE]["terms_ok"] = True
        with self.assertRaises(rs.Refused):
            rs.validate_registry(d)


# ---------------------------------------------------------------------------
# LF-08 / LF-11 / LF-15 / LF-16: local runs end to end
# ---------------------------------------------------------------------------
_CITE_RE = re.compile(r"^official: .+, .+ \(obtained from .+, \d{4}-\d{2}-\d{2}\)$")


class LocalRunTests(InboxTest):
    def test_no_path_in_any_output_and_document_cites(self):  # LF-08 / LF-15
        self.put("fairburn/ch80-synthetic.html", MUNI_HTML)
        d = local_registry(local_src("fb", "fairburn/ch80-synthetic.html"))
        code, console = self.run_tool(d, "--watch")
        self.assertEqual(code, rs.EXIT_OK, console)
        texts = {p.name: p.read_text(encoding="utf-8") for p in self.out.iterdir()}
        self.assertEqual(set(texts), {"cand.json", "report.md", "report.json", "watch_state.json"})
        home = os.path.expanduser("~")
        for name, t in texts.items():
            for leak in (str(self.inbox_dir), str(self.root), str(REPO_ROOT), home + "/",
                         "ch80-synthetic", "rules_inbox/fairburn", "file:"):
                self.assertNotIn(leak, t, (name, leak))
        cand = json.loads(texts["cand.json"])
        entry = cand["_candidate"]["sources"]["fb"]
        self.assertEqual({k: entry[k] for k in ("kind", "obtained_from", "obtained_on",
                                                "provenance_check", "sha256_pinned")},
                         {"kind": "local_file", "obtained_from": "City Clerk (synthetic)",
                          "obtained_on": "2026-11-01", "provenance_check": "passed",
                          "sha256_pinned": False})
        self.assertEqual(entry["sha256"], hashlib.sha256(MUNI_HTML.encode()).hexdigest())
        self.assertNotIn("url", entry)
        self.assertNotIn("path", entry)
        dims = list(cand["_candidate"]["unplaced_values"])
        for dd in cand["jurisdictions"]["fairburn"]["districts"].values():
            for f, v in (dd.get("dimensions") or {}).items():
                dims.append(dict(v, field=f))
        cites = [v["cite"] for v in dims]
        self.assertTrue(cites)
        for c in cites:
            self.assertRegex(c, _CITE_RE)
        got = {v["field"]: (v["value"], v["confidence"]) for v in dims}
        self.assertEqual(got["front_setback_ft"], (47, "high"))
        self.assertEqual(got["rear_setback_ft"], (29, "high"))
        self.assertEqual(got["side_setback_ft"][0], 13)          # footnote -> review
        self.assertTrue(next(v for v in dims if v["field"] == "side_setback_ft")["needs_review"])
        self.assertNotIn(91, [v["value"] for v in dims])         # the C-9 block is not M-1
        self.assertNotIn(61, [v["value"] for v in dims])         # nor the M-2 one

    def test_sha_pin_semantics(self):  # LF-11
        p = self.put("f.html", MUNI_HTML)
        sha = hashlib.sha256(MUNI_HTML.encode()).hexdigest()
        ok = local_registry(local_src("fb", "f.html", sha256=sha))
        res = self.scrape(ok)
        self.assertEqual(res.outcomes[0].status, "read")
        self.assertTrue(res.emitted)
        bad = local_registry(local_src("fb", "f.html", sha256="0" * 64))
        res = self.scrape(bad)
        o = res.outcomes[0]
        self.assertEqual((o.status, o.reason), ("refused", "file sha256 differs from the pinned "
                                                "value (operator replaced file? update the pin)"))
        self.assertEqual((res.emitted, o.sections), ([], []))
        code, _ = self.run_tool(bad)
        self.assertEqual(code, rs.EXIT_PARTIAL)
        for pin in ("A" * 64, "abc", "record on first use ", None):
            with self.assertRaises(rs.Refused):
                rs.validate_registry(local_registry(local_src("fb", "f.html", sha256=pin)))
        # Unpinned: recorded, then a replaced file is reported as such.
        tofu = local_registry(local_src("fb", "f.html"))
        code, _ = self.run_tool(tofu, "--watch")
        self.assertEqual(code, rs.EXIT_OK)
        state = json.loads((self.out / "watch_state.json").read_text())
        self.assertEqual(state["sources"]["fb"]["raw_sha256"], sha)
        p.write_text(MUNI_HTML.replace("29 feet", "31 feet"))
        code, console = self.run_tool(tofu, "--watch")
        report = (self.out / "report.md").read_text()
        self.assertIn("| local file sha256 | fb | changed | changed (operator replaced file) |",
                      report)
        self.assertIn("| section | fb/m1 | changed |", report)
        self.assertIn("changed 2", console)

    def test_no_network_in_a_local_only_run(self):  # LF-15 / LF-16
        self.put("f.html", MUNI_HTML)
        d = local_registry(local_src("fb", "f.html",
                                     obtained_from="Municode export downloaded by operator"))

        def boom(*a, **k):
            raise AssertionError("network used")

        with mock.patch.object(socket, "socket", side_effect=boom), \
                mock.patch.object(socket, "create_connection", side_effect=boom), \
                mock.patch.object(requests.Session, "request", side_effect=boom):
            code, console = self.run_tool(d)
        self.assertEqual(code, rs.EXIT_OK, console)
        cand = json.loads((self.out / "cand.json").read_text())
        self.assertEqual(cand["jurisdictions"]["fairburn"]["districts"]["M-1"]["dimensions"][
            "front_setback_ft"]["value"], 47)
        # A mixed run: the recording fetcher sees only the url source.
        mixed = local_registry(local_src("fb", "f.html"), extra_url_sources=[county_src()])
        f, http, _ = fetcher(routes_for({f"https://{COUNTY}/art10.html": COUNTY_HTML}))
        res = self.scrape(mixed, fetcher=f)
        self.assertEqual(http.calls, [f"https://{COUNTY}/robots.txt", f"https://{COUNTY}/art10.html"])
        self.assertEqual({o.source.key: o.status for o in res.outcomes}, {"fb": "read", "fc": "fetched"})

    def test_offline_reads_local_files_without_the_cache(self):
        self.put("f.html", MUNI_HTML)
        res = self.scrape(local_registry(local_src("fb", "f.html")), offline=True)
        self.assertEqual(res.outcomes[0].status, "read")
        self.assertFalse(self.cache.root.exists() and list(self.cache.root.iterdir()))


# ---------------------------------------------------------------------------
# LF-10: the verifier on local files
# ---------------------------------------------------------------------------
class LocalVerifierTests(InboxTest):
    def setUp(self):
        super().setUp()
        html = PROSE_HTML.replace(
            "<p>FAR is 50%.</p>",
            "<table><tr><td>District</td><td>Front yard</td><td>Side yard</td><td>Rear yard</td></tr>"
            "<tr><td>I-1</td><td>60 feet</td><td>25 feet</td><td>40 feet</td></tr></table>"
            "<p>Minimum front yard: 25 feet; side yard as required by the building code.</p>"
            "<p>I-1 Rear: 20 feet. Side: None. Front yard: 40 feet.</p>"
            "<p>I-1-C rear yard 90 feet.</p>").replace("Rear: 50 feet", "Rear: 5&shy;0 feet")
        self.put("atl.html", html)
        d = base_registry()
        atl = d["jurisdictions"]["atlanta"]["sources"][0]
        loc = local_src("atl_local", "atl.html", parser="llm_prose", sections=atl["sections"])
        d["jurisdictions"] = {"atlanta": dict(d["jurisdictions"]["atlanta"], sources=[loc])}
        self.data = d

    def verdicts(self, records):
        recs = [rs.Record("atlanta", "I-1", f, v, q, "atl_local", "dev", "assisted")
                for f, v, q in records]
        with mock.patch.object(rs, "verify", wraps=rs.verify) as vf:
            res = self.scrape(self.data, assisted=recs)
        self.assertEqual(vf.call_count, len(recs))
        out = {}
        for e in res.emitted + res.rejected:
            out[(e.record.field, e.record.value, e.record.quote)] = e.verdict
        return [out[(f, v, q)] for f, v, q in records]

    def test_round_one_regressions_hold_on_a_local_source(self):
        cases = [
            ("front_setback_ft", 40, "Front yard: forty feet", "rejected"),       # fabricated
            ("front_setback_ft", 40, "front: 40 feet.", "rejected"),              # not verbatim
            ("rear_setback_ft", 60, "Rear yard I-1 | 60 feet", "rejected"),       # across rows
            ("side_setback_ft", 5, "5 feet; side yard", "rejected"),              # fragment
            ("rear_setback_ft", None, "Rear: 20 feet. Side: None.", "rejected"),  # unattached null
            ("rear_setback_ft", 90, "I-1-C rear yard 90 feet", "rejected"),       # suffixed district
            ("rear_setback_ft", 50, "(b) Rear: 50 feet.", "accepted"),            # soft hyphen dropped
            ("rear_setback_ft", 5, "(b) Rear: 5 feet.", "rejected"),
            ("front_setback_ft", 40, "(a) Front: 40 feet.", "accepted"),
        ]
        got = self.verdicts([c[:3] for c in cases])
        for c, v in zip(cases, got):
            self.assertEqual(v.outcome, c[3], c)
        self.assertEqual(got[2].reasons, ("quote crosses table rows",))

    def test_txt_keeps_lines(self):
        doc = rs.text_document(CITY_TXT.encode())
        self.assertEqual(doc.raw_lines, [ln.rstrip() for ln in CITY_TXT.split("\n") if ln.strip()])
        self.assertEqual(len(doc.lines), len(doc.raw_lines))

    def test_emitted_only_from_emit(self):
        tree = ast.parse((REPO_ROOT / "rules_scrape.py").read_text(encoding="utf-8"))
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "Emitted":
                    self.assertEqual(fn.name, "_emit", node.lineno)


# ---------------------------------------------------------------------------
# LF-12: official formats
# ---------------------------------------------------------------------------
class OfficialFormatTests(InboxTest):
    def test_municode_shape_cuts_and_parses(self):
        self.put("m.html", MUNI_HTML)
        res = self.scrape(local_registry(local_src("fb", "m.html")))
        got = {e.record.field: (e.record.district, e.record.value, e.verdict.outcome)
               for e in res.emitted}
        self.assertEqual(got["front_setback_ft"], ("M-1", 47, "accepted"))
        self.assertEqual(got["rear_setback_ft"], ("M-1", 29, "accepted"))
        self.assertEqual(got["side_setback_ft"][:2], ("M-1", 13))
        self.assertEqual(got["side_setback_ft"][2], "needs_review")

    def test_municode_chain_does_not_jump(self):
        secs = []
        for code, no, name in (("M-1", "80-85", "Light"), ("M-2", "80-86", "Heavy")):
            secs.append({"id": code.lower().replace("-", ""), "label": f"Sec. {no}",
                         "start": [f"Sec. {no}. - {code}—{name} Industrial Zoning District.",
                                   "Development standards."],
                         "end": "Design standards.", "district": code,
                         "config": {"labels": [{"label": "Minimum rear yard setback",
                                                "field": "rear_setback_ft"}]}})
        self.put("c.html", MUNI_CHAIN_HTML)
        res = self.scrape(local_registry(local_src("fb", "c.html", sections=secs)))
        st = {s.defn.id: s.status for s in res.outcomes[0].sections}
        self.assertEqual(st, {"m1": "found", "m2": "not found in source"})
        self.assertEqual([(e.record.district, e.record.value) for e in res.emitted], [("M-1", 37)])

    def test_city_pdf_and_txt_parse(self):
        self.put("c.txt", CITY_TXT)
        tx = local_src("t", "c.txt", "txt", sections=pdf_src()["sections"])
        res = self.scrape(local_registry(tx))
        self.assertEqual(sorted((e.record.field, e.record.value) for e in res.emitted),
                         [("front_setback_ft", 43), ("rear_setback_ft", 27)])
        if rs.pdftotext_info() is None:
            self.skipTest("pdftotext not installed")
        self.put("c.pdf", make_pdf(CITY_PDF_LINES))
        res = self.scrape(local_registry(pdf_src(path="c.pdf")))
        got = {e.record.field: (e.record.value, e.verdict.outcome) for e in res.emitted}
        self.assertEqual(got, {"front_setback_ft": (43, "accepted"), "rear_setback_ft": (27, "accepted")})

    def test_image_only_pdf_is_no_text_layer(self):
        if rs.pdftotext_info() is None:
            self.skipTest("pdftotext not installed")
        self.put("s.pdf", make_pdf())
        d = local_registry(pdf_src(path="s.pdf"))
        res = self.scrape(d)
        self.assertEqual({s.status for s in res.outcomes[0].sections}, {rs.NO_TEXT_LAYER})
        code, console = self.run_tool(d)
        self.assertEqual(code, rs.EXIT_PARTIAL)
        self.assertIn("ask for a text PDF; values cannot be verified", console)
        self.assertNotIn("route to --assisted", console)
        rows = json.loads((self.out / "report.json").read_text())["rows"]
        self.assertNotIn("not found in source", {r["category"] for r in rows})

    def test_parser_format_compatibility_at_load(self):
        for parser, fmt in (("district_grid", "txt"), ("use_table", "txt"), ("pdf_grid", "txt"),
                            ("pdf_grid", "html"), ("district_grid", "pdf")):
            d = local_registry(local_src("fb", "f.x", fmt, parser))
            with self.assertRaises(rs.Refused, msg=(parser, fmt)) as cm:
                rs.validate_registry(d)
            self.assertIn("cannot read format", str(cm.exception))
        d = base_registry()
        d["jurisdictions"]["south_fulton"]["sources"][0]["format"] = "pdf"   # url source too
        with self.assertRaises(rs.Refused):
            rs.validate_registry(d)


# ---------------------------------------------------------------------------
# LF-13 / LF-23: registry schema
# ---------------------------------------------------------------------------
class SchemaTests(unittest.TestCase):
    def test_kind_key_sets(self):
        url_with_path = county_src()
        url_with_path["path"] = "f.html"
        bad = [
            local_registry(url_with_path),
            local_registry(dict(local_src("fb", "f.html"), url=f"https://{COUNTY}/x")),
            local_registry(dict(local_src("fb", "f.html"), kind="ftp")),
            local_registry(dict(county_src(), kind="file")),
            local_registry(local_src("fb", "f.html", source_class="mirror")),
            local_registry(local_src("fb", "f.doc", "doc")),
            local_registry(local_src("fb", "f.html", sha256="deadbeef")),
            local_registry(dict(county_src(), obtained_from="x")),
            local_registry(dict(county_src(), sha256="0" * 64)),
            local_registry(local_src("fc", "f.html"), extra_url_sources=[county_src("fc")]),
        ]
        missing = local_src("fb", "f.html")
        del missing["obtained_on"]
        bad.append(local_registry(missing))
        for i, d in enumerate(bad):
            with self.assertRaises(rs.Refused, msg=i):
                rs.validate_registry(d)
        reg = rs.validate_registry(local_registry(local_src("fb", "f.html"),
                                                  extra_url_sources=[county_src()]))
        self.assertEqual({s.key: s.kind for s in reg.sources}, {"fb": "local_file", "fc": "url"})
        self.assertEqual(reg.source("fb").url, "")
        self.assertEqual(reg.source("fb").identity, "local:fb")

    def test_placeholders_and_dates(self):  # LF-13
        today = date(2026, 11, 6)
        for over in ({"obtained_from": "<OPERATOR: channel>"}, {"obtained_on": "<OPERATOR: date>"},
                     {"title": "TODO title"}, {"obtained_on": "2026-11-07"},
                     {"obtained_on": "1999-12-31"}, {"obtained_on": "11/01/2026"}):
            d = local_registry(local_src("fb", "f.html", **over))
            with self.assertRaises(rs.Refused, msg=over) as cm:
                rs.validate_registry(d, today=today)
            msg = str(cm.exception)
            self.assertIn("sources[0]", msg)
            for v in over.values():
                self.assertNotIn(v, msg)
        for over in ({"obtained_from": "<OPERATOR: channel>"}, {"obtained_on": "<OPERATOR: date>"}):
            rs.validate_registry(local_registry(local_src("fb", "f.html", enabled=False, **over)),
                                 today=today)
        future = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaises(rs.Refused):
            rs.validate_registry(local_registry(local_src("fb", "f.html", obtained_on=future)))


# ---------------------------------------------------------------------------
# LF-13 / LF-14 / LF-18 / LF-22 / LF-26: the registry patch (throwaway copy)
# ---------------------------------------------------------------------------
class RegistryPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rules-patch-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.patch = json.loads(PATCH_PATH.read_text(encoding="utf-8"))
        self.live_raw = rs.REGISTRY_PATH.read_bytes()
        self.live = json.loads(self.live_raw.decode("utf-8"))

    def patched(self, optional=False):
        d = apply_patch(self.live, self.patch, optional)
        p = self.tmp / "rules_sources.json"
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        return d, p

    def test_patched_copy_loads_and_the_real_file_is_untouched(self):
        self.assertEqual(self.patch["base_registry_sha256"], hashlib.sha256(self.live_raw).hexdigest())
        for optional in (False, True):
            d, p = self.patched(optional)
            self.assertFalse(rs._is_under(p.resolve(), REPO_ROOT.resolve()))
            reg = rs.load_registry(p)
            enabled = {s.key for s in reg.sources if s.enabled}
            self.assertEqual(enabled, {"fc_art10_pdf", "uc_zoning_ordinance_pdf"})
            self.assertFalse([s for s in reg.sources if s.enabled and s.blocked_reason])
            local = [s for s in reg.sources if s.is_local]
            self.assertEqual({s.jurisdiction for s in local}, {"south_fulton", "fairburn"})
            self.assertTrue(all(not s.enabled and s.sha256_pin is None for s in local))
            self.assertEqual(reg.hosts[ZONE]["terms_url"], "https://www.zoneomics.com/terms-of-use")
            for h in ("www.fultoncountyga.gov", "www.unioncityga.gov"):    # LF-14
                self.assertFalse(reg.terms_reviewed(h), h)
                self.assertIn("NOT CHECKED BY THE AGENT", reg.hosts[h]["note"])
                self.assertIn("<OPERATOR SIGNS>", reg.hosts[h]["note"])
            atl = {g.key: g.enabled for g in reg.signals if g.jurisdiction == "atlanta"}
            self.assertEqual(set(atl.values()), {not optional})
        self.assertEqual(rs.REGISTRY_PATH.read_bytes(), self.live_raw)

    def test_enabling_a_placeholder_entry_is_refused(self):  # LF-13
        d, _p = self.patched()
        src = d["jurisdictions"]["fairburn"]["sources"][-1]
        src["enabled"] = True
        with self.assertRaises(rs.Refused) as cm:
            rs.validate_registry(d)
        self.assertIn("sources[", str(cm.exception))
        self.assertNotIn("OPERATOR", str(cm.exception))

    def test_patch_covers_every_zoneomics_source(self):
        keys = {s["key"] for j in self.live["jurisdictions"].values() for s in j.get("sources") or []
                if "zoneomics" in s["url"]}
        self.assertEqual(set(self.patch["disable_sources"]), keys)
        added = {sd["label"].split(" (")[0] for srcs in self.patch["add_sources"].values()
                 for s in srcs for sd in s["sections"]}
        for want in ("Sec. 403 / Table 4-1", "Sec. 207.06 / Table 2-4", "Sec. 405", "Sec. 510",
                     "Sec. 80-85(f)-(g)", "Sec. 80-86(f)-(g)", "Sec. 80-337", "Sec. 80-367",
                     "Sec. 80-372"):
            self.assertIn(want, added)

    def test_diff_categories_for_mirror_jurisdictions(self):  # LF-22
        current = json.loads(rs.RULES_PATH.read_text(encoding="utf-8"))
        gone = {"south_fulton", "fairburn", "atlanta", "east_point", "hapeville"}
        for data, want in ((self.live, "source blocked (terms)"),
                           (self.patched()[0], "source disabled")):
            reg = rs.validate_registry(data)
            f, _http, _ = fetcher({})
            res = rs.scrape(reg, fetcher=f, cache=rs.Cache(self.tmp / "c"), offline=False,
                            inbox=rs.LocalInbox(self.tmp / "none", self.tmp))
            rows = rs.diff(res, current, reg)
            for r in rows:
                if r.jurisdiction in gone:
                    self.assertNotEqual(r.category, rs.REMOVED_NOT_EXTRACTED, r)
            cats = {r.category for r in rows if r.jurisdiction in gone}
            self.assertIn(want, cats)


# ---------------------------------------------------------------------------
# LF-09 / LF-17 / LF-18 / LF-27: repository hygiene and cite classes
# ---------------------------------------------------------------------------
class HygieneTests(unittest.TestCase):
    def test_inbox_ignored_and_untracked(self):  # LF-09
        git = shutil.which("git")
        if git is None:
            self.skipTest("git not installed")
        out = subprocess.run([git, "check-ignore", "-q", "--no-index", "--", "rules_inbox/x.pdf"],
                             cwd=REPO_ROOT, capture_output=True, timeout=30, check=False)
        if out.returncode == 128:
            self.skipTest("not a git work tree")
        self.assertEqual(out.returncode, 0)
        ls = subprocess.run([git, "ls-files", "--", "rules_inbox"], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(ls.stdout, "")
        self.assertEqual(rs.INBOX_DIR, REPO_ROOT / "rules_inbox")

    def test_review_folder_is_not_writable(self):  # LF-09 / LF-18
        with self.assertRaises(rs.Refused):
            rs.guard_output_path(REVIEW_DIR / "x.json")
        with self.assertRaises(rs.Refused):
            rs.guard_output_path(REPO_ROOT / "rules_inbox" / "x.json")
        self.assertEqual(rs.REPO_OUTPUT_DIRS, (rs.OUT_DIR, rs.CACHE_DIR, rs.REVIEW_DIR))
        self.assertEqual(rs.HUMAN_OWNED, ("zoning_rules.json", "building_prototypes.json",
                                          "sources.json", "parameters.json", "program.md",
                                          "rules_sources.json"))
        self.assertIn("rules_scrape.py", rs.PROTECTED_MODULES)

    def test_fixtures_short_and_synthetic(self):  # LF-09
        for name, fx in (("MUNI_HTML", MUNI_HTML), ("MUNI_CHAIN_HTML", MUNI_CHAIN_HTML),
                         ("CITY_TXT", CITY_TXT), ("COUNTY_HTML", COUNTY_HTML)):
            self.assertLess(len(fx.encode()), 2048 + 1024 * (name == "MUNI_HTML"), name)
            self.assertIn("synthetic", fx, name)
        self.assertIn("synthetic", CITY_PDF_LINES[0])

    def test_review_artifacts_small_no_model_names(self):  # LF-09 / LF-27
        rx = re.compile(r"(?i)\b(opus|sonnet|haiku|gpt-?\d|gemini|llama)\b|claude-\d")
        self.assertTrue(REVIEW_DIR.exists())
        for p in sorted(REVIEW_DIR.iterdir()):
            self.assertIn(p.suffix, (".json", ".md"), p.name)
            self.assertLess(p.stat().st_size, 100_000, p.name)
            t = p.read_text(encoding="utf-8")
            self.assertIsNone(rx.search(t), p.name)
            self.assertNotIn("<html", t.lower(), p.name)

    def test_cite_class_for_document_cites(self):  # LF-17
        src = rs.SourceDef("fairburn", "fb", True, "", "html", "official", "label_value", (),
                           kind="local_file", path="f.html", title="Synthetic Code, Ch. 80",
                           obtained_from="City Clerk", obtained_on="2026-11-01")
        c = rs.local_cite(src, "Sec. 80-85(f)")
        self.assertEqual(c, "official: Synthetic Code, Ch. 80, Sec. 80-85(f) "
                            "(obtained from City Clerk, 2026-11-01)")
        self.assertEqual(rs.cite_class(c), "official")
        self.assertEqual(rs.cite_class(c + " https://www.zoneomics.com/code/x"), "mixed")
        self.assertEqual(rs.cite_class(c + " zoneomics"), "mixed")
        self.assertEqual(rs.cite_class("https://api.zoneomics.com/x Sec. 1"), "mirror")
        self.assertEqual(rs.cite_class("Table 4-1"), "unknown")


class DiffLocalTests(InboxTest):
    def test_new_official_cite_and_new_cite(self):  # LF-17
        self.put("m.html", MUNI_HTML)
        d = local_registry(local_src("fb", "m.html"))
        reg = self.reg(d)
        res = self.scrape(d)
        z = "https://www.zoneomics.com/code/fairburn-GA/chapter_2 Sec. 80-85(f)"
        other = "official: Another Synthetic Code, Sec. 80-85(f) (obtained from City Clerk, 2026-01-01)"
        same = res.emitted[0].section.cite
        for cite, want in ((z, "same value, new official cite"), (other, "same value, new cite"),
                           (same, "same")):
            cur = {"jurisdictions": {"fairburn": {"districts": {"M-1": {
                "warehouse_distribution": {"status": "permitted_by_right", "cite": cite},
                "dimensions": {"front_setback_ft": {"value": 47, "cite": cite}}}}}}}
            row = next(r for r in rs.diff(res, cur, reg) if r.field == "front_setback_ft")
            self.assertEqual(row.category, want, cite)


# ---------------------------------------------------------------------------
# Post-change review, round 1 (reviews/24_rules_local_files/03_decision_note.md)
# ---------------------------------------------------------------------------
PCT_ZONE_URLS = ("https://zoneomics%2ecom/x", "https://%7Aoneomics.com/x",
                 "https://www%2Ezoneomics.com/code/south-fulton-GA/chapter_4",
                 "https://www%2ezoneomics.com/code/x", "https://www%252ezoneomics.com/x")
PCT_MUNI = "library.%6Dunicode.com"

# Fairburn-shaped markers (as in the registry patch), a two-column table as
# pdftotext -layout prints it: label, a run of spaces, value. Invented values.
TWO_COL_LINES = [
    "CHAPTER 80 ZONING (synthetic fixture, invented values)",
    "Sec. 80-85. - M-1—Light Industrial Zoning District.",
    "(f) M-1—Development standards.",
    "   Minimum front yard setback                    47 feet",
    "   Minimum side yard setback                     13 feet",
    "   Minimum rear yard setback                     29 feet",
    "(g) M-1—Design standards.",
]


class ReviewRound1Tests(InboxTest):
    # -- finding 1: percent-encoded hosts ------------------------------------
    def test_percent_encoded_host_predicates(self):
        for u in PCT_ZONE_URLS:
            self.assertTrue(rs.url_terms_blocked(u), u)
            self.assertFalse(rs._https_url(u), u)
            if "%25" not in u:     # what requests would actually contact: the blocked host
                self.assertTrue(rs.terms_blocked(urlparse_host(
                    requests.Request("GET", u).prepare().url)), u)
        self.assertEqual(rs.norm_host("www%2ezoneomics.com"), "www.zoneomics.com")
        self.assertEqual(rs.norm_host("%7Aoneomics.com"), "zoneomics.com")
        self.assertTrue(rs.terms_blocked("zoneomics%2ecom"))
        self.assertTrue(rs.mirror_host("www%2Ezoneomics.com"))
        self.assertTrue(rs.host_denied(PCT_MUNI))
        self.assertTrue(rs.host_denied("LIBRARY.MUNICODE.COM."))
        self.assertFalse(rs._https_url(f"https://{PCT_MUNI}/ga/x"))
        self.assertEqual(urlparse_host(requests.Request("GET", f"https://{PCT_MUNI}/ga/x")
                                       .prepare().url), "library.municode.com")
        self.assertFalse(rs.host_denied("www.fultoncountyga.gov"))
        self.assertFalse(rs.url_terms_blocked("https://www.fultoncountyga.gov/a%20b.pdf"))

    def test_percent_encoded_host_refused_in_the_registry(self):
        for cls in ("mirror", "official"):
            for host, url in (("www%2ezoneomics.com", "https://www%2ezoneomics.com/code/x"),
                              (PCT_MUNI, f"https://{PCT_MUNI}/ga/x")):
                d = local_registry(dict(county_src("zp"), url=url, source_class=cls))
                d["hosts"][host] = {"terms_url": None, "terms_reviewed_by": "op",
                                    "terms_reviewed_on": "2026-11-05", "note": "x"}
                with self.assertRaises(rs.Refused, msg=(cls, host)):
                    rs.validate_registry(d)
        d = local_registry(county_src())
        d["jurisdictions"]["fairburn"]["change_signals"] = [
            {"key": "zsig", "enabled": True, "kind": "layer",
             "url": "https://www%2ezoneomics.com/arcgis/rest/services/x/FeatureServer/0"}]
        d["hosts"]["www%2ezoneomics.com"] = d["hosts"][COUNTY]
        with self.assertRaises(rs.Refused):
            rs.validate_registry(d)

    def test_percent_encoded_host_never_contacted(self):
        f, http, _ = fetcher({})
        for u in PCT_ZONE_URLS + (f"https://{PCT_MUNI}/ga/x",):
            with self.assertRaises(rs.Unreachable, msg=u):
                f.fetch(u)
            with self.assertRaises(rs.Unreachable, msg=u):
                f._raw_get(u)
        self.assertTrue(f.robots_for("https://www%2ezoneomics.com").deny_all)
        self.assertEqual(http.calls, [])
        # A redirect to a percent-encoded blocked host is not followed either.
        r = routes_for({})
        r[f"https://{COUNTY}/a.html"] = FakeResp(302, b"", {
            "Location": "https://www%2ezoneomics.com/x"})
        f, http, _ = fetcher(r)
        with self.assertRaises(rs.Unreachable):
            f.fetch(f"https://{COUNTY}/a.html")
        self.assertFalse([c for c in http.calls if "zoneomics" in c.lower()])

    def test_raw_get_backstop_checks_the_prepared_url(self):
        # Even with the front checks fooled, _raw_get refuses: it re-checks
        # the host of the URL requests will actually contact.
        real_tb, real_hd = rs.url_terms_blocked, rs.host_denied
        with mock.patch.object(rs, "url_terms_blocked",
                               lambda u: False if "%" in str(u) else real_tb(u)), \
                mock.patch.object(rs, "host_denied",
                                  lambda h: False if "%" in str(h) else real_hd(h)):
            f, http, _ = fetcher({})
            with self.assertRaises(rs.Unreachable) as cm:
                f._raw_get("https://www%2ezoneomics.com/code/x")
            self.assertEqual(cm.exception.reason, rs.TERMS_BLOCKED)
            with self.assertRaises(rs.Unreachable) as cm:
                f._raw_get(f"https://{PCT_MUNI}/ga/x")
            self.assertEqual(cm.exception.reason, "denylisted host")
        self.assertEqual(http.calls, [])

    def test_percent_encoded_host_cache_and_signal(self):
        u = "https://www%2ezoneomics.com/code/x"
        with self.assertRaises(rs.Unreachable):
            self.cache.store(rs.Fetched(u, u, 200, "text/html", b"<p>x</p>", ""))
        self.seed_cache(u)
        with self.assertRaises(rs.Unreachable):
            self.cache.load(u)
        ok = f"https://{COUNTY}/art10.html"
        self.seed_cache(ok, final_url=u)
        with self.assertRaises(rs.Unreachable):
            self.cache.load(ok)
        sig = rs.SignalDef("fairburn", "zsig", True, "https://www%2ezoneomics.com/x/FeatureServer/0",
                           "layer", None, None, ())
        f, http, _ = fetcher({})
        out = rs.gis_signal(f, sig)
        self.assertEqual((out["reason"], out.get("skipped")), (rs.TERMS_BLOCKED, True))
        self.assertEqual(http.calls, [])

    def seed_cache(self, url, final_url=None):
        body = COUNTY_HTML.encode()
        b, m = self.cache._paths(url)
        self.cache.root.mkdir(parents=True, exist_ok=True)
        b.write_bytes(body)
        m.write_text(json.dumps({"url": url, "final_url": final_url or url, "status": 200,
                                 "content_type": "text/html", "fetched_at": "2026-11-05",
                                 "sha256": hashlib.sha256(body).hexdigest()}))

    # -- finding 3: two-column table PDF / text ------------------------------
    def fb_pdf_src(self, path="fairburn/chapter80.pdf", fmt="pdf"):
        return local_src("fb", path, fmt, sections=[
            {"id": "m1_standards", "label": "Sec. 80-85(f)-(g)",
             "start": ["Sec. 80-85. - M-1—Light Industrial Zoning District.",
                       "M-1—Development standards."],
             "end": "M-1—Design standards.", "district": "M-1",
             "config": {"labels": LABELS_M1}}])

    def test_two_column_table_pdf_reads_with_label_value(self):
        if rs.pdftotext_info() is None:
            self.skipTest("pdftotext not installed")
        self.put("fairburn/chapter80.pdf", make_pdf(TWO_COL_LINES))
        d = local_registry(self.fb_pdf_src())
        res = self.scrape(d)
        got = {e.record.field: (e.record.value, e.verdict.outcome) for e in res.emitted}
        self.assertEqual(got, {"front_setback_ft": (47, "accepted"),
                               "side_setback_ft": (13, "accepted"),
                               "rear_setback_ft": (29, "accepted")})
        for e in res.emitted:      # the quote is the normalised line of the cut
            self.assertIn(e.record.quote, e.section.text.split("\n"))
        code, _console = self.run_tool(d)
        self.assertEqual(code, rs.EXIT_OK)

    def test_tab_separated_txt_and_html_unchanged(self):
        txt = "\n".join(["synthetic fixture", *TWO_COL_LINES[1:3],
                         "Minimum front yard setback\t47 feet",
                         "Minimum side yard setback \t 13 feet",
                         "Minimum rear yard setback 29 feet",      # one space: not a separator
                         TWO_COL_LINES[-1]]) + "\n"
        self.put("fairburn/ch80.txt", txt)
        res = self.scrape(local_registry(self.fb_pdf_src("fairburn/ch80.txt", "txt")))
        self.assertEqual(sorted((e.record.field, e.record.value) for e in res.emitted),
                         [("front_setback_ft", 47), ("side_setback_ft", 13)])
        self.assertIn(("fb", "m1_standards", "rear_setback_ft: label found, no separator or "
                       "value after it on its line"), res.section_review)
        # HTML is unchanged: whitespace inside one cell never separates.
        html = MUNI_HTML.replace("<td><p>Minimum front yard setback</p></td><td><p>47 feet</p></td>",
                                 "<td><p>Minimum front yard setback    47 feet</p></td>")
        self.put("m.html", html)
        res = self.scrape(local_registry(local_src("fb", "m.html")))
        self.assertNotIn("front_setback_ft", {e.record.field for e in res.emitted})

    def test_label_without_separator_reported_and_exit_4(self):
        lines = [re.sub(r"(?<=\S) {2,}", " ", ln) for ln in TWO_COL_LINES]
        self.put("fairburn/ch80.txt", "\n".join(lines) + "\n")
        d = local_registry(self.fb_pdf_src("fairburn/ch80.txt", "txt"))
        res = self.scrape(d)
        self.assertEqual(res.emitted, [])
        msgs = [m for _k, _i, m in res.section_review]
        self.assertTrue(msgs and all("label found, no separator" in m for m in msgs), msgs)
        code, console = self.run_tool(d)
        self.assertEqual(code, rs.EXIT_PARTIAL)
        self.assertIn("found but no value read", console)
        self.assertIn("fb/m1_standards", console)

    # -- finding 4: assisted records for an unusable section -----------------
    def test_assisted_record_for_a_scanned_section_is_counted(self):
        if rs.pdftotext_info() is None:
            self.skipTest("pdftotext not installed")
        self.put("fairburn/chapter80.pdf", make_pdf())
        d = local_registry(self.fb_pdf_src(), dict(local_src("off", "x.html"), enabled=False))
        recs = {"extractor": "assisted", "records": [
            {"source": "fb", "section": "m1_standards", "district": "M-1",
             "field": "front_setback_ft", "value": 47,
             "quote": "Minimum front yard setback 47 feet"},
            {"source": "off", "section": "m1", "district": "M-1",
             "field": "front_setback_ft", "value": 47,
             "quote": "Minimum front yard setback: 47 feet"}]}
        ap = self.tmp / "assisted.json"
        ap.write_text(json.dumps(recs))
        code, console = self.run_tool(d, "--assisted", str(ap))
        self.assertEqual(code, rs.EXIT_PARTIAL)
        self.assertIn("assisted rejected 2", console)
        self.assertIn("2 assisted record(s) rejected, their section was not read", console)
        self.assertIn(f"fb/m1_standards ({rs.NO_TEXT_LAYER})", console)
        self.assertIn("off/m1 (source disabled)", console)
        self.assertIn("values: none", console)
        res = self.scrape(d, assisted=rs.load_assisted(ap, self.reg(d))[0])
        self.assertEqual(res.assisted_rejected, 2)
        self.assertEqual(sorted(k for k, _i, _w in res.assisted_unusable), ["fb", "off"])
        # out of the --jurisdiction selection: not counted
        res = self.scrape(d, assisted=rs.load_assisted(ap, self.reg(d))[0],
                          jurisdictions=["fid"])
        self.assertEqual(res.assisted_rejected, 0)

    # -- finding 5: invisible characters in a mirror name --------------------
    def test_invisible_characters_do_not_hide_a_mirror_name(self):
        for t in ("zone­omics", "zone​omics.com", "www.zone‍omics.com",
                  "zone⁠omics", "zone﻿omics", "zone&shy;omics", "zone%C2%ADomics"):
            self.assertTrue(rs.mentions_blocked_host(t), repr(t))
        self.assertFalse(rs.mentions_blocked_host("Fulton County Zoning Resolution"))
        html = MUNI_HTML.replace("</body>", "<p>Saved from www.zone­omics.com</p></body>")
        self.put("m.html", html)
        res = self.scrape(local_registry(local_src("fb", "m.html")))
        self.assertEqual(res.outcomes[0].status, "refused")
        self.assertEqual(res.emitted, [])

    # -- finding 8: placeholder markers in an enabled local entry ------------
    def test_placeholder_markers_refused_when_enabled(self):
        for over in ({"start": "<OPERATOR: re-derive from the file>"},
                     {"start": ["Sec. 80-85.", "<OPERATOR: x>"]},
                     {"end": "<OPERATOR: re-derive from the file>"},
                     {"label": "TODO section label"}):
            src = local_src("fb", "f.html")
            src["sections"][0].update(over)
            with self.assertRaises(rs.Refused, msg=over) as cm:
                rs.validate_registry(local_registry(src))
            self.assertIn("placeholder", str(cm.exception))
            self.assertNotIn("OPERATOR", str(cm.exception))
            rs.validate_registry(local_registry(dict(src, enabled=False)))

    # -- findings 6/7: a placeholder signature is not a terms review ----------
    def test_placeholder_terms_signature_is_not_a_review(self):
        d = base_registry()
        d["hosts"][COUNTY].update({"terms_reviewed_by": "<OPERATOR: your name>",
                                   "terms_reviewed_on": "<OPERATOR: YYYY-MM-DD>"})
        reg = rs.validate_registry(d)
        self.assertFalse(reg.terms_reviewed(COUNTY))
        d["hosts"][COUNTY].update({"terms_reviewed_by": "A. Operator",
                                   "terms_reviewed_on": "2026-11-06"})
        self.assertTrue(rs.validate_registry(d).terms_reviewed(COUNTY))


def urlparse_host(u):
    return urlparse(u).hostname


# ---------------------------------------------------------------------------
# Finding 2: diff categories in the PATCHED registry (kept disabled entries)
# ---------------------------------------------------------------------------
class PatchedDiffTests(InboxTest):
    def setUp(self):
        super().setUp()
        patch = json.loads(PATCH_PATH.read_text(encoding="utf-8"))
        self.live_raw = rs.REGISTRY_PATH.read_bytes()
        self.data = apply_patch(json.loads(self.live_raw.decode("utf-8")), patch)
        self.current = json.loads(rs.RULES_PATH.read_text(encoding="utf-8"))

    def enable_fairburn(self, **over):
        d = copy.deepcopy(self.data)
        src = next(s for s in d["jurisdictions"]["fairburn"]["sources"]
                   if s["key"] == "fairburn_ch80_standards_local")
        src.update({"enabled": True, "title": "Synthetic Code of Ordinances, Chapter 80",
                    "obtained_from": "City Clerk (synthetic)", "obtained_on": "2026-11-01"})
        src.update(over)
        return d, src

    def fairburn_rows(self, d):
        reg = rs.validate_registry(d)
        res = self.scrape(d, offline=True, jurisdictions=["fairburn"])
        rows = [r for r in rs.diff(res, self.current, reg, jurisdictions=["fairburn"])]
        return res, rows

    def assert_category(self, d, want):
        res, rows = self.fairburn_rows(d)
        st = {o.source.key: o.status for o in res.outcomes}
        self.assertEqual(st["fairburn_ch2_mirror"], "disabled")
        self.assertEqual(st["fairburn_ch80_prose_local"], "disabled")
        cats = {r.category for r in rows}
        self.assertNotIn(rs.REMOVED_NOT_EXTRACTED, cats, (want, cats))
        self.assertIn(want, cats)
        return rows

    def test_missing_file_is_source_unreachable(self):
        self.assert_category(self.enable_fairburn()[0], "source unreachable")

    def test_pin_mismatch_is_source_refused(self):
        self.put("fairburn/chapter80.html", MUNI_HTML)
        d, _src = self.enable_fairburn(sha256="0" * 64)
        self.assert_category(d, "source refused (local file)")

    def test_markers_absent_is_not_found_in_source(self):
        self.put("fairburn/chapter80.html", "<!-- synthetic --><html><body><p>Sec. 1. Other."
                                            "</p></body></html>")
        self.assert_category(self.enable_fairburn()[0], "not found in source")

    def test_image_only_pdf_is_no_text_layer(self):
        if rs.pdftotext_info() is None:
            self.skipTest("pdftotext not installed")
        self.put("fairburn/chapter80.pdf", make_pdf())
        d, _src = self.enable_fairburn(path="fairburn/chapter80.pdf", format="pdf")
        self.assert_category(d, "no text layer (scanned PDF)")

    def test_a_good_file_gives_values_and_the_real_registry_is_untouched(self):
        self.put("fairburn/chapter80.html", MUNI_HTML)
        res, rows = self.fairburn_rows(self.enable_fairburn()[0])
        m1 = {r.field: r for r in rows if r.district == "M-1"}
        self.assertEqual(m1["front_setback_ft"].candidate, 47)
        self.assertEqual(rs.REGISTRY_PATH.read_bytes(), self.live_raw)


if __name__ == "__main__":
    unittest.main()
