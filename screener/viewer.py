"""Site Plan Viewer: one self-contained HTML page holding a run's plans.

    python -m screener.viewer RUN_DIR --out OUT.html [--plans PLANS_DIR]
        [--decisions CSV] [--leads DIR ...] [--land land_today.json]
        [--include-notes] [--include-headlines] [--join-by-rank]

What goes in:
- the run's own F12 plans (RUN_DIR/briefs/NNN_<parcel>.svg), with their
  facts from ranked.csv;
- the site planner's plans (PLANS_DIR/plans.json from ``screener.siteplan``,
  or a prototype plans4.json) and the SVG files those name;
- decisions.csv: only the decision value, and the "probably taken" status
  read from the note. The note text itself only with --include-notes;
- lead folders (each parcel's findings.checked.json and lead.md): only the
  skeptic verdict, an enum. The lead.md "In one line" headline only with
  --include-headlines;
- land_today.json from ``screener.builtcheck``: the land class and what
  the aerial review saw.

Every input is joined by parcel id, never by rank alone: ranks belong to
one run. An input that names its run (``ranked_csv_sha256``) must name this
one. A planner file without that fingerprint or parcel ids (the prototype's
plans4.json) is joined by rank only when --join-by-rank says the operator
vouches it came from this run (reviews/37 R10).

Page safety (reviews/37 R1, R3, R11, R28):
- Owner names and mailing addresses never reach the page. The owner guard
  (``OwnerGuard``) normalises the page text and refuses it, writing
  nothing, when an owner or mailing string of the run appears in it.
- Every SVG is parsed and re-written from an allow-list of elements and
  attributes (no script, href, event handler, style, foreignObject or
  external url()). An SVG holding anything else is refused.
- The data goes into the page as JSON with < > & and U+2028/9 escaped,
  and the template is filled in one pass; a Content-Security-Policy lets
  only the page's own script run.
- File names come from fixed patterns and every path must stay inside
  its folder. The page is written atomically.

The helpers in the first part of this file (run fingerprint, owner guard,
atomic writes, CSV cells, the --out check) are shared with
``screener.builtcheck``.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).with_name("viewer_template.html")
# The briefs' file-name rule (report.write_all): letters, digits, _ and -.
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A planner key: a rank, a "115+170" rank list or an --assemble NAME.
KEY_RE = re.compile(r"^[A-Za-z0-9_+-]{1,64}$")
PLAN_SVG_RE = re.compile(r"^plan4?-[A-Za-z0-9_-]{1,64}\.svg$")
BRIEF_SVG_RE = re.compile(r"^(\d{1,6})_([A-Za-z0-9_-]{1,64})\.svg$")
FLAG_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,59}$")
PLAN_FILES = ("plans.json", "plans4.json")   # the planner's, then the prototype's
MAX_PAGE_BYTES = 15_000_000   # refuse above this (reviews/37 R28)
WARN_PAGE_BYTES = 5_000_000   # say so above this
TEXT_CAP = 600                # free text from reviews, notes, headlines (R27)
NOTE_CAP = 400                # one flag note
TITLE_CAP = 200

# Land classes written by screener.builtcheck (land_today).
LAND_CLASSES = ("raw_land", "partly_usable", "not_raw_land", "unclear")
# quotecheck.SKEPTIC_VERDICTS, repeated so the viewer reads no lead text.
SKEPTIC_VERDICTS = ("survives", "weakened", "killed")
# Status shown per parcel, from decisions.csv.
STATUSES = ("open", "unchecked", "doubt", "taken", "drop", "listed")
# A multi-parcel plan shows its worst member: land class first to last
# (None = not reviewed, worse than a reviewed usable class), status most
# negative first.
LAND_WORST_FIRST = ("not_raw_land", "unclear", None, "partly_usable", "raw_land")
STATUS_WORST_FIRST = ("drop", "taken", "doubt", "unchecked", "listed", "open")


class Refused(RuntimeError):
    """An input or the page would break a safety rule; nothing is written."""


# --------------------------------------------------------------------------
# Shared artifact helpers (also used by screener.builtcheck)
# --------------------------------------------------------------------------

def ranked_sha256(run_dir: Path) -> str:
    return hashlib.sha256((Path(run_dir) / "ranked.csv").read_bytes()).hexdigest()


def run_identity(run_dir: Path) -> dict[str, str]:
    """What every output carries so a later join can tell its run."""
    return {"name": Path(run_dir).name, "ranked_csv_sha256": ranked_sha256(run_dir)}


def fingerprint_of(obj: Any) -> str | None:
    """The ranked.csv sha256 an input says it was made from, or None."""
    if not isinstance(obj, dict):
        return None
    for holder in (obj, obj.get("run"), obj.get("meta"),
                   (obj.get("meta") or {}).get("run") if isinstance(obj.get("meta"), dict) else None):
        if isinstance(holder, dict) and isinstance(holder.get("ranked_csv_sha256"), str):
            return holder["ranked_csv_sha256"]
    return None


def read_ranked(run_dir: Path) -> list[dict[str, str]]:
    with open(Path(run_dir) / "ranked.csv", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# Owner guard (reviews/37 R1). The text is normalised (HTML entities and
# JSON escapes decoded, NFKC, casefold, every run of punctuation and space
# made one space) and searched for each owner and mailing string of the
# run, whole words only. Owner names are also searched with their company
# suffixes removed ("ACME HOLDINGS LLC" -> "acme holdings") when what is
# left has 8 characters or more.
_JSON_ESCAPE = re.compile(r'\\(?:u([0-9a-fA-F]{4})|[nrtbf"\\/])')
_NON_WORD = re.compile(r"[\W_]+")
OWNER_SUFFIXES = frozenset({
    "llc", "l", "c", "inc", "incorporated", "lp", "llp", "lllp", "ltd", "limited", "trust",
    "tr", "trs", "trustee", "trustees", "co", "corp", "corporation", "company", "plc",
    "pllc", "pc", "pa", "et", "al", "etal", "etux", "the",
})
MIN_FULL_CHARS = 7    # the prototype matched owner strings longer than 6
MIN_CORE_CHARS = 8
# One-word owner names that are ordinary words in an aerial note or a flag
# ("a cemetery just outside the line"), not a lead to an owner.
GENERIC_OWNER_WORDS = frozenset({"cemetery", "church", "unknown", "owner", "estate", "heirs",
                                 "vacant", "railroad", "railway"})


# Words that mark an owner string as not an individual's name (a public
# body, a church, a company without a suffix): no natural-order name form
# is added for those.
NOT_PERSON_WORDS = frozenset({
    "of", "and", "for", "county", "city", "state", "board", "church", "department", "dept", "authority",
    "united", "states", "georgia", "ga", "fulton", "atlanta", "association", "assn", "assoc", "bank", "fund",
    "group", "development", "dev", "investments", "investment", "invest", "realty", "land", "lands",
    "enterprises", "ministries", "ministry", "school", "schools", "district", "housing", "partners",
    "properties", "property", "holdings", "family", "living", "revocable", "irrevocable", "estate", "heirs",
    "baptist", "methodist", "temple", "center", "club", "services", "management", "capital", "ventures",
    "industries", "industrial", "logistics", "railroad", "railway", "power", "electric", "water", "dot",
    "transportation", "commission", "federal", "national", "american", "south", "north", "east", "west",
    "park", "union", "point", "college", "palmetto", "fairburn", "hills", "chattahoochee", "airport",
    "rd", "road", "st", "street", "ave", "hwy", "cemetery", "unknown", "owner", "vacant", "farm", "farms",
    "homes", "home", "builders", "construction", "storage", "warehouse", "equity", "trustee", "trust",
}) - {"and"}


# Spellings that read the same on both sides of the match (the guard's
# strings and the text searched): the USPS street suffixes and unit words,
# spelled out or abbreviated, and "&" / "and".
STREET_WORDS = {
    "road": "rd", "drive": "dr", "street": "st", "avenue": "ave", "av": "ave", "parkway": "pkwy",
    "pky": "pkwy", "boulevard": "blvd", "circle": "cir", "court": "ct", "highway": "hwy",
    "lane": "ln", "place": "pl", "terrace": "ter", "trail": "trl", "way": "wy", "square": "sq",
    "crossing": "xing", "point": "pt", "suite": "ste", "apartment": "apt", "building": "bldg",
    "floor": "fl", "room": "rm", "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
}
UNIT_WORDS = frozenset({"ste", "unit", "apt", "bldg", "fl", "rm", "lot", "spc"})
# NOT_PERSON_WORDS as normal_tokens spells them ("point" -> "pt").
NOT_PERSON_WORDS = NOT_PERSON_WORDS | {STREET_WORDS.get(w, w) for w in NOT_PERSON_WORDS}


# An apostrophe after a one-letter prefix is dropped, not read as a
# break: "O'Brien", "O’BRIEN" and "OBRIEN" read the same ("Smith's"
# still reads "smith s").
_PREFIX_APOSTROPHE = re.compile(r"(?<!\w)(\w)['\u2019\u02bc](?=\w\w)")
# A company suffix spelled in single letters ("L.L.C.", "L L C", "Inc.")
# reads as the one word.
SPELLED_SUFFIXES = frozenset({"llc", "llp", "lllp", "lp", "lc", "pc", "pllc", "inc"})


def normal_tokens(text: str, fold: bool = True) -> list[str]:
    """The words of a text as the owner guard compares them. `fold` maps
    the street and compass words to one spelling (STREET_WORDS); the guard
    measures its length limits on the unfolded words."""
    text = html.unescape(text)
    text = _JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)) if m.group(1) else " ", text)
    text = unicodedata.normalize("NFKC", text).casefold().replace("&", " and ")
    text = _PREFIX_APOSTROPHE.sub(r"\1", text)
    raw = [t for t in _NON_WORD.split(text) if t]
    toks = [STREET_WORDS.get(t, t) for t in raw] if fold else raw
    # "P.O. Box" and "PO BOX" read the same; a run of single letters that
    # spells a company suffix is joined ("l l c" -> "llc").
    out: list[str] = []
    i = 0
    while i < len(toks):
        if toks[i] == "p" and i + 2 < len(toks) and toks[i + 1] == "o" and toks[i + 2] == "box":
            out.append("po")
            i += 2
            continue
        if len(toks[i]) == 1 and toks[i].isalpha():
            j = i
            while j < len(toks) and len(toks[j]) == 1 and toks[j].isalpha():
                j += 1
            run = "".join(toks[i:j])
            if j - i >= 2 and run in SPELLED_SUFFIXES:
                out.append(run)
                i = j
                continue
        out.append(toks[i])
        i += 1
    return out


def street_base(street: str) -> list[str]:
    """A mailing street line without its unit ("4410 OLD STAGE RD STE 300"
    -> "4410 old stage rd"; "... RD # 300" -> "... rd"), so a text that
    drops the suite still matches."""
    toks = normal_tokens(street or "")
    for i, t in enumerate(toks):
        if i >= 2 and t in UNIT_WORDS:
            return toks[:i]
    if len(toks) >= 3 and toks[-1].isdigit() and not toks[-2].isdigit() and "#" in (street or ""):
        return toks[:-1]
    return toks


_TAG = re.compile(r"<[^<>]*>")


def markup_text(text: str) -> str:
    """The text of an SVG or HTML fragment with every tag replaced by a
    space, so an owner name split across elements ("A <tspan>B</tspan>")
    or wrapped over two text lines reads as one run of words."""
    return _TAG.sub(" ", text)


# Words after an individual's name on the tax roll that are not part of it.
PERSON_TRAILERS = frozenset({"et", "al", "etal", "etux", "ux", "jr", "sr", "ii", "iii", "iv"})
# "L.L.C.", "L.P.", "L.L.P." spelled with dots read as single letters.
_DOTTED_SUFFIXES = (("l", "l", "c"), ("l", "l", "p"), ("l", "p"), ("l", "c"), ("p", "c"))


# Surname particles kept with the surname ("DE LA CRUZ MARIA" -> "maria
# de la cruz"), and one-letter surname prefixes the roll may write apart
# ("O BRIEN PATRICK" -> "obrien").
SURNAME_PARTICLES = frozenset({"de", "la", "del", "della", "van", "von", "der", "den", "di", "da", "du",
                               "le", "dos", "das", "el", "mc", "mac"})
SURNAME_PREFIX_LETTERS = frozenset({"o", "d"})


def _person_words(toks: list[str]) -> list[str]:
    """One person's words without initials and trailers, a split one-letter
    surname prefix joined to the surname."""
    toks = list(toks)
    while toks and toks[-1] in PERSON_TRAILERS:
        toks.pop()
    if len(toks) >= 2 and toks[0] in SURNAME_PREFIX_LETTERS:
        toks = [toks[0] + toks[1]] + toks[2:]
    return [t for t in toks if len(t) > 1 and t not in PERSON_TRAILERS]


def _surname_split(names: list[str]) -> tuple[str, list[str]]:
    """(surname with its particles, the given names after it)."""
    k = 0
    while k < len(names) - 2 and names[k] in SURNAME_PARTICLES:
        k += 1
    return " ".join(names[:k + 1]), names[k + 1:]


def person_name_forms(owner: str) -> list[str]:
    """An individual owner as the tax roll writes it ("SMITH JOHN A",
    "SMITH JOHN L", "SMITH JOHN ET AL") read in both orders without
    initials ("smith john", "john smith"), the way a person writes a name in
    a note. Middle initials and trailing ET AL / JR words are dropped first;
    empty for anything that is not plainly a person's name (a company suffix
    such as LLC or a dotted L.L.C., or a NOT_PERSON_WORDS word).

    A couple ("WALKER DONALD & BETTY") gives both people's forms ("donald
    walker", "betty walker") and the couple's ("donald and betty walker");
    a second person with two words ("& SMITH BETTY") also gives those two
    words as a name of its own. Surname particles stay with the surname
    ("maria de la cruz"), and a split "O BRIEN" reads "obrien"."""
    toks = normal_tokens(owner or "")
    if not 2 <= len(toks) <= 8 or not all(t.isalpha() for t in toks):
        return []
    for pat in _DOTTED_SUFFIXES:
        n = len(pat)
        if any(tuple(toks[i:i + n]) == pat for i in range(len(toks) - n + 1)):
            return []
    words = [t for t in toks if len(t) > 1 and t != "and" and t not in PERSON_TRAILERS]
    if any(t in OWNER_SUFFIXES or t in NOT_PERSON_WORDS for t in words):
        return []
    second: list[str] = []
    if "and" in toks:                 # "WALKER DONALD & BETTY"
        cut = toks.index("and")
        toks, second = toks[:cut], _person_words(toks[cut + 1:])
    names = _person_words(toks)
    if not 2 <= len(names) <= 4:
        return []
    surname, given = _surname_split(names)
    first = given[0]
    forms = [f"{surname} {first}", f"{first} {surname}"]
    if second and len(second) <= 4:
        other = second[0]
        forms += [f"{other} {surname}", f"{surname} {other}", f"{first} and {other} {surname}"]
        if len(second) >= 2:
            s2, g2 = _surname_split(second)
            if g2:
                forms += [f"{s2} {g2[0]}", f"{g2[0]} {s2}"]
    return list(dict.fromkeys(forms))


class OwnerDataFound(Refused):
    def __init__(self, artifact: str, counts: Counter):
        what = ", ".join(f"{k} x{n}" for k, n in sorted(counts.items()))
        super().__init__(f"{artifact}: owner data found ({what}); nothing was written")
        self.counts = counts


@dataclass
class OwnerGuard:
    """Refuses text that holds an owner name or owner mailing address."""
    by_first: dict[str, list[tuple[tuple[str, ...], str]]] = field(default_factory=dict)

    def add(self, text: str, what: str, *, min_chars: int = MIN_FULL_CHARS,
            skip: set[tuple[str, ...]] | frozenset = frozenset()) -> None:
        toks = tuple(normal_tokens(text or ""))
        # The length limit is measured on the words as written, before the
        # street words are folded ("MILL ROAD" is 9 characters although it
        # is matched as "mill rd").
        size = len(" ".join(normal_tokens(text or "", fold=False)))
        if not toks or size < min_chars or toks in skip:
            return
        if len(toks) == 1 and toks[0] in GENERIC_OWNER_WORDS:
            return
        if not any(ch.isalpha() for ch in "".join(toks)):
            return
        entry = (toks, what)
        bucket = self.by_first.setdefault(toks[0], [])
        if entry not in bucket:
            bucket.append(entry)

    @classmethod
    def from_rows(cls, rows: Iterable[dict[str, str]]) -> "OwnerGuard":
        rows = list(rows)
        guard = cls()
        # Two kinds of published text are skipped. (1) Place names: the run's
        # jurisdiction names are in every artifact, so an owner named after
        # a city (a city's own land, or "<CITY> LLC" once its suffix is
        # gone) is skipped for every row. (2) The row's OWN site address: a
        # mailing address that is the same parcel's site address (an
        # owner-occupied house mailing from "12 X RD" for the parcel "12 X
        # RD REAR"). Another row's site address is never skipped here; an
        # artifact that writes it passes it to hits() as `public`. One
        # exception, for one-word strings only: a single word that is a word
        # of any site address of the run ("HILLSIDE LLC" -> "hillside", a
        # street-name word) is an ordinary word in an aerial note, so it is
        # skipped like a place name. A multi-word string (a name, a mailing
        # line) is never skipped because of another row.
        places: set[tuple[str, ...]] = set()
        for r in rows:
            places |= _subphrases(r.get("jurisdiction") or "")
            places |= {(t,) for t in normal_tokens(r.get("site_address") or "")}
        for r in rows:
            public = places | _subphrases(r.get("site_address") or "")
            owner = r.get("owner") or ""
            guard.add(owner, "owner", skip=public)
            toks = normal_tokens(owner, fold=False)
            while toks and toks[-1] in OWNER_SUFFIXES:
                toks.pop()
            while toks and toks[0] == "the":
                toks.pop(0)
            guard.add(" ".join(toks), "owner", min_chars=MIN_CORE_CHARS, skip=public)
            for form in person_name_forms(owner):
                guard.add(form, "owner", min_chars=MIN_CORE_CHARS, skip=public)
            mailing = r.get("owner_mailing") or ""
            guard.add(mailing, "owner_mailing", skip=public)
            street = mailing.split(",")[0]
            guard.add(street, "owner_mailing", min_chars=MIN_CORE_CHARS, skip=public)
            guard.add(" ".join(street_base(street)), "owner_mailing", min_chars=MIN_CORE_CHARS, skip=public)
        return guard

    @classmethod
    def from_run(cls, run_dir: Path) -> "OwnerGuard":
        """Owners of the ranked parcels and, when present, of the rejected
        ones (an assemblage member may sit in either)."""
        rows = read_ranked(run_dir)
        rejected = Path(run_dir) / "rejected.csv"
        if rejected.exists():
            with open(rejected, encoding="utf-8-sig", newline="") as fh:
                rows += list(csv.DictReader(fh))
        return cls.from_rows(rows)

    def hits(self, text: str, public: Iterable[str] = ()) -> Counter:
        """Owner strings found in text, by field. A string that is part of
        one of the `public` texts (the site addresses this artifact itself
        writes: its sites' and county records' addresses) is skipped there,
        for the same reason as above."""
        skip: set[tuple[str, ...]] = set()
        for p in public:
            skip |= _subphrases(p or "")
        toks = normal_tokens(text)
        found: set[tuple[int, str]] = set()     # (where, field): a name and its core count once
        for i, t in enumerate(toks):
            for pat, what in self.by_first.get(t, ()):
                if tuple(toks[i:i + len(pat)]) == pat and pat not in skip:
                    found.add((i, what))
        return Counter(what for _, what in found)

    def check(self, text: str, artifact: str, public: Iterable[str] = ()) -> None:
        """Raise OwnerDataFound (field names and counts only, never the text)."""
        counts = self.hits(text, public)
        if counts:
            raise OwnerDataFound(artifact, counts)


def _subphrases(text: str, longest: int = 12) -> set[tuple[str, ...]]:
    t = normal_tokens(text)
    return {tuple(t[a:b]) for a in range(len(t)) for b in range(a + 1, min(len(t), a + longest) + 1)}


def atomic_write(path: Path, data: str | bytes) -> None:
    """Write through a temp file in the same folder and os.replace it, so a
    failure leaves the old file as it was (reviews/37 R11)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        # mkstemp makes the file private (0600); give it the mode a plain
        # write would (the old file's, else 0644).
        os.chmod(tmp, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: Any) -> Any:
    """A text cell a spreadsheet would read as a formula gets a leading '
    (reviews/37 R27). Numbers are left alone."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def check_out_path(out: Path, run_dir: Path | None = None, *, repo: Path = REPO_ROOT) -> Path:
    """Refuse an output path equal to the run folder, or inside the repo
    but not git-ignored (it would leave untracked files under screener/,
    which the evaluator refuses: reviews/37 R7, R26)."""
    out = Path(out).resolve()
    if run_dir is not None and out == Path(run_dir).resolve():
        raise Refused("--out is the run folder itself; choose another folder")
    repo = repo.resolve()
    if out == repo or out.is_relative_to(repo):
        try:
            res = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", str(out)],
                                 capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise Refused(f"cannot ask git whether {out} is ignored; write outside the repo")
        if res.returncode != 0:
            raise Refused(f"{out} is inside the repo and not git-ignored; "
                          "use screener/output/ or a folder outside the repo")
    return out


def inside(base: Path, name: str, pattern: re.Pattern) -> Path:
    """base/name when name matches the pattern and the path stays in base."""
    if not isinstance(name, str) or not pattern.match(name):
        raise Refused(f"file name not allowed: {name!r}")
    base = Path(base).resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise Refused(f"file name escapes {base}: {name!r}")
    return path


def cap(text: Any, limit: int) -> str | None:
    if text is None:
        return None
    s = str(text)
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# SVG sanitiser (reviews/37 R3)
# --------------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"
SVG_ELEMENTS = frozenset({
    "svg", "g", "title", "desc", "defs", "pattern", "clipPath", "path", "rect", "circle",
    "ellipse", "line", "polyline", "polygon", "text", "tspan",
})
TEXT_ELEMENTS = frozenset({"title", "desc", "text", "tspan"})
SVG_ATTRIBUTES = frozenset({
    "width", "height", "viewBox", "preserveAspectRatio", "font-family", "font-size",
    "font-weight", "font-style", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx",
    "ry", "dx", "dy", "d", "points", "fill", "fill-opacity", "fill-rule", "stroke",
    "stroke-width", "stroke-opacity", "stroke-dasharray", "stroke-linecap",
    "stroke-linejoin", "opacity", "class", "id", "text-anchor", "dominant-baseline",
    "letter-spacing", "transform", "patternUnits", "patternTransform", "clip-path",
    "clipPathUnits",
})
_ID = r"[A-Za-z][A-Za-z0-9_-]{0,39}"
_ID_RE = re.compile(rf"^{_ID}$")
_CLASS_RE = re.compile(r"^[A-Za-z0-9_ -]{0,80}$")
_URL_REF = re.compile(rf"url\(#{_ID}\)")
_TRANSFORM_RE = re.compile(r"^[A-Za-z0-9.,\s()+-]*$")
# Left after removing url(#id): none of these may appear in a value.
_BAD_IN_VALUE = ("url(", ":", "\\", "<", ">", "@", "expression", "&")
MAX_SVG_CHARS = 2_000_000


class UnsafeSVG(Refused):
    pass


def _local(tag: str) -> str:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        if ns != SVG_NS:
            raise UnsafeSVG(f"element in a foreign namespace: {name}")
        return name
    return tag


def _check_value(name: str, value: str) -> None:
    if name == "id":
        if not _ID_RE.match(value):
            raise UnsafeSVG("id not allowed")
        return
    if name == "class" and not _CLASS_RE.match(value):
        raise UnsafeSVG("class not allowed")
    if name in ("transform", "patternTransform") and not _TRANSFORM_RE.match(value):
        raise UnsafeSVG("transform not allowed")
    rest = re.sub(r"\s+", "", _URL_REF.sub("", value)).lower()
    if any(bad in rest for bad in _BAD_IN_VALUE):
        raise UnsafeSVG(f"value of {name} not allowed")


def sanitise_svg(text: str) -> str:
    """The SVG re-written from what an allow-list keeps, or UnsafeSVG.

    It is parsed as XML (after refusing a DOCTYPE or ENTITY, so no entity
    tricks) and written out again from the parsed tree: comments, processing
    instructions and CDATA wrappers vanish and every text is escaped, so the
    browser's HTML parser sees exactly what was checked. Whitespace between
    elements is dropped (R28)."""
    if not isinstance(text, str) or len(text) > MAX_SVG_CHARS:
        raise UnsafeSVG("SVG missing or too large")
    low = text.lower()
    if "<!doctype" in low or "<!entity" in low:
        raise UnsafeSVG("DOCTYPE or ENTITY in an SVG")
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise UnsafeSVG(f"SVG does not parse: {exc}") from None
    if _local(root.tag) != "svg":
        raise UnsafeSVG("the root element is not svg")
    out: list[str] = []
    _write(root, out, top=True)
    return "".join(out)


def _write(el: ET.Element, out: list[str], top: bool = False) -> None:
    name = _local(el.tag)
    if name not in SVG_ELEMENTS:
        raise UnsafeSVG(f"element not allowed: {name}")
    attrs = []
    for key, value in el.attrib.items():
        if key.startswith("{") or key not in SVG_ATTRIBUTES:
            raise UnsafeSVG(f"attribute not allowed: {key.split('}')[-1]}")
        _check_value(key, value)
        attrs.append(f' {key}="{html.escape(value, quote=True)}"')
    head = f"<{name}" + (f' xmlns="{SVG_NS}"' if top else "") + "".join(attrs)
    text = el.text or ""
    if text.strip() and name not in TEXT_ELEMENTS:
        raise UnsafeSVG(f"text inside {name}")
    if name not in TEXT_ELEMENTS:
        text = ""
    children = list(el)
    if not text and not children:
        out.append(head + "/>")
        return
    out.append(head + ">" + html.escape(text, quote=False))
    for child in children:
        _write(child, out)
        tail = child.tail or ""
        if tail.strip():
            if name not in TEXT_ELEMENTS:
                raise UnsafeSVG(f"text inside {name}")
            out.append(html.escape(tail, quote=False))
    out.append(f"</{name}>")


def svg_meta(svg: str) -> tuple[float | None, float | None]:
    """County-map acres from the legend and the scale bar (ft per SVG px)."""
    ac = re.search(r"Parcel \(county map\) ([\d,.]+) ac", svg)
    bar = (re.search(r'class="scale" d="M([\d.]+),[\d.]+ L[\d.]+,[\d.]+ L([\d.]+),', svg)
           or re.search(r'<path d="M([\d.]+),[\d.]+ L([\d.]+),[\d.]+" stroke="[^"]+" '
                        r'stroke-width="3"/><text[^>]*>[\d,]+ ft</text>', svg))
    ft = re.search(r">([\d,]+) ft</text>", svg)
    fpp = None
    if bar and ft:
        px = float(bar.group(2)) - float(bar.group(1))
        fpp = float(ft.group(1).replace(",", "")) / px if px > 0 else None
    try:
        acres = float(ac.group(1).replace(",", "")) if ac else None
    except ValueError:
        acres = None
    return acres, fpp


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def num(x: Any, digits: int | None = None) -> float | int | None:
    """A number from a CSV cell or JSON value, or None (never a string)."""
    if isinstance(x, bool) or x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    if digits is not None:
        return round(v, digits)
    return int(v) if v == int(v) and abs(v) < 1e15 else v


def text(x: Any, limit: int = TEXT_CAP) -> str | None:
    if x is None or isinstance(x, (dict, list)):
        return None
    return cap(str(x), limit)


def load_decisions(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {r["parcel_id"]: r for r in csv.DictReader(fh) if r.get("parcel_id")}


def status_of(decision: dict[str, str] | None) -> str:
    """A status enum from the decision (and a 'probably taken' note)."""
    if not decision:
        return "unchecked"
    value = (decision.get("decision") or "").strip()
    if value == "pass":
        return "drop"
    if value == "pursue":
        return "open"
    if value == "listed":
        return "listed"
    if "probably taken" in (decision.get("note") or "").lower():
        return "taken"
    return "doubt"


def lead_check(pid: str, roots: list[Path], with_headline: bool) -> tuple[str | None, str | None]:
    """The skeptic verdict (an enum) for a parcel's lead folder, and its
    'In one line' headline when asked for. The folder must hold this parcel."""
    folder = pid.replace(" ", "_")
    if not SLUG_RE.match(folder):
        return None, None
    for root in roots:
        base = Path(root).resolve()
        d = (base / folder).resolve()
        if d.parent != base:
            continue
        checked, md = d / "findings.checked.json", d / "lead.md"
        if not (checked.exists() and md.exists()):
            continue
        try:
            c = json.loads(checked.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(c, dict) or c.get("parcel_id") != pid:
            continue
        verdict = (c.get("skeptic") or {}).get("verdict") if isinstance(c.get("skeptic"), dict) else None
        verdict = verdict if verdict in SKEPTIC_VERDICTS else None
        headline = None
        if with_headline:
            m = re.search(r"\*\*In one line[^*]*\*\*\s*(.+)", md.read_text(encoding="utf-8"))
            headline = cap(m.group(1).strip(), TEXT_CAP) if m else None
        return verdict, headline
    return None, None


# builtcheck's words for a check that did not complete (footprint_check
# NOT_CHECKED, and the start of `why` in classify_land's incomplete branch).
BUILT_CHECK_NOT_RUN = "not checked"
BUILT_CHECK_INCOMPLETE = ("footprint check not completed", "an input of the built check failed")


def load_land(path: Path | None, identity: dict[str, str],
              by_pid: dict[str, dict[str, str]]) -> dict[str, dict[str, Any]]:
    """land_today keyed by parcel id; refused when it is from another run."""
    if path is None:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fp = fingerprint_of(data)
    if fp is not None and fp != identity["ranked_csv_sha256"]:
        raise Refused(f"{path} was made from another run (ranked.csv differs)")
    items = data.get("parcels") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise Refused(f"{path}: no list of parcels")
    land: dict[str, dict[str, Any]] = {}
    for x in items:
        pid = x.get("parcel_id") if isinstance(x, dict) else None
        if pid not in by_pid:
            raise Refused(f"{path}: a parcel that is not in this run's ranked.csv")
        if str(x.get("rank")) != by_pid[pid]["rank"]:
            raise Refused(f"{path}: rank and parcel id do not match this run")
        if x.get("land_today") not in LAND_CLASSES:
            raise Refused(f"{path}: unknown land class")
        what = text(x.get("what_is_there"))
        why = text(x.get("why")) or ""
        if x.get("footprint_check") == BUILT_CHECK_NOT_RUN or why.startswith(BUILT_CHECK_INCOMPLETE):
            # The built check did not complete for this parcel: say so next
            # to the class, which then rests on the photo alone.
            what = text(f"{what or 'no evidence'} [built check incomplete: {why or 'footprint check not completed'}]")
        land[pid] = {"land": x["land_today"], "landWhat": what}
    return land


def load_plans(plans_dir: Path, identity: dict[str, str],
               join_by_rank: bool) -> tuple[list[dict[str, Any]], bool]:
    """The planner's records and whether they carry the run fingerprint."""
    for name in PLAN_FILES:
        path = Path(plans_dir) / name
        if path.exists():
            break
    else:
        raise Refused(f"no {' or '.join(PLAN_FILES)} in {plans_dir}")
    data = json.loads(path.read_text(encoding="utf-8"))
    fp = fingerprint_of(data)
    if fp is not None and fp != identity["ranked_csv_sha256"]:
        raise Refused(f"{path} was made from another run (ranked.csv differs)")
    if isinstance(data, dict):
        items = data.get("plans", data.get("sites"))
    else:
        items = data
    if not isinstance(items, list):
        raise Refused(f"{path}: no list of plans")
    if fp is None and not join_by_rank and not all(
            isinstance(t, dict) and t.get("parcel_ids") for t in items):
        raise Refused(f"{path} names no run and no parcel ids; pass --join-by-rank if you "
                      "know it was planned from this run")
    return items, fp is not None


# --------------------------------------------------------------------------
# Building the page
# --------------------------------------------------------------------------

@dataclass
class Inputs:
    run_dir: Path
    plans_dir: Path | None = None
    decisions: Path | None = None
    leads: list[Path] = field(default_factory=list)
    land: Path | None = None
    include_notes: bool = False
    include_headlines: bool = False
    join_by_rank: bool = False


def _zoning(r: dict[str, str]) -> str:
    return f"{r.get('zoning') or 'unknown'} ({(r.get('entitlement') or '').replace('_', ' ')})"


def _flag_list(x: Any) -> list[dict[str, str]]:
    out = []
    for f in x if isinstance(x, list) else []:
        if isinstance(f, dict):
            out.append({"flag": text(f.get("flag"), 80) or "", "note": text(f.get("note"), NOTE_CAP) or ""})
    return out


def _sub(d: Any, spec: dict[str, str]) -> dict[str, Any] | None:
    """Copy a nested planner record, each value coerced to its declared
    kind ("n" number, "b" bool, "s" short text), so nothing else gets in."""
    if not isinstance(d, dict):
        return None
    out: dict[str, Any] = {}
    for key, kind in spec.items():
        v = d.get(key)
        if kind == "n":
            out[key] = num(v)
        elif kind == "b":
            out[key] = None if v is None else bool(v)
        else:
            out[key] = text(v, NOTE_CAP)
    return out


PLAN_FACTS = {
    "building": {"L_ft": "n", "D_ft": "n", "sf": "n", "office_sf": "n", "clear_height_ft": "n"},
    "trailers": {"court_rows": "n", "drop_yards": "n", "total": "n", "target": "n"},
    "cars": {"provided": "n", "required": "n", "ada": "n", "stall": "s", "basis": "s",
             "per_1000_sf": "n"},
    "coverage": {"gross_ac": "n", "sf_per_gross_ac": "n", "gross_far": "n",
                 "impervious_ac": "n", "impervious_pct": "n"},
    "detention": {"storage_acft": "n", "ponds": "n", "fits": "b", "basis": "s"},
    "access": {"truck_driveway": "b", "car_driveway": "b", "frontage_ft": "n", "road": "s",
               "truck_route_ft": "n", "car_route_ft": "n"},
    "grading": {"relief_ft": "n", "cut_fill_cy": "n", "steep_share_pct": "n", "basis": "s"},
    "second_building": {"configuration": "s", "sf": "n", "own_driveways": "b"},
}


def build_plans(inp: Inputs) -> list[dict[str, Any]]:
    run_dir = Path(inp.run_dir)
    identity = run_identity(run_dir)
    rows = read_ranked(run_dir)
    by_rank = {r["rank"]: r for r in rows}
    by_pid = {r["parcel_id"]: r for r in rows}
    decisions = load_decisions(inp.decisions)
    land = load_land(inp.land, identity, by_pid)

    def common(pid: str) -> dict[str, Any]:
        d = decisions.get(pid)
        out = {"status": status_of(d), **land.get(pid, {"land": None, "landWhat": None})}
        if inp.include_notes and d and d.get("note"):
            out["note"] = text(d["note"])
        return out

    def common_many(pids: list[str]) -> dict[str, Any]:
        """A multi-parcel plan: the worst member's land class and status,
        and each member's class in landWhat (an assemblage is not raw land
        because its first member is)."""
        if len(pids) == 1:
            return common(pids[0])
        each = [(p, common(p)) for p in pids]
        out = {"status": min((c["status"] for _, c in each), key=STATUS_WORST_FIRST.index),
               "land": min((c["land"] for _, c in each), key=LAND_WORST_FIRST.index)}
        parts = []
        for p, c in each:
            r = by_pid.get(p)
            who = f"#{r['rank']}" if r else p
            parts.append(f"{who} {(c['land'] or 'not reviewed').replace('_', ' ')}" + (f": {c['landWhat']}" if c.get("landWhat") else ""))
        out["landWhat"] = text("; ".join(parts))
        notes = [c["note"] for _, c in each if c.get("note")]
        if notes:
            out["note"] = text(" | ".join(notes))
        return out

    plans: list[dict[str, Any]] = []
    brief_ranks: set[int] = set()
    briefs = run_dir / "briefs"
    for path in sorted(briefs.glob("*.svg")) if briefs.is_dir() else []:
        m = BRIEF_SVG_RE.match(path.name)
        if not m:
            raise Refused(f"unexpected file name in briefs/: {path.name!r}")
        svg_path = inside(briefs, path.name, BRIEF_SVG_RE)
        rank = int(m.group(1))
        r = by_rank.get(str(rank))
        if r is None or r["parcel_id"].replace(" ", "_") != m.group(2):
            raise Refused(f"briefs/{path.name} does not match this run's ranked.csv")
        pid = r["parcel_id"]
        svg = sanitise_svg(svg_path.read_text(encoding="utf-8"))
        map_ac, fpp = svg_meta(svg)
        verdict, headline = lead_check(pid, inp.leads, inp.include_headlines)
        brief_ranks.add(rank)
        rec = {
            "id": f"run-{rank}", "kind": "run", "rank": rank, "pid": pid,
            "title": text(f"#{rank} {r['site_address']}", TITLE_CAP), "place": text(r["jurisdiction"]),
            "taxAc": num(r["acres"], 1), "mapAc": map_ac, "fpp": fpp,
            "zoning": text(_zoning(r)), "grade": text(r["lead_grade"], 20),
            "fit": text(r["envelope_fit"]), "sf": int(num(r["envelope_building_sf"]) or 0),
            "stdFits": (r["envelope_fit"] or "").startswith(("cross", "front")),
            "pond": text(r["pond_fit"]), "open": num(r["largest_open_acres"], 1),
            "flood": num(r["flood_pct"]), "wet": num(r["wetland_pct"]), "steep": num(r["steep_pct"]),
            "frontage": float(num(r["frontage_ft"]) or 0), "frontageRoad": text(r["frontage_road"]),
            "exit": text(f"{r['exit_miles']} mi straight line to {r['exit_label']}"),
            "skeptic": verdict, "maps": text(r["maps_link"], 400), "svg": svg, **common(pid),
        }
        rec["sfText"] = f"{rec['sf']:,} SF" if rec["sf"] else "no building fit"
        if headline:
            rec["headline"] = headline
        plans.append(rec)

    if inp.plans_dir is not None:
        items, _ = load_plans(Path(inp.plans_dir), identity, inp.join_by_rank)
        seen: set[str] = set()
        no_fit: list[str] = []
        not_planned: list[str] = []
        for t in items:
            if not isinstance(t, dict):
                continue
            if not t.get("svg") or t.get("fits") is False:
                # Planned, nothing drawn: there is no drawing to show, so the
                # page leaves it out and says why (no building fits, or the
                # search or an input stopped before a plan was made).
                key = str(t.get("key") if t.get("key") is not None else t.get("rank"))
                key = key if KEY_RE.match(key) else "?"
                sev = t.get("flags") if isinstance(t.get("flags"), dict) else {}
                first = {f.get("flag") for f in (sev.get("1", sev.get(1)) or []) if isinstance(f, dict)}
                if "PLAN_BUDGET_EXCEEDED" in first or any(str(f).endswith("_NOT_CHECKED") for f in first):
                    not_planned.append(key)
                else:
                    no_fit.append(key)
                continue
            plans.append(_plan_record(t, inp, by_rank, by_pid, brief_ranks, common_many, seen))
        if no_fit:
            print(f"viewer: note: {len(no_fit)} planned site(s) without a drawing (no building fits) are not shown: "
                  + ", ".join(no_fit[:40]) + (" ..." if len(no_fit) > 40 else ""), file=sys.stderr)
        if not_planned:
            print(f"viewer: note: {len(not_planned)} site(s) not planned (the search budget ran out or an input was "
                  "not checked before any plan was made; re-run the planner) are not shown: "
                  + ", ".join(not_planned[:40]) + (" ..." if len(not_planned) > 40 else ""), file=sys.stderr)
    return plans


def _plan_record(t: dict[str, Any], inp: Inputs, by_rank: dict[str, dict[str, str]],
                 by_pid: dict[str, dict[str, str]], brief_ranks: set[int], common,
                 seen: set[str]) -> dict[str, Any]:
    key = str(t.get("key") if t.get("key") is not None else t.get("rank"))
    if not KEY_RE.match(key):
        raise Refused(f"plan key not allowed: {key!r}")
    # The ranks a plan names: its "rank" ("4", "115+170"; an --assemble NAME
    # names none). screener.siteplan slugs the key ("115-170"), so the rank
    # field is read first; the prototype's records carry only a rank.
    ranks: list[int] = []
    for cand in (t.get("rank"), key):
        if cand is not None and re.fullmatch(r"\d{1,6}(\+\d{1,6})*", str(cand)):
            ranks = [int(k) for k in str(cand).split("+")]
            break
    pids = t.get("parcel_ids")
    if pids is not None:
        if not (isinstance(pids, list) and pids and all(isinstance(p, str) for p in pids)):
            raise Refused(f"plan {key}: parcel_ids is not a list of ids")
        for rank in ranks:
            row = by_rank.get(str(rank))
            if row is None or row["parcel_id"] not in pids:
                raise Refused(f"plan {key}: rank and parcel ids do not match this run")
        members = sorted((by_pid[p] for p in pids if p in by_pid), key=lambda r: int(r["rank"]))
    else:
        if not ranks or any(str(k) not in by_rank for k in ranks):
            raise Refused(f"plan {key}: no parcel ids and no rank of this run to join by")
        members = [by_rank[str(k)] for k in ranks]
    r = members[0] if members else None
    pid = r["parcel_id"] if r else pids[0]
    sort_rank = int(r["rank"]) if r else 10 ** 6
    pid_slug = re.sub(r"[^A-Za-z0-9_-]", "-", key)
    plan_id = f"plan-{pid_slug}"
    if plan_id in seen:
        raise Refused(f"two plans with the key {key!r}")
    seen.add(plan_id)
    svg_path = inside(Path(inp.plans_dir), t["svg"], PLAN_SVG_RE)
    if not svg_path.is_file():
        raise Refused(f"plan {key}: {t['svg']} is named in the plans file but is not in {inp.plans_dir}")
    svg = sanitise_svg(svg_path.read_text(encoding="utf-8"))
    _, fpp = svg_meta(svg)
    sev = t.get("flags") if isinstance(t.get("flags"), dict) else {}
    zoning = t.get("zoning") if isinstance(t.get("zoning"), dict) else {}
    zoning_name = text(zoning.get("name") or t.get("jurisdiction_rules"), 80)
    feasible = t.get("feasible")
    feasible = None if feasible is None else bool(feasible)
    gated = [g for g in (t.get("gated") if isinstance(t.get("gated"), list) else [])
             if isinstance(g, str) and FLAG_NAME_RE.match(g)][:10]
    det = t.get("detention") if isinstance(t.get("detention"), dict) else {}
    cov = t.get("coverage") if isinstance(t.get("coverage"), dict) else {}
    v4 = {k: _sub(t.get(k), spec) for k, spec in PLAN_FACTS.items() if isinstance(t.get(k), dict)}
    v4["doors"] = num(t.get("doors"))
    decisions = t.get("operator_decisions")
    if isinstance(decisions, dict):
        v4["operator_decisions"] = [[text(k, 60), text(json.dumps(v, sort_keys=True), 200)]
                                    for k, v in sorted(decisions.items())][:20]
    alts = []
    for a in (t.get("alternatives") or [])[:6] if isinstance(t.get("alternatives"), list) else []:
        if isinstance(a, dict):
            # geometry_ok: the layout passed its own geometry checks (route,
            # pond, caps); it is not the site's feasibility, which also
            # needs access and complete inputs. The prototype called it
            # "feasible".
            ok = a.get("geometry_ok", a.get("feasible"))
            alts.append({"variant": text(a.get("variant"), 120), "sf": num(a.get("sf")),
                         "geometryOk": ok if ok in (True, False) else None,
                         "route_problem": bool(a.get("route_problem"))})
    main_rank = ranks[0] if ranks else (sort_rank if r else None)
    rec = {
        "id": plan_id, "kind": "plan", "rank": sort_rank, "key": key, "pid": pid,
        "pids": [p for p in (pids or [m["parcel_id"] for m in members])][:12],
        "title": text(t.get("title") or f"#{key}", TITLE_CAP),
        "place": text(r["jurisdiction"]) if r else None,
        "taxAc": num(r["acres"], 1) if r and len(members) == 1 and len(pids or [pid]) == 1 else None,
        "mapAc": num(cov.get("gross_ac")), "fpp": fpp,
        "zoning": text(_zoning(r)) if r else None,
        "zoningRules": zoning_name, "zoningStatus": "draft",
        "grade": text(r["lead_grade"], 20) if r else None,
        "fit": text(t.get("configuration"), 200), "sf": int(num(t.get("total_sf")) or 0),
        "sfIfAccess": int(num(t.get("total_sf_if_access")) or 0), "stdFits": plan_std_fits(t),
        "feasible": feasible, "hypothetical": bool(t.get("hypothetical")),
        "sfText": plan_sf_text(feasible, gated, bool(t.get("hypothetical")),
                               int(num(t.get("total_sf")) or 0), int(num(t.get("total_sf_if_access")) or 0)),
        "pond": ("fits" if det.get("fits") else "does not fit (see flags)"), "open": None,
        "v4": v4,
        "flags1": _flag_list(sev.get("1", sev.get(1))), "flags2": _flag_list(sev.get("2", sev.get(2))),
        "flags3": _flag_list(sev.get("3", sev.get(3))),
        "alternatives": alts,
        # Entitlement gates (a use permit): named flags, kept apart from feasibility.
        "gated": gated,
        "related": f"run-{main_rank}" if main_rank in brief_ranks else None,
        "svg": svg, **common(list(dict.fromkeys(pids or [m["parcel_id"] for m in members]))),
    }
    return rec


def plan_std_fits(t: dict[str, Any]) -> bool | None:
    """Whether an operator prototype fits, for the "Operator prototypes fit"
    filter: the plan drawn is an operator prototype, or one of the layouts
    it lists (alternatives) is one and passed its own geometry checks. Most
    plans are market depths, so a plan is not a prototype fit by being a
    plan. None when the record says nothing either way."""
    alts = [a for a in (t.get("alternatives") or []) if isinstance(a, dict)]
    if t.get("operator_prototype") is True:
        return True
    if any(a.get("operator") is True and a.get("geometry_ok", a.get("feasible")) is True for a in alts):
        return True
    if t.get("operator_prototype") is False or alts or t.get("configuration") is None:
        return False
    return None


def plan_sf_text(feasible: bool | None, gated: list[str], hypothetical: bool, sf: int, sf_if_access: int) -> str:
    """The floor-area line the page shows for a planner record: what is
    counted, and when nothing is, why (reviews/37: a gated plan is not
    'no building fit')."""
    if sf:
        return f"{sf:,} SF"
    if not sf_if_access:
        return "no building fit"
    drawn = f"{sf_if_access:,} SF"
    if feasible is True and gated:
        return f"{drawn} (gated: {', '.join(gated)}; not counted)"
    if feasible is None:
        return f"{drawn} if the unchecked inputs were clear (not counted)"
    if hypothetical:
        return f"{drawn} if the site had road access (not counted)"
    return f"{drawn} if the severity-1 flags were cleared (not counted)"


def _json_for_script(obj: Any) -> str:
    """JSON safe inside a <script>: no literal < > & or line separators, so
    no </script>, <!-- or entity can come from the data (R3)."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_page(plans: list[dict[str, Any]], run_name: str, template: str | None = None) -> str:
    """Fill the template in one pass and add a CSP that lets only the page's
    own script (by its hash) run."""
    template = TEMPLATE.read_text(encoding="utf-8") if template is None else template
    if template.count("__CSP__") != 1:
        raise Refused("the template must hold __CSP__ once")
    head, rest = template.split("__CSP__")
    for ph in ("__DATA__", "__RUN__"):
        if rest.count(ph) != 1 or ph in head:
            raise Refused(f"the template must hold {ph} once, after __CSP__")
    values = {"__DATA__": _json_for_script(plans), "__RUN__": html.escape(run_name, quote=True)}
    filled = re.sub(r"__DATA__|__RUN__", lambda m: values[m.group(0)], rest)
    scripts = re.findall(r"<script>(.*?)</script>", filled, re.S)
    if len(scripts) != 1 or filled.count("<script") != 1:
        raise Refused("the page must hold exactly one script")
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode()
    csp = (f"default-src 'none'; script-src 'sha256-{digest}'; style-src 'unsafe-inline'; "
           "img-src data: blob:; base-uri 'none'; form-action 'none'")
    return head + html.escape(csp, quote=True) + filled


def build(inp: Inputs, out: Path, *, max_bytes: int = MAX_PAGE_BYTES) -> tuple[int, int]:
    """Build and write the page. Returns (bytes, plans). Writes nothing when
    any check refuses."""
    run_dir = Path(inp.run_dir)
    out = check_out_path(out, run_dir)
    for root in inp.leads:
        if not Path(root).is_dir():
            raise Refused(f"--leads {root}: not a folder")
    try:
        plans = build_plans(inp)
        guard = OwnerGuard.from_run(run_dir)
        # The site addresses the page itself writes: those of the run's
        # parcels it shows (titles and facts). An owner string that is part
        # of one of them is that parcel's published address.
        by_pid = {r["parcel_id"]: r for r in read_ranked(run_dir)}
        shown = {q for p in plans for q in ([p.get("pid")] + list(p.get("pids") or [])) if q}
        public = [by_pid[q].get("site_address") or "" for q in sorted(shown) if q in by_pid]
    except (OSError, ValueError, csv.Error) as exc:
        # A missing or unreadable input (a run folder without ranked.csv, a
        # --land or --decisions file that is not there or not valid JSON /
        # CSV). The page is written last, so nothing was written.
        raise Refused(f"cannot read an input ({type(exc).__name__}: {str(exc)[:200]})") from None
    guard.check(json.dumps(plans, ensure_ascii=False), "viewer data", public)
    for p in plans:
        # The visible text of each drawing, its markup removed (a name split
        # across SVG elements or wrapped over two lines).
        guard.check(markup_text(p.get("svg") or ""), f"plan {p.get('id')}", public)
    page = render_page(plans, run_dir.name)
    guard.check(page, "viewer page", public)
    size = len(page.encode("utf-8"))
    if size > max_bytes:
        raise Refused(f"the page would be {size:,} bytes, over the {max_bytes:,} limit")
    atomic_write(out, page)
    return size, len(plans)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m screener.viewer", description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="the HTML file to write")
    ap.add_argument("--plans", type=Path, help="screener.siteplan output folder (plans.json + SVGs)")
    ap.add_argument("--decisions", type=Path, help="decisions.csv (only the decision value is used)")
    ap.add_argument("--leads", type=Path, nargs="*", default=[], help="lead folder roots")
    ap.add_argument("--land", type=Path, help="land_today.json from screener.builtcheck")
    ap.add_argument("--include-notes", action="store_true",
                    help="also show decisions.csv notes (still owner-guarded)")
    ap.add_argument("--include-headlines", action="store_true",
                    help="also show lead.md 'In one line' headlines (still owner-guarded)")
    ap.add_argument("--join-by-rank", action="store_true",
                    help="accept a plans file with no run fingerprint or parcel ids, joined by rank")
    args = ap.parse_args(argv)
    inp = Inputs(run_dir=args.run_dir, plans_dir=args.plans, decisions=args.decisions,
                 leads=list(args.leads), land=args.land, include_notes=args.include_notes,
                 include_headlines=args.include_headlines, join_by_rank=args.join_by_rank)
    try:
        size, n = build(inp, args.out)
    except Refused as exc:
        print(f"viewer: refused: {exc}", file=sys.stderr)
        return 2

    if size > WARN_PAGE_BYTES:
        print(f"viewer: note: the page is {size:,} bytes", file=sys.stderr)
    print(f"{args.out}: {size:,} bytes; {n} plans")
    return 0


if __name__ == "__main__":
    sys.exit(main())
