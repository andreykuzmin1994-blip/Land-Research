"""leads.md: the short reading list of a run (reviews/35_screener_lead_layer).

One table row per graded lead, A first, then B, each in rank order, at
most ``[grade] digest_max`` rows; C, blocked and ungraded parcels are
counted, not listed. The caveats are given once, at the foot, and each
row points at its brief for the detail.

Reads the report fields on the ranked parcels and writes text. It puts
nothing on a parcel and changes no order.
"""

from __future__ import annotations

import re
from typing import Any

from . import grade

FILE_NAME = "leads.md"


_MARKUP = str.maketrans({"|": "/", "[": "(", "]": ")", "<": "(", ">": ")", "`": "'",
                         "*": "", "_": " ", "\\": "/"})


def _cell(text: Any) -> str:
    """One markdown table cell of county text: no pipes or line breaks (they
    would add a column or a row) and no link, HTML, code or emphasis markup."""
    return re.sub(r"\s+", " ", str(text if text is not None else "").translate(_MARKUP)).strip()


def _acres(p: dict[str, Any]) -> str:
    a = p.get("acres")
    return f"{a:.1f}" if isinstance(a, (int, float)) and not isinstance(a, bool) else ""


def _zoning(p: dict[str, Any]) -> str:
    return f"{p.get('zoning') or 'unknown'} ({p.get('entitlement') or 'unknown'})"


def _building(p: dict[str, Any]) -> str:
    fit = p.get("envelope_fit") or ""
    sf = p.get("envelope_building_sf")
    if isinstance(sf, (int, float)) and not isinstance(sf, bool) and sf:
        return f"{fit} ({sf:,.0f} SF)"
    return fit


def _frontage(p: dict[str, Any]) -> str:
    ft = p.get("frontage_ft")
    if isinstance(ft, (int, float)) and not isinstance(ft, bool) and ft:
        return f"{ft:,.0f} ft"
    return str(p.get("frontage_road") or "")


def _owner(p: dict[str, Any]) -> str:
    years = p.get("years_held")
    held = (f", held {years:.0f} yr" if isinstance(years, (int, float))
            and not isinstance(years, bool) else "")
    return f"{p.get('owner') or 'unknown'} ({p.get('owner_type') or 'unknown'}{held})"


def _open_item(p: dict[str, Any]) -> str:
    lead: grade.Lead | None = p.get(grade.DETAIL_KEY)
    if lead is None:
        return ""
    items = [f"found: {x}" for x in lead.found] + [f"not checked: {x}" for x in lead.not_checked]
    return "; ".join(items)


def _row(p: dict[str, Any], brief: str | None, with_item: bool) -> str:
    cells = [f"{p.get('rank')}", f"{p.get('score')}",
             f"{p.get('site_address') or p.get('parcel_id')} ({p.get('jurisdiction')})",
             _acres(p), _zoning(p), _building(p), _frontage(p), _owner(p)]
    if with_item:
        cells.append(_open_item(p))
    # The last cell is ours, not county text: a link to a file name the
    # report made safe, or the parcel id as plain text.
    last = f"[brief](briefs/{brief})" if brief else _cell(p.get("parcel_id"))
    return "| " + " | ".join([*(_cell(c) for c in cells), last]) + " |"


def select(ranked: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """The leads the digest lists: A in rank order, then B, at most limit."""
    def of(g: str) -> list[dict[str, Any]]:
        return [p for p in ranked
                if isinstance(p.get(grade.DETAIL_KEY), grade.Lead)
                and p[grade.DETAIL_KEY].grade == g]
    return (of(grade.A) + of(grade.B))[:max(0, limit)]


def leads_md(county: str, run_date: str, ranked: list[dict[str, Any]], limit: int,
             brief_names: dict[int, str]) -> str:
    """The digest text. brief_names maps id(parcel) to its brief file name."""
    leads = [p.get(grade.DETAIL_KEY) for p in ranked]
    counts = {g: sum(1 for x in leads if isinstance(x, grade.Lead) and x.grade == g)
              for g in (*grade.GRADES, grade.NOT_GRADED)}
    graded = sum(counts[g] for g in grade.GRADES)
    listed = select(ranked, limit)
    lines = [f"# {county} County leads — {run_date}", "",
             f"{graded:,} of {len(ranked):,} ranked parcels were graded: "
             + ", ".join(f"{g} {counts[g]:,}" for g in grade.GRADES)
             + (f"; {counts[grade.NOT_GRADED]:,} not graded (outside a check's top-N cap, or a "
                "check switched off: see summary.md)" if counts[grade.NOT_GRADED] else "") + ".",
             "", "The ranking is unchanged (`ranked.csv`); this list re-reads it by grade."]
    head = "| # | Score | Where | Acres | Zoning (path) | Building that fits | Road frontage | Owner |"
    rule = "|---|---|---|---|---|---|---|---|"
    for g, title, with_item in ((grade.A, "Grade A: every check clear", False),
                                (grade.B, "Grade B: one item open", True)):
        rows = [p for p in listed if p[grade.DETAIL_KEY].grade == g]
        lines += ["", f"## {title} ({len(rows)} listed of {counts[g]:,})", ""]
        if not rows:
            lines.append("None listed." if counts[g] else "None.")
            continue
        extra = " The open item |" if with_item else ""
        lines += [head + extra + " Detail |", rule + ("---|" if with_item else "") + "---|"]
        lines += [_row(p, brief_names.get(id(p)), with_item) for p in rows]
    lines += ["", "## How to read this", "",
              "- A: every check came back clear. The zoning path is by right or likely, a "
              "prototype building envelope fits on the open ground, a detention pond fits "
              "beside it, the parcel has public road frontage, the slope and soils readings "
              "came back and are under their limits, the parcel map agrees with the tax "
              "record, and none of these was found: the same owner built next door, a listed "
              "contamination site on the parcel, a priced deed in the last few years.",
              "- B: exactly one of those was found or could not be checked (a failed service "
              "counts as not checked, never as clear). C: two or more. Blocked: the zoning "
              "path is against industrial use.",
              f"- {grade.CAVEAT}.",
              "- Still not checked by anything here: utilities and their capacity, zoning "
              "conditions, easements other than conservation easements, curb cuts, whether "
              "the parcel is for sale, and who is behind a company owner. Each brief lists "
              "its own next steps."]
    return "\n".join(lines) + "\n"
