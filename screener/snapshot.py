"""Per-run feature snapshot: what the evaluation would see for each ranked
parcel, so your pursue / pass / maybe marks can later become labels.

Written to <run output dir>/features.json for the ranked survivors (the
analog of the evaluation pool; a rejected parcel's map features are
partial). The feature list is build_eval.FEATURES itself, imported, so it
can't drift from the evaluation's. Nothing here reads or writes the
evaluation files, and nothing here is put on a parcel: run.main takes the
snapshot before scoring and keeps it in a local variable, so rank.py and
scoring.py never see it.

Phase A only. Turning decisions into evaluation labels (Phase B) is a human
--new-baseline step; see reviews/34_screener_quick_wins.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .autoresearch.build_eval import BANNED_AS_NONE, FEATURES

FILE_NAME = "features.json"
TEMP_NAME = ".features.json.tmp"  # same (gitignored) folder, so os.replace stays atomic
SCHEMA = 1
CODE_DIR = Path(__file__).resolve().parent


def _plain(v: Any, bad: list[int]) -> Any:
    """A JSON-safe copy of one value. NaN / inf become None and are counted
    in bad[0]; unknown types become their str()."""
    if v is None or isinstance(v, (bool, str, int)):
        return v
    if isinstance(v, float):
        if math.isfinite(v):
            return v
        bad[0] += 1
        return None
    if isinstance(v, (list, tuple)):
        return [_plain(x, bad) for x in v]
    return str(v)


def code_commit() -> str | None:
    """The screener's git commit, or None if git doesn't answer."""
    try:
        out = subprocess.run(["git", "-C", str(CODE_DIR), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and len(sha) == 40 else None


def run_meta(county: str, run_date: str, limit: int | None, slope: bool,
             settings_bytes: bytes) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "county": county,
        "run_date": run_date,
        "run_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "limit": limit,
        "slope": slope,
        "settings_sha256": hashlib.sha256(settings_bytes).hexdigest(),
        "code_commit": code_commit(),
        "pool": "ranked survivors",
    }


def take(parcels: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    """Copy the evaluation features of each parcel, as plain values, now.

    Only names on the allow-list are read: never the geometry, owner name or
    mailing, earthwork, or a decision. The BANNED_AS_NONE fields (blank in
    every evaluation record) are kept apart under "live_only", so Phase B
    can blank them as the evaluation does without losing them."""
    bad = [0]
    records = [{
        "parcel_id": p["parcel_id"],
        "lon": _plain(p.get("lon"), bad),
        "lat": _plain(p.get("lat"), bad),
        "features": {f: _plain(p.get(f), bad) for f in FEATURES},
        "live_only": {f: _plain(p.get(f), bad) for f in BANNED_AS_NONE},
    } for p in parcels]
    return {"meta": {**meta, "records": len(records), "nonfinite_as_null": bad[0]},
            "records": records}


def write(out_dir: Path, snap: dict[str, Any]) -> Path:
    """Write the snapshot atomically (temp name in the same folder, then
    os.replace). Standard JSON only: allow_nan=False."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp, path = out_dir / TEMP_NAME, out_dir / FILE_NAME
    tmp.write_text(json.dumps(snap, allow_nan=False, indent=1))
    os.replace(tmp, path)
    return path
