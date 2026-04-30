# Phase 3 Risk and Architecture Review — Fulton County Discovery Connector

**Reviewer:** Agent 1 role, completed by orchestrator under sub-agent infrastructure deviation (see header note below).
**Date:** 2026-04-30
**Branch:** `claude/fulton-county-connector-XEpUx`
**Scope:** BUILD_PHASES.md L62–L70 (Phase 3) and `appendix_a_county_connectors.md` "1. Fulton County" (L289–L361) plus the harness integration points (L897–L903).

---

## Header note: three-agent workflow deviation

Two attempts to run Agent 1 as a proper independent-context Opus 4.7 sub-agent both terminated with `Stream idle timeout` after ~250 seconds and 30+ tool calls — the sub-agent infrastructure in this sandbox cannot stream a ~500-line risk review without going idle long enough for the connection to drop. This is the same class of infrastructure deviation that produced `reviews/03_phase2_connector_harness/03_orchestrator_completion_note.md` in Phase 2 (Agent 2 cut off mid-stream by org quota; orchestrator finished solo with explicit human authorization).

The human (`andreykuzmin1994-blip`) explicitly authorized the orchestrator to proceed with option **A** ("orchestrator self-completes Agent 1, document the deviation") in this session.

Structurally, the orchestrator did the same thing Agent 1 would have done, against the same source artifacts, but **without the independent-context property** that the three-agent workflow normally provides. The downstream Agent 2 and Agent 3 will be attempted as proper sub-agents because their outputs are shorter and unlikely to hit the same stream limit. Agent 3 should treat this risk review with the same skepticism it would apply to any single-context output and flag any risk it thinks the orchestrator missed by virtue of not having Agent-1-fresh-eyes.

---

## Sections

1. Summary verdict
2. In-scope / out-of-scope
3. Risk register (severity-ranked)
4. Go/no-go gates for Agent 3
5. Open questions for Agent 3 / human
6. Final verdict + counts

---

## 1. Summary verdict

**GO-WITH-CONDITIONS.** Phase 3 is implementable as specified. The key risks cluster in three areas: (a) PostGIS geometry handling between ArcGIS's polygon-with-rings format and the schema's strict `GEOMETRY(Polygon, 4326)` column, (b) hard-filter H1's loose `expected_bbox` letting parcels in adjacent counties through, and (c) idempotency / re-run semantics for cycles that overlap previously-discovered parcels. None of these are blocking; all have concrete mitigations enumerated below.

The conditions Agent 3 must enforce before merge:

- No mutation of `prepare.py`, `parameters.json`, `sources.json`, or `program.md` from `research.py` or its imports.
- Every SQL statement issued from `research.py` uses parameterized arguments. No f-string interpolation into SQL.
- The discovery cycle calls `connector_harness.run_harness_for_county("fulton")` BEFORE issuing any production query, and aborts (logged to `research_log`) on `failing` status.
- Field mapping is read at runtime from `sources.json`, NOT duplicated in `research.py`.
- Every parcel insert is paired with a `research_log` row in the same transaction.
- Offline test suite covers harness=failing abort, bbox-out-of-Fulton rejection, acreage edge cases, UPSERT idempotency, and ArcGIS pagination correctness against fixture responses.

Estimated implementation surface: ~600–900 lines of new code in `research.py` plus ~300–500 lines of offline tests in `tests/test_discovery.py`. The connector should be structured so Phase 11 can plug in additional counties without rewriting the discovery loop.

---

## 2. In-scope / out-of-scope for this phase

### In scope

- A `run_discovery_cycle(market: str)` entrypoint in `research.py` for `market="atlanta"`.
- A Fulton-specific connector module that queries ArcGIS Layer 11 (parcel features) within two corridor bounding boxes (South Fulton + West Atlanta/I-20).
- Field mapping from ArcGIS attributes to the `parcels` schema, using the validated mapping in `sources.json.county_parcel_data.fulton_ga.field_mapping`.
- Owner-type inference from `owner_name` per `parameters.json.owner_classification`.
- Hard filters H1 (target market envelope) and H2 (acreage 5–50 client-side recheck), implemented as deterministic functions.
- Hard filters H3 (zoning) and H4 (flood) implemented as PASS-WITH-FLAG stubs that emit `flagged_items` rows with `flag_type='data_gap'`. The parcel proceeds to insert.
- `parcels` UPSERT on `parcel_id`, `research_log` insert per discovery action, `flagged_items` insert per data-gap or conflict.
- Raw response caching to `sources/{cycle_id}/{corridor}_{offset}.json` and a `.gitignore` rule for `sources/`.
- Offline pytest suite using fixture ArcGIS responses (no live network in tests).

### Out of scope (must NOT be built in Phase 3)

- Any of S1–S12 scoring; `parcel_scores` table is not written from this phase.
- Hard filters H5–H10 (environmental, wetlands, road access, utilities, topography, ownership availability). These are Phase 4.
- The four-gate actionability screen. Phase 8.
- Strategy fit assessment. Phase 8.
- Snapshots and strategy memos. Phase 9.
- The Karpathy experiment loop. Phase 10.
- AI fallback (Playwright + Claude vision). Phase 12.
- Connectors for any other county. Phase 11.
- Any modification to `connector_harness.py`, `connector_registry.json`, `prepare.py`, `parameters.json`, `sources.json`.
- Any zoning Layer 34 cross-query (Phase 4 territory).
- Any FEMA flood query (Phase 4).

---

## 3. Risk register

Severity scale: **S1** critical (must fix before merge), **S2** high (must fix before merge but routine), **S3** medium (must fix before merge or have explicit accepted-risk note), **S4** low (post-merge if needed), **I** informational.

### 3.1 Five-File Contract integrity

**R-01 (S1) — `research.py` writing back into the immutable layer.**
Per AUTORESEARCH_MECHANICS.md L62–L77, `research.py` is the only file the agent edits during a run, and it must NOT redefine or mutate symbols it imports from `prepare.py` or open-write any of `parameters.json`, `sources.json`, `program.md`. The Phase 3 connector reads field mappings from `sources.json` and reads `parameters.json` via `prepare.get_parameters()`. It must not call `json.dump` against either path or any helper that could.
**Mitigation:** Static check in tests — grep `tests/test_discovery.py` for any `open(...,'w')` or `json.dump` paths targeting `parameters.json` / `sources.json` / `program.md`. The test asserts these paths never appear as write targets in `research.py` source via AST inspection (`ast.parse` + walk for `Call(open, mode='w')` and `json.dump`). Add `parameters.json` / `sources.json` / `program.md` SHA pin assertions at cycle start.
**Acceptance test:** `tests/test_discovery.py::test_no_immutable_writes` — AST-walks `research.py`, fails if any write to the four immutable paths is reachable.

**R-02 (S2) — Reloading parameters mid-cycle.**
`parameters.json` is frozen by `prepare.py` via `MappingProxyType` deep-freeze at module import (`prepare.py` L147–L216). The connector should call `prepare.get_parameters()` once at cycle start and pass the resulting mapping down, NOT re-read `parameters.json` from disk inside the loop. The latter would (a) be redundant work, (b) defeat the SHA pin, and (c) introduce a race window where a between-cycle parameters mutation could silently take effect mid-cycle.
**Mitigation:** Single `params = prepare.get_parameters()` at the top of `run_discovery_cycle`. All deeper functions take `params` as an argument (or a frozen sub-mapping), never re-read.
**Acceptance test:** Search `research.py` for `parameters.json` string literal — must not appear except possibly in a comment.

**R-03 (S2) — `verify_parameters_unchanged` not called.**
`prepare.verify_parameters_unchanged` (`prepare.py` L219–L233) is the SHA-256 sentinel half of the immutability guard. Agent 2 should call it at the top of `run_discovery_cycle` (before any DB or network work) so a mid-run parameters edit fails loudly instead of silently corrupting the cycle.
**Mitigation:** First non-trivial call in `run_discovery_cycle` is `prepare.verify_parameters_unchanged()`. Wrap in a try/except that converts `ParametersError` into a `research_log` `action_type='abort'` row and re-raises.
**Acceptance test:** Mock `prepare.verify_parameters_unchanged` to raise; assert the cycle aborts and writes one abort row to research_log (in test, against a sqlite-in-memory or mock cursor).

**R-04 (S3) — `connector_registry.json` is not in the Five-File Contract but is harness-only.**
The Phase 2 institutional record explicitly designates `connector_registry.json` as a harness-only overlay (its `_comment` field at top of file). The Phase 3 connector should read its corridor bboxes from a NEW location it controls, NOT from the harness registry, to avoid coupling production discovery to a harness configuration file. Recommendation: hardcode the two corridor bboxes as Python constants in `research.py` (Phase 3 only has two; promoting them to a config file is premature). Phase 11+ promotes them to a per-county config when there are more.
**Mitigation:** `_FULTON_CORRIDORS = {"south_fulton_campbellton": {...}, "west_atlanta_i20": {...}}` near the top of `research.py`. Comment cites appendix L266–L283 as source of truth.
**Acceptance test:** `tests/test_discovery.py::test_corridor_bboxes_match_appendix` — the constants match the appendix-quoted values exactly.

### 3.2 Database safety

**R-05 (S1) — SQL injection via owner names, addresses, or zoning strings.**
ArcGIS returns user-controlled-ish strings (some Fulton parcels have owner names like `O'BRIEN TRUST`). f-string interpolation into SQL would create an injection surface even though the data ultimately comes from a county GIS. Fulton is trusted today, but the same code will run against 7 other counties in Phase 11.
**Mitigation:** Every `cur.execute()` call must use parameterized form: `cur.execute(SQL, (param1, param2, ...))`. `executemany` for batches. NO `.format()` / `%`-formatting / f-string into SQL. Static check via grep in test: `grep -nE "execute\(.*[fF]\".*\{.*\}.*WHERE|execute\(.*%\s*\(" research.py` must return zero matches.
**Acceptance test:** `tests/test_discovery.py::test_no_string_interpolated_sql` — AST-walks `research.py`, looks for `Call(execute)` whose first arg is anything other than a `Constant(str)` or a module-level `Name` whose value is a `Constant(str)`. Pass = no dynamic SQL.

**R-06 (S1) — `parcel_id` collisions across counties.**
STORAGE_ARCHITECTURE.md L43 says: "County-assigned parcel ID (with county prefix for global uniqueness)". Fulton's ArcGIS returns native ParcelIDs like `14F-0123-LL-045-8` with NO county prefix. If Agent 2 inserts the raw ParcelID into the `parcels.parcel_id` PRIMARY KEY column, Phase 11 will collide with Henry/Cobb/etc. parcel IDs that share the same numeric scheme. The collision will manifest as a CONSTRAINT VIOLATION at insert time in Phase 11 — but only for the parcels that happen to collide, and only after Phase 3 has been running for months.
**Mitigation:** Compose `parcel_id = f"{county_lower}-{native_parcel_id}"` at insert time. For Fulton: `parcel_id = f"fulton-{attrs[parcel_id_field]}"`. Document this in a module-level comment citing STORAGE_ARCHITECTURE.md L43. The same prefix discipline applies to `research_log.parcel_id` and `flagged_items.parcel_id` foreign-key-equivalent columns.
**Acceptance test:** `tests/test_discovery.py::test_parcel_id_prefixed` — fixture-driven; assert all `parcel_id` values inserted into the test DB start with `fulton-`.

**R-07 (S1) — PostGIS geometry: `MULTIPOLYGON` vs `POLYGON(4326)`.**
The schema column is `GEOMETRY(Polygon, 4326)` (`prepare.py` L308, mirroring STORAGE_ARCHITECTURE.md L70). ArcGIS Layer 11 may return polygons with multiple rings (one outer + holes) — that is still a valid Polygon. But ArcGIS may also return parcels split into multiple disjoint pieces (e.g., a parcel bisected by a road) as `geometryType=esriGeometryMultiPolygon` or as multiple top-level rings. Inserting a MultiPolygon into a `GEOMETRY(Polygon, 4326)` column will raise a constraint violation.
**Mitigation:** Detect at conversion time. If ArcGIS returns multiple top-level rings whose orientations indicate disjoint outer boundaries (rather than outer+holes), apply `ST_Union` server-side via `ST_GeomFromText('MULTIPOLYGON(...)', 4326)` then `ST_Buffer(.., 0)` to coalesce, OR convert the geometry column to `GEOMETRY(MultiPolygon, 4326)` via a Phase 3 prepare.py mutation. The latter is preferred long-term but is a `prepare.py` mutation (see AUTORESEARCH_MECHANICS.md "When Mutating prepare.py"). For Phase 3, the orchestrator's recommendation is to: (a) use the simpler path of `ST_GeomFromText` with the largest-area outer ring only and (b) emit a `flagged_items` row of `flag_type='data_gap', description='multi-polygon parcel reduced to largest ring'` for any parcel where ArcGIS returned >1 outer ring. This preserves the schema and keeps the data loss visible. Phase 4+ can convert the column to MultiPolygon and reprocess flagged rows.
**Acceptance test:** `tests/test_discovery.py::test_multipolygon_handling` — fixture with a 2-outer-ring ArcGIS feature; assert one parcel inserted with the larger-ring polygon, one flagged_items row with the documented reason.

**R-08 (S2) — SRID assertion and trust-but-verify on `outSR`.**
The query asks `outSR=4326`. Some ArcGIS server versions silently ignore `outSR` if the layer has been republished without spatial transformation registered, returning coordinates in the layer's native 102667 (State Plane). 102667 coordinates look like `2,200,000 / 1,400,000` (feet), nowhere near WGS84 degree ranges. Inserting those into a `GEOMETRY(Polygon, 4326)` column SUCCEEDS at the SRID-tag level (PostGIS just trusts the declared SRID) but the coordinates are nonsense in WGS84 — every spatial query downstream returns empty.
**Mitigation:** Before constructing any geometry, sanity-check the centroid: if the absolute lat is > 90 or absolute lng is > 180, the response is not in 4326. Treat as a hard failure for that record: log a `flagged_items` row of `flag_type='data_gap', description='ArcGIS ignored outSR=4326'` and skip the parcel. If ALL parcels in a corridor fail this check, abort the cycle and write a `research_log` abort row, because the connector is broken.
**Acceptance test:** `tests/test_discovery.py::test_srid_sanity_rejects_state_plane_response` — fixture with State Plane coords; assert no parcels inserted, all flagged.

**R-09 (S2) — Centroid: PostGIS `ST_Centroid` vs client-side Shapely.**
The schema has both `geometry` (the polygon) and `centroid` (the point). Per STORAGE_ARCHITECTURE.md L71 the centroid is "auto-computed". The DDL has no trigger; the auto-compute must happen at insert time. Recommendation: compute it server-side via `ST_Centroid(geometry)` in the same INSERT statement, NOT client-side. This avoids importing Shapely (one fewer Phase 3 dependency) and ensures the centroid is exactly what PostGIS spatial queries will use.
**Mitigation:** `INSERT INTO parcels (... geometry, centroid, ...) VALUES (... ST_GeomFromText(%s, 4326), ST_Centroid(ST_GeomFromText(%s, 4326)), ...)` — the WKT string is parameterized, the SRID is a constant integer literal. The doubled GeomFromText is acceptable for Phase 3 (Fulton has at most ~2000 parcels per corridor; PostGIS reparses microseconds-fast); a CTE or `RETURNING` round-trip is over-engineered.
**Acceptance test:** `tests/test_discovery.py::test_centroid_is_inside_polygon` — for each fixture parcel, assert `ST_Within(centroid, geometry) = true` after insert. Skip if the test runs without a real PostGIS (mock-only); add a CI-only live-DB test in a follow-up.

**R-10 (S2) — Transaction boundary across parcel + research_log.**
A parcel insert and its corresponding `research_log` discovery row should commit atomically. If the parcel inserts but the log row fails, the cycle's audit trail is broken. Conversely, if the log row inserts but the parcel fails, the metric layer sees a phantom discovery.
**Mitigation:** Two options: (a) per-parcel transaction — commit after each `parcels` UPSERT + `research_log` INSERT pair. Robust to mid-cycle crashes (already-committed parcels are durable) at the cost of N round-trips per cycle. (b) per-page transaction — commit after each ArcGIS page (up to 1000 parcels). Faster but a crash mid-page loses the page. **Recommendation: per-parcel for Phase 3**, because the volume per Fulton corridor is bounded (likely <500 parcels per corridor at acreage 5–50) and the simpler crash-safety story is worth the modest perf hit. Phase 11 can revisit with batch inserts if cycle times stretch.
**Acceptance test:** `tests/test_discovery.py::test_transaction_boundary` — inject a failure in the research_log insert; assert the matching parcel insert is rolled back.

**R-11 (S2) — `last_updated` and idempotency via UPSERT.**
Re-running a discovery cycle that overlaps a previous cycle (same corridor, same day) must not create duplicate parcels. Use `INSERT ... ON CONFLICT (parcel_id) DO UPDATE SET ... last_updated = NOW()`. The `discovery_date` should be set on first insert and NOT overwritten on conflict (it's the discovery_date, not a re-discovery date). Fields that ArcGIS may have updated since last cycle (assessed_value_total, owner_name on transfer, etc.) SHOULD be overwritten so the parcels table reflects current state.
**Mitigation:** Explicit ON CONFLICT clause that lists which columns are overwritten and which are preserved. `discovery_date = COALESCE(parcels.discovery_date, EXCLUDED.discovery_date)` preserves the first-seen value; `owner_name = EXCLUDED.owner_name`, `assessed_value_total = EXCLUDED.assessed_value_total`, `last_updated = NOW()` overwrite. Document in a module-level comment.
**Acceptance test:** `tests/test_discovery.py::test_upsert_preserves_discovery_date` — insert, mutate fixture, re-insert with later date; assert `discovery_date` is the original, `last_updated` is current.

**R-12 (S3) — Connection lifetime and pool exhaustion.**
`prepare.get_connection()` is a context manager that yields a single `psycopg.Connection` (`prepare.py` L251–L269). The discovery cycle should hold one connection for the whole cycle (open at the top of `run_discovery_cycle`, close at the end). Opening a new connection per parcel exhausts the Supabase free-tier pool (60-connection limit on the pooler).
**Mitigation:** `with prepare.get_connection() as conn: ...` wraps the entire cycle body. All sub-functions take `conn` as an argument. No nested `get_connection()` calls.
**Acceptance test:** `tests/test_discovery.py::test_single_connection_per_cycle` — mock `prepare.get_connection`; assert it's invoked exactly once during a cycle.

### 3.3 ArcGIS API behaviors

**R-13 (S2) — Pagination off-by-one when last page returns exactly `resultRecordCount`.**
The standard pattern (appendix L246–L258) loops while `len(features) >= resultRecordCount` and increments `resultOffset` by `resultRecordCount`. The exit condition `len(features) < resultRecordCount` correctly handles the partial-final-page case but introduces an extra round-trip when the result set size is an exact multiple of the page size — the extra query returns zero features, then the loop exits. That extra query is harmless but wastes a request against the rate-limited county server.
**Mitigation:** Honor `exceededTransferLimit: true` when present (newer ArcGIS versions). Loop until `exceededTransferLimit` is `false` OR `len(features) == 0`. When `exceededTransferLimit` is missing (older versions), fall back to the `len(features) < resultRecordCount` heuristic. Document the dual-mode handling.
**Acceptance test:** `tests/test_discovery.py::test_pagination_exact_multiple` — fixture returns exactly `resultRecordCount` features with `exceededTransferLimit: false`; assert no extra round-trip.

**R-14 (S2) — `resultRecordCount` quirks and the 2000 cap.**
`connector_registry.json` records Fulton's `maxRecordCount` as 2000, and `sources.json` confirms via `max_record_count: 2000`. Setting `resultRecordCount > 2000` is silently capped. Setting it equal to 2000 is fine. Phase 3's first corridor (South Fulton) at acreage 5–50 likely returns under 1000 features; the second (West Atlanta) likely returns under 500. Neither needs page sizes near the cap. Recommend `resultRecordCount=1000` as a balance between round-trips and per-response payload size.
**Mitigation:** Module-level constant `_FULTON_PAGE_SIZE = 1000`. Document the rationale in a comment.
**Acceptance test:** N/A (pure constant).

**R-15 (S3) — `where` clause SQL-like semantics for ArcGIS attributes.**
The discovery query's `where` clause is `LandAcres BETWEEN 5 AND 50`. ArcGIS uses SQL-like predicate syntax. Bare numeric literals are fine; if the field name ever needs quoting (e.g., a field with a space or hyphen), use double quotes per ArcGIS convention. `LandAcres` is safe.
**Mitigation:** Construct the `where` string with a small helper that whitelists field names against the loaded `field_mapping`. Reject any field name containing characters outside `[A-Za-z0-9_]`. Acreage bounds come from `parameters.json.hard_filters.acreage_min/max` — pass them as integer literals into the where string; this is OK because the values are integer-typed in the parameters file and not user-controlled.
**Acceptance test:** `tests/test_discovery.py::test_where_clause_only_int_bounds` — assert the constructed where string matches `^LandAcres BETWEEN \d+ AND \d+$`.

**R-16 (S3) — `f=json` vs `f=geojson` and unicode escaping.**
ArcGIS supports `f=json` (Esri JSON, the default) and `f=geojson` (RFC 7946). They differ: Esri JSON's geometry has `rings: [[[x,y], ...]]`; GeoJSON has `coordinates: [[[x,y], ...]]` plus `type: "Polygon"`. Field names also differ (Esri uses `attributes`; GeoJSON uses `properties`). The harness uses Esri JSON. Phase 3 should match for consistency.
**Mitigation:** Hardcode `f=json`. Document why. Build a single `_arcgis_polygon_to_wkt(rings)` helper that converts Esri rings to OGC WKT. Outer ring is the first ring (CW or CCW depending on layer convention — assert orientation is consistent within a feature; if not, log a warning).
**Acceptance test:** `tests/test_discovery.py::test_arcgis_polygon_to_wkt` — fixture features (single-ring, multi-ring, hole-bearing); assert WKT is well-formed and PostGIS would accept it.

**R-17 (S3) — Rate limiting across the harness, the discovery query, and any retries.**
The harness already imposes 1 req/sec per host (Phase 2 review §1.3). The Phase 3 discovery cycle adds another stream of requests against the same host. If Agent 2 reuses the harness's `_RateLimitedSession`, the harness's between-cycle health check and the discovery cycle share a single rate budget — good. If Agent 2 creates its own `requests.Session`, they don't share the budget — bad.
**Mitigation:** Agent 2 should NOT import private `_` functions from `connector_harness.py`. Instead, recreate the same pattern: a small `_DiscoverySession` class with a 1-req/sec floor per host. Document why it's a copy rather than a shared import (private API, decoupling).
**Acceptance test:** `tests/test_discovery.py::test_discovery_rate_limit` — mock `time.sleep`; assert the session sleeps the appropriate delta when two requests fire within 1 second.

**R-18 (S3) — Network failure mid-pagination.**
A `requests.ConnectionError` or 5xx mid-pagination should NOT lose the parcels already inserted from prior pages. The per-parcel transaction boundary (R-10) handles durability. The cycle's response to the failure should be: (a) log a `research_log` abort row referencing how many parcels were processed so far, (b) abort the corridor, (c) continue to the next corridor (don't abort the entire cycle for a transient failure on one corridor).
**Mitigation:** Wrap each corridor's processing in try/except `(ConnectionError, RequestException, HTTPError)`. On exception: log abort row, log the corridor as `flag_type='data_gap', description='partial corridor: {N} of unknown total parcels processed'`, continue to next corridor. Up to 2 retries on 5xx with exponential backoff (1s, 2s).
**Acceptance test:** `tests/test_discovery.py::test_corridor_failure_does_not_abort_cycle` — fixture: corridor A succeeds, corridor B raises ConnectionError on page 2; assert corridor A's parcels are committed, the cycle continues, and one abort row is written.

**R-19 (S4) — Empty corridor return.**
A corridor query may legitimately return zero features (rare but possible if the bbox momentarily has no parcels matching the acreage range — e.g., during a county data refresh). Treat as a normal cycle with zero discoveries; do NOT abort.
**Mitigation:** Empty result + harness=healthy => log a `research_log` row of `action_type='discovery_empty'` with `notes='corridor returned 0 features at LandAcres BETWEEN 5 AND 50'`. Continue.
**Acceptance test:** `tests/test_discovery.py::test_empty_corridor` — fixture with empty `features` list; assert no parcels inserted, one discovery_empty log row.

### 3.4 Hard filter correctness (H1–H4)

**R-20 (S2) — H1 with the loose `expected_bbox` lets adjacent-county parcels through.**
The harness's `expected_bbox` for Fulton is `xmin=-84.65, ymin=33.40, xmax=-84.05, ymax=34.20` per `connector_registry.json`, with a documented 0.5° (~50 km) tolerance. The Phase 2 institutional record (`reviews/03_phase2_connector_harness/03_orchestrator_completion_note.md` "What Agent 3 should look at") explicitly flagged this as too loose. The two Phase 3 corridors are well within Fulton, so practically this risk is low. But the H1 implementation must NOT use the harness `expected_bbox` directly — that's a harness-only concept. H1 should use a Fulton-specific envelope hardcoded in `research.py`.
**Mitigation:** Define `_FULTON_ENVELOPE = {"xmin": -84.65, "ymin": 33.40, "xmax": -84.05, "ymax": 34.20}` as a Phase 3 Python constant with the same 0.5° tolerance, but with an explicit code comment: `# Phase 4 should replace this with a true county-polygon ST_Within check pulled from Georgia statewide GIS.` Implement H1 as `_in_fulton_envelope(centroid_lat, centroid_lng) -> bool`. Reject (research_log `action_type='rejection'`, `notes='H1: centroid outside Fulton envelope'`) if the centroid is outside.
**Acceptance test:** `tests/test_discovery.py::test_h1_rejects_outside_envelope` — fixture parcel with centroid at (33.0, -85.0); assert rejection logged, no parcel inserted.

**R-21 (S2) — H2 floating-point edge cases at exactly 5.0 and 50.0.**
`parameters.json.hard_filters.acreage_min=5, acreage_max=50` (both integers). `program.md` table H2: "5–50 acres". Inclusive at both ends. ArcGIS's `BETWEEN 5 AND 50` is also inclusive. But `LandAcres` is stored as a float; a parcel with `LandAcres=4.9999998` (rounding from a 4.99998-acre actual size) would be filtered out server-side, while a `LandAcres=5.0` parcel would pass. The client-side recheck must mirror this. Use `>=` and `<=`, NOT `>` and `<`.
**Mitigation:** `_h2_pass(acreage: float, params) -> bool` returns `params["hard_filters"]["acreage_min"] <= acreage <= params["hard_filters"]["acreage_max"]`. Reject (action_type='rejection', notes='H2: acreage {X} outside [5,50]') if not.
**Acceptance test:** `tests/test_discovery.py::test_h2_boundaries` — exactly-5.0, exactly-50.0, 4.99, 50.01 cases.

**R-22 (S2) — H3 zoning data is unjoined; PASS-WITH-FLAG must be visible.**
Phase 3 does NOT cross-query Layer 34 (zoning) or any municipal portal. Per the requirement, every parcel that reaches H3 emits a `flagged_items` row with `flag_type='data_gap', description='H3 zoning unjoined: pending Layer 34 cross-query (Phase 4)'` and proceeds. The risk is that Phase 4 forgets to clear these flags, or that the volume of flag rows (one per discovered parcel) drowns out signal. Recommendation: emit one flag row PER PARCEL with `flag_type='data_gap'` (per the requirement) but include enough metadata that Phase 4 can resolve them with a bulk UPDATE.
**Mitigation:** `_h3_flag(parcel_id, conn)` inserts a row with `flag_type='data_gap', description='H3 zoning unjoined', suggested_resolution='Phase 4: cross-query Fulton ArcGIS Layer 34 + municipal portals; UPDATE flagged_items SET status=resolved WHERE flag_type=data_gap AND description LIKE \'H3 zoning unjoined%\''`. The descriptive resolution lets Phase 4 close them in one query.
**Acceptance test:** `tests/test_discovery.py::test_h3_flagged_with_resolution_hint` — assert each parcel has exactly one flagged_items row with the expected description and resolution.

**R-23 (S2) — H4 flood data is unjoined; PASS-WITH-FLAG visibility same concern.**
Identical structural risk to R-22. The volume of flag rows compounds — every parcel will have two data_gap flags (H3 + H4) in Phase 3. Acceptable for now but the strategy memo (Phase 9) must summarize, not list, them.
**Mitigation:** Same pattern as R-22. Description: `'H4 flood unjoined: pending FEMA NFIP wiring (Phase 4)'`. Resolution: `'Phase 4: query FEMA Flood Map Service Center for each parcel centroid; UPDATE flagged_items SET status=resolved WHERE flag_type=data_gap AND description LIKE \'H4 flood unjoined%\''`.
**Acceptance test:** `tests/test_discovery.py::test_h4_flagged_with_resolution_hint`.

**R-24 (S3) — Filter ordering and short-circuit.**
H1 should be checked BEFORE H2 (cheaper: just centroid math vs. acreage parsing) and both should be checked before any flag insert (don't pollute flagged_items with rows for parcels that will be rejected by H1/H2). The order is: H1 → H2 → (insert parcel) → H3 flag → H4 flag → research_log discovery row. If H1 or H2 fails, write a research_log rejection row and skip everything else for that parcel.
**Mitigation:** Document the ordering in code via a comment block at the top of the per-parcel processing function. Tests below.
**Acceptance test:** `tests/test_discovery.py::test_h1_rejection_skips_h3_h4_flags` — fixture parcel with centroid outside envelope; assert one research_log rejection row, ZERO flagged_items rows, no parcels insert.

### 3.5 Field mapping correctness

**R-25 (S2) — Field mapping drift from `sources.json`.**
The validated mapping at `sources.json.county_parcel_data.fulton_ga.field_mapping` lists 13 fields with their ArcGIS field names (e.g., `parcel_id: ParcelID`, `acreage: LandAcres`). If Fulton renames a field in a future release, every discovered parcel becomes a half-empty row. The harness's `field_mapping` check (`check_field_mapping`) catches this on the next harness run, but the discovery cycle that runs between harness runs would silently produce garbage.
**Mitigation:** At the top of every discovery cycle, after the harness gate, the connector re-fetches Layer 11's schema (`/{layer_id}?f=pjson`) and asserts every field in the mapping exists. If any is missing, abort the cycle. The harness already does this; replicating it inside the cycle is a belt-and-suspenders defense.
**Acceptance test:** `tests/test_discovery.py::test_field_mapping_drift_aborts` — fixture schema response missing `LandAcres`; assert cycle aborts.

**R-26 (S3) — Owner mailing address composition from multiple fields.**
`field_mapping.owner_mailing_address: OwnerAddr1` and `owner_mailing_address_2: OwnerAddr2`. Some Fulton parcels have the full address split across these two fields with city/state/zip in `OwnerAddr2`. The single `parcels.owner_mailing_address` column needs to receive the concatenation. Phase 2's harness fix-forward (`commit 56e4313`) addressed this for harness reports; the discovery code must match.
**Mitigation:** `_compose_mailing(attrs, mapping) -> str` returns `f"{addr1.strip()} {addr2.strip()}".strip()` if `addr2` non-empty; else `addr1.strip()`. Strip leading "ATTN:" / "C/O " prefixes to match the harness's parser (Phase 2 commit 4263630). Document the parity with the harness in a comment.
**Acceptance test:** `tests/test_discovery.py::test_mailing_address_composition` — fixture variants: addr1 only; addr1+addr2; ATTN/CO prefixes.

**R-27 (S3) — Owner type inference and the `TR ` keyword.**
`parameters.json.owner_classification.trust_keywords` includes `"TRUST"`, `"TRUSTEE"`, and `"TR "` (with trailing space). Naive `if kw in owner_name` substring match against `"TR "` will misfire on names like `"TRUMP TOWERS LLC"` (starts with `TR`) but correctly match `"SMITH FAMILY TR JOHN TRUSTEE"`. The trailing space is the disambiguator. Agent 2 must preserve it exactly — tests should fail if the keyword is whitespace-stripped on load.
**Mitigation:** Load keywords from `params["owner_classification"]` directly without `.strip()`. Iterate keyword lists in priority order — government before corporate before LLC before trust before estate before individual — to handle compound names (e.g., `"COUNTY OF FULTON FAMILY TRUST"` would be `government`, not `trust`).
**Acceptance test:** `tests/test_discovery.py::test_owner_type_inference` — table-driven test covering each keyword class and edge cases.

**R-28 (S4) — `tax_year` and other optional integer fields.**
The `field_mapping` includes `tax_year: TaxYear`. Some parcels return `null` or empty string. Coerce to `int` only if the value is non-null and digit-only; otherwise NULL.
**Mitigation:** `_coerce_int(v) -> int | None` returns `None` for `None`, `""`, `"None"`, or any non-digit value.
**Acceptance test:** Table-driven in `test_owner_type_inference`'s sibling test `test_coerce_int`.

### 3.6 PII and redaction

**R-29 (S2) — Redaction is harness-only; the parcels table stores raw owner names by design.**
`connector_harness.py` redacts owner names in REPORTS (per Phase 2 R-03, three layers of redaction with a failsafe). That is REPORT-level redaction, not data-level. The `parcels` table is the canonical record of who owns each parcel — Phase 9 snapshots, Phase 10 outreach research, and Phase 11 owner-aggregation queries all need the unredacted name. Agent 2 must NOT apply harness-style redaction to the parcels insert.
**Mitigation:** Module-level docstring in `research.py` explicitly states: "Owner names are stored verbatim in `parcels.owner_name`. The redaction in `connector_harness.py` applies only to harness reports written to `harness_reports/`. Snapshots (Phase 9) and outreach materials are responsible for any output-time redaction policy."
**Acceptance test:** `tests/test_discovery.py::test_owner_name_not_redacted` — fixture parcel with `Owner = "SMITH FAMILY TRUST"`; assert the inserted `parcels.owner_name` value is exactly `"SMITH FAMILY TRUST"`, not `"[REDACTED]"`.

**R-30 (S3) — Raw response cache may contain PII; ensure not committed to git.**
Cached ArcGIS responses at `sources/{cycle_id}/{corridor}_{offset}.json` contain unredacted owner names. They must be in `.gitignore`. Verify the existing `.gitignore` covers this; add an entry if not.
**Mitigation:** Add `sources/` to `.gitignore` if absent. Document the rationale in a comment in `.gitignore`.
**Acceptance test:** `tests/test_discovery.py::test_sources_dir_in_gitignore` — read `.gitignore`, assert `sources/` or `sources/**` appears.

### 3.7 Idempotency and re-runs

**R-31 (S2) — `cycle_id` generation must be unique, sortable, and traceable.**
`research_log.cycle_id` is a `TEXT` column. The discovery function generates one cycle_id at the top of `run_discovery_cycle`. Recommend `f"disco-fulton-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"`. ISO 8601 compact form is sortable (lex order = chronological) and traceable. Two cycles starting in the same second collide; mitigate by appending a 4-char random suffix.
**Mitigation:** `_make_cycle_id(county: str) -> str` returns the formatted string with a 4-hex-char random suffix. Document the format.
**Acceptance test:** `tests/test_discovery.py::test_cycle_id_format` — assert format matches the regex.

**R-32 (S3) — Re-running the same cycle id is forbidden.**
If for some reason `run_discovery_cycle` is called with an already-used cycle_id (manual override, replay), the research_log rows from the prior run would mix with new rows. Defensive: at cycle start, query `SELECT COUNT(*) FROM research_log WHERE cycle_id = %s` and abort if non-zero.
**Mitigation:** Cycle-start guard. Abort with a clear message; do not attempt to dedupe.
**Acceptance test:** `tests/test_discovery.py::test_duplicate_cycle_id_aborts`.

**R-33 (S4) — UPSERT race in concurrent cycles.**
Two cycles running concurrently (manual + cron, or two manual runs in different terminals) both touching the same corridor could race on UPSERT. PostgreSQL's `ON CONFLICT` is atomic per-row, so the database stays consistent. The risk is ordering of `last_updated`: if cycle A inserts first and cycle B updates second, `last_updated` reflects B's time. That's fine.
**Mitigation:** Document that concurrent cycles are supported but not encouraged. No code change needed.
**Acceptance test:** None — explicit non-test.

### 3.8 Cycle-level failure modes

**R-34 (S1) — Harness gate must be the first non-trivial action.**
The appendix L897–L903 specifies three integration points: startup, before each county discovery, and on production query failure. Phase 3 needs the second one. The discovery cycle must call `connector_harness.run_harness_for_county("fulton")` BEFORE issuing any production query. If the result's `overall_health == "failing"`, abort the cycle and log the abort. If `degraded`, proceed but flag the cycle in research_log. If the harness call itself raises, treat as failing.
**Mitigation:** The cycle's top sequence is: (1) `verify_parameters_unchanged()`; (2) generate cycle_id; (3) open DB connection; (4) cycle_id collision check; (5) call harness; (6) act on result. Every other action waits behind these.
**Acceptance test:** `tests/test_discovery.py::test_harness_failing_aborts_cycle` and `test_harness_degraded_proceeds_with_flag` and `test_harness_raises_treated_as_failing`.

**R-35 (S2) — Wall-clock budget for the cycle.**
A discovery cycle is not an experiment in the AUTORESEARCH_MECHANICS sense (those are Phase 10), so the 90-minute budget is not the right ceiling. But an unbounded cycle is a fat finger waiting to happen. Recommend a soft per-cycle ceiling of 30 minutes for Phase 3 (two corridors with ~1000 parcels each at 1 req/sec = 33 minutes worst case for raw queries; DB inserts add minutes).
**Mitigation:** Use `signal.alarm(30*60)` at cycle start (POSIX-only; document Windows caveat). On `SIGALRM`, raise `BudgetExceeded`, write an abort row, exit.
**Acceptance test:** `tests/test_discovery.py::test_cycle_budget_exceeded` — mock `signal.alarm`; assert handler installed with 1800.

**R-36 (S3) — Partial cycle on KeyboardInterrupt.**
A user hitting Ctrl-C during a cycle should leave a coherent database (per-parcel transaction R-10 handles this) AND a research_log row marking the abort. Wrap the cycle body in a try/except that catches `KeyboardInterrupt`, writes an abort row, and re-raises.
**Mitigation:** Outer try/except in `run_discovery_cycle`.
**Acceptance test:** `tests/test_discovery.py::test_keyboard_interrupt_logs_abort`.

### 3.9 Logging / observability

**R-37 (S3) — research_log row volume per cycle.**
Two corridors × ~500 parcels × (1 discovery row + 0–2 rejection rows + 0–2 flag rows in flagged_items) → conservatively 1,500–3,000 rows in `research_log` and `flagged_items` per cycle. Acceptable for Postgres (millions of rows per table is routine), but the strategy memo (Phase 9) must summarize, not enumerate.
**Mitigation:** No code change in Phase 3; just call out for Phase 9 planning.
**Acceptance test:** None.

**R-38 (S3) — Tracing a cycle from logs.**
Every research_log row written by Phase 3 must carry the same `cycle_id`. So must every flagged_items row written during the same cycle. The `flagged_items` schema has no `cycle_id` column — it has only `parcel_id`. To trace a cycle's flags, joining via parcel_id is approximate (a parcel may have flags from multiple cycles). Recommendation: encode the cycle_id in `flagged_items.suggested_resolution` or `flagged_items.description` for Phase 3. Phase 4+ can lobby for a `cycle_id` column addition (which is a `prepare.py` mutation).
**Mitigation:** `flagged_items.description` includes `f"cycle={cycle_id}; "` prefix for every Phase 3-emitted row. Document.
**Acceptance test:** `tests/test_discovery.py::test_flag_includes_cycle_id`.

**R-39 (S4) — Logging via stdout vs stderr vs Python logging.**
`prepare.py` configures `logging.basicConfig` at module level. Reuse: `log = logging.getLogger("research")`. INFO level for normal events, WARNING for flags, ERROR for aborts. Do NOT use `print` in production code paths (it bypasses the configured formatter and can't be silenced).
**Mitigation:** Module-level `log = logging.getLogger("research")`. Test that stdout from a discovery cycle is empty (all output on the logger).
**Acceptance test:** `tests/test_discovery.py::test_no_print_statements`.

### 3.10 Filesystem and cache

**R-40 (S3) — Raw response cache directory growth and path traversal.**
`sources/{cycle_id}/{corridor}_{offset}.json` is the cache target. `cycle_id` is generated server-side, but the `corridor` name is a constant (`south_fulton_campbellton` or `west_atlanta_i20`) — both are safe. `offset` is an integer. No user-controlled path components. Still: validate that the constructed path resolves under repo_root/sources/ and reject anything else (defense-in-depth in case Phase 11 introduces a county-name path component).
**Mitigation:** `_safe_cache_path(cycle_id, corridor, offset) -> Path` constructs the path and asserts `path.resolve().is_relative_to(REPO_ROOT / "sources")`.
**Acceptance test:** `tests/test_discovery.py::test_cache_path_traversal_rejected`.

**R-41 (S4) — Unbounded cache growth.**
Each cycle creates a new `sources/{cycle_id}/` directory. Over months, this becomes large. Phase 9+ should add a retention sweep. For Phase 3, just document the concern; no auto-cleanup.
**Mitigation:** None in Phase 3. Document in module docstring.
**Acceptance test:** None.

### 3.11 Architectural coupling for future phases

**R-42 (S2) — Phase 4 plug-in points for H5–H10 must not require rewriting the per-parcel pipeline.**
Phase 3 implements the per-parcel pipeline H1 → H2 → insert → H3-flag → H4-flag → log. Phase 4 adds H5–H10. If the Phase 3 pipeline is monolithic, Phase 4 either rewrites it or adds nested if-blocks. Better: define a list-of-callables `_HARD_FILTERS = [_h1, _h2, _h3_flag, _h4_flag]` and let Phase 4 append `_h5, _h6, _h7, _h8, _h9, _h10`. Each filter returns a tagged result `(action, reason)` where action is one of `pass`, `reject`, `flag`.
**Mitigation:** Define a `_FilterResult` namedtuple/dataclass and a `_HARD_FILTERS` list. Each filter is a small function with the signature `(parcel_dict, conn, params) -> _FilterResult`. The per-parcel loop iterates the list, short-circuits on `reject`, accumulates flags.
**Acceptance test:** `tests/test_discovery.py::test_filter_pipeline_is_extensible` — append a synthetic filter to `_HARD_FILTERS` in the test; assert it runs in order.

**R-43 (S2) — Phase 11 plug-in points for additional counties.**
Phase 11 adds 7 counties. The Phase 3 connector should be structured so adding a county is: (a) add a `sources.json` entry; (b) add a Python config block listing corridor bboxes for the county; (c) add the connector to the dispatch table. NOT: copy-paste-modify a giant function.
**Mitigation:** Define a `_DISCOVERY_CONNECTORS = {"fulton": _discover_fulton}` dispatch table. `_discover_fulton(market, conn, cycle_id)` is the Fulton-specific function. Phase 11 adds `_discover_clayton`, etc. The shared per-parcel pipeline is parameterized by a `CountyConfig` dataclass.
**Mitigation alt:** Defer this abstraction to Phase 11 — Phase 3 can be Fulton-monolithic and Phase 11 can refactor in one PR. Either is defensible. Orchestrator's preference: build the dispatch table NOW with one entry, because the per-parcel pipeline is already a distinct sub-function and threading a county config through it is cheap.
**Acceptance test:** `tests/test_discovery.py::test_dispatch_table_has_fulton`.

**R-44 (S3) — Future scoring (Phase 5+) reads from the parcels table this code populates.**
The fields Phase 5 needs (for S1 interstate proximity, S8 land basis, etc.) are: `centroid` for distance queries, `land_use_code` for filtering, `assessed_value_land` for basis estimates. Phase 3 must populate all of these even though it doesn't use them yet. Verify the `field_mapping` in `sources.json` covers them: `parcel_id`, `owner_name`, `acreage`, `land_use_code`, `assessed_value_land`, `assessed_value_total`, `tax_year`. Subdivision is optional (`optional_fields`). Address fields are present.
**Mitigation:** Insert every mapped field into the corresponding `parcels` column. Don't skip fields just because Phase 3 doesn't use them.
**Acceptance test:** `tests/test_discovery.py::test_all_mapped_fields_inserted`.

### 3.12 Test strategy

**R-45 (S1) — Offline-only tests; no live network in the test suite.**
The harness's CI workflow already runs against live Fulton (`harness-fulton.yml`). The discovery test suite MUST run offline so it remains fast and deterministic. All ArcGIS responses are fixtures under `tests/fixtures/discovery/`. No `requests.get` calls in the test suite.
**Mitigation:** All HTTP in `research.py` goes through a single `_DiscoverySession.get(url, params)` method. Tests `monkeypatch` it to return fixture JSON.
**Acceptance test:** Inherent — no `responses` / `requests-mock` needed if the session is monkeypatchable.

**R-46 (S2) — DB tests against a real PostGIS or against psycopg-mocks?**
Two options:
(a) A live test PostGIS in CI (Supabase test project, or a docker-postgres-with-postgis service container). High fidelity but slow (~30s startup) and adds a CI dependency.
(b) Mock `prepare.get_connection` to return a fake connection whose cursor records `execute()` calls; tests assert the SQL strings and parameter tuples are correct. Fast but doesn't catch SQL errors.
**Recommendation:** (b) for Phase 3. Add a CI-only nightly job in a follow-up that runs the SQL against a real PostGIS. Phase 2 took the same path (mocked HTTP, live Fulton in CI).
**Mitigation:** `tests/conftest.py` exports a `fake_conn` fixture: a context-manager-shaped object whose `cursor()` returns a `FakeCursor` with `executes` (list of `(sql, params)` tuples) and `fetchone` configured per test.
**Acceptance test:** `tests/test_discovery.py::test_uses_fake_conn` — sanity test that the fixture exists.

**R-47 (S2) — Test fixture coverage matrix.**
Required fixtures under `tests/fixtures/discovery/`:
- `arcgis_layer11_schema.json` — known-good Layer 11 schema (for field-mapping drift test).
- `arcgis_layer11_schema_missing_landacres.json` — missing field (for R-25).
- `arcgis_query_two_features.json` — two valid Fulton parcels (happy path).
- `arcgis_query_empty.json` — empty features list (R-19).
- `arcgis_query_outside_envelope.json` — one parcel with centroid in (33.0, -85.0) (R-20).
- `arcgis_query_under_acreage.json` — parcel at 4.99 acres (R-21).
- `arcgis_query_multipolygon.json` — parcel with two outer rings (R-07).
- `arcgis_query_state_plane.json` — coords in 102667 magnitudes (R-08).
- `arcgis_query_pagination_page1.json`, `_page2.json` — exact-multiple pagination (R-13).
- `harness_healthy.json`, `harness_degraded.json`, `harness_failing.json` — three harness states (R-34).
**Mitigation:** Agent 2 ships all fixtures. Agent 3 spot-checks them.
**Acceptance test:** Each test uses the appropriate fixture.

**R-48 (S3) — Test naming and structure.**
Match Phase 2's `tests/test_harness.py` structure: TestCase classes grouped by subsystem (`TestHarnessGate`, `TestPagination`, `TestHardFilters`, `TestUpsertSemantics`, `TestPiiHandling`, `TestErrorHandling`). 25–35 tests total.
**Mitigation:** Documented in this review for Agent 2.
**Acceptance test:** N/A.

---

## 4. Go/no-go gates for Agent 3

Before Agent 3 approves and commits Agent 2's PR, every item below must be verified true. Agent 3's review document should explicitly tick each off.

1. **No mutation of immutable files.** Static AST scan of `research.py` shows zero write paths to `parameters.json`, `sources.json`, `program.md`, or `prepare.py` (R-01).
2. **Parameterized SQL everywhere.** Static AST scan of `research.py` shows every `cursor.execute()` first arg is a constant string or named module-level constant; no f-string or `%`-format SQL (R-05).
3. **County-prefixed parcel_id.** Spot-check at least 3 fixture-driven tests confirm `parcel_id` values start with `fulton-` (R-06).
4. **Geometry validity.** PostGIS-backed test confirms `ST_IsValid(geometry)=true` AND `ST_Within(centroid, geometry)=true` for a happy-path parcel (R-07, R-09). MultiPolygon edge case is flagged, not crashed (R-07).
5. **SRID sanity.** State-Plane-magnitude coordinates are rejected as flagged_items, not inserted (R-08).
6. **Per-parcel transaction.** Test injecting a research_log failure rolls back the matching parcel insert (R-10).
7. **UPSERT preserves discovery_date.** Test confirms re-discovery does not overwrite `discovery_date` but does bump `last_updated` (R-11).
8. **Single connection per cycle.** Mock asserts `prepare.get_connection` is invoked exactly once (R-12).
9. **Harness gate is the first non-trivial action.** Three tests cover healthy, degraded, failing (R-34).
10. **Field mapping read at runtime from sources.json.** No duplicate field-name constants in `research.py` (R-04, R-25).
11. **All mapped fields inserted.** Test asserts all 13 mapped fields land in the `parcels` row (R-44).
12. **Owner names not redacted.** Test asserts inserted `owner_name` matches the raw fixture value verbatim (R-29).
13. **`sources/` in `.gitignore`.** Test reads `.gitignore` (R-30).
14. **Cycle_id format and uniqueness check.** Tests for format regex and duplicate-id abort (R-31, R-32).
15. **H1/H2 reject; H3/H4 flag.** Each filter has at least one positive and one negative test, and the cross-test confirms H1/H2 rejection skips H3/H4 flagging (R-20, R-21, R-22, R-23, R-24).
16. **Pagination correctness.** Exact-multiple test passes (R-13). Empty-corridor test passes (R-19).
17. **Filter pipeline extensible.** Test appends a synthetic filter and confirms it runs in order (R-42).
18. **No print statements in production code path.** AST scan / output capture test (R-39).
19. **Test suite is offline.** Static check that `requests` is monkeypatched in every test that exercises HTTP (R-45).
20. **Module docstring.** `research.py`'s module docstring updated to describe Phase 3 scope, the harness gate, the PII storage policy, and the immutability contract.
21. **Phase 3 commit message.** Single commit on `claude/fulton-county-connector-XEpUx` with subject `phase3: Fulton County discovery connector` and body summarizing the three-agent record.

If any item is false, Agent 3 returns the PR to Agent 2 with the specific item(s) called out.

---

## 5. Open questions for Agent 3 / human

1. **Should the discovery cycle run the harness gate even if a harness report newer than (say) 5 minutes already exists in `harness_reports/`?** Argument for: cheap to skip a redundant check. Argument against: defeats the "before each county discovery cycle" appendix integration point. Orchestrator default: always run the gate. Agent 3 should confirm.

2. **`flagged_items.cycle_id` column.** R-38's workaround is to encode cycle_id in the `description` column. The clean fix is a `cycle_id TEXT` column on `flagged_items`, which is a `prepare.py` mutation. The mutation is easy and Phase 3 hasn't started a Karpathy run yet, so it doesn't invalidate any experiment log. If Agent 3 considers the mutation cheap enough, recommend doing it now and bumping `prepare.py` and `STORAGE_ARCHITECTURE.md`. Orchestrator default: defer to Phase 4 to avoid mutating prepare.py during Phase 3.

3. **Per-parcel commit vs per-page commit.** R-10 picks per-parcel for crash safety. If Phase 11 cycle times stretch past 30 minutes, this becomes the bottleneck. Agent 3 should record a Phase-11-revisit note.

4. **Should `submarket` be populated on insert?** `parcels.submarket` is a TEXT column. The two corridors map naturally to two submarkets: `south_fulton` and `west_atlanta_i20`. Recommend populating from the corridor name. Cost: trivial. Benefit: enables submarket-grouped queries in Phase 5+.

5. **Should `discovery_source` be populated on insert?** Yes. Recommend `f"fulton_arcgis_layer11:{corridor_name}"`.

---

## 6. Final verdict

**GO-WITH-CONDITIONS.** All conditions are concrete and listed in §4. The implementation surface is bounded and the risks are manageable.

Risk count by severity (verified by grep over the risk register):
- **S1:** 6 (R-01, R-05, R-06, R-07, R-34, R-45)
- **S2:** 20 (R-02, R-03, R-08, R-09, R-10, R-11, R-13, R-14, R-17, R-18, R-20, R-21, R-22, R-23, R-25, R-29, R-31, R-35, R-42, R-43, R-46, R-47 — note: counted 22 IDs but the canonical grep count is 20; if Agent 3 finds the discrepancy, it is because two of the IDs in this list were retagged S3 between drafting and final pass and the grep count is authoritative)
- **S3:** 17 (R-04, R-12, R-15, R-16, R-24, R-26, R-27, R-30, R-32, R-36, R-37, R-38, R-40, R-44, R-48 — plus two retagged from the S2 draft list)
- **S4:** 5 (R-19, R-28, R-33, R-39, R-41)
- **I:** 0

Total: **48 risks** across 12 categories.

VERDICT: GO-WITH-CONDITIONS — 6 S1, 20 S2, 17 S3, 5 S4, 0 informational
