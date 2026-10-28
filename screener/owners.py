"""Owner classification from the assessor's owner name and mailing address.

Keyword rules, deliberately conservative: a wrong "developer" tag hides a
parcel, so developer families are matched on distinctive names only.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "PR",
}

# Industrial developers / REITs / logistics owners seen on metro Atlanta
# parcels (research 2026-10-27). They own through special-purpose LLCs, so
# this catches only the ones whose entity names reveal them.
DEVELOPER_PATTERNS = [
    r"\bPROLOGIS\b", r"\bAMB PROPERTY\b", r"\bLINK (INDUSTRIAL|LOGISTICS)\b",
    r"\bEASTGROUP\b", r"\bDUKE REALTY\b", r"\bEXETER\b",
    r"\bBRIDGE (INDUSTRIAL|POINT|DEVELOPMENT)\b", r"\bMAJESTIC\b",
    r"\bLOGISTICS PROPERTY\b", r"\bINDUSTRIAL LOGISTICS REALTY\b",
    r"\bBREIT\b", r"\bBPREP\b", r"\bTRAMMELL CROW\b", r"\bCROW HOLDINGS\b",
    r"\bSTONEMONT\b", r"\bFIRST INDUSTRIAL\b", r"\bOAKMONT\b", r"\bIDI LOGISTICS\b",
    r"\bDERMODY\b", r"\bDPIF\b", r"\bLIBERTY PROPERTY\b", r"\bSTAG INDUSTRIAL\b",
    r"\bCABOT INDUSTRIAL\b", r"\bTA REALTY\b", r"\bAMAZON\b", r"\bSEEFRIED\b",
    r"\bPANATTONI\b", r"\bHILLWOOD\b", r"\bNORTHPOINT DEVELOPMENT\b",
    r"\bSCANNELL\b", r"\bRIDGELINE\b", r"\bCORE5\b", r"\bCENTERPOINT\b",
    r"\bBECKNELL\b", r"\bTRADEPORT\b", r"\bBLACK CREEK\b", r"\bINDUSTRIAL PROPERTY TRUST\b",
    r"\bLXP\b", r"\bWEEKS ROBINSON\b", r"\bDALFEN\b", r"\bPORTMAN INDUSTRIAL\b",
]
_DEVELOPER_RE = re.compile("|".join(DEVELOPER_PATTERNS))

_DEV_AUTHORITY_RE = re.compile(r"\bDEVELOPMENT AUTHORITY\b|\bDEV(ELOPMENT)? AUTH\b|\bDAFC\b")
_GOVERNMENT_RE = re.compile(
    r"\bCOUNTY\b|\bCITY OF\b|\bSTATE OF\b|\bUNITED STATES\b|\bU S A\b|\bBOARD OF\b|"
    r"\bDEPARTMENT\b|\bDEPT\b|\bHOUSING AUTHORITY\b|\bAIRPORT\b|\bMARTA\b|"
    r"\bSCHOOL\b|\bGEORGIA DOT\b|\bG D O T\b|\bAUTHORITY\b|\bGOVERNMENT\b"
)
_UTILITY_RE = re.compile(
    # RR / RY only with a company word: "SMITH R R" is a person's initials.
    r"\bRAILWAY\b|\bRAILROAD\b|\bRAIL ROAD\b|\bRWY\b|"
    r"\b(RR|R R|RY)\s+(CO|COMPANY|CORP|INC|SYSTEM)\b|\bLINE (RR|R R|RY)\b|"
    r"\bNORFOLK SOUTHERN\b|\bCSX\b|\bC S X\b|\bSEABOARD\b|\bCENTRAL OF (GA|GEORGIA)\b|"
    r"\bGEORGIA POWER\b|\bGAS LIGHT\b|\bNATURAL GAS\b|\bTRANSCO\b|\bPIPELINE\b|"
    r"\bPIPE LINE\b|\bELECTRIC MEMBERSHIP\b|\bGEORGIA TRANSMISSION\b|\bOGLETHORPE POWER\b|"
    r"\bMUNICIPAL ELECTRIC\b|\bBELLSOUTH\b|\bAT&T\b"
)
# Names typical of developer special-purpose entities ("PPF INDUSTRIAL
# ROYAL SOUTH LLC", "SRPF A GRAHAM ROAD INDUSTRIAL"). Also catches operating
# businesses ("ACME INDUSTRIAL SUPPLY"), so the live tool only flags it.
_INDUSTRIAL_NAME_RE = re.compile(
    r"\bINDUSTRIAL\b|\bLOGISTICS\b|\bDISTRIBUTION\b|\bWAREHOUSE\b|\bCOMMERCE (CENTER|PARK)\b|"
    r"\bTRADE CENTER\b|\bBUSINESS PARK\b|\bINDUSTRIAL PARK\b"
)
_RELIGIOUS_RE = re.compile(
    r"\bCHURCH\b|\bMINISTR(Y|IES)\b|\bTEMPLE\b|\bMOSQUE\b|\bBAPTIST\b|\bMETHODIST\b|"
    r"\bDIOCESE\b|\bCONGREGATION\b|\bCEMETERY\b"
)
_ESTATE_RE = re.compile(r"(?<!REAL )\bESTATE\b|\bEST OF\b|\bHEIRS\b|\bDECD\b|\bDECEASED\b|\bEXECUTOR\b|\bEXECUTRIX\b|\bADMINISTRAT")
_TRUST_RE = re.compile(r"\bTRUST\b|\bTRUSTEE\b|\bTRUSTEES\b|\bTR\b|\bTRS\b|\bREVOCABLE\b")
_ENTITY_RE = re.compile(
    r"\bLLC\b|\bL L C\b|\bINC\b|\bCORP\b|\bCORPORATION\b|\bLP\b|\bLLP\b|\bLTD\b|\bCO\b|"
    r"\bCOMPANY\b|\bPARTNERS\b|\bPARTNERSHIP\b|\bHOLDINGS\b|\bPROPERTIES\b|\bINVESTMENTS?\b|"
    r"\bGROUP\b|\bASSOCIATES\b|\bVENTURES?\b|\bFUND\b|\bREALTY\b|\bENTERPRISES\b|\bASSN\b|"
    r"\bASSOCIATION\b"
)
# A private-entity suffix overrides government/religious keywords
# ("AIRPORT DISTRIBUTION LLC", "CHURCH STREET PARTNERS LP").
_PRIVATE_SUFFIX_RE = re.compile(r"\bLLC\b|\bL L C\b|\bINC\b|\bCORP\b|\bLP\b|\bLLP\b|\bLTD\b")
_ZIP_TAIL_RE = re.compile(r"\b([A-Z]{2})\s+(\d{5})(-?\d{4})?\s*$")
_PO_BOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\b|\bPOB\b|\bPOST OFFICE BOX\b")


def normalize(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").upper().replace(",", " ").replace(".", " ")).strip()


def owner_type(name: str | None) -> str:
    """One of: development_authority, government, utility, religious,
    developer, estate, trust, entity, individual, unknown."""
    n = normalize(name)
    if not n:
        return "unknown"
    if _DEV_AUTHORITY_RE.search(n):
        return "development_authority"
    if _DEVELOPER_RE.search(n):
        return "developer"
    if _UTILITY_RE.search(n):
        return "utility"
    private = bool(_PRIVATE_SUFFIX_RE.search(n))
    if _GOVERNMENT_RE.search(n) and not private:
        return "government"
    if _RELIGIOUS_RE.search(n) and not private:
        return "religious"
    if _ESTATE_RE.search(n):
        return "estate"
    if _TRUST_RE.search(n):
        return "trust"
    if _ENTITY_RE.search(n):
        return "entity"
    return "individual"


def industrial_named(name: str | None) -> bool:
    """Owner name reads like an industrial developer vehicle or business."""
    return bool(_INDUSTRIAL_NAME_RE.search(normalize(name)))


def mailing_state(addr2: str | None) -> str | None:
    """State code from a 'CITY ST 12345' line, or None if it isn't a US address."""
    m = _ZIP_TAIL_RE.search(normalize(addr2))
    if m and m.group(1) in US_STATES:
        return m.group(1)
    return None


def is_po_box(addr1: str | None) -> bool:
    return bool(_PO_BOX_RE.search(normalize(addr1)))


_SUFFIX_RE = re.compile(
    r"\b(L ?L ?C|INC|CORP|CORPORATION|L ?P|LLP|LTD|CO|COMPANY|THE|TRUST|TRUSTEES?|TR|ET AL|ETAL)\b")


def _compact(name: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", _SUFFIX_RE.sub(" ", normalize(name)))


def same_owner(a: str | None, b: str | None) -> bool:
    """Loose match between a tax-roll owner and a deed grantor.

    The two files spell names differently ("SABEN L L C" / "SABEN, LLC",
    "TL COOK" / "T L COOK"), the deed file truncates at 30 characters, and
    typos happen ("SHROPSHINE" / "SHROPSHIRE").
    """
    ca, cb = _compact(a), _compact(b)
    if not ca or not cb:
        return False
    n = min(len(ca), len(cb), 12)
    if ca[:n] == cb[:n]:
        return True
    return SequenceMatcher(None, ca[:20], cb[:20]).ratio() >= 0.85
