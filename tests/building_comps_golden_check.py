"""One-off review artefact (reviews/28_building_comps, BC-16).

NOT part of the unittest suite (the file name does not match test_*.py): it
renders a fixed memo and snapshot through the CURRENT reporting.py, with no
building or lease comps of costar_import in the market, and compares them to
a frozen record of reporting.py at commit 3898047 (before building comps
existed). Identical texts; the only new statements are the two
building-comps reads at the end of each path. The suite carries the
in-module version (tests/test_building_comps.MemoReadTests).

    python tests/building_comps_golden_check.py        # exit 0 = identical

Regenerate the record (only to re-prove against a different base commit):

    B=$(mktemp -d) && git show 3898047:reporting.py > $B/reporting_base.py && \\
    PYTHONPATH=.:tests python -c "import sys, json, importlib.util as u; \\
      sys.path.insert(0, '$B'); import tests, building_comps_golden_case as c, \\
      reporting_base as r; assert not hasattr(r, 'fetch_building_comps'); \\
      json.dump(c.run_case(r), open('reviews/28_building_comps/golden_head_3898047.json', 'w'), \\
      indent=1, sort_keys=True)"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import tests  # noqa: E402,F401 — suite guards (mirror kill switch, sources/ redirect)
import reporting  # noqa: E402
import building_comps_golden_case as golden  # noqa: E402

RECORD = HERE.parent / "reviews" / "28_building_comps" / "golden_head_3898047.json"


def main() -> int:
    base = json.loads(RECORD.read_text(encoding="utf-8"))
    now = golden.run_case(reporting)
    problems = []
    for key in ("memo", "snapshot"):
        if now[key] != base[key]:
            problems.append(f"{key} text differs")
        new_reads = [reporting._SQL_BC_LEASES, reporting._SQL_BC_SALES]
        if now[f"{key}_sql"] != base[f"{key}_sql"] + new_reads:
            problems.append(f"{key} statements differ beyond the two building-comps reads")
    if problems:
        print("DIFFERENT: " + "; ".join(problems))
        return 1
    print("identical to the 3898047 record: memo and snapshot texts; statements identical "
          "apart from the two building-comps reads at the end of each path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
