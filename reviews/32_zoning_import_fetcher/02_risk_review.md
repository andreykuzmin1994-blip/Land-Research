# 02 — zoning_import onto the shared fetcher (D1-11): pre-change risk register

> **Tier 2** (external-service integration of a tool that writes a scoring
> input, `parcels.zoning` / `zoning_description`). This register was written
> by an independent reviewer BEFORE any code, from the requirement and the
> code at base `4fe8080` (PR 38 merge). It holds concerns, mitigations and
> go/no-go gates only; the code writer records a disposition per item in its
> response, and a fresh context that did not write the code reviews the
> change afterwards. Model names are omitted.

## Operator answers this round rests on (2026-11-09, binding, in chat)

"1 - Count them, jsut review, 3 - fix 4 - can you add this yourself. Start
building what you proposed keeping my answers in mind"

| | Answer | Meaning for the work of this round |
|---|---|---|
| 1 | "Count them" | D3: unlisted districts AND districts with unreadable setback values are counted too (answers D3-01 / D3-02 of `reviews/29_operator_decisions/03_decision_note.md`). |
| 2 | "just review" | D2 flood scope stays as implemented (V/VE review-only, floodway still blocks); no flood-rule change. The null-beats-low question is NOT answered: no new root sub-score. |
| 3 | "fix" | Fix the research.py ArcGIS error-envelope bug and audit-path bug, and **do this D1-11 zoning_import fetcher round**. |
| 4 | "can you add this yourself" | The agent, acting for the human between runs, applies the reviews/26 hazards prepare-mutation to prepare.py. |
| Step 2 | "Start building" | Build the screener quick wins. |

Only answer 3 (the D1-11 half) is in scope here. Nothing in this round may
touch `sources.json`, `zoning_rules.json`, `rules_sources.json`,
`prepare.py`, `parameters.json`, `program.md` or `building_prototypes.json`.

## The requirement as read

`zoning_import.ZoningSession` (`zoning_import.py:308-373`) is a bare
`requests.Session`: `allow_redirects` defaults to True (any host, any scheme,
no denylist), no robots.txt is read, cookies persist. Move it onto
`rules_scrape.PoliteFetcher` and the ONE shared verdict
`rules_scrape.robots_verdict` (`rules_scrape.py:1105`), as
`adjacency_import.AdjacencyFetcher` and `hazards_import.HazardsFetcher` do,
with the D1 ArcGIS exception (`arcgis_exception_applies`) and its line
`robots: <host>: robots unavailable (403): ArcGIS public REST accepted by
operator 2026-11-09` printed once per host (`robots_exception_lines`). What
zoning_import writes must stay byte-identical for the same inputs.

## What "byte-identical" can and cannot mean (decide first)

1. **The golden check proves nothing about this change.**
   `tests/zoning_golden_check.py` runs research.py's scoring over a fixture
   that already carries imported zoning (`tests/zoning_golden_case.py`); it
   never imports `zoning_import`'s HTTP layer. It exits 0 at base (run here
   offline: "identical to the ba4a780 record: 12 parcels + the cycle") and
   will exit 0 after any fetch-layer change. It must stay green, but the
   equivalence proof has to come from the fetch layer itself (ZI-04).
2. **Two printed lines change by design and cannot stay identical:** the new
   `robots: ...` lines (required), and the `requests: host n, ...` line,
   whose per-host counts grow by one robots.txt GET per host contacted
   (`PoliteFetcher._raw_get` counts it in `self.requests`, as in the two
   sibling tools). Recommended: keep counting robots.txt (honest SR-11
   accounting, same as the siblings) and name it as the one counted diff.
3. **Failure-path texts change** (ZI-07). Success-path stdout other than
   the two lines above, every UPDATE (column values, row set, order), the
   exit codes and every count line must be identical.

So the contract to hold is: **for identical answers from the servers, the
same UPDATE statements in the same order, the same exit code, and stdout
identical after removing the `robots:` lines and the robots.txt share of
the `requests:` line.**

## Register (ranked)

| ID | Sev | Concern | Mitigation | Gate |
|---|---|---|---|---|
| ZI-01 | **blocking** | **Import cycle.** `rules_scrape.py:132` does `import zoning_import` at module top. The sibling pattern (`import rules_scrape` at the top of zoning_import plus `class ZoningSession(rules_scrape.PoliteFetcher)`) breaks every entry that imports rules_scrape first. Proven here in a scratch copy (repo untouched): `import rules_scrape`, `import adjacency_import` and `python zoning_import.py --help` all fail with "partially initialized module 'rules_scrape' ... has no attribute 'PoliteFetcher'"; only `import zoning_import` first works. That would break `make rules-scrape`, `make adjacency-import`, `make hazards-import`, `make zoning-import` and `tests/test_rules_scrape.py`. | Preferred: move rules_scrape's four call-time uses of `zoning_import` (`tiles_for`, `esri_rings_to_geojson`, `tsv_has_baseline`, `mirror_blocks`, lines ~4052/4077/4285/4337) to a function-level import, so zoning_import can import rules_scrape at the top and subclass like its siblings. Acceptable alternative: composition (a `PoliteFetcher` built inside `ZoningSession.__init__` with a function-level import), no rules_scrape edit. Do NOT add a new shared module (new protected file, new SR-1 lists). | A test that runs, each in a FRESH subprocess, `import X` for X in rules_scrape, zoning_import, adjacency_import, hazards_import, runner, research, plus `python zoning_import.py --help`, `python rules_scrape.py --help`, `python adjacency_import.py --help`, `python hazards_import.py --help`: all exit 0. Also: `python rules_scrape.py` as `__main__` imports a second copy of rules_scrape through zoning_import; the test asserts that no rules_scrape exception class crosses that boundary (zoning_import's errors are its own `SourceError`). |
| ZI-02 | **blocking** | **Global request budget.** `PoliteFetcher.max_requests` defaults to 150 (`DEFAULT_MAX_REQUESTS`). The last live corridor import made ~1,520 data requests (reviews/29 03: 240+233+295+280+232+240 for the six sources) plus the parcel-layer IN-list pages. A default budget fails the run after 150 requests. `BudgetExceeded` is not a `SourceError`: `fetch_features`' caller catches `SourceError, ValueError, TypeError, KeyError` only and `fetch_jurisdictions`' caller only `SourceError`, so it would escape `run()` as a traceback. | Set `max_requests` so it never binds before the per-source budgets: at least `n_zoning_sources * (MAX_REQUESTS_PER_SOURCE + 1) + ceil(parcels / ID_CHUNK) + hosts` (computed in `run()` from the loaded sources and parcel count, or a documented constant with that bound proven by a test). Map `BudgetExceeded` and `Unreachable` to `SourceError` inside `ZoningSession.get_json` (as `AdjacencyFetcher.get_json` does). | Test: 6 sources at 401 requests each plus 2,000 parcels never hits the global budget; a forced global exhaustion yields `SourceError` (source FAILED, exit 4; or REFUSED exit 1 on the parcel layer), never an uncaught exception. |
| ZI-03 | **blocking** | **User-Agent leak.** `PoliteFetcher._raw_get` passes `headers={"User-Agent": rules_scrape.USER_AGENT}` per request, which overrides the session header. `AdjacencyFetcher` / `HazardsFetcher` re-implement `_raw_get` to send their own UA, and in doing so DROP the base's prepared-URL backstop (`requests.Request(...).prepare()` then `host_denied` / `url_terms_blocked` on the URL requests will really contact). Copying the sibling override would make zoning_import's denylist check weaker than rules_scrape's. | Either (a) one small rules_scrape change: a `user_agent` class attribute read by `__init__` and `_raw_get` (rules_scrape's own requests unchanged), ZoningSession sets it and does not override `_raw_get`; or (b) a zoning `_raw_get` that keeps every base check, prepared-URL backstop included. (a) is preferred and may be done in the same edit as ZI-01. Keep `robots_token = "Land-Research-ZoningImport"` (its own token, plus the AI-agent tokens via the shared verdict). | Tests: every request (robots.txt and data) carries zoning_import's UA and no other; a `%6Dunicode`-style host and a terms-blocked host in a configured URL make zero requests; `test_rules_scrape` RS-05 UA tests unchanged and green. If (a), rules_scrape's own fetches send exactly the old UA (pinned). |
| ZI-04 | **blocking** | **Equivalence proof with fixtures that were rewritten.** `tests/test_zoning_import.py`'s `FakeHttp.get(url, params, timeout)` and `_Resp.json()` do not fit the new call (`get(url, timeout=, allow_redirects=False, stream=True, headers=)`, body read via `iter_content`/`content`). Rewriting the fixtures can silently change what the 35 existing tests prove. | Keep the `World` class and its data unchanged; add an ADAPTER that parses the full URL's query back into the params dict `World.answer` expects, serves robots.txt (404 by default), and gives `_Resp` a `content` of the JSON bytes. Add a **differential test**: a verbatim reference copy of the base `ZoningSession` (from 4fe8080, in a non-`test_` helper under `tests/`, header naming the commit) and the new session each drive `run()` over the same `World` + `StoreConn`; assert the identical UPDATE list (order, values), identical exit code, identical stdout after the two named diffs; for the full `World` and for its failure variants (truncated ids, short pages, a failing host, join below floor, unknown TaxDist). Also pin that the wire URL is unchanged: for each query shape used (`where` IN-list with a double space inside an id, the envelope `geometry`, `objectIds`, `outSR`/`inSR` ints, metadata `f=pjson`), `requests.Request("GET", u, params=p).prepare().url == requests.Request("GET", u + "?" + urlencode(p)).prepare().url` (checked here for three shapes: equal). | Differential test green on success and every failure variant; wire-URL test green; the existing assertions of `test_zoning_import.py` kept (only ZI-07's deliberately changed ones edited, each named in the response). |
| ZI-05 | high | **New fail-closed outcomes on real hosts.** (a) robots.txt that answers 5xx, a timeout, a non-text 2xx, an off-site redirect or a Crawl-delay above 30 s now refuses the whole host, where the data was read before. On `gismaps.fultoncountyga.gov` (the parcel layer AND layer 34) that is `REFUSED ... exit 1`, nothing written. (b) A data redirect is now followed only to https on the same registrable domain (5 hops max); before, any redirect was followed. (c) A 200 body containing a challenge marker (`_CHALLENGE_BODY`, e.g. `_px3`) is now "challenge page". (d) Bodies over 25 MB fail. All fail-safe (parcels keep their values), all operator-visible. | Accept as the point of D1-11, and name each in the response and in the zoning_import docstring. Evidence that none fires today: reviews/29 03 (2026-11-09) shows the shared fetcher reading all six zoning layers on the same hosts and paths (adjacency_import, exit 0, every ArcGIS Online host under the D1 line), robots of gismaps.fultoncountyga.gov and gis.atlantaga.gov answering 404. zoning pages are up to 150 features (adjacency uses 100): keep `OID_CHUNK`. | One test per outcome (a)-(d) on fakes: correct exit code, the source named FAILED or the parcel layer REFUSED, zero UPDATEs for the affected parcels. |
| ZI-06 | high | **The D1 exception must apply exactly as in the siblings, no wider.** zoning_import's requests are layer metadata `<layer>?f=pjson` and `<layer>/query?...&f=json`; both are inside `arcgis_public_rest_path`. Risk: a zoning-specific `_check` or verdict override, or a path built differently (e.g. trailing slash, `f` twice) that the regex then refuses. | Inherit `PoliteFetcher._check` and `robots_for` unchanged (no `_check` override needed if ZI-03 (a) is taken); no `robots_verdict` / `arcgis_*` definition or call of its own in zoning_import. Print `rules_scrape.robots_exception_lines(session)` once, after the per-jurisdiction/class/written lines and before `requests:` (the siblings' order), also on the exit-4 path. | Extend `tests/test_operator_decisions.py`: `FETCHERS` includes `zoning_import.ZoningSession`; `test_tools_define_no_verdict_of_their_own` covers zoning_import.py (no def/assignment of `robots_verdict`, no `arcgis_exception` text, `ZoningSession.robots_for is PoliteFetcher.robots_for`, `_check` inherited or calling `rules_scrape.robots_verdict` exactly once); an OutputTests case: `run()` over services3/services8 fixtures behind a 403 robots prints the exact line once per host and no query string; a FEMA-style readable disallow and a non-ArcGIS 403 refuse with zero data requests. Update the `PinTests` comment ("zoning_import stays outside the shared robots policy") and keep `test_zoning_import_hosts_are_arcgis_rest_only`. |
| ZI-07 | high | **Retry semantics differ and are not output-neutral on failure.** Base: 3 retries (2/4/8 s), 429 retried with or without Retry-After (capped 30 s), messages `"<host>: 500 after 4 attempts"`, `"<host>: 429 after 4 attempts"`. Shared: 2 retries (2/4 s), 429 without an acceptable Retry-After refused at once, 5xx with Retry-After above the cap refused, messages `"HTTP 500"`, `"HTTP 429 without an acceptable Retry-After"`, `"connection error after 3 attempts"`, `"HTTP 403 (Akamai block)"`. These texts reach stdout (`source X: FAILED, nothing used: ...`, `REFUSED: Fulton parcel layer unavailable (...)`). | Adopt the shared policy (stricter, politer, one policy for all four tools); keep zoning's `"<host>: "` prefix and `"service error <code>"` for an HTTP-200 ArcGIS error envelope (more informative than the siblings' bare "service error"; codes only, never the message: SR-8). Do not keep a second retry loop in zoning_import. | `HttpEtiquetteTests` updated deliberately, each changed assertion listed in the response: bounded retries = `rules_scrape.MAX_RETRIES + 1`, Retry-After cap honoured, no sleep above the cap, 429 without Retry-After refused, the new message texts; "messages carry no body" kept (an error envelope whose message is the owner string never prints it). |
| ZI-08 | should-fix | **Per-source budget boundary shifts by one.** `_budget(session, start)` compares `total_requests()`; the first request of a source on a new host now triggers a robots.txt GET inside that source's window. | Count the per-source budget on data requests only (`total_requests()` = requests minus robots.txt GETs, or a separate data counter), so `MAX_REQUESTS_PER_SOURCE` keeps its exact meaning; the `requests:` line keeps the full count. | `test_source_budget` unchanged in its numbers (budget 5, at most 7 data calls), plus a case where the source's host is new (robots fetched inside the window) with the same refusal point. |
| ZI-09 | should-fix | **Mid-body transport errors.** With `stream=True`, `ChunkedEncodingError` / `ContentDecodingError` (subclasses of `requests.RequestException`, not `ConnectionError`) are raised while reading the body in `_read_capped` and are not caught by `PoliteFetcher._get`; they would escape `run()` as a traceback. (Pre-existing in the base session too: its non-streamed `get` raises them inside `http.get`, outside the caught pair.) | In `ZoningSession.get_json`, map `requests.RequestException` to `SourceError("<host>: connection error")` (no retry: the shared loop owns retries). Do not change `PoliteFetcher._get` this round. | Test: a fake whose `iter_content` raises `ChunkedEncodingError` gives source FAILED / exit 4 (parcel layer: REFUSED exit 1), no traceback, no UPDATE for the affected parcels. |
| ZI-10 | should-fix | **Run-state guards must still precede ANY request, robots.txt included.** `tsv_has_baseline` and `mirror_blocks` (exit 3) run before the session is used; robots are fetched lazily. A refactor that builds the fetcher earlier, or pre-warms robots, would contact hosts on a refused run. | Keep the order of `run()`; construct nothing that fetches before pass 1. | Test: baseline in the TSV, and a halted mirror row, each give exit 3 with zero requests (robots included) on the fake. |
| ZI-11 | should-fix | **Spacing and cookies change silently.** ArcGIS Online hosts now share one 1 s key (`ARCGIS_SPACING_KEY`); Crawl-delay is honoured; cookies are blocked (`_NO_COOKIES`); headers are cleared (Accept becomes what ZoningSession sets). Sources run sequentially, so wall time barely moves. | Set `Accept: application/json` explicitly as today. Name the shared spacing key and the cookie policy in the docstring. | `SpacingTests`-style case through ZoningSession (services3 then services8: gap >= 1 s); a test that a `Set-Cookie` answer is not replayed. |
| ZI-12 | nit | **Docs and records.** The module docstring ("Depends only on prepare and zoning", "HTTP: 1 request/second per host, bounded retries..."), README § "Zoning import", the `PinTests` comment, and the open item in `reviews/29_operator_decisions/03_decision_note.md` item 4. | Update the docstring and README; close the reviews/29 item by an APPENDED dated line pointing here (do not rewrite that note); `03_decision_note.md` in this folder records the operator answers above, the commits, the evidence, and the live status (ZI-13). | Reviewer check; `grep -riE` for model names over this folder is empty. |
| ZI-13 | nit | **No live run is possible in this environment.** The only authorised database, `lr_zimp`, holds no Fulton parcels, so `run()` returns "no Fulton parcels; nothing to do" before any request; the corridor copy used in reviews/29 is not available here. | Do not create one and do not point DATABASE_URL elsewhere. Prove equivalence offline (ZI-04). The decision note says so and lists, for the operator's next between-runs `make zoning-import`, the expected visible diffs only: four `robots:` lines (services, services3, services7, services8.arcgis.com) and +1 per host on the `requests:` line; any other difference in the count lines is a regression to report, not to accept. | Decision note states it; no live counts claimed. |

## Standing risks

- **SR-1**: zoning_import.py and rules_scrape.py are immutable during a
  run; this is a between-runs Tier 2 change. No protected file is touched
  (`test_zz_human_files_unchanged`). The new coupling is named: after this
  change a rules_scrape fetch-policy edit also changes how stored zoning is
  ingested, so any future rules_scrape HTTP change is reviewed for all four
  tools. If ZI-04 holds, no new run + baseline is needed for this change
  itself; if any UPDATE differs on the fixtures, it is a no-go (the change
  would then move a scoring input).
- **SR-2**: no SQL change. `git diff 4fe8080 -- zoning_import.py` must be
  confined to imports, the HTTP section, the robots print line, the budget
  helper and the docstring; every SQL constant and every function of
  matching, overlay, plan and write unchanged (the reviewer checks the diff
  hunks; the differential test proves the outcome).
- **SR-5 / SR-9**: unchanged (one write transaction; a re-run with the
  same inputs changes 0 rows), covered by the existing tests through the
  differential harness.
- **SR-6**: every new path on fakes; no network, no DATABASE_URL in tests.
- **SR-8**: messages carry hostnames, statuses and counts only; the
  `robots:` line has no URL or query; an ArcGIS error message never prints.
- **SR-10**: no new dependency.
- **SR-11**: stricter than before (robots honoured, manual redirects,
  fewer retries, shared ArcGIS spacing). The base's per-host 1 s spacing is
  kept for other hosts.
- **SR-15**: the metric and its scoping are untouched; the change sits in
  an input tool between runs.
- SR-3, SR-4, SR-7, SR-12, SR-13, SR-14, SR-16: not touched.

## Go / no-go

**GO with conditions.** The change may be implemented when the code writer
accepts ZI-01 to ZI-04 as blocking. It may be committed only when:

1. G1 (ZI-01): the fresh-subprocess import/entry-point test passes for all
   listed modules and CLIs.
2. G2 (ZI-02): the global budget never binds before the per-source
   budgets; `BudgetExceeded` / `Unreachable` surface as `SourceError`.
3. G3 (ZI-03): every request carries zoning_import's UA; the prepared-URL
   denylist backstop applies to zoning_import's requests.
4. G4 (ZI-04): the differential test against the verbatim base session
   shows identical UPDATEs, exit codes and stdout (after the two named
   diffs) on the full fixture world and every failure variant; the
   wire-URL test passes.
5. G5 (ZI-06): zoning_import is in `FETCHERS` and the no-own-verdict static
   test; the exception line prints once per host.
6. G6: `python -m unittest discover -s tests` and
   `python -m unittest discover -s screener/tests -t .` green;
   `tests/zoning_golden_check.py` exit 0 (necessary, not sufficient: item 1
   of "What byte-identical means").
7. G7: `git diff --cached --name-only` shows no protected file in any
   commit; no push.

**NO-GO** if any UPDATE differs on the fixtures, if any entry point fails
to import, or if the D1 exception is reached by a path other than the
shared `robots_verdict`. ZI-05 to ZI-13 are dispositioned in the response
(fixed, or accepted with a reason).
