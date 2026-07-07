"""pipeline_common.py — shared DB helpers and SQL used by multiple pipeline modules.

Holds the small set of symbols that more than one of research.py /
costar_ingest.py / reporting.py / runner.py needs: repo paths, the
flagged_items insert helper, and the two SQL constants shared between the
scoring and reporting layers. Keeping them here avoids both duplication and
cross-imports between the pipeline modules.

Mutability: infrastructure. Not part of the agent's experiment surface;
changes go through the tiered review process (see appendix_a "Coding
Workflow"), not the experiment loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Repo paths (R-40 — paths constructed for the raw-response cache must
# resolve under repo_root/sources/. Defense-in-depth.)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent

# Phase 6 (R-303, R-304, R-332): root of the CoStar ingestion staging tree.
# The directory is gitignored (.gitignore line 53). The agent creates
# subdirectories on demand. Tests monkey-patch this path inside a tempdir
# (R-329) so the offline suite never touches the real repo path.
_COSTAR_BASE_DIR = _REPO_ROOT / "costar_exports"

_SQL_INSERT_FLAG = (
    "INSERT INTO flagged_items "
    "(flag_type, parcel_id, market, description, suggested_resolution) "
    "VALUES (%s, %s, %s, %s, %s)"
)

# Phase 7 (R-511..R-515, R-516..R-518, R-519..R-522): latest market_context
# row per submarket, with CoStar preferred when sources tie on as_of_date.
# Returns vacancy/absorption/under_construction/proposed/asking_rent and the
# as_of_date so the caller can apply the staleness flag (R-514).
_SQL_LATEST_MARKET_CONTEXT = (
    "SELECT vacancy_rate_pct, net_absorption_t12_sf, "
    "under_construction_sf, proposed_sf, asking_rent_nnn_psf, "
    "as_of_date, source "
    "FROM market_context "
    "WHERE submarket_id = %s "
    "ORDER BY (CASE WHEN source = 'costar' THEN 0 ELSE 1 END), "
    "         as_of_date DESC "
    "LIMIT 1"
)

_SQL_FETCH_SUBMARKET_NAME = (
    "SELECT submarket_name FROM submarkets WHERE submarket_id = %s"
)


def _flag(
    conn: Any,
    cycle_id: str,
    parcel_id: str | None,
    market: str,
    flag_type: str,
    description: str,
    suggested_resolution: str,
) -> None:
    """Insert one flagged_items row. Embeds cycle_id into description (R-38).

    ``parcel_id`` is ``None`` for cycle-level flag rows that aren't tied to a
    specific parcel (e.g. ``harness=degraded`` or partial-corridor abort).
    psycopg sends Python ``None`` as SQL NULL; using a sentinel string like
    ``"(none)"`` would break future joins against the parcels table.
    """
    cycle_prefixed = f"cycle={cycle_id}; {description}"
    with conn.cursor() as cur:
        cur.execute(
            _SQL_INSERT_FLAG,
            (flag_type, parcel_id, market, cycle_prefixed, suggested_resolution),
        )
