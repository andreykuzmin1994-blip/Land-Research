# 02 — Operator decisions: pre-change risk register and dispositions

> The register (D1-01 .. D1-12, D3-01 .. D3-07, D2-01, D4-01, PF-01) was
> written by an independent reviewer BEFORE the change. Each item carries
> what was done and the test that pins it. Unless named, tests are in
> `tests/test_operator_decisions.py`. Standing risks (STANDING_RISKS.md):
> SR-1 no protected file touched (`test_zz_human_files_unchanged`); SR-2 no
> new SQL; SR-6 every new path runs on fakes; SR-8 outputs print hostnames
> and counts only; SR-10 `requirements.txt` unchanged; SR-11 the shared
> spacing key is stricter than before; SR-14 no new `action_type`.

## D1 — ArcGIS robots exception

| ID | Sev | Disposition | Pinned by |
|---|---|---|---|
| D1-01 | blocking | One `rules_scrape.robots_verdict(rb, path, token)`; `Robots.verdict` delegates; adjacency_import/hazards_import call it from `_check` with their own token and define no verdict (duplicate bodies removed; `robots_token` class attribute). | `SharedVerdictTests` (same services3 403 fixture passes in PoliteFetcher, AdjacencyFetcher, HazardsFetcher; a non-ArcGIS 403 refuses in all three; static: no `robots_verdict` def or assignment in either tool, exactly one call `rules_scrape.robots_verdict`) |
| D1-02 | blocking | `Unreachable.plain_refusal` (explicit field) is True only for a 401/403 whose `classify_block` text is the plain status and that shows no challenge/WAF marker (`classify_block(200, ...)` is None). `robots_for` stores `Robots.unavailable_status` only on hop 0 and only for such an answer. | `FailClosedTableTests`: 401/403 accepted; 407, 429 without Retry-After, 500, 503, timeout, connection error, Cloudflare 403, Akamai 403, 403 + cf-mitigated, 403 challenge page, 403 WAF header, cf-mitigated 200, HTML 200, 301 off-site, 302 to services2 that then 403s, Crawl-delay 120 all refuse with zero data requests; a forged deny-all Robots with the reason text but no status refuses |
| D1-03 | high | The exception branch is only inside `if rb.deny_all`; a parsed robots.txt is never deny-all. | `ReadableRobotsWinTests`: (a) hazards.fema.gov with its real robots text (`Disallow: /*?*`, `Disallow: /arcgis`) refuses MapServer/28/query 'robots.txt disallows', even with the host patched into the constant; (b) fwspublicservices.wim.usgs.gov 301 to www.fws.gov refuses 'robots redirect off-site'; (c) services3 `Disallow: /`, (d) an AI-agent token disallow and Content-Signal ai-input=no (global and in-group) refuse in all three fetchers |
| D1-04 | high | `arcgis_exception_host`: refuses userinfo, `%`, a non-443 port; normalises with `norm_host`; `re.fullmatch` (ASCII) or the exact-name constant. `_check` re-runs per hop on the hop's origin. | `HostMatchTests`: the hub.arcgis.com hosts of sources.json, www/tiles/acme.maps.arcgis.com, services.arcgis.com.evil.test, xservices, services3a, :8443, user@, a trailing newline, a Cyrillic lookalike, `%2e` refused; services, services8, upper case, trailing dot, :443 accepted; a redirect to tiles.arcgis.com behind a 403 refuses |
| D1-05 | high | Per-URL decision, deny_all kept; strict raw-path regex (no `%`, `..`, `//`, `\`, `;`), single `f` in the allowed set, no `token`. | `PathTests` (the gis_signal and adjacency_import metadata/query forms pass; service root, exportImage, export, tile, applyEdits, queryRelatedRecords, admin, sharing/rest, f=html, f=geojson on metadata, no f, two f, token/TOKEN, %2e%2e, %2F, `..`, `//`, lower-case featureserver, three segments, non-ASCII digit refused; a refused path on an accepted host makes no request) |
| D1-06 | should-fix | `OPERATOR_ACCEPTED_ARCGIS_HOSTS = frozenset()` with the evidence rule in a comment; the 404 evidence for gis.atlantaga.gov and gismaps.fultoncountyga.gov is in `00_design.md`. | `HostMatchTests.test_org_host_constant_is_empty_exact_and_frozen` |
| D1-07 | should-fix | `robots_meta[host]` keeps `deny_all: True`, adds `exception` with the status of the actual answer; one `robots: <host>: <text>` line per host in the three tools; --watch rows, report section and `watch_state.json["robots_exceptions"]`. | `SharedVerdictTests` (meta), `OutputTests` (stdout of rules_scrape --watch, adjacency_import and hazards_import: the exact string once per host; no query string on the line; no feature values; watch JSON and report row) |
| D1-08 | should-fix | Every accepted host spaces on `ARCGIS_SPACING_KEY = "arcgis.com"`; Retry-After, 429, budget and User-Agent unchanged. | `SpacingTests` (services3/services8 interleaved: every gap >= 1 s; two ordinary hosts still unspaced); RS-05 tests in `test_rules_scrape.py` unchanged |
| D1-09 | should-fix | Recorded here and in `00_design.md`: the Esri terms pages are not machine-readable from this environment (script-rendered), so no text about automated access was verified; item owners' licences also apply; the basis is operator acceptance D1 and the documented anonymous REST query pattern. adjacency_import prints "layers with a non-empty copyrightText: N of M". | doc check (this table, `00_design.md` "Terms (D1-09): not verified"); `OutputTests.test_adjacency_import_names_the_exception_and_counts_copyright` |
| D1-10 | should-fix | Side effect named in `00_design.md` and `03_decision_note.md`; --watch re-run live (counts in 03). | `WatchTests.test_watch_refuses_gis_while_the_loop_runs`, `FlagTests.test_flags_skip_case_layers_while_loop_runs` unchanged and green; `OutputTests.test_case_layers_still_skipped_while_the_loop_runs` (services3 behind a 403 robots, flock held: no request, layer "not queried") |
| D1-11 | should-fix | Not adopted this round (`zoning_import.py` unchanged). Residual recorded in `03_decision_note.md` as an open Tier 2 item. | `git diff` of zoning_import.py empty; zoning golden check exit 0 |
| D1-12 | high | Live run from a `git clone` under the scratchpad (never committed from); the reviews/27 DDL and the reviews/21 snippet applied to the clone only; own database `adj29_<hex>` created from the local corridor copy and dropped; DATABASE_URL set explicitly to localhost:55432; verified/class tables only in memory via `tests/zoning_helpers.patch_rules`. | `test_zz_human_files_unchanged`; `git status` clean of protected files; database name and drop in `03_decision_note.md` |

## D3 — no district setbacks: do not block

| ID | Sev | Disposition | Pinned by |
|---|---|---|---|
| D3-01 | high | Only a LISTED district with all three keys absent/null counts. An unlisted district (dimensions None) stays BTS N/A, also when `unlisted_districts` is `not_permitted` (REZONING, which passes the S9 gate). Raised with the operator in `03_decision_note.md`. | `test_site_fit.test_d3_unlisted_district_stays_bts_na`; `D3ScoringTests.test_unlisted_district_stays_na` (Fairburn R-1, 520K fit, `unlisted_districts` not_permitted in memory: BTS N/A, no flag) |
| D3-02 | high | `absent = all(_value(dims, k) is None ...)`: any present key (readable or not) disqualifies. | `test_site_fit.test_d3_unreadable_setback_value_stays_bts_na` ('see table' and a negative value: N/A plus the existing note; all three `{"value": null}`: True plus the text) |
| D3-03 | blocking | No record regenerated. The adjacency golden case's verified default-setback parcels (fulton-Z03 South Fulton R-3, fulton-Z05 Fairburn R-2 OVERLAY) are UNLISTED districts, so under D3-01 nothing in that record changes: `tests/adjacency_golden_check.py` exits 0 unmodified, and no expected-diff list was needed. The hazards golden check already DIFFERS at HEAD (4bba5e3) in the 10 discovery `process` cases because `discovery_date` comes from today's date (the record was made on 2026-11-06): a pre-existing clock artefact, not a code difference. Run with `research.datetime.now` frozen to 2026-11-06 it is identical, before and after this change. zoning, site_fit and building_comps checks exit 0 unmodified. | the five `tests/*_golden_check.py` (commands and output in `03_decision_note.md`) |
| D3-04 | high | Everything keyed on `rules.verified and rules.default_listed` / `p.setback_default_listed` (set only when verified) and on a projection existing (research passes none for draft). `VERIFIED_DEFAULT_LABEL` unchanged; the text is `BTS_REVIEW_TEMPLATE`. | site_fit/zoning golden checks; `test_site_fit_scoring.DraftDifferentialTests`; `test_site_fit.test_verified_listed_district_without_setbacks_counts_with_the_review_text` (draft half: label, no flag, no constraint line); `D3ScoringTests.test_draft_is_unchanged`, `test_memo_lines` (draft memo) |
| D3-05 | should-fix | `site_fit.bts_review_text` is the only static-guard change (`ALLOWED`); research reads no Projection field; written via `_flag_data_gap_once` with its own resolution text (not `data_gap_text`). | `test_site_fit_scoring.StaticResearchTests`; `D3ScoringTests.test_flag_once_data_gap_never_a_block_on_both_paths`, `test_not_rewritten_while_open`; forged projection returns None (`test_site_fit`) |
| D3-06 | high | Only the setback condition changed; finding-6 tests inverted deliberately (renamed; M-1 half kept); `test_a_projection_built_outside_the_module_never_passes` kept. | `test_site_fit.test_bts_threshold_boundary`, `test_a_default_setback_projection_built_outside_the_module_never_passes` (copy, replace, construction, draft), 149,999 SF default False and still flagged; new run + baseline recorded in reviews/22 and here |
| D3-07 | should-fix | Reporting texts, site_fit module docstring, research docstrings, reviews/22 notes updated. | `D3ScoringTests.test_memo_lines` (exact memo text; unlisted keeps "rated N/A"); notes carry the text once |

## D2 / D4 / hygiene

| ID | Sev | Disposition | Pinned by |
|---|---|---|---|
| D2-01 | nit | Dated "Operator decisions" section appended to reviews/26_flood_wetlands/03_decision_note.md naming V/VE, 1% future conditions, levee and open water, marking finding 7 answered. No hazards.py change. | `test_hazards.test_ae_with_lomr_is_a_review_pass`, `test_other_sfha_zones_are_reviews_not_blocks` (AH, AO, A99, AR, V, VE), `test_future_conditions_levee_open_water_are_reviews`, `test_hazards_scoring.test_review_flags_never_change_actionability` (existing); new `PinTests.test_flood_thresholds_are_the_human_values` (read-only) |
| D4-01 | nit | Only a dated note appended to reviews/24_rules_local_files/03_decision_note.md; no cache or quote deletion. | `PinTests.test_terms_block_and_denylist_unchanged` (TERMS_BLOCKED_DOMAINS, MIRROR_HOSTS, the Municode regex and host set; zoneomics/municode refused with no request); LF-03/RS-02 tests green |
| PF-01 | should-fix | Explicit `git add` paths; `git diff --cached --name-only` checked before each commit; two attribution lines only; no push. | `test_zz_human_files_unchanged`; commit list in `03_decision_note.md` |

## Pre-existing artefact (fixed in review round 2)

`tests/hazards_golden_check.py` compared discovery rows whose
`discovery_date` is today's UTC date with a record taken on 2026-11-06, so
on any later day it printed "DIFFERS ... 10 case(s)" at HEAD as well. Round
2 (F4) freezes `research.datetime` to the record date inside the check;
it now exits 0.

## Review round 2 (post-change adversarial review, 2026-11-09)

| # | Sev | Finding | Disposition | Pinned by |
|---|---|---|---|---|
| F1 | should-fix | `;params` on the last path segment were split off by `urlparse` before the robots/D1 path check, while the request carried them | Fixed: `rules_scrape.robots_path(url)` (urlsplit, keeps `;params`) is the checked path in all three `_check` methods | `PathTests.test_params_on_any_segment_refused_end_to_end` (`.../query;x?f=json`, `.../0;x?f=json`, `.../query;../../x`, all three fetchers, zero data requests), `test_robots_path_keeps_params`, `test_readable_robots_matches_the_params_path` |
| F2 | should-fix | D3 narrower than the operator's wording (unlisted / unreadable stay N/A) | No code change; open item D3-01 kept in reviews/22 and 03 for the operator's explicit answer | `test_site_fit.test_d3_unlisted_district_stays_bts_na`, `test_d3_unreadable_setback_value_stays_bts_na` (unchanged) |
| F3 | should-fix | The only parcels D3 moves today are Fairburn PD, where warehouse is `not_permitted` | Recorded in 03 decision 5 / D3-01 and reviews/22 | doc only |
| F4 | nit | hazards golden check depends on the clock | Fixed (record-date clock inside the check) | `python tests/hazards_golden_check.py` exit 0 |
| F5 | nit | Per-host exception line written even when nothing was read under it | Fixed: `note_robots_exception` records it in `_check` only after a verdict of None on a deny-all Robots | `PathTests.test_exception_recorded_only_after_a_read` (f=html refused: no line; then a read: one line), `test_params_...` (no line) |
| F6 | nit | zoning_import outside the shared policy, follows redirects | Kept as the D1-11 open Tier 2 item; pin added | `PinTests.test_zoning_import_hosts_are_arcgis_rest_only` |
| F7 | nit | Watch Detail of a READ row looked like a refusal | Fixed: `read under D1 exception: <exact text>` | `OutputTests.test_watch_reads_and_names_the_exception_once_per_host` |
| F8 | nit | east_point_zoning 232 requests for 0 polygons | Not changed (reviews/27 design); later round: skip tiles outside the layer extent | none |
