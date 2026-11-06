# Site-fit adjacency buffers — design

> **Tier 2** (touches site fit, which feeds BTS in verified jurisdictions,
> the runner, and proposes new storage). The pre-change risk register
> (BUF-01 to BUF-24) and the disposition of every item is `02_risk_review.md`;
> the storage and rules-schema proposals for the human are
> `01_prepare_mutation_proposal.md`; evidence, live numbers and what could
> not be done are in `03_decision_note.md`. Model names are omitted.

## Why

Until this stage every site-fit projection listed the district's adjacency
buffers (residential adjacency, Fairburn's 50 ft from other districts and
25 ft along public ROW, South Fulton Sec. 405 buffers plus the 10 ft
improvement setback, height-based extra setbacks) as "not applied": the
`parcels` table holds only candidate parcels, so neighbour zoning was
unknown. The goal is to apply them per side where neighbour zoning can be
known honestly, and to say so where it cannot.

## Pieces

1. **`adjacency.py`** (new, protected, immutable during a run; imports only
   `prepare` and `zoning`; never `research`, `runner`, `site_fit`, the import
   tools or `screener/`). It holds:
   - the **storage gate**: `enabled()` is true only once prepare.py's SOURCE
     TEXT assigns `_DDL_PARCEL_ADJACENCY` naming `parcel_adjacency` (read once
     at load, the hazards pattern; an in-process assignment changes nothing;
     no SQL). `schema_problem()` checks the DDL carries every column read and
     the `geometry(MultiLineString, 2240)` segment type;
   - the **fixed classification constants** (`ABUT_TOL_FT` 20 ft,
     `MIN_SLIVER_WIDTH_FT` 3 ft, `MIN_OVERLAP_AREA_SF` 50 SF,
     `MIN_SEGMENT_LEN_FT` 10 ft, `MISALIGNMENT_ALLOWANCE_FT` 5 ft,
     `QUAD_SEGS` 2, `VERTEX_QUAD_SEGS` 16, `VERTEX_FACTOR`
     1/cos(pi/64), `PLACEHOLDER_CODES`); none is a CLI option or reachable
     from research.py;
   - the **buffer-key allow-list** `BUFFER_KEYS`: (rules table, key) ->
     neighbour classes, role (side / rear / buffer / additive / text) and the
     rule text it reads (every quoted fragment is checked to appear verbatim
     in zoning_rules.json);
   - **classification at read time**: neighbour identity (rules table,
     district) through `zoning.normalize`, the governing jurisdiction and its
     `district_conversions` (as `zoning.district_for_stored` resolves a
     parcel); "same district" compares codes with hyphens ignored (`M-1` =
     `M1`); "other district" = not the parcel's CURRENT stored district;
     residential / agricultural / industrial / non-residential ONLY from a
     human `neighbour_classes` table in zoning_rules.json (a proposal;
     absent); the contested readings ONLY from that table's `readings`
     (see "Readings for the human to confirm"); a code that is not readable
     (`UNKNOWN`, `TBD`, `N/A`, no letter or digit) is no district: the side
     is "adjacency unknown (neighbour district code unreadable)"; for a
     parcel on a governed jurisdiction's own table (an unincorporated FID
     parcel) a neighbour code is tried under that table too;
   - the **per-parcel plan** `plan_parcel(parcel_id, stored, rows, setback)`:
     which stored rows to deduct, by what distance, and every text.

2. **`adjacency_import.py`** (new, protected operator tool, `make
   adjacency-import`, between runs only). Reads the `role="zoning"` layers a
   human registered in `sources.json` (`county_parcel_data.fulton_ga.
   zoning_sources`, through `zoning_import.load_config`). See "Import" below.

3. **`site_fit.py`** (protected; one new protected import, `import
   adjacency as _adj`, recorded in `test_site_fit.BoundaryTests`). See
   "Deduction" below.

4. **`reporting.py`**: the memo footnote and highlight line use
   `site_fit.adjacency_text` / `_projection_footnote()`; gate off they are
   the 595aa08 strings exactly.

5. **`runner.py`**: `_adjacency_fingerprint` / `_adjacency_audit` (BUF-12).
   Once enabled, `evaluate` fingerprints `parcel_adjacency` before the
   sub-cycles and after them; a difference (or an unreadable table) raises
   `AdjacencyAuditFailed`, so the experiment is a crash, discarded and purged.
   Gate off: no connection, no SQL.

`research.py` is NOT changed: the buffers act inside `site_fit.project_parcels`,
which research.py already calls only for verified parcels. A static guard
(`static_guards.absent_module_problems`) fails the suite if research.py names
`adjacency`, `adjacency_import`, `site_fit._adj` or the table, in any case,
in a literal or in a string folded from constants (`+`, `%`, f-strings,
`join`, `format`, module-level names), holds a literal of 4+ characters that
is a piece of those names, or holds `adjacen` outside an exact allow-list
(the S11 rail-adjacency name and label, the H5 docstring). The residual
(pieces under 4 characters, text built at run time, in-transaction hiding)
is recorded in SR-16.

## Storage (proposal only)

No adjacency storage exists and prepare.py is human-only. The proposed table
(`01_prepare_mutation_proposal.md`, the same text as `adjacency.PROPOSED_DDL`,
pinned by a test) stores per parcel: the boundary `segment`
(`geometry(MultiLineString, 2240)`, CHECKed SRID), `neighbour_kind`
(`zoned` / `row` / `uncovered`), the RAW `neighbour_district` and the source's
`neighbour_jurisdictions`, `source`, `source_as_of`, `parcel_geom_md5`
(`md5(ST_AsBinary(geometry))`, the parcel_hazards freshness test) and
`fetched_at`, with a btree index on `parcel_id` and a GiST index on `segment`.
No neighbour parcel id, owner, address or attribute other than the district
code is stored. No `parcels` column is added. Segments are geometries, not
vertex-index ranges (those move under `ST_MakeValid` and re-discovery).

Both states were tested only in throwaway local databases (the DDL applied
there by the live check and in the live clone), each dropped afterwards.

## Import

For every Fulton parcel inside EPSG:2240's area of use (made-valid centroid,
`hazards.in_area_of_use`):

1. **Fetch** (per registered zoning source): robots.txt through
   `rules_scrape.PoliteFetcher.robots_for`, unchanged (this tool's token and
   the AI-agent tokens; 404/410 allow, anything that is not a clean text 2xx
   denies all); layer metadata (zoning field present, polygon geometry,
   object-id field from `objectIdField` or the one `esriFieldTypeOID` field);
   object ids by envelope over a 0.01 degree grid of the parcels' envelopes
   padded by 0.001 degree; features by id in pages of at most 100, with
   `outFields` = object-id field + zoning field only (asserted on every
   request in the tests), `outSR=4326`, `geometryPrecision=7`. A short or
   truncated page, a missing field, a feature without polygon geometry, a
   service error or more than 400 requests fails the WHOLE source (nothing of
   it is used, exit 4, named). Features with a vertex outside EPSG:2240's area
   of use are rejected and counted. A `join` source whose layer has no polygon
   geometry is skipped, named, and its territory reads as unknown.
2. **Compute** (PostGIS, SRID 2240, TEMP tables with GiST indexes, one
   transaction): the parcel geometry site fit buffers; the outward ring
   `buffer(g, 20) - g`; per (source, district) the ring part its polygons
   cover, **opened** by 3 ft (`ST_Buffer(ST_Buffer(c, -1.5), 1.5)`), so every
   part under 3 ft wide is a sliver even where the same district is solid
   elsewhere on the ring (a cover is solid when the opened part is at least
   50 SF); per edge and source the **reach**: the strip within 5 ft of the
   edge plus 5 ft around every gap of the source's opened covers that touches
   the edge, so a neighbour behind more than 5 ft of another polygon of the
   same layer (an own district digitised to a street centreline) does not
   abut, while a layer offset from the parcel fabric (an uncovered gap) still
   does; every
   boundary edge (outer rings and holes of every part, `ST_DumpSegments` of
   `ST_ForcePolygonCCW(g)`, so the interior is on each edge's left) gets a
   one-sided flat outward band (`ST_Buffer(edge, 20, 'side=right
   endcap=flat')`); each polygon piece of band intersect solid cover
   intersect reach is
   projected back onto its edge (`ST_LineLocatePoint` extremes,
   `ST_LineSubstring`), so only the facing part of the edge is stored, never
   wrapped around a corner, never bridged across two pieces, and a neighbour
   touching only the corner wedge (diagonal) is not adjacent; pieces are
   merged per (source, district) and pieces under 10 ft dropped; boundary not
   covered by any zoned segment is stored as `uncovered`.
3. **Write**: per parcel, the new row set is compared with the stored one
   (md5 of the sorted row texts, segment WKB included); only changed parcels
   are deleted and re-inserted, so a re-run with the same inputs changes 0
   rows (SR-9; the live check (g) re-runs and compares every row, ids and
   fetched_at included).
4. **Output**: counts only, per source and per jurisdiction (parcels with an
   other-district side, residential / agricultural through the human table,
   unclassified neighbours, unknown sides, slivers (whole covers and thin
   parts of solid ones), parcels with a side facing two NEIGHBOUR districts,
   and, separately, parcels where the parcel's own district polygon
   overhangs a neighbour's side).

Exit codes: 0 imported; 1 refused (table not enabled or not in the database,
schema problem, sources.json key missing or malformed); 2 no database or a
database error (nothing written: one transaction); 3 a run has its baseline
(TSV, or the mirror's run baseline; off a run branch, a newest mirror row that
is not a halt, unless `--no-open-run-check`); 4 imported but at least one
source FAILED (named on the last line). Each is tested
(`test_adjacency_import.RunTests`).

## Deduction (site fit)

`project_parcels` with the gate on: one batched read
(`adjacency._SQL_ADJACENCY_FOR_PARCELS`, `parcel_id = ANY(%s)`, freshness by
md5) through site_fit's savepoint-safe `_query`; then `plan_parcel` per parcel
(pure). Per setback group, parcels with nothing to deduct use the frozen
statement they used before (identical ring text, so the fit cache key is
unchanged); parcels with applied segments use one of two composed statements:

- `_SQL_BUFFERED_FOR_FIT_ADJ` and `_SQL_BUFFERED_FOR_FIT_HAZARDS_ADJ` are
  built at load by `_with_adjacency` from the frozen `_SQL_BUFFERED_FOR_FIT`
  and `_SQL_BUFFERED_FOR_FIT_HAZARDS`: the frozen body is embedded verbatim as
  a subquery (`c1`) and a CTE `adj` adds, per parcel, the union of every
  applied segment buffered by its own distance as a **strip with flat ends**
  (`ST_Buffer(segment, d, 'endcap=flat join=round quad_segs=2')`: exact on
  straight sides, no disc past the end of an abutting part) and a polygon of
  circumradius `d * VERTEX_FACTOR` (16 segments per quarter; contains the
  circle of radius d, overshoots it by under 0.0013 d, 0.14 ft at 110 ft) at
  every join (interior vertex), at the ends of a closed line, and at an end
  shared with another applied segment of the parcel (two abutting parts
  meeting at a corner). So no fitted envelope enters the strip of an
  abutting part. `inner_a = inner_g - ad` (the buildable area); `fit_a` is `fit_g -
  ad`, or, above the vertex limit, the simplification (1 ft) of
  `(inner_g buffered by -2 ft) - (ad buffered by +2 ft)`, inside `inner_a`.
- Placeholders are named (`{adj_ids}`, `{vertex_factor}`, ...) and compiled to `%s`
  at load with their order kept; parameters are built by name
  (`_named_params`), and a test proves the named construction reproduces the
  frozen positional order of both base statements. SR-2 holds (module-level
  constants, `%s` only, no `{` left).
- A failed composed read falls back to the frozen statement, labelled
  "adjacency data unreadable" (never an error status, BUF-11). A failed
  adjacency read plans nothing (unreadable).

### Composition per side (BUF-02, BUF-06, BUF-07)

For a stored row with neighbour classes C: buffer = the largest side / rear /
buffer value of an allow-listed key applying to a class in C, plus every
additive key of that class; per-side distance = max(uniform setback, buffer).
A distance at or under the uniform setback deducts nothing and is named
("residential buffer 20 ft within the 40 ft setback on 1 side"). The uniform
setback logic (`_rules_for`) is untouched; buffers only subtract.

| Rules table | Keys read | Classes | Per side (uniform setback) |
|---|---|---|---|
| south_fulton (and FID parcels it governs) | side 50, rear 100, + plus_improvement_setback 10 | residential, agricultural | side 60, rear 110; with `readings.side_rear` "max" **110** on every side, "side" 60; unset: not applied (named), vs 40 |
| fid (unincorporated parcels) | the same keys | residential, agricultural | the same, by FID's own reading |
| union_city | buffer 75 + plus_setback_measured_from_buffer 20 | residential, agricultural | **95** vs 50; front exemption not applied (named) |
| fairburn | side_rear_yard_abutting_residential 50; buffer_abutting_other_districts 50; buffer_along_all_public_ROW 25 | residential; other_district (only with `readings.other_district`); row | **50** vs 35 (M-1) / 40 (M-2): Sec. 80-369 counts buffers within yards, so max, never 35 + 50; ROW 25 within the setback anyway, and no ROW source |
| atlanta | side 20, rear 20; screen (text) | residential | 20, within the uniform 40: no-op; screen named, not applied |

Any key outside the allow-list (or allow-listed for another table), or a value
that is not a non-negative number, is never applied and is named.

### Texts

Gate off: `NOT_MODELLED` and `RES_BUFFERS` verbatim (pinned), and every
projection, memo line and footnote as at 595aa08. Gate on: `RES_BUFFERS` is
replaced on every projection by a summary (`Projection.adjacency`), and the
detail lines are appended; "buffers listed for ..., not applied" is dropped
only when a plan was actually computed. Lines carry class, distance, side
count, source and fetch date only, never a parcel id or a district code:

- `adjacency buffers applied where neighbour zoning is known (<source>
  fetched <date>): residential 110 ft on 1 side; 2 sides adjacency unknown (no
  zoning polygon within 20 ft; not treated as ROW or as no neighbour)`; plus
  `; N sides adjacency unknown (neighbour district code unreadable)`
- `buffer measured as a strip along the abutting boundary (flat ends)`
  (whenever something is deducted), `rear value applied (frontage
  unknown)` / `side value applied on every side (frontage unknown; reading
  set in the rules)`, `... not applied on N sides (side/rear reading not
  decided in the rules)`, `other-district buffer not applied on N sides
  (which other districts: reading not decided in the rules)`, `... (industrial
  neighbour)`, `Fairburn Sec. 80-372 read as other non-industrial districts
  (reading set in the rules)`, `Union City front-yard exemption
  not applied (frontage unknown)`, `Fairburn Sec. 80-372 read literally
  (includes other industrial districts)`, `ROW buffer not applied (no ROW
  source); 25 ft is within the 35 ft setback anyway`,
  `residential/agricultural buffer not applied on N sides (neighbour
  classification not in the rules)`, `residential USE not checked
  (zoning-based adjacency only)` (South Fulton, FID), `height-based setback
  not applied (building height not modelled)` (wherever the district has a
  height rule, number or prose: today South Fulton, FID, Atlanta),
  `across-street buffers not applied (rules do not say across a street)`,
  split-parcel and other-district-unknown lines;
- states without a plan: not imported, stale, unreadable, no buffer in the
  rules for a district the rules list (`adjacency buffers: none listed in the
  rules for this district`), no district rule for the parcel (no stored
  zoning, an unparsed or unlisted code: `adjacency buffers not applied (no
  district rule for this parcel)`), zoning rules unavailable, no buildable
  geometry read.

Memo footnote (gate on): `adjacency buffers applied where neighbour zoning is
known (each line says which class, distance and how many sides; unknown sides
listed, never treated as ROW or as no neighbour)` replaces `RES_BUFFERS`.

## Readings for the human to confirm

The two readings the rules text leaves open are SWITCHES in the human-owned
`neighbour_classes` table (`readings`, schema in
`01_prepare_mutation_proposal.md`), never defaulted in code: unset, the
buffer they govern is not applied and every affected projection says
"reading not decided in the rules".

1. **Fairburn Sec. 80-372** (`readings.other_district`): `"literal"` = any
   district other than the parcel's own, other industrial districts and
   districts of another jurisdiction included (zoning_rules.json marks this
   wording ambiguous, "may include other industrial"); `"non_industrial"` =
   only other districts the table classifies and does not list under
   `industrial` (an unclassified neighbour is not applied, named). The live
   report counts "other district = industrial" sides separately (BUF-14).
2. **Side vs rear** (`readings.side_rear`) while frontage is unknown:
   `"max"` = the rear value on every abutting side (South Fulton/FID 110 ft),
   `"side"` = the side value (60 ft). Only districts with differing side and
   rear values need it (South Fulton, FID); Union City (one buffer value),
   Fairburn and Atlanta (side = rear) do not. Union City's front exemption is
   not applied (named).

Fixed in code and named (a change is a Tier-2 code change, between runs):

3. **20 ft abutment tolerance, 5 ft reach**: a neighbour polygon must lie
   within 20 ft of the edge AND within 5 ft of it, or of a gap of its layer
   that touches the edge. That catches a layer offset 15 ft from the parcel
   fabric (an uncovered gap) and drops a neighbour behind an own-district
   polygon digitised to a street centreline (Atlanta excludes an intervening
   street; no rule says "across a street"). A real neighbour strip under
   5 ft wide in front of another district reads as the strip's district
   only; a narrow alley under 20 ft that the layer leaves uncovered reads as
   abutting.
4. **Strip, not distance** (review finding 6): the buffer is a strip along
   the abutting boundary part with flat ends (`buffer measured as a strip
   along the abutting boundary (flat ends)` on every deducting projection);
   the quoted rules ("adjacent to", "side/rear yard abutting", "abut")
   describe a strip along the shared line. Where two abutting parts meet,
   the corner is filled (round join).
5. **Multi-jurisdiction layers** (Fulton layer 34 serves South Fulton and
   FID): a code is the parcel's own district when it resolves to it under any
   of the layer's jurisdictions (and, for an unincorporated FID parcel, under
   FID's own table); its class is the strictest the tables give.
6. **Agricultural neighbours** (Union City, South Fulton, FID): quoted rule
   text, not a reading ("Residential OR Agricultural district"; Sec.
   405.01(b)(1) lists AG-1); which codes are agricultural is the human table.
7. **Height**: no prototype has a clear height above 50 ft, so the 12 ft per
   foot above 50 ft rule cannot be shown to apply; it is never parsed or
   applied, and named wherever a height rule exists.

## Terms and disclaimers of the layers (BUF-19)

Only zoning layers already registered by a human (the
reviews/21_zoning snippet) are read; Zoneomics, Municode and listing sites are
never contacted (rules_scrape's denylist and terms blocks). On 2026-11-06 the
robots.txt of the four ArcGIS Online hosts (services, services3, services7,
services8.arcgis.com: South Fulton, Fairburn, Union City, East Point)
answered HTTP 403, which the unchanged policy reads as deny-all, so those
sources fail without a data request. gis.atlantaga.gov and
gismaps.fultoncountyga.gov answered 404 (allow). What their layer metadata
says about use is recorded in `03_decision_note.md`. City zoning layers are
screening data, not an official zoning verification; every projection names
its source and fetch date.
