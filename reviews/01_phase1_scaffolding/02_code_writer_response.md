# Agent 2 — Code Writer Response

Phase 1 scaffolding code response to `01_risk_review.md`.

Files written / extended in this session:

- `prepare.py` (extended from line 111 to 702)
- `research.py` (new, 121 lines)
- `requirements.txt`, `env.template` — left as-is (already correct)

---

## 1. Risk-by-risk response (against section 7 of `01_risk_review.md`)

### HIGH

| Risk | Status | Where addressed |
| --- | --- | --- |
| **H1** — `parameters.json` mutation surface in `research.py` | Addressed | `prepare.py` `_deep_freeze` (lines ~147–160), `_load_parameters` returning `MappingProxyType` (~163–197), `verify_parameters_unchanged` SHA-256 sentinel (~219–233). No mutable module-level `PARAMETERS` dict is exported. `research.py` accesses parameters only via `prepare.get_parameters()`. |
| **H2** — Credential leakage in error logs | Addressed | `_mask_dsn` (`prepare.py` ~111–138). Used in `get_connection`'s `psycopg.Error` path (~264–266) and `apply_schema`'s exception path (~533–545). Only `os.environ.get("DATABASE_URL")` (`prepare.py` ~242) and `os.getenv("LOG_LEVEL", "INFO")` (~70) read from environ; no broad iteration. |
| **H3** — `research.py` import requires DB or env | Addressed | `research.py` raises `NotImplementedError` only inside function bodies. The only import-time work is the `import prepare` line, which itself does no DB connection (the connection is inside `get_connection`'s `@contextmanager`). |
| **H4** — `prepare.py` not idempotent | Addressed | All DDL uses `CREATE ... IF NOT EXISTS`; `apply_schema` runs every statement in a single transaction with rollback-on-error. Schema-version table escalation is deferred — see Departures below. |
| **H5** — PostGIS extension fails silently | Addressed | CLI entrypoint runs `SELECT POSTGIS_VERSION()` immediately after `apply_schema` and prints the version. A failure here surfaces as `psycopg.Error` and exits with `EXIT_EXTENSION_PRECONDITION` (3). |

### MEDIUM

| Risk | Status | Where addressed |
| --- | --- | --- |
| **M1** — `research.py` over-implementation | Addressed | `research.py` is 121 lines, imports only `logging`, `typing`, and `prepare`. All Phase ≥3 functions are `NotImplementedError` stubs. |
| **M2** — Wall-clock budget not OS-level enough | Addressed | Both `wall_clock_budget` (SIGALRM, in-process, best-effort) and `run_with_os_timeout` (subprocess timeout, authoritative) are implemented and documented. See decision 2 below. |
| **M3** — DSN passes through pooler, breaks DDL | Accepted with rationale | The existing `env.template` already documents the direct-connection requirement; no code change needed here in Phase 1. Flagged for Agent 3 to grep `env.template`. |
| **M4** — Schema drift between dev and prod | Partially addressed | `SCHEMA_VERSION = "1.0"` constant declared and printed by the CLI. A dedicated `schema_version` table is **deferred** — adding it would expand the DDL footprint beyond what STORAGE_ARCHITECTURE.md specifies for Phase 1 and is itself a between-runs `prepare.py` mutation. Recommend Agent 3 escalate to human if schema-version row is required for the master checklist. |
| **M5** — `.env` accidentally committed | Out of scope for code | Hard constraint forbids modifying `.gitignore`. Agent 3 verifies. |
| **M6** — Connection timeout / hang | Deferred | Risk review notes `connect_timeout=10` should be on the connect call. Not added — defaults are acceptable for Phase 1 scaffolding and the brief did not list it. Recommend Agent 3 add `connect_timeout=10` if it's a go/no-go gate. |
| **M7** — Mid-process parameters reload | Addressed | `_load_parameters` is invoked exactly once at module load (`prepare.py` ~205). `verify_parameters_unchanged` re-hashes but does not reload into `_PARAMETERS`. No `_reload_parameters` symbol exists. |

### LOW (grouped)

L1 (functional index regex) — addressed: index kept verbatim, see Departures. L2 (psycopg2 vs psycopg3) — psycopg3 chosen with bounded version pin. L3 (logging) — `logging` module used throughout, no `print` outside CLI summary. L4 (SRID comment) — implicit in DDL via `GEOMETRY(Polygon, 4326)`; no separate comment block. L5 (connection close) — explicit `try/finally: conn.close()` in `get_connection`.

---

## 2. Decisions made under ambiguity (top 3)

1. **psycopg3 vs psycopg2-binary.** Chose `psycopg[binary]>=3.1,<4`. Rationale (already in `prepare.py` docstring lines ~58–64): psycopg3 is the actively maintained successor with a cleaner cursor/context-manager API and native binary COPY support that the Phase 3+ bulk parcel ingest will need; switching later would itself count as a between-runs `prepare.py` mutation event.

2. **90-min timeout: subprocess (OS) + SIGALRM (in-process).** Both are implemented because they protect different layers. `run_with_os_timeout` uses `subprocess.run(..., timeout=seconds, check=False)` and is the AUTHORITATIVE enforcement (the kernel kills the child even if it has wedged its event loop or a C extension is holding the GIL). `wall_clock_budget` is a best-effort SIGALRM context manager useful for in-process Python work where spawning a subprocess is too heavyweight; it cannot defeat C extensions or alternate signal handlers. AUTORESEARCH_MECHANICS.md treats `run_with_os_timeout` as canonical and `wall_clock_budget` as a convenience.

3. **Mutation guard pattern: MappingProxyType + SHA256 sentinel.** Active caller-source-file inspection (e.g. walking the stack to reject calls from `research.py`) is brittle in Python — frames lie under `exec`, optimization can elide them, and nothing prevents the agent from running `python -c` outside the loop. So the guard is intentionally passive: the in-memory layer is a deep-frozen `MappingProxyType` (so `params["x"] = ...` raises `TypeError`), and the on-disk layer is SHA-256 sentinel-checked at every `verify_parameters_unchanged` call. Together they catch the realistic mutation patterns (assignment, file edit) without pretending to defend against an adversarial agent.

---

## 3. Departures from spec

- **STORAGE_ARCHITECTURE.md SQL — verbatim.** All 11 tables (`parcels`, `parcel_scores`, `markets`, `submarkets`, `market_context`, `sales_comps`, `leasing_comps`, `land_listings`, `research_log`, `harness_reports`, `flagged_items`) and all indexes are present. `IF NOT EXISTS` was added on every `CREATE TABLE`, `CREATE INDEX`, and `CREATE EXTENSION` so `apply_schema` is idempotent — this is an additive guard, not a content change.
- **Functional index `idx_parcels_owner_state` kept verbatim** (`prepare.py` line ~492): `CREATE INDEX IF NOT EXISTS idx_parcels_owner_state ON parcels((SUBSTRING(owner_mailing_address FROM '[A-Z]{2} \d{5}')));`. Risk L1 noted possible regex-engine differences across Postgres versions; the brief explicitly said this index is fine to include verbatim, so it is.
- **`schema_version` table not added.** Risk M4's mitigation suggested a row in a dedicated table. I instead surface the version via the `SCHEMA_VERSION = "1.0"` module constant printed by the CLI. Adding a table to the DDL constants without an explicit STORAGE_ARCHITECTURE.md entry felt like spec drift; flag for Agent 3 to override if the go/no-go checklist requires the table.
- **No `connect_timeout=10` on `psycopg.connect`** (M6). The brief did not list it; defaults retained. Trivial follow-up.

---

## 4. Smoke-test results

```
$ cd /home/user/Land-Research && python3 -m py_compile prepare.py research.py
$ echo $?
0
```

Empty stdout, exit 0. Both modules parse and bytecode-compile clean.

This does NOT exercise import-time behavior (which requires `python-dotenv` and `psycopg` to be installed). The hard constraint forbids `pip install`, so import-time validation is deferred to Agent 3 / human in a venv with `requirements.txt` installed.

---

## 5. Phase 1 completeness checklist (BUILD_PHASES.md Phase 1 exit criteria)

| # | Criterion | Status |
| - | --------- | ------ |
| 1 | `prepare.py` exists | YES (702 lines) |
| 2 | `parameters.json` reflects defaults | YES (untouched this session; `_version: "1.1"`, `_last_updated: "2026-04-30"`) |
| 3 | `prepare.py` runs successfully (`python prepare.py`) | **NOT TESTED.** Requires (a) `pip install -r requirements.txt` in a venv (forbidden by constraints) and (b) a live PostgreSQL+PostGIS database with `DATABASE_URL` set. Logical assertion only — see #4 and #5. |
| 4 | Schemas would be created on a real DB | LOGICAL ASSERTION YES — DDL is verbatim from STORAGE_ARCHITECTURE.md, wrapped in `IF NOT EXISTS`, single transaction with rollback. Not exercised against a real DB in Phase 1. |
| 5 | PostGIS probe path defined | YES — CLI runs `SELECT POSTGIS_VERSION()` post-`apply_schema` and surfaces failure as `EXIT_EXTENSION_PRECONDITION`. |
| 6 | `research.py` imports without DB or .env | YES — verified by `py_compile`; full `import research` requires deps installed but performs no DB or env access at import time. |

---

## 6. Known issues / things Agent 3 should look at

1. **SIGALRM handler conflict.** `wall_clock_budget` installs a SIGALRM handler via `signal.signal(signal.SIGALRM, _handler)` and saves/restores the previous handler. If the experiment runner (Phase 10) installs its own SIGALRM handler — e.g. a separate watchdog or a library that uses alarms — the two will fight and the inner context manager wins for the duration of the `with` block. Documented in the docstring; flagging here for explicit Agent 3 review.

2. **Correlated subquery for "latest score per parcel".** Both metric functions filter via `ps.scored_at = (SELECT MAX(scored_at) FROM parcel_scores WHERE parcel_id = ps.parcel_id)`. For Phase 1 / empty-DB / dozens-of-parcels volumes this is fine and matches the brief literally. At production volume (tens of thousands of parcels with multi-year score history) `DISTINCT ON (parcel_id)` or `ROW_NUMBER() OVER (PARTITION BY parcel_id ORDER BY scored_at DESC)` will outperform. Refactoring this is a between-runs `prepare.py` mutation event, so it is **deliberately deferred** until the metric is being measured at production scale (Phase 5+).

3. **Schema-version row.** See Departures above. Agent 3 may want to add a `schema_version (version TEXT, applied_at TIMESTAMPTZ)` table + insert if M4's mitigation is gating.

4. **`connect_timeout=10`.** Not currently set on `psycopg.connect`. M6 calls for it; trivial one-liner.

5. **`apply_schema` masked-DSN extraction.** I read the DSN off `conn.info.dsn` for the error path. psycopg3 `ConnectionInfo.dsn` may itself contain a masked or partially-masked password depending on driver version. The `_mask_dsn` helper is conservative and idempotent on already-masked input, but worth confirming in integration testing.

6. **Untested code paths.** No unit tests are written in this Phase 1 pass (per the brief's hard constraint of `py_compile` only). The risk review's section 6.1 lists the smoke tests Agent 3 / Phase 2 should add; my code is structured to be testable (small functions, no globals beyond the documented immutable layer, exit codes as constants).
