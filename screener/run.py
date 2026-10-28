"""Run the land screen.

    python -m screener.run                 # full run with settings.toml
    python -m screener.run --limit 200     # quick trial on 200 parcels
    python -m screener.run --no-slope      # skip the lidar slope check
"""

from __future__ import annotations

import argparse
import collections
import csv
import importlib
import re
import sys
import time
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from . import arcgis, checks, layers, rank, report, screens
from .geo import expand, tile_envelopes

HERE = Path(__file__).resolve().parent
DECISIONS_FILE = HERE / "inputs" / "decisions.csv"
DECISIONS_HEADER = ["parcel_id", "decision", "date", "note"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_decisions() -> dict[str, dict[str, str]]:
    """Your pursue / pass / listed marks, keyed by parcel id."""
    if not DECISIONS_FILE.exists():
        DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DECISIONS_FILE, "w", newline="") as fh:
            csv.writer(fh).writerow(DECISIONS_HEADER)
        return {}
    out = {}
    with open(DECISIONS_FILE, newline="") as fh:
        for row in csv.DictReader(fh):
            pid = (row.get("parcel_id") or "").strip()
            decision = (row.get("decision") or "").strip().lower()
            if pid and decision in {"pursue", "pass", "listed"}:
                out[pid] = {"decision": decision, "date": (row.get("date") or "").strip(),
                            "note": (row.get("note") or "").strip()}
    return out


def reason_key(reason: str) -> str:
    """Group reject reasons that differ only by numbers ("45% in ...")."""
    key = re.sub(r"^only [\d.]+ usable acres.*", "too few usable acres after constraints", reason)
    key = re.sub(r"^[\d.]+% ", "", key)
    return key.split(" (")[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", type=Path, default=HERE / "settings.toml")
    ap.add_argument("--limit", type=int, help="only screen the first N parcels (trial runs)")
    ap.add_argument("--no-slope", action="store_true", help="skip the lidar slope check")
    ap.add_argument("--refresh", action="store_true", help="ignore cached map data")
    ap.add_argument("--out", type=Path, help="output folder (default screener/output/<county>-<date>)")
    args = ap.parse_args(argv)

    cfg = tomllib.loads(args.settings.read_text())
    run_cfg, area, scr = cfg["run"], cfg["area"], cfg["screens"]
    arcgis.cache_days = 0 if args.refresh else float(run_cfg.get("cache_days", 7))
    county = importlib.import_module(f"screener.{run_cfg['county']}")
    today = date.today().isoformat()
    out_dir = args.out or HERE / "output" / f"{run_cfg['county']}-{today}"
    funnel: list[tuple[str, int]] = []
    notes: list[str] = []

    log(f"{county.NAME} County: fetching {area['min_acres']}-{area['max_acres']} acre parcels")
    parcels = county.fetch_parcels(area["min_acres"], area["max_acres"],
                                   set(area.get("jurisdictions") or []))
    parcels.sort(key=lambda p: p["parcel_id"])
    if args.limit:
        parcels = parcels[:args.limit]
        notes.append(f"trial run limited to {args.limit} parcels")
    funnel.append((f"{area['min_acres']}-{area['max_acres']} acres in the chosen jurisdictions", len(parcels)))
    log(f"  {len(parcels):,} parcels")

    decisions = load_decisions()
    rejected: list[dict[str, Any]] = []
    live = []
    for p in parcels:
        why = screens.attribute_rejects(p, scr, decisions)
        if why:
            p["reject_reasons"] = why
            rejected.append(p)
        else:
            live.append(p)
    funnel.append(("not built on, not government / developer / exempt", len(live)))
    log(f"  {len(live):,} pass the ownership and land-only screens")
    if not live:
        log("nothing left to screen")
        return 1

    log("Deed history and site records (county CAMA extract)")
    cama = county.load_cama(HERE / "cache", {p["parcel_id"] for p in live})
    for p in live:
        p.update(cama.get(p["parcel_id"], {}))

    tiles = tile_envelopes([p["geometry"].bounds for p in live])
    xs = [b for p in live for b in (p["geometry"].bounds[0], p["geometry"].bounds[2])]
    ys = [b for p in live for b in (p["geometry"].bounds[1], p["geometry"].bounds[3])]
    region = expand((min(xs), min(ys), max(xs), max(ys)), 0.15)
    idx = {}
    for key, layer in layers.LAYERS.items():
        log(f"Map layer: {layer.name}")
        try:
            idx[key] = layers.fetch(layer, tiles, region)
            log(f"  {len(idx[key]):,} features")
        except arcgis.ArcGISError as exc:
            idx[key] = layers.Index([])
            notes.append(f"{layer.name} was unavailable this run; its check was skipped")
            log(f"  ! unavailable: {exc}")
    log("Industrial parcels (context)")
    industrial = county.fetch_industrial_points()

    log("Running map checks")
    checks.map_checks(live, idx, industrial, scr["contamination_buffer_ft"])
    log("Zoning and future land use")
    checks.zoning_checks(live, county.ZONING_SOURCES,
                         lambda group: tile_envelopes([p["geometry"].bounds for p in group]))

    survivors = []
    for p in live:
        p["usable_after_slope"] = p["usable_acres"]
        why = screens.site_rejects(p, scr)
        if why:
            p["reject_reasons"] = why
            rejected.append(p)
        else:
            survivors.append(p)
    funnel.append(("pass floodplain, wetlands, protected-land and size screens", len(survivors)))
    log(f"  {len(survivors):,} pass the map screens")

    if run_cfg.get("slope", True) and not args.no_slope and survivors:
        log(f"Slope from USGS lidar for {len(survivors):,} parcels")
        checks.slope_checks(survivors, int(run_cfg.get("workers", 6)),
                            lambda d, n: log(f"  slope {d:,}/{n:,}"))
        kept = []
        for p in survivors:
            p["usable_after_slope"] = screens.usable_after_slope(p)
            why = screens.site_rejects(p, scr)
            if why:
                p["reject_reasons"] = why
                rejected.append(p)
            else:
                kept.append(p)
        survivors = kept
        funnel.append(("pass the slope screen", len(survivors)))
    else:
        notes.append("slope check skipped: steepness is not in the scores or screens")

    for p in survivors:
        p.update(rank.score(p))
        p["flags"] = screens.flags(p)
        d = decisions.get(p["parcel_id"])
        p["decision"] = d["decision"] if d else ""
    survivors.sort(key=lambda p: (-p["score"], p["parcel_id"]))
    for i, p in enumerate(survivors, 1):
        p["rank"] = i
    funnel.append(("ranked", len(survivors)))

    reasons = collections.Counter(reason_key(p["reject_reasons"][0]) for p in rejected)
    unknown_zoning = sum(1 for p in survivors if p.get("entitlement") == "unknown")
    if unknown_zoning:
        notes.append(f"{unknown_zoning} ranked parcels have no machine-readable zoning "
                     "(College Park, Hapeville, Palmetto, Chattahoochee Hills): check with the city")
    notes.append("Wetlands come from the 1980s National Wetlands Inventory and miss small "
                 "and forested wetlands; floodplain from FEMA NFHL (Mar 2026 snapshot)")
    summary = {"county": county.NAME, "run_date": today, "funnel": funnel,
               "reject_reasons": reasons.most_common(), "notes": notes}
    report.write_all(out_dir, survivors, rejected, summary, int(run_cfg.get("briefs", 25)))
    log(f"Done: {len(survivors):,} ranked parcels -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
