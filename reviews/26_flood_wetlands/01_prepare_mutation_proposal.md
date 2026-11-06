# Prepare-mutation PROPOSAL: the `parcel_hazards` table

> **For the human, between runs only.** Nothing in this stage edits
> `prepare.py` or runs this DDL against a shared database
> (AUTORESEARCH_MECHANICS.md, "When Mutating prepare.py"). Until a human
> applies it, every consumer keeps the H4/H6 stub behaviour byte-identically
> (`00_design.md`, FW-03). Model names are omitted.

## What to add to prepare.py

Two constants, with exactly these names (the code gates on the first one:
`hazards.enabled()` is true iff `prepare._DDL_PARCEL_HAZARDS` exists and
names `parcel_hazards`), placed after `_DDL_EXPERIMENT_LOG_MIRROR`, and
both appended to `_ALL_DDL` after `_DDL_EXPERIMENT_LOG_MIRROR` (the table
references `parcels`, which `_ALL_DDL` creates first):

```python
_DDL_PARCEL_HAZARDS = """
CREATE TABLE IF NOT EXISTS parcel_hazards (
    parcel_id TEXT PRIMARY KEY REFERENCES parcels(parcel_id) ON DELETE CASCADE,
    nfhl_parcel_geom_md5 TEXT,
    nfhl_parcel_sf NUMERIC,
    nfhl_mapped_sf NUMERIC,
    nfhl_undetermined_sf NUMERIC,
    sfha_sf NUMERIC,
    floodway_sf NUMERIC,
    x500_sf NUMERIC,
    sfha_pct NUMERIC,
    floodway_pct NUMERIC,
    x500_pct NUMERIC,
    nfhl_zone_sf JSONB,
    zones TEXT[],
    bfe_ft NUMERIC,
    lomr_status TEXT,
    nfhl_as_of TEXT,
    nfhl_source TEXT,
    nfhl_fetched_at TIMESTAMPTZ,
    sfha_geom geometry(MultiPolygon, 2240),
    nwi_parcel_geom_md5 TEXT,
    nwi_parcel_sf NUMERIC,
    wetland_sf NUMERIC,
    wetland_pct NUMERIC,
    wetland_deep_excavated_sf NUMERIC,
    wetland_type_sf JSONB,
    nwi_as_of TEXT,
    nwi_source TEXT,
    nwi_fetched_at TIMESTAMPTZ,
    wetland_geom geometry(MultiPolygon, 2240)
);"""

_DDL_PARCEL_HAZARDS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_hazards_sfha_geom ON parcel_hazards USING GIST(sfha_geom);",
    "CREATE INDEX IF NOT EXISTS idx_hazards_wetland_geom ON parcel_hazards USING GIST(wetland_geom);",
)
```

and in `_ALL_DDL`: `_DDL_EXPERIMENT_LOG_MIRROR, _DDL_PARCEL_HAZARDS,
*_DDL_PARCEL_HAZARDS_INDEXES, *_DDL_INDEXES,`.

The SQL alone (for `STORAGE_ARCHITECTURE.md`, which prepare.py mirrors):

```sql

CREATE TABLE IF NOT EXISTS parcel_hazards (
    parcel_id TEXT PRIMARY KEY REFERENCES parcels(parcel_id) ON DELETE CASCADE,
    nfhl_parcel_geom_md5 TEXT,
    nfhl_parcel_sf NUMERIC,
    nfhl_mapped_sf NUMERIC,
    nfhl_undetermined_sf NUMERIC,
    sfha_sf NUMERIC,
    floodway_sf NUMERIC,
    x500_sf NUMERIC,
    sfha_pct NUMERIC,
    floodway_pct NUMERIC,
    x500_pct NUMERIC,
    nfhl_zone_sf JSONB,
    zones TEXT[],
    bfe_ft NUMERIC,
    lomr_status TEXT,
    nfhl_as_of TEXT,
    nfhl_source TEXT,
    nfhl_fetched_at TIMESTAMPTZ,
    sfha_geom geometry(MultiPolygon, 2240),
    nwi_parcel_geom_md5 TEXT,
    nwi_parcel_sf NUMERIC,
    wetland_sf NUMERIC,
    wetland_pct NUMERIC,
    wetland_deep_excavated_sf NUMERIC,
    wetland_type_sf JSONB,
    nwi_as_of TEXT,
    nwi_source TEXT,
    nwi_fetched_at TIMESTAMPTZ,
    wetland_geom geometry(MultiPolygon, 2240)
);

CREATE INDEX IF NOT EXISTS idx_hazards_sfha_geom ON parcel_hazards USING GIST(sfha_geom);
CREATE INDEX IF NOT EXISTS idx_hazards_wetland_geom ON parcel_hazards USING GIST(wetland_geom);
```

## Columns

| Column | Written by | Meaning |
|---|---|---|
| `parcel_id` | both | `parcels.parcel_id`; the row is deleted with the parcel |
| `nfhl_parcel_geom_md5` / `nwi_parcel_geom_md5` | NFHL / NWI | `md5(ST_AsBinary(parcels.geometry))` at import; a different current value = stale (FW-04) |
| `nfhl_parcel_sf` / `nwi_parcel_sf` | NFHL / NWI | area (SF, SRID 2240) of `ST_CollectionExtract(ST_MakeValid(geometry), 3)`, the geometry S2 and site fit use |
| `nfhl_mapped_sf` | NFHL | area covered by any NFHL polygon other than Zone D / AREA NOT INCLUDED |
| `nfhl_undetermined_sf` | NFHL | area of Zone D / AREA NOT INCLUDED |
| `sfha_sf`, `floodway_sf`, `x500_sf` | NFHL | union areas, SiteFit's precedence: floodway; SFHA including floodway; 0.2% zone outside the SFHA |
| `sfha_pct`, `floodway_pct`, `x500_pct` | NFHL | display only (4 decimals); decisions use the SF columns, unrounded ratio |
| `nfhl_zone_sf` | NFHL | JSONB, `"ZONE\|SUBTYPE\|T/F"` -> union SF (2 decimals) |
| `zones` | NFHL | sorted distinct FLD_ZONE values with at least 1 SF on the parcel |
| `bfe_ft` | NFHL | highest STATIC_BFE of the SFHA polygons, the -9999 sentinel dropped |
| `lomr_status` | NFHL | `intersects` / `none` / `not_read` (LOMR layer or S_LOMR file not available) |
| `nfhl_as_of`, `nwi_as_of` | NFHL / NWI | DFIRM_ID + VERSION_ID of the polygons on the parcel (NFHL), the .dbf header date or the layer's last edit date; never empty (FW-20) |
| `nfhl_source`, `nwi_source` | NFHL / NWI | which route wrote it (county/state file or REST layer) |
| `nfhl_fetched_at`, `nwi_fetched_at` | NFHL / NWI | when this content was first written (an unchanged re-import does not touch the row) |
| `sfha_geom`, `wetland_geom` | NFHL / NWI | the clipped union (SRID 2240 MultiPolygon) site fit deducts; NULL under 1 SF |
| `wetland_sf`, `wetland_pct` | NWI | union of every NWI polygon on the parcel |
| `wetland_deep_excavated_sf` | NWI | union of deepwater types (Lake, Riverine, Estuarine and Marine Deepwater) and excavated (`x`) classes |
| `wetland_type_sf` | NWI | JSONB, WETLAND_TYPE -> union SF |

## Rationale

H4/H6 need per-parcel facts that change only between runs (a new FIRM, a
new NWI release) and must not be fetched during a run (SR-11, and both
hosts' robots.txt). A table written by the between-runs import, keyed by
parcel and stamped with the parcel geometry it was computed on, is the
smallest honest store: no column on `parcels` (whose UPSERT would rewrite
or drop it), no metric SQL change (gate 4 already exists), and no geometry
that the agent sandbox reads.

## Migration notes

- Additive only: `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT
  EXISTS`; `prepare.apply_schema` stays idempotent. No backfill.
- Applying the DDL with the table EMPTY changes no score: H4/H6 stay
  passes; only the flag text changes (the discovery "unjoined" stubs stop,
  scoring writes "no hazards import for this parcel" data_gap flags;
  `00_design.md`). The old open stub flags stay open (no new write
  semantics); a human may resolve them.
- **The first populated `make hazards-import` IS the intended metric
  change**: H4/H6 start failing parcels through gate 4 and, for verified
  jurisdictions, site fit deducts the hazard areas. Run it between runs,
  then start a new run and baseline.
- Rollback: drop the two constants from prepare.py; the table may stay (no
  reader once the gate is off) or be dropped (`DROP TABLE parcel_hazards`).
- `.github/workflows/validate-phase1.yml` re-runs `python prepare.py` on
  any prepare.py change: the new DDL is exercised against the throwaway
  PostGIS service container. `make verify` / `make status` read named
  tables only; an additional table is not misreported (checked on a local
  database with the DDL applied, `03_decision_note.md`).
- The CHECK-free design follows the repo's existing tables; the import
  refuses a row without as-of values in code (FW-20).

## Tested

In this stage the DDL ran only in databases created for the purpose
(`createdb -T landtest <own>`) and in a scratchpad clone with a patched
prepare.py; never on `landtest` itself or any hosted database. Those
databases were dropped afterwards (`03_decision_note.md`).
