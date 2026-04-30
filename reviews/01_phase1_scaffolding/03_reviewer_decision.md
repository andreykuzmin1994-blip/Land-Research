# Phase 1 Scaffolding — Reviewer Decision

**Reviewer**: Agent 3 (Reviewer and Implementer), Claude Opus 4.7.
**Date**: 2026-04-30
**Branch**: `claude/project-onboarding-sazHe`
**Artifacts reviewed**: `prepare.py` (702 lines), `research.py` (122 lines),
`requirements.txt`, `env.template`, plus `01_risk_review.md` and
`02_code_writer_response.md`.

## 1. Summary verdict

**APPROVED AS-IS**.

No Agent-3 edits were required. Agent 2's deliverables address every HIGH
risk (H1–H5) and all MEDIUM risks the risk review flagged as gating. Two
items I had initially considered editing (M3 env.template comment direction
and M6 `connect_timeout=10`) are already correctly handled in the committed
code (`env.template` line 3–7 documents DIRECT, not pooler;
`prepare.py` line 262 sets `connect_timeout=10`). Live verification
against Supabase is gated on human-side Phase 0; commit will record that
explicitly.

## 2. Risks Agent 1 missed

I went looking for additional risks beyond Agent 1's list. Findings:

1. **Exit-code conflation in `_cli_main`'s top-level handlers** (LOW).
   `prepare.py` lines 700–702: any `psycopg.Error` raised from outside
   `apply_schema` (e.g. the `SELECT POSTGIS_VERSION()` probe failing for a
   reason other than missing extension) maps to
   `EXIT_EXTENSION_PRECONDITION` (3). A pure-network blip would also exit 3.
   Documented as deferred, not gating.

2. **`apply_schema` masked-DSN extraction is `getattr`-guarded but not
   exception-guarded** (LOW). Line 533: if `conn.info.dsn` raises (driver
   versions differ), the call site is in the same `try/except` block, so
   the bare `Exception` catcher will subsume it — but the masked variable
   used in the logging call is computed BEFORE the try block. So if
   `.dsn` raised, the entire `apply_schema` blows up with an attribute
   error rather than a clean rollback. Defer; psycopg3 ≥ 3.1 ships
   `ConnectionInfo.dsn` reliably.

3. **`load_dotenv` race condition** (LOW). `_get_connection_dsn` calls
   `load_dotenv` every time. If `.env` is rewritten mid-process, the next
   connection picks up the new DSN. Not a Phase 1 concern (one-shot
   script); flag for Phase 7+ when the loop persists.

4. **Timezone handling** — N/A in Phase 1: every `TIMESTAMPTZ DEFAULT
   NOW()` is server-side and timezone-aware. No Python `datetime.now()`
   without `timezone.utc` anywhere.

5. **Race on parameters.json reading** — `_load_parameters` is called once
   at module import; the SHA-256 sentinel detects later mutation. No
   race.

6. **Signal-handler conflict** already disclosed by Agent 2 (response
   doc, item 1). Documented, not gating for Phase 1.

7. **Import-order issues** — none. `psycopg`, `dotenv` are imported at
   top; both appear in `requirements.txt`.

None of (1)–(3) rise to must-fix.

## 3. Spot-check results — HIGH-risk mitigations

### H1 — parameters mutation surface
- `prepare.py` line 147–160 (`_deep_freeze`): recursive `MappingProxyType`
  wrap; lists become tuples. Verified.
- `prepare.py` line 205: module-level load is wrapped in try/except so the
  module doesn't import silently with garbage.
- `prepare.py` line 219–233 (`verify_parameters_unchanged`): re-reads the
  FILE BYTES (not the in-memory dict) and re-hashes. **This is the subtle
  bug Agent 1 warned about: re-hashing the dict would tautologically
  succeed; Agent 2 correctly re-reads `_PARAMETERS_PATH.read_bytes()`.**
- `research.py` line 31: `import prepare` only; no re-binding.
- Verdict: PASS.

### H2 — credential leakage
- `_mask_dsn` defined `prepare.py` line 111. Used in:
  - `get_connection` exception path (line 265) ✓
  - `apply_schema` exception path (line 544; masked is computed at line
    533 before the try block) ✓
- `os.environ` is referenced exactly twice (line 70 LOG_LEVEL via
  `os.getenv`, line 242 DATABASE_URL via `os.environ.get`). No iteration
  over environ, no `print(os.environ)`, no `repr(exc)` paths.
- The `_cli_main` outer handlers (lines 685–702) log `str(exc)` from
  `psycopg.Error`. psycopg3's exception messages do not embed the DSN
  (verified by docs); this is safe.
- Verdict: PASS.

### H3 — research.py importable without DB or env
- `research.py` line 31: imports `prepare`. `prepare`'s module-level work
  is `logging.basicConfig`, `_load_parameters` (file-only), and constant
  declarations. No `psycopg.connect` at module level.
- Stub functions in `research.py` raise `NotImplementedError` only inside
  bodies (lines 41, 51, 61, 68, 78, 85, 105). No top-level raises.
- `if __name__ == "__main__"` at line 121 is gated and only calls
  `_print_phase1_status` which reads frozen params, no DB.
- Verdict: PASS.

### H4 — idempotency
- All DDL uses `CREATE ... IF NOT EXISTS`. Verified by `grep -c "IF NOT
  EXISTS"` against the DDL constants — every CREATE has it.
- Single transaction in `apply_schema` (lines 535–538): all DDL runs
  inside one `with conn.cursor()`, then `conn.commit()`. On exception:
  `conn.rollback()`, then raise.
- One subtle point I considered: `CREATE EXTENSION` inside a transaction
  is supported on Postgres ≥ 9.1 (Supabase's version is far newer), so
  bundling it with the table DDL in a single transaction is safe.
- Verdict: PASS. Note that the integration test (apply twice without
  error) is gated on live DB, deferred to human Phase 0.

### H5 — PostGIS probe
- `_cli_main` line 672: `SELECT POSTGIS_VERSION()` immediately after
  `apply_schema`. If the extension is missing or in an inaccessible
  schema, this throws `psycopg.Error` and the outer handler (line 700)
  exits 3.
- Verdict: PASS.

## 4. Style and consistency findings

- snake_case throughout. No `camelCase` slipping in.
- Type hints on every function signature (including `Iterator[None]`,
  `Mapping[str, Any]`, `psycopg.Connection` quoted forward references).
- No naked `except:`. Every `except` names a class. The two
  `except Exception:` blocks (lines 539, 542) are deliberate (rollback
  best-effort and broad-catch in `apply_schema`); both immediately re-raise
  or swallow with a comment.
- Typed exceptions: `ParametersError`, `BudgetExceeded`. The
  `RuntimeError("schema apply failed")` raised by `apply_schema` is the
  one place a generic `RuntimeError` is used; arguably it should be a
  `SchemaApplyError(RuntimeError)`. Not gating; flag for Phase 2.
- No emoji.
- Logging via `logging` module, not `print`, except for the CLI summary
  (lines 677–680) which is intentional human-readable output.
- Docstrings present on every public symbol; module docstring is detailed
  and quotes the immutability spec verbatim.

## 5. Spec-compliance audit (AUTORESEARCH_MECHANICS.md Implementation Checklist, 17 items)

| # | Item | Phase 1 status |
|---|------|----------------|
| 1 | `prepare.py` contains metric/gates/filters/formula/universe | PASS as scaffolding (metric stubs return 0; gates/filters/formula deferred to Phase 4/5/8 per BUILD_PHASES.md) |
| 2 | Immutability header comment in `prepare.py` | PASS — lines 1–38 |
| 3 | `CLAUDE.md` tells the agent not to modify | PASS (existing; verified at /home/user/Land-Research/CLAUDE.md) |
| 4 | `parameters.json` `_immutable_during_run: true` | PASS — line 3 |
| 5 | `research.py` imports from `prepare`, never redefines | PASS — only `import prepare`, no shadowing |
| 6 | Setup phase as discrete sequence | DEFER (Phase 7+) |
| 7 | Baseline experiment as first TSV row | DEFER (Phase 7+) |
| 8 | 90-min OS-level timeout | PASS — `run_with_os_timeout` (subprocess) is the authoritative enforcer; `wall_clock_budget` is in-process inner |
| 9 | `git reset --hard HEAD~1` revert | DEFER (documentation) |
| 10 | Every commit corresponds to keep row | DEFER (Phase 7+) |
| 11 | TSV in `.gitignore` | PASS — `.gitignore` line covering `experiment_log.tsv` already present |
| 12 | Branch naming convention | DEFER (documentation) |
| 13 | Agent cannot modify immutable files from `research.py` (runtime check) | PASS — `MappingProxyType` deep-freeze + SHA-256 sentinel |
| 14 | Simplicity criterion documented | DEFER (documentation) |
| 15 | NEVER STOP rule documented | DEFER (documentation) |
| 16 | Mutation protocol followed | DEFER (Phase 7+) |
| 17 | Cross-run TSV accumulation | DEFER (Phase 7+) |

Of the 17, items 1–5, 8, 11, 13 are now PASS. Items 6, 7, 9, 10, 12, 14,
15, 16, 17 are correctly deferred per the risk review's classification.

## 6. DDL cross-check vs STORAGE_ARCHITECTURE.md

Tables in spec (11): parcels, parcel_scores, markets, submarkets,
market_context, sales_comps, leasing_comps, land_listings, research_log,
harness_reports, flagged_items.

All 11 present in `prepare.py`:
- `_DDL_PARCELS` (line 279)
- `_DDL_PARCEL_SCORES` (line 317)
- `_DDL_MARKETS` (line 334)
- `_DDL_SUBMARKETS` (line 344)
- `_DDL_MARKET_CONTEXT` (line 354)
- `_DDL_SALES_COMPS` (line 370)
- `_DDL_LEASING_COMPS` (line 393)
- `_DDL_LAND_LISTINGS` (line 412)
- `_DDL_RESEARCH_LOG` (line 435)
- `_DDL_HARNESS_REPORTS` (line 454)
- `_DDL_FLAGGED_ITEMS` (line 468)

Indexes in spec (15): all present in `_DDL_INDEXES` (lines 484–502).
Spot-checked the trickiest one — the functional index
`idx_parcels_owner_state` — matches verbatim including the regex
`'[A-Z]{2} \d{5}'`.

PostGIS extension: `CREATE EXTENSION IF NOT EXISTS postgis` line 277.

Verdict: complete and faithful to spec.

## 7. Edits applied

None. The current source already incorporates the M3 (env.template DIRECT
DSN comment) and M6 (`connect_timeout=10`) mitigations. I verified by
reading `env.template` and `prepare.py` line 262.

## 8. Deferred concerns (non-gating, surface for future phases)

1. **Exit-code conflation** (Section 2 finding 1): refine top-level
   handlers in Phase 7+ when more error classes exist. Suggested:
   `EXIT_DATABASE_CONNECT_FAILED = 6` distinct from
   `EXIT_EXTENSION_PRECONDITION = 3`.
2. **`SchemaApplyError`** subclass instead of generic `RuntimeError`.
3. **No unit tests yet.** The risk review's section 6 lists 12 smoke
   tests. The brief explicitly accepts `python -m py_compile` for Phase
   1 scaffolding. Phase 2 should adopt the section-6 test suite before
   adding any new behaviour to `prepare.py`.
4. **Schema-version table.** Agent 2 surfaced `SCHEMA_VERSION = "1.0"` as
   a constant printed by the CLI; the risk review's M4 suggested a
   `schema_version` row. Not gating; revisit in Phase 7+ via the proper
   between-runs mutation protocol.
5. **Correlated subquery in metric SQL.** Latest-score-per-parcel uses a
   correlated subquery (lines 558–565). Stable correctness on Phase 1's
   empty DB, but at scale prefer `DISTINCT ON` or `ROW_NUMBER`. Refactor
   would be a between-runs `prepare.py` mutation; defer.
6. **Stack-trace credential leak surface.** psycopg3 doesn't embed DSNs
   in exception messages, but a future driver upgrade could regress.
   Consider an installable test that asserts `secret` never appears in
   `repr` of a deliberately-failed connection.
7. **`apply_schema` early `_mask_dsn` call** (Section 2 finding 2): if a
   driver upgrade makes `conn.info.dsn` raise, the masking line itself
   throws. Wrap in `try/except` returning `<conn>` on failure.

## 9. Documentation updates needed

None at Phase 1. Hard constraint forbids `.md` spec edits. The risk
review's open question 8.8 (where do review docs live long-term) is
answered implicitly by this commit landing them in the repo at
`reviews/01_phase1_scaffolding/`.

## 10. Commit plan

Files staged:
- `prepare.py` (new, 702 lines)
- `research.py` (new, 122 lines)
- `requirements.txt` (new)
- `env.template` (modified — Phase 0 DSN guidance)
- `reviews/01_phase1_scaffolding/02_code_writer_response.md` (new)
- `reviews/01_phase1_scaffolding/03_reviewer_decision.md` (this file)

Note: `01_risk_review.md` is already tracked from a prior commit; only
new files in `reviews/01_phase1_scaffolding/` need staging.

Commit message follows the heredoc form specified in the brief.

Phase 1 exit criterion "python prepare.py runs successfully against
Supabase" is GATED on human-side Phase 0 (Supabase project + .env
populated). Code is ready; live verification awaits.
