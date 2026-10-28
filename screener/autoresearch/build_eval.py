"""Build the frozen evaluation set the AutoResearch loop is scored on.

A human runs this between runs, never the agent:

    python -m screener.autoresearch.build_eval

The question the metric asks: standing in January 2016, knowing only what
public data showed then, would the ranking have put the land that the
owners sold to industrial developers, who built on it by 2025, near the
top?

Pool:      Fulton 5-50 acre parcels in the 2016 tax digest that pass the live
           tool's ownership and land-only screens (screens.attribute_rejects),
           minus owners whose names read like industrial developer vehicles
           (that land was already off the market).
Positives: pool parcels that the 2016 owner SOLD in 2016 or later at a real
           price (>= MIN_PRICE in the county deed file; right-of-way deeds and
           sales to a development authority don't count) AND that are at
           least half covered by a 2025-digest parcel carrying $1M+ of
           industrial improvements.
Left out:  parcels built on by 2025 without such a sale (owner-users,
           corporate successors, replatted ids) are dropped from the pool,
           not counted as misses.
Features:  the 2016 digest + deed history before 2016 + layers that change
           slowly (flood, 1980s wetlands, easements, exits, main-line rail).
           Blank (None) because today's values were often changed FOR the
           project or weren't knowable in 2016: zoning, utilities, road
           type, slope (2018-19 lidar shows graded pads as flat),
           contamination listings, Opportunity Zones (designated 2018),
           and jurisdiction (Fulton re-coded its tax districts since).
Split:     ~5 km grid cells, stratified: cells are taken in a fixed
           pseudo-random order into the sealed test set until it holds
           about a quarter of the positives (positives cluster in a few
           corridors, so a plain random split is lopsided). Each record
           keeps its cell as "block" for the clustered bootstrap.

Writes screener/autoresearch/data/fulton_2016_features.json.gz and
fulton_2016_labels.json. Records carry no owner names, parcel ids or
coordinates.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
import tomllib
from dataclasses import replace
from datetime import date
from pathlib import Path

from shapely import unary_union

from .. import arcgis, checks, fulton, layers, screens
from ..geo import LocalProjection, esri_to_shape, expand, tile_envelopes
from ..layers import Index

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SNAPSHOTS = "https://services1.arcgis.com/AQDHTHDrZzfsFsB5/arcgis/rest/services"
SNAP_AS_OF = f"{SNAPSHOTS}/Tax_Parcels2016/FeatureServer/0"
SNAP_LABEL = f"{SNAPSHOTS}/Tax_Parcels_2025/FeatureServer/0"
AS_OF = date(2016, 1, 1)
LABEL_YEAR = 2025
MIN_PRICE = 50_000
TEST_SHARE = 0.25

# Industrial land-use codes in the Fulton digest (warehouse, manufacturing,
# truck terminal...). 332/335/339 are left out: they also cover parking,
# self-storage, auto auctions and hospitals.
INDUSTRIAL_LU = ("390", "391", "394", "395", "397", "398", "399", "401", "419",
                 "420", "443", "485")
BUILT_WHERE = ("ImprAppr >= 1000000 AND (ClassCode LIKE 'I%' OR LUCode IN ("
               + ",".join(f"'{c}'" for c in INDUSTRIAL_LU) + "))")

# The only parcel fields the ranking sees (see the module docstring).
FEATURES = [
    "acres", "usable_acres", "usable_after_slope", "flood_pct", "floodway_pct",
    "wetland_pct", "water_pct", "easement_pct", "protected_pct", "constrained_pct",
    "exit_miles", "rail_ft", "rail_owner", "industrial_within_1mi",
    "land_value", "class_code", "land_use_code", "owner_type", "owner_state",
    "owner_po_box", "years_held", "sales_count", "inherited", "distress_owned",
    "last_sale_price",
]
BANNED_AS_NONE = ["zoning", "flu", "entitlement", "entitlement_note", "water", "sewer",
                  "road", "steep_pct", "relief_ft", "mean_slope_pct", "contamination",
                  "opportunity_zone", "jurisdiction"]


def key_for(parcel_id: str) -> str:
    return hashlib.sha256(f"fulton-{AS_OF.year}:{parcel_id}".encode()).hexdigest()[:16]


def cell_for(lon: float, lat: float) -> str:
    return f"{math.floor(lon / 0.05)}:{math.floor(lat / 0.05)}"


def stratified_test_cells(cells: dict[str, list[int]], total_pos: int, total_pool: int) -> set[str]:
    """cells: cell -> [pool count, positive count]. Fixed hash order, so the
    split is reproducible; a cell joins the test set only if it keeps the
    test set near TEST_SHARE of the positives and under 30% of the pool."""
    target = round(TEST_SHARE * total_pos)
    test, tp, tn = set(), 0, 0
    for cell in sorted(cells, key=lambda c: hashlib.sha256(c.encode()).hexdigest()):
        n, pos = cells[cell]
        if tp + pos <= target + 1 and tn + n <= 0.3 * total_pool:
            test.add(cell)
            tp, tn = tp + pos, tn + n
    return test


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    cfg = tomllib.loads((HERE.parent / "settings.toml").read_text())
    arcgis.cache_days = 30
    juris = set(cfg["area"]["jurisdictions"])
    scr = cfg["screens"]

    log(f"{AS_OF.year} digest: 5-50 acre parcels")
    pool = fulton.fetch_parcels(5, 50, juris, url=SNAP_AS_OF)
    pool = [p for p in pool
            if not screens.attribute_rejects(p, scr, {}) and not p["industrial_named"]]
    log(f"  {len(pool):,} parcels pass the ownership and land-only screens")

    log("Deed history")
    owners = {p["parcel_id"]: p["owner"] for p in pool}
    sales, _ = fulton.read_cama(HERE.parent / "cache", set(owners))
    cama = fulton.load_cama(HERE.parent / "cache", owners, as_of=AS_OF)
    for p in pool:
        p.update({k: v for k, v in cama[p["parcel_id"]].items()
                  if k not in ("water", "sewer", "road", "topography", "fronting")})

    tiles = tile_envelopes([p["geometry"].bounds for p in pool])
    xs = [b for p in pool for b in (p["geometry"].bounds[0], p["geometry"].bounds[2])]
    ys = [b for p in pool for b in (p["geometry"].bounds[1], p["geometry"].bounds[3])]
    region = expand((min(xs), min(ys), max(xs), max(ys)), 0.15)
    eval_layers = dict(layers.LAYERS)
    # Main lines and yards only: a spur or siding may have been built for the project.
    eval_layers["rail"] = replace(layers.LAYERS["rail"], where="NET IN ('M','Y')")
    idx = {}
    for key, layer in eval_layers.items():
        log(f"Map layer: {layer.name}")
        idx[key] = layers.fetch(layer, tiles, region)
    log(f"Industrial parcels in the {AS_OF.year} digest")
    industrial = fulton.fetch_industrial_points(url=SNAP_AS_OF)
    checks.map_checks(pool, idx, industrial, float(scr["contamination_buffer_ft"]))

    log(f"{LABEL_YEAR} digest: industrial buildings worth $1M+")
    built = arcgis.query_features(SNAP_LABEL, BUILT_WHERE, "ParcelID,Owner,ClassCode,LUCode")
    built_idx = Index([(s, f.get("attributes") or {}) for f in built
                       if (s := esri_to_shape(f.get("geometry"))) is not None and not s.is_empty])
    log(f"  {len(built_idx):,} industrial-improved parcels")

    records, positives, cells = [], [], {}
    covered_not_sold = 0
    for p in pool:
        g = p["geometry"]
        proj = LocalProjection.around(g)
        parts = [s.intersection(g) for s, _ in built_idx.candidates(g)]
        parts = [i for i in parts if not i.is_empty and i.area > 0]
        share = proj(unary_union(parts)).area / proj(g).area if parts else 0.0
        sold = fulton.sold_after(sales.get(p["parcel_id"], []), p["owner"], AS_OF, MIN_PRICE)
        key = key_for(p["parcel_id"])
        is_pos = share >= 0.5 and sold
        if share >= 0.5 and not sold:
            covered_not_sold += 1
            continue
        if is_pos:
            positives.append(key)
        cell = cell_for(p["lon"], p["lat"])
        cells.setdefault(cell, [0, 0])
        cells[cell][0] += 1
        cells[cell][1] += int(is_pos)
        p["usable_after_slope"] = p["usable_acres"]  # no slope in the eval set
        rec = {"key": key, "block": hashlib.sha256(cell.encode()).hexdigest()[:8], "split": cell}
        rec.update({f: p.get(f) for f in FEATURES})
        rec.update({f: None for f in BANNED_AS_NONE})
        records.append(rec)

    test_cells = stratified_test_cells(cells, len(positives), len(records))
    for rec in records:
        rec["split"] = "test" if rec["split"] in test_cells else "dev"

    DATA.mkdir(parents=True, exist_ok=True)
    meta = {"as_of": AS_OF.isoformat(), "label_year": LABEL_YEAR, "built": date.today().isoformat(),
            "pool": len(records), "min_price": MIN_PRICE, "features": FEATURES,
            "banned": BANNED_AS_NONE, "screens": scr}
    with gzip.open(DATA / f"fulton_{AS_OF.year}_features.json.gz", "wt") as fh:
        json.dump({"meta": meta, "records": records}, fh)
    (DATA / f"fulton_{AS_OF.year}_labels.json").write_text(json.dumps(
        {"meta": meta, "positives": sorted(positives)}, indent=1))
    pos = set(positives)
    by_split = {s: sum(1 for r in records if r["split"] == s and r["key"] in pos)
                for s in ("dev", "test")}
    log(f"Pool {len(records):,}; positives {len(positives)} "
        f"(dev {by_split['dev']}, test {by_split['test']}); "
        f"{covered_not_sold} built on but not sold by the {AS_OF.year} owner (left out of the pool)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
