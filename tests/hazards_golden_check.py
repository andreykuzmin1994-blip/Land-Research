"""One-off review artefact (reviews/26_flood_wetlands, FW-03 / FW-25).

NOT part of the unittest suite (the file name does not match test_*.py):
it compares the CURRENT research.py, with the hazard table NOT enabled (the
shipped state: prepare.py carries no ``_DDL_PARCEL_HAZARDS``), to a frozen
record of research.py at commit 8ddaf69 (before the hazards module
existed): discovery (every hard filter's result and ``_process_parcel``'s
status and executed statements) and scoring (the site-fit golden case, per
parcel and cycle). Like the zoning and site-fit golden checks it stops
being meaningful once an experiment is kept; the suite carries the
differential version (tests/test_hazards_scoring.py).

    python tests/hazards_golden_check.py        # exit 0 = identical

Regenerate the record (only to re-prove against a different base commit).
Run from the directory holding the old research.py, so that it, not the
repo's, is first on sys.path:

    H=$(mktemp -d) && git show 8ddaf69:research.py > $H/research.py && \\
    (cd $H && PYTHONPATH=$H:$OLDPWD:$OLDPWD/tests python -c "import tests, research, \\
      json, hazards_golden_case as c; assert not hasattr(research, 'hazards'); \\
      json.dump(c.run_case(research), open('$OLDPWD/reviews/26_flood_wetlands/\\
golden_head_8ddaf69.json', 'w'), indent=1, sort_keys=True, default=str)")
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import tests  # noqa: E402,F401 — suite guards (mirror kill switch, sources/ redirect)
import hazards  # noqa: E402
import research  # noqa: E402
import zoning  # noqa: E402
import hazards_golden_case as golden  # noqa: E402

RECORD = HERE.parent / "reviews" / "26_flood_wetlands" / "golden_head_8ddaf69.json"
# The record was taken on this UTC date; discovery_date comes from
# research.datetime.now, so the check freezes that clock to the record date
# (reviews/29_operator_decisions round 2, F4). Exit 0 = identical again.
RECORD_DATE = (2026, 11, 6)


class _RecordClock(research.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return cls(*RECORD_DATE, 12, 0, 0, tzinfo=tz)


def main() -> int:
    if zoning.any_verified():
        print("not comparable: some jurisdiction is verified (the record is all-draft)")
        return 2
    if hazards.enabled():
        print("not comparable: the hazard table is enabled (the record predates it)")
        return 2
    want = json.loads(RECORD.read_text(encoding="utf-8"))
    real = research.datetime
    research.datetime = _RecordClock
    try:
        got = json.loads(json.dumps(golden.run_case(research), sort_keys=True, default=str))
    finally:
        research.datetime = real
    bad = []
    for part in ("filters", "process"):
        w, g = want["discovery"][part], got["discovery"][part]
        if sorted(w) != sorted(g):
            bad.append(f"<discovery {part} keys>")
        bad += [f"discovery {part} {k}" for k in w if g.get(k) != w[k]]
    ws, gs = want["scoring"], got["scoring"]
    bad += [f"scoring {pid}" for pid in ws["per_parcel"]
            if gs["per_parcel"].get(pid) != ws["per_parcel"][pid]]
    if gs["cycle"] != ws["cycle"]:
        bad.append("<scoring cycle>")
    if bad:
        print(f"DIFFERS from the 8ddaf69 record: {len(bad)} case(s): {', '.join(bad[:20])}")
        return 1
    print(f"identical to the 8ddaf69 record: {len(want['discovery']['process'])} discovery "
          f"runs, {len(want['discovery']['filters'])} filter sets, "
          f"{len(ws['per_parcel'])} scored parcels + the cycle (statements, parameters, "
          "inserts, notes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
