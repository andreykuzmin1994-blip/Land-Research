"""Neighbour adjacency for site fit: facts, gate, read and per-side buffers.

Protected module (reviews/27_site_fit_buffers/): immutable during a run, like
zoning.py, site_fit.py and hazards.py. It imports only the standard library,
``prepare`` and ``zoning``; never ``research`` (the agent's sandbox),
``runner``, ``reporting``, ``site_fit``, the import tools or anything under
screener/. Loading it never raises and never touches the database.

What it owns
------------
1. The storage gate (BUF-03, BUF-18). There is no adjacency table in the
   schema. ``parcel_adjacency`` is a PROPOSAL (``PROPOSED_DDL``, the same
   text as reviews/27_site_fit_buffers/01_prepare_mutation_proposal.md).
   Only a human can add it, by putting the constant ``_DDL_PARCEL_ADJACENCY``
   into prepare.py through the prepare-mutation protocol. ``enabled()``
   reads that constant from prepare.py's SOURCE TEXT on disk, once, when
   this module loads (hazards.py's pattern): no SQL, and an in-process
   assignment to ``prepare._DDL_PARCEL_ADJACENCY`` changes nothing. While
   it is off, site_fit issues exactly the statements it issued before this
   module existed and every projection text is unchanged.

2. The facts. Fixed classification constants (abutment tolerance, sliver
   width/area/length floors) used by adjacency_import.py; the allow-list of
   buffer keys this module reads from ``zoning_rules.json`` (BUF-02): each
   entry maps one (rules table, key) to the neighbour classes it applies
   to and its role, and cites the rule text it reads. A key that is not in
   the allow-list, or a value that is not a number, is never applied and is
   named in the projection.

3. The read and the per-side plan (``plan_parcel``). Rows store the RAW
   neighbour district code plus the source's jurisdictions (BUF-01, BUF-13).
   At read time:
   - "other district" = a neighbour whose district identity (jurisdiction
     table, code after ``zoning.normalize`` and the governing jurisdiction's
     ``district_conversions``, as ``zoning.district_for_stored`` resolves
     the parcel itself) differs from the parcel's CURRENT stored district;
   - "residential" / "agricultural" come ONLY from a human-owned table,
     ``jurisdictions.<j>.neighbour_classes`` in zoning_rules.json (a schema
     PROPOSAL in reviews/27_site_fit_buffers/01_prepare_mutation_proposal.md,
     absent today). No district code is ever classified in code. While the
     table is absent every residential buffer reads "not applied (neighbour
     classification not in the rules)";
   - an uncovered side (no fetched zoning polygon within the tolerance) is
     "adjacency unknown", never ROW and never "no neighbour" (BUF-04); ROW
     is applied only from a ROW source, and none is registered;
   - stale rows (the parcel geometry changed since the import) or no rows
     at all: nothing applied, and the projection says why.
   Side vs rear is unknown (frontage is unknown), so the larger value
   applicable to a neighbour class is used on every abutting side and the
   projection says "rear value applied (frontage unknown)" (BUF-07). The
   per-side distance is max(uniform setback, buffer + listed additive keys):
   buffers only ever subtract (BUF-06); a distance at or under the uniform
   setback deducts nothing and is named.

The deduction itself is SQL inside site_fit (``ST_Buffer`` of each applied
stored segment, SRID 2240 feet, plus circumscribed octagons at its vertices
so the round caps and joins are covered; BUF-09, BUF-10).
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import prepare
import zoning

# ---------------------------------------------------------------------------
# The storage gate
# ---------------------------------------------------------------------------
GATE_CONSTANT = "_DDL_PARCEL_ADJACENCY"
TABLE = "parcel_adjacency"

# The proposed DDL (reviews/27_site_fit_buffers/01_prepare_mutation_proposal.md
# carries the same text; a test compares them). Idempotent; no parcels column.
PROPOSED_DDL = """
CREATE TABLE IF NOT EXISTS parcel_adjacency (
    adjacency_id BIGSERIAL PRIMARY KEY,
    parcel_id TEXT NOT NULL REFERENCES parcels(parcel_id) ON DELETE CASCADE,
    segment geometry(MultiLineString, 2240) NOT NULL CHECK (ST_SRID(segment) = 2240),
    neighbour_kind TEXT NOT NULL CHECK (neighbour_kind IN ('zoned', 'row', 'uncovered')),
    neighbour_district TEXT,
    neighbour_jurisdictions TEXT,
    source TEXT NOT NULL,
    source_as_of TEXT,
    parcel_geom_md5 TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    CHECK ((neighbour_kind = 'zoned') = (neighbour_district IS NOT NULL))
);
"""
PROPOSED_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_adjacency_parcel ON parcel_adjacency (parcel_id);",
    "CREATE INDEX IF NOT EXISTS idx_adjacency_segment ON parcel_adjacency USING GIST(segment);",
)
COLUMNS = (
    "adjacency_id", "parcel_id", "segment", "neighbour_kind", "neighbour_district",
    "neighbour_jurisdictions", "source", "source_as_of", "parcel_geom_md5", "fetched_at",
)
KIND_ZONED, KIND_ROW, KIND_UNCOVERED = "zoned", "row", "uncovered"


def _string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _string_value(node.left), _string_value(node.right)
        return a + b if a is not None and b is not None else None
    return None


def ddl_from_prepare_source(path: Any = None) -> str | None:
    """The module-level string ``_DDL_PARCEL_ADJACENCY = "..."`` of prepare.py
    as written on disk (the last top-level assignment wins), or None. Never
    raises. Read once at load into ``_SOURCE_DDL``."""
    try:
        src = Path(path if path is not None else prepare.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001 - load never raises
        return None
    found: str | None = None
    for node in tree.body:
        targets: list[ast.AST] = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if any(isinstance(t, ast.Name) and t.id == GATE_CONSTANT for t in targets):
            found = _string_value(value) if value is not None else None
    return found


# Frozen at load from prepare.py's source text: runtime assignments to
# ``prepare._DDL_PARCEL_ADJACENCY`` change nothing.
_SOURCE_DDL: str | None = ddl_from_prepare_source()


def enabled() -> bool:
    """True only once a human has added ``_DDL_PARCEL_ADJACENCY`` to prepare.py
    (its source text, frozen at load). No SQL: the gate costs nothing while
    it is off (BUF-03)."""
    ddl = _SOURCE_DDL
    return isinstance(ddl, str) and TABLE in ddl


def schema_problem() -> str | None:
    """Why prepare.py's DDL cannot be read by this module, or None. A DDL that
    lacks a column this module reads is never queried."""
    ddl = _SOURCE_DDL
    if not isinstance(ddl, str):
        return "no adjacency table in prepare.py"
    missing = [c for c in COLUMNS if c not in ddl]
    if missing:
        return f"adjacency table lacks {len(missing)} expected column(s)"
    if "MultiLineString, 2240" not in ddl:
        return "adjacency segment column is not geometry(MultiLineString, 2240)"
    return None


# ---------------------------------------------------------------------------
# Classification constants (used by adjacency_import.py; fixed here, never
# in research.py or on the command line; BUF-08)
# ---------------------------------------------------------------------------
SRID_FEET = 2240
# Outward ring width: a neighbour polygon counts for a boundary part when it
# covers part of the ring buffer(parcel, ABUT_TOL_FT) - parcel. 20 ft catches
# a zoning layer offset 15 ft from the parcel fabric and misses a neighbour
# across a 50-60 ft street (Atlanta's transitional yard excludes an
# intervening street; no rule in zoning_rules.json says "across a street").
ABUT_TOL_FT = 20.0
# A covered ring piece is a sliver (counted, never a buffer) when it is under
# MIN_OVERLAP_AREA_SF or nowhere at least MIN_SLIVER_WIDTH_FT wide (2 ft
# misdigitisation strips), or when the boundary part it faces is under
# MIN_SEGMENT_LEN_FT.
MIN_SLIVER_WIDTH_FT = 3.0
MIN_OVERLAP_AREA_SF = 50.0
MIN_SEGMENT_LEN_FT = 10.0
# Round caps and joins of ST_Buffer(segment, d, 'quad_segs=2') are inscribed
# in the circle; an octagon of circumradius d / cos(pi/8) around every segment
# vertex contains the circle of radius d, so the union of both contains every
# point within d of the segment (BUF-10: conservative).
QUAD_SEGS = 2
OCTAGON_FACTOR = 1.0 / math.cos(math.pi / (4 * QUAD_SEGS))

# ---------------------------------------------------------------------------
# Neighbour classes and the buffer-key allow-list (BUF-01, BUF-02)
# ---------------------------------------------------------------------------
RESIDENTIAL, AGRICULTURAL, NON_RESIDENTIAL = "residential", "agricultural", "non_residential"
OTHER_DISTRICT, ROW = "other_district", "row"
NEIGHBOUR_CLASS_KEYS = (RESIDENTIAL, AGRICULTURAL, NON_RESIDENTIAL)
SIDE, REAR, BUFFER, ADDITIVE, TEXT = "side", "rear", "buffer", "additive", "text"
# Strictest first: the order classes are reported in.
CLASS_ORDER = (RESIDENTIAL, AGRICULTURAL, OTHER_DISTRICT, ROW)


@dataclass(frozen=True)
class BufferKey:
    """One allow-listed key of ``residential_adjacency_buffer_ft``."""
    table: str
    key: str
    classes: frozenset
    role: str
    cite: str


def _bk(table: str, key: str, classes: Iterable[str], role: str, cite: str) -> BufferKey:
    return BufferKey(table, key, frozenset(classes), role, cite)


_SF_CITE = ("zoning_rules.json south_fulton M-1/M-2 residential_adjacency_buffer_ft cite: Sec. "
            "405.01 / Table 4-4; Sec. 405.01(b)(1) 'buffers required adjacent to AG-1, R-*, TR, "
            "A, A-L, NUP, CUP, MIX-with-residential and existing single-family uses'")
_SF_IMP = ("zoning_rules.json south_fulton cite: Sec. 405.01(c) '10-ft improvement setback "
           "interior to all buffers' (added to the buffer)")
_FID_CITE = ("zoning_rules.json fid M-1/M-2 residential_adjacency_buffer_ft cite: Fulton County "
             "Zoning Resolution Sec. 4.23.1.B and E, note 'Buffers adjacent to AG-1, R-*, TR, A, "
             "A-L, NUP, CUP, MIX-with-residential and all single-family uses'")
_FID_IMP = ("zoning_rules.json fid key plus_improvement_setback (Sec. 4.23.1.E, the same rule as "
            "South Fulton Sec. 405.01(c): added to the buffer)")
_UC_CITE = ("zoning_rules.json union_city M-1 cite: Sec. 6-13.D.6, note '75-ft buffer where rear "
            "or side yard abuts a Residential OR Agricultural district'")
_UC_ADD = ("zoning_rules.json union_city note 'in addition to the setback; setback measured from "
           "the buffer (so effectively 95 ft)' (added to the buffer)")
_ATL_CITE = ("zoning_rules.json atlanta I-1/I-2 cite: Sec. 16-16.006(3) / 16-17.006(3), note "
             "'Transitional yards adjacent to an R district (no intervening street)'")
_FB_RES = ("zoning_rules.json fairburn M-1/M-2 cite: Sec. 80-85(f)/80-86(f) note, side/rear yard "
           "abutting residential; Sec. 80-369 'buffers may be counted within required yards' "
           "(not added to the setback)")
_FB_OTHER = ("zoning_rules.json fairburn cite: Sec. 80-372 'When C-1, C-2, M-1, or M-2 zoned "
             "properties abut all other zoning districts; a 50 feet buffer shall be required' "
             "(read literally; Sec. 80-369: within required yards)")
_FB_ROW = ("zoning_rules.json fairburn cite: Sec. 80-367, buffer along all public ROW "
           "(Sec. 80-369: within required yards)")

BUFFER_KEYS: Mapping[tuple[str, str], BufferKey] = {
    (b.table, b.key): b for b in (
        _bk("south_fulton", "side", (RESIDENTIAL, AGRICULTURAL), SIDE, _SF_CITE),
        _bk("south_fulton", "rear", (RESIDENTIAL, AGRICULTURAL), REAR, _SF_CITE),
        _bk("south_fulton", "plus_improvement_setback", (RESIDENTIAL, AGRICULTURAL), ADDITIVE,
            _SF_IMP),
        _bk("fid", "side", (RESIDENTIAL, AGRICULTURAL), SIDE, _FID_CITE),
        _bk("fid", "rear", (RESIDENTIAL, AGRICULTURAL), REAR, _FID_CITE),
        _bk("fid", "plus_improvement_setback", (RESIDENTIAL, AGRICULTURAL), ADDITIVE, _FID_IMP),
        _bk("union_city", "buffer", (RESIDENTIAL, AGRICULTURAL), BUFFER, _UC_CITE),
        _bk("union_city", "plus_setback_measured_from_buffer", (RESIDENTIAL, AGRICULTURAL),
            ADDITIVE, _UC_ADD),
        _bk("atlanta", "side", (RESIDENTIAL,), SIDE, _ATL_CITE),
        _bk("atlanta", "rear", (RESIDENTIAL,), REAR, _ATL_CITE),
        # A screening requirement, not a distance: named, never applied.
        _bk("atlanta", "screen", (RESIDENTIAL,), TEXT, _ATL_CITE),
        _bk("fairburn", "side_rear_yard_abutting_residential", (RESIDENTIAL,), BUFFER, _FB_RES),
        _bk("fairburn", "buffer_abutting_other_districts", (OTHER_DISTRICT,), BUFFER, _FB_OTHER),
        _bk("fairburn", "buffer_along_all_public_ROW", (ROW,), BUFFER, _FB_ROW),
    )
}
# Rules tables whose buffer triggers include USES (single-family homes,
# MIX-with-residential) that zoning cannot show (BUF-15).
USE_TRIGGER_TABLES: Mapping[str, str] = {"south_fulton": _SF_CITE, "fid": _FID_CITE}
# Table-specific reading notes, each stated on every projection it affects.
FAIRBURN_LITERAL = "Fairburn Sec. 80-372 read literally (includes other industrial districts)"
UNION_CITY_FRONT = "Union City front-yard exemption not applied (frontage unknown)"
REAR_APPLIED = "rear value applied (frontage unknown)"
USE_NOT_CHECKED = "residential USE not checked (zoning-based adjacency only)"
HEIGHT_NOT_APPLIED = "height-based setback not applied (building height not modelled)"
ACROSS_STREET = "across-street buffers not applied (rules do not say across a street)"
ROW_NOT_APPLIED = "ROW buffer not applied (no ROW source)"
CLASS_TABLE_MISSING = "neighbour classification not in the rules"

# Projection summaries (Projection.adjacency) for the states without a plan.
ADJ_OFF = "adjacency buffers not applied"      # the memo summary while the gate is off
ADJ_NOT_IMPORTED = "adjacency buffers not applied (adjacency unknown: not imported)"
ADJ_STALE = ("adjacency buffers not applied (adjacency unknown (stale): parcel geometry "
             "changed since the import)")
ADJ_UNREADABLE = "adjacency buffers not applied (adjacency data unreadable)"
ADJ_NO_GEOMETRY = "adjacency buffers not applied (no buildable geometry read)"
ADJ_NO_RULE = "adjacency buffers: none listed in the rules for this district"
# Memo footnote once the gate is on (BUF-24).
FOOTNOTE = ("adjacency buffers applied where neighbour zoning is known (each line says which "
            "class, distance and how many sides; unknown sides listed, never treated as ROW or "
            "as no neighbour)")


# ---------------------------------------------------------------------------
# Rules access
# ---------------------------------------------------------------------------
def _rules() -> Mapping[str, Any] | None:
    try:
        return zoning.load_rules()
    except Exception:  # noqa: BLE001 - rules unavailable: nothing classifies
        return None


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


def _fmt(v: float) -> str:
    return f"{v:g}"


def _safe(text: Any, limit: int = 40) -> str:
    s = "".join(ch if (ch.isalnum() or ch in " -_./()'&+:%#,") else " " for ch in str(text))
    return " ".join(s.split())[:limit]


def neighbour_classes_table(table: str) -> tuple[Mapping[str, frozenset] | None, str]:
    """The human-owned class table of one rules table: ({class: codes}, why).
    None when absent or malformed (the reason says which)."""
    rules = _rules()
    if rules is None:
        return None, "zoning rules unavailable"
    j = (rules.get("jurisdictions") or {}).get(table)
    if not isinstance(j, Mapping):
        return None, CLASS_TABLE_MISSING
    nc = j.get("neighbour_classes")
    if nc is None:
        return None, CLASS_TABLE_MISSING
    if not isinstance(nc, Mapping) or not isinstance(nc.get("cite"), str) or not nc["cite"]:
        return None, "neighbour classification malformed in the rules"
    out: dict[str, frozenset] = {}
    for k in NEIGHBOUR_CLASS_KEYS:
        v = nc.get(k, ())
        if not isinstance(v, (list, tuple)) or not all(isinstance(c, str) and c for c in v):
            return None, "neighbour classification malformed in the rules"
        out[k] = frozenset(zoning.normalize(c)[0] or "" for c in v) - {""}
    return out, ""


def _matches(code: str, entries: frozenset) -> bool:
    """An entry ending in '*' is a prefix the HUMAN wrote, matched literally
    (e.g. 'R-*' matches 'R-3', never 'RM'); every other entry matches one code,
    hyphens ignored on both sides as in ``same_district``."""
    c = code.translate(_NO_HYPHEN)
    for e in entries:
        if e.endswith("*"):
            if code.startswith(e[:-1]):
                return True
        elif c == e.translate(_NO_HYPHEN):
            return True
    return False


def identity(jurisdiction: str, code: Any) -> tuple[str, str] | None:
    """(rules table, district) of a neighbour district code, resolved as
    ``zoning.district_for_stored`` resolves a parcel (normalisation, the
    governing jurisdiction and its district_conversions). Neighbours are
    never in a jurisdiction's unincorporated_parcel_ids (no neighbour id is
    stored), so a governed jurisdiction always resolves to its governor."""
    base = zoning.normalize(code)[0]
    if base is None:
        return None
    rules = _rules()
    jurs = (rules or {}).get("jurisdictions") or {}
    j = jurs.get(jurisdiction)
    if isinstance(j, Mapping) and "governing_jurisdiction" in j:
        gov = j.get("governing_jurisdiction")
        if gov != "self" and isinstance(gov, str) and gov in jurs:
            conv = {zoning.normalize(a)[0]: zoning.normalize(b)[0]
                    for a, b in (j.get("district_conversions") or {}).items()}
            return gov, conv.get(base, base)
    return jurisdiction, base


_NO_HYPHEN = str.maketrans("", "", "-")


def same_district(a: tuple[str, str] | None, b: tuple[str, str] | None) -> bool:
    """Two (rules table, district) identities name the same district. Codes
    compare after ``zoning.normalize`` (already applied) with hyphens
    dropped, so 'M-1' and 'M1' are one district (BUF-13: a layer's spelling
    is never read as another district)."""
    if a is None or b is None:
        return False
    return a[0] == b[0] and a[1].translate(_NO_HYPHEN) == b[1].translate(_NO_HYPHEN)


def classify_neighbour(identities: Sequence[tuple[str, str]]) -> tuple[str | None, str]:
    """(class, why) of a neighbour from the human table(s): residential,
    agricultural, non_residential, or None (unknown, why says which). With
    several identities (a layer serving several jurisdictions) the strictest
    known class wins."""
    found: list[str] = []
    whys: list[str] = []
    for table, code in identities:
        t, why = neighbour_classes_table(table)
        if t is None:
            whys.append(why)
            continue
        for k in NEIGHBOUR_CLASS_KEYS:
            if _matches(code, t[k]):
                found.append(k)
                break
        else:
            whys.append("neighbour district not classified in the rules")
    for k in NEIGHBOUR_CLASS_KEYS:
        if k in found:
            if k == NON_RESIDENTIAL and whys:
                return None, whys[0]
            return k, ""
    return None, (whys[0] if whys else CLASS_TABLE_MISSING)


# ---------------------------------------------------------------------------
# Buffer values -> distances (BUF-02, BUF-07)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Terms:
    """The usable allow-listed terms of one district's buffer value."""
    table: str
    terms: tuple[tuple[BufferKey, float], ...]
    ignored: tuple[str, ...]          # named, never applied


def buffer_terms(table: str, raw: Any) -> Terms:
    """Split ``residential_adjacency_buffer_ft``'s value into allow-listed
    numeric terms and ignored ones (unknown key, or a value that is not a
    number: named, never coerced)."""
    if raw is None:
        return Terms(table, (), ())
    items = list(raw.items()) if isinstance(raw, Mapping) else [("buffer", raw)]
    terms, ignored = [], []
    for k, v in sorted(items, key=lambda kv: str(kv[0])):
        bk = BUFFER_KEYS.get((table, str(k)))
        n = _num(v)
        if bk is None:
            ignored.append(f"{_safe(k)} {_safe(v)} not applied (key not in the adjacency "
                           f"allow-list)")
        elif bk.role == TEXT or n is None or n < 0:
            ignored.append(f"{_safe(k)} {_safe(v)} not applied (not a distance)")
        else:
            terms.append((bk, n))
    return Terms(table, tuple(terms), tuple(ignored))


def component_distance(t: Terms, cls: str, role: str | None = None) -> float | None:
    """Buffer for one neighbour class: the role's value (side / rear), or with
    role None the LARGEST side/rear/buffer value (frontage unknown, BUF-07),
    plus every additive term of that class. None when no term applies."""
    base = [v for b, v in t.terms if cls in b.classes and b.role in (SIDE, REAR, BUFFER)
            and (role is None or b.role in (role, BUFFER))]
    if not base:
        return None
    add = sum(v for b, v in t.terms if cls in b.classes and b.role == ADDITIVE)
    return max(base) + add


def effective_distance(t: Terms, classes: Iterable[str]) -> tuple[float | None, str | None]:
    """(distance, class that set it) for a side with these neighbour classes:
    the largest component over the classes, None when none applies."""
    best: tuple[float | None, str | None] = (None, None)
    for c in CLASS_ORDER:
        if c not in classes:
            continue
        d = component_distance(t, c)
        if d is not None and (best[0] is None or d > best[0]):
            best = (d, c)
    return best


def side_distance(t: Terms, classes: Iterable[str], setback: float) -> float:
    """Per-side composition: max(uniform setback, buffer + additive). Never
    below the uniform setback (BUF-06)."""
    d, _ = effective_distance(t, classes)
    return setback if d is None else max(setback, d)


def _rear_over_side(t: Terms, cls: str) -> bool:
    side = component_distance(t, cls, SIDE)
    rear = component_distance(t, cls, REAR)
    roles = {b.role for b, _ in t.terms if cls in b.classes}
    return SIDE in roles and REAR in roles and rear is not None and side is not None \
        and rear > side


def classes_needed(t: Terms) -> frozenset:
    return frozenset(c for b, _ in t.terms for c in b.classes)


# ---------------------------------------------------------------------------
# The read (site_fit runs it through its savepoint-safe query function)
# ---------------------------------------------------------------------------
# Parameters: (ids). Freshness: the stored md5(ST_AsBinary(geometry)) of the
# parcel at import time against the parcel's current geometry (BUF-13).
_SQL_ADJACENCY_FOR_PARCELS = (
    "SELECT a.adjacency_id, a.parcel_id, a.neighbour_kind, a.neighbour_district, "
    "a.neighbour_jurisdictions, a.source, a.source_as_of, "
    "to_char(a.fetched_at AT TIME ZONE 'UTC', 'YYYY-MM-DD'), "
    "(a.parcel_geom_md5 = md5(ST_AsBinary(p.geometry))), ST_Length(a.segment) "
    "FROM parcel_adjacency a JOIN parcels p ON p.parcel_id = a.parcel_id "
    "WHERE a.parcel_id = ANY(%s) ORDER BY a.parcel_id, a.adjacency_id"
)


@dataclass(frozen=True)
class Row:
    adjacency_id: int
    parcel_id: str
    kind: str
    district: str | None
    jurisdictions: tuple[str, ...]
    source: str
    source_as_of: str | None
    fetched_on: str | None
    fresh: bool
    length_ft: float


def read_rows(conn: Any, parcel_ids: Sequence[str],
              query: Callable[[Any, str, tuple], list]) -> dict[str, list[Row]]:
    """Stored rows per parcel (every requested id present, [] = none).
    Raises on a failed read (the caller labels it unreadable)."""
    ids = sorted({str(p) for p in parcel_ids})
    out: dict[str, list[Row]] = {pid: [] for pid in ids}
    for r in query(conn, _SQL_ADJACENCY_FOR_PARCELS, (ids,)):
        if not isinstance(r, (tuple, list)) or len(r) < 10:
            raise ValueError("adjacency row shape")
        jurs = tuple(j for j in str(r[4] or "").split(",") if j)
        out.setdefault(str(r[1]), []).append(Row(
            int(r[0]), str(r[1]), str(r[2]), None if r[3] is None else str(r[3]), jurs,
            str(r[5]), None if r[6] is None else str(r[6]), None if r[7] is None else str(r[7]),
            bool(r[8]), float(r[9] or 0.0)))
    return out


# ---------------------------------------------------------------------------
# The per-parcel plan
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Plan:
    """What to deduct and what to say, for one parcel."""
    deductions: tuple[tuple[int, float], ...]     # (adjacency_id, distance ft)
    summary: str                                   # replaces RES_BUFFERS
    lines: tuple[str, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)


def _plural(n: int) -> str:
    return f"{n} side" if n == 1 else f"{n} sides"


def _height_value(dims: Mapping[str, Any] | None) -> Any:
    if dims is None:
        return None
    v = dims.get("height_based_extra_setback")
    return v.get("value") if isinstance(v, Mapping) else v


def plan_parcel(parcel_id: str, stored: tuple[Any, Any] | None, rows: Sequence[Row] | None,
                setback: float) -> Plan:
    """The adjacency plan of one parcel (pure; never raises on data).

    ``stored``: the parcel's (zoning, zoning_description); ``rows``: its
    stored adjacency rows, or None when the read failed; ``setback``: the
    uniform setback site_fit already applies (never relaxed)."""
    if rows is None:
        return Plan((), ADJ_UNREADABLE)
    st = zoning.parse_stored(stored[0], stored[1]) if stored else None
    dist = zoning.district_for_stored(stored[0], stored[1], parcel_id) if stored else None
    dims = dist.dimensions if dist is not None else None
    raw = None
    if dims is not None:
        v = dims.get("residential_adjacency_buffer_ft")
        raw = v.get("value") if isinstance(v, Mapping) else v
    table = dist.table_jurisdiction if dist is not None else ""
    terms = buffer_terms(table, raw)
    extra: list[str] = []
    hv = _height_value(dims)
    if hv is not None and _num(hv) is None:
        extra.append(HEIGHT_NOT_APPLIED)
    if not terms.terms and not terms.ignored:
        return Plan((), ADJ_NO_RULE, tuple(extra))
    if not rows:
        return Plan((), ADJ_NOT_IMPORTED, tuple(extra))
    if not all(r.fresh for r in rows):
        return Plan((), ADJ_STALE, tuple(extra))
    split = bool(st.split) if st is not None else False
    own = (dist.table_jurisdiction, dist.district) if dist is not None else None
    needed = classes_needed(terms)
    applied: dict[tuple[str, float], int] = {}
    within: dict[tuple[str, float], int] = {}
    deductions: list[tuple[int, float]] = []
    c = {"sides": 0, "same_district": 0, "unknown": 0, "class_unknown": 0,
         "other_unknown": 0, "applied": 0, "within_setback": 0, "other_industrial": 0,
         RESIDENTIAL: 0, AGRICULTURAL: 0, OTHER_DISTRICT: 0, "split": int(split)}
    class_why: dict[str, int] = {}
    rear_used = False
    for r in rows:
        c["sides"] += 1
        classes: set[str] = set()
        if r.kind == KIND_UNCOVERED:
            c["unknown"] += 1
            continue
        if r.kind == KIND_ROW:
            classes.add(ROW)
        elif r.kind == KIND_ZONED:
            ids = [i for i in (identity(j, r.district) for j in (r.jurisdictions or ("",)))
                   if i is not None]
            # A layer serving several jurisdictions (Fulton layer 34: South
            # Fulton and FID) carries one code: it is the parcel's own district
            # when it resolves to it under any of them (M-1A via FID's
            # conversion is M-1).
            if own is not None and not split and ids and any(same_district(i, own)
                                                             for i in ids):
                c["same_district"] += 1
                continue
            if own is None or split or not ids:
                c["other_unknown"] += 1
            else:
                classes.add(OTHER_DISTRICT)
                c[OTHER_DISTRICT] += 1
                c["other_industrial"] += int(_looks_industrial_class(ids))
            if needed & {RESIDENTIAL, AGRICULTURAL}:
                ncls, why = classify_neighbour(ids)
                if ncls in (RESIDENTIAL, AGRICULTURAL):
                    classes.add(ncls)
                    c[ncls] += 1
                elif ncls is None:
                    c["class_unknown"] += 1
                    class_why[why] = class_why.get(why, 0) + 1
        else:
            c["unknown"] += 1
            continue
        d, by = effective_distance(terms, classes)
        if d is None or by is None:
            continue
        if by in (RESIDENTIAL, AGRICULTURAL) and _rear_over_side(terms, by):
            rear_used = True
        if d <= setback:
            within[(by, d)] = within.get((by, d), 0) + 1
            c["within_setback"] += 1
            continue
        applied[(by, d)] = applied.get((by, d), 0) + 1
        deductions.append((r.adjacency_id, d))
        c["applied"] += 1
    srcs = sorted({(r.source, r.fetched_on or "?") for r in rows if r.kind != KIND_UNCOVERED})
    src_text = ", ".join(f"{_safe(s, 40)} fetched {d}" for s, d in srcs) or "no zoning source"
    parts = [f"{k[0]} {_fmt(k[1])} ft on {_plural(n)}"
             for k, n in sorted(applied.items(), key=lambda kv: (CLASS_ORDER.index(kv[0][0]),
                                                                 -kv[0][1]))]
    summary = (f"adjacency buffers applied where neighbour zoning is known ({src_text}): "
               + (", ".join(parts) if parts else "none applied"))
    if c["unknown"]:
        summary += (f"; {_plural(c['unknown'])} adjacency unknown (no zoning polygon within "
                    f"{_fmt(ABUT_TOL_FT)} ft; not treated as ROW or as no neighbour)")
    lines: list[str] = []
    for k, n in sorted(within.items(), key=lambda kv: (CLASS_ORDER.index(kv[0][0]), -kv[0][1])):
        lines.append(f"{k[0]} buffer {_fmt(k[1])} ft within the {_fmt(setback)} ft setback on "
                     f"{_plural(n)}")
    if rear_used:
        lines.append(REAR_APPLIED)
    for why, n in sorted(class_why.items()):
        lines.append(f"residential/agricultural buffer not applied on {_plural(n)} ({why})")
    if c["other_unknown"] and OTHER_DISTRICT in needed:
        why = "split-zoned parcel" if split else "parcel district unknown"
        lines.append(f"other-district buffer not applied on {_plural(c['other_unknown'])} "
                     f"(adjacency unknown ({why}))")
    if split:
        lines.append("adjacency unknown on internal district lines (split-zoned parcel)")
    if ROW in needed:
        row_vals = [v for b, v in terms.terms if ROW in b.classes]
        note = ROW_NOT_APPLIED
        if row_vals and max(row_vals) <= setback:
            note += (f"; {_fmt(max(row_vals))} ft is within the {_fmt(setback)} ft setback "
                     "anyway")
        lines.append(note)
    if table == "fairburn" and OTHER_DISTRICT in needed:
        lines.append(FAIRBURN_LITERAL)
    if table == "union_city" and needed & {RESIDENTIAL, AGRICULTURAL}:
        lines.append(UNION_CITY_FRONT)
    if needed & {RESIDENTIAL, AGRICULTURAL}:
        lines.append(ACROSS_STREET)
    if table in USE_TRIGGER_TABLES:
        lines.append(USE_NOT_CHECKED)
    lines.extend(terms.ignored)
    lines.extend(extra)
    return Plan(tuple(deductions), summary, tuple(lines), dict(c))


def _looks_industrial_class(ids: Sequence[tuple[str, str]]) -> bool:
    """Counting only (BUF-14 live report): the neighbour's district is listed
    in its rules table with warehouse/distribution permitted by right (an
    industrial district of the rules file). Never used for a deduction."""
    rules = _rules()
    jurs = (rules or {}).get("jurisdictions") or {}
    for table, code in ids:
        d = ((jurs.get(table) or {}).get("districts") or {})
        for k, v in d.items():
            if zoning.normalize(k)[0] == code and isinstance(v, Mapping):
                st = (v.get("warehouse_distribution") or {}).get("status")
                if st == "permitted_by_right":
                    return True
    return False
