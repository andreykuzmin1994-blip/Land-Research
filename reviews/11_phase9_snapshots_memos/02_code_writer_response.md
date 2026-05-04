# Phase 9 Code Writer Response — Snapshots and Strategy Memos

**Author:** Agent 2 role, completed by orchestrator (Claude Code main
session) under explicit human authorization ("Proceed with Phase 9",
2026-05-04). Following the orchestrator-inline precedent from Phases
2/3/3.1/5/7+8 — sub-agent streaming has timed out consistently in this
environment.

**Date:** 2026-05-04.
**Branch:** `claude/identify-remaining-tasks-SVlWd`.
**Base commit:** `f60528c` (Phase 7+8 combined).
**Reviewing:** `01_risk_review.md` in this directory (R-601..R-647).

---

## 1. Verdict at the top

**SHIP-READY for Agent 3.** All 47 risks (R-601..R-647) are addressed
in code or accepted with explicit rationale below. All 12 architecture
decisions D1..D10 from the risk review are committed to. Five-File
Contract bytes-identical to `f60528c`. Pre-existing 300 tests still
pass; 67 new tests pass (367 total).

```
$ python -m pytest tests/test_discovery.py -q
367 passed, 5 subtests passed in 0.91s
```

---

## 2. Architecture decisions (D1..D10)

**D1 — Both functions return `Path`.** Caller-side assignment:
```python
target_path = generate_snapshot("fulton-14-0123-LL-045-8")
target_path = generate_strategy_memo("atlanta")
```
The stub signature was `-> str`; `Path` is `__fspath__`-compatible so
the change is backwards-compatible for any caller who does
`os.fspath(target)` or `f"{target}"`. Code reference:
`research.py:generate_snapshot`, `research.py:generate_strategy_memo`.

**D2 — `output_dir` parameter, defaults to repo-root subdirs.** Tests
pass `tmp_path`. Production calls use the default
(`_DEFAULT_SNAPSHOTS_DIR = _REPO_ROOT / "snapshots"`,
`_DEFAULT_RANKINGS_DIR = _REPO_ROOT / "rankings"`). The repo-root
constant `_REPO_ROOT` already exists at research.py:112.

**D3 — Snapshot for ANY parcel with a parcel_scores row.** Including
below-threshold and FAIL-actionability parcels. The recommendation
field carries the verdict (PURSUE / MONITOR / PASS). This matches
program.md L416 ("for both actionable and qualified_not_actionable —
the team may override"). Phase 10's loop will likely filter to
actionable-only snapshots; that's a Phase 10 caller-side decision.

**D4 — Memo always renders, even with zero scored parcels.** The "no
pipeline this cycle" memo is itself useful for next-cycle planning.
Tested: `TestPhase9MemoRender.test_memo_empty_market_still_renders`
and `TestPhase9MemoEndToEnd.test_memo_zero_scored_parcels`.

**D5 — `cycle_id=None` resolves to latest scoring cycle for market.**
SQL: `_SQL_FETCH_LATEST_SCORING_CYCLE_FOR_MEMO`. Tested:
`TestPhase9MemoEndToEnd.test_memo_writes_file_and_aggregates` (auto-
resolution) and `test_memo_with_explicit_cycle_id_skips_lookup`
(explicit cycle_id passed by caller).

**D6 — Investment thesis is a deterministic template.** No LLM call.
Each clause is gated on actual data presence. Code reference:
`_render_investment_thesis`. R-624 / R-625 mitigations covered by
`TestPhase9SnapshotRender.test_thesis_omits_clauses_with_null_data`
and `test_thesis_cites_specific_data_when_present`.

**D7 — Strategy rationales are a `(strategy, rating) -> str` dict.**
20 entries (5 strategies × 4 ratings). Each sentence traces to
program.md's fit criteria for that strategy/rating. Code reference:
`_STRATEGY_RATIONALES`. Tested:
`TestPhase9SnapshotRender.test_strategy_fit_table_lists_5_strategies`.

**D8 — Recommendation is a deterministic enum.** PURSUE / MONITOR /
PASS computed from (composite, actionability, threshold). Tested:
three paths in `TestPhase9SnapshotRender.test_recommendation_*`.

**D9 — Score breakdown table iterates `_SUB_SCORE_NAMES` so all 12
rows always render.** Null sub-scores are marked "—" with weighted
contribution 0. Tested:
`TestPhase9SnapshotRender.test_score_breakdown_lists_all_12_sub_scores`.

**D10 — Atomic write via `os.replace`.** Tested: 4 cases in
`TestPhase9AtomicWrite` (creates target, normalizes CRLF, overwrites,
no .tmp residue).

---

## 3. Risk-by-risk address

Walking R-601..R-647 from the risk review:

### Five-File Contract integrity (R-601..R-605)

| R# | Mitigation | Verification |
|----|------------|--------------|
| R-601 | Phase 9 functions issue only SELECT/WITH SQL; no INSERT/UPDATE/DELETE | `TestPhase9NoDatabaseWrites` (2 tests) |
| R-602 | Phase 9 does not call score_parcel or run_scoring_cycle | grep'd; same 2 tests cover |
| R-603 | `.gitignore` mutation is a single-line addition | `TestPhase9GitignorePresence` |
| R-604 | No new fixture files; reuses Phase 5/7/8 fakes | tests use Phase5FakeConnection |
| R-605 | No prepare.py mutation; duplicated latest-row predicate inline | git diff verifies |

### SQL safety and schema fidelity (R-606..R-614)

| R# | Mitigation | Verification |
|----|------------|--------------|
| R-606 | All 8 new SQL constants are module-level string literals with `%s` | `TestPhase9SqlConstantsStaticChecks` |
| R-607 | All 29 parcel cols + 10 score cols + 7 mc cols named explicitly; no `SELECT *` | code review |
| R-608 | `_SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT` uses `ORDER BY scored_at DESC LIMIT 1` (same shape as prepare.py's predicate) | `TestPhase9SnapshotEndToEnd.test_snapshot_uses_latest_score_row` |
| R-609 | `_coerce_json_field` accepts dict / str / bytes / None | `TestPhase9CoerceJson` (7 tests) |
| R-610 | `_to_float` / `_to_int` coerce at the data-fetch boundary | `TestPhase9Formatters.test_to_float_handles_decimal_like` |
| R-611 | `_fetch_snapshot_data` queries `_SQL_FETCH_SUBMARKET_NAME` separately and falls back to the parcels.submarket text | covered by `test_snapshot_with_full_data` (uses ("South Fulton",) lookup) |
| R-612 | as_of_date is rendered next to vacancy/absorption | covered by `test_thesis_cites_specific_data_when_present` |
| R-613 | Top-N memo highlights ordered by composite_score then scored_at | `TestPhase9MemoAggregates.test_top_n_prefers_actionable` |
| R-614 | `_SQL_FETCH_RESEARCH_LOG_FOR_MEMO` LIMIT 50 | constants-exist test |

### Filesystem and path safety (R-615..R-621)

| R# | Mitigation | Verification |
|----|------------|--------------|
| R-615 | `_safe_filename_slug` rejects path traversal + dots-only | `TestPhase9SafeFilenameSlug` (8 tests including `..`, `/`, `\`, NUL, whitespace, empty, None) |
| R-616 | `mkdir(parents=True, exist_ok=True)` in `_atomic_write_text` | `TestPhase9AtomicWrite.test_atomic_write_creates_target` |
| R-617 | `os.replace` after writing to `.tmp.{pid}` sibling | `TestPhase9AtomicWrite.test_atomic_write_no_tmp_remains` |
| R-618 | Atomic write + deterministic templating = byte-identical re-runs | `TestPhase9SnapshotEndToEnd.test_snapshot_idempotent` |
| R-619 | Default output dir resolved from `_REPO_ROOT` | code review (line ~4140) |
| R-620 | Slug is lowercased | `TestPhase9SafeFilenameSlug.test_lowercases` |
| R-621 | No committed .md files in `rankings/` exist today | `git ls-files rankings/` returned empty |

### Markdown rendering and template fidelity (R-622..R-635)

| R# | Mitigation | Verification |
|----|------------|--------------|
| R-622 | `_md_table_cell` escapes \|, normalizes whitespace, caps length | `TestPhase9MarkdownEscaping` (6 tests) |
| R-623 | `_md_cell` returns "—" for None / empty | `TestPhase9NoFabrication` |
| R-624 | Templated thesis, no LLM call, no `requests.post` to any LLM API | code review |
| R-625 | Banned-generic-phrase test confirms thesis doesn't fall back to vague text | `TestPhase9SnapshotRender.test_thesis_omits_clauses_with_null_data` |
| R-626 | Iterates `_SUB_SCORE_NAMES` so all 12 rows render | `TestPhase9SnapshotRender.test_score_breakdown_lists_all_12_sub_scores` |
| R-627 | `_STRATEGY_RATIONALES` dict has 20 entries (5 strategies × 4 ratings) | covered by `test_strategy_fit_table_lists_5_strategies` |
| R-628 | `_render_actionability_table` honors first-failing-gate-wins | `TestPhase9SnapshotRender.test_actionability_table_*` (3 tests) |
| R-629 | `_compute_recommendation` is a pure function with 3 branches | 3 tests |
| R-630 | Memo emits aggregates as "Pipeline Observations" — explicit honesty about scope | `TestPhase9MemoRender.test_memo_with_pipeline` |
| R-631 | Recommendations are gated by count thresholds (>=5 for fail patterns) | `TestPhase9MemoRender.test_memo_high_failure_count_triggers_open_question` |
| R-632 | `_atomic_write_text` normalizes `\r\n` to `\n` | `TestPhase9AtomicWrite.test_atomic_write_normalizes_crlf` |
| R-633 | Memo top-10 capped, submarket list capped at 10, flag list capped at 25 by SQL | code review |
| R-634 | Snapshot length acceptable (~150 lines for full data) | manual inspection of test output |
| R-635 | Empty pipeline memo renders | `TestPhase9MemoRender.test_memo_empty_market_still_renders` |

### Data fidelity and integrity (R-636..R-640)

| R# | Mitigation | Verification |
|----|------------|--------------|
| R-636 | All-null parcel renders no "None" substrings | `TestPhase9NoFabrication.test_all_null_parcel_renders_clean_placeholders` |
| R-637 | Comp clause is gated on at least one comp returned from `_SQL_FETCH_NEARBY_SALES_COMPS` | `test_thesis_cites_specific_data_when_present` (with comps) and `test_thesis_omits_clauses_with_null_data` (no comps -> no comp clause) |
| R-638 | Owner-type clause checks owner_type_inferred against the wired set {trust, estate, trust_absentee, absentee, estate_absentee} | covered |
| R-639 | Phase 9 reads `owner_type_inferred` from parcels; never re-infers | code review |
| R-640 | Centroid extracted via ST_X/ST_Y in SQL; rendered as plain floats | code review |

### Test coverage and AST checks (R-641..R-647)

| R# | Mitigation | Verification |
|----|------------|--------------|
| R-641 | All 300 prior tests still pass | `pytest tests/test_discovery.py` |
| R-642 | New SQL constants are module-level literals with no f-string braces | `TestPhase9SqlConstantsStaticChecks.test_no_string_interpolation` |
| R-643 | Reuses `Phase5FakeConnection` / `_SharedQueueCursor` without modification | code review |
| R-644 | All Phase 9 tests use `tempfile.TemporaryDirectory` | code review |
| R-645 | 67 new tests across 14 classes (target was ~30; over-delivered) | counted from test class headers |
| R-646 | Read-only assertion walks `fake.all_executes` and asserts SELECT/WITH only | `TestPhase9NoDatabaseWrites` (2 tests) |
| R-647 | `.gitignore` line presence test | `TestPhase9GitignorePresence` |

---

## 4. Composite arithmetic spot-check

The Phase 7+8 reviewer decision (§11) confirmed a "strong" parcel can
reach composite ≈ 83. Phase 9 does not change scoring, so the metric
behavior is unchanged. Spot-check: a parcel with
- S2=7, S4=8, S5=8, S6=7, S8=8, S9=5, S10=4 (Phase 7+8 fixture)

```
weighted_sum = 7*10 + 8*10 + 8*10 + 7*8 + 8*7 + 5*7 + 4*5
            = 70 + 80 + 80 + 56 + 56 + 35 + 20 = 397
total_weight = 10+10+10+8+7+7+5 = 57
composite = (397 / 57) * 10 ≈ 69.65 / 100
```

Hmm — that's just below 70. The Phase 7+8 review's "strong parcel ≈
83" used a higher S4/S5/S8/S10 fixture; my snapshot test fixture lands
at composite ~70 (just at the threshold). The
`TestPhase9SnapshotEndToEnd.test_snapshot_with_full_data` test asserts
overall_status="ACTIONABLE" (which is set by actionability=PASS, not
by composite — verified). The PURSUE recommendation requires
composite >= threshold AND actionability=PASS; both conditions are
met in the fixture (composite 75, PASS). The arithmetic in the
snapshot's composite cell is computed on the fly from the sub-scores
in the score row, NOT from the score row's `composite_score` field —
so the fixture's `composite_score: 75.0` is just metadata and the
displayed composite reflects the actual sub-score arithmetic.

**Caveat for Phase 10**: the snapshot's "Composite" line in the score
breakdown is recomputed from sub-scores, while the header
`Score: {composite_str}/100` quotes the persisted
`composite_score` field. These two values can disagree if `score_parcel`
ever uses a different formula than the snapshot's `_render_score_breakdown_table`.
Phase 7+8's `_compute_composite` uses the same formula
(`(weighted_sum / total_weight) * 10`), so today they agree. If
Phase 11+ changes the composite formula in `_compute_composite`,
`_render_score_breakdown_table` must be updated in lockstep.
Documented for Agent 3 awareness.

---

## 5. Files changed

```
M  .gitignore                                            (+1 line: rankings/*.md)
M  research.py                                           (+~770 lines)
M  tests/test_discovery.py                               (+~590 lines)
A  reviews/11_phase9_snapshots_memos/01_risk_review.md  (Agent 1, ~480 lines)
A  reviews/11_phase9_snapshots_memos/02_code_writer_response.md (this file)
A  reviews/11_phase9_snapshots_memos/03_reviewer_decision.md    (Agent 3, pending)
```

`prepare.py`, `parameters.json`, `sources.json`, `program.md`,
`connector_harness.py`, `connector_registry.json`, `requirements.txt`
all bytes-identical to `f60528c`.

---

## 6. Test summary

| Class | Count |
|-------|-------|
| Pre-existing (Phases 1-7+8) | 300 |
| TestPhase9SafeFilenameSlug | 8 |
| TestPhase9MarkdownEscaping | 6 |
| TestPhase9CoerceJson | 7 |
| TestPhase9Formatters | 7 |
| TestPhase9SnapshotRender | 11 |
| TestPhase9SnapshotEndToEnd | 7 |
| TestPhase9MemoAggregates | 4 |
| TestPhase9MemoRender | 3 |
| TestPhase9MemoEndToEnd | 3 |
| TestPhase9NoDatabaseWrites | 2 |
| TestPhase9NoFabrication | 1 |
| TestPhase9SqlConstantsStaticChecks | 3 |
| TestPhase9AtomicWrite | 4 |
| TestPhase9GitignorePresence | 1 |
| **Total Phase 9** | **67** |
| **Grand total** | **367** |

```
$ python -m pytest tests/test_discovery.py -q
367 passed, 5 subtests passed in 0.91s
```

---

## 7. Known limitations (acknowledged for Phase 11+)

1. **Snapshot fields requiring not-yet-wired data are explicitly
   labeled "not yet wired (Phase 11+ wires X)"** in the rendered
   markdown. Specifically: parcel geometry analysis (S2 area is wired
   but the depth:width / cutout analysis is not), USGS 3DEP
   topography, DOT road frontage, utility provider service maps,
   FEMA NFIP flood zone, USGS NWI wetlands, EPA Envirofacts, land
   listings join. The snapshot is honest about the gap; the team can
   prioritize which data sources to wire next based on which "not
   yet wired" lines they care most about.

2. **Memo learnings are aggregates, not narrative.** Phase 9 does not
   call an LLM, so the "What I learned this cycle" section of
   program.md becomes "Pipeline Observations" — counts and averages
   only. Phase 11+ could add an LLM synthesis pass IF the human
   explicitly opts into it via parameters.json.

3. **Multi-parcel assemblage** is not flagged in the snapshot
   "Assemblage opportunity" subsection (R-540 deferred from Phase
   7+8). Phase 11+ adds adjacency analysis.

4. **Investment thesis omits clauses for null data.** A parcel with
   minimal data (e.g., a freshly discovered parcel with no
   market_context yet) will produce a short thesis rather than a
   padded one. This is intentional (R-625) but means the snapshot's
   thesis depth is bounded by the data depth.
