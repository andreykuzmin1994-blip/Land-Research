"""Check an investigation's findings against the pages they cite, and write
the lead's reading file (reviews/35_screener_lead_layer; the rules are in
INVESTIGATE.md).

    python -m screener.quotecheck screener/leads/<parcel>/findings.json
    python -m screener.quotecheck screener/leads/<parcel>/findings.json --with-skeptic --findings-sha256 <hash>

An investigating agent writes ``findings.json``: per checklist section a
verdict and claims, each claim with the URL it rests on and an exact quote
from that page. A skeptic agent later writes ``skeptic.json`` beside it.
This tool, not an agent, fetches each URL and keeps a claim only if its
quote is on the page. It writes ``findings.checked.json``:

- a claim whose quote is not on its page, whose page could not be
  fetched, or that is malformed is dropped, with the reason;
- a section left with no kept claim has its verdict set to ``unknown``,
  whatever the agent wrote: a verdict with no surviving evidence is not a
  finding. A skeptic with no kept claim is marked ``supported: false``;
- each fetched page is recorded (when, final address, size, SHA-256), and
  so are the SHA-256 of the input files.

It also writes ``lead.md`` from the checked file. Nobody writes that file
by hand: in it, every passage in double quotation marks is a kept quote
and every link is a kept claim's address, because this code put them
there. A kept quote is printed as an inert code span. The agents' own
words (claim text, objections, open items, and the launching session's
``--headline``) are labelled as theirs and reduced to letters, digits and
plain punctuation, with web addresses taken out.

The two runs. The first run (no flag) is for the investigator's file
alone and refuses when a ``skeptic.json`` is already there. It prints the
SHA-256 of ``findings.json``. The run after the skeptic takes
``--with-skeptic`` and that hash, and refuses if ``findings.json`` has
changed since: the skeptic's disputes name claims by position.

Every run fetches every page again. A copy is saved in ``pages/`` beside
the findings as a record; a file already there is never used as the page
(an agent can write in that folder). The old ``findings.checked.json`` and
``lead.md`` are removed before anything else, so a failed run leaves
neither. Output files are created new and never written through a link.

What it proves is narrow: the quoted words were on the page at that URL
when this tool fetched it. It does not prove the page is right, current
or authoritative, or that the claim follows from the quote. That is the
skeptic's job and then a person's. Claim text, objections and open items
are the agents' own words and are not checked.

What a kept quote must pass (reviews/35 .../06 and 07):
- the quote is not made of words from its own URL (a page that echoes its
  request would otherwise let the agent write the "page"): 25 characters
  in a row anywhere in the address, or three words in a row or one long
  word from its query part, drops the claim. This cannot be made airtight
  (a query can name an output field in two short words, or split words
  across parameters), so each kept claim lists the words it shares with
  the query part of its address for the skeptic to weigh;
- it sits on the page between word boundaries ("allowed" is not found in
  "disallowed", nor "5 feet" in "21.5 feet"), and the 80 characters on
  each side are saved with the claim, so a cut "not" shows;
- form fields, drop-down lists, templates and the page title do not count
  as page text; nor do scripts, styles or comments. Only a reply that says
  it is HTML or XML is read as markup; anything else is plain text;
- in a PDF the quote must be inside one page. Reading order is tried
  first; a quote found only in layout order (which can read across
  columns) is kept and marked ``pdf_order: layout``.
Known and not closed: text hidden by a style sheet, struck-through text
and invisible PDF text still count as page text.

Fetching rules: GET only, http(s) only, no cookies, no sign-ins. Never an
address that names CoStar, LoopNet or Crexi anywhere in it (their terms
prohibit automated use), so not through an archive, cache or reader
either. (A short link or an encoded address that hides the name cannot be
seen here; redirects off the cited host are not followed.) Only public
internet addresses. robots.txt is read first and honoured (wildcards
included); a robots.txt that cannot be read (other than "not found")
means the page is not fetched, a 403 included. At least one second
between requests to a site, or the host's own Crawl-delay when it asks
for more (a host asking for more than 30 seconds is not fetched).
Redirects are followed by hand, at most five, each hop under all of these
rules; a redirect to another host is not followed (cite that address
instead). Size caps. The screener's own User-Agent. PDFs are read with
the ``pdftotext`` command when it is installed and are otherwise not
verifiable (the claim is dropped and says so).

Offline in tests: every request goes through ``Fetcher.open_once``, which
tests replace. Stdlib only.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import http.client
import ipaddress
import json
import os
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
LEAD_NAME = "lead.md"
MAX_URL_CHARS = 2000
DECODE_ROUNDS = 10               # an address still changing after this many is refused
# What a parcel id and a run name may look like: they go into lead.md's title.
PARCEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}")
PAGES_DIR = "pages"
MIN_QUOTE_CHARS = 25
MAX_QUOTE_CHARS = 1200
MAX_PDF_BYTES = 200_000_000      # adopted comprehensive plans run past 150 MB
MAX_HTML_BYTES = 10_000_000      # anything that is not a PDF
MAX_ROBOTS_BYTES = 500_000
MAX_CLAIMS = 200                 # the investigator's
MAX_URLS = 60                    # distinct pages of the investigator's claims in one run
MAX_SKEPTIC_CLAIMS = 60          # the skeptic's own allowance, so the first pass
MAX_SKEPTIC_URLS = 40            # cannot use it up
MAX_HEADLINE_CHARS = 400          # of the launching session's --headline
# lead.md is a page to read, not the record: how much of the agents' words it carries.
LEAD_TEXT_CHARS = 280
LEAD_CONTEXT_CHARS = 45
LEAD_SKEPTIC_CLAIMS = 2
LEAD_OPEN_ITEMS = 8
MAX_REDIRECTS = 5
HOST_DELAY_S = 1.0
MAX_CRAWL_DELAY_S = 30.0         # a host asking for more is not fetched
TIMEOUT_S = 60
PDFTOTEXT_TIMEOUT_S = 300
CONTEXT_CHARS = 80
ECHO_WORDS = 3                   # this many quote words in a row in the address: an echo
ECHO_TOKEN_CHARS = 12            # or one word this long (with 8+ letters)
ECHO_SLUG_CHARS = 40             # or this much of the quote's words, whatever separates them
# Path words that mark a page built from the request (a search or results page).
REQUEST_PATH_WORDS = frozenset({"search", "query", "find", "results", "lookup"})
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
# Tags that sit inside a word or a sentence: no space is put where they are,
# so "dis<b>allowed</b>" stays one word.
INLINE_TAGS = ("a", "abbr", "acronym", "b", "bdi", "bdo", "big", "cite", "code", "data", "del",
               "dfn", "em", "font", "i", "ins", "kbd", "label", "mark", "nobr", "q", "s", "samp",
               "small", "span", "strike", "strong", "sub", "sup", "time", "tt", "u", "var", "wbr")
# The character sets a page may declare; anything else is read as UTF-8.
CHARSETS = {"utf-8": "utf-8", "utf8": "utf-8", "windows-1252": "cp1252", "cp1252": "cp1252",
            "iso-8859-1": "cp1252", "latin-1": "cp1252", "latin1": "cp1252",
            "us-ascii": "utf-8", "ascii": "utf-8", "utf-16": "utf-16"}


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
    hops: list[str] = field(default_factory=list)   # every address asked, the first included


@dataclass
class Page:
    # (PDF page or 0, "reading" / "layout" / "", normalised text)
    units: list[tuple[int, str, str]] = field(default_factory=list)
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
        if tag not in INLINE_TAGS and "-" not in tag:      # custom elements are inline
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS and self._skip:
            self._skip -= 1
        if tag not in INLINE_TAGS and "-" not in tag:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


_PUNCT = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2212": "-",
                        "\u2027": "-", "\u2043": "-",
                        **{chr(c): "-" for c in range(0x2010, 0x2016)}})
# Control and format characters other than whitespace: soft hyphens,
# zero-width characters, direction marks, terminal escapes, surrogates and
# Unicode tag characters. Written as escapes: they are invisible in a file.
_INVISIBLE = re.compile("[\u0000-\u0008\u000e-\u001f\u007f-\u009f\u00ad\u061c\u180e"
                        "\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff"
                        "\u034f\u115f\u1160\u2800\u3164\ufe00-\ufe0f\uffa0"
                        "\ud800-\udfff\U000e0000-\U000e007f\U000e0100-\U000e01ef]")


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
    """The address with percent-escapes undone until nothing changes
    (DECODE_ROUNDS at most), HTML entities undone, then normalised: soft hyphens,
    zero-width characters and look-alike letters do not hide a name."""
    out = url
    for _ in range(DECODE_ROUNDS):
        again = html.unescape(urllib.parse.unquote_plus(out))
        if again == out:
            break
        out = again
    return normalise(out)


def _settles(url: str) -> bool:
    """Does the address stop changing within DECODE_ROUNDS of decoding?"""
    out = url
    for _ in range(DECODE_ROUNDS):
        again = html.unescape(urllib.parse.unquote_plus(out))
        if again == out:
            return True
        out = again
    return False


def url_problem(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        return "no source_url"
    if url != url.strip() or _INVISIBLE.search(url) or re.search(r"\s", url):
        return "source_url has spaces or control characters"
    if len(url) > MAX_URL_CHARS:
        return f"source_url longer than {MAX_URL_CHARS} characters"
    if not _settles(url):
        return "source_url is encoded over and over: write the plain address"
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


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text)


def _query_words(url: str) -> tuple[list[str], list[str]]:
    """The words of the address's query and fragment, in order; and of the
    query's values alone, in order (a page may join ?a=..&b=..&c=.. into
    one sentence). This is the part a query page shows back."""
    try:
        parts = urllib.parse.urlsplit(url)
        values = " ".join(v for _, v in urllib.parse.parse_qsl(parts.query))
    except ValueError:
        return [], []
    return _tokens(_decoded(parts.query + " " + parts.fragment)), _tokens(_decoded(values))


_REQUEST = "(?:" + "|".join(sorted(REQUEST_PATH_WORDS)) + ")"
REQUEST_SEGMENT = re.compile(_REQUEST + r"(?:[-_](?:" + _REQUEST[3:-1] + r"|page|for))*"
                             r"(?:\.[a-z0-9]{1,6})*")
DOCUMENT_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".rtf", ".odt")


def _path_words(url: str, document_names: bool = True) -> tuple[list[str], list[str]]:
    """The words of the address's path; and the words in it that look typed.
    Typed: the segments after a segment that names a request
    ("/search/<words>", "/search-results.php/<words>": REQUEST_SEGMENT), and
    a segment with an encoded space of any kind unless it is the file name of a document
    ("Article%2012%20Light%20Industrial.pdf"). A request word inside a
    slug ("hearing-results") or as the last segment (ArcGIS ".../query")
    types nothing."""
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return [], []
    segments = [seg for seg in path.split("/") if seg]
    typed: list[str] = []
    after_request = False
    for n, seg in enumerate(segments):
        words = _tokens(_decoded(seg))
        # Any encoded space: +, %20, %09, %C2%A0, %E3%80%80, %2B (reviews/35 13, F4).
        # _decoded turns a raw "+" into a space and leaves a decoded one.
        spaced = " " in _decoded(seg) or "+" in _decoded(seg)
        document = (document_names and n == len(segments) - 1
                    and _decoded(seg).endswith(DOCUMENT_SUFFIXES))
        if after_request or (spaced and not document):
            typed += words
        # A request segment is request words joined by - or _ (with "page" or
        # "for"), then any file extension: search, search.php, search.json,
        # search-results, search-page, search;jsessionid=1 (also encoded).
        # Not a slug that begins with one ("find-your-zoning": reviews/35 13
        # F3, 14 F3).
        if REQUEST_SEGMENT.fullmatch(_decoded(seg).split(";", 1)[0].strip(" /")):
            after_request = True
    return _tokens(_decoded(path)), typed


def _shared_runs(words: list[str], seq: list[str]) -> list[int]:
    """Where in `words` a run of ECHO_WORDS of them, two of four letters or
    more, also stands in `seq` in the same order."""
    runs = {tuple(seq[i:i + ECHO_WORDS]) for i in range(len(seq) - ECHO_WORDS + 1)}
    return [i for i in range(len(words) - ECHO_WORDS + 1)
            if tuple(words[i:i + ECHO_WORDS]) in runs
            and sum(1 for w in words[i:i + ECHO_WORDS] if sum(ch.isalpha() for ch in w) >= 4) >= 2]


def words_shared_with_url(quote: str, url: str) -> list[str]:
    """The quote's words of four or more letters that are also in the query
    part of its address (after the "?"), or in a run of ECHO_WORDS words it
    shares with the path, in the quote's order. Saved with a kept claim for
    the skeptic, and flagged in lead.md."""
    words = _tokens(normalise(quote))
    shared = set(_query_words(url)[0])
    path, _ = _path_words(url)
    for i in _shared_runs(words, path):
        shared.update(words[i:i + ECHO_WORDS])
    seen: list[str] = []
    for word in words:
        if sum(ch.isalpha() for ch in word) >= 4 and word in shared and word not in seen:
            seen.append(word)
    return seen


def quote_is_from_url(quote: str, url: str, document_names: bool = True) -> bool:
    """Is the quote made of its own address? True when the address holds
    MIN_QUOTE_CHARS in a row of the quote anywhere, or ECHO_SLUG_CHARS of
    the quote's words whatever separates them ("_", "-", "+", "."); or when
    its query part holds ECHO_WORDS of the quote's words in a row (two of
    them of four letters or more) or one of its words of ECHO_TOKEN_CHARS or
    more with eight letters or more. A page that shows its request back
    would show them. The path is held to the same run of words only where it
    looks typed (_path_words: an encoded space, or what follows a "search"
    or "results" segment): a
    document's file name often shares words with the document
    ("2045-Comprehensive-Plan-Update.pdf"), so there a shared run is flagged,
    not dropped (words_shared_with_url)."""
    nq = normalise(quote)
    if any(nq[i:i + MIN_QUOTE_CHARS] in form
           for form in (_decoded(url), normalise(urllib.parse.unquote(url)))
           for i in range(max(1, len(nq) - MIN_QUOTE_CHARS + 1))):
        return True
    words = _tokens(nq)
    joined, in_url = " ".join(words), " ".join(_tokens(_decoded(url)))
    if len(joined) >= ECHO_SLUG_CHARS and any(
            joined[i:i + ECHO_SLUG_CHARS] in in_url
            for i in range(len(joined) - ECHO_SLUG_CHARS + 1)):
        return True
    in_query, values = _query_words(url)
    if any(len(w) >= ECHO_TOKEN_CHARS and sum(ch.isalpha() for ch in w) >= 8 and w in in_query
           for w in words):
        return True
    if _shared_runs(words, in_query) or _shared_runs(words, values):
        return True
    return bool(_shared_runs(words, _path_words(url, document_names)[1]))


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
            if key not in ("allow", "disallow", "crawl-delay"):
                continue                      # Sitemap and the like: not part of any group
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

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        """Does a robots pattern match from the start of the path? `*` is any
        run of characters, a closing `$` the end of the path. Matched piece
        by piece, leftmost first, so a long rule cannot stall the run."""
        anchored = pattern.endswith("$")
        pieces = (pattern[:-1] if anchored else pattern).split("*")
        if not path.startswith(pieces[0]):
            return False
        at = len(pieces[0])
        if len(pieces) == 1:
            return at == len(path) if anchored else True
        for piece in pieces[1:-1]:
            found = path.find(piece, at)
            if found == -1:
                return False
            at = found + len(piece)
        last = pieces[-1]
        if anchored:
            return len(path) - len(last) >= at and path.endswith(last)
        return path.find(last, at) != -1

    def allows(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        path = self._norm((parts.path or "/") + ("?" + parts.query if parts.query else ""))
        best = (-1, True)
        for allow, pattern in self.rules:
            pat = self._norm(pattern)
            if self._matches(pat, path):
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
                declared = headers.get("content-length", "").strip()
                if declared.isdigit() and len(body) < int(declared):
                    raise FetchError("the reply was cut short")
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
        asked: list[str] = []
        for hop in range(MAX_REDIRECTS + 1):
            why = url_problem(url)
            if not why and site_of(url) != site:
                why = "it is on another host; cite the final address instead"
            why = why or self.address_problem(url) or (self.allowed(url) if robots else "")
            if why:
                raise FetchError(why if hop == 0 else f"it redirects to an address not fetched: {why}")
            self.wait(url)
            asked.append(url[:2000])
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
            return Fetched(body, headers.get("content-type", "").lower(), url[:2000], status, asked)
        raise FetchError(f"more than {MAX_REDIRECTS} redirects")

    def wait(self, url: str) -> None:
        """Sleep until this host may be asked again (called before each request)."""
        site = site_of(url)                   # www.x and x are one site to pace
        delay = self._delay.get(site, HOST_DELAY_S)
        pause = delay - (self._clock() - self._last.get(site, -delay))
        if pause > 0:
            self._sleep(pause)
        self._last[site] = self._clock()

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
        site = site_of(url)
        self._delay[site] = max(HOST_DELAY_S, rules.delay, self._delay.get(site, 0.0))
        return ""

    def _read_robots(self, root: str) -> Robots | str:
        try:
            got = self.fetch(root + "/robots.txt", cap=MAX_ROBOTS_BYTES, robots=False)
        except FetchError as exc:
            if exc.status in (404, 410):
                return Robots("")           # no robots.txt: no crawl rule applies
            return f"robots.txt could not be read ({exc})"
        return Robots(got.body.decode("utf-8-sig", "replace"))


def _pdf_units(path: Path) -> list[tuple[int, str, str]] | str:
    """Per PDF page, its text in reading order and then in layout order
    (which keeps tables but can read across columns); or why not."""
    tool = shutil.which("pdftotext")
    if tool is None:
        return "a PDF, and pdftotext is not installed: not verifiable here"
    units: list[tuple[int, str, str]] = []
    for order, mode in (("reading", []), ("layout", ["-layout"])):
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
                units.append((number, order, text))
    return units or "the PDF has no text (a scan without OCR?)"


def _charset(body: bytes, ctype: str, markup: bool) -> str:
    """The character set to read a page in: the reply header's, or (markup
    only) a <meta> tag's, when it is one of CHARSETS; else UTF-8. Never a
    name found loose in the text: a page that echoes its request could
    otherwise choose how its own bytes are read."""
    found = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", ctype)
    if not found and markup:
        found = re.search(r"<meta[^>]{0,200}?charset\s*=\s*[\"']?([\w.:-]+)",
                          body[:4096].decode("ascii", "replace"), re.I)
    return CHARSETS.get(found.group(1).lower(), "utf-8") if found else "utf-8"


def _safe_write(path: Path, data: bytes) -> None:
    """Create `path` new with `data`. Whatever had that name (a link an agent
    left there included) is removed, never written through."""
    tmp = path.with_name(path.name + ".part")
    for old in (tmp, path):
        if old.is_symlink() or old.exists():
            old.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def load_page(url: str, pages: Path, fetcher: Fetcher) -> Page:
    """Fetch the page at url now, save a copy in pages/, return its text."""
    try:
        got = fetcher.fetch(url)
    except FetchError as exc:
        return Page(problem=f"could not be fetched: {exc}")
    is_pdf = b"%PDF-" in got.body[:1024] or "pdf" in got.ctype
    path = pages / (page_name(url) + (".pdf" if is_pdf else ".html"))
    try:
        if pages.is_symlink():
            return Page(problem="could not be saved (pages is a link, not a folder)")
        pages.mkdir(parents=True, exist_ok=True)
        for old in pages.glob(page_name(url) + ".*"):
            old.unlink()
        _safe_write(path, got.body)
    except OSError as exc:
        return Page(problem=f"could not be saved ({type(exc).__name__})")
    record = {
        "file": f"{PAGES_DIR}/{path.name}",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "final_url": clean(got.final_url, 2000), "status": got.status, "bytes": len(got.body),
        "sha256": hashlib.sha256(got.body).hexdigest(),
    }
    if len(got.hops) > 1:      # the echo rules are run on every address asked
        record["redirected_from"] = [clean(h, 2000) for h in got.hops[:-1]]
    if is_pdf:
        units = _pdf_units(path)
        if isinstance(units, str):
            return Page(problem=units, record=record)
        return Page(units=units, record=record)
    # Markup only when the reply says so: a JSON or text reply is read as it
    # is, so "&#101;" in it is not turned into a letter.
    markup = "html" in got.ctype or "xml" in got.ctype
    raw = got.body.decode(_charset(got.body, got.ctype, markup), "replace")
    text = normalise(html_text(raw) if markup else raw)
    if not text:
        return Page(problem="the page has no text", record=record)
    return Page(units=[(0, "", text)], record=record)


def _wordlike(ch: str) -> bool:
    return ch.isalnum() or ch in "-_"


def _cut(quote_edge: str, beside: str, beyond: str) -> bool:
    """Does the quote stop inside a word or a number? quote_edge is the
    quote's first (or last) character, beside the page character next to
    it, beyond the one after that."""
    if not _wordlike(quote_edge):
        return False
    if _wordlike(beside):
        return True
    return quote_edge.isdigit() and beside in ".," and beyond.isdigit()   # 21.5, 2,500,000


def find_quote(quote: str, page: Page) -> dict[str, Any] | None:
    """Where the quote sits on the page, between word boundaries: the text
    around it (and the PDF page and text order), or None when it is not
    there."""
    nq = normalise(quote)
    texts = {(number, order): text for number, order, text in page.units}
    for number, order, text in page.units:
        at = text.find(nq)
        while at != -1:
            end = at + len(nq)
            front = _cut(nq[0], text[at - 1] if at else " ", text[at - 2] if at > 1 else " ")
            back = _cut(nq[-1], text[end] if end < len(text) else " ",
                        text[end + 1] if end + 1 < len(text) else " ")
            if not front and not back:
                before = text[max(0, at - CONTEXT_CHARS):at]
                if number and len(before) < CONTEXT_CHARS and (number - 1, order) in texts:
                    # The end of the PDF page before: a "not" there still shows.
                    tail = texts[(number - 1, order)][-(CONTEXT_CHARS - len(before)):]
                    before = f"{tail} (page break) {before}"
                after = text[end:end + CONTEXT_CHARS]
                if number and len(after) < CONTEXT_CHARS and (number + 1, order) in texts:
                    after = f"{after} (page break) {texts[(number + 1, order)][:CONTEXT_CHARS - len(after)]}"
                found: dict[str, Any] = {"context_before": before, "context_after": after}
                if number:
                    found["pdf_page"], found["pdf_order"] = number, order
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
    if type(doc.get("schema")) is not int or doc["schema"] != SCHEMA:
        raise Invalid(f"{what}: schema must be {SCHEMA}")
    if not isinstance(doc.get("parcel_id"), str) or not doc["parcel_id"].strip():
        raise Invalid(f"{what}: no parcel_id")
    if not PARCEL_ID.fullmatch(doc["parcel_id"]):
        raise Invalid(f"{what}: parcel_id is not a parcel id (letters, digits, spaces, . _ -)")
    if "run" in doc and not (isinstance(doc["run"], str) and RUN_NAME.fullmatch(doc["run"])):
        raise Invalid(f"{what}: run is not a run folder name")


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
        if len(skeptic.get("claims", [])) > MAX_SKEPTIC_CLAIMS:
            raise Invalid(f"skeptic: more than {MAX_SKEPTIC_CLAIMS} claims")
        out.append((SKEPTIC, skeptic))
    if sum(len(body.get("claims", [])) for name, body in out if name != SKEPTIC) > MAX_CLAIMS:
        raise Invalid(f"more than {MAX_CLAIMS} claims")
    return out


def _addresses(page: Page) -> list[str]:
    """Every address the fetch asked after the cited one, the final one last."""
    hops = page.record.get("redirected_from", [])[1:]
    return hops + ([page.record["final_url"]] if page.record.get("final_url") else [])


def check(findings: Any, pages: Path, fetcher: Fetcher, skeptic_doc: Any = None) -> Result:
    """Check every claim; return the checked copy and what was dropped."""
    sections = validate(findings, skeptic_doc)
    checked = json.loads(json.dumps(findings))
    if skeptic_doc is not None:
        checked[SKEPTIC] = json.loads(json.dumps(skeptic_doc[SKEPTIC]))
    result = Result(checked=checked)
    cache: dict[str, Page] = {}
    asked: dict[bool, set[str]] = {False: set(), True: set()}    # is the skeptic's -> its pages
    for name, body in sections:
        target = checked[SKEPTIC] if name == SKEPTIC else checked["sections"][name]
        kept = []
        for number, claim in enumerate(body.get("claims", []), 1):
            claim_id = f"{name}-{number}"
            why = claim_problem(claim)
            found = None
            if not why:
                url = urllib.parse.urldefrag(claim["source_url"])[0]
                mine = asked[name == SKEPTIC]
                most = MAX_SKEPTIC_URLS if name == SKEPTIC else MAX_URLS
                if url not in mine and len(mine) >= most:
                    why = f"more than {most} different pages in one run"
                else:
                    mine.add(url)
                    if url not in cache:
                        try:
                            cache[url] = load_page(url, pages, fetcher)
                        except Exception as exc:     # one bad claim must not void the run
                            cache[url] = Page(problem=f"could not be read ({type(exc).__name__})")
                    page = cache[url]
                    if page.problem:
                        why = f"page {page.problem}"
                    elif any(quote_is_from_url(claim["quote"], hop) for hop in _addresses(page)):
                        why = "the quote repeats words from an address the request was redirected to"
                    elif not page.record.get("file", "").endswith(".pdf") and any(
                            quote_is_from_url(claim["quote"], a, document_names=False)
                            for a in [claim["source_url"], *_addresses(page)]):
                        # A ".pdf" name that did not answer with a PDF is no
                        # document's file name (reviews/35 12, F2).
                        why = "the quote repeats words from its own source_url"
                    else:
                        found = find_quote(claim["quote"], page)
                        if found is None:
                            why = "the quote is not on the page"
            if why or found is None:
                result.dropped.append({"id": claim_id, "section": name, "reason": why,
                                       "claim": claim if isinstance(claim, dict) else repr(claim)})
            else:
                shared: list[str] = []
                for address in [claim["source_url"], *_addresses(cache[url])]:
                    shared += [w for w in words_shared_with_url(claim["quote"], address)
                               if w not in shared]
                kept.append({"id": claim_id, **claim, **found,
                             "words_shared_with_url": shared,
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


# Every character that can pass for a quotation mark becomes an apostrophe.
_QUOTE_MARKS = {ord(ch): "'" for ch in (
    '"`\u00ab\u00bb\u02ba\u02dd\u02ee\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f\u2032\u2033'
    '\u2034\u2035\u2036\u2037\u2039\u203a\u275b\u275c\u275d\u275e\u276e\u276f\u2e42\u301d\u301e'
    '\u301f\uff02\uff07')}
_PLAIN_KEPT = " .,;:!?()'-/%$+=\u00a7"
_LABEL = r"[^\W_](?:[^\W_]|-)*"
_HOST = _LABEL + r"(?:\." + _LABEL + r")+"                   # dotted, any script
# A host name ends in a label of letters (or punycode): "4.2.1.3", "1.1" and
# "S.E.1" are numbers, not hosts.
_NAMED_HOST = r"(?:" + _LABEL + r"\.)+(?:[^\W\d_]{2,}|xn--[a-z0-9-]+)"
_ADDRESS = re.compile(
    r"(?i)\S*:/\s*/\S*"                                  # any scheme, whatever stands before it
    r"|\S*[/\\]\s*[/\\]\s*" + _HOST + r"\S*"            # //host or \\host, spaced schemes too
    r"|\S*www\s*\.\s*\S+"
    # a numeric host with a port, query or fragment, or a path that is no
    # second dotted number; the hex form
    r"|\b\d{1,3}(?:\.\d{1,3}){3}(?:[:?#]|/(?!\d+(?:\.\d+)+\b))\S*"
    r"|\b0x[0-9a-f]+(?:\.0x[0-9a-f]+){1,3}\S*"
    # IPv6: a "::" form standing alone, or all eight groups
    r"|(?<![\w:])(?=[0-9a-f:]*::)[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7}(?![\w:])"
    r"|\b(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}\b"
    # a scheme word with a colon and anything but a space straight after it
    # ("https:3405803785/x", "data:text/html,...", "javascript:...")
    r"|\b(?:https?|ftps?|sftp|file|data|javascript|vbscript|mailto|tel|wss?|blob|about)\b:\S+"
    # a named host after a scheme word and a colon or slash, with spaces the
    # reduction left ("https: evil.example"); not "HTTP 1.1", "FTP: Sec.4.2",
    # "the www St.Louis office" (reviews/35 17)
    r"|\b(?:https?|ftps?|www)\b\s*[:/][\s:/]*" + _NAMED_HOST + r"\S*"
    # localhost, and a long number or hex number used as a host
    r"|\blocalhost\b[:/]\S*|\b(?:\d{8,}|0x[0-9a-f]{6,})[/:]\S*")
# Any other dotted word ("clerk.zone", "M-2.No", "St.Louis/Kansas") is split
# at a dot before a letter instead of removed: every word stays, and nothing
# can be linked or pasted as an address, though "clerk. zone" can still be
# read as one (reviews/35 15, 16). A dot before a digit stays: "4.2.1.3".
_DOTTED = re.compile(r"(?<=[^\W_])\.(?=[^\W\d_])")
# Latin letters that are no ASCII letter plus marks, and look like no
# quotation mark: kept, so names and streets read right.
_LATIN_KEPT = frozenset("\u0141\u0142\u00df\u1e9e\u00d8\u00f8\u00c6\u00e6\u0110\u0111\u00d0\u00f0"
                        "\u00de\u00fe\u0152\u0153\u0126\u0127\u0131\u014a\u014b")
_FRACTIONS = re.compile("(?<=\\d)(?=[\u00bc-\u00be\u2150-\u215f\u2189])")


def _kept_char(ch: str) -> bool:
    """ASCII letters and digits, Latin letters with marks, plain punctuation.
    Other scripts go: their letters include look-alike quotation marks
    (U+02BB, U+1425, U+05F2) and right-to-left text."""
    if ch.isascii() or ch in _PLAIN_KEPT:
        return ch.isalnum() or ch in _PLAIN_KEPT
    if ch in _LATIN_KEPT:
        return True
    # A Latin letter that is an ASCII letter plus marks ("e" + accent). The
    # Saltillo and click letters are Latin too, and look like quotation marks.
    base = unicodedata.normalize("NFKD", ch)[0]
    return ch.isalpha() and base.isascii() and base.isalpha()


def _plain(text: Any, limit: int = 600, keep_end: bool = False) -> str:
    """An agent's (or a page's) words made safe to sit in lead.md: one line
    of letters, digits and plain punctuation. No quotation marks but the
    apostrophe, no markup, no entities, and nothing that can be linked or
    pasted as an address: an address with a scheme, //, www., localhost,
    or a numeric, long-integer, hex or IPv6 host is removed; a named host
    after a scheme word and a colon or slash is removed; any other dotted
    word is split at a dot before a letter ("clerk. zone"), which a person
    can still read. A dotted number ("203.0.113.9", "4.2.1.3") stays: it
    cannot be told from a section number. Characters are reduced first and
    addresses removed after, so nothing removed can leave an address behind. In lead.md only this code writes quotation
    marks and links."""
    raw = str(text)
    if keep_end:
        raw = raw[-limit * 4:]
    t = html.unescape(html.unescape(clean(raw, limit * 4)))       # &quot; and &amp;quot;
    t = t.replace("\\", "/")                     # https:\\host\\x reads as an address
    t = _FRACTIONS.sub(" ", t)                    # 1\u00bd reads 1 1/2, not 11/2
    t = unicodedata.normalize("NFKC", clean(t, limit * 4)).translate(_QUOTE_MARKS)
    t = t.replace("\u2044", "/")
    t = t.replace("@", " at ").replace("&", " and ")
    t = "".join(ch if _kept_char(ch) else " " for ch in t)
    for _ in range(5):
        again = _ADDRESS.sub("(address removed)", t)
        if again == t:
            break
        t = again
    t = _DOTTED.sub(". ", t)
    t = re.sub(r"'{2,}", "'", re.sub(r"\s+", " ", t)).strip()
    if len(t) <= limit:
        return t
    if keep_end:          # the words just before a quote: keep the last ones, whole
        return t[-limit:].split(" ", 1)[-1] if " " in t[-limit:] else t[-limit:]
    return t[:limit] + " (cut)"


def _link(url: str) -> str:
    """A kept claim's address as an autolink; nothing in it can close the link."""
    # The fragment is never sent, so it is the agent's text, not the page's.
    url = urllib.parse.urldefrag(url)[0]
    return "<" + urllib.parse.quote(url, safe=":/?#@!$&'*+,;=%~-._") + ">"


def _code(text: str) -> str:
    """Page words as an inert code span: links, markup and entities in them
    are shown, not acted on. In the compatibility form, so look-alike
    letters and digits read as what they matched."""
    t = clean(unicodedata.normalize("NFKC", text), MAX_QUOTE_CHARS + 100)
    fence = "`" * (max((len(run) for run in re.findall(r"`+", t)), default=0) + 1)
    pad = " " if t.startswith("`") or t.endswith("`") else ""
    return f"{fence}{pad}{t}{pad}{fence}"


def _claim_lines(claim: dict[str, Any]) -> list[str]:
    """One kept claim: the agent's words, then the quote, its address and
    the page's words on each side of it."""
    where = f", PDF page {claim['pdf_page']}" if claim.get("pdf_page") else ""
    before = _plain(claim.get("context_before", ""), LEAD_CONTEXT_CHARS, keep_end=True)
    after = _plain(claim.get("context_after", "")[:LEAD_CONTEXT_CHARS], LEAD_CONTEXT_CHARS)
    lines = [f"- {_plain(claim['text'], LEAD_TEXT_CHARS)} ({_plain(claim['id'], 40)})",
             f"  - On the page: \"{_code(claim['quote'])}\" ({_link(claim['source_url'])}{where})",
             f"  - Around it the page reads: ...{before} (the quote) {after}..."]
    if claim.get("pdf_order") == "layout":
        lines.append("  - Found only in the PDF's layout order, which can read across "
                     "columns: open the page.")
    if claim.get("words_shared_with_url"):
        lines.append("  - Some of these words are also in the request ("
                     + _plain(", ".join(claim["words_shared_with_url"][:6]), 80)
                     + "): check the page did not just show the request back.")
    return lines


def render_lead(checked: dict[str, Any], headline: str = "") -> str:
    """lead.md, written from the checked file alone. Double quotation marks
    and links appear only where this function puts a kept quote and its
    address; everything an agent wrote goes through _plain()."""
    qc, sections = checked["quotecheck"], checked["sections"]
    out = [f"# Lead check: parcel {_plain(checked['parcel_id'], 60)}"
           + (f", run {_plain(checked['run'], 60)}" if checked.get("run") else ""), ""]
    if _plain(headline):
        out += ["**In one line (the launching session's reading, not checked):** "
                + _plain(headline, MAX_HEADLINE_CHARS), ""]
    out += ["Written by `quotecheck` from `findings.checked.json`; nobody edits it by hand. "
            "Words in double quotation marks were on the linked page when it was fetched "
            f"({qc['checked_at']}). Everything else is an agent's own reading, not checked.", "",
            "## What the documents say", ""]
    for name in SECTIONS:
        body = sections.get(name)
        if body is None:
            out += [f"**{name.capitalize()}: not looked at.**", ""]
            continue
        note = (f" The investigator wrote `{_plain(body['verdict_as_written'], 40)}`, but none of its "
                "quotes was found on its page." if "verdict_as_written" in body else "")
        out += [f"**{name.capitalize()}: `{body['verdict']}`.**{note}"]
        claims = body["claims"]
        if claims:
            out += _claim_lines(claims[0])
            if len(claims) > 1:
                out += [f"- and {len(claims) - 1} more kept claims in the checked file."]
        out += [""]
    out += ["## Skeptic", ""]
    skeptic = checked.get(SKEPTIC)
    if skeptic is None:
        out += ["Not run yet.", ""]
    else:
        backing = ("backed by quotes of its own, below" if skeptic["supported"] else
                   "the skeptic's opinion only: none of its own quotes was found on its page")
        out += [f"**Skeptic: `{skeptic['verdict']}`** ({backing}).", "",
                "The skeptic's objection, in its own words: "
                + _plain(skeptic["objection"], 3 * LEAD_TEXT_CHARS),
                ""]
        if skeptic.get("disputes"):
            out += ["It disputes these first-pass claims: "
                    + _plain(", ".join(skeptic["disputes"]), 300) + ".", ""]
        for claim in skeptic["claims"][:LEAD_SKEPTIC_CLAIMS]:
            out += _claim_lines(claim)
        if len(skeptic["claims"]) > LEAD_SKEPTIC_CLAIMS:
            out += [f"- and {len(skeptic['claims']) - LEAD_SKEPTIC_CLAIMS} more kept claims in "
                    "the checked file."]
        out += [""]
    unknown = [n for n in SECTIONS if n not in sections or sections[n]["verdict"] == UNKNOWN]
    out += ["## What the check proves", "",
            f"`quotecheck` kept {qc['kept']} claims and dropped {qc['dropped']}"
            + (f"; it set to `unknown`: {', '.join(qc['downgraded'])}" if qc["downgraded"] else "")
            + ". A kept claim proves only that its quote was on its page when fetched. It "
            "does not prove the page is current, or that the claim follows from the quote. "
            + (f"Still `unknown`: {', '.join(unknown)}." if unknown else ""), "",
            "## Questions for a person (the agents' own, not checked)", ""]
    bodies = list(sections.items()) + ([(SKEPTIC, skeptic)] if skeptic else [])
    items = [(name, item) for name, body in bodies for item in body.get("open_items") or []]
    # The skeptic's questions first, then one from each section in turn.
    per = [[(n, i) for n, i in items if n == name] for name in (SKEPTIC, *SECTIONS)]
    turns = [pair for rank in range(max((len(p) for p in per), default=0))
             for p in per if rank < len(p) for pair in [p[rank]]]
    for name, item in turns[:LEAD_OPEN_ITEMS]:
        out += [f"- ({name}) {_plain(item, LEAD_TEXT_CHARS)}"]
    if len(turns) > LEAD_OPEN_ITEMS:
        out += [f"- and {len(turns) - LEAD_OPEN_ITEMS} more in the checked file."]
    if not items:
        out += ["None written down."]
    return "\n".join(out) + "\n"


def _read_json(path: Path) -> tuple[Any, str]:
    """(the document, the SHA-256 of the file). Refuses repeated fields and
    numbers that are not finite (they would break the writing at the end)."""
    def no_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("a field is given twice")
        return dict(pairs)

    def no_constant(name: str) -> Any:
        raise ValueError(f"{name} is not a JSON value")

    def finite(text: str) -> float:
        value = float(text)
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("a number is too large")
        return value

    try:
        raw = path.read_bytes()
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=no_repeats,
                         parse_constant=no_constant, parse_float=finite)
    except (OSError, ValueError, RecursionError) as exc:
        raise Invalid(f"{path.name} is unreadable ({clean(exc, 120)})") from exc
    return doc, hashlib.sha256(raw).hexdigest()


def _remove_results(folder: Path) -> str:
    """Remove the checked file and lead.md, trying both; '' or what failed."""
    failed = []
    for old in (folder / CHECKED_NAME, folder / LEAD_NAME):
        try:
            if old.is_symlink() or old.exists():
                old.unlink()
        except OSError as exc:
            failed.append(f"{old.name} ({type(exc).__name__})")
    return " and ".join(failed)


def main(argv: list[str] | None = None, fetcher: Fetcher | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("findings", type=Path, help="a lead's findings.json")
    ap.add_argument("--with-skeptic", action="store_true",
                    help=f"the run after the skeptic: check {SKEPTIC_NAME} too")
    ap.add_argument("--findings-sha256", metavar="HASH", default="",
                    help="with --with-skeptic: the hash the first run printed for findings.json")
    ap.add_argument("--headline", metavar="TEXT", default="",
                    help="the launching session's one line for the top of lead.md (its own "
                         "reading; printed as such, as plain text)")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        # A mistyped option must not leave an old result behind either.
        for word in (sys.argv[1:] if argv is None else argv):
            if Path(word).name == FINDINGS_NAME:
                _remove_results(Path(word).parent)
        raise
    path: Path = args.findings
    folder = path.parent
    out, lead = folder / CHECKED_NAME, folder / LEAD_NAME
    try:
        if path.name != FINDINGS_NAME:     # another file: this folder's results are not its
            raise Invalid(f"the file to check is named {FINDINGS_NAME}")
        failed = _remove_results(folder)   # a failed run must not leave an old result
        if failed:
            raise Invalid(f"the old {failed} could not be removed")
        findings, findings_hash = _read_json(path)
        if (isinstance(findings, dict) and isinstance(findings.get("parcel_id"), str)
                and folder.resolve().name != findings["parcel_id"].replace(" ", "_")):
            raise Invalid(f"parcel_id does not match the folder name {clean(folder.resolve().name, 60)!r} "
                          "(the folder is the parcel id with each space written as _)")
        skeptic_path = folder / SKEPTIC_NAME
        skeptic_doc, skeptic_hash = None, ""
        if args.with_skeptic:
            if args.findings_sha256.strip().lower() != findings_hash:
                raise Invalid(f"{FINDINGS_NAME} is not the file the first run checked "
                              "(--findings-sha256 must give the hash that run printed, and "
                              "no other). The skeptic disputes claims by position, so the "
                              "file may not change between the runs")
            if not skeptic_path.is_file() or skeptic_path.is_symlink():
                raise Invalid(f"--with-skeptic, but there is no {SKEPTIC_NAME}")
            skeptic_doc, skeptic_hash = _read_json(skeptic_path)
        elif skeptic_path.exists() or skeptic_path.is_symlink():
            raise Invalid(f"there is already a {SKEPTIC_NAME} here. The first run is for the "
                          "investigator's file alone: remove it (the skeptic has not run "
                          "yet), or pass --with-skeptic and --findings-sha256 for the run "
                          "after the skeptic")
        result = check(findings, folder / PAGES_DIR, fetcher or Fetcher(), skeptic_doc)
        result.checked["quotecheck"]["inputs"] = {
            FINDINGS_NAME: findings_hash, **({SKEPTIC_NAME: skeptic_hash} if skeptic_doc else {})}
        _safe_write(out, json.dumps(result.checked, indent=1, allow_nan=False).encode("utf-8"))
        _safe_write(lead, render_lead(result.checked, args.headline).encode("utf-8"))
    except Invalid as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        for half in (out, lead):           # both files or neither
            with contextlib.suppress(OSError):
                half.unlink()
        print(f"refused: a file could not be written ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(f"{result.kept} claims kept, {len(result.dropped)} dropped"
          + (f"; verdict set to unknown for: {', '.join(result.downgraded)}"
             if result.downgraded else ""))
    if skeptic_doc is not None:
        backed = "has" if result.checked[SKEPTIC]["supported"] else "has NO"
        print(f"skeptic ({SKEPTIC_NAME}): {result.checked[SKEPTIC]['verdict']}; it {backed} "
              "kept quote of its own")
    for d in result.dropped:
        print(f"  dropped ({clean(d['id'], 40)}): {clean(d['reason'])}")
    print(f"written: {out} and {lead.name}")
    print(f"{FINDINGS_NAME} sha256: {findings_hash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
