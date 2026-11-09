"""One-off review artefact (reviews/26_flood_wetlands, FW-03 / FW-25).

NOT part of the unittest suite (the file name does not match test_*.py):
it compares the CURRENT research.py to a frozen record of research.py at
commit 8ddaf69 (before the hazards module existed): discovery (every hard
filter's result and ``_process_parcel``'s status and executed statements)
and scoring (the site-fit golden case, per parcel and cycle). Two modes:

- gate OFF (``hazards_helpers.disabled()``): byte-identical to the record;
- gate ON with the table EMPTY, the SHIPPED state since the operator-
  authorized prepare-mutation of 2026-11-09 (reviews/33_hazards_enabled):
  identical apart from the documented flag-text delta
  (``hazards_golden_case.differences_on_empty``: discovery H4/H6 flag ->
  pass and two fewer stub flagged_items INSERTs; scoring one hazard read
  per path and two "no hazards import" data_gap INSERTs per parcel;
  results, parcel_scores inserts and research_log notes identical).

Like the zoning and site-fit golden checks it stops being meaningful once
an experiment is kept; the suite carries the differential versions
(tests/test_hazards_scoring.py, tests/test_hazards_enabled.py).

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
from hazards_helpers import disabled  # noqa: E402

RECORD = HERE.parent / "reviews" / "26_flood_wetlands" / "golden_head_8ddaf69.json"
# The record was taken on this UTC date; discovery_date comes from
# research.datetime.now, so the check freezes that clock to the record date
# (reviews/29_operator_decisions round 2, F4). Exit 0 = identical again.
RECORD_DATE = (2026, 11, 6)


class _RecordClock(research.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return cls(*RECORD_DATE, 12, 0, 0, tzinfo=tz)


def current() -> dict:
    real = research.datetime
    research.datetime = _RecordClock
    try:
        return json.loads(json.dumps(golden.run_case(research), sort_keys=True, default=str))
    finally:
        research.datetime = real


def main() -> int:
    if zoning.any_verified():
        print("not comparable: some jurisdiction is verified (the record is all-draft)")
        return 2
    want = json.loads(RECORD.read_text(encoding="utf-8"))
    shipped_on = hazards.enabled()
    with disabled():
        got = current()
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
        print(f"gate OFF DIFFERS from the 8ddaf69 record: {len(bad)} case(s): "
              f"{', '.join(bad[:20])}")
        return 1
    print(f"gate off: identical to the 8ddaf69 record: {len(want['discovery']['process'])} "
          f"discovery runs, {len(want['discovery']['filters'])} filter sets, "
          f"{len(ws['per_parcel'])} scored parcels + the cycle (statements, parameters, "
          "inserts, notes)")
    if not shipped_on:
        print("the shipped gate is OFF: the enabled, empty-table mode was not run")
        return 0
    bad = golden.differences_on_empty(want, current(), hazards._SQL_HAZARDS_FOR_PARCELS)
    if bad:
        print(f"gate ON + empty table DIFFERS: {len(bad)} case(s): {', '.join(bad[:20])}")
        return 1
    print("enabled, empty table (the shipped state): parcel_scores inserts, results and "
          "notes identical; discovery statuses identical, H4/H6 flag -> pass and the two "
          "stub flagged_items INSERTs gone; scoring adds one hazard read per path and two "
          "'no hazards import' data_gap INSERTs per parcel, nothing else")
    return 0


if __name__ == "__main__":
    sys.exit(main())
