# Phase 3 Independent Re-Validation — Fresh-Eye Spot-Check

> **Reviewer**: orchestrator (main session) operating as Agent 3 spot-checker
> in a *new* session (`claude/revalidate-phase-3-ytHPT`) explicitly tasked with
> re-validating Phase 3 with full context independence from the original
> Phase 3 trio. Date: 2026-05-01. Branch: `claude/revalidate-phase-3-ytHPT`.

---

## 0. Sub-agent infrastructure deviation (continues)

This re-validation was first attempted as a fresh-context Opus 4.7 sub-agent
(`general-purpose`, model=opus). It terminated with `Stream idle timeout —
partial response received` after ~6 minutes / 50 tool calls, exactly mirroring
the timeout pattern documented in `01_risk_review.md` §0, `02_code_writer_response.md`
§0, and `03_reviewer_decision.md` §0. The sub-agent wrote **zero output to disk**.

Per explicit human authorization in this session ("use any agent you need, you
decide on scope"), the orchestrator completes the spot-check inline with the
discipline an independent Agent 3 would apply: cite line numbers for every
finding, dispute the prior trio's reads where the code says otherwise, and
hunt for new risks the orchestrator-as-Agent-1 missed. The fact that this is
*another* orchestrator review on top of the prior orchestrator review is the
single biggest integrity hole, and a future session with working sub-agent
streaming should re-run this once more.

---

## 1. Verdict at the top

**AMEND.** The previous Agent 3's APPROVE-WITH-FOLLOWUPS verdict at
`03_reviewer_decision.md` §11 stands — Phase 3 ships, the harness gate, the
parameterized SQL, the per-parcel transaction discipline, and the 48-test
offline suite all hold up under independent inspection. But the §10 punch
list is incomplete and one of its rationales is wrong. The proposed Phase 3.1
adds **three new items** (one real bug, two test weaknesses) and **rewrites
the rationale** for §10 item 3. None block merge; all should land before
Phase 4. The full revised punch list is in §6 below.

---

## 2. Eight-risk spot-check

### R-01 (S1) — no immutable-file writes
**VERIFIED-WITH-CAVEAT.** `research.py` reads `sources.json` via `open(..., "r")`
at L225 and never opens any of the immutable files for writing. The AST scan in
`tests/test_discovery.py:136-155` correctly catches the literal-path-and-mode
case. **Caveat:** the scan only catches `open(<Constant str>, <Constant str>)`
patterns. It would NOT catch `open(_PARAMS_PATH, "w")`, `Path("parameters.json").write_text(...)`,
`csv.writer(f)`, or `shutil.copy("foo", "parameters.json")`. The current code
is fine; the test would not fail on a regression that introduced any of those
patterns. New punch-list item §6.A.

### R-05 (S1) — parameterized SQL
**VERIFIED.** Every `cur.execute()` call in `research.py` (L626-L628, L644-L647,
L651-L652, L693-L694) takes a module-level constant as first arg
(`_SQL_INSERT_RESEARCH_LOG`, `_SQL_INSERT_FLAG`, `_SQL_COUNT_LOG_FOR_CYCLE`,
`_SQL_UPSERT_PARCEL`). Test at `test_discovery.py:157-172` AST-walks for
non-`Constant`/`Name`/`Attribute` first args. Pass.

### R-06 (S1) — county-prefixed parcel_id
**VERIFIED.** `_map_feature_to_parcel` at `research.py:834` constructs
`parcel_id = f"fulton-{str(raw_id).strip()}"`. Hardcoded prefix is correct
for Phase 3's single-county scope; Phase 11 will need a county-arg variant.
Test at `test_discovery.py:500-518` exercises the prefix.

### R-07 (S1) — MultiPolygon handling
**FAILED.** `_arcgis_polygon_to_wkt` (L308-L345) keeps the largest outer ring
in the WKT and emits `multipolygon=True` correctly. BUT `_map_feature_to_parcel`
at L844 calls `_ring_centroid(rings[0])` — which is the *first* ring, not the
*kept* (largest) outer ring. For multi-polygon parcels where the first ring is
not the largest, the row's `centroid_lng`/`centroid_lat` (used downstream by
`_h1_filter` for the H1 envelope check) reference the wrong ring. The PostGIS
`centroid` column in `parcels` is fine because it's `ST_Centroid(ST_GeomFromText(wkt, ...))`
server-side and `wkt` derives from `kept_outer`. The bug is the H1 client-side
check.

Verified by direct experiment: with two rings (small first, large second of
30× area), `_arcgis_polygon_to_wkt` emits WKT for the large ring while
`_ring_centroid(rings[0])` returns the small ring's centroid (-84.554, 33.554)
versus the kept ring's centroid (-84.530, 33.568). The H1 envelope is loose
enough that real Fulton multi-polygons would still pass, so production impact
is bounded. Still a real correctness bug. New punch-list item §6.B.

### R-29 (S2) — owner names verbatim
**VERIFIED.** `_map_feature_to_parcel` at L851-L853, L863 passes `owner_name`
through unchanged. Test at `test_discovery.py:539-551` asserts
`row["owner_name"] == "SMITH FAMILY TRUST"` from fixture. No redaction layer
between connector and DB.

### R-34 (S1) — harness gate first
**VERIFIED.** The cycle ordering in `run_discovery_cycle` (L1110-L1168) is:
parameter-sentinel verify → params load → sources load → cycle-id gen → DB
connect → cycle-id collision check → harness gate. This matches the
mitigation specified in `01_risk_review.md` R-34 verbatim (steps 1-5 in that
order). Tests at `test_discovery.py:580-603` cover failing and harness-raise
branches. **Coverage gap:** the `harness=degraded` path (L1201-L1210) has no
explicit unit test — the happy-path test uses `harness_healthy.json`. This
gap is small enough to live with but worth a Phase 3.1 test. New punch-list
item §6.C.

### R-42 (S2) — extensible filter pipeline
**VERIFIED-WITH-CAVEAT.** `_HARD_FILTERS` at L534 is a module-level list and
Phase 4 can append H5–H10 trivially. Test at `test_discovery.py:414-423`
appends a synthetic filter and asserts the list grows by one. **Caveat:** the
test only checks `len()` — it does NOT execute the pipeline against a fixture
to confirm the new filter actually runs in order. A regression that broke
iteration order (e.g., switched the loop to use `_HARD_FILTERS[:4]`) would
not fail this test. New punch-list item §6.D.

### R-45 (S1) — offline-only tests
**VERIFIED.** Confirmed locally: `python3 -m unittest tests.test_discovery -v`
runs 48 tests in 0.068s. No network calls. All HTTP is mocked via
`mock.patch.object(research, "_DiscoverySession", ...)` or via
direct `_MockSession` classes inside individual tests.

---

## 3. Orchestrator-flagged items in `03_reviewer_decision.md` §2.1–§2.5

### §2.1 `action_type` vocabulary drift — CONFIRMED (real bug)
`program.md:127` enumerates `action_type` as exactly `discovery|scoring|rescore|rejection|flag`.
`research.py` writes `"abort"` at L1052, L1084, L1157, L1191 (5th occurrence)
and `"discovery_empty"` at L1042. Both are spec drift. Phase 4 will compound
this when H5–H10 add their own action types. Punch-list item is correct.

### §2.2 `parcel_id="(none)"` magic string — CONFIRMED (real bug)
`research.py:1056` and `:1205`. The `flagged_items.parcel_id` column is
declared `parcel_id TEXT` with no NOT NULL (verified in `prepare.py` schema).
The string `"(none)"` is a sentinel that breaks any future
`WHERE flagged_items.parcel_id = parcels.parcel_id` join because `"(none)"`
will never match a real `fulton-*` parcel id. Should be Python `None` (psycopg
sends as SQL NULL). Punch-list item is correct.

### §2.3 `_DiscoverySession._spacing_sleep` thread-safety — RATIONALE WRONG
The orchestrator's claim that "they converge instead of staggering" is
incorrect. Walking through the code at L262-L273:
the lock-protected reservation `_last_request_at[host] = max(now, last) + max(wait, 0)`
correctly stages concurrent threads. Trace: thread A enters at t=0.5 with
last=0; sets last=0.5+0.5=1.0; sleeps 0.5s; fires at t=1.0. Thread B enters
at t=0.51 (lock acquired after A); sees last=1.0; computes wait=1.49; sets
last=1.0+1.49=2.49; sleeps 1.49s; fires at t=2.0. That's 1.0s after A,
correctly staggered.

The code is thread-safe for staggering. The formula does over-stagger by
`elapsed` (B records 2.49 even though it fires at 2.00), but that's
conservative-not-broken. The punch-list item should still land — adding a
docstring note that the class is single-threaded by design is good practice
— but the rationale must be rewritten. The class is *thread-safe*; we just
don't use it concurrently. Punch-list item §6.E rewrites the framing.

### §2.4 Cache write before yield — CONFIRMED (not Phase 3 bug)
`research.py:788-789` writes the cache file before the for-loop yields features
at L795-L796. With Phase 3's no-early-break consumers this is fine. Phase 4+
should revisit if it adds early termination. No action this phase.

### §2.5 Pagination fallback test missing — CONFIRMED
`test_pagination_terminates_on_exceeded_false` uses fixtures that include
`exceededTransferLimit` (page1=true, page2=false). The fallback at
`research.py:801-802` (`exceeded is None and len(features) < page_size`) is
not exercised. The empty-corridor test exercises the `not features` branch
at L803-804, a different path. Punch-list item is correct.

---

## 4. New risks the orchestrator may have missed

### 4.A `test_no_immutable_writes` AST scan misses non-`open()` writes
The scan at `test_discovery.py:140-155` only catches `Call(open, [<Constant path>, <Constant mode>])`.
It misses `Path.write_text`, `Path.open`, `csv.writer`, `shutil.copy`, and
`open(_DYNAMIC_PATH, ...)` patterns. Promote to a more aggressive scanner that
walks Attribute access patterns and resolves any string constant containing
`"parameters.json"`, `"program.md"`, or `"sources.json"` in the function or
its kwargs. **Severity: S2.** New §6.A.

### 4.B Multi-polygon centroid mismatch with kept outer
Already covered under R-07 above. **Severity: S2** because H1 envelope check
gets the wrong centroid for multi-polygon parcels with non-first largest ring.
Phase 3 impact bounded (H1 envelope is loose); Phase 4's true county-polygon
H1 will be more sensitive to this. New §6.B.

### 4.C `harness=degraded` path is not covered
`research.py:1201-1210` emits a flag row when harness is degraded; this branch
is not exercised by any test. **Severity: S3.** New §6.C.

### 4.D `test_filter_pipeline_extensible` only checks list length
Already covered under R-42. **Severity: S3.** New §6.D.

### 4.E No risks found in these categories (looked explicitly)
- **Silent data corruption (units/encoding)**: `_coerce_int`/`_coerce_float`
  reject NaN; `land_sf = acreage * 43560.0` is the standard conversion.
  No issues.
- **Contract violations between research.py and prepare.py**: verified
  `verify_parameters_unchanged`, `get_parameters`, `get_connection` are all
  used per `prepare.py`'s public surface. No re-imports or shadowing.
- **Broad `except Exception: pass` swallowing failures**: every bare-ish
  except in `research.py` (L932-L937, L951-L956, L989-L994, L1045-L1046,
  L1060-L1061, L1160-L1161, L1194-L1195) logs via `log.exception` before
  rolling back. Acceptable defensive code; not silent.
- **Transaction boundary partial commits**: per-parcel transaction at L946
  and L968 is correct. No multi-row commits where a partial failure would
  leave inconsistent state.
- **`ON CONFLICT` column-list mismatches**: `_SQL_UPSERT_PARCEL` at L584-L613
  refreshes every column the connector should refresh and preserves
  `discovery_date` and `discovery_source` via `COALESCE(parcels.x, EXCLUDED.x)`.
  Verified.

---

## 5. Test suite confirmation

```
$ python3 -m unittest tests.test_discovery -v
...
Ran 48 tests in 0.068s
OK
```

All 48 tests pass; no network calls; no real DB. Matches the runtime
recorded in `02_code_writer_response.md` §"Test run summary".

---

## 6. Recommended Phase 3.1 punch list (revised)

The original §10 list (six items) plus three new items, with §10 item 3's
rationale rewritten:

1. **Action_type vocabulary alignment** (§2.1, unchanged from §10.1).
   Add `"abort"` and `"discovery_empty"` to `program.md:127`. Human-only edit.

2. **Replace `parcel_id="(none)"` with `None`** (§2.2, unchanged from §10.2).
   `research.py:1056`, `:1205`. One-line each. Add a test asserting cycle-level
   flag rows have `parcel_id IS NULL`.

3. **(REWRITTEN)** **Document `_DiscoverySession` single-threaded design**
   (§2.3, was §10.3). The class IS thread-safe for the staggering use case
   (verified above), but research.py is single-threaded by design and the
   docstring should call this out so a future contributor doesn't add
   concurrent callers and rely on staggering semantics that are
   conservative-but-correct.

4. **Add fallback-pagination test** (§2.5, unchanged from §10.4). New fixture
   with `exceededTransferLimit` field absent and `len(features) < page_size`.

5. **Live PostGIS CI workflow** (§6.1, unchanged from §10.5). New
   `.github/workflows/discovery-fulton.yml` analogous to `harness-fulton.yml`
   with a postgres+postgis service container.

6. **Add `field_mapping_drift` and `cycle_id_collision` tests** (§6.2/§6.4,
   unchanged from §10.6).

7. **(NEW §6.A)** Strengthen `test_no_immutable_writes` to catch
   `Path.write_text`, `Path.open`, dynamic paths via name resolution. ~30
   lines of test code.

8. **(NEW §6.B)** Fix multi-polygon centroid bug at `research.py:844`. After
   `_arcgis_polygon_to_wkt` returns, recompute centroid from the *kept* outer
   ring, not `rings[0]`. Cleanest: have `_arcgis_polygon_to_wkt` return
   `(wkt, was_multi, kept_outer)` and use `_ring_centroid(kept_outer)`. Add
   a unit test with a fixture where the largest ring is not first.

9. **(NEW §6.C)** Add a `harness=degraded` test that asserts the cycle
   proceeds AND a flag row is emitted with parcel_id NULL (covered by item 2's
   fix).

10. **(NEW §6.D)** Strengthen `test_filter_pipeline_extensible` to actually
    invoke `_process_parcel` against a fixture with a synthetic filter
    appended, asserting the new filter's flag/reject is observed in the
    SQL execution log.

Estimated total: ~4 hours (was ~3). Items 1, 2, 3, 7, 8, 9, 10 are quick;
items 4 and 6 require fixtures; item 5 is the largest single piece (CI
workflow + live-PostGIS happy-path test).

---

## 7. Final verdict

**AMEND.** Previous Agent 3's APPROVE-WITH-FOLLOWUPS verdict at commit `48d805c`
stands; Phase 3 ships. The Phase 3.1 punch list above (10 items) replaces the
6-item list in `03_reviewer_decision.md` §10. Land Phase 3.1 in a single
follow-up before Phase 4 begins.

A future session with working sub-agent streaming should re-run this
re-validation one more time. The orchestrator-on-orchestrator-on-orchestrator
chain is the deepest integrity caveat in this codebase and the
infrastructure that would resolve it (working Opus 4.7 streaming for
~1500-word reviews) is the precondition.

---

AGENT 3 (orchestrator inline) DONE — verdict: AMEND, 10 punch-list items
