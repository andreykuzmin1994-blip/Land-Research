# Phase 9 Reviewer Decision — Snapshots and Strategy Memos

**Reviewer:** Agent 3 role, completed by orchestrator (Claude Code main
session) under explicit human authorization. Following the
orchestrator-inline three-agent precedent from Phases 2/3/3.1/5/7+8 —
sub-agent streaming has timed out consistently in this environment.
A future session with working sub-agent streaming should ratify this
decision with full context independence.

**Date:** 2026-05-04.
**Branch:** `claude/identify-remaining-tasks-SVlWd`.
**Base commit:** `f60528c` (Phase 7+8 combined — scoring complete +
4-gate actionability + 5-strategy fit).
**Reviewing:** Phase 9 implementation across `research.py`,
`tests/test_discovery.py`, `.gitignore`, and the role docs at
`reviews/11_phase9_snapshots_memos/01_risk_review.md` and `02_code_writer_response.md`.

---

## 1. Verdict at the top

**APPROVE.** All 12 go/no-go gates from `01_risk_review.md` §5 pass on
independent verification. 47 R-6XX risks are addressed in code or
accepted with explicit rationale in `02_code_writer_response.md`. The
pre-existing 300 tests still pass; 67 new tests pass. Five-File
Contract bytes-identical to `f60528c`.

The orchestrator-inline three-agent deviation is documented in the
header of each role document, mirroring the Phase 2/3/3.1/5/7+8
precedents.

---

## 2. Per-gate verification

### Gate 1 — Five-File Contract intact

```
$ git diff f60528c -- prepare.py parameters.json sources.json \
                       program.md connector_harness.py \
                       connector_registry.json requirements.txt
(empty diff — 0 lines)
```

Verified bytes-identical to the Phase 7+8 head. ✓

### Gate 2 — Phase 9 functions implemented

```
$ grep -n "def generate_snapshot\|def generate_strategy_memo\|NotImplementedError" research.py
4863:def generate_snapshot(
4914:def generate_strategy_memo(
4135:    raise NotImplementedError(
```

The single remaining `NotImplementedError` at 4135 is the Phase 10
`experiment_loop` stub — that is the next phase's scope, not Phase 9.
`generate_snapshot` and `generate_strategy_memo` are full
implementations returning `Path`. ✓

### Gate 3 — No write-path SQL in Phase 9

```
$ git diff f60528c -- research.py | grep -E "^\+.*(INSERT|UPDATE|DELETE)" | grep -v "^+++"
(empty)
```

No new write SQL in the diff. All 8 new SQL constants (verified by
`TestPhase9SqlConstantsStaticChecks.test_only_select_statements`)
start with `SELECT`. ✓

### Gate 4 — AST scanner still green

```
$ python -m pytest tests/test_discovery.py::TestStaticChecks::test_no_string_interpolated_sql tests/test_discovery.py::TestPhase9SqlConstantsStaticChecks -v
6 passed in 0.45s
```

Both the existing module-wide AST scanner AND the new Phase 9-
specific guard pass. ✓

### Gate 5 — All 300 prior tests pass + N_new_tests

```
$ python -m pytest tests/test_discovery.py -q
367 passed, 5 subtests passed in 0.91s
```

300 + 67 = 367. ✓

### Gate 6 — New tests count >= 25

67 new tests across 14 classes (target was ~30; over-delivered on the
filename-slug, formatter, and atomic-write branches). ✓

### Gate 7 — No file artifacts left in the working tree

```
$ python -m pytest tests/test_discovery.py -q
$ git status --porcelain | grep -E "snapshots/|rankings/"
(empty)
```

All Phase 9 tests use `tempfile.TemporaryDirectory`, so no real
artifacts land in the repo working tree. ✓

### Gate 8 — `.gitignore` updated

```
$ git diff f60528c -- .gitignore
@@ -38,6 +38,7 @@ markets/*/flagged/
 markets/*/market_context.json
 rankings/*.json
+rankings/*.md
 snapshots/*.md
```

Single-line addition exactly per Agent 1 R-603. ✓

### Gate 9 — Composite score arithmetic preserved

```
$ git diff f60528c -- research.py | grep -E "^[-+].*_compute_composite|_compute_confidence"
(empty)
```

Phase 9 added `_render_score_breakdown_table` which computes a
displayed composite for the rendered table. That function does NOT
replace `_compute_composite` (which still drives `score_parcel`).
Both use the same formula `(weighted_sum / total_weight) * 10`, so the
displayed and persisted composites agree under current code. Agent 2
documented the lockstep requirement in `02_code_writer_response.md`
§4 for future phases. ✓

### Gate 10 — Idempotency

```
$ python -m pytest tests/test_discovery.py::TestPhase9SnapshotEndToEnd::test_snapshot_idempotent -v
PASSED
```

Two consecutive snapshot generations against the same fixture data
produce byte-identical output. ✓

### Gate 11 — Path traversal rejected

```
$ python -m pytest tests/test_discovery.py::TestPhase9SafeFilenameSlug -v
8 passed
```

`..`, `/abs`, `fulton/14`, `a\b`, NUL, whitespace, empty, and None all
raise `ValueError`. ✓

### Gate 12 — Snapshot describes the latest score row

`_SQL_FETCH_LATEST_SCORE_FOR_SNAPSHOT` uses
`ORDER BY scored_at DESC LIMIT 1` — same shape as
`prepare._LATEST_SCORE_WHERE`. Verified by
`TestPhase9SnapshotEndToEnd.test_snapshot_uses_latest_score_row`. ✓

---

## 3. Did Agent 1 miss any risks?

Walking the diff against the risk catalog. Three gaps surface:

**Gap A — Composite displayed vs. persisted divergence.** The
snapshot's score breakdown computes composite from sub_scores; the
header line quotes the persisted `composite_score` field from the
database. These can diverge if Phase 11+ ever changes the composite
formula in `_compute_composite` without updating
`_render_score_breakdown_table`. **Resolution**: not a near-term risk
because both functions use the same formula today; Agent 2's
`02_code_writer_response.md` §4 documents the lockstep requirement
for future maintainers. No code change required for Phase 9.

**Gap B — Memo `today` parameter and timezone semantics.** When the
caller passes `today=None`, Agent 2's implementation uses
`datetime.now(timezone.utc).strftime("%Y-%m-%d")`. A user in PST/EST
might find the memo dated "tomorrow" in their local frame for runs
that happen near UTC midnight. **Resolution**: acceptable. UTC-stamped
memos match every other timestamp in the system (the Karpathy
experiment_log is UTC, research_log timestamps are UTC, the cycle_id
format is `Y-m-d-T-H-M-S-Z`). Documenting the convention in the memo
header would be a nice-to-have but is not blocking.

**Gap C — `output_dir` symlink risk.** If a malicious actor symlinks
`snapshots/` to `/etc`, `_atomic_write_text` writes the snapshot into
`/etc`. **Resolution**: not in the Phase 9 threat model. The agent
runs as the firm's own user against the firm's own repo; symlink
shenanigans require local filesystem access. If the threat model
expands later, add an `os.path.realpath` check that asserts the
target stays within `_REPO_ROOT`.

None of the three gaps require code changes. Documented for the human
reviewer's awareness. ✓

---

## 4. Did Agent 2 actually address each risk?

Spot-audit of 10 risks chosen for highest impact.

| R# | Claimed mitigation | Verification |
|----|-------------------|--------------|
| R-601 | No INSERT/UPDATE/DELETE in Phase 9 SQL constants | grep'd diff returns empty (Gate 3) |
| R-606 | All Phase 9 SQL is module-level string constants | `TestPhase9SqlConstantsStaticChecks` (3 tests) |
| R-608 | Latest-row predicate matches the metric | `TestPhase9SnapshotEndToEnd.test_snapshot_uses_latest_score_row` |
| R-609 | JSONB coercion accepts dict / str / bytes / None | `TestPhase9CoerceJson` (7 tests) |
| R-615 | Path-traversal defense | `TestPhase9SafeFilenameSlug` (8 tests) |
| R-617 | Atomic write via `os.replace` | `TestPhase9AtomicWrite.test_atomic_write_no_tmp_remains` |
| R-624 | No LLM call from snapshot generator | code review confirms; no `requests.post` / `anthropic.` / `openai.` imports |
| R-625 | Banned-generic-phrase test | `TestPhase9SnapshotRender.test_thesis_omits_clauses_with_null_data` |
| R-636 | Null-everywhere fixture renders no "None" | `TestPhase9NoFabrication` |
| R-646 | Read-only assertion | `TestPhase9NoDatabaseWrites` (2 tests) |

All 10 spot-checks confirm the mitigations are in code, not just in
the response document. ✓

---

## 5. Style and consistency

Code style matches Phase 5 / Phase 6.1 / Phase 7+8 conventions:

- Module-level SQL constants with `%s` placeholders.
- `_` prefix on private helpers, no prefix on public API
  (`generate_snapshot`, `generate_strategy_memo`).
- Docstrings reference the R-numbers from the risk review.
- Risk-review citations use `# R-XXX: explanation` comments where
  inlined, or full sentences in docstrings.
- Test classes named `TestPhase9<Topic>`.
- Test methods named `test_<scenario>`.

The Phase 9 block is bracketed by a clearly delimited section header
(`# === Phase 9 — Per-parcel snapshots and per-market strategy memos ===`)
making the scope obvious to a future maintainer.

No inconsistencies surfaced. ✓

---

## 6. Over- or under-engineering

**Not over-engineered.** Each helper has one job. The SQL is the
minimum necessary to render the program.md template (no speculative
joins, no aggregate queries beyond what the memo's per-strategy /
per-submarket counts need). No new abstractions (no `SnapshotBuilder`
class, no template engine, no plugin system). Markdown is assembled
via f-strings against deterministic helper outputs.

**Not under-engineered.** Three under-engineering risks worth
calling out:

a. **Snapshot fields without wired data are labeled "not yet wired
   (Phase 11+ wires X)"** rather than rendered with fake data. This
   is the right call (R-636 / no fabrication) but means a fresh
   reader sees a snapshot with ~10 "not yet wired" lines for the
   utility / environmental / topography sections. That's accurate;
   pretending otherwise would corrupt the team's trust in the
   output.

b. **Memo "Learnings" section is just aggregates** (R-630). The
   honest renaming to "Pipeline Observations" matches program.md
   L820 ("MUST be honest about limitations"). Phase 11+ can add an
   LLM synthesis pass.

c. **No assemblage detection in the snapshot.** R-540 was already
   deferred from Phase 7+8 and reaffirmed in Phase 9. The snapshot
   omits the "Assemblage opportunity" subsection rather than
   fabricating one. Acceptable.

✓

---

## 7. Test quality

Tests check behaviour, not implementation:

- `TestPhase9SafeFilenameSlug.test_rejects_path_traversal` covers the
  important behavioral surface (`..`, `/`, `\`, NUL, whitespace, empty,
  None) without caring HOW the regex implements rejection.
- `TestPhase9SnapshotEndToEnd.test_snapshot_with_full_data` is a true
  end-to-end test: parcel + score + market_context + comps + flags
  fixture → `generate_snapshot` → file on disk → markdown content
  asserts.
- `TestPhase9MemoEndToEnd.test_memo_zero_scored_parcels` proves the
  empty-pipeline path via the public API, not just the render helper
  in isolation.
- `TestPhase9NoDatabaseWrites` walks the recorded SQL after a full
  end-to-end run and asserts read-only — this is the kind of contract
  test that catches regressions even when callers refactor heavily.

The fake-conn fixture pattern from Phase 5 (`Phase5FakeConnection`,
`_SharedQueueCursor`) is reused without modification. ✓

---

## 8. Documentation updates

Updated:
- Phase 9 section header in research.py with R-601..R-647 reference.
- New helper docstrings cite R-numbers.
- `_print_phase1_status` banner now references "Phase 9".
- `02_code_writer_response.md` records D1..D10 explicitly.

NOT updated (intentional):
- `program.md` — read-only per the Five-File Contract.
- `BUILD_PHASES.md` — the human owns the roadmap; updating it from
  research.py-side is a between-runs concern (matches Phase 7+8
  precedent).
- `README.md` — phase progress goes in commits.

✓

---

## 9. Commit plan

Commit 1: `phase9: snapshots and strategy memos (deterministic markdown rendering)`

Single commit covering:
- `research.py`: Phase 9 SQL constants, helpers (slug,
  markdown-escape, JSONB coercion, formatters), data fetch, render
  helpers (score breakdown / strategy fit / actionability /
  investment thesis), aggregation helpers, atomic write, public
  `generate_snapshot` and `generate_strategy_memo` functions, banner
  update.
- `tests/test_discovery.py`: 67 new tests across 14 classes;
  `Phase5FakeConnection` reused.
- `.gitignore`: single-line addition `rankings/*.md`.
- `reviews/11_phase9_snapshots_memos/`: 01_risk_review.md,
  02_code_writer_response.md, 03_reviewer_decision.md (this file).

The combined commit is a Phase 9 build commit. The Karpathy single-
variable-change discipline applies to *experiment-loop* commits on
`autoresearch/<tag>` branches, not to *build-phase* commits like this
one.

Push: `git push -u origin claude/identify-remaining-tasks-SVlWd` per
the branch instructions in the harness preamble.

No PR will be opened (the human did not request one).

---

## 10. Closing note

Phase 9 makes the agent's output legible. Before this push, the
metric (`actionable_pipeline_count`) was a number with no human-
readable backing — a parcel could be "actionable" but the team had no
way to read about it without querying the database directly. After
this push, every scored parcel produces a self-contained markdown
snapshot the team can scan in 60 seconds, and every market produces
a strategy memo that contextualizes the cycle's pipeline against the
program.md fit criteria.

Phase 10 (the overnight loop) is now genuinely unblocked: the loop
can score parcels, render snapshots, render the memo, commit on the
`autoresearch/<tag>` branch, and the human wakes up to a directory
of human-readable artifacts plus a git history of experiments.

The honest limitation: the snapshot's "Investment Thesis" is a
deterministic template, not an LLM-generated narrative. The team
will see specific data points (acreage, basis, vacancy, comps) but
not the kind of "smell test" prose a senior analyst would write.
That's the right scope for Phase 9 — fabrication-free and
deterministic — and Phase 11+ can layer an LLM synthesis pass on top
once the human has ratified the deterministic baseline.
