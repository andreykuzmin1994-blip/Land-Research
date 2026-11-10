"""Record what you decided about a parcel from the latest run.

    python -m screener.decide "14F0063  LL0219" pursue called owner, open to offers
    python -m screener.decide 09F030000140097 maybe
    python -m screener.decide "07 290001330986" pass power line easement
    python -m screener.decide ID pursue --run screener/output/fulton-2026-11-09

Decisions: pursue, pass, maybe, listed (as in inputs/decisions.csv). The
parcel id must match the run's ranked.csv (or rejected.csv) exactly,
spaces included, so quote an id that has spaces. Each call appends one row to inputs/decisions.csv (the
last row for a parcel wins, so a later row undoes an earlier one) and
copies the parcel's features from the run's features.json into
inputs/decision_features.jsonl, so they outlive the run folder. Both files
are kept out of git. Offline: no network.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date
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
    path = facts_store_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, allow_nan=False) + "\n")


def main(argv: list[str] | None = None) -> int:
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
        if pid not in ranked and pid not in rejected:
            close = near(pid, ranked + rejected)
            hint = ("; close matches: " + " | ".join(repr(c) for c in close)) if close else ""
            raise Refuse(f"parcel id {pid!r} is not in this run{hint}")
        meta, records = snapshot_records(run_dir, pid)
        if not records:
            why = (f"{run_dir.name} has no {snapshot.FILE_NAME}" if meta is None
                   else "it isn't in this run's snapshot (only ranked parcels are)")
            if not args.no_features:
                raise Refuse(f"no features to keep for {pid!r}: {why}. "
                             "Pass --no-features to record the decision anyway")
            print(f"no features kept for this decision ({why}); missing: "
                  + ", ".join(snapshot.FEATURES))
        header = decisions_header()
        day = date.today().isoformat()
        facts_meta, fact_records, facts_problem = facts.records_for(run_dir, pid)
        if facts_problem:
            print(f"no report facts kept for this decision ({facts_problem})")
        print(f"{pid}: {args.decision}: {CONSEQUENCE[args.decision]}")
        # Features first: a crash after this leaves features without a
        # decision row (harmless), never a decision whose features are lost.
        append_store({
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "parcel_id": pid, "decision": args.decision, "date": day,
            "run": run_dir.name, "run_meta": meta, "records": records,
        })
        if fact_records:
            append_facts({
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "parcel_id": pid, "decision": args.decision, "date": day,
                "run": run_dir.name, "run_meta": facts_meta, "records": fact_records,
            })
        append_decision(header, pid, args.decision, day, note)
    except Refuse as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"recorded in {decisions_file()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
