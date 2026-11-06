# Flood (H4) and wetlands (H6) — design

> **Tier 2** (touches the metric through gate 4, the site-fit input of BTS,
> an external-service integration and the mutability contract). The
> pre-change risk register (FW-01 to FW-27, written by an independent
> reviewer) and the disposition of every item is `02_risk_review.md`; the
> storage proposal for the human is `01_prepare_mutation_proposal.md`; the
> decisions and the evidence are in `03_decision_note.md`. Model names are
> omitted.

## Why

program.md lists two hard filters that were stubs (data_gap flag, pass):

- **H4 Flood zone**: "Not in FEMA Zone A or AE (100-year floodplain). Zone
  X (minimal risk) and Zone B/C (moderate) acceptable. Zone AE with
  LOMR/LOMA may pass — flag for review."
- **H6 Wetlands**: "No NWI-mapped wetlands covering >20% of parcel. Minor
  fringe wetlands acceptable if buildable area still meets minimum
  footprint."

The frozen thresholds already exist in `parameters.json` `hard_filters`:
`flood_zones_blocked` `["A","AE"]`, `flood_zones_flag_for_review`
`["AE_with_LOMR"]`, `wetlands_max_pct_of_parcel` `20`. They are read, never
edited. The operator's SiteFit tool (`runAnalysis`) reads the same two
layers (NFHL MapServer layer 28 and NWI Wetlands layer 0) and categorises
floodway / SFHA / 0.2% ("x500"); this stage mirrors that.

## Pieces

1. **`hazards.py`** (new, protected, imports only the standard library and
   `prepare`; load never raises). The NFHL categories, the three states,
   the H4/H6 decision (thresholds from `prepare.get_parameters()` only),
   one read SQL, the memo lines and the site-fit deduction texts. Public to
   `research.py`: `enabled()`, `assess_parcels()`, `GAP_RESOLUTION`,
   `REVIEW_FLAG_TYPE`, `REVIEW_RESOLUTION` (static guard).
2. **`hazards_import.py`** (new, protected operator tool,
   `make hazards-import`). Fetch or read, compute in PostGIS, write
   `parcel_hazards`. Between runs only.
3. **`research.py`**: H4/H6 at scoring through gate 4; discovery stops
   emitting the stubs once enabled.
4. **`site_fit.py`**: the SFHA and wetland deduction inside its own SQL.
5. **`reporting.py`**: program.md's snapshot "Flood zone" and "Wetlands"
   lines; the memo's projection lines and footnote say what was deducted.

## Storage and the gate (FW-03, FW-04, FW-06)

There is no hazard table. The stage proposes one (`parcel_hazards`, keyed by
`parcel_id`, per-source column groups, clipped SRID 2240 geometry for site
fit; exact DDL in `01_prepare_mutation_proposal.md` and
`hazards.PROPOSED_DDL`, compared by a test) and gates every consumer on a
static, human-owned signal that costs no SQL: `hazards.enabled()` is true
only when prepare.py's source text assigns the module-level string
`_DDL_PARCEL_HAZARDS` naming the table. The text is parsed from disk once,
when `hazards` loads (`hazards._SOURCE_DDL`); the in-process `prepare`
attribute is never consulted, so an assignment or deletion of
`prepare._DDL_PARCEL_HAZARDS` at runtime (from research.py, say) can neither
switch the gate on nor, once a human has switched it on, off (review
finding 2). `tests/static_guards.prepare_write_problems` also fails the
suite if research.py stores to, deletes, aliases or `setattr`s any
`prepare.<attr>`, and research.py may not name the gate constant. Only a
human can add it. While it is absent:

- no consumer issues a hazard statement; discovery still writes the two
  stub flags; every `parcel_scores` field, flag text and statement is
  identical to 8ddaf69 (golden record `golden_head_8ddaf69.json`, checked
  by `tests/hazards_golden_check.py`; both earlier golden checks
  unchanged);
- `make hazards-import` refuses (exit 1) and names the proposal.

Once enabled, three states per parcel and per source:

| State | Meaning | H4/H6 |
|---|---|---|
| absent | no row, or that source never imported for the parcel | pass + data_gap "no hazards import for this parcel" |
| stale | the row's `md5(ST_AsBinary(geometry))` differs from the parcel's current geometry (discovery's UPSERT rewrote it) | pass + data_gap "hazard data stale" |
| fresh | stored md5 equals current | the real gate |
| unreadable | the read failed (savepoint, like `site_fit._query`) or prepare's DDL lacks a column this module reads | pass + data_gap |

Applying the DDL with an empty table changes no score; only flag text
changes (the discovery stubs stop; scoring writes the two "no hazards
import" data_gap flags per parcel, once while open). The old stub flags stay
open (no new write semantics; a human may resolve them). **The first
populated import is the intended metric change: run it between runs, then
start a new run and baseline.**

## H4: how "in Zone A/AE" maps to an area test (FW-08, FW-09, FW-10)

On a fresh NFHL row, with FLD_ZONE/ZONE_SUBTY/SFHA_TF normalised (strip,
collapse spaces, upper):

- **Category per polygon, SiteFit's precedence**: ZONE_SUBTY containing
  FLOODWAY -> floodway; SFHA_TF = 'T' -> sfha; Zone D, AREA NOT INCLUDED or
  blank -> undetermined; ZONE_SUBTY containing 0.2 -> x500; OPEN WATER ->
  open water; else minimal. Areas are unions per category within the
  parcel: floodway; SFHA *including* floodway (SiteFit's
  `res.sfha += a2` for floodway too); 0.2% *minus* SFHA. So categories are
  disjoint where they must be, SFHA >= floodway and no share exceeds 100%.
- **Block** (gate 4, blocker `H4: ...`): any union area of a zone in
  `flood_zones_blocked` on the parcel at or above the numeric noise floor;
  a regulatory floodway, always, named separately (it lies inside the
  SFHA, AE in practice, and no LOMR relaxes it; this is the conservative
  reading of "may pass", not a new threshold).
- **Noise floor** = 1 SF: SiteFit drops a clipped piece under 1 SF
  (`if (a2 < 1) return;`). It is numeric noise from clipping, cited as
  precedent, not a sliver tolerance. No percentage tolerance is invented:
  any SFHA area of 1 SF or more blocks, as program.md reads ("Not in Zone A
  or AE"). Parcel lines and FIRM lines are each positionally uncertain by
  5-20 ft, so boundary slivers will block; the import prints the SFHA
  share distribution in bands (>0-0.1%, 0.1-1%, 1-5%, 5-20%, >20%) so the
  human can decide, between runs, whether to add a parameters.json key
  (for example `flood_sfha_min_pct_of_parcel`) through the prepare-mutation
  protocol. That is an option for the human, not part of this stage.
- **LOMR / LOMA** (FW-09, review findings 3, 4 and 11; a metric-relevant
  decision): `"AE_with_LOMR"` is not an NFHL value. It is read as
  "`<ZONE>_with_LOMR` relaxes `<ZONE>`", and only where the LOMR is about
  that zone: the AE block (outside any floodway) becomes a review flag and
  the parcel passes ONLY when the union of the NFHL LOMR polygons (layer
  located by its name "LOMRs", never a hard-coded id; `S_LOMR` in the
  county package) covers the parcel's whole AE area: the AE inside the LOMR
  union is at least 1 SF and the AE outside it is under 1 SF (the noise
  floor), summed over every AE subtype. The import stores, per zone key,
  the part of that zone on the parcel inside the LOMR union
  (`nfhl_zone_lomr_sf`, unioned before `ST_Area`). A LOMR elsewhere on the
  lot (a corner of the parcel away from the AE strip) relaxes nothing: the
  effective NFHL already folds LOMRs in, so AE outside a LOMR is plain
  effective AE and says nothing about the LOMR. A LOMR covering only part
  of the AE blocks too, and the blocker says "a LOMR covers only part of
  zone AE on the parcel". The first version relaxed AE whenever any LOMR
  polygon touched the parcel; review found that too lenient and it was
  narrowed. **For the human to confirm**: this is still program.md's "may
  pass, flag for review" clause and it still RELAXES a block where a LOMR
  covers all the AE. Even that AE is effective AE in layer 28 (the LOMR
  revised it and it stayed AE), so the strictest reading, "AE always
  blocks; a LOMR only adds a review flag", is also defensible. To get it,
  remove `"AE_with_LOMR"` from `flood_zones_flag_for_review` (parameters.json,
  between runs, prepare-mutation protocol). The code needs no change: an
  empty list relaxes nothing. A LOMA is never in layer 28 and never
  detectable here: every H4 text says "LOMA not checked". Without LOMR data
  (`lomr_status` not_read) AE blocks.
- **Review flags, never blocks**: other SFHA zones (SFHA_TF = 'T' outside
  the blocked list: AH, AO, A99, AR, V, VE: not invented as blocked; an
  open question for the human in `03_decision_note.md`, since program.md
  calls H4 the "100-year floodplain" and these are 1% annual-chance zones
  too), the 1%
  future-conditions floodplain (metro Atlanta FIRMs carry it and local
  ordinances regulate it), levee-reduced areas, open water.
- **Data gap**: no NFHL polygon on the parcel, or any part of it in Zone D
  / AREA NOT INCLUDED / not covered by any polygon. NFHL polygons tile the
  whole mapped area including X, so "nothing returned" means unmapped, not
  clear.
- **Pass**: X (minimal) and the 0.2% zone (program.md's B/C).

## H6 (FW-07, FW-15)

Share = union of every NWI polygon on the parcel (wetlands and deepwater
habitats, as SiteFit counts them) over the parcel area, both SF in SRID
2240. **Fail** only when `100 * wetland_sf > threshold * parcel_sf`,
strictly, compared with exact fractions of the stored values (no float, no
rounded percentage: 20.00% passes, 20.01% fails). Stored `*_pct` columns
are display only. A share above 0 (at least 1 SF) and at most the threshold
passes with a review flag "minor fringe ...; minimum footprint undefined,
not tested": `parameters.json` has no minimum footprint, and a hard filter
never depends on site fit, `BTS_MIN_FOOTPRINT_SF` (a strategy test) or
draft zoning. Per-WETLAND_TYPE areas are stored; a review flag is added
when deepwater types (Lake, Riverine, Estuarine and Marine Deepwater) or
excavated classes (`x` special modifier, e.g. PUBHx) make up most of the
share. Memo classes: none / minor fringe (0 < share <= threshold) /
significant (> threshold), from the existing threshold only.

## Where the gate bites (FW-02, FW-17)

At scoring, where the metric reads: `score_parcel` and the
`run_scoring_cycle` cache path use the same `hazards.assess_parcels` (one
prefetch per cycle; the per-parcel path reads one parcel). A block or fail
goes through the existing gate-4 channel (`FAIL:deal_killer`, blocker
`H4: ...` / `H6: ...`): no new actionability value, no
`actionability_block` flag is ever written for it. Review flags use
`flagged_items.flag_type = 'review'`, which no gate reads (gate 4 reads only
`actionability_block`), so a review never changes actionability; they are
written once while an identical one is open (read in the same prefetch).
Flag texts carry the zone class and band, never a timestamp, date or float
share. Discovery cannot enforce H4/H6 (a new parcel has no hazard row at
discovery, and a discovery reject never reaches `parcel_scores`), so once
enabled the discovery H4/H6 filters simply pass with no flag.

## Site fit (FW-14, FW-23)

While enabled, `site_fit` reads the parcel's FRESH `sfha_geom` (floodway
included) and `wetland_geom` itself, in `_SQL_BUFFERED_FOR_FIT_HAZARDS`
(hook semantics: never passed in by a caller), and differences their union
from the setback buffer in PostGIS. The hazard is first expanded outward
(buffer +2 ft, simplify 1 ft, so it still covers the original), and the
simplified branch differences it buffered by a further 2 ft, so no fitted
envelope enters a deducted area. With no fresh hazard geometry the buffer
expression is exactly the old one, so the ring text, which is the fit cache
key, is unchanged; any deduction changes the ring text and so the key. The
0.2% zone is not deducted. A failed hazard read falls back to the plain
buffer, labelled. Every projection says what was deducted ("SFHA incl.
floodway deducted (0.2% zone not deducted); NWI wetlands deducted;
fill/compensatory storage not considered") or "flood/wetlands not deducted
(<reason>)" when absent or stale, plus "stream/state-waters buffers not
deducted" (Georgia's 25 ft state-waters buffer and local 50-75 ft stream
buffers are not modelled). BTS still moves only through
`meets_bts_footprint` for verified jurisdictions. With the gate off the
projection constraints are the old ones byte for byte, which is why the
stream-buffer note appears only once enabled (adding it while absent would
change the snapshot and memo that FW-03/FW-18 require unchanged; the old
"flood/wetlands not deducted (H4/H6 not wired)" already says nothing
hydrologic is deducted).

## Data routes (FW-01)

robots.txt, fetched 2026-11-06 (a second fetch the same day was reset by
the host):

```
hazards.fema.gov/robots.txt  -> 200 text/plain
  User-agent: *
  Disallow: /*?*
  Disallow: /arcgis
  Disallow: /nri/report/viewer
  ... (further /flood-maps/archived-flood-data/... paths)

msc.fema.gov/robots.txt -> 200 text/plain
  User-agent: *
  Disallow: /*?*
  Disallow: /portal/search?AddressQuery=
  Disallow: /portal/availabilitySearch?addcommunity
  Disallow: /portal/downloadProduct?productID=
  Disallow: /arcgis

fwspublicservices.wim.usgs.gov/robots.txt -> 301 text/html
  Location: https://www.fws.gov/program/national-wetlands-inventory/wetlands-mapper
```

So every NFHL MapServer query path is disallowed, the MSC download URL is
disallowed, and the NWI answer is a redirect off the registrable domain to
an HTML page. `hazards_import --mode rest` checks robots before any query
with rules_scrape's parser and policy unchanged (`PoliteFetcher.robots_for`
is inherited, not overridden; 404/410 allows all, anything that is not a
clean text 2xx denies all, an off-site redirect denies all; this tool's
own token plus the AI-agent tokens rules_scrape honours). Today it fails
both sources with exit 4 and makes no data request. No code path
overrides a robots answer.

The honest route is **local files** (`--mode files`, the default), in the
style of reviews/24_rules_local_files: the operator downloads, in a
browser, the NFHL county package for Fulton (13121C) from the FEMA Map
Service Center and the NWI Georgia shapefile from the USFWS download page,
and unpacks them into the gitignored `hazards_inbox/`
(`nfhl/S_FLD_HAZ_AR.*`, optional `nfhl/S_LOMR.*`, `nwi/<one>.shp`; when
the state package unpacks several shapefiles, the single `*_Wetlands.shp`
is read and any other ambiguity fails the source). dBASE records flagged
deleted (`*`) are skipped. They are
read with a pure standard-library .shp/.dbf/.prj reader (no shp2pgsql or
ogr2ogr: SR-10 needs no decision): polygon records only, record bboxes
pre-filtered against the parcel envelopes in the file's own SRID, outer
rings clockwise and holes counter-clockwise as the format defines, each hole
assigned to the smallest containing outer ring; dBASE's 10-character names
are resolved (WETLAND_TYPE is stored as WETLAND_TY). The .prj must be NAD83
geographic (4269), WGS84 geographic (4326) or NAD83 Conus Albers (5070,
checked by its parameters); anything else, Web Mercator included, is
refused. A parcel counts as covered by a file only when its (slightly
padded) envelope lies inside the file's extent.

The REST mode, for the day robots allows it: layer metadata checked first
(name "Flood Hazard Zones" / "Wetlands", Feature Layer, polygon, required
fields; drift = exit 4); object ids by 0.02 degree envelope tiles over the
parcels (returnIdsOnly), then features by object ids in pages of at most
100 with `f=geojson`, `outSR=4326`, `geometryPrecision=7` and no
`maxAllowableOffset`; a short page, an error body, HTML, a missing field
or `exceededTransferLimit` fails the whole source; features deduplicated
by id. Politeness is rules_scrape's: 1 request/second per host, bounded
retries with back-off, Retry-After honoured and capped, 60 s timeout,
budget, no cookies, generic User-Agent
`Land-Research-HazardsImport/1.0 (between-runs operator tool; 1 req/s per
host; honours robots.txt)`.

The service URLs are code constants in the protected tool (they are the
URLs SiteFit uses); `sources.json`'s `environmental.fema_flood` and
`usgs_wetlands` entries are the web mappers and are not edited.

## Computation (FW-11, FW-13, FW-19, FW-22, FW-24)

All in PostGIS. Features go into a TEMP table made valid, polygons only,
transformed to SRID 2240 and `ST_Subdivide`d (256 vertices), with a GiST
index. Per parcel: the parcel geometry is
`ST_Transform(ST_CollectionExtract(ST_MakeValid(geometry), 3), 2240)`, the
same polygonal part S2 and site fit use; intersections are unioned per
category and per zone key / wetland type before `ST_Area`, so overlapping
panel-seam or LOMR polygons count once; shares are ratios over that same
parcel area. The service's Shape_Area, Shape_Length and ACRES are never
requested or read (NWI's are Web Mercator, about 1.4x too large at 33.6 N,
and unclipped). Parcels outside EPSG:2240's area of use or outside Fulton
are not imported (absent -> data_gap). The area-of-use test takes the
centroid, and the REST/file pre-filter the envelope, of the SAME made-valid
polygonal geometry (review finding 5: the raw centroid of a self-
intersecting bowtie can fall more than 5 degrees from the parcel); a parcel
whose repaired geometry is empty is counted apart ("no polygonal area
after repair"), never as outside the area of use. On the 1,075 corridor
parcels (57 invalid, "Hole lies outside shell") neither rule excludes any
parcel. Features are sorted by id before
insertion, so server order never changes a row; stored areas are rounded
to 0.01 SF, percentages to 4 decimals, zone and type maps are JSONB (key
order canonical) and `zones` is sorted. `parcels.geometry` is the largest
outer ring of a multipart parcel (Phase 3); shares are computed over the
stored geometry, consistent with S2 and site fit, and the memo lines add
"parcel stored as its largest outer ring ..." where the parcel carries the
open multi-polygon data_gap.

## Writes (FW-16, FW-20, FW-21)

One transaction per source, committed only after every page arrived and
every computation succeeded. Each source owns a column group of
`parcel_hazards`: the import UPSERTs the whole group for every covered
parcel (the SR-9 replace, per source; the other source's columns are never
touched), and a row whose content is unchanged is not updated, so a re-run
with the same inputs changes 0 rows and keeps `*_fetched_at` (the time the
content was first written). A failed source keeps all of its previous
values; the other source still commits. Every row carries per-source as-of
values (NFHL DFIRM_ID + VERSION_ID of the polygons on the parcel, else the
file's dBASE date or the layer's last edit date; NWI the dBASE date or the
layer's last edit date); without one nothing is written. Counts only on the
console (parcels, features, changed/unchanged, share bands, H4/H6
outcomes, requests, wall time); no owner or address field is requested.
Exit codes: 0 imported; 1 refused (table not enabled or not in the
database, bad arguments); 2 database; 3 a run has its baseline (TSV or
experiment_log_mirror, read-only, zoning_import's guard); 4 a source failed
(named on the last line). Exit 2 can follow a partial commit (a source
committed, then the connection died): the last line then names the
source(s) committed and the one(s) not written, never "not imported"
(review finding 6). Each source line also counts the parcels imported
before that the current input no longer covers; their previous values are
kept and still read as fresh while their geometry is unchanged, so a
shrunken inbox file is visible (review finding 12).

## Metric-side enforcement (review finding 1)

Gate 4 lives in research.py, the agent sandbox, like H1-H3. An experiment
that drops the hazard argument, sets `hazard = None` or forges assessments
would turn every H4/H6 `FAIL:deal_killer` back into PASS and the ratchet
would keep the higher metric. So once `hazards.enabled()`, the protected
`runner.evaluate` re-checks H4/H6 outside the sandbox, before the metric
read (`runner._hazard_audit`):

- it lists the parcels the metric would count with
  `prepare._metric_sql_and_params("parcel_id", run_tag)`, the same
  latest-row CTE and PASS + threshold filter (no reimplementation), and
  re-assesses them through `hazards.assess_parcels`;
- any counted parcel with an H4/H6 blocker, any unreadable assessment, or
  any change to `parcel_hazards` between a fingerprint taken before the
  sub-cycles and one taken after them (count plus md5 of every row's text;
  the import refuses during a run, so the table must not change) raises
  `HazardAuditFailed`; evaluate reports a crash (metric 0) and the loop
  discards and purges the experiment, as for any crash;
- counts only in the error and the log; stale parcels in the counted set
  are counted in the log (a warning) and in
  `sub_summaries["hazard_audit"]`.

While the gate is off it issues no statement and opens no connection
(`tests/test_hazards_runner.DisabledTests`). Residual, accepted with
SR-16: sandbox code runs in the runner's interpreter, so a deliberately
adversarial edit could still rebind `hazards.assess_parcels` or the runner's
functions in-process (static guards ban the reflective routes), and a
discovery edit that perturbs parcel geometry makes rows stale (a data_gap
pass, counted in the audit's log line). Both are visible in the `exp:`
diff. A stricter, sandbox-proof option for the human is in
`01_prepare_mutation_proposal.md`: put the H4/H6 exclusion into prepare.py's
metric SQL.

## Terms and disclaimers (FW-20)

- FEMA NFHL: the NFHL is FEMA's digital flood hazard data for
  informational use; it is not an official determination; the effective
  FIRM (and any LOMC) governs; screening only. Every flood line says
  "screening only; not an official determination (the effective FIRM
  governs)" and "LOMA not checked".
- USFWS NWI: wetland mapping from imagery at a 1:24,000-class screening
  scale; it is not a jurisdictional wetland delineation and does not
  replace a field delineation (Section 404). Every wetland line says "NWI
  is not a jurisdictional delineation (1:24,000 screening scale)".
- Both datasets are US federal public data; the operator downloads them by
  hand. robots.txt above.

## Not modelled (stated)

Stream and state-waters buffers, fill and compensatory storage, LOMA,
the regulatory effect of future-conditions floodplain (flagged only),
detention, any minimum-footprint test for H6.
