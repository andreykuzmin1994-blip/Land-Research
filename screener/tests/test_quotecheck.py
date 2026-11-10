"""quotecheck: a claim is kept only if its quote is on the page it cites
(reviews/35_screener_lead_layer). Offline: every fetch goes through a fake."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from screener import quotecheck as qc

URL = "https://city.example.gov/code/article-9"
PAGE = b"""<html><head><style>.x{color:red}</style><script>var q = "warehouses are prohibited";</script>
</head><body><h1>Article 9 &ndash; M-1 Light Industrial</h1>
<p>Permitted uses: <b>Warehousing</b> and distribution&nbsp;facilities,
   \xe2\x80\x9clight\xe2\x80\x9d manufacturing, and truck terminals.</p></body></html>"""
QUOTE = 'Permitted uses: Warehousing and distribution facilities, "light" manufacturing'


class FakeFetcher(qc.Fetcher):
    def __init__(self, pages=None, robots=None):
        self.slept: list[float] = []
        self.now = [0.0]
        super().__init__(sleep=self.slept.append, clock=lambda: self.now[0])
        self.pages = dict(pages or {})
        self.robots = robots              # None: 404; bytes: the file; Exception: raised
        self.calls: list[str] = []

    def get(self, url):
        self.wait(url)
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            if self.robots is None:
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
            if isinstance(self.robots, Exception):
                raise self.robots
            return self.robots, "text/plain"
        if url not in self.pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        body = self.pages[url]
        if isinstance(body, Exception):
            raise body
        return body, "application/pdf" if body[:5] == b"%PDF-" else "text/html; charset=utf-8"


def claim(**over):
    c = {"text": "Warehousing is a permitted use in M-1.", "source_url": URL,
         "retrieved": "2026-11-10", "quote": QUOTE}
    c.update(over)
    return c


def findings(*claims, verdict="by_right", **over):
    f = {"schema": 1, "parcel_id": "14F0001 0001",
         "sections": {"zoning": {"verdict": verdict, "claims": list(claims)}}}
    f.update(over)
    return f


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.pages = self.dir / qc.PAGES_DIR
        self.fetcher = FakeFetcher({URL: PAGE})

    def check(self, f):
        return qc.check(f, self.pages, self.fetcher)


class CheckTests(Base):
    def test_quote_on_the_page_is_kept(self):
        r = self.check(findings(claim()))
        self.assertEqual((r.kept, r.dropped, r.downgraded), (1, [], []))
        zoning = r.checked["sections"]["zoning"]
        self.assertEqual(zoning["verdict"], "by_right")
        self.assertEqual(zoning["claims"][0]["page_file"], f"pages/{qc.page_name(URL)}")
        self.assertEqual(r.checked["quotecheck"]["kept"], 1)
        self.assertIn("nothing more", r.checked["quotecheck"]["proves"])
        self.assertTrue((self.pages / (qc.page_name(URL) + ".html")).is_file())

    def test_matching_ignores_case_spacing_and_typographic_quotes(self):
        r = self.check(findings(claim(quote="PERMITTED USES:   warehousing and\n distribution "
                                            "facilities, “light” manufacturing")))
        self.assertEqual(r.kept, 1)

    def test_quote_not_on_the_page_is_dropped_and_the_verdict_becomes_unknown(self):
        r = self.check(findings(claim(quote="Warehousing is permitted by right in all districts")))
        self.assertEqual(r.kept, 0)
        self.assertEqual(r.dropped[0]["reason"], "the quote is not on the page")
        zoning = r.checked["sections"]["zoning"]
        self.assertEqual((zoning["verdict"], zoning["verdict_as_written"]), ("unknown", "by_right"))
        self.assertEqual(r.downgraded, ["zoning"])
        self.assertEqual(zoning["claims"], [])
        self.assertEqual(len(r.checked["dropped_claims"]), 1)

    def test_text_inside_script_or_style_does_not_count(self):
        r = self.check(findings(claim(quote="warehouses are prohibited in this district")))
        self.assertEqual(r.kept, 0)
        hidden = self.check(findings(claim(quote='var q = "warehouses are prohibited"')))
        self.assertEqual(hidden.kept, 0)

    def test_one_kept_claim_keeps_the_verdict(self):
        r = self.check(findings(claim(), claim(quote="this sentence is nowhere on that page at all")))
        self.assertEqual((r.kept, len(r.dropped), r.downgraded), (1, 1, []))
        self.assertEqual(r.checked["sections"]["zoning"]["verdict"], "by_right")

    def test_unknown_with_no_claims_is_left_alone(self):
        r = self.check(findings(verdict="unknown"))
        self.assertEqual((r.kept, r.downgraded), (0, []))
        self.assertNotIn("verdict_as_written", r.checked["sections"]["zoning"])

    def test_malformed_claims_are_dropped_without_a_fetch(self):
        cases = {
            "no claim text": claim(text=" "),
            "no source_url": claim(source_url=None),
            "not an http(s) address": claim(source_url="file:///etc/passwd"),
            "carries credentials": claim(source_url="https://user:pw@city.example.gov/x"),
            "no quote of at least": claim(quote="too short"),
            "quote longer than": claim(quote="x" * (qc.MAX_QUOTE_CHARS + 1)),
            "no retrieved date": claim(retrieved="yesterday"),
            "is not an object": "just a string",
        }
        for words, bad in cases.items():
            with self.subTest(words):
                self.fetcher.calls.clear()
                r = self.check(findings(bad))
                self.assertEqual(r.kept, 0)
                self.assertIn(words, r.dropped[0]["reason"])
                self.assertEqual(self.fetcher.calls, [])

    def test_refused_hosts_are_never_fetched(self):
        for host in ("www.costar.com", "loopnet.com", "www.crexi.com", "x.y.LoopNet.com"):
            with self.subTest(host):
                self.fetcher.calls.clear()
                r = self.check(findings(claim(source_url=f"https://{host}/listing/1")))
                self.assertIn("never fetched", r.dropped[0]["reason"])
                self.assertEqual(self.fetcher.calls, [])
        # A look-alike host is not refused by the rule (and is simply not found here).
        r = self.check(findings(claim(source_url="https://notcostar.com.example.org/x")))
        self.assertIn("could not be fetched", r.dropped[0]["reason"])

    def test_robots_txt_is_honoured(self):
        self.fetcher.robots = b"User-agent: *\nDisallow: /code/\n"
        r = self.check(findings(claim()))
        self.assertEqual(r.dropped[0]["reason"], "page robots.txt disallows this page")
        self.assertEqual(self.fetcher.calls, ["https://city.example.gov/robots.txt"])
        self.assertFalse(self.pages.exists())

    def test_crawl_delay_is_honoured_and_a_long_one_refuses_the_host(self):
        self.fetcher.robots = b"User-agent: *\nCrawl-delay: 10\n"
        r = self.check(findings(claim()))
        self.assertEqual(r.kept, 1)
        # robots.txt itself was asked at the default pace; the page waited the 10 s asked.
        self.assertEqual(self.fetcher.slept, [10.0])
        self.fetcher = FakeFetcher({URL: PAGE}, robots=b"User-agent: *\nCrawl-delay: 120\n")
        self.pages = self.dir / "pages2"
        r = self.check(findings(claim()))
        self.assertIn("asks for 120 s between requests", r.dropped[0]["reason"])
        self.assertEqual(self.fetcher.calls, ["https://city.example.gov/robots.txt"])

    def test_default_pace_is_one_second_per_host(self):
        other = "https://city.example.gov/code/article-10"
        self.fetcher.pages[other] = PAGE
        self.check(findings(claim(), claim(source_url=other)))
        self.assertEqual(self.fetcher.slept, [qc.HOST_DELAY_S, qc.HOST_DELAY_S])

    def test_unreadable_robots_txt_means_no_fetch(self):
        for robots, words in ((urllib.error.HTTPError("u", 403, "x", None, None), "HTTP 403"),
                              (urllib.error.HTTPError("u", 503, "x", None, None), "HTTP 503"),
                              (OSError("timed out"), "timed out")):
            with self.subTest(words):
                self.fetcher = FakeFetcher({URL: PAGE}, robots=robots)
                r = self.check(findings(claim()))
                self.assertIn("robots.txt could not be read", r.dropped[0]["reason"])
                self.assertIn(words, r.dropped[0]["reason"])
                self.assertEqual(len(self.fetcher.calls), 1)

    def test_fetch_failure_and_empty_page(self):
        self.fetcher.pages[URL] = OSError("connection reset")
        r = self.check(findings(claim()))
        self.assertIn("could not be fetched (connection reset)", r.dropped[0]["reason"])
        self.fetcher = FakeFetcher({URL: b"<html><script>x</script></html>"})
        r = self.check(findings(claim()))
        self.assertEqual(r.dropped[0]["reason"], "page the page has no text")

    def test_each_page_is_fetched_once_and_reused_from_disk(self):
        self.check(findings(claim(), claim(text="Truck terminals are permitted.",
                                           quote="light manufacturing, and truck terminals")))
        self.assertEqual(self.fetcher.calls, ["https://city.example.gov/robots.txt", URL])
        again = FakeFetcher()                     # nothing served: must come from pages/
        r = qc.check(findings(claim()), self.pages, again)
        self.assertEqual((r.kept, again.calls), (1, []))

    def test_pdf_without_pdftotext_is_not_verifiable(self):
        self.fetcher.pages[URL] = b"%PDF-1.7 binary"
        orig = qc.shutil.which
        qc.shutil.which = lambda name: None
        self.addCleanup(setattr, qc.shutil, "which", orig)
        r = self.check(findings(claim()))
        self.assertIn("pdftotext is not installed", r.dropped[0]["reason"])
        self.assertTrue((self.pages / (qc.page_name(URL) + ".pdf")).is_file())

    def test_skeptic_claims_are_checked_but_its_verdict_is_its_own(self):
        f = findings(claim(), skeptic={"verdict": "killed", "objection": "x",
                                       "claims": [claim(quote="a quote that is not on the page")]})
        r = self.check(f)
        self.assertEqual(r.checked["skeptic"]["claims"], [])
        self.assertEqual(r.checked["skeptic"]["verdict"], "killed")
        self.assertEqual(r.dropped[0]["section"], "skeptic")

    def test_the_input_is_not_changed(self):
        f = findings(claim(quote="a quote that is not on the page at all"))
        before = json.dumps(f, sort_keys=True)
        self.check(f)
        self.assertEqual(json.dumps(f, sort_keys=True), before)

    def test_invalid_files_are_refused(self):
        for bad in ([], {"schema": 2}, {"schema": 1}, {"schema": 1, "parcel_id": "p"},
                    {"schema": 1, "parcel_id": "p", "sections": {"zoning": []}},
                    {"schema": 1, "parcel_id": "p", "sections": {"zoning": {"claims": "x"}}},
                    findings(skeptic="no"),
                    findings(*[claim()] * (qc.MAX_CLAIMS + 1))):
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(qc.Invalid):
                self.check(bad)


class CliTests(Base):
    def run_cli(self, text):
        path = self.dir / "findings.json"
        path.write_text(text)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = qc.main([str(path)], fetcher=self.fetcher)
        return code, out.getvalue() + err.getvalue()

    def test_writes_the_checked_file_and_reports(self):
        code, text = self.run_cli(json.dumps(findings(
            claim(), claim(quote="words that never appear on that page"))))
        self.assertEqual(code, 0)
        self.assertIn("1 claims kept, 1 dropped", text)
        self.assertIn("dropped (zoning): the quote is not on the page", text)
        checked = json.loads((self.dir / qc.CHECKED_NAME).read_text())
        self.assertEqual(len(checked["sections"]["zoning"]["claims"]), 1)
        self.assertEqual(json.loads((self.dir / "findings.json").read_text())["sections"]
                         ["zoning"]["claims"][1]["quote"], "words that never appear on that page")
        self.assertEqual(list(self.dir.glob("*.part")), [])

    def test_unreadable_or_invalid_input_is_refused(self):
        for text in ("{not json", json.dumps({"schema": 1})):
            with self.subTest(text=text):
                code, out = self.run_cli(text)
                self.assertEqual(code, 2)
                self.assertIn("refused:", out)
                self.assertFalse((self.dir / qc.CHECKED_NAME).exists())


class HelperTests(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(qc.normalise(" A –\tB\n“c” "), 'a - b "c"')

    def test_page_name_is_stable_and_safe(self):
        name = qc.page_name("https://x.example/../../etc/passwd?a=b")
        self.assertRegex(name, r"^[0-9a-f]{24}$")


if __name__ == "__main__":
    unittest.main()
