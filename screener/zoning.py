"""Turn city/county zoning codes and future-land-use labels into categories.

Codes differ by jurisdiction (South Fulton M-1/M-2, Atlanta I-1/I-2,
East Point I1/MI, Fairburn M-1/M-2, Union City M-1/M-2), so matching is on
normalized code families. A trailing "-C"/"C" (conditional) keeps the base
category.
"""

from __future__ import annotations

import re

INDUSTRIAL = "industrial"     # warehouse/manufacturing allowed by right
FLEX = "flex"                 # business park / industrial-mixed: often workable
REZONABLE = "rezonable"       # agricultural / rural / commercial: rezoning path
RESIDENTIAL = "residential"
OTHER = "other"               # office, institutional, special districts
UNKNOWN = "unknown"           # no machine-readable zoning for this parcel

_INDUSTRIAL_RE = re.compile(r"^(M-?1A?|M-?2|M-?3|I-?1|I-?2|I-?3|MI|LI|HI|IL|IH|IND|IG|IM)C?$")
_FLEX_RE = re.compile(r"^(BP|I-?MIX|IMU|MIC|MXCI)C?$")
_REZONABLE_RE = re.compile(r"^(AG(-?1)?|A(-?1)?|A-?L|RR|AR|C-?[1-5]|GC|CR|CL|HC|C2[CM]?|C1[CM]?)C?$")
_RESIDENTIAL_RE = re.compile(r"^(R|RM|RG|RMD|MR|PD-?H|PUD|R-?LC|MHP|TR|SUB|RL|RT|R1A?)")

_FLU_INDUSTRIAL_RE = re.compile(r"INDUSTR|LOGISTIC|BUSINESS PARK|DISTRIBUTION|MANUFACTUR|^I$|^BP$")
_FLU_RESIDENTIAL_RE = re.compile(
    r"RESIDENTIAL|NEIGHBORHOOD|^MDR$|^LDR$|^HDR$|^R$|SINGLE[- ]FAMILY"
)
_FLU_PROTECT_RE = re.compile(r"CONSERV|PRESERVE|GREENSPACE|OPEN SPACE|PARK(?!.*BUSINESS)")
_FLU_RURAL_RE = re.compile(r"RURAL|AGRICULTUR")


def _norm_code(code: str | None) -> str:
    c = (code or "").upper().strip()
    c = re.sub(r"\s*OVERLAY$", "", c)
    c = re.sub(r"-C$", "C", c)
    return c.replace(" ", "")


def zoning_category(code: str | None) -> str:
    c = _norm_code(code)
    if not c or c in {"UNKNOWN", "NONE", "NULL"}:
        return UNKNOWN
    if _INDUSTRIAL_RE.match(c):
        return INDUSTRIAL
    if _FLEX_RE.match(c):
        return FLEX
    if _REZONABLE_RE.match(c):
        return REZONABLE
    if _RESIDENTIAL_RE.match(c):
        return RESIDENTIAL
    return OTHER


def flu_category(label: str | None) -> str:
    t = (label or "").upper().strip()
    if not t:
        return UNKNOWN
    if _FLU_INDUSTRIAL_RE.search(t):
        return INDUSTRIAL
    if _FLU_PROTECT_RE.search(t):
        return "conservation"
    if _FLU_RURAL_RE.search(t):
        return "rural"
    if _FLU_RESIDENTIAL_RE.search(t):
        return RESIDENTIAL
    return OTHER


def entitlement(zoning_cat: str, flu_cat: str, industrial_neighbors: int) -> tuple[str, str]:
    """(path, explanation). path is by_right / likely / possible / hard / unknown."""
    if zoning_cat == INDUSTRIAL:
        return "by_right", "zoned industrial"
    if flu_cat == INDUSTRIAL:
        return "likely", "future land use map calls for industrial"
    if zoning_cat == FLEX:
        return "likely", "flex / business-park zoning"
    if flu_cat == "conservation":
        return "hard", "planned for conservation"
    if zoning_cat == RESIDENTIAL and flu_cat == RESIDENTIAL:
        return "hard", "zoned and planned residential"
    if zoning_cat == REZONABLE and industrial_neighbors >= 5:
        return "possible", f"rezoning needed; {industrial_neighbors} industrial parcels within 1 mile"
    if zoning_cat == RESIDENTIAL or flu_cat == RESIDENTIAL:
        if industrial_neighbors >= 10:
            return "possible", (f"residential designation but {industrial_neighbors} industrial "
                                "parcels within 1 mile")
        return "hard", "residential designation"
    if zoning_cat == REZONABLE:
        return "possible", "rezoning needed (agricultural/commercial today)"
    if zoning_cat == UNKNOWN and flu_cat == UNKNOWN:
        return "unknown", "no machine-readable zoning here; check with the city"
    return "possible", "rezoning likely needed"
