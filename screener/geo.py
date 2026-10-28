"""Geometry helpers: Esri JSON -> shapely, local projection, distances.

Parcels are 5-50 acres, so each parcel is measured in a flat local
projection centred on its own centroid (equirectangular, metres). Over a
few kilometres the error is well under 1%, which is plenty for "what share
of this parcel is floodplain" and avoids a pyproj dependency.
"""

from __future__ import annotations

import math
from typing import Any

import shapely
from shapely import make_valid
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

SQM_PER_ACRE = 4046.8564224
FT_PER_M = 3.28084
EARTH_RADIUS_MI = 3958.7613


def esri_to_shape(geom: dict[str, Any] | None) -> BaseGeometry | None:
    """Convert an Esri JSON geometry (WGS84) into a valid shapely geometry."""
    if not geom:
        return None
    if "x" in geom and "y" in geom:
        if geom["x"] is None or geom["y"] is None:
            return None
        return Point(geom["x"], geom["y"])
    if "paths" in geom:
        lines = [LineString(p) for p in geom["paths"] if len(p) >= 2]
        return MultiLineString(lines) if lines else None
    if "rings" in geom:
        return _rings_to_polygon(geom["rings"])
    return None


def _rings_to_polygon(rings: list[list[list[float]]]) -> BaseGeometry | None:
    # Esri: exterior rings run clockwise, holes counter-clockwise.
    shells, holes = [], []
    for ring in rings:
        if len(ring) < 4:
            continue
        (holes if _signed_area(ring) > 0 else shells).append(ring)
    if not shells:  # some servers emit counter-clockwise shells only
        shells, holes = holes, []
    polys = []
    for shell in shells:
        shell_poly = Polygon(shell)
        inner = [h for h in holes
                 if shell_poly.contains(Polygon(h).representative_point())]
        polys.append(Polygon(shell, inner))
    if not polys:
        return None
    shape = polys[0] if len(polys) == 1 else MultiPolygon(polys)
    if not shape.is_valid:
        shape = make_valid(shape)
    return shape


def _signed_area(ring: list[list[float]]) -> float:
    """Shoelace area; positive for counter-clockwise rings."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += x1 * y2 - x2 * y1
    return total / 2.0


class LocalProjection:
    """Flat metres around (lon0, lat0)."""

    def __init__(self, lon0: float, lat0: float):
        self.lon0, self.lat0 = lon0, lat0
        self.kx = 111_320.0 * math.cos(math.radians(lat0))
        self.ky = 110_574.0

    def __call__(self, geom: BaseGeometry) -> BaseGeometry:
        return shapely.transform(geom, self._fwd)

    def _fwd(self, coords):
        out = coords.copy()
        out[:, 0] = (coords[:, 0] - self.lon0) * self.kx
        out[:, 1] = (coords[:, 1] - self.lat0) * self.ky
        return out

    @classmethod
    def around(cls, geom: BaseGeometry) -> "LocalProjection":
        c = geom.centroid
        return cls(c.x, c.y)


def acres(geom_m: BaseGeometry) -> float:
    return geom_m.area / SQM_PER_ACRE


def haversine_miles(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def tile_envelopes(bounds_list, size_deg: float = 0.05):
    """Grid tiles (WGS84 envelopes) that touch any of the given bounds."""
    keys = set()
    for xmin, ymin, xmax, ymax in bounds_list:
        for i in range(math.floor(xmin / size_deg), math.floor(xmax / size_deg) + 1):
            for j in range(math.floor(ymin / size_deg), math.floor(ymax / size_deg) + 1):
                keys.add((i, j))
    return [(i * size_deg, j * size_deg, (i + 1) * size_deg, (j + 1) * size_deg)
            for i, j in sorted(keys)]


def expand(bounds, deg: float):
    xmin, ymin, xmax, ymax = bounds
    return (xmin - deg, ymin - deg, xmax + deg, ymax + deg)
