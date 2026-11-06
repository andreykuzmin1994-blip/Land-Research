"""Test helpers for the adjacency buffers (reviews/27_site_fit_buffers). Offline (SR-6).

- ``enabled()``: the adjacency-table gate ON (as if prepare.py carried the
  proposed DDL constant: ``adjacency._SOURCE_DDL`` and the prepare attribute
  are patched) for the duration of a ``with`` block. Nothing on disk changes.
- ``with_classes``: a mutate hook for ``zoning_helpers.verified`` that adds a
  human-style ``neighbour_classes`` table to one rules table, IN MEMORY only
  (the proposal in 01_prepare_mutation_proposal.md; the agent never edits
  zoning_rules.json).
- ``row``: an ``adjacency.Row`` with defaults.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Sequence
from unittest import mock

import adjacency
import prepare


@contextmanager
def enabled(ddl: str | None = None):
    text = ddl or adjacency.PROPOSED_DDL
    with mock.patch.object(adjacency, "_SOURCE_DDL", text), \
            mock.patch.object(prepare, adjacency.GATE_CONSTANT, text, create=True):
        yield


def with_classes(table: str, residential: Sequence[str] = (), agricultural: Sequence[str] = (),
                 non_residential: Sequence[str] = (), cite: str = "test table",
                 industrial: Sequence[str] | None = None,
                 readings: dict[str, str] | None = None) -> Callable[[dict[str, Any]], None]:
    """``readings`` (e.g. {"side_rear": "max", "other_district": "literal"})
    are the human's reading switches; omitted, no reading is decided."""
    def mutate(data: dict[str, Any]) -> None:
        nc: dict[str, Any] = {
            "residential": list(residential), "agricultural": list(agricultural),
            "non_residential": list(non_residential), "cite": cite}
        if industrial is not None:
            nc["industrial"] = list(industrial)
        if readings is not None:
            nc["readings"] = dict(readings)
        data["jurisdictions"][table]["neighbour_classes"] = nc
    return mutate


# The reading switches a human would write to get the shipped design's
# behaviour (rear value everywhere; Fairburn Sec. 80-372 literally).
MAX = {"side_rear": "max"}
LITERAL = {"other_district": "literal"}


def chain(*fns: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    def mutate(data: dict[str, Any]) -> None:
        for f in fns:
            f(data)
    return mutate


def row(adjacency_id: int = 1, parcel_id: str = "a", kind: str = "zoned",
        district: str | None = "R-3", jurisdictions: Sequence[str] = ("south_fulton",),
        source: str = "south_fulton_zoning", fresh: bool = True,
        length_ft: float = 1000.0) -> "adjacency.Row":
    return adjacency.Row(adjacency_id, parcel_id, kind, district, tuple(jurisdictions), source,
                         "2026-11-01", "2026-11-06", fresh, length_ft)
