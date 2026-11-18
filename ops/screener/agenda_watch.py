#!/usr/bin/env python3
"""Between screener runs: do new council or planning agendas name a parcel
you watch, or hold an industrial zoning item?

    agenda_watch.py run --sources FILE --output-root screener/output --state DIR
    agenda_watch.py prepare --sources FILE --output-root DIR --state DIR --job JOB
    agenda_watch.py scan JOB RESULT
    agenda_watch.py report --job JOB --result RESULT --state DIR

No AI model reads anything here. Fixed code fetches the agenda index
pages the operator listed, reads the documents that are new since the
last time, and searches their text for

- **a parcel id** of the watch list, compared with every character that
  is not a letter or a digit removed on both sides (`09F-0700-0032-092-7`
  is `09F070000320927`). Ids under 10 letters and digits are not searched;
- **a site address** of the watch list: the house number and the street's
  name as whole words (`3600 Naturally Fresh`), only in the sources that
  cover the parcel's city when a source says which cities it covers. A
  site with house number 0 has no address to search for. A possible
  match, no more: another city can have the same number and street;
- **industrial zoning words**: within 300 characters of each other, a
  word for a zoning action (rezoning, annexation, use permit, moratorium,
  variance) and a word for an industrial district or use.

It also reads the **titles** the index page gives its documents, for
every new one, read or not: a title that holds a watched parcel's street
name ("Z26-008 Spence Road"), or a zoning case number or zoning word.
Cities post many adopted ordinances as scans with no text, and there the
title is all there is to search.

The three steps, and who runs them on the server (`agenda-watch.sh`):

1. ``prepare`` (the trusted run user): the watch list from the newest full
   run of each county (grade A and B leads, parcels marked pursue or
   maybe there or in ``decisions.csv``, the top of the ranking: ids and
   site addresses only) and what each source has already seen, as one
   job file.
2. ``scan`` (an account of its own that owns nothing): every request and
   every PDF. Agenda packets hold documents that applicants and the
   public filed, so the account that reads them is not the one that owns
   the checkout. It writes one result file.
3. ``report`` (the trusted run user): checks the result against the job
   (only that source's host and paths, only parcels of the watch list),
   updates ``state.json``, and writes ``agenda-watch.md`` and
   ``summary.json`` (counts only, for the alert).

``run`` does the three in one process, for a trial by hand and the tests.

What a hit is worth: the words are in that document on that page. It
does not say the item is about the parcel (an id can be listed as a
neighbour), that the document is current, or what was decided. And no hit
does not mean no item: the report counts the documents that could not be
read or were only partly text (scans), and says when a source stops
answering or stops posting.

A document counts as seen only once its text was searched; one that
cannot be read is tried on three runs and then listed for you to open.
A source seen for the first time has its list noted and only its first
few documents read; a source whose list is suddenly mostly new (its
addresses changed) is noted, not crawled, and said so. An agenda that is
amended under the same address after it was read is not read again.

Fetching is the quote checker's own (`screener/quotecheck.py`, loaded by
its path): GET only, public https addresses only, robots.txt read first
and honoured with the site's Crawl-delay, no redirect to another host,
never CoStar, LoopNet or Crexi; and here, only links found on the listed
index page, on its host, under the path prefixes the source names, with
caps on documents, requests and bytes per run.

``agenda-watch.md`` shows a few words of the document around each hit,
reduced to plain text. They are public record and can name an applicant
or an owner; tax-roll owner names of the runs are withheld where they are
found in roll spelling, no more. The alert line holds counts only.
Nothing here changes a score, grade, rank, screen or decision. Stdlib
only. Exit codes: 0 done (also when a source failed: the counts say so);
2 refused (the message says why).

Kept outside ``screener/`` on purpose, like the rest of ``ops/screener``:
changing it does not reset the screener's AutoResearch baseline.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import resource
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MD_NAME, STATE_NAME, SUMMARY_NAME, LOCK_NAME = "agenda-watch.md", "state.json", "summary.json", "lock"
SCHEMA = 1
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
PREFIX_RE = re.compile(r"/[A-Za-z0-9._~/-]{1,200}")
URL_RE = re.compile(r"https://[A-Za-z0-9.-]{1,200}(?::\d{1,5})?/[A-Za-z0-9._~/-]{1,500}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
MAX_SOURCES = 20
MAX_NEW_DEFAULT, MAX_NEW_LIMIT = 6, 10      # new documents read per source per run
FIRST_SIGHT = 3                             # documents read from a source seen for the first time
MAX_REQUESTS = 80                           # every request of a run: robots.txt, index pages, documents, redirects
MAX_BYTES = 300_000_000                     # downloaded per run
STORM_COUNT, STORM_SHARE = 20, 0.5          # this many, or this share, of a known source's links new at once
DOC_BYTES = 60_000_000
INDEX_LINKS = 5_000                         # links read from one index page
SEEN_KEPT = 4_000                           # documents remembered per source
HITS_KEPT = 300                             # hits kept in the report
HITS_PER_DOC = 12
MAX_TRIES = 3                               # runs a document that cannot be read is tried on
MIN_ID_CHARS = 10
TOP_N = 50
SNIPPET = 110                               # characters each side of a hit
NEAR = 300                                  # zoning word and industrial word within this many characters
PDF_TIMEOUT_S, PDF_CPU_S = 240, 120
PDF_TEXT_BYTES, PDF_PAGES = 30_000_000, 600
PDF_MEMORY = 1_500_000_000
PARTLY = 0.2                                # more than this share of pages without text: partly read
FAIL_DAYS, FAIL_REPEAT_DAYS = 3, 30         # alert after this many failed days, then every so many
STALE_DAYS = 45                             # a source with no new document for this long
OLD_RUN_DAYS = 15
RESULT_BYTES = 2_000_000
GRADES = ("A", "B")
MARKS = ("pursue", "maybe")
KINDS = ("parcel id", "possible address", "industrial zoning words",
         "same street, in a title", "zoning case, in a title")
TITLE_KINDS = (KINDS[1], KINDS[3], KINDS[4])
PARCEL_KINDS = (KINDS[0], KINDS[1], KINDS[3])   # hits that name a parcel you watch, or its street
TITLES_PER_SOURCE = 200                     # titles with a hit kept from one source in one run
TITLE_MAX_AGE_DAYS = 400                    # a document dated older than this in its address: title not reported
DATE_IN_URL = re.compile(r"(?<!\d)(?:(?P<m>\d{2})(?P<d>\d{2})(?P<y>20\d{2})|(?P<y2>20\d{2})-?(?P<m2>\d{2})-?(?P<d2>\d{2}))(?!\d)")
ZONING_TITLE = re.compile(r"(?<![A-Za-z0-9])(?:Z|RZ|ZM|U|SUP|CUP|CDP|CV|ANX)-?\d{2,4}-\d{2,4}(?![A-Za-z0-9])|"
                          r"\b(?:rezon\w*|annex\w*|use permit|moratorium|zoning)\b", re.I)
DOC_STATES = ("read", "partly read", "not read")
STREET_ENDS = frozenset(
    "RD ROAD ST STREET DR DRIVE BLVD BOULEVARD AVE AVENUE HWY HIGHWAY PKWY PARKWAY LN LANE CT COURT "
    "CIR CIRCLE WAY PL PLACE TER TERRACE TRL TRAIL CONN CONNECTOR EXT REAR N S E W NE NW SE SW".split())
ZONING_ACT = re.compile(r"\b(?:rezon\w*|zoning map amendment|annex\w*|(?:special |conditional )?use permit|"
                        r"moratorium|variance)\b", re.I)
INDUSTRIAL = re.compile(r"\b(?:M-?[12]\b|I-?[12]\b|light industrial|heavy industrial|industrial district|"
                        r"warehous\w*|distribution cent\w*|logistics|data cent\w*|truck (?:terminal|parking|stop))",
                        re.I)


class Refuse(Exception):
    """Exit 2: nothing done; the message says why."""


def load_beside(name: str, *folders: Path) -> Any:
    """A module of ours loaded by its file path: the first folder that
    holds it. No folder is put on sys.path for it (python -I runs this)."""
    for folder in folders:
        path = folder / f"{name}.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module
    raise Refuse(f"{name}.py is not beside this tool or in the checkout")


_LOADED: dict[str, Any] = {}     # this tool's own copies: never whatever else is in sys.modules by that name


def quotecheck() -> Any:
    if "quotecheck" not in _LOADED:
        _LOADED["quotecheck"] = load_beside("quotecheck", HERE, REPO / "screener")
    return _LOADED["quotecheck"]


def changes() -> Any:
    if "changes" not in _LOADED:
        _LOADED["changes"] = load_beside("changes", HERE)
    return _LOADED["changes"]


def pin_names() -> None:
    """Every later look-up of a host name gives the addresses its first
    look-up gave (as lead_fetch.py): the address that was checked to be
    public is the one connected to."""
    real = socket.getaddrinfo
    first: dict[str, list[str]] = {}

    def pinned(host, port, *args, **kwargs):
        key = host.decode("ascii", "replace").lower() if isinstance(host, bytes) else str(host).lower()
        key = key.rstrip(".")
        if "%" in key:
            raise socket.gaierror(socket.EAI_NONAME, "a host name with % in it is not looked up")
        if key not in first:
            first[key] = sorted({info[4][0] for info in real(host, None)})
        answers = []
        for address in first[key]:
            answers += real(address, port, *args, **kwargs)
        return answers

    socket.getaddrinfo = pinned


def plain(text: Any, limit: int = 400) -> str:
    """Document or page text as inert text on one line: letters, digits and
    plain punctuation, nothing that reads as markup or as an address."""
    out = re.sub(r"[\s_]+", " ", str(text if text is not None else ""))
    out = re.sub(r"(?i)\b(?:https?|ftp|file|mailto)\s*:\s*/*", "", out)
    out = re.sub(r"(?i)\bwww\.", "www ", out)
    out = re.sub(r"[^A-Za-z0-9 .,;:()'%$/&#-]", "", out)
    out = re.sub(r"\.(?=[A-Za-z])", ". ", out)
    out = re.sub(r"^[#\s-]+", "", out.strip())             # never a heading or a list item
    return out if len(out) <= limit else out[:limit].rstrip() + " ..."


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_between(earlier: str, later: str) -> int | None:
    try:
        a = datetime.date.fromisoformat(earlier[:10])
        b = datetime.date.fromisoformat(later[:10])
    except (ValueError, TypeError):
        return None
    return (b - a).days


def write(path: Path, text: str, mode: int = 0o600) -> None:
    """Atomically and to disk, never through a link."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------- sources

def read_sources(path: Path) -> list[dict[str, Any]]:
    """The operator's list of agenda index pages, checked. The file is the
    operator's own: a regular file of this user that nobody else can write."""
    try:
        info = os.lstat(path)
    except OSError:
        raise Refuse(f"{path} is not there (copy agenda_sources.example.json and edit it)") from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise Refuse(f"{path} must be a regular file of this user that no one else can write (chmod 600)")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refuse(f"{path} cannot be read as JSON ({type(exc).__name__})") from None
    items = doc.get("sources") if isinstance(doc, dict) else None
    if not isinstance(items, list) or not items:
        raise Refuse(f'{path} must hold {{"sources": [...]}} with at least one source')
    if len(items) > MAX_SOURCES:
        raise Refuse(f"{path} lists more than {MAX_SOURCES} sources")
    names: set[str] = set()
    return [check_source(item, f"{path.name}, source {n}", names) for n, item in enumerate(items, 1)]


def check_source(item: Any, what: str, names: set[str]) -> dict[str, Any]:
    """One source, as the fixed fields this tool uses."""
    def bad(why: str) -> Refuse:
        return Refuse(f"{what}: {why}")
    allowed = {"name", "index_url", "path_prefixes", "max_new", "jurisdictions", "expect_every_days", "note"}
    if not isinstance(item, dict) or set(item) - allowed:
        raise bad("fields are " + ", ".join(sorted(allowed)))
    name, url, prefixes = item.get("name"), item.get("index_url"), item.get("path_prefixes")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name.lower() in names:
        raise bad("name must be 1-40 letters, digits, spaces, dots, dashes, and not used twice")
    names.add(name.lower())
    parts = urllib.parse.urlsplit(url) if isinstance(url, str) and len(url) <= 500 else None
    if (parts is None or parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or parts.fragment or "\\" in url or any(ord(c) < 33 or ord(c) > 126 for c in url)):
        raise bad("index_url must be a plain https address")
    if (not isinstance(prefixes, list) or not 1 <= len(prefixes) <= 5
            or not all(isinstance(p, str) and PREFIX_RE.fullmatch(p) and ".." not in p and "//" not in p
                       for p in prefixes)):
        raise bad('path_prefixes must be 1-5 paths such as "/AgendaCenter/ViewFile/Agenda/"')
    max_new = item.get("max_new", MAX_NEW_DEFAULT)
    if isinstance(max_new, bool) or not isinstance(max_new, int) or not 1 <= max_new <= MAX_NEW_LIMIT:
        raise bad(f"max_new must be a whole number from 1 to {MAX_NEW_LIMIT}")
    cities = item.get("jurisdictions", [])
    if not isinstance(cities, list) or len(cities) > 20 or not all(isinstance(c, str) and 0 < len(c) <= 80 for c in cities):
        raise bad("jurisdictions must be a list of city names as ranked.csv writes them")
    every = item.get("expect_every_days", STALE_DAYS)
    if isinstance(every, bool) or not isinstance(every, int) or not 7 <= every <= 400:
        raise bad("expect_every_days must be a whole number from 7 to 400")
    return {"name": name, "index_url": url, "path_prefixes": prefixes, "max_new": max_new,
            "jurisdictions": [c.strip().lower() for c in cities], "expect_every_days": every}


def link_allowed(url: str, src: dict[str, Any]) -> bool:
    """Whether an address is a document of this source: https, the index
    page's own host, a path under one of its prefixes, nothing else in it."""
    if not isinstance(url, str) or not URL_RE.fullmatch(url):
        return False
    parts, home = urllib.parse.urlsplit(url), urllib.parse.urlsplit(src["index_url"])
    path = parts.path
    return (parts.netloc.lower() == home.netloc.lower() and ".." not in path and "//" not in path
            and any(path.startswith(p) and len(path) > len(p) for p in src["path_prefixes"]))


class _Links(HTMLParser):
    """The <a href> of a page with a title for each: its aria-label, else
    its own text. A <base> is not honoured."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._open: list[Any] | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag != "a" or len(self.links) >= INDEX_LINKS:
            return
        a = dict(attrs)
        href = a.get("href")
        if isinstance(href, str) and 0 < len(href) <= 600:
            self._open = [href, a.get("aria-label") or "", []]

    def handle_data(self, data: str) -> None:
        if self._open is not None and len(self._open[2]) < 20:
            self._open[2].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._open is not None:
            href, label, words = self._open
            self.links.append((href, label or " ".join(words)))
            self._open = None


def document_links(page_url: str, html: str, src: dict[str, Any]) -> list[tuple[str, str]]:
    """(address, title) of the documents an index page links, in page
    order, each address once, without query string or fragment."""
    parser = _Links()
    try:
        parser.feed(html)
    except Exception:                      # a page the parser cannot finish: what it read so far
        pass
    out: dict[str, str] = {}
    for href, title in parser.links:
        try:
            parts = urllib.parse.urlsplit(urllib.parse.urljoin(page_url, href.strip()))
        except ValueError:
            continue
        if parts.scheme != "https" or parts.query:
            continue
        url = urllib.parse.urlunsplit(("https", parts.netloc, parts.path, "", ""))
        if not link_allowed(url, src):
            continue
        if not out.get(url):
            out[url] = plain(title, 80)
    return list(out.items())


# -------------------------------------------------------------- watch list

def newest_full_runs(output_root: Path) -> list[Path]:
    """For each county under output_root, its newest run that ranked
    something and was not a --limit trial."""
    ch = changes()
    best: dict[str, tuple[float, Path]] = {}
    if not output_root.is_dir():
        return []
    for d in sorted(output_root.iterdir()):
        if not d.is_dir() or d.is_symlink() or not (d / "ranked.csv").is_file():
            continue
        meta = ch.meta_of(d)
        if meta.get("limit") is not None:
            continue
        try:
            if not ch.read_rows(d / "ranked.csv"):
                continue
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
        county, when = ch.county_of(d, meta), ch.run_time(d, meta)
        if county not in best or when > best[county][0]:
            best[county] = (when, d)
    return [d for _, d in sorted(best.values(), key=lambda t: t[1].name)]


def marks_of(decisions: Path | None) -> dict[str, str]:
    """The last decision per parcel in decisions.csv, read only."""
    out: dict[str, str] = {}
    if decisions is None or not decisions.is_file():
        return out
    with open(decisions, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        reader.fieldnames = [(h or "").strip() for h in (reader.fieldnames or [])]
        for row in reader:
            pid = (row.get("parcel_id") or "").strip()
            if pid:
                out[pid] = (row.get("decision") or "").strip().lower()
    return out


def street_words(address: str) -> list[str] | None:
    """The street's own name of a site address ("0 SPENCE RD REAR" gives
    SPENCE), or None when there is no name to tell it by."""
    words = re.findall(r"[A-Za-z0-9]+", address.upper())
    if len(words) < 2 or not words[0].isdigit():
        return None
    name: list[str] = []
    for word in words[1:]:
        if word in STREET_ENDS and name:
            break
        name.append(word)
    name = name[:4]
    if not name or not any(len(w) >= 4 and not w.isdigit() for w in name):
        return None
    return name


def address_words(address: str) -> list[str] | None:
    """[house number, street name words] to search for, or None when the
    site address has no number (0) or no name to tell it by."""
    name = street_words(address)
    number = re.match(r"\s*(\d+)", address)
    if name is None or number is None or int(number.group(1)) == 0:
        return None
    return [number.group(1), *name]


def watch_list(run_dirs: list[Path], decisions: Path | None = None, top: int = TOP_N) -> list[dict[str, Any]]:
    """The parcels to look for. Trusted side: each entry also carries what
    the report shows (where, why); scan is handed the search keys only."""
    ch = changes()
    marks = marks_of(decisions)
    out: dict[str, dict[str, Any]] = {}
    for run_dir in run_dirs:
        for pid, row in ch.read_rows(run_dir / "ranked.csv").items():
            why = []
            grade = (row.get("lead_grade") or "").strip()
            if grade in GRADES:
                why.append(f"grade {grade}")
            mark = marks.get(pid, (row.get("decision") or "").strip().lower())
            if mark in MARKS:
                why.append(f"marked {mark}")
            rank = ch.rank_of(row)
            if rank is not None and rank <= top:
                why.append(f"rank {rank}")
            if not why or mark in ("pass", "listed") or pid in out:
                continue
            key = re.sub(r"[^A-Za-z0-9]", "", pid).upper()
            out[pid] = {"parcel_id": pid, "why": ", ".join(why), "where": ch.where(row),
                        "id_key": key if len(key) >= MIN_ID_CHARS and key.isascii() else "",
                        "address": address_words(row.get("site_address") or ""),
                        "street": street_words(row.get("site_address") or ""),
                        "jurisdiction": (row.get("jurisdiction") or "").strip().lower()}
    return list(out.values())


# ----------------------------------------------------------------- matching

def squeeze(text: str) -> tuple[str, list[int]]:
    """The text's ASCII letters and digits, upper case, and where each was."""
    chars, at = [], []
    for i, ch in enumerate(text):
        if ch.isascii() and ch.isalnum():
            chars.append(ch.upper())
            at.append(i)
    return "".join(chars), at


def address_pattern(words: list[str]) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\s,.]+".join(map(re.escape, words)) + r"(?![A-Za-z0-9])", re.I)


def snippet(text: str, start: int, end: int) -> str:
    return plain(text[max(0, start - SNIPPET):end + SNIPPET], 2 * SNIPPET + 80)


def find_hits(pages: list[tuple[int, str]], watch: list[dict[str, Any]], src: dict[str, Any]) -> list[dict[str, Any]]:
    """What a document's text holds. At most one hit of a kind per parcel
    per document, and three pages of zoning words."""
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    zoning_pages = 0
    cities = src.get("jurisdictions") or []
    patterns = {w["parcel_id"]: address_pattern(w["address"]) for w in watch
                if w.get("address") and (not cities or w.get("jurisdiction") in cities)}
    for number, raw in pages:
        text = unicodedata.normalize("NFKC", raw)
        flat, at = squeeze(text)
        for w in watch:
            pid = w["parcel_id"]
            if w.get("id_key") and ("id", pid) not in seen:
                i = flat.find(w["id_key"])
                while i >= 0:
                    a, b = at[i], at[i + len(w["id_key"]) - 1]
                    # Not a piece of a longer run of letters and digits.
                    inside = ((a > 0 and text[a - 1].isascii() and text[a - 1].isalnum())
                              or (b + 1 < len(text) and text[b + 1].isascii() and text[b + 1].isalnum()))
                    if not inside:
                        seen.add(("id", pid))
                        hits.append({"kind": KINDS[0], "parcel_id": pid, "page": number,
                                     "text": snippet(text, a, b + 1)})
                        break
                    i = flat.find(w["id_key"], i + 1)
            if pid in patterns and ("address", pid) not in seen:
                m = patterns[pid].search(text)
                if m:
                    seen.add(("address", pid))
                    hits.append({"kind": KINDS[1], "parcel_id": pid, "page": number,
                                 "text": snippet(text, m.start(), m.end())})
        if zoning_pages < 3:
            for act in ZONING_ACT.finditer(text):
                m = INDUSTRIAL.search(text, max(0, act.start() - NEAR), act.end() + NEAR)
                if m:
                    zoning_pages += 1
                    a, b = min(act.start(), m.start()), max(act.end(), m.end())
                    hits.append({"kind": KINDS[2], "parcel_id": "", "page": number, "text": snippet(text, a, b)})
                    break
        if len(hits) >= HITS_PER_DOC:
            break
    return hits[:HITS_PER_DOC]


def date_in(url: str) -> str:
    """The date a document's address carries (`_10272026-1904`,
    `2026-10-27`), as YYYY-MM-DD, or '' when it carries none."""
    m = DATE_IN_URL.search(urllib.parse.urlsplit(url).path)
    if not m:
        return ""
    y, mo, d = (m.group("y") or m.group("y2")), (m.group("m") or m.group("m2")), (m.group("d") or m.group("d2"))
    try:
        return datetime.date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return ""


def title_hits(title: str, watch: list[dict[str, Any]], src: dict[str, Any]) -> list[dict[str, Any]]:
    """What a document's title on the index page holds: a watched parcel's
    address, a street that watched parcels are on, a zoning case."""
    hits: list[dict[str, Any]] = []
    text = unicodedata.normalize("NFKC", title).replace("_", " ")
    cities = src.get("jurisdictions") or []
    mine = [w for w in watch if not cities or w.get("jurisdiction") in cities]
    streets: dict[tuple[str, ...], list[str]] = {}
    for w in mine:
        if w.get("address") and address_pattern(w["address"]).search(text):
            hits.append({"kind": KINDS[1], "parcel_id": w["parcel_id"], "page": 0, "text": plain(title, 80), "others": 0})
        elif w.get("street"):
            streets.setdefault(tuple(w["street"]), []).append(w["parcel_id"])
    for name, parcels in streets.items():
        if address_pattern(list(name)).search(text):
            hits.append({"kind": KINDS[3], "parcel_id": parcels[0], "page": 0, "text": plain(title, 80),
                         "others": len(parcels) - 1})
    if ZONING_TITLE.search(text):
        hits.append({"kind": KINDS[4], "parcel_id": "", "page": 0, "text": plain(title, 80), "others": 0})
    return hits[:HITS_PER_DOC]


# ------------------------------------------------------------------ reading

def _pdf_limits() -> None:
    """In the pdftotext child only: a ceiling on memory, processor time and
    the size of anything it writes."""
    resource.setrlimit(resource.RLIMIT_AS, (PDF_MEMORY, PDF_MEMORY))
    resource.setrlimit(resource.RLIMIT_CPU, (PDF_CPU_S, PDF_CPU_S + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (PDF_TEXT_BYTES, PDF_TEXT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def document_pages(qc: Any, body: bytes, ctype: str) -> tuple[list[tuple[int, str]], int, str]:
    """([(page number or 0, text)], pages in the document, why there is no
    text). A PDF's text comes from pdftotext under hard limits."""
    if b"%PDF-" in body[:1024] or "pdf" in ctype:
        tool = shutil.which("pdftotext")
        if tool is None:
            return [], 0, "a PDF, and pdftotext is not installed"
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "document.pdf", Path(tmp) / "document.txt"
            source.write_bytes(body)
            try:
                done = subprocess.run([tool, "-l", str(PDF_PAGES), str(source), str(target)], check=False,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      timeout=PDF_TIMEOUT_S, cwd=tmp, preexec_fn=_pdf_limits,
                                      env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "HOME": tmp})
                text = target.read_bytes()[:PDF_TEXT_BYTES].decode("utf-8", "replace") if target.is_file() else ""
            except (OSError, subprocess.SubprocessError) as exc:
                return [], 0, f"pdftotext failed ({type(exc).__name__})"
        if done.returncode != 0 and not text.strip():
            return [], 0, f"pdftotext failed (exit {done.returncode})"
        every = text.split("\f")
        if every and not every[-1].strip():
            every = every[:-1]                 # the form feed after the last page
        pages = [(n, t) for n, t in enumerate(every, 1) if t.strip()]
        return (pages, len(every), "") if pages else ([], len(every), "no text in it (a scan without text)")
    markup = "html" in ctype or "xml" in ctype
    raw = body.decode(qc._charset(body, ctype, markup), "replace")
    text = qc.html_text(raw) if markup else raw
    return ([(0, text)], 1, "") if text.strip() else ([], 1, "no text in it")


class Meter:
    """Counts every request and byte of a run, at the fetcher's own door
    (robots.txt and redirects too), and stops at the run's allowance."""

    def __init__(self, qc: Any, fetcher: Any, requests: int, size: int) -> None:
        self.requests, self.bytes, self._qc = 0, 0, qc
        self._max_requests, self._max_bytes = requests, size
        self._open = fetcher.open_once
        fetcher.open_once = self._counted

    def spent(self) -> bool:
        return self.requests >= self._max_requests or self.bytes >= self._max_bytes

    def _counted(self, url: str, cap: int) -> Any:
        if self.spent():
            raise self._qc.FetchError("the run's allowance of requests or bytes is used up")
        self.requests += 1
        status, headers, body = self._open(url, cap)
        self.bytes += len(body)
        return status, headers, body


def scan_source(qc: Any, fetcher: Any, meter: Meter, src: dict[str, Any], watch: list[dict[str, Any]]) -> dict[str, Any]:
    """One source: its index page, then its new documents. Returns what
    was found; the state is the report step's to change."""
    out: dict[str, Any] = {"problem": "", "links": 0, "storm": False, "noted": [], "docs": [], "left": 0,
                           "titles": []}
    home = urllib.parse.urlsplit(src["index_url"]).netloc.lower()
    try:
        got = fetcher.fetch(src["index_url"], cap=qc.MAX_HTML_BYTES)
        if got.status != 200:
            raise qc.FetchError(f"HTTP {got.status}")
        if urllib.parse.urlsplit(got.final_url).netloc.lower() != home:
            raise qc.FetchError("the index page answered from another host")
    except qc.FetchError as exc:
        out["problem"] = f"index page not read: {plain(exc, 160)}"
        return out
    html = got.body.decode(qc._charset(got.body, got.ctype, True), "replace")
    links = document_links(got.final_url, html, src)
    out["links"] = len(links)
    if not links:
        out["problem"] = "the index page lists no document under the path prefixes"
        return out
    done, pending = set(src.get("seen") or []), dict(src.get("pending") or {})
    new = [(url, title) for url, title in links if url not in done]
    fresh = [x for x in new if x[0] not in pending]
    # The titles of everything new, whether or not the document is read.
    for url, title in fresh:
        age = days_between(date_in(url), src.get("now", ""))
        if age is not None and age > TITLE_MAX_AGE_DAYS:
            continue                       # an old document on a list seen for the first time
        found = title_hits(title, watch, src)
        if found and len(out["titles"]) < TITLES_PER_SOURCE:
            out["titles"].append({"url": url, "title": title, "hits": found})
    limit = src["max_new"]
    if not src.get("known"):
        # First sight: note the list, read only the first few.
        limit = min(limit, FIRST_SIGHT)
        out["noted"] = [url for url, _ in new[limit:]]
        new = new[:limit]
    elif len(fresh) > STORM_COUNT or (len(links) >= 8 and len(fresh) > STORM_SHARE * len(links)):
        # A known source whose list is suddenly mostly new: its addresses
        # changed, or a large batch was posted. Noted, not crawled.
        out["storm"], out["noted"] = True, [url for url, _ in fresh]
        new = [x for x in new if x[0] in pending]
    for url, title in new[:limit]:
        if meter.spent():
            break
        doc = {"url": url, "title": title, "state": DOC_STATES[2], "why": "", "sha256": "", "pages": 0,
               "text_pages": 0, "hits": []}
        try:
            got = fetcher.fetch(url, cap=DOC_BYTES)
            if got.status != 200:
                raise qc.FetchError(f"HTTP {got.status}")
            doc["sha256"] = hashlib.sha256(got.body).hexdigest()
            pages, total, why = document_pages(qc, got.body, got.ctype)
        except qc.FetchError as exc:
            pages, total, why = [], 0, f"not fetched: {plain(exc, 120)}"
        doc["pages"], doc["text_pages"], doc["why"] = total, len(pages), plain(why, 160)
        if pages:
            doc["state"] = DOC_STATES[1] if total and (total - len(pages)) / total > PARTLY else DOC_STATES[0]
            doc["hits"] = find_hits(pages, watch, src)
        out["docs"].append(doc)
    read = {d["url"] for d in out["docs"]} | set(out["noted"])
    out["left"] = sum(1 for url, _ in links if url not in done and url not in read)
    return out


def scan(job: dict[str, Any], *, fetcher: Any = None, qc: Any = None) -> dict[str, Any]:
    """Every request and every document of a run. Reads the job, touches
    no other file, returns the result."""
    qc = qc or quotecheck()
    if fetcher is None:
        pin_names()
        fetcher = qc.Fetcher()
    meter = Meter(qc, fetcher, MAX_REQUESTS, MAX_BYTES)
    sources = job["sources"]
    # A different source first each day, so the last one is not always the
    # one the allowance runs out on.
    turn = datetime.date.fromisoformat(job["now"][:10]).toordinal() % len(sources)
    result: dict[str, Any] = {"schema": SCHEMA, "sources": {}}
    for src in sources[turn:] + sources[:turn]:
        result["sources"][src["name"]] = scan_source(qc, fetcher, meter, {**src, "now": job["now"]}, job["watch"])
    result["requests"], result["bytes"] = meter.requests, meter.bytes
    return result


# ------------------------------------------------------- prepare and report

def read_state(path: Path) -> dict[str, Any]:
    """The state file, or a fresh state. A file that cannot be read is
    refused, not replaced: without it every document would look new."""
    if not path.exists():
        return {"schema": SCHEMA, "sources": {}, "hits": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not (isinstance(state, dict) and state.get("schema") == SCHEMA
                and isinstance(state.get("sources"), dict) and isinstance(state.get("hits"), list)):
            raise ValueError("not a state file of this version")
    except (OSError, ValueError) as exc:
        raise Refuse(f"{path} cannot be read ({plain(exc, 120)}): look at it, or move it aside to start "
                     "again (every source is then seen as new: noted, with its first documents read)") from None
    return state


def prepare(sources: list[dict[str, Any]], run_dirs: list[Path], decisions: Path | None, state_dir: Path,
            now: str | None = None) -> dict[str, Any]:
    """The job: the sources with what each has seen, and the watch list."""
    if state_dir.resolve().is_relative_to(REPO.resolve()) and REPO.joinpath(".git").exists():
        raise Refuse("the state folder may not be inside the checkout (a run folder or a lead folder least of all)")
    state = read_state(state_dir / STATE_NAME)
    now = now or utc_now()
    watch = watch_list(run_dirs, decisions)
    job_sources = []
    for src in sources:
        st = state["sources"].get(src["name"]) or {}
        seen = st.get("seen") or {}
        job_sources.append({**src, "known": bool(st.get("known")),
                            "seen": sorted(u for u, r in seen.items() if r.get("state") != "pending"),
                            "pending": {u: int(r.get("tries", 0)) for u, r in seen.items()
                                        if r.get("state") == "pending"}})
    return {"schema": SCHEMA, "now": now, "sources": job_sources, "watch": watch,
            "runs": [str(d) for d in run_dirs]}


def scan_view(job: dict[str, Any]) -> dict[str, Any]:
    """The job as the scanning account sees it: search keys only. No run
    folder path, no grade, rank or mark of a parcel."""
    return {"schema": job["schema"], "now": job["now"], "sources": job["sources"],
            "watch": [{k: w[k] for k in ("parcel_id", "id_key", "address", "street", "jurisdiction")}
                      for w in job["watch"]]}


def checked_result(result: Any, job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The scan's result, kept only as far as it fits the job: this job's
    sources, addresses on a source's own host and paths, parcels of the
    watch list, the fixed kinds and states, text reduced again. Anything
    else is dropped; a result that is not one at all is refused."""
    if not isinstance(result, dict) or result.get("schema") != SCHEMA or not isinstance(result.get("sources"), dict):
        raise Refuse("the scan's result is not a result file")
    watched = {w["parcel_id"] for w in job["watch"]}
    out: dict[str, dict[str, Any]] = {}
    for src in job["sources"]:
        got = result["sources"].get(src["name"])
        if not isinstance(got, dict):
            out[src["name"]] = {"problem": "the scan gave no answer for this source", "links": 0, "storm": False,
                                "noted": [], "docs": [], "left": 0, "titles": []}
            continue
        docs = []
        for d in (got.get("docs") if isinstance(got.get("docs"), list) else [])[:MAX_NEW_LIMIT]:
            if not isinstance(d, dict) or not link_allowed(d.get("url"), src) or d.get("state") not in DOC_STATES:
                continue
            hits = []
            for h in (d.get("hits") if isinstance(d.get("hits"), list) else [])[:HITS_PER_DOC]:
                if not isinstance(h, dict) or h.get("kind") not in KINDS[:3]:
                    continue
                pid = h.get("parcel_id") if h["kind"] != KINDS[2] else ""
                if h["kind"] != KINDS[2] and pid not in watched:
                    continue
                page = h.get("page")
                hits.append(checked_hit(h, pid))
            def count(key: str) -> int:
                v = d.get(key)
                return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1_000_000 else 0
            sha = d.get("sha256")
            docs.append({"url": d["url"], "title": plain(d.get("title"), 80), "state": d["state"],
                         "why": plain(d.get("why"), 160), "sha256": sha if isinstance(sha, str) and SHA_RE.fullmatch(sha) else "",
                         "pages": count("pages"), "text_pages": count("text_pages"), "hits": hits})
        noted = [u for u in (got.get("noted") if isinstance(got.get("noted"), list) else [])[:INDEX_LINKS]
                 if link_allowed(u, src)]
        titles = []
        for t in (got.get("titles") if isinstance(got.get("titles"), list) else [])[:TITLES_PER_SOURCE]:
            if not isinstance(t, dict) or not link_allowed(t.get("url"), src):
                continue
            hits = []
            for h in (t.get("hits") if isinstance(t.get("hits"), list) else [])[:HITS_PER_DOC]:
                if not isinstance(h, dict) or h.get("kind") not in TITLE_KINDS:
                    continue
                pid = h.get("parcel_id") if h["kind"] != KINDS[4] else ""
                if h["kind"] != KINDS[4] and pid not in watched:
                    continue
                hits.append(checked_hit(h, pid))
            if hits:
                titles.append({"url": t["url"], "title": plain(t.get("title"), 80), "hits": hits})
        def number(key: str) -> int:
            v = got.get(key)
            return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= INDEX_LINKS else 0
        out[src["name"]] = {"problem": plain(got.get("problem"), 200), "links": number("links"),
                            "storm": got.get("storm") is True, "noted": noted, "docs": docs,
                            "left": number("left"), "titles": titles}
    return out


def checked_hit(h: dict[str, Any], pid: str) -> dict[str, Any]:
    def whole(key: str, most: int) -> int:
        v = h.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= most else 0
    return {"kind": h["kind"], "parcel_id": pid, "page": whole("page", 100_000),
            "others": whole("others", 100_000), "text": plain(h.get("text"), 2 * SNIPPET + 80)}


def report(job: dict[str, Any], result: Any, state_dir: Path) -> dict[str, int]:
    """Check the result, update the state, write the report and the counts."""
    ch = changes()
    now = job["now"]
    found = checked_result(result, job)
    state = read_state(state_dir / STATE_NAME)
    by_pid = {w["parcel_id"]: w for w in job["watch"]}
    counts = {"sources": len(job["sources"]), "watched": len(job["watch"]), "read": 0, "partly": 0,
              "not_read": 0, "gave_up": 0, "scans": 0, "noted": 0, "left": 0, "failed": 0, "failing": 0,
              "recovered": 0,
              "stale": 0, "storms": 0, "parcel_hits": 0, "zoning_hits": 0, "no_watch_list": int(not job["watch"])}
    notes, new_hits = [], []
    said = {(h.get("url"), h.get("kind"), h.get("parcel_id")) for h in state["hits"]}
    for position, src in enumerate(job["sources"], 1):
        name, got = ch.cell(src["name"]), found[src["name"]]
        st = state["sources"].setdefault(src["name"], {})
        seen: dict[str, Any] = st.setdefault("seen", {})
        if got["problem"]:
            if st.get("fail_day") != now[:10]:             # one strike a day, however often it is run
                st["failures"], st["fail_day"] = int(st.get("failures", 0)) + 1, now[:10]
            counts["failed"] += 1
            n = st["failures"]
            if n == FAIL_DAYS or (n > FAIL_DAYS and (n - FAIL_DAYS) % FAIL_REPEAT_DAYS == 0):
                counts["failing"] += 1
            notes.append(f"Source {position} ({name}): {got['problem']} ({n} days running). No hit from it "
                         "means nothing while this lasts.")
            continue
        if int(st.get("failures", 0)) >= FAIL_DAYS:
            counts["recovered"] += 1
            notes.append(f"Source {position} ({name}) answers again after {st['failures']} failed days.")
        st["failures"], st["known"] = 0, True
        for url in got["noted"]:
            seen.setdefault(url, {"first": now, "state": "noted"})
        counts["noted"] += len(got["noted"])
        if got["storm"]:
            counts["storms"] += 1
            notes.append(f"Source {position} ({name}): {len(got['noted'])} documents were suddenly new at once "
                         "and were noted, not read. Either its addresses changed or a large batch was posted: "
                         "open its index page yourself.")
        elif got["noted"]:
            notes.append(f"Source {position} ({name}) is new to the watch: {len(got['noted'])} documents already "
                         "on its list were noted, not read.")
        for t in got["titles"]:
            for h in t["hits"]:
                if (t["url"], h["kind"], h["parcel_id"]) in said:
                    continue               # a title already reported on an earlier run
                w = by_pid.get(h["parcel_id"], {})
                new_hits.append({**h, "why": w.get("why", ""), "where": w.get("where", ""),
                                 "source": src["name"], "url": t["url"], "title": t["title"], "found": now})
        scans = []
        for d in got["docs"]:
            old = seen.get(d["url"]) or {}
            tries = int(old.get("tries", 0)) + 1
            record = {"first": old.get("first", now), "title": d["title"], "sha256": d["sha256"],
                      "pages": d["pages"], "text_pages": d["text_pages"]}
            if d["state"] == DOC_STATES[2] and d["pages"] and not d["text_pages"]:
                # A scan: the same bytes will hold no text tomorrow either.
                record.update(state="scan", tries=tries, why=d["why"])
                counts["scans"] += 1
                scans.append(link(d["url"]))
            elif d["state"] == DOC_STATES[2]:
                gave_up = tries >= MAX_TRIES
                record.update(state="gave up" if gave_up else "pending", tries=tries, why=d["why"])
                counts["gave_up" if gave_up else "not_read"] += 1
                if gave_up:
                    notes.append(f"Source {position} ({name}): a document could not be read on {MAX_TRIES} runs "
                                 f"({d['why'] or 'no reason given'}); open it yourself: {link(d['url'])}")
            else:
                record["state"] = d["state"]
                counts["read" if d["state"] == DOC_STATES[0] else "partly"] += 1
                st["last_new"] = now
                if d["state"] == DOC_STATES[1]:
                    notes.append(f"Source {position} ({name}): only {d['text_pages']} of {d['pages']} pages of a "
                                 f"document hold text (the rest are scans): {link(d['url'])}")
                for h in d["hits"]:
                    w = by_pid.get(h["parcel_id"], {})
                    new_hits.append({**h, "why": w.get("why", ""), "where": w.get("where", ""),
                                     "source": src["name"], "url": d["url"], "title": d["title"], "found": now})
            seen[d["url"]] = record
        if scans:
            notes.append(f"Source {position} ({name}): {len(scans)} documents are scans without text and could "
                         "not be searched (their titles were): " + ", ".join(scans))
        counts["left"] += got["left"]
        st.setdefault("last_new", st.get("first_seen", now))
        st.setdefault("first_seen", now)
        quiet = days_between(st["last_new"], now)
        if quiet is not None and quiet > src["expect_every_days"]:
            if st.get("stale_said") != st["last_new"]:
                counts["stale"] += 1
                st["stale_said"] = st["last_new"]
            notes.append(f"Source {position} ({name}): no new document for {quiet} days (expected at least every "
                         f"{src['expect_every_days']}). Is its index page still the right one?")
        for url in sorted(seen, key=lambda u: seen[u].get("first", ""))[:max(0, len(seen) - SEEN_KEPT)]:
            del seen[url]
    counts["parcel_hits"] = sum(1 for h in new_hits if h["kind"] in PARCEL_KINDS)
    counts["zoning_hits"] = sum(1 for h in new_hits if h["kind"] not in PARCEL_KINDS)
    state["hits"] = (new_hits + state["hits"])[:HITS_KEPT]
    state["last_run"], state["runs"] = now, int(state.get("runs", 0)) + 1
    if not job["watch"]:
        notes.insert(0, "There is no watch list: no finished full run was found. Only industrial zoning words "
                        "are looked for.")
    for run_dir in job.get("runs", []):
        age = days_between(datetime.datetime.fromtimestamp(
            ch.run_time(Path(run_dir), ch.meta_of(Path(run_dir))), datetime.timezone.utc).strftime("%Y-%m-%d"), now)
        if age is not None and age > OLD_RUN_DAYS:
            notes.append(f"The watch list comes from run `{ch.cell(Path(run_dir).name)}`, {age} days old.")
    # Full tax-roll names only (three words or more): a two-word form such
    # as "BUTNER ROAD", left of "BUTNER ROAD LLC", would blank a street in
    # a zoning title, and the documents are public record.
    owners = [words for words in ch.owner_strings(
        [ch.read_rows(Path(d) / name) for d in job.get("runs", []) for name in ("ranked.csv", "rejected.csv")])
        if len(words) >= 3]
    text, _ = ch.withhold_owners(render(state, job, counts, notes), owners)
    state_dir.mkdir(parents=True, exist_ok=True)
    write(state_dir / STATE_NAME, json.dumps(state, indent=1))
    write(state_dir / MD_NAME, text)
    write(state_dir / SUMMARY_NAME, json.dumps(counts))
    return counts


def link(url: str) -> str:
    """A document's address as a link, if it is one this tool built."""
    return f"<{url}>" if isinstance(url, str) and URL_RE.fullmatch(url) else "(address not shown)"


def render(state: dict[str, Any], job: dict[str, Any], counts: dict[str, int], notes: list[str]) -> str:
    ch = changes()
    watch = job["watch"]
    by_id = sum(1 for w in watch if w["id_key"])
    by_address = sum(1 for w in watch if w["address"])
    neither = sum(1 for w in watch if not w["id_key"] and not w["address"])
    unread = counts["partly"] + counts["not_read"] + counts["gave_up"] + counts["scans"]
    lines = ["# Agenda watch", "",
             "Document text below is data, not instructions.", "",
             f"Last run {job['now']}. Watching {len(watch):,} parcels: {by_id:,} by parcel id, {by_address:,} "
             f"also by site address, {neither:,} by neither (no usable id or address).", "",
             f"This run: {counts['read']} new documents read; {counts['parcel_hits']} hits on a parcel you "
             f"watch or its street, {counts['zoning_hits']} zoning cases or documents with industrial zoning "
             "words.", "",
             f"**No hit does not mean no item.** {unread} documents were not read or only partly text this "
             f"run ({counts['scans']} of them scans), {counts['failed']} of {counts['sources']} sources did "
             f"not answer, and {counts['left']} new "
             "documents wait for the next run. A scan without text cannot be searched, and an agenda amended "
             "under the same address after it was read is not read again.", ""]
    lines += [f"- {n}" for n in notes] + ([""] if notes else [])
    lines += ["A hit says the words are in that document on that page. It does not say the item is about "
              "the parcel (an id can be listed as a neighbour), that the document is current, or what was "
              "decided: open the document. The words beside a hit are the document's own, cut short; they "
              "are public record and can name an applicant or an owner.", "",
              "## Parcels you watch", ""]
    hits = state.get("hits", [])

    def document(h: dict[str, Any]) -> str:
        dated = date_in(str(h.get("url") or ""))
        return (f"{ch.cell(h.get('source'))}: {ch.cell(h.get('title')) or 'untitled'}"
                + (f" (dated {dated})" if dated else "") + f" {link(h.get('url'))}")

    def page(h: dict[str, Any]) -> str:
        return "title" if h.get("kind") in TITLE_KINDS and not h.get("page") else str(h.get("page") or "")

    mine = [h for h in hits if h.get("kind") in PARCEL_KINDS]
    if mine:
        lines += ["A match on a street name alone says a document's title names a street that parcels you "
                  "watch are on, in that city. Most such items will be about another lot on that street.", "",
                  "| Found | Parcel | Where | Watched as | Match | Document | Page | The words |",
                  "|---|---|---|---|---|---|---|---|"]
        for h in mine:
            others = h.get("others") if isinstance(h.get("others"), int) else 0
            lines.append("| " + " | ".join([
                ch.cell(str(h.get("found", ""))[:10]),
                ch.pid_cell(str(h.get("parcel_id", ""))) + (f" and {others} more on that street" if others else ""),
                ch.cell(h.get("where")), ch.cell(h.get("why")), ch.cell(h.get("kind")), document(h), page(h),
                ch.cell(h.get("text"))]) + " |")
    else:
        lines.append("None so far.")
    lines += ["", "## Zoning cases and industrial zoning words", "",
              "A title holds a zoning case number or a zoning word; or a page holds a word for a zoning action "
              "near a word for an industrial district or use. It contains the words; it does not say what is "
              "proposed or where.", ""]
    zoning = [h for h in hits if h.get("kind") not in PARCEL_KINDS]
    if zoning:
        lines += ["| Found | Match | Document | Page | The words |", "|---|---|---|---|---|"]
        for h in zoning:
            lines.append("| " + " | ".join([ch.cell(str(h.get("found", ""))[:10]), ch.cell(h.get("kind")),
                                            document(h), page(h), ch.cell(h.get("text"))]) + " |")
    else:
        lines.append("None so far.")
    lines += ["", f"The newest {HITS_KEPT} hits are kept. Nothing here changes a score, a grade, a rank or a "
              "decision."]
    return "\n".join(lines) + "\n"


@contextlib.contextmanager
def one_run(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / LOCK_NAME, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refuse("another agenda watch is running") from None
        yield
    finally:
        os.close(fd)


def summary_line(counts: dict[str, int]) -> str:
    unread = counts["partly"] + counts["not_read"] + counts["gave_up"] + counts["scans"]
    return (f"{counts['read']} new documents read from {counts['sources']} sources: {counts['parcel_hits']} "
            f"hits on a parcel you watch or its street, {counts['zoning_hits']} zoning cases or documents "
            f"with industrial zoning words; {unread} not read or only partly text ({counts['scans']} scans); "
            f"{counts['failed']} sources did not answer.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "prepare"):
        p = sub.add_parser(name)
        p.add_argument("--sources", type=Path, required=True, help="the JSON list of agenda index pages")
        p.add_argument("--output-root", type=Path, help="screener/output: the newest full run of each county is watched")
        p.add_argument("--run", type=Path, action="append", default=[], help="a run folder to watch (instead)")
        p.add_argument("--decisions", type=Path, help="decisions.csv, read only, for marks made since the run")
        p.add_argument("--state", type=Path, required=True, help="the folder for state.json and agenda-watch.md")
        if name == "prepare":
            p.add_argument("--job", type=Path, required=True, help="the job file to write (trusted side)")
            p.add_argument("--scan-job", type=Path, required=True, help="the job as the scanning account gets it")
    s = sub.add_parser("scan")
    s.add_argument("job", type=Path)
    s.add_argument("result", type=Path)
    r = sub.add_parser("report")
    r.add_argument("--job", type=Path, required=True)
    r.add_argument("--result", type=Path, required=True)
    r.add_argument("--state", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "scan":
            job = json.loads(args.job.read_text(encoding="utf-8"))
            result = json.dumps(scan(job))
            if len(result) > RESULT_BYTES:
                raise Refuse("the result is larger than a result can be")
            write(args.result, result, 0o640)
            return 0
        if args.cmd == "report":
            if args.result.stat().st_size > RESULT_BYTES:
                raise Refuse("the result is larger than a result can be")
            job = json.loads(args.job.read_text(encoding="utf-8"))
            try:
                result = json.loads(args.result.read_text(encoding="utf-8"))
            except ValueError:
                raise Refuse("the scan's result is not JSON") from None
            with one_run(args.state):
                counts = report(job, result, args.state)
            print(summary_line(counts))
            return 0
        sources = read_sources(args.sources)
        runs = args.run or (newest_full_runs(args.output_root) if args.output_root else [])
        for d in runs:
            if not (d / "ranked.csv").is_file():
                raise Refuse(f"{d} has no ranked.csv")
        if args.cmd == "prepare":
            job = prepare(sources, runs, args.decisions, args.state)
            write(args.job, json.dumps(job))
            write(args.scan_job, json.dumps(scan_view(job)), 0o640)
            print(f"{len(job['sources'])} sources, {len(job['watch'])} parcels watched")
            return 0
        with one_run(args.state):
            job = prepare(sources, runs, args.decisions, args.state)
            counts = report(job, scan(scan_view(job)), args.state)
        print(summary_line(counts))
        print(f"written: {args.state / MD_NAME}")
    except Refuse as exc:
        print(f"agenda_watch: refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as exc:
        print(f"agenda_watch: refused: a file could not be read or written, or is not what it should be "
              f"({type(exc).__name__}: {plain(exc, 200)})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
