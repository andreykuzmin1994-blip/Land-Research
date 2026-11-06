"""Test helpers for the flood/wetland work (reviews/26_flood_wetlands). Offline (SR-6).

- ``enabled()``: the hazard table gate ON (as if prepare.py carried the
  proposed DDL constant: ``hazards._SOURCE_DDL`` and the prepare attribute
  are patched) for the duration of a ``with`` block. Nothing on disk changes.
- ``write_shapefile``: a minimal pure-stdlib writer of polygon .shp/.shx/
  .dbf/.prj files, for synthetic fixtures only.
- ``row``: a read-row mapping for ``hazards.assess_row``.
"""

from __future__ import annotations

import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest import mock

import hazards
import prepare

PRJ_NAD83 = ('GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",'
             'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
             'UNIT["Degree",0.0174532925199433]]')
PRJ_WGS84 = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,'
             '298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
PRJ_ALBERS = ('PROJCS["USA_Contiguous_Albers_Equal_Area_Conic_USGS_version",'
              'GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",'
              'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
              'UNIT["Degree",0.0174532925199433]],PROJECTION["Albers"],'
              'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],'
              'PARAMETER["Central_Meridian",-96.0],PARAMETER["Standard_Parallel_1",29.5],'
              'PARAMETER["Standard_Parallel_2",45.5],PARAMETER["Latitude_Of_Origin",23.0],'
              'UNIT["Meter",1.0]]')
PRJ_MERCATOR = ('PROJCS["WGS_1984_Web_Mercator_Auxiliary_Sphere",GEOGCS["GCS_WGS_1984",'
                'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
                'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
                'PROJECTION["Mercator_Auxiliary_Sphere"],UNIT["Meter",1.0]]')

NFHL_DBF_FIELDS = (("FLD_ZONE", "C", 17, 0), ("ZONE_SUBTY", "C", 76, 0),
                   ("SFHA_TF", "C", 1, 0), ("STATIC_BFE", "N", 12, 2),
                   ("DFIRM_ID", "C", 6, 0), ("VERSION_ID", "C", 11, 0))
NWI_DBF_FIELDS = (("ATTRIBUTE", "C", 20, 0), ("WETLAND_TY", "C", 50, 0))


@contextmanager
def enabled(ddl: str | None = None):
    """The hazard-table gate ON: as if prepare.py's source text carried the
    proposed DDL constant (``hazards._SOURCE_DDL``, frozen at load), and the
    in-process prepare module the matching attribute."""
    text = ddl or hazards.PROPOSED_DDL
    with mock.patch.object(hazards, "_SOURCE_DDL", text), \
            mock.patch.object(prepare, hazards.GATE_CONSTANT, text, create=True):
        yield


def _dbf_bytes(fields: Sequence[tuple], records: Sequence[Mapping[str, Any]]) -> bytes:
    rec_len = 1 + sum(f[2] for f in fields)
    head_len = 32 + 32 * len(fields) + 1
    out = bytearray(struct.pack("<BBBBIHH20x", 3, 126, 11, 6, len(records), head_len, rec_len))
    for name, ftype, length, dec in fields:
        assert len(name) <= 10, name
        out += struct.pack("<11sc4xBB14x", name.encode("ascii"), ftype.encode("ascii"),
                           length, dec)
    out += b"\x0d"
    for rec in records:
        out += b" "
        for name, ftype, length, dec in fields:
            v = rec.get(name)
            if ftype == "N":
                text = "" if v is None else f"{float(v):.{dec}f}"
                out += text.rjust(length)[:length].encode("ascii")
            else:
                out += str(v or "").ljust(length)[:length].encode("latin-1")
    out += b"\x1a"
    return bytes(out)


def write_shapefile(directory: Path, name: str, records: Sequence[tuple[list, Mapping]],
                    fields: Sequence[tuple], prj: str = PRJ_NAD83) -> Path:
    """records: (rings, attributes); rings are lists of (x, y) in the file
    CRS, already oriented as the format wants (outer clockwise)."""
    directory.mkdir(parents=True, exist_ok=True)
    body = bytearray()
    index = bytearray()
    allx: list[float] = []
    ally: list[float] = []
    for i, (rings, _attrs) in enumerate(records, start=1):
        pts = [p for r in rings for p in r]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        allx += xs
        ally += ys
        parts, k = [], 0
        for r in rings:
            parts.append(k)
            k += len(r)
        content = struct.pack("<i4dii", 5, min(xs), min(ys), max(xs), max(ys), len(rings),
                              len(pts))
        content += struct.pack(f"<{len(parts)}i", *parts)
        content += struct.pack(f"<{2 * len(pts)}d", *[c for p in pts for c in p])
        offset = (100 + len(body)) // 2
        body += struct.pack(">ii", i, len(content) // 2) + content
        index += struct.pack(">ii", offset, len(content) // 2)
    box = (min(allx), min(ally), max(allx), max(ally)) if allx else (0.0, 0.0, 0.0, 0.0)

    def header(total_bytes: int) -> bytes:
        return (struct.pack(">i20xi", 9994, total_bytes // 2)
                + struct.pack("<ii4d32x", 1000, 5, *box))
    (directory / f"{name}.shp").write_bytes(header(100 + len(body)) + bytes(body))
    (directory / f"{name}.shx").write_bytes(header(100 + len(index)) + bytes(index))
    (directory / f"{name}.dbf").write_bytes(_dbf_bytes(fields, [a for _, a in records]))
    (directory / f"{name}.prj").write_text(prj, encoding="ascii")
    return directory / f"{name}.shp"


def cw(ring: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """The ring closed and clockwise (a shapefile outer ring)."""
    r = list(ring)
    if r[0] != r[-1]:
        r.append(r[0])
    a = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r, r[1:]))
    return r if a < 0 else r[::-1]


def ccw(ring: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return cw(ring)[::-1]


def row(**kw: Any) -> dict[str, Any]:
    """A fresh read row for hazards.assess_row (both sources imported on
    the current geometry, a 100,000 SF parcel, nothing on it)."""
    base = {
        "parcel_id": "fulton-H01", "geom_md5": "m", "nfhl_md5": "m",
        "nfhl_parcel_sf": 100000, "nfhl_mapped_sf": 100000, "nfhl_undetermined_sf": 0,
        "sfha_sf": 0, "floodway_sf": 0, "x500_sf": 0,
        "nfhl_zone_sf": {"X|AREA OF MINIMAL FLOOD HAZARD|F": 100000},
        "nfhl_zone_lomr_sf": {}, "zones": ["X"],
        "bfe_ft": None, "lomr_status": "none", "nfhl_as_of": "13121C 1.1.1.0",
        "nfhl_fetched_at": "2026-11-06", "nwi_md5": "m", "nwi_parcel_sf": 100000,
        "wetland_sf": 0, "wetland_deep_excavated_sf": 0, "wetland_type_sf": {},
        "nwi_as_of": "dbf 2026-05-01", "nwi_fetched_at": "2026-11-06",
        "open_reviews": None, "multipart": False,
    }
    base.update(kw)
    return base


def db_row(r: Mapping[str, Any]) -> tuple:
    """The same mapping as the tuple hazards._SQL_HAZARDS_FOR_PARCELS returns."""
    return tuple(r[k] for k in hazards.ROW_FIELDS)
