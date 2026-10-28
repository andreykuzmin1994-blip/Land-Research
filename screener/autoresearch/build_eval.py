"""Build the frozen evaluation set the AutoResearch loop is scored on.

A human runs this between runs, never the agent:

    python -m screener.autoresearch.build_eval

The question the metric asks: standing in January 2018, knowing only what
public data showed then, would the ranking have put the land that
industrial developers actually bought and built on by 2025 near the top?

Pool:      Fulton 5-50 acre parcels that were land in the 2018 tax digest
           (improvements < $100k, not tax-exempt, not government / developer /
           utility owned), in the jurisdictions from settings.toml.
Positives: pool parcels at least half covered, in the 2025 digest, by an
           industrial building worth $1M+ AND owned by someone else by then.
Features:  the 2018 digest + 2018 deed history + map layers that change
           slowly (flood, 1980s wetlands, easements, exits, rail, lidar).
           Zoning, utilities and road type are left out: their current
           values were often changed FOR the project (leakage).
Split:     ~5 km grid cells; one cell in four is the sealed test set.

Writes screener/autoresearch/data/fulton_2018_features.json.gz and
fulton_2018_labels.json. Records carry no owner names, parcel ids or
coordinates, so the ranking cannot memorize answers.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
import tomllib
from datetime import date
from pathlib import Path

from shapely import unary_union

from .. import arcgis, checks, fulton, layers, screens
from ..geo import LocalProjection, esri_to_shape, expand, tile_envelopes
from ..layers import Index
from ..owners import normalize

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SNAPSHOTS = "https://services1.arcgis.com/AQDHTHDrZzfsFsB5/arcgis/rest/services"
SNAP_AS_OF = f"{SNAPSHOTS}/Tax_Parcels2018/FeatureServer/0"
SNAP_LABEL = f"{SNAPSHOTS}/Tax_Parcels_2025/FeatureServer/0"
AS_OF = date(2018, 1, 1)
LABEL_YEAR = 2025

# Industrial land-use codes in the Fulton digest (warehouse, manufacturing,
# truck terminal, ...), from the 2026-10-27 answer-key research.
INDUSTRIAL_LU = ("390", "391", "394", "395", "397", "398", "399", "401", "419",
                 "420", "443", "485", "332", "335", "339")
BUILT_WHERE = ("ImprAppr >= 1000000 AND (ClassCode LIKE 'I%' OR LUCode IN ("
               + ",".join(f"'{c}'" for c in INDUSTRIAL_LU) + "))")

EXCLUDED_OWNERS = {"government", "development_authority", "developer", "utility"}

# The only parcel fields the ranking sees. Everything is as of 2018 or
# changes slowly; see the module docstring for what is left out and why.
FEATURES = [
    "acres", "usable_acres", "usable_after_slope", "flood_pct", "floodway_pct",
    "wetland_pct", "water_pct", "easement_pct", "protected_pct", "constrained_pct",
    "opportunity_zone", "contamination_count", "contamination_nearest_ft",
    "exit_miles", "rail_ft", "rail_owner", "industrial_within_1mi", "steep_pct",
    "relief_ft", "mean_slope_pct", "land_value", "class_code", "land_use_code",
    "jurisdiction", "owner_type", "owner_state", "owner_po_box", "years_held",
    "sales_count", "inherited", "distress_owned", "last_sale_price",
]
BANNED_AS_NONE = ["zoning", "flu", "entitlement", "entitlement_note", "water", "sewer", "road"]


def key_for(parcel_id: str) -> str:
    return hashlib.sha256(f"fulton-2018:{parcel_id}".encode()).hexdigest()[:16]


def split_for(lon: float, lat: float) -> str:
    cell = f"{math.floor(lon / 0.05)}:{math.floor(lat / 0.05)}"
    return "test" if int(hashlib.sha256(cell.encode()).hexdigest(), 16) % 4 == 0 else "dev"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    cfg = tomllib.loads((HERE.parent / "settings.toml").read_text())
    arcgis.cache_days = 30
    juris = set(cfg["area"]["jurisdictions"])

    log("2018 digest: 5-50 acre parcels")
    pool = fulton.fetch_parcels(5, 50, juris, url=SNAP_AS_OF)
    pool = [p for p in pool
            if p["improvement_value"] < 100_000 and not p["class_code"].startswith("E")
            and p["owner_type"] not in EXCLUDED_OWNERS]
    log(f"  {len(pool):,} land parcels in the pool")

    log("Deed history as of 2018-01-01")
    cama = fulton.load_cama(HERE.parent / "cache", {p["parcel_id"] for p in pool}, as_of=AS_OF)
    for p in pool:
        p.update({k: v for k, v in cama.get(p["parcel_id"], {}).items()
                  if k not in ("water", "sewer", "road", "topography", "fronting")})

    tiles = tile_envelopes([p["geometry"].bounds for p in pool])
    xs = [b for p in pool for b in (p["geometry"].bounds[0], p["geometry"].bounds[2])]
    ys = [b for p in pool for b in (p["geometry"].bounds[1], p["geometry"].bounds[3])]
    region = expand((min(xs), min(ys), max(xs), max(ys)), 0.15)
    idx = {}
    for key, layer in layers.LAYERS.items():
        log(f"Map layer: {layer.name}")
        idx[key] = layers.fetch(layer, tiles, region)
    log("Industrial parcels in the 2018 digest")
    industrial = fulton.fetch_industrial_points(url=SNAP_AS_OF)
    checks.map_checks(pool, idx, industrial, float(cfg["screens"]["contamination_buffer_ft"]))
    log(f"Slope for {len(pool):,} parcels")
    checks.slope_checks(pool, int(cfg["run"].get("workers", 6)),
                        lambda d, n: log(f"  slope {d:,}/{n:,}"))

    log(f"{LABEL_YEAR} digest: industrial buildings worth $1M+")
    built = arcgis.query_features(SNAP_LABEL, BUILT_WHERE, "ParcelID,Owner,ClassCode,LUCode")
    built_idx = Index([(s, f.get("attributes") or {}) for f in built
                       if (s := esri_to_shape(f.get("geometry"))) is not None and not s.is_empty])
    log(f"  {len(built_idx):,} industrial-improved parcels")

    records, positives = [], []
    for p in pool:
        g = p["geometry"]
        proj = LocalProjection.around(g)
        hits = [(s.intersection(g), a) for s, a in built_idx.candidates(g)]
        hits = [(i, a) for i, a in hits if not i.is_empty and i.area > 0]
        share = (proj(unary_union([i for i, _ in hits])).area / proj(g).area) if hits else 0.0
        new_owners = {normalize(a.get("Owner")) for _, a in hits}
        sold = bool(new_owners) and normalize(p["owner"]) not in new_owners
        key = key_for(p["parcel_id"])
        if share >= 0.5 and sold:
            positives.append(key)
        p["usable_after_slope"] = screens.usable_after_slope(p)
        p["contamination_count"] = len(p.get("contamination") or [])
        p["contamination_nearest_ft"] = (p["contamination"][0]["feet"]
                                         if p.get("contamination") else None)
        rec = {"key": key, "split": split_for(p["lon"], p["lat"])}
        rec.update({f: p.get(f) for f in FEATURES})
        rec.update({f: None for f in BANNED_AS_NONE})
        records.append(rec)

    DATA.mkdir(parents=True, exist_ok=True)
    meta = {"as_of": AS_OF.isoformat(), "label_year": LABEL_YEAR, "built": date.today().isoformat(),
            "pool": len(records), "features": FEATURES, "banned": BANNED_AS_NONE}
    with gzip.open(DATA / "fulton_2018_features.json.gz", "wt") as fh:
        json.dump({"meta": meta, "records": records}, fh)
    (DATA / "fulton_2018_labels.json").write_text(json.dumps(
        {"meta": meta, "positives": sorted(positives)}, indent=1))
    by_split = {s: sum(1 for r in records if r["split"] == s and r["key"] in set(positives))
                for s in ("dev", "test")}
    log(f"Pool {len(records):,}; positives {len(positives)} "
        f"(dev {by_split['dev']}, test {by_split['test']}); "
        f"base rate {100 * len(positives) / max(1, len(records)):.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
