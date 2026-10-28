"""Land screener: find buyable, buildable industrial land from free public data.

A plain pipeline, run on demand:

    fetch parcels -> attribute screens -> map overlays (flood, wetlands,
    easements, contamination, access, rail, zoning) -> slope -> hard screens
    -> rank -> report (CSV, map, one-page briefs)

It is separate from the AutoResearch loop in the repo root (research.py,
prepare.py, runner.py) and shares no code or tables with it.

See screener/README.md.
"""
