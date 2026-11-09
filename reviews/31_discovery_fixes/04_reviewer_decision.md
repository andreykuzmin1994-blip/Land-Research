# 04 — Discovery fixes: reviewer decision (Tier 1, sub-item A)

> Written by the Tier 1 reviewer and implementer, in a fresh context that
> wrote neither the register (`02_risk_review.md`) nor the code
> (`03_code_writer_response.md`, commit `b4fbbb7`). Read: the requirement,
> both documents, the full diff `4fe8080..HEAD` and the surrounding code in
> `research.py` and `runner.py`. Model names are omitted. Live evidence is
> counts only.

## Operator answers recorded (owner, in chat, 2026-11-09, binding)

1. **D3**: COUNT unlisted districts AND districts with unreadable setback
   values (answers D3-01 / D3-02).
2. **D2 flood scope**: "just review". D2 stays as implemented (V/VE
   review-only, floodway still blocks). No flood-rule change. The
   null-beats-low question is NOT answered: no new root sub-score.
3. **"fix"**: fix the `research.py` ArcGIS error-envelope bug and the
   audit-path bug (this change), and do the D1-11 `zoning_import` fetcher
   round (separate sub-item).
4. **"can you add this yourself"**: the agent, acting for the human between
   runs, may apply the reviews/26 hazards prepare-mutation to `prepare.py`
   (separate sub-item). Step 2: build the screener quick wins (separate
   sub-item).

This decision covers answer 3, first half. The diff honours answers 1, 2
and 4 by not touching them: no flood rule, no sub-score, no `prepare.py`
change, no zoning or screener change in this commit range.

## Verdict

**Approved.** No fixes were needed in this round.

| Check | Result |
|---|---|
| Both bugs real (independently re-read at `4fe8080`) | Yes. A: `body.get("features") or []` read `{"error": {...}}` as an empty page, giving `discovery_empty`. B: `offset_for_audit = 0` was never reassigned, so every page's features named `<corridor>_0.json`. |
| Gates 1 to 9 of `02` | All met (below). |
| Protected files | `git diff --name-only 4fe8080..HEAD` = `research.py`, `tests/test_discovery.py`, this folder. No `prepare.py`, `runner.py`, `sources.json`, `parameters.json`, `program.md`, `requirements.txt`. |
| Metric definition (SR-15) | Unchanged. `prepare.py` reads none of `research_log`, `flagged_items`, `raw_response_path`. |
| Commits | No `exp:` prefix, not on an `autoresearch/` branch, not pushed. |

## Gate check

| Gate | Verified how | Result |
|---|---|---|
| 1 GO, bugs real | read base code paths `_query_arcgis_corridor`, `_discover_fulton_corridor` | met |
| 2 envelope or malformed body is a corridor abort, cycle continues, no `discovery_empty` | `ArcGISErrorEnvelope` subclasses `requests.RequestException`; the untouched corridor `except` catches it and writes `abort` + `partial corridor` data_gap. Tests on page 1, page 2, malformed bodies, next-corridor continuation, and real empty still empty. | met |
| 3 schema envelope is a county `aborted/arcgis_error`, no crash, no drift, no TSV token | `_discover_fulton` catches only `ArcGISErrorEnvelope`; test asserts `runner._discovery_count(...) is None` and no `discovery_fulton` in `_format_loop_description`; real drift still `field_mapping_drift`. | met |
| 4 text = host + numeric code only | helper never reads `message`/`details`, never formats the URL; bools and strings show `?`; sentinel and `token=` absent from notes, flags and captured logs (corridor and schema paths). | met |
| 5 `raw_response_path` names its page | page generator yields `(cache_path, features)`; two-page test asserts `_0.json` then `_<page_size>.json`, and the UPSERT carries it; public `_query_arcgis_corridor` still yields plain features. | met |
| 6 no retry for envelopes | `get` unchanged; a real `_DiscoverySession` sends one request for a 200 envelope; `_DISCOVERY_MAX_RETRIES` still 2. | met |
| 7 suites and golden checks | below | met |
| 8 file scope | above | met |
| 9 NO-GO triggers | no `runner.py` change, no new `action_type` (both paths write `abort`), no cycle crash, no retry change | none hit |

## Points examined and accepted

- **Termination unchanged.** The `exceededTransferLimit` / short-page logic
  sits after the `yield` exactly as before; only the yield unit changed from
  one feature to one page. A page-2 envelope raises after page 1's features
  were processed, so their parcels stay (same as an HTTPError mid-corridor).
- **Cache before check.** The envelope body is written to its page file
  before `_raise_on_arcgis_error`, so the failure leaves evidence (R-10).
  Paths still come only from `_safe_cache_path` (SR-7).
- **Stricter body rule (R-4).** `{"features": null}`, a missing `features`
  key, and a non-dict top level now abort instead of reading as empty or
  crashing with AttributeError. A genuine `{"features": []}` still passes.
  No fixture or mock tripped the rule, so it was not loosened.
- **Schema abort shape.** It matches the drift abort (no market data_gap
  flag, cycle-level `aborted` False). It is consistent with the existing
  county-abort path, and the runner sees the same `None` count.
- **Other `get("features")` sites.** `research.py:2128` reads the bundled OZ
  GeoJSON from disk, not ArcGIS, so it is out of scope.
- **Abort note wording.** The corridor note still says "network error" with
  `ArcGISErrorEnvelope('<host>: ArcGIS error envelope code=<n>')`. Accepted
  as the writer recorded it: R-1 said not to touch the handler, and the
  class name makes the cause plain.

## Evidence (counts only, re-run in this context)

| Check | Result |
|---|---|
| `python -m unittest discover -s tests` | 1769 OK (base 1744; +25 new) |
| `python -m unittest discover -s screener/tests -t .` | 134 OK |
| `tests/adjacency_golden_check.py` | exit 0 |
| `tests/building_comps_golden_check.py` | exit 0 |
| `tests/hazards_golden_check.py` | exit 0 |
| `tests/site_fit_golden_check.py` | exit 0 |
| `tests/zoning_golden_check.py` | exit 0 |
| `git status` after the suites | clean (no cache files leaked into the tree) |

The golden checks ran against the local throwaway `lr_rbug` DB only. No
record was regenerated. No live endpoint was queried. On a healthy endpoint
no discovery count moves; the only stored difference is `raw_response_path`
for features on page 2 or later of a corridor.

## Residuals (carried, not in this change)

- `raw_response_path` is absolute and embeds the checkout location (R-9).
- An `HTTPError` at the schema check still propagates uncaught and crashes
  the cycle (pre-existing). A follow-up should fold it into a county abort
  with its own test.
- Non-dict elements inside `fields` would still raise in the drift check
  (pre-existing; not seen in any real or fixture schema).
- The corridor abort note's "network error" label now also covers
  envelopes. A wording change would touch the handler and is left for a
  later change.

## Post-change adversarial review round 1

One finding (minor). Verified against the code. Accepted as a residual, not
a bug. No code change; one pinning test added.

**F-1: an all-envelope county reads `discovery_fulton=0` in the TSV.** If
every corridor gets an error envelope, each corridor writes its abort row
and its partial-corridor data_gap flag. `_discover_fulton` still returns
`{"aborted": False, ..., "totals": {"discovery": 0, ...}}`, so
`runner._discovery_count` returns 0. The TSV then shows the same token as
a genuinely empty county.

- **Verified.** `_discover_fulton_corridor` catches `ArcGISErrorEnvelope`
  (a `RequestException` subclass) in the existing network handler and
  returns zero counts. `per_corridor` carries no abort field.
  `_discover_fulton` sums the totals with `aborted` False.
  `_discovery_count` returns `None` only for an aborted or skipped county.
- **Why not fixed here.** This is the transport-error path's existing
  shape. R-1 mirrors it on purpose: an all-`ConnectionError` county gives
  the same `discovery_fulton=0` at base. The 0 is not silent. The cycle
  writes one `abort` research_log row and one `partial corridor` data_gap
  flag per corridor, and no `discovery_empty` row. That is how an operator
  tells this case from a real empty county. Marking the county shape
  would change the runner's TSV token for the transport path too. It is a
  metric-adjacent change that belongs in its own reviewed change, not in
  this fix.
- **Test.** `TestArcgisEnvelopeCorridor.test_every_corridor_enveloped_pins_documented_residual`
  pins today's behaviour. It checks: county not aborted, totals 0, one
  request per corridor (no retry), N abort rows, N partial-corridor flags,
  no discovery_empty row, and `discovery_fulton=0` in the description. A
  later change that marks this shape must update the test on purpose.
- **Residual added** (below): an all-corridor failure, envelope or
  transport, is not marked in the per-county summary or the TSV.
  Follow-up: add a per-corridor `aborted` marker. Have `_discovery_count`
  return `None`, or a distinct token, when every corridor aborted.

Re-run after this round: `python -m unittest discover -s tests` 1770 OK;
`python -m unittest discover -s screener/tests -t .` 134 OK; git status
clean apart from the committed change.

### Residual added by round 1

- An all-corridor failure (envelope or transport) leaves the county
  `aborted` False. The TSV reads `discovery_fulton=0`, which matches a
  genuinely empty county. The abort rows and partial-corridor flags are the
  only marker. Pinned by the test above.
