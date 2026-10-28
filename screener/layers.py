"""Free national/state map layers used for the buildable checks.

Each layer is fetched once per run for the tiles around the candidate
parcels (cached on disk), then queried locally with a spatial index.
Endpoints were tested live on 2026-10-27.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shapely.strtree import STRtree

from . import arcgis
from .geo import esri_to_shape


@dataclass(frozen=True)
class Layer:
    key: str
    name: str
    url: str
    where: str = "1=1"
    fields: str = "*"
    tiled: bool = True             # fetch per tile around candidates
    max_offset: float | None = None  # geometry simplification, degrees


LAYERS: dict[str, Layer] = {
    "flood": Layer(
        "flood", "FEMA flood hazard areas (NFHL via Esri Living Atlas)",
        "https://services5.arcgis.com/7weheFjxuNkGGiZi/arcgis/rest/services/USA_Flood_Hazard_Areas_view/FeatureServer/0",
        where="SFHA_TF = 'T'", fields="FLD_ZONE,ZONE_SUBTY", max_offset=0.00001),
    "wetlands": Layer(
        "wetlands", "USFWS National Wetlands Inventory",
        "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0",
        fields="ATTRIBUTE,WETLAND_TYPE", max_offset=0.00001),
    "easements": Layer(
        "easements", "National Conservation Easement Database",
        "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/nced_DO_NOT_DELETE_240308_view/FeatureServer/0",
        fields="sitename,esmthldr,purpose"),
    "protected": Layer(
        "protected", "USGS PAD-US protected areas",
        "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/Protection_Mechanism_Category_PADUS/FeatureServer/0",
        fields="Category,Unit_Nm,Own_Type,Mang_Name"),
    "oz": Layer(
        "oz", "Federal Opportunity Zones (HUD)",
        "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/Opportunity_Zones/FeatureServer/13",
        fields="GEOID10"),
    "epa": Layer(
        "epa", "EPA cleanup sites (Superfund, RCRA corrective action, brownfields)",
        "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/Cleanups_in_my_Community_Sites/FeatureServer/0",
        fields="REGISTRY_ID,LOCATION_ADDRESS,CITY_NAME,CAT_NAME,SUB_NAME,SF_NPL_CODE",
        tiled=False),
    "hsi": Layer(
        "hsi", "Georgia EPD Hazardous Site Inventory",
        "https://services1.arcgis.com/p0dLjwtOaJHU8zq2/arcgis/rest/services/FMS_Site_Summary_ESRI_Pdf_List_export_2026_06_26_11_09_51_992/FeatureServer/0",
        fields="HSI_ID,Facility_Name,Address,Class", tiled=False),
    "exits": Layer(
        "exits", "GDOT interstate exits",
        "https://rnhp.dot.ga.gov/hosting/rest/services/FCRS/MapServer/0",
        fields="FED_ASSIGNED_INTERSTATE_NUMBER,SIGNED_EXIT_NUMBER,ROUTE_NUMBER", tiled=False),
    "rail": Layer(
        "rail", "Freight rail (BTS North American Rail Network)",
        "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_North_American_Rail_Network_Lines/FeatureServer/0",
        # M main, Y yard, O other, S siding, I industry; excludes abandoned.
        where="NET IN ('M','Y','O','S','I')", fields="RROWNER1,NET,SUBDIV", tiled=False),
}


class Index:
    """Shapes + attributes with an STRtree for bbox candidate lookups."""

    def __init__(self, items: list[tuple[Any, dict[str, Any]]]):
        self.items = items
        self.tree = STRtree([s for s, _ in items]) if items else None

    def candidates(self, geom) -> list[tuple[Any, dict[str, Any]]]:
        if self.tree is None:
            return []
        return [self.items[i] for i in self.tree.query(geom)]

    def __len__(self) -> int:
        return len(self.items)


def fetch(layer: Layer, tiles: list[tuple[float, float, float, float]],
          region: tuple[float, float, float, float]) -> Index:
    """Fetch a layer for the candidate tiles (or the whole region)."""
    envelopes = tiles if layer.tiled else [region]
    ids: set[int] = set()
    for env in envelopes:
        ids.update(arcgis.query_ids(layer.url, layer.where, env))
    feats = arcgis.fetch_by_ids(layer.url, sorted(ids), layer.fields,
                                max_offset=layer.max_offset)
    items = []
    for f in feats:
        shape = esri_to_shape(f.get("geometry"))
        if shape is not None and not shape.is_empty:
            items.append((shape, f.get("attributes") or {}))
    return Index(items)
