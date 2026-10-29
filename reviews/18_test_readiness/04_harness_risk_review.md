# Risk review: harness track (Tier 2), Agent 1

Base: 29c051e. Scope: `connector_harness.py`, `.github/workflows/harness-fulton.yml`, new `tests/test_harness_health.py`. `connector_registry.json` is read-only for this track (rationale under H-3). This review contains no code. Nothing in the repo was changed.

## Reproduced at base (offline, in memory)

| Probe | Result at base |
|---|---|
| `_run_all_checks(cobb/gwinnett/clayton/henry)` | `healthy`, all 10 checks `skipped`, exit 0 |
| `_overall_health([])` | `healthy` |
| `service_alive` pass, everything else skipped (DeKalb once a live URL is set without `parcel_layer_id`) | `healthy` |
| Same as above, plus a crash in a non-critical check | `degraded`. `research._harness_gate` proceeds on `degraded`. |
| `research._harness_gate` given `n_a` or any unknown label | `failing`, logs WARNING "unknown harness status", not cached |
| Workflow step under `bash -e`, harness rc 2 or 3 | `exit_code` is never written to `$GITHUB_OUTPUT` (empty) |
| Workflow grep run on a DeKalb-style `{"code":404,"message":"Service not found"}` failing report | Classified as TRANSPORT, so the job passes with a warning |
| `requests` given a `service_url` with no scheme | `MissingSchema` becomes `"request: MissingSchema"`, which `_arcgis_get` reports as status `"transport"` |

## Decisions (Agent 1)

1. **Status and exit code for a connector that is not configured.** Use `overall_health="n_a"`. That keeps the label inside the STORAGE_ARCHITECTURE vocabulary (`healthy|degraded|failing|n/a`) and matches the appendix dashboard's N/A. Add a structured discriminator so callers can tell "not configured" apart from `ai_fallback_only`. The recommended way is a top-level `failure_class: "config"` (see 3). A separate key is acceptable if it is tested. Exit codes:
   - n_a because the connector is not configured: **1** (`EXIT_CONFIG_ERROR`).
   - `ai_fallback_only` n_a: **0**, unchanged (R-20).
   - Aggregate precedence: failing 2 > degraded 3 > config 1 > ok 0. A permanently unconfigured county then cannot mask a degraded or failing one in `--all`/`--market`. Nothing consumes the `--all` exit code today; I checked Makefile, cli.py and the workflows.
2. **Health rule.** `healthy` and `degraded` both require all three critical checks (`service_alive`, `layer_schema`, `known_good_query`) to be present with status `pass`. If any critical check is skipped or missing, the result is `failing` when any check failed (fail-closed) and `n_a` (config) otherwise. Put the rule in `_overall_health`. Do **not** add a short-circuit before the network calls: DeKalb today (service_url set, no layer id, dead service) must keep reporting `failing`, because that is the signal that its URL is dead.
3. **Failure class.** Each failed check gets `failure_class` in `{transport, logic}`. The report gets a top-level `failure_class` in `{null, "config", "transport", "logic"}`.
   - The report is `transport` only if **every** failed check is `transport`. Any failed check without an explicit class counts as `logic`.
   - A check is `transport` only when `_arcgis_get` status is `"transport"` **and** the error string starts with `timeout:`, `http_5`, or `connection:` (excluding `SSLError`).
   - Everything else is `logic`: ArcGIS error envelopes with any code (including 200 + `{"code":404}`), HTTP 4xx (including 429), non-JSON or invalid bodies, `request:*` errors (`MissingSchema`, `InvalidURL`, `TooManyRedirects`), TLS failures, zero features, a missing query capability, and crashes. That includes the `_safe` crash results and the `_safe_run_checks` crash stub.
   - Keep the legacy per-check `transport_status` key unchanged so 90-day archives still compare. It is misnamed (it holds `transport|error|invalid`). The workflow must stop reading it.
4. **`--verbose`.** Implement it, but print only metadata summaries, at DEBUG, through the existing stderr logger:
   - What it prints: the sanitised URL via `_strip_sensitive_query_params`, HTTP status, elapsed time, byte length, parse status, top-level keys, feature count, and the error-envelope code and message.
   - It never prints response bodies or attribute values, and never writes to `harness_reports/`.
   - Update the `--help` text and the module docstring to say it deviates from the appendix's "prints raw API responses" on purpose (SR-8).
   - Do **not** remove the flag. An argparse error exits with 2, which collides with `EXIT_CONNECTOR_FAILING`.
5. **CI policy.** The verdict comes from the report's `failure_class`, not from exit code alone. `transport` produces a `::warning` and the job passes. `logic` fails the job, for both degraded (3) and failing (2). That matches R-12's "logic failures fail the job" and keeps today's de facto behaviour, where degraded fails because the exit code was lost. `config` (1), a crash, a missing or unparseable report, an unknown value, or an empty exit code all fail the job. If the human prefers degraded to only warn, that is a one-line change, and it should be recorded in the decision note.

## Risks (ranked)

### Critical

- **C-1 (req a): the narrow fix leaves false-healthy in place.** If the fix only handles "every check skipped", three cases stay wrong. An empty result list is `healthy`. A partially configured connector (`service_url` with no `parcel_layer_id`) is `healthy`, and that is exactly the state DeKalb reaches if the human pastes the live candidate URL into sources.json. Partial config plus a crash in a non-critical check is `degraded`, and discovery proceeds on it.
  - Mitigation: decision 2.
  - Tests: run `_overall_health` against a table covering all of those cases, plus quick-mode Fulton with every check passing and non-critical checks skipped (must stay `healthy`).
- **C-2 (req e): logic failures can still be classified as transport.** If `failure_class` is derived from `status == "transport"` alone, a typo'd `service_url` (missing scheme, `MissingSchema`) or an `InvalidURL` in a sources.json PR still passes CI with only a warning. TLS failures behave the same way; the DeKalb alternate's expired certificate is a persistent failure, not a transient one.
  - Mitigation: decision 3, with an explicit allowlist for transport and logic as the default.
  - Tests: one per error class, plus a mixed case (transport and logic together gives `logic`).

### High

- **H-1 (req d/e): CI fails open.** The exit code must always be captured, for example `rc=0; python … || rc=$?; echo "exit_code=$rc" >> "$GITHUB_OUTPUT"`, with the step left green. When the exit code is 0, CI must also check that the report says `overall_health=="healthy"`, as defence in depth against a Fulton config regression that yields n_a. Every unknown or empty state must fail. Parse the JSON with `python` (no `jq` or PyYAML; SR-10). Pass the exit code through `env:` rather than `${{ }}` interpolated into the script. Remove the `transport_status` grep entirely.
- **H-2 (a + e cascade): a full outage gets classified as logic, which violates R-12.** `check_pagination` returns `{"reason":"query failed"}` and `check_performance_baseline` returns `fail` without recording the status of the underlying request. During an outage the "unclassified counts as logic" rule then turns `service_alive`/`layer_schema`/`known_good_query` transport failures plus a pagination "logic" failure into a red job.
  - Mitigation: both checks must carry the transport class of their sub-queries. If either sub-query is logic, the check is logic.
  - Test: every network call returns a transport failure. Expect report `failure_class=="transport"` in both quick and full mode.
- **H-3 (req b): `redaction_extra_fields` can fail in four ways (SR-8).**
  1. The live registry's list contains the string `"_comment_for_humans: …"`. Skip `_`-prefixed entries; ArcGIS field names cannot start with `_`. Then the registry does not need editing.
  2. A scalar string would be iterated character by character, so nothing gets redacted. Raise `ValueError` at load time for anything that is not a list of strings, following the precedent of the orphan-key check.
  3. The extra fields must be a **union** with the defaults, never a replacement. Defaults stay redacted even when the extra list is empty or malformed.
  4. Match case-insensitively. ArcGIS returns keys in server case, and over-redaction is harmless. Apply the same matching to the default mapped fields (R-07 case drift).

  Blast radius: `load_registry` runs inside `research._harness_gate`, so a raise becomes Fulton `failing`, which aborts discovery and fails verify_setup. That is acceptable fail-closed behaviour, but only for a genuinely bad config. Gate: the real registry must load, and the Fulton extra list must come out empty.

  The new `Connector` field must have a default and go last. The `_redact_feature` change must be backward compatible (optional third argument), because existing tests call it with two arguments. Never apply extra-field redaction outside report sample features. `parcels` stays verbatim (SR-8 split).
- **H-4 (req c): `--verbose` can leak PII (SR-8).** Raw bodies end up in terminal scrollback, in the `make loop … | tee /tmp/loop-*.log` output if DEBUG is ever enabled in process, and in GitHub Actions logs, which anyone with repo read access can read and which are retained. Hence decision 4 (metadata only).
  - Tests: give a fake response that contains `"SMITH JOHN"` and a mailing address, and confirm neither appears in the `assertLogs` output.
  - The JSON report with `--verbose` must be identical to the one without it, apart from the timestamp.
  - `main()` changes the module logger's level globally. Tests must restore it (`addCleanup`), or later tests (including the research ones) inherit DEBUG.

### Medium

- **M-1: interaction with the discovery gate and runner.** Do not edit research.py in this track. The n_a to `failing` mapping is the intended skip, but it also writes `aborted=True` and an "abort … harness=failing" row, and it makes `runner.verify_setup` fail if an unconfigured county is ever added to `_MARKET_TO_COUNTIES`. That is acceptable fail-closed behaviour for now. Teaching the gate about n_a is the later Tier 1 change that wires those counties in. Add a test of `research._harness_gate` against a mocked not-configured report: expect `failing`, and no `_HARNESS_GATE_CACHE` entry.
- **M-2: a Fulton regression stops the loop.** A typo in the critical-name set, or quick mode accidentally skipping a critical check, would flip Fulton to n_a or failing. Then verify_setup fails and the NEVER-STOP loop cannot run. Gate it with a canned all-pass Fulton result in tests, plus the live checks in G10. research.py calls the harness in full mode (`quick=False`), so a full live run is recommended.
- **M-3: test hermeticity (SR-6, SR-7).**
  - `_write_report(report, reports_dir=REPORTS_DIR)` binds its default when the function is defined, so patching `ch.REPORTS_DIR` does **not** redirect writes. Tests that touch `run_harness_for_county` or `main` must patch `_write_report` and `_write_dashboard`.
  - Patch `time.sleep` (or `_arcgis_get`) so the backoff (1+2+4 s) and the rate limiter do not slow the suite.
  - No network calls. Use a session or `_arcgis_get` stub that fails the test if it is called on the not-configured path.
- **M-4: workflow hygiene.**
  - Add `tests/test_harness_health.py` to both `paths:` filters.
  - Server-supplied text is printed to the log (`cat "$report"`, error messages), and `::…` sequences in it would be interpreted as workflow commands. Prefer printing a `json.dumps` summary, or wrap the output in `::stop-commands::<token>`. This is low likelihood.
  - With `shell: bash` set explicitly, `pipefail` comes into play. Test the block under the same shell it will run in.
- **M-5 (pre-existing): retry budget versus job timeout.** If the server stops responding after connecting, each call can take about 87 s. Five quick-mode calls exceed the 5-minute job timeout, the job is cancelled, and R-12 is violated. Optional fix: wrap the call as `timeout 240 python …` and treat 124 with no report as transport. SIGTERM kills Python without writing a report. `_write_report` is not atomic (SR-7), so a kill mid-write could leave truncated JSON, and the classifier must fail closed on a parse error.

### Low

- The DNS NXDOMAIN case (a host typo) cannot be told apart from transient DNS failure; both appear as `connection: ConnectionError`. It stays transport. This is a documented residual risk, mitigated because the research gate fails closed at runtime.
- STORAGE_ARCHITECTURE spells the label `n/a` while the code emits `n_a`, and the future `harness_reports` table has no `failure_class` column (wiring it needs a prepare-mutation). This is doc drift only. Do not rename `n_a`.
- In the sandbox, the agent proxy can produce 403, 502 or ProxyError responses that are not Fulton verdicts. Verify transport paths offline, not by pointing at bogus hosts.
- Parallel tracks or repeated runs could exceed 1 req/s to Fulton (SR-11). Run live checks once each, one after another.

## Standing risks

SR-1: only the three track files change. sources.json, the registry, research.py and the immutable files stay untouched.
SR-2, SR-3, SR-4, SR-5, SR-9, SR-13, SR-14, SR-15, SR-16: not applicable. There is no SQL, no DB access, no metric impact and no research_log write. The harness must still not import prepare (`TestImportSafety`).
SR-6: covered by M-3.
SR-7: covered by M-5 (pre-existing).
SR-8: covered by H-3 and H-4.
SR-10: no new dependencies (no PyYAML or jq for tests).
SR-11: rate limiting unchanged, and the live runs are bounded.
SR-12: the workflow uses no secrets; keep it that way.
The screener and its frozen key are unaffected (no shared code).

## Tests required in `tests/test_harness_health.py`

1. Status table for `_overall_health` (C-1). Includes empty input, all skipped, service-only pass, partial config plus a failure, quick all-pass gives healthy, a critical failure gives failing, and a non-critical failure gives degraded.
2. In memory, with a network stub that raises if called: cobb, gwinnett, clayton and henry give n_a with config. Spalding and fayette give n_a, `failure_class` null, unchanged. `main(["--county","cobb"])` returns 1, `--county spalding` returns 0. Test aggregate precedence. No files are written.
3. Failure class per check and for the report: DeKalb 200-envelope 404 gives logic. HTTP 400 for a renamed field gives logic. A non-JSON 200 gives logic. MissingSchema gives logic. SSLError gives logic. ConnectionError, Timeout and http_503 after retries give transport. A full outage (pagination included) gives transport in both modes. Mixed transport and logic gives logic. The crash stub gives logic. Healthy gives null.
4. Redaction (H-3): an extra field is redacted, case-insensitively, including non-string and None values. `_` entries are ignored. The real registry loads with an empty extra list. A scalar string raises (or follows whatever behaviour is chosen, documented). Defaults stay redacted when the extra list is empty. The `_write_report` failsafe still fires.
5. Verbose (H-4): no attribute values in the logs, an identical report, and the logger level restored.
6. `research._harness_gate` given a not-configured report returns `failing` and adds no cache entry (M-1).
7. Optional static guard: the workflow text no longer contains the `transport_status` grep and does reference `failure_class`.

## Note for the decision note: DeKalb (report only, never edit sources.json)

Both DeKalb URLs in sources.json are dead. The primary returns 200 + `{"code":404,"message":"Service not found"}`. The alternate has an expired TLS certificate, and the harness never reads it.

Live candidate: `https://dcgis.dekalbcountyga.gov/hosted/rest/services/PropertyAppraisal/Parcels_IASWorld/MapServer`, layer 0.

For the human:
- Set `service_url` **and** `parcel_layer_id`, `field_mapping`, and an overlay entry in `connector_registry.json` (test_bbox and so on).
- After this fix, a URL on its own makes DeKalb n_a (config), not healthy. That is intended.
