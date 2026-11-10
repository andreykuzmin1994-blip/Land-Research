"""Per-run report facts: what each ranked parcel's brief and ``ranked.csv``
row showed, kept so a pursue / pass / maybe mark can later be read against
the facts on screen when it was made (reviews/35_screener_lead_layer).

Written to <run output dir>/facts.json after every report check and the
lead grade. ``decide.py`` copies a parcel's record into
``inputs/decision_facts.jsonl`` when a mark is recorded.

This is NOT the evaluation's feature snapshot (snapshot.py), and it must
never become one: these are live-only facts the frozen evaluation never
measured (fit, pond, soils, frontage, earthwork, the grade), plus the
score and rank themselves. Nothing in ``autoresearch/`` reads this file,
and turning decisions into evaluation labels stays the human
``--new-baseline`` step, from ``features.json`` alone.

Only names on the allow-list are copied: never the geometry, the owner's
name or mailing address, the site address, the flags (they name sibling
parcels) or the decision.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import snapshot

FILE_NAME = "facts.json"
TEMP_NAME = ".facts.json.tmp"   # same (gitignored) folder, so os.replace stays atomic
STORE_NAME = "decision_facts.jsonl"
SCHEMA = 1

COLUMNS = (
    "rank", "score", "fixed_score", "learned_score", "motivation_score", "jurisdiction",
    "acres", "usable_after_slope", "entitlement", "zoning", "flu", "exit_miles", "rail_ft",
    "industrial_within_1mi", "flood_pct", "wetland_pct", "steep_pct", "relief_ft",
    "earthwork_cy", "mean_cut_fill_ft", "largest_open_acres", "envelope_fit",
    "envelope_building_sf", "detention_pct", "pond_fit", "soil_hsg", "soil_hydric_pct",
    "soil_poorly_drained_pct", "soil_dwellings_limited_pct", "frontage_ft", "frontage_road",
    "frontage_limited_ft", "frontage_private_ft", "owner_type", "owner_state", "years_held",
    "last_sale_date", "last_sale_price", "land_value", "opportunity_zone",
    "lead_grade", "lead_found", "lead_not_checked",
)
NEVER = ("geometry", "owner", "owner_mailing", "site_address", "flags", "decision", "maps_link")


def take(ranked: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    """Copy the allow-listed report facts of each ranked parcel, as plain
    values. Call after every report check and the grade."""
    bad = [0]
    records = [{"parcel_id": p["parcel_id"],
                "facts": {c: snapshot._plain(p.get(c), bad) for c in COLUMNS}}
               for p in ranked]
    return {"meta": {**meta, "schema": SCHEMA, "records": len(records),
                     "nonfinite_as_null": bad[0], "pool": "ranked survivors",
                     "kind": "report facts (live-only; not evaluation features)"},
            "records": records}


def clear(out_dir: Path) -> None:
    """Remove an earlier run's facts before the reports are rewritten, so a
    failed re-run never leaves old facts beside a new ranked.csv."""
    (out_dir / FILE_NAME).unlink(missing_ok=True)


def write(out_dir: Path, facts: dict[str, Any]) -> Path:
    """Write atomically (temp name in the same folder, then os.replace).
    Standard JSON only: allow_nan=False."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp, path = out_dir / TEMP_NAME, out_dir / FILE_NAME
    tmp.write_text(json.dumps(facts, allow_nan=False, indent=1))
    os.replace(tmp, path)
    return path


def records_for(run_dir: Path, pid: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    """(meta, the parcel's records, problem). Never raises: facts are an
    extra, so an absent or unreadable file is reported, not refused."""
    path = run_dir / FILE_NAME
    if not path.is_file():
        return None, [], f"{run_dir.name} has no {FILE_NAME}"
    try:
        data = json.loads(path.read_text())
        meta, records = data["meta"], data["records"]
        found = [r for r in records if isinstance(r, dict) and r.get("parcel_id") == pid]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, [], f"{path.name} is unreadable ({exc})"
    return meta, found, "" if found else "it isn't in this run's facts (only ranked parcels are)"
