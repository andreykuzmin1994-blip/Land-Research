# Phase 9 Risk and Architecture Review — Snapshots and Strategy Memos

**Reviewer:** Agent 1 role, completed by orchestrator (Claude Code main
session) under explicit human authorization ("Proceed with Phase 9",
2026-05-04). Following the established orchestrator-inline precedent
from Phases 2/3/3.1/5/7+8 — sub-agent streaming has timed out
consistently in this environment, so the orchestrator authors all three
role documents and a future session with working sub-agent streaming
should ratify them with full context independence.

**Date:** 2026-05-04.
**Branch:** `claude/identify-remaining-tasks-SVlWd`.
**Base commit:** `f60528c` (Phase 7+8 combined — scoring complete +
4-gate actionability + 5-strategy fit).
**Scope:** BUILD_PHASES.md Phase 9 — implement `generate_snapshot` and
`generate_strategy_memo` in `research.py`, currently `NotImplementedError`
stubs at research.py:4084 and 4091. Render program.md's per-parcel
snapshot template (program.md L408-L524) and per-market strategy memo
template (program.md L757-L807) from the live database. Phase 10 (the
overnight loop) will consume these as the team's only narrative outputs.

---

## 1. The Five-File Contract

Phase 9 edits ONLY `research.py`, `tests/test_discovery.py`, and
`.gitignore`. The metric layer (`prepare.py`) and the configuration
layer (`parameters.json`, `sources.json`) stay bytes-identical to
`f60528c`. `program.md` is the spec we render against — read-only.

The `.gitignore` change is the single mutation outside research.py /
tests. Today's `.gitignore` lists `snapshots/*.md` (correct — gitignored
artifacts of a cycle) and `rankings/*.json` (the ranked-shortlist JSON,
also correct) but does NOT list `rankings/*.md`, even though program.md
L57 puts the strategy memo at `rankings/{market_id}_strategy_memo.md`.
Memos are per-cycle artifacts, the same way snapshots are — they should
not pollute the commit history. Adding `rankings/*.md` to `.gitignore`
is a Phase 9 prerequisite. This is not a `prepare.py` mutation and does
not invalidate the experiment log.

**Hard rule for Agent 2**: every diff against `f60528c` for `prepare.py`,
`parameters.json`, `sources.json`, `program.md`, `connector_harness.py`,
`connector_registry.json`, `requirements.txt`, and `connector_harness.py`
MUST be empty. The only allowed file-system mutations are:

- `research.py` — append two implementations and supporting helpers
- `tests/test_discovery.py` — append new test classes
- `.gitignore` — single line addition: `rankings/*.md`
- `reviews/11_phase9_snapshots_memos/` — three role documents (Agent
  1/2/3)

Agent 3 verifies as Gate 1.

---

## 2. The Metric Contract — UNCHANGED in Phase 9

`prepare.calculate_actionable_pipeline_count` (prepare.py:568-583)
counts parcels where `actionability='PASS'` AND `composite_score>=70`
AND latest score row. **Phase 9 does not change the metric. It does not
change parcel_scores. It does not change any input to the metric
calculation.** Phase 9 reads the database and writes markdown to disk.
Nothing else.

The risk that has been flagged repeatedly across prior phases — "the
agent silently relaxes the metric to make output look better" — is
nearly impossible to trigger from Phase 9 because Phase 9 has no path
back into `parcel_scores`. The two functions are read-only against the
database. Mitigation:

- **R-601 (CRITICAL)** — Agent 2 must NOT write any SQL `INSERT`,
  `UPDATE`, `DELETE`, or `UPSERT` to `parcel_scores`, `research_log`, or
  any other immutable-during-run target from inside `generate_snapshot`
  or `generate_strategy_memo`. The functions are PURE READS plus a file
  write. Verified by Agent 3 via grep against the new code.
- **R-602 (HIGH)** — Agent 2 must NOT call `score_parcel`,
  `run_scoring_cycle`, or any other write-path helper from inside the
  Phase 9 functions. If a snapshot is requested for an unscored parcel,
  the function returns a clear error or skip — it does not trigger
  scoring as a side-effect. (Phase 10's experiment loop is what calls
  scoring then snapshots, in that order; Phase 9 just renders.)

---

## 3. Risk Catalog

Risks are numbered R-601 .. R-647 (continuing from R-501..R-545 used in
Phase 7+8). Severity: CRITICAL / HIGH / MEDIUM / LOW.

### 3.1 Five-File Contract integrity (R-601 .. R-605)

**R-601 (CRITICAL) — No writes to parcel_scores from Phase 9.** See §2.

**R-602 (HIGH) — No transitive scoring from Phase 9.** See §2.

**R-603 (MEDIUM) — `.gitignore` mutation is documented.** Adding
`rankings/*.md` is the only file-system mutation outside research.py /
tests. The risk is the line silently appearing in a future PR diff and
being misread as scope creep. Mitigation: explicit one-line entry under
the existing "Agent runtime artifacts" block, with no other changes.

**R-604 (LOW) — Test fixtures don't accidentally commit real CoStar
data.** Phase 9 tests use synthetic parcel/score/market_context
fixtures already established in Phase 5/7/8 (Phase5FakeConnection
pattern). No new fixture files are required. Mitigation: re-use the
existing fakes; no new files under `tests/fixtures/`.

**R-605 (LOW) — Prevent accidental insertion of `prepare.py`-style
helpers.** Agent 2 may be tempted to factor "fetch latest score by
parcel" into a helper inside prepare.py. That's a `prepare.py`
mutation. Mitigation: any helper Phase 9 needs lives in research.py
even if it duplicates a small bit of SQL from prepare.py's
`_LATEST_SCORE_WHERE`. Duplication is acceptable; mutation is not.

### 3.2 SQL safety and schema fidelity (R-606 .. R-614)

**R-606 (CRITICAL) — No string-interpolated SQL.** Phase 9's queries
are non-trivial (joins across parcels / parcel_scores / submarkets /
market_context / flagged_items). The `TestStaticChecks.test_no_string_
interpolated_sql` AST scanner walks every `cursor.execute(...)` call
site and asserts the first arg is a `Constant` / `Name` / `Attribute`.
Mitigation: every Phase 9 SQL is a module-level string constant with
`%s` placeholders; Agent 2 adds `_SQL_FETCH_PARCEL_FOR_SNAPSHOT`,
`_SQL_FETCH_LATEST_SCORE`, `_SQL_FETCH_MARKET_CONTEXT_FOR_SNAPSHOT`,
`_SQL_FETCH_OPEN_FLAGS_FOR_PARCEL`, `_SQL_FETCH_NEARBY_SALES_COMPS`,
`_SQL_FETCH_SCORED_PARCELS_FOR_MEMO`,
`_SQL_FETCH_LATEST_SCORING_CYCLE_FOR_MEMO`,
`_SQL_FETCH_RECENT_FLAGS_FOR_MARKET` and a tightly bounded set of
related constants. The new
`TestPhase9SqlConstantsStaticChecks.test_no_string_interpolation`
explicitly asserts no `{` braces (no f-strings) in any Phase 9 SQL
constant.

**R-607 (HIGH) — Schema column drift.** The snapshot reads ~25 columns
from `parcels` (address, county, owner_name, owner_mailing_address,
owner_type_inferred, acreage, zoning, zoning_description,
land_use_code, assessed_value_total, last_sale_date, last_sale_price,
discovery_source, etc.) plus ~9 columns from `parcel_scores`
(composite_score, confidence_score, actionability,
actionability_blockers, sub_scores, strategy_fit, primary_strategy,
notes, scored_at) plus ~6 from `market_context` (vacancy_rate_pct,
net_absorption_t12_sf, under_construction_sf, asking_rent_nnn_psf,
as_of_date, source). If the DDL drops or renames a column the SELECT
breaks at runtime. Mitigation: Agent 2 names every column explicitly in
the SELECT list (no `SELECT *`); Agent 3 cross-checks each column
against the DDL in prepare.py during code review.

**R-608 (HIGH) — Latest-score semantics must match the metric.**
prepare.py's metric query uses `scored_at = MAX(scored_at) WHERE
parcel_id = ps.parcel_id` (prepare.py:_LATEST_SCORE_WHERE). The
snapshot must use the SAME predicate so that what the metric counts and
what the snapshot describes are the same row. Mitigation:
`_SQL_FETCH_LATEST_SCORE` for snapshots reuses the same `ORDER BY
scored_at DESC LIMIT 1` shape; new test asserts that against a
multi-row fixture (older PENDING + newer PASS) the snapshot describes
the PASS row.

**R-609 (HIGH) — JSONB type handling across psycopg vs. fakes.** Real
psycopg returns `JSONB` columns as Python dicts. The Phase 5/7/8 fake
connections store JSON as strings (because the fakes don't decode
JSON). The Phase 7+8 score_parcel persists `actionability_blockers`,
`sub_scores`, and `strategy_fit` as `json.dumps(...)` strings; the fake
test reads them back as the same string. Phase 9 must accept either
shape. Mitigation: a `_coerce_json(value) -> dict` helper that returns
`value` if already a dict, otherwise `json.loads(value)`. Tested both
paths.

**R-610 (HIGH) — Decimal and BIGINT handling in Markdown.**
`composite_score`, `confidence_score`, `acreage`, `vacancy_rate_pct`,
`net_absorption_t12_sf`, `assessed_value_total`, `last_sale_price`
return as `Decimal`, `int`, or `float` from psycopg. Naive `f"{x:.1f}"`
on a `Decimal` works in Python 3.10+, but `f"{x:,}"` on a `Decimal`
also works. The risk is mixing types in arithmetic (Decimal + float
TypeError). Mitigation: explicit `_to_float(v) -> float | None` and
`_to_int(v) -> int | None` coercers at the data-fetch boundary; all
downstream rendering operates on float/int.

**R-611 (MEDIUM) — Submarket name vs. submarket_id rendering.** The
parcels table stores `submarket` (a free-text submarket label like
"South Fulton") and `parcel_scores` references it through whatever
identifier `_compute_market_context_scores` uses. The submarkets
reference table holds `submarket_name` keyed by `submarket_id`.
program.md's snapshot uses the human-readable submarket name. The risk
is the snapshot renders the raw lowercase id ("south_fulton") instead
of the pretty name ("South Fulton"). Mitigation: prefer the value
joined from the submarkets table when available; fall back to the
parcels.submarket text; final fallback "—". Tested in
`TestPhase9SnapshotRender.test_submarket_name_resolution`.

**R-612 (MEDIUM) — market_context staleness leaking into the
snapshot.** If the latest market_context row for a submarket is >30
days old, the snapshot must render it WITH a staleness note rather than
silently quote stale numbers as fresh. Mitigation: the rendered
"Submarket vacancy / absorption / rent" lines include an "(as of
YYYY-MM-DD, N days)" suffix when the row exists; if the row is older
than `_MARKET_CONTEXT_STALENESS_DAYS` (already defined at
research.py:1838), an explicit "stale — refresh recommended" flag is
appended. Same constant used by the scoring path so behavior is
consistent.

**R-613 (MEDIUM) — Memo top-10 ordering must be deterministic.**
program.md L789 calls for "Top 10 highlights." The natural ordering is
`composite_score DESC, scored_at DESC` (newest first as tiebreaker).
The risk is non-deterministic ordering across DB backends or a
Decimal-vs-float comparison surprise. Mitigation: explicit `ORDER BY`
in `_SQL_FETCH_SCORED_PARCELS_FOR_MEMO` with both columns named, and a
test that asserts the top-10 ordering against a fixed fixture.

**R-614 (LOW) — research_log read for memo context.** The memo
references the cycle's discovery / scoring activity. Reading from
`research_log` is fine (read-only) but the column types and ordering
must be respected. Mitigation: separate SQL constant
`_SQL_FETCH_RESEARCH_LOG_FOR_MEMO` with an explicit `ORDER BY timestamp
DESC LIMIT n` to bound result size.

### 3.3 Filesystem and path safety (R-615 .. R-621)

**R-615 (CRITICAL) — Path traversal via parcel_id or market name.**
The functions write to `snapshots/{parcel_id}_snapshot.md` and
`rankings/{market_id}_strategy_memo.md`. Real parcel_ids look like
`fulton-14-0123-LL-045-8` (safe). But database state is not the only
input — a future Phase 11+ county connector could insert parcel_ids
containing `..`, `/`, `\\`, NUL, or whitespace. Mitigation: a
`_safe_filename_slug(s) -> str` helper that asserts the input matches
`^[A-Za-z0-9._\-]+$` and otherwise raises `ValueError`. Both functions
gate the filename through this helper before opening any file. The
`market` argument is also routed through it (markets like "atlanta",
"dallas-fort-worth" are fine; the helper still asserts).

**R-616 (HIGH) — Output directory creation race.** The `snapshots/`
and `rankings/` directories may not exist on a fresh clone. Mitigation:
`Path(output_dir).mkdir(parents=True, exist_ok=True)` before any file
write. The `output_dir` parameter is overridable for tests
(default-resolves to repo-root `snapshots/` or `rankings/`).

**R-617 (HIGH) — Atomic write to avoid half-written files.** A snapshot
or memo half-written when the agent is killed mid-cycle leaves a
corrupt artifact in the directory. Mitigation: write to
`{path}.tmp.{pid}.{cycle_id?}` then `os.replace(...)` to the final
path. `os.replace` is atomic on POSIX. Tested by simulating a partial
write and verifying the final file is either complete or absent (no
intermediate state visible to a reader).

**R-618 (MEDIUM) — Idempotent re-runs.** Re-running `generate_snapshot`
for the same parcel must overwrite cleanly without leaving stale
content. Atomic write per R-617 already covers this; an explicit test
asserts that running twice produces the same output (and the second
run's bytes equal the first run's bytes against the same DB state).

**R-619 (MEDIUM) — Default output dir resolution.** The default output
dir resolves relative to the repo root (the directory containing
`prepare.py`), not `os.getcwd()`. The risk is a Phase 10 loop run from
a subdirectory writing snapshots to the wrong place. Mitigation: a
module-level `_REPO_ROOT = Path(__file__).resolve().parent` constant;
default `snapshots/` and `rankings/` are resolved against it.

**R-620 (LOW) — Filename casing on case-insensitive filesystems.** A
parcel_id slug that differs only in case from an existing file collides
on macOS / Windows. Real parcel_ids in our connectors are all-lowercase
so this is a hypothetical risk; mitigation is to normalize the slug to
lowercase before filename use.

**R-621 (LOW) — `.gitignore` correctness.** `rankings/*.md` correctly
excludes `rankings/atl_strategy_memo.md` but does not exclude any
existing committed `.md` (none exist today). Mitigation: verify with
`git status` before commit that no committed `.md` files in `rankings/`
become untracked.

### 3.4 Markdown rendering and template fidelity (R-622 .. R-635)

**R-622 (HIGH) — Markdown table cell escaping.** Owner names, owner
mailing addresses, broker names, and notes can contain `|`, `\n`, or
backticks. `|` will break Markdown tables; `\n` will break table rows;
backticks can spawn unintended code spans. Mitigation: a
`_md_table_cell(s) -> str` helper that replaces `|` with `\|`, replaces
internal whitespace runs (including `\r`, `\n`, `\t`) with single
spaces, strips backticks, and caps length at 120 chars (with `…`
ellipsis suffix). Applied at every table cell boundary.

**R-623 (HIGH) — NULL handling everywhere.** Many Phase 9 fields are
nullable in the DB (zoning_description, land_use_code, asking_price,
last_sale_price, mc.asking_rent_nnn_psf, etc.). Rendering `None` as
the string `"None"` is a quality bug. Mitigation: a `_md_cell(value,
default="—")` helper used at every leaf rendering site; explicitly
tested with all-None inputs producing a snapshot with no `"None"`
substrings.

**R-624 (HIGH) — Investment thesis is templated, not LLM-generated.**
program.md L416 describes a "2-4 paragraph narrative." The temptation
is to call an LLM (Claude API or otherwise) to generate the text. That
introduces an external dependency, a non-deterministic output, an API
budget concern, and a metric-integrity risk (if the LLM ever cites
data not in the database, the snapshot lies). Mitigation: the thesis
is a fully deterministic templated narrative composed of conditional
clauses driven by actual data points (acreage, owner type, mismatched
use signals derived from owner_type_inferred + owner_mailing_address,
strategy fit ratings, basis vs. comps, vacancy/absorption). No LLM
call. Phase 11+ could add an LLM-thesis-rewrite pass IF the human
explicitly opts into it via parameters.json — Phase 9 punts cleanly.

**R-625 (HIGH) — Specificity over generic language.** program.md
L426-L427 explicitly forbids generic language like "strong market
fundamentals" without backing data. The thesis must cite specific
numbers (vacancy %, basis $/acre, acreage, comp counts). Mitigation:
the templated clauses are written as data-driven sentences with f-
string interpolation of actual fetched values; if a value is null, the
clause is omitted rather than rendered as a vague placeholder. Tested
in `TestPhase9ThesisSpecificity.test_no_generic_phrases` — asserts the
rendered thesis does NOT contain a fixed list of banned phrases
("strong fundamentals," "good location," "favorable market," etc.)
when the underlying data is null, instead omitting the clause entirely.

**R-626 (HIGH) — Score breakdown table covers all 12 sub-scores.**
program.md L499-L506 calls for a table listing every sub-score with
weight + weighted contribution. With Phase 7+8, only S2/S4/S5/S6/S8/S9-
stub/S10 are populated; S1/S3/S7/S11/S12 are null. The table must
render all 12 with the null ones marked clearly ("not yet wired" or
"—") and the weighted-contribution column showing 0 for nulls.
Mitigation: iterate `_SUB_SCORE_NAMES` (research.py:1611) directly so
no sub-score can be omitted; weighted contribution computed from
`scoring_weights` in parameters.json, with null sub-scores contributing
0.

**R-627 (HIGH) — Strategy fit table uses STRONG/MODERATE/WEAK/N/A
verbatim.** The strategy_fit JSONB has these exact tokens (Phase 7+8
contract). The table column must render them as-is; the rationale
column needs deterministic text per (strategy, rating). Mitigation: a
`_STRATEGY_RATIONALES` dict keyed by `(strategy, rating)` with one
sentence each; rendered directly. Sentence text references program.md
section names so a future reviewer can trace it back.

**R-628 (MEDIUM) — Actionability table mirrors the gate definitions.**
program.md L508-L515 shows four rows: control, entitlement, viable
strategy, deal-killers. The verdict (`actionability` enum) maps to one
PASS row + zero or one FAIL row(s); the rest are PASS or PENDING.
Mitigation: the table is rendered from `_run_actionability_screen`'s
output dict (already exposed at research.py:_run_actionability_screen)
plus the `actionability_blockers` JSONB. First-failing-gate-wins
(R-534) means at most one FAIL is rendered.

**R-629 (MEDIUM) — Recommendation field is a fixed enum + rationale.**
program.md L520-L523 calls for `PURSUE / MONITOR / PASS`. The mapping:
- `actionability='PASS'` AND `composite_score >= threshold` → PURSUE
- `composite_score >= threshold` AND `actionability != 'PASS'` →
  MONITOR (with the specific blocker that would unblock it)
- `composite_score < threshold` → PASS (do not pursue this cycle)
This is deterministic and lives in a small helper
`_compute_recommendation(score, actionability, blockers, ...)`.
Tested.

**R-630 (MEDIUM) — Memo "What I learned this cycle" without LLM.**
program.md L774-L779 example learnings ("absentee-owned parcels scored
18% higher on average...") are pattern-matched insights an LLM might
generate. Phase 9 cannot do that without an LLM. Mitigation: the memo
exposes RAW AGGREGATES — counts, averages, distributions — and labels
the section "Pipeline observations" instead of "Learnings." The
template prefaces with: "Phase 9 emits aggregate observations only;
narrative learnings will be added in a future phase that wires an LLM
synthesis pass." This is honest about scope and matches program.md
L820 ("MUST be honest about limitations").

**R-631 (MEDIUM) — Memo "Recommended adjustments for next cycle"
without LLM.** Same constraint as R-630 — those are LLM-style
recommendations. Mitigation: emit a small set of DATA-DRIVEN
candidates (e.g., "X parcels rejected for entitlement gate; consider
relaxing entitlement_min_score in parameters.json next cycle if this
pattern persists" — derived from `actionability='FAIL:entitlement'`
counts). Each recommendation is gated by a count threshold so the
section doesn't suggest changes off noise.

**R-632 (LOW) — Newline normalization for cross-platform.** Markdown
files written on different OS may have CRLF vs LF endings. Mitigation:
write text files in binary mode with explicit `\n`. Existing
research.py file writes (e.g., harness reports) use the same
convention; reuse.

**R-633 (LOW) — Memo total length cap.** program.md L821 says memos
must be readable in 5 minutes. A market with 200 actionable parcels
could blow past that. Mitigation: top-10 highlights are capped at 10;
the Pipeline Composition section is one paragraph; raw aggregates are
table-format not prose. Total memo target: <= 500 lines / ~5000 words.
Soft cap, not enforced; visible in the test.

**R-634 (LOW) — Snapshot total length cap.** Snapshots must fit on
"one page" per program.md L408 — but the template is structured enough
that it'll render to ~2-3 screen-pages. Acceptable. Mitigation: none
required; document the deviation in Agent 2 response.

**R-635 (LOW) — Empty pipeline memo still renders.** A market with
zero scored parcels in the cycle must still produce a memo with "no
actionable pipeline this cycle" rather than crashing or emitting a
zero-byte file. Tested.

### 3.5 Data fidelity and integrity (R-636 .. R-640)

**R-636 (CRITICAL) — Never fabricate data (program.md L736).** Every
field in the snapshot must trace back to a column in the database or
to a deterministic computation over those columns. Mitigation: at
no point does the rendering code write a hard-coded guess for a
nullable field. If a field is null, the snapshot says "—" or "not yet
wired." Tested in `TestPhase9NoFabrication`.

**R-637 (HIGH) — Comp citations must be real.** The thesis paragraphs
may reference comparable transactions ("comparable land parcel within
1 mi sold in last 12 months at $X/acre"). The function must NOT make
up a comp; it must read `sales_comps` and only cite a comp that was
fetched. Mitigation: the comp clause is fully gated on the
`_SQL_FETCH_NEARBY_SALES_COMPS` result returning at least one row;
when zero rows return the clause is omitted and a "no nearby comps"
parenthetical is rendered.

**R-638 (HIGH) — Mismatched-use-signal count is data-derived.**
program.md L304-L323 lists eight signals. Phase 9 cannot evaluate all
of them (some require Phase 11+ data — surrounded-by-development needs
adjacency analysis; recent-nearby-transaction is partial). Mitigation:
the snapshot reports the count of signals that ARE wired (absentee
owner, estate/trust ownership) and explicitly labels the others as
"signal not yet wired." Test covers the explicit-label case.

**R-639 (MEDIUM) — Owner-type inference reuse.** program.md L256
inference ladder (trust / estate / LLC / corporate / government /
individual). The parcels table stores the inferred type as
`owner_type_inferred` (Phase 3 / Phase 6 already populate this).
Phase 9 reads it; does NOT re-infer. If the column is null, the
snapshot says "(not classified)" and notes the open item.

**R-640 (LOW) — Coordinates in WKT/EWKT.** parcels.geometry and
parcels.centroid are PostGIS columns. The snapshot uses lat/lng only —
returned as plain floats from `ST_Y(centroid), ST_X(centroid)` in
existing scoring SQL (research.py:_SQL_FETCH_PARCEL). Reuse same
extraction; do NOT try to parse raw WKT in Phase 9.

### 3.6 Test coverage and AST checks (R-641 .. R-647)

**R-641 (CRITICAL) — Existing 300 tests must still pass.** Phase 9
must not break any prior phase. Mitigation: pre-flight runs the full
suite before any Phase 9 code lands, then runs it again after; both
must show 300+ passing.

**R-642 (HIGH) — New SQL constants pass the AST scanner.** All Phase 9
SQL constants must be module-level string literals, no f-strings, no
.format(), no string concatenation that includes user input.
`TestStaticChecks.test_no_string_interpolated_sql` already walks every
`cursor.execute()` call site; new
`TestPhase9SqlConstantsStaticChecks.test_no_string_interpolation`
explicitly asserts no `{` braces in the new constants.

**R-643 (HIGH) — Render path tests use Phase5FakeConnection.** The
existing `Phase5FakeConnection` / `_SharedQueueCursor` pattern
(tests/test_discovery.py:1083-1153) supports sequenced fetchone /
fetchall queues across multiple cursors. Phase 9 reuses it without
modification. Snapshot tests load a parcel + score + market_context
+ flags fixture into the queues, then call `generate_snapshot` with
`output_dir=tmp_path`, then read the resulting file and assert section
headers + selected data points. Memo tests follow the same shape.

**R-644 (HIGH) — File-IO isolation in tests.** All Phase 9 tests pass
`output_dir=tmp_path` (a unittest-friendly tmpdir) so no test leaves
artifacts in `snapshots/` or `rankings/`. CI catches any regression
via `git status --porcelain` after the test run.

**R-645 (MEDIUM) — Test count target.** Phase 9 adds approximately 30
new tests across ~10 test classes. Target classes:
- `TestPhase9SafeFilenameSlug` (path safety helper)
- `TestPhase9MarkdownEscaping` (table cell escaping)
- `TestPhase9CoerceJson` (string-or-dict JSONB coercion)
- `TestPhase9SnapshotRender` (per-section render assertions)
- `TestPhase9SnapshotEndToEnd` (full happy + sad paths)
- `TestPhase9MemoAggregates` (counts, top-10 ordering)
- `TestPhase9MemoRender` (per-section render assertions)
- `TestPhase9MemoEmptyMarket` (zero-pipeline memo)
- `TestPhase9NoFabrication` (no fake data when nulls)
- `TestPhase9SqlConstantsStaticChecks` (AST-style guards)

**R-646 (MEDIUM) — Test for "this is not a database write."** Verify
snapshot/memo rendering never appears in `_all_executes` as an INSERT
/ UPDATE / DELETE. Mitigation: `TestPhase9NoDatabaseWrites.test_
generate_snapshot_makes_no_writes` parses every recorded SQL call's
first token and asserts it is `SELECT` (or `WITH`). Same for memo.

**R-647 (LOW) — Test for `.gitignore` line presence.** A simple test
that reads `.gitignore` and asserts `rankings/*.md` is present, so a
future refactor doesn't accidentally drop the line.

---

## 4. Architecture decisions Agent 2 must commit to

These are high-stakes calls that should be documented in
`02_code_writer_response.md` so a future reviewer can audit them
quickly.

**D1 — Both functions return `Path`, not markdown text.** The stub
signature returns `str`. Phase 9's contract is "render and write a
file." The most useful return value is the resolved `Path`. The `str`
return type accepts a `Path` via `os.fspath()` if a caller insists.

**D2 — Both functions accept `output_dir` for test isolation.** Default
to `<repo_root>/snapshots` and `<repo_root>/rankings`. Test code passes
`tmp_path` to avoid polluting the repo working tree.

**D3 — Snapshot is generated for any parcel with a `parcel_scores`
row** — actionable, qualified-not-actionable, AND below-threshold
parcels. program.md L416 says "for both actionable and
qualified_not_actionable — the team may override." For below-threshold
the recommendation is PASS with the "below-threshold" rationale.
Rationale: the snapshot is a human-readable record of what the agent
found and why; the team needs to see the negative evidence too. (Phase
10 will likely only emit snapshots for >=threshold parcels in the
overnight loop — that's a Phase 10 caller-side filter, not a Phase 9
constraint.)

**D4 — Memo always renders even with zero scored parcels.** The "no
pipeline this cycle" memo is itself useful information — it tells the
team a corridor was searched and produced nothing, which informs the
next cycle's prioritization.

**D5 — Memo's "current cycle" definition.** When `cycle_id=None` is
passed, the memo selects the most recent `cycle_id` from
`research_log` for the given market. When an explicit `cycle_id` is
passed, the memo uses that. Tested both.

**D6 — Investment thesis is a deterministic template.** No LLM call.
Each clause is gated on actual data presence. Documented limitation;
Phase 11+ could add an LLM rewrite step.

**D7 — Strategy rationale text is a `(strategy, rating) -> str` table.**
Not LLM-generated. Each entry is one sentence, traceable to the
program.md fit criteria for that strategy.

**D8 — Recommendation is a deterministic enum.** PURSUE / MONITOR /
PASS computed from (composite_score, actionability) per R-629.

**D9 — `score_breakdown` table iterates `_SUB_SCORE_NAMES`** so all 12
rows always render, with null sub-scores marked.

**D10 — Atomic write via `os.replace`.** Half-written files are
prevented by writing to a `.tmp.{pid}` sibling and renaming.

---

## 5. Go/no-go gates for Agent 3

Agent 3 verifies these BEFORE approving the merge:

1. **Five-File Contract intact** — `git diff f60528c -- prepare.py
   parameters.json sources.json program.md connector_harness.py
   connector_registry.json requirements.txt` is empty.
2. **Phase 9 functions implemented** — `generate_snapshot` and
   `generate_strategy_memo` no longer raise `NotImplementedError`.
3. **No write-path SQL in Phase 9** — grep `INSERT|UPDATE|DELETE|UPSERT`
   inside the new code returns nothing in the function bodies (only in
   shared module-level constants from earlier phases).
4. **AST scanner still green** — `TestStaticChecks.test_no_string_
   interpolated_sql` passes against the new SQL constants.
5. **All 300 prior tests pass** — `python -m pytest tests/test_discovery.py`
   shows 300 + N_new_tests passing.
6. **New tests count ~30** — Agent 2 adds at least 25 new tests across
   the ~10 classes listed in R-645.
7. **No file artifacts left in the working tree** — `git status` after
   running the full test suite shows no untracked files in `snapshots/`
   or `rankings/`.
8. **`.gitignore` updated** — `rankings/*.md` line present, with no
   other changes.
9. **Composite score arithmetic preserved** — Phase 9 does NOT alter
   `_compute_composite`, `_compute_confidence`, or any sub-score
   helper.
10. **Idempotency** — running `generate_snapshot` twice for the same
    parcel produces byte-identical output against the same DB state
    (test).
11. **Path traversal** — `_safe_filename_slug` raises `ValueError` on
    `..`, `/`, NUL, and whitespace inputs (test).
12. **Snapshot describes the latest score row** — multi-row score
    fixture test confirms.

If any gate fails, Agent 3 rejects and writes the rejection note;
Agent 2 fixes; Agent 3 re-runs gates.

---

## 6. What Phase 9 explicitly does NOT do

These are out of scope and must NOT land in this push:

- LLM-driven thesis or learnings (R-624, R-630).
- Per-parcel comp adjacency analysis beyond simple submarket+date
  filter (deferred to Phase 11+).
- Multi-parcel assemblage scanning across the market (deferred per
  R-540 from Phase 7+8).
- Snapshot-format experimentation — the template is program.md
  L411-L524 verbatim section ordering.
- Memo experimentation — same, program.md L757-L807 verbatim section
  ordering.
- Any change to the metric calculation or scoring path. Phase 9 reads;
  it does not score.
- Phase 10 experiment loop. Phase 10 calls these functions; it is not
  in this push.

---

## 7. Closing note

Phase 9 is the simplest phase since Phase 1 in terms of new logic — no
new SQL writes, no metric impact, no scoring math. The primary risks
are (a) accidentally fabricating data the team will trust, (b) path
traversal via filename slug, and (c) snapshot/memo "drifting" from
program.md's spec because the spec is detailed and 100+ fields wide.
Mitigations against all three are concrete tests, not just careful
review.

The harder risk is the philosophical one: program.md's snapshot
template implies a level of analysis (development feasibility model,
yield-on-cost calc, comp adjacency analysis) that Phase 9 cannot
deliver from the data we have today. The honest path is to render
those fields with explicit "—" or "not yet wired" markers so the team
sees the gap rather than reading a fabricated number. Phase 11+ closes
the gaps; Phase 9 doesn't pretend to.
