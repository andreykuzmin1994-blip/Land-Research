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

from . import (arcgis, checks, detention, frontage, layers, owner_clusters, report, scoring,
               screens, sitefit, snapshot, soils)
from .geo import expand, tile_envelopes

HERE = Path(__file__).resolve().parent
DECISIONS_FILE = HERE / "inputs" / "decisions.csv"
DECISIONS_HEADER = ["parcel_id", "decision", "date", "note"]
# "maybe" is a mark only: no score, flag, sort or screen reacts to it.
DECISION_VALUES = ("pursue", "pass", "maybe", "listed")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def stop(what: str, exc: Exception) -> int:
    """A source the screen can't do without failed: say which, and stop."""
    log(f"  ! {what} is unavailable ({exc}).")
    log("Stopping: re-run later (finished downloads are cached).")
    return 1


def load_decisions() -> dict[str, dict[str, str]]:
    """Your pursue / pass / maybe / listed marks, keyed by parcel id (the
    last row for an id wins). utf-8-sig: a spreadsheet's byte-order mark
    would otherwise hide the parcel_id column and drop every decision.
    Header names are stripped, as decide.py strips them when it checks and
    appends, so a hand-edited "parcel_id, decision, date, note" is read
    rather than silently ignored."""
    if not DECISIONS_FILE.exists():
        DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DECISIONS_FILE, "w", newline="") as fh:
            csv.writer(fh).writerow(DECISIONS_HEADER)
        return {}
    out = {}
    with open(DECISIONS_FILE, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames:
            reader.fieldnames = [(h or "").strip() for h in reader.fieldnames]
        for row in reader:
            pid = (row.get("parcel_id") or "").strip()
            decision = (row.get("decision") or "").strip().lower()
            if pid and decision in DECISION_VALUES:
                out[pid] = {"decision": decision, "date": (row.get("date") or "").strip(),
                            "note": (row.get("note") or "").strip()}
    return out


def reason_key(reason: str) -> str:
    """Group reject reasons that differ only by numbers ("45% in ...")."""
    key = re.sub(r"^only [\d.]+ usable acres.*", "too few usable acres after constraints", reason)
    key = re.sub(r"^[\d.]+% ", "", key)
    key = re.sub(r"^[\d.]+ mi from", "too far from", key)
    return key.split(" (")[0]


def fit_settings(cfg: dict[str, Any], table: str = "fit") -> tuple[bool, int]:
    """[fit] (or [detention]) enabled (default true) and max_parcels (default
    0 = no cap; the top N by rank are checked, the rest read "not checked").
    Deterministic: there is no time budget."""
    fit = cfg.get(table, {}) or {}
    enabled = fit.get("enabled", True)
    cap = fit.get("max_parcels", 0)
    if not isinstance(enabled, bool):
        raise ValueError(f"[{table}] enabled must be true or false")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        raise ValueError(f"[{table}] max_parcels must be a whole number, 0 or more")
    return enabled, cap


def attach_fits(ranked: list[dict[str, Any]], enabled: bool, cap: int,
                notes: list[str]) -> None:
    """Fit each ranked parcel (in rank order) and attach the report fields.
    Called only after scores, flags and the sort."""
    reasons: collections.Counter = collections.Counter()
    started = time.monotonic()
    if enabled and ranked:
        log(f"Building fit for {len(ranked) if not cap else min(cap, len(ranked)):,} parcels")
    for i, p in enumerate(ranked, 1):
        if not enabled:
            f = sitefit.not_checked("fit switched off in settings")
        elif cap and i > cap:
            f = sitefit.not_checked(f"outside the top {cap} ([fit] max_parcels)")
        else:
            f = sitefit.fit_parcel(p["geometry"], p.get("_open_ground"), p.get("constrained_pct"))
            if i % 100 == 0:
                log(f"  fit {i:,}/{len(ranked) if not cap else min(cap, len(ranked)):,}")
        f.map_mismatch = bool(p.get("acreage_mismatch") or checks.too_small_for_slope(p))
        if f.state == sitefit.NOT_CHECKED:
            reasons[f.reason.split(" (")[0]] += 1
        p.update(f.fields())
    if enabled and ranked:
        log(f"  fit done ({time.monotonic() - started:.1f} s)")
    for why, n in sorted(reasons.items()):
        notes.append(f"Building fit not checked for {n} of {len(ranked)} ranked parcels: {why}")


def attach_ponds(ranked: list[dict[str, Any]], enabled: bool, cap: int,
                 notes: list[str]) -> None:
    """Place the detention pond on each ranked parcel (in rank order) beside
    its F6 envelope and attach the report fields. Called only after scores,
    flags, the sort and attach_fits."""
    reasons: collections.Counter = collections.Counter()
    states: collections.Counter = collections.Counter()
    for i, p in enumerate(ranked, 1):
        if not enabled:
            d = detention.not_checked("detention switched off in settings")
        elif cap and i > cap:
            d = detention.not_checked(f"outside the top {cap} ([detention] max_parcels)")
        else:
            d = detention.place_pond(p["geometry"], p.get("_open_ground"),
                                     p.get("constrained_pct"), p.get("_envelope_detail"),
                                     p.get(sitefit.ENVELOPE_KEY))
        if d.state == detention.NOT_CHECKED:
            reasons[d.reason.split(" (")[0]] += 1
        else:
            states[d.state] += 1
        p.update(d.fields())
    if states:
        notes.append(f"Detention pond (rough allowance, no hydrology): pond fits on "
                     f"{states[detention.FITS]} of {len(ranked)} ranked parcels, no pond room "
                     f"found on {states[detention.NONE_FOUND]}")
    for why, n in sorted(reasons.items()):
        notes.append(f"Detention pond not checked for {n} of {len(ranked)} ranked parcels: {why}")


def attach_soils(ranked: list[dict[str, Any]], enabled: bool, cap: int, workers: int,
                 notes: list[str]) -> None:
    """Ask USDA Soil Data Access about each ranked parcel (the top ``cap``
    by rank; 0 = all) and attach the report fields. Called only after
    scores, flags, the sort, the fit and the pond."""
    todo = ranked if not enabled or not cap else ranked[:cap]
    results: list[soils.Soils] = []
    if enabled and todo:
        log(f"Soils from USDA Soil Data Access for {len(todo):,} parcels")
        started = time.monotonic()
        results = soils.check_all([p.get("geometry") for p in todo], workers)
        log(f"  soils done ({time.monotonic() - started:.1f} s)")
    reasons: collections.Counter = collections.Counter()
    checked = 0
    for i, p in enumerate(ranked):
        if not enabled:
            s = soils.not_checked("soils switched off in settings")
        elif i >= len(results):
            s = soils.not_checked(f"outside the top {cap} ([soils] max_parcels)")
        else:
            s = results[i]
        if s.state == soils.NOT_CHECKED:
            reasons[s.reason.split(" (")[0]] += 1
        else:
            checked += 1
        p.update(s.fields())
    if checked:
        notes.append(f"Soils (USDA SSURGO via Soil Data Access, map-unit level, not a "
                     f"geotechnical report): checked for {checked} of {len(ranked)} ranked parcels")
    for why, n in sorted(reasons.items()):
        notes.append(f"Soils not checked for {n} of {len(ranked)} ranked parcels: {why}")
    if reasons and any(w.startswith(soils.SERVICE_FAILED) or "failures in a row" in w
                       for w in reasons):
        notes.append("SOIL SERVICE FAILURES: USDA Soil Data Access is down nightly about "
                     "00:00-02:00 Mountain; re-run later for those parcels.")


def attach_frontage(ranked: list[dict[str, Any]], enabled: bool, cap: int, workers: int,
                    notes: list[str]) -> None:
    """Ask GDOT's road centrelines about each ranked parcel (the top ``cap``
    by rank; 0 = all) and attach the report fields. Called only after
    scores, flags, the sort, the fit, the pond and the soils."""
    todo = ranked if not enabled or not cap else ranked[:cap]
    results: list[frontage.Frontage] = []
    if enabled and todo:
        log(f"Road frontage from GDOT road centrelines for {len(todo):,} parcels")
        started = time.monotonic()
        results = frontage.check_all([p.get("geometry") for p in todo], workers)
        log(f"  frontage done ({time.monotonic() - started:.1f} s)")
    reasons: collections.Counter = collections.Counter()
    states: collections.Counter = collections.Counter()
    for i, p in enumerate(ranked):
        if not enabled:
            f = frontage.not_checked("frontage switched off in settings")
        elif i >= len(results):
            f = frontage.not_checked(f"outside the top {cap} ([frontage] max_parcels)")
        else:
            f = results[i]
        if f.state == frontage.NOT_CHECKED:
            reasons[f.reason.split(" (")[0]] += 1
        else:
            states[f.state] += 1
        p.update(f.fields())
    if states:
        notes.append(f"Road frontage (GDOT road centrelines, parcel line within "
                     f"{frontage.BUFFER_FT:.0f} ft; curb cuts not checked): frontage on "
                     f"{states[frontage.CHECKED]} of {len(ranked)} ranked parcels, "
                     f"{frontage.NONE_LABEL} on {states[frontage.NONE]}")
        if not states[frontage.CHECKED] and states[frontage.NONE] >= 3:
            notes.append("No ranked parcel came back with road frontage. If that's unexpected, "
                         "re-run with --refresh before relying on it.")
    for why, n in sorted(reasons.items()):
        notes.append(f"Road frontage not checked for {n} of {len(ranked)} ranked parcels: {why}")
    if any(w.startswith(frontage.SERVICE_FAILED) or "failures in a row" in w for w in reasons):
        notes.append("ROAD SERVICE FAILURES: the GDOT road centreline service did not answer "
                     "for some parcels; re-run later for those parcels.")


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
    try:
        gap_ft = owner_clusters.gap_ft_setting(cfg)
    except ValueError as exc:
        ap.error(f"{args.settings}: {exc}")
    arcgis.cache_days = 0 if args.refresh else float(run_cfg.get("cache_days", 7))
    county = importlib.import_module(f"screener.{run_cfg['county']}")
    today = date.today().isoformat()
    out_dir = args.out or HERE / "output" / f"{run_cfg['county']}-{today}"
    funnel: list[tuple[str, int]] = []
    notes: list[str] = []
    earthwork: dict[int, dict[str, Any] | None] = {}
    try:
        fit_on, fit_max = fit_settings(cfg)
        pond_on, pond_max = fit_settings(cfg, "detention")
        soils_on, soils_max, soils_workers = soils.settings(cfg)
        front_on, front_max, front_workers = frontage.settings(cfg)
    except ValueError as exc:
        ap.error(f"{args.settings}: {exc}")

    log(f"{county.NAME} County: fetching {area['min_acres']}-{area['max_acres']} acre parcels")
    try:
        parcels = county.fetch_parcels(area["min_acres"], area["max_acres"],
                                       set(area.get("jurisdictions") or []))
    except arcgis.ArcGISError as exc:
        return stop("the county parcel layer", exc)
    parcels.sort(key=lambda p: p["parcel_id"])
    # Same-owner groups over every fetched parcel, before any screen and
    # before the trial limit, so a trial run gives the same counts. The
    # result stays in this local variable: nothing goes on p until the
    # parcels are scored and sorted (the evaluation never measures it).
    started = time.monotonic()
    owner_groups = owner_clusters.build(parcels, gap_ft, area["min_acres"], area["max_acres"])
    log(f"  same-owner groups: {len(owner_groups.groups):,} "
        f"({time.monotonic() - started:.1f} s)")
    notes.extend(owner_clusters.summary_notes(owner_groups))
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
    funnel.append(("not built on, not government / developer / association / exempt", len(live)))
    log(f"  {len(live):,} pass the ownership and land-only screens")
    if not live:
        log("nothing left to screen")
        return 1

    log("Deed history and site records (county CAMA extract)")
    try:
        cama = county.load_cama(HERE / "cache", {p["parcel_id"]: p["owner"] for p in live},
                                refresh=args.refresh)
    except arcgis.ArcGISError as exc:
        return stop("the county deed file", exc)
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
        except arcgis.ArcGISError as exc:
            # A missing layer would read as "0% floodplain" etc., so stop
            # rather than publish unchecked parcels as clean.
            return stop(layer.name, exc)
        log(f"  {len(idx[key]):,} features")
    log("Industrial parcels (context)")
    try:
        industrial = county.fetch_industrial_points()
    except arcgis.ArcGISError as exc:
        return stop("the county parcel layer (industrial parcels)", exc)
    # No features at all is normal for some layers (no Opportunity Zone
    # nearby) but reads as "clear" or "far" for every parcel, so say so.
    empty = [layers.LAYERS[key].name for key in idx if not len(idx[key])]
    if not industrial:
        empty.append("county industrial parcels")
    if empty:
        log(f"  ! no features near these parcels: {'; '.join(empty)}")
        notes.append(f"No features came back near these parcels from: {'; '.join(empty)}. "
                     "Every parcel reads as clear of (or far from) them; if that's unexpected, "
                     "re-run with --refresh before relying on it.")

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
    # Kept and flagged rather than screened out: the tax record says there is
    # land, and the frozen evaluation knows no such screen (screens.py).
    tiny = sum(1 for p in survivors if checks.too_small_for_slope(p))
    if tiny:
        log(f"  ! {tiny:,} of {len(survivors):,} parcels have under one 10 m lidar pixel of open "
            "ground on the parcel map")
        notes.append(f"{tiny} of {len(survivors)} parcels that pass the map screens have under "
                     "one 10 m lidar pixel (100 m²) of open ground on the county parcel map, so "
                     "their map checks can't be trusted and slope was not checked (each is "
                     "flagged). The map shape is the problem, not a failed check: compare the "
                     "county parcel map with the tax record.")

    slope_on = bool(run_cfg.get("slope", True)) and not args.no_slope
    if slope_on and survivors:
        log(f"Slope from USGS lidar for {len(survivors):,} parcels")
        estimates = checks.slope_checks(survivors, int(run_cfg.get("workers", 6)),
                                        lambda d, n: log(f"  slope {d:,}/{n:,}"))
        # Keyed by the parcel object, not its id (ids aren't guaranteed
        # unique): the estimate stays off p until scoring is done.
        earthwork = {id(p): e for p, e in zip(survivors, estimates)}
        slope_failed = sum(1 for p in survivors if p.get("slope_error"))
        if slope_failed:
            log(f"  ! slope check failed for {slope_failed:,} of {len(survivors):,} parcels")
            notes.append(f"SLOPE CHECK FAILED for {slope_failed} of {len(survivors)} parcels: "
                         "the steep-ground screen was not applied to them (each is flagged "
                         "\"slope check failed\"). Re-run later.")
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

    # The feature snapshot (future labels' features) is copied now, before
    # scoring, and stays in this local variable: nothing is put on p.
    snap = snapshot.take(survivors, snapshot.run_meta(
        county.NAME, today, args.limit, slope_on, args.settings.read_bytes()))
    if snap["meta"]["nonfinite_as_null"]:
        notes.append(f"{snap['meta']['nonfinite_as_null']} non-finite values (NaN or infinity) "
                     f"were written as null in {snapshot.FILE_NAME}")
    for p in survivors:
        p.update(scoring.score(p))
        p["flags"] = screens.flags(p)
        if checks.too_small_for_slope(p):
            p["flags"].append(f"{checks.SLOPE_SKIPPED}: slope not checked, and the map checks "
                              "(floodplain, wetlands, usable acres) can't be trusted")
        d = decisions.get(p["parcel_id"])
        p["decision"] = d["decision"] if d else ""
    survivors.sort(key=lambda p: (-p["score"], p["parcel_id"]))
    for i, p in enumerate(survivors, 1):
        p["rank"] = i
    # The cut/fill estimate is a live-only fact the frozen evaluation never
    # measures, so it is attached only now, after scores, flags and the sort,
    # for the reports alone (rank.py and scoring.py never see it).
    for p in survivors:
        p.update(checks.earthwork_fields(earthwork.get(id(p))))
    # The building fit is live-only too: computed only now, after the sort,
    # and attached for the reports alone (sitefit.py).
    attach_fits(survivors, fit_on, fit_max, notes)
    # The detention allowance is live-only too, placed beside the fitted
    # envelope (detention.py). Runs with and without the slope check.
    attach_ponds(survivors, pond_on, pond_max, notes)
    # SSURGO soils are live-only too: asked for only now, after the sort,
    # for the reports alone (soils.py). One SDA request per ranked parcel.
    attach_soils(survivors, soils_on, soils_max, soils_workers, notes)
    # Road frontage is live-only too: GDOT centrelines asked for only now,
    # after the sort, for the reports alone (frontage.py). Not a map layer:
    # it stays out of map_checks and everything build_eval copies.
    attach_frontage(survivors, front_on, front_max, front_workers, notes)
    # Same-owner lines, now that every sibling's final status is known.
    # Only p["flags"] (reports) gains them; scores and order are done.
    ranked_at = {p["parcel_id"]: p["rank"] for p in survivors}
    out_why = {p["parcel_id"]: reason_key(p["reject_reasons"][0]) for p in rejected}
    mismatch = {p["parcel_id"] for p in survivors + rejected if p.get("acreage_mismatch")}

    def status_of(pid: str) -> str:
        if pid in ranked_at:
            return f"ranked #{ranked_at[pid]}"
        if pid in out_why:
            return f"screened out: {out_why[pid]}"
        return "not screened in this trial run"
    for p in survivors:
        p["flags"].extend(owner_clusters.describe(p["parcel_id"], owner_groups, status_of,
                                                  mismatch.__contains__))
    funnel.append(("ranked", len(survivors)))

    reasons = collections.Counter(reason_key(p["reject_reasons"][0]) for p in rejected)
    unknown = collections.Counter(p["jurisdiction"] for p in survivors
                                  if p.get("entitlement") == "unknown" and not p.get("zoning_problem"))
    if unknown:
        where = ", ".join(f"{j} {n}" for j, n in unknown.most_common())
        notes.append(f"{sum(unknown.values())} ranked parcels have no machine-readable zoning "
                     f"({where}): check with the city")
    failed = collections.Counter(p["zoning_problem"] for p in survivors if p.get("zoning_problem"))
    for problem, n in failed.most_common():
        notes.append(f"ZONING CHECK FAILED for {n} parcels: {problem}. Re-run later.")
    pursued_out = [p for p in rejected if decisions.get(p["parcel_id"], {}).get("decision") == "pursue"]
    for p in pursued_out:
        notes.append(f"A parcel you're pursuing was screened out: {p['parcel_id']} "
                     f"({'; '.join(p['reject_reasons'])})")
    for p in rejected:
        p["decision"] = decisions.get(p["parcel_id"], {}).get("decision", "")
    notes.append("Wetlands come from the 1980s National Wetlands Inventory and miss small "
                 "and forested wetlands; floodplain from FEMA NFHL (Mar 2026 snapshot)")
    summary = {"county": county.NAME, "run_date": today, "funnel": funnel,
               "reject_reasons": reasons.most_common(), "notes": notes}
    snapshot.clear(out_dir)  # never pair a new ranked.csv with an old snapshot
    report.write_all(out_dir, survivors, rejected, summary, int(run_cfg.get("briefs", 25)))
    snapshot.write(out_dir, snap)
    log(f"Done: {len(survivors):,} ranked parcels -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
