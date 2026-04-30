"""research.py — The autonomous agent's sandbox.

============================================================================
THE ONLY FILE THE AGENT EDITS DURING A RUN
============================================================================
Per AUTORESEARCH_MECHANICS.md (Five-File Contract, File 4): this is the only
Python file the agent edits while the experiment loop is active. The agent:

    - reads parameters via :func:`prepare.get_parameters`,
    - never re-parses ``parameters.json`` directly,
    - never re-defines symbols imported from :mod:`prepare`,
    - never modifies :mod:`prepare` or any spec ``.md`` file.

Phase 1 scope: experiment-loop skeleton stubs only. Real discovery
(Phase 3+), scoring (Phase 5+), actionability (Phase 8), snapshots (Phase 9),
and the overall loop (Phase 10) are :class:`NotImplementedError` stubs that
are deliberately NOT raised at import time so that
``python -c "import research"`` succeeds without a database or .env present
(see risk H3 in ``reviews/01_phase1_scaffolding/01_risk_review.md``).
"""

from __future__ import annotations

import logging
from typing import Any

# Importing prepare gives the agent its read-only handle to the immutable
# layer. Importing it does NOT open a database connection — the connection
# is lazy, inside :func:`prepare.get_connection`. This keeps `import research`
# safe to run in CI / smoke tests with no DATABASE_URL set.
import prepare  # noqa: F401 — re-exported intentionally for the agent.

log = logging.getLogger("research")


# ---------------------------------------------------------------------------
# Phase 3+: discovery
# ---------------------------------------------------------------------------
def run_discovery_cycle(market: str) -> None:
    """Discover parcels in a target market. Phase 3+."""
    raise NotImplementedError(
        "Discovery is not implemented at Phase 1; see BUILD_PHASES.md Phase 3"
    )


# ---------------------------------------------------------------------------
# Phase 5+ / Phase 7: scoring (S1..S12). S4/S5/S6 in Phase 7.
# ---------------------------------------------------------------------------
def score_parcel(parcel_id: str) -> dict[str, Any]:
    """Compute sub-scores S1..S12 for a parcel. Phase 5+ (S4/S5/S6 in Phase 7)."""
    raise NotImplementedError(
        "Scoring is not implemented at Phase 1; see BUILD_PHASES.md Phase 5/7"
    )


# ---------------------------------------------------------------------------
# Phase 8: actionability and strategy fit
# ---------------------------------------------------------------------------
def run_actionability_screen(parcel_id: str) -> dict[str, Any]:
    """Apply the four-gate actionability screen. Phase 8."""
    raise NotImplementedError(
        "Actionability screen is not implemented at Phase 1; see BUILD_PHASES.md Phase 8"
    )


def assess_strategy_fit(parcel_id: str) -> dict[str, Any]:
    """Tag a parcel with strategy fit ratings. Phase 8."""
    raise NotImplementedError(
        "Strategy fit is not implemented at Phase 1; see BUILD_PHASES.md Phase 8"
    )


# ---------------------------------------------------------------------------
# Phase 9: snapshots and memos
# ---------------------------------------------------------------------------
def generate_snapshot(parcel_id: str) -> str:
    """Render the per-parcel investment thesis snapshot. Phase 9."""
    raise NotImplementedError(
        "Snapshot generation is not implemented at Phase 1; see BUILD_PHASES.md Phase 9"
    )


def generate_strategy_memo(market: str) -> str:
    """Render the per-market strategy memo. Phase 9."""
    raise NotImplementedError(
        "Strategy memo is not implemented at Phase 1; see BUILD_PHASES.md Phase 9"
    )


# ---------------------------------------------------------------------------
# Phase 10: the experiment loop
# ---------------------------------------------------------------------------
def experiment_loop() -> None:
    """The Karpathy-pattern experiment loop. Phase 10.

    When implemented, the loop will (per AUTORESEARCH_MECHANICS.md):

        1. Call :func:`prepare.verify_parameters_unchanged` at the top.
        2. For each experiment, wrap the subprocess in
           :func:`prepare.run_with_os_timeout` (authoritative budget).
        3. Read the metric via :func:`prepare.calculate_actionable_pipeline_count`
           and :func:`prepare.calculate_confidence_weighted_pipeline`.
        4. Append a row to ``experiment_log.tsv``.
    """
    raise NotImplementedError(
        "The experiment loop is not implemented at Phase 1; see BUILD_PHASES.md Phase 10"
    )


# ---------------------------------------------------------------------------
# Phase 1 demonstration: read-only access to the immutable parameters layer.
# ---------------------------------------------------------------------------
def _print_phase1_status() -> None:
    """Print enough state to prove the immutable layer is wired correctly."""
    params = prepare.get_parameters()
    threshold = params["composite_threshold"]
    print("research.py — Phase 1 scaffold; experiment loop not yet implemented.")
    print(f"composite_threshold (from parameters.json, frozen): {threshold}")


if __name__ == "__main__":
    _print_phase1_status()
