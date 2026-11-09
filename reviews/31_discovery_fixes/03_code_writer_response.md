# 03 — Discovery fixes: code writer response (Tier 1, sub-item A)

> Written by the code writer, after reading the requirement and the
> pre-change register `02_risk_review.md` (R-1 to R-11, gates 1 to 9). It
> covers sub-item A only: the two `research.py` discovery bugs. Every item
> of the register is disposed below. The Tier 1 reviewer, in a fresh
> context, records the decision in a separate note. Model names are
> omitted. Live evidence is counts only.

## Operator answers recorded (owner, in chat, 2026-11-09, binding)

1. **D3**: COUNT unlisted districts AND districts with unreadable setback
   values (answers D3-01 / D3-02 of `reviews/29_operator_decisions/`).
2. **D2 flood scope**: "just review". D2 stays as implemented (V/VE are
   review-only, floodway still blocks). There is no flood-rule change. The
   null-beats-low question is NOT answered, so no new root sub-score is
   added.
3. **"fix"**: fix the `research.py` ArcGIS error-envelope bug and the
   audit-path bug (this change), and do the D1-11 `zoning_import` fetcher
   round (a separate sub-item).
4. **"can you add this yourself"**: the agent, acting for the human between
   runs, may apply the reviews/26 hazards prepare-mutation to `prepare.py`
   (a separate sub-item). Step 2: build the screener quick wins (a separate
   sub-item).

This change acts on answer 3, first half only. Answers 1, 2 and 4 are
recorded here and are not acted on in this diff.

## Boundary

- **Tier 1.** This is a between-runs fix that a human authorised. It is not
  an experiment: there is no `exp:` prefix and no `autoresearch/<tag>`
  branch. The next run's baseline picks it up.
- **Files changed**: `research.py` (+117 / −23), `tests/test_discovery.py`
  (+383 / −0), and this folder. No protected file is touched. `runner.py`,
  `prepare.py`, `sources.json`, `parameters.json` and `program.md` are
  unchanged. `requirements.txt` is unchanged (SR-10).
- **No metric change** (SR-15). `prepare.py` reads none of `research_log`,
  `flagged_items` or `parcels.raw_response_path` (confirmed in `02` § Tier).
  On a healthy endpoint no count moves.
- **No new `action_type`** (SR-14). Both failure paths write `abort`.
- **NO-GO triggers** (gate 9): none were hit. The fix needed no `runner.py`
  change, no new action_type, no cycle-level crash and no retry change.

## Verification (both bugs real)

The register's evidence was re-checked against the code at `4fe8080`.

- **Bug A, real.** `_DiscoverySession.get` returns `resp.json()` for any
  status from 200 to 399. `_query_arcgis_corridor` read
  `body.get("features") or []`, so `{"error": {...}}` became a zero-feature
  page. `_discover_fulton_corridor` then wrote `discovery_empty`. Proof: on
  the base code, a shimmed run of the new behavioural tests failed 13 of
  16. The 3 that passed are the regression guards (real empty, real drift,
  public yield shape).
- **Bug B, real.** `offset_for_audit = 0` was never reassigned, so every
  feature's `raw_response_path` named `<corridor>_0.json`. On the base code,
  `test_each_feature_points_at_its_own_page` and
  `test_upsert_carries_the_page_path` fail. The page-2 parcel pointed at the
  page-1 file, which does not contain it.

## Change

1. `ArcGISErrorEnvelope(requests.RequestException)` is a new module-level
   exception.
2. `_raise_on_arcgis_error(body, url, list_key)` is a new module helper. It
   raises in four cases:
   - the top level is not a dict;
   - an `error` key is present, with any value;
   - for a query, `features` is missing or is not a list;
   - for a schema, `fields` is missing or is not a list.

   A genuine `{"features": []}` passes. The text is `<host>: ArcGIS error
   envelope code=<int|?>`, or a short malformed-body phrase with the host.
   It never includes `message`, `details` or the URL.
3. `_query_arcgis_corridor_pages(...)` is the old loop. It yields
   `(cache_path, features)` per page. It caches the raw body first, then
   checks it. Termination is byte-for-byte the same.
4. `_query_arcgis_corridor(...)` is now a thin wrapper that yields plain
   features. Its signature is the same, so it is a drop-in for its two test
   callers.
5. `_discover_fulton_corridor` iterates over pages. Each feature gets its
   own page's `cache_path`. `offset_for_audit` is deleted. The corridor
   `except` clause is untouched.
6. `_check_field_mapping_drift` calls the helper before it reads `fields`.
   `_discover_fulton` catches `ArcGISErrorEnvelope` around it. It then
   writes one `abort` row (`schema check: <host>: ArcGIS error envelope
   code=<n>`) and returns `{"aborted": True, "reason": "arcgis_error"}`.
7. Docstrings are updated on `get`, `_query_arcgis_corridor` and
   `_check_field_mapping_drift`. The R-39 no-print scan now also covers the
   two new functions.

## Disposition of the register

| ID | Sev | Disposition | Test(s) |
|---|---|---|---|
| R-1 | blocking | **Done as specified.** A `RequestException` subclass is caught by the existing corridor `except`. The result is one `abort` row plus one `partial corridor: <name>; processed_parcels=<n>` data_gap, and the cycle goes on to the next corridor. No `discovery_empty` row is written. | `TestArcgisEnvelopeCorridor.test_envelope_on_page_1_aborts_corridor`, `test_cycle_continues_to_next_corridor_after_envelope` |
| R-2 | blocking | **Done.** The check is a module helper called after `session.get` in both callers, so tests that mock `get` exercise it. It is deliberately NOT called inside `get`. `get` is a generic JSON getter, and one check site per caller keeps a single path. | all `TestArcgisEnvelope*` use mock sessions; `test_real_session_does_not_retry_an_envelope` uses the real `_DiscoverySession` |
| R-3 | high | **Done.** County shape `{aborted: True, reason: 'arcgis_error'}`. The cycle-level `summary["aborted"]` stays False, as on the drift path. `runner._discovery_count` returns None, and `_format_loop_description` carries no `discovery_fulton=` token. No query is issued. Real drift is still reported as drift. | `TestArcgisEnvelopeSchemaCheck` (4) |
| R-4 | high | **Done.** A non-dict top level, a missing or non-list `features`, and a missing or non-list `fields` are all the same class. The top-level list case used to crash the cycle with an AttributeError. It now aborts the corridor. No existing fixture or mock tripped the stricter rule (the suite was green before the tests were added), so the rule was not loosened. | `TestArcgisEnvelopeHelper.test_malformed_*`, `test_missing_or_non_list_features`, `TestArcgisEnvelopeCorridor.test_malformed_bodies_abort_instead_of_empty_or_crash`, `test_malformed_schema_body_aborts_county` |
| R-5 | high | **Done.** The text is the host plus a numeric code (bools and strings show `?`). A sentinel in `message`/`details` and a `token=` value are absent from the research_log notes, the flag text and the captured `research` log output. | `test_envelope_text_is_host_and_numeric_code_only`, `test_non_numeric_or_missing_code_shows_question_mark`, `test_sentinel_never_reaches_log_flag_or_logs`, schema-path sentinel in `test_schema_envelope_aborts_county` |
| R-6 | high | **Done.** The public generator yields plain features, unchanged, and no path is stashed in a feature dict. | `TestRawResponsePathPerPage.test_public_generator_still_yields_plain_features`; existing `TestArcgisPagination`, `test_hermetic` unchanged and green |
| R-7 | high | **Done.** Termination code is unchanged. An envelope on page 1 gives zero parcels, an abort, and the envelope cached on disk. An envelope on page 2 keeps both page-1 parcels and gives `processed_parcels=2`. | `test_envelope_on_page_2_keeps_page_1_parcels`, `test_query_generator_raises_and_caches_the_envelope` |
| R-8 | should-fix | **Done.** An envelope is never retried. `get` is unchanged, the real session sends exactly one request for a 200 envelope, and `_DISCOVERY_MAX_RETRIES` is still 2. | `test_real_session_does_not_retry_an_envelope`; existing `test_retry_cap_is_two` green |
| R-9 | should-fix | **Accepted as a residual.** The path stays absolute (`str(_safe_cache_path(...))`). Only the offset component changes. | — |
| R-10 | should-fix | **Done.** The body is written before the check, so the cache file holds the `error` key. SR-7 still holds through `_safe_cache_path`. | `test_query_generator_raises_and_caches_the_envelope` |
| R-11 | nit | **Done.** Three docstrings are updated. | — |

**Accepted wording residual.** The corridor abort note still reads
`corridor=<name>: network error: ArcGISErrorEnvelope('<host>: ArcGIS error
envelope code=<n>')`. The handler's "network error" label is kept because
R-1 says not to touch the handler. The class name and code make the cause
plain in the note.

## Evidence (counts only)

| Check | Base `4fe8080` | After |
|---|---|---|
| `python -m unittest discover -s tests` | 1744 OK | **1769 OK** (+25 new) |
| `python -m unittest discover -s screener/tests -t .` | 134 OK | **134 OK** |
| `tests/zoning_golden_check.py` | — | exit 0: identical to the ba4a780 record (12 parcels + the cycle) |
| `tests/site_fit_golden_check.py` | — | exit 0: identical to the 28d3205 record (22 parcels + the cycle) |
| `tests/hazards_golden_check.py` | — | exit 0: identical to the 8ddaf69 record (16 discovery runs, 8 filter sets, 22 scored parcels + the cycle) |
| `tests/adjacency_golden_check.py` | — | exit 0: identical to the 595aa08 record |
| `tests/building_comps_golden_check.py` | — | exit 0: identical to the 3898047 record |
| new behavioural tests on the base code (shimmed) | 13 of 16 fail | 16 of 16 pass |

The golden checks ran with `DATABASE_URL` set to the local throwaway
`lr_rbug` DB only. No record was regenerated and no golden file changed. No
live public endpoint was queried, because every new path is covered offline
(SR-6).

## Residuals (not in this change)

- `raw_response_path` is absolute and embeds the checkout location (R-9).
- An `HTTPError` at the schema check still propagates uncaught and crashes
  the cycle (pre-existing). Folding it into the `arcgis_error` county abort
  would change a transport path's behaviour. It is left for its own change
  with its own test.
- Elements of `fields` that are not dicts would still raise in the drift
  check (pre-existing, not seen in any real or fixture schema).
