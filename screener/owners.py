"""Owner classification from the assessor's owner name and mailing address.

Keyword rules, deliberately conservative: a wrong "developer" tag hides a
parcel, so developer families are matched on distinctive names only.
"""

from __future__ import annotations

import re
from collections import Counter
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
# Distinctive names: matched anywhere.
DEVELOPER_PATTERNS = [
    r"\bPROLOGIS\b", r"\bAMB PROPERTY\b", r"\bLINK (INDUSTRIAL|LOGISTICS)\b",
    r"\bEASTGROUP\b", r"\bDUKE REALTY\b",
    r"\bBRIDGE (INDUSTRIAL|POINT|DEVELOPMENT)\b", r"\bLOGISTICS PROPERTY\b",
    r"\bINDUSTRIAL LOGISTICS REALTY\b", r"\bBPREP\b", r"\bTRAMMELL CROW\b",
    r"\bCROW HOLDINGS\b", r"\bSTONEMONT\b", r"\bFIRST INDUSTRIAL\b", r"\bIDI LOGISTICS\b",
    r"\bDPIF\b", r"\bLIBERTY PROPERTY\b", r"\bSTAG INDUSTRIAL\b",
    r"\bCABOT INDUSTRIAL\b", r"\bTA REALTY\b", r"\bAMAZON\b",
    r"\bNORTHPOINT DEVELOPMENT\b", r"\bCORE5\b", r"\bINDUSTRIAL PROPERTY TRUST\b",
    r"\bPORTMAN INDUSTRIAL\b",
]
_DEVELOPER_RE = re.compile("|".join(DEVELOPER_PATTERNS))
# Names that are also surnames ("SCANNELL MARY", "DERMODY PATRICK"): count
# only in a company's name, next to a legal form, an entity word or a
# real-estate word ("EXETER 650 SELIG L P", "DALFEN AMERICA CORP").
_DEVELOPER_SURNAME_RE = re.compile(
    r"\b(EXETER|BREIT|DERMODY|SEEFRIED|PANATTONI|HILLWOOD|SCANNELL|WEEKS ROBINSON|DALFEN)\b")
# Names that are also surnames or common words ("OAKMONT BAPTIST CHURCH",
# "LESLIE BECKNELL"): count only next to a real-estate word.
_DEVELOPER_WEAK_RE = re.compile(
    r"\b(MAJESTIC|OAKMONT|RIDGELINE|CENTERPOINT|BECKNELL|TRADEPORT|BLACK CREEK|LXP)\b")
_REAL_ESTATE_WORD_RE = re.compile(
    r"\b(INDUSTRIAL|LOGISTICS|REALTY|PROPERTIES|DEVELOPMENT|FUND|DISTRIBUTION|ACQUISITIONS?|"
    r"OWNER|AIRPORT CENTER|COMMERCE)\b")

_DEV_AUTHORITY_RE = re.compile(r"\bDEVELOPMENT AUTHORITY\b|\bDEV(ELOPMENT)? AUTH\b|\bDAFC\b")
# Public bodies, by the forms their names take on the tax roll.
_GOVERNMENT_RE = re.compile(
    r"\bCITY OF\b|\bCOUNTY OF\b|\bSTATE OF\b|\bTOWN OF\b|\bUNITED STATES\b|\bU S A\b|"
    r"\b(FULTON|DEKALB|CLAYTON|COBB|GWINNETT|HENRY|FAYETTE|SPALDING) COUNTY\b|"
    r"\bBOARD OF (EDUCATION|COMMISSIONERS|REGENTS)\b|\bDEPARTMENT OF\b|\bDEPT OF\b|"
    r"\bHOUSING AUTHORITY\b|\bAIRPORT AUTHORITY\b|\bWATER (AND SEWER )?AUTHORITY\b|"
    r"\bMARTA\b|\bMETROPOLITAN ATLANTA RAPID\b|\bGEORGIA DOT\b|\bG D O T\b|"
    r"\bSCHOOL DISTRICT\b|\bPUBLIC SCHOOLS\b|\bUNIVERSITY SYSTEM\b|\bGOVERNMENT\b|"
    # Federal agencies ("VETERANS ADMINISTRATION", "SECRETARY OF HOUSING ...").
    r"\b(VETERANS|SMALL BUSINESS|GENERAL SERVICES|FARMERS HOME|SOCIAL SECURITY|FEDERAL [A-Z]+) "
    r"ADMIN(ISTRATION)?\b|\bVETERANS AFFAIRS\b|\bSECRETARY OF\b"
)
# Homeowners' / property owners' / community / neighborhood / condominium /
# civic associations own subdivision common areas, not land for sale. The
# owners' word must come right before the association word: ASSOCIATION
# alone also names banks and trade bodies, and ASSOC alone is often
# ASSOCIATES. A trailing HOMEOWNERS ("... CROSSING HOMEOWNERS INC") counts.
# So does any owners' word, misspelt or glued to the word before it ("UNIT
# OWNERS", "INDUSTRIAL PARK OWNERS", "HOMEOWNRS", "HMOWNERS",
# "TOWNHOMEOWNERS"), but not TOWERS / POWERS. A planned community's town
# association counts ("SERENBE TOWN ASSOCIATION", "SERENBETOWN ASSOCIATION"),
# but not "... TOWN ASSOC", usually ASSOCIATES ("ACKERMAN MIDTOWN ASSOC LTD").
_ASSOCIATION_RE = re.compile(
    r"\b(HOME ?OWNER(S|'S|S'|S'S| S)?|PROPERTY OWNERS?'?|COMMUNITY|NEIGHBORHOOD|NBHD|"
    r"CONDOMINIUMS?|CONDOS?|CIVIC|TOWN ?HOMES?|TOWN ?HOUSES?|HOMES|"
    r"[A-Z]*OWNER(S|'S|S'|S'S| S)|[A-Z]*OWNRS|[A-Z]*HOME?OWERS) "
    r"ASS(OCIATION|OCATION|OCIAT|N|OC|O|C)?\b|\bHOME ?OWNERS( INC| THE)*$|"
    r"TOWN ASS(OCIATION|OCATION|OCIAT|N)\b"
)
# A partnership's legal form wins over any association word: "<builder>
# HOMES ASSOC L P" and "LENOX TOWERS ASSOC LTD" are associates.
_PARTNERSHIP_RE = re.compile(r"\b(L ?L ?L ?P|L ?L ?P|L ?P|LTD)\b")
# HOA is also a given name ("NGUYEN THI HOA"): it counts only after the
# first word of a company's name ("HIGH GROVE HOA INC"). POA alone usually
# means power of attorney, so it never counts.
_HOA_RE = re.compile(r"\S \bHOA\b")
_HOA_COMPANY_RE = re.compile(r"\b(INC|LLC|L L C|CORP|CORPORATION|THE)\b")
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
# A CONFERENCE ASSOCIATION is a church conference's legal body ("SOUTH
# ATLANTIC CONFERENCE ASSOC"); CONFERENCE alone is not a church word.
_RELIGIOUS_RE = re.compile(
    r"\b(CHURCH|MINISTRY|MINISTRIES|TEMPLE|MOSQUE|BAPTIST|METHODIST|DIOCESE|CONGREGATION|"
    r"CEMETERY|TABERNACLE|ADVENTISTS?|CONFERENCE ASS(OCIATION|OCIAT|OC|O|N))\b"
)
# ADMINISTRATOR / ADMINISTRATRIX, not ADMINISTRATION (agencies are government).
_ESTATE_RE = re.compile(r"(?<!REAL )\bESTATE\b|\bEST OF\b|\bHEIRS\b|\bDECD\b|\bDECEASED\b|\bEXECUTOR\b|\bEXECUTRIX\b|\bADMINISTRAT(?!I)")
_TRUST_RE = re.compile(r"\bTRUST\b|\bTRUSTEE\b|\bTRUSTEES\b|\bTR\b|\bTRS\b|\bREVOCABLE\b")
# ASSOC is ASSOCIATES or ASSOCIATION, a company either way ("FOUR FIFTY
# PIEDMONT ASSOC", "CHESTER INDUSTRIAL PARK ASSOC L P").
_ENTITY_RE = re.compile(
    r"\bLLC\b|\bL L C\b|\bINC\b|\bCORP\b|\bCORPORATION\b|\bLP\b|\bLLP\b|\bLTD\b|\bCO\b|"
    r"\bCOMPANY\b|\bPARTNERS\b|\bPARTNERSHIP\b|\bHOLDINGS\b|\bPROPERTIES\b|\bINVESTMENTS?\b|"
    r"\bGROUP\b|\bASSOCIATES\b|\bVENTURES?\b|\bFUND\b|\bREALTY\b|\bENTERPRISES\b|\bASSN\b|"
    r"\bASSOC\b|\bASSOCIATION\b|\bRETIREMENT\b|\bPENSION\b|\bSYSTEM\b"
)
# A private-entity word overrides government keywords ("CITY OF ATLANTA
# PARTNERS LP", "PENNSYLVANIA PUBLIC SCHOOL EMPLOYEES RETIREMENT SYSTEM").
_PRIVATE_SUFFIX_RE = re.compile(
    r"\bLLC\b|\bL L C\b|\bINC\b|\bCORP\b|\bLP\b|\bLLP\b|\bLTD\b|\bPARTNERS\b|"
    r"\bPARTNERSHIP\b|\bHOLDINGS\b|\bPROPERTIES\b|\bINVESTMENTS?\b|\bRETIREMENT\b|\bFUND\b")
# Legal forms _ENTITY_RE doesn't spell out.
_SPACED_FORM_RE = re.compile(r"\bL P\b|\bLLLP\b|\bL L L P\b")
_ZIP_TAIL_RE = re.compile(r"\b([A-Z]{2})\s+(\d{5})(-?\d{4})?\s*$")
_PO_BOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\b|\bPOB\b|\bPOST OFFICE BOX\b")


def normalize(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").upper().replace(",", " ").replace(".", " ")).strip()


def owner_type(name: str | None) -> str:
    """One of: development_authority, government, utility, association,
    religious, developer, estate, trust, entity, individual, unknown."""
    n = normalize(name)
    if not n:
        return "unknown"
    if _DEV_AUTHORITY_RE.search(n):
        return "development_authority"
    if (_DEVELOPER_RE.search(n)
            or (_DEVELOPER_SURNAME_RE.search(n) and _company_name(n))
            or (_DEVELOPER_WEAK_RE.search(n) and _REAL_ESTATE_WORD_RE.search(n)
                and not _RELIGIOUS_RE.search(n))):
        return "developer"
    if _UTILITY_RE.search(n):
        return "utility"
    if _GOVERNMENT_RE.search(n) and not _PRIVATE_SUFFIX_RE.search(n):
        return "government"
    if ((_ASSOCIATION_RE.search(n) or (_HOA_RE.search(n) and _HOA_COMPANY_RE.search(n)))
            and not _PARTNERSHIP_RE.search(n)):
        return "association"
    if _is_religious(n):
        return "religious"
    if _ESTATE_RE.search(n):
        return "estate"
    if _TRUST_RE.search(n):
        return "trust"
    if _ENTITY_RE.search(n):
        return "entity"
    return "individual"


def _company_name(n: str) -> bool:
    """A legal form, an entity word or a real-estate word is in the name."""
    return bool(_ENTITY_RE.search(n) or _SPACED_FORM_RE.search(n) or _REAL_ESTATE_WORD_RE.search(n))


def _is_religious(n: str) -> bool:
    """A religious word that isn't just a person's surname ("TEMPLE GRAY JR",
    "CHURCH JOHN"): it must not be the first word of a short name."""
    m = _RELIGIOUS_RE.search(n)
    if not m or re.search(r"\bLLC\b|\bL L C\b|\bLP\b", n):
        return False
    words = n.split()
    return not (m.start() == 0 and len(words) <= 3)


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


# The county deed file cuts grantor names at 30 characters (29 once a
# trailing space is stripped).
DEED_NAME_CAP = 29
# Words that don't tell two owners apart: legal forms, trust and estate roles.
_ROLE_WORD_RE = re.compile(
    r"\b(L ?L ?L ?P|L ?L ?P|L ?L ?C|P ?L ?L ?C|L ?P|LIMITED PARTNER\w*|INC|INCORPORATED|CORP|"
    r"CORPORATION|CO|COMPANY|LTD|LIMITED|THE|AND|TRUSTS?|TRUSTEES?|TR|TRS|TTEES?|REVOCABLE|"
    r"REVOC|IRREVOCABLE|LIVING|LIFE ESTATE|ESTATE|EST|HEIRS|DECD|DECEASED|EXEC\w*|EXTRX?|EXR|"
    r"ADMINISTRAT\w*|ADMR|ADMX)\b")
# "..., LLC, A GEORGIA LIMITED LIABILITY COMPANY": where it was organised.
_ORGANIZED_IN_RE = re.compile(r"\b(LLC|L L C|INC|CORP|LP|L P|LLLP|LTD|CO)\s*,\s*AN?\b.*$")
# Tails that add people or aliases: "ET AL", "A/K/A ...", "C/O ...".
_ALIAS_TAIL_RE = re.compile(r"\s(ET|ETAL|ETUX|A/?K/?A|F/?K/?A|N/?K/?A|C/O|ATTN)\b.*$")
# "X LLC & Y LLC": each company is one of the owners.
_CO_COMPANY_RE = re.compile(r"\b(LLC|L L C|INC|CORP|LP|L P|LLLP|LLP|LTD|CO) (&|AND) ")
_GENERATIONS = {"JR", "SR", "II", "III", "IV"}
_SMALL_WORDS = {"OF", "AT", "ON", "IN", "FOR", "AS", "BY", "A", "AN"}
_NUMBER_RE = re.compile(r"^(\d+|[IVX]+)$")


def _letters(name: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def _co_owners(name: str | None) -> list[str]:
    n = _ORGANIZED_IN_RE.sub(r"\1", (name or "").upper().replace(".", " "))
    return _CO_COMPANY_RE.sub(r"\1|", normalize(n)).split("|")


def _owner_words(name: str) -> list[str]:
    n = re.sub(r"[^A-Z0-9/& ]+", " ", name.replace("'", ""))
    n = _ALIAS_TAIL_RE.sub("", " " + re.sub(r"\s+", " ", n).strip())
    n = re.sub(r"\bMC (?=[A-Z])", "MC", n.replace("&", " ").replace("/", " "))
    return _ROLE_WORD_RE.sub(" ", n).split()


def same_owner(a: str | None, b: str | None) -> bool:
    """Whether a deed grantor and a tax-roll owner are the same owner.

    The two files spell names differently, the deed file cuts names at 30
    characters, and co-owners, trusts and typos come and go. Names are
    compared as words, leaving out legal forms ("LLC", "INC", "THE"), trust
    and estate roles ("TRUST", "REVOCABLE", "ESTATE", "EXECUTOR"), "&" /
    "AND", and tails like "ET AL" or "A/K/A ...". They match when:

    1. the letters are the same ("SABEN L L C" / "SABEN, LLC", "TL COOK" /
       "T L COOK");
    2. one is cut at the deed file's limit and the other carries on from it;
    3. they are the same words in another order ("HARVISCHAK DENNIS" /
       "DENNIS HARVISCHAK REVOCABLE TRUST");
    4. two or more words agree and the rest differ on one side only: by
       initials ("SMITH JOHN" / "SMITH JOHN W"), or, when both are people,
       trusts or estates, by any words (a co-owner, a middle name, a trust's
       name). An initial or a cut-short word stands for the whole word
       ("W" / "WILLIAM"); clashing initials, or JR against SR, don't match;
    5. the spelling is nearly the same ("SHROPSHINE" / "SHROPSHIRE") with
       the same numbers.

    So companies that only share words are different owners ("SOUTH FULTON
    LAND PARTNERS LLC" / "SOUTH FULTON LAND CO", "OAK RIDGE LLC" / "OAK
    RIDGE HOMEOWNERS ASSOCIATION INC"). "X LLC & Y LLC" matches X or Y.
    """
    for x, y in ((a, b), (b, a)):
        if len((x or "").strip()) >= DEED_NAME_CAP and _letters(y).startswith(_letters(x)):
            return True
    return any(_same_name(x, y) for x in _co_owners(a) for y in _co_owners(b))


def _same_name(a: str, b: str) -> bool:
    wa, wb = _owner_words(a), _owner_words(b)
    if not wa or not wb:
        return False
    if "".join(wa) == "".join(wb) or sorted(wa) == sorted(wb):
        return True
    people = {owner_type(a), owner_type(b)} <= {"individual", "trust", "estate"}
    return _words_agree(wa, wb, people) or _near_spelling(wa, wb)


def _words_agree(wa: list[str], wb: list[str], people: bool) -> bool:
    ca, cb = Counter(wa), Counter(wb)
    shared = ca & cb
    if sum(1 for w in shared.elements() if len(w) > 1 and w not in _SMALL_WORDS) < 2:
        return False
    ra, rb = list((ca - shared).elements()), list((cb - shared).elements())
    # An initial or a cut-short word stands for the whole word ("W" /
    # "WILLIAM"), but a number never does ("FUND I" / "FUND II").
    for x, y in ((ra, rb), (rb, ra)):
        for w in sorted(x, key=len):
            whole = next((v for v in y if w.isalpha() and len(v) > len(w) and v.startswith(w)
                          and not (_NUMBER_RE.match(w) and _NUMBER_RE.match(v))), None)
            if whole is not None:
                x.remove(w)
                y.remove(whole)
    if (_GENERATIONS & set(ra) and _GENERATIONS & set(rb)) or (
            any(len(w) == 1 for w in ra) and any(len(w) == 1 for w in rb)):
        return False                                    # JR / SR, or clashing initials
    ra = [w for w in ra if not _ignorable(w, people)]
    rb = [w for w in rb if not _ignorable(w, people)]
    if ra and rb:
        return False
    return people or not (ra or rb)


def _ignorable(word: str, people: bool) -> bool:
    if word in _SMALL_WORDS:
        return True
    if people:
        return (len(word) == 1 and word.isalpha()) or word in _GENERATIONS
    return len(word) == 1 and word.isalpha() and word not in "IVX"     # not a fund number


def _near_spelling(wa: list[str], wb: list[str]) -> bool:
    sa, sb = "".join(wa), "".join(wb)
    # A typo, not another initial, generation or number ("... JOHN R" /
    # "... JOHN W", "... JR" / "... SR", "FUND I" / "FUND II").
    marks = lambda ws: [w for w in ws if len(w) == 1 or w in _GENERATIONS or _NUMBER_RE.match(w)]
    return (len(wa) == len(wb) and abs(len(sa) - len(sb)) <= 1 and marks(wa) == marks(wb)
            and SequenceMatcher(None, sa, sb).ratio() >= 0.9)
