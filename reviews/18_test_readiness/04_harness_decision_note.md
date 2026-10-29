# Decision Note: Connector Harness Health, Failure Class and CI Gate

> **Tier 2** per `STANDING_RISKS.md` § "Change tiers". The change touches an
> external-service integration (`connector_harness.py`) and its CI gate
> (`.github/workflows/harness-fulton.yml`). It was made in a between-runs
> development session: the orchestrator completed the full orientation chain
> and the human confirmed the plan. No autoresearch run was active.
>
> **Roles.** Risk enumeration before the change (`04_harness_risk_review.md`),
> implementation, and adversarial review plus commit were three separate
> roles. Each ran in an independent fresh context on the session's strongest
> available model.

## What changed

1. **No false healthy.** `_overall_health` now returns `healthy` or `degraded`
   only when `service_alive`, `layer_schema` and `known_good_query` were all
   present and all passed.
   - If a critical check could not run, the result is `failing` when anything
     failed, otherwise `n_a`.
   - A connector that is not configured (cobb, gwinnett, clayton, henry, or a
     `service_url` without a `parcel_layer_id`) reports `n_a` with
     `failure_class: "config"` and exits 1. Before this change it was `healthy`
     and exited 0.
   - `ai_fallback_only` stays `n_a` with exit 0.
   - The aggregate exit-code precedence is 2 > 3 > 1 > 0.
2. **Explicit failure class.** Every failed check, and the report itself,
   carries `failure_class`.
   - `transport` is an allowlist: a non-TLS connection error, a timeout, or
     HTTP 5xx, after retries.
   - Everything else is `logic`: ArcGIS envelopes of any code (including DeKalb's
     200 + `{"code":404}`), 4xx, non-JSON bodies, `MissingSchema`/`InvalidURL`,
     `SSLError`, zero features, drift, and crashes.
   - A report is `transport` only if every failed check is. `pagination` and
     `performance_baseline` carry the class of their sub-queries, so a full
     outage stays `transport` (R-12).
3. **`redaction_extra_fields` honoured (SR-8).**
   - The extra fields are added to the default redacted fields, never
     substituted for them, and matched case-insensitively.
   - The redaction applies to report sample features only.
   - Entries starting with `_` are human notes and are skipped, so
     `connector_registry.json` needed no edit.
   - A malformed value fails loudly when the registry loads.
4. **`--verbose` implemented as metadata only.** It logs one DEBUG line per
   request with the sanitised URL, HTTP code, time, bytes, parse status,
   top-level keys, feature count and the envelope code/message. It never logs
   bodies or attribute values, and the report is unchanged.
5. **Workflow.**
   - The exit code is captured for every rc (`rc=0; timeout 180 python … || rc=$?`).
   - The `transport_status` grep is gone. The verdict comes from the report's
     `failure_class`, cross-checked against the exit code and `overall_health`.
   - `transport` warns. `logic`, config, a missing or garbled report or exit
     code, and any disagreement all fail. Timeout 124 with no report warns.
   - Server text is printed inside `::stop-commands::`.
   - The new test file is in both path filters.

## What was verified

- **SR-6.** Root suite: 676 tests OK (636 base + 40 new). Screener suite: 23 OK.
  Both ran offline with `DATABASE_URL` unset. The new tests write nothing into
  the repo.
- **The new tests fail on base.** Against base `connector_harness.py` and the
  base workflow they fail (34 failures, 55 errors). Running base code against
  the new tests reproduces the exit code lost under `bash -e` for rc 2 and 3.
  Four reviewer mutations were each caught by the suite:
  - dropping the `SSLError` exclusion;
  - flattening pagination's class;
  - letting `logic` warn in the workflow;
  - case-sensitive redaction.
- **Workflow.** The run blocks were extracted independently with a YAML parser
  (scratch only, not a dependency) and run verbatim under `bash -e` against
  17 hand-written reports:
  - pass: healthy;
  - warn: degraded-transport (3), failing-transport (2), timeout 124 with no report;
  - fail: degraded-logic (3), the DeKalb envelope (2), n_a config (1), crash
    with no report, 124 with a report, exit 0 with n_a, exit 0 healthy but with
    a class set, exit 3 with a failing report, malformed JSON, a JSON list,
    missing `failure_class`, an unknown `failure_class`, and an injected
    `::error` in a server message, which stayed inside the stop-commands window.

  The harness, the test file and the inline classifier compile under 3.11,
  the CI interpreter.
- **In memory.** cobb, gwinnett, clayton and henry give `n_a`/`config` with
  rc 1 and 0 network calls. spalding and fayette give `n_a`/null with rc 0.
  dekalb, stubbed with the live envelope, gives `failing`/`logic` with rc 2.
- **Live (SR-11), run sequentially, once each.**
  - `--county fulton --quick --verbose`: rc 0, `healthy`, class null. The
    verbatim CI classifier printed "Harness healthy.". The DEBUG lines held
    metadata only.
  - Full mode, which is what `research._harness_gate` uses: rc 0, all 10 checks
    pass.
  - Owner and mailing fields were `[REDACTED]` in both. `harness_reports/`
    was removed afterwards.
- **Other standing risks.**
  - SR-1: the only files changed are the three track files and these two notes.
  - SR-10: no new dependencies.
  - SR-12: no secrets in the workflow.
  - SR-2, SR-5: not applicable (no SQL, no database).

## Phase-specific risks found (full list in `04_harness_risk_review.md`)

The review corrected the spec in several places, and the implementation
adopted every correction:

- Fixing only the "all skipped" case would have left three false-healthy
  paths: an empty result list, a URL without a layer id, and partial config
  with a crash.
- Classifying on status `transport` alone would have passed `MissingSchema`
  and expired-TLS failures in CI.
- Under fail-closed defaults, a full outage would have turned CI red unless
  pagination and baseline carried the class of their sub-queries.
- The registry's comment string would have been iterated as field names.
- Raw `--verbose` output would have leaked owner PII into CI and loop logs.

**Reviewer fixup.** `test_real_registry_loads_with_empty_extra_lists` pinned
every live list to `()`. That would turn CI red the moment a human adds a real
field name, which is the point of the feature. It is now
`test_real_registry_loads_and_skips_human_notes`, which asserts that the
registry loads and that no `_` note becomes a field name.

## Deviations

- **`--verbose`.** Appendix A says it "prints raw API responses". It now logs
  metadata only (SR-8). The module docstring and `--help` record this.
- **Not-configured `n_a` exits 1** (config error), not 0.
- **Degraded with `logic` fails CI.** That was already the de facto outcome,
  because the lost exit code meant degraded runs failed.
- **Spelling.** The report spells `n_a`; `STORAGE_ARCHITECTURE.md` spells `n/a`.

## Residual gaps and human follow-ups

- **DeKalb URLs in `sources.json` are dead** (human-only edit; not touched).
  - The primary returns 200 + `{"code":404,"message":"Service not found"}`.
  - The alternate has an expired TLS certificate and is never read by the
    harness.
  - Live candidate:
    `https://dcgis.dekalbcountyga.gov/hosted/rest/services/PropertyAppraisal/Parcels_IASWorld/MapServer`,
    layer 0.
  - It also needs `parcel_layer_id`, `field_mapping` and a
    `connector_registry.json` overlay. A URL alone now reports `n_a` (config),
    not healthy.
- **`research._harness_gate` maps `n_a` to failing.** That skip is fail-closed,
  but it also aborts the cycle and fails `verify_setup`. The Tier 1 change that
  adds an unconfigured county to `_MARKET_TO_COUNTIES` must teach the gate
  about `n_a`. Today only fulton is mapped.
- **CI fails open (warns only) in these cases:**
  - a DNS typo, a refused port, or a persistently 5xx host in a `sources.json`
    PR;
  - a code hang that hits the 180 s timeout.

  `research._harness_gate` still fails closed at runtime.
- **CI fails closed (red) on a mid-body connection reset.**
  `ChunkedEncodingError` is classified `logic`.
- **Out of scope here and unchanged:**
  - an HTTP 4xx whose JSON has no `error` key parses as ok;
  - `_write_report` is not atomic (SR-7);
  - the failsafe scans `sample_features` only;
  - argparse usage errors exit 2, which collides with the failing exit code;
  - Appendix A's `--verbose` line.
- **Process mismatch.** `STANDING_RISKS.md` asks the Tier 2 note to name the
  model. This session forbids model names in docs, so the roles are described
  generically. The human should reconcile the two rules.
