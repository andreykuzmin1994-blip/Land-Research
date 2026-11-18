#!/usr/bin/env python3
"""changes.md: what changed between two screener runs.

    python ops/screener/changes.py RUN_DIR
    python ops/screener/changes.py RUN_DIR --previous OLDER_RUN_DIR --leads screener/leads

Compares a finished run folder (``ranked.csv``, ``rejected.csv``,
``summary.md``, ``features.json``) with the run before it and writes
``RUN_DIR/changes.md``: the grade A and B leads that are new or gone, the
parcels that entered, left or moved in the top of the ranking, parcels
with a new deed, what happened to the parcels you marked, and the checks
this run could not make. With ``--leads`` each lead also shows its
document check (the skeptic's verdict in ``findings.checked.json``).

Without ``--previous`` the run before is the newest earlier run folder of
the same county beside RUN_DIR. A trial run (``--limit``) is never taken
as the run before a full run.

It prints a short summary of counts on stdout. That summary carries no
owner name, mailing address or site address, so it can go in an alert.
``changes.md`` itself carries no owner name or mailing address either:
the columns that hold them are never copied, and because other columns
can repeat a name (a listed contamination site is often named after the
company that owns the parcel), the finished text is searched for every
owner name and mailing address of both runs and each one found is
replaced by "(name withheld)". A misspelt name or a nickname is not
caught.

Reads files only: no network, no screener import, nothing written but the
one output file. It changes no score, grade, rank, screen or decision.
Exit codes: 0 written; 2 refused (the message says why), nothing written.

Kept outside ``screener/`` on purpose, like the rest of ``ops/screener``:
changing it does not reset the screener's AutoResearch baseline.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

FILE_NAME = "changes.md"
TOP_N = 50
MOVED_BY = 10
LIST_CAP = 40                 # rows per table; the rest are counted
ITEM_CAP = 160                # characters of a lead's open items shown in a cell
GRADES = ("A", "B")           # the grades leads.md lists
MARKS = ("pursue", "maybe")   # decisions that keep a parcel in the list
SKEPTIC_VERDICTS = ("survives", "weakened", "killed")
SITE_NAME = re.compile(r"(contamination site(?: [\d,.]+ ft away)?) \(.*?\)(?=; |$)")
WITHHELD = "(name withheld)"
OWNER_MIN_CHARS = 7           # shorter owner strings are too common to search for
COMPANY_WORDS = frozenset({"LLC", "INC", "LP", "LLLP", "LLP", "CORP", "CORPORATION", "CO", "COMPANY",
                           "LTD", "TRUST", "THE", "ET", "AL"})
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DATE_IN_NAME = re.compile(r"-\d{4}-\d{2}-\d{2}")
# Lines of summary.md that say a check failed (the screener's own words;
# the same ones supervise.sh looks for). A check held to the top N by its
# cap is not a failure and is not listed.
NOT_RUN = re.compile(r"FAILURES|FAILED|[Rr]e-run")

_MARKUP = str.maketrans({"|": "/", "[": "(", "]": ")", "<": "(", ">": ")", "`": "'",
                         "*": "", "_": " ", "\\": "/"})


class Refuse(Exception):
    """Nothing written; the message says why."""


def cell(text: Any) -> str:
    """One markdown table cell of county text: no pipes or line breaks and
    no link, HTML, code or emphasis markup (as screener/digest.py), and no
    bare address a renderer would turn into a link."""
    out = re.sub(r"\s+", " ", str(text if text is not None else "").translate(_MARKUP)).strip()
    return re.sub(r"(?i)\bwww\.", "www ", out.replace("://", ": "))


class Raw(str):
    """A table cell that is already safe markdown (a code span)."""


PLAIN_ID = re.compile(r"[A-Za-z0-9 ._-]+")


def pid_cell(pid: str) -> str:
    """A parcel id as a code span, so its spaces survive: ids are copied
    from here into decisions.csv and must match exactly. An id that cannot
    sit in a code span is shown cleaned and marked as not exact."""
    if PLAIN_ID.fullmatch(pid) or not re.search(r"[`|\r\n]", pid):
        return Raw(f"`{pid}`")
    return Raw(cell(pid) + " (id not exact: see ranked.csv)")


def read_rows(path: Path) -> dict[str, dict[str, str]]:
    """A run's CSV by parcel id (stripped). The first row for an id wins."""
    out: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return out
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            pid = (row.get("parcel_id") or "").strip()
            if pid and pid not in out:
                out[pid] = row
    return out


def meta_of(run_dir: Path) -> dict[str, Any]:
    """The run's own description (features.json meta), or {} when the
    folder has none or it cannot be read."""
    try:
        meta = json.loads((run_dir / "features.json").read_text(encoding="utf-8"))["meta"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def run_time(run_dir: Path, meta: dict[str, Any]) -> float:
    """When the run was made: its recorded time, else ranked.csv's."""
    stamp = meta.get("run_time")
    if isinstance(stamp, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(stamp, fmt).timestamp()
            except ValueError:
                continue
    return (run_dir / "ranked.csv").stat().st_mtime


def county_of(run_dir: Path, meta: dict[str, Any]) -> str:
    """The county a run folder is for: its meta, else the folder name up to
    the date (``fulton-2026-11-13`` -> ``fulton``)."""
    county = meta.get("county")
    if isinstance(county, str) and county.strip():
        return county.strip().lower()
    return DATE_IN_NAME.split(run_dir.name, maxsplit=1)[0].lower()


def find_previous(run_dir: Path) -> Path | None:
    """The newest earlier run of the same county beside run_dir."""
    meta = meta_of(run_dir)
    county, when, trial = county_of(run_dir, meta), run_time(run_dir, meta), meta.get("limit")
    best: tuple[float, str, Path] | None = None
    here = run_dir.resolve()
    for d in sorted(run_dir.resolve().parent.iterdir()):
        if d == here or not d.is_dir() or not (d / "ranked.csv").is_file():
            continue
        m = meta_of(d)
        if county_of(d, m) != county:
            continue
        if m.get("limit") is not None and trial is None:
            continue                      # a trial run is not the run before a full one
        try:
            if not read_rows(d / "ranked.csv"):
                continue                  # a run that ranked nothing compares with nothing
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
        t = run_time(d, m)
        if t < when and (best is None or (t, d.name) > best[:2]):
            best = (t, d.name, d)
    return best[2] if best else None


def rank_of(row: dict[str, str] | None) -> int | None:
    try:
        return int(float((row or {}).get("rank") or ""))
    except (ValueError, OverflowError):
        return None


def grade_of(row: dict[str, str] | None) -> str:
    """A, B, C, blocked, or 'not graded' (the reason is dropped)."""
    g = ((row or {}).get("lead_grade") or "").strip()
    return "not graded" if g.startswith("not graded") or not g else g


def where(row: dict[str, str]) -> str:
    place = (row.get("site_address") or "").strip() or (row.get("parcel_id") or "").strip()
    town = (row.get("jurisdiction") or "").strip()
    return f"{place} ({town})" if town else place


def open_items(row: dict[str, str], limit: int = ITEM_CAP) -> str:
    """What the grade found or could not check, cut to limit characters
    (the full text is in ranked.csv and the brief)."""
    # The grade names a listed contamination site, which is often the
    # company that owns the parcel: the name stays in the brief.
    found = SITE_NAME.sub(r"\1 (named in the brief)", (row.get("lead_found") or "").strip())
    unchecked = (row.get("lead_not_checked") or "").strip()
    parts = ([f"found: {found}"] if found else []) + ([f"not checked: {unchecked}"] if unchecked else [])
    text = "; ".join(parts)
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def lead_verdict(pid: str, leads: Path | None) -> str:
    """The skeptic's verdict for a parcel's lead folder: 'killed',
    'weakened', 'survives', 'checked, no skeptic', or '' when the lead has
    not been checked. The folder must hold this parcel."""
    slug = pid.replace(" ", "_")
    if leads is None or not SLUG_RE.match(slug):
        return ""
    base = leads.resolve()
    folder = (base / slug).resolve()
    if folder.parent != base:
        return ""
    try:
        doc = json.loads((folder / "findings.checked.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(doc, dict) or doc.get("parcel_id") != pid:
        return ""
    skeptic = doc.get("skeptic")
    verdict = skeptic.get("verdict") if isinstance(skeptic, dict) else None
    return verdict if verdict in SKEPTIC_VERDICTS else "checked, no skeptic"


def table(head: list[str], rows: list[list[Any]], cap: int = LIST_CAP) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(c if isinstance(c, Raw) else cell(c) for c in r) + " |"
              for r in rows[:cap]]
    if len(rows) > cap:
        lines += ["", f"And {len(rows) - cap:,} more."]
    return lines


def why_gone(pid: str, new: dict[str, dict[str, str]], rejected: dict[str, dict[str, str]]) -> str:
    """What became of a parcel that is no longer a lead or in the top."""
    if pid in new:
        row = new[pid]
        items = open_items(row)
        return f"rank {row.get('rank')}, grade {grade_of(row)}" + (f" ({items})" if items else "")
    if pid in rejected:
        return "screened out: " + ((rejected[pid].get("reasons") or "").strip() or "no reason given")
    return "not in this run (outside the acreage band or the chosen cities, or gone from the county parcel map)"


def compare(run_dir: Path, prev_dir: Path | None, leads: Path | None, top: int = TOP_N) -> tuple[str, str]:
    """(changes.md text, the alert summary)."""
    new = read_rows(run_dir / "ranked.csv")
    if not new:
        raise Refuse(f"{run_dir} has no ranked.csv, or it lists no parcel")
    rejected = read_rows(run_dir / "rejected.csv")
    meta = meta_of(run_dir)
    county = str(meta.get("county") or county_of(run_dir, meta)).strip() or "This"
    county = county[:1].upper() + county[1:]

    def verdict_cell(pid: str) -> str:
        return lead_verdict(pid, leads) or ("not checked yet" if leads is not None else "")

    lead_head = ["#", "Score", "Where", "Parcel", "Acres", "Zoning (path)", "Building that fits",
                 "Open item", "Document check"]

    def lead_row(pid: str, row: dict[str, str]) -> list[Any]:
        sf = (row.get("envelope_building_sf") or "").strip()
        fit = (row.get("envelope_fit") or "").strip() + (f" ({float(sf):,.0f} SF)" if _is_number(sf) else "")
        return [row.get("rank"), row.get("score"), where(row), pid_cell(pid), row.get("acres"),
                f"{row.get('zoning') or 'unknown'} ({row.get('entitlement') or 'unknown'})",
                fit, open_items(row), verdict_cell(pid)]

    by_grade = {g: [p for p, r in new.items() if grade_of(r) == g] for g in GRADES}
    checked = {v: 0 for v in (*SKEPTIC_VERDICTS, "checked, no skeptic")}
    if leads is not None:
        for g in GRADES:
            for pid in by_grade[g]:
                v = lead_verdict(pid, leads)
                if v:
                    checked[v] += 1
    not_run = _not_run_lines(run_dir / "summary.md")
    title = f"# {cell(county)} County: what changed"
    lines: list[str]

    if prev_dir is None:
        lines = [title + f" — {cell(run_dir.name)}", "",
                 "There is no earlier run of this county beside this one, so nothing is compared. "
                 "The leads as they stand:", ""]
        if leads is not None:
            lines += [_checked_line(checked, sum(len(by_grade[g]) for g in GRADES)), ""]
        for g in GRADES:
            lines += [f"## Grade {g} ({len(by_grade[g]):,})", ""]
            lines += (table(lead_head, [lead_row(p, new[p]) for p in by_grade[g]])
                      if by_grade[g] else ["None."])
            lines.append("")
        lines += _not_run_section(not_run)
        summary = (f"{run_dir.name}: first run, nothing to compare. Grade A {len(by_grade['A']):,}, "
                   f"grade B {len(by_grade['B']):,}, ranked {len(new):,}." + _checked_summary(checked, leads))
        return "\n".join(lines).rstrip() + "\n", summary

    old = read_rows(prev_dir / "ranked.csv")
    if not old:
        raise Refuse(f"{prev_dir} has no ranked.csv, or it lists no parcel")
    old_rejected = read_rows(prev_dir / "rejected.csv")
    old_meta = meta_of(prev_dir)
    lines = [title + f" since {cell(prev_dir.name)}", "",
             f"This run: `{cell(run_dir.name)}`. Compared with: `{cell(prev_dir.name)}`.", ""]
    cautions = _cautions(meta, old_meta)
    lines += [f"- {c}" for c in cautions] + ([""] if cautions else [])

    new_leads = {g: [p for p in by_grade[g] if grade_of(old.get(p)) != g] for g in GRADES}
    gone_leads = [p for p, r in old.items()
                  if grade_of(r) in GRADES and grade_of(new.get(p)) not in GRADES]
    top_new = {p for p, r in new.items() if (rank_of(r) or top + 1) <= top}
    top_old = {p for p, r in old.items() if (rank_of(r) or top + 1) <= top}
    entered = sorted(top_new - top_old, key=lambda p: rank_of(new[p]) or 0)
    left = sorted(top_old - top_new, key=lambda p: rank_of(old[p]) or 0)
    moved = sorted((p for p in top_new & top_old
                    if rank_of(old[p]) is not None and rank_of(new[p]) is not None
                    and abs(rank_of(old[p]) - rank_of(new[p])) >= MOVED_BY),
                   key=lambda p: rank_of(new[p]) or 0)
    deeds = sorted((p for p in set(new) & set(old)
                    if (new[p].get("last_sale_date") or "").strip()
                    and (new[p].get("last_sale_date") or "").strip()
                    != (old[p].get("last_sale_date") or "").strip()),
                   key=lambda p: rank_of(new[p]) or 0)
    owners = sorted((p for p in set(new) & set(old)
                     if "owner" in new[p] and "owner" in old[p]
                     and _squash(new[p]["owner"]) != _squash(old[p]["owner"])),
                    key=lambda p: rank_of(new[p]) or 0)
    marked = _marked(new, old, rejected, old_rejected)

    lines += ["## In short", ""]
    for g in GRADES:
        was = sum(1 for r in old.values() if grade_of(r) == g)
        lines.append(f"- Grade {g}: {len(by_grade[g]):,} (was {was:,}); {len(new_leads[g]):,} new.")
    lines += [f"- {len(gone_leads):,} parcels that were grade A or B are no longer.",
              f"- Ranked parcels: {len(new):,} (was {len(old):,}); {len(set(new) - set(old)):,} new, "
              f"{len(set(old) - set(new)):,} gone.",
              f"- Top {top}: {len(entered):,} new, {len(left):,} left, {len(moved):,} moved "
              f"{MOVED_BY} or more places.",
              f"- Ranked parcels with a new deed: {len(deeds):,}; with a new owner on the tax "
              f"roll: {len(owners):,}."]
    if marked:
        lines.append(f"- {len(marked):,} of the parcels you marked pursue or maybe changed.")
    if leads is not None:
        lines.append("- " + _checked_line(checked, sum(len(by_grade[g]) for g in GRADES)))
    if not_run:
        lines.append(f"- {len(not_run):,} notes in summary.md about a check that failed or did not run (below).")
    lines.append("")

    for g in GRADES:
        lines += [f"## New grade {g} leads ({len(new_leads[g]):,})", ""]
        if new_leads[g]:
            head = lead_head + ["Before"]
            rows = [lead_row(p, new[p]) + [_before(p, old, old_rejected)] for p in new_leads[g]]
            lines += table(head, rows)
        else:
            lines.append("None.")
        lines.append("")

    lines += [f"## No longer grade A or B ({len(gone_leads):,})", ""]
    if gone_leads:
        gone_leads.sort(key=lambda p: (grade_of(old[p]), rank_of(old[p]) or 0))
        lines += table(["Where", "Parcel", "Was", "Now", "Document check"],
                       [[where(old[p]), pid_cell(p), f"rank {old[p].get('rank')}, grade {grade_of(old[p])}",
                         why_gone(p, new, rejected), verdict_cell(p)] for p in gone_leads])
    else:
        lines.append("None.")
    lines.append("")

    lines += [f"## New in the top {top} ({len(entered):,})", ""]
    if entered:
        lines += table(["#", "Score", "Where", "Parcel", "Acres", "Zoning (path)", "Grade", "Before"],
                       [[new[p].get("rank"), new[p].get("score"), where(new[p]), pid_cell(p), new[p].get("acres"),
                         f"{new[p].get('zoning') or 'unknown'} ({new[p].get('entitlement') or 'unknown'})",
                         grade_of(new[p]), _before(p, old, old_rejected)] for p in entered])
    else:
        lines.append("None.")
    lines.append("")

    lines += [f"## Left the top {top} ({len(left):,})", ""]
    if left:
        lines += table(["Was #", "Where", "Parcel", "Now"],
                       [[old[p].get("rank"), where(old[p]), pid_cell(p), why_gone(p, new, rejected)]
                        for p in left])
    else:
        lines.append("None.")
    lines.append("")

    lines += [f"## Moved {MOVED_BY} or more places in the top {top} ({len(moved):,})", ""]
    if moved:
        lines += table(["#", "Was #", "Where", "Parcel", "Score", "Was"],
                       [[new[p].get("rank"), old[p].get("rank"), where(new[p]), pid_cell(p),
                         new[p].get("score"), old[p].get("score")] for p in moved])
    else:
        lines.append("None.")
    lines.append("")

    both = sorted(set(deeds) | set(owners), key=lambda p: rank_of(new[p]) or 0)
    lines += [f"## New deed or new owner on the tax roll ({len(both):,})", ""]
    if both:
        lines += ["Any priced or unpriced deed counts, a transfer between related companies "
                  "included. Owner names are in `ranked.csv`, not here.", ""]
        lines += table(["#", "Where", "Parcel", "Grade", "Last deed", "Was", "Owner on the roll"],
                       [[new[p].get("rank"), where(new[p]), pid_cell(p), grade_of(new[p]),
                         _deed(new[p]), _deed(old[p]), "changed" if p in owners else "same"]
                        for p in both])
    else:
        lines.append("None.")
    lines.append("")

    lines += [f"## Parcels you marked ({len(marked):,} changed)", ""]
    if marked:
        lines += table(["Mark", "Where", "Parcel", "Was", "Now"], marked)
    else:
        lines.append("No parcel marked pursue or maybe changed grade, moved "
                     f"{MOVED_BY} or more places or dropped out.")
    lines.append("")
    lines += _not_run_section(not_run)
    lines += ["## How to read this", "",
              "- A parcel can change because the public data changed (a new deed, a new appraisal, "
              "a lidar answer that differs) or because the code or settings changed. The notes at "
              "the top say when the code or settings differ between the two runs.",
              "- \"Document check\" is the skeptic's verdict from the lead's own folder: killed, "
              "weakened or survives. It is an agent's reading of public documents, backed by "
              "quotes the tool re-fetched; read `lead.md` before acting on it.",
              "- Nothing here changes a score, a grade, a rank or a decision."]

    summary = (f"{run_dir.name} vs {prev_dir.name}: "
               + "; ".join(f"grade {g} {len(by_grade[g]):,} ({len(new_leads[g]):,} new)" for g in GRADES)
               + f"; {len(gone_leads):,} no longer A or B; top {top}: {len(entered):,} new, "
               f"{len(left):,} left; {len(both):,} with a new deed or owner"
               + (f"; {len(marked):,} marked parcels changed" if marked else "")
               + (f"; {len(not_run):,} check notes" if not_run else "") + "."
               + _checked_summary(checked, leads))
    return "\n".join(lines).rstrip() + "\n", summary


def _is_number(s: str) -> bool:
    try:
        return float(s) > 0
    except ValueError:
        return False


def _squash(s: str | None) -> str:
    return " ".join((s or "").upper().split())


def _deed(row: dict[str, str]) -> str:
    day = (row.get("last_sale_date") or "").strip() or "none on file"
    price = (row.get("last_sale_price") or "").strip()
    return f"{day}, ${float(price):,.0f}" if _is_number(price) else day


def _before(pid: str, old: dict[str, dict[str, str]], old_rejected: dict[str, dict[str, str]]) -> str:
    if pid in old:
        return f"rank {old[pid].get('rank')}, grade {grade_of(old[pid])}"
    was = old_rejected.get(pid)
    if was:
        return "screened out: " + ((was.get("reasons") or "").strip() or "no reason given")
    return "not in that run"


def _marked(new: dict[str, dict[str, str]], old: dict[str, dict[str, str]],
            rejected: dict[str, dict[str, str]], old_rejected: dict[str, dict[str, str]]) -> list[list[Any]]:
    """Parcels marked pursue or maybe (in either run) that dropped out,
    changed grade or moved MOVED_BY places or more."""
    out: list[list[Any]] = []
    marks: dict[str, str] = {}
    for i, rows in enumerate((old_rejected, old, rejected, new)):   # the newest mark wins
        for pid, row in rows.items():
            mark = (row.get("decision") or "").strip().lower()
            if mark in MARKS:
                marks[pid] = mark
            elif mark and i >= 2:
                marks.pop(pid, None)                       # passed or listed since
    for pid, mark in sorted(marks.items()):
        was, now = old.get(pid), new.get(pid)
        if was is None:
            continue                                       # not ranked before: nothing to compare
        if now is None:
            out.append([mark, where(was), pid_cell(pid), f"rank {was.get('rank')}, grade {grade_of(was)}",
                        why_gone(pid, new, rejected)])
            continue
        a, b = rank_of(was), rank_of(now)
        if grade_of(was) != grade_of(now) or (a is not None and b is not None and abs(a - b) >= MOVED_BY):
            items = open_items(now)
            out.append([mark, where(now), pid_cell(pid), f"rank {was.get('rank')}, grade {grade_of(was)}",
                        f"rank {now.get('rank')}, grade {grade_of(now)}" + (f" ({items})" if items else "")])
    return out


def _cautions(meta: dict[str, Any], old_meta: dict[str, Any]) -> list[str]:
    out = []
    if meta.get("limit") is not None:
        out.append(f"This is a trial run (--limit {cell(meta.get('limit'))[:20]}): most differences "
                   "are the limit.")
    if old_meta.get("limit") is not None:
        out.append(f"The run compared with was a trial run (--limit {cell(old_meta.get('limit'))[:20]}).")
    for key, what in (("code_commit", "The code"), ("settings_sha256", "The settings file")):
        a, b = old_meta.get(key), meta.get(key)
        if a and b and a != b:
            out.append(f"{what} changed between the two runs ({cell(a)[:10]} to {cell(b)[:10]}), so a "
                       "difference below may come from that and not from new public data.")
    if not meta or not old_meta:
        out.append("One of the two runs has no readable features.json, so a change of code or "
                   "settings between them cannot be seen.")
    if meta.get("slope") != old_meta.get("slope") and meta and old_meta:
        out.append("Slope was checked in one of the two runs and not in the other.")
    return out


def _not_run_lines(summary: Path) -> list[str]:
    try:
        text = summary.read_text(encoding="utf-8")
    except OSError:
        return []
    return [cell(line.lstrip("- ").strip())[:600] for line in text.splitlines()
            if line.startswith("- ") and NOT_RUN.search(line)]


def _not_run_section(notes: list[str]) -> list[str]:
    lines = [f"## Checks that failed or did not run ({len(notes):,})", ""]
    lines += [f"- {n}" for n in notes] if notes else ["summary.md names none."]
    return lines + [""]


def _checked_line(checked: dict[str, int], graded: int) -> str:
    return ("Document checks of this run's grade A and B leads: "
            + ", ".join(f"{checked[v]:,} {v}" for v in SKEPTIC_VERDICTS)
            + (f", {checked['checked, no skeptic']:,} checked without a skeptic"
               if checked["checked, no skeptic"] else "")
            + f"; {graded - sum(checked.values()):,} not checked yet.")


def _checked_summary(checked: dict[str, int], leads: Path | None) -> str:
    if leads is None or not sum(checked.values()):
        return ""
    return (" Document checks of the A and B leads: "
            + ", ".join(f"{checked[v]} {v}" for v in SKEPTIC_VERDICTS) + ".")


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Z0-9]+", text.upper())


def owner_strings(row_sets: list[dict[str, dict[str, str]]], also_public: tuple[str, ...] = ()) -> list[list[str]]:
    """Every owner name and mailing address of the runs, as word lists: the
    full string, an owner name without its company words, and a mailing
    address's street line. Left out: strings under OWNER_MIN_CHARS, and a
    string that is part of a site address or a jurisdiction of the runs,
    or of also_public (the county's name): an owner-occupied site, a
    city's or the county's own land, public text the tables rightly show."""
    public: set[str] = {" ".join(_words(text)) for text in also_public}
    for rows in row_sets:
        for row in rows.values():
            for column in ("site_address", "jurisdiction"):
                public.add(" ".join(_words(row.get(column) or "")))
    # One string to search per owner form. " | " parts the entries, and no
    # owner word holds a "|", so a match never spans two of them.
    shown = " | " + " | ".join(sorted(public)) + " | "
    seen: set[tuple[str, ...]] = set()
    for rows in row_sets:
        for row in rows.values():
            owner = _words(row.get("owner") or "")
            mailing = row.get("owner_mailing") or ""
            forms = [owner, [w for w in owner if w not in COMPANY_WORDS],
                     _words(mailing), _words(mailing.split(",")[0])]
            for words in forms:
                flat = " ".join(words)
                if len(flat) < OWNER_MIN_CHARS or len(words) < 2 and len(flat) < 2 * OWNER_MIN_CHARS:
                    continue
                if f" {flat} " in shown:
                    continue
                seen.add(tuple(words))
    return [list(words) for words in sorted(seen, key=lambda w: -len(" ".join(w)))]


def withhold_owners(text: str, owners: list[list[str]]) -> tuple[str, int]:
    """Replace every owner string found in text, whole words, any case, any
    punctuation between the words. Returns (text, how many were replaced)."""
    flat = " " + " ".join(_words(text)) + " "
    count = 0
    for words in owners:
        if f" {' '.join(words)} " not in flat:
            continue                           # the cheap test; most names are nowhere in it
        pattern = re.compile(r"(?<![A-Za-z0-9])" + r"[^A-Za-z0-9\n|`]+".join(map(re.escape, words))
                             + r"(?![A-Za-z0-9])", re.I)
        text, n = pattern.subn(WITHHELD, text)
        count += n
    return text, count


def write(path: Path, text: str) -> None:
    """Atomically: a temp name in the same folder, then os.replace."""
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir", type=Path, help="the finished run folder")
    ap.add_argument("--previous", type=Path, help="the run to compare with (default: the newest "
                    "earlier run of the same county beside RUN_DIR)")
    ap.add_argument("--leads", type=Path, help="lead folder root (screener/leads): show each "
                    "lead's document check")
    ap.add_argument("--out", type=Path, help=f"the file to write (default: RUN_DIR/{FILE_NAME})")
    ap.add_argument("--top", type=int, default=TOP_N, help=f"how deep 'the top' is (default {TOP_N})")
    args = ap.parse_args(argv)
    try:
        run_dir: Path = args.run_dir
        if not (run_dir / "ranked.csv").is_file():
            raise Refuse(f"{run_dir} has no ranked.csv")
        if args.top < 1:
            raise Refuse("--top must be 1 or more")
        if args.previous is not None:
            if not (args.previous / "ranked.csv").is_file():
                raise Refuse(f"{args.previous} has no ranked.csv")
            if args.previous.resolve() == run_dir.resolve():
                raise Refuse("--previous is the run itself")
            prev = args.previous
        else:
            prev = find_previous(run_dir)
        if args.leads is not None and not args.leads.is_dir():
            raise Refuse(f"--leads {args.leads}: not a folder")
        text, summary = compare(run_dir, prev, args.leads, args.top)
        folders = [run_dir] + ([prev] if prev is not None else [])
        county = county_of(run_dir, meta_of(run_dir))
        owners = owner_strings([read_rows(d / name) for d in folders
                                for name in ("ranked.csv", "rejected.csv")],
                               (county, f"{county} county"))
        text, withheld = withhold_owners(text, owners)
        summary, _ = withhold_owners(summary, owners)
        if withheld:
            print(f"changes: {withheld} owner name(s) or address(es) withheld from the text",
                  file=sys.stderr)
        out = args.out if args.out is not None else run_dir / FILE_NAME
        write(out, text)
    except Refuse as exc:
        print(f"changes: refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        print(f"changes: refused: cannot read or write a file ({type(exc).__name__}: "
              f"{str(exc)[:200]})", file=sys.stderr)
        return 2
    print(summary)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
