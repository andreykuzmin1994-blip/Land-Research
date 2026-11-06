# Prepare-mutation PROPOSAL: the `parcel_adjacency` table, and a rules-file schema addition

> **For the human, between runs only.** Nothing in this stage edits
> `prepare.py` or `zoning_rules.json`, or runs this DDL against a shared
> database (AUTORESEARCH_MECHANICS.md, "When Mutating prepare.py"). Until a
> human applies the DDL, every projection, score, snapshot and memo is
> byte-identical to 595aa08 (`00_design.md`, BUF-03; `tests/adjacency_golden_check.py`).
> Until a human adds the class table below, no residential or agricultural
> buffer is ever applied, even with the table populated. Model names are omitted.

There are two independent human acts. Each is between runs only, and once any
zoning jurisdiction is verified each changes BTS and so the metric: start a
new run and baseline afterwards.

1. **Storage** (prepare.py): the `parcel_adjacency` table. Without it nothing
   is read and nothing changes.
2. **Neighbour classes** (zoning_rules.json): a per-jurisdiction table of
   which district codes are residential, agricultural or non-residential.
   Without it only Fairburn's "abutting other districts" buffer can apply
   (it needs no classification, only a code comparison).

## 1. What to add to prepare.py

Two constants, with exactly these names. The code gates on the first one:
`adjacency.enabled()` is true iff prepare.py's source text assigns a string
`_DDL_PARCEL_ADJACENCY` at module level that names `parcel_adjacency`. The text is
read from disk once, when `adjacency` loads, so a runtime assignment to
`prepare._DDL_PARCEL_ADJACENCY` changes nothing (the hazards gate's rule). Place
them after `_DDL_EXPERIMENT_LOG_MIRROR` (or after `_DDL_PARCEL_HAZARDS` if that
proposal was applied first), and append both to `_ALL_DDL` after
`_DDL_EXPERIMENT_LOG_MIRROR`: the table references `parcels`, which `_ALL_DDL`
creates first.

```python
_DDL_PARCEL_ADJACENCY = """
CREATE TABLE IF NOT EXISTS parcel_adjacency (
    adjacency_id BIGSERIAL PRIMARY KEY,
    parcel_id TEXT NOT NULL REFERENCES parcels(parcel_id) ON DELETE CASCADE,
    segment geometry(MultiLineString, 2240) NOT NULL CHECK (ST_SRID(segment) = 2240),
    neighbour_kind TEXT NOT NULL CHECK (neighbour_kind IN ('zoned', 'row', 'uncovered')),
    neighbour_district TEXT,
    neighbour_jurisdictions TEXT,
    source TEXT NOT NULL,
    source_as_of TEXT,
    parcel_geom_md5 TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    CHECK ((neighbour_kind = 'zoned') = (neighbour_district IS NOT NULL))
);"""

_DDL_PARCEL_ADJACENCY_INDEXES = (
    'CREATE INDEX IF NOT EXISTS idx_adjacency_parcel ON parcel_adjacency (parcel_id);',
    'CREATE INDEX IF NOT EXISTS idx_adjacency_segment ON parcel_adjacency USING GIST(segment);',
)
```

and in `_ALL_DDL`: `_DDL_EXPERIMENT_LOG_MIRROR, _DDL_PARCEL_ADJACENCY,
*_DDL_PARCEL_ADJACENCY_INDEXES, *_DDL_INDEXES,`.

The SQL alone (for `STORAGE_ARCHITECTURE.md`, which prepare.py mirrors):

```sql
CREATE TABLE IF NOT EXISTS parcel_adjacency (
    adjacency_id BIGSERIAL PRIMARY KEY,
    parcel_id TEXT NOT NULL REFERENCES parcels(parcel_id) ON DELETE CASCADE,
    segment geometry(MultiLineString, 2240) NOT NULL CHECK (ST_SRID(segment) = 2240),
    neighbour_kind TEXT NOT NULL CHECK (neighbour_kind IN ('zoned', 'row', 'uncovered')),
    neighbour_district TEXT,
    neighbour_jurisdictions TEXT,
    source TEXT NOT NULL,
    source_as_of TEXT,
    parcel_geom_md5 TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    CHECK ((neighbour_kind = 'zoned') = (neighbour_district IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_adjacency_parcel ON parcel_adjacency (parcel_id);
CREATE INDEX IF NOT EXISTS idx_adjacency_segment ON parcel_adjacency USING GIST(segment);
```

## Columns

| Column | Meaning |
|---|---|
| `adjacency_id` | row id; site fit passes the ids of the rows it applies, with their distances |
| `parcel_id` | `parcels.parcel_id`; the rows are deleted with the parcel (`ON DELETE CASCADE`) |
| `segment` | the part of the parcel boundary (outer rings and holes of every part of `ST_CollectionExtract(ST_MakeValid(geometry), 3)` in SRID 2240, the geometry site fit buffers) that faces this neighbour, `geometry(MultiLineString, 2240)`, US survey feet; CHECKed to SRID 2240 (BUF-09). A geometry, not a vertex index range: vertex indexes move under `ST_MakeValid` and re-discovery (BUF-18) |
| `neighbour_kind` | `zoned` (a fetched zoning polygon of a district faces the segment), `row` (public right-of-way from a human-registered ROW source; none exists, so never written today), `uncovered` (no fetched polygon faces the segment: adjacency unknown, never ROW, never "no neighbour"; BUF-04) |
| `neighbour_district` | the RAW district code of the neighbour polygon as the layer gives it (`zoned` rows only; CHECKed). Never a computed class (BUF-01): "residential" and "other district" are decided at read time |
| `neighbour_jurisdictions` | comma-separated jurisdiction keys of the source (sources.json `jurisdictions`); the read resolves the code under them as `zoning.district_for_stored` does (governing jurisdiction, district conversions) |
| `source` | the sources.json `zoning_sources` key the polygon came from; `coverage` for `uncovered` rows |
| `source_as_of` | the layer's last edit date when it publishes one |
| `parcel_geom_md5` | `md5(ST_AsBinary(parcels.geometry))` at import; a different current value = stale: nothing applied (BUF-13) |
| `fetched_at` | import time |

No neighbour parcel id, owner, address or feature attribute other than the
district code is stored (BUF-05, SR-8). No `parcels` column is added.

## Migration and impact

- Idempotent (`IF NOT EXISTS`); a new table only, no existing table or column
  changes; `apply_schema` on an existing database creates it empty.
- **Empty table** (DDL applied, no import yet): parcel_scores are identical to
  595aa08; every projection reads the table once per batch and says
  "adjacency buffers not applied (adjacency unknown: not imported)"; the ring
  text, so the fit cache key, is unchanged; the memo footnote's adjacency clause
  changes. `tests/adjacency_golden_check.py` proves both states against a
  frozen record of 595aa08.
- **Populated** (`make adjacency-import`, between runs): projections in every
  jurisdiction show what was applied. BTS changes only for parcels in a
  `verified` jurisdiction (the existing S9 gate). With every jurisdiction
  draft, the metric is unchanged.
- CI (`validate-phase1.yml`) runs `python prepare.py` against a throwaway
  PostGIS 16 / 3.4 container; the DDL uses only core PostGIS types. The import
  tool (not CI) uses `ST_DumpSegments` (PostGIS 3.2 or later) and the
  one-sided `ST_Buffer` option `side=right`.
- The runner fingerprints the table before and after every experiment once it
  is enabled and crashes (so discards) an experiment during which it changed
  (`runner._adjacency_audit`, BUF-12).
- `make verify` / `make status` are unaffected (they read no adjacency).

Tested in this stage only in throwaway local databases: `createdb -T landtest
<own>` and a fresh `liveadj_<hex>` per run of `tests/live_adjacency_check.py`,
the DDL above applied there, each dropped afterwards.

## 2. Proposed zoning_rules.json addition: `neighbour_classes`

The rules file holds the buffer VALUES, but which neighbour districts trigger a
residential buffer exists only as prose in cites and notes (South Fulton Sec.
405.01(b)(1): "AG-1, R-*, TR, A, A-L, NUP, CUP, MIX-with-residential and
existing single-family uses"; Union City: "Residential OR Agricultural";
Atlanta: "an R district"). The code never turns that prose into a code list
(BUF-01). The proposal is one optional object per jurisdiction, written and
cited by the human:

```json
"neighbour_classes": {
  "residential": ["R-*", "TR", "A", "A-L", "NUP"],
  "agricultural": ["AG-1"],
  "non_residential": ["M-1", "M-2", "C-1", "C-2"],
  "cite": "Sec. 405.01(b)(1); district list Sec. 203 Table 2-1"
}
```

The example values above are ILLUSTRATIVE ONLY, copied from the prose, and not
checked against the ordinances. Rules the code applies:

- Keys: `residential`, `agricultural`, `non_residential` (lists of district
  codes as the zoning layers spell them) and `cite` (required, non-empty). A
  malformed object reads as absent, with the reason in the projection.
- Codes are compared after `zoning.normalize` with hyphens ignored (`M-1` =
  `M1`). An entry ending in `*` is a literal prefix (`R-*` matches `R-3`,
  never `RM`).
- A code in none of the lists is "not classified": no buffer, named. Codes
  that are residential only conditionally (PD, CUP, MIX) are applied only if
  the human lists them.
- The table of the rules table the neighbour resolves to is used (an FID
  neighbour resolves to South Fulton's table, through `governing_jurisdiction`).
- Which buffer applies to which class is the allow-list in `adjacency.py`
  (`BUFFER_KEYS`), each entry citing the rule text it reads: South Fulton and
  FID side/rear/improvement setback apply to residential and agricultural
  (Sec. 405.01(b)(1) lists AG-1); Union City's buffer to residential and
  agricultural ("Residential OR Agricultural"); Atlanta's transitional yards to
  residential only ("an R district"); Fairburn's 50 ft to residential and,
  separately, to any other district.
- Like every rules edit, between runs only, and `verify_rules_committed`
  refuses an experiment while the file differs from git HEAD.

`zoning.validate_rules` already accepts unknown jurisdiction-level keys, so the
addition needs no code change.

## 3. Optional: a ROW source

Fairburn Sec. 80-367 asks a 25 ft buffer along all public ROW. No ROW layer is
registered in sources.json, and a gap between zoning polygons is never read as
ROW (BUF-04). The 25 ft value is at or under Fairburn's uniform 35/40 ft
setback, so it would deduct nothing today. If a human later registers a ROW
polygon layer, the import can write `row` rows; the read already applies the
`row` class (and still deducts nothing while the value is within the setback).
