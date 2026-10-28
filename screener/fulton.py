"""Fulton County adapter: parcels, deed history, site supplement, zoning.

All sources were tested live on 2026-10-27 (see screener/README.md,
"Data sources"). Other counties get their own module with the same
functions: fetch_parcels, load_cama, ZONING_SOURCES, INDUSTRIAL_PARCELS_WHERE.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from . import arcgis
from .geo import esri_to_shape
from .owners import industrial_named, is_po_box, mailing_state, owner_type, same_owner

NAME = "Fulton"
STATE = "GA"

PARCELS_URL = ("https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/"
               "PropertyMapViewer/PropertyMapViewer/MapServer/11")
PARCEL_FIELDS = ("ParcelID,Owner,OwnerAddr1,OwnerAddr2,Address,LandAcres,LUCode,"
                 "ClassCode,ExCode,TaxDist,LandAppr,ImprAppr,TotAppr")
# Free annual CAMA extract: deed history, year built, road/water/sewer.
CAMA_URL = "https://gis.fultoncountyga.gov/Data/OpenData/Data/Tax_CAMAData/TXT/Tax_CAMAInfo.zip"
# Manual lookups only: qPublic forbids automated access.
ASSESSOR_PORTAL = "https://qpublic.schneidercorp.com/Application.aspx?AppID=1010"

# First two characters of TaxDist -> jurisdiction (Fulton Tyler_Cities layer).
JURISDICTIONS = {
    "05": "Atlanta", "10": "Alpharetta", "15": "College Park", "20": "East Point",
    "25": "Fairburn", "30": "Hapeville", "35": "Mountain Park", "40": "Palmetto",
    "45": "Roswell", "50": "Union City", "55": "South Fulton / unincorporated",
    "56": "Milton", "57": "Johns Creek", "59": "Sandy Springs",
    "65": "Chattahoochee Hills", "70": "Fulton Industrial District",
}

# Industrial-class parcels of any size: the "is this an industrial area"
# context signal.
INDUSTRIAL_PARCELS_WHERE = "ClassCode LIKE 'I%'"

_SF = "https://services3.arcgis.com/y2BJK2GUfoTwH7py/arcgis/rest/services"
ZONING_SOURCES: list[dict[str, Any]] = [
    # kind "join": parcel-keyed table, matched on parcel id.
    # kind "overlay": zoning polygons, largest overlap wins.
    {"name": "South Fulton future land use 2045", "kind": "join",
     "jurisdictions": {"South Fulton / unincorporated"},
     "url": f"{_SF}/FLUM_2045_New1/FeatureServer/0", "id_field": "ParcelID",
     "flu_field": "FLUM_2045"},
    {"name": "South Fulton zoning", "kind": "overlay",
     "jurisdictions": {"South Fulton / unincorporated"},
     "url": f"{_SF}/Zoning/FeatureServer/0", "zoning_field": "ZClass",
     "desc_field": "ZClassDesc"},
    {"name": "Fulton Industrial District zoning", "kind": "overlay",
     "jurisdictions": {"South Fulton / unincorporated", "Fulton Industrial District"},
     "url": PARCELS_URL.rsplit("/", 1)[0] + "/34", "zoning_field": "ZClass",
     "desc_field": "ZClassDesc"},
    {"name": "Fulton Industrial District future land use", "kind": "overlay",
     "jurisdictions": {"South Fulton / unincorporated", "Fulton Industrial District"},
     "url": PARCELS_URL.rsplit("/", 1)[0] + "/33", "flu_field": "LandUses"},
    {"name": "Fairburn zoning + future land use", "kind": "join",
     "jurisdictions": {"Fairburn"},
     "url": "https://services8.arcgis.com/ZvMgtqecACHwgXJb/arcgis/rest/services/Current_Zoning_FLU/FeatureServer/0",
     "id_field": "ParcelID", "zoning_field": "ZoningCurr", "flu_field": "FLU"},
    {"name": "Union City zoning", "kind": "join", "jurisdictions": {"Union City"},
     "url": "https://services.arcgis.com/MewUCwyCByL0k7La/arcgis/rest/services/Union_City_Data_WFL1/FeatureServer/14",
     "id_field": "ParcelID", "zoning_field": "Zoning", "desc_field": "ZonDesc"},
    {"name": "East Point zoning + future land use", "kind": "join",
     "jurisdictions": {"East Point"},
     "url": "https://services7.arcgis.com/CPLm4MuWYjfnA2R9/arcgis/rest/services/Current_Zoning/FeatureServer/0",
     # East Point's PIN is the Fulton parcel id minus its last character.
     "id_field": "PIN", "id_trim_last": True, "zoning_field": "ZONING",
     "flu_field": "Future_Use"},
    {"name": "Atlanta zoning", "kind": "overlay", "jurisdictions": {"Atlanta"},
     "url": "https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/0",
     "zoning_field": "ZONECLASS"},
    {"name": "Atlanta future land use", "kind": "overlay", "jurisdictions": {"Atlanta"},
     "url": "https://gis.atlantaga.gov/dpcd/rest/services/LandUsePlanning/LandUsePlanning/MapServer/8",
     "flu_field": "LANDUSEDES"},
]
# No machine-readable zoning found (2026-10-27): College Park, Hapeville,
# Palmetto, Chattahoochee Hills. Those parcels show "unknown — check with
# the city".

ESTATE_DEEDS = {"Executor/Executrix Deed", "Administrators Deed", "Deed of Assent",
                "Year's Support"}
DISTRESS_DEEDS = {"Deed Under Power of Foreclosur", "Foreclosure Deed", "Tax Deed",
                  "Sheriff's Deed", "Deed in Lieu of Foreclosure"}
# Recorded deeds that don't move the land to a new owner.
NON_TRANSFER_DEEDS = {"Right of Way/Dept of Trans", "Corrective Warranty Deed",
                      "Corrective QC Deed"}


def jurisdiction(tax_dist: str | None) -> str:
    code = (tax_dist or "").strip()
    return JURISDICTIONS.get(code[:2], f"district {code or '?'}")


def _num(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def fetch_parcels(min_acres: float, max_acres: float, jurisdictions: set[str] | None,
                  url: str = PARCELS_URL) -> list[dict[str, Any]]:
    """Parcels in the acreage band. url may be a yearly snapshot (same schema)."""
    where = f"LandAcres >= {float(min_acres)} AND LandAcres <= {float(max_acres)}"
    feats = arcgis.query_features(url, where, PARCEL_FIELDS)
    by_id: dict[str, dict[str, Any]] = {}
    for f in feats:
        a = f.get("attributes") or {}
        pid = (a.get("ParcelID") or "").strip()
        geom = esri_to_shape(f.get("geometry"))
        if not pid or geom is None or geom.is_empty:
            continue
        juris = jurisdiction(a.get("TaxDist"))
        if jurisdictions and juris not in jurisdictions:
            continue
        if pid in by_id:  # multipart parcels come back as several rows
            by_id[pid]["geometry"] = by_id[pid]["geometry"].union(geom)
            continue
        owner = (a.get("Owner") or "").strip()
        addr1, addr2 = (a.get("OwnerAddr1") or "").strip(), (a.get("OwnerAddr2") or "").strip()
        by_id[pid] = {
            "parcel_id": pid,
            "county": NAME,
            "geometry": geom,
            "site_address": (a.get("Address") or "").strip(),
            "jurisdiction": juris,
            "acres": _num(a.get("LandAcres")),
            "class_code": (a.get("ClassCode") or "").strip(),
            "land_use_code": (a.get("LUCode") or "").strip(),
            "exempt_code": (a.get("ExCode") or "").strip(),
            "land_value": _num(a.get("LandAppr")),
            "improvement_value": _num(a.get("ImprAppr")),
            "total_value": _num(a.get("TotAppr")),
            "owner": owner,
            "owner_mailing": ", ".join(x for x in (addr1, addr2) if x),
            "owner_type": owner_type(owner),
            "industrial_named": industrial_named(owner),
            "owner_state": mailing_state(addr2),
            "owner_po_box": is_po_box(addr1),
            "assessor_portal": ASSESSOR_PORTAL,
        }
    return list(by_id.values())


def fetch_industrial_points(url: str = PARCELS_URL) -> list[tuple[float, float]]:
    """Centroids of industrial-class parcels (context signal)."""
    feats = arcgis.query_features(url, INDUSTRIAL_PARCELS_WHERE, "ParcelID",
                                  max_offset=0.0005)
    pts = []
    for f in feats:
        g = esri_to_shape(f.get("geometry"))
        if g is not None and not g.is_empty:
            c = g.centroid
            pts.append((c.x, c.y))
    return pts


def _parse_date(text: str | None) -> date | None:
    t = (text or "").strip()[:10]
    try:
        d = date.fromisoformat(t)
    except ValueError:
        return None
    # The extract carries junk years (0002, 5024); keep plausible ones only.
    return d if 1900 <= d.year <= date.today().year + 1 else None


def read_cama(cache_dir: Path, parcel_ids: set[str], refresh: bool = False
              ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """All recorded deeds and the site supplement for the given parcels."""
    zip_path = arcgis.download(CAMA_URL, cache_dir / "fulton_cama.zip",
                               max_age_days=0 if refresh else 30)
    sales: dict[str, list[dict[str, Any]]] = {pid: [] for pid in parcel_ids}
    supplement: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(zip_path) as z:
        with z.open("Tax_Sales.txt") as fh:
            for row in csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1"), delimiter="\t"):
                pid = (row.get("ParcelID") or "").strip()
                d = _parse_date(row.get("SaleDate"))
                if pid in sales and d is not None:
                    sales[pid].append({
                        "date": d,
                        "price": _num(row.get("Price")),
                        "instrument": (row.get("InstrType") or "").strip(),
                        "old_owner": (row.get("OldOwner") or "").strip(),
                    })
        with z.open("Tax_ParcelSupplement.txt") as fh:
            for row in csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1"), delimiter="\t"):
                pid = (row.get("ParcelID") or "").strip()
                if pid in sales:
                    supplement[pid] = {
                        "road": (row.get("Street1") or "").strip(),
                        "topography": (row.get("Topo") or "").strip(),
                        "water": (row.get("Water") or "").strip(),
                        "sewer": (row.get("Sewer") or "").strip(),
                        "fronting": (row.get("Fronting") or "").strip(),
                    }
    return sales, supplement


def acquisition(sales: list[dict[str, Any]], owner: str | None) -> dict[str, Any] | None:
    """The deed by which `owner` most likely took title.

    Skips deeds that don't move the land to a new owner (right-of-way,
    corrective), deeds the owner granted (grantor = owner), and $0
    quitclaims / gifts, which are usually family or estate-planning retitles
    ("SMITH JOHN" -> "JOHN SMITH REVOCABLE TRUST") rather than a purchase.
    Estate and foreclosure deeds count even at $0. Same-day ties prefer a
    named instrument, then the highest price.
    """
    usable = [s for s in sales
              if s["instrument"] not in NON_TRANSFER_DEEDS and not same_owner(s["old_owner"], owner)]
    real = [s for s in usable if s["price"] > 0 or s["instrument"] in ESTATE_DEEDS | DISTRESS_DEEDS]
    # Old deeds often carry no price; fall back to the latest transfer then.
    pick = real or usable
    if not pick:
        return None
    return max(pick, key=lambda s: (s["date"], bool(s["instrument"]), s["price"]))


def sold_after(sales: list[dict[str, Any]], owner: str | None, after: date,
               min_price: float) -> bool:
    """Did `owner` sell the land at a real price on or after `after`?

    A sale whose buyer was a development authority (seen as the grantor of
    the next deed) is a tax-abatement title transfer, not a market sale.
    """
    deeds = sorted(sales, key=lambda s: s["date"])
    for i, s in enumerate(deeds):
        if (s["date"] < after or s["price"] < min_price or s["instrument"] in NON_TRANSFER_DEEDS
                or not same_owner(s["old_owner"], owner)):
            continue
        buyer = next((d["old_owner"] for d in deeds[i + 1:]
                      if not same_owner(d["old_owner"], owner)), None)
        if buyer and owner_type(buyer) == "development_authority":
            continue
        return True
    return False


def load_cama(cache_dir: Path, owners: dict[str, str], as_of: date | None = None,
              refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Ownership history + site supplement, keyed by parcel id.

    owners maps parcel id -> current owner name. as_of keeps only deeds
    recorded before that date (historical evaluation).
    """
    sales, supplement = read_cama(cache_dir, set(owners), refresh)
    out: dict[str, dict[str, Any]] = {}
    for pid, owner in owners.items():
        rec = dict(supplement.get(pid, {}))
        before = [s for s in sales.get(pid, []) if as_of is None or s["date"] < as_of]
        last = acquisition(before, owner)
        rec["sales_count"] = len(before)
        rec["last_sale_date"] = last["date"].isoformat() if last else None
        rec["last_sale_price"] = last["price"] if last else None
        rec["last_sale_instrument"] = last["instrument"] if last else None
        # The current owner's own deed: inherited land, or bank/tax-deed owned.
        rec["inherited"] = bool(last and last["instrument"] in ESTATE_DEEDS)
        rec["distress_owned"] = bool(last and last["instrument"] in DISTRESS_DEEDS)
        ref = as_of or date.today()
        rec["years_held"] = round((ref - last["date"]).days / 365.25, 1) if last else None
        out[pid] = rec
    return out
