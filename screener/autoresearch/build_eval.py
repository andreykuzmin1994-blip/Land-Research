"""Build the frozen evaluation set the AutoResearch loop is scored on.

A human runs this between runs, never the loop's agent:

    python -m screener.autoresearch.build_eval

The question the metric asks, once for each as-of year in COHORTS:
standing in January of that year, knowing only what public data showed
then, would the ranking have put the land that its owners then sold to
industrial developers, who built on it by 2025, near the top?

Pool:      Fulton 5-50 acre parcels in that year's tax digest that pass the
           live tool's ownership and land-only screens
           (screens.attribute_rejects), minus owners whose names read like
           industrial developer vehicles (that land was already off the
           market).
Positives: pool parcels that the as-of owner SOLD on or after 1 January of
           the as-of year at a real price (>= MIN_PRICE in the county deed
           file; right-of-way deeds and sales to a development authority
           don't count), BEFORE the building: the sale must be dated before
           1 January of the first digest year in which the parcel is at
           least half covered by parcels carrying $1M+ of industrial
           improvements (BUILT_YEARS; the label is that coverage in
           LABEL_YEAR). A developer who bought the land, built, and sold the
           finished building is not "an owner who sold land to a developer".
           The digests date a building only to the year, so that rule alone
           still let through a buyer who built and resold within a year or
           so (reviews/36, 05, finding 4). So the as-of owner must also be
           the owner from before the key's window: a built parcel that
           anyone bought at a real price on or after 1 January of the first
           as-of year and before this as-of date is left out for this year.
           Its earlier years, under the owner who sold it, still count.
Left out:  parcels built on by LABEL_YEAR without such a sale (owner-users,
           corporate successors, replatted ids, sales after the building,
           buyers inside the window, and sales whose order against the
           building can't be told because the county published no 2022
           digest) are dropped from the pool, not counted as misses. So is
           any parcel, built or not, with a priced deed in the first quarter
           of the as-of year whose seller is not the digest's owner: the
           digest is from somewhat after 1 January and already shows the
           buyer, whose record would be paired with the seller's history.
Features:  that year's digest + deed history before the as-of date + layers
           that change slowly (flood, 1980s wetlands, easements, exits,
           main-line rail). Blank (None) because today's values were often
           changed FOR the project or weren't knowable then: zoning,
           utilities, road type, slope (2018-19 lidar shows graded pads as
           flat), contamination listings, Opportunity Zones (designated
           2018), and jurisdiction (Fulton re-coded its tax districts).
Sites:     the same parcel is in several years' pools. A positive record's
           weight is 1 / (the number of years in which that parcel is a
           positive), so every distinct site sums to 1 (labels file). A
           parcel's map cell is fixed by the first year it appears in.
Split:     ~5 km grid cells. The cells sealed for the test set are frozen
           in data/test_blocks.json (their block ids) and are the same in
           every year; every other cell is dev. No dev parcel may overlap
           the ground of a test parcel in any year (such a parcel is
           dropped). Each record keeps its cell as "block" for the
           clustered bootstrap.

Writes screener/autoresearch/data/fulton_<year>_features.json.gz and
fulton_<year>_labels.json for each year. The labels file also gives each
positive record a site hash ("sites"); tests/test_eval_key.py checks on the
built files that a site's weights sum to 1 and that it sits in one cell and
one split. The ranking never sees the labels file. Records carry no owner names,
parcel ids or coordinates (the keys and block ids are short hashes: they
keep casual eyes off the answers, they are not a secret, since the county
file is public).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
import tomllib
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from shapely import STRtree, unary_union

from .. import arcgis, checks, fulton, layers, screens
from ..geo import LocalProjection, esri_to_shape, expand, tile_envelopes
from ..layers import Index

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
TEST_BLOCKS_FILE = DATA / "test_blocks.json"
SNAPSHOTS = "https://services1.arcgis.com/AQDHTHDrZzfsFsB5/arcgis/rest/services"
# As-of years. Later years are left out: under five years to sell and build
# is too short a window.
COHORTS = (2015, 2016, 2017, 2018, 2019)
LABEL_YEAR = 2025
# Digest years used to date the building (the first one that shows it). The
# county published no 2022 layer.
BUILT_YEARS = (2015, 2016, 2017, 2018, 2019, 2020, 2021, 2023, 2024, 2025)
MIN_PRICE = 50_000
# The key's window opens on 1 January of the first as-of year: a built parcel
# bought at a real price since then is in a developer's or middleman's hands.
WINDOW_START = date(COHORTS[0], 1, 1)
# How long after 1 January a digest may still have been exported (the
# snapshot date check found first-quarter buyers in every year's digest).
SNAPSHOT_SLACK = timedelta(days=92)
TEST_SHARE = 0.25     # what the sealed cells were first picked to hold (stratified_test_cells)


def snapshot_url(year: int) -> str:
    """The county's digest layer for a year (2023 on are named with an underscore)."""
    return f"{SNAPSHOTS}/Tax_Parcels{'_' if year >= 2023 else ''}{year}/FeatureServer/0"


SNAP_LABEL = snapshot_url(LABEL_YEAR)

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


def key_for(parcel_id: str, year: int = 2016) -> str:
    """A record's key: per year, so one parcel has a different key in each."""
    return hashlib.sha256(f"fulton-{year}:{parcel_id}".encode()).hexdigest()[:16]


def site_for(parcel_id: str) -> str:
    """A site's hash in the labels file: the same in every year."""
    return hashlib.sha256(f"fulton-site:{parcel_id}".encode()).hexdigest()[:16]


def block_for(cell: str) -> str:
    return hashlib.sha256(cell.encode()).hexdigest()[:8]


def cell_for(lon: float, lat: float) -> str:
    return f"{math.floor(lon / 0.05)}:{math.floor(lat / 0.05)}"


def stratified_test_cells(cells: dict[str, list[int]], total_pos: int, total_pool: int) -> set[str]:
    """How the sealed cells were first picked (2016 key, 2026-10-29); kept
    for the record. The build no longer calls it: the sealed cells are
    frozen in TEST_BLOCKS_FILE. cells: cell -> [pool count, positive count].
    Fixed hash order; a cell joins the test set only if it keeps the test
    set near TEST_SHARE of the positives and under 30% of the pool."""
    target = round(TEST_SHARE * total_pos)
    test, tp, tn = set(), 0, 0
    for cell in sorted(cells, key=lambda c: hashlib.sha256(c.encode()).hexdigest()):
        n, pos = cells[cell]
        if tp + pos <= target + 1 and tn + n <= 0.3 * total_pool:
            test.add(cell)
            tp, tn = tp + pos, tn + n
    return test


def sale_before(sales: list[dict[str, Any]], owner: str | None, after: date, before: date,
                min_price: float) -> bool:
    """Did `owner` sell the land at a real price on or after `after` and
    before `before`? fulton.sold_after with an upper bound: a sale whose
    buyer was a development authority (seen as the grantor of the next deed)
    is a tax-abatement title transfer, not a market sale. The whole deed
    list is read for the buyer, whatever the bound."""
    deeds = sorted(sales, key=lambda s: s["date"])
    for i, s in enumerate(deeds):
        if (s["date"] < after or s["date"] >= before or s["price"] < min_price
                or s["instrument"] in fulton.NON_TRANSFER_DEEDS
                or not fulton.same_owner(s["old_owner"], owner)):
            continue
        buyer = next((d["old_owner"] for d in deeds[i + 1:]
                      if not fulton.same_owner(d["old_owner"], owner)), None)
        if buyer and fulton.owner_type(buyer) == "development_authority":
            continue
        return True
    return False


def built_window(first_year: int) -> tuple[date, date]:
    """(before the digests show the building, surely not before it) for a
    building first shown in the `first_year` digest. A sale before the first
    date came before any digest showed the building, which is not the same
    as before the building: a digest shows a building months to a year
    after it is finished (bought_in_window covers that gap). A sale on or
    after the second date did not precede it; between them (only when the
    previous digest year is missing: 2022) the order can't be told."""
    i = BUILT_YEARS.index(first_year)
    prev = BUILT_YEARS[i - 1] if i else first_year - 1
    return date(prev + 1, 1, 1), date(first_year, 1, 1)


def covered_share(geom: Any, built_idx: Index) -> float:
    """Share of a parcel covered by industrial-improved parcels of one digest."""
    proj = LocalProjection.around(geom)
    parts = [s.intersection(geom) for s, _ in built_idx.candidates(geom)]
    parts = [i for i in parts if not i.is_empty and i.area > 0]
    return proj(unary_union(parts)).area / proj(geom).area if parts else 0.0


def _real_transfer(deed: dict[str, Any], owner: str | None, min_price: float) -> bool:
    """A priced deed (above min_price) by which someone other than `owner` sold."""
    return (deed["price"] > min_price and deed["instrument"] not in fulton.NON_TRANSFER_DEEDS
            and not fulton.same_owner(deed["old_owner"], owner))


def digest_shows_buyer(sales: list[dict[str, Any]], owner: str | None, as_of: date) -> bool:
    """Is the digest's owner the buyer of a first-quarter sale? True when the
    first priced deed dated in the SNAPSHOT_SLACK after the as-of date has a
    seller other than the digest's owner (a January owner would be the
    seller of that deed)."""
    quarter = sorted((s for s in sales if as_of <= s["date"] < as_of + SNAPSHOT_SLACK
                      and s["price"] > 0 and s["instrument"] not in fulton.NON_TRANSFER_DEEDS),
                     key=lambda s: s["date"])
    # Only the quarter's first priced deed says who owned the land in
    # January: when the January owner sells and the buyer resells within the
    # quarter, the second deed's seller is not the digest's owner either.
    return bool(quarter) and not fulton.same_owner(quarter[0]["old_owner"], owner)


def bought_in_window(sales: list[dict[str, Any]], owner: str | None, as_of: date) -> bool:
    """Did the land change hands at a real price inside the key's window
    before the as-of date? The seller of such a deed is not the as-of owner,
    so the as-of owner (or someone between) is its buyer."""
    return any(WINDOW_START <= s["date"] < as_of and _real_transfer(s, owner, MIN_PRICE - 0.01)
               for s in sales)


def classify(shares: dict[int, float], sales: list[dict[str, Any]], owner: str | None,
             as_of: date) -> str:
    """One pool parcel's label in one cohort: "positive", "negative", or a
    reason it is left out of the pool. shares: digest year -> covered share."""
    if digest_shows_buyer(sales, owner, as_of):
        return "left out: the digest shows the buyer of a first-quarter sale"
    if shares.get(LABEL_YEAR, 0.0) < 0.5:
        return "negative"
    if bought_in_window(sales, owner, as_of):
        return f"left out: bought at a real price since {WINDOW_START.year}"
    first = next(y for y in BUILT_YEARS if shares.get(y, 0.0) >= 0.5)
    surely_before, not_before = built_window(first)
    if sale_before(sales, owner, as_of, surely_before, MIN_PRICE):
        return "positive"
    if sale_before(sales, owner, as_of, not_before, MIN_PRICE):
        return "left out: sale and building can't be ordered (no 2022 digest)"
    if sale_before(sales, owner, as_of, date.max, MIN_PRICE):
        return "left out: sold after the building"
    return "left out: built but not sold by the as-of owner"


def snapshot_date_check(pool: list[dict[str, Any]], sales: dict[str, list[dict[str, Any]]],
                        as_of: date) -> dict[str, list[int]]:
    """Does the digest show owners as of about 1 January? For pool parcels
    with exactly one priced transfer deed in the quarter before or after
    the as-of date: [snapshot owner is the seller, snapshot owner is not].
    A January snapshot shows the buyer for deeds before it and the seller
    for deeds after it."""
    out = {"quarter_before": [0, 0], "quarter_after": [0, 0]}
    lo, hi = as_of - timedelta(days=92), as_of + timedelta(days=92)
    for p in pool:
        near = [s for s in sales.get(p["parcel_id"], [])
                if lo <= s["date"] < hi and s["price"] > 0
                and s["instrument"] not in fulton.NON_TRANSFER_DEEDS]
        if len(near) != 1:
            continue
        side = "quarter_before" if near[0]["date"] < as_of else "quarter_after"
        out[side][0 if fulton.same_owner(near[0]["old_owner"], p["owner"]) else 1] += 1
    return out


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_test_blocks() -> set[str]:
    """The sealed cells' block ids. Frozen: the build never re-picks them."""
    try:
        blocks = json.loads(TEST_BLOCKS_FILE.read_text())["test_blocks"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"{TEST_BLOCKS_FILE} is missing or unreadable ({exc}). It freezes the "
                         "sealed test cells; restore it from git rather than rebuilding it.")
    if not isinstance(blocks, list) or not blocks or not all(isinstance(b, str) for b in blocks):
        raise SystemExit(f"{TEST_BLOCKS_FILE} holds no list of block ids")
    return set(blocks)


def fetch_built(year: int) -> Index:
    """Parcels carrying $1M+ of industrial improvements in one digest year."""
    built = arcgis.query_features(snapshot_url(year), BUILT_WHERE, "ParcelID,ClassCode,LUCode")
    return Index([(s, f.get("attributes") or {}) for f in built
                  if (s := esri_to_shape(f.get("geometry"))) is not None and not s.is_empty])


def build_cohort(year: int, cfg: dict[str, Any], built: dict[int, Index]
                 ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One as-of year's pool with features and labels. Returns the parcels
    (each with "label") and the counts for the build log."""
    as_of = date(year, 1, 1)
    snap = snapshot_url(year)
    juris = set(cfg["area"]["jurisdictions"])
    scr = cfg["screens"]
    log(f"{year} digest: 5-50 acre parcels")
    fetched = fulton.fetch_parcels(5, 50, juris, url=snap)
    pool = [p for p in fetched
            if not screens.attribute_rejects(p, scr, {}) and not p["industrial_named"]]
    log(f"  {len(pool):,} of {len(fetched):,} parcels pass the ownership and land-only screens")

    owners = {p["parcel_id"]: p["owner"] for p in pool}
    sales, _ = fulton.read_cama(HERE.parent / "cache", set(owners))
    cama = fulton.load_cama(HERE.parent / "cache", owners, as_of=as_of)
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
    idx = {key: layers.fetch(layer, tiles, region) for key, layer in eval_layers.items()}
    industrial = fulton.fetch_industrial_points(url=snap)
    checks.map_checks(pool, idx, industrial, float(scr["contamination_buffer_ft"]))

    counts: dict[str, Any] = {"fetched": len(fetched), "screened_pool": len(pool),
                              "snapshot_date_check": snapshot_date_check(pool, sales, as_of),
                              "by_jurisdiction": {}}
    for p in pool:
        counts["by_jurisdiction"][p["jurisdiction"]] = \
            counts["by_jurisdiction"].get(p["jurisdiction"], 0) + 1
        shares = {LABEL_YEAR: covered_share(p["geometry"], built[LABEL_YEAR])}
        if shares[LABEL_YEAR] >= 0.5:      # only then does the building's first year matter
            shares.update({y: covered_share(p["geometry"], built[y])
                           for y in BUILT_YEARS if y != LABEL_YEAR})
        p["label"] = classify(shares, sales.get(p["parcel_id"], []), p["owner"], as_of)
        counts[p["label"]] = counts.get(p["label"], 0) + 1
        p["usable_after_slope"] = p["usable_acres"]  # no slope in the eval set
    return pool, counts


def site_cells(cohorts: dict[int, list[dict[str, Any]]]) -> dict[str, str]:
    """One map cell per site: the cell of the first year the parcel is in,
    whatever its centroid does in later years."""
    cell_of: dict[str, str] = {}
    for year in sorted(cohorts):
        for p in cohorts[year]:
            cell_of.setdefault(p["parcel_id"], cell_for(p["lon"], p["lat"]))
    return cell_of


def dev_on_test_ground(kept: list[dict[str, Any]], is_test: dict[str, bool]) -> set[str]:
    """Dev parcel ids that share ground with a test parcel in any year (a
    replatted or re-drawn parcel under a new id). They are dropped: no dev
    record may sit on sealed ground."""
    test_shapes = [p["geometry"] for p in kept if is_test[p["parcel_id"]]]
    if not test_shapes:
        return set()
    tree = STRtree(test_shapes)
    straddle: set[str] = set()
    for p in kept:
        if is_test[p["parcel_id"]]:
            continue
        g = p["geometry"]
        for i in tree.query(g):
            inter = g.intersection(test_shapes[int(i)])
            if not inter.is_empty and inter.area > 0.01 * min(g.area, test_shapes[int(i)].area):
                straddle.add(p["parcel_id"])
                break
    return straddle


def positive_years(kept: list[dict[str, Any]], straddle: set[str]) -> dict[str, int]:
    """parcel id -> the number of years in which it is a positive record.
    A positive record's weight is 1 over this, so a site sums to 1."""
    years: dict[str, int] = {}
    for p in kept:
        if p["label"] == "positive" and p["parcel_id"] not in straddle:
            years[p["parcel_id"]] = years.get(p["parcel_id"], 0) + 1
    return years


def write_gz(path: Path, doc: Any) -> None:
    """Gzip without a timestamp: the same content gives the same bytes."""
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
        fh.write(json.dumps(doc).encode())


def main() -> int:
    cfg = tomllib.loads((HERE.parent / "settings.toml").read_text())
    arcgis.cache_days = 30
    scr = cfg["screens"]
    test_blocks = load_test_blocks()

    log(f"Industrial buildings worth $1M+ in the digests of {BUILT_YEARS[0]}-{BUILT_YEARS[-1]}")
    built = {y: fetch_built(y) for y in BUILT_YEARS}
    log("  " + ", ".join(f"{y}: {len(built[y]):,}" for y in BUILT_YEARS))

    cohorts: dict[int, list[dict[str, Any]]] = {}
    counts: dict[int, dict[str, Any]] = {}
    for year in COHORTS:
        cohorts[year], counts[year] = build_cohort(year, cfg, built)

    # The county replaces its deed file every year: record which one this was.
    deed_file = hashlib.sha256((HERE.parent / "cache" / "fulton_cama.zip").read_bytes()).hexdigest()
    cell_of = site_cells(cohorts)
    is_test = {pid: block_for(cell) in test_blocks for pid, cell in cell_of.items()}
    kept = [p for year in COHORTS for p in cohorts[year] if not p["label"].startswith("left out")]
    straddle = dev_on_test_ground(kept, is_test)
    years_positive = positive_years(kept, straddle)

    DATA.mkdir(parents=True, exist_ok=True)
    seen_keys: set[str] = set()
    summary = []
    for year in COHORTS:
        records, weights, sites = [], {}, {}
        dropped = 0
        for p in cohorts[year]:
            pid = p["parcel_id"]
            if p["label"].startswith("left out"):
                continue
            if pid in straddle:
                dropped += 1
                continue
            key = key_for(pid, year)
            if key in seen_keys:
                raise SystemExit(f"duplicate record key in {year}: the keys must be unique")
            seen_keys.add(key)
            rec = {"key": key, "block": block_for(cell_of[pid]),
                   "split": "test" if is_test[pid] else "dev", "cohort": year}
            rec.update({f: p.get(f) for f in FEATURES})
            rec.update({f: None for f in BANNED_AS_NONE})
            records.append(rec)
            if p["label"] == "positive":
                weights[key] = round(1.0 / years_positive[pid], 6)
                sites[key] = site_for(pid)
        meta = {"as_of": date(year, 1, 1).isoformat(), "label_year": LABEL_YEAR,
                "cohorts": list(COHORTS), "built_years": list(BUILT_YEARS),
                "built": date.today().isoformat(), "pool": len(records), "min_price": MIN_PRICE,
                "window_start": WINDOW_START.isoformat(), "deed_file_sha256": deed_file,
                "features": FEATURES, "banned": BANNED_AS_NONE, "screens": scr}
        write_gz(DATA / f"fulton_{year}_features.json.gz", {"meta": meta, "records": records})
        (DATA / f"fulton_{year}_labels.json").write_text(json.dumps(
            {"meta": meta, "positives": dict(sorted(weights.items())),
             "sites": dict(sorted(sites.items()))}, indent=1))
        by_split = {s: [sum(1 for r in records if r["split"] == s),
                        sum(1 for r in records if r["split"] == s and r["key"] in weights),
                        round(sum(weights[r["key"]] for r in records
                                  if r["split"] == s and r["key"] in weights), 2)]
                    for s in ("dev", "test")}
        left = {k: v for k, v in counts[year].items() if k.startswith("left out")}
        log(f"{year}: pool {len(records):,}; positives {len(weights)} "
            f"(dev {by_split['dev'][1]}, test {by_split['test'][1]}); "
            f"sites dev {by_split['dev'][2]}, test {by_split['test'][2]}; "
            f"dropped for sharing ground with a test parcel: {dropped}")
        for why, n in sorted(left.items()):
            log(f"    {n} {why}")
        chk = counts[year]["snapshot_date_check"]
        log(f"    snapshot date check [owner is seller, owner is not]: deeds in the quarter "
            f"before {chk['quarter_before']}, after {chk['quarter_after']}")
        summary.append((year, by_split))
    dev_sites = sum(b["dev"][2] for _, b in summary)
    test_sites = sum(b["test"][2] for _, b in summary)
    log(f"All years: {len(years_positive)} distinct positive sites "
        f"(dev {dev_sites:.1f}, test {test_sites:.1f}); test share of sites "
        f"{test_sites / (dev_sites + test_sites):.0%}")
    log("Pool by jurisdiction and year: " + json.dumps(
        {str(y): counts[y]["by_jurisdiction"] for y in COHORTS}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
