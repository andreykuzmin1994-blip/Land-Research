"""Record what you decided about a parcel from the latest run.

    python -m screener.decide "14F0063  LL0219" pursue called owner, open to offers
    python -m screener.decide 09F030000140097 maybe
    python -m screener.decide "07 290001330986" pass power line easement
    python -m screener.decide ID pursue --run screener/output/fulton-2026-11-09
    python -m screener.decide --import decisions-fulton-2026-11-09.csv

Decisions: pursue, pass, maybe, listed (as in inputs/decisions.csv). The
parcel id must match the run's ranked.csv (or rejected.csv) exactly,
spaces included, so quote an id that has spaces. Each call appends one row to inputs/decisions.csv (the
last row for a parcel wins, so a later row undoes an earlier one) and
copies the parcel's features from the run's features.json into
inputs/decision_features.jsonl, so they outlive the run folder. Both files
are kept out of git. Offline: no network.

--import FILE records every mark in a file the Site Plan Viewer page
saved ("Save my marks"): the same checks and the same three files as one
call per mark. The file names the run its page was built from, and that
run's folder must be the one used (the newest, or --run). Every row is
checked before anything is written: one bad row and nothing is recorded.
A mark is skipped, and said to be, when the same mark (decision, date and
note) is already among the parcel's rows, or when the parcel's last row
is newer than the mark: importing a file twice adds nothing, and an old
file never undoes a later decision. To record such a mark anyway, give it
as a single decision.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import facts, run, snapshot

STORE_NAME = "decision_features.jsonl"
CONSEQUENCE = {
    "pass": "this parcel will be dropped from future runs (\"you passed on this\").",
    "listed": "this parcel will be dropped from future runs while exclude_listed is on.",
    "pursue": "marked in ranked.csv; summary.md says so if a later run screens it out.",
    "maybe": "a mark only: no score, flag, order or screen changes.",
}


IMPORT_MAX_BYTES = 1_000_000   # a marks file is a few rows; refuse anything large
IMPORT_MAX_ROWS = 5_000
NOTE_MAX_CHARS = 500


class Refuse(Exception):
    """Nothing written; the message says why."""


def decisions_file() -> Path:
    return run.DECISIONS_FILE  # read at call time, so tests can redirect it


def store_file() -> Path:
    return run.DECISIONS_FILE.with_name(STORE_NAME)


def output_root() -> Path:
    return run.HERE / "output"


def latest_run() -> Path:
    root = output_root()
    runs = sorted(d for d in root.iterdir() if (d / "ranked.csv").is_file()) if root.is_dir() else []
    if not runs:
        raise Refuse(f"no run found under {output_root()} (run python -m screener.run first, "
                     "or pass --run DIR)")
    mtime = {d: (d / "ranked.csv").stat().st_mtime_ns for d in runs}
    newest = max(mtime.values())
    tied = sorted(d.name for d in runs if mtime[d] == newest)
    if len(tied) > 1:
        raise Refuse(f"the latest runs tie ({', '.join(tied)}): pass --run DIR")
    return next(d for d in runs if mtime[d] == newest)


def run_ids(run_dir: Path) -> tuple[list[str], list[str]]:
    """(ranked ids, rejected ids) of a run, stripped as load_decisions does."""
    out = []
    for name in ("ranked.csv", "rejected.csv"):
        path = run_dir / name
        if not path.is_file():
            out.append([])
            continue
        with open(path, newline="", encoding="utf-8-sig") as fh:
            out.append([(r.get("parcel_id") or "").strip() for r in csv.DictReader(fh)])
    return out[0], out[1]


def near(pid: str, ids: list[str], n: int = 5) -> list[str]:
    """Ids that differ from pid only in spacing, else same-length ids that
    share the longest prefix with it. Shown, never picked."""
    squash = "".join(pid.split())
    spacing = sorted({i for i in ids if "".join(i.split()) == squash})
    if spacing:
        return spacing[:n]

    def common(i: str) -> int:
        k = 0
        while k < min(len(i), len(pid)) and i[k] == pid[k]:
            k += 1
        return k
    same = sorted({i for i in ids if len(i) == len(pid) and common(i) >= 3},
                  key=lambda i: (-common(i), i))
    return same[:n]


def snapshot_records(run_dir: Path, pid: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    path = run_dir / snapshot.FILE_NAME
    if not path.is_file():
        return None, []
    try:
        snap = json.loads(path.read_text())
        meta, records = snap["meta"], snap["records"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refuse(f"{path} is unreadable ({exc}); re-run the screen") from exc
    return meta, [r for r in records if r.get("parcel_id") == pid]


def _ends_with_newline(path: Path) -> bool:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            return True
        fh.seek(-1, os.SEEK_END)
        return fh.read(1) in (b"\n", b"\r")


def _header(path: Path) -> list[str] | None:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return next(csv.reader(fh), None)


def decisions_header() -> list[str]:
    """The decisions file's header, creating the file (with
    run.DECISIONS_HEADER) if it is absent or empty. Refuses a file whose
    header has no parcel_id and decision columns."""
    path = decisions_file()
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(run.DECISIONS_HEADER)
    header = _header(path)
    if not header or not {"parcel_id", "decision"} <= {h.strip() for h in header}:
        raise Refuse(f"{path} has no parcel_id,decision header; fix the file by hand "
                     f"(expected {','.join(run.DECISIONS_HEADER)})")
    return header


def append_decision(header: list[str], pid: str, decision: str, day: str, note: str) -> None:
    """Append one row. Never rewrites the file: history is the label record."""
    path = decisions_file()
    row = {"parcel_id": pid, "decision": decision, "date": day, "note": note}
    # Follow the file's own column order, so a reordered or widened header
    # still lines up. Columns we don't know are left blank.
    cells = [row.get(h.strip(), "") for h in header]
    with open(path, "a", newline="", encoding="utf-8") as fh:
        if not _ends_with_newline(path):
            fh.write("\r\n")  # csv.writer's own line ending
        csv.writer(fh).writerow(cells)


def append_store(entry: dict[str, Any]) -> None:
    path = store_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, allow_nan=False) + "\n")


def facts_store_file() -> Path:
    return run.DECISIONS_FILE.with_name(facts.STORE_NAME)


def append_facts(entry: dict[str, Any]) -> None:
    """The report facts shown when the mark was made, in their own store:
    never mixed with the evaluation's features (facts.py)."""
    line = json.dumps(entry, allow_nan=False) + "\n"   # before the file is touched
    path = facts_store_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def prepare(run_dir: Path, ranked: list[str], rejected: list[str], pid: str,
            no_features: bool) -> dict[str, Any]:
    """Read and check everything one decision needs. Writes nothing; raises
    Refuse when the decision cannot be recorded."""
    if not pid:
        raise Refuse("empty parcel id")
    if pid not in ranked and pid not in rejected:
        close = near(pid, ranked + rejected)
        hint = ("; close matches: " + " | ".join(repr(c) for c in close)) if close else ""
        raise Refuse(f"parcel id {pid!r} is not in this run{hint}")
    meta, records = snapshot_records(run_dir, pid)
    no_features_note = ""
    if not records:
        why = (f"{run_dir.name} has no {snapshot.FILE_NAME}" if meta is None
               else "it isn't in this run's snapshot (only ranked parcels are)")
        if not no_features:
            raise Refuse(f"no features to keep for {pid!r}: {why}. "
                         "Pass --no-features to record the decision anyway")
        no_features_note = (f"no features kept for this decision ({why}); missing: "
                            + ", ".join(snapshot.FEATURES))
    facts_meta, fact_records, facts_problem = facts.records_for(run_dir, pid)
    return {"pid": pid, "run": run_dir.name, "meta": meta, "records": records,
            "no_features_note": no_features_note, "facts_meta": facts_meta,
            "fact_records": fact_records, "facts_problem": facts_problem}


def commit(prep: dict[str, Any], header: list[str], decision: str, day: str, note: str) -> None:
    """Append one checked decision to the three files."""
    pid = prep["pid"]
    # Features first: a crash after this leaves features without a
    # decision row (harmless), never a decision whose features are lost.
    append_store({
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "parcel_id": pid, "decision": decision, "date": day,
        "run": prep["run"], "run_meta": prep["meta"], "records": prep["records"],
    })
    if prep["fact_records"]:
        # The facts are an extra: a failure here must never cost the
        # decision row (the features above are already kept).
        try:
            append_facts({
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "parcel_id": pid, "decision": decision, "date": day,
                "run": prep["run"], "run_meta": prep["facts_meta"],
                "records": prep["fact_records"],
            })
        except (OSError, ValueError, TypeError) as exc:
            print(f"no report facts kept for this decision (could not be written: {exc})")
    append_decision(header, pid, decision, day, note)


def read_marks(path: Path) -> list[dict[str, str]]:
    """The marks in a file the viewer page saved: parcel_id, decision, and
    optionally date, note and run. Checked, not yet matched to a run. The
    last row for a parcel wins, as in decisions.csv."""
    if not path.is_file():
        raise Refuse(f"{path} is not a file")
    if path.stat().st_size > IMPORT_MAX_BYTES:
        raise Refuse(f"{path} is over {IMPORT_MAX_BYTES:,} bytes: not a marks file")
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            reader.fieldnames = [(h or "").strip() for h in (reader.fieldnames or [])]
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise Refuse(f"{path} cannot be read as CSV ({exc})") from exc
    if not {"parcel_id", "decision"} <= set(reader.fieldnames):
        raise Refuse(f"{path} has no parcel_id,decision header")
    if len(rows) > IMPORT_MAX_ROWS:
        raise Refuse(f"{path} has more than {IMPORT_MAX_ROWS:,} rows: not a marks file")
    today = date.today()
    marks: dict[str, dict[str, str]] = {}
    for n, row in enumerate(rows, start=2):
        def bad(why: str) -> Refuse:
            return Refuse(f"{path.name} line {n}: {why}; nothing recorded")
        if None in row:    # csv puts the cells beyond the header under None
            raise bad("more cells than the header has columns")
        pid = (row.get("parcel_id") or "").strip()
        decision = (row.get("decision") or "").strip().lower()
        day = (row.get("date") or "").strip()
        note = " ".join((row.get("note") or "").split())
        if not pid:
            raise bad("empty parcel id")
        if decision not in run.DECISION_VALUES:
            raise bad(f"decision {decision!r} is not one of {', '.join(run.DECISION_VALUES)}")
        if day:
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                raise bad(f"date {day!r} is not YYYY-MM-DD") from None
            # One day of slack: the page's clock may be a time zone ahead.
            if parsed > today + timedelta(days=1):
                raise bad(f"date {day} is in the future")
            day = parsed.isoformat()
        if len(note) > NOTE_MAX_CHARS:
            raise bad(f"the note is over {NOTE_MAX_CHARS} characters")
        marks.pop(pid, None)   # the last row for a parcel wins, in its own place
        marks[pid] = {"parcel_id": pid, "decision": decision, "date": day or today.isoformat(),
                      "note": note, "run": (row.get("run") or "").strip()}
    if not marks:
        raise Refuse(f"{path} holds no marks")
    return list(marks.values())


def decision_history() -> tuple[tuple[str, ...], dict[str, list[dict[str, str]]]]:
    """(the columns of decision, date and note that decisions.csv has, every
    valid row per parcel in file order). An absent file has all three
    columns and no rows."""
    path = decisions_file()
    if not path.exists() or path.stat().st_size == 0:
        return ("decision", "date", "note"), {}
    history: dict[str, list[dict[str, str]]] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        names = [(h or "").strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = names
        for row in reader:
            pid = (row.get("parcel_id") or "").strip()
            decision = (row.get("decision") or "").strip().lower()
            if pid and decision in run.DECISION_VALUES:
                history.setdefault(pid, []).append(
                    {"decision": decision, "date": (row.get("date") or "").strip(),
                     "note": " ".join((row.get("note") or "").split())})
    return tuple(c for c in ("decision", "date", "note") if c in names), history


def import_marks(path: Path, run_arg: Path | None, no_features: bool) -> int:
    """Record every mark of a viewer page's file, or none. Returns how many
    were written."""
    marks = read_marks(path)
    named = sorted({m["run"] for m in marks if m["run"]})
    if len(named) > 1:
        raise Refuse(f"{path.name} holds marks from more than one run ({', '.join(named)}): "
                     "import each run's file on its own")
    if run_arg is not None:
        run_dir = run_arg
        if not (run_dir / "ranked.csv").is_file():
            raise Refuse(f"{run_dir} has no ranked.csv")
    else:
        run_dir = latest_run()
    print(f"run: {run_dir}")
    if named and named[0] != run_dir.resolve().name:
        raise Refuse(f"these marks were made on the page of run {named[0]!r}, not {run_dir.name!r}: "
                     "pass --run with that run's folder (the features kept with a decision "
                     "must be that run's)")
    ranked, rejected = run_ids(run_dir)
    columns, history = decision_history()
    todo, problems, same, older = [], [], 0, []
    for m in marks:
        rows = history.get(m["parcel_id"], [])
        # Compared on the columns the file has: a decisions.csv without a
        # date or note column cannot tell two marks of one value apart.
        if any(all(r[c] == m[c] for c in columns) for r in rows):
            same += 1
            continue
        if rows and "date" in columns and rows[-1]["date"] > m["date"]:
            older.append(f"{m['parcel_id']!r}: {m['decision']} of {m['date']} not recorded, the "
                         f"{rows[-1]['decision']} of {rows[-1]['date']} on file is newer")
            continue
        try:
            todo.append((m, prepare(run_dir, ranked, rejected, m["parcel_id"], no_features)))
        except Refuse as exc:
            problems.append(str(exc))
    if problems:
        raise Refuse("nothing recorded; " + "; ".join(problems[:10])
                     + (f"; and {len(problems) - 10} more" if len(problems) > 10 else ""))
    if todo:
        header = decisions_header()
        for m, prep in todo:
            for line in (prep["no_features_note"],
                         f"no report facts kept for this decision ({prep['facts_problem']})"
                         if prep["facts_problem"] else ""):
                if line:
                    print(f"{m['parcel_id']}: {line}")
            print(f"{m['parcel_id']}: {m['decision']}: {CONSEQUENCE[m['decision']]}")
            commit(prep, header, m["decision"], m["date"], m["note"])
    for line in older:
        print(f"skipped: {line}")
    print(f"{len(todo)} recorded in {decisions_file()}"
          + (f"; {same} already recorded, skipped" if same else "")
          + (f"; {len(older)} older than the decision on file, skipped" if older else ""))
    return len(todo)


def import_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m screener.decide --import",
        description="Record every mark in a file saved from the Site Plan Viewer page.")
    ap.add_argument("--import", dest="import_file", type=Path, metavar="FILE", required=True,
                    help="the decisions-<run>.csv the page's Save my marks downloaded")
    ap.add_argument("--run", type=Path, help="run folder (default: the newest under screener/output)")
    ap.add_argument("--no-features", action="store_true",
                    help="record a mark even if the run has no features for its parcel")
    args, extra = ap.parse_known_args(argv)
    if extra:
        ap.error("--import takes no parcel id or decision (got: " + " ".join(extra)[:80] + ")")
    try:
        import_marks(args.import_file, args.run, args.no_features)
    except Refuse as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    # The marks file has its own small parser, so the single-decision
    # command line below is exactly what it was before --import existed.
    if any(a == "--import" or a.startswith("--import=") for a in argv):
        return import_main(argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parcel_id", help="exactly as in ranked.csv; quote it if it has spaces")
    ap.add_argument("decision", type=str.lower, choices=run.DECISION_VALUES)
    ap.add_argument("reason", nargs="*", help="free text, saved as the note")
    ap.add_argument("--run", type=Path, help="run folder (default: the newest under screener/output)")
    ap.add_argument("--no-features", action="store_true",
                    help="record the decision even if the run has no features for this parcel")
    args = ap.parse_args(argv)

    pid = args.parcel_id.strip()
    note = " ".join(args.reason).strip()
    try:
        if not pid:
            raise Refuse("empty parcel id")
        if args.run is not None:
            run_dir = args.run
            if not (run_dir / "ranked.csv").is_file():
                raise Refuse(f"{run_dir} has no ranked.csv")
        else:
            run_dir = latest_run()
        print(f"run: {run_dir}")
        ranked, rejected = run_ids(run_dir)
        prep = prepare(run_dir, ranked, rejected, pid, args.no_features)
        if prep["no_features_note"]:
            print(prep["no_features_note"])
        header = decisions_header()
        day = date.today().isoformat()
        if prep["facts_problem"]:
            print(f"no report facts kept for this decision ({prep['facts_problem']})")
        print(f"{pid}: {args.decision}: {CONSEQUENCE[args.decision]}")
        commit(prep, header, args.decision, day, note)
    except Refuse as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"recorded in {decisions_file()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
