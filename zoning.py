"""Zoning rules: load the human-owned facts file and classify districts.

Protected module (reviews/21_zoning/): immutable during a run, like
prepare.py's helpers. It imports neither research (the agent's sandbox) nor
runner (which imports research) nor anything under screener/.

What it owns
------------
1. ``zoning_rules.json`` (repo root, HUMAN-OWNED). Loaded ONCE per process
   at import, validated, deep-frozen (MappingProxyType) and fingerprinted by
   SHA-256. ``verify_rules_unchanged()`` re-hashes the file and raises
   ``ZoningRulesChanged`` when its bytes differ from import time (including
   appearing or disappearing). research.py calls it at discovery and scoring
   cycle entry, next to ``prepare.verify_parameters_unchanged()``: an edit
   mid-run (above all a draft -> verified flip) would change S9 and be
   credited to whichever experiment ran next (Z-01). Edit the file only
   between runs, then start a new run and baseline.

   A missing or invalid file never raises inside scoring: the module records
   the problem (``rules_problem()``), every classification is ``unknown``
   with ``verified=False``, and S9 keeps its stub value (Z-03).

2. The stored encoding of an imported district (Z-02). zoning_import.py
   writes::

       parcels.zoning             = <district code as the source gives it>
       parcels.zoning_description = "zoning_import v1 | <jurisdiction key> |
                                     <source key> | <district code> |
                                     <split|-> | <source description>"

   ``parse_stored()`` reads it back. A value without this provenance (for
   example a zoning code written by discovery) is ``unknown`` (Z-06): only
   the protected import tool can put a district in front of S9.

3. ``classify(jurisdiction_key, district)`` -> ``Classification`` with the
   entitlement class (by_right / conditional / rezoning / unknown), whether
   the jurisdiction's rules are verified, and a short reason.

Vocabulary (Z-08): permitted_by_right -> by_right; special_use_permit,
use_permit, conditional_use -> conditional; not_permitted -> rezoning;
anything else -> unknown (fail closed to the S9 stub). A district absent
from a jurisdiction's table follows that jurisdiction's human-owned
``unlisted_districts`` field: "not_permitted" -> rezoning, "unknown" ->
unknown (Z-07).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

RULES_PATH = Path(__file__).resolve().parent / "zoning_rules.json"

BY_RIGHT = "by_right"
CONDITIONAL = "conditional"
REZONING = "rezoning"
UNKNOWN = "unknown"
CLASSES = (BY_RIGHT, CONDITIONAL, REZONING, UNKNOWN)

# warehouse_distribution status vocabulary -> entitlement class (Z-08).
STATUS_TO_CLASS: Mapping[str, str] = types.MappingProxyType({
    "permitted_by_right": BY_RIGHT,
    "special_use_permit": CONDITIONAL,
    "use_permit": CONDITIONAL,
    "conditional_use": CONDITIONAL,
    "not_permitted": REZONING,
})
RULE_STATUSES = ("draft", "verified")
UNLISTED_SETTINGS = {"not_permitted": REZONING, "unknown": UNKNOWN}
LOCATION_EFFECTS = ("ignored", "downgrade_to_conditional")

# Stable jurisdiction keys. zoning_import.py maps Fulton TaxDist prefixes to
# these; zoning_rules.json and the sources.json snippet use the same ids.
KNOWN_JURISDICTIONS = frozenset({
    "atlanta", "alpharetta", "college_park", "east_point", "fairburn",
    "hapeville", "mountain_park", "palmetto", "roswell", "union_city",
    "south_fulton", "milton", "johns_creek", "sandy_springs",
    "chattahoochee_hills", "fid",
})

STORED_PREFIX = "zoning_import v1"
# H3 text for a parcel with no import provenance: never imported, imported
# but not matched by any source, or a zoning value written by something
# other than zoning_import (review finding 10).
H3_NOT_IMPORTED = ("H3 zoning not imported or not matched by any zoning source "
                   "(make zoning-import between runs)")
_FIELD_SEP = " | "
_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_MAX_CODE = 40
_MAX_DESC = 120
_SAFE_TEXT_RE = re.compile(r"[^A-Za-z0-9 &/().,+'#:_-]")


class ZoningRulesError(Exception):
    """zoning_rules.json is missing, not JSON, or violates the schema."""


class ZoningRulesChanged(Exception):
    """zoning_rules.json changed since this process loaded it (mid-run edit)."""


# ---------------------------------------------------------------------------
# Loading, validation, freeze
# ---------------------------------------------------------------------------
def _deep_freeze(obj: Any) -> Any:
    if isinstance(obj, dict):
        return types.MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


def validate_rules(data: Any) -> list[str]:
    """Schema problems of a parsed rules file (empty list = valid).

    Messages name jurisdiction keys and district codes only, never cited
    text.
    """
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["top level is not an object"]
    jur = data.get("jurisdictions")
    if not isinstance(jur, dict) or not jur:
        return ["'jurisdictions' must be a non-empty object"]
    for key, j in jur.items():
        where = f"jurisdiction {key!r}"
        if key not in KNOWN_JURISDICTIONS:
            errs.append(f"{where}: unknown jurisdiction key")
        if not isinstance(j, dict):
            errs.append(f"{where}: not an object")
            continue
        if j.get("status") not in RULE_STATUSES:
            errs.append(f"{where}: status must be one of {RULE_STATUSES}")
        if j.get("unlisted_districts") not in UNLISTED_SETTINGS:
            errs.append(f"{where}: unlisted_districts must be one of {sorted(UNLISTED_SETTINGS)}")
        if not isinstance(j.get("as_of"), str) or not j.get("as_of"):
            errs.append(f"{where}: as_of must be a non-empty string")
        for f in ("verified_by", "verified_on"):
            if f not in j:
                errs.append(f"{where}: {f} missing (null while draft)")
        if j.get("status") == "verified" and not (j.get("verified_by") and j.get("verified_on")):
            errs.append(f"{where}: verified needs verified_by and verified_on")
        if "governing_jurisdiction" in j:
            g = j["governing_jurisdiction"]
            if g != "self" and (g not in jur or g == key):
                errs.append(f"{where}: governing_jurisdiction must be 'self' or another key")
            if not isinstance(j.get("unincorporated_parcel_ids", []), list):
                errs.append(f"{where}: unincorporated_parcel_ids must be a list")
            conv = j.get("district_conversions", {})
            if not isinstance(conv, dict) or not all(
                    isinstance(a, str) and isinstance(b, str) for a, b in conv.items()):
                errs.append(f"{where}: district_conversions must map code -> code")
        districts = j.get("districts")
        if not isinstance(districts, dict):
            errs.append(f"{where}: districts must be an object")
            continue
        for code, d in districts.items():
            dw = f"{where} district {code!r}"
            if not isinstance(d, dict):
                errs.append(f"{dw}: not an object")
                continue
            wd = d.get("warehouse_distribution")
            if not isinstance(wd, dict):
                errs.append(f"{dw}: warehouse_distribution missing")
                continue
            if wd.get("status") not in STATUS_TO_CLASS:
                errs.append(f"{dw}: warehouse_distribution status not in the vocabulary")
            if not isinstance(wd.get("cite"), str) or not wd["cite"].strip():
                errs.append(f"{dw}: warehouse_distribution cite is empty")
            for lc in d.get("location_conditions", []) or []:
                if not isinstance(lc, dict) or lc.get("effect") not in LOCATION_EFFECTS:
                    errs.append(f"{dw}: location_conditions effect must be one of {LOCATION_EFFECTS}")
    return errs


@dataclass(frozen=True)
class _State:
    rules: Mapping[str, Any] | None
    sha256: str | None
    problem: str | None


def _file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def parse_rules_bytes(raw: bytes) -> Mapping[str, Any]:
    """Parse + validate rules bytes; raise ZoningRulesError; return frozen."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ZoningRulesError("zoning_rules.json is not valid UTF-8 JSON") from None
    errs = validate_rules(data)
    if errs:
        raise ZoningRulesError(f"zoning_rules.json schema: {errs[0]} ({len(errs)} problem(s))")
    return _deep_freeze(data)


def _load_state(path: Path) -> _State:
    """Read the rules file once. Never raises: a problem degrades to unknown."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _State(None, None, "zoning_rules.json is missing")
    except OSError as exc:
        return _State(None, None, f"zoning_rules.json unreadable ({type(exc).__name__})")
    sha = hashlib.sha256(raw).hexdigest()
    try:
        return _State(parse_rules_bytes(raw), sha, None)
    except ZoningRulesError as exc:
        return _State(None, sha, str(exc))


_STATE: _State = _load_state(RULES_PATH)


def load_rules() -> Mapping[str, Any]:
    """The frozen rules loaded at import (never a re-read). Raises when the
    file was missing or invalid at import; callers in scoring use
    ``classify`` instead, which degrades to ``unknown``."""
    if _STATE.rules is None:
        raise ZoningRulesError(_STATE.problem or "zoning rules unavailable")
    return _STATE.rules


def rules_problem() -> str | None:
    """Why the rules are unavailable in this process, or None when loaded."""
    return _STATE.problem


def rules_sha256() -> str | None:
    return _STATE.sha256


def verify_rules_unchanged(path: Path | None = None) -> None:
    """Raise ZoningRulesChanged if the file's bytes differ from import time.

    Call at cycle entry (discovery and scoring). A file that was missing at
    import and is still missing passes: nothing changed, S9 stays the stub.
    """
    current = _file_sha(path or RULES_PATH)
    if current != _STATE.sha256:
        raise ZoningRulesChanged(
            "zoning_rules.json changed since this process loaded it. Edits to the "
            "zoning rules are between-runs only: stop the loop, start a new run and "
            "baseline (reviews/21_zoning/02_risk_review.md, Z-01)."
        )


def _git(args: list[str], cwd: Path) -> str | None:
    """stdout of a git command, or None when it fails (no git, not a repo,
    path not in HEAD)."""
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                             text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def verify_rules_committed(path: Path | None = None) -> None:
    """Raise ZoningRulesChanged unless the file on disk is the committed one.

    ``verify_rules_unchanged`` compares against this process's import-time
    bytes, so a NEW process started after an uncommitted edit (a crashed
    experiment that was skipped, a restart, a revert of research.py only)
    would take the edited bytes as its baseline. This compares the working
    file's git blob id with ``HEAD:zoning_rules.json`` instead; the runner
    calls it before every experiment (review finding 3). Missing on disk
    AND absent from HEAD passes. Anything git cannot confirm fails loud.
    """
    p = (path or RULES_PATH).resolve()
    repo = p.parent
    top = _git(["rev-parse", "--show-toplevel"], repo)
    if top is None:
        raise ZoningRulesChanged(
            "cannot confirm zoning_rules.json against git HEAD (git unavailable or "
            "not a repository); the runner needs the committed rules file")
    rel = p.relative_to(Path(top).resolve()).as_posix()
    committed = _git(["rev-parse", "--verify", "--quiet", f"HEAD:{rel}"], repo)
    on_disk = _git(["hash-object", "--", str(p)], repo) if p.exists() else None
    if committed is None and on_disk is None:
        return
    if committed != on_disk:
        raise ZoningRulesChanged(
            "zoning_rules.json differs from the committed version (git HEAD). Edits "
            "to the zoning rules are between-runs only: commit them, then start a new "
            "run and baseline; or restore the file (git checkout -- zoning_rules.json)."
        )


def any_verified() -> bool:
    """True when at least one jurisdiction's rules are 'verified'."""
    rules = _STATE.rules
    if rules is None:
        return False
    return any(j.get("status") == "verified" for j in rules["jurisdictions"].values())


def jurisdiction_status(key: str | None) -> str:
    """'draft' / 'verified' for a jurisdiction in the rules, else 'none'."""
    rules = _STATE.rules
    if rules is None or key is None:
        return "none"
    j = rules["jurisdictions"].get(key)
    return j.get("status", "none") if j is not None else "none"


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------
def normalize(code: Any) -> tuple[str | None, bool]:
    """(base district, has_conditions) for a raw district code (Z-08).

    Uppercases, drops a trailing 'OVERLAY' and all whitespace, and maps a
    trailing '-C' (or 'C' right after a digit) to its base district:
    Atlanta 'I-1-C' means site conditions of zoning on an I-1 parcel, NOT a
    conditional use, so it never downgrades the class by itself.
    """
    if code is None:
        return None, False
    c = str(code).upper().strip()
    c = re.sub(r"\s*OVERLAY$", "", c)
    c = re.sub(r"\s+", "", c)
    if not c or c in {"UNKNOWN", "NONE", "NULL", "-"}:
        return None, False
    m = re.match(r"^(.+?)-C$", c)
    if m:
        return m.group(1), True
    m = re.match(r"^(.*\d)C$", c)
    if m:
        return m.group(1), True
    return c, False


def _clean(text: Any, limit: int) -> str:
    s = _SAFE_TEXT_RE.sub(" ", str(text or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:limit]


def encode_stored(jurisdiction_key: str, source_key: str, code: Any,
                  split: bool, source_desc: Any) -> tuple[str, str]:
    """(zoning, zoning_description) as zoning_import writes them."""
    if not _KEY_RE.match(jurisdiction_key) or jurisdiction_key not in KNOWN_JURISDICTIONS:
        raise ValueError("unknown jurisdiction key")
    if not _KEY_RE.match(source_key):
        raise ValueError("bad source key")
    zoning = _clean(code, _MAX_CODE)
    if not zoning:
        raise ValueError("empty district code")
    desc = _FIELD_SEP.join((
        STORED_PREFIX, jurisdiction_key, source_key, zoning,
        "split" if split else "-", _clean(source_desc, _MAX_DESC) or "-",
    ))
    return zoning, desc


def no_source_description(jurisdiction_key: str) -> str:
    """zoning_description for a parcel whose city has no machine-readable zoning."""
    if jurisdiction_key not in KNOWN_JURISDICTIONS:
        raise ValueError("unknown jurisdiction key")
    return _FIELD_SEP.join((STORED_PREFIX, jurisdiction_key, "-", "-", "-",
                            "no machine-readable zoning"))


@dataclass(frozen=True)
class Stored:
    jurisdiction: str
    source: str | None
    district: str | None
    split: bool


def parse_stored(zoning: Any, zoning_description: Any) -> Stored | None:
    """Read back what zoning_import wrote; None when there is no provenance.

    The district inside the description must equal parcels.zoning, so a
    zoning value changed by anything other than the import (discovery's
    upsert, a hand edit) loses its provenance and reads as unknown (Z-06).
    """
    if not isinstance(zoning_description, str):
        return None
    parts = zoning_description.split(_FIELD_SEP)
    if len(parts) != 6 or parts[0] != STORED_PREFIX:
        return None
    _, juris, source, code, split, _desc = parts
    if juris not in KNOWN_JURISDICTIONS:
        return None
    if source == "-" and code == "-":
        return Stored(juris, None, None, False) if zoning is None else None
    if not _KEY_RE.match(source) or not code or code == "-":
        return None
    if zoning is None or str(zoning) != code:
        return None
    return Stored(juris, source, code, split == "split")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Classification:
    cls: str
    verified: bool
    reason: str
    has_conditions: bool = False


def _unknown(reason: str) -> Classification:
    return Classification(UNKNOWN, False, reason)


def classify(jurisdiction_key: str | None, district: Any,
             parcel_id: str | None = None) -> Classification:
    """Entitlement class of a district in a jurisdiction. Never raises."""
    rules = _STATE.rules
    if rules is None:
        return _unknown("zoning rules unavailable")
    jurs = rules["jurisdictions"]
    j = jurs.get(jurisdiction_key) if jurisdiction_key else None
    if j is None:
        return _unknown("no zoning rules for this jurisdiction")
    base, conditions = normalize(district)
    if base is None:
        return _unknown("no district on file")
    verified = j.get("status") == "verified"
    table = j
    if "governing_jurisdiction" in j:
        gov = j.get("governing_jurisdiction")
        own = parcel_id is not None and parcel_id in (j.get("unincorporated_parcel_ids") or ())
        if gov != "self" and not own:
            g = jurs.get(gov) if isinstance(gov, str) else None
            if g is None:
                return _unknown("governing jurisdiction not in the rules")
            conv = {normalize(a)[0]: normalize(b)[0]
                    for a, b in (j.get("district_conversions") or {}).items()}
            base = conv.get(base, base)
            table = g
            verified = verified and g.get("status") == "verified"
    districts = {normalize(k)[0]: v for k, v in (table.get("districts") or {}).items()}
    d = districts.get(base)
    if d is None:
        cls = UNLISTED_SETTINGS.get(table.get("unlisted_districts"), UNKNOWN)
        return Classification(cls, verified, "district not listed in the rules", conditions)
    status = (d.get("warehouse_distribution") or {}).get("status")
    cls = STATUS_TO_CLASS.get(status, UNKNOWN)
    reason = "warehouse/distribution " + (status or "status missing")
    if cls == BY_RIGHT and any(
            (lc or {}).get("effect") == "downgrade_to_conditional"
            for lc in d.get("location_conditions") or ()):
        cls, reason = CONDITIONAL, reason + " (location condition)"
    return Classification(cls, verified, reason, conditions)


def classify_stored(zoning: Any, zoning_description: Any,
                    parcel_id: str | None = None) -> Classification:
    """Classify a parcel from its stored columns; no provenance -> unknown."""
    st = parse_stored(zoning, zoning_description)
    if st is None:
        return _unknown("zoning not imported")
    if st.district is None:
        return _unknown("no machine-readable zoning")
    return classify(st.jurisdiction, st.district, parcel_id)


def h3_description(zoning: Any, zoning_description: Any,
                   parcel_id: str | None = None) -> str:
    """Stable, short, sanitised H3 data_gap text (no dates; Z-15)."""
    st = parse_stored(zoning, zoning_description)
    if st is None:
        return H3_NOT_IMPORTED
    if st.district is None:
        return f"H3 zoning not machine-readable for {st.jurisdiction}: check with the city"
    c = classify(st.jurisdiction, st.district, parcel_id)
    extra = []
    if c.has_conditions:
        extra.append("conditions of zoning")
    if st.split:
        extra.append("split-zoned")
    tail = f" ({', '.join(extra)})" if extra else ""
    return (f"H3 zoning {_clean(st.district, _MAX_CODE)} ({st.jurisdiction}): {c.cls}, "
            f"rules {jurisdiction_status(st.jurisdiction)}{tail}")


def memo_text(zoning: Any, zoning_description: Any,
              parcel_id: str | None = None) -> str:
    """Human-readable zoning line for the strategy memo (review findings 9/11).

    An imported value renders as 'I-1 (atlanta, split-zoned): by_right,
    rules draft; source atlanta_zoning: Light Industrial' instead of the raw
    provenance string. A value without provenance renders as before:
    'zoning — description'.
    """
    st = parse_stored(zoning, zoning_description)
    if st is None:
        return f"{zoning if zoning is not None else '—'} — " \
               f"{zoning_description if zoning_description is not None else '—'}"
    if st.district is None:
        return f"no machine-readable zoning for {st.jurisdiction} (check with the city)"
    c = classify(st.jurisdiction, st.district, parcel_id)
    extra = [st.jurisdiction]
    if c.has_conditions:
        extra.append("conditions of zoning")
    if st.split:
        extra.append("split-zoned")
    desc = str(zoning_description).split(_FIELD_SEP)[-1]
    tail = f": {desc}" if desc and desc != "-" else ""
    return (f"{_clean(st.district, _MAX_CODE)} ({', '.join(extra)}): {c.cls}, rules "
            f"{jurisdiction_status(st.jurisdiction)}; source {st.source}{tail}")
