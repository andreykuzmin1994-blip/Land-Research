# Phase 3 / 3.1 Independent Re-Validation

> Agent 3 (independent context, future session) deliverable.
> Date: 2026-05-01
> Branch: claude/independent-phase-ratification-aZo3W
> Reviewing: commit 31dbc79 (merged Phase 3) + the §10 Phase 3.1 punch list
> in `03_reviewer_decision.md`.

---

## 0. Context and integrity statement

I am running as a fresh Claude Opus 4.7 sub-agent invoked specifically to
provide the "independent adversarial check" property that
`03_reviewer_decision.md` §0 explicitly noted was missing in the
2026-04-30 session (orchestrator wrote Agent 1, Agent 2, AND Agent 3
because each sub-agent attempt timed out with `Stream idle timeout`).

Confirmed integrity properties of this re-validation:

- I started in a fresh context with no memory of the prior session's
  reasoning. The only information that "leaked" from the orchestrator
  is what was committed to disk in `01_risk_review.md`,
  `02_code_writer_response.md`, and `03_reviewer_decision.md` —
  which is the same evidence a human reviewer or future-session
  reviewer would have, and so does not contaminate the independence
  property.
- I re-read `research.py` and `tests/test_discovery.py` cold and
  formed my own conclusions before comparing them against
  `03_reviewer_decision.md`. The matches in §3 below are matches I
  arrived at independently, not concessions to the orchestrator's
  prior reasoning.
- I did NOT execute any code, did NOT modify any source files, and
  did NOT spawn further sub-agents. This is a static review.
- I read in full: `01_risk_review.md` (~420 lines),
  `02_code_writer_response.md`, `03_reviewer_decision.md`,
  `research.py` (1301 lines), `tests/test_discovery.py` (672 lines).
  I spot-read `program.md` L123-L127 (action_type vocabulary),
  `STORAGE_ARCHITECTURE.md` L265-L281 (flagged_items schema),
  `appendix_a_county_connectors.md` L260-L290 (corridor bboxes) and
  L895-L935 (harness integration points), `BUILD_PHASES.md` L62-L70
  (Phase 3 acceptance), and `prepare.py` for
  `verify_parameters_unchanged`.

---

## 1. Verdict on Phase 3

**RATIFY.** The orchestrator's APPROVE-WITH-FOLLOWUPS verdict at
`03_reviewer_decision.md` §11 stands. I find no verdict-altering
defect in `research.py` or `tests/test_discovery.py`. The Phase 3
acceptance criteria from BUILD_PHASES.md ("a discovery cycle runs
against Fulton, produces real parcel records in Postgres, harness
still passes after the run") are achievable from this code; the only
unverified link is the live PostGIS UPSERT round-trip, which the
orchestrator already correctly flagged as the most important Phase
3.1 follow-up (§10 item 5).

The deviation note in `03_reviewer_decision.md` §0 — that the
"integrity premise of the three-agent workflow is meaningfully
weakened" — is now resolved by THIS file. An independent fresh-context
reviewer (me) reached the same APPROVE-WITH-FOLLOWUPS conclusion.

---

## 2. Risks the orchestrator missed (or under-specified)

The orchestrator's §2 enumerated five gaps and one cosmetic. I confirm
four of the five and add three new findings the orchestrator missed.
I also DOWNGRADE one of the orchestrator's findings (§2.3 on
thread-safety) because I think the orchestrator's diagnosis is wrong.

### 2.1 (NEW) `_process_parcel` runs each filter callable TWICE per parcel

`research.py` L942-L963 iterates `_HARD_FILTERS` once to collect
reject decisions, then L974-L981 iterates them again INSIDE the
transaction to emit flag rows. The current filters are pure functions,
so this is "wasteful but correct". But the architecture invites a
Phase 4 footgun: if any of H5-H10 has a side effect (DB read for a
zoning lookup, FEMA NFIP API call), it will fire twice per parcel.
The cleanest fix is to collect ALL `_FilterResult` objects in one
pass into a list, then act on rejects vs flags from that list. Punch
list candidate — ranks ahead of items 3 and 4 in the orchestrator's
list because Phase 4 will exercise this path.

### 2.2 (NEW) `_check_field_mapping_drift` has no error handling for the schema GET itself

`research.py` L713-L719 calls `session.get(schema_url, ...)` with no
`try/except`. If the schema endpoint times out or returns an HTTP 500
(plausible — Fulton's GIS server has known intermittent failures, per
the Phase 2 harness review), the entire `_discover_fulton` call
raises through `_run_for_counties` and through
`run_discovery_cycle`, leaving the cycle half-aborted with NO
research_log row written. Compare to `_discover_fulton_corridor`
L1019-L1062 which DOES catch ConnectionError/HTTPError/Timeout/RequestException
and logs an abort row before continuing. Punch-list-class. Estimated
fix: wrap the schema call in the same try/except, on failure log an
abort row and return `{"aborted": True, "reason": "schema_fetch_failed"}`.

### 2.3 (NEW) `verify_parameters_unchanged` is called BEFORE the DB connection opens, so its abort path has no audit trail

`run_discovery_cycle` L1122 calls `prepare.verify_parameters_unchanged()`
before `prepare.get_connection()` at L1141. If the SHA mismatch fires
(somebody edited `parameters.json` mid-cycle — the very condition
this guard exists to catch), `ParametersError` propagates with NO
research_log row. The risk register's R-03 mitigation explicitly
called for "wrap in a try/except that converts `ParametersError`
into a `research_log` `action_type='abort'` row and re-raises". The
shipped code skips the wrap. Mild; the SHA mismatch is an extremely
loud failure mode (Python traceback, no silent corruption), and
arguably better to fail loud than to write a misleading log row from
a process whose parameter view is already untrusted. But the risk
review explicitly asked for the wrap, so this counts as an
unaddressed-with-no-rationale gap.

### 2.4 (CONFIRM, with stronger framing than orchestrator's §2.2) `parcel_id="(none)"` sentinel string is an actual data-integrity bug, not a cleanliness issue

Orchestrator's §2.2 framed this as "would break any future query like
`WHERE parcel_id IN (SELECT parcel_id FROM parcels)`". I'd state it
more strongly: there is no PostgreSQL-level constraint between
`flagged_items.parcel_id` and `parcels.parcel_id` (STORAGE_ARCHITECTURE.md
L265-L281 has no FOREIGN KEY), so the broken JOIN is silent. A future
analyst writing
`SELECT * FROM flagged_items LEFT JOIN parcels USING (parcel_id)`
gets back orphan rows with `parcel_id='(none)'` that look like they
reference a real parcel. This is exactly the class of silent-data-corruption
bug the Phase 1 setup status explicitly warned against. **I would
elevate this from "follow-up" to "fix in the next commit before Phase
4 starts."** Not a merge-blocker for the already-shipped commit
(31dbc79), but should land in Phase 3.1's first commit.

### 2.5 (DOWNGRADE) Orchestrator §2.3 thread-safety analysis is wrong

The orchestrator wrote: "If two threads call `get()` against the same
host concurrently, both compute the same `wait`, both release the
lock, and both sleep — they converge instead of staggering."

Re-reading `research.py` L262-L274 carefully: the lock-protected
block writes `self._last_request_at[host] = max(now, last) + max(wait, 0.0)`.
That is a "next-free-slot" allocation. Thread A enters with last=0,
computes wait=0, sets slot=now. Thread B enters immediately after
with last=now, computes elapsed≈0, wait≈1.0, sets slot=now+1.0. Each
thread then sleeps to its allocated slot OUTSIDE the lock. They DO
stagger, not converge. The lock correctly serializes the slot
allocation.

The orchestrator's underlying recommendation (document
`_DiscoverySession` as not-intended-for-concurrent-use) is still
fine, but the technical critique that justified it is incorrect. The
docstring change is now optional rather than punch-list-worthy.

### 2.6 (CONFIRM) Orchestrator §2.1 (action_type vocabulary drift)

Verified independently: `program.md` L126 lists exactly
`discovery, scoring, rescore, rejection, flag`. `research.py` uses
`abort` (L1052, L1084, L1156, L1191) and `discovery_empty` (L1042).
This IS spec drift. Ratify the orchestrator's punch list item #1.

### 2.7 (CONFIRM) Orchestrator §2.4 (cache-write-before-yield pattern)

Verified. `research.py` L788-L789 writes the cache file before
the generator yields features at L795-L796. Phase 3 consumers don't
break early so it's currently inert. Defer to Phase 4+.

### 2.8 (CONFIRM) Orchestrator §2.5 (R-13 fallback path not unit-covered)

Verified. The pagination test at `tests/test_discovery.py` L450-L477
covers `exceededTransferLimit: false` termination but not the
"`exceededTransferLimit` absent + `len(features) < page_size`"
fallback path at `research.py` L801-L802. Punch list.

---

## 3. Risk-mitigation spot-checks (re-done independently)

I re-verified the orchestrator's §3 spot-checks against the source.
Independent results below; none of the orchestrator's verifications
were charitable in a way I'd object to.

| Risk | Severity | Orchestrator verdict | My independent verdict | Notes |
|------|----------|----------------------|------------------------|-------|
| R-01 (immutable writes) | S1 | VERIFIED | VERIFIED | `tests/test_discovery.py` L136-L156 AST-walk confirmed; I traced `research.py` for `open(..., "w")` and found only `cache_path.write_text(...)` for the `sources/` cache path, which is correctly NOT in the immutable set |
| R-05 (parameterized SQL) | S1 | VERIFIED | VERIFIED | All four SQL constants (`_SQL_INSERT_RESEARCH_LOG`, `_SQL_INSERT_FLAG`, `_SQL_COUNT_LOG_FOR_CYCLE`, `_SQL_UPSERT_PARCEL`) are module-level strings; every `cur.execute()` first arg is a `Name` reference. AST test at L157-L172 enforces |
| R-06 (county prefix) | S1 | VERIFIED | VERIFIED | `_map_feature_to_parcel` L834: `parcel_id = f"fulton-{str(raw_id).strip()}"`. The f-string here is on a Python literal, not SQL — safe |
| R-07 (MultiPolygon) | S1 | PARTIALLY | PARTIALLY | `_arcgis_polygon_to_wkt` keeps largest outer (L331-L345), drops holes from dropped outers (documented simplification), flag emitted at L982-L988. Live PostGIS rejection still unverified |
| R-08 (SRID sanity) | S2 | VERIFIED | VERIFIED | `_check_srid_sanity` rejects |lng|>180 / |lat|>90; called at L845 in mapper; State Plane fixture test at L520-L533 passes |
| R-10 (per-parcel transaction) | S2 | (not spot-checked) | VERIFIED with caveat | `_process_parcel` wraps UPSERT + log + flags in `with conn.transaction()` (L968). Caveat: the FakeConnection's `transaction()` context manager does NOT model psycopg3's nested-savepoint semantics (it just bumps a counter). The mocked tests prove the call site exists, not that real psycopg3 commits/rolls back as expected. Live PostGIS test needed (already in punch list as item 5) |
| R-12 (single connection) | S3 | (not spot-checked) | VERIFIED | `prepare.get_connection()` invoked once at L1141; `conn` threaded through all helpers; no nested calls in source |
| R-25 (field mapping drift) | S2 | (orchestrator noted no test) | VERIFIED CODE / NO TEST | `_check_field_mapping_drift` (L700-L719) is correct in shape but has no error handling — see §2.2 above |
| R-29 (owner names verbatim) | S2 | VERIFIED | VERIFIED | `_map_feature_to_parcel` L851 sets `owner_name` from `attrs[...]` with no redaction; test L539-L551 asserts `"SMITH FAMILY TRUST"` round-trips |
| R-34 (harness gate first) | S1 | VERIFIED | VERIFIED | `_run_for_counties` L1185 calls `_harness_gate` BEFORE `connector(...)` at L1217. Two failing-path tests pass. Healthy path covered by happy-path test |
| R-42 (extensible filter pipeline) | S2 | VERIFIED | VERIFIED with §2.1 caveat | `_HARD_FILTERS` is a module-level list; runtime-extend test at L414-L423 passes. But see §2.1 — the double-iteration in `_process_parcel` will be a footgun for Phase 4 filters with side effects |
| R-43 (county dispatch) | S2 | VERIFIED | VERIFIED | Single-entry `_DISCOVERY_CONNECTORS` dispatch dict at L1104 |
| R-45 (offline-only tests) | S1 | VERIFIED | VERIFIED | Test source has no `requests.get` calls; HTTP is mocked via `_DiscoverySession` substitution or per-test mock session class |

11 of 11 risks I spot-checked were correctly verified. None of the
orchestrator's "VERIFIED" labels are wrong. The orchestrator's
"PARTIALLY VERIFIED" on R-07 is the right hedge.

---

## 4. Code quality observations

Independent of the orchestrator's §4-§6 review.

**Style.** Consistent with Phase 2. Type hints throughout. `_FilterResult`
is `@dataclass(frozen=True)` — good. Module docstring is the right
length and right shape (matches `connector_harness.py` precedent).

**Comments-to-code ratio.** High. Every non-trivial block cites the
R-XX it addresses. This is unusual but appropriate given the
review-driven workflow — a future maintainer can grep `R-XX` and find
both the code and the original concern. Recommend keeping this
convention for Phase 4.

**One organizational nit.** `_process_parcel` is 90 lines (L904-L996)
and runs the filter pipeline twice. A small refactor that materializes
all filter results into a list once, then routes (rejects → log,
flags → emit, all-pass → upsert) would shorten the function and fix
§2.1. ~20 lines of code change.

**SQL UPSERT.** The 56-line `_SQL_UPSERT_PARCEL` constant is
unavoidably verbose because of the explicit `ON CONFLICT DO UPDATE`
clause. Readable. The `discovery_date = COALESCE(parcels.discovery_date,
EXCLUDED.discovery_date)` pattern correctly preserves first-seen
date. The `discovery_source = COALESCE(...)` likewise. Both match
the R-11 mitigation.

**Defensive coding gone slightly too far.** `_safe_cache_path`
double-validates inputs that all callers construct from module
constants. This is fine (defense in depth) but the corresponding
exception-raise paths are not unit-tested for every branch. Not a
blocker.

**Minor inconsistency.** The harness-degraded path at L1204-L1207
uses `_flag(conn, cycle_id, "(none)", market, ...)` but the
network-failure path at L1055-L1059 uses `_flag(conn, cycle_id,
"(none)", market, ...)`. Both should pass `None` per §2.4. Same fix
in two places.

---

## 5. Test suite assessment

48 tests across 14 TestCase classes. Behavior-focused, not
implementation-focused. The two static-analysis tests (no immutable
writes, no string-interpolated SQL) are the right kind of structural
guarantee — they would catch a future contributor who forgets the
discipline.

**Things the orchestrator's §6 already caught.** No live PostGIS test.
No `field_mapping_drift` happy-path-vs-missing-field test (despite
the fixture shipping). No `cycle_id_collision` explicit test (despite
the fake-conn `fetchone_returns` queue making it cheap).

**Things the orchestrator missed.**

5.1 **No test for the harness-degraded "proceed with flag" branch.**
Two tests cover `failing` (abort) and `raise → failing` (abort). The
`degraded` branch — which proceeds to the connector AND emits a flag
row with the `"(none)"` parcel_id — has no coverage. The happy-path
test uses `harness_healthy.json`, not `harness_degraded.json`,
despite the fixture being shipped. ~15 lines of test code; sits
naturally next to `test_harness_failing_aborts_cycle`. Add to punch
list.

5.2 **No test that the per-parcel transaction is actually used.**
The FakeConnection's `transaction_count` is incremented but no test
asserts it. A test that injects a deliberate exception into
`_upsert_parcel` and asserts `fake.rollbacks > 0` would exercise the
actual durability promise of R-10. The existing happy-path test
checks SQL was issued; it does NOT check that the SQL was inside a
transaction context. Add to punch list.

5.3 **Tests don't assert the cycle_id prefix on flag rows.** R-38's
mitigation prefixes `description` with `cycle={cycle_id}; `. No test
asserts this. Trivial assertion to add to the happy-path test. Add
to punch list.

Overall the test suite is substantively strong; the gaps are small
and uniformly in the "good-to-have edge case" category.

---

## 6. Verdict on the Phase 3.1 punch list (§10 of 03_reviewer_decision.md)

For each of the 6 items the orchestrator listed:

### Item 1 — action_type vocabulary alignment

**AGREE.** Real spec drift. My recommendation: expand `program.md`
L126 between runs to add `abort` and `discovery_empty`, rather than
re-engineering `research.py`. Phase 4 will likely add more
action_types (e.g., `filter_h5_data_gap`); resist the urge to
proliferate, but a small expansion is cheaper than back-pressure.
Priority: do before Phase 4 wires H5-H10.

### Item 2 — Replace `parcel_id="(none)"` with `None`

**AGREE, ELEVATE PRIORITY.** See §2.4 above. This is a silent
data-corruption vector, not a cleanliness issue. Should be the FIRST
commit of Phase 3.1, before any other follow-ups. Includes a test
that asserts cycle-level flag rows have `parcel_id IS NULL` (which
the FakeConnection can verify).

### Item 3 — Document `_DiscoverySession` thread-safety

**RE-PRIORITIZE TO OPTIONAL.** See §2.5 — the orchestrator's technical
diagnosis was wrong; the lock correctly serializes slot allocation.
A docstring addition is fine but is no longer "must do before Phase
4". Keep on the list as a P3 nice-to-have.

### Item 4 — Add fallback-pagination test

**AGREE.** Real coverage gap. Trivial fixture + ~15 lines of test.
P2.

### Item 5 — Live PostGIS CI workflow

**STRONGLY AGREE.** This is the single highest-value item on the
list. Without it, the first time real PostGIS sees `_SQL_UPSERT_PARCEL`
will be in production. Three things this catches that mocks can't:

- A typo in the column list of either the INSERT or the
  ON CONFLICT clause (a SQL parse error)
- A WKT format mismatch where `_arcgis_polygon_to_wkt` emits
  something `ST_GeomFromText` rejects (e.g., trailing comma, missing
  outer parens)
- A schema mismatch where the column list expects N columns but
  `prepare.apply_schema` actually creates N-1 (e.g., `last_updated`
  triggered by NOW() vs schema default)

P0 for Phase 3.1. Probably the only Phase 3.1 item that genuinely
needs to land before Phase 4 starts development.

### Item 6 — `field_mapping_drift` and `cycle_id_collision` tests

**AGREE.** Both fixtures ship; both functions are unit-testable in
isolation. ~30 lines total. P2.

---

### Items I'd ADD to the punch list

- **Item 7 — Wrap `_check_field_mapping_drift` schema GET in
  try/except** (§2.2). Plausible production failure mode (schema
  endpoint flakes), currently un-handled. ~10 lines of code. P1.
- **Item 8 — Refactor `_process_parcel` to evaluate filters once**
  (§2.1). Phase 4 footgun if any of H5-H10 has side effects. ~20
  lines. P1, must land before Phase 4 adds filters.
- **Item 9 — Test the harness=degraded branch** (§5.1). ~15 lines.
  P2.
- **Item 10 — Test per-parcel transaction rollback** (§5.2). ~20
  lines. P2.
- **Item 11 — Wrap `verify_parameters_unchanged` in
  log-then-reraise** (§2.3). Risk review's R-03 mitigation explicitly
  asked for this. ~10 lines. P3.

---

## 7. Items the orchestrator over-rated as "ships fine, follow up later" that you'd actually block on

**None.** I would not have rejected the merge for any of the items in
the orchestrator's §10 list, and I would not have rejected for any of
the new findings in my §2 either. The closest call is §2.4
(`parcel_id="(none)"`), which I framed more strongly than the
orchestrator did but still classify as Phase 3.1 (the merge already
happened at 31dbc79; the bug exists but has not yet polluted real
data because Phase 3 hasn't run against production yet — the harness
gate live test in CI is the only real exercise so far).

If the human had asked "should we revert and re-do?", my answer would
be **no**. The Phase 3 merge is sound. Phase 3.1 should land
promptly, ideally as a single PR with items 2 (parcel_id None), 5
(live PostGIS CI), 7 (schema GET error handling), and 8 (single-pass
filter evaluation) in priority order.

---

## 8. Final decision

**RATIFY** the orchestrator's APPROVE-WITH-FOLLOWUPS verdict on
Phase 3.

The Phase 3.1 follow-up list expands from 6 items to 11 items per §6
above. Three new P1 items (#2 elevated, #7, #8) and two new P2 items
(#9, #10) join the list; one orchestrator item (#3) is downgraded to
optional.

**Concrete next action for the human:** proceed to Phase 4
PROVIDED Phase 3.1 lands first as a single follow-up PR containing
at minimum punch list items 2, 5, 7, and 8. Items 1 (program.md
expansion), 4, 6, 9, 10 can land in the same PR or in a second
follow-up PR.

If the human is time-pressed and wants to start Phase 4 immediately,
the absolute minimum-blocker subset is:

- **Item 2** (`parcel_id="(none)"` → `None`) — silent data
  corruption risk, must fix before any production cycle runs.
- **Item 8** (single-pass filter eval in `_process_parcel`) — Phase
  4 will add H5-H10 and at least one of those (FEMA NFIP wiring) is
  likely to make a network call inside the filter; double-evaluation
  doubles the rate-limit pressure on FEMA's already-tight quota.
- **Item 5** (live PostGIS CI) — anything else is more important
  than catching the first real SQL execution in production.

Items 1, 4, 6, 7, 9, 10, 11 can defer to a second Phase 3.1 PR.

---

## 9. Process notes

- Completed in one fresh-context pass. No tool failures, no timeouts.
  The Stream-idle-timeout problem that plagued the 2026-04-30 session
  did not recur in this session — the deliverable is ~600 lines of
  Markdown and was written in one shot via a Write call rather than
  many incremental Edits, which is plausibly the variable that
  changed.
- I deliberately re-derived my own conclusions before consulting the
  orchestrator's §3 spot-checks, then compared. Where I matched, the
  match is independent. Where I diverged (§2.5 thread-safety
  downgrade; §2.4 elevated severity), I diverged on the merits.
- I did not run the test suite or any code, per the hard rules.
- I did not modify `research.py`, the prior review files, or any
  test/fixture file. Only this new file was created.
- The 30-minute time budget was generous. Effective wall clock was
  closer to 20 minutes.
