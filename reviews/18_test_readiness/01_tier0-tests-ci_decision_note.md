# Decision Note — Track tier0-tests-ci (test readiness)

> Tier 0 per `STANDING_RISKS.md` § "Change tiers" (tests, CI config,
> `.gitignore`, README). No pipeline module, metric, schema or sandbox
> code changes. Between runs; full orientation chain completed by the
> orchestrator and the plan confirmed by the operator ("Proceed").
> Three roles (design, implementation, review/commit), each in an
> independent fresh context on the session's strongest available model.
> Verdict: **APPROVE**.

## What changed

1. **`.gitignore`** — the `results.tsv` / `experiment_log.tsv` lines
   carried inline `# ...` comments, which gitignore treats as part of the
   pattern, so neither file was ignored (`git check-ignore --no-index
   experiment_log.tsv` exited 1). That made the append-only TSV
   committable (SR-13) and left a dirty tree that `make setup` refuses
   after a baseline. Comments moved to their own lines; every other line
   of `.gitignore` and `screener/.gitignore` audited — no other defect.
2. **`.github/workflows/offline-tests.yml`** — also installs
   `screener/requirements.txt` and runs the screener suite as a separate
   step (`discover -s screener/tests -t . -v`), so it runs in CI at all
   and a failure names its suite. Root step unchanged.
3. **`tests/test_postgis_smoke.py`** — (i) inserted `parcel_scores` rows
   now carry a fresh `smoke-<hex>` run tag and both metric calls pass it
   as `run_tag=` explicitly (prepare's existing API; `prepare.py`
   untouched). Before, on any `autoresearch/<tag>` branch prepare derived
   the run scope from the branch, filtered out the NULL-tagged rows and
   the test false-failed with count 0. (ii) Host guard: refuses unless
   every host libpq would reach (DSN `host`/`hostaddr`, `PGHOST`/
   `PGHOSTADDR` fallbacks, Unix sockets) is local; `service=` counts as
   non-local; opt-in only with `POSTGIS_SMOKE_ALLOW_NONLOCAL=1`. Prints
   host names only, never the DSN.
4. **`tests/__init__.py`** — points `research._SOURCES_DIR` at a
   per-process temp dir (removed at exit), so the offline suite no
   longer leaves `sources/disco-fulton-*` dirs in the repo root.
   `_safe_cache_path` is unchanged and validates against the redirected
   dir.
5. **`tests/test_hermetic.py` (new)** — regression guards for all of the
   above: gitignore inline-comment lint + `git check-ignore` on runner's
   own path constants; cache redirect (incl. traversal still rejected);
   smoke host guard (Supabase direct/pooler, private IP, multi-host,
   `hostaddr`, `service`, env fallbacks, no password echo, exact opt-in
   value); AST checks that smoke metric calls and INSERTs are tagged; CI
   workflow runs both suites.
6. **`README.md`** — new "Testing" section: offline root and screener
   suites, live PostGIS smoke (CI image, local DSN, never Supabase), live
   connector harness, screener live trial and read-only `evaluate`
   calls. Links to existing docs; no counts or state snapshots.

## Verified by the reviewer (independently)

- **Tests fail on base** — with only `tests/test_hermetic.py` applied
  over base code: 14 run, 7 failures + 6 errors (every guard except the
  traversal test, which proves the checks were not weakened).
- **SR-13/SR-12** — `git check-ignore -v -n --no-index`: at base
  `experiment_log.tsv` and `results.tsv` are `::` (exit 1); after, they
  match `.gitignore:50`/`:48`. `.env`, `.halt`, `.experiment_loop.lock`
  and `sources/<cycle>/…` match; `tests/fixtures/costar/*.csv` stays
  re-included.
- **SR-6** — base suite leaves 6 `sources/disco-fulton-*` dirs; patched
  suite leaves none, and no `/tmp/land-research-test-sources-*` remains.
  Checked under `discover -s tests`, `discover tests` (`make tests`),
  `tests.test_discovery`, `tests.test_hermetic`, and each module run in
  isolation with `-p`.
- **Suites** — root 650 OK (636 at base + 14), screener 23 OK, with the
  default interpreter and in a fresh Python 3.11 venv holding only the
  two CI-installed requirements files, using the exact CI commands.
- **Live PostGIS** (own throwaway container, `127.0.0.1`, schema via
  `python prepare.py`): patched smoke exits 0 on the worktree branch
  (twice, used DB), on a temporary `autoresearch/smoke-check-rev` branch
  (`current_run_tag()` = `smoke-check-rev`), against a fresh
  `createdb`, and under Python 3.11. Base smoke on that same branch
  reproduces the false failure (`expected count==1 … got 0`). Temp
  branch deleted; container stopped.
- **Guard** — remote URL DSN and `postgresql:///…` with remote `PGHOST`
  are refused with exit 1 before any connect; a URL host of `127.0.0.1`
  wins over a remote `PGHOST`; the opt-in skips the guard. CI's
  `localhost` DSN passes.
- **SR-1/SR-15** — `prepare.py`, `parameters.json`, `sources.json`,
  `program.md` untouched; the metric is still computed only by prepare.
  **SR-2/SR-5** — smoke SQL stays literal with bound `%s` (run tag
  bound, never interpolated); INSERTs still share one transaction with
  an explicit commit. **SR-10** — no new dependency (`psycopg.conninfo`
  ships with psycopg; CI installs the existing screener requirements).
  **SR-8** — refusals print host names only; tests assert no password
  is echoed. No model identifiers or secret-shaped literals in the diff.

## Risks found in review, and what changed as a result

- One test comment claimed the refusal prints a "masked DSN"; it prints
  host names only. Reviewer corrected the comment (no behavior change).
- No other defects. Considered and accepted: the guard is stricter than
  libpq in places (e.g. `127.0.0.2`, upper-case `LOCALHOST`, a remote
  `host` with a local `hostaddr` are refused) — fails safe, and the
  opt-in covers deliberate use.

## Deviations

- The smoke test now exercises the run-scoped metric query (the one the
  ratchet uses) instead of the unscoped informational one; forcing the
  unscoped query would reintroduce a DB-wide count. DISTINCT ON ordering
  is identical in both.
- This note lives outside the track's file list, as the review procedure
  requires.

## Residual gaps / human follow-ups

- The smoke test still leaves its rows in the database it writes to (as
  before). The host guard and unique `smoke-*` tag contain the impact; a
  cleanup step would be a separate change.
- The cache redirect relies on the existing `import tests` convention: a
  future test module that reaches the cache writer must import the
  package (documented in `tests/__init__.py`).
- Network live trials (`connector_harness.py --county fulton --quick`,
  `screener.run --limit 200 --refresh`) were not run; only their flags
  were checked. README's pre-existing "SR-1 … SR-15" (register now has
  SR-16) is outside this track's scope.
