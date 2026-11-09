"""Flood (H4, FEMA NFHL) and wetlands (H6, USFWS NWI): facts and decisions.

Protected module (reviews/26_flood_wetlands/): immutable during a run, like
zoning.py and site_fit.py. It imports only the standard library and
``prepare``; never ``research`` (the agent's sandbox), ``runner``,
``reporting``, the import tools or anything under screener/. Loading it
never raises and never touches the database.

The storage gate (FW-03, FW-06)
-------------------------------
There is no hazard table in the schema. The table this module reads,
``parcel_hazards``, is a PROPOSAL (``PROPOSED_DDL`` below, the same text as
reviews/26_flood_wetlands/01_prepare_mutation_proposal.md). Only a human can
add it, by putting the constant ``_DDL_PARCEL_HAZARDS`` into prepare.py
through the prepare-mutation protocol. ``enabled()`` looks for that
constant and nothing else: it costs no SQL, so while the constant is absent
every consumer issues exactly the statements it issued before this module
existed, and H4/H6 keep their stub behaviour (data_gap flag, pass).

The constant is read from prepare.py's SOURCE TEXT on disk, once, when this
module loads (``_SOURCE_DDL``), never from the in-process ``prepare``
module object. An assignment or deletion of ``prepare._DDL_PARCEL_HAZARDS``
at runtime (for example from research.py, the agent sandbox) therefore
cannot switch the gate on, or off once a human has switched it on
(review finding 2; ``tests/test_hazards_scoring.StaticResearchTests`` also
bans any store to ``prepare.<attr>`` in research.py).

Three states per parcel and per source (FW-04)
----------------------------------------------
- ``absent``: no row, or the source never imported for the parcel: pass
  with a data_gap flag that names the cause.
- ``stale``: the row's stored md5(ST_AsBinary(geometry)) differs from the
  parcel's current geometry (discovery rewrote it): pass with a data_gap.
- ``fresh``: a real gate.
A failed read (``unreadable``) is a data_gap pass too, never a block.

H4 (program.md: "Not in FEMA Zone A or AE (100-year floodplain). Zone X and
B/C acceptable. Zone AE with LOMR/LOMA may pass, flag for review")
--------------------------------------------------------------------
On a fresh NFHL row, with ``hard_filters.flood_zones_blocked`` (["A","AE"])
and ``hard_filters.flood_zones_flag_for_review`` (["AE_with_LOMR"]) read
from ``prepare.get_parameters()`` only (SR-3):

- any area of a blocked zone on the parcel at or above ``NOISE_FLOOR_SF``
  (1 SF: SiteFit's ``a2 < 1`` clip drop, numeric noise, not policy; FW-10)
  blocks, through gate 4 with blocker text ``H4: ...`` (FW-02);
- a regulatory floodway (ZONE_SUBTY containing FLOODWAY) blocks, always,
  and is named separately: it lies inside the SFHA, and no LOMR relaxes it;
- a ``<ZONE>_with_LOMR`` token relaxes ``<ZONE>`` (outside any floodway) to
  a review flag only when the LOMR polygons of the NFHL LOMR layer cover
  that zone's whole area on the parcel: the part of the zone inside the
  LOMR union is at least the noise floor and the part outside it is below
  the noise floor (``nfhl_zone_lomr_sf``). A LOMR elsewhere on the lot, or
  one that covers only part of the zone, relaxes nothing (review findings
  3 and 4: the effective NFHL already folds LOMRs in, so AE outside a LOMR
  is plain effective AE). This is program.md's "may pass" clause and it
  RELAXES a block; the reading is for the human to confirm (00_design.md
  H4). A LOMA is never detectable from these layers: every text says
  "LOMA not checked" (FW-09);
- other SFHA zones (SFHA_TF = 'T' outside the blocked list: AH, AO, A99,
  AR, V, VE), the 1% future-conditions floodplain, levee-reduced areas and
  open water are review flags, never blocks (FW-08);
- Zone D, AREA NOT INCLUDED or a part of the parcel no NFHL polygon covers
  is a data_gap: NFHL tiles every mapped area, X included, so "nothing
  returned" means unmapped, not clear. The 0.2% (x500) zone passes.

H6 (program.md: "No NWI-mapped wetlands covering >20% of parcel. Minor
fringe acceptable if buildable area still meets minimum footprint")
--------------------------------------------------------------------
Fails only when 100 * wetland_sf / parcel_sf > wetlands_max_pct_of_parcel,
strictly, compared exactly (fractions, unrounded; FW-07). A share above 0
and at most the threshold passes with a review flag ("minimum footprint
undefined"): parameters.json has no minimum footprint, and a hard filter
never depends on site fit or draft zoning (FW-15). Every NWI polygon counts
(wetlands AND deepwater habitats, as SiteFit counts them); a review flag
names a share made up mostly of deepwater or excavated classes.

Areas are computed by hazards_import.py in PostGIS, SRID 2240 (NAD83 /
Georgia West, US survey feet), over the same made-valid parcel geometry as
site_fit and S2. Web Mercator is never used. The service's Shape_Area,
Shape_Length and ACRES fields are never requested or read (FW-11).
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import prepare

# ---------------------------------------------------------------------------
# The storage gate
# ---------------------------------------------------------------------------
GATE_CONSTANT = "_DDL_PARCEL_HAZARDS"
TABLE = "parcel_hazards"

# The proposed DDL (reviews/26_flood_wetlands/01_prepare_mutation_proposal.md
# carries the same text; a test compares them). Idempotent.
PROPOSED_DDL = """
CREATE TABLE IF NOT EXISTS parcel_hazards (
    parcel_id TEXT PRIMARY KEY REFERENCES parcels(parcel_id) ON DELETE CASCADE,
    nfhl_parcel_geom_md5 TEXT,
    nfhl_parcel_sf NUMERIC,
    nfhl_mapped_sf NUMERIC,
    nfhl_undetermined_sf NUMERIC,
    sfha_sf NUMERIC,
    floodway_sf NUMERIC,
    x500_sf NUMERIC,
    sfha_pct NUMERIC,
    floodway_pct NUMERIC,
    x500_pct NUMERIC,
    nfhl_zone_sf JSONB,
    nfhl_zone_lomr_sf JSONB,
    zones TEXT[],
    bfe_ft NUMERIC,
    lomr_status TEXT,
    nfhl_as_of TEXT,
    nfhl_source TEXT,
    nfhl_fetched_at TIMESTAMPTZ,
    sfha_geom geometry(MultiPolygon, 2240),
    nwi_parcel_geom_md5 TEXT,
    nwi_parcel_sf NUMERIC,
    wetland_sf NUMERIC,
    wetland_pct NUMERIC,
    wetland_deep_excavated_sf NUMERIC,
    wetland_type_sf JSONB,
    nwi_as_of TEXT,
    nwi_source TEXT,
    nwi_fetched_at TIMESTAMPTZ,
    wetland_geom geometry(MultiPolygon, 2240)
);
"""
PROPOSED_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_hazards_sfha_geom ON parcel_hazards USING GIST(sfha_geom);",
    "CREATE INDEX IF NOT EXISTS idx_hazards_wetland_geom ON parcel_hazards USING GIST(wetland_geom);",
)
COLUMNS = (
    "parcel_id", "nfhl_parcel_geom_md5", "nfhl_parcel_sf", "nfhl_mapped_sf",
    "nfhl_undetermined_sf", "sfha_sf", "floodway_sf", "x500_sf", "sfha_pct", "floodway_pct",
    "x500_pct", "nfhl_zone_sf", "nfhl_zone_lomr_sf", "zones", "bfe_ft", "lomr_status", "nfhl_as_of", "nfhl_source",
    "nfhl_fetched_at", "sfha_geom", "nwi_parcel_geom_md5", "nwi_parcel_sf", "wetland_sf",
    "wetland_pct", "wetland_deep_excavated_sf", "wetland_type_sf", "nwi_as_of", "nwi_source",
    "nwi_fetched_at", "wetland_geom",
)


def _string_value(node: ast.AST) -> str | None:
    """A string literal, or a ``+`` concatenation of string literals."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _string_value(node.left), _string_value(node.right)
        return a + b if a is not None and b is not None else None
    return None


def ddl_from_prepare_source(path: Any = None) -> str | None:
    """The module-level string ``_DDL_PARCEL_HAZARDS = "..."`` of prepare.py
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


# Frozen at load from prepare.py's source text (review finding 2): runtime
# assignments to ``prepare._DDL_PARCEL_HAZARDS`` change nothing.
_SOURCE_DDL: str | None = ddl_from_prepare_source()


def enabled() -> bool:
    """True only once a human has added ``_DDL_PARCEL_HAZARDS`` to prepare.py
    (its source text, frozen at load). No SQL: the gate costs nothing while
    it is off (FW-03)."""
    ddl = _SOURCE_DDL
    return isinstance(ddl, str) and TABLE in ddl


def schema_problem() -> str | None:
    """Why prepare.py's DDL cannot be read by this module, or None. A DDL
    that lacks a column this module reads is never queried (every parcel
    is then a data_gap pass, FW-06)."""
    ddl = _SOURCE_DDL
    if not isinstance(ddl, str):
        return "no hazard table in prepare.py"
    missing = [c for c in COLUMNS if c not in ddl]
    return f"hazard table lacks {len(missing)} expected column(s)" if missing else None


# ---------------------------------------------------------------------------
# Units, area of use, categories
# ---------------------------------------------------------------------------
SRID_FEET = 2240
# EPSG:2240 (NAD83 / Georgia West, ftUS) area of use, lon/lat degrees.
AREA_OF_USE = (-85.61, 30.62, -82.99, 35.01)
SUPPORTED_COUNTIES = ("fulton",)
# SiteFit drops a clipped piece under 1 SF (``a2 < 1``): numeric noise from
# the clip, not a sliver tolerance. Nothing below it is counted anywhere.
NOISE_FLOOR_SF = 1.0

FLOODWAY = "floodway"
SFHA = "sfha"
X500 = "x500"
MINIMAL = "minimal"
UNDETERMINED = "undetermined"
OPEN_WATER = "open_water"
CATEGORIES = (FLOODWAY, SFHA, X500, MINIMAL, UNDETERMINED, OPEN_WATER)
UNDETERMINED_ZONES = frozenset({"D", "AREA NOT INCLUDED", ""})

ABSENT, STALE, FRESH, UNREADABLE = "absent", "stale", "fresh", "unreadable"
PASS, BLOCK, FAIL, REVIEW, GAP = "pass", "block", "fail", "review", "gap"

# flagged_items.flag_type for "flag for review": no gate reads it (gate 4
# reads only 'actionability_block'), so a review never moves actionability.
REVIEW_FLAG_TYPE = "review"
REVIEW_RESOLUTION = ("review the FEMA/NWI maps for this parcel (screening only, "
                     "reviews/26_flood_wetlands)")
GAP_RESOLUTION = ("run make hazards-import between runs, before the run's baseline "
                  "(reviews/26_flood_wetlands)")
MULTIPOLYGON_MARKER = "multi-polygon parcel reduced to largest outer ring"

LOMA_NOT_CHECKED = "LOMA not checked"
NFHL_DISCLAIMER = "screening only; not an official determination (the effective FIRM governs)"
NWI_DISCLAIMER = "NWI is not a jurisdictional delineation (1:24,000 screening scale)"
STREAM_BUFFERS = "stream/state-waters buffers not deducted"

# NWI classes that are deepwater habitat rather than wetland (WETLAND_TYPE).
DEEPWATER_TYPES = frozenset({"lake", "riverine", "estuarine and marine deepwater"})


def norm(v: Any) -> str:
    """NFHL attribute normalisation: strip, collapse spaces, upper (FW-08)."""
    return " ".join(str(v if v is not None else "").split()).upper()


def classify_nfhl(fld_zone: Any, zone_subty: Any, sfha_tf: Any) -> str:
    """One NFHL flood hazard polygon -> category, SiteFit's precedence first
    (floodway > sfha > x500), then the cases SiteFit drops."""
    zone, sub, tf = norm(fld_zone), norm(zone_subty), norm(sfha_tf)
    if "FLOODWAY" in sub:
        return FLOODWAY
    if tf == "T":
        return SFHA
    if zone in UNDETERMINED_ZONES:
        return UNDETERMINED
    if "0.2" in sub:
        return X500
    if zone == "OPEN WATER":
        return OPEN_WATER
    return MINIMAL


def zone_key(fld_zone: Any, zone_subty: Any, sfha_tf: Any) -> str:
    """Stable key of an NFHL attribute combination: 'ZONE|SUBTYPE|T/F'."""
    return f"{norm(fld_zone)}|{norm(zone_subty)}|{norm(sfha_tf)}"


def is_deepwater(wetland_type: Any) -> bool:
    return " ".join(str(wetland_type or "").split()).lower() in DEEPWATER_TYPES


def is_excavated(attribute: Any) -> bool:
    """NWI special modifier 'x' (excavated) in the trailing lowercase run of
    the Cowardin code, e.g. PUBHx."""
    code = str(attribute or "").strip()
    tail = ""
    for ch in reversed(code):
        if ch.islower():
            tail = ch + tail
        else:
            break
    return "x" in tail


def exceeds_pct(part: Any, whole: Any, threshold_pct: Any) -> bool:
    """100 * part / whole > threshold, strictly, exactly: no float rounding
    can move the boundary (FW-07). The one place this comparison is made."""
    p, w, t = Fraction(str(part)), Fraction(str(whole)), Fraction(str(threshold_pct))
    return w > 0 and 100 * p > t * w


def at_least(v: Any, floor: float = NOISE_FLOOR_SF) -> bool:
    return v is not None and Fraction(str(v)) >= Fraction(str(floor))


def pct_text(part: Any, whole: Any) -> str:
    """Display share, two decimals ('<0.01%' when positive but tiny)."""
    if part is None or whole is None or Fraction(str(whole)) <= 0:
        return "—"
    v = 100 * Fraction(str(part)) / Fraction(str(whole))
    if 0 < v < Fraction(1, 100):
        return "<0.01%"
    return f"{float(v):.2f}%"


def in_area_of_use(lng: Any, lat: Any) -> bool:
    try:
        x, y = float(lng), float(lat)
    except (TypeError, ValueError):
        return False
    a = AREA_OF_USE
    return a[0] <= x <= a[2] and a[1] <= y <= a[3]


# ---------------------------------------------------------------------------
# The read (one statement per cycle, only while enabled())
# ---------------------------------------------------------------------------
_SQL_FLAG_DESCRIPTION = (
    "CASE WHEN strpos(description, '; ') > 0 "
    "THEN substr(description, strpos(description, '; ') + 2) "
    "ELSE description END"
)
# Parameters: (ids, ids, MULTIPOLYGON_MARKER, ids). Reads the open review
# flags (idempotency, FW-17) and the multi-polygon data_gap (FW-22) once.
_SQL_HAZARDS_FOR_PARCELS = (
    "WITH rv AS (SELECT parcel_id, array_agg(DISTINCT " + _SQL_FLAG_DESCRIPTION + ") AS d "
    "FROM flagged_items WHERE flag_type = 'review' AND status = 'open' "
    "AND parcel_id = ANY(%s) GROUP BY parcel_id), "
    "mp AS (SELECT DISTINCT parcel_id FROM flagged_items WHERE flag_type = 'data_gap' "
    "AND status = 'open' AND parcel_id = ANY(%s) AND strpos(description, %s) > 0) "
    "SELECT p.parcel_id, md5(ST_AsBinary(p.geometry)), "
    "h.nfhl_parcel_geom_md5, h.nfhl_parcel_sf, h.nfhl_mapped_sf, h.nfhl_undetermined_sf, "
    "h.sfha_sf, h.floodway_sf, h.x500_sf, h.nfhl_zone_sf, h.nfhl_zone_lomr_sf, h.zones, "
    "h.bfe_ft, "
    "h.lomr_status, h.nfhl_as_of, h.nfhl_fetched_at, "
    "h.nwi_parcel_geom_md5, h.nwi_parcel_sf, h.wetland_sf, h.wetland_deep_excavated_sf, "
    "h.wetland_type_sf, h.nwi_as_of, h.nwi_fetched_at, "
    "rv.d, (mp.parcel_id IS NOT NULL) "
    "FROM parcels p "
    "LEFT JOIN parcel_hazards h ON h.parcel_id = p.parcel_id "
    "LEFT JOIN rv ON rv.parcel_id = p.parcel_id "
    "LEFT JOIN mp ON mp.parcel_id = p.parcel_id "
    "WHERE p.parcel_id = ANY(%s) ORDER BY p.parcel_id"
)
ROW_FIELDS = (
    "parcel_id", "geom_md5", "nfhl_md5", "nfhl_parcel_sf", "nfhl_mapped_sf",
    "nfhl_undetermined_sf", "sfha_sf", "floodway_sf", "x500_sf", "nfhl_zone_sf",
    "nfhl_zone_lomr_sf", "zones", "bfe_ft", "lomr_status", "nfhl_as_of", "nfhl_fetched_at", "nwi_md5", "nwi_parcel_sf",
    "wetland_sf", "wetland_deep_excavated_sf", "wetland_type_sf", "nwi_as_of",
    "nwi_fetched_at", "open_reviews", "multipart",
)

_INTRANS, _IDLE = 2, 0  # psycopg.pq.TransactionStatus values (site_fit's pattern)


def _query(conn: Any, sql: str, params: tuple) -> list[tuple]:
    """One read that never poisons the caller's connection: a SAVEPOINT
    inside an open transaction, a rollback on an idle one (site_fit._query)."""
    status = getattr(getattr(conn, "info", None), "transaction_status", None)
    status = int(status) if status is not None else None
    if status == _INTRANS:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    except Exception:
        if status == _IDLE:
            conn.rollback()
        raise


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Assessment:
    parcel_id: str
    flood_state: str
    wetland_state: str
    h4: str
    h6: str
    blocker: str | None
    data_gaps: tuple[str, ...]
    reviews: tuple[str, ...]
    open_reviews: frozenset[str]
    multipart: bool
    flood_line: str
    wetlands_line: str


def _json_map(v: Any) -> dict[str, Any]:
    if v is None:
        return {}
    if isinstance(v, (str, bytes)):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return dict(v) if isinstance(v, Mapping) else {}


def _state(stored_md5: Any, current_md5: Any, fetched_at: Any) -> str:
    if stored_md5 is None or fetched_at is None:
        return ABSENT
    if current_md5 is None or str(stored_md5) != str(current_md5):
        return STALE
    return FRESH


def _relaxed_zones(tokens: Iterable[Any]) -> frozenset[str]:
    """'AE_with_LOMR' -> {'AE'}: the zones a LOMR relaxes to a review. Any
    other token form relaxes nothing (it is not an NFHL value)."""
    out = set()
    for t in tokens or ():
        s = str(t)
        if s.upper().endswith("_WITH_LOMR") and len(s) > len("_with_lomr"):
            out.add(norm(s[: -len("_with_lomr")]))
    return frozenset(out)


_GAP_TEXT = {
    ("H4", ABSENT): "H4 flood: no hazards import for this parcel (FEMA NFHL not assessed)",
    ("H4", STALE): ("H4 flood: hazard data stale (parcel geometry changed since the "
                    "hazards import)"),
    ("H4", UNREADABLE): "H4 flood: hazard data unreadable (FEMA NFHL not assessed)",
    ("H6", ABSENT): "H6 wetlands: no hazards import for this parcel (USFWS NWI not assessed)",
    ("H6", STALE): ("H6 wetlands: hazard data stale (parcel geometry changed since the "
                    "hazards import)"),
    ("H6", UNREADABLE): "H6 wetlands: hazard data unreadable (USFWS NWI not assessed)",
}
_LINE_REASON = {
    ABSENT: "no hazards import for this parcel",
    STALE: "hazard data stale: parcel geometry changed since the import",
    UNREADABLE: "hazard data unreadable",
}
_MULTIPART_NOTE = ("parcel stored as its largest outer ring (multi-polygon data_gap): "
                   "shares cover that ring only")


def _flood(r: Mapping[str, Any], params: Mapping[str, Any]) -> tuple[str, list, list, list, str]:
    """(verdict, blockers, gaps, reviews, memo line) for a FRESH NFHL row."""
    hf = params["hard_filters"]
    blocked = frozenset(norm(z) for z in hf["flood_zones_blocked"])
    relaxed = _relaxed_zones(hf.get("flood_zones_flag_for_review") or ())
    parcel_sf = r["nfhl_parcel_sf"]
    zmap = _json_map(r["nfhl_zone_sf"])
    lmap = _json_map(r.get("nfhl_zone_lomr_sf"))
    zone_all: dict[str, Fraction] = {}
    zone_in_lomr: dict[str, Fraction] = {}
    for key, sf in zmap.items():
        z = norm(str(key).partition("|")[0])
        zone_all[z] = zone_all.get(z, Fraction(0)) + Fraction(str(sf or 0))
    for key, sf in lmap.items():
        z = norm(str(key).partition("|")[0])
        zone_in_lomr[z] = zone_in_lomr.get(z, Fraction(0)) + Fraction(str(sf or 0))
    by_zone: dict[str, Fraction] = {}
    other_sfha: set[str] = set()
    future = levee = water = False
    for key, sf in sorted(zmap.items()):
        if not at_least(sf):
            continue
        zone, _, rest = str(key).partition("|")
        sub, _, tf = rest.partition("|")
        zone, sub, tf = norm(zone), norm(sub), norm(tf)
        by_zone[zone] = by_zone.get(zone, Fraction(0)) + Fraction(str(sf))
        if tf == "T" and zone not in blocked:
            other_sfha.add(zone)
        if "FUTURE" in sub:
            future = True
        if "LEVEE" in sub:
            levee = True
        if zone == "OPEN WATER":
            water = True
    blockers, gaps, reviews = [], [], []
    present_blocked = sorted(z for z in by_zone if z in blocked)
    lomr = str(r.get("lomr_status") or "")
    if at_least(r["floodway_sf"]):
        blockers.append("H4: FEMA regulatory floodway on parcel (always blocked; "
                        f"{LOMA_NOT_CHECKED})")
    if present_blocked:
        def covered(z: str) -> bool:
            # The LOMR union covers the zone's whole area on the parcel
            # (review findings 3/4): inside >= floor, outside < floor.
            inside = zone_in_lomr.get(z, Fraction(0))
            outside = zone_all.get(z, Fraction(0)) - inside
            return at_least(inside) and not at_least(outside)
        hard = [z for z in present_blocked
                if not (z in relaxed and lomr == "intersects" and covered(z))]
        soft = [z for z in present_blocked if z not in hard]
        partial = [z for z in hard if z in relaxed and lomr == "intersects"
                   and at_least(zone_in_lomr.get(z, Fraction(0)))]
        if hard:
            note = (f"; a LOMR covers only part of zone {'/'.join(partial)} on the parcel"
                    if partial else "")
            blockers.append(f"H4: FEMA SFHA zone {'/'.join(hard)} on parcel "
                            f"(flood_zones_blocked{note}; {LOMA_NOT_CHECKED})")
        if soft:
            reviews.append(f"H4 flood: zone {'/'.join(soft)} on parcel lies wholly within a "
                           "LOMR: passes, flag for review (program.md 'may pass'); "
                           f"{LOMA_NOT_CHECKED}")
    for z in sorted(other_sfha):
        reviews.append(f"H4 flood: SFHA zone {z} on parcel (not in flood_zones_blocked): "
                       "flag for review")
    if future:
        reviews.append("H4 flood: 1% future-conditions floodplain on parcel (locally "
                       "regulated): flag for review")
    if levee:
        reviews.append("H4 flood: levee-reduced flood risk area on parcel: flag for review")
    if water:
        reviews.append("H4 flood: NFHL open water on parcel: flag for review")
    mapped = r["nfhl_mapped_sf"]
    unmapped = (Fraction(str(parcel_sf)) - Fraction(str(mapped))
                if parcel_sf is not None and mapped is not None else None)
    if mapped is None or not at_least(mapped):
        gaps.append("H4 flood: no NFHL flood hazard polygon on the parcel (unmapped, "
                    "not clear)")
    elif at_least(r["nfhl_undetermined_sf"]) or (unmapped is not None and at_least(unmapped)):
        gaps.append("H4 flood: part of the parcel has no FEMA flood determination "
                    "(Zone D, area not included, or unmapped)")
    verdict = BLOCK if blockers else GAP if gaps else REVIEW if reviews else PASS
    zones = ", ".join(sorted({norm(z) for z in (r.get("zones") or ())})) or "none"
    bfe = r.get("bfe_ft")
    bfe_txt = f"{float(bfe):g} ft" if bfe is not None and float(bfe) > -9000 else "—"
    line = (f"FEMA zones {zones}; SFHA {pct_text(r['sfha_sf'], parcel_sf)} (floodway "
            f"{pct_text(r['floodway_sf'], parcel_sf)}), 0.2% annual chance "
            f"{pct_text(r['x500_sf'], parcel_sf)}; BFE {bfe_txt}; NFHL "
            f"{r.get('nfhl_as_of') or '—'}; H4 {verdict}; {LOMA_NOT_CHECKED}; "
            f"{NFHL_DISCLAIMER}")
    return verdict, blockers, gaps, reviews, line


def _wetlands(r: Mapping[str, Any], params: Mapping[str, Any]) -> tuple[str, list, list, list, str]:
    """(verdict, blockers, gaps, reviews, memo line) for a FRESH NWI row."""
    thr = params["hard_filters"]["wetlands_max_pct_of_parcel"]
    parcel_sf, wet = r["nwi_parcel_sf"], r["wetland_sf"]
    blockers, gaps, reviews = [], [], []
    if parcel_sf is None or wet is None or Fraction(str(parcel_sf)) <= 0:
        gaps.append("H6 wetlands: parcel area unavailable in the hazards import")
        return GAP, blockers, gaps, reviews, f"— ({gaps[0]})"
    thr_txt = f"{float(thr):g}%"
    if exceeds_pct(wet, parcel_sf, thr):
        blockers.append(f"H6: NWI-mapped wetlands cover more than {thr_txt} of the parcel "
                        "(wetlands_max_pct_of_parcel)")
        cls = "significant"
    elif at_least(wet):
        reviews.append(f"H6 wetlands: minor fringe (above 0% and at most {thr_txt} of the "
                       "parcel); minimum footprint undefined, not tested: flag for review")
        cls = "minor fringe"
    else:
        cls = "none"
    deep = r.get("wetland_deep_excavated_sf")
    if at_least(wet) and deep is not None and 2 * Fraction(str(deep)) > Fraction(str(wet)):
        reviews.append("H6 wetlands: most of the NWI share is deepwater or excavated (x) "
                       "classes: flag for review")
    verdict = FAIL if blockers else REVIEW if reviews else PASS
    types = _json_map(r.get("wetland_type_sf"))
    tparts = [f"{t} {pct_text(sf, parcel_sf)}" for t, sf in sorted(types.items())
              if at_least(sf)]
    line = (f"{cls}: NWI {pct_text(wet if at_least(wet) else 0, parcel_sf)} of parcel"
            + (f" ({', '.join(tparts)})" if tparts else "")
            + f"; H6 {verdict}; NWI as of {r.get('nwi_as_of') or '—'}; {NWI_DISCLAIMER}")
    return verdict, blockers, gaps, reviews, line


def assess_row(r: Mapping[str, Any], params: Mapping[str, Any]) -> Assessment:
    """Pure: one read row (``ROW_FIELDS`` mapping) -> Assessment."""
    pid = str(r["parcel_id"])
    fstate = _state(r.get("nfhl_md5"), r.get("geom_md5"), r.get("nfhl_fetched_at"))
    wstate = _state(r.get("nwi_md5"), r.get("geom_md5"), r.get("nwi_fetched_at"))
    multipart = bool(r.get("multipart"))
    blockers: list[str] = []
    gaps: list[str] = []
    reviews: list[str] = []
    if fstate == FRESH:
        h4, b, g, rv, fline = _flood(r, params)
        blockers += b
        gaps += g
        reviews += rv
    else:
        h4, fline = GAP, f"— ({_LINE_REASON[fstate]}; H4 not assessed)"
        gaps.append(_GAP_TEXT[("H4", fstate)])
    if wstate == FRESH:
        h6, b, g, rv, wline = _wetlands(r, params)
        blockers += b
        gaps += g
        reviews += rv
    else:
        h6, wline = GAP, f"— ({_LINE_REASON[wstate]}; H6 not assessed)"
        gaps.append(_GAP_TEXT[("H6", wstate)])
    if multipart and (fstate == FRESH or wstate == FRESH):
        fline = fline + "; " + _MULTIPART_NOTE if fstate == FRESH else fline
        wline = wline + "; " + _MULTIPART_NOTE if wstate == FRESH else wline
    opened = frozenset(str(d) for d in (r.get("open_reviews") or ()) if d is not None)
    return Assessment(pid, fstate, wstate, h4, h6, "; ".join(blockers) or None,
                      tuple(gaps), tuple(reviews), opened, multipart, fline, wline)


def _gap_only(pid: str, state: str) -> Assessment:
    return Assessment(pid, state, state, GAP, GAP, None,
                      (_GAP_TEXT[("H4", state)], _GAP_TEXT[("H6", state)]), (), frozenset(),
                      False, f"— ({_LINE_REASON[state]}; H4 not assessed)",
                      f"— ({_LINE_REASON[state]}; H6 not assessed)")


def assess_parcels(conn: Any, parcel_ids: Sequence[str]) -> Mapping[str, Assessment]:
    """Assessments for ``parcel_ids``: one read, thresholds from
    prepare.get_parameters() only. Never raises; a failed read or a schema
    that differs from the proposal gives every parcel a data_gap pass.
    Returns an entry for EVERY requested id (no row = absent). Callers
    check ``enabled()`` first; this function reads the table regardless."""
    ids = sorted({str(p) for p in parcel_ids})
    if not ids:
        return {}
    if schema_problem() is not None:
        return {pid: _gap_only(pid, UNREADABLE) for pid in ids}
    params = prepare.get_parameters()
    try:
        rows = _query(conn, _SQL_HAZARDS_FOR_PARCELS, (ids, ids, MULTIPOLYGON_MARKER, ids))
    except Exception:
        return {pid: _gap_only(pid, UNREADABLE) for pid in ids}
    out: dict[str, Assessment] = {}
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) < len(ROW_FIELDS):
            continue
        rec = dict(zip(ROW_FIELDS, row))
        try:
            out[str(rec["parcel_id"])] = assess_row(rec, params)
        except Exception:
            out[str(rec["parcel_id"])] = _gap_only(str(rec["parcel_id"]), UNREADABLE)
    for pid in ids:
        out.setdefault(pid, _gap_only(pid, ABSENT))
    return out


# ---------------------------------------------------------------------------
# Site fit texts (the deduction itself is SQL inside site_fit)
# ---------------------------------------------------------------------------
def deduction_text(flood_state: str, wetland_state: str, sfha_present: bool | None = None,
                   wetland_present: bool | None = None, nfhl_gap: bool = False) -> str:
    """The FLOOD_WETLANDS constraint of a projection while the table is
    enabled: what was actually deducted, never more (FW-14). ``sfha_present``
    / ``wetland_present`` say whether the fresh row holds a deductible
    geometry (None = not known: the generic text); ``nfhl_gap`` that part of
    the parcel has no FEMA determination (review finding 9)."""
    parts = []
    if flood_state == FRESH:
        if sfha_present is False:
            parts.append("no SFHA on parcel, nothing deducted for flood (0.2% zone not "
                         "deducted)")
        else:
            parts.append("SFHA incl. floodway deducted (0.2% zone not deducted)")
        if nfhl_gap:
            parts.append("part of the parcel has no FEMA flood determination (not deducted)")
    if wetland_state == FRESH:
        parts.append("no NWI wetlands on parcel" if wetland_present is False
                     else "NWI wetlands deducted")
    missing = [n for n, s in (("flood", flood_state), ("wetlands", wetland_state))
               if s != FRESH]
    if missing:
        why = sorted({_LINE_REASON.get(s, s) for n, s in (("flood", flood_state),
                                                            ("wetlands", wetland_state))
                      if s != FRESH})
        parts.append(f"{'/'.join(missing)} not deducted ({'; '.join(why)})")
    if flood_state == FRESH:
        parts.append("fill/compensatory storage not considered")
    return "; ".join(parts)
