# 02 — zoning_import onto the shared fetcher (D1-11): code writer response

> **Tier 2.** Written by the code writer after implementing against
> `02_risk_review.md` (written before any code by an independent reviewer).
> One disposition per register item and per gate. A fresh context that did
> not write this code reviews it next and writes `03_decision_note.md`.
> Model names are omitted. Base `4fe8080` (PR 38 merge); code commit
> `16b5b97`. Nothing pushed.

## Operator answers this round rests on (2026-11-09, binding, in chat)

"1 - Count them, jsut review, 3 - fix 4 - can you add this yourself. Start
building what you proposed keeping my answers in mind"

| | Answer | What it means here |
|---|---|---|
| 1 | "Count them" | D3: unlisted districts AND districts with unreadable setback values are counted too (D3-01 / D3-02). Not in this round's code. |
| 2 | "just review" | D2 flood scope stays as implemented (V/VE review-only, floodway still blocks); no flood-rule change. The null-beats-low question is NOT answered: no new root sub-score. Nothing here touches either. |
| 3 | "fix" | Fix the research.py ArcGIS error-envelope and audit-path bugs, and **do this D1-11 zoning_import fetcher round**. Only the D1-11 half is this round. |
| 4 | "can you add this yourself" | The agent, acting for the human between runs, applies the reviews/26 hazards prepare-mutation to `prepare.py`. Not this round. |
| Step 2 | "Start building" | Screener quick wins. Not this round. |

No file in this round's commits is human-owned: `sources.json`,
`zoning_rules.json`, `rules_sources.json`, `prepare.py`, `parameters.json`,
`program.md` and `building_prototypes.json` are untouched
(`git diff --cached --name-only` checked before the commit).

## What changed

| File | Change |
|---|---|
| `zoning_import.py` | `ZoningSession(rules_scrape.PoliteFetcher)`: `robots_token = "Land-Research-ZoningImport"`, `user_agent = USER_AGENT` (the same string as before). `robots_for`, `_check`, `fetch`, `_raw_get`, `_space` inherited unchanged. `get_json` builds `url?urlencode(params)`, maps `Unreachable`, `BudgetExceeded` and `requests.RequestException` to `SourceError("<host>: ...")`, and parses the body with `requests.Response.json()` itself on the answer's headers (`_json_like_requests`). `_get` is extended only to record the answer's headers. `total_requests()` counts data requests (robots.txt excluded). New `request_budget()`; `run()` raises `session.max_requests` to it before the first request, and prints `rules_scrape.robots_exception_lines(session)` after the `written:` line and before `requests:`. The old retry loop and its constants (`REQUEST_INTERVAL_S`, `MAX_RETRIES`, `BACKOFF_S`, `RETRY_AFTER_CAP_S`, `HTTP_TIMEOUT_S`) are gone. Docstring updated. |
| `rules_scrape.py` | `import zoning_import` moved from module top into `case_features` and `run` (the only two functions that use it). `PoliteFetcher.user_agent = USER_AGENT` class attribute, read by `__init__` and `_raw_get` instead of the module constant; rules_scrape's own requests send exactly the old UA. |
| `tests/zoning_session_4fe8080.py` | New, not a test module: the 4fe8080 `ZoningSession` class body and its six constants, verbatim (a test pins it against `git show 4fe8080:zoning_import.py`). |
| `tests/test_zoning_import.py` | Fixture adapter, the rewritten `HttpEtiquetteTests`, and five new classes (below). `World`, `StoreConn` and their data unchanged. |
| `tests/test_operator_decisions.py` | `ZoningSession` in `FETCHERS`; the no-own-verdict test covers `zoning_import.py`; a zoning `run()` output case; a spacing case; the `PinTests` comment updated. |
| `README.md` | § "Zoning import": the fetch line names the shared fetcher, robots/D1, redirects and cookies. |

`git diff 4fe8080 -- zoning_import.py` has six hunks: docstring, imports,
constants, the HTTP section, the budget line in `run()`, and the robots
print. No `_SQL_` constant and no function of matching, overlay, plan or
write changed (SR-2).

## Gates

| Gate | Result |
|---|---|
| **G1** (ZI-01) | Done the preferred way. `ImportCycleTests`: in a fresh interpreter each of `rules_scrape`, `zoning_import`, `adjacency_import`, `hazards_import`, `runner`, `research` imports first, and `python {zoning_import,rules_scrape,adjacency_import,hazards_import}.py --help` exit 0. rules_scrape as `__main__` (runpy) reaches `zoning_import` at call time, which loads an ordinary second `rules_scrape`; asserted that `zoning_import.rules_scrape is sys.modules["rules_scrape"]` and is not the `__main__` copy. AST check: the four helpers rules_scrape calls (`tiles_for`, `esri_rings_to_geojson`, `tsv_has_baseline`, `mirror_blocks`) name neither `rules_scrape`, `ZoningSession` nor `SourceError`, so no exception class crosses the two copies. |
| **G2** (ZI-02) | `request_budget(n_sources, n_parcels)` = calls x per-call worst case, where calls = `n_sources * (MAX_REQUESTS_PER_SOURCE + 1) + ceil(parcels / ID_CHUNK)` and per call = `(MAX_REDIRECTS + 1) * (MAX_RETRIES + 1)` data attempts plus as many robots.txt attempts per hop. It is a backstop by construction; the per-source budget is the guard. Tests: six sources each driven past 400 requests with 2,000 parcels all fail with `request budget of 400 exceeded` (the per-source text), the parcel line prints, exit 4, `total() < max_requests`. Forced exhaustion: budget 3 gives both later sources `FAILED ... request budget of 3 spent`, exit 4, no traceback; budget 1 gives `REFUSED: Fulton parcel layer unavailable (... request budget of 1 spent)`, exit 1, nothing committed. |
| **G3** (ZI-03) | Option (a). `ZoningSession._raw_get is PoliteFetcher._raw_get`, so the prepared-URL denylist backstop applies. Tests: every request of a full run, robots.txt included, carries exactly `{"User-Agent": zi.USER_AGENT}`, `allow_redirects=False`, `stream=True`. On a real `requests.Session` (a mounted offline adapter) the session headers are zoning's UA and `Accept: application/json`. Source URLs on `library.municode.com`, `%6Dunicode.com` and `www.zoneomics.com` make zero requests to that host (robots.txt included), and the source FAILs. `PoliteFetcher` itself still sends `rules_scrape.USER_AGENT`. `test_rules_scrape` is unchanged and green. |
| **G4** (ZI-04) | `DifferentialTests`: the reference session (behind a one-attribute adapter: `max_requests = 0`, which `run()` raises and the reference never reads) and the new one each drive `run()` over the same fixture. 13 variants: full world, fb 503, truncated ids, short pages, an error envelope, join below the floor, unknown TaxDist, parcel layer 500, parcel answer truncated, TSV baseline, mirror baseline, no `zoning_sources` key, FLU-only. In every variant `conn.executed` (every SQL statement and its parameters, in order), the committed rows, the commit count and the exit code are identical, and stdout is identical after the normalisation below. Except for the two transport variants, the data queries (URL plus parameters) are also identical in order. A second run (SR-9: `written: 0 changed`) is identical too. `WireUrlTests`: for every data query of a full run, `params=` and `urlencode` prepare to the same URL, and so does the URL the new session sent. Every shape is covered: metadata `f=pjson`, the IN-list with the double-spaced id, the envelope query, and pages with and without `outSR`. |
| **G5** (ZI-06) | `FETCHERS` has 4 members; all shared-verdict, path, params and fail-closed tests now run on `ZoningSession` too. `test_tools_define_no_verdict_of_their_own` covers `zoning_import.py`: no `robots_verdict` def or assignment, no `arcgis_exception` text, zero direct `robots_verdict` calls (its `_check` is the inherited one, asserted `is PoliteFetcher._check`), `robots_for` inherited. New `test_zoning_import_names_the_exception_once_per_host`: `run()` with join sources on services3 and services8 behind a 403 robots exits 0. It prints each `robots: <host>: robots unavailable (403): ArcGIS public REST accepted by operator 2026-11-09` line once, with no `?`, between `written:` and `requests:`. A FEMA-style readable `Disallow: /*?*` on services3 and a 403 on a non-ArcGIS host each FAIL the source with zero data requests and no robots line. `test_zoning_import_hosts_are_arcgis_rest_only` is kept. |
| **G6** | `python -m unittest discover -s tests`: **1773 OK** (base 1744; +29). `python -m unittest discover -s screener/tests -t .`: **134 OK**. `tests/zoning_golden_check.py` exits 0 ("identical to the ba4a780 record: 12 parcels + the cycle"), as do the adjacency and hazards golden checks. As the register says, the golden check never touches this HTTP layer: necessary, not sufficient. G4 is the proof. |
| **G7** | One code commit, no protected file, no push. |

### The normalisation (the only accepted stdout differences)

1. `robots:` lines are dropped (they are new by design; on the fixture
   hosts, which answer robots.txt 404, there are none at all).
2. In the `requests:` line, the robots.txt GETs are subtracted per host.
   On the full fixture this is exactly +1 per host contacted, asserted.
3. **Transport failures only** (`fb 503`, parcel layer `500`), per ZI-07:
   `"<host>: 503 after 4 attempts"` becomes `"<host>: HTTP 503"`, and the
   failing host's count drops by one (4 attempts before, 3 now). Each
   variant asserts the old text was in the old output and the new text is
   in the new one before it substitutes. The UPDATEs and exit codes of
   these variants are identical without any normalisation.

### Mutation check (run during development, then reverted)

| Mutation in `zoning_import.py` | Caught by |
|---|---|
| Sorting the query parameters before `urlencode` | `WireUrlTests` |
| One extra space in the `service error` text | `DifferentialTests` (error envelope) and `HttpEtiquetteTests` |
| Ignoring the answer's headers when decoding (`text/plain` assumed) | No fixture caught it at first, because the fixture data are ASCII. Added `test_utf8_body_parsed_like_requests`: non-ASCII bodies through `get_json` under three content types, compared with the reference session on real `requests.Response` objects. It catches the mutation now. |

## Register dispositions

| ID | Disposition |
|---|---|
| ZI-01 | **Fixed** (G1). Two call-time imports, not four: two of the four uses share `run()`. rules_scrape's docstring says why. |
| ZI-02 | **Fixed** (G2). |
| ZI-03 | **Fixed**, option (a) (G3). |
| ZI-04 | **Fixed** (G4). The 35 pre-existing assertions are kept, apart from the deliberate edits listed under ZI-07. |
| ZI-05 | **Accepted as the point of D1-11; one test per outcome.** (a) Robots outcomes: robots.txt 503, a timeout, a non-text 2xx, an off-site redirect, a Crawl-delay of 60 s, a readable disallow for this tool's token, a readable disallow for an AI-agent token, and a non-ArcGIS 403. On a source host each one FAILs the source (exit 4, prior value kept, no UPDATE for its parcels, no data request to it). On the parcel host, robots 500 gives `REFUSED ... exit 1`, nothing committed. (b) Data redirects: off-site, http and to a denylisted host each FAIL the source with no request to the target. A same-site https redirect is followed (the new host's robots.txt is read) and the import completes. (c) A `challenge-platform` marker in a 200 body gives `challenge page`. (d) An answer over the size cap gives `answer larger than the size cap`. One more: a parcel layer configured as plain http would now be refused ("not an https URL"). The live one is https (pinned by `PinTests`). `OID_CHUNK` is kept at 150. Named in the module docstring. |
| ZI-06 | **Fixed** (G5). No `_check` override at all. |
| ZI-07 | **Accepted: shared policy adopted**, no second retry loop. Kept: the `"<host>: "` prefix and `"service error <code>"` (codes only; a test shows the owner string in the envelope's message never prints). Deliberately changed assertions: (1) `EndToEndTests.test_parcel_layer_down_exits_1_and_writes_nothing`: `"500 after 4 attempts"` became `"HTTP 500"`. (2) `HttpEtiquetteTests.test_same_host_spaced_different_hosts_not_serialised`: the exact sleep list became per-host timestamps at least 1 s apart, a second host not held back, and `requests == {"a.test": 4, "b.test": 2}` (was 3 / 1: the robots.txt GET). (3) `test_retry_after_is_capped_and_retries_are_bounded` became two tests. In `test_429_without_acceptable_retry_after_is_refused_at_once`, Retry-After 3600, absent or garbage gives one call and `"HTTP 429 without an acceptable Retry-After"` (was 4 calls and `"429 after 4 attempts"`). In `test_retries_are_bounded_and_retry_after_is_honoured_under_the_cap`, a 429 with Retry-After 7 or a 503 gives `rules_scrape.MAX_RETRIES + 1` = 3 calls (was 4); back-off is `rules_scrape.BACKOFF_S`, the message is `"HTTP 503"`, and a 5xx whose Retry-After is above the cap is refused at once. (4) `test_4xx_fails_fast_and_messages_carry_no_body`: assertions unchanged, three cases added. (5) In `test_operator_decisions`, the no-own-verdict test takes a per-module call count (zoning_import: 0). New: a retry-then-success test. |
| ZI-08 | **Fixed.** `total_requests()` excludes robots.txt GETs. A robots.txt *redirect* hop counts as data, which is tighter, never looser. `test_source_budget` is unchanged in its numbers. New `test_source_budget_counts_data_requests_only`: cold and warm robots give the same refusal point (6 data calls, the same as 4fe8080). |
| ZI-09 | **Fixed.** A `ChunkedEncodingError` raised mid-body becomes `"<host>: connection error"`. On a source this gives FAILED / exit 4; on the parcel layer, REFUSED / exit 1. No traceback. |
| ZI-10 | **Holds.** `run()` order unchanged; the fetcher fetches lazily. Test: TSV baseline, mirror baseline (exit 3) and the missing key (exit 1) each make zero requests (robots.txt included), and `robots_meta` stays empty. |
| ZI-11 | **Fixed.** `Accept: application/json` set explicitly. Spacing through `ZoningSession.get_json` on services3 / services8: every gap is at least 1 s. On a real session a `Set-Cookie` is never replayed. The docstring names the shared key and the cookie policy. |
| ZI-12 | **Done:** docstring, README, the `PinTests` comment, and a dated line appended to `reviews/29_operator_decisions/03_decision_note.md` (that note is otherwise unchanged). `03_decision_note.md` here is the reviewer's. |
| ZI-13 | **Holds: no live run.** No database was used in this round. The worktree's `sources.json` has no `zoning_sources` key, so `make zoning-import` here would exit 1 before any request; the corridor copy of reviews/29 is not available. Equivalence is proven offline (G4). |

## Standing risks

All hold as the register states. SR-1: `zoning_import.py` and
`rules_scrape.py` are immutable during a run; this is a between-runs Tier 2
change. The new coupling is real: a future rules_scrape HTTP change now
also changes zoning ingestion, so any such change is reviewed for all four
tools. Because G4 holds (no UPDATE differs on any fixture variant), this
change needs no new run or baseline by itself. SR-2: no SQL change.
SR-8: messages carry hosts, statuses and counts only, and the `robots:`
line has no URL. SR-10: no new dependency. SR-11: stricter than before.

## For the operator: the next between-runs `make zoning-import`

Expected visible differences only (any other change in a count line is a
regression to report, not to accept):

1. Up to four new lines, one per ArcGIS Online host actually read:
   `robots: services.arcgis.com: robots unavailable (403): ArcGIS public
   REST accepted by operator 2026-11-09`, and the same for
   services3, services7 and services8.arcgis.com. (A host whose robots.txt
   no longer answers 403 gets no line; a host with candidate parcels that
   is refused shows `source X: FAILED` instead.)
2. On the `requests:` line, +1 per host contacted (its robots.txt):
   gismaps.fultoncountyga.gov and gis.atlantaga.gov answered 404 on
   2026-11-09 and are read as before.
3. Only if a server misbehaves: retries are 2 instead of 3, a 429 without
   a usable Retry-After fails at once, and the failure texts read
   `HTTP 503`, `connection error after 3 attempts` and similar, instead of
   `503 after 4 attempts`.

Every `source ...:` count, the `jurisdiction`, `classes` and `written:`
lines, and the exit code should be identical for the same server answers.
