"""One-off review artefact (reviews/22_site_fit, SF-01).

NOT part of the unittest suite (the file name does not match test_*.py):
it compares the CURRENT research.py's scoring output, with the real
all-draft zoning_rules.json and the real building_prototypes.json, to a
frozen record of research.py at commit 28d3205 (before site_fit existed):
result dicts, every executed SQL statement and parameter, the parcel_scores
insert tuples and the research_log notes, on both the per-parcel and the
cycle (cache) path. It proves the site-fit change itself moved nothing
while every jurisdiction is draft. Like tests/zoning_golden_check.py it
stops being meaningful once a scoring experiment is kept; the suite carries
the differential version (tests/test_site_fit_scoring.py).

    python tests/site_fit_golden_check.py        # exit 0 = identical

Regenerate the record (only to re-prove against a different base commit).
Run from the directory holding the old research.py, so that it, not the
repo's, is first on sys.path:

    H=$(mktemp -d) && git show 28d3205:research.py > $H/research.py && \\
    (cd $H && PYTHONPATH=$H:$OLDPWD:$OLDPWD/tests python -c "import tests, research, \\
      json, site_fit_golden_case as c; assert not hasattr(research, 'site_fit'); \\
      json.dump(c.run_case(research), open('$OLDPWD/reviews/22_site_fit/\\
golden_scores_head_28d3205.json', 'w'), indent=1, sort_keys=True)")

Since the operator-authorized prepare-mutation of 2026-11-09
(reviews/33_hazards_enabled) the hazard gate is ON in the shipped repo. This
record predates the hazard read and records non-hazard behaviour, so the
case runs with the gate OFF (``hazards_helpers.disabled()``); the shipped
hazard state is proven by tests/hazards_golden_check.py's enabled mode.
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
import site_fit_golden_case as golden  # noqa: E402

RECORD = HERE.parent / "reviews" / "22_site_fit" / "golden_scores_head_28d3205.json"


def _compare() -> int:
    if zoning.any_verified():
        print("not comparable: some jurisdiction is verified (the record is all-draft)")
        return 2
    want = json.loads(RECORD.read_text(encoding="utf-8"))
    got = json.loads(json.dumps(golden.run_case(research), sort_keys=True, default=str))
    bad = [pid for pid in want["per_parcel"] if got["per_parcel"].get(pid) != want["per_parcel"][pid]]
    if sorted(got["per_parcel"]) != sorted(want["per_parcel"]):
        bad.append("<parcel set>")
    if got["cycle"] != want["cycle"]:
        bad.append("<cycle>")
    if bad:
        print(f"DIFFERS from the 28d3205 record: {len(bad)} case(s): {', '.join(bad)}")
        return 1
    print(f"identical to the 28d3205 record: {len(want['per_parcel'])} parcels + the cycle "
          "(results, executed SQL and parameters, parcel_scores inserts, research_log notes)")
    # SF-02: with South Fulton verified, every parcel of another (draft)
    # jurisdiction still scores exactly as at 28d3205 on the per-parcel path.
    from zoning_helpers import patch_rules, verified  # noqa: E402
    with patch_rules(verified("south_fulton")):
        mixed = json.loads(json.dumps(golden.run_case(research), sort_keys=True, default=str))
    draft = [pid for pid, v in golden.PARCELS.items() if not (v[4] and "| south_fulton |" in v[4])]
    keys = ("result", "parcel_score_inserts", "research_log_notes")
    # Verified rules anywhere add zoning's own stored-zoning read (reviews/21_zoning);
    # apart from it, the statements must be 28d3205's, and none may be site_fit's.
    zread = {research._SQL_STORED_ZONING, research._SQL_STORED_ZONING_BATCH}

    def stmts(rec):
        return [e for e in rec["executed"] if e[0] not in zread]
    bad = [pid for pid in draft
           if any(mixed["per_parcel"][pid][k] != want["per_parcel"][pid][k] for k in keys)
           or stmts(mixed["per_parcel"][pid]) != stmts(want["per_parcel"][pid])]
    if bad:
        print(f"DIFFERS with south_fulton verified: {', '.join(bad)}")
        return 1
    print(f"with south_fulton verified, the {len(draft)} parcels of other jurisdictions are "
          "identical to the 28d3205 record (results, inserts, notes; SQL apart from zoning's "
          "own stored-zoning read)")
    return 0


def main() -> int:
    from hazards_helpers import disabled
    with disabled():
        return _compare()


if __name__ == "__main__":
    sys.exit(main())
