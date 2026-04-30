# Phase 2 Risk and Architecture Review — `connector_harness.py`

**Reviewer:** Agent 1 (Risk and Architecture, Claude Opus 4.7, read-only)
**Date:** 2026-04-30
**Scope:** Phase 2 deliverables enumerated in BUILD_PHASES.md L50–L60 and `appendix_a_county_connectors.md` "Connector Test Harness" (L854–L970).
**Inputs read:**
- `appendix_a_county_connectors.md` L133–L203, L204–L286, L289–L361, L600–L611, L837–L853, L854–L970
- `sources.json` (full)
- `STORAGE_ARCHITECTURE.md` L247–L263 (harness_reports table only)
- `parameters.json` L74 (`harness_report_retention_days: 90`)
- `BUILD_PHASES.md` L50–L60
- `prepare.py` L1–L80 (header + immutability comment)
**Inputs deliberately not read:** `program.md`, `COSTAR_INGESTION_CONTRACT.md`, Phase 1 review files, the Layer 2 implementation pseudocode (only the activation triggers were read).

This document is read-only output. No code, no `sources.json` mutation, no `prepare.py` import. Sections 1–9 below; severity-ranked roll-up in §8.

---

## 1. Failure modes of the harness's external HTTP layer

The harness's only network surface is HTTP/HTTPS to county ArcGIS endpoints (and, eventually, qPublic/Beacon for layer-2 connectors not in Phase 2 scope). All of the following must be specified before Agent 2 writes a line.

### 1.1 Timeouts

- **Connect timeout:** 5 s. County GIS hosts are typically reachable within 1 s; a 5 s ceiling is generous and still well under the per-check budget.
- **Read timeout per check:** differentiate by check type:
  - service_alive (`?f=pjson` on root): 10 s
  - layer_schema (`/{layer_id}?f=pjson`): 10 s
  - known_good_query: 30 s (a 1000-feature spatial query with geometry against a 102667-projected store can legitimately take 5–15 s on Fulton)
  - performance_baseline: 30 s, but record actual wall-clock to expose drift
  - pagination: 30 s
- **Total harness budget per county:** ≤ 90 s with `--quick`, ≤ 180 s without. The appendix L965 claims "under 2 minutes for 8 counties" — that aspiration only holds if individual queries stay under 15 s. Agent 2 must enforce a hard upper-bound timeout via `signal.alarm` or equivalent so a hung county does not stall the whole harness.
- A timeout must produce a `failing` rating for that check, never raise an unhandled exception.

### 1.2 Retry policy by error class

Specify per-class behavior. "Handle errors gracefully" is not a spec.

| Class | Examples | Policy |
|-------|----------|--------|
| 5xx | 500, 502, 503, 504 | Exponential backoff: sleep 1 s, retry; sleep 2 s, retry; sleep 4 s, retry; then fail. Max 3 retries. Record retry count in the report. |
| 429 | Rate-limit | Honor `Retry-After` header if present, else sleep 10 s, retry once. After second 429, mark `failing` and stop hitting that host for the remainder of the harness run. |
| 4xx (other) | 400, 401, 403, 404 | Fail-fast. No retry. Record status code and response body (truncated to 500 chars) in the check's error field. 401/403 specifically should be flagged as a configuration regression, not a transient failure. |
| ConnectionError | DNS failure, refused, reset | Treat as host unreachable. One retry after 2 s. If still failing, emit `failing` rating with `errors[]` entry "host unreachable: {host}". |
| SSLError | bad cert, expired cert | Fail-fast. Do **not** disable verification. Emit `failing` and a warning. |
| Read timeout | server accepted but did not respond | Treat as 5xx (retry with backoff). |

### 1.3 Politeness and rate-limiting

The appendix does not state a literal "1 req/sec" anywhere I read; the prompt cites it from memory. The conservative defaults that match real-world ArcGIS courtesy norms:

- 1 request per second per host minimum spacing (token bucket or simple `time.sleep(1.0)` between requests to the same hostname).
- Do not parallelize requests across counties under `--all` for Phase 2 — sequential is fine and stays well within budget. Concurrency is a Phase 4+ optimization and an extra failure mode (race conditions in the report writer, harder log correlation).
- Set a stable `User-Agent` header: `Land-Research-Harness/0.1 (+contact: <env COUNTY_HARNESS_CONTACT>)`. Counties block default `python-requests/X.Y` UAs; a stable UA also lets county admins identify us if they need to.

### 1.4 SSL cert errors on legacy county portals

Some Georgia county portals run aged TLS stacks (TLS 1.0/1.1, self-signed intermediate, or expired certs in the chain). The harness must:

- Default to strict verification (`verify=True`).
- If a cert error occurs, do **not** automatically retry with `verify=False`. Emit `failing` for that check and surface the cert error string in `errors[]`. A human must explicitly opt into insecure mode via env var (e.g., `HARNESS_ALLOW_INSECURE_HOSTS=fulton,clayton`).
- Never log full request URLs that include any auth tokens; today there are none, but the policy must precede the future-failure case (see §5.3).

### 1.5 Library choice: `requests` vs `httpx`

**Recommendation: `requests`.**

Rationale (one sentence): `requests` is already a Phase 1 transitive dependency (via `psycopg`'s peers / `python-dotenv` is independent, but `requests` is universally present and Karpathy-simple), supports per-call timeouts/retries via `urllib3.Retry`, and avoids the async surface that `httpx` would invite — the harness is sequential by design and async would add a failure mode without adding throughput.

Caveat: `requests` does not natively support per-host rate-limit token buckets. Implement a 12-line `_RateLimitedSession` wrapper that subclasses `requests.Session` and inserts a `time.sleep` based on `(now - last_request_time_for_host)` before `send`. Do not pull in a third-party rate-limiter library for this.

### 1.6 Connection pooling under `--all`

`requests.Session` reuses TCP connections per host. With sequential, single-county-at-a-time execution, the pool stays small (1–2 sockets per host). No special tuning required. **Watch-out:** if the harness ever moves to concurrent county execution, the default `HTTPAdapter` `pool_maxsize=10` becomes a silent serialization point; out of scope for Phase 2 but flag in code comments.

### 1.7 DNS caching gotcha

`requests` does not cache DNS; the OS resolver does. On CI runners with short-lived DNS caches and counties whose authoritative DNS is flaky, intermittent `NXDOMAIN`s will look like outages. Treat `socket.gaierror` as a transient ConnectionError (one retry). Document this so Agent 3 doesn't get confused by red CI runs that pass on re-run.

---

## 2. Failure modes of ArcGIS-specific behavior

The harness's core surface is the ArcGIS REST query pattern documented at appendix L218–L258. Each ArcGIS quirk below is a real production risk that the harness must catch, not a theoretical edge case.

### 2.1 `f=pjson` vs `f=json`

- `f=pjson` returns "pretty JSON" (whitespace-indented). Some servers reject `pjson` for query endpoints but accept it on metadata endpoints.
- `f=json` is universally supported.
- Appendix L874 explicitly uses `?f=pjson` for the service_alive check. **Recommendation:** Agent 2 should use `f=pjson` only for the two metadata checks (1, 2) that are human-debuggable, and `f=json` for all data queries (4, 9, 10). Mixing them this way mirrors the appendix and reduces parsed-bytes at minimal cost.
- Both formats parse identically in Python; the only behavioral difference that matters is server-side support. If a county returns 400 on `pjson`, the harness should auto-fall-back to `json` for that host with a recorded warning, not fail the check.

### 2.2 `maxRecordCount` quirks

Fulton's `maxRecordCount` is **2000** (sources.json L15, appendix L323). Risks:

- ArcGIS servers **silently truncate** to `maxRecordCount` and do **not** return a `exceededTransferLimit: true` flag on every server version. Agent 2 must:
  - Cross-check `len(features) == requested resultRecordCount` and treat that boundary case as a possible truncation, not a happy path.
  - Use `returnCountOnly=true` first on the known-good query to obtain the real count, then a second query for the records. The cost is one extra trivial round-trip; the benefit is that `field_population` and `pagination` checks operate on a known denominator.
- The pagination check (L882, "request 1 record then 10 records") must use `resultRecordCount=1` then `resultRecordCount=10`. If the server ignores that param and returns all 2000, that's a finding. The appendix wording "confirm the API respects `resultRecordCount`" is exactly this assertion.

### 2.3 Spatial reference handling

- Fulton's native SR is **102667** (State Plane Georgia West, NAD83, feet — sources.json L16; appendix L325).
- The pipeline schema is **4326** (WGS84 lat/lng) per ParcelRecord `centroid_lat`/`centroid_lng` (appendix L170–L171) and the Standard ArcGIS Query Pattern's `outSR=4326` (appendix L228).
- **The harness's job is to validate, not transform.** Specifically:
  - Geometry validation check (L881) should request `outSR=4326` and assert the returned coordinates are in the WGS84 lat/lng range (-90 ≤ lat ≤ 90, -180 ≤ lng ≤ 180). If they look like 2,200,000 / 1,400,000-magnitude State Plane numbers, the server ignored `outSR` and the harness must mark the check as `failing` with a clear "spatial reference reprojection rejected" message.
  - Do not implement reprojection in the harness. That's connector / `research.py` territory, and doing it here would double-count work and introduce a `pyproj` dependency.
- Flag: if Fulton's server ever stops honoring `outSR=4326`, every downstream consumer breaks. The harness is the only place where this regression gets caught early.

### 2.4 Pagination via `resultRecordCount` + `resultOffset`

- The check must verify both pagination params actually work:
  - Query 1: `resultRecordCount=10, resultOffset=0` → save the 10 ParcelIDs.
  - Query 2: `resultRecordCount=10, resultOffset=10` → save the next 10 ParcelIDs.
  - Assert no overlap between the two sets. If the server ignores `resultOffset` and returns the same page, the assertion fails and the check is `failing`.
  - Without ordering (`orderByFields`), some ArcGIS versions return non-deterministic page contents. **Add `orderByFields=<parcel_id_field>` to all paginated test queries** to make the check deterministic. Without it, this check will be flaky.

### 2.5 Field name case sensitivity

ArcGIS REST is mostly case-insensitive on `outFields`, but the **response keys preserve server-side casing**. Two failure modes:

1. The connector's `field_mapping` (sources.json L17–L32) lists keys like `"Owner"`, `"OwnerAddr1"`, `"LandAcres"`. If the live schema returns `OWNER`, `OWNERADDR1`, `LANDACRES`, the field_mapping check (L876) will report all fields missing — this is a **hard regression signal** and should fail the check, but Agent 2 must compare case-insensitively when *diagnosing* the discrepancy so the error message is "field 'Owner' not found; closest match: 'OWNER' (case difference)" rather than "field 'Owner' not found". Diagnostic clarity matters.
2. The field_population check downstream uses the response keys as-is. If casing drifts and the diagnostic isn't there, every population rate silently reports 0%, which would falsely look like a Daniel's-Law-style redaction event.

### 2.6 Empty geometry handling

Some ArcGIS layers return `"geometry": null` for features whose geometry was never digitized or was administratively voided. The geometry validation check must:

- Count features with null geometry separately from features with invalid geometry.
- Report null-geometry rate as a warning (not a failure) if it's < 5%, as a failure if ≥ 5%.
- Never crash on `feature["geometry"] is None` — the most common harness bug pattern.

### 2.7 ArcGIS error envelopes

ArcGIS often returns HTTP 200 with a JSON body of `{"error": {"code": 400, "message": "..."}}`. **Do not infer success from HTTP 200 alone.** Every parsed response must be checked for an `error` key before being treated as data. Add this as the very first thing the response parser does; it is the #1 reason ArcGIS clients silently misreport health.

### 2.8 Layer 11 vs other Fulton layers

The Phase 2 harness only validates the parcel layer (id `11`, sources.json L10). Zoning (34), Future Land Use (33), and TADs (37) are out of Phase 2 scope but their existence in `sources.json` could tempt Agent 2 to check them all "while we're here." **Don't.** Each extra layer adds a check matrix dimension and a new failure mode the agent isn't yet ready to handle. Phase 2 = parcel layer only. Document the deferral in a code comment.

---

## 3. Failure modes of validation logic

The harness's value comes from precise definitions of "pass." Loose definitions produce false-greens that the agent will trust. Each of the 10 checks below has a concrete pass/fail rule.

### 3.1 Field population rate (check 5)

The appendix says "non-null, non-empty" with an 80% threshold (L878, L922). Agent 2 must define **empty** explicitly:

- `None` → empty
- `""` (empty string) → empty
- whitespace-only string (`"   "`, `"\t"`, `"\n"`) → empty
- string `"null"` / `"NULL"` / `"None"` (case-insensitive) → empty (some ArcGIS exports stringify nulls)
- numeric `0` → **NOT** empty for value/acreage fields. A $0 land assessment is a real datum (vacant lot, agricultural exemption, government parcel). Treating `0` as null would falsely lower population for valid records.
- Date `0` epoch / `"1900-01-01"` → empty (sentinel placeholder).

Recommend: a single `_is_populated(field_name, value) -> bool` helper that the harness routes everything through, with a per-field type hint (string vs numeric vs date). Without typed handling, every county will produce subtly different population rates and trend analysis becomes meaningless.

Edge: the population rate is computed across the **sample set** (10 features from the known-good query), so the rate has resolution ±10%. An 80% threshold against a 10-feature sample means 7/10 passes and 8/10 fails, which is unstable. Recommend Agent 2 either (a) raise sample size to 25 features for the population check or (b) require sample size = 10 but compare to a 70% threshold, with the appendix's 80% threshold reserved for trend tracking across multiple runs. Flag this as Open Question for Agent 3 (§9).

### 3.2 Owner-name sanity (check 6 — Daniel's Law-style redaction detection)

Appendix L879: "real names (not redacted, not all-uppercase placeholder strings, length > 3 characters)." Three concrete rules + one trap:

1. **Length:** trimmed length > 3 characters.
2. **Redaction tokens:** flag any owner whose value, normalized (uppercased, non-alpha stripped), matches a pattern in: `{"REDACTED", "PROTECTED", "CONFIDENTIAL", "DANIELSLAW", "ACT200", "WITHHELDPERLAW", "NAMEONFILE", "NOTPUBLIC", "PRIVATE"}`. This list grows; treat it as a constant in the harness with a code comment pointing at the appendix.
3. **All-uppercase placeholder heuristic:** the appendix explicitly calls out all-uppercase strings. **But** legitimately many county records store ALL-CAPS owner names ("SMITH JOHN H"). Agent 2 must NOT treat all-uppercase as a redaction signal on its own. The signal is "all-uppercase **AND** matches a known redaction token **OR** length ≤ some threshold AND no spaces." A name like "REDACTED" is the bad case; "SMITH JOHN H" is fine.
4. **Trap:** the field_population check (3.1) treats `""`/whitespace as empty, so 100%-redacted counties would already report 0% population. The owner-data sanity check is for the harder case where the field has data but it's redaction tokens. Agent 2 must run both, and the harness must report `"redaction_detected": true` when even **one** of N sample features matches the redaction tokens — a single redaction token in a sample of 10 means thousands of redactions county-wide.

### 3.3 Address parsing (check 7)

Appendix L880: "have street numbers, contain state codes, etc." Recommend:

- Street number heuristic: regex `^\s*\d+`. Many addresses lead with the number; if no number prefix, mark as unparseable.
- State code: regex `\b[A-Z]{2}\b` AND that 2-letter token must be in the USPS state set (mostly we'll see `GA`).
- ZIP: regex `\b\d{5}(-\d{4})?\b`.
- "Parses cleanly" = all three present. **Pass rate threshold: 80%** of mailing addresses (sample of 10).

False-negative cases to flag:
- PO Box owner addresses ("PO BOX 1234, ATLANTA GA 30303") have no street number — they would fail the street-number rule but are fully valid. Recommend a separate PO-Box regex branch that bypasses the street-number requirement.
- Apartment/Unit suffixes ("123 MAIN ST APT 5B") — no impact on parsing pass rate; still has a number prefix.
- International addresses (rare for parcel owners, but corporate owners with foreign addresses exist). The harness should classify these as "unparseable but not invalid" and not count them in the failure rate. A simple "if no US ZIP-like token, classify as international, exclude from rate" works.
- Municipality-only addresses ("ATLANTA GA 30303" as the entire owner address, no street). These exist for trustees, government, etc. Classify as warning, not failure.

Site address parsing has the same rules but **stricter expectations**: a site address that lacks a street number is an actual data quality issue (a parcel without a digitized street number is unusual outside of unimproved rural land). Recommend split rates: `mailing_address_parse_rate` and `site_address_parse_rate`.

### 3.4 Geometry validation (check 8) — per-county bbox

Appendix L881: "valid coordinates within the county's expected geographic extent." This **requires** a per-county bounding box in the registry. Spec it now:

- New field per registry entry: `expected_bbox: { "min_lng": -84.65, "min_lat": 33.40, "max_lng": -84.05, "max_lat": 34.20 }` (Fulton's approximate WGS84 envelope; Agent 2 can refine with one DevTools fetch of layer 11's `extent` from `?f=pjson` and convert from 102667 if needed).
- The check passes if every returned feature's centroid (or first ring point) falls inside the bbox after WGS84 reprojection. A point outside the bbox is either a server-side reprojection bug, a multi-county shared layer, or a corrupt record.
- **Tolerance:** allow features up to 5 km outside the bbox (border parcels, slight bbox under-estimation). Hard-fail beyond that.
- For Phase 2, only Fulton needs an `expected_bbox`. Agent 2 should make the field optional in the registry schema; missing bbox → check is `skipped` with a warning, not a failure. This keeps the registry abstraction extensible to AI-fallback-only counties (Spalding, Fayette) which will never have an ArcGIS bbox.

### 3.5 Performance baseline drift (check 10)

The single-run baseline is meaningless on its own. To flag degradation Agent 2 needs history. Two options:

- **Option A (file-based, recommended):** read the last N JSON reports from `harness_reports/{county}_*.json`, extract `checks.performance_baseline.avg_response_ms`, and flag drift if the current run's response time is > 2.0× the median of the last 10 runs.
- **Option B (DB-backed):** the `harness_reports` table per STORAGE_ARCHITECTURE.md L247–L263. **Reject this for Phase 2** — it violates "must work even when Postgres is down" (appendix L897–L903 paraphrase: harness is a diagnostic when production queries fail; if Postgres is the production failure mode, the harness can't depend on it).
- **Bootstrapping:** with 0–9 prior runs, performance check is `pass` (insufficient history). Document this state explicitly in the JSON output (`"reason": "insufficient history; need 10 runs to establish baseline"`).
- **Pruning interaction:** retention is 90 days (parameters.json `harness_report_retention_days: 90`). At nightly cadence that's ~90 reports per county — plenty of history. If the agent ever runs the harness on every production query failure, the dir explodes. Recommend the harness cap reads at the 100 most-recent files for trend analysis regardless of file count present.

### 3.6 Known-good query test (check 4) — non-empty assertion

The bbox + acreage filter must return at least 1 feature. Risks:

- The test bbox may be empty after a county-wide reparcellization (unlikely but possible). The harness should fail loudly with a clear "test bbox returned 0 features — bbox may need updating" message rather than swallowing the empty result.
- The acreage filter is configured per connector (sources.json doesn't yet specify; appendix L290–L361 should). For Fulton recommend test acreage range 5–50 acres (standard industrial site target). If Agent 2 finds the appendix's Fulton spec doesn't include test acreage, it must add a default to the registry, not invent one inline.

### 3.7 Service-alive (check 1) — what counts as "alive"

- HTTP 200 + parseable JSON + presence of `currentVersion` or `serviceDescription` keys at the top level. Don't accept just "200 with any body" — caching proxies and captive-portal redirects can return 200 with HTML.
- Add a content-type sniff: must be `application/json` or `text/plain` with parseable JSON body. HTML body → fail.

### 3.8 Layer schema (check 2)

- Confirm `type == "Feature Layer"` (not "Raster Layer", not "Group Layer").
- Confirm `geometryType` is `esriGeometryPolygon` (parcels are polygons; if a county returns points, that's a different layer and the connector is mis-configured).
- Confirm `capabilities` includes `"Query"`. If `"Pagination"` is not in `advancedQueryCapabilities`, mark the pagination check (9) as `skipped` with reason rather than running it and erroring.

---

## 4. AutoResearch-mechanics integrity for Phase 2

The harness sits in a sensitive position: it's a tool the autonomous agent invokes to make decisions about its own data flow. Mechanics violations here would silently corrupt the AutoResearch loop.

### 4.1 No `prepare.py` import, no Postgres touch

- `prepare.py` is the schema layer (Phase 1 GREEN). It declares the `parcels` and `parcel_scores` tables and is imported by `research.py`. **The harness must not import `prepare.py`.** Doing so would (a) drag in `psycopg`/`SQLAlchemy` (whichever Phase 1 uses) into the harness module, (b) make the harness fail-import when Postgres is down, breaking the L897–L903 diagnostic role, and (c) blur the boundary between schema layer and probe layer.
- The harness must not open any DB connection of its own. Period. If a future requirement calls for harness output to land in the `harness_reports` Postgres table, that's a *separate* loader script, not a harness function. Phase 2 emits JSON files only.
- Concrete code-review check for Agent 3: `grep -E "import prepare|from prepare|psycopg|sqlalchemy|create_engine|DATABASE_URL" connector_harness.py` → must return zero matches.

### 4.2 No `sources.json` mutation

- The harness **reads** `sources.json` to seed the registry (or the registry is built statically in Python — see §6). It must never write to `sources.json`, even to update `validated: true` / `validation_date`. Those are human-curated fields per the AUTORESEARCH_MECHANICS immutability convention; the harness output (JSON report) is the machine-generated artifact, and a human gates the promotion of fields in `sources.json`.
- Concrete code-review check: `grep -E "open\(.*sources\.json.*['\"](w|a|r\+|w\+|a\+)" connector_harness.py` → zero matches. Read-only `open(... "r")` only.

### 4.3 The "validation as a discovery cheat code" risk

There is a subtle attack surface where the autonomous agent could exploit the harness:

- The harness reports `overall_health: healthy` for any connector whose 10 checks all pass against a *test* bbox + acreage filter.
- A rogue or sloppy agent could shrink the test bbox / loosen the acreage filter to make a marginal connector report `healthy` and thereby get its discovery rotation back.
- Mitigation: the test bbox and acreage filter are **registry-level configuration**, not runtime parameters. The agent has no CLI flag to override them. The registry is set by humans (or by a sub-agent in a one-time Phase 3 PR review). The harness must reject any attempt to override these via env var or CLI; if Agent 2 builds an override path "for testing convenience," that's a vector. Spec: no override.
- Secondary mitigation: the harness writes the registry config it actually used into the JSON report (`"registry_snapshot": { "test_bbox": ..., "test_acreage": ... }`). If the registry ever drifts, audit trail is preserved.

### 4.4 Harness must not write to the agent's metric loop

- AutoResearch tracks "discovery success" metrics that drive the agent's reward signal. The harness writes to `harness_reports/`, full stop. It must not append to any metric file the agent reads as performance signal (e.g., `metrics.jsonl`, strategy memos). If Agent 2 is tempted to "share a counter" with `research.py`, that's a foot-gun: the agent would see a green harness as success and pretend production was fine.
- Concrete: the harness writes only to `harness_reports/{county}_{timestamp}.json` and `harness_reports/markets_dashboard.md`. No other path under any circumstance.

### 4.5 `--output report.md` does NOT replace the JSON

- CLI flag `--output report.md` (L893) writes a human-readable Markdown report. Risk: Agent 2 reads "writes a Markdown report instead of JSON" and skips writing the per-county JSON files entirely when `--output` is set.
- Spec: `--output` is **additive**. The per-county JSON in `harness_reports/{county}_{timestamp}.json` is always written; `--output PATH` writes an additional Markdown summary to PATH. The agent's L897–L903 integration points read JSON, so JSON must always exist after a run.

### 4.6 Sample-feature redaction policy (PII)

Appendix L931 shows `"Owner": "[REDACTED]"` in the example sample feature. Spec the redaction:

- **Strict-by-default redaction.** The harness redacts before serializing to disk. The set of redacted fields, hardcoded into the harness:
  - `owner_name` (logical) / `Owner` (Fulton API field) → `"[REDACTED]"`
  - `owner_mailing_address` / `OwnerAddr1` → `"[REDACTED]"`
  - `owner_mailing_address_2` / `OwnerAddr2` → `"[REDACTED]"`
- Owner mailing addresses are the most under-thought PII risk: an LLC owner is fine to expose, but an individual owner's mailing address is often their home address. The harness has no way to disambiguate LLC from individual at sample time, so redact both.
- Site addresses are **not redacted** — they are the parcel's location and are public record at the county level. Agent 3 should explicitly confirm this is correct policy (§9).
- All other parcel attributes (ParcelID, LandAcres, LUCode, assessed values) are public-record and are NOT redacted. They are essential for debugging when a sample feature looks wrong.
- **Verification step:** before writing the JSON file, the harness asserts `"REDACTED"` literal string is present in every sample feature dict (it's always present because the owner field is always sampled). If the assertion fails, refuse to write the file. This is a belt-and-suspenders check that prevents a code regression from leaking owner names.
- **Verbose-mode caveat:** `--verbose` (L892) prints raw API responses to stdout. This is a console-only path and not committed to disk, but operators must be told that `--verbose` prints unredacted owner data. Document in `--help` text.

---

## 5. Security and credentials

### 5.1 No credentials in `sources.json` (forward-looking)

Today, all eight Atlanta-market county portals are anonymous-access. `sources.json` contains no auth tokens. **Spec the policy now, before the first one needs auth:**

- If a county's ArcGIS service ever requires a token, it goes into an env var (e.g., `HARNESS_TOKEN_FULTON`). The registry references the env var name, not the value.
- The harness loads tokens via `os.environ.get("HARNESS_TOKEN_<COUNTY>")`. Missing token → mark connector as `skipped` with reason "auth required, env var unset", not failing.
- Tokens never get logged. The `--verbose` mode must redact `?token=...` and `Authorization:` headers before printing.

### 5.2 File-write paths

- `harness_reports/` is the only write sink. Validate at startup that the directory is a relative subdirectory of the harness's CWD or an absolute path matching a hardcoded allowlist (just `<repo>/harness_reports/`). No symlink traversal.
- `--output PATH` (L893): the user-supplied path is the most exposed surface. Spec:
  - Resolve to absolute path with `pathlib.Path(path).resolve()`.
  - Reject paths whose resolved form is outside the repo (`Path.resolve().is_relative_to(REPO_ROOT)` check). This blocks `--output ../../etc/passwd`-style traversal.
  - Reject paths whose suffix is not `.md` (force the user's intent; the flag is documented as Markdown).
  - Reject if the file exists and is a symlink (anti-overwrite-via-symlink).
- Path validation errors → exit code 2 with a clear stderr message, before any network calls.

### 5.3 Logging hygiene

- Log entries (stdout/stderr or a `harness_reports/run.log`): never include full request URLs that contain query tokens. Strip query params for tokens/keys before logging; keep path + sanitized params.
- Never log response bodies in non-verbose mode. In verbose mode, truncate to 2000 chars and redact owner fields.
- Stack traces from unhandled exceptions can leak file paths and env vars in some Python builds. Wrap the top-level `main()` in a try/except that converts exceptions to a single-line error log and exits with non-zero. The full traceback goes to a debug log file in `harness_reports/`, gitignored.

### 5.4 Retention and pruning

- `parameters.json` `logging.harness_report_retention_days: 90`. Phase 2 spec:
  - The harness **never deletes** report files by default. Even if files older than 90 days exist, they remain.
  - A `--prune` mode (Phase 3+, not Phase 2) would delete reports older than retention. Out of scope for this phase.
  - Phase 2 deliverable just enforces the **read-only diagnostic** posture. Irreversible deletes from a tool the agent calls automatically would be catastrophic; flag any Agent 2 attempt to add deletion as a no-go.
- The CI workflow (`.github/workflows/harness-fulton.yml`) writes a fresh report every push and does NOT clean up. The repo should `.gitignore` `harness_reports/*.json` and `harness_reports/*.log`, while keeping `harness_reports/markets_dashboard.md` committed (or also ignored — Open Question 9.b).

### 5.5 Owner data exposure via response caching

- Some HTTP libraries (`requests` does NOT, `httpx` with `cache=True` would) cache responses to disk. The harness must not enable on-disk response caching, ever — the cache file would contain unredacted owner names.

### 5.6 Dependency supply chain

- Recommendation: add only `requests` (or whatever Phase 1 already pinned, likely `requests >= 2.31`). Do not add: `httpx`, `aiohttp`, `tenacity`, `arcgis` (the Esri Python SDK — heavyweight, opinionated, includes a credentials store), `pyproj` (geospatial reprojection, not needed since harness only validates).
- If `requirements.txt` already pins `requests`, no change needed. Verify before editing.

---

## 6. Architectural considerations

### 6.1 Single file vs small package

**Recommendation: single file `connector_harness.py`.**

Rationale (one sentence): Karpathy simplicity criterion holds — the harness is ≤ 800 lines of straightforward sequential logic with one external dependency, splitting across modules would create import-cycle and testability friction without buying composability anyone needs at this stage.

If the file later exceeds 1200 lines or check logic grows test-suite-sized, refactor to `harness/` package in a separate PR. Phase 2 = single file.

### 6.2 Connector registry: module vs JSON vs embedded in `sources.json`

Three options; trade-offs:

| Option | Pros | Cons |
|--------|------|------|
| A. Python module (`connector_registry.py`) with a `REGISTRY = [...]` constant of dataclasses or dicts | Type-checkable, IDE-navigable, can include callables (e.g., per-county custom field-coercion funcs) for free, no parsing layer | Adding a county requires editing `.py` (the appendix promises "no code change to add a county" — this is a soft conflict) |
| B. Standalone JSON file (`connector_registry.json`) | Pure data, "no code change" promise honored, easy to diff in PRs | Two registries (this one + `sources.json`) means a sync risk and a question of which is canonical |
| C. Read directly from `sources.json` `county_parcel_data` block | Single source of truth, already validated by humans | `sources.json` lacks harness-only fields (`expected_bbox`, `test_acreage`, redaction allowlist), so it would need extension and the AUTORESEARCH_MECHANICS rules around `sources.json` mutability complicate it |

**Recommendation: hybrid — Option A with auto-population from C.** The harness defines a `Connector` dataclass in `connector_harness.py`. At startup, it reads `sources.json`, copies fields verbatim into a `Connector` instance, and overlays harness-only config (`expected_bbox`, `test_acreage`) from a small adjacent file `connector_registry.json` (Option B). Adding a new county = add it to `sources.json` (the human curatorship layer) AND add the harness-only overlay to `connector_registry.json`. Both files are JSON, both are gated by humans, and there's no `.py` edit required to add a county.

This means Agent 2 should produce, alongside `connector_harness.py`:
- `connector_registry.json` — the harness-only config overlay, seeded with Fulton (`expected_bbox`, `test_acreage`, redaction policy).

The file `connector_registry.py` proposed in the prompt becomes unnecessary. Agent 1's call: don't create it. The dataclass lives inside `connector_harness.py`.

### 6.3 Mixed connector types (ArcGIS REST vs AI fallback only)

Per appendix L837–L853 and `sources.json`:
- Tier 1 / `arcgis_rest`: Fulton, DeKalb, Cobb, Gwinnett.
- Tier 1 / `arcgis_rest_with_fallback`: Clayton, Henry.
- Tier 2 / `ai_fallback_only`: Spalding, Fayette.

Phase 2 implements ONLY `arcgis_rest` checks against Fulton. The `Connector` abstraction must accommodate the other types eventually:

- `Connector.access` field carries the type string from `sources.json`.
- The harness dispatches on `access`:
  - `arcgis_rest` → run the 10 ArcGIS checks.
  - `arcgis_rest_with_fallback` → in Phase 2, run the same 10 ArcGIS checks; fallback portal validation is Phase 5+.
  - `ai_fallback_only` → return overall_health `n/a`, status string "No API; AI fallback only", matching the dashboard rendering (L951–L952). Do NOT skip silently — emit a JSON report stub so the dashboard has a row.
- Add a `Connector.access_required_at_phase` enum-like field so that adding a not-yet-implemented type produces a clear `NotImplementedError` rather than silently passing.

### 6.4 Three integration points: API surface

Appendix L897–L903 lists three agent integration points (startup, before discovery, on production failure). Two API shapes:

- **Option A: three importable functions.** `run_all_connectors() -> Dict[str, Report]`, `run_county(county: str) -> Report`, `diagnose_failure(county: str, query: dict) -> Report`.
- **Option B: one function with a mode param.** `run(mode: Literal["all", "county", "diagnose"], **kwargs) -> Union[Dict, Report]`.

**Recommendation: Option A.** Three call-sites with three different return shapes; mode-param overloading complicates type signatures and the call-site is more verbose without being clearer. Option A also makes mocking trivial in Phase 2 tests.

The CLI (`__main__` guard) is a thin wrapper that dispatches argparse args to one of the three functions. The CLI and import surface share the same code paths.

### 6.5 Module structure (informative for Agent 2)

Suggested top-level layout inside `connector_harness.py`:

```
1. constants (REPO_ROOT, REPORTS_DIR, USER_AGENT, REDACTION_TOKENS, ...)
2. dataclasses (Connector, CheckResult, Report)
3. registry loader (read sources.json + connector_registry.json, return List[Connector])
4. HTTP layer (_RateLimitedSession, retry-with-backoff helper)
5. ArcGIS helpers (_arcgis_get, _check_error_envelope)
6. 10 check functions (each takes Connector + session, returns CheckResult)
7. report writer (JSON + dashboard markdown)
8. orchestrator (run_county, run_market, run_all, diagnose_failure)
9. CLI (argparse, main)
10. __main__ guard
```

This is informative, not prescriptive. Agent 2 may reorganize.

### 6.6 What does `harness_reports/.gitkeep` even do

The prompt asks whether to ship `harness_reports/.gitkeep`. Recommendation: **yes, ship it**, alongside `harness_reports/.gitignore` containing:

```
*.json
*.log
!.gitkeep
!.gitignore
```

This keeps the directory tracked but excludes generated reports. The CI workflow's report files won't accidentally get committed. `markets_dashboard.md` — see Open Question 9.b.

---

## 7. Phase 2 testing requirements

### 7.1 Smoke test

Mandatory in CI before any check runs:

```
python -m py_compile connector_harness.py
```

This catches syntax errors and import-time failures. Add it as the first step in `.github/workflows/harness-fulton.yml`.

### 7.2 Offline test with fixture (mandatory)

The harness must have at least one offline test that doesn't hit Fulton's live server. Pattern:

- Capture a real Fulton response once (manually, by Agent 2 during local dev) for: `?f=pjson` service root, `/11?f=pjson` layer schema, and a known-good query response with 10 features.
- **PII-redact those fixtures by hand** before committing — none of the captured features may contain real owner names. Replace with `"REDACTED-FIXTURE"` literal so the redaction test below can assert against a deterministic value.
- Save under `tests/fixtures/fulton/` as JSON files.
- Write a Python test (pytest, or a single `if __name__ == "__main__"` self-test if pytest isn't already in the project) that monkeypatches `requests.Session.get` to return these fixtures and runs `run_county("fulton")`. Assert overall_health == `"healthy"` and all 10 checks pass.
- This is the only Phase 2 test that proves the harness's logic, independent of Fulton's uptime.

### 7.3 Live test in CI

`.github/workflows/harness-fulton.yml` runs `python connector_harness.py --county fulton --quick` on every push. Risks:

- **Self-DoS:** every push triggers ~3–5 HTTP requests to Fulton's ArcGIS endpoint (service root, layer schema, known-good query, owner sanity, pagination). On a busy branch with many pushes per day, this can reach 30–50 req/day. That's fine for Fulton but document a ceiling: if push rate ever exceeds 100 pushes/day, gate the CI job with `if: github.event_name != 'push' || github.ref == 'refs/heads/main'` to limit live runs to main-branch pushes.
- **CI flakiness:** Fulton's server occasionally hits transient 503s. The CI job should not block PRs on transient infra. Spec:
  - Run the live check; on `failing` rating from a network/timeout cause, mark the CI job as a warning (yellow), not red. GitHub Actions does this with `continue-on-error: true` plus a separate fail-on-logic-error step.
  - Field-mapping or schema-drift failures **do** block the PR (these are real regressions).
  - Network/timeout failures **don't** block.
- **Parallel CI runs hammering Fulton:** if multiple PRs push at the same minute, you can get 3–4 simultaneous CI jobs hitting Fulton. The 1 req/sec rate limit only constrains within one process. Across processes, Fulton sees the parallel load. Mitigation: GitHub Actions concurrency group (`concurrency: { group: harness-fulton, cancel-in-progress: false }`) to serialize harness CI runs across the repo. This prevents self-DoS without complicating the harness code.
- **Token leakage:** the CI job runs in a public-or-private repo and the harness logs run output. If tokens ever appear, they leak. Today no tokens; revisit when adding a county that requires auth.

### 7.4 Test isolation

- The harness in test mode must NOT write to `harness_reports/` in the real repo. Use `tempfile.TemporaryDirectory` or a `--reports-dir` override env var (`HARNESS_REPORTS_DIR`).
- Wait — overriding via env var creates the very mutability hole §4.3 warns against. Compromise: the env var is allowed, but the reports written to a non-default dir are clearly marked `"_test_run": true` in the JSON, and the orchestrator refuses to read non-default-dir reports for trend analysis.

### 7.5 What's NOT required for Phase 2

- No mutation testing.
- No fuzzing of ArcGIS responses.
- No integration test against `research.py` (Phase 3 work).
- No load/stress test (only one connector live).

---

## 8. Severity-ranked risk list — go/no-go gates for Agent 2

Severity scale: **S1 = blocker (must fix before Agent 2's PR is reviewable)**, **S2 = must address before merge**, **S3 = address-or-justify in Agent 2's response**, **S4 = nice-to-have / deferrable**.

| ID | Severity | Risk | Gate (what Agent 2 must demonstrate) |
|----|----------|------|--------------------------------------|
| R-01 | **S1** | Harness imports `prepare.py` or opens a Postgres connection | `grep -E "import prepare\|from prepare\|psycopg\|sqlalchemy\|create_engine" connector_harness.py` returns 0 matches; harness module imports cleanly with `DATABASE_URL` unset |
| R-02 | **S1** | Harness writes to `parcels`/`parcel_scores` tables or to `sources.json` | Same grep returns 0 matches; `open("sources.json", "w"...)` not present |
| R-03 | **S1** | Sample features in committed JSON contain unredacted owner names | Pre-write assertion: `assert all("[REDACTED]" in str(feat) for feat in sample_features)`; offline test asserts the literal `"REDACTED-FIXTURE"` is what was redacted |
| R-04 | **S1** | ArcGIS HTTP 200 with `{"error": ...}` body silently treated as success | Response parser checks for `error` key first, before treating body as data; offline test fixture for the error-envelope case |
| R-05 | **S1** | `--output PATH` accepts traversal paths | Path resolved, validated `is_relative_to(REPO_ROOT)`, suffix `.md` enforced; reject-test exists |
| R-06 | **S2** | Spatial reference: server returns coords in 102667 instead of 4326 | Geometry check asserts coords in WGS84 range; failing assertion produces clear "outSR not honored" message |
| R-07 | **S2** | Field name case drift produces silent 0% population | Field-mapping check is case-insensitive in *diagnostic* and reports "case difference" hint; population check uses the response's actual key |
| R-08 | **S2** | Pagination check is non-deterministic without `orderByFields` | Pagination check appends `orderByFields=<parcel_id_field>` |
| R-09 | **S2** | Owner-sanity check false-positives on legitimate ALL-CAPS names | All-uppercase alone is not a redaction signal; only redaction-token match is. Test: "SMITH JOHN H" passes; "REDACTED" fails |
| R-10 | **S2** | Performance baseline drift check trusts a single run | Bootstrapping logic returns `pass + insufficient history` until 10 runs; trend check uses median-of-last-10 |
| R-11 | **S2** | `--output` replaces JSON output instead of supplementing | JSON files are always written; `--output` only adds the Markdown |
| R-12 | **S2** | CI workflow blocks PRs on transient network failures | Network/timeout failures yield warning, not red; logic failures yield red |
| R-13 | **S3** | Test bbox / acreage filter overridable at runtime (cheat-code risk per §4.3) | No CLI flag, no env var override; registry config snapshotted into JSON output |
| R-14 | **S3** | Field population denominator unstable at sample size 10 | Either sample size 25 OR threshold 70%; documented choice |
| R-15 | **S3** | Empty geometry counted as invalid geometry | Null geometries counted separately; warning < 5%, failure ≥ 5% |
| R-16 | **S3** | PO-Box mailing addresses fail address-parse check | PO-Box regex branch bypasses street-number rule |
| R-17 | **S3** | Concurrent CI runs hit Fulton in parallel | GitHub Actions concurrency group set |
| R-18 | **S3** | Harness logs full URLs containing future tokens | Logging helper strips known-sensitive query params; covered by a unit test |
| R-19 | **S3** | Single-file vs package decision undocumented | Top-of-file comment explicitly chooses single-file with rationale |
| R-20 | **S3** | Mixed-type connector (`ai_fallback_only`) errors instead of producing N/A row | Dispatch on `Connector.access`; `ai_fallback_only` yields stub report with `"overall_health": "n/a"` |
| R-21 | **S4** | Verbose-mode output unredacted | `--help` text warns; not a code-blocking risk because it's stdout-only |
| R-22 | **S4** | DNS flakiness causes false CI failures | One retry on `socket.gaierror` |
| R-23 | **S4** | Connection pool tuning under future concurrent execution | Code comment flags it; no Phase 2 action |

**Go/no-go gate summary for Agent 2:**

- All S1s must be addressed in Agent 2's first submission. A submission with any S1 unaddressed is rejected at code-writer-response stage.
- All S2s must be addressed before Agent 3's reviewer-decision is positive.
- S3s must be addressed OR Agent 2 must include a written justification for deferral in `02_code_writer_response.md`.
- S4s are noted; Agent 3 can sign off without them.

---

## 9. Open questions and Agent 3 escalation candidates

Items I could not resolve from the specs alone and that Agent 3 should rule on before merge.

### 9.a Sample-feature redaction: strict-by-default or opt-in via flag?

- I recommended **strict-by-default** in §4.6: the harness always redacts owner_name and owner_mailing_address before writing to disk. There is no `--no-redact` CLI flag.
- Counter-argument: human operators debugging a connector failure may want unredacted data once. They can run `--verbose` (stdout, not committed) to see it. The strict-by-default policy survives.
- **Agent 3 ruling needed:** confirm strict-by-default. If overridden, a `--no-redact` flag must require an env var `HARNESS_ALLOW_UNREDACTED_OUTPUT=I_UNDERSTAND_PII_IS_LEAKING` to actually take effect — high-friction guard.

### 9.b Are committed reports gitignored, or does `markets_dashboard.md` get committed?

- Per-county JSON reports (`{county}_{timestamp}.json`): clearly gitignored; they're machine-generated, time-stamped, and would explode the repo.
- `markets_dashboard.md`: it's a single file, easy to read, would let humans see the latest health at a glance from the GitHub UI without cloning. But committing it on every CI run produces commit churn.
- **My recommendation:** gitignore both. The dashboard is regenerated on every run; if a human wants to see it, they pull and run the harness, or check the CI artifact upload (the workflow can `actions/upload-artifact` the dashboard).
- **Agent 3 ruling needed:** approve gitignore-both, or override.

### 9.c Does the harness participate in the AutoResearch metric loop in any way?

- I'm certain the answer is **no** (per §4.4). Harness writes to `harness_reports/` only.
- But the agent reads harness output to gate discovery (per L897–L903). That's a *consumer* relationship, not a metric-write relationship.
- **Agent 3 ruling needed:** explicitly affirm in the reviewer decision that the harness is a one-way diagnostic — agent reads, agent does NOT learn-from-or-reward from harness output, and the harness writes nothing the AutoResearch reward signal sees.

### 9.d Is `connector_registry.py` a separate file, embedded, or replaced by `connector_registry.json`?

- Prompt says Agent 1's call. I called §6.2: replace `connector_registry.py` with `connector_registry.json` (harness-only overlay) + Python dataclass inside `connector_harness.py`.
- **Agent 3 ruling needed:** confirm or override. If overriding to a Python module, document why "no code change to add a county" is being relaxed.

### 9.e Test acreage range and test bbox for Fulton

- Appendix L289–L361 should specify Fulton's test acreage and test bbox. I did not find those values in the appendix during the focused read; my recommendation in §3.6 is 5–50 acres + Fulton's WGS84 envelope.
- **Agent 2's job before submission:** hit `https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11?f=pjson` once, extract the layer's `extent`, convert from 102667 to 4326 (one-off, not in harness code), and use that as the bbox in `connector_registry.json`. Document the conversion in a code comment.
- **Agent 3 ruling needed:** accept Agent 2's chosen bbox or send it back for refinement.

### 9.f What does `overall_health: degraded` actually mean?

- Appendix L885 lists `healthy / degraded / failing`. The exact pass-count threshold is not specified. My recommendation:
  - `failing`: any S1-equivalent check fails (service_alive, layer_schema, field_mapping, known_good_query) OR ≥ 3 of 10 checks fail.
  - `degraded`: 1–2 non-S1 checks fail OR field_population is below 80% OR performance ≥ 2.0× baseline.
  - `healthy`: all 10 checks pass and population ≥ 80% and performance within baseline.
- **Agent 3 ruling needed:** confirm thresholds. The agent's discovery-rotation logic (L901) keys off these exact words, so they must be deterministic.

### 9.g Should the harness validate the Fulton zoning/FLU/TAD layers later, or always parcels-only?

- §2.8: Phase 2 is parcels-only. Long-term, do other layers get harness coverage?
- The appendix doesn't say. Layer 34 (zoning) is consumed by the agent for site-suitability analysis — it absolutely needs the same health checks, eventually.
- **Agent 3 ruling needed:** accept Phase 2 parcels-only, with a note that Phase 6+ extends harness to additional layers.

### 9.h `--quick` mode behavior under live-CI

- The CI runs `--quick`, which per appendix L891 skips geometry validation and performance baseline.
- That means the CI never catches a spatial-reference regression on PR. The nightly full run (L965) does catch it.
- **Agent 3 ruling needed:** accept "CI catches schema/field/auth regressions; nightly catches geometry/performance regressions" split, or extend CI to non-quick mode (more flake risk).

### 9.i Performance baseline storage when reports are gitignored

- §3.5 recommends file-based history. If reports are gitignored (9.b), the CI runner has no history file from prior runs (each runner is ephemeral).
- This means baseline drift detection only works on a long-lived host (the nightly cron runner with persistent disk) or with explicit history upload to S3 / artifacts.
- **Agent 3 ruling needed:** accept that CI has no baseline (`pass + insufficient history`) and only the nightly cron runner accumulates the baseline. Or specify a history persistence mechanism.

### 9.j Behavior when `sources.json` and `connector_registry.json` disagree

- Per §6.2 hybrid registry. Sync risk: someone updates one and not the other.
- Recommendation: the harness validates at startup that every county in `connector_registry.json` exists in `sources.json` (key match) and fails loudly if not. The reverse (every `sources.json` county in registry overlay) is a warning, not a failure (counties not yet harnessed).
- **Agent 3 ruling needed:** accept this validation.

---

## End of review

This document is read-only output by Agent 1. Agent 2 should treat sections 1–7 as design constraints, section 8 as merge gates, and section 9 as items requiring Agent 3 input before final sign-off.

