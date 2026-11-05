# 02 — Zoning import, rules and S9: risk register and dispositions

> **Tier 2:** this change adds a protected data file (`zoning_rules.json`),
> a protected module (`zoning.py`) and a between-runs operator tool
> (`zoning_import.py`), and it changes `research.py` (the agent's sandbox)
> between runs. The risk register below came from an independent
> **pre-change** reviewer, a context that did not write the code. The
> dispositions are the implementer's. A fresh-context **post-change**
> adversarial review is still owed and is not part of this document. Model
> names are omitted, as in `reviews/18_test_readiness/`.

## The operator's decisions (binding)

1. `zoning_import.py` is a protected operator tool, run between runs only.
   It finds each parcel's jurisdiction (Fulton TaxDist) and district, and
   writes `parcels.zoning` / `zoning_description`. It prints counts only.
   Run it with `make zoning-import`.
2. The zoning source endpoints are read from `sources.json`, which is
   human-only. The snippet to paste is
   `reviews/21_zoning/sources_json_snippet.json`, under key
   `county_parcel_data.fulton_ga.zoning_sources`. While the key is absent
   the tool exits 1.
3. `zoning_rules.json` is a human-owned facts file. Every jurisdiction is
   `draft` for now, and the agent never edits it.
4. `zoning.py` is a protected classifier with four classes: by_right,
   conditional, rezoning, unknown. It also reports whether a
   jurisdiction's rules are verified.
5. `research.py`:
   - (a) the upsert keeps imported zoning;
   - (b) S9 uses zoning only for verified jurisdictions (10 / 7 / 4 / 1)
     and is unchanged otherwise;
   - (c) H3 becomes a zoning-aware data_gap flag.
6. Nothing human-only is edited, and the schema does not change.

## What shipped

| File | Role |
|---|---|
| `zoning_rules.json` | Human-owned. Built from `01_zoning_rules_DRAFT.json` with stable keys: `south_fulton`, `fairburn`, `atlanta`, `union_city`, `east_point`, `fid`. Each jurisdiction has `status: "draft"`, `as_of`, `verified_by` / `verified_on` (null), and `unlisted_districts: "unknown"`. Each district has a `warehouse_distribution` {status, cite, confidence} plus the other uses, dimensions and notes from the DRAFT. `fid` adds `governing_jurisdiction: "south_fulton"`, `district_conversions {"M-1A": "M-1"}` and `unincorporated_parcel_ids: []`. Atlanta I-1/I-2 add a `location_conditions` entry for the Beltline exception (`effect: "ignored"`). |
| `zoning.py` | Protected. Loads the rules once, validates them, deep-freezes them and records their SHA-256. Defines the stored encoding, `normalize`, `classify`, `classify_stored` and `h3_description`. Imports only the standard library. |
| `zoning_import.py` | Protected operator tool (`make zoning-import`). Imports only `prepare` and `zoning`. |
| `research.py` | Upsert COALESCE. A guarded S9. H3 as a data_gap flag. Cycle-entry rules sentinel. Zoning reads happen only while some jurisdiction is verified. |
| `reviews/21_zoning/sources_json_snippet.json` | The exact list to paste, with key path, precedence order and role (zoning or flu). |
| Tests | `tests/test_zoning.py` (28), `tests/test_zoning_import.py` (33), `tests/test_zoning_scoring.py` (24) and `tests/test_zz_human_files_unchanged.py` (1). Fixtures: `tests/fixtures/zoning/golden_scores_head_ba4a780.json` (golden record from the pre-zoning code) and `spike_codes.json` (95 distinct spike codes). `tests/live_zoning_check.py` runs against real PostGIS with no network. |

### Stored encoding (Z-02)

```
parcels.zoning             = <district code as the source gives it, sanitised, <= 40 chars>
parcels.zoning_description = "zoning_import v1 | <jurisdiction key> | <source key> | <district code> | <split|-> | <source description>"
```

A city with no machine-readable zoning keeps `zoning` NULL. It gets
`"zoning_import v1 | <jurisdiction> | - | - | - | no machine-readable zoning"`,
written only when both columns were empty. `zoning.parse_stored()` reads
the encoding back. It requires the district inside the description to
equal `parcels.zoning`. Anything else reads as `unknown`.

## Live check counts (local PostGIS, throwaway clone, real public layers)

The checks ran on a copy of the `landtest` database: 1,075 real Fulton
corridor parcels. The snippet was applied only to the throwaway clone's
`sources.json`.

- **First import (2 min 55 s):** 1,071 changed, 4 unmatched_kept, 0
  drift, 0 precedence conflicts. The spike assigned 1,072. Against the
  spike's districts, 1,070 of 1,075 agree; 5 differ at overlay edges.
  - **Requests per host:** gis.atlantaga.gov 15,
    gismaps.fultoncountyga.gov 33, services.arcgis.com 49,
    services3.arcgis.com 24, services8.arcgis.com 49. All at 1 req/s per
    host.
- **Second import:** 0 changed, 1,071 unchanged (SR-9). The parcel count
  was 1,075 before and after.
- **Per jurisdiction (parcels / matched / unmatched):**

  | Jurisdiction | Parcels | Matched | Unmatched |
  |---|---|---|---|
  | atlanta | 335 | 335 | 0 |
  | fairburn | 239 | 237 | 2 |
  | fid | 24 | 24 | 0 |
  | south_fulton | 365 | 364 | 1 |
  | union_city | 112 | 111 | 1 |

  East Point has a source but 0 corridor parcels, so it was not queried.
- **Per source:**

  | Source | Candidates | Matched | Split | Slivers |
  |---|---|---|---|---|
  | south_fulton_zoning | 389 | 365 | 7 | 1 |
  | fulton_layer34_zoning | 389 | 23 | – | 2 |
  | fairburn_zoning | 239 | 237 | – | – |
  | union_city_zoning | 112 | 111 | – | – |
  | atlanta_zoning | 335 | 335 | 7 | – |

  The 23 layer-34 matches are FID parcels the South Fulton city layer
  does not cover (it matched 364 South Fulton parcels and 1 FID parcel).
  Precedence worked as intended.
- **Entitlement class against the shipped rules:** by_right 359,
  conditional 0, rezoning 98, unknown 618.
  - By jurisdiction:

    | Jurisdiction | by_right | rezoning | unknown |
    |---|---|---|---|
    | atlanta | 49 | 0 | 286 |
    | fairburn | 87 | 30 | 120 |
    | fid | 20 | 0 | 4 |
    | south_fulton | 187 | 68 | 109 |
    | union_city | 16 | 0 | 95 |

    4 parcels have no zoning.
  - All the rules are draft, so none of these classes reaches S9.
- **Scoring before and after the import, byte for byte.** The
  pre-zoning research.py (ba4a780) scored the copy without zoning. The new
  research.py scored the copy with 1,071 parcels zoned.
  - 1,075 `parcel_scores` rows each. composite, confidence,
    actionability, blockers, strategy_fit, sub_scores, primary_strategy
    and notes are all **identical**.
  - `actionable_pipeline_count` is 15 in both.
- **`tests/live_zoning_check.py`:**
  - The precedent query at 1,500 m and 1,700 m, with a self-match row,
    returns exactly 1 hit. A degree-unit variant would return 2. The batch
    query agrees.
  - With seq scans off, the plan uses `idx_parcels_geometry`. On the
    `landtest` copy the natural plan is an index scan on
    `idx_parcels_geometry`.
  - The overlay handles majority, split, tie (alphabetical) and sliver
    cases. Only zoning columns are updated, `last_updated` is untouched, a
    second plan changes 0 rows, and the check's own database is dropped.
- **Guard:** a non-local DSN is refused (exit 2).

## Risk register and dispositions

| # | Severity | Risk (short) | Disposition |
|---|---|---|---|
| Z-01 | blocking | No freeze on `zoning_rules.json` during a run. | **Done.** `zoning.py` loads the file once at import and records its SHA-256. `verify_rules_unchanged()` raises `ZoningRulesChanged` on any byte change, including the file appearing or disappearing. research.py calls it at `run_discovery_cycle` and `run_scoring_cycle` entry (and in ad-hoc `score_parcel`), next to `prepare.verify_parameters_unchanged()`. `load_rules()` returns the frozen MappingProxy and never re-reads. The between-runs rule and the new run + baseline are documented in CLAUDE.md, AUTORESEARCH_MECHANICS.md and README. Tests: `FreezeTests` (a, c) and `CycleEntryFreezeTests` (b: verify runs before connect; a raise aborts both cycles before any DB work). |
| Z-02 | blocking | No jurisdiction column; codes are not unique across cities; names did not match. | **Done.** There is one encoding, owned by `zoning.py`; see above. Stable keys are shared by `zoning_rules.json`, the snippet and `zoning_import.TAXDIST_TO_JURISDICTION` (copied, not imported, from the screener). Tests: (a) `StoredEncodingTests.test_round_trip`; (b) `test_rules_cover_exactly_the_snippet_zoning_jurisdictions`; (c) `test_keys_are_known_and_declare_required_fields`; (d) `test_same_code_in_two_jurisdictions_uses_its_own_rules`. |
| Z-03 | blocking | The byte-identical-while-draft guarantee could break quietly. | **Done.** With all rules draft (or rules unavailable), the new code issues **no** new query: the zoning reads are gated on `zoning.any_verified()`. The precedent query runs only for a verified rezoning parcel. `_compute_s9` returns int `_S9_MODERATE_DEFAULT` (5) for draft, unknown and unparsable input. A missing or invalid file never raises in scoring: it degrades to the stub plus ONE cycle-level data_gap event (parcel_id NULL). Golden test: `ByteIdenticalWhileDraftTests` runs ba4a780's recorded output against the new code. It covers 12 parcels across every class and jurisdiction, null zoning, `-C`, `OVERLAY`, East Point, an uncovered city, FID M-1A, a split and a forged value. It checks both paths (per parcel and the cycle). It asserts equal result dicts, **every executed statement and parameter**, the parcel_scores insert tuples and the research_log notes. The missing and malformed variants allow exactly the one extra cycle-level flag. Live: the before/after diff above is identical. |
| Z-04 | blocking | The import mutating more than zoning, or a partial or truncated write. | **Done.** The only parcels write is the module constant `UPDATE parcels SET zoning = %s, zoning_description = %s WHERE parcel_id = %s`. There is no INSERT or DELETE on parcels and no other column. `last_updated` is **deliberately not touched**: it records discovery refreshes. No match keeps the old value (`unmatched_kept`). A source that errors, a truncated id list or page, a short page, an error body or a budget overrun fails that source whole, and nothing of it is used. Network work happens with no transaction open. Then the overlays, plan and UPDATEs run in ONE transaction, committed once; the parcel count is checked before commit, and a mismatch rolls back. A second run changes 0 rows. Tests: `StaticTests`, `EndToEndTests` (fail, truncate, short, error body, empty, idempotency, one-row rezoning), and the live parcel count. |
| Z-05 | high | An import mid-run; a TSV-only check is fragile. | **Done.** Exit 3 if the TSV has the current tag's baseline (costar_import's rule, tested for agreement), or if `experiment_log_mirror` has a baseline for the current tag. Off a run branch, exit 3 also when the newest mirror row belongs to a baselined run and is not `halt`; `--no-open-run-check` overrides only this off-branch heuristic. The SQL is read-only and runner is not imported. `tests/test_mirror.py`'s fence now allows exactly these 3 `FROM experiment_log_mirror` reads in zoning_import.py (refusal only, never a metric input). Tests: `RunStateGuardTests`. The live run hit the off-branch refusal first. **Not done (optional):** autopilot wiring. The operator runs `make zoning-import` at a new run. |
| Z-06 | high | Discovery or an agent edit overwriting imported zoning (fabrication channel). | **Done, residual accepted.** S9 and H3 classify only values with import provenance. The district inside the description must equal `parcels.zoning`, so a changed code alone reads as unknown. `_map_feature_to_parcel` now sets `zoning`/`zoning_description` to None explicitly. `zoning_import` counts `drift`. Tests: `test_mapped_row_never_carries_zoning` (ZClass, Zoning, ClassCode and LUCode present, even with a `zoning` mapping key); `test_only_the_upsert_assigns_zoning_columns`; `test_values_without_provenance_are_unknown`. **Residual (SR-16 class, accepted):** a drifting agent could forge both columns in the encoded form. That needs deliberate intent; it is visible in the `exp:` diff and caught by the next import's `drift` count. |
| Z-07 | high | Districts that exist but are not listed. | **Done.** The human-owned per-jurisdiction `unlisted_districts` is `not_permitted` (rezoning) or `unknown`. It ships as `unknown` everywhere, and choosing it is part of verification (README). Tests: both settings; draft is never verified under either; schema requires the field. |
| Z-08 | high | Code normalisation and use-status vocabulary. | **Done.** `normalize()` uppercases, drops `OVERLAY` and whitespace, and maps `-C` (or `C` after a digit) to its base with `has_conditions`. That flag never downgrades the class and is shown in the H3 text. Vocabulary: permitted_by_right → by_right; special_use_permit / use_permit / conditional_use → conditional; not_permitted → rezoning; anything else → unknown. **Beltline 500 ft:** encoded as a human-owned `location_conditions` entry with `effect: "ignored"` (alternative `downgrade_to_conditional`, which makes the whole district conditional). The verifier chooses. Tests: a table over all 95 spike codes, the vocabulary, an unrecognised status → unknown, and the schema. |
| Z-09 | high | Precedent SQL units, self-match, neighbour judgement, persistence, cost. | **Done.** Module constants use `ST_DWithin(::geography, ::geography, %s)` with 1609.344 m bound, a `p2.geometry && ST_Expand(p1.geometry, 0.03)` index pre-filter, and `p2.parcel_id <> p1.parcel_id`. The query is batched once per cycle (`_SQL_ZONING_PRECEDENT_BATCH`) and runs only for verified rezoning parcels. A neighbour counts only if `zoning.py` classifies it **by_right in its own verified jurisdiction**: a draft-jurisdiction neighbour does not count. **Persistence decision:** accepted. Zoning is written only by between-runs imports, and a neighbour needs import provenance to count. A parcel discovered mid-run (even by a discarded experiment) has NULL zoning until the next import and cannot be precedent. **Known limits:** the parcels table holds only discovered 10-50 ac parcels that passed H1/H2, so precedent is undercounted. "Industrially zoned parcel within 1 mile" is not program.md gate 2's "approved rezoning within 2 miles in the past 5 years". Both are recorded here, and program.md is human-only. Tests: offline SQL shape, `test_a_draft_neighbour_is_not_precedent`; live: 1 hit of 2 plus self, the degree-unit variant matches 2, EXPLAIN uses the index. |
| Z-10 | high | Overlay correctness and dependency creep. | **Done.** The overlay runs in PostGIS: features come with outSR 4326 and become GeoJSON MultiPolygons (Esri ring orientation, holes assigned) through `ST_GeomFromGeoJSON`. A TEMP table (`ON COMMIT DROP`, inside a savepoint per source) holds them, with `ST_MakeValid` and `ST_CollectionExtract(…, 3)` on both sides. The ranking uses `ST_Area(ST_Intersection(...)::geography)` summed per district, area DESC then district code. Under 60% of the parcel counts as `split` (majority still written and marked); under 20% is a sliver (nothing written). Paging: object ids are fetched per 0.05° grid cell (a truncated id list fails), then features are fetched by objectIds in pages of min(150, maxRecordCount), and every page must return exactly its ids. Precedence is the snippet's order (South Fulton city layer before layer 34); conflicts are counted. Tests: split, tie, sliver, hole, truncated, short, plus the live overlay. `requirements.txt` is unchanged; no shapely or screener import (`BoundaryTests`). |
| Z-11 | high | Join key formats. | **Done.** `normalize_id()` strips `fulton-` and outer space, collapses runs of whitespace and uppercases; `id_trim_last` serves East Point. Layer 11 is queried with the **exact** stored id, because internal double spaces matter to the server. This was found live: 486 parcels were missed before the fix. A PIN shared by two parcels is a collision and assigns neither. Conflicting duplicates take the alphabetically first value and are counted. A join source below a 50% match rate is refused for the import. Tests: `JoinTests`. Live: 1,071 of 1,075 against the spike's 1,072. |
| Z-12 | high | Network etiquette and the screener boundary. | **Done.** `ZoningSession` (modelled on `_DiscoverySession`) enforces a 1 req/s floor per host (lock plus last-request map), at most 3 retries with back-off (2/4/8 s), and Retry-After honoured but capped at 30 s. It writes no cache and sends a descriptive User-Agent. Fetches go by envelope per source, not per parcel. A `?f=pjson` pre-flight per layer (fields present, polygon geometry) is the harness gate. A per-source budget of 400 requests applies. Tests: an injected clock (same host spaced 1.0 s; different hosts not serialised), the Retry-After cap, bounded retries, 4xx fail-fast, the budget, and AST checks (no screener import, no file writes). |
| Z-13 | high | PII leakage. | **Done.** Layer 11 is queried with `outFields=ParcelID,TaxDist` only; zoning layers with only their id, zoning and description fields. No feature attribute is ever printed. Errors give host, status and counts only; an ArcGIS error body yields only its numeric code. No raw cache is kept. The live check prints a masked DSN. Tests: `_assert_counts_only` on every run (fixture features carry owner names), and an assertion on the outFields value. |
| Z-14 | high | Protected-file and immutable-module lists incomplete. | **Done.** Updated: the CLAUDE.md invariants card; AUTORESEARCH_MECHANICS.md ("does NOT live here" list, the zoning_rules.json paragraph, and two checklist lines); STANDING_RISKS.md SR-1 (rule text and enforcement); START_HERE.md Step 1 confirmation wording; `home_machine/agent_prompt.md`; README (file tree and the "Zoning import" section); the research.py module docstring; `tests/test_discovery.py` (`ALL_PIPELINE_AST`/`ALL_PIPELINE_PY_SRC` sources; `zoning_rules.json` added to `forbidden_paths` and `FORBIDDEN`); `tests/test_mirror.py` (`_PIPELINE_MODULES` plus the 3 allowed reads); `.github/workflows/discovery-fulton.yml` (path triggers and a live-check step). `offline-tests.yml` has no path filter, so it needs no change. New tests: `ProtectionTests` covers write contexts on `zoning_rules` (open with w/a/x/+, write_text, write_bytes, json.dump, os.replace, rename, shutil.copy*, move, touch, unlink) across 11 modules; research.py reaches the rules only via `zoning` and never imports `zoning_import`; doc string-presence; the autopilot allowlist is exactly `Edit(./research.py)` and `Write(./research.py)` (`home_machine/autopilot.sh` `allowed=(...)`, unchanged). |
| Z-15 | should-fix | Wrong H3 flag type, churn, H3 reading the row's None. | **Done.** `_h3_flag` returns only `flag` and is written through `_flag_data_gap_once` as data_gap. The text comes from `zoning.h3_description`: a stable format, no dates, no `"; "`, sanitised and truncated. Example: `H3 zoning M-1 (south_fulton): by_right, rules draft`. Discovery reads the market's stored zoning once per cycle (`_SQL_STORED_ZONING_FOR_MARKET` → `_STORED_ZONING_BY_CYCLE`); a direct `_process_parcel` call without a registered cycle says "not imported". **Left over:** old open `H3 zoning unjoined: …` flags stay open; they are data_gap only and never touch actionability. The optional cleanup by the import was not done, so the import writes no flags at all (Z-16). One new open flag per parcel appears when the rules status changes (rare, between runs). Tests: H3 never rejects; research's `_flag` calls are data_gap only and there is no flagged_items INSERT SQL; actionability is identical (golden); dedup across two cycles; H3 sees the stored zoning, not the row's None. |
| Z-16 | should-fix | SR-14 action_type vocabulary. | **Done.** zoning_import writes no research_log and no flagged_items rows. Test: `test_no_research_log_or_flag_rows`. |
| Z-17 | should-fix | Snippet correctness and handling. | **Done.** The snippet carries `key_path`, the precedence order and `role` (zoning or flu); FLU entries need `flu_field` and are never queried or written. The validator requires https, known jurisdiction keys, safe field names and the kind/role fields. Absent key → exit 1 with a clear message; malformed → exit 1 naming the index only. Tests: absent, malformed (5 variants), the snippet passes, FLU never written, `connector_harness.load_registry` with the snippet applied yields the same connectors, and `test_zz_human_files_unchanged` (sources.json, parameters.json, program.md and zoning_rules.json hashed at suite load and compared last). Live runs used a throwaway clone only. |
| Z-18 | should-fix | FID decided from TaxDist alone. | **Done.** Human-owned `governing_jurisdiction: "south_fulton"`, `district_conversions {"M-1A": "M-1"}` and `unincorporated_parcel_ids` (empty; those parcels use FID's own county table). `verified` = FID verified AND governing verified. Without a mapping FID uses its own table, and an unlisted code is unknown. FID stays draft. Tests: `test_fid_resolves_to_governing_rules_with_conversion`. |
| Z-19 | should-fix | Coverage gaps. | **Done.** An uncovered jurisdiction (College Park, Palmetto, Hapeville, Chattahoochee Hills) keeps zoning NULL, is counted `no_source` per jurisdiction, and gets the 'no machine-readable zoning' description only when both columns were empty. An unknown TaxDist prefix is untouched and counted. Classification is unknown and unverified, so S9 is the stub. H3 reads `H3 zoning not machine-readable for <jurisdiction>: check with the city`. Tests: `test_uncovered_and_unknown_jurisdictions`, `test_unknown_taxdist_prefix_is_untouched`, the end-to-end no_source count. |
| Z-20 | should-fix | Stale zoning and stale rules. | **Done.** The console prints the as-of date and changed / unchanged / unmatched_kept / drift counts. Each jurisdiction carries `as_of`, `verified_by` and `verified_on`. There are no dates in the flag text and no automatic expiry. The README recommends a re-import at every new run. Test: a re-import after a fixture rezoning updates exactly one row and counts 1 drift. |
| Z-21 | should-fix | The verified gate lives in the sandbox. | **Done, residual accepted.** `zoning.classify(...)` returns (cls, verified, reason). `_compute_s9` has one guarded early return of `_S9_MODERATE_DEFAULT`. The protected test `test_repo_rules_all_draft_give_the_stub_for_every_class` fails any sandbox edit that bypasses the gate while the repo's rules are draft. **Residual (SR-16 class):** accepted. After each run, a human should review any `exp:` diff that touches `_compute_s9`, `_fetch_stored_zoning`, `_has_precedent` or `_is_industrial_precedent`. |
| Z-22 | should-fix | Rules file validation and the all-draft state. | **Done.** `zoning.validate_rules` checks status, `unlisted_districts`, `as_of`, the `verified_by`/`verified_on` keys (required non-empty when verified), the vocabulary, non-empty cites, location effects and the FID mapping. A violation makes the rules unavailable (the stub), never a crash. Tests: the schema on the repo file, all draft (an **operator step** to update when verifying, in the README), and the six reported violations. |
| Z-23 | should-fix | Import boundaries. | **Done.** AST test: zoning.py and zoning_import.py import neither research, runner, screener, shapely nor costar_import. zoning.py imports neither prepare nor zoning_import. research.py imports `zoning`, never `zoning_import`. |
| Z-24 | should-fix | Database targeting for live checks. | **Done.** `tests/live_zoning_check.py` reuses `live_txn_check.check_dsn_is_local` plus a host check. It prints a masked DSN and creates and drops its own `livezoning_<hex>` database. Test: `LiveCheckGuardTests`. The live import ran only from a throwaway clone against local databases. |
| Z-25 | should-fix | SR-2 surface beyond execute. | **Done.** Zoning sources are fetched by envelope and objectIds; only layer 11 uses an IN list. `in_list_where` admits ids matching `^[0-9A-Z .-]+$`, escapes quotes anyway, rejects bad field names, and counts skipped ids. zoning.py and zoning_import.py joined `ALL_PIPELINE_AST`, so `test_no_string_interpolated_sql` covers them; `StaticTests.test_execute_takes_constants_only` adds a local check. Test: `WhereClauseTests` (quote-containing id). |
| Z-26 | nit | Performance and FakeConnection brittleness. | **Done.** Zoning is one `ANY(%s)` batch per cycle, plus one precedent batch, and only while something is verified. With all rules draft nothing is added. **Existing tests needed no edits** except the declared enumeration updates in `tests/test_discovery.py` (scan sources, forbidden paths) and `tests/test_mirror.py` (module list, allowed refusal reads). `TestPhase5S9.test_returns_moderate_5` passes unchanged. |
| Z-27 | nit | Memos show unverified districts. | **Out of scope, noted.** `reporting.py` (immutable during a run) already renders `parcels.zoning` and `zoning_description` into memos. After an import, memos show e.g. "zoned M-1" and the provenance string, and "Required action: not yet wired" stays. A memo reader may take an unverified district as vetted. Changing the memo needs a separate between-runs Tier 2 change; until then the H3 data_gap text (`rules draft`) appears with the parcel's flags. |

## Other decisions

- **`last_updated`** is not touched by the import (Z-04).
- **Source precedence.** South Fulton's city zoning layer now also covers
  `fid` parcels and is listed before Fulton layer 34. This differs from the
  screener, where layer 34 alone served FID. Live, the city layer
  matched 1 of the 24 FID parcels and layer 34 matched the other 23.
- **Live finding fixed during the build.** The first live run matched 589
  of 1,075 parcels. Layer 11 ids with internal runs of spaces were sent
  normalised. They are now sent exactly as stored. The 100-id IN lists
  also exceeded the server's URL limit (HTTP 404), so chunks are now 60
  ids and 150 objectIds.
- **Exit codes.**
  - 0: imported, possibly with failed sources listed.
  - 1: refused (config, or the parcel layer is down).
  - 2: no database, or a database error.
  - 3: a run has its baseline.

## What could not be done

- **The post-change adversarial review.** This document is the
  implementer's disposition of the pre-change register. The STANDING_RISKS
  Tier 2 rule needs a fresh-context review of the diff. It is owed and
  was not run in this context.
- **Autopilot wiring** (optional in Z-05) was not added.
- **The optional resolution of old `H3 zoning unjoined` flags** (Z-15)
  was not added.
