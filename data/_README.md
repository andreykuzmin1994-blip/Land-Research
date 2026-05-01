# `data/` — bundled reference data for the Land-Research agent

> Static, public-domain datasets that the scoring engine reads at runtime.
> Distinct from `sources/` (gitignored API response cache) and from CoStar
> exports (separate ingestion pipeline per `COSTAR_INGESTION_CONTRACT.md`).

## Files

### `oz_ga_stub.geojson` — Georgia Opportunity Zones (STUB)

Used by `research.py:_compute_s10_incentives()` to determine whether a
parcel centroid falls inside a Georgia Opportunity Zone census tract.

**This file is a STUB.** It contains 2 polygon features that approximate
the South Fulton (Campbellton-Fairburn corridor) and Clayton County
(Airport / I-85 South corridor) areas where industrial OZ census tracts
exist. The polygons are NOT precise tract boundaries — they are deliberately
loose so the Phase 5 code path is exercised end-to-end against the
discovery test fixtures.

**Human action required before relying on S10 signal in production:**

1. Download the official OZ designations from HUD:
   - Source URL: <https://opportunityzones.hud.gov/>
   - Data tab → "Opportunity Zone Resources" → census tract list (CSV) +
     TIGER/Line tract polygons (Shapefile / GeoJSON)
2. Filter to Georgia (state FIPS = 13).
3. Convert to GeoJSON FeatureCollection with one Polygon Feature per
   designated tract. Each Feature should have a `properties.GEOID` (the
   11-digit census tract identifier) and `properties.tract_name`.
4. Replace this file with the result, keeping the same filename.
5. Run the Phase 5 test suite (`python3 -m unittest tests.test_discovery
   -v`) — the OZ unit tests are bbox-tolerant by design and should still
   pass against the real data.

The agent's startup will emit a `flagged_items` row noting "OZ data is
stub only" until a maintainer flips the `_STUB` marker in
`oz_ga_stub.geojson` (set `properties.is_stub` to `false` after replacing
with real HUD data).

### Future data files

When Phase 5+ adds real S1/S3/S7/S11/S12 implementations, additional
public-domain reference datasets will land here:

- `interstate_centerlines_ga.geojson` — for S1 interstate proximity
- `usgs_3dep_atlanta.tif` — for S3 topography (or a query helper)
- `census_lodes_ga.csv` — for S7 labor pool
- `class1_railroad_ga.geojson` — for S11 rail adjacency
- `intermodal_facilities_ga.geojson` — for S12 demand generators

Each will have its own README section in this file.
