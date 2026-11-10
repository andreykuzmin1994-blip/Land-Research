"""Check an investigation's findings against the pages they cite
(reviews/35_screener_lead_layer; the rules are in INVESTIGATE.md).

    python -m screener.quotecheck screener/leads/<parcel>/findings.json

An investigating agent writes ``findings.json``: per checklist section a
verdict and claims, each claim with the URL it rests on and an exact quote
from that page. This tool, not the agent, fetches each URL (once, into
``pages/`` beside the findings), and keeps a claim only if its quote is on
the page. It writes ``findings.checked.json``:

- a claim whose quote is not on its page, whose page could not be
  fetched, or that is malformed is dropped, with the reason;
- a section left with no kept claim has its verdict set to ``unknown``,
  whatever the agent wrote: a verdict with no surviving evidence is not a
  finding.

What it proves is narrow: the quoted words were on the page at that URL
when it was fetched. It does not prove the page is right, current or
authoritative, or that the claim follows from the quote. That is the
skeptic's job and then a person's.

Fetching rules: http(s) only; never a host in ``REFUSED_HOSTS`` (CoStar,
LoopNet, Crexi: their terms prohibit automated use); robots.txt is read
first and honoured, and a robots.txt that cannot be read (other than
"not found") means the page is not fetched; one request per second per
host; a size cap; the screener's own User-Agent. PDFs are read with the
``pdftotext`` command when it is installed and are otherwise not
verifiable (the claim is dropped and says so).

Offline in tests: every fetch goes through ``Fetcher``, which tests
replace. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "land-screener/0.1 (internal land-acquisition research)"
SCHEMA = 1
CHECKED_NAME = "findings.checked.json"
PAGES_DIR = "pages"
MIN_QUOTE_CHARS = 25
MAX_QUOTE_CHARS = 1200
MAX_PAGE_BYTES = 25_000_000
MAX_CLAIMS = 200
HOST_DELAY_S = 1.0
UNKNOWN = "unknown"
# Hosts never fetched: their terms prohibit automated use (README, "What it
# can't tell you"; CLAUDE.md "NEVER scrape CoStar").
REFUSED_HOSTS = ("costar.com", "loopnet.com", "crexi.com")
SKEPTIC = "skeptic"


class Invalid(Exception):
    """The findings file cannot be checked at all; the message says why."""


@dataclass
class Page:
    text: str = ""            # normalised text, "" when there is none
    problem: str = ""         # why there is no text


class _Text(HTMLParser):
    """Visible text of an HTML page (scripts and styles left out)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


_PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                        "–": "-", "—": "-", "−": "-", "­": "",
                        " ": " "})


def normalise(text: str) -> str:
    """One comparable form for a quote and a page: NFKC, straight quotes and
    hyphens, whitespace collapsed, case folded."""
    t = unicodedata.normalize("NFKC", text).translate(_PUNCT)
    return re.sub(r"\s+", " ", t).strip().casefold()


def html_text(raw: str) -> str:
    parser = _Text()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:           # a malformed page: fall back to a crude strip
        return html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return "".join(parser.parts)


def page_name(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def url_problem(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        return "no source_url"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "source_url is not an http(s) address"
    if parts.username or parts.password:
        return "source_url carries credentials"
    host = parts.hostname.lower()
    if any(host == h or host.endswith("." + h) for h in REFUSED_HOSTS):
        return f"{host} is never fetched (its terms prohibit automated use)"
    return ""


class Fetcher:
    """Polite fetches with robots.txt. Tests replace ``get``."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._sleep, self._clock = sleep, clock
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | str] = {}

    def get(self, url: str) -> tuple[bytes, str]:
        """(body, content type). Raises OSError / ValueError on failure."""
        host = host_of(url)
        wait = HOST_DELAY_S - (self._clock() - self._last.get(host, -HOST_DELAY_S))
        if wait > 0:
            self._sleep(wait)
        self._last[host] = self._clock()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:        # noqa: S310 (http/https only)
            body = resp.read(MAX_PAGE_BYTES + 1)
            if len(body) > MAX_PAGE_BYTES:
                raise ValueError(f"page larger than {MAX_PAGE_BYTES:,} bytes")
            return body, (resp.headers.get("Content-Type") or "").lower()

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
        return "" if rules.can_fetch(USER_AGENT, url) else "robots.txt disallows this page"

    def _read_robots(self, root: str) -> urllib.robotparser.RobotFileParser | str:
        parser = urllib.robotparser.RobotFileParser()
        try:
            body, _ = self.get(root + "/robots.txt")
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                parser.parse([])            # no robots.txt: no crawl rule applies
                return parser
            return f"robots.txt could not be read (HTTP {exc.code})"
        except (OSError, ValueError) as exc:
            return f"robots.txt could not be read ({str(exc)[:80]})"
        parser.parse(body.decode("utf-8", "replace").splitlines())
        return parser


def _pdf_text(path: Path) -> Page:
    tool = shutil.which("pdftotext")
    if tool is None:
        return Page(problem="a PDF, and pdftotext is not installed: not verifiable here")
    try:
        out = subprocess.run([tool, "-layout", str(path), "-"], capture_output=True,
                             timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return Page(problem=f"pdftotext failed ({str(exc)[:80]})")
    if out.returncode != 0:
        return Page(problem=f"pdftotext failed (exit {out.returncode})")
    return Page(text=normalise(out.stdout.decode("utf-8", "replace")))


def load_page(url: str, pages: Path, fetcher: Fetcher) -> Page:
    """The normalised text of the page at url, fetched once into pages/."""
    stem = page_name(url)
    kept = sorted(pages.glob(stem + ".*")) if pages.is_dir() else []
    if kept:
        path = kept[0]
    else:
        why = fetcher.allowed(url)
        if why:
            return Page(problem=why)
        try:
            body, ctype = fetcher.get(url)
        except (OSError, ValueError) as exc:
            return Page(problem=f"could not be fetched ({str(exc)[:120]})")
        is_pdf = body[:5] == b"%PDF-" or "pdf" in ctype
        path = pages / (stem + (".pdf" if is_pdf else ".html"))
        pages.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(body)
        tmp.replace(path)
    if path.suffix == ".pdf":
        return _pdf_text(path)
    try:
        raw = path.read_bytes().decode("utf-8", "replace")
    except OSError as exc:
        return Page(problem=f"kept page unreadable ({str(exc)[:80]})")
    text = normalise(html_text(raw))
    return Page(text=text) if text else Page(problem="the page has no text")


def claim_problem(claim: Any) -> str:
    """What is wrong with a claim's shape ('' if nothing)."""
    if not isinstance(claim, dict):
        return "a claim is not an object"
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
    return ""


@dataclass
class Result:
    checked: dict[str, Any]
    kept: int = 0
    dropped: list[dict[str, Any]] = field(default_factory=list)
    downgraded: list[str] = field(default_factory=list)


def _sections(findings: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    sections = findings.get("sections")
    if not isinstance(sections, dict) or not sections:
        raise Invalid("no sections")
    out = []
    for name, body in sections.items():
        if not isinstance(name, str) or not isinstance(body, dict):
            raise Invalid(f"section {name!r} is not an object")
        out.append((name, body))
    skeptic = findings.get(SKEPTIC)
    if skeptic is not None:
        if not isinstance(skeptic, dict):
            raise Invalid("skeptic is not an object")
        out.append((SKEPTIC, skeptic))
    return out


def check(findings: Any, pages: Path, fetcher: Fetcher) -> Result:
    """Check every claim; return the checked copy and what was dropped."""
    if not isinstance(findings, dict):
        raise Invalid("the findings file is not a JSON object")
    if findings.get("schema") != SCHEMA:
        raise Invalid(f"schema must be {SCHEMA}")
    if not isinstance(findings.get("parcel_id"), str) or not findings["parcel_id"].strip():
        raise Invalid("no parcel_id")
    sections = _sections(findings)
    total = sum(len(b.get("claims") or []) if isinstance(b.get("claims"), list) else 0
                for _, b in sections)
    if total > MAX_CLAIMS:
        raise Invalid(f"more than {MAX_CLAIMS} claims")
    result = Result(checked=json.loads(json.dumps(findings)))
    cache: dict[str, Page] = {}
    for name, body in sections:
        target = result.checked[SKEPTIC] if name == SKEPTIC else result.checked["sections"][name]
        claims = body.get("claims")
        if claims is None:
            claims = []
        if not isinstance(claims, list):
            raise Invalid(f"{name}: claims is not a list")
        kept = []
        for claim in claims:
            why = claim_problem(claim)
            if not why:
                url = claim["source_url"]
                if url not in cache:
                    cache[url] = load_page(url, pages, fetcher)
                page = cache[url]
                if page.problem:
                    why = f"page {page.problem}"
                elif normalise(claim["quote"]) not in page.text:
                    why = "the quote is not on the page"
            if why:
                result.dropped.append({"section": name, "reason": why,
                                       "claim": claim if isinstance(claim, dict) else repr(claim)})
            else:
                kept.append({**claim, "page_file": f"{PAGES_DIR}/{page_name(claim['source_url'])}"})
        target["claims"] = kept
        result.kept += len(kept)
        verdict = body.get("verdict")
        if name != SKEPTIC and not kept and verdict != UNKNOWN:
            target["verdict"] = UNKNOWN
            target["verdict_as_written"] = verdict
            result.downgraded.append(name)
    result.checked["quotecheck"] = {
        "kept": result.kept, "dropped": len(result.dropped), "downgraded": result.downgraded,
        "proves": "each kept quote was on the page at its URL when fetched; nothing more",
    }
    result.checked["dropped_claims"] = result.dropped
    return result


def main(argv: list[str] | None = None, fetcher: Fetcher | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("findings", type=Path, help="a lead's findings.json")
    args = ap.parse_args(argv)
    path: Path = args.findings
    try:
        try:
            findings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Invalid(f"{path} is unreadable ({exc})") from exc
        result = check(findings, path.parent / PAGES_DIR, fetcher or Fetcher())
    except Invalid as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    out = path.with_name(CHECKED_NAME)
    tmp = out.with_suffix(".json.part")
    tmp.write_text(json.dumps(result.checked, indent=1, allow_nan=False), encoding="utf-8")
    tmp.replace(out)
    print(f"{result.kept} claims kept, {len(result.dropped)} dropped"
          + (f"; verdict set to unknown for: {', '.join(result.downgraded)}"
             if result.downgraded else ""))
    for d in result.dropped:
        print(f"  dropped ({d['section']}): {d['reason']}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
