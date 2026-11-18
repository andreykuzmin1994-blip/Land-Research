#!/usr/bin/env python3
"""Why leads fail their document check, counted, as prompts for the screens.

    python ops/screener/lead_tally.py screener/leads
    python ops/screener/lead_tally.py screener/leads --run screener/output/fulton-2026-11-17

Reads every lead folder's ``findings.checked.json`` (the quote checker's
file: `screener/INVESTIGATE.md`) and writes ``_tally.md`` in the leads
folder: how many leads the skeptic killed, weakened or let stand; the
reasons, each with its leads; and, for a reason that keeps coming up, the
screen or grade setting to look at. With ``--run`` it also says which of
those leads the newest run still ranks grade A or B, so a reason whose
leads are already screened out reads as closed and one whose leads still
come through reads as open.

How a reason is found, and what that is worth:

- from the checked sections' verdicts (`market`, `owner`, `plans`,
  `zoning`): fixed values the quote checker kept only with a quote behind
  them;
- from the skeptic's objection: fixed word patterns over the agent's own
  words. This is a reading aid, not a measurement: a pattern can miss a
  reason or see one that is not there. Each lead is listed so the count
  can be checked against its ``lead.md``.

Nothing here changes a score, grade, rank, screen or decision, and no
screen is proposed by a model: the proposals are fixed text per reason.
``screens.py`` and ``settings.toml`` stay the operator's, and a change to
them needs a new evaluation baseline.

The file names companies and people where the skeptic's objection does,
like ``lead.md``: it stays in the leads folder. The one line printed on
stdout holds counts only. Offline, stdlib only, reads and writes files
only. Exit codes: 0 written; 2 refused (the message says why).

Kept outside ``screener/`` on purpose, like the rest of ``ops/screener``:
changing it does not reset the screener's AutoResearch baseline.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

FILE_NAME = "_tally.md"            # not a lead folder name: those start with a letter or digit
CHECKED_NAME = "findings.checked.json"
MAX_CHECKED_BYTES = 16_000_000
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
VERDICTS = ("killed", "weakened", "survives")
NO_SKEPTIC = "no skeptic"
GRADES = ("A", "B")
PROPOSE_MIN = 2                    # leads killed or weakened for a reason before a proposal is shown
EXCERPT_CHARS = 260

# code -> (label, section verdicts that say it, word patterns over the
# skeptic's objection that say it, what to look at).
REASONS: dict[str, dict[str, Any]] = {
    "built": {
        "label": "Not raw land: a building, a yard or an operating site is on it",
        "sections": {},
        # Said of the parcel itself. Bare words such as "facility",
        # "operating" or "improvements" are not here: on the 29 leads of
        # November 2026 they matched a neighbour's site or a building that
        # was only proposed.
        "words": [r"\b(?:not|neither) (?:raw|vacant)\b", r"\bnot land\b",
                  r"\b(?:it|this|parcel|site) is an? operating\b", r"\bsite of an? operating\b",
                  r"\bbuildings? stands?\b", r"\b(?:fuel|propane|truck) terminal\b", r"\btrailer yard\b",
                  r"\bpaved\b", r"\bmanufactured[- ]home\b", r"\bactive main addresses\b"],
        "look": "the built-site screen (`[screens] built_min_building_sf`, `built_min_improvement_value`, "
                "`max_improvement_value`) and `python -m screener.builtcheck` on the top parcels before "
                "they are read as leads",
    },
    "developer": {
        "label": "A developer or investor already holds it or is pursuing it",
        "sections": {"market": ("signs_developer_pursuing", "signs_under_contract"),
                     "owner": ("developer_or_investor",)},
        "words": [r"\bdeveloper\b", r"\bassemblage\b", r"\bproject\b.{0,30}\bllc\b", r"\bportfolio\b",
                  r"\bbought (?:it|the parcel)\b", r"\blimited partnership\b"],
        "look": "the developer-owner rules (`owners.py`, `[screens] exclude_developer_owned`), the "
                "`recent_purchase_years` item of the lead grade, and a look at the city's rezoning "
                "cases for the parcel before it is graded",
    },
    "operator": {
        "label": "Held by an operating company: buffer, expansion or railroad land",
        "sections": {"owner": ("operator",)},
        "words": [r"\bquarry\b", r"\btransfer station\b", r"\bexpansion\b", r"\bbuffer\b", r"\brailroad\b",
                  r"\bnorfolk southern\b", r"\breit\b", r"\bwaste company\b", r"\bfleet\b", r"\btravel cent",
                  r"\boperated by\b", r"\boperates the\b"],
        "look": "the operating-owner names of the lead grade (`[grade] operating_owner`), the "
                "same-owner-next-door check (`[grade] next_door_any_size`), and `[screens] "
                "exclude_utility_owned`",
    },
    "plan": {
        "label": "The adopted plan is against it, or the screener read a superseded land-use layer",
        "sections": {"plans": ("against",)},
        "words": [r"\bfuture land use\b", r"\bcomprehensive (?:development )?plan\b", r"\bdeprecated\b",
                  r"\bdevelopment[- ]pattern\b", r"\bfuture development map\b",
                  r"\btown cent(?:er|re)\b", r"\brural residential\b"],
        "look": "which future-land-use layer each city's source in the county module reads (a plan "
                "adopted since the layer was published is the usual cause), and `scoring.py`'s "
                "zoning path, which is yours to change",
    },
    "zoning": {
        "label": "Industrial use is not by right: a use permit, a rezoning or a past denial",
        "sections": {"zoning": ("conditional", "rezoning_needed", "blocked")},
        "words": [r"\buse[- ]permit\b", r"\bnot by right\b", r"\bdenial\b", r"\bdenied\b", r"\bvariance\b",
                  r"\bconditional\b", r"\bone mile\b", r"\bneeds? a rezoning\b",
                  r"\buse list bars\b"],
        "look": "the district code lists in `zoning.py` (a district read as by right that the code "
                "makes a use-permit use), and the draft `zoning_rules` of the planner",
    },
    "shape": {
        "label": "The shape or size cannot hold a building",
        "sections": {},
        # "strip" alone also names a wooded edge or a buffer between two sites.
        "words": [r"\b(?:curved|narrow|thin|long) strip\b", r"\bwedge\b", r"\bnarrow\b", r"\bcannot hold\b",
                  r"\bwidest\b", r"\bsliver\b"],
        "look": "the building-fit check (`[fit]`, `[grade]` counts \"no building envelope found\") and "
                "whether these parcels were outside its `max_parcels`",
    },
    "neighbour": {
        "label": "Neighbours or the road: a cemetery, homes on the lot line, a road that will not take trucks",
        "sections": {},
        "words": [r"\bcemetery\b", r"\bapartments?\b", r"\bresidential (?:property|infill|land)\b",
                  r"\bsingle[- ]family\b", r"\bsubdivision\b", r"\broundabout\b", r"\blot line\b"],
        "look": "the adjacency facts the screener does not have (it reads no neighbour's use); a "
                "question for the brief's next steps more than for a screen",
    },
}
SECTIONS = ("zoning", "plans", "utilities", "market", "owner")


class Refuse(Exception):
    """Exit 2: nothing written; the message says why."""


def plain(text: Any, limit: int) -> str:
    """An agent's words as inert text: letters, digits and plain
    punctuation on one line, nothing that reads as markup or an address."""
    out = re.sub(r"[\s_]+", " ", str(text if text is not None else ""))
    # The characters first: an address must not come together again out of
    # what the filter removed ('https"://' is 'https://' once the quote is gone).
    out = re.sub(r"[^A-Za-z0-9 .,;:()'%$/&-]", "", out)
    out = re.sub(r"(?i)\b(?:https?|ftps?|file|mailto)\s*:\s*/*", "", out)
    out = re.sub(r":/+", ": ", out)                        # no scheme is left to open
    out = re.sub(r"(?i)\bwww\.", "www ", out)
    out = re.sub(r"\.(?=[A-Za-z0-9])", ". ", out)          # no dotted host, by name or by number
    out = out.strip()
    return out if len(out) <= limit else out[:limit].rstrip() + " ..."


def cell(text: Any) -> str:
    """County or run text in a table cell: the same plain text as an
    agent's words (a reject reason, a rank, a grade, a run folder's name)."""
    return plain(text, 200)


def pid_cell(pid: str) -> str:
    return f"`{pid}`" if not re.search(r"[`|\r\n]", pid) else cell(pid)


def read_lead(folder: Path) -> dict[str, Any] | None:
    """One lead's checked file as the fields the tally uses, or None when
    the folder holds no usable one (not a regular file, too large, not
    JSON, or about another parcel)."""
    path = folder / CHECKED_NAME
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CHECKED_BYTES:
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("parcel_id"), str):
        return None
    pid = doc["parcel_id"]
    if pid.replace(" ", "_") != folder.name:
        return None
    sections = doc.get("sections") if isinstance(doc.get("sections"), dict) else {}
    verdicts = {}
    for name in SECTIONS:
        value = sections.get(name, {}).get("verdict") if isinstance(sections.get(name), dict) else None
        verdicts[name] = (value if isinstance(value, str) and len(value) <= 40
                          and re.fullmatch(r"[a-z]+(?:_[a-z]+)*", value) else "unknown")
    skeptic = doc.get("skeptic") if isinstance(doc.get("skeptic"), dict) else {}
    verdict = skeptic.get("verdict") if skeptic.get("verdict") in VERDICTS else NO_SKEPTIC
    objection = skeptic.get("objection") if isinstance(skeptic.get("objection"), str) else ""
    return {"parcel_id": pid, "verdict": verdict,
            "supported": skeptic.get("supported") is True, "sections": verdicts, "objection": objection}


def reasons_of(lead: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(reasons the skeptic's objection words say, reasons the checked
    sections say), as codes in REASONS order."""
    # A sentence that says a search found nothing ("Nothing found shows a
    # building, a developer or a sale...") names things that are not there.
    # Only that clause is left out: what follows ", so", ", but" or a
    # semicolon is the skeptic's own finding again.
    sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+", lead["objection"].lower()):
        if re.match(r"(?:nothing|no (?:document|sign|record)s?)\b[^.]{0,40}\bfound\b", sentence):
            rest = re.split(r",\s+(?:so|but|yet)\b|;", sentence, maxsplit=1)
            sentence = rest[1] if len(rest) > 1 else ""
        sentences.append(sentence)
    text = " ".join(sentences)
    by_words = [code for code, r in REASONS.items() if any(re.search(p, text) for p in r["words"])]
    by_sections = [code for code, r in REASONS.items()
                   if any(lead["sections"].get(s) in values for s, values in r["sections"].items())]
    return by_words, by_sections


def read_csv(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return out
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            pid = (row.get("parcel_id") or "").strip()
            if pid and pid not in out:
                out[pid] = row
    return out


def today_of(pid: str, runs: list[tuple[str, dict[str, dict[str, str]], dict[str, dict[str, str]]]]) -> tuple[str, bool | None]:
    """(what the newest run that knows the parcel says of it, whether it is
    still a grade A or B lead there; None when no run given knows it)."""
    for name, ranked, rejected in runs:
        if pid in ranked:
            grade = (ranked[pid].get("lead_grade") or "").strip()
            grade = "not graded" if not grade or grade.startswith("not graded") else grade
            return f"rank {cell(ranked[pid].get('rank'))}, grade {cell(grade)} ({name})", grade in GRADES
        if pid in rejected:
            why = cell(rejected[pid].get("reasons") or "no reason given")
            return f"screened out: {why} ({name})", False
    return ("in none of the runs given" if runs else ""), None


def tally(leads_dir: Path, run_dirs: list[Path]) -> tuple[str, str]:
    """(the tally text, the one line of counts)."""
    folders = sorted(d for d in leads_dir.iterdir()
                     if d.is_dir() and not d.is_symlink() and SLUG.fullmatch(d.name))
    leads = [lead for d in folders if (lead := read_lead(d)) is not None]
    unreadable = len(folders) - len(leads)
    if not leads:
        raise Refuse(f"{leads_dir} holds no lead with a readable {CHECKED_NAME}")
    runs = []

    def dated(d: Path) -> tuple[str, str]:
        found = re.search(r"\d{4}-\d{2}-\d{2}", d.name)
        return (found.group(0) if found else "", d.name)
    for d in sorted(run_dirs, key=dated, reverse=True):                 # newest date first, whatever the county
        if not (d / "ranked.csv").is_file():
            raise Refuse(f"{d} has no ranked.csv")
        ranked = read_csv(d / "ranked.csv")
        if not ranked:
            raise Refuse(f"{d}/ranked.csv lists no parcel (no rows, or no parcel_id column)")
        runs.append((cell(d.name), ranked, read_csv(d / "rejected.csv")))

    counts = {v: sum(1 for x in leads if x["verdict"] == v) for v in (*VERDICTS, NO_SKEPTIC)}
    failed = [x for x in leads if x["verdict"] in ("killed", "weakened")]
    for lead in leads:
        lead["by_words"], lead["by_sections"] = reasons_of(lead)
        lead["today"], lead["still"] = today_of(lead["parcel_id"], runs)

    lines = ["# Lead checks: why leads fail", "",
             f"{len(leads):,} leads have a document check: "
             + ", ".join(f"{counts[v]:,} {v}" for v in VERDICTS)
             + (f", {counts[NO_SKEPTIC]:,} without a skeptic verdict" if counts[NO_SKEPTIC] else "") + "."
             + (f" {unreadable:,} lead folders have no readable checked file and are left out."
                if unreadable else ""), ""]
    if runs:
        still = sum(1 for x in failed if x["still"])
        lines += [f"Compared with {', '.join('`' + r[0] + '`' for r in runs)}: {still:,} of the "
                  f"{len(failed):,} killed or weakened leads are still grade A or B there. A run made "
                  "before a lead was checked could not know of the check: this says what the screens "
                  "let through, not that anyone ignored a finding.", ""]
    lines += ["A reason is read from the skeptic's own words by fixed word patterns, and from the "
              "checked sections' verdicts. It is a prompt to read those leads, not a measurement: "
              "open each `lead.md` before changing a screen.", "",
              "## Reasons", "",
              "| Reason | Killed | Weakened | Of these, from the words / from a checked section | "
              "Still grade A or B |",
              "|---|---|---|---|---|"]

    def still_text(hit: list[dict[str, Any]]) -> str:
        if not runs:
            return "not compared"
        unknown = sum(1 for x in hit if x["still"] is None)
        return f"{sum(1 for x in hit if x['still'])}" + (f" ({unknown} in none of the runs)" if unknown else "")

    for lead in failed:
        lead["codes"] = [c for c in REASONS if c in lead["by_words"] or c in lead["by_sections"]]
    open_reasons = []
    for code, reason in REASONS.items():
        hit = [x for x in failed if code in x["codes"]]
        killed = sum(1 for x in hit if x["verdict"] == "killed")
        lines.append(f"| {reason['label']} | {killed} | {len(hit) - killed} | "
                     f"{sum(1 for x in hit if code in x['by_words'])} / "
                     f"{sum(1 for x in hit if code in x['by_sections']) if reason['sections'] else 'n/a'} | "
                     f"{still_text(hit)} |")
        if len(hit) >= PROPOSE_MIN:
            open_reasons.append((code, hit, sum(1 for x in hit if x["still"])))
    none = [x for x in failed if not x["codes"]]
    lines += [f"| No reason found | {sum(1 for x in none if x['verdict'] == 'killed')} | "
              f"{sum(1 for x in none if x['verdict'] == 'weakened')} | n/a | {still_text(none)} |", "",
              "A lead can have several reasons, so the rows add up to more than the leads.", ""]

    lines += ["## What to look at", ""]
    if not open_reasons:
        lines += [f"No reason has {PROPOSE_MIN} or more killed or weakened leads yet.", ""]
    # Most leads still coming through first; then most leads.
    for code, hit, still in sorted(open_reasons, key=lambda t: (-t[2], -len(t[1]))):
        reason = REASONS[code]
        known = [x for x in hit if x["still"] is not None]
        if not runs:
            state = "not compared with a run (pass --run)."
        elif not known:
            state = "none of them is in the runs given, so nothing can be said about today."
        elif still:
            state = (f"{still} of them still come through as grade A or B in the newest run that has "
                     "them.")
        else:
            state = ("none of them is grade A or B in the newest run that has them: screened out, "
                     "regraded, or marked.")
        lines += [f"### {reason['label']}", "",
                  f"{len(hit)} leads ({sum(1 for x in hit if x['verdict'] == 'killed')} killed); {state}", "",
                  f"Look at {reason['look']}.", "",
                  "Leads: " + ", ".join(pid_cell(x["parcel_id"]) + (" (still A/B)" if x["still"] else "")
                                        for x in sorted(hit, key=lambda x: (not x["still"], x["parcel_id"])))
                  + ".", ""]

    lines += ["## Every lead", "",
              "The objection is the skeptic agent's own words, cut short and reduced to plain text. "
              "It is not checked; the quotes behind it are in the lead's `lead.md`.", "",
              "| Parcel | Skeptic | Reasons | Zoning / plans / utilities / market / owner | "
              + ("Today | " if runs else "") + "The skeptic's objection (its words) |",
              "|---|---|---|---|" + ("---|" if runs else "") + "---|"]
    order = {v: i for i, v in enumerate(("killed", "weakened", "survives", NO_SKEPTIC))}
    for lead in sorted(leads, key=lambda x: (order[x["verdict"]], x["parcel_id"])):
        codes = [c for c in REASONS if c in lead["by_words"] or c in lead["by_sections"]]
        verdict = lead["verdict"] + ("" if lead["supported"] or lead["verdict"] == NO_SKEPTIC
                                     else " (no kept quote of its own)")
        row = [pid_cell(lead["parcel_id"]), verdict, ", ".join(codes) or "none matched",
               " / ".join(lead["sections"][s] for s in SECTIONS)]
        if runs:
            row.append(lead["today"])
        row.append(plain(lead["objection"], EXCERPT_CHARS) or "none given")
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## The reason codes", ""]
    lines += [f"- `{code}`: {r['label']}." for code, r in REASONS.items()]
    lines += ["", "Nothing here changes a score, a grade, a rank or a decision. `screens.py` and "
              "`settings.toml` are yours to change, and a change needs a new evaluation baseline "
              "(`python -m screener.autoresearch.evaluate --decide --new-baseline`)."]

    top = sorted(open_reasons, key=lambda t: (-t[2], -len(t[1])))[:1]
    summary = (f"{len(leads)} leads checked so far: {counts['killed']} killed, {counts['weakened']} "
               f"weakened, {counts['survives']} survive."
               + (f" Most common reason still coming through: {REASONS[top[0][0]]['label'].lower()} "
                  f"({len(top[0][1])} leads, {top[0][2]} still grade A or B)." if top and runs and top[0][2] else
                  f" Most common reason: {REASONS[top[0][0]]['label'].lower()} ({len(top[0][1])} leads)."
                  if top else ""))
    return "\n".join(lines) + "\n", summary


def write(path: Path, text: str) -> None:
    """Atomically: a new temp file beside it, then os.replace. Never over
    something that is not a regular file (a folder, a device, a link)."""
    if os.path.lexists(path) and (path.is_symlink() or not path.is_file()):
        raise Refuse(f"{path} is there and is not a regular file")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("leads_dir", type=Path, help="the lead folder root (screener/leads)")
    ap.add_argument("--run", type=Path, action="append", default=[],
                    help="a run folder to compare with (may be given more than once)")
    ap.add_argument("--out", type=Path, help=f"the file to write (default: LEADS_DIR/{FILE_NAME})")
    args = ap.parse_args(argv)
    try:
        if not args.leads_dir.is_dir():
            raise Refuse(f"{args.leads_dir} is not a folder")
        text, summary = tally(args.leads_dir, args.run)
        out = args.out if args.out is not None else args.leads_dir / FILE_NAME
        write(out, text)
    except Refuse as exc:
        print(f"lead_tally: refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, csv.Error, ValueError) as exc:       # ValueError: text that is not UTF-8, either way
        print(f"lead_tally: refused: cannot read or write a file ({type(exc).__name__}: "
              f"{plain(exc, 200)})", file=sys.stderr)
        return 2
    print(summary)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
