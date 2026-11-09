# 02 — Discovery fixes: pre-change risk register (Tier 1)

> Written by an independent reviewer BEFORE any code change, from the
> requirement and the code at base `4fe8080` (PR 38 merge). No code was
> written in this context. This folder collects the 2026-11-09 operator
> round; this file covers sub-item A (two `research.py` discovery bugs).
> Later sub-items (prepare-mutation, zoning_import fetcher, screener) add
> their own section or file here.

## Operator answers recorded (owner, in chat, 2026-11-09, binding)

1. **D3**: COUNT unlisted districts AND districts with unreadable setback
   values (answers D3-01 / D3-02 of `reviews/29_operator_decisions/`).
2. **D2 flood scope**: "just review". D2 stays as implemented (V/VE are
   review-only, floodway still blocks). No flood-rule change. The
   null-beats-low question is NOT answered, so no new root sub-score.
3. **"fix"**: fix the `research.py` ArcGIS error-envelope bug and the
   audit-path bug (this section), and do the D1-11 `zoning_import`
   fetcher round.
4. **"can you add this yourself"**: the agent, acting for the human between
   runs, may apply the reviews/26 hazards prepare-mutation to `prepare.py`.
   Step 2: build the screener quick wins.

Only answer 3 (first half) is in scope for this register.

## Tier and boundary

- **Tier 1** (STANDING_RISKS § Change tiers: discovery logic in
  `research.py`). This is a between-runs fix authorised by a human, not an
  experiment. It is NOT an `exp:` commit and does not go on an
  `autoresearch/<tag>` branch. The next run's baseline picks it up.
- **No metric-definition change** (SR-15). `prepare.py` reads neither
  `research_log`, `flagged_items` nor `parcels.raw_response_path`: grep
  finds only the DDL (`prepare.py:330`, `:463`, `:496`). `reporting.py` and
  `runner.py` never read `raw_response_path`. Neither fix touches
  `prepare.py`, `runner.py` or any protected file.

## Verification: both bugs are real

### Bug A: error envelope read as an empty corridor (REAL)

- `_DiscoverySession.get` (`research.py:373-440`) returns `resp.json()` for
  any status from 200 to 399, without looking at the body.
- `_query_arcgis_corridor` (`:1376-1420`) caches the body, then runs
  `features = body.get("features") or []`. For `{"error": {...}}`, that
  gives `[]`, `exceededTransferLimit` is None, and `0 < page_size`, so the
  loop breaks.
- `_discover_fulton_corridor` (`:1648-1712`) then sees `empty = True` and
  writes `research_log` `discovery_empty` "returned 0 features". No `abort`
  row is written, no data_gap flag is raised, and the summary looks healthy.
  The `get` docstring (R-1302) promises the opposite: the corridor handler
  fires "rather than mistaking a dead endpoint for an empty one".
- Root has no `"error" in body` check anywhere. The harness has one
  (`connector_harness._parse_arcgis_response`, `:451-471`, R-04: "logic"
  failure). So do `zoning_import.py:359`, `hazards_import.py:242`,
  `adjacency_import.py:195` and `screener/arcgis.py`. The harness only
  probes on its own schedule, though. A corridor query that errors
  (a `where`/`orderByFields` rejected after a server upgrade, or a
  per-service quota) passes the gate and still becomes "empty".
- **Same defect at a second call site.** `_check_field_mapping_drift`
  (`:1317-1335`) reads `body.get("fields") or []`. An envelope then reports
  EVERY mapped field as missing, and the county aborts as
  `field_mapping_drift`. That is loud, but it is the wrong reason, and it
  would send a human to refresh `field_mapping` for nothing.
- **Already loud (no change needed).** A non-JSON 200 raises
  `requests.exceptions.JSONDecodeError`. In the installed requests 2.34.2
  (pin `>=2.31`) that is a `RequestException`, so the corridor handler
  already aborts. A top-level JSON list makes `body.get` raise
  AttributeError, which is NOT caught: the cycle crashes and the runner
  rolls back to `crash`. That is loud but inconsistent (see R-4).

### Bug B: audit path never advances (REAL)

- `offset_for_audit = 0` (`:1661`) is never reassigned. Every feature's
  `raw_response_path` (`:1669`, passed at `:1673`) names
  `<corridor>_0.json`, even for features from page 2+ (cached as
  `<corridor>_1000.json`, `_2000.json`, ...). The parcels UPSERT writes this
  column on every conflict (`:1145`). Any parcel past the first 1000 in a
  corridor therefore points its audit trail (R-30/R-40) at a file that does
  not contain it.
- Nothing else is wrong on this path. `_safe_cache_path` builds and checks
  the correct name, and the generator writes each page to its own correct
  file. Only the value attributed to each feature is wrong. The stored path
  is absolute (resolved under `_SOURCES_DIR`); see R-9.

## Risk register (ranked)

| ID | Sev | Risk | Mitigation / required design |
|---|---|---|---|
| R-1 | blocking | **Wrong failure semantics.** The fix could crash the whole cycle, or keep it silent. A crash rolls back every other corridor's committed-at-end work, because `run_discovery_cycle` commits only at the end and catches only KeyboardInterrupt. | An envelope on a corridor query MUST follow the existing transport-failure path exactly (R-18 / R-1302). Write one `abort` row plus one market-level data_gap "partial corridor" flag, keep the processed-so-far counts, and continue to the next corridor. Do this by raising a dedicated `requests.RequestException` subclass (e.g. `ArcGISErrorEnvelope`), so the existing `except` at `:1683` catches it unchanged. Do not touch the corridor handler. No `discovery_empty` row may be written for an envelope. |
| R-2 | blocking | **Test-mocked sessions bypass a check placed only in `_DiscoverySession.get`.** Every pagination/cycle test replaces `get` with a mock that returns dicts, so a check inside `get` would leave `_query_arcgis_corridor` itself unguarded and untested. | Put the check in one module-level helper (e.g. `_raise_on_arcgis_error(body, url)`), called right after `session.get` in `_query_arcgis_corridor` and in `_check_field_mapping_drift`. Optionally also call it in `get`, but the caller-side call is the one the tests must pin. |
| R-3 | high | **Drift check mislabels the envelope.** | Raise the same helper's exception before reading `fields`. Do not let it crash the cycle (today the HTTPError path there propagates uncaught, which is a pre-existing inconsistency). Catch it in `_discover_fulton` and return `{"aborted": True, "reason": "arcgis_error", ...}`, with one `abort` log row naming the host and the numeric code. The county shape stays the same as the drift abort, so `runner._discovery_count` returns None and the TSV drops `discovery_fulton=` exactly as it does today for a drift abort. Do NOT set the cycle-level `summary["aborted"]` (no change of shape vs the drift path). |
| R-4 | high | **Malformed-but-200 bodies.** A non-dict top level (list, null, string) crashes today. A dict with no `features` key, or with `features` not a list, is read as empty today. | Treat all of these as the same failure class as an envelope (the harness's "logic" class): a top level that is not a dict; `"error"` present (any value, any code); `features` missing or not a list (query); `fields` not a list (schema). A genuine empty answer, `{"features": [], ...}` (fixture `arcgis_query_empty.json`), must stay `discovery_empty` (R-19). Gate: the suite stays green. If a test fixture returns a query body with no `features` key, the implementer reports it and does not loosen the rule. |
| R-5 | high | **Secret/PII leakage in the failure text** (SR-8, R-1308). ArcGIS `error.message` / `error.details` can echo the `where` clause or the request. The abort note embeds `{exc!r}` (`:1688`). | Exception text = hostname + the numeric `code` only (an int, else `?`), the way `zoning_import.py:359-362` does it. Never the URL with its query, never `message`/`details`. Test: a forged envelope with a sentinel in `message`/`details` and a `token=` in the URL. The sentinel is absent from the `research_log` note, the flag text and the captured log output. |
| R-6 | high | **Audit-path fix breaks the generator's public shape.** Tests iterate `_query_arcgis_corridor` and compare the features (`test_discovery.TestArcgisPagination`, `test_hermetic` `:140-154`). | Keep `_query_arcgis_corridor` yielding plain features (as a thin wrapper). Add an internal page-level generator that yields `(cache_path, features)` per page (or `(offset, feature)`), and have `_discover_fulton_corridor` use it, so `raw_response_path` is the path of the page that held the feature. Do not stash the path in the feature dict (`_map_feature_to_parcel` would carry it). Delete `offset_for_audit`. |
| R-7 | high | **Pagination/termination regressions.** | Termination logic stays byte-for-byte (`exceeded is False`, short page with no field, no features). An envelope on page N>1 aborts after pages 1..N-1 were yielded and processed; their parcels remain (savepoints are committed at cycle end, as with an HTTPError mid-corridor today). Tests: an envelope on page 1 gives zero parcels, an abort, no `discovery_empty`, and a cache file holding the envelope. An envelope on page 2 gives page-1 parcels kept, an abort, and a partial-corridor flag whose `processed_parcels` equals the page-1 count. |
| R-8 | should-fix | **Retrying an envelope** would add requests to a failing county server (SR-11). | No retry for envelopes, whatever the `code` (even 429/5xx-looking codes). One request, then the corridor aborts and the flag asks for a re-run. The per-host spacing and the R-1301 retry constants stay unchanged (`test_retry_cap_is_two` stays green). |
| R-9 | should-fix | **Scope creep on the stored path.** `raw_response_path` is absolute and contains the worktree location. Making it relative would change every stored value. | Out of scope. Keep the absolute `str(_safe_cache_path(...))`. Only the offset component changes. Note it as a residual. |
| R-10 | should-fix | **Audit evidence of the failure.** | Keep caching the envelope body (it is written before the check today). The cache file is the evidence, and SR-7 still holds through `_safe_cache_path`. The test asserts the file exists and holds the `error` key. |
| R-11 | nit | **Docstrings** still say "Empty corridors are not an error (R-19)", with no mention of envelopes. | Update the `_query_arcgis_corridor`, `_check_field_mapping_drift` and `get` docstrings: empty is not an error; an envelope or a malformed body is a corridor abort (R-04 parity with the harness). |

## Standing risks

- **SR-1**: no protected file is touched. Only `research.py`, tests and this
  folder change. Pinned by `test_no_immutable_writes`,
  `TestStrictNoImmutableWrites` and `test_zz_human_files_unchanged` where
  present.
- **SR-2**: no new SQL. The `abort` / flag writes reuse `_log_research` /
  `_flag`.
- **SR-3/SR-4/SR-9**: unchanged (no new parameter reads, same cycle id,
  same UPSERT).
- **SR-5**: the abort is written in its own `conn.transaction()` savepoint,
  as today. Per-parcel savepoints are unchanged.
- **SR-6**: every new path runs on mock sessions and `FakeConnection`. No
  network.
- **SR-7**: the paths still come only from `_safe_cache_path`.
- **SR-8**: see R-5.
- **SR-10**: no dependency. `requirements.txt` stays byte-identical.
- **SR-11**: see R-8.
- **SR-14**: no new `action_type`. Reuse `abort` (listed in `program.md:116`).
- **SR-15**: no metric change (see Tier and boundary).
- **SR-16**: not affected.
- **Static guards** on `research.py` (`static_guards.absent_module_problems`,
  `StaticResearchTests`): new literals must not trip them. Gate: the full
  suite.

## Golden checks and counts

On a healthy endpoint, no count changes. The only stored difference is
`raw_response_path` for features from page 2+ of a corridor (`_0.json` →
`_<offset>.json`). The golden cases (`tests/*_golden_case.py`) and
`tests/live_txn_check.py` call `_process_parcel` with a fixed
`raw_response_path`, so they are expected to be unaffected. Gate: run the
five `tests/*_golden_check.py` against the local throwaway DB
(`postgresql://postgres:testpw@localhost:55432/lr_rbug` only). Each must
exit 0 unmodified. If one differs, list the differing cases in the decision
note with the reason; do not regenerate a record silently. Discovery counts
change only on the failure paths: an envelope corridor now yields `abort`
plus a flag instead of `discovery_empty`, and an envelope at the schema
check yields `aborted/arcgis_error` instead of `field_mapping_drift`.

## Go / no-go gates

1. **GO** to implement both fixes. Both bugs are confirmed real (evidence
   above).
2. Envelope / malformed body → corridor `abort` + partial-corridor
   data_gap, the cycle continues, no `discovery_empty` (R-1, R-4, R-7).
   Tests pin the page-1 and page-2 cases and the "real empty is still
   empty" case.
3. An envelope at the schema check gives a county `aborted` with reason
   `arcgis_error`: no crash, no `field_mapping_drift`, no TSV
   `discovery_fulton=` token (R-3). Test via `runner._format_loop_description`
   or `_discovery_count`.
4. Failure text carries only the host and the numeric code. A test with a
   sentinel proves this (R-5).
5. `raw_response_path` names the page that held the feature. A two-page
   test asserts `_0.json` for page 1 and `_<page_size>.json` for page 2.
   The `_query_arcgis_corridor` public yield is unchanged (R-6).
6. No retry for envelopes, and the retry constants are unchanged (R-8).
7. Both suites are green (`python -m unittest discover -s tests`, at base
   ~1744 OK; `python -m unittest discover -s screener/tests -t .`, 134 OK).
   The golden checks exit 0 or have documented diffs.
8. `git diff --name-only` = `research.py`, tests, and
   `reviews/31_discovery_fixes/` only. No `prepare.py`, `runner.py`,
   `sources.json`, `parameters.json` or `program.md`. No `exp:` prefix.
   No push.
9. **NO-GO** if the fix needs a change to `runner.py`'s decision or TSV
   logic, a new `action_type`, a cycle-level crash for a corridor
   envelope, or a retry policy change. Any of these is Tier 2: stop and
   report.

## Residuals (not for this change)

- `raw_response_path` is absolute (R-9).
- An HTTPError at the schema check still propagates uncaught (pre-existing).
  Gate 3 only covers the envelope. Folding HTTPError into the same county
  abort is a reasonable follow-up, but it changes behaviour on a transport
  path, so it was left to the implementer to record as a residual or to do
  with its own test.
