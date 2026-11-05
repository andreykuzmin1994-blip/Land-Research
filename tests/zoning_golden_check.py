"""One-off review artefact (reviews/21_zoning, Z-03; review finding 1).

NOT part of the unittest suite (the file name does not match test_*.py):
it compares the CURRENT research.py's scoring output, with the real
all-draft zoning_rules.json, to a frozen record of the pre-zoning
research.py (commit ba4a780). It proves the zoning change itself moved no
score. It stops being meaningful as soon as any scoring experiment is
kept, which is exactly why it must not gate the agent's suite; the suite
carries the differential version (tests/test_zoning_scoring.py,
DraftEqualsAbsentTests).

    python tests/zoning_golden_check.py        # exit 0 = identical

Regenerate the record (only to re-prove against a different base commit):

    mkdir /tmp/head && git show ba4a780:research.py > /tmp/head/research.py
    PYTHONPATH=/tmp/head:.:tests python -c "import research, json, zoning_golden_case as c; \\
        json.dump(c.run_case(research), open('reviews/21_zoning/golden_scores_head_ba4a780.json', 'w'), \\
        indent=1, sort_keys=True)"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import tests  # noqa: E402,F401 — suite guards (mirror kill switch, sources/ redirect)
import research  # noqa: E402
import zoning  # noqa: E402
import zoning_golden_case as golden  # noqa: E402

RECORD = HERE.parent / "reviews" / "21_zoning" / "golden_scores_head_ba4a780.json"


def main() -> int:
    if zoning.any_verified():
        print("not comparable: some jurisdiction is verified (the record is all-draft)")
        return 2
    want = json.loads(RECORD.read_text(encoding="utf-8"))
    got = json.loads(json.dumps(golden.run_case(research), sort_keys=True, default=str))
    bad = [pid for pid in want["per_parcel"] if got["per_parcel"].get(pid) != want["per_parcel"][pid]]
    if got["cycle"] != want["cycle"]:
        bad.append("<cycle>")
    if bad:
        print(f"DIFFERS from the ba4a780 record: {len(bad)} case(s): {', '.join(bad)}")
        return 1
    print(f"identical to the ba4a780 record: {len(want['per_parcel'])} parcels + the cycle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
