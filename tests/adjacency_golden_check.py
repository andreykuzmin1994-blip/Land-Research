"""One-off review artefact (reviews/27_site_fit_buffers, BUF-03).

NOT part of the unittest suite (the file name does not match test_*.py):
it compares the CURRENT research.py / site_fit.py / reporting.py, with every
fixture jurisdiction verified (and, for projections and the memo, all
draft), to a frozen record of the same three modules at commit 595aa08
(before the adjacency module existed): scoring (results, every executed
statement and parameter, parcel_scores inserts, research_log notes),
projections (every field, ring text included, and the statements) and the
memo/snapshot lines. Two states: the gate OFF (prepare.py carries no
``_DDL_PARCEL_ADJACENCY``: everything identical) and the gate ON with an
empty table (scores identical; the only new statement is the adjacency
read; only the adjacency texts change). The suite carries the same
comparison (tests/test_adjacency_scoring.DifferentialTests).

    python tests/adjacency_golden_check.py        # exit 0 = identical

Regenerate the record (only to re-prove against a different base commit),
from a worktree of the base, so that its modules, not the repo's, are first
on sys.path:

    B=$(mktemp -d)/base && git worktree add --detach $B 595aa08 && \\
    (cd $B && PYTHONPATH=$B:$B/tests:$OLDPWD/tests python -c "import tests, research, \\
      site_fit, reporting, json, adjacency_golden_case as c; \\
      assert not hasattr(site_fit, '_adj'); json.dump(c.run_case(research, site_fit, \\
      reporting), open('$OLDPWD/reviews/27_site_fit_buffers/golden_head_595aa08.json', 'w'), \\
      indent=1, sort_keys=True, default=str)") && git worktree remove --force $B
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import tests  # noqa: E402,F401 — suite guards (mirror kill switch, sources/ redirect)
import adjacency  # noqa: E402
import reporting  # noqa: E402
import research  # noqa: E402
import site_fit  # noqa: E402
import zoning  # noqa: E402
import adjacency_golden_case as golden  # noqa: E402
from adjacency_helpers import enabled  # noqa: E402

RECORD = HERE.parent / "reviews" / "27_site_fit_buffers" / "golden_head_595aa08.json"


def current() -> dict:
    return json.loads(json.dumps(golden.run_case(research, site_fit, reporting),
                                 sort_keys=True, default=str))


def main() -> int:
    if zoning.any_verified() or adjacency.enabled():
        print("not comparable: the shipped state must be all-draft with the gate off")
        return 2
    want = json.loads(RECORD.read_text(encoding="utf-8"))
    bad = golden.differences_off(want, current())
    if bad:
        print(f"gate OFF DIFFERS from the 595aa08 record: {len(bad)} case(s): {', '.join(bad)}")
        return 1
    print("gate off: identical to the 595aa08 record (scoring with every fixture jurisdiction "
          "verified, projections and memo lines verified and draft)")
    with enabled():
        got = current()
    bad = golden.differences_on_empty(want, got, site_fit, adjacency)
    if bad:
        print(f"gate ON + empty table DIFFERS: {len(bad)} case(s): {', '.join(bad[:20])}")
        return 1
    print("gate on + empty table: parcel_scores inserts, results and notes identical; SQL "
          "identical apart from the one adjacency read; projections identical (ring text "
          "included) apart from the adjacency texts; memo lines and footnote changed only in "
          "the adjacency clause")
    return 0


if __name__ == "__main__":
    sys.exit(main())
