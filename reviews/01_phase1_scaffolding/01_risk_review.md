# Phase 1 Scaffolding — Risk and Architecture Review

**Reviewer**: Agent 1 (Risk and Architecture Reviewer), Claude Opus 4.7, read-only role.
**Date**: 2026-04-30
**Artifact under review**: Phase 1 scaffolding deliverables — `prepare.py`, `research.py`, `env.template`, `requirements.txt`.
**Spec basis**: `AUTORESEARCH_MECHANICS.md` (Five-File Contract, The Metric, Implementation Checklist), `STORAGE_ARCHITECTURE.md`, `BUILD_PHASES.md` (Phase 1), `parameters.json`, `appendix_a_county_connectors.md` (Three-Agent Code Team).

This document is the deliverable. Agent 2 must read it before writing code; Agent 3 will use it as the rubric for accepting or rejecting Agent 2's implementation.

Out of scope: scoring logic (Phase 5), connector code (Phase 3), CoStar ingestion (Phase 6), AI fallback (later phase), the experiment loop (Phase 7+). Any drift from Phase 1 scope is itself a risk and is called out below.

---

## 1. Failure modes of `prepare.py`

`prepare.py` is the Karpathy-immutable measurement layer. The integrity of every future experiment in this branch depends on it being correct, idempotent, and not silently lying about success. The following failure modes must each be addressed in code or explicitly accepted with rationale.

### 1.1 Malformed `parameters.json`

**Failure modes**:
- File missing entirely (developer cloned repo without it, or `.gitignore` accident).
- File present but JSON-invalid (trailing comma, unterminated string after manual edit between runs).
- File present and JSON-valid but schema-incomplete: a key like `scoring_weights.S1_interstate_proximity` is missing, mistyped (`S1_interstate_proxmity`), or has a non-numeric value.
- Weights present but do not sum to 100 (the current file sums to 100 — 15+10+10+10+10+8+8+7+7+5+5+5 — but a between-run human edit could break this).
- `composite_threshold` outside the legal 0–100 range.
- `_immutable_during_run` flag missing or set to `false` (per Implementation Checklist line 468, this flag is mandatory).
- `_version` missing — required so the experiment log can later be cross-referenced to a parameters version when Phase 7+ accumulates cross-run TSVs.

**Recommended mitigations** (Phase 1):
- On module import, `prepare.py` calls `_load_parameters()` which (a) reads `parameters.json` from a path computed relative to `__file__` not `os.getcwd()` (so `python -m research` from any cwd works), (b) validates the JSON, (c) validates a minimal schema: presence of `_immutable_during_run == true`, `_version`, `hard_filters`, `scoring_weights` (all 12 S-keys present and numeric), `composite_threshold` (0 ≤ x ≤ 100), `actionability_thresholds`.
- On any validation failure, `prepare.py` raises a single `ParametersError` with the offending field and a one-line remediation hint. It does NOT auto-correct. It does NOT fall back to defaults. Auto-correction would be a metric-mutation vector.
- Validate that `sum(scoring_weights.values()) == 100` with a 0.01 tolerance; if not, raise. (This is a load-time check, not a runtime check, so it is cheap and Phase 1-appropriate.)
- Do NOT validate the more nuanced semantic rules (e.g., `acreage_min < acreage_max`) until Phase 4 when hard filters are wired up; Phase 1 should keep the load lean.

### 1.2 PostGIS extension permissions on Supabase free tier

**Known**: Supabase free tier is documented in `STORAGE_ARCHITECTURE.md` as the recommended host. PostGIS is listed as a supported extension on Supabase, but the role used by the application typically does NOT have superuser privileges; `CREATE EXTENSION postgis` may fail with permission denied unless executed by the Supabase `postgres` role or via the Dashboard's extension manager. **I do not have authoritative knowledge of Supabase's current free-tier behavior in April 2026** — this should be confirmed by Agent 3 or the human before Phase 1 sign-off.

**Failure modes**:
- `CREATE EXTENSION IF NOT EXISTS postgis` returns permission denied — `prepare.py` aborts with an opaque psycopg2 error.
- Extension is enabled in another schema (e.g., `extensions` schema on Supabase) and the geometry types are not resolvable from the default search_path. Symptom: `CREATE TABLE parcels (... geometry GEOMETRY(Polygon, 4326) ...)` fails with `type "geometry" does not exist`.
- Extension is enabled but at a version that does not support a function we use later (low risk for Phase 1 since we only DDL, not query).

**Recommended mitigations** (Phase 1):
- Wrap the `CREATE EXTENSION IF NOT EXISTS postgis` call in its own try/except and on permission failure print a clear message: `"PostGIS extension is not enabled and the application role cannot enable it. Enable PostGIS via Supabase Dashboard → Database → Extensions, then re-run prepare.py."` Exit code 3 (reserved for "extension/setup precondition not met").
- After the extension call (whether or not we created it), execute a probe query: `SELECT postgis_version();`. If this returns a non-null string, proceed. If it errors, abort with the same remediation message.
- Do NOT attempt to set `search_path` programmatically in Phase 1 — that's a between-runs human operation. If the geometry type is not resolvable, abort with a message pointing the human to set `search_path` on the database role.

### 1.3 Idempotency concerns

The Phase 1 spec requires `prepare.py` to be re-runnable. `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` are necessary but not sufficient.

**Failure modes**:
- Schema drift: a table exists from a prior run with the OLD column set; the new `CREATE TABLE IF NOT EXISTS` is a no-op and silently leaves the schema stale. The agent later writes to a column that does not exist, fails at runtime, mid-experiment-loop, well after Phase 1.
- A unique constraint or index that was added in a later schema revision is silently absent because the table already existed.
- `CREATE INDEX IF NOT EXISTS idx_parcels_owner_state ON parcels((SUBSTRING(owner_mailing_address FROM '[A-Z]{2} \d{5}')))` — this is a functional index. Different Postgres versions may parse the regex differently; idempotency check on a functional index name is correct, but if the expression mutates between schema revisions, the old index lingers.
- Multiple developers run `prepare.py` concurrently against the same Supabase instance; without `LOCK` or transaction discipline, two `CREATE EXTENSION` calls or `CREATE INDEX CONCURRENTLY` style operations could race. Low probability, but possible during the next two weeks.

**Recommended mitigations** (Phase 1):
- Print a short schema-version banner at the end of every successful run: `"prepare.py: schema applied (version 1.0, parameters version 1.1)"`.
- Add a `schema_version` table (single row) populated by `prepare.py`. Schema:
  ```sql
  CREATE TABLE IF NOT EXISTS schema_version (
      id INT PRIMARY KEY DEFAULT 1,
      version TEXT NOT NULL,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      CONSTRAINT singleton CHECK (id = 1)
  );
  INSERT INTO schema_version (id, version) VALUES (1, '1.0')
  ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, applied_at = NOW();
  ```
  This costs ~5 LOC and turns "did prepare.py run on this DB?" into a single SELECT for Phases 2+.
- Wrap all DDL in a single transaction so that a partial failure leaves the DB unchanged. NOTE: `CREATE INDEX CONCURRENTLY` cannot run inside a transaction; do not use `CONCURRENTLY` in Phase 1 — the table is empty, so a non-concurrent index build is fine.
- Do NOT add migration logic in Phase 1. If a later phase changes the schema, that's a between-runs `prepare.py` mutation per AUTORESEARCH_MECHANICS line 36 and triggers a new branch + baseline. Agent 2 should not pre-build a migration system — that's over-engineering and Phase 1 over-implementation.

### 1.4 Transaction boundaries

**Failure modes**:
- Auto-commit mode (psycopg2 default) means each DDL statement is its own transaction. If `CREATE TABLE parcels` succeeds but `CREATE INDEX idx_parcels_geometry` fails (e.g., PostGIS not in search_path), the database is left in a half-applied state. Re-running fixes it, but the failure message will be confusing.
- Long-running DDL holding `ACCESS EXCLUSIVE` on a system catalog could block other Supabase clients (the dashboard, harness reports). Phase 1 risk is low because tables are empty.
- Connection drops mid-DDL. Need a clean error path that does not leak DSN.

**Recommended mitigations**:
- Use a single explicit transaction: `with conn:` (psycopg2 context manager commits/rolls back). Disable autocommit. Issue all DDL inside the transaction. The one exception is `CREATE EXTENSION` which on some hosts must be in its own statement; do that first, commit, then begin the schema transaction.
- On exception inside the transaction, log the offending DDL statement (NOT the connection string), roll back, exit non-zero with a stable exit code (suggested: 4 for "schema apply failed").

### 1.5 SRID / projection risks

`STORAGE_ARCHITECTURE.md` (line 70–71) defines `geometry GEOMETRY(Polygon, 4326)` and `centroid GEOMETRY(Point, 4326)`, i.e., WGS84. **However**, per the prompt's instructions and `sources.json`, Fulton County's native ArcGIS layer is in EPSG:102667 (Georgia State Plane West, US survey feet). This means every Fulton parcel polygon must be projected to 4326 before insert.

This is a Phase 3 problem (the Fulton connector), not a Phase 1 problem — but `prepare.py` should not lock in any choice that makes the Phase 3 reprojection harder.

**Phase 1 risks**:
- `prepare.py` adds CHECK constraints on `ST_SRID(geometry) = 4326`. This is mostly fine — but when paired with bulk loads from the connector, it is a hot footgun if any single record is mis-projected. Recommend NOT adding the SRID CHECK constraint in Phase 1; rely on the column type declaration `GEOMETRY(Polygon, 4326)` which Postgres enforces automatically.
- `prepare.py` does not need to know about 102667 at all — that's the connector's concern. Resist any temptation to centralize reprojection logic in `prepare.py`; that would be Phase 3 leakage.

**Recommendation**: Agent 2 should add a one-line comment near the geometry columns: `-- All geometries stored as EPSG:4326. Reprojection from county-native SRID (e.g. EPSG:102667 for Fulton) is the connector's responsibility — see Phase 3.` This costs nothing and prevents future Agent-2-equivalents from putting reprojection in `prepare.py`.

### 1.6 Connection failure paths

**Failure modes**:
- `DATABASE_URL` not set → `os.getenv("DATABASE_URL")` returns `None` → `psycopg2.connect(None)` raises an unhelpful error.
- `DATABASE_URL` set but malformed (missing port, missing password). psycopg2 error message often includes the full DSN.
- TCP connect timeout (Supabase region cold start, network blip). Default timeout is essentially infinite — the script appears to hang.
- TLS handshake failure (Supabase requires TLS; if the URL lacks `?sslmode=require` and the client lib has stale CA bundle).
- Pooler vs direct: `STORAGE_ARCHITECTURE.md` line 339 specifies the **pooler** endpoint for the autonomous loop. `prepare.py` runs DDL, which the pooler in transaction mode does not support cleanly. Agent 2 should use the **direct** connection for `prepare.py`, not the pooler. This is a documented Supabase gotcha.

**Recommended mitigations**:
- Validate `DATABASE_URL` is set; if not, abort with exit code 2 and message: `"DATABASE_URL not set. Copy env.template to .env and fill in the connection string from Supabase → Settings → Database → Connection string (Direct, not Pooler, for prepare.py)."`
- Pass `connect_timeout=10` to `psycopg2.connect`.
- Catch `psycopg2.OperationalError` separately, mask the password and host:port-only error for logs (see 1.7).
- Add a comment in `prepare.py`: `# Use direct DATABASE_URL (port 5432), NOT the pooler (port 6543), for prepare.py. The pooler does not support all DDL operations cleanly.`
- Do NOT implement retry-with-backoff in Phase 1; an operator running a one-shot setup script can re-run it.

### 1.7 Credential leakage in error logs

This is the highest-criticality silent risk in Phase 1.

**Failure modes**:
- psycopg2's default exception messages contain the connection string with password in plaintext when `connect()` fails.
- A traceback printed to stdout/stderr ends up in CI logs, terminal scrollback, screen-shared demos, or pasted into a GitHub issue.
- `parameters.json` does not contain credentials but `os.environ` does. A blanket `print(os.environ)` for "debugging" anywhere in `prepare.py` would leak everything.
- Error handlers that re-raise after `print(repr(exception))` can leak DSN.

**Recommended mitigations**:
- In `prepare.py`, define a helper `_mask_dsn(dsn: str) -> str` that returns `host:port/database` only, dropping user+password. Use it in every log/print path.
- Wrap the connection block in a top-level try/except that catches `Exception`, calls `_mask_dsn` on the URL, prints a sanitized message, and `sys.exit(2)` — does not re-raise the raw exception which contains the DSN.
- Never `print(os.environ)` or `logger.debug(parameters_dict)` if parameters ever grows to include credentials (it does not today, but defense-in-depth).
- Confirm `.env` is gitignored. (Check `.gitignore`. If absent, add it; if `.env.template` is also accidentally gitignored, that's a separate bug.)

---

## 2. Failure modes of `research.py` scaffolding

`research.py` is the agent sandbox (FULL EDIT during a run, per AUTORESEARCH_MECHANICS.md line 65). At Phase 1 it must exist as an importable module with stubbed entry points. The dominant failure mode for a Phase-1 `research.py` is **doing too much**.

### 2.1 Over-implementation creep into Phase 2/3/5

**Failure modes**:
- Agent 2, having read `program.md` and the storage architecture, writes a "skeleton" that imports `requests`, defines `class FultonConnector`, sketches a `discover()` method that calls the ArcGIS endpoint. None of these belong in Phase 1.
- A "convenience" function gets added that scores parcels using parameters from `parameters.json`. That's Phase 5.
- A `costar_ingest()` stub that opens a folder. That's Phase 6.
- Anything that imports `playwright`, `anthropic`, `openai`, `arcgis`, or `geopandas`. None are in the Phase 1 `requirements.txt` and adding them is scope creep.

**Recommended mitigations**:
- `research.py` for Phase 1 is permitted to contain ONLY: (a) the file header comment, (b) `from prepare import <names>` imports, (c) four stub functions matching the four core experiment-loop concepts (`discover`, `score`, `snapshot`, `memo`) each raising `NotImplementedError("Phase 2+: <name> not implemented yet")`, (d) an optional `if __name__ == "__main__":` block that prints "research.py is a sandbox — invoked directly does nothing in Phase 1" and exits 0.
- Total target line count: under 80 lines including header. Anything over 120 lines is a red flag for Agent 3 review.
- `requirements.txt` must NOT contain `playwright`, `anthropic`, `requests`, `geopandas`, `arcgis`, `pandas`. If Agent 2 adds any of these, Agent 3 must reject. The Phase 1 requirements set is exactly: `psycopg2-binary` (or `psycopg[binary]` for psycopg3), `python-dotenv`. Optionally `pytest` if smoke tests are written (recommended — see Section 6).

### 2.2 NotImplementedError at import vs. call time

**Failure modes**:
- Agent 2 puts `raise NotImplementedError(...)` at module level "as a placeholder," meaning `import research` itself fails. This breaks the smoke test `python -c "import research"` and breaks any future test runner.
- Agent 2 defines stubs that do `raise NotImplementedError` inside the function body — correct — but ALSO calls one of those stubs at module import time as a "self-check." That defeats the purpose.
- A decorator or class-level expression evaluates eagerly (e.g., `@register(name="discover")`) and references `prepare.calculate_actionable_pipeline_count()` against an empty DB, requiring a live DB connection just to import.

**Recommended mitigations**:
- Hard rule for Agent 3: `python -c "import research"` must succeed without a database connection, without a populated `.env`, and without network access. This is testable and binary.
- Stubs must raise only when CALLED. Function bodies look like:
  ```python
  def discover(*args, **kwargs):
      raise NotImplementedError("Phase 3: connector-driven discovery not implemented")
  ```
- No top-level connection establishment in `research.py`. `prepare.py` should expose a lazy `get_connection()` factory; `research.py` uses it inside function bodies, not at import.

### 2.3 Runtime guard against `research.py` mutating immutable files

Per AUTORESEARCH_MECHANICS.md line 477, the agent must NOT have the ability to modify `prepare.py`, `parameters.json`, or `sources.json` from within `research.py`. This is a property the Implementation Checklist explicitly demands.

**Failure modes** (the Phase-1-relevant ones):
- `research.py` does `import prepare` and re-binds names: `prepare.composite_threshold = 50`. Even though the JSON file is unchanged on disk, the in-memory parameters used by the metric have shifted — silently corrupting the run.
- `research.py` does `with open("parameters.json", "w") as f: ...`. This is the obvious case and easy to test.
- A scoring function in `research.py` later in Phase 5 reaches into `prepare`'s module globals and mutates a cached parameters dict. If `prepare.py` exposes a mutable `dict`, this is trivially possible.

**Recommended mitigations** (Phase 1-implementable):
- `prepare.py` exposes parameters via a frozen accessor, NOT a module-level dict. Two acceptable patterns:
  1. A frozen dataclass: `@dataclass(frozen=True) class Parameters: hard_filters: HardFilters; scoring_weights: ScoringWeights; composite_threshold: float; ...` instantiated once and exposed as `PARAMETERS: Parameters = _load_parameters()`. Mutation attempts raise `FrozenInstanceError` at runtime.
  2. A `MappingProxyType`-wrapped dict tree: returns read-only views. Less type-safe but simpler to implement for arbitrary nested JSON.
  Recommendation: **frozen dataclass** for the top-level fields used in metric calculation (weights, threshold, hard filter scalars). For the more open-ended sections (`owner_classification.trust_keywords` etc., not used in Phase 1), `MappingProxyType` is fine. This is Section 3's parameters caching pattern question (see below).
- Add a runtime sentinel in `prepare.py`: at import time, capture the SHA-256 of `parameters.json` bytes and expose it as `PARAMETERS_SHA256`. The metric stub functions assert this hash is unchanged when called (cheap re-read). This catches the case of an external process editing `parameters.json` during a long-running cycle.
- Filesystem-level guard (NOT required for Phase 1, but cheap to add): on first run, `prepare.py` could call `os.chmod("parameters.json", 0o444)` to make it read-only. Skipping for Phase 1 because between-runs the human needs to write it. Defer to Phase 7+ when the loop is live.

### 2.4 Re-defining symbols imported from `prepare`

Implementation Checklist line 469: `research.py` imports from `prepare.py` and never redefines anything imported from there.

**Failure mode**: Agent 2 writes `from prepare import calculate_actionable_pipeline_count` and then later `def calculate_actionable_pipeline_count(...): ...` to "stub it out." This shadows the immutable function. For Phase 1, since the `prepare.py` version is itself a stub returning 0, the symptom would be silent.

**Mitigation**: Agent 3 grep check before commit: `git diff research.py | grep -E '^\+def ' | sort -u` should not contain any name that also appears in `dir(prepare)`. This is a pre-commit hook candidate but for Phase 1 a manual grep gate is sufficient.

---

## 3. AutoResearch-mechanics integrity

Walking the Implementation Checklist (AUTORESEARCH_MECHANICS.md lines 465–482), classifying each item as **Phase 1 must hit now**, **Phase 1 partially**, or **Defer**.

| # | Checklist item | Phase 1 status |
|---|----------------|----------------|
| 1 | `prepare.py` contains metric, gates, hard filters, formula, evaluation universe | **Phase 1 must hit (stubs)**: metric stubs return 0 against empty DB; gates and filter logic are stub functions raising NotImplementedError or returning a default-fail. Skeleton must be in place because moving these to `research.py` later would re-do the immutability boundary. |
| 2 | Immutability header comment in `prepare.py` | **Phase 1 must hit now** |
| 3 | `CLAUDE.md` tells the agent not to modify `prepare.py`/`parameters.json` | **Already done** (CLAUDE.md exists; verify the explicit statement in START_HERE.md or AUTORESEARCH_MECHANICS.md is reachable from the agent's bootstrap reading list) |
| 4 | `parameters.json` has `_immutable_during_run: true` | **Already done** (parameters.json line 3 has it) |
| 5 | `research.py` imports from `prepare.py`, never redefines | **Phase 1 must hit now** (see Section 2.4) |
| 6 | Setup phase implemented as discrete sequence | **Defer to Phase 7+**: the setup phase is what the human walks through to start a run, not Phase-1 scaffolding. |
| 7 | Baseline experiment as first row of `experiment_log.tsv` | **Defer to Phase 7+** |
| 8 | 90-min timeout enforced at OS level | **Phase 1 must hit now (helper only)**: a callable helper that the loop will invoke. The actual enforcement happens when the loop runs. See 3.1 below. |
| 9 | `git reset --hard HEAD~1` revert mechanism | **Defer**: this is a git-level discipline, not code. Document in CLAUDE.md/program.md. |
| 10 | Every commit on experiment branch corresponds to a `keep` row | **Defer to Phase 7+** |
| 11 | TSV is in `.gitignore` | **Phase 1 must hit now** if the TSV path is decided. Else defer. |
| 12 | Branch naming `autoresearch/<tag>`, `main` never written during a run | **Defer**: branch convention, not code |
| 13 | Agent cannot modify `prepare.py`/`parameters.json`/`sources.json` from `research.py` (file permissions or runtime check) | **Phase 1 must hit now (runtime check at minimum)**: see Section 2.3 frozen dataclass pattern. |
| 14 | Simplicity criterion in `program.md` and prompt | **Defer**: documentation |
| 15 | NEVER STOP rule in `program.md` and prompt | **Defer**: documentation |
| 16 | When `prepare.py` is mutated between runs, protocol followed | **Defer to phase 7+** |
| 17 | Cross-run TSV accumulation | **Defer to Phase 7+** |

Of the 17 items, **6 are Phase 1 must-hit (items 1, 2, 5, 8, 11, 13)**. Items 3 and 4 are already satisfied by existing files. The remaining items are Phase 7+.

### 3.1 OS-level vs Python-level 90-minute timeout

Implementation Checklist line 472 is unambiguous: "enforced at the OS level (not just an in-Python check)." The reason is that an in-Python check requires the Python event loop to remain responsive; if the agent enters a tight C-extension loop, calls `time.sleep(99999)`, gets stuck in a blocking subprocess, or hits a runaway recursion that doesn't honor signal handlers, the in-Python timeout never fires. OS-level enforcement is the only way to guarantee the loop dies at 90 minutes.

**Phase 1 deliverable**: a helper function in `prepare.py` exposing the timeout primitive. The actual loop invocation happens in Phase 7+, but the helper must exist and be testable now so Phase 7 doesn't have to revisit `prepare.py` (which would invalidate the run history).

**Implementation options**, ranked:

1. **`signal.SIGALRM` with `signal.alarm(seconds)`** (POSIX only). Pros: pure stdlib, fires regardless of what the main thread is doing — if the C extension respects signals, dies immediately; even when it doesn't, the next Python instruction triggers it. Cons: SIGALRM cannot be set from a non-main thread. The autonomous loop is single-threaded (per Karpathy pattern), so this is fine.
2. **Subprocess + `subprocess.run(..., timeout=5400)`** with the actual experiment running as a subprocess. Pros: hard kill via SIGKILL when timeout fires; truly OS-level. Cons: requires the loop to be packaged as a subprocess-invokable script, which is an architectural commitment.
3. **External `timeout(1)` GNU coreutils wrapper**: `timeout 90m python -m research`. Pros: simplest, true OS-level. Cons: only works when the loop is invoked from the command line; doesn't help if the loop is invoked from a notebook or test harness.
4. **`threading.Timer` + `os.kill(os.getpid(), signal.SIGTERM)`**. Pros: works on Windows. Cons: more moving parts, and SIGTERM is catchable so a misbehaving loop could swallow it.

**Recommendation for Phase 1**: implement the helper as a context manager backed by `signal.SIGALRM` for POSIX (Linux/macOS — the dev and Supabase target hosts), with a clear `NotImplementedError` on Windows pointing the user to option 3. Also document option 3 (`timeout 90m`) in the README/program.md as the recommended outermost wrapper for production runs. This gives belt-and-suspenders OS-level enforcement: a Python signal handler as the inner kill switch and a shell-level wall-clock kill as the outer one.

Concrete sketch (Agent 2 should write something like this, NOT exactly this — keep the code in Agent 2's hands):

```python
@contextmanager
def wall_clock_budget(seconds: int = 5400):
    """OS-level wall-clock timeout. Raises BudgetExceeded inside the with-block.

    Implementation note: uses SIGALRM on POSIX. This is the inner kill switch.
    Production runs should ALSO be wrapped in `timeout 90m ...` at the shell
    layer for belt-and-suspenders enforcement against C-extension loops that
    don't yield to Python signal handlers.
    """
```

The helper must be present in Phase 1 and tested (Section 6). Whether the loop actually wires it up is Phase 7+'s problem.

### 3.2 Parameters caching pattern: frozen dataclass vs global dict

Two competing patterns:

**Pattern A — frozen dataclass** (recommended):
```python
@dataclass(frozen=True)
class HardFilters:
    acreage_min: float
    acreage_max: float
    flood_zones_blocked: tuple[str, ...]
    # ...

@dataclass(frozen=True)
class Parameters:
    hard_filters: HardFilters
    scoring_weights: dict  # MappingProxyType wrapped
    composite_threshold: float
    version: str
    sha256: str

PARAMETERS: Parameters = _load_and_freeze_parameters()
```

Pros: any attempt to set an attribute raises `FrozenInstanceError` at runtime. IDE/type-checker support. Self-documenting.
Cons: more upfront code. New parameter sections require updating the dataclass. Mismatch between JSON and dataclass fields is a load-time error, which is what we want anyway.

**Pattern B — module-level dict** with `MappingProxyType`:
```python
_raw = json.load(open("parameters.json"))
PARAMETERS = types.MappingProxyType(_deep_freeze(_raw))
```

Pros: minimal code, handles arbitrary JSON shape.
Cons: no type checking; consumers do dict lookups; nested mutations require a recursive freeze function which is easy to get subtly wrong (lists become tuples? what about deeply nested lists?). Testing for "did I freeze this correctly" is harder.

**Recommendation**: **Pattern A for the top-level metric-relevant fields** (`hard_filters` scalars, `scoring_weights` dict, `composite_threshold`), **Pattern B (`MappingProxyType` of a deep-frozen tree) for the rest**. The metric-relevant fields are the ones whose mutation would corrupt the run; those deserve the strongest guarantee. The rest (owner classification keywords, etc.) are advisory inputs and a `MappingProxyType` is sufficient. This hybrid is more code than pure Pattern B but materially less than fully expanding every nested section into a frozen dataclass.

Agent 2 may pick either pure pattern if the hybrid is judged too complex; Agent 3's job is to ensure mutability tests pass (see Section 6) regardless of which pattern is chosen.

### 3.3 Idempotent imports

`prepare.py` is imported by `research.py` and possibly by tests. The parameters file load must be **once** per process — not per import (Python caches that automatically) and not per function call. Specifically:

- `_load_parameters()` runs at module import. The result is bound to module-level `PARAMETERS`.
- No code path causes a re-read mid-process. Specifically, no `if os.environ.get("RELOAD_PARAMETERS"):` or similar dev convenience hook. That's a metric-corruption vector.
- The SHA-256 sentinel is captured at load time and any function that uses parameters can optionally re-hash and assert equality — defense in depth for the rare case the file was hand-edited under a long-running process.

---

## 4. Security and credentials

### 4.1 `DATABASE_URL` handling

**Risks**:
- DSN ends up in tracebacks (see 1.7).
- DSN ends up in `git log` because someone committed `.env` instead of `env.template`.
- DSN ends up in shell history when a developer pastes it as a positional argument to a debug script.
- DSN ends up in CI logs because a dev added `set -x` to a deploy script.

**Mitigations** (Phase 1):
- `.env` is in `.gitignore`. Verify before committing Phase 1.
- `env.template` is committed and contains placeholders only:
  ```
  # Postgres connection (use the DIRECT connection string from Supabase, not the pooler, for prepare.py)
  DATABASE_URL=postgresql://postgres:CHANGE_ME@db.PROJECT_REF.supabase.co:5432/postgres?sslmode=require
  # Anthropic API key for Phase 7+ AI fallback (Phase 1 leaves this blank)
  ANTHROPIC_API_KEY=
  ```
  Note: include `?sslmode=require` in the placeholder so devs don't omit it.
- `prepare.py` and `research.py` both call `load_dotenv()` at the top, which is idempotent.
- `prepare.py` MUST NOT print `DATABASE_URL` even masked at INFO level. Only print masked DSN inside an error branch.
- No environment variable other than `DATABASE_URL` is referenced in Phase 1. `ANTHROPIC_API_KEY` is reserved for later phases — including it in `env.template` is correct (so the dev knows to fill it in eventually) but `prepare.py`/`research.py` MUST NOT read it in Phase 1.

### 4.2 Masked logging

Provide a single helper, used everywhere a connection-string error is rendered:

```python
def _mask_dsn(dsn: str | None) -> str:
    if not dsn:
        return "<unset>"
    try:
        # postgresql://user:pass@host:port/db?... -> host:port/db
        from urllib.parse import urlparse
        u = urlparse(dsn)
        return f"{u.hostname}:{u.port or 5432}/{u.path.lstrip('/').split('?')[0]}"
    except Exception:
        return "<unparseable>"
```

Agent 2's tests must include: pass a DSN with `password=secret`, assert `secret` does not appear in `_mask_dsn(dsn)`.

### 4.3 psycopg2-binary vs psycopg3 recommendation

**Recommendation: `psycopg[binary] >= 3.1` (psycopg3)**, **with `psycopg2-binary` as an acceptable Phase-1 fallback if psycopg3 introduces friction**.

**One-sentence rationale**: psycopg3 is the actively maintained successor with a cleaner connection-pool API, native async support (relevant for the Phase 7+ loop), and binary-COPY support useful for the bulk parcel inserts that Phase 3 onward will need; psycopg2 is in maintenance mode but works fine for Phase 1 DDL and is widely deployed against Supabase.

If Agent 2 picks psycopg2-binary for simplicity, Agent 3 should accept it but note in the commit message that a psycopg3 migration is on the radar for Phase 7. If Agent 2 picks psycopg3, all of `prepare.py`'s DDL still works because the cursor API is largely backward-compatible; the small differences (e.g., `conn.cursor()` is a context manager in v3, server-side parameter binding semantics) do not affect Phase 1.

Either way: **pin a major version** in `requirements.txt`, e.g., `psycopg[binary]>=3.1,<4` or `psycopg2-binary>=2.9,<3`. Unbounded pins are a Phase-7-stability risk.

### 4.4 Other security items

- No `os.system()`, no `subprocess.run(shell=True)` anywhere in Phase 1. There's no reason for either; flag any occurrence as a hard-reject for Agent 3.
- No reading of arbitrary user-supplied paths in Phase 1 (no path traversal surface yet).
- No SQL injection surface yet because all queries in Phase 1 are static DDL; Phase 3+ must use parameterized queries. Worth a comment in `prepare.py`'s header for posterity.

---

## 5. Architectural future-proofing

The danger of Phase 1 scaffolding is that small choices made now force unrelated changes in Phases 2–8. The Karpathy pattern depends on `prepare.py` being **stable** across the run; every Phase-N change to `prepare.py` invalidates the cumulative experiment log on that branch and forces a fresh baseline. Phase 1 must therefore make `prepare.py` shaped right for the steady state, not just shaped right for Phase 1's stub functionality.

### 5.1 Module vs. package

**Question**: Should `prepare.py` and `research.py` stay as flat single-file modules at the repo root, or should they be packages (`prepare/__init__.py`, `prepare/schema.py`, `prepare/metric.py`, etc.)?

**Recommendation: keep them as flat modules at the repo root for now**, but design the internal organization so a future split into a package is mechanical.

Rationale:
- Karpathy's pattern is explicitly file-shaped: "five-file contract." Making `prepare.py` a directory immediately raises the question "which subfile is immutable?" — answer becomes "all of them, plus `__init__.py`" — and the immutability discipline is harder to enforce at code review time.
- Phase 1 `prepare.py` will be ~150–250 LOC: imports, header, parameters loader, frozen dataclass(es), DDL string constants, `apply_schema()` function, metric stubs, timeout helper, masked-DSN helper, `if __name__ == "__main__":` runner. That fits comfortably in a single file.
- Phase 7+ `prepare.py` will grow with real metric logic but should still fit under ~600 LOC. Above that, splitting into a package is reasonable, but it triggers a metric-mutation event per the spec — so don't do it speculatively.

**However**: the DDL itself can be in a `schema.sql` text file loaded by `prepare.py`, OR inline as Python string constants. Recommendation: **inline string constants** for Phase 1. A separate `schema.sql` file adds an immutability question (is `schema.sql` part of the immutable layer? answer: yes) that's better avoided. If schema becomes >300 LOC, revisit in Phase 7+ via the proper between-runs `prepare.py` mutation protocol.

### 5.2 Avoid premature abstraction

**Anti-patterns to flag if Agent 2 introduces them**:
- A `BaseConnector` ABC. Phase 3.
- A `ScoringEngine` class. Phase 5.
- A `Pipeline` orchestrator. Phase 7.
- A migration framework (Alembic, etc.). Beyond scope.
- An ORM (SQLAlchemy). The spec says "psycopg2 or asyncpg"; an ORM layer is over-engineering and obscures the immutable schema.
- A test fixture system that requires a running Postgres. Phase 1 tests should mostly be unit-level — see Section 6.

### 5.3 Force-on-future-phases test

For each artifact, ask: "If Phase 5 needs to add scoring, does Phase 1's choice force Phase 5 to also touch `prepare.py`?"

- **DDL placement**: if all DDL is in `prepare.py`, then Phase 5 (scoring) doesn't need new tables — `parcel_scores` already exists. ✓
- **Parameters loader**: Phase 5 will read scoring weights. If `prepare.PARAMETERS.scoring_weights` exposes them today, Phase 5 doesn't need to touch the loader. ✓ (but confirm the dataclass exposes weights as a `MappingProxyType` of name→weight, not a typed-per-field structure — the latter would break when a new sub-score is added. Good news: adding a new sub-score requires editing `parameters.json` which is a between-runs human edit anyway, so a typed structure is fine. Ambiguous; recommend dict/MappingProxyType for resilience.)
- **Metric stub signatures**: `calculate_actionable_pipeline_count()` and `calculate_confidence_weighted_pipeline()` are the exposed metric API. Their signatures should be stable across phases. Recommended Phase 1 signature:
  ```python
  def calculate_actionable_pipeline_count(experiment_id: str | None = None) -> int: ...
  def calculate_confidence_weighted_pipeline(experiment_id: str | None = None) -> float: ...
  ```
  Both query the database directly — `experiment_id` filters to "scored_in_current_experiment" rows. In Phase 1, against an empty DB, both return 0 / 0.0. This signature will not need to change in Phases 2–8.
- **Timeout helper**: a context manager `wall_clock_budget(seconds: int)` is stable across phases. ✓

### 5.4 Connection management strategy

**Decision needed in Phase 1**: per-call connection vs. shared/pooled connection.

- For `prepare.py`'s one-shot DDL, a per-call connection (open, apply schema, close) is fine and is what Agent 2 should write.
- For `research.py`'s future loop, pooling is needed but should NOT be implemented in Phase 1.
- Phase 1 should expose `prepare.get_connection()` as a function returning a fresh connection — callers manage the lifecycle. No pool.
- Phase 7+ may add a `get_pool()` factory; that's a research.py-side concern, not a prepare.py concern (because pool config is a runtime tuning matter, not a metric matter).

### 5.5 Logging strategy

**Decision needed in Phase 1**: print vs. logging module.

Recommendation: use Python `logging` with a single `logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))` at module top of `prepare.py`. Reasons:
- Phase 7+ wants structured logs to feed the experiment log; the standard `logging` module is the only sane way to get there without rewriting later.
- `print()` to stdout pollutes interactive Python sessions and is harder to redirect.

Keep the format simple in Phase 1 (`%(asctime)s %(levelname)s %(message)s`); structured JSON logging is a Phase 7+ concern.

---

## 6. Phase 1 testing requirements

The Phase 1 spec in `BUILD_PHASES.md` line 46 says only: "`python prepare.py` runs successfully, creates all tables in Supabase. `parameters.json` reflects program.md defaults and can be tuned." That's a sniff test, not a test suite. For a measurement layer that the rest of the project depends on for its integrity, Agent 2 must include a minimal `pytest`-runnable smoke test set.

Recommended `requirements.txt` addition: `pytest>=8.0,<9`.

### 6.1 Required smoke tests (`tests/test_prepare.py`)

1. **`test_import_research_without_db()`** — asserts `import research` succeeds with `DATABASE_URL` unset, no network. Catches Section 2.2 failures. Implementation: monkeypatch `os.environ` to remove `DATABASE_URL`, then `importlib.import_module("research")`; expect no exception.

2. **`test_parameters_loaded_and_frozen()`** — asserts `prepare.PARAMETERS.composite_threshold == 70` (matching parameters.json), and that attempting `prepare.PARAMETERS.composite_threshold = 50` raises `FrozenInstanceError` (or `TypeError` for `MappingProxyType`).

3. **`test_parameters_weights_sum_to_100()`** — asserts `sum(prepare.PARAMETERS.scoring_weights.values())` is within 0.01 of 100.

4. **`test_parameters_immutable_flag_present()`** — asserts the loader rejects a parameters file with `_immutable_during_run: false` (use a tmp_path fixture and a monkeypatched loader path).

5. **`test_malformed_parameters_raises()`** — feed the loader an invalid JSON string; assert `ParametersError`.

6. **`test_mask_dsn_hides_password()`** — assert `_mask_dsn("postgresql://u:supersecret@host:5432/db")` does not contain `"supersecret"`.

7. **`test_mask_dsn_handles_unset()`** — assert `_mask_dsn(None)` returns a stable sentinel and does not raise.

8. **`test_metric_stubs_return_zero_on_empty_db()`** — requires a live test DB. Marked `@pytest.mark.integration`, skipped by default. Asserts `calculate_actionable_pipeline_count()` returns 0 against an empty `parcel_scores`. Skip if `DATABASE_URL` not set.

9. **`test_research_does_not_redefine_prepare_symbols()`** — introspects `dir(research)`, asserts no name in the set of "core prepare names" (e.g., `calculate_actionable_pipeline_count`, `PARAMETERS`, `wall_clock_budget`) is shadowed by a local definition in `research.__dict__` other than imports.

10. **`test_research_stubs_raise_not_implemented()`** — asserts calling each of `discover()`, `score()`, `snapshot()`, `memo()` raises `NotImplementedError`.

11. **`test_wall_clock_budget_fires()`** — uses a 1-second budget, runs `time.sleep(2)` inside the context manager, asserts `BudgetExceeded` is raised. POSIX-only; skipped on Windows with a clear reason.

12. **`test_wall_clock_budget_does_not_fire_when_within_budget()`** — sanity check the helper doesn't false-positive.

### 6.2 Required integration tests (manual, gated)

Marked `@pytest.mark.integration` and runnable with `pytest -m integration`. Skipped in default `pytest` invocation.

13. **`test_apply_schema_creates_all_tables()`** — connects to test DB (separate Supabase project recommended; DO NOT run against production), runs `apply_schema()`, asserts the expected tables exist via `information_schema.tables`. Confirms PostGIS extension is reachable.

14. **`test_apply_schema_is_idempotent()`** — runs `apply_schema()` twice; the second call must not raise. Confirms the IF NOT EXISTS guards are correct.

15. **`test_postgis_geometry_columns_present()`** — confirms `geometry_columns` view shows `parcels.geometry` and `parcels.centroid` with SRID 4326.

### 6.3 Tests Agent 2 should NOT write in Phase 1

- Tests that exercise the real ArcGIS Fulton endpoint (Phase 3).
- Tests that score a parcel (Phase 5).
- Tests that simulate the experiment loop (Phase 7+).
- Performance/load tests (premature).

### 6.4 Test execution gate

Agent 3 must not commit unless:
- `pytest` (default invocation, no integration marker) passes with at least tests 1–7, 9–12 above.
- The integration tests have been manually run by the human at least once against a Supabase free-tier instance and the result attached to the commit message or PR description.

---

## 7. Severity-ranked risk list with go/no-go gates

Severity definitions:
- **HIGH**: a failure here corrupts the metric, leaks credentials, or forces a re-do of the immutable layer (which invalidates run history). Must be mitigated before Agent 3 commits.
- **MEDIUM**: a failure here is recoverable but causes confusion, lost time, or future rework. Should be mitigated; Agent 3 may accept with documented rationale.
- **LOW**: a failure here is cosmetic or easily fixed in a follow-up. Mitigation is recommended but not gating.

### HIGH

**H1 — `parameters.json` mutation surface in `research.py`.**
Mitigation: frozen-dataclass / MappingProxyType pattern in `prepare.py`; SHA-256 sentinel; no mutable module-level dict exposed.
Go/no-go gate: tests 2 (`test_parameters_loaded_and_frozen`) and 9 (`test_research_does_not_redefine_prepare_symbols`) MUST pass. Agent 3 inspects `prepare.py` source and confirms no top-level mutable dict named `PARAMETERS`.

**H2 — Credential leakage in error logs.**
Mitigation: `_mask_dsn` helper; top-level try/except around `psycopg2.connect`; never `print(os.environ)`.
Go/no-go gate: test 6 (`test_mask_dsn_hides_password`) MUST pass. Agent 3 greps for `os.environ` usage in `prepare.py`/`research.py` and confirms only `os.getenv("DATABASE_URL")` and `os.getenv("LOG_LEVEL", ...)` appear, no broader iteration over environ.

**H3 — `research.py` import requires DB or env.**
Mitigation: lazy connection; no top-level `psycopg2.connect()` or `prepare.get_connection()` call; `NotImplementedError` only inside function bodies.
Go/no-go gate: test 1 (`test_import_research_without_db`) MUST pass. Agent 3 manually runs `unset DATABASE_URL && python -c "import research"` and confirms exit 0.

**H4 — `prepare.py` not idempotent (or appears idempotent but isn't on schema drift).**
Mitigation: all DDL inside a single transaction; `IF NOT EXISTS` guards; `schema_version` table.
Go/no-go gate: test 14 (integration, `test_apply_schema_is_idempotent`) MUST pass when run by human against test DB. Result attached to commit.

**H5 — PostGIS extension fails silently because it's in a non-default schema.**
Mitigation: probe `SELECT postgis_version()` after `CREATE EXTENSION`; abort with remediation message on failure.
Go/no-go gate: human runs `prepare.py` against Supabase free tier; confirms extension is present (either created by the script or already enabled via dashboard); attaches the success log to commit. **If the human cannot enable PostGIS on free tier**, this is an escalation to Agent 3 / human (see Section 8 open question).

### MEDIUM

**M1 — Over-implementation of `research.py` (Phase 3/5 leakage).**
Mitigation: hard line count cap (~80 LOC); requirements.txt restricted to psycopg+dotenv+pytest.
Gate: Agent 3 reviews `research.py` and rejects if it imports anything beyond `prepare`, `os`, stdlib, `dotenv`, or contains business logic beyond stub function definitions.

**M2 — Wall-clock budget helper not OS-level enough.**
Mitigation: `signal.SIGALRM`-based context manager; document `timeout 90m` shell wrapper as outer kill switch in README.
Gate: tests 11 and 12 pass on Linux/macOS. Windows behavior documented (NotImplementedError + remediation).

**M3 — DSN passes through pooler, breaks DDL.**
Mitigation: comment in `prepare.py` and `env.template` mandating direct (5432) connection.
Gate: comment present; integration test 13 succeeds against direct connection.

**M4 — Schema drift between dev and prod.**
Mitigation: `schema_version` table; banner print at end of `prepare.py` showing version applied.
Gate: schema_version row present after `prepare.py` runs.

**M5 — `.env` accidentally committed.**
Mitigation: confirm `.env` in `.gitignore`; `env.template` is what's committed.
Gate: Agent 3 runs `git check-ignore .env` and confirms it's ignored; `git ls-files | grep '^.env$'` returns nothing.

**M6 — Connection timeout / hang.**
Mitigation: `connect_timeout=10` on `psycopg2.connect`.
Gate: visible in Agent 2's source.

**M7 — Mid-process parameters reload.**
Mitigation: SHA-256 sentinel re-checked inside metric stubs; no `_reload_parameters()` exposed.
Gate: Agent 3 greps for any `_load_parameters` call other than the single module-level invocation.

### LOW

**L1 — Functional index regex parsing differences across Postgres versions.**
Mitigation: leave the index DDL exactly as in `STORAGE_ARCHITECTURE.md`. If it fails on Supabase's Postgres version, the human escalates and we reconsider.
Gate: index appears in `pg_indexes` after `apply_schema` (covered by integration tests).

**L2 — psycopg2 vs psycopg3 choice.**
Mitigation: pinned major version; rationale in commit message.
Gate: `requirements.txt` has a bounded version pin.

**L3 — Logging module not used (uses print instead).**
Mitigation: prefer `logging`; acceptable in Phase 1 but flag for Phase 7 cleanup.
Gate: not blocking.

**L4 — Schema reprojection comment missing.**
Mitigation: one-line comment near geometry columns.
Gate: not blocking; Agent 3 nice-to-have request.

**L5 — psycopg connection not closed on error path.**
Mitigation: use `with psycopg2.connect(...) as conn:`; psycopg2's context manager handles commit/rollback but NOT close — additional `try/finally` for `conn.close()` is recommended.
Gate: not blocking; cosmetic for Phase 1's one-shot script.

### Master go/no-go checklist (Agent 3 must verify before commit)

- [ ] `python -c "import research"` exits 0 with no `.env` (test 1)
- [ ] `pytest` default invocation passes tests 1–7, 9–12 with zero failures
- [ ] Integration tests 13–15 manually run by human; logs attached
- [ ] `.env` in `.gitignore`; `env.template` committed with placeholders for `DATABASE_URL` and `ANTHROPIC_API_KEY`
- [ ] `requirements.txt` contains exactly: psycopg(2-binary or 3 with binary extra), python-dotenv, pytest. No others.
- [ ] `prepare.py` has the immutability header comment quoting AUTORESEARCH_MECHANICS.md
- [ ] `parameters.json` unchanged from current state (schema/version unchanged)
- [ ] No `os.system`, no `subprocess.run(shell=True)`, no `eval`, no `exec` anywhere in Phase 1 code
- [ ] No imports of `playwright`, `anthropic`, `requests`, `pandas`, `geopandas`, `arcgis`, `sqlalchemy` anywhere
- [ ] Frozen parameters: `prepare.PARAMETERS.composite_threshold = 99` raises (test 2)
- [ ] PostGIS extension probe succeeds on the dev's Supabase instance (manual)
- [ ] Schema applied twice in succession without error (integration test 14)
- [ ] DSN never appears unmasked in any code path's logs (test 6)

If any item is missing, Agent 3 returns to Agent 2 with the specific gap, not a vague "looks good but...".

---

## 8. Open questions / Agent 3 escalation candidates

Items I cannot resolve from the specs alone. Agent 3 should resolve with the human or escalate.

### 8.1 Supabase free-tier PostGIS enablement (HIGH)

**Question**: Can the application role on Supabase free tier (April 2026) execute `CREATE EXTENSION postgis`? If not, the human must enable it via the Supabase Dashboard before `prepare.py` runs, and `prepare.py`'s probe-and-fail behavior must be the documented setup path.

**Why I can't resolve**: I do not have authoritative knowledge of Supabase's current free-tier permission model, and the spec assumes it works without specifying which mechanism enables it.

**Recommended resolution path**: Human confirms by attempting `CREATE EXTENSION postgis` from the dashboard SQL editor on the actual project. Agent 3 documents the result and updates `BUILD_PHASES.md` Phase 0 if dashboard enablement is required.

### 8.2 Pooler vs direct DSN — what does `DATABASE_URL` mean? (MEDIUM)

**Question**: `STORAGE_ARCHITECTURE.md` line 339 says the pooler should be used for the autonomous loop. `prepare.py` needs the direct connection for DDL. Should there be **one** `DATABASE_URL` env var (the direct one) used for both, or **two** (`DATABASE_URL` for direct, `DATABASE_POOLER_URL` for pooled)?

**Recommended Phase 1 resolution**: a single `DATABASE_URL` set to the direct connection string. Phase 7+ can introduce `DATABASE_POOLER_URL` if/when the loop demonstrates connection-limit issues. Documenting this in `env.template`'s comment is sufficient for Phase 1.

### 8.3 Test database vs production database (MEDIUM)

**Question**: Should the integration tests (13–15) run against the same Supabase project as production, or a separate one? The Phase 1 DB is empty, so collisions are unlikely, but Phases 3+ will have real parcel data.

**Recommended resolution**: human creates a second Supabase free-tier project labeled `land-research-test`. `requirements.txt`/`env.template` documents `DATABASE_URL` and an optional `TEST_DATABASE_URL`. Phase 1 doesn't need this yet, but adding the env var now in `env.template` (commented out) prevents a between-runs `prepare.py` mutation later.

### 8.4 psycopg2 vs psycopg3 final pick (LOW)

**Question**: spec allows either. I recommended psycopg3. Agent 2 may pick either; Agent 3 ratifies.

**Resolution path**: Agent 2 picks; Agent 3 reviews; human can override. Not blocking, but a 30-second decision should not become a recurring debate.

### 8.5 Where does the experiment_log.tsv live? (LOW for Phase 1)

**Question**: AUTORESEARCH_MECHANICS.md mentions `experiment_log.tsv` and the Implementation Checklist requires it to be in `.gitignore`. Phase 1 doesn't write to it, but should `.gitignore` already list it?

**Recommended resolution**: yes — add `experiment_log.tsv` and `*.tsv` to `.gitignore` in Phase 1 as a pre-emptive measure. This is a Phase 1 must-hit item per Implementation Checklist line 475 if the path is decided. If undecided, defer and surface to human.

### 8.6 Should `prepare.py` enforce immutability against itself? (LOW, philosophical)

**Question**: should `prepare.py` capture its own SHA-256 at module import and assert it unchanged before each metric call? This catches the case where the human edits `prepare.py` mid-run (forbidden per spec).

**Recommended resolution**: not in Phase 1. The spec relies on human discipline and CLAUDE.md instructions, not runtime self-checks. Adding this in Phase 1 is over-engineering; revisit in Phase 7 if a real incident occurs.

### 8.7 Naming: `prepare.py` and `research.py` at repo root — conflict with packages? (LOW)

**Question**: if Phase 7+ wants to publish anything as a Python package, having top-level `prepare.py` will conflict with `prepare` being a common name. Phase 1 accepts this; not relevant unless we publish.

**Recommended resolution**: deferred indefinitely.

### 8.8 Three-agent workflow: where do these review docs live long-term? (LOW)

**Question**: This document is at `reviews/01_phase1_scaffolding/01_risk_review.md`. The `appendix_a_county_connectors.md` describes the three-agent workflow but doesn't specify a directory layout for accumulated review docs. As phases accumulate, are review docs committed to git on the experiment branch, on main, or both?

**Recommended resolution**: commit review docs to `main` (not the autoresearch branch) since they predate any agent-loop activity. Agent 3 should confirm this with the human and may need to update `appendix_a_county_connectors.md` to clarify, but that's outside Phase 1's scope.

---

## End of review

Agent 2: read every gate in Section 7 before writing code. Address each HIGH and MEDIUM item; document acceptance rationale for any LOW item you choose not to mitigate.

Agent 3: use Section 7's master checklist verbatim as the commit gate. Section 8 items are escalation candidates — surface to the human, do not silently decide.
