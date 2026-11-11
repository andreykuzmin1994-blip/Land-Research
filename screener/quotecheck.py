"""Check an investigation's findings against the pages they cite
(reviews/35_screener_lead_layer; the rules are in INVESTIGATE.md).

    python -m screener.quotecheck screener/leads/<parcel>/findings.json
    python -m screener.quotecheck screener/leads/<parcel>/findings.json --lead screener/leads/<parcel>/lead.md

An investigating agent writes ``findings.json``: per checklist section a
verdict and claims, each claim with the URL it rests on and an exact quote
from that page. A skeptic agent may later write ``skeptic.json`` beside it.
This tool, not an agent, fetches each URL and keeps a claim only if its
quote is on the page. It writes ``findings.checked.json``:

- a claim whose quote is not on its page, whose page could not be
  fetched, or that is malformed is dropped, with the reason;
- a section left with no kept claim has its verdict set to ``unknown``,
  whatever the agent wrote: a verdict with no surviving evidence is not a
  finding. A skeptic with no kept claim is marked ``supported: false``;
- each fetched page is recorded (when, final address, size, SHA-256).

Every run fetches every page again. A copy is saved in ``pages/`` beside
the findings as a record; a file already there is never used as the page
(an agent can write in that folder). The old ``findings.checked.json`` is
removed before anything else, so a failed run leaves none.

What it proves is narrow: the quoted words were on the page at that URL
when this tool fetched it. It does not prove the page is right, current
or authoritative, or that the claim follows from the quote. That is the
skeptic's job and then a person's. Claim text, objections and open items
are the agents' own words and are not checked.

What a kept quote must pass (reviews/35 .../06_quotecheck_review.md):
- the quote is not made of words from its own URL (a page that echoes its
  request would otherwise let the agent write the "page");
- it sits on the page between word boundaries ("allowed" is not found in
  "disallowed"), and the 80 characters on each side are saved with the
  claim, so a cut "not" shows;
- form fields, drop-down lists, templates and the page title do not count
  as page text; nor do scripts, styles or comments;
- in a PDF the quote must be inside one page.

Fetching rules: GET only, http(s) only, no cookies, no sign-ins. Never an
address that names CoStar, LoopNet or Crexi anywhere in it (their terms
prohibit automated use), so not through an archive, cache or reader
either. Only public internet addresses. robots.txt is read first and
honoured (wildcards included); a robots.txt that cannot be read (other
than "not found") means the page is not fetched, a 403 included. At least
one second between requests to a host, or the host's own Crawl-delay when
it asks for more (a host asking for more than 30 seconds is not fetched).
Redirects are followed by hand, at most five, each hop under all of these
rules; a redirect to another host is not followed (cite that address
instead). Size caps. The screener's own User-Agent. PDFs are read with
the ``pdftotext`` command when it is installed and are otherwise not
verifiable (the claim is dropped and says so).

``--lead`` also checks a ``lead.md`` against the checked file: every
quoted passage of 20 or more characters must be inside a kept quote, every
link must be a kept claim's address, and each verdict it gives must be
the checked one. Exit code 1 if it finds a problem.

Offline in tests: every request goes through ``Fetcher.open_once``, which
tests replace. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

AGENT_TOKEN = "land-screener"
USER_AGENT = f"{AGENT_TOKEN}/0.1 (internal land-acquisition research)"
SCHEMA = 1
FINDINGS_NAME = "findings.json"
SKEPTIC_NAME = "skeptic.json"
CHECKED_NAME = "findings.checked.json"
PAGES_DIR = "pages"
MIN_QUOTE_CHARS = 25
MAX_QUOTE_CHARS = 1200
MAX_PDF_BYTES = 200_000_000      # adopted comprehensive plans run past 150 MB
MAX_HTML_BYTES = 10_000_000      # anything that is not a PDF
MAX_ROBOTS_BYTES = 500_000
MAX_CLAIMS = 200
MAX_URLS = 60                    # distinct pages fetched in one run
MAX_REDIRECTS = 5
HOST_DELAY_S = 1.0
MAX_CRAWL_DELAY_S = 30.0         # a host asking for more is not fetched
TIMEOUT_S = 60
PDFTOTEXT_TIMEOUT_S = 300
CONTEXT_CHARS = 80
LEAD_QUOTE_CHARS = 20
UNKNOWN = "unknown"
# Never fetched: their terms prohibit automated use (README, "What it can't
# tell you"; CLAUDE.md "NEVER scrape CoStar"). A name anywhere in the
# address refuses it: the host, a look-alike domain, or the path of an
# archive, cache or reader service.
REFUSED_NAMES = ("costar", "loopnet", "crexi")
SKEPTIC = "skeptic"
# The checklist of INVESTIGATE.md: section -> the verdicts it may carry.
SECTIONS: dict[str, tuple[str, ...]] = {
    "zoning": ("by_right", "conditional", "rezoning_needed", "blocked", UNKNOWN),
    "plans": ("supports", "neutral", "against", UNKNOWN),
    "utilities": ("at_site", "nearby", "not_shown", UNKNOWN),
    "market": ("signs_for_sale", "signs_developer_pursuing", "signs_under_contract",
               "no_sign_found", UNKNOWN),
    "owner": ("operator", "developer_or_investor", "holding_entity", "individual_or_family",
              UNKNOWN),
}
SKEPTIC_VERDICTS = ("survives", "weakened", "killed")
TOP_KEYS = {"schema", "parcel_id", "run", "sections"}
SKEPTIC_FILE_KEYS = {"schema", "parcel_id", "run", SKEPTIC}
SECTION_KEYS = {"verdict", "claims", "open_items"}
SKEPTIC_KEYS = {"verdict", "objection", "claims", "open_items", "disputes"}
CLAIM_KEYS = {"text", "source_url", "retrieved", "quote"}
# Text inside these elements is not page text: code, and the places a page
# echoes what the request typed (form fields, lists, the title).
SKIPPED_TAGS = ("script", "style", "noscript", "template", "textarea", "select", "title")


class Invalid(Exception):
    """The findings file cannot be checked at all; the message says why."""


class FetchError(Exception):
    """A page was not fetched. The message is this tool's own words, never
    the server's. ``status`` is the HTTP status when there was one."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Fetched:
    body: bytes
    ctype: str
    final_url: str
    status: int


@dataclass
class Page:
    units: list[tuple[int, str]] = field(default_factory=list)   # (PDF page or 0, normalised text)
    problem: str = ""         # why there is no text
    record: dict[str, Any] = field(default_factory=dict)


class _Text(HTMLParser):
    """Text of an HTML page, without the elements in SKIPPED_TAGS."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in SKIPPED_TAGS:
            self._skip += 1
        self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS and self._skip:
            self._skip -= 1
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


_PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                        "–": "-", "—": "-", "−": "-"})
# Control and format characters other than whitespace: soft hyphens,
# zero-width characters, direction marks, terminal escapes.
_INVISIBLE = re.compile("[\u0000-\u0008\u000e-\u001f\u007f-\u009f­؜᠎"
                        "​-‏‪-‮⁠-⁤⁦-⁯﻿]")


def normalise(text: str) -> str:
    """One comparable form for a quote and a page: NFKC, straight quotes and
    hyphens, invisible characters removed, whitespace collapsed, case folded."""
    t = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text)).translate(_PUNCT)
    return re.sub(r"\s+", " ", t).strip().casefold()


def clean(text: Any, limit: int = 200) -> str:
    """Text safe to print: one line, no control characters, cut to limit."""
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", str(text))).strip()[:limit]


def html_text(raw: str) -> str:
    parser = _Text()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:           # a malformed page: no text, so nothing matches
        return ""
    return "".join(parser.parts)


def page_name(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")


def site_of(url: str) -> str:
    """The host a redirect may stay on: the host without a leading www."""
    host = host_of(url)
    return host[4:] if host.startswith("www.") else host


def _decoded(url: str) -> str:
    """The address with percent-escapes undone (three rounds), case folded."""
    out = url
    for _ in range(3):
        out = urllib.parse.unquote_plus(out)
    return out.casefold()


def url_problem(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        return "no source_url"
    if url != url.strip() or _INVISIBLE.search(url) or re.search(r"\s", url):
        return "source_url has spaces or control characters"
    try:
        parts = urllib.parse.urlsplit(url)
        host, _ = parts.hostname, parts.port          # .port raises on a bad port
    except ValueError:
        return "source_url is not an http(s) address"
    if parts.scheme not in ("http", "https") or not host:
        return "source_url is not an http(s) address"
    if parts.username or parts.password:
        return "source_url carries credentials"
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        return "source_url host is not plain ASCII (write it in its xn-- form)"
    if any(name in _decoded(url) for name in REFUSED_NAMES):
        return ("the address names a site that is never fetched, by any route "
                "(CoStar, LoopNet, Crexi: their terms prohibit automated use)")
    return ""


def quote_is_from_url(quote: str, url: str) -> bool:
    """True when MIN_QUOTE_CHARS in a row of the quote are in the address:
    a page that echoes its request would show them back."""
    nq = normalise(quote)
    forms = {normalise(_decoded(url)), normalise(urllib.parse.unquote(url))}
    return any(nq[i:i + MIN_QUOTE_CHARS] in form
               for form in forms for i in range(max(1, len(nq) - MIN_QUOTE_CHARS + 1)))


class Robots:
    """One host's robots.txt for this tool: the group naming AGENT_TOKEN, or
    else the ``*`` group. The longest matching rule wins, Allow on a tie;
    ``*`` and a closing ``$`` are understood (RFC 9309). Parsed here because
    the standard library's parser reads neither on older Pythons, and reads
    only whole-number Crawl-delay."""

    def __init__(self, text: str) -> None:
        groups: list[tuple[list[str], list[tuple[bool, str]], list[float]]] = []
        open_group = False                    # the last line was a User-agent line
        for line in text.lstrip("\ufeff").splitlines():
            key, sep, value = line.split("#", 1)[0].partition(":")
            key, value = key.strip().lower(), value.strip()
            if not sep or not key:
                continue
            if key == "user-agent":
                if not open_group:
                    groups.append(([], [], []))
                    open_group = True
                groups[-1][0].append(value.split("/")[0].strip().lower())
                continue
            open_group = False
            if not groups:
                continue
            if key in ("allow", "disallow") and value:
                groups[-1][1].append((key == "allow", value))
            elif key == "crawl-delay":
                try:
                    delay = float(value)
                except ValueError:
                    continue
                if delay >= 0 and delay == delay:
                    groups[-1][2].append(delay)
        mine = [g for g in groups if AGENT_TOKEN in g[0]] or [g for g in groups if "*" in g[0]]
        self.rules = [rule for g in mine for rule in g[1]]
        self.delay = max((d for g in mine for d in g[2]), default=0.0)

    @staticmethod
    def _norm(path: str) -> str:
        return urllib.parse.quote(urllib.parse.unquote(path), safe="/?=&*$:@!+,;~%-._()'")

    def allows(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        path = self._norm((parts.path or "/") + ("?" + parts.query if parts.query else ""))
        best = (-1, True)
        for allow, pattern in self.rules:
            pat = self._norm(pattern)
            anchored = pat.endswith("$")
            regex = ".*".join(re.escape(piece) for piece in pat.rstrip("$").split("*"))
            if re.match(regex + ("$" if anchored else ""), path):
                best = max(best, (len(pat), allow))
        return best[1]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None                 # a 3xx comes back as an HTTPError; fetch() decides


class Fetcher:
    """Polite GET requests under the rules in the module docstring. Tests
    replace ``open_once`` and ``resolve``."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._sleep, self._clock = sleep, clock
        self._last: dict[str, float] = {}
        self._delay: dict[str, float] = {}
        self._robots: dict[str, Robots | str] = {}
        self._public: dict[str, str] = {}
        self._opener = urllib.request.build_opener(_NoRedirect)

    def resolve(self, host: str) -> list[str]:
        """The addresses the host name stands for. Raises OSError."""
        return [info[4][0] for info in socket.getaddrinfo(host, None)]

    def address_problem(self, url: str) -> str:
        """'' when every address of the host is a public internet address."""
        host = host_of(url)
        if host not in self._public:
            try:
                addresses = [ipaddress.ip_address(a.split("%")[0]) for a in self.resolve(host)]
            except (OSError, ValueError):
                addresses = []
            if not addresses:
                self._public[host] = "the host name does not resolve"
            elif all(a.is_global for a in addresses):
                self._public[host] = ""
            else:
                self._public[host] = "the host is not a public internet address"
        return self._public[host]

    def open_once(self, url: str, cap: int) -> tuple[int, dict[str, str], bytes]:
        """One GET, no redirect followed: (status, headers by lower-case name,
        body). A 3xx is returned; any other failure raises FetchError."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
        try:
            with self._opener.open(req, timeout=TIMEOUT_S) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                if headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
                    raise FetchError("the reply is compressed, which was not asked for")
                body = resp.read(min(cap, MAX_HTML_BYTES) + 1)
                if len(body) > MAX_HTML_BYTES and cap > MAX_HTML_BYTES and (
                        body[:5] == b"%PDF-" or "pdf" in headers.get("content-type", "").lower()):
                    body += resp.read(cap + 1 - len(body))
                if len(body) > min(cap, MAX_HTML_BYTES) and not (
                        body[:5] == b"%PDF-" or "pdf" in headers.get("content-type", "").lower()):
                    raise FetchError(f"larger than {min(cap, MAX_HTML_BYTES):,} bytes and not a PDF")
                if len(body) > cap:
                    raise FetchError(f"larger than {cap:,} bytes")
                return resp.status, headers, body
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                return exc.code, {k.lower(): v for k, v in exc.headers.items()}, b""
            raise FetchError(f"HTTP {int(exc.code)}", status=int(exc.code)) from None
        except FetchError:
            raise
        except TimeoutError:
            raise FetchError("timed out") from None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise FetchError(f"the request failed ({type(exc).__name__})") from None

    def fetch(self, url: str, cap: int = MAX_PDF_BYTES, robots: bool = True) -> Fetched:
        """GET url, following at most MAX_REDIRECTS redirects on the same
        site. Every hop passes url_problem, address_problem, robots.txt
        (unless this is the robots.txt itself) and the host's pace."""
        site = site_of(url)
        for hop in range(MAX_REDIRECTS + 1):
            why = url_problem(url)
            if not why and site_of(url) != site:
                why = (f"it is on another host ({clean(host_of(url), 80)}); cite the final "
                       "address instead")
            why = why or self.address_problem(url) or (self.allowed(url) if robots else "")
            if why:
                raise FetchError(why if hop == 0 else f"it redirects to an address not fetched: {why}")
            self.wait(url)
            status, headers, body = self.open_once(url, cap)
            if status in (301, 302, 303, 307, 308):
                target = headers.get("location", "")
                if not target.strip():
                    raise FetchError(f"HTTP {status} without an address to go to")
                try:
                    url = urllib.parse.urldefrag(urllib.parse.urljoin(url, target.strip()))[0]
                except ValueError:
                    raise FetchError(f"HTTP {status} to an unreadable address") from None
                continue
            return Fetched(body, headers.get("content-type", "").lower(), url, status)
        raise FetchError(f"more than {MAX_REDIRECTS} redirects")

    def wait(self, url: str) -> None:
        """Sleep until this host may be asked again (called before each request)."""
        host = host_of(url)
        delay = self._delay.get(host, HOST_DELAY_S)
        pause = delay - (self._clock() - self._last.get(host, -delay))
        if pause > 0:
            self._sleep(pause)
        self._last[host] = self._clock()

    def allowed(self, url: str) -> str:
        """'' when robots.txt allows the fetch, else why not."""
        parts = urllib.parse.urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        rules = self._robots.get(root)
        if rules is None:
            rules = self._read_robots(root)
            self._robots[root] = rules
        if isinstance(rules, str):
            return rules
        if not rules.allows(url):
            return "robots.txt disallows this page"
        if rules.delay > MAX_CRAWL_DELAY_S:
            return (f"robots.txt asks for {rules.delay:g} s between requests "
                    f"(over {MAX_CRAWL_DELAY_S:g} s): not fetched")
        self._delay[host_of(url)] = max(HOST_DELAY_S, rules.delay)
        return ""

    def _read_robots(self, root: str) -> Robots | str:
        try:
            got = self.fetch(root + "/robots.txt", cap=MAX_ROBOTS_BYTES, robots=False)
        except FetchError as exc:
            if exc.status in (404, 410):
                return Robots("")           # no robots.txt: no crawl rule applies
            return f"robots.txt could not be read ({exc})"
        return Robots(got.body.decode("utf-8-sig", "replace"))


def _pdf_units(path: Path) -> list[tuple[int, str]] | str:
    """Per PDF page, its text in layout order and in reading order; or why not."""
    tool = shutil.which("pdftotext")
    if tool is None:
        return "a PDF, and pdftotext is not installed: not verifiable here"
    units: list[tuple[int, str]] = []
    for mode in (["-layout"], []):
        try:
            out = subprocess.run([tool, *mode, str(path), "-"], capture_output=True,
                                 timeout=PDFTOTEXT_TIMEOUT_S, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"pdftotext failed ({type(exc).__name__})"
        if out.returncode != 0:
            return f"pdftotext failed (exit {out.returncode})"
        for number, text in enumerate(out.stdout.decode("utf-8", "replace").split("\f"), 1):
            text = normalise(text)
            if text:
                units.append((number, text))
    return units or "the PDF has no text (a scan without OCR?)"


def _charset(body: bytes, ctype: str) -> str:
    """The page's declared character set, if Python knows it; else UTF-8."""
    found = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", ctype) or re.search(
        r"charset\s*=\s*[\"']?([\w.:-]+)", body[:4096].decode("ascii", "replace"), re.I)
    if found:
        try:
            "".encode(found.group(1))
            return found.group(1)
        except LookupError:
            pass
    return "utf-8"


def load_page(url: str, pages: Path, fetcher: Fetcher) -> Page:
    """Fetch the page at url now, save a copy in pages/, return its text."""
    try:
        got = fetcher.fetch(url)
    except FetchError as exc:
        return Page(problem=f"could not be fetched: {exc}")
    is_pdf = got.body[:5] == b"%PDF-" or "pdf" in got.ctype
    path = pages / (page_name(url) + (".pdf" if is_pdf else ".html"))
    try:
        pages.mkdir(parents=True, exist_ok=True)
        for old in pages.glob(page_name(url) + ".*"):
            old.unlink()
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(got.body)
        tmp.replace(path)
    except OSError as exc:
        return Page(problem=f"could not be saved ({type(exc).__name__})")
    record = {
        "file": f"{PAGES_DIR}/{path.name}",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "final_url": got.final_url, "status": got.status, "bytes": len(got.body),
        "sha256": hashlib.sha256(got.body).hexdigest(),
    }
    if is_pdf:
        units = _pdf_units(path)
        if isinstance(units, str):
            return Page(problem=units, record=record)
        return Page(units=units, record=record)
    text = normalise(html_text(got.body.decode(_charset(got.body, got.ctype), "replace")))
    if not text:
        return Page(problem="the page has no text", record=record)
    return Page(units=[(0, text)], record=record)


def _wordlike(ch: str) -> bool:
    return ch.isalnum() or ch in "-_"


def find_quote(quote: str, page: Page) -> dict[str, Any] | None:
    """Where the quote sits on the page, between word boundaries: the text
    around it (and the PDF page), or None when it is not there."""
    nq = normalise(quote)
    for number, text in page.units:
        at = text.find(nq)
        while at != -1:
            end = at + len(nq)
            cut_front = _wordlike(nq[0]) and at > 0 and _wordlike(text[at - 1])
            cut_back = _wordlike(nq[-1]) and end < len(text) and _wordlike(text[end])
            if not cut_front and not cut_back:
                found: dict[str, Any] = {"context_before": text[max(0, at - CONTEXT_CHARS):at],
                                         "context_after": text[end:end + CONTEXT_CHARS]}
                if number:
                    found["pdf_page"] = number
                return found
            at = text.find(nq, at + 1)
    return None


def claim_problem(claim: Any) -> str:
    """What is wrong with a claim's shape ('' if nothing)."""
    if not isinstance(claim, dict):
        return "a claim is not an object"
    extra = sorted(set(claim) - CLAIM_KEYS)
    if extra:
        return f"a claim has a field that is not allowed ({clean(extra[0], 40)})"
    if not isinstance(claim.get("text"), str) or not claim["text"].strip():
        return "no claim text"
    why = url_problem(claim.get("source_url"))
    if why:
        return why
    quote = claim.get("quote")
    if not isinstance(quote, str) or len(normalise(quote)) < MIN_QUOTE_CHARS:
        return f"no quote of at least {MIN_QUOTE_CHARS} characters"
    if len(quote) > MAX_QUOTE_CHARS:
        return f"quote longer than {MAX_QUOTE_CHARS} characters"
    try:
        date.fromisoformat(str(claim.get("retrieved")))
    except ValueError:
        return "no retrieved date (YYYY-MM-DD)"
    if quote_is_from_url(quote, claim["source_url"]):
        return "the quote repeats words from its own source_url"
    return ""


@dataclass
class Result:
    checked: dict[str, Any]
    kept: int = 0
    dropped: list[dict[str, Any]] = field(default_factory=list)
    downgraded: list[str] = field(default_factory=list)


def _strings(value: Any, what: str) -> None:
    if value is not None and not (isinstance(value, list)
                                  and all(isinstance(v, str) for v in value)):
        raise Invalid(f"{what} is not a list of text")


def _header(doc: Any, keys: set[str], what: str) -> None:
    if not isinstance(doc, dict):
        raise Invalid(f"{what} is not a JSON object")
    extra = sorted(set(doc) - keys)
    if extra:
        raise Invalid(f"{what} has a field that is not allowed: {clean(extra[0], 40)!r} "
                      f"(allowed: {', '.join(sorted(keys))})")
    if doc.get("schema") != SCHEMA or isinstance(doc.get("schema"), bool):
        raise Invalid(f"{what}: schema must be {SCHEMA}")
    if not isinstance(doc.get("parcel_id"), str) or not doc["parcel_id"].strip():
        raise Invalid(f"{what}: no parcel_id")
    if "run" in doc and not isinstance(doc["run"], str):
        raise Invalid(f"{what}: run is not text")


def _body(body: Any, keys: set[str], verdicts: tuple[str, ...], what: str) -> None:
    if not isinstance(body, dict):
        raise Invalid(f"{what} is not an object")
    extra = sorted(set(body) - keys)
    if extra:
        raise Invalid(f"{what} has a field that is not allowed: {clean(extra[0], 40)!r} "
                      f"(allowed: {', '.join(sorted(keys))})")
    if body.get("verdict") not in verdicts:
        raise Invalid(f"{what}: verdict must be one of {', '.join(verdicts)}")
    if not isinstance(body.get("claims", []), list):
        raise Invalid(f"{what}: claims is not a list")
    _strings(body.get("open_items"), f"{what}: open_items")


def validate(findings: Any, skeptic_doc: Any = None) -> list[tuple[str, dict[str, Any]]]:
    """Refuse anything outside the schema; return (name, body) to check, the
    skeptic last. Unknown fields are refused, not passed on: the checked
    file must not carry text that only looks checked."""
    _header(findings, TOP_KEYS, FINDINGS_NAME)
    sections = findings.get("sections")
    if not isinstance(sections, dict) or not sections:
        raise Invalid("no sections")
    out = []
    for name, body in sections.items():
        if name not in SECTIONS:
            raise Invalid(f"section {clean(name, 40)!r} is not one of {', '.join(SECTIONS)}")
        _body(body, SECTION_KEYS, SECTIONS[name], name)
        out.append((name, body))
    if skeptic_doc is not None:
        _header(skeptic_doc, SKEPTIC_FILE_KEYS, SKEPTIC_NAME)
        if skeptic_doc["parcel_id"] != findings["parcel_id"]:
            raise Invalid(f"{SKEPTIC_NAME} is for another parcel")
        skeptic = skeptic_doc.get(SKEPTIC)
        _body(skeptic, SKEPTIC_KEYS, SKEPTIC_VERDICTS, SKEPTIC)
        if not isinstance(skeptic.get("objection"), str):
            raise Invalid("skeptic: objection is not text")
        _strings(skeptic.get("disputes"), "skeptic: disputes")
        ids = {f"{name}-{i}" for name, body in out
               for i in range(1, len(body.get("claims", [])) + 1)}
        unknown = sorted(set(skeptic.get("disputes") or []) - ids)
        if unknown:
            raise Invalid(f"skeptic: disputes names a claim that does not exist "
                          f"({clean(unknown[0], 40)})")
        out.append((SKEPTIC, skeptic))
    if sum(len(body.get("claims", [])) for _, body in out) > MAX_CLAIMS:
        raise Invalid(f"more than {MAX_CLAIMS} claims")
    return out


def check(findings: Any, pages: Path, fetcher: Fetcher, skeptic_doc: Any = None) -> Result:
    """Check every claim; return the checked copy and what was dropped."""
    sections = validate(findings, skeptic_doc)
    checked = json.loads(json.dumps(findings))
    if skeptic_doc is not None:
        checked[SKEPTIC] = json.loads(json.dumps(skeptic_doc[SKEPTIC]))
    result = Result(checked=checked)
    cache: dict[str, Page] = {}
    for name, body in sections:
        target = checked[SKEPTIC] if name == SKEPTIC else checked["sections"][name]
        kept = []
        for number, claim in enumerate(body.get("claims", []), 1):
            claim_id = f"{name}-{number}"
            why = claim_problem(claim)
            found = None
            if not why:
                url = urllib.parse.urldefrag(claim["source_url"])[0]
                if url not in cache and len(cache) >= MAX_URLS:
                    why = f"more than {MAX_URLS} different pages in one run"
                else:
                    if url not in cache:
                        try:
                            cache[url] = load_page(url, pages, fetcher)
                        except Exception as exc:     # one bad claim must not void the run
                            cache[url] = Page(problem=f"could not be read ({type(exc).__name__})")
                    page = cache[url]
                    if page.problem:
                        why = f"page {page.problem}"
                    else:
                        found = find_quote(claim["quote"], page)
                        if found is None:
                            why = "the quote is not on the page"
            if why or found is None:
                result.dropped.append({"id": claim_id, "section": name, "reason": why,
                                       "claim": claim if isinstance(claim, dict) else repr(claim)})
            else:
                kept.append({"id": claim_id, **claim, **found,
                             "page_file": cache[url].record["file"]})
        target["claims"] = kept
        result.kept += len(kept)
        if name == SKEPTIC:
            target["supported"] = bool(kept)
        elif not kept and body["verdict"] != UNKNOWN:
            target["verdict"] = UNKNOWN
            target["verdict_as_written"] = body["verdict"]
            result.downgraded.append(name)
    checked["pages"] = {url: page.record for url, page in cache.items() if page.record}
    checked["quotecheck"] = {
        "kept": result.kept, "dropped": len(result.dropped), "downgraded": result.downgraded,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "proves": ("each kept quote was on the page at its URL when this tool fetched it "
                   "(see pages); nothing more. Claim text, objections and open_items are the "
                   "agents' own words, not checked"),
    }
    checked["dropped_claims"] = result.dropped
    return result


def lead_problems(lead: str, checked: dict[str, Any]) -> list[str]:
    """What a lead.md says that the checked file does not back."""
    bodies = dict(checked.get("sections") or {})
    if isinstance(checked.get(SKEPTIC), dict):
        bodies[SKEPTIC] = checked[SKEPTIC]
    claims = [c for body in bodies.values() for c in body.get("claims", [])]
    quotes = [normalise(c["quote"]) for c in claims]
    urls = {c["source_url"] for c in claims}
    problems = []
    for passage in re.findall(r'"([^"]{%d,}?)"|“([^”]{%d,}?)”' % (LEAD_QUOTE_CHARS, LEAD_QUOTE_CHARS),
                              lead):
        words = normalise(passage[0] or passage[1])
        if len(words) >= LEAD_QUOTE_CHARS and not any(words in q for q in quotes):
            problems.append(f"quoted words that are in no kept quote: \"{clean(words, 70)}\"")
    for url in re.findall(r"\]\(\s*<?(https?://[^)\s>]+)", lead):
        if url not in urls:
            problems.append(f"a link that is no kept claim's address: {clean(url, 110)}")
    for name, body in bodies.items():
        allowed = SKEPTIC_VERDICTS if name == SKEPTIC else SECTIONS.get(name, ())
        said = [v for v in re.findall(r"(?i)\b%s\b[^\n`]{0,40}`([a-z_]+)`" % re.escape(name), lead)
                if v in allowed]
        if not said:
            problems.append(f"no verdict given for {name} (write it as {name}: `{body.get('verdict')}`)")
        elif any(v != body.get("verdict") for v in said):
            problems.append(f"{name}: lead.md says `{said[0]}`, the checked file says "
                            f"`{body.get('verdict')}`")
    return problems


def _read_json(path: Path) -> Any:
    def no_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("a field is given twice")
        return dict(pairs)

    def no_constant(name: str) -> Any:
        raise ValueError(f"{name} is not a JSON value")

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_repeats,
                          parse_constant=no_constant)
    except (OSError, ValueError, RecursionError) as exc:
        raise Invalid(f"{path.name} is unreadable ({clean(exc, 120)})") from exc


def main(argv: list[str] | None = None, fetcher: Fetcher | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("findings", type=Path, help="a lead's findings.json")
    ap.add_argument("--lead", type=Path, metavar="LEAD_MD",
                    help="also check this lead.md against the checked file")
    args = ap.parse_args(argv)
    path: Path = args.findings
    out = path.with_name(CHECKED_NAME)
    try:
        out.unlink(missing_ok=True)     # a failed run must not leave an old result
    except OSError as exc:
        print(f"refused: the old {CHECKED_NAME} could not be removed ({type(exc).__name__})",
              file=sys.stderr)
        return 2
    try:
        if path.name != FINDINGS_NAME:
            raise Invalid(f"the file to check is named {FINDINGS_NAME}")
        findings = _read_json(path)
        skeptic_path = path.with_name(SKEPTIC_NAME)
        skeptic_doc = _read_json(skeptic_path) if skeptic_path.exists() else None
        result = check(findings, path.parent / PAGES_DIR, fetcher or Fetcher(), skeptic_doc)
    except Invalid as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    tmp = out.with_suffix(".json.part")
    tmp.write_text(json.dumps(result.checked, indent=1, allow_nan=False), encoding="utf-8")
    tmp.replace(out)
    print(f"{result.kept} claims kept, {len(result.dropped)} dropped"
          + (f"; verdict set to unknown for: {', '.join(result.downgraded)}"
             if result.downgraded else ""))
    if skeptic_doc is not None:
        backed = "has" if result.checked[SKEPTIC]["supported"] else "has NO"
        print(f"skeptic ({SKEPTIC_NAME}): {result.checked[SKEPTIC]['verdict']}; it {backed} "
              "kept quote of its own")
    for d in result.dropped:
        print(f"  dropped ({d['id']}): {clean(d['reason'])}")
    print(f"written: {out}")
    if args.lead is None:
        return 0
    try:
        problems = lead_problems(args.lead.read_text(encoding="utf-8"), result.checked)
    except (OSError, ValueError) as exc:
        print(f"refused: {args.lead.name} is unreadable ({type(exc).__name__})", file=sys.stderr)
        return 2
    for p in problems:
        print(f"  {args.lead.name}: {p}")
    print(f"{args.lead.name}: {len(problems)} problems" if problems
          else f"{args.lead.name}: agrees with the checked file")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
